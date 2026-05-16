# RAG 进阶：混合检索、Rerank、Chunking 策略

朴素 RAG（embedding 相似度 → top-K → 喂给 LLM）在生产环境召回率往往只有 60-70%。进阶 RAG 通过 chunking 优化、混合检索、reranking、查询重写等手段把召回率提到 85%+，回答准确率显著提升。

## 1. RAG pipeline 全貌

```
[文档] → [清洗] → [Chunking] → [Embedding] → [Vector Store]
                                                ↓
[Query] → [改写/扩展] → [混合检索] → [Rerank] → [Top-K] → [LLM 生成] → [Answer]
                                                              ↑
                                                        [引文标注]
```

每一步都有优化空间。把 RAG 当作 ML pipeline 而非单点调用。

## 2. Chunking 策略

错误的 chunking 让任何下游优化都无效。

### 2.1 固定大小（Fixed-size）

最简单、最常见也最差。按字符数切（如 500 char + 50 overlap），可能切断语义单元。

### 2.2 递归切分（Recursive Character）

LangChain 的 `RecursiveCharacterTextSplitter`：按分隔符层级递归切（先 \n\n，再 \n，再句号，再空格）。比固定切好很多，但仍不完美。

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter
splitter = RecursiveCharacterTextSplitter(
    chunk_size=512,
    chunk_overlap=64,
    separators=["\n## ", "\n### ", "\n\n", "\n", "。", " "],
)
```

### 2.3 语义切分（Semantic Chunking）

按句子 embedding 相似度的"断崖"位置切。同一段落语义相似度高，切到下一段时余弦距离骤降——在断崖处切。

```python
from langchain_experimental.text_splitter import SemanticChunker
chunker = SemanticChunker(embeddings, breakpoint_threshold_type="percentile")
```

### 2.4 文档结构感知切分

针对特定格式（Markdown / HTML / PDF）按结构切：
- Markdown：按 H1/H2/H3 切，保留 header 路径作为 metadata
- HTML：按 `<section>` `<article>` 切
- PDF：按版面 layout 分块（用 unstructured / pdfplumber 解析）

### 2.5 父子 chunk（Parent Document Retriever）

embedding 用小 chunk（高精度匹配），返回时给 LLM 大 chunk（保留完整上下文）。

```python
from langchain.retrievers import ParentDocumentRetriever
retriever = ParentDocumentRetriever(
    vectorstore=vector_store,
    docstore=byte_store,
    child_splitter=child_splitter,  # 256 char
    parent_splitter=parent_splitter, # 2048 char
)
```

### 2.6 Chunk size 选择

- **过小**（<200 char）：上下文不足，召回多但质量低
- **过大**（>1500 char）：稀释相关信号，embedding 漂移
- **经验**：技术文档 400-600 char + 50-100 overlap；对话记录按 Q&A 对切

## 3. 混合检索

单独的 embedding 检索（dense）会漏掉关键词命中（如"GPT-4o"这种品牌词），单独的 BM25（sparse）漏掉语义相关。混合两者：

### 3.1 Reciprocal Rank Fusion (RRF)

最简单、效果好的融合算法。两路检索各自的排名加权倒数求和：

```python
def rrf(rankings: list[list[str]], k: int = 60) -> dict:
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1 / (k + rank)
    return dict(sorted(scores.items(), key=lambda x: -x[1]))

dense_results = vector_store.search(query, k=20)
sparse_results = bm25.search(query, k=20)
fused = rrf([
    [d.id for d in dense_results],
    [d.id for d in sparse_results],
])
```

### 3.2 Weaviate / Qdrant 内置 hybrid

```python
results = qdrant.query_points(
    collection_name="docs",
    query=embedding,
    using="dense_vector",
    prefetch=[
        models.Prefetch(query=sparse_vector, using="sparse_vector"),
    ],
    fusion=models.Fusion.RRF,
    limit=10,
)
```

## 4. Reranking

混合检索后 top-20，再用 cross-encoder rerank 选 top-3。Cross-encoder 比 bi-encoder 精度高一个数量级（query 和 doc 同时进 transformer 算注意力），代价是慢——所以只对小 top-K 跑。

### 4.1 开源 Reranker

- **bge-reranker-large**：中英文都好
- **Cohere Rerank API**：托管服务、零运维
- **Voyage rerank**：精度领先

```python
from sentence_transformers import CrossEncoder
reranker = CrossEncoder("BAAI/bge-reranker-large")

def rerank(query, docs, top_k=3):
    pairs = [[query, d.content] for d in docs]
    scores = reranker.predict(pairs)
    ranked = sorted(zip(docs, scores), key=lambda x: -x[1])
    return [d for d, _ in ranked[:top_k]]
```

### 4.2 Rerank 收益

实测在企业知识库场景：单 dense 检索 → 加 rerank，召回 top-3 命中率从 65% → 88%。代价是延迟 +200-500ms，绝对值得。

## 5. 查询重写与扩展

### 5.1 HyDE (Hypothetical Document Embeddings)

让 LLM 先生成"假设答案"，用假设答案的 embedding 去检索。原理：答案文本和文档库语义更接近，比 query 直接检索匹配度高。

```python
def hyde(query):
    fake_answer = llm.invoke(f"用 100 字回答：{query}").content
    return embeddings.embed(fake_answer)
```

### 5.2 Multi-query

一个 query 改写成多个角度，分别检索后融合：

```python
queries = llm.invoke(f"把以下问题改写成 3 个不同角度的搜索查询：{query}")
all_results = []
for q in queries.split("\n"):
    all_results.extend(vector_store.search(q, k=5))
```

### 5.3 Step-back

复杂问题先抽象成更通用的问题再查（"Python 3.11 怎么做 GIL 优化" → "Python GIL 是什么"），让背景知识也召回。

## 6. 上下文压缩

检索回来的 chunk 通常含大量无关内容，给 LLM 前先压缩：
- **LLMLingua**：用小模型评估每个 token 的重要性，删低分 token
- **LongLLMLingua**：针对长文档版本
- **Contextual Compression**：用 LLM 判断每个 chunk 跟 query 是否相关，删无关

## 7. 引文标注

让 LLM 在答案中标 [1] [2] 引用，对应 chunk 元信息（文件名+行号），用户能溯源验证。

```python
prompt = f"""基于以下资料回答问题。每个事实必须标注引用编号 [N]。
{format_chunks_with_id(chunks)}

问题：{query}
"""
```

## 8. 评估

### 8.1 离线指标

- **Hit@K**：标准答案文档是否在 top-K 召回里
- **MRR (Mean Reciprocal Rank)**：标准文档的排名倒数平均
- **NDCG**：考虑相关度等级的排序质量

### 8.2 端到端评估（RAGAS）

无需人工标注的全自动评估：
- **Faithfulness**：答案是否忠于 chunk（无幻觉）
- **Answer Relevancy**：答案是否切题
- **Context Precision**：召回的 chunk 是否真的有用
- **Context Recall**：标准答案需要的信息是否都召回了

```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision

result = evaluate(dataset, metrics=[faithfulness, answer_relevancy, context_precision])
```

## 9. 多模态 RAG

PDF 含图表、扫描件、图像时单纯 OCR 丢信息。方案：
- 图片 → CLIP / SigLIP embedding
- 表格 → 转 Markdown 后 embedding
- 视频 → 抽帧 + 字幕 + 多模态 embedding
- 检索时多模态 query 也用 CLIP 做对齐

## 10. 高频面试题

**Q1：朴素 RAG 召回率低怎么排查？**
按 pipeline 阶段拆：① chunking 是否切断语义（hand-eval 几个 chunk）；② embedding 模型是否适配领域（中文用中文模型）；③ 是否需要 hybrid（业务里有专有名词必须 hybrid）；④ 加 rerank 看召回前 3 是否变好。

**Q2：怎么处理超长文档（>100 页）？**
父子 chunk 或 multi-vector：每个父 chunk 生成多个子 chunk（摘要、关键问题、原文）分别 embedding，检索时多路命中加分。

**Q3：embedding 模型选哪个？**
中文：bge-large-zh-v1.5 / m3e-large / Conan-embedding；英文：bge-large-en-v1.5 / OpenAI text-embedding-3-large；多语言：multilingual-e5-large。**有领域语料的话 fine-tune 收益最大**。

**Q4：向量数据库怎么选？**
- 小规模 / 单机：Chroma / FAISS
- 中等规模 / 云原生：Qdrant / Weaviate
- 大规模 / 已有 ES：Elasticsearch + dense_vector
- 多租户 SaaS：Pinecone（托管省心）

**Q5：怎么避免 RAG 幻觉？**
① prompt 强约束"只用资料中事实，资料没说就回答不知道"；② 引文标注 + 后置校验（LLM 判断每句是否被 chunk 支持）；③ Faithfulness 评估持续监控；④ 不确定时降级到"建议人工咨询"。

**Q6：实时数据怎么集成 RAG？**
- 高频更新（订单状态）：直接走 Function Calling 查实时 API，不进 RAG
- 低频更新（公司政策）：定时 ETL 增量 embedding
- 文档变更：file watcher + 增量 upsert（按 doc_id 删除旧 chunk + 写入新 chunk）

**Q7：怎么处理 query 跟文档语言不一致？**
两种思路：① 都翻译成英文做检索；② 用多语言 embedding 模型（multilingual-e5）。前者精度高但有翻译损耗，后者一步到位。
