import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Markdown } from "../components/ChatBubble";
import { ArrowLeft } from "lucide-react";
import { getTopicIcon } from "../utils/topicIcons";
import { getProfile, getTopics, getTopicRetrospective, getTopicHistory } from "../api/interview";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Profile as ProfileType } from "../types/api";

export default function TopicDetail() {
  const { topic } = useParams();
  const navigate = useNavigate();

  const [profile, setProfile] = useState<ProfileType | null>(null);
  const [topicInfo, setTopicInfo] = useState<{ name: string; icon?: string } | null>(null);
  const [sessions, setSessions] = useState<any[]>([]);
  const [retrospective, setRetrospective] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [retroProgress, setRetroProgress] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getProfile(), getTopics(), getTopicHistory(topic)])
      .then(([prof, topics, hist]) => {
        setProfile(prof);
        setTopicInfo(topics[topic] || { name: topic, icon: "" });
        setSessions(hist);
        const cached = prof?.topic_mastery?.[topic]?.retrospective;
        if (cached) setRetrospective(cached);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [topic]);

  const handleGenerate = async () => {
    setGenerating(true);
    setRetroProgress("");
    try {
      const res = await getTopicRetrospective(topic, { onProgress: (msg) => setRetroProgress(msg) });
      setRetrospective(res.retrospective);
    } catch (err: any) {
      alert("生成失败: " + err.message);
    } finally {
      setGenerating(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full space-y-4">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-20" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  const mastery = profile?.topic_mastery?.[topic] || {};
  const masteryScore = mastery.score ?? (mastery.level ? mastery.level * 20 : 0);

  return (
    <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full">
      <button
        className="text-sm text-dim hover:text-text cursor-pointer mb-4 inline-flex items-center gap-1 transition-colors"
        onClick={() => navigate("/profile")}
      >
        <ArrowLeft size={16} /> 返回画像
      </button>

      <div className="flex items-center gap-3 md:gap-4 mb-8 animate-fade-in">
        <div className="text-[color:var(--sig-accent)]">{getTopicIcon(topicInfo?.icon, 36)}</div>
        <div className="flex-1">
          <h1 className="sig-display text-2xl md:text-[28px]">{topicInfo?.name || topic}</h1>
          <div className="sig-num text-sm text-dim mt-1.5">
            {sessions.length} 次训练记录
            {mastery.last_assessed && ` · 上次评估 ${mastery.last_assessed.slice(0, 10)}`}
          </div>
        </div>
      </div>

      {(masteryScore > 0) && (
        <Card className="mb-6 animate-fade-in-up">
          <CardContent className="p-4 md:p-5 flex items-center gap-3 md:gap-4">
            <div>
              <span className="sig-stat text-[32px] text-[color:var(--sig-accent)]">{masteryScore}</span>
              <span className="text-base text-dim">/100</span>
            </div>
            <div className="sig-progress flex-1">
              <div className="sig-progress-fill" style={{ width: `${masteryScore}%` }} />
            </div>
            {mastery.notes && <div className="text-[13px] text-dim ml-2 md:ml-4 max-w-[200px] hidden md:block">{mastery.notes}</div>}
          </CardContent>
        </Card>
      )}

      <div className="mb-7 animate-fade-in-up [animation-delay:0.1s]">
        <div className="text-base font-semibold mb-3 flex items-center justify-between">
          <span>领域回顾</span>
          {retrospective && (
            <Button variant="outline" size="sm" onClick={handleGenerate} disabled={generating}>
              {generating ? "生成中..." : "刷新回顾"}
            </Button>
          )}
        </div>

        {retrospective ? (
          <Card>
            <CardContent className="p-5 md:p-6 leading-[1.8] text-[15px]">
              <div className="md-content">
                <Markdown>{retrospective}</Markdown>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-5 md:p-10 text-center text-dim">
              <p>{sessions.length === 0 ? "该领域暂无训练记录" : "还没有生成领域回顾"}</p>
              {sessions.length > 0 && (
                <Button variant="default" className="mt-4" onClick={handleGenerate} disabled={generating}>
                  {generating ? (retroProgress || "正在分析历史记录...") : "生成领域回顾"}
                </Button>
              )}
            </CardContent>
          </Card>
        )}
      </div>

      <div className="mb-7 animate-fade-in-up [animation-delay:0.15s]">
        <div className="text-base font-semibold mb-3">训练历史</div>
        {sessions.length === 0 ? (
          <Card>
            <CardContent className="p-5 md:p-10 text-center text-dim">该领域暂无训练记录</CardContent>
          </Card>
        ) : (
          <div className="flex flex-col gap-2 stagger-children">
            {[...sessions].reverse().map((s) => {
              const scores = s.scores || [];
              const validScores = scores.map((sc: any) => sc.score).filter((v: any) => typeof v === "number");
              const avg = validScores.length ? (validScores.reduce((a: number, b: number) => a + b, 0) / validScores.length).toFixed(1) : null;

              return (
                <Card
                  key={s.session_id}
                  hoverLift
                  className="cursor-pointer"
                  onClick={() => navigate(`/review/${s.session_id}`)}
                >
                  <CardContent className="p-3.5 md:p-4 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-medium">{s.created_at?.slice(0, 10)}</span>
                      {avg && <Badge variant="outline" style={{ color: getScoreColor(Number(avg)), borderColor: "transparent", background: getScoreBg(Number(avg)) }}>{avg}/10</Badge>}
                    </div>
                    <span className="text-xs text-dim">#{s.session_id}</span>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function getScoreColor(score: number): string {
  if (score >= 8) return "var(--green)";
  if (score >= 6) return "var(--ai-glow)";
  if (score >= 4) return "#e2b93b";
  return "var(--red)";
}

function getScoreBg(score: number): string {
  if (score >= 8) return "rgba(34,197,94,0.15)";
  if (score >= 6) return "rgba(245,158,11,0.15)";
  if (score >= 4) return "rgba(253,203,110,0.2)";
  return "rgba(239,68,68,0.15)";
}
