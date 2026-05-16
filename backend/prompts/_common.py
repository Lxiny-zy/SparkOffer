"""提示词公共片段。

汇总评分标准、三板块（Java / Python / Agent）锚点示例、术语库、JSON 输出
纪律等跨 prompt 复用的内容，供 prompts/* 与内联 prompt import。

这些常量曾在 5+ 文件里逐字复制，已发生措辞漂移。集中维护一处后，调整
评分标准 / 增补术语只需改一行。
"""

SCORING_RUBRIC = """评分标准（每题 score 范围 0-10，整数）：
- 0-2 完全跑偏：核心概念错误。例：Python 把 GIL 说成 GC 阶段的事；Java 把 JMM 当作 GC 模型；Agent 把 RAG 描述成训练阶段而非推理阶段。
- 3-4 印象式答错：概念有印象但理解错位。例：知道 ThreadLocal 是线程隔离但解释不出 InheritableThreadLocal 行为；知道 asyncio 是并发但与多线程混淆使用边界。
- 5-6 大方向对但浅：能复述流程，无独立思考、缺工程视角。例：能讲 RAG 是检索+生成，但讲不清 chunk 大小、overlap、reranker 对召回的影响。
- 7-8 理解正确且有思考：能联系实际、能反向推导。例：能从 token budget 与上下文窗口反推 chunk 设计原则；能从 happens-before 推出何时需要 volatile。
- 9-10 深入透彻：兼具原理、实战、工程权衡。例：能讲 RAG 在长尾问题上为何失效、混合检索（BM25 + 向量）的工程取舍；能讲 G1 与 ZGC 在不同堆大小下的延迟特性差异。

评分关注三件事：是否理解本质 / 是否有自己思考 / 能否举例或联系工程实践。
"""


ANCHOR_EXAMPLES = """各分档典型回答示例（按三板块各给一例，用作评分锚定）：

【Java】题：解释 ThreadLocal 内存泄漏的成因与规避手段
- 3 分："ThreadLocal 会泄漏内存。"（仅复述结论，缺机制）
- 6 分："ThreadLocal 的 entry 是弱引用 key、强引用 value，线程不结束 value 不会被回收。"（机制对但缺工程视角）
- 8 分："线程池场景下 worker 线程长存，ThreadLocal 不 remove 会让 value 长期挂在 ThreadLocalMap 上；规范做法是 try-finally 显式 remove，框架层（如 TransmittableThreadLocal）也会接管异步任务的清理。"（理解 + 工程实践）

【Python】题：asyncio 和多线程的边界，分别适合什么场景
- 3 分："asyncio 是异步，比多线程快。"（无原理）
- 6 分："asyncio 是单线程协程切换，多线程被 GIL 限制但 IO 密集场景也能用。"（流程对但缺权衡）
- 8 分："CPU 密集走多进程绕开 GIL；IO 密集协程更轻量但要求全链路 async，否则同步调用会阻塞 event loop；混合场景用 run_in_executor 把阻塞调用丢回线程池。"（场景化决策 + 工程权衡）

【Agent】题：Function Calling / Tool Use 失败如何处理
- 3 分："让 LLM 重试。"（笼统）
- 6 分："捕获 JSON 解析错误，retry 重新生成。"（流程对但缺工程深度）
- 8 分："分三类处理：schema 不匹配 → 把校验错误回灌给 LLM 让它修正参数；工具执行失败 → tool 层抛结构化 error 让 LLM 决定降级或换工具；超时或循环 → 直接 fallback 到默认回复并截断 agent loop，避免 token 爆炸。"（多场景 + 工程权衡）
"""


LANGUAGE_TERMINOLOGY = """三板块专业术语锚点（评估候选人术语精确度，错用术语要扣分；不强制原文背诵）：

- Python：GIL / GC（引用计数 + 分代回收）/ asyncio / coroutine / await 点 / event loop / descriptor / metaclass / dataclass / typing / 鸭子类型 / contextvars / 弱引用 / 上下文管理器。
- Java：JVM（堆 / 方法区 / 栈）/ JMM（happens-before / volatile / synchronized / final 重排）/ CAS / AQS / Spring（IoC / AOP / Bean 生命周期）/ Netty Reactor / Mybatis / GC（CMS / G1 / ZGC）/ 类加载机制 / 双亲委派。
- Agent：LLM / function calling / tool use / RAG / chunking / overlap / embedding / reranker / hybrid retrieval / agent loop / ReAct / planning / memory（short-term / long-term / episodic）/ context window / token budget / streaming / structured output / guardrails / eval / multi-agent / human-in-the-loop。
"""


JSON_OUTPUT_DISCIPLINE = """JSON 输出纪律：
- 只返回 JSON 对象本体；不要在 JSON 前后写解释、客套话或总结。
- 字符串字段内的引号必须正确转义，禁止用未配对的中文引号代替。
- 数组允许为空 []，但绝不可省略键，否则下游解析会拿到 None。
- 不要在 JSON 内插入注释（// 或 #），不要把 JSON 包在 ```json``` 代码块里（除非该 JSON 是要展示给候选人看的示例答案）。
"""


PILLAR_ENUM = '"java" | "python" | "agent" | "general"'
"""字段值域：归类候选人当前讨论的技术板块，便于画像归因和趋势分析。"""


def with_rubric(prompt: str) -> str:
    """便捷工具：在 prompt 末尾追加评分标准和锚点示例。"""
    return f"{prompt}\n\n{SCORING_RUBRIC}\n\n{ANCHOR_EXAMPLES}"
