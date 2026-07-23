"""统一数据库连接管理 — 线程本地连接 + 集中式表初始化。

每个线程持有独立的 SQLite 连接，避免多线程/并发请求共用同一连接导致锁定和状态污染。
init_all_tables() 在启动时调用一次，创建所有表、索引和迁移。
"""
import logging
import sqlite3
import threading

from backend.config import settings

logger = logging.getLogger("uvicorn")

DB_PATH = settings.db_path

_local = threading.local()


def _make_connection() -> sqlite3.Connection:
    """创建一条新的 SQLite 连接并完成基础配置。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")  # 等待锁最多 5s，而非直接报错
    return conn


def get_db() -> sqlite3.Connection:
    """返回当前线程独立的 SQLite 连接。若连接已关闭则重建。"""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        conn = _make_connection()
        _local.conn = conn
        return conn
    # 检测连接是否已被关闭/损坏，如有则重建
    try:
        conn.execute("SELECT 1")
    except Exception:
        conn = _make_connection()
        _local.conn = conn
    return conn


def init_all_tables():
    """启动时调用一次，创建所有表、索引、执行迁移。"""
    conn = get_db()

    # ── users ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         TEXT PRIMARY KEY,
            email      TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            name       TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── sessions ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            topic TEXT,
            meta TEXT DEFAULT '{}',
            questions TEXT DEFAULT '[]',
            transcript TEXT DEFAULT '[]',
            scores TEXT DEFAULT '[]',
            weak_points TEXT DEFAULT '[]',
            overall TEXT DEFAULT '{}',
            review TEXT,
            user_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrate: add columns if missing
    for col, default in [("questions", "'[]'"), ("overall", "'{}'"),
                         ("user_id", "NULL"), ("meta", "'{}'"),
                         ("reference_answers", "'{}'")]:
        try:
            conn.execute(f"SELECT {col} FROM sessions LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT DEFAULT {default}")

    # ── favorites ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT,
            question TEXT NOT NULL,
            user_answer TEXT DEFAULT '',
            reference_answer TEXT DEFAULT '',
            score REAL,
            assessment TEXT DEFAULT '',
            topic TEXT DEFAULT '',
            difficulty TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── algorithm_cards ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS algorithm_cards (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            problem_text TEXT NOT NULL,
            difficulty TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            solution TEXT DEFAULT '',
            conversation_history TEXT DEFAULT '[]',
            source_url TEXT DEFAULT '',
            language TEXT DEFAULT 'python',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── live_sessions ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_sessions (
            session_id TEXT PRIMARY KEY,
            session_type TEXT NOT NULL,
            user_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── assistant_chats ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_chats (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── memory_vectors ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_vectors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_type  TEXT NOT NULL,
            content     TEXT NOT NULL,
            topic       TEXT,
            session_id  TEXT,
            metadata    TEXT DEFAULT '{}',
            embedding   BLOB NOT NULL,
            user_id     TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrate: add user_id if missing
    try:
        conn.execute("SELECT user_id FROM memory_vectors LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE memory_vectors ADD COLUMN user_id TEXT")

    # ── question_embeddings ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS question_embeddings (
            question_hash TEXT PRIMARY KEY,
            topic         TEXT,
            question_text TEXT,
            embedding     BLOB NOT NULL,
            user_id       TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("SELECT user_id FROM question_embeddings LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE question_embeddings ADD COLUMN user_id TEXT")

    # ── qa_sessions ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_sessions (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            title      TEXT DEFAULT '新对话',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration: add context_summary column to qa_sessions
    try:
        conn.execute("SELECT context_summary FROM qa_sessions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE qa_sessions ADD COLUMN context_summary TEXT")
        conn.execute("ALTER TABLE qa_sessions ADD COLUMN summary_msg_count INTEGER DEFAULT 0")

    # ── qa_messages ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration: add images column to qa_messages (multimodal attachments —
    # JSON array of stored image filenames under data/users/<uid>/qa_uploads/<sid>/)
    try:
        conn.execute("SELECT images FROM qa_messages LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE qa_messages ADD COLUMN images TEXT")

    # ── 索引 ──

    # sessions（新增 — 修复全表扫描）
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_created ON sessions(user_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_topic ON sessions(user_id, topic)")

    # favorites
    conn.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_favorites_topic ON favorites(user_id, topic)")

    # algorithm_cards
    conn.execute("CREATE INDEX IF NOT EXISTS idx_algo_user ON algorithm_cards(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_algo_diff ON algorithm_cards(user_id, difficulty)")

    # assistant_chats
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ac_user ON assistant_chats(user_id)")

    # memory_vectors
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mv_type ON memory_vectors(chunk_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mv_topic ON memory_vectors(topic)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mv_user ON memory_vectors(user_id)")

    # question_embeddings（新增）
    conn.execute("CREATE INDEX IF NOT EXISTS idx_qe_user_topic ON question_embeddings(user_id, topic)")

    # qa_sessions / qa_messages
    conn.execute("CREATE INDEX IF NOT EXISTS idx_qa_sessions_user ON qa_sessions(user_id, updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_qa_messages_session ON qa_messages(session_id, id ASC)")

    # Durable idempotency records for user-confirmed QA knowledge ingestion.
    # A browser may abandon the HTTP request while the LLM/writeback continues;
    # keeping the completed response here prevents a later retry from applying
    # the same card a second time, including across worker or process restarts.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_ingest_requests (
            user_id         TEXT NOT NULL,
            session_id      TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            content_hash    TEXT NOT NULL,
            idempotency_marker TEXT NOT NULL,
            claim_token     TEXT NOT NULL,
            status          TEXT NOT NULL CHECK (status IN ('pending', 'complete')),
            topic_key       TEXT,
            normalized_content TEXT,
            response_json   TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id, idempotency_key)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_qa_ingest_updated "
        "ON qa_ingest_requests(updated_at)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_qa_ingest_marker "
        "ON qa_ingest_requests(user_id, idempotency_marker)"
    )

    # ── rag_metrics ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_metrics (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT NOT NULL,
            user_id             TEXT NOT NULL,
            topic               TEXT NOT NULL,
            stage               TEXT NOT NULL,
            context_relevance   REAL,
            context_precision   REAL,
            context_recall      REAL,
            faithfulness        REAL,
            answer_relevance    REAL,
            answer_correctness  REAL,
            chunk_count         INTEGER,
            detail_json         TEXT DEFAULT '{}',
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_metrics_user ON rag_metrics(user_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_metrics_topic ON rag_metrics(user_id, topic, created_at DESC)")

    # question_gen stage moved off circular precision/recall to non-circular
    # relevance/coverage/diversity. Old context_* + discrimination columns kept
    # for read-only history; new rows write relevance + coverage + diversity.
    # ("discrimination" survives for legacy rows written before the coverage
    # rename; it is no longer written.)
    for col in ("discrimination", "diversity", "coverage"):
        try:
            conn.execute(f"SELECT {col} FROM rag_metrics LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE rag_metrics ADD COLUMN {col} REAL")

    # The logical key is (user, session, stage), but older schemas allowed
    # duplicate rows. Keep the newest observation before adding the unique
    # index so startup migration is safe on existing databases. ``created_at``
    # is the primary recency signal; id breaks ties deterministically.
    dedupe_cursor = conn.execute(
        """
        DELETE FROM rag_metrics
        WHERE EXISTS (
            SELECT 1
            FROM rag_metrics AS newer
            WHERE newer.user_id = rag_metrics.user_id
              AND newer.session_id = rag_metrics.session_id
              AND newer.stage = rag_metrics.stage
              AND (
                  COALESCE(newer.created_at, '') > COALESCE(rag_metrics.created_at, '')
                  OR (
                      COALESCE(newer.created_at, '') = COALESCE(rag_metrics.created_at, '')
                      AND newer.id > rag_metrics.id
                  )
              )
        )
        """
    )
    if dedupe_cursor.rowcount:
        logger.warning(
            "Removed %d duplicate RAG metrics row(s) before adding uniqueness",
            dedupe_cursor.rowcount,
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_metrics_user_session_stage "
        "ON rag_metrics(user_id, session_id, stage)"
    )

    # ── rag_eval_runs ──
    # 离线 RAGAS 基准评测结果（每次评测一行）。与 rag_metrics（线上免费健康度仪表，
    # 按 session/stage 记录）刻意分表：本表是 run 级、带 gold 标注的基准指标
    # （hit@k / mrr / precision / recall / faithfulness / relevancy / correctness），
    # 混入 rag_metrics 会污染按 stage 过滤的趋势图。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_eval_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id              TEXT NOT NULL,
            user_id             TEXT NOT NULL,
            topic               TEXT NOT NULL,
            scope               TEXT DEFAULT 'topic',
            n_questions         INTEGER NOT NULL,
            k                   INTEGER NOT NULL,
            judge_mode          TEXT DEFAULT 'standard',
            eval_kind           TEXT DEFAULT 'synthetic_e2e',
            retrieval_mode      TEXT DEFAULT 'atomic_dense',
            dataset_id          TEXT DEFAULT '',
            dataset_version     TEXT DEFAULT '',
            dataset_hash        TEXT DEFAULT '',
            corpus_hash         TEXT DEFAULT '',
            seed                INTEGER,
            claim_token         TEXT,
            hit_at_k            REAL,
            hit_at_k_strict     REAL,
            mrr                 REAL,
            ndcg_at_k           REAL,
            context_precision   REAL,
            context_recall      REAL,
            faithfulness        REAL,
            answer_relevancy    REAL,
            answer_correctness  REAL,
            success_rate        REAL,
            latency_p50_ms      REAL,
            latency_p95_ms      REAL,
            status              TEXT NOT NULL,
            error               TEXT DEFAULT '',
            manifest_json       TEXT DEFAULT '{}',
            detail_json         TEXT DEFAULT '{}',
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_eval_runs_user ON rag_eval_runs(user_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_eval_runs_topic ON rag_eval_runs(user_id, topic, created_at DESC)")

    # Compatibility migration for databases created before the frozen benchmark
    # and run-manifest fields existed. Keep this in startup DDL because Docker
    # does not run backend/migrate.py automatically.
    rag_eval_columns = {
        "hit_at_k_strict": "REAL",
        "eval_kind": "TEXT DEFAULT 'synthetic_e2e'",
        "retrieval_mode": "TEXT DEFAULT 'atomic_dense'",
        "dataset_id": "TEXT DEFAULT ''",
        "dataset_version": "TEXT DEFAULT ''",
        "dataset_hash": "TEXT DEFAULT ''",
        "corpus_hash": "TEXT DEFAULT ''",
        "seed": "INTEGER",
        "claim_token": "TEXT",
        "ndcg_at_k": "REAL",
        "success_rate": "REAL",
        "latency_p50_ms": "REAL",
        "latency_p95_ms": "REAL",
        "manifest_json": "TEXT DEFAULT '{}'",
    }
    for col, ddl in rag_eval_columns.items():
        try:
            conn.execute(f"SELECT {col} FROM rag_eval_runs LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE rag_eval_runs ADD COLUMN {col} {ddl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rag_eval_comparable "
        "ON rag_eval_runs(user_id, topic, eval_kind, retrieval_mode, created_at DESC)"
    )
    # One durable job has exactly one terminal run. Older versions had no
    # uniqueness guard, so preserve any duplicate history under an explicitly
    # legacy job id while keeping the newest row addressable by the original id.
    duplicate_runs = conn.execute(
        "SELECT id, user_id, job_id FROM rag_eval_runs AS candidate "
        "WHERE EXISTS (SELECT 1 FROM rag_eval_runs AS newer "
        "WHERE newer.user_id = candidate.user_id "
        "AND newer.job_id = candidate.job_id AND newer.id > candidate.id) "
        "ORDER BY id"
    ).fetchall()
    if duplicate_runs:
        occupied_run_ids = {
            (str(row["user_id"]), str(row["job_id"]))
            for row in conn.execute("SELECT user_id, job_id FROM rag_eval_runs")
        }
        for row in duplicate_runs:
            user_id = str(row["user_id"])
            original_job_id = str(row["job_id"])
            migrated_job_id = f"{original_job_id}:legacy-duplicate:{row['id']}"
            while (user_id, migrated_job_id) in occupied_run_ids:
                migrated_job_id += ":migrated"
            conn.execute(
                "UPDATE rag_eval_runs SET job_id = ? WHERE id = ?",
                (migrated_job_id, row["id"]),
            )
            occupied_run_ids.add((user_id, migrated_job_id))
        logger.warning(
            "Migrated %d duplicate RAG eval job ids before adding uniqueness",
            len(duplicate_runs),
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_eval_runs_user_job "
        "ON rag_eval_runs(user_id, job_id)"
    )

    # Durable start-request identity for RAG evaluations. The benchmark task is
    # still process-local, but this mapping lets an HTTP retry recover the same
    # job id after the original response is lost or the process restarts.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_eval_start_requests (
            user_id          TEXT NOT NULL,
            idempotency_key  TEXT NOT NULL,
            request_hash     TEXT NOT NULL,
            job_id           TEXT NOT NULL,
            response_json    TEXT NOT NULL DEFAULT '{}',
            claim_token      TEXT NOT NULL,
            lease_expires_at TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            PRIMARY KEY (user_id, idempotency_key)
        )
    """)
    try:
        conn.execute("SELECT response_json FROM rag_eval_start_requests LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE rag_eval_start_requests "
            "ADD COLUMN response_json TEXT NOT NULL DEFAULT '{}'"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rag_eval_start_job "
        "ON rag_eval_start_requests(user_id, job_id)"
    )

    # ── knowledge_cards ──
    # Persisted knowledge-training flashcards + SM-2 spaced-repetition state. The
    # card id is the deterministic kt-hash (topic|title|source), so re-generating
    # an identical card is an INSERT OR IGNORE no-op (exact dedup). source_file /
    # source_header back the section-coverage dedup at generation time.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_cards (
            id              TEXT NOT NULL,
            user_id         TEXT NOT NULL,
            topic           TEXT NOT NULL,
            title           TEXT NOT NULL,
            knowledge       TEXT NOT NULL,
            example         TEXT DEFAULT '',
            question        TEXT DEFAULT '',
            answer          TEXT DEFAULT '',
            tags            TEXT DEFAULT '[]',
            source_refs     TEXT DEFAULT '[]',
            source_file     TEXT DEFAULT '',
            source_header   TEXT DEFAULT '',
            depth           TEXT DEFAULT 'understand',
            familiarity     TEXT DEFAULT '',
            sr_state        TEXT DEFAULT '{}',
            next_review     TEXT,
            review_count    INTEGER DEFAULT 0,
            last_reviewed_at TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_cards_topic ON knowledge_cards(user_id, topic, created_at DESC)")
    # Due-review queue lookup: cards whose next_review has elapsed, per (user, topic).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_cards_due ON knowledge_cards(user_id, topic, next_review)")

    # ── audit_logs ──
    # Security audit trail: auth events (login/register/password) and global
    # config mutations (AI channels / tuning). Append-only; queried by the
    # owner-only admin endpoints. `detail` is JSON and must never contain
    # secrets (API keys, passwords) — writers log names/counts, not values.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event      TEXT NOT NULL,
            user_id    TEXT,
            email      TEXT,
            ip         TEXT,
            detail     TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_logs(event, created_at DESC)")

    conn.commit()
    logger.info("All database tables and indexes initialized.")
