"""问答演练场 — 自由问答 + 长期记忆 + 背诵卡片式总结导出。"""

import asyncio
import base64
import json
import logging
import re
import threading
import uuid
import weakref
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.llm_provider import get_langchain_llm
from backend.storage import qa_sessions as store
from backend.context_assembler import resolve_input_budget, count_tokens, pack_messages
from backend.prompts.qa_arena import (
    QA_ARENA_SYSTEM,
    SUMMARY_SYSTEM,
    SUMMARY_USER_TEMPLATE,
    MAP_PROMPT,
    REDUCE_PROMPT,
    COMPRESS_PROMPT,
    INCREMENTAL_COMPRESS_PROMPT,
)
from backend.utils.sse_helpers import chunk_text as _chunk_text, chunk_reasoning as _chunk_reasoning, iter_chunks_with_idle

logger = logging.getLogger("uvicorn")

_turn_locks: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)
_turn_locks_guard = threading.Lock()


def _get_turn_lock(user_id: str, session_id: str) -> asyncio.Lock:
    key = (user_id, session_id)
    with _turn_locks_guard:
        lock = _turn_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _turn_locks[key] = lock
        return lock

# ── Multimodal image attachments ──
# Uploaded images are saved as files under the per-user data dir (the DB stores
# only filenames, kept lean) and re-encoded to base64 data URLs when fed to the
# vision model — the model provider is external and can't fetch our auth-protected
# serve endpoint, so it must receive the bytes inline.

_IMAGE_EXT_BY_MIME = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
}
_MIME_BY_EXT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif",
}
MAX_IMAGE_BYTES = 6 * 1024 * 1024   # 6MB per image
MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BASE64_CHARS = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
_DATA_URL_RE = re.compile(r"^data:(image/[\w.+-]+);base64,(.+)$", re.DOTALL)
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _session_upload_dir(user_id: str, session_id: str) -> Path:
    root = (settings.user_data_dir(user_id) / "qa_uploads").resolve()
    candidate = (root / session_id).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Invalid QA session upload path")
    return candidate


def _qa_uploads_dir(user_id: str, session_id: str) -> Path:
    d = _session_upload_dir(user_id, session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def delete_session_images(session_id: str, user_id: str) -> None:
    """Remove all uploaded images for a session — called when the session or its
    messages are deleted, so image files don't orphan on disk."""
    import shutil
    try:
        d = _session_upload_dir(user_id, session_id)
    except ValueError:
        logger.warning("Refusing to delete unsafe QA upload path for session %r", session_id)
        return
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def delete_uploaded_images(session_id: str, user_id: str, names: list[str]) -> None:
    try:
        directory = _session_upload_dir(user_id, session_id)
    except ValueError:
        return
    for name in names:
        if not _SAFE_NAME_RE.fullmatch(name):
            continue
        try:
            (directory / name).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove orphaned QA image %s: %s", name, exc)


def save_uploaded_images(session_id: str, user_id: str, data_urls: list[str] | None) -> list[str]:
    """Decode base64 image data URLs, persist to disk, return the stored filenames.

    Silently skips entries that aren't a supported image data URL, fail to decode,
    or exceed the size cap — a bad attachment never blocks the text turn.
    """
    saved: list[str] = []
    for url in (data_urls or [])[:MAX_IMAGES_PER_MESSAGE]:
        if not isinstance(url, str):
            continue
        m = _DATA_URL_RE.match(url)
        if not m:
            continue
        ext = _IMAGE_EXT_BY_MIME.get(m.group(1).lower())
        if not ext:
            continue
        encoded = m.group(2)
        if len(encoded) > MAX_IMAGE_BASE64_CHARS:
            continue
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception:
            continue
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            continue
        name = f"{uuid.uuid4().hex}.{ext}"
        try:
            (_qa_uploads_dir(user_id, session_id) / name).write_bytes(raw)
        except OSError as e:
            logger.warning("Failed to persist QA image %s: %s", name, e)
            continue
        saved.append(name)
    return saved


def get_image_path(session_id: str, user_id: str, name: str) -> Path | None:
    """Resolve a stored image filename to its path. Returns None if the name is
    unsafe (path-traversal guard) or the file is missing."""
    if not name or not _SAFE_NAME_RE.match(name):
        return None
    try:
        p = _session_upload_dir(user_id, session_id) / name
    except ValueError:
        return None
    return p if p.exists() else None


def _image_data_url(session_id: str, user_id: str, name: str) -> str | None:
    """Read a stored image and return it as a base64 data URL for the vision model."""
    p = get_image_path(session_id, user_id, name)
    if not p:
        return None
    mime = _MIME_BY_EXT.get(p.suffix.lstrip(".").lower(), "image/png")
    try:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:{mime};base64,{b64}"


def _user_content(text: str, image_names: list[str] | None, session_id: str, user_id: str):
    """Build the LLM ``content`` value for a user turn.

    A plain string when there are no images (keeps the prompt-prefix cache
    friendly); otherwise an OpenAI-style multimodal parts list
    ``[{type:text}, {type:image_url}, ...]``. Falls back to the text string if
    every referenced image fails to load.
    """
    if not image_names:
        return text
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    loaded = 0
    for name in image_names:
        url = _image_data_url(session_id, user_id, name)
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
            loaded += 1
    if loaded == 0:
        return text  # every image failed to load → fall back to text-only
    if not text:
        # Image-only turn: a minimal neutral instruction so the model knows to read
        # the image, and so endpoints that reject a text-less content array accept it.
        parts.insert(0, {"type": "text", "text": "请看图片。"})
    return parts


async def _build_memory_context(user_message: str, user_id: str) -> str:
    """Retrieve long-term vector memory relevant to the current question."""
    if not user_message or not user_message.strip():
        return ""  # image-only turn: nothing to search on
    try:
        from backend.vector_memory import search_memory
        results = await search_memory(user_message, user_id, top_k=5)
    except Exception as e:
        logger.warning("Vector memory search failed (embedding may not be configured): %s", e)
        return ""
    if not results:
        return ""
    lines = []
    for r in results:
        lines.append(f"- [{r.get('topic', '未知')}] {r['content'][:200]}")
    return (
        "\n\n## 相关历史知识（来自你之前的学习记录）\n\n"
        + "\n".join(lines)
        + "\n\n如果上述知识与当前问题相关，可以自然融入回答中，但不要强行提及。"
    )


# Per-message caps when feeding the conversation to the summarizer.
# User turns are short questions; AI turns carry the substance (tables/code/design),
# so they get a much larger budget. With a large model context (≥256k) these are
# raised to near-pass-through — a deep design answer used to get cut at 16k chars,
# losing exactly the trade-offs/pitfalls section. They only act as a runaway safety now.
QA_USER_CAP = 6000
QA_ASSISTANT_CAP = 48000


def _truncate_on_boundary(text: str, cap: int) -> str:
    """Truncate to <= cap chars without cutting through a line (table row / code line).

    Snaps back to the last newline before the cap so we never emit a half-row of a
    Markdown table. Rarely fires given the generous caps above.
    """
    if len(text) <= cap:
        return text
    head = text[:cap]
    nl = head.rfind("\n")
    if nl > cap * 0.6:  # only snap back if it doesn't discard too much
        head = head[:nl]
    return head.rstrip() + "\n\n…(本段过长，已节选)"


def _format_message(m: dict, user_cap: int = QA_USER_CAP, assistant_cap: int = QA_ASSISTANT_CAP) -> str:
    role = "用户" if m["role"] == "user" else "AI"
    cap = user_cap if m["role"] == "user" else assistant_cap
    return f"[{role}] {_truncate_on_boundary(m['content'], cap)}"


def _format_conversation(
    messages: list[dict], user_cap: int = QA_USER_CAP, assistant_cap: int = QA_ASSISTANT_CAP
) -> str:
    return "\n\n".join(_format_message(m, user_cap, assistant_cap) for m in messages)


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff\-]", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:50]


def _extract_topic(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# ") and len(line) > 2:
            return line[2:].strip()
    return "技术问答总结"


COMPRESSION_THRESHOLD = 20
KEEP_RECENT = 10
SUMMARY_REGEN_INTERVAL = 10
IDLE_HEARTBEAT_SECONDS = 30
MAX_RESPONSE_STORE_LENGTH = 16000

# TEMP DIAGNOSTIC: log the raw shape of the first few streamed chunks so we can see what
# the gateway actually sends (does reasoning arrive? under which additional_kwargs key?).
# Capped across the process; remove once the reasoning channel is understood.
_DIAG_CHUNKS = {"n": 0, "limit": 12}




async def _get_or_create_summary(
    session_id: str, user_id: str, history: list[dict], total_count: int,
    message_version: int,
) -> tuple[str, int]:
    """Return ``(rolling_summary, covered)`` where ``covered`` is how many of the
    OLDEST messages are folded into the summary.

    Incremental: reuses the cached summary and only folds in the turns added
    since it was last built (prev_summary + new turns → updated summary), instead
    of re-summarizing the whole history from scratch each time. Cost is O(new
    turns); older context is preserved in compressed form rather than re-read. The
    raw tail the caller still sends verbatim is ``history[covered:]``.
    """
    target_cover = total_count - KEEP_RECENT
    if target_cover <= 0:
        return "", 0

    cached = store.get_context_summary(session_id, user_id)
    prev_summary, covered = "", 0
    if cached:
        prev_summary, covered = cached
        # Legacy rows stored len(history)-at-summary-time, not a cover count; if
        # that exceeds the cover target the semantics don't line up — rebuild.
        if covered > target_cover:
            prev_summary, covered = "", 0
        elif target_cover - covered < SUMMARY_REGEN_INTERVAL:
            return prev_summary, covered  # fresh enough; reuse as-is

    new_msgs = history[covered:target_cover]
    if not new_msgs:
        return prev_summary, covered

    # Modest per-message caps keep the incremental call cheap (target ~300 chars).
    conversation = _format_conversation(new_msgs, user_cap=800, assistant_cap=1500)
    llm = get_langchain_llm()
    try:
        if prev_summary:
            user_content = INCREMENTAL_COMPRESS_PROMPT.format(
                prev_summary=prev_summary, conversation=conversation,
            )
        else:
            user_content = COMPRESS_PROMPT.format(conversation=conversation)
        resp = await llm.ainvoke([
            {"role": "system", "content": "你是对话摘要助手。"},
            {"role": "user", "content": user_content},
        ])
        summary = (resp.content or "").strip()[:600]
    except Exception as e:
        logger.warning("Context compression failed: %s", e)
        # Keep the prior summary if we have one; else a raw excerpt of the new turns.
        summary = prev_summary or conversation[:300]

    store.save_context_summary(
        session_id,
        user_id,
        summary,
        target_cover,
        expected_message_version=message_version,
    )
    return summary, target_cover


def _sse(payload: dict) -> str:
    """Serialize one SSE ``data:`` frame (ensure_ascii=False + trailing blank line)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_chat_answer(
    session_id: str, user_id: str, history: list[dict], prompt: str,
    prompt_images: list[str] | None = None,
    *,
    message_version: int,
) -> AsyncGenerator[str, None]:
    """Core QA streaming, shared by first-send and regenerate.

    ``history`` is the prior conversation (NOT including ``prompt``); ``prompt`` is the
    current user question to answer, with ``prompt_images`` its attached image filenames.
    Persists the assistant reply itself so callers stay thin. Three mutually-exclusive
    terminal states:
      - non-empty content, clean finish     → save assistant + ``done``
      - non-empty content, mid-stream error → save partial  + ``error`` + terminal ``done``
      - empty content (any reason)          → persist nothing + ``error`` + terminal ``done``

    Emits ``stage`` events (memory → thinking → answering) and ``reasoning`` deltas so the
    front end can show progress and the SSE stream never goes byte-silent during a long
    reasoning phase (which a proxy would otherwise time out → blank reply).
    """
    # Stage 1 — memory retrieval. Emit the marker first so the UI shows progress while the
    # (possibly slow) vector search runs, instead of a frozen spinner.
    yield _sse({"type": "stage", "stage": "memory", "message": "正在检索相关记忆…"})
    memory_ctx = await _build_memory_context(prompt, user_id)

    # Keep QA_ARENA_SYSTEM as a STABLE prefix so the channel's automatic prompt-prefix
    # cache can hit across turns. Dynamic content (retrieved memory, rolling summary)
    # must NOT be concatenated onto the system message — that would change the prefix
    # every turn and defeat caching. They go in as separate messages after the prefix.
    lc_messages: list[dict] = [{"role": "system", "content": QA_ARENA_SYSTEM}]

    # Context compression for long conversations. The rolling summary is built
    # incrementally; the raw tail we still send verbatim is everything AFTER the
    # part the summary already covers (not a fixed last-N), so the middle is never
    # lost between the summary and the recent window.
    if len(history) > COMPRESSION_THRESHOLD:
        summary, covered = await _get_or_create_summary(
            session_id, user_id, history, len(history), message_version,
        )
        raw_tail = history[covered:]
        summary_block = (
            f"## 之前的对话摘要\n{summary}\n\n（以下是最近的对话记录）" if summary else ""
        )
    else:
        summary_block = ""
        raw_tail = history

    # #4 token budgeting: the system prefix, rolling summary, retrieved memory and
    # the user prompt are all bounded and kept whole; budget only the raw tail
    # (the part that grows) so a very long recent window can't overflow the input
    # window. pack_messages drops the OLDEST tail turns first, always keeping the
    # latest two.
    budget = resolve_input_budget()
    reserved = (
        count_tokens(QA_ARENA_SYSTEM) + count_tokens(prompt)
        + count_tokens(summary_block) + count_tokens(memory_ctx)
    )
    hist_budget = max(1000, budget - reserved)
    kept_tail, _tail_report = pack_messages(raw_tail, hist_budget, keep_last=2)

    if summary_block:
        lc_messages.append({"role": "system", "content": summary_block})
    for m in kept_tail:
        # Re-attach images for recent user turns so follow-ups ("what about the
        # bottom-left of that diagram") still see them; old turns drop off with
        # the context window. Assistant turns are always plain text.
        if m["role"] == "user" and m.get("images"):
            content = _user_content(m["content"], m.get("images"), session_id, user_id)
        else:
            content = m["content"]
        lc_messages.append({"role": m["role"], "content": content})

    if memory_ctx:
        lc_messages.append({"role": "system", "content": memory_ctx})

    lc_messages.append({"role": "user", "content": _user_content(prompt, prompt_images, session_id, user_id)})

    # Stage 2 — model call. A reasoning model can "think" for a long time before any visible
    # answer token; we stream that thinking (see _chunk_reasoning) to keep the SSE connection
    # alive past proxy idle timeouts AND to show the user progress.
    yield _sse({"type": "stage", "stage": "thinking", "message": "正在思考…"})

    content = ""
    had_reasoning = False
    answering = False
    stream_error = False
    try:
        llm = get_langchain_llm()
        aiter = llm.astream(lc_messages).__aiter__()
        async for _kind, chunk in iter_chunks_with_idle(aiter, IDLE_HEARTBEAT_SECONDS):
            if _kind == "idle":
                # No chunk for IDLE_HEARTBEAT_SECONDS — upstream is genuinely silent (deep
                # reasoning with no streamed trace, or a stalled relay). Ping to keep the
                # proxy from closing the idle connection; the read itself stays in flight.
                yield _sse({"type": "ping"})
                continue
            if _DIAG_CHUNKS["n"] < _DIAG_CHUNKS["limit"]:
                _DIAG_CHUNKS["n"] += 1
                _c = getattr(chunk, "content", None)
                logger.warning(
                    "QA chunk diag #%d: content_type=%s content=%.60r akw_keys=%s meta_keys=%s",
                    _DIAG_CHUNKS["n"], type(_c).__name__, _c,
                    list(getattr(chunk, "additional_kwargs", {}) or {}),
                    list(getattr(chunk, "response_metadata", {}) or {}),
                )
            reasoning = _chunk_reasoning(chunk)
            if reasoning:
                had_reasoning = True
                yield _sse({"type": "reasoning", "content": reasoning})
            token = _chunk_text(chunk)
            if token:
                if not answering:
                    answering = True
                    yield _sse({"type": "stage", "stage": "answering", "message": "正在作答…"})
                content += token
                yield _sse({"type": "token", "content": token})
    except Exception as e:
        # astream can't fail over once the first chunk is out, so a mid-stream drop
        # propagates here. Whatever streamed before the drop is preserved below.
        logger.error("QA arena LLM call failed: %s", e)
        stream_error = True

    content = content.strip()

    # Empty completion — channel cooldown/failover, content filter, a reasoning model
    # that produced no visible answer, or a provider that streamed content in a field we
    # don't read. The backend call "succeeded" yet there is nothing to show. Don't persist
    # a blank turn or send a silent ``done`` (which renders an empty bubble): surface a
    # retry so the user can regenerate — a fresh attempt / channel often succeeds. Mirrors
    # the guard already on the summary path.
    if not content:
        logger.warning(
            "QA arena produced empty content for session %s (had_reasoning=%s, stream_error=%s)",
            session_id, had_reasoning, stream_error,
        )
        msg = (
            "模型只输出了思考过程、没有给出正文，请点击重新生成"
            if had_reasoning
            else "模型暂时无响应，请点击重新生成"
        )
        yield _sse({"type": "error", "message": msg})
        return

    # Persist whatever we got (full or partial) so a reload shows it and regenerate can
    # cleanly replace it.
    persisted = store.save_message(
        session_id,
        user_id,
        "assistant",
        content[:MAX_RESPONSE_STORE_LENGTH],
        expected_message_version=message_version,
    )
    if not persisted:
        yield _sse({
            "type": "error",
            "message": "会话已被清空或删除，当前回答未保存",
        })
        return

    if stream_error:
        yield _sse({"type": "error", "message": "回复被中断，可能不完整，请点击重新生成"})
    else:
        yield _sse({"type": "done"})

    # Lightweight profile evolution: track QA activity for learning insights.
    # Runs after the stream so it never blocks the response.
    try:
        from backend.memory import update_profile_realtime
        update_profile_realtime(
            mode="qa_arena",
            topic=None,
            user_id=user_id,
            score_entry={"score": None, "question": prompt[:80]},
        )
    except Exception:
        pass  # Non-critical, don't break the chat flow


async def _stream_qa_chat_unlocked(
    session_id: str, message: str, user_id: str, images: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream SSE events for a new QA arena chat turn (saves the user message).

    ``images`` is an optional list of base64 image data URLs; they're persisted to
    disk and the stored filenames are attached to the user message + fed to the
    vision model.
    """
    # Full history: the rolling-summary covered cursor indexes from message 0, and
    # the compression + token-budget layers below already bound what reaches the LLM.
    message_version = store.get_message_version(session_id, user_id)
    if message_version is None:
        yield _sse({"type": "error", "message": "会话不存在"})
        return
    history = store.load_messages(session_id, user_id, limit=None)
    image_names = save_uploaded_images(session_id, user_id, images)
    if not store.save_message(
        session_id,
        user_id,
        "user",
        message,
        images=image_names,
        expected_message_version=message_version,
    ):
        delete_uploaded_images(session_id, user_id, image_names)
        yield _sse({"type": "error", "message": "会话已被清空或删除"})
        return

    # Auto-title on first user message
    if not history:
        title = message[:20].strip() or ("图片提问" if image_names else "新对话")
        if len(message) > 20:
            title += "..."
        store.update_session_title(session_id, user_id, title)

    async for event in _stream_chat_answer(
        session_id,
        user_id,
        history,
        message,
        image_names,
        message_version=message_version,
    ):
        yield event


async def stream_qa_chat(
    session_id: str, message: str, user_id: str, images: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """Serialize a complete user/assistant turn for one persisted session."""
    async with _get_turn_lock(user_id, session_id):
        async for event in _stream_qa_chat_unlocked(
            session_id, message, user_id, images,
        ):
            yield event


async def _stream_qa_regenerate_unlocked(
    session_id: str, user_id: str
) -> AsyncGenerator[str, None]:
    """Re-answer the last user question, replacing any prior broken/partial/empty reply.

    The user message is NOT re-saved; the trailing assistant message (if any) is dropped
    so the regenerated answer cleanly replaces it. Shares the same context window as
    ``stream_qa_chat`` so the regenerated answer sees the same history — including any
    images attached to that last user turn.
    """
    message_version = store.get_message_version(session_id, user_id)
    if message_version is None:
        yield _sse({"type": "error", "message": "会话不存在"})
        return
    store.delete_last_message_if_assistant(
        session_id,
        user_id,
        expected_message_version=message_version,
    )
    messages = store.load_messages(session_id, user_id, limit=None)
    if not store.message_version_is_current(session_id, user_id, message_version):
        yield _sse({"type": "error", "message": "会话已被清空或删除"})
        return
    if not messages or messages[-1]["role"] != "user":
        yield f"data: {json.dumps({'type': 'error', 'message': '没有可重新生成的提问'}, ensure_ascii=False)}\n\n"
        return
    prompt = messages[-1]["content"]
    prompt_images = messages[-1].get("images") or []
    history = messages[:-1]
    async for event in _stream_chat_answer(
        session_id,
        user_id,
        history,
        prompt,
        prompt_images,
        message_version=message_version,
    ):
        yield event


async def stream_qa_regenerate(
    session_id: str, user_id: str,
) -> AsyncGenerator[str, None]:
    """Regenerate atomically with respect to normal chat turns and retries."""
    async with _get_turn_lock(user_id, session_id):
        async for event in _stream_qa_regenerate_unlocked(session_id, user_id):
            yield event


# With a large model context (≥256k) almost every real session fits in ONE pass —
# both faster (a single streamed call, no sequential map-reduce) and higher quality
# (full fidelity, no summary-of-summaries loss). Map-reduce is now a rare fallback for
# extreme sessions; its chunks are large and mapped in parallel.
SINGLE_PASS_BUDGET = 120000  # formatted-conversation chars that still go single-pass
CHUNK_SIZE = 48000           # per-chunk size for the (rare) map phase
MAP_CONCURRENCY = 4          # parallel map calls (bounded so a burst can't trip rate limits)


def _chunk_conversation(messages: list[dict], chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split the formatted conversation into chunks on message boundaries (never mid-message)."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for m in messages:
        block = _format_message(m)
        if current and current_len + len(block) > chunk_size:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(block)
        current_len += len(block)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def _map_chunk_to_notes(chunk: str, idx: int, total: int, reasoning_effort: str | None = None) -> str:
    """Map phase: summarize one conversation chunk into structured note fragments.

    On failure, keep a raw excerpt of the chunk rather than dropping it, so no part of
    the session is silently lost.
    """
    llm = get_langchain_llm(reasoning_effort=reasoning_effort)
    prompt = MAP_PROMPT.format(idx=idx, total=total, conversation=chunk)
    try:
        resp = await llm.ainvoke([
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": prompt},
        ])
        text = (resp.content or "").strip()
        if text:
            return text
    except Exception as e:
        logger.warning("Map-phase summary failed for chunk %d/%d: %s", idx, total, e)
    return f"（第 {idx} 段自动整理失败，保留原文节选）\n{chunk[:4000]}"


async def stream_generate_summary(
    session_id: str, user_id: str, reasoning_effort: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream SSE events for knowledge card generation.

    reasoning_effort: per-call override (minimal/low/medium/high). Summary is an
    extract-and-reorganize task, not deep reasoning, so a lower effort cuts latency
    sharply with little quality loss. None/"" keeps the configured default.
    """
    # Full history — the map-reduce path below exists precisely so long sessions
    # are summarized in full instead of being truncated.
    message_version = store.get_message_version(session_id, user_id)
    if message_version is None:
        yield _sse({"type": "error", "message": "会话不存在"})
        return
    messages = store.load_messages(session_id, user_id, limit=None)
    if len(messages) < 2:
        yield f"data: {json.dumps({'type': 'error', 'message': '对话内容太少，无法生成总结'}, ensure_ascii=False)}\n\n"
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yield f"data: {json.dumps({'type': 'progress', 'message': '正在分析对话内容...'}, ensure_ascii=False)}\n\n"

    conversation = _format_conversation(messages)

    # Almost all sessions fit one pass (large model context) → a single streamed call,
    # full fidelity. Only an extreme session falls back to map-reduce — and then the
    # chunks are mapped in PARALLEL (bounded) and merged with a dedicated reduce prompt,
    # so it's both faster and cleaner than the old sequential summary-of-summaries.
    if len(conversation) > SINGLE_PASS_BUDGET:
        chunks = _chunk_conversation(messages)
        total = len(chunks)
        yield f"data: {json.dumps({'type': 'progress', 'message': f'对话较长，正在并行整理 {total} 段...'}, ensure_ascii=False)}\n\n"

        sem = asyncio.Semaphore(MAP_CONCURRENCY)

        async def _map_one(idx: int, chunk: str):
            async with sem:
                return idx, await _map_chunk_to_notes(chunk, idx, total, reasoning_effort=reasoning_effort)

        tasks = [asyncio.ensure_future(_map_one(i, c)) for i, c in enumerate(chunks, 1)]
        results: dict[int, str] = {}
        done = 0
        # as_completed: emit progress as each chunk finishes (keeps the SSE alive and
        # shows real progress) while they run concurrently; order restored via the index.
        for fut in asyncio.as_completed(tasks):
            idx, note = await fut
            results[idx] = note
            done += 1
            yield f"data: {json.dumps({'type': 'progress', 'message': f'正在整理段落 {done}/{total}...'}, ensure_ascii=False)}\n\n"
        notes = [results[i] for i in range(1, total + 1)]
        yield f"data: {json.dumps({'type': 'progress', 'message': '正在汇总知识卡片...'}, ensure_ascii=False)}\n\n"
        prompt_text = REDUCE_PROMPT.format(notes="\n\n---\n\n".join(notes), date=today)
    else:
        prompt_text = SUMMARY_USER_TEMPLATE.format(conversation=conversation, date=today)

    content = ""
    try:
        llm = get_langchain_llm(reasoning_effort=reasoning_effort)
        aiter = llm.astream([
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": prompt_text},
        ]).__aiter__()
        chars_since_heartbeat = 0
        async for _kind, chunk in iter_chunks_with_idle(aiter, IDLE_HEARTBEAT_SECONDS):
            if _kind == "idle":
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                continue
            token = _chunk_text(chunk)
            if token:
                content += token
                chars_since_heartbeat += len(token)
                if chars_since_heartbeat >= 200:
                    yield f"data: {json.dumps({'type': 'progress', 'message': f'正在生成知识卡片... ({len(content)} 字)'}, ensure_ascii=False)}\n\n"
                    chars_since_heartbeat = 0
    except Exception as e:
        logger.error("Summary generation failed: %s", e)
        yield f"data: {json.dumps({'type': 'error', 'message': '生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"
        return

    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)

    # Model occasionally returns an empty completion (transient channel cooldown/failover)
    # without raising — don't persist a blank card or send an empty "complete"; surface a retry.
    if not content:
        logger.warning("Summary generation produced empty content for session %s", session_id)
        yield f"data: {json.dumps({'type': 'error', 'message': '生成结果为空，请重试（模型可能临时无响应）'}, ensure_ascii=False)}\n\n"
        return

    topic = _extract_topic(content)
    safe_topic = _sanitize_filename(topic)
    # Session id in the filename so get_summary_file can resolve THIS session's
    # card instead of whichever card was written most recently.
    filename = f"{today}-{safe_topic}-{session_id}.md"

    if not store.message_version_is_current(session_id, user_id, message_version):
        yield _sse({"type": "error", "message": "会话已被清空或删除，总结未保存"})
        return

    notes_dir = settings.base_dir / "data" / "qa_notes" / user_id
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / filename).write_text(content, encoding="utf-8")

    try:
        from backend.embedding_tasks import schedule_session_memory_index
        schedule_session_memory_index(
            session_id=session_id, topic=topic, summary=content[:4000],
            weak_points=[], user_id=user_id, insight_text=content[:4000],
        )
    except Exception as e:
        logger.warning("Failed to schedule QA summary indexing: %s", e)

    result = {"content": content, "filename": filename, "topic": topic}
    yield f"data: {json.dumps({'type': 'complete', 'data': result}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


def get_summary_file(session_id: str, user_id: str) -> tuple[str, str] | None:
    """Find the most recent summary file for a session. Returns (content, filename) or None."""
    notes_dir = settings.base_dir / "data" / "qa_notes" / user_id
    if not notes_dir.exists():
        return None
    suffix = f"-{session_id}.md"
    files = sorted(
        (f for f in notes_dir.glob("*.md") if f.name.endswith(suffix)),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return None
    f = files[0]
    return f.read_text(encoding="utf-8"), f.name
