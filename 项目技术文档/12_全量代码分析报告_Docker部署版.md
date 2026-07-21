# SparkOffer 全量代码分析与审查报告（Docker 部署版）

> 审查基线：commit `c2684270fdb5` 加本报告所述未提交 RAG 评测实现，审查日期：2026-07-21
> 代码规模：后端约 18,886 行 Python；前端约 16,387 行 TS/TSX/CSS；FastAPI 路由 100 个
> 目标：从源码解释项目职责、实现思路、关键数据流和 Docker 真实运行方式，并给出可复核的缺陷与改进建议。
> 说明：路径后的行号均指本次审查基线，后续修改代码后可能漂移。

---

## 0. 阅读结论与使用方式

SparkOffer 是一个面向技术面试训练的多形态 AI 系统。它并非单纯的“上传文档后问答”，而是把以下能力组织成闭环：

1. 以简历面试、专项训练、JD 备战、自由问答和算法练习承接用户输入；
2. 以用户私有知识库、简历、历史薄弱点和长期向量记忆组装上下文；
3. 以 LangGraph 或显式 Pipeline 编排生成、检索、校验、评估和持久化；
4. 将评分重新写回能力画像、SM-2 复习状态、高频题和演进知识；
5. 通过在线 RAG 指标和离线 RAGAS 风格评测观察质量。

本报告应按以下顺序阅读：

- 第 1～4 章先建立 Docker 拓扑、启动时序、目录和数据模型；
- 第 5～12 章理解后端核心实现；
- 第 13 章理解前端页面、状态和接口契约；
- 第 14 章查完整 API 面；
- 第 15～17 章用于代码审查和上线整改，第 18～19 章给出设计评价与总体判断；第 20～23 章集中解释公式、功能链路、容量参数和边界条件。

### 0.1 审查方法与可信边界

本次由后端、前端、Docker/配置三个并行审查分支分别阅读源码，再由主审查交叉核对调用链。验证结果如下：

| 检查 | 结果 | 解释 |
|---|---|---|
| `pytest -q` | **71 passed, 1 failed, 1 warning** | 唯一失败是 SM-2“第三次通过即毕业”的旧测试语义与当前连续高分语义冲突，见 15.5；新增 RAG 定向测试均通过 |
| `python -m compileall -q backend scripts` | 通过 | Python 语法/字节码编译基线通过 |
| `npm run build` | 通过 | Vite 生产构建成功，约 4,143 modules |
| `npx tsc --noEmit` | 通过 | 但 `strict=false`，只能说明当前宽松类型配置可编译 |
| `npm run lint` | 命令通过但结论无效 | ESLint 仅匹配 JS/JSX，未扫描实际 TS/TSX 业务代码，见 `frontend/eslint.config.js:7-28` |
| `docker compose config --quiet` | 通过 | Compose 合并和语法有效，共 4 个服务 |
| 真实镜像 build / 容器探活 | 未执行 | 当前 Docker daemon 未运行，不能确认镜像内 `curl`、实际启动耗时和服务连通性 |
| 独立 `codex review` | 未完成 | 当前模型通道对 `codex_exec` 返回 403；不代表代码失败 |

本轮在审查基础上按需求新增并加固了 RAG 评测业务源码、Dashboard、Docker 数据集回退和测试；其他既有业务问题只记录、不擅自扩展修复范围。报告包含对当前本地数据库的只读安全核验，但不会复述任何真实 API key、JWT secret 或其他密钥。

### 0.2 最重要的结论

1. **Docker 与裸 `uvicorn` 的核心差异是向量后端。** 本地未配置时 `Settings.vector_backend_mode()` 推断为 NumPy；Compose 会将空值覆盖为 `qdrant`，知识库和长期记忆的存储、迁移、备份、失败模式都随之变化（`backend/config.py:102-113`，`docker-compose.yml:34-42`）。
2. **当前实例不应直接上线。** 当前 owner 账号仍可用公开模板默认口令验证，而 backend `9001` 和无鉴权 Qdrant `6333/6334` 均发布到所有网卡；这不是泛化建议，而是已核验的当前部署阻断项。
3. **系统整体是单进程友好、横向扩展不安全。** SQLite 和文件是真相源，但限流器、live store、索引缓存、后台任务队列、任务状态及多数锁都在进程内；直接增加 Uvicorn workers 或 backend replicas 会产生状态丢失、重复副作用和轮询 404。
4. **主业务闭环完整。** 专项训练已形成 `prepare -> retrieve -> generate -> validate -> finalize -> evaluate -> profile/SR/knowledge writeback`，并具备 SSE 进度、RAG 指标和失败后的手工同步入口。
5. **存在若干可确定复现的契约缺陷。** 例如助手读取历史/收藏的返回键错误、设置页三个 ChannelManager 相互覆盖旧快照、知识库快速切 topic 可能跨领域误写，这些不是风格问题，而是行为错误。

---

## 1. 系统架构总览

### 1.1 Docker 实际请求拓扑

```text
浏览器
  │ http://HOST:9000（生产应由 HTTPS 入口代理）
  ▼
frontend 容器：nginx:alpine，容器端口 80
  ├─ /assets/*  -> Vite 哈希静态资源，一年缓存
  ├─ /*         -> React SPA，fallback /index.html
  └─ /api/*     -> http://backend:8000，关闭缓冲，300s SSE 超时
                         │
                         ▼
backend 容器：FastAPI + Uvicorn，容器端口 8000
  ├─ SQLite：/app/data/interviews.db
  ├─ LangGraph checkpoint：/app/data/checkpoints.db
  ├─ 用户文件：/app/data/users/<uid>/...
  ├─ Redis：redis://redis:6379/0
  ├─ Qdrant：http://qdrant:6333
  └─ 外部 LLM / Embedding / Reranker API

宿主机当前还直接发布：
  9001 -> backend:8000
  6333 -> qdrant:6333
  6334 -> qdrant:6334
```

证据：`docker-compose.yml:2-74`、`frontend/nginx.conf:1-37`、`frontend/src/api/client.ts:1`。

浏览器端把 API 基址固定为相对路径 `/api`。开发时由 Vite 将它代理到 `localhost:8000`（`frontend/vite.config.js:16-20`）；Docker 时由 Nginx 转到 Docker DNS 名 `backend:8000`。因此前端构建产物不依赖某个服务器 IP，也没有运行时 `VITE_API_URL` 注入。

### 1.2 分层职责

| 层 | 主要代码 | 职责 |
|---|---|---|
| UI 与交互 | `frontend/src/pages/`、`components/` | 路由页面、图表、编辑器、流式状态、草稿和用户操作 |
| API 传输 | `frontend/src/api/` | Bearer 注入、HTTP 错误、SSE 解析、业务请求封装 |
| Web 路由 | `backend/routers/` | 鉴权、参数处理、HTTP/SSE 响应、业务编排入口 |
| Agent / 流程 | `backend/graphs/`、`assistant.py`、`qa_arena.py` | LangGraph、专项 Pipeline、JD、工具调用、问答 |
| 上下文与模型 | `llm_provider.py`、`channel_manager.py`、`context_assembler.py` | 渠道选择、失败切换、模型/embedding、token 预算 |
| RAG | `indexer.py`、`graphs/rag_retrieval.py`、`reranker.py` | 文档切片、索引、并发召回、RRF、去重、重排 |
| 个性化 | `memory.py`、`vector_memory.py`、`spaced_repetition.py` | 画像、长期记忆、掌握度和复习调度 |
| 质量闭环 | `rag_metrics.py`、`rag_eval.py`、`backend/eval/` | 在线指标、离线 gold/RAGAS、策略对比 |
| 持久层 | `backend/storage/`、`vector_store/` | SQLite、Qdrant/NumPy 向量、会话与业务实体 |
| 运行基础设施 | `redis_cache.py`、`embedding_tasks.py`、Docker/Nginx | 缓存、后台队列、容器编排和反向代理 |

### 1.3 顶层目录地图

| 路径 | 作用 | 是否运行时真相源 |
|---|---|---|
| `backend/` | FastAPI、Agent、RAG、存储和评测实现 | 代码 |
| `frontend/` | React 应用及 Nginx 镜像 | 代码/构建输入 |
| `data/knowledge/` | 仓库内置三大知识域 Markdown | 初始模板；用户运行后复制到私有目录 |
| `data/users/<uid>/` | 每用户画像、简历、知识、高频题、topic、上传和本地索引 | **是** |
| `data/interviews.db` | 业务 SQLite | **是** |
| `data/checkpoints.db` | LangGraph 进行中状态 | **是** |
| `data/ai_config.json` | UI 保存的 AI 渠道与 tuning | **是，且覆盖 `.env` 的 provider 配置** |
| `data/qdrant/` | Docker Qdrant 数据 | **是** |
| `data/redis/` | Redis RDB | 缓存，可重建 |
| `scripts/` | topic 迁移、向量迁移、索引 warmup、RAG eval | 运维工具 |
| `tests/` | 既有业务测试，以及新增的 RAG benchmark、聚合、retriever、router、store、reranker cache 测试 | 验证资产 |
| `项目技术文档/` | 原理和项目学习文档 | 文档 |
| `interview-docs/` | 架构/面试表达材料 | 文档 |

---

## 2. Docker 部署版的真实配置

### 2.1 四个服务

| 服务 | 镜像/构建 | 端口与持久化 | 启动条件/降级 |
|---|---|---|---|
| Redis | `redis:7-alpine` | `./data/redis:/data`；256 MB；`allkeys-lru`；60 秒至少一次变更时 RDB | 有 `redis-cli ping` healthcheck；代码连接失败可退内存 LRU，但 Compose 启动阶段把它当硬依赖 |
| Qdrant | `qdrant/qdrant:latest` | 发布 6333/6334；`./data/qdrant:/qdrant/storage` | 无 healthcheck，只要求容器 started；未给 Qdrant 服务配置 API key |
| Backend | `backend/Dockerfile` | `9001:8000`；整棵 `./data:/app/data`；2 GB limit | 等 Redis healthy、Qdrant started；`/docs` healthcheck |
| Frontend | `frontend/Dockerfile` | `9000:80` | 等 backend healthy；静态首页 healthcheck 使用 `curl` |

精确位置：`docker-compose.yml`、`backend/Dockerfile`。backend 镜像安装 `requirements.txt` 并复制 `backend/`、`scripts/`；固定评测集单独复制到 `/app/backend/eval/data/rag_queries.json`，避免被运行时 `/app/data` bind mount 遮蔽。宿主 `data/eval/rag_queries.json` 存在时仍优先使用，实际文件 hash 进入 manifest。frontend 使用 Node 22 构建后复制到 Nginx（`frontend/Dockerfile:1-12`）。

### 2.2 配置覆盖优先级

理解以下三层是分析 Docker 版本的前提：

```text
Compose 插值层
  宿主 shell / --env-file > 根 .env > ${VAR:-default}
       │
       ▼
容器进程环境层
  compose service.environment > compose env_file > Settings 类默认值
       │
       ▼
AI 运行时层（仅 LLM / embedding / reranker / tuning）
  data/ai_config.json channels > .env 合成的 fallback channel
```

具体结果：

- `REDIS_URL` 被 Compose 固定为 `redis://redis:6379/0`，根 `.env` 中同名值不能覆盖；
- `.env` 中 `VECTOR_BACKEND` 为空时，`${VECTOR_BACKEND:-qdrant}` 仍得到 `qdrant`；
- `.env` 中 `QDRANT_URL` 为空时，容器得到 `http://qdrant:6333`；
- `QDRANT_API_KEY` 默认空，只传给 backend client，不会为 Qdrant 服务开启鉴权；
- `data/ai_config.json` 由设置页原子写入并位于挂载卷内，重建镜像后仍优先于 `.env` 的 LLM/embedding/reranker 配置。

证据：`docker-compose.yml:34-42`、`backend/config.py:8-142`、`backend/ai_config.py:42-49,135-221,225-255`、`backend/llm_provider.py:247-270,469-502`。

### 2.3 Docker 与裸启动差异

| 行为 | 裸 `uvicorn backend.main:app` | 当前 Docker Compose |
|---|---|---|
| HTTP 入口 | 通常 `localhost:8000` | 浏览器走 `:9000` Nginx；backend 另暴露 `:9001` |
| 前端代理 | Vite `/api -> localhost:8000` | Nginx `/api -> backend:8000` |
| 向量后端 | `VECTOR_BACKEND`、`QDRANT_URL` 都空时 NumPy | 默认强制 Qdrant |
| 长期记忆 | SQLite `memory_vectors` + NumPy 余弦 | Qdrant `sparkoffer_memory` collection |
| 知识索引 | 用户 `.index_cache` 本地 SimpleVectorStore | `kb_<uid>_<topic>` Qdrant collection |
| Redis | 空 URL 时内存 LRU | 始终启动并注入 Redis URL |
| 上传上限 | 后端知识文件单文件 200 MB | 先被 Nginx 全 `/api` 32 MB 拦截 |
| 时区 | 跟随本机 Asia/Shanghai | 容器通常 UTC，Compose 未设 `TZ` |
| local embedding | 安装额外 requirements 后可用 | 镜像未安装 `requirements.local-embedding.txt`，模板配置不可直接工作 |
| 多进程 | 默认单 Uvicorn 进程 | 仍是单进程；不能把 restart policy 误解为横向扩展 |

### 2.4 持久化与备份边界

| 宿主数据 | 容器路径/消费者 | 内容 | 正确备份方式 |
|---|---|---|---|
| `data/interviews.db` | backend | 用户、会话、收藏、算法卡、QA、指标、训练卡、审计等 | SQLite `.backup`，不能只热拷主文件 |
| `data/checkpoints.db` | LangGraph SqliteSaver | 进行中的简历面试状态 | 单独 SQLite `.backup` |
| `data/users/<uid>/` | backend | profile、resume、knowledge、high_freq、topics、qa uploads | 停写后文件快照；`.index_cache` 可重建 |
| `data/ai_config.json` | backend | 渠道、明文 keys、tuning | 限权后加密备份，恢复时与 `.env` 一起审计 |
| `data/qdrant/` | Qdrant | KB 和长期记忆向量 | 使用 Qdrant snapshot API，不应在线复制底层目录 |
| `data/redis/` | Redis | 可丢缓存/RDB | 通常可重建；若要保留用 Redis 原生流程 |
| `.env` | Compose/backend | auth、fallback provider、Compose 插值 | 独立加密备份，权限至少 600 |

`DEPLOYMENT.md:125-145` 当前只说备份 `data/` 和 `.env`，没有保证 SQLite WAL、Qdrant snapshot 与跨存储一致性。生产恢复必须在维护窗口做一致性快照并实际演练。

---

## 3. 后端启动与请求生命周期

### 3.1 启动顺序

FastAPI lifespan 位于 `backend/main.py:19-95`，顺序有实际语义：

1. `init_config()` 读取/迁移 `data/ai_config.json`，加载渠道；
2. `init_all_tables()` 建表、索引并做兼容迁移；
3. 检查默认 JWT secret 和默认账号口令，但当前仅打印 warning，不拒绝启动；
4. 解析 effective embedding 配置，创建 embedding client；
5. `_init_llama_settings()` 把 embedding/LLM 注入 LlamaIndex 全局 Settings；
6. 清理超过时限的 live sessions；
7. `ensure_default_user()` 创建或迁移默认 owner，并初始化用户目录；
8. 初始化 Redis，连接失败时构造内存 LRU；
9. 打印 LLM/embedding 多渠道数量；
10. 启动进程内 embedding 任务队列；
11. 后台 warmup 第一个用户的知识索引，不阻塞 HTTP ready；
12. 关闭时取消 warmup 并停止任务队列。

应用对象、CORS 和 15 个 router 的注册见 `backend/main.py:97-121`。默认 CORS 是 `*`（`backend/config.py:48-50`），Docker 同源代理本不需要放开所有 origin。

### 3.2 一次受保护请求

```text
React api helper
  -> 从 localStorage 取 token
  -> Authorization: Bearer <JWT>
  -> Nginx 保留 /api 路径并反代
  -> FastAPI Depends(get_current_user)
  -> jose 解 HS256 / exp / sub
  -> 校验 sub 格式 [a-zA-Z0-9_-]{1,64}
  -> 业务查询始终追加 user_id 条件或进入 user_data_dir(user_id)
```

关键实现：`frontend/src/api/client.ts:26-45`、`backend/auth.py:131-160`。用户 ID 格式检查同时防止把 JWT `sub` 用作路径穿越载荷。

### 3.3 同步、异步和线程边界

项目大量第三方调用仍是同步接口，因此路由常用 `asyncio.to_thread` 将 SQLite、LlamaIndex 或同步 LLM 工作移出事件循环。专项检索额外使用 semaphore 控制并发，SSE 生成器负责不断让出数据。

这个设计在单进程下可工作，但必须注意：

- `to_thread` 被取消并不等于底层线程停止；
- 进程退出时仍在写 Qdrant/manifest 的线程可能继续到强制终止；
- `asyncio.shield` 保护的是协程取消，不是进程崩溃；
- 任何 async 函数里的同步 `future.result()` 都会重新阻塞整个事件循环，助手当前正有此问题（`backend/assistant.py:685-700`）。

---

## 4. 认证、租户隔离与数据模型

### 4.1 认证实现

| 能力 | 实现 | 代码位置 |
|---|---|---|
| 密码 | bcrypt hash/verify | `backend/auth.py:32-37` |
| 默认 owner | 邮箱 SHA-256 前 8 位生成 stable user id；初始化私有目录 | `backend/auth.py:40-92` |
| 注册 | 开关、invite code、邮箱格式、密码长度、唯一邮箱 | `backend/auth.py:95-117` |
| 登录 | 查询邮箱并验证 bcrypt | `backend/auth.py:120-128` |
| Token | HS256，`sub=user_id`，有效期 7 天 | `backend/auth.py:22-23,131-137` |
| 请求鉴权 | Bearer decode、用户 ID 格式检查；当前 dependency 不查询 `users` 表 | `backend/auth.py:140-160` |
| Owner 权限 | default email 对应 user 才可访问设置/admin/debug | `backend/auth.py:163-177` |
| 登录限流 | 按 IP 的进程内滑动窗口 | `backend/routers/auth.py:21-87` |
| 审计 | login/register/password/settings 事件写 `audit_logs` | `backend/storage/audit.py:17-51` |

改密接口已经检查新密码最小长度（`backend/routers/auth.py:106-115`），不应误报为缺失。更实际的问题是 JWT 为无服务端 session 的长效 token：改密码不会自动撤销已签发 token，发生凭证泄露时需要轮换 `JWT_SECRET` 或引入 token version/revocation。

### 4.2 用户文件隔离

`backend/config.py:71-92` 将每个用户的数据映射为：

```text
data/users/<uid>/
  profile/profile.json
  resume/*.pdf
  knowledge/<topic-dir>/*.md
  high_freq/<topic>.md
  topics.json
  .index_cache/<topic>/...
  qa_uploads/<session-id>/...
```

主题 key、文件名和 user id 在不同入口有白名单/解析保护，例如 knowledge 文件名会取 basename 并限制扩展名（`backend/routers/knowledge.py:25-57`）。SQLite 查询大多显式带 `user_id`。但 schema 基本没有外键和级联删除，隔离靠应用层纪律而不是数据库约束；删除 session/topic 后容易遗留关联数据。

### 4.3 SQLite 设计

`backend/storage/database.py:19-30` 使用 thread-local connection，并设置：

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
```

WAL 提高读写并发，5 秒 busy timeout 避免短锁立刻失败；代价是备份必须包含 WAL 一致性。主要表如下：

| 表 | 作用 | 核心字段/说明 | 代码 |
|---|---|---|---|
| `users` | 账号 | id/email/password/name | `database.py:50-59` |
| `sessions` | 各类面试统一记录 | mode/topic/meta/questions/transcript/scores/overall/review/reference_answers/user_id | `database.py:61-86` |
| `favorites` | 面试题收藏 | 问题、答案、参考答案、评分、tag/note | `database.py:88-105` |
| `algorithm_cards` | 算法题卡 | 题面、解法、对话、语言、标签 | `database.py:107-124` |
| `live_sessions` | 可恢复的进行中 drill/JD/algorithm 上下文 | JSON data，默认清理旧记录 | `database.py:126-136` |
| `assistant_chats` | 浮动助手历史 | role/content/user_id | `database.py:138-147` |
| `memory_vectors` | NumPy 模式长期记忆 | embedding BLOB + type/topic/session/user | `database.py:149-167` |
| `question_embeddings` | 能力图题目 embedding 缓存 | question_hash 为主键 | `database.py:169-183` |
| `qa_sessions` / `qa_messages` | Q&A Arena | 标题、摘要、消息、图片文件名 | `database.py:185-220` |
| `rag_metrics` | 在线 session/stage 指标 | relevance/coverage/diversity 和答案指标 | `database.py:251-282` |
| `rag_eval_runs` | 离线 RAG 基准 run | hit@k/MRR/precision/recall/faithfulness 等 | `database.py:284-320` |
| `knowledge_cards` | 知识训练卡与 SM-2 状态 | 复合主键 `(user_id,id)`、source、next_review | `database.py:322-354` |
| `audit_logs` | 安全审计 | event/user/email/ip/detail | `database.py:356-373` |

`sessions` 将复杂对象存 JSON，便于模式扩展并减少 join；代价是数据库无法约束 JSON 内部结构，也无法按单题高效查询。`backend/storage/sessions.py:8-278` 负责序列化、恢复、分页、进度、reference answer 和 synced marker。

### 4.4 文件画像为何不是纯数据库

`backend/memory.py` 把 `profile.json` 作为画像真相源：

- `DEFAULT_PROFILE` 定义统计、强弱点、主题掌握、行为与沟通模式；
- 每用户 `threading.Lock` 包住 read-modify-write；
- 临时文件写完后 `os.replace`，避免半 JSON；
- `profile_transaction()` 允许 SR 和画像更新共享同一临界区；
- JSON 便于人工查看、导出和作为 LLM 上下文。

这是单进程内合理的工程折中，但 `threading.Lock` 不能协调多个 backend 容器。横向扩展前必须改成数据库事务、分布式锁或单写者服务。

---

## 5. AI 配置、渠道池与模型调用

### 5.1 运行时配置

`backend/ai_config.py` 管理 `data/ai_config.json`：

1. 启动加载磁盘 JSON（`ai_config.py:42-49`）；
2. 将旧 flat config 迁成 channels（`ai_config.py:52-83`）；
3. 没有运行时 channel 时，从 `.env` 构造 fallback channel（`ai_config.py:85-133`）；
4. 保存时写临时文件再 `os.replace`，随后 bump config version、reload channel manager 和 client singleton（`ai_config.py:159-221`）；
5. tuning 对 output/context/retrieval 参数做默认值和 clamp（`ai_config.py:328-403`）。

该文件会保存完整 provider key，设置接口也会把 channel 对象返回浏览器（`backend/routers/settings_router.py:86-98`）。因此它不是普通偏好设置，而是高敏感密钥库。

### 5.2 ChannelManager

`backend/channel_manager.py:16-223` 为 LLM、embedding、reranker 各维护一组 `ChannelState`：

- 按 `enabled`、tier 和 priority 选择 channel；
- channel 内多 key 轮询；
- 记录连续错误；3 次错误进入约 60 秒 cooldown；
- cooldown 后允许 HALF_OPEN 探测；成功复位，失败继续冷却；
- 用线程锁保护状态选择和错误上报。

这比简单 `try provider A, except provider B` 更稳健，因为它避免持续把流量打到已知故障通道，也允许自动恢复。但健康状态只在当前进程，多个 worker 的熔断判断会分叉。

### 5.3 ResilientChatModel

`backend/llm_provider.py:205-332` 用统一 wrapper 实现 `invoke`、`ainvoke`、`astream`：

- 同 channel 可先用下一把 key 重试；
- 瞬态网络、超时、429、5xx 可切换 channel；
- 明确的 4xx 配置/鉴权错误被视为 fatal，直接暴露而不盲目切换；
- 流式响应只允许在第一个可见 chunk 之前 failover，发出部分文本后不再切换，避免拼接两家模型输出；
- httpx sync/async client 按代理与 timeout 缓存复用（`llm_provider.py:87-149`）；
- reasoning 模型可设置 `reasoning_effort`，output token 从 tuning 解析。

`get_langchain_llm()` 服务业务 Agent，`get_llama_llm()` 适配 LlamaIndex；`get_embedding()` 用于查询，`get_embedding_for_index()` 用于批量建索引（`llm_provider.py:334-533`）。查询和索引 embedding 配置分离，便于索引侧 batch/timeout 优化，但必须保证模型维度和语义空间一致。

### 5.4 ContextBudget

`backend/context_assembler.py` 解决“简历、画像、知识、历史全塞 prompt”问题：

- 优先使用 tiktoken `cl100k_base`；不可用时按 ASCII/CJK 字符启发估算（`context_assembler.py:38-84`）；
- 读取所有 enabled LLM channel 中最小 context window，避免 failover 到小窗口时溢出（`context_assembler.py:96-134`）；
- 从窗口减去 output reserve 和 2,000 token safety margin（`context_assembler.py:136-143`）；
- `Section` 带 priority、required、min_tokens，按优先级贪心装配并在段落/句子边界截断（`context_assembler.py:146-268`）；
- `pack_messages()` 保留近期对话（`context_assembler.py:270-327`）。

边界缺陷：`resolve_input_budget()` 最低强制 4,000 token；若实际 channel window 小于 reserve + margin + 4,000，预算仍会超过模型窗口。required section 也不会截断，异常超大必选段仍可溢出。

---

## 6. 知识库索引与 RAG 检索

### 6.1 Topic 与文档组织

`backend/indexer.py:206-224` 从用户 `topics.json` 读取 topic 配置，topic 再映射到 `knowledge/<dir>` 和 `high_freq` 文件。新用户初始化由 `backend/auth.py:40-57` 将全局模板复制到私有目录。

这里有一个 fresh clone 缺陷：仓库忽略 `data/topics.json`，只跟踪 `data/topics.example.json`；初始化代码却只复制前者。全新部署若没有手工生成 `data/topics.json`，`load_topics()` 会返回空对象，前端没有领域可展示。更进一步，当前 example 仍是旧 13 目录映射，与仓库现有 python/java/agent 三目录不一致，不能直接无脑复制。

### 6.2 Markdown 到向量索引

`backend/indexer.py` 的核心过程：

1. 枚举用户 topic 目录中的 Markdown/PDF 等知识源；
2. 使用 heading-aware 解析保留章节语义，再以 `SentenceSplitter` 将节点控制在约 1,024 token（`indexer.py:229-290`）；
3. 对每个源文件计算 hash，并在 manifest 中保存（`indexer.py:114-177`）；
4. 比较新增、修改、删除集合，选择复用、增量更新或重建；
5. embedding 后写本地 SimpleVectorStore 或 Qdrant；
6. 将已加载 index 放进进程内 TTL/LRU cache：约 1 小时、最多 50 项（`indexer.py:69-112`）。

本地模式路径见 `indexer.py:606-744`；Qdrant 路径见 `indexer.py:292-330,426-603`。知识 collection 命名为 `kb_<uid>_<topic>`（经过清洗/截断）。这让删除和隔离简单，但用户×topic 增长会造成 collection 数量膨胀。

`backend/vector_store/__init__.py:36-60` 对长期记忆的 qdrant backend 初始化失败会抛错，并不会自动降级 NumPy。文件顶部旧 docstring 所称“自动 fallback”已经过时，应以工厂代码和 qdrant-only 注释为准。这个选择避免故障期间双写分叉，但要求部署侧真正监控 Qdrant。

### 6.3 缺索引时的降级

请求侧使用 `safe_retrieve_topic_context*()`（`backend/indexer.py:1096-1151`）：

- 将同步 LlamaIndex 查询放入 `asyncio.to_thread`；
- 有总 timeout；
- index 不存在/未 ready 时返回空上下文；
- 后台提交 rebuild，当前请求继续生成而不是直接 500。

这保证可用性，但“HTTP 服务健康”不等于“RAG 已可用”。首次 Docker 启动时 Qdrant 只是 started，warmup 又是 fire-and-forget，早期请求可能拿到无知识上下文的答案。

### 6.4 专项训练的多路召回

`backend/graphs/rag_retrieval.py:57-181` 不只用一个泛化 query：

1. 从画像取当前 topic 的弱点和到期复习项，最多选择 5 个检索 query；
2. 无弱点时使用通用 topic query；
3. semaphore 限制 embedding/检索并发，每路有 timeout；
4. 每路返回按相关度排序的 chunk；
5. 用 Reciprocal Rank Fusion 合并：

```text
RRF(c) = Σ 1 / (60 + rank_i(c))
```

实现见 `rag_retrieval.py:183-195`。RRF 只依赖名次，不强行比较不同 query 的原始相似度尺度。

6. 对候选 embedding，以 cosine threshold（默认 0.85）做语义去重（`rag_retrieval.py:197-300`）；
7. 若配置 Cross-Encoder，则以弱点组合 query 重排；失败时保留融合结果（`rag_retrieval.py:302-337`）；
8. 输出 raw/fused/dedup/final 数量、cache hit、reranker 状态供 SSE 和指标展示。

### 6.5 Reranker

`backend/reranker.py` 把候选 query-document pair 发送到兼容 API。Bi-encoder embedding 适合大范围召回；Cross-Encoder 联合阅读 query 和 chunk，成本更高但排序更精。项目因此采用“两阶段检索”：先 dense recall，再对少量候选重排。reranker 不是硬依赖，未配置或超时会降级。

### 6.6 索引后台队列

`backend/embedding_tasks.py:188-520` 是进程内 `asyncio.PriorityQueue`：默认 2 个 worker、队列容量 100，带任务状态、去重、retry/backoff 和独立熔断器。任务类型包括 topic rebuild、增量插入、画像向量重建、session memory index（`embedding_tasks.py:526-627`）。

需要特别区分“设计声明”和“当前实现”：`schedule_index_rebuild()` 文档宣称可根据 manifest 增量 diff，但 `_do_index_rebuild()` 固定调用 `build_topic_index(..., force_rebuild=True)`（`embedding_tasks.py:601-612`）。因此知识编辑/上传触发的普通 rebuild 实际也是全量重嵌入。

队列、状态、pending id、active task 和熔断器全在内存。容器重启后任务丢失，多 worker 下提交与轮询可能落在不同进程，状态会 404；`stop()` 取消 worker 却无法终止已经进入 `to_thread` 的底层写操作（`embedding_tasks.py:227-235`）。

---

## 7. 长期画像、向量记忆与间隔重复

### 7.1 Mem0 风格画像更新

`backend/memory.py` 采用 Extract -> Update 两阶段：

1. 从本轮 transcript、评分和旧画像提取候选事实；
2. 让 LLM 为每项输出 `ADD / UPDATE / NOOP / IMPROVE`；
3. 解析失败时不丢整轮，退到确定性的文本/语义去重；
4. 在 `profile_transaction()` 中合并强弱点、统计、行为和沟通模式；
5. 原子写回 `profile.json`；
6. 异步调度画像向量重建。

实现集中在 `backend/memory.py:318-552`。这种设计将“模型提出修改”和“程序执行修改”分开，比直接让模型重写完整 JSON 更可控。

已发现一个 LLM 输出防御缺口：`_apply_memory_ops()` 假设 operation 的 `index` 是 int；若模型返回字符串 `"1"`，范围比较可能触发 `TypeError`，使整次画像更新失败（`memory.py:493-552`）。

### 7.2 掌握度算法

每次命中某薄弱点时，先计算本题贡献：

```text
contribution = (clamp(difficulty, 1, 5) / 5)
             * (clamp(score, 0, 10) / 10)
             * 100
```

已有 mastery 使用 EWMA：

```text
new_mastery = 0.7 * old_mastery + 0.3 * contribution
```

代码：`backend/spaced_repetition.py:116-159`。EWMA 强调近期表现；难题高分贡献大，简单题即使满分也不会立即把掌握度拉满。

### 7.3 SM-2 调度

`backend/spaced_repetition.py:31-71` 将 0～10 分映射成 0～5 quality（`int(score/2)`）：

- score < 6：失败，repetitions 归零、interval 回 1 天；
- 第一次通过：1 天；
- 第二次通过：3 天；
- 之后：`round(previous_interval * ease_factor)`；
- ease factor 依据 quality 调整，下限 1.3；
- `next_review` 写日期。

弱点“毕业”采用更严格规则：score >= 7 才增加 `consecutive_high`，连续 3 次才 `improved=True` 并加入 strong points（`spaced_repetition.py:140-174`）。`repetitions` 表示 SM-2 的所有通过次数，不等于连续高分次数。

### 7.4 长期向量记忆

`backend/vector_memory.py` 将 session/画像切成 weak point、strong point、experience 等 chunk，embedding 后写 `AbstractVectorStore`：

- NumPy 模式写 SQLite BLOB，查询时矩阵余弦；
- Docker 默认 Qdrant 使用单一 `sparkoffer_memory` collection，以 payload 的 `user_id/chunk_type/topic/session_id` 过滤；
- 查询结果应用时间衰减，半衰期约 14 天，最大降权约 30%（`vector_memory.py:251-265`）；
- 每用户最多保留约 500 条 memory，避免无限增长；
- 语义弱点匹配阈值约 0.75，用于合并措辞不同但含义相近的弱点。

Qdrant payload 和过滤实现见 `backend/vector_store/qdrant_store.py:63-165`。查询失败时 `vector_memory.py:325-358` 返回空结果，业务仍运行；它不会切回 SQLite，因此从 NumPy 切 Docker Qdrant 前必须显式执行迁移脚本并对账。

---

## 8. 面试与训练编排

### 8.1 模式边界

`backend/models.py:12-27` 声明四种 mode：

| mode | 当前入口 | 编排方式 | 结果沉淀 |
|---|---|---|---|
| `resume` | `/api/interview/start` | LangGraph 多轮对话 | transcript、inline eval、review、画像 |
| `topic_drill` | `/api/interview/start-stream`（主路径）或 `/start`（兼容同步路径） | 五阶段 DrillPipeline + 批量评分 | 单题分数、弱点、SM-2、画像、知识演进、RAG 指标 |
| `jd_prep` | `/api/job-prep/preview` + `/start` | JD 专用生成流程，结束时复用 interview end | 蓝图、题目、维度分、画像、知识演进 |
| `recording` | 当前无创建 API/UI 入口 | 仅 enum、历史图谱和 Review 保留兼容分支 | 属于 legacy/预留能力 |

`/interview/start` 只接受 topic drill 和 resume，其他 mode 明确 400（`backend/routers/interview.py:127-202`）。因此不能仅凭 enum 或 Review 分支宣称录音训练已可用。

### 8.2 专项训练生成主链

主入口 `POST /api/interview/start-stream` 位于 `backend/routers/interview.py:205-221`，它校验 mode/topic 后返回 `DrillPipeline.run()` 的 SSE。Pipeline 在 `backend/graphs/drill_pipeline.py:60-123` 固定经过五阶段：

```text
prepare -> retrieve -> generate -> validate -> finalize
```

#### Prepare：把用户状态变成出题计划

`drill_pipeline.py:183-237` 读取：

- topic 名称和题数配置；
- profile 中本 topic 的 active weak points；
- SM-2 已到期项；
- high-frequency 文档；
- 难度 anchor 和历史题/seed pool；
- 上下文预算。

它先确定题目槽位和难度分布，再让后续检索与生成围绕弱点工作，避免“有画像但出题仍随机”。

#### Retrieve：并发 RAG

`drill_pipeline.py:238-377` 调用第 6.4 节的 `retrieve_for_drill()`，把弱点 query、多路检索、RRF、去重、rerank 结果写入 `ctx`。若知识索引未就绪，设置 `index_not_ready` 并继续；SSE 的 stage detail 会明确显示本轮空上下文和后台重建，而非假装命中知识库。

#### Generate：流式结构化出题

`drill_pipeline.py:378-629` 将系统约束、topic、画像、SR、high-frequency、RAG chunks 和 seed 装入 prompt。LLM 输出 JSON 时不等待整段结束：`backend/utils/stream_parser.py` 跟踪字符串、转义和花括号深度，从 partial buffer 中提取完整 object；每得到一题就发 `question`，修补同题时发 `question_update`。

保留 seed 的思路是：确定性题池先提供基础覆盖，LLM 补足个性化和新题；即使模型部分输出损坏，也尽量 salvage 已完整对象，而不是整批报废。

#### Validate：确定性护栏

`drill_pipeline.py:630-750` 运行三个 validator：

| Validator | 目的 | 代码 |
|---|---|---|
| WeakPointCoverage | 弱点题比例是否达到计划 | `backend/graphs/validators/weak_point_coverage.py:25-97` |
| SemanticDuplicate | 题间 embedding 相似度是否过高 | `semantic_duplicate.py:21-111` |
| DifficultyDistribution | 实际难度分布与目标差异，含 symmetric KL | `difficulty_distribution.py:22-95` |

校验失败可触发修补/替换，而不是重新生成全部题。这样将 LLM 的创造力和程序的可测规则分开。

#### Finalize：建立可恢复会话

`drill_pipeline.py:751-805` 创建 `sessions`，保存 live session，持久化 question generation RAG metrics，最后发 `complete/done`。前端仍兼容 legacy 的 `progress/question/done`，同时消费 `pipeline_stage` 和 `rag_metrics`。

### 8.3 专项训练作答与结束

前端 `frontend/src/pages/Interview.tsx:68-237` 先尝试路由 state，刷新后再从 `/interview/session/{id}` 恢复；答案同时写 localStorage 草稿和 `/progress` SQLite。用户逐题填写、跳过或请求 hint/reference answer（`Interview.tsx:339-456`），结束时一次提交 answers。

`POST /api/interview/end/{session_id}` 的 drill 分支位于 `backend/routers/interview.py:281-480`：

1. 从 live store 获取 topic/questions；若进程重启或 TTL 淘汰，则从持久 session 重建；
2. 先保存用户 answers，保证后续评估失败时仍能恢复；
3. 若存在 `small` tier，使用 `decoupled_eval.py:50-174` 并发逐题评分，再由大模型总结；失败回退 legacy batch stream；
4. clamp LLM 返回的 RAG 分数到 0～10；
5. 保存 answer-level RAG metrics；
6. 生成 review 并保存 scores/overall/weak points；
7. 首次评分才执行 SR、profile、知识演进，避免重复统计；
8. 标记 `meta.synced_at`，删除 live entry；
9. 发 `complete` 后前端跳 Review。

持久化段由 `asyncio.shield` 保护（`interview.py:429-468`），可抵抗浏览器在评估结束瞬间断开导致的协程取消。但 `save_review -> profile/SR/knowledge -> mark synced` 不是跨 SQLite、JSON、Qdrant 的原子事务，进程崩溃仍可能只完成一半。

`POST /api/interview/sync/{session_id}` 是手工补偿入口（`interview.py:660-723`）：读取已经持久化的 scores，不重跑昂贵 LLM 评估，再补 SR/画像/知识副作用；以 `synced_at` 做幂等判断。当前 marker 检查和副作用并非一个事务，并发两次 sync 仍有重复应用窗口。

### 8.4 简历 LangGraph

状态定义在 `backend/models.py:30-43`，包括 messages、phase、冻结 system prompt、resume/knowledge context、questions_asked、阶段题数、最后 eval 和 eval_history。

`backend/graphs/resume_interview.py:273-296` 构图：

```text
START -> init -> wait [interrupt]
                   │ 用户回答后恢复
                   ▼
            route_after_answer
              ├─ ask -> wait
              ├─ advance -> ask -> wait
              └─ END
```

阶段顺序为 `greeting -> self_intro -> technical -> project_deep_dive -> reverse_qa -> end`。核心设计点：

- init 一次性读取简历、画像和跨 topic 知识，冻结稳定 system prefix，提高 prompt cache 命中（`resume_interview.py:106-142`）；
- interviewer 在可见回答末尾附隐藏 `<!--EVAL:{...}-->`，程序剥离标记后把 score/should_advance/brief 写状态（`resume_interview.py:61-104,143-212`）；
- conditional edge 同时考虑模型建议、每阶段题数和硬上限，防止模型一直不推进；
- `interrupt_before=["wait"]` 实现人在环，每次等待用户回答；
- checkpoint 使用独立 `data/checkpoints.db` 的 `SqliteSaver`，`thread_id=session_id`（`backend/graphs/checkpointer.py:25-46`）。

路由只把 graph object 缓存在内存；重启或命中另一 worker 时，`_get_resume_graph()` 依据 live metadata 重新 compile，再从 SqliteSaver checkpoint 恢复（`backend/routers/interview.py:79-103`）。这是比“把不可序列化 graph 塞数据库”更正确的边界。

### 8.5 JD 备战

JD 流程位于 `backend/graphs/job_prep.py` 和 `backend/routers/job_prep.py`：

1. preview 读取 JD 文本、可选简历、画像；按关键词匹配相关 topics；并发查询 resume index 和 topic index（`job_prep.py:29-105`）；
2. LLM 输出岗位画像、技能要求、匹配度、风险点、准备建议，程序 normalize（`job_prep.py:108-184`）；
3. start 可复用前端传回的 preview，再生成 4～8 道定向题（`job_prep.py:187-248`）；
4. 创建 `jd_prep` session/live entry，并用 SSE 返回（`backend/routers/job_prep.py:21-103`）；
5. end 复用 interview route 的保存、评分、画像和知识演进分支（`backend/routers/interview.py:482-659`）。

前端 `frontend/src/pages/JobPrep.tsx:24-114` 给 JD+resume payload 计算签名；用户修改 JD 后不会误用旧 preview。后端 Pydantic 模型却没有 `jd_text/preview_data` 大小上限，且 start 对客户端回传的 preview_data 信任过多（`backend/models.py:63-71`）。

---

## 9. 知识维护、训练卡与知识演进

### 9.1 知识库 CRUD

`backend/routers/knowledge.py` 提供：

- core 文件列表、创建、编辑、删除、批量上传（`knowledge.py:91-220`）；
- LLM 自动生成初始知识（`knowledge.py:221-263`）；
- high-frequency 读取/编辑（`knowledge.py:264-291`）；
- 单 topic/全部异步 rebuild、任务状态（`knowledge.py:292-373`）；
- 文件数、chunk 数、索引时间等统计（`knowledge.py:374-465`）。

编辑走 atomic write helper，随后 invalidate/rebuild。上传允许 Markdown/TXT/PDF，单文件后端上限 200 MB（`knowledge.py:16-22`）。Nginx 全 `/api` 只有 32 MB，所以 Docker 实际契约更小。

Topic 本身由 `backend/routers/profile.py:22-72` 写用户 `topics.json`。新增时建立 knowledge/high_freq 目录并触发索引；删除当前只删配置与向量索引，不删源目录、高频文件或 cards，同 key 重建后旧内容可能重新出现。

### 9.2 知识训练卡生成

`backend/knowledge_training.py` 是“从文档生成可复习卡”的领域逻辑：

1. 按 Markdown H1～H6 建层级 section，长纯文本再切片（`knowledge_training.py:142-208`）；
2. 去除标题/内容重复，按长度、代码/结构等质量过滤（`knowledge_training.py:209-267`）；
3. `random` 从所有 section 取样，`high_freq` 优先高频来源；同一 header group 做连贯采样（`knowledge_training.py:268-322`）；
4. 构造 basic/understand/interview_expression 三种深度 prompt（`knowledge_training.py:325-348`）；
5. 从流式 LLM 文本 salvage JSON，规范 knowledge/example/question/answer（`knowledge_training.py:349-511`）；
6. 校验 source_ref 必须能映射到实际 section；过滤“请参见原文”等低质量卡（`knowledge_training.py:512-592`）；
7. 用 topic/title/source 生成稳定 `kt-...` hash，实现精确幂等去重。

Route `backend/routers/knowledge_training.py:90-169` 流式生成后 `upsert_cards()`；已保存、到期、review 分别在 `:47-89`。`backend/storage/knowledge_cards.py:35-171` 保存卡与来源覆盖，并在 review 时把 `known/uncertain/unknown` 映射成得分后调用 SM-2。

### 9.3 知识演进

专项评估完成后，`backend/knowledge_evolution.py` 将训练结果反哺知识：

- 高分且答案有价值的 Q&A 经 LLM 提取成可复用知识块（`knowledge_evolution.py:58-104`）；
- 全局 asyncio lock + atomic write 追加到“自动沉淀”Markdown，再调度增量 embedding；
- 低分题追加到 high-frequency（`knowledge_evolution.py:107-136`）；
- Q&A Arena 的知识卡必须由用户明确点击，随后 topic 分类、factualize、去重并写入对应知识库（`knowledge_evolution.py:181-275`）。

“用户显式确认 + 模型事实化 + 程序去重”能降低聊天幻觉直接污染知识库的风险。锁仍只在单进程有效，多容器会 lost update。

---

## 10. 其他功能模块

### 10.1 Q&A Arena

后端分为薄路由 `backend/routers/qa_arena.py:9-159`、领域逻辑 `backend/qa_arena.py`、SQLite 仓储 `backend/storage/qa_sessions.py`：

- session/title/message CRUD；
- 最多 4 张图片，每张最多 6 MB，支持 PNG/JPEG/WebP/GIF；base64 解码后写用户私有 `qa_uploads/<sid>`（`qa_arena.py:225-299`）；
- 取长期画像和向量记忆构造 memory context（`qa_arena.py:328-427`）；
- 长对话按 recent messages + context_summary 控制上下文；摘要记录对应 msg_count（`qa_arena.py:428-482`）；
- 流式输出 reasoning/token/stage/ping，首 token 前可 provider failover，中途失败保存 partial answer 并发 error（`qa_arena.py:488-649`）；
- regenerate 删除最后 assistant message 后用相同历史重答（`qa_arena.py:676-696`）；
- 总结短会话单次完成，长会话 map-reduce；可下载 Markdown 或生成知识卡（`qa_arena.py:699-877`）。

前端 `frontend/src/pages/QAArena.tsx:73-1011` 实现会话列表、per-session 草稿、图片预览、停止、重生成、reasoning/stage、总结和收录。它用请求代次避免已完成的 A 会话响应覆盖 B，但加载失败时仍可能把旧 messages 暂时挂在新 activeId 下。

### 10.2 Floating Assistant 与工具调用

`backend/assistant.py:96-328` 声明约 14 类工具，包括导航、启动训练、查历史、查收藏、读画像/复习、知识检索等。`stream_assistant_chat()`（`assistant.py:731-890`）流程：

1. 读取助手历史和动态画像；
2. 为 tool schemas 预留约 4,000 token，再用 ContextBudget 压缩历史；
3. `llm.bind_tools(TOOLS)`，流式收集普通文本或 tool_call_chunks；
4. 最多 3 轮工具调用；一轮多个工具并行执行；
5. action 类型通过 SSE 交给前端执行导航/启动；data 类型回填 ToolMessage 让模型继续回答；
6. 保存用户/assistant 历史，并异步用关键词提取偏好。

`frontend/src/components/FloatingAssistant.tsx:60-570` 实现全局浮窗、历史/欢迎、拖动吸边、peek、SSE 和 action dispatch，因此无论处于哪个业务页都可调用。

当前存在两个确定性实现错误：

- assistant 的 search_history/welcome 读取 `.get("sessions")`，仓储实际返回 `{items,total}`（`backend/assistant.py:337-348,928-951` 对比 `backend/storage/sessions.py:175-243`）；
- list_favorites 读取 `favorites`，仓储实际返回 `items`（`backend/assistant.py:357-363` 对比 `backend/storage/favorites.py:34-64`）。

因此有数据时也会误报空。另一个性能问题是 async `_execute_tool` 内直接 `future.result(timeout=60)`（`assistant.py:685-700`），会阻塞事件循环和 SSE 心跳。

### 10.3 算法解题与题卡

`backend/routers/algorithm.py:23-168` 覆盖：

- solve：题面+语言进入专用 prompt，SSE 生成分析、复杂度和代码；
- chat：基于 live algorithm session 继续追问；
- save：把题面、最终解法和 conversation 存为题卡；
- cards/tags：筛选、分页、编辑、删除；
- export：Markdown 或 JSON。

持久逻辑在 `backend/storage/algorithm.py:16-168`。前端 `AlgorithmSolver.tsx:25-200` 负责离页 abort 和恢复题卡；`AlgorithmCollection.tsx:38-137` 提供筛选、批删、导出和重新打开。

### 10.4 收藏

`backend/routers/favorites.py:15-75` 和 `backend/storage/favorites.py:15-148` 将面试单题的用户答案、参考答案、分数、评语、topic、难度、tag/note 保存为独立卡片，并支持过滤和 Markdown/JSON 导出。前端 `Favorites.tsx:38-128` 提供标签编辑、单删/批删和导出。

### 10.5 能力图谱

`backend/graph.py:19-81` 从已经评分完成的 topic drill/JD/legacy recording session 提取题目，按题文本去重并保留近期记录。`_get_or_compute_embeddings()` 缓存题目向量（`graph.py:84-130`），`build_graph()` 对两两 cosine >= 0.65 的节点建边（`graph.py:133-178`）。

前端 `frontend/src/pages/Graph.tsx:21-170` 用 `react-force-graph-2d` 渲染，可切 topic、hover、缩放并在 tab 重新可见时恢复 canvas。

实现风险：缓存主键只有 question MD5，未包含 user/model/version；换 embedding 模型后可能混用旧向量，维度变化会 `np.stack` 失败。同 topic N 题两两比较是 O(N²)，没有节点上限。

### 10.6 Profile、History 与 Review

- `backend/routers/profile.py:74-183` 返回画像与导出；`184-303` 到期复习、topic history、AI retrospective；
- `profile.py:304-407` review/history/session/progress/delete/topic list；
- 前端 `Profile.tsx:52+` 组合强弱项、雷达、趋势、热力、频次、treemap；
- `History.tsx:23-267` 负责模式/topic/status 筛选、分页和继续/复盘；
- `TopicDetail.tsx:14-177` 显示单 topic 历史和 retrospective；
- `Review.tsx:375-1094` 按 resume/topic_drill/jd_prep/legacy recording 分派视图，展示单题时间线、维度分、RAG 质量、参考答案、收藏和手工同步。

Retrospective 当前把该 topic 全部 sessions/reviews 拼进 prompt（`backend/routers/profile.py:194-268`），没有滚动摘要或 token budget，历史增长后会超上下文或显著增费。

### 10.7 Resume 上传

`backend/routers/resume.py:16-67` 提供 status/upload，PDF 供 LlamaIndex/PyPDF 建 resume index。当前流程先删除旧 PDF，再读取/验证新文件大小并写目标（`resume.py:30-57`）；超限、断线或磁盘错误会使旧简历丢失。它只检查 `.pdf` 扩展名，没有 magic/MIME/可解析性验证，也不是临时文件 + `os.replace` 的事务式替换。

---

## 11. SSE、缓存与故障语义

### 11.1 服务端 SSE

`backend/utils/sse_helpers.py` 提供三层能力：

- `sse_event()` 统一 `data: JSON\n\n`；
- blocking 调用放入 thread，定期发 progress；
- LLM async stream 在上游静默时约 30 秒发 ping，reasoning 约 3 秒节流为 keepalive；
- streaming response 设置 `text/event-stream`、no-cache 和禁代理缓冲。

`backend/utils/stream_parser.py` 的增量 JSON 解析器维护 brace depth、in_string、escape 状态，可处理 Markdown fence 和跨 chunk 的对象。这是专项题卡/训练卡能“模型出一题，前端显示一题”的基础。

Nginx 对 `/api/` 配套设置 `proxy_buffering off`、`proxy_cache off`、HTTP/1.1 和 300 秒 read/send timeout（`frontend/nginx.conf:20-37`）。前后两端必须一起成立；只在 Python 里 yield 但代理缓存，浏览器仍看不到流。

取消边界仍不完整：某些 helper 在客户端断开后不会取消已经开始的 `__anext__`/tool task；`to_thread` 更无法强停。需要把“停止向客户端发送”和“停止昂贵上游调用”分别设计。

### 11.2 前端 SSE

`frontend/src/api/client.ts:54-95` 检查流式 401，并按 SSE 行解析 JSON；生成器退出时 cancel reader。`frontend/src/api/sse.ts:29-142` 为 Promise 和 async generator 提供 6 分钟硬超时，`fetchSSE` 同时兼容普通 JSON cache hit 与 SSE。

业务 event 大致分为：

| event | 用途 |
|---|---|
| `progress` / `eval_progress` | 文本进度 |
| `pipeline_stage` | 五阶段 start/ok/error、耗时和 detail |
| `question` / `question_update` | 增量题目 |
| `token` / `reasoning` / `ping` | 模型流和保活 |
| `rag_metrics` / `rag_eval_metrics` | 检索/答案指标 |
| `action` | Assistant 导航或启动动作 |
| `complete` / `done` / `error` | 结果、协议终止或错误 |

生成器版 timeout 当前把任何 `AbortError` 都映射为“超时”，没有像 Promise 版区分外部 signal 主动取消（`frontend/src/api/sse.ts:59-80`），可能给用户错误提示。

### 11.3 Redis 与进程内缓存

`backend/redis_cache.py:113-287` 将 Redis 和内存 `_LRU` 包成统一接口，记录 hit/miss/error 和健康信息；URL 日志会脱敏（`redis_cache.py:33-63`）。Redis 故障时应用级操作可退 LRU，但 Compose 在启动阶段要求 Redis healthy，形成“运行时可选、编排启动时必需”的语义矛盾。

进程内状态还包括：

- `live_store.py` 的 drill/job/algorithm dict + TTL；同时关键 live state 会写 SQLite；
- `indexer.py` 的 index cache 和 build lock；
- `channel_manager.py` 的 health/cooldown；
- `embedding_tasks.py` 的队列和状态；
- `routers/rag_eval.py` 的 job/inflight 状态；
- auth rate limiter；
- graph object cache。

因此当前 Dockerfile 不加 `--workers` 是与架构一致的。若要横向扩容，必须先把这些状态迁到 Redis/DB/持久队列，并解决文件/Qdrant 单写者锁。

---

## 12. RAG 指标与评测体系

### 12.1 在线免费指标

`backend/rag_metrics.py:106-182` 使用已有 embedding 计算 question-generation 阶段：

- relevance：query 与各 chunk cosine；
- coverage：有多少 query/弱点获得超过阈值的支持；
- diversity：chunk 两两相似度的反向指标；
- 记录 raw/fused/final chunks、cache/reranker 等 detail。

答案评估阶段从 per-question LLM score 提取 faithfulness 和 answer relevance，再约按 0.4/0.6 合成 correctness（`rag_metrics.py:185-217`）。这些记录写 `rag_metrics`，RAGDashboard 展示趋势。

它们成本低、适合线上监控，但不是有人工 gold 的严格 RAGAS；不能与离线指标的数值直接比较。

### 12.2 离线 RAG 基准

新模块不是把一个分数面板换皮，而是提供两类目的不同的实验协议。

**固定回归集 `frozen_retrieval`。** `backend/eval/rag_benchmark.py` 读取版本化 `rag_queries.json`，每个 case 带固定 ID、问题、`must_include_any`、`expected_keywords`、difficulty 和 query type。Docker 中先找宿主 `/app/data/eval/rag_queries.json`，不存在才读镜像内 `/app/backend/eval/data/rag_queries.json`；数据集内容 hash 和精确 case IDs 都进入 manifest。当前固定集覆盖 agent 11 题、python 9 题、java 9 题；API 对未覆盖 topic 返回 422，并把请求题数缩到实际可选数量。选择策略是文件顺序前缀，当前 `seed` 只被记录、并不改变这一路径的样本顺序。

固定集有两种 retrieval mode：

1. `atomic_dense`：每题独立调用 `async_retrieve_topic_context_with_scores`，禁止请求路径自动建索引，直接测 dense top-K；最多两个 case 并发。
2. `production_replay`：每最多 5 个 case 组成一个 bundle，把五个问题作为 weak points 一起走 `retrieve_for_drill`，复现多查询 dense 召回、RRF、语义去重和 reranker，再让 bundle 内每个 case 对同一最终候选列表评分。

第二种更接近专项训练的真实召回形状，但必须正确解释统计单位：五条质量行共享一次检索，不是五次独立样本；P50/P95 的观测单位是 bundle；bundle 内错误和候选也相关。若运行配置 `final_top_n < k`，最终可评分候选数会少于请求 K，Precision 分母使用实际返回数。

**合成端到端 `synthetic_e2e`。** `backend/rag_eval.py` 从实际 topic chunk 用局部 seeded RNG 取样，LLM 合成 question/reference/source，然后逐题检索、匹配源 chunk、做 leave-one-out、生成回答并计算检索侧和生成侧 judge 指标。`standard` 每题约 6 次 LLM 调用，`full` 还对每个 chunk 做 precision judge。即使选择 `production_replay`，synthetic 仍是“一道 gold 问题作为 weak point + topic fallback”逐题调用生产式链路，不是固定集的五题 bundle；两类 run 不能因 mode 名相同就直接混比。

检索 outcome 明确区分 `ok / empty / degraded / timeout / index_not_ready / error`。`empty` 表示链路健康但无结果，是有效的零质量样本；`degraded` 表示部分子查询、dedup embedding 或 reranker 失败，仍可测量但不能作为严格基线；基础设施失败和 judge 缺测都保留在总分分母中贡献 0，避免删除失败样本形成 survivor bias。逐题 outcome、错误码、阶段耗时、候选和 bundle trace 写入 `detail_json`。

Route `backend/routers/rag_eval.py` 负责启动、轮询、历史和详情。合成式评测仅 owner 可启动；单次估算 LLM 调用不得超过 300；每个 backend 进程同时执行 1 个、运行加排队最多 4 个；完全相同的在途请求复用原 `job_id`。完成和失败终态写 `rag_eval_runs`，容器重启后 status 可从 SQLite 恢复终态；但 task、semaphore、队列、进度和锁仍在内存，重启会中断在途任务，多 worker/多副本也没有分布式唯一执行保证。

### 12.3 RAG Dashboard 到底检测什么

`frontend/src/pages/RAGDashboard.tsx` 现在刻意分成上下两层：

| 区域 | 数据来源 | 回答的问题 | 不能证明什么 |
|---|---|---|---|
| 离线评测 | `/api/rag-eval/start`、status、runs、run detail | 固定回归是否退化；生产式召回在哪题/哪个 bundle 失败；synthetic 的回答链路是否有依据且切题 | 小固定集不是总体真实流量；LLM judge 不是人工真值 |
| 在线健康监控 | 业务会话写入的 `rag_metrics` | 真实 Session 的 query-chunk 相关性、覆盖、多样性、召回阶段数量/延迟，以及用户答案相对题目和依据的代理指标 | 它没有 qrel/reference answer，不能判定“模型回答绝对正确”，也不是自动 RAGAS 回归门禁 |

因此旧 Dashboard 原本确实在“监控 RAG”，但主要监控生产会话的在线代理信号，不是带 ground truth 的离线检测器。新模块补上了可重复回归和逐题诊断，而不是替代在线监控。历史页只把后端 `comparison_signature` 相同且 completed、有效测量率至少 95%、运行状态稳定、execution profile 为 `healthy` 的 run 放进严格比较组；legacy run 明确显示为不可比较。当前 job ID 进入 localStorage，刷新后恢复单飞轮询；历史大列表不带 detail，展开某一行时才调用 `GET /api/rag-eval/runs/{run_id}`。

### 12.4 旧离线策略矩阵

`backend/eval/run.py` 组织 `personas/*.json × strategies/{random,topic_only,personalized} × judges`：

- deterministic judge：弱点覆盖、难度 KL、题目 diversity；
- LLM-as-Judge：对质量维度评分/投票；
- `backend/eval/rag_recall.py` 提供检索 recall 辅助评测。

`personalized.py:117-137` 运行真实 DrillPipeline，会写生产 SQLite session/RAG/live；finally 只删除 synthetic 用户文件目录（`personalized.py:94-104`），没有清数据库行，评测可能污染业务数据。

---

## 13. 前端工程与页面实现

### 13.1 启动、路由和应用壳

入口是 `frontend/src/main.tsx`，根编排是 `frontend/src/App.tsx`：

- Interview、Review、History、Profile、Knowledge 等业务页 lazy load（`App.tsx:15-31`）；
- `/` 未登录显示 Landing，登录后显示 Home；`/login` 已登录会重定向（`App.tsx:44-68`）；
- 受保护页面统一包在 `ProtectedRoute -> AppShell`，壳内固定 Sidebar、主滚动区和 FloatingAssistant（`App.tsx:70-130`）；
- 401 事件显示全局 session expired modal（`App.tsx:133-165`）；
- 根部有 AuthProvider、BrowserRouter、ErrorBoundary、PointerFX 和 Sonner（`App.tsx:167-182`）。

路由矩阵：

| 路由 | 页面 | 作用 |
|---|---|---|
| `/` | Landing / Home | 访客介绍或训练入口与统计 |
| `/login` | Login | 登录/按后端开关注册 |
| `/interview/:sessionId` | Interview | 对话式面试或专项逐题作答 |
| `/review/:sessionId` | Review | 多模式复盘与补同步 |
| `/history` | History | 会话筛选、继续、删除 |
| `/profile` | Profile | 全局画像和可视化 |
| `/profile/topic/:topic` | TopicDetail | 单领域趋势和 AI 复盘 |
| `/knowledge` | Knowledge | topic、文件、高频题、索引管理 |
| `/knowledge-training` | KnowledgeTraining | 新卡、到期复习、已保存卡 |
| `/graph` | Graph | 题目能力关联图 |
| `/job-prep` | JobPrep | JD 分析和定向训练 |
| `/favorites` | Favorites | 面试题收藏库 |
| `/algorithm` | AlgorithmSolver | AI 算法解题/追问 |
| `/algorithm/collection` | AlgorithmCollection | 算法题卡库 |
| `/settings` | Settings | 账号、AI channel、tuning、owner admin |
| `/qa-arena` | QAArena | 多模态自由问答、总结、知识收录 |
| `/rag-dashboard` | RAGDashboard | 在线指标与离线评测 |

### 13.2 鉴权状态

`frontend/src/contexts/AuthContext.tsx:15-84`：

- 初始 token 从 localStorage 读取；
- 5 秒内请求 `/auth/me` 校验；
- 401 清本地会话，网络/5xx 暂时保留，避免服务短故障把用户登出；
- login/logout/updateProfile 同步 localStorage；
- `authFetch` 统一注入 Bearer 并广播 401（`frontend/src/api/client.ts:26-45`）。

这是实用的 SPA 策略，但 token 存 localStorage，任何 XSS 都可读取 7 天 bearer；Nginx 当前没有 CSP/HSTS/X-Content-Type-Options，公网 HTTP 更会直接暴露登录和 token。

### 13.3 关键页面状态流

#### Home

`frontend/src/pages/Home.tsx:56-236` 并行加载 topics、resume status、profile、due review；根据 mode 组 payload。resume 走普通 start SSE；topic drill 消费逐题和 pipeline events，全部生成后再让用户确认进入 Interview；JD/Knowledge Training 跳专页。

当前进度文案和百分比固定以 10 题计算（`frontend/src/pages/Home.tsx:493,505,591-596`）。后端虽然声明 `max_drill_questions=15`（`backend/config.py:54`），但专项主 Pipeline、prompt、legacy pipeline 都在生成、补题和最终截断处固定为 10（`backend/graphs/drill_pipeline.py:404,551,626`、`backend/prompts/strategies.py:23-28,158`、`backend/prompts/interviewer.py:124,177,284`、`backend/graphs/topic_drill.py:242`）。因此这里不是“前端 10 与后端 15 的运行时不一致”，而是 `MAX_DRILL_QUESTIONS` 当前未被消费的死配置/配置漂移；若未来接入该配置，前后端仍需改为同一运行时题数。

#### Interview 与草稿

`Interview.tsx:68-237` 支持刷新恢复，专项答案保存有两层：

- localStorage：即时/防网络故障；
- `/api/interview/session/{id}/progress`：SQLite/跨设备。

`frontend/src/hooks/useDraftPersist.ts:39-147` 以 key 隔离草稿，400 ms debounce，并用 generation 防旧 key 的异步 load 覆盖新 key。问题是 cleanup 会 cancel debounce 而不 flush；输入后 400 ms 内切页仍可能丢最后字符。Interview 合并时服务端只要存在 progress 就优先，离线更新较新的本地答案可能被旧云端覆盖，后端也没有 revision 防旧请求后到覆盖新请求。

#### Knowledge

`frontend/src/pages/Knowledge.tsx:48-403` 管 topic、core files、high-frequency、stats/chunk counts 和异步 rebuild；任务每 3 秒轮询。编辑草稿用 topic/file 组成 key。

关键竞态：topic 改变时 core/high_freq/stats 请求没有 AbortController 或 request generation（`Knowledge.tsx:103-143`）。A 的慢响应可覆盖已选择 B 的 state，随后保存 handler 使用当前 selected=B（`Knowledge.tsx:170-179`），从而把 A 内容写入 B。这是高优先级数据完整性问题。

#### KnowledgeTraining

`KnowledgeTraining.tsx:101-320` 实现 new/review/saved 三种 study mode，生成新卡时消费 SSE，卡片答案隐藏后展开，熟悉度按钮调用 review。按钮没有 per-card pending/幂等保护（`KnowledgeTraining.tsx:296-320,601-607`），快速多击会重复推进 SM-2。

#### Settings

`Settings.tsx:126-347` 包括账号、三个 ChannelManager、tuning、owner-only audit/users。每个 `ChannelManager.tsx:60-187` 加载并缓存一份完整 channels 配置；保存自己 section 时又提交这份 allData。三个组件同时挂载，各自快照会过期：先保存 LLM，再保存 embedding，后者可能把 LLM 回滚到旧值。正确做法应是后端支持 section PATCH 或父组件维护单一版本化状态。

#### Q&A、RAG 与图表

- `QAArena.tsx:73-1011` 见 10.1；
- `RAGDashboard.tsx:89-710` 展示筛选、趋势、雷达、分布、session 明细，启动评测后每 1.5 秒轮询；
- charts 目录实现 score trend、topic radar、dimension trend、26 周 heatmap、12 周频次、knowledge treemap；
- `LearningHeatmap.tsx:26-65` 用本地零点 Date 后 `toISOString().slice(0,10)`，Asia/Shanghai 会转成前一天；
- `DimensionTrendChart.tsx:22-35` 在条件 return 后调用 `useState`，违反 Hooks 调用顺序，数据形状变化时可能崩溃；当前 lint 没有扫描 TSX 因而未发现。

### 13.4 API 与类型层

| 文件 | 作用 |
|---|---|
| `api/client.ts` | authFetch、401 广播、基础 SSE parser |
| `api/sse.ts` | 通用 6 分钟 timeout、callback/generator SSE |
| `api/interview.ts` | topics/resume/interview/profile/knowledge/favorite/algorithm/RAG metrics 大聚合 |
| `api/settings.ts` | AI config/channel/tuning/account/admin |
| `api/assistant.ts` | 浮动助手流和历史 |
| `api/qa_arena.ts` | QA session/message/image/stream/summary/ingest |
| `api/knowledgeTraining.ts` | card generation/saved/due/review |
| `api/ragEval.ts` | eval start/status/runs 类型 |
| `types/api.ts`、`types/channels.ts` | 公共业务类型 |

前后端路由路径本次已做系统比对，未发现确定的 URL 拼写缺失。问题在错误处理不统一：assistant/qa 的部分 CRUD 直接 `fetch` 而非 `authFetch`，401 不触发全局过期，部分非 2xx 被吞成空数组。

TypeScript 配置 `strict=false, allowJs=true`（`frontend/tsconfig.json:2-25`），API 又有大量 `any`；当前 `tsc` 通过不等于契约严格。生产 build 最大公共 chunk 约 charts 486 KB、vendor 359 KB、markdown 157 KB、graph 121 KB（未 gzip），首路由已 lazy，但图表/Markdown 公共包仍可进一步按页面拆分。

---

## 14. 完整 API 目录

所有接口均由 `backend/main.py:107-121` 注册。除 Q&A Arena 和 Knowledge Training 自带更深 prefix 外，router 通常以 `/api` 为前缀。下表覆盖本次基线全部 100 个 route。

| 模块 | 方法与路径 | 作用/实现入口 |
|---|---|---|
| Auth | `GET /api/auth/config`；`POST /api/auth/register`；`POST /api/auth/login`；`GET /api/auth/me`；`PUT /api/auth/profile`；`PUT /api/auth/password`；`GET /api/` | 注册开关、登录、当前用户、资料/密码；`backend/routers/auth.py:42-120` |
| AI Settings | `GET/PUT /api/settings/ai`；`POST /api/settings/ai/test/llm`；`POST /api/settings/ai/test/embedding` | legacy/effective AI 配置与连通测试；`settings_router.py:17-84` |
| Channels | `GET/PUT /api/settings/ai/channels`；`POST .../channels/test`；`GET .../channels/health` | owner-only 多渠道、测试和熔断健康；`settings_router.py:86-220` |
| Tuning/Admin | `GET/PUT /api/settings/tuning`；`GET /api/admin/audit`；`GET /api/admin/users` | runtime tuning、审计与用户列表；`settings_router.py:222-263` |
| Resume | `GET /api/resume/status`；`POST /api/resume/upload` | 用户简历状态与替换；`backend/routers/resume.py:16-67` |
| Interview | `POST /api/interview/start`；`POST /api/interview/start-stream`；`POST /api/interview/chat`；`POST /api/interview/end/{id}`；`POST /api/interview/sync/{id}`；`POST /api/interview/reference-answer` | 三类训练核心生命周期；`backend/routers/interview.py:127-789` |
| RAG Metrics | `GET /api/interview/rag-metrics`；`GET /api/interview/rag-metrics/{id}` | 在线指标查询；`interview.py:105-125` |
| Topic | `GET/POST /api/topics`；`DELETE /api/topics/{key}` | topic 配置 CRUD；`backend/routers/profile.py:22-72` |
| Profile | `GET /api/profile`；`GET /api/profile/export`；`GET /api/profile/due-reviews`；`GET /api/profile/topic/{topic}/history`；`POST .../retrospective` | 画像、到期复习、领域历史和 AI 复盘；`profile.py:74-303` |
| Session/History | `GET /api/interview/review/{id}`；`GET /api/interview/history`；`GET /api/interview/session/{id}`；`POST .../progress`；`DELETE .../{id}`；`GET /api/interview/topics` | Review、分页、恢复、草稿进度、删除；`profile.py:304-407` |
| Knowledge Core | `GET /api/knowledge/{topic}/core`；`PUT/DELETE .../core/{filename}`；`POST .../core`；`POST .../upload`；`POST .../generate` | 私有知识文件管理与 AI 生成；`backend/routers/knowledge.py:91-263` |
| Knowledge Ops | `GET/PUT /api/knowledge/{topic}/high_freq`；`POST .../{topic}/rebuild`；`POST /api/knowledge/rebuild-all`；`GET /api/knowledge/rebuild-status`；`GET .../rebuild-status/{task_id}`；`GET .../{topic}/stats`；`GET .../chunk-counts` | 高频题、索引任务和统计；`knowledge.py:264-465` |
| Knowledge Training | `GET /api/knowledge-training/availability`；`GET .../cards/saved`；`GET .../due`；`POST .../review`；`POST .../cards` | section 可用性、卡片生成/复习；`backend/routers/knowledge_training.py:47-169` |
| Job Prep | `POST /api/job-prep/preview`；`POST /api/job-prep/start` | JD 蓝图与题目；`backend/routers/job_prep.py:21-103` |
| Algorithm | `POST /api/algorithm/solve`；`POST .../chat`；`POST .../save`；`GET .../cards`；`GET/PUT/DELETE .../cards/{id}`；`GET .../tags`；`POST .../export` | 解题、对话、题卡 CRUD/导出；`backend/routers/algorithm.py:23-168` |
| Favorites | `POST/GET /api/favorites`；`PUT/DELETE /api/favorites/{id}`；`GET /api/favorites/tags`；`POST /api/favorites/export` | 面试题收藏 CRUD/导出；`backend/routers/favorites.py:15-75` |
| Assistant | `POST /api/assistant/chat`；`GET/DELETE /api/assistant/history`；`GET /api/assistant/welcome` | 工具助手与历史；`backend/routers/assistant.py:10-45` |
| Graph | `GET /api/graph/{topic}` | 能力图节点/边；`backend/routers/graph_router.py:10-12` |
| QA Sessions | `POST/GET /api/qa-arena/sessions`；`DELETE/PATCH .../sessions/{id}`；`GET/DELETE .../{id}/messages` | 自由问答会话和消息 CRUD；`backend/routers/qa_arena.py:12-58` |
| QA Streaming | `POST .../{id}/chat`；`GET .../{id}/images/{name}`；`POST .../{id}/regenerate`；`POST .../{id}/summary`；`POST .../{id}/ingest-knowledge`；`GET .../{id}/summary/download` | 多模态流、重答、总结和入库；`qa_arena.py:60-159` |
| RAG Eval | `POST /api/rag-eval/start`；`GET /api/rag-eval/status/{job_id}`；`GET /api/rag-eval/runs`；`GET /api/rag-eval/runs/{run_id}` | 固定/合成离线评测、进度恢复、轻量历史与按需详情；`backend/routers/rag_eval.py` |
| Debug | `GET /api/debug/memory` | owner-only RSS/cache/GC/tracemalloc；`backend/routers/debug.py:22-98` |

### 14.1 后端文件级导航

| 文件/目录 | 读源码时应抓住的主线 |
|---|---|
| `backend/main.py` | 生命周期和 router 装配 |
| `models.py` | API Pydantic 模型与 LangGraph TypedDict state |
| `auth.py`、`rate_limit.py` | 身份、用户初始化、owner、内存限流 |
| `config.py`、`ai_config.py` | 环境配置与磁盘 runtime overlay |
| `channel_manager.py`、`llm_provider.py` | provider 池、key 轮询、熔断、failover、client |
| `context_assembler.py` | token 预算与消息压缩 |
| `indexer.py`、`reranker.py` | LlamaIndex、manifest、Qdrant/local、重排 |
| `graphs/drill_pipeline.py` | 专项出题五阶段主链 |
| `graphs/resume_interview.py`、`checkpointer.py` | LangGraph 状态机和断点 |
| `graphs/job_prep.py` | JD preview/question/eval |
| `graphs/decoupled_eval.py` | 小模型逐题并发 + 大模型总结 |
| `graphs/topic_drill.py` | legacy 同步专项生成/批评估 fallback |
| `graphs/rag_retrieval.py` | 多 query、RRF、去重、rerank |
| `graphs/validators/` | 弱点覆盖、语义重复、难度分布规则 |
| `graphs/seed_pool.py`、`difficulty_anchors.py` | 题池复用和难度锚点 |
| `graphs/review.py`、`formatters.py` | 多模式 review 文本格式化 |
| `memory.py`、`vector_memory.py` | JSON 画像与长期语义记忆 |
| `spaced_repetition.py` | SM-2、连续高分毕业、due review |
| `knowledge_training.py`、`knowledge_evolution.py` | 知识卡与训练结果反哺 |
| `qa_arena.py`、`assistant.py` | 两类开放式 Agent/工具交互 |
| `rag_metrics.py`、`rag_eval.py` | 在线/离线质量指标 |
| `graph.py` | 能力关联图 |
| `redis_cache.py`、`embedding_tasks.py`、`live_store.py` | 缓存、后台任务、进程内/SQLite live 状态 |
| `storage/` | 每张 SQLite 表的仓储边界 |
| `vector_store/` | 长期记忆的 NumPy/Qdrant Strategy |
| `prompts/` | interviewer/reviewer/job/knowledge/algorithm/RAG eval 模板；`_common.py` 放公共约束 |
| `eval/` | persona×strategy×judge 离线实验框架 |
| `migrate.py` | 旧单用户数据迁移，当前存在 stable-id 不兼容 |

---

## 15. 代码审查发现

### 15.1 P0：部署阻断项

#### P0-1 当前 owner 默认口令仍有效，且管理面可从公网直达

**证据链：**

- 模板和代码默认口令是公开值（`.env.example:23-27`、`backend/config.py:40-47`）；
- startup 只 warning，不拒绝启动（`backend/main.py:33-48`）；
- 本次对当前 SQLite bcrypt hash 做了只读核验，默认 owner 仍可用模板口令验证；
- Compose 将 backend `9001:8000` 发布到所有接口（`docker-compose.yml:30-31`）；
- 部署文档还要求防火墙开放 9001（`DEPLOYMENT.md:68-79`）；
- owner 的 channels API 返回包含 key 的完整 channel 对象（`backend/routers/settings_router.py:86-95`）。

**影响：** 未授权者可登录 owner，读取/替换 AI provider keys、修改模型设置、读取审计/用户信息并调用全部业务接口。

**立即处置：** 在应用内改 owner 密码；因旧凭证可能已签发 7 天 token，同时轮换 JWT secret 使旧 token 全部失效；只对外暴露 TLS frontend，取消公网 9001；检查 audit logs 和 provider 使用记录并轮换 provider key。报告不记录任何当前真实密钥。

#### P0-2 Qdrant 无服务端鉴权并直接发布 6333/6334

**证据：** `docker-compose.yml:14-24` 发布两个端口，Qdrant service 没有认证环境；`QDRANT_API_KEY` 只是 backend client 环境。长期记忆 payload 含 user/session/content（`backend/vector_store/qdrant_store.py:154-165`），KB collection 含原文 chunks。

**影响：** 可枚举、读取、篡改或删除所有用户向量和知识内容；篡改还会污染后续模型上下文。

**修复：** 删除 Qdrant `ports`，仅 Docker internal network 使用；确需外部运维时绑定 loopback，给 Qdrant 服务本身配置 API key/TLS/网关 ACL。不要把“client 设置了空 API key”理解为服务已鉴权。

#### P0-3 生产指南默认明文 HTTP 传输 bearer

Nginx 只 listen 80（`frontend/nginx.conf:1-2`），指南主入口是 `http://...:9000`，HTTPS 仅作为末尾建议（`DEPLOYMENT.md:31,220-225`）。JWT 存 localStorage 并经 Authorization 发送（`frontend/src/contexts/AuthContext.tsx:17,67-79`、`api/client.ts:26-35`）。公网链路可截获账号和 token。

**修复：** 把外层 TLS 反代设为生产硬前置；9000 也只绑定 loopback/internal network；启用 HSTS、CSP、X-Content-Type-Options、Referrer-Policy 等安全头。

### 15.2 P1：高优先级正确性与升级风险

| ID | 问题与证据 | 影响 | 建议 |
|---|---|---|---|
| P1-1 | Knowledge topic 切换的 core/high_freq/stats 请求无 abort/代次，慢 A 可覆盖 B；保存用当前 selected（`frontend/src/pages/Knowledge.tsx:103-179`） | 可把 A 文档写进 B，属于数据损坏 | request generation + AbortController；响应携带/核对 topic；保存绑定加载时 topic |
| P1-2 | 三个 ChannelManager 各缓存全量 allData，保存单 section 时重发旧的其他 section（`Settings.tsx:294-347`、`ChannelManager.tsx:73-89,166-187`） | 后保存的 embedding/reranker 可回滚刚保存的 LLM | 后端 section PATCH/ETag version；或父级单一 state |
| P1-3 | Assistant history/welcome 读 `sessions` 而仓储返回 `items`；favorites 同类（`backend/assistant.py:337-363,928-951`） | 有历史/收藏仍恒返回空 | 改键并为每个 tool 写契约测试 |
| P1-4 | async assistant tool 中同步 `future.result(timeout=60)`（`assistant.py:685-700`） | 阻塞整个 worker，SSE ping 和其他请求停顿 | `await asyncio.wait_for(asyncio.to_thread(...))`，复用受控 executor |
| P1-5 | Interview 云端 progress 优先于更新的本地草稿，保存无 revision（`Interview.tsx:110-138`） | 离线新答案被旧云端覆盖；乱序请求回滚 | 带 `updated_at/revision` 合并，服务端 compare-and-set |
| P1-6 | Resume upload 先删旧文件，再验证/写新文件（`backend/routers/resume.py:30-57`） | 超限、断线、磁盘错误导致旧简历丢失 | 临时文件完整读取、size/magic/PDF parse 后 `os.replace`，成功后清旧 |
| P1-7 | Docker 默认由 NumPy 切 Qdrant，但部署文档没有执行 `scripts/migrate_memory_to_qdrant.py` | 旧长期记忆静默不可见 | 备份后迁移，核对 SQLite/Qdrant count，再切流；脚本需幂等演练 |
| P1-8 | 旧 `migrate.py` 硬编码 `default0`，现行 user id 是 email hash；`ensure_default_user()` 只更新 users.id（`backend/migrate.py:13-38,119-135`、`auth.py:60-92`） | 旧会话/目录/向量仍指旧 ID，登录后“数据消失” | 单事务更新所有 user_id 表、重命名目录、迁 Qdrant payload/collection；迁移前后全表对账 |
| P1-9 | 当前 DB 只读对账发现 `assistant_chats` 有 2 条 user_id 不在 users 的孤儿记录 | 已出现迁移残留实证 | 先备份，再基于明确映射修复；增加 orphan invariant test |
| P1-10 | 普通 rebuild 最终固定 `force_rebuild=True`（`embedding_tasks.py:526-548,601-612`） | 每次编辑全量 embedding，成本、延迟、空窗放大 | 把 force 参数贯穿任务；优先 manifest incremental；加调用测试 |
| P1-11 | Qdrant force rebuild 先 delete collection，再重建/save manifest（`backend/indexer.py:564-602`） | 部署停止/线程中断可留下 partial collection；仅看 collection 存在会误判 ready | shadow collection 构建+点数校验+alias swap；完成标记 |
| P1-12 | RAG eval、embedding task status、live/cache/locks 多为进程内 | 多 worker 轮询 404、重复副作用、任务重启丢失 | 当前保持单 worker；扩容前迁 Redis/DB/持久队列和分布式锁 |
| P1-13 | 镜像 tag 和 Python 依赖未锁：Qdrant latest、基础镜像浮动、`requirements.txt` 全 `>=`、frontend `npm install` | fresh build 不可复现，依赖组合/数据格式可能突变 | lock/hash、`npm ci`、镜像 pin version+digest；升级单独做快照和回滚 |
| P1-14 | 备份指南只热备 `data/`；SQLite WAL、checkpoint、Qdrant/Redis 同时写 | 备份跨存储不一致或缺 WAL，无法恢复 | 维护窗口停写；两库 `.backup` + Qdrant snapshot + 文件源；恢复演练 |
| P1-15 | Docker 允许选择 local embedding，但 backend 镜像不安装独立 requirements（`backend/Dockerfile:5-6`、`requirements.local-embedding.txt:1-4`） | 按模板切 local 直接 RuntimeError，2 GB 也可能 OOM | 明确 Docker 仅 API embedding，或提供 local/GPU build target 和资源规格 |
| P1-16 | 密码只校验最小 6 字符，未限制 bcrypt 5.0 的 72 UTF-8 bytes 上限（`backend/models.py:85-104`、`backend/auth.py:32-37,95-126,215-223`） | 73 个 ASCII 或约 25 个中文字符在注册、登录/改密可抛 `ValueError` 返回 500 | 三条路径统一按 UTF-8 bytes 校验上限并返回 422；补多字节测试 |
| P1-17 | interview end/sync 的 `already_scored/synced_at` 是 check-then-act（`backend/routers/interview.py:281-316,429-468,660-723`） | 两个并发请求可重复推进 SR、profile 和 knowledge evolution | 数据库原子 claim/status + session lock + 幂等键 |
| P1-18 | 同一 QA session/chat 或 Resume thread 没有服务端串行化（`backend/qa_arena.py:650-695`、`backend/routers/interview.py:224-278`） | 历史漏 turn、消息/graph checkpoint 乱序，regenerate 可删错回复 | per-session/thread lock、turn sequence、expected revision |

### 15.3 P2：中优先级鲁棒性与业务缺陷

| ID | 发现 | 证据/建议 |
|---|---|---|
| P2-1 | Nginx 32 MB 与后端上传契约冲突 | 知识文件后端单个 200 MB；QA 4×6 MB 原图经 base64 约 32 MiB 再加 JSON 必超（`frontend/nginx.conf:20-28`、`backend/routers/knowledge.py:16-22`、`qa_arena.py:225-227`）。按 endpoint 分 location/cap；大文件应流式、配总请求/磁盘 quota |
| P2-2 | Fresh clone 缺 `data/topics.json` | `.gitignore:24` 忽略它，只有旧 `topics.example.json`；初始化只复制正式文件（`backend/auth.py:40-57`）。提交与当前三目录匹配的 seed 或启动时由目录生成 |
| P2-3 | Knowledge upload 文件名去重有 TOCTOU，且直接写目标 | 并发可选同名互相覆盖，崩溃留半文件（`backend/routers/knowledge.py:43-57,195-213`）。使用独占创建/UUID temp + atomic replace |
| P2-4 | Topic 删除不删目录/high_freq/cards | `backend/routers/profile.py:58-69`。明确软删或事务式清源文件、索引、cards，并提供二次确认/恢复策略 |
| P2-5 | 强制 rebuild 在入队前 invalidate | `backend/routers/knowledge.py:292-306`；提交失败就丢旧 index。先成功入队或构建新版本后切换 |
| P2-6 | Embedding queue stop 只 cancel，不 await 底层 thread；状态跨线程无完整锁 | `embedding_tasks.py:227-235,466-479`。增加 drain deadline、stop_grace_period、线程安全状态；避免跨 event loop 复用 asyncio.Queue |
| P2-7 | 合法跳过题不会降低已有弱点 SR | skipped score=0 但 weak_point 为空，持久化只更新 truthy weak point（`decoupled_eval.py:153-165`、`interview.py:442-446`）。定义 skip 产品语义并测试 |
| P2-8 | JD LLM schema normalize 不防非数字 difficulty/null/list | `backend/graphs/job_prep.py:232-247,416-433`。用 Pydantic/显式 coercion，校验题目非空、难度 1～5、数量 |
| P2-9 | Assistant 请求模型是裸 dict 且消息不限长 | `backend/routers/assistant.py:10-24`；缺 content/非字符串可 500，用户消息全量入库。改 Pydantic + length limits |
| P2-10 | ContextBudget 小窗口下仍最低 4,000，required 不截断 | `backend/context_assembler.py:136-143,221-268`。预算最低值不能超过真实剩余；给 required 单项硬 cap |
| P2-11 | 画像 operation index 未 coercion | `backend/memory.py:493-552`；字符串 index 可 TypeError。Pydantic parse 并跳过非法 op |
| P2-12 | 图谱 embedding cache 不含模型/version且 O(N²) | `backend/graph.py:84-176`。key 加 user/model/dim/schema，模型切换清缓存；限制节点或 ANN/kNN 建边 |
| P2-14 | Personalized eval 污染生产 SQLite | `backend/eval/strategies/personalized.py:94-137`。使用临时 DB/data root 或 finally 按 eval user 清所有表/向量 |
| P2-15 | Profile retrospective 拼全部历史无预算 | `backend/routers/profile.py:194-268`。滚动摘要+结构化聚合+最近 N 次原文 |
| P2-16 | 通用分页和 export ids 无边界 | profile/algorithm/favorites/knowledge saved route；大量 ids 可能超过 SQLite bind limit。Pydantic `ge/le`，导出分批 |
| P2-17 | Knowledge Training 熟悉度按钮可重复点 | `KnowledgeTraining.tsx:296-320,601-607`。per-card pending disable + idempotency key/version |
| P2-18 | 多页面筛选存在旧响应覆盖 | Graph `:48-60`、Favorites `:51-67`、AlgorithmCollection `:53-74`。统一 query hook/request generation/abort |
| P2-19 | Q&A 加载/清空错误被吞，切会话先换 ID 不清旧消息 | `frontend/src/pages/QAArena.tsx:151-169`、`api/qa_arena.ts:85-103`。非 2xx 抛错，切换先 loading/清消息，失败恢复旧选择 |
| P2-20 | QA 图片 object URL 未统一 revoke，IME Enter 未检查 composing | `QAArena.tsx:380-391,467-471`。生命周期 registry + `URL.revokeObjectURL`；检查 `event.isComposing` |
| P2-21 | 草稿 hook cleanup 不 flush debounce | `frontend/src/hooks/useDraftPersist.ts:115-144`。pagehide/cleanup 同步 flush 最近值 |
| P2-22 | LearningHeatmap 日期在东八区偏一天 | `frontend/src/components/charts/LearningHeatmap.tsx:26-65`。用本地年月日格式化，不经 UTC ISO 截取 |
| P2-24 | 时区混用 naive local/UTC/SQLite CURRENT_TIMESTAMP | Compose 未设 TZ，`memory.py`、`spaced_repetition.py`、`live_sessions.py` 等混用。统一 UTC-aware 持久化，API 带 `Z/offset`，前端本地化 |
| P2-25 | Algorithm save 未删除持久 live row | 只 pop 进程内 store，未调用 `del_live`（`backend/routers/algorithm.py:86-106`、`backend/live_store.py:124-126`）；重启后可恢复并重复保存卡片 |
| P2-26 | Embedding queue 满时可能仍返回 task id | `submit=False` 没有贯穿到 `schedule_index_rebuild` 响应（`backend/embedding_tasks.py:526-548`）；API 看似已提交，轮询却无状态 |
| P2-27 | `MAX_DRILL_QUESTIONS=15` 未被主/legacy Pipeline 消费 | 实际槽位、prompt、补题和截断固定 10（`backend/config.py:54`、`backend/prompts/strategies.py:23-28`、`backend/graphs/drill_pipeline.py:404,551,626`）；统一单一运行时题数或删除死配置 |
| P2-28 | `get_current_user` 不检查 users 行存在 | 只验签、exp 和 8 位 hex sub（`backend/auth.py:140-160`）；删除/迁移后的旧 token 仍可调用不另查账户的受保护路由。增加账户存在/token_version 检查 |

本轮已修复原 P2-13、P2-23、P2-29：评测有进程级并发/队列/LLM 预算和 owner 边界；Dashboard 改为单飞递归轮询并可刷新恢复；所有题和缺测 observation 留在总分分母，另报有效测量率、完整链路率和 execution profile。这里的“修复”不包含分布式持久队列，跨进程边界仍列在 P1-12 与第 23 章。

### 15.4 P3：工程、运维与安全加固

| 发现 | 说明与建议 |
|---|---|
| Redis 可选语义矛盾 | 代码可退 LRU，但 Compose 等 Redis healthy 才启 backend。要么承认其部署必需并监控，要么取消 health-gated 硬依赖 |
| Readiness 过浅 | backend 只测 `/docs`，frontend 只测静态首页，Qdrant 无 healthcheck。增加 `/health/live` 与 `/health/ready`，ready 检 DB 可写、Qdrant collection/embedding config；模型外部故障按产品策略降级 |
| frontend healthcheck 的 `curl` 未验证 | `nginx:alpine` 具体镜像是否带 curl 需真实 build 确认；可改 `wget` 或镜像显式安装。当前 daemon 未运行，不能宣称已复现 |
| backend root 运行、网络/卷隔离弱 | Dockerfile 无 USER；所有服务一张网；backend 挂整棵 data，可写 Redis/Qdrant 底层目录。改非 root、named volumes、public/internal 网络、cap_drop/read_only/tmpfs |
| 资源和日志无上限 | 仅 backend 有 2 GB；无 CPU/PID、Qdrant/frontend limit，也无 Docker log rotation。加 limits/reservations、max-size/max-file、磁盘/OOM 告警 |
| `.dockerignore` 不完整 | 根 build context 为 `.`，当前未排 `data/users`、`data/qdrant`、`data/redis`、`data/ai_config.json`、`checkpoints.db`；虽 Dockerfile 不 COPY，但会发送给 builder。补齐敏感 runtime path |
| 固定评测集仍允许宿主覆盖 | bind-mount 遮蔽已通过镜像内 `/app/backend/eval/data/rag_queries.json` fallback 修复；但宿主 `/app/data/eval/rag_queries.json` 有意优先，运维误改会改变实验集。manifest 记录实际文件 hash，比较前必须核对 |
| 明文 provider key | `data/ai_config.json` 保存完整 keys，UI API 原样返回；应限制文件/备份权限，API masking/一次性替换，生产接 secret store |
| CORS 默认 `*` | Docker 是同源，设精确 HTTPS origin；当前 backend 直出还允许客户端伪造 `X-Real-IP` 绕过 IP 限流，因为 auth 信任该头（`backend/routers/auth.py:29-39`） |
| 前端安全头缺失 | Nginx 没 CSP/HSTS/XFO/nosniff；结合 localStorage token 和渠道 key 响应扩大 XSS 影响。外层/内层统一安全头并审计 Markdown 渲染 |
| 前端 lint 实际未运行 | `frontend/eslint.config.js:10` 仅 `*.js,*.jsx`；把 TS/TSX parser/config 纳入，启 hooks 规则；修复 `DimensionTrendChart` 后作为 CI gate |
| TypeScript 严格性低 | `strict=false`、API 大量 any。逐模块启 `noUncheckedIndexedAccess/strict`，用 OpenAPI 生成契约类型 |
| `clear_data.sh` 名不副实 | 只删 SQLite memory_vectors/sessions 和 profile（`clear_data.sh:11-28`），不清 Docker Qdrant、checkpoint、QA、收藏、卡片、指标、审计、Redis；应改名 legacy partial reset 或实现带多重确认的全存储 reset |
| 文档端口矛盾 | README Docker 后写访问 `localhost`，实际 9000；DEPLOYMENT 说 backend 无需暴露，却映射并要求放行 9001；Qdrant 已公开但防火墙章节不讨论。统一唯一生产拓扑 |

### 15.5 现有测试失败的准确解释

失败用例是 `tests/test_spaced_repetition.py:138-147`：fixture 只有 `repetitions=2`，没有 `consecutive_high/last_score`，随后 score=8，期望直接毕业。

当前实现 `backend/spaced_repetition.py:140-162` 明确规定：

- `repetitions` 是 score >= 6 的所有连续通过；
- 毕业要求 score >= 7 的三次连续高分；
- legacy 数据没有 `consecutive_high` 时，只能从已知 `last_score` 至多恢复 1 次，不能把历史普通通过全部算高分；
- fixture 连 `last_score` 都没有，所以本次更新后 high streak=1，不毕业。

这属于**测试与当前产品语义冲突**，不能简单断言实现错误。两个可选修复方向：

1. 若坚持“连续三次高分”语义：更新测试 fixture，显式放 `consecutive_high=2` 或能证明的历史高分；这是与实现注释一致的推荐方向。
2. 若产品要求“SM-2 三次通过且本次高分即毕业”：改实现用 repetitions，但会让两次 6 分 + 一次 7 分也毕业，弱化规则。

本次仅记录，没有未经确认修改业务语义。

---

## 16. 测试覆盖与验证建议

### 16.1 当前测试实际覆盖

| 文件 | 覆盖 | 未覆盖的边界 |
|---|---|---|
| `tests/test_spaced_repetition.py` | SM-2、weak point 匹配/毕业 | 文件并发、多进程、API review 幂等 |
| `tests/test_knowledge_training.py` | section 拆分/采样/card normalize | SSE、SQLite upsert、重复 review |
| `tests/test_rag_retrieval.py` | RRF 等 helper | Qdrant、timeout、reranker、后台 rebuild |
| `tests/test_rag_recall.py` | keyword proxy recall helper | 实际 embedding/index/provider |
| `tests/test_rag_benchmark.py` | 固定集选择、Hit/MRR/nDCG/分母、production bundle、失败持久化 | 真实 Qdrant/provider 和大样本统计 |
| `tests/test_rag_eval_aggregation.py` | synthetic 缺测、基础设施失败、有效/可比较条件 | 真实 LLM judge 波动 |
| `tests/test_rag_eval_retrievers.py` | atomic/production outcome、冻结配置、degraded 分类 | Docker 网络级 timeout 和真实 reranker |
| `tests/test_rag_eval_router.py` | owner、预算、队列、幂等复用、固定 topic 校验、终态恢复 | 多进程/滚动升级/真正持久队列 |
| `tests/test_rag_eval_store.py` | schema 兼容、legacy NULL、排序、detail 查询 | 大量历史数据和迁移回滚 |
| `tests/test_reranker_cache.py` | cache schema/模型隔离、非法/重复 index | 真实外部 reranker 协议漂移 |

当前全量为 71 passed、1 个既有 SM-2 语义测试失败。前端仍没有测试目录和 test script；RAG Dashboard 的刷新恢复、轮询竞态和详情展开目前只经过 TypeScript/build 静态验证，尚缺 component/Playwright 自动化。其他关键竞态、草稿、SSE、Channel 保存和 Nginx 上传边界也没有自动化保护。

### 16.2 推荐测试金字塔

1. **纯函数单测：** context budget 小窗口、stream JSON 随机 chunk、SM-2 legacy state、LLM schema coercion、assistant tool return contract。
2. **仓储契约：** 临时 SQLite 对每个 storage 做 user isolation、分页边界、删除残留、migration orphan invariant。
3. **FastAPI integration：** 登录/owner/tenant、resume 原子上传、session restore/sync idempotency、错误 status 和 SSE event 序列。
4. **Qdrant/Redis Testcontainers：** NumPy->Qdrant 迁移、collection rebuild 中断、embedding model version、Redis 故障降级。
5. **前端 component：** fake timers 测 debounce/flush；延迟交错响应测 topic/filter；Channel 并发保存；SM-2 按钮 double click。
6. **Playwright Docker E2E：** 登录、专项生成/刷新恢复/评估、知识 >32 MB 边界、QA 多图、SSE 经 Nginx、401 modal。
7. **恢复演练：** SQLite/Qdrant snapshot 恢复后，核对用户数、session 数、collection count、一次检索和 checkpoint resume。

### 16.3 建议 CI 门禁

```text
backend:
  ruff/pyright(or mypy) -> pytest unit -> FastAPI integration

frontend:
  eslint TS/TSX -> tsc --strict migration target -> component tests -> vite build

deployment:
  docker compose config -> pinned-image build -> compose smoke -> security scan/SBOM
```

---

## 17. Docker 上线与升级清单

### 17.1 当前实例立即整改顺序

1. 暂停公网访问，移除 `9001/6333/6334` 发布，仅保留受 TLS 保护的 frontend 入口；
2. 修改 owner 密码、轮换 JWT secret 使现存 token 失效，检查审计并轮换 provider keys；
3. 给 `.env`、`data/ai_config.json`、备份目录设置最小权限；
4. 创建与 python/java/agent 目录一致的 canonical `topics.json` seed，验证新用户冷启动；
5. 修正 P1-1～P1-4 的数据竞态和助手错误，再允许真实用户写数据；
6. 明确当前是否从 NumPy 升级 Qdrant，若是则走迁移和 count 对账；
7. pin 镜像、Python lock、`npm ci`，再构建候选版本。

### 17.2 安全升级流程

```text
维护窗口停止写入
  -> SQLite .backup interviews.db + checkpoints.db
  -> Qdrant snapshot
  -> 备份 users 源文件、ai_config、env（加密）
  -> 记录当前镜像 digest/schema/count
  -> 构建 pinned candidate
  -> 如需向量/用户 ID 迁移，先 dry-run 后执行并对账
  -> docker compose up -d
  -> readiness + 业务 smoke
  -> 重启后 checkpoint/索引/记忆 smoke
  -> 观察 OOM/重启/磁盘/provider error
  -> 验证完成后才清旧数据
```

从 NumPy 升 Qdrant 可在 Qdrant ready 后运行仓库脚本 `python -m scripts.migrate_memory_to_qdrant`，但应先读脚本参数、备份、在副本演练；不要把 `--recreate` 当默认幂等模式。旧 `backend.migrate` 在修复 stable user id 逻辑前不应直接用于生产。

### 17.3 Smoke 验收项

- frontend `/`、SPA 深链接 refresh、静态缓存头；
- 登录、owner 受保护设置、普通用户不能访问 admin/debug；
- Redis health 和故障时明确行为；
- Qdrant 不可从公网访问，backend 内网可查 collection/count；
- 新用户存在 topics 和三类 seed knowledge；
- 一次 topic 检索有 chunk，一次长期记忆写后可读；
- topic drill 全 SSE event 顺序、刷新恢复、end、review、sync 幂等；
- resume graph 在 backend 重启后从 checkpoint 恢复；
- QA 单图/边界多图、知识上传边界与明确 413 提示；
- 备份恢复副本能登录、查看 session、检索知识。

---

## 18. 设计评价与学习路径

### 18.1 值得保留的设计

1. **失败切换边界正确。** 流式输出首 chunk 前可 failover，首 chunk 后不拼接另一模型；Qdrant 模式也不静默写回 NumPy，避免双源分叉。
2. **LLM 与确定性规则分工清楚。** LLM 负责生成、提取和 judge；RRF、cosine 去重、难度 KL、SM-2、EWMA、schema normalize 负责约束。
3. **训练结果形成闭环。** 评估不是终点，结果继续更新 profile、SR、memory、high-frequency 和 knowledge evolution。
4. **流式可观测性较完整。** 后端 stage timing、RAG stats、reasoning/ping 与 Nginx 禁缓冲配套，前端能显示瓶颈而非只有 spinner。
5. **持久化边界大体明确。** SQLite WAL、独立 checkpoint DB、用户私有文件、Qdrant payload filter 都有清楚职责。
6. **前端按路由拆包且恢复意识强。** lazy routes、local+server 草稿、live session 重建、手工 sync 都在处理长 AI 任务的真实失败场景。

### 18.2 最值得学习的代码顺序

1. `backend/main.py` + `docker-compose.yml` + `frontend/nginx.conf`：先知道程序如何真正运行；
2. `frontend/src/App.tsx` + `api/client.ts`：建立页面/API 心智模型；
3. `backend/routers/interview.py` + `graphs/drill_pipeline.py`：读完整专项闭环；
4. `graphs/rag_retrieval.py` + `indexer.py`：理解 RAG 数据面；
5. `memory.py` + `spaced_repetition.py` + `knowledge_evolution.py`：理解学习闭环；
6. `resume_interview.py` + `checkpointer.py`：理解 LangGraph 人在环；
7. `channel_manager.py` + `llm_provider.py` + `sse_helpers.py`：理解工程鲁棒性；
8. `knowledge_training.py` + `rag_metrics.py` + `rag_eval.py`：理解结构化生成和质量体系；
9. 再读 QAArena、Assistant、Algorithm 这些横向功能，观察同一基础设施如何复用。

### 18.3 与现有文档的交叉索引

| 主题 | 深入阅读 |
|---|---|
| 项目定位/技术栈 | `项目技术文档/01_项目简介与技术栈.md` |
| LangGraph | `02_Agent编排与LangGraph.md` |
| RAG/RRF/reranker | `03_RAG检索增强.md` |
| 画像/记忆/SM-2 | `04_记忆与个性化.md` |
| failover/队列/熔断 | `05_工程鲁棒性.md` |
| SSE/context | `06_流式与上下文工程.md` |
| FastAPI/SQLite/auth | `07_Web后端与存储.md` |
| 在线/离线评测 | `08_评测体系.md` |
| React 工程 | `09_前端工程.md` |
| Docker 基础 | `10_部署与可选能力.md` |
| 知识训练设计 | `11_知识训练场实施计划.md` |

现有 01～11 文档适合按主题学习；本报告的新增价值是以当前 commit 重新核验全仓库，并把 **Docker 默认 Qdrant、配置覆盖、当前安全状态、真实缺陷和验证失败** 统一放进一个端到端视图。若现有文档与本报告/源码不一致，应以当前源码和 Compose 合并结果为准。

---

## 19. 总体判断（先读结论）

SparkOffer 已经具备完整的个人面试训练产品骨架，技术亮点不是某个孤立框架，而是把 RAG、状态机、长期画像、间隔重复、知识演进、流式可观测和多渠道容错串成了可运行闭环。源码中大量降级、恢复和幂等意图说明项目已经越过 demo 阶段。

但当前 Docker 配置仍是“单机开发/个人部署可用”，不是可直接暴露公网的生产基线。上线前必须先处理默认 owner、Qdrant/Backend 端口和 HTTPS 三个 P0，再修知识页与渠道设置的数据竞态、助手契约、向量迁移和可重复构建。扩展到多 worker/多副本不是改一个启动参数，而是一项涉及任务、锁、live state、限流、索引和副作用事务化的架构改造。

本报告记录的是审查基线，不代表已修复上述问题；验证中的 1 个测试失败也被原样保留，以避免在没有产品决策时擅自改变“连续高分毕业”的业务语义。

---

## 20. 数值指标与公式推导

本章集中解释代码中真正参与业务决策的数值。阅读时必须先区分量纲：单题评分是 0～10，SM-2 quality 和题目难度是 1～5 附近的离散值，掌握度是 0～100，而在线/离线 RAG 指标通常是 0～1。名称相似不代表可以横向比较。

### 20.1 指标口径总表

| 指标 | 范围 | 输入 | 核心含义 | 不能解释成 |
|---|---:|---|---|---|
| 单题 `score` | 通常 0～10 | LLM 逐题评估 | 当前答案质量 | 已校准的长期能力 |
| SM-2 `quality` | 代码可得 `<=5` | `int(score/2)` | 是否通过和下次间隔 | 原始 0～10 分的等距保真映射 |
| 单弱点 `mastery` | 设计上 0～100 | 难度加权得分 EWMA | 最近在该弱点上的难题表现 | 简单答对率 |
| Topic mastery | 设计上 0～100 | 一轮全部题的难度加权贡献 | 某 topic 的会话级能力估计 | 单个弱点 mastery 的平均值 |
| 在线 RAG relevance/coverage/diversity | 0～1 | 当前 query、chunk embedding | 无 gold 的运行健康代理 | 离线检索准确率 |
| 离线 Hit/MRR/AP/RAGAS 风格指标 | 0～1 | 合成 gold、reference、retrieval、judge | 有参考基准的实验指标 | 线上同名字段的直接对照值 |
| Validator 结果 | pass/fail + bad ids | 当前 10 题、计划、历史题 | 是否触发一次局部修题 | 题目质量的最终保证 |

### 20.2 SM-2 调度、毕业与单弱点掌握度

#### 0～10 分到 SM-2 quality

`backend/spaced_repetition.py:31-71` 使用：

```text
quality = min(5, int(score / 2))
pass = quality >= 3
```

因此正常输入下，`score >= 6` 是复习通过线。`int` 是向零截断，不是四舍五入；例如 5.9 得到 quality=2，6.0 得到 3。函数本身没有先将 score clamp 到 0～10，也没有处理字符串、NaN 或无穷值，所以“0～10”目前主要依赖调用链契约，而不是该纯函数的强保证。

间隔更新为：

```text
通过且 repetitions=0: interval'=1 天
通过且 repetitions=1: interval'=3 天
通过且 repetitions>=2: interval'=round(interval * ease_factor)
失败: interval'=1 天, repetitions'=0
```

第二次通过使用 3 天是项目主动偏离标准 SM-2 常见 6 天的产品选择，目的是让面试复习更密集。第三次以后使用 Python `round`，其 `.5` 是银行家舍入；代码没有 interval 上限，损坏或极端历史状态可能得到超大日期。

Ease Factor 公式是：

```text
EF' = max(1.3,
          EF + 0.1 - (5-quality) * (0.08 + (5-quality)*0.02))
```

结果保留两位。quality=4 时增量为 0，quality=3 时约为 -0.14，quality=1 时约为 -0.54；最低 1.3 防止复习间隔永久塌缩。`next_review = date.today() + interval`，到期项再按最低 EF 优先，即先复习历史上最难稳定掌握的弱点（`backend/spaced_repetition.py:74-95`）。

“SM-2 通过”与“弱点毕业”是两套阈值：

```text
SM-2 pass: score >= 6
graduation: 连续 3 次原始 score >= 7
```

毕业计数保存在 `consecutive_high`。旧数据没有该字段时，只根据 `last_score >= 7` 最多补认 1 次，不会把 `repetitions` 当成连续高分次数（`backend/spaced_repetition.py:140-174`）。这正是当前唯一失败测试与实现冲突的原因。

#### 单弱点 mastery

`backend/spaced_repetition.py:116-159` 先算本次贡献：

```text
contribution = clamp(difficulty, 1, 5) / 5
             * clamp(score, 0, 10) / 10
             * 100
```

若该弱点没有历史 mastery，首次直接写 `round(contribution)`；否则：

```text
mastery' = round(0.7 * mastery_old + 0.3 * contribution)
```

例如难度 4、得分 8，本次贡献为 `4/5 * 8/10 * 100 = 64`；旧 mastery=40 时，新值为 `round(28+19.2)=47`。难度 1 即使满分，贡献上限也只有 20，长期只做难度 1 会向 20 收敛。这是“高难成功权重更大”的设计，不是普通正确率。

### 20.3 Topic mastery、会话权重与统计窗口

专项训练的会话掌握度位于 `backend/routers/interview.py:823-852`：

```text
session_mastery = sum((difficulty_i / 5) * (score_i / 10))
                  / total_questions
                  * 100

coverage = valid_score_count / total_questions
session_weight = coverage * 0.4
```

注意分母是本轮总题数，而不是有效评分数。缺失/非法评分既不贡献分子，又仍占总题数；随后 coverage 还会降低合并权重，形成“双重降权”。完整评分时 `session_weight=0.4`，画像合并为：

```text
topic_mastery' = old * (1 - session_weight)
               + session_mastery * session_weight
```

即完整一轮是旧值 60%、新值 40%，不同于单弱点的 70/30（`backend/memory.py:628-654`）。该 topic 计算会把 score/difficulty 转 float，但未 clamp；如果上游 LLM 越界，理论上可把值推离 0～100。

JD 备战不写 topic mastery，但画像更新权重为：

```text
coverage = valid_score_count / total_questions
session_weight = max(0.25, coverage * 0.5)
```

也就是说即使评分覆盖为 0，传入画像更新的 session weight 仍至少为 0.25；实际是否影响某字段取决于 LLM update payload 是否含可合并内容（`backend/routers/interview.py:855-883`）。

画像统计不是 EWMA，而是滚动算术平均（`backend/memory.py:686-729`）：

| 字段 | 窗口 |
|---|---:|
| `score_history` | 最多保留 100 条 |
| `drill_avg_score` | 最近 20 次 topic drill |
| `resume_avg_score` | 最近 10 次 resume |
| `job_prep_avg_score` | 最近 10 次 JD |
| 综合 `avg_score` | 所有模式最近 30 条 |

0 分会正常进入历史，不会被 truthy 判断漏掉。阅读趋势时还要区分“会话级记录”和可能由实时单题路径写入的记录，避免把一次训练拆出的多条数据误当成多个独立 session。

### 20.4 固定十题槽位、难度映射与 Anchor 校准

专项策略的真实题数由 `backend/prompts/strategies.py:23-28` 决定：

```text
focus = 5
consolidate = 3
graduate = 2
TOTAL_SLOTS = 10
```

弱点先按“到期优先、mastery 升序”排序；最弱 3 个进入 focus pool，中段进入 consolidate，至少有 5 个弱点时最强 2 个进入 graduate（`backend/prompts/strategies.py:59-139`）。池为空时用 difficulty=2 的 exploration 槽位补齐。

基础难度是：

```text
base = clamp(round(mastery / 20), 1, 5)
graduate_base = min(5, base + 1)
jitter = random choice [-1, 0, 0, +1]
final = clamp(base + jitter, 1, 5)
```

所以不抖动概率 50%，上下浮动各 25%。Python 银行家舍入使 mastery=50 的 `round(2.5)=2`，mastery=70 的 `round(3.5)=4`，边界并不对称。只有 1 个弱点时 focus 会轮询分配给它 5 题，与 prompt 中“同一弱点不超过 3 题”的文字约束冲突；这项限制没有确定性代码强制。

`MAX_DRILL_QUESTIONS=15` 目前未接入上述槽位，也未接入 Pipeline 的 `10-len(seed)` 和 `[:10]` 截断，所以是死配置而非有效运行参数。

生成结束后可用 difficulty anchor 做二次校准：每题找 top-3 正 cosine 邻居，以 cosine 为权重求 anchor 难度均值，再 round/clamp 到 1～5；与 LLM 难度至少相差 1 才覆盖并保存 `difficulty_llm`（`backend/graphs/difficulty_anchors.py:66-123`）。无 anchor、embedding 失败或所有权重非正都保持原值。注释称 mmap，但当前实现实际是 JSON 读取后 `np.stack` 进进程缓存；仓库当前没有默认 `data/anchors/*.json`，Docker 挂载数据目录后只有运维先生成 anchor 才会生效。

### 20.5 RRF、检索去重与重排

多路召回的 Reciprocal Rank Fusion 位于 `backend/graphs/rag_retrieval.py:183-192`：

```text
RRF(chunk) = sum_i 1 / (60 + rank_i(chunk))
```

rank 从 1 开始。它只融合“名次”，不直接比较不同 query/索引返回的原始向量分，因此能减轻不同检索分数尺度不一致的问题；代价是同一 query 重复出现会重复给对应排名加权。query 最多取前 5 个 weak point；0 个时用 fallback，只有 1～2 个时再追加 fallback，3～5 个时不追加（`backend/graphs/rag_retrieval.py:80-137`）。

RRF 后按当前顺序做贪心语义去重：候选与任一已保留 chunk 的 cosine 达运行阈值就丢弃；embedding 失败的候选 fail-open 保留。之后 CrossEncoder 只重新排序，不修改 RRF 分数；最多处理 50 个候选，每篇截 2,000 字符，query 截 512 字符，缓存 TTL 1 小时（`backend/reranker.py:23-32,112-145,180-188`）。

三套 preset 来自 `backend/ai_config.py:290-315`：

| Preset | 每路 top-k | 最终 top-n | 并发 | 去重阈值 | E2E/query/rerank 超时 |
|---|---:|---:|---:|---:|---|
| fast | 3 | 6 | 2 | 0.85 | 40s / 20s / 15s |
| balanced | 5 | 10 | 2 | 0.85 | 100s / 45s / 30s |
| thorough | 8 | 15 | 3 | 0.88 | 150s / 60s / 45s |

运行配置还会 clamp：top-k 1～20、final 1～50、并发 1～16、dedup 0.5～0.99、E2E 10～600 秒、query 5～300 秒、rerank 5～120 秒。直接调用底层函数仍可能绕过配置 clamp；去重路径对模型切换后缓存混维也没有像在线 metrics 那样的维度 guard。

### 20.6 在线 RAG 指标

在线检索指标不调用 judge，也没有人工 gold。`backend/rag_metrics.py:106-183` 对每个有效 query 取它与最终 chunks 的最大 cosine：

```text
Relevance = mean_q(max_c cosine(q, c))

Coverage = count_q(max_c cosine(q, c) >= 0.5)
           / valid_query_count

Diversity = 1 - mean(pairwise cosine(final_chunks))
```

三者最后 clamp 到 0～1；单 chunk 没有可比较 pair，diversity 明确定义为 0。每个 chunk 的 detail score 是它对所有 query 的最大 cosine。无 chunk、embedding 全失败、无有效 query 或向量混维时函数返回 `None`，调用者不应落一个伪造的 0 分。常量 `RELEVANCE_THRESHOLD=0.5` 当前未参与 relevance 计算；coverage 使用的是独立的 `COVERAGE_FLOOR=0.5`。

由于 query 集可能含 fallback，online coverage 不是纯粹的“弱点覆盖率”。它回答的是“本轮实际检索 query 中，有多少获得了至少一个 cosine>=0.5 的 chunk”。

答案生成指标从逐题 LLM 结果提取（`backend/rag_metrics.py:186-218`）：

```text
Faithfulness = mean(valid faithfulness_score / 10)
AnswerRelevance = mean(valid answer_relevance_score / 10)
AnswerCorrectness = 0.4 * Faithfulness + 0.6 * AnswerRelevance
```

`skipped` 题跳过；numeric string 可转换，bool 拒绝，越界截到 0～10。两维都缺失才返回 `None`；若只有一维存在，另一维按 0 合成，会系统性压低 correctness。这只是项目定义的组合代理，不是离线 RAGAS correctness。

### 20.7 离线 Golden/RAGAS 风格评测

离线 API 的 `n` 和 `k` 输入上限分别是 50、20；固定集会进一步缩到 topic 的真实 case 数。合成式常量位于 `backend/rag_eval.py`：LLM 并发 4、检索/embedding 并发 2、gold 内容兜底匹配阈值 0.90、近同源阈值 0.97、leave-one-out 多取 3 条、非源支持阈值 0.5、embedding AP 相关阈值 0.35，支撑等级 full/partial/none 映射为 1/0.5/0。

#### 固定回归指标

对一题实际返回的 `m <= k` 个 chunk，令 `r_i` 表示第 `i` 个 chunk 是否包含任一 `must_include_any` 子串：

```text
Hit@K = 1[max(r_i)=1]，否则 0
MRR = 1 / min{i | r_i=1}，无命中为 0
DCG@K = sum(r_i / log2(i+1), i=1..m)
IDCG@K = sum(1 / log2(j+1), j=1..R)，R=sum(r_i)
nDCG@K = DCG@K / IDCG@K，无相关返回时为 0
ContextPrecision = sum(r_i) / m，m=0 时为 0
KeywordRecall = 命中的 expected_keywords 数 / expected_keywords 总数
```

这里的 `IDCG` 只按“本次返回中观察到的相关 chunk 数”重排，不使用全库完整 qrel，所以 nDCG 是稳定的排序代理而非标准全库 nDCG。关键词匹配为不带词边界的大小写无关 substring：短词可能误命中，同义改写可能漏命中。Precision 分母是实际返回 `m`，不是请求 K；`final_top_n < k` 时 `m` 会天然更小。

所有质量指标按所选 case 做宏平均，`timeout/index_not_ready/error` 行保留且质量贡献为 0：

```text
EffectiveMeasurementRate = count(ok, empty, degraded) / N
FullyHealthyRate = count(ok, empty) / N
Valid = N>0 and EffectiveMeasurementRate >= 0.95
Comparable = Valid and state_stable and execution_profile == healthy
```

`empty` 可测量但所有质量为 0；`degraded` 可测量却不满足严格比较。延迟分位数用检索 attempt 列表做线性插值；`atomic_dense` 一题一个 attempt，`production_replay` 最多五题共享一个 bundle attempt，两个 mode 的 P50/P95 不能当成相同单位横比。production bundle 的五条宏平均行也高度相关，不能据此声称样本量扩大了五倍。

#### 合成端到端指标

主要口径如下：

| 指标 | 代码口径 | 解释 |
|---|---|---|
| Hit@k | stable chunk ID 相等，或源内容与候选 cosine>=0.90 | gold 从源 chunk 合成，裸 hit 容易被同源自命中抬高 |
| MRR | `mean(1/rank)`，未命中为 0 | 奖励正确源 chunk 排得更靠前 |
| `hit_at_k_strict` | 剔除 identity/cosine>=0.97 源片段后，前 k 个非源片段任一与 reference cosine>=0.5 | 实际是 leave-one-out 泛化/冗余覆盖，不是传统严格 Hit@K |
| Context Precision | 相关 chunk 位置 `Precision@i` 之和 / 相关 chunk 数 | `standard` 以 reference-chunk cosine>=0.35 定相关；`full` 要求 LLM 返回真正 JSON bool |
| Context Recall | reference statements 的 support 权重均值 | 判断参考答案要点是否被上下文覆盖 |
| Faithfulness | generated answer claims 对 retrieved context 的 support 权重均值 | 判断回答断言是否有检索依据 |
| Answer Relevancy | 由答案反向生成问题，与原问题 cosine 均值，负值按 0 | prompt 期望 3 个问题，目前未硬截模型多返回的项 |
| Answer Correctness | generated claims 对 reference 的 support 权重均值 | 检查已有断言正确性；遗漏要点主要由 recall 反映 |

gold chunk ID 使用 `content + source_file + header_path` 的稳定 hash，避免长 Markdown 同一标题被切成多个节点时只靠 metadata 误命中。抽样使用 `random.Random(seed).sample`，不污染全局 RNG；prompt 全文 hash、阈值和协议进入 manifest。Judge 或 embedding observation 缺失时，对应质量仍以 0 留在总题数分母，同时单独报告 generation success、judge observed、metric observation rate；因此不能再通过过滤异常题抬高均值。

#### 可复现和严格比较

`backend/eval/rag_manifest.py` 记录 dataset id/version/hash/精确 case IDs、实时语料 hash、索引 `_file_hashes.json` 快照 hash、index revision、chunk 参数/数量、脱敏 provider 路由、模型、endpoint hash、vector backend、依赖版本、冻结 retrieval tuning、prompt/protocol、seed、metric semantics 和 `APP_GIT_SHA`。评测前后各取一次比较快照；变化即 `state_stable=false`，execution profile 分为 `healthy / degraded / infrastructure_failure / evaluation_degraded / question_failure / state_changed_during_run`。

严格比较必须使用后端生成的 `comparison_signature`，不能由前端挑几个字段自行拼。它仍有三个重要限制：Qdrant 现只校验 embedding 维度，索引 manifest 没持久化“实际建库模型 fingerprint”，同维度换语义模型可能复用旧向量；provider failover 的实际 channel 仍受运行时健康状态影响；未显式注入 `APP_GIT_SHA` 时 Docker 镜像内通常无 `.git`，只能记录 `unknown`。固定集样本仅 9～11 题，单题可让宏平均变化约 9%～11%，当前也没有 bootstrap 置信区间，分数差异必须结合逐题结果而不是只看小数点。

### 20.8 旧评测矩阵与确定性 Validator

旧关键词 RAG recall 不是严格 IR recall（`backend/eval/rag_recall.py:84-156`）：

```text
HitRate = 是否至少一个 chunk 包含 must_include_any 子串
MRR = 第一个子串命中位置的倒数
KeywordCoverage = top-k 文本并集覆盖 expected_keywords 的比例
Precision = 包含关键词的 chunk 数 / 实际返回 chunk 数
```

它是大小写无关 substring proxy，会短词误命中、同义词漏检。`_score_query` 的 k 参数本身没有切片，依赖传入 chunks 已是 top-k。

个性化策略矩阵的三个 deterministic judge 也有独立口径：

- CoverageJudge：每个 weak point 先做关键词匹配，再用 cosine>=0.55；分数为覆盖 WP 数/总 WP 数，无 WP 时返回 1.0（`backend/eval/judges/coverage.py:24-97`）。
- DifficultyKL：对 5 桶加 Laplace `alpha=0.05`，算 `KL(P||Q)`，最终 `max(0, 1-KL/2)`（`backend/eval/judges/difficulty_kl.py:19-89`）。恰好 mastery=30 时它归 mid，而生成策略的 legacy 分档可能归 beginner，存在边界错位。
- DiversityJudge：`1-题目两两 cosine 均值`，少于 2 题或 embedding 失败为 0（`backend/eval/judges/diversity.py:25-55`）。

LLMJudge 最多选 3 个不同模型 channel，将 1～10 分的中位数除以 10；失败票丢弃、全失败为 0（`backend/eval/judges/llm_judge.py:52-70,176-228`）。最终 runner 只按 `strategy × judge` 对 persona 做算术平均，并没有把不同 judge 再合成一个科学意义明确的总分（`backend/eval/run.py:178-185`）。

专项出题在线 Validator 的规则是：

| Validator | 判定 | 修题上限 | 重要边界 |
|---|---|---:|---|
| WeakPointCoverage | substring 或 cosine>=0.55，默认目标 60% | 4 | `round(n_wp*0.6)` 可能先判断无需 embedding，最终精确比例却失败 |
| SemanticDuplicate | 与最近 20 题或本批已保留题 cosine>=0.90 | 4 | embedding 失败直接放行 |
| DifficultyDistribution | 双向 KL 之和式，epsilon `1e-6`，失败阈值 0.4 | 3 | 名称接近 symmetric KL；阈值越高实际越宽松 |

代码位于 `backend/graphs/validators/weak_point_coverage.py:21-82`、`backend/graphs/validators/semantic_duplicate.py:17-97`、`backend/graphs/validators/difficulty_distribution.py:18-91`。Pipeline 只做一轮局部 repair；repair 调用失败或修后仍有质量问题，批次仍会进入 finalize（`backend/graphs/drill_pipeline.py:630-747`）。因此它是成本有界的 fail-open 质量护栏，不是严格验收门。

### 20.9 长期记忆时间衰减

`backend/vector_memory.py:251-262` 使用：

```text
raw_decay = 0.5 ** (max(age_days, 0) / 14)
multiplier = 0.3 * raw_decay + 0.7
final_score = cosine * multiplier
```

所以“14 天半衰期”只作用在 30% 的衰减分量上：新记录约乘 1.0，14 天乘 0.85，无限久趋近 0.7，最大只降权 30%，绝不是 14 天后总相关度减半。无效或未来时间返回 1.0。若 cosine 为负，乘更小的正系数反而让旧负相关项更接近 0，可能排在同等新负相关项之前；通常语义候选为正相似，但这是公式的真实边界。

弱点语义合并阈值为 0.75，每用户最多 500 条向量记忆；检索先做 raw cosine，再乘时间权重重排并截 top-k（`backend/vector_memory.py:31-40,325-360`、`backend/vector_store/base.py:22-24`）。画像只取 score>0.3 的 top-3 相关记忆（`backend/memory.py:281-293`）。

### 20.10 上下文预算公式

`backend/context_assembler.py:96-140` 按所有已启用 LLM channel 做保守预算：

```text
window = min(显式声明的 context_window)
reserve = max(显式声明的 max_tokens)
input_budget = max(4000, window - reserve - 2000)
```

optional section 按 priority 从小到大贪心装入；能完整放下就保留，剩余 token 严格大于 `max(min_tokens,50)` 时按行边界截断，否则整段 drop。required section 先完整计入，永不截断（`backend/context_assembler.py:221-268`）。消息每条额外估 4 token，优先丢最老历史，但最近 `keep_last=2` 始终完整保留（`backend/context_assembler.py:270-307`）。

Token 计数优先 `cl100k_base`，失败时使用：

```text
heuristic_tokens = int(CJK_chars * 1.05 + other_chars / 3.5) + 1
```

这对非 OpenAI tokenizer 只是近似。最低 4,000、required 和固定保留尾消息都可能让实际 used 超过真实窗口；如果某 enabled channel 没声明 context window，而另一个声明了，它不会以 default window 参与 `min`。因此本模块提供“尽量保守的装配策略”，不是数学上的不溢出保证。

### 20.11 五个可手算的例子

这些例子使用当前源码的真实常数，适合在阅读 debugger 或日志时复核：

1. **SM-2 三次高分**：初始 `repetitions=0, interval=1, EF=2.5`，score=8 得 `quality=4`、interval=1、repetitions=1；再次 score=8 得 interval=3、repetitions=2；第三次 score=8 得 `round(3*2.5)=8` 天。EF 在 quality=4 时增量为 0。若中间出现 score=6，SM-2 仍算通过，但 `consecutive_high` 会归零，不能毕业。
2. **单弱点 EWMA**：难度 4、score 8 的 contribution=64；旧 mastery=40 时新值 `0.7*40+0.3*64=47.2`，存储为 47。难度 1 满分 contribution 仍只有 20。
3. **Topic coverage 双重降权**：10 题只有 8 个合法 score，且 8 题平均难度加权结果为 60，则 `coverage=.8`、`session_weight=.32`，session mastery 仍按总题数计算为 `8*0.6/10*100=48`，再以 32% 权重并入旧画像。
4. **RRF 与在线指标**：chunk A 在两个 query 中排第 1、第 3，RRF=`1/61+1/63≈0.03226`；chunk B 排第 2、第 1，RRF=`1/62+1/61≈0.03252`，B 会略高。若两个 query 的 best cosine 是 0.8、0.4，则 relevance=.6、coverage=.5；最终两个 chunk cosine=.9 时 diversity=`1-.9=.1`。
5. **Average Precision**：retrieved cosine 为 `[0.8, 0.2, 0.7]`、相关阈值 .35，相关位置是 1 和 3，AP=`(1/1 + 2/3)/2≈0.8333`，不是相关数/3=`0.6667`。这解释了为什么离线 context precision 可能比直觉的“命中率”高。

时间衰减再举一例：cosine=.8 的记录在 14 天后最终分数是 `.8*.85=.68`，无限久也不会低于 `.8*.7=.56`；因此长期记忆不会因为过期自动变成零，只是排序优先级下降。

## 21. 功能链路、状态机与设计意图

本章把“哪个模块调用哪个模块”改写成可执行的状态转换。理解这些链路时，优先区分三类状态：

1. **权威持久状态**：SQLite、用户目录文件、Qdrant collection；用于重启后的恢复和审计。
2. **短期运行状态**：进程内 live store、缓存、队列、channel health；用于降低延迟，但不能单独作为真相源。
3. **客户端暂存状态**：localStorage 草稿、React state、SSE 已显示内容；用于体验，不能替代服务端提交。

### 21.1 Docker 启动与请求主链

Compose 的正常启动顺序是：

```text
redis healthcheck + qdrant service_started
  -> backend 容器启动
     -> 建立 SQLite/WAL、checkpoint DB、目录和默认用户
     -> 读取 ai_config，构造 ChannelManager/缓存/embedding queue
     -> FastAPI lifespan 启动后台 worker
  -> frontend nginx 启动
     -> 浏览器请求 /api/* 由 nginx 代理到 backend:8000
```

证据为 `docker-compose.yml:2-74`、`backend/main.py:1-121`、`backend/config.py:102-113`。Docker 环境的空 `VECTOR_BACKEND` 会被 Compose 覆盖为 `qdrant`；裸启动没有该覆盖时可能走 NumPy，因此相同 API 在两个部署方式下有不同的存储、迁移和故障语义。

一次受保护请求的共同骨架是：

```text
Authorization Bearer
  -> get_current_user：验签、exp、sub 格式
  -> 路由再次按 user_id 查询/过滤资源
  -> 领域服务读取持久状态 + 可选 live/cache
  -> LLM/RAG/队列副作用
  -> 先写可恢复结果，再返回 JSON 或 SSE
```

这里有一个重要的安全边界：`get_current_user` 当前不查询 `users` 表（`backend/auth.py:140-160`），因此 token 签名有效且 sub 格式正确并不等于用户仍存在；`/auth/me` 等具体路由才会显式查用户。不要把 dependency 名称理解成完整的账户存活检查。

### 21.2 专项训练的完整闭环

主链由 `backend/graphs/drill_pipeline.py:60-123` 和 `backend/routers/interview.py:205-480` 共同实现：

```text
Home 选择 topic
  -> POST /interview/start-stream
  -> PREPARE：读取 profile、weak points、SR due、high-frequency、历史题、seed
  -> RETRIEVE：weak-point queries -> 并发多路召回 -> RRF -> 语义去重 -> rerank
  -> GENERATE：流式解析 JSON，逐题发 question SSE
  -> VALIDATE：coverage / duplicate / difficulty 三个确定性 validator
       ├─ pass -> FINALIZE
       └─ fail -> 一次局部 repair -> 仍失败也继续 FINALIZE
  -> FINALIZE：校准难度、建立 sessions/live、写 retrieval metrics、发 complete/done
  -> Interview 页面逐题答题：local draft + /progress
  -> POST /interview/end/{session_id}
  -> 保存 answers（先于评估）
  -> small tier 解耦逐题评估，失败时回退 legacy batch
  -> 保存 scores/review/RAG metrics
  -> 首次评分才写 profile、SM-2、knowledge evolution
  -> synced_at / 清理 live / 返回 Review
```

设计意图是让 LLM 负责“生成和解释”，让 RRF、validator、score clamp、幂等 marker 负责“可控性”。其中每一步的降级语义如下：

| 阶段 | 正常 | 可接受降级 | 仍需关注的边界 |
|---|---|---|---|
| Prepare | 有画像、到期项、seed | 无 weak point 时走 cold-start | `MAX_DRILL_QUESTIONS` 不参与；计划固定 10 槽 |
| Retrieve | Qdrant/embedding/reranker 全部可用 | 索引未就绪或超时则空上下文并触发后台 rebuild | 空上下文仍可生成，质量不一定等于失败 |
| Generate | 增量 JSON 逐题发送 | 部分完整对象 salvage；空/坏 JSON 抛出冷启动错误 | 已发送首 chunk 后不再切换 provider |
| Validate | 一轮内修坏题 | validator 异常被记录并继续；repair 失败放行原题 | fail-open，不能当质量硬门 |
| End/Evaluate | 逐题并发评分 + 总结 | small tier 失败回 legacy；答案已先保存 | 并发 end/sync 仍有 check-then-act 重复副作用 |
| Writeback | profile、SR、memory、evolution 一起更新 | 失败后前端有手工 sync 入口 | 进程崩溃时 shield 不能保证未提交写入完成 |

#### 为什么要先保存答案再评估

LLM 评估可能超时、断线或返回无法解析的 JSON。`backend/routers/interview.py:281-316` 先将原始答案写入 `sessions`，所以评估失败不会丢用户输入；后续可以重试评估或手工同步。这是“事实数据”和“派生评分”分离的典型设计。相反，若只在评估完成后落库，断线会同时丢答案和评分，无法补偿。

#### 首次评分与同步 marker

结束流程通过 `already_scored`/`meta.synced_at` 避免重复写 profile、SR 和知识演进（`backend/routers/interview.py:429-468,660-723`）。这解决了“刷新 Review 页面重复累计”的常见问题，但目前是先查询、最后写 marker 的 check-then-act：两个并发 end 或 sync 仍可能同时认为自己是首次。真正的幂等需要 session 级锁或数据库原子 claim/version，而不是只依赖末尾字段。

### 21.3 Resume LangGraph 的阶段状态机

Resume 面试不是固定题单，而是由 checkpoint 保存的对话状态驱动。阶段顺序定义在 `backend/graphs/resume_interview.py:50-59`：

```text
greeting -> self_intro -> technical -> project_deep_dive -> reverse_qa -> end
```

每次回答后的转移由 `route_after_answer` 决定（`backend/graphs/resume_interview.py:214-247`）：

| 当前阶段 | 推进条件 |
|---|---|
| greeting | 1 题后推进 |
| self_intro | 2 题后推进 |
| technical / project_deep_dive | 至少 2 题后才接受 `should_advance=true`，否则达到 `settings.max_questions_per_phase` 推进 |
| reverse_qa | 2 题后结束 |
| 任意阶段 | 硬上限 10 题，防止 LLM 永不推进 |

score 解析会 clamp 到 0～10，使用最后一个 EVAL marker（`backend/graphs/resume_interview.py:190-210`）。Graph 以 `thread_id=session_id` 绑定 checkpoint，`interrupt_before=["wait"]` 把“生成下一问”和“等待用户输入”分开（`backend/graphs/resume_interview.py:277-295`）。服务重启后可以从 checkpoint 恢复，但同一 thread 并发请求没有 revision/CAS，两个回答可能交叉 append transcript；这是状态机持久化而非并发串行化。

### 21.4 JD 备战链路

JD 流程由 `backend/graphs/job_prep.py:138-499` 和 `backend/routers/job_prep.py:22-101` 组成：

```text
提交 JD + position + 可选 resume
  -> preview：抽取岗位画像、能力要求、可能问题组、question blueprint
  -> questions：把 JD、resume context、用户知识库装入 prompt
  -> 增量 JSON 解析 / salvage 完整 question objects
  -> 最多保留 8 题并规范 id、question、difficulty、category
  -> 前端逐题作答
  -> evaluate：只评估非空答案，逐题流式返回 eval_result
  -> overall + dimension_scores + weak/strong points
  -> job_prep profile writeback，session/live 清理
```

预览和题目生成共享 `(user, jd)` 知识缓存，TTL 600 秒（`backend/graphs/job_prep.py:26-78`），避免同一 JD 在三个 prompt 中重复检索。生成 JSON 被截断时只 salvage 已完整对象，空结果才返回错误/回退；评估失败会发一个结构化的 fallback overall，而不是让 SSE 永远悬挂（`backend/graphs/job_prep.py:473-499`）。当前 schema normalize 对 difficulty/null/list 的强校验仍不足，非数字字段可能穿过部分路径。

### 21.5 知识库编辑、索引和演进链

知识正文的权威顺序是“文件操作先完成，再淘汰进程缓存并排队重建”：

```text
GET/PUT/DELETE/UPLOAD /knowledge/{topic}/core/...
  -> topic/user/path 校验
  -> create/update/generate：atomic_write_text；delete：unlink；upload：分块直写目标
  -> evict 当前进程 index cache（force route 还会 invalidate 持久索引）
  -> schedule_index_rebuild(topic,user)
  -> 2-worker embedding queue
  -> manifest + NumPy/Qdrant collection
  -> retrieval 读取 ready index
```

`backend/routers/knowledge.py:117-217` 的 create/update 使用原子文本写，减少半文件；upload 仍直接写最终路径，所以另有崩溃/并发边界。`backend/indexer.py:779-847,1096-1150` 在索引缺失或请求超时期间返回空上下文并触发后台重建，保证编辑接口不会被 embedding provider 长时间阻塞。强制 rebuild 目前会先 invalidate 再入队（`backend/routers/knowledge.py:292-306`），如果排队/构建失败，旧索引已经丢失；更稳妥的设计是新 collection/manifest 构建成功后再 alias swap。

队列链路为：

```text
submit -> task-id 去重 -> bounded queue(100)
  -> worker 执行 -> 失败按 2/4/8 秒退避（各任务 max_retries 为 2 或 3）
  -> 8 次连续失败熔断 60 秒
  -> done/error 状态保留 1 小时
```

队列满时 `submit` 可以返回 False，但 `schedule_index_rebuild` 当前仍可能向 API 返回 task_id，造成“看起来已提交、轮询却没有任务”的契约落差（`backend/embedding_tasks.py:188-285,526-548`）。队列和状态都在进程内，重启会丢未完成任务；多 worker 也会出现每个进程各自执行的重复 rebuild。

知识训练卡的链路则是：

```text
读取 topic sections
  -> 按文件/标题/长度/代码信号质量打分
  -> 600 候选上限、section 最短 80 字、最多连续 3 段
  -> LLM 生成 question/answer/tags/source_refs
  -> normalize + source_index 回指权威 section
  -> SQLite upsert knowledge_cards
  -> 用户 review：known(9)/uncertain(6)/unknown(2)
  -> 熟悉度写回并影响下次抽样
```

阈值和启发式见 `backend/knowledge_training.py:22-29,99-128,473-593`。它们是候选偏好，不是“低分段永远不训练”的硬过滤；没有候选时仍会退回 dedup 后的全部段落。

### 21.6 QA Arena 的多模态会话链

一次 QA turn 的顺序是：

```text
创建/选择 qa_session
  -> 图片数量/解码后大小校验；用户文字在送入 prompt 时按 6000 字符截断
  -> 保存 user message + image files
  -> 读取完整 history、rolling summary、向量 memory
  -> ContextBudget 分配 system/summary/memory/history
  -> LLM stream + reasoning/ping SSE
  -> 截断后保存 assistant response（最多 16000 字符）
  -> 超过 20 条时把最旧部分折叠为 summary，保留最近 10 条
```

图片每张解码后最多 6 MiB、每轮最多 4 张（`backend/qa_arena.py:225-275`）；请求正文还受 Docker Nginx 全局 32 MiB 限制，因此理论允许的 4×6 MiB base64 JSON 可能在进入后端前就被 413。摘要在新增约 10 条后增量刷新，普通单次 summary 预算 120,000 字符，map 阶段每块 48,000 字符、并发 4（`backend/qa_arena.py:350-402,700-770`）。

这是“原始消息可审计、prompt 上下文可压缩”的双层设计。需要特别注意：同一 session 的两个 chat 请求会同时读取同一 history，再各自写 user/assistant，后端没有 per-session 锁或 turn sequence；前端 pending 只能改善体验，不能保证并发顺序。regenerate 删除最后 assistant 也可能与正在进行的 chat 竞态。

### 21.7 Floating Assistant 与工具调用

Assistant 路由的思路是把自然语言请求交给一个受限工具循环：

```text
请求 + 历史
  -> ContextBudget
  -> LLM 选择 zero/one tool
  -> 工具查询 session/profile/history/knowledge 或执行受控动作
  -> 将 tool result 回填 messages
  -> 最多若干轮后生成最终文本
  -> 持久化 assistant chat + SSE complete
```

工具调用和历史写入集中在 `backend/assistant.py:300-730`，路由契约在 `backend/routers/assistant.py:1-30`。设计上工具结果应是事实，LLM 只负责解释；但 history/favorites 部分接口存在 `sessions` 与 `items` 返回键不一致，导致“工具执行成功、助手看到空列表”的契约错误（`backend/assistant.py:337-363,928-951`）。异步工具中仍有 `future.result(timeout=60)` 的同步等待，会阻塞 worker；应视作独立的并发边界。

### 21.8 Algorithm 会话与收藏链

算法流程是“题目输入 -> 解法流式生成 -> 追问 -> 保存卡片”：

```text
POST /algorithm/solve
  -> 建立 live algorithm session
  -> LLM 输出 solution + explanation + complexity
  -> 可选 follow-up 追加 messages
POST /algorithm/save
  -> 读取 live session
  -> 把 problem/solution/conversation 写 algorithm_cards
  -> 删除进程内 live entry
```

session 同时持久化在 `live_sessions`，但 save 路径只 `algorithm_sessions.pop`，没有调用 `del_live` 删除持久行（`backend/routers/algorithm.py:86-106`、`backend/live_store.py:124-126`）。重启后该算法 session 可能恢复，重复 save 会产生重复卡片；这属于“内存清理成功、持久 live 未清理”的补偿缺口。

### 21.9 SSE、故障切换与连接恢复

LLM 调用有两层故障语义：

```text
非流式 / 流开始前：同 channel 原请求 + 1 次重试
  -> retryable(408/409/429/5xx/timeout) 才换下一个 channel
  -> fatal 400/401/403/404/422 不 failover

流已发出 first chunk：提交当前 channel
  -> 中途错误不把另一模型的完整答案拼接到半截答案
```

`backend/llm_provider.py:53-57,171-203,298-327` 实现上述边界；`backend/channel_manager.py:11-69` 在 3 次错误后 cooldown 60 秒，冷却结束只允许一个 half-open probe，probe gate 30 秒。这个取舍避免重复答案和跨模型上下文不一致，但意味着用户可能看到“已输出部分内容后失败”，调用者必须把已发送部分当不可回滚事件。

SSE helper 对字符串、content block、reasoning block 都做容错；reasoning 阶段每 3 秒/idle 窗口发 keepalive，Nginx 关闭 proxy buffering，前端按 event type 增量更新（`backend/utils/sse_helpers.py:34-107,163-207`、`frontend/nginx.conf:20-28`）。浏览器断开时会 cancel/aclose pull task；这只能保证连接资源释放，不能撤销已执行的数据库/线程副作用。故障恢复必须分为“重新连接并恢复 UI”和“后台作业可重放/可查询”两套语义，不能把 SSE 重连当作事务回滚。

### 21.10 失败、补偿和人工恢复入口

项目目前的补偿策略可以归纳为：

| 故障 | 已有补偿 | 最终一致性边界 |
|---|---|---|
| LLM 生成 JSON 截断 | salvage 完整对象/局部 repair | salvage 仍可能少于 10 题 |
| RAG index 未就绪 | 空上下文继续生成 + 后台 rebuild | 本轮题目可能无知识支撑 |
| 小模型逐题评估失败 | legacy batch fallback | 两条评估路径评分口径可能不同 |
| SSE 断线 | 已保存答案/持久 session 可恢复，前端可 sync | 进程崩溃期间未完成写入无法靠 shield 保证 |
| profile/SR 写回失败 | `synced_at` 和手工同步接口 | 当前缺少原子 claim，并发可重复写 |
| embedding 队列失败 | 退避、熔断、状态查询 | queue 进程内，重启后任务丢失 |
| Qdrant 不可用 | 配置层可退 NumPy/空检索（取决于 backend mode） | Docker 默认 qdrant，迁移和双写必须人工核对 |

这套设计的核心思想是“先保存用户事实，再异步/可重试地产生派生信息；派生失败时保留可恢复的中间态”。但它还没有把所有副作用变成真正的事务或持久队列，生产上应把 `session_id + operation + revision` 作为幂等键，并为任务引入持久状态机。

## 22. 参数、阈值与容量边界

这一章把散落在代码和 Compose 中的“魔法数字”集中列出。参数只有在知道其作用域后才有意义：有些是单请求上限，有些是单用户上限，有些是进程内缓存容量；它们不能简单相加，也不能因为配置文件里存在就假设运行时一定消费。

### 22.1 Docker 部署默认值

| 服务/项 | Docker 默认 | 作用与边界 | 证据 |
|---|---|---|---|
| frontend | 宿主 `9000 -> 80` | 浏览器唯一推荐入口；Nginx `/api` 代理 backend | `docker-compose.yml:55-74`、`frontend/nginx.conf:1-37` |
| backend | 宿主 `9001 -> 8000` | 当前直接暴露，生产应收回内网 | `docker-compose.yml:34-53` |
| Qdrant | `6333/6334` 直接发布 | REST/gRPC 无服务端 API key 配置，含原文和用户向量 | `docker-compose.yml:14-31` |
| Redis | 256 MB、allkeys-lru | 缓存可淘汰；backend 启动却依赖 healthy | `docker-compose.yml:2-13` |
| backend memory | 2 GiB limit | local embedding/大批量 rebuild 可能 OOM；没有 CPU/PID limit | `docker-compose.yml:49-53` |
| vector backend | `qdrant` | Compose 环境覆盖空值；不是裸启动默认行为 | `docker-compose.yml:34-42`、`backend/config.py:102-113` |
| backend health | `GET /docs` | 只证明 HTTP/应用能返回 docs，不证明 DB、Redis、Qdrant、provider ready | `docker-compose.yml:44-48` |
| frontend health | `curl /` | `nginx:alpine` 是否自带 curl 要在真实 build 中验证 | `docker-compose.yml:66-70` |

Qdrant 只有 `service_started` 依赖，没有健康检查；backend 设计为连不上时空上下文并委派 rebuild，而不是容器启动失败。这是有意的“应用可启动、功能降级”，但 readiness 与 liveness 目前没有分开。

### 22.2 认证、会话与安全容量

| 参数 | 当前值 | 语义 |
|---|---:|---|
| JWT 有效期 | 7 天 | 无服务端 session；改密码不会撤销已发 token（`backend/auth.py:131-137`） |
| 密码最短 | 6 字符 | 注册/改密检查；没有 UTF-8 字节上限 |
| 登录失败 | 每 `(IP,email)` 15 分钟 5 次 | 只计失败，成功后清账户 bucket |
| 登录失败 | 每 IP 15 分钟 30 次 | 防 credential spraying |
| 注册尝试 | 每 IP 1 小时 5 次 | 成功与失败都计数 |
| limiter bucket | 10,000 目标容量 | 超限只清理已过期 bucket，近期 key 仍可能增长 |
| token user id | 8 位 hex 格式 | 防止直接把任意字符串拼入用户路径；不等于查库 |

位置：`backend/routers/auth.py:21-87`、`backend/rate_limit.py:15-62`、`backend/auth.py:25-29,140-160`。bcrypt 5.0 对超过 72 个 UTF-8 字节的密码会直接抛 `ValueError`，而模型只检查最小字符数；73 个 ASCII 字符或约 25 个中文字符即可触发 500，应该在三条密码路径统一返回 422。

### 22.3 LLM、渠道和 SSE 时间预算

| 参数 | 值 | 设计意图 |
|---|---:|---|
| HTTP connect/read/write/pool | 15s / 360s / 30s / 30s | read 高于 Nginx 300s，避免 app/httpx 先后竞速 |
| 同 channel 尝试 | 2 次 | 原请求 + 1 次 retry |
| 同 channel backoff | 1.5s | 仅 retryable 错误使用 |
| retryable status | 408/409/429/500/502/503/504 | 瞬态错误可重试/切换 |
| fatal status | 400/401/403/404/422 | 配置/请求错误不盲目 failover |
| channel cooldown | 3 次错误后 60s | 熔断，避免持续打坏 provider |
| half-open probe | 一次，最长 gate 30s | 冷却后只允许一个探活请求 |
| SSE idle ping | 30s | 保持代理连接；不代表上游完成 |
| reasoning keepalive | 约 3s | reasoning 有 chunk 但无 visible text 时仍让 UI 有进展 |
| Nginx proxy read timeout | 300s | 长推理的外层硬上限 |

来源：`backend/channel_manager.py:11-69`、`backend/llm_provider.py:25-57,171-203`、`backend/utils/sse_helpers.py:16-26,218-307`、`frontend/nginx.conf:20-28`。注意：SSE idle ping 只能刷新下游代理的可见活动；httpx read timeout 仍跟踪上游 socket，因而设置为 360s。

### 22.4 RAG、索引和向量缓存容量

| 项目 | 当前值 | 影响 |
|---|---:|---|
| index chunk | 1,024 token | SentenceSplitter 基础 chunk |
| chunk overlap | 100 token | 相邻 chunk 重叠，增加召回连续性和索引体积 |
| index cache | TTL 1h、最多 50 项 | 进程内缓存，超出 LRU 淘汰 |
| Redis embedding cache | TTL 7d、默认 JSON TTL 1h | Redis 不可用时退最多 5,000 项的进程内 LRU |
| memory vectors | 每用户最多 500 | 先全量 raw cosine，再时间衰减排序 |
| weak-point semantic merge | cosine 0.75 | 超过阈值视作同一弱点候选 |
| RAG dedup | balanced 0.85 | 与已保留片段任一相似即丢弃 |
| graph edge | cosine 0.65 | 只建可视化边，不做聚类/PageRank |
| reranker | 50 docs / doc 2,000 chars / query 512 chars | 防单次 rerank payload 失控 |
| embedding batch | 10 texts、4 workers | provider 常见 batch cap=10；并发约 40 条在途 |
| query embedding timeout | 20s | 延迟敏感；index rebuild timeout 默认 60s |

来源：`backend/indexer.py:43-44,241-266`、`backend/redis_cache.py:52-70`、`backend/vector_store/base.py:22-24`、`backend/vector_memory.py:31-40`、`backend/reranker.py:23-32`、`backend/graph.py:35-176`、`backend/llm_provider.py:30-48`。这些容量大多按进程计算，增加 worker 不会共享预算，反而会把总并发乘上 worker 数。

### 22.5 Live state、队列和缓存生命周期

| 状态 | TTL/容量 | 重启行为 |
|---|---:|---|
| graph live | 2h inactivity / 100 | 内存丢失，但 resume checkpoint 可从独立 DB 恢复 |
| drill/job/algorithm/RAG eval live | 2h inactivity / 200 | 内存丢失；部分 session 可从 SQLite 重建 |
| SQLite live_sessions cleanup | 24h | 过期行异步清理，不是实时删除 |
| embedding queue | 2 workers / 100 pending | 进程重启丢未执行任务 |
| task status | 1h | 轮询太晚会 404/unknown |
| queue retry | 2s, 4s, 8s；任务配置最多 2 或 3 次 retry | 失败后进入 error；不跨进程持久化 |
| queue breaker | 8 failures / 60s recovery | 保护 embedding provider |
| RAG eval job | live job 最多跟随进程内 store 约 2h；终态长期落 `rag_eval_runs` | 重启后 completed/failed 可按 job ID 从 SQLite 恢复；pending/running 被中断，进度、semaphore、队列和 in-flight 去重丢失 |

证据：`backend/live_store.py:17-93`、`backend/storage/live_sessions.py:8-43`、`backend/embedding_tasks.py:188-285,320-517`、`backend/routers/rag_eval.py:17-108`。因此 Docker 当前单 worker 与这些状态的作用域一致；多副本前必须把 job/queue/lock/live 全部外置或显式接受丢失。

### 22.6 文件、正文和请求体上限

| 输入 | 后端限制 | 额外 Docker 限制 |
|---|---:|---:|
| knowledge 单文件流式上传 | 200 MiB | Nginx `client_max_body_size=32m` 先拦截 |
| knowledge 正文保存 | 8 MiB 字符 | 同上 |
| QA 用户文本 | 送入 prompt 时 6,000 字符/消息 | endpoint 裸 dict 未对原始存储文本设 max；JSON 仍受 32 MiB |
| QA 图片 | 每张解码后 6 MiB，最多 4 张 | base64/JSON 开销使 4 张理论上可能先被 413 |
| assistant 请求 | 当前裸 dict，缺统一 max length | 32 MiB |
| JD 文本 | 代码有最小长度，缺最大值 | 32 MiB |
| assistant stored response | 16,000 字符 | 只是持久化截断，prompt cap 另算 |

来源：`backend/routers/knowledge.py:16-40,176-218`、`backend/qa_arena.py:225-275,350-404`、`backend/routers/assistant.py:10-24`、`backend/routers/job_prep.py:22-45`、`frontend/nginx.conf:20-28`。后端限制是业务层，Nginx 限制是传输层；修改其中一个不会自动修改另一个。

### 22.7 QA、摘要与上下文容量

| 参数 | 值 |
|---|---:|
| QA history formatter 的 assistant cap | 48,000 字符/消息 |
| 压缩触发 | 20 条消息 |
| 压缩保留 | 最近 10 条 |
| summary refresh | 每新增约 10 条 |
| single-pass summary budget | 120,000 字符 |
| map chunk / concurrency | 48,000 字符 / 4 |
| SSE reasoning heartbeat | 3s 左右 |
| generic ContextBudget minimum | 4,000 tokens |
| generic recent tail | 最近 2 条 message 永保留 |

“字符”和“token”是两套单位：QA 的 cap 是字符，ContextBudget 的 cap 是 tokenizer token。中文、代码和 base64 的实际 token 比例不同，不能把 48,000 字符直接当成 48,000 token。

### 22.8 训练卡、评测和导出边界

| 功能 | 限制/阈值 |
|---|---|
| knowledge training file | 2,000,000 chars |
| section | 最多 6,000 chars；候选最多 600；最短 80 chars；连续组最多 3 |
| card refs/tags | source refs 最多 3；tags 最多 6，单 tag 24 chars |
| RAG eval 输入 | API n 1～50、k 1～20；固定集再缩到 agent=11/python=9/java=9 的实际 case 数；未覆盖 topic 为 422 |
| RAG eval 进程预算 | 每进程同时执行 1 个，运行+排队最多 4 个；相同在途请求复用 job ID；这些限制不是跨副本全局限制 |
| synthetic 成本边界 | owner-only；估算 `n * (6 + (full ? k : 0))`，最多 300 次 LLM 调用；题内 LLM concurrency 4、检索/embedding 2 |
| frozen 成本边界 | 不调用 LLM；atomic 最多两题并发；production 每最多 5 case 串行执行一个共享 bundle |
| CLI 边界 | 绕过 HTTP owner、队列和 semaphore；退出码只拦截执行无效、degraded 或状态变化，不会自动对历史分数做回归阈值判断 |
| answer relevancy | prompt 期望反生成 3 个问题，但未硬截断返回列表 |
| export/list pagination | 多个路由未设 `ge/le`，负 LIMIT 在 SQLite 可能表示“不限” |
| export ids | 未统一分批，超大列表可能撞 SQLite bind parameter limit |

知识训练和评测限制分别见 `backend/knowledge_training.py`、`backend/rag_eval.py`、`backend/eval/rag_benchmark.py`、`backend/routers/rag_eval.py`。CLI 的 `_bootstrap()` 不调用 API lifespan 中的 Redis `init_cache`，因此 CLI 与 API 即使比较签名相同，也可能实际分别走进程 LRU 与 Redis；manifest 当前不记录 cold/warm cache，正式实验需在外部运行协议中固定并标注。分页边界分散在 `backend/routers/profile.py`、`backend/routers/qa_arena.py`、`backend/routers/favorites.py`、`backend/routers/algorithm.py`。

### 22.9 已确认的配置漂移和死参数

| 配置/注释 | 实际状态 | 学习时的正确结论 |
|---|---|---|
| `MAX_DRILL_QUESTIONS=15` | Pipeline、prompt、legacy 均固定 10，未读取 | 死配置/漂移；不是当前前后端 15 vs 10 运行时 bug |
| `RELEVANCE_THRESHOLD=0.5` | 在线 metrics 未使用，coverage 另有 floor | 不要用它解释 relevance 计算 |
| `difficulty anchors` mmap 注释 | 实际 JSON + `np.stack` 进程缓存 | 无 anchor 文件时默认 no-op |
| Redis optional | 代码可退 LRU，Compose backend 依赖 Redis healthy | 运行时可选与编排启动必需并存 |
| Docker `qdrant:latest` | 未 pin digest/version | fresh build 不可完全复现 |
| `strict=false` + ESLint JS-only | tsc/build 通过不代表 TS 静态门禁完整 | CI 需单独加入 TS/TSX ESLint 和 strict 迁移 |

这些不是“看起来不整洁”的文档问题，而是未来改配置时最容易产生错误假设的地方。任何把 10 改成可配置值的改动，都必须同时更新策略槽位、prompt、SSE 进度、前端百分比、validator 目标、RAG eval prompt 和测试夹具。

## 23. 边界条件处理矩阵

状态定义：

- **已处理**：当前代码在该作用域内有明确校验、原子操作或可验证的不变量。
- **部分处理**：覆盖了正常失败或单进程场景，但并发、重启、跨层限制或异常输入仍能突破。
- **未处理**：缺少边界校验/串行化/持久补偿，或已存在可确定复现的契约错误。

“已捕获异常并返回空结果”只能说明服务可继续运行，不自动意味着数据正确；“有 synced 标记”也不自动意味着并发幂等。

### 23.1 已明确处理的边界

| 边界 | 当前保证 | 实现依据 | 保证范围 |
|---|---|---|---|
| user id 路径注入 | JWT sub 只接受 8 位 hex，用户目录由服务端组合 | `backend/auth.py:25-29,140-160`、`backend/config.py:71-92` | 防任意路径字符串；不验证 users 行存在 |
| 多数资源租户隔离 | session、favorite、algorithm、QA、knowledge card 查询/更新带 `user_id` | `backend/storage/sessions.py:136-152,246-253`、`backend/storage/qa_sessions.py:50-88`、`backend/storage/algorithm.py:77-117` | 仅限已逐一带 filter 的仓储；无 DB RLS/外键兜底 |
| Qdrant memory 隔离 | 检索/删除带 payload `user_id` filter | `backend/vector_store/qdrant_store.py:127-136,178-204` | 应用通过该 adapter 访问时有效；Qdrant 端口公开仍可绕过 |
| knowledge 文件名/path | basename、扩展名、topic/path 校验 | `backend/routers/knowledge.py:25-57,176-218` | 防正常 API 路径穿越 |
| knowledge 正文崩溃原子性 | 临时文件完整写入后 `os.replace` | `backend/utils/files.py:15-39`、`backend/routers/knowledge.py:117-172` | 防半文件；不防两个编辑者 last-write-wins |
| knowledge 大文件读取 | 1 MiB 分块、单文件 200 MiB、正文 8 MiB 字符 | `backend/routers/knowledge.py:16-40,176-218` | 后端层；Nginx 可能更早以 32 MiB 拒绝 |
| QA 图片 | 最多 4 张、每张 base64 解码后 6 MiB，坏 data URL 拒绝 | `backend/qa_arena.py:225-275` | 单轮后端处理；总 HTTP body 仍受 Nginx 限制 |
| 在线 RAG mixed dimensions | metrics 检测 chunk/query 维度，无法测量时返回 None | `backend/rag_metrics.py:106-141` | 只保护 metrics；检索去重路径仍可能混维报错 |
| generation metric score | numeric string 转换、bool 拒绝、0～10 clamp | `backend/rag_metrics.py:51-73,186-218` | 只保护 RAG generation metrics，不覆盖所有业务评分 |
| RAG eval outcome/status 分层 | 固定集区分 `ok/empty/degraded/timeout/index_not_ready/error`；合成式缺测留分母并另报观察率 | `backend/eval/rag_benchmark.py:181-188,402-440`、`backend/rag_eval.py:663-741` | 防失败样本被静默剔除；不提供人工真值 |
| RAG eval full precision bool | full 模式 context precision judge 只接受真实 JSON bool | `backend/rag_eval.py:415-428` | 字符串 `"false"` 会按缺测/0 处理并影响 comparable，不会被当作 True |
| RAG eval 稳定标识 | dataset hash/case IDs、comparison signature、chunk stable id 进入 manifest/detail | `backend/eval/rag_manifest.py:155-335`、`backend/rag_ids.py` | 只保证同签名可比较；host 覆盖固定集仍需核对 hash |
| reranker index 校验 | cache 与实时 API 返回 index 均拒绝负数、bool、重复和越界 | `backend/reranker.py:117-140,181-188,247-262`、`tests/test_reranker_cache.py` | API 协议漂移时降级原排序；真实外部协议仍需 smoke |
| 固定评测集 Docker fallback | 镜像内带 fallback 固定集；运行时宿主固定集存在时 hash 进入 manifest | `backend/Dockerfile:15`、`backend/eval/rag_benchmark.py:46-50` | 防 bind mount 让固定集缺失；不阻止宿主有意覆盖 |
| 向量记忆零 embedding | embedding 失败的全零向量写库前过滤 | `backend/vector_memory.py:264-310` | 防新脏记录；旧记录/NumPy 与 Qdrant 查询语义仍需迁移检查 |
| SSE idle 检测 | idle heartbeat 不 cancel 正在进行的 `__anext__`；退出时 cancel/aclose | `backend/utils/sse_helpers.py:110-160` | 保护连接和资源释放；不保证后台事务完成 |
| 流式 failover 边界 | first chunk 前可换 channel；first chunk 后不拼另一模型答案 | `backend/llm_provider.py:298-327` | 保证输出不跨模型拼接；中途失败仍会留下部分输出 |
| drill 原始答案恢复 | 评估前先持久化 answers；live miss 可从 session 重建 | `backend/routers/interview.py:281-316`、`backend/routers/interview.py:79-103` | 单请求失败可恢复；并发 end 仍可能重复副作用 |
| Resume 重启恢复 | LangGraph 使用独立 SQLite checkpoint，thread_id=session_id | `backend/graphs/resume_interview.py:277-295`、`backend/routers/interview.py:83-103` | 进程重启可恢复；同 thread 并发无锁 |
| 索引请求降级 | 缺索引/超时返回空上下文并调后台 rebuild | `backend/indexer.py:1096-1150` | 保持请求可用；不能保证本轮答案有知识依据 |
| 索引单进程互斥 | cache 有 lock/TTL/LRU，同 user/topic build 有 lock | `backend/indexer.py:55-107,779-847` | 单进程有效；多 worker/副本不共享 |
| profile 文件写事务 | per-user lock + 临时文件 replace | `backend/memory.py:149-205` | 同进程写入不互相覆盖；跨进程锁不成立 |
| 最近上下文保留 | 按 token 预算丢最旧消息、最近消息保留 | `backend/context_assembler.py:221-307` | 普通窗口有效；超大 required/tail 可溢出 |

### 23.2 只处理了一部分的边界

| 边界 | 已处理部分 | 未覆盖部分/建议 | 证据 |
|---|---|---|---|
| auth 限流 | 滑窗、线程锁、账户和 IP 两级 bucket | 仅进程内；10,000 超限只删过期 key；直连 9001 可伪造 `X-Real-IP` | `backend/routers/auth.py:21-87`、`backend/rate_limit.py:15-62` |
| JWT 有效性 | 验签、exp、sub 格式 | dependency 不查用户存在/token version；删号/迁移后的旧 JWT 可继续命中部分 API | `backend/auth.py:140-160` |
| 请求体限制 | 后端各功能有单项限制 | Nginx 统一 32 MiB 与 knowledge 200 MiB、QA 4×6 MiB 契约冲突 | `frontend/nginx.conf:20-28`、`backend/routers/knowledge.py:16-22`、`backend/qa_arena.py:225-275` |
| interview 幂等意图 | `already_scored`、`synced_at` 防顺序重放 | SELECT/check 与写 marker 非原子；需要 DB claim/status/version | `backend/routers/interview.py:281-316,429-468,660-723` |
| 断线后的持久化 | 关键写入部分用 `asyncio.shield` | shield 只抗协程取消，不抗进程崩溃；`to_thread` 也不能强停 | `backend/routers/interview.py:429-468,516-545,600-640` |
| drill/JD progress | live 同时落 SQLite，重启可重建 | progress 无 revision/CAS，旧 POST 后到可覆盖新值 | `backend/routers/profile.py:337-386`、`backend/storage/sessions.py:78-109` |
| knowledge 编辑 | 正文 atomic replace | `topics.json` load/save 无锁且直接 write_text；并发 create/delete 可丢更新 | `backend/indexer.py:206-221`、`backend/routers/profile.py:27-69` |
| embedding queue | bounded queue、去重、退避、breaker、状态回收 | submit False 可仍返回 task_id；重启丢队列；stop 不 drain thread | `backend/embedding_tasks.py:188-285,466-548` |
| RAG validator | coverage/duplicate/KL + 一次局部 repair | embedding fail-open、validator crash 跳过、修复失败仍 finalize | `backend/graphs/drill_pipeline.py:630-747` |
| RAG eval job 生命周期 | 每进程 semaphore、队列上限、in-flight 去重；completed/failed 落 SQLite | pending/running 仍是进程内，重启/多副本会丢进度和队列；需 Redis/DB 持久队列 | `backend/routers/rag_eval.py:31-90`、`backend/storage/rag_eval_store.py` |
| RAG eval CLI/API parity | CLI 可复用 benchmark logic 并输出同类 manifest | CLI 绕过 HTTP owner、队列和 semaphore，且不执行 API lifespan Redis init；正式比较需记录 cold/warm cache | `backend/eval/rag_benchmark.py`、`backend/rag_eval.py`、`backend/routers/rag_eval.py:107-151` |
| RAG eval manifest | 记录 dataset/corpus/index/provider/model/vector backend/git sha | `APP_GIT_SHA` 未注入为 unknown；Qdrant 只校验维度，缺实际建库 embedding fingerprint；provider failover 受健康态影响 | `backend/eval/rag_manifest.py:170-335` |
| 固定评测集宿主覆盖 | 镜像 fallback 和 manifest hash 能发现变更 | `/app/data/eval/rag_queries.json` 有意优先，运维误改仍会改变实验集；发布前应冻结文件并对比 hash | `backend/eval/rag_benchmark.py:46-50` |
| LLM schema 防御 | stream block、训练卡 normalize、部分 score clamp | JD、profile op index、assistant dict 等边界仍宽松；缺统一 Pydantic schema | `backend/utils/sse_helpers.py:34-107`、`backend/memory.py:493-552`、`backend/graphs/job_prep.py:232-247` |
| ContextBudget | 最小 channel window、最大 output reserve、optional 截断 | min 4,000/required/keep_last 可越预算；工具调用结构未完整计 token | `backend/context_assembler.py:96-140,221-307` |
| embedding 缓存 | Redis TTL 和进程 LRU | key 未统一包含 model/version/dimension；切模型可能混维 | `backend/redis_cache.py:113-287`、`backend/graph.py:84-130` |
| 时间处理 | JWT/部分 QA 使用 UTC-aware | profile/SR/live/session 混用 naive local 与 SQLite UTC，Docker/本地会偏移 | `backend/auth.py:131-137`、`backend/memory.py:184-188`、`backend/storage/live_sessions.py:8-43` |
| Resume 阶段终止 | 每阶段规则 + 硬上限 10 | 不防同 session 并发回答；checkpoint 不是串行锁 | `backend/graphs/resume_interview.py:214-295` |
| QA 上下文压缩 | 20 条后摘要，保留最近 10，增量刷新 | 同 session 并发 turn 会基于同一旧 history 生成，摘要 cursor 也可竞态 | `backend/qa_arena.py:400-480,650-695` |
| Qdrant rebuild | 有 force rebuild、manifest 和 task status | delete collection 后重建，中断可留下 partial；应 shadow+alias swap | `backend/indexer.py:564-602` |
| Redis 可用性 | 运行时异常退内存 LRU | Compose 启动强依赖 Redis healthy；“可选”只适用于启动后的故障 | `backend/redis_cache.py:113-287`、`docker-compose.yml:34-45` |
| backup 文档 | 提到 data 目录和迁移脚本 | SQLite WAL/checkpoint、Qdrant snapshot、文件源缺一致性维护窗口 | `DEPLOYMENT.md:180-225`、`docker-compose.yml:2-74` |

### 23.3 尚未处理或存在确定缺陷的边界

| 严重度 | 边界/复现场景 | 当前后果 | 建议与证据 |
|---|---|---|---|
| 高 | 密码 UTF-8 长度 >72 bytes | bcrypt 5.0 在注册、登录或改密抛 `ValueError`，可返回 500 | 三条路径统一校验 `len(password.encode('utf-8')) <= 72` 并返回 422；`backend/models.py:85-104`、`backend/auth.py:32-37,95-126,215-223` |
| 高 | 两个并发 `end`/`sync` 同一 session | 两者都在 marker 前通过检查，重复推进 SR、profile 和 knowledge evolution | DB 原子 claim + session lock + idempotency key；`backend/routers/interview.py:281-316,429-468,660-723` |
| 高 | 同一 QA session 并发 chat | 两个回复读到同一历史，消息交错、上下文漏 turn | per-session lock、request id、turn sequence；`backend/qa_arena.py:650-695` |
| 高 | 同一 Resume thread 并发 answer | `update_state`/stream 对同 checkpoint 交叉写，transcript 乱序 | thread_id 互斥 + expected revision；`backend/routers/interview.py:224-278` |
| 高 | force rebuild 在新索引 ready 前删旧 collection | 崩溃/关机可留下不存在或 partial collection | shadow collection + count 校验 + alias 原子切换；`backend/indexer.py:564-602` |
| 高 | Settings 三个 ChannelManager 保存旧全量快照 | 后保存的 section 可回滚刚保存的另一 section | section PATCH/ETag 或父级单一 state；`frontend/src/pages/Settings.tsx:294-347`、`frontend/src/components/ChannelManager.tsx:73-89,166-187` |
| 高 | Knowledge 快速切 topic 后旧响应到达 | A topic 响应可覆盖 B 编辑器并误写 B | AbortController + request generation + 保存绑定 load-time topic；`frontend/src/pages/Knowledge.tsx:103-179` |
| 中 | `topics.json` 并发 read-modify-write | 建/删 topic 丢更新，崩溃可能破坏 JSON | per-user file lock + atomic replace + revision；`backend/indexer.py:206-221`、`backend/routers/profile.py:27-69` |
| 中 | algorithm save 只 pop 内存 live | 持久 `live_sessions` 行仍在，重启可恢复并重复保存卡片 | 调用 `del_live`，卡片保存加 session id 唯一键；`backend/routers/algorithm.py:86-106`、`backend/live_store.py:124-126` |
| 中 | 多路 list API 的负/超大 `limit/offset` | SQLite `LIMIT -1` 可变成不限制，全表加载/导出 | Pydantic `ge=0/le=N` + cursor pagination；`backend/routers/profile.py:314-324`、`backend/routers/favorites.py:31-42`、`backend/routers/algorithm.py:109-119` |
| 中 | 超大 export ids | 可能超过 SQLite bind 参数上限或产生大内存响应 | 限量、分批、流式导出；`backend/routers/favorites.py:65-75` |
| 中 | Assistant history/favorites 返回键 | tool 读 `sessions`，仓储返回 `items`，历史存在也显示空 | 统一 typed contract 并加工具契约测试；`backend/assistant.py:337-363,928-951` |
| 中 | Assistant async tool 同步 `future.result(60)` | 阻塞事件循环 worker，SSE ping 和其他请求停顿 | `await asyncio.wait_for(asyncio.to_thread(...))`；`backend/assistant.py:685-700` |
| 中 | RAG dedup 遇缓存混维 | cosine 计算可能抛错，中断整轮 retrieval | cache key 加 model/dim，去重前维度 guard；`backend/graphs/rag_retrieval.py:197-229` |
| 中 | WeakPointCoverage `round` 门槛 | 2 个 WP 命中 1 个时可能不跑 embedding，最终 0.5<0.6 又失败 | 使用 `ceil` 或按最终 ratio 决定补 embedding；`backend/graphs/validators/weak_point_coverage.py:21-82` |
| 中 | Difficulty validator 类型/数量边界 | 字符串 difficulty 或非 10 题时可能 fail 但 bad_ids 为空 | normalize 后统一 int，按实际 n 计算目标计数；`backend/graphs/validators/difficulty_distribution.py:18-91` |
| 中 | 单弱点 SR 快速重复 review | read-modify-write 无幂等键，多击可重复推进或丢更新 | card version/CAS + idempotency key；`backend/storage/knowledge_cards.py:109-135` |
| 中 | Topic mastery 输入未 clamp | 越界 LLM score/difficulty 可推离 0～100 | 复用统一 score/difficulty parser；`backend/routers/interview.py:823-852` |
| 中 | ContextBudget 极小窗口 | `max(4000, ...)` 可直接超过真实剩余窗口 | budget 上限不得超过 `window-reserve-margin`，required 设置硬 cap；`backend/context_assembler.py:136-140,221-307` |
| 中 | Graph cache 不含 embedding model/version | 切模型复用旧向量或混维；节点多时 O(N²) | key 加 model/schema，切换清 cache，限制节点/ANN 建边；`backend/graph.py:84-176` |
| 低 | LearningHeatmap 用 UTC ISO 截日 | Asia/Shanghai 本地日期可能偏前一天 | 本地年月日格式化；`frontend/src/components/charts/LearningHeatmap.tsx:26-65` |
| 低 | 前端 TS lint 未覆盖 | lint 命令成功但 TS/TSX 缺规则保护 | ESLint 配置纳入 TS parser/files；`frontend/eslint.config.js:7-28` |

### 23.4 边界整改优先顺序

1. **先修数据重复和跨租户/公网暴露风险**：并发 end/sync、QA/Resume session 锁、Settings/Knowledge 竞态、Qdrant/Backend 端口、默认 owner 和 TLS。
2. **再修可确定的输入崩溃与错误契约**：bcrypt 72 bytes、Assistant 返回键、pagination、JD/profile strict schema。
3. **随后提升作业和索引的一致性**：持久队列、原子 claim、shadow collection、任务恢复、跨存储备份演练。
4. **最后校准指标与配置**：统一 10 题配置、固定集置信区间、mastery clamp、cache model version、TS lint/strict。

边界处理的验收标准不应只是“接口不 500”。至少要同时验证：响应状态正确、权威数据未被旧请求覆盖、重复请求不重复副作用、进程重启后状态可解释、Docker 代理层与后端限制一致、指标在失败样本下不会静默变好。
