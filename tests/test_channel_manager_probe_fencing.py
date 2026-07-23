import asyncio
import time

import httpx
import pytest

import backend.channel_manager as channel_manager
import backend.llm_provider as llm_provider


def _manager_with_channel() -> tuple[channel_manager.ChannelManager, object]:
    manager = channel_manager.ChannelManager()
    manager.load_channels("llm", [{
        "id": "primary",
        "name": "Primary",
        "api_base": "https://provider.example/v1",
        "keys": ["secret-key"],
        "model": "model",
        "enabled": True,
    }])
    return manager, manager._states["llm"]["primary"]


def test_late_probe_completion_cannot_mutate_new_generation():
    manager, state = _manager_with_channel()
    state.healthy = False
    state.cooldown_until = 0.0

    first = manager.get_channel("llm")
    first_token = first["_probe_token"]
    state.probe_started = time.time() - channel_manager.PROBE_TIMEOUT_SECONDS - 1

    second = manager.get_channel("llm")
    second_token = second["_probe_token"]
    assert second_token != first_token

    manager.report_error("llm", "primary", first_token)
    manager.report_success("llm", "primary", first_token)
    assert state.active_probe_token == second_token
    assert state.healthy is False

    manager.report_success("llm", "primary", second_token)
    assert state.healthy is True
    assert state.active_probe_token is None


def test_tokenless_report_cannot_complete_half_open_probe():
    manager, state = _manager_with_channel()
    state.healthy = False
    state.cooldown_until = 0.0
    selected = manager.get_channel("llm")
    token = selected["_probe_token"]

    manager.report_success("llm", "primary")
    manager.report_error("llm", "primary")

    assert state.active_probe_token == token
    assert state.healthy is False


def test_reload_invalidates_probe_lease():
    manager, state = _manager_with_channel()
    state.healthy = False
    state.cooldown_until = 0.0
    selected = manager.get_channel("llm")
    token = selected["_probe_token"]

    manager.load_channels("llm", [{
        "id": "primary",
        "name": "Replaced",
        "api_base": "https://new-provider.example/v1",
        "keys": ["new-secret"],
        "model": "new-model",
        "enabled": True,
    }])
    manager.report_success("llm", "primary", token)

    assert state.active_probe_token is None
    assert state.healthy is False


def test_removed_and_readded_channel_does_not_reuse_probe_token():
    manager, state = _manager_with_channel()
    state.healthy = False
    state.cooldown_until = 0.0
    first = manager.get_channel("llm")
    old_token = first["_probe_token"]

    manager.load_channels("llm", [])
    manager.load_channels("llm", [{
        "id": "primary",
        "name": "Readded",
        "api_base": "https://readded.example/v1",
        "keys": ["new-secret"],
        "model": "new-model",
        "enabled": True,
    }])
    new_state = manager._states["llm"]["primary"]
    new_state.healthy = False
    new_state.cooldown_until = 0.0
    second = manager.get_channel("llm")
    new_token = second["_probe_token"]

    manager.report_success("llm", "primary", old_token)

    assert new_token != old_token
    assert new_state.active_probe_token == new_token
    assert new_state.healthy is False


def _fatal_http_error():
    request = httpx.Request("POST", "https://provider.example/v1/chat")
    response = httpx.Response(400, request=request)
    return httpx.HTTPStatusError(
        "bad request for secret-key", request=request, response=response,
    )


@pytest.mark.parametrize("method", ["invoke", "ainvoke", "astream"])
def test_fatal_llm_error_releases_half_open_probe(monkeypatch, caplog, method):
    manager, state = _manager_with_channel()
    state.healthy = False
    state.error_count = channel_manager.MAX_ERRORS_BEFORE_COOLDOWN
    state.cooldown_until = 0.0
    monkeypatch.setattr(channel_manager, "_manager", manager)

    class FatalLLM:
        def invoke(self, *_args, **_kwargs):
            raise _fatal_http_error()

        async def ainvoke(self, *_args, **_kwargs):
            raise _fatal_http_error()

        def astream(self, *_args, **_kwargs):
            async def stream():
                raise _fatal_http_error()
                yield None

            return stream()

    model = llm_provider.ResilientChatModel()
    monkeypatch.setattr(model, "_make_and_bind", lambda _channel: FatalLLM())

    with pytest.raises(httpx.HTTPStatusError):
        if method == "invoke":
            model.invoke([])
        elif method == "ainvoke":
            asyncio.run(model.ainvoke([]))
        else:
            async def consume():
                async for _chunk in model.astream([]):
                    pass

            asyncio.run(consume())

    assert state.probing is False
    assert state.active_probe_token is None
    assert state.healthy is False
    assert state.error_count == channel_manager.MAX_ERRORS_BEFORE_COOLDOWN
    assert "secret-key" not in caplog.text
    assert "[redacted]" in caplog.text
