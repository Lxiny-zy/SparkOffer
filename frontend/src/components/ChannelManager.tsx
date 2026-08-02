import { useState, useEffect, useCallback, useRef } from "react";
import {
  Plus, Trash2, ChevronDown, ChevronRight, FlaskConical,
  ArrowUp, ArrowDown, Eye, EyeOff, Loader2, CheckCircle2,
  XCircle, Lock, AlertTriangle, KeyRound,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { getChannels, saveChannelSection, testChannel } from "@/api/settings";
import type { ChannelHealth } from "@/types/channels";
import { REASONING_EFFORT_HINT } from "@/lib/badge-presets";

type ChannelData = Record<string, any>;

interface ChannelManagerProps {
  section: "llm" | "embedding" | "reranker";
  onDirty?: (dirty: boolean) => void;
}

const SECTION_DEFAULTS: Record<string, () => ChannelData> = {
  llm: () => ({ id: "", name: "", api_base: "", keys: [""], model: "", temperature: 0.7, reasoning_effort: "", max_tokens: 32768, context_window: 0, timeout: 0, tier: "large", priority: 1, enabled: true, proxy: "" }),
  embedding: () => ({ id: "", name: "", backend: "api", api_base: "", keys: [""], api_model: "", local_model: "", local_path: "", priority: 1, enabled: true, proxy: "" }),
  reranker: () => ({ id: "", name: "", api_base: "", keys: [""], api_model: "", priority: 1, enabled: true, proxy: "" }),
};

function readLocalStorage(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function SecretInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input
        type={visible ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="pr-9"
      />
      <button
        type="button"
        className="absolute right-2 top-1/2 -translate-y-1/2 text-dim hover:text-text"
        onClick={() => setVisible(!visible)}
      >
        {visible ? <EyeOff size={15} /> : <Eye size={15} />}
      </button>
    </div>
  );
}

function HealthDot({ health }: { health?: ChannelHealth }) {
  if (!health) return <span className="w-2.5 h-2.5 rounded-full bg-dim/30" />;
  if (!health.healthy) return <span className="w-2.5 h-2.5 rounded-full bg-red animate-pulse" title="Cooldown" />;
  if (health.error_count > 0) return <span className="w-2.5 h-2.5 rounded-full bg-orange" title={`${health.error_count} errors`} />;
  return <span className="w-2.5 h-2.5 rounded-full bg-green" title="Healthy" />;
}

export default function ChannelManager({ section, onDirty }: ChannelManagerProps) {
  const [channels, setChannels] = useState<ChannelData[]>([]);
  const [healthMap, setHealthMap] = useState<Record<string, ChannelHealth>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [testing, setTesting] = useState<Record<string, "loading" | "ok" | "error" | null>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const editGenerationRef = useRef(0);
  const loadGenerationRef = useRef(0);
  const lastEditedKey = `channels:lastEdited:${section}`;
  const lastEditedRef = useRef<string | null>(
    typeof window !== "undefined" ? readLocalStorage(lastEditedKey) : null
  );

  const load = useCallback(async () => {
    const requestGeneration = ++loadGenerationRef.current;
    try {
      const data = await getChannels();
      if (requestGeneration !== loadGenerationRef.current) return;
      const sec = data[section] || {};
      const loadedChannels = Array.isArray(sec.channels) ? sec.channels : [];
      setChannels(loadedChannels.map((ch: ChannelData, index: number) => ({
        ...ch,
        keys: Array.isArray(ch.keys) ? ch.keys : [],
        priority: Number.isFinite(ch.priority) ? ch.priority : index + 1,
      })));
      const hm: Record<string, ChannelHealth> = {};
      for (const h of sec.health || []) hm[h.id] = h;
      setHealthMap(hm);
      setLoadError(null);
    } catch (e: any) {
      if (requestGeneration !== loadGenerationRef.current) return;
      setLoadError(e?.message || "Unable to load channels");
      toast.error("Failed to load channels: " + (e?.message || "Unknown error"));
    } finally {
      if (requestGeneration === loadGenerationRef.current) setLoading(false);
    }
  }, [section]);

  useEffect(() => { load(); }, [load]);

  const markEdited = () => {
    editGenerationRef.current += 1;
    onDirty?.(true);
  };

  const updateChannel = (idx: number, patch: Partial<ChannelData>) => {
    setChannels((prev) => prev.map((ch, i) => i === idx ? { ...ch, ...patch } : ch));
    const ch = channels[idx];
    if (ch?.id) {
      lastEditedRef.current = ch.id;
      try { localStorage.setItem(lastEditedKey, ch.id); } catch { /* ignore */ }
    }
    markEdited();
  };

  const addChannel = () => {
    const ch = SECTION_DEFAULTS[section]();
    const localId = typeof crypto !== "undefined" && crypto.randomUUID
      ? `new-${crypto.randomUUID()}`
      : `new-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    ch.id = localId;
    ch.priority = channels.length + 1;
    ch.name = `Channel ${channels.length + 1}`;
    if (section === "embedding") {
      ch.api_model = channels.find((item) => item.api_model)?.api_model || "";
    }
    setChannels((prev) => [...prev, ch].map((item, index) => ({ ...item, priority: index + 1 })));
    setExpanded((prev) => ({ ...prev, [localId]: true }));
    markEdited();
  };

  const removeChannel = (idx: number) => {
    setChannels((prev) => prev.filter((_, i) => i !== idx).map((ch, i) => ({ ...ch, priority: i + 1 })));
    markEdited();
  };

  const movePriority = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= channels.length) return;
    setChannels((prev) => {
      const next = [...prev];
      [next[idx], next[target]] = [next[target], next[idx]];
      return next.map((ch, i) => ({ ...ch, priority: i + 1 }));
    });
    markEdited();
  };

  const handleKeyChange = (chIdx: number, keyIdx: number, value: string) => {
    const ch = channels[chIdx];
    const keys = [...(ch.keys || [])];
    keys[keyIdx] = value;
    updateChannel(chIdx, { keys });
  };

  const addKey = (chIdx: number) => {
    const ch = channels[chIdx];
    updateChannel(chIdx, { keys: [...(ch.keys || []), ""] });
  };

  const removeKey = (chIdx: number, keyIdx: number) => {
    const ch = channels[chIdx];
    const keys = (ch.keys || []).filter((_: any, i: number) => i !== keyIdx);
    updateChannel(chIdx, { keys: keys.length ? keys : [""] });
  };

  const handleTest = async (idx: number) => {
    const ch = channels[idx];
    const chId = ch.id || `idx-${idx}`;
    setTesting((prev) => ({ ...prev, [chId]: "loading" }));
    try {
      const testKeys = (ch.keys || []).map((k: string) => k.trim()).filter(Boolean);
      const testKey = testKeys[0] || "";
      const payload: any = { ...ch, api_key: testKey };
      const res = await testChannel(section, payload);
      setTesting((prev) => ({ ...prev, [chId]: res.ok ? "ok" : "error" }));
      if (res.ok) {
        toast.success(res.message || `Dimensions: ${res.dimensions || "OK"}`);
      } else {
        toast.error(res.error || "Test failed");
      }
    } catch (e: any) {
      setTesting((prev) => ({ ...prev, [chId]: "error" }));
      toast.error(e.message);
    }
    setTimeout(() => setTesting((prev) => ({ ...prev, [chId]: null })), 3000);
  };

  const handleSave = async () => {
    if (loadError || saving) return;
    const saveGeneration = editGenerationRef.current;
    const snapshot = channels;
    setSaving(true);
    try {
      const cleanChannels = snapshot.map((ch, index) => {
        const cleaned = { ...ch };
        cleaned.keys = (Array.isArray(cleaned.keys) ? cleaned.keys : [])
          .filter((k: unknown): k is string => typeof k === "string")
          .map((k: string) => k.trim())
          .filter(Boolean);
        cleaned.priority = index + 1;
        if (cleaned.temperature !== "" && cleaned.temperature != null) {
          const temperature = Number(cleaned.temperature);
          cleaned.temperature = Number.isFinite(temperature) ? temperature : 0.7;
        }
        return cleaned;
      });
      await saveChannelSection(section, cleanChannels);
      // A future server snapshot must never overwrite edits made while the
      // request was in flight. (The fieldset is disabled for normal UI edits,
      // but this also protects against parent-driven updates and hot reloads.)
      if (editGenerationRef.current !== saveGeneration) return;
      toast.success("Channels saved");
      onDirty?.(false);
      await load();
    } catch (e: any) {
      toast.error("Save failed: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  const embeddingModel = section === "embedding" ? (channels.find((c) => c.api_model)?.api_model || "") : "";

  if (loading) {
    return <div className="p-6 text-center text-dim"><Loader2 className="animate-spin inline mr-2" size={16} />Loading channels...</div>;
  }

  if (loadError) {
    return (
      <div className="p-6 text-center text-dim text-sm">
        <p className="mb-3">Unable to load channel settings.</p>
        <Button variant="outline" size="sm" onClick={() => { setLoading(true); void load(); }}>
          <Loader2 size={13} className="mr-1" /> Retry
        </Button>
      </div>
    );
  }

  return (
    <fieldset disabled={saving} className="space-y-3 min-w-0">
      {channels.length === 0 && (
        <div className="text-center py-6 text-dim text-sm">
          No channels configured. Add one to get started.
        </div>
      )}

      {channels.map((ch, idx) => {
        const chId = ch.id || `idx-${idx}`;
        const health = healthMap[ch.id];
        const isUnhealthy = !!(health && (!health.healthy || (health.error_count ?? 0) > 0));
        const isLastEdited = lastEditedRef.current === ch.id;
        // Smart default-expand: single channel / unhealthy / recently edited
        const autoOpen = channels.length === 1 || isUnhealthy || isLastEdited;
        const isOpen = expanded[chId] ?? autoOpen;
        const testState = testing[chId];

        return (
          <Card
            key={chId}
            className={cn(
              "transition-all duration-300",
              !ch.enabled && "opacity-50",
              isUnhealthy && "border-orange/40 shadow-[0_0_0_1px_rgba(253,203,110,0.15)]"
            )}
          >
            <div
              className="flex items-center gap-3 px-4 py-3 cursor-pointer select-none hover:bg-muted/30"
              onClick={() => !saving && setExpanded((prev) => ({ ...prev, [chId]: !isOpen }))}
            >
              {isOpen ? <ChevronDown size={14} className="text-dim" /> : <ChevronRight size={14} className="text-dim" />}
              <HealthDot health={health} />
              <span className="font-medium text-sm flex-1">{ch.name || `Channel ${idx + 1}`}</span>
              {(ch.model || ch.api_model) && <Badge variant="secondary" className="text-[10px]">{ch.model || ch.api_model}</Badge>}
              <Badge variant="outline" className="text-[10px]">P{ch.priority}</Badge>
              <span className="text-[10px] text-dim">{(ch.keys || []).length} key{(ch.keys || []).length !== 1 ? "s" : ""}</span>
            </div>

            {isOpen && (
              <CardContent className="pt-0 pb-4 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs">Name</Label>
                    <Input value={ch.name} onChange={(e) => updateChannel(idx, { name: e.target.value })} placeholder="My Channel" className="h-8 text-sm" />
                  </div>
                  <div className="flex items-end gap-1">
                    <div className="flex-1">
                      <Label className="text-xs">Enabled</Label>
                      <Button
                        variant={ch.enabled ? "default" : "outline"}
                        size="sm"
                        className="w-full h-8 text-xs"
                        onClick={() => updateChannel(idx, { enabled: !ch.enabled })}
                      >
                        {ch.enabled ? "Enabled" : "Disabled"}
                      </Button>
                    </div>
                    <div className="flex gap-0.5">
                      <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={(e) => { e.stopPropagation(); movePriority(idx, -1); }} disabled={idx === 0}>
                        <ArrowUp size={14} />
                      </Button>
                      <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={(e) => { e.stopPropagation(); movePriority(idx, 1); }} disabled={idx === channels.length - 1}>
                        <ArrowDown size={14} />
                      </Button>
                    </div>
                  </div>
                </div>

                <div>
                  <Label className="text-xs">API Base URL</Label>
                  <Input value={ch.api_base || ""} onChange={(e) => updateChannel(idx, { api_base: e.target.value })} placeholder="https://api.openai.com/v1" className="h-8 text-sm" />
                </div>

                <div>
                  <Label className="text-xs">Proxy URL <span className="text-dim font-normal">(optional)</span></Label>
                  <Input value={ch.proxy || ""} onChange={(e) => updateChannel(idx, { proxy: e.target.value })} placeholder="http://127.0.0.1:7890 or http://user:pass@host:port" className="h-8 text-sm" />
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <Label className="text-xs flex items-center gap-1"><KeyRound size={12} /> API Keys</Label>
                    <Button variant="ghost" size="sm" className="h-6 text-xs px-2" onClick={() => addKey(idx)}>
                      <Plus size={12} className="mr-1" /> Add Key
                    </Button>
                  </div>
                  <div className="space-y-1.5">
                    {(ch.keys || [""]).map((key: string, ki: number) => (
                      <div key={ki} className="flex gap-1.5">
                        <div className="flex-1">
                          <SecretInput
                            value={key}
                            onChange={(v) => handleKeyChange(idx, ki, v)}
                            placeholder="sk-..."
                          />
                        </div>
                        {(ch.keys || []).length > 1 && (
                          <Button variant="ghost" size="sm" className="h-9 w-9 p-0 text-dim hover:text-red" onClick={() => removeKey(idx, ki)}>
                            <Trash2 size={14} />
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  {section === "embedding" ? (
                    <div>
                      <Label className="text-xs flex items-center gap-1">
                        Model
                        {idx > 0 && embeddingModel && <Lock size={10} className="text-dim" />}
                      </Label>
                      {idx === 0 ? (
                        <Input
                          value={ch.api_model || ""}
                          onChange={(e) => {
                            const model = e.target.value;
                            setChannels((prev) => prev.map((c) => ({
                              ...c,
                              api_model: model,
                            })));
                            markEdited();
                          }}
                          placeholder="Qwen3-Embedding-8B"
                          className="h-8 text-sm"
                        />
                      ) : (
                        <Input value={embeddingModel} disabled className="h-8 text-sm opacity-60" />
                      )}
                      {idx === 0 && channels.length > 1 && (
                        <p className="text-[10px] text-dim mt-1 flex items-center gap-1">
                          <AlertTriangle size={10} /> Model synced across all channels
                        </p>
                      )}
                    </div>
                  ) : section === "reranker" ? (
                    <div>
                      <Label className="text-xs">Model</Label>
                      <Input value={ch.api_model || ""} onChange={(e) => updateChannel(idx, { api_model: e.target.value })} placeholder="Qwen3-Reranker-8B" className="h-8 text-sm" />
                    </div>
                  ) : (
                    <div>
                      <Label className="text-xs">Model</Label>
                      <Input value={ch.model || ""} onChange={(e) => updateChannel(idx, { model: e.target.value })} placeholder="gpt-4o" className="h-8 text-sm" />
                    </div>
                  )}
                  {section === "llm" && (
                    <div>
                      <Label className="text-xs">Temperature</Label>
                      <Input type="number" min={0} max={2} step={0.1} value={ch.temperature ?? 0.7} onChange={(e) => updateChannel(idx, { temperature: e.target.value === "" ? "" : Number(e.target.value) })} className="h-8 text-sm" />
                    </div>
                  )}
                </div>

                {section === "llm" && (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <Label className="text-xs">Max Tokens</Label>
                      <Input type="number" min={256} step={256} value={ch.max_tokens ?? 32768} onChange={(e) => updateChannel(idx, { max_tokens: parseInt(e.target.value) || 32768 })} className="h-8 text-sm" />
                      <p className="text-[10px] text-dim mt-1">单次回复上限（非上下文窗口）；推理模型含思考 token，建议 ≥8192</p>
                    </div>
                    <div>
                      <Label className="text-xs">Context Window</Label>
                      <Input type="number" min={0} step={1000} value={ch.context_window ?? 0} placeholder="0 = 用全局默认" onChange={(e) => updateChannel(idx, { context_window: parseInt(e.target.value) || 0 })} className="h-8 text-sm" />
                      <p className="text-[10px] text-dim mt-1">输入上下文窗口（token）；0 = 用全局默认。决定出题等的 token 预算</p>
                    </div>
                    <div>
                      <Label className="text-xs">Timeout (秒)</Label>
                      <Input type="number" min={0} step={10} value={ch.timeout ?? 0} placeholder="默认 240" onChange={(e) => updateChannel(idx, { timeout: parseInt(e.target.value) || 0 })} className="h-8 text-sm" />
                      <p className="text-[10px] text-dim mt-1">读取超时；0 = 默认 240s</p>
                    </div>
                  </div>
                )}

                {section === "llm" && (
                  <div>
                    <Label className="text-xs flex items-center gap-1.5">
                      Reasoning Effort
                      <span className="text-dim font-normal">(thinking 模型生效)</span>
                      {ch.reasoning_effort && REASONING_EFFORT_HINT[ch.reasoning_effort] && (
                        <Badge variant="outline" className={cn("ml-auto text-[10px] font-mono", REASONING_EFFORT_HINT[ch.reasoning_effort].tone)}>
                          {REASONING_EFFORT_HINT[ch.reasoning_effort].latency}
                        </Badge>
                      )}
                    </Label>
                    <select
                      value={ch.reasoning_effort || ""}
                      onChange={(e) => updateChannel(idx, { reasoning_effort: e.target.value })}
                      className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 ring-offset-[color:var(--sig-bg)] transition-colors hover:border-primary/40"
                    >
                      <option value="">Off — 不发送 reasoning_effort 字段</option>
                      <option value="minimal">Minimal — 极轻思考</option>
                      <option value="low">Low — 浅度思考</option>
                      <option value="medium">Medium — 中度思考</option>
                      <option value="high">High — 深度思考</option>
                      <option value="xhigh">XHigh — 极深思考</option>
                    </select>
                    <p className="text-[10px] text-dim mt-1">仅对支持 reasoning 的上游模型生效（如 gpt-5 / o-series / DeepSeek-R1）</p>
                  </div>
                )}

                {section === "llm" && (
                  <div>
                    <Label className="text-xs flex items-center gap-1.5">
                      Tier
                      <span className="text-dim font-normal">(出题/评估分层路由)</span>
                    </Label>
                    <select
                      value={ch.tier || "large"}
                      onChange={(e) => updateChannel(idx, { tier: e.target.value })}
                      className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 ring-offset-[color:var(--sig-bg)] transition-colors hover:border-primary/40"
                    >
                      <option value="large">Large — 主模型 (出题 / 整体评估)</option>
                      <option value="small">Small — 便宜小模型 (per-question 并发评分)</option>
                    </select>
                    <p className="text-[10px] text-dim mt-1">标记为 small 的渠道仅用于并发 per-question 评估；未配置 small 时评估自动 fallback 到 large。</p>
                  </div>
                )}

                <div className="flex items-center justify-between pt-2 border-t border-border/50">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs text-red hover:bg-red/10"
                    onClick={() => removeChannel(idx)}
                  >
                    <Trash2 size={13} className="mr-1" /> Remove
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-xs h-7"
                    onClick={() => handleTest(idx)}
                    disabled={testState === "loading"}
                  >
                    {testState === "loading" ? <Loader2 size={13} className="mr-1 animate-spin" /> :
                     testState === "ok" ? <CheckCircle2 size={13} className="mr-1 text-green" /> :
                     testState === "error" ? <XCircle size={13} className="mr-1 text-red" /> :
                     <FlaskConical size={13} className="mr-1" />}
                    Test
                  </Button>
                </div>
              </CardContent>
            )}
          </Card>
        );
      })}

      <div className="flex items-center justify-between pt-2">
        <Button variant="outline" size="sm" className="text-xs" onClick={addChannel}>
          <Plus size={14} className="mr-1" /> Add Channel
        </Button>
        <Button size="sm" className="text-xs" onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 size={13} className="mr-1 animate-spin" /> : null}
          Save {section.toUpperCase()} Channels
        </Button>
      </div>
    </fieldset>
  );
}
