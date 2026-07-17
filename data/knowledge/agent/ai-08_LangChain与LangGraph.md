# LangChain 与 LangGraph

## 1. LangChain 概述

### 什么是 LangChain
LangChain 是一个用于构建 LLM 应用的开发框架，提供了标准化的组件和编排工具，让开发者可以快速构建复杂的 AI 应用。

### 核心价值
- **组件化**：标准化的 LLM、Prompt、Memory、Tools 等组件
- **可组合**：通过 LCEL 和 Agent 将组件灵活组合
- **生态丰富**：大量集成（各种 LLM、向量数据库、工具）

### LangChain 生态系统

```
LangChain 生态:
├── langchain-core       —— 核心抽象和接口（Runnable、Message等）
├── langchain            —— 链、Agent 等高级组件
├── langchain-community  —— 第三方集成（各种 Loader、VectorStore等）
├── langchain-openai     —— OpenAI 集成
├── langchain-anthropic  —— Anthropic 集成
├── langgraph            —— 状态图 Agent 编排（核心重点）
├── langserve            —— 部署为 REST API
└── langsmith            —— 调试、监控、评估平台
```

---

## 2. LangChain 核心组件

### Chat Model（对话模型）

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4",
    temperature=0.7,
    api_key="your-key",
    base_url="https://your-api-base/v1"
)

# 基本调用
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

messages = [
    SystemMessage(content="你是一个有帮助的助手"),
    HumanMessage(content="什么是 RAG？")
]
response = llm.invoke(messages)
print(response.content)

# 流式输出
for chunk in llm.stream(messages):
    print(chunk.content, end="")

# 异步调用
response = await llm.ainvoke(messages)

# 批量调用
responses = llm.batch([messages_1, messages_2, messages_3])
```

### Prompt Template（提示模板）

```python
from langchain_core.prompts import ChatPromptTemplate

# 基础模板
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个{role}，擅长{skill}"),
    ("human", "{question}")
])

# 使用模板
formatted = prompt.invoke({
    "role": "资深后端工程师",
    "skill": "系统设计",
    "question": "如何设计一个秒杀系统？"
})

# MessagesPlaceholder：动态插入消息列表
from langchain_core.prompts import MessagesPlaceholder

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个有帮助的助手"),
    MessagesPlaceholder(variable_name="history"),  # 对话历史
    ("human", "{question}")
])

# Few-Shot 模板
from langchain_core.prompts import FewShotChatMessagePromptTemplate

examples = [
    {"input": "happy", "output": "快乐"},
    {"input": "sad", "output": "悲伤"}
]

few_shot_prompt = FewShotChatMessagePromptTemplate(
    example_prompt=ChatPromptTemplate.from_messages([
        ("human", "{input}"),
        ("ai", "{output}")
    ]),
    examples=examples
)
```

### Output Parser（输出解析器）

```python
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from pydantic import BaseModel, Field

# 字符串解析（最简单）
parser = StrOutputParser()

# JSON 解析（结构化输出）
class CodeReview(BaseModel):
    issues: list[str] = Field(description="代码问题列表")
    score: int = Field(description="代码质量评分 1-10")
    suggestion: str = Field(description="改进建议")

json_parser = JsonOutputParser(pydantic_object=CodeReview)

# 在 prompt 中加入格式说明
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是代码审查专家。{format_instructions}"),
    ("human", "请审查以下代码：\n{code}")
]).partial(format_instructions=json_parser.get_format_instructions())

# Structured Output（结构化输出，推荐）
structured_llm = llm.with_structured_output(CodeReview)
result = structured_llm.invoke("审查这段代码...")
# result 是 CodeReview 类型的对象
```

---

## 3. LCEL（LangChain Expression Language）

### 基本概念
LCEL 是 LangChain 的链式组合语法，用 `|` 管道符连接组件，每个组件都是 `Runnable`。

### 简单链
```python
from langchain_core.output_parsers import StrOutputParser

# prompt → LLM → 解析输出
chain = prompt | llm | StrOutputParser()
result = chain.invoke({"question": "什么是微服务？"})

# 流式输出
for chunk in chain.stream({"question": "什么是微服务？"}):
    print(chunk, end="")

# 异步
result = await chain.ainvoke({"question": "什么是微服务？"})

# 批量
results = chain.batch([{"question": "Q1"}, {"question": "Q2"}])
```

### 并行执行
```python
from langchain_core.runnables import RunnableParallel, RunnablePassthrough

# 同时执行多个链
chain = RunnableParallel(
    summary=prompt_summary | llm | StrOutputParser(),
    keywords=prompt_keywords | llm | StrOutputParser(),
    original=RunnablePassthrough()  # 透传原始输入
)
result = chain.invoke({"text": "..."})
# result = {"summary": "...", "keywords": "...", "original": {"text": "..."}}
```

### 条件路由
```python
from langchain_core.runnables import RunnableBranch, RunnableLambda

# 根据条件选择不同的链
branch = RunnableBranch(
    (lambda x: "代码" in x["question"], code_chain),
    (lambda x: "设计" in x["question"], design_chain),
    default_chain  # 默认分支
)

# 或使用 RunnableLambda 自定义逻辑
def route(input):
    if "代码" in input["question"]:
        return code_chain
    return default_chain

chain = RunnableLambda(route)
```

### 自定义 Runnable
```python
from langchain_core.runnables import RunnableLambda

# 将普通函数包装为 Runnable
@RunnableLambda
def format_docs(docs):
    return "\n\n---\n\n".join(doc.page_content for doc in docs)

# 使用
chain = retriever | format_docs | prompt | llm | StrOutputParser()
```

### LCEL 的优势
- **流式输出**：自动支持 token 级流式
- **异步**：自动支持异步调用
- **并行**：RunnableParallel 自动并行执行
- **重试和回退**：.with_retry()、.with_fallbacks()
- **可观测性**：与 LangSmith 无缝集成，自动追踪

---

## 4. 文档处理管线

### Document Loader（文档加载器）
```python
# PDF
from langchain_community.document_loaders import PyMuPDFLoader
loader = PyMuPDFLoader("document.pdf")
docs = loader.load()

# 网页
from langchain_community.document_loaders import WebBaseLoader
loader = WebBaseLoader("https://example.com")

# Markdown
from langchain_community.document_loaders import UnstructuredMarkdownLoader
loader = UnstructuredMarkdownLoader("README.md")

# 目录批量加载
from langchain_community.document_loaders import DirectoryLoader
loader = DirectoryLoader("./docs", glob="**/*.md")
docs = loader.load()

# CSV
from langchain_community.document_loaders import CSVLoader
loader = CSVLoader("data.csv")
```

### Text Splitter（文本分割器）
```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
    separators=["\n\n", "\n", "。", "！", "？", " ", ""],
    length_function=len
)
chunks = splitter.split_documents(docs)

# 语义分割
from langchain_experimental.text_splitter import SemanticChunker
from langchain_openai import OpenAIEmbeddings

splitter = SemanticChunker(
    OpenAIEmbeddings(),
    breakpoint_threshold_type="percentile"
)
```

### Embedding
```python
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    dimensions=512  # Matryoshka 截断
)

# 本地 Embedding
from langchain_huggingface import HuggingFaceEmbeddings
embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-m3")
```

### VectorStore（向量存储）
```python
from langchain_community.vectorstores import Chroma, FAISS

# Chroma
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_db"
)

# FAISS
vectorstore = FAISS.from_documents(chunks, embeddings)
vectorstore.save_local("./faiss_index")
# 加载
vectorstore = FAISS.load_local("./faiss_index", embeddings)
```

### Retriever（检索器）
```python
# 基础向量检索器
retriever = vectorstore.as_retriever(
    search_type="similarity",    # 或 "mmr"
    search_kwargs={"k": 5}
)

# MMR 检索（最大边际相关性，减少重复）
retriever = vectorstore.as_retriever(
    search_type="mmr",
    search_kwargs={"k": 5, "fetch_k": 20, "lambda_mult": 0.7}
)

# 多查询检索器（自动扩展查询）
from langchain.retrievers import MultiQueryRetriever
multi_retriever = MultiQueryRetriever.from_llm(
    retriever=retriever, llm=llm
)

# 上下文压缩检索器
from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor
compressor = LLMChainExtractor.from_llm(llm)
compression_retriever = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=retriever
)

# 混合检索（向量 + BM25）
from langchain.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever

bm25 = BM25Retriever.from_documents(docs, k=10)
ensemble = EnsembleRetriever(
    retrievers=[retriever, bm25],
    weights=[0.6, 0.4]
)
```

### 完整文档处理管线
```
Loader → Splitter → Embedding → VectorStore → Retriever
(加载)   (分块)     (向量化)    (存储)        (检索)
```

---

## 5. 构建 RAG 应用

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

# RAG Prompt
rag_prompt = ChatPromptTemplate.from_messages([
    ("system", """你是一个专业的技术问答助手。请基于以下参考资料回答用户问题。
如果资料中没有相关信息，请明确说明。

参考资料：
{context}"""),
    ("human", "{question}")
])

# 格式化检索结果
def format_docs(docs):
    return "\n\n---\n\n".join(doc.page_content for doc in docs)

# 构建 RAG 链
rag_chain = (
    {
        "context": retriever | format_docs,
        "question": RunnablePassthrough()
    }
    | rag_prompt
    | llm
    | StrOutputParser()
)

# 使用
answer = rag_chain.invoke("HashMap 的扩容机制是什么？")

# 流式输出
for chunk in rag_chain.stream("解释 B+ 树索引的原理"):
    print(chunk, end="")

# 带来源引用的 RAG
def rag_with_sources(question):
    docs = retriever.invoke(question)
    context = format_docs(docs)
    answer = (rag_prompt | llm | StrOutputParser()).invoke({
        "context": context,
        "question": question
    })
    sources = [doc.metadata.get("source", "unknown") for doc in docs]
    return {"answer": answer, "sources": sources}
```

---

## 6. LangChain Tools 与 Agent

```python
from langchain_core.tools import tool
from langchain.agents import create_tool_calling_agent, AgentExecutor

# 定义工具
@tool
def search_docs(query: str) -> str:
    """搜索技术文档知识库，输入关键词返回相关内容"""
    results = retriever.invoke(query)
    return "\n".join(r.page_content for r in results)

@tool
def calculate(expression: str) -> str:
    """计算数学表达式，输入数学表达式如 '2+3*4'"""
    try:
        return str(eval(expression))
    except Exception as e:
        return f"计算错误: {e}"

# 创建 Agent Prompt
agent_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个技术助手，可以搜索文档和进行计算。"),
    MessagesPlaceholder(variable_name="chat_history", optional=True),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad")
])

# 创建 Agent
agent = create_tool_calling_agent(llm, [search_docs, calculate], agent_prompt)
executor = AgentExecutor(
    agent=agent,
    tools=[search_docs, calculate],
    verbose=True,       # 打印执行过程
    max_iterations=10   # 最大执行步数
)

result = executor.invoke({
    "input": "查一下 Redis 缓存淘汰策略，然后算一下如果缓存命中率 90%，10000 次请求中有多少次命中"
})
```

---

## 7. LangGraph 状态图

### 什么是 LangGraph
LangGraph 是 LangChain 团队推出的 Agent 编排框架，基于**有向图**来构建复杂的 LLM 应用和多步 Agent 流程。

### LangGraph vs LangChain Agent

| 维度 | LangChain Agent | LangGraph |
|------|----------------|-----------|
| 流程控制 | LLM 自主决策 | 开发者定义图结构 |
| 可预测性 | 低 | 高 |
| 状态管理 | 隐式 | 显式 State |
| 循环支持 | 有限 | 原生支持 |
| 持久化 | 有限 | Checkpoint |
| Human-in-Loop | 有限 | 原生支持 |
| 适合场景 | 简单工具调用 | 复杂多步流程 |

### 核心概念

#### State（状态）
Agent 在整个流程中传递和共享的数据：

```python
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]  # 对话历史（自动追加）
    documents: list[str]                      # 检索到的文档
    question: str                             # 用户问题
    generation: str                           # 生成的回答
    retry_count: int                          # 重试次数
```

**Annotated[list, add_messages]**：
- `add_messages` 是 reducer 函数
- 不会覆盖，而是追加到列表
- 确保消息历史不会丢失

#### Node（节点）
图中的处理步骤，每个节点是一个函数，接收 State 并返回部分更新：

```python
def retrieve_node(state: AgentState) -> dict:
    """检索相关文档"""
    question = state["question"]
    docs = retriever.invoke(question)
    return {"documents": [doc.page_content for doc in docs]}

def generate_node(state: AgentState) -> dict:
    """基于文档生成回答"""
    docs_text = "\n\n".join(state["documents"])
    messages = [
        SystemMessage(content=f"基于以下资料回答：\n{docs_text}"),
        HumanMessage(content=state["question"])
    ]
    response = llm.invoke(messages)
    return {"generation": response.content}

def grade_node(state: AgentState) -> dict:
    """评估回答质量"""
    grade_prompt = f"评估回答是否准确回答了问题。问题：{state['question']}\n回答：{state['generation']}"
    grade = llm.invoke(grade_prompt)
    return {"grade": grade.content, "retry_count": state.get("retry_count", 0) + 1}
```

#### Edge（边）
连接节点的路径：

```python
# 固定边：A → B
workflow.add_edge("retrieve", "generate")

# 条件边：根据状态决定下一步
def should_retry(state: AgentState) -> str:
    if "不够好" in state.get("grade", ""):
        if state.get("retry_count", 0) < 3:
            return "retry"
        return "fallback"
    return "output"

workflow.add_conditional_edges(
    "grade",
    should_retry,
    {
        "retry": "rewrite_query",
        "output": END,
        "fallback": END
    }
)
```

### 构建 LangGraph 应用

#### 示例：RAG + 质量评估 + 重试

```python
from langgraph.graph import StateGraph, END, START

# 创建图
workflow = StateGraph(AgentState)

# 添加节点
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("generate", generate_node)
workflow.add_node("grade", grade_node)
workflow.add_node("rewrite_query", rewrite_query_node)

# 设置入口和边
workflow.add_edge(START, "retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", "grade")

# 条件边
workflow.add_conditional_edges(
    "grade",
    should_retry,
    {
        "retry": "rewrite_query",
        "output": END,
        "fallback": END
    }
)
workflow.add_edge("rewrite_query", "retrieve")  # 改写后重新检索

# 编译
app = workflow.compile()

# 运行
result = app.invoke({
    "question": "解释 MySQL 的 MVCC 机制",
    "messages": [],
    "documents": [],
    "generation": "",
    "retry_count": 0
})

# 可视化
print(app.get_graph().draw_mermaid())
```

---

## 8. LangGraph 高级特性

### Checkpoint（检查点）
```python
from langgraph.checkpoint.memory import MemorySaver

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)

# 带 thread_id 的调用（支持多轮对话）
config = {"configurable": {"thread_id": "user-123"}}
result1 = app.invoke({"question": "什么是索引？"}, config)

# 后续对话自动继承状态
result2 = app.invoke({"question": "联合索引呢？"}, config)

# 查看历史状态
states = list(app.get_state_history(config))

# 回退到某个检查点
app.update_state(config, {"generation": ""}, as_node="generate")
```

**持久化存储**：
```python
# SQLite 持久化
from langgraph.checkpoint.sqlite import SqliteSaver
memory = SqliteSaver.from_conn_string("checkpoints.db")

# PostgreSQL 持久化（生产环境）
from langgraph.checkpoint.postgres import PostgresSaver
memory = PostgresSaver.from_conn_string("postgresql://...")
```

### Human-in-the-Loop（人工介入）
```python
# 在关键节点设置中断
app = workflow.compile(
    checkpointer=memory,
    interrupt_before=["execute_action"]  # 执行操作前暂停
)

config = {"configurable": {"thread_id": "task-1"}}

# 第一次运行 → 到 execute_action 前暂停
result = app.invoke(input_data, config)
# 此时可以查看 Agent 要执行的操作

# 获取当前状态
state = app.get_state(config)
print(state.next)  # ['execute_action']
print(state.values)  # 当前状态数据

# 人工确认后继续
result = app.invoke(None, config)  # 传 None 表示继续

# 或修改状态后继续（人工修正）
app.update_state(config, {"approved": True})
result = app.invoke(None, config)
```

### 子图（Subgraph）
将复杂图拆分为可复用的子图：

```python
# 定义子图
class ResearchState(TypedDict):
    topic: str
    search_results: list[str]
    summary: str

research_subgraph = StateGraph(ResearchState)
research_subgraph.add_node("search", search_node)
research_subgraph.add_node("summarize", summarize_node)
research_subgraph.add_edge(START, "search")
research_subgraph.add_edge("search", "summarize")
research_subgraph.add_edge("summarize", END)
research_app = research_subgraph.compile()

# 在主图中使用子图
class MainState(TypedDict):
    research_output: str
    final_report: str

main_graph = StateGraph(MainState)
main_graph.add_node("research", research_app)  # 子图作为节点
main_graph.add_node("write_report", write_report_node)
main_graph.add_edge(START, "research")
main_graph.add_edge("research", "write_report")
main_graph.add_edge("write_report", END)
```

### 流式输出

```python
# 节点级流式（每个节点完成时输出状态更新）
for event in app.stream(input_data, config, stream_mode="updates"):
    for node_name, state_update in event.items():
        print(f"Node: {node_name}")
        print(f"Update: {state_update}")

# Token 级流式（LLM 输出的每个 token）
async for event in app.astream_events(input_data, config, version="v2"):
    if event["event"] == "on_chat_model_stream":
        print(event["data"]["chunk"].content, end="")
    elif event["event"] == "on_chain_end":
        print(f"\nNode completed: {event['name']}")

# 多种流式模式
for chunk in app.stream(input_data, config, stream_mode="values"):
    # 每个节点完成后输出完整状态
    print(chunk)

for chunk in app.stream(input_data, config, stream_mode="messages"):
    # 只输出 LLM 消息
    print(chunk)
```

---

## 9. LangGraph 实战模式

### 模式一：ReAct Agent

```python
from langgraph.prebuilt import create_react_agent

# 最简单的 ReAct Agent
tools = [search_docs, calculate]
agent = create_react_agent(llm, tools, checkpointer=memory)

result = agent.invoke(
    {"messages": [HumanMessage(content="查一下 Redis 的缓存策略")]},
    config={"configurable": {"thread_id": "user-1"}}
)
```

### 模式二：Corrective RAG

```
graph:
  START → retrieve → grade_documents
  grade_documents →|相关| generate
  grade_documents →|不相关| web_search → generate
  generate → grade_answer
  grade_answer →|好| END
  grade_answer →|不好| rewrite_query → retrieve
```

```python
def grade_documents(state):
    docs = state["documents"]
    question = state["question"]

    # 用 LLM 评估每个文档的相关性
    relevant_docs = []
    for doc in docs:
        grade = llm.with_structured_output(GradeResult).invoke(
            f"文档是否与问题相关？\n问题：{question}\n文档：{doc}"
        )
        if grade.is_relevant:
            relevant_docs.append(doc)

    if relevant_docs:
        return {"documents": relevant_docs, "search_needed": False}
    else:
        return {"documents": [], "search_needed": True}

def route_after_grade(state):
    if state["search_needed"]:
        return "web_search"
    return "generate"
```

### 模式三：Multi-Agent Supervisor

```python
from langgraph.graph import StateGraph, END, START

class TeamState(TypedDict):
    messages: Annotated[list, add_messages]
    next_agent: str

def supervisor_node(state):
    """Supervisor 决定下一步交给谁"""
    response = llm.with_structured_output(RouteDecision).invoke(
        f"基于当前对话，决定下一步交给哪个 Agent："
        f"researcher（需要搜索信息）、coder（需要写代码）、"
        f"FINISH（任务完成）"
    )
    return {"next_agent": response.agent}

def researcher_node(state):
    """研究员 Agent"""
    result = research_agent.invoke(state["messages"])
    return {"messages": [AIMessage(content=result, name="researcher")]}

def coder_node(state):
    """程序员 Agent"""
    result = coding_agent.invoke(state["messages"])
    return {"messages": [AIMessage(content=result, name="coder")]}

# 构建图
workflow = StateGraph(TeamState)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("researcher", researcher_node)
workflow.add_node("coder", coder_node)

workflow.add_edge(START, "supervisor")
workflow.add_conditional_edges(
    "supervisor",
    lambda state: state["next_agent"],
    {
        "researcher": "researcher",
        "coder": "coder",
        "FINISH": END
    }
)
workflow.add_edge("researcher", "supervisor")
workflow.add_edge("coder", "supervisor")
```

### 模式四：Plan-and-Execute

```python
class PlanState(TypedDict):
    plan: list[str]         # 计划步骤
    current_step: int       # 当前步骤
    results: list[str]      # 每步的结果
    final_answer: str       # 最终答案

def planner_node(state):
    """生成执行计划"""
    plan = llm.invoke(f"为以下任务生成步骤计划：{state['messages'][-1].content}")
    steps = parse_plan(plan.content)
    return {"plan": steps, "current_step": 0}

def executor_node(state):
    """执行当前步骤"""
    step = state["plan"][state["current_step"]]
    result = execute_step(step, state["results"])
    return {
        "results": state["results"] + [result],
        "current_step": state["current_step"] + 1
    }

def should_continue(state):
    if state["current_step"] >= len(state["plan"]):
        return "summarize"
    return "execute"
```

---

## 10. LangSmith 监控与调试

### 配置
```python
import os
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = "your-langsmith-api-key"
os.environ["LANGCHAIN_PROJECT"] = "my-rag-project"

# 之后所有 LangChain/LangGraph 调用自动记录到 LangSmith
```

### 核心功能
- **Trace（追踪）**：记录每次调用的完整执行链路
  - 每个组件的输入/输出
  - Token 使用量和延迟
  - 错误信息和堆栈追踪
- **Debug（调试）**：
  - 查看 Agent 的每一步决策
  - 分析为什么模型选择了某个工具
  - 对比不同 Prompt 版本的效果
- **Evaluate（评估）**：
  - 创建评估数据集
  - 运行自动化评估
  - 对比不同版本的性能
- **Monitor（监控）**：
  - 生产环境实时监控
  - 性能指标追踪
  - 异常告警

### 自定义追踪
```python
from langsmith import traceable

@traceable(name="my_rag_pipeline")
def rag_pipeline(question: str):
    docs = retriever.invoke(question)
    context = format_docs(docs)
    answer = llm.invoke(prompt.format(context=context, question=question))
    return answer
```

---

## 11. 实战案例：构建 RAG Agent

完整的 RAG Agent，支持检索、质量评估、自动重试和 Human-in-the-Loop：

```python
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.messages import HumanMessage, SystemMessage

# 1. 定义状态
class RAGState(TypedDict):
    messages: Annotated[list, add_messages]
    question: str
    documents: list[str]
    generation: str
    is_relevant: bool
    retry_count: int

# 2. 初始化组件
llm = ChatOpenAI(model="gpt-4")
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectorstore = Chroma(persist_directory="./db", embedding_function=embeddings)
retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

# 3. 定义节点
def retrieve(state: RAGState) -> dict:
    docs = retriever.invoke(state["question"])
    return {"documents": [d.page_content for d in docs]}

def grade_documents(state: RAGState) -> dict:
    # 用 LLM 评估文档相关性
    relevant = []
    for doc in state["documents"]:
        result = llm.invoke(
            f"这个文档是否与问题相关？只回答'是'或'否'。\n"
            f"问题：{state['question']}\n文档：{doc}"
        )
        if "是" in result.content:
            relevant.append(doc)
    return {"documents": relevant, "is_relevant": len(relevant) > 0}

def generate(state: RAGState) -> dict:
    context = "\n\n".join(state["documents"])
    response = llm.invoke([
        SystemMessage(content=f"基于以下资料回答。如果资料不足请说明。\n\n{context}"),
        HumanMessage(content=state["question"])
    ])
    return {"generation": response.content}

def rewrite_query(state: RAGState) -> dict:
    rewritten = llm.invoke(
        f"原始问题检索效果不好，请重写为更适合检索的形式：\n{state['question']}"
    )
    return {
        "question": rewritten.content,
        "retry_count": state.get("retry_count", 0) + 1
    }

# 4. 定义路由
def route_after_grade(state: RAGState) -> str:
    if state["is_relevant"]:
        return "generate"
    if state.get("retry_count", 0) >= 2:
        return "generate"  # 重试次数用完，强制生成
    return "rewrite"

# 5. 构建图
workflow = StateGraph(RAGState)
workflow.add_node("retrieve", retrieve)
workflow.add_node("grade", grade_documents)
workflow.add_node("generate", generate)
workflow.add_node("rewrite", rewrite_query)

workflow.add_edge(START, "retrieve")
workflow.add_edge("retrieve", "grade")
workflow.add_conditional_edges("grade", route_after_grade, {
    "generate": "generate",
    "rewrite": "rewrite"
})
workflow.add_edge("rewrite", "retrieve")
workflow.add_edge("generate", END)

# 6. 编译（带持久化和人工介入）
memory = MemorySaver()
app = workflow.compile(
    checkpointer=memory,
    interrupt_before=["generate"]  # 生成前可暂停人工审核
)

# 7. 运行
config = {"configurable": {"thread_id": "session-1"}}
result = app.invoke(
    {"question": "什么是 B+ 树索引？", "messages": [], "documents": [],
     "generation": "", "is_relevant": False, "retry_count": 0},
    config
)

# 人工确认后继续
state = app.get_state(config)
if state.next:  # 有待执行的节点
    result = app.invoke(None, config)

print(result["generation"])
```

---

## 面试高频问题

### Q1: LangChain 的核心组件有哪些？各自的作用？
**答**：核心组件包括：Chat Model（封装各种 LLM 调用）、Prompt Template（可重用的提示模板）、Output Parser（解析 LLM 输出为结构化数据）、Document Loader/Splitter（加载和分块文档）、Embedding（文本向量化）、VectorStore（向量存储和检索）、Retriever（检索器）、Tools（工具定义）。通过 LCEL 管道符将它们组合。

### Q2: LCEL 是什么？有什么优势？
**答**：LCEL（LangChain Expression Language）是用 `|` 管道符链式组合 Runnable 组件的语法。优势：自动支持流式输出（token 级）、异步调用、并行执行（RunnableParallel）、重试和回退机制、与 LangSmith 无缝集成实现全链路追踪。简洁的语法降低了构建复杂 LLM 应用的门槛。

### Q3: 如何用 LangChain 构建一个 RAG 应用？
**答**：五步流程：1) Document Loader 加载文档；2) Text Splitter 分块；3) Embedding 模型向量化；4) VectorStore 存储并创建 Retriever；5) LCEL 构建 RAG Chain：`retriever | format_docs → prompt + question → LLM → StrOutputParser`。可以进一步添加重排序、混合搜索等优化。

### Q4: LangGraph 的 State、Node、Edge 分别是什么？
**答**：State 是流经整个图的共享数据结构（TypedDict），每个节点可以读取和部分更新。Node 是图中的处理函数，接收 State 返回部分更新。Edge 连接节点，可以是固定边（A→B）或条件边（根据 State 动态路由到不同节点）。Annotated[list, add_messages] 等 reducer 控制状态如何合并。

### Q5: LangGraph 和 LangChain Agent 的区别？
**答**：LangChain Agent 由 LLM 自主决策下一步做什么，流程不可预测。LangGraph 由开发者定义图结构（节点和边），流程可控可预测。LangGraph 还原生支持状态持久化（Checkpoint）、循环执行、Human-in-the-Loop、子图复用。LangGraph 更适合复杂的生产级应用。

### Q6: LangGraph 的 Checkpoint 有什么用？
**答**：Checkpoint 将图的状态持久化到存储（内存/SQLite/PostgreSQL），实现：1) 多轮对话状态保持（同一 thread_id）；2) 暂停后恢复执行；3) 回退到历史状态重新执行；4) 查看完整执行历史。是 Human-in-the-Loop 和容错的基础。

### Q7: Human-in-the-Loop 如何实现？
**答**：在 compile 时指定 `interrupt_before=["node_name"]`，执行到该节点前自动暂停。可以查看当前状态（app.get_state）、修改状态（app.update_state）、然后继续执行（app.invoke(None, config)）。适用于需要人工审核 Agent 决策的关键步骤。

### Q8: 如何用 LangGraph 实现多 Agent 协作？
**答**：Supervisor 模式：定义 supervisor 节点决定下一步交给哪个 Agent（条件边路由），每个 Agent 执行完后回到 supervisor，supervisor 判断是否完成。通过 State 共享消息和中间结果。也可以用子图封装每个 Agent 的内部逻辑，在主图中编排。

### Q9: LangSmith 的作用？
**答**：LangChain 的可观测性平台，提供：Trace（记录每次调用的完整执行链路、输入输出、token 使用、延迟）、Debug（分析 Agent 决策过程）、Evaluate（创建评估数据集，自动化测试不同版本）、Monitor（生产环境监控和告警）。只需设置环境变量即可自动记录。

### Q10: 实战中构建 RAG Agent 需要注意什么？
**答**：1) 文档质量和分块策略是基础（垃圾进垃圾出）；2) 混合检索比纯向量检索效果好；3) 重排序显著提升结果质量；4) 加入质量评估和重试机制（Corrective RAG）；5) Human-in-the-Loop 保障安全；6) 用 LangSmith 监控每步的输入输出；7) 设置最大重试次数防止无限循环；8) 流式输出提升用户体验。
