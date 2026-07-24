import React, { useState, useEffect, useLayoutEffect, useRef, useCallback } from "react";
import { useParams, useLocation, useNavigate } from "react-router-dom";
import { Markdown } from "../components/ChatBubble";
import { Check, Minus, Star, Lightbulb, Eye, Loader2, RefreshCw, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import ChatBubble from "../components/ChatBubble";
import { sendMessage, endInterview, getReferenceAnswer, getInterviewSession, saveDrillProgress, type RAGEvalMetrics, type DrillProgressPayload } from "../api/interview";
import { formatQuestionLabel } from "@/lib/question";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { scrollToElement } from "@/lib/utils";
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

  const [messages, setMessages] = useState<ChatMessage[]>(() => (
    !isBatchMode && initData.message
      ? [{ role: "assistant", content: initData.message }]
      : []
  ));
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendProgress, setSendProgress] = useState("");
  const [finished, setFinished] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [progress, setProgress] = useState(initData.progress || "");

  const [questions, setQuestions] = useState<Question[]>(initData.questions || []);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string | number, string>>({});
  const [drillInput, setDrillInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [evalProgress, setEvalProgress] = useState("");
  const [evalError, setEvalError] = useState<string | null>(null);
  const [hints, setHints] = useState<Record<string | number, HintState>>({});
  const [hintLoading, setHintLoading] = useState(false);
  const [showEndConfirm, setShowEndConfirm] = useState(false);
  const [restoring, setRestoring] = useState<boolean>(!initData.mode);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const restoredRef = useRef(false);
  const autoEvalDoneRef = useRef(false);
  const saveTimerRef = useRef<number | null>(null);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null);

  const draftKey = sessionId ? `drill:draft:${sessionId}` : null;

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
          // Keys are question IDs verbatim. They can be strings like "Q2"
          // (the LLM emits string ids), so do NOT Number() them — that yields
          // NaN and collapses every answer into one key, which is exactly the
          // "answers all gone after resume" bug.
          setAnswers({ ...progress.partial_answers });
        }
        if (typeof progress.current_index === "number") setCurrentIndex(progress.current_index);
        if (progress.hints) {
          setHints({ ...progress.hints });
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

        // Tier-2 (backend) restore done. If backend had no progress yet,
        // try tier-1 (localStorage) — recovers drafts written during a
        // backend outage so the user doesn't see a blank canvas.
        const backendHasProgress = progress && (
          progress.partial_answers || progress.current_index != null
        );
        if (!backendHasProgress && draftKey) {
          try {
            const raw = localStorage.getItem(draftKey);
            if (raw) {
              const local = JSON.parse(raw);
              if (local.partial_answers && Object.keys(local.partial_answers).length) {
                setAnswers({ ...local.partial_answers });
                const localIdx = typeof local.current_index === "number" ? local.current_index : 0;
                if (typeof local.current_index === "number") setCurrentIndex(local.current_index);
                // Same as the tier-2 path: load the current question's saved
                // answer into drillInput, otherwise the sync effect sees an
                // empty editor and deletes that answer once restoring is done.
                const localQid = qList[localIdx]?.id;
                if (localQid != null && local.partial_answers[String(localQid)]) {
                  setDrillInput(local.partial_answers[String(localQid)]);
                }
                toast.info("已从本地缓存恢复未同步的草稿");
              }
            }
          } catch (e) {
            console.warn("localStorage fallback restore failed:", e);
          }
        }
      })
      .catch((err: any) => {
        console.error("恢复面试失败:", err);
        setRestoreError(err?.message || "无法加载会话，请返回首页重试");
      })
      .finally(() => setRestoring(false));
  }, [sessionId, initData.mode, draftKey]);

  // Debounced persist of in-progress state.
  // Two-tier strategy:
  //   1. localStorage (sync, immediate) — survives backend outage
  //   2. backend SQLite (async) — authoritative, cross-device
  // On backend success we clear the localStorage entry to avoid bloat.
  const saveErrorShownRef = useRef(false);
  useEffect(() => {
    if (!isBatchMode || !sessionId || restoring || finished) return;
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      const payload = {
        current_index: currentIndex,
        partial_answers: answers,
        hints,
      };
      // Tier 1: localStorage — synchronous, never fails on network issues
      if (draftKey) {
        try {
          localStorage.setItem(
            draftKey,
            JSON.stringify({ ...payload, savedAt: Date.now() }),
          );
        } catch {
          // Storage quota / private mode — silently degrade to backend-only
        }
      }
      // Tier 2: backend
      setSaveStatus("saving");
      saveDrillProgress(sessionId, payload)
        .then(() => {
          setSaveStatus("saved");
          setLastSavedAt(Date.now());
          saveErrorShownRef.current = false;
          // Successful upload — drop the local fallback to avoid stale data
          if (draftKey) {
            try { localStorage.removeItem(draftKey); } catch { /* noop */ }
          }
        })
        .catch((err: any) => {
          console.warn("保存进度失败:", err);
          setSaveStatus("error");
          if (!saveErrorShownRef.current) {
            saveErrorShownRef.current = true;
            toast.error("云端保存失败，已在本地缓存，恢复后会自动同步");
          }
        });
    }, 400);
    return () => {
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    };
  }, [currentIndex, answers, hints, sessionId, isBatchMode, restoring, finished, draftKey]);

  // Snapshot of the latest savable state, read by the flush handlers below.
  // Kept in a ref so the pagehide / unmount listeners never see a stale closure.
  const saveSnapshotRef = useRef<{ enabled: boolean; payload: DrillProgressPayload | null }>({
    enabled: false,
    payload: null,
  });
  useEffect(() => {
    saveSnapshotRef.current = {
      enabled: isBatchMode && !!sessionId && !restoring && !finished,
      payload: { current_index: currentIndex, partial_answers: answers, hints },
    };
  }, [isBatchMode, sessionId, restoring, finished, currentIndex, answers, hints]);

  // Force an immediate (non-debounced) save. Critical for the "answer half, then
  // exit" flow: the 400ms debounce is cleared on unmount without firing, so
  // without this flush every input made since the last debounce tick is lost.
  // keepalive ensures the POST still goes out while the page is being torn down.
  const flushProgress = useCallback(() => {
    const snap = saveSnapshotRef.current;
    if (!snap.enabled || !snap.payload || !sessionId) return;
    if (draftKey) {
      try {
        localStorage.setItem(draftKey, JSON.stringify({ ...snap.payload, savedAt: Date.now() }));
      } catch { /* quota / private mode */ }
    }
    saveDrillProgress(sessionId, snap.payload, { keepalive: true }).catch(() => { /* localStorage still holds it */ });
  }, [sessionId, draftKey]);

  useEffect(() => {
    const onVisibility = () => { if (document.visibilityState === "hidden") flushProgress(); };
    window.addEventListener("pagehide", flushProgress);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", flushProgress);
      document.removeEventListener("visibilitychange", onVisibility);
      // Route change / unmount — flush whatever is pending before the component dies.
      flushProgress();
    };
  }, [flushProgress]);

  // Sync drillInput → answers so the draft survives refresh.
  // Guard on `finished`: the last-question submit sets answers then clears
  // drillInput, and without this guard the effect would re-run with the empty
  // drillInput and delete the just-submitted answer.
  useEffect(() => {
    if (!isBatchMode || restoring || finished) return;
    const q = questions[currentIndex];
    if (!q) return;
    setAnswers((prev) => {
      const cur = prev[q.id];
      // Empty editor → drop the entry instead of storing "". Keeps the "已答"
      // count honest and stops a blank drillInput (e.g. right after advancing)
      // from persisting an empty answer.
      if (!drillInput) {
        if (cur === undefined) return prev;
        const next = { ...prev };
        delete next[q.id];
        return next;
      }
      if (cur === drillInput) return prev;
      return { ...prev, [q.id]: drillInput };
    });
  }, [drillInput, currentIndex, questions, isBatchMode, restoring, finished]);

  useEffect(() => {
    if (!isBatchMode) scrollToElement(chatEndRef.current);
  }, [messages, sending, isBatchMode]);

  useEffect(() => {
    if (isBatchMode) textareaRef.current?.focus();
  }, [currentIndex, isBatchMode]);

  // Auto-grow the drill answer box to fit its content, so a long answer expands
  // the box (and the page scrolls) instead of scrolling inside a fixed-height
  // box. Capped at 60% of the viewport so a very long answer can't keep growing
  // off-screen — past the cap the box itself scrolls internally. Runs on every
  // drillInput / question change — covers typing, voice input, question
  // navigation and draft restore (all set drillInput).
  const autoResizeDrill = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const cap = Math.round(window.innerHeight * 0.6);
    if (el.scrollHeight > cap) {
      el.style.height = `${cap}px`;
      el.style.overflowY = "auto";
    } else {
      el.style.height = `${el.scrollHeight}px`;
      el.style.overflowY = "hidden";
    }
  }, []);

  useLayoutEffect(() => {
    if (isBatchMode) autoResizeDrill();
  }, [drillInput, currentIndex, isBatchMode, autoResizeDrill]);

  // Recompute the cap when the viewport changes (window resize / device rotation /
  // mobile keyboard) — otherwise a box sized to the old 60vh stays stale.
  useEffect(() => {
    if (!isBatchMode) return;
    const onResize = () => autoResizeDrill();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [isBatchMode, autoResizeDrill]);

  const currentQ = questions[currentIndex];
  const totalQ = questions.length;
  const answeredCount = Object.keys(answers).length;
  const allAnswered = isBatchMode && questions.length > 0 && questions.every((q) => (answers[q.id] || "").trim().length > 0);

  // Immediate (non-debounced) save with explicit values. Called on every
  // question navigation so progress is persisted per step, independent of the
  // debounce timer / unmount timing.
  const persistNow = useCallback((
    idx: number,
    answersObj: Record<string | number, string>,
    hintsObj: Record<string | number, HintState>,
  ) => {
    if (!isBatchMode || !sessionId || restoring || finished) return;
    const payload = { current_index: idx, partial_answers: answersObj, hints: hintsObj };
    if (draftKey) {
      try { localStorage.setItem(draftKey, JSON.stringify({ ...payload, savedAt: Date.now() })); } catch { /* */ }
    }
    setSaveStatus("saving");
    saveDrillProgress(sessionId, payload)
      .then(() => {
        setSaveStatus("saved");
        setLastSavedAt(Date.now());
        saveErrorShownRef.current = false;
        if (draftKey) { try { localStorage.removeItem(draftKey); } catch { /* */ } }
      })
      .catch(() => {
        setSaveStatus("error");
        if (!saveErrorShownRef.current) {
          saveErrorShownRef.current = true;
          toast.error("云端保存失败，已在本地缓存，恢复后会自动同步");
        }
      });
  }, [isBatchMode, sessionId, restoring, finished, draftKey]);

  const handleDrillSubmit = () => {
    const text = drillInput.trim();
    if (!text || !currentQ) return;
    const nextAnswers = { ...answers, [currentQ.id]: text };
    setAnswers(nextAnswers);
    if (currentIndex < totalQ - 1) {
      const nextIdx = currentIndex + 1;
      // Load the destination question's existing answer (if it was answered
      // earlier and we're revisiting) so the drillInput→answers sync effect
      // doesn't overwrite it with an empty string.
      setDrillInput(nextAnswers[questions[nextIdx]?.id] || "");
      setCurrentIndex(nextIdx);
      persistNow(nextIdx, nextAnswers, hints);
    } else {
      setDrillInput("");
      persistNow(currentIndex, nextAnswers, hints);
      setFinished(true);
    }
  };

  const handleSkip = () => {
    if (!currentQ) return;
    if (currentIndex < totalQ - 1) {
      const nextIdx = currentIndex + 1;
      setDrillInput(answers[questions[nextIdx]?.id] || "");
      setCurrentIndex(nextIdx);
      persistNow(nextIdx, answers, hints);
    } else {
      setDrillInput("");
      persistNow(currentIndex, answers, hints);
      setFinished(true);
    }
  };

  const handlePrev = () => {
    if (currentIndex <= 0) return;
    const prevIdx = currentIndex - 1;
    setDrillInput(answers[questions[prevIdx]?.id] || "");
    setCurrentIndex(prevIdx);
    persistNow(prevIdx, answers, hints);
  };

  const handleHint = async () => {
    if (!currentQ || hintLoading) return;
    const qid = currentQ.id;
    const current: HintState = hints[qid] || { stage: "none" };
    const nextMode = current.stage === "none" ? "hint" : "full";
    if (current.stage === "full") return;
    if ((current as any)[nextMode]) {
      setHints((p) => ({ ...p, [qid]: { ...p[qid], stage: nextMode } as HintState }));
      return;
    }
    setHintLoading(true);
    try {
      const topic = effectiveInit.topic || "";
      // Pass the question id verbatim — ids may be strings like "Q2", and
      // Number("Q2") = NaN would break reference-answer caching on the backend.
      const data = await getReferenceAnswer(topic, currentQ.question, sessionId, qid, false, nextMode);
      setHints((p) => ({
        ...p,
        [qid]: { ...p[qid], [nextMode]: data.reference_answer, stage: nextMode } as HintState,
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
    setEvalError(null);
    try {
      const answerList = questions.map((q) => ({
        question_id: q.id,
        answer: answers[q.id] || "",
      }));
      let ragMetrics: RAGEvalMetrics | null = null;
      const data = await endInterview(sessionId, answerList, {
        onProgress: (msg) => setEvalProgress(msg),
        onRAGMetrics: (m) => { ragMetrics = m; },
      });
      // Evaluation succeeded — the session is completed on the backend. Cancel
      // any pending debounced save, disable the unmount flush (handleEndBatch
      // can run while `finished` is still false, e.g. via the confirm dialog),
      // and drop the local draft so no stale progress write fires after
      // navigation.
      saveSnapshotRef.current.enabled = false;
      if (saveTimerRef.current) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
      if (draftKey) {
        try { localStorage.removeItem(draftKey); } catch { /* noop */ }
      }
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
          ragEvalMetrics: ragMetrics,
        },
      });
    } catch (err: any) {
      setEvalError(err?.message || "评估失败，请重试");
    } finally {
      setSubmitting(false);
    }
  };

  // Auto-evaluate when arriving from the history page ("时光机") with intent.
  // The backend reconstructs the submitted answers from the persisted transcript
  // when the live answers are gone, so we don't gate on `allAnswered` here —
  // only that questions have loaded. Fires at most once (autoEvalDoneRef).
  useEffect(() => {
    if (restoring || submitting || evalError || finished) return;
    if (questions.length === 0) return;
    if (!(location.state as any)?.autoEvaluate) return;
    if (autoEvalDoneRef.current) return;
    autoEvalDoneRef.current = true;
    handleEndBatch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restoring, submitting, evalError, finished, questions.length]);

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
      if (isBatchMode) handleDrillSubmit();
      else handleSend();
    }
  };

  const modeBadge = isJobPrep
    ? { text: "JD 备面", variant: "blue" }
    : effectiveInit.mode === "topic_drill"
      ? { text: "专项训练", variant: "success" }
      : { text: "简历面试", variant: "default" };

  if (restoring) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    );
  }

  if (restoreError) {
    return (
      <div className="flex-1 flex items-center justify-center px-4">
        <Card className="max-w-md w-full">
          <CardContent className="p-6 text-center space-y-3">
            <div className="text-base font-medium">面试会话加载失败</div>
            <p className="text-sm text-dim">{restoreError}</p>
            <Button variant="default" onClick={() => navigate("/")}>返回首页</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isBatchMode && questions.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center px-4">
        <Card className="max-w-md w-full">
          <CardContent className="p-6 text-center space-y-3">
            <div className="text-base font-medium">会话已失效</div>
            <p className="text-sm text-dim">没有题目数据，可能是会话已被清理。请重新开始一场面试。</p>
            <Button variant="default" onClick={() => navigate("/")}>返回首页</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isBatchMode) {
    return (
      <div className="sig-page sig-workspace flex-1 flex flex-col h-full">
        <div className="sig-workspace-header flex items-center justify-between px-3 py-2.5 md:px-6 md:py-3 border-b border-border/50 flex-wrap gap-2" style={{ background: "var(--sig-bg)" }}>
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
            <SaveIndicator status={saveStatus} lastSavedAt={lastSavedAt} />
          </div>
          <Button variant="destructive" size="sm" onClick={() => finished ? handleEndBatch() : setShowEndConfirm(true)} disabled={submitting} className="shrink-0">
            {submitting ? "评估中..." : finished ? "查看评估" : isJobPrep ? "结束备面" : "结束训练"}
          </Button>
        </div>

        <div className="sig-workspace-scroll flex-1 overflow-y-auto px-3 py-4 md:px-6 md:py-8 flex flex-col items-center gap-4 md:gap-5">
          {/* Eval failure banner — answers are preserved; one-click retry. */}
          {evalError && !submitting && (
            <div className="w-full max-w-[720px] rounded-xl bg-red/8 border border-red/20 px-4 py-3.5 flex flex-col gap-2 animate-fade-in">
              <div className="text-sm text-red font-medium leading-relaxed">评估失败：{evalError}</div>
              <div className="text-[13px] text-dim">答案已保留，可直接重试。</div>
              <div className="flex justify-end">
                <Button variant="default" size="sm" className="gap-1.5" onClick={handleEndBatch}>
                  <RefreshCw size={14} />
                  重新评估
                </Button>
              </div>
            </div>
          )}

          {/* Auto-eval entry from history: fully answered but no report yet. */}
          {isBatchMode && !restoring && !finished && !submitting && allAnswered && !evalError && (
            <div className="w-full max-w-[720px] rounded-xl bg-primary/8 border border-primary/20 px-4 py-3.5 flex flex-col gap-2 animate-fade-in">
              <div className="text-sm font-medium leading-relaxed">检测到你已完成全部作答，但尚未生成评估报告。</div>
              <div className="flex justify-end">
                <Button variant="default" size="sm" onClick={handleEndBatch}>
                  生成评估报告
                </Button>
              </div>
            </div>
          )}

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
                    <span>{formatQuestionLabel(q.id)}: {q.question.slice(0, 60)}{q.question.length > 60 ? "..." : ""}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : currentQ ? (
            <>
              <div className="w-full max-w-[720px] flex items-center gap-3">
                <div className="sig-progress flex-1">
                  <div className="sig-progress-fill" style={{ width: `${(currentIndex / totalQ) * 100}%` }} />
                </div>
                <span className="text-[12px] text-dim whitespace-nowrap sig-num">{currentIndex + 1} / {totalQ}</span>
              </div>

              {/* L1 — 题号 + 分类侧边栏 */}
              <div className="w-full max-w-[720px] flex gap-3 md:gap-5 animate-fade-in">
                <div className="hidden md:flex flex-col items-center gap-2 pt-1 shrink-0">
                  <span className="text-[10px] font-mono uppercase tracking-widest text-dim/60">{formatQuestionLabel(currentQ.id, 2)}</span>
                  {currentQ.difficulty && (
                    <div className="flex flex-col items-center gap-0.5">
                      {Array.from({ length: 5 }, (_, i) => (
                        <Star key={i} size={8} className={i < currentQ.difficulty! ? "text-primary fill-primary" : "text-border"} />
                      ))}
                    </div>
                  )}
                </div>

                {/* L2 — 题目主卡（站 C 位） */}
                <Card hoverLift className="flex-1 min-w-0 question-hero">
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
                        <Markdown>{currentQ.question}</Markdown>
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
                          <Markdown>{content}</Markdown>
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
                    className="sig-textarea w-full min-h-[100px] md:min-h-[80px] pl-12 text-[15px] md:text-sm resize-none"
                    value={drillInput}
                    onChange={(e) => setDrillInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="输入你的回答... (Enter 提交)"
                    rows={3}
                  />
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
    <div className="sig-page sig-workspace flex-1 flex flex-col h-full">
      <div className="sig-workspace-header flex items-center justify-between px-4 py-3 md:px-6 border-b border-border/50" style={{ background: "var(--sig-bg)" }}>
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

      <div className="sig-workspace-scroll flex-1 overflow-y-auto px-4 py-6 md:px-6 md:py-8 flex flex-col gap-7 max-w-3xl w-full mx-auto">
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

        <div className="sig-workspace-composer px-3 pt-3 pb-4 md:px-6 md:pt-4 md:pb-6 flex justify-center safe-area-bottom border-t border-border/50" style={{ background: "var(--sig-bg)" }}>
          <div className="relative w-full max-w-3xl">
            <textarea
              ref={textareaRef}
              className="sig-textarea w-full pl-12 min-h-[72px] md:min-h-[80px] max-h-[200px] md:max-h-[240px] text-[15px]"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={finished ? "面试已结束" : "输入你的回答... (Enter 发送)"}
              disabled={finished || sending}
              rows={3}
            />
          </div>
        </div>
    {/* End confirmation dialog */}
    {showEndConfirm && (
      <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "var(--sig-overlay)" }}>
        <Card className="w-full max-w-sm mx-4 animate-fade-in">
          <CardContent className="p-5 text-center space-y-4">
            <h3 className="text-lg font-semibold">
              {isBatchMode ? (isJobPrep ? "确定结束备面？" : "确定结束训练？") : "确定结束面试？"}
            </h3>
            <p className="text-sm text-dim">
              {isBatchMode
                ? `已完成 ${Object.keys(answers).filter((k) => answers[k as any]).length}/${questions.length} 题，结束后将进入 AI 评估。`
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

interface SaveIndicatorProps {
  status: "idle" | "saving" | "saved" | "error";
  lastSavedAt: number | null;
}

function SaveIndicator({ status, lastSavedAt }: SaveIndicatorProps) {
  if (status === "idle") return null;
  if (status === "saving") {
    return (
      <span className="text-[11px] text-dim flex items-center gap-1 whitespace-nowrap">
        <Loader2 size={11} className="animate-spin" />
        保存中
      </span>
    );
  }
  if (status === "saved") {
    const title = lastSavedAt ? `保存于 ${new Date(lastSavedAt).toLocaleTimeString()}` : "";
    return (
      <span className="text-[11px] text-green flex items-center gap-1 whitespace-nowrap" title={title}>
        <Check size={11} />
        已保存
      </span>
    );
  }
  // error
  return (
    <span className="text-[11px] text-orange flex items-center gap-1 whitespace-nowrap" title="本地已缓存，云端连接恢复后会自动同步">
      <AlertTriangle size={11} aria-hidden="true" /> 云端失败
    </span>
  );
}
