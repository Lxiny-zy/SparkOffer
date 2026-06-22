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
    stack: list[str] = []
    current_path = Path(filename).stem
    current_lines: list[str] = []
    sections: list[KnowledgeSection] = []

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if _is_usable_section(content):
            sections.append(KnowledgeSection(filename, current_path, _clip_section(content)))

    for line in text.splitlines():
        m = heading_re.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = _clean_heading(m.group(2))
            stack[:] = stack[: max(0, level - 1)]
            stack.append(title)
            current_path = " > ".join(stack) if stack else Path(filename).stem
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

    return _dedupe_sections(sections)


def sample_topic_sections(
    user_id: str,
    topic: str,
    count: int,
    mode: str = "random",
    seed: str | None = None,
) -> tuple[list[KnowledgeSection], str]:
    if mode not in SUPPORTED_MODES:
        raise ValueError("当前版本仅支持随机知识点和高频考点模式")
    count = max(1, min(10, int(count or 5)))
    seed_value = seed or f"{topic}:{time.time_ns()}"
    sections = collect_topic_sections(user_id, topic, mode=mode)
    rnd = random.Random(hashlib.sha256(seed_value.encode("utf-8")).hexdigest())
    sampled = sections[:]
    rnd.shuffle(sampled)
    return sampled[:count], seed_value


def build_llm_messages(topic_name: str, depth: str, sections: list[KnowledgeSection]) -> list[dict[str, str]]:
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
        {"role": "user", "content": build_knowledge_training_prompt(topic_name, depth, sections_json)},
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


def _normalize_source_refs(value: Any, fallback: KnowledgeSection) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if isinstance(value, list):
        for ref in value:
            if not isinstance(ref, dict):
                continue
            filename = _coerce_text(ref.get("filename"))
            header_path = _coerce_text(ref.get("header_path"))
            if filename:
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
    parsed = _parse_llm_cards(raw)
    cards: list[KnowledgeTrainingCard] = []
    for i, item in enumerate(parsed):
        fallback = sections[min(i, len(sections) - 1)]
        title = _coerce_text(item.get("title"))[:120]
        knowledge = _coerce_text(item.get("knowledge"))
        example = _coerce_text(item.get("example"))
        question = _coerce_text(item.get("question"))
        answer = _coerce_text(item.get("answer"))
        if not all([title, knowledge, example, question, answer]):
            continue

        raw_tags = item.get("tags")
        tags = [
            _coerce_text(tag)[:24]
            for tag in (raw_tags if isinstance(raw_tags, list) else [])
            if _coerce_text(tag)
        ][:6]
        source_refs = _normalize_source_refs(item.get("source_refs"), fallback)
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
