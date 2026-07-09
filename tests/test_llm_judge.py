import pytest

from src.adaptation.failure_boundary import FailureBoundary
from src.adaptation.llm_judge import LLMJudge, LLMJudgeInput, LLMJudgeOutputError


class FakeJudgeClient:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def complete_json(self, prompt: str):
        self.prompts.append(prompt)
        return self.payload


def _input() -> LLMJudgeInput:
    return LLMJudgeInput(
        task_id="prepare_room_A",
        skill_id="set_temperature",
        failure_reason="postcondition failed but executor reported success",
        selected_backend="dom",
        allowed_backends=["dom", "wot"],
        conflict_summaries=[],
        recovery_trace=[
            {"tier": 1, "policy": "retry", "selected": False, "reason": "postcondition mismatch"},
            {"tier": 2, "policy": "reroute", "selected": True, "backend": "wot"},
        ],
        history_summary={"same_failure_count": 1, "reroute_success_rate": 0.0},
    )


def test_llm_judge_accepts_valid_schema_but_never_auto_applies():
    client = FakeJudgeClient(
        {
            "boundary": "skill_spec_insufficient",
            "failure_type": "weak_postcondition",
            "confidence": 0.71,
            "evidence": ["executor success conflicts with postcondition evidence"],
            "immediate_action": "use_recovery_cascade",
            "long_term_action": "strengthen_postcondition",
            "safe_to_auto_apply": True,
            "needs_human_review": False,
        }
    )

    analysis = LLMJudge(client=client).judge(_input())

    assert analysis.boundary == FailureBoundary.SKILL_SPEC_INSUFFICIENT
    assert analysis.failure_type == "weak_postcondition"
    assert analysis.safe_to_auto_apply is False
    assert analysis.needs_human_review is True
    assert "schema_validated_llm_judge" in analysis.evidence
    assert "postcondition failed" in client.prompts[0]


def test_llm_judge_rejects_unknown_boundary_and_action_values():
    client = FakeJudgeClient(
        {
            "boundary": "silently_ignore_safety",
            "failure_type": "conflict",
            "confidence": 0.9,
            "evidence": [],
            "immediate_action": "bypass_conflict_gate",
            "long_term_action": "lower_safety_threshold",
            "safe_to_auto_apply": False,
            "needs_human_review": True,
        }
    )

    with pytest.raises(LLMJudgeOutputError):
        LLMJudge(client=client).judge(_input())


def test_llm_judge_reports_unavailable_without_env_or_client(monkeypatch, tmp_path):
    for key in ["LLM_API_KEY", "LLM_MODEL_ID", "LLM_BASE_URL"]:
        monkeypatch.delenv(key, raising=False)

    judge = LLMJudge(env_path=tmp_path / "missing.env")

    assert judge.is_available() is False
