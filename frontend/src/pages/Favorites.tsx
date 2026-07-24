import { useState, useEffect, useCallback, useRef } from "react";
import { Markdown } from "../components/ChatBubble";
import {
  Star, Trash2, Download, Tag, ChevronDown, ChevronUp,
  Filter, X, Check,
} from "lucide-react";
import {
  getFavorites, deleteFavorite, updateFavorite,
  getFavoriteTags, exportFavorites,
} from "../api/interview";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { Favorite } from "../types/api";

function getScoreColor(score: number) {
  if (score >= 8) return { color: "var(--green)", bg: "var(--green)" };
  if (score >= 6) return { color: "var(--blue)", bg: "var(--blue)" };
  if (score >= 4) return { color: "var(--orange)", bg: "var(--orange)" };
  return { color: "var(--red)", bg: "var(--red)" };
}

function ScorePill({ score }: { score: number | null | undefined }) {
  if (score == null) return null;
  const { color } = getScoreColor(score);
  return (
    <span
      className="inline-flex items-center gap-0.5 text-sm font-bold px-2 py-0.5 rounded"
      style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}
    >
      {score}<span className="text-xs font-normal opacity-60">/10</span>
    </span>
  );
}

export default function Favorites() {
  const [items, setItems] = useState<Favorite[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [topicFilter, setTopicFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [allTags, setAllTags] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editingTags, setEditingTags] = useState<string | null>(null);
  const [tagInput, setTagInput] = useState("");
  const loadGenerationRef = useRef(0);

  const loadData = useCallback(async () => {
    const generation = ++loadGenerationRef.current;
    setLoading(true);
    try {
      const [favData, tags] = await Promise.all([
        getFavorites({ topic: topicFilter || undefined, tag: tagFilter || undefined, sort_by: sortBy }),
        getFavoriteTags(),
      ]);
      if (generation === loadGenerationRef.current) {
        setItems(favData.items);
        setTotal(favData.total);
        setAllTags(tags);
      }
    } catch (e) {
      console.error("加载收藏失败:", e);
    }
    if (generation === loadGenerationRef.current) setLoading(false);
  }, [sortBy, tagFilter, topicFilter]);

  useEffect(() => {
    // Loading filtered server data is the external synchronization performed by this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadData();
  }, [loadData]);

  const handleDelete = async (id: string) => {
    if (!confirm("确定删除这条收藏？")) return;
    try {
      await deleteFavorite(id);
      setItems((prev) => prev.filter((it) => it.id !== id));
      setTotal((prev) => prev - 1);
      setSelected((prev) => { const s = new Set(prev); s.delete(id); return s; });
    } catch (e) {
      console.error("删除失败:", e);
    }
  };

  const handleBulkDelete = async () => {
    if (!confirm(`确定删除选中的 ${selected.size} 条收藏？`)) return;
    // Delete each independently so one failure doesn't leave the UI showing rows
    // that were actually deleted; only prune the ones that succeeded.
    const results = await Promise.allSettled(
      [...selected].map((id) => deleteFavorite(id).then(() => id)),
    );
    const deleted = new Set(
      results.filter((r) => r.status === "fulfilled").map((r) => (r as PromiseFulfilledResult<string>).value),
    );
    if (deleted.size) {
      setItems((prev) => prev.filter((it) => !deleted.has(it.id)));
      setTotal((prev) => prev - deleted.size);
      setSelected((prev) => new Set([...prev].filter((id) => !deleted.has(id))));
    }
    if (deleted.size < selected.size) {
      alert(`有 ${selected.size - deleted.size} 条删除失败，请重试。`);
    }
  };

  const handleExport = async (format: string, ids?: string[] | null) => {
    try {
      const res = await exportFavorites(format, ids, topicFilter || undefined);
      const blob = await res.blob();
      const ext = format === "json" ? "json" : "md";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `sparkoffer-favorites.${ext}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error("导出失败:", e);
    }
  };

  const handleSaveTags = async (id: string) => {
    const tags = tagInput.split(/[,，\s]+/).filter(Boolean);
    try {
      await updateFavorite(id, { tags });
      setItems((prev) => prev.map((it) => it.id === id ? { ...it, tags } : it));
      setEditingTags(null);
      setTagInput("");
      void loadData();
    } catch (e) {
      console.error("更新标签失败:", e);
    }
  };

  const toggleExpand = (id: string) => setExpanded((p) => ({ ...p, [id]: !p[id] }));

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });
  };

  const topics = [...new Set(items.map((it) => it.topic).filter(Boolean))];

  return (
    <div className="sig-page flex-1 overflow-y-auto min-h-0 px-4 py-8 md:px-6 md:py-10 max-w-4xl mx-auto w-full">
      <div className="mb-8 animate-fade-in">
        <div className="sig-kicker mb-2">// 收藏夹 / FAVORITES</div>
        <div className="flex items-center gap-2 mb-1">
          <Star size={20} className="text-yellow-400 fill-yellow-400" />
          <h1 className="sig-display text-2xl md:text-[28px]">宝藏夹<span className="sig-accent-c">.</span></h1>
          <Badge variant="secondary" className="ml-2">{total} 题</Badge>
        </div>
        <p className="text-sm text-dim">收藏的面试题目，支持标签管理和导出</p>
      </div>

      {/* Filters & Actions */}
      <div className="flex flex-wrap items-center gap-2 mb-6">
        <div className="flex items-center gap-1.5 text-sm text-dim shrink-0">
          <Filter size={14} />
        </div>
        <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0">
          <select
            className="sig-field w-auto py-1.5 text-sm min-w-0"
            value={topicFilter}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setTopicFilter(e.target.value)}
          >
            <option value="">全部领域</option>
            {topics.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <select
            className="sig-field w-auto py-1.5 text-sm min-w-0"
            value={tagFilter}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setTagFilter(e.target.value)}
          >
            <option value="">全部标签</option>
            {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <select
            className="sig-field w-auto py-1.5 text-sm min-w-0"
            value={sortBy}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setSortBy(e.target.value)}
          >
            <option value="created_at">按时间</option>
            <option value="score">按分数</option>
            <option value="topic">按领域</option>
          </select>
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
          <Download size={13} /> MD
        </Button>
      </div>

      {/* Cards */}
      {loading ? (
        <div className="flex flex-col gap-4">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-20 text-dim">
          <Star size={40} className="mx-auto mb-3 opacity-30" />
          <div className="text-lg font-medium mb-1">还没有收藏的题目</div>
          <div className="text-sm">在训练复盘页面点击「收藏」按钮添加题目</div>
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
                      {item.topic && <Badge variant="secondary">{item.topic}</Badge>}
                      {item.difficulty && <Badge variant="outline">难度 {item.difficulty}</Badge>}
                      <ScorePill score={item.score} />
                    </div>
                    <div
                      className="text-[15px] font-medium leading-relaxed cursor-pointer hover:text-primary transition-colors"
                      onClick={() => toggleExpand(item.id)}
                    >
                      {item.question}
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
                    <Button variant="ghost" size="sm" className="h-7 px-2 text-dim hover:text-[color:var(--sig-danger)]" onClick={() => handleDelete(item.id)}>
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
                    {item.user_answer && (
                      <div>
                        <div className="text-xs font-semibold text-dim mb-1">我的回答</div>
                        <div className="bg-secondary rounded-lg px-3 py-2.5 text-sm leading-relaxed whitespace-pre-wrap">
                          {item.user_answer}
                        </div>
                      </div>
                    )}
                    {item.reference_answer && (
                      <div>
                        <div className="text-xs font-semibold text-dim mb-1">参考答案</div>
                        <div className="md-content bg-secondary rounded-lg px-3.5 py-3 text-sm leading-[1.8]">
                          <Markdown>{item.reference_answer}</Markdown>
                        </div>
                      </div>
                    )}
                    {item.assessment && (
                      <div>
                        <div className="text-xs font-semibold text-dim mb-1">点评</div>
                        <div className="text-sm leading-relaxed text-text">{item.assessment}</div>
                      </div>
                    )}
                    <div className="text-xs text-dim">
                      收藏于 {new Date(item.created_at).toLocaleString("zh-CN")}
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
