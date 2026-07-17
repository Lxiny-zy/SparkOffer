# Agent 记忆体系深度

## 1. 为什么 Agent 需要记忆

### LLM 的"无记忆"本质
LLM 是**无状态**的：每次调用只看到当前传入的 messages。所谓"对话记忆"其实是每次把历史消息全部塞进上下文。

### 单纯拼接历史的局限
- **上下文窗口限制**：128K/1M 仍有上限
- **成本线性增长**：Token 数正比于历史长度
- **信息稀释**：关键信息淹没在无关闲聊中
- **跨会话不记得**：新 session 清零
- **多 Agent 难共享**：每个 Agent 独立上下文

### 记忆系统的目标
- 让 Agent 跨轮次、跨会话、跨 Agent **持续累积知识**
- 关键信息能**精准召回**
- 容量理论上**无限**
- 成本**亚线性增长**

---

## 2. 记忆的分类

### 按时间维度

| 类型 | 时间尺度 | 典型实现 |
|------|----------|----------|
| **Sensory（感知）** | 毫秒-秒 | 当前输入 |
| **Short-term（短期）** | 当前对话 | 消息历史 |
| **Long-term（长期）** | 永久 | 向量库、图谱、数据库 |

### 按内容维度（认知科学借鉴）

| 类型 | 含义 | 例子 |
|------|------|------|
| **Semantic（语义）** | 事实、知识 | "用户是程序员" |
| **Episodic（情景）** | 具体事件 | "2024-03-15 用户问过 Python GIL" |
| **Procedural（程序）** | 技能、流程 | "查天气需要先调 API" |

### 按作用域

- **User Memory**：某用户的长期画像
- **Session Memory**：当前会话内
- **Agent Memory**：Agent 自身的知识/技能
- **Shared Memory**：多 Agent 共享

---

## 3. 短期记忆（Working Memory）

### 实现方式

**1. Buffer Memory（朴素）**
```python
messages = [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    ...
]
```
直接存储全部历史。**缺点**：轮次多时 token 爆炸。

**2. Window Memory（滑动窗口）**
```python
class WindowMemory:
    def __init__(self, k=10):
        self.k = k
        self.messages = []

    def add(self, msg):
        self.messages.append(msg)
        self.messages = self.messages[-self.k*2:]  # 保留最近 k 轮
```
只保留最近 K 轮。**缺点**：丢失早期关键信息。

**3. Summary Memory（摘要）**
```python
class SummaryMemory:
    def __init__(self, llm, max_tokens=500):
        self.summary = ""
        self.recent = []

    def add(self, msg):
        self.recent.append(msg)
        if len(self.recent) > 10:
            # 用 LLM 把 recent 压缩成摘要
            new_summary = self.llm.summarize(self.summary, self.recent)
            self.summary = new_summary
            self.recent = []
```
把早期对话压缩成摘要，保留最近几轮原文。**缺点**：LLM 调用开销，细节丢失。

**4. Summary Buffer（混合）**
- 最近 K 轮保留原文
- 超出部分持续滚动摘要
- **LangChain `ConversationSummaryBufferMemory`** 是典型实现

**5. Entity Memory（实体记忆）**
维护一个实体字典：
```python
entities = {
    "Alice": "用户，25岁，程序员，喜欢 Python",
    "Project X": "用户正在做的 AI 项目..."
}
```
只在提到实体时注入对应信息。

---

## 4. 长期记忆（Long-term Memory）

### 向量库存储（最常用）

```python
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings

vectorstore = Chroma(
    collection_name="user_memory",
    embedding_function=OpenAIEmbeddings()
)

# 存入
vectorstore.add_texts(
    texts=["用户叫 Alice，是 Python 工程师"],
    metadatas=[{"user_id": "u123", "timestamp": "2024-03-15"}]
)

# 检索（按当前 query 语义相似度）
docs = vectorstore.similarity_search(
    "我叫什么？",
    k=3,
    filter={"user_id": "u123"}
)
```

### 关键设计

**1. 写什么进去？**
- 不是所有对话都值得存：闲聊无需保留
- **重要事实**：用户自述信息、偏好、目标
- **任务成果**：已完成任务的关键结论
- **反馈**：用户纠正"不对，应该…"

**写入策略**：
- **显式触发**：用户说"记住 XXX"
- **隐式抽取**：LLM 周期性扫描对话，抽取事实
- **结构化提取**：按 Schema 抽取（姓名、职业、偏好）

**2. 怎么写？**
```python
# 原始对话
messages = [
    {"user": "我是北京的数据科学家，喜欢用 R 和 Python"},
    {"assistant": "好的"}
]

# 通过 LLM 抽取为可检索的事实
facts = llm.extract("""
从对话中抽取关于用户的结构化事实：
[对话内容]
输出 JSON 列表，每条含 fact + category。
""")
# → [{"fact": "用户在北京", "category": "location"},
#    {"fact": "用户是数据科学家", "category": "job"},
#    {"fact": "用户用 R 和 Python", "category": "skill"}]

for f in facts:
    vectorstore.add_texts([f["fact"]], metadatas=[{"category": f["category"]}])
```

**3. 怎么检索？**
- 按语义相似度（默认）
- 按时间衰减：最近的记忆权重高
- 按重要性打分：LLM 给每条记忆打分，检索时综合
- 按类别过滤：先定位 category，再相似度

### 知识图谱记忆

适合处理**实体关系**：

```python
# 用 Neo4j / NetworkX 构建
graph.add_node("Alice", type="person", age=25)
graph.add_node("Python", type="language")
graph.add_edge("Alice", "Python", relation="skilled_in")

# 查询
query = "Alice 会什么？"
# → Cypher: MATCH (a:person{name:'Alice'})-[r:skilled_in]->(x) RETURN x
```

**代表框架**：
- **Mem0**：基于向量 + 图的混合记忆系统
- **Zep**：企业级 Agent 记忆平台
- **Graphiti**（Zep 开源）：时序知识图谱

---

## 5. MemGPT / Letta：操作系统级记忆

### 核心思想
**借鉴虚拟内存**：LLM 上下文窗口是"RAM"，外部存储是"磁盘"，通过分页机制让 Agent 自主管理记忆。

### 架构

```
┌────────────────────────────────┐
│         Main Context            │  ← LLM 看到的
│  ┌──────────────────────────┐  │
│  │ System Prompt            │  │
│  │ Core Memory (persona,    │  │
│  │              human)      │  │
│  │ Messages (FIFO queue)    │  │
│  └──────────────────────────┘  │
└────────────────────────────────┘
         ▲        │
         │ recall │ save
         ▼        ▼
┌────────────────────────────────┐
│      External Context           │
│  ┌────────────┐ ┌───────────┐  │
│  │ Recall     │ │ Archival  │  │
│  │ Storage    │ │ Storage   │  │
│  │(消息历史)  │ │(向量库)   │  │
│  └────────────┘ └───────────┘  │
└────────────────────────────────┘
```

### 关键机制
- **Core Memory**：always-in-context 的核心信息（用户画像、Agent 角色），可被 LLM 主动编辑
- **Recall Memory**：历史消息的可搜索存储，按需 load 回 context
- **Archival Memory**：向量库形式的无限存储
- **Self-editing**：Agent 主动决策什么信息该存、取、改
- **Context Overflow**：上下文将满时自动把旧消息存入 Recall

### 代码示例（Letta，MemGPT 后继）

```python
from letta import create_client, LLMConfig

client = create_client()

agent = client.create_agent(
    name="assistant",
    persona="你是一个博学助手",
    human="用户 Alice，Python 工程师",
    llm_config=LLMConfig.default_config("gpt-4o")
)

# Agent 自主调用 core_memory_replace / archival_memory_insert
response = client.user_message(agent.id, "我最近在学 Rust")
# Agent 会自动把这条信息存入 archival_memory

# 下次对话 Agent 能召回
response = client.user_message(agent.id, "我之前说过在学什么？")
# → Agent 自动检索 archival，回答 Rust
```

---

## 6. 层次化记忆（Hierarchical Memory）

借鉴人类记忆结构，多层存储：

```
Level 1: 工作内存（当前对话）   → 原始 messages
Level 2: 短期记忆（最近 N 天）   → SQL / 关系库
Level 3: 长期记忆（久远）        → 向量库摘要
Level 4: 语义记忆（一般知识）    → 知识图谱
Level 5: 程序记忆（技能）        → Prompt 模板库
```

### 召回策略
- **先浅后深**：从工作内存开始找，找不到再下沉
- **综合打分**：相似度 × 重要性 × 时间衰减 × 层级权重

---

## 7. 反思与自我编辑（Reflection）

### Generative Agents 的反思机制

斯坦福论文的核心创新：

```
每 N 条观察后触发 Reflection:

1. LLM 扫描最近 100 条记忆
2. 生成 3 个"重要问题"
3. 针对每个问题检索相关记忆
4. 生成高层洞察（"Alice 最近情绪低落可能因为工作压力"）
5. 洞察作为新记忆存入
```

伪代码：
```python
def reflect(agent):
    recent = memory.get_recent(100)
    questions = llm.ask(f"基于这些事件，你应该思考的 3 个问题？{recent}")
    insights = []
    for q in questions:
        related = memory.search(q, k=15)
        insight = llm.ask(f"关于{q}，从这些记忆能得出什么洞察？{related}")
        insights.append(insight)
    for i in insights:
        memory.add(i, type="reflection", importance=9)
```

### 记忆的重要性评分

每条记忆打分：
```python
importance = llm.score("""
在 1-10 分间评估这条记忆的重要性：
1 = 极日常（刷牙）
10 = 关键时刻（分手、换工作）
记忆：{memory}
""")
```

召回时 `score = similarity × 0.5 + importance × 0.3 + recency × 0.2`。

---

## 8. 跨会话 / 跨 Agent 记忆

### 用户画像（User Profile）

```python
{
  "user_id": "u123",
  "name": "Alice",
  "occupation": "Python 工程师",
  "preferences": {
    "communication_style": "简洁",
    "languages": ["zh-CN", "en"]
  },
  "history_summary": "过去 6 个月主要讨论 AI、LangChain、K8s"
}
```

每次对话开始注入 user profile 作为 System Prompt 的一部分。

### 共享记忆池

多 Agent 同一 VectorStore，namespace 隔离：
```
memory/
  ├─ user_profiles/      # 跨 Agent 共享
  ├─ agent_a_private/    # Agent A 专有
  └─ project_context/    # 特定项目共享
```

### 记忆同步

- 用户在 Web 端对话 → 存入云端 VectorStore
- App 端继续对话 → 召回同一记忆
- **Zep、Mem0** 等云服务提供托管方案

---

## 9. 主流框架的记忆实现

### LangChain Memory（已逐步弃用）

```python
from langchain.memory import ConversationBufferMemory, ConversationSummaryMemory

memory = ConversationBufferMemory(return_messages=True)
# 或
memory = ConversationSummaryMemory(llm=llm)
```

LangChain 0.3+ 推荐改用 LangGraph 的 State + Checkpointer。

### LangGraph（推荐）

```python
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver.from_conn_string("postgresql://...")
graph = workflow.compile(checkpointer=checkpointer)

# 每次调用传入 thread_id 恢复上下文
config = {"configurable": {"thread_id": "user-123"}}
graph.invoke({"messages": [...]}, config=config)
```

### LlamaIndex Memory

```python
from llama_index.core.memory import ChatMemoryBuffer, VectorMemory

buffer = ChatMemoryBuffer.from_defaults(token_limit=3000)
vector = VectorMemory.from_defaults(vector_store=chroma)

# 组合
from llama_index.core.memory import SimpleComposableMemory
memory = SimpleComposableMemory.from_defaults(
    primary_memory=buffer,
    secondary_memory_sources=[vector]
)
```

### Mem0

```python
from mem0 import Memory

m = Memory()
m.add("我喜欢素食", user_id="alice")
m.add("我对花生过敏", user_id="alice")

# 检索
results = m.search("饮食", user_id="alice")
# → 返回素食和过敏信息
```

### OpenAI Assistants API

内置 Thread 机制自动保留历史，文件可上传到 Thread 持久化。

---

## 10. 实战：构建生产级记忆系统

### 需求
- 支持 10 万用户
- 每用户可能有几千条历史
- 召回延迟 < 500ms
- 跨设备同步

### 架构

```
用户 Query
    ↓
[Short-term] Redis（Session 消息 + 滑动窗口）
    ↓（缺失时下沉）
[Long-term] 
  ├─ VectorStore（Qdrant/Milvus/pgvector）  按语义召回
  ├─ PostgreSQL                               结构化事实
  └─ Neo4j（可选）                            实体关系
    ↓
组装 Prompt → LLM
```

### 关键点
- **异步写入**：对话结束后异步抽取事实，不阻塞响应
- **去重合并**：新事实与老事实冲突时用 LLM 合并
- **遗忘机制**：定期清理低重要性、过期信息
- **隐私**：用户可导出/删除所有记忆（GDPR）
- **A/B 测试**：不同记忆策略对比

### 记忆更新 Pipeline

```python
async def update_memory(user_id, session_messages):
    # 1. 抽取事实
    facts = await llm.extract_facts(session_messages)

    # 2. 查重：与已有记忆对比
    for fact in facts:
        existing = vectorstore.search(fact.content, k=3, filter={"user_id": user_id})
        if any(sim > 0.9 for sim in existing):
            continue  # 已存在，跳过
        if any(0.7 < sim < 0.9 for sim in existing):
            # 可能冲突，LLM 合并
            merged = await llm.merge(fact, existing[0])
            vectorstore.update(existing[0].id, merged)
        else:
            # 新事实，存入
            vectorstore.add(fact.content, metadata={
                "user_id": user_id,
                "timestamp": now(),
                "importance": fact.importance
            })
```

---

## 11. 记忆的评估

### 指标
- **Recall@K**：关键信息是否在 Top-K 检索到
- **Precision**：召回的记忆是否相关
- **Answer Accuracy**：基于记忆的回答是否准确
- **Latency**：召回延迟
- **Consistency**：长期使用后记忆是否矛盾

### 基准
- **LoCoMo**：长对话记忆评测
- **LongMemEval**：长期记忆评测
- **MSC**：Multi-Session Chat 数据集

---

## 面试高频问题

**Q1：短期记忆有哪些实现方式，优缺点？**

- **Buffer**：全保留，准确但 token 爆炸
- **Window（滑窗）**：保留最近 K 轮，丢失早期
- **Summary（摘要）**：压缩历史，省 token 但损失细节
- **SummaryBuffer（混合）**：最近原文 + 早期摘要，折中最优
- **Entity**：按实体索引，精准但覆盖有限

实务用 SummaryBuffer 或改用 LangGraph State + 长上下文模型。

**Q2：如何设计长期记忆？**

核心决策三问：
1. **写什么**：重要事实、用户偏好、任务结论（非闲聊）
2. **怎么写**：LLM 抽取为短事实句，加 metadata（user_id/时间/重要性）
3. **怎么读**：向量检索 + 时间衰减 + 重要性加权

存储层：VectorStore 为主，配合 SQL/图谱。框架用 Mem0 / Letta / 自研。

**Q3：MemGPT 的核心创新是什么？**

**把 Agent 上下文管理建模为操作系统的虚拟内存**：
- LLM 上下文窗口 = RAM（有限）
- 外部向量库 = 磁盘（无限）
- 提供 API 让 LLM **自主调用** `recall_memory_insert`、`archival_memory_search` 等工具
- 上下文将溢出时自动分页换出

意义：让 Agent 理论上可以"永远运行"而不受窗口限制。

**Q4：记忆检索只用向量相似度够吗？**

不够。纯相似度问题：
- 早年的记忆和最新的一视同仁（缺时序感）
- 高频琐事挤占关键信息（缺重要性）
- 只能做语义匹配（缺精确过滤）

完整方案：
```
score = α · similarity + β · importance + γ · recency_decay
```
再加**硬过滤**（user_id、时间范围、类别）。Generative Agents 论文是经典参考。

**Q5：如何避免记忆越来越多导致检索变差？**

- **定期归档**：低重要性、过期的记忆移到冷存储
- **合并相似**：相似度 > 阈值的记忆 LLM 合并
- **遗忘**：引入遗忘曲线，长期未访问的衰减删除
- **分层**：Hot（常用）/ Warm（偶尔）/ Cold（归档）
- **Reflection**：定期生成高层摘要，原始细节降级

**Q6：多 Agent 之间怎么共享记忆？**

三种模式：
1. **共享 VectorStore**：同一库，namespace 隔离
2. **共享 State**：LangGraph 模式，所有 Agent 读写同一 State
3. **消息广播**：GroupChat 模式，消息历史对所有 Agent 可见

关键：**隐私边界**——哪些是用户级共享、哪些是 Agent 私有。

**Q7：记忆的写入时机如何设计？**

- **即时写入**：每条消息都分析抽取，准确但贵
- **定时写入**：会话结束/每 N 轮后批处理
- **触发写入**：用户说"记住"、Agent 判断重要时
- **后台异步**：不阻塞用户响应

生产常用：**后台异步 + 定时批处理**，平衡成本和实时性。

**Q8：Agent 记忆的隐私如何处理？**

- **用户 ID 隔离**：vectorStore 强制按 user_id 过滤
- **加密存储**：静态加密（KMS），不同用户不同 key
- **可删除**：用户可请求删除所有记忆（GDPR、个人信息保护法）
- **脱敏**：写入前去掉身份证、电话等敏感信息
- **审计**：记录谁访问过哪些记忆

**Q9：短长期记忆怎么配合？**

典型 Agent Query 处理：
```
1. 短期：直接取当前 session 最近 K 轮（免费、0 延迟）
2. 长期：向量检索相关事实（注入到 System Prompt）
3. 组装：System + LongTerm Facts + ShortTerm Messages + 当前 Query
4. LLM 生成
5. 异步更新记忆
```

**Q10：未来记忆系统的发展方向？**

- **超长上下文**：1M-10M token 上下文让"记忆"边界模糊
- **持续学习**：Agent 不只存记忆，还把记忆"学"进模型权重（PEFT）
- **图结构记忆**：超越向量，用知识图谱表达复杂关系（Graphiti）
- **多模态记忆**：图像、音频、视频的统一记忆
- **社会记忆**：群体 Agent 共享的集体记忆
- **可解释**：记忆为什么被召回 → 用户可审计
