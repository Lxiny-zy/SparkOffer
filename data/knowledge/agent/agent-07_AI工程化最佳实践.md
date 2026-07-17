# AI 工程化最佳实践

本章覆盖 LLM/Agent 应用从原型到生产的工程化问题：流式处理、Token 管理、成本优化、缓存、重试、限流、降级、灰度、安全。

## 1. 流式处理（Streaming）

### 为什么要流式
- **首字节时延（TTFB）**：用户感知响应"快"
- **心智负担**：长回答边看边读
- **可中断**：用户可提前终止
- **GPU 利用**：Decoder 边生成边发送，减少 buffer 压力

### 实现

**OpenAI 流式**：
```python
stream = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    stream=True,
    stream_options={"include_usage": True}  # 最后一个 chunk 含 token 统计
)
for chunk in stream:
    if chunk.choices:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
```

**SSE 推送到前端**：
```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.post("/chat")
async def chat(body: dict):
    async def event_stream():
        async for chunk in llm.astream(body["messages"]):
            yield f"data: {json.dumps({'text': chunk})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

**WebSocket 双向**：双向通信，适合多轮对话 + 中断。

### 流式中的坑
- **工具调用参数分块**：`function.arguments` 分多个 chunk 到达，需要客户端拼接完整 JSON
- **错误处理**：流中途失败需要发送错误事件，而非静默断开
- **心跳**：长时间无数据要发心跳（`: heartbeat\n\n`）避免代理超时
- **断线重连**：前端维护 cursor，重连时 resume
- **生成到一半用户关闭**：后端需要感知并释放 LLM 连接（避免浪费）

---

## 2. Token 管理

### 准确计数

```python
import tiktoken

enc = tiktoken.encoding_for_model("gpt-4o")
tokens = len(enc.encode("你好世界"))

# 估算消息列表总 token（含 role 等开销）
def num_tokens_from_messages(messages, model="gpt-4o"):
    enc = tiktoken.encoding_for_model(model)
    total = 0
    for m in messages:
        total += 4  # 每条消息元数据约 4 token
        for k, v in m.items():
            total += len(enc.encode(str(v)))
    total += 2  # 最终回复启动 token
    return total
```

Claude / Gemini 各有自己的 tokenizer，需用官方 SDK 或 transformers 库。

### Token 预算
- 系统 prompt 尽量短而精
- 多轮对话用 summary / window 压缩
- 工具定义也占 token，不用的工具不要加入
- 长文档检索后要**去重裁剪**再喂 LLM

### 超限处理
- 预先检查：`num_tokens > context_limit - max_tokens` 则拒绝/压缩
- 运行时降级：切换到更长上下文模型（GPT-4o-128k → Gemini-1.5-Pro-1M）
- 截断策略：优先砍最老消息，保留 System Prompt 和最近几轮

---

## 3. 成本优化

### 选型
| 任务 | 推荐 |
|------|------|
| 路由 / 分类 / 意图识别 | Haiku / GPT-4o-mini |
| 主对话 / 推理 | Sonnet / GPT-4o |
| 复杂推理 / 代码 | Opus / GPT-4o |
| 本地可控 | Qwen / Llama 3 / DeepSeek（自部署） |

**分层调用**：先用小模型判断简单问题能否直接答，不能再升级到大模型。

### Prompt Caching

**Anthropic**：
```python
response = client.messages.create(
    model="claude-opus-4-7",
    system=[
        {
            "type": "text",
            "text": "你是助手...（长 system prompt）",
            "cache_control": {"type": "ephemeral"}
        }
    ],
    messages=[...]
)
```
相同前缀命中缓存：**读取成本降 90%**，写入首次增加 25%。5 分钟 TTL。

**OpenAI Automatic Caching**：同前缀 ≥ 1024 token 自动缓存，50% 折扣。

**实务**：把**不变部分**（System Prompt、工具定义、示例）放前面，变的部分放后面，最大化缓存命中。

### Batch API
非实时任务（批量数据处理、离线评估）用 Batch API：
- OpenAI：**50% 价格**，24h 内返回
- Anthropic：**50% 折扣**，Message Batches API
```python
client.messages.batches.create(requests=[...])
```

### 模型级优化
- **KV Cache 复用**：多 turn 对话复用 Cache
- **Speculative Decoding**：小模型先生成，大模型验证
- **Structured Output**：减少重试次数
- **Stop Sequences**：提前终止减少 token

### 监控与归因
按 user_id / feature / model 维度统计 token 消耗，找出**头部大户**优化：
```sql
SELECT feature, model, SUM(input_tokens + output_tokens) AS total, SUM(cost) AS total_cost
FROM llm_calls
WHERE date = '2026-04'
GROUP BY feature, model
ORDER BY total_cost DESC
LIMIT 20;
```

---

## 4. 缓存策略

### 语义缓存（Semantic Cache）
同义 query 命中缓存：
```python
# 查询前先向量化，找相似度 > 0.95 的历史回答
emb = embed(query)
cached = vector_cache.search(emb, threshold=0.95)
if cached:
    return cached.answer
else:
    answer = llm.invoke(query)
    vector_cache.add(emb, answer)
    return answer
```

**框架**：GPTCache、Redis + RedisSearch、Upstash Semantic Cache。

### 精确缓存
Query 规范化（去空白、小写、排序）后 hash：
```python
key = hashlib.md5(normalize(query).encode()).hexdigest()
cached = redis.get(f"llm:{key}")
```

### 缓存分层
- L1：进程内内存（LRU，毫秒）
- L2：Redis（集群共享，< 10ms）
- L3：向量库（语义匹配，< 100ms）

### 失效策略
- TTL：数据时效性决定（实时数据短 TTL，百科长 TTL）
- 主动失效：源数据变更触发 invalidate
- Stale-While-Revalidate：返回旧结果同时异步刷新

---

## 5. 重试与错误处理

### 错误分类
| 类型 | 示例 | 应对 |
|------|------|------|
| 可重试 | 429 限流、500/502/504、超时 | 指数退避重试 |
| 不可重试 | 400 参数错、401 认证 | 立即返回错误 |
| 业务错 | 工具调用返回失败 | 回传 LLM 自我修正 |
| 内容过滤 | 模型拒答 | 根据策略降级或通知用户 |

### 指数退避

```python
import tenacity

@tenacity.retry(
    stop=tenacity.stop_after_attempt(5),
    wait=tenacity.wait_exponential(min=1, max=30),
    retry=tenacity.retry_if_exception_type((APIConnectionError, RateLimitError))
)
def call_llm(messages):
    return client.chat.completions.create(model="gpt-4o", messages=messages)
```

### Circuit Breaker（熔断）
连续失败 N 次后短路，不再调用，给下游喘息：
```python
from circuitbreaker import circuit

@circuit(failure_threshold=5, recovery_timeout=60)
def call_llm(...):
    ...
```

### Fallback 链
```python
models = ["gpt-4o", "claude-opus-4-7", "gemini-1.5-pro"]
for m in models:
    try:
        return call(m, messages)
    except Exception as e:
        log.warning(f"{m} failed: {e}")
        continue
raise Exception("所有模型都失败")
```

---

## 6. 限流与配额

### 场景
- 保护下游 LLM API 不被打爆
- 成本控制：单用户每日上限
- 防滥用：爬虫 / 恶意用户

### 算法
- **Token Bucket**：允许短时突发
- **Leaky Bucket**：严格平滑
- **Sliding Window**：精确统计窗口内请求

### Redis + Lua 实现

```lua
-- sliding_window.lua
local key = KEYS[1]
local window = tonumber(ARGV[1])
local max_requests = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local current = redis.call('ZCARD', key)
if current < max_requests then
    redis.call('ZADD', key, now, now)
    redis.call('EXPIRE', key, window)
    return 1
end
return 0
```

### 多维度限流
- 按 user_id：每用户每分钟 60 次
- 按 feature：RAG 接口每秒 100 次
- 按 model：gpt-4 每分钟 50k tokens
- 按 IP：反爬虫

---

## 7. 降级与熔断

### 场景
- LLM API 不可用
- 成本超预算
- 响应太慢

### 策略
- **模型降级**：Opus 不可用时切 Sonnet / Haiku
- **能力降级**：RAG 检索失败时切到纯 LLM
- **返回兜底**：`"服务繁忙，请稍后再试"`
- **异步化**：转成后台任务，邮件/通知返回

### 配置驱动
```yaml
features:
  smart_search:
    primary: gpt-4o
    fallback: claude-haiku-4.5
    timeout: 10s
    circuit_breaker:
      failure_threshold: 10
      recovery_window: 60s
```

---

## 8. 灰度与 A/B

### 逐步放量
```python
def pick_version(user_id):
    hash_val = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
    if hash_val % 100 < 10:  # 10%
        return "v2"
    return "v1"
```

### 双跑对比（Shadow）
```python
async def chat(query):
    result_a = await asyncio.create_task(agent_v1.run(query))
    result_b = asyncio.create_task(agent_v2.run(query))  # Shadow
    asyncio.ensure_future(compare_and_log(result_a, result_b))
    return result_a
```

### A/B 指标
- 任务成功率
- 用户满意度（👍/👎）
- 停留时长
- 成本
- 延迟

---

## 9. 安全与合规

### Prompt Injection 防御
- **输入分层**：System Prompt 与用户输入**明确标记**
- **输入清洗**：检测注入模式（"ignore previous instructions"）
- **输出校验**：敏感操作前再确认
- **权限隔离**：LLM 无直接访问敏感资源能力
- **工具审批**：高危工具需人工确认

### PII 脱敏
```python
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

results = analyzer.analyze(text=user_input, language="en")
anonymized = anonymizer.anonymize(text=user_input, analyzer_results=results)
# 电话 → <PHONE>, 邮箱 → <EMAIL>
```

### 内容审核
- **OpenAI Moderation API**：免费，7 类违禁内容
- **阿里绿网、腾讯内容安全**：国内合规
- 自训练分类器：特定场景（金融、医疗）

### 数据留存
- 用户对话不默认保存（隐私优先）
- 保存需明确授权
- 敏感行业（医疗、金融）数据不出境
- GDPR / 个保法：可查询、可导出、可删除

### 审计日志
每次 LLM 调用记录：
- 时间戳、user_id、session_id
- 完整 prompt、完整 response（或 hash）
- 模型、参数
- 成本、耗时
- 标记是否涉及敏感操作

---

## 10. 部署架构

### 单体（PoC）
```
客户端 → FastAPI → LLM API
```

### 生产（推荐）
```
                       ┌──── LLM Gateway ────┐
                       │  (路由/限流/重试)    │
Client → CDN → LB →   Chat Service ────────►  OpenAI/Claude/本地
                       │                      │
                       ├─► Vector DB          │
                       ├─► Redis (cache)      │
                       ├─► Postgres (meta)    │
                       └─► Tool Services (K8s)│
                                              │
              Observability ─────────────────┘
              (Traces/Logs/Metrics)
```

### LLM Gateway
统一入口，封装：
- 多提供商路由（OpenAI / Claude / 自部署）
- 鉴权、配额
- 缓存
- 重试、熔断
- 监控、审计

开源方案：**LiteLLM**、**OneAPI**、**Helicone**。

---

## 11. 本地推理部署

### 主流方案
| 工具 | 特点 | 场景 |
|------|------|------|
| **Ollama** | 傻瓜式本地 LLM | 开发调试、桌面应用 |
| **vLLM** | 高吞吐推理引擎 | 生产服务 |
| **TGI (HuggingFace)** | 生产级 | HF 生态 |
| **llama.cpp** | CPU 推理 | 资源受限 |
| **SGLang** | 复杂 workflow 友好 | Agent 场景 |

### vLLM 部署
```bash
vllm serve Qwen/Qwen2.5-72B-Instruct \
    --tensor-parallel-size 4 \
    --gpu-memory-utilization 0.9 \
    --max-model-len 32768
```
提供 OpenAI 兼容 API，可直接接入。

### 量化
- INT8 / INT4 / FP8：显存省、速度快、精度略降
- GGUF：llama.cpp 格式，CPU 可运行
- AWQ / GPTQ：常用权重量化方案

---

## 12. 可维护性

### Prompt 版本管理
- Git 管理 Prompt 模板
- 数据库记录每个版本与效果
- 生产切换 Prompt 要灰度 + 回滚能力

### 评估驱动开发
- 改 Prompt / 换模型 → 跑 benchmark → 通过才合并
- CI 集成：PR 触发测试集评估

### 文档化
- Prompt 的设计意图
- 工具的功能与约束
- 已知失败模式和解决方案

### 持续迭代闭环
```
生产日志 → 采样 → 评估 → 标注 → 测试集/微调数据 → Prompt/模型优化 → 回归 → 发布
```

---

## 13. 常见陷阱

1. **PoC 代码直接上线**：没流式、没缓存、没重试，一上量就崩
2. **Prompt 改就上**：无回归测试，效果可能回退
3. **工具错误不处理**：LLM 看到 Exception 傻眼或胡编
4. **成本不设上限**：单用户/单 session 可能刷爆账单
5. **日志保留过多敏感信息**：合规和隐私风险
6. **全程同步**：高延迟场景不用异步，用户长时间等待
7. **只依赖在线评估**：样本少、成本高，应建立离线评估体系
8. **忽视多模态成本**：图像/视频 token 消耗比文本高数倍
9. **Agent 循环无限制**：max_iterations / budget 必加
10. **未做流量治理**：限流、熔断、降级缺失，故障传导

---

## 面试高频问题

**Q1：流式输出中的工具调用如何处理？**

LLM 流式返回时，tool_call 的 name 和 arguments 会**分块**到达，需要客户端按 index 拼接。完整收到一个 tool_call 后再执行。要点：
- 按 `delta.tool_calls[].index` 分组累积
- 累积 `function.arguments` 字符串直至完整 JSON
- 如果并行多个 tool_call 会有多个 index
- 累积完成后 `json.loads` 可能失败，需要容错

**Q2：Prompt Caching 原理与用法？**

LLM 推理时，前缀的 KV Cache 可复用。Anthropic 显式声明 `cache_control`，OpenAI 自动缓存 ≥ 1024 token 前缀。

**最佳实践**：把**不变内容**（System Prompt、工具定义、few-shot）放前面，变化内容放后面。可节省 50%-90% 输入成本。注意 TTL（通常 5 分钟）和最小前缀长度。

**Q3：大规模生产系统如何控制 LLM 成本？**

多管齐下：
- **分层模型**：路由用小模型，主逻辑用大模型
- **Prompt Caching**：稳定前缀缓存
- **语义缓存**：同义 query 命中历史结果
- **Batch API**：非实时任务半价
- **Token 精简**：系统 prompt 瘦身、历史压缩
- **按维度监控**：找出头部消耗源
- **预算上限**：用户/功能级硬限
- **本地模型兜底**：非关键场景用自部署模型

**Q4：LLM 调用失败的降级策略？**

- **模型降级**：主模型失败切备用（GPT-4 → Claude → 本地）
- **能力降级**：RAG 失败切普通对话
- **兜底回答**：预定义模板
- **异步化**：转后台任务，结果邮件通知
- **Circuit Breaker**：连续失败短路，避免雪崩
- **用户友好**：不暴露内部错误

配合 Fallback Chain + Feature Flag 实现。

**Q5：如何防止 Prompt Injection？**

多层防护：
- **输入校验**：检测可疑模式（"forget previous"）
- **明确分界**：System Prompt 与用户输入用标记分隔
- **最小权限**：LLM 能调的工具严格限定
- **人工审批**：敏感操作二次确认
- **输出校验**：Guardrails 过滤异常输出
- **资源标注**：外部内容（网页、文档）明确告知"不可信数据"
- **不同权限分级**：不同信任级别的输入走不同 Prompt

**Q6：语义缓存如何实现？**

```python
# 查询时
emb = embed(query)
candidates = vector_db.search(emb, threshold=0.95)
if candidates:
    return candidates[0].answer

# 未命中
answer = llm.invoke(query)
vector_db.add(emb, answer)
return answer
```

**注意**：
- 相似度阈值调优（太高命中率低，太低错召回）
- 时效性：新闻类不适用
- 个性化：同问题不同用户可能答案不同，需按 user_id 分 namespace
- 失效：源数据变化需刷新

**Q7：LLM Gateway 的价值？**

统一入口层，承担：
- **多提供商路由**：按成本/质量/可用性动态选择
- **鉴权限流**：统一管理 API Key、配额
- **缓存**：语义缓存、精确缓存
- **重试熔断**：可靠性
- **监控**：成本、延迟、错误率
- **审计**：合规要求

开源：LiteLLM、OneAPI。自研收益也大。

**Q8：本地部署 vs API 调用如何选？**

**API（OpenAI/Claude）**：
- 优点：零运维、最强模型、按量计费
- 缺点：数据出境、网络依赖、成本可能更高（大规模）

**本地（vLLM + Qwen/Llama）**：
- 优点：数据不出、稳定延迟、大规模成本低、可定制
- 缺点：模型通常略弱、运维复杂、GPU 投入大

**混合**：高价值/通用任务用 API，长尾/数据敏感任务本地。很多企业走混合路线。

**Q9：长对话如何避免上下文爆炸？**

- **滑窗**：只保留最近 N 轮
- **摘要**：LLM 压缩历史为摘要
- **实体抽取**：抽关键实体替代原文
- **向量化**：历史存 Vector Store，按需检索
- **分层**：System Prompt + User Profile（稳定） + Summary（长期） + Recent Messages（近期）
- **超长模型**：Gemini 1.5 / Claude 1M 直接硬扛

LangGraph + Checkpointer / LlamaIndex Memory 都有现成方案。

**Q10：生产 LLM 应用的可观测性需要哪些？**

- **Traces**：完整调用链（Request → LLM → Tool → LLM → Response）
- **Metrics**：QPS、延迟 P50/P95/P99、错误率、Token/成本
- **Logs**：结构化日志，可检索
- **业务指标**：成功率、用户满意度、转化率
- **成本归因**：按 user / feature / model 维度
- **告警**：阈值触发、异常检测

工具栈：OpenTelemetry + Prometheus + Grafana + LangFuse/LangSmith + ELK。
