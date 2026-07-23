import asyncio
import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

import backend.embedding_tasks as embedding_tasks
import backend.memory as memory
import backend.vector_memory as vector_memory


class RecordingStore:
    def __init__(self):
        self.add_calls = []
        self.cleanup_calls = []

    def add(self, user_id, records):
        self.add_calls.append((user_id, records))

    def cleanup_oldest(self, user_id, max_count):
        self.cleanup_calls.append((user_id, max_count))


@pytest.mark.asyncio
async def test_durable_index_rejects_partial_zero_vectors_before_write(monkeypatch):
    store = RecordingStore()

    async def partial_embeddings(_texts):
        return [
            np.array([1.0, 0.0], dtype=np.float32),
            np.zeros(2, dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        ]

    monkeypatch.setattr(vector_memory, "_embed_batch", partial_embeddings)
    monkeypatch.setattr(vector_memory, "get_vector_store", lambda: store)

    with pytest.raises(
        vector_memory.IncompleteMemoryIndexError,
        match="1 invalid/missing vectors for 3 chunks",
    ):
        await vector_memory.index_session_memory(
            session_id="session-sync:s1:profile",
            topic="python",
            summary="summary",
            weak_points=[{"point": "weak"}],
            insight_text="insight",
            user_id="user-1",
            require_complete=True,
        )

    assert store.add_calls == []
    assert store.cleanup_calls == []


@pytest.mark.asyncio
async def test_background_index_keeps_partial_embedding_degradation(monkeypatch):
    store = RecordingStore()

    async def partial_embeddings(_texts):
        return [
            np.array([1.0, 0.0], dtype=np.float32),
            np.zeros(2, dtype=np.float32),
        ]

    monkeypatch.setattr(vector_memory, "_embed_batch", partial_embeddings)
    monkeypatch.setattr(vector_memory, "get_vector_store", lambda: store)

    await vector_memory.index_session_memory(
        session_id=None,
        topic="python",
        summary="summary",
        weak_points=[{"point": "weak"}],
        user_id="user-1",
    )

    assert len(store.add_calls) == 1
    assert [record.content for record in store.add_calls[0][1]] == ["summary"]
    assert len(store.cleanup_calls) == 1


@pytest.mark.asyncio
async def test_durable_index_with_no_chunks_is_successful_noop(monkeypatch):
    store = RecordingStore()

    async def unexpected_embeddings(_texts):
        raise AssertionError("empty sessions must not request embeddings")

    monkeypatch.setattr(vector_memory, "_embed_batch", unexpected_embeddings)
    monkeypatch.setattr(vector_memory, "get_vector_store", lambda: store)

    await vector_memory.index_session_memory(
        session_id="session-sync:s1:profile",
        topic=None,
        summary="",
        weak_points=[],
        insight_text="",
        user_id="user-1",
        require_complete=True,
    )

    assert store.add_calls == []


class RecordingBreaker:
    def __init__(self):
        self.successes = 0
        self.failures = 0

    def acquire(self):
        return object()

    def record_success(self, _permit):
        self.successes += 1

    def record_failure(self, _permit):
        self.failures += 1

    def release_probe(self, _permit):
        pass


@pytest.mark.asyncio
async def test_embed_batch_length_mismatch_degrades_to_matching_zero_vectors(monkeypatch):
    breaker = RecordingBreaker()
    monkeypatch.setattr(embedding_tasks, "get_circuit_breaker", lambda: breaker)
    monkeypatch.setattr(vector_memory, "_MAX_EMBED_RETRIES", 0)
    monkeypatch.setattr(
        vector_memory,
        "_embed_batch_sync",
        lambda _texts: [[1.0, 0.0]],
    )

    vectors = await vector_memory._embed_batch(["first", "second"])

    assert len(vectors) == 2
    assert all(not np.any(vector) for vector in vectors)
    assert breaker.successes == 0
    assert breaker.failures == 1


@pytest.mark.asyncio
async def test_profile_operation_requests_complete_vector_index(monkeypatch):
    calls = []

    monkeypatch.setattr(memory, "_save_insight", lambda *_args, **_kwargs: True)

    async def capture_index(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(vector_memory, "index_session_memory", capture_index)

    await memory._ensure_profile_artifacts(
        mode="topic_drill",
        topic="python",
        session_summary="summary",
        weak_points=[],
        strong_points=[],
        user_id="user-1",
        operation_id="session-sync:s1:profile",
    )

    assert len(calls) == 1
    assert calls[0]["require_complete"] is True


def test_insight_operation_marker_is_deduplicated_across_daily_files(
    tmp_path, monkeypatch,
):
    insights_dir = tmp_path / "insights"
    insights_dir.mkdir()
    operation_id = "session-sync:midnight:profile"
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    old_file = insights_dir / "2000-01-01.md"
    original = f"<!-- profile-operation:{digest} -->\nold insight\n"
    old_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(memory, "_insights_dir", lambda _user_id: insights_dir)

    written = memory._save_insight(
        "topic_drill",
        "python",
        "retry after midnight",
        {"weak_points": [{"point": "weak"}]},
        "user-1",
        operation_id,
    )

    assert written is False
    assert old_file.read_text(encoding="utf-8") == original
    assert list(insights_dir.glob("*.md")) == [old_file]
    assert (tmp_path / ".insights.lock").exists()


@pytest.mark.asyncio
async def test_profile_artifacts_discard_malformed_json_fields(tmp_path, monkeypatch):
    insights_dir = tmp_path / "insights"
    vector_calls = []
    monkeypatch.setattr(memory, "_insights_dir", lambda _user_id: insights_dir)

    async def capture_index(**kwargs):
        vector_calls.append(kwargs)

    monkeypatch.setattr(vector_memory, "index_session_memory", capture_index)

    await memory._ensure_profile_artifacts(
        mode="topic_drill",
        topic="python",
        session_summary=["not", "text"],
        weak_points=[
            None,
            {"point": ["not text"]},
            {"point": " valid weak ", "topic": 42, "evidence": "keep me"},
            " scalar weak ",
        ],
        strong_points={"point": " valid strong ", "source": "keep me"},
        user_id="user-1",
        operation_id="session-sync:malformed:profile",
    )

    assert len(vector_calls) == 1
    call = vector_calls[0]
    assert call["summary"] == ""
    assert call["weak_points"] == [
        {"point": "valid weak", "topic": "python", "evidence": "keep me"},
        "scalar weak",
    ]
    assert call["strong_points"] == [
        {"point": "valid strong", "source": "keep me"},
    ]
    assert call["require_complete"] is True

    insight = next(insights_dir.glob("*.md")).read_text(encoding="utf-8")
    assert "valid weak" in insight
    assert "scalar weak" in insight
    assert "valid strong" in insight
    assert "not text" not in insight


@pytest.mark.asyncio
async def test_malformed_cached_profile_result_is_normalized_before_replay(
    tmp_path, monkeypatch,
):
    profile_path = tmp_path / "profile.json"
    operation_id = "session-sync:cached-malformed:profile"
    vector_calls = []
    monkeypatch.setattr(memory, "_profile_path", lambda _user_id: profile_path)
    monkeypatch.setattr(memory, "_save_insight", lambda *_args, **_kwargs: True)

    async def capture_index(**kwargs):
        vector_calls.append(kwargs)

    monkeypatch.setattr(vector_memory, "index_session_memory", capture_index)
    with memory.profile_transaction("user-1") as profile:
        memory._record_applied_operation(
            profile,
            operation_id,
            result={
                "session_summary": ["bad summary"],
                "weak_points": [
                    {"point": "kept", "evidence": "valid field"},
                    {"point": ["bad"]},
                    None,
                ],
                "strong_points": {"point": "strong"},
                "topic_mastery": ["bad"],
                "communication_observations": "bad",
                "thinking_patterns": {"new_gaps": ["gap", 3]},
                "dimension_scores": {"technical_depth": "8", "bad": []},
                "avg_score": "7.5",
            },
        )

    result = await memory.update_profile_after_interview(
        mode="resume",
        topic=None,
        messages=[],
        user_id="user-1",
        operation_id=operation_id,
    )

    assert result["session_summary"] == ""
    assert result["weak_points"] == [
        {"point": "kept", "evidence": "valid field"},
    ]
    assert result["strong_points"] == [{"point": "strong"}]
    assert result["topic_mastery"] == {}
    assert result["communication_observations"] == {}
    assert result["thinking_patterns"] == {"new_gaps": ["gap"]}
    assert result["dimension_scores"] == {"technical_depth": 8.0}
    assert result["avg_score"] == 7.5
    assert len(vector_calls) == 1
    assert vector_calls[0]["weak_points"] == result["weak_points"]
    assert vector_calls[0]["strong_points"] == result["strong_points"]
    assert vector_calls[0]["require_complete"] is True


@pytest.mark.asyncio
async def test_duplicate_profile_fast_path_replays_journal_winner(
    tmp_path, monkeypatch,
):
    profile_path = tmp_path / "profile.json"
    operation_id = "session-sync:fast-winner:profile"
    insight_calls = []
    vector_calls = []
    winner = {
        "session_summary": "winner summary",
        "weak_points": [{"point": "winner weak"}],
        "strong_points": [{"point": "winner strong"}],
    }
    monkeypatch.setattr(memory, "_profile_path", lambda _user_id: profile_path)
    monkeypatch.setattr(
        memory,
        "get_langchain_llm",
        lambda: pytest.fail("duplicate fast path must not call the LLM"),
    )

    def capture_insight(_mode, _topic, summary, extraction, _user_id, _operation_id):
        insight_calls.append((summary, extraction))
        return True

    async def capture_index(**kwargs):
        vector_calls.append(kwargs)

    monkeypatch.setattr(memory, "_save_insight", capture_insight)
    monkeypatch.setattr(vector_memory, "index_session_memory", capture_index)
    with memory.profile_transaction("user-1") as profile:
        memory._record_applied_operation(profile, operation_id, result=winner)

    applied = await memory.llm_update_profile(
        mode="resume",
        topic=None,
        new_weak_points=[{"point": "loser weak"}],
        new_strong_points=[{"point": "loser strong"}],
        topic_mastery={},
        communication={},
        user_id="user-1",
        session_summary="loser summary",
        operation_id=operation_id,
        operation_result={
            "session_summary": "loser summary",
            "weak_points": [{"point": "loser weak"}],
            "strong_points": [{"point": "loser strong"}],
        },
    )

    assert applied is False
    assert insight_calls == [
        (
            "winner summary",
            {
                "weak_points": winner["weak_points"],
                "strong_points": winner["strong_points"],
            },
        ),
    ]
    assert len(vector_calls) == 1
    assert vector_calls[0]["summary"] == "winner summary"
    assert vector_calls[0]["weak_points"] == winner["weak_points"]
    assert vector_calls[0]["strong_points"] == winner["strong_points"]


@pytest.mark.asyncio
async def test_concurrent_profile_race_loser_replays_winner_payload(
    tmp_path, monkeypatch,
):
    profile_path = tmp_path / "profile.json"
    operation_id = "session-sync:concurrent-winner:profile"
    insight_calls = []
    vector_calls = []
    both_at_llm = asyncio.Event()
    arrivals = 0
    monkeypatch.setattr(memory, "_profile_path", lambda _user_id: profile_path)

    class RacingLLM:
        async def ainvoke(self, messages):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                both_at_llm.set()
            await both_at_llm.wait()
            if "loser weak" in messages[-1].content:
                await asyncio.sleep(0.05)
            return SimpleNamespace(content="{}")

    def capture_insight(_mode, _topic, summary, extraction, _user_id, _operation_id):
        insight_calls.append((summary, extraction))
        return True

    async def capture_index(**kwargs):
        vector_calls.append(kwargs)

    monkeypatch.setattr(memory, "get_langchain_llm", RacingLLM)
    monkeypatch.setattr(memory, "_save_insight", capture_insight)
    monkeypatch.setattr(vector_memory, "index_session_memory", capture_index)

    async def update(label):
        result = {
            "session_summary": f"{label} summary",
            "weak_points": [{"point": f"{label} weak"}],
            "strong_points": [{"point": f"{label} strong"}],
        }
        return await memory.llm_update_profile(
            mode="resume",
            topic=None,
            new_weak_points=result["weak_points"],
            new_strong_points=result["strong_points"],
            topic_mastery={},
            communication={},
            user_id="user-1",
            session_summary=result["session_summary"],
            operation_id=operation_id,
            operation_result=result,
        )

    outcomes = await asyncio.gather(update("winner"), update("loser"))

    assert outcomes == [True, False]
    stored = memory._profile_operation_result("user-1", operation_id)
    assert stored["session_summary"] == "winner summary"
    assert len(insight_calls) == 2
    assert all(summary == "winner summary" for summary, _ in insight_calls)
    assert all(
        extraction["weak_points"] == [{"point": "winner weak"}]
        for _, extraction in insight_calls
    )
    assert len(vector_calls) == 2
    assert all(call["summary"] == "winner summary" for call in vector_calls)
    assert all(
        call["weak_points"] == [{"point": "winner weak"}]
        for call in vector_calls
    )
    assert all(
        call["strong_points"] == [{"point": "winner strong"}]
        for call in vector_calls
    )
