import { useState, useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, RadarChart, PolarGrid, PolarAngleAxis,
  PolarRadiusAxis, Radar, BarChart, Bar,
} from "recharts";
import { BarChart3, RefreshCw, ExternalLink, FlaskConical, Loader2, ChevronDown, ChevronUp, AlertTriangle } from "lucide-react";
import { getRAGMetrics, getTopics, type RAGMetricsRecord } from "../api/interview";
import { startRagEval, getRagEvalStatus, type RagEvalStatus, type RagEvalSummary, type RagEvalQuestionDetail } from "../api/ragEval";
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
        {d.discrimination != null && <div style={{ color: "var(--green)" }}>区分度: {fmtPct01(d.discrimination)}</div>}
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
        {d.faithfulness != null && <div style={{ color: "var(--sig-chart-2)" }}>忠实度: {fmtPct01(d.faithfulness)}</div>}
        {d.answer_relevance != null && <div style={{ color: "var(--sig-chart-1)" }}>切题度: {fmtPct01(d.answer_relevance)}</div>}
        {d.correctness != null && <div style={{ color: "var(--primary)" }}>综合: {fmtPct01(d.correctness)}</div>}
      </div>
    </div>
  );
}

export default function RAGDashboard() {
  const navigate = useNavigate();
  const [records, setRecords] = useState<RAGMetricsRecord[]>([]);
  const [topics, setTopics] = useState<Record<string, any>>({});
  const [selectedTopic, setSelectedTopic] = useState<string>("");
  const [loading, setLoading] = useState(true);

  // RAG eval (true RAGAS benchmark) — async backend job + frontend polling
  const [judgeMode, setJudgeMode] = useState<"standard" | "full">("standard");
  const [nQuestions, setNQuestions] = useState(20);
  const [evalStatus, setEvalStatus] = useState<RagEvalStatus | null>(null);
  const [evalRunning, setEvalRunning] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

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

  useEffect(() => { loadData(); }, []);

  // Stop polling on unmount.
  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const runEval = async () => {
    if (!selectedTopic) {
      setEvalError("请先在上方选择一个具体 Topic（非\"全部\"）再运行评测。");
      return;
    }
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    setEvalError(null);
    setEvalStatus(null);
    setEvalRunning(true);
    try {
      const { job_id } = await startRagEval({ topic: selectedTopic, n_questions: nQuestions, judge_mode: judgeMode });
      const poll = async () => {
        try {
          const s = await getRagEvalStatus(job_id);
          setEvalStatus(s);
          if (s.status === "completed" || s.status === "failed") {
            if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
            setEvalRunning(false);
            if (s.status === "failed") setEvalError(s.error || "评测失败");
          }
        } catch (e: any) {
          if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
          setEvalRunning(false);
          setEvalError(e?.message || "查询进度失败");
        }
      };
      await poll();
      pollRef.current = window.setInterval(poll, 1500);
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
      discrimination: r.discrimination,
      diversity: r.diversity,
    })),
    [retrievalRecords],
  );

  // Gen trend data
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
      if (r.stage === "question_gen") map[r.session_id].retrieval = r;
      else if (r.stage === "answer_eval") map[r.session_id].eval = r;
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
        <Button variant="outline" size="sm" onClick={loadData} className="gap-1.5">
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

      {/* RAG 评测（真 RAGAS 基准） */}
      <Card>
        <CardContent className="p-4 space-y-3">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <div className="flex items-center gap-2">
                <FlaskConical size={16} className="text-primary" />
                <span className="text-sm font-medium">RAG 评测（真 RAGAS 基准）</span>
              </div>
              <p className="text-[11px] text-muted-fg mt-1 max-w-xl">
                从所选 Topic 知识库自动合成 golden 集，调用模型与向量库跑 hit@k / MRR / precision / recall /
                faithfulness / answer_relevancy / correctness。后端异步执行，前端轮询进度。
              </p>
            </div>
            <div className="flex items-center gap-2">
              {(["standard", "full"] as const).map((m) => (
                <Badge
                  key={m}
                  variant={judgeMode === m ? "default" : "outline"}
                  className={cn("cursor-pointer hover:-translate-y-px hover:brightness-110", evalRunning && "pointer-events-none opacity-50")}
                  onClick={() => !evalRunning && setJudgeMode(m)}
                  title={m === "standard"
                    ? "标准：检索指标嵌入锚定，生成侧 LLM 评判（约 5 次调用/题）"
                    : "完整：precision 也逐 chunk LLM 判定（约 13 次/题，更慢更贵）"}
                >
                  {m === "standard" ? "标准" : "完整"}
                </Badge>
              ))}
              <select
                value={nQuestions}
                disabled={evalRunning}
                onChange={(e) => setNQuestions(Number(e.target.value))}
                className="h-8 rounded-md border border-border bg-card px-2 text-xs text-foreground disabled:opacity-50"
                title="评测题量"
              >
                {[5, 10, 20, 30].map((n) => <option key={n} value={n}>{n} 题</option>)}
              </select>
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

      {records.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center text-muted-fg">
            <p className="text-lg mb-2">暂无 RAG 指标数据</p>
            <p className="text-sm">完成一次专项训练后，检索和评估质量指标将在这里展示。</p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MetricCard label="平均相关度" value={avgRelevance} delta={relevanceDelta} />
            <MetricCard label="平均忠实度" value={avgFaithfulness} delta={faithfulnessDelta} />
            <div className="rounded-xl border border-border/40 bg-card/60 px-4 py-3 text-center">
              <div className="text-[11px] text-muted-fg mb-1">检索 Sessions</div>
              <div className="text-2xl font-bold font-mono tabular-nums text-foreground">{retrievalRecords.length}</div>
            </div>
            <div className="rounded-xl border border-border/40 bg-card/60 px-4 py-3 text-center">
              <div className="text-[11px] text-muted-fg mb-1">评估 Sessions</div>
              <div className="text-2xl font-bold font-mono tabular-nums text-foreground">{evalRecords.length}</div>
            </div>
          </div>

          {/* Row 1: Retrieval trend + Radar */}
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
            <Card className="lg:col-span-3">
              <CardContent className="p-4">
                <div className="text-xs font-medium text-muted-fg mb-3">检索质量趋势</div>
                {retrievalTrend.length >= 2 ? (
                  <ResponsiveContainer width="100%" height={240}>
                    <LineChart data={retrievalTrend} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <Tooltip content={<RetrievalTrendTooltip />} />
                      <Line type="monotone" dataKey="relevance" stroke="var(--primary)" strokeWidth={2} dot={false} name="相关度" />
                      <Line type="monotone" dataKey="discrimination" stroke="var(--green)" strokeWidth={1.5} dot={false} name="区分度" />
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
                <div className="text-xs font-medium text-muted-fg mb-3">检索质量分布</div>
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
                <div className="text-xs font-medium text-muted-fg mb-3">生成质量趋势</div>
                {genTrend.length >= 2 ? (
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={genTrend} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
                      <Tooltip content={<GenTrendTooltip />} />
                      <Line type="monotone" dataKey="faithfulness" stroke="var(--sig-chart-2)" strokeWidth={2} dot={false} name="忠实度" />
                      <Line type="monotone" dataKey="answer_relevance" stroke="var(--sig-chart-1)" strokeWidth={1.5} dot={false} name="切题度" />
                      <Line type="monotone" dataKey="correctness" stroke="var(--primary)" strokeWidth={1.5} dot={false} name="综合质量" />
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

          {/* Row 3: Recent sessions table */}
          <Card>
            <CardContent className="p-4">
              <div className="text-xs font-medium text-muted-fg mb-3">近期 Session 明细</div>
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
                        <th className="text-center py-2 px-2 font-medium">区分</th>
                        <th className="text-center py-2 px-2 font-medium">多样</th>
                        <th className="text-center py-2 px-2 font-medium">忠实</th>
                        <th className="text-center py-2 px-2 font-medium">切题</th>
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
                            <MetricPill value={retrieval?.discrimination} metricKey="discrimination" />
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
  synthesizing: "合成评测集",
  evaluating: "评测中",
  aggregating: "汇总结果",
  completed: "完成",
  failed: "失败",
};

function EvalProgress({ status }: { status: RagEvalStatus }) {
  const { phase, done, total } = status;
  const pct = total > 0 ? Math.round((done / total) * 100) : phase === "synthesizing" ? 5 : 0;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[11px] text-muted-fg">
        <span className="flex items-center gap-1.5">
          <Loader2 size={12} className="animate-spin" />
          {EVAL_PHASE_LABEL[phase] || phase}
          {total > 0 && phase === "evaluating" ? ` · ${done}/${total}` : ""}
        </span>
        <span className="font-mono tabular-nums">{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

const RESULT_METRICS: { key: keyof RagEvalSummary; label: string }[] = [
  { key: "hit_at_k_strict", label: "Hit@K (严格)" },
  { key: "mrr", label: "MRR" },
  { key: "context_precision", label: "Precision" },
  { key: "context_recall", label: "Recall" },
  { key: "faithfulness", label: "Faithfulness" },
  { key: "answer_relevancy", label: "Relevancy" },
  { key: "answer_correctness", label: "Correctness" },
];

function RagEvalResultCard({ status, topicName }: { status: RagEvalStatus; topicName: string }) {
  const [showDetail, setShowDetail] = useState(false);
  const s = status.summary!;
  const questions: RagEvalQuestionDetail[] = status.detail?.questions ?? [];
  // Older runs predate hit_at_k_strict — fall back to hit_at_k so they still render.
  const metricValue = (key: keyof RagEvalSummary): number | null => {
    if (key === "hit_at_k_strict" && s.hit_at_k_strict == null) return s.hit_at_k ?? null;
    return (s[key] as number | null) ?? null;
  };
  const radarData = RESULT_METRICS.map(({ key, label }) => ({
    metric: label,
    value: metricValue(key) == null ? 0 : Math.round((metricValue(key) as number) * 100),
  }));
  return (
    <div className="rounded-xl border border-border/50 bg-muted/20 p-4 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <span className="text-sm font-medium">本次评测结果 · {topicName}</span>
        <span className="text-[11px] text-muted-fg">
          {s.n_questions} 题{s.error_count > 0 ? ` · ${s.error_count} 题失败` : ""} · {status.judge_mode === "full" ? "完整评判" : "标准评判"}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {RESULT_METRICS.map(({ key, label }) => (
          <MetricCard key={key} label={label} value={metricValue(key)} metricKey={key} />
        ))}
      </div>

      <ResponsiveContainer width="100%" height={240}>
        <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="70%">
          <PolarGrid stroke="var(--border)" />
          <PolarAngleAxis dataKey="metric" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
          <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 9, fill: "var(--muted-foreground)" }} />
          <Radar dataKey="value" stroke="var(--primary)" fill="var(--primary)" fillOpacity={0.2} strokeWidth={2} />
        </RadarChart>
      </ResponsiveContainer>

      <p className="text-[11px] text-muted-fg">
        注：评测基于<strong className="text-foreground">基础向量检索</strong>，不含线上出题链路的 RRF 融合 / 重排；
        Hit@K、MRR 反映底层检索质量，非端到端出题效果。
        <strong className="text-foreground">严格 Hit@K</strong> 已排除「检索到 gold 问题自身源文」的送分自命中，
        比原始 Hit@K 更接近真实泛化检索表现。Faithfulness / Recall 采用 full/partial/none 三档加权，
        避免「沾边即满分」的乐观偏差。
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
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="border-b border-border/50 text-muted-fg">
                    <th className="text-left py-1.5 px-2 font-medium">问题 / 来源</th>
                    <th className="text-center py-1.5 px-2 font-medium">Rank</th>
                    <th className="text-center py-1.5 px-2 font-medium">Prec</th>
                    <th className="text-center py-1.5 px-2 font-medium">Recall</th>
                    <th className="text-center py-1.5 px-2 font-medium">Faith</th>
                    <th className="text-center py-1.5 px-2 font-medium">Relv</th>
                    <th className="text-center py-1.5 px-2 font-medium">Corr</th>
                  </tr>
                </thead>
                <tbody>
                  {questions.map((q, i) => (
                    <tr key={i} className="border-b border-border/30 align-top">
                      <td className="py-1.5 px-2 max-w-[16rem]">
                        <div className="truncate" title={q.question}>{q.question}</div>
                        <div className="text-[10px] text-muted-fg truncate" title={q.gold_source}>{q.gold_source || "—"}</div>
                      </td>
                      <td className="py-1.5 px-2 text-center font-mono tabular-nums">
                        {q.rank ?? "—"}{q.trivial_hit ? <span title="送分自命中（检索到 gold 源文自身）" className="ml-0.5 text-[9px]" style={{ color: "var(--warning)" }}>*</span> : null}
                      </td>
                      <td className="py-1.5 px-2 text-center"><MetricPill value={q.context_precision} metricKey="context_precision" /></td>
                      <td className="py-1.5 px-2 text-center"><MetricPill value={q.context_recall} metricKey="context_recall" /></td>
                      <td className="py-1.5 px-2 text-center"><MetricPill value={q.faithfulness} metricKey="faithfulness" /></td>
                      <td className="py-1.5 px-2 text-center"><MetricPill value={q.answer_relevancy} metricKey="answer_relevancy" /></td>
                      <td className="py-1.5 px-2 text-center"><MetricPill value={q.answer_correctness} metricKey="answer_correctness" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
