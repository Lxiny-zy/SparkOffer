import { Fragment, useState, useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, RadarChart, PolarGrid, PolarAngleAxis,
  PolarRadiusAxis, Radar, BarChart, Bar,
} from "recharts";
import { BarChart3, RefreshCw, ExternalLink, FlaskConical, Loader2, ChevronDown, ChevronUp, AlertTriangle, Database, GitBranch, History } from "lucide-react";
import { getRAGMetrics, getTopics, type RAGMetricsRecord } from "../api/interview";
import {
  startRagEval,
  getRagEvalStatus,
  getRagEvalRun,
  listRagEvalRuns,
  type RagEvalKind,
  type RagRetrievalMode,
  type RagEvalRun,
  type RagEvalStatus,
  type RagEvalSummary,
  type RagEvalQuestionDetail,
  type RagEvalManifest,
} from "../api/ragEval";
import { cn } from "@/lib/utils";
import { fmtPct01, metricColorVar } from "@/lib/metrics";
import { MetricInfoTooltip } from "@/components/MetricInfoTooltip";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/** newer-half mean − older-half mean. records arrive DESC (newest first),
 *  so slice(0, halfIdx) is the recent half. Returns null when <2 records. */
function halfMeanDelta(
  records: RAGMetricsRecord[],
  pick: (r: RAGMetricsRecord) => number | null,
): number | null {
  if (records.length < 2) return null;
  const halfIdx = Math.floor(records.length / 2);
  const newer = records.slice(0, halfIdx);
  const older = records.slice(halfIdx);
  if (!newer.length || !older.length) return null;
  const mean = (rs: RAGMetricsRecord[]) => rs.reduce((s, r) => s + (pick(r) ?? 0), 0) / rs.length;
  return mean(newer) - mean(older);
}

function MetricCard({ label, value, delta, metricKey }: { label: string; value: number | null; delta?: number | null; metricKey?: string }) {
  if (value == null) return (
    <div className="rounded-xl border border-border/40 bg-card/60 px-4 py-3 text-center">
      <div className="text-[11px] text-muted-fg mb-1">{label}</div>
      <div className="text-2xl font-bold font-mono text-muted-fg">--</div>
    </div>
  );
  const p = Math.round(value * 100);
  const color = metricColorVar(value, metricKey);
  const deltaPct = delta != null ? Math.round(delta * 100) : null;
  const card = (
    <div data-spotlight className="spotlight rounded-xl border border-border/40 bg-card/60 px-4 py-3 text-center transition-all duration-300 hover:-translate-y-0.5 hover:border-primary/40">
      <div className="text-[11px] text-muted-fg mb-1">{label}</div>
      <div className="text-2xl font-bold font-mono tabular-nums" style={{ color }}>{p}%</div>
      {deltaPct != null && deltaPct !== 0 && (
        <div className={cn("text-[11px] font-mono", deltaPct > 0 ? "text-green-500" : "text-red-500")}>
          {deltaPct > 0 ? "+" : ""}{deltaPct}%
        </div>
      )}
    </div>
  );
  return metricKey ? <MetricInfoTooltip metricKey={metricKey} label={label}>{card}</MetricInfoTooltip> : card;
}

function RetrievalTrendTooltip({ active, payload }: { active?: boolean; payload?: any[] }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-lg text-xs">
      <div className="font-medium">{d.date}</div>
      <div className="text-dim">{d.topic}</div>
      <div className="mt-1 space-y-0.5">
        {d.relevance != null && <div style={{ color: "var(--primary)" }}>相关度: {fmtPct01(d.relevance)}</div>}
        {d.coverage != null && <div style={{ color: "var(--green)" }}>覆盖度: {fmtPct01(d.coverage)}</div>}
        {d.diversity != null && <div style={{ color: "var(--warning)" }}>多样性: {fmtPct01(d.diversity)}</div>}
      </div>
    </div>
  );
}

function GenTrendTooltip({ active, payload }: { active?: boolean; payload?: any[] }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-lg text-xs">
      <div className="font-medium">{d.date}</div>
      <div className="text-dim">{d.topic}</div>
      <div className="mt-1 space-y-0.5">
        {d.faithfulness != null && <div style={{ color: "var(--sig-chart-2)" }}>依据一致性: {fmtPct01(d.faithfulness)}</div>}
        {d.answer_relevance != null && <div style={{ color: "var(--sig-chart-1)" }}>切题度: {fmtPct01(d.answer_relevance)}</div>}
        {d.correctness != null && <div style={{ color: "var(--primary)" }}>综合依据质量: {fmtPct01(d.correctness)}</div>}
      </div>
    </div>
  );
}

const EVAL_KIND_LABEL: Record<RagEvalKind, string> = {
  frozen_retrieval: "固定回归",
  synthetic_e2e: "合成端到端",
};

const RETRIEVAL_MODE_LABEL: Record<RagRetrievalMode, string> = {
  atomic_dense: "原子向量",
  production_replay: "生产回放",
};

function evalKindLabel(kind?: string): string {
  return kind && kind in EVAL_KIND_LABEL
    ? EVAL_KIND_LABEL[kind as RagEvalKind]
    : kind || "历史结果";
}

function retrievalModeLabel(mode?: string): string {
  return mode && mode in RETRIEVAL_MODE_LABEL
    ? RETRIEVAL_MODE_LABEL[mode as RagRetrievalMode]
    : mode || "未知链路";
}

function manifestDatasetHash(manifest?: RagEvalManifest | null): string {
  if (!manifest) return "";
  return manifest.dataset?.hash || (typeof manifest.dataset_hash === "string" ? manifest.dataset_hash : "");
}

function manifestCorpusHash(manifest?: RagEvalManifest | null): string {
  if (!manifest) return "";
  return manifest.corpus?.hash || (typeof manifest.corpus_hash === "string" ? manifest.corpus_hash : "");
}

function runDatasetHash(run: RagEvalRun): string {
  return run.dataset_hash || manifestDatasetHash(run.manifest) || "";
}

function runCorpusHash(run: RagEvalRun): string {
  return run.corpus_hash || manifestCorpusHash(run.manifest) || "";
}

function runEvalKind(run: Pick<RagEvalRun, "eval_kind" | "manifest">): string {
  return run.eval_kind || run.manifest?.eval_kind || "legacy";
}

function runRetrievalMode(run: Pick<RagEvalRun, "retrieval_mode" | "manifest">): string {
  return run.retrieval_mode || run.manifest?.retrieval_mode || "legacy";
}

function runComparisonSignature(run: Pick<RagEvalRun, "manifest">): string {
  const signature = run.manifest?.comparison_signature;
  return typeof signature === "string" ? signature.trim() : "";
}

function runExecutionProfile(run: Pick<RagEvalRun, "manifest">): string {
  const profile = run.manifest?.comparison_dimensions?.execution_profile;
  return typeof profile === "string" ? profile : "";
}

function strictComparisonEligibility(run: RagEvalRun): { eligible: boolean; reason: string } {
  const signature = runComparisonSignature(run);
  if (!signature) return { eligible: false, reason: "旧记录缺少后端严格比较签名" };
  if (run.status !== "completed") return { eligible: false, reason: "评测未成功完成" };
  if (run.success_rate == null || run.success_rate < 0.95) {
    return { eligible: false, reason: "有效测量率低于 95%" };
  }
  if (run.manifest?.state_stable !== true) {
    return { eligible: false, reason: "运行前后配置或索引状态未确认稳定" };
  }
  if (runExecutionProfile(run) !== "healthy") {
    return { eligible: false, reason: "检索链路存在降级或基础设施失败" };
  }
  return { eligible: true, reason: "" };
}

function shortHash(hash: string): string {
  if (!hash) return "无 hash";
  return hash.length > 12 ? `${hash.slice(0, 8)}…${hash.slice(-4)}` : hash;
}

function evalMetricValue(summary: RagEvalSummary, key: keyof RagEvalSummary): number | null {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const ACTIVE_RAG_EVAL_KEY = "sparkoffer.active-rag-eval";

export default function RAGDashboard() {
  const navigate = useNavigate();
  const [records, setRecords] = useState<RAGMetricsRecord[]>([]);
  const [topics, setTopics] = useState<Record<string, any>>({});
  const [selectedTopic, setSelectedTopic] = useState<string>("");
  const [loading, setLoading] = useState(true);

  // Offline benchmark dimensions. Frozen + production replay is the default
  // regression view; synthetic judging is an explicit, costlier diagnostic.
  const [evalKind, setEvalKind] = useState<RagEvalKind>("frozen_retrieval");
  const [retrievalMode, setRetrievalMode] = useState<RagRetrievalMode>("production_replay");
  const [judgeMode, setJudgeMode] = useState<"standard" | "full">("standard");
  const [nQuestions, setNQuestions] = useState(20);
  const [evalStatus, setEvalStatus] = useState<RagEvalStatus | null>(null);
  const [evalRunning, setEvalRunning] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);
  const [evalRuns, setEvalRuns] = useState<RagEvalRun[]>([]);
  const [evalHistoryLoading, setEvalHistoryLoading] = useState(false);
  const [evalHistoryError, setEvalHistoryError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const pollGenerationRef = useRef(0);

  const loadData = async () => {
    setLoading(true);
    try {
      const [metrics, topicData] = await Promise.all([
        getRAGMetrics({ limit: 200 }),
        getTopics(),
      ]);
      setRecords(metrics);
      setTopics(topicData);
    } catch (e) {
      console.error("Failed to load RAG metrics:", e);
    } finally {
      setLoading(false);
    }
  };

  const loadEvalHistory = async (topic?: string) => {
    setEvalHistoryLoading(true);
    setEvalHistoryError(null);
    try {
      setEvalRuns(await listRagEvalRuns(topic, 30));
    } catch (e: any) {
      setEvalHistoryError(e?.message || "加载离线评测历史失败");
    } finally {
      setEvalHistoryLoading(false);
    }
  };

  const refreshAll = async () => {
    await Promise.all([loadData(), loadEvalHistory(selectedTopic || undefined)]);
  };

  const stopEvalPolling = () => {
    pollGenerationRef.current += 1;
    if (pollRef.current) window.clearTimeout(pollRef.current);
    pollRef.current = null;
  };

  const pollEvalJob = (jobId: string, topic: string) => {
    stopEvalPolling();
    const generation = pollGenerationRef.current;
    setEvalRunning(true);

    const poll = async () => {
      if (generation !== pollGenerationRef.current) return;
      try {
        const status = await getRagEvalStatus(jobId);
        if (generation !== pollGenerationRef.current) return;
        setEvalStatus(status);
        if (status.status === "completed" || status.status === "failed") {
          stopEvalPolling();
          localStorage.removeItem(ACTIVE_RAG_EVAL_KEY);
          setEvalRunning(false);
          if (status.status === "failed") {
            setEvalError(status.error || "评测失败");
          } else {
            void loadEvalHistory(topic || undefined);
          }
          return;
        }
        pollRef.current = window.setTimeout(() => { void poll(); }, 1500);
      } catch (error: any) {
        if (generation !== pollGenerationRef.current) return;
        stopEvalPolling();
        localStorage.removeItem(ACTIVE_RAG_EVAL_KEY);
        setEvalRunning(false);
        setEvalError(error?.message || "查询进度失败");
      }
    };
    void poll();
  };

  useEffect(() => { void loadData(); }, []);
  useEffect(() => { void loadEvalHistory(selectedTopic || undefined); }, [selectedTopic]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(ACTIVE_RAG_EVAL_KEY);
      if (!raw) return;
      const active = JSON.parse(raw) as { job_id?: string; topic?: string };
      if (!active.job_id || !active.topic) {
        localStorage.removeItem(ACTIVE_RAG_EVAL_KEY);
        return;
      }
      setSelectedTopic(active.topic);
      pollEvalJob(active.job_id, active.topic);
    } catch {
      localStorage.removeItem(ACTIVE_RAG_EVAL_KEY);
    }
    return () => stopEvalPolling();
    // Restore exactly once; polling reads all subsequent state from the API.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Stop polling on unmount.
  useEffect(() => () => stopEvalPolling(), []);

  const runEval = async () => {
    if (!selectedTopic) {
      setEvalError("请先在上方选择一个具体 Topic（非\"全部\"）再运行评测。");
      return;
    }
    stopEvalPolling();
    setEvalError(null);
    setEvalStatus(null);
    setEvalRunning(true);
    try {
      const { job_id } = await startRagEval({
        topic: selectedTopic,
        n_questions: nQuestions,
        eval_kind: evalKind,
        retrieval_mode: retrievalMode,
        seed: 42,
        ...(evalKind === "synthetic_e2e" ? { judge_mode: judgeMode } : {}),
      });
      localStorage.setItem(ACTIVE_RAG_EVAL_KEY, JSON.stringify({
        job_id,
        topic: selectedTopic,
      }));
      pollEvalJob(job_id, selectedTopic);
    } catch (e: any) {
      setEvalRunning(false);
      setEvalError(e?.message || "启动评测失败");
    }
  };

  const filtered = useMemo(() => {
    if (!selectedTopic) return records;
    return records.filter((r) => r.topic === selectedTopic);
  }, [records, selectedTopic]);

  const retrievalRecords = useMemo(() =>
    filtered.filter((r) => r.stage === "question_gen" && r.relevance != null),
    [filtered],
  );

  const evalRecords = useMemo(() =>
    filtered.filter((r) => r.stage === "answer_eval" && r.faithfulness != null),
    [filtered],
  );

  // Summary cards — headline value is the overall average.
  const avgRelevance = retrievalRecords.length
    ? retrievalRecords.reduce((s, r) => s + (r.relevance ?? 0), 0) / retrievalRecords.length : null;
  const avgFaithfulness = evalRecords.length
    ? evalRecords.reduce((s, r) => s + (r.faithfulness ?? 0), 0) / evalRecords.length : null;

  // Trend arrow: newer half mean − older half mean (records are DESC).
  const relevanceDelta = halfMeanDelta(retrievalRecords, (r) => r.relevance);
  const faithfulnessDelta = halfMeanDelta(evalRecords, (r) => r.faithfulness);

  // Retrieval trend data (chronological)
  const retrievalTrend = useMemo(() =>
    [...retrievalRecords].reverse().map((r, i) => ({
      index: i,
      date: r.created_at?.slice(0, 10) || "",
      topic: r.topic,
      relevance: r.relevance,
      coverage: r.coverage,
      diversity: r.diversity,
    })),
    [retrievalRecords],
  );

  // User-answer grounding trend. These values come from answer_eval and score
  // the user's submitted answer against retrieved references, not model output.
  const genTrend = useMemo(() =>
    [...evalRecords].reverse().map((r, i) => ({
      index: i,
      date: r.created_at?.slice(0, 10) || "",
      topic: r.topic,
      faithfulness: r.faithfulness,
      answer_relevance: r.answer_relevance,
      correctness: r.answer_correctness,
    })),
    [evalRecords],
  );

  // Radar per-topic
  const radarData = useMemo(() => {
    const byTopic: Record<string, number[]> = {};
    for (const r of retrievalRecords) {
      if (!byTopic[r.topic]) byTopic[r.topic] = [];
      if (r.relevance != null) byTopic[r.topic].push(r.relevance);
    }
    return Object.entries(byTopic).map(([topic, vals]) => ({
      topic: topics[topic]?.name || topic,
      relevance: Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 100),
    }));
  }, [retrievalRecords, topics]);

  // Quality distribution
  const qualityDist = useMemo(() => {
    const byTopic: Record<string, { excellent: number; good: number; fair: number; poor: number }> = {};
    for (const r of retrievalRecords) {
      if (!byTopic[r.topic]) byTopic[r.topic] = { excellent: 0, good: 0, fair: 0, poor: 0 };
      const v = r.relevance ?? 0;
      if (v >= 0.7) byTopic[r.topic].excellent++;
      else if (v >= 0.5) byTopic[r.topic].good++;
      else if (v >= 0.3) byTopic[r.topic].fair++;
      else byTopic[r.topic].poor++;
    }
    return Object.entries(byTopic).map(([topic, d]) => ({
      topic: topics[topic]?.name || topic,
      ...d,
    }));
  }, [retrievalRecords, topics]);

  // Recent sessions table
  const recentSessions = useMemo(() => {
    const map: Record<string, { retrieval?: RAGMetricsRecord; eval?: RAGMetricsRecord }> = {};
    for (const r of filtered) {
      if (!map[r.session_id]) map[r.session_id] = {};
      // Records arrive newest-first. Fill each slot once so an older duplicate
      // stage cannot overwrite the latest session reading.
      if (r.stage === "question_gen" && !map[r.session_id].retrieval) map[r.session_id].retrieval = r;
      else if (r.stage === "answer_eval" && !map[r.session_id].eval) map[r.session_id].eval = r;
    }
    return Object.entries(map)
      .map(([sid, data]) => ({ session_id: sid, ...data }))
      .slice(0, 20);
  }, [filtered]);

  const topicKeys = Object.keys(topics);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center py-20 text-muted-fg">
        <div className="flex flex-col items-center gap-3">
          <div className="flex gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse [animation-delay:0.2s]" />
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse [animation-delay:0.4s]" />
          </div>
          <span className="text-sm">Loading RAG metrics...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-5xl mx-auto w-full space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <BarChart3 size={22} className="text-primary" />
          <div>
            <div className="sig-kicker mb-1">// RAG 质量 / RAG QUALITY</div>
            <h1 className="sig-display text-2xl">RAG 仪表盘<span className="sig-accent-c">.</span></h1>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={refreshAll} className="gap-1.5">
          <RefreshCw size={14} /> 刷新
        </Button>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-2 flex-wrap">
        <Badge
          variant={selectedTopic === "" ? "default" : "outline"}
          className="cursor-pointer hover:-translate-y-px hover:brightness-110"
          onClick={() => setSelectedTopic("")}
        >
          全部
        </Badge>
        {topicKeys.map((k) => (
          <Badge
            key={k}
            variant={selectedTopic === k ? "default" : "outline"}
            className="cursor-pointer hover:-translate-y-px hover:brightness-110"
            onClick={() => setSelectedTopic(k)}
          >
            {topics[k]?.name || k}
          </Badge>
        ))}
      </div>

      {/* Offline benchmark — deliberately separate from live session gauges. */}
      <Card>
        <CardContent className="p-4 space-y-4">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <div className="flex items-center gap-2">
                <FlaskConical size={16} className="text-primary" />
                <span className="text-sm font-medium">离线 RAG 基准评测</span>
                <Badge variant="outline" className="text-[10px]">独立于在线监控</Badge>
              </div>
              <p className="text-[11px] text-muted-fg mt-1 max-w-xl">
                固定回归用于可复现的检索前后对比；合成端到端用于探索回答质量。评测类型、检索链路或数据集 hash
                不一致时，结果只可并列查看，不能直接计算升降。
              </p>
            </div>
          </div>

          <div className="flex items-end gap-3 flex-wrap">
            <div className="space-y-1">
              <div className="text-[10px] text-muted-fg">评测集</div>
              <div className="inline-flex h-8 rounded-md border border-border bg-muted/40 p-0.5" role="tablist" aria-label="评测集类型">
                {(["frozen_retrieval", "synthetic_e2e"] as const).map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    role="tab"
                    aria-selected={evalKind === kind}
                    disabled={evalRunning}
                    onClick={() => setEvalKind(kind)}
                    className={cn(
                      "h-7 px-2.5 text-xs rounded-sm transition-colors disabled:opacity-50",
                      evalKind === kind ? "bg-card text-foreground shadow-sm" : "text-muted-fg hover:text-foreground",
                    )}
                  >
                    {kind === "frozen_retrieval" ? "固定回归" : "合成端到端"}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1">
              <div className="text-[10px] text-muted-fg">检索链路</div>
              <div className="inline-flex h-8 rounded-md border border-border bg-muted/40 p-0.5" role="tablist" aria-label="检索链路">
                {(["atomic_dense", "production_replay"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    role="tab"
                    aria-selected={retrievalMode === mode}
                    disabled={evalRunning}
                    onClick={() => setRetrievalMode(mode)}
                    title={mode === "atomic_dense" ? "只评测基础向量召回" : "复放多查询、RRF、去重与重排链路"}
                    className={cn(
                      "h-7 px-2.5 text-xs rounded-sm transition-colors disabled:opacity-50",
                      retrievalMode === mode ? "bg-card text-foreground shadow-sm" : "text-muted-fg hover:text-foreground",
                    )}
                  >
                    {mode === "atomic_dense" ? "原子向量" : "生产回放"}
                  </button>
                ))}
              </div>
            </div>

            {evalKind === "synthetic_e2e" && (
              <div className="space-y-1">
                <div className="text-[10px] text-muted-fg">LLM 评判</div>
                <div className="inline-flex h-8 rounded-md border border-border bg-muted/40 p-0.5" role="tablist" aria-label="LLM 评判强度">
                  {(["standard", "full"] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      role="tab"
                      aria-selected={judgeMode === mode}
                      disabled={evalRunning}
                      onClick={() => setJudgeMode(mode)}
                      title={mode === "standard"
                        ? "标准：检索指标嵌入锚定，生成侧 LLM 评判"
                        : "完整：Precision 也逐 chunk 由 LLM 判定，耗时与成本更高"}
                      className={cn(
                        "h-7 px-2.5 text-xs rounded-sm transition-colors disabled:opacity-50",
                        judgeMode === mode ? "bg-card text-foreground shadow-sm" : "text-muted-fg hover:text-foreground",
                      )}
                    >
                      {mode === "standard" ? "标准" : "完整"}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="space-y-1">
              <div className="text-[10px] text-muted-fg">样本数</div>
              <select
                value={nQuestions}
                disabled={evalRunning}
                onChange={(e) => setNQuestions(Number(e.target.value))}
                className="h-8 rounded-md border border-border bg-card px-2 text-xs text-foreground disabled:opacity-50"
                title="评测题量"
              >
                {[5, 10, 20, 30].map((n) => <option key={n} value={n}>{n} 题</option>)}
              </select>
            </div>

            <Button
              size="sm"
              onClick={runEval}
              disabled={evalRunning || !selectedTopic}
              className="gap-1.5"
              title={!selectedTopic ? "请先选择一个具体 Topic" : "运行 RAG 评测"}
            >
              {evalRunning ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
              运行评测
            </Button>
          </div>

          <div className="flex items-start gap-2 text-[11px] text-muted-fg rounded-md border border-border/40 bg-muted/20 px-3 py-2">
            {evalKind === "frozen_retrieval" ? <Database size={13} className="shrink-0 mt-0.5" /> : <FlaskConical size={13} className="shrink-0 mt-0.5" />}
            <span>
              {evalKind === "frozen_retrieval"
                ? "固定版本查询集，不调用 LLM 裁判；适合回归门禁。"
                : "从当前知识库按 seed=42 合成 golden 集并调用 LLM 裁判；结果有模型随机性。"}
              {retrievalMode === "production_replay"
                ? " 当前复放多查询 → RRF → 语义去重 → 重排链路。"
                : " 当前只检查单查询基础向量召回。"}
            </span>
          </div>

          {!selectedTopic && (
            <p className="text-[11px]" style={{ color: "var(--warning)" }}>
              请先在上方选择一个具体 Topic（非"全部"）再运行评测。
            </p>
          )}

          {evalRunning && evalStatus && <EvalProgress status={evalStatus} />}
          {evalRunning && !evalStatus && (
            <p className="text-xs text-muted-fg flex items-center gap-1.5">
              <Loader2 size={12} className="animate-spin" /> 正在启动评测…
            </p>
          )}

          {evalError && (
            <div className="flex items-start gap-2 text-xs rounded-md px-3 py-2" style={{ color: "var(--sig-danger)", background: "color-mix(in srgb, var(--sig-danger) 10%, transparent)", border: "1px solid color-mix(in srgb, var(--sig-danger) 20%, transparent)" }}>
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{evalError}</span>
            </div>
          )}

          {evalStatus?.status === "completed" && evalStatus.summary && (
            <RagEvalResultCard status={evalStatus} topicName={topics[evalStatus.topic]?.name || evalStatus.topic} />
          )}
        </CardContent>
      </Card>

      <RagEvalHistory
        runs={evalRuns}
        loading={evalHistoryLoading}
        error={evalHistoryError}
        topics={topics}
      />

      <div className="flex items-start gap-3 rounded-md border border-border/40 bg-muted/15 px-4 py-3">
        <GitBranch size={15} className="text-primary shrink-0 mt-0.5" />
        <div>
          <div className="text-xs font-medium">在线会话健康监控</div>
          <p className="text-[11px] text-muted-fg mt-0.5">
            以下数据来自真实专项训练 Session：检索侧是无 ground truth 的 embedding 健康信号；作答侧评估的是用户回答是否切题、是否有检索参考依据。
            它们不是上方的固定离线回归结果。
          </p>
        </div>
      </div>

      {records.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center text-muted-fg">
            <p className="text-lg mb-2">暂无在线会话指标</p>
            <p className="text-sm">完成一次专项训练后，实时检索健康度与用户作答依据质量将在这里展示。</p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MetricCard label="在线平均检索相关度" value={avgRelevance} delta={relevanceDelta} />
            <MetricCard label="平均作答依据一致性" value={avgFaithfulness} delta={faithfulnessDelta} />
            <div className="rounded-xl border border-border/40 bg-card/60 px-4 py-3 text-center">
              <div className="text-[11px] text-muted-fg mb-1">检索 Sessions</div>
              <div className="text-2xl font-bold font-mono tabular-nums text-foreground">{retrievalRecords.length}</div>
            </div>
            <div className="rounded-xl border border-border/40 bg-card/60 px-4 py-3 text-center">
              <div className="text-[11px] text-muted-fg mb-1">作答评估 Sessions</div>
              <div className="text-2xl font-bold font-mono tabular-nums text-foreground">{evalRecords.length}</div>
            </div>
          </div>

          {/* Row 1: Retrieval trend + Radar */}
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
            <Card className="lg:col-span-3">
              <CardContent className="p-4">
                <div className="text-xs font-medium text-muted-fg mb-3">在线检索健康趋势</div>
                {retrievalTrend.length >= 2 ? (
                  <ResponsiveContainer width="100%" height={240}>
                    <LineChart data={retrievalTrend} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <Tooltip content={<RetrievalTrendTooltip />} />
                      <Line type="monotone" dataKey="relevance" stroke="var(--primary)" strokeWidth={2} dot={false} name="相关度" />
                      <Line type="monotone" dataKey="coverage" stroke="var(--green)" strokeWidth={1.5} dot={false} name="覆盖度" />
                      <Line type="monotone" dataKey="diversity" stroke="var(--warning)" strokeWidth={1.5} dot={false} name="多样性" />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-[240px] flex items-center justify-center text-sm text-muted-fg">
                    需要至少 2 条数据才能显示趋势
                  </div>
                )}
              </CardContent>
            </Card>

            <Card className="lg:col-span-2">
              <CardContent className="p-4">
                <div className="text-xs font-medium text-muted-fg mb-3">Topic 相关度</div>
                {radarData.length >= 3 ? (
                  <ResponsiveContainer width="100%" height={240}>
                    <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="70%">
                      <PolarGrid stroke="var(--border)" />
                      <PolarAngleAxis dataKey="topic" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 9, fill: "var(--muted-foreground)" }} />
                      <Radar dataKey="relevance" stroke="var(--primary)" fill="var(--primary)" fillOpacity={0.2} strokeWidth={2} />
                    </RadarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-[240px] flex items-center justify-center text-sm text-muted-fg">
                    需要至少 3 个 topic 数据
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Row 2: Quality distribution + Generation trend */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardContent className="p-4">
                <div className="text-xs font-medium text-muted-fg mb-3">在线检索健康分布</div>
                {qualityDist.length > 0 ? (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={qualityDist} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="topic" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <YAxis tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <Tooltip />
                      <Bar dataKey="excellent" stackId="a" fill="var(--green)" name="优秀 (>=70%)" radius={[0, 0, 0, 0]} />
                      <Bar dataKey="good" stackId="a" fill="var(--sig-chart-2)" name="良好 (50-70%)" />
                      <Bar dataKey="fair" stackId="a" fill="var(--warning)" name="一般 (30-50%)" />
                      <Bar dataKey="poor" stackId="a" fill="var(--red)" name="差 (<30%)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-[220px] flex items-center justify-center text-sm text-muted-fg">暂无数据</div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4">
                <div className="text-xs font-medium text-muted-fg mb-1">用户作答引用 / 依据质量趋势</div>
                <div className="text-[10px] text-muted-fg mb-3">衡量用户答案是否切题、是否可由检索参考支撑；不是模型生成质量。</div>
                {genTrend.length >= 2 ? (
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={genTrend} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <Tooltip content={<GenTrendTooltip />} />
                      <Line type="monotone" dataKey="faithfulness" stroke="var(--sig-chart-2)" strokeWidth={2} dot={false} name="依据一致性" />
                      <Line type="monotone" dataKey="answer_relevance" stroke="var(--sig-chart-1)" strokeWidth={1.5} dot={false} name="回答切题度" />
                      <Line type="monotone" dataKey="correctness" stroke="var(--primary)" strokeWidth={1.5} dot={false} name="综合依据质量" />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-[220px] flex items-center justify-center text-sm text-muted-fg">
                    需要至少 2 条评估数据
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Row 3: Recent online sessions table */}
          <Card>
            <CardContent className="p-4">
              <div className="text-xs font-medium text-muted-fg mb-3">近期在线 Session 明细</div>
              {recentSessions.length === 0 ? (
                <div className="py-8 text-center text-sm text-muted-fg">暂无数据</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="border-b border-border/50 text-muted-fg">
                        <th className="text-left py-2 px-2 font-medium">Session</th>
                        <th className="text-left py-2 px-2 font-medium">Topic</th>
                        <th className="text-center py-2 px-2 font-medium">相关度</th>
                        <th className="text-center py-2 px-2 font-medium">覆盖</th>
                        <th className="text-center py-2 px-2 font-medium">多样</th>
                        <th className="text-center py-2 px-2 font-medium">依据一致</th>
                        <th className="text-center py-2 px-2 font-medium">回答切题</th>
                        <th className="text-center py-2 px-2 font-medium">日期</th>
                        <th className="py-2 px-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentSessions.map(({ session_id, retrieval, eval: evalR }) => (
                        <tr key={session_id} className="border-b border-border/30 hover:bg-muted/30 transition-colors">
                          <td className="py-2 px-2 font-mono text-[11px] text-muted-fg">{session_id.slice(0, 8)}</td>
                          <td className="py-2 px-2">{topics[retrieval?.topic || evalR?.topic || ""]?.name || retrieval?.topic || evalR?.topic || "--"}</td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={retrieval?.relevance} metricKey="relevance" />
                          </td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={retrieval?.coverage} metricKey="coverage" />
                          </td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={retrieval?.diversity} metricKey="diversity" />
                          </td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={evalR?.faithfulness} metricKey="faithfulness" />
                          </td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={evalR?.answer_relevance} metricKey="answer_relevance" />
                          </td>
                          <td className="py-2 px-2 text-center text-muted-fg">
                            {(retrieval?.created_at || evalR?.created_at || "").slice(0, 10)}
                          </td>
                          <td className="py-2 px-2">
                            <button
                              onClick={() => navigate(`/review/${session_id}`)}
                              className="text-primary hover:text-primary/80 transition-colors"
                              title="查看复盘"
                            >
                              <ExternalLink size={13} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function RagEvalHistory({
  runs,
  loading,
  error,
  topics,
}: {
  runs: RagEvalRun[];
  loading: boolean;
  error: string | null;
  topics: Record<string, any>;
}) {
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);
  const [expandedRun, setExpandedRun] = useState<RagEvalRun | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const detailGenerationRef = useRef(0);

  const toggleRunDetail = async (runId: number) => {
    const generation = ++detailGenerationRef.current;
    if (expandedRunId === runId) {
      setExpandedRunId(null);
      setExpandedRun(null);
      setDetailError(null);
      return;
    }
    setExpandedRunId(runId);
    setExpandedRun(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const detail = await getRagEvalRun(runId);
      if (generation === detailGenerationRef.current) setExpandedRun(detail);
    } catch (detailLoadError: any) {
      if (generation === detailGenerationRef.current) {
        setDetailError(detailLoadError?.message || "加载评测详情失败");
      }
    } finally {
      if (generation === detailGenerationRef.current) setDetailLoading(false);
    }
  };

  const groupCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const run of runs) {
      const signature = runComparisonSignature(run);
      if (signature && strictComparisonEligibility(run).eligible) {
        counts.set(signature, (counts.get(signature) || 0) + 1);
      }
    }
    return counts;
  }, [runs]);

  const groupIds = useMemo(() => {
    const ids = new Map<string, number>();
    for (const run of runs) {
      const signature = runComparisonSignature(run);
      if (signature && (groupCounts.get(signature) || 0) > 1 && !ids.has(signature)) {
        ids.set(signature, ids.size + 1);
      }
    }
    return ids;
  }, [runs, groupCounts]);

  const comparableGroupCount = useMemo(
    () => [...groupCounts.values()].filter((count) => count > 1).length,
    [groupCounts],
  );

  const formatRunMetric = (value: number | null | undefined, unit: "ratio" | "ms" = "ratio") => {
    if (value == null || !Number.isFinite(value)) return "--";
    return unit === "ms" ? `${Math.round(value)} ms` : `${Math.round(value * 100)}%`;
  };

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <History size={15} className="text-primary" />
            <span className="text-sm font-medium">离线评测历史</span>
          </div>
          {runs.length > 0 && (
            <span className="text-[11px] text-muted-fg">最近 {runs.length} 次</span>
          )}
        </div>

        <div className="rounded-md border border-border/40 bg-muted/20 px-3 py-2 text-[11px] text-muted-fg">
          严格签名由后端综合 Topic、评测类型、检索链路、数据集与 case、语料和索引 revision、K / 裁判模式、检索配置、模型/provider、向量后端、切片参数、prompt、评测协议、依赖版本及指标语义生成。当前有 {comparableGroupCount} 组可比基线；旧记录无签名、运行期状态变化或降级结果均不进入严格比较组。
        </div>

        {error && <div className="text-[11px]" style={{ color: "var(--sig-danger)" }}>{error}</div>}
        {loading ? (
          <div className="py-6 text-center text-sm text-muted-fg flex items-center justify-center gap-2">
            <Loader2 size={14} className="animate-spin" /> 加载离线评测历史…
          </div>
        ) : runs.length === 0 ? (
          <div className="py-6 text-center text-sm text-muted-fg">暂无离线评测记录</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] min-w-[760px]">
              <thead>
                <tr className="border-b border-border/50 text-muted-fg">
                  <th className="text-left py-2 px-2 font-medium">时间 / Topic</th>
                  <th className="text-left py-2 px-2 font-medium">评测签名</th>
                  <th className="text-center py-2 px-2 font-medium">Hit@K</th>
                  <th className="text-center py-2 px-2 font-medium">nDCG</th>
                  <th className="text-center py-2 px-2 font-medium">有效测量率</th>
                  <th className="text-center py-2 px-2 font-medium">P95</th>
                  <th className="text-center py-2 px-2 font-medium">可比性</th>
                  <th className="py-2 px-2"><span className="sr-only">详情</span></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const signature = runComparisonSignature(run);
                  const groupId = groupIds.get(signature) || 0;
                  const datasetHash = runDatasetHash(run);
                  const corpusHash = runCorpusHash(run);
                  const eligibility = strictComparisonEligibility(run);
                  const sameGroupRuns = signature ? (groupCounts.get(signature) || 0) : 0;
                  const comparable = eligibility.eligible && sameGroupRuns > 1;
                  const comparisonReason = comparable
                    ? `严格签名组 ${groupId}，共 ${sameGroupRuns} 次健康运行`
                    : eligibility.reason || "没有另一条相同严格签名的健康基线";
                  const comparisonLabel = comparable
                    ? `严格签名组 ${groupId}`
                    : signature ? "不可严格比较" : "legacy / 不可严格比较";
                  const kind = runEvalKind(run);
                  const mode = runRetrievalMode(run);
                  const isExpanded = expandedRunId === run.id;
                  const detailQuestions = expandedRun?.detail?.questions || [];
                  return (
                    <Fragment key={run.id}>
                    <tr className="border-b border-border/30 hover:bg-muted/30 transition-colors">
                      <td className="py-2 px-2">
                        <div className="font-medium">{(run.created_at || "").slice(0, 16).replace("T", " ") || "--"}</div>
                        <div className="text-[10px] text-muted-fg flex items-center gap-1.5 flex-wrap">
                          <span>{topics[run.topic]?.name || run.topic}</span>
                          <Badge variant="outline" className="text-[9px]">{run.status}</Badge>
                        </div>
                      </td>
                      <td className="py-2 px-2">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <Badge variant="outline" className="text-[10px]">{evalKindLabel(kind)}</Badge>
                          <Badge variant="outline" className="text-[10px]">{retrievalModeLabel(mode)}</Badge>
                          <Badge variant="outline" className="text-[10px]">K={run.k}{kind === "synthetic_e2e" ? ` · ${run.judge_mode}` : ""}</Badge>
                        </div>
                        <div className="text-[10px] text-muted-fg mt-1" title={`${datasetHash || "无 dataset hash"} / ${corpusHash || "无 corpus hash"}`}>
                          数据集 {shortHash(datasetHash)} · 语料 {shortHash(corpusHash)}
                        </div>
                      </td>
                      <td className="py-2 px-2 text-center"><MetricPill value={run.hit_at_k} metricKey="hit_at_k" /></td>
                      <td className="py-2 px-2 text-center"><MetricPill value={run.ndcg_at_k} metricKey="ndcg_at_k" /></td>
                      <td className="py-2 px-2 text-center"><MetricPill value={run.success_rate} metricKey="success_rate" /></td>
                      <td className="py-2 px-2 text-center font-mono tabular-nums">{formatRunMetric(run.latency_p95_ms, "ms")}</td>
                      <td className="py-2 px-2 text-center">
                        <span className={cn("text-[10px]", comparable ? "text-green-500" : "text-muted-fg")} title={comparisonReason}>
                          {comparisonLabel}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => { void toggleRunDetail(run.id); }}
                          title={isExpanded ? "收起评测详情" : "查看评测详情"}
                        >
                          {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                        </Button>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-border/40 bg-muted/20">
                        <td colSpan={8} className="px-3 py-3">
                          {detailLoading ? (
                            <div className="flex items-center gap-2 text-muted-fg"><Loader2 size={13} className="animate-spin" />加载评测详情…</div>
                          ) : detailError ? (
                            <div style={{ color: "var(--sig-danger)" }}>{detailError}</div>
                          ) : expandedRun ? (
                            <div className="space-y-2">
                              <div className="flex items-center gap-3 flex-wrap text-muted-fg">
                                <span>execution: {runExecutionProfile(expandedRun) || "legacy"}</span>
                                <span>signature: {shortHash(runComparisonSignature(expandedRun))}</span>
                                <span>逐题: {detailQuestions.length}</span>
                              </div>
                              {expandedRun.error && (
                                <div className="rounded-sm border px-2 py-1.5" style={{ color: "var(--sig-danger)", borderColor: "color-mix(in srgb, var(--sig-danger) 30%, transparent)" }}>
                                  {expandedRun.error}
                                </div>
                              )}
                              {detailQuestions.length > 0 && (
                                <div className="max-h-64 overflow-auto border-t border-border/40">
                                  {detailQuestions.map((question, index) => {
                                    const outcome = question.outcome || question.retrieval_status || "unknown";
                                    const latency = question.latency_ms ?? question.retrieval_latency_ms;
                                    return (
                                      <div key={question.id || `${run.id}-${index}`} className="grid grid-cols-[minmax(0,1fr)_90px_70px] gap-2 border-b border-border/30 py-1.5">
                                        <span className="truncate" title={question.question}>{question.question}</span>
                                        <span className="text-muted-fg">{outcome}</span>
                                        <span className="text-right font-mono">{latency == null ? "--" : `${Math.round(latency)} ms`}</span>
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function MetricPill({ value, metricKey }: { value: number | null | undefined; metricKey?: string }) {
  if (value == null) return <span className="text-muted-fg">--</span>;
  const p = Math.round(value * 100);
  const pill = (
    <span className="font-mono tabular-nums font-medium" style={{ color: metricColorVar(value, metricKey) }}>
      {p}%
    </span>
  );
  return metricKey ? <MetricInfoTooltip metricKey={metricKey}>{pill}</MetricInfoTooltip> : pill;
}

const EVAL_PHASE_LABEL: Record<string, string> = {
  pending: "排队中",
  loading_dataset: "加载固定评测集",
  preflight: "检查索引与配置",
  retrieving: "执行检索回放",
  synthesizing: "合成评测集",
  evaluating: "评测中",
  aggregating: "汇总结果",
  completed: "完成",
  failed: "失败",
};

function EvalProgress({ status }: { status: RagEvalStatus }) {
  const { phase, done, total } = status;
  const pct = total > 0 ? Math.round((done / total) * 100) : ["loading_dataset", "preflight", "synthesizing"].includes(phase) ? 5 : 0;
  const showCount = total > 0 && ["retrieving", "evaluating"].includes(phase);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[11px] text-muted-fg">
        <span className="flex items-center gap-1.5">
          <Loader2 size={12} className="animate-spin" />
          {EVAL_PHASE_LABEL[phase] || phase}
          {showCount ? ` · ${done}/${total}` : ""}
        </span>
        <span className="font-mono tabular-nums">{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

interface EvalMetricDef {
  key: keyof RagEvalSummary;
  label: string;
  metricKey?: string;
  operational?: boolean;
}

const FROZEN_RESULT_METRICS: EvalMetricDef[] = [
  { key: "hit_at_k", label: "Hit@K", metricKey: "hit_at_k" },
  { key: "mrr", label: "MRR", metricKey: "mrr" },
  { key: "ndcg_at_k", label: "nDCG@K", metricKey: "ndcg_at_k" },
  { key: "context_precision", label: "Keyword Precision", metricKey: "context_precision" },
  { key: "context_recall", label: "Keyword Recall", metricKey: "context_recall" },
  { key: "success_rate", label: "有效测量率", metricKey: "success_rate", operational: true },
  { key: "fully_healthy_rate", label: "完整链路率", metricKey: "success_rate", operational: true },
];

const SYNTHETIC_RESULT_METRICS: EvalMetricDef[] = [
  { key: "hit_at_k_strict", label: "泛化命中(留一)", metricKey: "hit_at_k_strict" },
  { key: "mrr", label: "MRR", metricKey: "mrr" },
  { key: "context_precision", label: "Precision", metricKey: "context_precision" },
  { key: "context_recall", label: "Recall", metricKey: "context_recall" },
  { key: "faithfulness", label: "Faithfulness", metricKey: "faithfulness" },
  { key: "answer_relevancy", label: "Relevancy", metricKey: "answer_relevancy" },
  { key: "answer_correctness", label: "Correctness", metricKey: "answer_correctness" },
  { key: "success_rate", label: "有效测量率", metricKey: "success_rate", operational: true },
  { key: "fully_healthy_rate", label: "完整检索率", metricKey: "success_rate", operational: true },
  { key: "generation_success_rate", label: "答案生成成功率", metricKey: "success_rate", operational: true },
  { key: "judge_observed_rate", label: "Judge 观测率", metricKey: "success_rate", operational: true },
  { key: "metric_observation_rate", label: "指标观测率", metricKey: "success_rate", operational: true },
];

function RagEvalResultCard({ status, topicName }: { status: RagEvalStatus; topicName: string }) {
  const [showDetail, setShowDetail] = useState(false);
  const s = status.summary!;
  const questions: RagEvalQuestionDetail[] = status.detail?.questions ?? [];
  const evalKind = status.eval_kind || status.detail?.eval_kind || "synthetic_e2e";
  const retrievalMode = status.retrieval_mode || status.detail?.retrieval_mode || "atomic_dense";
  const isSynthetic = evalKind === "synthetic_e2e";
  const metrics = isSynthetic ? SYNTHETIC_RESULT_METRICS : FROZEN_RESULT_METRICS;
  const manifest = status.manifest || status.detail?.manifest;
  const datasetHash = status.detail?.dataset_hash || manifestDatasetHash(manifest);
  const corpusHash = status.detail?.corpus_hash || manifestCorpusHash(manifest);
  const seed = status.seed ?? status.detail?.seed ?? manifest?.seed;
  // Older runs predate hit_at_k_strict — fall back to hit_at_k so they still render.
  const metricValue = (key: keyof RagEvalSummary): number | null => {
    if (key === "hit_at_k_strict" && s.hit_at_k_strict == null) return s.hit_at_k ?? null;
    return evalMetricValue(s, key);
  };
  const radarData = metrics
    .filter(({ operational }) => !operational)
    .map(({ key, label }) => ({ metric: label, raw: metricValue(key) }))
    .filter((item) => item.raw != null)
    .map((item) => ({ metric: item.metric, value: Math.round((item.raw as number) * 100) }));
  const evaluated = s.evaluated_questions ?? (
    s.error_count != null ? Math.max(0, s.n_questions - s.error_count) : null
  );
  return (
    <div className="rounded-md border border-border/50 bg-muted/20 p-4 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">本次评测结果 · {topicName}</span>
          <Badge variant="outline" className="text-[10px]">{evalKindLabel(evalKind)}</Badge>
          <Badge variant="outline" className="text-[10px]">{retrievalModeLabel(retrievalMode)}</Badge>
        </div>
        <span className="text-[11px] text-muted-fg">
          {evaluated == null ? "--" : evaluated}/{s.n_questions} 有效
          {s.error_count != null && s.error_count > 0 ? ` · ${s.error_count} 题失败` : ""}
          {isSynthetic ? ` · ${status.judge_mode === "full" ? "完整评判" : "标准评判"}` : ""}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {metrics.map(({ key, label, metricKey }) => (
          <MetricCard key={key} label={label} value={metricValue(key)} metricKey={metricKey} />
        ))}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
        <div className="rounded-md border border-border/40 px-3 py-2">
          <div className="text-muted-fg">P50 检索延迟</div>
          <div className="font-mono tabular-nums mt-0.5">{s.latency_p50_ms == null ? "--" : `${Math.round(s.latency_p50_ms)} ms`}</div>
        </div>
        <div className="rounded-md border border-border/40 px-3 py-2">
          <div className="text-muted-fg">P95 检索延迟</div>
          <div className="font-mono tabular-nums mt-0.5">{s.latency_p95_ms == null ? "--" : `${Math.round(s.latency_p95_ms)} ms`}</div>
        </div>
        <div className="rounded-md border border-border/40 px-3 py-2">
          <div className="text-muted-fg">数据集 hash</div>
          <div className="font-mono tabular-nums mt-0.5 truncate" title={datasetHash || "无 hash"}>{shortHash(datasetHash)}</div>
        </div>
        <div className="rounded-md border border-border/40 px-3 py-2">
          <div className="text-muted-fg">语料 hash / seed</div>
          <div className="font-mono tabular-nums mt-0.5 truncate" title={corpusHash || "无 hash"}>{shortHash(corpusHash)}{seed != null ? ` · ${seed}` : ""}</div>
        </div>
      </div>

      {s.valid === false && (
        <div className="flex items-start gap-2 rounded-md border px-3 py-2 text-[11px]" style={{ color: "var(--warning)", borderColor: "color-mix(in srgb, var(--warning) 30%, transparent)" }}>
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          有效测量率低于门槛，本次结果不应作为回归结论。
        </div>
      )}

      {s.valid !== false && s.comparable === false && (
        <div className="flex items-start gap-2 rounded-md border px-3 py-2 text-[11px]" style={{ color: "var(--warning)", borderColor: "color-mix(in srgb, var(--warning) 30%, transparent)" }}>
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          有效测量率达标，但运行存在检索降级、生成/Judge 缺测或状态变化，不能作为严格回归基线。
        </div>
      )}

      {radarData.length >= 3 && (
        <ResponsiveContainer width="100%" height={240}>
          <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="70%">
            <PolarGrid stroke="var(--border)" />
            <PolarAngleAxis dataKey="metric" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
            <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 9, fill: "var(--muted-foreground)" }} />
            <Radar dataKey="value" stroke="var(--primary)" fill="var(--primary)" fillOpacity={0.2} strokeWidth={2} />
          </RadarChart>
        </ResponsiveContainer>
      )}

      <p className="text-[11px] text-muted-fg">
        {isSynthetic ? (
          <>
            合成端到端评测从当前语料生成 golden 问答并由 LLM 评判；不同 golden hash 的运行不可直接比较。
            {retrievalMode === "production_replay"
              ? "检索使用生产形态的多查询、RRF、语义去重与重排。"
              : "检索只使用单查询基础向量召回。"}
          </>
        ) : (
          <>
            固定回归使用版本化 keyword qrel，Hit@K、MRR、nDCG 与 Keyword Precision / Recall 用于检索回归；
            不调用答案生成或 LLM 裁判，因此生成质量项不适用。只有评测类型、检索链路、数据集 hash 与语料 hash 全部一致的运行才可直接比较。
          </>
        )}
      </p>

      {questions.length > 0 && (
        <div>
          <button
            onClick={() => setShowDetail((v) => !v)}
            className="flex items-center gap-1 text-[11px] text-muted-fg hover:text-foreground transition-colors"
          >
            {showDetail ? <ChevronUp size={12} /> : <ChevronDown size={12} />} 逐题明细（{questions.length}）
          </button>
          {showDetail && (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-[11px] min-w-[680px]">
                <thead>
                  <tr className="border-b border-border/50 text-muted-fg">
                    <th className="text-left py-1.5 px-2 font-medium">问题 / 来源</th>
                    <th className="text-center py-1.5 px-2 font-medium">状态</th>
                    <th className="text-center py-1.5 px-2 font-medium">Rank</th>
                    {!isSynthetic && <th className="text-center py-1.5 px-2 font-medium">nDCG</th>}
                    <th className="text-center py-1.5 px-2 font-medium">Prec</th>
                    <th className="text-center py-1.5 px-2 font-medium">Recall</th>
                    {isSynthetic && <th className="text-center py-1.5 px-2 font-medium">Faith</th>}
                    {isSynthetic && <th className="text-center py-1.5 px-2 font-medium">Relv</th>}
                    {isSynthetic && <th className="text-center py-1.5 px-2 font-medium">Corr</th>}
                    <th className="text-center py-1.5 px-2 font-medium">延迟</th>
                  </tr>
                </thead>
                <tbody>
                  {questions.map((q, i) => {
                    const outcome = q.outcome || q.retrieval_status || (q.error_code ? "error" : "ok");
                    const latency = q.latency_ms ?? q.retrieval_latency_ms;
                    return (
                    <tr key={q.id || i} className="border-b border-border/30 align-top">
                      <td className="py-1.5 px-2 max-w-[16rem]">
                        <div className="truncate" title={q.question}>{q.question}</div>
                        <div className="text-[10px] text-muted-fg truncate" title={q.gold_source || q.bundle_id}>{q.gold_source || q.bundle_id || [q.difficulty, q.type].filter(Boolean).join(" · ") || "—"}</div>
                      </td>
                      <td className="py-1.5 px-2 text-center">
                        <span className={cn(
                          "text-[10px]",
                          outcome === "ok" ? "text-green-500" : ["empty", "degraded"].includes(outcome) ? "text-yellow-500" : "text-red-500",
                        )} title={q.error || q.retrieval_error || q.error_code || outcome}>
                          {outcome}
                        </span>
                      </td>
                      <td className="py-1.5 px-2 text-center font-mono tabular-nums">
                        {q.rank ?? "—"}{q.trivial_hit ? <span title="题面与源文高度重合（词面泄漏），仅供参考" className="ml-0.5 text-[9px]" style={{ color: "var(--warning)" }}>*</span> : null}
                      </td>
                      {!isSynthetic && <td className="py-1.5 px-2 text-center"><MetricPill value={q.ndcg_at_k} metricKey="ndcg_at_k" /></td>}
                      <td className="py-1.5 px-2 text-center"><MetricPill value={q.context_precision} metricKey="context_precision" /></td>
                      <td className="py-1.5 px-2 text-center"><MetricPill value={q.context_recall} metricKey="context_recall" /></td>
                      {isSynthetic && <td className="py-1.5 px-2 text-center"><MetricPill value={q.faithfulness} metricKey="faithfulness" /></td>}
                      {isSynthetic && <td className="py-1.5 px-2 text-center"><MetricPill value={q.answer_relevancy} metricKey="answer_relevancy" /></td>}
                      {isSynthetic && <td className="py-1.5 px-2 text-center"><MetricPill value={q.answer_correctness} metricKey="answer_correctness" /></td>}
                      <td className="py-1.5 px-2 text-center font-mono tabular-nums">{latency == null ? "--" : `${Math.round(latency)} ms`}</td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
