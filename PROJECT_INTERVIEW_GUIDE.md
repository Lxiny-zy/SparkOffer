# SparkOffer 项目 · Agent 岗位面试备战总索引

> 这是入口文件。详细内容拆分在 `interview-docs/` 子目录下分章节维护。
> 阅读顺序：先看本文 → 按"面试前 7 天复习路径"挑章节深入 → 模拟面试章节做闭卷自测。

---

## 1. 项目一句话（开口先抛这句）

**SparkOffer 是一个 AI 驱动的个性化面试训练系统。核心是构建了一个「训练 → 评估 → 画像更新 → 复习调度」的闭环：训练数据不会消失，而是沉淀成跨会话的能力画像，下一次出题融合三层上下文（长期画像 + 领域掌握度 + RAG 知识库）做个性化定题，让 AI 越练越懂你。**

代码规模：**后端 11.6k 行 Python，前端 10.2k 行 TypeScript**。

---

## 2. 30 秒电梯版本（背熟）

> 自我介绍后立刻抛出来的版本，控制在 60 秒内讲完。

"我做了个叫 SparkOffer 的 AI 面试训练系统。它跟市面上"出题—答题—结束"的工具不一样，整个系统设计成一个**可累积的能力建模闭环**：每答一道题都会更新长期画像，下次出题会融合「跨会话画像 + 领域掌握度 + 知识库 RAG」三层上下文。

技术栈是 FastAPI + LangGraph + LangChain + LlamaIndex + React 19。后端 1.1 万行 Python。

设计上我比较得意的几个点：

1. 借鉴 Mem0 做了**两阶段画像更新**（Extract + LLM 决策 ADD/UPDATE/IMPROVE），避免薄弱点列表无限堆叠
2. 自研了 **SQLite + numpy 的轻量向量检索**，百级数据规模下比 Milvus 更合适
3. LangGraph 状态机里埋了一个**隐藏 EVAL 标记**，让 LLM 自评是否推进阶段，配合三重护栏避免死循环
4. **多渠道 LLM 自动 failover** + Embedding **三态熔断器**，生产环境的鲁棒性都做了

整个项目所有 Prompt 中心化管理，新增岗位类型只要改 Prompt 不动代码。"

---

## 3. 3 分钟深度版本（被追问"详细说说"时用）

> 比 30s 版本多出来的部分是**用具体数据和场景**支撑设计决策。

"具体讲一下闭环怎么转：

**第一步是出题。** 用户选「Python」领域开始训练，系统先查这个用户该领域的画像 —— 比如掌握度 45/100、有 3 个未克服的薄弱点（"GIL 理解停在表面"、"asyncio 边界不清"等）；然后查间隔重复算法（SM-2）告诉我哪些薄弱点今天到期复习；最后用这些 weak_point 当 query 去拉知识库 RAG。**梯度策略**根据掌握度选难度区间：30 分以下出概念题、30-60 出场景题、60+ 出系统设计题。这些信息一股脑塞进 Prompt，LLM 一次性生成 10 道高度个性化的题。

**第二步是答题。** 用户作答用 SSE 流式推送，我们做了**增量 JSON 解析**，LLM 边吐字 token 边在前端逐题渲染，首屏从 30 秒降到 3 秒。

**第三步是评估。** 一次 LLM 调用批量评估 10 道题，返回每题分数、薄弱点、改进建议、整体观察。

**第四步是画像更新（最关键）。** 这里用了 Mem0 风格的两阶段：
- Stage 1（Extract）：让 LLM 从对话里提取本次新发现的薄弱点和强项
- Stage 2（Update）：再让 LLM 看「已有画像」+「新发现」，决定每条做 ADD / UPDATE / NOOP / IMPROVE 哪种操作

为什么不直接 append？因为同一个概念用户可能反复出错，描述也会变化。如果无脑 append，画像几十次训练后就变成几百条重复条目。Mem0 的思路是让 LLM 做语义合并，我们再加一层：LLM 解析失败时 fallback 到向量 cosine 去重（阈值 0.75）。

**第五步是入库 + 复习调度。** 新的薄弱点 embedding 后存进 SQLite BLOB；SM-2 算法计算这个薄弱点下次到期日（连续答对间隔越来越长，答错重置到 1 天）。

**第六步是知识沉淀。** 高分答案（>=7）让 LLM 提炼知识点写回知识库的「自动沉淀.md」，低分题进入高频题库 —— 知识系统会自我进化。

整套链路有几个工程鲁棒性设计：多渠道 LLM 自动 failover（key 轮询 + 冷却 + 健康检查）、Embedding 服务三态熔断器（CLOSED/OPEN/HALF_OPEN，5 次失败开路 60 秒后探活）、后台任务队列（优先级 + 指数退避重试）、SSE 心跳（30 秒无 token 推 ping 防代理超时）。"

---

## 4. 章节导航（按需深入）

| # | 文档 | 用途 | 优先级 |
|---|---|---|---|
| 01 | [架构详解](interview-docs/01_ARCHITECTURE.md) | 后端 / 前端目录结构 + 模块责任 + 代码规模 | ★★★ |
| 02 | [十大亮点代码级 trace](interview-docs/02_HIGHLIGHTS_DEEP.md) | 每个亮点附完整代码 trace + 设计权衡 + 替代方案 + 演讲稿 | ★★★★★ |
| 03 | [典型数据流](interview-docs/03_DATA_FLOWS.md) | 6 个用户操作从前端到数据库的完整 trace | ★★★★ |
| 04 | [数据库与存储设计](interview-docs/04_DATABASE_AND_STORAGE.md) | 9 张表 schema + 索引 + 文件目录布局 | ★★★ |
| 05 | [Prompt 工程深度](interview-docs/05_PROMPTS_DEEP.md) | 每个 Prompt 的目的、约束、踩过的坑 | ★★★★ |
| 06 | [前端架构](interview-docs/06_FRONTEND.md) | React 19 + Vite + Tailwind + SSE 客户端 | ★★ |
| 07 | [部署与运维](interview-docs/07_DEPLOYMENT.md) | Docker / Nginx / 环境变量 / 备份策略 | ★★ |
| 08 | [50+ 面试问答](interview-docs/08_INTERVIEW_QA.md) | 8 大类问题 × 完整回答提纲 | ★★★★★ |
| 09 | [模拟面试现场](interview-docs/09_MOCK_INTERVIEW.md) | 6 个真实场景对话演练 | ★★★★ |

---

## 5. 技术栈速查表

| 层 | 技术 | 项目里干什么 |
|---|---|---|
| Web 框架 | FastAPI 0.115+ | 异步路由 / Pydantic 校验 / SSE 流式 |
| Agent 编排 | LangGraph 0.2+ | 简历面试 5 阶段状态机 + MemorySaver 持久化 |
| LLM 抽象 | LangChain 0.3+ | ChatModel + Function Calling + astream |
| RAG 索引 | LlamaIndex 0.11+ | 简历索引 + 领域知识库索引 + 增量插入 |
| 向量存储 | **自研** SQLite BLOB + numpy | 百级数据无需 Milvus |
| 持久化 | SQLite (WAL) | 9 张表共一个 .db 文件 |
| 认证 | JWT (HS256) + bcrypt | 7 天过期 + path traversal 防护 |
| Embedding | BAAI/bge-m3 | API / local 双模式 |
| ASR | DashScope qwen3-asr + 七牛云 OSS | 录音复盘 |
| 前端 | React 19 + Vite 8 | Suspense + lazy 路由 |
| UI | Tailwind v4 + Radix UI | shadcn 风格 |
| 图表 | Recharts + react-force-graph-2d | 雷达图 / 趋势图 / 题目关联图 |
| 部署 | Docker Compose | 前端 nginx + 后端 uvicorn 双容器 |

---

## 6. 面试前 7 天复习路径

**Day 7（看看自己写了啥）** → 读 [01 架构详解](interview-docs/01_ARCHITECTURE.md)，对所有模块有印象。

**Day 6-5（重头戏）** → 精读 [02 十大亮点](interview-docs/02_HIGHLIGHTS_DEEP.md)，每个亮点都要做到「闭着眼睛能讲清楚 trace」。

**Day 4** → [03 数据流](interview-docs/03_DATA_FLOWS.md)，建立"从点击按钮到数据库行"的完整心智模型。

**Day 3** → [05 Prompt 工程](interview-docs/05_PROMPTS_DEEP.md)。Agent 岗位会重点考你怎么调 Prompt，这章必须熟。

**Day 2** → [08 面试问答](interview-docs/08_INTERVIEW_QA.md)，每题都过一遍。

**Day 1** → [09 模拟面试](interview-docs/09_MOCK_INTERVIEW.md) 做闭卷自测，找室友/家人扮面试官念题。

**面试当天上午** → 重读 30 秒和 3 分钟版本，准备好开场。

---

## 7. 关键数据备查（防止面试时答不上具体数字）

| 项 | 数字 | 出处 |
|---|---|---|
| 后端代码行数 | 11,650 | `wc -l backend/**/*.py` |
| 前端代码行数 | 10,251 | `wc -l frontend/src/**/*.{tsx,ts}` |
| Agent 工具数量 | 14 | `assistant.py:TOOLS` |
| LangGraph 阶段数 | 5 | `models.py:InterviewPhase` |
| 单用户向量上限 | 500 条 | `vector_memory.py:MAX_VECTORS_PER_USER` |
| 索引内存缓存 TTL | 1 小时 | `indexer.py:_INDEX_CACHE_TTL` |
| 索引最多缓存数 | 50 | `indexer.py:_INDEX_CACHE_MAX_SIZE` |
| Live session TTL | 2 小时 | `live_store.py:TTLDict(default_ttl=7200)` |
| JWT 过期时间 | 7 天 | `auth.py:JWT_EXPIRE_DAYS` |
| Embedding 超时 | 30s + 2 次重试 | `vector_memory.py:_EMBED_TIMEOUT_SECONDS` |
| 知识库检索超时 | 60s | `indexer.py:_RETRIEVAL_TIMEOUT` |
| 熔断阈值 | 5 次失败 → OPEN | `embedding_tasks.py:CircuitBreaker` |
| 熔断恢复时间 | 60s | 同上 |
| 渠道冷却 | 60s / 3 次错触发 | `channel_manager.py:COOLDOWN_SECONDS` |
| SQLite busy_timeout | 5s | `storage/database.py` |
| 每题答案最长存储 | 8000 字 | `assistant.py:MAX_RESPONSE_STORE_LENGTH` |
| 时间衰减半衰期 | 14 天 | `vector_memory.py:TIME_DECAY_HALF_LIFE` |
| 时间衰减最大权重 | 30% | `vector_memory.py:TIME_DECAY_WEIGHT` |
| 薄弱点语义去重阈值 | 0.75 | `vector_memory.py:SIMILARITY_THRESHOLD` |
| 题目图谱相似阈值 | 0.65 | `graph.py:SIMILARITY_THRESHOLD` |
| 上下文压缩阈值 | 20 条消息 | `qa_arena.py:COMPRESSION_THRESHOLD` |
| 摘要重生成间隔 | 每 10 条消息 | `qa_arena.py:SUMMARY_REGEN_INTERVAL` |
| 心跳间隔 | 30s 无 token | `utils/sse_helpers.py:IDLE_HEARTBEAT_SECONDS` |
| 进度反馈间隔 | 200 字符 | `utils/sse_helpers.py:PROGRESS_CHAR_INTERVAL` |
| SM-2 ease_factor 下限 | 1.3 | `spaced_repetition.py:sm2_update` |
| 自动毕业阈值 | 连续 3 次 ≥ 7 分 | `spaced_repetition.py:update_weak_point_sr` |

---

## 8. 容易翻车的红线（看完一定要记住）

1. **不要把 SparkOffer 描述成"RAG 应用"** —— 它是"RAG + LangGraph + Memory + Function Calling"的复合 Agent 系统，RAG 只是其中一层上下文。
2. **不要说"有 multi-agent collaboration"** —— 项目里**没有**多 Agent 协作，FloatingAssistant 是单 Agent + Tool Use，简历面试是 State Machine 不是 Agent。诚实表达「目前是单 Agent，理解 ReAct/AutoGen/CrewAI 的设计差异」。
3. **不要说"用了 Milvus / Pinecone"** —— 自研 SQLite + numpy 才是项目的工程亮点之一，说成主流向量库反而失分。
4. **不要把"画像系统"说成"会自己学习的 ML 模型"** —— 它本质是 LLM 提示工程 + 确定性算法（掌握度公式、SM-2）的组合，没用任何 fine-tuning。
5. **不要说"全异步"** —— LlamaIndex 的检索是同步的，我们用 `asyncio.to_thread` + `concurrent.futures.TimeoutError` 包了一层，要诚实说"混合同步异步，关键路径用 timeout 兜底"。
6. **不要说"评测体系完整"** —— 项目目前没有完整自动评测集，要诚实说"目前是人工抽样 + 用户反馈，已知 TODO"。
7. **不要因为面试官说"用 LLM 评分不靠谱"就慌** —— 我们的掌握度是**确定性公式**（`difficulty/5 × score/10`），不依赖 LLM 主观分；画像合并才用 LLM。

---

## 9. 反问环节准备

面试官问"你有问题问我吗"时，按重要度从高到低准备：

1. **"你们 Agent 团队对 multi-agent collaboration vs 单 agent + tool use 的取舍是怎么想的？什么场景下会选 multi-agent？"** — 体现你了解 Agent 设计前沿话题。
2. **"线上 Agent 的评估你们怎么做？是 LLM-as-Judge 还是有人工标注集？怎么避免 evaluator 漂移？"** — 体现你关注生产化痛点。
3. **"LLM 推理成本怎么控制？有没有用 prompt caching、context distillation、speculative decoding？"** — 体现你了解成本优化。
4. **"Agent 在生产环境最常见的 fail mode 是什么？怎么做监控和报警？"** — 直接问最痛的点，对方一定有共鸣。
5. **"你们的工具调用是用 OpenAI Function Calling 还是 ReAct prompt？切换成本是怎么评估的？"** — 体现你对工程选型的关注。

**注意**：不要问"晚加班吗"、"涨薪机制"这种 HR 问题 —— 留到 HR 面再问。技术轮的反问要全部是技术问题。

---

## 10. 文档版本

- 生成时间：2026-05-17
- 项目代码版本：commit `6dc5efe` (feat(prompts): 面向 Agent 岗位的内容质量提升 Phase 1)
- 维护策略：项目代码更新后，重点更新 02 章亮点 trace 和关键数据备查
