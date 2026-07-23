import { useState, useRef, useEffect, useCallback, memo, useMemo } from "react";
import {
  Plus, Trash2, Send, Download, FileText, Loader2,
  MessageSquare, Pencil, Check, X, PanelLeftOpen, Eraser, Square, RefreshCw, AlertCircle, Brain, BookPlus, ImagePlus,
} from "lucide-react";
import { toast } from "sonner";
import { Markdown } from "../components/ChatBubble";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import useDraftPersist from "../hooks/useDraftPersist";
import {
  createQASession,
  listQASessions,
  deleteQASession,
  renameQASession,
  loadQAMessages,
  clearQAMessages,
  streamQAChat,
  regenerateQAChat,
  generateQASummary,
  ingestQACardToKnowledge,
  downloadMarkdown,
  fetchAuthedImage,
  type QASession,
  type QAMessage,
  type QASummaryResult,
} from "../api/qa_arena";

const ALL_SUGGESTED_QUESTIONS = [
  "Redis 缓存穿透、缓存击穿、缓存雪崩的区别和解决方案？",
  "Python 的 GIL 是什么？它如何影响多线程？",
  "TCP 三次握手和四次挥手的过程是什么？",
  "数据库索引的原理是什么？B+ 树和哈希索引的区别？",
  "什么是 RESTful API？它和 GraphQL 有什么区别？",
  "Docker 和虚拟机的区别是什么？容器的隔离原理？",
  "进程和线程的区别？协程又是什么？",
  "HTTP/2 相比 HTTP/1.1 有哪些改进？",
  "什么是 CAP 定理？在分布式系统中如何取舍？",
  "React 的虚拟 DOM 是如何工作的？diff 算法的原理？",
  "什么是死锁？如何预防和检测死锁？",
  "OAuth 2.0 的授权流程是怎样的？",
  "微服务架构的优缺点？什么场景适合用微服务？",
  "什么是事件循环（Event Loop）？Node.js 的事件循环机制？",
  "数据库事务的 ACID 特性分别是什么？",
  "什么是 JWT？它和 Session 认证的区别？",
  "Linux 中软链接和硬链接的区别？",
  "什么是一致性哈希？它解决了什么问题？",
  "Git rebase 和 merge 的区别？什么时候用哪个？",
  "WebSocket 和 HTTP 长轮询的区别？各自的适用场景？",
];

const SUGGESTED_COUNT = 4;

// Image attachment limits — mirror the backend caps in qa_arena.py.
const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;
const ACCEPTED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function createQAIngestIdempotencyKey(sessionId: string, content: string): Promise<string> {
  const payload = `${sessionId}\u0000${content}`;
  try {
    const subtle = globalThis.crypto?.subtle;
    if (subtle) {
      const digest = await subtle.digest("SHA-256", new TextEncoder().encode(payload));
      const hex = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
      return `qa-ingest:${hex}`;
    }
  } catch {
    // Deterministic fallback for restricted browser contexts without Web Crypto.
  }
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < payload.length; index += 1) {
    const code = payload.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x5bd1e995);
  }
  return `qa-ingest:${(first >>> 0).toString(16)}${(second >>> 0).toString(16)}:${payload.length.toString(36)}`;
}

function pickRandomQuestions(pool: string[], count: number): string[] {
  const shuffled = [...pool].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, count);
}

export default function QAArena() {
  const [sessions, setSessions] = useState<QASession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<QAMessage[]>([]);
  const [input, setInput] = useState("");
  // Staged image attachments for the next message. `preview` is a local object URL
  // (rendered immediately); `dataUrl` is the base64 sent to the backend.
  const [attachments, setAttachments] = useState<{ preview: string; dataUrl: string }[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamStage, setStreamStage] = useState<string>("");
  const [isSummarizing, setIsSummarizing] = useState(false);
  const [summaryProgress, setSummaryProgress] = useState("");
  const [summaryEffort, setSummaryEffort] = useState("");
  const [summaryResult, setSummaryResult] = useState<QASummaryResult | null>(null);
  const [summarySessionId, setSummarySessionId] = useState<string | null>(null);
  const [showSummary, setShowSummary] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [ingestResult, setIngestResult] = useState<{ ok: boolean; topic: string | null; reason: string } | null>(null);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionListBusy, setSessionListBusy] = useState(false);
  const [sessionListError, setSessionListError] = useState<string | null>(null);
  const [sessionLoadError, setSessionLoadError] = useState<string | null>(null);
  const [sessionListReloadGeneration, setSessionListReloadGeneration] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [loaded, setLoaded] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const rafRef = useRef<number | null>(null);
  const isStreamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const summaryAbortRef = useRef<AbortController | null>(null);
  const summaryGenerationRef = useRef(0);
  const ingestGenerationRef = useRef(0);
  const ingestingRef = useRef(false);
  const ingestAbortRef = useRef<AbortController | null>(null);
  const ingestRequestKeyRef = useRef<string | null>(null);
  const streamGenerationRef = useRef(0);
  const attachmentGenerationRef = useRef(0);
  const sessionLoadingRef = useRef(false);
  const sessionListBusyRef = useRef(false);
  const sessionLoadGenerationRef = useRef(0);
  const sessionsMutationGenerationRef = useRef(0);
  const mountedRef = useRef(false);
  const messageObjectUrlsRef = useRef<Set<string>>(new Set());
  const attachmentObjectUrlsRef = useRef<Set<string>>(new Set());
  // Mirrors activeId for the stream consumer: callbacks check it so a stream
  // started in session A can't overwrite messages after switching to B.
  const activeIdRef = useRef<string | null>(null);
  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  const cancelSummaryWork = useCallback(() => {
    summaryGenerationRef.current += 1;
    summaryAbortRef.current?.abort();
    summaryAbortRef.current = null;
    setIsSummarizing(false);
    setSummaryProgress("");
  }, []);

  const cancelIngestWork = useCallback(() => {
    ingestGenerationRef.current += 1;
    const controller = ingestAbortRef.current;
    ingestAbortRef.current = null;
    ingestRequestKeyRef.current = null;
    ingestingRef.current = false;
    controller?.abort();
    setIngesting(false);
  }, []);

  const cancelStreamForTransition = useCallback(() => {
    streamGenerationRef.current += 1;
    const controller = abortRef.current;
    abortRef.current = null;
    controller?.abort();
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    isStreamingRef.current = false;
    setIsStreaming(false);
    setStreamStage("");
  }, []);

  const revokeMessageObjectUrls = useCallback(() => {
    messageObjectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    messageObjectUrlsRef.current.clear();
  }, []);

  const revokeAttachmentObjectUrl = useCallback((url: string) => {
    attachmentObjectUrlsRef.current.delete(url);
    URL.revokeObjectURL(url);
  }, []);

  const discardAttachments = useCallback(() => {
    attachmentGenerationRef.current += 1;
    attachmentObjectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    attachmentObjectUrlsRef.current.clear();
    setAttachments([]);
  }, []);

  // Abort in-flight streams on unmount so their fetch readers are released.
  useEffect(() => {
    const attachmentObjectUrls = attachmentObjectUrlsRef.current;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      streamGenerationRef.current += 1;
      summaryGenerationRef.current += 1;
      ingestGenerationRef.current += 1;
      attachmentGenerationRef.current += 1;
      ingestingRef.current = false;
      ingestRequestKeyRef.current = null;
      abortRef.current?.abort();
      summaryAbortRef.current?.abort();
      ingestAbortRef.current?.abort();
      ingestAbortRef.current = null;
      revokeMessageObjectUrls();
      attachmentObjectUrls.forEach((url) => URL.revokeObjectURL(url));
      attachmentObjectUrls.clear();
    };
  }, [revokeMessageObjectUrls]);

  // Auto-scroll: instant during streaming, smooth on new user message
  useEffect(() => {
    if (!scrollRef.current) return;
    // Skip scroll during streaming — handled by flushUpdate
    if (isStreamingRef.current) return;
    scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Focus input on session switch
  useEffect(() => {
    if (activeId) inputRef.current?.focus();
  }, [activeId]);

  // Draft persistence — per-session input backup
  const { clear: clearDraft } = useDraftPersist({
    key: activeId ? `qa:draft:${activeId}` : null,
    value: input,
    onRestore: (restored) => setInput(restored),
    isEmpty: (text) => !text.trim(),
  });

  const switchReqRef = useRef<string | null>(null);
  const switchSession = useCallback(async (id: string) => {
    // Stop any in-flight stream first — its callbacks would otherwise clobber
    // the newly loaded session's last message. The ref is updated synchronously
    // (not waiting for the effect) so the dying stream's final flush already
    // sees the new id.
    cancelStreamForTransition();
    cancelSummaryWork();
    cancelIngestWork();
    const requestGeneration = ++sessionLoadGenerationRef.current;
    sessionLoadingRef.current = true;
    setSessionLoading(true);
    activeIdRef.current = id;
    switchReqRef.current = id;
    setActiveId(id);
    revokeMessageObjectUrls();
    discardAttachments();
    setMessages([]);
    setSummaryResult(null);
    setSummarySessionId(null);
    setShowSummary(false);
    setIngestResult(null);
    setSessionLoadError(null);
    setStreamError(null);
    setInput("");
    try {
      const msgs = await loadQAMessages(id);
      // Guard against out-of-order responses: if the user switched again while this
      // load was in flight, don't clobber the newer session's messages.
      if (
        mountedRef.current
        && sessionLoadGenerationRef.current === requestGeneration
        && switchReqRef.current === id
      ) {
        setMessages(msgs);
        setSessionLoadError(null);
      }
    } catch (error: any) {
      if (
        mountedRef.current
        && sessionLoadGenerationRef.current === requestGeneration
        && switchReqRef.current === id
      ) {
        setMessages([]);
        setSessionLoadError(error?.message || "无法加载会话消息");
      }
    } finally {
      if (
        mountedRef.current
        && sessionLoadGenerationRef.current === requestGeneration
      ) {
        sessionLoadingRef.current = false;
        setSessionLoading(false);
      }
    }
  }, [cancelIngestWork, cancelStreamForTransition, cancelSummaryWork, discardAttachments, revokeMessageObjectUrls]);

  // Load sessions on mount. Keeping this below switchSession makes the callback
  // dependency explicit and avoids capturing an undeclared binding.
  useEffect(() => {
    let active = true;
    setSessionListError(null);
    listQASessions()
      .then((data) => {
        if (!active) return;
        setSessionListError(null);
        setSessions(data.sessions);
        setLoaded(true);
        if (data.sessions.length > 0) {
          void switchSession(data.sessions[0].id);
        }
      })
      // A network-layer reject (e.g. offline) would otherwise leave `loaded`
      // false forever, freezing the page on the full-screen loader.
      .catch((error: any) => {
        if (active) setSessionListError(error?.message || "无法加载会话列表");
      })
      .finally(() => {
        if (active) setLoaded(true);
      });
    return () => {
      active = false;
      sessionLoadGenerationRef.current += 1;
      sessionLoadingRef.current = false;
      switchReqRef.current = null;
    };
  }, [sessionListReloadGeneration, switchSession]);

  const retrySessionList = useCallback(() => {
    setLoaded(false);
    setSessionListReloadGeneration((generation) => generation + 1);
  }, []);

  const handleNewSession = async () => {
    if (sessionLoadingRef.current || sessionListBusyRef.current) return;
    sessionListBusyRef.current = true;
    setSessionListBusy(true);
    // Same as switchSession: kill any in-flight stream before swapping state.
    const previousId = activeIdRef.current;
    cancelStreamForTransition();
    cancelSummaryWork();
    cancelIngestWork();
    sessionsMutationGenerationRef.current += 1;
    const requestGeneration = ++sessionLoadGenerationRef.current;
    sessionLoadingRef.current = true;
    setSessionLoading(true);
    switchReqRef.current = null;
    activeIdRef.current = null;
    setSummaryResult(null);
    setSummarySessionId(null);
    setShowSummary(false);
    setIngestResult(null);
    try {
      const session = await createQASession();
      if (
        !mountedRef.current
        || sessionLoadGenerationRef.current !== requestGeneration
      ) return;
      setSessions((prev) => [session, ...prev]);
      switchReqRef.current = session.id;
      activeIdRef.current = session.id;
      setActiveId(session.id);
      sessionLoadingRef.current = false;
      setSessionLoading(false);
      revokeMessageObjectUrls();
      discardAttachments();
      setMessages([]);
      setInput("");
      setSummaryResult(null);
      setSummarySessionId(null);
      setShowSummary(false);
      setIngesting(false);
      setIngestResult(null);
      setSessionLoadError(null);
      setStreamError(null);
      inputRef.current?.focus();
    } catch (error: any) {
      if (
        !mountedRef.current
        || sessionLoadGenerationRef.current !== requestGeneration
      ) return;
      // Keep the existing session usable if creating the replacement failed.
      switchReqRef.current = previousId;
      activeIdRef.current = previousId;
      sessionLoadingRef.current = false;
      setSessionLoading(false);
      toast.error(error?.message || "无法创建新会话");
    } finally {
      sessionListBusyRef.current = false;
      if (
        mountedRef.current
        && sessionLoadGenerationRef.current === requestGeneration
      ) {
        setSessionListBusy(false);
      }
    }
  };

  const handleDeleteSession = async (id: string) => {
    const deletingActive = activeIdRef.current === id;
    if (sessionLoadingRef.current || sessionListBusyRef.current) return;
    sessionListBusyRef.current = true;
    setSessionListBusy(true);
    sessionsMutationGenerationRef.current += 1;
    if (deletingActive) {
      cancelStreamForTransition();
      cancelSummaryWork();
      cancelIngestWork();
      sessionLoadGenerationRef.current += 1;
      sessionLoadingRef.current = true;
      setSessionLoading(true);
      activeIdRef.current = null;
      switchReqRef.current = null;
      setSummaryResult(null);
      setSummarySessionId(null);
      setShowSummary(false);
      setIngestResult(null);
    }
    try {
      await deleteQASession(id);
    } catch (error: any) {
      sessionListBusyRef.current = false;
      if (!mountedRef.current) return;
      if (deletingActive) {
        activeIdRef.current = id;
        switchReqRef.current = id;
        sessionLoadingRef.current = false;
        setSessionLoading(false);
      }
      setSessionListBusy(false);
      toast.error(error?.message || "无法删除会话");
      return;
    }
    if (!mountedRef.current) {
      sessionListBusyRef.current = false;
      return;
    }
    const remaining = sessions.filter((s) => s.id !== id);
    setSessions(remaining);
    sessionListBusyRef.current = false;
    setSessionListBusy(false);
    if (deletingActive) {
      revokeMessageObjectUrls();
      if (remaining.length > 0) {
        void switchSession(remaining[0].id);
      } else {
        cancelSummaryWork();
        cancelIngestWork();
        discardAttachments();
        switchReqRef.current = null;
        activeIdRef.current = null;
        sessionLoadingRef.current = false;
        setSessionLoading(false);
        setActiveId(null);
        setMessages([]);
        setInput("");
        setSummaryResult(null);
        setSummarySessionId(null);
        setShowSummary(false);
        setIngestResult(null);
        setSessionLoadError(null);
      }
    }
  };

  const handleRename = async (id: string) => {
    if (sessionLoadingRef.current || sessionListBusyRef.current) return;
    const title = editTitle.trim();
    if (!title) {
      setEditingId(null);
      return;
    }
    sessionListBusyRef.current = true;
    setSessionListBusy(true);
    sessionsMutationGenerationRef.current += 1;
    try {
      await renameQASession(id, title);
      if (!mountedRef.current) return;
      sessionsMutationGenerationRef.current += 1;
      setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title } : s)));
      setEditingId(null);
    } catch (error: any) {
      if (mountedRef.current) {
        toast.error(error?.message || "无法重命名会话");
      }
    } finally {
      sessionListBusyRef.current = false;
      if (mountedRef.current) setSessionListBusy(false);
    }
  };

  // Shared streaming consumer for both first-send and regenerate. `makeStream` receives
  // the abort signal so handleStop can cancel the underlying fetch. Assumes the LAST
  // message in state is the assistant placeholder to fill. `sid` is the session the
  // stream belongs to — writes are skipped if the user switched sessions meanwhile.
  const consumeStream = useCallback(async (sid: string, makeStream: (signal: AbortSignal) => AsyncGenerator<any>) => {
    const streamGeneration = ++streamGenerationRef.current;
    const sessionsMutationGeneration = sessionsMutationGenerationRef.current;
    setIsStreaming(true);
    isStreamingRef.current = true;
    setStreamError(null);
    setStreamStage("");

    const controller = new AbortController();
    abortRef.current = controller;

    let assistantContent = "";
    let assistantReasoning = "";
    let pendingUpdate = false;
    let errMsg: string | null = null;

    const flushUpdate = () => {
      rafRef.current = null;
      pendingUpdate = false;
      // Second line of defense (after the abort in switchSession/handleNewSession):
      // never write into a session this stream doesn't belong to.
      if (
        streamGenerationRef.current !== streamGeneration
        || activeIdRef.current !== sid
      ) return;
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: assistantContent, created_at: "", reasoning: assistantReasoning };
        return updated;
      });
      // Scroll to bottom during streaming
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    };

    const scheduleFlush = () => {
      if (!pendingUpdate) {
        pendingUpdate = true;
        rafRef.current = requestAnimationFrame(flushUpdate);
      }
    };

    try {
      for await (const event of makeStream(controller.signal)) {
        if (event.type === "token") {
          assistantContent += event.content;
          scheduleFlush();
        } else if (event.type === "reasoning") {
          // Live thinking trace from a reasoning model — keeps the stream alive during long
          // thinking and shows progress. Accumulated separately from the visible answer.
          assistantReasoning += event.content;
          scheduleFlush();
        } else if (event.type === "stage") {
          // Pipeline phase marker (memory → thinking → answering).
          if (streamGenerationRef.current === streamGeneration) {
            setStreamStage(event.message || "");
          }
        } else if (event.type === "error") {
          // Backend surfaced an empty / interrupted completion. Keep whatever streamed
          // (if anything) and show a retry hint instead of a silent blank bubble.
          errMsg = event.message || "生成失败，请点击重新生成";
          break;
        } else if (event.type === "done") {
          break;
        }
        // `ping` and any unknown type: ignored (ping is just a network-level keepalive).
      }
    } catch (err: any) {
      if (err.name !== "AbortError") {
        errMsg = err.message || "网络异常，请点击重新生成";
      }
    } finally {
      // Cancel any pending RAF and do a final flush
      if (
        streamGenerationRef.current === streamGeneration
        && rafRef.current !== null
      ) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      // Same session guard as flushUpdate — skip the final write if the user
      // switched sessions while this stream was running.
      if (
        streamGenerationRef.current === streamGeneration
        && activeIdRef.current === sid
      ) {
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = { role: "assistant", content: assistantContent, created_at: "", reasoning: assistantReasoning };
          return updated;
        });
      }
      if (
        streamGenerationRef.current === streamGeneration
        && abortRef.current === controller
      ) {
        abortRef.current = null;
        isStreamingRef.current = false;
        setIsStreaming(false);
        setStreamStage("");
        if (errMsg && activeIdRef.current === sid) setStreamError(errMsg);
      }
      if (
        streamGenerationRef.current === streamGeneration
        && activeIdRef.current === sid
      ) {
        listQASessions().then((data) => {
          if (
            streamGenerationRef.current === streamGeneration
            && activeIdRef.current === sid
            && sessionsMutationGenerationRef.current === sessionsMutationGeneration
          ) setSessions(data.sessions);
        }).catch(() => {});
      }
    }
  }, []);

  // Stage image files for the next message: validate type/size, build a local
  // preview + base64 payload, enforce the per-message cap.
  const addImageFiles = useCallback(async (files: FileList | File[] | null) => {
    if (!files) return;
    const attachmentGeneration = attachmentGenerationRef.current;
    const list = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (list.length === 0) return;
    const accepted: { preview: string; dataUrl: string }[] = [];
    for (const file of list) {
      if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) {
        toast.error(`不支持的图片格式：${file.name || file.type}`);
        continue;
      }
      if (file.size > MAX_IMAGE_BYTES) {
        toast.error(`图片过大（需 ≤ 6MB）：${file.name}`);
        continue;
      }
      try {
        const dataUrl = await readFileAsDataURL(file);
        if (attachmentGenerationRef.current !== attachmentGeneration) {
          accepted.forEach((item) => revokeAttachmentObjectUrl(item.preview));
          return;
        }
        const preview = URL.createObjectURL(file);
        attachmentObjectUrlsRef.current.add(preview);
        accepted.push({ preview, dataUrl });
      } catch {
        if (attachmentGenerationRef.current === attachmentGeneration) {
          toast.error(`读取图片失败：${file.name}`);
        }
      }
    }
    if (accepted.length === 0) return;
    setAttachments((prev) => {
      if (attachmentGenerationRef.current !== attachmentGeneration) {
        accepted.forEach((item) => revokeAttachmentObjectUrl(item.preview));
        return prev;
      }
      const room = MAX_IMAGES - prev.length;
      if (room <= 0) {
        toast.error(`最多上传 ${MAX_IMAGES} 张图片`);
        accepted.forEach((item) => revokeAttachmentObjectUrl(item.preview));
        return prev;
      }
      if (accepted.length > room) {
        toast.error(`最多上传 ${MAX_IMAGES} 张图片，多余的已忽略`);
        accepted.slice(room).forEach((item) => revokeAttachmentObjectUrl(item.preview));
      }
      return [...prev, ...accepted.slice(0, room)];
    });
  }, [revokeAttachmentObjectUrl]);

  const removeAttachment = useCallback((idx: number) => {
    setAttachments((prev) => {
      const a = prev[idx];
      if (a) revokeAttachmentObjectUrl(a.preview);
      return prev.filter((_, i) => i !== idx);
    });
  }, [revokeAttachmentObjectUrl]);

  // Paste a screenshot / image straight into the box.
  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.items || [])
      .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
      .map((it) => it.getAsFile())
      .filter((f): f is File => !!f);
    if (files.length > 0) {
      e.preventDefault();
      addImageFiles(files);
    }
  }, [addImageFiles]);

  const handleSend = async (overrideText?: string) => {
    const text = (overrideText || input).trim();
    const imgs = attachments;
    if (
      (!text && imgs.length === 0)
      || isStreamingRef.current
      || sessionLoadingRef.current
      || !!sessionLoadError
      || !activeId
      || activeIdRef.current !== activeId
    ) return;
    const sid = activeId;

    // Invalidate any FileReader work that is still preparing attachments. A
    // user can press Send while a text message is ready even though a newly
    // selected image is still being decoded; without a generation bump that
    // late result would be staged for the *next* message after this send.
    // Existing attachments are transferred below, so only in-flight work is
    // invalidated here; transferred blob URLs remain owned by the message.
    attachmentGenerationRef.current += 1;

    cancelSummaryWork();
    cancelIngestWork();
    setSummaryResult(null);
    setSummarySessionId(null);
    setShowSummary(false);
    setIngestResult(null);

    const previews = imgs.map((a) => a.preview);
    const dataUrls = imgs.map((a) => a.dataUrl);
    previews.forEach((url) => {
      if (url.startsWith("blob:")) {
        attachmentObjectUrlsRef.current.delete(url);
        messageObjectUrlsRef.current.add(url);
      }
    });

    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, created_at: new Date().toISOString(), images: previews },
      { role: "assistant", content: "", created_at: "" },
    ]);
    setInput("");
    clearDraft();
    // Hand the previews off to the message bubble — don't revoke them here.
    setAttachments([]);
    // Reset textarea height
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }

    await consumeStream(sid, (signal) => streamQAChat(sid, text, dataUrls, signal));
  };

  // Re-answer the last user question without re-typing it. Replaces the trailing
  // assistant bubble (broken / partial / empty) with a freshly streamed answer.
  const handleRegenerate = async () => {
    if (
      !activeId
      || activeIdRef.current !== activeId
      || sessionLoadingRef.current
      || isStreamingRef.current
      || !!sessionLoadError
      || messages.length === 0
    ) return;
    const sid = activeId;

    cancelSummaryWork();
    cancelIngestWork();
    setSummaryResult(null);
    setSummarySessionId(null);
    setShowSummary(false);
    setIngestResult(null);

    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role === "assistant") {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: "", created_at: "" };
        return updated;
      }
      // Last turn left no assistant reply (e.g. a prior empty completion) — add a slot.
      return [...prev, { role: "assistant", content: "", created_at: "" }];
    });

    await consumeStream(sid, (signal) => regenerateQAChat(sid, signal));
  };

  const handleStop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
  };

  const handleClearMessages = async () => {
    if (
      !activeId
      || activeIdRef.current !== activeId
      || sessionLoadingRef.current
      || isStreamingRef.current
      || !!sessionLoadError
    ) return;
    const sid = activeId;
    const requestGeneration = ++sessionLoadGenerationRef.current;
    sessionLoadingRef.current = true;
    setSessionLoading(true);
    cancelSummaryWork();
    cancelIngestWork();
    setSummaryResult(null);
    setSummarySessionId(null);
    setShowSummary(false);
    setIngestResult(null);
    try {
      await clearQAMessages(sid);
      if (
        sessionLoadGenerationRef.current === requestGeneration
        && activeIdRef.current === sid
      ) {
        revokeMessageObjectUrls();
        setMessages([]);
        setStreamError(null);
      }
    } catch (error: any) {
      if (sessionLoadGenerationRef.current === requestGeneration) {
        toast.error(error?.message || "无法清空会话消息");
      }
    } finally {
      if (sessionLoadGenerationRef.current === requestGeneration) {
        sessionLoadingRef.current = false;
        setSessionLoading(false);
      }
    }
  };

  const handleGenerateSummary = async () => {
    if (
      !activeId
      || activeIdRef.current !== activeId
      || sessionLoadingRef.current
      || isStreamingRef.current
      || summaryAbortRef.current !== null
      || !!sessionLoadError
    ) return;
    const sid = activeId;
    const requestGeneration = ++summaryGenerationRef.current;
    cancelIngestWork();
    summaryAbortRef.current?.abort();
    const controller = new AbortController();
    summaryAbortRef.current = controller;
    setIsSummarizing(true);
    setSummarySessionId(null);
    setIngestResult(null);
    setSummaryProgress("正在分析对话内容...");
    try {
      const result = await generateQASummary(
        sid,
        (msg) => {
          if (
            summaryGenerationRef.current === requestGeneration
            && activeIdRef.current === sid
          ) setSummaryProgress(msg);
        },
        summaryEffort,
        controller.signal,
      );
      if (
        summaryGenerationRef.current !== requestGeneration
        || activeIdRef.current !== sid
      ) return;
      setSummaryResult(result);
      setSummarySessionId(sid);
      setShowSummary(true);
    } catch (err: any) {
      if (
        err?.name !== "AbortError"
        && summaryGenerationRef.current === requestGeneration
        && activeIdRef.current === sid
      ) alert(err.message);
    } finally {
      if (summaryGenerationRef.current === requestGeneration) {
        summaryAbortRef.current = null;
        setIsSummarizing(false);
        setSummaryProgress("");
      }
    }
  };

  // Promote the generated card into the RAG knowledge base (user-confirmed; the backend
  // classifies a topic + factualizes before indexing). One-shot per card.
  const handleIngestKnowledge = async () => {
    if (
      !activeId
      || activeIdRef.current !== activeId
      || sessionLoadingRef.current
      || summarySessionId !== activeId
      || !summaryResult
      || ingestingRef.current
      || ingestAbortRef.current !== null
      || ingestRequestKeyRef.current !== null
      || ingestResult?.ok
    ) return;
    const sid = activeId;
    const content = summaryResult.content;
    const requestGeneration = ++ingestGenerationRef.current;
    const controller = new AbortController();
    ingestAbortRef.current = controller;
    ingestingRef.current = true;
    setIngesting(true);
    setIngestResult(null);
    try {
      const idempotencyKey = await createQAIngestIdempotencyKey(sid, content);
      if (
        ingestGenerationRef.current !== requestGeneration
        || ingestAbortRef.current !== controller
        || controller.signal.aborted
      ) return;
      ingestRequestKeyRef.current = idempotencyKey;
      const r = await ingestQACardToKnowledge(sid, content, {
        signal: controller.signal,
        idempotencyKey,
      });
      if (
        ingestGenerationRef.current === requestGeneration
        && activeIdRef.current === sid
      ) setIngestResult(r);
    } catch (e: any) {
      if (
        e?.name !== "AbortError"
        && !controller.signal.aborted
        && ingestGenerationRef.current === requestGeneration
        && activeIdRef.current === sid
      ) {
        setIngestResult({ ok: false, topic: null, reason: e.message || "收录失败" });
      }
    } finally {
      if (
        ingestGenerationRef.current === requestGeneration
        && ingestAbortRef.current === controller
      ) {
        ingestAbortRef.current = null;
        ingestRequestKeyRef.current = null;
        ingestingRef.current = false;
        setIngesting(false);
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const activeSession = sessions.find((s) => s.id === activeId);

  if (!loaded) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: "var(--sig-accent)" }} />
      </div>
    );
  }

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* ── Left sidebar: session list ── */}
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col border-r border-border bg-bg shrink-0 w-64">
        <div className="p-3 flex items-center gap-2 border-b border-border">
          <Button variant="outline" size="sm" className="flex-1 gap-1.5" onClick={handleNewSession} disabled={sessionLoading || sessionListBusy}>
            <Plus className="w-4 h-4" /> 新对话
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={cn(
                "group flex items-center gap-2 px-3 py-2.5 cursor-pointer transition-colors border-b border-border/50",
                s.id === activeId ? "bg-secondary" : "hover:bg-secondary/50",
              )}
              onClick={() => {
                if (
                  s.id !== activeId
                  && !sessionLoadingRef.current
                  && !sessionListBusyRef.current
                ) void switchSession(s.id);
              }}
            >
              <MessageSquare className="w-4 h-4 shrink-0 text-dim" />
              {editingId === s.id ? (
                <div className="flex-1 flex items-center gap-1">
                  <input
                    className="flex-1 bg-transparent border-b border-primary text-sm outline-none"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") handleRename(s.id); if (e.key === "Escape") setEditingId(null); }}
                    autoFocus
                    disabled={sessionLoading || sessionListBusy}
                    onClick={(e) => e.stopPropagation()}
                  />
                  <button disabled={sessionLoading || sessionListBusy} onClick={(e) => { e.stopPropagation(); handleRename(s.id); }} className="text-primary"><Check className="w-3.5 h-3.5" /></button>
                  <button onClick={(e) => { e.stopPropagation(); setEditingId(null); }} className="text-dim"><X className="w-3.5 h-3.5" /></button>
                </div>
              ) : (
                <>
                  <span className="flex-1 text-sm truncate">{s.title}</span>
                  <div className="hidden group-hover:flex items-center gap-0.5">
                    <button
                      disabled={sessionLoading || sessionListBusy}
                      onClick={(e) => { e.stopPropagation(); setEditingId(s.id); setEditTitle(s.title); }}
                      className="p-0.5 rounded hover:bg-muted text-dim"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button
                      disabled={sessionLoading || sessionListBusy}
                      onClick={(e) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                      className="p-0.5 rounded hover:bg-destructive/10 text-dim hover:text-destructive"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
          {sessions.length === 0 && (
            <div className="p-6 text-center text-dim text-sm">
              {sessionListError || "还没有对话，开始一个吧"}
            </div>
          )}
        </div>
      </aside>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden flex">
          <aside className="flex flex-col border-r border-border bg-bg w-72 max-w-[85vw] shadow-2xl animate-slide-in-right z-10">
            <div className="p-3 border-b border-border flex items-center justify-between">
              <span className="text-sm font-medium text-dim">对话列表</span>
              <button onClick={() => setSidebarOpen(false)} className="p-1 rounded-lg hover:bg-secondary text-dim">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex flex-col h-full">
              <div className="p-3 flex items-center gap-2 border-b border-border">
                <Button variant="outline" size="sm" className="flex-1 gap-1.5" onClick={handleNewSession} disabled={sessionLoading || sessionListBusy}>
                  <Plus className="w-4 h-4" /> 新对话
                </Button>
              </div>
              <div className="flex-1 overflow-y-auto">
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    className={cn(
                      "group flex items-center gap-2 px-3 py-2.5 cursor-pointer transition-colors border-b border-border/50",
                      s.id === activeId ? "bg-secondary" : "hover:bg-secondary/50",
                    )}
                    onClick={() => {
                      if (!sessionLoadingRef.current && !sessionListBusyRef.current) {
                        if (s.id !== activeId) void switchSession(s.id);
                        setSidebarOpen(false);
                      }
                    }}
                  >
                    <MessageSquare className="w-4 h-4 shrink-0 text-dim" />
                    {editingId === s.id ? (
                      <div className="flex-1 flex items-center gap-1">
                        <input
                          className="flex-1 bg-transparent border-b border-primary text-sm outline-none"
                          value={editTitle}
                          onChange={(e) => setEditTitle(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") handleRename(s.id); if (e.key === "Escape") setEditingId(null); }}
                          autoFocus
                          disabled={sessionLoading || sessionListBusy}
                          onClick={(e) => e.stopPropagation()}
                        />
                        <button disabled={sessionLoading || sessionListBusy} onClick={(e) => { e.stopPropagation(); handleRename(s.id); }} className="text-primary"><Check className="w-3.5 h-3.5" /></button>
                        <button onClick={(e) => { e.stopPropagation(); setEditingId(null); }} className="text-dim"><X className="w-3.5 h-3.5" /></button>
                      </div>
                    ) : (
                      <>
                        <span className="flex-1 text-sm truncate">{s.title}</span>
                        <div className="hidden group-hover:flex items-center gap-0.5">
                          <button
                            disabled={sessionLoading || sessionListBusy}
                            onClick={(e) => { e.stopPropagation(); setEditingId(s.id); setEditTitle(s.title); }}
                            className="p-0.5 rounded hover:bg-muted text-dim"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
                            disabled={sessionLoading || sessionListBusy}
                            onClick={(e) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                            className="p-0.5 rounded hover:bg-destructive/10 text-dim hover:text-destructive"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
                {sessions.length === 0 && (
                  <div className="p-6 text-center text-dim text-sm">
                    {sessionListError || "还没有对话，开始一个吧"}
                  </div>
                )}
              </div>
            </div>
          </aside>
          <div className="flex-1 bg-black/50" style={{ background: "var(--sig-overlay)" }} onClick={() => setSidebarOpen(false)} />
        </div>
      )}

      {/* ── Right main area ── */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Header */}
        <div className="px-3 py-2.5 md:px-4 border-b border-border flex items-center gap-2">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-1.5 rounded-lg hover:bg-secondary text-dim md:hidden shrink-0">
            <PanelLeftOpen className="w-5 h-5" />
          </button>
          <h2 className="text-base font-medium flex-1 truncate min-w-0">
            {activeSession?.title || "问答演练场"}
          </h2>
          {activeId && (
            <div className="flex items-center gap-1 shrink-0">
              <Button
                variant="ghost"
                size="sm"
                className="gap-1 text-dim whitespace-nowrap hidden sm:inline-flex"
                onClick={handleClearMessages}
                disabled={sessionLoading || isStreaming || messages.length === 0}
              >
                <Eraser className="w-4 h-4" /> 清空
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="sm:hidden h-8 w-8 text-dim"
                onClick={handleClearMessages}
                disabled={sessionLoading || isStreaming || messages.length === 0}
                title="清空消息"
              >
                <Eraser className="w-4 h-4" />
              </Button>
              <select
                className="h-8 shrink-0 rounded border bg-transparent px-2 text-xs transition-colors focus:outline-none focus:border-[color:var(--sig-accent)] hidden sm:inline-flex"
                style={{ borderColor: "var(--sig-line-2)", color: "var(--sig-fg)" }}
                value={summaryEffort}
                onChange={(e) => setSummaryEffort(e.target.value)}
                disabled={sessionLoading || isStreaming || isSummarizing}
                title="知识卡片思考强度：低档更快，高档更精炼（简单对话用低档即可）"
              >
                <option value="" style={{ color: "#000" }}>不思考·默认</option>
                <option value="low" style={{ color: "#000" }}>low·快</option>
                <option value="medium" style={{ color: "#000" }}>medium·均衡</option>
                <option value="high" style={{ color: "#000" }}>high·最精</option>
              </select>
              <Button
                variant="outline"
                size="sm"
                className="gap-1 hidden sm:inline-flex"
                onClick={handleGenerateSummary}
                disabled={sessionLoading || isStreaming || isSummarizing || messages.length < 2}
              >
                {isSummarizing ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
                生成知识卡片
              </Button>
              <Button
                variant="outline"
                size="icon"
                className="sm:hidden h-8 w-8"
                onClick={handleGenerateSummary}
                disabled={sessionLoading || isStreaming || isSummarizing || messages.length < 2}
                title="生成知识卡片"
              >
                {isSummarizing ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
              </Button>
              {isSummarizing && summaryProgress && (
                <span className="text-xs text-dim hidden md:inline">{summaryProgress}</span>
              )}
            </div>
          )}
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 md:px-8 py-6">
          {sessionLoading ? (
            <div className="h-full flex items-center justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-dim" />
            </div>
          ) : !activeId ? (
            sessionListError ? (
              <LoadFailure message={sessionListError} onRetry={retrySessionList} />
            ) : (
              <EmptyWelcome onNewSession={handleNewSession} />
            )
          ) : sessionLoadError ? (
            <LoadFailure message={sessionLoadError} onRetry={() => { void switchSession(activeId); }} />
          ) : messages.length === 0 ? (
            <EmptyChat onSend={(q) => handleSend(q)} />
          ) : (
            <div className="max-w-3xl mx-auto space-y-6">
              {messages.map((m, i) => (
                <ChatMessage
                  key={i}
                  role={m.role}
                  content={m.content}
                  reasoning={m.reasoning}
                  images={m.images}
                  streaming={isStreaming && i === messages.length - 1 && m.role === "assistant"}
                  emptyHint={m.role === "assistant" && !m.content && !m.reasoning && !isStreaming && i === messages.length - 1}
                />
              ))}
              {isStreaming && messages[messages.length - 1]?.content === "" && (
                <div className="flex items-center gap-2 text-dim text-sm">
                  <Loader2 className="w-4 h-4 animate-spin" /> {streamStage || "思考中..."}
                </div>
              )}
              {!sessionLoading && !isStreaming && messages.length > 0 && (
                <div className="flex">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-dim h-7 px-2 text-xs gap-1"
                    onClick={handleRegenerate}
                    title="重新生成上一条回复（无需重新输入问题）"
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> 重新生成
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Input */}
        {activeId && !sessionLoadError && (
          <div className="px-3 md:px-8 py-3 border-t border-border/50 safe-area-bottom" style={{ background: "var(--sig-bg)" }}>
            {streamError && !isStreaming && (
              <div className="max-w-3xl mx-auto mb-2 flex items-center gap-1.5 text-xs text-dim">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--sig-accent)" }} />
                <span>{streamError}</span>
              </div>
            )}
            <div className="max-w-3xl mx-auto">
              {attachments.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-2">
                  {attachments.map((a, i) => (
                    <div key={i} className="relative">
                      <img
                        src={a.preview}
                        alt={`附件 ${i + 1}`}
                        className="w-16 h-16 object-cover rounded-lg border border-border"
                      />
                      <button
                        type="button"
                        onClick={() => removeAttachment(i)}
                        className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-bg border border-border flex items-center justify-center text-dim hover:text-destructive shadow-sm"
                        title="移除"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex items-end gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  multiple
                  className="hidden"
                  onChange={(e) => { addImageFiles(e.target.files); e.target.value = ""; }}
                />
                <Button
                  size="icon"
                  variant="outline"
                  className="h-11 w-11 md:h-10 md:w-10 shrink-0"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={sessionLoading || isStreaming || !!sessionLoadError || attachments.length >= MAX_IMAGES}
                  title="上传图片（最多 4 张，支持粘贴截图）"
                >
                  <ImagePlus className="w-4 h-4" />
                </Button>
                <textarea
                  ref={inputRef}
                  className="sig-textarea flex-1 text-[15px] md:text-sm min-h-[44px]"
                  rows={1}
                  placeholder="输入你的问题，或上传/粘贴图片... (Enter 发送)"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  onInput={(e) => {
                    const t = e.target as HTMLTextAreaElement;
                    t.style.height = "auto";
                    t.style.height = Math.min(t.scrollHeight, 160) + "px";
                  }}
                  disabled={sessionLoading || isStreaming || !!sessionLoadError}
                />
                {isStreaming ? (
                  <Button
                    size="icon"
                    variant="destructive"
                    className="h-11 w-11 md:h-10 md:w-10 shrink-0"
                    onClick={handleStop}
                    title="停止生成"
                  >
                    <Square className="w-4 h-4" />
                  </Button>
                ) : (
                  <Button
                    size="icon"
                    className="h-11 w-11 md:h-10 md:w-10 shrink-0"
                    onClick={() => handleSend()}
                    disabled={sessionLoading || !!sessionLoadError || (!input.trim() && attachments.length === 0)}
                  >
                    <Send className="w-4 h-4" />
                  </Button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Summary slide-over ── */}
      {showSummary && summaryResult && (
        <div className="fixed inset-0 z-50 flex justify-end" style={{ background: "var(--sig-overlay)" }} onClick={() => setShowSummary(false)}>
          <div
            className="w-full max-w-2xl bg-bg h-full overflow-y-auto shadow-2xl animate-slide-in-right flex flex-col md:max-w-2xl border-l border-border"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-border flex items-center justify-between sticky top-0 bg-bg z-10">
              <h3 className="font-medium text-base">知识卡片预览</h3>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1"
                  onClick={handleIngestKnowledge}
                  disabled={sessionLoading || ingesting || !!ingestResult?.ok}
                  title="把卡片中确定的知识清洗后收录进知识库，供今后出题检索（自我进化）"
                >
                  {ingesting ? <Loader2 className="w-4 h-4 animate-spin" /> : <BookPlus className="w-4 h-4" />}
                  {ingestResult?.ok ? "已收录" : "收录进知识库"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1"
                  onClick={() => downloadMarkdown(summaryResult.content, summaryResult.filename)}
                >
                  <Download className="w-4 h-4" /> 下载 Markdown
                </Button>
                <button onClick={() => setShowSummary(false)} className="p-1 rounded-lg hover:bg-secondary text-dim">
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>
            {ingestResult && (
              <div
                className="px-5 py-2 text-xs border-b border-border/50"
                style={{ color: ingestResult.ok ? "var(--sig-accent)" : "var(--sig-fg-dim, #888)" }}
              >
                {ingestResult.ok
                  ? `✓ 已收录到「${ingestResult.topic}」知识库，下次出题即可检索到`
                  : `· ${ingestResult.reason}`}
              </div>
            )}
            <div className="px-5 py-4 flex-1">
              <div className="md-content text-sm leading-[1.8]">
                <Markdown>{summaryResult.content}</Markdown>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const ChatMessage = memo(function ChatMessage({ role, content, reasoning, streaming, emptyHint, images }: { role: string; content: string; reasoning?: string; streaming?: boolean; emptyHint?: boolean; images?: string[] }) {
  if (role === "user") {
    return (
      <div className="flex flex-col items-end gap-1.5 animate-fade-in">
        {images && images.length > 0 && (
          <div className="flex flex-wrap gap-1.5 justify-end max-w-[75%]">
            {images.map((src, i) => (
              <ChatImage key={i} src={src} />
            ))}
          </div>
        )}
        {content && (
          <div className="max-w-[75%] px-4 py-2.5 rounded-lg rounded-tr-sm text-[15px] leading-[1.7] whitespace-pre-wrap" style={{ background: "var(--sig-accent)", color: "var(--sig-accent-fg)" }}>
            {content}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="flex flex-col animate-fade-in">
      <div className="sig-card max-w-full leading-[1.8] text-[15px] text-text rounded-lg rounded-tl-sm px-4 py-3">
        {reasoning && (
          // Collapsible thinking trace from a reasoning model. Auto-open while streaming so
          // the user sees live progress; collapsible afterward to keep the answer prominent.
          <details className="mb-2 -mt-0.5" open={streaming}>
            <summary className="cursor-pointer select-none text-xs text-dim flex items-center gap-1.5 list-none">
              <Brain className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--sig-accent)" }} />
              思考过程{streaming && !content ? "（进行中…）" : ""}
            </summary>
            <div
              className="mt-2 mb-1 text-xs leading-relaxed text-dim whitespace-pre-wrap max-h-64 overflow-y-auto border-l-2 pl-3"
              style={{ borderColor: "var(--sig-line-2)" }}
            >
              {reasoning}
            </div>
          </details>
        )}
        <div className="md-content">
          {content ? (
            <Markdown>{content}</Markdown>
          ) : emptyHint ? (
            <span className="text-dim text-sm italic">（本次回复为空，请点击下方"重新生成"）</span>
          ) : null}
        </div>
      </div>
    </div>
  );
});

// Renders a chat image. Local previews (blob:/data:) display directly; stored
// history images come from an auth-protected endpoint, so they're fetched through
// the authenticated client into an object URL (a JWT can't ride on an <img src>).
const ChatImage = memo(function ChatImage({ src }: { src: string }) {
  const isLocal = src.startsWith("blob:") || src.startsWith("data:");
  if (isLocal) return <ResolvedChatImage src={src} />;
  return <RemoteChatImage key={src} src={src} />;
});

function RemoteChatImage({ src }: { src: string }) {
  const [resolved, setResolved] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let obj: string | null = null;
    const controller = new AbortController();
    fetchAuthedImage(src, controller.signal)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        obj = url;
        setResolved(url);
      })
      .catch((error: any) => {
        if (!cancelled && error?.name !== "AbortError") setFailed(true);
      });
    return () => {
      cancelled = true;
      controller.abort();
      if (obj) URL.revokeObjectURL(obj);
    };
  }, [attempt, src]);

  if (failed) {
    return (
      <div className="w-28 h-28 rounded-lg border border-border flex flex-col gap-2 items-center justify-center text-dim text-xs text-center px-2">
        <span>图片加载失败</span>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-primary hover:underline"
          onClick={() => {
            setResolved(null);
            setFailed(false);
            setAttempt((value) => value + 1);
          }}
        >
          <RefreshCw className="w-3 h-3" /> 重试
        </button>
      </div>
    );
  }
  if (!resolved) {
    return <div className="w-28 h-28 rounded-lg border border-border bg-secondary animate-pulse" />;
  }
  return <ResolvedChatImage src={resolved} />;
}

function ResolvedChatImage({ src }: { src: string }) {
  return (
    <a href={src} target="_blank" rel="noopener noreferrer" className="block">
      <img
        src={src}
        alt="对话图片"
        className="max-w-[160px] max-h-[160px] rounded-lg border border-border object-cover hover:opacity-90 transition-opacity"
      />
    </a>
  );
}

function LoadFailure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center px-6">
      <AlertCircle className="w-8 h-8 mb-3 text-destructive" />
      <p className="text-sm text-dim max-w-md mb-4">{message}</p>
      <Button variant="outline" size="sm" className="gap-1.5" onClick={onRetry}>
        <RefreshCw className="w-4 h-4" /> 重试
      </Button>
    </div>
  );
}

function EmptyWelcome({ onNewSession }: { onNewSession: () => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center py-20">
      <div className="w-16 h-16 rounded-lg flex items-center justify-center mb-4 mx-auto" style={{ background: "color-mix(in srgb, var(--sig-accent) 10%, transparent)" }}>
        <MessageSquare className="w-8 h-8" style={{ color: "var(--sig-accent)" }} />
      </div>
      <div className="sig-kicker mb-2">// 问答演练场 / Q&A ARENA</div>
      <h3 className="sig-display text-2xl mb-2">自由提问<span className="sig-accent-c">.</span></h3>
      <p className="text-dim text-sm mb-6 max-w-sm">
        自由提问，深入学习技术知识。AI 导师会记住你之前的学习内容，帮你建立完整的知识体系。
      </p>
      <Button onClick={onNewSession} variant="cta" className="gap-1.5 px-5">
        <Plus className="w-4 h-4" /> 开始新对话
      </Button>
    </div>
  );
}

function EmptyChat({ onSend }: { onSend: (q: string) => void }) {
  const questions = useMemo(() => pickRandomQuestions(ALL_SUGGESTED_QUESTIONS, SUGGESTED_COUNT), []);
  return (
    <div className="max-w-3xl mx-auto flex flex-col items-center justify-center py-16">
      <div className="w-14 h-14 rounded-lg flex items-center justify-center mb-4 mx-auto" style={{ background: "color-mix(in srgb, var(--sig-accent) 10%, transparent)" }}>
        <MessageSquare className="w-7 h-7" style={{ color: "var(--sig-accent)" }} />
      </div>
      <h3 className="text-base font-medium mb-1 text-center">有什么想问的？</h3>
      <p className="text-dim text-sm mb-6 text-center">试试下面的问题，或者直接输入你的疑问</p>
      <div className="grid gap-2 w-full max-w-md">
        {questions.map((q, i) => (
          <button
            key={i}
            className="sig-card sig-hover-lift text-left px-4 py-3 text-sm transition-all duration-200"
            onClick={() => onSend(q)}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
