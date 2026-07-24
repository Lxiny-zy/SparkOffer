import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Bot, Database, Eye, EyeOff, Loader2,
  Save, User, Lock, Activity, ListOrdered, SlidersHorizontal,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  getMe, updateProfile, changePassword,
  getChannelsHealth,
} from "@/api/settings";
import { useAuth } from "@/contexts/AuthContext";
import ChannelManager from "@/components/ChannelManager";
import TuningSettings from "@/components/TuningSettings";
import AdminAuditPanel from "@/components/AdminAuditPanel";

// ─────────────────────────────────────────────────────────────
// 网关健康仪表盘 ── L1 hero
// ─────────────────────────────────────────────────────────────
interface SectionHealth { healthy: number; total: number; }
interface HealthSummary { llm: SectionHealth; embedding: SectionHealth; reranker: SectionHealth; }

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
  const s = summary || { llm: {healthy:0,total:0}, embedding: {healthy:0,total:0}, reranker: {healthy:0,total:0} };
  const allHealthy = s.llm.healthy === s.llm.total && s.embedding.healthy === s.embedding.total && s.reranker.healthy === s.reranker.total;
  const anyConfigured = s.llm.total + s.embedding.total + s.reranker.total > 0;
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
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5 md:gap-4">
          <HealthRing healthy={s.llm.healthy} total={s.llm.total} label="LLM"       color="var(--sig-chart-1)" icon={<Bot size={20} />} />
          <HealthRing healthy={s.embedding.healthy} total={s.embedding.total} label="Embedding" color="var(--sig-chart-2)"  icon={<Database size={20} />} />
          <HealthRing healthy={s.reranker.healthy} total={s.reranker.total} label="Reranker" color="var(--sig-chart-6)" icon={<ListOrdered size={20} />} />
        </div>
      </CardContent>
    </Card>
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
      await changePassword({ current_password: currentPassword, new_password: newPassword });
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
  const { user } = useAuth();
  const isOwner = !!user?.is_owner;
  const [healthSummary, setHealthSummary] = useState<HealthSummary | null>(null);
  const healthGenerationRef = useRef(0);

  const loadHealth = useCallback(async () => {
    const generation = ++healthGenerationRef.current;
    try {
      const data = await getChannelsHealth();
      if (generation !== healthGenerationRef.current) return;
      const summarize = (list: any[] | undefined): SectionHealth => {
        const arr = Array.isArray(list) ? list : [];
        const healthy = arr.filter((h) => h.healthy).length;
        return { healthy, total: arr.length };
      };
      setHealthSummary({
        llm: summarize(data.llm),
        embedding: summarize(data.embedding),
        reranker: summarize(data.reranker),
      });
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    if (!isOwner) return;
    const initialLoad = window.setTimeout(() => { void loadHealth(); }, 0);
    const onFocus = () => { void loadHealth(); };
    window.addEventListener("focus", onFocus);
    return () => {
      healthGenerationRef.current += 1;
      window.clearTimeout(initialLoad);
      window.removeEventListener("focus", onFocus);
    };
  }, [loadHealth, isOwner]);

  return (
    <div className="sig-page flex-1 overflow-y-auto min-h-0 w-full"><div className="max-w-3xl mx-auto p-4 md:p-8 space-y-6">
      <div className="space-y-1 animate-fade-in">
        <div className="sig-kicker mb-1">// 设置 / SETTINGS</div>
        <h1 className="sig-display text-2xl md:text-[28px]">设置<span className="sig-accent-c">.</span></h1>
        <p className="text-dim text-sm">
          {isOwner ? "Manage your account and AI provider configuration." : "Manage your account."}
        </p>
      </div>

      {isOwner && <HealthDashboard summary={healthSummary} />}

      <AccountSection />

      {isOwner && (<>
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

      <AdminAuditPanel />
      </>)}
    </div>
    </div>
  );
}
