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
    safe_retrieve_topic_context_with_scores, get_topic_map, _build_nodes,
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
    return 1.0 if bool(item.get("supported")) else 0.0


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
    trivial_hit: bool
    loo_hit: int            # leave-one-out 泛化命中：剔除源 chunk 后答案是否仍被覆盖
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
            "trivial_hit": self.trivial_hit,
            "loo_hit": self.loo_hit,
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
    hit_at_k_strict: float
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
            "hit_at_k_strict": r(self.hit_at_k_strict),
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

async def _match_gold(gold: GoldItem, retrieved: list) -> tuple[int | None, str, bool]:
    """Locate gold chunk in the ranked retrieval → (1-based rank, method, trivial).

    Identity match is only reliable for .md chunks (non-empty header_path);
    .txt/.py share an empty header within a file, so fall back to content cosine.

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

    if gold.source_file and gold.header_path:
        for i, c in enumerate(retrieved):
            if c.source_file == gold.source_file and c.header_path == gold.header_path:
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
        if gold.source_file and gold.header_path \
           and c.source_file == gold.source_file and c.header_path == gold.header_path:
            return True
        if gold_emb is not None and _cos(gold_emb, emb_map.get(c.content)) >= _TRIVIAL_HIT_COSINE:
            return True
        return False

    survivors = [c for c in retrieved_ext if not _is_source(c)][:k]
    for c in survivors:
        if _cos(ref_emb, emb_map.get(c.content)) >= _LOO_SUPPORT_FLOOR:
            return 1
    return 0


async def _precision_embedding(gold: GoldItem, retrieved: list) -> float:
    if not retrieved:
        return 0.0
    emb_map = await _embed_unique([gold.reference_answer] + [c.content for c in retrieved])
    ref_emb = emb_map.get(gold.reference_answer)
    if ref_emb is None:
        return 0.0
    scores = [_cos(ref_emb, emb_map.get(c.content)) for c in retrieved]
    return _average_precision(scores, PRECISION_REL_FLOOR)


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
    total = 0
    weighted = 0.0
    for s in stmts:
        if not isinstance(s, dict):
            continue
        total += 1
        weighted += _support_weight(s)
    return weighted / total if total else None


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
    total = 0
    weighted = 0.0
    for c in claims:
        if not isinstance(c, dict):
            continue
        total += 1
        weighted += _support_weight(c)
    return weighted / total if total else 0.0


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
    # Clamp negatives to 0 instead of dropping them: a generated question that is
    # unrelated/opposite to the original (cosine <= 0) is a *low* relevancy signal,
    # not an absent one. Dropping it inflated the mean.
    sims = [max(0.0, s) for s in sims]
    if not sims:
        return 0.0
    return max(0.0, min(1.0, float(np.mean(sims))))


async def _answer_correctness(answer: str, reference: str, sem: asyncio.Semaphore) -> float:
    """LLM 判定事实正确性：把生成答案拆成断言，逐条对照参考答案打 full/partial/none
    加权（与 _faithfulness_llm / _recall_llm 同构）。取代旧的纯 cosine(answer, reference)
    ——后者无事实核对、且两段中文余弦天然偏高，落在乐观带。"""
    if not answer or not reference:
        return 0.0
    llm = get_langchain_llm()
    prompt = ANSWER_CORRECTNESS_PROMPT.format(
        answer=answer, reference_answer=reference, json_discipline=JSON_OUTPUT_DISCIPLINE,
    )
    data = await _json_call(llm, prompt, sem)
    if not data:
        return 0.0
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        return 0.0
    total = 0
    weighted = 0.0
    for c in claims:
        if not isinstance(c, dict):
            continue
        total += 1
        weighted += _support_weight(c)
    return weighted / total if total else 0.0


async def evaluate_question(
    gold: GoldItem, topic: str, user_id: str, k: int, judge_mode: str, sem: asyncio.Semaphore,
) -> QuestionResult:
    # Retrieve a few extra so leave-one-out still has k candidates after dropping
    # the gold's own source chunk. Standard metrics use the top-k slice.
    retrieved_ext = await safe_retrieve_topic_context_with_scores(
        topic, gold.question, user_id, top_k=k + _LOO_MARGIN
    )
    retrieved = retrieved_ext[:k]
    rank, match_method, trivial_hit = await _match_gold(gold, retrieved)
    hit = 1 if rank is not None else 0
    loo_hit = await _leave_one_out_hit(gold, retrieved_ext, k)

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
    correctness = await _answer_correctness(a_gen, gold.reference_answer, sem)

    gold_source = gold.source_file + (f" [{gold.header_path}]" if gold.header_path else "")
    return QuestionResult(
        question=gold.question,
        reference_answer=gold.reference_answer,
        generated_answer=a_gen,
        rank=rank,
        hit=hit,
        trivial_hit=trivial_hit,
        loo_hit=loo_hit,
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
        return EvalSummary(0.0, 0.0, 0.0, 0.0, None, 0.0, 0.0, 0.0, 0, error_count)

    recalls = [r.context_recall for r in results if r.context_recall is not None]
    # hit_at_k_strict = leave-one-out 泛化命中率：剔除 gold 自身源 chunk 后，答案是否
    # 仍被其它片段覆盖。比裸 hit@k 更接近真实泛化检索（裸 hit@k 含自命中送分）。
    return EvalSummary(
        hit_at_k=_mean([r.hit for r in results]),
        hit_at_k_strict=_mean([r.loo_hit for r in results]),
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
            hit_at_k_strict=summary.hit_at_k_strict,
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
