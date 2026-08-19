import pytest

from backend.ai_config import SECRET_MASK, get_all_effective, redact_channel
from backend.utils.outbound import OutboundTargetError, validate_api_base, validate_probe_targets


def _resolver(address: str):
    def resolve(_host, _port, **_kwargs):
        return [(None, None, None, None, (address, 0))]
    return resolve


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
def test_probe_rejects_non_public_resolved_addresses(address):
    with pytest.raises(OutboundTargetError, match="private or non-public"):
        validate_probe_targets(
            "https://provider.example/v1",
            resolver=_resolver(address),
        )


def test_probe_requires_https_for_public_targets():
    with pytest.raises(OutboundTargetError, match="HTTPS"):
        validate_api_base(
            "http://provider.example/v1",
            resolver=_resolver("93.184.216.34"),
        )


def test_redact_channel_does_not_mutate_or_leak_key():
    original = {"id": "one", "keys": ["super-secret"], "api_key": "another-secret"}
    redacted = redact_channel(original)

    assert redacted["keys"] == [SECRET_MASK]
    assert redacted["api_key"] == SECRET_MASK
    assert original["keys"] == ["super-secret"]
    assert "super-secret" not in repr(redacted)
    assert "another-secret" not in repr(redacted)


def test_effective_secret_values_are_masked(monkeypatch):
    import backend.ai_config as ai_config

    monkeypatch.setattr(ai_config, "get_effective", lambda _section, key: {
        "api_key": "provider-secret",
        "embedding_api_key": "embedding-secret",
    }.get(key, "value"))
    monkeypatch.setattr(ai_config, "_detect_source", lambda *_args: "env")

    result = get_all_effective()
    assert result["llm"]["api_key"]["value"] == SECRET_MASK
    assert result["embedding"]["api_key"]["value"] == SECRET_MASK
