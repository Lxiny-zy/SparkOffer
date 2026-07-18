# 06 · 前端架构

> React 19 + Vite 8 + TypeScript 6 + Tailwind CSS v4 + Radix UI（shadcn 风格）。
> 10.2k 行 TypeScript，19 个页面。这章重点讲**前后端协作机制**而不是 React 语法。

---

## 1. 技术栈选型

| 层 | 技术 | 选择理由 |
|---|---|---|
| 框架 | **React 19** | 最新稳定版，concurrent 渲染、`use()` hook 支持 |
| 构建 | **Vite 8** | 比 webpack 快 10 倍，HMR 秒级；`@vitejs/plugin-react`，`@tailwindcss/vite` |
| 语言 | **TypeScript 6** | 静态类型 + IDE 友好；后端 Pydantic schema 手动镜像到 `types/api.ts` |
| 路由 | **React Router v7** | 项目早期还在 v6，迁过来主要是 future flags 友好 |
| 样式 | **Tailwind CSS v4** | utility-first，新版没有 `tailwind.config.js`（v4 用 CSS @theme） |
| 组件库 | **Radix UI + shadcn 风格** | Radix 解决无障碍（focus trap / aria），样式自己写 |
| 图表 | **Recharts** + **react-force-graph-2d** | 雷达图/趋势图用 Recharts；题目关联图用 force-graph |
| Markdown | **react-markdown** + **remark-gfm** + **react-syntax-highlighter** | Prism oneDark 主题 |
| Toast | **sonner** | 比 react-hot-toast 更好看 |
| 图标 | **lucide-react** | 0.577 版，比 heroicons 更全 |

**为什么不用 Next.js**：项目是纯 SPA（不需要 SSR），Vite 已经够用。Next.js 会引入 server functions 让架构复杂。

**为什么不用 Redux / Zustand**：

- 服务端状态用 fetch + local state（每个页面自己维护），不需要全局 store
- 用户认证用 React Context（AuthContext）
- 复杂状态只在 Interview 页面（drill 中途答题进度），用 local state + debounce 持久化
- 引入 Redux 会增加心智成本，没必要

---

## 2. 整体路由 + 鉴权

```tsx
// App.tsx
<AuthProvider>
  <BrowserRouter>
    <ErrorBoundary>
      <SessionExpiredModal />  {/* 监听 401 事件 */}
      <Routes>
        <Route path="/" element={<PublicHome />} />        {/* 未登录 → Landing，已登录 → Home */}
        <Route path="/login" element={<AuthPage />} />
        <Route path="/*" element={
          <ProtectedRoute>
            <AppShell>
              <Routes>
                <Route path="/interview/:sessionId" element={<Interview />} />
                <Route path="/review/:sessionId" element={<Review />} />
                <Route path="/profile" element={<Profile />} />
                <Route path="/profile/topic/:topic" element={<TopicDetail />} />
                <Route path="/knowledge" element={<Knowledge />} />
                <Route path="/graph" element={<Graph />} />
                <Route path="/job-prep" element={<JobPrep />} />
                <Route path="/qa-arena" element={<QAArena />} />
                <Route path="/algorithm" element={<AlgorithmSolver />} />
                <Route path="/algorithm/collection" element={<AlgorithmCollection />} />
                <Route path="/favorites" element={<Favorites />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/history" element={<History />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </AppShell>
          </ProtectedRoute>
        } />
      </Routes>
      <Toaster />
    </ErrorBoundary>
  </BrowserRouter>
</AuthProvider>
```

**三层路由保护**：
- L1 `AuthProvider`：把 token 注入 Context，启动时拉 `/api/profile` 验证
- L2 `ProtectedRoute`：未登录跳 `/`
- L3 `SessionExpiredModal`：401 时弹层提示重新登录（不强制跳转，**防丢失页面数据**）

---

## 3. AuthContext 的鲁棒性设计

```tsx
// contexts/AuthContext.tsx
useEffect(() => {
  if (token) {
    // ★ 5 秒超时：防止后端繁忙导致 loading 永远不结束
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);

    fetch("/api/profile", {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then((res) => {
        clearTimeout(timer);
        if (res.ok) {
          const stored = localStorage.getItem("user");
          if (stored) setUser(JSON.parse(stored));
          return;
        }
        if (res.status === 401) {
          logout();
          return;
        }
        // ★ 5xx 或其他错误：保留本地会话，不登出
        const stored = localStorage.getItem("user");
        if (stored) setUser(JSON.parse(stored));
        console.warn("Auth bootstrap: backend temporarily unavailable, keeping local session:", res.status);
      })
      .catch((err) => {
        clearTimeout(timer);
        // AbortError / 网络错误：保留本地会话
        const stored = localStorage.getItem("user");
        if (stored) setUser(JSON.parse(stored));
      })
      .finally(() => setLoading(false));
  }
}, []);
```

**三个细节**：
- **5 秒超时**：后端启动慢（首次 embedding 初始化 30s+）时不让用户黑屏
- **5xx 保留会话**：后端临时不可用，用户体验不应是被踢出
- **401 才登出**：明确认证失效才清 localStorage

---

## 4. SSE 客户端 · 两种模式

项目里 SSE 有两种用法，对应两种 helper：

### 4.1 `fetchSSE`：返回单个 result（progress 通用 helper）

```typescript
// api/sse.ts
export async function fetchSSE<T>(
  url: string,
  options: RequestInit,
  callbacks?: { onProgress?: (msg: string) => void },
): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 120000); // 2 分钟

  const res = await fetch(url, { ...options, signal: controller.signal, headers: authHeaders(...) });

  // 不是 SSE → 退化成 JSON
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream")) {
    return res.json();
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: T | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";  // 保留最后一段（可能未完）
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(line.slice(6));
        switch (event.type) {
          case "progress": callbacks?.onProgress?.(event.message); break;
          case "error": throw new Error(event.message);
          case "complete": result = event.data as T; break;
        }
      } catch (e) {
        if (e.message && !e.message.includes("JSON")) throw e;
      }
    }
  }

  if (!result) throw new Error("请求失败：未收到结果");
  return result;
}
```

**用法**（页面侧）：

```typescript
const result = await startInterview("topic_drill", "python", {
  onProgress: (msg) => setStartProgress(msg),  // "正在生成题目..."
});
```

### 4.2 自定义 reader：边解析边渲染

出题流式场景，每收到一个完整题目就更新 UI：

```typescript
// api/interview.ts:startInterviewStream
export async function startInterviewStream(
  mode: string, topic: string,
  { onQuestion, onDone, onError }: StreamCallbacks,
): Promise<void> {
  const res = await authFetch(`${API_BASE}/interview/start-stream`, { ... });
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop()!;

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === "question") onQuestion?.(event.data);
      else if (event.type === "done") onDone?.(event);
      else if (event.type === "error") onError?.(event.message);
    }
  }
}
```

**用法**：

```typescript
const questions: Question[] = [];
await startInterviewStream("topic_drill", "python", {
  onQuestion: (q) => {
    questions.push(q);
    setQuestions([...questions]);  // 触发重渲染
  },
  onDone: (event) => navigate(`/interview/${event.session_id}`, { state: {...} }),
});
```

### 4.3 关键细节：buffer 处理

```typescript
const lines = buffer.split("\n");
buffer = lines.pop()!;  // 最后一段保留为 buffer，可能未完整
```

**为什么**：TextDecoder 每次 read() 返回的数据可能在 `\n` 中间切断。`lines.pop()` 把最后一段留到下次合并，**保证 JSON 不会被切散**。

---

## 5. Auth Fetch 拦截器

```typescript
// api/client.ts
const _sessionExpiredListeners: SessionExpiredListener[] = [];
let _sessionExpiredFired = false;  // 只触发一次

export function onSessionExpired(listener) {
  _sessionExpiredListeners.push(listener);
  return () => { /* unsubscribe */ };
}

function _fireSessionExpired() {
  if (_sessionExpiredFired) return;  // 防止重复弹层
  _sessionExpiredFired = true;
  _sessionExpiredListeners.forEach(fn => fn());
}

export async function authFetch(url, options = {}) {
  const headers = authHeaders(options.headers);
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    _fireSessionExpired();  // ★ 触发全局事件
    throw new Error("Session expired");
  }
  if (res.status >= 500) {
    throw new Error(`Backend temporarily unavailable (${res.status})`);
  }
  return res;
}
```

**事件总线模式**：
- `authFetch` 是所有页面的统一入口
- 401 触发 `_sessionExpiredListeners`
- `App.tsx` 注册 `SessionExpiredModal`，监听到事件弹层提示
- **不直接跳转登录页**，让用户在原页面看到提示，刷新后页面状态不丢

---

## 6. 路由懒加载 + Suspense

```tsx
// App.tsx
const Interview = lazy(() => import("./pages/Interview"));
const Review = lazy(() => import("./pages/Review"));
// ... 15 个 lazy 路由

// 在 AppShell 里：
<Suspense fallback={<Loader2 className="w-6 h-6 animate-spin text-primary" />}>
  {children}
</Suspense>
```

**收益**：
- 首屏只下载 Home + Sidebar + FloatingAssistant
- 进 `/interview` 才下载 Interview.tsx + ChatBubble + 各种 markdown 库
- 单 chunk ~10-30KB（gzip 后），按需加载

---

## 7. 关键页面：Interview.tsx 的中途持久化

```tsx
// pages/Interview.tsx
const [answers, setAnswers] = useState<Record<number, string>>({});
const [currentIndex, setCurrentIndex] = useState(0);
const [hints, setHints] = useState<Record<number, HintState>>({});
const saveTimerRef = useRef<number | null>(null);

// ★ Debounced 400ms 保存到后端
useEffect(() => {
  if (!isBatchMode || !sessionId || restoring || finished) return;
  if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
  saveTimerRef.current = window.setTimeout(() => {
    saveDrillProgress(sessionId, {
      current_index: currentIndex,
      partial_answers: answers,
      hints,
    })
      .then(() => { saveErrorShownRef.current = false; })
      .catch((err) => {
        if (!saveErrorShownRef.current) {
          saveErrorShownRef.current = true;
          toast.error("进度保存失败，刷新可能丢失最近输入");
        }
      });
  }, 400);
  return () => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
  };
}, [currentIndex, answers, hints, sessionId, isBatchMode, restoring, finished]);

// ★ 刷新页面后从后端恢复
useEffect(() => {
  if (initData.mode || !sessionId || restoredRef.current) return;
  restoredRef.current = true;
  getInterviewSession(sessionId)
    .then((sess) => {
      const progress = sess.meta?.progress || {};
      if (progress.partial_answers) {
        const parsed: Record<number, string> = {};
        for (const [k, v] of Object.entries(progress.partial_answers)) {
          parsed[Number(k)] = v as string;
        }
        setAnswers(parsed);
      }
      if (typeof progress.current_index === "number") setCurrentIndex(progress.current_index);
      // ...
    })
    .catch(...)
    .finally(() => setRestoring(false));
}, [sessionId, initData.mode]);
```

**设计点**：
- **400ms 防抖**：用户连打字时不会每次按键都发请求
- **保存失败只 toast 一次**：避免连续保存失败弹一堆 toast
- **刷新恢复**：从 `sessions.meta.progress` 拉回中途状态
- **`restoredRef.current`** 防止 strict mode 双调用导致重复加载

---

## 8. Markdown 渲染组件复用

`components/ChatBubble.tsx` 导出可复用的 `markdownComponents`：

```typescript
export const markdownComponents = {
  pre({ children }) {
    // 代码块用 SyntaxHighlighter (Prism oneDark)
    return (
      <div className="md-code-wrapper">
        {lang && <span className="md-code-lang">{lang}</span>}
        <SyntaxHighlighter language={lang} style={oneDark} ...>
          {codeString}
        </SyntaxHighlighter>
      </div>
    );
  },
  table({ children, ...props }) {
    return <div className="md-table-wrapper"><table {...props}>{children}</table></div>;
  },
  // ...
};

export const remarkPlugins = [remarkGfm];  // GFM 启用表格 / 任务列表
```

**复用点**：
- ChatBubble（面试官回复）
- FloatingAssistant（小鱼回复）
- Review（复盘报告）
- 参考答案弹层
- QAArena 消息

**关键设计**：代码块在 `pre` 组件里渲染（不是 `code`），因为 `react-markdown` 把代码块的 className 放在内层 `code` 元素上。`pre` 从 children 中提取 className 决定语言。

---

## 9. 图表组件（Recharts）

`components/charts/` 下 6 个图表：

| 组件 | 类型 | 用途 |
|---|---|---|
| `TopicRadarChart` | 雷达图 | 各领域掌握度分布（带 previous_topic_mastery 对比叠加） |
| `ScoreTrendChart` | 折线图 | 训练得分趋势（最近 30 次） |
| `DimensionTrendChart` | 多线折线 | dimension_scores 四维趋势（resume / job_prep） |
| `SessionFrequencyChart` | 柱状图 | 训练频率（按周聚合） |
| `LearningHeatmap` | 热力图 | 学习日历（参考 GitHub contribution graph） |
| `KnowledgeTreemap` | 矩形树图 | 各领域薄弱点占比 |

**通用模式**：每个图表都接收一个 `data` prop，自己处理空数据状态（"暂无数据"占位）。

---

## 10. ErrorBoundary（全局兜底）

```tsx
// components/ErrorBoundary.tsx
export default class ErrorBoundary extends Component<...> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex flex-col items-center justify-center p-10 md:p-15 gap-4 min-h-[60vh]">
        <div className="text-2xl font-bold text-text">出了点问题</div>
        <div className="text-sm text-dim max-w-[400px] text-center break-words">
          {this.state.error?.message || "未知错误"}
        </div>
        <Button onClick={() => this.setState({ error: null })}>重试</Button>
        <Link to="/" className="text-sm text-primary hover:underline">返回首页</Link>
      </div>
    );
  }
}
```

**关键设计**：
- 用 class 组件实现（React Hooks 没有 ErrorBoundary 等价物）
- "重试" 清空 error 状态，组件树重新渲染
- "返回首页" 用 Link 做 SPA 跳转

**没做的事**：
- 没上 Sentry / 监控
- 没自动上报 error
- 是个 TODO

---

## 11. Vite 配置

```javascript
// vite.config.js
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),  // @ 别名指向 src
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',  // dev 模式代理后端
    },
  },
})
```

**dev / prod 差异**：
- dev：vite dev server 直接代理到 `localhost:8000`
- prod：nginx 容器代理 `/api/*` 到 `backend:8000`（docker compose 内网）

---

## 12. 构建产物分析

```
frontend/dist/
├── index.html               ~ 2 KB
├── assets/
│   ├── index-{hash}.js      主 bundle (Home + AuthContext + Sidebar)
│   ├── Interview-{hash}.js  懒加载 chunk
│   ├── Profile-{hash}.js    ...
│   └── ...
└── ...
```

主 bundle 大约 200 KB（gzip 60 KB），首屏加载 < 500ms（国内一般网络）。

**Nginx 缓存策略**：

```nginx
# 静态资源带 hash → 强缓存 1 年
location /assets/ {
  expires 1y;
  add_header Cache-Control "public, immutable";
}

# index.html → 永不缓存，每次拉最新（保证 hash 变了能命中新资源）
location / {
  add_header Cache-Control "no-cache, no-store, must-revalidate";
}
```

---

## 13. 类型系统

```typescript
// types/api.ts
export interface User {
  id: string;
  email: string;
  name: string;
}

export interface Question {
  id: number;
  question: string;
  difficulty: number;
  focus_area: string;
  category: string;
  pillar?: string;
}

export interface Profile {
  name: string;
  target_role: string;
  topic_mastery: Record<string, MasteryEntry>;
  previous_topic_mastery?: Record<string, MasteryEntry>;
  weak_points: WeakPoint[];
  strong_points: StrongPoint[];
  communication: { style: string; habits: string[]; suggestions: string[] };
  thinking_patterns: { strengths: string[]; gaps: string[] };
  preferences: { ... };
  stats: { ... };
}

// ... 等
```

**手动镜像后端 Pydantic**：

- 没用 OpenAPI 自动生成
- 改后端 schema 时要同步改前端 types
- 是个**已知缺点**，但项目规模小，手工维护成本可接受

**生产规模需要的事**：用 `openapi-typescript` 或 `pydantic2ts` 自动生成。

---

## 14. 关键页面清单

| 页面 | 行数 | 复杂点 |
|---|---|---|
| `Home.tsx` | ~500 | 4 种 mode 选择、topic 选择、上传简历 |
| `Interview.tsx` | ~900 | 双模式（batch / chat）共用、中途持久化、提示/参考答案 |
| `Review.tsx` | ~400 | Markdown 复盘渲染、收藏按钮、参考答案再生 |
| `Profile.tsx` | ~600 | 6 个图表、薄弱点 / 强项 / 偏好展示 |
| `Knowledge.tsx` | ~600 | 多文件 markdown 编辑、自动沉淀展示、索引重建进度 |
| `Graph.tsx` | ~300 | react-force-graph-2d 题目关联可视化 |
| `JobPrep.tsx` | ~600 | JD 输入 / preview / 答题三步流 |
| `QAArena.tsx` | ~700 | 会话列表 + 实时聊天 + 总结导出 |
| `AlgorithmSolver.tsx` | ~600 | 题目解析 + 多轮聊天 + 保存为卡片 |
| `Settings.tsx` | ~600 | LLM / Embedding / Reranker 多渠道配置 |
| `FloatingAssistant.tsx` | ~600 | 拖拽小窗 + 工具调用 action 接收 + 跨页面 |

---

## 15. 面试可能问的前端问题

### Q：为什么选 Vite 不是 Webpack / Turbopack？

A：Vite 的核心优势：
- 开发模式 ESM 原生加载，**HMR 几乎是秒级**，比 Webpack 快 10-100 倍
- 配置极简（vite.config.js 一个文件，10 行）
- 生产构建用 Rollup，tree-shaking 比 Webpack 好

不选 Turbopack 是因为它还在 beta，生态兼容性问题多。

### Q：React 19 的新特性你用了哪些？

A：
- **Concurrent rendering**：Suspense 用于懒加载，fallback 流畅
- **`use` hook 暂未用**（项目主要在 React 18 升级时建的，少数地方用 useTransition）
- **Server Components**：纯 SPA 不适用

实话说，**新特性用得不多**，React 19 主要是稳定性和性能改进，不是项目刚需。

### Q：SSE 客户端为什么不用 EventSource？

A：`EventSource` 有几个限制：
- 不支持 POST 请求（我们需要传 JSON body）
- 不支持 custom headers（我们要传 `Authorization`）

所以用 `fetch` + `ReadableStream.getReader()` 手写 SSE 解析。

### Q：怎么处理后端长时间响应（如 LangGraph 30s）？

A：
- 前端 fetch timeout 设 2 分钟（`SSE_TIMEOUT_MS = 120000`）
- 后端 30s 无 token 推 `ping` 事件保持连接
- Nginx `proxy_read_timeout 300s` + `proxy_buffering off`
- 浏览器看到的是持续的 progress 事件，不会触发自带的 timeout

### Q：组件之间怎么共享状态？

A：
- **认证**：AuthContext（React Context）
- **服务端状态**：每个页面自己 fetch + local state
- **URL 状态**：useSearchParams（如 history 页的筛选）
- **客户端持久状态**：localStorage（如 FloatingAssistant 拖拽位置）
- **跨页面跳转传参**：`navigate(path, { state: data })`

**没用 Redux/Zustand**，因为没有需要全局共享的复杂状态。

### Q：你的 TypeScript types 和后端 Pydantic 怎么同步？

A：**目前手动维护**。后端改了 schema 要去 `types/api.ts` 同步。

**已知缺点**：容易漂移、类型与实际不符。

**改进方向**：
1. 后端用 `openapi-typescript` 从 OpenAPI 文档生成 TS 类型
2. 或用 `pydantic2ts` 从 Pydantic model 直接转 TS
3. CI 检查 diff，schema 变了强制更新 types

诚实说这是个**已知 TODO**。

---

下一章 → [07 部署与运维](07_DEPLOYMENT.md)
