"""Multi-channel manager — priority selection, key rotation, failover, cooldown."""
import logging
import threading
import time
import uuid

logger = logging.getLogger("uvicorn")

COOLDOWN_SECONDS = 60.0
MAX_ERRORS_BEFORE_COOLDOWN = 3


class ChannelState:
    __slots__ = ("channel_id", "healthy", "error_count", "cooldown_until", "current_key_index")

    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self.healthy = True
        self.error_count = 0
        self.cooldown_until = 0.0
        self.current_key_index = 0

    def next_key(self, keys: list[str]) -> str:
        if not keys:
            return ""
        key = keys[self.current_key_index % len(keys)]
        self.current_key_index = (self.current_key_index + 1) % len(keys)
        return key

    def mark_error(self):
        self.error_count += 1
        if self.error_count >= MAX_ERRORS_BEFORE_COOLDOWN:
            self.healthy = False
            self.cooldown_until = time.time() + COOLDOWN_SECONDS
            logger.warning(
                "Channel %s entered cooldown for %.0fs after %d errors",
                self.channel_id, COOLDOWN_SECONDS, self.error_count,
            )

    def mark_success(self):
        if self.error_count > 0:
            logger.info("Channel %s recovered", self.channel_id)
        self.error_count = 0
        self.healthy = True

    def is_available(self) -> bool:
        if self.healthy:
            return True
        if time.time() >= self.cooldown_until:
            self.healthy = True
            self.error_count = 0
            return True
        return False


class ChannelManager:
    def __init__(self):
        self._channels: dict[str, list[dict]] = {}
        self._states: dict[str, dict[str, ChannelState]] = {}
        self._lock = threading.Lock()
        # Dedup the "tier filter empty, falling back" warning per (section, tier).
        # Without this every drill request logs once, drowning real signals.
        self._tier_fallback_warned: set[tuple[str, str]] = set()

    def load_channels(self, section: str, channels: list[dict]):
        with self._lock:
            for ch in channels:
                if not ch.get("id"):
                    ch["id"] = uuid.uuid4().hex[:8]
            sorted_channels = sorted(channels, key=lambda c: c.get("priority", 1))
            self._channels[section] = sorted_channels

            old_states = self._states.get(section, {})
            new_states = {}
            for ch in sorted_channels:
                cid = ch["id"]
                if cid in old_states:
                    new_states[cid] = old_states[cid]
                else:
                    new_states[cid] = ChannelState(cid)
            self._states[section] = new_states
            # Reload invalidates any prior tier-fallback warning state — the
            # operator may have just added the missing tier.
            self._tier_fallback_warned = {
                k for k in self._tier_fallback_warned if k[0] != section
            }

    def get_channel(self, section: str, tier: str | None = None) -> dict | None:
        with self._lock:
            return self._select(section, exclude=set(), tier=tier)

    def get_next_channel(self, section: str, exclude: set[str], tier: str | None = None) -> dict | None:
        with self._lock:
            return self._select(section, exclude, tier=tier)

    def _select(self, section: str, exclude: set[str], tier: str | None = None) -> dict | None:
        channels = self._channels.get(section, [])
        states = self._states.get(section, {})

        # First pass: respect tier filter if requested
        # Second pass: if tier filter empties the pool, fall back to any healthy channel
        for pass_idx in (0, 1):
            for ch in channels:
                cid = ch["id"]
                if cid in exclude:
                    continue
                if not ch.get("enabled", True):
                    continue
                if pass_idx == 0 and tier is not None:
                    # tier defaults to "large" for back-compat with channels that lack the field
                    ch_tier = (ch.get("tier") or "large").lower()
                    if ch_tier != tier.lower():
                        continue
                state = states.get(cid)
                if state and not state.is_available():
                    continue
                resolved = dict(ch)
                if state:
                    resolved["api_key"] = state.next_key(ch.get("keys", []))
                else:
                    keys = ch.get("keys", [])
                    resolved["api_key"] = keys[0] if keys else ""
                return resolved
            # No tier filter → no need for a second pass
            if tier is None:
                break
            # Tier-filtered pass produced nothing → log once per (section, tier).
            if pass_idx == 0:
                key = (section, (tier or "").lower())
                if key not in self._tier_fallback_warned:
                    self._tier_fallback_warned.add(key)
                    logger.warning(
                        "Channel manager: no channel matched tier=%s in section=%s, falling back to any tier",
                        tier, section,
                    )
        return None

    def report_error(self, section: str, channel_id: str):
        with self._lock:
            state = self._states.get(section, {}).get(channel_id)
            if state:
                state.mark_error()

    def report_success(self, section: str, channel_id: str):
        with self._lock:
            state = self._states.get(section, {}).get(channel_id)
            if state:
                state.mark_success()

    def get_health(self, section: str) -> list[dict]:
        with self._lock:
            channels = self._channels.get(section, [])
            states = self._states.get(section, {})
            result = []
            for ch in channels:
                cid = ch["id"]
                state = states.get(cid)
                result.append({
                    "id": cid,
                    "name": ch.get("name", ""),
                    "healthy": state.is_available() if state else True,
                    "error_count": state.error_count if state else 0,
                    "cooldown_until": state.cooldown_until if state and not state.healthy else None,
                    "current_key_index": state.current_key_index if state else 0,
                })
            return result

    def has_channels(self, section: str) -> bool:
        with self._lock:
            channels = self._channels.get(section, [])
            return any(ch.get("enabled", True) for ch in channels)

    def get_all_channels(self, section: str) -> list[dict]:
        with self._lock:
            return list(self._channels.get(section, []))


_manager = ChannelManager()


def load_channels(section: str, channels: list[dict]):
    _manager.load_channels(section, channels)

def get_channel(section: str, tier: str | None = None) -> dict | None:
    return _manager.get_channel(section, tier=tier)

def get_next_channel(section: str, exclude: set[str], tier: str | None = None) -> dict | None:
    return _manager.get_next_channel(section, exclude, tier=tier)

def report_error(section: str, channel_id: str):
    _manager.report_error(section, channel_id)

def report_success(section: str, channel_id: str):
    _manager.report_success(section, channel_id)

def get_health(section: str) -> list[dict]:
    return _manager.get_health(section)

def has_channels(section: str) -> bool:
    return _manager.has_channels(section)

def get_all_channels(section: str) -> list[dict]:
    return _manager.get_all_channels(section)
