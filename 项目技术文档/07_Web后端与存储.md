# Part 2 · 07 — Web 后端与存储

> 本章覆盖支撑整个系统的工程底座：**FastAPI**（异步 Web 框架）、**asyncio 并发模式**（这是后端性能的关键）、**SQLite/WAL 存储**（9 张表）、以及 **JWT + bcrypt 认证 + 路径穿越防护**。

---

## 1. FastAPI：现代异步 Web 框架

### 1.1 是什么 / 为什么

**FastAPI** 是基于 **ASGI**（异步服务器网关接口）的 Python Web 框架，三大卖点：
- **原生异步**：`async def` 路由，天然适合 IO 密集（LLM/DB/向量都是等网络/磁盘）。
- **Pydantic 校验**：请求/响应模型用类型注解声明，自动校验 + 生成 OpenAPI 文档。
- **依赖注入**：`Depends(...)` 把鉴权、DB 连接等横切关注点声明式注入。

本项目 16 个 router、API 版本 0.3.0，在 `main.py` 用 `app.include_router(...)` 注册。

### 1.2 异步路由与 SSE

普通 JSON 路由：

```python
@router.get("/profile")
async def get_profile(user_id: str = Depends(get_current_user)):  # 依赖注入鉴权
    return profile_service.load(user_id)
```

流式路由返回 `StreamingResponse`（包一个 async 生成器）：

```python
@router.post("/interview/start-stream")
async def start_stream(req: StartReq, user_id: str = Depends(get_current_user)):
    return streaming_response(DrillPipeline(req.topic, user_id).run())
```

### 1.3 Pydantic 模型

请求体用 Pydantic `BaseModel` 声明，FastAPI 自动解析校验。配置也用 `pydantic-settings`（`config.py`）从 `.env` 读，类型安全。

### 1.4 依赖注入做鉴权

`Depends(get_current_user)` 是最常用的注入——每个需要登录的路由声明它，FastAPI 自动先跑鉴权、把 `user_id` 注进来。`Depends(require_owner)` 则限制只有 owner 能调（如全局 AI 配置）。

---

## 2. asyncio 并发模式（后端性能的关键）

LLM 应用是**重 IO**的——大量时间在等 LLM/embedding/DB 返回。用对 asyncio 能让"等待"重叠起来。本项目用到的模式：

### 2.1 `asyncio.to_thread`：把同步阻塞调用移出事件循环

**最重要的模式**。事件循环是单线程的，任何同步阻塞调用（SQLite 查询、LlamaIndex 检索、numpy 计算、embedding 同步 SDK）都会**卡住整个事件循环**——期间所有其他请求、SSE 心跳全停。解法：

```python
result = await asyncio.to_thread(同步阻塞函数, *args)   # 丢到线程池跑
```

项目里大量使用：embedding（`vector_memory._embed`）、SQLite（checkpointer）、LlamaIndex 检索（`safe_retrieve_topic_context`）、向量排序（`search_memory`）、缓存读写。

> **面试金句**：「我不是'全异步'——LlamaIndex 检索、SQLite、embedding SDK 都是同步阻塞的。我用 `asyncio.to_thread` 把它们丢到线程池，这样既不卡事件循环（SSE 心跳还能推），`asyncio.wait_for` 也能真正取消它们。」

### 2.2 `asyncio.wait_for`：超时控制

```python
chunks, stats = await asyncio.wait_for(retrieve_for_drill(...), timeout=100.0)
```

给任何 awaitable 套硬超时，超时抛 `TimeoutError`。注意：**只有配合 `to_thread`，超时才能真正取消同步调用**（直接同步调用是没法被 `wait_for` 打断的）。

### 2.3 `asyncio.gather`：并发聚合

```python
raw = await asyncio.gather(*[_bounded(q) for q in queries],
                           return_exceptions=True)   # 多路检索并发
```

把多个独立协程并发跑、一起等结果。`return_exceptions=True` 让单个失败不炸整体（转成异常对象放回结果列表）。项目用在：多路 RAG 检索、并发工具执行、多 topic 知识检索。**墙钟时间从"各延迟之和"降到"取最大值"。**

### 2.4 `asyncio.Semaphore`：并发限流

```python
sem = asyncio.Semaphore(2)   # 最多 2 个并发
async def _bounded(q):
    async with sem: return await retrieve(q)
```

控制同时进行的并发数。项目用 `_EMBED_CONCURRENCY=2` 限制 embedding 并发——避免触发上游 per-key 限流（见 03 章）。

### 2.5 异步生成器（`async def ... yield`）

SSE 流式的核心：路由返回一个 async 生成器，FastAPI 边迭代边推给客户端。`iter_llm_stream`、`DrillPipeline.run`、assistant 的 chat 都是 async generator。

### 2.6 detached task + 引用保持

后台 fire-and-forget 任务（`embedding_tasks._track`）用 `asyncio.create_task` 起协程，**必须保留引用**（存进 `_bg_tasks` 集合）——否则可能被 GC 中途回收。完成后 `add_done_callback` 清引用 + 记录异常。这是 asyncio 的一个著名坑。

---

## 3. SQLite 存储（WAL）

### 3.1 为什么是 SQLite

单文件、零运维、嵌入式——非常适合**单机部署的中小应用**。本项目两个库：
- `data/interviews.db`：9 张业务表。
- `data/checkpoints.db`：LangGraph 状态（**独立库**，避免和业务表锁争用）。

### 3.2 WAL 模式 + busy_timeout

```python
PRAGMA journal_mode=WAL    # Write-Ahead Logging
PRAGMA busy_timeout=5000   # 锁等待 5 秒
```

- **WAL（预写日志）**：写操作先写日志、不直接改主库文件，**让"读"和"写"可以并发**（传统 rollback journal 模式下写会阻塞读）。对"一边出题落库、一边查历史"的场景很关键。
- **busy_timeout=5s**：拿不到锁时等 5 秒再报错，而不是立刻 `database is locked` 失败——吸收短暂的锁争用。

### 3.3 9 张表 schema（`storage/database.py`）

| 表 | 存什么 | 关键列/索引 |
|---|---|---|
| `users` | 账号 | id(PK)、email(UNIQUE)、password(bcrypt) |
| `sessions` | 面试/训练/录音会话 | session_id(PK)、mode、topic、questions(JSON)、scores(JSON)、overall(JSON)、review；按 user+created / user+topic 建索引 |
| `favorites` | 收藏的 Q&A | user_id、question、reference_answer、tags(JSON) |
| `algorithm_cards` | 算法题卡 | title、problem_text、solution、conversation_history(JSON) |
| `live_sessions` | 进行中会话的临时态 | session_id(PK)、data(JSON)（重启可重放） |
| `assistant_chats` | 助手对话历史 | user_id、role、content |
| `memory_vectors` | **长期向量记忆** | embedding(**BLOB**)、chunk_type、topic、user_id |
| `question_embeddings` | 题目预算 embedding（冷启动检索） | question_hash(PK)、embedding(BLOB) |
| `qa_sessions` / `qa_messages` | QA Arena 会话 + 消息 | context_summary、summary_msg_count（滚动摘要） |
| `rag_metrics` | 在线 RAG 质量信号 | relevance/coverage/diversity/faithfulness… |
| `rag_eval_runs` | 离线 RAG 评测结果 | hit_at_k、mrr、context_precision… |

> 注意 **embedding 存为 BLOB**——这就是"自研向量检索"的物理形态（04 章）：向量序列化成字节存进 SQLite，读出来 numpy 反序列化算余弦。

### 3.4 连接管理

线程局部连接（每 worker 线程一个），配合 `to_thread` 跨线程访问时 checkpointer 用 `check_same_thread=False` + SqliteSaver 内部锁串行化。

---

## 4. 认证与安全（`auth.py`）

### 4.1 bcrypt 密码哈希

```python
bcrypt.hashpw(password.encode(), bcrypt.gensalt())   # 注册：加盐哈希
bcrypt.checkpw(password.encode(), hashed.encode())   # 登录：校验
```

**bcrypt** 是专为密码设计的**慢哈希**——故意计算慢（带可调 cost），让暴力破解代价高；`gensalt()` 每次生成随机盐，**防彩虹表**。绝不存明文。

### 4.2 JWT（JSON Web Token）

```python
JWT_ALGORITHM = "HS256"; JWT_EXPIRE_DAYS = 7
jwt.encode({"sub": user_id, "exp": expire}, settings.jwt_secret, algorithm="HS256")
```

- **JWT** 是无状态认证：登录后签发一个带签名的 token，之后每个请求带它，服务端验签即可知道是谁——**不用在服务端存 session**。
- **HS256**：HMAC-SHA256 对称签名（用 `jwt_secret` 签和验）。
- 7 天过期（`exp` claim）。`get_current_user` 依赖里 `jwt.decode` 验签 + 验过期，失败抛 401。

### 4.3 路径穿越防护（一个真实的安全考量）

用户数据按 `data/users/<user_id>/` 隔离，`user_id` 会被拼进文件路径。如果 token 里的 `user_id` 是 `../../etc` 之类，就可能**路径穿越**读到别人/系统文件。防护（`auth.py:26,144-147`）：

```python
_USER_ID_PATTERN = re.compile(r"^[a-f0-9]{8}$")   # 只允许 8 位十六进制
if not _USER_ID_PATTERN.match(user_id):
    raise HTTPException(401, "Invalid token")       # 格式不对直接拒
```

`user_id` 由 `uuid4().hex[:8]` 或 `sha256[:8]` 生成，天然是 8 位 hex。任何不符合的 token 直接拒——**把路径穿越堵在鉴权层**。

### 4.4 RBAC：owner-only

全局 AI 渠道配置（LLM/embedding key）是**全局共享**的，不能让任意注册用户改（否则能把流量指向攻击者的端点、或改掉所有人的配置）。`require_owner`（`auth.py:153-162`）限制只有默认账号能调：

```python
if user_id != _default_user_id(settings.default_email):
    raise HTTPException(403, "Owner only")
```

单用户部署时这退化成 no-op，多用户时是必要的权限隔离。

### 4.5 用户数据隔离 + 新用户初始化

- 每用户独立目录 `data/users/<uid>/`：`profile/` `knowledge/` `resume/` `insights/` `high_freq/` `.index_cache/`。
- 注册时把全局知识库 + topics.json **拷贝**一份给新用户（`_init_user_knowledge`），之后各自维护、互不影响。

---

## 5. uvicorn / ASGI

**ASGI** 是异步版的 WSGI——Python 异步 Web 应用的标准接口。**uvicorn** 是高性能 ASGI server，承载 FastAPI 应用。生产里 nginx 反代 uvicorn（10 章）。

`main.py` 还用 FastAPI 的 **lifespan**（启动/关闭钩子）做：加载渠道配置、启动 embedding 任务队列、（规划中）预热共享索引治冷启动。

---

## 本章小结

- **FastAPI**：异步路由 + Pydantic 校验 + 依赖注入鉴权 + `StreamingResponse` 流式。
- **asyncio 模式**：`to_thread`（移走阻塞调用，最关键）/ `wait_for`（超时）/ `gather`（并发，墙钟取 max）/ `Semaphore`（限流）/ async generator（SSE）。
- **SQLite WAL**：读写并发 + busy_timeout 吸收锁争用；9 张表，向量存 BLOB；业务库与 checkpoint 库分离。
- **认证安全**：bcrypt 慢哈希 + JWT(HS256) 无状态鉴权 + **正则校验 user_id 防路径穿越** + owner-only RBAC + 用户目录隔离。

➡️ 下一章：[08_评测体系.md](08_评测体系.md)——离线评测矩阵、LLM-as-Judge、KL 散度、基线对比方法论。
