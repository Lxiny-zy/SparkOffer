import { useState, useEffect, useCallback } from "react";
import { Menu, X, Sparkles, ChevronRight, ChevronDown, Activity, Clock, FileText, RefreshCw, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { getTopicIcon, ICON_OPTIONS } from "../utils/topicIcons";
import {
  getTopics, getCoreKnowledge, updateCoreKnowledge, createCoreKnowledge,
  deleteCoreKnowledge, getHighFreq, updateHighFreq, createTopic, deleteTopic, generateKnowledge,
  getKnowledgeStats, rebuildTopicIndex, rebuildAllIndices,
} from "../api/interview";
import type { KnowledgeStats } from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { TopicInfo } from "../types/api";

interface CoreFile {
  filename: string;
  content: string;
  mtime?: number;
}

function formatRelativeTime(ms: number | undefined): string {
  if (!ms) return "尚未更新";
  const diff = Date.now() - ms;
  if (diff < 0) return new Date(ms).toLocaleString("zh-CN", { hour12: false });
  if (diff < 60_000) return "刚刚";
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return new Date(ms).toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function formatAbsoluteTime(ms: number | undefined): string {
  if (!ms) return "—";
  return new Date(ms).toLocaleString("zh-CN", { hour12: false });
}

export default function Knowledge() {
  const [topics, setTopics] = useState<Record<string, TopicInfo>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<string>("core");
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(false);

  const [coreFiles, setCoreFiles] = useState<CoreFile[]>([]);
  const [expandedFile, setExpandedFile] = useState<string | null>(null);
  const [editContent, setEditContent] = useState<Record<string, string>>({});
  const [coreSaving, setCoreSaving] = useState<string | null>(null);

  const [highFreq, setHighFreq] = useState<string>("");
  const [highFreqDraft, setHighFreqDraft] = useState<string>("");
  const [highFreqMtime, setHighFreqMtime] = useState<number>(0);
  const [hfSaving, setHfSaving] = useState<boolean>(false);

  const [stats, setStats] = useState<KnowledgeStats | null>(null);

  const [newFileName, setNewFileName] = useState<string>("");
  const [showNewFile, setShowNewFile] = useState<boolean>(false);
  const [generating, setGenerating] = useState<boolean>(false);
  const [genProgress, setGenProgress] = useState<string>("");

  const [showAddTopic, setShowAddTopic] = useState<boolean>(false);
  const [newTopicName, setNewTopicName] = useState<string>("");
  const [newTopicIcon, setNewTopicIcon] = useState<string>("FileText");

  const [rebuilding, setRebuilding] = useState<boolean>(false);
  const [rebuildProgress, setRebuildProgress] = useState<string>("");
  const [showRebuildMenu, setShowRebuildMenu] = useState<boolean>(false);

  const refreshTopics = useCallback(async () => {
    const t = await getTopics();
    setTopics(t);
    return t;
  }, []);

  useEffect(() => {
    refreshTopics().then((t: Record<string, TopicInfo>) => {
      const keys = Object.keys(t);
      if (keys.length > 0) setSelected(keys[0]);
    });
  }, [refreshTopics]);

  const loadCore = useCallback(async (topic: string) => {
    try {
      const files: CoreFile[] = await getCoreKnowledge(topic);
      setCoreFiles(files);
      setExpandedFile(null);
      const buf: Record<string, string> = {};
      files.forEach((f) => { buf[f.filename] = f.content; });
      setEditContent(buf);
    } catch { setCoreFiles([]); }
  }, []);

  const loadHighFreq = useCallback(async (topic: string) => {
    try {
      const data = await getHighFreq(topic);
      setHighFreq(data.content || "");
      setHighFreqDraft(data.content || "");
      setHighFreqMtime(data.mtime || 0);
    } catch { setHighFreq(""); setHighFreqDraft(""); setHighFreqMtime(0); }
  }, []);

  const loadStats = useCallback(async (topic: string) => {
    try {
      const s = await getKnowledgeStats(topic);
      setStats(s);
    } catch { setStats(null); }
  }, []);

  useEffect(() => {
    if (!selected) return;
    loadCore(selected);
    loadHighFreq(selected);
    loadStats(selected);
  }, [selected, loadCore, loadHighFreq, loadStats]);

  const handleSaveCore = async (filename: string) => {
    setCoreSaving(filename);
    try {
      await updateCoreKnowledge(selected!, filename, editContent[filename] || "");
      const now = Date.now();
      setCoreFiles((prev) => prev.map((f) => f.filename === filename ? { ...f, content: editContent[filename], mtime: now } : f));
      loadStats(selected!);
      toast.success(`${filename} 已保存`);
    } catch (e: any) { toast.error("保存失败: " + e.message); }
    setTimeout(() => setCoreSaving(null), 1500);
  };

  const handleSaveHighFreq = async () => {
    setHfSaving(true);
    try {
      await updateHighFreq(selected!, highFreqDraft);
      setHighFreq(highFreqDraft);
      setHighFreqMtime(Date.now());
      loadStats(selected!);
      toast.success("高频题库已保存");
    } catch (e: any) { toast.error("保存失败: " + e.message); }
    setTimeout(() => setHfSaving(false), 1500);
  };

  const handleCreateFile = async () => {
    const name = newFileName.trim();
    if (!name) return;
    const fname = name.endsWith(".md") ? name : name + ".md";
    try {
      await createCoreKnowledge(selected!, fname, "");
      setNewFileName("");
      setShowNewFile(false);
      loadCore(selected!);
      toast.success(`已创建 ${fname}`);
    } catch (e: any) { toast.error("创建失败: " + e.message); }
  };

  const handleDeleteFile = async (filename: string) => {
    if (!confirm(`确定删除「${filename}」？此操作不可撤销。`)) return;
    try {
      await deleteCoreKnowledge(selected!, filename);
      setCoreFiles((prev) => prev.filter((f) => f.filename !== filename));
      if (expandedFile === filename) setExpandedFile(null);
      toast.success("文件已删除");
    } catch (e: any) { toast.error("删除失败: " + e.message); }
  };

  const handleGenerate = async () => {
    setGenerating(true);
    setGenProgress("");
    try {
      await generateKnowledge(selected!, { onProgress: (msg) => setGenProgress(msg) });
      await loadCore(selected!);
      setExpandedFile("README.md");
      toast.success("AI 已生成基础内容");
    } catch (e: any) { toast.error("生成失败: " + e.message); }
    setGenerating(false);
  };

  const coreIsEmpty = coreFiles.length === 0 ||
    (coreFiles.length === 1 && coreFiles[0].filename === "README.md" && (coreFiles[0].content?.length || 0) <= 20);

  // ── Dirty tracking for unsaved-change protection ──
  const coreDirty = coreFiles.some((f) => (editContent[f.filename] ?? f.content) !== f.content);
  const hfDirty = highFreqDraft !== highFreq;
  const anyDirty = coreDirty || hfDirty;

  const handleSelectTopic = (key: string) => {
    if (key === selected) return;
    if (anyDirty && !confirm("当前领域有未保存的修改，切换会丢失。是否继续？")) return;
    setSelected(key);
    setSidebarOpen(false);
  };

  const handleAddTopic = async () => {
    const name = newTopicName.trim();
    if (!name) return;
    try {
      const result = await createTopic(name, newTopicIcon);
      setNewTopicName(""); setNewTopicIcon("FileText");
      setShowAddTopic(false);
      await refreshTopics();
      setSelected(result.key);
      toast.success(`已添加领域：${name}`);
    } catch (e: any) { toast.error("添加失败: " + e.message); }
  };

  const handleRebuildCurrent = async () => {
    if (!selected || rebuilding) return;
    setShowRebuildMenu(false);
    if (!confirm(`确定重新向量化「${topics[selected]?.name || selected}」？\n\n该操作会清空旧索引并重新对所有文件做 embedding，耗时取决于文件数量。`)) return;
    setRebuilding(true);
    setRebuildProgress("");
    try {
      const result = await rebuildTopicIndex(selected, {
        onProgress: (msg) => setRebuildProgress(msg),
      });
      if (result?.ok) {
        toast.success(`「${topics[selected]?.name}」索引重建完成（${result.file_count} 文件）`);
        loadStats(selected);
      } else {
        toast.error("重建失败：" + (rebuildProgress || "未知错误"));
      }
    } catch (e: any) {
      toast.error("重建失败：" + e.message);
    } finally {
      setRebuilding(false);
      setRebuildProgress("");
    }
  };

  const handleRebuildAll = async () => {
    setShowRebuildMenu(false);
    if (rebuilding) return;
    if (!confirm(`确定全量重建所有领域的向量索引？\n\n共 ${topicKeys.length} 个领域，将依次重建，可能需要 5-15 分钟。`)) return;
    setRebuilding(true);
    setRebuildProgress("");
    try {
      const result = await rebuildAllIndices({
        onProgress: (msg) => setRebuildProgress(msg),
        onTopicDone: ({ topic, index, total }) => {
          setRebuildProgress(`✓ ${topic} 完成 (${index}/${total})`);
        },
        onTopicError: ({ topic, message }) => {
          toast.error(`${topic} 失败: ${message}`);
        },
      });
      if (result) {
        const failed = result.failed || [];
        if (failed.length === 0) {
          toast.success(`全量重建完成：${result.succeeded}/${result.total}`);
        } else {
          toast.error(`部分失败：成功 ${result.succeeded}，失败 ${failed.length}`);
        }
        if (selected) loadStats(selected);
      }
    } catch (e: any) {
      toast.error("全量重建失败：" + e.message);
    } finally {
      setRebuilding(false);
      setRebuildProgress("");
    }
  };

  const handleDeleteTopic = async (key: string) => {
    if (!confirm(`确定删除「${topics[key]?.name || key}」？`)) return;
    try {
      await deleteTopic(key);
      const t = await refreshTopics();
      const keys = Object.keys(t);
      if (selected === key) setSelected(keys.length > 0 ? keys[0] : null);
      toast.success("领域已删除");
    } catch (e: any) { toast.error("删除失败: " + e.message); }
  };

  const topicKeys = Object.keys(topics);

  return (
    <div className="flex flex-1 overflow-hidden h-full">
      <Button
        variant="default"
        size="icon"
        className="fixed bottom-4 right-4 z-50 md:hidden rounded-full w-12 h-12"
        onClick={() => setSidebarOpen(!sidebarOpen)}
      >
        {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
      </Button>

      <div className={cn(
        "fixed inset-y-0 left-0 z-30 w-[200px] border-r border-border/50 bg-bg/80 backdrop-blur-sm p-4 flex flex-col transition-transform duration-200 md:static md:translate-x-0 md:shrink-0",
        sidebarOpen ? "translate-x-0" : "-translate-x-full"
      )}>
        <div className="flex justify-between items-center mb-3 px-2">
          <div className="text-[13px] font-semibold text-dim">专项领域</div>
          <Button variant="ghost" size="icon" className="w-6 h-6 text-base" title="新增领域" onClick={() => setShowAddTopic(true)}>+</Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {topicKeys.map((key) => {
            const isCurrent = selected === key;
            const showDirtyDot = isCurrent && anyDirty;
            return (
              <div key={key} className="relative mb-0.5 group">
                <button
                  className={cn(
                    "w-full px-3 py-2.5 rounded-lg text-sm text-left flex items-center gap-2 transition-all cursor-pointer",
                    isCurrent ? "bg-secondary text-text shadow-[inset_3px_0_0_var(--primary)]" : "text-dim hover:bg-secondary/60 hover:translate-x-0.5"
                  )}
                  onClick={() => handleSelectTopic(key)}
                >
                  <span className="text-dim">{getTopicIcon(topics[key]?.icon, 16)}</span>
                  <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{topics[key]?.name || key}</span>
                  {showDirtyDot && (
                    <span
                      title="有未保存的修改"
                      className="w-1.5 h-1.5 rounded-full bg-orange shrink-0"
                      style={{ animation: "pulse-dot 1.4s infinite" }}
                    />
                  )}
                </button>
                <button
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-dim text-sm px-1.5 py-1 rounded opacity-0 group-hover:opacity-100 hover:text-red hover:bg-red/10 transition-all cursor-pointer"
                  title="删除领域"
                  onClick={() => handleDeleteTopic(key)}
                ><X size={14} /></button>
              </div>
            );
          })}
        </div>
      </div>

      {sidebarOpen && (
        <div className="fixed inset-0 z-20 bg-black/40 backdrop-blur-sm md:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      {showAddTopic && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in" onClick={() => setShowAddTopic(false)}>
          <Card className="w-[380px] max-w-[90vw] animate-bounce-in" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <CardContent className="p-6 md:p-8">
              <div className="text-lg font-semibold mb-5">新增训练领域</div>
              <div className="mb-3.5 space-y-1.5">
                <Label>名称</Label>
                <Input placeholder="Docker 容器化" value={newTopicName} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewTopicName(e.target.value)} autoFocus />
              </div>
              <div className="mb-3.5 space-y-1.5">
                <Label>图标</Label>
                <div className="grid grid-cols-8 gap-1.5">
                  {ICON_OPTIONS.map(({ name, Icon }: { name: string; Icon: any }) => (
                    <button
                      key={name}
                      type="button"
                      className={cn(
                        "w-9 h-9 rounded-lg flex items-center justify-center transition-all cursor-pointer border",
                        newTopicIcon === name ? "bg-primary/20 text-primary border-primary" : "bg-secondary text-dim border-transparent hover:text-text"
                      )}
                      onClick={() => setNewTopicIcon(name)}
                      title={name}
                    >
                      <Icon size={16} />
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex gap-2.5 justify-end mt-6">
                <Button variant="outline" onClick={() => { setShowAddTopic(false); setNewTopicName(""); setNewTopicIcon("FileText"); }}>取消</Button>
                <Button variant="default" onClick={handleAddTopic} disabled={!newTopicName.trim()}>添加</Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex border-b border-border px-4 md:px-6 bg-card items-center">
          {["core", "high_freq"].map((t) => (
            <button
              key={t}
              className={cn(
                "px-4 py-3 md:px-5 text-sm border-b-2 transition-all cursor-pointer",
                tab === t ? "text-text border-b-primary font-medium" : "text-dim border-b-transparent hover:text-text"
              )}
              onClick={() => setTab(t)}
            >
              {t === "core" ? "核心知识库" : "高频题库"}
            </button>
          ))}
          <div className="ml-auto pr-1 relative">
            {rebuilding ? (
              <div className="flex items-center gap-2 text-xs text-dim px-3 py-1.5 rounded-lg bg-primary/8 border border-primary/20">
                <Loader2 size={14} className="animate-spin text-primary" />
                <span className="hidden sm:inline">{rebuildProgress || "重建中..."}</span>
                <span className="sm:hidden">重建中</span>
              </div>
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                onClick={() => setShowRebuildMenu((v) => !v)}
                disabled={topicKeys.length === 0}
                title="重新向量化知识库"
              >
                <RefreshCw size={14} />
                <span className="hidden sm:inline">初始化向量库</span>
                <ChevronDown size={12} />
              </Button>
            )}
            {showRebuildMenu && !rebuilding && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setShowRebuildMenu(false)} />
                <div className="absolute right-0 top-full mt-1 z-40 w-56 bg-card border border-border rounded-xl shadow-lg overflow-hidden animate-fade-in">
                  <button
                    className="w-full px-4 py-2.5 text-sm text-left hover:bg-secondary/60 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                    onClick={handleRebuildCurrent}
                    disabled={!selected}
                  >
                    <RefreshCw size={14} className="text-primary" />
                    <div className="flex-1">
                      <div>重建当前模块</div>
                      <div className="text-[11px] text-dim">{selected ? topics[selected]?.name : "未选择"}</div>
                    </div>
                  </button>
                  <div className="h-px bg-border" />
                  <button
                    className="w-full px-4 py-2.5 text-sm text-left hover:bg-secondary/60 flex items-center gap-2"
                    onClick={handleRebuildAll}
                  >
                    <Sparkles size={14} className="text-primary" />
                    <div className="flex-1">
                      <div>全量重建（所有模块）</div>
                      <div className="text-[11px] text-dim">{topicKeys.length} 个领域，依次重建</div>
                    </div>
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 md:p-6">
          {!selected ? (
            <div className="text-center py-15 text-dim text-sm">选择一个领域</div>
          ) : (
            <>
              {/* ── 知识进化状态条 ── 用来验证自我进化与答题沉淀是否生效 ── */}
              {stats && (
                <Card className="mb-4 border-primary/20 bg-gradient-to-br from-primary/5 via-card to-card">
                  <CardContent className="p-3.5 md:p-4">
                    <div className="flex items-start gap-3 flex-wrap">
                      <div className="w-9 h-9 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">
                        <Activity size={16} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1.5">
                          <span className="text-[13px] font-semibold text-text">知识进化状态</span>
                          {stats.evolution_count > 0 ? (
                            <Badge variant="outline" className="text-[10px] bg-green/10 border-green/30 text-green">
                              已沉淀 {stats.evolution_count} 次
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="text-[10px] bg-dim/10 border-dim/30 text-dim">
                              暂无自动沉淀
                            </Badge>
                          )}
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-[12px] text-dim">
                          <div className="flex items-center gap-1.5" title={formatAbsoluteTime(stats.last_evolved_at)}>
                            <Sparkles size={12} className="text-primary/70" />
                            <span>最近自我进化：</span>
                            <span className={cn("font-mono", stats.last_evolved_at ? "text-text" : "text-dim/60")}>
                              {stats.last_evolved_at ? formatRelativeTime(stats.last_evolved_at) : "—"}
                            </span>
                            {stats.last_evolved_file && (
                              <span className="text-dim/70 truncate">· {stats.last_evolved_file}</span>
                            )}
                          </div>
                          <div className="flex items-center gap-1.5" title={formatAbsoluteTime(stats.last_any_update_at)}>
                            <Clock size={12} className="text-tertiary/70" />
                            <span>任意更新：</span>
                            <span className={cn("font-mono", stats.last_any_update_at ? "text-text" : "text-dim/60")}>
                              {stats.last_any_update_at ? formatRelativeTime(stats.last_any_update_at) : "—"}
                            </span>
                          </div>
                          <div className="flex items-center gap-1.5" title={formatAbsoluteTime(stats.last_high_freq_at)}>
                            <FileText size={12} className="text-warning/70" />
                            <span>高频题库更新：</span>
                            <span className={cn("font-mono", stats.last_high_freq_at ? "text-text" : "text-dim/60")}>
                              {stats.last_high_freq_at ? formatRelativeTime(stats.last_high_freq_at) : "—"}
                            </span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            <FileText size={12} className="text-info/70" />
                            <span>文件总数：</span>
                            <span className="font-mono text-text">{stats.file_count}</span>
                          </div>
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-[11px] text-dim shrink-0"
                        onClick={() => selected && loadStats(selected)}
                        title="刷新统计"
                      >
                        刷新
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              )}
              {tab === "core" ? (
                <div>
              <div className="text-[13px] text-dim mb-3">
                AI 出题和评分的参考依据，编辑后影响该领域的题目质量。支持 Markdown 格式。
              </div>
              <div className="flex gap-2 mb-4">
                {showNewFile ? (
                  <div className="flex gap-2 flex-1">
                    <Input className="flex-1" placeholder="文件名 (例: 装饰器.md)" value={newFileName} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewFileName(e.target.value)} onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => e.key === "Enter" && handleCreateFile()} />
                    <Button variant="default" size="sm" onClick={handleCreateFile}>创建</Button>
                    <Button variant="outline" size="sm" onClick={() => { setShowNewFile(false); setNewFileName(""); }}>取消</Button>
                  </div>
                ) : (
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => setShowNewFile(true)}>+ 新增文件</Button>
                    {coreIsEmpty && (
                      <Button variant="outline" size="sm" className="border-primary/40 text-primary" onClick={handleGenerate} disabled={generating}>
                        {generating ? (genProgress || "正在生成...") : <><Sparkles size={14} /> AI 生成基础内容</>}
                      </Button>
                    )}
                  </div>
                )}
              </div>

              {coreFiles.length === 0 ? (
                <div className="text-center py-15 text-dim text-sm">该领域暂无知识文件</div>
              ) : (
                <div className="flex flex-col gap-3 stagger-children">
                  {coreFiles.map((f) => {
                    const fileDirty = (editContent[f.filename] ?? f.content) !== f.content;
                    const isAutoDeposit = f.filename === "自动沉淀.md";
                    return (
                    <Card key={f.filename} className={cn("overflow-hidden transition-all duration-300", fileDirty && "ring-1 ring-orange/30", isAutoDeposit && "border-primary/30")}>
                      <div
                        className="flex justify-between items-center px-4 py-3 cursor-pointer text-sm font-medium hover:bg-secondary/50 transition-colors"
                        onClick={() => setExpandedFile(expandedFile === f.filename ? null : f.filename)}
                      >
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <span className="truncate">{f.filename}</span>
                          {isAutoDeposit && (
                            <Badge variant="outline" className="text-[10px] bg-primary/10 border-primary/30 text-primary shrink-0">
                              <Sparkles size={10} className="mr-0.5" /> 自动沉淀
                            </Badge>
                          )}
                          {fileDirty && (
                            <Badge variant="outline" className="text-[10px] bg-orange/10 border-orange/30 text-orange shrink-0">
                              未保存
                            </Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          {f.mtime ? (
                            <span className="text-[11px] text-dim/70 hidden sm:inline" title={formatAbsoluteTime(f.mtime)}>
                              {formatRelativeTime(f.mtime)}
                            </span>
                          ) : null}
                          <span className="text-xs text-dim flex items-center gap-1">{expandedFile === f.filename ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {(f.content?.length || 0)} 字</span>
                          <button
                            className="text-dim cursor-pointer p-1 rounded opacity-50 hover:text-red hover:opacity-100 transition-all"
                            title="删除文件"
                            onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleDeleteFile(f.filename); }}
                          ><X size={14} /></button>
                        </div>
                      </div>
                      {expandedFile === f.filename && (
                        <div className="border-t border-border p-4 animate-fade-in">
                          <textarea
                            className="w-full min-h-[300px] p-3 rounded-lg border border-border bg-bg text-text text-[13px] font-mono leading-relaxed resize-y focus:outline-none focus:border-primary transition-colors"
                            value={editContent[f.filename] ?? f.content}
                            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setEditContent((prev) => ({ ...prev, [f.filename]: e.target.value }))}
                          />
                          <div className="flex gap-2 mt-3 justify-end items-center">
                            {coreSaving === f.filename && <span className="text-xs text-green self-center mr-3 animate-fade-in">✓ 已保存</span>}
                            {fileDirty && coreSaving !== f.filename && (
                              <Button variant="ghost" size="sm" onClick={() => setEditContent((prev) => ({ ...prev, [f.filename]: f.content }))}>
                                撤销修改
                              </Button>
                            )}
                            <Button
                              variant={fileDirty ? "default" : "outline"}
                              size="sm"
                              onClick={() => handleSaveCore(f.filename)}
                              disabled={!fileDirty}
                              className={fileDirty ? "shadow-[0_0_12px_var(--glow-primary)]" : ""}
                            >
                              保存
                            </Button>
                          </div>
                        </div>
                      )}
                    </Card>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
            <div>
              <div className="text-[13px] text-dim mb-3 flex items-center gap-2 flex-wrap">
                <span>标记的高频面试考点，出题时会优先覆盖。支持 Markdown 格式。</span>
                {highFreqMtime > 0 && (
                  <span className="text-[11px] text-dim/70" title={formatAbsoluteTime(highFreqMtime)}>
                    · 最后更新 {formatRelativeTime(highFreqMtime)}
                  </span>
                )}
              </div>
              <textarea
                className="w-full min-h-[300px] md:min-h-[500px] p-3 rounded-xl border border-border bg-bg text-text text-[13px] font-mono leading-relaxed resize-y focus:outline-none focus:border-primary"
                value={highFreqDraft}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setHighFreqDraft(e.target.value)}
                placeholder={"# 高频题\n\n## 1. xxx原理是什么？为什么这样设计？\n\n## 2. 实际项目中遇到xxx问题怎么解决？"}
              />
              <div className="flex gap-2 mt-3 justify-end">
                {hfSaving && <span className="text-xs text-green self-center mr-3">已保存</span>}
                {highFreqDraft !== highFreq && (
                  <Button variant="outline" size="sm" onClick={() => setHighFreqDraft(highFreq)}>撤销修改</Button>
                )}
                <Button variant="default" size="sm" onClick={handleSaveHighFreq} disabled={highFreqDraft === highFreq}>保存</Button>
              </div>
            </div>
          )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
