"""Knowledge base management routes."""
import asyncio
import re
from fastapi import APIRouter, HTTPException, Depends

from backend.config import settings
from backend.indexer import load_topics, _index_cache, invalidate_topic_index, build_topic_index
from backend.auth import get_current_user

router = APIRouter(prefix="/api")


@router.get("/knowledge/{topic}/core")
async def get_core_knowledge(topic: str, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    if not topic_dir.exists():
        return []
    files = []
    for f in sorted(topic_dir.glob("*.md")):
        try:
            mtime = int(f.stat().st_mtime * 1000)
        except OSError:
            mtime = 0
        files.append({
            "filename": f.name,
            "content": f.read_text(encoding="utf-8"),
            "mtime": mtime,
        })
    return files


@router.put("/knowledge/{topic}/core/{filename}")
async def update_core_knowledge(topic: str, filename: str, body: dict,
                                user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    filepath = topic_dir / filename
    if not filepath.exists():
        raise HTTPException(404, f"File not found: {filename}")
    filepath.write_text(body.get("content", ""), encoding="utf-8")
    _index_cache.pop((user_id, topic), None)
    return {"ok": True}


@router.delete("/knowledge/{topic}/core/{filename}")
async def delete_core_knowledge(topic: str, filename: str,
                                user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    filepath = topic_dir / filename
    if not filepath.exists():
        raise HTTPException(404, f"File not found: {filename}")
    filepath.unlink()
    _index_cache.pop((user_id, topic), None)
    return {"ok": True}


@router.post("/knowledge/{topic}/core")
async def create_core_knowledge(topic: str, body: dict,
                                user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    filename = body.get("filename", "").strip()
    if not filename or not filename.endswith(".md"):
        raise HTTPException(400, "Filename must end with .md")
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    topic_dir.mkdir(parents=True, exist_ok=True)
    filepath = topic_dir / filename
    if filepath.exists():
        raise HTTPException(409, f"File already exists: {filename}")
    filepath.write_text(body.get("content", ""), encoding="utf-8")
    _index_cache.pop((user_id, topic), None)
    return {"ok": True, "filename": filename}


@router.post("/knowledge/{topic}/generate")
async def generate_core_knowledge(topic: str, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    from langchain_core.messages import SystemMessage, HumanMessage
    from backend.utils.sse_helpers import stream_llm_sse, streaming_response, sse_event

    topic_name = topics[topic].get("name", topic)
    lc_messages = [
        SystemMessage(content="你是一位资深技术面试官，擅长梳理技术领域的核心知识体系。"),
        HumanMessage(content=(
            f"请为「{topic_name}」这个技术领域生成一份核心知识梳理，作为面试出题和评分的参考依据。\n\n"
            "要求：\n"
            "- 用 Markdown 格式\n"
            f"- 以 `# {topic_name}` 作为标题\n"
            "- 列出该领域最核心的 8-12 个知识点，每个用二级标题\n"
            "- 每个知识点下用简洁的要点说明关键概念、原理、常见面试考点\n"
            "- 重点覆盖：核心概念、工作原理、最佳实践、常见陷阱\n"
            "- 保持简洁实用，面向面试准备场景\n"
            "- 直接输出 Markdown 内容，不要包裹在代码块中"
        )),
    ]

    async def _gen():
        content = ""
        async for kind, value in stream_llm_sse(lc_messages, progress_prefix="正在生成知识库"):
            if kind == "sse":
                yield value
            else:
                content = value.strip()

        topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
        topic_dir.mkdir(parents=True, exist_ok=True)
        (topic_dir / "README.md").write_text(content, encoding="utf-8")
        _index_cache.pop((user_id, topic), None)

        yield sse_event({"type": "complete", "data": {"ok": True, "content": content}})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.get("/knowledge/{topic}/high_freq")
async def get_high_freq(topic: str, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    filepath = settings.user_high_freq_path(user_id) / f"{topic}.md"
    if not filepath.exists():
        return {"content": "", "mtime": 0}
    try:
        mtime = int(filepath.stat().st_mtime * 1000)
    except OSError:
        mtime = 0
    return {"content": filepath.read_text(encoding="utf-8"), "mtime": mtime}


@router.put("/knowledge/{topic}/high_freq")
async def update_high_freq(topic: str, body: dict, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    hf_dir = settings.user_high_freq_path(user_id)
    hf_dir.mkdir(parents=True, exist_ok=True)
    filepath = hf_dir / f"{topic}.md"
    filepath.write_text(body.get("content", ""), encoding="utf-8")
    return {"ok": True}


@router.post("/knowledge/{topic}/rebuild")
async def rebuild_topic_index(topic: str, user_id: str = Depends(get_current_user)):
    """Force-rebuild a single topic's vector index. SSE progress stream."""
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")

    from backend.utils.sse_helpers import streaming_response, sse_event

    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    file_count = 0
    if topic_dir.exists():
        file_count = sum(1 for _ in topic_dir.glob("*.md"))

    async def _gen():
        yield sse_event({"type": "progress", "message": f"清理 {topic} 旧索引..."})
        await asyncio.to_thread(invalidate_topic_index, topic, user_id)
        yield sse_event({"type": "progress", "message": f"开始重建 {topic} 索引（{file_count} 个文件，预计 1-3 分钟）..."})
        try:
            await asyncio.to_thread(build_topic_index, topic, user_id, True)
            yield sse_event({"type": "complete", "data": {"ok": True, "topic": topic, "file_count": file_count}})
        except Exception as e:
            yield sse_event({"type": "error", "message": str(e)})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.post("/knowledge/rebuild-all")
async def rebuild_all_topics(user_id: str = Depends(get_current_user)):
    """Rebuild every topic's index sequentially. SSE progress stream."""
    topics = load_topics(user_id)
    if not topics:
        raise HTTPException(400, "No topics configured")

    from backend.utils.sse_helpers import streaming_response, sse_event

    async def _gen():
        total = len(topics)
        succeeded = 0
        failed = []
        for i, key in enumerate(topics.keys(), start=1):
            topic_dir = settings.user_knowledge_path(user_id) / topics[key]["dir"]
            file_count = sum(1 for _ in topic_dir.glob("*.md")) if topic_dir.exists() else 0
            yield sse_event({
                "type": "progress",
                "message": f"[{i}/{total}] 重建 {key}（{file_count} 文件）...",
            })
            try:
                await asyncio.to_thread(invalidate_topic_index, key, user_id)
                await asyncio.to_thread(build_topic_index, key, user_id, True)
                succeeded += 1
                yield sse_event({"type": "topic_done", "data": {"topic": key, "index": i, "total": total}})
            except Exception as e:
                failed.append({"topic": key, "message": str(e)})
                yield sse_event({"type": "topic_error", "data": {"topic": key, "message": str(e)}})
        yield sse_event({
            "type": "complete",
            "data": {"ok": True, "total": total, "succeeded": succeeded, "failed": failed},
        })
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.get("/knowledge/{topic}/stats")
async def get_knowledge_stats(topic: str, user_id: str = Depends(get_current_user)):
    """Return knowledge-base evolution stats for a topic.

    Surfaces (a) the most recent automatic write-back time, (b) total number of
    auto-deposits across the corpus, and (c) high-freq collection freshness —
    so users can verify the self-evolution loop is actually running.
    """
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")

    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]

    last_evolved_at = 0  # mtime of 自动沉淀.md (or any file containing auto-deposit markers)
    evolution_count = 0  # total <!-- 自动沉淀 ... --> markers across all files
    last_evolved_file = ""
    last_any_update_at = 0  # latest mtime across all .md files in this topic
    file_count = 0

    if topic_dir.exists():
        marker_re = re.compile(r"<!--\s*自动沉淀\s+[\d\-:\s]+-->")
        for f in topic_dir.glob("*.md"):
            file_count += 1
            try:
                mtime = int(f.stat().st_mtime * 1000)
            except OSError:
                mtime = 0
            if mtime > last_any_update_at:
                last_any_update_at = mtime

            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = marker_re.findall(content)
            if hits:
                evolution_count += len(hits)
                if mtime > last_evolved_at:
                    last_evolved_at = mtime
                    last_evolved_file = f.name

    hf_path = settings.user_high_freq_path(user_id) / f"{topic}.md"
    last_high_freq_at = 0
    high_freq_size = 0
    if hf_path.exists():
        try:
            last_high_freq_at = int(hf_path.stat().st_mtime * 1000)
            high_freq_size = hf_path.stat().st_size
        except OSError:
            pass

    return {
        "topic": topic,
        "file_count": file_count,
        "last_any_update_at": last_any_update_at,
        "last_evolved_at": last_evolved_at,
        "last_evolved_file": last_evolved_file,
        "evolution_count": evolution_count,
        "last_high_freq_at": last_high_freq_at,
        "high_freq_size": high_freq_size,
    }
