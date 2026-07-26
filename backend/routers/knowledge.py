"""Knowledge base management routes."""
import asyncio
import codecs
import hashlib
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Header
from pydantic import BaseModel, Field

from backend.config import settings
from backend.indexer import load_topics, evict_topic_cache, topic_chunk_count
from backend.embedding_tasks import schedule_index_rebuild, try_schedule_index_rebuild, get_task_queue
from backend.auth import get_current_user
from backend.utils.files import (
    atomic_write_text,
    exclusive_file_lock,
    file_mutation_lock_path,
)

router = APIRouter(prefix="/api")
_upload_promote_lock = asyncio.Lock()
logger = logging.getLogger("uvicorn")

KNOWLEDGE_EXTS = (".md", ".txt", ".py")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200MB per file
MAX_UPLOAD_FILES = 20
MAX_UPLOAD_BATCH_BYTES = 500 * 1024 * 1024
MAX_CORE_INLINE_BYTES = 256 * 1024
MAX_CORE_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SINGLE_CORE_READ_BYTES = 8 * 1024 * 1024
# Cap for inline JSON-body edits (PUT/POST {content}). FastAPI has already parsed
# the whole body into memory by the time we see it, so this is a post-hoc guard
# against persisting an absurd document, not a transport limit. ~8MB of text is
# far above any real knowledge file (a 30万字 .md is ~1MB) yet blocks runaways.
MAX_DOC_CHARS = 8 * 1024 * 1024


class KnowledgeContentRequest(BaseModel):
    content: str = ""
    expected_version: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$",
    )


class KnowledgeCreateRequest(KnowledgeContentRequest):
    filename: str = Field(max_length=255)


def _check_doc_size(content: str) -> None:
    if not isinstance(content, str):
        raise HTTPException(422, "content must be a string")
    if len(content) > MAX_DOC_CHARS:
        raise HTTPException(413, f"内容过大（>{MAX_DOC_CHARS // (1024*1024)}MB），请拆分后再保存")


def _body_value(body: BaseModel | dict, field: str, default=""):
    """Keep direct unit callers safe while FastAPI supplies validated models."""
    if isinstance(body, BaseModel):
        return getattr(body, field, default)
    if isinstance(body, dict):
        return body.get(field, default)
    raise HTTPException(422, "request body must be an object")


class _KnowledgeVersionConflict(RuntimeError):
    pass


def _content_version(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _stream_content_version(path: Path) -> str:
    """Hash decoded text without loading the complete file into memory."""
    digest = hashlib.sha256()
    # Text mode intentionally mirrors Path.read_text(): universal-newline
    # translation keeps this token identical to _content_version(content).
    with path.open("r", encoding="utf-8", newline=None) as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _read_versioned_summary(path: Path, *, inline_limit: int) -> dict:
    """Return one stable list snapshot, streaming the hash when not inlining."""
    with exclusive_file_lock(file_mutation_lock_path(path)):
        if not path.exists():
            raise FileNotFoundError(path)
        stat = path.stat()
        if stat.st_size <= inline_limit:
            content = path.read_text(encoding="utf-8")
            version = _content_version(content)
            loaded = True
        else:
            content = ""
            version = _stream_content_version(path)
            loaded = False
        return {
            "filename": path.name,
            "content": content,
            "mtime": int(stat.st_mtime * 1000),
            "size": stat.st_size,
            "content_loaded": loaded,
            "version": version,
        }


def _read_versioned_text(
    path: Path,
    *,
    allow_missing: bool = False,
    max_bytes: int | None = None,
) -> dict:
    with exclusive_file_lock(file_mutation_lock_path(path)):
        if not path.exists():
            if allow_missing:
                content = ""
                return {
                    "content": content,
                    "version": _content_version(content),
                    "mtime": 0,
                    "size": 0,
                }
            raise FileNotFoundError(path)
        stat = path.stat()
        if max_bytes is not None and stat.st_size > max_bytes:
            raise OverflowError(path)
        content = path.read_text(encoding="utf-8")
        return {
            "content": content,
            "version": _content_version(content),
            "mtime": int(stat.st_mtime * 1000),
            "size": stat.st_size,
        }


def _write_versioned_text(
    path: Path,
    content: str,
    *,
    expected_version: str | None = None,
    create_only: bool = False,
    require_exists: bool = False,
) -> dict:
    with exclusive_file_lock(file_mutation_lock_path(path)):
        exists = path.exists()
        if create_only and exists:
            raise FileExistsError(path)
        if require_exists and not exists:
            raise FileNotFoundError(path)
        if not create_only and expected_version is not None:
            current_version = (
                _stream_content_version(path)
                if exists
                else _content_version("")
            )
            if current_version != expected_version:
                raise _KnowledgeVersionConflict(path.name)
        atomic_write_text(path, content)
        stat = path.stat()
        return {
            "version": _content_version(content),
            "mtime": int(stat.st_mtime * 1000),
            "size": stat.st_size,
        }


def _delete_versioned_file(path: Path, expected_version: str | None = None) -> None:
    with exclusive_file_lock(file_mutation_lock_path(path)):
        if not path.exists():
            raise FileNotFoundError(path)
        if expected_version is not None:
            if _stream_content_version(path) != expected_version:
                raise _KnowledgeVersionConflict(path.name)
        path.unlink()


def _validate_filename(filename: object) -> str:
    """Reject path-traversal / separators in a user-supplied filename.

    The file endpoints join `filename` under a per-user topic dir; without this
    a value like '../../../ai_config.json' would escape the user's knowledge
    root and let an authenticated user read/write/delete arbitrary files.
    """
    if not isinstance(filename, str):
        raise HTTPException(422, "filename must be a string")
    name = filename.strip()
    stem = Path(name).stem.upper()
    if (
        not name
        or len(name) > 255
        or name in (".", "..")
        or name != Path(name).name
        or name.rstrip(" .") != name
        or any(ord(char) < 32 for char in name)
        or stem in _WINDOWS_RESERVED_NAMES
    ):
        raise HTTPException(400, f"Invalid filename: {filename}")
    return name


def _dedupe_filename(topic_dir: Path, filename: str) -> str:
    """Return a non-colliding filename within topic_dir.

    If `filename` already exists, append ' (1)', ' (2)', ... to the stem until a
    free name is found, so uploads never overwrite existing knowledge files.
    """
    if not (topic_dir / filename).exists():
        return filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while True:
        candidate = f"{stem} ({i}){suffix}"
        if not (topic_dir / candidate).exists():
            return candidate
        i += 1


def _promote_staged_uploads(
    topic_dir: Path,
    staged: list[tuple[str, Path]],
) -> list[Path]:
    """Promote a batch and roll every promoted file back on any failure."""
    promoted: list[Path] = []
    try:
        for name, temp_path in staged:
            while True:
                final_name = _dedupe_filename(topic_dir, name)
                filepath = topic_dir / final_name
                with exclusive_file_lock(file_mutation_lock_path(filepath)):
                    if filepath.exists():
                        continue
                    os.replace(temp_path, filepath)
                    break
            promoted.append(filepath)
    except Exception:
        _rollback_promoted_uploads(promoted)
        raise
    return promoted


def _rollback_promoted_uploads(promoted: list[Path]) -> None:
    for filepath in reversed(promoted):
        try:
            with exclusive_file_lock(file_mutation_lock_path(filepath)):
                filepath.unlink(missing_ok=True)
        except OSError as rollback_exc:
            logger.error(
                "Unable to roll back promoted knowledge file %s: %s",
                filepath,
                rollback_exc,
            )


async def _commit_upload_transaction(
    topic_dir: Path,
    staged: list[tuple[str, Path]],
    topic: str,
    user_id: str,
) -> list[str]:
    promoted = await asyncio.to_thread(_promote_staged_uploads, topic_dir, staged)
    try:
        # Queue submission owns asyncio state and must run on the event-loop
        # thread. It is still part of the transaction: failure rolls every
        # newly-visible source file back.
        evict_topic_cache(topic, user_id)
        schedule_index_rebuild(topic, user_id)
    except Exception:
        await asyncio.to_thread(_rollback_promoted_uploads, promoted)
        try:
            evict_topic_cache(topic, user_id)
        except Exception as cache_exc:
            logger.warning("Unable to evict topic cache during upload rollback: %s", cache_exc)
        raise
    return [path.name for path in promoted]


async def _run_transaction_to_completion(coro):
    """Wait for a filesystem worker even when the HTTP client disconnects."""
    task = asyncio.create_task(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


def _glob_knowledge_files(topic_dir: Path) -> list[Path]:
    """Return all knowledge files sorted by name, matching KNOWLEDGE_EXTS."""
    if not topic_dir.exists():
        return []
    allowed = {ext.lower() for ext in KNOWLEDGE_EXTS}
    return sorted(
        f for f in topic_dir.iterdir()
        if f.is_file() and f.suffix.lower() in allowed
    )


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
        "progress_done": st.progress_done,
        "progress_total": st.progress_total,
    }


@router.get("/knowledge/{topic}/core")
async def get_core_knowledge(topic: str, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]

    def _read_all() -> list[dict]:
        # Synchronous full-text reads of every file — pushed to a worker thread so
        # a topic with many 30万字 files can't stall the event loop while it reads.
        out = []
        budget = MAX_CORE_RESPONSE_BYTES
        for f in _glob_knowledge_files(topic_dir):
            try:
                snapshot = _read_versioned_summary(
                    f,
                    inline_limit=min(MAX_CORE_INLINE_BYTES, max(0, budget)),
                )
            except (OSError, UnicodeError):
                try:
                    stat = f.stat()
                    mtime = int(stat.st_mtime * 1000)
                    size = stat.st_size
                except OSError:
                    mtime = 0
                    size = 0
                snapshot = {
                    "filename": f.name,
                    "content": "",
                    "mtime": mtime,
                    "size": size,
                    "content_loaded": False,
                    "version": None,
                }
            if snapshot["content_loaded"]:
                budget -= len(snapshot["content"].encode("utf-8"))
            out.append(snapshot)
        return out

    return await asyncio.to_thread(_read_all)


@router.get("/knowledge/{topic}/core/{filename}")
async def get_core_file(topic: str, filename: str,
                        user_id: str = Depends(get_current_user)):
    """Read one file on demand; the aggregate endpoint never returns huge files."""
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    filename = _validate_filename(filename)
    filepath = settings.user_knowledge_path(user_id) / topics[topic]["dir"] / filename
    if not filepath.is_file():
        raise HTTPException(404, f"File not found: {filename}")
    try:
        snapshot = await asyncio.to_thread(
            _read_versioned_text,
            filepath,
            max_bytes=MAX_SINGLE_CORE_READ_BYTES,
        )
    except FileNotFoundError:
        raise HTTPException(404, f"File not found: {filename}")
    except OverflowError:
        raise HTTPException(413, "File is too large for inline reading")
    except UnicodeError as exc:
        raise HTTPException(422, "File is not valid UTF-8") from exc
    except OSError as exc:
        raise HTTPException(500, "Unable to read file") from exc
    return {
        "filename": filename, **snapshot,
        "content_loaded": True,
    }


@router.put("/knowledge/{topic}/core/{filename}")
async def update_core_knowledge(topic: str, filename: str, body: KnowledgeContentRequest,
                                user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    filename = _validate_filename(filename)
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    filepath = topic_dir / filename
    if not filepath.exists():
        raise HTTPException(404, f"File not found: {filename}")
    content = _body_value(body, "content")
    _check_doc_size(content)
    expected_version = _body_value(body, "expected_version", None)
    try:
        written = await asyncio.to_thread(
            _write_versioned_text,
            filepath,
            content,
            expected_version=expected_version,
            require_exists=True,
        )
    except _KnowledgeVersionConflict as exc:
        raise HTTPException(
            409, "File changed since it was loaded; reload before saving."
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, f"File not found: {filename}") from exc
    evict_topic_cache(topic, user_id)
    schedule_index_rebuild(topic, user_id)
    return {"ok": True, **written}


@router.delete("/knowledge/{topic}/core/{filename}")
async def delete_core_knowledge(topic: str, filename: str,
                                user_id: str = Depends(get_current_user),
                                expected_version: Annotated[
                                    str | None,
                                    Header(
                                        alias="If-Match",
                                        pattern=r"^[a-f0-9]{64}$",
                                    ),
                                ] = None):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    filename = _validate_filename(filename)
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    filepath = topic_dir / filename
    if not filepath.exists():
        raise HTTPException(404, f"File not found: {filename}")
    try:
        await asyncio.to_thread(
            _delete_versioned_file, filepath, expected_version,
        )
    except _KnowledgeVersionConflict as exc:
        raise HTTPException(
            409, "File changed since it was loaded; reload before deleting."
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, f"File not found: {filename}") from exc
    evict_topic_cache(topic, user_id)
    schedule_index_rebuild(topic, user_id)
    return {"ok": True}


@router.post("/knowledge/{topic}/core")
async def create_core_knowledge(topic: str, body: KnowledgeCreateRequest,
                                user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    filename = _validate_filename(_body_value(body, "filename", None))
    if not any(filename.lower().endswith(ext) for ext in KNOWLEDGE_EXTS):
        raise HTTPException(400, f"Filename must end with one of {', '.join(KNOWLEDGE_EXTS)}")
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    topic_dir.mkdir(parents=True, exist_ok=True)
    filepath = topic_dir / filename
    content = _body_value(body, "content")
    _check_doc_size(content)
    try:
        written = await asyncio.to_thread(
            _write_versioned_text, filepath, content, create_only=True,
        )
    except FileExistsError as exc:
        raise HTTPException(409, f"File already exists: {filename}") from exc
    evict_topic_cache(topic, user_id)
    schedule_index_rebuild(topic, user_id)
    return {"ok": True, "filename": filename, **written}


@router.post("/knowledge/{topic}/upload")
async def upload_core_knowledge(topic: str, files: list[UploadFile] = File(...),
                                user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
    topic_dir.mkdir(parents=True, exist_ok=True)
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(413, f"Upload at most {MAX_UPLOAD_FILES} files per request")

    saved, skipped, rejected = [], [], []
    staged: list[tuple[str, Path]] = []
    temp_paths: list[Path] = []
    batch_total = 0
    try:
        for file in files:
            try:
                name = _validate_filename(file.filename)
            except HTTPException:
                rejected.append(file.filename)
                continue
            if not name.lower().endswith(".md"):
                skipped.append(name)
                continue
            temp_path = topic_dir / f".{uuid.uuid4().hex}.upload"
            temp_paths.append(temp_path)
            total = 0
            too_large = False
            invalid_utf8 = False
            decoder = codecs.getincrementaldecoder("utf-8")()
            with temp_path.open("xb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    batch_total += len(chunk)
                    if batch_total > MAX_UPLOAD_BATCH_BYTES:
                        raise HTTPException(
                            413,
                            "Combined upload size exceeds "
                            f"{MAX_UPLOAD_BATCH_BYTES // (1024 * 1024)}MB",
                        )
                    if total > MAX_UPLOAD_BYTES:
                        too_large = True
                        break
                    try:
                        decoder.decode(chunk)
                    except UnicodeDecodeError:
                        invalid_utf8 = True
                        break
                    out.write(chunk)
                if not too_large and not invalid_utf8:
                    try:
                        decoder.decode(b"", final=True)
                    except UnicodeDecodeError:
                        invalid_utf8 = True
            if too_large or invalid_utf8:
                rejected.append(name)
                continue
            staged.append((name, temp_path))

        if staged:
            async with _upload_promote_lock:
                saved = await _run_transaction_to_completion(
                    _commit_upload_transaction(topic_dir, staged, topic, user_id),
                )
    finally:
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)

    return {"ok": True, "saved": saved, "skipped": skipped, "rejected": rejected}


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
            elif kind == "error":
                return  # LLM failed — keep the existing README.md untouched
            else:
                content = value.strip()

        if not content:
            yield sse_event({"type": "error", "message": "生成内容为空，已保留原有知识库。"})
            return

        topic_dir = settings.user_knowledge_path(user_id) / topics[topic]["dir"]
        await asyncio.to_thread(
            _write_versioned_text, topic_dir / "README.md", content,
        )
        evict_topic_cache(topic, user_id)
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
    try:
        return await asyncio.to_thread(
            _read_versioned_text, filepath, allow_missing=True,
        )
    except UnicodeError as exc:
        raise HTTPException(422, "High-frequency file is not valid UTF-8") from exc
    except OSError as exc:
        raise HTTPException(500, "Unable to read high-frequency file") from exc


@router.put("/knowledge/{topic}/high_freq")
async def update_high_freq(
    topic: str, body: KnowledgeContentRequest,
    user_id: str = Depends(get_current_user),
):
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")
    content = _body_value(body, "content")
    _check_doc_size(content)
    filepath = settings.user_high_freq_path(user_id) / f"{topic}.md"
    expected_version = _body_value(body, "expected_version", None)
    try:
        written = await asyncio.to_thread(
            _write_versioned_text,
            filepath,
            content,
            expected_version=expected_version,
        )
    except _KnowledgeVersionConflict as exc:
        raise HTTPException(
            409, "High-frequency file changed; reload before saving."
        ) from exc
    return {"ok": True, **written}


async def _submit_rebuild(topic: str, topic_info: dict, user_id: str,
                          force: bool = False) -> dict:
    """Submit a rebuild task and report whether it actually entered the queue.

    Force rebuild used to invalidate the persisted index before enqueueing. If
    the queue was full or the task was deduplicated, Docker/Qdrant deployments
    could be left with an empty retriever while the API still returned success.
    Force invalidation is now deferred to the worker and only happens after a
    task has been accepted by the queue.
    """
    file_count = _count_files(user_id, topic_info["dir"])
    label = f"重建 {topic_info.get('name', topic)} 向量索引"
    if not force:
        evict_topic_cache(topic, user_id)
    result = try_schedule_index_rebuild(
        topic, user_id, file_count=file_count, label=label,
        force_invalidate=force,
    )
    return {
        "task_id": result.task_id,
        "topic": topic,
        "file_count": file_count,
        "submitted": result.submitted,
        "reason": result.reason,
    }

@router.post("/knowledge/{topic}/rebuild")
async def rebuild_topic_index(topic: str, force: bool = False,
                              user_id: str = Depends(get_current_user)):
    """Submit a single-topic rebuild to the background queue. Returns immediately.

    Incremental by default (manifest diff, only changed files re-embed);
    ``?force=true`` wipes the collection + manifest for a full re-embed. The
    actual embedding work runs in EmbeddingTaskQueue workers. Poll
    /knowledge/rebuild-status to track progress. Submitting an in-flight
    rebuild for the same (user, topic) is a no-op (deduplicated by task_id).
    """
    topics = load_topics(user_id)
    if topic not in topics:
        raise HTTPException(400, f"Unknown topic: {topic}")

    manifest = await _submit_rebuild(topic, topics[topic], user_id, force=force)
    if not manifest["submitted"] and manifest.get("reason") == "queue_full":
        raise HTTPException(503, {
            "message": "Embedding rebuild queue is full; index was not invalidated.",
            **manifest,
        })
    mode = "full" if force else "incremental"
    status_text = "already queued" if not manifest["submitted"] else "submitted"
    return {
        "ok": True,
        **manifest,
        "status": manifest.get("reason") if not manifest["submitted"] else "submitted",
        "message": f"Index {mode} rebuild for {topic} {status_text} ({manifest['file_count']} files). Poll rebuild-status for progress.",
    }


@router.post("/knowledge/rebuild-all")
async def rebuild_all_topics(user_id: str = Depends(get_current_user)):
    """Submit all topics' rebuild tasks (always full: wipe + re-embed everything,
    matching the 「全量重建」 button). Returns the list of submitted task_ids."""
    topics = load_topics(user_id)
    if not topics:
        raise HTTPException(400, "No topics configured")

    submitted = [
        await _submit_rebuild(key, info, user_id, force=True)
        for key, info in topics.items()
    ]
    queue_full = [t for t in submitted if not t["submitted"] and t.get("reason") == "queue_full"]
    if queue_full:
        raise HTTPException(503, {
            "message": "Embedding rebuild queue is full; affected indexes were not invalidated.",
            "total": len(submitted),
            "tasks": submitted,
        })
    submitted_count = sum(1 for t in submitted if t["submitted"])
    deduplicated_count = len(submitted) - submitted_count
    return {
        "ok": True,
        "total": len(submitted),
        "submitted_count": submitted_count,
        "deduplicated_count": deduplicated_count,
        "tasks": submitted,
        "message": f"Submitted {submitted_count} rebuild tasks; {deduplicated_count} were already queued. Poll rebuild-status for progress.",
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
    hf_path = settings.user_high_freq_path(user_id) / f"{topic}.md"

    def _compute() -> dict:
        # This endpoint is POLLED by the UI and reads every file's full text to
        # count auto-deposit markers. Run the whole scan in a worker thread so a
        # topic with many 30万字 files never blocks the event loop on each poll.
        last_evolved_at = 0
        evolution_count = 0
        last_evolved_file = ""
        last_any_update_at = 0
        file_count = 0
        total_chars = 0

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
                total_chars += len(content)
                hits = marker_re.findall(content)
                if hits:
                    evolution_count += len(hits)
                    if mtime > last_evolved_at:
                        last_evolved_at = mtime
                        last_evolved_file = f.name

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
            "total_chars": total_chars,
            "chunk_count": topic_chunk_count(topic, user_id),
            "last_any_update_at": last_any_update_at,
            "last_evolved_at": last_evolved_at,
            "last_evolved_file": last_evolved_file,
            "evolution_count": evolution_count,
            "last_high_freq_at": last_high_freq_at,
            "high_freq_size": high_freq_size,
        }

    return await asyncio.to_thread(_compute)


@router.get("/knowledge/chunk-counts")
async def get_chunk_counts(user_id: str = Depends(get_current_user)):
    """每个 topic 的已索引 chunk 数 + 全库总数。Qdrant 后端每个 topic O(1)，很快。"""
    topics = load_topics(user_id)

    def _compute() -> dict:
        counts = {key: topic_chunk_count(key, user_id) for key in topics}
        return {"counts": counts, "total": sum(counts.values())}

    return await asyncio.to_thread(_compute)
