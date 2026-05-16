# Agent 评估方法论

Agent 系统的"对错"难量化——同一个问题可以有多个合理答案、推理路径多样、外部 API 状态不稳定。系统化评估是 Agent 工程化的核心难题，也是面试官最喜欢问的方向之一。

## 1. 评估的层次

| 层次 | 评估对象 | 例子 |
|---|---|---|
| **单元层** | 单个 LLM call | prompt → 输出是否符合 schema |
| **组件层** | 单个 tool / 单次 RAG | 检索 hit@K、tool 调用准确率 |
| **轨迹层** | 一次完整任务 | 步数、调用顺序、最终成功 |
| **会话层** | 多轮对话 | 用户满意度、解决率、CSAT |
| **业务层** | 系统对业务的影响 | 转化率、留存、成本 |

**生产 Agent 必须四层都覆盖**。

## 2. 数据集构建

### 2.1 三类数据来源

1. **手工标注**：精心设计的 golden cases，覆盖核心 + 边界场景。质量最高，量小。
2. **生产日志采样**：真实分布，但需脱敏 + 标注。
3. **LLM 合成**：用强模型基于场景描述生成 case，配合人工抽检。低成本、高量，但要防止"偏向 LLM 偏好"的偏差。

### 2.2 案例分布

按业务场景频率 + 难度分层：
- 高频简单（70%）：保准确率
- 高频复杂（20%）：保体验
- 长尾边界（10%）：保鲁棒性

### 2.3 Golden case 字段

```json
{
  "id": "case-042",
  "input": {"messages": [...], "user_context": {...}},
  "expected_intent": "refund_request",
  "expected_tool_calls": [
    {"name": "search_orders", "args_partial": {"user_id": "*"}}
  ],
  "expected_outcome": {"refund_initiated": true},
  "acceptable_outputs": ["...", "..."],
  "tags": ["refund", "happy-path", "high-priority"]
}
```

## 3. 评估方法

### 3.1 规则评估

确定性维度直接用断言：
- 输出 schema 是否合规
- 是否调对了 tool（按 name 匹配）
- 是否避免了禁用词
- 步数是否在阈值内
- 最终状态是否符合期望（DB 状态 / API 状态）

```python
def assert_tool_called(trace, tool_name):
    return any(step.tool == tool_name for step in trace.steps)
```

### 3.2 LLM-as-Judge

主观维度（自然度、有用性、忠实性）让强模型当裁判：

```python
JUDGE_PROMPT = """
你是评测员。给以下 Agent 回答打 1-5 分，并说明理由。

问题：{question}
参考答案：{reference}
Agent 回答：{response}

输出 JSON: {"score": 1-5, "reasoning": "..."}
"""

def llm_judge(question, reference, response):
    return judge_llm.with_structured_output(JudgeResult).invoke(...)
```

**陷阱**：LLM judge 有偏差。必须：
- 用比 Agent 模型强一档的模型当 judge
- 校准：人工标 50 个，跟 LLM judge 算 Spearman 相关性，>0.7 才用
- 多 judge 取平均（不同模型）
- judge prompt 给明确评分细则

### 3.3 端到端业务指标

**任务成功率（Task Success Rate）**：用户最终目标是否达成。
**首次解决率（First Contact Resolution）**：单轮搞定 vs 需要升级。
**平均轮次**：轮次越少越高效。
**用户满意度（CSAT）**：用户主动评分。

## 4. RAG 专项评估（RAGAS）

```python
from ragas.metrics import (
    faithfulness, answer_relevancy,
    context_precision, context_recall,
    answer_similarity, answer_correctness,
)
```

| 指标 | 测什么 | 怎么算 |
|---|---|---|
| Faithfulness | 答案是否忠于 context | LLM 拆答案为 claims，逐个验证是否在 context 中 |
| Answer Relevancy | 答案是否切题 | 反向生成问题，与原问题求 cosine |
| Context Precision | 召回的 context 是否真有用 | 按相关性给 context 排名，看 top 是否相关 |
| Context Recall | 标准答案需要的信息是否都召回 | 把 ground truth 拆成 claims，验证每个是否能在 context 找到 |

## 5. 轨迹评估

不只看最终答案，还要看推理路径：

```
[正确轨迹]
T1: 调 search_orders → T2: 找到 order_id → T3: 调 get_tracking → T4: 回答

[错误轨迹]
T1: 直接回答（无调 tool，幻觉）
T1: 调 search_orders → T2: 错的 user_id → T3: 找不到 → T4: 错答
T1: 调 wrong_tool → ... 
```

**Trajectory 距离**：编辑距离 / DAG 同构 / LLM 评估。

```python
def trajectory_match_score(actual, expected):
    """expected 是允许的工具调用序列模板，actual 是实际轨迹。"""
    matched = sum(1 for e in expected if any(matches(a, e) for a in actual))
    return matched / len(expected)
```

## 6. 在线评估与 A/B

### 6.1 影子流量

新版本 Agent 不直接服务用户，跟生产版并行处理同一请求，对比输出（用户只看到旧版本结果）。零风险评估候选版本。

### 6.2 Canary 部署

新版本接 1% 流量 → 5% → 25% → 100%。每阶段观察核心指标，异常自动回滚。

### 6.3 多臂老虎机

多版本同时在线，按效果动态分配流量。Tonsen / Multi-armed bandit。

## 7. Observability

### 7.1 必备可观测性

每次 Agent 调用持久化：
- request_id、trace_id、user_id、session_id
- 完整 conversation history
- 每个节点的 input / output / latency / cost
- 每次 LLM call 的 prompt / response / model / token / temperature
- 每次 tool call 的参数 / 结果 / 错误
- 最终 outcome 与人工反馈

工具：**Langfuse / LangSmith / Helicone / Arize Phoenix**。OpenTelemetry 兼容方案越来越多。

### 7.2 关键指标 Dashboard

- 实时：QPS、p50/p95/p99 延迟、错误率、成本/小时
- 质量：成功率、平均轮次、用户评分
- 行为：每个 tool 的调用频率、平均参数、失败率
- 异常：高延迟会话、连续失败 user、成本突增

### 7.3 告警

- p95 延迟 > 阈值
- 错误率 > 1%
- 单会话成本 > $0.5
- LLM provider 5xx 比例突增
- 某个 tool 调用失败率 > 5%

## 8. 持续改进闭环

```
[生产日志] → [失败案例自动挖掘] → [人工标注] → [加入回归集]
                                                  ↓
[发版] ← [验证] ← [本地评估] ← [改 prompt / model / tools]
```

关键：**让评估集每周增长，覆盖率持续扩大**。新发现的失败模式必须纳入回归集，防止后续修改重复打脸。

## 9. 成本评估

模型选型不只看准确率，还要看成本/正确答案：

```
成本效率 = 正确率 / (avg_input_tokens × $/M_in + avg_output_tokens × $/M_out)
```

GPT-5 准确率高但 $$$，Haiku 4.5 便宜但弱——不同场景可能用不同模型。

**模型路由**：分类器先判断难度，简单的走小模型，难的升级到大模型。能省 60-80% 成本而准确率几乎不降。

## 10. 高频面试题

**Q1：怎么评估 Agent 的好坏？**
不是单一指标。最少分四层：① 单元（schema、规则）② 组件（tool 准确率、RAG hit@K）③ 轨迹（成功率、步数）④ 业务（CSAT、转化）。线下回归集 + 线上 A/B + 持续 Observability。

**Q2：LLM-as-Judge 有什么坑？**
偏差：① 偏好长答案 ② 偏好礼貌答案 ③ 偏好同模型生成的答案 ④ 不一致（同 prompt 不同 score）。缓解：用更强模型当 judge、人工校准、多 judge ensemble、严格评分细则、控温采样多次。

**Q3：怎么挑选 LLM-as-Judge 的模型？**
原则：judge 应明显强于被测模型。被测 Haiku → judge 用 Sonnet；被测 Sonnet → judge 用 Opus 或 GPT-4o。同档可能"互相吹捧"，结果不可信。

**Q4：怎么处理"答案多样合理"的情况？**
不要 string match。三种方法：① 让 judge 看参考答案 + 实际答案打分；② Embedding 余弦相似度（>阈值算对）；③ 关键事实点评估——拆解为 atomic facts 验证每个事实是否被 cover。

**Q5：A/B 测试需要多大样本量？**
基于显著性测试。对核心指标用 sequential testing 或贝叶斯方法实时计算。经验值：要检测 5% 提升至少需要数千 session；只检测 20% 提升数百也够。

**Q6：怎么防止"评估集污染"？**
评估集与训练 / few-shot / RAG 数据严格隔离。定期 rotate 一部分（避免被 prompt 工程师"过拟合"）。生产采样的 case 要看时间跨度，老 case 可能已被业务变化淘汰。
