"""RAG 评测引擎 —— 基于 LLM 自动合成 golden 集的真 RAGAS 指标。

与 backend/rag_metrics.py（出题/评分时顺手免费算的线上健康度仪表，无 ground
truth）不同：本模块从知识库构造带标注的评测集，跑一轮 **离线基准评测**，产出
hit@k / mrr / context_precision / context_recall / faithfulness /
answer_relevancy / answer_correctness。

按需触发，作为后台 asyncio 任务运行、前端轮询进度（见 routers/rag_eval.py）。
全程异步、不阻塞事件循环：知识库加载/切分走 to_thread，检索走已包好的
safe_retrieve_*，嵌入走缓存批量 _embed_many，LLM 只用 .ainvoke。

每题流程（k 默认 8）：
  检索 top-(k+margin) → gold 匹配定 rank（hit@k/mrr）→ 留一法泛化命中（剔源 chunk
  后答案是否仍被覆盖）→ context_precision → context_recall(LLM) → 生成候选答案(LLM)
  → faithfulness(LLM) → answer_relevancy(LLM+嵌入) → answer_correctness(LLM 对照参考答案)

judge_mode:
  "standard"  context_precision 用嵌入锚定（cosine vs 参考答案），其余生成侧 LLM 评判。~6 次 LLM/题。
  "full"      context_precision 改为逐 chunk LLM 判定。~14 次 LLM/题。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass

import numpy as np
from langchain_core.messages import HumanMessage

from backend.vector_store.base import _cosine_similarity
from backend.rag_metrics import _average_precision, _embed_unique
from backend.indexer import (
    get_topic_map, _build_nodes,
)
from backend.rag_ids import stable_chunk_id
from backend.rag_eval_retrievers import (
    get_evaluation_retrieval_config,
    retrieve_for_evaluation,
)
from backend.eval.rag_manifest import (
    METRIC_SEMANTICS_VERSION,
    build_run_manifest,
    finalize_comparison_signature,
    hash_topic_corpus,
    sha256_json,
)
from backend.llm_provider import get_langchain_llm
from backend.prompts.rag_eval import (
    GOLDEN_SYNTH_PROMPT, CONTEXT_RECALL_PROMPT, FAITHFULNESS_PROMPT,
    ANSWER_RELEVANCY_PROMPT, CANDIDATE_ANSWER_PROMPT, CONTEXT_PRECISION_PROMPT,
    ANSWER_CORRECTNESS_PROMPT,
)
from backend.prompts._common import JSON_OUTPUT_DISCIPLINE

logger = logging.getLogger("uvicorn")

_LLM_CONCURRENCY = 4          # 限制并发 ainvoke，避免打爆 channel
_EMBED_CONCURRENCY = 2        # 限制并发检索（含 embedding 调用），避免打爆 embedding key
_RELEVANCY_QUESTIONS = 3      # answer_relevancy 反向生成的问题数
_GOLD_MATCH_COSINE = 0.90     # gold↔检索 chunk 的内容兜底匹配阈值
_TRIVIAL_HIT_COSINE = 0.97    # 命中 chunk 与 gold 源文几乎相同 → 送分自命中
_MIN_CHUNK_CHARS = 80         # 太短的 chunk 不适合合成问题
_LOO_MARGIN = 3               # 检索 top-(k+margin)，留一法剔除源 chunk 后仍留 k 个候选
_LOO_SUPPORT_FLOOR = 0.5      # 留一命中：非源片段与参考答案余弦 ≥ 此值即视为「仍覆盖」
# context_precision(embedding 模式) 的相关阈值。旧版借用 relevance 的 0.5，对「短参考
# 答案 vs 长 chunk」的余弦过严，AP 常塌底。0.35 更贴合该分布。
PRECISION_REL_FLOOR = 0.35

# faithfulness / context_recall 的支撑分档权重。旧版用 supported 布尔，把"沾边"
# 也算满分，导致比率天然偏高。三档加权消除这种乐观偏差。
_SUPPORT_WEIGHTS = {"full": 1.0, "partial": 0.5, "none": 0.0}


def _support_weight(item: dict) -> float:
    """Read a statement/claim's support level → weight. Falls back to the legacy
    boolean ``supported`` field so older cached responses still score."""
    level = item.get("support")
    if isinstance(level, str) and level.lower() in _SUPPORT_WEIGHTS:
        return _SUPPORT_WEIGHTS[level.lower()]
    # Legacy fallback: bool supported → full/none.
    supported = item.get("supported")
    if isinstance(supported, str):
        normalized = supported.strip().lower()
        if normalized in {"true", "1", "yes", "supported", "full"}:
            return 1.0
        if normalized in {"false", "0", "no", "unsupported", "none", ""}:
            return 0.0
        return 0.0
    if supported is True:
        return 1.0
    if isinstance(supported, (int, float)) and not isinstance(supported, bool):
        return 1.0 if supported != 0 else 0.0
    return 0.0


# ── data shapes ──

@dataclass
class GoldItem:
    question: str
    reference_answer: str
    source_file: str
    header_path: str
    content: str
    node_id: str = ""


@dataclass
class QuestionResult:
    question: str
    reference_answer: str
    generated_answer: str
    rank: int | None
    hit: int
    trivial_hit: bool
    loo_hit: int            # leave-one-out 泛化命中：剔除源 chunk 后答案是否仍被覆盖
    context_precision: float
    context_recall: float | None
    faithfulness: float
    answer_relevancy: float
    answer_correctness: float
    match_method: str       # "identity" | "cosine" | "miss"
    gold_source: str
    retrieval_status: str = "ok"
    retrieval_error: str = ""
    retrieval_latency_ms: float = 0.0
    generation_success: bool = True
    judge_successes: int = 0
    judge_attempts: int = 0
    metric_observation_success: bool = True

    def to_dict(self) -> dict:
        def r(v):
            return round(v, 4) if isinstance(v, (int, float)) else v
        return {
            "question": self.question,
            "reference_answer": self.reference_answer,
            "generated_answer": self.generated_answer,
            "rank": self.rank,
            "hit": self.hit,
            "trivial_hit": self.trivial_hit,
            "loo_hit": self.loo_hit,
            "context_precision": r(self.context_precision),
            "context_recall": r(self.context_recall),
            "faithfulness": r(self.faithfulness),
            "answer_relevancy": r(self.answer_relevancy),
            "answer_correctness": r(self.answer_correctness),
            "match_method": self.match_method,
            "gold_source": self.gold_source,
            "retrieval_status": self.retrieval_status,
            "retrieval_error": self.retrieval_error,
            "retrieval_latency_ms": r(self.retrieval_latency_ms),
            "generation_success": self.generation_success,
            "judge_successes": self.judge_successes,
            "judge_attempts": self.judge_attempts,
            "judge_observed_rate": r(
                self.judge_successes / self.judge_attempts
                if self.judge_attempts else None
            ),
            "metric_observation_success": self.metric_observation_success,
        }


@dataclass
class EvalSummary:
    hit_at_k: float
    hit_at_k_strict: float
    mrr: float
    context_precision: float
    context_recall: float | None
    faithfulness: float
    answer_relevancy: float
    answer_correctness: float
    n_questions: int
    error_count: int
    ndcg_at_k: float | None = None
    evaluated_questions: int = 0
    success_rate: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    valid: bool = False
    degraded_count: int = 0
    fully_healthy_rate: float = 0.0
    comparable: bool = False
    generation_success_rate: float | None = None
    judge_observed_rate: float | None = None
    metric_observation_rate: float | None = None

    def to_dict(self) -> dict:
        def r(v):
            return round(v, 4) if isinstance(v, (int, float)) else v
        return {
            "hit_at_k": r(self.hit_at_k),
            "hit_at_k_strict": r(self.hit_at_k_strict),
            "mrr": r(self.mrr),
            "context_precision": r(self.context_precision),
            "context_recall": r(self.context_recall),
            "faithfulness": r(self.faithfulness),
            "answer_relevancy": r(self.answer_relevancy),
            "answer_correctness": r(self.answer_correctness),
            "n_questions": self.n_questions,
            "error_count": self.error_count,
            "ndcg_at_k": r(self.ndcg_at_k),
            "evaluated_questions": self.evaluated_questions,
            "success_rate": r(self.success_rate),
            "latency_p50_ms": r(self.latency_p50_ms),
            "latency_p95_ms": r(self.latency_p95_ms),
            "valid": self.valid,
            "degraded_count": self.degraded_count,
            "fully_healthy_rate": r(self.fully_healthy_rate),
            "comparable": self.comparable,
            "generation_success_rate": r(self.generation_success_rate),
            "judge_observed_rate": r(self.judge_observed_rate),
            "metric_observation_rate": r(self.metric_observation_rate),
        }


# ── small helpers ──

def _normalize_header(meta: dict) -> str:
    """Mirror indexer.retrieve_topic_context_with_scores header normalization so
    gold-node identity matches retrieved-chunk identity exactly."""
    raw = (meta.get("header_path") or "").strip("/")
    return raw.replace("/", " > ") if raw else ""


def _cos(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None or a.shape[0] != b.shape[0]:
        return 0.0
    return float(_cosine_similarity(a, b.reshape(1, -1))[0])


def _parse_json(text: str) -> dict | None:
    """Best-effort JSON extraction: strip code fences, slice first {..last }."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        nl = t.find("\n")
        if nl != -1 and t[:nl].strip().lower() in ("json", ""):
            t = t[nl + 1:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(t[start:end + 1])
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


async def _json_call(llm, prompt: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        try:
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.warning("rag_eval JSON LLM call failed: %s", e)
            return None
    return _parse_json((resp.content or "").strip() if resp else "")


def _load_topic_nodes(topic: str, user_id: str) -> list:
    """Load + chunk the topic KB into the SAME nodes the index is built from
    (identical file_name / header_path metadata). Runs sync inside to_thread."""
    from llama_index.core import SimpleDirectoryReader
    from backend.config import settings

    topic_map = get_topic_map(user_id)
    if topic not in topic_map:
        raise ValueError(f"Unknown topic: {topic}")
    topic_dir = settings.user_knowledge_path(user_id) / topic_map[topic]
    if not topic_dir.exists():
        return []
    docs = SimpleDirectoryReader(
        input_dir=str(topic_dir),
        required_exts=[".md", ".txt", ".py"],
        recursive=True,
    ).load_data()
    return _build_nodes(docs)


# ── phase 1: golden-set synthesis ──

async def synthesize_golden_set(
    topic: str, user_id: str, n: int, sem: asyncio.Semaphore, seed: int = 42,
) -> list[GoldItem]:
    nodes = await asyncio.to_thread(_load_topic_nodes, topic, user_id)
    if not nodes:
        return []
    candidates = [nd for nd in nodes if len((nd.get_content() or "").strip()) >= _MIN_CHUNK_CHARS]
    if not candidates:
        candidates = nodes
    # A local RNG makes node selection reproducible without mutating the process
    # global random state used by other background jobs.
    sample = random.Random(seed).sample(candidates, min(n, len(candidates)))
    llm = get_langchain_llm()

    async def _one(node) -> GoldItem | None:
        content = node.get_content() or ""
        meta = node.metadata if hasattr(node, "metadata") else {}
        prompt = GOLDEN_SYNTH_PROMPT.format(
            chunk=content[:3000], json_discipline=JSON_OUTPUT_DISCIPLINE,
        )
        data = await _json_call(llm, prompt, sem)
        if not data:
            return None
        q = (data.get("question") or "").strip()
        a = (data.get("reference_answer") or "").strip()
        if not q or not a:
            return None
        return GoldItem(
            question=q, reference_answer=a,
            source_file=meta.get("file_name", ""),
            header_path=_normalize_header(meta),
            content=content,
            node_id=stable_chunk_id(
                content,
                meta.get("file_name", ""),
                _normalize_header(meta),
            ),
        )

    results = await asyncio.gather(*[_one(nd) for nd in sample])
    return [r for r in results if r is not None]


# ── phase 2: per-question metrics ──

async def _match_gold(gold: GoldItem, retrieved: list) -> tuple[int | None, str, bool]:
    """Locate gold chunk in the ranked retrieval → (1-based rank, method, trivial).

    Stable node identity includes a content hash. This matters for long Markdown
    sections: SentenceSplitter creates several nodes that share file + header,
    so metadata-only matching would report false hits.

    ``trivial`` flags a self-hit: the matched chunk is (near-)identical to the
    very chunk the gold question was synthesized from. Finding your own source
    text is expected and inflates hit@k — hit_at_k_strict excludes these.
    """
    if not retrieved:
        return None, "miss", False
    # Embed gold source + retrieved content once; reused for both cosine fallback
    # match and trivial-hit detection.
    emb_map = await _embed_unique([gold.content] + [c.content for c in retrieved])
    gold_emb = emb_map.get(gold.content)

    def _is_trivial(idx: int) -> bool:
        return _cos(gold_emb, emb_map.get(retrieved[idx].content)) >= _TRIVIAL_HIT_COSINE

    gold_node_id = gold.node_id or stable_chunk_id(
        gold.content, gold.source_file, gold.header_path,
    )
    for i, c in enumerate(retrieved):
        candidate_id = c.node_id or stable_chunk_id(
            c.content, c.source_file, c.header_path,
        )
        if candidate_id == gold_node_id:
            return i + 1, "identity", _is_trivial(i)
    if gold_emb is None:
        return None, "miss", False
    for i, c in enumerate(retrieved):
        if _cos(gold_emb, emb_map.get(c.content)) >= _GOLD_MATCH_COSINE:
            return i + 1, "cosine", _is_trivial(i)
    return None, "miss", False


async def _leave_one_out_hit(gold: GoldItem, retrieved_ext: list, k: int) -> int:
    """Leave-one-out 泛化命中（纯嵌入，无额外 LLM）。

    把 gold 自己的源 chunk 从检索结果里剔除，取剩余前 k 个，判断参考答案是否仍被
    某个「非源」片段覆盖（cosine ≥ _LOO_SUPPORT_FLOOR）。衡量冗余度 / 真泛化——
    系统能否在不依赖那段原文的情况下仍召回答案。替代旧的「排除自命中」严格命中
    （对 1:1 自合成 golden 集结构性恒为 0）。
    """
    if not retrieved_ext:
        return 0
    emb_map = await _embed_unique(
        [gold.content, gold.reference_answer] + [c.content for c in retrieved_ext]
    )
    ref_emb = emb_map.get(gold.reference_answer)
    if ref_emb is None:
        return 0
    gold_emb = emb_map.get(gold.content)

    def _is_source(c) -> bool:
        gold_node_id = gold.node_id or stable_chunk_id(
            gold.content, gold.source_file, gold.header_path,
        )
        candidate_id = c.node_id or stable_chunk_id(
            c.content, c.source_file, c.header_path,
        )
        if candidate_id == gold_node_id:
            return True
        if gold_emb is not None and _cos(gold_emb, emb_map.get(c.content)) >= _TRIVIAL_HIT_COSINE:
            return True
        return False

    survivors = [c for c in retrieved_ext if not _is_source(c)][:k]
    for c in survivors:
        if _cos(ref_emb, emb_map.get(c.content)) >= _LOO_SUPPORT_FLOOR:
            return 1
    return 0


async def _precision_embedding(gold: GoldItem, retrieved: list) -> float | None:
    if not retrieved:
        return 0.0
    emb_map = await _embed_unique([gold.reference_answer] + [c.content for c in retrieved])
    ref_emb = emb_map.get(gold.reference_answer)
    if ref_emb is None:
        return None
    scores = [_cos(ref_emb, emb_map.get(c.content)) for c in retrieved]
    return _average_precision(scores, PRECISION_REL_FLOOR)


async def _precision_llm(
    gold: GoldItem, retrieved: list, sem: asyncio.Semaphore,
) -> tuple[float, int, int]:
    if not retrieved:
        return 0.0, 0, 0
    llm = get_langchain_llm()

    async def _judge(c) -> float | None:
        prompt = CONTEXT_PRECISION_PROMPT.format(
            question=gold.question, reference_answer=gold.reference_answer,
            context=(c.content or "")[:2000], json_discipline=JSON_OUTPUT_DISCIPLINE,
        )
        data = await _json_call(llm, prompt, sem)
        if not data or not isinstance(data.get("relevant"), bool):
            return None
        return 1.0 if data["relevant"] else 0.0

    flags = await asyncio.gather(*[_judge(c) for c in retrieved])
    observed = sum(flag is not None for flag in flags)
    scored = [flag if flag is not None else 0.0 for flag in flags]
    return _average_precision(scored, 0.5), observed, len(flags)


async def _recall_llm(gold: GoldItem, context_text: str, sem: asyncio.Semaphore) -> float | None:
    llm = get_langchain_llm()
    prompt = CONTEXT_RECALL_PROMPT.format(
        reference_answer=gold.reference_answer, context=context_text[:6000],
        json_discipline=JSON_OUTPUT_DISCIPLINE,
    )
    data = await _json_call(llm, prompt, sem)
    if not data:
        return None
    stmts = data.get("statements")
    if not isinstance(stmts, list) or not stmts:
        return None
    total = 0
    weighted = 0.0
    for s in stmts:
        if not isinstance(s, dict):
            continue
        total += 1
        weighted += _support_weight(s)
    return weighted / total if total else None


async def _generate_answer(
    question: str, context_text: str, sem: asyncio.Semaphore,
) -> str | None:
    llm = get_langchain_llm()
    prompt = CANDIDATE_ANSWER_PROMPT.format(question=question, context=context_text[:6000])
    async with sem:
        try:
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.warning("rag_eval answer generation failed: %s", e)
            return None
    answer = (resp.content or "").strip() if resp else ""
    return answer or None


async def _faithfulness_llm(
    answer: str, context_text: str, sem: asyncio.Semaphore,
) -> float | None:
    if not answer:
        return None
    llm = get_langchain_llm()
    prompt = FAITHFULNESS_PROMPT.format(
        answer=answer, context=context_text[:6000], json_discipline=JSON_OUTPUT_DISCIPLINE,
    )
    data = await _json_call(llm, prompt, sem)
    if not data:
        return None
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        return None
    total = 0
    weighted = 0.0
    for c in claims:
        if not isinstance(c, dict):
            continue
        total += 1
        weighted += _support_weight(c)
    return weighted / total if total else None


async def _answer_relevancy(
    answer: str, question: str, sem: asyncio.Semaphore,
) -> float | None:
    if not answer:
        return None
    llm = get_langchain_llm()
    prompt = ANSWER_RELEVANCY_PROMPT.format(
        answer=answer, n_questions=_RELEVANCY_QUESTIONS, json_discipline=JSON_OUTPUT_DISCIPLINE,
    )
    data = await _json_call(llm, prompt, sem)
    if not data:
        return None
    gen_qs = [q for q in (data.get("questions") or []) if isinstance(q, str) and q.strip()]
    if not gen_qs:
        return None
    emb_map = await _embed_unique([question] + gen_qs)
    q_emb = emb_map.get(question)
    if q_emb is None:
        return None
    sims = [_cos(q_emb, emb_map.get(gq)) for gq in gen_qs]
    # Clamp negatives to 0 instead of dropping them: a generated question that is
    # unrelated/opposite to the original (cosine <= 0) is a *low* relevancy signal,
    # not an absent one. Dropping it inflated the mean.
    sims = [max(0.0, s) for s in sims]
    if not sims:
        return None
    return max(0.0, min(1.0, float(np.mean(sims))))


async def _answer_correctness(
    answer: str, reference: str, sem: asyncio.Semaphore,
) -> float | None:
    """LLM 判定事实正确性：把生成答案拆成断言，逐条对照参考答案打 full/partial/none
    加权（与 _faithfulness_llm / _recall_llm 同构）。取代旧的纯 cosine(answer, reference)
    ——后者无事实核对、且两段中文余弦天然偏高，落在乐观带。"""
    if not answer or not reference:
        return None
    llm = get_langchain_llm()
    prompt = ANSWER_CORRECTNESS_PROMPT.format(
        answer=answer, reference_answer=reference, json_discipline=JSON_OUTPUT_DISCIPLINE,
    )
    data = await _json_call(llm, prompt, sem)
    if not data:
        return None
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        return None
    total = 0
    weighted = 0.0
    for c in claims:
        if not isinstance(c, dict):
            continue
        total += 1
        weighted += _support_weight(c)
    return weighted / total if total else None


async def evaluate_question(
    gold: GoldItem, topic: str, user_id: str, k: int, judge_mode: str,
    sem: asyncio.Semaphore, embed_sem: asyncio.Semaphore,
    retrieval_mode: str = "atomic_dense",
    retrieval_config: dict[str, float | int] | None = None,
) -> QuestionResult:
    # Retrieve a few extra so leave-one-out still has k candidates after dropping
    # the gold's own source chunk. Standard metrics use the top-k slice.
    # 检索内部含 embedding 调用：用独立的 embed_sem 限流，避免 N 题同时打 embedding
    # key 触发上游限流。只包检索这一步，下游 LLM 指标仍走各自的 sem，不串行化整题。
    async with embed_sem:
        outcome = await retrieve_for_evaluation(
            topic=topic,
            user_id=user_id,
            queries=[gold.question],
            fallback_query=f"{topic} 核心知识点 面试常见问题",
            mode=retrieval_mode,
            k=k + _LOO_MARGIN,
            retrieval_config=retrieval_config,
        )
    retrieved_ext = outcome.chunks
    retrieved = retrieved_ext[:k]
    gold_source = gold.source_file + (f" [{gold.header_path}]" if gold.header_path else "")
    if not outcome.measured:
        return QuestionResult(
            question=gold.question,
            reference_answer=gold.reference_answer,
            generated_answer="",
            rank=None,
            hit=0,
            trivial_hit=False,
            loo_hit=0,
            context_precision=0.0,
            context_recall=None,
            faithfulness=0.0,
            answer_relevancy=0.0,
            answer_correctness=0.0,
            match_method="infra_error",
            gold_source=gold_source,
            retrieval_status=outcome.status,
            retrieval_error=outcome.error_code or outcome.error,
            retrieval_latency_ms=outcome.latency_ms,
            generation_success=False,
            metric_observation_success=False,
        )
    rank, match_method, trivial_hit = await _match_gold(gold, retrieved)
    hit = 1 if rank is not None else 0
    loo_hit = await _leave_one_out_hit(gold, retrieved_ext, k)

    context_text = (
        "\n\n---\n\n".join((c.content or "") for c in retrieved) if retrieved else "（无检索结果）"
    )

    if judge_mode == "full":
        precision, precision_judge_successes, precision_judge_attempts = (
            await _precision_llm(gold, retrieved, sem)
        )
        metric_observation_success = (
            precision_judge_successes == precision_judge_attempts
        )
    else:
        precision_raw = await _precision_embedding(gold, retrieved)
        precision = precision_raw if precision_raw is not None else 0.0
        precision_judge_successes = 0
        precision_judge_attempts = 0
        metric_observation_success = precision_raw is not None

    recall = await _recall_llm(gold, context_text, sem)
    a_gen = await _generate_answer(gold.question, context_text, sem)
    faith = await _faithfulness_llm(a_gen or "", context_text, sem)
    relevancy = await _answer_relevancy(a_gen or "", gold.question, sem)
    correctness = await _answer_correctness(
        a_gen or "", gold.reference_answer, sem,
    )
    judge_successes = precision_judge_successes + sum(
        value is not None for value in (recall, faith, relevancy, correctness)
    )
    judge_attempts = precision_judge_attempts + 4

    return QuestionResult(
        question=gold.question,
        reference_answer=gold.reference_answer,
        generated_answer=a_gen or "",
        rank=rank,
        hit=hit,
        trivial_hit=trivial_hit,
        loo_hit=loo_hit,
        context_precision=precision,
        context_recall=recall,
        faithfulness=faith if faith is not None else 0.0,
        answer_relevancy=relevancy if relevancy is not None else 0.0,
        answer_correctness=correctness if correctness is not None else 0.0,
        match_method=match_method,
        gold_source=gold_source,
        retrieval_status=outcome.status,
        retrieval_error=outcome.error_code,
        retrieval_latency_ms=outcome.latency_ms,
        generation_success=a_gen is not None,
        judge_successes=judge_successes,
        judge_attempts=judge_attempts,
        metric_observation_success=metric_observation_success,
    )


# ── phase 3: aggregation ──

def _aggregate(
    results: list[QuestionResult], error_count: int, total_questions: int | None = None,
) -> EvalSummary:
    total = total_questions if total_questions is not None else len(results) + error_count

    def _mean(vals: list) -> float:
        vals = [v for v in vals if v is not None]
        # Unexpected question failures and missing judge observations retain a
        # zero contribution instead of disappearing from the denominator.
        return float(sum(vals) / total) if total else 0.0

    measured = sum(
        1 for r in results if r.retrieval_status in {"ok", "empty", "degraded"}
    )
    retrieval_errors = len(results) - measured
    total_errors = error_count + retrieval_errors
    degraded_count = sum(1 for r in results if r.retrieval_status == "degraded")
    fully_healthy = sum(
        1 for r in results if r.retrieval_status in {"ok", "empty"}
    )
    measured_results = [
        r for r in results if r.retrieval_status in {"ok", "empty", "degraded"}
    ]
    generation_success_rate = (
        sum(r.generation_success for r in measured_results) / len(measured_results)
        if measured_results else None
    )
    judge_attempts = sum(r.judge_attempts for r in measured_results)
    judge_observed_rate = (
        sum(r.judge_successes for r in measured_results) / judge_attempts
        if judge_attempts else None
    )
    metric_observation_rate = (
        sum(r.metric_observation_success for r in measured_results) / len(measured_results)
        if measured_results else None
    )
    if not results:
        return EvalSummary(
            0.0, 0.0, 0.0, 0.0, None, 0.0, 0.0, 0.0,
            total, total_errors,
            evaluated_questions=0,
            success_rate=0.0,
            valid=False,
            degraded_count=0,
            fully_healthy_rate=0.0,
            comparable=False,
            generation_success_rate=None,
            judge_observed_rate=None,
            metric_observation_rate=None,
        )

    recalls = [r.context_recall for r in results if r.context_recall is not None]
    # hit_at_k_strict = leave-one-out 泛化命中率：剔除 gold 自身源 chunk 后，答案是否
    # 仍被其它片段覆盖。比裸 hit@k 更接近真实泛化检索（裸 hit@k 含自命中送分）。
    latencies = [r.retrieval_latency_ms for r in results]
    success_rate = measured / total if total else 0.0
    return EvalSummary(
        hit_at_k=_mean([r.hit for r in results]),
        hit_at_k_strict=_mean([r.loo_hit for r in results]),
        mrr=_mean([(1.0 / r.rank if r.rank else 0.0) for r in results]),
        context_precision=_mean([r.context_precision for r in results]),
        context_recall=(_mean(recalls) if recalls else None),
        faithfulness=_mean([r.faithfulness for r in results]),
        answer_relevancy=_mean([r.answer_relevancy for r in results]),
        answer_correctness=_mean([r.answer_correctness for r in results]),
        n_questions=total,
        error_count=total_errors,
        evaluated_questions=measured,
        success_rate=success_rate,
        latency_p50_ms=float(np.percentile(latencies, 50)) if latencies else 0.0,
        latency_p95_ms=float(np.percentile(latencies, 95)) if latencies else 0.0,
        valid=bool(total) and success_rate >= 0.95,
        degraded_count=degraded_count,
        fully_healthy_rate=fully_healthy / total if total else 0.0,
        comparable=(
            bool(total)
            and success_rate >= 0.95
            and degraded_count == 0
            and total_errors == 0
            and generation_success_rate == 1.0
            and judge_observed_rate == 1.0
            and metric_observation_rate == 1.0
        ),
        generation_success_rate=generation_success_rate,
        judge_observed_rate=judge_observed_rate,
        metric_observation_rate=metric_observation_rate,
    )


# ── orchestrator (background task target) ──

async def run_eval(
    job: dict,
    topic: str,
    user_id: str,
    n: int,
    k: int,
    judge_mode: str,
    retrieval_mode: str = "atomic_dense",
    seed: int = 42,
) -> None:
    """Run the full eval, mutating `job` in place for progress polling. On success
    persists a rag_eval_runs row. One outer guard flips status→failed (no broad
    per-step try/except — individual question failures already degrade gracefully)."""
    from backend.storage.rag_eval_store import (
        RagEvalPersistenceFenceError,
        save_failed_rag_eval_run,
        save_rag_eval_run,
    )

    sem = asyncio.Semaphore(_LLM_CONCURRENCY)
    embed_sem = asyncio.Semaphore(_EMBED_CONCURRENCY)
    manifest: dict = {}

    async def _persist_failure(message: str) -> None:
        try:
            job["run_id"] = await asyncio.to_thread(
                save_failed_rag_eval_run,
                job_id=job["job_id"],
                user_id=user_id,
                topic=topic,
                scope=job.get("scope", "topic"),
                n_questions=int(job.get("total") or n),
                k=k,
                judge_mode=judge_mode,
                eval_kind="synthetic_e2e",
                retrieval_mode=retrieval_mode,
                seed=seed,
                error=message,
                manifest=manifest,
                detail={
                    "phase": job.get("phase", "failed"),
                    "done": job.get("done", 0),
                    "total": job.get("total", 0),
                },
                durable_claim=job.get("_durable_claim"),
            )
        except RagEvalPersistenceFenceError:
            raise
        except Exception:
            logger.exception("failed to persist failed synthetic RAG eval")

    try:
        job["status"] = "running"
        job["phase"] = "synthesizing"
        job["updated_at"] = time.time()

        gold = await synthesize_golden_set(topic, user_id, n, sem, seed=seed)
        if not gold:
            job["status"] = "failed"
            job["phase"] = "failed"
            job["error"] = "无法从该主题知识库合成评测集（知识库为空或文档过短）"
            job["updated_at"] = time.time()
            await _persist_failure(job["error"])
            return

        job["total"] = len(gold)
        job["done"] = 0
        job["phase"] = "evaluating"
        job["updated_at"] = time.time()

        corpus_hash, corpus_file_count = await asyncio.to_thread(
            hash_topic_corpus, topic, user_id,
        )
        golden_hash = sha256_json([
            {
                "question": g.question,
                "reference_answer": g.reference_answer,
                "source_file": g.source_file,
                "header_path": g.header_path,
                "node_id": g.node_id,
            }
            for g in gold
        ])
        fallback_query = f"{topic} 核心知识点 面试常见问题"
        prompt_hash = sha256_json({
            "golden": GOLDEN_SYNTH_PROMPT,
            "context_recall": CONTEXT_RECALL_PROMPT,
            "faithfulness": FAITHFULNESS_PROMPT,
            "answer_relevancy": ANSWER_RELEVANCY_PROMPT,
            "candidate_answer": CANDIDATE_ANSWER_PROMPT,
            "context_precision": CONTEXT_PRECISION_PROMPT if judge_mode == "full" else "embedding",
            "answer_correctness": ANSWER_CORRECTNESS_PROMPT,
            "json_output_discipline": JSON_OUTPUT_DISCIPLINE,
        })
        protocol = {
            "case_selection": "seeded_random_without_replacement",
            "golden_generation": "llm_generated",
            "fallback_query_hash": sha256_json(fallback_query),
            "leave_one_out_margin": _LOO_MARGIN,
            "gold_match_cosine": _GOLD_MATCH_COSINE,
            "trivial_hit_cosine": _TRIVIAL_HIT_COSINE,
            "leave_one_out_support_floor": _LOO_SUPPORT_FLOOR,
            "precision_relevance_floor": PRECISION_REL_FLOOR,
            "relevancy_questions": _RELEVANCY_QUESTIONS,
            "answer_generation": "llm_generated",
        }
        retrieval_config = get_evaluation_retrieval_config()
        manifest_kwargs = {
            "eval_kind": "synthetic_e2e",
            "retrieval_mode": retrieval_mode,
            "topic": topic,
            "user_id": user_id,
            "dataset_id": "synthetic-golden",
            "dataset_version": "generated",
            "dataset_hash": golden_hash,
            "seed": seed,
            "case_ids": [g.node_id for g in gold],
            "corpus_hash": corpus_hash,
            "corpus_file_count": corpus_file_count,
            "k": k,
            "judge_mode": judge_mode,
            "prompt_hash": prompt_hash,
            "protocol": protocol,
            "retrieval_config_snapshot": retrieval_config,
        }
        manifest = build_run_manifest(**manifest_kwargs)
        initial_comparison_signature = manifest["comparison_signature"]
        job["manifest"] = manifest

        async def _eval_one(g: GoldItem) -> QuestionResult | None:
            try:
                res = await evaluate_question(
                    g, topic, user_id, k, judge_mode, sem, embed_sem, retrieval_mode,
                    retrieval_config,
                )
            except Exception as e:
                logger.warning("rag_eval question failed: %s", e)
                res = None
            job["done"] = job.get("done", 0) + 1   # no await between read+write → atomic
            job["updated_at"] = time.time()
            return res

        # return_exceptions=True：单题被取消/抛出（如逃出内层 try 的 CancelledError）
        # 不应炸掉整批。聚合前过滤掉 Exception 实例（参考 graphs/rag_retrieval.py:94-97）。
        results = await asyncio.gather(
            *[_eval_one(g) for g in gold],
            return_exceptions=True,
        )
        ok = [r for r in results if isinstance(r, QuestionResult)]
        error_count = len(results) - len(ok)

        job["phase"] = "aggregating"
        job["updated_at"] = time.time()
        summary = _aggregate(ok, error_count, total_questions=len(results))
        statuses = [result.retrieval_status for result in ok]
        if error_count:
            execution_profile = "question_failure"
        elif any(status not in {"ok", "empty", "degraded"} for status in statuses):
            execution_profile = "infrastructure_failure"
        elif any(status == "degraded" for status in statuses):
            execution_profile = "degraded"
        elif (
            summary.generation_success_rate != 1.0
            or summary.judge_observed_rate != 1.0
            or summary.metric_observation_rate != 1.0
        ):
            execution_profile = "evaluation_degraded"
        else:
            execution_profile = "healthy"

        post_corpus_hash, post_corpus_file_count = await asyncio.to_thread(
            hash_topic_corpus, topic, user_id,
        )
        post_manifest = build_run_manifest(**{
            **manifest_kwargs,
            "corpus_hash": post_corpus_hash,
            "corpus_file_count": post_corpus_file_count,
        })
        state_stable = post_manifest["comparison_signature"] == initial_comparison_signature
        if not state_stable:
            execution_profile = "state_changed_during_run"
        manifest["post_run_comparison_signature"] = post_manifest["comparison_signature"]
        manifest["state_stable"] = state_stable
        manifest["observations"] = {
            "generation_success_rate": summary.generation_success_rate,
            "judge_observed_rate": summary.judge_observed_rate,
            "metric_observation_rate": summary.metric_observation_rate,
            "degraded_count": summary.degraded_count,
        }
        finalize_comparison_signature(manifest, execution_profile=execution_profile)
        summary.comparable = bool(
            summary.valid and state_stable and execution_profile == "healthy"
        )
        detail = {
            "metric_semantics_version": METRIC_SEMANTICS_VERSION,
            "eval_kind": "synthetic_e2e",
            "retrieval_mode": retrieval_mode,
            "seed": seed,
            "manifest": manifest,
            "judge_mode": judge_mode,
            "k": k,
            "error_count": summary.error_count,
            "question_exception_count": error_count,
            "execution_profile": execution_profile,
            "state_stable": state_stable,
            "questions": [r.to_dict() for r in ok],
        }

        run_id = await asyncio.to_thread(
            save_rag_eval_run,
            job_id=job["job_id"], user_id=user_id, topic=topic,
            scope=job.get("scope", "topic"), n_questions=len(gold), k=k, judge_mode=judge_mode,
            hit_at_k=summary.hit_at_k, mrr=summary.mrr,
            hit_at_k_strict=summary.hit_at_k_strict,
            ndcg_at_k=summary.ndcg_at_k,
            context_precision=summary.context_precision, context_recall=summary.context_recall,
            faithfulness=summary.faithfulness, answer_relevancy=summary.answer_relevancy,
            answer_correctness=summary.answer_correctness,
            success_rate=summary.success_rate,
            latency_p50_ms=summary.latency_p50_ms,
            latency_p95_ms=summary.latency_p95_ms,
            eval_kind="synthetic_e2e",
            retrieval_mode=retrieval_mode,
            dataset_id="synthetic-golden",
            dataset_version="generated",
            dataset_hash=golden_hash,
            corpus_hash=corpus_hash,
            seed=seed,
            status="completed", error="", detail=detail,
            manifest=manifest,
            durable_claim=job.get("_durable_claim"),
        )

        job["summary"] = summary.to_dict()
        job["detail"] = detail
        job["run_id"] = run_id
        job["status"] = "completed"
        job["phase"] = "completed"
        job["updated_at"] = time.time()
    except RagEvalPersistenceFenceError:
        raise
    except Exception as e:
        logger.exception("rag_eval run failed")
        job["status"] = "failed"
        job["phase"] = "failed"
        job["error"] = str(e)[:300]
        job["updated_at"] = time.time()
        await _persist_failure(job["error"])
