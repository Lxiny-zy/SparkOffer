import React, { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useLocation, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { markdownComponents } from "../components/ChatBubble";
import { Check, Minus, Star, Lightbulb, Eye, Loader2 } from "lucide-react";
import ChatBubble from "../components/ChatBubble";
import { sendMessage, endInterview, getReferenceAnswer, getInterviewSession, saveDrillProgress } from "../api/interview";
import useVoiceInput from "../hooks/useVoiceInput";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Question, ChatMessage } from "../types/api";

interface HintState {
  stage: "none" | "hint" | "full";
  hint?: string;
  full?: string;
}

export default function Interview() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const initData: any = location.state || {};
  const [restoredMeta, setRestoredMeta] = useState<any>(null);
  const effectiveInit: any = initData.mode ? initData : (restoredMeta || {});
  const isBatchMode = effectiveInit.mode === "topic_drill" || effectiveInit.mode === "jd_prep";
  const isJobPrep = effectiveInit.mode === "jd_prep";

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendProgress, setSendProgress] = useState("");
  const [finished, setFinished] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [progress, setProgress] = useState(initData.progress || "");

  const [questions, setQuestions] = useState<Question[]>(initData.questions || []);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [drillInput, setDrillInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [evalProgress, setEvalProgress] = useState("");
  const [hints, setHints] = useState<Record<number, HintState>>({});
  const [hintLoading, setHintLoading] = useState(false);
  const [showEndConfirm, setShowEndConfirm] = useState(false);
  const [restoring, setRestoring] = useState<boolean>(!initData.mode);
  const restoredRef = useRef(false);
  const saveTimerRef = useRef<number | null>(null);

  const drillVoice = useVoiceInput({
    onResult: useCallback((text: string) => setDrillInput((prev) => prev + text), []),
  });
  const chatVoice = useVoiceInput({
    onResult: useCallback((text: string) => setInput((prev) => prev + text), []),
  });

  useEffect(() => {
    if (!isBatchMode && initData.message) {
      setMessages([{ role: "assistant", content: initData.message }]);
    }
  }, []);

  // Restore from backend when location.state is empty (page refresh / direct visit)
  useEffect(() => {
    if (initData.mode || !sessionId || restoredRef.current) return;
    restoredRef.current = true;
    setRestoring(true);
    getInterviewSession(sessionId)
      .then((sess: any) => {
        const meta = sess.meta || {};
        const progress = meta.progress || {};
        setRestoredMeta({
          mode: sess.mode,
          topic: sess.topic,
          questions: sess.questions || [],
          company: meta.company,
          position: meta.position,
          meta,
        });
        if (sess.questions?.length) setQuestions(sess.questions);
        if (progress.partial_answers) {
          const parsed: Record<number, string> = {};
          for (const [k, v] of Object.entries(progress.partial_answers)) {
            parsed[Number(k)] = v as string;
          }
          setAnswers(parsed);
        }
        if (typeof progress.current_index === "number") setCurrentIndex(progress.current_index);
        if (progress.hints) {
          const parsedH: Record<number, HintState> = {};
          for (const [k, v] of Object.entries(progress.hints)) {
            parsedH[Number(k)] = v as HintState;
          }
          setHints(parsedH);
        }
        // Restore last typed draft into drillInput
        const restoredAnswers = progress.partial_answers || {};
        const qList = sess.questions || [];
        const idx = progress.current_index ?? 0;
        const qid = qList[idx]?.id;
        if (qid != null && restoredAnswers[String(qid)]) {
          setDrillInput(restoredAnswers[String(qid)]);
        }
        // Resume mode: replay transcript
        if (sess.mode === "resume" && Array.isArray(sess.transcript)) {
          setMessages(sess.transcript.map((m: any) => ({ role: m.role, content: m.content })));
        }
      })
      .catch((err: any) => {
        console.error("恢复面试失败:", err);
      })
      .finally(() => setRestoring(false));
  }, [sessionId, initData.mode]);

  // Debounced persist of in-progress state
  useEffect(() => {
    if (!isBatchMode || !sessionId || restoring || finished) return;
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      saveDrillProgress(sessionId, {
        current_index: currentIndex,
        partial_answers: answers,
        hints,
      }).catch(() => {});
    }, 400);
    return () => {
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    };
  }, [currentIndex, answers, hints, sessionId, isBatchMode, restoring, finished]);

  // Sync drillInput → answers so the draft survives refresh
  useEffect(() => {
    if (!isBatchMode || restoring) return;
    const q = questions[currentIndex];
    if (!q) return;
    setAnswers((prev) => {
      if (prev[q.id] === drillInput) return prev;
      return { ...prev, [q.id]: drillInput };
    });
  }, [drillInput, currentIndex, questions, isBatchMode, restoring]);

  useEffect(() => {
    if (!isBatchMode) chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending, isBatchMode]);

  useEffect(() => {
    if (isBatchMode) textareaRef.current?.focus();
  }, [currentIndex, isBatchMode]);

  const currentQ = questions[currentIndex];
  const totalQ = questions.length;
  const answeredCount = Object.keys(answers).length;

  const handleDrillSubmit = () => {
    const text = drillInput.trim();
    if (!text || !currentQ) return;
    setAnswers((prev) => ({ ...prev, [currentQ.id]: text }));
    setDrillInput("");
    if (currentIndex < totalQ - 1) setCurrentIndex((i) => i + 1);
    else setFinished(true);
  };

  const handleSkip = () => {
    if (!currentQ) return;
    setDrillInput("");
    if (currentIndex < totalQ - 1) setCurrentIndex((i) => i + 1);
    else setFinished(true);
  };

  const handlePrev = () => {
    if (currentIndex <= 0) return;
    setDrillInput(answers[questions[currentIndex - 1]?.id] || "");
    setCurrentIndex((i) => i - 1);
  };

  const handleHint = async () => {
    if (!currentQ || hintLoading) return;
    const qid = currentQ.id;
    const current: HintState = hints[qid] || { stage: "none" };
    const nextMode = current.stage === "none" ? "hint" : "full";
    if (current.stage === "full") return;
    if ((current as any)[nextMode]) {
      setHints((p) => ({ ...p, [qid]: { ...p[qid], stage: nextMode } }));
      return;
    }
    setHintLoading(true);
    try {
      const topic = effectiveInit.topic || "";
      const data = await getReferenceAnswer(topic, currentQ.question, sessionId, Number(qid), false, nextMode);
      setHints((p) => ({
        ...p,
        [qid]: { ...p[qid], [nextMode]: data.reference_answer, stage: nextMode },
      }));
    } catch (e) {
      console.error("获取提示失败:", e);
    } finally {
      setHintLoading(false);
    }
  };

  const handleEndBatch = async () => {
    setShowEndConfirm(false);
    setSubmitting(true);
    setEvalProgress("");
    try {
      const answerList = questions.map((q) => ({
        question_id: q.id,
        answer: answers[q.id] || "",
      }));
      const data = await endInterview(sessionId, answerList, {
        onProgress: (msg) => setEvalProgress(msg),
      });
      navigate(`/review/${sessionId}`, {
        state: {
          review: data.review,
          scores: data.scores,
          overall: data.overall,
          questions,
          answers: answerList,
          mode: effectiveInit.mode,
          topic: effectiveInit.topic,
          company: effectiveInit.company,
          position: effectiveInit.position,
          meta: data.meta || effectiveInit.meta,
        },
      });
    } catch (err: any) {
      alert("评估失败: " + err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setSending(true);
    setSendProgress("");
    try {
      const data = await sendMessage(sessionId, text, {
        onProgress: (msg) => setSendProgress(msg),
      });
      setMessages((prev) => [...prev, { role: "assistant", content: data.message }]);
      if (data.progress) setProgress(data.progress);
      if (data.is_finished) setFinished(true);
    } catch (err: any) {
      setMessages((prev) => [...prev, { role: "assistant", content: `[错误] ${err.message}` }]);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  };

  const handleEndResume = async () => {
    setShowEndConfirm(false);
    setReviewing(true);
    setEvalProgress("");
    try {
      const data = await endInterview(sessionId, null, {
        onProgress: (msg) => setEvalProgress(msg),
      });
      navigate(`/review/${sessionId}`, {
        state: {
          review: data.review,
          messages,
          mode: "resume",
          dimension_scores: data.dimension_scores,
          avg_score: data.avg_score,
        },
      });
    } catch (err: any) {
      alert("复盘生成失败: " + err.message);
    } finally {
      setReviewing(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !(e.nativeEvent as any).isComposing) {
      e.preventDefault();
      isBatchMode ? handleDrillSubmit() : handleSend();
    }
  };

  const modeBadge = isJobPrep
    ? { text: "JD 备面", variant: "blue" }
    : effectiveInit.mode === "topic_drill"
      ? { text: "专项训练", variant: "success" }
      : { text: "简历面试", variant: "default" };

  const MicButton = ({ voice }: { voice: any }) => (
    <button
      type="button"
      className={cn(
        "w-9 h-9 rounded-full flex items-center justify-center transition-all shrink-0",
        voice.isListening ? "bg-red text-white animate-pulse-dot" : voice.isTranscribing ? "bg-primary text-white animate-pulse-dot" : "bg-secondary text-muted-fg hover:text-text"
      )}
      onClick={voice.toggle}
      disabled={voice.isTranscribing}
      title={voice.isListening ? "停止录音" : voice.isTranscribing ? "正在识别..." : "语音输入"}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
        <line x1="12" y1="19" x2="12" y2="23"/>
        <line x1="8" y1="23" x2="16" y2="23"/>
      </svg>
    </button>
  );

  if (restoring) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    );
  }

  if (isBatchMode) {
    return (
      <div className="flex-1 flex flex-col h-full">
        <div className="flex items-center justify-between px-3 py-2.5 md:px-6 md:py-3 border-b border-border/50 bg-card/70 backdrop-blur-sm flex-wrap gap-2">
          <div className="flex items-center gap-2 md:gap-3 flex-wrap min-w-0">
            <Badge variant={modeBadge.variant as any}>{modeBadge.text}</Badge>
            {isJobPrep
              ? (
                <span className="text-sm text-dim truncate">
                  {effectiveInit.company ? `${effectiveInit.company} · ` : ""}{effectiveInit.position || "目标岗位"}
                </span>
              )
              : effectiveInit.topic && <span className="text-sm text-dim truncate">{effectiveInit.topic}</span>}
            <span className="text-[13px] text-dim whitespace-nowrap">{answeredCount}/{totalQ} 已答</span>
          </div>
          <Button variant="destructive" size="sm" onClick={() => finished ? handleEndBatch() : setShowEndConfirm(true)} disabled={submitting} className="shrink-0">
            {submitting ? "评估中..." : finished ? "查看评估" : isJobPrep ? "结束备面" : "结束训练"}
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-4 md:px-6 md:py-8 flex flex-col items-center gap-4 md:gap-5">
          {submitting ? (
            <div className="w-full max-w-[720px] flex flex-col items-center justify-center gap-4 py-15 text-dim text-base">
              <div className="flex gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot" />
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.2s]" />
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.4s]" />
              </div>
              <span>{isJobPrep ? "正在生成岗位匹配复盘..." : "正在批量评估你的回答..."}</span>
              <span className="text-[13px] text-dim opacity-60">
                {evalProgress || (isJobPrep ? "AI 会结合 JD 判断你的真实匹配度" : `AI 将对 ${totalQ} 道题逐一点评`)}
              </span>
            </div>
          ) : finished ? (
            <div className="w-full max-w-[720px]">
              <Card className="mb-5">
                <CardContent className="p-6 md:p-8 text-center">
                  <div className="text-xl font-semibold mb-3">{isJobPrep ? "定向备面完成" : "训练完成"}</div>
                  <div className="text-[15px] text-dim mb-6 leading-relaxed">
                    共 {totalQ} 题，已回答 {answeredCount} 题，跳过 {totalQ - answeredCount} 题
                  </div>
                  <Button variant="default" size="lg" className="px-10" onClick={handleEndBatch}>
                    提交评估
                  </Button>
                </CardContent>
              </Card>
              <div className="flex flex-col gap-1.5">
                {questions.map((q) => (
                  <div key={q.id} className="flex items-center gap-2 px-3 py-2 bg-secondary rounded-lg text-[13px] text-dim">
                    {answers[q.id]
                      ? <Check size={14} className="text-green" />
                      : <Minus size={14} className="text-dim opacity-50" />}
                    <span>Q{q.id}: {q.question.slice(0, 60)}{q.question.length > 60 ? "..." : ""}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : currentQ ? (
            <>
              <div className="w-full max-w-[720px] flex items-center gap-3">
                <div className="flex-1 h-1 rounded-full bg-border overflow-hidden">
                  <div className="h-full rounded-full bg-primary transition-[width] duration-300 ease-in-out" style={{ width: `${(currentIndex / totalQ) * 100}%` }} />
                </div>
                <span className="text-[12px] text-dim whitespace-nowrap font-mono">{currentIndex + 1} / {totalQ}</span>
              </div>

              {/* L1 — 题号 + 分类侧边栏 */}
              <div className="w-full max-w-[720px] flex gap-3 md:gap-5 animate-fade-in">
                <div className="hidden md:flex flex-col items-center gap-2 pt-1 shrink-0">
                  <span className="text-[10px] font-mono uppercase tracking-widest text-dim/60">Q{String(currentQ.id).padStart(2, "0")}</span>
                  {currentQ.difficulty && (
                    <div className="flex flex-col items-center gap-0.5">
                      {Array.from({ length: 5 }, (_, i) => (
                        <Star key={i} size={8} className={i < currentQ.difficulty! ? "text-primary fill-primary" : "text-border"} />
                      ))}
                    </div>
                  )}
                </div>

                {/* L2 — 题目主卡（站 C 位） */}
                <Card className="flex-1 min-w-0 card-hover-lift question-hero">
                  <CardContent className="p-5 md:p-8">
                    {/* 顶栏：标签 */}
                    <div className="flex items-center gap-2 flex-wrap mb-5 md:mb-6">
                      {currentQ.category && (
                        <Badge variant={isJobPrep ? "blue" as any : "secondary"}>{currentQ.category}</Badge>
                      )}
                      {currentQ.focus_area && (
                        <Badge variant="outline" className="text-dim border-border/60">{currentQ.focus_area}</Badge>
                      )}
                      {currentQ.difficulty && (
                        <div className="md:hidden flex items-center gap-0.5 ml-auto">
                          {Array.from({ length: 5 }, (_, i) => (
                            <Star key={i} size={11} className={i < currentQ.difficulty! ? "text-primary fill-primary" : "text-dim"} />
                          ))}
                        </div>
                      )}
                    </div>

                    {/* L3 — 题目正文（最大最醒目） */}
                    <h2 className="text-lg md:text-[22px] font-bold leading-[1.65] text-text tracking-tight mb-4">
                      <div className="md-content">
                        <ReactMarkdown components={markdownComponents}>{currentQ.question}</ReactMarkdown>
                      </div>
                    </h2>

                    {/* L4 — 面试官视角（仅 JD 模式） */}
                    {isJobPrep && currentQ.intent && (
                      <div className="rounded-xl bg-tertiary/8 border border-tertiary/15 px-4 py-3 text-sm leading-relaxed text-dim">
                        <span className="text-tertiary font-semibold text-xs uppercase tracking-wider mr-1.5">面试官在看什么</span>
                        {currentQ.intent}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              {/* Hint / Reference Answer */}
              {(() => {
                const hintState: HintState = hints[currentQ.id] || { stage: "none" };
                const content = hintState.stage === "full" ? hintState.full : hintState.hint;
                return (
                  <div className="w-full max-w-[720px] flex flex-col gap-2">
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-dim hover:text-primary gap-1.5"
                        disabled={hintLoading || hintState.stage === "full"}
                        onClick={handleHint}
                      >
                        {hintLoading ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : hintState.stage === "none" ? (
                          <Lightbulb size={14} />
                        ) : (
                          <Eye size={14} />
                        )}
                        {hintState.stage === "none" ? "查看提示" : hintState.stage === "hint" ? "查看参考答案" : "已显示参考答案"}
                      </Button>
                    </div>
                    {content && (
                      <div className={`rounded-xl px-4 py-3 text-sm leading-relaxed animate-fade-in ${
                        hintState.stage === "hint"
                          ? "bg-yellow-500/8 border border-yellow-500/20 text-dim"
                          : "bg-primary/8 border border-primary/20"
                      }`}>
                        <div className="flex items-center gap-1.5 text-xs font-semibold mb-1.5 opacity-70">
                          {hintState.stage === "hint" ? (
                            <><Lightbulb size={12} /> 提示</>
                          ) : (
                            <><Eye size={12} /> 参考答案</>
                          )}
                        </div>
                        <div className="md-content">
                          <ReactMarkdown components={markdownComponents}>{content}</ReactMarkdown>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

              <div className="w-full max-w-[720px] flex flex-col gap-3 py-2">
                <div className="flex-1 relative">
                  <textarea
                    ref={textareaRef}
                    className="w-full min-h-[100px] md:min-h-[80px] max-h-[240px] px-4 py-3 rounded-xl border border-border bg-input text-text resize-none text-[15px] md:text-sm leading-relaxed pl-12 placeholder:text-dim/50 focus-visible:outline-none focus-visible:border-primary focus-visible:ring-1 focus-visible:ring-primary/30"
                    value={drillInput}
                    onChange={(e) => setDrillInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={drillVoice.isListening ? "正在录音..." : drillVoice.isTranscribing ? "正在识别语音..." : "输入你的回答... (Enter 提交)"}
                    rows={3}
                  />
                  {drillVoice.isSupported && (
                    <div className="absolute bottom-3 left-3">
                      <MicButton voice={drillVoice} />
                    </div>
                  )}
                </div>
                <div className="flex items-center justify-between sm:justify-end gap-3">
                  <Button variant="ghost" size="sm" onClick={handleSkip} className="text-[13px]">
                    跳过
                  </Button>
                  <Button variant="default" className="px-6 py-3 md:px-7 md:py-3.5 text-[14px] md:text-[15px] flex-1 sm:flex-none" disabled={!drillInput.trim()} onClick={handleDrillSubmit}>
                    {currentIndex < totalQ - 1 ? "下一题" : "完成"}
                  </Button>
                </div>
              </div>

              {currentIndex > 0 && (
                <div className="w-full max-w-[720px]">
                  <button className="py-1.5 text-dim text-[13px] hover:text-text transition-colors cursor-pointer" onClick={handlePrev}>
                    ← 上一题
                  </button>
                </div>
              )}
            </>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 md:px-6 border-b border-border/50 bg-card/70 backdrop-blur-sm">
        <div className="flex items-center gap-2 md:gap-3 flex-wrap">
          <Badge variant={modeBadge.variant as any}>{modeBadge.text}</Badge>
          {effectiveInit.topic && <span className="text-sm text-dim">{effectiveInit.topic}</span>}
          {progress && (
            <span className="text-[13px] text-dim flex items-center gap-1.5">
              <span className="text-border">|</span>
              进度: {progress}
            </span>
          )}
        </div>
        <Button variant="destructive" size="sm" onClick={() => finished ? handleEndResume() : setShowEndConfirm(true)} disabled={reviewing}>
          {reviewing ? (evalProgress || "生成复盘中...") : finished ? "查看复盘" : "结束面试"}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-6 md:px-6 md:py-8 flex flex-col gap-7 max-w-3xl w-full mx-auto">
        {messages.map((msg, i) => (
          <ChatBubble key={i} role={msg.role} content={msg.content} />
        ))}
        {sending && (
          <div className="flex items-center gap-2 px-4 py-3 text-dim text-sm">
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot" />
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.2s]" />
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.4s]" />
            <span className="ml-1">{sendProgress || "面试官思考中..."}</span>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

        <div className="px-3 pt-3 pb-4 md:px-6 md:pt-4 md:pb-6 flex justify-center safe-area-bottom border-t border-border/50 backdrop-blur-sm bg-card/30">
          <div className="relative w-full max-w-3xl">
            <textarea
              ref={textareaRef}
              className="w-full px-4 py-3.5 md:px-5 md:py-4 pl-12 min-h-[72px] md:min-h-[80px] max-h-[200px] md:max-h-[240px] rounded-2xl border border-border bg-bg/80 backdrop-blur-sm text-text resize-none text-[15px] leading-normal placeholder:text-dim/50 focus-visible:outline-none focus-visible:border-primary/40 focus-visible:ring-2 focus-visible:ring-primary/30 transition-all"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={chatVoice.isListening ? "正在录音..." : finished ? "面试已结束" : "输入你的回答... (Enter 发送)"}
              disabled={finished || sending}
              rows={3}
            />
            {chatVoice.isSupported && !finished && (
              <div className="absolute bottom-3.5 left-3">
                <MicButton voice={chatVoice} />
              </div>
            )}
          </div>
        </div>
    {/* End confirmation dialog */}
    {showEndConfirm && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
        <Card className="w-full max-w-sm mx-4 animate-fade-in">
          <CardContent className="p-5 text-center space-y-4">
            <h3 className="text-lg font-semibold">
              {isBatchMode ? (isJobPrep ? "确定结束备面？" : "确定结束训练？") : "确定结束面试？"}
            </h3>
            <p className="text-sm text-dim">
              {isBatchMode
                ? `已完成 ${Object.keys(answers).filter((k) => answers[Number(k)]).length}/${questions.length} 题，结束后将进入 AI 评估。`
                : "结束后将生成面试复盘报告。"}
            </p>
            <div className="flex justify-center gap-3 pt-2">
              <Button variant="outline" onClick={() => setShowEndConfirm(false)}>继续答题</Button>
              <Button variant="destructive" onClick={isBatchMode ? handleEndBatch : handleEndResume}>
                确认结束
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )}
    </div>
  );
}
