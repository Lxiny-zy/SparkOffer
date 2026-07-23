"""Slot-based question generation strategy.

Phase 2 replaces the legacy "3-band hard-coded text" approach with per-weak-point
slot allocation: each of the 10 questions is assigned to a specific weak_point
(or "exploration" if no weak_point is available), with its difficulty derived
from THAT weak_point's mastery — not the user's aggregate topic mastery.

Why: the legacy strategy treated a user with topic mastery=50 as a single
profile, even when their weak points spanned mastery 10 (haven't grasped GIL)
and 80 (almost mastered asyncio). Slot allocation lets the same training
session mix concept-level questions for the cold areas with system-design
questions for the warm ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random


# Slot allocation ratios — focus the most questions on the weakest points,
# but reserve some for consolidating mid-range knowledge and graduating the
# near-mastered ones.
SLOT_BUDGET = {
    "focus": 5,        # weakest 3 weak_points share these
    "consolidate": 3,  # mid-range weak_points
    "graduate": 2,     # near-mastered weak_points (sanity check)
}
TOTAL_SLOTS = sum(SLOT_BUDGET.values())  # 10

# Difficulty mapping from per-WP mastery: rough rule is "give them a question
# one step harder than their current proven level". clamp(round(m/20), 1, 5)
# yields: mastery 0→0→1, 20→1, 40→2, 60→3, 80→4, 100→5.
DIFF_JITTER = 1


@dataclass
class Slot:
    """A single question slot in the upcoming batch."""
    role: str                     # "focus" | "consolidate" | "graduate" | "exploration"
    weak_point: str | None        # None means "exploration of new ground"
    difficulty_target: int        # 1-5
    is_due_review: bool = False   # True if this weak_point is due for SR review


@dataclass
class StrategyPlan:
    """Output of slot allocation, consumed by drill_pipeline + the LLM prompt."""
    slots: list[Slot] = field(default_factory=list)
    avg_mastery: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)  # role → count

    def difficulty_distribution(self) -> dict[int, int]:
        dist: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for s in self.slots:
            dist[s.difficulty_target] = dist.get(s.difficulty_target, 0) + 1
        return dist


def _diff_from_mastery(mastery: int) -> int:
    """Map per-WP mastery (0-100) to a target difficulty (1-5)."""
    base = max(1, min(5, round(mastery / 20.0)))
    return base


def allocate_slots(
    weak_points: list[dict],
    due_points: set[str],
    seed: int | None = None,
) -> StrategyPlan:
    """Partition the 10-question budget across the user's weak_points.

    Args:
        weak_points: list of profile weak_point dicts, each with at minimum
            ``point: str`` and optionally ``mastery: int``, ``attempts: int``.
            Improved points should already be filtered out by the caller.
        due_points: set of weak_point text strings that are due for SR review;
            these are bumped to the head of the focus queue regardless of mastery.
        seed: RNG seed for difficulty jitter (deterministic for tests).

    Returns:
        StrategyPlan with ``len(slots) == TOTAL_SLOTS``.
    """
    rng = random.Random(seed)

    # Default missing mastery to 20 (consistent with init_sr_for_existing_points
    # default — keeps cold weak_points in the "focus" bucket).
    enriched = [
        {
            "point": wp.get("point", ""),
            "mastery": int(wp.get("mastery", 20)),
            "is_due": wp.get("point", "") in due_points,
        }
        for wp in weak_points
        if wp.get("point")
    ]

    # Sort: due first, then by ascending mastery (weakest first).
    enriched.sort(key=lambda w: (not w["is_due"], w["mastery"]))

    n = len(enriched)
    slots: list[Slot] = []

    # Allocate "focus" slots (weakest 3 WPs share 5 questions).
    focus_pool = enriched[:3] if n >= 3 else enriched[:]
    consolidate_pool = enriched[3:6] if n >= 6 else (enriched[3:] if n > 3 else [])
    graduate_pool = enriched[-2:] if n >= 5 else []

    def _emit(role: str, pool: list[dict], count: int) -> None:
        if not pool:
            # No weak_points to fill this role → fall back to "exploration"
            # of new ground; difficulty defaults to topic-appropriate mid-range.
            for _ in range(count):
                slots.append(Slot(role="exploration", weak_point=None,
                                  difficulty_target=2))
            return
        # Round-robin assign across the pool, then jitter difficulty ±1.
        for i in range(count):
            wp = pool[i % len(pool)]
            base = _diff_from_mastery(wp["mastery"])
            # graduate slots aim slightly above mastery to test the ceiling;
            # focus slots aim at mastery (don't overwhelm).
            if role == "graduate":
                base = min(5, base + 1)
            jitter = rng.choice([-DIFF_JITTER, 0, 0, DIFF_JITTER])
            diff = max(1, min(5, base + jitter))
            slots.append(Slot(role=role, weak_point=wp["point"],
                              difficulty_target=diff,
                              is_due_review=wp["is_due"]))

    _emit("focus", focus_pool, SLOT_BUDGET["focus"])
    _emit("consolidate", consolidate_pool, SLOT_BUDGET["consolidate"])
    _emit("graduate", graduate_pool, SLOT_BUDGET["graduate"])

    avg_mastery = (sum(w["mastery"] for w in enriched) / len(enriched)) if enriched else 0.0
    counts: dict[str, int] = {}
    for s in slots:
        counts[s.role] = counts.get(s.role, 0) + 1

    return StrategyPlan(slots=slots, avg_mastery=avg_mastery, counts=counts)


# ── Prompt rendering ──

ROLE_INSTRUCTION = {
    "focus": "聚焦薄弱点：题目必须直接考察这个 weak_point 的核心，**不要**绕开它考别的；如果用户连基础概念都未掌握，先考概念，再考应用。",
    "consolidate": "巩固中段：题目应该让用户在掌握的边缘地带做选择/取舍，考察从已知到未知的迁移能力。",
    "graduate": "毕业测试：题目应该比该 weak_point 当前难度高一档，验证用户是否真的能在压力下应用知识；如果连这题都拿下，可以把它从 weak_point 列表里毕业。",
    "exploration": "探索新主题：当前没有针对性的 weak_point，请围绕该 topic 的核心知识体系出题，覆盖用户从未接触过的细分领域。",
}


def render_strategy_block(plan: StrategyPlan) -> str:
    """Render the slot plan as plain text to inject into the question-gen prompt.

    Output is consumed verbatim as ``question_strategy`` in DRILL_QUESTION_GEN_PROMPT.
    """
    lines: list[str] = [
        "按以下「槽位计划」生成 10 道题（按顺序，q1=slot1, q2=slot2, ...）：",
        "",
    ]
    for i, slot in enumerate(plan.slots, start=1):
        tag = "[到期复习] " if slot.is_due_review else ""
        wp_desc = f'"{slot.weak_point}"' if slot.weak_point else "（无具体 weak_point，自由出题）"
        lines.append(
            f"- Slot {i} · 角色={slot.role} · 目标难度={slot.difficulty_target}/5 "
            f"· 针对 weak_point: {tag}{wp_desc}"
        )
    lines.append("")
    lines.append("各角色的出题原则：")
    used_roles = {s.role for s in plan.slots}
    for role in ["focus", "consolidate", "graduate", "exploration"]:
        if role in used_roles:
            lines.append(f"- {role}: {ROLE_INSTRUCTION[role]}")
    lines.append("")
    lines.append(
        "**硬性要求**：每道题的 difficulty 字段必须等于槽位计划里指定的目标难度（允许 ±1 偏差）；"
        "不要把所有题难度集中在同一档；不要重复考察同一 weak_point 超过 3 次。"
    )
    return "\n".join(lines)


def difficulty_range_for_plan(plan: StrategyPlan) -> tuple[int, int]:
    """Convenience helper: get (min, max) difficulty across all slots."""
    diffs = [s.difficulty_target for s in plan.slots] or [3]
    return max(1, min(diffs)), min(5, max(diffs))
