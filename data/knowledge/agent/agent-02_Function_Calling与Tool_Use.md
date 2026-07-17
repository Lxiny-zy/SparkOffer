# Function Calling 与 Tool Use 深度

## 1. 什么是 Function Calling

### 定义
Function Calling（函数调用）是大模型将自然语言意图转化为**结构化工具调用**的能力。模型不直接执行函数，而是输出符合 Schema 的 JSON，由应用层解析并执行。

### 为什么重要
- **突破 LLM 的静态局限**：可查询实时数据（天气、股价）、读写数据库、调用 API
- **Agent 的基石**：Agent 的"行动"能力本质就是 Function Calling 的循环
- **结构化输出**：强制模型输出符合 JSON Schema 的内容，替代脆弱的正则解析

### Function Calling vs Tool Use

两者术语常混用，细微区别：
- **Function Calling**（OpenAI 早期叫法）：单次函数调用
- **Tool Use**（Anthropic / 新规范）：更通用的概念，包含函数调用 + 代码执行 + 文件操作等

本文统一称 **Tool Use**，Function Calling 作为其子集。

---

## 2. 核心工作流

### 完整闭环

```
用户问题
   ↓
①  LLM + 工具列表
   ↓
②  LLM 判断是否需要调用工具
   ├─ 不需要：直接回答
   └─ 需要：输出 tool_call (name + arguments)
            ↓
③  应用层解析 tool_call，实际执行函数
            ↓
④  把结果包装成 tool_result 回传给 LLM
            ↓
⑤  LLM 基于结果生成最终回答
   （可能再次触发 tool_call，进入下一轮）
```

### 示意图

```
User:     "北京天气怎么样？"
   │
   ▼
Assistant: [tool_call: get_weather(city="北京")]
   │
   ▼
App:       调用真实 API → "晴，25℃"
   │
   ▼
User(tool): {"temp": 25, "weather": "晴"}
   │
   ▼
Assistant: "北京今天天气晴朗，气温 25℃。"
```

---

## 3. 工具定义（Schema）

### OpenAI 格式

```python
tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的当前天气。支持全球主要城市。",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称，中英文均可，例如 '北京' 或 'Tokyo'"
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "温度单位"
                }
            },
            "required": ["city"]
        }
    }
}]
```

### Anthropic 格式

```python
tools = [{
    "name": "get_weather",
    "description": "查询指定城市的当前天气...",
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
        },
        "required": ["city"]
    }
}]
```

### Schema 设计黄金法则

**1. 描述要足够详尽**
模型完全依赖 description 判断何时调用。错：`"查天气"`；对：`"查询指定城市当前实时天气，包含温度、湿度、风力；适用于用户询问任一城市天气的场景"`。

**2. 参数类型明确**
- 使用 `enum` 限定有限选项（避免模型幻觉值）
- 使用 `pattern` 校验格式（如邮箱、日期）
- 必填用 `required`，可选别列在里面

**3. 工具数量控制**
- ≤ 20 个：模型准确率高
- 20-50 个：准确率下降，需 Prompt 工程辅助
- > 50 个：建议**工具分层/动态路由**

**4. 命名规范**
- 动词开头：`get_`、`create_`、`search_`、`send_`
- 避免歧义：`get_user` → `get_user_by_id`
- 单一职责：`get_user_and_send_email` → 拆成两个

---

## 4. 代码实战（OpenAI）

### 基础调用

```python
from openai import OpenAI
import json

client = OpenAI()

def get_weather(city: str, unit: str = "celsius"):
    # 真实业务
    return {"city": city, "temp": 25, "unit": unit, "weather": "sunny"}

tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的当前天气",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
            },
            "required": ["city"]
        }
    }
}]

messages = [{"role": "user", "content": "北京天气怎么样？"}]

# 第一轮：LLM 决定调用工具
response = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    tools=tools,
    tool_choice="auto"  # auto / none / required / {"type":"function","function":{"name":"xxx"}}
)

msg = response.choices[0].message
messages.append(msg)

# 解析并执行工具
if msg.tool_calls:
    for call in msg.tool_calls:
        name = call.function.name
        args = json.loads(call.function.arguments)
        result = globals()[name](**args)
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(result)
        })

# 第二轮：LLM 基于结果生成回答
final = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    tools=tools
)
print(final.choices[0].message.content)
```

### 多轮 Agent Loop

```python
def run_agent(user_input, max_iterations=10):
    messages = [{"role": "user", "content": user_input}]

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model="gpt-4o", messages=messages, tools=tools
        )
        msg = response.choices[0].message
        messages.append(msg)

        # 终止条件：没有 tool_call，LLM 给出最终答案
        if not msg.tool_calls:
            return msg.content

        # 执行所有工具调用
        for call in msg.tool_calls:
            result = execute_tool(call.function.name, json.loads(call.function.arguments))
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result)
            })

    raise Exception("超出最大迭代次数")
```

---

## 5. 代码实战（Anthropic Claude）

```python
import anthropic

client = anthropic.Anthropic()

tools = [{
    "name": "get_weather",
    "description": "查询指定城市的当前天气",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
    }
}]

messages = [{"role": "user", "content": "北京天气？"}]

response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=1024,
    tools=tools,
    messages=messages
)

# Claude 返回 content blocks
if response.stop_reason == "tool_use":
    for block in response.content:
        if block.type == "tool_use":
            result = execute_tool(block.name, block.input)
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result)
                }]
            })

    # 继续调用
    final = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        tools=tools,
        messages=messages
    )
```

---

## 6. 并行工具调用（Parallel Tool Use）

现代模型（GPT-4o、Claude 3.5+）支持**单轮内并行调用多个工具**：

```python
# 用户："对比北京和上海的天气"
# LLM 单次返回两个 tool_calls
msg.tool_calls = [
    ToolCall(id="1", name="get_weather", args={"city": "北京"}),
    ToolCall(id="2", name="get_weather", args={"city": "上海"})
]

# 应用层可并发执行
import asyncio
async def run_parallel(calls):
    tasks = [execute_tool_async(c.name, c.args) for c in calls]
    return await asyncio.gather(*tasks)
```

**禁用并行**：`parallel_tool_calls=False`（OpenAI）。

---

## 7. 流式（Streaming）工具调用

流式输出时工具调用参数分块返回：

```python
stream = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    tools=tools,
    stream=True
)

tool_calls_buffer = {}
for chunk in stream:
    delta = chunk.choices[0].delta
    if delta.tool_calls:
        for tc in delta.tool_calls:
            idx = tc.index
            if idx not in tool_calls_buffer:
                tool_calls_buffer[idx] = {"id": "", "name": "", "args": ""}
            if tc.id: tool_calls_buffer[idx]["id"] += tc.id
            if tc.function.name: tool_calls_buffer[idx]["name"] += tc.function.name
            if tc.function.arguments: tool_calls_buffer[idx]["args"] += tc.function.arguments

# 最后拼接
for call in tool_calls_buffer.values():
    args = json.loads(call["args"])
    execute_tool(call["name"], args)
```

---

## 8. 结构化输出（Structured Output）

Function Calling 的衍生用法——强制 LLM 返回结构化 JSON：

### JSON Mode
```python
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "提取：张三，25岁，工程师"}],
    response_format={"type": "json_object"}
)
```

### Structured Output（严格 Schema）
```python
from pydantic import BaseModel

class Person(BaseModel):
    name: str
    age: int
    job: str

response = client.beta.chat.completions.parse(
    model="gpt-4o-2024-08-06",
    messages=[{"role": "user", "content": "张三，25岁，工程师"}],
    response_format=Person
)
person: Person = response.choices[0].message.parsed
```

**vs Function Calling**：
- Function Calling：模型决策"是否调用"、"调用哪个"
- Structured Output：强制返回固定 Schema，无决策空间

---

## 9. 进阶：工具编排模式

### 模式一：路由（Router Pattern）

```python
# 先让 LLM 分类，再选择工具子集
router_tools = [{"name": "classify_intent", "..."}]
intent = call_llm(user_input, tools=router_tools)

if intent == "weather":
    tools = weather_tools
elif intent == "shopping":
    tools = shopping_tools
# ...
```

适合工具数 > 30 的场景。

### 模式二：分层工具（Hierarchical Tools）

```python
# Level 1: 粗粒度工具
{"name": "database", "description": "所有数据库操作", 
 "parameters": {"action": {"enum": ["query", "insert", "update"]}}}

# Level 2: 细粒度子工具（按需展开）
```

### 模式三：计划再执行（Plan-and-Execute）

```python
# Step 1: LLM 生成完整计划（Structured Output）
plan = llm_generate_plan(user_input)  # [step1, step2, step3...]

# Step 2: 按计划顺序调用工具
for step in plan:
    result = execute_tool(step.tool, step.args)
    # 可动态 replan
```

### 模式四：ReAct

见 Agent 智能体章节，Thought → Action → Observation 循环。

---

## 10. 常见坑与最佳实践

### 坑 1：模型幻觉参数
**症状**：模型编造不存在的参数值，如 `city="Atlantis"`。
**解法**：
- 用 `enum` 限定值域
- Prompt 明确"如果信息不足请向用户询问"
- 对关键字段做白名单校验，不通过时返回错误让 LLM 修正

### 坑 2：无限循环
**症状**：LLM 反复调用同一工具。
**解法**：
- 设置 `max_iterations`
- 记录调用历史，相同参数二次调用直接返回缓存
- System Prompt 强调"不要重复调用已有结果"

### 坑 3：JSON 解析失败
**症状**：`arguments` 字段不是合法 JSON。
**解法**：
- 用 `tool_choice="required"` 强制调用
- 使用支持严格模式的 API（如 OpenAI `strict: true`）
- 容错：解析失败时回传错误给 LLM 自我修正

### 坑 4：工具描述不准导致误调用
**例**：`get_user_info` 描述写"查用户"，LLM 在用户只说"你好"时也调用。
**解法**：
- 明确**何时调用**与**何时不调用**
- 提供反例：`"仅当用户明确要求查看某用户信息时调用，闲聊不触发"`

### 坑 5：上下文爆炸
**症状**：工具返回数据巨大（如整个数据库表），超过上下文窗口。
**解法**：
- 工具返回分页/截断
- 用 RAG 把大结果存向量库，LLM 再二次查询
- 返回摘要 + URI，需要时再调 `fetch_details`

### 最佳实践清单

1. **工具设计**：单一职责、描述详尽、参数最小化
2. **安全**：敏感操作（删除/付款）必须人工确认
3. **幂等**：工具应幂等，便于重试
4. **可观测**：记录每次 tool_call 的 input/output/latency
5. **超时**：工具调用设超时，避免阻塞 Agent
6. **错误处理**：工具异常也要结构化返回给 LLM，不直接抛
7. **缓存**：相同参数的 idempotent 查询可缓存

---

## 11. Tool Use 与 Function Calling 演进

| 时期 | 技术 | 特点 |
|------|------|------|
| 2022 | ReAct 论文 | 通过 Prompt 约定让 LLM 输出 `Action:` |
| 2023.06 | OpenAI Function Calling | 原生支持，JSON Schema |
| 2023.11 | Assistants API / GPT-4 Turbo | 并行调用 |
| 2024 | Anthropic Tool Use / Claude 3 | 更强推理 + 工具混合 |
| 2024.08 | OpenAI Structured Output | strict 模式，100% 遵守 Schema |
| 2024.11 | MCP 协议发布 | 跨模型、跨应用的工具协议 |
| 2025 | Agentic Loop / Computer Use | 工具扩展到屏幕操作、代码执行 |

---

## 面试高频问题

**Q1：Function Calling 的完整流程？**

5 步闭环：
1. 定义 Tool Schema（name + description + parameters JSON Schema）
2. 随 messages 一起传给 LLM
3. LLM 返回 `tool_call`（name + arguments）或普通文本
4. 应用解析 arguments，调用真实函数，得到 result
5. 把 result 以 `tool` 角色追加到 messages，再调 LLM 生成最终回答

多轮时循环 3-5，直到 LLM 不再返回 tool_call。

**Q2：LLM 没有真的"调用"函数，它做了什么？**

LLM 只输出**意图描述**（JSON 文本），本身不能执行代码。执行发生在应用层：
1. LLM 把"用户想查天气"翻译成 `{"name":"get_weather","arguments":{"city":"北京"}}`
2. 应用代码解析该 JSON，映射到真实的 Python 函数，执行
3. 结果再回传给 LLM 生成自然语言回答

LLM 本质是"自然语言 ↔ 结构化参数"的翻译器。

**Q3：如何避免 LLM 乱调工具？**

- **Schema 里 description 写清楚调用时机和不调用时机**
- `tool_choice` 控制：`auto`（默认）/`none`（禁用）/`required`（强制调用任一）/指定某工具
- 用少量 Few-shot 示例展示何时调用
- System Prompt 加规则："仅当用户明确要求时调用工具"
- 调用失败/无结果时返回清晰错误信息让 LLM 决策是否重试

**Q4：工具很多（50+）怎么办？**

三种方案：
1. **路由模式**：先用一个分类 LLM 选出相关工具子集，再传给主 LLM
2. **RAG over Tools**：把工具描述向量化，按用户 query 检索 top-K
3. **分层工具**：暴露粗粒度工具，LLM 二次选择细粒度

实务：GPT-4 在 ~20 个工具下表现稳定，超过需要优化。

**Q5：工具调用失败如何处理？**

- **可重试错误**（超时、限流）：应用层自动重试（指数退避），不暴露给 LLM
- **参数错误**：结构化返回给 LLM（"city 不能为空"），让 LLM 修正参数重试
- **业务错误**（找不到用户）：原样返回，让 LLM 决策如何回复用户
- **不可恢复错误**：终止 loop，上报给用户

关键：区分**给 LLM 看的错误**（引导修正）和**给用户看的错误**（终止流程）。

**Q6：并行工具调用的风险？**

- **顺序依赖**：后一个工具需要前一个结果时不能并行
- **限流**：并发过多可能触发 API 限流
- **事务**：多个写操作并行时数据一致性问题
- **成本**：同时失败时 Token 和 API 费用浪费

应用层应对模型返回的并行 tool_calls 做**依赖分析**，必要时降级为串行。

**Q7：Function Calling 和 Structured Output 区别？**

| 维度 | Function Calling | Structured Output |
|------|------------------|-------------------|
| 目的 | 触发工具执行 | 仅获取结构化数据 |
| 决策 | LLM 决定是否调用、调用哪个 | 强制一定返回指定结构 |
| 多次 | 可多轮循环 | 单次返回 |
| 场景 | Agent、工具使用 | 数据抽取、表单填充 |

实际上两者底层机制类似（都基于 JSON Schema 约束），OpenAI Structured Output = Function Calling `strict:true` + `tool_choice:required`。

**Q8：如何在 Claude 和 GPT 之间复用工具定义？**

用抽象层：
```python
class Tool:
    def __init__(self, name, description, params, handler):
        self.name, self.description, self.params, self.handler = ...

    def to_openai(self):
        return {"type": "function", "function": {...}}

    def to_anthropic(self):
        return {"name": self.name, "input_schema": {...}}
```

或直接用 LangChain / LlamaIndex 的 Tool 抽象；MCP 则是跨模型的标准方案。

**Q9：如何保证 LLM 输出的 JSON 100% 合法？**

- **OpenAI Structured Output**：`strict: true`，运行时用 grammar 约束解码，保证 Schema 合规
- **Outlines / lm-format-enforcer**：客户端控制解码，强制符合正则/JSON Schema
- **JSON Mode**：只保证是合法 JSON，但不保证 Schema 匹配
- **容错**：仍然需要 try-catch + 失败后让 LLM 重新生成

生产环境建议 strict 模式 + 客户端校验双重保障。

**Q10：Tool Use 的未来趋势？**

- **Agentic Loop 标准化**：MCP、A2A 协议统一 Agent-工具/Agent-Agent 交互
- **Computer Use**：工具从 API 扩展到屏幕操作（Anthropic Computer Use）
- **Tool Learning**：模型自动学习新工具，无需重新训练
- **长上下文 + 工具**：100 万 token 上下文让一次对话能用更多工具
- **工具市场**：类似 App Store，开发者发布可组合的 MCP Server
