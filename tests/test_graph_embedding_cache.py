import json
import sqlite3

from backend import graph


def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE question_embeddings ("
        "question_hash TEXT PRIMARY KEY, topic TEXT, question_text TEXT, "
        "embedding BLOB NOT NULL, user_id TEXT, created_at TEXT)"
    )
    return conn


def test_graph_embedding_cache_isolates_user_and_model(monkeypatch):
    conn = _cache_conn()
    fingerprint = {"value": "model-a"}
    batches: list[list[str]] = []

    class FakeEmbedding:
        def get_text_embedding_batch(self, texts):
            batches.append(list(texts))
            width = 3 if fingerprint["value"] == "model-b" else 2
            return [[float(i + 1) for i in range(width)] for _ in texts]

    monkeypatch.setattr(
        "backend.indexer._embedding_fingerprint", lambda: fingerprint["value"]
    )
    monkeypatch.setattr(
        "backend.llm_provider.get_embedding", lambda: FakeEmbedding()
    )
    questions = [{"question": "What is a transaction?"}]

    first = graph._get_or_compute_embeddings(conn, questions, "db", "user-a")
    cached = graph._get_or_compute_embeddings(conn, questions, "db", "user-a")
    other_user = graph._get_or_compute_embeddings(conn, questions, "db", "user-b")
    fingerprint["value"] = "model-b"
    other_model = graph._get_or_compute_embeddings(conn, questions, "db", "user-a")

    assert first.shape == cached.shape == other_user.shape == (1, 2)
    assert other_model.shape == (1, 3)
    assert batches == [
        ["What is a transaction?"],
        ["What is a transaction?"],
        ["What is a transaction?"],
    ]
    assert conn.execute("SELECT COUNT(*) FROM question_embeddings").fetchone()[0] == 3


def test_graph_caps_questions_to_latest_entries(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sessions ("
        "session_id TEXT, mode TEXT, topic TEXT, questions TEXT, scores TEXT, "
        "review TEXT, user_id TEXT, created_at TEXT)"
    )
    for idx in range(3):
        conn.execute(
            "INSERT INTO sessions VALUES (?, 'topic_drill', 'python', ?, ?, 'done', 'u1', ?)",
            (
                f"s{idx}",
                json.dumps([{"id": "q", "question": f"Question {idx}"}]),
                json.dumps([{"question_id": "q", "score": 8}]),
                f"2026-01-0{idx + 1}",
            ),
        )
    monkeypatch.setattr(graph, "MAX_GRAPH_QUESTIONS", 2)

    questions, meta = graph._extract_questions(conn, "python", "u1")

    assert [question["question"] for question in questions] == ["Question 1", "Question 2"]
    assert meta["unique_questions"] == 3
    assert meta["returned_questions"] == 2
    assert meta["truncated"] is True
