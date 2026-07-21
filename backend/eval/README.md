# Personalization-Regression Eval Harness

Goal: prove (or disprove) that the personalized drill-question pipeline beats
naive baselines on objective metrics. This is the "ruler" that future
optimization phases (RAG re-rank, slot strategy, anchor calibration) will be
measured against.

## Quick start

Smoke test (no LLM tokens, ~1s):

```bash
python -m backend.eval.run --smoke
```

Deterministic-only run (no LLM judge — fast + free):

```bash
python -m backend.eval.run --judges coverage,difficulty_kl,diversity
```

Full matrix including the LLM judge (slowest, costs tokens):

```bash
python -m backend.eval.run --persona all --strategy all \
    --judges coverage,difficulty_kl,diversity,llm_judge \
    --output-csv backend/eval/reports/full.csv
```

Output: a CSV under `backend/eval/reports/` with columns
`strategy, persona, judge, score, detail` plus an aggregate table printed to
stdout.

## Strategies

| name              | what it does                                                                  |
|-------------------|-------------------------------------------------------------------------------|
| `personalized`    | Materializes the persona into a fake user dir and runs the real `DrillPipeline` |
| `random_baseline` | Samples question-shaped lines from `data/knowledge/<topic>/*.md` + high_freq  |
| `topic_only`      | Asks the same LLM but with only the topic name (no profile / weak points)     |

The personalized strategy creates a temp user under `data/users/eval_<id>_<hex>/`
and cleans up after the run. It depends on at least one channel being
configured in `llm` section (see `backend/channel_manager.py`).

## Judges

| name            | type          | what it measures                                                                              | direction |
|-----------------|---------------|----------------------------------------------------------------------------------------------|-----------|
| `coverage`      | deterministic | % of persona weak_points hit by ≥1 question (keyword + embedding fallback ≥ 0.55 cosine)     | higher = better |
| `difficulty_kl` | deterministic | KL(produced difficulty dist \|\| target dist) — target depends on persona mastery (3 bands)  | higher = better |
| `diversity`     | deterministic | 1 - mean(pairwise question embedding cosine)                                                  | higher = better |
| `llm_judge`     | LLM           | 1-10 rating from up to 3 distinct LLM channels (different models), divided by 10, median vote | higher = better |

All judges are fail-soft: an embedding outage produces a low score with a
diagnostic `detail` string, never an exception that aborts the run.

### Expected ranges (rough hypothesis)

For a typical persona (`junior_python`, `mid_python`, `agent_focused`):

| judge          | personalized | topic_only | random_baseline |
|----------------|--------------|------------|------------------|
| coverage       | 0.70 – 0.95  | 0.10 – 0.30 | 0.05 – 0.20    |
| difficulty_kl  | 0.75 – 0.95  | 0.50 – 0.75 | 0.30 – 0.55    |
| diversity      | 0.45 – 0.65  | 0.40 – 0.60 | 0.50 – 0.70    |
| llm_judge      | 0.65 – 0.85  | 0.45 – 0.65 | 0.20 – 0.40    |

Notes:
- `random_baseline` may actually beat `personalized` on `diversity` — random
  sampling spans more sub-topics. That's fine: diversity is a "don't regress"
  metric, not a target.
- `cold_start` persona has no weak_points so `coverage` returns 1.0 for all
  strategies. The differentiator on cold_start is mostly `difficulty_kl`.

## Personas (5 fixtures)

| persona_id           | topic   | mastery | weak_points | notes                              |
|----------------------|---------|---------|-------------|--------------------------------------|
| `junior_python`      | python  | 25      | 5           | Beginner band — target easy/medium  |
| `mid_python`         | python  | 50      | 4           | Mid band — balanced difficulty mix  |
| `senior_distributed` | java    | 75      | 4           | Advanced — should skew hard         |
| `cold_start`         | (any)   | 0       | 0           | New user — pipeline must not crash  |
| `agent_focused`      | agent   | 60      | 5           | Tests agent-tagged weak point match |

Why 5? Two persona slots per mastery band (junior+mid in band 1, senior in
band 2, agent in mid-high) plus the cold-start edge case covers the matrix
the strategies are differentiated on. More personas wouldn't change the
qualitative conclusion but would 5x the LLM-judge cost.

## CLI flags

```
--persona       comma list of persona_ids or 'all' (default: all)
--strategy      comma list or 'all' (default: all)
--judges        comma list (default: coverage,difficulty_kl,diversity)
--n-questions   per cell (default: 10)
--no-llm-judge  drop llm_judge from --judges (token saver)
--smoke         override everything: 1×1×1×3, no LLM tokens
--output-csv    custom CSV path
```

## Open items

- **LLM judge cost**: ~3 channels × N persona × M strategy LLM calls per run.
  Use `--no-llm-judge` for fast iteration; only run with `llm_judge` before
  declaring a phase done.
- **Coverage embedding fallback**: requires a working embedding channel. On
  a fresh environment with no embedding configured, the keyword pass still
  runs and `coverage` will under-report (which is OK — it's directionally
  correct).
- **Topic per persona**: currently picked from `preferences.focus_topics[0]`
  then `topic_mastery` keys, then defaults to `python`. If you add a persona
  that should be evaluated on a topic not in its profile, edit
  `_topic_for_persona` in `run.py`.
- **No CI wiring yet**: Phase 6 plan only requires the harness. Wiring this
  into pytest / GH Actions is a follow-up — the CSV output is already
  diffable.

## RAG retrieval and end-to-end evaluation

The project deliberately keeps two RAG evaluation layers separate:

| layer | `eval_kind` | purpose | reproducibility and cost |
|-------|-------------|---------|--------------------------|
| Frozen retrieval regression | `frozen_retrieval` | Replays the versioned cases in `data/eval/rag_queries.json` and measures retrieval quality and latency | Deterministic case selection, no LLM judge, low cost; use this for before/after regression checks |
| Synthetic end-to-end evaluation | `synthetic_e2e` | Generates a golden set from the current corpus, retrieves context, generates answers, and applies RAGAS-style embedding/LLM judges | Covers answer quality, but generation and judging are not fully reproducible and consume embedding/LLM tokens |

The frozen benchmark has two retrieval modes:

- `atomic_dense` sends one question at a time through the dense retriever. It
  isolates embedding, vector-store, and top-K retrieval behavior.
- `production_replay` groups up to five evaluation questions into one query
  bundle and replays the production-shaped path: multi-query retrieval, RRF
  fusion, semantic deduplication, and the configured reranker. Use it as the
  primary release regression mode; use `atomic_dense` to localize a regression.

The RAG dashboard is not one undifferentiated "RAG detector". Its upper section
runs these offline benchmarks against a selected topic and ground-truth proxy;
its lower section visualizes metrics already emitted by real interview sessions.
The online section is a health/observability view without qrels. In particular,
its answer-side values score the user's submitted answer for topicality and
support from retrieved references; they are not model-generation quality.

The bundled frozen dataset currently covers `agent` (11 cases), `python` (9),
and `java` (9). A request above the available count is reduced to the actual
stable file-order prefix, and a topic with no frozen cases is rejected with
HTTP 422 before a background job is created. `seed` is retained in the manifest
for protocol compatibility but does not alter this stable frozen selection.

### Run the frozen benchmark in Docker

Start the services required by the configured vector backend first. The
default Compose configuration uses Qdrant and Redis:

```bash
docker compose up -d redis qdrant
```

The benchmark intentionally does not build an index on the evaluation request
path. Build or warm the selected user's topic index before running it; otherwise
the report records an explicit `index_not_ready` infrastructure failure.

Compose mounts the host `./data` directory over `/app/data`. The image therefore
keeps an immutable fallback copy of `rag_queries.json` under
`/app/backend/eval/data`; a host `data/eval/rag_queries.json` takes precedence
when present, and its content hash identifies which copy a run used.

Inject the source revision while building so every result manifest identifies
the exact image code:

```bash
APP_GIT_SHA=$(git rev-parse HEAD) docker compose build backend
```

If `APP_GIT_SHA` is not explicitly injected, the manifest records `unknown`
when the image has no `.git` directory from which to recover a revision. That
is a real reproducibility limitation: build and deploy pipelines should pass a
commit SHA rather than relying on the container to infer one.

PowerShell equivalent:

```powershell
$env:APP_GIT_SHA = git rev-parse HEAD
docker compose build backend
```

Run the single-query dense baseline:

```bash
docker compose run --rm backend python -m backend.eval.rag_benchmark \
  --user-id <USER_ID> \
  --topic python \
  --mode atomic_dense \
  --top-k 8 \
  --n-questions 50 \
  --seed 42
```

Run the production-shaped retrieval path:

```bash
docker compose run --rm backend python -m backend.eval.rag_benchmark \
  --user-id <USER_ID> \
  --topic python \
  --mode production_replay \
  --top-k 8 \
  --n-questions 50 \
  --seed 42
```

PowerShell form (change `atomic_dense` to `production_replay` for the other
mode):

```powershell
docker compose run --rm backend python -m backend.eval.rag_benchmark `
  --user-id <USER_ID> `
  --topic python `
  --mode atomic_dense `
  --top-k 8 `
  --n-questions 50 `
  --seed 42
```

`--no-persist` skips SQLite persistence but still writes the JSON report.
`--output-json <PATH>` selects a custom JSON path. The command exits with code
`3` when the job fails, the effective-measurement rate is below 95%, retrieval
degrades, or the corpus/index/provider state changes during the run. This is an
execution-validity gate, not yet a score-regression gate: CI must separately
compare a metric delta against an approved baseline and threshold.

### Run evaluation through the API

The backend exposes the same frozen benchmark as an asynchronous job:

```bash
curl -X POST http://localhost:9001/api/rag-eval/start \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{
    "topic": "python",
    "n_questions": 50,
    "k": 8,
    "eval_kind": "frozen_retrieval",
    "retrieval_mode": "production_replay",
    "seed": 42
  }'
```

The response contains a `job_id`. Poll progress and inspect persisted runs with:

```text
GET /api/rag-eval/status/{job_id}
GET /api/rag-eval/runs
GET /api/rag-eval/runs/{run_id}
```

For a synthetic end-to-end run, set `eval_kind` to `synthetic_e2e` and select
the judge mode explicitly:

```bash
curl -X POST http://localhost:9001/api/rag-eval/start \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{
    "topic": "python",
    "n_questions": 20,
    "k": 8,
    "eval_kind": "synthetic_e2e",
    "judge_mode": "standard",
    "seed": 42
  }'
```

Synthetic evaluation requires owner/admin permission. It generates questions
and answers and invokes embedding/LLM judges, so it can consume substantial
tokens and API quota. Reserve it for scheduled or release evaluation rather
than every commit.

The API permits at most four running/queued jobs per backend process and executes
one at a time. An exact duplicate in-flight request returns the existing
`job_id` with `reused=true`. These controls are process-local; they are not a
distributed queue or cross-replica quota.

### Frozen retrieval metric semantics

All quality metrics are macro averages over selected cases. Infrastructure
failures remain in the denominator with zero quality scores, so an outage cannot
silently improve a benchmark:

- **Hit@K**: `1` when at least one of the top-K chunks contains any case-specific
  `must_include_any` term (case-insensitive), otherwise `0`.
- **MRR**: reciprocal rank of the first matching chunk, `1 / first_rank`; `0`
  when no chunk matches.
- **nDCG@K**: position-discounted ranking quality of all matching chunks. Binary
  relevance is defined by `must_include_any`, and the observed DCG is normalized
  by the ideal ordering for the same number of relevant chunks.
- **Context precision**: number of matching chunks divided by the number of
  returned top-K chunks. It penalizes irrelevant context included with a hit.
- **Context recall**: fraction of the case's `expected_keywords` found in the
  concatenated top-K context. This is keyword coverage, not LLM-estimated recall.
- **Effective-measurement rate** (`success_rate`): fraction of cases with a
  measurable retrieval outcome (`ok`, `empty`, or `degraded`). The run is marked
  `valid=false` below `0.95`. The persisted field keeps its legacy name.
- **Fully healthy rate**: fraction with `ok` or `empty`. `degraded` is measurable
  and remains in quality denominators, but it cannot form a strict comparison
  baseline. `empty` means the retriever completed successfully and returned no
  chunk; it is a valid zero-quality observation, not an infrastructure error.
- **Latency p50/p95**: end-to-end latency per retrieval attempt. An attempt is
  one question for `atomic_dense` and one five-question bundle for
  `production_replay`; therefore latency must not be compared across modes as if
  the units were identical.

The report also groups frozen metrics by `difficulty` and query `type`, which is
important because a high aggregate score can hide failures on semantic-gap,
ambiguity, long-tail, or cross-chapter cases.

These labels are keyword proxies rather than exhaustive chunk qrels. Matching is
case-insensitive substring matching without token boundaries; context precision
divides by the number actually returned, not requested K; and nDCG's ideal DCG
uses the number of relevant chunks observed in the returned list. Treat the
numbers as stable regression signals for this dataset, not universal IR scores.

Synthetic runs additionally report answer-generation success, LLM-judge
observation rate, and metric-observation rate. Missing observations still
contribute zero to quality aggregates, but any missing generation/judge/metric
observation sets `comparable=false`, so provider outages are not confused with a
healthy low-quality model result.

### Results and reproducibility manifest

The CLI always writes a detailed JSON job report to
`data/eval/reports/rag_benchmark_<job_id>.json` unless `--output-json` is set.
By default it also stores the summary, manifest, per-question rows, bundle
traces, and candidates in `data/interviews.db`, table `rag_eval_runs`.
Compose mounts `./data:/app/data`, so both artifacts are directly available in
the host project's `data` directory. `--no-persist` disables only the SQLite
write.

Each run records a reproducibility manifest including the source SHA, exact case
IDs, dataset and live-corpus hashes, the persisted index source-manifest hash,
index revision, sanitized provider routing, vector/reranker/LLM settings,
chunking parameters, a run-frozen retrieval snapshot, prompt/protocol hashes,
package versions, seed, and metric-semantics version. Retrieval tuning is frozen
once per run; a settings edit affects the next run rather than later bundles in
the current run.

The backend derives `comparison_signature` from these dimensions. It snapshots
again after evaluation; a changed corpus/index/provider state produces
`state_stable=false`. Runtime execution is classified as `healthy`, `degraded`,
`infrastructure_failure`, `evaluation_degraded`, `question_failure`, or
`state_changed_during_run`. The dashboard groups only completed, >=95% measured,
stable, `healthy` runs with the same backend signature. Legacy rows without a
signature are explicitly non-comparable.

Only compare runs directly when all of these fields are identical:

- `dataset.hash` (matching `dataset.version` alone is insufficient)
- exact `dataset.case_ids`
- `corpus.hash`
- `index_revision`
- `metric_semantics_version`
- `retrieval_mode` and `k`
- retrieval tuning (`retrieval_config`)
- embedding backend and embedding target/model
- vector backend
- reranker model and enabled/disabled state
- chunk token limit and overlap
- prompt/protocol hash, package versions, and metric-semantics version

Also label and separate warm-cache from cold-cache runs, Qdrant from numpy runs,
different source/image SHAs, and different dependency versions. Differences in
any of these dimensions are experimental changes, not noise; do not interpret
their score delta as a pure algorithm regression.

Completed and failed terminal rows survive a container restart in SQLite, and
the status endpoint can reconstruct them. The active task, semaphore, queue,
progress heartbeat, and lock remain in process memory. A restart interrupts an
in-flight run; production multi-worker/replica deployment requires a durable job
table/queue and distributed lock before this module can provide run recovery.

The current index manifest records source files but not the embedding model that
originally built each vector. Qdrant loading checks vector dimension only, so a
same-dimension model switch can reuse semantically incompatible old vectors.
Until index metadata persists an embedding fingerprint and forces a rebuild on
change, treat that scenario as a known limit of `index_revision`.
