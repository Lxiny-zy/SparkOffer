# Multi-Agent 编排

单 Agent 适合垂直任务，复杂场景往往需要多个 Agent 协作——专业分工、并行加速、互相校验。Multi-Agent 是 Agent 工程化的高阶能力。

## 1. 什么时候需要 Multi-Agent

| 场景 | 单 Agent | Multi-Agent |
|---|---|---|
| 一问一答 | ✓ | ✗（杀鸡用牛刀） |
| 端到端工作流（接收任务 → 拆解 → 执行 → 汇总） | 勉强 | ✓ |
| 角色冲突任务（创作 + 校对、攻 + 防） | ✗ | ✓ |
| 大量独立子任务并行 | ✗ | ✓ |
| 不同领域专长（Python + 数据库 + 设计） | 全能 prompt 难做 | 拆分 |
| 长上下文（超 context 限制） | ✗ | 分片处理 |

## 2. 经典编排模式

### 2.1 Supervisor 模式

一个主 Agent 接收任务、拆解、分派给专长子 Agent、汇总结果。LangGraph 的标准实践。

```python
class SupervisorState(TypedDict):
    messages: Annotated[list, add]
    next_agent: str  # which sub-agent to route to

def supervisor(state):
    response = llm.with_structured_output(Route).invoke([
        SystemMessage("根据用户需求决定调用哪个子 Agent: researcher / coder / reviewer / FINISH"),
        *state["messages"],
    ])
    return {"next_agent": response.agent}

def researcher(state): ...
def coder(state): ...
def reviewer(state): ...

graph = StateGraph(SupervisorState)
graph.add_node("supervisor", supervisor)
graph.add_node("researcher", researcher)
graph.add_node("coder", coder)
graph.add_node("reviewer", reviewer)

graph.set_entry_point("supervisor")
graph.add_conditional_edges("supervisor", lambda s: s["next_agent"], {
    "researcher": "researcher", "coder": "coder",
    "reviewer": "reviewer", "FINISH": END,
})
for sub in ["researcher", "coder", "reviewer"]:
    graph.add_edge(sub, "supervisor")  # 子 Agent 完成回到 supervisor
```

### 2.2 Pipeline 模式

固定顺序：Agent A → Agent B → Agent C。每个 Agent 处理上一个的输出。

例：内容审核流水线 = 审核员（标问题）→ 修订员（改）→ 终审（通过/拒绝）。

适合流程稳定的任务。LangGraph 一行 `add_edge` 串起来即可。

### 2.3 并行 + 汇总（Map-Reduce）

把任务切片，多个 Agent 并行处理，最后 Reducer 合并。

```python
async def parallel_research(state):
    queries = state["sub_queries"]
    results = await asyncio.gather(*[research_one(q) for q in queries])
    return {"research_results": results}

def synthesize(state):
    return {"answer": synthesizer_llm.invoke(format(state["research_results"]))}
```

### 2.4 辩论 / 自洽（Debate / Self-Consistency）

多个 Agent 独立给出方案，再相互辩论 / 投票。提升复杂决策准确率。

```
[问题] → [Agent A 方案] + [Agent B 方案] + [Agent C 方案]
              ↓
       [Critic 评估三方案，给出最优或综合]
```

### 2.5 Hierarchical（层级嵌套）

Top supervisor 调用 Mid supervisor，Mid 调用 Worker。规模大、域多场景。

```
CEO Agent
  ├─ Marketing Manager Agent
  │    ├─ SEO Worker
  │    └─ Content Worker
  └─ Engineering Manager Agent
       ├─ Backend Worker
       └─ Frontend Worker
```

## 3. 通信协议

### 3.1 共享 State

最简单：所有 Agent 操作同一个 LangGraph State。优点：透明、可观测；缺点：紧耦合、扩展难。

### 3.2 消息总线

每个 Agent 是独立服务，通过 message broker（Redis Streams / Kafka / RabbitMQ）通信。

```python
class AgentNode:
    def __init__(self, name, broker):
        self.name = name
        self.broker = broker
        self.broker.subscribe(f"task.{name}.*", self.handle)

    def handle(self, msg):
        result = self.process(msg.payload)
        self.broker.publish(f"result.{msg.task_id}", result)
```

适合大规模、异步、多语言混部。

### 3.3 标准协议：A2A、MCP

行业开始有标准化协议尝试：
- **MCP (Model Context Protocol)**：Anthropic 主导，重在 Agent ↔ Tool/Resource 通信
- **A2A (Agent2Agent)**：Google 提出，Agent ↔ Agent 通信
- **AutoGen Conversation**：MS 的 Agent 对话框架

未来 Multi-Agent 系统大概率是混搭：MCP 接 tool，A2A 跨 Agent 通信。

## 4. 状态与上下文管理

### 4.1 共享内存还是各自隔离

- **共享**：方便协作但有干扰风险（一个 Agent 污染 state，影响所有人）
- **隔离**：清晰但需显式传递必要数据

实践：核心字段（task_id、user_id、shared_context）共享；中间产物各自私有。

### 4.2 上下文截断与摘要

子 Agent 之间传完整对话历史会快速爆 context。两个策略：
- **结构化交接**：每个 Agent 完成后输出结构化 summary（state + 决策 + 待办），下游只看 summary 不看原文
- **角色感知裁剪**：传给 Agent 的上下文按角色相关度筛选

### 4.3 全局 trace

每次调用打 root_trace_id，跨 Agent 调用 propagate trace。Langfuse / Phoenix 都支持。

## 5. 冲突解决

多个 Agent 给出不一致结论怎么办？

### 5.1 投票

3+ 个 Agent 独立判断，多数获胜。简单但成本高。

### 5.2 优先级

预定义角色权重（CEO Agent > Manager Agent > Worker Agent）。

### 5.3 仲裁 Agent

冲突时启动专门的 Critic 综合考虑各方观点出最终决策。

### 5.4 用户介入

冲突无法机器解决时升级人工。

## 6. 错误传播与隔离

### 6.1 单 Agent 失败不应拖垮整体

```python
async def safe_agent_call(agent, input):
    try:
        return await asyncio.wait_for(agent.invoke(input), timeout=60)
    except (TimeoutError, Exception) as e:
        return {"error": str(e), "fallback": True}
```

下游 Agent 看到 fallback 标记决定降级或跳过。

### 6.2 重试与回退

- 网络错误指数退避
- LLM 输出格式错让 Critic Agent 改写
- 全员失败降级到固定模板回答

### 6.3 死循环防护

Supervisor 路由可能无限转圈（A → B → A → B...）。必须有：
- 最大轮次（`max_iterations=10`）
- 状态环检测（同 state 重复出现报警）
- 总超时

## 7. 性能

### 7.1 并行优于串行

独立子任务必须并行（asyncio.gather）。LangGraph 通过加多个出边触发并行。

### 7.2 模型混部

按任务难度配模型：
- Supervisor / Critic：强模型（Opus / GPT-5）
- Worker：中模型（Sonnet / GPT-4o）
- 简单提取：小模型（Haiku / GPT-4.1-mini）

### 7.3 共享缓存

多 Agent 都查相同知识库 / 调相同 API 时，跨 Agent 的 result cache 能砍 50% 成本。

## 8. 可观测性

### 8.1 必备 trace 字段

```json
{
  "trace_id": "...",
  "agent_role": "researcher",
  "parent_agent": "supervisor",
  "iteration": 2,
  "input_summary": "...",
  "output_summary": "...",
  "tools_used": ["search", "scrape"],
  "tokens": {"input": 1200, "output": 500},
  "cost_usd": 0.012,
  "duration_ms": 3400
}
```

### 8.2 可视化

- 时间轴视图：看每个 Agent 何时启动、何时完成
- DAG 视图：看 Agent 之间的调用关系
- Token / 成本 breakdown：知道钱花哪去了

LangSmith / Langfuse 内置这些视图。

## 9. 现有框架对比

| 框架 | 优势 | 劣势 |
|---|---|---|
| **LangGraph** | 底层、可控、生产级、丰富生态 | 学习曲线 |
| **CrewAI** | 声明式、上手快、中文文档好 | 灵活性差、生产案例少 |
| **AutoGen** | MS 出品、研究丰富、对话驱动 | 工程化弱 |
| **MetaGPT** | 软件开发场景特化 | 通用性差 |
| **Swarm (OpenAI)** | 极简、官方教学 | 只是 demo，不适合生产 |

业务场景：先 LangGraph，验证后视情况引入。

## 10. 高频面试题

**Q1：什么场景适合 Multi-Agent？**
任务可拆解 + 角色专长不同 + 并行收益大。例如：研究助手（搜索 Agent + 阅读 Agent + 写作 Agent）、内容审核（标注 + 修订 + 终审）。简单一问一答用单 Agent 即可。

**Q2：Multi-Agent 怎么避免成本失控？**
① 模型分层（核心强模型，外围小模型）；② 缓存共享中间结果；③ 限制最大轮次和总 token 预算；④ 并行而不是串行（不省 token 但省时间）；⑤ 持续 monitor 单任务成本，异常告警。

**Q3：Agent 间通信选共享 state 还是消息总线？**
- 单进程小规模 → 共享 state（LangGraph state）
- 跨服务 / 大规模 → 消息总线（Redis Streams / Kafka）
- 混合架构 → 内部用 state，跨边界用 broker

**Q4：怎么处理 Agent 互相 hallucinate（连环错）？**
① 每个 Agent 输出加 confidence 字段；② Critic Agent 校验关键决策；③ 关键事实必须有 source（RAG 引用），不能编；④ 多 Agent 独立判断 + 投票；⑤ 持续在评估集上 monitor。

**Q5：LangGraph 怎么实现 Supervisor 模式？**
StateGraph 加一个 supervisor 节点，用 conditional_edges 根据 supervisor 决定路由到哪个子节点；每个子节点完成后 add_edge 回 supervisor；supervisor 决定 FINISH 时路由到 END。

**Q6：Multi-Agent 系统的可观测性挑战？**
单 Agent 一条 trace，Multi-Agent 是 DAG。需要：① 全局 trace_id propagate；② 父子关系记录；③ 时间轴 + DAG 双视图；④ 跨 Agent 的 token / 成本聚合。LangSmith / Langfuse 都支持。
