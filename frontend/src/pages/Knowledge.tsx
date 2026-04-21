import { useState, useEffect, useCallback } from "react";
import { Menu, X, Sparkles, ChevronRight, ChevronDown } from "lucide-react";
import { getTopicIcon, ICON_OPTIONS } from "../utils/topicIcons";
import {
  getTopics, getCoreKnowledge, updateCoreKnowledge, createCoreKnowledge,
  deleteCoreKnowledge, getHighFreq, updateHighFreq, createTopic, deleteTopic, generateKnowledge,
} from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { TopicInfo } from "../types/api";

interface CoreFile {
  filename: string;
  content: string;
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
  const [hfSaving, setHfSaving] = useState<boolean>(false);

  const [newFileName, setNewFileName] = useState<string>("");
  const [showNewFile, setShowNewFile] = useState<boolean>(false);
  const [generating, setGenerating] = useState<boolean>(false);
  const [genProgress, setGenProgress] = useState<string>("");

  const [showAddTopic, setShowAddTopic] = useState<boolean>(false);
  const [newTopicName, setNewTopicName] = useState<string>("");
  const [newTopicIcon, setNewTopicIcon] = useState<string>("FileText");

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
    } catch { setHighFreq(""); setHighFreqDraft(""); }
  }, []);

  useEffect(() => {
    if (!selected) return;
    loadCore(selected);
    loadHighFreq(selected);
  }, [selected, loadCore, loadHighFreq]);

  const handleSaveCore = async (filename: string) => {
    setCoreSaving(filename);
    try {
      await updateCoreKnowledge(selected!, filename, editContent[filename] || "");
      setCoreFiles((prev) => prev.map((f) => f.filename === filename ? { ...f, content: editContent[filename] } : f));
    } catch (e: any) { alert("保存失败: " + e.message); }
    setTimeout(() => setCoreSaving(null), 1500);
  };

  const handleSaveHighFreq = async () => {
    setHfSaving(true);
    try {
      await updateHighFreq(selected!, highFreqDraft);
      setHighFreq(highFreqDraft);
    } catch (e: any) { alert("保存失败: " + e.message); }
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
    } catch (e: any) { alert("创建失败: " + e.message); }
  };

  const handleDeleteFile = async (filename: string) => {
    if (!confirm(`确定删除「${filename}」？此操作不可撤销。`)) return;
    try {
      await deleteCoreKnowledge(selected!, filename);
      setCoreFiles((prev) => prev.filter((f) => f.filename !== filename));
      if (expandedFile === filename) setExpandedFile(null);
    } catch (e: any) { alert("删除失败: " + e.message); }
  };

  const handleGenerate = async () => {
    setGenerating(true);
    setGenProgress("");
    try {
      await generateKnowledge(selected!, { onProgress: (msg) => setGenProgress(msg) });
      await loadCore(selected!);
      setExpandedFile("README.md");
    } catch (e: any) { alert("生成失败: " + e.message); }
    setGenerating(false);
  };

  const coreIsEmpty = coreFiles.length === 0 ||
    (coreFiles.length === 1 && coreFiles[0].filename === "README.md" && (coreFiles[0].content?.length || 0) <= 20);

  const handleAddTopic = async () => {
    const name = newTopicName.trim();
    if (!name) return;
    try {
      const result = await createTopic(name, newTopicIcon);
      setNewTopicName(""); setNewTopicIcon("FileText");
      setShowAddTopic(false);
      await refreshTopics();
      setSelected(result.key);
    } catch (e: any) { alert("添加失败: " + e.message); }
  };

  const handleDeleteTopic = async (key: string) => {
    if (!confirm(`确定删除「${topics[key]?.name || key}」？`)) return;
    try {
      await deleteTopic(key);
      const t = await refreshTopics();
      const keys = Object.keys(t);
      if (selected === key) setSelected(keys.length > 0 ? keys[0] : null);
    } catch (e: any) { alert("删除失败: " + e.message); }
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
        "fixed inset-y-0 left-0 z-30 w-[200px] border-r border-border bg-bg p-4 flex flex-col transition-transform duration-200 md:static md:translate-x-0 md:shrink-0",
        sidebarOpen ? "translate-x-0" : "-translate-x-full"
      )}>
        <div className="flex justify-between items-center mb-3 px-2">
          <div className="text-[13px] font-semibold text-dim">专项领域</div>
          <Button variant="ghost" size="icon" className="w-6 h-6 text-base" title="新增领域" onClick={() => setShowAddTopic(true)}>+</Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {topicKeys.map((key) => (
            <div key={key} className="relative mb-0.5 group">
              <button
                className={cn(
                  "w-full px-3 py-2.5 rounded-lg text-sm text-left flex items-center gap-2 transition-all cursor-pointer",
                  selected === key ? "bg-secondary text-text" : "text-dim hover:bg-secondary"
                )}
                onClick={() => { setSelected(key); setSidebarOpen(false); }}
              >
                <span className="text-dim">{getTopicIcon(topics[key]?.icon, 16)}</span>
                <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{topics[key]?.name || key}</span>
              </button>
              <button
                className="absolute right-2 top-1/2 -translate-y-1/2 text-dim text-sm px-1.5 py-1 rounded opacity-0 group-hover:opacity-100 hover:text-red hover:bg-red/10 transition-all cursor-pointer"
                title="删除领域"
                onClick={() => handleDeleteTopic(key)}
              ><X size={14} /></button>
            </div>
          ))}
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
        <div className="flex border-b border-border px-4 md:px-6 bg-card">
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
        </div>

        <div className="flex-1 overflow-y-auto p-4 md:p-6">
          {!selected ? (
            <div className="text-center py-15 text-dim text-sm">选择一个领域</div>
          ) : tab === "core" ? (
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
                  {coreFiles.map((f) => (
                    <Card key={f.filename} className="overflow-hidden">
                      <div
                        className="flex justify-between items-center px-4 py-3 cursor-pointer text-sm font-medium hover:bg-secondary/50 transition-colors"
                        onClick={() => setExpandedFile(expandedFile === f.filename ? null : f.filename)}
                      >
                        <span>{f.filename}</span>
                        <div className="flex items-center gap-2">
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
                            className="w-full min-h-[300px] p-3 rounded-lg border border-border bg-bg text-text text-[13px] font-mono leading-relaxed resize-y focus:outline-none focus:border-primary"
                            value={editContent[f.filename] ?? f.content}
                            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setEditContent((prev) => ({ ...prev, [f.filename]: e.target.value }))}
                          />
                          <div className="flex gap-2 mt-3 justify-end">
                            {coreSaving === f.filename && <span className="text-xs text-green self-center mr-3">已保存</span>}
                            <Button variant="default" size="sm" onClick={() => handleSaveCore(f.filename)}>保存</Button>
                          </div>
                        </div>
                      )}
                    </Card>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div>
              <div className="text-[13px] text-dim mb-3">
                标记的高频面试考点，出题时会优先覆盖。支持 Markdown 格式。
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
        </div>
      </div>
    </div>
  );
}
