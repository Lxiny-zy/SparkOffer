import React, { useState, useEffect, useCallback } from "react";
import { useNavigate, Link } from "react-router-dom";
import { ChevronRight, TrendingUp, Download, BarChart3, Target, Brain } from "lucide-react";
import { getProfile, exportProfile } from "../api/interview";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import ScoreTrendChart from "@/components/charts/ScoreTrendChart";
import TopicRadarChart from "@/components/charts/TopicRadarChart";
import DimensionTrendChart from "@/components/charts/DimensionTrendChart";
import LearningHeatmap from "@/components/charts/LearningHeatmap";
import KnowledgeTreemap from "@/components/charts/KnowledgeTreemap";
import type { Profile as ProfileType } from "../types/api";

interface CollapsibleListProps {
  items: any[];
  limit: number;
  renderItem: (item: any, index: number) => React.ReactNode;
}

function CollapsibleList({ items, limit, renderItem }: CollapsibleListProps) {
  const [expanded, setExpanded] = useState(false);
  const show = expanded ? items : items.slice(0, limit);
  const hasMore = items.length > limit;

  return (
    <div className="flex flex-col gap-2">
      {show.map((item, i) => renderItem(item, i))}
      {hasMore && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-primary text-[13px] cursor-pointer py-1 text-left hover:underline"
        >
          {expanded ? "收起" : `展开更多 (+${items.length - limit})`}
        </button>
      )}
    </div>
  );
}


export default function Profile() {
  const [profile, setProfile] = useState<ProfileType | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const loadProfile = useCallback(() => {
    getProfile()
      .then(setProfile)
      .catch(() => setProfile(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  // Auto-refresh profile when user returns to the page (e.g. after training).
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") loadProfile();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [loadProfile]);

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full space-y-4">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-4 w-64" />
        <div className="grid grid-cols-2 gap-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
        <Skeleton className="h-48" />
      </div>
    );
  }

  const hasData = profile && (
    (profile.stats?.total_sessions ?? 0) > 0 ||
    (profile.stats?.total_answers ?? 0) > 0 ||
    (profile.weak_points || []).length > 0 ||
    (profile.strong_points || []).length > 0
  );

  if (!hasData) {
    return (
      <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full">
        <h1 className="sig-display text-2xl md:text-[28px] mb-2 animate-fade-in">成长报告<span className="sig-accent-c">.</span></h1>
        <div className="text-center py-15 text-dim animate-fade-in-up">
          <p>还没有面试数据</p>
          <p className="mt-3 text-sm">开始面试后，系统会实时分析你的每个回答，自动构建你的能力画像</p>
          <Button variant="default" className="mt-5" onClick={() => navigate("/")}>
            开始第一场面试
          </Button>
        </div>
      </div>
    );
  }

  const stats = profile!.stats ?? ({} as NonNullable<typeof profile.stats>);
  const weakActive = (profile!.weak_points || []).filter((w) => !w.improved);
  const weakImproved = (profile!.weak_points || []).filter((w) => w.improved);

  return (
    <div className="sig-page flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full">
      <div className="flex items-center justify-between mb-2 animate-fade-in">
        <div>
          <div className="sig-kicker mb-2">// 成长报告 / PROFILE</div>
          <h1 className="sig-display text-2xl md:text-[32px]">成长报告<span className="sig-accent-c">.</span></h1>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={async () => {
            try {
              const { markdown, filename } = await exportProfile();
              const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = filename;
              a.click();
              URL.revokeObjectURL(url);
            } catch (err: any) {
              alert("导出失败: " + err.message);
            }
          }}
        >
          <Download size={14} />
          导出画像
        </Button>
      </div>
      <div className="text-sm text-dim mb-8 animate-fade-in-up">
        {stats.total_answers || 0} 次回答分析{stats.total_sessions ? ` | ${stats.total_sessions} 次完整面试` : ""} | 上次更新: {profile!.updated_at?.slice(0, 16)}
      </div>

      <div className="mb-7 animate-fade-in-up">
        <div className="flex items-center gap-2 text-base font-semibold mb-3">
          <TrendingUp size={18} className="text-primary" />
          练习统计
        </div>
        <div className="grid grid-cols-2 gap-3 mb-3">
          <Card hoverLift>
            <CardContent className="p-4 text-center">
              <div className="text-[28px] sig-stat text-primary">{stats.total_sessions}</div>
              <div className="text-xs text-dim mt-1">总练习次数</div>
            </CardContent>
          </Card>
          <Card hoverLift>
            <CardContent className="p-4 text-center">
              <div className="text-[32px] sig-stat text-green">{stats.avg_score || "-"}</div>
              <div className="text-xs text-dim mt-1">综合平均分</div>
            </CardContent>
          </Card>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Card hoverLift className="border-l-[3px] border-l-primary">
            <CardContent className="p-3.5">
              <div className="text-[13px] font-semibold text-primary mb-2.5">简历面试</div>
              <div className="flex gap-3">
                <div className="flex-1 text-center">
                  <div className="text-[22px] sig-stat text-primary">{stats.resume_sessions || 0}</div>
                  <div className="text-[11px] text-dim mt-0.5">次数</div>
                </div>
                <div className="flex-1 text-center">
                  <div className="text-[22px] sig-stat text-primary">{stats.resume_avg_score ?? "-"}</div>
                  <div className="text-[11px] text-dim mt-0.5">平均分</div>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card hoverLift className="border-l-[3px] border-l-green">
            <CardContent className="p-3.5">
              <div className="text-[13px] font-semibold text-green mb-2.5">专项训练</div>
              <div className="flex gap-3">
                <div className="flex-1 text-center">
                  <div className="text-[22px] sig-stat text-green">{stats.drill_sessions || 0}</div>
                  <div className="text-[11px] text-dim mt-0.5">次数</div>
                </div>
                <div className="flex-1 text-center">
                  <div className="text-[22px] sig-stat text-green">{stats.drill_avg_score ?? "-"}</div>
                  <div className="text-[11px] text-dim mt-0.5">平均分</div>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card hoverLift className="border-l-[3px] border-l-tertiary">
            <CardContent className="p-3.5">
              <div className="text-[13px] font-semibold text-tertiary mb-2.5">JD 备面</div>
              <div className="flex gap-3">
                <div className="flex-1 text-center">
                  <div className="text-[22px] sig-stat text-tertiary">{stats.job_prep_sessions || 0}</div>
                  <div className="text-[11px] text-dim mt-0.5">次数</div>
                </div>
                <div className="flex-1 text-center">
                  <div className="text-[22px] sig-stat text-tertiary">{stats.job_prep_avg_score ?? "-"}</div>
                  <div className="text-[11px] text-dim mt-0.5">平均分</div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {(stats.score_history || []).length >= 2 && (
        <div className="mb-7 animate-fade-in-up [animation-delay:0.1s]">
          <div className="flex items-center gap-2 text-base font-semibold mb-3">
            <TrendingUp size={18} className="text-primary" />
            成长趋势
          </div>
          <Card variant="tech">
            <CardContent className="p-4 md:p-6">
              <ScoreTrendChart history={stats.score_history} />
            </CardContent>
          </Card>
        </div>
      )}

      {(stats.score_history || []).some((h: any) => h.dimension_scores) && (
        <div className="mb-7 animate-fade-in-up [animation-delay:0.11s]">
          <div className="flex items-center gap-2 text-base font-semibold mb-3">
            <BarChart3 size={18} className="text-primary" />
            维度评分趋势
          </div>
          <Card variant="tech">
            <CardContent className="p-4 md:p-6">
              <DimensionTrendChart history={stats.score_history} />
            </CardContent>
          </Card>
        </div>
      )}

      {(stats.score_history || []).length >= 2 && (
        <div className="mb-7 animate-fade-in-up [animation-delay:0.12s]">
          <div className="flex items-center gap-2 text-base font-semibold mb-3">
            <BarChart3 size={18} className="text-primary" />
            学习热力图
          </div>
          <Card variant="tech">
            <CardContent className="p-4 md:p-6">
              <LearningHeatmap history={stats.score_history} />
            </CardContent>
          </Card>
        </div>
      )}

      {Object.keys(profile!.topic_mastery || {}).length > 0 && (
        <div className="mb-7 animate-fade-in-up [animation-delay:0.15s]">
          <div className="flex items-center gap-2 text-base font-semibold mb-3">
            <Target size={18} className="text-primary" />
            领域掌握度
          </div>
          <Card variant="tech" className="mb-4">
            <CardContent className="p-4 md:p-6">
              <TopicRadarChart mastery={profile!.topic_mastery} previousMastery={profile!.previous_topic_mastery} />
            </CardContent>
          </Card>
          {Object.keys(profile!.topic_mastery || {}).length >= 2 && (
            <Card variant="tech" className="mb-4">
              <CardContent className="p-4 md:p-6">
                <div className="text-sm font-medium text-dim mb-2">知识掌握全景</div>
                <KnowledgeTreemap mastery={profile!.topic_mastery} />
              </CardContent>
            </Card>
          )}
          <div className="grid grid-cols-1 md:grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-2.5 stagger-children">
            {Object.entries(profile!.topic_mastery!).map(([topic, data]) => (
              <Card
                key={topic}
                hoverLift
                className="cursor-pointer transition-all"
                onClick={() => navigate(`/profile/topic/${topic}`)}
              >
                <CardContent className="p-4">
                  <div className="flex justify-between items-center mb-1.5">
                    <span className="text-sm font-medium">{profile!.topic_names?.[topic] ?? topic}</span>
                    <span className="text-xs text-dim flex items-center gap-0.5">
                      <span className="font-semibold text-primary">{data.score ?? (data.level ? data.level * 20 : 0)}</span>/100 <ChevronRight size={14} />
                    </span>
                  </div>
                  <div className="sig-progress">
                    <div className="sig-progress-fill" style={{ width: `${data.score ?? (data.level ? data.level * 20 : 0)}%` }} />
                  </div>
                  {data.notes && <div className="text-xs text-dim mt-1.5">{data.notes}</div>}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {(weakActive.length > 0 || (profile!.strong_points || []).length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-7 animate-fade-in-up [animation-delay:0.2s]">
          {weakActive.length > 0 && (
            <div>
              <div className="flex items-center gap-2 text-base font-semibold mb-3">
                待改进 <Badge variant="destructive">{weakActive.length}</Badge>
              </div>
              <CollapsibleList items={weakActive} limit={3} renderItem={(w, i) => (
                <Card key={i}>
                  <CardContent className="p-3.5 flex justify-between items-start gap-3">
                    <div className="flex-1 min-w-0">
                      <span className="text-sm block mb-1">{w.point}</span>
                      <div className="flex items-center gap-2 text-xs text-dim">
                        {w.topic && <Badge variant="default">{profile!.topic_names?.[w.topic] ?? w.topic}</Badge>}
                        <span>出现 {w.times_seen} 次</span>
                      </div>
                    </div>
                    {w.topic && (
                      <Link to={`/knowledge-training?topic=${encodeURIComponent(w.topic)}`}>
                        <Button variant="outline" size="sm" className="shrink-0 h-8 text-xs">
                          <Brain size={12} /> 去训练
                        </Button>
                      </Link>
                    )}
                  </CardContent>
                </Card>
              )} />
            </div>
          )}
          {(profile!.strong_points || []).length > 0 && (
            <div>
              <div className="text-base font-semibold mb-3">强项</div>
              <CollapsibleList items={profile!.strong_points!} limit={3} renderItem={(s, i) => (
                <Card key={i} className="border-l-[3px] border-l-green">
                  <CardContent className="p-3.5 flex justify-between items-center">
                    <span className="text-sm">{s.point}</span>
                    {s.topic && <Badge variant="default">{profile!.topic_names?.[s.topic] ?? s.topic}</Badge>}
                  </CardContent>
                </Card>
              )} />
            </div>
          )}
        </div>
      )}

      {weakImproved.length > 0 && (
        <div className="mb-7 animate-fade-in-up [animation-delay:0.25s]">
          <div className="flex items-center gap-2 text-base font-semibold mb-3">
            已改善 <Badge variant="success">{weakImproved.length}</Badge>
          </div>
          <div className="flex flex-col gap-2">
            {weakImproved.map((w, i) => (
              <Card key={i} className="opacity-70">
                <CardContent className="p-3.5 flex justify-between items-center">
                  <span className="flex-1 line-through text-sm">{w.point}</span>
                  <Badge variant="success">已改善</Badge>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {((profile!.thinking_patterns?.strengths || []).length > 0 ||
        (profile!.thinking_patterns?.gaps || []).length > 0) && (
        <div className="mb-7 animate-fade-in-up [animation-delay:0.3s]">
          <div className="text-base font-semibold mb-3">思维模式</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {(profile!.thinking_patterns!.strengths || []).length > 0 && (
              <div>
                <div className="text-xs font-semibold text-green mb-1.5">优势</div>
                <CollapsibleList items={profile!.thinking_patterns!.strengths!} limit={3} renderItem={(s, i) => (
                  <div key={i} className="px-3 py-2 rounded-lg text-sm bg-green/8 border-l-[3px] border-l-green">{s}</div>
                )} />
              </div>
            )}
            {(profile!.thinking_patterns!.gaps || []).length > 0 && (
              <div>
                <div className="text-xs font-semibold text-red mb-1.5">短板</div>
                <CollapsibleList items={profile!.thinking_patterns!.gaps!} limit={3} renderItem={(g, i) => (
                  <div key={i} className="px-3 py-2 rounded-lg text-sm bg-red/8 border-l-[3px] border-l-red">{g}</div>
                )} />
              </div>
            )}
          </div>
        </div>
      )}

      {profile!.communication?.style && (
        <div className="mb-7 animate-fade-in-up [animation-delay:0.35s]">
          <div className="text-base font-semibold mb-3">沟通风格分析</div>
          <Card>
            <CardContent className="p-4 text-sm leading-[1.8]">
              <div>{profile!.communication!.style}</div>
              {(profile!.communication!.habits || []).length > 0 && (
                <div className="mt-3">
                  <strong className="text-[13px]">习惯</strong>
                  <ul className="mt-1.5 pl-4.5 leading-[1.8]">
                    {profile!.communication!.habits!.map((h, i) => <li key={i}>{h}</li>)}
                  </ul>
                </div>
              )}
              {(profile!.communication!.suggestions || []).length > 0 && (
                <div className="mt-3">
                  <strong className="text-[13px]">建议</strong>
                  <ul className="mt-1.5 pl-4.5 leading-[1.8]">
                    {profile!.communication!.suggestions!.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
