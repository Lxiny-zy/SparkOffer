# SparkOffer · 项目技术与知识点全解

> 本文档分两大部分：
> **Part 1** 用「简历项目介绍」的口径，列清技术栈与创新点；
> **Part 2** 把项目里涉及到的**每一个知识点**展开讲透——既能当面试复习手册，也能当从零理解这套系统的教科书。
>
> 所有结论都基于对当前代码库（commit `c268427`）的逐行勘察，关键处标注了 `file:line` 出处。

---

## 文档地图

### Part 1 · 简历项目介绍

| 文件 | 内容 |
|---|---|
| [01_项目简介与技术栈.md](01_项目简介与技术栈.md) | 一句话定位 · 解决的问题 · 分层技术栈 · 系统架构图 · **十大创新点（简历口径）** · 代码规模 |

### Part 2 · 知识点全解析

| 文件 | 覆盖的知识点 |
|---|---|
| [02_Agent编排与LangGraph.md](02_Agent编排与LangGraph.md) | LangGraph 状态机 · 节点/边/条件边 · Checkpointer 持久化 · `interrupt_before` 人在环 · 隐藏 EVAL 标记 · 三重护栏 · LangChain ChatModel · Function Calling · 多轮工具循环 · 单 Agent vs ReAct vs Multi-Agent |
| [03_RAG检索增强.md](03_RAG检索增强.md) | RAG 原理 · LlamaIndex · Embedding/向量/余弦 · Chunk 策略 · 多路召回 · **RRF 倒数排名融合** · 语义去重 · **Cross-Encoder 重排** · Dense vs Sparse/BM25 · RAG 质量提示 · 评测指标（Recall@k / Hit-rate / MRR / Faithfulness） |
| [04_记忆与个性化.md](04_记忆与个性化.md) | **Mem0 两阶段画像更新** · 自研向量记忆（SQLite BLOB + numpy）· 时间衰减 · 薄弱点语义匹配 · 掌握度 EWMA 算法 · **SM-2 间隔重复** · 文件即真相/原子写/文件锁 · 知识自进化 |
| [05_工程鲁棒性.md](05_工程鲁棒性.md) | **多渠道 failover** · Key 轮询 · 冷却与 HALF_OPEN 探活 · **三态熔断器** · 后台任务队列（优先级/去重/指数退避）· `ResilientChatModel` 两阶段流式 failover · 超时/降级模式 · 连接池复用 · 配置热重载 |
| [06_流式与上下文工程.md](06_流式与上下文工程.md) | SSE 原理 · 心跳/keepalive · reasoning 模型流式 · **增量 JSON 解析** · **Token 预算装配器**（tiktoken + CJK 启发式）· Prompt 前缀缓存 · 上下文压缩（map-reduce 摘要） |
| [07_Web后端与存储.md](07_Web后端与存储.md) | FastAPI · 异步编程（asyncio/to_thread/gather/semaphore）· SQLite WAL · 9 张表 schema · JWT + bcrypt · 路径穿越防护 · 用户隔离目录 · ASGI/uvicorn |
| [08_评测体系.md](08_评测体系.md) | 离线评测矩阵（persona × strategy × judge）· **LLM-as-Judge 多模型投票** · 确定性 judge（coverage / KL 散度 / diversity）· 基线对比方法论 · 在线 RAG 指标 |
| [09_前端工程.md](09_前端工程.md) | React 19 · Hooks · Suspense/lazy 代码分割 · React Router v7 · Vite · Tailwind v4 + Radix（shadcn）· Recharts/力导向图 · SSE 客户端 · TypeScript |
| [10_部署与可选能力.md](10_部署与可选能力.md) | Docker Compose · nginx 反代（流式调优）· 可选依赖策略（Redis/Qdrant/本地 embedding/reranker） |

### Part 3 · 功能实施方案

| 文件 | 内容 |
|---|---|
| [11_知识训练场实施计划.md](11_知识训练场实施计划.md) | 基于现有知识库、RAG、SSE、前端路由与导航设计「知识训练场」：模块选择、随机知识点、例子、自测问题、隐藏答案、接口与迭代计划 |

### Part 4 · 当前代码全量审查

| 文件 | 内容 |
|---|---|
| [12_全量代码分析报告_Docker部署版.md](12_全量代码分析报告_Docker部署版.md) | 基于当前源码的全模块地图、端到端数据流、完整 API 目录、Docker 配置覆盖与持久化边界、数值公式与阈值推导、正常/降级/补偿链路、边界条件保证矩阵、风险分级、测试基线和上线迁移清单 |

---

## 怎么用这份文档

- **想快速了解项目** → 只读 Part 1（01）。
- **准备面试 / 想把每个名词都讲清楚** → Part 1 打底，再按主题刷 Part 2。每个知识点都遵循「**是什么 → 为什么需要 → 本项目怎么实现 → 设计权衡**」四段式。
- **想对照源码** → 每节都带 `file:line`，直接跳代码。

---

## 一句话记住这个项目

> **SparkOffer 不是「RAG 应用」，而是 `RAG + LangGraph 状态机 + Mem0 式长期记忆 + Function Calling` 的复合 Agent 系统**——把每一次面试训练沉淀成跨会话的能力画像，让出题「越练越懂你」，并用一整套确定性算法 + 生产级鲁棒性兜住 LLM 的不确定性。
