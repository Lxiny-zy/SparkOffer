import sqlite3

from backend.graphs import checkpointer


def test_delete_thread_checkpoints_keeps_other_sessions(monkeypatch, tmp_path):
    db_path = tmp_path / "checkpoints.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE writes (thread_id TEXT, checkpoint_id TEXT)"
    )
    conn.execute("CREATE TABLE migrations (version INTEGER)")
    for table in ("checkpoints", "writes"):
        conn.executemany(
            f"INSERT INTO {table} VALUES (?, ?)",
            [("remove", "1"), ("keep", "2")],
        )
    conn.commit()
    conn.close()
    monkeypatch.setattr(checkpointer.settings, "checkpoint_db_path", db_path)

    checkpointer.delete_thread_checkpoints("remove")

    conn = sqlite3.connect(db_path)
    try:
        for table in ("checkpoints", "writes"):
            assert conn.execute(
                f"SELECT thread_id FROM {table}"
            ).fetchall() == [("keep",)]
    finally:
        conn.close()
