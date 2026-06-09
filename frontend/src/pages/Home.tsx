import React, { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, ChevronRight, Sparkles, Target, Mic, TrendingUp, Zap, BriefcaseBusiness, AlertCircle, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import TopicCard from "../components/TopicCard";
import { getTopics, startInterview, startInterviewStream, getResumeStatus, uploadResume, getProfile, getDueReviews } from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineTimeline, applyStageEvent, type PipelineStages } from "@/components/PipelineTimeline";
import { RAGMetricsPanel } from "@/components/RAGMetricsPanel";
import type { Question, Profile, DueReview, TopicInfo } from "../types/api";

interface ModeCard {
  mode: string;
  icon: React.ComponentType<any>;
  title: string;
  desc: string;
  tag: string;
}

const MODE_CARDS: ModeCard[] = [
  {
    mode: "resume",
    icon: FileText,
    title: "实战模拟场",
    desc: "AI 读取你的简历，模拟真实面试官。从自我介绍到项目深挖，完整走一遍面试流程。",
    tag: "沉浸体验",
  },
  {
    mode: "topic_drill",
    icon: Target,
    title: "弱点狙击站",
    desc: "选一个领域集中训练，AI 根据你的回答动态调整难度，精准定位薄弱点。",
    tag: "精准打击",
  },
  {
    mode: "job_prep",
    icon: BriefcaseBusiness,
    title: "岗位特训营",
    desc: "贴入岗位 JD，AI 拆解岗位重点，结合简历生成高概率问题和岗位匹配复盘。",
    tag: "定向突破",
  },
  {
    mode: "recording",
    icon: Mic,
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
  const [pipelineStages, setPipelineStages] = useState<PipelineStages>({});
  const [readyToStart, setReadyToStart] = useState<{
    session_id: string;
    mode: string;
    topic: string;
    questions: Question[];
  } | null>(null);
  const streamedQuestionsRef = useRef<Question[]>([]);
  const pipelineStagesRef = useRef<PipelineStages>({});

  const resetStreaming = () => {
    setReadyToStart(null);
    setPipelineStages({});
    setStreamingQuestions([]);
    pipelineStagesRef.current = {};
    streamedQuestionsRef.current = [];
  };

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
      setPipelineStages({});
      pipelineStagesRef.current = {};
      try {
        await startInterviewStream(mode, selectedTopic, {
          onQuestion: (q: Question) => {
            streamedQuestionsRef.current = [...streamedQuestionsRef.current, q];
            setStreamingQuestions((prev) => [...prev, q]);
          },
          onQuestionUpdate: (q: Question) => {
            // Validator repair / difficulty calibration replaces by id rather
            // than appends. Without this branch the user would see duplicate
            // cards (one repaired, one original).
            const updateById = (list: Question[]) =>
              list.map((existing) => (existing.id === q.id ? { ...existing, ...q } : existing));
            streamedQuestionsRef.current = updateById(streamedQuestionsRef.current);
            setStreamingQuestions((prev) => updateById(prev));
          },
          onStage: (evt) => {
            setPipelineStages((prev) => {
              const next = applyStageEvent(prev, evt);
              pipelineStagesRef.current = next;
              return next;
            });
          },
          onDone: (event: any) => {
            setIsStreaming(false);
            setLoading(false);
            // Stash the timing breakdown so the Interview page (or future
            // analytics) can read the last run without re-fetching.
            try {
              sessionStorage.setItem(
                `drill:lastPipeline:${event.session_id}`,
                JSON.stringify({
                  topic: event.topic,
                  stages: pipelineStagesRef.current,
                  finishedAt: Date.now(),
                }),
              );
            } catch {
              // sessionStorage may be unavailable in private mode — ignore.
            }
            setReadyToStart({
              session_id: event.session_id,
              mode: event.mode,
              topic: event.topic,
              questions: streamedQuestionsRef.current,
            });
          },
          onError: (msg: string) => {
            setIsStreaming(false);
            setLoading(false);
            setReadyToStart(null);
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
      <Card hoverLift className="w-full max-w-[700px] mb-10">
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
                    <div className="sig-progress flex-1">
                      <div className="sig-progress-fill" style={{ width: `${d.score || 0}%` }} />
                    </div>
                    <span className="text-[11px] text-dim w-7 text-right sig-num">{d.score || 0}</span>
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
      <div className="text-center mb-10 md:mb-12">
        <div className="sig-kicker mb-3 inline-block">// AI-POWERED MOCK INTERVIEW</div>
        <h1 className="sig-display text-3xl md:text-[44px] mb-3">
          SparkOffer<span className="sig-accent-c">.</span>
        </h1>
        <p className="text-base text-dim max-w-[500px] mx-auto">
          越练越懂你的 AI 面试教练——追踪你的成长轨迹，精准命中薄弱点
        </p>
      </div>

      {/* Mode cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 md:gap-6 mb-10 md:mb-12 w-full max-w-[1320px] stagger-scale">
        {MODE_CARDS.map((card) => {
          const Icon = card.icon;
          const isActive = mode === card.mode;
          return (
            <div
              key={card.mode}
              data-spotlight={isActive ? undefined : ""}
              style={isActive ? { borderColor: "var(--sig-accent)", background: "color-mix(in srgb, var(--sig-accent) 6%, transparent)" } : undefined}
              className={cn(
                "sig-card w-full relative overflow-hidden cursor-pointer text-left active:scale-[0.97] group",
                "transition-[transform,border-color,background-color] duration-300 ease-[cubic-bezier(0.2,0,0,1)]",
                isActive ? "" : "sig-hover-lift spotlight"
              )}
              onClick={() => {
                const switching = mode !== card.mode;
                setMode(card.mode);
                if (card.mode !== "topic_drill") setSelectedTopic(null);
                if (switching) resetStreaming();
              }}
            >
              {/* L1 — Eyebrow 眼眉色条 */}
              <div className="relative px-6 pt-6 pb-0">
                <div className="flex items-center gap-2 mb-3">
                  <div className={cn("w-1 h-4 rounded-full transition-opacity", isActive ? "opacity-90" : "opacity-40")} style={{ background: "var(--sig-accent)" }} />
                  <span className="sig-kicker" style={{ opacity: isActive ? 0.9 : 0.55 }}>
                    {card.tag}
                  </span>
                </div>
              </div>

              {/* L2 — 图标 + L3 标题 */}
              <div className="relative px-6 pt-1 pb-4">
                <div className="flex items-center gap-3 mb-3">
                  <div className={cn("w-11 h-11 rounded-md flex items-center justify-center transition-all duration-300 shrink-0", isActive && "scale-110")} style={{ background: "color-mix(in srgb, var(--sig-accent) 12%, transparent)", color: "var(--sig-accent)" }}>
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
            </div>
          );
        })}
      </div>

      {/* Due review reminder — 编号列表化 */}
      {!mode && dueReviews.length > 0 && (
        <Card hoverLift className="w-full max-w-[700px] mb-6 animate-fade-in relative overflow-hidden" style={{ borderColor: "color-mix(in srgb, var(--sig-warning) 35%, transparent)", background: "color-mix(in srgb, var(--sig-warning) 5%, transparent)" }}>
          <CardContent className="p-4 md:p-5 relative">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <AlertCircle size={18} style={{ color: "var(--sig-warning)" }} />
                <span className="text-[15px] font-semibold">
                  {dueReviews.length} 个薄弱点待复习
                </span>
              </div>
              <button
                className="text-[13px] font-medium px-3 py-1.5 rounded-md transition-all flex items-center gap-1 cursor-pointer hover:-translate-y-px"
                style={{ background: "color-mix(in srgb, var(--sig-warning) 15%, transparent)", color: "var(--sig-warning)" }}
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
                <div key={i} className="flex items-start gap-2.5 text-[13px] animate-fade-in-up" style={{ animationDelay: `${0.05 * i}s`, color: "color-mix(in srgb, var(--sig-warning) 85%, var(--sig-fg))" }}>
                  <span className="sig-num font-semibold text-[11px] shrink-0 mt-0.5 w-5 text-right" style={{ color: "color-mix(in srgb, var(--sig-warning) 55%, transparent)" }}>
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="flex-1 leading-relaxed">
                    {wp.point}
                    {wp.topic && <span className="ml-2 text-[11px] text-dim">({wp.topic})</span>}
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
                onClick={() => {
                  if (selectedTopic !== key) resetStreaming();
                  setSelectedTopic(key);
                }}
              />
            ))}
          </div>
        </div>
      )}

      {/* Streaming progress */}
      {isStreaming && (
        <Card className="w-full max-w-[700px] mb-6 animate-fade-in">
          <CardContent className="p-4 md:p-5 space-y-3">
            <div className="flex items-center gap-2">
              <div className="flex gap-1.5">
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse [animation-delay:0.2s]" />
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse [animation-delay:0.4s]" />
              </div>
              <span className="text-sm font-medium">
                AI 正在出题... ({streamingQuestions.length}/10)
              </span>
            </div>

            <PipelineTimeline stages={pipelineStages} />
            {pipelineStages.retrieve?.rag_metrics && (
              <RAGMetricsPanel metrics={pipelineStages.retrieve.rag_metrics} />
            )}

            {streamingQuestions.length > 0 && (
              <>
                <div className="sig-progress">
                  <div className="sig-progress-fill" style={{ width: `${(streamingQuestions.length / 10) * 100}%` }} />
                </div>
                <div className="flex flex-col gap-1.5">
                  {streamingQuestions.map((q) => (
                    <div key={q.id} className="flex items-center gap-2 text-[13px] text-dim animate-fade-in">
                      <Badge variant="outline" className="text-xs shrink-0">Q{q.id}</Badge>
                      <span className="truncate">{q.question}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}

      {/* Ready to start — 出题完成后等用户确认 */}
      {readyToStart && (
        <Card className="w-full max-w-[700px] mb-6 animate-fade-in">
          <CardContent className="p-4 md:p-5 space-y-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 size={18} style={{ color: "var(--sig-success)" }} />
              <span className="text-sm font-medium">
                出题完成 · {readyToStart.questions.length} 道题已准备就绪
              </span>
            </div>

            <PipelineTimeline stages={pipelineStages} />
            {pipelineStages.retrieve?.rag_metrics && (
              <RAGMetricsPanel metrics={pipelineStages.retrieve.rag_metrics} />
            )}

            <div className="flex items-center gap-2 text-[12px] text-dim flex-wrap">
              <span className="mr-1">难度分布：</span>
              {(() => {
                const dist = readyToStart.questions.reduce(
                  (acc, q) => {
                    const d = q.difficulty ?? 3;
                    if (d <= 2) acc.easy++;
                    else if (d === 3) acc.medium++;
                    else acc.hard++;
                    return acc;
                  },
                  { easy: 0, medium: 0, hard: 0 },
                );
                return (
                  <>
                    <Badge variant="outline" className="font-mono">简单 {dist.easy}</Badge>
                    <Badge variant="outline" className="font-mono">中等 {dist.medium}</Badge>
                    <Badge variant="outline" className="font-mono">困难 {dist.hard}</Badge>
                  </>
                );
              })()}
            </div>

            <Button
              variant="cta"
              size="lg"
              className="w-full"
              onClick={() => {
                const payload = readyToStart;
                setReadyToStart(null);
                navigate(`/interview/${payload.session_id}`, {
                  state: {
                    session_id: payload.session_id,
                    mode: payload.mode,
                    topic: payload.topic,
                    questions: payload.questions,
                  },
                });
              }}
            >
              开始答题 →
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Start button */}
      {mode && !readyToStart && (
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
