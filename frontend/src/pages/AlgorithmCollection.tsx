import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { markdownComponents } from "../components/ChatBubble";
import {
  Library, Trash2, Download, Tag, ChevronDown, ChevronUp,
  Filter, X, Edit3, Check, Code2, ExternalLink, Search,
} from "lucide-react";
import {
  getAlgorithmCards, deleteAlgorithmCard, updateAlgorithmCard,
  getAlgorithmTags, exportAlgorithmCards,
} from "../api/interview";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { AlgorithmCard } from "../types/api";

const DIFFICULTY_MAP: Record<string, { label: string; color: string }> = {
  easy: { label: "Easy", color: "var(--green)" },
  medium: { label: "Medium", color: "var(--orange)" },
  hard: { label: "Hard", color: "var(--red)" },
};

function DifficultyBadge({ difficulty }: { difficulty: string }) {
  const d = DIFFICULTY_MAP[difficulty];
  if (!d) return null;
  return (
    <span
      className="inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full"
      style={{ color: d.color, background: `color-mix(in srgb, ${d.color} 12%, transparent)` }}
    >
      {d.label}
    </span>
  );
}

export default function AlgorithmCollection() {
  const navigate = useNavigate();
  const [items, setItems] = useState<AlgorithmCard[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [difficultyFilter, setDifficultyFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [allTags, setAllTags] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editingTags, setEditingTags] = useState<string | null>(null);
  const [tagInput, setTagInput] = useState("");

  const loadData = async () => {
    setLoading(true);
    try {
      const [cardData, tags] = await Promise.all([
        getAlgorithmCards({
          difficulty: difficultyFilter || undefined,
          tag: tagFilter || undefined,
          search: searchQuery || undefined,
          sort_by: sortBy,
        }),
        getAlgorithmTags(),
      ]);
      setItems(cardData.items);
      setTotal(cardData.total);
      setAllTags(tags);
    } catch (e) {
      console.error("加载算法收藏失败:", e);
    }
    setLoading(false);
  };

  useEffect(() => { loadData(); }, [difficultyFilter, tagFilter, sortBy, searchQuery]);

  const handleDelete = async (id: string) => {
    try {
      await deleteAlgorithmCard(id);
      setItems((prev) => prev.filter((it) => it.id !== id));
      setTotal((prev) => prev - 1);
      setSelected((prev) => { const s = new Set(prev); s.delete(id); return s; });
    } catch (e) {
      console.error("删除失败:", e);
    }
  };

  const handleBulkDelete = async () => {
    for (const id of selected) {
      await deleteAlgorithmCard(id);
    }
    setItems((prev) => prev.filter((it) => !selected.has(it.id)));
    setTotal((prev) => prev - selected.size);
    setSelected(new Set());
  };

  const handleExport = async (format: string, ids?: string[] | null) => {
    try {
      const res = await exportAlgorithmCards(format, ids, difficultyFilter || undefined);
      const blob = await res.blob();
      const ext = format === "json" ? "json" : "md";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `sparkoffer-algorithm.${ext}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error("导出失败:", e);
    }
  };

  const handleSaveTags = async (id: string) => {
    const tags = tagInput.split(/[,，\s]+/).filter(Boolean);
    try {
      await updateAlgorithmCard(id, { tags });
      setItems((prev) => prev.map((it) => it.id === id ? { ...it, tags } : it));
      setEditingTags(null);
      setTagInput("");
      loadData();
    } catch (e) {
      console.error("更新标签失败:", e);
    }
  };

  const handleReopen = (item: AlgorithmCard) => {
    navigate("/algorithm", {
      state: {
        problemText: item.problem_text,
        sourceUrl: item.source_url,
        language: item.language,
      },
    });
  };

  const toggleExpand = (id: string) => setExpanded((p) => ({ ...p, [id]: !p[id] }));
  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const s = new Set(prev);
      s.has(id) ? s.delete(id) : s.add(id);
      return s;
    });
  };

  return (
    <div className="flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-4xl mx-auto w-full">
      {/* Header */}
      <div className="mb-8 animate-fade-in relative">
        <div className="absolute -top-6 -left-6 w-[180px] h-[120px] rounded-full pointer-events-none opacity-20" style={{ background: "radial-gradient(ellipse, var(--glow-accent), transparent 70%)" }} />
        <div className="relative">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Library size={20} className="text-primary" />
              <h1 className="text-2xl md:text-[28px] font-display font-bold aurora-text">算法收藏</h1>
              <Badge variant="secondary" className="ml-2">{total} 题</Badge>
            </div>
            <Button variant="outline" size="sm" onClick={() => navigate("/algorithm")}>
              <Code2 size={14} /> 去解题
            </Button>
          </div>
          <p className="text-sm text-dim">保存的算法题解答，方便复习回顾</p>
        </div>
      </div>

      {/* Filters & Actions */}
      <div className="flex flex-wrap items-center gap-2 mb-6">
        <div className="flex items-center gap-1.5 text-sm text-dim">
          <Filter size={14} />
        </div>
        <select
          className="bg-card border border-border rounded-lg px-3 py-1.5 text-sm"
          value={difficultyFilter}
          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setDifficultyFilter(e.target.value)}
        >
          <option value="">全部难度</option>
          <option value="easy">Easy</option>
          <option value="medium">Medium</option>
          <option value="hard">Hard</option>
        </select>
        <select
          className="bg-card border border-border rounded-lg px-3 py-1.5 text-sm"
          value={tagFilter}
          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setTagFilter(e.target.value)}
        >
          <option value="">全部标签</option>
          {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select
          className="bg-card border border-border rounded-lg px-3 py-1.5 text-sm"
          value={sortBy}
          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setSortBy(e.target.value)}
        >
          <option value="created_at">按时间</option>
          <option value="difficulty">按难度</option>
          <option value="title">按标题</option>
        </select>
        <div className="relative flex-1 min-w-[160px]">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dim" />
          <Input
            placeholder="搜索题目..."
            className="pl-8 h-8 text-sm"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {selected.size > 0 && (
          <>
            <Button variant="destructive" size="sm" onClick={handleBulkDelete}>
              <Trash2 size={13} /> 删除选中 ({selected.size})
            </Button>
            <Button variant="outline" size="sm" onClick={() => handleExport("json", [...selected])}>
              <Download size={13} /> 导出选中
            </Button>
          </>
        )}
        <Button variant="outline" size="sm" onClick={() => handleExport("json")}>
          <Download size={13} /> JSON
        </Button>
        <Button variant="outline" size="sm" onClick={() => handleExport("markdown")}>
          <Download size={13} /> Markdown
        </Button>
      </div>

      {/* Cards */}
      {loading ? (
        <div className="flex flex-col gap-4">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-20 text-dim">
          <Library size={40} className="mx-auto mb-3 opacity-30" />
          <div className="text-lg font-medium mb-1">还没有收藏的算法题</div>
          <div className="text-sm mb-4">在算法解题页面完成解答后点击「保存到收藏」</div>
          <Button variant="outline" onClick={() => navigate("/algorithm")}>
            <Code2 size={14} /> 去解题
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-4 stagger-children">
          {items.map((item) => (
            <Card key={item.id} className={`animate-fade-in transition-all ${selected.has(item.id) ? "ring-2 ring-primary" : ""}`}>
              <CardContent className="p-4 md:p-5">
                {/* Header */}
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-1.5 accent-primary"
                    checked={selected.has(item.id)}
                    onChange={() => toggleSelect(item.id)}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      <DifficultyBadge difficulty={item.difficulty} />
                      <Badge variant="secondary" className="text-xs">{item.language || "python"}</Badge>
                      {item.source_url && (
                        <a href={item.source_url} target="_blank" rel="noopener noreferrer"
                           className="text-dim hover:text-primary transition-colors">
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </div>
                    <div
                      className="text-[15px] font-medium leading-relaxed cursor-pointer hover:text-primary transition-colors"
                      onClick={() => toggleExpand(item.id)}
                    >
                      {item.title}
                    </div>
                    {/* Tags */}
                    <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                      {editingTags === item.id ? (
                        <div className="flex items-center gap-2 w-full">
                          <Input
                            className="h-7 text-xs flex-1"
                            placeholder="输入标签，逗号分隔"
                            value={tagInput}
                            onChange={(e) => setTagInput(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && handleSaveTags(item.id)}
                          />
                          <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => handleSaveTags(item.id)}>
                            <Check size={12} />
                          </Button>
                          <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => { setEditingTags(null); setTagInput(""); }}>
                            <X size={12} />
                          </Button>
                        </div>
                      ) : (
                        <>
                          {item.tags.map((t) => (
                            <Badge key={t} variant="outline" className="text-xs">{t}</Badge>
                          ))}
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 px-1.5 text-dim"
                            onClick={() => { setEditingTags(item.id); setTagInput(item.tags.join(", ")); }}
                          >
                            <Tag size={11} />
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button variant="ghost" size="sm" className="h-7 px-2 text-dim" onClick={() => toggleExpand(item.id)}>
                      {expanded[item.id] ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 px-2 text-dim hover:text-red" onClick={() => handleDelete(item.id)}>
                      <Trash2 size={13} />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 px-2 text-dim" onClick={() => handleExport("markdown", [item.id])}>
                      <Download size={13} />
                    </Button>
                  </div>
                </div>

                {/* Expanded Content */}
                {expanded[item.id] && (
                  <div className="mt-4 pt-3 border-t border-border space-y-3 animate-fade-in">
                    {item.problem_text && (
                      <div>
                        <div className="text-xs font-semibold text-dim mb-1">题目描述</div>
                        <div className="bg-secondary rounded-lg px-3 py-2.5 text-sm leading-relaxed whitespace-pre-wrap">
                          {item.problem_text}
                        </div>
                      </div>
                    )}
                    {item.solution && (
                      <div>
                        <div className="text-xs font-semibold text-dim mb-1">解答</div>
                        <div className="md-content bg-secondary rounded-lg px-3.5 py-3 text-sm leading-[1.8]">
                          <ReactMarkdown components={markdownComponents}>{item.solution}</ReactMarkdown>
                        </div>
                      </div>
                    )}
                    {item.conversation_history && item.conversation_history.length > 0 && (
                      <div>
                        <div className="text-xs font-semibold text-dim mb-1">
                          对话记录 ({item.conversation_history.length} 条)
                        </div>
                        <div className="bg-secondary rounded-lg px-3 py-2.5 space-y-2 max-h-[300px] overflow-y-auto">
                          {item.conversation_history.map((msg, i) => (
                            <div key={i} className="text-sm">
                              <span className={`font-medium ${msg.role === "user" ? "text-primary" : "text-text"}`}>
                                {msg.role === "user" ? "你: " : "AI: "}
                              </span>
                              <span className="text-text leading-relaxed">{msg.content.slice(0, 200)}{msg.content.length > 200 ? "..." : ""}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {item.note && (
                      <div>
                        <div className="text-xs font-semibold text-dim mb-1">笔记</div>
                        <div className="text-sm leading-relaxed text-text">{item.note}</div>
                      </div>
                    )}
                    <div className="flex items-center justify-between">
                      <div className="text-xs text-dim">
                        保存于 {new Date(item.created_at).toLocaleString("zh-CN")}
                      </div>
                      <Button variant="outline" size="sm" onClick={() => handleReopen(item)}>
                        <Code2 size={12} /> 重新打开解题
                      </Button>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
