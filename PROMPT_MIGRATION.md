# 提示词改动迁移文档（Prompt Migration）

> 生成日期：2026-07-18。本文档完整记录本仓库最近一次针对提示词的未提交改动（基于 commit `8dcf27d` 之上的工作区 diff），用于在另一个已更新的项目中落盘同样的修改。
>
> 涉及文件（共 4 个，其中 2 个为**必须配套**的代码改动）：
>
> | 文件 | 性质 |
> |------|------|
> | `backend/prompts/interviewer.py` | 提示词改动 ×3 |
> | `backend/qa_arena.py` | 提示词改动 ×4（知识卡片整理链路整体重写） |
> | `backend/graphs/decoupled_eval.py` | **必须配套**：新增 `topic_key` 传参 + 输出归一化 |
> | `backend/graphs/resume_interview.py` | **必须配套**：EVAL 标记 `brief` 字段回填 |
>
> 注意：工作区里同时存在的 README 修改、根目录若干 `.md` 文档删除、`interview-docs/` 新增文件均**与本次提示词改动无关**，不在本文档范围内。

---

## 改动总览（三组主题）

1. **简历面试 EVAL 标记瘦身 + 容错**：从 `RESUME_INTERVIEWER_SYSTEM` 的隐藏评估标记里删除冗余的 `brief` 字段（它与 `observation` 内容完全相同），新增单行合法 JSON 的格式硬约束；解析端 `_parse_inline_eval` 增加 `brief ← observation` 回填，保证下游 review 构建器不丢数据。
2. **专项训练（drill）提示词两处**：
   - 出题提示词 `DRILL_QUESTION_GEN_PROMPT`：明确「出题策略」优先级最高——策略给出逐题槽位/比例分布时严格执行，仅在策略未指定时才退回"难度递进 + 前 3 题薄弱点"的默认规则。
   - 整体总结提示词 `DRILL_OVERALL_SUMMARY_PROMPT`：输出 schema 与批量评估（batch eval）统一为**同一套嵌套结构**（此前 decoupled 路径要求扁平字符串，batch 路径要求嵌套对象，下游画像更新只认嵌套结构）。同时删除 LLM 估算的 `topic_mastery.score`（掌握度分数是系统按 `difficulty/5 × score/10` 确定性计算的，不允许 LLM 估算），并新增"看不到原文就禁止编造沟通/思维观察"的约束。配套 `decoupled_eval.py` 新增 `topic_key` 传参与 `_normalize_overall` 归一化兜底。
3. **QA 竞技场知识卡片整理链路重写**（`qa_arena.py` 的 4 个提示词）：核心思想从"按固定小节分类堆放"改为"**按主题聚合、每个主题自包含**"——同一主题的多轮问答合并为一节，对比表格/代码块**就地放在所属主题小节内部**而非抽离到集中的"横向对比"/"关键代码"章节；主题间按「基础概念 → 机制原理 → 应用/调优」逻辑排序而非对话先后；新增「主题之间的关联」「一句话总结」等结构。单次总结（`SUMMARY_SYSTEM` + `SUMMARY_USER_TEMPLATE`）与长对话 map-reduce 路径（`MAP_PROMPT` + `REDUCE_PROMPT`）保持同一套理念。

---

## 一、`backend/prompts/interviewer.py`

### 1.1 `RESUME_INTERVIEWER_SYSTEM` — EVAL 标记删除 `brief` 字段

改动位置：系统提示词中「## 内部评估标记（仅 technical / project_deep_dive 阶段）」小节。

**旧文本（删除）：**

```text
<!--EVAL:{{"score":7,"should_advance":false,"observation":"对 ThreadLocal 的内存泄漏有正确直觉但未提到线程池场景下的清理责任","next_focus":"想追问 try-finally remove 在异步框架里的实践","pillar":"java","brief":"对 ThreadLocal 的内存泄漏有正确直觉但未提到线程池场景下的清理责任"}}-->
```

以及字段说明中的这一行：

```text
- `brief`：与 `observation` 内容相同（兼容旧解析器，**必须填**）
```

**新文本（替换为）：**

```text
<!--EVAL:{{"score":7,"should_advance":false,"observation":"对 ThreadLocal 的内存泄漏有正确直觉但未提到线程池场景下的清理责任","next_focus":"想追问 try-finally remove 在异步框架里的实践","pillar":"java"}}-->
```

字段说明中删除 `brief` 行，并在字段说明列表之后（`""" + SCORING_RUBRIC + """` 拼接之前）新增一段：

```text
标记格式要求：整个 EVAL 注释必须写在**一行内**（JSON 不换行），且是回复的最后一行；JSON 字符串值里不要出现双引号（用中文引号或改写），保证注释体是合法 JSON。
```

改动后该小节完整文本（`{{`/`}}` 为 Python `str.format` 的字面大括号转义，原样保留）：

```text
## 内部评估标记（仅 technical / project_deep_dive 阶段）

在 technical 和 project_deep_dive 阶段，回复**最末行**必须附加一个隐藏评估标记，格式如下（**前端会自动剥离，候选人看不到**）：

<!--EVAL:{{"score":7,"should_advance":false,"observation":"对 ThreadLocal 的内存泄漏有正确直觉但未提到线程池场景下的清理责任","next_focus":"想追问 try-finally remove 在异步框架里的实践","pillar":"java"}}-->

字段说明：
- `score`：0-10 整数，对候选人**上一个回答**的评分
- `should_advance`：是否建议推进到下一阶段（true / false）。当当前阶段已充分考察、或候选人表现明显高出/低于阶段难度时设为 true
- `observation`：本次回答的核心观察，1-2 句，**必须带技术细节**（"未提到线程池场景下的清理责任" √；"理解不深" ✗）
- `next_focus`：基于本次表现，下一步你想验证什么
- `pillar`：本问题所属板块，取值之一："java" / "python" / "agent" / "general"

标记格式要求：整个 EVAL 注释必须写在**一行内**（JSON 不换行），且是回复的最后一行；JSON 字符串值里不要出现双引号（用中文引号或改写），保证注释体是合法 JSON。
```

> **⚠️ 必须配套**：见 [四、`resume_interview.py` 的 `brief` 回填](#四backendgraphsresume_interviewpy必须配套)。只改提示词不改解析器，review 构建器读 `brief` 时会拿到空值。

### 1.2 `DRILL_QUESTION_GEN_PROMPT` — 「出题策略」优先级最高

改动位置：提示词末尾「## 其它规则」小节的前两条。

**旧文本：**

```text
- 题目难度从 {diff_min} 到 {diff_max} **递进**排列
- 前 3 题尽量包含候选人薄弱点（如有），剩余拓展到其他知识点
```

**新文本：**

```text
- 难度分布以上方「出题策略」为最高优先：若策略给出了逐题槽位计划（Slot 1..10）或比例分布，**严格按它执行**；仅当策略未指定具体分布时，才让难度从 {diff_min} 到 {diff_max} 大致递进
- 薄弱点覆盖同理服从「出题策略」；策略未指定时，前 3 题尽量包含候选人薄弱点（如有），剩余拓展到其他知识点
```

「其它规则」小节其余两条（不重复最近练过的题、一题一考点）不变。无配套代码改动（`{diff_min}`/`{diff_max}` 占位符未变）。

### 1.3 `DRILL_OVERALL_SUMMARY_PROMPT` — 输出 schema 与批量评估统一

**核心变化：**
- `new_weak_points` / `new_strong_points`：从字符串数组改为对象数组 `{"point": ..., "topic": ...}`，并引入**新占位符 `{topic_key}`**（⚠️ 必须配套修改 `.format(...)` 调用，否则 `KeyError: 'topic_key'`）。
- `communication_observations`：从扁平字符串改为嵌套对象 `{style_update, new_habits, new_suggestions}`。
- `thinking_patterns`：从扁平字符串改为嵌套对象 `{new_strengths, new_gaps}`。
- `topic_mastery`：**删除 `score` 字段**，只留 `notes`——掌握度分数由系统确定性计算（`difficulty/5 × score/10`），不允许 LLM 估算（项目约定：Mastery scoring is deterministic）。
- 新增"只看得到分数统计、看不到原文，信号不足宁可留空、禁止编造"规则。

**改动后完整提示词（整体替换）：**

```python
DRILL_OVERALL_SUMMARY_PROMPT = """你是「{topic_name}」资深面试官，正在汇总候选人一次训练的整体观察。**只看分数统计，不看完整答卷**。

## 本轮分数统计

{score_stats}

## 候选人画像

{user_profile}

## 输出要求

只返回 JSON 对象（不要数组）。字段结构与批量评估共用**同一套 schema**（嵌套对象，不是字符串）：
{{
  "avg_score": 平均分（float, 保留 1 位小数）,
  "summary": "100-150 字整体观察：候选人本轮表现是稳健 / 偏科 / 退步，对照画像看是否符合预期",
  "new_weak_points": [{{"point": "未掌握的薄弱点描述，带技术细节", "topic": "{topic_key}"}}],
  "new_strong_points": [{{"point": "展示出的强项描述", "topic": "{topic_key}"}}],
  "communication_observations": {{
    "style_update": "30-60 字表达风格观察；统计信号不足以支撑观察时给空字符串",
    "new_habits": [],
    "new_suggestions": []
  }},
  "thinking_patterns": {{
    "new_strengths": ["从分数分布能推断出的思维优势；推断不出就留空数组"],
    "new_gaps": ["从分数分布能推断出的思维短板"]
  }},
  "topic_mastery": {{
    "notes": "对该领域掌握程度的 30 字内备注"
  }}
}}

规则：
- 判别 new_weak_points：从 per_question 的 weak_point 里出现 >= 2 次的，或单次出现但 score <= 4 的
- topic_mastery 只填 notes——掌握度分数由系统按 difficulty/5 × score/10 确定性计算，不要你估算
- 你只看得到分数统计、看不到候选人原文：communication_observations / thinking_patterns 仅在统计信号足够时填写，宁可留空，**禁止编造**"""
```

> **⚠️ 必须配套**：见 [三、`decoupled_eval.py`](#三backendgraphsdecoupled_evalpy必须配套)。

---

## 二、`backend/qa_arena.py` — 知识卡片整理链路（4 个提示词整体替换）

设计意图（四个提示词共享）：
- **按主题聚合，不按轮次流水**——同一主题的提问、追问、纠错、展开合并为一个小节。
- **表格与代码就地放置**——放在所属主题小节内部，让每节自包含；禁止抽离成集中的"横向对比"/"关键代码"章节（旧结构因此被整体删除）。
- **主题按逻辑排序**：基础概念 → 机制原理 → 应用/调优，不按对话/片段先后。
- 保留旧原则：忠实不编造、表格优先（对比一律用 Markdown 表格）、全面覆盖、可选小节没内容整节省略不写占位句。
- 每个主题小节新增「一句话总结」（一句能在面试中直接说出口的话）；新增可选章节「主题之间的关联」；「系统设计与权衡」收窄为仅限**跨主题**的架构讨论（单主题内部的取舍写进该主题小节）。

占位符未变化：`{conversation}`、`{date}`、`{idx}`、`{total}`、`{notes}`，调用侧 `.format(...)` 无需改动。

### 2.1 `SUMMARY_SYSTEM`（整体替换）

```python
SUMMARY_SYSTEM = """你是一位忠实、全面的技术笔记整理专家。你的任务是把一段技术问答对话整理成一张高质量、可直接用于复习的知识卡片。

核心原则：
1. **忠实不编造**：只整理对话中真实讨论过的内容，绝不补充对话之外的知识；记不清/没讨论的不写。
2. **按主题聚合，不按轮次流水**：同一主题的多轮问答（提问、追问、纠错、展开）必须合并成同一个知识点小节；知识点之间按「基础概念 → 机制原理 → 应用/调优」的逻辑递进排序，不按对话先后顺序。
3. **表格与代码就地放置**：对比表格、代码块放在**它所属知识点的小节内部**，让每个知识点自包含——读完一节即掌握一个完整主题；禁止把表格/代码抽离到与知识点脱节的集中章节。对话中已出现的表格必须完整保留并补全表头/列，关键代码用带语言标注的代码块原样保留，不要改写成散文。
4. **表格优先**：凡是横向对比、方案选型、概念辨析，一律用 Markdown 表格呈现，禁止降级成并列的 bullet 列表。
5. **全面覆盖**：对话讨论过的每一个知识点都要落到卡片里，不要只挑一两个；但覆盖靠"并入相关主题的小节"实现，不靠多开零散小节。"""
```

### 2.2 `SUMMARY_USER_TEMPLATE`（整体替换）

```python
SUMMARY_USER_TEMPLATE = """请根据以下问答对话，生成一份结构化、可用于复习的知识卡片。

## 对话内容

{conversation}

## 整理步骤（先想清楚再动笔）

1. 先归纳对话覆盖了哪几个**主题**（同一主题的提问、追问、纠错、展开算一个主题，通常 2-6 个）
2. 给主题排出逻辑顺序：基础概念 → 机制原理 → 应用/调优/设计（不按对话先后顺序）
3. 每个主题写成一个自包含小节：该主题的对比表格、代码、易错点全部放在**本节内部**

## 输出要求

严格输出 Markdown（不要用代码块包裹整份输出），按以下结构组织：

# {{自动识别的主题名称}}
> {date} 问答演练总结

## 速览
（2-4 句：这次对话围绕什么主线，覆盖了哪几个主题，得到的核心结论。让人读完知道整张卡片的骨架）

## 核心知识点

### 1. {{主题名称}}
- **定义**: 用一两句话精确定义
- **原理 / 关键要点**: 关键要点，可展开多条（原理、步骤、适用场景等）

（若本主题在对话中出现了 A vs B 对比、方案选型、概念辨析，**紧接着在这里放 Markdown 表格**；
 对话已有的表格保留并补全表头与列。没有对比则不放。）

| 维度 | 方案 A | 方案 B |
|------|--------|--------|

（若本主题在对话中出现了关键代码/命令/配置，**紧接着在这里放带语言标注的代码块**，并用一句话说明这段代码演示了本主题的什么要点。没有代码则不放。）

- **易错点**: 常见误解或容易混淆的地方
- **一句话总结**: 用一句能在面试中直接说出口的话收束本主题

### 2. {{下一个主题}}
（同上结构。对话讨论到的**每个**主题都要有小节，但要合并同主题的多轮问答，不要一轮对话开一个小节）

## 主题之间的关联
（1-3 句：这几个主题如何串成一条线——例如"从 X 的定义引出 Y 的实现，再到 Z 的调优"。只有一个主题时**省略本节**）

## 系统设计与权衡
（仅当对话包含**跨主题**的设计/架构类讨论：需求拆解 → 方案选型 → 权衡取舍 → 潜在坑点。
 单一主题内部的取舍写在该主题小节里，不放这里。没有则**省略本节**）

## 高频追问
- Q: {{对话中出现的或可延伸的面试常考问题}}?
- A: {{简洁回答}}

（列出 3-6 个，按上面主题的顺序排列）

## 待巩固 / 薄弱点
（基于对话中用户暴露的困惑或回答薄弱处，列出需要重点复习的点，并标注属于上面哪个主题；没有明显薄弱点则**省略本节**）

注意：
1. 主题名称要准确反映对话讨论的核心内容
2. 所有内容均来自对话，不要编造对话中没有讨论的内容
3. **表格和代码必须放在所属主题的小节内**，不要抽离成独立章节；对比用**表格**、代码用**代码块**，不要降级成普通文字
4. 易错点/薄弱点要基于对话中用户的实际困惑或常见误区
5. 对话未涉及的可选小节（关联/系统设计/待巩固）整节省略，不写占位句"""
```

### 2.3 `MAP_PROMPT`（整体替换，长对话分段抽取）

```python
MAP_PROMPT = """以下是一段较长技术问答对话的第 {idx}/{total} 段。请抽取本段中**真实讨论到**的内容，输出结构化的笔记片段，供后续汇总成知识卡片使用。

要求：
- **按主题组织**，不按对话轮次流水：本段每个主题一个 `### 主题名` 小节，同一主题的提问、追问、纠错合并到一节
- 每个主题小节内写：定义/结论、关键要点、易错点，并把该主题的对比表格、代码**就地放在本节内**（不要抽离到集中的"表格区"/"代码区"）
- 对话已有的表格必须完整保留（补全表头与列），不要丢任何一张表；关键代码用带语言标注的代码块原样保留
- 本段出现的高频追问（Q/A）附在对应主题小节末尾
- 只整理本段真实出现的内容，不编造、不补充对话外知识
- 直接输出片段内容，不要写"本段总结如下"之类的开场白

## 本段对话

{conversation}"""
```

### 2.4 `REDUCE_PROMPT`（整体替换，片段合并）

```python
REDUCE_PROMPT = """下面是同一段技术问答对话被分段整理出的多份**笔记片段**（用 --- 分隔）。请把它们**合并**成一张统一、无重复的知识卡片。

合并要求：
1. **同一主题只保留一个小节**：多个片段讲到同一主题时（哪怕措辞不同）合并成一节，融合各片段的要点，不要重复罗列；跨段被拆开的同一主题（前段讲概念、后段讲追问）必须合回一节。
2. **表格与代码就地放置**：表格/代码放在它所属主题的小节内部，讲同一主题的对比表合并成一张（并集列）；任一片段的表格/代码都不可丢失。
3. **主题按逻辑排序**：基础概念 → 机制原理 → 应用/调优，不按片段先后顺序。
4. **不新增、不臆造**：只整合片段里已有的内容，不补充片段之外的知识。
5. 按下面的小节组织，**所有片段都没涉及的小节整节省略**。

## 笔记片段

{notes}

## 输出要求

严格输出 Markdown（不要用代码块包裹整份输出）：

# {{自动识别的主题名称}}
> {date} 问答演练总结

## 速览
（2-4 句：对话围绕什么主线，覆盖了哪几个主题，核心结论）

## 核心知识点

### 1. {{主题名称}}
- **定义**: 一两句精确定义
- **原理 / 关键要点**: 关键要点，可多条

（本主题的对比表格放这里；本主题的关键代码块放这里，附一句它演示了什么）

- **易错点**: 常见误解或易混点
- **一句话总结**: 一句能在面试中直接说出口的话

（合并后每个主题只有一个小节，不重复）

## 主题之间的关联
（1-3 句串联各主题的逻辑线；只有一个主题时**省略本节**）

## 系统设计与权衡
（仅限**跨主题**的设计/架构类讨论：需求拆解 → 方案选型 → 权衡 → 坑点；单主题内的取舍写进该主题小节。没有则**省略本节**）

## 高频追问
- Q: {{对话中出现的面试常考问题}}?
- A: {{简洁回答}}

## 待巩固 / 薄弱点
（基于对话暴露的困惑，标注属于哪个主题；没有则**省略本节**）"""
```

---

## 三、`backend/graphs/decoupled_eval.py`（必须配套）

配套 §1.3。三处改动：

### 3.1 `_summarize_overall` 增加 `topic` 参数（供 `{topic_key}` 占位符使用）

调用处（`evaluate_decoupled` 内）：

```python
# 旧
overall = await _summarize_overall(topic_name, scores, user_id)
# 新
overall = await _summarize_overall(topic, topic_name, scores, user_id)
```

函数签名：

```python
# 旧
async def _summarize_overall(topic_name: str, scores: list[dict], user_id: str) -> dict:
# 新
async def _summarize_overall(topic: str, topic_name: str, scores: list[dict], user_id: str) -> dict:
```

`prompt` 构造处新增 `topic_key=topic`：

```python
prompt = DRILL_OVERALL_SUMMARY_PROMPT.format(
    topic_name=topic_name,
    topic_key=topic,
    score_stats="\n".join(stats_lines),
    user_profile=get_profile_summary_for_drill(user_id),
)
```

> 不加 `topic_key=topic` 而只替换提示词，运行时会抛 `KeyError: 'topic_key'`。

### 3.2 解析成功路径改走 `_normalize_overall`；失败兜底的 weak_points 改为对象形

```python
# 旧
        parsed.setdefault("avg_score", round(avg, 1))
        parsed.setdefault("new_weak_points", [])
        parsed.setdefault("new_strong_points", [])
        return parsed
    except Exception as exc:
        logger.warning("overall summary failed: %s", exc)
        return {
            "avg_score": round(avg, 1),
            "summary": f"整体总结失败 ({exc})，仅展示分数。",
            "new_weak_points": [wp for wp, cnt in weak_hits.items() if cnt >= 2],
            "new_strong_points": [],
        }
```

```python
# 新
        parsed.setdefault("avg_score", round(avg, 1))
        return _normalize_overall(parsed, topic)
    except Exception as exc:
        logger.warning("overall summary failed: %s", exc)
        return {
            "avg_score": round(avg, 1),
            "summary": f"整体总结失败 ({exc})，仅展示分数。",
            "new_weak_points": [
                {"point": wp, "topic": topic} for wp, cnt in weak_hits.items() if cnt >= 2
            ],
            "new_strong_points": [],
        }
```

### 3.3 新增 `_normalize_overall`（文件末尾，紧随 `_summarize_overall` 之后）

作用：把 LLM 输出强制归一到批量评估的嵌套 schema（单一事实来源）。即便换用的模型仍按旧扁平格式输出（字符串数组 / 扁平字符串 / 带 score 的 topic_mastery），也不会污染下游画像更新（`llm_update_profile` / `_update_communication` / `_update_thinking_patterns`）与前端 `Overall` 类型。

```python
def _normalize_overall(parsed: dict, topic: str) -> dict:
    """Coerce the overall dict to the batch-eval schema (single source of truth).

    The decoupled path historically asked the LLM for flat strings where the
    batch path used nested objects; downstream (llm_update_profile /
    _update_communication / _update_thinking_patterns and the frontend Overall
    type) all consume the nested shape. Normalize here so a model that still
    emits the legacy flat shape can't corrupt the profile.
    """
    def _pointify(items) -> list[dict]:
        out = []
        for it in items or []:
            if isinstance(it, dict) and it.get("point"):
                out.append({"point": str(it["point"]), "topic": it.get("topic") or topic})
            elif isinstance(it, str) and it.strip():
                out.append({"point": it.strip(), "topic": topic})
        return out

    parsed["new_weak_points"] = _pointify(parsed.get("new_weak_points"))
    parsed["new_strong_points"] = _pointify(parsed.get("new_strong_points"))

    comm = parsed.get("communication_observations")
    if isinstance(comm, str):
        comm = {"style_update": comm.strip(), "new_habits": [], "new_suggestions": []}
    elif not isinstance(comm, dict):
        comm = {}
    parsed["communication_observations"] = comm

    tp = parsed.get("thinking_patterns")
    if isinstance(tp, str):
        # A flat string carries no strength/gap split — file it as neither
        # rather than guessing; the summary text already covers it.
        tp = {"new_strengths": [], "new_gaps": []}
    elif not isinstance(tp, dict):
        tp = {}
    parsed["thinking_patterns"] = tp

    # Mastery score is deterministic (difficulty/5 × score/10, computed in
    # _update_drill_profile) — drop any LLM-estimated score, keep only notes.
    tm = parsed.get("topic_mastery")
    parsed["topic_mastery"] = {"notes": tm.get("notes", "")} if isinstance(tm, dict) else {}
    return parsed
```

---

## 四、`backend/graphs/resume_interview.py`（必须配套）

配套 §1.1。`_parse_inline_eval` 中，在 `score` 归一化（clamp 到 0-10）之后、`return clean, eval_data` 之前，插入 `brief` 回填：

```python
    # brief ← observation fallback: the review builder reads `brief`, but the
    # model only needs to emit `observation` — don't lose the eval when it
    # (reasonably) skips the duplicated legacy field.
    if not eval_data.get("brief") and eval_data.get("observation"):
        eval_data["brief"] = eval_data["observation"]

    return clean, eval_data
```

上下文参考（插入后的函数尾部形如）：

```python
    else:
        eval_data["score"] = max(0, min(10, int(round(score))))

    # brief ← observation fallback: the review builder reads `brief`, but the
    # model only needs to emit `observation` — don't lose the eval when it
    # (reasonably) skips the duplicated legacy field.
    if not eval_data.get("brief") and eval_data.get("observation"):
        eval_data["brief"] = eval_data["observation"]

    return clean, eval_data
```

---

## 五、落盘后的验证清单

1. **静态检查**：`grep -n "topic_key" backend/` 应命中两处——`DRILL_OVERALL_SUMMARY_PROMPT` 内的 `{topic_key}` 占位符，以及 `decoupled_eval.py` 的 `topic_key=topic`。缺一即会 `KeyError`。
2. **EVAL 标记**：提示词内不应再出现 `"brief"`；`resume_interview.py` 的 `_parse_inline_eval` 应包含 `brief ← observation` 回填。跑一轮简历面试的 technical 阶段，确认时光机/复盘页仍能显示每题 brief。
3. **专项训练**：发起一轮 drill，确认出题正常（§1.2 只是措辞级改动）；答完后确认整体总结返回嵌套结构（`new_weak_points` 为 `[{point, topic}]` 对象数组、`topic_mastery` 无 `score` 键），画像页薄弱点/沟通观察正常更新。
4. **QA 竞技场**：分别用短对话（走 `SUMMARY_USER_TEMPLATE` 单次总结）和长对话（走 `MAP_PROMPT`/`REDUCE_PROMPT` map-reduce）各生成一张知识卡片，确认：表格/代码出现在对应主题小节内部，没有集中的「横向对比 / 选型」「关键代码 / 实现要点」章节；每个主题小节以「一句话总结」收束。
5. 冒烟入口：起服务后打 `http://localhost:8000/docs`（本项目无 pytest 套件；按项目约定，测试在服务器上执行）。

---

## 六、知识训练场卡片答案增强（补充改动，2026-07-18）

> 上述迁移之外，本次同时增强了「知识训练场」生成卡片的答案质量——用户反馈生成的 answer 过于简略。涉及 `backend/prompts/knowledge_training.py`（提示词）与 `backend/knowledge_training.py`（质量过滤阈值）。

### 6.1 `KNOWLEDGE_TRAINING_SYSTEM` — answer 字段要求

`必须遵守` 列表里的 answer 约束由「精炼即可」改为「面试可口述的完整参考答案」：

```text
# 旧
- answer 要能独立回答 question，包含关键判断和适用边界，但保持精炼。
# 新
- answer 是卡片的核心：要写成候选人在面试中能完整说出口的参考回答，按“结论 -> 机制/推导 -> 边界或易错点”展开，能独立回答 question，不依赖 knowledge 也成立。禁止只给一两句结论。
```

### 6.2 `build_knowledge_training_prompt` — 生成要求第 6 条改为三层展开

将原「6. answer 要能直接回应 question，不要只重复 knowledge。」替换为三层结构要求（结论 / 机制推导 / 边界易错），并追加一条「answer 与 knowledge 组织方式必须不同」，同时在坏例子里加入「只有一句结论」的反例、好例子里加入一条三层展开的 answer 示范。核心新增文本：

```text
6. answer 按三层展开，来源片段有料就写满（通常 80-200 字）：
   - **结论**：先用 1-2 句直接回答 question 的问法（问"为什么"就答原因，问"区别"就点出关键差异）
   - **机制/推导**：说清结论为什么成立——原理、执行过程、对比维度；来源片段里的关键代码/命令/参数在这里引用
   - **边界/易错点**：什么场景下不适用、常见的错误理解是什么
   来源片段撑不起某一层就省略该层，**不编造**；但不允许只给一层——只有一句结论的答案对着 question 复习时没有回忆量。
7. answer 不要照抄 knowledge 的列表——knowledge 是"看"的要点清单，answer 是"说"的完整回答，两者组织方式必须不同。
```

（原第 6 条之后的编号顺延：source_index→8、不生成分数→9、信息不足跳过→10。）

### 6.3 `_looks_like_low_quality_card` — answer 长度下限 18 → 40

`backend/knowledge_training.py`：

```python
# 旧
    if len(answer) < 18 or len(question) < 10:
# 新
    # Answer floor raised 18→40: the prompt now demands 结论→机制→边界 layered
    # answers (~80-200 chars when the source supports it); a sub-40-char answer
    # is a bare conclusion with no recall value when reviewed against question.
    if len(answer) < 40 or len(question) < 10:
```

验证：起服务后对任一 topic 生成 3 张卡片，answer 长度应显著上升（实测从旧版几十字提升到 ~280-350 字，且呈现"结论/机制/边界"三段）。注意现有 `tests/test_knowledge_training.py` 里的样例 answer 均 ≥ 40 字，不受阈值提升影响。
