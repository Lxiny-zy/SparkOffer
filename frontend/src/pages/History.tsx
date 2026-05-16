import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { X, Clock, MessageSquare } from "lucide-react";
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
  const [topics, setTopics] = useState<string[]>([]);

  useEffect(() => { getInterviewTopics().then(setTopics).catch(() => {}); }, []);

  const fetchSessions = useCallback((reset: boolean) => {
    const offset = reset ? 0 : sessions.length;
    const setter = reset ? setLoading : setLoadingMore;
    setter(true);
    const mode = modeFilter === "all" ? null : modeFilter;
    const topic = topicFilter === "all" ? null : topicFilter;
    getHistory(PAGE_SIZE, offset, mode, topic, statusFilter)
      .then((data: any) => {
        setSessions((prev) => (reset ? data.items : [...prev, ...data.items]));
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => setter(false));
  }, [modeFilter, topicFilter, statusFilter, sessions.length]);

  useEffect(() => { fetchSessions(true); }, [modeFilter, topicFilter, statusFilter]);

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
    <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full">
      <div className="flex items-baseline justify-between mb-5 animate-fade-in relative">
        <div className="absolute -top-6 -left-6 w-[180px] h-[120px] rounded-full pointer-events-none opacity-20" style={{ background: "radial-gradient(ellipse, var(--glow-accent), transparent 70%)" }} />
        <div className="text-2xl md:text-[28px] font-display font-bold aurora-text relative">历史记录</div>
        <div className="text-sm text-dim">共 {total} 条记录</div>
      </div>

      <div className="flex items-center gap-1 mb-4 p-1 rounded-full bg-card/50 border border-border w-fit">
        {([
          { key: "completed", label: "已完成" },
          { key: "in_progress", label: "进行中" },
        ] as const).map((opt) => {
          const active = statusFilter === opt.key;
          return (
            <button
              key={opt.key}
              onClick={() => setStatusFilter(opt.key)}
              className={cn(
                "px-4 py-1.5 rounded-full text-[12px] font-medium transition-all duration-200 cursor-pointer",
                active
                  ? "bg-primary text-primary-foreground shadow-[0_0_12px_var(--glow-primary)]"
                  : "text-dim hover:text-text"
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
              className={cn(
                "px-3.5 py-1.5 rounded-full text-[12px] font-medium transition-all duration-200 border cursor-pointer",
                active
                  ? "bg-primary/15 text-primary border-primary/30 ring-2 ring-primary/15 shadow-[0_0_12px_var(--glow-primary)]"
                  : "bg-card/50 text-dim border-border hover:text-text hover:border-primary/40 hover:bg-card hover:-translate-y-px"
              )}
            >
              {m.label}
            </button>
          );
        })}

        {modeFilter !== "resume" && modeFilter !== "jd_prep" && topics.length > 0 && (
          <>
            <div className="w-px h-5 bg-border mx-1" />
            <select
              className="px-3.5 py-1.5 rounded-full text-[12px] bg-card/50 text-text border border-border outline-none cursor-pointer hover:border-primary/40 transition-colors"
              value={topicFilter}
              onChange={(e) => setTopicFilter(e.target.value)}
            >
              <option value="all">全部领域</option>
              {topics.map((t) => <option key={t} value={t}>{t}</option>)}
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
              const title = s.meta?.position || s.topic || "综合面试";
              const subtitle = s.meta?.company || "";
              const timeRaw = s.created_at?.slice(0, 16)?.replace("T", " ") || "";
              const qCount = s.question_count ?? s.questions?.length;
              const shortId = s.session_id?.slice(-6);

              return (
                <Card
                  key={s.session_id}
                  className="group cursor-pointer hover:border-primary/50 hover:-translate-y-px hover:shadow-[0_6px_20px_var(--glow-primary)] transition-all duration-300 overflow-hidden card-hover-lift"
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
                          <span className="text-sm text-text font-medium truncate">{title}</span>
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
                        <button
                          className="p-1.5 rounded-md text-dim opacity-0 group-hover:opacity-100 hover:text-red hover:bg-red/10 transition-all cursor-pointer"
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
              onClick={() => fetchSessions(false)}
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
