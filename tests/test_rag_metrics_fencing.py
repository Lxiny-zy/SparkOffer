from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

import backend.storage.database as database
from backend.storage.rag_metrics_store import (
    get_rag_metrics_for_session,
    get_rag_metrics_history,
    save_rag_metrics,
)
from backend.storage.sessions import create_session, try_claim_session_evaluation


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "rag-metrics-fencing.db"
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


def test_active_evaluation_token_persists_all_metric_fields(isolated_db):
    create_session("active", "resume", topic="python", user_id="user-1")
    token = try_claim_session_evaluation("active", user_id="user-1")
    assert token

    saved = save_rag_metrics(
        "active",
        "user-1",
        "python",
        "answer_eval",
        relevance=0.1,
        coverage=0.2,
        diversity=0.3,
        faithfulness=0.4,
        answer_relevance=0.5,
        answer_correctness=0.6,
        chunk_count=7,
        detail={"source": "fenced"},
        evaluation_token=token,
    )

    assert saved is True
    row = database.get_db().execute(
        "SELECT * FROM rag_metrics WHERE session_id = ? AND user_id = ?",
        ("active", "user-1"),
    ).fetchone()
    assert row is not None
    assert row["topic"] == "python"
    assert row["stage"] == "answer_eval"
    assert row["context_relevance"] == 0.1
    assert row["coverage"] == 0.2
    assert row["diversity"] == 0.3
    assert row["faithfulness"] == 0.4
    assert row["answer_relevance"] == 0.5
    assert row["answer_correctness"] == 0.6
    assert row["chunk_count"] == 7
    assert json.loads(row["detail_json"]) == {"source": "fenced"}


def test_reclaimed_evaluation_rejects_stale_token_and_accepts_new_token(isolated_db):
    create_session("reclaimed", "resume", topic="rag", user_id="user-1")
    stale_token = try_claim_session_evaluation("reclaimed", user_id="user-1")
    assert stale_token

    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_set(meta, '$.evaluation_claimed_at', ?) "
        "WHERE session_id = ? AND user_id = ?",
        ("2000-01-01T00:00:00", "reclaimed", "user-1"),
    )
    conn.commit()
    active_token = try_claim_session_evaluation("reclaimed", user_id="user-1")
    assert active_token and active_token != stale_token

    assert save_rag_metrics(
        "reclaimed",
        "user-1",
        "rag",
        "question_gen",
        relevance=0.25,
        evaluation_token=stale_token,
    ) is False
    assert save_rag_metrics(
        "reclaimed",
        "user-1",
        "rag",
        "question_gen",
        relevance=0.75,
        evaluation_token=active_token,
    ) is True

    rows = conn.execute(
        "SELECT context_relevance FROM rag_metrics "
        "WHERE session_id = ? AND user_id = ?",
        ("reclaimed", "user-1"),
    ).fetchall()
    assert [row["context_relevance"] for row in rows] == [0.75]


@pytest.mark.parametrize("token", ["", "wrong-token"])
def test_invalid_supplied_token_cannot_bypass_fence(isolated_db, token):
    create_session("invalid", "resume", topic="rag", user_id="user-1")
    assert try_claim_session_evaluation("invalid", user_id="user-1")

    assert save_rag_metrics(
        "invalid",
        "user-1",
        "rag",
        "question_gen",
        evaluation_token=token,
    ) is False
    count = database.get_db().execute(
        "SELECT COUNT(*) FROM rag_metrics WHERE session_id = ?",
        ("invalid",),
    ).fetchone()[0]
    assert count == 0


def test_omitted_token_preserves_legacy_unfenced_writer(isolated_db):
    assert save_rag_metrics(
        "background-only",
        "user-1",
        "rag",
        "question_gen",
        relevance=0.5,
    ) is True
    count = database.get_db().execute(
        "SELECT COUNT(*) FROM rag_metrics WHERE session_id = ?",
        ("background-only",),
    ).fetchone()[0]
    assert count == 1


def test_repeated_stage_writes_upsert_one_latest_row(isolated_db):
    assert save_rag_metrics(
        "repeat", "user-1", "rag", "question_gen",
        relevance=0.25, detail={"attempt": 1},
    ) is True
    assert save_rag_metrics(
        "repeat", "user-1", "rag", "question_gen",
        relevance=0.85, coverage=0.9, detail={"attempt": 2},
    ) is True

    rows = database.get_db().execute(
        "SELECT id, context_relevance, coverage, detail_json "
        "FROM rag_metrics WHERE user_id = ? AND session_id = ? AND stage = ?",
        ("user-1", "repeat", "question_gen"),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["context_relevance"] == 0.85
    assert rows[0]["coverage"] == 0.9
    assert json.loads(rows[0]["detail_json"]) == {"attempt": 2}
    history = get_rag_metrics_history("user-1")
    session_history = get_rag_metrics_for_session("repeat", "user-1")
    assert len(history) == 1
    assert len(session_history) == 1
    assert history[0]["relevance"] == 0.85
    assert session_history[0]["detail"] == {"attempt": 2}


def test_stale_fenced_upsert_cannot_overwrite_existing_row(isolated_db):
    create_session("upsert-fence", "resume", topic="rag", user_id="user-1")
    stale_token = try_claim_session_evaluation("upsert-fence", user_id="user-1")
    assert stale_token
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_set(meta, '$.evaluation_claimed_at', ?) "
        "WHERE session_id = ? AND user_id = ?",
        ("2000-01-01T00:00:00", "upsert-fence", "user-1"),
    )
    conn.commit()
    active_token = try_claim_session_evaluation("upsert-fence", user_id="user-1")
    assert active_token and active_token != stale_token

    assert save_rag_metrics(
        "upsert-fence", "user-1", "rag", "answer_eval",
        relevance=0.2, evaluation_token=active_token,
    ) is True
    assert save_rag_metrics(
        "upsert-fence", "user-1", "rag", "answer_eval",
        relevance=0.99, evaluation_token=stale_token,
    ) is False

    row = conn.execute(
        "SELECT context_relevance FROM rag_metrics "
        "WHERE session_id = ? AND user_id = ? AND stage = ?",
        ("upsert-fence", "user-1", "answer_eval"),
    ).fetchone()
    assert row["context_relevance"] == 0.2


def test_metrics_migration_keeps_latest_duplicate_and_adds_unique_index(isolated_db):
    conn = database.get_db()
    conn.execute("DROP INDEX idx_rag_metrics_user_session_stage")
    conn.execute("DELETE FROM rag_metrics")
    conn.executemany(
        """INSERT INTO rag_metrics
           (id, session_id, user_id, topic, stage, context_relevance, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (100, "legacy", "user-1", "rag", "question_gen", 0.1, "2024-01-01 00:00:00"),
            (50, "legacy", "user-1", "rag", "question_gen", 0.9, "2025-01-01 00:00:00"),
            (101, "legacy", "user-2", "rag", "question_gen", 0.7, "2024-01-01 00:00:00"),
        ],
    )
    conn.commit()

    database.init_all_tables()

    rows = conn.execute(
        "SELECT id, user_id, context_relevance FROM rag_metrics "
        "WHERE session_id = ? ORDER BY user_id",
        ("legacy",),
    ).fetchall()
    assert [(row["id"], row["user_id"], row["context_relevance"]) for row in rows] == [
        (50, "user-1", 0.9),
        (101, "user-2", 0.7),
    ]
    index = conn.execute(
        "SELECT name, [unique] FROM pragma_index_list('rag_metrics') "
        "WHERE name = ?",
        ("idx_rag_metrics_user_session_stage",),
    ).fetchone()
    assert index is not None
    assert index["unique"] == 1

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rag_metrics "
            "(session_id, user_id, topic, stage) VALUES (?, ?, ?, ?)",
            ("legacy", "user-1", "rag", "question_gen"),
        )
    conn.rollback()


def test_concurrent_stage_writes_are_serialized_by_unique_upsert(isolated_db):
    def write(value):
        return save_rag_metrics(
            "concurrent", "user-1", "rag", "question_gen", relevance=value,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(24)))

    assert all(results)
    count = database.get_db().execute(
        "SELECT COUNT(*) FROM rag_metrics "
        "WHERE user_id = ? AND session_id = ? AND stage = ?",
        ("user-1", "concurrent", "question_gen"),
    ).fetchone()[0]
    assert count == 1


def test_concurrent_fenced_stage_writes_are_serialized(isolated_db):
    create_session("concurrent-fenced", "resume", topic="rag", user_id="user-1")
    token = try_claim_session_evaluation("concurrent-fenced", user_id="user-1")
    assert token

    def write(value):
        return save_rag_metrics(
            "concurrent-fenced", "user-1", "rag", "answer_eval",
            relevance=value, evaluation_token=token,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(24)))

    assert all(results)
    count = database.get_db().execute(
        "SELECT COUNT(*) FROM rag_metrics "
        "WHERE user_id = ? AND session_id = ? AND stage = ?",
        ("user-1", "concurrent-fenced", "answer_eval"),
    ).fetchone()[0]
    assert count == 1
