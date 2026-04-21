import { useState, useRef, useEffect, useCallback, KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
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

export default function FloatingAssistant() {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [isIdle, setIsIdle] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();

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
          // No history — try welcome message
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
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isStreaming]);

  // Idle detection: flip to idle after 30s of no streaming
  useEffect(() => {
    setIsIdle(false);
    if (!isOpen || isStreaming || messages.length === 0) return;
    const timer = setTimeout(() => setIsIdle(true), 30000);
    return () => clearTimeout(timer);
  }, [isStreaming, messages.length, isOpen]);

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
      try {
        const data = await startInterview(action.mode, action.topic);
        navigate(`/interview/${data.session_id}`, { state: data });
        setIsOpen(false);
      } catch (err: any) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `启动面试失败: ${err.message}` },
        ]);
      }
    }
  }, [navigate]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    const userMsg: Message = { role: "user", content: text };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setIsStreaming(true);

    let assistantContent = "";
    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

    try {
      for await (const event of streamAssistantChat(text)) {
        if (event.type === "token") {
          assistantContent += event.content;
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: "assistant", content: assistantContent };
            return updated;
          });
        } else if (event.type === "action") {
          handleAction(event as any);
        } else if (event.type === "done") {
          break;
        }
      }
    } catch (err: any) {
      assistantContent = `出错了: ${err.message}`;
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: assistantContent };
        return updated;
      });
    } finally {
      setIsStreaming(false);
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
      {/* Floating Cat Bubble */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          className="fixed bottom-5 right-5 z-50 w-16 h-16 rounded-full shadow-lg hover:shadow-xl transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] active:scale-[0.9] flex items-center justify-center group bg-card border border-border/50 hover:border-primary/30"
          title="找小鱼学姐聊聊～"
        >
          <CatAvatar
            size={52}
            mood={isHovered ? "curious" : "idle"}
            className={cn(
              "transition-transform duration-300",
              isHovered ? "scale-110 -translate-y-0.5" : ""
            )}
          />
          {/* Notification dot */}
          <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-tertiary border-2 border-card animate-pulse-dot" />
        </button>
      )}

      {/* Chat Panel */}
      {isOpen && (
        <div className="fixed bottom-0 right-0 sm:bottom-6 sm:right-6 z-50 w-full sm:w-[400px] sm:max-w-[calc(100vw-2rem)] h-[calc(100vh-3rem)] sm:h-[600px] sm:max-h-[calc(100vh-4rem)] rounded-t-3xl sm:rounded-3xl bg-card shadow-2xl flex flex-col overflow-hidden animate-fade-in border border-border/50">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border/50 bg-gradient-to-r from-tertiary/5 to-primary/5">
            <div className="flex items-center gap-2.5">
              <CatAvatar size={36} mood={isStreaming ? "thinking" : (isIdle ? "sleepy" : "happy")} />
              <div>
                <div className="text-sm font-medium text-text">小鱼学姐 🐟</div>
                <div className="text-[11px] text-muted-fg">你的面试陪练伙伴～</div>
              </div>
            </div>
            <div className="flex items-center gap-1">
              {messages.length > 0 && (
                <button
                  onClick={handleClearHistory}
                  className="w-8 h-8 rounded-full flex items-center justify-center text-muted-fg hover:text-red-500 hover:bg-red-500/8 transition-all active:scale-[0.93]"
                  title="清空对话"
                >
                  <Trash2 size={14} />
                </button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="w-8 h-8 rounded-full flex items-center justify-center text-muted-fg hover:text-text hover:bg-primary/8 transition-all active:scale-[0.93]"
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
                <div className="text-sm font-medium text-text mb-1">嗨～我是小鱼 🐟</div>
                <div className="text-[13px] text-muted-fg mb-4">有什么面试问题都可以问我呀～</div>
                <div className="flex flex-col gap-2">
                  {["帮我分析一下现在的水平", "有哪些知识点该复习了？", "帮我开始一场面试", "我的思维模式有什么特点？"].map((q) => (
                    <button
                      key={q}
                      onClick={() => { setInput(q); }}
                      className="px-4 py-2 rounded-full bg-secondary text-secondary-foreground text-sm hover:bg-secondary/80 transition-all active:scale-[0.97]"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => {
              // Skip empty assistant messages that are still streaming (shown as dots below)
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
                      ? "rounded-3xl rounded-tr-lg bg-primary text-primary-foreground"
                      : "rounded-3xl rounded-tl-lg bg-secondary text-secondary-foreground"
                  )}
                >
                  {msg.role === "assistant" ? (
                    <div className="md-content">
                      <ReactMarkdown>{msg.content || "..."}</ReactMarkdown>
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
          </div>

          {/* Input */}
          <div className="px-4 py-3 border-t border-border/50">
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="和小鱼聊聊吧～"
                rows={1}
                className="flex-1 resize-none rounded-2xl bg-muted px-4 py-2.5 text-sm text-text placeholder:text-muted-fg/50 focus:outline-none focus:ring-2 focus:ring-primary/30 max-h-[80px]"
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || isStreaming}
                className="w-10 h-10 rounded-full bg-primary text-primary-foreground flex items-center justify-center shrink-0 hover:bg-primary/90 transition-all active:scale-[0.93] disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Send size={16} />
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
