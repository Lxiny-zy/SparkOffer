# Changelog

## [Unreleased]

### Added / Changed / Fixed

- Added validation/repair SSE heartbeats and bounded timeouts, explicit LLM `extra_body` forwarding, deterministic audit ordering, early-close detection for interview streams, and refreshed project documentation.
- Added durable Resume interview reply recovery across SSE/network failures, including pending-turn replay, refresh restoration, evaluation fencing, empty-response rejection, and reverse-QA completion fixes.
- Hardened production authentication and Docker defaults, trusted-proxy handling, bounded/atomic rate limiting, deleted-user token checks, and transactional default-user ID migration.
- Extended default-user migration to stage, verify, roll back, and finalize Qdrant memory/knowledge data without exposing reserved resume or escaped Topic namespaces.
- Made legacy Qdrant cleanup restart-safe with durable retry markers, preserved memory collections outside knowledge-prefix cleanup, migrated configured Qdrant data even from numpy mode, and retained Qdrant 1.12 collection settings.
- Refused production startup when an existing owner still uses the public `legend` password, while keeping bootstrap credential validation separate from stored-account verification.
- Expanded new user and session identifiers to 128-bit values while retaining legacy 8-hex user compatibility; hardened right-to-left proxy-chain parsing and production network validation.
- Added Pydantic request models and bounds across JSON endpoints so malformed bodies, pagination values, export batches, scores, and settings return explicit 4xx responses instead of server errors.
- Made interview evaluation and side-effect synchronization token-claimed, retryable, step-aware, and mutually exclusive across automatic and manual completion paths.
- Kept long-running Resume turn leases alive across transient SQLite contention, while stopping promptly on explicit token loss or cancellation.
- Made online RAG metrics one-row-per-user/session/stage with startup deduplication, atomic retries, and evaluation-token fencing on both insert and update.
- Added atomic profile update transactions with rollback and cross-process email-conflict fencing for auth/profile changes.
- Serialized the full Resume chat/end lifecycle with database turn leases, heartbeats, token-fenced transcript commits, shielded disconnect recovery, and durable completion profile steps before review publication.
- Added target-level interview idempotency, post-claim snapshot rereads, frozen JD/Resume knowledge targets, deleted-topic skip markers, and stale-generation completion fencing.
- Added strict durable vector validation and compensation/replay paths so incomplete embeddings cannot be persisted as authoritative memory.
- Added optimistic knowledge-file versions with shared sidecar locks, streaming hashes, and conflict-safe PUT/DELETE operations.
- Isolated RAG frontend state by authenticated user and fenced durable RAG evaluation leases, terminal results, and metrics writes against stale workers.
- Serialized Assistant, QA Arena, and Algorithm session turns; moved blocking knowledge retrieval off the event loop and fixed Assistant storage response contracts.
- Made resume and knowledge uploads transactional, including UTF-8/PDF validation, strict index invalidation, full rollback, topic source deletion, and bounded inline document reads.
- Isolated resume vectors in an internal namespace and serialized topic build, incremental insert, invalidation, manifest persistence, and topic metadata transactions with re-entrant locks.
- Hardened the embedding queue against dirty rebuild loss, stop/restart thread overlap, generic callable argument injection, corrupt manifests, and HALF_OPEN probe lease leaks.
- Fixed graph module imports, isolated embedding caches by user/topic/model fingerprint, and bounded graph construction to prevent stale mixed-model vectors and quadratic growth.
- Added section-scoped AI channel updates, strict channel validation, token-fenced HALF_OPEN probes, real reranker channel failover, credential-safe provider logging, valid zero-value preservation, legacy backend normalization, and explicit embedding-model synchronization.
- Fixed frontend stale-response races and polling state recovery in Knowledge and RAG evaluation flows, including bounded backoff and correct all-topic history restoration.
- Isolated QA session loads, CRUD, streams, summaries, ingestion, and image object URLs by request generation; added explicit load failures/retries and synchronous duplicate-action guards.
- Made QA knowledge ingestion durably idempotent across disconnects, restarts, and workers with token leases, persisted write plans, fenced session deletion, atomic cross-process file updates, and index-repair compensation.
- Made RAG job restoration and cleanup job-specific across tabs, preserved transient poll failures, and prevented stale or unmounted start responses from replacing the active job.
- Made RAG evaluation starts durably idempotent with fixed job mappings, request-conflict detection, renewable execution leases, terminal replay from SQLite, ghost recovery, and frontend-owned retry tokens.
- Refreshed frontend transitive dependencies within existing version ranges to clear known lodash-es, picomatch, PostCSS, React Router, and Vite advisories.
- Normalized requirements-file comments to ASCII so pip can parse production and development dependencies on Windows systems using a non-UTF-8 locale.
- Restricted Nginx's 512 MiB streaming allowance to the authenticated knowledge upload route shape while retaining a 40 MiB cap for JSON APIs.
- Hardened the production Compose boundary with loopback-only HTTP ingress, pinned Qdrant defaults, authenticated internal data services, gateway-aware trusted proxies, and backend mount masking for Redis/Qdrant persistence; documented mandatory TLS and legacy-owner password verification.
- Added regression coverage for API validation, security, migrations, synchronization claims, identifier compatibility, session concurrency, upload rollback, graph caching, topic transactions, index locking/fingerprints, rebuild queueing, and settings contracts.
- Added a Docker-aware RAG evaluation suite with deterministic frozen retrieval regression, production-shaped replay, synthetic end-to-end judging, reproducibility manifests, comparison signatures, persisted run details, and bounded background jobs.
- Added RAG evaluation APIs and dashboard workflows for starting jobs, restoring terminal jobs after restart, filtering history, and expanding per-question diagnostics.
- Hardened retrieval evaluation status handling, stable chunk identity, frozen configuration snapshots, failure accounting, reranker cache isolation, and Docker dataset fallback behavior.
- Updated the Docker deployment and project analysis documentation to describe the two dashboard layers, metric formulas, execution profiles, reproducibility limits, and remaining production boundaries.
