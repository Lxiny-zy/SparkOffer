"""Read-only knowledge training card generation helpers."""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.indexer import load_topics, topic_chunk_count
from backend.prompts.knowledge_training import (
    KNOWLEDGE_TRAINING_SYSTEM,
    build_knowledge_training_prompt,
)
from backend.utils.stream_parser import extract_complete_objects

KNOWLEDGE_EXTS = (".md", ".txt", ".py")
MAX_FILE_CHARS = 2_000_000
MAX_SECTION_CHARS = 6_000
MAX_CANDIDATE_SECTIONS = 600
MIN_SECTION_CHARS = 80
SUPPORTED_MODES = {"random", "high_freq"}
SUPPORTED_DEPTHS = {"basic", "understand", "interview_expression"}
MAX_SECTION_GROUP_SPAN = 3

NON_KNOWLEDGE_HEADING_RE = re.compile(
    r"(目录|大纲|计划|安排|冲刺|进度|打卡|复盘|刷题建议|每天固定时间|今日任务|todo|待办)",
    re.IGNORECASE,
)
SCHEDULE_CONTENT_RE = re.compile(
    r"(Day\s*\d+|第\s*\d+\s*天|周计划|题量|每天|固定时间|做不出|第二天复盘)",
    re.IGNORECASE,
)
KNOWLEDGE_SIGNAL_RE = re.compile(
    r"("
    r"定义|原理|机制|区别|适用|边界|优缺点|场景|流程|步骤|模式|策略|注意|陷阱|复杂度|"
    r"命令|参数|示例|代码|实现|源码|协议|事务|索引|缓存|锁|并发|线程|进程|内存|网络|"
    r"Docker|Linux|Python|Java|Redis|SQL|Kubernetes|Service|Ingress|Volume|tmpfs|grep|sed|awk"
    r")",
    re.IGNORECASE,
)


@dataclass
class KnowledgeSection:
    filename: str
    header_path: str
    content: str


@dataclass
class KnowledgeTrainingCard:
    id: str
    topic: str
    title: str
    knowledge: str
    example: str
    question: str
    answer: str
    tags: list[str]
    source_refs: list[dict[str, str]]


def _knowledge_files(topic_dir: Path) -> list[Path]:
    if not topic_dir.exists():
        return []
    return sorted(
        f for f in topic_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in KNOWLEDGE_EXTS
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
    except OSError:
        return ""


def _clean_heading(text: str) -> str:
    text = re.sub(r"\s+#*$", "", text.strip())
    return text[:120] or "未命名小节"


def _is_usable_section(content: str) -> bool:
    compact = re.sub(r"\s+", "", content)
    if len(compact) < MIN_SECTION_CHARS:
        return False
    if compact.count("-") + compact.count("#") > len(compact) * 0.55:
        return False
    return True


def _section_quality_score(section: KnowledgeSection) -> int:
    text = f"{section.header_path}\n{section.content}"
    compact = re.sub(r"\s+", "", section.content)
    score = 0

    if NON_KNOWLEDGE_HEADING_RE.search(section.header_path):
        score -= 5
    if len(SCHEDULE_CONTENT_RE.findall(section.content)) >= 2:
        score -= 4

    lines = [line.strip() for line in section.content.splitlines() if line.strip()]
    if lines:
        short_lines = sum(1 for line in lines if len(line) <= 24)
        if short_lines / len(lines) > 0.75 and not re.search(r"```|\|", section.content):
            score -= 2

    signals = KNOWLEDGE_SIGNAL_RE.findall(text)
    score += min(6, len(signals))
    if re.search(r"```|\|.+\|", section.content):
        score += 2
    if re.search(r"(是什么|为什么|如何|怎么|用于|表示|意味着|区别|对比)", section.content):
        score += 2
    if len(compact) >= 220:
        score += 1

    return score


def _is_trainable_section(section: KnowledgeSection) -> bool:
    return _section_quality_score(section) >= 1


def _clip_section(content: str) -> str:
    content = content.strip()
    if len(content) <= MAX_SECTION_CHARS:
        return content
    clipped = content[:MAX_SECTION_CHARS]
    last_break = max(clipped.rfind("\n\n"), clipped.rfind("\n"), clipped.rfind("。"))
    if last_break > MAX_SECTION_CHARS * 0.6:
        clipped = clipped[:last_break]
    return clipped.rstrip()


def _split_long_plain_text(text: str, filename: str) -> list[KnowledgeSection]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    sections: list[KnowledgeSection] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        content = "\n\n".join(buf).strip()
        if _is_usable_section(content):
            sections.append(KnowledgeSection(filename, Path(filename).stem, _clip_section(content)))
        buf = []
        buf_len = 0

    for block in blocks:
        if buf and buf_len + len(block) > MAX_SECTION_CHARS:
            flush()
        buf.append(block)
        buf_len += len(block)
    flush()
    return sections


def split_knowledge_sections(filename: str, text: str) -> list[KnowledgeSection]:
    """Split one knowledge file into trainable sections."""
    if not text.strip():
        return []
    if not filename.lower().endswith(".md"):
        return _split_long_plain_text(text, filename)

    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    stack: list[tuple[int, str]] = []
    current_path = Path(filename).stem
    current_lines: list[str] = []
    sections: list[KnowledgeSection] = []
    in_fence = False

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if _is_usable_section(content):
            sections.append(KnowledgeSection(filename, current_path, _clip_section(content)))

    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            current_lines.append(line)
            continue

        m = heading_re.match(line) if not in_fence else None
        if m:
            flush()
            level = len(m.group(1))
            title = _clean_heading(m.group(2))
            stack[:] = [(prev_level, prev_title) for prev_level, prev_title in stack if prev_level < level]
            stack.append((level, title))
            current_path = " > ".join(prev_title for _, prev_title in stack) if stack else Path(filename).stem
            current_lines = []
        else:
            current_lines.append(line)

    flush()

    if sections:
        return sections
    return _split_long_plain_text(text, filename)


def _dedupe_sections(sections: list[KnowledgeSection]) -> list[KnowledgeSection]:
    out: list[KnowledgeSection] = []
    seen: set[str] = set()
    for section in sections:
        key = hashlib.sha1(re.sub(r"\s+", "", section.content[:800]).encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(section)
        if len(out) >= MAX_CANDIDATE_SECTIONS:
            break
    return out


def _section_group_key(section: KnowledgeSection) -> tuple[str, ...]:
    parts = [part.strip() for part in section.header_path.split(">") if part.strip()]
    if parts:
        return (section.filename, parts[0])
    return (section.filename,)


def _coherent_section_sample(
    sections: list[KnowledgeSection],
    count: int,
    rnd: random.Random,
) -> list[KnowledgeSection]:
    if count >= len(sections):
        return sections[:]

    groups: dict[tuple[str, ...], list[KnowledgeSection]] = {}
    for section in sections:
        groups.setdefault(_section_group_key(section), []).append(section)

    grouped = list(groups.values())
    rnd.shuffle(grouped)

    selected: list[KnowledgeSection] = []
    seen: set[int] = set()
    for group in grouped:
        remaining = count - len(selected)
        if remaining <= 0:
            break
        take = min(len(group), remaining, MAX_SECTION_GROUP_SPAN)
        start = 0 if len(group) == take else rnd.randrange(0, len(group) - take + 1)
        for section in group[start:start + take]:
            marker = id(section)
            if marker in seen:
                continue
            selected.append(section)
            seen.add(marker)

    if len(selected) < count:
        rest = [section for section in sections if id(section) not in seen]
        rnd.shuffle(rest)
        selected.extend(rest[:count - len(selected)])

    return selected[:count]


def collect_topic_sections(user_id: str, topic: str, mode: str = "random") -> list[KnowledgeSection]:
    topics = load_topics(user_id)
    if topic not in topics:
        raise ValueError(f"Unknown topic: {topic}")

    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    sections: list[KnowledgeSection] = []

    if mode == "high_freq":
        high_freq_path = settings.user_high_freq_path(user_id) / f"{topic}.md"
        if high_freq_path.exists():
            sections.extend(split_knowledge_sections(high_freq_path.name, _read_text(high_freq_path)))

    for file_path in _knowledge_files(topic_dir):
        rel_name = str(file_path.relative_to(topic_dir)).replace("\\", "/")
        sections.extend(split_knowledge_sections(rel_name, _read_text(file_path)))
        if len(sections) >= MAX_CANDIDATE_SECTIONS:
            break

    deduped = _dedupe_sections(sections)
    trainable = [section for section in deduped if _is_trainable_section(section)]
    return trainable or deduped


def sample_topic_sections(
    user_id: str,
    topic: str,
    count: int,
    mode: str = "random",
    seed: str | None = None,
    exclude_section_keys: set[tuple[str, str]] | None = None,
) -> tuple[list[KnowledgeSection], str]:
    if mode not in SUPPORTED_MODES:
        raise ValueError("当前版本仅支持随机知识点和高频考点模式")
    count = max(1, min(20, int(count or 5)))
    seed_value = seed or f"{topic}:{time.time_ns()}"
    sections = collect_topic_sections(user_id, topic, mode=mode)
    rnd = random.Random(hashlib.sha256(seed_value.encode("utf-8")).hexdigest())

    # Section-coverage dedup: steer sampling toward sections that haven't been
    # turned into cards yet, so each round covers new ground. Only fall back to
    # already-carded sections when there aren't enough fresh ones.
    if exclude_section_keys:
        uncovered = [s for s in sections if _section_ref_key(s) not in exclude_section_keys]
        if len(uncovered) >= count:
            sampled = _coherent_section_sample(uncovered, count, rnd)
            return sampled, seed_value
        if uncovered:
            covered = [s for s in sections if _section_ref_key(s) in exclude_section_keys]
            fill = _coherent_section_sample(covered, count - len(uncovered), rnd)
            return (_coherent_section_sample(uncovered, len(uncovered), rnd) + fill)[:count], seed_value
        # Everything is already covered → allow re-coverage (review-by-regeneration).

    sampled = _coherent_section_sample(sections, count, rnd)
    return sampled, seed_value


def build_llm_messages(
    topic_name: str,
    depth: str,
    sections: list[KnowledgeSection],
    target_count: int,
) -> list[dict[str, str]]:
    if depth not in SUPPORTED_DEPTHS:
        depth = "understand"
    payload = [
        {
            "index": i + 1,
            "filename": section.filename,
            "header_path": section.header_path,
            "content": section.content,
        }
        for i, section in enumerate(sections)
    ]
    sections_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": KNOWLEDGE_TRAINING_SYSTEM},
        {"role": "user", "content": build_knowledge_training_prompt(topic_name, depth, sections_json, target_count)},
    ]


def _strip_reasoning_and_fences(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    if text.lower().startswith("<think>"):
        idx = next((i for i, ch in enumerate(text) if ch in "[{"), -1)
        text = text[idx:] if idx >= 0 else ""
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_llm_cards(raw: str) -> list[dict[str, Any]]:
    text = _strip_reasoning_and_fences(raw)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        parsed = None
        for i, ch in enumerate(text):
            if ch not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[i:])
                break
            except json.JSONDecodeError:
                continue
        if parsed is None:
            salvaged, _ = extract_complete_objects(text)
            return salvaged
    if isinstance(parsed, dict):
        parsed = parsed.get("cards") or parsed.get("data") or []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip() if value is not None else ""


def _format_knowledge(value: Any) -> str:
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            line = _coerce_text(item)
            if not line:
                continue
            if re.match(r"^\s*[-*+]\s+", line):
                lines.append(line)
            else:
                lines.append(f"- {line}")
        return "\n".join(lines).strip()

    text = _coerce_text(value)
    if not text or "```" in text or re.search(r"(?m)^\s*[-*+]\s+", text):
        return text

    lines = [
        re.sub(r"^\s*(?:\d+[.)、]\s+)", "", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    if len(lines) > 1:
        return "\n".join(f"- {line}" for line in lines)
    return text


def _section_ref_key(section: KnowledgeSection) -> tuple[str, str]:
    return (section.filename.strip(), section.header_path.strip())


def _ref_key(filename: str, header_path: str) -> tuple[str, str]:
    return (filename.strip(), header_path.strip())


def _section_by_ref(sections: list[KnowledgeSection]) -> dict[tuple[str, str], KnowledgeSection]:
    return {_section_ref_key(section): section for section in sections}


def _source_index_section(value: Any, sections: list[KnowledgeSection]) -> KnowledgeSection | None:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= idx <= len(sections):
        return sections[idx - 1]
    return None


def _fallback_section(
    item: dict[str, Any],
    positional: KnowledgeSection,
    sections: list[KnowledgeSection],
) -> tuple[KnowledgeSection, bool]:
    """
    Resolve the authoritative source section for a card.

    Returns (section, from_index) where from_index is True when the section was
    resolved from an explicit source_index — in that case source_refs should be
    overridden by this section rather than trusted.
    """
    indexed = _source_index_section(item.get("source_index"), sections)
    if indexed:
        return indexed, True

    by_ref = _section_by_ref(sections)
    raw_refs = item.get("source_refs")
    if isinstance(raw_refs, list):
        for ref in raw_refs:
            if not isinstance(ref, dict):
                continue
            filename = _coerce_text(ref.get("filename"))
            header_path = _coerce_text(ref.get("header_path"))
            section = by_ref.get(_ref_key(filename, header_path))
            if section:
                return section, False
    return positional, False


def _looks_like_low_quality_card(title: str, knowledge: str, example: str, question: str, answer: str) -> bool:
    text = "\n".join([title, knowledge, example, question, answer])
    if NON_KNOWLEDGE_HEADING_RE.search(title):
        return True
    if len(SCHEDULE_CONTENT_RE.findall(text)) >= 2:
        return True
    # Answer floor raised 18→40: the prompt now demands 结论→机制→边界 layered
    # answers (~80-200 chars when the source supports it); a sub-40-char answer
    # is a bare conclusion with no recall value when reviewed against question.
    if len(answer) < 40 or len(question) < 10:
        return True

    # 完整过滤代码块
    in_code_block = False
    lines = []
    for line in knowledge.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        lines.append(stripped)

    if lines:
        complete = 0
        for line in lines:
            clean_line = re.sub(r"^\s*[-*+]\s+", "", line)
            if len(clean_line) >= 22 or re.search(r"[，。；：、,.!?！？;:()（）]", clean_line):
                complete += 1
        if complete / len(lines) < 0.5:
            return True

    if not KNOWLEDGE_SIGNAL_RE.search(text):
        return True
    return False

def _normalize_source_refs(
    value: Any,
    fallback: KnowledgeSection,
    sections: list[KnowledgeSection],
    prefer_fallback: bool = False,
) -> list[dict[str, str]]:
    """
    Normalize source_refs from LLM output.

    Args:
        value: Raw source_refs from LLM (list of dicts or None)
        fallback: The authoritative source section (from source_index or positional)
        sections: All candidate sections for validation
        prefer_fallback: If True, ignore value and use fallback directly
                         (set when fallback came from source_index)
    """
    if prefer_fallback:
        return [{"filename": fallback.filename, "header_path": fallback.header_path}]

    refs: list[dict[str, str]] = []
    by_ref = _section_by_ref(sections)
    if isinstance(value, list):
        for ref in value:
            if not isinstance(ref, dict):
                continue
            filename = _coerce_text(ref.get("filename"))
            header_path = _coerce_text(ref.get("header_path"))
            key = _ref_key(filename, header_path)
            if key in by_ref:
                refs.append({"filename": filename, "header_path": header_path})
    if not refs:
        refs.append({"filename": fallback.filename, "header_path": fallback.header_path})
    return refs[:3]


def normalize_training_cards(
    raw: str,
    *,
    topic: str,
    sections: list[KnowledgeSection],
) -> list[dict[str, Any]]:
    if not sections:
        return []
    parsed = _parse_llm_cards(raw)
    cards: list[KnowledgeTrainingCard] = []
    for i, item in enumerate(parsed):
        positional = sections[min(i, len(sections) - 1)]
        fallback, from_index = _fallback_section(item, positional, sections)
        title = _coerce_text(item.get("title"))[:120]
        knowledge = _format_knowledge(item.get("knowledge"))
        example = _coerce_text(item.get("example"))
        question = _coerce_text(item.get("question"))
        answer = _coerce_text(item.get("answer"))
        if not all([title, knowledge, example, question, answer]):
            continue
        if _looks_like_low_quality_card(title, knowledge, example, question, answer):
            continue

        raw_tags = item.get("tags")
        tags = [
            _coerce_text(tag)[:24]
            for tag in (raw_tags if isinstance(raw_tags, list) else [])
            if _coerce_text(tag)
        ][:6]
        source_refs = _normalize_source_refs(
            item.get("source_refs"), fallback, sections, prefer_fallback=from_index
        )
        card_hash = hashlib.sha1(
            f"{topic}|{title}|{source_refs[0]['filename']}|{source_refs[0].get('header_path', '')}".encode("utf-8")
        ).hexdigest()[:12]
        cards.append(KnowledgeTrainingCard(
            id=f"kt-{card_hash}",
            topic=topic,
            title=title,
            knowledge=knowledge,
            example=example,
            question=question,
            answer=answer,
            tags=tags,
            source_refs=source_refs,
        ))
    return [asdict(card) for card in cards]


def topic_availability(user_id: str) -> dict[str, Any]:
    topics = load_topics(user_id)
    result: dict[str, Any] = {}
    for key, info in topics.items():
        topic_dir = settings.user_knowledge_path(user_id) / info["dir"]
        file_count = len(_knowledge_files(topic_dir))
        chunk_count = topic_chunk_count(key, user_id)
        result[key] = {
            "name": info.get("name", key),
            "icon": info.get("icon", ""),
            "file_count": file_count,
            "chunk_count": chunk_count,
            "available": file_count > 0,
        }
    return {"topics": result}
