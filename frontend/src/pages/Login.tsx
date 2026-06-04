import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { ArrowLeft, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { useTilt } from "@/hooks/useTilt";
import CatAvatar from "@/components/CatAvatar";

export default function Login() {
  const [isRegister, setIsRegister] = useState(false);
  const [allowReg, setAllowReg] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const cardTilt = useTilt();

  useEffect(() => {
    fetch("/api/auth/config")
      .then((r) => r.json())
      .then((d: any) => setAllowReg(d.allow_registration))
      .catch(() => setAllowReg(false));
  }, []);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");

    if (isRegister && password.length < 6) {
      setError("密码至少 6 个字符");
      return;
    }

    setLoading(true);
    try {
      const endpoint = isRegister ? "/api/auth/register" : "/api/auth/login";
      const body = isRegister ? { email, password, name } : { email, password };

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
    <div className="min-h-screen bg-bg text-text relative overflow-hidden bg-noise flex items-center justify-center px-4">
      {/* Background layers */}
      <div className="fixed inset-0 bg-aurora pointer-events-none" />
      <div className="fixed inset-0 bg-grid pointer-events-none opacity-50" />
      <div className="particle" style={{ top: "20%", left: "15%", animationDelay: "0s" }} />
      <div className="particle" style={{ top: "70%", right: "20%", animationDelay: "5s" }} />
      <div className="particle" style={{ top: "40%", right: "10%", animationDelay: "10s" }} />

      <div className="w-full max-w-md relative z-10">
        <button
          onClick={() => navigate("/")}
          className="flex items-center gap-1.5 text-sm text-dim hover:text-text transition-colors mb-6 cursor-pointer group"
        >
          <ArrowLeft size={16} className="transition-transform duration-300 group-hover:-translate-x-1" />
          返回首页
        </button>

        <div
          ref={cardTilt.ref}
          onMouseMove={cardTilt.onMouseMove}
          onMouseLeave={cardTilt.onMouseLeave}
          className="animate-scale-in"
        >
          <Card
            variant="glass"
            className="relative overflow-hidden tech-border transition-transform duration-200 ease-out"
            style={{
              transform:
                "perspective(900px) rotateX(calc(var(--py, 0) * -4deg)) rotateY(calc(var(--px, 0) * 4deg))",
            }}
          >
            {/* Decorative glow halo — parallaxes for depth */}
            <div
              className="absolute -top-20 -right-20 w-48 h-48 rounded-full opacity-40 blur-3xl pointer-events-none transition-transform duration-200 ease-out"
              style={{
                background: "var(--aurora-2)",
                transform: "translate(calc(var(--px, 0) * -28px), calc(var(--py, 0) * -28px))",
              }}
            />

          <CardHeader className="relative items-center text-center pb-2">
            <div className="relative mb-2">
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-20 h-20 rounded-full opacity-40 blur-xl" style={{ background: "var(--aurora-2)" }} />
              </div>
              <div className="relative animate-glow-breathe rounded-full">
                <CatAvatar size={64} mood="happy" />
              </div>
            </div>

            <div className="inline-flex items-center gap-1.5 text-xs text-dim mt-2">
              <Sparkles size={12} style={{ color: "var(--aurora-2)" }} />
              {isRegister ? "开启你的训练之旅" : "继续你的训练"}
            </div>

            <h1 className="text-2xl md:text-3xl font-display font-bold mt-2">
              <span className="aurora-text">{isRegister ? "创建账号" : "欢迎回来"}</span>
            </h1>
          </CardHeader>

          <CardContent className="relative pt-4">
            <form onSubmit={handleSubmit} className="space-y-4">
              {isRegister && (
                <div className="space-y-1.5 animate-fade-in">
                  <Label>昵称</Label>
                  <Input type="text" placeholder="你的称呼（选填）" value={name} onChange={(e) => setName(e.target.value)} />
                </div>
              )}

              <div className="space-y-1.5">
                <Label>邮箱</Label>
                <Input type="email" placeholder="your@email.com" value={email} onChange={(e) => setEmail(e.target.value)} required />
              </div>

              <div className="space-y-1.5">
                <Label>密码</Label>
                <Input type="password" placeholder={isRegister ? "至少 6 个字符" : "输入密码"} value={password} onChange={(e) => setPassword(e.target.value)} required />
              </div>

              {error && (
                <div className="px-3 py-2.5 rounded-2xl bg-red/10 border border-red/30 text-red text-sm animate-fade-in shadow-[0_0_16px_rgba(179,38,30,0.15)]">
                  {error}
                </div>
              )}

              <Button type="submit" variant="cta" size="lg" className="w-full mt-2" disabled={loading}>
                {loading ? "处理中..." : isRegister ? "立即注册" : "登录"}
              </Button>
            </form>

            {allowReg && (
              <div className="mt-6 pt-5 text-center">
                <div className="section-divider mb-4" />
                <span className="text-sm text-dim">
                  {isRegister ? "已有账号？" : "还没有账号？"}
                </span>
                <button
                  onClick={() => { setIsRegister(!isRegister); setError(""); }}
                  className="text-sm font-medium ml-1.5 hover:underline cursor-pointer aurora-text"
                >
                  {isRegister ? "去登录" : "立即注册"}
                </button>
              </div>
            )}
          </CardContent>
        </Card>
        </div>

        <p className="text-center text-xs text-dim mt-6">
          由 ❤️ + Claude 驱动 · 本地优先 · 数据归你
        </p>
      </div>
    </div>
  );
}
