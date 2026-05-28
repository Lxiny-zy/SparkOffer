"""Question-generation strategies under evaluation.

Each strategy takes a persona dict + topic and returns up to ``n_questions``
question dicts with at least the keys ``id``, ``question``, ``difficulty``.
"""
from backend.eval.strategies.base import Strategy
from backend.eval.strategies.personalized import PersonalizedStrategy
from backend.eval.strategies.random_baseline import RandomBaselineStrategy
from backend.eval.strategies.topic_only import TopicOnlyStrategy

ALL_STRATEGIES: dict[str, type[Strategy]] = {
    "personalized": PersonalizedStrategy,
    "random_baseline": RandomBaselineStrategy,
    "topic_only": TopicOnlyStrategy,
}

__all__ = ["Strategy", "ALL_STRATEGIES",
           "PersonalizedStrategy", "RandomBaselineStrategy", "TopicOnlyStrategy"]
