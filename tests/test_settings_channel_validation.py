"""Validation at the settings API boundary keeps unusable channels disabled."""

import pytest
from fastapi import HTTPException

import backend.channel_manager as channel_manager
import backend.llm_provider as llm_provider
from backend.llm_provider import _resolve_temperature
from backend.routers.settings_router import _validate_channel_section


def test_keyless_llm_channel_is_valid_for_authless_endpoint():
    channels = _validate_channel_section("llm", [{
        "name": "Local",
        "api_base": "http://localhost:11434/v1",
        "model": "local-model",
    }])

    assert channels[0].keys == []


def test_enabled_empty_embedding_channel_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        _validate_channel_section("embedding", [{}])

    assert exc_info.value.status_code == 422
    assert "api_model" in str(exc_info.value.detail)


def test_disabled_empty_embedding_channel_can_be_saved_as_draft():
    channels = _validate_channel_section("embedding", [{"enabled": False}])

    assert channels[0].enabled is False


def test_disabled_empty_llm_channel_can_be_saved_as_draft():
    channels = _validate_channel_section("llm", [{"enabled": False}])

    assert channels[0].enabled is False


def test_embedding_backend_is_canonicalized_before_persisting():
    channels = _validate_channel_section("embedding", [{
        "backend": "API",
        "api_base": "http://localhost:8080/v1",
        "api_model": "embed-v1",
    }])

    assert channels[0].backend == "api"


def test_legacy_channel_backend_is_normalized_at_runtime(monkeypatch):
    marker = object()
    monkeypatch.setattr(channel_manager, "has_channels", lambda _section: True)
    monkeypatch.setattr(
        channel_manager,
        "get_channel",
        lambda _section: {
            "backend": " LOCAL ",
            "local_path": "model-path",
            "local_model": "",
        },
    )
    monkeypatch.setattr(
        llm_provider, "_create_local_embedding", lambda *_args: marker,
    )

    assert llm_provider._create_embedding() is marker


def test_enabled_local_embedding_requires_a_model_or_path():
    with pytest.raises(HTTPException) as exc_info:
        _validate_channel_section("embedding", [{"backend": "local"}])

    assert exc_info.value.status_code == 422
    assert "local_model or local_path" in str(exc_info.value.detail)


def test_explicit_zero_temperature_is_preserved():
    assert _resolve_temperature(0) == 0.0
    assert _resolve_temperature(None) == 0.7
