# 03 · 典型数据流

> 6 个典型场景，每个都从「用户点击按钮」追踪到「数据库行被写入」。
> 用途：面试官问"你这个 XX 功能怎么实现"时，能讲出完整链路而不是只点几个文件名。

---

## 数据流 1 · 专项训练（Topic Drill）：从点击到出题完成

### 用户视角

1. Home 页选「Python 核心」
2. 点击"弱点狙击站"
3. 看见进度条："正在准备知识库..." → "AI 正在生成题目..."
4. 题目逐个出现（第 1 题 3s、第 2 题 5s ...）
5. 全部 10 题加载完，进入答题界面

### 完整 trace

```
[1] 前端 Home.tsx
    onClick → startInterviewStream("topic_drill", "python", callbacks)
    ↓
[2] frontend/src/api/interview.ts:startInterviewStream
    POST /api/interview/start-stream
    body: {mode: "topic_drill", topic: "python"}
    ↓ (SSE 长连接)
[3] backend/main.py: app routes → routers/interview.py
    @router.post("/interview/start-stream")
    async def start_interview_stream(req, user_id=Depends(get_current_user))
    ↓ user_id 已由 auth.get_current_user 解出（JWT）
[4] 校验 topic 是否存在
    topics = load_topics(user_id)  # 读 data/users/{uid}/topics.json
    if req.topic not in topics: raise HTTPException(400)
    ↓
[5] 进入 stream_questions() async generator，开始流式输出 SSE
    yield "data: {progress: '正在准备知识库...'}\n\n"
    ↓
[6] init_sr_for_existing_points(user_id)
    遍历 profile["weak_points"]，给没有 SR 状态的初始化
    ↓
[7] drill_ctx = get_topic_context_for_drill("python", user_id)
    ├── _load_profile(user_id)  # 读 data/users/{uid}/profile/profile.json
    ├── 提取该 topic 的 mastery_score, weak_points, recent_questions
    └── 调用 search_memory_sync(query="python 面试薄弱点", topic="python", top_k=3)
        ├── _embed(query)  # 调 embedding API
        ├── 从 memory_vectors 表加载该用户该 topic 的向量
        └── numpy cosine + 时间衰减 → top_k 结果
    ↓
[8] due_reviews = get_due_reviews(user_id, "python")
    遍历 profile["weak_points"]，next_review <= today 的进入候选
    按 ease_factor 升序（最难的优先）
    ↓
[9] yield "data: {progress: '正在检索知识库...'}\n\n"
    ↓
[10] 知识库 RAG（两次 query 拼接）
     queries = []
     if all_weak:
         queries.append(" ".join(all_weak[:5]))
     queries.append("Python 核心 核心知识点 面试常见问题")
     
     for q in queries:
         chunks = await safe_retrieve_topic_context(topic, q, user_id, top_k=5, timeout=60.0)
         ├── build_topic_index(topic, user_id)
         │   ├── 内存缓存 hit? → 直接返回（TTL=1h）
         │   ├── 磁盘缓存 hit? → load_index_from_storage("data/users/{uid}/.index_cache/python/")
         │   └── 都 miss → 读 data/users/{uid}/knowledge/01_Python核心/*.md
         │                 → VectorStoreIndex.from_documents() (embed 所有文档)
         │                 → persist 到磁盘
         ├── index.as_retriever(similarity_top_k=5)
         └── retriever.retrieve(query) → [TextNode, ...] → 5 个 chunks
     ↓
[11] 知识 chunks 去重（按前 100 字 hash）
     ↓
[12] 加载 high_freq_questions（用户标记的高频题）
     filepath = data/users/{uid}/high_freq/python.md
     ↓
[13] 决定难度梯度（基于 mastery_score）
     0-30:  diff 1-3, 70% 概念题
     30-60: diff 2-4, 40% 深度概念 + 40% 场景
     60+:   diff 3-5, 20% 概念 + 80% 系统设计
     ↓
[14] yield "data: {progress: 'AI 正在生成题目...'}\n\n"
     ↓
[15] 拼 DRILL_QUESTION_GEN_PROMPT（~3k token）
     ↓
[16] LLM 流式调用
     ResilientChatModel.astream([SystemMessage, HumanMessage])
     ├── ChannelManager.get_channel("llm") 选优先级最高的可用渠道
     ├── 取 first_chunk 验证连通 → 失败则 get_next_channel 切换
     └── 后续 yield chunk
     ↓
[17] 边收 token 边解析 JSON
     accumulated = ""
     async for chunk in llm.astream(...):
         accumulated += chunk.content
         objects, _ = extract_complete_objects(accumulated)
         while emitted_count < len(objects):
             q = objects[emitted_count]
             emitted_count += 1
             yield f"data: {json.dumps({'type': 'question', 'data': q})}\n\n"
     ↓ (用户在前端逐题看见)
[18] 流结束后保存
     session_id = uuid.uuid4().hex[:8]
     create_session(session_id, "topic_drill", "python", questions=questions, user_id=user_id)
         → INSERT INTO sessions(session_id, mode, topic, user_id, questions, ...)
     save_live(drill_sessions, session_id, "drill", user_id, {
         "topic": "python", "questions": ..., "user_id": user_id
     })
         → drill_sessions[session_id] = data (内存)
         → INSERT INTO live_sessions(session_id, ...) (持久化)
     ↓
[19] yield "data: {type: 'done', session_id, topic, mode, total: 10}\n\n"
     ↓ (前端跳转到 /interview/{session_id})
[20] 前端 Interview.tsx
     用 location.state 拿到 {mode, topic, questions}，开始答题
```

### 数据库副作用

| 表 | 写入 |
|---|---|
| `sessions` | 1 行：session_id, mode='topic_drill', topic='python', questions=[10道题] |
| `live_sessions` | 1 行：session_id, data={questions, topic, user_id} |
| `memory_vectors` | 0 行（本流程只查不写） |

### 关键性能数字

- 首次（冷启动，索引未缓存）：~30s（embed 全部 .md 文档）
- 热路径（索引已缓存）：~3s（首道题出现）
- LLM 输出 10 题完整：~25-40s（流式期间用户已在答题）

---

## 数据流 2 · 简历模拟面试：从开始到结束

### 用户视角

1. Home 页上传简历 PDF
2. 点击"实战模拟场"
3. 看到 AI 开场："你好，请简单介绍一下你自己..."（greeting）
4. 用户回答 → AI 追问（self_intro）
5. 进入技术阶段，AI 问 5 个技术题
6. 进入项目深挖，AI 问 5 个项目题
7. 进入反问环节，AI："你有什么问题问我吗？"
8. 用户结束面试 → 看到复盘报告

### 完整 trace（已简化，跳过细节）

```
[1] 用户上传简历
    POST /api/resume/upload (form data)
    ↓
    routers/resume.py: 保存 PDF 到 data/users/{uid}/resume/
    ↓
    invalidate resume index cache (下次会重建)

[2] 开始面试
    POST /api/interview/start (mode=resume)
    ↓
    routers/interview.py:start_interview (resume 分支)
    ↓
    compile_resume_interview(user_id) → graph
       (LangGraph 5 节点：init/ask/advance/wait + START/END)
    ↓
    graph.invoke({}, config={"thread_id": session_id})
       ├── init 节点：
       │   query_resume("列出候选人的所有项目经历、技能和教育背景", user_id)
       │       → build_resume_index() (RAG)
       │       → query_engine.query(question) → 简历文本
       │   _retrieve_all_topic_knowledge(user_id) → 各 topic chunks
       │   注入 RESUME_INTERVIEWER_SYSTEM Prompt
       │   LLM 生成 greeting
       │   state = {messages: [AI 开场白], phase: "greeting", ...}
       └── wait 节点：interrupt_before 暂停

    返回 SSE: {type: "complete", session_id, message: "你好..."}
    graphs[session_id] = {graph, config, mode, user_id}  ← in-memory

[3] 用户每次答题
    POST /api/interview/chat (session_id, message)
    ↓
    entry = graphs[session_id]
    graph.update_state(config, {"messages": [HumanMessage(message)]})
    graph.invoke(None, config)
       ├── 从 wait 后续传
       ├── conditional_edges → route_after_answer(state)
       │   · 检查 last_eval.should_advance 和 count
       │   · 决定 "ask" / "advance" / "end"
       ├── 若 "ask"：
       │   ask 节点：LLM 生成下一题（带 <!--EVAL:-->）
       │   ├── _parse_inline_eval 剥离 EVAL
       │   ├── eval_history.append(eval_data)
       │   └── messages.append(AIMessage)
       │   → wait 节点暂停
       └── 若 "advance"：
           advance_phase 节点：phase 切换、reset count
           → ask 节点
           → wait 节点暂停
    ↓
    append_message(session_id, "user", message, user_id)
        → UPDATE sessions SET transcript = json_insert(...)
    append_message(session_id, "assistant", ai_message, user_id)
    ↓
    SSE: {type: "complete", message: ai_message, is_finished: false}

[4] 阶段进展示例（不绝对）
    greeting    (1 题) → self_intro
    self_intro  (2 题) → technical
    technical   (2-5 题，看 should_advance) → project_deep_dive
    project_deep_dive (2-5 题) → reverse_qa
    reverse_qa  (2 题) → END
    
    每轮 LLM 都在 EVAL JSON 里给当前回答打分和判断是否推进

[5] 结束面试
    POST /api/interview/end/{session_id}
    ↓
    graph.get_state(config) → 全部 messages + eval_history + scores
    ↓
    stream_generate_review() 生成复盘（流式 SSE）
        prompt = REVIEW_SYSTEM.format(transcript, extra_context=eval_history)
        LLM 生成 markdown 复盘报告（80-150 字 × 5 节）
    ↓
    update_profile_after_interview(mode="resume", topic=None, messages, user_id, scores)
        ├── Stage 1: EXTRACT_PROMPT 让 LLM 提取本次发现
        │   返回：{weak_points, strong_points, topic_mastery, communication,
        │          thinking_patterns, session_summary, dimension_scores, avg_score}
        └── Stage 2: llm_update_profile()
            ├── PROFILE_UPDATE_PROMPT 让 LLM 决定 ADD/UPDATE/NOOP/IMPROVE
            ├── _apply_memory_ops 执行操作
            ├── 失败 fallback 到 _deterministic_update（向量去重）
            ├── _update_mastery / _update_communication / _update_thinking_patterns
            ├── _update_stats
            ├── _save_profile (原子写)
            ├── _save_insight (写 insights/{YYYY-MM-DD}.md)
            └── schedule_session_memory_index() （后台向量化入库）
    ↓
    save_review(session_id, review, scores, weak_points, overall, user_id)
        → UPDATE sessions SET review = ..., scores = ..., weak_points = ..., overall = ...
    ↓
    extract_and_writeback(topics, questions, answers, scores, user_id)
        ↓ (异步知识沉淀，如果是 resume 模式会先 _match_resume_to_topics)
    collect_high_freq(topics, questions, scores, user_id)
        ↓ (低分题进入 high_freq/{topic}.md)
    ↓
    del_live(graphs, session_id)
        → graphs.pop(session_id)
        → DELETE FROM live_sessions WHERE session_id = ?
    ↓
    SSE: {type: "complete", review, scores, profile_update, dimension_scores}
```

### 数据库副作用

| 表 | 写入 |
|---|---|
| `sessions` | 1 行：mode='resume', transcript=[..所有对话..], scores, weak_points, overall, review |
| `live_sessions` | 1 行 → 训练结束删除 |
| `memory_vectors` | N 行（new_weak_points + session_summary，后台异步） |

### 文件系统副作用

| 文件 | 写入 |
|---|---|
| `data/users/{uid}/profile/profile.json` | 原子覆盖（threading.Lock + tempfile + os.replace） |
| `data/users/{uid}/profile/insights/{YYYY-MM-DD}.md` | append 一条 |
| `data/users/{uid}/knowledge/{topic}/自动沉淀.md` | append（高分/低分知识点） |
| `data/users/{uid}/high_freq/{topic}.md` | append（低分题） |

---

## 数据流 3 · FloatingAssistant 工具调用：用户问"我的薄弱点有哪些"

### 用户视角

1. 用户点击右下角浮窗，输入"我的薄弱点有哪些？"
2. 看到小鱼回复："让我看看你的画像..."（其实是工具调用中）
3. 然后看到完整回答："你目前有 8 个未克服的薄弱点，主要集中在 Python 和 Java 板块..."

### 完整 trace

```
[1] FloatingAssistant.tsx
    onSubmit → streamAssistantChat(message, callbacks)
    ↓
[2] POST /api/assistant/chat (body: {message: "我的薄弱点有哪些？"})
    ↓
[3] routers/assistant.py → stream_assistant_chat(message, user_id)
    ↓
[4] 加载动态 SYSTEM_PROMPT
    profile_summary = get_profile_summary(user_id)  # 把画像 summary 注入
    dynamic_prompt = SYSTEM_PROMPT + "\n## 当前用户画像\n\n" + profile_summary
    ↓
[5] 加载历史对话
    history = load_history(user_id, limit=30)  # 从 assistant_chats 表
    lc_messages = [system] + history + [{role: "user", content: message}]
    ↓
[6] save_message(user_id, "user", message)  # 立即持久化用户消息
    ↓
[7] 第 1 轮 LLM 流式（带 tools）
    aiter = llm_with_tools.astream(lc_messages)
    
    LLM 决策：用户问"薄弱点" → 触发 get_weak_points_detail 工具
    
    chunk 1: tool_call_chunks=[{name:"get_weak_points_detail", args:{}}]
    chunk 2: tool_call_chunks=[{args:" "}] (继续累积)
    chunk 3: ...
    
    has_tool_chunks = True → 不向前端推 token
    ↓
[8] 完整 tool_calls = [{"name": "get_weak_points_detail", "args": {}}]
    ↓
[9] 把 assistant 的 tool_call 消息追加到 history
    lc_messages.append({
        "role": "assistant",
        "content": "",
        "tool_calls": [{...}]
    })
    ↓
[10] 执行工具：_execute_tool("get_weak_points_detail", {}, user_id)
     profile = _load_profile(user_id)
     weak_points = [w for w in profile["weak_points"] if not w.get("improved")]
     格式化每条：
       "1. 对 GIL 在 IO 场景的释放时机不熟
          领域: python | 出现次数: 5 | 首次发现: 2026-05-01
          SR状态: 下次复习: 2026-05-19 | 间隔: 3天 | 难度系数: 1.85"
     return {"data": "薄弱点详情 (共 8 个):\n..."}
     ↓
[11] 工具结果回填
     lc_messages.append({
         "role": "tool",
         "tool_call_id": "call_xxx",
         "content": "薄弱点详情..."
     })
     ↓
[12] 第 2 轮 LLM 流式（无 tools）
     aiter = llm_with_tools.astream(lc_messages)
     LLM 基于工具结果 + 用户画像，生成自然语言回复
     
     chunk 1: content="嗯～"
     chunk 2: content="让我看了一下"
     chunk 3: content="你的画像"
     ...
     每个 chunk yield 给前端 → 用户看到实时打字效果
     ↓
[13] has_tool_chunks=False → final_content = streamed_content
     yield {type: "done"}
     break out of for loop
     ↓
[14] save_message(user_id, "assistant", final_content[:8000])
     ↓
[15] _extract_and_update_preferences(message, user_id)
     正则匹配 _PREF_PATTERNS 和 _FOCUS_PATTERN
     （这个例子用户消息没匹配到任何偏好，跳过）
```

### 数据库副作用

| 表 | 写入 |
|---|---|
| `assistant_chats` | 2 行：user 消息 + assistant 回复 |

### 关键设计点

- **第 1 轮 LLM 调用是 tool_call 决策**，不要给用户看（has_tool_chunks 时不 yield token）
- **第 2 轮 LLM 调用是最终回答**，流式给用户
- 最多 3 轮（max_tool_rounds）防止无限循环
- 每个工具调用都有 timeout 兜底（如向量检索 60s 超时返回友好错误）

---

## 数据流 4 · 录音复盘（双人模式）：上传到分析完成

### 用户视角

1. 上传一段面试录音（webm/mp3）
2. 看到进度："正在转写..." → "正在分析录音结构..." → "正在评估回答质量..."
3. 看到完整复盘：Q&A 结构化、每题评分、整体观察

### 完整 trace

```
[1] 前端 RecordingAnalysis.tsx
    Step 1: transcribeRecording(blob)
    Step 2: analyzeRecording(transcript, "dual")
    ↓
[2] Step 1 — 转写
    POST /api/recording/transcribe (form: file)
    ↓
    routers/recording.py:recording_transcribe
    ├── _upload_to_qiniu(local_path)
    │   ├── QiniuAuth(ak, sk).upload_token(bucket, key, 3600)
    │   ├── put_file(token, key, local_path)
    │   └── return f"{QINIU_DOMAIN}/{key}" (公网 URL)
    ├── POST https://dashscope.aliyuncs.com/.../audio/asr/transcription
    │   header: X-DashScope-Async: enable
    │   body: {model: "qwen3-asr-flash-filetrans", input: {file_url}}
    │   → task_id
    └── 轮询 GET /tasks/{task_id} 直到 SUCCEEDED
        → result.transcription_url
        → 下载 JSON → 提取 transcripts[*].text
    ↓
    返回 {transcript: "完整转写文本"}

[3] Step 2 — 分析（流式）
    POST /api/recording/analyze (body: {transcript, recording_mode: "dual"})
    ↓
    routers/recording.py: _stream_analyze_dual
    ↓
    Phase 1: 结构化提取
    prompt = RECORDING_STRUCTURE_PROMPT.format(transcript=transcript[:8000])
    
    async for kind, value in stream_llm_sse(messages, "正在分析录音结构"):
        if kind == "sse": yield value
        else: structure_text = value
    
    structured = _parse_json_response(structure_text)
    qa_pairs = structured["qa_pairs"]
    # = [{"id":1, "question":"...", "answer":"...", "focus_area":"...", "topic":"python", "pillar":"python"}]
    ↓
[4] Phase 2: 创建 session
    create_session(session_id, mode="recording", questions, user_id)
        → INSERT INTO sessions
    ↓
[5] Phase 3: 评估
    qa_lines = [...]  # 拼成 Markdown
    prompt = RECORDING_DUAL_EVAL_PROMPT.format(qa_pairs="...")
    
    async for kind, value in stream_llm_sse(messages, "正在评估回答质量"):
        if kind == "sse": yield value
        else: eval_text = value
    
    eval_result = _parse_json_response(eval_text)
    # = {scores: [...], overall: {avg_score, summary, new_weak_points, ...}}
    ↓
[6] 格式化复盘 + 保存
    review = format_drill_review(questions, answers, scores, overall)
    save_drill_answers(session_id, answers, user_id)
        → UPDATE sessions SET transcript = ...
    save_review(session_id, review, scores, weak_points, overall, user_id)
        → UPDATE sessions SET review = ..., scores = ..., overall = ...
    ↓
[7] 更新画像
    await _update_recording_profile(overall, scores, len(questions), user_id)
        ├── llm_update_profile(mode="recording", topic=None, ...)
        └── session_weight=0.3 (录音模式权重低，因为不是主动训练)
    ↓
[8] SSE: {type: "complete", data: {review, scores, overall, questions, answers}}
```

### 数据库副作用

| 表 | 写入 |
|---|---|
| `sessions` | 1 行：mode='recording', transcript, review, scores, overall |
| `memory_vectors` | 异步写入 weak_points + summary |

### 容易踩的坑

- **DashScope 异步 API 必须轮询**：不是同步返回，要 polling 直到 SUCCEEDED。我们最多 300 次 × 3s = 15 分钟
- **七牛 URL 必须公网可访问**：DashScope 服务器要能拉这个 URL
- **角色识别可能搞错**：Prompt 强调"多信号加权判断"，并返回 `role_confidence` 让前端决定是否提示用户校对

---

## 数据流 5 · 知识库重建：用户点"重建索引"按钮

### 用户视角

1. Knowledge 页面，点"重建 Python 索引"
2. 立即看到："已提交任务，可在进度面板查看"
3. 进度面板显示："正在重建 Python 索引（25 个文件）..."
4. 几分钟后变成"完成"

### 完整 trace

```
[1] 前端 Knowledge.tsx
    rebuildTopicIndex("python")
    ↓
[2] POST /api/knowledge/python/rebuild
    ↓
[3] routers/knowledge.py: rebuild_topic_index
    ├── 校验 topic 存在
    ├── manifest = await _submit_rebuild(topic, topic_info, user_id)
    │   ├── file_count = _count_files(user_id, "01_Python核心")
    │   ├── await asyncio.to_thread(invalidate_topic_index, topic, user_id)
    │   │   ├── _index_cache.pop((user_id, "python"))
    │   │   └── shutil.rmtree("data/users/{uid}/.index_cache/python/")
    │   └── task_id = schedule_index_rebuild(topic, user_id, file_count, label)
    │       └── _task_queue.submit(task_id, _do_index_rebuild, topic, user_id, ...)
    │           └── 入队 PriorityQueue (priority=LOW)
    │           └── _statuses[task_id] = TaskStatus(state="pending", ...)
    └── return {ok: True, task_id, file_count, message}
    ↓
[4] 前端拿到 task_id，启动轮询
    setInterval(() => getRebuildStatus(), 2000)
    ↓
[5] 后台 worker 协程拿到任务
    task = await self._queue.get()
    ├── 检查熔断器 cb.can_execute()
    │   └── 若 OPEN → sleep 65s 后重新入队
    └── 执行任务
        ├── _update_status(task_id, state="running", started_at=now, message="正在执行")
        ├── await asyncio.to_thread(_do_index_rebuild, topic, user_id)
        │   └── _do_index_rebuild():
        │       build_topic_index(topic, user_id, force_rebuild=True)
        │       ├── 读 data/users/{uid}/knowledge/01_Python核心/*.md (25 个文件)
        │       ├── VectorStoreIndex.from_documents() 
        │       │   └── 批量 embed 所有 chunks (每 chunk 1024 维)
        │       │       ├── _embed_batch_chunk 每次 10 条
        │       │       └── 总耗时 ~30-120s（取决于文件数量）
        │       └── index.storage_context.persist("data/users/{uid}/.index_cache/python/")
        ├── cb.record_success()
        └── _update_status(task_id, state="completed", finished_at=now, message="完成")
    ↓
[6] 前端轮询返回任务状态
    GET /api/knowledge/rebuild-status
    → queue.list_statuses(user_id=user_id, task_id_prefix="rebuild:{uid}:")
    → 返回 [{task_id, state, started_at, ...}]
    ↓
[7] 前端显示"完成"
```

### 失败 → 重试场景

```
任务执行中 embedding API 502
↓
cb.record_failure()  # failure_count += 1
↓
backoff = min(2 ** retry_count * 2, 30) = 4s
↓
sleep 4s
↓
await self._queue.put(task)  # 重新入队
↓
TaskStatus.state="pending", message="失败重试 1/3，4s 后重试"
↓
第 2 次执行成功 → state="completed"

或者：

第 5 次失败 → cb.failure_count >= 5 → 熔断 OPEN
↓
所有新提交的任务 sleep cb.recovery_timeout+5=65s 后重新入队
↓
65s 后熔断 HALF_OPEN → 放 2 次探活
↓
探活成功 → CLOSED 恢复 → 任务继续执行
```

### 数据库副作用

| 表 | 写入 |
|---|---|
| `live_sessions` | 0 行（任务队列纯内存） |

### 文件系统副作用

| 文件 | 写入 |
|---|---|
| `data/users/{uid}/.index_cache/python/*.json` | 覆盖（FAISS-like 持久化文件） |

### 性能数字

- 25 个 markdown 文件、~50 chunks、bge-m3 embedding API
- 耗时：~30-60s
- 期间 CPU/内存压力：低（主要瓶颈是 embedding API 调用）

---

## 数据流 6 · 问答演练场（QA Arena）的上下文压缩

### 场景

用户在问答演练场和小鱼聊了 25 轮天，第 26 轮会触发**上下文压缩**机制。

### 完整 trace

```
[1] 用户发第 26 条消息
    POST /api/qa-arena/sessions/{session_id}/chat (body: {message})
    ↓
[2] qa_arena.py:stream_qa_chat
    history = store.load_messages(session_id, user_id, limit=50)  # 假设拿到 51 条
    save_message(session_id, user_id, "user", message)
    ↓
[3] 加载长期向量记忆
    memory_ctx = await _build_memory_context(message, user_id)
    ├── search_memory(message, user_id, top_k=5)
    └── 返回："## 相关历史知识\n- [python] ...\n- [agent] ..."
    
    system_prompt = QA_ARENA_SYSTEM + memory_ctx
    ↓
[4] ★ 上下文压缩判定
    len(history) > COMPRESSION_THRESHOLD(20) → 触发压缩
    
    old_messages = history[:-KEEP_RECENT]  # 前 41 条
    recent_messages = history[-KEEP_RECENT:]  # 最近 10 条
    
    summary = await _get_or_create_summary(session_id, user_id, old_messages, len(history))
        ├── cached = store.get_context_summary(session_id, user_id)
        │   → 返回 (summary_text, summary_count) 或 None
        ├── 若 cached 且 (current_count - summary_count < SUMMARY_REGEN_INTERVAL=10)
        │   → 直接用缓存（避免每次都重新生成摘要）
        └── 否则：
            conversation = _format_conversation(old_messages)
            resp = await llm.ainvoke([
                "你是对话摘要助手。",
                COMPRESS_PROMPT.format(conversation=conversation)
            ])
            summary = resp.content[:500]
            store.save_context_summary(session_id, user_id, summary, current_count)
                → UPDATE qa_sessions SET context_summary = ?, summary_msg_count = ?
    
    system_prompt += f"\n## 之前的对话摘要\n{summary}\n\n（以下是最近的对话记录）"
    
    lc_messages = [system] + recent_messages + [user]  # 共 12 条
    ↓
[5] 流式 LLM 调用 + 推 token
    async for chunk in llm.astream(lc_messages):
        yield {type: "token", content: chunk.content}
    ↓
[6] 持久化 + 后台索引
    store.save_message(session_id, user_id, "assistant", content[:8000])
    update_profile_realtime(mode="qa_arena", ...)  # 轻量统计更新
    ↓
[7] SSE: {type: "done"}
```

### 关键设计

- **20 条阈值**：低于 20 全部喂；超 20 才压缩
- **保留最近 10 条**：保证最近上下文不丢
- **摘要缓存 10 条间隔**：每 10 条消息重新生成一次，避免每次都调 LLM
- **summary 限 500 字**：单次摘要不能太长

### 数据库副作用

| 表 | 写入 |
|---|---|
| `qa_messages` | 2 行（user + assistant） |
| `qa_sessions` | 1 行 update（updated_at, 可能 context_summary） |

---

## 总结：数据流的共性模式

读完这 6 个数据流，你应该能看出几个**架构模式**：

### 模式 1 · 写时同步、慢操作异步

凡是慢操作（embedding、索引构建）都 schedule 到后台任务队列，主请求**写完关键数据立即返回**。

### 模式 2 · 双 SSE 通道（progress + token）

LLM 流式生成的内容用 `type: token` 推送，进度信息用 `type: progress`，二者并行不冲突。

### 模式 3 · 三层缓存

- L1 内存（TTL=2h，TTLDict）
- L2 SQLite live_sessions（持久化）
- L3 sessions 表（永久）

### 模式 4 · 失败可降级，不可中断

熔断、超时、解析失败都有兜底（zero vector、fallback、占位文本），**用户体验绝不静默死掉**。

### 模式 5 · 写文件原子，读多写少

`profile.json` 是热点文件，但更新频率低（每次训练结束 1 次），用 tempfile + os.replace 保证原子性。

---

下一章 → [04 数据库与存储设计](04_DATABASE_AND_STORAGE.md)
