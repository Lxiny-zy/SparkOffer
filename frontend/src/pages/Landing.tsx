import { useEffect, useRef, useState } from "react";
import type { CSSProperties, RefObject } from "react";
import { useNavigate } from "react-router-dom";
import {
  Sun, Moon, ArrowRight, ArrowDown, Target, FileText, BriefcaseBusiness,
  MessageSquare, Code2, Brain, Radar, ChartLine, GitFork, User, Flame,
  BarChart3, BookOpen, ShieldCheck,
} from "lucide-react";
import CatAvatar from "@/components/CatAvatar";

/* ─────────────────────────────────────────────────────────────
   访客首页 · 设计假设（sig 系统内深化，不引入新设计语言）
   - 内容源：以代码为准 —— Home MODE_CARDS 四训练模式 + Sidebar 导航
     （算法竞技场 / 问答演练场 / 数据观测页），场景命名与产品内一致
   - 两个签名可视化：闭环信号流（The Loop）、三层上下文堆叠（Context Stack）
   - 滚动 reveal 用 .sig-reveal + IntersectionObserver；动效尊重 prefers-reduced-motion
   ───────────────────────────────────────────────────────────── */

const SPECS = [
  { k: "知识增强", v: "RAG" },
  { k: "长期画像", v: "Mem0" },
  { k: "复习调度", v: "SM-2" },
  { k: "面试状态机", v: "LangGraph" },
];

const TICKER = [
  "三层上下文融合", "Mem0 画像合并", "SM-2 间隔复习", "RAG 知识检索",
  "LangGraph 状态机", "增量向量索引", "确定性掌握度", "错题热区",
  "多路召回 RRF", "语义去重",
];

/* ── 闭环节点 ── */
const LOOP_NODES = [
  { label: "训练作答", sub: "六种场景任选" },
  { label: "LLM 评估", sub: "逐题评分 + 薄弱点" },
  { label: "画像更新", sub: "ADD / UPDATE / IMPROVE" },
  { label: "向量入库", sub: "洞察语义化存储" },
  { label: "SM-2 调度", sub: "按遗忘曲线排复习" },
  { label: "精准出题", sub: "命中当前薄弱点" },
];

const LOOP_CAPTIONS = [
  "作答被完整记录 —— 不只是对错，还有你的表达方式",
  "AI 逐题评分，提取本轮暴露的薄弱点与错误模式",
  "Mem0 风格合并进长期画像：新发现不是堆叠，是精炼",
  "洞察向量化入库，带时间衰减的语义检索随时召回",
  "SM-2 间隔重复：薄弱点按遗忘曲线自动安排复习",
  "下一轮出题融合全部历史 —— 每道题都有出处",
  "记忆回流 · 练得越多，AI 越懂你",
];

/* ── 三层上下文（视觉顺序：L3 顶 → L1 底）── */
const LAYERS = [
  {
    tag: "LAYER 3", name: "长期画像",
    desc: "跨会话累积的「你」：各领域强弱项、思维模式、表达习惯。Mem0 风格 ADD / UPDATE / IMPROVE 智能合并，几十轮训练后画像依然精炼。",
    src: ["画像档案", "向量记忆", "时间衰减检索"],
  },
  {
    tag: "LAYER 2", name: "领域掌握度",
    desc: "0-100 掌握度走确定性算法（难度/5 × 得分/10 加权），不靠 LLM 主观分。掌握度决定难度带：概念辨析 → 场景应用 → 系统设计。",
    src: ["掌握度算法", "历史薄弱点", "SM-2 到期项"],
  },
  {
    tag: "LAYER 1", name: "会话上下文",
    desc: "本轮训练的即时输入：简历与 JD 解析、知识库 RAG 检索、最近 20 题语义去重 —— 不重复、不跑题。",
    src: ["简历 / JD", "知识库 RAG", "近题去重"],
  },
];

const LAYER_OUTPUT = {
  name: "融合出题",
  desc: "三层上下文注入 LangGraph 工作流 —— 生成 10 道只属于你的题：命中你的薄弱点，引用你的知识库，避开你刚练过的。",
};

/* ── 六种训练场景（与产品内命名一致：Home 四模式 + 算法/问答两个竞技场）── */
const SCENES = [
  { icon: Target, tag: "精准打击", title: "弱点狙击站", desc: "选一个领域集中训练，AI 根据你的回答动态调整难度。前几题精准命中历史薄弱点，掌握度越高题越难。" },
  { icon: FileText, tag: "沉浸体验", title: "实战模拟场", desc: "AI 读取你的简历，模拟真实面试官。自我介绍 → 技术问题 → 项目深挖 → 反问收尾，完整走一遍面试流程。" },
  { icon: BriefcaseBusiness, tag: "定向突破", title: "岗位特训营", desc: "贴入目标岗位 JD，AI 拆解岗位重点，结合简历生成高概率问题和岗位匹配复盘。" },
  { icon: Brain, tag: "记忆强化", title: "知识训练场", desc: "AI 把知识库拆成记忆卡片，正反翻面强化记忆，三档深度循序渐进，配合 SM-2 到期复习。" },
  { icon: Code2, tag: "错题闭环", title: "算法竞技场", desc: "刷题收藏、错题回顾、AI 解题陪练 —— 反复出错的题自动进入高频复习队列。" },
  { icon: MessageSquare, tag: "知识回流", title: "问答演练场", desc: "自由追问任意技术点；好答案一键沉淀为知识卡片，回流进你的知识库参与下次出题。" },
];

/* ── 可观测成长 ── */
const OBSERVABILITY = [
  { icon: Radar, title: "掌握度雷达", desc: "各领域 0-100 一眼横评" },
  { icon: ChartLine, title: "成长趋势", desc: "每一轮训练后的掌握度曲线" },
  { icon: GitFork, title: "能力星图", desc: "题目与知识点的关联图谱" },
  { icon: User, title: "长期画像", desc: "思维模式 · 表达习惯 · 习惯性问题" },
  { icon: Flame, title: "高频错题", desc: "反复出错的考点热区" },
  { icon: BarChart3, title: "RAG 仪表盘", desc: "检索质量在线指标可观测" },
];

/* ── hooks ── */

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

function useInView<T extends HTMLElement>(threshold = 0.25): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) { setInView(true); return; }
    const ob = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { threshold });
    ob.observe(el);
    return () => ob.disconnect();
  }, [threshold]);
  return [ref, inView];
}

/** 给页面上所有 .sig-reveal 挂一次性 scroll-reveal（进入视口加 .sig-in 后停止观察）。 */
function useScrollReveal() {
  useEffect(() => {
    const els = Array.from(document.querySelectorAll<HTMLElement>(".sig-reveal"));
    if (!("IntersectionObserver" in window)) {
      els.forEach((el) => el.classList.add("sig-in"));
      return;
    }
    const ob = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) { e.target.classList.add("sig-in"); ob.unobserve(e.target); }
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -40px 0px" },
    );
    els.forEach((el) => ob.observe(el));
    return () => ob.disconnect();
  }, []);
}

/* ── 闭环信号流 ──
   phase 0..11：偶数 = 节点点亮驻留（0,2,…,10 → 节点 0-5），奇数 = 连接线通流
   （1,3,…,9 → conn 0-4），11 = 回环虚线（右→左），之后清零重来。 */
const NODE_MS = 1100, CONN_MS = 700, RETURN_MS = 1400;
const CONN_VAR = { "--sig-flow-ms": `${CONN_MS}ms` } as CSSProperties;
const RETURN_VAR = { "--sig-flow-ms": `${RETURN_MS}ms` } as CSSProperties;

function LoopViz() {
  const reduced = usePrefersReducedMotion();
  const [ref, inView] = useInView<HTMLDivElement>(0.3);
  const [phase, setPhase] = useState(0);
  const running = inView && !reduced;

  useEffect(() => {
    if (!running) return;
    const ms = phase === 11 ? RETURN_MS : phase % 2 ? CONN_MS : NODE_MS;
    const t = setTimeout(() => setPhase((p) => (p >= 11 ? 0 : p + 1)), ms);
    return () => clearTimeout(t);
  }, [phase, running]);

  const nodeOn = (i: number) => (reduced ? true : phase >= i * 2);
  const connCls = (i: number) => {
    if (reduced) return "sig-done";
    if (phase === i * 2 + 1) return "sig-flow";
    return phase > i * 2 + 1 ? "sig-done" : "";
  };
  const caption = LOOP_CAPTIONS[reduced ? 6 : phase === 11 ? 6 : Math.floor(phase / 2)];

  return (
    <div ref={ref}>
      {/* 桌面：横排 6 节点 + 回环线 */}
      <div className="hidden md:block">
        <div className="flex items-stretch">
          {LOOP_NODES.map((n, i) => (
            <div key={n.label} className="contents">
              {i > 0 && (
                <div className={`sig-loop-conn self-center w-6 lg:w-10 shrink-0 ${connCls(i - 1)}`} style={CONN_VAR}>
                  <i />
                </div>
              )}
              <div className={`sig-loop-node flex-1 min-w-0 p-3.5 lg:p-4 ${nodeOn(i) ? "sig-on" : ""}`}>
                <div className="sig-loop-idx sig-mono text-[11px] text-[color:var(--sig-faint)]">
                  {String(i + 1).padStart(2, "0")}
                </div>
                <div className="mt-2 text-[13px] lg:text-sm font-semibold tracking-[-0.01em]">{n.label}</div>
                <div className="mt-1 text-[11px] leading-snug text-[color:var(--sig-dim)] hidden lg:block">{n.sub}</div>
              </div>
            </div>
          ))}
        </div>
        <div className={`sig-loop-return mt-6 ${!reduced && phase === 11 ? "sig-flow" : ""}`} style={RETURN_VAR}>
          <i />
        </div>
        <div className="mt-2.5 flex items-center justify-between sig-kicker">
          <span>Memory Writeback</span>
          <span>↺ 洞察回流 — 下一轮出题携带全部历史</span>
        </div>
      </div>

      {/* 移动端：纵排 */}
      <div className="md:hidden">
        {LOOP_NODES.map((n, i) => (
          <div key={n.label}>
            {i > 0 && (
              <div className="flex justify-center">
                <div className={`sig-loop-conn-v ${connCls(i - 1)}`} style={CONN_VAR}><i /></div>
              </div>
            )}
            <div className={`sig-loop-node p-4 ${nodeOn(i) ? "sig-on" : ""}`}>
              <div className="flex items-baseline gap-3">
                <span className="sig-loop-idx sig-mono text-[11px] text-[color:var(--sig-faint)]">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="text-sm font-semibold">{n.label}</span>
              </div>
              <div className="mt-1 pl-8 text-[11.5px] text-[color:var(--sig-dim)]">{n.sub}</div>
            </div>
          </div>
        ))}
        <div className="mt-3 sig-kicker text-center">↺ 洞察回流至下一轮出题</div>
      </div>

      {/* 实时说明行 */}
      <div className="mt-5 min-h-6 sig-mono text-[12px] md:text-[12.5px] text-[color:var(--sig-accent)]">
        <span className="text-[color:var(--sig-faint)]">// </span>{caption}
      </div>
    </div>
  );
}

/* ── 三层上下文堆叠 ── 自动轮播 L1→L2→L3→融合输出；hover 可锁定任一层 */
const STACK_SEQ = [2, 1, 0, 3]; // 视觉自底向上聚合，最后输出

function ContextStack() {
  const reduced = usePrefersReducedMotion();
  const [ref, inView] = useInView<HTMLDivElement>(0.3);
  const [step, setStep] = useState(0);
  const [hold, setHold] = useState<number | null>(null);

  useEffect(() => {
    if (reduced || !inView || hold !== null) return;
    const t = setTimeout(() => setStep((s) => (s + 1) % STACK_SEQ.length), 2600);
    return () => clearTimeout(t);
  }, [step, reduced, inView, hold]);

  const active = reduced ? 3 : hold ?? STACK_SEQ[step];
  const layerOn = (i: number) => active === i || active === 3;
  const detail = active === 3 ? null : LAYERS[active];

  return (
    <div ref={ref} className="grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-10" onPointerLeave={() => setHold(null)}>
      {/* 左：堆叠可视化 */}
      <div className="lg:col-span-6 flex flex-col gap-2.5">
        {LAYERS.map((l, i) => (
          <div
            key={l.tag}
            className={`sig-stack-layer p-4 md:p-5 ${layerOn(i) ? "sig-on" : ""}`}
            onPointerEnter={() => setHold(i)}
          >
            <div className="flex items-baseline justify-between gap-4">
              <span className="text-sm md:text-[15px] font-semibold tracking-[-0.01em]">{l.name}</span>
              <span className="sig-kicker">{l.tag}</span>
            </div>
            <p className="lg:hidden mt-1.5 text-[12.5px] leading-relaxed text-[color:var(--sig-dim)]">{l.desc}</p>
          </div>
        ))}
        <div className="flex justify-center py-0.5">
          <ArrowDown size={15} className={active === 3 ? "text-[color:var(--sig-accent)]" : "text-[color:var(--sig-faint)]"} />
        </div>
        <div
          className={`sig-stack-layer p-4 md:p-5 ${active === 3 ? "sig-on" : ""}`}
          onPointerEnter={() => setHold(3)}
        >
          <div className="flex items-baseline justify-between gap-4">
            <span className="text-sm md:text-[15px] font-semibold tracking-[-0.01em]">{LAYER_OUTPUT.name}</span>
            <span className="sig-kicker">LangGraph</span>
          </div>
          <p className="lg:hidden mt-1.5 text-[12.5px] leading-relaxed text-[color:var(--sig-dim)]">{LAYER_OUTPUT.desc}</p>
        </div>
      </div>

      {/* 右：当前层详情（桌面） */}
      <div className="hidden lg:block lg:col-span-6">
        <div className="lg:sticky lg:top-10 min-h-[220px]">
          <div className="sig-kicker">{detail ? detail.tag : "OUTPUT"}</div>
          <h3 className="mt-3 text-xl font-semibold tracking-[-0.01em]">
            {detail ? detail.name : LAYER_OUTPUT.name}<span className="sig-accent-c">.</span>
          </h3>
          <p className="mt-3 max-w-md text-sm leading-relaxed text-[color:var(--sig-dim)]">
            {detail ? detail.desc : LAYER_OUTPUT.desc}
          </p>
          {detail && (
            <div className="mt-5 flex flex-wrap gap-2">
              {detail.src.map((s) => (
                <span key={s} className="sig-badge sig-badge-accent sig-mono">{s}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

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

  useScrollReveal();

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
          <div className="lg:col-span-8">
            <div className="sig-kicker sig-rise" style={{ animationDelay: "0.05s" }}>
              // 自适应面试训练系统
            </div>
            <h1 className="sig-display mt-6 text-[clamp(2.75rem,8.5vw,6.5rem)]">
              <span className="sig-linemask"><span style={{ animationDelay: "0.05s" }}>越练越懂你的</span></span>
              <span className="sig-linemask"><span style={{ animationDelay: "0.18s" }}>AI 面试<span className="sig-accent-c">教练</span></span></span>
            </h1>
            <p className="sig-rise mt-8 max-w-xl text-[15px] md:text-base leading-relaxed text-[color:var(--sig-dim)]" style={{ animationDelay: "0.35s" }}>
              不是一次性问答，而是跨会话的训练闭环 —— 作答、评估、画像更新、复习调度自动完成。
              你练得越多，AI 越懂你；下一道题，永远来自你此刻真实的薄弱点。
            </p>
            <div className="sig-rise mt-10 flex flex-col sm:flex-row items-stretch sm:items-center gap-3" style={{ animationDelay: "0.45s" }}>
              <button onClick={() => navigate("/login")} className="sig-cta justify-center">
                立即开始训练 <ArrowRight size={16} />
              </button>
              <button
                onClick={() => document.getElementById("loop")?.scrollIntoView({ behavior: "smooth" })}
                className="sig-cta-ghost justify-center"
              >
                看它如何进化 <ArrowDown size={15} />
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

      {/* The Loop */}
      <section id="loop" className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-16 md:py-24">
        <div className="sig-reveal flex items-end justify-between mb-4">
          <h2 className="sig-display text-[clamp(1.75rem,4vw,3rem)]">每次训练都被记住<span className="sig-accent-c">.</span></h2>
          <span className="sig-kicker hidden md:block">闭环 / The Loop</span>
        </div>
        <p className="sig-reveal max-w-2xl mb-10 text-sm md:text-[15px] leading-relaxed text-[color:var(--sig-dim)]" style={{ transitionDelay: "80ms" }}>
          市面上的面试工具大多「答完即结束」。SparkOffer 把每一次训练变成一次可累积的能力建模 ——
          六个环节自动流转，洞察回流到下一轮出题。
        </p>
        <div className="sig-reveal" style={{ transitionDelay: "160ms" }}>
          <LoopViz />
        </div>
      </section>

      <div className="sig-hr" />

      {/* Three-layer context */}
      <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-16 md:py-24">
        <div className="sig-reveal flex items-end justify-between mb-4">
          <h2 className="sig-display text-[clamp(1.75rem,4vw,3rem)]">出题前，先读懂你<span className="sig-accent-c">.</span></h2>
          <span className="sig-kicker hidden md:block">三层上下文 / Context</span>
        </div>
        <p className="sig-reveal max-w-2xl mb-10 text-sm md:text-[15px] leading-relaxed text-[color:var(--sig-dim)]" style={{ transitionDelay: "80ms" }}>
          不是从固定题库随机抽题。每一轮提问前，三层信息被同时读取、融合注入工作流。
        </p>
        <div className="sig-reveal" style={{ transitionDelay: "160ms" }}>
          <ContextStack />
        </div>
      </section>

      <div className="sig-hr" />

      {/* Training scenes — index list */}
      <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-16 md:py-24">
        <div className="sig-reveal flex items-end justify-between mb-10">
          <h2 className="sig-display text-[clamp(1.75rem,4vw,3rem)]">六种训练场景<span className="sig-accent-c">.</span></h2>
          <span className="sig-kicker hidden md:block">场景 / Arenas</span>
        </div>
        <div className="sig-reveal sig-hr" />
        {SCENES.map((f, i) => {
          const Icon = f.icon;
          return (
            <div key={f.title} className="sig-reveal" style={{ transitionDelay: `${i * 60}ms` }}>
              <div className="sig-row group flex items-center gap-5 md:gap-10 py-7 md:py-8 cursor-default">
                <span className="sig-row-num sig-num text-sm md:text-base text-[color:var(--sig-faint)] w-10 shrink-0">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="flex-1 min-w-0">
                  <h3 className="text-lg md:text-2xl font-semibold tracking-[-0.01em]">{f.title}</h3>
                  <p className="mt-1.5 text-[13.5px] md:text-sm text-[color:var(--sig-dim)] max-w-xl leading-relaxed">{f.desc}</p>
                </div>
                <span className="sig-kicker hidden lg:block shrink-0">{f.tag}</span>
                <Icon size={22} className="shrink-0 text-[color:var(--sig-faint)] group-hover:text-[color:var(--sig-accent)] transition-colors" />
              </div>
              <div className="sig-hr" />
            </div>
          );
        })}
      </section>

      <div className="sig-hr" />

      {/* Observability */}
      <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-16 md:py-24">
        <div className="sig-reveal flex items-end justify-between mb-10">
          <h2 className="sig-display text-[clamp(1.75rem,4vw,3rem)]">成长全程可观测<span className="sig-accent-c">.</span></h2>
          <span className="sig-kicker hidden md:block">数据 / Observability</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-px bg-[color:var(--sig-line)] border border-[color:var(--sig-line)]">
          {OBSERVABILITY.map((o, i) => {
            const Icon = o.icon;
            return (
              <div key={o.title} className="sig-reveal bg-[color:var(--sig-bg)] p-7 md:p-8 group" style={{ transitionDelay: `${i * 50}ms` }}>
                <div className="flex items-center justify-between">
                  <span className="sig-num text-xl md:text-2xl font-bold text-[color:var(--sig-faint)]">{String(i + 1).padStart(2, "0")}</span>
                  <Icon size={19} className="text-[color:var(--sig-faint)] group-hover:text-[color:var(--sig-accent)] transition-colors" />
                </div>
                <div className="mt-6 text-[15px] md:text-base font-semibold">{o.title}</div>
                <div className="mt-1 text-[12.5px] md:text-[13px] text-[color:var(--sig-dim)]">{o.desc}</div>
              </div>
            );
          })}
        </div>
      </section>

      <div className="sig-hr" />

      {/* Knowledge & ownership */}
      <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-16 md:py-24">
        <div className="sig-reveal sig-kicker mb-10">知识与数据 / Ownership</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-px bg-[color:var(--sig-line)] border border-[color:var(--sig-line)]">
          <div className="sig-reveal bg-[color:var(--sig-bg)] p-7 md:p-9">
            <BookOpen size={20} className="text-[color:var(--sig-accent)]" />
            <h3 className="mt-6 text-lg md:text-xl font-semibold">你的知识库，出题的事实依据</h3>
            <p className="mt-2 text-[13.5px] md:text-sm leading-relaxed text-[color:var(--sig-dim)]">
              上传自己的 Markdown 笔记，增量向量化入库 —— 只重嵌变更的文件。出题与评分引用你的知识库原文，而不是模型的泛泛记忆。
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              {["Markdown 入库", "增量向量化", "引用出处"].map((s) => (
                <span key={s} className="sig-badge sig-mono">{s}</span>
              ))}
            </div>
          </div>
          <div className="sig-reveal bg-[color:var(--sig-bg)] p-7 md:p-9" style={{ transitionDelay: "80ms" }}>
            <ShieldCheck size={20} className="text-[color:var(--sig-accent)]" />
            <h3 className="mt-6 text-lg md:text-xl font-semibold">数据归你</h3>
            <p className="mt-2 text-[13.5px] md:text-sm leading-relaxed text-[color:var(--sig-dim)]">
              每个用户独立数据目录，画像、记忆与训练记录彼此隔离；支持完全自部署。免费、无广告，你的成长数据只属于你。
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              {["用户隔离", "可自部署", "本地优先"].map((s) => (
                <span key={s} className="sig-badge sig-mono">{s}</span>
              ))}
            </div>
          </div>
        </div>
      </section>

      <div className="sig-hr" />

      {/* Final CTA */}
      <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-24 md:py-32 text-center">
        <div className="sig-reveal sig-kicker mb-6">// 免费 · 无广告 · 数据归你</div>
        <h2 className="sig-reveal sig-display text-[clamp(2.25rem,7vw,5.5rem)]" style={{ transitionDelay: "80ms" }}>
          成为下一个 <span className="sig-stroke">OFFER</span>
          <br />
          <span className="sig-accent-c">holder</span> 了吗？
        </h2>
        <div className="sig-reveal mt-12 flex justify-center" style={{ transitionDelay: "160ms" }}>
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
