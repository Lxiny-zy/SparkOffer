# Seed pool

Per-topic curated questions used by the **Phase 7c hybrid generation strategy**.
File format: one JSON object per line (JSONL) at `data/seed_pool/{topic}.jsonl`.

Each line:

```json
{"id": "py-gil-01", "question": "Python 的 GIL 在 I/O 阻塞时会释放吗？为什么这与多线程加速 IO 密集场景的常见说法不冲突？", "weak_points": ["GIL 释放时机"], "difficulty": 3, "category": "core_concept", "pillar": "python"}
```

## Why this exists

When this directory is populated, `backend/graphs/seed_pool.py` returns up to
6 questions per drill that target the user's active weak_points. The LLM then
only needs to generate the remaining 4 questions, halving prompt size and
cutting generation cost ~50%. Difficulty/category metadata also feeds the
Phase 5B anchor calibration as a sanity check.

## Adding seeds

There is **no script** that auto-generates this content — quality matters more
than quantity. Curate manually or via review of existing high-frequency
question logs. Recommended size: 30-100 questions per topic. Topics without
files trigger the all-LLM legacy path (no error).

## Loading behavior

- File is read on first request per topic, then cached in process memory.
- Restart the backend after editing a seed file.
- Empty / malformed lines are silently skipped (with a warning log).
