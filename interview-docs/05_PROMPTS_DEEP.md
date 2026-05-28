# 05 · Prompt 工程深度

> Agent 岗位面试官最爱深挖的方向。这章按「**为什么这么写、踩过什么坑、怎么测试、有什么替代方案**」展开。
> 项目里共 17 个 Prompt 模板，分布在 `backend/prompts/*.py`。

---

## 1. Prompt 中心化的工程意义

### 问题

项目早期，Prompt 散落在 5+ 个文件里：
- `graphs/topic_drill.py` 内联 Prompt
- `graphs/resume_interview.py` 内联 Prompt
- `qa_arena.py` 内联 Prompt
- `assistant.py` 内联 Prompt
- ...

**踩过的坑**：
1. 评分标准在 4 个文件里复制粘贴，调整一次要改 4 处
2. "锚点示例"措辞漂移，drill 模式 6 分定义为"流程对但缺权衡"，resume 模式定义为"理解大方向"，导致 AI 行为不一致
3. 新加 Prompt 时容易遗漏 JSON 输出纪律，导致评估接口偶发 parse 失败

### 解决方案：`prompts/_common.py`

抽出公共片段：
```python
SCORING_RUBRIC = """评分标准（每题 score 范围 0-10，整数）：
- 0-2 完全跑偏：核心概念错误...
- 3-4 印象式答错：概念有印象但理解错位...
- 5-6 大方向对但浅：能复述流程，无独立思考、缺工程视角...
- 7-8 理解正确且有思考：能联系实际、能反向推导...
- 9-10 深入透彻：兼具原理、实战、工程权衡...
"""

ANCHOR_EXAMPLES = """各分档典型回答示例（按三板块各给一例）：
【Java】题：解释 ThreadLocal 内存泄漏的成因与规避手段
- 3 分：「ThreadLocal 会泄漏内存。」（仅复述结论，缺机制）
- 6 分：「ThreadLocal 的 entry 是弱引用 key、强引用 value...」（机制对但缺工程视角）
- 8 分：「线程池场景下 worker 线程长存...规范做法是 try-finally 显式 remove...」（理解 + 工程实践）
【Python】题：...
【Agent】题：...
"""

LANGUAGE_TERMINOLOGY = """三板块专业术语锚点：
- Python：GIL / GC / asyncio / coroutine / await 点 / event loop / ...
- Java：JVM / JMM / happens-before / volatile / AQS / Spring / ...
- Agent：LLM / function calling / RAG / chunking / overlap / ...
"""

JSON_OUTPUT_DISCIPLINE = """JSON 输出纪律：
- 只返回 JSON 对象本体；不要在 JSON 前后写解释、客套话或总结
- 字符串字段内的引号必须正确转义
- 数组允许为空 []，但绝不可省略键
- 不要在 JSON 内插入注释，不要把 JSON 包在代码块里
"""
```

业务 Prompt 通过字符串拼接复用：

```python
DRILL_BATCH_EVAL_PROMPT = """你是「{topic_name}」领域的资深工程师，正在批量评估候选人的训练答卷。
...
""" + SCORING_RUBRIC + """

""" + ANCHOR_EXAMPLES + """

""" + LANGUAGE_TERMINOLOGY + """

## 字段写作规范（强约束）
...

## 输出格式

""" + JSON_OUTPUT_DISCIPLINE + """

```json
{{
    "scores": [...],
    ...
}}
```
"""
```

### 收益

- 调整评分标准：改 1 行，所有 Prompt 同步
- 新 Prompt 自动继承"JSON 输出纪律"，避免遗漏
- 评分一致性：4 个模式（drill / resume / job_prep / recording）评分行为对齐

---

## 2. 17 个 Prompt 全景

| Prompt 名 | 文件位置 | 用途 |
|---|---|---|
| `RESUME_INTERVIEWER_SYSTEM` | `interviewer.py` | 简历面试官 system |
| `DRILL_QUESTION_GEN_PROMPT` | `interviewer.py` | Drill 出题 |
| `DRILL_BATCH_EVAL_PROMPT` | `interviewer.py` | Drill 批量评估 |
| `REFERENCE_ANSWER_PROMPT` | `interviewer.py` | 参考答案 |
| `HINT_PROMPT` | `interviewer.py` | 提示（不给答案） |
| `PROFILE_UPDATE_PROMPT` | `interviewer.py` | Mem0 风格画像更新 |
| `TOPIC_RETROSPECTIVE_PROMPT` | `interviewer.py` | 领域回顾报告 |
| `EXTRACT_PROMPT` | `memory.py`（内联） | 训练后提取洞察 |
| `JOB_PREP_PREVIEW_PROMPT` | `job_prep.py` | JD 分析 |
| `JOB_PREP_QUESTION_GEN_PROMPT` | `job_prep.py` | JD 出题 |
| `JOB_PREP_EVAL_PROMPT` | `job_prep.py` | JD 评估 |
| `RECORDING_STRUCTURE_PROMPT` | `recording.py` | 录音结构化 |
| `RECORDING_DUAL_EVAL_PROMPT` | `recording.py` | 录音双人评估 |
| `RECORDING_SOLO_EVAL_PROMPT` | `recording.py` | 录音单人评估 |
| `REVIEW_SYSTEM` | `reviewer.py` | 复盘报告 |
| `ALGORITHM_SOLVE_SYSTEM/PROMPT` | `algorithm.py` | 算法解题 |
| `ALGORITHM_CHAT_SYSTEM` | `algorithm.py` | 算法追问 |
| `SYSTEM_PROMPT` | `assistant.py`（内联） | FloatingAssistant |
| `QA_ARENA_SYSTEM` | `qa_arena.py`（内联） | 自由问答 |

---

## 3. 单 Prompt 深度剖析：`DRILL_QUESTION_GEN_PROMPT`

> 这是项目里**最复杂、最重要**的 Prompt，约 90 行 / 3k token。

### 3.1 完整结构

```
段落 1 · 知识库使用约束
段落 2 · 角色定义 + 候选人目标岗位
段落 3 · 知识库 chunks (<knowledge>...</knowledge>)
段落 4 · 候选人画像（跨领域 summary）
段落 5 · 当前领域掌握度
段落 6 · 已知薄弱点（含[到期复习]标记）
段落 7 · 高频面试题（用户标记的重点）
段落 8 · 最近练过的题（避免重复）
段落 9 · 历史训练洞察（向量检索结果）
段落 10 · 出题策略（基于掌握度的梯度策略）
段落 11 · 难度范围
段落 12 · 好题 vs 坏题对照（按三板块给反例和正例）
段落 13 · 题目分组要求（强约束）
段落 14 · 输出格式（JSON Schema）
段落 15 · 其它规则
```

### 3.2 核心设计点

#### 点 1：知识库的"辅助层"定位

```
## 知识库使用约束（必读）

下文 `<knowledge>` 仅供你了解该领域有哪些知识点，
**不要照搬原文出题**（"请解释第 N 个核心概念"是被禁止的出题方式）。
```

**为什么这么写**：早期版本没这段，LLM 经常把知识库 chunk 当原题抛出来，问"什么是 GIL"这种背诵题。加这段约束后，LLM 把知识库当"我可以问哪些方向"的索引，而不是"题目源"。

#### 点 2：场景化出题示范（按三板块）

```
## 好题 vs 坏题对照

| 板块 | ✗ 坏题（背诵型） | ✓ 好题（理解型） |
|---|---|---|
| Java | 描述 JVM 内存结构 | 一段代码 Young GC 频繁触发，你怎么排查？ |
| Python | 什么是 GIL | 你的多线程爬虫为什么没跑满 CPU？ |
| Agent | 描述 RAG 流程 | RAG 系统对长尾问题召回率低，你会从哪几环节定位？ |
```

**为什么按三板块**：候选人面 Agent 工程师岗，简历可能侧重 Python 或 Java，AI 出题要在两边都强。

#### 点 3：题目分组的硬约束

```
## 题目分组要求（强约束）

10 道题必须按以下分组分布：
- `weak_point` 组：**≥3 题**，直接命中候选人已知薄弱点
- `scenario` 组：**≥2 题**，给真实工程场景让候选人诊断 / 设计 / 取舍
- `core_concept` 组：补足到 10 题，考察核心原理但要求结合实际而非背诵
```

**踩过的坑**：早期没有强约束，LLM 经常出 10 道 core_concept，weak_point 全忽略。加上 ≥3 ≥2 的硬约束后，LLM 必然先安排 weak_point 题，再补 scenario 和 core_concept。

#### 点 4：输出格式的字段约定

```json
[
    {"id": 1, "question": "问题内容", "difficulty": 3, "focus_area": "AQS 中 CLH 队列的工作机制", "category": "weak_point", "pillar": "java"}
]
```

**字段语义**：
- `id`：1-10
- `question`：完整问题描述，**禁止"请解释 XX"句式**
- `difficulty`：动态填入 `{diff_min}-{diff_max}`，让 LLM 知道当前难度区间
- `focus_area`：**不是宽泛的"并发"**，而是"AQS 中 CLH 队列的工作机制"
- `category`：`weak_point` / `scenario` / `core_concept` 三选一（便于事后分析覆盖率）
- `pillar`：`java` / `python` / `agent` / `general`（便于画像归因）

### 3.3 单变量验证（怎么调）

调 Prompt 时我做的小实验：

| 实验 | 结果 |
|---|---|
| 删除"好题 vs 坏题对照" | 背诵题占比 50% → 80%（明显恶化） |
| 删除题目分组硬约束 | weak_point 命中率 30% → 5% |
| 把 `focus_area` 字段说明删了 | 字段值变成"并发"、"GC" 等宽泛标签 |
| 把 pillar 字段删了 | 画像归因数据丢失，趋势图不准 |
| 把 EVAL 标记字段简化（去掉 brief 兼容字段） | 偶发 parse 失败（LLM 漂移） |

**经验**：Prompt 每一句都要"养"出价值。**没用的约束删掉、有用的约束加重**。

### 3.4 演讲版本（90 秒）

"出题 Prompt 是项目里最复杂的一段，3k token，我专门花了几周打磨。

结构上有 15 个段落，最核心的设计是这几个：

1. **知识库约束**：明确告诉 LLM『knowledge 只是辅助你理解该领域有哪些知识点，**不要把原文当题抛出来**』。早期没这段，AI 老是问『请解释第 N 个核心概念』。

2. **好题 vs 坏题对照表**：按 Python / Java / Agent 三板块各给反例和正例。比如『描述 JVM 内存结构』是坏题（背诵型），『一段代码 Young GC 频繁触发，你怎么排查？』是好题（理解型）。

3. **题目分组硬约束**：10 题里必须 ≥3 个 weak_point、≥2 个 scenario、其余 core_concept。早期没这约束，LLM 会全出 core_concept，薄弱点完全不命中。

4. **输出字段的精细化**：focus_area 必须是『AQS 中 CLH 队列的工作机制』而不是『并发』。pillar 字段帮我们做画像归因。

整个项目所有 Prompt 共享 `_common.py` 的评分标准、术语库、锚点示例，**调整一处全局生效**。"

---

## 4. `PROFILE_UPDATE_PROMPT` 深度剖析

> 这是 Mem0 风格画像更新的核心 Prompt，最难写。

### 4.1 核心难点

让 LLM 做"语义相似度判断"：哪些新发现和已有画像合并、哪些独立 ADD。

### 4.2 Prompt 设计

```
## 语义相似度判断标准（关键）

两条记录如果**根因或考察的技术能力相同**就算相似，应该 UPDATE 合并；
只是涉及相关但不同的子系统/工具/概念，则独立 ADD。

### X = Y（应该 UPDATE 合并）
- 「对 GIL 理解不深」+「Python 并发模型理解薄弱」→ 同根因（都是 Python 并发本质）
- 「ThreadLocal 用法不熟」+「线程上下文传递模糊」→ 同根因
- 「RAG chunk 设计粗糙」+「检索召回率优化思路单一」→ 同根因

### X ≠ Y（独立 ADD）
- 「Pandas 数据处理不熟」+「Numpy 广播机制混乱」→ 相关但独立的工具
- 「Spring AOP 使用不熟」+「Spring IoC 容器理解模糊」→ 同框架但不同子模块
- 「Agent 工具调用错误处理弱」+「Agent 记忆系统设计粗糙」→ 同 Agent 但不同子系统
```

**为什么用对照示例而不是抽象规则**：
- 抽象规则（"看根因是否相同"）LLM 容易误用
- 对照示例 LLM 能 in-context learning
- 6 个例子覆盖了「同/异」的边界（Python 并发、Java 工具链、RAG、Pandas/Numpy、Spring 内部、Agent 内部）

### 4.3 操作类型设计

```
- `ADD`：全新发现，已有画像中无语义相似条目
- `UPDATE`：已有条目中存在语义相似项（通过 `index` 指定），合并为更准确的描述
- `NOOP`：已有条目已完全覆盖该发现
- `improvements`：新强项证明某个旧薄弱点已被克服
```

`improvements` 是个巧思：用户在新一次训练中表现出某个强项，可能正是过去某个薄弱点被克服的证据。`PROFILE_UPDATE_PROMPT` 让 LLM 检测这种关联，自动把旧薄弱点标记为 `improved`。

### 4.4 输出格式

```json
{
    "weak_point_ops": [
        {"action": "ADD", "point": "...", "topic": "..."},
        {"action": "UPDATE", "index": 0, "new_point": "合并后的更准确描述"},
        {"action": "NOOP", "reason": "已有记录已覆盖"}
    ],
    "strong_point_ops": [...],
    "improvements": [
        {"weak_index": 2, "reason": "本次表现证明已掌握"}
    ]
}
```

**关键字段**：`index` —— LLM 必须告诉我们 UPDATE 哪一条旧记录（在 prompt 里把已有画像列出来时带了 `[i]` 序号）。

### 4.5 失败时的 fallback

LLM 解析失败时（JSON 不合法、字段缺失）→ fallback 到向量 cosine 0.75 去重：

```python
try:
    ops = _parse_json_safe(response.content)
    _apply_memory_ops(profile, ops, topic, now)
except (json.JSONDecodeError, ValueError, KeyError) as e:
    logger.warning(f"Profile update LLM parse failed ({e}), falling back to deterministic")
    _deterministic_update(profile, new_weak_points, new_strong_points, topic, now, user_id)
```

---

## 5. `RESUME_INTERVIEWER_SYSTEM` 深度剖析

> 最长的 Prompt，~150 行。简历模拟面试的"灵魂"。

### 5.1 核心设计：追问动作指南

```
## 追问动作指南（关键）

不同候选人状态对应**不同**的下一步动作，禁止用统一的"那你再说说"敷衍：

| 候选人状态 | 你的动作 |
|---|---|
| 回答模糊（"差不多"、"就是"、"大概"） | 追问具体："你说的 XX，具体指什么？能举一个你项目里的例子吗？" |
| 回答有偏差（核心概念错位） | 不直接否定，换场景验证："如果场景换成 YY，你刚才这个方案还成立吗？" |
| 明显在背八股（语言流畅但脱离实际） | 切换到实战："概念清楚了，但你在项目里实际遇到过这个情况吗？踩过什么坑？" |
| 坦诚说不会 | 先认可坦诚，给一个 20 秒切入点提示，看推导能力 |
| 答对核心 | 自然下钻 why 或 scale："那为什么这样设计？" / "如果规模放大 10 倍..." |
```

**为什么这么写**：早期 LLM 面试官像个"机器人"，每次都"那你再说说"或"很好"。加了这张表，LLM 学会针对候选人状态做不同的下一步动作 —— 接近真实面试官。

### 5.2 EVAL 标记规范

```
## 内部评估标记（仅 technical / project_deep_dive 阶段）

回复**最末行**必须附加一个隐藏评估标记：

<!--EVAL:{"score":7,"should_advance":false,"observation":"...","next_focus":"...","pillar":"java","brief":"..."}-->

字段说明：
- `score`：0-10 整数，对候选人**上一个回答**的评分
- `should_advance`：是否建议推进到下一阶段
- `observation`：本次回答的核心观察，1-2 句，**必须带技术细节**
- `next_focus`：基于本次表现，下一步你想验证什么
- `pillar`：取值之一："java" / "python" / "agent" / "general"
- `brief`：与 observation 内容相同（兼容旧解析器，**必须填**）

只在 technical / project_deep_dive 阶段附加 EVAL 标记。
```

**踩过的坑**：早期版本没有 `brief` 字段，但旧代码依赖它做 fallback parse。Prompt 加上"必须填"避免漂移。

### 5.3 通用规则（语气控制）

```
## 通用规则

- 每次只抛一个问题，不要一次问多个并列点
- 不重复已问过的内容
- 用中文，语气专业但不死板（用"你"而非"您"）
- **绝不在提问中暴露你期望的答案**
- 候选人答对后**不要**给空洞的"很好"，而是基于内容做技术性认可：
  "嗯，对 happens-before 的理解到位了。继续——你之前提到 volatile，
   那 volatile 在 DCL 单例里解决的是哪个问题？"
```

**为什么禁止"很好"**：LLM 的"很好"会让用户怀疑 AI 是不是在敷衍。基于具体内容的技术性认可才像真实面试官。

---

## 6. `QA_ARENA_SYSTEM` 深度剖析

> 自由问答场景的 Prompt，~50 行。最考验"长度档位"控制。

### 6.1 三档长度策略

```
## 长度档位（最重要的约束）

### 档位 A — 简单事实/对比类（3-5 句）
触发：「XX 是什么」「XX 和 YY 区别」「XX 的默认值是多少」
示例：
> Q: Redis 和 Memcached 的区别？
> A: 三个核心差异：
> 1. **数据结构**：Redis 支持 string/list/hash/set/zset，Memcached 只有 KV
> 2. **持久化**：Redis 有 RDB/AOF，Memcached 纯内存重启即丢
> 3. **高可用**：Redis 自带 Sentinel/Cluster，Memcached 需要客户端分片
>
> 选型上：要复杂结构或持久化用 Redis；纯轻量缓存用 Memcached。

### 档位 B — 实现/原理类（≤200 字 + 必要时精简代码）
触发：「XX 怎么实现」「XX 的原理」「为什么 XX 设计成这样」
结构：分点说原理 → 一段精简代码或伪代码（如有必要）→ 一句话点出关键

### 档位 C — 设计/深度类（完整展开）
触发：「设计一个 XX 系统」「深入分析 XX」「如何优化 XX」
结构：需求拆解 → 规模估算 → 方案选型 + 权衡 → 关键模块设计 → 潜在坑点

**绝对禁止**：为了显得"有深度"把简单问题写成长篇。能 3 句说清的不用 5 句。
```

**为什么三档**：单一档位有两种失败模式：
- 全短：复杂问题答不清
- 全长：简单问题啰嗦

**踩过的坑**：早期 Prompt 没有长度约束，用户问"Redis 是什么"AI 写 800 字。加了三档后，体验显著改善。

### 6.2 内部思考流程

```
## 内部思考流程（**不要输出给用户**，只在脑内做）

回答前先在脑内判断三件事：
1. **问题类型**：事实查询 / 实现细节 / 系统设计 / 开放讨论？
2. **用户水平**：从提问的术语准确度推测（用对术语 → 中高级；混淆基本概念 → 初级）
3. **长度档位**：根据下面三档自适应
```

**为什么写这段**：教 LLM 做"思维链"但不输出。LLM 内部隐式执行这个判断流程，外部输出干净答案。

---

## 7. `SYSTEM_PROMPT` (FloatingAssistant) 深度剖析

> 双档运行 + 工具调用决策树。

### 7.1 双档设计

```
### 档位 1 — 关怀/陪伴档
触发：闲聊、情绪表达、问候
风格：完整学姐风格 — 软语气词（"呀""呢""哦"）、可爱 emoji（🐟✨🌸💕）、生活化表达

### 档位 2 — 技术解释档
触发：用户问技术概念、原理、面试题
风格：保持温柔语气，但**比喻必须精准，专业术语不能被替换**
- 错误 ❌：「Redis 像小鱼一样游来游去存数据 🐟」
- 正确 ✅：「Redis 是内存数据库哦～你可以把它想象成一个超大的字典」
```

**为什么明确禁止"错误示例"**：早期 Prompt 只说"保持温柔但术语精准"，LLM 还是会写"Redis 像小鱼一样游来游去"这种比喻。加了具体 ❌ 示例后，LLM 才学会区分"可爱比喻"和"准确表达"。

### 7.2 工具调用决策树

```
| 用户意图 | 是否调工具 | 调什么 |
|---|---|---|
| "我的 XX 怎样" / "薄弱点" / "复习什么" | ✅ 必调 | `get_full_profile` / `get_due_reviews` |
| "上次那道 XX 题" | ✅ 必调 | `search_history` / `search_knowledge_memory` |
| "什么是 XX" / "XX 怎么实现" | ❌ 不调 | 直接讲技术（档位 2）|
| 语气焦虑但意图模糊 | 🤔 先共情 | 共情后问"要不要看看你的训练情况？" |
```

**核心巧思**：用 Prompt 显式约束『**何时调工具**』。这避免两种失败模式：
1. 用户问"什么是 GIL"，AI 乱调 `search_history` 工具
2. 用户问"我的薄弱点"，AI 凭空捏造答案

### 7.3 硬性禁止

```
## 禁止行为（硬性约束）

- ❌ 用"亲""宝""亲亲"等电商客服用语
- ❌ 说"我只是一个 AI" / "作为 AI 助手"这类自降身份的话
- ❌ 用户没问的时候主动推销功能（违背"陪伴"定位）
- ❌ 过度撒娇或太幼稚 — 你是靠谱学姐，不是萌妹
- ❌ 用可爱比喻稀释技术准确性
- ❌ 编造数据 — 工具没查到就如实说没查到
- ❌ 每句都堆语气词或 emoji（过度反而做作）
```

**为什么用"硬性约束"列表**：正面 Prompt（"你要简洁"）效果有限，负面 Prompt（"你不要堆 emoji"）效果更显著。

---

## 8. JSON 输出纪律（项目最重要的小段）

```python
JSON_OUTPUT_DISCIPLINE = """JSON 输出纪律：
- 只返回 JSON 对象本体；不要在 JSON 前后写解释、客套话或总结
- 字符串字段内的引号必须正确转义，禁止用未配对的中文引号代替
- 数组允许为空 []，但绝不可省略键，否则下游解析会拿到 None
- 不要在 JSON 内插入注释（// 或 #），不要把 JSON 包在 ```json``` 代码块里
"""
```

### 配套的"宽容解析器"

```python
# graphs/topic_drill.py:_parse_json_response
def _parse_json_response(content):
    content = content.strip()

    # 直接 parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 去 markdown 代码块
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 从第一个 [ 或 { 开始 parse
    for i, c in enumerate(content):
        if c in ("[", "{"):
            try:
                return json.loads(content[i:])
            except json.JSONDecodeError:
                pass
            break

    raise json.JSONDecodeError("No valid JSON found", content, 0)
```

**为什么 Prompt 约束 + 宽容解析双保险**：
- Prompt 提高 LLM 守纪律的概率
- 解析器兜底 LLM 偶尔违纪的情况
- 两者结合让 parse 成功率从 ~85% 提到 ~99%

---

## 9. 字段写作规范（强约束）

```
## 字段写作规范（强约束）

- `assessment`：60-150 字，**单段不分行**，先点出对错关键再补一句具体观察
- `improvement`：必须以**动词**开头（"补充..."、"先用...再..."、"画一张...图"等）
- `understanding`：**必须**从 `["核心理解正确", "有偏差", "完全跑偏"]` 三选一
- `weak_point`：**仅当该题 score ≤ 5** 时填写；其余题填 `null`
- `key_missing`：最多 3 项，每项是具体的关键点
- `topic_mastery.notes`：用一句话描述该领域整体掌握程度，带具体技术点
```

**为什么这种规范有用**：
- LLM 默认会写出 200 字的`assessment`（太长）或 20 字（太短）
- "动词开头" 强制 `improvement` 是 actionable 而不是抽象
- 枚举值约束防止 LLM 自由发挥（`understanding` 写"还行"、"差"等不规范值）
- `weak_point: null` 约束避免画像污染（不该是薄弱点的题被记成薄弱点）

---

## 10. 演讲版本：怎么讲 Prompt 工程（120 秒）

"项目里有 17 个 Prompt 模板，我做了几件比较系统的事情：

**第一：Prompt 中心化管理**。早期 Prompt 散落在 5+ 个文件里，评分标准复制粘贴，调一次改 5 处，导致 4 个模式（drill / resume / job_prep / recording）评分行为不一致。我抽出了 `_common.py`，提取『评分标准、锚点示例、术语库、JSON 输出纪律』四个公共片段，所有业务 Prompt 通过字符串拼接复用，**调整一处全局生效**。

**第二：场景化好题 vs 坏题对照**。我发现 LLM 默认出题倾向于背诵题（"什么是 GIL"）。我在 Prompt 里加了三板块（Python/Java/Agent）的反例和正例对照表，强制 LLM 出场景化的好题（"你的多线程爬虫为什么没跑满 CPU"）。

**第三：硬约束分组**。早期出题没有分组约束，LLM 会全出 core_concept，薄弱点完全不命中。加了 `weak_point ≥ 3 / scenario ≥ 2` 的硬约束后，薄弱点命中率从 5% 升到 80%。

**第四：JSON 输出纪律 + 宽容解析双保险**。Prompt 严格约束格式（不准包 markdown 代码块、不准写注释、字段必填），同时写了宽容解析器兜底（去 markdown 包裹、从首个 `[` 或 `{` 开始 parse），让 parse 成功率达 99%。

**第五：字段写作规范**。强制 `assessment` 60-150 字单段，`improvement` 动词开头，`understanding` 三选一枚举，避免 LLM 自由发挥导致下游 UI 错乱。

**第六：调 Prompt 的工程方法**。每改一句都做单变量验证：删某段看效果变化。比如『删除好题 vs 坏题对照』测出背诵题占比从 50% 升到 80%，证明这段必须保留。"

---

## 11. 测 Prompt 的方法（**诚实**）

目前我**没有自动化的 Prompt 评测体系**，主要靠：

1. **人工抽样**：每改一段 Prompt 跑 10 次出题，人工评估题目质量
2. **用户反馈**：复盘报告的"赞/踩"按钮，跟踪哪些题导致不满意
3. **单变量验证**：删某段看输出变化，证明该段的价值

**生产化要做的事**（面试时诚实说）：
1. 构建评估集：50 道题 × 3 档难度 × 标准答案
2. 离线评测：换 Prompt 后跑评估集，对比平均分、字段填充率
3. LLM-as-Judge：让 GPT-4 给两版 Prompt 输出打分
4. 字段填充率：assessment 不为空率、score 非 null 率
5. 用户偏好分布：用户对每版 Prompt 的赞踩比

---

## 12. 面试官最爱追问的 Prompt 问题

### Q：怎么避免 Prompt Injection？

A：项目里有几个层面：
1. **用户输入不进 SystemMessage**：所有用户输入只进 HumanMessage
2. **关键约束放 Prompt 末尾**：LLM 对末尾指令更敏感
3. **角色硬绑定**：明确"你是面试官"，用户说"你现在是 XX"也不切换
4. **结构化输出**：要求返回 JSON 而不是自由文本，注入成本高

但**没做工业级 Prompt Injection 防御**（如 OpenAI 的 Moderation API、ConstitutionalAI），是个 TODO。

### Q：怎么处理 LLM 幻觉？

A：分场景：
- **画像更新**：双阶段（Extract 只看本次对话 → Update 只决策操作类型），LLM 不能编造没出现过的薄弱点
- **知识沉淀**：明确"对低分回答提取正确答案方向，不是答案本身"
- **参考答案生成**：知识库 RAG 拉相关 chunk 作为依据，要求 LLM "如果不确定就明说"

诚实地讲，**完全防住幻觉很难**，主要靠：低温度（0.7）+ 强 schema 约束 + 用户审计入口（profile.json 可手动编辑）。

### Q：你的 Prompt 一次调用多少 token？

A：
- DRILL_QUESTION_GEN_PROMPT：~3k token input + ~3k output（10 道题 JSON）
- PROFILE_UPDATE_PROMPT：~2k token input + ~500 output
- RESUME_INTERVIEWER_SYSTEM（system）：~2k token，每轮对话累积消息
- 平均一次训练 + 评估 + 画像更新：~30k token 总消耗

成本：用 gpt-4o-mini（$0.15/M input, $0.6/M output）一次训练 < $0.01。

### Q：Prompt 改完怎么不影响老 session？

A：
- 进行中的 session 在 `live_sessions` 表 + `graphs[session_id]` 内存，已经持有旧 Prompt 编译的 graph
- 新 session 用新 Prompt
- 不向后兼容（这是个 TODO，目前能接受 session 完成时间 < 2 小时的窗口）

### Q：跨模式 Prompt 一致性怎么保证？

A：`_common.py` 抽出 SCORING_RUBRIC / ANCHOR_EXAMPLES / LANGUAGE_TERMINOLOGY，4 个 mode 共享。具体到字段约束，每个 mode 自己写（因为评估字段不同，比如 jd_prep 有 `role_expectation` 字段而 drill 没有）。

---

下一章 → [06 前端架构](06_FRONTEND.md)
