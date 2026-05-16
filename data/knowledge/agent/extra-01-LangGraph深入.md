# LangGraph 深入：Agent 状态编排

LangGraph 是 LangChain 团队推出的有状态、可控、可观测的 Agent 编排框架。相比 LangChain 的链式调用，LangGraph 把 Agent 建模为「带状态的有向图」，每个节点是一个可执行单元，边定义流转逻辑，适合构建复杂多阶段、可中断、可回放的 Agent 系统。

## 1. 核心概念

| 概念 | 说明 |
|---|---|
| `StateGraph` | 图的容器，定义 state schema、节点、边 |
| `Node` | 接收 state、返回 state 更新的函数（可同步可异步） |
| `Edge` | 节点流转：固定边 / 条件边 / `END` |
| `State` | 跨节点传递的数据，通常是 TypedDict 或 Pydantic Model |
| `Reducer` | 字段级合并策略（如 `operator.add` 让 messages list 累加而非覆盖） |
| `Checkpointer` | 持久化 state 快照，支持时间旅行与断点续跑 |

## 2. State 设计

State 是 LangGraph 的核心抽象。**字段的 reducer 决定多次更新如何合并**。

```python
from typing import Annotated, TypedDict
from operator import add
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # messages 用 add reducer 实现追加；不写 reducer 则默认覆盖
    messages: Annotated[list[BaseMessage], add]
    user_id: str
    retrieved_docs: list[str]
    iteration: int
```

**常见错误**：忘记写 reducer，导致 messages 被覆盖，对话历史丢失。

## 3. 节点与条件路由

```python
def retrieve(state: AgentState) -> dict:
    docs = vector_store.search(state["messages"][-1].content)
    return {"retrieved_docs": docs}

def generate(state: AgentState) -> dict:
    response = llm.invoke([
        SystemMessage("基于以下资料回答："),
        HumanMessage("\n".join(state["retrieved_docs"])),
        *state["messages"],
    ])
    return {"messages": [response]}

def should_continue(state: AgentState) -> str:
    if state["iteration"] >= 3:
        return END
    if "TOOL_CALL" in state["messages"][-1].content:
        return "tools"
    return END

graph = StateGraph(AgentState)
graph.add_node("retrieve", retrieve)
graph.add_node("generate", generate)
graph.set_entry_point("retrieve")
graph.add_edge("retrieve", "generate")
graph.add_conditional_edges("generate", should_continue, {"tools": "retrieve", END: END})
app = graph.compile()
```

## 4. Checkpointer 与时间旅行

Checkpointer 把每个节点执行后的 state 写入持久化存储（内存 / SQLite / Redis / Postgres）。带 checkpointer 的图获得三种能力：

1. **断点续跑**：进程重启后用 `thread_id` 恢复
2. **时间旅行**：回到某个历史 checkpoint，修改状态后重新跑
3. **Human-in-the-loop**：在指定节点 `interrupt`，等人工干预后继续

```python
from langgraph.checkpoint.sqlite import SqliteSaver

saver = SqliteSaver.from_conn_string("checkpoints.db")
app = graph.compile(checkpointer=saver, interrupt_before=["sensitive_action"])

config = {"configurable": {"thread_id": "user-42"}}
app.invoke({"messages": [HumanMessage("...")]}, config)

# 列出所有 checkpoint
for state in app.get_state_history(config):
    print(state.config, state.values)

# 从指定 checkpoint 恢复
app.invoke(None, config={"configurable": {"thread_id": "user-42", "checkpoint_id": "..."}})
```

## 5. Streaming：三种粒度

| 模式 | 输出粒度 | 适用 |
|---|---|---|
| `stream(mode="values")` | 每个节点完整 state | 调试 / 监控 |
| `stream(mode="updates")` | 每次只发增量字段 | 节省带宽 |
| `astream_events()` | 包括 LLM token / tool 调用细节 | 前端打字机效果 |

```python
async for event in app.astream_events(input_, config, version="v2"):
    if event["event"] == "on_chat_model_stream":
        token = event["data"]["chunk"].content
        await websocket.send(token)
```

## 6. Human-in-the-loop

通过 `interrupt_before` / `interrupt_after` 在敏感节点暂停，等待人工审核。

```python
app = graph.compile(checkpointer=saver, interrupt_before=["execute_payment"])
app.invoke(input_, config)
# 此时图停在 execute_payment 之前，state 已存

# 人工检查后修改 state 并继续
app.update_state(config, {"approved": True})
app.invoke(None, config)  # None 表示从断点恢复
```

## 7. Subgraph 与多 Agent

复杂系统用 subgraph 把一组节点封装成单元，外层图调用：

```python
researcher = build_researcher_graph()  # 子图：检索 + 总结
writer = build_writer_graph()          # 子图：草稿 + 校对

main = StateGraph(MainState)
main.add_node("research", researcher.compile())
main.add_node("write", writer.compile())
main.add_edge("research", "write")
```

## 8. 错误处理模式

**重试**：节点抛异常时通过 try/except 写入 error 字段，路由到 retry 节点。
**回退**：路由到 fallback 节点用更弱模型或人工介入。
**中断**：写入 `_interrupt` 标记，由外层观察并切换流程。

```python
def safe_call_llm(state):
    for attempt in range(3):
        try:
            return {"messages": [llm.invoke(state["messages"])]}
        except RateLimitError:
            time.sleep(2 ** attempt)
    return {"error": "LLM 三次重试失败", "fallback": True}
```

## 9. 性能优化

- **并行节点**：用 `add_node` + `add_edge` 让多个独立检索并发跑
- **State 切片**：超大 state 拆成 main/aux，aux 不放进 checkpoint
- **缓存 LLM**：相同 prompt 命中缓存，避免重复推理
- **流式控制**：长链不需要每个节点都 stream，只在最终生成节点开

## 10. 高频面试题

**Q1：LangGraph 比 LangChain 优势在哪？**
LangChain 链式调用难以表达条件分支、循环、并发；LangGraph 的图模型自然支持这些拓扑，且自带 state 管理、checkpointing、interrupt，更适合生产级 Agent。

**Q2：怎么实现一个能反思的 Agent？**
两个节点：generate 产出答案，reflect 评估答案质量，条件边判断「质量分 < 阈值」时回到 generate 重写，最多 N 次。

**Q3：怎么做断点续跑？**
启用 SqliteSaver/PostgresSaver checkpointer + 固定的 thread_id，进程崩溃后用同一 thread_id 调 invoke，框架自动从最近 checkpoint 恢复。

**Q4：State 字段更新冲突怎么处理？**
通过 reducer 显式指定合并策略：list 用 `operator.add`、dict 用自定义 merger、原子值用覆盖（默认）。多并发节点同时写同一字段时也按 reducer 合并。

**Q5：怎么把 LangGraph 跟 FastAPI 集成做流式输出？**
FastAPI endpoint 用 `StreamingResponse(app.astream_events(input_, config, version="v2"))`，前端用 EventSource 接收；token 事件用 SSE 格式 `data: {token}\n\n` 发送。

**Q6：多 Agent 编排选 LangGraph 还是 CrewAI / AutoGen？**
LangGraph：底层、可控、生产级；CrewAI：声明式、上手快、但灵活性差；AutoGen：研究导向、对话驱动、生产部署经验少。重业务可控选 LangGraph。
