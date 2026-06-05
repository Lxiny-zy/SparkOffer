import { useParams, useLocation, useNavigate } from "react-router-dom";
import { useState, useEffect, useRef, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { markdownComponents, remarkPlugins } from "../components/ChatBubble";
import { BookOpen, BriefcaseBusiness, Sparkles, RefreshCw, Star } from "lucide-react";
import { getReview, getReferenceAnswer, addFavorite, getSessionRAGMetrics, type RAGEvalMetrics } from "../api/interview";
import { cn } from "@/lib/utils";
import { metricColorVar } from "@/lib/metrics";
import { MetricInfoTooltip } from "@/components/MetricInfoTooltip";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Question, Score, Overall } from "../types/api";

function getScoreColor(score: number): { bg: string; color: string } {
  if (score >= 8) return { bg: "rgba(56,106,32,0.15)", color: "var(--green)" };
  if (score >= 6) return { bg: "rgba(103,80,164,0.15)", color: "var(--primary)" };
  if (score >= 4) return { bg: "rgba(125,88,0,0.15)", color: "var(--warning)" };
  return { bg: "rgba(179,38,30,0.15)", color: "var(--red)" };
}

function getScoreLevel(score: number): { label: string; color: string } {
  if (score >= 9) return { label: "卓越", color: "var(--green)" };
  if (score >= 8) return { label: "优秀", color: "var(--green)" };
  if (score >= 6) return { label: "良好", color: "var(--primary)" };
  if (score >= 4) return { label: "待提升", color: "var(--warning)" };
  return { label: "需重练", color: "var(--red)" };
}

const RESUME_DIMENSION_LABELS: Record<string, string> = {
  technical_depth: "技术深度",
  project_articulation: "项目表达",
  communication: "表达能力",
  problem_solving: "问题解决",
};

const JOB_PREP_DIMENSION_LABELS: Record<string, string> = {
  role_fit: "岗位匹配",
  technical_depth: "技术深度",
  project_relevance: "项目相关性",
  engineering_quality: "工程质量",
  communication: "表达能力",
};

interface ScorePillProps {
  score: number | null | undefined;
}

function ScorePill({ score }: ScorePillProps) {
  if (score == null) return <Badge variant="secondary">--</Badge>;
  const sc = getScoreColor(score);
  return (
    <Badge variant="outline" className="min-w-[52px] justify-center font-semibold text-[13px]" style={{ background: sc.bg, borderColor: "transparent", color: sc.color }}>
      {score}/10
    </Badge>
  );
}

// ─────────────────────────────────────────────────────────────
// L1 焦点层：英雄区总览 ── 巨型分数 + 评级标签 + 概要
// ─────────────────────────────────────────────────────────────
interface HeroOverviewProps {
  eyebrow: string;          // 顶部小字（如 "OVERALL PERFORMANCE"）
  title: string;            // 主标题
  subtitle?: string;        // 副标题（说明）
  summary?: string;         // 详细概要文本
  avgScore: number | string;
  rightExtra?: ReactNode;   // 右上角扩展（如岗位匹配判断框）
  icon?: ReactNode;
  accent?: "primary" | "tertiary";
}

function HeroOverview({ eyebrow, title, subtitle, summary, avgScore, rightExtra, icon, accent = "primary" }: HeroOverviewProps) {
  const numScore = typeof avgScore === "number" ? avgScore : null;
  const level = numScore != null ? getScoreLevel(numScore) : null;
  const sc = numScore != null ? getScoreColor(numScore) : { bg: "var(--muted)", color: "var(--muted-fg)" };
  const accentVar = accent === "tertiary" ? "var(--tertiary)" : "var(--primary)";

  return (
    <Card className="mb-6 relative overflow-hidden animate-fade-in-up" hoverLift>
      {/* 装饰光斑 */}
      <div
        className="absolute -top-20 -right-20 w-[300px] h-[300px] rounded-full pointer-events-none opacity-30"
        style={{ background: `radial-gradient(circle, ${sc.bg}, transparent 70%)` }}
      />
      <CardContent className="p-6 md:p-8 relative">
        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-6 items-start">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-2">
              {icon}
              <span
                className="text-[10px] font-mono uppercase tracking-[0.18em]"
                style={{ color: accentVar, opacity: 0.7 }}
              >
                {eyebrow}
              </span>
            </div>
            <h2 className="text-2xl md:text-[26px] font-bold leading-tight mb-2">{title}</h2>
            {subtitle && (
              <p className="text-sm text-dim leading-relaxed mb-3">{subtitle}</p>
            )}
            {summary && (
              <div className="text-[15px] leading-[1.85] text-text/90 mt-3">{summary}</div>
            )}
          </div>

          {/* 右侧巨型分数 */}
          <div className="flex md:flex-col items-center md:items-end gap-3 md:gap-2 shrink-0">
            <div className="flex items-baseline gap-1">
              <span
                className="text-5xl md:text-[64px] font-bold leading-none score-pop"
                style={{ color: sc.color }}
              >
                {avgScore}
              </span>
              {numScore != null && <span className="text-base text-dim/70">/10</span>}
            </div>
            {level && (
              <span
                className="text-xs font-semibold px-3 py-1 rounded-full"
                style={{ background: sc.bg, color: sc.color }}
              >
                {level.label}
              </span>
            )}
          </div>
        </div>

        {rightExtra && <div className="mt-5">{rightExtra}</div>}
      </CardContent>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────
// 数据条带 ── 横排关键指标
// ─────────────────────────────────────────────────────────────
interface StatStripProps {
  items: { label: string; value: string | number; unit?: string; accent?: string }[];
}

function StatStrip({ items }: StatStripProps) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
      {items.map((it, i) => (
        <div
          key={i}
          className="sig-card px-4 py-3 hover:border-[color:var(--sig-line-2)] transition-colors animate-fade-in-up"
          style={{ animationDelay: `${i * 0.05}s` }}
        >
          <div className="text-[10px] uppercase tracking-widest text-dim/70 mb-1">{it.label}</div>
          <div className="flex items-baseline gap-1">
            <span
              className="text-2xl font-bold leading-none"
              style={{ color: it.accent || "var(--text)" }}
            >
              {it.value}
            </span>
            {it.unit && <span className="text-xs text-dim">{it.unit}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 时间线节点 ── 左侧分数圆 + 右侧题卡内容
// ─────────────────────────────────────────────────────────────
interface TimelineNodeProps {
  index: number;
  score: number | null | undefined;
  isSkipped?: boolean;
  isLast?: boolean;
  children: ReactNode;
}

function TimelineNode({ index, score, isSkipped, isLast, children }: TimelineNodeProps) {
  const sc = score != null && !isSkipped ? getScoreColor(score) : { bg: "var(--muted)", color: "var(--muted-fg)" };
  return (
    <div className="relative pl-14 md:pl-16 pb-6 last:pb-0">
      {/* 竖线（最后一项不画） */}
      {!isLast && (
        <div
          className="absolute left-5 md:left-6 top-12 bottom-0 w-px"
          style={{ background: "linear-gradient(to bottom, var(--border), transparent)" }}
        />
      )}
      {/* 分数圆节点 */}
      <div
        className={cn(
          "absolute left-0 top-1 w-10 h-10 md:w-12 md:h-12 rounded-full",
          "flex items-center justify-center font-bold text-base md:text-lg",
          "ring-4 ring-bg shadow-sm shrink-0",
          isSkipped && "opacity-50"
        )}
        style={{ background: sc.bg, color: sc.color }}
      >
        {isSkipped ? "—" : score ?? "—"}
      </div>
      {/* 题号小标 */}
      <div className="absolute left-0 -bottom-1 w-10 md:w-12 text-center">
        <span className="text-[9px] font-mono text-dim/60 tracking-wider">Q{String(index).padStart(2, "0")}</span>
      </div>
      {/* 内容卡 */}
      <div className={cn("rounded-lg transition-colors", isSkipped ? "opacity-60" : "")}>
        {children}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 题卡 meta 行 ── 面包屑式分类 + 右侧操作
// ─────────────────────────────────────────────────────────────
interface QMetaRowProps {
  parts: (string | undefined | null)[];
  trailing?: ReactNode;
}

function QMetaRow({ parts, trailing }: QMetaRowProps) {
  const valid = parts.filter(Boolean) as string[];
  if (!valid.length && !trailing) return null;
  return (
    <div className="flex items-center justify-between gap-2 mb-2 text-xs text-dim/80">
      <div className="flex items-center gap-1.5 flex-wrap min-w-0">
        {valid.map((p, i) => (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && <span className="opacity-40">/</span>}
            <span className="truncate">{p}</span>
          </span>
        ))}
      </div>
      {trailing && <div className="shrink-0">{trailing}</div>}
    </div>
  );
}

interface DimensionScoresProps {
  dimensionScores: Record<string, number> | undefined;
  avgScore: number | undefined | null;
  labels: Record<string, string>;
}

function DimensionScores({ dimensionScores, avgScore, labels }: DimensionScoresProps) {
  if (!dimensionScores) return null;
  const entries = Object.entries(labels || {}).filter(([k]) => dimensionScores[k] != null);
  if (!entries.length) return null;

  return (
    <Card className="mb-6" hoverLift>
      <CardContent className="p-5 md:p-7">
        <div className="text-lg font-semibold mb-4 heading-underline">
          维度评分
          {avgScore != null && (
            <span className="text-sm font-normal text-dim ml-3">综合 <ScorePill score={avgScore} /></span>
          )}
        </div>
        {entries.map(([key, label], idx) => {
          const score = dimensionScores[key];
          const color = score >= 8 ? "var(--green)" : score >= 6 ? "var(--ai-glow)" : score >= 4 ? "#e2b93b" : "var(--red)";
          return (
            <div key={key} className="flex items-center gap-3 mb-2.5" style={{ animationDelay: `${idx * 0.1}s` }}>
              <div className="w-[90px] md:w-[110px] text-[13px] text-dim text-right shrink-0">{label}</div>
              <div className="flex-1 h-2.5 bg-border overflow-hidden relative">
                <div
                  className="h-full transition-[width] duration-700 ease-out"
                  style={{ width: `${score * 10}%`, background: color }}
                />
              </div>
              <div className="w-9 text-sm font-semibold text-right shrink-0 score-pop" style={{ color, animationDelay: `${idx * 0.15 + 0.3}s` }}>{score}</div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

interface PointListProps {
  title: string;
  items: any[] | undefined;
  tone?: "red" | "green" | "blue";
  eyebrow?: string;
}

function PointList({ title, items, tone = "red", eyebrow }: PointListProps) {
  if (!items?.length) return null;
  const palette =
    tone === "green"
      ? { bg: "bg-green/8", border: "border-green/20", num: "bg-green/15 text-green", line: "bg-green/30", label: "var(--green)" }
      : tone === "blue"
        ? { bg: "bg-tertiary/8", border: "border-tertiary/20", num: "bg-tertiary/15 text-tertiary", line: "bg-tertiary/30", label: "var(--tertiary)" }
        : { bg: "bg-red/8", border: "border-red/20", num: "bg-red/15 text-red", line: "bg-red/30", label: "var(--red)" };

  const defaultEyebrow = tone === "green" ? "STRENGTHS" : tone === "blue" ? "INSIGHTS" : "WEAKNESSES";

  return (
    <div className="mb-6 animate-fade-in-up">
      {/* 区块头：眼眉色条 + 标题 + 数量徽标 */}
      <div className="flex items-center gap-3 mb-3">
        <div className="flex items-center gap-2">
          <div className={`w-1 h-4 rounded-full ${palette.line}`} />
          <span
            className="text-[10px] font-mono uppercase tracking-[0.18em] opacity-70"
            style={{ color: palette.label }}
          >
            {eyebrow || defaultEyebrow}
          </span>
        </div>
        <div className="text-base font-semibold text-text">{title}</div>
        <span
          className={`ml-auto text-xs font-mono px-2 py-0.5 rounded-md ${palette.num}`}
        >
          {items.length}
        </span>
      </div>
      {/* 列表项 */}
      <div className="flex flex-col gap-2">
        {items.map((item: any, i: number) => {
          const text = typeof item === "string" ? item : item.point || JSON.stringify(item);
          return (
            <div
              key={i}
              className={`flex gap-3 px-3.5 py-2.5 rounded-xl text-[14px] text-text border animate-fade-in ${palette.bg} ${palette.border}`}
              style={{ animationDelay: `${i * 0.04}s` }}
            >
              <span
                className={`${palette.num} text-[10px] font-mono font-semibold w-5 h-5 rounded-md flex items-center justify-center shrink-0 mt-0.5`}
              >
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="leading-relaxed flex-1">{text}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 区块标题（统一规范）
// ─────────────────────────────────────────────────────────────
interface SectionHeadingProps {
  eyebrow: string;
  title: string;
  count?: number;
  accent?: string;
}

function SectionHeading({ eyebrow, title, count, accent = "var(--primary)" }: SectionHeadingProps) {
  return (
    <div className="flex items-center gap-3 mb-4 mt-2">
      <div className="flex items-center gap-2">
        <div className="w-1 h-4 rounded-full" style={{ background: accent, opacity: 0.5 }} />
        <span
          className="text-[10px] font-mono uppercase tracking-[0.18em] opacity-70"
          style={{ color: accent }}
        >
          {eyebrow}
        </span>
      </div>
      <h3 className="text-base font-semibold text-text">{title}</h3>
      {count != null && (
        <span className="ml-auto text-xs text-dim font-mono">
          {count} 项
        </span>
      )}
    </div>
  );
}

interface SoloRecordingReviewProps {
  topicsCovered: any[];
  overall: Overall | null | undefined;
}

function SoloRecordingReview({ topicsCovered, overall }: SoloRecordingReviewProps) {
  const avgScore: number | string = overall?.avg_score ?? "-";

  return (
    <>
      <HeroOverview
        eyebrow="RECORDING REVIEW"
        title="录音复盘报告"
        subtitle="基于你提供的面试录音，AI 已完成转写与多维度分析"
        summary={overall?.summary}
        avgScore={avgScore}
        icon={<BookOpen size={14} className="text-primary" />}
      />

      {topicsCovered?.length > 0 && (
        <StatStrip
          items={[
            { label: "知识点", value: topicsCovered.length, unit: "项" },
            { label: "亮点", value: overall?.new_strong_points?.length || 0, unit: "条", accent: "var(--green)" },
            { label: "薄弱点", value: overall?.new_weak_points?.length || 0, unit: "条", accent: "var(--warning)" },
            { label: "综合", value: avgScore, unit: typeof avgScore === "number" ? "/10" : "" },
          ]}
        />
      )}

      <PointList title="薄弱点" items={overall?.new_weak_points} />
      <PointList title="亮点" items={overall?.new_strong_points} tone="green" />

      {topicsCovered?.length > 0 && (
        <div className="mb-6">
          <SectionHeading eyebrow="TOPICS COVERED" title="涉及知识点" count={topicsCovered.length} />
          <div className="relative">
            {topicsCovered.map((t: any, i: number) => (
              <TimelineNode
                key={i}
                index={i + 1}
                score={t.score}
                isLast={i === topicsCovered.length - 1}
              >
                <Card className="animate-fade-in">
                  <CardContent className="p-4 md:p-5">
                    <QMetaRow parts={["知识点", t.understanding]} />
                    <h4 className="text-[15px] font-semibold mb-2.5">{t.topic || "未知知识点"}</h4>
                    {t.assessment && (
                      <div className="text-sm leading-[1.7] text-text/90 bg-secondary/40 rounded-lg px-3 py-2.5 mb-2">
                        {t.assessment}
                      </div>
                    )}
                    {t.errors?.length > 0 && (
                      <div className="text-[13px] text-red leading-relaxed mt-1">
                        <span className="font-semibold opacity-70">错误：</span>
                        {t.errors.join("、")}
                      </div>
                    )}
                    {t.missing?.length > 0 && (
                      <div className="text-[13px] text-dim leading-relaxed mt-1">
                        <span className="font-semibold opacity-70">遗漏：</span>
                        {t.missing.join("、")}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TimelineNode>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

interface DrillReviewProps {
  scores: Score[] | null | undefined;
  overall: Overall | null | undefined;
  questions: Question[] | null | undefined;
  answers: { question_id: number; answer: string }[] | null | undefined;
  topic: string | null | undefined;
  sessionId: string | undefined;
  cachedRefAnswers: Record<string, string>;
  ragEvalMetrics?: RAGEvalMetrics | null;
}

function RAGQualityBadge({ value, label, metricKey }: { value: number | null | undefined; label: string; metricKey?: string }) {
  if (value == null) return null;
  const pct = Math.round(value);
  const color = metricColorVar(value / 100, metricKey);
  const badge = (
    <div className="flex flex-col items-center gap-0.5 rounded-lg border border-border/40 bg-card/60 px-3 py-2 min-w-[80px]">
      <span className="text-[11px] text-muted-fg">{label}</span>
      <span className="text-lg font-semibold font-mono tabular-nums" style={{ color }}>{pct}%</span>
      <div className="w-full h-1 rounded-full bg-muted overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
  return metricKey ? <MetricInfoTooltip metricKey={metricKey} label={label}>{badge}</MetricInfoTooltip> : badge;
}

function DrillReview({ scores, overall, questions, answers, topic, sessionId, cachedRefAnswers, ragEvalMetrics }: DrillReviewProps) {
  const answerMap: Record<number, string> = {};
  for (const a of (answers || [])) answerMap[a.question_id] = a.answer;
  const scoreMap: Record<number, Score> = {};
  for (const s of (scores || [])) scoreMap[s.question_id] = s;
  const [refAnswers, setRefAnswers] = useState<Record<string, string>>(cachedRefAnswers || {});
  const [refLoading, setRefLoading] = useState<Record<string, boolean>>({});
  const [favorited, setFavorited] = useState<Record<number, boolean>>({});
  const [favLoading, setFavLoading] = useState<Record<number, boolean>>({});

  // Sync cached refs in once they arrive from the parent (getReview is async).
  // Merge instead of replace so locally-fetched answers aren't clobbered.
  useEffect(() => {
    if (!cachedRefAnswers || !Object.keys(cachedRefAnswers).length) return;
    setRefAnswers((prev) => ({ ...cachedRefAnswers, ...prev }));
  }, [cachedRefAnswers]);

  const handleRefAnswer = async (qId: number, questionText: string, force: boolean = false) => {
    if (refAnswers[qId] && !force) return;
    setRefLoading((p) => ({ ...p, [qId]: true }));
    try {
      const data = await getReferenceAnswer(topic, questionText, sessionId, qId, force);
      setRefAnswers((p) => ({ ...p, [qId]: data.reference_answer }));
    } catch (e: any) {
      setRefAnswers((p) => ({ ...p, [qId]: "生成失败: " + e.message }));
    }
    setRefLoading((p) => ({ ...p, [qId]: false }));
  };

  const handleFavorite = async (q: Question, s: any) => {
    if (favorited[q.id]) return;
    setFavLoading((p) => ({ ...p, [q.id]: true }));
    try {
      await addFavorite({
        session_id: sessionId,
        question: q.question,
        user_answer: answerMap[q.id] || "",
        reference_answer: refAnswers[q.id] || "",
        score: s?.score,
        assessment: s?.assessment || "",
        topic: topic || "",
        difficulty: q.difficulty ? String(q.difficulty) : "",
      });
      setFavorited((p) => ({ ...p, [q.id]: true }));
    } catch (e: any) {
      console.error("收藏失败:", e);
    }
    setFavLoading((p) => ({ ...p, [q.id]: false }));
  };

  const avgScore: number | string = overall?.avg_score ?? "-";
  const totalQ = questions?.length || 0;
  const answered = answers?.filter((a) => a.answer).length || 0;
  const skipped = totalQ - answered;

  return (
    <>
      <HeroOverview
        eyebrow="DRILL REVIEW"
        title={topic ? `${topic} · 训练复盘` : "训练复盘"}
        subtitle="逐题点评、改进建议与参考答案，按时间线查看"
        summary={overall?.summary}
        avgScore={avgScore}
        icon={<Sparkles size={14} className="text-primary" />}
      />

      <StatStrip
        items={[
          { label: "题目", value: totalQ, unit: "题" },
          { label: "已答", value: answered, unit: "题", accent: "var(--green)" },
          { label: "跳过", value: skipped, unit: "题", accent: skipped > 0 ? "var(--warning)" : undefined },
          { label: "综合", value: avgScore, unit: typeof avgScore === "number" ? "/10" : "" },
        ]}
      />

      {ragEvalMetrics && (
        <div className="mb-4 animate-fade-in">
          <div className="text-[11px] font-medium text-muted-fg tracking-wider mb-2">RAG 质量</div>
          <div className="grid grid-cols-3 gap-2">
            <RAGQualityBadge label="忠实度" value={ragEvalMetrics.faithfulness} metricKey="faithfulness" />
            <RAGQualityBadge label="切题度" value={ragEvalMetrics.answer_relevance} metricKey="answer_relevance" />
            <RAGQualityBadge label="综合质量" value={ragEvalMetrics.answer_correctness} metricKey="answer_correctness" />
          </div>
        </div>
      )}

      <PointList title="薄弱点" items={overall?.new_weak_points} />
      <PointList title="亮点" items={overall?.new_strong_points} tone="green" />

      <SectionHeading eyebrow="QUESTION TIMELINE" title="逐题复盘" count={totalQ} />

      <div className="relative">
        {(questions || []).map((q, idx) => {
          const s: any = scoreMap[q.id] || {};
          const answer = answerMap[q.id];
          const isSkipped = !answer;
          const isLast = idx === (questions?.length || 0) - 1;

          return (
            <TimelineNode key={q.id} index={q.id} score={s.score} isSkipped={isSkipped} isLast={isLast}>
              {isSkipped ? (
                <Card className="animate-fade-in">
                  <CardContent className="p-4 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <QMetaRow parts={["跳过", q.focus_area]} />
                      <p className="text-sm text-dim truncate">{q.question}</p>
                    </div>
                    <span className="text-[11px] uppercase tracking-wider text-dim/60 shrink-0">未作答</span>
                  </CardContent>
                </Card>
              ) : (
                <Card className="animate-fade-in" hoverLift>
                  <CardContent className="p-4 md:p-5">
                    <QMetaRow parts={[q.focus_area, q.difficulty ? `难度 ${q.difficulty}/5` : null]} />
                    {/* L2 焦点：题目 */}
                    <div className="md-content question-md text-[15px] md:text-base leading-relaxed mb-3">
                      <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{q.question}</ReactMarkdown>
                    </div>

                    {/* 你的回答 */}
                    <div className="bg-secondary/60 rounded-xl px-3.5 py-3 mb-3 border-l-2 border-primary/30">
                      <div className="text-[10px] uppercase tracking-widest text-dim/70 mb-1.5">YOUR ANSWER</div>
                      <div className="md-content text-sm leading-relaxed text-text/90">
                        <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{answer}</ReactMarkdown>
                      </div>
                    </div>

                    {/* 点评 */}
                    {s.assessment && s.assessment !== "未作答" && (
                      <div className="mb-2.5">
                        <div className="text-[10px] uppercase tracking-widest text-dim/70 mb-1">AI ASSESSMENT</div>
                        <p className="text-sm leading-[1.7] text-text/90">{s.assessment}</p>
                      </div>
                    )}

                    {/* 改进建议 */}
                    {s.improvement && (
                      <div className="rounded-xl px-3.5 py-2.5 mb-2 border border-primary/20 bg-primary/5">
                        <div className="text-[10px] uppercase tracking-widest text-primary/80 mb-1 font-semibold">SUGGESTION</div>
                        <p className="text-sm leading-[1.7] text-primary">{s.improvement}</p>
                      </div>
                    )}

                    {/* 理解 / 遗漏 — 同级两栏 */}
                    {(s.understanding || s.key_missing?.length > 0) && (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-2">
                        {s.understanding && s.understanding !== "未作答" && (
                          <div className="text-[12px] text-dim leading-relaxed">
                            <span className="font-semibold opacity-70">理解程度：</span>
                            {s.understanding}
                          </div>
                        )}
                        {s.key_missing?.length > 0 && (
                          <div className="text-[12px] text-red/90 leading-relaxed">
                            <span className="font-semibold opacity-70">遗漏关键点：</span>
                            {s.key_missing.join("、")}
                          </div>
                        )}
                      </div>
                    )}

                    {/* RAG 指标 */}
                    {ragEvalMetrics?.per_question && (() => {
                      const pq = ragEvalMetrics.per_question.find((r) => r.question_id === q.id);
                      if (!pq) return null;
                      return (
                        <div className="flex gap-2 mt-2 text-[11px]">
                          {pq.faithfulness != null && (
                            <Badge variant="outline" className="font-mono text-[10px] gap-1" style={{ borderColor: metricColorVar(pq.faithfulness / 10), color: metricColorVar(pq.faithfulness / 10) }}>
                              忠实 {pq.faithfulness}/10
                            </Badge>
                          )}
                          {pq.answer_relevance != null && (
                            <Badge variant="outline" className="font-mono text-[10px] gap-1" style={{ borderColor: metricColorVar(pq.answer_relevance / 10), color: metricColorVar(pq.answer_relevance / 10) }}>
                              切题 {pq.answer_relevance}/10
                            </Badge>
                          )}
                        </div>
                      );
                    })()}

                    {/* 操作行 */}
                    <div className="mt-3 pt-3 border-t border-border/50 flex items-center gap-1 flex-wrap">
                      {!refAnswers[q.id] && topic && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-primary"
                          onClick={() => handleRefAnswer(q.id, q.question)}
                          disabled={refLoading[q.id]}
                        >
                          <BookOpen size={13} />
                          {refLoading[q.id] ? "正在生成参考答案..." : "查看参考答案"}
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        className={favorited[q.id] ? "text-yellow-500" : "text-dim"}
                        onClick={() => handleFavorite(q, s)}
                        disabled={favLoading[q.id] || favorited[q.id]}
                      >
                        <Star size={13} className={favorited[q.id] ? "fill-yellow-400" : ""} />
                        {favorited[q.id] ? "已收藏" : "收藏"}
                      </Button>
                    </div>

                    {refAnswers[q.id] && (
                      <div className="text-sm leading-[1.8] mt-2">
                        <div className="text-xs font-semibold text-dim mb-2 flex items-center justify-between">
                          <span className="flex items-center gap-1.5">
                            <BookOpen size={13} /> 参考答案
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-dim h-6 px-2 text-xs"
                            onClick={() => handleRefAnswer(q.id, q.question, true)}
                            disabled={refLoading[q.id]}
                          >
                            <RefreshCw size={11} className={refLoading[q.id] ? "animate-spin" : ""} />
                            重新生成
                          </Button>
                        </div>
                        <div className="md-content bg-secondary/60 rounded-xl px-3.5 py-3">
                          <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{refAnswers[q.id]}</ReactMarkdown>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}
            </TimelineNode>
          );
        })}
      </div>
    </>
  );
}

interface JobPrepReviewProps {
  scores: Score[] | null | undefined;
  overall: Overall | null | undefined;
  questions: Question[] | null | undefined;
  answers: { question_id: number; answer: string }[] | null | undefined;
  meta: Record<string, any>;
}

function JobPrepReview({ scores, overall, questions, answers, meta }: JobPrepReviewProps) {
  const answerMap: Record<number, string> = {};
  for (const a of (answers || [])) answerMap[a.question_id] = a.answer;
  const scoreMap: Record<number, Score> = {};
  for (const s of (scores || [])) scoreMap[s.question_id] = s;
  const avgScore = overall?.avg_score || "-";

  return (
    <>
      <Card className="mb-6">
        <CardContent className="p-5 md:p-8">
          <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <BriefcaseBusiness size={18} className="text-tertiary" />
                <span className="text-lg font-semibold">
                  {meta?.company ? `${meta.company} · ` : ""}{meta?.position || "目标岗位"}
                </span>
              </div>
              {meta?.preview?.role_summary && (
                <div className="text-sm text-dim leading-relaxed">{meta.preview.role_summary}</div>
              )}
            </div>
            <div className="text-right">
              <div className="text-[32px] font-bold" style={{ color: typeof avgScore === "number" ? getScoreColor(avgScore).color : "var(--text)" }}>
                {avgScore}
              </div>
              <div className="text-sm text-dim">/10</div>
            </div>
          </div>

          {overall?.summary && (
            <div className="text-[15px] leading-[1.8] text-text mb-4">{overall.summary}</div>
          )}
          {overall?.role_fit_summary && (
            <div className="rounded-xl bg-tertiary/8 border border-tertiary/15 px-4 py-3 text-sm leading-relaxed">
              <div className="text-[13px] font-semibold text-tertiary mb-1.5">岗位匹配判断</div>
              {overall.role_fit_summary}
            </div>
          )}

          <div className="flex flex-wrap gap-3 mt-4">
            <Badge variant="secondary">共 {questions?.length || 0} 题</Badge>
            <Badge variant="secondary">已答 {answers?.filter((a) => a.answer).length || 0} 题</Badge>
            <Badge variant={meta?.use_resume ? "blue" : "secondary"}>{meta?.use_resume ? "JD + 简历联动" : "仅 JD"}</Badge>
          </div>
        </CardContent>
      </Card>

      <DimensionScores
        dimensionScores={overall?.dimension_scores}
        avgScore={overall?.avg_score}
        labels={JOB_PREP_DIMENSION_LABELS}
      />

      <PointList title="高风险追问点" items={(overall as any)?.interviewer_hotspots} tone="blue" />
      <PointList title="面试前优先补强" items={(overall as any)?.prep_priorities} />
      <PointList title="薄弱点" items={overall?.new_weak_points} />
      <PointList title="亮点" items={overall?.new_strong_points} tone="green" />

      <div className="text-base font-semibold mb-3 text-text">逐题复盘</div>
      <div className="flex flex-col gap-4">
        {(questions || []).map((q) => {
          const s: any = scoreMap[q.id] || {};
          const answer = answerMap[q.id];
          const isSkipped = !answer;

          return (
            <Card key={q.id} className={isSkipped ? "opacity-60" : "animate-fade-in"}>
              <CardContent className="p-4 md:p-6">
                <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="outline" className="text-primary border-primary/30">Q{q.id}</Badge>
                    {q.category && <Badge variant="blue">{q.category}</Badge>}
                    {q.focus_area && <Badge variant="secondary">{q.focus_area}</Badge>}
                  </div>
                  <ScorePill score={isSkipped ? null : s.score} />
                </div>

                <div className="text-[15px] font-medium leading-relaxed mb-3 md-content question-md">
                  <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{q.question}</ReactMarkdown>
                </div>

                {q.intent && (
                  <div className="mb-3 rounded-lg bg-secondary px-3.5 py-3 text-sm text-dim leading-relaxed">
                    <span className="font-medium text-text">面试官在看什么：</span> {q.intent}
                  </div>
                )}

                {isSkipped ? (
                  <div className="text-[13px] text-dim">未作答</div>
                ) : (
                  <>
                    <div className="bg-secondary rounded-lg px-3 py-3 md:px-4 mb-3">
                      <div className="text-xs font-semibold text-dim mb-1.5 opacity-70">你的回答</div>
                      <div className="md-content text-sm leading-relaxed">
                        <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{answer}</ReactMarkdown>
                      </div>
                    </div>

                    {s.role_expectation && (
                      <div className="text-sm leading-[1.7] text-dim mb-2">
                        <strong className="text-xs opacity-60">岗位在看什么: </strong>{s.role_expectation}
                      </div>
                    )}
                    {s.assessment && (
                      <div className="text-sm leading-[1.7] text-text mb-2">
                        <strong className="text-xs opacity-60">点评: </strong>{s.assessment}
                      </div>
                    )}
                    {s.improvement && (
                      <div className="text-sm leading-[1.7] text-primary bg-primary/8 rounded-lg px-3 py-2.5 mb-2">
                        <strong className="text-xs opacity-70">改进建议: </strong>{s.improvement}
                      </div>
                    )}
                    {s.understanding && (
                      <div className="text-[13px] text-dim italic mb-1">理解程度: {s.understanding}</div>
                    )}
                    {s.key_missing?.length > 0 && (
                      <div className="text-[13px] text-red leading-normal">遗漏关键点: {s.key_missing.join("、")}</div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </>
  );
}

function inferAnswers(questions: Question[], transcript: { role: string; content: string }[]): { question_id: number; answer: string }[] {
  if (!questions?.length || !transcript?.length) return [];
  return questions.map((q) => {
    const qIdx = transcript.findIndex((m) => m.role === "assistant" && m.content === q.question);
    const next = qIdx >= 0 ? transcript[qIdx + 1] : null;
    return { question_id: q.id, answer: next?.role === "user" ? next.content : "" };
  });
}

export default function Review() {
  const { sessionId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();

  const stateData: any = location.state || {};

  const [review, setReview] = useState<string | null>(stateData.review || null);
  const [scores, setScores] = useState<Score[] | null>(stateData.scores || null);
  const [overall, setOverall] = useState<Overall | null>(stateData.overall || null);
  const [questions, setQuestions] = useState<Question[]>(stateData.questions || []);
  const [answers, setAnswers] = useState<{ question_id: number; answer: string }[]>(stateData.answers || []);
  const [messages, setMessages] = useState<{ role: string; content: string }[]>(stateData.messages || []);
  const [mode, setMode] = useState<string | null>(stateData.mode || null);
  const [topic, setTopic] = useState<string | null>(stateData.topic || null);
  const [topicsCovered, setTopicsCovered] = useState<any[]>(stateData.topics_covered || []);
  const [meta, setMeta] = useState<Record<string, any>>(stateData.meta || {});
  const [showTranscript, setShowTranscript] = useState(false);
  const [refAnswersCache, setRefAnswersCache] = useState<Record<string, string>>({});
  const [ragEvalMetrics, setRagEvalMetrics] = useState<RAGEvalMetrics | null>(stateData.ragEvalMetrics || null);
  const [loading, setLoading] = useState(!review && !scores);
  // Fetch the review at most once per session. The effect's deps include
  // review/scores, so an empty backend response for an in-progress session would
  // otherwise let it re-fire getReview on every subsequent render.
  const fetchedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!review && !scores) {
      if (fetchedRef.current === sessionId) return;
      fetchedRef.current = sessionId;
      setLoading(true);
      getReview(sessionId)
        .then((data: any) => {
          setReview(data.review);
          if (data.scores) setScores(data.scores);
          if (data.questions) setQuestions(data.questions);
          if (data.transcript) setMessages(data.transcript);
          if (data.mode) setMode(data.mode);
          if (data.topic) setTopic(data.topic);
          if (data.overall && Object.keys(data.overall).length) {
            setOverall(data.overall);
          } else if (data.weak_points) {
            const wp = Array.isArray(data.weak_points) ? data.weak_points : [];
            if (wp.length) setOverall((prev) => ({ ...prev, new_weak_points: wp }));
          }
          if (data.topics_covered) setTopicsCovered(data.topics_covered);
          if (data.meta) setMeta(data.meta);
          if (data.reference_answers && Object.keys(data.reference_answers).length) {
            setRefAnswersCache(data.reference_answers);
          }
          if (data.mode === "topic_drill" || data.mode === "jd_prep") {
            setAnswers(inferAnswers(data.questions || [], data.transcript || []));
          }
        })
        .catch((err: any) => setReview("加载失败: " + err.message))
        .finally(() => setLoading(false));
    }
  }, [sessionId, review, scores]);

  useEffect(() => {
    if (ragEvalMetrics || !sessionId) return;
    getSessionRAGMetrics(sessionId)
      .then((records) => {
        const evalRec = records.find((r) => r.stage === "answer_eval");
        if (evalRec && (evalRec.faithfulness != null || evalRec.answer_relevance != null)) {
          setRagEvalMetrics({
            faithfulness: Math.round((evalRec.faithfulness ?? 0) * 100),
            answer_relevance: Math.round((evalRec.answer_relevance ?? 0) * 100),
            answer_correctness: Math.round((evalRec.answer_correctness ?? 0) * 100),
            per_question: (evalRec.detail as any)?.per_question?.map((pq: any) => ({
              question_id: pq.qid,
              faithfulness: pq.f,
              answer_relevance: pq.ar,
            })) || [],
          });
        }
      })
      .catch(() => {});
  }, [sessionId, ragEvalMetrics]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center py-15 text-dim">
        <div className="flex flex-col items-center gap-3">
          <div className="flex gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot" />
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.2s]" />
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.4s]" />
          </div>
          <span className="text-sm">加载复盘报告中...</span>
        </div>
      </div>
    );
  }

  const currentMode = mode || stateData.mode;
  const isRecording = currentMode === "recording";
  const isJobPrep = currentMode === "jd_prep";
  const isRecordingDual = isRecording && (stateData.recording_mode === "dual" || questions.length > 0);
  const showDrill = currentMode === "topic_drill" || isRecordingDual;
  const title = isRecording ? "录音复盘" : isJobPrep ? "JD 备面复盘" : showDrill ? "训练复盘" : "面试复盘";

  return (
    <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-3xl mx-auto w-full">
      <div className="mb-8 animate-fade-in">
        <div className="flex items-center gap-2 mb-2">
          {isJobPrep && <BriefcaseBusiness size={18} className="text-tertiary" />}
          {showDrill && !isJobPrep && !isRecording && <Sparkles size={18} className="text-primary" />}
          {isRecording && <BookOpen size={18} className="text-primary" />}
          <h1 className="sig-display text-2xl md:text-[28px]">{title}<span className="sig-accent-c">.</span></h1>
        </div>
        <div className="text-sm text-dim sig-num">Session: {sessionId}</div>
      </div>

      <div className="stagger-children">
        {isRecording && !isRecordingDual ? (
          <SoloRecordingReview topicsCovered={topicsCovered} overall={overall} />
        ) : isJobPrep ? (
          <JobPrepReview scores={scores} overall={overall} questions={questions} answers={answers} meta={meta} />
        ) : showDrill ? (
          <DrillReview scores={scores} overall={overall} questions={questions} answers={answers} topic={topic} sessionId={sessionId} cachedRefAnswers={refAnswersCache} ragEvalMetrics={ragEvalMetrics} />
        ) : (
          <>
            <DimensionScores
              dimensionScores={stateData.dimension_scores || overall?.dimension_scores}
              avgScore={stateData.avg_score ?? overall?.avg_score}
              labels={RESUME_DIMENSION_LABELS}
            />
            <Card className="mb-6">
              <CardContent className="p-5 md:p-8 leading-[1.8] text-[15px]">
                <div className="md-content">
                  <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{review || ""}</ReactMarkdown>
                </div>
              </CardContent>
            </Card>

            {messages.length > 0 && (
              <div className="mb-6">
                <Button variant="outline" onClick={() => setShowTranscript(!showTranscript)} className="mr-3">
                  {showTranscript ? "收起面试记录" : "查看面试记录"}
                </Button>
                {showTranscript && (
                  <Card className="mt-4">
                    <CardContent className="p-4 md:p-6 max-h-[500px] overflow-y-auto">
                      {messages.map((msg, i) => (
                        <div key={i} className="py-2 border-b border-border text-sm leading-relaxed last:border-0">
                          <strong style={{ color: msg.role === "user" ? "var(--ai-glow)" : "var(--green)" }}>
                            {msg.role === "user" ? "你" : "面试官"}:
                          </strong>{" "}
                          {msg.content}
                        </div>
                      ))}
                    </CardContent>
                  </Card>
                )}
              </div>
            )}
          </>
        )}
      </div>

      <Button variant="outline" className="mt-6" onClick={() => navigate("/")}>
        返回首页
      </Button>
    </div>
  );
}
