# Part 2 · 02 — Agent 编排与 LangGraph

> 本章讲清三件事：**LangGraph 状态机**怎么编排一场"会自然推进"的模拟面试、**LangChain** 如何抽象 LLM 调用、以及浮窗助手的 **Function Calling Agent** 怎么用多轮工具循环干活。最后辨析「单 Agent vs ReAct vs Multi-Agent」——这是 Agent 岗位的高频考点。

---

## 0. 先建立坐标系：这个项目里有几种"Agent 形态"？

很多人一上来就把所有 LLM 应用叫"Agent"，面试时一追问就露馅。SparkOffer 里其实有**三种不同的编排形态**，要分清：

| 形态 | 代表模块 | 本质 | 控制流由谁决定 |
|---|---|---|---|
| **状态机（State Machine）** | `graphs/resume_interview.py` | 预定义阶段 + 条件转移 | **开发者写死的图** + LLM 提供"是否推进"的信号 |
| **流水线（Pipeline）** | `graphs/drill_pipeline.py` | 固定 5 阶段顺序执行 | **完全确定性**，LLM 只在 generate 阶段产出内容 |
| **工具调用 Agent（Tool-Use Agent）** | `assistant.py` | LLM 自主决定调哪些工具、调几轮 | **LLM 决定**（在开发者给的工具集内） |

> **一句话**：状态机和流水线是"开发者掌舵、LLM 划桨"；只有浮窗助手是"LLM 掌舵"。这正是 Agent 自主性的分水岭。

---

## 1. LangGraph 基础：用图来编排有状态的 LLM 工作流

### 1.1 是什么

**LangGraph** 是 LangChain 团队出的**有状态、可持久化的多步 LLM 编排框架**。核心抽象：

- **State（状态）**：一个贯穿整张图的共享数据结构（通常是 `TypedDict`）。每个节点读它、返回"对它的增量更新"。
- **Node（节点）**：一个普通函数 `state -> dict`，返回的 dict 会被**合并**进 State。
- **Edge（边）**：节点之间的连接。普通边是无条件跳转；**条件边（conditional edge）**根据一个路由函数的返回值选择下一个节点。
- **START / END**：图的虚拟入口和出口。
- **compile()**：把图编译成可执行对象，可挂 `checkpointer`（持久化）和 `interrupt_before/after`（中断点）。

### 1.2 为什么需要它（而不是自己写 if-else + while 循环）

裸写 LLM 多步流程会遇到三个痛点，LangGraph 各给一个答案：

| 痛点 | 裸写的样子 | LangGraph 的答案 |
|---|---|---|
| 状态散落 | 一堆局部变量在函数间传来传去 | 统一的 State，节点只声明"我改了哪几个字段" |
| 流程不可视、难改 | 嵌套 if-else，加一个阶段要动多处 | 图结构声明式，加节点/边互不影响 |
| 中断与恢复 | 自己存档/读档，重启即丢 | Checkpointer 自动按 `thread_id` 存档，`interrupt_before` 天然支持"等用户输入" |

### 1.3 本项目怎么用——简历模拟面试的状态机

看 `compile_resume_interview`（`resume_interview.py:263-287`）这张图：

```python
graph = StateGraph(ResumeInterviewState)        # State 是 TypedDict
graph.add_node("init",    _make_init_interview(user_id))   # 载入简历/知识库, 生成开场白
graph.add_node("ask",     _make_interviewer_ask(user_id))  # 生成下一个问题
graph.add_node("advance", advance_phase)                   # 推进到下一阶段
graph.add_node("wait",    wait_for_answer)                 # 占位节点：图在这里暂停等用户

graph.add_edge(START, "init")
graph.add_edge("init", "wait")
graph.add_edge("ask",  "wait")
graph.add_edge("advance", "ask")

graph.add_conditional_edges("wait", route_after_answer, {  # 条件边：核心路由
    "ask":     "ask",       # 继续在本阶段追问
    "advance": "advance",   # 进入下一阶段
    "end":     END,         # 结束面试
})

return graph.compile(
    checkpointer=get_checkpointer(),    # 持久化（见 §2）
    interrupt_before=["wait"],          # 在 wait 节点前中断（见 §3）
)
```

对应的状态流转：

```
START → init → wait ──route_after_answer──┬─→ ask → wait …（同阶段追问）
                                          ├─→ advance → ask → wait …（换阶段）
                                          └─→ END（反问结束）
```

**5 个阶段**定义在 `PHASE_ORDER`（`resume_interview.py:46-52`）：
`GREETING`（开场）→ `SELF_INTRO`（自我介绍）→ `TECHNICAL`（技术问答）→ `PROJECT_DEEP_DIVE`（项目深挖）→ `REVERSE_QA`（反问）。

### 1.4 设计权衡

- **为什么用一个 `wait` 占位节点而不是直接中断在 `ask`？** 因为"生成问题"和"等待回答"是两件事：`ask` 负责产出，`wait` 是一个**纯停靠点**（函数体 `return {}`），把"中断语义"和"业务语义"解耦。条件路由也都挂在 `wait` 上，逻辑集中。
- **为什么节点工厂 `_make_xxx(user_id)`？** 因为 LangGraph 节点签名固定是 `state -> dict`，但每个节点要知道当前用户。用闭包把 `user_id` 绑进去，是在不污染 State 的前提下注入依赖的常见手法。

---

## 2. Checkpointer：让面试"重启可续接"

### 2.1 是什么

**Checkpointer** 是 LangGraph 的状态持久化机制。每执行完一个 super-step，它把整个 State 快照存下来，键是 `thread_id`。下次用同一个 `thread_id` 调图，就从上次的快照继续。

### 2.2 本项目怎么实现

`graphs/checkpointer.py` 用进程级单例的 `SqliteSaver`：

```python
conn = sqlite3.connect(str(settings.checkpoint_db_path),
                       check_same_thread=False)   # invoke() 跑在 to_thread 工作线程
conn.execute("PRAGMA journal_mode=WAL")            # 读写并发
conn.execute("PRAGMA busy_timeout=5000")           # 锁等待 5s
saver = SqliteSaver(conn); saver.setup()
```

- `thread_id` = 面试 `session_id`，状态落在**独立的** `data/checkpoints.db`（与业务库 `interviews.db` 分开，避免锁争用）。
- `check_same_thread=False`：因为图的 `invoke()` 被 `asyncio.to_thread` 丢到工作线程跑（见 06 章流式），SQLite 连接要允许跨线程。
- 注释点出关键约束：`SqliteSaver` 内部有锁串行化读写，在本项目"低续接并发"下单连接是安全的。

### 2.3 价值与权衡

- **价值**：用了持久化 checkpointer，**进程重启 / 多 worker** 都不丢面试进度——这是从 `MemorySaver`（内存版，重启即丢）升级来的。
- **权衡**：SQLite 不适合高并发写。本项目面试续接是低频操作，单库单连接够用；若要横向扩展，会换 PostgresSaver。**面试时主动说出这个边界，比假装"能扛高并发"更稳。**

---

## 3. `interrupt_before`：人在环（Human-in-the-Loop）

### 3.1 是什么

`interrupt_before=["wait"]` 告诉 LangGraph：**执行到 `wait` 节点之前停下来**，把控制权交还调用方。这就是 LLM Agent 领域常说的 **Human-in-the-Loop（HITL）**——图跑到需要人介入的点暂停，等外部输入后再 `invoke` 继续。

### 3.2 为什么需要

模拟面试本质是"问一句、等一句"。没有中断机制的话，图会一口气把 5 个阶段跑完，根本没机会让用户回答。`interrupt_before` 让"等用户回答"成为图的一等公民：

```
图跑到 wait 前 → 暂停、落 checkpoint → API 把"面试官的问题"返回前端
                                       ↓ 用户回答，前端再次请求
图从 checkpoint 恢复 → 注入用户回答 → route_after_answer 决定下一步
```

### 3.3 权衡

也可以不用图的中断、自己在外面写循环，但那样就要手动管理"问到第几阶段、问了几题"等状态——又退回到 §1.2 的痛点。用 `interrupt_before` + checkpointer，状态管理和中断恢复都交给框架，这是 LangGraph 相比裸写最大的价值点之一。

---

## 4. 隐藏 EVAL 标记：让 LLM 自评、驱动阶段推进

### 4.1 问题背景

状态机要决定"这个阶段问够了没、该不该进下一阶段"。最笨的办法是固定每阶段问 N 题。但面试是动态的——候选人答得好可以早点深入，答得卡需要多问几句。怎么让"推进决策"既智能又可控？

### 4.2 本项目的解法：隐藏自评标记

让面试官在每条回复的**末尾**输出一段对用户**不可见**的自评 JSON：

```
（面试官可见的问题正文……）
<!--EVAL:{"score": 7, "should_advance": true, "reason": "已充分考察 Python 并发"}-->
```

后端用正则把它抠出来（`_parse_inline_eval`, `resume_interview.py:60-93`），关键防御：

- **取最后一个 EVAL 标记**：`matches[-1]`。因为模型可能在答案中间复述一个示例标记，真正的自评永远在最后一行。
- **分数夹紧 + 类型校验**：`score` 只保留 0-10 的数值，否则**丢弃该字段**（`isinstance(score, bool)` 也排除——`True` 在 Python 里是 `int` 子类，不校验会被当成 1 分）。这样**被注入或畸形的标记污染不了画像**。
- 把可见正文（`clean`）和自评（`eval_data`）分离返回。

### 4.3 三重护栏：防止死循环又不牺牲灵活性

`route_after_answer`（`resume_interview.py:203-236`）的推进逻辑是三层兜底：

```python
if count >= HARD_MAX_PER_PHASE:        # 1) 硬上限：每阶段最多 10 题，绝不超
    return "advance"
# 简单阶段用计数规则
if phase == "greeting" and count >= 1:     return "advance"
if phase == "self_intro" and count >= 2:   return "advance"
if phase == "reverse_qa" and count >= 2:   return "end"
# 技术 / 项目深挖阶段：eval 驱动 + 计数兜底
if phase in ("technical", "project_deep_dive"):
    if count >= 2 and last_eval and last_eval.get("should_advance"):  # 2) eval 驱动
        return "advance"
    if count >= settings.max_questions_per_phase:                      # 3) 计数兜底
        return "advance"
return "ask"
```

| 护栏 | 作用 | 为什么需要 |
|---|---|---|
| 硬上限 10 | 无论 eval 说什么都强制推进 | LLM 可能永远 `should_advance=false`，死循环兜底 |
| eval 驱动（≥2 题 + should_advance） | 答得好就早推进 | 灵活、自然 |
| 计数兜底（≥max_questions_per_phase=5） | eval 一直不给推进信号时 | 防止某阶段无限追问 |

> **面试金句**：「我让 LLM 出'是否推进'的*建议*，但*决策权*在确定性的护栏里——LLM 负责判断，代码负责兜底。这样既有智能又不会失控。」

---

## 5. Prompt 前缀缓存优化：把"固定内容"冻在最前面

### 5.1 是什么 / 为什么

很多 LLM 网关支持 **prompt prefix caching**：如果两次请求的**前缀字节完全一致**，前缀部分的计算/计费可以命中缓存，省钱省延迟。要吃到这个红利，就得让"跨轮不变的内容"在 prompt 里**逐字节稳定**。

### 5.2 本项目怎么做

`_make_init_interview` 在面试**开始时一次性**构造稳定系统前缀（简历 + 知识库 + 长期画像），存进 `state["system_prompt"]`；之后每一轮 `interviewer_ask` 都复用这个**冻结的前缀**，把"每轮会变的内容（当前阶段 + 已问问题列表）"放到一条**尾部** `SystemMessage` 里（`RESUME_TURN_CONTEXT`）：

```python
messages = [SystemMessage(content=stable)] + kept + [SystemMessage(content=turn_ctx)]
#            ↑ 跨轮逐字节不变（命中前缀缓存）        ↑ 每轮才变的放最后
```

同时做**历史预算**：保留尽量多的最近轮次，但要给稳定前缀和 turn 上下文留出 token 空间（`resolve_input_budget()`，见 06 章）。

### 5.3 权衡

代价是把"阶段/已问题"从系统前缀挪到尾部，prompt 结构稍微绕一点；收益是**前缀稳定 → 缓存命中 → 多轮面试整体更快更省**。这是个典型的"为可观测的工程指标做的微观优化"。

---

## 6. LangChain：LLM 调用的统一抽象

### 6.1 Message 抽象

LangChain 把对话抽象成消息序列：`SystemMessage`（系统指令）/ `HumanMessage`（用户）/ `AIMessage`（模型）/ `ToolMessage`（工具结果）。本项目所有 LLM 调用都用这套，好处是**与具体厂商解耦**——底层换 OpenAI / DeepSeek / 通义都一样。

### 6.2 三种调用方式

| 方法 | 用途 | 本项目用在哪 |
|---|---|---|
| `invoke(messages)` | 同步一次性调用 | LangGraph 节点内（图本身跑在线程里） |
| `ainvoke(messages)` | 异步一次性调用 | 画像更新、评估、修复等异步路径 |
| `astream(messages)` | 异步流式（逐 chunk） | 所有 SSE 出题/答题/助手 |

### 6.3 Function Calling / `bind_tools`

`llm.bind_tools(tools)` 把工具的 JSON Schema 绑定到模型上，模型就能在回复里产出 `tool_calls`（要调哪个工具、参数是什么）。这是 OpenAI **Function Calling** 的 LangChain 封装。本项目浮窗助手用它（见 §7）。

> **辨析（高频考点）**：Function Calling 和 ReAct 都能实现"工具调用"，但路线不同——
> - **Function Calling**：模型原生支持，结构化输出 `tool_calls`，**可靠、易解析**，但依赖模型/网关支持。
> - **ReAct（Reason+Act）**：纯 prompt 工程，让模型按 `Thought/Action/Observation` 格式输出，再用正则解析。**不依赖原生能力**，但解析脆、易跑偏。
> 本项目选 Function Calling（`bind_tools`），因为后端统一走 OpenAI 兼容端点，原生支持，省去解析脆弱性。

---

## 7. 浮窗助手：Function Calling Agent（项目里唯一"LLM 掌舵"的部分）

### 7.1 是什么

`assistant.py` 是一个**单 Agent + 多工具**的助手：用户问"我最近 Python 练得怎么样"，它自己决定去调 `get_score_trends` + `get_weak_points_detail`，拿到数据后组织回答；问"带我去做算法题"，它调 `navigate` 工具让前端跳转。

### 7.2 18 个工具（`assistant.py:97-` 的 `TOOLS`）

按用途分三类：

| 类别 | 工具 | 干什么 |
|---|---|---|
| **前端动作** | `navigate`、`start_interview` | 返回 `action` 事件让前端跳转/开始训练 |
| **画像/数据查询** | `check_profile`、`get_full_profile`、`get_weak_points_detail`、`get_score_trends`、`get_training_stats`、`get_due_reviews`、`list_trained_topics`、`list_topics` | 读用户画像/掌握度/复习计划 |
| **历史/收藏/知识检索** | `search_history`、`get_session_detail`、`get_session_transcript`、`list_favorites`、`search_favorites_detail`、`search_algorithm_cards`、`search_knowledge_memory`、`query_knowledge_base` | 检索会话/收藏/算法卡/知识库 |

### 7.3 多轮工具循环（Agent Loop）

核心循环（`assistant.py:~770-887`）：

```
while 轮次 < 上限:
    流式调用 llm.bind_tools(TOOLS).astream(messages)
    ├─ 若本轮没有 tool_calls → 直接把回答 token 流给前端, done, 退出
    └─ 若有 tool_calls：
         把 assistant 消息（含 tool_calls）追加进 messages
         并发执行所有工具（asyncio.gather, return_exceptions=True）
         把每个工具结果作为 role="tool" 消息追加回 messages
         （前端动作类结果额外推一个 action 事件）
    进入下一轮 ← LLM 看到工具结果后继续
```

几个工程细节值得讲：

- **工具并发执行**：一轮里多个 `tool_call` 用 `asyncio.gather` 并发——它们都是独立的异步 DB/向量读取，无共享可变状态，**墙钟时间从"各延迟之和"降到"取最大值"**。结果按原顺序消费，保证 `tool_call_id` 对齐、`action` 事件顺序确定。
- **单工具失败不炸整轮**：`return_exceptions=True`，某个工具抛错（比如 LLM 给的参数缺字段）转成错误结果继续，前端仍能收到 `done`。
- **工具执行期间推 ping**：单次知识库查询最长 60s，多工具叠加更久，期间用 15s 轮询推 `ping` 防代理掐断空闲连接。
- **轮次上限兜底**：`while/else` 结构，超过最大轮数给个"操作完成"兜底，绝不无限调工具。

### 7.4 附带的轻量个性化：无 LLM 的偏好提取

用户说"回答简洁点""难度提高""重点练 Redis"这类话时，`_extract_and_update_preferences`（`assistant.py:922`）用**纯正则**匹配写进画像 `preferences`（response_style / preferred_difficulty / interview_pace / feedback_style / focus_topics）。**不调 LLM**——这种确定性偏好抽取没必要花一次模型调用。

---

## 8. 终极辨析：单 Agent vs ReAct vs Multi-Agent

这是 Agent 岗位**必问**。诚实且有深度的回答框架：

| 维度 | 单 Agent + Tool Use（本项目） | ReAct | Multi-Agent（AutoGen / CrewAI） |
|---|---|---|---|
| 控制流 | LLM 在固定工具集内决策，主程序管轮次 | LLM 用 Thought/Action 文本驱动 | 多个角色 Agent 互相对话/分工 |
| 适用 | 工具明确、任务边界清晰 | 需要显式推理链、模型不支持原生 FC | 任务需要分工协作/辩论/审校 |
| 代价 | 简单、可控、好调试 | 解析脆弱、token 多 | 复杂、token 爆炸、难调试、易发散 |

**关于本项目的诚实表述**：
> 「SparkOffer 目前是**单 Agent + Function Calling**（浮窗助手）和**状态机**（简历面试）的组合，**没有引入 multi-agent 协作**。因为我的任务边界清晰——查画像、检索知识、跳页面，单 Agent 多工具就够了。引入 multi-agent 会带来 token 成本和发散风险，性价比不划算。但我理解 ReAct 的推理-行动循环、以及 AutoGen/CrewAI 这类多 Agent 框架的'角色分工+对话协作'范式，知道在什么场景（如需要写作-审校对抗、复杂研究分解）才值得上。」

这个回答比硬吹"我做了 multi-agent"高级得多——它证明你**懂取舍**，而取舍意识正是 Agent 工程师的核心素养。

---

## 本章小结

- LangGraph 用**图 + State + 条件边**把"会自然推进的面试"编排成可持久化、可中断恢复的状态机。
- **隐藏 EVAL 标记 + 三重护栏**让 LLM 提供推进建议、代码保留决策权——智能与可控兼得。
- **Checkpointer + `interrupt_before`** 实现重启可续接的人在环。
- 浮窗助手是项目里唯一"LLM 掌舵"的 **Function Calling Agent**，多轮工具循环 + 并发执行 + 失败隔离。
- 能清晰辨析「单 Agent / ReAct / Multi-Agent」的取舍，是这一章最值钱的产出。

➡️ 下一章：[03_RAG检索增强.md](03_RAG检索增强.md)——RRF 多路召回、语义去重、Cross-Encoder 重排、以及"先修尺子"的 RAG 评测观。
