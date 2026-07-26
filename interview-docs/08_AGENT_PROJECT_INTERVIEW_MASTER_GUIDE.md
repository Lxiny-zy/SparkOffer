# SparkOffer Agent 项目面试全解：功能、原理、实现路径与追问

> 用途：用于 Agent 开发、LLM 应用、RAG、AI 后端岗位的简历提炼和项目面试准备。
> 事实基线：以当前仓库代码为准；涉及运行基础设施时，以 **Docker Compose 版本**为准。
> Docker 在本文中只是运行环境边界，不是讲解重点。

---

## 1. 先把项目讲准确

### 1.1 一句话定位

SparkOffer 是一个能够持续理解用户能力变化的 AI 面试训练系统：它将个性化出题、回答评估、长期记忆、能力画像、知识库 RAG、SM-2 复习调度和下一轮训练串成闭环，使系统不只是“生成一道题”，而是能根据用户历史表现持续调整训练内容。

### 1.2 30 秒项目介绍

> 我做了一个面向技术求职者的个性化 AI 面试训练系统。项目没有把所有任务都塞进一个自由 Agent，而是按任务确定性选择三种编排方式：简历模拟面试用 LangGraph 构建可中断、可恢复的状态机；专项训练和 JD 备面使用带 RAG、难度策略和质量校验的 Agentic Workflow；站内学习助手使用 Function Calling，根据用户问题调用画像、历史、知识库和训练工具。每次训练结束后，系统会更新长期记忆、领域掌握度和 SM-2 复习计划，再用于下一轮个性化出题。

### 1.3 两分钟项目介绍

> 这个项目解决的是普通 AI 面试工具“每次从零开始”的问题。传统做法通常只把主题交给 LLM 临时出题，无法知道用户过去答过什么、哪些知识点反复出错、什么难度最合适，也无法形成长期训练效果。
>
> 我的设计是一个闭环系统。用户开始专项训练时，系统先加载长期画像、当前领域掌握度、薄弱点、到期复习项和最近题目；然后针对不同薄弱点分别构造检索 query，从 Qdrant 知识库召回上下文，经过融合、语义去重和可选 reranker，再按用户掌握度生成不同难度分布的题目。生成结果还要经过重复度、薄弱点覆盖和难度分布等 validator。
>
> 用户答题后，评估链路会并发进行逐题评分，再生成整体总结。可计算的掌握度、统计和 SM-2 调度使用确定性算法更新；需要语义理解的薄弱点、强项、思维模式和沟通风格，则通过类似 Mem0 的 ADD、UPDATE、IMPROVE、NOOP 操作合并进长期画像，并向量化供以后检索。
>
> Agent 编排上，简历面试有明确阶段、人在回路和恢复要求，所以使用 LangGraph；专项训练步骤稳定，所以使用可观测流水线；学习助手面对开放意图，所以使用有限循环的 Function Calling Agent。工程上还实现了上下文 token 预算、SSE 心跳、多模型渠道 failover、Embedding 后台队列与熔断、在线 RAG 指标和离线评测。

### 1.4 面试中最重要的真实性边界

1. 项目不是多 Agent 系统。
2. 当前真正的自由工具选择 Agent 是 FloatingAssistant。
3. 简历面试是“外层确定性状态机 + 节点内 LLM 决策”的受控 Agent。
4. Topic Drill、JD Prep 属于 Agentic Workflow，不应说成自由自治 Agent。
5. 当前业务主库是 SQLite，不是 PostgreSQL。
6. Docker 版本实际使用 Redis 和 Qdrant；不是本地 numpy 主路径。
7. 当前是单机、单 Uvicorn worker 的完整产品原型，不应声称已经支撑大规模用户。

---

## 2. 当前 Docker 运行版本只需要记住什么

Docker 版本下，项目的 Agent 能力运行在以下基础设施上：

| 组件 | 当前选择 | 对 Agent 功能的意义 |
|---|---|---|
| 前端 | React + Nginx | 展示流式题目、评估、工具动作和可视化 |
| Agent 后端 | FastAPI + 单进程 Uvicorn | 承载状态机、工作流、工具执行和 SSE |
| 业务存储 | SQLite WAL | 会话、评分、收藏、卡片、审计、QA 记录 |
| LangGraph 状态 | 独立 SQLite checkpoint | 简历面试中断与恢复 |
| 缓存 | Redis | 缓存加速；不可用时代码可回退内存 LRU |
| 向量数据库 | Qdrant | Docker 版知识库和长期记忆检索主路径 |
| Embedding | 外部 API | query embedding、知识库构建、记忆向量化 |
| LLM | OpenAI-compatible 多渠道 | 出题、评估、画像抽取、工具调用和总结 |

面试时只需要说明：

> 项目存在裸机和 Docker 两种运行方式。我的项目介绍以 Docker 运行版本为准，所以 RAG 和长期记忆向量走 Qdrant，缓存走 Redis，业务事实和 LangGraph checkpoint 仍分别使用 SQLite 文件持久化。

---

## 3. 产品功能全景

### 3.1 核心训练功能

1. 简历 PDF 上传和解析。
2. 基于简历的多阶段模拟面试。
3. 按技术主题进行个性化专项训练。
4. 输入 JD 后进行岗位技能拆解和定向备面。
5. 批量回答、逐题评分、整体复盘和参考答案。
6. 历史会话、答题进度恢复和重新评估。

### 3.2 个性化学习功能

1. 用户长期画像。
2. 领域掌握度。
3. 薄弱点、优势点、思维模式和沟通风格。
4. SM-2 到期复习。
5. 基于历史表现的难度自适应。
6. 长期记忆语义检索。

### 3.3 知识与 RAG 功能

1. 多领域 Markdown 知识库。
2. 知识文件增删改查和上传。
3. Qdrant 向量索引。
4. 增量重建、索引预热和异常自愈。
5. 多 query 检索、融合、去重和可选 rerank。
6. 在线 RAG 质量指标和离线 RAGAS 风格评测。
7. 从训练或 QA 对话中反向沉淀新知识。

### 3.4 Agent 辅助功能

1. 全站 FloatingAssistant。
2. 画像、历史、趋势、收藏、知识库等工具查询。
3. 页面导航和训练入口动作。
4. 算法题分析、追问和算法卡片管理。
5. Q&A Arena 多轮问答、图片输入、总结和知识入库。
6. 知识训练卡生成与复习。

### 3.5 工程与平台功能

1. LLM、Embedding、Reranker 多渠道配置。
2. 大小模型 tier 路由。
3. 渠道失败冷却和自动切换。
4. SSE 心跳与增量 JSON 解析。
5. Embedding 后台任务队列和三态熔断。
6. 用户数据隔离、JWT、登录限流和审计。

---

## 4. 整个个性化训练闭环

```text
用户选择训练主题
  ↓
加载三层用户上下文
  ├─ 长期画像：跨领域弱点、思维模式、偏好
  ├─ 领域状态：mastery、近期得分、到期 SM-2 项
  └─ 当前知识：Qdrant RAG、最近题目、难度锚点
  ↓
个性化检索与出题
  ├─ 为不同薄弱点拆 query
  ├─ 多路向量召回
  ├─ 融合、去重、可选 rerank
  ├─ 按掌握度选择难度分布
  └─ validators 检查题目质量
  ↓
用户批量回答
  ↓
逐题评估 + 整体总结
  ↓
双轨更新
  ├─ 确定性：分数、mastery、统计、SM-2
  └─ LLM：薄弱点、强项、思维和沟通发现
  ↓
Mem0 风格合并 + 记忆向量化 + 知识沉淀
  ↓
下一轮训练重新读取更新后的状态
```

这条闭环是项目最核心的业务价值，也是面试介绍的主线。

---

## 5. 为什么项目采用三种编排范式

| 场景 | 编排方式 | 原因 |
|---|---|---|
| 简历模拟面试 | LangGraph 状态机 | 阶段明确、需要人在回路、条件跳转和 checkpoint |
| Topic Drill / JD Prep | Agentic Workflow | 步骤稳定、需要进度可观测、方便质量门控与降级 |
| FloatingAssistant | Function Calling Agent | 用户意图开放，需要动态选择工具和组合结果 |

### 面试回答

> 我没有为了使用 Agent 框架而把所有业务都做成 ReAct 循环。我的原则是：确定性越强的部分越应该由程序控制，只有真正需要语义判断和动态决策的局部才交给模型。这样更容易控制成本、延迟、权限、停止条件和失败恢复。

---

## 6. LangGraph 简历模拟面试 Agent

### 6.1 用户功能

1. 上传简历后启动模拟面试。
2. 面试官按阶段追问。
3. 根据上一轮回答决定继续追问或切换阶段。
4. 达到终止条件后结束。
5. 容器重启后恢复面试状态。
6. 面试结束生成整体复盘并更新画像。

### 6.2 实现路径

```text
frontend/src/pages/Interview.tsx
  -> POST /api/interview/start
  -> backend/routers/interview.py
  -> backend/graphs/resume_interview.py
  -> backend/graphs/checkpointer.py
  -> data/checkpoints.db
  -> backend/graphs/review.py
  -> backend/memory.py
```

### 6.3 Graph 结构

```text
START
  ↓
init
  ↓
wait  ← interrupt_before
  ├─ ask      继续当前阶段追问
  ├─ advance  切换到下一阶段
  └─ END      面试结束
```

核心节点：

1. `init`：加载简历、画像和相关上下文，生成首问。
2. `wait`：暂停，等待真实用户回答。
3. `ask`：评估回答并生成追问。
4. `advance`：重置阶段题数并进入下一阶段。
5. `route_after_answer`：根据状态决定下一条边。

### 6.4 人在回路如何实现

Graph 在 `wait` 前中断。用户回答通过新的 HTTP 请求提交，服务端使用同一个 `thread_id=session_id` 更新 state，再继续 invoke。

这不是把用户回答保存在普通聊天 history 后重新调用，而是恢复同一条 Graph execution。

### 6.5 checkpoint 如何工作

1. `thread_id` 使用 session id。
2. `SqliteSaver` 将 Graph state 写入独立 `checkpoints.db`。
3. 进程内 graph 对象丢失后，可根据 `live_sessions` 中的元数据重新 compile。
4. 新 graph 使用相同 thread id 后读取原 checkpoint。

### 6.6 如何避免无限追问

1. 阶段顺序由程序定义。
2. 每阶段存在最大问题数。
3. 路由同时考虑 LLM 评估和确定性题数上限。
4. 最后一阶段后强制 END。
5. Graph 路径不是由模型随意创建。

### 6.7 为什么不直接 while 循环

普通循环也能实现，但 LangGraph 更适合表达：

1. 显式状态结构。
2. 条件路由。
3. interrupt/resume。
4. checkpoint。
5. 节点级观测和后续扩展。

代价是状态 schema、checkpoint 兼容和副作用幂等更复杂。

---

## 7. Topic Drill 个性化训练 Agentic Workflow

### 7.1 用户功能

1. 选择一个技术领域。
2. 系统根据个人薄弱点生成一组题。
3. 前端实时显示画像加载、RAG、生成和校验进度。
4. 用户统一作答。
5. 系统并发逐题评分并给出整体复盘。
6. 结果更新画像、SM-2、长期记忆和知识库。

### 7.2 实现路径

```text
POST /api/interview/start-stream
  -> backend/graphs/drill_pipeline.py
  -> backend/memory.py
  -> backend/spaced_repetition.py
  -> backend/graphs/rag_retrieval.py
  -> backend/indexer.py
  -> backend/graphs/seed_pool.py
  -> backend/graphs/difficulty_anchors.py
  -> backend/graphs/validators/
  -> backend/graphs/topic_drill.py
  -> backend/graphs/decoupled_eval.py
  -> backend/routers/interview.py::_persist_drill
```

### 7.3 三层上下文

#### 第一层：长期用户画像

包含跨会话弱点、优势、思维模式、沟通风格和历史洞察。

#### 第二层：当前领域状态

包含领域 mastery、近期得分、该领域薄弱点、最近题目和到期复习项。

#### 第三层：知识库 RAG

从 Docker 版 Qdrant 的 `kb_{user}_{topic}` collection 检索和当前训练目标相关的知识片段。

### 7.4 为什么拆分多个检索 query

如果把多个薄弱点直接拼成一个长 query，单个 embedding 会把不同语义平均化，召回容易偏向其中一个主题。

项目将每个薄弱点或目标分别检索，再融合候选：

```text
弱点 A -> top-k
弱点 B -> top-k
复习点 C -> top-k
          ↓
        融合与去重
```

### 7.5 RRF 的意义

不同 query 的相似度分数不能直接比较。RRF 主要根据每个结果在各列表中的排名进行融合：

```text
score(d) = Σ 1 / (k + rank_i(d))
```

优势：

1. 不要求不同召回列表分数同尺度。
2. 多个 query 都排在前面的结果自然得到更高权重。
3. 比简单拼接更稳定。

### 7.6 为什么还需要语义去重和 rerank

1. 向量库可能召回内容高度相似的多个 chunk。
2. 重复上下文浪费 token，还会让模型过度关注某一点。
3. 语义去重负责增加信息覆盖。
4. Cross-Encoder reranker 联合阅读 query 和 chunk，精度通常高于独立 embedding cosine。
5. rerank 成本更高，因此只对较小候选集执行，并允许未配置时跳过。

### 7.7 难度自适应

项目不是让模型自由决定难度，而是先根据 mastery 得到目标难度分布，再把分布作为生成约束。

例如：

1. 低掌握度：基础概念、现象解释和简单应用更多。
2. 中掌握度：原理、边界、比较和问题定位更多。
3. 高掌握度：设计权衡、性能、故障场景和系统设计更多。

### 7.8 validators 为什么重要

LLM 生成题目后，还要经过确定性或 embedding 校验：

1. `semantic_duplicate`：检查题目和最近题目是否重复。
2. `weak_point_coverage`：检查是否覆盖目标薄弱点。
3. `difficulty_distribution`：检查难度分布是否偏离目标。

这体现了“模型负责候选生成，程序负责质量门控”。

### 7.9 为什么这是 Agentic Workflow 而不是普通固定流水线

路径虽然由程序控制，但内部会根据画像、检索结果、校验结果和模型输出动态调整题目内容、难度与覆盖目标，因此具备环境感知和状态驱动；不过它没有开放式工具循环，所以应准确称为 Agentic Workflow。

---

## 8. JD 定向备面 Workflow

### 8.1 用户功能

1. 输入岗位 JD、公司和职位。
2. 系统识别技术要求、职责、重点能力和潜在追问方向。
3. 可选择融合用户简历。
4. 生成岗位定向问题。
5. 完成评估并更新画像。

### 8.2 实现路径

```text
frontend/src/pages/JobPrep.tsx
  -> backend/routers/job_prep.py
  -> backend/graphs/job_prep.py
  -> backend/indexer.py
  -> backend/memory.py
  -> backend/routers/interview.py
```

### 8.3 为什么先 preview 再出题

preview 相当于一个中间结构：先把开放文本 JD 转换为岗位、公司、技能矩阵和重点方向，再基于该结构生成问题。

好处：

1. 用户可以检查模型是否理解了 JD。
2. 出题 Prompt 不必反复携带完整原文。
3. 评估阶段可以复用同一岗位结构。
4. 方便未来缓存、版本化和人工修正。

---

## 9. FloatingAssistant Function Calling Agent

### 9.1 用户功能

用户可以用自然语言询问：

1. 我的薄弱点有哪些？
2. 最近训练得怎么样？
3. 某个领域的得分趋势是什么？
4. 哪些内容到期需要复习？
5. 帮我查某次面试的完整记录。
6. 在知识库中解释一个技术问题。
7. 帮我进入专项训练或 JD 备面页面。

### 9.2 实现路径

```text
frontend/src/components/FloatingAssistant.tsx
  -> backend/routers/assistant.py
  -> backend/assistant.py::stream_assistant_chat
  -> llm.bind_tools(TOOLS)
  -> _execute_tool
  -> 画像/历史/收藏/知识库/算法卡片等模块
```

### 9.3 工具类型

#### 查询型工具

1. 用户画像摘要与完整画像。
2. 历史会话和 session 详情。
3. 完整面试 transcript。
4. 得分趋势和训练统计。
5. 到期复习和薄弱点详情。
6. 收藏、算法卡片和领域列表。
7. 长期记忆语义检索。
8. 领域知识库检索。

#### 动作型工具

1. 页面导航。
2. 启动某种面试或训练。

动作工具不直接操作浏览器 DOM，而是返回结构化 action，由前端执行。

### 9.4 Agent 循环

```text
用户消息
  ↓
LLM 判断是否需要工具
  ├─ 不需要 -> 直接回答
  └─ 需要 -> 生成 tool_calls
              ↓
         服务端校验并执行
              ↓
         ToolMessage 回填
              ↓
         LLM 继续组织答案/再次调用
```

### 9.5 为什么限制循环轮数

1. 防止模型不断调用同一工具。
2. 控制 token、延迟和费用。
3. 防止工具失败形成死循环。
4. 让异常能尽快返回用户。

### 9.6 为什么同轮多个工具并发执行

例如“比较 Java 和 Python 最近得分并告诉我到期复习内容”可能同时需要趋势和复习工具。这些查询互不依赖，可以使用 `asyncio.gather` 并发，整体延迟接近最慢工具，而不是所有工具耗时相加。

### 9.7 如何防止越权

1. `user_id` 来自 JWT 认证依赖，不允许模型传入。
2. 工具集合是 allowlist，模型不能执行任意代码。
3. 数据访问层按 user id 查询。
4. session 工具再次校验资源归属。
5. 当前写操作较少；高风险写操作未来应增加显式确认和幂等 key。

### 9.8 Function Calling 和 MCP 的关系

Function Calling 是模型输出结构化工具调用的能力；MCP 是标准化工具、资源和 Prompt 接入协议。

当前项目工具直接定义在应用代码中，没有实现 MCP Server。面试时可以说未来如果需要把工具复用给多个 Agent 客户端，可封装为 MCP，但不能声称当前已经使用 MCP。

---

## 10. 长期记忆与用户画像

### 10.1 Memory 和聊天历史的区别

聊天历史记录“说过什么”；长期记忆记录“未来任务仍有价值的用户事实”。

项目长期记忆包括：

1. 稳定薄弱点。
2. 已形成的强项。
3. 领域掌握度。
4. 思维习惯和沟通风格。
5. 历史训练洞察。

### 10.2 实现路径

```text
训练评估结果
  -> backend/memory.py::update_profile_after_interview
  -> 确定性 profile 更新
  -> backend/memory.py::llm_update_profile
  -> ADD / UPDATE / IMPROVE / NOOP
  -> profile.json 原子写入
  -> backend/vector_memory.py
  -> Qdrant sparkoffer_memory collection
```

### 10.3 为什么不能直接 append

直接追加会导致：

1. 同一薄弱点出现大量同义记录。
2. 已改善问题仍长期影响出题。
3. 画像越来越长，Prompt 成本持续增长。
4. 新旧结论冲突时无法确定真相。

### 10.4 Mem0 风格操作

1. `ADD`：全新的长期发现。
2. `UPDATE`：同一事实发生补充或变化。
3. `IMPROVE`：原薄弱点已经改善，迁移为已掌握状态。
4. `NOOP`：没有新增价值，不修改画像。

### 10.5 为什么确定性更新和 LLM 更新分开

#### 适合确定性算法

1. 得分。
2. 次数。
3. mastery 计算。
4. SM-2 interval。
5. 均分和趋势。

#### 适合 LLM

1. 多段评价中抽取共同薄弱点。
2. 判断两条自然语言记忆是否同一事实。
3. 总结思维模式和沟通特征。
4. 将新发现合并进旧画像。

这样避免让 LLM 控制本可精确计算的状态。

### 10.6 并发一致性

1. 每个用户拥有独立 lock。
2. 画像 read-modify-write 在事务范围内完成。
3. 文件保存使用临时文件替换，降低半写入风险。
4. 耗时 LLM 操作应尽量放在锁外，最终合并时重新读取最新状态。

### 10.7 向量记忆检索

Docker 版使用 Qdrant：

1. 记忆文本被 embedding。
2. 写入共享 memory collection。
3. payload 保存 user、topic、类型和时间。
4. 查询时按 user id 过滤。
5. 语义分数与时间衰减组合。
6. 相似度约 0.75 用于去重。
7. 每用户向量数量设置上限，避免长期无界增长。

### 10.8 时间衰减的意义

用户三个月前的薄弱点可能已经不再重要。项目加入约 14 天半衰期的时间权重，但时间只占有限比例，避免最新但无关的记忆压过真正语义相关的旧记录。

---

## 11. 掌握度、EWMA 与 SM-2

### 11.1 三者分别解决什么

| 机制 | 解决的问题 |
|---|---|
| 逐题 LLM 评分 | 本次回答质量如何 |
| mastery/EWMA | 最近一段时间该知识点掌握得怎样 |
| SM-2 | 下一次应该什么时候复习 |

### 11.2 难度加权掌握度

项目先根据题目难度和得分得到贡献值，再使用 EWMA 更新：

```text
contribution = difficulty / 5 × score / 10 × 100
new_mastery = 0.7 × old_mastery + 0.3 × contribution
```

这样高难度高分贡献更大，同时近期表现比很久以前更重要。

### 11.3 SM-2 核心逻辑

1. 将 0-10 分映射到 0-5 quality。
2. 通过：间隔从 1 天、3 天逐步增长。
3. 失败：repetitions 清零，间隔重置 1 天。
4. ease factor 最低 1.3。
5. 到期项在下次出题时获得更高优先级。
6. 连续高分后薄弱点可标记 improved。

### 11.4 当前需要如实说明的问题

当前测试中，“旧记录 repetitions=2 且缺少 consecutive_high，本次高分是否直接毕业”的历史兼容规则与测试存在冲突。面试时不必主动展开，但如果谈测试，可以说明项目已发现旧状态迁移语义需要统一，而不是声称所有测试全绿。

---

## 12. RAG 知识库实现

### 12.1 数据来源

1. 共享技术知识文档。
2. 用户自定义领域知识。
3. 用户上传文件。
4. LLM 生成的核心知识。
5. QA Arena 或训练过程中沉淀的新知识。

### 12.2 实现路径

```text
知识文件
  -> backend/indexer.py
  -> LlamaIndex 文档解析与 chunk
  -> Embedding API
  -> Qdrant kb_{user}_{topic}
  -> query_topic / retrieve_topic_context
  -> 出题、问答、评估上下文
```

### 12.3 Docker 版 Qdrant 隔离方式

知识库 collection 名带 user 和 topic。这样不同用户的自定义文档不会混入其他用户检索结果。

长期记忆则采用共享 collection + payload user filter，因为每用户记忆规模较小，统一 collection 更便于管理。

### 12.4 增量索引

1. 为知识文件保存 hash manifest。
2. 重建时对比新增、修改和删除文件。
3. 仅对变更文件重新解析和 embedding。
4. 首次构建、embedding 维度变化、空 collection 或显式 force 时全量重建。
5. 构建任务进入后台队列，不长期阻塞请求。

### 12.5 索引缓存

1. 进程内缓存已加载索引。
2. TTL 约 1 小时。
3. 最多约 50 个用户/topic 索引。
4. 启动后后台预热常用知识库。

### 12.6 Qdrant 不可用怎么办

Docker 配置选择 qdrant 后，不会静默切换到 numpy，因为静默切换会产生两套数据真相。检索上层会返回空上下文或降级结果，并安排后台重试/重建。

这是“功能降级但不污染数据”的取舍。

---

## 13. 上下文工程

### 13.1 为什么不能把所有信息直接塞给模型

1. 会超过模型 context window。
2. 成本和首 token 延迟上升。
3. 无关信息会稀释关键约束。
4. 工具 schema 和输出也需要 token 空间。

### 13.2 ContextBudget

`backend/context_assembler.py` 将上下文拆成多个 Section，每段包含：

1. 优先级。
2. 最小保留预算。
3. 最大 token。
4. 是否可截断。
5. 超预算时是压缩、截断还是丢弃。

### 13.3 常见优先级

1. 系统安全和输出格式约束最高。
2. 当前用户问题和当前训练目标必须保留。
3. 关键薄弱点、简历/JD 重点优先。
4. RAG 候选按相关性装配。
5. 较旧聊天历史最先删除。

### 13.4 token 计算

项目使用 tiktoken；不可用时才回退字符估算。截断尽量落在段落、换行或句子边界，避免从一个 JSON/Markdown 结构中间截断。

---

## 14. LLM 多渠道与模型路由

### 14.1 实现路径

```text
业务模块
  -> backend/llm_provider.py::ResilientChatModel
  -> backend/channel_manager.py
  -> 可用 channel
  -> OpenAI-compatible API
```

### 14.2 为什么需要多渠道

1. 单供应商可能 429、5xx 或超时。
2. 不同任务需要不同模型能力和成本。
3. 小模型适合逐题评分、抽取和分类。
4. 大模型适合复杂生成和整体总结。

### 14.3 Failover 流程

1. 选择符合 tier 的健康渠道。
2. 发起调用。
3. 瞬时错误可短暂重试。
4. 仍失败则记录错误并进入 cooldown。
5. 选择下一个渠道。
6. 成功后清理失败状态。

### 14.4 哪些错误不应切换渠道

参数错误、认证配置错误、非法模型名等确定性 4xx，换渠道通常不能解决。对这类错误应快速失败，避免把全部渠道错误冷却。

### 14.5 流式请求为什么首 token 后不能无缝切换

如果已经向用户发送半段回答，再换模型重跑，会将两个模型的输出拼在一起。项目只允许首 chunk 前 failover；首 chunk 后异常由上层明确结束或提示重试。

### 14.6 tier 路由

逐题独立评分可以发给 small tier 并发执行，最后总结再由更强模型完成。这种“任务拆分 + 模型分层”比所有步骤统一使用最强模型更节省成本和延迟。

---

## 15. Embedding 后台队列与熔断器

### 15.1 为什么要异步化

知识库重建、会话记忆入库、画像向量重建不是用户当前响应必须等待的结果。如果全部 inline await：

1. API 延迟显著增加。
2. Embedding 服务抖动会拖垮在线请求。
3. 客户端断开会导致任务丢失。

### 15.2 支持的任务

1. 全量/增量知识索引重建。
2. 新知识增量插入。
3. 用户画像向量重建。
4. 训练会话长期记忆入库。

### 15.3 三态熔断

```text
CLOSED
  ├─ 连续失败达到阈值 -> OPEN
OPEN
  ├─ 冷却时间未到 -> 拒绝任务调用
  └─ 冷却结束 -> HALF_OPEN
HALF_OPEN
  ├─ 探测成功 -> CLOSED
  └─ 探测失败 -> OPEN
```

### 15.4 任务队列还做什么

1. 优先级调度。
2. 指数退避。
3. 状态记录。
4. 启停生命周期管理。
5. 限制后台任务对外部 API 的冲击。

---

## 16. SSE 流式交互与可靠性

### 16.1 使用 SSE 的场景

1. 简历面试回复。
2. Topic Drill 出题阶段进度。
3. 逐题评估进度。
4. QA Arena 流式回答。
5. 长对话总结。

### 16.2 为什么选择 SSE 而不是 WebSocket

这些场景主要是服务端持续向浏览器推送，用户的下一次输入仍可通过普通 HTTP 提交。SSE 协议简单、事件语义清晰、浏览器支持好。

### 16.3 heartbeat

模型推理、检索或工具调用期间可能几十秒没有文本。`sse_helpers.py` 定时发送 ping，防止 Nginx 或上游代理将连接视为空闲。

### 16.4 Nginx 配合

Docker 前端 Nginx 对 `/api/`：

1. 关闭 proxy buffering。
2. 关闭 cache。
3. 延长 read/send timeout。
4. 保持 HTTP/1.1 长连接。

### 16.5 增量 JSON 解析

批量出题时，LLM 可能逐 token 输出 JSON 数组。前端不必等待整个数组完成；后端 parser 可识别已经闭合的对象，逐题发送给前端，改善感知延迟。

### 16.6 客户端断开后的副作用

1. 答案先写入持久化存储。
2. 关键画像/复盘写入使用 shield 或后台任务。
3. 重复评估通过状态标记避免重复累计。
4. 简历面试状态由 checkpoint 保存。

---

## 17. Q&A Arena 与知识沉淀

### 17.1 功能

1. 独立多轮问答会话。
2. 文本和图片输入。
3. 流式回答与重新生成。
4. 长对话 map-reduce 总结。
5. 下载 Markdown 总结。
6. 将总结写入个人知识库并增量索引。

### 17.2 上下文压缩

长对话不直接全部放入 Prompt：

1. 保留近期消息。
2. 较早消息形成滚动 summary。
3. 用户消息和助手消息设置不同长度上限。
4. 总结任务按 chunk map，再 reduce 为结构化笔记。

### 17.3 Agent 相关价值

QA Arena 展示了 Agent 系统中“短期会话记忆、长期记忆和外部知识”的区别：近期对话用于当前连贯性，历史 summary 用于压缩，稳定知识最终进入 RAG 库。

---

## 18. 知识自我进化

### 18.1 来源

1. 训练过程中反复出现的高频薄弱点。
2. QA Arena 生成的高质量总结。
3. 用户手动保存的知识训练卡。

### 18.2 写回链路

```text
对话/训练结果
  -> LLM 提取可复用知识
  -> 写入用户知识文件
  -> schedule_incremental_insert
  -> Embedding task queue
  -> Qdrant collection
```

### 18.3 风险控制

模型输出不能直接无条件污染知识库。需要结构化抽取、来源记录、长度限制、重复检测；进一步生产化应增加人工确认、版本和回滚。

---

## 19. RAG 评测体系

### 19.1 在线健康指标

`backend/rag_metrics.py` 位于真实出题链路，低成本计算：

1. relevance。
2. coverage。
3. diversity。

它们没有人工 ground truth，主要用于发现召回质量是否突然退化。

### 19.2 离线 RAGAS 风格评测

`backend/rag_eval.py`：

1. 从知识库合成 golden set。
2. 执行 hit@k、strict hit、MRR、precision、recall。
3. 用 LLM Judge 计算 faithfulness、answer relevancy、correctness。
4. 保存每次 run，支持前端对比。

### 19.3 个性化策略评测

项目还可以比较：

1. personalized：真实画像 + RAG + 难度策略。
2. topic_only：只告诉模型主题。
3. random_baseline：从知识内容随机生成。

主要指标：

1. 薄弱点覆盖率。
2. 难度分布与目标的 KL divergence。
3. 题目语义多样性。
4. LLM Judge 综合质量。

### 19.4 为什么不能只用 LLM Judge

LLM Judge 有位置偏差、模型偏好和不可复现问题。项目应将确定性指标、embedding 指标、LLM Judge 和人工抽检结合，而不是用单一分数证明效果。

---

## 20. 数据与用户隔离

### 20.1 业务事实

SQLite 保存：

1. 用户。
2. 训练 session、题目、回答、评分和复盘。
3. 收藏和算法卡片。
4. Assistant/QA 会话。
5. RAG 指标与评测结果。
6. 知识训练卡。
7. 审计日志。

### 20.2 文件数据

```text
data/users/{user_id}/
├─ resume/
├─ profile/
├─ knowledge/
├─ high_freq/
├─ qa_uploads/
├─ qa_notes/
└─ topics.json
```

### 20.3 隔离原则

1. 路径统一由 `backend/config.py` helper 生成。
2. SQL 查询带 user id。
3. KB collection 带 user/topic。
4. Memory collection 使用 payload user filter。
5. 工具执行注入认证 user id。
6. session、图片和文件接口再次校验归属。

---

## 21. 完整功能到源码导航

| 功能 | 前端 | API/业务实现 |
|---|---|---|
| 简历面试 | `pages/Interview.tsx` | `routers/interview.py`、`graphs/resume_interview.py` |
| 专项训练 | `pages/Interview.tsx` | `graphs/drill_pipeline.py`、`graphs/topic_drill.py` |
| JD 备面 | `pages/JobPrep.tsx` | `routers/job_prep.py`、`graphs/job_prep.py` |
| 画像与趋势 | `pages/Profile.tsx`、`TopicDetail.tsx` | `routers/profile.py`、`memory.py` |
| 历史复盘 | `pages/History.tsx`、`Review.tsx` | `storage/sessions.py`、`routers/profile.py` |
| 知识库 | `pages/Knowledge.tsx` | `routers/knowledge.py`、`indexer.py` |
| 知识训练 | `pages/KnowledgeTraining.tsx` | `knowledge_training.py` |
| QA Arena | `pages/QAArena.tsx` | `qa_arena.py` |
| Floating Agent | `components/FloatingAssistant.tsx` | `assistant.py` |
| RAG 看板 | `pages/RAGDashboard.tsx` | `rag_metrics.py`、`rag_eval.py` |
| 算法助手 | `pages/AlgorithmSolver.tsx` | `routers/algorithm.py`、`prompts/algorithm.py` |
| 题目图谱 | `pages/Graph.tsx` | `graph.py` |
| 模型渠道 | `pages/Settings.tsx` | `ai_config.py`、`channel_manager.py`、`llm_provider.py` |

---

## 22. 面试高频问题与项目答案

### Q1：为什么这是 Agent 项目，不是普通聊天机器人？

因为系统具备持久状态、条件决策、工具调用、环境反馈和跨轮执行。简历面试根据阶段和回答评估决定下一步；Assistant 根据意图选择工具；训练 Workflow 根据画像和检索结果动态生成并校验题目。

### Q2：是不是用了 LangGraph 就是 Agent？

不是。LangGraph 只是编排工具。如果图只是固定 A 到 B，本质还是 workflow。项目的 Agent 特征来自条件路由、人在回路、状态恢复和节点内的语义决策。

### Q3：为什么不用一个万能 ReAct Agent？

训练系统的大量规则必须稳定，例如阶段顺序、最大题数、评分持久化和用户隔离。万能 Agent 会增加循环、权限、成本和不可复现问题。因此采用外层程序护栏，局部开放模型决策。

### Q4：Workflow 和 Agent 的边界是什么？

Workflow 的路径主要由程序预定义；Agent 的下一步主要由模型根据观察决定。生产系统常见做法是外层 workflow 保证边界，局部 Agent 处理不确定性。

### Q5：LangGraph state 中保存什么？

保存 messages、当前阶段、阶段问题数、最近评估、是否结束等恢复执行必需的信息；可从业务数据库重新计算的大型数据不应全部塞进 state。

### Q6：checkpoint 会不会造成副作用重复？

会，所以节点重放时必须区分纯计算和写操作。项目将关键会话写入和画像副作用放在受控结束链路，并使用 synced/already_scored 等状态避免重复处理。更完整方案是 event id + 幂等表。

### Q7：RAG 为什么按薄弱点拆 query？

一个 embedding 同时表达多个主题会产生语义平均，召回偏向主导主题。拆 query 后分别召回，再融合，可以提高不同薄弱点的覆盖率。

### Q8：向量召回、去重和 rerank 分别解决什么？

向量召回解决高召回率；去重解决上下文重复和 token 浪费；rerank 解决 embedding 双塔模型精排能力有限的问题。

### Q9：如何处理 embedding 模型升级？

新旧模型维度和向量空间不兼容。项目检测 collection 维度与当前 embedding，变化时触发全量重建；生产中还应对 index 增加 model/version 元数据并做双写切换。

### Q10：为什么长期记忆不直接用数据库全文？

结构化画像适合精确读取，向量记忆适合从大量历史中按当前问题召回相关片段。两者互补：画像是事实源，向量库是可重建的检索索引。

### Q11：如何避免错误记忆一直影响用户？

采用操作式合并、相似去重、时间衰减和 improved 状态；进一步应提供用户查看、编辑、删除和来源追踪功能，并在高风险记忆写入前要求确认。

### Q12：为什么 mastery 不交给 LLM？

掌握度需要稳定、可解释和可回归。难度与得分可直接计算，使用确定性 EWMA 更合适；LLM 只负责抽取自然语言层面的新洞察。

### Q13：为什么同时需要 mastery 和 SM-2？

mastery 表示会不会，SM-2 表示何时复习。一个是能力估计，一个是时间调度。

### Q14：Function Calling 工具参数错误怎么办？

先用 schema 限制字段和枚举，服务端再次校验。错误作为结构化 ToolMessage 回填，允许模型修复一次；超过循环上限后返回明确错误。

### Q15：Prompt Injection 如何处理？

知识文档和工具结果属于不可信数据，不能覆盖 system 指令。工具权限由代码控制，不由文档决定；高风险写操作需要确认；检索内容应标记来源，输出前进行引用和安全检查。

### Q16：为什么选择 SSE？

主要需求是服务端单向推送 token 和进度，用户输入仍通过普通 HTTP。SSE 更简单，并能通过事件类型表达 progress、question、action、error 和 done。

### Q17：如何避免异步 event loop 被阻塞？

LlamaIndex 等同步磁盘、网络或 CPU 操作用 `asyncio.to_thread` 包装；外部 API 并发由 semaphore 控制，避免无界 gather。

### Q18：为什么并发不是越高越好？

Embedding/LLM 服务有并发和速率限制。无界并发会同时触发 429 和超时，实际吞吐反而下降。并发度应结合上游限额、耗时和成本压测。

### Q19：如何证明个性化有效？

与 topic-only、random baseline 对比，观察薄弱点覆盖、难度分布、多样性和 Judge 质量；线上还需要真实用户学习效果和 A/B，当前离线评测不能替代生产实验。

### Q20：当前项目最大的不足是什么？

当前是完整单机产品原型：SQLite、单 worker 和本地文件画像不适合大规模多实例；自动化测试覆盖不足；真实用户长期学习效果尚未通过线上实验验证。

---

## 23. 系统设计追问

### 23.1 如果扩展到十万用户

1. FastAPI 无状态化并水平扩容。
2. SQLite 迁移 PostgreSQL。
3. LangGraph checkpoint 迁移共享持久化后端。
4. 文档和图片放对象存储。
5. Embedding、评估和画像总结进入分布式任务队列。
6. Qdrant 按 tenant 做 payload/collection 规划和分片。
7. 统一 LLM Gateway 负责模型路由、限流、费用和审计。
8. SSE 事件持久化并支持 last-event-id 断线续传。
9. 接入 OpenTelemetry/LangSmith 类 trace。

### 23.2 如果改成多 Agent

只有在角色具备独立上下文、权限或并行价值时才拆分，例如：

1. Interviewer Agent 负责追问。
2. Evaluator Agent 独立评分。
3. Memory Agent 负责画像合并。
4. Safety/Reviewer Agent 做最终审查。

但多 Agent 会增加通信 token、状态一致性、循环和错误定位成本。当前项目用状态机、流水线和不同模型 tier 已经实现职责分离，没有必要为了概念强拆。

### 23.3 如果工具允许写操作

1. 工具分只读、可逆写和不可逆写风险等级。
2. 写入前展示结构化计划和影响范围。
3. 高风险操作要求用户确认。
4. 每次写操作带 idempotency key。
5. 保存审计日志。
6. 执行后重新读取真实状态验证。
7. 为可逆操作提供补偿或撤销。

---

## 24. 项目亮点排序

面试中建议按下面顺序主讲，不要平均介绍所有页面。

### 第一亮点：个性化闭环

训练结果不是只展示给用户，而是更新画像、记忆、复习调度和下一次出题。

### 第二亮点：按任务选择 Agent 编排

LangGraph 状态机、Agentic Workflow、Function Calling Agent 三种范式各自解决适合的问题。

### 第三亮点：三层上下文与 RAG

长期画像、领域状态和 Qdrant 知识上下文共同决定问题，而不是简单 topic Prompt。

### 第四亮点：Mem0 风格长期记忆

LLM 操作式合并与确定性状态更新分离，避免画像无限追加。

### 第五亮点：工程鲁棒性

SSE 心跳、多渠道 failover、后台 embedding、熔断、checkpoint 和幂等副作用。

### 第六亮点：评测意识

在线检索健康、离线 RAGAS 和个性化 baseline 对比，避免只展示主观 Demo。

---

## 25. 简历 bullet 候选

1. 设计并实现个性化 AI 面试训练系统，将出题、逐题评估、长期画像、向量记忆、SM-2 复习调度和下一轮训练构建为跨会话闭环。
2. 基于 LangGraph 构建多阶段、人在回路的简历面试 Agent，使用 SQLite checkpoint 按 session 持久化执行状态并支持服务重启恢复。
3. 构建 Topic Drill Agentic Workflow，融合用户画像、领域掌握度、到期复习项与 Qdrant RAG，通过多 query 召回、融合、语义去重、可选 rerank 和 validators 生成个性化题目。
4. 实现 Function Calling 学习助手，将画像、历史、趋势、收藏、长期记忆和知识库封装为受控工具，支持同轮多工具并发和结构化前端 action。
5. 设计 Mem0 风格画像更新机制，以 ADD、UPDATE、IMPROVE、NOOP 合并长期发现，并将确定性 mastery/SM-2 更新与 LLM 语义抽取解耦。
6. 实现 LLM 多渠道 failover、大小模型 tier、运行时热配置以及 Embedding 优先级队列、指数退避和三态熔断器。
7. 设计 SSE 事件协议、心跳和增量 JSON 解析，配合 Nginx 禁用代理缓冲，支持长时间出题、评估、工具调用和多模态问答。
8. 建立在线 RAG 健康指标、离线 RAGAS 风格评测与个性化策略 baseline，对召回、忠实度、覆盖率、难度分布和多样性进行验证。

---

## 26. 面试前必背的 20 个问题

1. 用 30 秒介绍项目。
2. 为什么这是 Agent，不是聊天机器人？
3. 为什么不用一个万能 ReAct Agent？
4. 三种编排方式分别解决什么问题？
5. LangGraph interrupt 和 checkpoint 如何工作？
6. 如何避免 Agent 无限循环和重复副作用？
7. 专项训练三层上下文是什么？
8. 为什么按薄弱点拆多个 RAG query？
9. RRF、语义去重、rerank 分别做什么？
10. Qdrant 中知识库和长期记忆如何隔离？
11. 长期记忆为什么不能直接 append？
12. ADD、UPDATE、IMPROVE、NOOP 如何工作？
13. mastery、EWMA 和 SM-2 有什么区别？
14. Function Calling 循环和工具权限如何控制？
15. 多 LLM 渠道如何 failover？
16. 为什么流式首 token 后不能切模型？
17. Embedding 后台任务和熔断器如何工作？
18. SSE heartbeat 解决什么问题？
19. 如何评估 RAG 和个性化效果？
20. 当前项目不足和十万用户演进方案是什么？

建议每题准备三层答案：一句结论、60 秒项目实现、继续追问时的原理与取舍。

---

## 27. 最后的表达原则

1. 先讲业务问题，再讲 Agent 决策链，最后讲框架。
2. 不要把每个调用 LLM 的函数都称为 Agent。
3. 不要把单 Agent 项目包装成多 Agent。
4. 说明哪些状态由程序计算，哪些语义交给模型。
5. 每个亮点都准备“为什么这样做、替代方案、失败会怎样”。
6. 所有性能数字必须有真实测试证据，没有就不写。
7. 当前版本已实现的能力和未来演进方案要明确区分。
8. 涉及向量后端时明确说：本文按 Docker 版本，实际走 Qdrant。

一个可信的结尾是：

> 这个项目让我最深的认识是，Agent 工程的重点不是把更多决策交给模型，而是建立清晰的状态、工具、权限、上下文、停止条件和失败边界。模型负责处理真正不确定的语义问题，确定性业务仍由程序控制，最终再通过评测证明整个闭环是否有效。
