import React, { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useLocation, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { Check, Minus, Star, Lightbulb, Eye, Loader2 } from "lucide-react";
import ChatBubble from "../components/ChatBubble";
import { sendMessage, endInterview, getReferenceAnswer } from "../api/interview";
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
  const isBatchMode = initData.mode === "topic_drill" || initData.mode === "jd_prep";
  const isJobPrep = initData.mode === "jd_prep";

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendProgress, setSendProgress] = useState("");
  const [finished, setFinished] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [progress, setProgress] = useState(initData.progress || "");

  const [questions] = useState<Question[]>(initData.questions || []);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [drillInput, setDrillInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [evalProgress, setEvalProgress] = useState("");
  const [hints, setHints] = useState<Record<number, HintState>>({});
  const [hintLoading, setHintLoading] = useState(false);

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
      const topic = initData.topic || "";
      const data = await getReferenceAnswer(topic, currentQ.question, sessionId, String(qid), false, nextMode);
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
          mode: initData.mode,
          topic: initData.topic,
          company: initData.company,
          position: initData.position,
          meta: data.meta || initData.meta,
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
    : initData.mode === "topic_drill"
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

  if (isBatchMode) {
    return (
      <div className="flex-1 flex flex-col h-full">
        <div className="flex items-center justify-between px-4 py-3 md:px-6 border-b border-border bg-card">
          <div className="flex items-center gap-2 md:gap-3 flex-wrap">
            <Badge variant={modeBadge.variant as any}>{modeBadge.text}</Badge>
            {isJobPrep
              ? (
                <span className="text-sm text-dim">
                  {initData.company ? `${initData.company} · ` : ""}{initData.position || "目标岗位"}
                </span>
              )
              : initData.topic && <span className="text-sm text-dim">{initData.topic}</span>}
            <span className="text-[13px] text-dim">{answeredCount}/{totalQ} 已答</span>
          </div>
          <Button variant="destructive" size="sm" onClick={handleEndBatch} disabled={submitting}>
            {submitting ? "评估中..." : finished ? "查看评估" : isJobPrep ? "结束备面" : "结束训练"}
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-6 md:px-6 md:py-8 flex flex-col items-center gap-5">
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
              <div className="w-full max-w-[720px] flex items-center gap-2">
                <div className="flex-1 h-1 rounded-full bg-border overflow-hidden">
                  <div className="h-full rounded-full bg-primary transition-[width] duration-300 ease-in-out" style={{ width: `${(currentIndex / totalQ) * 100}%` }} />
                </div>
                <span className="text-[13px] text-dim whitespace-nowrap">{currentIndex + 1} / {totalQ}</span>
              </div>

              <Card className="w-full max-w-[720px] animate-fade-in">
                <CardContent className="p-5 md:p-8">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-primary border-primary/30">
                        Q{currentQ.id}
                      </Badge>
                      {currentQ.category && (
                        <Badge variant={isJobPrep ? "blue" as any : "secondary"}>{currentQ.category}</Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {currentQ.focus_area && (
                        <Badge variant="secondary">{currentQ.focus_area}</Badge>
                      )}
                      {currentQ.difficulty && (
                        <span className="flex items-center gap-0.5">
                          {Array.from({ length: 5 }, (_, i) => (
                            <Star key={i} size={13} className={i < currentQ.difficulty! ? "text-primary fill-primary" : "text-dim"} />
                          ))}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="text-base leading-[1.8]">
                    <div className="md-content">
                      <ReactMarkdown>{currentQ.question}</ReactMarkdown>
                    </div>
                  </div>
                  {isJobPrep && currentQ.intent && (
                    <div className="mt-4 rounded-xl bg-tertiary/8 border border-tertiary/15 px-4 py-3 text-sm leading-relaxed text-dim">
                      <span className="text-tertiary font-medium">面试官在看什么：</span> {currentQ.intent}
                    </div>
                  )}
                </CardContent>
              </Card>

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
                          <ReactMarkdown>{content}</ReactMarkdown>
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
                    className="w-full min-h-[80px] max-h-[240px] px-4 py-3 rounded-xl border border-border bg-input text-text resize-none text-sm leading-relaxed pl-12 placeholder:text-dim/50 focus-visible:outline-none focus-visible:border-primary focus-visible:ring-1 focus-visible:ring-primary/30"
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
                <div className="flex items-center justify-end gap-3">
                  <Button variant="ghost" size="sm" onClick={handleSkip}>
                    跳过
                  </Button>
                  <Button variant="default" className="px-7 py-3.5 text-[15px]" disabled={!drillInput.trim()} onClick={handleDrillSubmit}>
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
      <div className="flex items-center justify-between px-4 py-3 md:px-6 border-b border-border bg-card">
        <div className="flex items-center gap-2 md:gap-3 flex-wrap">
          <Badge variant={modeBadge.variant as any}>{modeBadge.text}</Badge>
          {initData.topic && <span className="text-sm text-dim">{initData.topic}</span>}
          {progress && (
            <span className="text-[13px] text-dim flex items-center gap-1.5">
              <span className="text-border">|</span>
              进度: {progress}
            </span>
          )}
        </div>
        <Button variant="destructive" size="sm" onClick={handleEndResume} disabled={reviewing}>
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

        <div className="px-4 pt-4 pb-5 md:px-6 md:pb-6 flex justify-center">
          <div className="relative w-full max-w-3xl">
            <textarea
              ref={textareaRef}
              className="w-full px-4 py-4 md:px-5 pl-12 min-h-[80px] max-h-[240px] rounded-2xl border border-border bg-card text-text resize-none text-[15px] leading-normal placeholder:text-dim/50 focus-visible:outline-none focus-visible:border-primary focus-visible:ring-1 focus-visible:ring-primary/30"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={chatVoice.isListening ? "正在录音..." : finished ? "面试已结束" : "输入你的回答... (Enter 发送)"}
              disabled={finished || sending}
              rows={3}
            />
            {chatVoice.isSupported && !finished && (
              <div className="absolute bottom-4 left-3">
                <MicButton voice={chatVoice} />
              </div>
            )}
          </div>
        </div>
    </div>
  );
}
