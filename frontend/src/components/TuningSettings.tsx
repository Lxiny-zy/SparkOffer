import { useEffect, useState, useCallback } from "react";
import { Loader2, Save, ChevronRight, ChevronDown } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { getTuning, saveTuning } from "@/api/settings";

// Keep in sync with backend ai_config.RETRIEVAL_PRESETS / _RETRIEVAL_CLAMP.
const RETRIEVAL_FIELDS: { key: string; label: string; min: number; max: number; step: number; hint?: string }[] = [
  { key: "per_query_top_k", label: "每路 top_k", min: 1, max: 20, step: 1, hint: "每个薄弱点检索条数" },
  { key: "final_top_n", label: "最终 top_n", min: 1, max: 50, step: 1, hint: "融合去重后保留条数" },
  { key: "embed_concurrency", label: "embedding 并发", min: 1, max: 16, step: 1, hint: "并发嵌入请求；DashScope 单 key 建议 ≤2" },
  { key: "dedup_threshold", label: "去重阈值", min: 0.5, max: 0.99, step: 0.01, hint: "余弦 ≥ 此值视为重复片段" },
  { key: "end_to_end_timeout", label: "总超时 (秒)", min: 10, max: 600, step: 5, hint: "RAG 整体预算；超时降级空上下文" },
  { key: "per_query_timeout", label: "单路超时 (秒)", min: 5, max: 300, step: 5 },
  { key: "reranker_read_timeout", label: "rerank 超时 (秒)", min: 5, max: 120, step: 5 },
];

const PRESET_LABELS: Record<string, string> = { fast: "快速", balanced: "均衡", thorough: "充分", custom: "自定义" };

type NumMap = Record<string, number | undefined>;

export default function TuningSettings() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxOutput, setMaxOutput] = useState<number | undefined>(32768);
  const [defaultWindow, setDefaultWindow] = useState<number | undefined>(200000);
  const [preset, setPreset] = useState<string>("balanced");
  const [retrieval, setRetrieval] = useState<NumMap>({});
  const [presets, setPresets] = useState<Record<string, Record<string, number>>>({});

  const load = useCallback(async () => {
    try {
      const data = await getTuning();
      const v = data.values || {};
      setMaxOutput(v.max_output_tokens ?? 32768);
      setDefaultWindow(v.default_context_window ?? 200000);
      const r = v.retrieval || {};
      const { preset: p, ...nums } = r;
      setPreset(p || "balanced");
      setRetrieval(nums);
      setPresets(data.presets || {});
    } catch (e: any) {
      toast.error("加载调参失败: " + e.message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const applyPreset = (name: string) => {
    setPreset(name);
    if (name !== "custom" && presets[name]) setRetrieval({ ...presets[name] });
  };
  const editField = (key: string, raw: string, isFloat: boolean) => {
    const n = isFloat ? parseFloat(raw) : parseInt(raw);
    setRetrieval((prev) => ({ ...prev, [key]: isNaN(n) ? undefined : n }));
    setPreset("custom");
  };

  const save = async () => {
    setSaving(true);
    try {
      await saveTuning({
        max_output_tokens: maxOutput,
        default_context_window: defaultWindow,
        retrieval: { preset, ...retrieval },
      });
      toast.success("调参已保存，下次请求即生效");
      await load();
    } catch (e: any) {
      toast.error("保存失败: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-6"><Loader2 className="w-5 h-5 animate-spin text-dim" /></div>;
  }

  return (
    <div className="space-y-5">
      {/* 模型输出 / 全局默认（兜底） */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label className="text-xs">输出上限 max_tokens（兜底）</Label>
          <Input type="number" min={256} step={1024} value={maxOutput ?? ""} onChange={(e) => setMaxOutput(parseInt(e.target.value) || undefined)} className="h-8 text-sm" />
          <p className="text-[10px] text-dim mt-1">渠道未设 Max Tokens 时兜底；推理模型含思考 token，建议 ≥32k</p>
        </div>
        <div>
          <Label className="text-xs">默认上下文窗口（兜底）</Label>
          <Input type="number" min={1000} step={1000} value={defaultWindow ?? ""} onChange={(e) => setDefaultWindow(parseInt(e.target.value) || undefined)} className="h-8 text-sm" />
          <p className="text-[10px] text-dim mt-1">渠道未设 Context Window 时兜底的输入窗口</p>
        </div>
      </div>

      {/* 检索档位 */}
      <div>
        <Label className="text-xs">检索档位</Label>
        <select
          value={preset}
          onChange={(e) => applyPreset(e.target.value)}
          className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring transition-colors hover:border-primary/40"
        >
          {["fast", "balanced", "thorough", "custom"].map((p) => (
            <option key={p} value={p}>{PRESET_LABELS[p]}{p === "balanced" ? "（默认）" : ""}</option>
          ))}
        </select>
        <p className="text-[10px] text-dim mt-1">快速 / 均衡 / 充分 一键设定下面 7 项；手改任一项会转为「自定义」</p>
      </div>

      {/* 高级（折叠） */}
      <div>
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          className="flex items-center gap-1 text-xs text-dim hover:text-text transition-colors"
        >
          {advancedOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />} 高级
        </button>
        {advancedOpen && (
          <div className="grid grid-cols-2 gap-3 mt-2">
            {RETRIEVAL_FIELDS.map((f) => (
              <div key={f.key}>
                <Label className="text-xs">{f.label}</Label>
                <Input
                  type="number" min={f.min} max={f.max} step={f.step}
                  value={retrieval[f.key] ?? ""}
                  placeholder="留空回退默认"
                  onChange={(e) => editField(f.key, e.target.value, f.step < 1)}
                  className="h-8 text-sm"
                />
                {f.hint && <p className="text-[10px] text-dim mt-1">{f.hint}</p>}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex justify-end">
        <Button onClick={save} disabled={saving} size="sm" className="gap-2">
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
          保存
        </Button>
      </div>
    </div>
  );
}
