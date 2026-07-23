from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import backend.storage.database as database
from backend.storage.sessions import (
    abort_session_sync_claim,
    commit_resume_turn,
    create_session,
    get_session,
    mark_session_sync_step,
    release_resume_turn_claim,
    release_session_evaluation_claim,
    release_session_sync_claim,
    renew_resume_turn_claim,
    session_sync_targets,
    try_claim_resume_turn,
    try_claim_session_evaluation,
    try_claim_session_sync,
)


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "resume-turn-claim.db"
    database._local.conn = None
    try:
        database.init_all_tables()
        yield database.DB_PATH
    finally:
        temp_conn = getattr(database._local, "conn", None)
        if temp_conn is not None:
            temp_conn.close()
        database.DB_PATH = original_path
        database._local.conn = original_conn


def _set_claim_time(session_id: str, field: str, value: str) -> None:
    conn = database.get_db()
    conn.execute(
        f"UPDATE sessions SET meta = json_set(meta, '$.{field}', ?) "
        "WHERE session_id = ?",
        (value, session_id),
    )
    conn.commit()


def test_resume_turn_claim_requires_owned_unfinished_resume_session(isolated_db):
    create_session("resume", "resume", user_id="u1")
    create_session("drill", "topic_drill", "python", user_id="u1")
    create_session("complete", "resume", user_id="u1")
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET review = 'done' WHERE session_id = 'complete'"
    )
    conn.commit()

    token = try_claim_resume_turn("resume", user_id="u1")

    assert token
    assert try_claim_resume_turn("resume", user_id="u1") is None
    assert try_claim_resume_turn("resume", user_id="other") is None
    assert try_claim_resume_turn("drill", user_id="u1") is None
    assert try_claim_resume_turn("complete", user_id="u1") is None


def test_only_one_connection_can_claim_a_resume_turn(isolated_db):
    create_session("concurrent", "resume", user_id="u1")
    barrier = threading.Barrier(2)

    def claim():
        database._local.conn = None
        barrier.wait(timeout=5)
        return try_claim_resume_turn("concurrent", user_id="u1")

    with ThreadPoolExecutor(max_workers=2) as executor:
        tokens = list(executor.map(lambda _index: claim(), range(2)))

    assert sum(token is not None for token in tokens) == 1
    stored = get_session("concurrent", user_id="u1")
    assert stored["meta"]["resume_turn_claim_token"] in tokens


def test_resume_turn_commit_is_token_fenced_and_clears_claim(isolated_db):
    create_session("commit", "resume", user_id="u1")
    token = try_claim_resume_turn("commit", user_id="u1")
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]

    assert commit_resume_turn(
        "commit", messages, user_id="u1", claim_token="wrong",
    ) is False
    assert get_session("commit", user_id="u1")["transcript"] == []

    assert commit_resume_turn(
        "commit", messages, user_id="u1", claim_token=token,
    ) is True
    stored = get_session("commit", user_id="u1")
    assert [message["content"] for message in stored["transcript"]] == [
        "question", "answer",
    ]
    assert all(message.get("time") for message in stored["transcript"])
    assert "resume_turn_claim_token" not in stored["meta"]
    assert commit_resume_turn(
        "commit", messages, user_id="u1", claim_token=token,
    ) is False


def test_resume_turn_commit_rejects_session_completed_after_claim(isolated_db):
    create_session("completed-late", "resume", user_id="u1")
    token = try_claim_resume_turn("completed-late", user_id="u1")
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET review = 'done' WHERE session_id = 'completed-late'"
    )
    conn.commit()

    assert commit_resume_turn(
        "completed-late",
        [{"role": "user", "content": "late"}],
        user_id="u1",
        claim_token=token,
    ) is False
    assert get_session("completed-late", user_id="u1")["transcript"] == []


def test_evaluation_rejects_active_turn_and_fences_expired_owner(isolated_db):
    create_session("eval", "resume", user_id="u1")
    turn_token = try_claim_resume_turn("eval", user_id="u1")

    assert try_claim_session_evaluation("eval", user_id="u1") is None

    _set_claim_time(
        "eval", "resume_turn_claimed_at", "2000-01-01T00:00:00",
    )
    evaluation_token = try_claim_session_evaluation("eval", user_id="u1")

    assert evaluation_token
    stored = get_session("eval", user_id="u1")
    assert "resume_turn_claim_token" not in stored["meta"]
    assert commit_resume_turn(
        "eval",
        [{"role": "user", "content": "stale"}],
        user_id="u1",
        claim_token=turn_token,
    ) is False
    assert release_resume_turn_claim(
        "eval", user_id="u1", claim_token=turn_token,
    ) is False


def test_resume_turn_renewal_keeps_evaluation_fenced(isolated_db):
    create_session("renew", "resume", user_id="u1")
    turn_token = try_claim_resume_turn("renew", user_id="u1")
    _set_claim_time(
        "renew", "resume_turn_claimed_at", "2000-01-01T00:00:00",
    )

    assert renew_resume_turn_claim(
        "renew", user_id="u1", claim_token="wrong",
    ) is False
    assert renew_resume_turn_claim(
        "renew", user_id="u1", claim_token=turn_token,
    ) is True
    renewed = get_session("renew", user_id="u1")
    assert renewed["meta"]["resume_turn_claimed_at"] > "2000-01-01T00:00:00"
    assert try_claim_session_evaluation("renew", user_id="u1") is None

    database.get_db().execute(
        "UPDATE sessions SET review = 'done' WHERE session_id = 'renew'"
    )
    database.get_db().commit()
    assert renew_resume_turn_claim(
        "renew", user_id="u1", claim_token=turn_token,
    ) is False


def test_sync_rejects_active_turn_and_fences_expired_owner(isolated_db):
    create_session("sync", "resume", user_id="u1")
    turn_token = try_claim_resume_turn("sync", user_id="u1")

    assert try_claim_session_sync("sync", user_id="u1") is None

    _set_claim_time(
        "sync", "resume_turn_claimed_at", "2000-01-01T00:00:00",
    )
    sync_token = try_claim_session_sync("sync", user_id="u1")

    assert sync_token
    assert commit_resume_turn(
        "sync",
        [{"role": "user", "content": "stale"}],
        user_id="u1",
        claim_token=turn_token,
    ) is False
    assert release_resume_turn_claim(
        "sync", user_id="u1", claim_token=turn_token,
    ) is False


def test_resume_turn_takes_over_expired_evaluation_claim(isolated_db):
    session_id = "stale-evaluation"
    create_session(session_id, "resume", user_id="u1")
    stale_token = try_claim_session_evaluation(session_id, user_id="u1")
    _set_claim_time(
        session_id, "evaluation_claimed_at", "2000-01-01T00:00:00",
    )

    turn_token = try_claim_resume_turn(session_id, user_id="u1")

    assert turn_token
    stored = get_session(session_id, user_id="u1")
    assert "evaluation_claim_token" not in stored["meta"]
    assert release_session_evaluation_claim(
        session_id, user_id="u1", claim_token=stale_token,
    ) is False


def test_resume_turn_rejects_pending_or_partially_applied_sync(isolated_db):
    create_session("pending", "resume", user_id="u1")
    pending_token = try_claim_session_sync("pending", user_id="u1")
    _set_claim_time("pending", "sync_claimed_at", "2000-01-01T00:00:00")

    assert try_claim_resume_turn("pending", user_id="u1") is None
    assert release_session_sync_claim(
        "pending", user_id="u1", claim_token=pending_token,
    ) is True
    assert try_claim_resume_turn("pending", user_id="u1") is None

    create_session("partial", "resume", user_id="u1")
    partial_token = try_claim_session_sync("partial", user_id="u1")
    assert mark_session_sync_step(
        "partial", "profile", user_id="u1", claim_token=partial_token,
    ) is True
    assert release_session_sync_claim(
        "partial", user_id="u1", claim_token=partial_token,
    ) is True
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_remove(meta, '$.sync_pending_at') "
        "WHERE session_id = 'partial'"
    )
    conn.commit()
    assert try_claim_resume_turn("partial", user_id="u1") is None


def test_sync_targets_are_frozen_atomically_with_claim_and_reused(isolated_db):
    create_session("targets", "resume", user_id="u1")

    first_token = try_claim_session_sync(
        "targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=[" python ", "", "python", "go"],
    )

    assert session_sync_targets(
        "targets", "knowledge", user_id="u1", claim_token=first_token,
    ) == ["python", "go"]
    assert release_session_sync_claim(
        "targets", user_id="u1", claim_token=first_token,
    ) is True

    retry_token = try_claim_session_sync(
        "targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=["changed"],
    )
    assert session_sync_targets(
        "targets", "knowledge", user_id="u1", claim_token=retry_token,
    ) == ["python", "go"]
    assert session_sync_targets(
        "targets", "knowledge", user_id="u1", claim_token=first_token,
    ) == []


def test_empty_sync_target_list_is_still_frozen(isolated_db):
    create_session("empty-targets", "resume", user_id="u1")
    first_token = try_claim_session_sync(
        "empty-targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=["", 1, None],
    )
    assert session_sync_targets(
        "empty-targets", "knowledge", user_id="u1", claim_token=first_token,
    ) == []
    assert release_session_sync_claim(
        "empty-targets", user_id="u1", claim_token=first_token,
    ) is True

    retry_token = try_claim_session_sync(
        "empty-targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=["must-not-appear"],
    )
    assert session_sync_targets(
        "empty-targets", "knowledge", user_id="u1", claim_token=retry_token,
    ) == []
    stored = get_session("empty-targets", user_id="u1")
    assert stored["meta"]["sync_targets"]["knowledge"] == []


def test_first_target_freeze_preserves_legacy_completed_topics(isolated_db):
    create_session("legacy-targets", "resume", user_id="u1")
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_set(meta, '$.sync_steps', "
        "json_object('knowledge_extract:legacy', 'done')) "
        "WHERE session_id = 'legacy-targets'"
    )
    conn.commit()

    token = try_claim_session_sync(
        "legacy-targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=["new"],
    )

    assert session_sync_targets(
        "legacy-targets", "knowledge", user_id="u1", claim_token=token,
    ) == ["new", "legacy"]


def test_abort_sync_claim_only_before_first_step(isolated_db):
    create_session("abort-fresh", "resume", user_id="u1")
    fresh_token = try_claim_session_sync(
        "abort-fresh",
        user_id="u1",
        target_group="knowledge",
        target_topics=["python"],
    )

    assert abort_session_sync_claim(
        "abort-fresh", user_id="u1", claim_token="wrong",
    ) is False
    assert abort_session_sync_claim(
        "abort-fresh", user_id="u1", claim_token=fresh_token,
    ) is True
    aborted = get_session("abort-fresh", user_id="u1")
    assert "sync_claim_token" not in aborted["meta"]
    assert "sync_claimed_at" not in aborted["meta"]
    assert "sync_pending_at" not in aborted["meta"]
    assert aborted["meta"]["sync_targets"]["knowledge"] == ["python"]

    create_session("abort-started", "resume", user_id="u1")
    started_token = try_claim_session_sync("abort-started", user_id="u1")
    assert mark_session_sync_step(
        "abort-started", "profile", user_id="u1", claim_token=started_token,
    ) is True
    assert abort_session_sync_claim(
        "abort-started", user_id="u1", claim_token=started_token,
    ) is False
    assert release_session_sync_claim(
        "abort-started", user_id="u1", claim_token=started_token,
    ) is True
    started = get_session("abort-started", user_id="u1")
    assert started["meta"].get("sync_pending_at")


def test_concurrent_sync_target_freeze_has_one_stable_winner(isolated_db):
    create_session("target-race", "resume", user_id="u1")
    # Both callers intentionally share the same current token, modelling two
    # processes resuming the same claimed operation.
    token = try_claim_session_sync("target-race", user_id="u1")
    assert release_session_sync_claim(
        "target-race", user_id="u1", claim_token=token,
    ) is True
    barrier = threading.Barrier(2)

    def reclaim(target):
        database._local.conn = None
        barrier.wait(timeout=5)
        claim_token = try_claim_session_sync(
            "target-race",
            user_id="u1",
            target_group="knowledge",
            target_topics=[target],
        )
        if not claim_token:
            return None, []
        return claim_token, session_sync_targets(
            "target-race", "knowledge", user_id="u1", claim_token=claim_token,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reclaim, ["python", "go"]))

    winners = [result for result in results if result[0]]
    assert len(winners) == 1
    assert winners[0][1] in (["python"], ["go"])
