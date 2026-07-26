# 01 · 架构详解

> 完整理解项目骨架。重点：每个模块解决什么问题、为什么这么放、跟谁交互。

---

## 1. 仓库整体布局

```
SparkOffer/
├── backend/                # 后端（FastAPI + LangGraph）— 11.6k 行
│   ├── main.py             # 入口：FastAPI app + lifespan
│   ├── config.py           # pydantic-settings 配置层
│   ├── ai_config.py        # 运行时 AI 配置覆盖（JSON > .env）
│   ├── auth.py             # JWT + bcrypt
│   ├── models.py           # Pydantic / TypedDict 数据模型
│   ├── memory.py           # ★ Mem0 风格画像系统
│   ├── vector_memory.py    # ★ 自研向量检索
│   ├── indexer.py          # LlamaIndex 索引管理
│   ├── llm_provider.py     # ★ ResilientChatModel 多渠道 failover
│   ├── channel_manager.py  # 渠道优先级 / Key 轮询 / 冷却
│   ├── embedding_tasks.py  # ★ 后台 embedding 任务队列 + 熔断器
│   ├── spaced_repetition.py# SM-2 算法
│   ├── knowledge_evolution.py # 知识库自我进化
│   ├── live_store.py       # TTLDict + SQLite 持久化的 in-memory store
│   ├── assistant.py        # FloatingAssistant Agent (18 工具)
│   ├── qa_arena.py         # 问答演练场（长期记忆 + 上下文压缩）
│   ├── graph.py            # 题目关联图谱（语义相似度）
│   ├── formatters.py       # Review Markdown 生成
│   ├── migrate.py          # 单用户 → 多用户的一次性迁移脚本
│   ├── context_assembler.py# Token 预算上下文装配器
│   ├── rag_metrics.py       # 在线 RAG 检索健康指标（relevance/coverage/diversity）
│   ├── rag_eval.py          # 离线 RAGAS 基准评测
│   ├── rag_eval_retrievers.py # 离线评测用检索器
│   ├── rag_ids.py           # RAG chunk / doc ID 工具
│   ├── reranker.py          # Cross-Encoder 重排（可降级）
│   ├── redis_cache.py       # Redis 缓存（不可用回退内存 LRU）
│   ├── rate_limit.py        # 接口限流
│   ├── knowledge_training.py# 知识训练闪卡生成
│   ├── user_vector_migration.py # 用户向量迁移脚本
│   ├── graphs/             # LangGraph 工作流 + 出题/评估管道（10 个）
│   │   ├── resume_interview.py  # ★ 5 阶段状态机 + 隐藏 EVAL
│   │   ├── topic_drill.py       # 批量出题/评估（流式）
│   │   ├── drill_pipeline.py    # 分阶段流式出题管道
│   │   ├── rag_retrieval.py     # 多路召回 + RRF 融合 + 去重
│   │   ├── seed_pool.py         # 种子题池（压首屏延迟）
│   │   ├── difficulty_anchors.py# k-NN 难度锚点校准
│   │   ├── decoupled_eval.py    # 解耦式评估
│   │   ├── checkpointer.py      # 进程级 SqliteSaver 单例
│   │   ├── job_prep.py          # JD 解析 + 备面四件套
│   │   └── review.py            # 复盘报告生成
│   ├── prompts/            # ★ 系统 Prompts 中心化
│   │   ├── _common.py       # 评分标准 / 术语库 / 锚点示例（公共）
│   │   ├── interviewer.py   # 简历面试 + Drill 出题 + 画像更新
│   │   ├── job_prep.py      # JD 预览 / 出题 / 评估
│   │   ├── reviewer.py      # 复盘评估
│   │   ├── algorithm.py     # 算法解题陪练
│   │   ├── knowledge.py     # 知识库问答
│   │   ├── knowledge_training.py # 知识训练闪卡
│   │   ├── rag_eval.py      # 离线评测金标准合成
│   │   └── strategies.py    # 出题策略
│   ├── routers/            # FastAPI 路由（15 个）
│   │   ├── auth.py
│   │   ├── interview.py     # ★ 面试主入口（start / chat / end）
│   │   ├── job_prep.py
│   │   ├── profile.py
│   │   ├── knowledge.py     # 知识库 CRUD + 后台重建
│   │   ├── knowledge_training.py # 知识训练闪卡
│   │   ├── algorithm.py
│   │   ├── favorites.py
│   │   ├── assistant.py
│   │   ├── graph_router.py
│   │   ├── qa_arena.py
│   │   ├── rag_eval.py      # 离线 RAG 评测任务
│   │   ├── resume.py
│   │   ├── debug.py         # 调试端点
│   │   └── settings_router.py
│   ├── storage/            # DAO 层（SQLite CRUD）
│   │   ├── database.py      # 连接管理 + 表迁移
│   │   ├── sessions.py      # 面试会话
│   │   ├── favorites.py
│   │   ├── algorithm.py
│   │   ├── assistant_chats.py
│   │   ├── qa_sessions.py
│   │   ├── knowledge_cards.py # 知识训练闪卡 + SM-2
│   │   ├── rag_metrics_store.py # 在线 RAG 指标
│   │   ├── rag_eval_store.py    # 离线评测结果
│   │   ├── audit.py         # 安全审计日志
│   │   └── live_sessions.py # 进行中会话持久化
│   └── utils/
│       ├── sse_helpers.py   # ★ SSE 流式响应工具
│       └── stream_parser.py # ★ 增量 JSON 解析器
├── frontend/               # 前端（React 19 + Vite）— 10.2k 行
│   ├── src/
│   │   ├── App.tsx          # 路由 + AuthContext + 全局 ErrorBoundary
│   │   ├── pages/           # 19 个页面
│   │   ├── components/      # UI + Charts + FloatingAssistant
│   │   ├── api/             # API 客户端（authFetch + SSE）
│   │   ├── contexts/        # AuthContext
│   │   ├── hooks/           # useDraftPersist / useTilt
│   │   ├── types/           # TypeScript 类型
│   │   └── utils/
│   ├── nginx.conf           # 生产环境 Nginx 配置
│   └── Dockerfile           # 多阶段构建（node build → nginx serve）
├── data/                   # 运行时数据（gitignored）
│   ├── interviews.db        # SQLite 主库（WAL 模式）
│   ├── ai_config.json       # 运行时 AI 配置覆盖
│   ├── topics.example.json  # 13 个默认主题
│   ├── knowledge/           # 全局知识库（首次注册时拷贝到用户目录）
│   ├── users/{user_id}/     # 用户隔离目录
│   │   ├── profile/profile.json
│   │   ├── profile/insights/{YYYY-MM-DD}.md
│   │   ├── resume/          # 上传的 PDF
│   │   ├── knowledge/       # 用户的领域知识库
│   │   ├── high_freq/{topic}.md  # 高频题
│   │   ├── topics.json      # 用户自定义主题列表
│   │   └── .index_cache/    # LlamaIndex 持久化
│   └── qa_notes/{user_id}/  # 问答演练场总结
├── scripts/                # 运维脚本
├── docker-compose.yml      # 编排
├── requirements.txt        # Python 依赖
├── requirements.local-embedding.txt # 本地 embedding 可选依赖
└── README.md
```

---

## 2. 后端分层（按依赖方向，从下往上）

```
┌─────────────────────────────────────────────────────────┐
│  L5  Routers (FastAPI)        ★ 入口，处理 HTTP/SSE      │
│  ─── interview.py, profile.py, knowledge.py, ...        │
├─────────────────────────────────────────────────────────┤
│  L4  Business Logic           ★ 核心逻辑                  │
│  ─── memory.py, assistant.py, qa_arena.py,              │
│      knowledge_evolution.py, spaced_repetition.py       │
├─────────────────────────────────────────────────────────┤
│  L3  Workflows (LangGraph)    ★ Agent / 状态机            │
│  ─── graphs/resume_interview, topic_drill, job_prep     │
├─────────────────────────────────────────────────────────┤
│  L2  Infrastructure                                      │
│  ─── llm_provider, channel_manager,                     │
│      indexer (RAG), vector_memory,                      │
│      embedding_tasks (queue)                            │
├─────────────────────────────────────────────────────────┤
│  L1  Storage (DAO)                                       │
│  ─── storage/*.py (CRUD), live_store (TTL+persist)      │
├─────────────────────────────────────────────────────────┤
│  L0  Foundation                                          │
│  ─── config, ai_config, auth, models, utils             │
└─────────────────────────────────────────────────────────┘
```

**依赖规则**：上层可以引用下层，下层不引用上层。比如 `storage/sessions.py` 不会知道 `routers/interview.py` 存在。

**违反规则的地方**（诚实暴露）：
- `memory.py` 引用了 `prompts/interviewer.py:PROFILE_UPDATE_PROMPT`，理论上 Prompt 是 L3 业务资产，但放在 prompts/ 包里方便管理
- `memory.py` 反向引用 `embedding_tasks.schedule_session_memory_index`（在 llm_update_profile 末尾触发后台索引），这是个 L4 → L2 的合理调用
- `auth.py` 调用了 `storage/database.py`，正常依赖

---

## 3. 模块责任矩阵

### 3.1 核心业务模块

| 模块 | 行数 | 责任 | 对外暴露 |
|---|---|---|---|
| `memory.py` | 769 | 用户画像 CRUD + Mem0 两阶段更新 | `get_profile`, `get_profile_summary`, `llm_update_profile`, `update_profile_after_interview`, `update_profile_realtime` |
| `vector_memory.py` | 466 | 向量化记忆 + 时间衰减 + 语义去重 | `search_memory`, `find_similar_weak_point`, `index_session_memory`, `rebuild_index_from_profile` |
| `embedding_tasks.py` | 515 | 后台 embedding 任务队列 + 熔断器 | `get_task_queue`, `get_circuit_breaker`, `schedule_*` 系列 |
| `assistant.py` | 953 | FloatingAssistant Agent | `stream_assistant_chat`, `generate_welcome_back` |
| `qa_arena.py` | 347 | 自由问答 + 上下文压缩 + 总结导出 | `stream_qa_chat`, `stream_generate_summary` |
| `knowledge_evolution.py` | 113 | 自动知识沉淀 + 高频题收集 | `extract_and_writeback`, `collect_high_freq` |
| `spaced_repetition.py` | 137 | SM-2 算法 + 自动毕业 | `sm2_update`, `get_due_reviews`, `update_weak_point_sr` |
| `graph.py` | 178 | 题目关联图谱构图 | `build_graph` |

### 3.2 LangGraph 工作流

| 文件 | 行数 | 模式 | 核心节点 / 函数 |
|---|---|---|---|
| `resume_interview.py` | 247 | **真正的 LangGraph 状态机** | init / ask / advance / wait + SqliteSaver（data/checkpoints.db，经 `graphs/checkpointer.py`）+ interrupt_before |
| `topic_drill.py` | 308 | **批量调用（不用 LangGraph）** | `generate_drill_questions`, `stream_evaluate_drill_answers` |
| `job_prep.py` | 454 | 批量调用 + 流式 | preview / questions / evaluate 三步 |
| `review.py` | 148 | 单次 LLM 调用 | `generate_review`, `stream_generate_review` |

**注意**：drill / job_prep 用的是"批量调用 + 流式响应"模式而不是 LangGraph，因为它们没有状态机需求（10 题一次生成、一次评估，前端按需展示）。LangGraph 只在 resume_interview 这种有真正阶段切换需求的场景才用。

### 3.3 Routers（15 个）按职责

| Router | 主要 endpoints | 备注 |
|---|---|---|
| `auth` | `/api/auth/login`, `/register`, `/me` | JWT 颁发 |
| `interview` | `/api/interview/start`, `/chat`, `/end/{id}` | **主入口**，处理三种 mode |
| `interview` (续) | `/api/interview/session/{id}/progress` | 中途进度保存（debounced） |
| `interview` (续) | `/api/interview/reference-answer` | 参考答案 / 提示流式生成 |
| `job_prep` | `/api/job-prep/preview`, `/start` | JD 解析 + 备面 |
| `profile` | `/api/profile`, `/topics`, `/topic/{t}/retrospective` | 画像 + 主题 + 回顾 |
| `knowledge` | `/api/knowledge/{topic}/{core,high_freq,rebuild}` | 知识库 CRUD + 后台重建 |
| `algorithm` | `/api/algorithm/{solve,chat,save,cards}` | 算法陪练 |
| `favorites` | `/api/favorites` | 收藏夹 |
| `assistant` | `/api/assistant/{chat,history,welcome}` | 小鱼 Agent |
| `graph_router` | `/api/graph/{topic}` | 题目图谱 |
| `qa_arena` | `/api/qa-arena/sessions/*` | 问答演练 |
| `resume` | `/api/resume/{upload,status}` | 简历上传 |
| `settings_router` | `/api/settings/ai`, `/api/settings/channels` | 运行时 AI 配置 |

---

## 4. 关键 Singleton / 全局状态

```python
# llm_provider.py
_embedding_instance   # Singleton（按 config_version 失效）
_llama_llm_instance   # 同上

# indexer.py
_index_cache: dict[(user_id, topic), (expire, VectorStoreIndex)]
_INDEX_CACHE_TTL = 3600.0  # 1 小时
_INDEX_CACHE_MAX_SIZE = 50
_rebuild_locks: dict[(user_id, topic), asyncio.Lock]

# embedding_tasks.py
_circuit_breaker = EmbeddingCircuitBreaker()
_task_queue = EmbeddingTaskQueue(max_workers=2, max_queue_size=100)

# channel_manager.py
_manager = ChannelManager()  # 多渠道状态机
# 内部：_channels[section] / _states[section][channel_id]

# live_store.py — 4 个 TTLDict
graphs: TTLDict(default_ttl=7200, max_size=100)        # LangGraph 实例
drill_sessions: TTLDict(default_ttl=7200, max_size=200)
job_prep_sessions: TTLDict(default_ttl=7200, max_size=200)
algorithm_sessions: TTLDict(default_ttl=7200, max_size=200)

# memory.py
_profile_locks: dict[user_id, threading.Lock]  # 防 profile.json 并发写

# storage/database.py
_local = threading.local()  # 每线程 SQLite 连接
```

**全局状态的设计原则**：
1. **线程安全**：所有可变全局都加锁（threading.Lock 或 asyncio.Lock）
2. **可观测**：所有缓存都有 TTL + 上限，防止内存泄漏
3. **可恢复**：进行中的 session 同时存内存 + SQLite，重启不丢

---

## 5. 启动流程（main.py lifespan）

```
1. init_config()              加载 data/ai_config.json + 迁移老格式
   → _reload_channel_manager() 把渠道喂给 ChannelManager
2. init_all_tables()           CREATE TABLE IF NOT EXISTS × 16
3. 检查 JWT_SECRET 默认值     未改提示警告
4. get_embedding()             首次创建 embedding 实例（API 或 local）
5. _init_llama_settings()      把 LLM + embedding 注入 LlamaIndex 全局
6. cleanup_expired_sessions()  清理 24h 前的 live_sessions
7. ensure_default_user()       创建 .env 配的默认账号
8. 日志：多渠道概览（每个 section 启用了几个）
9. get_task_queue().start()    启动 2 个 worker 协程
```

**优雅停机**：

```
yield  # FastAPI 接管
get_task_queue().stop()  # 取消所有 worker 任务
```

---

## 6. 前端架构概览

```
React 19 + Vite 8 + TypeScript 6 + Tailwind v4
              ↓
     BrowserRouter (react-router-dom v7)
              ↓
  ┌──────────────────────────┐
  │  AuthContext (token + user) │
  └──────────────────────────┘
              ↓
  ┌──────────────────────────┐
  │   ErrorBoundary (全局兜底) │
  └──────────────────────────┘
              ↓
  ┌──────────────────────────────────────┐
  │  AppShell                              │
  │   ├── Sidebar                          │
  │   ├── <Suspense fallback=Loader>       │
  │   │     <Routes>                       │
  │   │       /login                       │
  │   │       /interview/:id (lazy)        │
  │   │       /review/:id    (lazy)        │
  │   │       /profile        (lazy)       │
  │   │       /knowledge      (lazy)       │
  │   │       /graph          (lazy)       │
  │   │       /job-prep       (lazy)       │
  │   │       /qa-arena       (lazy)       │
  │   │       /algorithm      (lazy)       │
  │   │       /favorites      (lazy)       │
  │   │       /settings       (lazy)       │
  │   │     </Routes>                      │
  │   ├── FloatingAssistant (always)       │
  │   └── Toaster (sonner)                 │
  └──────────────────────────────────────┘
```

**关键设计**：
- **所有非首页路由 lazy 加载**：减少首屏 bundle
- **AuthContext 在路由外**：token 失效时全局触发 `onSessionExpired`
- **SessionExpiredModal**：401 不强制跳转，弹层提示"重新登录"防止丢数据
- **FloatingAssistant 全局**：在任何页面都能召唤小鱼

---

## 7. 配置体系（三层覆盖）

```
优先级（高 → 低）：
┌─────────────────────────────────────────┐
│ Runtime override (data/ai_config.json)   │  ← 用户在 Settings 页面改的
├─────────────────────────────────────────┤
│ Multi-channel config (data/ai_config.json:channels) │  ← 多渠道独立配置
├─────────────────────────────────────────┤
│ .env (环境变量)                          │  ← 部署时设的
├─────────────────────────────────────────┤
│ pydantic-settings defaults               │  ← 兜底
└─────────────────────────────────────────┘
```

实现：`ai_config.get_effective(section, key)`：先查 JSON 覆盖，再查 .env，最后查默认。

**热重载**：`save_ai_config()` 写完 JSON 后：
1. `_config_version += 1` 触发 LLM/Embedding singleton 失效（下次调用重建）
2. `_reload_channel_manager()` 立即把新渠道配置喂给 ChannelManager

---

## 8. 用户数据隔离

```
data/users/{user_id}/                  ← user_id = 8 位十六进制
  ├── profile/
  │   ├── profile.json                 # 画像主文件
  │   └── insights/{YYYY-MM-DD}.md     # 每日洞察日志
  ├── resume/                          # PDF
  ├── knowledge/                       # 用户的领域知识库
  │   ├── 01_Python核心/
  │   ├── 02_LLM基础/
  │   └── ...
  ├── high_freq/{topic}.md             # 高频题库（按主题）
  ├── topics.json                      # 自定义主题
  └── .index_cache/{topic_or_resume}/  # LlamaIndex 持久化
```

**安全保证**：
- `auth.py:_USER_ID_PATTERN = r"^[a-f0-9]{8}$"`：JWT 解码后强校验格式，防 path traversal
- 所有 DAO 函数都强制 `user_id` 参数，SQL 必定带 `WHERE user_id = ?`
- live_store 的 session 入字典前校验 `entry["user_id"] == user_id`

**首次注册**：`auth._init_user_knowledge(user_id)` 把全局 `data/knowledge/` 拷贝到用户目录，作为初始知识。

---

## 9. 13 个默认知识主题（恰好对应 Agent 岗位面试范围）

```
python              → 01_Python核心
llm                 → 02_LLM基础
agent               → 03_Agent架构
rag                 → 04_RAG
function_calling    → 05_Function_Calling
mcp                 → 06_MCP
langchain           → 07_LangChain_LangGraph
prompt              → 08_Prompt_Engineering
database            → 09_数据库与中间件
memory              → 10_记忆管理
backend             → 11_后端八股
leetcode            → leetcode
docker              → docker
```

**为什么是这 13 个**：覆盖 Agent 工程师面试 100% 考点 —— Python 语言、LLM 基础、Agent 设计、RAG、Tool Use、MCP、LangChain 生态、Prompt 工程、数据库中间件、记忆管理、后端基础、算法、容器化。

---

## 10. 代码量分布（值得跟面试官说的数字）

```
backend 总计:    11,650 行
├── memory.py / vector_memory.py / embedding_tasks.py：~1.7k 行（记忆系统大头）
├── assistant.py：             953 行（FloatingAssistant 占了 1/10）
├── graphs/*：                 1.2k 行（LangGraph 工作流）
├── prompts/*：                1.2k 行（提示词）
├── routers/*：                ~2k 行（15 个 router）
├── storage/*：                ~1k 行（DAO）
└── 其他（auth / config / indexer 等）：~3.6k 行

frontend 总计:   10,251 行
├── pages/*：                  ~7k 行（19 个页面）
├── components/*：             ~2k 行
├── api/*：                    ~1k 行
└── hooks / utils / types：    ~250 行
```

面试时可以说：「整个项目 22k 行代码，后端逻辑权重更大，前端主要是 UI 编排」。

---

下一章 → [02 十大亮点代码级 trace](02_HIGHLIGHTS_DEEP.md)
