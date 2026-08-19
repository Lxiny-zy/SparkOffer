"""Runtime AI configuration — JSON overlay on top of .env settings.

Provides hot-reloadable AI provider config that persists to data/ai_config.json
and takes priority over environment variables.  Supports multi-channel configs.
"""

import json
import os
import threading
import logging
import uuid
import copy
from pathlib import Path

from backend.config import settings

logger = logging.getLogger("uvicorn")

AI_CONFIG_PATH: Path = settings.base_dir / "data" / "ai_config.json"

_lock = threading.Lock()
_cache: dict = {}
_config_version: int = 0

# Fixed-length placeholder returned by owner-only settings APIs.  A fixed mask
# avoids leaking key length or prefixes while still allowing the existing UI to
# render one input per configured key.  The placeholder is never persisted as a
# credential: save helpers restore the previous value when it is submitted.
SECRET_MASK = "********"
_SECRET_FIELDS = {"api_key", "keys", "token", "password", "secret"}


def is_secret_mask(value: object) -> bool:
    return isinstance(value, str) and value == SECRET_MASK


def mask_secret(value: object) -> str:
    return SECRET_MASK if value not in (None, "") else ""


def redact_channel(channel: dict) -> dict:
    """Return a deep-copied channel with all provider keys masked."""
    result = copy.deepcopy(channel)
    keys = result.get("keys")
    if isinstance(keys, list):
        # Preserve list positions.  ``save_channels`` uses the position of a
        # masked key to restore the value from the persisted channel; dropping
        # empty slots here would shift later keys and could restore the wrong
        # credential after an otherwise harmless settings edit.
        result["keys"] = [mask_secret(key) for key in keys]
    for field in ("api_key", "token", "password", "secret"):
        if field in result:
            result[field] = mask_secret(result.get(field))
    # Legacy configurations allowed credentials in URL userinfo.  Current
    # validation rejects that shape for provider bases, but an old config (or
    # a proxy URL) can still contain it and must not be reflected by the
    # owner-only settings response.
    for field in ("api_base", "proxy"):
        value = result.get(field)
        if isinstance(value, str) and value:
            try:
                from backend.utils.outbound import redact_url_credentials
                result[field] = redact_url_credentials(value)
            except Exception:
                # Redaction is a security boundary: if parsing fails, do not
                # return the original potentially credential-bearing value.
                result[field] = "<redacted-url>"
    return result


def _restore_masked_url(value: object, old_value: object) -> object:
    """Restore a URL that came back from ``redact_channel`` unchanged."""
    if not isinstance(value, str) or not isinstance(old_value, str) or not old_value:
        return value
    if value == SECRET_MASK:
        return old_value
    try:
        from backend.utils.outbound import redact_url_credentials
        if value == redact_url_credentials(old_value):
            return old_value
    except Exception:
        pass
    return value

# Mapping from (section, key) to the settings attribute name
_SETTINGS_KEY_MAP = {
    ("llm", "api_base"): "api_base",
    ("llm", "api_key"): "api_key",
    ("llm", "model"): "model",
    ("llm", "temperature"): "temperature",
    ("embedding", "backend"): "embedding_backend",
    ("embedding", "api_base"): "embedding_api_base",
    ("embedding", "api_key"): "embedding_api_key",
    ("embedding", "api_model"): "embedding_api_model",
    ("embedding", "local_model"): "local_embedding_model",
    ("embedding", "local_path"): "local_embedding_path",
    ("reranker", "api_base"): "reranker_api_base",
    ("reranker", "api_key"): "reranker_api_key",
    ("reranker", "api_model"): "reranker_api_model",
}


def _load_from_disk() -> dict:
    """Read JSON file, return empty dict if missing or corrupt."""
    try:
        if AI_CONFIG_PATH.exists():
            return json.loads(AI_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load ai_config.json: {e}")
    return {}


def _migrate_flat_to_channels(data: dict) -> bool:
    """Convert old flat config to channels format. Returns True if migrated."""
    migrated = False
    for section, field_map in (
        ("llm", {"api_base": "api_base", "api_key": "keys", "model": "model", "temperature": "temperature"}),
        ("embedding", {"backend": "backend", "api_base": "api_base", "api_key": "keys",
                       "api_model": "api_model", "local_model": "local_model", "local_path": "local_path"}),
    ):
        sec_data = data.get(section)
        if not isinstance(sec_data, dict):
            continue
        if "channels" in sec_data:
            continue
        has_values = any(v for k, v in sec_data.items() if k not in ("channels",))
        if not has_values:
            continue
        channel: dict = {"id": uuid.uuid4().hex[:8], "name": "Default", "priority": 1, "enabled": True}
        for old_key, new_key in field_map.items():
            val = sec_data.get(old_key)
            if val is None or val == "":
                continue
            if new_key == "keys":
                channel["keys"] = [val]
            elif new_key == "temperature":
                channel[new_key] = float(val) if val else 0.7
            else:
                channel[new_key] = val
        if channel.get("api_base") or channel.get("keys") or channel.get("model"):
            data[section] = {"channels": [channel]}
            migrated = True
    return migrated


def _build_env_channels():
    """Create synthetic channels from .env values for sections without JSON channels."""
    from backend.channel_manager import load_channels, has_channels

    for section, builder in (("llm", _env_llm_channel), ("embedding", _env_embedding_channel), ("reranker", _env_reranker_channel)):
        if not has_channels(section):
            ch = builder()
            if ch:
                load_channels(section, [ch])


def _env_llm_channel() -> dict | None:
    base = getattr(settings, "api_base", "")
    key = getattr(settings, "api_key", "")
    model = getattr(settings, "model", "")
    if not (base and key and model):
        return None
    return {
        "id": "env-llm", "name": ".env", "api_base": base, "keys": [key],
        "model": model, "temperature": float(getattr(settings, "temperature", 0.7)),
        "priority": 1, "enabled": True,
    }


def _env_embedding_channel() -> dict | None:
    backend = getattr(settings, "embedding_backend", "") or ""
    if backend.strip().lower() == "local" or (not getattr(settings, "embedding_api_base", "") and not getattr(settings, "embedding_api_key", "")):
        return None
    return {
        "id": "env-emb", "name": ".env", "backend": "api",
        "api_base": getattr(settings, "embedding_api_base", ""),
        "keys": [k] if (k := getattr(settings, "embedding_api_key", "")) else [],
        "api_model": getattr(settings, "embedding_api_model", ""),
        "priority": 1, "enabled": True,
    }


def _env_reranker_channel() -> dict | None:
    base = getattr(settings, "reranker_api_base", "")
    key = getattr(settings, "reranker_api_key", "")
    model = getattr(settings, "reranker_api_model", "")
    if not (base and key):
        return None
    return {
        "id": "env-reranker", "name": ".env",
        "api_base": base, "keys": [key], "api_model": model,
        "priority": 1, "enabled": True,
    }


def _reload_channel_manager():
    """Feed current channels from cache into channel_manager."""
    from backend.channel_manager import load_channels
    for section in ("llm", "embedding", "reranker"):
        sec = _cache.get(section)
        if isinstance(sec, dict) and "channels" in sec:
            load_channels(section, sec["channels"])
    _build_env_channels()


def init_config():
    """Load config from disk into memory cache. Called at startup."""
    global _cache
    with _lock:
        _cache = _load_from_disk()
        if _cache and _migrate_flat_to_channels(_cache):
            AI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            AI_CONFIG_PATH.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Migrated flat AI config to channels format")
    if _cache:
        logger.info(f"Loaded AI config overrides from {AI_CONFIG_PATH}")
    _reload_channel_manager()


def save_ai_config(config: dict):
    """Merge incoming config into cache, write to disk, bump version."""
    global _cache, _config_version
    with _lock:
        # Deep merge: only set keys that are explicitly provided
        for section in ("llm", "embedding", "reranker"):
            incoming = config.get(section)
            if incoming is None:
                continue
            if section not in _cache:
                _cache[section] = {}
            for key, value in incoming.items():
                # The settings UI sends SECRET_MASK for an unchanged key after
                # GET responses were redacted.  Keep the stored credential in
                # that case; an explicit empty value still removes it.
                if key in _SECRET_FIELDS and is_secret_mask(value):
                    continue
                # Legacy flat settings responses redact credentials embedded in
                # api_base URLs.  Preserve the persisted URL when a client
                # sends that redacted representation back unchanged.
                if key == "api_base":
                    value = _restore_masked_url(value, _cache[section].get(key))
                if value is None or value == "":
                    _cache[section].pop(key, None)
                else:
                    _cache[section][key] = value
            if not _cache[section]:
                del _cache[section]

        _write_and_bump()
    _reload_channel_manager()
    logger.info(f"AI config saved (version {_config_version})")


def save_channels(channels_config: dict):
    """Save multi-channel config. channels_config = {llm: [...], embedding: [...], reranker: [...]}."""
    global _cache, _config_version
    with _lock:
        for section in ("llm", "embedding", "reranker"):
            channels = channels_config.get(section)
            if channels is None:
                continue
            # Merge masked key placeholders with the previous persisted values.
            # This permits editing a channel's model/base URL without forcing a
            # credential rotation or exposing the old value to the browser.
            old_channels = ((_cache.get(section) or {}).get("channels") or [])
            old_by_id = {str(ch.get("id")): ch for ch in old_channels if isinstance(ch, dict)}
            normalized_channels = []
            for incoming in channels:
                ch = copy.deepcopy(incoming)
                old = old_by_id.get(str(ch.get("id")), {})
                incoming_keys = ch.get("keys")
                old_keys = old.get("keys") if isinstance(old, dict) else []
                if isinstance(incoming_keys, list):
                    kept = []
                    for idx, key in enumerate(incoming_keys):
                        if is_secret_mask(key):
                            old_key = old_keys[idx] if isinstance(old_keys, list) and idx < len(old_keys) else ""
                            if old_key:
                                kept.append(old_key)
                        elif key not in (None, ""):
                            kept.append(key)
                    ch["keys"] = kept
                for field in ("api_key", "token", "password", "secret"):
                    if is_secret_mask(ch.get(field)):
                        ch[field] = old.get(field, "") if isinstance(old, dict) else ""
                for field in ("api_base", "proxy"):
                    if isinstance(old, dict):
                        ch[field] = _restore_masked_url(ch.get(field), old.get(field))
                normalized_channels.append(ch)
            channels = normalized_channels
            for ch in channels:
                if not ch.get("id"):
                    ch["id"] = uuid.uuid4().hex[:8]
            if section not in _cache:
                _cache[section] = {}
            _cache[section]["channels"] = channels
        _write_and_bump()
    _reload_channel_manager()
    logger.info(f"Channels config saved (version {_config_version})")


def get_channels(section: str) -> list[dict]:
    """Return a redacted channel list for API/UI consumers.

    Internal provider code reads the cache through ``channel_manager`` or
    ``get_effective``; this public helper is intentionally safe by default.
    """
    with _lock:
        sec = _cache.get(section)
        if isinstance(sec, dict):
            return [redact_channel(ch) for ch in sec.get("channels", []) if isinstance(ch, dict)]
    return []


def _write_and_bump():
    global _config_version
    AI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: serialize to a temp file in the same dir, then os.replace().
    # A crash mid-write must not leave a truncated ai_config.json — that would
    # silently drop all runtime channel config back to .env on next load.
    tmp = AI_CONFIG_PATH.with_suffix(AI_CONFIG_PATH.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(
        json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    os.replace(tmp, AI_CONFIG_PATH)
    _config_version += 1


def get_effective(section: str, key: str):
    """Return JSON override if present, else the .env/default value."""
    # Check JSON overlay first
    with _lock:
        json_val = _cache.get(section, {}).get(key)
    if json_val is not None and json_val != "":
        return json_val

    # Fall back to settings attribute
    attr = _SETTINGS_KEY_MAP.get((section, key))
    if attr:
        return getattr(settings, attr, "")
    return ""


def get_config_version() -> int:
    return _config_version


def _detect_source(section: str, key: str) -> str:
    """Determine where the effective value comes from."""
    with _lock:
        json_val = _cache.get(section, {}).get(key)
    if json_val is not None and json_val != "":
        return "json"
    attr = _SETTINGS_KEY_MAP.get((section, key))
    if attr:
        env_val = getattr(settings, attr, "")
        if env_val is not None and env_val != "":
            return "env"
    return "default"


def get_all_effective() -> dict:
    """Return full config with effective values and source markers."""
    result = {}
    for (section, key), _ in _SETTINGS_KEY_MAP.items():
        if section not in result:
            result[section] = {}
        value = get_effective(section, key)
        source = _detect_source(section, key)

        if key in _SECRET_FIELDS:
            display = mask_secret(value)
        elif key == "api_base" and isinstance(value, str):
            try:
                from backend.utils.outbound import redact_url_credentials
                display = redact_url_credentials(value)
            except Exception:
                display = "<redacted-url>"
        elif isinstance(value, (int, float)):
            display = value
        else:
            display = str(value) if value else ""

        result[section][key] = {"value": display, "source": source}

    return result


def get_raw_value(section: str, key: str):
    """Return the raw (unmasked) effective value. For internal use only."""
    return get_effective(section, key)


# ── Runtime tuning: context budget + retrieval knobs ─────────────────────────
# Centralizes magic numbers that used to live as scattered module constants
# (LLM output cap, fallback context window, the RAG retrieval knobs). Stored
# under the "tuning" key of ai_config.json. The resolvers below apply defaults
# AND clamp to a safe range, and are meant to be read at CALL TIME so a settings
# save hot-applies on the next request (no restart). "Empty/missing → default"
# is the rule everywhere: defaults are a floor, never the only source.

RETRIEVAL_PRESETS: dict[str, dict] = {
    "fast":     {"per_query_top_k": 3, "final_top_n": 6,  "embed_concurrency": 2,
                 "dedup_threshold": 0.85, "end_to_end_timeout": 40,
                 "per_query_timeout": 20, "reranker_read_timeout": 15},
    "balanced": {"per_query_top_k": 5, "final_top_n": 10, "embed_concurrency": 2,
                 "dedup_threshold": 0.85, "end_to_end_timeout": 100,
                 "per_query_timeout": 45, "reranker_read_timeout": 30},
    "thorough": {"per_query_top_k": 8, "final_top_n": 15, "embed_concurrency": 3,
                 "dedup_threshold": 0.88, "end_to_end_timeout": 150,
                 "per_query_timeout": 60, "reranker_read_timeout": 45},
}

# (min, max) clamp per retrieval key — an operator typo must not wedge retrieval.
_RETRIEVAL_CLAMP: dict[str, tuple[float, float]] = {
    "per_query_top_k": (1, 20),
    "final_top_n": (1, 50),
    "embed_concurrency": (1, 16),
    "dedup_threshold": (0.5, 0.99),
    "end_to_end_timeout": (10, 600),
    "per_query_timeout": (5, 300),
    "reranker_read_timeout": (5, 120),
}
_RETRIEVAL_INT_KEYS = {
    "per_query_top_k", "final_top_n", "embed_concurrency",
    "end_to_end_timeout", "per_query_timeout", "reranker_read_timeout",
}

TUNING_DEFAULTS: dict = {
    "max_output_tokens": 32768,        # output reserve / per-call max_tokens fallback
    "default_context_window": 200000,  # used when a channel declares no context_window
    "retrieval": dict(RETRIEVAL_PRESETS["balanced"]),
}
_TUNING_CLAMP: dict[str, tuple[int, int]] = {
    "max_output_tokens": (256, 200000),
    "default_context_window": (1000, 2_000_000),
}


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


def get_tuning(key: str, default=None):
    """Resolve a scalar tuning value (``max_output_tokens`` / ``default_context_window``).

    Reads the ai_config.json "tuning" overlay, falls back to ``TUNING_DEFAULTS``
    (then ``default``), and clamps to a safe range. Read at call time.
    """
    with _lock:
        raw = (_cache.get("tuning") or {}).get(key)
    if raw is None or raw == "":
        raw = TUNING_DEFAULTS.get(key, default)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return TUNING_DEFAULTS.get(key, default)
    if key in _TUNING_CLAMP:
        lo, hi = _TUNING_CLAMP[key]
        val = int(_clamp(val, lo, hi))
    return val


def get_retrieval_setting(key: str):
    """Resolve one retrieval knob (overlay → balanced default), clamped to range.

    Int-valued keys come back as ``int``; ``dedup_threshold`` as ``float``.
    """
    default = RETRIEVAL_PRESETS["balanced"].get(key)
    with _lock:
        raw = ((_cache.get("tuning") or {}).get("retrieval") or {}).get(key)
    if raw is None or raw == "":
        raw = default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = float(default)
    if key in _RETRIEVAL_CLAMP:
        lo, hi = _RETRIEVAL_CLAMP[key]
        val = _clamp(val, lo, hi)
    return int(val) if key in _RETRIEVAL_INT_KEYS else float(val)


def get_tuning_config() -> dict:
    """Full resolved tuning for the settings UI: current values + defaults + presets."""
    with _lock:
        retrieval_overlay = dict((_cache.get("tuning") or {}).get("retrieval") or {})
    values = {
        "max_output_tokens": get_tuning("max_output_tokens"),
        "default_context_window": get_tuning("default_context_window"),
        "retrieval": {
            "preset": retrieval_overlay.get("preset", "balanced"),
            **{k: get_retrieval_setting(k) for k in RETRIEVAL_PRESETS["balanced"]},
        },
    }
    return {"values": values, "defaults": TUNING_DEFAULTS, "presets": RETRIEVAL_PRESETS}


def save_tuning(cfg: dict):
    """Merge tuning overlay into cache, write to disk, bump version, reload.

    Mirrors save_ai_config/save_channels: mutate under the lock, then reload the
    channel manager OUTSIDE the lock. Empty/None values delete the key so it
    reverts to the default.
    """
    global _cache, _config_version
    with _lock:
        existing = dict(_cache.get("tuning") or {})
        for key in ("max_output_tokens", "default_context_window"):
            if key in cfg:
                v = cfg[key]
                if v is None or v == "":
                    existing.pop(key, None)
                else:
                    existing[key] = int(v)
        if isinstance(cfg.get("retrieval"), dict):
            r_in = cfg["retrieval"]
            r_existing = dict(existing.get("retrieval") or {})
            if "preset" in r_in:
                r_existing["preset"] = r_in["preset"] or "balanced"
            for k in RETRIEVAL_PRESETS["balanced"]:
                if k in r_in:
                    v = r_in[k]
                    if v is None or v == "":
                        r_existing.pop(k, None)
                    else:
                        r_existing[k] = v
            existing["retrieval"] = r_existing
        _cache["tuning"] = existing
        _write_and_bump()
    _reload_channel_manager()
    logger.info(f"Tuning config saved (version {_config_version})")
