from backend import llm_provider


def test_reasoning_effort_is_passed_as_explicit_extra_body(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_provider, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(
        llm_provider,
        "_build_http_clients",
        lambda *_args, **_kwargs: (object(), object()),
    )

    channel = {
        "model": "gpt-5.5",
        "api_key": "secret",
        "api_base": "https://provider.example/v1",
        "temperature": 0.6,
        "max_tokens": 80000,
        "reasoning_effort": "medium",
    }
    llm_provider.ResilientChatModel()._make_llm(channel)

    assert captured["extra_body"] == {"reasoning_effort": "medium"}
    assert "model_kwargs" not in captured
    assert captured["max_tokens"] == 80000
