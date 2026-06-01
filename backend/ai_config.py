"""Runtime AI configuration — JSON overlay on top of .env settings.

Provides hot-reloadable AI provider config that persists to data/ai_config.json
and takes priority over environment variables.  Supports multi-channel configs.
"""

import json
import os
import threading
import logging
import uuid
from pathlib import Path

from backend.config import settings

logger = logging.getLogger("uvicorn")

AI_CONFIG_PATH: Path = settings.base_dir / "data" / "ai_config.json"

_lock = threading.Lock()
_cache: dict = {}
_config_version: int = 0

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
    ("asr", "dashscope_api_key"): "dashscope_api_key",
    ("asr", "model"): "asr_model",
    ("qiniu", "access_key"): "qiniu_access_key",
    ("qiniu", "secret_key"): "qiniu_secret_key",
    ("qiniu", "bucket"): "qiniu_bucket",
    ("qiniu", "domain"): "qiniu_domain",
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
        ("asr", {"dashscope_api_key": "keys", "model": "model"}),
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
        if channel.get("api_base") or channel.get("keys") or channel.get("model") or section == "asr":
            data[section] = {"channels": [channel]}
            migrated = True
    return migrated


def _build_env_channels():
    """Create synthetic channels from .env values for sections without JSON channels."""
    from backend.channel_manager import load_channels, has_channels

    for section, builder in (("llm", _env_llm_channel), ("embedding", _env_embedding_channel), ("asr", _env_asr_channel)):
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


def _env_asr_channel() -> dict | None:
    key = getattr(settings, "dashscope_api_key", "")
    if not key:
        return None
    return {
        "id": "env-asr", "name": ".env",
        "keys": [key], "model": getattr(settings, "asr_model", "qwen3-asr-flash-filetrans"),
        "priority": 1, "enabled": True,
    }


def _reload_channel_manager():
    """Feed current channels from cache into channel_manager."""
    from backend.channel_manager import load_channels
    for section in ("llm", "embedding", "asr"):
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
        for section in ("llm", "embedding", "asr", "qiniu"):
            incoming = config.get(section)
            if incoming is None:
                continue
            if section not in _cache:
                _cache[section] = {}
            for key, value in incoming.items():
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
    """Save multi-channel config. channels_config = {llm: [...], embedding: [...], asr: [...]}."""
    global _cache, _config_version
    with _lock:
        for section in ("llm", "embedding", "asr"):
            channels = channels_config.get(section)
            if channels is None:
                continue
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
    """Return channel list for a section from cache."""
    with _lock:
        sec = _cache.get(section)
        if isinstance(sec, dict):
            return sec.get("channels", [])
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

        if isinstance(value, (int, float)):
            display = value
        else:
            display = str(value) if value else ""

        result[section][key] = {"value": display, "source": source}

    return result


def get_raw_value(section: str, key: str):
    """Return the raw (unmasked) effective value. For internal use only."""
    return get_effective(section, key)
