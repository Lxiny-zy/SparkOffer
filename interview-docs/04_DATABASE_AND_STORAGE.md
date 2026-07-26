# 04 · 数据库与存储设计

> 16 张表 + 文件系统布局。面试官问"你的数据怎么存的"时，能讲到字段、索引、迁移策略。

---

## 1. 总览

```
存储层级：
┌─────────────────────────────────────────────────┐
│ SQLite（data/interviews.db, WAL 模式）           │
│   ├── users                                       │
│   ├── sessions                                    │
│   ├── favorites                                   │
│   ├── algorithm_cards                             │
│   ├── live_sessions                               │
│   ├── assistant_chats                             │
│   ├── memory_vectors (含 BLOB embedding)          │
│   ├── question_embeddings (题目向量缓存)          │
│   ├── qa_sessions                                 │
│   ├── qa_messages                                 │
│   ├── qa_ingest_requests (入库幂等去重)           │
│   ├── rag_metrics (在线 RAG 检索指标)             │
│   ├── rag_eval_runs (离线 RAGAS 评测结果)         │
│   ├── rag_eval_start_requests (评测启动幂等)      │
│   ├── knowledge_cards (知识训练闪卡 + SM-2)       │
│   └── audit_logs (安全审计流水)                   │
├─────────────────────────────────────────────────┤
│ 文件系统                                          │
│   ├── data/users/{uid}/profile/profile.json      │
│   ├── data/users/{uid}/profile/insights/*.md     │
│   ├── data/users/{uid}/knowledge/{topic}/*.md    │
│   ├── data/users/{uid}/resume/*.pdf              │
│   ├── data/users/{uid}/high_freq/{topic}.md      │
│   ├── data/users/{uid}/topics.json               │
│   ├── data/users/{uid}/.index_cache/{topic}/...  │
│   └── data/qa_notes/{uid}/*.md                   │
└─────────────────────────────────────────────────┘
```

**为什么不全部入库**：
- markdown 文档需要用户在 UI 里手动编辑，文件系统更直观
- profile.json 需要被人类 / 脚本审查，JSON 比 SQLite 易读
- 向量索引文件（.index_cache）是 LlamaIndex 的产物，本身就是文件形式

**为什么不全部用文件**：
- sessions 有结构化查询需求（按时间排序、按 mode 过滤），SQL 更合适
- 向量检索需要批量加载，SQLite BLOB 比百万小文件高效

---

## 2. 表结构详解

### 2.1 `users` — 用户表

```sql
CREATE TABLE users (
    id         TEXT PRIMARY KEY,       -- 8 位 hex，uuid4().hex[:8] 或 sha256(email)[:8]
    email      TEXT UNIQUE NOT NULL,
    password   TEXT NOT NULL,          -- bcrypt 哈希
    name       TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**设计点**：
- `id` 是 8 位十六进制：既保证全局唯一性，又作为文件系统目录名安全（不会含 `/` `..`）
- 默认用户的 id 是 `sha256(email)[:8]`，**稳定可重现**，方便 docker rebuild 后默认用户不变
- 密码用 bcrypt 单向哈希，不可逆
- email 在数据库层 UNIQUE，并发注册同邮箱直接 sqlite3.IntegrityError

### 2.2 `sessions` — 面试会话主表

```sql
CREATE TABLE sessions (
    session_id        TEXT PRIMARY KEY,       -- 8 位 hex
    mode              TEXT NOT NULL,           -- 'resume' | 'topic_drill' | 'jd_prep'（历史行可能残留 'recording'，功能已移除）
    topic             TEXT,                    -- 训练主题 key（drill 必填，其他可空）
    meta              TEXT DEFAULT '{}',       -- JSON：company/position/preview/progress
    questions         TEXT DEFAULT '[]',       -- JSON：[{id, question, difficulty, ...}]
    transcript        TEXT DEFAULT '[]',       -- JSON：[{role, content, time}]
    scores            TEXT DEFAULT '[]',       -- JSON：[{question_id, score, assessment, ...}]
    weak_points       TEXT DEFAULT '[]',
    overall           TEXT DEFAULT '{}',       -- 整体评价 JSON
    review            TEXT,                    -- markdown 复盘报告
    reference_answers TEXT DEFAULT '{}',       -- {question_id: "参考答案"} 缓存
    user_id           TEXT,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sessions_user_created ON sessions(user_id, created_at DESC);
CREATE INDEX idx_sessions_user_topic ON sessions(user_id, topic);
```

**设计点**：
- 一张表存四种 mode，靠 `mode` 字段区分。结构灵活但有冗余（resume 不用 topic）
- 所有结构化数据存 JSON 字段。**优势**：schema 不固定时方便扩展；**劣势**：不能直接 SQL 索引 questions[] 内容
- `review IS NULL` = 未完成的会话；`review IS NOT NULL` = 已完成
- `meta.progress` 存 drill/jd_prep 的中途进度（current_index, partial_answers, hints），用于刷新页面后恢复
- 双索引：
  - `idx_sessions_user_created`：history 页按时间倒序列出
  - `idx_sessions_user_topic`：按 topic 查历史 + 图谱构建

**为什么不拆成 sessions + drill_questions + drill_answers**：
- 业务上 questions/transcript/scores 总是一起读写
- 拆表后查一次复盘要 join 4 张表，复杂度↑性能↓
- JSON 字段在 SQLite 上有 `json_insert` / `json_extract` 函数（如 `append_message` 用了 `json_insert(transcript, '$[#]', json(?))`），灵活查询

**JSON 字段的 append 操作**（避免 read-modify-write 竞态）：

```python
# storage/sessions.py:append_message
def append_message(session_id, role, content, *, user_id):
    msg_json = json.dumps({"role": role, "content": content, "time": now})
    conn.execute(
        "UPDATE sessions SET transcript = json_insert(transcript, '$[#]', json(?)), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ?",
        (msg_json, session_id, user_id),
    )
```

`'$[#]'` 表示 append 到数组末尾，是 SQLite JSON1 扩展的语法。这样**不需要先读出整个 transcript 再写回**，避免并发 race。

### 2.3 `favorites` — 收藏夹

```sql
CREATE TABLE favorites (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    session_id      TEXT,                       -- 来源 session（可空）
    question        TEXT NOT NULL,
    user_answer     TEXT DEFAULT '',
    reference_answer TEXT DEFAULT '',
    score           REAL,
    assessment      TEXT DEFAULT '',
    topic           TEXT DEFAULT '',
    difficulty      TEXT DEFAULT '',
    tags            TEXT DEFAULT '[]',          -- JSON 数组
    note            TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_favorites_user ON favorites(user_id);
CREATE INDEX idx_favorites_topic ON favorites(user_id, topic);
```

**设计点**：
- `tags` 用 JSON 数组存，**没法 SQL 直接索引**，但筛选时数据量小（用户级别），Python 端过滤可接受
- `score`, `assessment` 是冗余字段（也在 sessions.scores 里），收藏后单独存防止 session 删除丢失上下文

### 2.4 `algorithm_cards` — 算法题卡

```sql
CREATE TABLE algorithm_cards (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    title                TEXT NOT NULL,
    problem_text         TEXT NOT NULL,
    difficulty           TEXT DEFAULT '',        -- 'easy' | 'medium' | 'hard'
    tags                 TEXT DEFAULT '[]',
    solution             TEXT DEFAULT '',
    conversation_history TEXT DEFAULT '[]',      -- 完整解题对话
    source_url           TEXT DEFAULT '',         -- 题目来源链接（如 LeetCode）
    language             TEXT DEFAULT 'python',
    note                 TEXT DEFAULT '',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_algo_user ON algorithm_cards(user_id);
CREATE INDEX idx_algo_diff ON algorithm_cards(user_id, difficulty);
```

**设计点**：
- 每题保存**完整对话历史**（用户每次问 AI 都追加），方便后续复盘
- `language` 字段：同一道题可能用不同语言练，分开存

### 2.5 `live_sessions` — 进行中会话持久化

```sql
CREATE TABLE live_sessions (
    session_id   TEXT PRIMARY KEY,
    session_type TEXT NOT NULL,                -- 'drill' | 'job_prep' | 'algorithm'
    user_id      TEXT NOT NULL,
    data         TEXT NOT NULL,                 -- JSON 整个 session 状态
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**用途**：进行中的 drill 会话存储两份：
- 内存：`drill_sessions` (TTLDict)，2 小时过期
- SQLite：`live_sessions` 表，进程重启也能恢复

**清理策略**：启动时 `cleanup_expired_sessions(max_age_hours=24)` 删除 24h 前的记录。

### 2.6 `assistant_chats` — 小鱼助手历史

```sql
CREATE TABLE assistant_chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL,                 -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ac_user ON assistant_chats(user_id);
```

**设计点**：
- 没有 session_id，所有 assistant 对话都属于「用户的全局对话流」
- 加载时 `load_history(user_id, limit=30)` 只拿最近 30 条注入 LLM
- 单条 content 上限 8000 字（assistant.py:MAX_RESPONSE_STORE_LENGTH）

### 2.7 ★ `memory_vectors` — 向量记忆（核心）

```sql
CREATE TABLE memory_vectors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_type  TEXT NOT NULL,                 -- 'session_summary' | 'weak_point' | 'insight'
    content     TEXT NOT NULL,                  -- 原文
    topic       TEXT,                           -- 所属领域（可空）
    session_id  TEXT,                           -- 来源 session（可空）
    metadata    TEXT DEFAULT '{}',
    embedding   BLOB NOT NULL,                  -- ★ float32 字节，1024 维 = 4 KB
    user_id     TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_mv_type ON memory_vectors(chunk_type);
CREATE INDEX idx_mv_topic ON memory_vectors(topic);
CREATE INDEX idx_mv_user ON memory_vectors(user_id);
```

**设计点**：
- `embedding` 是 BLOB，存 `numpy.float32.tobytes()`
- `_serialize(vec) = vec.astype(np.float32).tobytes()`
- `_deserialize(blob) = np.frombuffer(blob, dtype=np.float32)`
- 不在 SQL 层做向量检索 —— 直接 `SELECT *` 全量加载后用 numpy 矩阵向量化 cosine
- 单用户上限 500 条，超过自动删最旧（`_cleanup_old_vectors`）

**为什么 chunk_type 设计三种**：
- `session_summary`：整次训练的总结（粒度大，用于宏观语义搜索）
- `weak_point`：单个薄弱点（粒度小，用于精准去重）
- `insight`：自由形式洞察（用于关联回顾）

### 2.8 `question_embeddings` — 题目向量缓存

```sql
CREATE TABLE question_embeddings (
    question_hash TEXT PRIMARY KEY,            -- md5(question)
    topic         TEXT,
    question_text TEXT,
    embedding     BLOB NOT NULL,
    user_id       TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_qe_user_topic ON question_embeddings(user_id, topic);
```

**用途**：题目关联图谱（`/api/graph/{topic}`）构建时复用 embedding，避免每次重算。

**为什么和 memory_vectors 分表**：
- 题目向量是「跨 session 共享」的，不绑定 session_id
- 缓存粒度不同：memory_vectors 是输出（写完读多），question_embeddings 是中间态（增量缓存）

### 2.9 `qa_sessions` + `qa_messages` — 问答演练场

```sql
CREATE TABLE qa_sessions (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    title             TEXT DEFAULT '新对话',
    context_summary   TEXT,                       -- ★ 上下文压缩后的摘要
    summary_msg_count INTEGER DEFAULT 0,          -- 摘要包含的消息数
    created_at        TEXT,
    updated_at        TEXT
);

CREATE TABLE qa_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_qa_sessions_user ON qa_sessions(user_id, updated_at DESC);
CREATE INDEX idx_qa_messages_session ON qa_messages(session_id, id ASC);
```

**设计点**：
- 拆成两表：sessions 是元数据，messages 是细节
- `context_summary` + `summary_msg_count` 实现摘要缓存，避免每次都让 LLM 重新摘要
- 索引 `(session_id, id ASC)` 保证按时间顺序加载消息

### 2.10 其余业务表（一句话用途）

| 表 | 用途 |
|---|---|
| `qa_ingest_requests` | QA Arena 入库幂等去重：按 `(user_id, idempotency_marker)` 唯一，重复入库请求变 no-op |
| `rag_metrics` | **在线** RAG 检索健康信号（relevance/coverage/diversity 等），随出题落库，供 RAG Dashboard 可视化 |
| `rag_eval_runs` | **离线** RAGAS 基准评测结果（hit@k / mrr / precision / recall / faithfulness…），按 job_id 归档 |
| `rag_eval_start_requests` | 离线评测启动幂等去重：缓存 `(user_id, idempotency_key)` 对应的 job 与 `response_json` |
| `knowledge_cards` | 知识训练闪卡 + SM-2 复习态；id 为确定性 kt-hash（`topic\|title\|source`），重复生成 `INSERT OR IGNORE` 精确去重 |
| `audit_logs` | 安全审计流水：鉴权事件（登录/注册/改密）与全局配置变更；append-only，仅 owner 端点查询 |

---

## 3. 索引策略

| 索引名 | 字段 | 用途 |
|---|---|---|
| `idx_sessions_user_created` | (user_id, created_at DESC) | History 页倒序列出 |
| `idx_sessions_user_topic` | (user_id, topic) | 按主题查历史 + 图谱构建 |
| `idx_favorites_user` | (user_id) | 收藏夹列表 |
| `idx_favorites_topic` | (user_id, topic) | 按主题筛选收藏 |
| `idx_algo_user` | (user_id) | 算法卡列表 |
| `idx_algo_diff` | (user_id, difficulty) | 按难度筛选算法卡 |
| `idx_ac_user` | (user_id) | 小鱼助手历史 |
| `idx_mv_type` | (chunk_type) | 向量按类型过滤 |
| `idx_mv_topic` | (topic) | 向量按主题过滤 |
| `idx_mv_user` | (user_id) | **向量按用户过滤（关键）** |
| `idx_qe_user_topic` | (user_id, topic) | 题目向量批量加载 |
| `idx_qa_sessions_user` | (user_id, updated_at DESC) | QA 会话列表 |
| `idx_qa_messages_session` | (session_id, id ASC) | QA 消息按时序加载 |

**核心原则**：所有索引都以 `user_id` 开头（除了向量字段类索引），因为**所有查询都是 per-user 的**。

**没有创建的索引（值得反思）**：
- `assistant_chats(user_id, id DESC)` — 加载历史时按 id 排序，加 `id DESC` 复合索引可优化
- `memory_vectors(user_id, chunk_type, topic)` — 复合索引可避免回表

如果面试问"你的索引哪里可以优化"，我会答这两点。

---

## 4. SQLite 配置（生产化要点）

```python
# storage/database.py:_make_connection
def _make_connection():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row                # 行可按字段名访问
    conn.execute("PRAGMA journal_mode=WAL")        # ★ WAL 模式
    conn.execute("PRAGMA synchronous=NORMAL")      # ★ 性能 vs 安全权衡
    conn.execute("PRAGMA busy_timeout=5000")       # ★ 锁等待 5s
    return conn
```

**WAL（Write-Ahead Logging）**：
- 读写不互斥（普通模式下读会阻塞写）
- 性能比 DELETE 模式（默认）快 2-3 倍
- 代价：多两个文件（`.db-wal`, `.db-shm`），备份时要一起拷贝

**synchronous=NORMAL**：
- FULL（默认）：每次写都 fsync，最安全但最慢
- NORMAL：检查点时才 fsync，崩溃可能丢最后几次写，性能好
- 我们的场景能接受（最坏情况丢一次训练的几行）

**busy_timeout=5000**：
- 写锁冲突时等 5 秒，而不是立即抛 `database is locked`
- 给并发写一个排队机会

### 线程本地连接

```python
_local = threading.local()

def get_db():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _make_connection()
        _local.conn = conn
        return conn
    try:
        conn.execute("SELECT 1")  # 健康检查
    except Exception:
        conn = _make_connection()
        _local.conn = conn
    return conn
```

**为什么每线程独立连接**：SQLite 单连接不支持多线程并发写（即使 `check_same_thread=False`），每线程独立避免锁争用。

**为什么有健康检查**：长时间不用的连接可能被 SQLite 关闭，做一次 `SELECT 1` 验证。

---

## 5. 表迁移策略（无 Alembic）

代码风格：**启动时 `init_all_tables()` 调用，幂等**。

```python
def init_all_tables():
    conn = get_db()
    
    # ─── 表 1: users ───
    conn.execute("CREATE TABLE IF NOT EXISTS users (...)")
    
    # ─── 表 2: sessions（带列迁移）───
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (...)")
    for col, default in [
        ("questions", "'[]'"),
        ("overall", "'{}'"),
        ("user_id", "NULL"),
        ("meta", "'{}'"),
        ("reference_answers", "'{}'"),
    ]:
        try:
            conn.execute(f"SELECT {col} FROM sessions LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT DEFAULT {default}")
    
    # ... 其他表 ...
    
    # ─── qa_sessions 多列迁移 ───
    try:
        conn.execute("SELECT context_summary FROM qa_sessions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE qa_sessions ADD COLUMN context_summary TEXT")
        conn.execute("ALTER TABLE qa_sessions ADD COLUMN summary_msg_count INTEGER DEFAULT 0")
```

**优点**：
- 启动幂等，重复运行无害
- 不需要外部工具（Alembic、Liquibase）
- 老用户升级版本时自动迁移

**缺点**：
- 不支持降级（rollback）
- 列重命名 / 删除需要复杂的 SQL（SQLite 不支持 DROP COLUMN < 3.35）
- 数据迁移（如「字段从 int 改成 float」）需要手写逻辑

**生产环境**：项目规模小、单库，这种方案够用。如果要 schema 频繁变更或多机部署，会换 Alembic。

---

## 6. 一次性数据迁移（migrate.py）

项目早期是单用户结构（`data/profile/`），后来改成多用户（`data/users/{uid}/profile/`）。

```python
# migrate.py
def main():
    print("[1/3] Creating default user...")
    create_default_user()        # 创建 id='default0' 的默认账号

    print("[2/3] Migrating database...")
    migrate_database()           # 给 sessions / memory_vectors / question_embeddings 加 user_id 列

    print("[3/3] Migrating files...")
    migrate_files()              # 把 data/profile, data/knowledge, data/resume 等拷到 data/users/default0/
```

**关键设计**：
- 使用 ALTER TABLE ADD COLUMN 给老数据加 user_id='default0'
- 文件用 shutil.copytree 复制（不删除原文件，方便回滚）
- 启动后再删除老目录

---

## 7. 文件系统结构

```
data/                                  ← gitignored
├── interviews.db                      ← SQLite 主库
├── interviews.db-wal                  ← WAL 文件（必须一起备份）
├── interviews.db-shm                  ← Shared memory（运行时）
├── ai_config.json                     ← 运行时 AI 配置覆盖
├── topics.example.json                ← 默认 13 个主题模板
├── topics.json                        ← 全局主题（首次注册时复制给用户）
├── knowledge/                         ← 全局知识库（首次注册时复制给用户）
│   ├── 01_Python核心/
│   │   └── *.md
│   ├── 02_LLM基础/
│   └── ...
├── users/
│   └── {user_id}/                     ← 8 位 hex，例如 'a1b2c3d4'
│       ├── profile/
│       │   ├── profile.json           ← ★ 用户画像主文件
│       │   └── insights/
│       │       ├── 2026-05-15.md      ← 每日洞察日志
│       │       └── 2026-05-16.md
│       ├── topics.json                ← 用户自定义主题
│       ├── resume/
│       │   └── *.pdf                  ← 上传的简历
│       ├── knowledge/
│       │   ├── 01_Python核心/
│       │   │   ├── README.md          ← 核心知识手稿
│       │   │   └── 自动沉淀.md         ← AI 自动写入的知识点
│       │   └── ...
│       ├── high_freq/
│       │   └── python.md              ← 低分题收集
│       └── .index_cache/
│           ├── resume/                ← 简历的 LlamaIndex 持久化
│           │   ├── default__vector_store.json
│           │   ├── docstore.json
│           │   ├── graph_store.json
│           │   ├── image__vector_store.json
│           │   └── index_store.json
│           └── python/                ← 各 topic 索引
└── qa_notes/
    └── {user_id}/
        └── 2026-05-15-Python协程笔记.md  ← QA Arena 导出的总结
```

### profile.json 结构（前面已展示）

```json
{
  "name": "Legend",
  "target_role": "通用 Agent 工程师（Python 或 Java 后端方向）",
  "updated_at": "2026-05-15T18:32:00",
  "topic_mastery": {
    "python": {"score": 65, "notes": "基础扎实但 GIL 理解浅", "last_assessed": "..."}
  },
  "previous_topic_mastery": {...},
  "weak_points": [
    {
      "point": "对 GIL 在 IO 场景的释放时机不熟",
      "topic": "python",
      "first_seen": "2026-05-01T...",
      "last_seen": "2026-05-15T...",
      "times_seen": 5,
      "improved": false,
      "sr": {
        "interval_days": 3,
        "ease_factor": 1.85,
        "repetitions": 2,
        "next_review": "2026-05-19",
        "last_score": 6
      }
    }
  ],
  "strong_points": [...],
  "communication": {
    "style": "回答技术题逻辑清晰，但项目描述缺少量化数据",
    "habits": ["遇到不会的题会坦诚说不确定"],
    "suggestions": ["项目经历多用数据指标量化成果"]
  },
  "thinking_patterns": {
    "strengths": ["能用类比解释复杂概念"],
    "gaps": ["被追问 why 时缺乏推导链路"]
  },
  "preferences": {
    "response_style": "简洁",
    "preferred_difficulty": "困难",
    "focus_topics": ["Redis", "Agent"],
    "interview_pace": "",
    "feedback_style": "直接",
    "custom_notes": []
  },
  "stats": {
    "total_sessions": 12,
    "resume_sessions": 2,
    "drill_sessions": 8,
    "job_prep_sessions": 2,
    "total_answers": 95,
    "avg_score": 6.8,
    "drill_avg_score": 7.1,
    "resume_avg_score": 6.5,
    "job_prep_avg_score": 6.3,
    "score_history": [
      {
        "date": "2026-05-15",
        "mode": "topic_drill",
        "topic": "python",
        "avg_score": 7.2,
        "question": "...",
        "dimension_scores": {...}
      }
      // ... 最多 100 条
    ]
  }
}
```

**字段语义**：
- `topic_mastery[topic].score`：0-100，加权合并历史
- `weak_points[].sr`：SM-2 状态
- `weak_points[].improved`：被 SR 算法自动毕业或显式标记
- `previous_topic_mastery`：上次更新的快照，前端用来显示「↑ 提升了 5 分」的对比

### profile.json 的并发保护

```python
# memory.py
_profile_locks: dict[str, threading.Lock] = {}
_profile_locks_lock = threading.Lock()

def _get_profile_lock(user_id):
    with _profile_locks_lock:
        if user_id not in _profile_locks:
            _profile_locks[user_id] = threading.Lock()
        return _profile_locks[user_id]


def _save_profile(profile, user_id):
    lock = _get_profile_lock(user_id)
    with lock:
        path = _profile_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        profile["updated_at"] = datetime.now().isoformat()
        data = json.dumps(profile, ensure_ascii=False, indent=2)

        # ★ 原子写：tempfile + os.replace
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".profile_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp_path, str(path))  # 原子操作
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
```

**为什么 tempfile + os.replace**：
- `os.replace` 在 POSIX 上是原子的（rename(2)）
- 写到一半进程崩了，原文件还在
- Windows 上 `os.replace` 也是原子的（自 Python 3.3 起）

**为什么 per-user 锁**：
- 不同用户的 profile.json 是独立文件，没必要互斥
- 用全局锁会让所有用户串行更新画像，吞吐下降

---

## 8. LlamaIndex 持久化布局

```
data/users/{uid}/.index_cache/{topic}/
├── default__vector_store.json    ← 主向量数据（含每个 chunk 的 embedding）
├── docstore.json                  ← 文档存储（chunk 原文 + metadata）
├── graph_store.json               ← 知识图谱（如果用到）
├── image__vector_store.json       ← 图像向量（项目里没用，空）
└── index_store.json               ← 索引元数据（chunk ID 映射）
```

**特点**：
- LlamaIndex 自己管理这些文件
- `index.storage_context.persist(persist_dir=cache_dir)` 全量写入
- `load_index_from_storage(...)` 全量读取

**为什么不入 SQLite**：
- LlamaIndex 默认就是文件 storage
- 自己写适配器（VectorStore + DocStore）开发成本高
- 这些文件只在重建时改，平时只读，磁盘 IO 不是瓶颈

---

## 9. 数据备份策略

**当前**：手动 `cp -r data/` 备份整个目录。

**为什么不复杂化**：
- SQLite WAL 模式下复制 .db + .db-wal + .db-shm 三个文件即可（启用 WAL 后冷拷贝是一致的）
- 项目数据增长慢（单用户 < 10MB），全量备份无压力

**改进方向**（生产规模需要）：
1. 定时任务：`sqlite3 interviews.db ".backup '/backup/{date}.db'"` 在线备份
2. profile.json 写入 git（用户隐私敏感场景慎用）
3. 知识库 markdown 文件用 git 单独管理

---

## 10. 跨表完整性

SQLite 没启用外键检查（默认关闭）。**业务层保证完整性**：

- 删除 session 时**不级联删除** memory_vectors（保留向量记忆）
- 删除 favorite 时不影响 session（favorite 是冗余存储）
- 删除 user 没实现（管理员手动）

**这样设计的理由**：
- 数据敏感性低
- 级联删除会丢失「即使原 session 删了，画像里的洞察还在」

---

下一章 → [05 Prompt 工程深度](05_PROMPTS_DEEP.md)
