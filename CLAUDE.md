# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**SparkOffer** — AI 面试训练系统。核心不是"一次性问答"，而是一个跨会话的闭环：`训练 → LLM 评估 → 画像更新（Mem0 风格 ADD/UPDATE/IMPROVE） → 向量化入库 → SM-2 复习调度 → 下次出题融合三层上下文（长期画像 + 领域掌握度 + RAG 知识库）`。技术栈：FastAPI + LangGraph + LangChain + LlamaIndex 后端；React 19 + Vite + Tailwind v4 前端。

## Commands

### Backend (Python, run from repo root)
```bash
pip install -r requirements.txt
# Optional, only if EMBEDDING_BACKEND=local
pip install -r requirements.local-embedding.txt

# Dev server (host 0.0.0.0:8000, reload on change)
uvicorn backend.main:app --reload --port 8000

# Standalone scripts (run via -m from repo root so backend.* imports resolve)
python -m scripts.warmup_index
python -m scripts.migrate_to_three_topics
```

No pytest suite exists. Smoke-test backend changes by hitting `http://localhost:8000/docs`.

### Frontend (from `frontend/`)
```bash
npm install
npm run dev        # vite, defaults to http://localhost:5173
npm run build      # vite build → frontend/dist
npm run lint       # eslint .
```
No frontend test runner is configured.

### Full stack via Docker
```bash
docker compose up --build         # local: front 80, back 8000
# Server deploy uses ports 9000 (front) / 9001 (back) — see DEPLOYMENT.md
```
The backend container has a `data:/app/data` volume — SQLite DB, knowledge docs, user profiles, and `ai_config.json` persist here. Never bake user data into the image.

### Data reset
`clear_data.sh` wipes user data (SQLite, vectors, profiles) while keeping the seed knowledge base — use during local debugging only.

## Architecture (the big picture)

### Backend module map (`backend/`)
The backend is **mostly flat, not layered**. Each file is responsible for a domain capability, not a layer:

- `main.py` — FastAPI app + lifespan. Lifespan initializes embeddings, LlamaIndex settings, DB tables, default user, multi-channel config, and starts the background embedding task queue. Modifying startup order matters: DB → embeddings → indexer → task queue.
- `config.py` — Pydantic `Settings`. **Per-user paths are derived here** (`user_data_dir`, `user_profile_dir`, `user_resume_path`) — all user data lives under `data/users/{user_id}/`. Never write user-scoped data outside this prefix (path-traversal protection assumes this layout).
- `ai_config.py` + `channel_manager.py` — Runtime-mutable LLM/Embedding/ASR channel pool. Keys rotate, failed channels cool down (60s, 3-strike). Code paths must call `llm_provider.get_chat_model()` / `get_embedding()` rather than reading `settings.api_key` directly, otherwise the failover layer is bypassed.
- `auth.py` — JWT (HS256, 7-day) + bcrypt. Default user is auto-created on startup; registration gated by `ALLOW_REGISTRATION`.
- `storage/` — SQLite (WAL mode, 5s busy_timeout). All tables live in **one** `data/interviews.db`. `database.py` owns connection + schema init; other files are table-specific repositories.
- `models.py` — Pydantic schemas + the `InterviewPhase` enum that drives the LangGraph state machine.
- `graphs/` — LangGraph workflows. **Four entry points**: `resume_interview.py` (5-phase state machine), `job_prep.py` (JD-targeted), `topic_drill.py` (10-question专项), `review.py` (SM-2 due items). Each builds a `StateGraph` with `MemorySaver` checkpointing.
- `assistant.py` — Floating side-panel agent. **Single agent + tool-use** (~14 tools, see `TOOLS` constant). Not multi-agent — do not describe it as such.
- `memory.py` — Long-term user profile. Mem0-style two-stage update: (1) LLM extracts new findings, (2) LLM merges with existing profile choosing ADD / UPDATE / NOOP / IMPROVE per entry.
- `vector_memory.py` — Self-built vector store: SQLite BLOB + numpy cosine. Designed for ≤500 vectors per user (`MAX_VECTORS_PER_USER`). Includes time-decay (14-day half-life, capped at 30% weight) and semantic dedup at similarity 0.75. **Do not swap to Milvus/Pinecone** — the SQLite-blob approach is intentional for project scale.
- `indexer.py` — LlamaIndex knowledge-base indices. **Cached in-process** (TTL 1h, max 50 user indices). Retrieval is wrapped with `asyncio.to_thread` + 60s timeout — LlamaIndex itself is sync.
- `embedding_tasks.py` — Background priority queue + exponential-retry worker + 3-state circuit breaker (CLOSED/OPEN/HALF_OPEN, 5-failure trip, 60s probe). All embedding writes go through this — don't `await embed(...)` inline in request handlers for non-blocking paths.
- `spaced_repetition.py` — SM-2 (`ease_factor ≥ 1.3`). Weak points auto-graduate after 3 consecutive ≥7 scores.
- `prompts/` — **All LLM prompts are centralized here.** Adding a new interview style means editing prompts, not the graph code. Keep this invariant.
- `routers/` — FastAPI routers, one per domain (auth / interview / resume / knowledge / qa_arena / assistant / graph_router / …). New endpoints register in `main.py` `include_router(...)` block.
- `utils/sse_helpers.py` — SSE streaming primitives: 30s idle heartbeat ping, 200-char progress events. Use these wrappers for any new streaming endpoint; raw `EventSourceResponse` won't survive nginx idle timeouts.
- `live_store.py` — In-memory TTLDict for transient session state (2h TTL). Not a replacement for the SQLite tables — use only for short-lived streaming buffers.

### Frontend (`frontend/src/`)
- React 19 + React Router v7 + Vite 8 + Tailwind v4 + Radix primitives (shadcn-style). TypeScript throughout.
- `pages/` — route-level views, lazy-loaded.
- `api/` — fetch client wrappers; auth header injection lives here.
- `components/` — UI building blocks + Recharts (radar/trend) + react-force-graph-2d (question graph).
- SSE consumption: streaming endpoints emit incremental JSON; the frontend incrementally parses and renders question-by-question as the LLM streams tokens. Maintain this contract when changing streaming responses.

### Data layout (`data/`, gitignored)
```
data/
├── interviews.db                # all SQLite tables
├── ai_config.json               # runtime LLM/Embedding/ASR channels
├── topics.json                  # domain catalog
├── knowledge/                   # shared knowledge base (LlamaIndex source)
├── high_freq/                   # auto-curated frequent-mistake bank
├── qa_notes/                    # arena Q&A notes
├── users/{user_id}/             # per-user isolation root
│   ├── resume/
│   └── profile/                 # long-term profile JSON + vector blobs
└── .index_cache/                # LlamaIndex on-disk index cache
```

## Project conventions to respect

- **Per-user isolation is enforced by `config.py` helpers.** Any new feature that writes user data must call `settings.user_data_dir(user_id)` (or its derivatives), not hand-roll paths.
- **LLM/Embedding access goes through `llm_provider` + `channel_manager`.** Calling vendor SDKs directly defeats the failover/cooldown layer.
- **Prompts live in `backend/prompts/`.** Don't inline new long prompts in graph/router code; centralize them.
- **Mastery scoring is deterministic** (`difficulty/5 × score/10`), not an LLM judgment. Profile *merging* is the only LLM-driven step. Don't conflate the two when changing scoring logic.
- **Two distinct RAG-metric systems, never comparable.** `rag_metrics.py` = online, zero-LLM-cost retrieval health gauges on the question-gen path (`relevance` / `discrimination` / `diversity` — embedding-only, no ground truth). `rag_eval.py` = offline RAGAS benchmark with an LLM-synthesized golden set (`hit@k` / `hit_at_k_strict` / `mrr` / precision / recall / faithfulness / relevancy / correctness — ground-truth, LLM-judged). They live on different scales: out-题 relevance naturally ~0.45–0.65, RAGAS scores naturally higher. Color each metric against its own band via `frontend/src/lib/metrics.ts` `METRIC_SPEC`, never a global threshold. Don't reintroduce the old circular precision/recall on the question-gen path (chunks scored against the queries that retrieved them → pinned ~100%).
- **The simplification pass (commit `9a07107`) deliberately removed defensive layers** — don't reintroduce broad try/except, mock fallbacks, or feature-flag scaffolding without a concrete reason.
- **Sync code in async paths is wrapped, not avoided.** LlamaIndex retrieval uses `asyncio.to_thread` + timeout; mirror this pattern for new sync-only dependencies rather than blocking the event loop.
- **Hard limits encode design tradeoffs** (see `项目技术文档/04_记忆与个性化.md`): 500 vectors/user, 50 cached indices, 1h index TTL, 2h live-session TTL, 0.75 semantic-dedup threshold, 14-day decay half-life. Change them with care — they bound memory/cost.

## Reference docs already in the repo

- `README.md` / `README.en.md` — product pitch, quickstart, tech stack table.
- `DEPLOYMENT.md` — Docker, ports (9000/9001), volume backup, healthcheck details.
- `interview-docs/01..06_*.md` + `项目技术文档/01..11_*.md` — exhaustive architecture / data-flow / DB / prompt / frontend write-ups. When asked deep "how does X work" questions, prefer reading these over re-deriving from code.
