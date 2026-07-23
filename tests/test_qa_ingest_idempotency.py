from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import backend.knowledge_evolution as knowledge_evolution
import backend.storage.database as database
from backend.routers import qa_arena
from backend.storage import qa_sessions


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "qa-ingest.db"
    database._local.conn = None
    try:
        database.init_all_tables()
        yield
    finally:
        temp_conn = getattr(database._local, "conn", None)
        if temp_conn is not None:
            temp_conn.close()
        database.DB_PATH = original_path
        database._local.conn = original_conn


def _create_session(user_id: str = "user-1") -> str:
    return qa_sessions.create_session(user_id, "idempotency test")["id"]


def test_successful_ingest_is_replayed_without_second_mutation(
    isolated_db, monkeypatch,
):
    session_id = _create_session()
    calls = 0

    async def fake_ingest(content, user_id, **_kwargs):
        nonlocal calls
        calls += 1
        return {"ok": True, "topic": "Python", "reason": "stored"}

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )
    body = qa_arena.QAKnowledgeIngestRequest(content="x" * 40)

    async def run():
        first = await qa_arena.ingest_knowledge(
            session_id, body, "qa-ingest:key-1", "user-1",
        )
        second = await qa_arena.ingest_knowledge(
            session_id, body, "qa-ingest:key-1", "user-1",
        )
        return first, second

    first, second = asyncio.run(run())
    assert first == second
    assert calls == 1


def test_legacy_request_without_header_uses_content_idempotency(
    isolated_db, monkeypatch,
):
    session_id = _create_session()
    calls = 0

    async def fake_ingest(content, user_id, **_kwargs):
        nonlocal calls
        calls += 1
        return {"ok": True, "topic": "Python", "reason": "stored"}

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )
    body = qa_arena.QAKnowledgeIngestRequest(content="legacy card " * 4)

    async def run():
        first = await qa_arena.ingest_knowledge(
            session_id, body, None, "user-1",
        )
        second = await qa_arena.ingest_knowledge(
            session_id, body, None, "user-1",
        )
        return first, second

    first, second = asyncio.run(run())
    assert first == second
    assert calls == 1


def test_idempotency_key_cannot_be_reused_for_different_content(
    isolated_db, monkeypatch,
):
    session_id = _create_session()

    async def fake_ingest(content, user_id, **_kwargs):
        return {"ok": True, "topic": "Python", "reason": "stored"}

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )

    async def run():
        await qa_arena.ingest_knowledge(
            session_id,
            qa_arena.QAKnowledgeIngestRequest(content="a" * 40),
            "qa-ingest:reused",
            "user-1",
        )
        with pytest.raises(HTTPException) as exc_info:
            await qa_arena.ingest_knowledge(
                session_id,
                qa_arena.QAKnowledgeIngestRequest(content="b" * 40),
                "qa-ingest:reused",
                "user-1",
            )
        return exc_info.value

    error = asyncio.run(run())
    assert error.status_code == 409


def test_unsuccessful_ingest_releases_claim_for_retry(isolated_db, monkeypatch):
    session_id = _create_session()
    calls = 0

    async def fake_ingest(content, user_id, **_kwargs):
        nonlocal calls
        calls += 1
        return {"ok": False, "topic": None, "reason": "retry"}

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )
    body = qa_arena.QAKnowledgeIngestRequest(content="x" * 40)

    async def run():
        await qa_arena.ingest_knowledge(
            session_id, body, "qa-ingest:retry", "user-1",
        )
        await qa_arena.ingest_knowledge(
            session_id, body, "qa-ingest:retry", "user-1",
        )

    asyncio.run(run())
    assert calls == 2


def test_complete_error_expires_saved_plan_for_immediate_retry(
    isolated_db, monkeypatch,
):
    session_id = _create_session()
    calls = 0

    async def fake_ingest(_content, user_id, **kwargs):
        nonlocal calls
        calls += 1
        assert qa_sessions.save_ingest_plan(
            user_id,
            kwargs["idempotency_marker"],
            kwargs["claim_token"],
            "python",
            "Stable normalized knowledge block with enough content.",
        )
        return {"ok": True, "topic": "Python", "reason": "stored"}

    original_complete = qa_sessions.complete_ingest_request
    complete_calls = 0

    def flaky_complete(*args, **kwargs):
        nonlocal complete_calls
        complete_calls += 1
        if complete_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )
    monkeypatch.setattr(qa_sessions, "complete_ingest_request", flaky_complete)
    body = qa_arena.QAKnowledgeIngestRequest(content="x" * 40)

    async def run():
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await qa_arena.ingest_knowledge(
                session_id, body, "qa-ingest:complete-error", "user-1",
            )

        row = database.get_db().execute(
            "SELECT topic_key, updated_at FROM qa_ingest_requests "
            "WHERE idempotency_key = ?",
            ("qa-ingest:complete-error",),
        ).fetchone()
        assert row is not None
        assert row["topic_key"] == "python"
        assert row["updated_at"] == "1970-01-01T00:00:00+00:00"

        return await qa_arena.ingest_knowledge(
            session_id, body, "qa-ingest:complete-error", "user-1",
        )

    replay = asyncio.run(run())
    assert replay == {"ok": True, "topic": "Python", "reason": "stored"}
    assert calls == complete_calls == 2


def test_release_error_removes_claim_for_immediate_retry(
    isolated_db, monkeypatch,
):
    session_id = _create_session()
    calls = 0

    async def fake_ingest(_content, _user_id, **_kwargs):
        nonlocal calls
        calls += 1
        return {"ok": False, "topic": None, "reason": "retry"}

    original_release = qa_sessions.release_ingest_request
    release_calls = 0

    def flaky_release(*args, **kwargs):
        nonlocal release_calls
        release_calls += 1
        if release_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_release(*args, **kwargs)

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )
    monkeypatch.setattr(qa_sessions, "release_ingest_request", flaky_release)
    body = qa_arena.QAKnowledgeIngestRequest(content="x" * 40)

    async def run():
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await qa_arena.ingest_knowledge(
                session_id, body, "qa-ingest:release-error", "user-1",
            )
        row = database.get_db().execute(
            "SELECT 1 FROM qa_ingest_requests WHERE idempotency_key = ?",
            ("qa-ingest:release-error",),
        ).fetchone()
        assert row is None
        return await qa_arena.ingest_knowledge(
            session_id, body, "qa-ingest:release-error", "user-1",
        )

    replay = asyncio.run(run())
    assert replay == {"ok": False, "topic": None, "reason": "retry"}
    assert calls == release_calls == 2


def test_lost_complete_lease_does_not_abandon_new_owner(
    isolated_db, monkeypatch,
):
    session_id = _create_session()
    new_token = None
    abandon_calls = 0

    async def fake_ingest(_content, _user_id, **_kwargs):
        return {"ok": True, "topic": "Python", "reason": "stored"}

    def reclaim_before_complete(
        claimed_session_id,
        claimed_user_id,
        claimed_key,
        claimed_hash,
        _claimed_token,
        _response,
    ):
        nonlocal new_token
        state, _, new_token = qa_sessions.claim_ingest_request(
            claimed_session_id,
            claimed_user_id,
            claimed_key,
            claimed_hash,
            "f" * 64,
            stale_after=timedelta(seconds=-1),
        )
        assert state == "claimed" and new_token
        return False

    original_abandon = qa_sessions.abandon_ingest_request

    def observe_abandon(*args, **kwargs):
        nonlocal abandon_calls
        abandon_calls += 1
        return original_abandon(*args, **kwargs)

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )
    monkeypatch.setattr(
        qa_sessions, "complete_ingest_request", reclaim_before_complete,
    )
    monkeypatch.setattr(qa_sessions, "abandon_ingest_request", observe_abandon)

    async def run():
        with pytest.raises(HTTPException) as exc_info:
            await qa_arena.ingest_knowledge(
                session_id,
                qa_arena.QAKnowledgeIngestRequest(content="x" * 40),
                "qa-ingest:lost-complete-lease",
                "user-1",
            )
        return exc_info.value

    error = asyncio.run(run())
    row = database.get_db().execute(
        "SELECT claim_token, updated_at FROM qa_ingest_requests "
        "WHERE idempotency_key = ?",
        ("qa-ingest:lost-complete-lease",),
    ).fetchone()
    assert error.status_code == 503
    assert abandon_calls == 0
    assert row is not None and row["claim_token"] == new_token
    assert row["updated_at"] != "1970-01-01T00:00:00+00:00"


def test_concurrent_duplicate_reports_pending_without_second_mutation(
    isolated_db, monkeypatch,
):
    session_id = _create_session()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_ingest(content, user_id, **_kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"ok": True, "topic": "Python", "reason": "stored"}

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )
    body = qa_arena.QAKnowledgeIngestRequest(content="x" * 40)

    async def run():
        first = asyncio.create_task(
            qa_arena.ingest_knowledge(
                session_id, body, "qa-ingest:pending", "user-1",
            )
        )
        await entered.wait()
        with pytest.raises(HTTPException) as exc_info:
            await qa_arena.ingest_knowledge(
                session_id, body, "qa-ingest:pending", "user-1",
            )
        release.set()
        await first
        return exc_info.value

    error = asyncio.run(run())
    assert error.status_code == 409
    assert error.headers == {"Retry-After": "2"}
    assert calls == 1


def test_stale_worker_cannot_finish_or_release_reclaimed_lease(isolated_db):
    session_id = _create_session()
    content_hash = "a" * 64
    first_state, _, first_token = qa_sessions.claim_ingest_request(
        session_id, "user-1", "qa-ingest:lease", content_hash, "1" * 64,
    )
    second_state, _, second_token = qa_sessions.claim_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:lease",
        content_hash,
        "1" * 64,
        stale_after=timedelta(seconds=-1),
    )

    assert first_state == second_state == "claimed"
    assert first_token and second_token and first_token != second_token
    assert qa_sessions.renew_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:lease",
        content_hash,
        first_token,
    ) is False
    assert qa_sessions.release_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:lease",
        content_hash,
        first_token,
    ) is False
    assert qa_sessions.complete_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:lease",
        content_hash,
        first_token,
        {"ok": True},
    ) is False
    assert qa_sessions.complete_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:lease",
        content_hash,
        second_token,
        {"ok": True, "topic": "Python", "reason": "stored"},
    ) is True


def test_cancelled_http_waiter_does_not_release_live_ingestion(
    isolated_db, monkeypatch,
):
    session_id = _create_session()
    entered = asyncio.Event()
    release_write = threading.Event()
    calls = 0

    async def fake_ingest(content, user_id, **_kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.to_thread(release_write.wait)
        return {"ok": True, "topic": "Python", "reason": "stored"}

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )
    body = qa_arena.QAKnowledgeIngestRequest(content="x" * 40)

    async def run():
        waiter = asyncio.create_task(
            qa_arena.ingest_knowledge(
                session_id, body, "qa-ingest:cancel", "user-1",
            )
        )
        await entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        row = database.get_db().execute(
            "SELECT status FROM qa_ingest_requests WHERE idempotency_key = ?",
            ("qa-ingest:cancel",),
        ).fetchone()
        assert row["status"] == "pending"

        release_write.set()
        await asyncio.gather(*tuple(qa_arena._qa_ingest_tasks))
        return await qa_arena.ingest_knowledge(
            session_id, body, "qa-ingest:cancel", "user-1",
        )

    replay = asyncio.run(run())
    assert replay == {"ok": True, "topic": "Python", "reason": "stored"}
    assert calls == 1


def test_session_delete_is_blocked_until_ingestion_finishes(
    isolated_db, monkeypatch,
):
    session_id = _create_session()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_ingest(content, user_id, **_kwargs):
        entered.set()
        await release.wait()
        return {"ok": True, "topic": "Python", "reason": "stored"}

    monkeypatch.setattr(
        knowledge_evolution, "ingest_qa_card_to_knowledge", fake_ingest,
    )

    async def run():
        operation = asyncio.create_task(
            qa_arena.ingest_knowledge(
                session_id,
                qa_arena.QAKnowledgeIngestRequest(content="x" * 40),
                "qa-ingest:delete",
                "user-1",
            )
        )
        await entered.wait()
        assert qa_sessions.delete_session_checked(session_id, "user-1") == "busy"
        release.set()
        await operation

    asyncio.run(run())
    assert qa_sessions.delete_session_checked(session_id, "user-1") == "deleted"
    assert qa_sessions.get_session(session_id, "user-1") is None


def test_claim_cannot_be_created_after_session_delete(isolated_db):
    session_id = _create_session()
    assert qa_sessions.delete_session_checked(session_id, "user-1") == "deleted"

    state, cached, token = qa_sessions.claim_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:deleted",
        "a" * 64,
        "2" * 64,
    )

    assert (state, cached, token) == ("missing", None, None)


def test_stale_ingest_lease_does_not_block_session_delete(isolated_db):
    session_id = _create_session()
    state, _, token = qa_sessions.claim_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:stale-delete",
        "a" * 64,
        "3" * 64,
    )
    assert state == "claimed" and token
    database.get_db().execute(
        "UPDATE qa_ingest_requests SET updated_at = ? WHERE idempotency_key = ?",
        ("2000-01-01T00:00:00+00:00", "qa-ingest:stale-delete"),
    )
    database.get_db().commit()

    assert qa_sessions.delete_session_checked(session_id, "user-1") == "deleted"


def test_resumed_stale_worker_is_fenced_after_session_delete(
    isolated_db, tmp_path, monkeypatch,
):
    session_id = _create_session()
    marker = "4" * 64
    state, _, token = qa_sessions.claim_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:stale-worker",
        "a" * 64,
        marker,
    )
    assert state == "claimed" and token
    assert qa_sessions.save_ingest_plan(
        "user-1",
        marker,
        token,
        "python",
        "Stable normalized knowledge block with enough content.",
    )

    monkeypatch.setattr(
        "backend.indexer.load_topics",
        lambda _user_id: {"python": {"name": "Python"}},
    )
    monkeypatch.setattr(
        knowledge_evolution,
        "get_langchain_llm",
        lambda **_kwargs: pytest.fail("persisted plan must skip the LLM"),
    )

    def delete_stale_session(_topic, _user_id):
        conn = database.get_db()
        conn.execute(
            "UPDATE qa_ingest_requests SET updated_at = ? "
            "WHERE idempotency_key = ?",
            ("2000-01-01T00:00:00+00:00", "qa-ingest:stale-worker"),
        )
        conn.commit()
        assert (
            qa_sessions.delete_session_checked(session_id, "user-1")
            == "deleted"
        )
        return tmp_path

    monkeypatch.setattr(
        knowledge_evolution, "_get_topic_dir", delete_stale_session,
    )

    async def run():
        with pytest.raises(RuntimeError, match="lease changed"):
            await knowledge_evolution.ingest_qa_card_to_knowledge(
                "Source card content long enough for ingestion.",
                "user-1",
                idempotency_marker=marker,
                claim_token=token,
            )

    asyncio.run(run())
    assert qa_sessions.get_session(session_id, "user-1") is None
    assert not (tmp_path / "用户沉淀_qa.md").exists()


def test_duplicate_marker_repairs_index_without_appending_llm_variant(
    isolated_db, tmp_path, monkeypatch,
):
    outputs = iter((
        "python",
        "First normalized knowledge block with enough content to persist.",
    ))
    llm_calls = 0

    class FakeLlm:
        async def ainvoke(self, _messages):
            nonlocal llm_calls
            llm_calls += 1
            return SimpleNamespace(content=next(outputs))

    scheduled = []
    dirtied = []
    monkeypatch.setattr(knowledge_evolution, "get_langchain_llm", lambda **_kwargs: FakeLlm())
    monkeypatch.setattr(
        "backend.indexer.load_topics",
        lambda _user_id: {
            "python": {"name": "Python"},
            "java": {"name": "Java"},
        },
    )
    monkeypatch.setattr(
        knowledge_evolution,
        "_get_topic_dir",
        lambda _topic, _user_id: tmp_path,
    )
    monkeypatch.setattr(
        "backend.embedding_tasks.schedule_incremental_insert",
        lambda *args, **kwargs: scheduled.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        "backend.indexer.mark_topic_index_dirty",
        lambda *args: dirtied.append(args),
    )
    session_id = _create_session()
    marker = "a" * 64
    state, _, claim_token = qa_sessions.claim_ingest_request(
        session_id,
        "user-1",
        "qa-ingest:planned",
        "b" * 64,
        marker,
    )
    assert state == "claimed" and claim_token

    async def run():
        first = await knowledge_evolution.ingest_qa_card_to_knowledge(
            "Source card content long enough for ingestion.",
            "user-1",
            idempotency_marker=marker,
            claim_token=claim_token,
        )
        second = await knowledge_evolution.ingest_qa_card_to_knowledge(
            "Source card content long enough for ingestion.",
            "user-1",
            idempotency_marker=marker,
            claim_token=claim_token,
        )
        return first, second

    first, second = asyncio.run(run())
    deposit = (tmp_path / "用户沉淀_qa.md").read_text(encoding="utf-8")
    assert first["ok"] is True and second["ok"] is True
    assert deposit.count("<!-- qa-ingest-id:") == 1
    assert "First normalized knowledge block" in deposit
    assert llm_calls == 2
    assert len(scheduled) == 1
    assert dirtied == [("python", "user-1")]


def test_post_append_failure_preserves_plan_for_immediate_retry(
    isolated_db, tmp_path, monkeypatch,
):
    outputs = iter((
        "python",
        "Stable normalized knowledge block with enough content to persist.",
    ))
    llm_calls = 0

    class FakeLlm:
        async def ainvoke(self, _messages):
            nonlocal llm_calls
            llm_calls += 1
            return SimpleNamespace(content=next(outputs))

    topic_dirs = {
        "python": tmp_path / "python",
        "java": tmp_path / "java",
    }
    for topic_dir in topic_dirs.values():
        topic_dir.mkdir()

    schedule_calls = 0
    dirtied = []

    def fail_first_schedule(*_args, **_kwargs):
        nonlocal schedule_calls
        schedule_calls += 1
        if schedule_calls == 1:
            raise RuntimeError("simulated post-append scheduling failure")
        return True

    monkeypatch.setattr(
        knowledge_evolution, "get_langchain_llm", lambda **_kwargs: FakeLlm(),
    )
    monkeypatch.setattr(
        "backend.indexer.load_topics",
        lambda _user_id: {
            "python": {"name": "Python"},
            "java": {"name": "Java"},
        },
    )
    monkeypatch.setattr(
        knowledge_evolution,
        "_get_topic_dir",
        lambda topic, _user_id: topic_dirs[topic],
    )
    monkeypatch.setattr(
        "backend.embedding_tasks.schedule_incremental_insert",
        fail_first_schedule,
    )
    monkeypatch.setattr(
        "backend.indexer.mark_topic_index_dirty",
        lambda *args: dirtied.append(args),
    )

    session_id = _create_session()
    body = qa_arena.QAKnowledgeIngestRequest(
        content="Source card content long enough for ingestion.",
    )

    async def run():
        with pytest.raises(RuntimeError, match="post-append scheduling failure"):
            await qa_arena.ingest_knowledge(
                session_id, body, "qa-ingest:post-append", "user-1",
            )

        row = database.get_db().execute(
            "SELECT topic_key, normalized_content, updated_at "
            "FROM qa_ingest_requests WHERE idempotency_key = ?",
            ("qa-ingest:post-append",),
        ).fetchone()
        assert row is not None
        assert row["topic_key"] == "python"
        assert "Stable normalized knowledge block" in row["normalized_content"]
        assert row["updated_at"] == "1970-01-01T00:00:00+00:00"

        return await qa_arena.ingest_knowledge(
            session_id, body, "qa-ingest:post-append", "user-1",
        )

    replay = asyncio.run(run())
    python_deposit = (
        topic_dirs["python"] / "用户沉淀_qa.md"
    ).read_text(encoding="utf-8")
    assert replay["ok"] is True
    assert replay["topic"] == "Python"
    assert python_deposit.count("<!-- qa-ingest-id:") == 1
    assert list(topic_dirs["java"].glob("*.md")) == []
    assert llm_calls == 2
    assert schedule_calls == 1
    assert dirtied == [("python", "user-1")]
