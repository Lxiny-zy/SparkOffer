# sig 设计系统 · 参考规范

> **代号 sig（signature / 签名式视觉语言）。**
> 一句话：**瑞士国际主义编辑排版 × 终端蓝图风（Swiss-editorial × Terminal Blueprint）**——
> 硬朗蓝图网格 + 超大紧排标题 + 等宽字体标记 + 单一电光紫强调色 + 直角 + 发丝线。
>
> 这是一份**可移植**的规范：另一个项目（或另一个 AI）只要照此文档，就能复刻同一套视觉语言。
> 下面的 token、组件类、示例都来自真实在跑的代码（SparkOffer 前端）。

---

## 0. 它是什么 / 不是什么

sig 是**反潮流**的：当下大多数 SaaS 用「圆角 + 玻璃拟态 + 多彩渐变 + 满屏动效」，sig 故意走相反方向，追求**工程感、克制、印刷品般的高级感**。

| | sig（要） | 明确避免（不要） |
|---|---|---|
| 形 | **直角**（4px / 卡片 8px） | 大圆角胶囊（16–24px+） |
| 色 | 米白/近黑 + **唯一**电光紫强调 | 多彩极光、霓虹渐变 |
| 线 | 1px 发丝线、结构性分隔线 | 玻璃边、发光描边 |
| 质感 | **纯平面**，无填充渐变 | glassmorphism、box-shadow 发光 |
| 字 | 标题超大紧排 Inter + 等宽 mono 标记 | 常规字重、无层级对比 |
| 背景 | **静态**蓝图网格 | 动态极光 / 粒子 / 噪点 |
| 动效 | 一个招牌入场（行遮罩上滑）+ 克制 hover | 大量 keyframes、3D 倾斜、流光 |
| hover | 上移 + **硬偏移投影**（`4px 4px 0`，像印刷错位） | 柔和外发光 |

> ⚠️ 复刻时最容易犯的错：把它做成"又一个发光玻璃风"。sig 的灵魂是**留白、网格、对齐、字体层级和那一点点紫**，不是特效。

---

## 1. 设计原则（复刻时的判断准则）

1. **一个强调色，用得稀少。** 全站只有一种紫（`--sig-accent`）。它出现在：链接 hover 下划线、CTA 填充、active 指示线、序号 hover、`.` 句点装饰、图标点睛。**面积越小越高级**——大色块一律用中性色。
2. **网格是结构，不是装饰。** 蓝图网格静止、边缘径向渐隐；内容用 `max-w-[1200px]` 居中，靠发丝线（`sig-hr` / `sig-vr`）切分区块，区块之间用 `1px gap + 背景透出` 做"格子"。
3. **字体即层级。** 标题用 `sig-display`（Inter，字距 -0.035em，行高 0.96，clamp 自适应到很大）；所有"标签/眉题/序号/规格/数字"用等宽（`sig-kicker` / `sig-num`），大写 + 宽字距，营造终端感。正文用 `--sig-dim` 降一级灰。
4. **直角到底。** 圆角统一 4px（卡片 8px、徽章 3px）。`--radius` 被压到 `0.375rem`。
5. **克制动效。** 入场只有 `sig-linemask`（文字从裁切行内上滑）和 `sig-rise`（淡入上移），靠 `animation-delay` 错峰。hover 是位移 + 硬投影，不是发光。尊重 `prefers-reduced-motion`。
6. **亮/暗一次声明。** 所有颜色走 `--sig-*` 变量，`.dark .sig-root` 重定义一遍即可，组件层不写死颜色。

---

## 2. 设计 Token（直接拷贝）

整套系统的颜色全部来自这两个块。其余一切都引用这些变量。

```css
.sig-root {
  /* 基础面 / 前景 */
  --sig-bg:        #FAFAF8;   /* 页面底——米白，不是纯白 */
  --sig-fg:        #0B0B0D;   /* 主文字——近黑，不是纯黑 */
  --sig-dim:       #6A6A72;   /* 二级文字 */
  --sig-faint:     #9A9AA2;   /* 三级 / 占位 / 序号 */
  --sig-surface:   #FFFFFF;   /* 卡片面 */
  --sig-surface-2: #F2F2F0;   /* 次级面 */
  --sig-overlay:   rgba(11, 11, 13, 0.45);  /* 遮罩 */

  /* 发丝线 */
  --sig-line:   rgba(11, 11, 13, 0.12);  /* 默认分隔线 */
  --sig-line-2: rgba(11, 11, 13, 0.22);  /* 输入框 / 强边 */

  /* 唯一强调色：电光紫 */
  --sig-accent:    #6D3BFF;
  --sig-accent-fg: #FFFFFF;

  /* 状态色（克制使用） */
  --sig-danger:  #C8362B;
  --sig-success: #2E7D32;
  --sig-warning: #B26A00;
  --sig-info:    #2563EB;

  /* 图表 6 色（数据可视化时按序取用） */
  --sig-chart-1: #6D3BFF;
  --sig-chart-2: #2563EB;
  --sig-chart-3: #2E7D32;
  --sig-chart-4: #B26A00;
  --sig-chart-5: #C8362B;
  --sig-chart-6: #0E7490;

  background: var(--sig-bg);
  color: var(--sig-fg);
  font-family: "Inter", "Noto Sans SC", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}

.dark .sig-root {
  --sig-bg:        #08080A;
  --sig-fg:        #F4F4F2;
  --sig-dim:       #8E8E96;
  --sig-faint:     #5C5C64;
  --sig-surface:   #111114;
  --sig-surface-2: #17171B;
  --sig-overlay:   rgba(0, 0, 0, 0.6);

  --sig-line:   rgba(255, 255, 255, 0.10);
  --sig-line-2: rgba(255, 255, 255, 0.22);

  --sig-accent:    #8B6CFF;   /* 暗色下提亮的紫 */
  --sig-accent-fg: #0A0A0B;

  --sig-danger:  #FF6B5E;
  --sig-success: #6BBF6E;
  --sig-warning: #E0A030;
  --sig-info:    #6B9BFF;

  --sig-chart-1: #8B6CFF;
  --sig-chart-2: #6B9BFF;
  --sig-chart-3: #6BBF6E;
  --sig-chart-4: #E0A030;
  --sig-chart-5: #FF6B5E;
  --sig-chart-6: #4BB8C9;
}
```

---

## 3. 字体

三款字体，各司其职：

| 用途 | 字体 | 说明 |
|---|---|---|
| 标题 / 正文（西文） | **Inter** | 可变字重 400–700，紧排时用负字距 |
| 中文 | **Noto Sans SC** | 400 / 500 / 700 |
| 标记 / 序号 / 数字 / CTA | **JetBrains Mono** | 等宽、大写、宽字距，终端味的来源 |

HTML `<head>` 引入（Google Fonts，一行）：

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,400;14..32,500;14..32,600;14..32,700&family=Noto+Sans+SC:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

---

## 4. 组件类套件（直接拷贝）

以下 CSS 是 sig 的"积木"。配合上面的 token，复制即用，亮/暗自动适配。

### 4.1 蓝图网格 + 排版

```css
/* 蓝图网格——静态、结构性、边缘径向渐隐 */
.sig-grid {
  background-image:
    linear-gradient(to right, var(--sig-line) 1px, transparent 1px),
    linear-gradient(to bottom, var(--sig-line) 1px, transparent 1px);
  background-size: 72px 72px;
  -webkit-mask-image: radial-gradient(ellipse 110% 90% at 50% 0%, #000 45%, transparent 100%);
          mask-image: radial-gradient(ellipse 110% 90% at 50% 0%, #000 45%, transparent 100%);
}

/* 等宽家族：标记 / 眉题 / 数字 */
.sig-mono   { font-family: "JetBrains Mono", ui-monospace, "SFMono-Regular", monospace; }
.sig-kicker {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  text-transform: uppercase;
  letter-spacing: 0.24em;
  font-size: 11px;
  font-weight: 500;
  color: var(--sig-dim);
}
.sig-num {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}
.sig-stat {  /* 大号统计数字 */
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  font-weight: 700; line-height: 1; letter-spacing: -0.01em;
}

/* 超大紧排编辑体标题 */
.sig-display {
  font-family: "Inter", "Noto Sans SC", sans-serif;
  font-weight: 700;
  letter-spacing: -0.035em;
  line-height: 0.96;
}
.sig-accent-c { color: var(--sig-accent); }       /* 紫色点睛（句点 / 单词） */
.sig-stroke {                                      /* 空心描边大字 */
  color: transparent;
  -webkit-text-stroke: 1.2px var(--sig-fg);
}

/* 发丝分隔线 */
.sig-hr { height: 1px; width: 100%; background: var(--sig-line); border: none; }
.sig-vr { width: 1px; background: var(--sig-line); align-self: stretch; }
```

### 4.2 按钮 / CTA

```css
/* 招牌 CTA——hover 上移 + 硬偏移投影，无发光 */
.sig-cta {
  display: inline-flex; align-items: center; gap: 0.6rem;
  background: var(--sig-accent); color: var(--sig-accent-fg);
  font-family: "JetBrains Mono", ui-monospace, monospace;
  text-transform: uppercase; letter-spacing: 0.14em; font-size: 12.5px; font-weight: 600;
  padding: 1rem 1.5rem; border-radius: 4px; border: 1px solid var(--sig-accent);
  transition: transform 0.18s cubic-bezier(0.2,0,0,1), box-shadow 0.18s, background 0.18s;
}
.sig-cta:hover  { transform: translateY(-2px); box-shadow: 4px 4px 0 0 var(--sig-fg); }
.sig-cta:active { transform: translateY(0); box-shadow: 0 0 0 0 var(--sig-fg); }
.sig-cta svg       { transition: transform 0.2s ease; }
.sig-cta:hover svg { transform: translateX(3px); }   /* 箭头右移 */

.sig-cta-ghost {
  display: inline-flex; align-items: center; gap: 0.5rem;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  text-transform: uppercase; letter-spacing: 0.14em; font-size: 12.5px; font-weight: 500;
  padding: 1rem 1.25rem; border-radius: 4px;
  border: 1px solid var(--sig-line-2); color: var(--sig-fg);
  transition: border-color 0.18s, background 0.18s;
}
.sig-cta-ghost:hover { border-color: var(--sig-fg); background: color-mix(in srgb, var(--sig-fg) 4%, transparent); }

/* 通用按钮（中文友好：不强制 mono/大写） */
.sig-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 0.45rem;
  border-radius: 4px; font-weight: 500; font-size: 13.5px; line-height: 1;
  border: 1px solid var(--sig-line-2); color: var(--sig-fg); background: transparent;
  padding: 0.55rem 0.95rem; white-space: nowrap;
  transition: transform 0.16s cubic-bezier(0.2,0,0,1), box-shadow 0.16s, border-color 0.16s, background 0.16s, color 0.16s, opacity 0.16s;
}
.sig-btn:hover    { border-color: var(--sig-fg); background: color-mix(in srgb, var(--sig-fg) 5%, transparent); }
.sig-btn:active   { transform: translateY(1px); }
.sig-btn:disabled { opacity: 0.45; pointer-events: none; }
.sig-btn svg      { flex-shrink: 0; }
.sig-btn-accent { background: var(--sig-accent); color: var(--sig-accent-fg); border-color: var(--sig-accent); }
.sig-btn-accent:hover { transform: translateY(-1px); box-shadow: 3px 3px 0 0 var(--sig-fg); }
.sig-btn-danger { color: var(--sig-danger); border-color: color-mix(in srgb, var(--sig-danger) 45%, transparent); }
.sig-btn-danger:hover { border-color: var(--sig-danger); background: color-mix(in srgb, var(--sig-danger) 8%, transparent); }
.sig-btn-ghost  { border-color: transparent; color: var(--sig-dim); }
.sig-btn-ghost:hover { color: var(--sig-fg); background: color-mix(in srgb, var(--sig-fg) 5%, transparent); }

/* 方形图标按钮 */
.sig-iconbtn {
  width: 36px; height: 36px; display: grid; place-items: center;
  border: 1px solid var(--sig-line-2); border-radius: 4px; color: var(--sig-fg);
  transition: border-color 0.18s, background 0.18s;
}
.sig-iconbtn:hover { border-color: var(--sig-fg); background: color-mix(in srgb, var(--sig-fg) 4%, transparent); }
```

### 4.3 卡片 / 徽章

```css
.sig-card { background: var(--sig-surface); border: 1px solid var(--sig-line); border-radius: 8px; }
.sig-card-hover { transition: transform 0.18s cubic-bezier(0.2,0,0,1), border-color 0.18s; }
.sig-card-hover:hover { transform: translateY(-2px); border-color: var(--sig-line-2); }
.sig-hover-lift { transition: transform 0.18s cubic-bezier(0.2,0,0,1); }
.sig-hover-lift:hover { transform: translateY(-2px); }

/* 直角徽章 + 状态变体 */
.sig-badge {
  display: inline-flex; align-items: center; gap: 0.3rem;
  font-size: 11px; font-weight: 600; line-height: 1; white-space: nowrap;
  padding: 0.2rem 0.5rem; border-radius: 3px;
  border: 1px solid var(--sig-line-2); color: var(--sig-dim); background: transparent;
}
.sig-badge-accent  { color: var(--sig-accent);  border-color: color-mix(in srgb, var(--sig-accent) 40%, transparent);  background: color-mix(in srgb, var(--sig-accent) 9%, transparent); }
.sig-badge-success { color: var(--sig-success); border-color: color-mix(in srgb, var(--sig-success) 42%, transparent); background: color-mix(in srgb, var(--sig-success) 10%, transparent); }
.sig-badge-warning { color: var(--sig-warning); border-color: color-mix(in srgb, var(--sig-warning) 42%, transparent); background: color-mix(in srgb, var(--sig-warning) 10%, transparent); }
.sig-badge-info    { color: var(--sig-info);    border-color: color-mix(in srgb, var(--sig-info) 42%, transparent);    background: color-mix(in srgb, var(--sig-info) 10%, transparent); }
.sig-badge-danger  { color: var(--sig-danger);  border-color: color-mix(in srgb, var(--sig-danger) 42%, transparent);  background: color-mix(in srgb, var(--sig-danger) 10%, transparent); }
```

### 4.4 表单输入

```css
.sig-field {
  width: 100%; background: transparent; color: var(--sig-fg);
  border: 1px solid var(--sig-line-2); border-radius: 4px;
  padding: 0.85rem 0.95rem; font-size: 15px; font-family: inherit;
  transition: border-color 0.18s, box-shadow 0.18s;
}
.sig-field::placeholder { color: var(--sig-faint); }
.sig-field:focus { outline: none; border-color: var(--sig-accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--sig-accent) 18%, transparent); }

.sig-textarea {
  width: 100%; background: transparent; color: var(--sig-fg);
  border: 1px solid var(--sig-line-2); border-radius: 4px;
  padding: 0.75rem 0.95rem; font-size: 14px; font-family: inherit; line-height: 1.6;
  transition: border-color 0.18s, box-shadow 0.18s; resize: vertical;
}
.sig-textarea::placeholder { color: var(--sig-faint); }
.sig-textarea:focus { outline: none; border-color: var(--sig-accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--sig-accent) 18%, transparent); }
```

### 4.5 导航

```css
/* 顶部导航链接——hover 下划线从左展开 */
.sig-navlink { position: relative; color: var(--sig-dim); transition: color 0.18s; }
.sig-navlink:hover { color: var(--sig-fg); }
.sig-navlink::after {
  content: ""; position: absolute; left: 0; bottom: -3px; height: 1px; width: 100%;
  background: var(--sig-accent); transform: scaleX(0); transform-origin: left;
  transition: transform 0.25s cubic-bezier(0.2,0,0,1);
}
.sig-navlink:hover::after { transform: scaleX(1); }

/* 侧栏导航项 + active 左侧 2px 紫线 */
.sig-navitem {
  position: relative; display: flex; align-items: center; gap: 0.7rem;
  width: 100%; padding: 0.6rem 0.85rem; border-radius: 4px;
  font-size: 13px; font-weight: 500; color: var(--sig-dim); text-align: left;
  transition: color 0.18s, background 0.18s;
}
.sig-navitem:hover { color: var(--sig-fg); background: color-mix(in srgb, var(--sig-fg) 4%, transparent); }
.sig-navitem-active { color: var(--sig-fg); background: color-mix(in srgb, var(--sig-fg) 6%, transparent); }
.sig-navitem-active::before {
  content: ""; position: absolute; left: 0; top: 50%; transform: translateY(-50%);
  width: 2px; height: 62%; background: var(--sig-accent);
}

/* 索引列表行——hover 左缩进 + 左竖线刷入 */
.sig-row { position: relative; transition: background 0.2s ease, padding-left 0.25s cubic-bezier(0.2,0,0,1); }
.sig-row::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
  background: var(--sig-accent); transform: scaleY(0); transform-origin: top;
  transition: transform 0.25s cubic-bezier(0.2,0,0,1);
}
.sig-row:hover { background: color-mix(in srgb, var(--sig-fg) 3%, transparent); padding-left: 1.25rem; }
.sig-row:hover::before { transform: scaleY(1); }
.sig-row:hover .sig-row-num { color: var(--sig-accent); }
.sig-row-num { transition: color 0.2s ease; }
```

### 4.6 反馈 / 状态 / 动效

```css
/* 骨架屏——克制脉冲，无彩虹 */
.sig-skeleton { background: color-mix(in srgb, var(--sig-fg) 8%, transparent); border-radius: 4px; animation: sig-skeleton-pulse 1.5s ease-in-out infinite; }
@keyframes sig-skeleton-pulse { 0%, 100% { opacity: 0.5; } 50% { opacity: 1; } }

/* 工具提示——反相小黑块 */
.sig-tooltip { background: var(--sig-fg); color: var(--sig-bg); border-radius: 4px; font-size: 12px; font-weight: 500; padding: 0.35rem 0.6rem; }

/* 功能性进度条——直角 */
.sig-progress { height: 6px; width: 100%; background: var(--sig-line); overflow: hidden; }
.sig-progress-fill { height: 100%; background: var(--sig-accent); transition: width 0.3s ease; }

/* 招牌入场①：文字从裁切行内上滑 */
.sig-linemask { display: block; overflow: hidden; padding-bottom: 0.04em; }
.sig-linemask > span { display: block; transform: translateY(110%); animation: sig-line-up 0.85s cubic-bezier(0.16,1,0.3,1) forwards; }
@keyframes sig-line-up { to { transform: translateY(0); } }

/* 招牌入场②：淡入上移（配 animation-delay 错峰） */
.sig-rise { opacity: 0; animation: sig-rise 0.6s cubic-bezier(0.2,0,0,1) forwards; }
@keyframes sig-rise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }

/* 等宽跑马灯 */
.sig-ticker { display: flex; overflow: hidden; white-space: nowrap; }
.sig-ticker-track { display: inline-flex; align-items: center; gap: 2.5rem; padding-right: 2.5rem; animation: sig-marquee 38s linear infinite; }
@keyframes sig-marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
.sig-ticker:hover .sig-ticker-track { animation-play-state: paused; }

@media (prefers-reduced-motion: reduce) {
  .sig-linemask > span, .sig-rise { animation: none !important; opacity: 1 !important; transform: none !important; }
  .sig-ticker-track { animation: none !important; }
}
```

---

## 5. 布局骨架

页面根容器套 `.sig-root`，网格固定在视口、内容在其上滚动；正文用 `max-w-[1200px]` 居中，靠 `sig-hr` 分段。

```jsx
<div className="sig-root min-h-screen relative overflow-hidden">
  {/* 蓝图网格——固定、不拦截事件 */}
  <div className="sig-grid fixed inset-0 pointer-events-none" aria-hidden />

  {/* 顶栏 */}
  <header className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 h-16 flex items-center justify-between">
    <span className="sig-display text-[17px] tracking-[-0.03em]">品牌名</span>
    <span className="sig-vr h-4" />
    <span className="sig-kicker">AI Mock Interview</span>
  </header>
  <div className="sig-hr" />

  {/* 内容区 */}
  <section className="relative z-10 mx-auto max-w-[1200px] px-6 md:px-8 py-24">
    …
  </section>
</div>
```

> 应用内（带侧栏）的外壳：`<div className="sig-root flex h-screen">` 包 `<Sidebar/>` + `<main>`，把 `sig-grid` 用一个 `sticky top-0 h-0` 包裹层钉在视口、内容在其上滚动（不做视差、不做粒子）。

---

## 6. 真实用法片段（摘自本项目）

**Hero 标题（行遮罩入场 + 紫色点睛）：**

```jsx
<div className="sig-kicker sig-rise">// 自适应面试训练系统</div>
<h1 className="sig-display mt-6 text-[clamp(2.75rem,8.5vw,6.5rem)]">
  <span className="sig-linemask"><span>越练越懂你的</span></span>
  <span className="sig-linemask"><span>AI 面试<span className="sig-accent-c">教练</span></span></span>
</h1>
```

**规格表（眉题 + 等宽大数字 + 发丝线）：**

```jsx
<div className="sig-kicker mb-4">规格 / SPEC</div>
<div className="sig-hr" />
{specs.map((s) => (
  <div key={s.k}>
    <div className="flex items-baseline justify-between py-4">
      <span className="sig-kicker">{s.k}</span>
      <span className="sig-num text-2xl font-semibold">{s.v}</span>
    </div>
    <div className="sig-hr" />
  </div>
))}
```

**索引列表（序号 + hover 左缩进/竖线/图标变紫）：**

```jsx
<div className="sig-row group flex items-center gap-10 py-8 cursor-default">
  <span className="sig-row-num sig-num text-[color:var(--sig-faint)] w-10 shrink-0">01</span>
  <div className="flex-1">
    <h3 className="text-2xl font-semibold tracking-[-0.01em]">标题</h3>
    <p className="mt-1.5 text-sm text-[color:var(--sig-dim)]">描述。</p>
  </div>
  <Icon size={22} className="text-[color:var(--sig-faint)] group-hover:text-[color:var(--sig-accent)] transition-colors" />
</div>
<div className="sig-hr" />
```

**"格子"区块（1px gap 透出背景做分隔，无圆角）：**

```jsx
<div className="grid grid-cols-3 gap-px bg-[color:var(--sig-line)] border border-[color:var(--sig-line)]">
  {steps.map((s, i) => (
    <div key={s.label} className="bg-[color:var(--sig-bg)] p-9">
      <span className="sig-num text-5xl font-bold">{String(i+1).padStart(2,"0")}</span>
      …
    </div>
  ))}
</div>
```

**侧栏导航项 + 主按钮：**

```jsx
<button className={cn("sig-navitem", active && "sig-navitem-active")}>
  <Icon size={18} className={cn(active && "text-[color:var(--sig-accent)]")} />
  <span className="truncate">{label}</span>
</button>

<button className="sig-cta">立即开始 <ArrowRight size={16} /></button>
<button className="sig-btn sig-btn-accent">保存</button>
<span className="sig-badge sig-badge-success">已完成</span>
```

---

## 7. 移植到新项目（3 步）

sig 的复用成本极低，关键在第 2 步的"语义兜底"技巧。

### 步骤 1 · 引字体
把第 3 节的 `<link>` 放进 HTML `<head>`。

### 步骤 2 · 拷 CSS
把第 2 节（token）+ 第 4 节（组件类）整段拷进全局 CSS。**到这里 `.sig-*` 类已可直接用。**

> 若新项目用 **Tailwind**、且页面里大量用了 `bg-card / text-foreground / border-border / rounded-lg` 这类**语义工具类**，可加一段"语义兜底"——在 `.sig-root` 作用域内把这些语义变量重映射到 sig 调色板。这样**老代码一行不改**，套上 `.sig-root` 就整体变 sig 配色，再逐页换成 `.sig-*` 基元即可。映射要点：
>
> ```css
> .sig-root {
>   --background: var(--sig-bg);          --foreground: var(--sig-fg);
>   --card: var(--sig-surface);           --card-foreground: var(--sig-fg);
>   --primary: var(--sig-accent);         --primary-foreground: var(--sig-accent-fg);
>   --muted-foreground: var(--sig-dim);   --border: var(--sig-line);
>   --ring: var(--sig-accent);            --radius: 0.375rem;  /* 圆角压成 sig 直角 */
>   /* 把发光/玻璃/渐变 token 全部清零，避免旧风格残留 */
>   --glow-primary: transparent; --glass-bg: var(--sig-surface); --gradient-primary: var(--sig-accent);
> }
> ```
>
> （变量名按你项目实际的 token 命名对应即可。纯 CSS / 非 Tailwind 项目跳过这步。）

### 步骤 3 · 套壳 + 逐页采用
- 应用根容器加 `className="sig-root"`，背景放 `<div className="sig-grid fixed inset-0 pointer-events-none" />`。
- 暗色模式：在 `<html>` 或根上切 `.dark` 类（`--sig-*` 会自动取暗值）。
- 之后按第 5–6 节的范式逐页写：标题 `sig-display`、标签 `sig-kicker`、分隔 `sig-hr`、按钮 `sig-cta/sig-btn`、卡片 `sig-card`……

---

## 8. 类名速查表

| 类别 | 类名 |
|---|---|
| 作用域 / 背景 | `.sig-root` · `.sig-grid` |
| 排版 | `.sig-display` · `.sig-kicker` · `.sig-mono` · `.sig-num` · `.sig-stat` · `.sig-accent-c` · `.sig-stroke` |
| 分隔 | `.sig-hr` · `.sig-vr` |
| 按钮 | `.sig-cta` · `.sig-cta-ghost` · `.sig-btn`（`-accent` / `-danger` / `-ghost`）· `.sig-iconbtn` |
| 容器 | `.sig-card` · `.sig-card-hover` · `.sig-hover-lift` |
| 徽章 | `.sig-badge`（`-accent` / `-success` / `-warning` / `-info` / `-danger`） |
| 表单 | `.sig-field` · `.sig-textarea` |
| 导航 | `.sig-navlink` · `.sig-navitem`（`-active`）· `.sig-row` / `.sig-row-num` |
| 反馈 | `.sig-skeleton` · `.sig-tooltip` · `.sig-progress` / `.sig-progress-fill` |
| 动效 | `.sig-linemask` · `.sig-rise` · `.sig-ticker` / `.sig-ticker-track` |

---

## 9. 一段话喂给 AI（让它按 sig 风格生成 UI）

> 用 **sig 设计系统**：瑞士编辑排版 × 终端蓝图风。米白底 `#FAFAF8`/近黑字 `#0B0B0D`（暗色 `#08080A`/`#F4F4F2`），**唯一**强调色电光紫 `#6D3BFF`（暗色 `#8B6CFF`），**只小面积**用。一律**直角**（4px）、**1px 发丝线**分隔、**纯平面无玻璃无发光无渐变填充**。标题用 Inter 超大紧排（字距 -0.035em、行高 0.96、clamp 自适应），标签/序号/数字/CTA 用 JetBrains Mono 等宽、大写、宽字距。背景是**静态**蓝图网格（72px、边缘渐隐）。hover 用位移 + 硬偏移投影（`4px 4px 0`），不要发光。入场动效只用"文字裁切行上滑"。内容 `max-w-[1200px]` 居中，区块靠发丝线和 1px gap 切分。
