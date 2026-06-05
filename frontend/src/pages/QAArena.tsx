import { useState, useRef, useEffect, useCallback, memo, useMemo } from "react";
import {
  Plus, Trash2, Send, Download, FileText, Loader2,
  MessageSquare, Pencil, Check, X, PanelLeftOpen, Eraser, Square,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { markdownComponents } from "../components/ChatBubble";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  createQASession,
  listQASessions,
  deleteQASession,
  renameQASession,
  loadQAMessages,
  clearQAMessages,
  streamQAChat,
  generateQASummary,
  downloadMarkdown,
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

function pickRandomQuestions(pool: string[], count: number): string[] {
  const shuffled = [...pool].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, count);
}

export default function QAArena() {
  const [sessions, setSessions] = useState<QASession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<QAMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isSummarizing, setIsSummarizing] = useState(false);
  const [summaryProgress, setSummaryProgress] = useState("");
  const [summaryResult, setSummaryResult] = useState<QASummaryResult | null>(null);
  const [showSummary, setShowSummary] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [loaded, setLoaded] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const rafRef = useRef<number | null>(null);
  const isStreamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  // Load sessions on mount
  useEffect(() => {
    listQASessions().then((data) => {
      setSessions(data.sessions);
      if (data.sessions.length > 0) {
        switchSession(data.sessions[0].id);
      }
      setLoaded(true);
    });
  }, []);

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

  const switchReqRef = useRef<string | null>(null);
  const switchSession = useCallback(async (id: string) => {
    switchReqRef.current = id;
    setActiveId(id);
    setSummaryResult(null);
    setShowSummary(false);
    const msgs = await loadQAMessages(id);
    // Guard against out-of-order responses: if the user switched again while this
    // load was in flight, don't clobber the newer session's messages.
    if (switchReqRef.current === id) setMessages(msgs);
  }, []);

  const handleNewSession = async () => {
    const session = await createQASession();
    setSessions((prev) => [session, ...prev]);
    setActiveId(session.id);
    setMessages([]);
    setSummaryResult(null);
    setShowSummary(false);
    inputRef.current?.focus();
  };

  const handleDeleteSession = async (id: string) => {
    await deleteQASession(id);
    const remaining = sessions.filter((s) => s.id !== id);
    setSessions(remaining);
    if (activeId === id) {
      if (remaining.length > 0) {
        switchSession(remaining[0].id);
      } else {
        setActiveId(null);
        setMessages([]);
      }
    }
  };

  const handleRename = async (id: string) => {
    const title = editTitle.trim();
    if (!title) {
      setEditingId(null);
      return;
    }
    await renameQASession(id, title);
    setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title } : s)));
    setEditingId(null);
  };

  const handleSend = async (overrideText?: string) => {
    const text = (overrideText || input).trim();
    if (!text || isStreaming || !activeId) return;

    const userMsg: QAMessage = { role: "user", content: text, created_at: new Date().toISOString() };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    // Reset textarea height
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
    setIsStreaming(true);
    isStreamingRef.current = true;

    const controller = new AbortController();
    abortRef.current = controller;

    let assistantContent = "";
    let pendingUpdate = false;
    setMessages((prev) => [...prev, { role: "assistant", content: "", created_at: "" }]);

    const flushUpdate = () => {
      rafRef.current = null;
      pendingUpdate = false;
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: assistantContent, created_at: "" };
        return updated;
      });
      // Scroll to bottom during streaming
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    };

    try {
      for await (const event of streamQAChat(activeId, text, controller.signal)) {
        if (event.type === "token") {
          assistantContent += event.content;
          if (!pendingUpdate) {
            pendingUpdate = true;
            rafRef.current = requestAnimationFrame(flushUpdate);
          }
        } else if (event.type === "done") {
          break;
        }
      }
    } catch (err: any) {
      if (err.name !== "AbortError") {
        assistantContent += assistantContent ? "\n\n[出错了: " + err.message + "]" : `出错了: ${err.message}`;
      }
    } finally {
      // Cancel any pending RAF and do a final flush
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: assistantContent, created_at: "" };
        return updated;
      });
      abortRef.current = null;
      isStreamingRef.current = false;
      setIsStreaming(false);
      listQASessions().then((data) => setSessions(data.sessions));
    }
  };

  const handleStop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
  };

  const handleClearMessages = async () => {
    if (!activeId) return;
    await clearQAMessages(activeId);
    setMessages([]);
  };

  const handleGenerateSummary = async () => {
    if (!activeId || isSummarizing) return;
    setIsSummarizing(true);
    setSummaryProgress("正在分析对话内容...");
    try {
      const result = await generateQASummary(activeId, (msg) => setSummaryProgress(msg));
      setSummaryResult(result);
      setShowSummary(true);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setIsSummarizing(false);
      setSummaryProgress("");
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
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* ── Left sidebar: session list ── */}
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col border-r border-border bg-bg shrink-0 w-64">
        <div className="p-3 flex items-center gap-2 border-b border-border">
          <Button variant="outline" size="sm" className="flex-1 gap-1.5" onClick={handleNewSession}>
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
              onClick={() => s.id !== activeId && switchSession(s.id)}
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
                    onClick={(e) => e.stopPropagation()}
                  />
                  <button onClick={(e) => { e.stopPropagation(); handleRename(s.id); }} className="text-primary"><Check className="w-3.5 h-3.5" /></button>
                  <button onClick={(e) => { e.stopPropagation(); setEditingId(null); }} className="text-dim"><X className="w-3.5 h-3.5" /></button>
                </div>
              ) : (
                <>
                  <span className="flex-1 text-sm truncate">{s.title}</span>
                  <div className="hidden group-hover:flex items-center gap-0.5">
                    <button
                      onClick={(e) => { e.stopPropagation(); setEditingId(s.id); setEditTitle(s.title); }}
                      className="p-0.5 rounded hover:bg-muted text-dim"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button
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
            <div className="p-6 text-center text-dim text-sm">还没有对话，开始一个吧</div>
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
                <Button variant="outline" size="sm" className="flex-1 gap-1.5" onClick={handleNewSession}>
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
                    onClick={() => { switchSession(s.id); setSidebarOpen(false); }}
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
                          onClick={(e) => e.stopPropagation()}
                        />
                        <button onClick={(e) => { e.stopPropagation(); handleRename(s.id); }} className="text-primary"><Check className="w-3.5 h-3.5" /></button>
                        <button onClick={(e) => { e.stopPropagation(); setEditingId(null); }} className="text-dim"><X className="w-3.5 h-3.5" /></button>
                      </div>
                    ) : (
                      <>
                        <span className="flex-1 text-sm truncate">{s.title}</span>
                        <div className="hidden group-hover:flex items-center gap-0.5">
                          <button
                            onClick={(e) => { e.stopPropagation(); setEditingId(s.id); setEditTitle(s.title); }}
                            className="p-0.5 rounded hover:bg-muted text-dim"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
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
                  <div className="p-6 text-center text-dim text-sm">还没有对话，开始一个吧</div>
                )}
              </div>
            </div>
          </aside>
          <div className="flex-1 bg-black/50 backdrop-blur-sm" onClick={() => setSidebarOpen(false)} />
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
                disabled={isStreaming || messages.length === 0}
              >
                <Eraser className="w-4 h-4" /> 清空
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="sm:hidden h-8 w-8 text-dim"
                onClick={handleClearMessages}
                disabled={isStreaming || messages.length === 0}
                title="清空消息"
              >
                <Eraser className="w-4 h-4" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="gap-1 hidden sm:inline-flex"
                onClick={handleGenerateSummary}
                disabled={isStreaming || isSummarizing || messages.length < 2}
              >
                {isSummarizing ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
                生成知识卡片
              </Button>
              <Button
                variant="outline"
                size="icon"
                className="sm:hidden h-8 w-8"
                onClick={handleGenerateSummary}
                disabled={isStreaming || isSummarizing || messages.length < 2}
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
          {!activeId ? (
            <EmptyWelcome onNewSession={handleNewSession} />
          ) : messages.length === 0 ? (
            <EmptyChat onSend={(q) => handleSend(q)} />
          ) : (
            <div className="max-w-3xl mx-auto space-y-6">
              {messages.map((m, i) => (
                <ChatMessage key={i} role={m.role} content={m.content} />
              ))}
              {isStreaming && messages[messages.length - 1]?.content === "" && (
                <div className="flex items-center gap-2 text-dim text-sm">
                  <Loader2 className="w-4 h-4 animate-spin" /> 思考中...
                </div>
              )}
            </div>
          )}
        </div>

        {/* Input */}
        {activeId && (
          <div className="px-3 md:px-8 py-3 border-t border-border/50 safe-area-bottom backdrop-blur-sm bg-card/30">
            <div className="max-w-3xl mx-auto flex items-end gap-2">
              <textarea
                ref={inputRef}
                className="flex-1 resize-none rounded-2xl border border-border bg-bg/80 backdrop-blur-sm px-4 py-3 text-[15px] md:text-sm outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/40 transition-all min-h-[44px]"
                rows={1}
                placeholder="输入你的问题... (Enter 发送)"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onInput={(e) => {
                  const t = e.target as HTMLTextAreaElement;
                  t.style.height = "auto";
                  t.style.height = Math.min(t.scrollHeight, 160) + "px";
                }}
                disabled={isStreaming}
              />
              {isStreaming ? (
                <Button
                  size="icon"
                  variant="destructive"
                  className="rounded-xl h-11 w-11 md:h-10 md:w-10 shrink-0 shadow-md"
                  onClick={handleStop}
                  title="停止生成"
                >
                  <Square className="w-4 h-4" />
                </Button>
              ) : (
                <Button
                  size="icon"
                  className="rounded-xl h-11 w-11 md:h-10 md:w-10 shrink-0 shadow-md"
                  onClick={() => handleSend()}
                  disabled={!input.trim()}
                >
                  <Send className="w-4 h-4" />
                </Button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Summary slide-over ── */}
      {showSummary && summaryResult && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={() => setShowSummary(false)}>
          <div
            className="w-full max-w-lg bg-bg h-full overflow-y-auto shadow-2xl animate-slide-in-right flex flex-col md:max-w-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-border flex items-center justify-between sticky top-0 bg-bg z-10">
              <h3 className="font-medium text-base">知识卡片预览</h3>
              <div className="flex items-center gap-2">
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
            <div className="px-5 py-4 flex-1">
              <div className="md-content text-sm leading-[1.8]">
                <ReactMarkdown components={markdownComponents}>{summaryResult.content}</ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const ChatMessage = memo(function ChatMessage({ role, content }: { role: string; content: string }) {
  if (role === "user") {
    return (
      <div className="flex justify-end animate-fade-in">
        <div className="max-w-[75%] px-4 py-2.5 rounded-3xl rounded-tr-lg text-primary-foreground text-[15px] leading-[1.7] whitespace-pre-wrap shadow-md" style={{ background: "linear-gradient(135deg, var(--primary), color-mix(in srgb, var(--primary) 80%, var(--aurora-2)))" }}>
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col animate-fade-in">
      <div className="max-w-full leading-[1.8] text-[15px] text-text rounded-2xl rounded-tl-lg px-4 py-3 bg-card/60 backdrop-blur-sm border border-border/50 shadow-sm">
        <div className="md-content">
          <ReactMarkdown components={markdownComponents}>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
});

function EmptyWelcome({ onNewSession }: { onNewSession: () => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center py-20 relative">
      <div className="absolute inset-0 pointer-events-none flex items-center justify-center">
        <div className="w-[400px] h-[400px] rounded-full morph-blob opacity-30" style={{ background: "radial-gradient(circle, var(--glow-accent), transparent 70%)" }} />
      </div>
      <div className="relative">
        <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mb-4 mx-auto hover-glow">
          <MessageSquare className="w-8 h-8 text-primary" />
        </div>
        <h3 className="text-lg font-medium mb-2">问答演练场</h3>
        <p className="text-dim text-sm mb-6 max-w-sm">
          自由提问，深入学习技术知识。AI 导师会记住你之前的学习内容，帮你建立完整的知识体系。
        </p>
        <Button onClick={onNewSession} className="gap-1.5 cta-gradient rounded-xl px-5">
          <Plus className="w-4 h-4" /> 开始新对话
        </Button>
      </div>
    </div>
  );
}

function EmptyChat({ onSend }: { onSend: (q: string) => void }) {
  const questions = useMemo(() => pickRandomQuestions(ALL_SUGGESTED_QUESTIONS, SUGGESTED_COUNT), []);
  return (
    <div className="max-w-3xl mx-auto flex flex-col items-center justify-center py-16 relative">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[300px] h-[200px] rounded-full pointer-events-none opacity-25" style={{ background: "radial-gradient(ellipse, var(--glow-accent), transparent 70%)" }} />
      <div className="relative">
        <div className="w-14 h-14 rounded-2xl bg-primary/10 flex items-center justify-center mb-4 mx-auto">
          <MessageSquare className="w-7 h-7 text-primary" />
        </div>
        <h3 className="text-base font-medium mb-1 text-center">有什么想问的？</h3>
        <p className="text-dim text-sm mb-6 text-center">试试下面的问题，或者直接输入你的疑问</p>
        <div className="grid gap-2 w-full max-w-md">
          {questions.map((q, i) => (
            <button
              key={i}
              className="text-left px-4 py-3 rounded-xl border border-border hover:bg-secondary/50 hover:border-primary/30 text-sm transition-all duration-200 hover:shadow-sm"
              onClick={() => onSend(q)}
            >
              {q}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
