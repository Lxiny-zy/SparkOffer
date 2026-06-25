import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Sun, Moon, ArrowRight, ArrowDown, Target, BookOpen,
  BriefcaseBusiness, Upload, Brain, ChartLine,
} from "lucide-react";
import CatAvatar from "@/components/CatAvatar";

const FEATURES = [
  { icon: Target, title: "简历模拟面试", desc: "AI 读取简历，模拟真实面试官，从自我介绍到项目深挖逐层追问。" },
  { icon: BookOpen, title: "专项强化训练", desc: "选定领域集中突破，AI 结合你的能力画像动态调难度，精准命中薄弱点。" },
  { icon: BriefcaseBusiness, title: "JD 定向备面", desc: "粘贴岗位 JD，AI 拆解考察重点，结合简历与知识库生成高概率追问。" },
];

const STEPS = [
  { icon: Upload, label: "上传简历", desc: "或粘贴目标岗位 JD" },
  { icon: Brain, label: "AI 出题", desc: "RAG + 知识库定制题目" },
  { icon: ChartLine, label: "画像进化", desc: "成长曲线 + 薄弱点持续追踪" },
];

const SPECS = [
  { k: "知识增强", v: "RAG" },
  { k: "长期画像", v: "Mem0" },
  { k: "复习调度", v: "SM-2" },
  { k: "状态机", v: "LangGraph" },
];

const TICKER = [
  "RAG 检索增强", "LangGraph 状态机", "LlamaIndex", "SM-2 间隔重复",
  "Mem0 画像", "向量召回", "重排序", "本地优先",
];

export default function Landing() {
  const navigate = useNavigate();
  const [theme, setTheme] = useState<string>(() => {
    if (typeof window === "undefined") return "dark";
    return localStorage.getItem("theme") || "dark";
  });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
  }, [theme]);

  return (
    <div className="sig-root min-h-screen relative overflow-hidden">
      {/* Blueprint grid */}
      <div className="sig-grid fixed inset-0 pointer-events-none" aria-hidden />

      <div className="sig-hr" />

      {/* Header */}
      <header className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 h-16 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <CatAvatar size={24} mood="static" />
          <span className="sig-display text-[17px] tracking-[-0.03em]">SparkOffer</span>
          <span className="hidden sm:block sig-vr h-4" />
          <span className="hidden sm:block sig-kicker">AI Mock Interview</span>
        </div>
        <div className="flex items-center gap-4">
          <button onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))} className="sig-iconbtn" aria-label="toggle theme">
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          <button onClick={() => navigate("/login")} className="sig-navlink sig-mono text-[12.5px] uppercase tracking-[0.14em]">
            登录
          </button>
        </div>
      </header>

      <div className="sig-hr" />

      {/* Hero */}
      <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 pt-16 md:pt-28 pb-16 md:pb-24">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 lg:gap-8">
          {/* Copy */}
          <div className="lg:col-span-8">
            <div className="sig-kicker sig-rise" style={{ animationDelay: "0.05s" }}>
              // 自适应面试训练系统
            </div>
            <h1 className="sig-display mt-6 text-[clamp(2.75rem,8.5vw,6.5rem)]">
              <span className="sig-linemask"><span style={{ animationDelay: "0.05s" }}>越练越懂你的</span></span>
              <span className="sig-linemask"><span style={{ animationDelay: "0.18s" }}>AI 面试<span className="sig-accent-c">教练</span></span></span>
            </h1>
            <p className="sig-rise mt-8 max-w-xl text-[15px] md:text-base leading-relaxed text-[color:var(--sig-dim)]" style={{ animationDelay: "0.35s" }}>
              基于 RAG 知识库与自我进化画像的自适应训练系统。每一次练习都在重写你的能力画像——下一道题，都来自你真实的薄弱点。
            </p>
            <div className="sig-rise mt-10 flex flex-col sm:flex-row items-stretch sm:items-center gap-3" style={{ animationDelay: "0.45s" }}>
              <button onClick={() => navigate("/login")} className="sig-cta justify-center">
                立即开始训练 <ArrowRight size={16} />
              </button>
              <button
                onClick={() => document.getElementById("capabilities")?.scrollIntoView({ behavior: "smooth" })}
                className="sig-cta-ghost justify-center"
              >
                查看能力 <ArrowDown size={15} />
              </button>
            </div>
          </div>

          {/* Spec sheet */}
          <div className="lg:col-span-4 sig-rise" style={{ animationDelay: "0.55s" }}>
            <div className="sig-kicker mb-4">规格 / SPEC</div>
            <div className="sig-hr" />
            {SPECS.map((s) => (
              <div key={s.k}>
                <div className="flex items-baseline justify-between py-4">
                  <span className="sig-kicker">{s.k}</span>
                  <span className="sig-num text-2xl md:text-3xl font-semibold">{s.v}</span>
                </div>
                <div className="sig-hr" />
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="sig-hr" />

      {/* Ticker */}
      <div className="sig-ticker relative z-10 py-3.5">
        <div className="sig-ticker-track sig-kicker">
          {[...TICKER, ...TICKER].map((t, i) => (
            <span key={i} className="flex items-center gap-2.5">
              <span className="sig-accent-c">/</span>
              {t}
            </span>
          ))}
        </div>
      </div>

      <div className="sig-hr" />

      {/* Capabilities — index list */}
      <section id="capabilities" className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-16 md:py-24">
        <div className="flex items-end justify-between mb-10">
          <h2 className="sig-display text-[clamp(1.75rem,4vw,3rem)]">核心能力<span className="sig-accent-c">.</span></h2>
          <span className="sig-kicker hidden md:block">能力 / Capabilities</span>
        </div>
        <div className="sig-hr" />
        {FEATURES.map((f, i) => {
          const Icon = f.icon;
          return (
            <div key={f.title}>
              <div className="sig-row group flex items-center gap-5 md:gap-10 py-7 md:py-8 cursor-default">
                <span className="sig-row-num sig-num text-sm md:text-base text-[color:var(--sig-faint)] w-10 shrink-0">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="flex-1 min-w-0">
                  <h3 className="text-lg md:text-2xl font-semibold tracking-[-0.01em]">{f.title}</h3>
                  <p className="mt-1.5 text-[13.5px] md:text-sm text-[color:var(--sig-dim)] max-w-xl leading-relaxed">{f.desc}</p>
                </div>
                <Icon size={22} className="shrink-0 text-[color:var(--sig-faint)] group-hover:text-[color:var(--sig-accent)] transition-colors" />
              </div>
              <div className="sig-hr" />
            </div>
          );
        })}
      </section>

      <div className="sig-hr" />

      {/* Process */}
      <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-16 md:py-24">
        <div className="sig-kicker mb-10">流程 / Process</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-[color:var(--sig-line)] border border-[color:var(--sig-line)]">
          {STEPS.map((step, i) => {
            const Icon = step.icon;
            return (
              <div key={step.label} className="bg-[color:var(--sig-bg)] p-7 md:p-9">
                <div className="flex items-center justify-between">
                  <span className="sig-num text-3xl md:text-5xl font-bold text-[color:var(--sig-fg)]">{String(i + 1).padStart(2, "0")}</span>
                  <Icon size={20} className="text-[color:var(--sig-accent)]" />
                </div>
                <div className="mt-8 text-base md:text-lg font-semibold">{step.label}</div>
                <div className="mt-1 text-[13.5px] text-[color:var(--sig-dim)]">{step.desc}</div>
              </div>
            );
          })}
        </div>
      </section>

      <div className="sig-hr" />

      {/* Final CTA */}
      <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-24 md:py-32 text-center">
        <div className="sig-kicker mb-6">// 免费 · 无广告 · 数据归你</div>
        <h2 className="sig-display text-[clamp(2.25rem,7vw,5.5rem)]">
          成为下一个 <span className="sig-stroke">OFFER</span>
          <br />
          <span className="sig-accent-c">holder</span> 了吗？
        </h2>
        <div className="mt-12 flex justify-center">
          <button onClick={() => navigate("/login")} className="sig-cta">
            免费开始 <ArrowRight size={16} />
          </button>
        </div>
      </section>

      <div className="sig-hr" />

      {/* Footer */}
      <footer className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 h-16 flex items-center justify-between sig-kicker">
        <span>© 2026 SparkOffer</span>
        <span>Built with ❤ + Claude</span>
      </footer>
    </div>
  );
}
