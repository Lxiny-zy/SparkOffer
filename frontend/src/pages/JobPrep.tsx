import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BriefcaseBusiness, Loader2, Sparkles, FileText, Target, ShieldAlert } from "lucide-react";
import { getResumeStatus, previewJobPrep, startJobPrep } from "../api/interview";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

function priorityVariant(priority: string): "destructive" | "blue" | "secondary" {
  if (priority === "high") return "destructive";
  if (priority === "medium") return "blue";
  return "secondary";
}

interface ResumeFile {
  filename: string;
  size: number;
}

export default function JobPrep() {
  const navigate = useNavigate();
  const [company, setCompany] = useState<string>("");
  const [position, setPosition] = useState<string>("");
  const [jdText, setJdText] = useState<string>("");
  const [resumeFile, setResumeFile] = useState<ResumeFile | null>(null);
  const [useResume, setUseResume] = useState<boolean>(true);
  const [preview, setPreview] = useState<any>(null);
  const [previewSignature, setPreviewSignature] = useState<string>("");
  const [loadingResume, setLoadingResume] = useState<boolean>(true);
  const [previewing, setPreviewing] = useState<boolean>(false);
  const [previewProgress, setPreviewProgress] = useState<string>("");
  const [starting, setStarting] = useState<boolean>(false);
  const [startProgress, setStartProgress] = useState<string>("");
  const [error, setError] = useState<string>("");
  // Aborts the in-flight preview/start stream on unmount so the SSE connection
  // is released instead of lingering and firing setState on an unmounted page.
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    getResumeStatus()
      .then((data: any) => {
        if (data.has_resume) {
          setResumeFile({ filename: data.filename, size: data.size });
          setUseResume(true);
        } else {
          setUseResume(false);
        }
      })
      .catch(() => setUseResume(false))
      .finally(() => setLoadingResume(false));
  }, []);

  const payload = useMemo(() => ({
    company: company.trim() || null,
    position: position.trim() || null,
    jd_text: jdText.trim(),
    use_resume: !!(useResume && resumeFile),
  }), [company, position, jdText, useResume, resumeFile]);

  const signature = JSON.stringify(payload);
  const previewStale = !!preview && previewSignature !== signature;
  const canPreview = payload.jd_text.length >= 50 && !previewing;
  const canStart = !!preview && !previewStale && !starting;

  const handlePreview = async () => {
    // Warn on re-analysis, as it discards "你现在能打的点" and other analysis results.
    if (preview && !confirm('重新分析将覆盖当前的岗位分析结果（含"你现在能打的点"），确定继续？')) {
      return;
    }
    setPreviewing(true);
    setPreviewProgress("");
    setError("");
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const data = await previewJobPrep(payload, { onProgress: (msg) => setPreviewProgress(msg) }, controller.signal);
      setPreview(data.preview);
      setPreviewSignature(signature);
    } catch (err: any) {
      if (controller.signal.aborted) return;
      setError("JD 分析失败: " + err.message);
    } finally {
      if (!controller.signal.aborted) setPreviewing(false);
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const handleStart = async () => {
    setStarting(true);
    setStartProgress("");
    setError("");
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const data = await startJobPrep({ ...payload, preview_data: preview }, {
        onProgress: (msg) => setStartProgress(msg),
      }, controller.signal);
      navigate(`/interview/${data.session_id}`, { state: data });
    } catch (err: any) {
      if (controller.signal.aborted) return;
      setError("启动失败: " + err.message);
      setStarting(false);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  return (
    <div className="flex-1 overflow-y-auto min-h-0 flex flex-col items-center px-4 pt-8 pb-10 md:px-6 md:pt-12">
      <div className="w-full max-w-[860px]">
        <div className="mb-8 animate-fade-in">
          <div className="sig-kicker mb-2">// JD 定向备面 / JOB PREP</div>
          <h1 className="sig-display text-2xl md:text-[28px] mb-2">定向备战<span className="sig-accent-c">.</span></h1>
          <p className="text-sm text-dim">贴入岗位 JD，AI 先拆解岗位重点，再结合你的简历和历史画像生成定向追问。</p>
        </div>

        <Card className="mb-6 animate-fade-in-up">
          <CardContent className="p-5 md:p-6 space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label className="text-xs">公司（可选）</Label>
                <Input placeholder="例：字节跳动" value={company} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCompany(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">岗位（建议填写）</Label>
                <Input placeholder="例：AI 后台开发实习生" value={position} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPosition(e.target.value)} />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">岗位 JD</Label>
              <Textarea
                className="min-h-[240px] resize-none md:resize-y"
                placeholder="粘贴完整 JD。内容越完整，问题预测和岗位匹配分析越准。"
                value={jdText}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setJdText(e.target.value)}
              />
              <div className="text-[12px] text-dim">{jdText.trim().length} 字</div>
            </div>

            <div className="rounded-xl border border-border bg-secondary/50 px-4 py-3">
              <label className={cn("flex items-start gap-3", !resumeFile && "opacity-70")}>
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={!!(useResume && resumeFile)}
                  disabled={!resumeFile}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setUseResume(e.target.checked)}
                />
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium">结合简历生成针对性追问</span>
                    <Badge variant={resumeFile ? "blue" : "secondary"}>
                      {loadingResume ? "检查中" : resumeFile ? "已可用" : "未上传简历"}
                    </Badge>
                  </div>
                  <div className="text-[13px] text-dim leading-relaxed">
                    {resumeFile
                      ? `已检测到简历：${resumeFile.filename}。开启后，问题会直接对照你的项目和岗位要求。`
                      : "当前没有可用简历。你仍然可以只基于 JD 做岗位拆解和定向训练。"}
                  </div>
                </div>
              </label>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-md border text-sm animate-shake" style={{ borderColor: "color-mix(in srgb, var(--sig-danger) 35%, transparent)", background: "color-mix(in srgb, var(--sig-danger) 8%, transparent)", color: "var(--sig-danger)" }}>
            {error}
          </div>
        )}

        {previewStale && (
          <div className="mb-4 px-4 py-3 rounded-md border text-sm animate-fade-in" style={{ borderColor: "color-mix(in srgb, var(--sig-warning) 35%, transparent)", background: "color-mix(in srgb, var(--sig-warning) 8%, transparent)", color: "var(--sig-warning)" }}>
            你修改了 JD 或岗位信息，请重新分析后再开始训练。
          </div>
        )}

        {preview && (
          <div className="space-y-6 animate-fade-in">
            <Card>
              <CardContent className="p-5 md:p-6">
                <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <BriefcaseBusiness size={18} className="text-tertiary" />
                      <span className="text-lg font-semibold">
                        {preview.company ? `${preview.company} · ` : ""}{preview.position || "目标岗位"}
                      </span>
                    </div>
                    <p className="text-sm text-dim leading-relaxed">{preview.role_summary}</p>
                  </div>
                  <Badge variant={preview.resume_alignment?.resume_used ? "blue" : "secondary"}>
                    {preview.resume_alignment?.resume_used ? "JD + 简历联动" : "仅 JD 分析"}
                  </Badge>
                </div>

                {preview.resume_alignment?.fit_assessment && (
                  <div className="rounded-xl bg-tertiary/8 border border-tertiary/15 px-4 py-3 text-sm leading-relaxed">
                    <div className="text-[13px] font-semibold text-tertiary mb-1.5">岗位匹配判断</div>
                    {preview.resume_alignment.fit_assessment}
                  </div>
                )}
              </CardContent>
            </Card>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <Card>
                <CardContent className="p-5 md:p-6">
                  <div className="flex items-center gap-2 mb-4">
                    <Target size={17} className="text-primary" />
                    <span className="font-semibold">核心考察点</span>
                  </div>
                  <div className="space-y-3">
                    {(preview.focus_areas || []).map((item: any, idx: number) => (
                      <div key={`${item.area}-${idx}`} className="rounded-xl border border-border px-4 py-3">
                        <div className="flex items-center justify-between gap-3 mb-1.5">
                          <span className="font-medium text-sm">{item.area}</span>
                          <Badge variant={priorityVariant(item.priority)}>{item.priority || "normal"}</Badge>
                        </div>
                        <div className="text-[13px] text-dim leading-relaxed">{item.reason}</div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardContent className="p-5 md:p-6">
                  <div className="flex items-center gap-2 mb-4">
                    <ShieldAlert size={17} className="text-red" />
                    <span className="font-semibold">面试前优先补强</span>
                  </div>
                  <div className="space-y-2">
                    {(preview.prep_priorities || []).map((item: string, idx: number) => (
                      <div key={`${item}-${idx}`} className="rounded-xl bg-red/8 border border-red/15 px-4 py-3 text-sm leading-relaxed">
                        {item}
                      </div>
                    ))}
                    {preview.resume_alignment?.risk_gaps?.map((item: string, idx: number) => (
                      <div key={`gap-${idx}`} className="rounded-xl bg-secondary px-4 py-3 text-sm text-dim leading-relaxed">
                        {item}
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </div>

            {(preview.resume_alignment?.matching_evidence?.length > 0 || preview.resume_alignment?.recommended_stories?.length > 0) && (
              <Card>
                <CardContent className="p-5 md:p-6">
                  <div className="flex items-center gap-2 mb-4">
                    <FileText size={17} className="text-green" />
                    <span className="font-semibold">简历对位建议</span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <div className="text-[13px] font-semibold text-green mb-2">你现在能打的点</div>
                      <div className="space-y-2">
                        {(preview.resume_alignment?.matching_evidence || []).map((item: string, idx: number) => (
                          <div key={`evidence-${idx}`} className="rounded-xl bg-green/8 border border-green/15 px-4 py-3 text-sm leading-relaxed">
                            {item}
                          </div>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="text-[13px] font-semibold text-primary mb-2">优先拿来讲的经历</div>
                      <div className="space-y-2">
                        {(preview.resume_alignment?.recommended_stories || []).map((item: any, idx: number) => (
                          <div key={`story-${idx}`} className="rounded-xl bg-secondary px-4 py-3">
                            <div className="text-sm font-medium mb-1">{item.project}</div>
                            <div className="text-[13px] text-dim leading-relaxed">{item.reason}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            <Card>
              <CardContent className="p-5 md:p-6">
                <div className="flex items-center gap-2 mb-4">
                  <Sparkles size={17} className="text-primary" />
                  <span className="font-semibold">高概率提问方向</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {(preview.likely_question_groups || []).map((group: any, idx: number) => (
                    <div key={`${group.title}-${idx}`} className="rounded-xl border border-border px-4 py-4">
                      <div className="text-sm font-medium mb-1.5">{group.title}</div>
                      <div className="text-[13px] text-dim leading-relaxed mb-3">{group.reason}</div>
                      <div className="space-y-2">
                        {(group.sample_questions || []).map((q: string, qIdx: number) => (
                          <div key={`${q}-${qIdx}`} className="text-sm leading-relaxed text-text bg-secondary rounded-lg px-3 py-2.5">
                            {q}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        <div className="mt-6">
          <Button variant="ghost" onClick={() => navigate("/")}>返回首页</Button>
        </div>

        {/* Spacer for sticky action bar */}
        <div className="h-24" />

        {/* Sticky action bar — always-visible Preview / Start CTA */}
        <div className="sticky bottom-0 z-20 -mx-4 md:-mx-6 px-4 md:px-6 pb-4 pt-3 mt-2" style={{ background: "linear-gradient(to top, var(--sig-bg), color-mix(in srgb, var(--sig-bg) 80%, transparent) 70%, transparent)" }}>
          <div className="max-w-[860px] mx-auto">
            <div className="sig-card p-3 shadow-[0_-4px_24px_rgba(0,0,0,0.12)]">
              <div className="flex flex-col md:flex-row gap-2.5">
                <Button
                  variant="default"
                  size="lg"
                  className="md:flex-1 transition-all"
                  disabled={!canPreview}
                  onClick={handlePreview}
                >
                  {previewing
                    ? <><Loader2 size={18} className="animate-spin" /> {previewProgress || "分析中..."}</>
                    : <><Sparkles size={18} /> {preview ? "重新分析岗位" : "先分析这个岗位"}</>}
                </Button>
                <Button
                  variant={canStart ? "default" : "outline"}
                  size="lg"
                  className="md:w-[240px] transition-all"
                  disabled={!canStart}
                  onClick={handleStart}
                >
                  {starting
                    ? <><Loader2 size={18} className="animate-spin" /> {startProgress || "初始化中..."}</>
                    : "开始定向训练"}
                </Button>
              </div>
              {/* Status hint line */}
              <div className="mt-2 flex items-center justify-center gap-2 text-[11px] text-dim">
                {!canPreview && payload.jd_text.length < 50 && (
                  <span>📝 JD 至少 50 字才能开始分析（当前 {payload.jd_text.length}）</span>
                )}
                {canPreview && !preview && (
                  <span className="text-primary">→ 表单已就绪，点击分析</span>
                )}
                {preview && !previewStale && (
                  <span className="text-green">✓ 分析完成，可以开始训练</span>
                )}
                {previewStale && (
                  <span className="text-warning">⚠ 表单已修改，请重新分析</span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
