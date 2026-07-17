# Multi-Agent 系统与框架

## 1. 为什么需要 Multi-Agent

### 单 Agent 的局限
- **上下文爆炸**：复杂任务涉及大量工具和中间结果，单 Agent 上下文窗口不够
- **角色混乱**：单 Agent 扮演过多角色，Prompt 互相干扰
- **能力单一**：难以做到"既是程序员又是测试工程师又是产品经理"
- **难以并行**：串行执行效率低
- **容错差**：单点失败

### Multi-Agent 的优势
- **职责分离**：每个 Agent 专注单一领域（如"代码生成"、"代码审查"、"测试"）
- **并行执行**：独立任务可并发处理
- **扩展性**：新增角色只需加新 Agent，不影响其他
- **模拟人类团队**：天然适合工作流类任务（产品规划 → 开发 → 测试 → 部署）
- **知识隔离**：不同 Agent 可用不同模型、不同知识库

### 适用场景
- 软件开发（MetaGPT、ChatDev）
- 复杂研究报告
- 客服分流（意图识别 Agent → 专业领域 Agent）
- 游戏 NPC
- 仿真模拟（斯坦福小镇 Generative Agents）

---

## 2. 核心设计模式

### 模式 1：Supervisor（监督者）

```
             Supervisor
           (决策路由)
          /     |     \
     Agent A  Agent B  Agent C
    (写代码) (写文档) (写测试)
```
- Supervisor 接收用户输入，决定交给哪个 Agent
- 子 Agent 执行后结果回到 Supervisor，决策下一步
- 适合：**中心化决策**，Supervisor 掌控全局

### 模式 2：Hierarchical（层级）

```
         CEO Agent
        /        \
    CTO Agent   CFO Agent
    /     \       |
  Dev   Test    Accounting
```
- 多层级，每层有自己的 Supervisor
- 适合：**企业级**复杂任务，模拟组织结构

### 模式 3：Network（网状对等）

```
  Agent A ◄──► Agent B
     ▲           ▲
     │           │
     ▼           ▼
  Agent D ◄──► Agent C
```
- 任意两 Agent 可直接通信
- 适合：**协作讨论**类任务（辩论、头脑风暴）

### 模式 4：Pipeline（流水线）

```
Agent1 → Agent2 → Agent3 → Output
(解析)  (处理)  (生成)
```
- 固定顺序，前一个 Agent 输出是后一个输入
- 适合：**明确工作流**（ETL、文档处理）

### 模式 5：Debate（辩论）

```
Agent A (正方) ↔ Agent B (反方)
              ↓
         Judge Agent（裁判）
              ↓
            最终答案
```
- 两 Agent 对立观点讨论，裁判决出结果
- 提升推理质量，减少幻觉

### 模式 6：Group Chat（群聊）

- 多 Agent 在同一"聊天室"发言，轮流/抢占式
- 典型：AutoGen 的 `GroupChat`

---

## 3. 主流框架对比

| 框架 | 出品方 | 语言 | 特点 | 适用 |
|------|--------|------|------|------|
| **AutoGen** | Microsoft | Python | 对话驱动、GroupChat、可人工介入 | 研究、原型 |
| **CrewAI** | 社区 | Python | 角色+任务+流程三元组、易上手 | 业务流水线 |
| **MetaGPT** | DeepWisdom | Python | 模拟软件公司、SOP 驱动 | 代码生成 |
| **LangGraph** | LangChain | Python | 状态图、可控性强 | 生产级 Agent |
| **Swarm** | OpenAI | Python | 极简、轻量 handoff | 学习、简单场景 |
| **OpenAI Agents SDK** | OpenAI | Python | Swarm 后继、生产级 | OpenAI 生态 |
| **LlamaIndex Workflows** | LlamaIndex | Python | 事件驱动 | RAG + Agent |
| **AutoGPT** | 社区 | Python | 最早的自主 Agent | 历史意义 |
| **BabyAGI** | 社区 | Python | 任务列表驱动 | 学习 |
| **AgentScope** | 阿里 | Python | 分布式 Agent | 国内大规模 |

---

## 4. AutoGen 详解

### 核心概念
- **ConversableAgent**：可对话 Agent 基类
- **AssistantAgent**：带 LLM 的 Agent
- **UserProxyAgent**：代理人类用户（可执行代码、调工具）
- **GroupChat**：多 Agent 群聊
- **GroupChatManager**：群聊调度

### 最简示例

```python
from autogen import AssistantAgent, UserProxyAgent

config_list = [{"model": "gpt-4o", "api_key": "..."}]

assistant = AssistantAgent(
    name="coder",
    llm_config={"config_list": config_list},
    system_message="你是资深 Python 工程师"
)

user_proxy = UserProxyAgent(
    name="user",
    code_execution_config={"work_dir": "coding", "use_docker": False},
    human_input_mode="NEVER",
    max_consecutive_auto_reply=10
)

user_proxy.initiate_chat(
    assistant,
    message="写一个爬取 HackerNews 首页标题的脚本并运行"
)
```

UserProxyAgent 能**实际执行代码**，Assistant 写完代码后它执行并反馈错误，形成自动修复循环。

### GroupChat 示例

```python
from autogen import GroupChat, GroupChatManager

planner = AssistantAgent("planner", system_message="你负责任务分解", llm_config=llm_config)
coder = AssistantAgent("coder", system_message="你负责写代码", llm_config=llm_config)
reviewer = AssistantAgent("reviewer", system_message="你负责代码审查", llm_config=llm_config)

groupchat = GroupChat(
    agents=[planner, coder, reviewer, user_proxy],
    messages=[],
    max_round=15,
    speaker_selection_method="auto"  # auto/round_robin/manual
)

manager = GroupChatManager(groupchat=groupchat, llm_config=llm_config)
user_proxy.initiate_chat(manager, message="实现一个 LRU 缓存")
```

### AutoGen 的进阶
- **Human in the Loop**：`human_input_mode="ALWAYS"` 每轮询问用户
- **Code Executor**：Docker / Jupyter / 本地 三种执行后端
- **Nested Chat**：Agent 内部再嵌套 GroupChat
- **Teachability**：Agent 可从对话中学习并保存到长期记忆
- **AutoGen Studio**：可视化拖拽搭建

---

## 5. CrewAI 详解

### 核心抽象
- **Agent**：角色（role + goal + backstory）
- **Task**：具体任务（description + expected_output + agent）
- **Crew**：Agent 和 Task 的集合 + 执行流程（Process）
- **Tool**：工具
- **Flow**：CrewAI 2024 新增，状态流控制

### 示例

```python
from crewai import Agent, Task, Crew, Process

researcher = Agent(
    role="资深市场研究员",
    goal="分析 AI 行业最新趋势",
    backstory="你在硅谷做了 10 年行研...",
    tools=[search_tool],
    llm=llm
)

writer = Agent(
    role="技术作家",
    goal="撰写深度分析文章",
    backstory="你擅长把复杂技术讲得通俗...",
    llm=llm
)

task1 = Task(
    description="调研 2025 年 AI Agent 领域的 5 大突破",
    expected_output="含数据和来源的研究报告",
    agent=researcher
)

task2 = Task(
    description="基于研究报告写一篇 2000 字文章",
    expected_output="Markdown 格式文章",
    agent=writer,
    context=[task1]  # 依赖 task1 输出
)

crew = Crew(
    agents=[researcher, writer],
    tasks=[task1, task2],
    process=Process.sequential,  # sequential / hierarchical
    verbose=True
)

result = crew.kickoff()
```

### Process 类型
- **Sequential**：按 task 顺序执行
- **Hierarchical**：自动创建 Manager Agent 协调子 Agent

### 特色
- **上手极快**：几十行代码跑通
- **Role-based**：强调人格化，Prompt 自动生成
- **集成广**：可接 LangChain 工具

### 局限
- 控制粒度较粗，不如 LangGraph
- Hierarchical 模式下 Manager 决策不总可靠

---

## 6. MetaGPT 详解

### 核心思想
**SOP（Standard Operating Procedure）驱动**：把软件工程的最佳实践固化为 Agent 协作流程。

### 角色设计（模拟软件公司）

```
ProductManager → 写 PRD
      ↓
Architect       → 写系统设计文档
      ↓
ProjectManager  → 任务拆解
      ↓
Engineer        → 写代码
      ↓
QAEngineer      → 写测试
```

### 示例

```python
from metagpt.roles import ProductManager, Architect, Engineer
from metagpt.team import Team

team = Team()
team.hire([
    ProductManager(),
    Architect(),
    Engineer()
])

team.invest(investment=3.0)  # 控制预算
team.run_project("做一个 2048 游戏")
await team.run(n_round=5)
```

### 核心创新
- **SOP 明确化**：每个角色有明确产出物（PRD/设计图/代码）
- **Message Bus**：Agent 间通过发布-订阅消息通信
- **结构化输出**：强制 schema，减少自由文本歧义

### 适用
- 代码生成类任务
- 有明确工作流的业务场景

---

## 7. LangGraph 多 Agent

LangGraph 不是现成的 Multi-Agent 框架，而是**底层状态图**，上面可搭任何拓扑。

### Supervisor 模式

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
import operator

class State(TypedDict):
    messages: Annotated[list, operator.add]
    next: str

def supervisor(state):
    # LLM 决定下一个 agent
    llm_response = llm.invoke([
        {"role": "system", "content": "你是 Supervisor，根据对话决定下一步让谁工作：[researcher, coder, FINISH]"},
        *state["messages"]
    ])
    return {"next": parse_agent(llm_response)}

def researcher(state):
    result = research_agent.invoke(state["messages"])
    return {"messages": [result]}

def coder(state):
    result = code_agent.invoke(state["messages"])
    return {"messages": [result]}

graph = StateGraph(State)
graph.add_node("supervisor", supervisor)
graph.add_node("researcher", researcher)
graph.add_node("coder", coder)

graph.set_entry_point("supervisor")
graph.add_conditional_edges(
    "supervisor",
    lambda s: s["next"],
    {"researcher": "researcher", "coder": "coder", "FINISH": END}
)
graph.add_edge("researcher", "supervisor")
graph.add_edge("coder", "supervisor")

app = graph.compile()
```

### Swarm 模式（Handoff）

LangGraph 提供 `langgraph-swarm` 扩展，模拟 OpenAI Swarm：

```python
from langgraph_swarm import create_swarm, create_handoff_tool

agent1 = create_react_agent(
    llm, tools=[...], name="booking",
    prompt="你负责订票。需要付款时交给 payment。",
    destinations=["payment"]
)
agent2 = create_react_agent(
    llm, tools=[...], name="payment",
    prompt="你负责支付",
    destinations=["booking"]
)

swarm = create_swarm(
    agents=[agent1, agent2],
    default_active_agent="booking"
).compile()
```

### 优势
- **完全可控**：每个节点、边自定义
- **可持久化**：内置 Checkpointer，支持中断恢复
- **可观测**：集成 LangSmith
- **生产级**：工业界实际在用

---

## 8. OpenAI Swarm / Agents SDK

### Swarm（实验性，已被 Agents SDK 替代）

```python
from swarm import Swarm, Agent

client = Swarm()

def transfer_to_sales():
    return sales_agent

triage = Agent(
    name="Triage",
    instructions="判断用户需求，技术问题转给 tech，销售问题转给 sales",
    functions=[transfer_to_sales, transfer_to_tech]
)

sales_agent = Agent(name="Sales", instructions="你是销售")
tech_agent = Agent(name="Tech", instructions="你是技术支持")

response = client.run(
    agent=triage,
    messages=[{"role": "user", "content": "想买你们的企业版"}]
)
```

### 核心：Handoff

Agent 通过"返回另一个 Agent"实现控制权交接。代码极简。

### OpenAI Agents SDK（2025 正式版）

```python
from agents import Agent, Runner, function_tool

@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 25℃"

agent = Agent(
    name="Weather",
    instructions="查天气助手",
    tools=[get_weather]
)

result = await Runner.run(agent, "北京天气")
print(result.final_output)
```

特性：
- **Handoffs**：Agent 间转交
- **Guardrails**：输入/输出校验
- **Tracing**：内置链路追踪
- **Streaming**：流式输出

---

## 9. 通信机制

### 消息传递（Message Passing）
Agent 间通过结构化消息交互：
```python
{
  "from": "planner",
  "to": "coder",
  "content": "实现函数 X",
  "metadata": {"task_id": "001", "priority": "high"}
}
```

### 共享状态（Shared State）
LangGraph 的 State 对象，所有 Agent 读写同一状态：
```python
class GlobalState(TypedDict):
    plan: list
    code: str
    test_results: dict
```

### 发布订阅（Pub/Sub）
MetaGPT 的 Message Bus：Agent 订阅感兴趣的事件，按需处理。

### 黑板模式（Blackboard）
所有 Agent 共享一块"黑板"（文件/数据库），谁有能力就处理谁。

---

## 10. 人工介入（Human in the Loop）

### 介入时机
- **高风险操作确认**：删除、付款、部署
- **歧义澄清**：Agent 不确定时询问
- **定期审查**：每 N 步暂停汇报
- **最终产出审核**：交付前人工确认

### LangGraph 实现

```python
from langgraph.types import interrupt

def risky_action(state):
    # 中断并等待人工输入
    decision = interrupt({"question": "确定删除吗？", "data": state["target"]})
    if decision == "approve":
        return execute_delete(state)
    else:
        return {"status": "cancelled"}
```

### AutoGen 实现

```python
user_proxy = UserProxyAgent(
    human_input_mode="TERMINATE",  # NEVER / TERMINATE / ALWAYS
    is_termination_msg=lambda m: "TERMINATE" in m["content"]
)
```

---

## 11. 性能与成本优化

### 1. 选择合适的模型
- Supervisor / Router：用 Haiku / GPT-4o-mini 等小模型，快且便宜
- Worker Agent：用 Opus / GPT-4o，保证质量
- 极长上下文：Gemini 1.5 / Claude 1M

### 2. 并行化
独立任务并发跑：
```python
import asyncio
results = await asyncio.gather(
    agent_a.arun(task_a),
    agent_b.arun(task_b),
    agent_c.arun(task_c)
)
```

### 3. 缓存
- **Prompt Caching**：重复的 System Prompt、工具定义缓存
- **结果缓存**：相同输入的 Agent 结果缓存

### 4. 减少轮次
- 明确终止条件
- 避免"礼貌式寒暄"消耗 token
- 用 `max_iterations` 硬限制

### 5. 上下文管理
- 长对话用 Summary Memory 压缩
- 子 Agent 执行结果只回传**关键信息**，不回传全部历史

---

## 12. 真实应用案例

### Devin（AI 软件工程师）
多 Agent：Planner + Browser + Terminal + Editor，共享沙箱环境，完成端到端软件任务。

### GitHub Copilot Workspace
Spec → Plan → Implement → Test 四阶段多 Agent 流水线。

### 斯坦福 Generative Agents
25 个虚拟居民的小镇，每个 Agent 有记忆/规划/反思，展现涌现社会行为。论文成为 Agent 研究里程碑。

### ChatDev
模拟软件公司，7 个角色协作开发，开源实现。

### 阿里 AgentScope / 字节 Coze
国内大厂平台，支持低代码搭建多 Agent 应用。

---

## 面试高频问题

**Q1：什么时候用 Multi-Agent，什么时候单 Agent 够用？**

**单 Agent 够用**：任务单一、工具 ≤ 20 个、无明确角色分工、上下文可控。
**需要 Multi-Agent**：
- 任务涉及多个专业领域（开发+设计+测试）
- 工具过多导致 LLM 选择困难
- 需要并行执行
- 需要辩论/反思提升质量
- 模拟组织/流程

**反模式**：简单任务硬拆成多 Agent，反而增加协调成本和延迟。

**Q2：Multi-Agent 主要设计模式有哪些？**

- **Supervisor**：中心化路由，最常用
- **Hierarchical**：多层级组织
- **Pipeline**：固定流程
- **Network**：对等协作
- **Debate**：对立辩论提质量
- **Group Chat**：群聊式自组织

选择依据：任务是否有明确流程、是否需要并行、决策中心化还是分布式。

**Q3：AutoGen、CrewAI、LangGraph、MetaGPT 怎么选？**

- **AutoGen**：研究、原型、对话驱动任务，需要人机协作
- **CrewAI**：业务流水线、上手快、Demo 型项目
- **LangGraph**：生产级 Agent，需要精细控制、可持久化
- **MetaGPT**：代码生成类，有明确工作流的开发任务

企业级生产推荐 LangGraph；快速 Demo 用 CrewAI；学习原理看 AutoGen。

**Q4：Multi-Agent 的主要挑战是什么？**

- **协调开销**：Agent 间沟通消耗 token
- **幻觉放大**：一个 Agent 的错误被下游采信
- **循环/死锁**：Agent 相互等待
- **成本**：多模型 × 多轮 = 高 API 费
- **调试难**：非确定性行为，难复现
- **评估难**：没有单一正确答案

**Q5：如何处理 Agent 间的冲突？**

- **Supervisor 仲裁**：中心节点拍板
- **投票机制**：多 Agent 投票，少数服从多数
- **Debate + Judge**：对立观点 + 第三方裁判
- **用户决定**：Human in the Loop

实际中 Supervisor 模式最常用。

**Q6：Agent 间如何共享记忆？**

- **共享 State**：LangGraph 模式，所有 Agent 读写同一状态对象
- **共享向量库**：各 Agent 对同一 VectorStore 检索
- **消息历史**：GroupChat 中所有消息对所有 Agent 可见
- **Blackboard**：文件系统 / DB 作为共享知识空间
- **私有 + 公共**：每个 Agent 有私有记忆 + 公共黑板

**Q7：如何评估 Multi-Agent 系统？**

- **端到端指标**：任务成功率、完成时间、成本
- **过程指标**：每个 Agent 的准确率、调用次数
- **成本**：Token 消耗、API 费用
- **AgentBench**：标准化评测基准
- **人工评估**：复杂任务仍需人看最终产出
- **A/B**：新架构 vs 旧架构对比

**Q8：如何防止 Agent 无限循环？**

- `max_rounds` / `max_iterations` 硬限制
- 检测到重复状态立即终止
- 每个 Agent 设置独立超时
- Supervisor 检测进展，长时间无推进则强制结束
- 预算控制（token/费用上限）

**Q9：Agent 框架是否该重度依赖？**

权衡：
- **轻度项目**：直接用 CrewAI/AutoGen 最快
- **生产项目**：框架抽象有成本（性能损耗、调试困难、升级风险）

建议：先用框架跑通 PoC，核心业务自己封装薄抽象（基于 OpenAI SDK + 自己的状态机），关键路径避免深度绑定框架内部。

**Q10：Multi-Agent 的未来？**

- **协议标准化**：MCP（Agent↔工具）、A2A（Agent↔Agent）成为标准
- **Agent OS**：操作系统级支持，进程/权限/调度
- **自组织 Agent**：无需人工设计拓扑，任务驱动自动组队
- **长期运行 Agent**：持续几天/几周的任务，需要持久化和监控
- **Physical Agent**：结合机器人、IoT，从数字世界走向物理世界
