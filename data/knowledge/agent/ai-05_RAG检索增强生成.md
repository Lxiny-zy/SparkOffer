# RAG 检索增强生成

## 1. 什么是 RAG

### 定义
RAG（Retrieval-Augmented Generation，检索增强生成）是一种将外部知识检索与大模型生成相结合的技术框架。不依赖模型本身"记忆"所有知识，而是在生成回答前，先从外部知识库中检索相关文档，将检索到的内容作为上下文提供给 LLM，让 LLM 基于这些信息生成更准确、更有依据的回答。

### 为什么需要 RAG

#### LLM 的固有问题
1. **知识过时**：训练数据有截止日期，无法回答最新信息
2. **幻觉（Hallucination）**：对不知道的问题会"一本正经地胡说八道"
3. **缺乏专业知识**：对企业内部数据、私有文档无法回答
4. **无法溯源**：生成的内容没有来源，无法验证

#### RAG 如何解决
1. **实时性**：检索最新文档，不受训练数据时间限制
2. **可靠性**：回答基于检索到的真实文档，减少幻觉
3. **私有知识**：可以接入企业内部知识库
4. **可溯源**：可以标注引用来源，用户可以验证

### RAG vs 微调

| 维度 | RAG | 微调 |
|------|-----|------|
| 知识更新 | 实时更新文档即可 | 需要重新训练 |
| 成本 | 低（只需构建索引） | 高（需要 GPU 训练） |
| 幻觉控制 | 好（基于检索到的文档） | 一般（模型可能编造） |
| 可溯源 | 可以标注来源 | 无法标注 |
| 适用场景 | 知识密集型问答 | 改变模型行为/风格 |
| 知识量 | 可扩展到 TB 级 | 受模型容量限制 |
| 组合 | **可以 RAG + 微调结合使用** | - |

---

## 2. Naive RAG 全流程

### 整体架构

```
离线阶段（Indexing）:
  文档 → 加载 → 分块 → Embedding → 存入向量数据库

在线阶段（Retrieval + Generation）:
  用户提问 → 查询 Embedding → 向量检索 → 取回文档 → 构建 Prompt → LLM 生成回答
```

### Step 1：文档加载（Document Loading）
```python
# 支持多种格式
from langchain_community.document_loaders import (
    PyMuPDFLoader,           # PDF
    UnstructuredWordDocumentLoader,  # Word
    UnstructuredMarkdownLoader,      # Markdown
    WebBaseLoader,                   # 网页
    CSVLoader,                       # CSV
    DirectoryLoader                  # 目录批量加载
)

# PDF 加载
loader = PyMuPDFLoader("document.pdf")
docs = loader.load()
# 每个 doc 包含: page_content(文本内容) + metadata(来源、页码等)
```

**文档解析挑战**：
- PDF 表格：使用 Camelot、Tabula 提取，或多模态模型识别
- 图片/图表：使用 GPT-4V 等多模态模型提取描述
- 扫描件：OCR（Tesseract、PaddleOCR）
- 复杂排版：Unstructured.io 提供统一处理方案

### Step 2：文本分块（Chunking）

#### 为什么需要分块
- LLM 有上下文长度限制
- 太长的文档作为整体检索效果差（语义稀释）
- 合适大小的块更容易被精准匹配

### Step 3：向量化（Embedding）
```python
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectors = embeddings.embed_documents([chunk.page_content for chunk in chunks])
```

### Step 4：存入向量数据库
```python
from langchain_community.vectorstores import Chroma

vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_db"
)
```

### Step 5：检索与生成
```python
def rag_pipeline(query, vectorstore, llm):
    # 1. 检索
    retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
    relevant_docs = retriever.invoke(query)

    # 2. 构建 prompt
    context = "\n\n".join([doc.page_content for doc in relevant_docs])
    prompt = f"""基于以下参考资料回答用户的问题。如果资料中没有相关信息，请说明。

参考资料：
{context}

用户问题：{query}

回答："""

    # 3. 生成
    answer = llm.invoke(prompt)
    return answer, relevant_docs  # 返回回答和来源
```

---

## 3. 文档分块策略（Chunking）

### 固定大小分块（Fixed-Size Chunking）
```python
from langchain.text_splitter import CharacterTextSplitter

splitter = CharacterTextSplitter(
    chunk_size=500,       # 每块最大字符数
    chunk_overlap=100,    # 相邻块重叠字符数
    separator="\n"
)
chunks = splitter.split_text(document)
```
- 简单快速
- 可能在句子中间切断

### 递归字符分块（Recursive Character Splitting，推荐）
```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
    separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
    # 优先按段落分，其次按句子分，最后按字符分
)
```
- 尽量在自然边界处分割
- 保持文本的完整性
- **最常用的分块方法**

### 语义分块（Semantic Chunking）
```python
from langchain_experimental.text_splitter import SemanticChunker
from langchain_openai import OpenAIEmbeddings

splitter = SemanticChunker(
    OpenAIEmbeddings(),
    breakpoint_threshold_type="percentile",  # 或 "standard_deviation"
    breakpoint_threshold_amount=95
)
chunks = splitter.split_text(document)
```
- 根据相邻句子的 Embedding 相似度自动判断分块边界
- 相似度低于阈值时切分（话题转换处）
- 效果好但计算成本高

### 结构化分块（Document Structure）
- **Markdown**：按标题层级（#, ##, ###）分块
- **HTML**：按 DOM 结构分块
- **代码**：按函数/类分块
- **表格**：整行或整表作为一个块
- 保留文档结构信息作为元数据

### Agentic Chunking（Agent 驱动分块）
- 使用 LLM 判断文本是否应该属于同一个块
- LLM 分析语义连贯性，决定分块边界
- 效果最好但成本最高
- 适合高价值文档的离线处理

### 分块大小选择

| 块大小 | 优点 | 缺点 | 适用场景 |
|--------|------|------|---------|
| 小（100-300 字） | 检索精度高 | 上下文不完整 | 精确 QA |
| 中（300-800 字） | 平衡精度和上下文 | 通用推荐 | 大多数场景 |
| 大（800-2000 字） | 上下文完整 | 语义稀释 | 长文档理解 |

### Overlap 的作用
- 防止重要信息被切断在两个块的边界上
- 通常设置为 chunk_size 的 10%-20%
- 过大的 overlap 会导致索引膨胀和检索重复

### Parent-Child 文档策略
```
理念: 索引用小块（精准匹配），返回用大块（完整上下文）

实现:
1. 将文档按大块切分（Parent, 如 2000 字）
2. 每个大块再切成小块（Child, 如 200 字）
3. 索引 Child 块
4. 检索命中 Child 后，返回其 Parent 块

效果: 既有精准检索又有完整上下文
```

---

## 4. 检索优化

### 查询改写（Query Transformation）

#### Query Rewriting（查询重写）
用 LLM 将用户的模糊问题重写为更清晰、更适合检索的形式：
```
原始查询: "Redis 怎么优化"
重写查询: "Redis 性能优化的方法和最佳实践有哪些？"
```

#### Multi-Query（多查询扩展）
将用户问题扩展为多个不同角度的子问题：
```
原始问题: "Redis 性能优化"
扩展为:
1. "Redis 内存优化有哪些方法？"
2. "Redis 的慢查询如何排查和优化？"
3. "Redis 集群模式如何提升性能？"
4. "Redis 的数据结构选择对性能有什么影响？"

→ 分别检索，合并去重结果
```

#### HyDE（Hypothetical Document Embeddings）
```
1. 让 LLM 先生成一个"假想答案"（不一定准确）
2. 用假想答案的 Embedding 去检索
3. 比用问题直接检索效果更好

原因: 假想答案和真实文档的语义空间更接近
      "问题"和"答案"的 Embedding 分布不同
      用"答案形式"去检索"答案文档"匹配度更高

示例:
  用户问题: "什么是 MVCC？"
  假想答案: "MVCC 是多版本并发控制，它通过维护数据的多个版本来实现..."
  → 用假想答案的 Embedding 检索 → 找到的文档更相关
```

#### Step-Back Prompting
让模型先思考一个更高层次的问题，再回答具体问题：
```
原始问题: "Python 3.12 的 GIL 有什么变化？"
回退问题: "Python 的 GIL 是什么？如何影响并发？"
→ 同时检索两个问题的结果，提供更全面的上下文
```

### 多路召回（Multi-Route Retrieval）

```
用户查询
    ├── 向量检索（语义）      → 候选集 A
    ├── BM25 检索（关键词）    → 候选集 B
    ├── 知识图谱检索（结构化） → 候选集 C
    └── 全文检索（精确匹配）   → 候选集 D
          ↓
    合并去重 → 统一重排序 → Top-K 结果
```

### 混合搜索（Hybrid Search）
- 同时使用向量检索（Dense）和关键词检索（BM25, Sparse）
- 结果融合：
  - **RRF（Reciprocal Rank Fusion）**：`score = sum(1/(k + rank_i))`
  - **加权线性融合**：`score = alpha * dense + (1-alpha) * sparse`
- 兼顾语义理解和精确匹配
- 大多数生产级 RAG 系统都使用混合搜索

```python
from langchain.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever

# 向量检索
dense_retriever = vectorstore.as_retriever(search_kwargs={"k": 10})

# BM25 检索
bm25_retriever = BM25Retriever.from_documents(docs, k=10)

# 混合检索
ensemble_retriever = EnsembleRetriever(
    retrievers=[dense_retriever, bm25_retriever],
    weights=[0.6, 0.4]
)
```

---

## 5. 重排序（Reranking）

### 为什么需要重排序
- 向量检索是 Bi-Encoder：query 和 doc 分别编码，用向量距离比较
- 快但不够精确（没有 query-doc 的深度交互）
- 重排序用 Cross-Encoder：输入 (query, doc) 对，深度交互计算相关性
- 更精确但更慢
- 策略：**先召回多，再精排少**（retrieve top-20 → rerank → top-5）

### Cross-Encoder 重排序
```python
from sentence_transformers import CrossEncoder

reranker = CrossEncoder('BAAI/bge-reranker-v2-m3')
query = "什么是 RAG？"
documents = [
    "RAG 是检索增强生成技术...",
    "机器学习的基础概念...",
    "RAG 的应用场景包括..."
]

# 计算相关性分数
pairs = [(query, doc) for doc in documents]
scores = reranker.predict(pairs)

# 按分数排序
reranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
```

### 常用重排序模型

| 模型 | 特点 |
|------|------|
| BGE-Reranker-v2-m3 | 多语言，效果好，开源 |
| BGE-Reranker-v2-gemma | 基于 LLM 的重排器 |
| Cohere Rerank | API 服务，效果很好 |
| Jina Reranker | 轻量级，速度快 |
| ColBERT | token 级别交互，速度和效果的平衡 |

### ColBERT 延迟交互
```
Cross-Encoder: query+doc → [CLS] token → 相关性分数（最慢但最准）
Bi-Encoder:    query → vec_q, doc → vec_d → 点积（最快但精度一般）
ColBERT:       query → [q1,q2,...], doc → [d1,d2,...] → MaxSim（平衡）

ColBERT MaxSim:
  对 query 的每个 token qi，找到 doc 中最相似的 token dj
  score = sum(max_j(sim(qi, dj))) for all i
```

---

## 6. 高级 RAG 模式

### Self-RAG（自我反思 RAG）

```
流程:
1. 模型判断是否需要检索（IsRet token）
   - 简单问题（"1+1=?"）→ 不需要检索，直接回答
   - 知识性问题 → 需要检索

2. 如果需要检索，执行检索

3. 对每个检索到的文档，模型评估相关性（IsRel token）
   - 相关 → 使用
   - 不相关 → 丢弃

4. 基于相关文档生成回答

5. 模型自我评估生成质量（IsSup + IsUse token）
   - 回答是否被文档支持？（Faithfulness）
   - 回答是否有用？（Usefulness）
   - 如果质量不好，重新生成
```

**优势**：按需检索，减少不必要的检索开销；自动过滤不相关文档；自我评估保证生成质量。

### Corrective RAG（CRAG，纠正性 RAG）

```
流程:
1. 检索文档
2. 用 LLM/分类器评估检索结果质量:
   - Correct（相关）→ 直接使用
   - Incorrect（不相关）→ 切换到网页搜索
   - Ambiguous（模糊）→ 同时使用检索结果和网页搜索

3. 知识精炼: 从有用的文档中提取核心信息
4. 基于精炼后的知识生成回答
```

**优势**：自动修正检索失败；支持降级到外部搜索；提高鲁棒性。

### Adaptive RAG（自适应 RAG）

```
路由策略:
1. 用分类器判断查询类型:
   - 简单查询（事实性问题）→ Naive RAG（单次检索）
   - 复杂查询（多跳推理）→ Advanced RAG（迭代检索）
   - 超复杂查询 → Agent RAG（工具调用+推理）

2. 根据查询类型选择不同的 RAG 策略
3. 动态调整检索深度和生成策略
```

### Graph RAG（图增强 RAG）

```
传统 RAG: 文档 → 块 → 向量 → 检索（独立块，缺乏关联）
Graph RAG: 文档 → 知识图谱 + 块向量 → 图+向量混合检索

构建流程:
1. 从文档中提取实体和关系（用 LLM）
2. 构建知识图谱（实体为节点，关系为边）
3. 社区检测: 将图划分为多个社区
4. 为每个社区生成摘要

检索流程:
1. 向量检索找到相关块
2. 从知识图谱中找到相关实体及其关联
3. 扩展到关联的社区摘要
4. 综合所有信息生成回答
```

**优势**：解决跨文档关联问题；支持多跳推理；全局理解能力强。
**工具**：Microsoft GraphRAG、Neo4j + LLM

### Agentic RAG（Agent 驱动 RAG）

```
将 RAG 融入 Agent 框架:
1. Agent 分析用户问题
2. 规划检索策略（需要检索什么、从哪里检索）
3. 执行检索（可能多轮）
4. 评估检索结果
5. 决定是否需要补充检索或使用其他工具
6. 生成最终回答

优势: 最灵活，可以动态调整策略
适用: 最复杂的知识问答场景
```

---

## 7. RAG 评估

### RAGAS 框架

RAGAS（Retrieval Augmented Generation Assessment）是最流行的 RAG 评估框架：

#### Faithfulness（忠实度）
- 回答中的每个声明是否都能从检索到的上下文中推导出来
- 防止幻觉
- 计算方法：将回答分解为多个声明，逐一验证是否被上下文支持
```
score = 被上下文支持的声明数 / 总声明数
```

#### Answer Relevancy（回答相关性）
- 回答与问题的相关程度
- 回答是否切题
- 计算方法：从回答反向生成问题，计算生成的问题与原始问题的相似度

#### Context Precision（上下文精确度）
- 检索到的文档中，排在前面的是否更相关
- 衡量检索结果的排序质量
```
score = (相关文档的加权排名得分) / (总检索文档数)
```

#### Context Recall（上下文召回率）
- 回答所需的信息是否都能从检索到的文档中找到
- 需要有标准答案（ground truth）
```
score = 标准答案中被上下文覆盖的句子数 / 标准答案总句子数
```

### RAGAS 使用示例
```python
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall
)
from datasets import Dataset

# 准备评估数据
eval_data = {
    "question": ["什么是 MVCC？"],
    "answer": ["MVCC 是多版本并发控制..."],
    "contexts": [["文档1内容...", "文档2内容..."]],
    "ground_truth": ["MVCC (Multi-Version Concurrency Control) 是..."]
}

dataset = Dataset.from_dict(eval_data)

result = evaluate(
    dataset,
    metrics=[faithfulness, answer_relevancy, context_precision, context_recall]
)
print(result)
# {'faithfulness': 0.85, 'answer_relevancy': 0.92,
#  'context_precision': 0.78, 'context_recall': 0.88}
```

### 其他评估方法

| 方法 | 说明 |
|------|------|
| LLM-as-Judge | 用 GPT-4 等强模型打分评估 |
| Human Evaluation | 人工评估，金标准但成本高 |
| DeepEval | 开源评估框架，支持更多指标 |
| LangSmith | LangChain 的评估平台 |

### 评估指标总结

| 指标 | 评估对象 | 需要 Ground Truth |
|------|---------|-----------------|
| Faithfulness | 生成质量（幻觉） | 否 |
| Answer Relevancy | 生成质量（相关性） | 否 |
| Context Precision | 检索质量（排序） | 是 |
| Context Recall | 检索质量（覆盖） | 是 |
| MRR | 检索质量（首个相关排名） | 是 |
| NDCG@k | 检索质量（综合排名） | 是 |

---

## 8. 企业级 RAG 架构设计

### 数据预处理流水线
```
原始文档（PDF/Word/网页/数据库）
    ↓
文档解析（PyMuPDF/Unstructured/Docling）
    ↓
清洗（去噪、去重、格式统一）
    ↓
分块（Recursive + Semantic 混合）
    ↓
元数据提取（来源、时间、作者、类别）
    ↓
Embedding（BGE-M3 或 text-embedding-3）
    ↓
存入向量数据库（Milvus/Qdrant）+ 元数据索引
    ↓
定期增量更新
```

### 完整 RAG 服务架构
```
用户查询
    ↓
查询理解层:
  ├── 意图识别（是否需要检索）
  ├── 查询分类（类型路由）
  └── 查询改写/扩展
    ↓
检索层:
  ├── 向量检索（Dense）
  ├── 关键词检索（BM25/Sparse）
  ├── 知识图谱检索（可选）
  └── 元数据过滤
    ↓
后处理层:
  ├── 融合（RRF/加权）
  ├── 重排序（Cross-Encoder）
  ├── 上下文压缩（提取关键段落）
  └── 去重
    ↓
生成层:
  ├── Prompt 构建（System + Context + Question）
  ├── LLM 生成（流式）
  └── 引用标注（标记来源文档）
    ↓
质量控制:
  ├── 幻觉检测
  ├── 安全过滤
  └── 回答质量评估
```

### 常见问题与解决方案

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 检索不到相关文档 | 语义不匹配 | 查询改写、HyDE、混合搜索、优化分块 |
| 检索到但回答不对 | LLM 不够好或 prompt 不好 | 优化 Prompt、换更强的模型、重排序 |
| 回答包含幻觉 | 检索结果不够好或模型编造 | 重排序、增加 Faithfulness 约束、引用标注 |
| 跨文档问答差 | 相关信息分散 | Graph RAG、多跳检索、Agentic RAG |
| 长文档处理差 | 分块不合理 | 优化分块策略、Parent-Child、层级索引 |
| 表格/图片信息丢失 | 文档解析不完整 | 多模态解析、表格专用处理 |
| 检索延迟高 | 索引效率低 | HNSW 优化、Prefix Caching、缓存热门查询 |

### 性能优化
1. **缓存**：热门查询结果缓存（语义缓存：相似问题复用答案）
2. **异步**：检索和生成可以流水线化
3. **索引优化**：定期重建索引、调优 HNSW 参数
4. **模型选择**：简单问题用小模型，复杂问题用大模型
5. **分块优化**：根据文档类型选择不同分块策略

---

## 面试高频问题

### Q1: RAG 的基本流程？每一步做了什么？
**答**：离线阶段：文档加载 → 文本分块 → Embedding 向量化 → 存入向量数据库。在线阶段：用户提问 → 查询 Embedding → 向量检索 top-k → 构建包含检索文档的 Prompt → LLM 生成回答。核心是将外部知识作为上下文注入 Prompt。

### Q2: RAG 和微调的区别？什么场景用哪个？
**答**：RAG 适合知识密集型问答、知识频繁更新、需要溯源的场景，成本低但依赖检索质量。微调适合改变模型行为/风格、特定任务优化、不需要外部知识的场景，成本高但改变能力更彻底。最佳实践是 RAG + 微调结合使用。

### Q3: 文档分块有哪些策略？如何选择？
**答**：固定大小分块简单但可能断句；递归分块优先在自然边界分割，最推荐；语义分块根据 Embedding 相似度自动判断边界，效果好但成本高；结构化分块按文档结构（标题、函数等）分割。块大小推荐 300-800 字，overlap 为 10-20%。Parent-Child 策略可以同时兼顾检索精度和上下文完整性。

### Q4: 如何提升 RAG 的检索质量？
**答**：多层优化：1) 查询层面：Query Rewriting、Multi-Query、HyDE 改善查询表示；2) 检索层面：混合搜索（Dense+BM25）、多路召回；3) 排序层面：Cross-Encoder 重排序；4) 数据层面：优化分块策略、丰富元数据。核心原则是"先召回多、再精排少"。

### Q5: HyDE 是什么？为什么有效？
**答**：HyDE 让 LLM 先为查询生成一个假想答案，再用假想答案的 Embedding 去检索。有效原因是问题和文档在语义空间中的分布不同（"什么是 X" vs "X 是..."），用答案形式去匹配答案形式的文档，相似度更高。

### Q6: 什么是幻觉？RAG 如何减少幻觉？
**答**：幻觉是模型生成看起来合理但实际错误的内容。RAG 通过以下方式减少幻觉：1) 基于检索到的真实文档生成（有据可查）；2) Prompt 中明确要求"如果资料中没有就说不知道"；3) 使用 RAGAS Faithfulness 指标评估；4) 重排序过滤不相关文档；5) Self-RAG 自我评估生成质量。

### Q7: Self-RAG、CRAG、Adaptive RAG 的区别？
**答**：Self-RAG 在模型内部集成"是否需要检索"和"生成质量评估"的能力，按需检索+自我反思。CRAG 在检索后评估结果质量，不好则切换到网页搜索等备选方案。Adaptive RAG 根据查询复杂度动态选择 RAG 策略（简单→Naive RAG，复杂→迭代检索，超复杂→Agent RAG）。

### Q8: Graph RAG 解决什么问题？
**答**：传统 RAG 将文档切成独立块，丢失了文档间的关联和全局信息。Graph RAG 从文档中提取实体和关系构建知识图谱，支持跨文档关联和多跳推理。例如"A 公司的 CEO 毕业于哪所大学"需要两跳推理，向量检索可能找不全，Graph RAG 可以沿着图谱路径找到完整信息。

### Q9: 如何评估 RAG 系统的效果？
**答**：使用 RAGAS 框架从四个维度评估：Faithfulness（回答是否忠于文档，检测幻觉）、Answer Relevancy（回答是否切题）、Context Precision（检索排序质量）、Context Recall（检索覆盖度）。还可以用 LLM-as-Judge 进行端到端评估。构建黄金测试集（问题+标准答案+相关文档）是评估的基础。

### Q10: 企业级 RAG 架构要考虑哪些问题？
**答**：1) 数据层：多格式文档解析、增量更新、数据质量；2) 检索层：混合搜索、重排序、元数据过滤；3) 生成层：Prompt 优化、引用标注、幻觉控制；4) 工程层：缓存（热门查询语义缓存）、监控（检索质量+生成质量）、AB 测试；5) 安全层：权限控制（不同用户看不同文档）、PII 脱敏。

### Q11: 重排序有哪些方法？Cross-Encoder 和 Bi-Encoder 的区别？
**答**：Bi-Encoder 将 query 和 doc 分别编码为向量，用向量距离比较，速度快但精度一般。Cross-Encoder 将 (query, doc) 拼接后一起输入模型，深度交互计算相关性，精度高但速度慢。ColBERT 是折中方案：token 级别编码+延迟交互。实践中用 Bi-Encoder 召回 top-20，Cross-Encoder 重排取 top-5。
