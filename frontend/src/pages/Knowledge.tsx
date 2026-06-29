import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Menu, X, Sparkles, ChevronRight, ChevronDown, Activity, Clock, FileText, RefreshCw, Loader2, CheckCircle2, XCircle, CircleDashed, Upload, Layers } from "lucide-react";
import { toast } from "sonner";
import { getTopicIcon, ICON_OPTIONS } from "../utils/topicIcons";
import {
  getTopics, getCoreKnowledge, updateCoreKnowledge, createCoreKnowledge,
  deleteCoreKnowledge, getHighFreq, updateHighFreq, createTopic, deleteTopic, generateKnowledge,
  getKnowledgeStats, rebuildTopicIndex, rebuildAllIndices, getRebuildStatus,
  uploadCoreKnowledgeFiles, getChunkCounts,
  type RebuildTaskStatus,
} from "../api/interview";
import type { KnowledgeStats, ChunkCounts } from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import useDraftPersist, { readDraft } from "../hooks/useDraftPersist";
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
  const [chunkCounts, setChunkCounts] = useState<ChunkCounts | null>(null);

  const [newFileName, setNewFileName] = useState<string>("");
  const [showNewFile, setShowNewFile] = useState<boolean>(false);
  const [generating, setGenerating] = useState<boolean>(false);
  const [genProgress, setGenProgress] = useState<string>("");

  const [uploading, setUploading] = useState<boolean>(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [showAddTopic, setShowAddTopic] = useState<boolean>(false);
  const [newTopicName, setNewTopicName] = useState<string>("");
  const [newTopicIcon, setNewTopicIcon] = useState<string>("FileText");

  const [showRebuildMenu, setShowRebuildMenu] = useState<boolean>(false);
  const [rebuildTasks, setRebuildTasks] = useState<RebuildTaskStatus[]>([]);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const pollRef = useRef<number | null>(null);
  const lastNotifiedRef = useRef<Record<string, string>>({});

  const refreshTopics = useCallback(async () => {
    const t = await getTopics();
    setTopics(t);
    return t;
  }, []);

  const loadChunkCounts = useCallback(async () => {
    try { setChunkCounts(await getChunkCounts()); } catch { /* best-effort: leave prior counts */ }
  }, []);

  useEffect(() => {
    refreshTopics().then((t: Record<string, TopicInfo>) => {
      const keys = Object.keys(t);
      if (keys.length > 0) setSelected(keys[0]);
    });
    loadChunkCounts();
  }, [refreshTopics, loadChunkCounts]);

  const loadCore = useCallback(async (topic: string) => {
    try {
      const files: CoreFile[] = await getCoreKnowledge(topic);
      setCoreFiles(files);
      setExpandedFile(null);
      const buf: Record<string, string> = {};
      files.forEach((f) => { buf[f.filename] = f.content; });
      // Reconcile with an unsaved draft (survives refresh / accidental close).
      const draft = readDraft<Record<string, string>>(`knowledge:core:${topic}`);
      if (draft) {
        Object.entries(draft).forEach(([name, content]) => {
          if (name in buf && content !== buf[name]) buf[name] = content;
        });
      }
      setEditContent(buf);
    } catch { setCoreFiles([]); }
  }, []);

  const loadHighFreq = useCallback(async (topic: string) => {
    try {
      const data = await getHighFreq(topic);
      setHighFreq(data.content || "");
      const draft = readDraft<string>(`knowledge:hf:${topic}`);
      setHighFreqDraft(draft ?? (data.content || ""));
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

  const dirtyCoreDraft = useMemo(() => {
    const dirty: Record<string, string> = {};
    coreFiles.forEach((f) => {
      const current = editContent[f.filename] ?? f.content;
      if (current !== f.content) dirty[f.filename] = current;
    });
    return dirty;
  }, [coreFiles, editContent]);

  const hfDirty = highFreqDraft !== highFreq;

  // Draft persistence — only unsaved core-file edits are stored.
  useDraftPersist({
    key: selected ? `knowledge:core:${selected}` : null,
    value: dirtyCoreDraft,
    isEmpty: (obj) => Object.keys(obj).length === 0,
  });

  // Draft persistence — high-freq knowledge.
  const { clear: clearHfDraft } = useDraftPersist({
    key: selected ? `knowledge:hf:${selected}` : null,
    value: highFreqDraft,
    isEmpty: () => !hfDirty,
  });

  const handleSaveCore = async (filename: string) => {
    setCoreSaving(filename);
    try {
      await updateCoreKnowledge(selected!, filename, editContent[filename] || "");
      const now = Date.now();
      const savedContent = editContent[filename] || "";
      setCoreFiles((prev) => prev.map((f) => f.filename === filename ? { ...f, content: savedContent, mtime: now } : f));
      setEditContent((prev) => ({ ...prev, [filename]: savedContent }));
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
      clearHfDraft(); // Persisted to server — clear draft
      toast.success("高频题库已保存");
    } catch (e: any) { toast.error("保存失败: " + e.message); }
    setTimeout(() => setHfSaving(false), 1500);
  };

  const handleCreateFile = async () => {
    const name = newFileName.trim();
    if (!name) return;
    const ALLOWED_EXTS = [".md", ".txt", ".py"];
    const hasValidExt = ALLOWED_EXTS.some((ext) => name.endsWith(ext));
    const fname = hasValidExt ? name : name + ".md";
    try {
      await createCoreKnowledge(selected!, fname, "");
      setNewFileName("");
      setShowNewFile(false);
      loadCore(selected!);
      toast.success(`已创建 ${fname}`);
    } catch (e: any) { toast.error("创建失败: " + e.message); }
  };

  const handleUploadFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const all = Array.from(fileList);
    const mdFiles = all.filter((f) => {
      const isMd = f.name.toLowerCase().endsWith(".md");
      if (!isMd) toast.error(`${f.name} 不是 .md 文件，已跳过`);
      return isMd;
    });
    const validFiles = mdFiles.filter((f) => {
      const tooLarge = f.size > 200 * 1024 * 1024;
      if (tooLarge) toast.error(`${f.name} 超过 200MB，已跳过`);
      return !tooLarge;
    });
    if (validFiles.length === 0) {
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    setUploading(true);
    try {
      const result = await uploadCoreKnowledgeFiles(selected!, validFiles);
      await loadCore(selected!);
      loadStats(selected!);
      toast.success(`已上传 ${result.saved.length} 个文件`);
      const extras: string[] = [];
      if (result.skipped?.length) extras.push(`${result.skipped.length} 个已跳过`);
      if (result.rejected?.length) extras.push(`${result.rejected.length} 个被拒绝`);
      if (extras.length) toast.error(extras.join("，"));
    } catch (e: any) {
      toast.error("上传失败: " + e.message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
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
  const coreDirty = Object.keys(dirtyCoreDraft).length > 0;
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

  // ── Rebuild status polling ──────────────────────────────────────────────
  // Workers run rebuilds in the backend queue; we just poll for state.
  // Polling stops when no task is still pending/running, so idle pages don't hit the API.

  const refreshStatus = useCallback(async () => {
    try {
      const { tasks } = await getRebuildStatus();
      setRebuildTasks(tasks);

      // Toast on state transitions we haven't notified about yet.
      // Initial mid-run page loads seed the ref without firing toasts.
      const liveIds = new Set<string>();
      for (const t of tasks) {
        liveIds.add(t.task_id);
        const prev = lastNotifiedRef.current[t.task_id];
        if (prev === undefined) {
          lastNotifiedRef.current[t.task_id] = t.state;
          continue;
        }
        if (prev !== t.state && (t.state === "completed" || t.state === "failed")) {
          lastNotifiedRef.current[t.task_id] = t.state;
          if (t.state === "completed") {
            toast.success(`${t.label} 完成（${t.file_count} 文件）`);
            if (selected && t.topic === selected) loadStats(selected);
            loadChunkCounts();
          } else {
            toast.error(`${t.label} 失败：${t.error || "未知错误"}`);
          }
        }
      }

      // Drop notification entries for tasks the server no longer reports — keeps
      // the ref from growing unbounded across many rebuild cycles.
      for (const k of Object.keys(lastNotifiedRef.current)) {
        if (!liveIds.has(k)) delete lastNotifiedRef.current[k];
      }

      // Stop polling if everything finished. Checked here (not via state) so the
      // decision uses the freshly-fetched tasks, not a stale render.
      const stillActive = tasks.some(t => t.state === "pending" || t.state === "running");
      if (!stillActive && pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    } catch {
      // network glitch — keep the timer, next tick will retry
    }
  }, [selected, loadStats]);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    refreshStatus();  // immediate first poll
    pollRef.current = window.setInterval(refreshStatus, 3000);
  }, [refreshStatus]);

  // On mount: pull current status — if a rebuild was already in progress, the UI resumes
  // and starts polling. refreshStatus does the fetch; we kick off polling here if needed.
  useEffect(() => {
    (async () => {
      const { tasks } = await getRebuildStatus().catch(() => ({ tasks: [] as RebuildTaskStatus[] }));
      // Seed the notified ref so we don't fire a toast for tasks that completed
      // before this page was opened.
      for (const t of tasks) lastNotifiedRef.current[t.task_id] = t.state;
      setRebuildTasks(tasks);
      if (tasks.some(t => t.state === "pending" || t.state === "running")) startPolling();
    })();
    return () => {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasActiveTask = rebuildTasks.some(t => t.state === "pending" || t.state === "running");

  const handleRebuildCurrent = async () => {
    if (!selected || submitting || hasActiveTask) return;
    setShowRebuildMenu(false);
    if (!confirm(`确定重新向量化「${topics[selected]?.name || selected}」？\n\n该操作会在后台异步执行，你可以关闭页面或切到其他界面，回来时仍能看到进度。`)) return;
    setSubmitting(true);
    try {
      const result = await rebuildTopicIndex(selected);
      toast.success(result.message);
      startPolling();
    } catch (e: any) {
      toast.error("提交失败：" + e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleRebuildAll = async () => {
    if (submitting || hasActiveTask) return;
    setShowRebuildMenu(false);
    if (!confirm(`确定全量重建所有领域的向量索引？\n\n共 ${topicKeys.length} 个领域，后台异步并行执行（worker 数受限会自动排队）。`)) return;
    setSubmitting(true);
    try {
      const result = await rebuildAllIndices();
      toast.success(result.message);
      startPolling();
    } catch (e: any) {
      toast.error("提交失败：" + e.message);
    } finally {
      setSubmitting(false);
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
        "fixed inset-y-0 left-0 z-30 w-[200px] border-r border-border/50 p-4 flex flex-col transition-transform duration-200 md:static md:translate-x-0 md:shrink-0",
        sidebarOpen ? "translate-x-0" : "-translate-x-full"
      )} style={{ background: "var(--sig-bg)" }}>
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
                    "w-full px-3 py-2.5 rounded-md text-sm text-left flex items-center gap-2 transition-all cursor-pointer",
                    isCurrent ? "bg-secondary text-text" : "text-dim hover:bg-secondary/60 hover:translate-x-0.5"
                  )}
                  style={isCurrent ? { boxShadow: "inset 2px 0 0 var(--sig-accent)" } : undefined}
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
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-dim text-sm px-1.5 py-1 rounded opacity-0 group-hover:opacity-100 hover:text-[color:var(--sig-danger)] hover:bg-secondary transition-all cursor-pointer"
                  title="删除领域"
                  onClick={() => handleDeleteTopic(key)}
                ><X size={14} /></button>
              </div>
            );
          })}
        </div>
      </div>

      {sidebarOpen && (
        <div className="fixed inset-0 z-20 md:hidden" style={{ background: "var(--sig-overlay)" }} onClick={() => setSidebarOpen(false)} />
      )}

      {showAddTopic && (
        <div className="fixed inset-0 flex items-center justify-center z-50 animate-fade-in" style={{ background: "var(--sig-overlay)" }} onClick={() => setShowAddTopic(false)}>
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
          <div className="ml-auto pr-1 relative flex items-center gap-2">
            {hasActiveTask && (
              <div className="hidden md:flex items-center gap-2 text-xs text-dim px-3 py-1.5 rounded-lg bg-primary/8 border border-primary/20 max-w-[280px]">
                <Loader2 size={14} className="animate-spin text-primary shrink-0" />
                <span className="truncate">
                  {(() => {
                    const active = rebuildTasks.find(t => t.state === "running") || rebuildTasks.find(t => t.state === "pending");
                    if (!active) return "构建中...";
                    return `${active.label}：${active.message || active.state}`;
                  })()}
                </span>
              </div>
            )}
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={() => setShowRebuildMenu((v) => !v)}
              disabled={topicKeys.length === 0 || submitting || hasActiveTask}
              title={hasActiveTask ? "已有构建任务在跑" : "重新向量化知识库"}
            >
              {submitting ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              <span className="hidden sm:inline">{hasActiveTask ? "构建中..." : "初始化向量库"}</span>
              <ChevronDown size={12} />
            </Button>
            {showRebuildMenu && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setShowRebuildMenu(false)} />
                <div className="absolute right-0 top-full mt-1 z-40 w-64 bg-card border border-border rounded-xl shadow-lg overflow-hidden animate-fade-in">
                  <button
                    className="w-full px-4 py-2.5 text-sm text-left hover:bg-secondary/60 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                    onClick={handleRebuildCurrent}
                    disabled={!selected || hasActiveTask}
                  >
                    <RefreshCw size={14} className="text-primary" />
                    <div className="flex-1">
                      <div>重建当前模块</div>
                      <div className="text-[11px] text-dim">{selected ? topics[selected]?.name : "未选择"}</div>
                    </div>
                  </button>
                  <div className="h-px bg-border" />
                  <button
                    className="w-full px-4 py-2.5 text-sm text-left hover:bg-secondary/60 flex items-center gap-2 disabled:opacity-50"
                    onClick={handleRebuildAll}
                    disabled={hasActiveTask}
                  >
                    <Sparkles size={14} className="text-primary" />
                    <div className="flex-1">
                      <div>全量重建（所有模块）</div>
                      <div className="text-[11px] text-dim">{topicKeys.length} 个领域，提交后后台并行</div>
                    </div>
                  </button>
                  <div className="h-px bg-border" />
                  <div className="px-4 py-2 text-[11px] text-dim leading-relaxed bg-secondary/30">
                    提交后任务在后台运行，关闭页面也不会中断；重新打开仍能看到进度。
                  </div>
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
              {/* ── 向量索引重建任务列表 ── 异步队列状态实时反映 ── */}
              {rebuildTasks.length > 0 && (
                <Card className="mb-4 border-primary/20">
                  <CardContent className="p-3.5 md:p-4">
                    <div className="flex items-center justify-between mb-2.5">
                      <div className="flex items-center gap-2">
                        <RefreshCw size={14} className={cn("text-primary", hasActiveTask && "animate-spin")} />
                        <span className="text-[13px] font-semibold">向量索引重建任务</span>
                        {hasActiveTask && (
                          <Badge variant="outline" className="text-[10px] bg-primary/10 border-primary/30 text-primary">
                            {rebuildTasks.filter(t => t.state === "running").length} 运行 / {rebuildTasks.filter(t => t.state === "pending").length} 等待
                          </Badge>
                        )}
                      </div>
                      <Button variant="ghost" size="sm" className="h-7 text-[11px] text-dim" onClick={refreshStatus}>刷新</Button>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      {rebuildTasks.map((t) => {
                        const icon = t.state === "completed" ? <CheckCircle2 size={14} className="text-green" />
                                  : t.state === "failed"   ? <XCircle size={14} className="text-red" />
                                  : t.state === "running"  ? <Loader2 size={14} className="animate-spin text-primary" />
                                  : <CircleDashed size={14} className="text-dim" />;
                        const stateText = ({pending: "等待", running: "运行中", completed: "已完成", failed: "失败"} as const)[t.state];
                        const dur = t.finished_at && t.started_at
                          ? `${Math.max(1, Math.round(t.finished_at - t.started_at))}s`
                          : t.started_at ? `${Math.max(1, Math.round(Date.now()/1000 - t.started_at))}s+` : "";
                        const showBar = t.state === "running" && t.progress_total > 0;
                        const pct = showBar ? Math.min(100, Math.round((t.progress_done / t.progress_total) * 100)) : 0;
                        return (
                          <div key={t.task_id} className="flex flex-col gap-1 px-2 py-1.5 rounded-lg bg-secondary/30">
                            <div className="flex items-center gap-2 text-[12px]">
                              {icon}
                              <span className="font-mono text-dim shrink-0 w-12 truncate" title={t.topic}>{topics[t.topic]?.name ?? t.topic}</span>
                              <span className="flex-1 truncate text-text/80">{t.message || stateText}</span>
                              {t.retry_count > 0 && t.state !== "completed" && (
                                <span className="text-amber-500 text-[11px] shrink-0">重试 {t.retry_count}/2</span>
                              )}
                              {t.file_count > 0 && <span className="text-dim text-[11px] shrink-0">{t.file_count} 文件</span>}
                              {dur && <span className="text-dim text-[11px] shrink-0 font-mono">{dur}</span>}
                              {t.error && (
                                <span className="text-red text-[11px] shrink-0 truncate max-w-[120px]" title={t.error}>
                                  {t.error}
                                </span>
                              )}
                            </div>
                            {showBar && (
                              <div className="flex items-center gap-2 pl-[22px]">
                                <div className="flex-1 h-1.5 rounded-full bg-secondary overflow-hidden">
                                  <div className="h-full bg-primary rounded-full transition-all duration-500" style={{ width: `${pct}%` }} />
                                </div>
                                <span className="text-dim text-[11px] shrink-0 font-mono w-24 text-right">{t.progress_done}/{t.progress_total} ({pct}%)</span>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* ── 知识进化状态条 ── 用来验证自我进化与答题沉淀是否生效 ── */}
              {stats && (
                <Card className="mb-4" style={{ borderColor: "color-mix(in srgb, var(--sig-accent) 25%, transparent)", background: "color-mix(in srgb, var(--sig-accent) 4%, transparent)" }}>
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
                          <div className="flex items-center gap-1.5" title="知识库所有文件的总字数">
                            <FileText size={12} className="text-success/70" />
                            <span>总字数：</span>
                            <span className="font-mono text-text">
                              {stats.total_chars >= 10000
                                ? `${(stats.total_chars / 10000).toFixed(1)} 万字`
                                : `${stats.total_chars ?? 0} 字`}
                            </span>
                          </div>
                          <div className="flex items-center gap-1.5" title="该模块向量化后的 chunk 数；括号内为全库总数">
                            <Layers size={12} className="text-primary/70" />
                            <span>向量块：</span>
                            <span className="font-mono text-text">{stats.chunk_count ?? 0}</span>
                            {chunkCounts && (
                              <span className="text-dim/70">（全库 {chunkCounts.total}）</span>
                            )}
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
                AI 出题和评分的参考依据，编辑后影响该领域的题目质量。支持 .md / .txt / .py 格式。也可直接上传 .md 文件（单文件最大 200MB）。
              </div>
              <div className="flex gap-2 mb-4">
                {showNewFile ? (
                  <div className="flex gap-2 flex-1">
                    <Input className="flex-1" placeholder="文件名 (例: 装饰器.md, notes.txt)" value={newFileName} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewFileName(e.target.value)} onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => e.key === "Enter" && handleCreateFile()} />
                    <Button variant="default" size="sm" onClick={handleCreateFile}>创建</Button>
                    <Button variant="outline" size="sm" onClick={() => { setShowNewFile(false); setNewFileName(""); }}>取消</Button>
                  </div>
                ) : (
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => setShowNewFile(true)}>+ 新增文件</Button>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".md,text/markdown"
                      multiple
                      className="hidden"
                      onChange={(e) => handleUploadFiles(e.target.files)}
                    />
                    <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                      {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} 上传 .md
                    </Button>
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
