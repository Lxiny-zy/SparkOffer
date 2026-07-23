import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest
import qdrant_client
from qdrant_client import QdrantClient
from qdrant_client import models as qdrant_models
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from backend import auth
from backend.user_vector_migration import (
    QdrantUserMigration,
    prepare_qdrant_user_migration,
)
from backend.vector_store.qdrant_store import make_point_id


OLD_USER_ID = "01234567"
NEW_USER_ID = "0123456789abcdef0123456789abcdef"
MEMORY_COLLECTION = "test_memory"


def _user_filter(user_id: str) -> Filter:
    return Filter(
        must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
    )


def _memory_points(
    client: QdrantClient,
    user_id: str,
    collection: str = MEMORY_COLLECTION,
):
    return client.scroll(
        collection,
        scroll_filter=_user_filter(user_id),
        limit=100,
        with_payload=True,
        with_vectors=True,
    )[0]


def _seed_qdrant(client: QdrantClient) -> tuple[str, str]:
    client.create_collection(
        MEMORY_COLLECTION,
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )
    client.upsert(
        MEMORY_COLLECTION,
        points=[
            PointStruct(
                id=make_point_id(
                    OLD_USER_ID, "session-1", "session_summary", "legacy memory",
                ),
                vector=[1.0, 0.0, 0.0],
                payload={
                    "user_id": OLD_USER_ID,
                    "session_id": "session-1",
                    "chunk_type": "session_summary",
                    "content": "legacy memory",
                    "topic": "python",
                    "created_at": "2026-01-01T00:00:00",
                    "custom": "preserved",
                },
            ),
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.0, 1.0, 0.0],
                payload={
                    "user_id": NEW_USER_ID,
                    "session_id": "orphan",
                    "chunk_type": "insight",
                    "content": "partial previous attempt",
                    "created_at": "2026-01-02T00:00:00",
                },
            ),
        ],
    )

    old_collection = f"kb_{OLD_USER_ID}_python"
    new_collection = f"kb_{NEW_USER_ID}_python"
    vector_config = {
        "text-dense": VectorParams(size=3, distance=Distance.COSINE)
    }
    client.create_collection(old_collection, vectors_config=vector_config)
    old_path = rf"D:\data\users\{OLD_USER_ID}\knowledge\python\guide.md"
    node = {
        "id_": "node-1",
        "metadata": {"file_path": old_path, "user_id": OLD_USER_ID},
        "text": "knowledge",
        "embedding": None,
    }
    client.upsert(
        old_collection,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector={"text-dense": [0.0, 0.0, 1.0]},
                payload={
                    "file_path": old_path,
                    "_node_content": json.dumps(node),
                    "_node_type": "TextNode",
                },
            )
        ],
    )

    legacy_resume_collection = f"kb_{OLD_USER_ID}_resume"
    client.create_collection(
        legacy_resume_collection,
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )
    client.upsert(
        legacy_resume_collection,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0, 0.0, 0.0],
                payload={"text": "ambiguous resume/topic legacy point"},
            )
        ],
    )

    # Before the reserved namespace was introduced, a user Topic named
    # ``__resume__`` occupied this exact suffix. Copying it verbatim would make
    # topic content visible through the new user's resume retriever.
    legacy_reserved_topic = f"kb_{OLD_USER_ID}___resume__"
    client.create_collection(
        legacy_reserved_topic,
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )
    client.upsert(
        legacy_reserved_topic,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.0, 1.0, 0.0],
                payload={"text": "topic content must never become resume content"},
            )
        ],
    )

    legacy_escape_topic = f"kb_{OLD_USER_ID}___topic_index__custom"
    client.create_collection(
        legacy_escape_topic,
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )
    client.upsert(
        legacy_escape_topic,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.0, 0.0, 1.0],
                payload={"text": "legacy escape-prefix topic"},
            )
        ],
    )

    # A crashed earlier attempt may have left an incompatible target. The DB
    # precondition says NEW_USER_ID has no owner, so prepare must replace it.
    client.create_collection(
        new_collection,
        vectors_config={
            "text-dense": VectorParams(size=2, distance=Distance.DOT)
        },
    )
    client.upsert(
        new_collection,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector={"text-dense": [1.0, 1.0]},
                payload={"text": "orphan"},
            )
        ],
    )
    return old_collection, new_collection


def test_qdrant_user_migration_is_idempotent_and_preserves_old_until_finalize():
    client = QdrantClient(":memory:")
    old_collection, new_collection = _seed_qdrant(client)

    first = QdrantUserMigration(
        client, MEMORY_COLLECTION, OLD_USER_ID, NEW_USER_ID,
    ).prepare()

    assert first.memory_point_count == 1
    assert first.knowledge_collections == (new_collection,)
    assert len(_memory_points(client, OLD_USER_ID)) == 1
    migrated_memory = _memory_points(client, NEW_USER_ID)
    assert len(migrated_memory) == 1
    assert migrated_memory[0].id == make_point_id(
        NEW_USER_ID, "session-1", "session_summary", "legacy memory",
    )
    assert migrated_memory[0].payload["user_id"] == NEW_USER_ID
    assert migrated_memory[0].payload["custom"] == "preserved"

    migrated_kb = client.scroll(
        new_collection, limit=100, with_payload=True, with_vectors=True,
    )[0]
    assert len(migrated_kb) == 1
    assert NEW_USER_ID in migrated_kb[0].payload["file_path"]
    assert f"users\\{OLD_USER_ID}\\" not in migrated_kb[0].payload["file_path"]
    node = json.loads(migrated_kb[0].payload["_node_content"])
    assert node["metadata"]["user_id"] == NEW_USER_ID
    assert NEW_USER_ID in node["metadata"]["file_path"]
    assert client.collection_exists(old_collection)
    assert client.collection_exists(f"kb_{OLD_USER_ID}_resume")
    assert not client.collection_exists(f"kb_{NEW_USER_ID}_resume")
    assert client.collection_exists(f"kb_{OLD_USER_ID}___resume__")
    assert not client.collection_exists(f"kb_{NEW_USER_ID}___resume__")
    assert client.collection_exists(f"kb_{OLD_USER_ID}___topic_index__custom")
    assert not client.collection_exists(
        f"kb_{NEW_USER_ID}___topic_index__custom"
    )

    ids_before = [point.id for point in migrated_memory]
    second = QdrantUserMigration(
        client, MEMORY_COLLECTION, OLD_USER_ID, NEW_USER_ID,
    ).prepare()
    assert [point.id for point in _memory_points(client, NEW_USER_ID)] == ids_before
    assert client.count(new_collection, exact=True).count == 1

    second.finalize()
    assert _memory_points(client, OLD_USER_ID) == []
    assert len(_memory_points(client, NEW_USER_ID)) == 1
    assert not client.collection_exists(old_collection)
    assert not client.collection_exists(f"kb_{OLD_USER_ID}_resume")
    assert not client.collection_exists(f"kb_{OLD_USER_ID}___resume__")
    assert not client.collection_exists(
        f"kb_{OLD_USER_ID}___topic_index__custom"
    )
    assert client.collection_exists(new_collection)


class _FailingKnowledgeUpsertClient:
    def __init__(self, client: QdrantClient, failing_collection: str):
        self._client = client
        self._failing_collection = failing_collection

    def __getattr__(self, name):
        return getattr(self._client, name)

    def upsert(self, collection_name, *args, **kwargs):
        if collection_name == self._failing_collection:
            raise OSError("injected knowledge copy failure")
        return self._client.upsert(collection_name, *args, **kwargs)


def test_qdrant_prepare_failure_removes_target_and_retains_all_old_data():
    real_client = QdrantClient(":memory:")
    old_collection, new_collection = _seed_qdrant(real_client)
    client = _FailingKnowledgeUpsertClient(real_client, new_collection)

    with pytest.raises(OSError, match="injected knowledge copy failure"):
        QdrantUserMigration(
            client, MEMORY_COLLECTION, OLD_USER_ID, NEW_USER_ID,
        ).prepare()

    assert len(_memory_points(real_client, OLD_USER_ID)) == 1
    assert _memory_points(real_client, NEW_USER_ID) == []
    assert real_client.collection_exists(old_collection)
    assert real_client.count(old_collection, exact=True).count == 1
    assert not real_client.collection_exists(new_collection)


class _MigrationProbe:
    def __init__(self):
        self.rolled_back = False
        self.finalized = False
        self.memory_collection = MEMORY_COLLECTION

    def rollback(self):
        self.rolled_back = True

    def finalize(self):
        self.finalized = True


def test_sqlite_commit_failure_rolls_back_staged_qdrant(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute("CREATE TABLE allowed_ids (id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE records (user_id TEXT REFERENCES allowed_ids(id) "
        "DEFERRABLE INITIALLY DEFERRED)"
    )
    conn.execute("INSERT INTO users VALUES (?, ?)", (OLD_USER_ID, "owner@example.com"))
    conn.execute("INSERT INTO allowed_ids VALUES (?)", (OLD_USER_ID,))
    conn.execute("INSERT INTO records VALUES (?)", (OLD_USER_ID,))
    conn.commit()
    probe = _MigrationProbe()
    monkeypatch.setattr(
        auth, "prepare_qdrant_user_migration", lambda *_args: probe,
    )

    with pytest.raises(sqlite3.IntegrityError):
        auth.migrate_user_id(conn, OLD_USER_ID, NEW_USER_ID)

    assert probe.rolled_back is True
    assert probe.finalized is False
    assert conn.execute("SELECT id FROM users").fetchone()[0] == OLD_USER_ID
    assert conn.execute("SELECT user_id FROM records").fetchone()[0] == OLD_USER_ID
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?",
        (auth._QDRANT_CLEANUP_TABLE,),
    ).fetchone() is None


def test_qdrant_prepare_error_rolls_back_sqlite_and_copied_files(
    monkeypatch, tmp_path,
):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute("CREATE TABLE records (user_id TEXT)")
    conn.execute("INSERT INTO users VALUES (?, ?)", (OLD_USER_ID, "owner@example.com"))
    conn.execute("INSERT INTO records VALUES (?)", (OLD_USER_ID,))
    conn.commit()
    old_dir = tmp_path / "data" / "users" / OLD_USER_ID
    old_dir.mkdir(parents=True)
    (old_dir / "profile.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(auth.settings, "base_dir", tmp_path)

    def fail_prepare(*_args):
        raise OSError("qdrant unavailable during copy")

    monkeypatch.setattr(auth, "prepare_qdrant_user_migration", fail_prepare)

    with pytest.raises(OSError, match="qdrant unavailable"):
        auth.migrate_user_id(conn, OLD_USER_ID, NEW_USER_ID)

    assert conn.execute("SELECT id FROM users").fetchone()[0] == OLD_USER_ID
    assert conn.execute("SELECT user_id FROM records").fetchone()[0] == OLD_USER_ID
    assert (old_dir / "profile.json").exists()
    assert not (tmp_path / "data" / "users" / NEW_USER_ID).exists()


def test_successful_sqlite_commit_finalizes_staged_qdrant(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute("CREATE TABLE records (user_id TEXT)")
    conn.execute("INSERT INTO users VALUES (?, ?)", (OLD_USER_ID, "owner@example.com"))
    conn.execute("INSERT INTO records VALUES (?)", (OLD_USER_ID,))
    conn.commit()
    probe = _MigrationProbe()
    monkeypatch.setattr(
        auth, "prepare_qdrant_user_migration", lambda *_args: probe,
    )

    changed = auth.migrate_user_id(conn, OLD_USER_ID, NEW_USER_ID)

    assert changed == 1
    assert probe.rolled_back is False
    assert probe.finalized is True
    assert conn.execute("SELECT id FROM users").fetchone()[0] == NEW_USER_ID
    assert conn.execute("SELECT user_id FROM records").fetchone()[0] == NEW_USER_ID
    assert conn.execute(
        f"SELECT COUNT(*) FROM {auth._QDRANT_CLEANUP_TABLE}"
    ).fetchone()[0] == 0


def test_finalize_cleanup_error_keeps_committed_new_identity(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute("CREATE TABLE records (user_id TEXT)")
    conn.execute("INSERT INTO users VALUES (?, ?)", (OLD_USER_ID, "owner@example.com"))
    conn.execute("INSERT INTO records VALUES (?)", (OLD_USER_ID,))
    conn.commit()
    probe = _MigrationProbe()

    def fail_finalize():
        probe.finalized = True
        raise OSError("old qdrant cleanup failed")

    probe.finalize = fail_finalize
    monkeypatch.setattr(
        auth, "prepare_qdrant_user_migration", lambda *_args: probe,
    )

    changed = auth.migrate_user_id(conn, OLD_USER_ID, NEW_USER_ID)

    assert changed == 1
    assert probe.rolled_back is False
    assert probe.finalized is True
    assert conn.execute("SELECT id FROM users").fetchone()[0] == NEW_USER_ID
    assert conn.execute("SELECT user_id FROM records").fetchone()[0] == NEW_USER_ID
    marker = conn.execute(
        f"""
        SELECT old_user_id, new_user_id, memory_collection
        FROM {auth._QDRANT_CLEANUP_TABLE}
        """
    ).fetchone()
    assert marker == (OLD_USER_ID, NEW_USER_ID, MEMORY_COLLECTION)


@pytest.mark.parametrize(
    "memory_collection",
    [f"kb_{OLD_USER_ID}_memory", f"kb_{NEW_USER_ID}_memory"],
)
def test_memory_collection_is_never_treated_as_a_kb_namespace(memory_collection):
    client = QdrantClient(":memory:")
    other_user_id = "aaaaaaaa"
    client.create_collection(
        memory_collection,
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )
    client.upsert(
        memory_collection,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0, 0.0, 0.0],
                payload={
                    "user_id": OLD_USER_ID,
                    "session_id": "old-session",
                    "chunk_type": "insight",
                    "content": "old memory",
                },
            ),
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.0, 1.0, 0.0],
                payload={
                    "user_id": NEW_USER_ID,
                    "session_id": "orphan",
                    "chunk_type": "insight",
                    "content": "stale target",
                },
            ),
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.0, 0.0, 1.0],
                payload={
                    "user_id": other_user_id,
                    "session_id": "other-session",
                    "chunk_type": "insight",
                    "content": "other user's memory",
                },
            ),
        ],
    )

    migration = QdrantUserMigration(
        client, memory_collection, OLD_USER_ID, NEW_USER_ID,
    ).prepare()

    assert client.collection_exists(memory_collection)
    assert len(_memory_points(client, OLD_USER_ID, memory_collection)) == 1
    assert len(_memory_points(client, NEW_USER_ID, memory_collection)) == 1
    assert len(_memory_points(client, other_user_id, memory_collection)) == 1

    migration.finalize()
    assert client.collection_exists(memory_collection)
    assert _memory_points(client, OLD_USER_ID, memory_collection) == []
    assert len(_memory_points(client, NEW_USER_ID, memory_collection)) == 1
    assert len(_memory_points(client, other_user_id, memory_collection)) == 1


def test_same_id_class_and_factory_are_complete_noops(monkeypatch):
    client = QdrantClient(":memory:")
    kb_collection = f"kb_{OLD_USER_ID}_python"
    client.create_collection(
        MEMORY_COLLECTION,
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )
    client.create_collection(
        kb_collection,
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )
    client.upsert(
        MEMORY_COLLECTION,
        points=[PointStruct(
            id=str(uuid.uuid4()),
            vector=[1.0, 0.0, 0.0],
            payload={"user_id": OLD_USER_ID},
        )],
    )
    client.upsert(
        kb_collection,
        points=[PointStruct(
            id=str(uuid.uuid4()),
            vector=[0.0, 1.0, 0.0],
            payload={"text": "knowledge"},
        )],
    )

    migration = QdrantUserMigration(
        client, MEMORY_COLLECTION, OLD_USER_ID, OLD_USER_ID,
    )
    assert migration.prepare() is migration
    migration.rollback()
    migration.finalize()

    assert migration.prepared is False
    assert client.count(MEMORY_COLLECTION, exact=True).count == 1
    assert client.count(kb_collection, exact=True).count == 1

    monkeypatch.setattr(auth.settings, "vector_backend", "qdrant")
    monkeypatch.setattr(auth.settings, "qdrant_url", "")
    assert prepare_qdrant_user_migration(OLD_USER_ID, OLD_USER_ID) is None


def test_qdrant_backend_requires_url_for_nontrivial_migration(monkeypatch):
    monkeypatch.setattr(auth.settings, "vector_backend", "qdrant")
    monkeypatch.setattr(auth.settings, "qdrant_url", "")

    with pytest.raises(RuntimeError, match="requires QDRANT_URL"):
        prepare_qdrant_user_migration(OLD_USER_ID, NEW_USER_ID)


def test_qdrant_url_triggers_migration_even_in_numpy_mode(monkeypatch):
    created_clients = []

    class ProbeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created_clients.append(self)

        def get_collections(self):
            return SimpleNamespace(collections=[])

        def collection_exists(self, _collection):
            return False

    monkeypatch.setattr(qdrant_client, "QdrantClient", ProbeClient)
    monkeypatch.setattr(auth.settings, "vector_backend", "numpy")
    monkeypatch.setattr(auth.settings, "qdrant_url", "http://qdrant.example:6333")

    migration = prepare_qdrant_user_migration(OLD_USER_ID, NEW_USER_ID)

    assert migration is not None and migration.prepared is True
    assert created_clients[0].kwargs["url"] == "http://qdrant.example:6333"


class _CollectionConfigClient:
    def __init__(self, info):
        self.info = info
        self.created = None
        self.updated = None

    def get_collection(self, _collection):
        return self.info

    def create_collection(
        self,
        collection_name,
        vectors_config=None,
        sparse_vectors_config=None,
        shard_number=None,
        sharding_method=None,
        replication_factor=None,
        write_consistency_factor=None,
        on_disk_payload=None,
        hnsw_config=None,
        optimizers_config=None,
        wal_config=None,
        quantization_config=None,
        metadata=None,
    ):
        self.created = {
            key: value for key, value in locals().items()
            if key not in {"self", "collection_name"} and value is not None
        }

    def create_payload_index(self, **_kwargs):
        return None

    def scroll(self, *_args, **_kwargs):
        return [], None

    def count(self, *_args, **_kwargs):
        return SimpleNamespace(count=0)

    def update_collection(
        self,
        _collection_name,
        collection_params=None,
        strict_mode_config=None,
        metadata=None,
    ):
        self.updated = {
            key: value for key, value in locals().items()
            if key not in {"self", "_collection_name"} and value is not None
        }


def test_knowledge_copy_preserves_collection_configuration():
    quantization = object()
    sparse_vectors = {"text-sparse": object()}
    params = SimpleNamespace(
        vectors=VectorParams(size=3, distance=Distance.COSINE),
        sparse_vectors=sparse_vectors,
        shard_number=4,
        sharding_method="auto",
        replication_factor=3,
        write_consistency_factor=2,
        read_fan_out_factor=2,
        read_fan_out_delay_ms=25,
        on_disk_payload=False,
    )
    strict_mode = (
        SimpleNamespace(enabled=True, max_query_limit=50)
        if hasattr(qdrant_models, "StrictModeConfig")
        else None
    )
    config = SimpleNamespace(
        params=params,
        hnsw_config=SimpleNamespace(m=8, ef_construct=64, on_disk=True),
        optimizer_config=SimpleNamespace(
            indexing_threshold=1234, flush_interval_sec=7,
        ),
        wal_config=SimpleNamespace(wal_capacity_mb=64, wal_segments_ahead=2),
        quantization_config=quantization,
        strict_mode_config=strict_mode,
        metadata={"embedding": "fixture-v1"},
    )
    client = _CollectionConfigClient(
        SimpleNamespace(config=config, payload_schema={})
    )
    migration = QdrantUserMigration(
        client, MEMORY_COLLECTION, OLD_USER_ID, NEW_USER_ID,
    )

    migration._copy_knowledge_collection(
        f"kb_{OLD_USER_ID}_python", f"kb_{NEW_USER_ID}_python",
    )

    assert client.created["vectors_config"] == params.vectors
    assert client.created["sparse_vectors_config"] == sparse_vectors
    assert client.created["shard_number"] == 4
    assert client.created["sharding_method"] == "auto"
    assert client.created["replication_factor"] == 3
    assert client.created["write_consistency_factor"] == 2
    assert client.created["on_disk_payload"] is False
    assert client.created["hnsw_config"].m == 8
    assert client.created["optimizers_config"].indexing_threshold == 1234
    assert client.created["wal_config"].wal_capacity_mb == 64
    assert client.created["quantization_config"] is quantization
    assert client.created["metadata"] == {"embedding": "fixture-v1"}
    assert client.updated["collection_params"].read_fan_out_factor == 2
    if "read_fan_out_delay_ms" in qdrant_models.CollectionParamsDiff.model_fields:
        assert client.updated["collection_params"].read_fan_out_delay_ms == 25
    if strict_mode is not None:
        assert client.updated["strict_mode_config"].enabled is True


def test_pending_cleanup_retries_on_startup_and_only_purges_old_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE, password TEXT)"
    )
    conn.execute(
        "INSERT INTO users VALUES (?, ?, ?)",
        (
            NEW_USER_ID,
            "owner@example.com",
            auth._hash_password("a-strong-existing-password"),
        ),
    )
    auth._record_qdrant_cleanup(
        conn, OLD_USER_ID, NEW_USER_ID, MEMORY_COLLECTION,
    )
    conn.commit()
    cleanup_calls = []

    def cleanup(old_user_id, *, memory_collection):
        cleanup_calls.append((old_user_id, memory_collection))
        return True

    monkeypatch.setattr(auth, "get_db", lambda: conn)
    monkeypatch.setattr(auth, "_default_user_id", lambda _email: NEW_USER_ID)
    monkeypatch.setattr(auth, "_init_user_knowledge", lambda _user_id: None)
    monkeypatch.setattr(auth, "cleanup_qdrant_legacy_user", cleanup)
    monkeypatch.setattr(auth.settings, "default_email", "owner@example.com")

    auth.ensure_default_user()

    assert cleanup_calls == [(OLD_USER_ID, MEMORY_COLLECTION)]
    assert conn.execute(
        f"SELECT COUNT(*) FROM {auth._QDRANT_CLEANUP_TABLE}"
    ).fetchone()[0] == 0


def test_existing_owner_public_default_password_blocks_production_startup(
    monkeypatch,
):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE, password TEXT)"
    )
    conn.execute(
        "INSERT INTO users VALUES (?, ?, ?)",
        (NEW_USER_ID, "owner@example.com", auth._hash_password("legend")),
    )
    conn.commit()

    monkeypatch.setattr(auth, "get_db", lambda: conn)
    monkeypatch.setattr(auth, "_default_user_id", lambda _email: NEW_USER_ID)
    monkeypatch.setattr(auth, "_init_user_knowledge", lambda _user_id: None)
    monkeypatch.setattr(auth.settings, "default_email", "owner@example.com")
    monkeypatch.setattr(auth.settings, "app_env", "production")

    with pytest.raises(RuntimeError, match="public default password"):
        auth.ensure_default_user()
