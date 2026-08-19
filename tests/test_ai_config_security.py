import socket

import pytest

import backend.ai_config as ai_config
from backend.utils.outbound import OutboundTargetError, validate_api_base, validate_probe_targets, validate_proxy
from backend.routers.settings_router import _redact_probe_error


def _resolver_for(addresses):
    def resolver(_host, port, type=None):
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET,
             socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]
    return resolver


def test_effective_config_masks_provider_keys(monkeypatch):
    monkeypatch.setattr(ai_config, "_cache", {})
    monkeypatch.setattr(ai_config.settings, "api_key", "llm-live-secret")
    monkeypatch.setattr(ai_config.settings, "embedding_api_key", "embedding-live-secret")
    monkeypatch.setattr(ai_config.settings, "reranker_api_key", "rerank-live-secret")

    result = ai_config.get_all_effective()

    assert result["llm"]["api_key"] == {"value": ai_config.SECRET_MASK, "source": "env"}
    assert result["embedding"]["api_key"]["value"] == ai_config.SECRET_MASK
    assert result["reranker"]["api_key"]["value"] == ai_config.SECRET_MASK
    assert "live-secret" not in repr(result)


def test_channel_reads_are_redacted_and_do_not_mutate_cache(monkeypatch):
    raw = {
        "id": "llm-1",
        "name": "primary",
        "keys": ["first-live-secret", "second-live-secret"],
        "api_key": "single-live-secret",
    }
    monkeypatch.setattr(ai_config, "_cache", {"llm": {"channels": [raw]}})

    result = ai_config.get_channels("llm")

    assert result[0]["keys"] == [ai_config.SECRET_MASK, ai_config.SECRET_MASK]
    assert result[0]["api_key"] == ai_config.SECRET_MASK
    assert raw["keys"][0] == "first-live-secret"


def test_channel_reads_redact_url_userinfo_without_mutating_cache(monkeypatch):
    raw = {
        "id": "llm-1",
        "api_base": "https://user:base-secret@provider.example/v1",
        "proxy": "http://proxy-user:proxy-secret@proxy.example:8080",
        "keys": ["live-secret"],
    }
    monkeypatch.setattr(ai_config, "_cache", {"llm": {"channels": [raw]}})

    result = ai_config.get_channels("llm")[0]

    assert "base-secret" not in repr(result)
    assert "proxy-secret" not in repr(result)
    assert result["api_base"] == "https://<redacted>@provider.example/v1"
    assert result["proxy"] == "http://<redacted>@proxy.example:8080"
    assert raw["api_base"].startswith("https://user:base-secret@")


def test_probe_errors_redact_provider_credentials():
    error = _redact_probe_error(
        RuntimeError("upstream rejected first-live-secret"),
        "first-live-secret",
    )
    assert error == "upstream rejected [redacted]"
    assert "first-live-secret" not in error


def test_masked_channel_save_preserves_existing_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config, "AI_CONFIG_PATH", tmp_path / "ai_config.json")
    monkeypatch.setattr(
        ai_config,
        "_cache",
        {"llm": {"channels": [{"id": "llm-1", "keys": ["old-a", "old-b"], "model": "old"}]}},
    )
    monkeypatch.setattr(ai_config, "_reload_channel_manager", lambda: None)

    ai_config.save_channels({"llm": [{"id": "llm-1", "keys": [ai_config.SECRET_MASK, "new-b"], "model": "new"}]})

    stored = ai_config._cache["llm"]["channels"][0]
    assert stored["keys"] == ["old-a", "new-b"]
    assert stored["model"] == "new"
    assert ai_config.SECRET_MASK not in repr(stored)


def test_masked_channel_save_preserves_url_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config, "AI_CONFIG_PATH", tmp_path / "ai_config.json")
    old = {
        "id": "llm-1",
        "api_base": "https://user:base-secret@provider.example/v1",
        "proxy": "http://proxy-user:proxy-secret@proxy.example:8080",
        "keys": ["old-key"],
    }
    monkeypatch.setattr(ai_config, "_cache", {"llm": {"channels": [old]}})
    monkeypatch.setattr(ai_config, "_reload_channel_manager", lambda: None)

    redacted = ai_config.redact_channel(old)
    ai_config.save_channels({"llm": [redacted]})

    stored = ai_config._cache["llm"]["channels"][0]
    assert stored["api_base"] == old["api_base"]
    assert stored["proxy"] == old["proxy"]
    assert stored["keys"] == old["keys"]


def test_flat_save_preserves_redacted_api_base_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config, "AI_CONFIG_PATH", tmp_path / "ai_config.json")
    old_url = "https://user:base-secret@provider.example/v1"
    monkeypatch.setattr(ai_config, "_cache", {"llm": {"api_base": old_url}})
    monkeypatch.setattr(ai_config, "_reload_channel_manager", lambda: None)

    displayed = ai_config.redact_channel({"api_base": old_url})["api_base"]
    ai_config.save_ai_config({"llm": {"api_base": displayed}})

    assert ai_config._cache["llm"]["api_base"] == old_url
    assert "base-secret" not in repr(ai_config.get_all_effective())


def test_flat_save_accepts_explicit_api_base_replacement(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config, "AI_CONFIG_PATH", tmp_path / "ai_config.json")
    monkeypatch.setattr(
        ai_config,
        "_cache",
        {"llm": {"api_base": "https://old.example/v1"}},
    )
    monkeypatch.setattr(ai_config, "_reload_channel_manager", lambda: None)

    ai_config.save_ai_config({"llm": {"api_base": "https://new.example/v1"}})

    assert ai_config._cache["llm"]["api_base"] == "https://new.example/v1"


def test_public_https_api_base_is_accepted_without_network_dns():
    target = validate_api_base(
        "https://provider.example/v1",
        resolver=_resolver_for(["93.184.216.34"]),
    )
    assert target.parsed.hostname == "provider.example"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/v1",
        "http://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/v1",
    ],
)
def test_probe_rejects_private_or_plaintext_api_targets(url):
    with pytest.raises(OutboundTargetError):
        validate_api_base(url, resolver=_resolver_for([url.split("//", 1)[1].split(":", 1)[0]]))


def test_probe_rejects_dns_rebinding_to_mixed_public_and_private_addresses():
    with pytest.raises(OutboundTargetError, match="private"):
        validate_api_base(
            "https://provider.example/v1",
            resolver=_resolver_for(["93.184.216.34", "127.0.0.1"]),
        )


def test_probe_rejects_embedded_api_credentials_and_private_proxy():
    with pytest.raises(OutboundTargetError, match="credentials"):
        validate_api_base(
            "https://user:pass@provider.example/v1",
            resolver=_resolver_for(["93.184.216.34"]),
        )

    with pytest.raises(OutboundTargetError, match="query"):
        validate_api_base(
            "https://provider.example/v1?api_key=live-secret",
            resolver=_resolver_for(["93.184.216.34"]),
        )

    with pytest.raises(OutboundTargetError, match="private"):
        validate_proxy(
            "http://user:pass@127.0.0.1:7890",
            resolver=_resolver_for(["127.0.0.1"]),
        )

    with pytest.raises(OutboundTargetError, match="private"):
        validate_probe_targets(
            "",
            "http://127.0.0.1:7890",
            resolver=_resolver_for(["127.0.0.1"]),
        )


def test_probe_validates_public_proxy_and_returns_normalized_targets():
    base, proxy = validate_probe_targets(
        "https://provider.example/v1",
        "http://user:pass@proxy.example:8080",
        resolver=_resolver_for(["93.184.216.34"]),
    )
    assert base is not None
    assert proxy is not None
    assert proxy.parsed.hostname == "proxy.example"
