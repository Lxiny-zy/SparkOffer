# Tool 设计与 Function Calling 工程实战

Tool（工具）是 Agent 与真实世界交互的桥梁。Function Calling 把 LLM 的"自然语言意图"翻译成"结构化的函数调用"。Tool 设计的质量直接决定 Agent 的可靠性、可维护性、安全边界。

## 1. Function Calling 的本质

LLM 通过 fine-tuning 学会输出特定结构的 JSON（OpenAI 格式 / Anthropic tool_use / Gemini function_call）。框架解析这些 JSON 并真正执行函数，再把结果回喂给 LLM 继续生成。**本质是「LLM 输出受约束的结构化文本」**。

```python
tools = [
  {
    "type": "function",
    "function": {
      "name": "search_orders",
      "description": "Query orders by user_id within date range",
      "parameters": {
        "type": "object",
        "properties": {
          "user_id": {"type": "string"},
          "start_date": {"type": "string", "format": "date"},
          "end_date": {"type": "string", "format": "date"},
        },
        "required": ["user_id"],
      },
    },
  }
]
resp = client.chat.completions.create(model="gpt-4o", messages=msgs, tools=tools)
```

## 2. Tool Schema 设计原则

### 2.1 描述清晰、可消歧

LLM 仅靠 description 决定何时调用。坏 description 会让 LLM 误调或漏调。

**反例**：
```json
{"name": "get_data", "description": "Get data"}
```

**正例**：
```json
{
  "name": "get_user_orders",
  "description": "Retrieve a user's order history. Use when the user asks about past purchases, refunds, or order status. Don't use for real-time tracking — use track_shipment instead."
}
```

### 2.2 参数最小化

每多一个参数 LLM 就多一个出错维度。优先：
- 必填字段最少（用合理默认值）
- 枚举代替自由文本（`status: "pending" | "shipped" | "delivered"`）
- 复杂对象拆成多个独立 tool

### 2.3 显式类型 + 约束

JSON Schema 完整表达约束：
```json
{
  "type": "object",
  "properties": {
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    "category": {"type": "string", "enum": ["electronics", "books", "clothing"]},
    "email": {"type": "string", "format": "email"},
  },
  "required": ["category"],
  "additionalProperties": false  // 防止 LLM 幻觉出多余字段
}
```

## 3. 工具执行的鲁棒性

### 3.1 参数校验

LLM 经常给出不合规参数（缺字段、类型错、越界）。**绝不能信任 LLM 的输出**，进函数第一行就用 Pydantic 校验：

```python
from pydantic import BaseModel, ValidationError, Field

class OrderQuery(BaseModel):
    user_id: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)

def search_orders(**kwargs):
    try:
        params = OrderQuery(**kwargs)
    except ValidationError as e:
        return {"error": "invalid_params", "detail": e.errors()}
    return db.query_orders(params.user_id, limit=params.limit)
```

### 3.2 错误回喂格式

错误也要回喂给 LLM 让它纠正，但格式要让 LLM 易理解：

```python
{"role": "tool", "tool_call_id": tc.id, "content": json.dumps({
  "error": "user_not_found",
  "message": "user_id 'xyz' does not exist",
  "suggestion": "Ask the user to verify their account ID"
})}
```

LLM 看到 suggestion 字段会按建议调整。

### 3.3 幂等性

LLM 可能重复调用同一 tool（特别是流式中断恢复）。修改类 tool 必须支持幂等：
- 写入操作带 idempotency_key（来自 tool_call_id）
- 删除前先查存在
- 状态切换检查当前状态是否已是目标状态

## 4. 工具集合管理

### 4.1 数量控制

OpenAI 实测：超过 ~20 个 tool 后准确率显著下降。策略：
- **Tool 路由**：先用一个 router tool 判断类别，再加载对应子集
- **MCP Server 拆分**：按业务域拆服务，每个服务暴露 5-10 个 tool
- **动态绑定**：根据对话历史只暴露相关 tool

### 4.2 命名空间

```python
tools = [
  {"name": "user_get_profile", ...},
  {"name": "user_update_profile", ...},
  {"name": "order_create", ...},
  {"name": "order_cancel", ...},
]
```

前缀让 LLM 一眼看出归属，也方便日志分析与权限控制。

## 5. 多工具编排模式

### 5.1 并行调用

GPT-4o 等支持单轮返回多个 tool_call，应并行执行：

```python
async def execute_tools(tool_calls):
    return await asyncio.gather(*[
        run_tool(tc.function.name, tc.function.arguments) for tc in tool_calls
    ])
```

### 5.2 ReAct 循环

经典模式：Reason → Act → Observe → Reason。LangGraph 一行实现：

```python
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(llm, tools=tools, checkpointer=saver)
```

### 5.3 Plan-and-Execute

复杂任务先让 LLM 出 plan（DAG），再按 plan 执行。优势：节省 token、可视化、可中断。

## 6. 安全边界

### 6.1 权限控制

每个 tool 调用都要校验当前用户对资源的权限：

```python
def get_user_orders(user_id, *, current_user):
    if current_user.id != user_id and not current_user.is_admin:
        raise PermissionError("cannot access other user's orders")
```

### 6.2 危险操作二次确认

删库、转账、发邮件这类不可逆操作**必须接 human-in-the-loop**：

```python
graph.compile(checkpointer=saver, interrupt_before=["delete_database", "transfer_money"])
```

### 6.3 沙箱执行

允许执行用户提供代码的 tool（code_interpreter）必须在沙箱：
- Docker container（无 host 网络、只读 root、内存/CPU 限额）
- WebAssembly runtime（更轻量，pyodide / wasmtime）
- E2B / Modal 等托管沙箱服务

## 7. 性能与成本

### 7.1 工具结果裁剪

数据库返回 1000 行直接给 LLM = 烧钱 + 超 context。策略：
- top-K 截断（按时间/相关性）
- 字段裁剪（只保留 LLM 需要的）
- 摘要再喂（先用小模型 summarize）

### 7.2 缓存

确定性 tool（汇率查询、商品信息）的结果按参数哈希缓存。Anthropic 的 prompt caching 也能让重复 tool schema 的 prompt 部分免费复用。

## 8. 高频面试题

**Q1：Function Calling 跟 OpenAPI 的关系？**
Function Calling 的 schema 本质上是 JSON Schema 子集，与 OpenAPI 的 parameter 定义同源。可以直接把 OpenAPI 接口 description 转成 tool schema（langchain-openapi-tools 实现了这一映射）。

**Q2：LLM 不调 tool 直接编答案怎么办？**
三个手段：① 提示工程明确"必须调用 tool"；② 用 `tool_choice="required"` 强制；③ 后处理校验 response 是否包含 tool_calls，否则重试。

**Q3：MCP 跟 Function Calling 区别？**
MCP（Model Context Protocol）是 Anthropic 定义的 server-client 协议，把 tool 抽象到独立进程，Agent 通过 stdio/HTTP 调 MCP server。优点：tool 可被多 Agent 共享、跨语言、生态化。Function Calling 是 LLM 端能力，MCP 是工程协议，互补关系。

**Q4：怎么给 Agent 加新 tool 不重新部署？**
MCP server 支持热加载；或在 framework 层把 tool registry 做成动态注册（Redis 存 schema，Agent 启动时拉取）；或用 plugin 机制（按目录扫描 .py 自动注册）。

**Q5：tool 调用失败重试策略？**
区分错误类型：
- 5xx / 网络超时 → 指数退避重试
- 4xx 参数错误 → 把错误回喂给 LLM 让它修正参数后重试
- 鉴权 / 配额 → 不重试，直接报错给用户

**Q6：怎么评估 tool 设计好坏？**
观测三个指标：① tool_call 准确率（该调用时调用了吗）；② 参数正确率（schema 校验通过率）；③ 单任务平均 tool 调用次数（应符合理论最小值，过多说明设计冗余）。
