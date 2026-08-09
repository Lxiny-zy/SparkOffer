import { useState, useRef, useEffect, useCallback, KeyboardEvent } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Markdown } from "./ChatBubble";
import { X, Send, Trash2 } from "lucide-react";
import { streamAssistantChat, fetchAssistantHistory, clearAssistantHistory, fetchWelcomeMessage } from "../api/assistant";
import { startInterview } from "../api/interview";
import { cn } from "@/lib/utils";
import CatAvatar from "./CatAvatar";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface AssistantAction {
  action: string;
  path?: string;
  mode?: string;
  topic?: string;
  [key: string]: any;
}

const SNAP_THRESHOLD = 40;
const DRAG_THRESHOLD = 5; // px movement before treating gesture as a drag (otherwise click)
const BTN_SIZE = 64;
const STORAGE_KEY = "sparkoffer.assistant.position";

interface SavedPosition {
  x: number;
  y: number;
  peeking: boolean;
}

function loadSavedPosition(): SavedPosition {
  if (typeof window === "undefined") return { x: 20, y: 0, peeking: false };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { x: 20, y: 0, peeking: false };
    const p = JSON.parse(raw);
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    return {
      x: typeof p.x === "number" ? Math.max(0, Math.min(vw - BTN_SIZE, p.x)) : 20,
      y: typeof p.y === "number" ? Math.max(0, Math.min(vh - BTN_SIZE - 60, p.y)) : 0,
      peeking: !!p.peeking,
    };
  } catch {
    return { x: 20, y: 0, peeking: false };
  }
}

function savePosition(pos: SavedPosition) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pos));
  } catch {
    // ignore storage failures (quota, private mode, etc.)
  }
}

export default function FloatingAssistant() {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  // Set while a tool action (e.g. start_interview) runs after the reply stream
  // ends — interview init takes 10–30s and used to give zero feedback.
  const [actionProgress, setActionProgress] = useState<string | null>(null);
  const [isHovered, setIsHovered] = useState(false);
  const [isIdle, setIsIdle] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  // --- Drag & peek state ---
  const [isMobile, setIsMobile] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [isPeekHovered, setIsPeekHovered] = useState(false);
  // Hydrate from localStorage on first render so refresh keeps user's chosen spot.
  const [isPeeking, setIsPeeking] = useState(() => loadSavedPosition().peeking);
  const [btnPos, setBtnPos] = useState(() => {
    const p = loadSavedPosition();
    return { x: p.x, y: p.y };
  });
  // Refs for drag-vs-click discrimination: dragStartRef holds the pointer-down anchor,
  // didDragRef flips true once the pointer crosses DRAG_THRESHOLD so we can swallow the
  // synthetic onClick that follows a real drag.
  const dragStartRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const didDragRef = useRef(false);
  const hasOpenedOnceRef = useRef(false);
  // Flips true once the user drags the bubble this session — their chosen spot
  // then wins over the route-based auto-peek below.
  const userMovedRef = useRef(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const rafRef = useRef<number | null>(null);
  const isStreamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const navigate = useNavigate();
  const location = useLocation();

  // On mobile answer pages (interview / review / arena / card training) the 64px
  // bubble sits right on top of the composer & send button. Retreat to the slim
  // edge-peek strip there, unless the user has dragged the bubble this session.
  const isAnswerRoute = /^\/(interview|review|qa-arena|knowledge-training)/.test(location.pathname);
  const effectivePeeking = isPeeking || (isMobile && isAnswerRoute && !userMovedRef.current);

  // Detect mobile
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 640);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  // Abort any in-flight assistant stream on unmount and block setState afterwards.
  useEffect(() => () => {
    mountedRef.current = false;
    abortRef.current?.abort();
  }, []);

  // Load chat history & welcome message on first open
  useEffect(() => {
    if (!isOpen || historyLoaded) return;
    setHistoryLoaded(true);
    (async () => {
      try {
        const history = await fetchAssistantHistory();
        if (history.length > 0) {
          setMessages(history.map((m: any) => ({ role: m.role, content: m.content })));
        } else {
          const welcome = await fetchWelcomeMessage();
          if (welcome) {
            setMessages([{ role: "assistant", content: welcome }]);
          }
        }
      } catch (err) {
        console.warn("Failed to load assistant history:", err);
      }
    })();
  }, [isOpen, historyLoaded]);

  useEffect(() => {
    if (isOpen && inputRef.current) inputRef.current.focus();
  }, [isOpen]);

  useEffect(() => {
    if (!scrollRef.current) return;
    // Skip smooth-scroll during streaming — flushUpdate handles instant scroll
    if (isStreamingRef.current) return;
    scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isStreaming, actionProgress]);

  // Idle detection
  useEffect(() => {
    setIsIdle(false);
    if (!isOpen || isStreaming || messages.length === 0) return;
    const timer = setTimeout(() => setIsIdle(true), 30000);
    return () => clearTimeout(timer);
  }, [isStreaming, messages.length, isOpen]);

  // Reset peek state when the user closes an opened chat — but preserve the
  // restored peek state on initial mount (otherwise refreshing would always
  // drop the user back to the floating bubble).
  useEffect(() => {
    if (isOpen) {
      hasOpenedOnceRef.current = true;
    } else if (hasOpenedOnceRef.current) {
      setIsPeeking(false);
    }
  }, [isOpen]);

  // Persist position + peek state so refresh keeps the user's setup.
  useEffect(() => {
    if (isDragging) return; // avoid spamming storage during a drag — save on release
    savePosition({ x: btnPos.x, y: btnPos.y, peeking: isPeeking });
  }, [btnPos, isPeeking, isDragging]);

  // Re-clamp position when the window shrinks so the button never escapes the viewport.
  useEffect(() => {
    const handle = () => {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      setBtnPos((prev) => ({
        x: Math.max(0, Math.min(vw - BTN_SIZE, prev.x)),
        y: Math.max(0, Math.min(vh - BTN_SIZE - 60, prev.y)),
      }));
    };
    window.addEventListener("resize", handle);
    return () => window.removeEventListener("resize", handle);
  }, []);

  // --- Drag handlers ---
  // Pattern: pointer-down records the start point; pointer-move only enters
  // drag mode once movement exceeds DRAG_THRESHOLD (so a quick tap still opens
  // chat). On pointer-up we either snap-to-edge (if dragged) or let the
  // synthetic click event fire onClick → open chat.
  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    dragStartRef.current = { x: btnPos.x, y: btnPos.y, px: e.clientX, py: e.clientY };
    didDragRef.current = false;
  }, [btnPos]);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    const start = dragStartRef.current;
    if (!start) return;
    const dx = e.clientX - start.px;
    const dy = e.clientY - start.py;

    if (!didDragRef.current) {
      // Still below threshold → treat as potential click
      if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
      didDragRef.current = true;
      setIsDragging(true);
    }
    e.preventDefault();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const nextX = start.x - dx; // dx>0 (pointer right) → reduce right offset → button moves right
    const nextY = start.y - dy;
    setBtnPos({
      x: Math.max(0, Math.min(vw - BTN_SIZE, nextX)),
      y: Math.max(0, Math.min(vh - BTN_SIZE - 60, nextY)),
    });
  }, []);

  const handlePointerUp = useCallback((e: React.PointerEvent) => {
    const start = dragStartRef.current;
    if (!start) return;
    (e.currentTarget as Element).releasePointerCapture?.(e.pointerId);
    dragStartRef.current = null;
    const wasDrag = didDragRef.current;
    setIsDragging(false);

    if (wasDrag) {
      userMovedRef.current = true;
      // Snap to right edge → peek mode. Use functional update so we read latest pos.
      setBtnPos((prev) => {
        if (prev.x < SNAP_THRESHOLD) {
          setIsPeeking(true);
          return { ...prev, x: 0 };
        }
        return prev;
      });
    }
    // didDragRef is reset in handleButtonClick (which fires right after pointer-up)
  }, []);

  const handleButtonClick = useCallback(() => {
    if (didDragRef.current) {
      didDragRef.current = false; // clear for next gesture
      return; // swallow click that was actually the end of a drag
    }
    setIsOpen(true);
  }, []);

  const handlePeekClick = useCallback(() => {
    setIsPeeking(false);
    setIsOpen(true);
  }, []);

  // Mobile chat panel: push the interview input area down
  const chatPanelClass = isMobile
    ? "bottom-0 right-0 left-0 sm:left-auto w-full sm:w-[400px] sm:max-w-[calc(100vw-2rem)] h-[100dvh] sm:h-[600px] sm:max-h-[calc(100vh-4rem)] sm:bottom-6 sm:right-6 rounded-t-lg sm:rounded-lg"
    : "bottom-6 right-6 w-[400px] max-w-[calc(100vw-2rem)] h-[600px] max-h-[calc(100vh-4rem)] rounded-lg";

  const handleClearHistory = useCallback(async () => {
    try {
      await clearAssistantHistory();
      setMessages([]);
    } catch (err) {
      console.warn("Failed to clear history:", err);
    }
  }, []);

  const handleAction = useCallback(async (action: AssistantAction) => {
    if (action.action === "navigate") {
      navigate(action.path!);
      setIsOpen(false);
    } else if (action.action === "start_interview") {
      setActionProgress("正在为你准备面试...");
      try {
        const data = await startInterview(action.mode, action.topic, {
          onProgress: (msg) => {
            if (mountedRef.current) setActionProgress(msg || "正在为你准备面试...");
          },
        });
        if (!mountedRef.current) return;
        navigate(`/interview/${data.session_id}`, { state: data });
        setIsOpen(false);
      } catch (err: any) {
        if (!mountedRef.current) return;
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `启动面试失败: ${err.message}` },
        ]);
      } finally {
        if (mountedRef.current) setActionProgress(null);
      }
    }
  }, [navigate]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || isStreamingRef.current) return;

    const userMsg: Message = { role: "user", content: text };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setIsStreaming(true);
    isStreamingRef.current = true;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    let assistantContent = "";
    let pendingUpdate = false;
    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

    const flushUpdate = () => {
      rafRef.current = null;
      pendingUpdate = false;
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: assistantContent };
        return updated;
      });
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    };

    try {
      for await (const event of streamAssistantChat(text, controller.signal)) {
        if (event.type === "token") {
          assistantContent += event.content;
          if (!pendingUpdate) {
            pendingUpdate = true;
            rafRef.current = requestAnimationFrame(flushUpdate);
          }
        } else if (event.type === "action") {
          handleAction(event as any);
        } else if (event.type === "done") {
          break;
        }
      }
    } catch (err: any) {
      if (!controller.signal.aborted) {
        // Append the error to whatever streamed successfully — replacing would
        // wipe a half-useful answer the user may already be reading.
        assistantContent = assistantContent
          ? `${assistantContent}\n\n> ⚠️ 回复中断：${err.message}`
          : `出错了: ${err.message}`;
      }
    } finally {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      // Skip state updates if the component unmounted or this stream was aborted.
      if (mountedRef.current && !controller.signal.aborted) {
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = { role: "assistant", content: assistantContent };
          return updated;
        });
        isStreamingRef.current = false;
        setIsStreaming(false);
      }
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <>
      {/* Chat Panel */}
      {isOpen && (
        <div
          className={cn(
            "sig-root sig-card fixed z-50 flex flex-col overflow-hidden animate-fade-in shadow-[0_12px_48px_rgba(0,0,0,0.22)]",
            chatPanelClass
          )}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--sig-line)" }}>
            <div className="flex items-center gap-2.5">
              <CatAvatar size={36} mood={isStreaming ? "thinking" : (isIdle ? "sleepy" : "happy")} />
              <div>
                <div className="text-sm font-medium text-text">小鱼学姐</div>
                <div className="text-[11px] text-muted-fg">你的面试陪练伙伴</div>
              </div>
            </div>
            <div className="flex items-center gap-1">
              {messages.length > 0 && (
                <button
                  onClick={handleClearHistory}
                  className="w-8 h-8 rounded-md flex items-center justify-center text-muted-fg hover:text-[color:var(--sig-danger)] hover:bg-secondary transition-all active:scale-[0.93]"
                  title="清空对话"
                >
                  <Trash2 size={14} />
                </button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="w-8 h-8 rounded-md flex items-center justify-center text-muted-fg hover:text-text hover:bg-secondary transition-all active:scale-[0.93]"
              >
                <X size={16} />
              </button>
            </div>
          </div>

          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
            {messages.length === 0 && (
              <div className="text-center py-6">
                <CatAvatar size={64} className="mx-auto mb-3" mood="happy" />
                <div className="text-sm font-medium text-text mb-1">嗨～我是小鱼</div>
                <div className="text-[13px] text-muted-fg mb-4">有什么面试问题都可以问我呀</div>
                <div className="flex flex-col gap-2">
                  {["帮我分析一下现在的水平", "有哪些知识点该复习了？", "帮我开始一场面试", "我的思维模式有什么特点？"].map((q) => (
                    <button
                      key={q}
                      onClick={() => { setInput(q); }}
                      className="sig-btn justify-start text-left text-[13px]"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => {
              if (msg.role === "assistant" && !msg.content && isStreaming && i === messages.length - 1) return null;
              return (
              <div key={i} className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
                {msg.role === "assistant" && (
                  <div className="w-6 h-6 shrink-0 mr-1.5 mt-1">
                    <CatAvatar size={24} mood="static" />
                  </div>
                )}
                <div
                  className={cn(
                    "max-w-[80%] px-4 py-2.5 text-sm leading-relaxed",
                    msg.role === "user"
                      ? "rounded-lg rounded-tr-sm bg-primary text-primary-foreground"
                      : "rounded-lg rounded-tl-sm bg-secondary text-secondary-foreground"
                  )}
                >
                  {msg.role === "assistant" ? (
                    <div className="md-content">
                      <Markdown>{msg.content || "..."}</Markdown>
                    </div>
                  ) : (
                    msg.content
                  )}
                </div>
              </div>
              );
            })}

            {isStreaming && messages[messages.length - 1]?.role === "assistant" && !messages[messages.length - 1]?.content && (
              <div className="flex items-center gap-1.5 px-2">
                <div className="w-6 h-6 shrink-0">
                  <CatAvatar size={24} mood="static" />
                </div>
                <div className="flex gap-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot" />
                  <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.2s]" />
                  <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.4s]" />
                </div>
              </div>
            )}

            {actionProgress && (
              <div className="flex items-center gap-2 px-2 animate-fade-in">
                <div className="w-6 h-6 shrink-0">
                  <CatAvatar size={24} mood="static" />
                </div>
                <div className="flex items-center gap-2 rounded-lg rounded-tl-sm bg-secondary px-3 py-2 text-[13px] text-secondary-foreground">
                  <span className="flex gap-1">
                    <span className="w-1 h-1 rounded-full bg-primary animate-pulse-dot" />
                    <span className="w-1 h-1 rounded-full bg-primary animate-pulse-dot [animation-delay:0.2s]" />
                    <span className="w-1 h-1 rounded-full bg-primary animate-pulse-dot [animation-delay:0.4s]" />
                  </span>
                  {actionProgress}
                </div>
              </div>
            )}
          </div>

          {/* Input */}
          <div className="px-4 py-3 border-t" style={{ borderColor: "var(--sig-line)" }}>
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="和小鱼聊聊吧～"
                rows={1}
                className="sig-textarea flex-1 max-h-[80px]"
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || isStreaming}
                className="sig-btn sig-btn-accent w-10 h-10 p-0 shrink-0"
              >
                <Send size={16} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Peeking Cat (hidden edge state on mobile / answer routes) */}
      {/* Clamp the bottom so a stale btnPos from a larger viewport doesn't push the peek off-screen. */}
      {effectivePeeking && !isOpen && (() => {
        const vh = typeof window !== "undefined" ? window.innerHeight : 800;
        const peekBottom = Math.max(0, Math.min(vh - 80, btnPos.y + 16));
        return (
        <button
          onClick={handlePeekClick}
          onMouseEnter={() => setIsPeekHovered(true)}
          onMouseLeave={() => setIsPeekHovered(false)}
          className="fixed right-0 z-50 flex items-end cursor-pointer group transition-[bottom] duration-200"
          style={{ height: "80px", bottom: peekBottom }}
        >
          {/* Peek body — a strip showing the cat peeking from right edge */}
          <div className="relative overflow-hidden animate-cat-peek">
            {/* Cat peek preview: only the left half of the cat face showing from right edge */}
            <div
              className={cn(
                "relative transition-all duration-300",
                isPeekHovered ? "w-[72px] opacity-100" : "w-[48px] opacity-90"
              )}
              style={{ height: "72px" }}
            >
              {/* Background pill */}
              <div className="absolute inset-0 bg-card rounded-l-lg border flex items-center justify-center overflow-hidden" style={{ borderColor: "var(--sig-line)", boxShadow: "0 6px 24px rgba(0,0,0,0.16)" }}>
                {/* Sway wrapper: gentle peek-out motion when not hovered (looks like the cat
                    is actually peeking from behind the edge), settles on hover for stability. */}
                <div className={isPeekHovered ? "" : "animate-cat-peek-sway"}>
                  <CatAvatar
                    size={isPeekHovered ? 52 : 44}
                    mood={isPeekHovered ? "curious" : "idle"}
                    className="transition-all duration-300"
                  />
                </div>
                {/* "Tap me!" bubble on hover */}
                {isPeekHovered && (
                  <div className="absolute -top-8 left-1/2 -translate-x-1/2 whitespace-nowrap px-2 py-1 rounded-lg bg-primary text-primary-foreground text-[11px] font-medium shadow-md animate-fade-in-up">
                    点我聊聊
                  </div>
                )}
              </div>
            </div>
          </div>
        </button>
        );
      })()}

      {/* Floating Cat Bubble (normal / draggable) */}
      {!isOpen && !effectivePeeking && (
        <button
          onClick={handleButtonClick}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          className={cn(
            "fixed z-50 w-16 h-16 rounded-full shadow-lg",
            "flex items-center justify-center group bg-card border border-border/50",
            "hover:border-primary/30 hover:shadow-xl",
            isDragging ? "cursor-grabbing shadow-xl scale-105" : "cursor-grab",
            isMobile && "active:scale-[0.9]"
          )}
          style={{
            right: btnPos.x,
            bottom: btnPos.y + 16,
            // No transition while dragging — pointer drives position 1:1.
            // After release, smoothly settle into the final spot.
            transition: isDragging
              ? "none"
              : "right 0.25s cubic-bezier(0.2,0,0,1), bottom 0.25s cubic-bezier(0.2,0,0,1), box-shadow 0.2s, transform 0.2s",
            touchAction: "none", // prevent page scrolling while dragging on touch devices
          }}
          title="拖动我或点我聊聊～（拖到右边缘会收起为偷看状态）"
        >
          <CatAvatar
            size={52}
            mood={isDragging ? "curious" : isHovered ? "curious" : "idle"}
            className={cn(
              "transition-transform duration-300",
              isHovered ? "scale-110 -translate-y-0.5" : ""
            )}
          />
          <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-tertiary border-2 border-card animate-pulse-dot" />
        </button>
      )}
    </>
  );
}
