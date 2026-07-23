from __future__ import annotations

import asyncio
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient

from backend.auth import get_current_user
from backend.knowledge_evolution import _append_sync
from backend.routers import knowledge


def _patch_topic(monkeypatch, tmp_path):
    monkeypatch.setattr(
        type(knowledge.settings),
        "user_knowledge_path",
        lambda _settings, _user_id: tmp_path,
    )
    monkeypatch.setattr(
        knowledge,
        "load_topics",
        lambda _user_id: {"python": {"dir": "python", "name": "Python"}},
    )
    monkeypatch.setattr(knowledge, "evict_topic_cache", lambda *args: None)
    monkeypatch.setattr(knowledge, "schedule_index_rebuild", lambda *args: None)
    monkeypatch.setattr(knowledge, "_upload_promote_lock", asyncio.Lock())


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(knowledge.router)
    app.dependency_overrides[get_current_user] = lambda: "user-1"
    return TestClient(app)


def test_invalid_utf8_upload_is_rejected_without_partial_file(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    upload = UploadFile(filename="broken.md", file=BytesIO(b"valid\n\xffbroken"))

    result = asyncio.run(knowledge.upload_core_knowledge(
        "python", [upload], user_id="user-1",
    ))

    assert result == {
        "ok": True,
        "saved": [],
        "skipped": [],
        "rejected": ["broken.md"],
    }
    topic_dir = tmp_path / "python"
    assert list(topic_dir.iterdir()) == []


def test_valid_utf8_upload_is_promoted_after_validation(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    upload = UploadFile(filename="notes.md", file=BytesIO("你好".encode("utf-8")))

    result = asyncio.run(knowledge.upload_core_knowledge(
        "python", [upload], user_id="user-1",
    ))

    assert result["saved"] == ["notes.md"]
    assert (tmp_path / "python" / "notes.md").read_text("utf-8") == "你好"


def test_non_string_document_content_returns_422(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir(parents=True)
    (topic_dir / "notes.md").write_text("old", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(knowledge.update_core_knowledge(
            "python", "notes.md", {"content": None}, user_id="user-1",
        ))

    assert exc_info.value.status_code == 422
    assert (topic_dir / "notes.md").read_text("utf-8") == "old"


def test_batch_promotion_failure_rolls_back_every_file(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    real_replace = knowledge.os.replace
    promotions = 0

    def fail_second_promotion(source, target):
        nonlocal promotions
        promotions += 1
        if promotions == 2:
            raise OSError("second promotion failed")
        return real_replace(source, target)

    monkeypatch.setattr(knowledge.os, "replace", fail_second_promotion)
    uploads = [
        UploadFile(filename="first.md", file=BytesIO(b"first")),
        UploadFile(filename="second.md", file=BytesIO(b"second")),
    ]

    with pytest.raises(OSError, match="second promotion failed"):
        asyncio.run(knowledge.upload_core_knowledge(
            "python", uploads, user_id="user-1",
        ))

    topic_dir = tmp_path / "python"
    assert list(topic_dir.glob("*.md")) == []
    assert list(topic_dir.glob("*.upload")) == []


def test_rebuild_submission_failure_rolls_back_promoted_batch(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    monkeypatch.setattr(
        knowledge,
        "schedule_index_rebuild",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("queue unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        asyncio.run(knowledge.upload_core_knowledge(
            "python",
            [UploadFile(filename="notes.md", file=BytesIO(b"notes"))],
            user_id="user-1",
        ))

    topic_dir = tmp_path / "python"
    assert list(topic_dir.glob("*.md")) == []
    assert list(topic_dir.glob("*.upload")) == []


def test_batch_size_limit_leaves_no_partial_files(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    monkeypatch.setattr(knowledge, "MAX_UPLOAD_BATCH_BYTES", 5)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir()
    existing = topic_dir / "existing.md"
    existing.write_bytes(b"keep")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(knowledge.upload_core_knowledge(
            "python",
            [
                UploadFile(filename="first.md", file=BytesIO(b"1234")),
                UploadFile(filename="second.md", file=BytesIO(b"5678")),
            ],
            user_id="user-1",
        ))

    assert exc_info.value.status_code == 413
    assert existing.read_bytes() == b"keep"
    assert [path.name for path in topic_dir.glob("*.md")] == ["existing.md"]
    assert list(topic_dir.glob("*.upload")) == []


def test_core_gets_return_content_version(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir(parents=True)
    filepath = topic_dir / "notes.md"
    filepath.write_text("versioned content", encoding="utf-8")
    expected = hashlib.sha256(b"versioned content").hexdigest()

    listing = asyncio.run(knowledge.get_core_knowledge("python", user_id="user-1"))
    detail = asyncio.run(
        knowledge.get_core_file("python", "notes.md", user_id="user-1")
    )

    assert len(listing) == 1
    assert listing[0]["filename"] == "notes.md"
    assert listing[0]["content"] == "versioned content"
    assert listing[0]["mtime"] > 0
    assert listing[0]["size"] == len(b"versioned content")
    assert listing[0]["content_loaded"] is True
    assert listing[0]["version"] == expected
    assert detail["version"] == expected
    assert detail["content"] == "versioned content"
    assert detail["content_loaded"] is True


def test_core_list_streams_large_file_version_matching_detail(
    monkeypatch, tmp_path,
):
    _patch_topic(monkeypatch, tmp_path)
    monkeypatch.setattr(knowledge, "MAX_CORE_INLINE_BYTES", 4)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir(parents=True)
    filepath = topic_dir / "large.md"
    filepath.write_bytes(b"line one\r\nline two\r\n")
    normalized = "line one\nline two\n"

    listing = asyncio.run(knowledge.get_core_knowledge("python", user_id="user-1"))
    detail = asyncio.run(
        knowledge.get_core_file("python", "large.md", user_id="user-1")
    )

    assert listing[0]["content"] == ""
    assert listing[0]["content_loaded"] is False
    assert listing[0]["version"] == hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()
    assert listing[0]["version"] == detail["version"]
    assert detail["content"] == normalized

    deleted = asyncio.run(knowledge.delete_core_knowledge(
        "python",
        "large.md",
        user_id="user-1",
        expected_version=listing[0]["version"],
    ))
    assert deleted == {"ok": True}
    assert not filepath.exists()


def test_core_list_returns_version_after_inline_budget_is_exhausted(
    monkeypatch, tmp_path,
):
    _patch_topic(monkeypatch, tmp_path)
    monkeypatch.setattr(knowledge, "MAX_CORE_INLINE_BYTES", 1024)
    monkeypatch.setattr(knowledge, "MAX_CORE_RESPONSE_BYTES", 5)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir(parents=True)
    (topic_dir / "a.md").write_text("12345", encoding="utf-8")
    (topic_dir / "b.md").write_text("second", encoding="utf-8")

    listing = asyncio.run(knowledge.get_core_knowledge("python", user_id="user-1"))

    assert listing[0]["content"] == "12345"
    assert listing[0]["content_loaded"] is True
    assert listing[1]["content"] == ""
    assert listing[1]["content_loaded"] is False
    assert listing[1]["version"] == hashlib.sha256(b"second").hexdigest()


def test_high_freq_get_returns_version_for_missing_and_existing_file(
    monkeypatch, tmp_path,
):
    _patch_topic(monkeypatch, tmp_path)
    monkeypatch.setattr(
        type(knowledge.settings),
        "user_high_freq_path",
        lambda _settings, _user_id: tmp_path / "high_freq",
    )
    empty_version = hashlib.sha256(b"").hexdigest()

    missing = asyncio.run(knowledge.get_high_freq("python", user_id="user-1"))
    assert missing == {
        "content": "",
        "version": empty_version,
        "mtime": 0,
        "size": 0,
    }

    filepath = tmp_path / "high_freq" / "python.md"
    filepath.write_text("review me", encoding="utf-8")
    existing = asyncio.run(knowledge.get_high_freq("python", user_id="user-1"))
    assert existing["content"] == "review me"
    assert existing["version"] == hashlib.sha256(b"review me").hexdigest()
    assert existing["size"] == len(b"review me")


def test_core_put_rejects_stale_expected_version(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir(parents=True)
    filepath = topic_dir / "notes.md"
    filepath.write_text("first", encoding="utf-8")
    first_version = hashlib.sha256(b"first").hexdigest()

    saved = asyncio.run(knowledge.update_core_knowledge(
        "python",
        "notes.md",
        {"content": "second", "expected_version": first_version},
        user_id="user-1",
    ))

    assert saved["version"] == hashlib.sha256(b"second").hexdigest()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(knowledge.update_core_knowledge(
            "python",
            "notes.md",
            {"content": "stale overwrite", "expected_version": first_version},
            user_id="user-1",
        ))

    assert exc_info.value.status_code == 409
    assert filepath.read_text(encoding="utf-8") == "second"


def test_high_freq_put_rejects_stale_expected_version(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    high_freq_dir = tmp_path / "high_freq"
    monkeypatch.setattr(
        type(knowledge.settings),
        "user_high_freq_path",
        lambda _settings, _user_id: high_freq_dir,
    )
    empty_version = hashlib.sha256(b"").hexdigest()

    saved = asyncio.run(knowledge.update_high_freq(
        "python",
        {"content": "first", "expected_version": empty_version},
        user_id="user-1",
    ))

    assert saved["version"] == hashlib.sha256(b"first").hexdigest()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(knowledge.update_high_freq(
            "python",
            {"content": "stale overwrite", "expected_version": empty_version},
            user_id="user-1",
        ))

    assert exc_info.value.status_code == 409
    assert (high_freq_dir / "python.md").read_text(encoding="utf-8") == "first"


def test_core_delete_honors_if_match_header(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir(parents=True)
    filepath = topic_dir / "notes.md"
    filepath.write_text("current", encoding="utf-8")
    current_version = hashlib.sha256(b"current").hexdigest()
    stale_version = hashlib.sha256(b"stale").hexdigest()

    with _api_client() as client:
        conflict = client.delete(
            "/api/knowledge/python/core/notes.md",
            headers={"If-Match": stale_version},
        )
        deleted = client.delete(
            "/api/knowledge/python/core/notes.md",
            headers={"If-Match": current_version},
        )

    assert conflict.status_code == 409
    assert deleted.status_code == 200
    assert not filepath.exists()


def test_auto_append_wins_race_against_stale_manual_put(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir(parents=True)
    filepath = topic_dir / "自动沉淀.md"
    filepath.write_text("original", encoding="utf-8")
    original_version = hashlib.sha256(b"original").hexdigest()
    append_has_lock = threading.Event()
    allow_append = threading.Event()
    manual_started = threading.Event()

    from backend import knowledge_evolution

    real_atomic_write = knowledge_evolution.atomic_write_text

    def pause_auto_append(path, content, *, encoding="utf-8"):
        append_has_lock.set()
        assert allow_append.wait(timeout=5)
        real_atomic_write(path, content, encoding=encoding)

    monkeypatch.setattr(
        knowledge_evolution, "atomic_write_text", pause_auto_append,
    )

    def stale_manual_put():
        manual_started.set()
        try:
            asyncio.run(knowledge.update_core_knowledge(
                "python",
                "自动沉淀.md",
                {
                    "content": "manual overwrite",
                    "expected_version": original_version,
                },
                user_id="user-1",
            ))
        except HTTPException as exc:
            return exc.status_code
        return 200

    with ThreadPoolExecutor(max_workers=2) as executor:
        append_future = executor.submit(_append_sync, filepath, "\nauto append")
        assert append_has_lock.wait(timeout=5)
        put_future = executor.submit(stale_manual_put)
        assert manual_started.wait(timeout=5)
        allow_append.set()

        assert append_future.result(timeout=5) is True
        assert put_future.result(timeout=5) == 409

    assert filepath.read_text(encoding="utf-8") == "original\nauto append"


def test_auto_append_preserves_concurrent_manual_put(monkeypatch, tmp_path):
    _patch_topic(monkeypatch, tmp_path)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir(parents=True)
    filepath = topic_dir / "自动沉淀.md"
    filepath.write_text("original", encoding="utf-8")
    original_version = hashlib.sha256(b"original").hexdigest()
    manual_has_lock = threading.Event()
    allow_manual = threading.Event()

    real_atomic_write = knowledge.atomic_write_text

    def pause_manual_put(path, content, *, encoding="utf-8"):
        manual_has_lock.set()
        assert allow_manual.wait(timeout=5)
        real_atomic_write(path, content, encoding=encoding)

    monkeypatch.setattr(knowledge, "atomic_write_text", pause_manual_put)

    def manual_put():
        return asyncio.run(knowledge.update_core_knowledge(
            "python",
            "自动沉淀.md",
            {
                "content": "manual update",
                "expected_version": original_version,
            },
            user_id="user-1",
        ))

    with ThreadPoolExecutor(max_workers=2) as executor:
        put_future = executor.submit(manual_put)
        assert manual_has_lock.wait(timeout=5)
        append_future = executor.submit(_append_sync, filepath, "\nauto append")
        allow_manual.set()

        assert put_future.result(timeout=5)["ok"] is True
        assert append_future.result(timeout=5) is True

    assert filepath.read_text(encoding="utf-8") == "manual update\nauto append"
