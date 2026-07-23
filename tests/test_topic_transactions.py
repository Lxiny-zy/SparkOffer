from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from backend.routers import profile


def _patch_knowledge_root(monkeypatch, tmp_path):
    monkeypatch.setattr(
        type(profile.settings),
        "user_knowledge_path",
        lambda _settings, _user_id: tmp_path,
    )


def test_create_topic_rolls_back_new_directory_when_mapping_save_fails(
    monkeypatch, tmp_path,
):
    _patch_knowledge_root(monkeypatch, tmp_path)

    @contextmanager
    def failing_transaction(_user_id):
        yield {}
        raise OSError("topics write failed")

    monkeypatch.setattr(profile, "topics_transaction", failing_transaction)

    with pytest.raises(OSError, match="topics write failed"):
        profile.create_topic(
            {"key": "python", "name": "Python", "icon": ""},
            user_id="user-1",
        )

    assert not (tmp_path / "python").exists()


def test_delete_topic_restores_source_when_index_invalidation_fails(
    monkeypatch, tmp_path,
):
    _patch_knowledge_root(monkeypatch, tmp_path)
    topic_dir = tmp_path / "python"
    topic_dir.mkdir()
    (topic_dir / "README.md").write_text("# Python\n", encoding="utf-8")
    topics = {"python": {"name": "Python", "icon": "", "dir": "python"}}

    @contextmanager
    def transaction(_user_id):
        yield topics

    monkeypatch.setattr(profile, "topics_transaction", transaction)
    monkeypatch.setattr(
        profile,
        "invalidate_topic_index",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("index cleanup failed")
        ),
    )

    with pytest.raises(OSError, match="index cleanup failed"):
        profile.delete_topic("python", user_id="user-1")

    assert topics["python"]["dir"] == "python"
    assert (topic_dir / "README.md").read_text("utf-8") == "# Python\n"
    assert list(tmp_path.glob(".topic-delete-*")) == []


def test_create_topic_rejects_non_string_fields():
    with pytest.raises(HTTPException) as exc_info:
        profile.create_topic({"name": None}, user_id="user-1")

    assert exc_info.value.status_code == 422
