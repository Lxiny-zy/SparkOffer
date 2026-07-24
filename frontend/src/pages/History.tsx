import React, { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { X, Clock, MessageSquare, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { getHistory, deleteSession, getInterviewTopics } from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getModeBadge, getScoreBand } from "@/lib/badge-presets";

const PAGE_SIZE = 15;

const FILTER_OPTIONS = [
  { key: "all", label: "全部" },
  { key: "resume", label: "简历面试" },
  { key: "topic_drill", label: "专项训练" },
  { key: "jd_prep", label: "JD 备面" },
  { key: "recording", label: "录音复盘" },
];

export default function History() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [modeFilter, setModeFilter] = useState("all");
  const [topicFilter, setTopicFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<"completed" | "in_progress">("completed");
  const [topics, setTopics] = useState<{ key: string; name: string }[]>([]);
  const filterGenerationRef = useRef(0);

  useEffect(() => { getInterviewTopics().then(setTopics).catch(() => {}); }, []);

  // sessions.length intentionally NOT in deps — that would refetch every time
  // setSessions runs and re-create the callback. Loaders pass their own offset.
  const fetchSessions = useCallback((reset: boolean, currentLen = 0) => {
    const generation = reset ? ++filterGenerationRef.current : filterGenerationRef.current;
    const offset = reset ? 0 : currentLen;
    const setter = reset ? setLoading : setLoadingMore;
    setter(true);
    const mode = modeFilter === "all" ? null : modeFilter;
    const topic = topicFilter === "all" ? null : topicFilter;
    getHistory(PAGE_SIZE, offset, mode, topic, statusFilter)
      .then((data: any) => {
        if (generation !== filterGenerationRef.current) return;
        setSessions((prev) => (reset ? data.items : [...prev, ...data.items]));
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => {
        if (generation === filterGenerationRef.current) setter(false);
      });
  }, [modeFilter, topicFilter, statusFilter]);

  useEffect(() => {
    // Fetching the current server-side filter is this effect's external synchronization.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchSessions(true);
  }, [fetchSessions]);

  const handleModeChange = (mode: string) => {
    if (mode === "resume") setTopicFilter("all");
    setModeFilter(mode);
  };

  const handleDelete = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    if (!window.confirm("确定要删除这条记录吗？")) return;
    try {
      await deleteSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
      setTotal((prev) => prev - 1);
      toast.success("记录已删除");
    } catch (err: any) {
      toast.error("删除失败: " + err.message);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full space-y-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-10 w-full" />
        {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
      </div>
    );
  }

  const hasFilters = modeFilter !== "all" || topicFilter !== "all";

  return (
    <div className="sig-page flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full">
      <div className="mb-5 animate-fade-in">
        <div className="sig-kicker mb-2">// 历史记录 / HISTORY</div>
        <div className="flex items-baseline justify-between">
          <h1 className="sig-display text-2xl md:text-[28px]">训练历史<span className="sig-accent-c">.</span></h1>
          <div className="sig-num text-sm text-dim">共 {total} 条</div>
        </div>
      </div>

      <div className="flex items-center gap-1 mb-4 p-1 rounded-md w-fit border" style={{ borderColor: "var(--sig-line)" }}>
        {([
          { key: "completed", label: "已完成" },
          { key: "in_progress", label: "进行中" },
        ] as const).map((opt) => {
          const active = statusFilter === opt.key;
          return (
            <button
              key={opt.key}
              onClick={() => setStatusFilter(opt.key)}
              style={active ? { background: "var(--sig-accent)", color: "var(--sig-accent-fg)" } : undefined}
              className={cn(
                "px-4 py-1.5 rounded text-[12px] font-medium transition-all duration-200 cursor-pointer",
                !active && "text-dim hover:text-text"
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-2 mb-5 flex-wrap animate-fade-in-up">
        {FILTER_OPTIONS.map((m) => {
          const active = modeFilter === m.key;
          return (
            <button
              key={m.key}
              onClick={() => handleModeChange(m.key)}
              style={active ? { background: "color-mix(in srgb, var(--sig-accent) 10%, transparent)", borderColor: "var(--sig-accent)", color: "var(--sig-accent)" } : undefined}
              className={cn(
                "px-3.5 py-1.5 rounded-md text-[12px] font-medium transition-all duration-200 border cursor-pointer",
                !active && "text-dim border-[color:var(--sig-line)] hover:text-text hover:border-[color:var(--sig-line-2)] hover:-translate-y-px"
              )}
            >
              {m.label}
            </button>
          );
        })}

        {modeFilter !== "resume" && modeFilter !== "jd_prep" && topics.length > 0 && (
          <>
            <div className="w-px h-5 mx-1" style={{ background: "var(--sig-line)" }} />
            <select
              className="sig-field w-auto py-1.5 text-[12px] cursor-pointer"
              value={topicFilter}
              onChange={(e) => setTopicFilter(e.target.value)}
            >
              <option value="all">全部领域</option>
              {topics.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select>
          </>
        )}
      </div>

      {sessions.length === 0 ? (
        <div className="text-center py-15 text-dim animate-fade-in">
          <p>{hasFilters ? "没有匹配的记录，试试调整筛选条件" : "还没有面试记录，去首页开始一场面试吧"}</p>
        </div>
      ) : (
        <>
          <div className="flex flex-col gap-2.5 stagger-children">
            {sessions.map((s) => {
              const badge = getModeBadge(s.mode);
              const title = s.meta?.position || s.topic_name || s.topic || "综合面试";
              const subtitle = s.meta?.company || "";
              const timeRaw = s.created_at?.slice(0, 16)?.replace("T", " ") || "";
              const qCount = s.question_count ?? s.questions?.length;
              const shortId = s.session_id?.slice(-6);

              return (
                <Card
                  key={s.session_id}
                  hoverLift
                  className="group cursor-pointer overflow-hidden"
                  onClick={() => navigate(
                    statusFilter === "in_progress" ? `/interview/${s.session_id}` : `/review/${s.session_id}`
                  )}
                >
                  <CardContent className="p-3.5 md:p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        {/* L1 — Mode + Title + Subtitle */}
                        <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                          <Badge variant={badge.variant as any} className="shrink-0 text-[10px]">{badge.text}</Badge>
                          {statusFilter === "in_progress" && s.awaiting_eval && (
                            <Badge
                              variant="outline"
                              className="shrink-0 text-[10px]"
                              style={{
                                background: "color-mix(in srgb, var(--sig-warning) 10%, transparent)",
                                borderColor: "color-mix(in srgb, var(--sig-warning) 42%, transparent)",
                                color: "var(--sig-warning)",
                              }}
                            >
                              评估未完成
                            </Badge>
                          )}                          <span className="text-sm text-text font-medium truncate">{title}</span>
                          {subtitle && <span className="text-xs text-dim truncate">· {subtitle}</span>}
                        </div>
                        {/* L2 — Meta */}
                        <div className="flex items-center gap-2 text-[11px] text-dim">
                          {timeRaw && (
                            <span className="flex items-center gap-1 font-mono">
                              <Clock size={11} className="opacity-60" /> {timeRaw}
                            </span>
                          )}
                          {qCount != null && (
                            <>
                              <span className="opacity-40">·</span>
                              <span className="flex items-center gap-1">
                                <MessageSquare size={11} className="opacity-60" /> {qCount} 轮
                              </span>
                            </>
                          )}
                          {shortId && (
                            <span className="font-mono text-dim/50 ml-auto">#{shortId}</span>
                          )}
                        </div>
                      </div>
                      {/* Right rail */}
                      <div className="flex items-center gap-2 shrink-0">
                        <ScorePill score={s.avg_score} />
                        {(s.mode === "topic_drill" || s.mode === "jd_prep") && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 px-2 text-[11px] gap-1"
                            style={{
                              borderColor: "color-mix(in srgb, var(--sig-warning) 42%, transparent)",
                              color: "var(--sig-warning)",
                            }}
                            title="重新生成评估报告"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigate(`/interview/${s.session_id}`, { state: { autoEvaluate: true } });
                            }}
                          >
                            <RotateCcw size={12} /> 重新评估
                          </Button>
                        )}
                        <button
                          className="p-1.5 rounded-md text-dim opacity-0 group-hover:opacity-100 hover:text-[color:var(--sig-danger)] hover:bg-secondary transition-all cursor-pointer"
                          title="删除"
                          onClick={(e) => handleDelete(e, s.session_id)}
                        >
                          <X size={14} />
                        </button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {sessions.length < total && (
            <Button
              variant="outline"
              className="block w-full py-3 mt-4"
              onClick={() => fetchSessions(false, sessions.length)}
              disabled={loadingMore}
            >
              {loadingMore ? "加载中..." : `加载更多 (${sessions.length}/${total})`}
            </Button>
          )}
        </>
      )}
    </div>
  );
}

function ScorePill({ score }: { score: number | null | undefined }) {
  if (score == null) {
    return <Badge variant="secondary" className="min-w-[52px] justify-center text-[13px]">--</Badge>;
  }
  const band = getScoreBand(score);
  return (
    <Badge variant="outline" className="min-w-[52px] justify-center font-semibold text-[13px] transition-transform group-hover:scale-105" style={{ background: band.bg, borderColor: "transparent", color: band.color }}>
      {score}/10
    </Badge>
  );
}
