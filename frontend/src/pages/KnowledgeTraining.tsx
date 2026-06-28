import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  Brain,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  FileText,
  HelpCircle,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/ChatBubble";
import { getTopicIcon } from "../utils/topicIcons";
import useDraftPersist, { readDraft } from "../hooks/useDraftPersist";
import {
  generateKnowledgeTrainingCards,
  getDueKnowledgeCards,
  getKnowledgeTrainingAvailability,
  getSavedKnowledgeCards,
  reviewKnowledgeCard,
  type Familiarity,
  type KnowledgeTrainingAvailabilityItem,
  type KnowledgeTrainingCard,
  type KnowledgeTrainingDepth,
  type KnowledgeTrainingMode,
} from "../api/knowledgeTraining";

type StudyMode = "new" | "review" | "saved";

const COUNT_OPTIONS = [3, 5, 10];
const MODE_OPTIONS: { value: KnowledgeTrainingMode; label: string }[] = [
  { value: "random", label: "随机知识点" },
  { value: "high_freq", label: "高频考点" },
];
const DEPTH_OPTIONS: { value: KnowledgeTrainingDepth; label: string }[] = [
  { value: "basic", label: "基础记忆" },
  { value: "understand", label: "例子理解" },
  { value: "interview_expression", label: "面试表达" },
];

function hasBlockMarkdown(text: string): boolean {
  return /```|^\s{0,3}(?:[-*+]\s+|\d+[.)、]\s+|#{1,6}\s+|>\s+|\|.+\|)/m.test(text);
}

function normalizeKnowledgeMarkdown(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return trimmed;

  // 新数据后端已格式化为 Markdown，含块级结构时直接渲染
  if (hasBlockMarkdown(trimmed)) return trimmed;

  // 旧数据兜底：多行无标记纯文本转成列表，单行原样作为段落
  const lines = trimmed
    .split(/\n+/)
    .map((line) => line.replace(/^\s*(?:[-*+]\s+|\d+[.)、]\s+)/, "").trim())
    .filter(Boolean);
  return lines.length > 1 ? lines.map((line) => `- ${line}`).join("\n") : trimmed;
}

function CardMarkdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("md-content text-sm leading-7 text-text/90", className)}>
      <Markdown>{children}</Markdown>
    </div>
  );
}

function sourceLabel(card: KnowledgeTrainingCard): string {
  const ref = card.source_refs?.[0];
  if (!ref) return "无来源";
  return ref.header_path ? `${ref.filename} / ${ref.header_path}` : ref.filename;
}

function familiarityLabel(value?: Familiarity) {
  if (value === "known") return "已掌握";
  if (value === "uncertain") return "模糊";
  if (value === "unknown") return "不会";
  return "未标记";
}

function familiarityClass(value?: Familiarity) {
  if (value === "known") return "border-green/40 bg-green/10 text-green";
  if (value === "uncertain") return "border-yellow-500/40 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400";
  if (value === "unknown") return "border-red/40 bg-red/10 text-red";
  return "border-border bg-secondary/40 text-dim";
}

export default function KnowledgeTraining() {
  const [searchParams] = useSearchParams();
  const [availability, setAvailability] = useState<Record<string, KnowledgeTrainingAvailabilityItem>>({});
  const [selected, setSelected] = useState<string>("");
  const [count, setCount] = useState(5);
  const [mode, setMode] = useState<KnowledgeTrainingMode>("random");
  const [depth, setDepth] = useState<KnowledgeTrainingDepth>("understand");
  const [cards, setCards] = useState<KnowledgeTrainingCard[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [revealedAnswers, setRevealedAnswers] = useState<Record<string, boolean>>({});
  const [familiarity, setFamiliarity] = useState<Record<string, Familiarity>>({});
  const [loadingTopics, setLoadingTopics] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  // 新卡生成 vs 到期复习 (SM-2 driven).
  const [studyMode, setStudyMode] = useState<StudyMode>("new");

  const topicKeys = Object.keys(availability);
  const selectedInfo = selected ? availability[selected] : null;
  const currentCard = cards[currentIndex] || null;
  const dueCount = selectedInfo?.due_count ?? 0;
  const savedCount = selectedInfo?.saved_count ?? 0;

  const loadAvailability = useCallback(async () => {
    setLoadingTopics(true);
    try {
      const data = await getKnowledgeTrainingAvailability();
      setAvailability(data.topics);
      const keys = Object.keys(data.topics);
      // Prefer ?topic= URL param (e.g. from Profile weak-point links), then
      // existing state, then first available topic.
      const urlTopic = searchParams.get("topic");
      const preferredTopic = urlTopic && data.topics[urlTopic] ? urlTopic : null;
      setSelected((prev) => preferredTopic || (prev && data.topics[prev] ? prev : keys[0] || ""));
    } catch (e: any) {
      setError(e.message || "加载知识库模块失败");
    } finally {
      setLoadingTopics(false);
    }
  }, [searchParams]);

  useEffect(() => {
    loadAvailability();
  }, [loadAvailability]);

  const sessionKey = selected ? `knowledge-training:session:${selected}` : "";

  // Familiarity now lives server-side on each card (SM-2 state). Build the
  // in-view lookup from the cards' own familiarity field.
  const hydrateFamiliarity = useCallback((list: KnowledgeTrainingCard[]) => {
    const map: Record<string, Familiarity> = {};
    for (const c of list) {
      if (c.familiarity === "known" || c.familiarity === "uncertain" || c.familiarity === "unknown") {
        map[c.id] = c.familiarity;
      }
    }
    setFamiliarity(map);
  }, []);

  // On topic / mode switch: reset, and in "new" mode restore the last generated
  // session (survives refresh / nav away, avoiding a wasted LLM regeneration).
  useEffect(() => {
    setRevealedAnswers({});
    setError("");
    setCurrentIndex(0);
    if (studyMode === "new") {
      const saved = readDraft<{ cards: KnowledgeTrainingCard[]; currentIndex: number }>(sessionKey);
      if (saved?.cards?.length) {
        setCards(saved.cards);
        setCurrentIndex(Math.min(saved.currentIndex || 0, saved.cards.length - 1));
        hydrateFamiliarity(saved.cards);
        return;
      }
    }
    setCards([]);
    setFamiliarity({});
  }, [sessionKey, studyMode, hydrateFamiliarity]);

  // Persist the generated card session per topic (new mode only).
  useDraftPersist({
    key: studyMode === "new" && cards.length > 0 ? sessionKey : null,
    value: { cards, currentIndex },
    isEmpty: (s) => !s.cards.length,
  });

  const markedStats = useMemo(() => {
    const visibleIds = new Set(cards.map((card) => card.id));
    return Object.entries(familiarity).reduce(
      (acc, [id, value]) => {
        if (!visibleIds.has(id)) return acc;
        acc[value] += 1;
        return acc;
      },
      { known: 0, uncertain: 0, unknown: 0 } as Record<Familiarity, number>,
    );
  }, [cards, familiarity]);

  const startTraining = useCallback(async () => {
    if (!selected || generating) return;
    setGenerating(true);
    setProgress("正在准备训练卡片...");
    setError("");
    setCards([]);
    setCurrentIndex(0);
    setRevealedAnswers({});
    setFamiliarity({});
    try {
      const result = await generateKnowledgeTrainingCards(
        { topic: selected, count, mode, depth },
        {
          onProgress: setProgress,
          onCard: (card) => {
            setCards((prev) => (prev.some((item) => item.id === card.id) ? prev : [...prev, card]));
          },
        },
      );
      setCards(result.cards);
      setCurrentIndex(0);
      hydrateFamiliarity(result.cards);
      setProgress("");
      toast.success(`已生成 ${result.total} 张训练卡片`);
      loadAvailability(); // refresh saved/due badges
    } catch (e: any) {
      setError(e.message || "训练卡片生成失败");
      setProgress("");
    } finally {
      setGenerating(false);
    }
  }, [count, depth, generating, hydrateFamiliarity, loadAvailability, mode, selected]);

  const startReview = useCallback(async () => {
    if (!selected || generating) return;
    setGenerating(true);
    setProgress("正在加载到期卡片...");
    setError("");
    setCards([]);
    setCurrentIndex(0);
    setRevealedAnswers({});
    setFamiliarity({});
    try {
      const { cards: due } = await getDueKnowledgeCards(selected);
      setCards(due);
      setCurrentIndex(0);
      hydrateFamiliarity(due);
      setProgress("");
      if (due.length === 0) toast.info("当前没有到期需要复习的卡片");
      else toast.success(`加载了 ${due.length} 张到期卡片`);
    } catch (e: any) {
      setError(e.message || "加载到期卡片失败");
      setProgress("");
    } finally {
      setGenerating(false);
    }
  }, [generating, hydrateFamiliarity, selected]);

  // Load all server-persisted cards for this topic (incl. unmarked ones). This
  // is the cross-device path: the per-session card view lives in localStorage,
  // but the cards themselves live in SQLite — so on a new browser they're only
  // reachable here, not via the localStorage-restored "new" mode.
  const startSaved = useCallback(async () => {
    if (!selected || generating) return;
    setGenerating(true);
    setProgress("正在加载已保存卡片...");
    setError("");
    setCards([]);
    setCurrentIndex(0);
    setRevealedAnswers({});
    setFamiliarity({});
    try {
      const { items } = await getSavedKnowledgeCards(selected);
      setCards(items);
      setCurrentIndex(0);
      hydrateFamiliarity(items);
      setProgress("");
      if (items.length === 0) toast.info("当前模块还没有已保存的卡片");
      else toast.success(`加载了 ${items.length} 张已保存卡片`);
    } catch (e: any) {
      setError(e.message || "加载已保存卡片失败");
      setProgress("");
    } finally {
      setGenerating(false);
    }
  }, [generating, hydrateFamiliarity, selected]);

  // Auto-load on entering "saved" mode or switching topic, so the full card set
  // appears without a manual click — the direct fix for "cards vanish on another
  // browser". startSaved guards on selected/generating; deps intentionally omit
  // it (it changes with `generating`) to avoid a reload loop.
  useEffect(() => {
    if (studyMode === "saved" && selected) startSaved();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studyMode, selected]);

  const startSession =
    studyMode === "review" ? startReview : studyMode === "saved" ? startSaved : startTraining;

  const setCardFamiliarity = async (value: Familiarity) => {
    if (!currentCard) return;
    const cardId = currentCard.id;
    const prevValue = familiarity[cardId];
    // Optimistic mark; the server applies the SM-2 schedule update.
    setFamiliarity((prev) => ({ ...prev, [cardId]: value }));
    try {
      await reviewKnowledgeCard(cardId, value);
      loadAvailability(); // due/saved counts shifted
    } catch (e: any) {
      setFamiliarity((prev) => {
        const next = { ...prev };
        if (prevValue) next[cardId] = prevValue;
        else delete next[cardId];
        return next;
      });
      toast.error(e?.message || "保存熟悉度失败");
    }
  };

  const revealCurrent = () => {
    if (!currentCard) return;
    setRevealedAnswers((prev) => ({ ...prev, [currentCard.id]: !prev[currentCard.id] }));
  };

  const canStart =
    !!selected && !generating &&
    (studyMode === "review" || studyMode === "saved" || !!selectedInfo?.available);

  return (
    <div className="flex flex-1 min-h-0 flex-col overflow-hidden">
      <header className="border-b border-border px-4 py-3 md:px-6" style={{ background: "var(--sig-bg)" }}>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="sig-kicker mb-1">// KNOWLEDGE TRAINING</div>
            <h1 className="text-xl font-semibold tracking-normal">知识训练场</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-md border border-[color:var(--sig-line-2)] p-0.5 text-sm">
              <button
                className={cn(
                  "rounded px-3 py-1 transition-colors",
                  studyMode === "new" ? "bg-primary text-primary-foreground" : "text-dim hover:text-text",
                )}
                onClick={() => setStudyMode("new")}
                disabled={generating}
              >
                新卡生成
              </button>
              <button
                className={cn(
                  "flex items-center gap-1 rounded px-3 py-1 transition-colors",
                  studyMode === "review" ? "bg-primary text-primary-foreground" : "text-dim hover:text-text",
                )}
                onClick={() => setStudyMode("review")}
                disabled={generating}
              >
                到期复习
                {dueCount > 0 && (
                  <span className={cn(
                    "rounded-full px-1.5 text-[10px] font-mono",
                    studyMode === "review" ? "bg-primary-foreground/20" : "bg-red/15 text-red",
                  )}>{dueCount}</span>
                )}
              </button>
              <button
                className={cn(
                  "flex items-center gap-1 rounded px-3 py-1 transition-colors",
                  studyMode === "saved" ? "bg-primary text-primary-foreground" : "text-dim hover:text-text",
                )}
                onClick={() => setStudyMode("saved")}
                disabled={generating}
              >
                已保存
                {savedCount > 0 && (
                  <span className={cn(
                    "rounded-full px-1.5 text-[10px] font-mono",
                    studyMode === "saved" ? "bg-primary-foreground/20" : "bg-secondary text-dim",
                  )}>{savedCount}</span>
                )}
              </button>
            </div>
            <select
              className="h-9 min-w-[150px] rounded-md border border-[color:var(--sig-line-2)] bg-transparent px-3 text-sm focus:outline-none focus:border-[color:var(--sig-accent)]"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              disabled={loadingTopics || generating}
            >
              {topicKeys.map((key) => (
                <option key={key} value={key}>
                  {availability[key]?.name || key}
                  {availability[key]?.due_count ? ` · ${availability[key]?.due_count} 到期` : ""}
                </option>
              ))}
            </select>
            {studyMode === "new" && (
              <>
                <select
                  className="h-9 rounded-md border border-[color:var(--sig-line-2)] bg-transparent px-3 text-sm focus:outline-none focus:border-[color:var(--sig-accent)]"
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                  disabled={generating}
                >
                  {COUNT_OPTIONS.map((n) => <option key={n} value={n}>{n} 张</option>)}
                </select>
                <select
                  className="h-9 rounded-md border border-[color:var(--sig-line-2)] bg-transparent px-3 text-sm focus:outline-none focus:border-[color:var(--sig-accent)]"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as KnowledgeTrainingMode)}
                  disabled={generating}
                >
                  {MODE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
                <select
                  className="h-9 rounded-md border border-[color:var(--sig-line-2)] bg-transparent px-3 text-sm focus:outline-none focus:border-[color:var(--sig-accent)]"
                  value={depth}
                  onChange={(e) => setDepth(e.target.value as KnowledgeTrainingDepth)}
                  disabled={generating}
                >
                  {DEPTH_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </>
            )}
            <Button onClick={startSession} disabled={!canStart} size="sm" className="shrink-0">
              {generating ? (
                <Loader2 className="animate-spin" />
              ) : studyMode === "review" ? (
                <RotateCcw />
              ) : studyMode === "saved" ? (
                <Layers />
              ) : (
                <Play />
              )}
              {studyMode === "review" ? "开始复习" : studyMode === "saved" ? "刷新已保存" : "开始训练"}
            </Button>
          </div>
        </div>
      </header>

      <div className="border-b border-border/60 px-4 py-2 md:px-6" style={{ background: "color-mix(in srgb, var(--sig-fg) 3%, transparent)" }}>
        <div className="flex flex-wrap items-center gap-3 text-xs text-dim">
          {selectedInfo ? (
            <>
              <span className="flex items-center gap-1.5 text-text">
                <span className="text-dim">{getTopicIcon(selectedInfo.icon, 14)}</span>
                {selectedInfo.name}
              </span>
              <span className="flex items-center gap-1"><FileText size={13} /> {selectedInfo.file_count} 文件</span>
              <span className="flex items-center gap-1"><Layers size={13} /> {selectedInfo.chunk_count} chunks</span>
              {selectedInfo.chunk_count === 0 && selectedInfo.file_count > 0 && (
                <Badge variant="outline" className="text-[10px]">索引未完成，仍可从原文抽样</Badge>
              )}
            </>
          ) : (
            <span>暂无知识库模块</span>
          )}
          {progress && <span className="flex items-center gap-1 text-primary"><Loader2 size={13} className="animate-spin" /> {progress}</span>}
        </div>
      </div>

      <main className="grid flex-1 min-h-0 gap-4 overflow-y-auto p-4 md:p-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0">
          {error && (
            <div className="mb-4 flex items-start gap-2 rounded-md border border-red/30 bg-red/10 px-3 py-2 text-sm text-red">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {!currentCard ? (
            <Card interactive={false} className="min-h-[420px]">
              <CardContent className="flex min-h-[420px] flex-col items-center justify-center p-6 text-center">
                {loadingTopics ? (
                  <Loader2 className="mb-3 h-6 w-6 animate-spin text-primary" />
                ) : studyMode === "review" ? (
                  <>
                    <RotateCcw className="mb-3 h-9 w-9 text-primary" />
                    <h2 className="mb-2 text-base font-semibold">到期复习 · 遗忘曲线</h2>
                    <p className="max-w-md text-sm leading-6 text-dim">
                      已标记的卡片按 SM-2 间隔重复安排复习，到期后回到这里。
                      {dueCount > 0
                        ? `当前 ${dueCount} 张到期。`
                        : "当前没有到期卡片，先去「新卡生成」标记熟悉度。"}
                    </p>
                    <Button className="mt-5" onClick={startReview} disabled={!canStart}>
                      {generating ? <Loader2 className="animate-spin" /> : <RotateCcw />}
                      开始复习
                    </Button>
                  </>
                ) : studyMode === "saved" ? (
                  <>
                    <Layers className="mb-3 h-9 w-9 text-primary" />
                    <h2 className="mb-2 text-base font-semibold">已保存卡片 · 跨设备同步</h2>
                    <p className="max-w-md text-sm leading-6 text-dim">
                      生成过的卡片都存在服务器，换浏览器或设备登录同一账户都能在这里找回并继续标记。
                      {savedCount > 0
                        ? `当前模块已保存 ${savedCount} 张。`
                        : "当前模块还没有已保存的卡片，先去「新卡生成」生成一组。"}
                    </p>
                    <Button className="mt-5" onClick={startSaved} disabled={!canStart}>
                      {generating ? <Loader2 className="animate-spin" /> : <Layers />}
                      加载已保存
                    </Button>
                  </>
                ) : selectedInfo?.available ? (
                  <>
                    <Brain className="mb-3 h-9 w-9 text-primary" />
                    <h2 className="mb-2 text-base font-semibold">选择配置后开始本轮记忆训练</h2>
                    <p className="max-w-md text-sm leading-6 text-dim">
                      卡片从当前知识库原文抽样生成（自动避开已出过的知识点），答案默认隐藏，
                      熟悉度标记会保存并按遗忘曲线安排复习。
                    </p>
                    <Button className="mt-5" onClick={startTraining} disabled={!canStart}>
                      {generating ? <Loader2 className="animate-spin" /> : <Play />}
                      开始训练
                    </Button>
                  </>
                ) : (
                  <>
                    <FileText className="mb-3 h-9 w-9 text-dim" />
                    <h2 className="mb-2 text-base font-semibold">当前模块没有可训练知识文件</h2>
                    <p className="max-w-md text-sm leading-6 text-dim">
                      请先到知识库上传 Markdown / 文本文件，或生成基础知识后再开始训练。
                    </p>
                  </>
                )}
              </CardContent>
            </Card>
          ) : (
            <Card interactive={false}>
              <CardContent className="p-4 md:p-6">
                <div className="mb-5 flex flex-col gap-3 border-b border-border pb-4 md:flex-row md:items-start md:justify-between">
                  <div className="min-w-0">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {currentIndex + 1}/{cards.length}
                      </Badge>
                      <Badge variant="outline" className={cn("text-[10px]", familiarityClass(familiarity[currentCard.id]))}>
                        {familiarityLabel(familiarity[currentCard.id])}
                      </Badge>
                      {currentCard.tags.map((tag) => (
                        <Badge key={tag} variant="blue" className="text-[10px]">{tag}</Badge>
                      ))}
                    </div>
                    <h2 className="text-xl font-semibold leading-tight tracking-normal">{currentCard.title}</h2>
                    <div className="mt-2 flex items-center gap-1.5 text-xs text-dim">
                      <FileText size={13} />
                      <span className="truncate">{sourceLabel(currentCard)}</span>
                    </div>
                  </div>
                  <Button variant="outline" size="sm" onClick={startSession} disabled={generating} className="shrink-0">
                    <RefreshCw />
                    {studyMode === "review" ? "刷新到期" : studyMode === "saved" ? "刷新" : "换一组"}
                  </Button>
                </div>

                <div className="space-y-5">
                  <section>
                    <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
                      <Brain size={15} className="text-primary" />
                      核心知识
                    </div>
                    <div className="rounded-md border border-border/60 bg-secondary/30 px-3 py-3">
                      <CardMarkdown>{normalizeKnowledgeMarkdown(currentCard.knowledge)}</CardMarkdown>
                    </div>
                  </section>

                  <section className="rounded-md border border-border/70 bg-secondary/20 p-3">
                    <div className="mb-1.5 text-sm font-semibold">例子</div>
                    <CardMarkdown>{currentCard.example}</CardMarkdown>
                  </section>

                  <section className="rounded-md border border-primary/35 bg-primary/5 p-3">
                    <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold">
                      <HelpCircle size={15} className="text-primary" />
                      自测问题
                    </div>
                    <CardMarkdown className="text-text">{currentCard.question}</CardMarkdown>
                  </section>

                  <section className="rounded-md border border-border/70 p-3">
                    <div className="mb-3 flex items-center justify-between gap-2">
                      <div className="text-sm font-semibold">参考答案</div>
                      <Button variant="outline" size="sm" onClick={revealCurrent}>
                        {revealedAnswers[currentCard.id] ? <EyeOff /> : <Eye />}
                        {revealedAnswers[currentCard.id] ? "隐藏答案" : "查看答案"}
                      </Button>
                    </div>
                    {revealedAnswers[currentCard.id] ? (
                      <CardMarkdown>{currentCard.answer}</CardMarkdown>
                    ) : (
                      <div className="rounded-md bg-secondary/40 px-3 py-5 text-center text-sm text-dim">
                        答案已隐藏，先在脑中复述后再查看。
                      </div>
                    )}
                  </section>
                </div>

                <div className="mt-6 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => setCurrentIndex((i) => Math.max(0, i - 1))} disabled={currentIndex === 0}>
                      <ChevronLeft /> 上一张
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setCurrentIndex((i) => Math.min(cards.length - 1, i + 1))} disabled={currentIndex >= cards.length - 1}>
                      下一张 <ChevronRight />
                    </Button>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button variant={familiarity[currentCard.id] === "known" ? "default" : "outline"} size="sm" onClick={() => setCardFamiliarity("known")}>
                      <CheckCircle2 /> 已掌握
                    </Button>
                    <Button variant={familiarity[currentCard.id] === "uncertain" ? "default" : "outline"} size="sm" onClick={() => setCardFamiliarity("uncertain")}>
                      <HelpCircle /> 模糊
                    </Button>
                    <Button variant={familiarity[currentCard.id] === "unknown" ? "default" : "outline"} size="sm" onClick={() => setCardFamiliarity("unknown")}>
                      <XCircle /> 不会
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </section>

        <aside className="min-w-0 xl:sticky xl:top-0 xl:self-start">
          <Card interactive={false}>
            <CardContent className="p-4">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="text-sm font-semibold">
                  {studyMode === "review" ? "到期卡片" : studyMode === "saved" ? "已保存卡片" : "本轮卡片"}
                </div>
                <Badge variant="outline" className="font-mono text-[10px]">
                  {studyMode === "new" ? `${cards.length}/${count}` : cards.length}
                </Badge>
              </div>
              <div className="mb-4 grid grid-cols-3 gap-2 text-center text-xs">
                <div className="rounded-md border border-green/30 bg-green/10 px-2 py-2 text-green">
                  <div className="font-mono text-base">{markedStats.known}</div>
                  <div>已掌握</div>
                </div>
                <div className="rounded-md border border-yellow-500/30 bg-yellow-500/10 px-2 py-2 text-yellow-600 dark:text-yellow-400">
                  <div className="font-mono text-base">{markedStats.uncertain}</div>
                  <div>模糊</div>
                </div>
                <div className="rounded-md border border-red/30 bg-red/10 px-2 py-2 text-red">
                  <div className="font-mono text-base">{markedStats.unknown}</div>
                  <div>不会</div>
                </div>
              </div>

              {cards.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-3 py-8 text-center text-sm text-dim">
                  生成后会显示卡片目录。
                </div>
              ) : (
                <div className="max-h-[52vh] space-y-1.5 overflow-y-auto pr-1">
                  {cards.map((card, idx) => (
                    <button
                      key={card.id}
                      className={cn(
                        "w-full rounded-md border px-3 py-2 text-left text-sm transition-colors",
                        idx === currentIndex ? "border-primary bg-primary/10" : "border-border bg-secondary/20 hover:bg-secondary/50",
                      )}
                      onClick={() => setCurrentIndex(idx)}
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[11px] text-dim">{idx + 1}</span>
                        <span className="min-w-0 flex-1 truncate">{card.title}</span>
                      </div>
                      <div className="mt-1 text-[11px] text-dim">{familiarityLabel(familiarity[card.id])}</div>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </aside>
      </main>
    </div>
  );
}
