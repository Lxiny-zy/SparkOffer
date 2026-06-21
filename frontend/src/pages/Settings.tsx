import React, { useState, useEffect, useCallback } from "react";
import {
  Bot, Database, Mic, Cloud, Eye, EyeOff, Loader2,
  CheckCircle2, XCircle, Save, RotateCcw, User, Lock, Activity, ListOrdered, SlidersHorizontal,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  getAIConfig, saveAIConfig, testQiniu,
  getMe, updateProfile, changePassword,
  getChannelsHealth,
} from "@/api/settings";
import { useAuth } from "@/contexts/AuthContext";
import ChannelManager from "@/components/ChannelManager";
import TuningSettings from "@/components/TuningSettings";

// ─────────────────────────────────────────────────────────────
// 网关健康仪表盘 ── L1 hero
// ─────────────────────────────────────────────────────────────
interface SectionHealth { healthy: number; total: number; }
interface HealthSummary { llm: SectionHealth; embedding: SectionHealth; asr: SectionHealth; reranker: SectionHealth; }

function HealthRing({ healthy, total, label, color, icon }: {
  healthy: number; total: number; label: string; color: string; icon: React.ReactNode;
}) {
  const radius = 26;
  const c = 2 * Math.PI * radius;
  const pct = total > 0 ? healthy / total : 0;
  const offset = c * (1 - pct);
  const empty = total === 0;
  return (
    <div className="flex items-center gap-3 group">
      <div className="relative w-[68px] h-[68px] shrink-0">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 64 64">
          <circle cx="32" cy="32" r={radius} stroke="var(--border)" strokeWidth="5" fill="none" />
          {!empty && (
            <circle
              cx="32" cy="32" r={radius}
              stroke={color} strokeWidth="5" fill="none"
              strokeDasharray={c}
              strokeDashoffset={offset}
              strokeLinecap="round"
              className="transition-all duration-1000 ease-out"
            />
          )}
        </svg>
        <div className="absolute inset-0 flex items-center justify-center text-[var(--color)]"
             style={{ color }}>
          <div className="transition-transform duration-300 group-hover:scale-110">{icon}</div>
        </div>
      </div>
      <div className="min-w-0">
        <div className="sig-kicker mb-0.5">{label}</div>
        <div className="flex items-baseline gap-1">
          <span className="text-2xl sig-stat" style={{ color: empty ? "var(--muted-fg)" : color }}>
            {empty ? "—" : healthy}
          </span>
          {!empty && <span className="text-sm text-dim">/ {total}</span>}
        </div>
        <div className="text-[11px] text-dim">{empty ? "未配置" : `${healthy === total ? "全部健康" : `${total - healthy} 个异常`}`}</div>
      </div>
    </div>
  );
}

function HealthDashboard({ summary }: { summary: HealthSummary | null }) {
  const s = summary || { llm: {healthy:0,total:0}, embedding: {healthy:0,total:0}, asr: {healthy:0,total:0}, reranker: {healthy:0,total:0} };
  const allHealthy = s.llm.healthy === s.llm.total && s.embedding.healthy === s.embedding.total && s.asr.healthy === s.asr.total && s.reranker.healthy === s.reranker.total;
  const anyConfigured = s.llm.total + s.embedding.total + s.asr.total + s.reranker.total > 0;
  return (
    <Card hoverLift className="mb-6 relative overflow-hidden animate-fade-in-up">
      <CardContent className="p-5 md:p-6 relative">
        <div className="flex items-center gap-2 mb-4">
          <Activity size={16} className="text-primary" />
          <span className="sig-kicker">Gateway Health</span>
          {anyConfigured && (
            <span className="ml-auto text-[11px] font-medium px-2 py-0.5 rounded" style={allHealthy ? { background: "color-mix(in srgb, var(--sig-success) 15%, transparent)", color: "var(--sig-success)" } : { background: "color-mix(in srgb, var(--sig-warning) 15%, transparent)", color: "var(--sig-warning)" }}>
              {allHealthy ? "● 全部健康" : "● 部分异常"}
            </span>
          )}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5 md:gap-4">
          <HealthRing healthy={s.llm.healthy} total={s.llm.total} label="LLM"       color="var(--sig-chart-1)" icon={<Bot size={20} />} />
          <HealthRing healthy={s.embedding.healthy} total={s.embedding.total} label="Embedding" color="var(--sig-chart-2)"  icon={<Database size={20} />} />
          <HealthRing healthy={s.asr.healthy} total={s.asr.total} label="ASR"       color="var(--sig-chart-3)"    icon={<Mic size={20} />} />
          <HealthRing healthy={s.reranker.healthy} total={s.reranker.total} label="Reranker" color="var(--sig-chart-6)" icon={<ListOrdered size={20} />} />
        </div>
      </CardContent>
    </Card>
  );
}

const SOURCE_LABELS: Record<string, string> = { json: "JSON", env: "ENV", default: "Default" };
const SOURCE_COLORS: Record<string, string> = {
  json: "bg-primary/15 text-primary",
  env: "bg-orange/15 text-orange",
  default: "bg-dim/15 text-dim",
};

function SourceBadge({ source }: { source?: string }) {
  if (!source) return null;
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${SOURCE_COLORS[source] || SOURCE_COLORS.default}`}>
      {SOURCE_LABELS[source] || source}
    </span>
  );
}

interface SecretInputProps {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  placeholder?: string;
  id?: string;
}

function SecretInput({ value, onChange, placeholder, id }: SecretInputProps) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        type={visible ? "text" : "password"}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="pr-10"
      />
      <button
        type="button"
        onClick={() => setVisible(!visible)}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-dim hover:text-text transition-colors"
      >
        {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      </button>
    </div>
  );
}

function TestButton({ onClick, status }: { onClick: () => void; status?: string }) {
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={onClick}
      disabled={status === "testing"}
      className="gap-1.5"
    >
      {status === "testing" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
      {status === "success" && <CheckCircle2 className="w-3.5 h-3.5 text-green" />}
      {status === "error" && <XCircle className="w-3.5 h-3.5 text-destructive" />}
      {!status && <RotateCcw className="w-3.5 h-3.5" />}
      {status === "testing" ? "Testing..." : "Test"}
    </Button>
  );
}

function ConfigField({ label, source, children }: { label: string; source?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Label className="text-sm font-medium">{label}</Label>
        <SourceBadge source={source} />
      </div>
      {children}
    </div>
  );
}

function AccountSection() {
  const { user, updateUser } = useAuth();
  const [name, setName] = useState(user?.name || "");
  const [email, setEmail] = useState(user?.email || "");
  const [savingProfile, setSavingProfile] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);

  useEffect(() => {
    getMe().then((data: any) => {
      setName(data.name || "");
      setEmail(data.email || "");
    }).catch(() => {});
  }, []);

  const handleSaveProfile = async () => {
    setSavingProfile(true);
    try {
      const result = await updateProfile({ name, email });
      updateUser(result.user);
      toast.success("Profile updated");
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSavingProfile(false);
    }
  };

  const handleChangePassword = async () => {
    if (newPassword.length < 6) {
      toast.error("Password must be at least 6 characters");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error("Passwords do not match");
      return;
    }
    setSavingPassword(true);
    try {
      await changePassword({ old_password: currentPassword, new_password: newPassword });
      toast.success("Password changed successfully");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSavingPassword(false);
    }
  };

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-secondary/20 flex items-center justify-center">
              <User className="w-5 h-5 text-secondary" />
            </div>
            <div>
              <CardTitle className="text-base">Account</CardTitle>
              <CardDescription>Your profile information</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-sm font-medium">Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm font-medium">Email</Label>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </div>
          <div className="flex justify-end">
            <Button onClick={handleSaveProfile} disabled={savingProfile} size="sm" className="gap-2">
              {savingProfile ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              Save Profile
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-orange/10 flex items-center justify-center">
              <Lock className="w-5 h-5 text-orange" />
            </div>
            <div>
              <CardTitle className="text-base">Change Password</CardTitle>
              <CardDescription>Update your login password</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-sm font-medium">Current Password</Label>
            <SecretInput value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} placeholder="Enter current password" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm font-medium">New Password</Label>
            <SecretInput value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="At least 6 characters" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm font-medium">Confirm New Password</Label>
            <SecretInput value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Repeat new password" />
          </div>
          <div className="flex justify-end">
            <Button onClick={handleChangePassword} disabled={savingPassword} size="sm" variant="outline" className="gap-2">
              {savingPassword ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Lock className="w-3.5 h-3.5" />}
              Change Password
            </Button>
          </div>
        </CardContent>
      </Card>

      <Separator />
    </>
  );
}

export default function Settings() {
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [healthSummary, setHealthSummary] = useState<HealthSummary | null>(null);

  const [qiniu, setQiniu] = useState({ access_key: "", secret_key: "", bucket: "", domain: "" });
  const [testStatus, setTestStatus] = useState<Record<string, string>>({});
  const [testMsg, setTestMsg] = useState<Record<string, string>>({});

  const loadHealth = useCallback(async () => {
    try {
      const data = await getChannelsHealth();
      const summarize = (list: any[] | undefined): SectionHealth => {
        const arr = Array.isArray(list) ? list : [];
        const healthy = arr.filter((h) => h.healthy).length;
        return { healthy, total: arr.length };
      };
      setHealthSummary({
        llm: summarize(data.llm),
        embedding: summarize(data.embedding),
        asr: summarize(data.asr),
        reranker: summarize(data.reranker),
      });
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    loadHealth();
    const onFocus = () => loadHealth();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [loadHealth]);

  const loadConfig = useCallback(async () => {
    try {
      const data = await getAIConfig();
      setConfig(data);
      const extract = (section: string) => {
        const result: Record<string, any> = {};
        for (const [key, info] of Object.entries(data[section] || {})) {
          result[key] = (info as any).value ?? "";
        }
        return result;
      };
      setQiniu((prev) => ({ ...prev, ...extract("qiniu") }));
    } catch (e: any) {
      toast.error("Failed to load config: " + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const handleSecretChange = (section: string, key: string, value: string) => {
    if (section === "qiniu") setQiniu((prev) => ({ ...prev, [key]: value }));
  };

  const handleChange = (section: string, key: string, value: any) => {
    if (section === "qiniu") setQiniu((prev) => ({ ...prev, [key]: value }));
  };

  const handleSaveQiniu = async () => {
    setSaving(true);
    try {
      const clean = (data: Record<string, any>) => {
        const result: Record<string, any> = {};
        for (const [key, value] of Object.entries(data)) {
          result[key] = value === "" || value === undefined ? "" : value;
        }
        return Object.keys(result).length ? result : undefined;
      };
      await saveAIConfig({ qiniu: clean(qiniu) });
      toast.success("Qiniu configuration saved");
      await loadConfig();
    } catch (e: any) {
      toast.error("Save failed: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (section: string, fn: (params: any) => Promise<any>, params: any) => {
    setTestStatus((prev) => ({ ...prev, [section]: "testing" }));
    setTestMsg((prev) => ({ ...prev, [section]: "" }));
    try {
      const result = await fn(params);
      if (result.ok) {
        setTestStatus((prev) => ({ ...prev, [section]: "success" }));
        setTestMsg((prev) => ({ ...prev, [section]: result.message || `OK` }));
        toast.success(section.toUpperCase() + " connection OK");
      } else {
        setTestStatus((prev) => ({ ...prev, [section]: "error" }));
        setTestMsg((prev) => ({ ...prev, [section]: result.error }));
        toast.error(result.error);
      }
    } catch (e: any) {
      setTestStatus((prev) => ({ ...prev, [section]: "error" }));
      setTestMsg((prev) => ({ ...prev, [section]: e.message }));
      toast.error(e.message);
    }
  };

  const getTestValue = (_section: string, _key: string, formValue: string) => {
    return formValue;
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: "var(--sig-accent)" }} />
      </div>
    );
  }

  const getSource = (section: string, key: string) => config?.[section]?.[key]?.source;

  return (
    <div className="flex-1 overflow-y-auto min-h-0 w-full"><div className="max-w-3xl mx-auto p-4 md:p-8 space-y-6">
      <div className="space-y-1 animate-fade-in">
        <div className="sig-kicker mb-1">// 设置 / SETTINGS</div>
        <h1 className="sig-display text-2xl md:text-[28px]">设置<span className="sig-accent-c">.</span></h1>
        <p className="text-dim text-sm">
          Manage your account and AI provider configuration.
        </p>
      </div>

      <HealthDashboard summary={healthSummary} />

      <AccountSection />

      {/* LLM Channels */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center">
              <Bot className="w-5 h-5 text-primary" />
            </div>
            <div>
              <CardTitle className="text-base">LLM Channels</CardTitle>
              <CardDescription>Multi-channel chat model with auto-failover</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ChannelManager section="llm" />
        </CardContent>
      </Card>

      {/* Embedding Channels */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-tertiary/10 flex items-center justify-center">
              <Database className="w-5 h-5 text-tertiary" />
            </div>
            <div>
              <CardTitle className="text-base">Embedding Channels</CardTitle>
              <CardDescription>Vector model — all channels must use the same model</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ChannelManager section="embedding" />
        </CardContent>
      </Card>

      {/* ASR Channels */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-green/10 flex items-center justify-center">
              <Mic className="w-5 h-5 text-green" />
            </div>
            <div>
              <CardTitle className="text-base">ASR Channels</CardTitle>
              <CardDescription>DashScope speech-to-text</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ChannelManager section="asr" />
        </CardContent>
      </Card>

      {/* Reranker Channels */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-secondary/10 flex items-center justify-center">
              <ListOrdered className="w-5 h-5 text-secondary" />
            </div>
            <div>
              <CardTitle className="text-base">Reranker Channels</CardTitle>
              <CardDescription>Cross-Encoder re-ranking — Cohere-compatible /rerank (optional)</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ChannelManager section="reranker" />
        </CardContent>
      </Card>

      {/* 输出 & 检索调参 */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center">
              <SlidersHorizontal className="w-5 h-5 text-primary" />
            </div>
            <div>
              <CardTitle className="text-base">输出 & 检索调参</CardTitle>
              <CardDescription>输出上限 / 窗口兜底与 RAG 检索档位（保存即全局生效，留空回退默认）</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <TuningSettings />
        </CardContent>
      </Card>

      {/* Qiniu OSS Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-secondary/20 flex items-center justify-center">
                <Cloud className="w-5 h-5 text-secondary" />
              </div>
              <div>
                <CardTitle className="text-base">Qiniu OSS</CardTitle>
                <CardDescription>Audio file storage for ASR</CardDescription>
              </div>
            </div>
            <TestButton
              status={testStatus.qiniu}
              onClick={() => runTest("qiniu", testQiniu, {
                access_key: getTestValue("qiniu", "access_key", qiniu.access_key),
                secret_key: getTestValue("qiniu", "secret_key", qiniu.secret_key),
                bucket: qiniu.bucket,
                domain: qiniu.domain,
              })}
            />
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <ConfigField label="Access Key" source={getSource("qiniu", "access_key")}>
              <SecretInput
                value={qiniu.access_key}
                onChange={(e) => handleSecretChange("qiniu", "access_key", e.target.value)}
                placeholder="Access Key"
              />
            </ConfigField>
            <ConfigField label="Secret Key" source={getSource("qiniu", "secret_key")}>
              <SecretInput
                value={qiniu.secret_key}
                onChange={(e) => handleSecretChange("qiniu", "secret_key", e.target.value)}
                placeholder="Secret Key"
              />
            </ConfigField>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <ConfigField label="Bucket" source={getSource("qiniu", "bucket")}>
              <Input
                value={qiniu.bucket}
                onChange={(e) => handleChange("qiniu", "bucket", e.target.value)}
                placeholder="my-bucket"
              />
            </ConfigField>
            <ConfigField label="Domain" source={getSource("qiniu", "domain")}>
              <Input
                value={qiniu.domain}
                onChange={(e) => handleChange("qiniu", "domain", e.target.value)}
                placeholder="https://cdn.example.com"
              />
            </ConfigField>
          </div>
          {testMsg.qiniu && (
            <div className="text-xs px-3 py-2 rounded-md" style={testStatus.qiniu === "success" ? { background: "color-mix(in srgb, var(--sig-success) 10%, transparent)", color: "var(--sig-success)" } : { background: "color-mix(in srgb, var(--sig-danger) 10%, transparent)", color: "var(--sig-danger)" }}>
              {testMsg.qiniu}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Save Qiniu */}
      <div className="flex justify-end pb-8">
        <Button onClick={handleSaveQiniu} disabled={saving} className="gap-2 px-8">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save Qiniu"}
        </Button>
      </div>
    </div>
    </div>
  );
}
