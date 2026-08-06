from backend.live_store import get_live


def test_get_live_uses_one_atomic_cache_lookup():
    cached = {"value": "ready"}

    class AtomicOnlyStore:
        def get(self, key, default=None):
            return cached if key == "session" else default

        def __contains__(self, _key):
            raise AssertionError("contains-then-get is not atomic")

        def __getitem__(self, _key):
            raise AssertionError("contains-then-get is not atomic")

    assert get_live(
        AtomicOnlyStore(), "session", "resume", "user-1"
    ) is cached
