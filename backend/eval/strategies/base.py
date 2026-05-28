"""Abstract base for question-generation strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    async def generate_questions(
        self, persona: dict, topic: str, n_questions: int = 10,
    ) -> list[dict]:
        """Return up to ``n_questions`` question dicts.

        Each dict must contain at least:
            - ``id``: int
            - ``question``: str
            - ``difficulty``: int in [1, 5]

        Optional keys (preserved through the pipeline):
            - ``weak_point``: str — which persona weak_point this targets
            - ``category``: str — concept / scenario / design / ...
        """
        raise NotImplementedError
