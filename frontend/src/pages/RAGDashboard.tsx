import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, RadarChart, PolarGrid, PolarAngleAxis,
  PolarRadiusAxis, Radar, BarChart, Bar,
} from "recharts";
import { BarChart3, RefreshCw, ExternalLink } from "lucide-react";
import { getRAGMetrics, getTopics, type RAGMetricsRecord } from "../api/interview";
import { cn } from "@/lib/utils";
import { fmtPct01, metricColorVar } from "@/lib/metrics";
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

function MetricCard({ label, value, delta }: { label: string; value: number | null; delta?: number | null }) {
  if (value == null) return (
    <div className="rounded-xl border border-border/40 bg-card/60 px-4 py-3 text-center">
      <div className="text-[11px] text-muted-fg mb-1">{label}</div>
      <div className="text-2xl font-bold font-mono text-muted-fg">--</div>
    </div>
  );
  const p = Math.round(value * 100);
  const color = metricColorVar(value);
  const deltaPct = delta != null ? Math.round(delta * 100) : null;
  return (
    <div className="rounded-xl border border-border/40 bg-card/60 px-4 py-3 text-center">
      <div className="text-[11px] text-muted-fg mb-1">{label}</div>
      <div className="text-2xl font-bold font-mono tabular-nums" style={{ color }}>{p}%</div>
      {deltaPct != null && deltaPct !== 0 && (
        <div className={cn("text-[11px] font-mono", deltaPct > 0 ? "text-green-500" : "text-red-500")}>
          {deltaPct > 0 ? "+" : ""}{deltaPct}%
        </div>
      )}
    </div>
  );
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
        {d.precision != null && <div style={{ color: "var(--green)" }}>排序质量: {fmtPct01(d.precision)}</div>}
        {d.recall != null && <div style={{ color: "var(--warning)" }}>覆盖率: {fmtPct01(d.recall)}</div>}
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
        {d.faithfulness != null && <div style={{ color: "#60a5fa" }}>忠实度: {fmtPct01(d.faithfulness)}</div>}
        {d.answer_relevance != null && <div style={{ color: "#a78bfa" }}>切题度: {fmtPct01(d.answer_relevance)}</div>}
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

  const filtered = useMemo(() => {
    if (!selectedTopic) return records;
    return records.filter((r) => r.topic === selectedTopic);
  }, [records, selectedTopic]);

  const retrievalRecords = useMemo(() =>
    filtered.filter((r) => r.stage === "question_gen" && r.context_relevance != null),
    [filtered],
  );

  const evalRecords = useMemo(() =>
    filtered.filter((r) => r.stage === "answer_eval" && r.faithfulness != null),
    [filtered],
  );

  // Summary cards — headline value is the overall average.
  const avgRelevance = retrievalRecords.length
    ? retrievalRecords.reduce((s, r) => s + (r.context_relevance ?? 0), 0) / retrievalRecords.length : null;
  const avgFaithfulness = evalRecords.length
    ? evalRecords.reduce((s, r) => s + (r.faithfulness ?? 0), 0) / evalRecords.length : null;

  // Trend arrow: newer half mean − older half mean (records are DESC).
  const relevanceDelta = halfMeanDelta(retrievalRecords, (r) => r.context_relevance);
  const faithfulnessDelta = halfMeanDelta(evalRecords, (r) => r.faithfulness);

  // Retrieval trend data (chronological)
  const retrievalTrend = useMemo(() =>
    [...retrievalRecords].reverse().map((r, i) => ({
      index: i,
      date: r.created_at?.slice(0, 10) || "",
      topic: r.topic,
      relevance: r.context_relevance,
      precision: r.context_precision,
      recall: r.context_recall,
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
      if (r.context_relevance != null) byTopic[r.topic].push(r.context_relevance);
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
      const v = r.context_relevance ?? 0;
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
          <h1 className="text-2xl font-display font-bold">RAG 质量仪表盘</h1>
        </div>
        <Button variant="outline" size="sm" onClick={loadData} className="gap-1.5">
          <RefreshCw size={14} /> 刷新
        </Button>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-2 flex-wrap">
        <Badge
          variant={selectedTopic === "" ? "default" : "outline"}
          className="cursor-pointer"
          onClick={() => setSelectedTopic("")}
        >
          全部
        </Badge>
        {topicKeys.map((k) => (
          <Badge
            key={k}
            variant={selectedTopic === k ? "default" : "outline"}
            className="cursor-pointer"
            onClick={() => setSelectedTopic(k)}
          >
            {topics[k]?.name || k}
          </Badge>
        ))}
      </div>

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
                      <Line type="monotone" dataKey="precision" stroke="var(--green)" strokeWidth={1.5} dot={false} name="排序质量" />
                      <Line type="monotone" dataKey="recall" stroke="var(--warning)" strokeWidth={1.5} dot={false} name="覆盖率" />
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
                      <Bar dataKey="good" stackId="a" fill="#60a5fa" name="良好 (50-70%)" />
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
                      <Line type="monotone" dataKey="faithfulness" stroke="#60a5fa" strokeWidth={2} dot={false} name="忠实度" />
                      <Line type="monotone" dataKey="answer_relevance" stroke="#a78bfa" strokeWidth={1.5} dot={false} name="切题度" />
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
                        <th className="text-center py-2 px-2 font-medium">排序</th>
                        <th className="text-center py-2 px-2 font-medium">覆盖</th>
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
                            <MetricPill value={retrieval?.context_relevance} />
                          </td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={retrieval?.context_precision} />
                          </td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={retrieval?.context_recall} />
                          </td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={evalR?.faithfulness} />
                          </td>
                          <td className="py-2 px-2 text-center">
                            <MetricPill value={evalR?.answer_relevance} />
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

function MetricPill({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-muted-fg">--</span>;
  const p = Math.round(value * 100);
  return (
    <span className="font-mono tabular-nums font-medium" style={{ color: metricColorVar(value) }}>
      {p}%
    </span>
  );
}
