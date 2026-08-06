import pytest

import backend.storage.database as database
from backend import qa_arena
from backend.storage import qa_sessions


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "qa-safety.db"
    database._local.conn = None
    database.init_all_tables()
    try:
        yield
    finally:
        temp_conn = getattr(database._local, "conn", None)
        if temp_conn is not None:
            temp_conn.close()
        database.DB_PATH = original_path
        database._local.conn = original_conn


@pytest.mark.parametrize("session_id", ["../..", "..\\.."])
def test_delete_session_images_refuses_path_traversal(monkeypatch, tmp_path, session_id):
    monkeypatch.setattr(qa_arena.settings, "base_dir", tmp_path)
    sentinel = tmp_path / "data" / "users" / "sentinel" / "keep.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep", encoding="utf-8")

    qa_arena.delete_session_images(session_id, "a" * 32)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_clear_messages_fences_late_stream_write(isolated_db):
    session_id = qa_sessions.create_session("user-1", "race")["id"]
    version = qa_sessions.get_message_version(session_id, "user-1")
    assert version == 0
    assert qa_sessions.save_message(
        session_id,
        "user-1",
        "user",
        "question",
        expected_message_version=version,
    )

    assert qa_sessions.clear_messages(session_id, "user-1") is True
    assert qa_sessions.save_message(
        session_id,
        "user-1",
        "assistant",
        "late answer",
        expected_message_version=version,
    ) is False
    assert qa_sessions.load_messages(session_id, "user-1", limit=None) == []


def test_message_insert_requires_existing_parent_session(isolated_db):
    assert qa_sessions.save_message(
        "missing", "user-1", "assistant", "orphan"
    ) is False
    count = database.get_db().execute(
        "SELECT COUNT(*) FROM qa_messages"
    ).fetchone()[0]
    assert count == 0


def test_summary_lookup_never_falls_back_to_another_session(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_arena.settings, "base_dir", tmp_path)
    notes = tmp_path / "data" / "qa_notes" / "user-1"
    notes.mkdir(parents=True)
    (notes / "2026-01-01-python-session-one.md").write_text(
        "session one", encoding="utf-8"
    )

    assert qa_arena.get_summary_file("session-two", "user-1") is None
    assert qa_arena.get_summary_file("session-one", "user-1") == (
        "session one",
        "2026-01-01-python-session-one.md",
    )


def test_oversized_base64_is_rejected_before_decode(monkeypatch):
    monkeypatch.setattr(qa_arena, "MAX_IMAGE_BASE64_CHARS", 4)

    def unexpected_decode(*_args, **_kwargs):
        raise AssertionError("oversized payload must not be decoded")

    monkeypatch.setattr(qa_arena.base64, "b64decode", unexpected_decode)

    assert qa_arena.save_uploaded_images(
        "session", "user", ["data:image/png;base64,AAAAA"]
    ) == []
