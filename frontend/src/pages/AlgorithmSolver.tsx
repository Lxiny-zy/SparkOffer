import { useState, useRef, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import {
  Code2, Send, Save, Play, Library, Loader2, X,
} from "lucide-react";
import { solveAlgorithm, chatAlgorithm, saveAlgorithmCard } from "../api/interview";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import ChatBubble from "@/components/ChatBubble";
import type { ChatMessage } from "../types/api";

const LANGUAGES = [
  { value: "python", label: "Python" },
  { value: "java", label: "Java" },
  { value: "cpp", label: "C++" },
  { value: "javascript", label: "JavaScript" },
  { value: "go", label: "Go" },
  { value: "typescript", label: "TypeScript" },
];

export default function AlgorithmSolver() {
  const navigate = useNavigate();
  const location = useLocation();
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Input state
  const [problemText, setProblemText] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [language, setLanguage] = useState("python");

  // Session state
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [solution, setSolution] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");

  // Loading states
  const [solving, setSolving] = useState(false);
  const [solveProgress, setSolveProgress] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatProgress, setChatProgress] = useState("");
  const [saving, setSaving] = useState(false);

  // Save dialog
  const [showSave, setShowSave] = useState(false);
  const [saveTitle, setSaveTitle] = useState("");
  const [saveDifficulty, setSaveDifficulty] = useState("");
  const [saveTags, setSaveTags] = useState("");
  const [saveNote, setSaveNote] = useState("");

  // Pre-fill from navigation state (re-open from collection)
  useEffect(() => {
    const s = location.state as any;
    if (s?.problemText) {
      setProblemText(s.problemText);
      setSourceUrl(s.sourceUrl || "");
      setLanguage(s.language || "python");
    }
  }, [location.state]);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, solution]);

  const handleSolve = async () => {
    if (!problemText.trim()) return;
    setSolving(true);
    setSolveProgress("");
    setSolution("");
    setChatMessages([]);
    setSessionId(null);
    try {
      const data = await solveAlgorithm(problemText.trim(), language, sourceUrl.trim(), {
        onProgress: (msg) => setSolveProgress(msg),
      });
      setSessionId(data.session_id);
      setSolution(data.solution);
    } catch (e) {
      console.error("解题失败:", e);
      setSolution("解题请求失败，请检查后端服务是否正常运行。");
    }
    setSolving(false);
  };

  const handleChat = async () => {
    if (!chatInput.trim() || !sessionId) return;
    const userMsg = chatInput.trim();
    setChatInput("");
    setChatMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setChatLoading(true);
    setChatProgress("");
    try {
      const data = await chatAlgorithm(sessionId, userMsg, {
        onProgress: (msg) => setChatProgress(msg),
      });
      setChatMessages((prev) => [...prev, { role: "assistant", content: data.message }]);
    } catch (e) {
      console.error("对话失败:", e);
      setChatMessages((prev) => [...prev, { role: "assistant", content: "请求失败，请重试。" }]);
    }
    setChatLoading(false);
  };

  const handleSave = async () => {
    if (!saveTitle.trim() || !sessionId) return;
    setSaving(true);
    try {
      const tags = saveTags.split(/[,，\s]+/).filter(Boolean);
      await saveAlgorithmCard(sessionId, saveTitle.trim(), saveDifficulty, tags, saveNote.trim());
      setShowSave(false);
      navigate("/algorithm/collection");
    } catch (e) {
      console.error("保存失败:", e);
    }
    setSaving(false);
  };

  const handleReset = () => {
    setProblemText("");
    setSourceUrl("");
    setSolution("");
    setChatMessages([]);
    setSessionId(null);
    setChatInput("");
    setShowSave(false);
  };

  return (
    <div className="flex-1 px-4 py-8 md:px-6 md:py-10 max-w-4xl mx-auto w-full">
      {/* Header */}
      <div className="mb-8 animate-fade-in">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Code2 size={20} className="text-primary" />
            <h1 className="text-2xl md:text-[28px] font-display font-bold">算法解题</h1>
          </div>
          <Button variant="outline" size="sm" onClick={() => navigate("/algorithm/collection")}>
            <Library size={14} /> 算法收藏
          </Button>
        </div>
        <p className="text-sm text-dim">粘贴算法题目，AI 帮你分析解答，支持多轮对话修改</p>
      </div>

      {/* Input Section */}
      <Card className="mb-6 animate-fade-in">
        <CardContent className="p-4 md:p-5 space-y-3">
          <Textarea
            placeholder="在这里粘贴算法题目描述..."
            className="min-h-[160px] text-sm leading-relaxed resize-y"
            value={problemText}
            onChange={(e) => setProblemText(e.target.value)}
            disabled={solving}
          />
          <div className="flex flex-wrap items-center gap-3">
            <Input
              placeholder="题目链接 (可选)"
              className="flex-1 min-w-0 md:min-w-[200px] h-9 text-sm"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              disabled={solving}
            />
            <select
              className="bg-card border border-border rounded-lg px-3 py-1.5 text-sm h-9"
              value={language}
              onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setLanguage(e.target.value)}
              disabled={solving}
            >
              {LANGUAGES.map((l) => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
            <Button onClick={handleSolve} disabled={!problemText.trim() || solving}>
              {solving ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              {solving ? "解题中..." : "开始解题"}
            </Button>
            {sessionId && (
              <Button variant="ghost" size="sm" onClick={handleReset}>
                <X size={14} /> 重新开始
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Solution Display */}
      {(solution || solving) && (
        <div className="animate-fade-in">
          {/* Solution Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">AI 解答</h2>
              <Badge variant="secondary">{LANGUAGES.find((l) => l.value === language)?.label}</Badge>
            </div>
            {sessionId && !solving && (
              <Button
                size="sm"
                onClick={() => {
                  setShowSave(true);
                  const firstLine = problemText.split("\n")[0].slice(0, 60);
                  setSaveTitle(firstLine);
                }}
              >
                <Save size={14} /> 保存到收藏
              </Button>
            )}
          </div>

          {/* Solution Content */}
          {solving ? (
            <Card>
              <CardContent className="p-6 flex items-center justify-center gap-3 text-dim">
                <Loader2 size={20} className="animate-spin" />
                <span>{solveProgress || "AI 正在分析题目并生成解答..."}</span>
              </CardContent>
            </Card>
          ) : (
            <Card className="mb-6">
              <CardContent className="p-4 md:p-5">
                <div className="md-content text-[15px] leading-[1.8]">
                  <ReactMarkdown>{solution}</ReactMarkdown>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Chat Messages */}
          {chatMessages.length > 0 && (
            <div className="space-y-4 mb-6">
              {chatMessages.map((msg, i) => (
                <ChatBubble key={i} role={msg.role} content={msg.content} />
              ))}
              {chatLoading && (
                <div className="flex items-center gap-2 text-dim text-sm">
                  <Loader2 size={14} className="animate-spin" />
                  <span>{chatProgress || "AI 正在思考..."}</span>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>
          )}

          {/* Chat Input */}
          {sessionId && !solving && (
            <div className="flex items-center gap-2">
              <Input
                placeholder="追问或要求修改解答..."
                className="flex-1 h-10"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleChat()}
                disabled={chatLoading}
              />
              <Button onClick={handleChat} disabled={!chatInput.trim() || chatLoading} size="icon" className="h-10 w-10">
                <Send size={16} />
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Save Dialog */}
      {showSave && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <Card className="w-full max-w-md mx-4 animate-fade-in">
            <CardContent className="p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold">保存到算法收藏</h3>
                <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setShowSave(false)}>
                  <X size={16} />
                </Button>
              </div>
              <div className="space-y-3">
                <div>
                  <label className="text-sm font-medium text-dim mb-1 block">标题</label>
                  <Input
                    value={saveTitle}
                    onChange={(e) => setSaveTitle(e.target.value)}
                    placeholder="例: 两数之和"
                  />
                </div>
                <div>
                  <label className="text-sm font-medium text-dim mb-1 block">难度</label>
                  <select
                    className="w-full bg-card border border-border rounded-lg px-3 py-2 text-sm"
                    value={saveDifficulty}
                    onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setSaveDifficulty(e.target.value)}
                  >
                    <option value="">未指定</option>
                    <option value="easy">简单 Easy</option>
                    <option value="medium">中等 Medium</option>
                    <option value="hard">困难 Hard</option>
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium text-dim mb-1 block">标签 (逗号分隔)</label>
                  <Input
                    value={saveTags}
                    onChange={(e) => setSaveTags(e.target.value)}
                    placeholder="例: 数组, 哈希表, 双指针"
                  />
                </div>
                <div>
                  <label className="text-sm font-medium text-dim mb-1 block">笔记 (可选)</label>
                  <Textarea
                    value={saveNote}
                    onChange={(e) => setSaveNote(e.target.value)}
                    placeholder="你的理解和心得..."
                    className="min-h-[80px]"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" onClick={() => setShowSave(false)}>取消</Button>
                <Button onClick={handleSave} disabled={!saveTitle.trim() || saving}>
                  {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                  {saving ? "保存中..." : "保存"}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
