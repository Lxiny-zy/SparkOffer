from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from backend.models import EndDrillRequest
from backend.routers import interview


def _session_payload(mode: str, *, fresh: bool, valid: bool = True) -> dict:
    label = "fresh" if fresh else "stale"
    question_id = f"{label}-q"
    question = {
        "id": question_id,
        "question": f"{label} question",
        "difficulty": 4 if fresh else 2,
    }
    return {
        "mode": mode,
        "topic": f"{label}-topic",
        "questions": [question],
        "scores": [{
            "question_id": question_id,
            "score": 8.5 if valid else None,
            "assessment": f"{label} assessment",
        }],
        "overall": {
            "avg_score": "8.5" if valid else None,
            "payload": label,
        },
        "transcript": [
            {
                "role": "assistant",
                "content": question["question"],
                "question_id": question_id,
            },
            {
                "role": "user",
                "content": f"{label} answer",
                "question_id": question_id,
            },
        ],
        "meta": {
            "payload": label,
            "position": f"{label} position",
            "sync_claim_token": "sync-token" if fresh else None,
        },
    }


@pytest.mark.parametrize("mode", ["topic_drill", "jd_prep"])
def test_manual_sync_uses_complete_post_claim_snapshot(monkeypatch, mode):
    stale = _session_payload(mode, fresh=False)
    fresh = _session_payload(mode, fresh=True)
    completed = {"meta": {"synced_at": "2026-07-23T00:00:00"}}
    snapshots = iter([stale, fresh, completed])
    captured = {}
    deleted = []

    monkeypatch.setattr(
        interview, "get_session", lambda *args, **kwargs: next(snapshots),
    )
    monkeypatch.setattr(
        interview, "try_claim_session_sync",
        lambda *args, **kwargs: "sync-token",
    )
    monkeypatch.setattr(
        interview, "del_live",
        lambda store, session_id, user_id: deleted.append(
            (store, session_id, user_id)
        ),
    )

    async def apply_drill(
        session_id, topic, questions, answers, scores, overall, user_id, claim_token,
    ):
        captured.update({
            "session_id": session_id,
            "topic": topic,
            "questions": questions,
            "answers": answers,
            "scores": scores,
            "overall": overall,
            "user_id": user_id,
            "claim_token": claim_token,
        })
        return True

    async def apply_jd(
        session_id, questions, answers, scores, overall, meta, user_id, claim_token,
        **kwargs,
    ):
        captured.update({
            "session_id": session_id,
            "questions": questions,
            "answers": answers,
            "scores": scores,
            "overall": overall,
            "meta": meta,
            "user_id": user_id,
            "claim_token": claim_token,
        })
        return True

    monkeypatch.setattr(interview, "_apply_drill_side_effects", apply_drill)
    monkeypatch.setattr(interview, "_apply_job_prep_side_effects", apply_jd)

    result = asyncio.run(
        interview.sync_session_side_effects("session-1", user_id="user-1")
    )

    assert result == {
        "status": "synced",
        "synced_at": "2026-07-23T00:00:00",
    }
    assert captured["session_id"] == "session-1"
    assert captured["questions"] == fresh["questions"]
    assert captured["answers"] == [{
        "question_id": "fresh-q",
        "answer": "fresh answer",
    }]
    assert captured["scores"][0] == {
        "question_id": "fresh-q",
        "score": 8.5,
        "assessment": "fresh assessment",
        "difficulty": 4,
    }
    assert captured["overall"] == {"avg_score": 8.5, "payload": "fresh"}
    assert captured["user_id"] == "user-1"
    assert captured["claim_token"] == "sync-token"
    if mode == "topic_drill":
        assert captured["topic"] == "fresh-topic"
        assert deleted == [(interview.drill_sessions, "session-1", "user-1")]
    else:
        assert captured["meta"] == fresh["meta"]
        assert deleted == [(interview.job_prep_sessions, "session-1", "user-1")]


@pytest.mark.parametrize("mode", ["topic_drill", "jd_prep"])
def test_manual_sync_fresh_invalid_score_skips_all_side_effects(monkeypatch, mode):
    stale = _session_payload(mode, fresh=False)
    fresh_invalid = _session_payload(mode, fresh=True, valid=False)
    snapshots = iter([stale, fresh_invalid])
    aborted = []
    side_effects = []

    monkeypatch.setattr(
        interview, "get_session", lambda *args, **kwargs: next(snapshots),
    )
    monkeypatch.setattr(
        interview, "try_claim_session_sync",
        lambda *args, **kwargs: "sync-token",
    )
    monkeypatch.setattr(
        interview,
        "abort_session_sync_claim",
        lambda *args, **kwargs: aborted.append(kwargs["claim_token"]) or True,
    )

    async def unexpected_side_effect(*args, **kwargs):
        side_effects.append((args, kwargs))
        return True

    monkeypatch.setattr(
        interview, "_apply_drill_side_effects", unexpected_side_effect,
    )
    monkeypatch.setattr(
        interview, "_apply_job_prep_side_effects", unexpected_side_effect,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            interview.sync_session_side_effects("session-1", user_id="user-1")
        )

    assert exc_info.value.status_code == 400
    assert side_effects == []
    assert aborted == ["sync-token"]


def _parse_sse_chunks(chunks: list[str]) -> list[dict]:
    return [
        json.loads(line[6:])
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.parametrize("mode", ["topic_drill", "jd_prep"])
def test_stale_evaluation_winner_token_never_completes_or_deletes_live(
    monkeypatch, mode,
):
    questions = [{"id": "q1", "question": "Question", "difficulty": 3}]
    existing = {
        "mode": mode,
        "topic": "python",
        "questions": questions,
        "scores": [],
        "overall": {},
        "transcript": [],
        "meta": {},
    }
    winner = {
        **existing,
        "review": "winner review",
        "meta": {
            "synced_at": "2026-07-23T00:00:00",
            "evaluation_claim_token": "winner-eval-token",
        },
    }
    get_session_calls = 0
    deleted = []
    released = []

    def get_session(*args, **kwargs):
        nonlocal get_session_calls
        get_session_calls += 1
        return existing if get_session_calls == 1 else winner

    if mode == "topic_drill":
        entry = {
            "topic": "python",
            "questions": questions,
            "user_id": "user-1",
        }
        live_store = interview.drill_sessions
    else:
        entry = {
            "questions": questions,
            "preview": {},
            "meta": {"position": "Backend Engineer"},
            "user_id": "user-1",
        }
        live_store = interview.job_prep_sessions

    monkeypatch.setattr(interview, "get_session", get_session)
    monkeypatch.setattr(
        interview,
        "get_live",
        lambda store, *args: entry if store is live_store else None,
    )
    monkeypatch.setattr(
        interview, "try_claim_session_evaluation",
        lambda *args, **kwargs: "stale-eval-token",
    )
    monkeypatch.setattr(
        interview, "release_session_evaluation_claim",
        lambda *args, **kwargs: released.append(kwargs["claim_token"]) or True,
    )
    monkeypatch.setattr(interview, "save_drill_answers", lambda *args, **kwargs: True)
    monkeypatch.setattr(interview, "save_review", lambda *args, **kwargs: True)
    monkeypatch.setattr(interview, "try_claim_session_sync", lambda *args, **kwargs: None)
    monkeypatch.setattr(interview, "format_drill_review", lambda *args, **kwargs: "review")
    monkeypatch.setattr(interview, "format_job_prep_review", lambda *args, **kwargs: "review")
    monkeypatch.setattr(
        interview,
        "del_live",
        lambda *args, **kwargs: deleted.append((args, kwargs)),
    )

    async def evaluation_stream(*args, **kwargs):
        yield interview.sse_event({
            "type": "eval_result",
            "data": {
                "scores": [{"question_id": "q1", "score": 8}],
                "overall": {"avg_score": 8},
            },
        })

    monkeypatch.setattr(
        interview, "stream_evaluate_drill_answers", evaluation_stream,
    )
    monkeypatch.setattr(
        interview, "stream_evaluate_job_prep_answers", evaluation_stream,
    )

    from backend.graphs import decoupled_eval
    from backend.storage import rag_metrics_store

    monkeypatch.setattr(decoupled_eval, "has_small_tier", lambda: False)
    monkeypatch.setattr(
        rag_metrics_store, "save_rag_metrics", lambda *args, **kwargs: True,
    )

    async def consume():
        response = await interview.end_interview(
            "session-1",
            EndDrillRequest(answers=[{
                "question_id": "q1",
                "answer": "Answer",
            }]),
            user_id="user-1",
        )
        return [chunk async for chunk in response.body_iterator]

    events = _parse_sse_chunks(asyncio.run(consume()))

    assert "complete" not in {event.get("type") for event in events}
    assert [event.get("type") for event in events][-2:] == ["error", "done"]
    assert deleted == []
    assert released == ["stale-eval-token"]
    assert get_session_calls >= 2
