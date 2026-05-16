# Agent 生产部署：推理优化、模型路由、成本控制

实验室 demo 跑得好不算事，能稳定承接生产流量、成本可控、体验合格才是工程能力。本章覆盖 Agent 上线后的核心运维主题。

## 1. 部署架构

### 1.1 典型分层

```
[Web/Mobile Client]
       ↓ (SSE / WebSocket)
[API Gateway / Load Balancer]
       ↓
[FastAPI Workers (Stateless)]
       ↓                     ↓
[LLM Provider]          [Vector Store]   [Cache]   [Tools / MCP Servers]
       ↓
[Observability (Langfuse / OTel)]
```

### 1.2 关键决策

- **同步 vs 异步**：长任务用 task queue（Celery / Arq）+ webhook，短任务直接 HTTP / SSE
- **有状态 vs 无状态**：Agent worker 无状态（state 进 Postgres / Redis）才能水平扩展
- **多区域**：LLM provider 延迟敏感时部署多区域 + 就近路由

## 2. 推理优化

### 2.1 模型选择

不同任务用不同模型：

| 任务 | 推荐 | 备选 |
|---|---|---|
| 复杂推理 / 代码 | Opus 4.7 / GPT-5 | Sonnet 4.6 |
| 通用对话 / Tool use | Sonnet 4.6 / GPT-4o | Haiku 4.5 |
| 分类 / 提取 / 短文本 | Haiku 4.5 / GPT-4.1-mini | Llama 3.3 70B |
| 大规模批处理 | 自部署开源 | API 限流场景 |

**经验**：业务上线先用顶级模型保证质量，跑通后通过路由 / 蒸馏降级。

### 2.2 自部署优化

要求自部署（数据合规 / 成本极致优化）：

- **量化**：FP16 → INT8 / INT4，显存砍半 / 四分之一
- **批处理（Continuous Batching）**：vLLM / TGI / TensorRT-LLM 支持
- **PagedAttention**：vLLM 内置，KV cache 利用率拉满
- **Speculative Decoding**：小模型先猜，大模型验证，2-3x 加速
- **FlashAttention**：注意力计算优化，长序列收益大

vLLM 是当前事实标准。

### 2.3 Prompt Caching

Anthropic / OpenAI / Gemini 都支持。重复的 system prompt + tool schema 部分被缓存，按 0.1x 价格收费。

```python
client.messages.create(
    model="claude-sonnet-4-5",
    system=[{
        "type": "text",
        "text": LONG_SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"}  # 5min TTL
    }],
    tools=[{
        "name": "search",
        "description": LONG_DESCRIPTION,
        "cache_control": {"type": "ephemeral"}
    }],
    messages=[...],
)
```

实测能省 40-80% 输入成本。

### 2.4 流式输出

用户感知延迟主要是 TTFT（Time to First Token）。开 streaming 让用户立刻看到响应，体验从"卡"变成"流畅"。

```python
async def chat_stream(request):
    async def gen():
        async for event in agent.astream_events(input_, version="v2"):
            if event["event"] == "on_chat_model_stream":
                yield f"data: {event['data']['chunk'].content}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

## 3. 成本控制

### 3.1 成本结构分析

每次 Agent 调用成本来源：
- LLM tokens（input + output）
- Embedding API（RAG）
- Tool 后端调用（自家 API / 第三方 SaaS）
- 基础设施（compute、网络、存储）

**先 profile 找大头再优化**。多数情况 LLM 占 80%+。

### 3.2 模型路由

按难度自动选模型。

```python
class ModelRouter:
    def route(self, query):
        complexity = classifier.predict(query)
        if complexity < 0.3:
            return "haiku-4.5"
        elif complexity < 0.7:
            return "sonnet-4.6"
        else:
            return "opus-4.7"
```

或用 LLM 自己路由（一个 mini 模型先判断难度）。生产案例显示能省 60-80% 成本而准确率几乎不降。

### 3.3 Caching 策略全景

| 缓存层 | 命中粒度 | 例子 |
|---|---|---|
| Prompt cache | LLM provider 端复用 prefix | Anthropic ephemeral cache |
| Embedding cache | Query 完全相同 | Redis hash by query |
| Result cache | (query + context) 完全相同 | 客服 FAQ |
| Semantic cache | Query 语义相似 | GPTCache |
| Tool result cache | tool + 参数完全相同 | 汇率 / 商品信息 |

### 3.4 成本预算与熔断

```python
class CostGuard:
    def check(self, user_id, estimated_cost):
        spent = redis.get(f"cost:{user_id}:today") or 0
        if spent + estimated_cost > USER_DAILY_LIMIT:
            raise BudgetExceeded()
```

异常用户 / 死循环 Agent 一旦超预算自动熔断，保护整体成本。

## 4. 延迟优化

### 4.1 关键路径

```
Total Latency = Network + Auth + RAG retrieve + LLM TTFT + Tool calls + LLM completion + Stream flush
```

每段都要 profile，瓶颈通常是 LLM completion 和 Tool。

### 4.2 减少 LLM 调用

- 一次性 prompt 解决多任务（合并请求）
- Function Calling 一轮多工具并行调用
- Plan-and-Execute 提前规划，避免 ReAct 多轮

### 4.3 异步 + 并行

任何独立工作（多 RAG 查询、多 tool 调用）必须并行。LangGraph 的并行节点 + asyncio.gather。

### 4.4 边缘缓存

Cloudflare Workers / Vercel Edge 部署 RAG 检索 / 简单 Q&A，把延迟从 100ms 降到 10ms。

## 5. 可靠性

### 5.1 LLM Provider 限流应对

- 多 provider fallback（OpenAI 不行切 Anthropic 切 Gemini）
- 多 API key 轮询
- 指数退避 + 抖动重试
- 降级到缓存 / 模板回答

```python
class LLMRouter:
    providers = [openai_client, anthropic_client, gemini_client]
    def invoke(self, messages):
        for p in self.providers:
            try:
                return p.invoke(messages)
            except (RateLimitError, ServerError) as e:
                logger.warning(f"{p} failed: {e}, trying next")
        raise RuntimeError("All providers failed")
```

### 5.2 幂等性

用户 retry 不应导致重复扣款 / 发邮件。每个写操作带 idempotency_key。

### 5.3 优雅降级

- LLM 全挂：降级到 RAG 直出 / 模板回答
- 向量库挂：禁用 RAG，只用 LLM 通用知识
- 第三方 API 挂：返回缓存数据 + 标记"信息可能过期"

### 5.4 健康检查

`/healthz` 检查关键依赖（LLM、Vector Store、DB）。Liveness vs Readiness 区分。

## 6. 扩展性

### 6.1 水平扩展

API 层 stateless → 按 CPU / RAM / QPS 自动扩缩容。Vector store 用 managed service（Pinecone / Qdrant Cloud）省心。

### 6.2 队列削峰

突发流量用 message queue 缓冲。同步请求直接响应，异步任务进队列由 worker 消费。

### 6.3 多租户隔离

- 数据：vector namespace、DB row level security
- 配额：每个 tenant 独立限流
- 模型：按 tenant 自定义 system prompt / tools

## 7. 数据与版本

### 7.1 Prompt 版本化

prompt 改了别直接改代码字符串。用配置中心 / DB 存版本，运行时拉取，支持 A/B + 回滚。

```python
prompts = PromptStore()
system = prompts.get("customer_service.system", version="v3")
```

### 7.2 模型版本固定

LLM provider 版本会迭代（gpt-4o → gpt-4o-2024-11-xx）。生产 pin 具体版本号，避免静默回归。

### 7.3 评估集随产品演进

新功能上线前先扩评估集，回归测试通过才发版。

## 8. 安全与合规

参见《Agent 安全与沙箱》一节。生产部署需额外关注：
- 网络隔离（VPC、IP 白名单）
- 审计日志（不可篡改、N 年保留）
- 灾备（多区域、定期恢复演练）
- 数据加密（at rest + in transit）
- 渗透测试 / 红队演练

## 9. 团队协作

### 9.1 Prompt 工程师与开发的协作

- Prompt 用配置存（不在代码里）
- 提供 sandbox 环境让 prompt 工程师独立迭代
- 改 prompt 走 PR review + 评估集回归

### 9.2 文档

- README：架构概览
- Runbook：on-call 处置流程（LLM 全挂、成本暴涨、用户投诉）
- Postmortem：每次故障复盘归档

## 10. 高频面试题

**Q1：Agent 系统的成本怎么控制？**
四层：① 模型路由（小模型处理简单任务）；② Prompt cache + 各级缓存；③ 减少 LLM 调用次数（合并 prompt / 并行 tool）；④ 用户 / 任务级预算 + 熔断。监控成本/任务，异常告警。

**Q2：Agent 系统的延迟瓶颈在哪？**
通常是 LLM completion（输出 token 数 × 单 token 延迟）。优化：① 缩短输出（精简 prompt 要求）；② 流式输出降 TTFT 感知；③ 多模型路由（简单任务 Haiku）；④ Tool 并行；⑤ Prompt cache 提升 TTFT。

**Q3：怎么应对 LLM provider 限流？**
- 多 provider fallback
- 多 key 轮询
- Exponential backoff 重试
- 长任务用队列削峰
- 真不行降级到缓存 / 模板

**Q4：自部署 vs 用 API 怎么选？**
合规要求 / 单价超 $10K/月 / 极低延迟 → 自部署（vLLM）。
开发期 / 流量不稳定 / 不想搞 infra → API。
混合：私有数据走自部署，通用任务走 API。

**Q5：Prompt cache 在生产怎么用？**
把不变部分（system prompt、tools schema、固定 few-shot）打 cache 标签，变化部分（user input、conversation history）放后面。Cache 5min TTL，保证 5min 内同 system prompt 重复请求复用。多数生产场景能省 50%+ 输入成本。

**Q6：Agent 系统怎么做灰度发布？**
按 user_id 哈希分桶。新版本接 1% → 5% → 25% → 100%。每阶段对比核心指标（成功率、延迟、成本、CSAT），异常自动回滚。Multi-Agent 系统按 trace_id 一致性 hash，确保同一 trace 全程走同版本避免混乱。

**Q7：怎么实现可观测性？**
工具：Langfuse / LangSmith / Helicone（Agent 专用）+ OpenTelemetry（通用）。指标：QPS、p95 延迟、成功率、token / 成本、tool 调用频率 / 失败率、用户满意度。每次 Agent 调用持久化完整 trace（含 prompt / response / tool calls）便于复盘。
