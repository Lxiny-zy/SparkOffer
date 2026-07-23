"""Authentication — users table, password hashing, JWT, FastAPI dependency."""
import re
import uuid
import shutil
import sqlite3
import logging
import hashlib
import filecmp
from datetime import datetime, timezone, timedelta

import bcrypt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from backend.config import settings
from backend.storage.database import get_db
from backend.user_vector_migration import (
    cleanup_qdrant_legacy_user,
    prepare_qdrant_user_migration,
)

logger = logging.getLogger("uvicorn")

bearer_scheme = HTTPBearer()

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7

# Existing installations used 8-hex ids. New accounts use the full 128-bit
# uuid/hash so the globally-keyed database and per-user directories do not hit
# birthday collisions as the user count grows.
_USER_ID_PATTERN = re.compile(r"^(?:[a-f0-9]{8}|[a-f0-9]{32})$")

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_BYTES = 72
_QDRANT_CLEANUP_TABLE = "user_id_qdrant_cleanup"
_KNOWN_PUBLIC_DEFAULT_PASSWORDS = ("legend",)


def validate_new_password(password: str) -> None:
    """Validate the shared registration/password-change bcrypt contract."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(422, f"Password must be at most {MAX_PASSWORD_BYTES} UTF-8 bytes")


def _hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password exceeds bcrypt's {MAX_PASSWORD_BYTES}-byte limit")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, hashed.encode("utf-8"))
    except ValueError:
        return False


def _reject_public_default_owner_password(password_hash: str) -> None:
    if settings.is_development():
        return
    if any(
        _verify_password(password, password_hash)
        for password in _KNOWN_PUBLIC_DEFAULT_PASSWORDS
    ):
        raise RuntimeError(
            "Refusing to start because the existing owner account still uses "
            "the public default password; reset it before production startup"
        )


def _init_user_knowledge(user_id: str):
    """Copy global knowledge base and topics.json to a new user's data directory."""
    global_knowledge = settings.base_dir / "data" / "knowledge"
    global_topics = settings.base_dir / "data" / "topics.json"

    user_knowledge = settings.user_knowledge_path(user_id)
    user_topics = settings.user_topics_path(user_id)

    # Copy knowledge files if global source exists and user doesn't have them yet
    if global_knowledge.exists() and not user_knowledge.exists():
        shutil.copytree(global_knowledge, user_knowledge)
        logger.info(f"Initialized knowledge for user {user_id}")

    # Copy topics.json
    if global_topics.exists() and not user_topics.exists():
        user_topics.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(global_topics, user_topics)
        logger.info(f"Initialized topics.json for user {user_id}")


def _default_user_id(email: str) -> str:
    """Generate a stable user id for the configured default admin account."""
    normalized = email.lower().strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:32]


def _quote_identifier(identifier: str) -> str:
    """Quote a SQLite identifier discovered from sqlite_master."""
    return '"' + identifier.replace('"', '""') + '"'


def _tables_with_user_id(conn: sqlite3.Connection) -> list[str]:
    tables = []
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        table = row[0]
        columns = conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        if any(column[1] == "user_id" for column in columns):
            tables.append(table)
    return tables


def _ensure_qdrant_cleanup_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_QDRANT_CLEANUP_TABLE} (
            old_user_id       TEXT PRIMARY KEY,
            new_user_id       TEXT NOT NULL,
            memory_collection TEXT NOT NULL,
            created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _record_qdrant_cleanup(
    conn: sqlite3.Connection,
    old_user_id: str,
    new_user_id: str,
    memory_collection: str,
) -> None:
    _ensure_qdrant_cleanup_table(conn)
    conn.execute(
        f"""
        INSERT INTO {_QDRANT_CLEANUP_TABLE} (
            old_user_id, new_user_id, memory_collection
        ) VALUES (?, ?, ?)
        ON CONFLICT(old_user_id) DO UPDATE SET
            new_user_id = excluded.new_user_id,
            memory_collection = excluded.memory_collection,
            updated_at = CURRENT_TIMESTAMP
        """,
        (old_user_id, new_user_id, memory_collection),
    )


def _clear_qdrant_cleanup(
    conn: sqlite3.Connection,
    old_user_id: str,
    new_user_id: str,
    memory_collection: str,
) -> None:
    conn.execute(
        f"""
        DELETE FROM {_QDRANT_CLEANUP_TABLE}
        WHERE old_user_id = ? AND new_user_id = ? AND memory_collection = ?
        """,
        (old_user_id, new_user_id, memory_collection),
    )
    conn.commit()


def _retry_pending_qdrant_cleanups(conn: sqlite3.Connection) -> int:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_QDRANT_CLEANUP_TABLE,),
    ).fetchone()
    if not table_exists:
        return 0

    rows = conn.execute(
        f"""
        SELECT old_user_id, new_user_id, memory_collection
        FROM {_QDRANT_CLEANUP_TABLE}
        ORDER BY created_at, old_user_id
        """
    ).fetchall()
    cleaned = 0
    for row in rows:
        old_user_id, new_user_id, memory_collection = row[0], row[1], row[2]
        old_exists = conn.execute(
            "SELECT 1 FROM users WHERE id = ?", (old_user_id,)
        ).fetchone()
        new_exists = conn.execute(
            "SELECT 1 FROM users WHERE id = ?", (new_user_id,)
        ).fetchone()
        if old_exists or not new_exists:
            logger.warning(
                "Deferring stale Qdrant cleanup marker %s -> %s: "
                "database identity state is not committed",
                old_user_id,
                new_user_id,
            )
            continue
        try:
            did_cleanup = cleanup_qdrant_legacy_user(
                old_user_id, memory_collection=memory_collection,
            )
        except Exception as exc:
            logger.warning(
                "Could not retry legacy Qdrant cleanup for %s: %s",
                old_user_id,
                exc,
            )
            continue
        if not did_cleanup:
            logger.info(
                "Legacy Qdrant cleanup for %s remains pending until "
                "QDRANT_URL is configured",
                old_user_id,
            )
            continue
        try:
            _clear_qdrant_cleanup(
                conn, old_user_id, new_user_id, memory_collection,
            )
        except Exception as exc:
            conn.rollback()
            logger.warning(
                "Legacy Qdrant data for %s was removed, but its cleanup "
                "marker could not be cleared: %s",
                old_user_id,
                exc,
            )
            continue
        cleaned += 1
    return cleaned


def migrate_user_references(conn: sqlite3.Connection, old_user_id: str, new_user_id: str) -> int:
    """Rewrite every discovered ``user_id`` column in one connection."""
    if old_user_id == new_user_id:
        return 0
    changed = 0
    for table in _tables_with_user_id(conn):
        cursor = conn.execute(
            f"UPDATE {_quote_identifier(table)} SET user_id = ? WHERE user_id = ?",
            (new_user_id, old_user_id),
        )
        changed += max(cursor.rowcount, 0)
    return changed


def _user_data_roots(user_id: str) -> tuple:
    return (
        settings.user_data_dir(user_id),
        settings.base_dir / "data" / "qa_notes" / user_id,
    )


def _preflight_tree_merge(source, target) -> None:
    if source.is_symlink():
        raise RuntimeError(f"Refusing to migrate symlinked user data: {source}")
    if source.is_dir():
        if target.exists() and (target.is_symlink() or not target.is_dir()):
            raise RuntimeError(f"User data type conflict at {target}")
        for child in source.iterdir():
            _preflight_tree_merge(child, target / child.name)
        return
    if not source.is_file():
        raise RuntimeError(f"Refusing to migrate unsupported user data: {source}")
    if not target.exists():
        return
    if target.is_symlink() or not target.is_file():
        raise RuntimeError(f"User data type conflict at {target}")
    if not filecmp.cmp(source, target, shallow=False):
        raise RuntimeError(f"User data content conflict at {target}")


def _copy_missing_tree(source, target, created: list) -> None:
    if target.exists():
        if source.is_dir():
            for child in source.iterdir():
                _copy_missing_tree(child, target / child.name, created)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    created.append(target)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _rollback_merged_paths(created: list) -> None:
    for path in reversed(created):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError as exc:
            logger.error("Could not roll back migrated user data %s: %s", path, exc)


def _merge_user_data(old_user_id: str, new_user_id: str) -> list:
    """Merge non-conflicting files and return paths created by this call."""
    pairs = [
        (source, target)
        for source, target in zip(
            _user_data_roots(old_user_id), _user_data_roots(new_user_id)
        )
        if source.exists()
    ]
    for source, target in pairs:
        if not source.is_dir() or source.is_symlink():
            raise RuntimeError(f"Refusing to migrate non-directory user data: {source}")
        _preflight_tree_merge(source, target)

    created = []
    try:
        for source, target in pairs:
            _copy_missing_tree(source, target, created)
    except Exception:
        _rollback_merged_paths(created)
        raise
    return created


def _remove_user_data(user_id: str) -> None:
    for path in _user_data_roots(user_id):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError as exc:
            logger.warning("Could not remove migrated legacy data %s: %s", path, exc)


def migrate_user_id(conn: sqlite3.Connection, old_user_id: str, new_user_id: str) -> int:
    """Migrate DB, files, and configured vector storage without data loss."""
    if old_user_id == new_user_id:
        return 0
    source = conn.execute("SELECT id FROM users WHERE id = ?", (old_user_id,)).fetchone()
    if not source:
        raise RuntimeError(f"Cannot migrate missing user {old_user_id!r}")
    target = conn.execute("SELECT id FROM users WHERE id = ?", (new_user_id,)).fetchone()
    if target:
        raise RuntimeError(
            f"Cannot migrate {old_user_id!r}: target user id {new_user_id!r} is already in use"
        )

    # Apply the DB updates while the transaction is open. This evaluates
    # uniqueness/foreign-key constraints before any new file is created.
    created_paths = []
    vector_migration = None
    cleanup_memory_collection = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = migrate_user_references(conn, old_user_id, new_user_id)
        cursor = conn.execute(
            "UPDATE users SET id = ? WHERE id = ?", (new_user_id, old_user_id)
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"User {old_user_id!r} disappeared during migration")
        created_paths = _merge_user_data(old_user_id, new_user_id)
        # External vector storage has no distributed transaction with SQLite.
        # Stage a verified new-id copy while retaining all old-id data; only
        # delete the old copy after the SQLite commit succeeds.
        vector_migration = prepare_qdrant_user_migration(
            old_user_id, new_user_id,
        )
        if vector_migration is not None:
            cleanup_memory_collection = vector_migration.memory_collection
            _record_qdrant_cleanup(
                conn,
                old_user_id,
                new_user_id,
                cleanup_memory_collection,
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        finally:
            _rollback_merged_paths(created_paths)
            if vector_migration is not None:
                try:
                    vector_migration.rollback()
                except Exception as exc:
                    # The old-id Qdrant data was never deleted, so even a failed
                    # cleanup cannot lose the authoritative pre-migration copy.
                    logger.error(
                        "Could not roll back staged vector data for %s -> %s: %s",
                        old_user_id,
                        new_user_id,
                        exc,
                    )
        raise

    if vector_migration is not None:
        try:
            vector_migration.finalize()
        except Exception as exc:
            # The committed marker makes this transient failure retryable on
            # every startup without ever touching the new-id namespace.
            logger.warning(
                "Could not remove legacy vector data for %s after migration: %s",
                old_user_id,
                exc,
            )
        else:
            try:
                _clear_qdrant_cleanup(
                    conn,
                    old_user_id,
                    new_user_id,
                    cleanup_memory_collection,
                )
            except Exception as exc:
                conn.rollback()
                logger.warning(
                    "Legacy vector data for %s was removed, but its cleanup "
                    "marker could not be cleared: %s",
                    old_user_id,
                    exc,
                )
    _remove_user_data(old_user_id)
    return changed



def ensure_default_user():
    """Create default user from .env config if not exists."""
    email = settings.default_email.lower().strip()
    uid = _default_user_id(email)
    conn = get_db()
    _retry_pending_qdrant_cleanups(conn)
    existing = conn.execute(
        "SELECT id, password FROM users WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        _reject_public_default_owner_password(existing["password"])
        if existing["id"] != uid:
            migrated = migrate_user_id(conn, existing["id"], uid)
            logger.warning(
                "Default user id migrated from %s to stable id %s for %s (%d rows)",
                existing["id"],
                uid,
                email,
                migrated,
            )
        _init_user_knowledge(uid)
        return
    collision = conn.execute("SELECT email FROM users WHERE id = ?", (uid,)).fetchone()
    if collision:
        raise RuntimeError(
            f"Stable default user id {uid!r} is already assigned to another account"
        )
    hashed = _hash_password(settings.default_password)
    conn.execute(
        "INSERT INTO users (id, email, password, name) VALUES (?, ?, ?, ?)",
        (uid, email, hashed, settings.default_name),
    )
    conn.commit()
    logger.info(f"Default user created: {email} ({uid})")
    _init_user_knowledge(uid)


def create_user(email: str, password: str, name: str = "", invite_code: str = "") -> dict:
    if not settings.allow_registration:
        raise HTTPException(403, "Registration is disabled")
    if settings.invite_code and invite_code.strip() != settings.invite_code:
        raise HTTPException(403, "Invalid invite code")
    email = email.lower().strip()
    if not _EMAIL_PATTERN.match(email):
        raise HTTPException(422, "Invalid email format")
    validate_new_password(password)
    uid = uuid.uuid4().hex
    hashed = _hash_password(password)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (id, email, password, name) VALUES (?, ?, ?, ?)",
            (uid, email, hashed, name.strip()[:50]),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email already registered")
    _init_user_knowledge(uid)
    return {"id": uid, "email": email, "name": name.strip()[:50], "is_owner": is_owner(uid)}


def authenticate_user(email: str, password: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
    ).fetchone()
    if not row or not _verify_password(password, row["password"]):
        return None
    _init_user_knowledge(row["id"])
    return {"id": row["id"], "email": row["email"], "name": row["name"], "is_owner": is_owner(row["id"])}


def create_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": user_id, "exp": expire},
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )


def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """FastAPI dependency — returns user_id string.

    Validates that user_id matches expected format to prevent path traversal.
    """
    try:
        payload = jwt.decode(
            cred.credentials, settings.jwt_secret, algorithms=[JWT_ALGORITHM]
        )
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(401, "Invalid token")
        # Validate user_id format to prevent path traversal attacks
        if not _USER_ID_PATTERN.match(user_id):
            logger.warning(f"Rejected token with invalid user_id format: {user_id!r}")
            raise HTTPException(401, "Invalid token")
        if get_user_by_id(user_id) is None:
            raise HTTPException(401, "Invalid or expired token")
        return user_id
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")


def is_owner(user_id: str) -> bool:
    """Whether this user_id is the configured owner/admin account."""
    return user_id == _default_user_id(settings.default_email)


def require_owner(user_id: str = Depends(get_current_user)) -> str:
    """Restrict an endpoint to the configured default/owner account.

    The AI channel pool is global (not per-user); without this, any registered
    user could rewrite everyone's LLM/embedding/ASR config or point traffic at an
    attacker-controlled endpoint. Reduces to a no-op in single-user deployments.
    """
    if not is_owner(user_id):
        raise HTTPException(403, "Owner only")
    return user_id


def get_user_by_id(user_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT id, email, name, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"], "email": row["email"], "name": row["name"],
        "created_at": row["created_at"], "is_owner": is_owner(row["id"]),
    }


def update_user_profile(user_id: str, name: str = None, email: str = None) -> dict:
    conn = get_db()
    normalized_name = name.strip()[:50] if name is not None else None
    normalized_email = email.lower().strip() if email is not None else None

    # Complete validation before starting a write transaction. This prevents a
    # rejected email from leaving a preceding name update pending on the
    # thread-local connection.
    if normalized_email is not None:
        if not _EMAIL_PATTERN.match(normalized_email):
            raise HTTPException(422, "Invalid email format")
        # The owner identity is anchored to DEFAULT_EMAIL (stable-id hash +
        # startup migration in ensure_default_user). Letting the owner move off
        # it, or anyone else onto it, would re-map owner privileges on the next
        # restart — so both directions are blocked here.
        default_email = settings.default_email.lower().strip()
        if is_owner(user_id) and normalized_email != default_email:
            raise HTTPException(400, "Owner email is fixed by DEFAULT_EMAIL in .env; change it there")
        if not is_owner(user_id) and normalized_email == default_email:
            raise HTTPException(409, "Email already in use")
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?",
            (normalized_email, user_id),
        ).fetchone()
        if existing:
            raise HTTPException(409, "Email already in use")

    assignments = []
    values = []
    if normalized_name is not None:
        assignments.append("name = ?")
        values.append(normalized_name)
    if normalized_email is not None:
        assignments.append("email = ?")
        values.append(normalized_email)

    if assignments:
        try:
            conn.execute(
                f"UPDATE users SET {', '.join(assignments)} WHERE id = ?",
                (*values, user_id),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            error_text = str(exc).lower()
            if (
                normalized_email is not None
                and (
                    getattr(exc, "sqlite_errorcode", None)
                    == sqlite3.SQLITE_CONSTRAINT_UNIQUE
                    or "unique constraint failed" in error_text
                )
            ):
                raise HTTPException(409, "Email already in use") from exc
            raise
        except Exception:
            conn.rollback()
            raise
    return get_user_by_id(user_id)


def change_user_password(user_id: str, current_password: str, new_password: str) -> bool:
    validate_new_password(new_password)
    conn = get_db()
    row = conn.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not _verify_password(current_password, row["password"]):
        return False
    hashed = _hash_password(new_password)
    conn.execute("UPDATE users SET password = ? WHERE id = ?", (hashed, user_id))
    conn.commit()
    return True
