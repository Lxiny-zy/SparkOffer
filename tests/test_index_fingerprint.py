from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest

import backend.channel_manager as channel_manager
import backend.indexer as indexer


@pytest.mark.parametrize(
    "payload",
    [[], "not-a-map", {"guide.md": 123}, {"guide.md": None}],
)
def test_invalid_manifest_shape_falls_back_to_full_rebuild(tmp_path, payload):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    indexer._manifest_path(cache_dir).write_text(
        json.dumps(payload), encoding="utf-8",
    )

    assert indexer._load_manifest(cache_dir) == {}


def test_resume_index_uses_reserved_namespace(monkeypatch):
    captured = {}

    def record(index_id, user_id, *, strict):
        captured.update(index_id=index_id, user_id=user_id, strict=strict)

    monkeypatch.setattr(indexer, "_invalidate_index", record)
    indexer.invalidate_resume_index("user-1", strict=True)

    assert captured == {
        "index_id": indexer._RESUME_INDEX_ID,
        "user_id": "user-1",
        "strict": True,
    }
    assert captured["index_id"] != "resume"


@pytest.mark.parametrize("topic", ["__resume__", "__RESUME__"])
def test_topic_matching_resume_namespace_is_escaped(monkeypatch, topic):
    captured = []

    def record(index_id, user_id, *, strict):
        captured.append((index_id, user_id, strict))

    monkeypatch.setattr(indexer, "_invalidate_index", record)
    indexer.invalidate_topic_index(topic, "user-1", strict=True)
    indexer.invalidate_resume_index("user-1", strict=True)

    topic_index_id, resume_index_id = captured[0][0], captured[1][0]
    assert topic_index_id != resume_index_id
    assert topic_index_id.startswith(indexer._TOPIC_INDEX_ESCAPE_PREFIX)
    assert indexer._topic_index_id(topic_index_id) != topic_index_id


def test_topic_matching_resume_namespace_uses_a_distinct_write_lock():
    acquired = threading.Event()

    def acquire_topic_lock():
        with indexer.index_mutation_lock(indexer._RESUME_INDEX_ID, "user-1"):
            acquired.set()

    with indexer.resume_index_mutation_lock("user-1"):
        worker = threading.Thread(target=acquire_topic_lock, daemon=True)
        worker.start()
        worker.join(timeout=1)

    assert acquired.is_set()


def test_index_mutation_lock_is_reentrant_for_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "_use_qdrant_kb", lambda: False)
    monkeypatch.setattr(
        type(indexer.settings),
        "user_index_cache_path",
        lambda _settings, _user_id: tmp_path,
    )
    finished = threading.Event()

    def mutate_and_invalidate():
        with indexer.index_mutation_lock("python", "user-1"):
            indexer.invalidate_topic_index("python", "user-1", strict=True)
        finished.set()

    worker = threading.Thread(target=mutate_and_invalidate, daemon=True)
    worker.start()
    worker.join(timeout=1)

    assert finished.is_set()


def test_incremental_insert_serializes_with_invalidation(tmp_path, monkeypatch):
    insert_started = threading.Event()
    allow_insert = threading.Event()
    invalidated = threading.Event()

    class StorageContext:
        @staticmethod
        def persist(*, persist_dir):
            return None

    class FakeIndex:
        storage_context = StorageContext()

        @staticmethod
        def insert(_document):
            insert_started.set()
            allow_insert.wait(timeout=2)

    monkeypatch.setattr(indexer, "build_topic_index", lambda *_args: FakeIndex())
    monkeypatch.setattr(indexer, "_embedding_fingerprint", lambda: "stable")
    monkeypatch.setattr(indexer, "_cache_set", lambda *_args: True)
    monkeypatch.setattr(indexer, "_use_qdrant_kb", lambda: False)
    monkeypatch.setattr(
        type(indexer.settings),
        "user_index_cache_path",
        lambda _settings, _user_id: tmp_path,
    )

    insert_thread = threading.Thread(
        target=indexer.incremental_insert_to_index,
        args=("python", "user-1", "new content"),
        daemon=True,
    )

    def invalidate():
        indexer.invalidate_topic_index("python", "user-1", strict=True)
        invalidated.set()

    invalidate_thread = threading.Thread(target=invalidate, daemon=True)
    insert_thread.start()
    assert insert_started.wait(timeout=1)
    invalidate_thread.start()
    time.sleep(0.05)

    try:
        assert not invalidated.is_set()
    finally:
        allow_insert.set()
        insert_thread.join(timeout=2)
        invalidate_thread.join(timeout=2)

    assert invalidated.is_set()


def test_strict_invalidation_raises_when_qdrant_delete_fails(
    tmp_path, monkeypatch,
):
    cache_dir = tmp_path / "resume"
    cache_dir.mkdir()
    indexer._save_embedding_fingerprint(cache_dir, "embed-a")

    class FailingClient:
        @staticmethod
        def collection_exists(_collection):
            return True

        @staticmethod
        def delete_collection(_collection):
            raise OSError("qdrant unavailable")

    monkeypatch.setattr(indexer, "_use_qdrant_kb", lambda: True)
    monkeypatch.setattr(indexer, "_get_qdrant_client", FailingClient)
    monkeypatch.setattr(
        type(indexer.settings),
        "user_index_cache_path",
        lambda _settings, _user_id: tmp_path,
    )

    with pytest.raises(RuntimeError, match="Qdrant collection"):
        indexer.invalidate_topic_index("resume", "user-1", strict=True)

    assert not indexer._fingerprint_path(cache_dir).exists()


def test_best_effort_invalidation_does_not_raise(tmp_path, monkeypatch):
    class FailingClient:
        @staticmethod
        def collection_exists(_collection):
            raise OSError("qdrant unavailable")

    monkeypatch.setattr(indexer, "_use_qdrant_kb", lambda: True)
    monkeypatch.setattr(indexer, "_get_qdrant_client", FailingClient)
    monkeypatch.setattr(
        type(indexer.settings),
        "user_index_cache_path",
        lambda _settings, _user_id: tmp_path,
    )

    indexer.invalidate_topic_index("missing", "user-1")


def test_embedding_fingerprint_tracks_channel_model_and_endpoint(monkeypatch):
    channel = {
        "id": "ignored-secret-free-id",
        "backend": "api",
        "api_model": "embed-a",
        "api_base": "https://embed-a.example/v1",
        "keys": ["secret-a"],
        "priority": 1,
        "enabled": True,
    }
    monkeypatch.setattr(
        channel_manager, "get_all_channels", lambda section: [channel.copy()],
    )

    original = indexer._embedding_fingerprint()
    channel["api_model"] = "embed-b"
    model_changed = indexer._embedding_fingerprint()
    channel["api_base"] = "https://embed-b.example/v1"
    endpoint_changed = indexer._embedding_fingerprint()
    channel["keys"] = ["rotated-secret"]
    key_rotated = indexer._embedding_fingerprint()

    assert original != model_changed
    assert model_changed != endpoint_changed
    assert endpoint_changed == key_rotated


def test_source_snapshot_hashes_the_exact_bytes_used_for_build(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "guide.md"
    source.write_text("snapshot-a", encoding="utf-8")

    with indexer._source_snapshot(source_dir, [".md"]) as (
        snapshot_dir, hashes,
    ):
        source.write_text("live-b", encoding="utf-8")
        assert (snapshot_dir / "guide.md").read_text(encoding="utf-8") == "snapshot-a"
        assert hashes == {
            "guide.md": hashlib.md5(b"snapshot-a").hexdigest(),
        }


def test_empty_snapshot_loads_as_no_nodes(tmp_path):
    assert indexer._load_nodes_streaming(tmp_path) == []


def test_manifest_rejects_reverted_identity_with_new_config_generation(
    tmp_path, monkeypatch,
):
    source_dir = tmp_path / "source"
    cache_dir = tmp_path / "cache"
    source_dir.mkdir()
    (source_dir / "guide.md").write_text("stable", encoding="utf-8")
    hashes = indexer._compute_file_hashes(source_dir, [".md"])

    monkeypatch.setattr(indexer, "_embedding_fingerprint", lambda: "same-identity")
    monkeypatch.setattr(indexer, "get_config_version", lambda: 2)

    assert not indexer._save_manifest_if_unchanged(
        cache_dir, source_dir, hashes, [".md"], "same-identity", 1,
    )
    assert not indexer._fingerprint_path(cache_dir).exists()


def test_manifest_writes_captured_fingerprint(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    cache_dir = tmp_path / "cache"
    source_dir.mkdir()
    (source_dir / "guide.md").write_text("stable", encoding="utf-8")
    hashes = indexer._compute_file_hashes(source_dir, [".md"])

    monkeypatch.setattr(indexer, "_embedding_fingerprint", lambda: "captured")
    monkeypatch.setattr(indexer, "get_config_version", lambda: 7)

    assert indexer._save_manifest_if_unchanged(
        cache_dir, source_dir, hashes, [".md"], "captured", 7,
    )
    assert indexer._load_embedding_fingerprint(cache_dir) == "captured"


def test_dirty_mark_prevents_older_build_from_revalidating_index(
    tmp_path, monkeypatch,
):
    source_dir = tmp_path / "source"
    cache_root = tmp_path / "cache"
    cache_dir = cache_root / "python"
    source_dir.mkdir()
    cache_dir.mkdir(parents=True)
    (source_dir / "guide.md").write_text("stable", encoding="utf-8")
    hashes = indexer._compute_file_hashes(source_dir, [".md"])

    monkeypatch.setattr(indexer, "_embedding_fingerprint", lambda: "stable")
    monkeypatch.setattr(indexer, "get_config_version", lambda: 1)
    monkeypatch.setattr(
        type(indexer.settings),
        "user_index_cache_path",
        lambda _settings, _user_id: cache_root,
    )
    indexer._save_embedding_fingerprint(cache_dir, "stable")
    build_generation = indexer._get_index_dirty_generation(cache_dir)

    indexer.mark_topic_index_dirty("python", "user-1")

    assert not indexer._fingerprint_path(cache_dir).exists()
    assert not indexer._save_manifest_if_unchanged(
        cache_dir, source_dir, hashes, [".md"], "stable", 1,
        build_generation,
    )
    assert not indexer._fingerprint_path(cache_dir).exists()


def test_memory_cache_rejects_changed_embedding_identity(monkeypatch):
    identity = {"value": "embed-a"}
    monkeypatch.setattr(
        indexer, "_embedding_fingerprint", lambda: identity["value"],
    )
    indexer.clear_index_cache()
    try:
        cached_object = object()
        assert indexer._cache_set(("user", "topic"), cached_object, "embed-a")
        assert indexer._cache_get(("user", "topic")) is cached_object

        identity["value"] = "embed-b"
        assert indexer._cache_get(("user", "topic")) is None
    finally:
        indexer.clear_index_cache()


def test_local_loader_rejects_stale_same_dimension_index(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    source_dir = tmp_path / "source"
    cache_dir.mkdir()
    source_dir.mkdir()
    indexer._save_embedding_fingerprint(cache_dir, "embed-a")
    monkeypatch.setattr(indexer, "_embedding_fingerprint", lambda: "embed-b")

    with pytest.raises(indexer.IndexNotReady, match="identity mismatch"):
        indexer._build_or_load_local_index(
            cache_dir, source_dir, force_rebuild=False, build_if_missing=False,
        )


def test_qdrant_loader_rejects_stale_same_dimension_index(
    tmp_path, monkeypatch,
):
    qdrant_module = pytest.importorskip("llama_index.vector_stores.qdrant")

    class FakeVectorStore:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeClient:
        @staticmethod
        def collection_exists(collection):
            return True

    cache_dir = tmp_path / "manifest"
    source_dir = tmp_path / "source"
    cache_dir.mkdir()
    source_dir.mkdir()
    indexer._save_embedding_fingerprint(cache_dir, "embed-a")

    monkeypatch.setattr(qdrant_module, "QdrantVectorStore", FakeVectorStore)
    monkeypatch.setattr(indexer, "_get_qdrant_client", lambda: FakeClient())
    monkeypatch.setattr(indexer, "_qdrant_collection_dim", lambda *args: 1024)
    monkeypatch.setattr(indexer, "_embedding_fingerprint", lambda: "embed-b")

    with pytest.raises(indexer.IndexNotReady, match="identity mismatch"):
        indexer._build_or_load_qdrant_index(
            "collection", source_dir, force_rebuild=False,
            build_if_missing=False, manifest_dir=cache_dir,
        )
