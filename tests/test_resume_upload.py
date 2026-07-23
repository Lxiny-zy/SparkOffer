"""Resume replacement must not destroy the current file on failed uploads."""

import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from backend.routers import resume


VALID_PDF = b"%PDF-1.7\nvalid"


def _patch_resume_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(
        type(resume.settings),
        "user_resume_path",
        lambda _settings, _user_id: tmp_path,
    )
    monkeypatch.setattr(
        resume,
        "invalidate_resume_index",
        lambda _user_id, **_kwargs: None,
    )
    monkeypatch.setattr(resume, "_resume_replace_lock", asyncio.Lock())


def test_oversized_upload_preserves_existing_resume(monkeypatch, tmp_path):
    _patch_resume_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(resume, "MAX_RESUME_BYTES", 4)
    old = tmp_path / "old.pdf"
    old.write_bytes(b"old")
    upload = UploadFile(filename="new.pdf", file=BytesIO(VALID_PDF))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(resume.upload_resume(upload, user_id="12345678"))

    assert exc_info.value.status_code == 413
    assert old.read_bytes() == b"old"
    assert list(tmp_path.glob("*.upload")) == []


def test_complete_upload_atomically_replaces_old_resume(monkeypatch, tmp_path):
    _patch_resume_dir(monkeypatch, tmp_path)
    old = tmp_path / "old.pdf"
    old.write_bytes(b"old")
    upload = UploadFile(filename="new.pdf", file=BytesIO(VALID_PDF))

    result = asyncio.run(resume.upload_resume(upload, user_id="12345678"))

    assert result == {"ok": True, "filename": "new.pdf", "size": len(VALID_PDF)}
    assert not old.exists()
    assert (tmp_path / "new.pdf").read_bytes() == VALID_PDF


def test_complete_upload_replaces_all_old_resumes(monkeypatch, tmp_path):
    _patch_resume_dir(monkeypatch, tmp_path)
    (tmp_path / "first.pdf").write_bytes(b"first")
    (tmp_path / "second.PDF").write_bytes(b"second")

    result = asyncio.run(resume.upload_resume(
        UploadFile(filename="new.pdf", file=BytesIO(VALID_PDF)),
        user_id="12345678",
    ))

    assert result["filename"] == "new.pdf"
    assert [path.name for path in tmp_path.iterdir()] == ["new.pdf"]


@pytest.mark.parametrize("payload", [b"", b"not a pdf"])
def test_invalid_pdf_preserves_existing_resume(monkeypatch, tmp_path, payload):
    _patch_resume_dir(monkeypatch, tmp_path)
    old = tmp_path / "old.pdf"
    old.write_bytes(b"old")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(resume.upload_resume(
            UploadFile(filename="new.pdf", file=BytesIO(payload)),
            user_id="12345678",
        ))

    assert exc_info.value.status_code == 400
    assert old.read_bytes() == b"old"
    assert not (tmp_path / "new.pdf").exists()


def test_invalidation_failure_rolls_back_same_filename(monkeypatch, tmp_path):
    _patch_resume_dir(monkeypatch, tmp_path)
    old = tmp_path / "resume.pdf"
    old.write_bytes(b"%PDF-1.4\nold")

    def fail_invalidation(*args, **kwargs):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(resume, "invalidate_resume_index", fail_invalidation)
    with pytest.raises(RuntimeError, match="cache unavailable"):
        asyncio.run(resume.upload_resume(
            UploadFile(filename="resume.pdf", file=BytesIO(VALID_PDF)),
            user_id="12345678",
        ))

    assert old.read_bytes() == b"%PDF-1.4\nold"
    assert list(tmp_path.glob(".resume-backup-*")) == []


def test_invalidation_failure_restores_all_old_resumes(monkeypatch, tmp_path):
    _patch_resume_dir(monkeypatch, tmp_path)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    monkeypatch.setattr(
        resume,
        "invalidate_resume_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("cache unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="cache unavailable"):
        asyncio.run(resume.upload_resume(
            UploadFile(filename="new.pdf", file=BytesIO(VALID_PDF)),
            user_id="12345678",
        ))

    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert not (tmp_path / "new.pdf").exists()
    assert list(tmp_path.glob(".resume-backup-*")) == []


def test_backup_failure_does_not_delete_unmoved_same_name_resume(monkeypatch, tmp_path):
    _patch_resume_dir(monkeypatch, tmp_path)
    first = tmp_path / "a.pdf"
    destination = tmp_path / "resume.pdf"
    first.write_bytes(b"first")
    destination.write_bytes(b"original destination")
    real_replace = resume.os.replace

    def fail_while_backing_up_destination(source, target):
        if source == destination:
            raise OSError("backup failed")
        return real_replace(source, target)

    monkeypatch.setattr(resume.os, "replace", fail_while_backing_up_destination)

    with pytest.raises(OSError, match="backup failed"):
        asyncio.run(resume.upload_resume(
            UploadFile(filename="resume.pdf", file=BytesIO(VALID_PDF)),
            user_id="12345678",
        ))

    assert first.read_bytes() == b"first"
    assert destination.read_bytes() == b"original destination"
    assert list(tmp_path.glob(".resume-backup-*")) == []
    assert list(tmp_path.glob("*.upload")) == []


@pytest.mark.parametrize("filename", ["CON.pdf", f"{'a' * 252}.pdf"])
def test_invalid_platform_filename_is_rejected_before_replacement(
    monkeypatch, tmp_path, filename,
):
    _patch_resume_dir(monkeypatch, tmp_path)
    old = tmp_path / "old.pdf"
    old.write_bytes(b"old")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(resume.upload_resume(
            UploadFile(filename=filename, file=BytesIO(VALID_PDF)),
            user_id="12345678",
        ))

    assert exc_info.value.status_code == 400
    assert old.read_bytes() == b"old"
