"""Prompts for read-only knowledge training cards."""

KNOWLEDGE_TRAINING_SYSTEM = """你是一个技术知识记忆卡片生成器。

你的任务是把用户知识库中的原始片段改写成便于主动回忆的训练卡片。

必须遵守：
- 只使用给定来源片段中的事实，不补充来源外知识。
- 输出严格 JSON 数组，不要 Markdown 代码块，不要解释性文字。
- 每张卡片都必须包含 title、knowledge、example、question、answer、tags、source_refs。
- knowledge 用 3-6 条短要点表达，可以使用换行分隔。
- question 用于主动回忆，不是面试评分题，不要包含评分标准。
- answer 要能独立回答 question，但保持精炼。
- source_refs 必须引用输入片段中的 filename 和 header_path。
"""

DEPTH_HINTS = {
    "basic": "偏基础记忆：强调定义、边界、关键词和容易混淆的短结论。",
    "understand": "偏例子理解：在核心结论之外，用具体场景解释为什么成立。",
    "interview_expression": "偏面试表达：帮助用户把知识点组织成清晰、可口述的回答。",
}


def build_knowledge_training_prompt(topic_name: str, depth: str, sections_json: str) -> str:
    depth_hint = DEPTH_HINTS.get(depth, DEPTH_HINTS["understand"])
    return f"""请基于下面的知识库片段，为「{topic_name}」生成同等数量的记忆训练卡片。

训练深度：{depth_hint}

输出 JSON 数组，数组元素结构如下：
{{
  "title": "知识点标题",
  "knowledge": "3-6 条核心要点，使用换行或分号分隔",
  "example": "一个直接来自片段语义的具体例子或场景",
  "question": "用于遮住答案时主动回忆的问题",
  "answer": "精炼参考答案",
  "tags": ["标签1", "标签2"],
  "source_refs": [
    {{"filename": "来源文件名", "header_path": "标题路径"}}
  ]
}}

要求：
1. 每个输入片段最多生成一张卡片。
2. 不生成考试分数、评分维度、难度等级。
3. 不要自动扩展到来源外知识。
4. 如果片段信息不足，就生成更窄的问题，不要编造。

知识库片段：
{sections_json}
"""
