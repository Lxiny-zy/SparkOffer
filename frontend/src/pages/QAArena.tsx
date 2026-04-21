import { useState, useRef, useEffect, useCallback } from "react";
import {
  Plus, Trash2, Send, Download, FileText, Loader2,
  MessageSquare, Pencil, Check, X, PanelLeftClose, PanelLeftOpen, Eraser,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
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

const SUGGESTED_QUESTIONS = [
  "Redis 缓存穿透、缓存击穿、缓存雪崩的区别和解决方案？",
  "Python 的 GIL 是什么？它如何影响多线程？",
  "TCP 三次握手和四次挥手的过程是什么？",
  "数据库索引的原理是什么？B+ 树和哈希索引的区别？",
];

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
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [loaded, setLoaded] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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

  // Auto-scroll
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Focus input on session switch
  useEffect(() => {
    if (activeId) inputRef.current?.focus();
  }, [activeId]);

  const switchSession = useCallback(async (id: string) => {
    setActiveId(id);
    setSummaryResult(null);
    setShowSummary(false);
    const msgs = await loadQAMessages(id);
    setMessages(msgs);
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
    setIsStreaming(true);

    let assistantContent = "";
    setMessages((prev) => [...prev, { role: "assistant", content: "", created_at: "" }]);

    try {
      for await (const event of streamQAChat(activeId, text)) {
        if (event.type === "token") {
          assistantContent += event.content;
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: "assistant", content: assistantContent, created_at: "" };
            return updated;
          });
        } else if (event.type === "done") {
          break;
        }
      }
    } catch (err: any) {
      assistantContent = `出错了: ${err.message}`;
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: assistantContent, created_at: "" };
        return updated;
      });
    } finally {
      setIsStreaming(false);
      listQASessions().then((data) => setSessions(data.sessions));
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
    <div className="flex h-full overflow-hidden">
      {/* ── Left sidebar: session list ── */}
      <aside
        className={cn(
          "flex flex-col border-r border-border bg-bg shrink-0 transition-all duration-200",
          sidebarOpen ? "w-64" : "w-0 overflow-hidden border-r-0",
        )}
      >
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

      {/* ── Right main area ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-1 rounded-lg hover:bg-secondary text-dim">
            {sidebarOpen ? <PanelLeftClose className="w-5 h-5" /> : <PanelLeftOpen className="w-5 h-5" />}
          </button>
          <h2 className="text-base font-medium flex-1 truncate">
            {activeSession?.title || "问答演练场"}
          </h2>
          {activeId && (
            <div className="flex items-center gap-1.5">
              <Button
                variant="ghost"
                size="sm"
                className="gap-1 text-dim"
                onClick={handleClearMessages}
                disabled={isStreaming || messages.length === 0}
              >
                <Eraser className="w-4 h-4" /> 清空
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="gap-1"
                onClick={handleGenerateSummary}
                disabled={isStreaming || isSummarizing || messages.length < 2}
              >
                {isSummarizing ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
                生成知识卡片
              </Button>
              {isSummarizing && summaryProgress && (
                <span className="text-xs text-dim">{summaryProgress}</span>
              )}
            </div>
          )}
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 md:px-8 py-6">
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
          <div className="px-4 md:px-8 py-3 border-t border-border">
            <div className="max-w-3xl mx-auto flex items-end gap-2">
              <textarea
                ref={inputRef}
                className="flex-1 resize-none rounded-2xl border border-border bg-bg px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-primary/30 transition-shadow"
                rows={1}
                placeholder="输入你的问题... (Enter 发送, Shift+Enter 换行)"
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
              <Button
                size="icon"
                className="rounded-xl h-10 w-10 shrink-0"
                onClick={() => handleSend()}
                disabled={isStreaming || !input.trim()}
              >
                <Send className="w-4 h-4" />
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* ── Summary slide-over ── */}
      {showSummary && summaryResult && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={() => setShowSummary(false)}>
          <div
            className="w-full max-w-lg bg-bg h-full overflow-y-auto shadow-2xl animate-slide-in-right flex flex-col"
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
                <ReactMarkdown>{summaryResult.content}</ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ChatMessage({ role, content }: { role: string; content: string }) {
  if (role === "user") {
    return (
      <div className="flex justify-end animate-fade-in">
        <div className="max-w-[75%] px-4 py-2.5 rounded-3xl rounded-tr-lg bg-primary text-primary-foreground text-[15px] leading-[1.7] whitespace-pre-wrap shadow-sm">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col animate-fade-in">
      <div className="max-w-full leading-[1.8] text-[15px] text-text">
        <div className="md-content">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

function EmptyWelcome({ onNewSession }: { onNewSession: () => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center py-20">
      <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mb-4">
        <MessageSquare className="w-8 h-8 text-primary" />
      </div>
      <h3 className="text-lg font-medium mb-2">问答演练场</h3>
      <p className="text-dim text-sm mb-6 max-w-sm">
        自由提问，深入学习技术知识。AI 导师会记住你之前的学习内容，帮你建立完整的知识体系。
      </p>
      <Button onClick={onNewSession} className="gap-1.5">
        <Plus className="w-4 h-4" /> 开始新对话
      </Button>
    </div>
  );
}

function EmptyChat({ onSend }: { onSend: (q: string) => void }) {
  return (
    <div className="max-w-3xl mx-auto flex flex-col items-center justify-center py-16">
      <div className="w-14 h-14 rounded-2xl bg-primary/10 flex items-center justify-center mb-4">
        <MessageSquare className="w-7 h-7 text-primary" />
      </div>
      <h3 className="text-base font-medium mb-1">有什么想问的？</h3>
      <p className="text-dim text-sm mb-6">试试下面的问题，或者直接输入你的疑问</p>
      <div className="grid gap-2 w-full max-w-md">
        {SUGGESTED_QUESTIONS.map((q, i) => (
          <button
            key={i}
            className="text-left px-4 py-3 rounded-xl border border-border hover:bg-secondary/50 text-sm transition-colors"
            onClick={() => onSend(q)}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
