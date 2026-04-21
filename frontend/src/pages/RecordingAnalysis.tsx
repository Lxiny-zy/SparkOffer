import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Upload, FileText, Users, User, Loader2 } from "lucide-react";
import { transcribeRecording, analyzeRecording } from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";

const RECORDING_MODES = [
  { key: "dual", label: "双人对话", sub: "面试官+你", Icon: Users, color: "blue" },
  { key: "solo", label: "单人录音", sub: "只有你", Icon: User, color: "violet" },
];

export default function RecordingAnalysis() {
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);

  const [recordingMode, setRecordingMode] = useState("dual");
  const [inputTab, setInputTab] = useState("upload");
  const [transcript, setTranscript] = useState("");
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [company, setCompany] = useState("");
  const [position, setPosition] = useState("");

  const [transcribing, setTranscribing] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeProgress, setAnalyzeProgress] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setAudioFile(file);
    setTranscript("");
    setError(null);
  };

  const handleTranscribe = async () => {
    if (!audioFile) return;
    setTranscribing(true);
    setError(null);
    try {
      const data = await transcribeRecording(audioFile, recordingMode);
      setTranscript(data.transcript || "");
    } catch (err: any) {
      setError("转写失败: " + err.message);
    } finally {
      setTranscribing(false);
    }
  };

  const handleAnalyze = async () => {
    if (!transcript.trim()) return;
    setAnalyzing(true);
    setAnalyzeProgress("");
    setError(null);
    try {
      const data = await analyzeRecording(transcript, recordingMode, company || null, position || null, {
        onProgress: (msg) => setAnalyzeProgress(msg),
      });
      navigate(`/review/${data.session_id}`, { state: { ...data, mode: "recording" } });
    } catch (err: any) {
      setError("分析失败: " + err.message);
      setAnalyzing(false);
    }
  };

  const canAnalyze = transcript.trim() && !analyzing;

  return (
    <div className="flex-1 flex flex-col items-center px-4 pt-8 pb-10 md:px-6 md:pt-12">
      <div className="w-full max-w-[700px]">
        <div className="mb-8 animate-fade-in">
          <h1 className="text-2xl md:text-[28px] font-display font-bold mb-2">录音复盘</h1>
          <p className="text-sm text-dim">上传面试录音或粘贴转写文字，AI 自动识别涉及领域并分析复盘</p>
        </div>

        <div className="mb-6 animate-fade-in-up">
          <div className="text-[15px] font-semibold mb-3">录音模式</div>
          <div className="flex gap-3">
            {RECORDING_MODES.map(({ key, label, sub, Icon, color }) => (
              <Card
                key={key}
                className={cn(
                  "flex-1 cursor-pointer transition-all hover:-translate-y-px",
                  recordingMode === key && "border-2 shadow-md",
                  recordingMode === key && color === "blue" && "border-tertiary",
                  recordingMode === key && color === "violet" && "border-violet-500"
                )}
                onClick={() => setRecordingMode(key)}
              >
                <CardContent className="p-4 flex items-center gap-3">
                  <div className={cn(
                    "w-10 h-10 rounded-lg flex items-center justify-center shrink-0",
                    recordingMode === key
                      ? color === "blue" ? "bg-tertiary/15 text-tertiary" : "bg-primary/15 text-primary"
                      : "bg-secondary text-dim"
                  )}>
                    <Icon size={20} />
                  </div>
                  <div>
                    <div className="text-sm font-medium">{label}</div>
                    <div className="text-xs text-dim">{sub}</div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>

        <div className="flex gap-3 mb-6 animate-fade-in-up [animation-delay:0.05s]">
          <div className="flex-1 space-y-1">
            <Label className="text-xs">公司（可选）</Label>
            <Input placeholder="例：字节跳动" value={company} onChange={(e) => setCompany(e.target.value)} />
          </div>
          <div className="flex-1 space-y-1">
            <Label className="text-xs">岗位（可选）</Label>
            <Input placeholder="例：后端开发实习" value={position} onChange={(e) => setPosition(e.target.value)} />
          </div>
        </div>

        <div className="mb-4 animate-fade-in-up [animation-delay:0.1s]">
          <div className="flex gap-1 bg-secondary rounded-lg p-1 w-fit mb-4">
            {["upload", "paste"].map((t) => (
              <button
                key={t}
                className={cn(
                  "px-4 py-1.5 rounded-md text-sm transition-all cursor-pointer",
                  inputTab === t ? "bg-card text-text shadow-sm font-medium" : "text-dim hover:text-text"
                )}
                onClick={() => setInputTab(t)}
              >
                {t === "upload" ? "上传录音" : "粘贴文字"}
              </button>
            ))}
          </div>

          {inputTab === "upload" && (
            <div className="space-y-3">
              <Card
                className={cn(
                  "cursor-pointer transition-all hover:-translate-y-px",
                  audioFile ? "border-primary/40" : "border-dashed"
                )}
                onClick={() => fileRef.current?.click()}
              >
                <CardContent className="p-8 flex flex-col items-center gap-3">
                  {audioFile ? (
                    <>
                      <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center">
                        <FileText size={24} className="text-primary" />
                      </div>
                      <div className="text-center">
                        <div className="font-medium text-text">{audioFile.name}</div>
                        <div className="text-xs text-dim mt-0.5">{(audioFile.size / 1024 / 1024).toFixed(1)} MB — 点击更换</div>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="w-12 h-12 rounded-xl bg-secondary flex items-center justify-center">
                        <Upload size={24} className="text-dim" />
                      </div>
                      <div className="text-center">
                        <div className="font-medium text-text">点击上传音频文件</div>
                        <div className="text-xs text-dim mt-0.5">支持 mp3, wav, m4a, webm 等格式</div>
                      </div>
                    </>
                  )}
                  <input ref={fileRef} type="file" accept="audio/*" className="hidden" onChange={handleFileChange} />
                </CardContent>
              </Card>

              {audioFile && !transcript && (
                <Button
                  variant="outline"
                  className="w-full border-tertiary/30 text-tertiary hover:bg-tertiary/10"
                  onClick={handleTranscribe}
                  disabled={transcribing}
                >
                  {transcribing ? (
                    <><Loader2 size={16} className="animate-spin" /> 转写中，请稍候...</>
                  ) : "开始转写"}
                </Button>
              )}
            </div>
          )}

          {inputTab === "paste" && !transcript && (
            <textarea
              className="w-full h-40 md:h-48 px-4 py-3 rounded-xl bg-card border border-border text-sm text-text leading-relaxed resize-none md:resize-y focus:outline-none focus:border-primary"
              placeholder={
                recordingMode === "dual"
                  ? "粘贴面试对话记录...\n\n格式示例：\n面试官：请介绍一下你自己\n我：我是XXX，目前..."
                  : "粘贴你的技术表达/复盘内容..."
              }
              onBlur={(e) => setTranscript(e.target.value)}
              defaultValue=""
            />
          )}
        </div>

        {transcript && (
          <div className="mb-6 animate-fade-in">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[15px] font-semibold">转写结果</span>
              <span className="text-xs text-dim">可直接编辑修正</span>
            </div>
            <textarea
              className="w-full h-48 md:h-64 px-4 py-3 rounded-xl bg-card border border-border text-sm text-text leading-relaxed resize-none md:resize-y focus:outline-none focus:border-primary"
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
            />
          </div>
        )}

        {error && (
          <div className="mb-4 px-4 py-3 rounded-xl bg-red/10 border border-red/20 text-sm text-red animate-shake">
            {error}
          </div>
        )}

        {transcript && (
          <Button
            variant="default"
            size="lg"
            className="w-full animate-fade-in-up"
            disabled={!canAnalyze}
            onClick={handleAnalyze}
          >
            {analyzing ? (
              <><Loader2 size={18} className="animate-spin" /> {analyzeProgress || "AI 分析中..."}</>
            ) : "开始分析"}
          </Button>
        )}

        <div className="mt-6">
          <Button variant="ghost" onClick={() => navigate("/")}>返回首页</Button>
        </div>
      </div>
    </div>
  );
}
