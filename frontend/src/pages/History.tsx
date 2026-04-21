import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { X } from "lucide-react";
import { getHistory, deleteSession, getInterviewTopics } from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const PAGE_SIZE = 15;

const MODE_BADGES: Record<string, { text: string; variant: string }> = {
  resume: { text: "简历面试", variant: "default" },
  topic_drill: { text: "专项训练", variant: "success" },
  jd_prep: { text: "JD 备面", variant: "blue" },
  recording: { text: "录音复盘", variant: "blue" },
};

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
  const [topics, setTopics] = useState<string[]>([]);

  useEffect(() => { getInterviewTopics().then(setTopics).catch(() => {}); }, []);

  const fetchSessions = useCallback((reset: boolean) => {
    const offset = reset ? 0 : sessions.length;
    const setter = reset ? setLoading : setLoadingMore;
    setter(true);
    const mode = modeFilter === "all" ? null : modeFilter;
    const topic = topicFilter === "all" ? null : topicFilter;
    getHistory(PAGE_SIZE, offset, mode, topic)
      .then((data: any) => {
        setSessions((prev) => (reset ? data.items : [...prev, ...data.items]));
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => setter(false));
  }, [modeFilter, topicFilter, sessions.length]);

  useEffect(() => { fetchSessions(true); }, [modeFilter, topicFilter]);

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
    } catch (err: any) {
      alert("删除失败: " + err.message);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full space-y-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-10 w-full" />
        {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
      </div>
    );
  }

  const hasFilters = modeFilter !== "all" || topicFilter !== "all";

  return (
    <div className="flex-1 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full">
      <div className="flex items-baseline justify-between mb-5 animate-fade-in">
        <div className="text-2xl md:text-[28px] font-display font-bold">历史记录</div>
        <div className="text-sm text-dim">共 {total} 条记录</div>
      </div>

      <div className="flex items-center gap-2 mb-5 flex-wrap animate-fade-in-up">
        {FILTER_OPTIONS.map((m) => (
          <Button
            key={m.key}
            variant={modeFilter === m.key ? "secondary" : "ghost"}
            size="sm"
            className={modeFilter === m.key ? "border-primary border text-text" : ""}
            onClick={() => handleModeChange(m.key)}
          >
            {m.label}
          </Button>
        ))}

        {modeFilter !== "resume" && modeFilter !== "jd_prep" && topics.length > 0 && (
          <>
            <div className="w-px h-5 bg-border mx-1" />
            <select
              className="px-3.5 py-1.5 rounded-lg text-[13px] bg-input-bg text-text border border-border outline-none cursor-pointer"
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
              const badge = MODE_BADGES[s.mode] || MODE_BADGES.resume;
              const title = s.meta?.position || s.topic || "综合";
              const subtitle = s.meta?.company || "";

              return (
                <Card
                  key={s.session_id}
                  className="cursor-pointer hover:border-primary/50 hover:-translate-y-px hover:shadow-sm transition-all"
                  onClick={() => navigate(`/review/${s.session_id}`)}
                >
                  <CardContent className="p-3.5 md:p-4 flex items-center justify-between">
                    <div className="flex items-center gap-2 md:gap-2.5 min-w-0 flex-1 flex-wrap">
                      <Badge variant={badge.variant as any}>{badge.text}</Badge>
                      <span className="text-sm text-text font-medium truncate">{title}</span>
                      {subtitle && <span className="text-xs text-dim truncate">{subtitle}</span>}
                      <span className="text-xs text-dim shrink-0 hidden md:inline">#{s.session_id}</span>
                    </div>
                    <div className="flex items-center gap-2 md:gap-3 shrink-0">
                      <ScorePill score={s.avg_score} />
                      <span className="text-[11px] text-dim whitespace-nowrap md:hidden">{s.created_at?.slice(5, 10)}</span>
                      <span className="text-[13px] text-dim whitespace-nowrap hidden md:inline">{s.created_at?.slice(0, 10)}</span>
                      <button
                        className="p-1.5 rounded-md text-dim text-[15px] opacity-50 hover:text-red hover:opacity-100 transition-all cursor-pointer"
                        title="删除"
                        onClick={(e) => handleDelete(e, s.session_id)}
                      >
                        <X size={14} />
                      </button>
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
  let bg: string, color: string;
  if (score >= 8) { bg = "rgba(34,197,94,0.15)"; color = "var(--green)"; }
  else if (score >= 6) { bg = "rgba(245,158,11,0.15)"; color = "var(--ai-glow)"; }
  else if (score >= 4) { bg = "rgba(253,203,110,0.2)"; color = "#e2b93b"; }
  else { bg = "rgba(239,68,68,0.15)"; color = "var(--red)"; }
  return (
    <Badge variant="outline" className="min-w-[52px] justify-center font-semibold text-[13px]" style={{ background: bg, borderColor: "transparent", color }}>
      {score}/10
    </Badge>
  );
}
