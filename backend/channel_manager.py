"""Multi-channel manager — priority selection, key rotation, failover, cooldown."""
import logging
import threading
import time
import uuid

logger = logging.getLogger("uvicorn")

COOLDOWN_SECONDS = 60.0
MAX_ERRORS_BEFORE_COOLDOWN = 3
# Max time a single HALF_OPEN probe may hold the gate before another is allowed.
# Guards against a probe request that dies without ever reporting success/error.
PROBE_TIMEOUT_SECONDS = 30.0


class ChannelState:
    __slots__ = (
        "channel_id", "healthy", "error_count", "cooldown_until",
        "current_key_index", "probing", "probe_started",
        "active_probe_token",
    )

    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self.healthy = True
        self.error_count = 0
        self.cooldown_until = 0.0
        self.current_key_index = 0
        self.probing = False
        self.probe_started = 0.0
        # A probe may outlive its admission timeout.  Keep an opaque generation
        # token so a late completion cannot mutate a newer probe's state.
        self.active_probe_token: str | None = None

    def next_key(self, keys: list[str]) -> str:
        if not keys:
            return ""
        key = keys[self.current_key_index % len(keys)]
        self.current_key_index = (self.current_key_index + 1) % len(keys)
        return key

    def _accept_probe_completion(self, probe_token: str | None) -> bool:
        """Validate and consume a probe lease, if the caller supplied one.

        Tokenless calls retain historical behavior for healthy channels. A
        HALF_OPEN completion must own the active lease, so both stale tokens
        and legacy tokenless callbacks are ignored while a probe is active.
        """
        if probe_token is None:
            # A tokenless completion remains valid for ordinary healthy-channel
            # calls, but it cannot prove ownership of a HALF_OPEN admission.
            return not self.probing
        if not self.probing or self.active_probe_token != probe_token:
            return False
        self.probing = False
        self.probe_started = 0.0
        self.active_probe_token = None
        return True

    def invalidate_probe(self) -> None:
        """Invalidate an in-flight probe when channel configuration reloads."""
        self.probing = False
        self.probe_started = 0.0
        self.active_probe_token = None

    def mark_error(self, probe_token: str | None = None) -> bool:
        if not self._accept_probe_completion(probe_token):
            return False
        self.probing = False
        self.probe_started = 0.0
        self.active_probe_token = None
        self.error_count += 1
        if self.error_count >= MAX_ERRORS_BEFORE_COOLDOWN:
            self.healthy = False
            self.cooldown_until = time.time() + COOLDOWN_SECONDS
            logger.warning(
                "Channel %s entered cooldown for %.0fs after %d errors",
                self.channel_id, COOLDOWN_SECONDS, self.error_count,
            )
        return True

    def mark_success(self, probe_token: str | None = None) -> bool:
        if not self._accept_probe_completion(probe_token):
            return False
        if self.error_count > 0:
            logger.info("Channel %s recovered", self.channel_id)
        self.error_count = 0
        self.healthy = True
        self.probing = False
        self.probe_started = 0.0
        self.active_probe_token = None
        return True

    def is_available(self) -> bool:
        if self.healthy:
            return True
        now = time.time()
        if now < self.cooldown_until:
            return False
        # Cooldown elapsed → HALF_OPEN. Admit exactly ONE probe request at a time;
        # concurrent callers are rejected until the probe reports success/error,
        # so a recovering channel isn't hammered by the whole recovery burst.
        # healthy stays False until a real mark_success() so the gate keeps holding.
        if self.probing and (now - self.probe_started) < PROBE_TIMEOUT_SECONDS:
            return False
        # UUIDs remain unique even if a channel id is removed and later added,
        # which would otherwise reset a per-state integer generation (ABA).
        self.active_probe_token = uuid.uuid4().hex
        self.probing = True
        self.probe_started = now
        return True

    def probe_token(self) -> str | None:
        """Return the token attached to the currently admitted probe."""
        return self.active_probe_token if self.probing else None

    def release_probe(self, probe_token: str | None) -> bool:
        """Release a probe without changing health (e.g. a cache hit)."""
        if probe_token is None or not self.probing:
            return False
        if self.active_probe_token != probe_token:
            return False
        self.probing = False
        self.probe_started = 0.0
        self.active_probe_token = None
        return True


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
                    state = old_states[cid]
                    # A config reload can replace the endpoint while an old
                    # request is still in flight.  Do not let that request
                    # report against the newly loaded channel generation.
                    state.invalidate_probe()
                    new_states[cid] = state
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
                    probe_token = state.probe_token()
                    if probe_token is not None:
                        resolved["_probe_token"] = probe_token
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

    def report_error(
        self,
        section: str,
        channel_id: str,
        probe_token: str | None = None,
    ):
        with self._lock:
            state = self._states.get(section, {}).get(channel_id)
            if state:
                state.mark_error(probe_token)

    def report_success(
        self,
        section: str,
        channel_id: str,
        probe_token: str | None = None,
    ):
        with self._lock:
            state = self._states.get(section, {}).get(channel_id)
            if state:
                state.mark_success(probe_token)

    def release_probe(
        self,
        section: str,
        channel_id: str,
        probe_token: str | None,
    ):
        with self._lock:
            state = self._states.get(section, {}).get(channel_id)
            if state:
                state.release_probe(probe_token)

    def get_health(self, section: str) -> list[dict]:
        with self._lock:
            channels = self._channels.get(section, [])
            states = self._states.get(section, {})
            result = []
            now = time.time()
            for ch in channels:
                cid = ch["id"]
                state = states.get(cid)
                # Pure read: don't call is_available() here — it revives a
                # cooled-down channel as a side effect. Compute availability inline.
                available = (state.healthy or now >= state.cooldown_until) if state else True
                result.append({
                    "id": cid,
                    "name": ch.get("name", ""),
                    "healthy": available,
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

def report_error(section: str, channel_id: str, probe_token: str | None = None):
    _manager.report_error(section, channel_id, probe_token=probe_token)

def report_success(section: str, channel_id: str, probe_token: str | None = None):
    _manager.report_success(section, channel_id, probe_token=probe_token)

def release_probe(section: str, channel_id: str, probe_token: str | None):
    _manager.release_probe(section, channel_id, probe_token=probe_token)

def get_health(section: str) -> list[dict]:
    return _manager.get_health(section)

def has_channels(section: str) -> bool:
    return _manager.has_channels(section)

def get_all_channels(section: str) -> list[dict]:
    return _manager.get_all_channels(section)
