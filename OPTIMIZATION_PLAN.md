# SparkOffer 出题质量全面优化方案

> **范围声明**：本方案聚焦**「出题质量闭环」**——即 `训练 → 评估 → 画像/掌握度更新 → 向量入库 → SM-2 调度 → 下次出题融合三层上下文` 这条主线。**不含**部署、前端体验、鉴权安全、成本治理等横向议题（如需可另开方案）。
>
> **方法论**：所有判断带 `file:line` 证据；所有优化项带「问题 / 方案 / 验收标准 / 成本 / 风险」；最后给分期路线图。**优先修「衡量手段」，再谈「优化手段」**——没有可信的尺子，一切调参都是盲调。
>
> 成本标记：**S**=半天内 / **M**=1–3 天 / **L**=3 天以上。

---

## 〇、先肯定已经做对的（不重复造轮子）

写优化方案前先把现状基线钉清楚，避免「优化」掉已经成熟的东西：

| 已有能力 | 位置 | 评价 |
|---|---|---|
| 分阶段流式出题管道（prepare/retrieve/generate/validate/finalize，逐阶段计时 SSE） | `graphs/drill_pipeline.py` | 架构清晰，是后续优化的稳定挂载点 |
| RRF 多路召回 + 语义去重 | `graphs/rag_retrieval.py` | 比纯 top-k 强，作者注释已自述老方法缺陷 |
| 知识上下文 Redis 缓存（按 topic+weak_points 哈希，TTL 1h） | `drill_pipeline.py:226-261` | 省掉重复 RAG hop，做得对 |
| `rag_quality_hint`：按召回数量动态指示 LLM 依赖程度 | `drill_pipeline.py:290-313` | 防幻觉的好设计，0/<3/≥3 三档 |
| 种子题库 + 增量补题 | `graphs/seed_pool.py` | 降时延、稳质量 |
| 结构校验 + 单次定向修复 | `drill_pipeline.py:507-624` | 修复预算封顶，克制 |
| 难度锚点 k-NN 校准（纠正 LLM 自报难度） | `graphs/difficulty_anchors.py` | 把主观难度拉回客观 |
| 冷启动专用短 prompt | `drill_pipeline.py:463-503` | 避免对空画像硬塞个性化 |
| Slot 槽位分配（按 per-WP 掌握度排兵） | `prompts/strategies.py` | 把「考什么」结构化 |
| Mem0 式画像更新（Extract→Update，ADD/UPDATE/IMPROVE/NOOP） | `memory.py:607-773` | 两阶段，思路正确 |
| SM-2 + 薄弱点自动毕业（连续 3 次 ≥7 分） | `spaced_repetition.py:127-135` | 闭环完整 |
| 离线评测框架（5 persona × 3 strategy × 4 judge） | `backend/eval/` | 有对比基线，难得 |

**结论**：地基扎实。本方案的任务是**补三块短板 + 修一把尺子**，不是重写。

---

## 一、核心判断（一句话）

> **出题质量的瓶颈不在「知识库大小」，而在「① 评测尺子测错了对象 ② 检索仍是纯语义、缺术语命中与精排 ③ 新旧检索路径分叉」。知识库本身是「仅供参考」层，扩容收益有上限——该投的是个性化与时效，不是体积。**

---

## 二、问题清单（按闭环分层，带证据）

### L0 · 评测层（最严重，因为它让其它判断失真）

- **P0-1 没有任何指标直接衡量 RAG 检索质量。** 四个 judge（coverage / difficulty_kl / diversity / llm_judge）测的都是**出题成品**，没有一个测「检索召回的 chunk 是否相关、是否召回了该召回的」。
- **P0-2 唯一的检索评测集是死文件。** `data/eval/rag_queries.json` 仅 10 条、`keyword_match` 判命中、**无任何 Python 代码消费它**（`backend/eval/run.py` 不加载它）。等于「为检索准备了尺子，但从没量过」。
- **P0-3 coverage 指标存在「同源性」陷阱。** persona 的 `weak_points` 喂给 personalized 策略出题，又用**同一批** `weak_points` 判覆盖（`eval/judges/coverage.py:62-97`）。
  - ⚠️ 注意：这**不代表 eval 没用**——random_baseline / topic_only 两个基线吃同样的 judge，所以「personalized 是否**优于**基线」这个**相对结论**有效；但 coverage 的**绝对数值**不能解读成「检索/出题质量好」。这是你说「召回率不错」时最可能踩的认知偏差。

### L1 · 检索层

- **P1-1 chunk 策略不可控。** `MarkdownNodeParser()` 全默认、不设 `chunk_size`/`overlap`（`indexer.py:107`）。文档普遍 500–1500 行，某二级标题下长正文 → 单 chunk 过大、向量被平均化稀释；标题密集处又过碎。chunk 是 RAG 调优第一刀，这里完全交给文档结构。
- **P1-2 纯 dense 检索，缺术语命中。** 即便新路径 RRF 融合的也是多个 dense query（`rag_retrieval.py`），**无 BM25/稀疏**。而面试题大量靠专有名词精确命中（GIL / MVCC / happens-before / Full GC / ZGC），dense 对术语召回天然弱。
- **P1-3 无重排（rerank）。** top-k 完全信任 embedding 粗排，无 cross-encoder / LLM 二次精排。
- **P1-4 无 query 改写/扩展。** weak_point / question 原文直接当 query，口语化长句 ↔ 书面知识库存在 query-doc gap；fallback query 是硬编码或随机子主题（`drill_pipeline.py:269-288`）。
- **P1-5 新旧检索路径分叉。** 流式 `DrillPipeline` 走新路径（RRF），而 `topic_drill.generate_drill_questions`（非流式）+ 多处答案评估仍走老的纯向量 `retrieve_topic_context`（`indexer.py:276-281`）。一套库两种检索质量。
- **P1-6 `query_resume/query_topic` 用 `as_query_engine` 多触发一次 LLM 合成**（`indexer.py:181-194`）。若下游只需原文喂 prompt，这是白花一次 LLM 调用 + 延迟，应统一走 retriever。

### L2 · 上下文融合层（画像 / 掌握度 / 洞察）

- **P2-1 掌握度 EWMA 方向不一致。** topic 级 `merged = old×0.3 + new×0.7`（偏向新，`memory.py:505-531`）；per-WP 级 `0.7×prev + 0.3×contribution`（偏向旧，`spaced_repetition.py:76-99`）。两个粒度方向相反——**待确认是有意（整体跟随近况、单点平滑防误毕业）还是疏漏**。
- **P2-2 薄弱点匹配靠文本/关键词（`wp_match`），易误配。** 出题覆盖、掌握度归属都依赖它，错配会把分数记到错的薄弱点上。
- **P2-3 历史洞察检索偏粗。** `past_insights` 固定 `top_k=3`、`score>0.3`（`memory.py:229-272`），阈值与条数未随场景调整。

### L3 · 知识库内容层

- **P3-1 库小但不是主瓶颈。** 2.1M、91 篇 md、仅 python/java/agent 三域。但它是「仅供参考」层，盲目扩容边际收益低，且与硬约束（500 向量/user、50 缓存索引）相悖。
- **P3-2 个性化库未被充分利用。** `data/users/{id}/` 隔离、`query_resume`、`high_freq/`、`vector_memory` 已就位，但「用户上传简历/项目文档/JD/错题来驱动出题」这条高价值线没做透。
- **P3-3 无时效补位。** 模型 cutoff 之后的新框架/新版本 API，靠静态 md 永远滞后。

### L4 · 生成层

- 已相当成熟（好题/坏题对照、slot 约束、校验修复、难度校准）。**仅小优化**，见 P4。

---

## 三、优化方案（逐项：问题 / 方案 / 验收 / 成本）

### 🟥 P0 — 修尺子（最高优先级，解锁后续一切调参）

#### A. 让检索评测真正跑起来
- **方案**：在 `backend/eval/` 新增 `rag_recall.py` + CLI 子命令，消费 `data/eval/rag_queries.json`。对每条 query 实跑 `retrieve_for_drill`/`retrieve_topic_context`，输出三个指标：
  - **Recall@k**：`must_include_any` 命中的 chunk 数 / 应召回数（k=5,10 各算一版）
  - **Hit-rate**：top-k 中是否至少 1 个命中（更贴近「出题够不够用」）
  - **MRR**：命中 chunk 的排名倒数均值（衡量精排质量，为 P1-3 rerank 提供 before/after 对照）
- **同时扩充评测集**：10 → **50–80 条**，覆盖三域、含「术语精确题 vs 概念理解题」两类（前者专门暴露 P1-2 纯 dense 的弱点）。标注 `must_include_any` + 1–2 个负样本 chunk_id（防止「召回一堆但都不相关」被算成高分）。
- **验收**：`python -m backend.eval.rag_recall` 一键产出 baseline 报告（Recall@5 / Hit-rate / MRR）。这是后面所有 L1 优化的 before/after 基准。
- **成本**：**M**

#### B. 给 coverage 判去同源化（可选但推荐）
- **方案**：新增一个「保留集」persona——其 `weak_points` **不喂给出题策略**，只用于判覆盖（模拟「考到了画像里没明说但相关的点」）。或引入「负向覆盖」：统计题目命中了**非目标** weak_point 的比例。
- **验收**：coverage 报告里区分「目标覆盖率」与「泛化覆盖率」两栏。
- **成本**：**S**

---

### 🟧 P1 — 检索层（拿 P0 的 baseline 验证收益，按性价比排序）

#### C. chunk 可控化（最便宜的一刀，先做）
- **方案**：`_build_nodes`（`indexer.py:89-110`）给 `MarkdownNodeParser` 之后接一道 `SentenceSplitter(chunk_size=512, chunk_overlap=64)` 对超长节点二次切分；或直接为 md 设置 size 上限。保留 Header 元数据（现有优点别丢）。
- **验收**：P0-A 报告里 Recall@5 / MRR 较 baseline 提升；chunk 长度分布从「长尾」收敛到可控区间。
- **成本**：**S** ｜ **风险**：需 `force_rebuild` 重建索引缓存，一次性。

#### D. 统一检索路径（消除技术债）
- **方案**：先核查非流式 `topic_drill.generate_drill_questions` 是否仍有活跃入口（`routers/interview.py`）。若已被流式取代 → 废弃老路径；若仍需 → 让它也调 `retrieve_for_drill`。答案评估处的 `retrieve_topic_context` 统一收口到同一检索函数。
- **验收**：全代码库只剩一条「topic 检索」入口；新旧路径输出一致性测试通过。
- **成本**：**M** ｜ **风险**：行为变更，需回归流式/非流式两条出题流。

#### E. 加 hybrid 检索（BM25 + dense）
- **方案**：用 LlamaIndex 原生 `QueryFusionRetriever` 或轻量 `rank-bm25`，把稀疏检索结果并入现有 RRF 融合（RRF 天然支持多路合并，改动小）。**不引入重型依赖、不动 `vector_memory` 的 SQLite-blob 设计**（那是用户记忆，与知识库索引是两套东西）。
- **验收**：P0-A 里「术语精确题」子集的 Recall 显著提升（这是 hybrid 的主战场）；概念题不退化。
- **成本**：**M** ｜ **风险**：低。

#### F. 加 rerank（top-N→top-k 精排）
- **方案**：检索 top-20 → 重排取 top-5。优先用 **LLM rerank**（复用现有 `llm_provider`，零新依赖，符合项目调性）；若延迟敏感再换轻量 cross-encoder。放在 `rag_retrieval.py` 融合之后、截断之前。
- **验收**：P0-A 的 **MRR** 明显提升；人工抽检 top-5 相关性。
- **成本**：**M** ｜ **风险**：增加一次 LLM 调用延迟——评估是否值得，用数据说话（P0 baseline 决定）。

#### G. query 改写（收益最不确定，放最后）
- **方案**：出题前对 weak_point 做一次轻量 query 扩展（同义术语 / 英文术语补全，如「锁竞争」→ 补「lock contention / 自旋锁 / CAS」）。可复用一次小模型调用，或维护一张术语别名表（零成本、更可控）。
- **验收**：P0-A Recall 提升且无明显噪声引入。
- **成本**：**S–M** ｜ **风险**：改写可能引入噪声，需 A/B。

---

### 🟨 P2 — 上下文融合层

#### H. 澄清/统一掌握度 EWMA
- **方案**：先**确认** P2-1 的方向差异是否有意。若无意 → 统一为「整体快、单点慢」并写注释固化语义；若有意 → 在代码注释里讲清楚，避免后人误改。
- **验收**：两处 EWMA 语义有显式注释；掌握度演化在 eval persona 上符合预期曲线。
- **成本**：**S**

#### I. 薄弱点匹配增强
- **方案**：`wp_match` 在关键词之外补 embedding 相似度兜底（部分路径已有，统一化），降低误配。
- **验收**：构造近义薄弱点测试集，误配率下降。
- **成本**：**S–M**

### 🟩 P3 — 知识库内容策略（呼应第一轮结论）

#### J. 做透个性化库（高 ROI，优先于扩通用库）
- **方案**：打通「用户上传简历 / 项目文档 / 目标 JD / 历史错题」→ 入个性化索引 → 出题时作为**高权重**检索源。这是基准库永远给不了的个性化，也是最好的产品/面试故事。
- **验收**：上传 JD 后，出题命中 JD 关键技能的比例可量化提升。
- **成本**：**M–L**

#### K. 时效补位（精准，非主路径）
- **方案**：联网搜索**只**接在低频、对时效敏感的环节——参考答案生成（`REFERENCE_ANSWER_PROMPT`）、事实校对（`interviewer.py` 的「仅供事实校对」槽位）。**绝不放进出题主路径**（批量 10 题逐题联网会慢/贵/不稳）。
- **验收**：参考答案中涉及「最新版本/最新实践」的内容时效性提升；出题主路径延迟不变。
- **成本**：**M**

#### L. 通用基准库——**基本不做**
- 只在发现「LLM 参数化知识盲区」（前沿框架、特定版本、冷门垂直）时**定向补**，不堆常识性长文。**不得**突破 500 向量/user、50 缓存索引等硬约束。

### 🟩 P4 — 生成层（小优化）

- 已成熟。可选：把 `_strategy_for_mastery` 的 legacy 3-band 文案与 slot-based 策略在冷启动外的重叠收敛，减少两套策略并存的维护面。**成本 S**，低优先。

---

## 四、实施路线图（分期，每期可独立交付）

```
第 1 期（P0，先修尺子）         成本 ~M
  └ A 检索评测跑起来 + 扩样本    ← 解锁后续一切 before/after
  └ B coverage 去同源（可选）
      ▼ 产出：RAG 检索质量 baseline 报告

第 2 期（P1 低成本项）          成本 ~M
  └ C chunk 可控化              ← 拿第1期 baseline 验证
  └ D 统一检索路径
      ▼ 产出：消除分叉 + chunk 收益数据

第 3 期（P1 增强项，数据驱动取舍）成本 ~M–L
  └ E hybrid 检索（术语命中）
  └ F rerank（精排）            ← 用第1期 MRR 判断是否值这次延迟
  └ G query 改写（最后，需 A/B）
      ▼ 产出：检索质量阶跃 + 延迟成本权衡结论

第 4 期（产品/个性化，高 ROI）   成本 ~M–L
  └ J 个性化库做透
  └ K 时效补位
  └ H/I 上下文融合微调（穿插进行）
```

**依赖关系**：第 1 期是闸门——不先修尺子，第 2/3 期的 C/E/F 全是盲调，无法证明收益。第 4 期可与 2/3 期并行（不依赖检索 baseline）。

---

## 五、明确不做什么（呼应 CLAUDE.md 的克制原则）

1. **不堆通用大基准库**——「仅供参考」层，边际收益低且违背硬约束。
2. **不把 `vector_memory` 换成 Milvus/Pinecone**——SQLite-blob 是按项目规模刻意选的；hybrid/rerank 加在 LlamaIndex 知识库索引上，与它无关。
3. **不重新引入防御层**（broad try/except、mock fallback、feature flag 脚手架）——简化 pass（commit `9a07107`）刻意移除的，不无故复活。
4. **不把联网搜索塞进出题主路径**——慢/贵/不稳。
5. **不在没有 P0 baseline 的情况下上 rerank/hybrid**——避免盲调，用数据决定每一刀。

---

## 六、附：一句话面试叙事

> 「我没有靠堆知识库体积来提升出题质量。我先用离线评测发现**衡量对象错了**——测的是出题覆盖率不是检索召回；补齐 RAG 检索的 Recall/MRR 指标后，按数据依次上 chunk 调优、hybrid 检索、rerank，并把高 ROI 投在**个性化库与时效补位**而非通用语料。每一刀都有 before/after 数据支撑。」

这比「我做了个很大的知识库」强一个量级——它证明你懂 RAG 的真实瓶颈在**检索精度与上下文融合**，不在语料体积。

---

*本方案为诊断与规划文档，未改动任何代码。落地时建议从第 1 期（P0-A）开始。*
