"""Token-budgeted context assembly.

Centralizes the "how much of each variable-size piece fits in the model's input
window" decision that drill / qa_arena / resume / assistant each used to make
with ad-hoc char caps (``knowledge_ctx[:5000]``, ``load_history(limit=30)``, …).

Each call site maps its pieces to prioritized :class:`Section` objects and calls
:meth:`ContextBudget.pack`; the assembler returns the (possibly trimmed) pieces
that fit plus a token report for observability. The call site still owns the
final prompt/message shape — this module only owns "what fits, by priority".

Token counting uses tiktoken (``cl100k_base``) when available — it's already a
transitive dependency of langchain-openai — and falls back to a CJK-aware char
heuristic otherwise. Counts are APPROXIMATE and used ONLY for budgeting
decisions, never for billing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger("uvicorn")

# Subtracted from the resolved window so prompt-template overhead and tokenizer
# drift never push the real request over the model's limit.
_SAFETY_MARGIN = 2000
# Mirrors llm_provider's default max_tokens — the output we must leave room for
# when no channel declares its own.
_DEFAULT_OUTPUT_RESERVE = 16384
# Never hand back a budget so small the prompt becomes useless.
_MIN_INPUT_BUDGET = 4000


# ── Token counting ──

_encoder = None
_encoder_tried = False


def _get_encoder():
    global _encoder, _encoder_tried
    if _encoder_tried:
        return _encoder
    _encoder_tried = True
    try:
        import tiktoken
        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception as e:  # tiktoken missing or model data unreachable
        logger.warning("tiktoken unavailable; context budgeting uses char heuristic: %s", e)
        _encoder = None
    return _encoder


def _heuristic_tokens(text: str) -> int:
    """CJK-aware fallback when tiktoken is absent.

    Biased to slightly OVER-count (CJK at ~1 token/char, ASCII at ~3.5 chars/
    token) so a missing tokenizer degrades toward "trim a bit early", never
    toward overflowing the real window.
    """
    cjk = 0
    other = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7A3:
            cjk += 1
        else:
            other += 1
    return int(cjk * 1.05 + other / 3.5) + 1


def count_tokens(text: str) -> int:
    """Approximate token count of ``text`` (tiktoken if available, else heuristic).

    Cached: pack_messages / budget resolution re-count the SAME history strings
    every turn, so a bounded LRU avoids re-tokenizing identical text. maxsize is
    small on purpose — a handful of large knowledge/resume strings is a few MB,
    not unbounded growth.
    """
    if not text:
        return 0
    return _count_tokens_cached(text)


@lru_cache(maxsize=1024)
def _count_tokens_cached(text: str) -> int:
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return _heuristic_tokens(text)


# ── Budget resolution ──

def _resolve_window() -> int:
    """Smallest declared input window across enabled LLM channels.

    Conservative on purpose: failover may land on any enabled channel, so we
    budget for the tightest one. Falls back to ``settings.default_context_window``
    when no channel declares an explicit ``context_window``.
    """
    from backend.config import settings
    try:
        from backend.channel_manager import get_all_channels
        windows = [
            int(ch["context_window"])
            for ch in get_all_channels("llm")
            if ch.get("enabled", True) and ch.get("context_window")
        ]
        if windows:
            return min(windows)
    except Exception as e:
        logger.debug("context window resolution fell back to default: %s", e)
    return settings.default_context_window


def _resolve_output_reserve() -> int:
    """Largest configured output ``max_tokens`` across enabled LLM channels."""
    try:
        from backend.channel_manager import get_all_channels
        reserves = [
            int(ch["max_tokens"])
            for ch in get_all_channels("llm")
            if ch.get("enabled", True) and ch.get("max_tokens")
        ]
        if reserves:
            return max(reserves)
    except Exception:
        pass
    return _DEFAULT_OUTPUT_RESERVE


def resolve_input_budget(reserve_output: int | None = None) -> int:
    """Token budget available for INPUT context = window − output reserve − margin."""
    window = _resolve_window()
    reserve = reserve_output if reserve_output is not None else _resolve_output_reserve()
    return max(_MIN_INPUT_BUDGET, window - reserve - _SAFETY_MARGIN)


# ── Section packing ──

@dataclass
class Section:
    """One candidate piece of context.

    priority : lower = more important; kept first among non-required sections.
    required : never dropped or truncated (counted before everything else).
    min_tokens : if the remaining budget can't fit at least this many tokens of
                 a truncatable section, drop it entirely instead of leaving a
                 uselessly tiny fragment.
    """
    name: str
    content: str
    priority: int = 100
    required: bool = False
    min_tokens: int = 0


@dataclass
class PackResult:
    sections: dict[str, str]
    report: dict = field(default_factory=dict)

    def get(self, name: str, default: str = "") -> str:
        return self.sections.get(name, default)


def _truncate_on_boundary(text: str, max_tokens: int) -> str:
    """Trim ``text`` to ≲ ``max_tokens``, snapping back to a line boundary.

    Never cuts through a Markdown table row or code line. Mirrors the intent of
    the old ``qa_arena._truncate_on_boundary`` but in token space.

    Implementation: when tiktoken is available, encode ONCE, slice the token ids,
    and decode back — O(n) single pass. The previous shrink-by-0.9 loop re-encoded
    the (still large) head on every iteration, so a 300k-char section paid for
    several full tokenizations; on big knowledge chunks that was seconds of pure
    CPU. Falls back to a proportional char estimate when tiktoken is absent.
    """
    enc = _get_encoder()
    if enc is not None:
        try:
            ids = enc.encode(text)
            if len(ids) <= max_tokens:
                return text
            # Reserve headroom for the "…节选" suffix that _snap_back appends, so
            # the final string still fits under max_tokens (budgeting is a hard cap).
            head = enc.decode(ids[: max(1, max_tokens - _TRUNCATE_SUFFIX_TOKENS)])
        except Exception:
            head = None
        if head is not None:
            return _snap_back(head)

    # Heuristic fallback (no tiktoken): proportional estimate, then verify down.
    total = count_tokens(text)
    if total <= max_tokens:
        return text
    approx_chars = max(1, int(len(text) * max_tokens / max(1, total)))
    head = text[:approx_chars]
    while head and count_tokens(head) > max_tokens:
        head = head[: max(1, int(len(head) * 0.9))]
    return _snap_back(head)


def _snap_back(head: str) -> str:
    """Snap a truncated head back to the last line boundary (if not too lossy)."""
    nl = head.rfind("\n")
    if nl > len(head) * 0.6:  # only snap back if it doesn't discard too much
        head = head[:nl]
    return head.rstrip() + _TRUNCATE_SUFFIX


_TRUNCATE_SUFFIX = "\n\n…（已按上下文预算节选）"
# Token headroom reserved for _TRUNCATE_SUFFIX so the truncated result stays ≤ cap.
_TRUNCATE_SUFFIX_TOKENS = 16


class ContextBudget:
    """Greedy section packer under a fixed token budget.

    Required sections are kept in full and counted first. Remaining sections are
    added in ascending ``priority`` order; one that overflows is truncated to the
    leftover budget (on a line boundary) or dropped if even ``min_tokens`` won't
    fit. The returned :class:`PackResult` carries a per-section token report for
    SSE/observability.
    """

    def __init__(self, budget_tokens: int):
        self.budget = budget_tokens

    def pack(self, sections: list[Section]) -> PackResult:
        required = [s for s in sections if s.required]
        optional = sorted((s for s in sections if not s.required), key=lambda s: s.priority)

        used = 0
        out: dict[str, str] = {}
        report: dict = {"budget": self.budget, "used": 0, "by_section": {}, "dropped": [], "truncated": []}

        for s in required:
            t = count_tokens(s.content)
            out[s.name] = s.content
            used += t
            report["by_section"][s.name] = t

        for s in optional:
            remaining = self.budget - used
            t = count_tokens(s.content)
            if t <= remaining:
                out[s.name] = s.content
                used += t
                report["by_section"][s.name] = t
            elif remaining > max(s.min_tokens, 50):
                trimmed = _truncate_on_boundary(s.content, remaining)
                tt = count_tokens(trimmed)
                out[s.name] = trimmed
                used += tt
                report["by_section"][s.name] = tt
                report["truncated"].append(s.name)
            else:
                out[s.name] = ""
                report["dropped"].append(s.name)

        report["used"] = used
        return PackResult(sections=out, report=report)


def pack_messages(
    messages: list[dict], budget_tokens: int, keep_last: int = 2,
) -> tuple[list[dict], dict]:
    """Trim a chat ``messages`` list to fit ``budget_tokens``, dropping OLDEST first.

    Always keeps the most recent ``keep_last`` messages regardless of budget so a
    turn never loses its immediate context. Returns ``(kept_messages, report)``.
    Per-message token cost includes a small role/framing overhead.
    """
    if not messages:
        return [], {"budget": budget_tokens, "used": 0, "kept": 0, "dropped": 0}

    def _msg_tokens(m: dict) -> int:
        return count_tokens(m.get("content", "")) + 4  # role + framing overhead

    keep_last = min(keep_last, len(messages))
    tail = messages[len(messages) - keep_last:] if keep_last else []
    head = messages[: len(messages) - keep_last]

    used = sum(_msg_tokens(m) for m in tail)
    kept_head: list[dict] = []
    # Walk the head newest→oldest, keeping what fits.
    for m in reversed(head):
        t = _msg_tokens(m)
        if used + t > budget_tokens:
            break
        kept_head.append(m)
        used += t
    kept_head.reverse()

    kept = kept_head + tail
    report = {
        "budget": budget_tokens,
        "used": used,
        "kept": len(kept),
        "dropped": len(messages) - len(kept),
    }
    return kept, report
