"""Question-quality judges.

Each judge returns a (score, detail) tuple where:
    - score ∈ [0.0, 1.0]: higher = better
    - detail: short str for the CSV column, used for debugging
"""
from backend.eval.judges.coverage import CoverageJudge
from backend.eval.judges.difficulty_kl import DifficultyKLJudge
from backend.eval.judges.diversity import DiversityJudge
from backend.eval.judges.llm_judge import LLMJudge

ALL_JUDGES: dict[str, type] = {
    "coverage": CoverageJudge,
    "difficulty_kl": DifficultyKLJudge,
    "diversity": DiversityJudge,
    "llm_judge": LLMJudge,
}

__all__ = ["ALL_JUDGES", "CoverageJudge", "DifficultyKLJudge",
           "DiversityJudge", "LLMJudge"]
