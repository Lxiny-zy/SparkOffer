"""RAG 评测引擎 —— 基于 LLM 自动合成 golden 集的真 RAGAS 指标。

与 backend/rag_metrics.py（出题/评分时顺手免费算的线上健康度仪表，无 ground
truth）不同：本模块从知识库构造带标注的评测集，跑一轮 **离线基准评测**，产出
hit@k / mrr / context_precision / context_recall / faithfulness /
answer_relevancy / answer_correctness。

按需触发，作为后台 asyncio 任务运行、前端轮询进度（见 routers/rag_eval.py）。
全程异步、不阻塞事件循环：知识库加载/切分走 to_thread，检索走已包好的
safe_retrieve_*，嵌入走缓存批量 _embed_many，LLM 只用 .ainvoke。

每题流程（k 默认 8）：
  检索 top-k → gold 匹配定 rank（hit@k/mrr）→ context_precision →
  context_recall(LLM) → 生成候选答案(LLM) → faithfulness(LLM) →
  answer_relevancy(LLM+嵌入) → answer_correctness(嵌入)

judge_mode:
  "standard"  context_precision 用嵌入锚定（cosine vs 参考答案），其余生成侧 LLM 评判。~5 次 LLM/题。
  "full"      context_precision 改为逐 chunk LLM 判定。~13 次 LLM/题。
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
from backend.rag_metrics import _average_precision, _embed_unique, RELEVANCE_THRESHOLD
from backend.indexer import (
    safe_retrieve_topic_context_with_scores, get_topic_map, _build_nodes,
)
from backend.llm_provider import get_langchain_llm
from backend.prompts.rag_eval import (
    GOLDEN_SYNTH_PROMPT, CONTEXT_RECALL_PROMPT, FAITHFULNESS_PROMPT,
    ANSWER_RELEVANCY_PROMPT, CANDIDATE_ANSWER_PROMPT, CONTEXT_PRECISION_PROMPT,
)
from backend.prompts._common import JSON_OUTPUT_DISCIPLINE

logger = logging.getLogger("uvicorn")

_LLM_CONCURRENCY = 4          # 限制并发 ainvoke，避免打爆 channel
_RELEVANCY_QUESTIONS = 3      # answer_relevancy 反向生成的问题数
_GOLD_MATCH_COSINE = 0.90     # gold↔检索 chunk 的内容兜底匹配阈值
_MIN_CHUNK_CHARS = 80         # 太短的 chunk 不适合合成问题


# ── data shapes ──

@dataclass
class GoldItem:
    question: str
    reference_answer: str
    source_file: str
    header_path: str
    content: str


@dataclass
class QuestionResult:
    question: str
    reference_answer: str
    generated_answer: str
    rank: int | None
    hit: int
    context_precision: float
    context_recall: float | None
    faithfulness: float
    answer_relevancy: float
    answer_correctness: float
    match_method: str       # "identity" | "cosine" | "miss"
    gold_source: str

    def to_dict(self) -> dict:
        def r(v):
            return round(v, 4) if isinstance(v, (int, float)) else v
        return {
            "question": self.question,
            "reference_answer": self.reference_answer,
            "generated_answer": self.generated_answer,
            "rank": self.rank,
            "hit": self.hit,
            "context_precision": r(self.context_precision),
            "context_recall": r(self.context_recall),
            "faithfulness": r(self.faithfulness),
            "answer_relevancy": r(self.answer_relevancy),
            "answer_correctness": r(self.answer_correctness),
            "match_method": self.match_method,
            "gold_source": self.gold_source,
        }


@dataclass
class EvalSummary:
    hit_at_k: float
    mrr: float
    context_precision: float
    context_recall: float | None
    faithfulness: float
    answer_relevancy: float
    answer_correctness: float
    n_questions: int
    error_count: int

    def to_dict(self) -> dict:
        def r(v):
            return round(v, 4) if isinstance(v, (int, float)) else v
        return {
            "hit_at_k": r(self.hit_at_k),
            "mrr": r(self.mrr),
            "context_precision": r(self.context_precision),
            "context_recall": r(self.context_recall),
            "faithfulness": r(self.faithfulness),
            "answer_relevancy": r(self.answer_relevancy),
            "answer_correctness": r(self.answer_correctness),
            "n_questions": self.n_questions,
            "error_count": self.error_count,
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
    topic: str, user_id: str, n: int, sem: asyncio.Semaphore,
) -> list[GoldItem]:
    nodes = await asyncio.to_thread(_load_topic_nodes, topic, user_id)
    if not nodes:
        return []
    candidates = [nd for nd in nodes if len((nd.get_content() or "").strip()) >= _MIN_CHUNK_CHARS]
    if not candidates:
        candidates = nodes
    sample = random.sample(candidates, min(n, len(candidates)))
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
        )

    results = await asyncio.gather(*[_one(nd) for nd in sample])
    return [r for r in results if r is not None]


# ── phase 2: per-question metrics ──

async def _match_gold(gold: GoldItem, retrieved: list) -> tuple[int | None, str]:
    """Locate gold chunk in the ranked retrieval → (1-based rank, method).

    Identity match is only reliable for .md chunks (non-empty header_path);
    .txt/.py share an empty header within a file, so fall back to content cosine.
    """
    if not retrieved:
        return None, "miss"
    if gold.source_file and gold.header_path:
        for i, c in enumerate(retrieved):
            if c.source_file == gold.source_file and c.header_path == gold.header_path:
                return i + 1, "identity"
    emb_map = await _embed_unique([gold.content] + [c.content for c in retrieved])
    gold_emb = emb_map.get(gold.content)
    if gold_emb is None:
        return None, "miss"
    for i, c in enumerate(retrieved):
        if _cos(gold_emb, emb_map.get(c.content)) >= _GOLD_MATCH_COSINE:
            return i + 1, "cosine"
    return None, "miss"


async def _precision_embedding(gold: GoldItem, retrieved: list) -> float:
    if not retrieved:
        return 0.0
    emb_map = await _embed_unique([gold.reference_answer] + [c.content for c in retrieved])
    ref_emb = emb_map.get(gold.reference_answer)
    if ref_emb is None:
        return 0.0
    scores = [_cos(ref_emb, emb_map.get(c.content)) for c in retrieved]
    return _average_precision(scores, RELEVANCE_THRESHOLD)


async def _precision_llm(gold: GoldItem, retrieved: list, sem: asyncio.Semaphore) -> float:
    if not retrieved:
        return 0.0
    llm = get_langchain_llm()

    async def _judge(c) -> float:
        prompt = CONTEXT_PRECISION_PROMPT.format(
            question=gold.question, reference_answer=gold.reference_answer,
            context=(c.content or "")[:2000], json_discipline=JSON_OUTPUT_DISCIPLINE,
        )
        data = await _json_call(llm, prompt, sem)
        return 1.0 if (data and bool(data.get("relevant"))) else 0.0

    flags = await asyncio.gather(*[_judge(c) for c in retrieved])
    return _average_precision(list(flags), 0.5)


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
    total = supported = 0
    for s in stmts:
        if not isinstance(s, dict):
            continue
        total += 1
        if bool(s.get("supported")):
            supported += 1
    return supported / total if total else None


async def _generate_answer(question: str, context_text: str, sem: asyncio.Semaphore) -> str:
    llm = get_langchain_llm()
    prompt = CANDIDATE_ANSWER_PROMPT.format(question=question, context=context_text[:6000])
    async with sem:
        try:
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
        except Exception as e:
            logger.warning("rag_eval answer generation failed: %s", e)
            return ""
    return (resp.content or "").strip() if resp else ""


async def _faithfulness_llm(answer: str, context_text: str, sem: asyncio.Semaphore) -> float:
    if not answer:
        return 0.0
    llm = get_langchain_llm()
    prompt = FAITHFULNESS_PROMPT.format(
        answer=answer, context=context_text[:6000], json_discipline=JSON_OUTPUT_DISCIPLINE,
    )
    data = await _json_call(llm, prompt, sem)
    if not data:
        return 0.0
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        return 0.0
    total = supported = 0
    for c in claims:
        if not isinstance(c, dict):
            continue
        total += 1
        if bool(c.get("supported")):
            supported += 1
    return supported / total if total else 0.0


async def _answer_relevancy(answer: str, question: str, sem: asyncio.Semaphore) -> float:
    if not answer:
        return 0.0
    llm = get_langchain_llm()
    prompt = ANSWER_RELEVANCY_PROMPT.format(
        answer=answer, n_questions=_RELEVANCY_QUESTIONS, json_discipline=JSON_OUTPUT_DISCIPLINE,
    )
    data = await _json_call(llm, prompt, sem)
    if not data:
        return 0.0
    gen_qs = [q for q in (data.get("questions") or []) if isinstance(q, str) and q.strip()]
    if not gen_qs:
        return 0.0
    emb_map = await _embed_unique([question] + gen_qs)
    q_emb = emb_map.get(question)
    if q_emb is None:
        return 0.0
    sims = [_cos(q_emb, emb_map.get(gq)) for gq in gen_qs]
    sims = [s for s in sims if s > 0.0]
    if not sims:
        return 0.0
    return max(0.0, min(1.0, float(np.mean(sims))))


async def _answer_correctness(answer: str, reference: str) -> float:
    if not answer or not reference:
        return 0.0
    emb_map = await _embed_unique([answer, reference])
    return max(0.0, min(1.0, _cos(emb_map.get(answer), emb_map.get(reference))))


async def evaluate_question(
    gold: GoldItem, topic: str, user_id: str, k: int, judge_mode: str, sem: asyncio.Semaphore,
) -> QuestionResult:
    retrieved = await safe_retrieve_topic_context_with_scores(topic, gold.question, user_id, top_k=k)
    rank, match_method = await _match_gold(gold, retrieved)
    hit = 1 if rank is not None else 0

    context_text = (
        "\n\n---\n\n".join((c.content or "") for c in retrieved) if retrieved else "（无检索结果）"
    )

    if judge_mode == "full":
        precision = await _precision_llm(gold, retrieved, sem)
    else:
        precision = await _precision_embedding(gold, retrieved)

    recall = await _recall_llm(gold, context_text, sem)
    a_gen = await _generate_answer(gold.question, context_text, sem)
    faith = await _faithfulness_llm(a_gen, context_text, sem)
    relevancy = await _answer_relevancy(a_gen, gold.question, sem)
    correctness = await _answer_correctness(a_gen, gold.reference_answer)

    gold_source = gold.source_file + (f" [{gold.header_path}]" if gold.header_path else "")
    return QuestionResult(
        question=gold.question,
        reference_answer=gold.reference_answer,
        generated_answer=a_gen,
        rank=rank,
        hit=hit,
        context_precision=precision,
        context_recall=recall,
        faithfulness=faith,
        answer_relevancy=relevancy,
        answer_correctness=correctness,
        match_method=match_method,
        gold_source=gold_source,
    )


# ── phase 3: aggregation ──

def _aggregate(results: list[QuestionResult], error_count: int) -> EvalSummary:
    def _mean(vals: list) -> float:
        vals = [v for v in vals if v is not None]
        return float(np.mean(vals)) if vals else 0.0

    if not results:
        return EvalSummary(0.0, 0.0, 0.0, None, 0.0, 0.0, 0.0, 0, error_count)

    recalls = [r.context_recall for r in results if r.context_recall is not None]
    return EvalSummary(
        hit_at_k=_mean([r.hit for r in results]),
        mrr=_mean([(1.0 / r.rank if r.rank else 0.0) for r in results]),
        context_precision=_mean([r.context_precision for r in results]),
        context_recall=(float(np.mean(recalls)) if recalls else None),
        faithfulness=_mean([r.faithfulness for r in results]),
        answer_relevancy=_mean([r.answer_relevancy for r in results]),
        answer_correctness=_mean([r.answer_correctness for r in results]),
        n_questions=len(results),
        error_count=error_count,
    )


# ── orchestrator (background task target) ──

async def run_eval(job: dict, topic: str, user_id: str, n: int, k: int, judge_mode: str) -> None:
    """Run the full eval, mutating `job` in place for progress polling. On success
    persists a rag_eval_runs row. One outer guard flips status→failed (no broad
    per-step try/except — individual question failures already degrade gracefully)."""
    from backend.storage.rag_eval_store import save_rag_eval_run

    sem = asyncio.Semaphore(_LLM_CONCURRENCY)
    try:
        job["status"] = "running"
        job["phase"] = "synthesizing"
        job["updated_at"] = time.time()

        gold = await synthesize_golden_set(topic, user_id, n, sem)
        if not gold:
            job["status"] = "failed"
            job["phase"] = "failed"
            job["error"] = "无法从该主题知识库合成评测集（知识库为空或文档过短）"
            job["updated_at"] = time.time()
            return

        job["total"] = len(gold)
        job["done"] = 0
        job["phase"] = "evaluating"
        job["updated_at"] = time.time()

        async def _eval_one(g: GoldItem) -> QuestionResult | None:
            try:
                res = await evaluate_question(g, topic, user_id, k, judge_mode, sem)
            except Exception as e:
                logger.warning("rag_eval question failed: %s", e)
                res = None
            job["done"] = job.get("done", 0) + 1   # no await between read+write → atomic
            job["updated_at"] = time.time()
            return res

        results = await asyncio.gather(*[_eval_one(g) for g in gold])
        ok = [r for r in results if r is not None]
        error_count = len(results) - len(ok)

        job["phase"] = "aggregating"
        job["updated_at"] = time.time()
        summary = _aggregate(ok, error_count)
        detail = {
            "judge_mode": judge_mode,
            "k": k,
            "error_count": error_count,
            "questions": [r.to_dict() for r in ok],
        }

        run_id = await asyncio.to_thread(
            save_rag_eval_run,
            job_id=job["job_id"], user_id=user_id, topic=topic,
            scope=job.get("scope", "topic"), n_questions=len(gold), k=k, judge_mode=judge_mode,
            hit_at_k=summary.hit_at_k, mrr=summary.mrr,
            context_precision=summary.context_precision, context_recall=summary.context_recall,
            faithfulness=summary.faithfulness, answer_relevancy=summary.answer_relevancy,
            answer_correctness=summary.answer_correctness,
            status="completed", error="", detail=detail,
        )

        job["summary"] = summary.to_dict()
        job["detail"] = detail
        job["run_id"] = run_id
        job["status"] = "completed"
        job["phase"] = "completed"
        job["updated_at"] = time.time()
    except Exception as e:
        logger.exception("rag_eval run failed")
        job["status"] = "failed"
        job["phase"] = "failed"
        job["error"] = str(e)[:300]
        job["updated_at"] = time.time()
