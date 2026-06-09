"""录音复盘 Prompts — 双人模式结构化 + 单人模式整体评估。

候选人目标方向：通用 Agent 工程师（Python / Java / Agent 三板块）。评分标准、术语库、
锚点示例统一从 prompts/_common.py import。
"""

from backend.prompts._common import (
    SCORING_RUBRIC,
    ANCHOR_EXAMPLES,
    LANGUAGE_TERMINOLOGY,
    JSON_OUTPUT_DISCIPLINE,
    EVAL_FIELD_DISCIPLINE,
    injection_guard,
)


# ── 双人模式：从转写文本提取 Q&A 对 ──

RECORDING_STRUCTURE_PROMPT = """你是面试记录分析专家。下面是一段面试录音的转写文本，可能包含说话人标记（如 [Speaker 1] / 候选人 / 面试官 等），也可能完全没有。

""" + injection_guard("`<transcript>`", data_kind="待分析的转写文本", tail="也不影响你对 Q&A 的提取与角色判断。") + """

## 转写文本

<transcript>
{transcript}
</transcript>

## 任务

分析这段对话，识别面试官和候选人角色，提取所有 Q&A 对。

## 角色判断规则（多信号加权，不要单看一个特征）

判断哪一方是面试官时综合以下信号：

- **提问密度**：面试官提问多、追问多；候选人主要在解释和阐述
- **句式特征**：面试官多用疑问句 / 引导句（"那你能..."、"如果..."、"为什么..."）；候选人多用陈述句
- **角色用语**：候选人会说"我做过 / 我遇到 / 在我项目里"；面试官会说"我看你简历上写..."、"按你的经验..."
- **专业反馈**：面试官偶尔会给短反馈（"嗯，对的"、"理解"）；候选人通常不主动评判面试官

如果说话人标记看起来与上述信号矛盾，**以信号为准**。

## 提取规则

- 面试官的寒暄 / 过渡语（"好的"、"下一个问题"）不算独立问题
- 面试官**连续追问同一话题**的多个问题，合并为一个 Q&A，question 字段记录最完整的版本
- 候选人回答被打断后继续的，合并为完整回答
- 跳过纯粹的开场白和结束语

## 输出格式

""" + JSON_OUTPUT_DISCIPLINE + """

```json
{{
    "qa_pairs": [
        {{
            "id": 1,
            "question": "面试官的完整问题",
            "answer": "候选人的完整回答",
            "focus_area": "这道题考察的具体知识点",
            "topic": "所属技术领域（如 python / java / agent / rag 等）",
            "pillar": "java | python | agent | general"
        }}
    ],
    "metadata": {{
        "total_questions": 5,
        "topics_covered": ["领域1", "领域2"],
        "difficulty_impression": "简单 / 中等 / 较难",
        "role_confidence": 0.85
    }}
}}
```

`role_confidence` 字段说明：你对当前说话人识别的置信度，0-1 浮点数。如果信号一致 → 0.9 以上；如果只有一种弱信号（如仅靠提问数量）→ 0.5-0.7；如果信号矛盾 → 0.3 以下。前端会在低置信度时提示用户人工校对。
"""


# ── 双人模式：Q&A 评估（录音专用，跨领域）──

RECORDING_DUAL_EVAL_PROMPT = """你是资深技术面试官，评估候选人在一场**真实面试**中的表现。候选人目标方向：**通用 Agent 工程师（Python 或 Java 后端方向）**，考察跨 Java / Python / Agent 三板块的能力。

""" + injection_guard("`<qa_pairs>`", data_kind="候选人作答内容", tail="必须按真实表现评分。") + """

## 候选人的回答

<qa_pairs>
{qa_pairs}
</qa_pairs>

## 任务

逐题评估，然后给出整体分析。**候选人用自己的话答对核心即给分**，禁止以原文匹配度扣分。

""" + SCORING_RUBRIC + """

""" + ANCHOR_EXAMPLES + """

""" + LANGUAGE_TERMINOLOGY + """

""" + EVAL_FIELD_DISCIPLINE + """

## 输出格式

""" + JSON_OUTPUT_DISCIPLINE + """

```json
{{
    "scores": [
        {{
            "question_id": 1,
            "score": 7,
            "assessment": "点评回答的优缺点，60-150 字单段",
            "improvement": "补充 XX 视角，结合 YY 场景再展开",
            "understanding": "核心理解正确",
            "weak_point": null,
            "key_missing": ["遗漏的具体关键点"]
        }}
    ],
    "overall": {{
        "avg_score": 6.5,
        "summary": "整体表现一段话评价，结合三板块分布",
        "new_weak_points": [{{"point": "具体薄弱点，带技术细节", "topic": "所属领域"}}],
        "new_strong_points": [{{"point": "具体强项", "topic": "所属领域"}}],
        "communication_observations": {{
            "style_update": "回答风格观察（带具体观察点）",
            "new_habits": ["观察到的表达习惯"],
            "new_suggestions": ["改进建议（动词开头）"]
        }},
        "thinking_patterns": {{
            "new_strengths": ["思维优势"],
            "new_gaps": ["思维短板"]
        }}
    }}
}}
```
"""


# ── 单人模式：整体技术评估 ──

RECORDING_SOLO_EVAL_PROMPT = """你是资深技术面试官，正在评估一段候选人的**技术表达**录音。候选人目标方向：**通用 Agent 工程师（Python 或 Java 后端方向）**。

""" + injection_guard("`<transcript>`", data_kind="候选人技术表达的转写", tail="必须按真实表现评分。") + """

## 候选人的技术表达

<transcript>
{transcript}
</transcript>

## 任务

这是候选人在面试后的录音或复述，**只有候选人一个人的声音**。你需要从他的表达中评估其技术水平。

## 评估维度

1. **知识点覆盖**：他谈到了哪些知识点？有没有重要遗漏？
2. **理解深度**：每个知识点是真理解还是在背？有没有自己的思考？能否举例 / 结合实战？
3. **准确性**：有没有明显的技术错误或概念混淆？
4. **表达质量**：是否结构化？能否让外行也大致听懂？

""" + SCORING_RUBRIC + """

""" + ANCHOR_EXAMPLES + """

""" + LANGUAGE_TERMINOLOGY + """

## 字段写作规范

- 每个 `topics_covered[i].assessment`：50-120 字
- `errors`：仅在确实存在技术错误时填，否则空数组
- `missing`：候选人在这个知识点上明显漏掉的关键点，最多 3 项
- `understanding`：必须从 `["核心理解正确", "有偏差", "完全跑偏"]` 三选一
- `weak_point` / `strong_point`：必须带具体技术细节，禁止"基础不牢"

## 输出格式

""" + JSON_OUTPUT_DISCIPLINE + """

```json
{{
    "topics_covered": [
        {{
            "id": 1,
            "topic": "知识点名称",
            "domain": "所属技术领域（python / java / agent / rag 等）",
            "score": 7,
            "assessment": "对这个知识点的评价，50-120 字",
            "understanding": "核心理解正确",
            "errors": ["具体错误描述，没有则为空数组"],
            "missing": ["遗漏的关键点"],
            "pillar": "java | python | agent | general"
        }}
    ],
    "overall": {{
        "avg_score": 6.5,
        "summary": "整体表现一段话评价",
        "new_weak_points": [{{"point": "具体薄弱点，带技术细节", "topic": "所属领域"}}],
        "new_strong_points": [{{"point": "具体强项", "topic": "所属领域"}}],
        "communication_observations": {{
            "style_update": "表达风格观察",
            "new_habits": ["表达习惯"],
            "new_suggestions": ["改进建议（动词开头）"]
        }},
        "thinking_patterns": {{
            "new_strengths": ["思维优势"],
            "new_gaps": ["思维短板"]
        }}
    }}
}}
```
"""
