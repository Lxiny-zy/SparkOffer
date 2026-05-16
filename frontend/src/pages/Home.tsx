import React, { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, ChevronRight, Sparkles, Target, Mic, TrendingUp, Zap, BriefcaseBusiness, AlertCircle } from "lucide-react";
import { toast } from "sonner";
import TopicCard from "../components/TopicCard";
import { getTopics, startInterview, startInterviewStream, getResumeStatus, uploadResume, getProfile, getDueReviews } from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { Question, Profile, DueReview, TopicInfo } from "../types/api";

interface ModeCard {
  mode: string;
  icon: React.ComponentType<any>;
  gradient: string;
  iconBg: string;
  borderActive: string;
  badgeVariant: string;
  title: string;
  desc: string;
  tag: string;
}

const MODE_CARDS: ModeCard[] = [
  {
    mode: "resume",
    icon: FileText,
    gradient: "from-primary/15 via-primary/5 to-transparent",
    iconBg: "bg-primary/12 text-primary",
    borderActive: "border-primary/50",
    badgeVariant: "default",
    title: "实战模拟场",
    desc: "AI 读取你的简历，模拟真实面试官。从自我介绍到项目深挖，完整走一遍面试流程。",
    tag: "沉浸体验",
  },
  {
    mode: "topic_drill",
    icon: Target,
    gradient: "from-green/15 via-green/5 to-transparent",
    iconBg: "bg-green/12 text-green",
    borderActive: "border-green/50",
    badgeVariant: "success",
    title: "弱点狙击站",
    desc: "选一个领域集中训练，AI 根据你的回答动态调整难度，精准定位薄弱点。",
    tag: "精准打击",
  },
  {
    mode: "job_prep",
    icon: BriefcaseBusiness,
    gradient: "from-tertiary/15 via-tertiary/5 to-transparent",
    iconBg: "bg-tertiary/12 text-tertiary",
    borderActive: "border-tertiary/50",
    badgeVariant: "secondary",
    title: "岗位特训营",
    desc: "贴入岗位 JD，AI 拆解岗位重点，结合简历生成高概率问题和岗位匹配复盘。",
    tag: "定向突破",
  },
  {
    mode: "recording",
    icon: Mic,
    gradient: "from-info/15 via-info/5 to-transparent",
    iconBg: "bg-info/12 text-info",
    borderActive: "border-info/50",
    badgeVariant: "blue",
    title: "回放实验室",
    desc: "上传面试录音或粘贴文字，AI 自动转写分析，帮你复盘每一场真实面试。",
    tag: "复盘利器",
  },
];

export default function Home() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<string | null>(null);
  const [topics, setTopics] = useState<Record<string, TopicInfo>>({});
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [startProgress, setStartProgress] = useState("");
  const [resumeFile, setResumeFile] = useState<{ filename: string; size: number } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [dueReviews, setDueReviews] = useState<DueReview[]>([]);
  const [pageLoading, setPageLoading] = useState(true);
  const [streamingQuestions, setStreamingQuestions] = useState<Question[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const streamedQuestionsRef = useRef<Question[]>([]);

  useEffect(() => {
    Promise.all([
      getTopics().catch(() => ({})),
      getResumeStatus().catch(() => ({})),
      getProfile().catch(() => null),
      getDueReviews().catch(() => []),
    ]).then(([t, s, p, dr]: any[]) => {
      setTopics(t);
      if (s.has_resume) setResumeFile({ filename: s.filename, size: s.size });
      setProfile(p);
      setDueReviews(Array.isArray(dr) ? dr : []);
    }).finally(() => setPageLoading(false));
  }, []);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const data = await uploadResume(file);
      setResumeFile({ filename: data.filename, size: data.size });
      toast.success("简历上传成功");
    } catch (err: any) {
      toast.error("上传失败: " + err.message);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleStart = async () => {
    if (!mode) return;
    if (mode === "job_prep") { navigate("/job-prep"); return; }
    if (mode === "recording") { navigate("/recording"); return; }
    if (mode === "topic_drill" && !selectedTopic) return;

    if (mode === "topic_drill") {
      setLoading(true);
      setIsStreaming(true);
      streamedQuestionsRef.current = [];
      setStreamingQuestions([]);
      try {
        await startInterviewStream(mode, selectedTopic, {
          onQuestion: (q: Question) => {
            streamedQuestionsRef.current = [...streamedQuestionsRef.current, q];
            setStreamingQuestions((prev) => [...prev, q]);
          },
          onDone: (event: any) => {
            setIsStreaming(false);
            setLoading(false);
            navigate(`/interview/${event.session_id}`, {
              state: {
                session_id: event.session_id,
                mode: event.mode,
                topic: event.topic,
                questions: streamedQuestionsRef.current,
              },
            });
          },
          onError: (msg: string) => {
            setIsStreaming(false);
            setLoading(false);
            toast.error("出题失败: " + msg);
          },
        });
      } catch (err: any) {
        setIsStreaming(false);
        setLoading(false);
        toast.error("启动失败: " + err.message);
      }
      return;
    }

    setLoading(true);
    setStartProgress("");
    try {
      const data = await startInterview(mode, selectedTopic, {
        onProgress: (msg) => setStartProgress(msg),
      });
      navigate(`/interview/${data.session_id}`, { state: data });
    } catch (err: any) {
      toast.error("启动失败: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  const canStart = (mode === "resume" && resumeFile) || (mode === "topic_drill" && selectedTopic) || mode === "recording" || mode === "job_prep";

  function renderStats() {
    if (pageLoading) {
      return (
        <div className="w-full max-w-[700px] mb-10">
          <div className="bg-card border border-border rounded-xl p-5 space-y-3">
            <Skeleton className="h-5 w-24" />
            <div className="flex gap-6">
              <Skeleton className="h-12 w-20" />
              <Skeleton className="h-12 w-20" />
              <Skeleton className="h-12 flex-1" />
            </div>
          </div>
        </div>
      );
    }
    if (!profile?.stats?.total_sessions || mode) return null;
    const s = profile.stats;
    const lastEntry = (s.score_history || []).slice(-1)[0];
    const mastery = profile.topic_mastery || {};
    const topTopics = Object.entries(mastery)
      .sort((a, b) => (b[1].score || 0) - (a[1].score || 0))
      .slice(0, 3);
    return (
      <Card className="w-full max-w-[700px] mb-10 stat-card-gradient card-hover-lift">
        <CardContent className="p-5 md:p-6">
          <div className="flex justify-between items-center mb-4">
            <div className="flex items-center gap-2">
              <TrendingUp size={18} className="text-primary" />
              <span className="text-[15px] font-semibold">训练概览</span>
            </div>
            <button
              className="text-[13px] text-primary flex items-center gap-1 hover:underline cursor-pointer"
              onClick={() => navigate("/profile")}
            >
              查看画像 <ChevronRight size={14} />
            </button>
          </div>
          <div className="flex flex-wrap gap-4 md:gap-6">
            <StatBox value={s.total_sessions} label="总练习" color="text-primary" />
            <StatBox value={s.avg_score || "-"} label="综合平均" color="text-green" />
            {topTopics.length > 0 && (
              <div className="flex-1 min-w-[120px]">
                <div className="text-[11px] text-dim mb-2">领域掌握</div>
                {topTopics.map(([t, d]) => (
                  <div key={t} className="flex items-center gap-2 mb-1.5">
                    <span className="text-xs w-[70px] text-text truncate">{t}</span>
                    <div className="flex-1 h-2 rounded-full bg-border overflow-hidden relative">
                      <div
                        className="h-full rounded-full transition-all duration-700 relative overflow-hidden"
                        style={{
                          width: `${d.score || 0}%`,
                          background: "linear-gradient(90deg, var(--aurora-1), var(--aurora-2))",
                          boxShadow: "0 0 8px var(--tech-glow)",
                        }}
                      >
                        <div className="absolute inset-0 opacity-50" style={{ background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.4), transparent)", backgroundSize: "200% 100%", animation: "text-shimmer 2s ease-in-out infinite" }} />
                      </div>
                    </div>
                    <span className="text-[11px] text-dim w-7 text-right">{d.score || 0}</span>
                  </div>
                ))}
              </div>
            )}
            {lastEntry && (
              <StatBox
                value={lastEntry.avg_score}
                label="上次得分"
                color={lastEntry.avg_score >= 6 ? "text-green" : "text-orange"}
              />
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto min-h-0 flex flex-col items-center px-4 pt-8 pb-10 md:px-6 md:pt-12">
      {/* Hero */}
      <div className="text-center mb-10 md:mb-12 relative">
        <div className="absolute -top-32 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-aurora rounded-full blur-3xl pointer-events-none opacity-50 morph-blob" />
        <div className="relative">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full glass-subtle text-xs font-medium mb-4 badge-pulse" style={{ color: "var(--aurora-2)" }}>
            <Sparkles size={14} className="animate-float" />
            AI-Powered Mock Interview
          </div>
          <h1 className="text-3xl md:text-[44px] font-display font-bold mb-3 aurora-text">
            SparkOffer
          </h1>
          <p className="text-base text-dim max-w-[500px]">
            越练越懂你的 AI 面试教练——追踪你的成长轨迹，精准命中薄弱点
          </p>
        </div>
      </div>

      {/* Mode cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 md:gap-6 mb-10 md:mb-12 w-full max-w-[1320px] stagger-scale">
        {MODE_CARDS.map((card) => {
          const Icon = card.icon;
          const isActive = mode === card.mode;
          return (
            <div
              key={card.mode}
              className={cn(
                "w-full relative overflow-hidden cursor-pointer text-left rounded-3xl active:scale-[0.97] group",
                "transition-[transform,border-color,box-shadow,background-color] duration-300 ease-[cubic-bezier(0.2,0,0,1)]",
                isActive
                  ? "glass-strong animated-border shadow-[0_8px_32px_var(--tech-glow)]"
                  : "bg-card border border-border/60 hover:border-primary/30 hover:shadow-[0_4px_16px_var(--glow-primary)]"
              )}
              onClick={() => { setMode(card.mode); if (card.mode !== "topic_drill") setSelectedTopic(null); }}
            >
              {/* L1 — Eyebrow 眼眉色条 */}
              <div className="relative px-6 pt-6 pb-0">
                <div className="flex items-center gap-2 mb-3">
                  <div className={cn("w-1 h-4 rounded-full transition-opacity", isActive ? "opacity-90" : "opacity-40")} style={{ background: "currentColor" }} />
                  <span className={cn("text-[10px] font-mono uppercase tracking-[0.18em] transition-opacity", isActive ? "opacity-80" : "opacity-50")} style={{ color: "currentColor" }}>
                    {card.tag}
                  </span>
                </div>
              </div>

              {/* L2 — 图标 + L3 标题 */}
              <div className="relative px-6 pt-1 pb-4">
                <div className="flex items-center gap-3 mb-3">
                  <div className={cn("w-11 h-11 rounded-2xl flex items-center justify-center transition-all duration-300 shrink-0", card.iconBg, isActive && "scale-110 shadow-[0_4px_20px_var(--tech-glow)]")}>
                    <Icon size={20} />
                  </div>
                  {/* L3 — 标题 */}
                  <h3 className="text-xl font-semibold leading-tight">{card.title}</h3>
                </div>
              </div>

              {/* L4 — 描述（最底层） */}
              <div className="relative px-6 pb-6">
                <div className="text-sm text-dim leading-relaxed">{card.desc}</div>
              </div>

              {/* Corner geometric accent — 默认隐藏，hover/激活时显现 */}
              <svg
                className={cn(
                  "absolute top-3 right-3 pointer-events-none transition-opacity duration-300",
                  isActive ? "opacity-50" : "opacity-0 group-hover:opacity-40"
                )}
                width="36" height="36" viewBox="0 0 36 36" fill="none"
              >
                <circle cx="30" cy="6" r="1.5" fill="currentColor" className="text-primary" />
                <circle cx="25" cy="11" r="1" fill="currentColor" className="text-primary" />
                <circle cx="30" cy="16" r="1" fill="currentColor" className="text-primary" />
                <line x1="20" y1="6" x2="30" y2="6" stroke="currentColor" strokeOpacity="0.3" strokeWidth="0.5" className="text-primary" />
              </svg>
            </div>
          );
        })}
      </div>

      {/* Due review reminder — 编号列表化 */}
      {!mode && dueReviews.length > 0 && (
        <Card className="w-full max-w-[700px] mb-6 border-orange/30 bg-orange/5 card-hover-lift animate-fade-in relative overflow-hidden">
          <div className="absolute -top-12 -right-12 w-[200px] h-[200px] rounded-full pointer-events-none opacity-25 morph-blob" style={{ background: "radial-gradient(circle, rgba(253,203,110,0.5), transparent 70%)" }} />
          <CardContent className="p-4 md:p-5 relative">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <AlertCircle size={18} className="text-orange animate-float" />
                <span className="text-[15px] font-semibold">
                  {dueReviews.length} 个薄弱点待复习
                </span>
              </div>
              <button
                className="text-[13px] font-medium px-3 py-1.5 rounded-full bg-orange/15 text-orange hover:bg-orange/25 transition-all flex items-center gap-1 cursor-pointer hover:-translate-y-px"
                onClick={() => {
                  setMode("topic_drill");
                  const topicOfFirst = dueReviews[0]?.topic;
                  if (topicOfFirst && topics[topicOfFirst]) setSelectedTopic(topicOfFirst);
                }}
              >
                立即复习 <ChevronRight size={14} />
              </button>
            </div>
            <div className="flex flex-col gap-2">
              {dueReviews.slice(0, 6).map((wp, i) => (
                <div key={i} className="flex items-start gap-2.5 text-[13px] text-orange/90 animate-fade-in-up" style={{ animationDelay: `${0.05 * i}s` }}>
                  <span className="font-mono font-semibold text-orange/50 text-[11px] shrink-0 mt-0.5 w-5 text-right">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="flex-1 leading-relaxed">
                    {wp.point}
                    {wp.topic && <span className="ml-2 text-[11px] text-orange/40">({wp.topic})</span>}
                  </span>
                </div>
              ))}
              {dueReviews.length > 6 && (
                <div className="text-[12px] text-dim pt-1 text-center">
                  还有 {dueReviews.length - 6} 个薄弱点 →
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Quick stats */}
      {renderStats()}

      {/* Resume upload */}
      {mode === "resume" && (
        <div className="w-full max-w-[700px] mb-8 animate-fade-in">
          {resumeFile ? (
            <Card className="hover:shadow-md transition-shadow">
              <CardContent className="p-4 md:p-5 flex items-center justify-between">
                <div className="flex items-center gap-2.5 text-sm text-text">
                  <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
                    <FileText size={18} className="text-primary" />
                  </div>
                  <div>
                    <div className="font-medium">{resumeFile.filename}</div>
                    <div className="text-xs text-dim">{(resumeFile.size / 1024).toFixed(0)} KB</div>
                  </div>
                </div>
                <label className={cn("cursor-pointer", uploading && "opacity-50 pointer-events-none")}>
                  <Button variant="outline" size="sm" asChild>
                    <span>{uploading ? "上传中..." : "重新上传"}</span>
                  </Button>
                  <input type="file" accept=".pdf" className="hidden" onChange={handleUpload} disabled={uploading} />
                </label>
              </CardContent>
            </Card>
          ) : (
            <label className={cn(
              "flex flex-col items-center gap-3 px-5 py-8 bg-card border-2 border-dashed border-border rounded-xl cursor-pointer transition-all text-sm text-dim hover:border-primary/50 hover:bg-card/80",
              uploading && "opacity-50 pointer-events-none"
            )}>
              <div className="w-12 h-12 rounded-xl bg-secondary flex items-center justify-center">
                <FileText size={24} className="text-dim" />
              </div>
              <div>
                <span className="font-medium text-text">{uploading ? "正在上传..." : "点击上传简历（PDF）"}</span>
              </div>
              <input type="file" accept=".pdf" className="hidden" onChange={handleUpload} disabled={uploading} />
            </label>
          )}
        </div>
      )}

      {/* Topic selection */}
      {mode === "topic_drill" && (
        <div className="w-full max-w-[700px] animate-fade-in">
          <div className="flex items-center gap-2 mb-4">
            <Zap size={18} className="text-green" />
            <span className="text-lg font-semibold">选择训练领域</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3 mb-8 stagger-children">
            {Object.entries(topics).map(([key, info]) => (
              <TopicCard
                key={key}
                topicKey={key}
                name={info.name || key}
                icon={info.icon}
                selected={selectedTopic === key}
                onClick={() => setSelectedTopic(key)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Streaming progress */}
      {isStreaming && streamingQuestions.length > 0 && (
        <Card className="w-full max-w-[700px] mb-6 animate-fade-in gradient-border">
          <CardContent className="p-4 md:p-5">
            <div className="flex items-center gap-2 mb-3">
              <div className="flex gap-1.5">
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse [animation-delay:0.2s]" />
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse [animation-delay:0.4s]" />
              </div>
              <span className="text-sm font-medium">AI 正在出题... ({streamingQuestions.length}/10)</span>
            </div>
            <div className="h-2 rounded-full bg-border overflow-hidden mb-3 relative">
              <div
                className="h-full rounded-full transition-all duration-300 ease-out relative overflow-hidden"
                style={{
                  width: `${(streamingQuestions.length / 10) * 100}%`,
                  background: "linear-gradient(90deg, var(--aurora-1), var(--aurora-2))",
                  boxShadow: "0 0 12px var(--tech-glow)",
                }}
              >
                <div className="absolute inset-0 opacity-60" style={{ background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.5), transparent)", backgroundSize: "200% 100%", animation: "text-shimmer 1.5s linear infinite" }} />
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              {streamingQuestions.map((q) => (
                <div key={q.id} className="flex items-center gap-2 text-[13px] text-dim animate-fade-in">
                  <Badge variant="outline" className="text-xs shrink-0">Q{q.id}</Badge>
                  <span className="truncate">{q.question}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Start button */}
      {mode && (
        <div className="w-full max-w-[700px] animate-fade-in-up">
          <Button
            variant="cta"
            size="xl"
            className="w-full"
            disabled={!canStart || loading}
            onClick={handleStart}
          >
            {isStreaming ? `出题中 (${streamingQuestions.length}/10)...` : loading ? (startProgress || "正在初始化面试...") : mode === "job_prep" ? "进入 JD 备面" : "开始面试"}
          </Button>
        </div>
      )}
    </div>
  );
}

interface StatBoxProps {
  value: number | string;
  label: string;
  color: string;
}

function StatBox({ value, label, color }: StatBoxProps) {
  return (
    <div className="text-center min-w-[60px]">
      <div className={cn("text-2xl font-bold score-pop", color)}>{value}</div>
      <div className="text-[11px] text-dim mt-0.5">{label}</div>
    </div>
  );
}
