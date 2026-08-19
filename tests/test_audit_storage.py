import sqlite3

from backend.storage import audit


def test_audit_logs_are_sorted_by_timestamp_then_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            user_id TEXT,
            email TEXT,
            ip TEXT,
            detail TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.executemany(
        "INSERT INTO audit_logs (event, created_at) VALUES (?, ?)",
        [
            ("newer-id-older-time", "2026-05-01 09:00:00"),
            ("older-id-newer-time", "2026-07-01 09:00:00"),
            ("same-time-first", "2026-08-01 09:00:00"),
            ("same-time-last", "2026-08-01 09:00:00"),
        ],
    )
    monkeypatch.setattr(audit, "get_db", lambda: conn)

    result = audit.list_audit_logs(limit=10)

    assert [item["event"] for item in result["items"]] == [
        "same-time-last",
        "same-time-first",
        "older-id-newer-time",
        "newer-id-older-time",
    ]

