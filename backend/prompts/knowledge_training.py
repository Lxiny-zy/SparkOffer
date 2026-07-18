"""Prompts for read-only knowledge training cards."""

KNOWLEDGE_TRAINING_SYSTEM = """你是一个技术知识训练卡片生成器。

你的任务是把用户知识库中的原始片段改写成便于主动回忆的训练卡片。卡片要像一个讲得清楚的小知识点，而不是原文摘抄。

必须遵守：
- 只使用给定来源片段中的事实，不补充来源外知识。
- 输出严格 JSON 数组，不要把整个 JSON 包在 Markdown 代码块里，不要输出解释性文字。
- 字段值可以使用 Markdown；如果原始片段包含代码、命令、配置或 SQL，必须保留为行内代码或 fenced code block。
- 每张卡片都必须包含 title、knowledge、example、question、answer、tags、source_index、source_refs。
- knowledge 必须是 3-5 条 Markdown 列表项，每条都是完整句子，按“概念/机制 -> 用法/场景 -> 边界/易错点”的顺序组织。
- question 用于主动回忆，不是面试评分题，不要包含评分标准。
- answer 是卡片的核心：要写成候选人在面试中能完整说出口的参考回答，按“结论 -> 机制/推导 -> 边界或易错点”展开，能独立回答 question，不依赖 knowledge 也成立。禁止只给一两句结论。
- source_index 必须引用输入片段中的 index；source_refs 必须引用同一片段的 filename 和 header_path。
- 如果片段只是学习计划、目录、题量安排、打卡清单、泛泛建议，而不是技术知识点，直接跳过，不要生成卡片。
"""

DEPTH_HINTS = {
    "basic": "偏基础记忆：强调定义、边界、关键词和容易混淆的短结论。",
    "understand": "偏例子理解：在核心结论之外，用具体场景解释为什么成立。",
    "interview_expression": "偏面试表达：帮助用户把知识点组织成清晰、可口述的回答。",
}


def build_knowledge_training_prompt(topic_name: str, depth: str, sections_json: str, target_count: int) -> str:
    depth_hint = DEPTH_HINTS.get(depth, DEPTH_HINTS["understand"])
    return f"""请基于下面的知识库片段，为「{topic_name}」生成最多 {target_count} 张高质量记忆训练卡片。
训练深度：{depth_hint}

输出 JSON 数组，数组元素结构如下：
{{
  "title": "知识点标题，聚焦一个可讲清楚的概念",
  "knowledge": "- 第一条完整知识要点\n- 第二条完整知识要点\n- 第三条完整知识要点",
  "example": "一个能帮助理解该知识点的具体例子、命令、配置、代码或工程场景",
  "question": "用于遮住答案时主动回忆的问题",
  "answer": "结构化参考答案（见下方 answer 展开要求），用 Markdown 列表或代码块",
  "tags": ["标签1", "标签2"],
  "source_index": 1,
  "source_refs": [
    {{"filename": "来源文件名", "header_path": "标题路径"}}
  ]
}}

生成要求：
1. 不要追求覆盖所有片段，优先选择信息密度高、能讲清楚、能形成完整卡片的片段。
2. 每张卡片只讲一个中心知识点；如果一个片段很散，只抽取其中最清晰的一点。
3. knowledge 必须按阅读顺序组织：先给定义或结论，再说明使用场景，再补充限制、边界或易错点。
4. example 必须具体；命令、代码、配置、SQL、正则表达式要使用 Markdown 代码格式。
5. question 要问“为什么/如何/什么时候/有什么区别/会踩什么坑”，避免只问“请解释 XX”。
6. answer 按三层展开，来源片段有料就写满（通常 80-200 字）：
   - **结论**：先用 1-2 句直接回答 question 的问法（问"为什么"就答原因，问"区别"就点出关键差异）
   - **机制/推导**：说清结论为什么成立——原理、执行过程、对比维度；来源片段里的关键代码/命令/参数在这里引用
   - **边界/易错点**：什么场景下不适用、常见的错误理解是什么
   来源片段撑不起某一层就省略该层，**不编造**；但不允许只给一层——只有一句结论的答案对着 question 复习时没有回忆量。
7. answer 不要照抄 knowledge 的列表——knowledge 是"看"的要点清单，answer 是"说"的完整回答，两者组织方式必须不同。
8. source_index 必须是输入片段里的 index；source_refs 必须与 source_index 对应。
9. 不生成考试分数、评分维度、难度等级。
10. 不要自动扩展到来源外知识；信息不足就跳过该片段。

坏例子（不要这样写）：
- "示例路径是 /app/cache"
- "mount 写法为 target=/app/cache"
- "Day 1-3：数组 + 字符串，25 题"
- answer 只有一句："tmpfs 是内存文件系统。"（无机制、无边界，没有回忆量）

好例子（应该这样写）：
- "Docker tmpfs 是挂载在内存中的临时文件系统，适合缓存或敏感临时数据，容器删除后不会持久化。"
- "`--tmpfs /app/cache` 是简写方式，适合快速挂载；`--mount type=tmpfs,target=/app/cache,tmpfs-size=100m` 更显式，适合声明大小等参数。"
- "grep 用于按模式过滤行，sed 更适合流式替换和编辑，awk 更适合按列分析、统计和格式化输出。"
- answer 三层展开："**结论**：tmpfs 挂载的数据存在内存里，容器停止即消失，所以适合缓存而非持久化数据。**机制**：它绕过了容器的写层（overlayfs），读写直接落在内存页上，速度快且不会写坏镜像层；`--mount type=tmpfs,tmpfs-size=100m` 可以限制内存占用。**易错点**：误把需要持久化的数据放进 tmpfs，重启后丢失；不设 size 上限时大文件会耗尽宿主内存。"

知识库片段：
{sections_json}
"""
