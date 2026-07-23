from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import backend.storage.database as database
import backend.embedding_tasks as embedding_tasks
import backend.knowledge_evolution as knowledge_evolution
import backend.memory as memory
import backend.indexer as indexer
import backend.spaced_repetition as spaced_repetition
import backend.vector_memory as vector_memory
from backend.models import ChatRequest, EndDrillRequest, InterviewMode
from backend.routers import interview
from backend.storage.sessions import (
    create_session,
    get_session,
    mark_session_sync_step,
    mark_session_synced,
    release_session_sync_claim,
    release_session_evaluation_claim,
    session_sync_step_result,
    session_sync_steps,
    try_claim_session_evaluation,
    try_claim_session_sync,
)


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "session-sync.db"
    database._local.conn = None
    try:
        yield database.DB_PATH
    finally:
        temp_conn = getattr(database._local, "conn", None)
        if temp_conn is not None:
            temp_conn.close()
        database.DB_PATH = original_path
        database._local.conn = original_conn


def test_session_sync_claim_is_one_time_until_marked_synced(isolated_db):
    database.init_all_tables()
    create_session("s1", "topic_drill", "python", user_id="u1")

    token = try_claim_session_sync("s1", user_id="u1")
    assert isinstance(token, str) and token
    assert try_claim_session_sync("s1", user_id="u1") is None

    claimed = get_session("s1", user_id="u1")
    assert claimed["meta"].get("sync_claimed_at")
    assert not claimed["meta"].get("synced_at")

    assert mark_session_sync_step(
        "s1", "profile", user_id="u1", claim_token=token,
    ) is True
    assert session_sync_steps("s1", user_id="u1") == {"profile"}
    assert mark_session_synced("s1", user_id="u1", claim_token=token) is True

    synced = get_session("s1", user_id="u1")
    assert synced["meta"].get("synced_at")
    assert "sync_claimed_at" not in synced["meta"]
    assert try_claim_session_sync("s1", user_id="u1") is None


def test_sync_step_can_persist_reusable_result(isolated_db):
    database.init_all_tables()
    create_session("step-result", "resume", user_id="u1")
    token = try_claim_session_sync("step-result", user_id="u1")

    result = {
        "extraction": {"avg_score": 8.5},
        "overall": {"avg_score": 8.5},
    }
    assert mark_session_sync_step(
        "step-result", "profile", user_id="u1",
        claim_token=token, result=result,
    ) is True

    assert session_sync_step_result(
        "step-result", "profile", user_id="u1",
    ) == result

def test_stale_worker_cannot_release_or_finish_new_sync_claim(isolated_db):
    database.init_all_tables()
    create_session("s2", "topic_drill", "python", user_id="u1")
    old_token = try_claim_session_sync("s2", user_id="u1")

    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_set(meta, '$.sync_claimed_at', '2000-01-01T00:00:00') "
        "WHERE session_id = 's2' AND user_id = 'u1'"
    )
    conn.commit()
    new_token = try_claim_session_sync("s2", user_id="u1")

    assert new_token and new_token != old_token
    assert release_session_sync_claim(
        "s2", user_id="u1", claim_token=old_token,
    ) is False
    assert mark_session_synced(
        "s2", user_id="u1", claim_token=old_token,
    ) is False
    assert mark_session_synced(
        "s2", user_id="u1", claim_token=new_token,
    ) is True


def test_manual_sync_takes_over_expired_evaluation_claim(isolated_db):
    database.init_all_tables()
    create_session("stale-eval", "topic_drill", "python", user_id="u1")
    old_eval = try_claim_session_evaluation("stale-eval", user_id="u1")
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_set(meta, '$.evaluation_claimed_at', "
        "'2000-01-01T00:00:00') WHERE session_id = 'stale-eval'"
    )
    conn.commit()

    sync_token = try_claim_session_sync("stale-eval", user_id="u1")

    assert sync_token and sync_token != old_eval
    claimed = get_session("stale-eval", user_id="u1")
    assert claimed["meta"].get("sync_pending_at")
    assert "evaluation_claim_token" not in claimed["meta"]
    assert release_session_evaluation_claim(
        "stale-eval", user_id="u1", claim_token=old_eval,
    ) is False
    assert mark_session_synced(
        "stale-eval", user_id="u1", claim_token=sync_token,
    ) is True


def test_partial_drill_sync_blocks_a_new_evaluation_until_recovered(isolated_db):
    database.init_all_tables()
    create_session("pending", "topic_drill", "python", user_id="u1")
    eval_token = try_claim_session_evaluation("pending", user_id="u1")
    sync_token = try_claim_session_sync(
        "pending", user_id="u1", evaluation_token=eval_token,
    )
    assert sync_token
    assert release_session_sync_claim(
        "pending", user_id="u1", claim_token=sync_token,
    ) is True
    assert release_session_evaluation_claim(
        "pending", user_id="u1", claim_token=eval_token,
    ) is True

    assert try_claim_session_evaluation("pending", user_id="u1") is None
    recovery = try_claim_session_sync("pending", user_id="u1")
    assert recovery
    assert mark_session_synced(
        "pending", user_id="u1", claim_token=recovery,
    ) is True
    assert try_claim_session_evaluation("pending", user_id="u1")


def test_new_resume_evaluation_fences_expired_sync_worker(isolated_db):
    database.init_all_tables()
    create_session("resume-fence", "resume", user_id="u1")
    old_sync = try_claim_session_sync("resume-fence", user_id="u1")
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_set(meta, '$.sync_claimed_at', "
        "'2000-01-01T00:00:00') WHERE session_id = 'resume-fence'"
    )
    conn.commit()

    new_eval = try_claim_session_evaluation("resume-fence", user_id="u1")

    assert new_eval and new_eval != old_sync
    assert mark_session_sync_step(
        "resume-fence", "profile", user_id="u1", claim_token=old_sync,
    ) is False
    assert mark_session_synced(
        "resume-fence", user_id="u1", claim_token=old_sync,
    ) is False


def test_evaluation_claim_is_token_scoped(isolated_db):
    database.init_all_tables()
    create_session("s3", "topic_drill", "python", user_id="u1")

    token = try_claim_session_evaluation("s3", user_id="u1")
    assert token
    assert try_claim_session_evaluation("s3", user_id="u1") is None
    assert release_session_evaluation_claim(
        "s3", user_id="u1", claim_token="wrong",
    ) is False
    assert release_session_evaluation_claim(
        "s3", user_id="u1", claim_token=token,
    ) is True
    assert try_claim_session_evaluation("s3", user_id="u1")


def test_evaluation_and_manual_sync_claims_are_mutually_exclusive(isolated_db):
    database.init_all_tables()
    create_session("eval-first", "topic_drill", "python", user_id="u1")
    eval_token = try_claim_session_evaluation("eval-first", user_id="u1")

    assert try_claim_session_sync("eval-first", user_id="u1") is None
    sync_token = try_claim_session_sync(
        "eval-first", user_id="u1", evaluation_token=eval_token,
    )
    assert sync_token
    assert release_session_sync_claim(
        "eval-first", user_id="u1", claim_token=sync_token,
    ) is True
    assert release_session_evaluation_claim(
        "eval-first", user_id="u1", claim_token=eval_token,
    ) is True

    create_session("sync-first", "topic_drill", "python", user_id="u1")
    sync_token = try_claim_session_sync("sync-first", user_id="u1")
    assert sync_token
    assert try_claim_session_evaluation("sync-first", user_id="u1") is None


def test_answers_and_scores_are_normalized_before_side_effects():
    request = EndDrillRequest(answers={"1": "answer", "2": {"answer": None}})
    assert request.answers == [
        {"question_id": "1", "answer": "answer"},
        {"question_id": "2", "answer": ""},
    ]

    scores = interview._normalize_scores([
        {"question_id": 1, "score": True},
        {"question_id": 2, "score": "12"},
        {"question_id": 3, "score": "nan"},
        {"question_id": {"malformed": True}, "score": "4"},
    ])
    assert [row["score"] for row in scores] == [None, 10.0, None, 4.0]
    assert interview._has_valid_side_effect_score([scores[0]]) is False
    assert interview._has_valid_side_effect_score([scores[1]]) is True


def test_side_effect_payload_is_canonicalized_by_question_id():
    questions = [
        {"id": 1, "question": "same text", "difficulty": 2},
        {"id": 2, "question": "same text", "difficulty": 4},
        {"id": 3, "question": "third", "difficulty": 3},
    ]
    body = EndDrillRequest(answers=[
        {"question_id": "2", "answer": "second"},
        {"question_id": 1, "answer": "first"},
    ])

    answers = interview._resolve_answers(body, None, questions)
    scores = interview._normalize_scores([
        {"question_id": "2", "score": 9},
        {"question_id": 1, "score": 7},
    ], questions)

    assert answers == [
        {"question_id": 1, "answer": "first"},
        {"question_id": 2, "answer": "second"},
        {"question_id": 3, "answer": ""},
    ]
    assert [row["question_id"] for row in scores] == [1, 2, 3]
    assert [row["score"] for row in scores] == [7.0, 9.0, None]
    assert [row["difficulty"] for row in scores] == [2, 4, 3]


def test_scores_without_ids_match_unique_question_text_before_position():
    questions = [
        {"id": 1, "question": "first", "difficulty": 2},
        {"id": 2, "question": "second", "difficulty": 4},
    ]

    scores = interview._normalize_scores([
        {"question": "second", "score": 9},
        {"question": "first", "score": 7},
    ], questions)

    assert [row["question_id"] for row in scores] == [1, 2]
    assert [row["score"] for row in scores] == [7.0, 9.0]


def test_scores_with_ambiguous_question_text_are_not_positionally_guessed():
    questions = [
        {"id": 1, "question": "duplicate", "difficulty": 2},
        {"id": 2, "question": "duplicate", "difficulty": 4},
    ]

    scores = interview._normalize_scores([
        {"question": "duplicate", "score": 8},
    ], questions)

    assert [row["score"] for row in scores[:2]] == [None, None]
    assert scores[2]["question"] == "duplicate"
    assert scores[2]["score"] == 8.0
    assert "question_id" not in scores[2]


def test_transcript_recovery_uses_question_ids_for_duplicate_text(isolated_db):
    database.init_all_tables()
    questions = [
        {"id": 1, "question": "duplicate"},
        {"id": 2, "question": "duplicate"},
    ]
    create_session(
        "duplicate-text", "topic_drill", "python",
        questions=questions, user_id="u1",
    )
    from backend.storage.sessions import save_drill_answers

    save_drill_answers(
        "duplicate-text",
        [
            {"question_id": 2, "answer": "answer two"},
            {"question_id": 1, "answer": "answer one"},
        ],
        user_id="u1",
    )
    stored = get_session("duplicate-text", user_id="u1")

    assert interview._answers_from_transcript(
        questions, stored["transcript"],
    ) == [
        {"question_id": 1, "answer": "answer one"},
        {"question_id": 2, "answer": "answer two"},
    ]


def test_save_drill_answers_tolerates_structured_question_ids(isolated_db):
    database.init_all_tables()
    questions = [{"id": 1, "question": "Q"}]
    create_session(
        "structured-id", "topic_drill", "python",
        questions=questions, user_id="u1",
    )
    from backend.storage.sessions import save_drill_answers

    assert save_drill_answers(
        "structured-id",
        [{"question_id": {"bad": "id"}, "answer": "ignored"}],
        user_id="u1",
    ) is True
    stored = get_session("structured-id", user_id="u1")
    assert len(stored["transcript"]) == 1
    assert {
        key: stored["transcript"][0][key]
        for key in ("role", "content", "question_id")
    } == {
        "role": "assistant",
        "content": "Q",
        "question_id": 1,
    }
    assert stored["transcript"][0]["time"]


def test_failed_knowledge_retry_skips_completed_profile_and_sr(isolated_db, monkeypatch):
    database.init_all_tables()
    create_session("s4", "topic_drill", "python", user_id="u1")
    calls = {"sr": 0, "profile": 0, "extract": 0, "freq": 0}

    monkeypatch.setattr(
        spaced_repetition,
        "update_weak_point_sr",
        lambda *args, **kwargs: calls.__setitem__("sr", calls["sr"] + 1),
    )

    async def profile(*args, **kwargs):
        calls["profile"] += 1

    async def extract(*args, **kwargs):
        calls["extract"] += 1
        return calls["extract"] > 1

    async def freq(*args, **kwargs):
        calls["freq"] += 1
        return True

    monkeypatch.setattr(interview, "_update_drill_profile", profile)
    monkeypatch.setattr(knowledge_evolution, "extract_and_writeback", extract)
    monkeypatch.setattr(knowledge_evolution, "collect_high_freq", freq)
    questions = [{"id": 1, "question": "Q", "difficulty": 3}]
    answers = [{"question_id": 1, "answer": "A"}]
    scores = [{"question_id": 1, "score": 5.0, "weak_point": "wp", "difficulty": 3}]

    first_token = try_claim_session_sync("s4", user_id="u1")
    with pytest.raises(RuntimeError, match="knowledge extraction"):
        asyncio.run(interview._apply_drill_side_effects(
            "s4", "python", questions, answers, scores, {}, "u1", first_token,
        ))
    assert release_session_sync_claim(
        "s4", user_id="u1", claim_token=first_token,
    ) is True
    assert session_sync_steps("s4", user_id="u1") == {"sr", "profile"}

    second_token = try_claim_session_sync("s4", user_id="u1")
    assert asyncio.run(interview._apply_drill_side_effects(
        "s4", "python", questions, answers, scores, {}, "u1", second_token,
    )) is True
    assert calls == {"sr": 1, "profile": 1, "extract": 2, "freq": 1}


def test_deleted_frozen_jd_topic_is_marked_skipped_without_recreation(
    isolated_db, monkeypatch,
):
    database.init_all_tables()
    create_session("deleted-target", "jd_prep", user_id="u1")
    token = try_claim_session_sync(
        "deleted-target",
        user_id="u1",
        target_group="knowledge",
        target_topics=["deleted"],
    )

    async def update_profile(*args, **kwargs):
        return None

    async def unexpected_writer(*args, **kwargs):
        raise AssertionError("deleted target must not be recreated")

    async def confirm(*args, **kwargs):
        return None

    monkeypatch.setattr(interview, "_update_job_prep_profile", update_profile)
    monkeypatch.setattr(interview, "load_topics", lambda *args: {"active": {}})
    monkeypatch.setattr(knowledge_evolution, "extract_and_writeback", unexpected_writer)
    monkeypatch.setattr(knowledge_evolution, "collect_high_freq", unexpected_writer)
    monkeypatch.setattr(interview, "_confirm_profile_operations", confirm)

    assert asyncio.run(interview._apply_job_prep_side_effects(
        "deleted-target",
        [],
        [],
        [],
        {},
        {},
        "u1",
        token,
        target_topics=["deleted"],
    )) is True
    stored = get_session("deleted-target", user_id="u1")
    steps = stored["meta"]["sync_steps"]
    assert steps["knowledge_extract:deleted"]["result"] == {
        "status": "skipped",
        "reason": "topic_deleted",
    }
    assert steps["high_freq:deleted"]["result"] == {
        "status": "skipped",
        "reason": "topic_deleted",
    }
    assert stored["meta"].get("synced_at")


def test_profile_target_is_idempotent_when_session_step_mark_fails(
    isolated_db, tmp_path, monkeypatch,
):
    database.init_all_tables()
    create_session("s5", "topic_drill", "python", user_id="u1")
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(memory, "_profile_path", lambda _user_id: profile_path)
    monkeypatch.setattr(memory, "_save_insight", lambda *args, **kwargs: None)

    async def index_memory(**_kwargs):
        return None

    monkeypatch.setattr(vector_memory, "index_session_memory", index_memory)

    original_mark = interview.mark_session_sync_step
    failed = {"profile": True}

    def flaky_mark(session_id, step, **kwargs):
        if step == "profile" and failed["profile"]:
            failed["profile"] = False
            return False
        return original_mark(session_id, step, **kwargs)

    monkeypatch.setattr(interview, "mark_session_sync_step", flaky_mark)
    questions = [{"id": 1, "question": "Q", "difficulty": 3}]
    answers = [{"question_id": 1, "answer": "A"}]
    scores = [{"question_id": 1, "score": 6.0, "difficulty": 3}]
    overall = {"avg_score": 6.0, "topic_mastery": {}}

    first_token = try_claim_session_sync("s5", user_id="u1")
    with pytest.raises(interview._SyncClaimLost):
        asyncio.run(interview._apply_drill_side_effects(
            "s5", "python", questions, answers, scores, overall,
            "u1", first_token,
        ))
    assert release_session_sync_claim(
        "s5", user_id="u1", claim_token=first_token,
    ) is True

    second_token = try_claim_session_sync("s5", user_id="u1")
    assert asyncio.run(interview._apply_drill_side_effects(
        "s5", "python", questions, answers, scores, overall,
        "u1", second_token,
    )) is True

    profile = memory._load_profile("u1")
    assert profile["stats"]["total_sessions"] == 1
    assert "session-sync:s5:profile" in profile["_applied_operations"]


@pytest.mark.parametrize("failure_stage", ["insight", "vector"])
def test_profile_operation_retry_finishes_artifacts_without_double_counting(
    tmp_path, monkeypatch, failure_stage,
):
    profile_path = tmp_path / "profile.json"
    insights_dir = tmp_path / "insights"
    operation_id = f"session-sync:artifact-{failure_stage}:profile"
    insight_attempts = []
    vector_attempts = []

    monkeypatch.setattr(memory, "_profile_path", lambda _user_id: profile_path)
    monkeypatch.setattr(memory, "_insights_dir", lambda _user_id: insights_dir)

    save_insight = memory._save_insight

    def flaky_save_insight(*args, **kwargs):
        insight_attempts.append(True)
        if failure_stage == "insight" and len(insight_attempts) == 1:
            raise RuntimeError("insight failed once")
        return save_insight(*args, **kwargs)

    async def flaky_index_memory(**kwargs):
        vector_attempts.append(kwargs)
        if failure_stage == "vector" and len(vector_attempts) == 1:
            raise RuntimeError("vector failed once")

    monkeypatch.setattr(memory, "_save_insight", flaky_save_insight)
    monkeypatch.setattr(vector_memory, "index_session_memory", flaky_index_memory)

    async def update_profile():
        return await memory.llm_update_profile(
            mode="topic_drill",
            topic="python",
            new_weak_points=[],
            new_strong_points=[],
            topic_mastery={},
            communication={},
            user_id="u1",
            session_summary="durable summary",
            avg_score=8.0,
            answer_count=1,
            operation_id=operation_id,
        )

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed once"):
        asyncio.run(update_profile())

    failed_profile = memory._load_profile("u1")
    assert failed_profile["stats"]["total_sessions"] == 1
    assert operation_id in failed_profile["_applied_operations"]

    assert asyncio.run(update_profile()) is False

    recovered_profile = memory._load_profile("u1")
    assert recovered_profile["stats"]["total_sessions"] == 1
    insight_text = next(insights_dir.glob("*.md")).read_text(encoding="utf-8")
    assert insight_text.count("durable summary") == 1
    assert len(vector_attempts) == (1 if failure_stage == "insight" else 2)
    assert all(call["session_id"] == operation_id for call in vector_attempts)


def test_resume_profile_operation_reuses_persisted_result(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(memory, "_profile_path", lambda _user_id: profile_path)
    extraction = {
        "session_summary": "cached",
        "weak_points": [{"point": "weak"}],
        "strong_points": [],
        "avg_score": 8.0,
    }
    with memory.profile_transaction("u1") as profile:
        memory._record_applied_operation(
            profile, "session-sync:resume-cache:profile", result=extraction,
        )

    monkeypatch.setattr(
        memory, "get_langchain_llm",
        lambda: pytest.fail("cached profile operation must not call the LLM"),
    )
    result = asyncio.run(memory.update_profile_after_interview(
        mode="resume",
        topic=None,
        messages=[],
        user_id="u1",
        operation_id="session-sync:resume-cache:profile",
    ))
    assert result == extraction


def test_knowledge_operation_markers_deduplicate_and_recover_index(
    tmp_path, monkeypatch,
):
    topic_dir = tmp_path / "topic"
    topic_dir.mkdir()
    llm_calls = []
    scheduled = []
    dirty = []

    class FakeLLM:
        async def ainvoke(self, _messages):
            llm_calls.append(True)
            return SimpleNamespace(
                content="## Durable insight\nThis extraction is long enough to persist.",
            )

    monkeypatch.setattr(knowledge_evolution, "_get_topic_dir", lambda *args: topic_dir)
    monkeypatch.setattr(knowledge_evolution, "get_langchain_llm", lambda: FakeLLM())
    monkeypatch.setattr(
        embedding_tasks,
        "schedule_incremental_insert",
        lambda *args, **kwargs: scheduled.append((args, kwargs)),
    )
    monkeypatch.setattr(indexer, "mark_topic_index_dirty", lambda *args: dirty.append(args))

    async def run():
        first = await knowledge_evolution.extract_and_writeback(
            "python", [{"question": "Q"}], [{"answer": "A"}],
            [{"score": 8}], "u1", operation_id="session-sync:k1:extract",
        )
        second = await knowledge_evolution.extract_and_writeback(
            "python", [{"question": "Q"}], [{"answer": "A"}],
            [{"score": 8}], "u1", operation_id="session-sync:k1:extract",
        )
        return first, second

    assert asyncio.run(run()) == (True, True)
    knowledge_file = topic_dir / "自动沉淀.md"
    assert knowledge_file.read_text(encoding="utf-8").count(
        "session-sync-operation:"
    ) == 1
    assert len(llm_calls) == 1
    assert len(scheduled) == 1
    assert len(dirty) == 1

    high_freq_root = tmp_path / "high-freq"
    monkeypatch.setattr(
        knowledge_evolution,
        "settings",
        SimpleNamespace(user_high_freq_path=lambda _user_id: high_freq_root),
    )

    async def run_high_freq():
        first = await knowledge_evolution.collect_high_freq(
            "python", [{"question": "Q"}], [{"score": 5}], "u1",
            operation_id="session-sync:k1:freq",
        )
        second = await knowledge_evolution.collect_high_freq(
            "python", [{"question": "Q"}], [{"score": 5}], "u1",
            operation_id="session-sync:k1:freq",
        )
        return first, second

    assert asyncio.run(run_high_freq()) == (True, True)
    freq_file = high_freq_root / "python.md"
    assert freq_file.read_text(encoding="utf-8").count(
        "session-sync-operation:"
    ) == 1


@pytest.mark.parametrize("failure_stage", ["review", "side_effect"])
def test_drill_persistence_failure_never_emits_complete(
    monkeypatch, failure_stage,
):
    existing = {
        "mode": "topic_drill",
        "topic": "python",
        "questions": [{"id": 1, "question": "Q", "difficulty": 3}],
        "scores": [],
        "meta": {},
        "transcript": [],
    }
    entry = {
        "topic": "python",
        "questions": existing["questions"],
        "user_id": "u1",
    }
    released = []
    sync_released = []
    deleted = []

    monkeypatch.setattr(interview, "get_session", lambda *args, **kwargs: existing)
    monkeypatch.setattr(
        interview,
        "get_live",
        lambda store, *args: entry if store is interview.drill_sessions else None,
    )
    monkeypatch.setattr(
        interview, "try_claim_session_evaluation", lambda *args, **kwargs: "eval-token",
    )
    monkeypatch.setattr(interview, "save_drill_answers", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        interview, "save_review",
        lambda *args, **kwargs: failure_stage != "review",
    )
    monkeypatch.setattr(
        interview, "try_claim_session_sync",
        lambda *args, **kwargs: "sync-token",
    )

    async def apply_side_effects(*args, **kwargs):
        if failure_stage == "side_effect":
            raise RuntimeError("vector write failed")
        return True

    monkeypatch.setattr(interview, "_apply_drill_side_effects", apply_side_effects)
    monkeypatch.setattr(
        interview,
        "release_session_sync_claim",
        lambda *args, **kwargs: sync_released.append(
            kwargs["claim_token"]
        ) or True,
    )
    monkeypatch.setattr(
        interview,
        "del_live",
        lambda *args, **kwargs: deleted.append(args),
    )
    monkeypatch.setattr(
        interview,
        "release_session_evaluation_claim",
        lambda *args, **kwargs: released.append(kwargs["claim_token"]) or True,
    )

    from backend.graphs import decoupled_eval
    from backend.storage import rag_metrics_store

    monkeypatch.setattr(decoupled_eval, "has_small_tier", lambda: False)
    monkeypatch.setattr(rag_metrics_store, "save_rag_metrics", lambda *args, **kwargs: True)

    async def fake_evaluate(*args, **kwargs):
        yield interview.sse_event({
            "type": "eval_result",
            "data": {
                "scores": [{"question_id": 1, "score": 8}],
                "overall": {"avg_score": 8},
            },
        })

    monkeypatch.setattr(interview, "stream_evaluate_drill_answers", fake_evaluate)

    async def consume():
        response = await interview.end_interview(
            "s1",
            EndDrillRequest(answers=[{"question_id": 1, "answer": "A"}]),
            user_id="u1",
        )
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(consume())
    events = [
        json.loads(line[6:])
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]

    assert "complete" not in {event.get("type") for event in events}
    assert [event.get("type") for event in events][-2:] == ["error", "done"]
    assert released == ["eval-token"]
    assert sync_released == (["sync-token"] if failure_stage == "side_effect" else [])
    assert deleted == []


class _BlockingResumeGraph:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.active = 0
        self.max_active = 0
        self._message = ""
        self._guard = threading.Lock()

    def update_state(self, _config, state):
        self._message = state["messages"][0].content
        self.calls.append(("update", self._message))

    def invoke(self, _state, _config):
        message = self._message
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.calls.append(("invoke", message))
        self.started.set()
        if message == "first":
            assert self.release.wait(timeout=2)
        with self._guard:
            self.active -= 1
        return {
            "messages": [
                HumanMessage(content=message),
                AIMessage(content=f"reply:{message}"),
            ],
            "is_finished": False,
        }


def _mock_resume_turn_storage(monkeypatch, batches):
    monkeypatch.setattr(
        interview,
        "try_claim_resume_turn",
        lambda *args, **kwargs: "turn-token",
    )
    monkeypatch.setattr(
        interview,
        "commit_resume_turn",
        lambda _session_id, messages, **_kwargs: batches.append(messages) or True,
    )
    monkeypatch.setattr(
        interview,
        "release_resume_turn_claim",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        interview,
        "renew_resume_turn_claim",
        lambda *args, **kwargs: True,
    )


def test_resume_disconnect_waits_for_graph_and_transcript_commit(monkeypatch):
    graph = _BlockingResumeGraph()
    entry = {
        "graph": graph,
        "config": {"configurable": {"thread_id": "disconnect"}},
        "mode": InterviewMode.RESUME,
        "topic": None,
        "user_id": "u1",
    }
    batches = []
    monkeypatch.setattr(interview, "_get_resume_graph", lambda *args: entry)
    monkeypatch.setattr(
        interview, "get_session", lambda *args, **kwargs: {"meta": {}, "review": None},
    )
    _mock_resume_turn_storage(monkeypatch, batches)

    async def run_disconnect():
        response = await interview.chat(
            ChatRequest(session_id="disconnect", message="first"), user_id="u1",
        )
        iterator = response.body_iterator
        first_chunk = await iterator.__anext__()
        assert '"type": "progress"' in first_chunk
        assert await asyncio.to_thread(graph.started.wait, 1)

        next_chunk = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0.05)
        next_chunk.cancel()
        await asyncio.sleep(0.05)
        assert not next_chunk.done()
        graph.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(next_chunk, timeout=1)

    asyncio.run(run_disconnect())

    assert [[message["content"] for message in batch] for batch in batches] == [
        ["first", "reply:first"],
    ]


def test_resume_turn_heartbeat_retries_transient_renewal_error(monkeypatch):
    """A temporary database failure must not abandon a long-running turn."""
    calls = 0

    def flaky_renew(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary sqlite lock")
        return True

    monkeypatch.setattr(interview, "renew_resume_turn_claim", flaky_renew)
    monkeypatch.setattr(interview, "_RESUME_TURN_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(interview, "_RESUME_TURN_CLAIM_TTL_SECONDS", 0.2)
    monkeypatch.setattr(
        interview, "_RESUME_TURN_HEARTBEAT_RENEWAL_GUARD_SECONDS", 0.05,
    )

    async def exercise():
        stop = asyncio.Event()
        task = asyncio.create_task(
            interview._resume_turn_heartbeat("heartbeat", "u1", "token", stop),
        )
        for _ in range(100):
            if calls >= 2:
                break
            await asyncio.sleep(0.005)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(exercise())
    assert calls >= 2


def test_resume_turn_heartbeat_stops_on_explicit_claim_loss(monkeypatch):
    calls = 0

    def lost_renewal(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(interview, "renew_resume_turn_claim", lost_renewal)
    monkeypatch.setattr(interview, "_RESUME_TURN_HEARTBEAT_SECONDS", 0.001)

    async def exercise():
        stop = asyncio.Event()
        await asyncio.wait_for(
            interview._resume_turn_heartbeat("lost", "u1", "stale", stop),
            timeout=1,
        )

    asyncio.run(exercise())
    assert calls == 1


def test_resume_turn_heartbeat_does_not_swallow_cancellation(monkeypatch):
    monkeypatch.setattr(interview, "_RESUME_TURN_HEARTBEAT_SECONDS", 60.0)

    async def exercise():
        task = asyncio.create_task(
            interview._resume_turn_heartbeat(
                "cancelled", "u1", "token", asyncio.Event(),
            ),
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_cancelled_resume_turn_itself_keeps_lock_until_commit(monkeypatch):
    graph = _BlockingResumeGraph()
    entry = {
        "graph": graph,
        "config": {"configurable": {"thread_id": "cancelled-turn"}},
    }
    batches = []
    monkeypatch.setattr(
        interview, "get_session", lambda *args, **kwargs: {"meta": {}, "review": None},
    )
    _mock_resume_turn_storage(monkeypatch, batches)

    async def cancel_turn():
        turn = asyncio.create_task(interview._run_resume_turn(
            entry, "cancelled-turn", "first", "u1",
        ))
        assert await asyncio.to_thread(graph.started.wait, 1)
        turn.cancel()
        await asyncio.sleep(0.05)
        assert not turn.done()
        graph.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(turn, timeout=1)

    asyncio.run(cancel_turn())

    assert [[message["content"] for message in batch] for batch in batches] == [
        ["first", "reply:first"],
    ]


def test_resume_turns_for_same_session_are_serialized(monkeypatch):
    graph = _BlockingResumeGraph()
    entry = {
        "graph": graph,
        "config": {"configurable": {"thread_id": "serialized"}},
        "mode": InterviewMode.RESUME,
        "topic": None,
        "user_id": "u1",
    }
    batches = []
    monkeypatch.setattr(interview, "_get_resume_graph", lambda *args: entry)
    monkeypatch.setattr(
        interview, "get_session", lambda *args, **kwargs: {"meta": {}, "review": None},
    )
    _mock_resume_turn_storage(monkeypatch, batches)

    async def consume(iterator):
        return [chunk async for chunk in iterator]

    async def run_concurrent_turns():
        first = await interview.chat(
            ChatRequest(session_id="serialized", message="first"), user_id="u1",
        )
        second = await interview.chat(
            ChatRequest(session_id="serialized", message="second"), user_id="u1",
        )
        first_task = asyncio.create_task(consume(first.body_iterator))
        second_task = asyncio.create_task(consume(second.body_iterator))
        assert await asyncio.to_thread(graph.started.wait, 1)
        await asyncio.sleep(0.05)
        assert graph.calls == [("update", "first"), ("invoke", "first")]
        graph.release.set()
        await asyncio.wait_for(
            asyncio.gather(first_task, second_task), timeout=2,
        )

    asyncio.run(run_concurrent_turns())

    assert graph.max_active == 1
    assert graph.calls == [
        ("update", "first"), ("invoke", "first"),
        ("update", "second"), ("invoke", "second"),
    ]
    assert [[message["content"] for message in batch] for batch in batches] == [
        ["first", "reply:first"],
        ["second", "reply:second"],
    ]


@pytest.mark.parametrize(
    "stored_session",
    [
        {"meta": {"evaluation_claim_token": "eval"}, "review": None},
        {"meta": {}, "review": "completed review"},
    ],
)
def test_resume_chat_does_not_mutate_evaluating_or_completed_session(
    monkeypatch, stored_session,
):
    calls = []

    class Graph:
        def update_state(self, *_args):
            calls.append("update")

        def invoke(self, *_args):
            calls.append("invoke")
            return {"messages": []}

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "closed"}},
        "mode": InterviewMode.RESUME,
        "topic": None,
        "user_id": "u1",
    }
    monkeypatch.setattr(interview, "_get_resume_graph", lambda *args: entry)
    monkeypatch.setattr(
        interview, "get_session", lambda *args, **kwargs: stored_session,
    )

    async def consume():
        response = await interview.chat(
            ChatRequest(session_id="closed", message="late"), user_id="u1",
        )
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(consume())
    events = [
        json.loads(line[6:])
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]

    assert calls == []
    assert [event.get("type") for event in events][-2:] == ["error", "done"]


def test_resume_review_failure_reuses_persisted_profile_step(monkeypatch):
    messages = [
        AIMessage(content="question"),
        HumanMessage(content="answer"),
    ]

    class State:
        values = {
            "messages": messages,
            "scores": [],
            "weak_points": [],
            "eval_history": [],
            "topic_name": None,
        }

    class Graph:
        def get_state(self, _config):
            return State()

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "resume-retry"}},
        "mode": InterviewMode.RESUME,
        "topic": None,
        "user_id": "u1",
    }
    stored = {
        "meta": {},
        "overall": {},
        "review": None,
    }
    calls = {"profile": 0, "save": 0}

    monkeypatch.setattr(interview, "_get_resume_graph", lambda *args: entry)
    monkeypatch.setattr(
        interview, "get_session",
        lambda *args, **kwargs: {
            "mode": "resume",
            "meta": dict(stored["meta"]),
            "overall": dict(stored["overall"]),
            "review": stored["review"],
        },
    )
    def claim_evaluation(*args, **kwargs):
        stored["meta"]["evaluation_claim_token"] = "eval-token"
        return "eval-token"

    def release_evaluation(*args, **kwargs):
        if stored["meta"].get("evaluation_claim_token") == kwargs.get("claim_token"):
            stored["meta"].pop("evaluation_claim_token", None)
            return True
        return False

    monkeypatch.setattr(interview, "try_claim_session_evaluation", claim_evaluation)
    monkeypatch.setattr(interview, "release_session_evaluation_claim", release_evaluation)
    monkeypatch.setattr(
        interview, "try_claim_session_sync",
        lambda *args, **kwargs: "sync-token",
    )
    monkeypatch.setattr(
        interview, "release_session_sync_claim",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        interview, "session_sync_steps",
        lambda *args, **kwargs: set(stored["meta"].get("sync_steps", {})),
    )

    def mark_step(_session_id, step, **kwargs):
        value = {"completed_at": "now"}
        if kwargs.get("result") is not None:
            value["result"] = kwargs["result"]
        stored["meta"].setdefault("sync_steps", {})[step] = value
        return True

    monkeypatch.setattr(interview, "mark_session_sync_step", mark_step)
    monkeypatch.setattr(
        interview, "session_sync_step_result",
        lambda *args, **kwargs: stored["meta"]["sync_steps"]["profile"].get("result"),
    )
    monkeypatch.setattr(
        interview, "mark_session_synced",
        lambda *args, **kwargs: stored["meta"].__setitem__("synced_at", "now") or True,
    )

    extraction = {
        "weak_points": [{"point": "weak"}],
        "strong_points": [],
        "session_summary": "summary",
        "dimension_scores": {"technical_depth": 8},
        "avg_score": 8.0,
    }

    async def update_profile(*args, **kwargs):
        calls["profile"] += 1
        return extraction

    async def review_stream(*args, **kwargs):
        yield interview.sse_event({"type": "review_result", "data": "review"})

    def save(_session_id, review, _scores, _weak_points, *, overall, **kwargs):
        calls["save"] += 1
        if calls["save"] == 1:
            return False
        stored["review"] = review
        stored["overall"] = overall
        return True

    monkeypatch.setattr(interview, "update_profile_after_interview", update_profile)
    monkeypatch.setattr(interview, "stream_generate_review", review_stream)
    monkeypatch.setattr(interview, "save_review", save)
    monkeypatch.setattr(interview, "_match_resume_to_topics", lambda *args: [])
    monkeypatch.setattr(interview, "del_live", lambda *args: None)

    async def evaluate_once():
        response = await interview.end_interview("resume-retry", user_id="u1")
        chunks = [chunk async for chunk in response.body_iterator]
        return [
            json.loads(line[6:])
            for chunk in chunks
            for line in chunk.splitlines()
            if line.startswith("data: ")
        ]

    first_events = asyncio.run(evaluate_once())
    second_events = asyncio.run(evaluate_once())

    assert "complete" not in {event.get("type") for event in first_events}
    complete = next(event for event in second_events if event.get("type") == "complete")
    assert calls == {"profile": 1, "save": 2}
    assert stored["overall"] == {
        "dimension_scores": {"technical_depth": 8},
        "avg_score": 8.0,
    }
    assert complete["data"]["dimension_scores"] == {"technical_depth": 8}


def test_resume_knowledge_retry_keeps_live_state_until_sync_completes(monkeypatch):
    messages = [AIMessage(content="question"), HumanMessage(content="answer")]

    class State:
        values = {
            "messages": messages,
            "scores": [],
            "weak_points": [],
            "eval_history": [{
                "question": "question",
                "answer": "answer",
                "score": 8.0,
                "assessment": "good",
            }],
            "topic_name": None,
        }

    class Graph:
        def get_state(self, _config):
            return State()

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "resume-knowledge-retry"}},
        "mode": InterviewMode.RESUME,
        "topic": None,
        "user_id": "u1",
    }
    stored = {"meta": {}, "overall": {}, "review": None}
    calls = {"profile": 0, "save": 0, "extract": 0, "freq": 0}
    deleted = []

    monkeypatch.setattr(interview, "_get_resume_graph", lambda *args: entry)
    monkeypatch.setattr(
        interview,
        "get_session",
        lambda *args, **kwargs: {
            "mode": "resume",
            "meta": dict(stored["meta"]),
            "overall": dict(stored["overall"]),
            "review": stored["review"],
        },
    )
    def claim_evaluation(*args, **kwargs):
        stored["meta"]["evaluation_claim_token"] = "eval-token"
        return "eval-token"

    def release_evaluation(*args, **kwargs):
        if stored["meta"].get("evaluation_claim_token") == kwargs.get("claim_token"):
            stored["meta"].pop("evaluation_claim_token", None)
            return True
        return False

    monkeypatch.setattr(interview, "try_claim_session_evaluation", claim_evaluation)
    monkeypatch.setattr(interview, "release_session_evaluation_claim", release_evaluation)
    monkeypatch.setattr(
        interview, "try_claim_session_sync", lambda *args, **kwargs: "sync-token",
    )
    monkeypatch.setattr(
        interview, "release_session_sync_claim", lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        interview,
        "session_sync_steps",
        lambda *args, **kwargs: set(stored["meta"].get("sync_steps", {})),
    )

    def mark_step(_session_id, step, **kwargs):
        value = {"completed_at": "now"}
        if kwargs.get("result") is not None:
            value["result"] = kwargs["result"]
        stored["meta"].setdefault("sync_steps", {})[step] = value
        return True

    monkeypatch.setattr(interview, "mark_session_sync_step", mark_step)
    monkeypatch.setattr(
        interview,
        "session_sync_step_result",
        lambda *args, **kwargs: stored["meta"]["sync_steps"]["profile"].get("result"),
    )
    monkeypatch.setattr(
        interview,
        "mark_session_synced",
        lambda *args, **kwargs: stored["meta"].__setitem__("synced_at", "now") or True,
    )

    extraction = {
        "weak_points": [],
        "strong_points": [],
        "session_summary": "summary",
        "avg_score": 8.0,
    }

    async def update_profile(*args, **kwargs):
        calls["profile"] += 1
        return extraction

    async def review_stream(*args, **kwargs):
        yield interview.sse_event({"type": "review_result", "data": "review"})

    def save(_session_id, review, _scores, _weak_points, *, overall, **kwargs):
        calls["save"] += 1
        stored["review"] = review
        stored["overall"] = overall
        return True

    async def extract(*args, **kwargs):
        calls["extract"] += 1
        return calls["extract"] > 1

    async def collect(*args, **kwargs):
        calls["freq"] += 1
        return True

    async def confirm(*args, **kwargs):
        return None

    monkeypatch.setattr(interview, "update_profile_after_interview", update_profile)
    monkeypatch.setattr(interview, "stream_generate_review", review_stream)
    monkeypatch.setattr(interview, "save_review", save)
    monkeypatch.setattr(interview, "_match_resume_to_topics", lambda *args: ["python"])
    monkeypatch.setattr(
        interview,
        "session_sync_targets",
        lambda *args, **kwargs: ["python"],
    )
    monkeypatch.setattr(interview, "_confirm_profile_operations", confirm)
    monkeypatch.setattr(knowledge_evolution, "extract_and_writeback", extract)
    monkeypatch.setattr(knowledge_evolution, "collect_high_freq", collect)
    monkeypatch.setattr(
        interview,
        "del_live",
        lambda _store, session_id, user_id: deleted.append((session_id, user_id)),
    )

    async def evaluate_once():
        response = await interview.end_interview(
            "resume-knowledge-retry", user_id="u1",
        )
        chunks = [chunk async for chunk in response.body_iterator]
        return [
            json.loads(line[6:])
            for chunk in chunks
            for line in chunk.splitlines()
            if line.startswith("data: ")
        ]

    first_events = asyncio.run(evaluate_once())
    assert "complete" not in {event.get("type") for event in first_events}
    assert deleted == []

    second_events = asyncio.run(evaluate_once())
    assert "complete" in {event.get("type") for event in second_events}
    assert calls == {"profile": 1, "save": 2, "extract": 2, "freq": 1}
    assert deleted == [("resume-knowledge-retry", "u1")]

    # A crash after synced_at but before the client receives `complete` must
    # replay the persisted profile result instead of returning empty metrics.
    third_events = asyncio.run(evaluate_once())
    third_complete = next(
        event for event in third_events if event.get("type") == "complete"
    )
    assert third_complete["data"]["profile_update"]["session_summary"] == "summary"
    assert third_complete["data"]["avg_score"] == 8.0
    assert calls == {"profile": 1, "save": 3, "extract": 2, "freq": 1}
