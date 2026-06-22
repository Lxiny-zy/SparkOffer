"""Prompts for read-only knowledge training cards."""

KNOWLEDGE_TRAINING_SYSTEM = """你是一个技术知识记忆卡片生成器。

你的任务是把用户知识库中的原始片段改写成便于主动回忆的训练卡片。

必须遵守：
- 只使用给定来源片段中的事实，不补充来源外知识。
- 输出严格 JSON 数组，不要 Markdown 代码块，不要解释性文字。
- 每张卡片都必须包含 title、knowledge、example、question、answer、tags、source_refs。
- knowledge 必须是 3-5 条“完整句子”的知识要点，不要把命令、路径、天数安排拆成孤立短语。
- question 用于主动回忆，不是面试评分题，不要包含评分标准。
- answer 要能独立回答 question，包含关键判断和适用边界，但保持精炼。
- source_refs 必须引用输入片段中的 filename 和 header_path。
- 如果片段只是学习计划、目录、题量安排、打卡清单、泛泛建议，而不是技术知识点，直接跳过，不要生成卡片。
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
  "knowledge": "3-5 条完整知识要点，每条都必须能独立理解，用换行分隔",
  "example": "一个能帮助理解该知识点的具体例子、命令或工程场景",
  "question": "用于遮住答案时主动回忆的问题",
  "answer": "精炼参考答案",
  "tags": ["标签1", "标签2"],
  "source_refs": [
    {{"filename": "来源文件名", "header_path": "标题路径"}}
  ]
}}

要求：
1. 每个输入片段最多生成一张卡片；非知识片段可以不生成，所以输出数组长度可以少于输入片段数。
2. 不生成考试分数、评分维度、难度等级。
3. 不要自动扩展到来源外知识。
4. 不要照抄原文碎句；必须把零散命令/列表综合成“概念 + 用法 + 边界”的可记忆表述。
5. 对命令类片段，knowledge 应说明命令用途、关键参数含义、典型场景；example 放完整命令。
6. 对对比类片段，knowledge 应说明差异维度、适用场景和选择依据。
7. 如果片段信息不足，就生成更窄的问题，不要编造。

坏例子（不要这样写）：
- "示例路径是 /app/cache"
- "mount 写法中 target=/app/cache"
- "Day 1-3：数组 + 字符串，25 题"

好例子（应该这样写）：
- "Docker tmpfs 是挂载在内存中的临时文件系统，适合缓存或敏感临时数据，容器删除后不会持久化。"
- "`--tmpfs /app/cache` 是简写方式，适合快速挂载；`--mount type=tmpfs,target=/app/cache,tmpfs-size=100m` 更显式，适合声明大小等参数。"
- "grep 用于按模式过滤行，sed 更适合流式替换和编辑，awk 更适合按列分析、统计和格式化输出。"

知识库片段：
{sections_json}
"""
