import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Sun, Moon, ArrowRight, Brain, Target, Mic, BarChart3, Repeat, BookOpen, BriefcaseBusiness, Sparkles, Upload, MessageSquare, ChartLine, Zap, Shield, Code2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTilt } from "@/hooks/useTilt";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import CatAvatar from "@/components/CatAvatar";

const FEATURES = [
  {
    icon: Target,
    accent: "var(--aurora-1)",
    title: "简历模拟面试",
    desc: "AI 读取简历，模拟真实面试官，从自我介绍到项目深挖完整还原。",
  },
  {
    icon: BookOpen,
    accent: "var(--aurora-3)",
    title: "专项强化训练",
    desc: "选定领域集中突破，AI 动态调难度，精准定位薄弱点。",
  },
  {
    icon: Mic,
    accent: "var(--aurora-2)",
    title: "录音复盘",
    desc: "上传录音或文字稿，AI 自动转写分析，复盘每场真实面试。",
  },
  {
    icon: BriefcaseBusiness,
    accent: "var(--aurora-1)",
    title: "JD 定向备面",
    desc: "粘贴岗位 JD，AI 拆解考察重点，结合简历生成高概率追问。",
  },
];

const STEPS = [
  { icon: Upload, label: "上传简历", desc: "或粘贴 JD" },
  { icon: Brain, label: "AI 出题", desc: "RAG + 知识库定制" },
  { icon: ChartLine, label: "智能复盘", desc: "成长曲线可视化" },
];

const TRUST_STATS = [
  { value: "10K+", label: "题库覆盖" },
  { value: "6", label: "训练模式" },
  { value: "RAG", label: "知识增强" },
  { value: "LangGraph", label: "状态机驱动" },
];

const DEMO_LINES = [
  { role: "面试官", color: "var(--aurora-1)", text: "请介绍 RAG 架构中的 chunk 策略" },
  { role: "候选人", color: "var(--aurora-3)", text: "我们采用语义切分 + 重叠窗口..." },
  { role: "评估", color: "var(--aurora-2)", text: "8.2/10 — 思路清晰，建议补充延迟指标" },
];

export default function Landing() {
  const navigate = useNavigate();
  const heroTilt = useTilt();
  const [theme, setTheme] = useState<string>(() => {
    if (typeof window === "undefined") return "dark";
    return localStorage.getItem("theme") || "dark";
  });
  const [demoStep, setDemoStep] = useState(0);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => {
    const t = setInterval(() => {
      setDemoStep((s) => (s + 1) % (DEMO_LINES.length + 1));
    }, 2000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="min-h-screen bg-bg text-text relative overflow-hidden bg-noise">
      {/* Background layers */}
      <div className="fixed inset-0 bg-aurora pointer-events-none" />
      <div className="fixed inset-0 bg-grid pointer-events-none opacity-60" />
      {/* Floating particles */}
      <div className="particle" style={{ top: "15%", left: "10%", animationDelay: "0s" }} />
      <div className="particle" style={{ top: "30%", right: "15%", animationDelay: "3s" }} />
      <div className="particle" style={{ top: "60%", left: "20%", animationDelay: "6s" }} />
      <div className="particle" style={{ top: "75%", right: "25%", animationDelay: "9s" }} />
      <div className="particle" style={{ top: "45%", left: "50%", animationDelay: "12s" }} />

      {/* Header */}
      <header className="relative z-20 flex items-center justify-between px-6 md:px-10 py-5">
        <div className="flex items-center gap-3">
          <CatAvatar size={32} mood="curious" />
          <span className="text-lg font-display font-bold aurora-text">SparkOffer</span>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}>
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </Button>
          <Button variant="outline" onClick={() => navigate("/login")}>
            登录
          </Button>
        </div>
      </header>

      {/* Hero */}
      <section className="relative z-10 px-6 md:px-10 pt-8 md:pt-16 pb-16 md:pb-24">
        <div className="max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-12 lg:gap-16 items-center">
          {/* Left: Copy */}
          <div className="text-center lg:text-left">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full glass-subtle text-xs font-medium mb-6 animate-fade-in" style={{ color: "var(--aurora-2)" }}>
              <Sparkles size={14} className="animate-float" />
              AI-Powered Mock Interview Coach
            </div>

            <h1 className="text-4xl md:text-6xl font-display font-bold leading-[1.1] mb-5 animate-fade-in-up">
              <span className="aurora-text">越练越懂你</span>
              <br />
              <span className="text-text">的 AI 面试教练</span>
            </h1>

            <p className="text-base md:text-lg text-dim leading-relaxed mb-8 max-w-lg mx-auto lg:mx-0 animate-fade-in-up [animation-delay:0.1s]">
              基于 RAG + LangGraph 的状态机驱动训练系统。每一次练习都比上一次更精准，每一道题都源于你的真实薄弱点。
            </p>

            <div className="flex flex-col sm:flex-row items-center gap-3 justify-center lg:justify-start animate-fade-in-up [animation-delay:0.2s]">
              <Button variant="cta" size="xl" magnetic onClick={() => navigate("/login")} className="w-full sm:w-auto">
                立即开始训练
                <ArrowRight size={18} />
              </Button>
              <Button variant="outline" size="xl" onClick={() => document.getElementById("features")?.scrollIntoView({ behavior: "smooth" })} className="w-full sm:w-auto">
                查看功能
              </Button>
            </div>

            {/* Trust stats inline */}
            <div className="grid grid-cols-4 gap-3 md:gap-4 mt-10 max-w-md mx-auto lg:mx-0 animate-fade-in-up [animation-delay:0.3s]">
              {TRUST_STATS.map((s) => (
                <div key={s.label} className="text-center lg:text-left">
                  <div className="text-lg md:text-xl font-bold aurora-text">{s.value}</div>
                  <div className="text-[10px] md:text-xs text-dim mt-0.5">{s.label}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Right: Showcase card */}
          <div
            ref={heroTilt.ref}
            onMouseMove={heroTilt.onMouseMove}
            onMouseLeave={heroTilt.onMouseLeave}
            className="relative animate-fade-in-up [animation-delay:0.2s]"
          >
            {/* Glow halo behind — parallaxes opposite the card */}
            <div
              className="absolute inset-0 -z-10 blur-3xl opacity-50 transition-transform duration-200 ease-out"
              style={{
                background: "radial-gradient(circle at center, var(--aurora-2), transparent 70%)",
                transform: "translate(calc(var(--px, 0) * 34px), calc(var(--py, 0) * 34px))",
              }}
            />

            <Card
              variant="glass"
              hoverLift={false}
              className="overflow-hidden scan-line transition-transform duration-200 ease-out"
              style={{
                transform:
                  "perspective(900px) rotateX(calc(var(--py, 0) * -5deg)) rotateY(calc(var(--px, 0) * 5deg))",
              }}
            >
              {/* Tab bar */}
              <div className="flex items-center gap-2 px-4 py-3 border-b border-border/30">
                <div className="w-2.5 h-2.5 rounded-full bg-red/70" />
                <div className="w-2.5 h-2.5 rounded-full bg-orange/70" />
                <div className="w-2.5 h-2.5 rounded-full bg-green/70" />
                <span className="text-[11px] text-dim ml-3 font-mono">interview-session.live</span>
              </div>

              <CardContent className="px-5 py-5 md:px-6 md:py-6">
                {/* Cat hero */}
                <div className="flex items-center justify-center mb-5 relative">
                  <div className="absolute inset-0 flex items-center justify-center">
                    <div className="w-32 h-32 rounded-full opacity-40 blur-2xl" style={{ background: "var(--aurora-2)" }} />
                  </div>
                  <div className="relative animate-glow-breathe rounded-full">
                    <CatAvatar size={120} mood="happy" />
                  </div>
                </div>

                {/* Demo conversation */}
                <div className="space-y-2.5 font-mono text-[13px] min-h-[140px]">
                  {DEMO_LINES.map((line, i) => (
                    <div
                      key={i}
                      className={cn("flex gap-2 transition-all duration-500", demoStep > i ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2")}
                    >
                      <span className="font-semibold shrink-0" style={{ color: line.color }}>{line.role}</span>
                      <span className="text-dim">›</span>
                      <span className="text-text/90">{line.text}</span>
                    </div>
                  ))}
                  {demoStep > DEMO_LINES.length - 1 && (
                    <div className="text-text typing-cursor inline-block" />
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="relative z-10 px-6 md:px-10 py-16 md:py-20">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-12">
            <div className="inline-flex items-center gap-1.5 text-xs font-medium text-dim mb-3">
              <Zap size={14} style={{ color: "var(--aurora-2)" }} />
              核心能力
            </div>
            <h2 className="text-3xl md:text-4xl font-display font-bold mb-3">
              <span className="aurora-text">六大模块</span>
              <span className="text-text">，覆盖全场景</span>
            </h2>
            <p className="text-dim max-w-xl mx-auto">从简历导入到岗位匹配，从知识沉淀到回放复盘，一站式的 AI 面试训练体验。</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-5 stagger-children">
            {FEATURES.map((f) => {
              const Icon = f.icon;
              return (
                <Card key={f.title} variant="tech" className="card-hover-lift group relative overflow-hidden">
                  {/* Decorative corner SVG */}
                  <svg className="absolute top-3 right-3 opacity-20 pointer-events-none" width="40" height="40" viewBox="0 0 40 40" fill="none">
                    <circle cx="35" cy="5" r="1.5" fill={f.accent} />
                    <circle cx="30" cy="10" r="1" fill={f.accent} />
                    <circle cx="35" cy="15" r="1" fill={f.accent} />
                    <circle cx="25" cy="5" r="1" fill={f.accent} />
                  </svg>
                  <CardContent className="p-6 pt-7">
                    <div
                      className="w-11 h-11 rounded-2xl flex items-center justify-center mb-4 transition-transform duration-300 group-hover:scale-110"
                      style={{
                        background: `linear-gradient(135deg, color-mix(in srgb, ${f.accent} 25%, transparent), color-mix(in srgb, ${f.accent} 8%, transparent))`,
                        color: f.accent,
                        boxShadow: `0 0 24px color-mix(in srgb, ${f.accent} 20%, transparent)`,
                      }}
                    >
                      <Icon size={20} />
                    </div>
                    <h3 className="text-[15px] font-semibold text-text mb-2">{f.title}</h3>
                    <p className="text-sm text-dim leading-relaxed">{f.desc}</p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="relative z-10 px-6 md:px-10 py-16 md:py-20">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-12">
            <div className="inline-flex items-center gap-1.5 text-xs font-medium text-dim mb-3">
              <Repeat size={14} style={{ color: "var(--aurora-3)" }} />
              三步开练
            </div>
            <h2 className="text-3xl md:text-4xl font-display font-bold">
              <span className="text-text">从导入到复盘，</span>
              <span className="aurora-text">不到三分钟</span>
            </h2>
          </div>

          <div className="relative grid grid-cols-1 md:grid-cols-3 gap-6 md:gap-4">
            {/* Connection lines on desktop */}
            <div className="hidden md:block absolute top-12 left-[16%] right-[16%] h-px" style={{ background: "linear-gradient(90deg, transparent, var(--aurora-2), transparent)" }} />

            {STEPS.map((step, i) => {
              const Icon = step.icon;
              return (
                <div key={step.label} className="relative flex flex-col items-center text-center animate-fade-in-up" style={{ animationDelay: `${i * 0.15}s` }}>
                  <div className="relative w-24 h-24 rounded-full glass-strong flex items-center justify-center mb-4 tech-border">
                    <Icon size={32} style={{ color: "var(--aurora-2)" }} />
                    <div className="absolute -top-2 -right-2 w-7 h-7 rounded-full cta-gradient flex items-center justify-center text-xs font-bold">
                      {i + 1}
                    </div>
                  </div>
                  <div className="text-base font-semibold text-text mb-1">{step.label}</div>
                  <div className="text-sm text-dim">{step.desc}</div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* Tech pills */}
      <section className="relative z-10 px-6 md:px-10 py-12">
        <div className="max-w-4xl mx-auto">
          <div className="flex flex-wrap items-center justify-center gap-3">
            {[
              { icon: Brain, text: "语义记忆" },
              { icon: Repeat, text: "间隔重复" },
              { icon: BarChart3, text: "成长画像" },
              { icon: Shield, text: "本地优先" },
              { icon: Code2, text: "开源可控" },
            ].map((p) => {
              const I = p.icon;
              return (
                <div key={p.text} className="flex items-center gap-2 px-4 py-2 rounded-full glass-subtle text-sm text-dim hover:text-text hover:-translate-y-0.5 transition-all duration-300">
                  <I size={14} style={{ color: "var(--aurora-2)" }} />
                  {p.text}
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="relative z-10 px-6 md:px-10 py-16 md:py-24">
        <div className="max-w-4xl mx-auto">
          <Card variant="glass" className="relative overflow-hidden tech-border">
            <div className="absolute inset-0 opacity-50" style={{ background: "radial-gradient(circle at center, var(--aurora-2), transparent 70%)" }} />
            <CardContent className="relative px-8 py-12 md:px-12 md:py-16 text-center">
              <div className="inline-flex mb-6">
                <CatAvatar size={64} mood="happy" />
              </div>
              <h2 className="text-3xl md:text-4xl font-display font-bold mb-4">
                <span className="text-text">准备好成为下一个</span>
                <br className="md:hidden" />
                <span className="aurora-text">offer holder</span>
                <span className="text-text"> 了吗？</span>
              </h2>
              <p className="text-dim mb-8 max-w-md mx-auto">免费、无广告、本地优先。你的训练数据完全归你所有。</p>
              <Button variant="cta" size="xl" magnetic onClick={() => navigate("/login")}>
                免费开始
                <ArrowRight size={18} />
              </Button>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 px-6 md:px-10 py-8 text-center">
        <div className="section-divider mb-6" />
        <p className="text-xs text-dim">SparkOffer · Built with ❤️ + Claude · 2026</p>
      </footer>
    </div>
  );
}
