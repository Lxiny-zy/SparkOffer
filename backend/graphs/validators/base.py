"""Phase 4: structural validators for generated drill questions.

Each validator is **pure** and **deterministic** — no LLM calls. We use the
LLM only when something is broken enough that we need to regenerate, and
even then only for the specific questions that failed (selective repair).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ValidationContext:
    """Everything a validator might need to make its decision."""
    topic: str
    user_id: str
    target_difficulty_distribution: dict[int, int] | None = None   # e.g. {2:1,3:4,4:3,5:2}
    weak_points: list[str] = field(default_factory=list)
    recent_questions: list[str] = field(default_factory=list)
    expected_weak_point_coverage: float = 0.6  # 60% of slots should hit a WP


@dataclass
class ValidationResult:
    """Per-validator verdict.

    ``bad_ids`` is the list of question.id values that this validator marks
    as invalid. ``reasons`` maps id → human-readable explanation that goes
    straight into the repair prompt.
    """
    ok: bool
    bad_ids: list[int] = field(default_factory=list)
    reasons: dict[int, str] = field(default_factory=dict)
    summary: str = ""


class Validator(Protocol):
    name: str
    def validate(self, questions: list[dict], ctx: ValidationContext) -> ValidationResult: ...
