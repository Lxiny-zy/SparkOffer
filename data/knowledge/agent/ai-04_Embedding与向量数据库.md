# Embedding 与向量数据库

## 1. Embedding 原理

### 什么是 Embedding
将文本（词、句子、段落）映射为固定维度的稠密向量（如 768 维或 1024 维），使得语义相近的文本在向量空间中距离相近。Embedding 是 RAG、语义搜索、推荐系统等应用的基础。

### 词向量发展历程

#### One-Hot Encoding
- 维度等于词表大小（如 30000 维），只有一个维度为 1，其余为 0
- 稀疏，无语义信息，"猫"和"狗"的距离与"猫"和"桌子"一样
- 无法表示词与词之间的关系

#### Word2Vec（2013, Google）
- 开创性工作，将词表示为低维稠密向量（如 300 维）
- 两种训练方式：
  - **CBOW（Continuous Bag of Words）**：用周围词预测中心词
  - **Skip-gram**：用中心词预测周围词
- 核心训练目标：最大化 `P(context | word)` 或 `P(word | context)`
- 学到的向量包含语义关系：
  ```
  king - man + woman ≈ queen
  Paris - France + Japan ≈ Tokyo
  ```
- 负采样（Negative Sampling）：优化训练效率
- **局限**：每个词只有一个向量，无法处理多义词（"苹果"：水果 vs 公司）

#### GloVe（2014, Stanford）
- 基于全局词共现矩阵（word co-occurrence matrix）
- 结合了 Word2Vec 的局部上下文和全局统计信息
- 目标：词向量的点积 ≈ 共现次数的对数
- 效果与 Word2Vec 可比

#### ELMo（2018）
- 基于双向 LSTM 的**上下文化**词向量
- 同一个词在不同上下文有不同表示
- "苹果很好吃"中的"苹果" vs "苹果发布了新 iPhone"中的"苹果" → 不同向量
- 预训练 + 微调的先驱

#### BERT Embedding（2018）
- 基于 Transformer Encoder 的深层上下文理解
- 使用 [CLS] token 的输出作为句子表示
- 或使用所有 token 的平均池化（Mean Pooling）
- 比 ELMo 效果更好，但直接用 BERT 的 [CLS] 做句子相似度效果不够理想

#### Sentence-BERT（2019）
- 专门为句子级别语义相似度优化
- 使用对比学习（Contrastive Learning）训练：
  - 正样本对：语义相似的句子
  - 负样本对：语义不相似的句子
- 训练目标：正样本对的向量距离近，负样本对的距离远
- 比直接用 BERT 的效果大幅提升

#### 现代 Embedding 模型
通过更大规模的对比学习训练，效果进一步提升：

```
训练流程:
1. 收集大量文本对（query-doc, 问题-答案, 同义句子等）
2. 使用 BERT/LLM 作为 backbone
3. 对比学习损失（InfoNCE/CoSENT/Matryoshka）
4. 指令微调（让模型理解检索 vs 分类等不同任务）
```

---

## 2. 现代 Embedding 模型

### BGE 系列（BAAI 智源）

#### bge-large-zh-v1.5
- 中文效果好，768 维
- 适合中文 RAG 场景

#### BGE-M3
- **多语言（Multi-Lingual）**：支持 100+ 语言
- **多粒度（Multi-Granularity）**：支持句子和段落级别
- **多功能（Multi-Functionality）**：
  - Dense Embedding：稠密向量检索
  - Sparse Embedding（Lexical）：稀疏向量（类似 BM25）
  - ColBERT：token 级别的交互式检索
- 最大输入长度 8192 tokens
- 一个模型同时支持三种检索方式，实现混合搜索

### OpenAI text-embedding-3
- **text-embedding-3-small**：1536 维，性价比高
- **text-embedding-3-large**：3072 维，效果最好
- 支持 **Matryoshka Embedding（套娃嵌入）**：
  - 可以截断到任意维度（如 3072 → 1024 → 256）
  - 截断后仍保持较好的语义质量
  - 灵活权衡存储空间和效果

### E5 系列（Microsoft）
- E5-base/large：基于 BERT 的 Embedding
- E5-mistral-7b-instruct：基于 LLM 的 Embedding，效果很好
- 支持指令前缀：不同任务使用不同的前缀（如 "query:", "passage:"）

### GTE 系列（阿里）
- gte-large-zh：中文 Embedding
- gte-Qwen2：基于 Qwen2 的 LLM Embedding

### Cohere Embed v3
- 支持多种输入类型（search_document, search_query, classification, clustering）
- 输出 float 和 int8/binary 多种精度

### 如何选择 Embedding 模型

| 考虑因素 | 说明 |
|---------|------|
| 语言支持 | 中文场景选 BGE/GTE，多语言选 BGE-M3 |
| 维度大小 | 维度越高效果越好，但存储和检索更慢 |
| 最大长度 | 长文档场景需要大 max_length |
| 性能 | 参考 MTEB 排行榜 |
| 成本 | 本地部署 vs API 调用 |
| 延迟 | 小模型（384维）延迟低，适合实时场景 |

**MTEB（Massive Text Embedding Benchmark）**：
- 最权威的 Embedding 评估基准
- 涵盖检索、分类、聚类、重排、STS 等多种任务
- C-MTEB 是中文版本
- 选型时的重要参考

### Embedding API 调用
```python
from openai import OpenAI

client = OpenAI()
response = client.embeddings.create(
    model="text-embedding-3-small",
    input=["什么是 RAG？", "检索增强生成技术"],
    dimensions=512  # Matryoshka: 可以指定截断维度
)

vec1 = response.data[0].embedding  # 512 维向量
vec2 = response.data[1].embedding

# 计算余弦相似度
import numpy as np
similarity = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
```

### 本地 Embedding 部署
```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('BAAI/bge-m3')
texts = ["什么是 RAG？", "检索增强生成技术"]
embeddings = model.encode(texts, normalize_embeddings=True)

# 余弦相似度（已归一化，点积即余弦相似度）
similarity = embeddings[0] @ embeddings[1]
```

---

## 3. 相似度度量

### 余弦相似度（Cosine Similarity）

$$\cos(\mathbf{A}, \mathbf{B}) = \frac{\mathbf{A} \cdot \mathbf{B}}{||\mathbf{A}|| \times ||\mathbf{B}||}$$

- 计算两个向量夹角的余弦值
- 范围 [-1, 1]，1 表示完全相同方向，0 表示正交，-1 表示完全相反
- **最常用**，不受向量长度（模）影响，只关注方向
- 适合文本语义搜索

### 欧氏距离（L2 Distance / Euclidean Distance）

$$d(\mathbf{A}, \mathbf{B}) = \sqrt{\sum_{i=1}^{n}(A_i - B_i)^2}$$

- 向量空间中两点的直线距离
- 距离越小越相似
- 受向量长度影响
- 适合需要考虑向量大小的场景

### 内积（Dot Product / IP）

$$\text{IP}(\mathbf{A}, \mathbf{B}) = \sum_{i=1}^{n} A_i \times B_i$$

- 如果向量已归一化（模为 1），等价于余弦相似度
- 计算速度最快（无需归一化步骤）
- 适合已归一化的向量

### 三者关系
```
当向量已归一化（||A|| = ||B|| = 1）时：
  余弦相似度 = 内积 = A · B
  欧氏距离 = sqrt(2 - 2 * cosine_similarity)

因此：
  余弦相似度最大 ↔ 欧氏距离最小 ↔ 内积最大
  三种度量的排序结果完全一致
```

### 选择建议
- 文本语义搜索：**余弦相似度**（最通用）
- 已归一化的向量：**内积**（更快）
- 需要考虑向量大小的场景：**欧氏距离**
- 大多数 Embedding 模型输出已归一化 → 三者等价，用内积最快

---

## 4. 向量索引算法（ANN）

### 为什么需要 ANN
- 精确最近邻搜索（brute force）需要计算查询向量与所有向量的距离
- 百万级向量 × 1024 维 = 数十亿次浮点运算
- ANN（Approximate Nearest Neighbor）用少量精度损失换取几个数量级的速度提升

### HNSW（Hierarchical Navigable Small World）

**最流行的索引算法，大多数向量数据库的默认索引。**

**原理**：
- 构建多层图结构，类似跳表（Skip List）
- 每一层是一个近邻图（Small World Graph）
- 底层包含所有节点（完整近邻图）
- 每上一层，节点数指数减少（稀疏图）

**查询过程**：
```
1. 从最顶层的一个入口节点开始
2. 在当前层贪心搜索最近的邻居
3. 找到当前层的局部最优 → 下降到下一层
4. 重复直到到达底层
5. 在底层精确搜索邻域
```

**关键参数**：
- **M**：每个节点的最大邻居数（通常 16-64）
  - M 越大 → 精度越高，内存越大，构建越慢
- **ef_construction**：构建时的搜索范围（通常 128-512）
- **ef_search**：查询时的搜索范围（通常 64-256）
  - ef_search 越大 → 精度越高，查询越慢

**特点**：
- 查询速度最快（对数级别）
- 内存占用大（需要存储图结构）
- 不支持删除（需要重建）
- 适合需要极快查询的场景

### IVF（Inverted File Index）

**原理**：
- 用 K-Means 将向量空间聚类为 nlist 个簇（Voronoi cell）
- 每个向量分配到最近的簇心
- 查询时，找到最近的 nprobe 个簇，只在这些簇内搜索

**关键参数**：
- **nlist**：簇数量（通常 sqrt(n) 到 4*sqrt(n)）
- **nprobe**：查询时搜索的簇数量（通常 nlist 的 1-10%）
  - nprobe 越大 → 精度越高，速度越慢

**特点**：
- 构建速度快（一次 K-Means）
- 查询速度中等
- 可以与 PQ 组合使用
- 适合大规模数据

### PQ（Product Quantization）

**原理**：
```
1. 将 d 维向量切分为 m 个子向量（每个 d/m 维）
2. 对每个子向量空间独立做 K-Means 聚类（如 K=256）
3. 每个子向量用聚类中心的编号（1 字节）表示
4. 原始向量 → m 字节的压缩码

压缩比（1024 维 float32）：
  原始: 1024 * 4 = 4096 字节
  PQ(m=64, K=256): 64 * 1 = 64 字节
  压缩比: 64x
```

**特点**：
- 大幅压缩存储空间
- 距离计算可以用查表（ADC, Asymmetric Distance Computation）加速
- 精度有损失（取决于 m 和 K）
- 适合显存/内存有限的大规模场景

### ScaNN（Google）
- 各向异性量化（Anisotropic Quantization）
- 根据查询方向优化量化误差
- 在精度-速度权衡上优于 PQ
- Google 内部大量使用

### IVF-PQ
- IVF + PQ 的组合
- IVF 粗筛 → PQ 细排
- 兼顾速度和存储效率
- 十亿级向量的常用方案

### DiskANN
- 将索引存储在 SSD 上，内存只保留少量信息
- 支持十亿级向量的搜索，内存占用极低
- 适合内存有限但有 SSD 的场景

### 索引算法对比

| 算法 | 查询速度 | 内存占用 | 精度 | 适用场景 |
|------|---------|---------|------|---------|
| Flat (暴力) | 最慢 | O(n*d) | 100% | 小数据集/基准 |
| HNSW | 最快 | 最大 | 最高 | 实时查询 |
| IVF | 中等 | 中等 | 中等 | 大规模 |
| PQ | 快 | 最小 | 较低 | 压缩存储 |
| IVF-PQ | 快 | 小 | 中等 | 大规模+压缩 |
| ScaNN | 快 | 中等 | 高 | Google 生态 |
| DiskANN | 中等 | 极小 | 高 | 十亿级向量 |

---

## 5. 向量数据库

### 为什么需要向量数据库
- 传统数据库只能做精确匹配或关键词搜索
- 向量数据库支持**近似最近邻搜索（ANN）**，在百万/十亿级向量中毫秒级找到最相似的 top-k
- 提供元数据过滤、持久化存储、分布式扩展等完整功能

### 主流向量数据库对比

| 数据库 | 类型 | 特点 | 适用场景 |
|--------|------|------|---------|
| Chroma | 嵌入式 | 轻量，Python 原生，开箱即用 | 原型开发、小规模 |
| FAISS | 库 | Meta 出品，纯 CPU/GPU，性能极高 | 大规模离线检索 |
| Milvus | 分布式 | 云原生，支持十亿级向量 | 生产环境大规模 |
| Qdrant | 独立部署 | Rust 编写，支持丰富的过滤条件 | 需要复杂过滤 |
| Pinecone | 云服务 | 全托管，无需运维 | 快速上线、不想自运维 |
| Weaviate | 独立部署 | 支持混合搜索、多模态 | 多模态搜索 |
| pgvector | PG 插件 | 直接在 PostgreSQL 中使用 | 已有 PG 的项目 |

### 选型建议

```
开发/原型阶段 → Chroma（最简单）
  ↓
中小规模生产 → Qdrant 或 pgvector
  ↓
大规模生产 → Milvus（自部署）或 Pinecone（全托管）
  ↓
离线批量处理 → FAISS（纯库，性能最高）
```

### Chroma 使用示例
```python
import chromadb

client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection(
    name="my_docs",
    metadata={"hnsw:space": "cosine"}  # 使用余弦相似度
)

# 添加文档（Chroma 内置 Embedding）
collection.add(
    ids=["doc1", "doc2", "doc3"],
    documents=["Python 是一门编程语言", "Java 是静态类型语言", "机器学习使用数据训练模型"],
    metadatas=[{"topic": "python"}, {"topic": "java"}, {"topic": "ml"}]
)

# 使用自定义 Embedding 添加
collection.add(
    ids=["doc4"],
    embeddings=[[0.1, 0.2, ...]],  # 预计算的向量
    documents=["文档内容"],
    metadatas=[{"topic": "custom"}]
)

# 查询
results = collection.query(
    query_texts=["编程语言有哪些？"],
    n_results=2,
    where={"topic": {"$in": ["python", "java"]}},  # 元数据过滤
    include=["documents", "distances", "metadatas"]
)
```

### FAISS 使用示例
```python
import faiss
import numpy as np

d = 768  # 向量维度
n = 1000000  # 向量数量

# 精确搜索（暴力）
index = faiss.IndexFlatL2(d)

# HNSW 近似搜索
index = faiss.IndexHNSWFlat(d, 32)  # M=32
index.hnsw.efConstruction = 200
index.hnsw.efSearch = 128

# IVF 近似搜索
nlist = 1024
quantizer = faiss.IndexFlatL2(d)
index = faiss.IndexIVFFlat(quantizer, d, nlist)
index.train(training_vectors)  # 需要训练
index.nprobe = 64

# IVF-PQ（大规模场景）
m = 64  # PQ 子向量数
index = faiss.IndexIVFPQ(quantizer, d, nlist, m, 8)
index.train(training_vectors)

# 添加向量
vectors = np.random.random((n, d)).astype('float32')
index.add(vectors)

# 搜索最近的 5 个
query = np.random.random((1, d)).astype('float32')
distances, indices = index.search(query, k=5)

# GPU 加速
res = faiss.StandardGpuResources()
gpu_index = faiss.index_cpu_to_gpu(res, 0, index)
```

### Milvus 使用示例
```python
from pymilvus import MilvusClient

client = MilvusClient("http://localhost:19530")

# 创建 Collection
client.create_collection(
    collection_name="my_docs",
    dimension=768,
    metric_type="COSINE"
)

# 插入数据
data = [
    {"id": 1, "vector": [0.1, ...], "text": "Python 编程", "topic": "dev"},
    {"id": 2, "vector": [0.2, ...], "text": "机器学习", "topic": "ml"},
]
client.insert(collection_name="my_docs", data=data)

# 搜索
results = client.search(
    collection_name="my_docs",
    data=[[0.1, ...]],
    limit=5,
    filter='topic == "dev"',
    output_fields=["text", "topic"]
)
```

---

## 6. 混合搜索（Hybrid Search）

### 密集检索（Dense Retrieval）
- 使用 Embedding 模型将文本转为稠密向量
- 优点：理解语义（"汽车"能匹配"轿车"）
- 缺点：对专业术语、罕见词、精确数字匹配可能不够好

### 稀疏检索（Sparse Retrieval）
- BM25、TF-IDF：基于关键词匹配和词频统计
- BM25 公式核心：`score(q,d) = sum(IDF(q_i) * tf(q_i,d) * (k1+1) / (tf + k1*(1-b+b*|d|/avgdl)))`
- 优点：对精确关键词匹配好，可解释性强，零样本能力好
- 缺点：不理解语义（"汽车"无法匹配"轿车"），依赖词频统计

### 混合搜索策略
同时执行密集检索和稀疏检索，融合结果：

#### RRF（Reciprocal Rank Fusion）
```
score(d) = sum(1 / (k + rank_i(d)))

k: 常数，通常取 60
rank_i(d): 文档 d 在第 i 个检索器中的排名

示例：
  文档 A: dense 排名 1, BM25 排名 5
  RRF(A) = 1/(60+1) + 1/(60+5) = 0.0164 + 0.0154 = 0.0318

  文档 B: dense 排名 3, BM25 排名 2
  RRF(B) = 1/(60+3) + 1/(60+2) = 0.0159 + 0.0161 = 0.0320

  文档 B > 文档 A（B 在两个排名中都比较靠前）
```

#### 加权线性融合
```
score = alpha * normalize(dense_score) + (1-alpha) * normalize(sparse_score)
alpha: 权重系数，通常 0.5-0.7
需要先对分数做归一化（Min-Max 或 Z-Score）
```

### 混合搜索的实际效果
- 大多数生产级 RAG 系统都使用混合搜索
- 通常比单独使用 dense 或 sparse 效果好 5-15%
- 对"语义+精确"混合查询特别有效

---

## 面试高频问题

### Q1: 什么是 Embedding？从 Word2Vec 到现代模型有什么进步？
**答**：Embedding 将文本映射为稠密向量表示。Word2Vec 学习静态词向量（一词一向量）；ELMo 引入上下文化表示；BERT 用 Transformer 深层理解；现代模型（BGE/E5）通过大规模对比学习和指令微调，在检索、分类等任务上效果大幅提升。关键进步是从静态到上下文化、从词级到句子/段落级。

### Q2: 余弦相似度和欧氏距离的区别？什么场景用哪个？
**答**：余弦相似度只关注向量方向，不受长度影响，范围 [-1,1]；欧氏距离衡量空间中两点的直线距离，受长度影响。文本语义搜索用余弦相似度；已归一化向量用内积（等价于余弦，更快）。当向量归一化后，三者排序结果完全一致。

### Q3: HNSW 索引的原理？
**答**：HNSW 构建多层近邻图，底层包含所有节点的完整近邻图，每上一层节点数指数减少。查询从最顶层开始贪心搜索，逐层下降到底层。类似跳表，实现了 O(log n) 的查询复杂度。关键参数 M 控制邻居数，ef 控制搜索范围。

### Q4: IVF 和 PQ 的原理？为什么要组合使用？
**答**：IVF 将向量空间聚类为多个簇，查询时只搜索最近的几个簇（粗筛）。PQ 将高维向量切分为多个子向量分别量化，大幅压缩存储。IVF-PQ 组合：IVF 快速定位候选簇，PQ 在候选中用压缩向量计算距离，兼顾速度和存储效率，适合十亿级场景。

### Q5: 密集检索和稀疏检索的优缺点？什么是混合搜索？
**答**：密集检索（向量）理解语义但对精确匹配不够好；稀疏检索（BM25）精确匹配好但不理解语义。混合搜索同时使用两者，通过 RRF 或加权融合结果，在语义理解和精确匹配之间取得平衡。生产级 RAG 系统通常使用混合搜索。

### Q6: 如何选择 Embedding 模型？
**答**：考虑因素：语言支持（中文选 BGE/GTE）、维度大小（越高越好但越慢）、最大输入长度、MTEB 排名。推荐：中文 RAG 用 BGE-M3，英文用 text-embedding-3-large 或 E5-mistral。如果需要混合搜索，BGE-M3 一个模型同时支持 dense+sparse+ColBERT。

### Q7: 向量数据库选型建议？
**答**：原型开发用 Chroma（最简单）；中小规模生产用 Qdrant（Rust 写，性能好，过滤强）或 pgvector（已有 PG 的项目）；大规模生产用 Milvus（分布式，十亿级）或 Pinecone（全托管）；纯离线批量处理用 FAISS（性能最高）。

### Q8: 向量检索的精度和速度如何权衡？
**答**：增大 HNSW 的 ef_search、IVF 的 nprobe 可提高精度但降低速度。PQ 的 m 和 K 越大精度越高但空间越大。实践中用 recall@k 评估精度（真实 top-k 中有多少被召回），目标通常是 recall@10 > 95%。可以先用 HNSW 粗筛，再用精确距离重排。

### Q9: Matryoshka Embedding 是什么？有什么用？
**答**：Matryoshka（套娃）嵌入在训练时同时优化多个维度的子向量，使得向量可以截断到任意维度仍保持较好的语义质量。例如 3072 维可以截断为 1024 或 256。用途是灵活权衡存储空间和效果：粗筛用短向量（快），精排用完整向量（准）。

### Q10: 如何评估 Embedding 模型的质量？
**答**：参考 MTEB/C-MTEB 排行榜，涵盖检索（Retrieval）、分类（Classification）、聚类（Clustering）、重排（Reranking）、语义文本相似度（STS）等任务。也可以在自己的数据集上评估：构建测试集（query-relevant_doc 对），计算 NDCG@10、MRR、Recall@k 等指标。
