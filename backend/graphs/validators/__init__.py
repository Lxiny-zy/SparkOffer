"""Default validator suite for the drill question batch."""
from .base import ValidationContext, ValidationResult, Validator
from .difficulty_distribution import DifficultyDistributionValidator
from .semantic_duplicate import SemanticDuplicateValidator
from .weak_point_coverage import WeakPointCoverageValidator

# Order matters: cheap deterministic checks first, embedding-heavy checks last.
DEFAULT_VALIDATORS: list[Validator] = [
    DifficultyDistributionValidator(),
    WeakPointCoverageValidator(),
    SemanticDuplicateValidator(),
]


__all__ = [
    "ValidationContext",
    "ValidationResult",
    "Validator",
    "DifficultyDistributionValidator",
    "WeakPointCoverageValidator",
    "SemanticDuplicateValidator",
    "DEFAULT_VALIDATORS",
]
