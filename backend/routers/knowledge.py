"""Knowledge base management routes."""
import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends

from backend.config import settings
from backend.indexer import load_topics, invalidate_topic_index, build_topic_index
from backend.embedding_tasks import schedule_index_rebuild, get_task_queue
from backend.auth import get_current_user

router = APIRouter(prefix="/api")

KNOWLEDGE_EXTS = (".md", ".txt", ".py")


def _validate_filename(filename: str) -> str:
    """Reject path-traversal / separators in a user-supplied filename.

    The file endpoints join `filename` under a per-user topic dir; without this
    a value like '../../../ai_config.json' would escape the user's knowledge
    root and let an authenticated user read/write/delete arbitrary files.
    """
    name = (filename or "").strip()
    if not name or name in (".", "..") or name != Path(name).name:
        raise HTTPException(400, f"Invalid filename: {filename}")
    return name


def _glob_knowledge_files(topic_dir: Path) -> list[Path]:
    """Return all knowledge files sorted by name, matching KNOWLEDGE_EXTS."""
    if not topic_dir.exists():
        return []
    return sorted(f for f in topic_dir.iterdir() if f.is_file() and f.suffix in KNOWLEDGE_EXTS)


def _count_files(user_id: str, topic_dir_name: str) -> int:
    topic_dir = settings.user_knowledge_path(user_id) / topic_dir_name
    return len(_glob_knowledge_files(topic_dir))


def _status_to_dict(st) -> dict:
    return {
        "task_id": st.task_id,
        "user_id": st.user_id,
        "topic": st.topic,
        "label": st.label,
        "state": st.state,
        "submitted_at": st.submitted_at,
        "started_at": st.started_at,
        "finished_at": st.finished_at,
        "file_count": st.file_count,
        "retry_count": st.retry_count,
        "error": st.error,
        "message": st.message,
    }


@router.get("/knowledge/{topic}/core")
async def get_core_knowledge(topic: str, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    files = []
    for f in _glob_knowledge_files(topic_dir):
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
    filename = _validate_filename(filename)
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    filepath = topic_dir / filename
    if not filepath.exists():
        raise HTTPException(404, f"File not found: {filename}")
    filepath.write_text(body.get("content", ""), encoding="utf-8")
    await asyncio.to_thread(invalidate_topic_index, topic, user_id)
    schedule_index_rebuild(topic, user_id)
    return {"ok": True}


@router.delete("/knowledge/{topic}/core/{filename}")
async def delete_core_knowledge(topic: str, filename: str,
                                user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    filename = _validate_filename(filename)
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    filepath = topic_dir / filename
    if not filepath.exists():
        raise HTTPException(404, f"File not found: {filename}")
    filepath.unlink()
    await asyncio.to_thread(invalidate_topic_index, topic, user_id)
    schedule_index_rebuild(topic, user_id)
    return {"ok": True}


@router.post("/knowledge/{topic}/core")
async def create_core_knowledge(topic: str, body: dict,
                                user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    filename = body.get("filename", "").strip()
    if not filename or not any(filename.endswith(ext) for ext in KNOWLEDGE_EXTS):
        raise HTTPException(400, f"Filename must end with one of {', '.join(KNOWLEDGE_EXTS)}")
    filename = _validate_filename(filename)
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    topic_dir.mkdir(parents=True, exist_ok=True)
    filepath = topic_dir / filename
    if filepath.exists():
        raise HTTPException(409, f"File already exists: {filename}")
    filepath.write_text(body.get("content", ""), encoding="utf-8")
    await asyncio.to_thread(invalidate_topic_index, topic, user_id)
    schedule_index_rebuild(topic, user_id)
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
        invalidate_topic_index(topic, user_id)
        schedule_index_rebuild(topic, user_id)

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


async def _submit_rebuild(topic: str, topic_info: dict, user_id: str) -> dict:
    """Invalidate cache (sync, off-loop) and submit rebuild task. Returns the manifest."""
    file_count = _count_files(user_id, topic_info["dir"])
    label = f"重建 {topic_info.get('name', topic)} 向量索引"
    await asyncio.to_thread(invalidate_topic_index, topic, user_id)
    task_id = schedule_index_rebuild(topic, user_id, file_count=file_count, label=label)
    return {"task_id": task_id, "topic": topic, "file_count": file_count}


@router.post("/knowledge/{topic}/rebuild")
async def rebuild_topic_index(topic: str, user_id: str = Depends(get_current_user)):
    """Submit a single-topic rebuild to the background queue. Returns immediately.

    The actual embedding work runs in EmbeddingTaskQueue workers. Poll
    /knowledge/rebuild-status to track progress. Submitting an in-flight
    rebuild for the same (user, topic) is a no-op (deduplicated by task_id).
    """
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")

    manifest = await _submit_rebuild(topic, topics[topic], user_id)
    return {
        "ok": True,
        **manifest,
        "message": f"已提交 {topic} 索引重建任务（{manifest['file_count']} 文件），可在状态接口查询进度",
    }


@router.post("/knowledge/rebuild-all")
async def rebuild_all_topics(user_id: str = Depends(get_current_user)):
    """Submit all topics' rebuild tasks. Returns the list of submitted task_ids."""
    topics = load_topics(user_id)
    if not topics:
        raise HTTPException(400, "No topics configured")

    submitted = [
        await _submit_rebuild(key, info, user_id)
        for key, info in topics.items()
    ]
    return {
        "ok": True,
        "total": len(submitted),
        "tasks": submitted,
        "message": f"已提交 {len(submitted)} 个重建任务，可在状态接口查询进度",
    }


@router.get("/knowledge/rebuild-status")
async def get_rebuild_status(user_id: str = Depends(get_current_user)):
    """Return all rebuild task statuses for the current user (newest first)."""
    queue = get_task_queue()
    statuses = queue.list_statuses(user_id=user_id, task_id_prefix=f"rebuild:{user_id}:")
    return {"tasks": [_status_to_dict(s) for s in statuses]}


@router.get("/knowledge/rebuild-status/{task_id:path}")
async def get_rebuild_task_status(task_id: str, user_id: str = Depends(get_current_user)):
    """Return a single rebuild task's status. 404 if unknown or not yours."""
    queue = get_task_queue()
    st = queue.get_status(task_id)
    if not st or st.user_id != user_id:
        raise HTTPException(404, "Task not found")
    return _status_to_dict(st)


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
        for f in _glob_knowledge_files(topic_dir):
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
