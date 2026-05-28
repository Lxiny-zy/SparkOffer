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
