import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from backend import auth, rate_limit
from backend.config import Settings
from backend.routers import auth as auth_router
from backend.storage.sessions import new_session_id


def _request(peer: str, **headers: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [
            (name.lower().replace("_", "-").encode(), value.encode())
            for name, value in headers.items()
        ],
        "client": (peer, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_production_rejects_public_auth_defaults():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="change-me-in-production",
        default_password="legend",
        cors_allow_origins="*",
        vector_backend="numpy",
    )

    with pytest.raises(RuntimeError, match="Refusing to start"):
        configured.validate_security_settings()


def test_explicit_development_mode_allows_defaults_with_warnings():
    configured = Settings(
        _env_file=None,
        app_env="development",
        jwt_secret="change-me-in-production",
        default_password="legend",
        cors_allow_origins="*",
        vector_backend="numpy",
    )

    issues = configured.validate_security_settings()

    assert len(issues) == 3


def test_passwords_over_bcrypt_utf8_limit_are_rejected_cleanly():
    too_long = "密" * 25  # 75 UTF-8 bytes, despite being only 25 characters.

    with pytest.raises(HTTPException) as exc_info:
        auth.validate_new_password(too_long)

    assert exc_info.value.status_code == 422
    assert "72 UTF-8 bytes" in str(exc_info.value.detail)
    valid_hash = auth._hash_password("valid-password")
    assert auth._verify_password(too_long, valid_hash) is False


def test_production_rejects_bcrypt_incompatible_default_password():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="a-private-jwt-secret",
        default_password="密" * 25,
        cors_allow_origins="https://app.example.com",
        vector_backend="numpy",
    )

    with pytest.raises(RuntimeError, match="72-byte"):
        configured.validate_security_settings()


def test_production_qdrant_requires_api_key():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="a-private-jwt-secret",
        default_password="a-private-password",
        cors_allow_origins="https://app.example.com",
        vector_backend="qdrant",
        qdrant_url="http://qdrant:6333",
        qdrant_api_key="",
    )

    with pytest.raises(RuntimeError, match="QDRANT_API_KEY"):
        configured.validate_security_settings()


def test_production_rejects_empty_cors_allowlist():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="a-private-jwt-secret",
        default_password="a-private-password",
        cors_allow_origins="",
        vector_backend="numpy",
    )

    with pytest.raises(RuntimeError, match="CORS_ALLOW_ORIGINS"):
        configured.validate_security_settings()


def test_production_rejects_short_jwt_secret():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="too-short",
        default_password="a-private-password",
        cors_allow_origins="https://app.example.com",
        vector_backend="numpy",
    )

    with pytest.raises(RuntimeError, match="at least 32"):
        configured.validate_security_settings()


def test_production_rejects_weak_bootstrap_password():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 32,
        default_password="1234567890",
        cors_allow_origins="https://app.example.com",
        vector_backend="numpy",
    )

    with pytest.raises(RuntimeError, match="at least 12"):
        configured.validate_security_settings()


def test_production_rejects_short_qdrant_key():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 32,
        default_password="a-private-password",
        cors_allow_origins="https://app.example.com",
        vector_backend="qdrant",
        qdrant_url="http://qdrant:6333",
        qdrant_api_key="too-short",
    )

    with pytest.raises(RuntimeError, match="QDRANT_API_KEY.*32"):
        configured.validate_security_settings()


def test_production_rejects_plaintext_public_cors_origin():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 32,
        default_password="a-private-password",
        cors_allow_origins="http://app.example.com",
        vector_backend="numpy",
    )

    with pytest.raises(RuntimeError, match="must use HTTPS"):
        configured.validate_security_settings()


def test_production_allows_plaintext_loopback_cors_origin():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 32,
        default_password="a-private-password",
        cors_allow_origins="http://127.0.0.1:9000",
        vector_backend="numpy",
    )

    assert configured.validate_security_settings() == []


@pytest.mark.parametrize(
    "origin",
    ["null", "https://app.example.com/path", "javascript://app.example.com"],
)
def test_production_rejects_malformed_cors_origins(origin):
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 32,
        default_password="a-private-password",
        cors_allow_origins=origin,
        vector_backend="numpy",
    )

    with pytest.raises(RuntimeError, match="invalid browser origins"):
        configured.validate_security_settings()


def test_production_rejects_catch_all_trusted_proxy_network():
    configured = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 32,
        default_password="a-private-password",
        cors_allow_origins="https://app.example.com",
        trusted_proxy_cidrs="0.0.0.0/0",
        vector_backend="numpy",
    )

    with pytest.raises(RuntimeError, match="must not trust the entire"):
        configured.validate_security_settings()


def test_client_ip_ignores_headers_from_untrusted_peer(monkeypatch):
    monkeypatch.setattr(auth_router.settings, "trusted_proxy_cidrs", "10.0.0.0/24")
    request = _request("203.0.113.7", x_real_ip="198.51.100.9")

    assert auth_router.client_ip(request) == "203.0.113.7"


def test_client_ip_accepts_valid_header_from_trusted_peer(monkeypatch):
    monkeypatch.setattr(auth_router.settings, "trusted_proxy_cidrs", "10.0.0.10/32")
    request = _request("10.0.0.10", x_real_ip="198.51.100.9")

    assert auth_router.client_ip(request) == "198.51.100.9"


def test_client_ip_rejects_malformed_forwarded_value(monkeypatch):
    monkeypatch.setattr(auth_router.settings, "trusted_proxy_cidrs", "10.0.0.10/32")
    request = _request("10.0.0.10", x_real_ip="not-an-ip")

    assert auth_router.client_ip(request) == "10.0.0.10"


def test_client_ip_ignores_spoofed_leftmost_forwarded_value(monkeypatch):
    monkeypatch.setattr(auth_router.settings, "trusted_proxy_cidrs", "10.0.0.10/32")
    request = _request(
        "10.0.0.10",
        x_forwarded_for="192.0.2.123, 198.51.100.9",
    )

    assert auth_router.client_ip(request) == "198.51.100.9"


def test_client_ip_walks_across_multiple_trusted_proxies(monkeypatch):
    monkeypatch.setattr(auth_router.settings, "trusted_proxy_cidrs", "10.0.0.0/24")
    request = _request(
        "10.0.0.10",
        x_forwarded_for="198.51.100.9, 10.0.0.20",
    )

    assert auth_router.client_ip(request) == "198.51.100.9"


@pytest.fixture(autouse=True)
def clear_rate_limit_buckets():
    with rate_limit._LOCK:
        rate_limit._BUCKETS.clear()
    yield
    with rate_limit._LOCK:
        rate_limit._BUCKETS.clear()


def test_rate_limiter_hard_caps_active_bucket_count(monkeypatch):
    monkeypatch.setattr(rate_limit, "_MAX_BUCKETS", 3)

    for index in range(12):
        assert rate_limit.check_and_record(f"key-{index}", 5, 900)

    assert len(rate_limit._BUCKETS) == 3
    assert list(rate_limit._BUCKETS) == ["key-9", "key-10", "key-11"]


def test_rate_limiter_prunes_each_bucket_using_its_own_window(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(rate_limit, "_MAX_BUCKETS", 2)
    rate_limit.record_failure("short", window_seconds=1)
    rate_limit.record_failure("long", window_seconds=100)

    now[0] = 2.0
    rate_limit.record_failure("new", window_seconds=100)

    assert "short" not in rate_limit._BUCKETS
    assert "long" in rate_limit._BUCKETS
    assert "new" in rate_limit._BUCKETS


def test_login_rate_limit_reserves_account_and_ip_slots_atomically(monkeypatch):
    monkeypatch.setattr(auth_router, "authenticate_user", lambda *_args: None)
    monkeypatch.setattr(auth_router, "log_event", lambda *args, **kwargs: None)

    def attempt(_index):
        try:
            auth_router.login(
                auth_router.LoginRequest(email="user@example.com", password="wrong"),
                _request("203.0.113.9"),
            )
        except HTTPException as exc:
            return exc.status_code
        return 200

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(attempt, range(20)))

    assert statuses.count(401) == 5
    assert statuses.count(429) == 15


def test_successful_login_releases_its_own_concurrent_reservation(monkeypatch):
    clock = threading.local()
    successful_auth_started = threading.Event()
    finish_successful_auth = threading.Event()

    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(auth_router, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_router, "create_token", lambda _user_id: "token")

    def authenticate(_email, password):
        if password == "success":
            successful_auth_started.set()
            assert finish_successful_auth.wait(timeout=2)
            return {"id": "user-id", "email": "user@example.com"}
        return None

    monkeypatch.setattr(auth_router, "authenticate_user", authenticate)

    def attempt(password, timestamp):
        clock.now = timestamp
        try:
            auth_router.login(
                auth_router.LoginRequest(
                    email="user@example.com", password=password
                ),
                _request("203.0.113.10"),
            )
        except HTTPException as exc:
            return exc.status_code
        return 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        success = pool.submit(attempt, "success", 10.0)
        assert successful_auth_started.wait(timeout=2)

        # This failure reserves the same account/IP buckets after the success
        # request, but completes while the successful bcrypt check is blocked.
        failure = pool.submit(attempt, "failure", 20.0)
        assert failure.result(timeout=2) == 401

        finish_successful_auth.set()
        assert success.result(timeout=2) == 200

    with rate_limit._LOCK:
        remaining_events = [
            list(bucket.events) for bucket in rate_limit._BUCKETS.values()
        ]

    # Both buckets retain the actual failure at t=20. A positional pop would
    # remove that newer failure and leave the successful t=10 reservation.
    assert len(remaining_events) == 2
    assert all(len(events) == 1 for events in remaining_events)
    assert all(events[0][0] == 20.0 for events in remaining_events)


def test_check_and_record_many_keeps_boolean_compatibility():
    limits = [("first", 1, 60), ("second", 1, 60)]

    assert rate_limit.check_and_record_many(limits) is True
    assert rate_limit.check_and_record_many(limits) is False


def test_reserve_many_rejects_nonpositive_limits_without_creating_buckets():
    assert rate_limit.reserve_many([("zero", 0, 60)]) == (False, None)
    assert rate_limit.reserve_many([("negative", -1, 60)]) == (False, None)

    with rate_limit._LOCK:
        assert "zero" not in rate_limit._BUCKETS
        assert "negative" not in rate_limit._BUCKETS


def test_deleted_user_token_is_rejected(monkeypatch):
    monkeypatch.setattr(auth.settings, "jwt_secret", "test-secret")
    token = auth.create_token("deadbeef")
    monkeypatch.setattr(auth, "get_user_by_id", lambda _user_id: None)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(credentials)

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    ("email", "expected_status"),
    [
        ("not-an-email", 422),
        ("taken@example.com", 409),
    ],
)
def test_profile_email_validation_does_not_partially_update_name(
    monkeypatch, email, expected_status
):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE, "
        "password TEXT, name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.executemany(
        "INSERT INTO users (id, email, password, name) VALUES (?, ?, '', ?)",
        [
            ("profile-user", "current@example.com", "Original name"),
            ("other-user", "taken@example.com", "Other user"),
        ],
    )
    conn.commit()
    monkeypatch.setattr(auth, "get_db", lambda: conn)

    with pytest.raises(HTTPException) as exc_info:
        auth.update_user_profile("profile-user", name="Changed name", email=email)

    assert exc_info.value.status_code == expected_status
    row = conn.execute(
        "SELECT name, email FROM users WHERE id = 'profile-user'"
    ).fetchone()
    assert (row["name"], row["email"]) == ("Original name", "current@example.com")
    assert conn.in_transaction is False


def test_profile_email_unique_race_returns_conflict_and_rolls_back(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "profile-race.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE, "
        "password TEXT, name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO users (id, email, password, name) VALUES (?, ?, '', ?)",
        ("profile-user", "current@example.com", "Original name"),
    )
    conn.commit()

    class RacingConnection:
        def __init__(self):
            self.raced = False

        @property
        def in_transaction(self):
            return conn.in_transaction

        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE users SET") and not self.raced:
                self.raced = True
                competitor = sqlite3.connect(db_path)
                try:
                    competitor.execute(
                        "INSERT INTO users (id, email, password, name) "
                        "VALUES (?, ?, '', ?)",
                        ("racing-user", "raced@example.com", "Racing user"),
                    )
                    competitor.commit()
                finally:
                    competitor.close()
            return conn.execute(sql, parameters)

        def commit(self):
            conn.commit()

        def rollback(self):
            conn.rollback()

    racing_conn = RacingConnection()
    monkeypatch.setattr(auth, "get_db", lambda: racing_conn)

    with pytest.raises(HTTPException) as exc_info:
        auth.update_user_profile(
            "profile-user", name="Changed name", email="raced@example.com"
        )

    assert exc_info.value.status_code == 409
    assert racing_conn.raced is True
    row = conn.execute(
        "SELECT name, email FROM users WHERE id = 'profile-user'"
    ).fetchone()
    assert (row["name"], row["email"]) == ("Original name", "current@example.com")
    assert conn.in_transaction is False


@pytest.mark.parametrize("user_id", ["deadbeef", "a" * 32])
def test_tokens_accept_legacy_and_full_length_user_ids(monkeypatch, user_id):
    monkeypatch.setattr(auth.settings, "jwt_secret", "test-secret")
    monkeypatch.setattr(auth, "get_user_by_id", lambda candidate: {"id": candidate})
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=auth.create_token(user_id),
    )

    assert auth.get_current_user(credentials) == user_id


def test_new_user_and_default_user_ids_use_full_128_bits(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE, "
        "password TEXT, name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    monkeypatch.setattr(auth, "get_db", lambda: conn)
    monkeypatch.setattr(auth, "_init_user_knowledge", lambda _user_id: None)
    monkeypatch.setattr(auth.settings, "allow_registration", True)
    monkeypatch.setattr(auth.settings, "invite_code", "")

    created = auth.create_user("new@example.com", "private-password")

    assert len(created["id"]) == 32
    assert auth._USER_ID_PATTERN.fullmatch(created["id"])
    assert len(auth._default_user_id("owner@example.com")) == 32


def test_generated_session_ids_are_full_length_and_unique():
    generated = {new_session_id() for _ in range(1_000)}

    assert len(generated) == 1_000
    assert all(len(value) == 32 and auth._USER_ID_PATTERN.fullmatch(value) for value in generated)


def test_user_id_migration_rewrites_all_tables_and_user_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(auth.settings, "base_dir", tmp_path)
    old_id = "default0"
    new_id = "0123abcd"
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute("CREATE TABLE sessions (id INTEGER, user_id TEXT)")
    conn.execute("CREATE TABLE knowledge_cards (id TEXT, user_id TEXT, PRIMARY KEY (id, user_id))")
    conn.execute("CREATE TABLE audit_logs (id INTEGER, user_id TEXT)")
    conn.execute("INSERT INTO users VALUES (?, ?)", (old_id, "owner@example.com"))
    conn.execute("INSERT INTO sessions VALUES (1, ?)", (old_id,))
    conn.execute("INSERT INTO knowledge_cards VALUES ('card', ?)", (old_id,))
    conn.execute("INSERT INTO audit_logs VALUES (1, ?)", (old_id,))
    conn.commit()

    old_user_dir = tmp_path / "data" / "users" / old_id
    new_user_dir = tmp_path / "data" / "users" / new_id
    old_user_dir.mkdir(parents=True)
    new_user_dir.mkdir(parents=True)
    (old_user_dir / "legacy.txt").write_text("legacy", encoding="utf-8")
    (new_user_dir / "keep.txt").write_text("keep", encoding="utf-8")
    old_notes = tmp_path / "data" / "qa_notes" / old_id
    new_notes = tmp_path / "data" / "qa_notes" / new_id
    old_notes.mkdir(parents=True)
    new_notes.mkdir(parents=True)
    (old_notes / "summary.md").write_text("summary", encoding="utf-8")

    changed = auth.migrate_user_id(conn, old_id, new_id)

    assert changed == 3
    assert conn.execute("SELECT id FROM users").fetchone()[0] == new_id
    for table in ("sessions", "knowledge_cards", "audit_logs"):
        assert conn.execute(f"SELECT user_id FROM {table}").fetchone()[0] == new_id
    assert (new_user_dir / "legacy.txt").read_text(encoding="utf-8") == "legacy"
    assert (new_user_dir / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (new_notes / "summary.md").exists()
    assert not old_user_dir.exists()
    assert not old_notes.exists()


def test_user_id_migration_rolls_back_db_and_keeps_old_files_on_conflict(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(auth.settings, "base_dir", tmp_path)
    old_id = "default0"
    new_id = "0123abcd"
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute(
        "CREATE TABLE cards (id TEXT, user_id TEXT, UNIQUE (id, user_id))"
    )
    conn.execute("INSERT INTO users VALUES (?, ?)", (old_id, "owner@example.com"))
    conn.execute("INSERT INTO cards VALUES ('same-card', ?)", (old_id,))
    conn.execute("INSERT INTO cards VALUES ('same-card', ?)", (new_id,))
    conn.commit()
    old_dir = tmp_path / "data" / "users" / old_id
    old_dir.mkdir(parents=True)
    (old_dir / "profile.json").write_text("{}", encoding="utf-8")

    with pytest.raises(sqlite3.IntegrityError):
        auth.migrate_user_id(conn, old_id, new_id)

    assert conn.execute("SELECT id FROM users").fetchone()[0] == old_id
    assert conn.execute(
        "SELECT COUNT(*) FROM cards WHERE user_id = ?", (old_id,)
    ).fetchone()[0] == 1
    assert (old_dir / "profile.json").exists()
    assert not (tmp_path / "data" / "users" / new_id).exists()


def test_user_id_migration_does_not_overwrite_conflicting_target_file(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(auth.settings, "base_dir", tmp_path)
    old_id = "default0"
    new_id = "0123abcd"
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute("INSERT INTO users VALUES (?, ?)", (old_id, "owner@example.com"))
    conn.commit()
    old_dir = tmp_path / "data" / "users" / old_id
    new_dir = tmp_path / "data" / "users" / new_id
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (old_dir / "same.txt").write_text("legacy", encoding="utf-8")
    (new_dir / "same.txt").write_text("newer", encoding="utf-8")

    with pytest.raises(RuntimeError, match="content conflict"):
        auth.migrate_user_id(conn, old_id, new_id)

    assert conn.execute("SELECT id FROM users").fetchone()[0] == old_id
    assert (old_dir / "same.txt").read_text(encoding="utf-8") == "legacy"
    assert (new_dir / "same.txt").read_text(encoding="utf-8") == "newer"


def test_user_id_migration_removes_copies_when_commit_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(auth.settings, "base_dir", tmp_path)
    old_id = "default0"
    new_id = "0123abcd"
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute("CREATE TABLE allowed_ids (id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE records (user_id TEXT REFERENCES allowed_ids(id) "
        "DEFERRABLE INITIALLY DEFERRED)"
    )
    conn.execute("INSERT INTO users VALUES (?, ?)", (old_id, "owner@example.com"))
    conn.execute("INSERT INTO allowed_ids VALUES (?)", (old_id,))
    conn.execute("INSERT INTO records VALUES (?)", (old_id,))
    conn.commit()
    old_dir = tmp_path / "data" / "users" / old_id
    new_dir = tmp_path / "data" / "users" / new_id
    old_dir.mkdir(parents=True)
    (old_dir / "profile.json").write_text("{}", encoding="utf-8")

    with pytest.raises(sqlite3.IntegrityError):
        auth.migrate_user_id(conn, old_id, new_id)

    assert conn.execute("SELECT id FROM users").fetchone()[0] == old_id
    assert conn.execute("SELECT user_id FROM records").fetchone()[0] == old_id
    assert (old_dir / "profile.json").exists()
    assert not new_dir.exists()
