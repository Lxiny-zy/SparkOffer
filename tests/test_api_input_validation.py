"""Malformed JSON bodies must be rejected before reaching business logic."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.auth import get_current_user, require_owner
from backend.routers import (
    algorithm,
    assistant,
    favorites,
    interview,
    knowledge,
    profile,
    qa_arena,
    rag_eval,
    settings_router,
)


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    for module in (
        algorithm,
        assistant,
        favorites,
        interview,
        knowledge,
        profile,
        qa_arena,
        rag_eval,
        settings_router,
    ):
        app.include_router(module.router)
    app.dependency_overrides[get_current_user] = lambda: "validation-user"
    app.dependency_overrides[require_owner] = lambda: "validation-owner"
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/assistant/chat", {"message": []}),
        ("POST", "/api/assistant/chat", {"message": "   "}),
        ("POST", "/api/assistant/chat", {"messages": {"content": "x"}}),
        (
            "POST",
            "/api/qa-arena/sessions/session-1/chat",
            {"message": [], "images": []},
        ),
        (
            "POST",
            "/api/qa-arena/sessions/session-1/chat",
            {"message": "hello", "images": {}},
        ),
        (
            "PATCH",
            "/api/qa-arena/sessions/session-1",
            {"title": None},
        ),
        (
            "POST",
            "/api/qa-arena/sessions/session-1/ingest-knowledge",
            {"content": []},
        ),
        ("POST", "/api/knowledge/python/core", {"filename": None}),
        (
            "POST",
            "/api/interview/reference-answer",
            {"topic": None, "question": "What is Python?"},
        ),
        (
            "POST",
            "/api/interview/session/session-1/progress",
            {"current_index": "not-an-integer"},
        ),
        (
            "POST",
            "/api/interview/session/session-1/progress",
            {"partial_answers": []},
        ),
        ("POST", "/api/favorites", {"question": []}),
        ("PUT", "/api/favorites/favorite-1", {"tags": {"bad": "shape"}}),
        ("POST", "/api/favorites/export", {"ids": {"bad": "shape"}}),
        ("PUT", "/api/algorithm/cards/card-1", {"solution": []}),
        ("POST", "/api/algorithm/export", {"ids": {"bad": "shape"}}),
        ("POST", "/api/topics", {"name": "x" * 201}),
        ("GET", "/api/interview/history?limit=-1", None),
        ("GET", "/api/interview/rag-metrics?limit=0", None),
        ("GET", "/api/rag-eval/runs?limit=101", None),
        ("GET", "/api/admin/audit?offset=-1", None),
    ],
)
def test_malformed_json_body_returns_422(client, method, path, payload):
    response = client.request(method, path, json=payload)

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("module", "export_request", "list_attr", "export_attr"),
    [
        (
            favorites,
            favorites.FavoriteExportRequest(ids=[]),
            "_list_favs",
            "_export_favs",
        ),
        (
            algorithm,
            algorithm.AlgorithmExportRequest(ids=[]),
            "_list_algo",
            "_export_algo",
        ),
    ],
)
def test_unfiltered_export_refuses_unbounded_result(
    monkeypatch, module, export_request, list_attr, export_attr,
):
    monkeypatch.setattr(module, list_attr, lambda **_kwargs: {"items": [], "total": 1001})
    monkeypatch.setattr(
        module,
        export_attr,
        lambda **_kwargs: pytest.fail("oversized export must not be materialized"),
    )

    endpoint = (
        module.export_favorites_endpoint
        if module is favorites
        else module.export_algorithm_cards_endpoint
    )
    with pytest.raises(HTTPException) as exc_info:
        endpoint(export_request, user_id="validation-user")

    assert exc_info.value.status_code == 413
