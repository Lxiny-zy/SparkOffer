# LlamaIndex 框架

## 1. LlamaIndex 概览

### 定位
LlamaIndex 是专注于 **"LLM 连接数据"** 的框架，核心场景是 RAG（检索增强生成）和数据密集型 Agent。相比 LangChain 更"数据导向"。

### 定位对比

| 框架 | 核心定位 | 强项 | 弱项 |
|------|----------|------|------|
| **LlamaIndex** | LLM + 数据 | Indexing、RAG、Query Engine、Workflow | Agent 生态略小 |
| **LangChain** | LLM 应用通用框架 | Chain、Tool、Agent、集成广 | 数据管线较基础 |
| **Haystack** | 企业 RAG | 生产级 pipeline、可视化 | 较重 |

### 生态模块

```
llama-index-core              核心抽象
llama-index-llms-*            LLM 集成（openai、anthropic、ollama…）
llama-index-embeddings-*      Embedding 模型
llama-index-readers-*         数据源读取器（PDF、Notion、Confluence、MySQL…）
llama-index-vector-stores-*   向量库（Chroma、Qdrant、Pinecone、Milvus…）
llama-index-retrievers-*      检索器
llama-index-postprocessor-*   重排序、过滤
llama-index-agent-*           Agent 实现
llama-index-workflows         事件驱动流程编排
llama-parse                   商业级文档解析
llama-cloud                   托管服务
```

---

## 2. 核心概念

### Document / Node
- **Document**：原始文件（一整篇 PDF / 网页）
- **Node**：切分后的最小检索单元（一段文字），带 metadata、relationships

```python
from llama_index.core import Document

doc = Document(
    text="LlamaIndex 是 LLM 与数据连接的框架...",
    metadata={"source": "官网", "date": "2024-01"}
)
```

### Index
把 Nodes 组织成可查询的数据结构：

| Index 类型 | 原理 | 适用 |
|-----------|------|------|
| **VectorStoreIndex** | 向量相似度 | 最常用，语义检索 |
| **SummaryIndex** | 顺序遍历全部 | 小文档、要全量遍历 |
| **TreeIndex** | 层级摘要树 | 结构化文档 |
| **KeywordTableIndex** | 关键词倒排 | 关键词精确匹配 |
| **KnowledgeGraphIndex** | 知识图谱 | 实体关系复杂 |
| **DocumentSummaryIndex** | 每文档一个摘要 | 多文档路由 |
| **ComposableGraph** | 组合多 Index | 异构数据源 |

### Query Engine
封装"检索 + 合成"逻辑，接收 query，返回 Response。

```python
query_engine = index.as_query_engine(similarity_top_k=5)
response = query_engine.query("LlamaIndex 适合什么场景？")
```

### Retriever
仅做检索（返回 Nodes），不调 LLM：

```python
retriever = index.as_retriever(similarity_top_k=5)
nodes = retriever.retrieve("...")
```

### Response Synthesizer
把检索到的 Nodes 合成最终答案，多种策略：
- **Refine**：逐 chunk 迭代精化
- **Compact**：尽可能塞进上下文
- **Tree Summarize**：层级摘要
- **Simple Summarize**：一次性合成

---

## 3. Quick Start

### 安装
```bash
pip install llama-index llama-index-llms-openai llama-index-embeddings-openai
```

### 5 行代码 RAG

```python
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader

docs = SimpleDirectoryReader("./data").load_data()
index = VectorStoreIndex.from_documents(docs)
query_engine = index.as_query_engine()
response = query_engine.query("文档主要讲什么？")
print(response)
```

### 持久化

```python
# 保存
index.storage_context.persist(persist_dir="./storage")

# 加载
from llama_index.core import StorageContext, load_index_from_storage
storage_context = StorageContext.from_defaults(persist_dir="./storage")
index = load_index_from_storage(storage_context)
```

---

## 4. 数据加载（Readers）

```python
# 本地文件
from llama_index.core import SimpleDirectoryReader
docs = SimpleDirectoryReader("./data", recursive=True).load_data()

# PDF（高质量解析）
from llama_index.readers.file import PDFReader
docs = PDFReader().load_data(file="./paper.pdf")

# 网页
from llama_index.readers.web import SimpleWebPageReader
docs = SimpleWebPageReader().load_data(urls=["https://..."])

# 数据库
from llama_index.readers.database import DatabaseReader
reader = DatabaseReader(uri="postgresql://...")
docs = reader.load_data(query="SELECT id, content FROM articles")

# Notion / Confluence / GitHub 等
pip install llama-index-readers-notion
from llama_index.readers.notion import NotionPageReader
```

**LlamaHub** 收录 300+ 数据源 Reader。

### LlamaParse（商业级解析）
处理复杂 PDF（表格、公式、图文混排）：
```python
from llama_parse import LlamaParse
docs = LlamaParse(result_type="markdown").load_data("./complex.pdf")
```

---

## 5. Indexing 与切分

### 默认切分
```python
from llama_index.core.node_parser import SentenceSplitter

splitter = SentenceSplitter(
    chunk_size=512,
    chunk_overlap=50,
    separator=" "
)
nodes = splitter.get_nodes_from_documents(docs)
```

### Semantic Splitter（语义切分）
按嵌入相似度突变处分段，避免强行按长度切：
```python
from llama_index.core.node_parser import SemanticSplitterNodeParser

splitter = SemanticSplitterNodeParser(
    buffer_size=1,
    breakpoint_percentile_threshold=95,
    embed_model=embed_model
)
```

### HierarchicalNodeParser（层级切分）
生成多粒度 Nodes（大块 + 小块 + 句子），配合 Auto-Merging Retriever：
```python
from llama_index.core.node_parser import HierarchicalNodeParser

parser = HierarchicalNodeParser.from_defaults(
    chunk_sizes=[2048, 512, 128]
)
```

### Metadata 注入

```python
from llama_index.core.extractors import (
    TitleExtractor, QuestionsAnsweredExtractor, SummaryExtractor
)

extractors = [
    TitleExtractor(nodes=5),
    QuestionsAnsweredExtractor(questions=3),
    SummaryExtractor(summaries=["prev", "self"])
]
# 每个 Node 增加 title、可回答的问题、摘要 metadata
```

---

## 6. 高级检索

### 基础
```python
retriever = index.as_retriever(
    similarity_top_k=10
)
```

### Hybrid（向量 + 关键字）
```python
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever

bm25 = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=10)
vector = index.as_retriever(similarity_top_k=10)

retriever = QueryFusionRetriever(
    [bm25, vector],
    similarity_top_k=5,
    num_queries=4,  # 多查询重写
    mode="reciprocal_rerank",  # RRF 融合
    use_async=True,
)
```

### Query Rewriting（查询改写）
```python
from llama_index.core.query_engine import MultiStepQueryEngine

ms_engine = MultiStepQueryEngine(
    query_engine=base_engine,
    query_transform=StepDecomposeQueryTransform(llm=llm, verbose=True),
    num_steps=3
)
```

### Sub-Question（子问题分解）
```python
from llama_index.core.query_engine import SubQuestionQueryEngine

engine = SubQuestionQueryEngine.from_defaults(
    query_engine_tools=[tool1, tool2],
    llm=llm
)
# 复杂问题 → 拆成多个子问题 → 分别查不同 index → 综合
```

### Auto-Merging（层级合并）
小块检索，命中多时自动合并到父块：
```python
from llama_index.core.retrievers import AutoMergingRetriever

retriever = AutoMergingRetriever(
    base_retriever,
    storage_context,
    verbose=True
)
```

### Sentence Window
索引时按句子存，检索到相关句子后返回**周围 N 句上下文**：
```python
from llama_index.core.node_parser import SentenceWindowNodeParser

parser = SentenceWindowNodeParser.from_defaults(window_size=3)
```

### Recursive Retrieval（递归检索）
先找章节摘要，再从章节下 drill down：
```python
from llama_index.core.retrievers import RecursiveRetriever

retriever = RecursiveRetriever(
    "vector",
    retriever_dict={"vector": base_retriever, "section1": sub_retriever},
    node_dict=all_nodes_dict,
)
```

---

## 7. 后处理与重排序

```python
from llama_index.core.postprocessor import (
    SimilarityPostprocessor,
    KeywordNodePostprocessor,
    MetadataReplacementPostProcessor,
    SentenceEmbeddingOptimizer
)
from llama_index.postprocessor.cohere_rerank import CohereRerank

# 过滤低相似度
filter = SimilarityPostprocessor(similarity_cutoff=0.7)

# Cohere 重排序
rerank = CohereRerank(top_n=3, api_key="...")

# BGE Reranker
from llama_index.postprocessor.flag_embedding_reranker import FlagEmbeddingReranker
rerank = FlagEmbeddingReranker(top_n=3, model="BAAI/bge-reranker-large")

query_engine = index.as_query_engine(
    similarity_top_k=10,
    node_postprocessors=[filter, rerank]
)
```

---

## 8. Agent

### FunctionAgent（推荐，新版）
```python
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.tools import FunctionTool

def multiply(a: float, b: float) -> float:
    """计算乘法"""
    return a * b

tool = FunctionTool.from_defaults(fn=multiply)

agent = FunctionAgent(
    tools=[tool],
    llm=llm,
    system_prompt="你是数学助手"
)

response = await agent.run("3.14 乘以 2.5 是多少？")
```

### ReActAgent
```python
from llama_index.core.agent.workflow import ReActAgent

agent = ReActAgent(
    tools=[...],
    llm=llm
)
```

### QueryEngineTool（把 QueryEngine 当工具）
```python
from llama_index.core.tools import QueryEngineTool

tool = QueryEngineTool.from_defaults(
    query_engine=legal_docs_engine,
    name="legal_qa",
    description="查询公司法律文档"
)
agent = FunctionAgent(tools=[tool, ...])
```

### Agent Workflows（新架构）
LlamaIndex 新一代 Agent 基于 `Workflow` 实现，见下一节。

---

## 9. Workflows（事件驱动流程）

LlamaIndex 2024 推出的新编排方式，类似 LangGraph 但更轻量。

### 核心概念
- **Event**：事件（消息）
- **Step**：处理某类事件，返回新事件

```python
from llama_index.core.workflow import (
    Workflow, step, Event, StartEvent, StopEvent, Context
)

class SearchEvent(Event):
    query: str

class AnalyzeEvent(Event):
    results: list

class MyFlow(Workflow):
    @step
    async def start(self, ev: StartEvent) -> SearchEvent:
        return SearchEvent(query=ev.input)

    @step
    async def search(self, ev: SearchEvent) -> AnalyzeEvent:
        results = await web_search(ev.query)
        return AnalyzeEvent(results=results)

    @step
    async def analyze(self, ev: AnalyzeEvent) -> StopEvent:
        summary = await llm.acomplete(f"总结：{ev.results}")
        return StopEvent(result=summary)

flow = MyFlow(timeout=60)
result = await flow.run(input="AI 最新进展")
```

### 特性
- **事件驱动**：step 由事件类型路由，天然解耦
- **并发**：多个 step 可并行发射事件
- **可视化**：自动生成流程图
- **断点续跑**：支持中断恢复
- **人机协同**：可插入等待人工输入

---

## 10. 多模态

```python
from llama_index.core.schema import ImageDocument
from llama_index.multi_modal_llms.openai import OpenAIMultiModal

mm_llm = OpenAIMultiModal(model="gpt-4o")
images = [ImageDocument(image_path="./chart.png")]
response = mm_llm.complete(prompt="图里是什么？", image_documents=images)
```

多模态 RAG（图文混合索引）：
```python
from llama_index.core.indices import MultiModalVectorStoreIndex

index = MultiModalVectorStoreIndex.from_documents(
    docs,  # 包含 Document 和 ImageDocument
    image_embed_model=CLIP_embed_model,
)
```

---

## 11. 生产化

### 异步
```python
response = await query_engine.aquery("...")
# 所有 API 都有 a 前缀的异步版本
```

### 流式
```python
streaming_engine = index.as_query_engine(streaming=True)
resp = streaming_engine.query("...")
for token in resp.response_gen:
    print(token, end="")
```

### LlamaCloud（托管服务）
- 托管 Parsing（LlamaParse）
- 托管 Index（不用自己跑 embedding）
- 数据自动同步（S3、Google Drive、Notion）

### 部署
```python
# FastAPI 包装
from fastapi import FastAPI
app = FastAPI()

@app.post("/query")
async def query(q: str):
    return await query_engine.aquery(q)
```

---

## 12. LlamaIndex vs LangChain 何时选谁

| 场景 | 首选 |
|------|------|
| 纯 RAG，数据源复杂 | LlamaIndex |
| Agent + 多工具 + 复杂流程 | LangChain / LangGraph |
| 多种 Index 结构 | LlamaIndex |
| 需要大量现成 Chain | LangChain |
| 事件驱动工作流 | LlamaIndex Workflows |
| 状态图 Agent | LangGraph |
| 企业生产、可观测性 | 两者 + LangSmith / LangFuse |

**实务**：不冲突，可混用。LlamaIndex 当 RAG 组件，LangGraph 编排上层 Agent。

---

## 13. 实战：企业级多源 RAG

### 需求
- 数据源：Notion + Confluence + PDF
- 问题分流：不同主题到不同数据源
- 混合检索：向量 + BM25
- 重排序：Cohere Rerank
- 引用：答案附来源

### 架构

```python
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core.tools import QueryEngineTool
from llama_index.core.query_engine import RouterQueryEngine
from llama_index.core.selectors import LLMSingleSelector

# 1. 三个数据源各自建索引
notion_index = build_index(notion_docs, collection="notion")
conf_index = build_index(conf_docs, collection="confluence")
pdf_index = build_index(pdf_docs, collection="pdfs")

# 2. 每个索引包装成 Tool
notion_engine = notion_index.as_query_engine(
    similarity_top_k=10,
    node_postprocessors=[rerank]
)
notion_tool = QueryEngineTool.from_defaults(
    query_engine=notion_engine,
    name="notion_qa",
    description="产品文档、PRD、设计稿"
)
conf_tool = QueryEngineTool.from_defaults(
    query_engine=conf_index.as_query_engine(...),
    name="confluence_qa",
    description="技术文档、架构、部署手册"
)
pdf_tool = QueryEngineTool.from_defaults(
    query_engine=pdf_index.as_query_engine(...),
    name="pdf_qa",
    description="研究报告、合同、法务文件"
)

# 3. Router 分流
router = RouterQueryEngine(
    selector=LLMSingleSelector.from_defaults(llm=llm),
    query_engine_tools=[notion_tool, conf_tool, pdf_tool]
)

# 4. 查询
response = router.query("去年 Q3 的产品路线图是什么？")
for node in response.source_nodes:
    print(node.metadata["source"], node.score)
```

---

## 面试高频问题

**Q1：LlamaIndex 和 LangChain 核心区别？**

- **LlamaIndex**：数据为中心，强 Indexing/RAG/Query Engine，多种 Index 结构（Vector/Tree/KG/Summary）
- **LangChain**：通用 LLM 应用框架，强 Chain/Tool/Agent，生态最广

LlamaIndex 做"让 LLM 理解你的数据"更强；LangChain 做"组合各种 LLM 组件"更强。实际可混用。

**Q2：LlamaIndex 有哪些 Index 类型？**

- **VectorStoreIndex**：向量相似度（最常用）
- **SummaryIndex**：顺序遍历全部 Nodes
- **TreeIndex**：层级摘要树
- **KeywordTableIndex**：关键词倒排
- **KnowledgeGraphIndex**：实体关系图
- **DocumentSummaryIndex**：文档级摘要
- **ComposableGraph**：组合多种 Index

选择依据：数据量、查询类型、是否需要全量遍历、实体关系强弱。

**Q3：Node 和 Document 的关系？**

- **Document**：原始文件（一整篇 PDF）
- **Node**：切分后的检索单元，带 metadata、relationships（previous/next/parent）

Index 以 Node 为最小单位。Node 可以携带丰富 metadata（source、date、title），支持过滤检索。

**Q4：Auto-Merging Retriever 是什么？**

层级切分（大块 → 中块 → 小块）后，检索小块获得高精度，但多个相邻小块命中时**自动合并回父块**提供更完整上下文。兼顾精度和完整性。

比单纯大块检索更精准，比单纯小块检索上下文更完整。

**Q5：Sub-Question Query Engine 解决什么问题？**

复杂问题（需要多来源信息）无法单次检索回答。例：
- 问题：对比 A 公司和 B 公司 Q3 财务
- Sub-Question 拆分：
  - Q1: A 公司 Q3 财报
  - Q2: B 公司 Q3 财报
- 分别查对应 Index，综合得出对比结论

关键：**问题分解 + 工具路由 + 结果综合**。

**Q6：LlamaIndex 如何处理大规模数据？**

- **向量库**：Qdrant/Milvus/Weaviate 等分布式后端
- **异步 API**：所有方法都有 `a` 前缀版本
- **Batch Embedding**：批量调用 embedding API
- **LlamaCloud**：托管 Indexing，不用本地跑
- **增量更新**：支持按需添加/删除 Nodes
- **Metadata 过滤**：先按用户/租户 ID 过滤减少检索范围

**Q7：Workflows 对比 LangGraph？**

**共同**：事件/状态驱动、支持并发、可视化、断点续跑。

**差异**：
- LlamaIndex Workflow：事件驱动（step 由事件类型自动路由），代码更简洁
- LangGraph：状态图驱动（显式定义节点和边），更适合有明确拓扑的场景

前者"声明式事件"，后者"声明式图"。LangGraph 生态成熟、集成 LangSmith；Workflow 更新但更轻量。

**Q8：LlamaIndex 如何做 Hybrid Search？**

```python
from llama_index.core.retrievers import QueryFusionRetriever
retriever = QueryFusionRetriever(
    [bm25_retriever, vector_retriever],
    mode="reciprocal_rerank",
    num_queries=4
)
```

- 向量检索：语义相似
- BM25：关键字精确
- RRF 融合：两路结果合并
- 查询改写：生成多个变体 query 提升召回

**Q9：LlamaParse 解决什么问题？**

普通 PDF 解析（pypdf、pdfplumber）对复杂文档效果差：
- 表格破碎
- 公式丢失
- 图文混排错乱
- 扫描件无法 OCR

LlamaParse 是商业级服务，基于视觉大模型，把 PDF 转成高质量 Markdown，表格、公式、版式都保留。对科研论文、财报、合同效果显著。

**Q10：生产 RAG 还需要什么？**

LlamaIndex 基础设施之上：
- **可观测性**：LangFuse/Arize 记录每次检索
- **评估**：Ragas 三指标持续跑
- **缓存**：相同 query 缓存
- **限流**：向量库、LLM API 限流保护
- **权限**：metadata 按租户/权限过滤
- **更新机制**：数据源变更触发增量索引
- **A/B 测试**：新旧检索策略对比
- **Fallback**：检索失败回退到普通对话
