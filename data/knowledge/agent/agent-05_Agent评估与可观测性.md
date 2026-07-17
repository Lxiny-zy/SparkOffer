# Agent 评估与可观测性

## 1. 为什么难

### 与传统软件的区别
- **非确定性**：同样输入可能有不同输出
- **无标准答案**：开放式任务（"写一份市场报告"）难以打分
- **多步骤**：错误可能发生在任何中间步
- **外部依赖**：工具 API 抖动、LLM 升级都影响结果
- **成本敏感**：每次评估调用大模型，花钱

### 与 LLM 评估的区别
LLM 评估单看"输入 → 输出"；Agent 评估看**完整轨迹**：
- 选对工具了吗？
- 参数填对了吗？
- 处理错误妥当吗？
- 步骤数是否合理？
- 最终结果好吗？

---

## 2. 评估维度

### 1. 最终结果（End-to-End）
- **任务成功率**（Task Success Rate）：完成比例
- **答案正确性**：与 Ground Truth 对比
- **用户满意度**：人工评分 / 用户反馈

### 2. 过程质量（Trajectory）
- **工具选择准确率**：选对工具的比例
- **参数准确率**：参数填写正确的比例
- **步骤数**：完成任务的平均步骤（越少越好）
- **无效调用率**：重复/无用调用占比
- **恢复能力**：出错后能否自行修正

### 3. 成本
- **Token 消耗**：prompt + completion
- **API 费用**：按模型定价
- **延迟**：端到端耗时
- **工具调用次数**：影响下游服务成本

### 4. 安全与合规
- **幻觉率**：输出虚构信息的比例
- **越权调用**：未授权工具的调用
- **敏感信息泄露**：个人信息是否被暴露
- **Prompt Injection 抵抗**：能否抗注入攻击

---

## 3. 评估方法

### 方法 1：规则匹配（Exact / Fuzzy Match）

```python
def evaluate(output, expected):
    return output.strip() == expected.strip()

# 或模糊匹配
from rapidfuzz import fuzz
score = fuzz.ratio(output, expected) / 100
```

**适用**：有明确答案的任务（数学、SQL 生成）。
**不适用**：开放式生成。

### 方法 2：语义相似度

```python
from sentence_transformers import SentenceTransformer, util
model = SentenceTransformer("BAAI/bge-m3")

emb1 = model.encode(output)
emb2 = model.encode(expected)
score = util.cos_sim(emb1, emb2).item()
```

**优点**：容忍措辞差异。
**缺点**：语义近但事实错的情况打分仍高。

### 方法 3：LLM-as-Judge（裁判模型）

用另一个 LLM 给 Agent 输出打分：

```python
judge_prompt = """
你是严格的评估专家。请评估 AI 助手的回答质量。

用户问题：{question}
AI 回答：{answer}
参考答案：{reference}

从以下维度打 1-5 分：
1. 事实准确性
2. 完整性
3. 清晰度
4. 遵循指令

输出 JSON：{{"accuracy": N, "completeness": N, "clarity": N, "instruction": N, "reasoning": "..."}}
"""

result = judge_llm.complete(judge_prompt.format(...))
```

**优点**：灵活、可覆盖开放任务。
**缺点**：
- 裁判模型偏见（倾向冗长、倾向同家族模型）
- 成本高（每次评估 = 一次 LLM 调用）
- 一致性问题

**改进**：
- **Pairwise 比较**：让裁判选 A 和 B 哪个好，比绝对打分更稳定
- **多 Judge 平均**：多个裁判模型投票
- **Chain-of-Thought Judge**：要求先分析再打分

### 方法 4：参考无关评估（Reference-Free）

无参考答案时：
- **Faithfulness**（忠实度）：回答是否基于提供的上下文
- **Relevance**（相关性）：回答是否切题
- **Coherence**（连贯性）：逻辑是否通顺

**Ragas / TruLens** 等框架专门做这个。

### 方法 5：人工评估

- **专家评估**：领域专家打分
- **众包**：Amazon MTurk、Scale AI
- **红队测试**：主动攻击寻找漏洞
- **A/B 测试**：真实用户对比两个版本

人工评估**昂贵但最权威**，通常用于建立 benchmark，之后用自动评估。

---

## 4. 轨迹评估（Trajectory Evaluation）

### 为什么重要
最终答案正确不代表过程正确。Agent 可能：
- 运气好瞎猜对了
- 走了极长的弯路
- 调用了错误工具但最后纠正了

### 评估项

**1. Exact Match Trajectory**
```python
expected_trace = [
    ("search_web", {"query": "..."}),
    ("read_url", {"url": "..."})
]
actual_trace = agent.run(task).trace

# 步骤匹配
correct = sum(1 for a, e in zip(actual, expected) if a.tool == e.tool)
```

**2. Subset Match**
```python
expected_tools = {"search_web", "read_url"}
actual_tools = {step.tool for step in actual_trace}

precision = len(expected_tools & actual_tools) / len(actual_tools)
recall = len(expected_tools & actual_tools) / len(expected_tools)
```

**3. LLM 评估轨迹**
```python
prompt = f"""
任务：{task}
Agent 执行步骤：
{format_trajectory(trace)}

评估：
- 步骤是否合理？
- 有无多余步骤？
- 有无遗漏关键步骤？
- 错误处理是否得当？
"""
```

---

## 5. 主流评估框架

### LangSmith

LangChain 官方，深度集成 LangChain/LangGraph。

```python
from langsmith import Client
from langsmith.evaluation import evaluate

client = Client()

# 创建数据集
dataset = client.create_dataset("my-agent-eval")
client.create_examples(
    inputs=[{"question": "..."}],
    outputs=[{"answer": "..."}],
    dataset_id=dataset.id
)

# 定义评估函数
def accuracy(run, example):
    return {"score": 1 if run.outputs["answer"] == example.outputs["answer"] else 0}

# 跑评估
result = evaluate(
    lambda x: agent.invoke(x),
    data=dataset.name,
    evaluators=[accuracy],
)
```

**特性**：
- Trace 可视化（每次调用完整记录）
- 数据集管理
- 多种内置评估器（LLM-as-Judge）
- Prompt 版本管理
- 生产环境监控

### LangFuse

开源替代 LangSmith，自托管友好。
- Trace 记录 + 评分
- Prompt 版本管理
- 成本追踪
- 支持任意 LLM 框架

```python
from langfuse import Langfuse
from langfuse.decorators import observe

langfuse = Langfuse()

@observe()
def my_agent(query):
    # 自动被追踪
    return agent.run(query)
```

### Ragas（专注 RAG）

```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision

result = evaluate(
    dataset=dataset,
    metrics=[faithfulness, answer_relevancy, context_precision]
)
```

指标：
- **Faithfulness**：答案是否基于检索内容
- **Answer Relevancy**：答案是否相关
- **Context Precision / Recall**：检索是否精准

### TruLens

```python
from trulens_eval import Tru, Feedback
from trulens_eval.feedback.provider.openai import OpenAI as OAIProvider

provider = OAIProvider()

f_groundedness = Feedback(provider.groundedness_measure)
f_relevance = Feedback(provider.relevance)

# 包装 agent
from trulens_eval import TruChain
tru_chain = TruChain(agent, feedbacks=[f_groundedness, f_relevance])
```

### DeepEval

单元测试风格的 LLM 评估：
```python
from deepeval.metrics import AnswerRelevancyMetric

metric = AnswerRelevancyMetric(threshold=0.7)
test_case = LLMTestCase(input="...", actual_output=agent.run("..."))
assert metric.measure(test_case) >= 0.7
```

### Promptfoo

YAML 配置驱动，CLI 友好：
```yaml
providers:
  - openai:gpt-4o
tests:
  - vars:
      topic: AI
    assert:
      - type: contains
        value: "人工智能"
      - type: llm-rubric
        value: "答案是否专业？"
```

---

## 6. 标准基准（Benchmark）

### AgentBench
多场景 Agent 能力测试（操作系统、数据库、网页、卡牌游戏等）。

### GAIA
通用 Agent 助手基准，任务分 3 级难度，强调多工具、多步骤。

### SWE-Bench
软件工程真实 PR 修复任务，广泛用于评估 AI 程序员。

### WebArena
浏览器环境中的任务完成（网购、社交操作）。

### ToolBench
大规模工具使用能力评测（16000+ 真实 API）。

### τ-bench（Tau-bench）
模拟多轮用户交互的工具使用评测。

### MMLU / MMLU-Pro
通用知识（Agent 底层能力）。

---

## 7. 可观测性（Observability）

### 三大支柱
- **Traces**：一次请求的完整链路
- **Metrics**：聚合指标（QPS、P99 延迟）
- **Logs**：详细日志

### Trace 结构

```
Trace (request_id: abc)
├─ Span: agent.invoke (total: 5.2s)
│  ├─ Span: llm.call #1 (1.1s)  [model=gpt-4o, prompt_tokens=1234, completion_tokens=45]
│  ├─ Span: tool.search (2.0s)  [tool=web_search, input={...}, output={...}]
│  ├─ Span: llm.call #2 (1.5s)
│  └─ Span: llm.call #3 (0.6s)
```

### 关键指标

**业务指标**：
- 任务成功率、用户 CSAT
- Agent 调用量（DAU/MAU）
- 人工兜底率（多少 case 需要人工介入）

**技术指标**：
- P50 / P95 / P99 延迟
- Token 消耗分布
- 错误率（LLM 错误、工具错误、超时）
- 成本（总额、均次、最贵 Top10）
- 缓存命中率

**质量指标**（需评估系统）：
- 自动评估分数
- 人工抽检评分
- 用户反馈（👍/👎）

### 实现栈

```
应用代码
  ↓ OpenTelemetry
[Traces] → Tempo / Jaeger / LangSmith
[Metrics] → Prometheus / DataDog
[Logs] → Loki / ELK
  ↓
Grafana 统一可视化
```

### OpenTelemetry 示例

```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

@tracer.start_as_current_span("agent_run")
def run_agent(query):
    span = trace.get_current_span()
    span.set_attribute("user_id", user_id)
    span.set_attribute("query", query)

    with tracer.start_as_current_span("llm_call") as llm_span:
        response = llm.invoke(query)
        llm_span.set_attribute("tokens", response.usage.total_tokens)

    return response
```

现在有 **OpenLLMetry**、**OpenInference** 等语义规范，统一 LLM/Agent 相关字段。

---

## 8. 生产环境 Agent 监控

### 告警（Alerting）
- 错误率 > 5% → 告警
- P99 延迟 > 30s → 告警
- 单次成本 > 阈值 → 告警
- 工具调用失败率突增 → 告警

### 巡检（Guardrails）
实时检查每次输出：
- **PII 检测**：防止泄露身份证、电话
- **敏感词**：过滤违禁内容
- **越权**：未授权工具调用阻止
- **成本上限**：单次对话 Token 超额自动终止

框架：**NVIDIA NeMo Guardrails**、**Guardrails AI**、**Rebuff**。

### Shadow Deployment
新版本 Agent 和老版本**并行跑**，不返回给用户，对比效果。

### A/B 测试
- 流量切分：10% 新版本，90% 老版本
- 对比指标：成功率、满意度、成本
- 逐步放量

---

## 9. 持续改进闭环

```
┌─────────────────┐
│ 线上真实对话    │
└──────┬──────────┘
       │ 抽样
       ▼
┌─────────────────┐
│ 自动评估        │
│ (LLM-as-Judge) │
└──────┬──────────┘
       │ 低分 case
       ▼
┌─────────────────┐
│ 人工审核 + 标注 │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ 加入测试集      │
│ / 优化 Prompt   │
│ / 微调模型      │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ 回归测试        │
└──────┬──────────┘
       │ 通过
       ▼
   部署新版本
```

---

## 10. 评估反面模式

### 1. 只看最终答案
忽略过程，无法定位错误环节。

### 2. 只在离线数据上评
线上真实分布与离线差异大，需线上持续评估。

### 3. LLM-as-Judge 无人工校准
裁判模型也会错，需定期抽检校准。

### 4. 指标不跟目标对齐
优化"准确率"但用户要"速度"，南辕北辙。

### 5. 评估数据污染
测试集泄露进训练集，分数虚高。

### 6. 单一指标
只看准确率忽略成本，或反之。

### 7. 无版本管理
换 Prompt 后不知道效果是否退步。

---

## 面试高频问题

**Q1：Agent 评估和 LLM 评估的区别？**

- **LLM 评估**：输入 → 输出，单步
- **Agent 评估**：完整轨迹（工具选择、参数、步骤数、恢复能力、最终结果）

Agent 要看"过程 + 结果"，LLM 只看结果。过程评估维度：工具选择准确率、参数准确率、步骤数、无效调用率、错误恢复。

**Q2：LLM-as-Judge 的问题与改进？**

**问题**：
- 倾向冗长输出
- 偏爱同家族模型
- 位置偏差（A/B 中先出现的更容易选中）
- 重复性差

**改进**：
- Pairwise 比较（比绝对打分更稳定）
- 多裁判投票
- 位置随机化
- Chain-of-Thought 先分析再打分
- 校准：定期与人工标注对齐
- 用最强模型做 Judge（Opus/GPT-4）

**Q3：无参考答案怎么评估？**

Reference-Free 评估：
- **Faithfulness**：回答是否基于上下文
- **Relevance**：是否切题
- **Coherence**：逻辑连贯性
- **Completeness**：是否遗漏

RAG 场景常用 Ragas 三指标：Faithfulness / Answer Relevancy / Context Precision。

**Q4：如何搭建 Agent 可观测性？**

三层：
- **Traces**：OpenTelemetry + LangSmith/LangFuse/Jaeger，记录每次调用的完整链路
- **Metrics**：Prometheus + Grafana，监控 QPS/延迟/成本/成功率
- **Logs**：结构化日志到 ELK/Loki

关键字段：request_id、user_id、model、tokens_in/out、tool_name、latency、error。

**Q5：生产 Agent 的 Guardrails 做什么？**

- **输入侧**：Prompt Injection 检测、敏感词过滤
- **输出侧**：PII 脱敏、幻觉检测、违禁内容过滤
- **行为侧**：越权工具调用拦截、成本上限、频率限制
- **合规侧**：审计日志、可追溯

框架：NeMo Guardrails、Guardrails AI、Rebuff。

**Q6：如何处理评估的不一致性？**

LLM 有随机性，同一 case 可能每次结果不同。
- **多次采样**：N 次运行取平均或多数
- **固定 temperature**：评估时 temperature=0
- **大样本**：至少数百个测试点，单个波动平均掉
- **置信区间**：报告均值 + 置信区间

**Q7：成本控制有哪些手段？**

- **模型降级**：Supervisor/Router 用小模型
- **Prompt Caching**：重复系统 Prompt 缓存
- **结果缓存**：相同查询命中缓存
- **早停**：达到置信度提前结束
- **预算约束**：单次对话 Token 硬上限
- **批处理**：非实时任务用 Batch API（半价）
- **监控告警**：异常消耗立刻发现

**Q8：如何做 Agent 的回归测试？**

- 维护**黄金测试集**（几百个关键 case）
- 每次 Prompt / 模型 / 工具变更后自动跑
- 设置质量阈值，低于则拒绝合并
- 区分"必过 case"和"统计指标"
- 记录每次回归的指标变化，对比历史

类似传统软件 CI，但加了质量门槛而非简单 pass/fail。

**Q9：红队测试（Red Teaming）是什么？**

主动攻击自己 Agent 找漏洞：
- **Prompt Injection**：注入恶意指令
- **越狱**：绕过安全约束
- **数据泄露**：诱导输出敏感信息
- **工具滥用**：引导调用危险工具
- **社会工程**：冒充管理员等

流程：红队攻击 → 发现漏洞 → 修复 Prompt/规则 → 回归测试 → 持续对抗。大厂（OpenAI/Anthropic）有专职红队。

**Q10：长期运行 Agent 如何监控？**

Agent 运行数小时/数天：
- **进度追踪**：定期汇报当前状态
- **Checkpoint**：定期持久化，崩溃可恢复
- **异常检测**：步骤数异常多、循环卡住、无新进展
- **资源上限**：Token/时间/费用预算
- **中断能力**：人工可随时中断/调整
- **流式 Trace**：实时查看，不用等结束
