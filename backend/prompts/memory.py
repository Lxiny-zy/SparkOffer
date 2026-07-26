"""长期画像 Mem0 两阶段更新的第一阶段——面试洞察提取。

第二阶段（ADD / UPDATE / NOOP / IMPROVE 合并）的 PROFILE_UPDATE_PROMPT 在
prompts/interviewer.py。两阶段的 prompt 自此都集中在 prompts/ 下维护。
"""

from backend.prompts._common import injection_guard


EXTRACT_PROMPT = """你是一个面试教练的分析引擎。根据面试对话记录，提取关于候选人的结构化洞察。

""" + injection_guard("`<transcript>`", data_kind="候选人的面试对话记录", tail="只提取本次面试真实暴露的信息。") + """

## 候选人当前画像
{current_profile}

## 本次面试记录
模式: {mode}
领域: {topic}
<transcript>
{transcript}
</transcript>

## 评分记录（如有）
{scores}

## 任务
分析这次面试，提取以下信息，返回 JSON：

```json
{{
    "weak_points": [
        {{"point": "对 Python GIL 的理解停留在表面", "topic": "python"}}
    ],
    "strong_points": [
        {{"point": "RAG 架构描述清晰，有实战数据支撑", "topic": "rag"}}
    ],
    "topic_mastery": {{
        "python": {{"notes": "基础扎实但高级特性（元类、描述符）薄弱"}}
    }},
    "communication_observations": {{
        "style_update": "回答技术题时逻辑清晰，但项目描述缺少量化数据",
        "new_habits": ["遇到不会的题会坦诚说不确定"],
        "new_suggestions": ["项目经历多用数据指标（提升了XX%）来量化成果"]
    }},
    "thinking_patterns": {{
        "new_strengths": ["能用类比解释复杂概念"],
        "new_gaps": ["被追问'为什么这样设计'时缺乏推导过程", "对比类问题回答缺乏结构"]
    }},
    "session_summary": "本次 Python 专项训练，基础题表现好，但 GIL 和 GC 机制理解不够深入",
    "dimension_scores": {{
        "technical_depth": 6,
        "project_articulation": 7,
        "communication": 5,
        "problem_solving": 6
    }},
    "avg_score": 6.0
}}
```

## dimension_scores 评分说明（仅简历面试模式需要填写，专项训练留空即可）
- technical_depth (1-10): 技术理解的深度，是真懂还是在背？能否说出 why？
- project_articulation (1-10): 项目描述能力——设计思路、量化成果、技术权衡是否讲清楚
- communication (1-10): 表达的清晰度、结构化程度、简洁性
- problem_solving (1-10): 被追问时的分析推理能力，能否现场推导
- avg_score = 四个维度的均值，保留一位小数

规则：
- 只提取本次面试中明确暴露的信息，不要猜测
- 薄弱点要具体，不要泛泛说"XX不好"
- 如果候选人对某个之前的薄弱点表现出了进步，在 strong_points 里标注
- topic_mastery 只需提供 notes（一句话描述掌握情况），score 由算法计算，不需要你判断
- 专项训练模式下 dimension_scores 可省略，只需给 avg_score
"""
