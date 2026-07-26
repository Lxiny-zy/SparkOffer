# 02 · 十大亮点代码级 Trace

> 这是面试主战场。每个亮点都按「**是什么 / 完整 trace / 设计权衡 / 替代方案 / 演讲稿**」展开。
> 推荐准备策略：选 3 个最熟的深讲，其余 7 个能 1 分钟描述清楚就行。

## 推荐主讲（按打动力排序）

1. **亮点 2** Mem0 风格画像更新 —— **Agent 岗位最对口**
2. **亮点 3** 自研向量检索 —— **工程取舍判断力**
3. **亮点 6** LangGraph 隐藏 EVAL —— **巧思 + 实战经验**
4. **亮点 4** 多渠道 LLM Failover —— **生产化能力**
5. **亮点 10** FloatingAssistant 18 工具 —— **Function Calling 实战**

---

## 亮点 1 · 三层上下文融合 + 出题梯度策略

### 1.1 是什么

不是从固定题库随机抽题，而是在每一轮 10 道题生成前，融合三层上下文 + 一个梯度策略：

```
┌────────────────────────────────────────────┐
│ Layer 3 · 长期画像 (profile.json)            │
│   跨领域强弱项 / 思维模式 / 沟通风格 / 偏好    │
├────────────────────────────────────────────┤
│ Layer 2 · 领域掌握度 (topic_mastery)         │
│   0-100 掌握度 / 该领域薄弱点 / 历史洞察       │
├────────────────────────────────────────────┤
│ Layer 1 · 会话上下文                          │
│   知识库 RAG (5 chunks) / 简历 / JD          │
│   + 最近 20 题 negative context              │
│   + SM-2 到期复习薄弱点                       │
└────────────────────────────────────────────┘
        ↓ 注入 DRILL_QUESTION_GEN_PROMPT
   一次 LLM 调用生成 10 道题（按梯度策略）
```

### 1.2 完整 trace（drill 模式出题）

入口：`POST /api/interview/start-stream` → `routers/interview.py:start_interview_stream`

```python
# Step 1: 进度通知（立即发送，防代理 524）
yield {'type': 'progress', 'message': '正在准备知识库...'}

# Step 2: 初始化薄弱点的 SR 状态（首次有 weak_points 时）
init_sr_for_existing_points(user_id)

# Step 3: 加载用户画像 + 该领域上下文
drill_ctx = get_topic_context_for_drill(topic, user_id)
# 返回：{
#   "mastery_info": "45/100 — 基础扎实但 GIL 理解浅",
#   "mastery_score": 45,
#   "weak_points": ["对 GIL 理解停留表面", "asyncio 边界不清"],
#   "recent_questions": [最近 20 题去重用],
#   "past_insights": [向量检索的历史洞察 top 3]
# }

# Step 4: SM-2 到期复习
due_reviews = get_due_reviews(user_id, topic)
due_points = [wp["point"] for wp in due_reviews[:5]]
all_weak = list(drill_ctx["weak_points"])
for dp in due_points:
    if dp not in all_weak:
        all_weak.insert(0, dp)  # 到期的优先

# Step 5: RAG 检索（两个 query）
queries = []
if all_weak:
    queries.append(" ".join(all_weak[:5]))  # 薄弱点拼接 query
queries.append(f"{topic_name} 核心知识点 面试常见问题")  # 兜底 query
all_chunks = []
for q in queries:
    chunks = await safe_retrieve_topic_context(topic, q, user_id, top_k=5, timeout=60.0)
    all_chunks.extend(chunks)

# Step 6: 去重（按前 100 字 hash）
seen = set()
unique_chunks = []
for c in all_chunks:
    if c[:100] not in seen:
        seen.add(c[:100])
        unique_chunks.append(c)
knowledge_ctx = "\n\n---\n\n".join(unique_chunks)[:5000]

# Step 7: 梯度策略（基于 mastery_score）
if mastery_score <= 30:
    diff_min, diff_max = 1, 3
    question_strategy = "70% 基础概念 + 30% 简单应用"
elif mastery_score <= 60:
    diff_min, diff_max = 2, 4
    question_strategy = "40% 深度概念 + 40% 场景应用 + 20% 设计权衡"
else:
    diff_min, diff_max = 3, 5
    question_strategy = "20% 边界 case + 80% 系统设计 + 权衡"

# Step 8: 拼 Prompt
prompt = DRILL_QUESTION_GEN_PROMPT.format(
    topic_name=topic_name,
    knowledge_context=knowledge_ctx,
    user_profile=get_profile_summary_for_drill(user_id),  # 跨领域画像（不重复 mastery）
    mastery_info=drill_ctx["mastery_info"],
    weak_points="\n".join(weak_lines) or "暂无",
    high_freq_questions=high_freq or "暂无",  # 用户标记的高频题
    recent_questions="\n".join(f"- {q}" for q in drill_ctx["recent_questions"]) or "暂无",
    past_insights=past_insights_text or "暂无",
    question_strategy=question_strategy,
    diff_min=diff_min, diff_max=diff_max,
)

# Step 9: 流式生成 + 增量 JSON 解析
accumulated = ""
emitted_count = 0
async for chunk in llm.astream([SystemMessage(...), HumanMessage(content=prompt)]):
    token = chunk.content
    accumulated += token
    objects, _ = extract_complete_objects(accumulated)
    while emitted_count < len(objects):
        q = objects[emitted_count]
        emitted_count += 1
        yield {'type': 'question', 'data': q}  # 边解析边推送

# Step 10: 保存到 session + live_store
session_id = uuid.uuid4().hex[:8]
create_session(session_id, mode, topic, questions=questions[:10], user_id=user_id)
save_live(drill_sessions, session_id, "drill", user_id, {"topic": ..., "questions": ..., "user_id": ...})
yield {'type': 'done', 'session_id': session_id, ...}
```

### 1.3 设计权衡

**为什么不用纯 LLM 自由发挥（不喂上下文）**：纯 LLM 出题会重复，不会针对用户薄弱点，没办法做"个性化"。

**为什么不用纯题库随机**：题库永远不够大，且无法适应用户成长。

**为什么是这三层（不是 5 层 / 不是 1 层）**：
- Layer 3（长期画像）：跨领域有共性（思维模式、沟通风格），不应每个领域重复学
- Layer 2（领域掌握度）：领域独立，因为同一个人 Python 可能 80 分、Java 只有 30 分
- Layer 1（会话上下文）：每次出题都新拿，避免缓存陈旧

**为什么有梯度策略**：教育心理学的「近端发展区」(ZPD) —— 题目应略高于当前水平但不能太难。30 分以下出系统设计题会让用户彻底放弃；80 分还在出概念题用户会无聊离开。

**为什么传"最近 20 题"作为 negative context**：防止 LLM 出同质题。20 题是经验值，1000 token 左右，不会挤占太多上下文。

### 1.4 替代方案对比

| 方案 | 优点 | 缺点 | 项目为什么不选 |
|---|---|---|---|
| 纯题库随机 | 快、零成本 | 不个性化、题目枯竭 | 失去训练价值 |
| 纯 LLM 不喂上下文 | 实现简单 | 题目漂移、重复 | 用户体验差 |
| 强化学习 (RLHF) 出题 | 理论最优 | 训练样本不够、迭代慢 | 个人项目搞不动 |
| **三层融合 + 梯度策略** (我们) | 个性化 + 可控 + 工程可落地 | Prompt 长（~3k token） | 是当前性价比最高的选择 |

### 1.5 面试演讲稿（90 秒讲完）

"出题这块我做了个比较深的设计。普通的 AI 面试工具就是『LLM 给我 10 道 Python 题』，结果就是泛泛的概念题，下次还是这十道。

我做的是**三层上下文融合**：

第一层是用户的长期画像，跨所有领域的思维模式、沟通风格、薄弱点；
第二层是该领域的掌握度（0-100 分）和领域薄弱点；
第三层是知识库 RAG，但我把它当**辅助层**而不是答案层 —— 用薄弱点的文字直接当 query 去拉知识 chunk，让 LLM 看到的是『针对这个用户已知薄弱的具体知识点』。

再加一个**梯度策略**：30 分以下出概念题、30-60 出场景题、60 以上出系统设计题。借鉴的是教育心理学的『近端发展区』。

最后 Prompt 还塞了最近 20 题作为 negative context 防止重复，以及 SM-2 到期复习的薄弱点优先。

整个 Prompt 大概 3k token，一次调用拿 10 道题，**流式响应 + 增量 JSON 解析**让用户能边出题边答题，首屏从 30 秒压到 3 秒。"

---

## 亮点 2 · ★★★★★ Mem0 风格的两阶段画像更新

### 2.1 是什么

每次训练结束，画像更新不是"无脑 append 新薄弱点"，而是分两步：

```
Stage 1: Extract     从对话中抽出本次新发现
                     ↓
Stage 2: LLM Update  对比已有画像，逐条决定：
                       ADD（全新）/ UPDATE（合并相似）
                       NOOP（已覆盖）/ IMPROVE（旧短板已克服）
                     ↓
fallback: 向量去重    如果 LLM 解析失败，用 cosine ≥ 0.75 去重
                     ↓
原子写 profile.json + 入向量库 + 日志 insights/{date}.md
```

### 2.2 完整 trace

入口：训练结束 → `routers/interview.py:end_interview` → `update_profile_after_interview`

```python
# memory.py:update_profile_after_interview
async def update_profile_after_interview(mode, topic, messages, user_id, scores=None):
    profile = _load_profile(user_id)
    llm = get_langchain_llm()

    # ─── Stage 1: Extract ───
    transcript_lines = [...]  # 把 messages 拼成对话文本
    score_text = "\n".join(f"- Q: {s['question']} → {s['score']}/10" for s in scores)

    extract_msg = EXTRACT_PROMPT.format(
        current_profile=json.dumps(profile)[:2000],
        mode=mode, topic=topic,
        transcript="\n".join(transcript_lines[-60:]),
        scores=score_text,
    )

    response = await llm.ainvoke([
        SystemMessage("你是面试分析引擎。只返回 JSON。"),
        HumanMessage(extract_msg),
    ])
    extraction = json.loads(_strip_markdown(response.content))
    # extraction = {
    #   "weak_points": [{"point": "对 GIL 理解停留在表面", "topic": "python"}],
    #   "strong_points": [...],
    #   "topic_mastery": {"notes": "基础扎实但高级特性薄弱"},
    #   "communication_observations": {...},
    #   "thinking_patterns": {...},
    #   "session_summary": "本次 Python 训练...",
    #   "dimension_scores": {...},
    #   "avg_score": 6.0,
    # }

    # ─── Stage 2: LLM Update ───
    await llm_update_profile(mode, topic, ..., user_id, ...)


# memory.py:llm_update_profile
async def llm_update_profile(mode, topic, new_weak_points, new_strong_points, ...):
    profile = _load_profile(user_id)
    now = datetime.now().isoformat()

    has_new_facts = bool(new_weak_points or new_strong_points)
    if has_new_facts:
        # 把已有画像格式化为带 index 的列表
        existing_weak_lines = []
        for i, wp in enumerate(profile["weak_points"]):
            status = "已改善" if wp.get("improved") else f"出现{wp.get('times_seen',1)}次"
            existing_weak_lines.append(f"[{i}] {wp['point']} (领域: {wp['topic']}, {status})")

        # 让 LLM 决定每条新发现的操作
        prompt = PROFILE_UPDATE_PROMPT.format(
            existing_weak="\n".join(existing_weak_lines),
            existing_strong="\n".join(existing_strong_lines),
            new_weak="\n".join(new_weak_lines),
            new_strong="\n".join(new_strong_lines),
        )

        response = await llm.ainvoke([
            SystemMessage("你是画像更新引擎。只返回 JSON。"),
            HumanMessage(prompt),
        ])

        try:
            ops = _parse_json_safe(response.content)
            _apply_memory_ops(profile, ops, topic, now)  # 执行 ADD/UPDATE/NOOP/IMPROVE
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            # ─── Fallback: 向量去重 ───
            logger.warning(f"LLM parse failed ({e}), fall back to deterministic")
            _deterministic_update(profile, new_weak_points, new_strong_points, topic, now, user_id)

    # 确定性更新（不依赖 LLM）
    _update_mastery(profile, topic, topic_mastery, now, session_weight)
    _update_communication(profile, communication)
    _update_thinking_patterns(profile, thinking_patterns)
    _update_stats(profile, mode, topic, avg_score, now, answer_count, dimension_scores)

    _save_profile(profile, user_id)              # 原子写
    _save_insight(mode, topic, summary, ..., user_id)  # 日志日志日志

    # 后台索引（不阻塞）
    schedule_session_memory_index(
        session_id=None, topic=topic,
        summary=session_summary,
        weak_points=new_weak_points,
        strong_points=new_strong_points,
        insight_text=session_summary,
        user_id=user_id,
    )
```

### 2.3 PROFILE_UPDATE_PROMPT 核心逻辑

来自 `prompts/interviewer.py:PROFILE_UPDATE_PROMPT`：

```
## 语义相似度判断标准（关键）

两条记录如果根因或考察的技术能力相同就算相似，应该 UPDATE 合并；
只是涉及相关但不同的子系统/工具/概念，则独立 ADD。

### X = Y（应该 UPDATE 合并）
- 「对 GIL 理解不深」+「Python 并发模型理解薄弱」→ 同根因
- 「ThreadLocal 用法不熟」+「线程上下文传递模糊」→ 同根因
- 「RAG chunk 设计粗糙」+「检索召回率优化思路单一」→ 同根因

### X ≠ Y（独立 ADD）
- 「Pandas 数据处理不熟」+「Numpy 广播机制混乱」→ 相关但独立工具
- 「Spring AOP 不熟」+「Spring IoC 理解模糊」→ 同框架但不同子模块
- 「Agent 工具调用错误处理弱」+「Agent 记忆系统设计粗糙」→ 同 Agent 但不同子系统
```

### 2.4 设计权衡

**为什么不无脑 append**：用户练几十次 Python，会重复出错「GIL 理解不深」，朴素 append 后画像变成「GIL 理解不深 × 30」，没意义。Mem0 思路是合并成一条「GIL 理解不深，已 30 次出现」。

**为什么是两阶段（不是一阶段）**：
- 一阶段（让 LLM 直接给出"最新画像"）：LLM 容易幻觉，可能把没出现过的薄弱点也写进去
- 两阶段（Extract 只看本次对话 → Update 决策合并）：信息流明确，可审计

**为什么有 fallback 到向量去重**：LLM 偶尔会返回错位的 JSON（比如把 ops 写成字符串），直接 crash 会让一次训练白做。fallback 用向量 cosine 0.75 去重，效果差一点但能保底。

**为什么用 LLM 而不是纯向量去重**：
- 纯向量去重：召回的相似条目不一定是同根因。比如「Pandas 不熟」和「Numpy 不熟」cosine 可能 0.78，但应独立 ADD
- LLM 理解语义：能区分"同根因"和"相关工具"，更精准

**为什么画像里还存 LLM 没参与的字段**（thinking_patterns / communication / stats）：
- 这些字段是 _append-only_ 的列表，没有合并需求
- 调用 LLM 是有成本的，能确定性算的就不用 LLM

### 2.5 替代方案对比

| 方案 | 优点 | 缺点 |
|---|---|---|
| 朴素 append | 实现 5 行 | 画像无限膨胀 |
| 纯向量去重 | 确定性 | 误合并相关但不同的概念 |
| 纯 LLM 决策 | 语义最准 | LLM 偶尔崩，无 fallback |
| 直接生成完整新画像 | 简单 | 容易幻觉，丢失历史 |
| **两阶段 + LLM + 向量 fallback** (我们) | 准确 + 可靠 | 调用 2 次 LLM 成本翻倍 |

### 2.6 面试演讲稿（60 秒）

"画像更新这块我借鉴了 Mem0 的设计。原始问题是：用户练几十次 Python，会反复暴露同一个薄弱点比如『GIL 理解不深』，如果无脑 append，画像几十条都是重复的。

我的方案是**两阶段**：
- Stage 1：训练结束后让 LLM 从对话里提取**本次**新发现的薄弱点和强项
- Stage 2：把『已有画像（带 index）』和『新发现』一起喂给 LLM，让它返回 JSON 告诉我每条做 ADD / UPDATE / NOOP / IMPROVE

关键的语义判断标准我在 Prompt 里写了大量对照示例：『GIL 不熟』和『Python 并发模型不熟』算同根因要合并；『Pandas 不熟』和『Numpy 不熟』算相关但独立，要分开 ADD。

兜底机制是 LLM 解析失败时 fallback 到向量 cosine 0.75 去重，保证一次训练不会因 LLM 抽风白做。

设计哲学是『**该用 LLM 的用 LLM，能算的用算法**』：薄弱点合并用 LLM（语义判断 LLM 擅长），掌握度计算用确定性公式（不依赖 LLM 主观判断）。"

---

## 亮点 3 · ★★★★★ 自研轻量级向量检索（SQLite BLOB + numpy）

### 3.1 是什么

不引入 Milvus / Pinecone / Weaviate / Chroma 这种重型向量数据库，直接：
- **存储**：SQLite TABLE 加 BLOB 列存 float32 字节
- **检索**：numpy 矩阵向量化 cosine 相似度
- **过期**：14 天指数衰减 + 单用户 500 条上限

### 3.2 完整 trace

#### 写入 trace

```python
# vector_memory.py:index_session_memory
async def index_session_memory(session_id, topic, summary, weak_points, user_id, ...):
    conn = get_db()
    chunks = []
    if summary:
        chunks.append(("session_summary", summary, topic, session_id, "{}"))
    for wp in weak_points:
        chunks.append(("weak_point", wp["point"], wp["topic"], session_id, json.dumps({"topic": wp["topic"]})))
    if insight_text:
        chunks.append(("insight", insight_text[:2000], topic, session_id, "{}"))

    if not chunks:
        return

    # 异步批量 embedding（30s timeout × 2 次重试 × 熔断器保护）
    texts = [c[1] for c in chunks]
    vectors = await _embed_batch(texts)   # → list[np.ndarray(1024,) float32]

    now = datetime.now().isoformat()
    for (chunk_type, content, t, sid, meta), vec in zip(chunks, vectors):
        blob = _serialize(vec)   # vec.astype(np.float32).tobytes()
        conn.execute(
            "INSERT INTO memory_vectors (chunk_type, content, topic, session_id, "
            "metadata, embedding, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chunk_type, content, t, sid, meta, blob, user_id, now),
        )

    conn.commit()
    _cleanup_old_vectors(user_id)  # 超 500 条删最旧
```

#### 检索 trace

```python
# vector_memory.py:search_memory
async def search_memory(query, user_id, chunk_types=None, topic=None, top_k=5):
    conn = get_db()

    # 拼 WHERE
    where = ["user_id = ?"]
    params = [user_id]
    if chunk_types:
        placeholders = ",".join("?" for _ in chunk_types)
        where.append(f"chunk_type IN ({placeholders})")
        params.extend(chunk_types)
    if topic:
        where.append("topic = ?")
        params.append(topic)

    rows = conn.execute(
        f"SELECT id, chunk_type, content, topic, session_id, embedding, created_at "
        f"FROM memory_vectors WHERE {' AND '.join(where)}", params
    ).fetchall()

    if not rows:
        return []

    # 全量加载到 numpy 矩阵
    query_vec = await _embed(query)                               # (D,)
    embeddings = np.stack([_deserialize(r["embedding"]) for r in rows])  # (N, D)
    similarities = _cosine_similarity(query_vec, embeddings)       # (N,)

    # 应用时间衰减
    results = []
    for i, row in enumerate(rows):
        decay = _time_decay(row["created_at"])  # 0.7~1.0
        score = float(similarities[i]) * decay
        results.append({"content": row["content"], ..., "score": score})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]
```

#### 核心数学函数

```python
def _serialize(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()

def _deserialize(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)

def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """query: (D,), matrix: (N, D) → (N,)"""
    query_norm = np.linalg.norm(query_vec)
    if query_norm < 1e-10:
        return np.zeros(matrix.shape[0])
    row_norms = np.linalg.norm(matrix, axis=1)
    row_norms = np.clip(row_norms, 1e-10, None)  # 防 0
    return (matrix @ query_vec) / (row_norms * query_norm)

def _time_decay(created_at: str) -> float:
    """指数衰减。14 天半衰期，最大降低 30% 权重。"""
    age = (datetime.now() - datetime.fromisoformat(created_at)).total_seconds() / 86400
    decay = 0.5 ** (max(age, 0) / TIME_DECAY_HALF_LIFE)  # 0.5^(age/14)
    # blend: score = score * (weight * decay + (1 - weight))
    # weight=0.3 → 衰减权重最多 30%
    return TIME_DECAY_WEIGHT * decay + (1 - TIME_DECAY_WEIGHT)
```

### 3.3 数据规模分析（核心论据）

| 项 | 数值 |
|---|---|
| 单用户向量上限 | 500 条 |
| Embedding 维度（bge-m3） | 1024 |
| 单向量大小 | 1024 × 4 字节 = 4 KB |
| 单用户全量大小 | **2 MB** |
| 加载到 numpy 时间 | < 10 ms |
| 1024 维向量化 cosine（500 条） | **< 1 ms** |
| 全量检索（含 embedding + decay） | ~50 ms |

**对比 Milvus 部署成本**：
- Milvus 至少需要 3 个组件：**etcd**（元数据） + **MinIO**（对象存储） + **Milvus 主进程**
- 内存：≥ 2GB（vs 我们 2MB / 用户）
- 部署链路：docker-compose 多 4 个服务
- 备份：3 个数据源（vs 我们 `cp interviews.db`）

### 3.4 设计权衡

**为什么不用 FAISS**：FAISS 适合大规模、固定数据集的近似最近邻（HNSW、IVF）。我们是**小规模 + 频繁增删**场景，FAISS 索引重建反而慢。

**为什么不用 sqlite-vss 扩展**：增加部署复杂度（要装编译扩展），收益不明显（500 条不需要 ANN）。

**为什么 500 条**：实测 1024 维向量 × 500 条 = 2MB，单用户全量加载 + 计算 < 100ms。如果将来真的要扩到 5000 条，加 ANN（如 HNSW）就好，代码改动 30 行。

**为什么时间衰减最大降 30%**：完全衰减会丢失历史记忆，30% 是经验值 —— 旧记忆贬值但不消失。

**为什么衰减是混合权重而不是纯指数**：
```
decay = TIME_DECAY_WEIGHT * exp_decay + (1 - TIME_DECAY_WEIGHT)
       = 0.3 * 0.5^(age/14) + 0.7
```
这样保证最差情况下还有 0.7 的权重，避免旧记忆完全消失。

### 3.5 替代方案对比

| 方案 | 部署成本 | 500 条性能 | 适用规模 | 选择评分 |
|---|---|---|---|---|
| Milvus | 高（3 组件） | < 5ms (HNSW) | > 10w | ❌（过度） |
| Pinecone | 低（SaaS） | < 10ms | 任意 | ❌（要钱） |
| Chroma | 中（独立进程） | < 5ms | < 100w | ⚠️（多个进程） |
| FAISS | 低（嵌入） | < 5ms (HNSW) | > 1w | ⚠️（小规模反而慢） |
| sqlite-vss | 低（扩展） | < 10ms | < 100w | ⚠️（要装扩展） |
| **SQLite BLOB + numpy** (我们) | 零（已有 SQLite） | < 1ms | < 5000 | ✅ |

### 3.6 面试演讲稿（90 秒）

"向量检索这块我没用 Milvus 或 Chroma 这种主流方案，而是自研了 SQLite BLOB + numpy 的轻量实现。

核心数字是：**单用户向量上限 500 条，bge-m3 是 1024 维，单用户全量数据 2MB**。这种规模直接全量加载到 numpy 矩阵，做向量化 cosine 计算，**sub-ms 级**就出结果。

如果用 Milvus 反而麻烦：要部署 etcd + MinIO + Milvus 至少 3 个组件，内存最低 2GB，备份还要管 3 套数据。我们 `cp interviews.db` 就备份完了。

工程上的判断是：**ANN 算法（HNSW、IVF）的优势在大规模数据上**，500 条数据上 ANN 反而比暴力 cosine 慢，因为索引开销不划算。

设计上还做了两层：
1. **时间衰减**：14 天半衰期，最多降低 30% 权重，让旧记忆贬值但不消失
2. **超 500 条自动清理最旧的**，防止无限增长

将来如果要扩到 5000+ 用户、每用户 5000+ 向量，再加 sqlite-vss 或迁 Chroma 都不晚，代码改动也就 30 行 —— **永远不为不存在的规模过度设计**。"

---

## 亮点 4 · ★★★★ 多渠道 LLM 自动 Failover

### 4.1 是什么

用户可以配置多个 LLM 渠道（多个 API base + 多个 key + 不同模型），运行时按优先级选用，失败自动切换，错误冷却 60 秒。三层隔离机制叠加：

```
渠道级 failover：多个 API base（OpenAI / Anthropic / 自部署）
       ↓ 同渠道失败 3 次 → 冷却 60s
Key 级轮询：同渠道多 key 平摊配额
       ↓ Key 配额超限自动用下一个
错误冷却：故障渠道 60s 后自动恢复 healthy 状态
       ↓ 无人工干预
```

### 4.2 完整 trace（一次 invoke 的失败 → 切换流程）

```python
# llm_provider.py:ResilientChatModel.invoke
def invoke(self, messages, **kwargs):
    tried: set[str] = set()
    channel = get_channel("llm")  # 选第一个可用渠道

    while channel:
        try:
            result = self._make_and_bind(channel).invoke(messages, **kwargs)
            report_success("llm", channel["id"])
            return result
        except Exception as e:
            logger.warning(f"LLM channel '{channel['name']}' invoke failed: {e}")
            report_error("llm", channel["id"])         # 这个渠道 error_count += 1
            tried.add(channel["id"])
            channel = get_next_channel("llm", tried)    # 切下一个（跳过 tried）

    raise RuntimeError("All LLM channels exhausted")


# channel_manager.py:ChannelManager._select
def _select(self, section, exclude):
    channels = self._channels.get(section, [])
    states = self._states.get(section, {})
    for ch in channels:  # 按 priority 排序后的列表
        cid = ch["id"]
        if cid in exclude or not ch.get("enabled", True):
            continue
        state = states.get(cid)
        if state and not state.is_available():  # 冷却中跳过
            continue
        # 选中！进行 key 轮询
        resolved = dict(ch)
        if state:
            resolved["api_key"] = state.next_key(ch["keys"])  # round-robin
        return resolved
    return None


# channel_manager.py:ChannelState.mark_error
def mark_error(self):
    self.error_count += 1
    if self.error_count >= MAX_ERRORS_BEFORE_COOLDOWN:  # 3 次
        self.healthy = False
        self.cooldown_until = time.time() + COOLDOWN_SECONDS  # 60s
        logger.warning(f"Channel {self.channel_id} entered cooldown for 60s")


def is_available(self):
    if self.healthy:
        return True
    if time.time() >= self.cooldown_until:
        # 冷却到期自动恢复
        self.healthy = True
        self.error_count = 0
        return True
    return False
```

### 4.3 流式版本的 failover（更复杂）

```python
async def astream(self, messages, **kwargs):
    tried = set()
    channel = get_channel("llm")
    while channel:
        try:
            llm = self._make_and_bind(channel)
            aiter = llm.astream(messages, **kwargs).__aiter__()
            first_chunk = await aiter.__anext__()  # ★ 先取第一个 chunk 验证连通
            report_success("llm", channel["id"])
            yield first_chunk
            async for chunk in aiter:  # 后续直接迭代
                yield chunk
            return
        except Exception as e:
            logger.warning(...)
            report_error("llm", channel["id"])
            tried.add(channel["id"])
            channel = get_next_channel("llm", tried)
    raise RuntimeError("All LLM channels exhausted")
```

**关键设计**：第一个 chunk 之前的失败可以切换渠道。第一个 chunk 已经吐出后再失败，**没办法切换**（流式 API 的天然约束）。我们的容忍是「30 秒 timeout + 客户端 SSE 自动重连」。

### 4.4 `bind_tools` 透传

```python
def bind_tools(self, tools, **kwargs):
    """返回一个绑定了 tools 的 ResilientChatModel。"""
    bound = ResilientChatModel()
    bound._bind_args = (tools,)
    bound._bind_kwargs = kwargs
    return bound

def _make_and_bind(self, channel):
    llm = self._make_llm(channel)
    if self._bind_args:
        return llm.bind_tools(*self._bind_args, **self._bind_kwargs)
    return llm
```

**为什么这么写**：LangChain 的 `bind_tools` 返回的是 `RunnableBinding`，类型变了。如果直接 bind 一次后保存，failover 切换渠道就拿不到原 LLM。我们的方案是**保存 bind 参数**，每次切换渠道都重新 bind，工具调用语义无损。

### 4.5 设计权衡

**为什么 3 次失败才冷却**：1 次失败可能是网络抖动，2 次也可能。3 次基本能确认是渠道问题。

**为什么冷却 60 秒**：经验值。OpenAI/Anthropic 的限流窗口通常是 60 秒，过了就恢复。

**为什么用 priority 排序而不是 round-robin**：用户配置渠道时显式指定优先级（"主用 OpenAI，备用 Anthropic"）。round-robin 会平均流量，不符合"主备"语义。

**为什么 key 用 round-robin**：同渠道多 key 是为了「平摊配额」，平均流量才合理。

### 4.6 面试演讲稿（60 秒）

"生产环境最怕的是 LLM 厂商单点故障。我做了**三层隔离机制**：

1. **渠道级 failover**：用户可以配多个 API base（OpenAI / Anthropic / 自部署），失败自动切下一个
2. **Key 级轮询**：同渠道多个 key 做 round-robin，平摊配额限制
3. **错误冷却**：渠道连续 3 次失败 → 标记 unhealthy + 60 秒冷却 → 到期自动恢复，无人工干预

实现上做了一个 **ResilientChatModel** 作为 ChatOpenAI 的 drop-in replacement，对外接口完全一样（`invoke` / `ainvoke` / `astream` / `bind_tools`），LangGraph 和 LangChain 调用方完全无感。

关键巧思：`bind_tools` 不能直接绑后保存（类型会变成 RunnableBinding），我的做法是**保存 bind 参数**，每次切换渠道都重新 bind，工具调用语义完全无损。

流式响应稍微复杂一点：**第一个 chunk 之前可以切换渠道，之后就只能靠客户端重连**。这是流式 API 的天然约束。"

---

## 亮点 5 · ★★★★ 后台 Embedding 任务队列 + 三态熔断器

### 5.1 是什么

Embedding 操作（索引重建、知识沉淀、画像入库）通过异步任务队列处理，主请求秒回。队列内置优先级、去重、重试、熔断器联动。

```
TaskPriority:  HIGH（增量插入）> NORMAL（画像重建）> LOW（全量重建）
                       ↓
              asyncio.PriorityQueue
                       ↓
              2 个 worker 协程
                       ↓
        每个任务有 max_retries=3
                       ↓
        重试用指数退避：2s → 4s → 8s（最高 30s）
                       ↓
        熔断器联动：CircuitBreaker 状态机
        CLOSED (正常) → 5 次失败 → OPEN (拒绝) → 60s 后 HALF_OPEN → 2 次成功 → CLOSED
```

### 5.2 熔断器状态机详解

```python
# embedding_tasks.py:EmbeddingCircuitBreaker
class CircuitState(Enum):
    CLOSED = "closed"       # 正常运行
    OPEN = "open"           # 拒绝调用
    HALF_OPEN = "half_open" # 探活测试

class EmbeddingCircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60.0, half_open_max_calls=2):
        ...

    @property
    def state(self):
        # 自动从 OPEN → HALF_OPEN
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
        return self._state

    def can_execute(self):
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False  # OPEN

    def record_success(self):
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.half_open_max_calls:
                self._state = CircuitState.CLOSED  # 探活成功 → 恢复
        else:
            self._failure_count = max(0, self._failure_count - 1)

    def record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN  # 探活又失败，立刻断
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
```

### 5.3 与上层调用的协作

```python
# vector_memory.py:_embed
async def _embed(text: str) -> np.ndarray:
    cb = get_circuit_breaker()
    if not cb.can_execute():
        # 熔断中 → 返回零向量（业务可继续，检索质量下降）
        return np.zeros(1536, dtype=np.float32)

    for attempt in range(_MAX_EMBED_RETRIES + 1):
        try:
            vec = await asyncio.wait_for(
                asyncio.to_thread(_embed_sync, text),
                timeout=_EMBED_TIMEOUT_SECONDS,  # 30s
            )
            cb.record_success()
            return np.array(vec, dtype=np.float32)
        except asyncio.TimeoutError:
            cb.record_failure()
            if attempt < _MAX_EMBED_RETRIES:
                backoff = _RETRY_BACKOFF_BASE ** (attempt + 1)  # 1.5^(1..) = 1.5, 2.25
                await asyncio.sleep(backoff)
            ...
    return np.zeros(1536, dtype=np.float32)  # 重试耗尽
```

### 5.4 任务队列的 worker

```python
async def _worker(self, name):
    while self._started:
        try:
            task = await asyncio.wait_for(self._queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            continue  # 没活儿，再等

        self._pending_ids.discard(task.task_id)
        self._active_tasks.add(task.task_id)
        self._update_status(task.task_id, state="running", ...)
        try:
            await self._execute_task(task)
        finally:
            self._active_tasks.discard(task.task_id)
            self._queue.task_done()
            self._gc_statuses()


async def _execute_task(self, task):
    cb = get_circuit_breaker()
    if not cb.can_execute():
        # 熔断 OPEN：延迟重试
        await asyncio.sleep(cb.recovery_timeout + 5.0)
        if task.retry_count < task.max_retries:
            task.retry_count += 1
            await self._queue.put(task)
        else:
            self._stats["failed"] += 1
        return

    try:
        if asyncio.iscoroutinefunction(task.func):
            await task.func(*task.args, **task.kwargs)
        else:
            await asyncio.to_thread(task.func, *task.args, **task.kwargs)
        cb.record_success()
    except Exception as e:
        cb.record_failure()
        if task.retry_count < task.max_retries:
            task.retry_count += 1
            backoff = min(2 ** task.retry_count * 2, 30)  # 4s, 8s, 16s, cap 30
            await asyncio.sleep(backoff)
            await self._queue.put(task)  # 重新入队
```

### 5.5 状态可观测（前端可查询）

```python
@dataclass
class TaskStatus:
    task_id: str
    user_id: str
    topic: str
    label: str  # 人读"重建 python 向量索引"
    state: str  # pending | running | completed | failed
    submitted_at: float
    started_at: float
    finished_at: float
    file_count: int
    retry_count: int
    error: str
    message: str  # "失败重试 2/3，4s 后重试"
```

前端 `GET /api/knowledge/rebuild-status` 轮询，UI 实时显示「正在重建 Python 知识库（45 个文件），任务 2 个排队中」。

### 5.6 设计权衡

**为什么不引入 Celery**：Celery 至少要 Redis/RabbitMQ 一个 broker，部署多一个组件。我们单机 + 后台异步场景，asyncio + PriorityQueue 200 行搞定，性能完全够。

**为什么熔断 OPEN 时返回零向量而不抛错**：业务可继续走，只是检索质量下降。比如用户在做 drill，熔断中 RAG 检索返回空，LLM 仍能基于画像出题，体验降级而不是中断。

**为什么 max_retries=3**：3 次重试是经验值。瞬态失败（网络抖动）多半在 1-2 次能恢复；3 次还失败说明渠道有问题，进入熔断更合理。

**为什么任务去重用 task_id 而不是参数 hash**：
- 同 (user, topic) 的重建只需要做一次，task_id 是 `rebuild:{user_id}:{topic}`
- 参数 hash 会让两次提交看似不同，反而重复执行

### 5.7 面试演讲稿（60 秒）

"知识库重建、画像入库这类慢操作不能阻塞主请求。我做了个**异步任务队列 + 三态熔断器**。

任务队列大约 200 行，包含：
- **优先级队列**：HIGH（增量插入）> NORMAL > LOW（全量重建）
- **任务去重**：同 task_id 已在队列不重复入队
- **指数退避重试**：2s → 4s → 8s 最高 30s
- **熔断器联动**：embedding 服务挂了不会反复打死

熔断器是经典的三态机：
- CLOSED（正常）→ 5 次失败 → OPEN（拒绝调用）
- OPEN 60s 后 → HALF_OPEN（放 2 个探活请求）
- HALF_OPEN 2 次成功 → CLOSED（恢复）
- HALF_OPEN 任何失败 → 立刻回 OPEN

**关键决策**：熔断 OPEN 时 `_embed()` 不抛错而是**返回零向量**，业务继续走，只是检索质量短暂下降。比如用户做 drill 时熔断了，LLM 仍然能基于画像出题，只是 RAG 部分弱化，**体验降级而不是中断**。

为什么不上 Celery？单机 + 后台异步场景，Celery + Redis 部署成本高，asyncio.PriorityQueue 完全够，**自研可控、可观测**（每个任务都有 TaskStatus 给前端轮询）。"

---

## （未完，请见后半部分）

后续亮点：
- 亮点 6 · LangGraph 隐藏 EVAL 标记驱动状态机
- 亮点 7 · 流式 SSE + 增量 JSON 解析
- 亮点 8 · SM-2 间隔重复
- 亮点 9 · 知识库自动沉淀
- 亮点 10 · FloatingAssistant 18 工具 Function Calling Agent

详见 [02 十大亮点 · 下半部分](02b_HIGHLIGHTS_DEEP_part2.md)
