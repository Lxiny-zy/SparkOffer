import asyncio
import time

from backend.graphs import drill_pipeline
from backend.graphs.drill_pipeline import DrillPipeline
from backend.graphs.validators import ValidationResult


def _pipeline() -> DrillPipeline:
    pipeline = DrillPipeline(topic="python", user_id="user-1")
    pipeline.questions = [
        {"id": 1, "question": "Explain the GIL", "difficulty": 3},
    ]
    pipeline.ctx = {
        "all_weak": ["GIL"],
        "drill_ctx": {"recent_questions": []},
        "plan": None,
    }
    return pipeline


def test_validation_runs_off_loop_and_emits_heartbeat(monkeypatch):
    class SlowValidator:
        name = "slow"

        def validate(self, _questions, _ctx):
            time.sleep(0.03)
            return ValidationResult(ok=True, summary="ok")

    monkeypatch.setattr(drill_pipeline, "DEFAULT_VALIDATORS", [SlowValidator()])
    monkeypatch.setattr(drill_pipeline, "PIPELINE_HEARTBEAT_SECONDS", 0.01)
    pipeline = _pipeline()

    async def collect():
        return [event async for event in pipeline._stage_validate()]

    events = asyncio.run(collect())

    assert any('"type": "ping"' in event for event in events)
    assert pipeline.ctx["validator_summary"] == ["slow=ok(ok)"]


def test_repair_wait_emits_heartbeat_and_updates_question(monkeypatch):
    class FailedValidator:
        name = "failed"

        def validate(self, _questions, _ctx):
            return ValidationResult(
                ok=False,
                bad_ids=[1],
                reasons={1: "repair it"},
                summary="failed",
            )

    async def slow_repair(_bad_ids, _reasons):
        await asyncio.sleep(0.03)
        return [{"id": 1, "question": "Explain GIL trade-offs", "difficulty": 4}]

    monkeypatch.setattr(drill_pipeline, "DEFAULT_VALIDATORS", [FailedValidator()])
    monkeypatch.setattr(drill_pipeline, "PIPELINE_HEARTBEAT_SECONDS", 0.01)
    pipeline = _pipeline()
    monkeypatch.setattr(pipeline, "_repair_partial", slow_repair)

    async def collect():
        return [event async for event in pipeline._stage_validate()]

    events = asyncio.run(collect())

    assert any('"type": "ping"' in event for event in events)
    assert any('"type": "question_update"' in event for event in events)
    assert pipeline.questions[0]["question"] == "Explain GIL trade-offs"
    assert pipeline.ctx["repair_outcome"] == "repaired 1/1"


def test_repair_timeout_keeps_original_batch(monkeypatch):
    class FailedValidator:
        name = "failed"

        def validate(self, _questions, _ctx):
            return ValidationResult(
                ok=False,
                bad_ids=[1],
                reasons={1: "repair it"},
                summary="failed",
            )

    async def stuck_repair(_bad_ids, _reasons):
        await asyncio.Event().wait()

    monkeypatch.setattr(drill_pipeline, "DEFAULT_VALIDATORS", [FailedValidator()])
    monkeypatch.setattr(drill_pipeline, "PIPELINE_HEARTBEAT_SECONDS", 0.005)
    monkeypatch.setattr(drill_pipeline, "REPAIR_TIMEOUT_SECONDS", 0.02)
    pipeline = _pipeline()
    original = list(pipeline.questions)
    monkeypatch.setattr(pipeline, "_repair_partial", stuck_repair)

    async def collect():
        return [event async for event in pipeline._stage_validate()]

    asyncio.run(collect())

    assert pipeline.questions == original
    assert pipeline.ctx["repair_outcome"].startswith("failed: operation exceeded")
