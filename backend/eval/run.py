"""Eval runner CLI.

Examples
--------
Smoke test (one persona × one strategy × one judge, no LLM-judge cost)::

    python -m backend.eval.run --smoke

Full run with deterministic judges only::

    python -m backend.eval.run --judges coverage,difficulty_kl,diversity

Full matrix including LLM judge::

    python -m backend.eval.run --persona all --strategy all \
        --judges coverage,difficulty_kl,diversity,llm_judge \
        --output-csv backend/eval/reports/full.csv

CSV schema (one row per (strategy × persona × judge))::

    strategy, persona, judge, score, detail
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
import time
from pathlib import Path

from backend.eval.judges import ALL_JUDGES
from backend.eval.strategies import ALL_STRATEGIES

logger = logging.getLogger("uvicorn")

PERSONAS_DIR = Path(__file__).parent / "personas"
REPORTS_DIR = Path(__file__).parent / "reports"


def _load_personas(selected: list[str]) -> list[dict]:
    """Load persona JSON files. ``selected`` can be ['all'] or list of ids."""
    all_files = sorted(PERSONAS_DIR.glob("*.json"))
    personas: list[dict] = []
    for f in all_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Skipping malformed persona %s: %s", f.name, e)
            continue
        pid = data.get("persona_id", f.stem)
        data.setdefault("persona_id", pid)
        if "all" in selected or pid in selected or f.stem in selected:
            personas.append(data)
    return personas


def _topic_for_persona(persona: dict) -> str:
    """Pick the topic this persona is built around.

    Priority: explicit ``preferences.focus_topics[0]`` → first key in
    ``topic_mastery`` → ``python`` (the safest universal default since
    knowledge_base/python exists in the repo).
    """
    focus = persona.get("preferences", {}).get("focus_topics", [])
    if focus:
        return focus[0]
    masteries = persona.get("topic_mastery", {})
    if masteries:
        return next(iter(masteries.keys()))
    return "python"


async def _run_cell(strategy_cls, persona: dict, topic: str, judges: list, n_q: int) -> list[dict]:
    """One (strategy, persona) cell — generate then run all judges. Returns list of result dicts."""
    strategy = strategy_cls()
    t0 = time.perf_counter()
    try:
        questions = await strategy.generate_questions(persona, topic, n_questions=n_q)
    except Exception as e:
        logger.exception("Strategy %s failed on persona %s: %s",
                         strategy.name, persona.get("persona_id"), e)
        return [
            {"strategy": strategy.name, "persona": persona.get("persona_id"),
             "judge": j.name, "score": 0.0, "detail": f"strategy_failed: {e}"}
            for j in judges
        ]
    gen_ms = (time.perf_counter() - t0) * 1000.0
    logger.info("[%s × %s] generated %d questions in %.0f ms",
                strategy.name, persona.get("persona_id"), len(questions), gen_ms)

    rows: list[dict] = []
    for judge in judges:
        jt0 = time.perf_counter()
        try:
            score, detail = await judge.evaluate(persona, questions)
        except Exception as e:
            logger.exception("Judge %s failed on (%s × %s): %s",
                             judge.name, strategy.name, persona.get("persona_id"), e)
            score, detail = 0.0, f"judge_failed: {str(e)[:120]}"
        jms = (time.perf_counter() - jt0) * 1000.0
        rows.append({
            "strategy": strategy.name,
            "persona": persona.get("persona_id"),
            "judge": judge.name,
            "score": round(score, 4),
            "detail": f"{detail} | judge_ms={jms:.0f}",
        })
    return rows


async def _main_async(args: argparse.Namespace) -> int:
    # Selection logic
    if args.smoke:
        # Force minimal config regardless of other flags
        persona_sel = ["junior_python"]
        strategy_sel = ["random_baseline"]
        judge_sel = ["coverage"]
        n_q = 3
    else:
        persona_sel = [p.strip() for p in args.persona.split(",") if p.strip()]
        strategy_sel = [s.strip() for s in args.strategy.split(",") if s.strip()]
        judge_sel = [j.strip() for j in args.judges.split(",") if j.strip()]
        if args.no_llm_judge:
            judge_sel = [j for j in judge_sel if j != "llm_judge"]
        n_q = args.n_questions

    personas = _load_personas(persona_sel)
    if not personas:
        print(f"ERROR: no personas matched {persona_sel}", file=sys.stderr)
        return 2

    if "all" in strategy_sel:
        strategy_classes = list(ALL_STRATEGIES.values())
    else:
        strategy_classes = [ALL_STRATEGIES[s] for s in strategy_sel if s in ALL_STRATEGIES]
    if not strategy_classes:
        print(f"ERROR: no strategies matched {strategy_sel}", file=sys.stderr)
        return 2

    judges = []
    for jn in judge_sel:
        if jn in ALL_JUDGES:
            judges.append(ALL_JUDGES[jn]())
        else:
            logger.warning("Unknown judge: %s", jn)
    if not judges:
        print(f"ERROR: no valid judges in {judge_sel}", file=sys.stderr)
        return 2

    print(f"Eval: personas={[p['persona_id'] for p in personas]} "
          f"strategies={[s.__name__ for s in strategy_classes]} "
          f"judges={[j.name for j in judges]} n_q={n_q}")
    print("-" * 70)

    all_rows: list[dict] = []
    for persona in personas:
        topic = _topic_for_persona(persona)
        for strat_cls in strategy_classes:
            rows = await _run_cell(strat_cls, persona, topic, judges, n_q)
            all_rows.extend(rows)
            for r in rows:
                print(f"  {r['strategy']:<18} {r['persona']:<22} "
                      f"{r['judge']:<14} score={r['score']:.3f}  {r['detail'][:70]}")
            print("-" * 70)

    # Write CSV
    out_path = Path(args.output_csv) if args.output_csv else REPORTS_DIR / f"eval_{int(time.time())}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["strategy", "persona", "judge", "score", "detail"])
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\nWrote {len(all_rows)} rows to {out_path}")

    # Print aggregate by strategy
    print("\nAggregate (mean score by strategy × judge):")
    agg: dict[tuple[str, str], list[float]] = {}
    for r in all_rows:
        agg.setdefault((r["strategy"], r["judge"]), []).append(r["score"])
    for (strat, judge), scores in sorted(agg.items()):
        mean = sum(scores) / len(scores) if scores else 0.0
        print(f"  {strat:<18} {judge:<14} mean={mean:.3f}  (n={len(scores)})")

    return 0


def main():
    p = argparse.ArgumentParser(prog="python -m backend.eval.run",
                                description="Personalization-regression eval harness")
    p.add_argument("--persona", default="all",
                   help="Comma list of persona_ids or 'all' (default: all)")
    p.add_argument("--strategy", default="all",
                   help=f"Comma list from {list(ALL_STRATEGIES.keys())} or 'all' (default: all)")
    p.add_argument("--judges", default="coverage,difficulty_kl,diversity",
                   help=f"Comma list from {list(ALL_JUDGES.keys())} (default: deterministic ones)")
    p.add_argument("--n-questions", type=int, default=10,
                   help="Number of questions per (strategy, persona) cell")
    p.add_argument("--no-llm-judge", action="store_true",
                   help="Drop llm_judge even if listed in --judges (saves tokens)")
    p.add_argument("--smoke", action="store_true",
                   help="Smoke test: one persona × one strategy × one judge × 3 questions, no LLM tokens")
    p.add_argument("--output-csv", default="",
                   help="CSV output path (default: backend/eval/reports/eval_<ts>.csv)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Bootstrap config + DB tables + channel manager — same calls ``main.py``
    # makes at startup. Without this the LLM judge and personalized strategy
    # can't find their channels and DB writes in finalize stage would crash.
    try:
        from backend.ai_config import init_config
        init_config()
    except Exception as e:
        logger.warning("ai_config.init_config failed (%s) — running with whatever .env provides", e)
    try:
        from backend.storage.database import init_all_tables
        init_all_tables()
    except Exception as e:
        logger.warning("init_all_tables failed: %s", e)

    try:
        exit_code = asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print("\nAborted", file=sys.stderr)
        exit_code = 130
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
