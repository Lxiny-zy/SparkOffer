"""Failure-safe vector storage migration for a changed user id.

SQLite/numpy vectors are migrated with the rest of the ``user_id`` columns in
``backend.auth``.  Qdrant needs separate handling because memory records live in
a shared collection while knowledge indexes use the user id in their collection
name.  This module stages a complete copy under the new id, retaining every old
point until the caller has committed its SQLite transaction.
"""
from __future__ import annotations

import json
import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any

from backend.config import settings

logger = logging.getLogger("uvicorn")

_SCROLL_BATCH = 256
_KB_COLLECTION_PREFIX = "kb_"
_LEGACY_AMBIGUOUS_RESUME_SUFFIX = "resume"
_RESERVED_RESUME_SUFFIX = "__resume__"
_TOPIC_INDEX_ESCAPE_PREFIX = "__topic_index__"


def _method_supports_parameter(method: Any, name: str) -> bool:
    """Return whether an installed Qdrant client explicitly supports an option."""
    try:
        return name in inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False


def _model_values(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(exclude_none=True)
    return {
        key: child for key, child in vars(value).items()
        if child is not None
    }


def _qdrant_diff(model_name: str, value: Any) -> Any:
    """Convert a full collection config model to its create/update diff model."""
    if value is None:
        return None
    from qdrant_client import models

    model_class = getattr(models, model_name)
    fields = getattr(model_class, "model_fields", None)
    if fields is None:
        fields = getattr(model_class, "__fields__", {})
    values = {
        key: child for key, child in _model_values(value).items()
        if key in fields
    }
    return model_class(**values)


def _requires_source_rebuild(suffix: str) -> bool:
    """Return whether a KB namespace cannot be classified from its name alone.

    Older releases allowed topic keys to occupy names now reserved for the
    isolated resume index and escaped topic ids. Their source files migrate with
    the user directory, so rebuilding is safer than copying a collection into
    the wrong semantic namespace.
    """
    folded = suffix.casefold()
    return (
        folded in {
            _LEGACY_AMBIGUOUS_RESUME_SUFFIX,
            _RESERVED_RESUME_SUFFIX,
        }
        or folded.startswith(_TOPIC_INDEX_ESCAPE_PREFIX)
    )


def _collection_names(client: Any) -> list[str]:
    response = client.get_collections()
    return sorted(collection.name for collection in response.collections)


def _user_filter(user_id: str):
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    return Filter(
        must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
    )


def _delete_user_points(client: Any, collection: str, user_id: str) -> None:
    from qdrant_client.models import FilterSelector

    if client.collection_exists(collection):
        client.delete(
            collection,
            points_selector=FilterSelector(filter=_user_filter(user_id)),
            wait=True,
        )


def _kb_prefix(user_id: str) -> str:
    # User ids are validated hex strings, so this is identical to indexer's
    # collection-name sanitizer and cannot match another user's prefix.
    return f"{_KB_COLLECTION_PREFIX}{user_id}_"


def _replace_user_path(value: str, old_user_id: str, new_user_id: str) -> str:
    pattern = rf"(?<=[\\/]){re.escape(old_user_id)}(?=[\\/])"
    return re.sub(pattern, new_user_id, value)


def _rewrite_metadata_paths(value: Any, old_user_id: str, new_user_id: str) -> Any:
    if isinstance(value, dict):
        rewritten = {}
        for key, child in value.items():
            folded = str(key).casefold()
            if folded == "user_id" and child == old_user_id:
                rewritten[key] = new_user_id
            elif isinstance(child, str) and "path" in folded:
                rewritten[key] = _replace_user_path(
                    child, old_user_id, new_user_id
                )
            else:
                rewritten[key] = _rewrite_metadata_paths(
                    child, old_user_id, new_user_id
                )
        return rewritten
    if isinstance(value, list):
        return [
            _rewrite_metadata_paths(item, old_user_id, new_user_id)
            for item in value
        ]
    return value


def _rewrite_knowledge_payload(
    payload: dict | None, old_user_id: str, new_user_id: str,
) -> dict:
    rewritten = _rewrite_metadata_paths(
        dict(payload or {}), old_user_id, new_user_id
    )
    node_content = rewritten.get("_node_content")
    if isinstance(node_content, str):
        try:
            node = json.loads(node_content)
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            node = _rewrite_metadata_paths(node, old_user_id, new_user_id)
            rewritten["_node_content"] = json.dumps(
                node, ensure_ascii=False, separators=(",", ":"),
            )
    return rewritten


def _upsert_batches(client: Any, collection: str, points: list[Any]) -> None:
    for start in range(0, len(points), _SCROLL_BATCH):
        client.upsert(
            collection,
            points=points[start:start + _SCROLL_BATCH],
            wait=True,
        )


@dataclass
class QdrantUserMigration:
    """A staged Qdrant copy that can be rolled back before DB commit."""

    client: Any
    memory_collection: str
    old_user_id: str
    new_user_id: str
    memory_point_count: int = 0
    knowledge_collections: tuple[str, ...] = ()
    prepared: bool = False

    def _purge_user(self, user_id: str) -> None:
        failures: list[tuple[str, Exception]] = []
        try:
            _delete_user_points(
                self.client, self.memory_collection, user_id,
            )
        except Exception as exc:
            failures.append(("memory points", exc))

        prefix = _kb_prefix(user_id)
        try:
            collections = _collection_names(self.client)
        except Exception as exc:
            failures.append(("knowledge collection listing", exc))
            collections = []
        for collection in collections:
            if (
                collection == self.memory_collection
                or not collection.startswith(prefix)
            ):
                continue
            try:
                self.client.delete_collection(collection)
            except Exception as exc:
                failures.append((f"knowledge collection {collection}", exc))

        if failures:
            details = "; ".join(f"{target}: {exc}" for target, exc in failures)
            raise RuntimeError(
                f"Could not purge Qdrant data for user {user_id}: {details}"
            ) from failures[0][1]

    def _copy_memory(self) -> int:
        if not self.client.collection_exists(self.memory_collection):
            return 0

        from qdrant_client.models import PointStruct
        from backend.vector_store.qdrant_store import make_point_id

        source_records = []
        offset = None
        while True:
            batch, offset = self.client.scroll(
                self.memory_collection,
                scroll_filter=_user_filter(self.old_user_id),
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            source_records.extend(batch)
            if offset is None:
                break

        mapped: dict[str, Any] = {}
        for record in source_records:
            payload = dict(record.payload or {})
            content = payload.get("content")
            chunk_type = payload.get("chunk_type")
            session_id = payload.get("session_id")
            if not isinstance(content, str) or not isinstance(chunk_type, str):
                raise RuntimeError(
                    f"Qdrant memory point {record.id!r} lacks string content/chunk_type"
                )
            if session_id is not None and not isinstance(session_id, str):
                raise RuntimeError(
                    f"Qdrant memory point {record.id!r} has invalid session_id"
                )
            if record.vector is None:
                raise RuntimeError(
                    f"Qdrant memory point {record.id!r} has no vector"
                )

            payload["user_id"] = self.new_user_id
            point_id = make_point_id(
                self.new_user_id, session_id, chunk_type, content,
            )
            if point_id in mapped:
                raise RuntimeError(
                    "Qdrant memory contains duplicate logical point identities; "
                    "refusing a lossy user-id migration"
                )
            mapped[point_id] = PointStruct(
                id=point_id, vector=record.vector, payload=payload,
            )

        _upsert_batches(
            self.client, self.memory_collection, list(mapped.values())
        )
        copied = self.client.count(
            self.memory_collection,
            count_filter=_user_filter(self.new_user_id),
            exact=True,
        ).count
        if copied != len(mapped):
            raise RuntimeError(
                "Qdrant memory verification failed: "
                f"expected {len(mapped)} new-user points, found {copied}"
            )
        return copied

    def _copy_knowledge_collection(self, source: str, target: str) -> int:
        from qdrant_client.models import PointStruct

        if source == self.memory_collection or target == self.memory_collection:
            raise RuntimeError(
                "Qdrant memory collection collides with a knowledge namespace: "
                f"{self.memory_collection!r}"
            )

        info = self.client.get_collection(source)
        config = info.config
        params = info.config.params
        vectors = params.vectors
        if vectors is None:
            raise RuntimeError(
                f"Qdrant knowledge collection {source!r} has no dense vector config"
            )
        create_kwargs: dict[str, Any] = {"vectors_config": vectors}
        sparse_vectors = getattr(params, "sparse_vectors", None)
        if sparse_vectors is not None:
            create_kwargs["sparse_vectors_config"] = sparse_vectors

        for parameter in (
            "shard_number",
            "sharding_method",
            "replication_factor",
            "write_consistency_factor",
            "on_disk_payload",
        ):
            value = getattr(params, parameter, None)
            if value is not None:
                create_kwargs[parameter] = value

        for target_parameter, source_parameter, model_name in (
            ("hnsw_config", "hnsw_config", "HnswConfigDiff"),
            ("optimizers_config", "optimizer_config", "OptimizersConfigDiff"),
            ("wal_config", "wal_config", "WalConfigDiff"),
        ):
            value = _qdrant_diff(
                model_name, getattr(config, source_parameter, None),
            )
            if value is not None:
                create_kwargs[target_parameter] = value

        quantization = getattr(config, "quantization_config", None)
        if quantization is not None:
            create_kwargs["quantization_config"] = quantization

        metadata = getattr(config, "metadata", None)
        if metadata is not None and _method_supports_parameter(
            self.client.create_collection, "metadata",
        ):
            create_kwargs["metadata"] = metadata

        self.client.create_collection(target, **create_kwargs)
        for field_name, index_info in (getattr(info, "payload_schema", {}) or {}).items():
            field_schema = getattr(index_info, "params", None) or index_info.data_type
            self.client.create_payload_index(
                collection_name=target,
                field_name=field_name,
                field_schema=field_schema,
            )

        copied = 0
        offset = None
        while True:
            batch, offset = self.client.scroll(
                source,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            points = []
            for record in batch:
                if record.vector is None:
                    raise RuntimeError(
                        f"Qdrant knowledge point {record.id!r} has no vector"
                    )
                points.append(PointStruct(
                    id=record.id,
                    vector=record.vector,
                    payload=_rewrite_knowledge_payload(
                        record.payload,
                        self.old_user_id,
                        self.new_user_id,
                    ),
                ))
            _upsert_batches(self.client, target, points)
            copied += len(points)
            if offset is None:
                break

        source_count = self.client.count(source, exact=True).count
        target_count = self.client.count(target, exact=True).count
        if copied != source_count or target_count != source_count:
            raise RuntimeError(
                f"Qdrant knowledge verification failed for {source!r}: "
                f"read={copied}, source={source_count}, target={target_count}"
            )

        update_kwargs: dict[str, Any] = {}
        from qdrant_client import models

        params_diff_class = models.CollectionParamsDiff
        params_diff_fields = getattr(params_diff_class, "model_fields", None)
        if params_diff_fields is None:
            params_diff_fields = getattr(params_diff_class, "__fields__", {})
        params_diff_values = {
            name: getattr(params, name)
            for name in ("read_fan_out_factor", "read_fan_out_delay_ms")
            if name in params_diff_fields and getattr(params, name, None) is not None
        }
        if params_diff_values:
            update_kwargs["collection_params"] = params_diff_class(
                **params_diff_values
            )

        strict_mode = getattr(config, "strict_mode_config", None)
        if strict_mode is not None:
            if not _method_supports_parameter(
                self.client.update_collection, "strict_mode_config",
            ):
                raise RuntimeError(
                    "Installed Qdrant client cannot preserve strict-mode config"
                )
            update_kwargs["strict_mode_config"] = _qdrant_diff(
                "StrictModeConfig", strict_mode,
            )
        if (
            metadata is not None
            and "metadata" not in create_kwargs
            and _method_supports_parameter(
                self.client.update_collection, "metadata",
            )
        ):
            update_kwargs["metadata"] = metadata
        if update_kwargs:
            self.client.update_collection(target, **update_kwargs)
        return copied

    def prepare(self) -> "QdrantUserMigration":
        """Copy old data to a clean new-id namespace, retaining the old data."""
        if self.old_user_id == self.new_user_id:
            return self
        if self.prepared:
            return self

        try:
            # The caller has already verified that no DB user owns new_user_id.
            # Purging makes recovery from a crashed/partial previous attempt
            # deterministic instead of merging unknown orphaned state.
            self._purge_user(self.new_user_id)
            self.memory_point_count = self._copy_memory()

            old_prefix = _kb_prefix(self.old_user_id)
            source_collections = [
                name for name in _collection_names(self.client)
                if (
                    name != self.memory_collection
                    and name.startswith(old_prefix)
                )
            ]
            copied_collections = []
            for source in source_collections:
                suffix = source[len(old_prefix):]
                # Reserved and escaped suffixes cannot be classified reliably
                # across versions. Copying a legacy ``__resume__`` Topic into
                # the current resume namespace would expose topic content to
                # resume retrieval. Disk sources are authoritative and rebuild
                # all of these namespaces safely after the identity migration.
                if _requires_source_rebuild(suffix):
                    logger.warning(
                        "Skipping ambiguous/reserved Qdrant collection %s; "
                        "the migrated disk sources will rebuild it",
                        source,
                    )
                    continue
                target = f"{_kb_prefix(self.new_user_id)}{suffix}"
                self._copy_knowledge_collection(source, target)
                copied_collections.append(target)
            self.knowledge_collections = tuple(copied_collections)
            self.prepared = True
            return self
        except Exception:
            try:
                self._purge_user(self.new_user_id)
            except Exception as rollback_exc:
                logger.error(
                    "Could not clean partial Qdrant user-id migration %s -> %s: %s",
                    self.old_user_id,
                    self.new_user_id,
                    rollback_exc,
                )
            raise

    def rollback(self) -> None:
        """Remove staged new-id data; all old-id data is still intact."""
        if self.old_user_id == self.new_user_id:
            return
        self._purge_user(self.new_user_id)
        self.prepared = False

    def finalize(self) -> None:
        """Delete old-id data after the caller's SQLite commit succeeds."""
        if self.old_user_id == self.new_user_id:
            return
        if not self.prepared:
            raise RuntimeError("Qdrant user-id migration was not prepared")
        self._purge_user(self.old_user_id)


def prepare_qdrant_user_migration(
    old_user_id: str, new_user_id: str,
) -> QdrantUserMigration | None:
    """Stage Qdrant data whenever a Qdrant endpoint is configured."""
    if old_user_id == new_user_id:
        return None
    backend = settings.vector_backend_mode()
    qdrant_url = settings.qdrant_url.strip()
    if backend == "qdrant" and not qdrant_url:
        raise RuntimeError(
            "VECTOR_BACKEND=qdrant requires QDRANT_URL for user-id migration"
        )
    if not qdrant_url:
        return None

    from qdrant_client import QdrantClient

    client = QdrantClient(
        url=qdrant_url,
        api_key=settings.qdrant_api_key or None,
        timeout=10.0,
    )
    client.get_collections()
    return QdrantUserMigration(
        client=client,
        memory_collection=settings.qdrant_memory_collection,
        old_user_id=old_user_id,
        new_user_id=new_user_id,
    ).prepare()


def cleanup_qdrant_legacy_user(
    old_user_id: str, *, memory_collection: str,
) -> bool:
    """Retry deletion of only a committed migration's legacy namespace."""
    backend = settings.vector_backend_mode()
    qdrant_url = settings.qdrant_url.strip()
    if backend == "qdrant" and not qdrant_url:
        raise RuntimeError(
            "VECTOR_BACKEND=qdrant requires QDRANT_URL for user-id cleanup"
        )
    if not qdrant_url:
        return False

    from qdrant_client import QdrantClient

    client = QdrantClient(
        url=qdrant_url,
        api_key=settings.qdrant_api_key or None,
        timeout=10.0,
    )
    client.get_collections()
    migration = QdrantUserMigration(
        client=client,
        memory_collection=memory_collection,
        old_user_id=old_user_id,
        new_user_id="",
    )
    migration._purge_user(old_user_id)
    return True
