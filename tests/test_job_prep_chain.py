"""Regression tests for the JD-prep (job_prep) chain.

Covers the four defects that made JD prep behave differently from topic drill:

1. hint / reference-answer returned HTTP 422 for every JD session, because the
   session persisted no topic and ``ReferenceAnswerRequest.topic`` was required.
2. ``resume_used`` was reported True even when the resume index yielded nothing,
   so the UI claimed 简历联动 while the prompt carried no resume text.
3. JD→topic matching used a raw substring test, which matched "java" inside
   "javascript" and could never match CJK-named / hash-keyed user topics.
4. An evaluation that failed before applying any side effect left
   ``sync_pending_at`` behind, permanently 409-ing 「重新评估」.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import backend.indexer as indexer
import backend.storage.database as database
import backend.utils.sse_helpers as sse_helpers
from backend.auth import get_current_user
from backend.graphs import job_prep
from backend.graphs import rag_retrieval
from backend.indexer import ChunkWithMeta, IndexNotReady
from backend.models import ReferenceAnswerRequest
from backend.routers import interview
from backend.storage.sessions import (
    clear_unstarted_sync_pending,
    create_session,
    mark_session_sync_step,
    release_session_evaluation_claim,
    release_session_sync_claim,
    try_claim_session_evaluation,
    try_claim_session_sync,
)


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "job-prep.db"
    database._local.conn = None
    try:
        database.init_all_tables()
        yield database.DB_PATH
    finally:
        temp_conn = getattr(database._local, "conn", None)
        if temp_conn is not None:
            temp_conn.close()
        database.DB_PATH = original_path
        database._local.conn = original_conn


TOPICS = {
    "python": {"name": "Python 核心"},
    "java": {"name": "Java 后端"},
    "agent": {"name": "AI Agent 工程"},
    "cb8f7fc8": {"name": "大厂题库"},
}


# ── 1. hint / reference-answer for JD sessions ──

def test_reference_answer_accepts_blank_topic():
    """JD prep sends topic="" — a required topic rejected it with HTTP 422."""
    request = ReferenceAnswerRequest(
        topic="", question="RAG 怎么调优？", session_id="s1",
        question_id=1, mode="hint",
    )
    assert request.topic == ""
    assert request.mode == "hint"


def test_reference_answer_still_rejects_non_string_topic():
    with pytest.raises(ValueError):
        ReferenceAnswerRequest(topic=None, question="q")


@pytest.fixture
def reference_answer_client(monkeypatch, isolated_db):
    """A client whose retrieval + LLM legs are stubbed, recording the topics hit."""
    seen: dict[str, list[str]] = {"topics": []}

    async def fake_retrieve(topic, question, user_id, top_k=3, timeout=60.0,
                            build_if_missing=False):
        seen["topics"].append(topic)
        return [f"chunk::{topic}"]

    def fake_stream(messages, progress_prefix=""):
        seen["prompt"] = messages[0].content

        async def _gen():
            yield ("text", "【提示】先想清楚召回链路。")

        return _gen()

    monkeypatch.setattr(indexer, "safe_retrieve_topic_context", fake_retrieve)
    monkeypatch.setattr(sse_helpers, "stream_llm_sse", fake_stream)
    monkeypatch.setattr(interview, "load_topics", lambda _uid: TOPICS)

    app = FastAPI()
    app.include_router(interview.router)
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return TestClient(app), seen


def test_jd_hint_uses_frozen_session_topics(reference_answer_client):
    client, seen = reference_answer_client
    create_session(
        "jd-1", "jd_prep", questions=[{"id": 1}],
        meta={"topics": ["python", "agent"], "position": "Agent 开发"},
        user_id="u1",
    )

    response = client.post("/api/interview/reference-answer", json={
        "topic": "", "question": "RAG 怎么调优？",
        "session_id": "jd-1", "question_id": 1, "mode": "hint",
    })

    assert response.status_code == 200, response.text
    assert seen["topics"] == ["python", "agent"]


def test_jd_hint_falls_back_for_sessions_without_frozen_topics(reference_answer_client):
    """Sessions created before meta.topics existed still resolve a scope."""
    client, seen = reference_answer_client
    create_session(
        "jd-legacy", "jd_prep", questions=[{"id": 1}],
        meta={"jd_excerpt": "负责 Agent 平台研发，熟悉 Python", "position": "Agent 开发"},
        user_id="u1",
    )

    response = client.post("/api/interview/reference-answer", json={
        "topic": "", "question": "q", "session_id": "jd-legacy",
        "question_id": 1, "mode": "hint",
    })

    assert response.status_code == 200, response.text
    assert seen["topics"] == ["python", "agent"]


# ── 2. resume_used must never claim more than was retrieved ──

@pytest.mark.parametrize(
    ("chunks", "raises"),
    [([], False), (None, True)],
    ids=["empty-index", "retrieval-error"],
)
def test_resume_used_is_false_without_usable_resume_text(monkeypatch, chunks, raises):
    monkeypatch.setattr(job_prep, "_has_resume", lambda _uid: True)

    def _retrieve(*_args, **_kwargs):
        if raises:
            raise RuntimeError("qdrant down")
        return chunks

    monkeypatch.setattr(job_prep, "retrieve_resume_chunks", _retrieve)

    _context, resume_used = job_prep._get_resume_context("u1", True)

    assert resume_used is False


def test_resume_used_is_true_only_with_real_content(monkeypatch):
    monkeypatch.setattr(job_prep, "_has_resume", lambda _uid: True)
    monkeypatch.setattr(
        job_prep, "retrieve_resume_chunks",
        lambda *_a, **_k: ["项目：RAG 知识库治理，负责召回与重排优化。"],
    )

    context, resume_used = job_prep._get_resume_context("u1", True)

    assert resume_used is True
    assert "RAG 知识库治理" in context


# ── 3. JD → topic matching ──

@pytest.mark.parametrize(
    ("jd", "expected"),
    [
        ("全栈岗位：前端用 JavaScript/React，后端用 Python 与 FastAPI。", ["python"]),
        ("后端岗位：Java 17、Spring Boot、MySQL，熟悉 JVM 调优。", ["java"]),
        ("岗位要求：刷过大厂题库，熟悉常见八股与算法题。", ["cb8f7fc8"]),
    ],
    ids=["javascript-is-not-java", "real-java", "cjk-topic-name"],
)
def test_jd_topic_matching(monkeypatch, jd, expected):
    monkeypatch.setattr(job_prep, "load_topics", lambda _uid: TOPICS)

    assert job_prep._match_jd_topics(jd, "u1") == expected


def test_jd_topic_matching_falls_back_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(job_prep, "load_topics", lambda _uid: TOPICS)

    matched = job_prep._match_jd_topics("岗位：市场运营，负责品牌推广与活动策划。", "u1")

    assert matched == ["python", "java", "agent"]


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"jd_excerpt": "前端 JavaScript/React，后端 Python", "position": "全栈"}, ["python"]),
        ({"jd_excerpt": "刷过大厂题库，八股扎实", "position": "后端"}, ["cb8f7fc8"]),
        # Writeback pushes mistakes INTO a knowledge base, so a miss must stay
        # empty rather than fall back and pollute unrelated topics.
        ({"jd_excerpt": "市场运营，品牌推广", "position": "运营"}, []),
    ],
    ids=["javascript-is-not-java", "cjk-topic-name", "no-match-stays-empty"],
)
def test_writeback_topic_matching(monkeypatch, meta, expected):
    monkeypatch.setattr(interview, "load_topics", lambda _uid: TOPICS)

    assert interview._match_jd_to_topics(meta, "u1") == expected


# ── 5. JD retrieval runs the same fused pipeline the drill uses ──

class _MemoryCache:
    def __init__(self):
        self.data: dict = {}

    def get_json(self, key):
        return self.data.get(key)

    def set_json(self, key, value, _ttl):
        self.data[key] = value


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Stub retrieval/dedup/rerank, recording every (topic, query) sub-query."""
    calls: list[tuple[str, str]] = []

    async def fake_retrieve(topic, question, user_id, top_k=5, timeout=60.0,
                            build_if_missing=False):
        calls.append((topic, question))
        return [ChunkWithMeta(
            content=f"{topic}::{question[:8]}", score=0.9,
            source_file=f"{topic}.md", header_path="", node_id=topic,
        )]

    async def no_dedup(chunks, **_kwargs):
        return chunks, 0, 0, [None] * len(chunks)

    async def passthrough_rerank(_query, chunks, **_kwargs):
        return chunks, "off"

    monkeypatch.setattr(rag_retrieval, "safe_retrieve_topic_context_with_scores", fake_retrieve)
    monkeypatch.setattr(rag_retrieval, "_semantic_dedup", no_dedup)
    monkeypatch.setattr(rag_retrieval, "_rerank_if_available", passthrough_rerank)
    return calls


def test_duplicate_queries_are_not_issued_twice(stub_pipeline):
    """JD prep passes the JD as both the query AND the fallback.

    The <3-queries padding then appended a second identical query, so every
    topic was retrieved twice. RRF *sums* per-ranking contributions, so feeding
    one ranking in twice doubles that chunk's score — a self-vote rather than
    independent evidence — besides wasting half the embedding round-trips.
    """
    jd = "Agent 平台研发，Python 后端，RAG 检索链路"

    _chunks, stats = asyncio.run(rag_retrieval.retrieve_for_job_prep(
        ["python", "agent"], "u1", [jd], jd, per_query_top_k=3,
    ))

    assert len(stub_pipeline) == len(set(stub_pipeline))  # no (topic, query) repeats
    assert stub_pipeline == [("python", jd), ("agent", jd)]  # submission order
    assert stats.queries == 2                              # 2 topics × 1 unique query


def test_duplicate_topics_are_collapsed(stub_pipeline):
    # Three distinct queries, so the "<3 queries → append fallback" padding does
    # not fire and the arithmetic stays exact: 2 unique topics × 3 queries.
    queries = ["Q1", "Q2", "Q3"]

    _chunks, stats = asyncio.run(rag_retrieval.retrieve_for_job_prep(
        ["python", "agent", "python", "", "  "], "u1", queries, "FB",
    ))

    assert list(dict.fromkeys(t for t, _q in stub_pipeline)) == ["python", "agent"]
    assert len(stub_pipeline) == 2 * len(queries)
    assert stats.queries == 6


def test_distinct_drill_queries_still_all_fan_out(stub_pipeline):
    """Dedup must not collapse genuinely different weak-point queries."""
    _chunks, stats = asyncio.run(rag_retrieval.retrieve_for_drill(
        "python", "u1", ["GIL 原理", "asyncio 调度"], "Python 基础",
    ))

    # 2 weak points + 1 appended fallback, all distinct → 3 sub-queries.
    assert stats.queries == 3
    assert len(stub_pipeline) == 3


def test_results_stay_aligned_when_topics_finish_out_of_order(monkeypatch):
    """gather() preserves submission order, so chunk↔metadata never skew."""
    async def fake_retrieve(topic, question, user_id, top_k=5, timeout=60.0,
                            build_if_missing=False):
        # 'agent' returns immediately; 'python' finishes last.
        if topic == "python":
            await asyncio.sleep(0.05)
        return [ChunkWithMeta(
            content=f"{topic}::{question[:4]}", score=0.9,
            source_file=f"{topic}.md", header_path="", node_id=topic,
        )]

    async def no_dedup(chunks, **_kwargs):
        return chunks, 0, 0, [None] * len(chunks)

    async def passthrough_rerank(_query, chunks, **_kwargs):
        return chunks, "off"

    monkeypatch.setattr(rag_retrieval, "safe_retrieve_topic_context_with_scores", fake_retrieve)
    monkeypatch.setattr(rag_retrieval, "_semantic_dedup", no_dedup)
    monkeypatch.setattr(rag_retrieval, "_rerank_if_available", passthrough_rerank)

    chunks, stats = asyncio.run(rag_retrieval.retrieve_for_job_prep(
        ["python", "agent"], "u1", ["Q1", "Q2"], "FB",
    ))

    for chunk, detail in zip(chunks, stats.final_chunk_details):
        assert detail["source_file"].startswith(chunk.split("::")[0])


def test_one_failing_topic_does_not_discard_the_others(monkeypatch):
    """A missing index degrades that slot only — partial results still return."""
    async def fake_retrieve(topic, question, user_id, top_k=5, timeout=60.0,
                            build_if_missing=False):
        if topic == "python":
            raise IndexNotReady("index not built")
        return [ChunkWithMeta(
            content=f"{topic}::{question[:4]}", score=0.9,
            source_file=f"{topic}.md", header_path="", node_id=topic,
        )]

    async def no_dedup(chunks, **_kwargs):
        return chunks, 0, 0, [None] * len(chunks)

    async def passthrough_rerank(_query, chunks, **_kwargs):
        return chunks, "off"

    monkeypatch.setattr(rag_retrieval, "safe_retrieve_topic_context_with_scores", fake_retrieve)
    monkeypatch.setattr(rag_retrieval, "_semantic_dedup", no_dedup)
    monkeypatch.setattr(rag_retrieval, "_rerank_if_available", passthrough_rerank)

    chunks, stats = asyncio.run(rag_retrieval.retrieve_for_job_prep(
        ["python", "agent"], "u1", ["Q1"], "FB",
    ))

    assert chunks and all(c.startswith("agent") for c in chunks)
    # A single query is padded with the fallback, so the dead topic contributes
    # one failure per query: 1 failing topic × 2 queries.
    assert stats.failed_queries == 2
    assert set(stats.failure_codes) == {"index_not_ready"}
    assert {d["topic"] for d in stats.failure_details} == {"python"}


def test_job_prep_retrieval_fans_out_over_topics_and_reranks(monkeypatch):
    """Fan-out is (topic × query); fusion, dedup and rerank all run."""
    calls: list[tuple[str, str]] = []

    async def fake_retrieve(topic, question, user_id, top_k=5, timeout=60.0,
                            build_if_missing=False):
        calls.append((topic, question))
        return [
            ChunkWithMeta(
                content=f"{topic}-chunk{i}", score=0.9 - i * 0.1,
                source_file=f"{topic}.md", header_path="H", node_id=f"{topic}{i}",
            )
            for i in range(2)
        ]

    async def no_dedup(chunks, **_kwargs):
        return chunks, 0, 0, [None] * len(chunks)

    async def fake_rerank(_query, chunks, **_kwargs):
        return list(reversed(chunks)), "applied"

    monkeypatch.setattr(rag_retrieval, "safe_retrieve_topic_context_with_scores", fake_retrieve)
    monkeypatch.setattr(rag_retrieval, "_semantic_dedup", no_dedup)
    monkeypatch.setattr(rag_retrieval, "_rerank_if_available", fake_rerank)

    chunks, stats = asyncio.run(rag_retrieval.retrieve_for_job_prep(
        ["python", "agent"], "u1", ["Agent 平台 RAG 检索"], "fallback",
        per_query_top_k=3, final_top_n=12,
    ))

    assert [topic for topic, _q in calls] == ["python", "python", "agent", "agent"]
    assert stats.queries == 4          # 2 topics × 2 queries
    assert stats.reranker_status == "applied"
    assert {c.split("-")[0] for c in chunks} == {"python", "agent"}


def test_job_prep_retrieval_without_topics_returns_empty(monkeypatch):
    def _explode(*_a, **_k):  # must never retrieve with no topic scope
        raise AssertionError("retrieval attempted without a topic")

    monkeypatch.setattr(rag_retrieval, "safe_retrieve_topic_context_with_scores", _explode)

    chunks, stats = asyncio.run(
        rag_retrieval.retrieve_for_job_prep([], "u1", ["q"], "fallback")
    )

    assert chunks == []
    assert stats.queries == 0


def test_all_three_prep_steps_share_one_retrieval(monkeypatch):
    """preview / question-gen / eval must hit ONE cache entry.

    The eval step only has ``preview["jd_excerpt"]`` (truncated to 1500 chars),
    so digesting the raw JD made it miss the cache and re-run the whole
    multi-topic retrieval on every evaluation.
    """
    retrievals = {"count": 0}

    async def fake_retrieve(_topics, _user_id, _queries, _fallback, **_kwargs):
        retrievals["count"] += 1
        return ["chunk A", "chunk B"], rag_retrieval.RetrievalStats(
            queries=2, raw_chunks=4, fused_chunks=2, final_chunks=2,
            embed_cache_hits=0, embed_cache_misses=0, reranker_status="applied",
        )

    monkeypatch.setattr(job_prep, "load_topics", lambda _uid: TOPICS)
    monkeypatch.setattr(job_prep, "retrieve_for_job_prep", fake_retrieve)
    cache = _MemoryCache()  # one shared instance — a per-call cache proves nothing
    monkeypatch.setattr(job_prep, "get_cache", lambda: cache)

    jd = "Agent 平台研发，Python 后端，负责 RAG 检索链路。" + "x" * 3000
    excerpt = jd.strip()[:1500]  # exactly what the session persists

    async def _run():
        return (
            await job_prep._get_knowledge_for_jd(jd, "u1"),        # preview
            await job_prep._get_knowledge_for_jd(jd, "u1"),        # question-gen
            await job_prep._get_knowledge_for_jd(excerpt, "u1"),   # eval
        )

    preview_ctx, question_ctx, eval_ctx = asyncio.run(_run())

    assert retrievals["count"] == 1
    assert preview_ctx == question_ctx == eval_ctx
    assert "chunk A" in preview_ctx


def test_jd_rag_metrics_are_persisted_even_on_a_cache_hit(monkeypatch, isolated_db):
    """The preview step warms the cache, so question-gen usually short-circuits.

    Metrics must still land, otherwise the JD path never appears on the RAG
    dashboard even though retrieval ran.
    """
    async def fake_retrieve(_topics, _user_id, _queries, _fallback, **_kwargs):
        return ["c1", "c2"], rag_retrieval.RetrievalStats(
            queries=2, raw_chunks=4, fused_chunks=2, final_chunks=2,
            embed_cache_hits=0, embed_cache_misses=0, reranker_status="applied",
            rag_metrics={
                "relevance": 0.55, "coverage": 0.7,
                "diversity": 0.8, "chunk_details": [],
            },
        )

    monkeypatch.setattr(job_prep, "load_topics", lambda _uid: TOPICS)
    monkeypatch.setattr(job_prep, "retrieve_for_job_prep", fake_retrieve)
    cache = _MemoryCache()
    monkeypatch.setattr(job_prep, "get_cache", lambda: cache)

    jd = "Agent 平台研发，Python 后端，负责 RAG 检索链路。"

    async def _run():
        await job_prep._get_knowledge_for_jd(jd, "u1")                       # preview
        await job_prep._get_knowledge_for_jd(jd, "u1", session_id="sid-1")   # question-gen

    asyncio.run(_run())

    rows = database.get_db().execute(
        "SELECT session_id, topic, stage, context_relevance, chunk_count "
        "FROM rag_metrics"
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["session_id"] == "sid-1"
    assert rows[0]["stage"] == "question_gen"
    assert rows[0]["context_relevance"] == 0.55
    # A JD session spans several topics, so the column holds the joined set.
    assert set(rows[0]["topic"].split(",")) == {"python", "agent"}


def test_pre_upgrade_string_cache_entries_are_still_readable(monkeypatch):
    """A cached plain string from before the {context, metrics} shape must not crash."""
    monkeypatch.setattr(job_prep, "load_topics", lambda _uid: TOPICS)
    cache = _MemoryCache()
    monkeypatch.setattr(job_prep, "get_cache", lambda: cache)

    def _explode(*_a, **_k):
        raise AssertionError("cache hit must not re-retrieve")

    monkeypatch.setattr(job_prep, "retrieve_for_job_prep", _explode)

    jd = "Agent 平台研发，Python 后端。"
    cache.data[job_prep._jd_cache_key(jd, "u1")] = "legacy cached context"

    result = asyncio.run(job_prep._get_knowledge_for_jd(jd, "u1", session_id="sid-2"))

    assert result == "legacy cached context"


# ── 4. a failed evaluation must not permanently block re-evaluation ──

def _strand_pending_marker(session_id: str) -> None:
    """Reproduce a JD eval that died before applying any side effect."""
    eval_token = try_claim_session_evaluation(session_id, user_id="u1")
    sync_token = try_claim_session_sync(
        session_id, user_id="u1", evaluation_token=eval_token,
        target_group="knowledge", target_topics=["agent"],
    )
    release_session_sync_claim(session_id, user_id="u1", claim_token=sync_token)
    release_session_evaluation_claim(session_id, user_id="u1", claim_token=eval_token)


def test_unstarted_pending_marker_no_longer_blocks_re_evaluation(isolated_db):
    create_session("stuck", "jd_prep", questions=[{"id": 1}], meta={}, user_id="u1")
    _strand_pending_marker("stuck")

    # The raw claim still refuses — this is the state that produced the 409 loop.
    assert try_claim_session_evaluation("stuck", user_id="u1") is None

    token = asyncio.run(interview._claim_evaluation_or_409("stuck", "u1"))

    assert token


def test_applied_side_effect_still_blocks_re_evaluation(isolated_db):
    """A partially applied sync keeps its recovery semantics (no double-counting)."""
    create_session("partial", "jd_prep", questions=[{"id": 1}], meta={}, user_id="u1")
    _strand_pending_marker("partial")
    recovery = try_claim_session_sync("partial", user_id="u1")
    mark_session_sync_step("partial", "profile", user_id="u1", claim_token=recovery)
    release_session_sync_claim("partial", user_id="u1", claim_token=recovery)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(interview._claim_evaluation_or_409("partial", "u1"))

    assert exc_info.value.status_code == 409


def test_clear_unstarted_sync_pending_refuses_when_a_step_exists(isolated_db):
    create_session("guard", "jd_prep", questions=[{"id": 1}], meta={}, user_id="u1")
    _strand_pending_marker("guard")
    recovery = try_claim_session_sync("guard", user_id="u1")
    mark_session_sync_step("guard", "profile", user_id="u1", claim_token=recovery)
    release_session_sync_claim("guard", user_id="u1", claim_token=recovery)

    assert clear_unstarted_sync_pending("guard", user_id="u1") is False


def test_clear_unstarted_sync_pending_refuses_while_a_claim_is_live(isolated_db):
    create_session("live", "jd_prep", questions=[{"id": 1}], meta={}, user_id="u1")
    assert try_claim_session_sync("live", user_id="u1", target_group="knowledge")

    assert clear_unstarted_sync_pending("live", user_id="u1") is False
