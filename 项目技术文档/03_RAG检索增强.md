# Part 2 · 03 — RAG 检索增强

> 本章把 SparkOffer 的检索链路讲透：从 RAG 的基本原理、LlamaIndex 索引，到**多路召回 + RRF 融合 + 语义去重 + Cross-Encoder 重排**这条出题主链路，再到"为什么不堆知识库体积、而是先修评测尺子"的 RAG 方法论。

---

## 1. RAG 是什么，为什么要用

### 1.1 定义

**RAG（Retrieval-Augmented Generation，检索增强生成）**：在让 LLM 生成内容前，先从外部知识源**检索**出相关片段，拼进 prompt 作为"事实依据"，再让 LLM 基于这些片段生成。一句话——**先查资料，再答题**。

### 1.2 为什么需要

LLM 有三个硬伤，RAG 各治一个：

| LLM 硬伤 | RAG 怎么治 |
|---|---|
| **幻觉**：会一本正经编造 | 把真实片段喂进去，让生成"有据可依" |
| **知识截止**：训练数据有 cutoff | 检索的是你**实时维护**的知识库 |
| **私域无知**：不知道你的简历/项目/错题 | 把私域文档索引进去检索 |

### 1.3 本项目的"双层知识增强"

SparkOffer 的 RAG 是**两层**的（这点要讲清，它不是单一知识库问答）：

1. **领域知识库**：用户维护的 Markdown 文档（python/java/agent 等），出题与评分的事实依据。
2. **历史训练记忆**：训练产生的洞察（薄弱点、错误模式）自动向量化入库，下次出题语义召回（这层在 04 章细讲）。

---

## 2. Embedding 与向量检索：RAG 的物理基础

### 2.1 Embedding（嵌入）

**Embedding** 把一段文本映射成一个**高维向量**（本项目用 `BAAI/bge-m3`，1024 维）。核心性质：**语义相近的文本，向量也相近**。于是"找相关文档"就变成"找向量空间里离 query 最近的点"。

本项目支持 API / 本地双模式（`llm_provider._create_embedding`）：
- **API 模式**：OpenAI 兼容端点（`OpenAIEmbedding`），`embed_batch_size=10`、`max_retries=1`、`timeout=20s`（**快速失败**，避免拖垮外层 60s 检索预算）。
- **本地模式**：`HuggingFaceEmbedding` 加载本地 bge-m3（可选依赖，装 `requirements.local-embedding.txt`）。

### 2.2 余弦相似度（Cosine Similarity）

衡量两个向量"方向有多接近"：

```
cos(A, B) = (A · B) / (‖A‖ · ‖B‖)   ∈ [-1, 1]，越接近 1 越相似
```

为什么用余弦而不是欧氏距离？因为文本 embedding 关心的是**语义方向**而非向量长度，余弦对长度不敏感、更稳。本项目所有"找相似"都基于它（`vector_store/base.py:_cosine_similarity`）。

### 2.3 向量检索的两种后端（知识库索引）

本项目**可插拔**（`indexer.py` + `VECTOR_BACKEND` 配置）：
- **Qdrant（服务器/Docker 部署默认）**：compose 注入 `VECTOR_BACKEND=qdrant`，知识库按 `kb_{user}_{topic}` 分 collection。qdrant-only：连不上不降级，检索降级空上下文并委派后台重建。
- **numpy（裸 uvicorn 本地开发默认）**：小规模直接全量算余弦，零外部依赖，索引 persist 到 `.index_cache/`。

> **设计哲学**：长期记忆那层（04 章）甚至直接用 SQLite BLOB 存向量——**百级数据规模刻意不上 Milvus/Pinecone**。这是"按规模选型"，不是技术不够。面试时这是加分项，别说成短板。

---

## 3. LlamaIndex：知识库索引与检索

### 3.1 是什么

**LlamaIndex** 是专做 RAG 的数据框架，负责"文档 → 切块 → embedding → 建索引 → 检索"这条流水线。本项目用它索引领域知识库和简历（`indexer.py`，29KB，是后端最大的模块之一）。

### 3.2 Chunk（切块）策略

文档要先切成小块（chunk）才能 embedding。本项目用 `MarkdownNodeParser`——**按 Markdown 标题结构切**，并保留 header 路径作为元数据（`ChunkWithMeta` 带 `source_file` + `header_path`）。

> **诚实点（也是已知优化项）**：`MarkdownNodeParser()` 用的是默认参数、没设 `chunk_size/overlap`。文档普遍 500-1500 行，某些长正文会切成超大 chunk（向量被"平均化"稀释）、标题密集处又切太碎。这是 RAG 调优"第一刀"，项目的优化方案里把它列为待办（`OPTIMIZATION_PLAN` P1-C）。**面试时主动说出来，体现你知道 chunk 是 RAG 第一调优点。**

### 3.3 检索的同步/异步处理

LlamaIndex 的检索是**同步阻塞**的。本项目用 `asyncio.to_thread` 把它丢到线程池，外面套 `asyncio.wait_for` 超时（知识库检索超时 60s），这样：
- 不阻塞事件循环（SSE 心跳还能正常推）；
- 超时能真正取消（`safe_retrieve_topic_context`）。

> **面试金句**：「我不是全异步——LlamaIndex 检索是同步的，我用 `to_thread + wait_for` 包了一层，关键路径都有 timeout 兜底。」诚实且专业。

### 3.4 增量插入

知识自进化（04 章）会往知识库追加内容。本项目用 `incremental_insert_to_index` 做**增量 embedding**——只嵌入新内容，不重建整个索引；重建走后台任务队列（05 章）。

---

## 4. 出题主链路：多路召回 + RRF + 去重 + 重排

这是 `graphs/rag_retrieval.py` 的 `retrieve_for_drill`，整个项目检索的精华。一步步拆：

### 4.1 第一步：多路召回（per-weak-point queries）

**老做法（已废弃）**：把用户的 5 个薄弱点拼成一个长 query 去检索。
**问题**：你没法让向量库"一次理解 5 件事"——语义信号被稀释，排序很差。

**新做法**：对**每个薄弱点各发一路检索**，并发执行：

```python
queries = list(weak_points[:5])           # 最多 5 路，防 fanout 滥用
sem = asyncio.Semaphore(_EMBED_CONCURRENCY)  # 并发限到 2
raw_results = await asyncio.gather(*[_bounded(q) for q in queries],
                                   return_exceptions=True)  # 单路失败不炸全局
```

**为什么并发限到 2**（`_EMBED_CONCURRENCY=2`）？注释写得很清楚：检索 fan-out 和后面的去重阶段都打**同一个 embedding key**，5 个并发会触发 DashScope 的 per-key 并发限流，每个请求反而 stall 到超时。2 既不触发限流、又比串行快 ~2.5×。**这是被生产事故教育出来的参数。**

### 4.2 第二步：RRF 倒数排名融合（Reciprocal Rank Fusion）

多路召回得到多个排序列表，怎么合并成一个？答案是 **RRF**：

```
score(chunk) = Σ_i  1 / (k + rank_i(chunk))     k = 60（标准平滑常数）
```

- `rank_i` 是 chunk 在第 i 路结果里的名次（从 1 开始）。
- 一个 chunk 在多路里都靠前 → 多个大分项累加 → 总分高。

**为什么用 RRF 而不是"把各路分数加权平均"？**
- 不同 query 的相似度分数**不在同一量纲**（绝对值不可比），直接相加是错的。
- RRF **只用排名、不用分数**，天然免标定（parameter-light），对多路融合极其鲁棒。这是信息检索领域的经典融合算法。

实现仅 10 行（`_reciprocal_rank_fusion`, `rag_retrieval.py:174-183`），简洁优雅。

### 4.3 第三步：语义去重（cosine ≥ 0.85）

多路召回必然有重复/复述的段落。**老做法**是用 `chunk[:100]` 前缀比对去重——漏掉所有"换了说法的近义重复"。

**新做法**：对融合后的 chunk 逐个 embedding，与已保留集算余弦，**≥0.85 视为"同一段的复述"丢弃**（`_semantic_dedup`）。用增量矩阵 `np.vstack` 累积已保留向量，每来一个新 chunk 做一次 `_cosine_similarity` 取 max。

> 注意 **0.85** 这个阈值比薄弱点去重的 0.75 高——因为这里要的是"几乎一样的段落"才去重，宁可放过也别误杀有差异的知识点。

### 4.4 第四步：Cross-Encoder 重排（可选、可降级）

**Bi-Encoder vs Cross-Encoder**（重要概念辨析）：
- **Bi-Encoder**（就是 embedding 检索）：query 和 doc **分别**编码成向量再算余弦。快，但 query-doc 交互弱，**粗排**。
- **Cross-Encoder**（重排）：query 和 doc **拼在一起**送进模型，输出一个相关性分数。精度高，但每个候选都要过一次模型，**慢，只适合精排 top-N**。

本项目 `reranker.py` 调 **Cohere 兼容 `/v1/rerank`** 端点（如 Gitee AI Qwen3-Reranker），把去重后的 chunk 重排。三态状态码（出现在前端时间线里）：

| 状态 | 含义 |
|---|---|
| `applied` | 重排成功生效（顺序可能变了） |
| `degraded` | 配了重排但调用失败 → 退回原顺序 |
| `off` | 没配重排（或没东西可排）→ 原顺序 |

工程细节：
- **输入上限**：`MAX_RERANK_DOCS=50`（候选条数）、`MAX_DOC_CHARS=2000`（单文档，防超上游 token 上限）、`MAX_QUERY_CHARS=512`——但**回传的是未截断的完整原文**，截断只用于打分入参。
- **结果缓存**：Redis 缓存重排结果索引，key 用**长度前缀哈希**防边界混淆（`(query="ab",docs=["c","d"])` 和 `(query="a",docs=["bc","d"])` 不会撞 key）。
- **确定性 4xx（400/413/422）不污染渠道失败计数**——这些是输入太大/格式错，换渠道也救不了，不该误把可用渠道冷却 60s。

### 4.5 第五步：算在线 RAG 指标 + 落库

检索完顺手算 relevance/coverage/diversity（**纯 embedding、零 LLM 成本**，`rag_metrics.compute_retrieval_metrics`），随出题落进 `rag_metrics` 表，前端 RAG Dashboard 可视化。算不出来时（embedding 降级/维度不符）返回 `None` 而非假的全零记录——避免"看起来像检索崩了"。

### 4.6 全链路一张图

```
weak_points ─┬─ q1 ─检索─┐
             ├─ q2 ─检索─┤
             ├─ q3 ─检索─┤  (并发, Semaphore=2, 单路失败→空)
             └─ qN ─检索─┘
                  ↓
            RRF 融合 (Σ 1/(60+rank))
                  ↓
            语义去重 (cosine ≥ 0.85)
                  ↓
            Cross-Encoder 重排 (applied/degraded/off)
                  ↓
            截 top-10 → 拼成 knowledge_ctx
                  ↓
            算在线指标(relevance/coverage/diversity) → 落库
```

整条链路有**硬端到端预算 100s**（`drill_pipeline._stage_retrieve`），超时就**降级到空上下文继续出题**——绝不让用户等几分钟。

---

## 5. Dense vs Sparse vs Hybrid（高频考点辨析）

| 检索方式 | 原理 | 强项 | 弱项 |
|---|---|---|---|
| **Dense（稠密/向量）** | embedding 余弦 | 语义相近、换说法也能召回 | **专有名词精确命中弱**（GIL/MVCC/ZGC 这种术语，向量未必准） |
| **Sparse（稀疏/BM25）** | 词频统计（TF-IDF 家族） | 术语精确命中强 | 不懂语义、换个说法就召不回 |
| **Hybrid（混合）** | dense + sparse 融合（常用 RRF 合并） | 两者互补 | 实现更复杂、加延迟 |

**本项目现状**：纯 dense（多路 + RRF 融合的也都是 dense query），**没有 BM25**。

> **诚实且有数据支撑的表述**（这是项目最成熟的方法论之一）：「我没有盲目上 hybrid/rerank。我先建了检索评测集（`eval/rag_recall.py`）跑 baseline——结果 HitRate=1.0、MRR≥0.9，**说明当前 dense 检索质量已经够用，瓶颈不在召回而在延迟**（冷启动 12-27s）。所以我把 hybrid/rerank **正式搁置**（写了重启条件：等更难的评测集暴露'召回到了但排序差'再上），转而去治冷启动延迟和审视 RRF 去重阈值。**每一刀都用 before/after 数据决定，不盲调。**」

这个回答的含金量在于：它证明你懂 **RAG 真实瓶颈在检索精度与上下文融合、不在语料体积**，且**用尺子说话**。

---

## 6. RAG 质量提示（rag_quality_hint）：防幻觉的小设计

检索结果有多有少，得告诉 LLM"这次该多依赖知识库"。`drill_pipeline._rag_quality_hint` 按召回数量分三档动态注入指令：

| 召回 chunk 数 | 给 LLM 的指令 |
|---|---|
| **0** | ⚠️ 未召回到内容，凭领域常识出题，**不要**说"参考资料中提到" |
| **<3** | ℹ️ 召回稀疏，仅供辅助判断深度，别当题面来源 |
| **≥3** | ✓ 可用于把握技术深度边界，但**禁止照搬原文** |

这是个简单但有效的防幻觉设计——**让模型行为与真实检索结果对齐**，召回为空时不假装有依据。

---

## 7. RAG 评测指标全解（08 章会再串一遍评测体系）

理解这些指标，才能聊"RAG 做得好不好"：

### 7.1 检索质量指标（离线，`eval/rag_recall.py`）

| 指标 | 定义 | 衡量什么 |
|---|---|---|
| **Recall@k** | top-k 里命中的"应召回 chunk"数 / 应召回总数 | 召回全不全 |
| **Hit-Rate** | top-k 里**至少 1 个**命中的 query 比例 | "出题够不够用" |
| **MRR**（Mean Reciprocal Rank） | 命中 chunk 排名倒数的均值 | **排序好不好**（rerank 的 before/after 就看它） |
| **Precision** | top-k 里相关 chunk 的比例 | 召回的准不准（少废料） |

### 7.2 生成质量指标（RAGAS 家族，在线 `rag_metrics.py`）

| 指标 | 衡量什么 |
|---|---|
| **Faithfulness（忠实度）** | 生成内容是否"忠于"检索片段（防幻觉） |
| **Context Relevance/Precision/Recall** | 检索上下文与问题的相关性/精度/召回 |
| **Answer Relevance/Correctness** | 答案与问题的相关性/正确性 |

> **关键认知（容易踩的坑）**：项目早期的 coverage 指标有"同源性陷阱"——用 persona 的 weak_points 出题，又用**同一批** weak_points 判覆盖，绝对数值偏高。所以**只能解读"个性化优于基线"这个相对结论，不能把绝对覆盖率当"检索质量好"**。这个认知偏差的自觉，本身就是 RAG 工程成熟度的体现。

---

## 8. 知识库缓存：省掉重复 RAG hop

同一次训练 sitting 里反复用相同 weak_points 出题，没必要每次都跑一遍完整 RAG。`drill_pipeline` 按 `(topic, weak_points)` 哈希做 **Redis 缓存**（`drill:knowledge_ctx:<sha256[:16]>`，TTL 1h）：

- 命中 → 直接复用上次的 knowledge_ctx，**整段 RAG hop 跳过**，前端时间线显示"缓存命中"。
- TTL 1h：长到能覆盖一次多题训练，短到知识库当天编辑能当天生效。

---

## 本章小结

- RAG = "先检索、再生成"，治幻觉/知识截止/私域无知；本项目是**领域知识库 + 历史记忆**双层增强。
- 出题主链路：**多路召回（每薄弱点一路）→ RRF 融合 → 语义去重(0.85) → Cross-Encoder 重排 → 在线指标落库**，全链路可观测、可降级、有 100s 硬预算。
- 能辨析 **Bi-/Cross-Encoder**、**Dense/Sparse/Hybrid**，并且**用评测数据决定是否上 hybrid/rerank**——这是本章最值钱的方法论。
- RAG 质量提示防幻觉、知识缓存省 RAG hop，是务实的小设计。

➡️ 下一章：[04_记忆与个性化.md](04_记忆与个性化.md)——Mem0 两阶段画像、自研向量记忆、SM-2、掌握度算法。
