"""One-time migration: add user_id to all tables + move files to per-user dirs.

Usage: python -m backend.migrate
"""
import shutil
import sqlite3
from pathlib import Path

from backend.config import settings
from backend.auth import _hash_password
from backend.storage.database import init_all_tables

DEFAULT_USER_ID = "default0"
DEFAULT_EMAIL = "legend@sparkoffer.local"
DEFAULT_PASSWORD = "legend"

DB_PATH = settings.db_path
DATA_DIR = settings.base_dir / "data"
USER_DIR = DATA_DIR / "users" / DEFAULT_USER_ID


def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def migrate_database():
    """Add user_id column to sessions, memory_vectors, question_embeddings."""
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}, skipping DB migration.")
        return

    conn = sqlite3.connect(str(DB_PATH))

    tables = [
        ("sessions", "user_id", f"'{DEFAULT_USER_ID}'"),
        ("memory_vectors", "user_id", f"'{DEFAULT_USER_ID}'"),
        ("question_embeddings", "user_id", f"'{DEFAULT_USER_ID}'"),
    ]

    for table, col, default in tables:
        # Check table exists
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            print(f"  Table {table} does not exist, skipping.")
            continue

        if _col_exists(conn, table, col):
            print(f"  {table}.{col} already exists, skipping.")
        else:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT DEFAULT {default}")
            print(f"  Added {table}.{col} with default={default}")

        # Create index
        idx_name = f"idx_{table}_user"
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({col})")

    conn.commit()
    conn.close()
    print("Database migration done.")


def create_default_user():
    """Create the default user account for existing data."""
    init_all_tables()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    existing = conn.execute("SELECT id FROM users WHERE id = ?", (DEFAULT_USER_ID,)).fetchone()
    if existing:
        print(f"Default user '{DEFAULT_USER_ID}' already exists, skipping.")
        conn.close()
        return

    hashed = _hash_password(DEFAULT_PASSWORD)
    conn.execute(
        "INSERT INTO users (id, email, password, name) VALUES (?, ?, ?, ?)",
        (DEFAULT_USER_ID, DEFAULT_EMAIL, hashed, "Default User"),
    )
    conn.commit()
    conn.close()
    print(f"Created default user: {DEFAULT_EMAIL} / {DEFAULT_PASSWORD}")


def _move_dir(src: Path, dst: Path):
    """Move directory contents, skipping if dst already has content."""
    if not src.exists():
        return
    if dst.exists() and any(dst.iterdir()):
        print(f"  {dst} already has content, skipping.")
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    print(f"  {src} -> {dst}")


def _move_file(src: Path, dst: Path):
    if not src.exists():
        return
    if dst.exists():
        print(f"  {dst} already exists, skipping.")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  {src} -> {dst}")


def migrate_files():
    """Copy existing data files into the default user's directory."""
    print("Migrating files to per-user directory...")

    # user_profile/ -> users/default0/profile/
    _move_dir(DATA_DIR / "user_profile", USER_DIR / "profile")

    # resume/ -> users/default0/resume/
    _move_dir(DATA_DIR / "resume", USER_DIR / "resume")

    # knowledge/ -> users/default0/knowledge/
    _move_dir(DATA_DIR / "knowledge", USER_DIR / "knowledge")

    # high_freq/ -> users/default0/high_freq/
    _move_dir(DATA_DIR / "high_freq", USER_DIR / "high_freq")

    # topics.json -> users/default0/topics.json
    _move_file(DATA_DIR / "topics.json", USER_DIR / "topics.json")

    # .index_cache/ -> users/default0/.index_cache/
    _move_dir(DATA_DIR / ".index_cache", USER_DIR / ".index_cache")

    print("File migration done.")


def migrate_weak_point_mastery():
    """Backfill per-weak-point ``mastery`` and ``attempts`` for legacy profiles.

    Idempotent — re-running is a no-op if all entries already carry the fields.
    Reuses the topic-level mastery as the initial per-WP value to avoid pretending
    we have data we don't.
    """
    import json

    users_dir = DATA_DIR / "users"
    if not users_dir.exists():
        print("  No users directory, skipping weak_point mastery backfill.")
        return

    touched = 0
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        profile_path = user_dir / "profile" / "profile.json"
        if not profile_path.exists():
            continue
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  Failed to read {profile_path}: {exc}")
            continue
        topic_mastery = profile.get("topic_mastery", {})
        changed = False
        for wp in profile.get("weak_points", []):
            if wp.get("improved"):
                continue
            if "mastery" not in wp:
                tm = topic_mastery.get(wp.get("topic", ""), {})
                wp["mastery"] = int(tm.get("score", 20))
                changed = True
            if "attempts" not in wp:
                wp["attempts"] = int(wp.get("times_seen", 1))
                changed = True
        if changed:
            profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            touched += 1
            print(f"  backfilled {user_dir.name}")

    print(f"Weak-point mastery backfill done ({touched} profile(s) updated).")


def main():
    print("=== SparkOffer Migration: Single-user -> Multi-user ===\n")

    print("[1/4] Creating default user...")
    create_default_user()

    print("\n[2/4] Migrating database...")
    migrate_database()

    print("\n[3/4] Migrating files...")
    migrate_files()

    print("\n[4/4] Backfilling weak-point mastery...")
    migrate_weak_point_mastery()

    print("\n=== Migration complete! ===")
    print(f"Default login: {DEFAULT_EMAIL} / {DEFAULT_PASSWORD}")


if __name__ == "__main__":
    main()
