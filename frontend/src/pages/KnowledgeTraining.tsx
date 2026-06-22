import { useCallback, useEffect, useMemo, useState } from "react";
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
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { getTopicIcon } from "../utils/topicIcons";
import {
  generateKnowledgeTrainingCards,
  getKnowledgeTrainingAvailability,
  type KnowledgeTrainingAvailabilityItem,
  type KnowledgeTrainingCard,
  type KnowledgeTrainingDepth,
  type KnowledgeTrainingMode,
} from "../api/knowledgeTraining";

type Familiarity = "known" | "uncertain" | "unknown";

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

function splitKnowledge(text: string): string[] {
  const lines = text
    .split(/\n+/)
    .map((line) => line.replace(/^[-*•\d.)、\s]+/, "").trim())
    .filter(Boolean);
  return lines.length > 1 ? lines : [text.trim()].filter(Boolean);
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

  const topicKeys = Object.keys(availability);
  const selectedInfo = selected ? availability[selected] : null;
  const currentCard = cards[currentIndex] || null;
  const familiarityKey = selected ? `knowledge-training:familiarity:${selected}` : "";

  const loadAvailability = useCallback(async () => {
    setLoadingTopics(true);
    try {
      const data = await getKnowledgeTrainingAvailability();
      setAvailability(data.topics);
      const keys = Object.keys(data.topics);
      setSelected((prev) => (prev && data.topics[prev] ? prev : keys[0] || ""));
    } catch (e: any) {
      setError(e.message || "加载知识库模块失败");
    } finally {
      setLoadingTopics(false);
    }
  }, []);

  useEffect(() => {
    loadAvailability();
  }, [loadAvailability]);

  useEffect(() => {
    if (!familiarityKey) return;
    try {
      setFamiliarity(JSON.parse(localStorage.getItem(familiarityKey) || "{}"));
    } catch {
      setFamiliarity({});
    }
    setCards([]);
    setCurrentIndex(0);
    setRevealedAnswers({});
    setError("");
  }, [familiarityKey]);

  useEffect(() => {
    if (familiarityKey) localStorage.setItem(familiarityKey, JSON.stringify(familiarity));
  }, [familiarity, familiarityKey]);

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
      setProgress("");
      toast.success(`已生成 ${result.total} 张训练卡片`);
    } catch (e: any) {
      setError(e.message || "训练卡片生成失败");
      setProgress("");
    } finally {
      setGenerating(false);
    }
  }, [count, depth, generating, mode, selected]);

  const setCardFamiliarity = (value: Familiarity) => {
    if (!currentCard) return;
    setFamiliarity((prev) => ({ ...prev, [currentCard.id]: value }));
  };

  const revealCurrent = () => {
    if (!currentCard) return;
    setRevealedAnswers((prev) => ({ ...prev, [currentCard.id]: !prev[currentCard.id] }));
  };

  const canGenerate = !!selected && !generating && !!selectedInfo?.available;

  return (
    <div className="flex flex-1 min-h-0 flex-col overflow-hidden">
      <header className="border-b border-border px-4 py-3 md:px-6" style={{ background: "var(--sig-bg)" }}>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="sig-kicker mb-1">// KNOWLEDGE TRAINING</div>
            <h1 className="text-xl font-semibold tracking-normal">知识训练场</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="h-9 min-w-[150px] rounded-md border border-[color:var(--sig-line-2)] bg-transparent px-3 text-sm focus:outline-none focus:border-[color:var(--sig-accent)]"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              disabled={loadingTopics || generating}
            >
              {topicKeys.map((key) => (
                <option key={key} value={key}>{availability[key]?.name || key}</option>
              ))}
            </select>
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
            <Button onClick={startTraining} disabled={!canGenerate} size="sm" className="shrink-0">
              {generating ? <Loader2 className="animate-spin" /> : <Play />}
              开始训练
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
                ) : selectedInfo?.available ? (
                  <>
                    <Brain className="mb-3 h-9 w-9 text-primary" />
                    <h2 className="mb-2 text-base font-semibold">选择配置后开始本轮记忆训练</h2>
                    <p className="max-w-md text-sm leading-6 text-dim">
                      卡片会从当前知识库原文抽样生成，答案默认隐藏，本轮标记只保存在本地。
                    </p>
                    <Button className="mt-5" onClick={startTraining} disabled={!canGenerate}>
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
                  <Button variant="outline" size="sm" onClick={startTraining} disabled={generating} className="shrink-0">
                    <RefreshCw />
                    换一组
                  </Button>
                </div>

                <div className="space-y-5">
                  <section>
                    <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
                      <Brain size={15} className="text-primary" />
                      核心知识
                    </div>
                    <div className="space-y-2">
                      {splitKnowledge(currentCard.knowledge).map((item, idx) => (
                        <div key={idx} className="flex gap-2 rounded-md border border-border/60 bg-secondary/30 px-3 py-2 text-sm leading-6">
                          <span className="font-mono text-xs text-primary">{String(idx + 1).padStart(2, "0")}</span>
                          <span>{item}</span>
                        </div>
                      ))}
                    </div>
                  </section>

                  <section className="rounded-md border border-border/70 bg-secondary/20 p-3">
                    <div className="mb-1.5 text-sm font-semibold">例子</div>
                    <p className="text-sm leading-7 text-text/90">{currentCard.example}</p>
                  </section>

                  <section className="rounded-md border border-primary/35 bg-primary/5 p-3">
                    <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold">
                      <HelpCircle size={15} className="text-primary" />
                      自测问题
                    </div>
                    <p className="text-sm leading-7">{currentCard.question}</p>
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
                      <p className="text-sm leading-7 text-text/90">{currentCard.answer}</p>
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
                <div className="text-sm font-semibold">本轮卡片</div>
                <Badge variant="outline" className="font-mono text-[10px]">{cards.length}/{count}</Badge>
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
