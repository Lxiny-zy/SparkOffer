# 02b · 十大亮点代码级 Trace（下半部分）

> 接 [02 十大亮点（上）](02_HIGHLIGHTS_DEEP.md)。包含亮点 6-10。

---

## 亮点 6 · ★★★★★ LangGraph 隐藏 EVAL 标记驱动状态机

### 6.1 是什么

简历模拟面试用 LangGraph 实现五阶段状态机：
`greeting → self_intro → technical → project_deep_dive → reverse_qa → END`

每次面试官回答的**末尾**附加一个隐藏 HTML 注释格式的 EVAL JSON，前端正则剥离，后端用它驱动状态转移和实时评分。

```
   面试官的回答：
   "嗯，对 happens-before 的理解到位了。继续——你之前提到 volatile，
    那 volatile 在 DCL 单例里解决的是哪个问题？
    <!--EVAL:{"score":7,"should_advance":false,"observation":"...",
              "next_focus":"...","pillar":"java","brief":"..."}-->"
                                          ↓
                          后端 _parse_inline_eval() 剥离
                                          ↓
                user 看到：纯净文本 "嗯，对 happens-before...volatile..."
                                          ↓
                后端拿到：{"score":7, "should_advance":false, ...}
                                          ↓
                          route_after_answer() 用它决定路由
```

### 6.2 完整 trace

#### Graph 定义

```python
# graphs/resume_interview.py:compile_resume_interview
def compile_resume_interview(user_id):
    graph = StateGraph(ResumeInterviewState)

    graph.add_node("init", _make_init_interview(user_id))
    graph.add_node("ask", _make_interviewer_ask(user_id))
    graph.add_node("advance", advance_phase)
    graph.add_node("wait", wait_for_answer)

    graph.add_edge(START, "init")
    graph.add_edge("init", "wait")
    graph.add_edge("ask", "wait")
    graph.add_edge("advance", "ask")

    graph.add_conditional_edges("wait", route_after_answer, {
        "ask": "ask",
        "advance": "advance",
        "end": END,
    })

    return graph.compile(
        checkpointer=MemorySaver(),           # ★ 状态持久化
        interrupt_before=["wait"],            # ★ 暂停等用户输入
    )
```

#### State 定义

```python
# models.py
class ResumeInterviewState(TypedDict, total=False):
    messages: Annotated[list, add_messages]   # LangGraph 自动 append
    phase: str                                 # 当前阶段
    resume_context: str                        # 简历 RAG 上下文
    questions_asked: list[str]
    phase_question_count: int
    is_finished: bool
    last_eval: dict                            # 最近一次 EVAL
    eval_history: list                         # 所有 EVAL 累积
```

#### EVAL 解析

```python
_EVAL_PATTERN = re.compile(r"<!--EVAL:(.*?)-->", re.DOTALL)

def _parse_inline_eval(content):
    m = _EVAL_PATTERN.search(content)
    if not m:
        return content, None
    clean = _EVAL_PATTERN.sub("", content).rstrip()  # 剥离
    try:
        eval_data = json.loads(m.group(1))
        return clean, eval_data
    except json.JSONDecodeError:
        logger.warning(...)
        return clean, None
```

#### ask 节点

```python
def _make_interviewer_ask(user_id):
    def interviewer_ask(state):
        asked_str = "\n".join(f"- {q}" for q in state["questions_asked"])
        knowledge_ctx = _retrieve_all_topic_knowledge(user_id)

        system_prompt = RESUME_INTERVIEWER_SYSTEM.format(
            resume_context=state["resume_context"],
            knowledge_context=knowledge_ctx,
            phase=state["phase"],
            asked_questions=asked_str,
            user_profile=get_profile_summary(user_id),
        )

        llm = get_langchain_llm()
        messages = [SystemMessage(system_prompt)] + list(state["messages"])
        response = llm.invoke(messages)

        # 剥离 EVAL
        clean_content, eval_data = _parse_inline_eval(response.content)
        count = state.get("phase_question_count", 0)

        result = {
            "messages": [AIMessage(content=clean_content)],
            "questions_asked": state["questions_asked"] + [clean_content[:100]],
            "phase_question_count": count + 1,
        }
        if eval_data:
            eval_data["phase"] = state["phase"]
            eval_data["question_index"] = count
            result["last_eval"] = eval_data
            result["eval_history"] = state["eval_history"] + [eval_data]
        return result
    return interviewer_ask
```

#### 路由决策（三重护栏）

```python
def route_after_answer(state):
    if state.get("is_finished"):
        return "end"

    phase = state["phase"]
    count = state["phase_question_count"]
    last_eval = state.get("last_eval")

    # ─── 护栏 1: 硬上限防死循环 ───
    if count >= HARD_MAX_PER_PHASE:  # 10
        return "advance"

    # ─── 简单阶段：纯计数 ───
    if phase == "greeting" and count >= 1:
        return "advance"
    if phase == "self_intro" and count >= 2:
        return "advance"
    if phase == "reverse_qa" and count >= 2:
        return "end"

    # ─── 复杂阶段：eval + count ───
    if phase in ("technical", "project_deep_dive"):
        # 护栏 2: 至少 2 题
        if count >= 2 and last_eval and last_eval.get("should_advance"):
            logger.info(f"Eval-driven advance: {phase} after {count} questions")
            return "advance"
        # 护栏 3: 最大题数兜底
        if count >= settings.max_questions_per_phase:  # 5
            return "advance"

    return "ask"
```

### 6.3 端到端调用流程

```
前端: POST /api/interview/start (mode=resume)
   ↓
routers/interview.py: compile_resume_interview() → graph
                      graph.invoke({}, config={"thread_id": session_id})
   ↓
LangGraph:
   ┌─ init 节点：query_resume() + RAG 拉知识 → 生成开场白
   ├─ wait 节点：interrupt_before 暂停 ← 等用户输入
   ↓
前端: POST /api/interview/chat (session_id, message)
   ↓
routers/interview.py:
   graph.update_state(config, {"messages": [HumanMessage(message)]})
   graph.invoke(None, config)  # 续传
   ↓
LangGraph:
   ┌─ 从 wait 后继续 → conditional_edges → route_after_answer
   │  · 看 last_eval.should_advance 和 count
   │  ↓
   ├─ ask 节点：LLM 生成下一题（带 EVAL）→ _parse_inline_eval 剥离
   │  ↓
   └─ wait 节点：interrupt_before 又暂停 ← 等下一轮
   ↓
前端: 重复 chat 直到 is_finished=True
   ↓
前端: POST /api/interview/end/{session_id}
   ↓
后端: graph.get_state(config) → 拿全部 messages + eval_history
       → stream_generate_review() 生成复盘
       → update_profile_after_interview() 更新画像
```

### 6.4 EVAL 数据结构详解

Prompt 强制约定（`prompts/interviewer.py:80-94`）：

```json
{
  "score": 7,                    // 上一个回答 0-10 整数
  "should_advance": false,        // 是否建议推进阶段
  "observation": "对 ThreadLocal 的内存泄漏有正确直觉但未提到线程池场景下的清理责任",
  "next_focus": "想追问 try-finally remove 在异步框架里的实践",
  "pillar": "java",              // java | python | agent | general
  "brief": "..."                  // observation 的复本（兼容旧解析器）
}
```

**字段设计巧思**：
- `score`：用于事后复盘的逐题评分
- `should_advance`：LLM 主观判断当前阶段是否充分
- `observation`：训练后画像更新的依据
- `pillar`：归类候选人当前讨论的技术板块，便于画像归因
- `brief`：兼容字段，防止 Prompt 漂移把字段名换了导致 parse 失败

### 6.5 设计权衡

**为什么用 HTML 注释 `<!--EVAL:-->`**：
- Markdown 渲染器会忽略 HTML 注释，前端显示干净
- 容易用正则提取，比 JSON 块前后加 marker 简单

**为什么 LLM 自评 + count 双护栏**：
- 纯 count（如"每个阶段 5 题"）：机械、不自然
- 纯 LLM 决定：可能因 LLM bug 卡死或太快推进
- 双护栏：LLM 主导 + count 兜底 + 硬上限防死循环

**为什么 `phase_question_count >= 2` 才让 LLM 推进**：避免一上来 should_advance=true 就推进，至少给候选人 2 题表现机会。

**为什么用 MemorySaver（内存）而不是 SqliteSaver**：
- 内存 saver 单进程足够，重启会丢
- 我们额外存了一份在 live_store + SQLite live_sessions 表，作为持久化兜底（实际上 LangGraph state 主要是 messages，业务关键数据已经在 sessions 表里）

### 6.6 失败处理

| 失败场景 | 处理方式 |
|---|---|
| LLM 没附 EVAL | `_parse_inline_eval` 返回 (content, None)，按 count 推进 |
| EVAL JSON 不合法 | logger.warning 记录，按 count 推进 |
| Graph 循环（理论上） | `HARD_MAX_PER_PHASE=10` 强制推进 |
| 用户中途关闭 | live_sessions 表保留 2 小时，刷新页面可恢复 |

### 6.7 面试演讲稿（90 秒）

"简历模拟面试我用了 LangGraph 做 5 阶段状态机：greeting → self_intro → technical → project_deep_dive → reverse_qa。

最得意的设计是『**隐藏 EVAL 标记**』。让 LLM 在每次回答的末尾附加一段 HTML 注释格式的 JSON：

`<!--EVAL:{"score":7,"should_advance":false,"observation":"...","pillar":"java"}-->`

前端用 Markdown 渲染时 HTML 注释会被忽略 —— 用户看到的是干净的对话；后端用正则剥离拿到 EVAL 数据，用它驱动状态转移。

这样有几个好处：
1. **LLM 自评是否推进阶段**（should_advance）比纯计数更智能
2. **逐题打分**（score）可以累积成评分历史
3. **observation 字段**直接用于训练后画像更新
4. **pillar 字段**把每题归类到 java/python/agent，便于画像归因

路由有**三重护栏**防止失控：
- 护栏 1：硬上限 `HARD_MAX_PER_PHASE=10`，防死循环
- 护栏 2：至少 2 题才允许推进（避免一上来就 should_advance=true）
- 护栏 3：最大题数兜底（`max_questions_per_phase=5`）

LangGraph 的 `interrupt_before=["wait"]` + `MemorySaver` checkpointer 让对话状态自动持久化，前端只要传 thread_id 就能续接。"

---

## 亮点 7 · ★★★★ 流式 SSE + 增量 JSON 解析

### 7.1 是什么

- LLM 流式响应通过 SSE 实时推送前端
- 出题阶段：**边生成边解析 JSON，逐题渲染**（不用等 10 题全好）
- 心跳机制防代理超时
- 用一个 200 字符进度阈值平衡推送频率

```
LLM 流式输出： [{"id":1,"question":"...","difficulty":3}{|...
                                                       ↑
                                  extract_complete_objects() 状态机扫描
                                                       ↓
                                    检测到完整 obj，立刻推送
                                                       ↓
                          前端 SSE 事件: {"type":"question","data":{...}}
                                                       ↓
                                          界面上闪现第 1 题
```

### 7.2 增量 JSON 解析器（核心）

```python
# utils/stream_parser.py
def extract_complete_objects(partial_json):
    cleaned = partial_json.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    if not cleaned.startswith("["):
        idx = cleaned.find("[")
        if idx == -1:
            return [], partial_json
        cleaned = cleaned[idx:]

    objects = []
    depth = 0           # { } 嵌套深度
    in_string = False   # 是否在字符串里
    escape = False
    obj_start = -1

    for i, ch in enumerate(cleaned):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue  # 字符串内的 {} 不计

        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                obj_str = cleaned[obj_start:i+1]
                try:
                    obj = json.loads(obj_str)
                    objects.append(obj)
                except json.JSONDecodeError:
                    pass
                obj_start = -1

    return objects, remaining
```

**为什么不用 `try: json.loads()` 暴力试**：
- 流式输出有可能是 `[{"id":1,"que` 这种不完整片段
- 每次 token 来都 try 一次性能差
- 状态机扫描精准，O(n) 一次过

**为什么处理字符串引号**：JSON 字符串内可能有 `{` `}`（比如 `"question": "if (x > 0) {return}"`），不能误计入深度。

### 7.3 SSE 心跳设计

```python
# utils/sse_helpers.py
IDLE_HEARTBEAT_SECONDS = 30        # 30 秒无 token 就 ping
PROGRESS_CHAR_INTERVAL = 200       # 累积 200 字推送 progress

async def stream_llm_sse(lc_messages, progress_prefix="正在生成中"):
    yield ("sse", sse_event({"type": "progress", "message": f"{progress_prefix}..."}))

    accumulated = ""
    chars_since_heartbeat = 0
    try:
        llm = get_langchain_llm()
        aiter = llm.astream(lc_messages).__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(aiter.__anext__(), timeout=IDLE_HEARTBEAT_SECONDS)
                token = chunk.content if hasattr(chunk, "content") else ""
                if token:
                    accumulated += token
                    chars_since_heartbeat += len(token)
                    if chars_since_heartbeat >= PROGRESS_CHAR_INTERVAL:
                        yield ("sse", sse_event({
                            "type": "progress",
                            "message": f"{progress_prefix}... ({len(accumulated)} 字)",
                        }))
                        chars_since_heartbeat = 0
            except asyncio.TimeoutError:
                yield ("sse", sse_event({"type": "ping"}))  # ★ 心跳
            except StopAsyncIteration:
                break
    except Exception as e:
        yield ("sse", sse_event({"type": "error", "message": "AI 服务暂时不可用"}))
        return

    yield ("result", accumulated)
```

### 7.4 阻塞函数也要心跳

`graph.invoke(...)` 是阻塞调用（虽然 LangGraph 节点里有 LLM 流式，但整体 invoke 同步等待），需要也走心跳：

```python
async def stream_blocking_sse(sync_callable, *args, progress_msg="处理中", heartbeat_interval=5.0):
    yield ("sse", sse_event({"type": "progress", "message": f"{progress_msg}..."}))

    task = asyncio.ensure_future(asyncio.to_thread(sync_callable, *args))

    while not task.done():
        await asyncio.sleep(heartbeat_interval)
        if not task.done():
            yield ("sse", sse_event({"type": "ping"}))

    if task.exception():
        yield ("sse", sse_event({"type": "error", "message": str(task.exception())}))
        return

    yield ("result", task.result())
```

### 7.5 Nginx 配置防止 SSE 缓冲

```nginx
# frontend/nginx.conf
location /api/ {
    proxy_pass http://backend:8000;
    ...
    proxy_read_timeout 300s;       # 5 分钟超时
    proxy_buffering off;            # ★ 关键：禁用缓冲
    proxy_cache off;
    proxy_http_version 1.1;
    proxy_set_header Connection ''; # 允许长连接
}
```

后端 response header 也要：

```python
return StreamingResponse(
    stream_questions(),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

`X-Accel-Buffering: no` 是 Nginx 的特殊 header，告诉它**这条连接禁用缓冲**。即使全局 `proxy_buffering on`，这个 header 也会覆盖。

### 7.6 性能数据

| 场景 | 非流式 | 流式 + 增量解析 |
|---|---|---|
| 出 10 题首屏时间 | ~30 秒 | **~3 秒**（第一题出现） |
| 复盘报告首字时间 | ~20 秒 | **~2 秒** |
| 用户感知 | "卡住了" | "在跑" |

### 7.7 面试演讲稿（60 秒）

"流式响应这块做了几个细节：

1. **增量 JSON 解析**：LLM 出 10 道题是流式 token，我用一个**状态机扫描 `{` `}` 嵌套深度**（处理字符串内引号转义），每检测到一个完整 obj 就立刻 yield。**首屏时间从 30 秒压到 3 秒**。

2. **三层心跳防超时**：
   - LLM 流式：30 秒无 token 推 `ping` 事件
   - 阻塞函数：用 `asyncio.to_thread` + `asyncio.ensure_future` 包装，每 5 秒 ping
   - 进度反馈：每累积 200 字符推一次 `progress`

3. **Nginx 配置**：`proxy_buffering off` + `X-Accel-Buffering: no` header 双保险，禁用缓冲。

4. **错误优雅降级**：流式中途 LLM 失败，先发 error 事件让前端展示，**不让连接静默死掉**。

这套设计让 SSE 在国内代理环境也能稳定跑。"

---

## 亮点 8 · ★★★★ SM-2 间隔重复算法

### 8.1 是什么

借鉴 Anki 的 SuperMemo-2 算法，为每个薄弱点维护复习调度：

```
答对（score >= 6）   答错（score < 6）
       ↓               ↓
   reps += 1         reps = 0
   ↓                 ↓
   reps=0 → 1 天      间隔重置 1 天
   reps=1 → 3 天
   reps>1 → interval × EF (ease_factor 动态调整)
                     ↓
                next_review = today + interval_days
                     ↓
          连续 3 次 ≥ 7 分 → 自动毕业到 strong_points
```

### 8.2 完整算法实现

```python
# spaced_repetition.py:sm2_update
def sm2_update(sr_state, score_0_10):
    quality = min(5, int(score_0_10 / 2))  # 0-10 → 0-5（SM-2 quality）
    ef = sr_state.get("ease_factor", 2.5)
    reps = sr_state.get("repetitions", 0)

    if quality >= 3:  # Pass (score >= 6)
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 3
        else:
            interval = int(sr_state.get("interval_days", 1) * ef)
        reps += 1
    else:  # Fail — reset
        interval = 1
        reps = 0

    # 调整 EF（永不低于 1.3）
    ef = max(1.3, ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))

    return {
        "interval_days": interval,
        "ease_factor": round(ef, 2),
        "repetitions": reps,
        "next_review": (date.today() + timedelta(days=interval)).isoformat(),
        "last_score": score_0_10,
    }
```

### 8.3 EF 调整公式分析

SM-2 原公式：
```
EF_new = EF_old + (0.1 - (5 - q)(0.08 + (5 - q) * 0.02))
```

代入不同 quality 看效果：
- q=5（完美）：EF + 0.1 → 间隔涨得快
- q=4（顺利）：EF - 0（不变）
- q=3（勉强）：EF - 0.14
- q=2（吃力）：EF - 0.32
- q=1（很难）：EF - 0.54
- q=0（完全不会）：EF - 0.80

所以**答得越烂 EF 降得越多**，下次间隔更短，复习更频繁。

### 8.4 触发时机

```python
# routers/interview.py:end_interview (drill 模式)
async def _stream_drill():
    ...
    for s in scores:
        wp = s.get("weak_point")
        sc = s.get("score")
        if wp and isinstance(sc, (int, float)):
            update_weak_point_sr(topic, wp, sc, user_id)  # ★ 触发 SM-2
    ...


# spaced_repetition.py:update_weak_point_sr
def update_weak_point_sr(topic, point_text, score, user_id):
    profile = _load_profile(user_id)
    for wp in profile.get("weak_points", []):
        if wp.get("improved"):
            continue
        if topic and wp.get("topic") != topic:
            continue
        # 模糊匹配
        if point_text.lower() in wp["point"].lower() or wp["point"].lower() in point_text.lower():
            sr = wp.get("sr", {})
            wp["sr"] = sm2_update(sr, score)

            # ★ 自动毕业：连续 3 次答对且高分
            if wp["sr"]["repetitions"] >= 3 and score >= 7:
                wp["improved"] = True
                wp["improved_at"] = datetime.now().isoformat()
                wp["improved_reason"] = "spaced_repetition_mastery"
                profile["strong_points"].append({
                    "point": f"已掌握: {wp['point']}",
                    "topic": wp.get("topic"),
                    "first_seen": datetime.now().isoformat(),
                })

            _save_profile(profile, user_id)
            return True
    return False
```

### 8.5 出题时优先到期复习

```python
# spaced_repetition.py:get_due_reviews
def get_due_reviews(user_id, topic=None):
    profile = _load_profile(user_id)
    today = date.today().isoformat()
    due = []
    for wp in profile.get("weak_points", []):
        if wp.get("improved"):
            continue
        if topic and wp.get("topic") != topic:
            continue
        sr = wp.get("sr", {})
        next_review = sr.get("next_review", "2000-01-01")
        if next_review <= today:
            due.append(wp)
    # ★ EF 最低的（最难）排在最前
    due.sort(key=lambda x: x.get("sr", {}).get("ease_factor", 2.5))
    return due
```

### 8.6 设计权衡

**为什么用 SM-2 不用 FSRS（更新的算法）**：SM-2 简单可解释，FSRS 需要训练参数。个人项目 SM-2 够用。

**为什么用模糊匹配而不是精确**：薄弱点描述会随时间变化（"GIL 理解不深" → "对 GIL 在 IO 场景的释放时机不熟"），模糊匹配能识别这是同一个点。

**为什么自动毕业阈值是 reps >= 3 && score >= 7**：3 次以上能验证不是偶然答对；7 分以上排除"勉强通过"。

### 8.7 面试演讲稿（45 秒）

"画像系统有个被忽视但很关键的设计：**间隔重复算法**。

我借鉴的是 Anki 用的 SuperMemo-2 算法。每个薄弱点都有 SR 状态（interval_days, ease_factor, repetitions, next_review）：
- 答对了，间隔拉长：1天 → 3天 → 3×EF → ...
- 答错了，间隔重置到 1 天
- EF（难度系数）根据表现动态调整，但永不低于 1.3

出题时 `get_due_reviews()` 返回**到期需要复习**的薄弱点，按 EF 升序（最难的最先复习）。

还有个**自动毕业机制**：连续 3 次答对（reps >= 3）且 score >= 7，自动从 weak_points 转入 strong_points，标记 `improved_reason="spaced_repetition_mastery"`。

这让训练有真正的『**长期收益**』：不是单次刷题，而是按遗忘曲线管理你的薄弱点。"

---

## 亮点 9 · ★★★ 知识库自我进化

### 9.1 是什么

训练结束后自动做两件事：

1. **高质量答案知识沉淀**：score >= 7 或 < 6 的题，让 LLM 提炼成 Markdown 知识点，append 到 `data/users/{uid}/knowledge/{topic}/自动沉淀.md`
2. **低分题进入高频题库**：score < 6 的题进入 `data/users/{uid}/high_freq/{topic}.md`，下次出题时作为**优先考察池**

```
训练评估完成
       ↓
extract_and_writeback(topic, questions, answers, scores, user_id)
       ↓
   筛选 score>=7（高分答案有价值）或 score<6（低分题有学习价值）
       ↓
LLM 提取知识点 → Markdown
       ↓
append 到 自动沉淀.md
       ↓
schedule_incremental_insert(topic, user_id, extracted)  ← 后台增量插入索引
       ↓
collect_high_freq(topic, questions, scores, user_id)
       ↓
   筛选 score<6 的题
       ↓
追加到 high_freq/{topic}.md
       ↓
下次出题，high_freq 作为 Prompt 的"高频题"上下文
```

### 9.2 知识沉淀实现

```python
# knowledge_evolution.py:extract_and_writeback
async def extract_and_writeback(topic, questions, answers, scores, user_id):
    worthy = []
    for i, s in enumerate(scores):
        score_val = s.get("score", 5)
        if score_val >= 7 or score_val < 6:  # 高分有价值，低分有学习意义
            q_text = questions[i]["question"]
            a_text = answers[i] if i < len(answers) else "(未作答)"
            assessment = s.get("assessment", "")
            worthy.append(
                f"Q: {q_text}\nA: {a_text}\n得分: {score_val}\n评价: {assessment}"
            )

    if not worthy:
        return

    qa_text = "\n\n---\n\n".join(worthy)
    response = llm.invoke([HumanMessage(content=_EXTRACT_PROMPT.format(qa_text=qa_text))])
    extracted = response.content.strip()

    if not extracted or len(extracted) < 20:
        return

    target = topic_dir / "自动沉淀.md"
    existing = target.read_text() if target.exists() else ""
    header = f"\n\n---\n\n<!-- 自动沉淀 {datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n\n"
    new_content = header + extracted + "\n"
    target.write_text(existing + new_content, encoding="utf-8")

    # ★ 后台增量插入索引（不全量重建）
    schedule_incremental_insert(topic, user_id, extracted)
```

### 9.3 提取 Prompt

```python
_EXTRACT_PROMPT = """你是一个知识提取引擎。请从以下面试 Q&A 中提取有价值的知识点。

## Q&A 列表
{qa_text}

## 任务
- 对于得分 >= 7 的回答，提取其中体现的深度知识、最佳实践或独到见解
- 对于得分 < 6 的回答，提取正确答案方向和关键概念作为参考

每个知识点用 `## ` 开头，简洁明确，包含核心概念和实际应用。
只返回 Markdown 知识点，不要其他内容。"""
```

### 9.4 增量插入（不全量重建）

```python
# indexer.py:incremental_insert_to_index
def incremental_insert_to_index(topic, user_id, new_text):
    try:
        index = build_topic_index(topic, user_id)
        doc = Document(text=new_text, metadata={"source": "auto_evolution", "topic": topic})
        index.insert(doc)                                                      # 只 embed 新内容
        cache_dir = settings.user_index_cache_path(user_id) / topic
        index.storage_context.persist(persist_dir=str(cache_dir))             # 持久化
        _cache_set((user_id, topic), index)
    except Exception as e:
        logger.warning(f"Incremental insert failed, falling back to invalidation: {e}")
        invalidate_topic_index(topic, user_id)  # 兜底全量重建
```

### 9.5 设计权衡

**为什么不全量重建索引**：全量重建一个主题（几十个 md 文件）要十几秒甚至几分钟。增量插入只 embed 新内容，**几秒搞定**。

**为什么沉淀高分和低分都有价值**：
- 高分（>=7）：候选人答得好，提炼出来是**正向知识**
- 低分（<6）：候选人答得差，Prompt 让 LLM 提炼"正确方向"，作为**学习参考**

**为什么不沉淀中等分（6-7）**：中等分通常是"概念对但缺深度"，没有沉淀价值。

**风险：会不会把错误答案当成知识沉淀进去**：会有这个风险。我的对策：
1. LLM Prompt 里写明"对低分回答提取正确答案方向，不是答案本身"
2. 用户可以在 Knowledge 页面看到 `自动沉淀.md` 并手动编辑
3. 文件头有 `<!-- 自动沉淀 时间 -->` 标记，方便识别

### 9.6 高频题收集

```python
# knowledge_evolution.py:collect_high_freq
async def collect_high_freq(topic, questions, scores, user_id):
    low_score_items = []
    for i, s in enumerate(scores):
        score_val = s.get("score", 5)
        if score_val < 6:
            q_text = questions[i]["question"]
            assessment = s.get("assessment", "")
            low_score_items.append((q_text, score_val, assessment))

    if not low_score_items:
        return

    filepath = high_freq_dir / f"{topic}.md"
    existing = filepath.read_text() if filepath.exists() else ""
    lines = [f"\n\n<!-- {datetime.now().strftime('%Y-%m-%d %H:%M')} -->"]
    for q, score, assessment in low_score_items:
        lines.append(f"\n## Q: {q}\n得分: {score}\n评估: {assessment}\n---")
    filepath.write_text(existing + "\n".join(lines) + "\n")
```

下次出题时（`graphs/topic_drill.py:_load_high_freq`）会读这个文件作为 Prompt 的 `high_freq_questions` 参数，告诉 LLM"这些是用户标记的高频题"。

### 9.7 面试演讲稿（45 秒）

"训练系统有个『**自我进化**』机制。每次训练结束自动做两件事：

1. **知识沉淀**：高分（>=7）+ 低分（<6）的题让 LLM 提炼成 Markdown 知识点，append 到知识库的『自动沉淀.md』。高分答案是正向知识，低分题 LLM 会提炼『正确答案方向』作为参考。

2. **高频题收集**：低分题进入 `high_freq/{topic}.md`，下次出题时作为优先考察池。

知识沉淀后我用**增量插入**更新向量索引（`index.insert(Document)`），只 embed 新内容，不全量重建。如果失败了 fallback 到 invalidation 全量重建。

价值是用户每练一次，知识库自动丰富一点，下次出题更准。这是 **AI 系统的飞轮效应**。

但我也承认风险：低分题的提炼可能不准。所以文件头都有时间戳标记，用户可以在 Knowledge 页面手动审核编辑。"

---

## 亮点 10 · ★★★★★ FloatingAssistant 14 工具 Function Calling Agent

### 10.1 是什么

一个全局漂浮的小猫助手「小鱼」，可以：
- 多轮对话（带历史 + 长期记忆）
- 调用 14 个工具（导航、画像查询、复习查询、训练启动、知识库检索、薄弱点详情...）
- 流式响应（带 tool_call 检测和工具结果回填）
- 双档运行（关怀档 / 技术解释档）

### 10.2 14 个工具完整列表

```python
TOOLS = [
    # ─── 页面级动作 ───
    "navigate"              → 导航到指定页面
    "start_interview"       → 开始面试训练

    # ─── 画像查询 ───
    "check_profile"         → 简版画像概览
    "get_full_profile"      → 完整画像（思维模式、沟通风格、改善的薄弱点）
    "get_weak_points_detail"→ 薄弱点详情（含 SR 状态）
    "get_score_trends"      → 得分趋势
    "get_training_stats"    → 训练统计

    # ─── 历史查询 ───
    "search_history"        → 搜索面试记录
    "get_session_detail"    → 单次面试详情
    "get_session_transcript"→ 完整对话记录
    "list_trained_topics"   → 训练过的领域

    # ─── 数据查询 ───
    "list_topics"           → 可用训练领域
    "list_favorites"        → 收藏题目
    "search_favorites_detail"→ 收藏题目详情
    "search_algorithm_cards"→ 算法题收藏
    "get_due_reviews"       → 到期复习薄弱点

    # ─── 知识查询 ───
    "search_knowledge_memory"→ 向量语义搜索历史训练记忆
    "query_knowledge_base"  → 检索某个领域知识库
]
```

### 10.3 流式 + 工具调用的完整 trace

```python
# assistant.py:stream_assistant_chat
async def stream_assistant_chat(message, user_id):
    llm = get_langchain_llm()
    llm_with_tools = llm.bind_tools(TOOLS)

    # 注入动态画像
    profile_summary = get_profile_summary(user_id)
    dynamic_prompt = SYSTEM_PROMPT + f"\n\n## 当前用户画像\n\n{profile_summary}"

    # 加载历史
    history = load_history(user_id, limit=30)
    lc_messages = [{"role": "system", "content": dynamic_prompt}]
    for m in history:
        lc_messages.append({"role": m["role"], "content": m["content"]})
    lc_messages.append({"role": "user", "content": message})
    save_message(user_id, "user", message)

    # ─── 多轮工具调用循环（最多 3 轮）───
    final_content = ""
    max_tool_rounds = 3
    for round_idx in range(max_tool_rounds):
        full_response = None
        has_tool_chunks = False
        streamed_content = ""

        # 流式生成 + 检测 tool_call_chunks
        aiter = llm_with_tools.astream(lc_messages).__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(aiter.__anext__(), timeout=30.0)
                full_response = chunk if full_response is None else full_response + chunk

                # 关键：检测当前 chunk 是不是工具调用
                if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                    has_tool_chunks = True

                token = chunk.content if hasattr(chunk, "content") else ""
                # 关键：tool_call 时不要推 token（用户看不到中间过程）
                if token and not has_tool_chunks:
                    streamed_content += token
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            except StopAsyncIteration:
                break

        # 没有 tool_call → 这就是最终回答
        if not has_tool_chunks:
            final_content = streamed_content
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            break

        # 提取 tool_calls
        tool_calls = full_response.tool_calls if full_response else []

        # 把 assistant 的 tool_call 消息追加到 history
        lc_messages.append({
            "role": "assistant",
            "content": full_response.content or "",
            "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                for tc in tool_calls
            ],
        })

        # 执行工具
        for tc in tool_calls:
            result = await _execute_tool(tc["name"], tc["args"], user_id)

            # 如果是前端 action（navigate / start_interview），推到客户端
            if "action" in result:
                yield f"data: {json.dumps({'type': 'action', **result})}\n\n"

            # 把工具结果回填到 history，让 LLM 下一轮生成最终回答
            lc_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": str(result.get("data", json.dumps(result))),
            })

    # 持久化 assistant 回复
    if final_content:
        save_message(user_id, "assistant", final_content[:8000])

    # 后台：基于消息提取用户偏好（无 LLM，纯正则）
    _extract_and_update_preferences(message, user_id)
```

### 10.4 偏好提取（轻量）

```python
# assistant.py:_PREF_PATTERNS
_PREF_PATTERNS = [
    (re.compile(r"(回答|回复).{0,4}(简洁|简短)", re.I), "response_style", "简洁"),
    (re.compile(r"(回答|回复).{0,4}(详细|展开)", re.I), "response_style", "详细"),
    (re.compile(r"(难度).{0,4}(高|难|困难)", re.I), "preferred_difficulty", "困难"),
    (re.compile(r"(节奏).{0,4}(快|加快)", re.I), "interview_pace", "快"),
    (re.compile(r"(直接).{0,4}(指出|说)", re.I), "feedback_style", "直接"),
    (re.compile(r"(温柔|鼓励).{0,4}(反馈)", re.I), "feedback_style", "鼓励型"),
]

_FOCUS_PATTERN = re.compile(
    r"(?:重点|专注|多练).{0,4}(?:练习|训练|学习)\s*(.{2,15})",
    re.I,
)
```

用户说"我想多练 Redis"会自动加 `focus_topics = ["Redis"]`，下次出题会优先考虑。

### 10.5 SYSTEM_PROMPT 的精妙之处

```
## 双档运行（最重要）

### 档位 1 — 关怀/陪伴档
触发：闲聊、情绪表达、问候
风格：完整学姐风格 — 软语气词、可爱 emoji、生活化表达

### 档位 2 — 技术解释档
触发：用户问技术概念、原理、面试题
风格：保持温柔语气，但比喻必须精准，专业术语不能被替换
- 错误 ❌：「Redis 像小鱼一样游来游去存数据 🐟」（不准确，丢失"内存"核心）
- 正确 ✅：「Redis 是内存数据库哦～你可以把它想象成一个超大的字典」


## 工具调用决策树（关键）

不是所有问题都要调工具，先判断意图再决定：

| 用户意图 | 是否调工具 | 调什么 |
|---|---|---|
| "我的 XX 怎样" / "薄弱点" / "复习什么" | ✅ 必调 | `get_full_profile` / `get_due_reviews` |
| "上次那道 XX 题" | ✅ 必调 | `search_history` |
| "什么是 XX" / "XX 怎么实现" | ❌ 不调 | 直接讲技术（档位 2）|
| 语气焦虑但意图模糊 | 🤔 先共情 | 共情后问"要不要看看你的训练情况？" |
```

**核心巧思**：用 Prompt 显式约束『**什么时候调工具**』而不是让 LLM 自由发挥。这避免了两种失败模式：
1. 用户问"什么是 GIL"，AI 乱调 search_history 工具，毫无意义
2. 用户问"我的薄弱点"，AI 凭空捏造答案

### 10.6 失败兜底

| 失败 | 处理 |
|---|---|
| 工具超时（如 vector_search 60s） | 工具内部 except 返回 `{"data": "语义搜索暂不可用"}`，LLM 用文字回应 |
| 工具结果为空 | LLM 按 Prompt 要求"温柔说明没找到，绝不硬编内容" |
| LLM 流式中断 | 已积累的 streamed_content 保留，标记 done 让前端正常结束 |
| 工具调用超 3 轮 | for-else 兜底，输出"操作完成" |

### 10.7 面试演讲稿（90 秒）

"FloatingAssistant 这块是真正的 Agent 实战：单 Agent + Tool Use + 多轮对话 + 长期记忆。

设计了 **14 个工具**覆盖『导航、画像查询、训练启动、历史搜索、向量记忆检索、知识库查询』。

最难的是**多轮工具调用循环**：
- 流式生成时检测 chunk 里有没有 `tool_call_chunks`
- 有的话**不要 yield token**给前端（用户不应看到中间过程）
- 等流结束后提取完整 tool_calls，并行执行
- 把工具结果以 `role=tool` 消息回填到 history
- 进入下一轮 LLM 生成

最多 3 轮防止失控。每个工具都有 timeout 兜底（如向量检索 60s 超时返回友好错误，不让 LLM 卡死）。

Prompt 设计最得意的是**工具调用决策树**：用表格教 LLM '什么时候该调工具' —— 用户问『我的薄弱点』必调，问『什么是 GIL』不调直接讲。这避免了 LLM 乱调工具或凭空捏答案两个失败模式。

还有**双档运行**：闲聊用学姐风格 + 可爱 emoji；技术解释保持温柔但术语精准（明确禁止用『Redis 像小鱼一样游来游去』这种不准确的比喻）。

附加设计：每次用户消息都做一次**无 LLM 偏好提取**（纯正则），抓住『我想多练 Redis』这种偏好直接写进画像 preferences，影响后续出题。"

---

## 总结：如何在面试中讲十大亮点

**不要逐一念这十条**。准备好这套话术：

> "项目里我觉得最有意思的设计是这几个方向："
> 1. （亮点 2）画像系统 —— Mem0 风格两阶段更新 + 向量去重 fallback
> 2. （亮点 3）向量检索 —— 自研 SQLite + numpy 而不上 Milvus
> 3. （亮点 6）LangGraph 隐藏 EVAL —— 让 LLM 自评驱动状态机
> 4. （亮点 10）FloatingAssistant —— 14 工具的 Agent 实战
>
> "另外做了一些工程鲁棒性设计："
> 5. （亮点 4）多渠道 LLM Failover
> 6. （亮点 5）后台任务队列 + 三态熔断器
> 7. （亮点 7）流式 SSE + 增量 JSON 解析
>
> "还有一些细节亮点："
> 8. （亮点 1）三层上下文 + 出题梯度
> 9. （亮点 8）SM-2 间隔重复
> 10. （亮点 9）知识库自我进化

让面试官**挑一个让你深讲**，而不是你把所有都灌给他。
