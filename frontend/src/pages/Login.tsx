import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { ArrowLeft, ArrowRight, BrainCircuit, Database, ShieldCheck } from "lucide-react";
import CatAvatar from "@/components/CatAvatar";
import GeometricNetwork from "@/components/GeometricNetwork";

export default function Login() {
  const [isRegister, setIsRegister] = useState(false);
  const [allowReg, setAllowReg] = useState<boolean | null>(null);
  const [inviteRequired, setInviteRequired] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    fetch("/api/auth/config")
      .then((r) => r.json())
      .then((d: any) => {
        setAllowReg(d.allow_registration);
        setInviteRequired(!!d.invite_required);
      })
      .catch(() => setAllowReg(false));
  }, []);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");

    if (isRegister && password.length < 6) {
      setError("密码至少 6 个字符");
      return;
    }
    if (isRegister && inviteRequired && !inviteCode.trim()) {
      setError("请输入邀请码");
      return;
    }

    setLoading(true);
    try {
      const endpoint = isRegister ? "/api/auth/register" : "/api/auth/login";
      const body = isRegister
        ? { email, password, name, invite_code: inviteCode.trim() }
        : { email, password };

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "操作失败");
      }

      const data = await res.json();
      login(data.token, data.user);
      navigate("/", { replace: true });
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="sig-root min-h-screen relative overflow-hidden flex flex-col">
      <div className="sig-grid fixed inset-0 pointer-events-none" aria-hidden />

      <div className="sig-hr" />

      {/* Header */}
      <header className="relative z-10 mx-auto w-full max-w-[1200px] px-6 md:px-8 h-16 flex items-center justify-between">
        <button onClick={() => navigate("/")} className="sig-navlink sig-mono text-[12.5px] uppercase tracking-[0.14em] flex items-center gap-2 group">
          <ArrowLeft size={14} className="transition-transform duration-300 group-hover:-translate-x-1" />
          返回首页
        </button>
        <div className="flex items-center gap-3">
          <CatAvatar size={22} mood="static" />
          <span className="sig-display text-[15px] tracking-[-0.03em]">SparkOffer</span>
        </div>
      </header>

      <div className="sig-hr" />

      {/* Editorial split: product thesis on the left, focused auth on the right */}
      <div className="relative z-10 flex-1 w-full max-w-[1440px] mx-auto grid lg:grid-cols-[minmax(0,1.25fr)_minmax(420px,0.75fr)]">
        <section className="sig-auth-manifesto hidden lg:flex relative min-h-[calc(100vh-130px)] overflow-hidden border-r" style={{ borderColor: "var(--sig-line)" }}>
          <GeometricNetwork />
          <div className="relative z-10 w-full flex flex-col justify-between p-10 xl:p-16">
            <div>
              <div className="sig-kicker">// CONTINUOUS INTERVIEW INTELLIGENCE</div>
              <h2 className="sig-display mt-8 text-[clamp(4rem,7vw,7.5rem)] leading-[0.82]">
                ONE<br />SESSION<br /><span className="sig-accent-c">AHEAD.</span>
              </h2>
              <p className="mt-8 max-w-xl text-[15px] leading-relaxed text-[color:var(--sig-dim)]">
                不是清空上下文后重新开始。训练、评估、画像与复习调度组成同一个持续演进的系统。
              </p>
            </div>

            <div className="grid grid-cols-3 border" style={{ borderColor: "var(--sig-line)" }}>
              {[
                [BrainCircuit, "MEMORY", "跨会话画像"],
                [Database, "CONTEXT", "知识库检索"],
                [ShieldCheck, "PRIVATE", "数据归你"],
              ].map(([Icon, label, detail]) => {
                const FeatureIcon = Icon as typeof BrainCircuit;
                return (
                  <div key={String(label)} className="p-4 border-r last:border-r-0" style={{ borderColor: "var(--sig-line)" }}>
                    <FeatureIcon size={16} className="text-[color:var(--sig-accent)]" />
                    <div className="sig-kicker mt-4">{String(label)}</div>
                    <div className="mt-1 text-[11px] text-[color:var(--sig-dim)]">{String(detail)}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        <section className="flex items-center justify-center px-6 py-14 md:py-20 lg:px-10">
        <div className="w-full max-w-[420px]">
          <div className="sig-kicker">// {isRegister ? "创建账号" : "登录"}</div>
          <h1 className="sig-display mt-4 text-[clamp(2rem,6vw,3.25rem)]">
            {isRegister ? "开始训练" : "欢迎回来"}<span className="sig-accent-c">.</span>
          </h1>
          <p className="mt-3 text-[13.5px] text-[color:var(--sig-dim)]">
            {isRegister ? "创建账号，开启你的自适应面试训练。" : "继续你的训练 — 进度与画像已为你保留。"}
          </p>

          <div className="sig-card sig-auth-card mt-8 p-6 md:p-7">
            <form onSubmit={handleSubmit} className="space-y-5">
              {isRegister && (
                <div className="space-y-2">
                  <label htmlFor="login-name" className="sig-kicker block">昵称 / Name</label>
                  <input
                    id="login-name"
                    type="text" className="sig-field" placeholder="你的称呼（选填）"
                    value={name} onChange={(e) => setName(e.target.value)}
                  />
                </div>
              )}

              <div className="space-y-2">
                <label htmlFor="login-email" className="sig-kicker block">邮箱 / Email</label>
                <input
                  id="login-email"
                  type="email" className="sig-field" placeholder="your@email.com"
                  value={email} onChange={(e) => setEmail(e.target.value)} required
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="login-password" className="sig-kicker block">密码 / Password</label>
                <input
                  id="login-password"
                  type="password" className="sig-field"
                  placeholder={isRegister ? "至少 6 个字符" : "输入密码"}
                  value={password} onChange={(e) => setPassword(e.target.value)} required
                />
              </div>

              {isRegister && inviteRequired && (
                <div className="space-y-2">
                  <label htmlFor="login-invite" className="sig-kicker block">邀请码 / Invite Code</label>
                  <input
                    id="login-invite"
                    type="text" className="sig-field" placeholder="向管理员索取"
                    value={inviteCode} onChange={(e) => setInviteCode(e.target.value)} required
                  />
                </div>
              )}

              {error && (
                <div
                  className="sig-mono text-[12.5px] rounded border px-3 py-2.5"
                  style={{
                    borderColor: "color-mix(in srgb, var(--sig-danger) 45%, transparent)",
                    color: "var(--sig-danger)",
                    background: "color-mix(in srgb, var(--sig-danger) 8%, transparent)",
                  }}
                >
                  {error}
                </div>
              )}

              <button type="submit" className="sig-cta w-full justify-center mt-1" disabled={loading}>
                {loading ? "处理中…" : isRegister ? "立即注册" : "登录"}
                {!loading && <ArrowRight size={16} />}
              </button>
            </form>

            {allowReg && (
              <div className="mt-6 pt-5">
                <div className="sig-hr mb-4" />
                <div className="text-center">
                  <span className="sig-kicker">{isRegister ? "已有账号？" : "还没有账号？"}</span>
                  <button
                    onClick={() => { setIsRegister(!isRegister); setError(""); }}
                    className="sig-navlink sig-mono text-[12px] uppercase tracking-[0.14em] ml-2 sig-accent-c"
                  >
                    {isRegister ? "去登录" : "立即注册"}
                  </button>
                </div>
              </div>
            )}
          </div>

          <p className="sig-kicker mt-6 text-center">本地优先 · 数据归你 · Built with Claude</p>
        </div>
        </section>
      </div>
    </div>
  );
}
