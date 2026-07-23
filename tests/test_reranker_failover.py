import pytest
import httpx

import backend.ai_config as ai_config
import backend.channel_manager as channel_manager
import backend.reranker as reranker


class _Cache:
    def get_json(self, _key):
        return None

    def set_json(self, _key, _value, _ttl):
        return None


class _Response:
    def __init__(self, url, status_code=200, *, body=None, text=""):
        self.request = httpx.Request("POST", url)
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=self,
            )

    def json(self):
        return self._body


def _install_fake_client(monkeypatch, outcomes, calls):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, **_kwargs):
            calls.append((url, headers["Authorization"]))
            outcome = outcomes[len(calls) - 1]
            return outcome(url)

    monkeypatch.setattr(reranker.httpx, "AsyncClient", _Client)


def _managed_channels(*channels):
    manager = channel_manager.ChannelManager()
    manager.load_channels("reranker", list(channels))
    return manager


@pytest.mark.asyncio
async def test_reranker_fails_over_to_next_managed_channel(monkeypatch, caplog):
    manager = _managed_channels(
        {
            "id": "first",
            "name": "First",
            "api_base": "https://first.example/v1",
            "keys": ["first-secret"],
            "api_model": "rerank-a",
            "priority": 1,
            "enabled": True,
        },
        {
            "id": "second",
            "name": "Second",
            "api_base": "https://second.example/v1",
            "keys": ["second-secret"],
            "api_model": "rerank-b",
            "priority": 2,
            "enabled": True,
        },
    )
    monkeypatch.setattr(channel_manager, "_manager", manager)
    monkeypatch.setattr(reranker, "get_cache", lambda: _Cache())
    calls = []

    def first_failure(url):
        return _Response(url, 503, text="upstream rejected first-secret")

    def second_success(url):
        return _Response(
            url,
            body={"results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.1},
            ]},
        )

    _install_fake_client(monkeypatch, [first_failure, second_success], calls)

    result, status = await reranker.rerank("question", ["first", "second"], top_n=2)

    assert result == ["second", "first"]
    assert status == "applied"
    assert calls == [
        ("https://first.example/v1/rerank", "Bearer first-secret"),
        ("https://second.example/v1/rerank", "Bearer second-secret"),
    ]
    assert manager._states["reranker"]["first"].error_count == 1
    assert "first-secret" not in caplog.text


@pytest.mark.asyncio
async def test_reranker_uses_legacy_env_fallback_after_managed_pool_exhausted(monkeypatch):
    manager = _managed_channels({
        "id": "managed",
        "name": "Managed",
        "api_base": "https://managed.example/v1",
        "keys": ["managed-secret"],
        "api_model": "managed-model",
        "enabled": True,
    })
    monkeypatch.setattr(channel_manager, "_manager", manager)
    monkeypatch.setattr(reranker, "get_cache", lambda: _Cache())

    def effective(section, key):
        return {
            ("reranker", "api_base"): "https://env.example/v1",
            ("reranker", "api_key"): "env-secret",
            ("reranker", "api_model"): "env-model",
        }.get((section, key), "")

    monkeypatch.setattr(ai_config, "get_effective", effective)
    calls = []

    def managed_failure(url):
        return _Response(url, 503, text="temporary failure")

    def env_success(url):
        return _Response(
            url,
            body={"results": [{"index": 0, "relevance_score": 1.0}]},
        )

    _install_fake_client(monkeypatch, [managed_failure, env_success], calls)

    result, status = await reranker.rerank("question", ["only", "other"], top_n=1)

    assert result == ["only"]
    assert status == "applied"
    assert calls == [
        ("https://managed.example/v1/rerank", "Bearer managed-secret"),
        ("https://env.example/v1/rerank", "Bearer env-secret"),
    ]


@pytest.mark.asyncio
async def test_deterministic_reranker_error_releases_half_open_probe(monkeypatch):
    manager = _managed_channels({
        "id": "managed",
        "name": "Managed",
        "api_base": "https://managed.example/v1",
        "keys": ["managed-secret"],
        "api_model": "managed-model",
        "enabled": True,
    })
    state = manager._states["reranker"]["managed"]
    state.healthy = False
    state.error_count = channel_manager.MAX_ERRORS_BEFORE_COOLDOWN
    state.cooldown_until = 0.0
    monkeypatch.setattr(channel_manager, "_manager", manager)
    monkeypatch.setattr(reranker, "get_cache", lambda: _Cache())
    monkeypatch.setattr(ai_config, "get_effective", lambda *_args: "")
    calls = []

    def bad_input(url):
        return _Response(url, 400, text="invalid documents")

    _install_fake_client(monkeypatch, [bad_input], calls)

    result, status = await reranker.rerank("question", ["first", "second"])

    assert result == ["first", "second"]
    assert status == "degraded"
    assert state.probing is False
    assert state.active_probe_token is None
    assert state.healthy is False
    assert state.error_count == channel_manager.MAX_ERRORS_BEFORE_COOLDOWN
