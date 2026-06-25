"""Resume upload routes."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends

from backend.config import settings
from backend.indexer import _index_cache
from backend.auth import get_current_user

router = APIRouter(prefix="/api")

MAX_RESUME_BYTES = 30 * 1024 * 1024   # 30MB — a PDF résumé is well under this


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
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported.")

    resume_dir = settings.user_resume_path(user_id)
    resume_dir.mkdir(parents=True, exist_ok=True)

    for old in resume_dir.iterdir():
        if old.is_file():
            old.unlink()

    dest = resume_dir / Path(file.filename).name
    # Stream to disk with a size cap instead of file.read() (which would load the
    # whole upload into memory before any check). Mirrors the knowledge-upload guard.
    total = 0
    too_large = False
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESUME_BYTES:
                too_large = True
                break
            out.write(chunk)
    if too_large:
        dest.unlink(missing_ok=True)
        raise HTTPException(413, f"PDF 过大（>{MAX_RESUME_BYTES // (1024*1024)}MB）")

    _index_cache.pop((user_id, "resume"), None)
    cache_dir = settings.user_index_cache_path(user_id) / "resume"
    if cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir)

    return {"ok": True, "filename": dest.name, "size": total}

