import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import backend.storage.database as database
from backend.spaced_repetition import sm2_update
from backend.storage import knowledge_cards


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "storage-concurrency.db"
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


def test_concurrent_reviews_advance_sm2_state_twice(isolated_db, monkeypatch):
    knowledge_cards.upsert_cards("user-1", "python", "understand", [{
        "id": "card-1",
        "title": "GIL",
        "knowledge": "knowledge",
        "source_refs": [],
    }])
    first_entered = threading.Event()
    release_first = threading.Event()
    calls_lock = threading.Lock()
    calls = 0
    real_update = knowledge_cards.sm2_update

    def controlled_update(state, score):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        return real_update(state, score)

    monkeypatch.setattr(knowledge_cards, "sm2_update", controlled_update)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            knowledge_cards.record_review,
            user_id="user-1",
            card_id="card-1",
            familiarity="known",
        )
        assert first_entered.wait(timeout=2)
        second = pool.submit(
            knowledge_cards.record_review,
            user_id="user-1",
            card_id="card-1",
            familiarity="known",
        )
        release_first.set()
        first.result(timeout=3)
        second.result(timeout=3)

    row = database.get_db().execute(
        "SELECT review_count, sr_state FROM knowledge_cards "
        "WHERE user_id = ? AND id = ?",
        ("user-1", "card-1"),
    ).fetchone()
    expected = sm2_update(sm2_update({}, 9.0), 9.0)
    assert row["review_count"] == 2
    assert json.loads(row["sr_state"]) == expected
