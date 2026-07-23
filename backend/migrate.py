"""One-time migration: add user_id to all tables + move files to per-user dirs.

Usage: python -m backend.migrate
"""
import shutil
import sqlite3
from pathlib import Path

from backend.config import settings
from backend.auth import (
    _copy_missing_tree,
    _default_user_id,
    _merge_user_data,
    _preflight_tree_merge,
    _quote_identifier,
    _rollback_merged_paths,
    _remove_user_data,
    _tables_with_user_id,
    ensure_default_user,
    migrate_user_references,
)
from backend.storage.database import init_all_tables

LEGACY_DEFAULT_USER_ID = "default0"
DEFAULT_EMAIL = settings.default_email.lower().strip()
DEFAULT_USER_ID = _default_user_id(DEFAULT_EMAIL)

DB_PATH = settings.db_path
DATA_DIR = settings.base_dir / "data"
USER_DIR = DATA_DIR / "users" / DEFAULT_USER_ID


def migrate_database():
    """Assign all legacy and unowned rows to the configured default user."""
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}, skipping DB migration.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = migrate_user_references(
            conn, LEGACY_DEFAULT_USER_ID, DEFAULT_USER_ID
        )
        # Only these columns were introduced as nullable fields by the legacy
        # single-user schema. Nullable audit_logs.user_id intentionally keeps
        # anonymous events anonymous.
        legacy_nullable_tables = {"sessions", "memory_vectors", "question_embeddings"}
        for table in _tables_with_user_id(conn):
            if table not in legacy_nullable_tables:
                continue
            cursor = conn.execute(
                f"UPDATE {_quote_identifier(table)} SET user_id = ? "
                "WHERE user_id IS NULL OR user_id = ''",
                (DEFAULT_USER_ID,),
            )
            changed += max(cursor.rowcount, 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Database migration done ({changed} row(s) assigned to {DEFAULT_USER_ID}).")


def create_default_user():
    """Create or migrate the configured default user account."""
    init_all_tables()
    ensure_default_user()
    print(f"Default user ready: {DEFAULT_EMAIL} ({DEFAULT_USER_ID})")


def _move_dir(src: Path, dst: Path):
    """Merge directory contents without discarding an existing target."""
    if not src.exists():
        return
    if src.is_symlink() or not src.is_dir():
        raise RuntimeError(f"Refusing to migrate non-directory data: {src}")
    _preflight_tree_merge(src, dst)
    created = []
    try:
        _copy_missing_tree(src, dst, created)
    except Exception:
        _rollback_merged_paths(created)
        raise
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
    """Copy global and legacy per-user files into the stable user directory."""
    print("Migrating files to per-user directory...")

    # Merge both user-scoped roots before removing the legacy copy. Existing
    # target files are retained unless the source contains the same path.
    _merge_user_data(LEGACY_DEFAULT_USER_ID, DEFAULT_USER_ID)
    _remove_user_data(LEGACY_DEFAULT_USER_ID)

    # user_profile/ -> users/<stable-id>/profile/
    _move_dir(DATA_DIR / "user_profile", USER_DIR / "profile")

    # resume/ -> users/<stable-id>/resume/
    _move_dir(DATA_DIR / "resume", USER_DIR / "resume")

    # knowledge/ -> users/<stable-id>/knowledge/
    _move_dir(DATA_DIR / "knowledge", USER_DIR / "knowledge")

    # high_freq/ -> users/<stable-id>/high_freq/
    _move_dir(DATA_DIR / "high_freq", USER_DIR / "high_freq")

    # topics.json -> users/<stable-id>/topics.json
    _move_file(DATA_DIR / "topics.json", USER_DIR / "topics.json")

    # .index_cache/ -> users/<stable-id>/.index_cache/
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
    settings.validate_security_settings()
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
    print(f"Default account: {DEFAULT_EMAIL} ({DEFAULT_USER_ID})")


if __name__ == "__main__":
    main()
