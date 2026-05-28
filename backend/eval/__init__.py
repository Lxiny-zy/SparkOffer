"""Personalization-regression eval harness.

Goal: prove (or disprove) that the personalized drill-question pipeline
produces objectively better questions than baseline strategies. Output a
CSV report that can be diffed across runs and later wired into CI.

Layout:
    personas/    fixture user profiles (JSON, mirrors backend.memory schema)
    strategies/  question producers — personalized (real DrillPipeline) vs baselines
    judges/      deterministic + LLM judges scoring 0-1 per (strategy, persona, judge)
    run.py       CLI entry; writes CSV to reports/
"""
