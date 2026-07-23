"""Resume upload routes."""
import asyncio
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends

from backend.config import settings
from backend.indexer import invalidate_resume_index, resume_index_mutation_lock
from backend.auth import get_current_user

router = APIRouter(prefix="/api")
_resume_replace_lock = asyncio.Lock()

MAX_RESUME_BYTES = 30 * 1024 * 1024  # 30 MB
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


_PDF_HEADER_BYTES = 1024
logger = logging.getLogger("uvicorn")


def _promote_resume(
    resume_dir: Path, temp_path: Path, dest: Path, user_id: str,
) -> None:
    backups: list[tuple[Path, Path]] = []
    promoted = False
    with resume_index_mutation_lock(user_id):
        try:
            for old in sorted(resume_dir.iterdir()):
                if old.is_file() and old.suffix.lower() == ".pdf":
                    backup = resume_dir / f".resume-backup-{uuid.uuid4().hex}"
                    os.replace(old, backup)
                    backups.append((old, backup))

            os.replace(temp_path, dest)
            promoted = True
            invalidate_resume_index(user_id, strict=True)
        except Exception:
            # A backup move itself can fail before ``dest`` has been touched.
            # Only remove it when this call actually promoted the new upload;
            # otherwise an existing same-name resume would be destroyed while
            # attempting to roll back.
            if promoted:
                dest.unlink(missing_ok=True)
            for old, backup in reversed(backups):
                if backup.exists():
                    os.replace(backup, old)
            raise
        else:
            for _, backup in backups:
                try:
                    backup.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "Unable to remove resume backup %s: %s", backup, exc,
                    )


async def _run_transaction_to_completion(func, *args):
    """Keep a filesystem transaction alive until its worker thread exits."""
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


@router.get("/resume/status")
def resume_status(user_id: str = Depends(get_current_user)):
    resume_dir = settings.user_resume_path(user_id)
    if not resume_dir.exists():
        return {"has_resume": False}
    files = [f for f in resume_dir.iterdir() if f.suffix.lower() == ".pdf"]
    if not files:
        return {"has_resume": False}
    f = files[0]
    return {"has_resume": True, "filename": f.name, "size": f.stat().st_size}


@router.post("/resume/upload")
async def upload_resume(file: UploadFile = File(...), user_id: str = Depends(get_current_user)):
    filename = Path(file.filename or "").name
    if (
        not filename
        or len(filename) > 255
        or filename.rstrip(" .") != filename
        or any(ord(char) < 32 for char in filename)
        or Path(filename).stem.upper() in _WINDOWS_RESERVED_NAMES
        or not filename.lower().endswith(".pdf")
    ):
        raise HTTPException(400, "Only PDF files are supported.")

    resume_dir = settings.user_resume_path(user_id)
    resume_dir.mkdir(parents=True, exist_ok=True)
    dest = resume_dir / filename
    temp_path = resume_dir / f".{uuid.uuid4().hex}.upload"

    # Write the complete upload to a sibling first. The current resume remains
    # usable if the client disconnects, the size limit trips, or I/O fails.
    total = 0
    header = bytearray()
    try:
        with temp_path.open("xb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESUME_BYTES:
                    raise HTTPException(
                        413,
                        f"PDF exceeds {MAX_RESUME_BYTES // (1024 * 1024)}MB",
                    )
                out.write(chunk)

                if len(header) < _PDF_HEADER_BYTES:
                    header.extend(chunk[:_PDF_HEADER_BYTES - len(header)])

        if total == 0 or b"%PDF-" not in header:
            raise HTTPException(400, "The uploaded file is not a valid PDF.")

        async with _resume_replace_lock:
            # Hide every current PDF before promotion. This guarantees a
            # successful replacement leaves exactly one source file, while a
            # failed invalidation can restore the complete previous state.
            await _run_transaction_to_completion(
                _promote_resume, resume_dir, temp_path, dest, user_id,
            )
    finally:
        temp_path.unlink(missing_ok=True)

    return {"ok": True, "filename": dest.name, "size": total}
