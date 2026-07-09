"""Optional LLM-assisted failure judgment.

The judge is intentionally advisory. It can help explain ambiguous failures,
but deterministic recovery, safety gates, and release gates remain authoritative.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.adaptation.failure_boundary import FailureAnalysis, FailureBoundary

ALLOWED_IMMEDIATE_ACTIONS = {
    "use_recovery_cascade",
    "retry_or_reroute",
    "fail_safely_and_escalate",
    "block_or_human_approval",
    "keep_using_recovery_cascade",
    "record_trace_only",
    "ask_user",
    "escalate_human",
    "abort_current_step",
}

ALLOWED_LONG_TERM_ACTIONS = {
    "record_trace_only",
    "collect_more_evidence",
    "strengthen_postcondition",
    "candidate_skill_or_spec_review",
    "architecture_or_environment_review",
    "propose_policy_update",
    "do_not_auto_learn",
    "new_regression_fixture",
    "human_review",
}

FORBIDDEN_ACTION_FRAGMENTS = {
    "bypass",
    "ignore_conflict",
    "lower_safety",
    "weaken_safety",
    "remove_human",
    "skip_postcondition",
    "auto_apply",
}


class LLMJudgeOutputError(ValueError):
    """Raised when an LLM response fails schema or safety validation."""


class LLMJudgeUnavailable(RuntimeError):
    """Raised when a live LLM judge is requested but no client/env is available."""


class LLMJudgeClient(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        """Return a parsed JSON object from a model completion."""


@dataclass(frozen=True)
class LLMJudgeInput:
    task_id: str
    skill_id: str
    failure_reason: str
    selected_backend: str
    allowed_backends: list[str] = field(default_factory=list)
    conflict_summaries: list[dict[str, Any]] = field(default_factory=list)
    recovery_trace: list[dict[str, Any]] = field(default_factory=list)
    history_summary: dict[str, Any] = field(default_factory=dict)


class OpenAICompatibleJudgeClient:
    """Small OpenAI-compatible JSON client.

    This supports OpenAI and many local/proxy endpoints that expose the same
    chat completions API. It is deliberately thin so tests can use a fake client.
    """

    def __init__(self, *, api_key: str, model: str, base_url: str | None = None) -> None:
        from openai import OpenAI

        if base_url:
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete_json(self, prompt: str) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an advisory failure-boundary judge for a bounded runtime action system. "
                        "Return only JSON matching the requested schema. Never propose weakening safety."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise LLMJudgeOutputError("LLM response must be a JSON object")
        return parsed


class LLMJudge:
    def __init__(self, client: LLMJudgeClient | None = None, *, env_path: Path | str = ".env") -> None:
        self._client = client
        self._env_path = Path(env_path)

    def is_available(self) -> bool:
        if self._client is not None:
            return True
        return bool(self._env_value("LLM_API_KEY") and self._env_value("LLM_MODEL_ID"))

    def judge(self, judge_input: LLMJudgeInput) -> FailureAnalysis:
        client = self._client or self._build_env_client()
        payload = client.complete_json(_build_prompt(judge_input))
        return _payload_to_analysis(payload)

    def _build_env_client(self) -> LLMJudgeClient:
        api_key = self._env_value("LLM_API_KEY")
        model = self._env_value("LLM_MODEL_ID")
        base_url = self._env_value("LLM_BASE_URL")
        if not api_key or not model:
            raise LLMJudgeUnavailable("LLM_API_KEY and LLM_MODEL_ID are required for live LLM judge calls")
        return OpenAICompatibleJudgeClient(api_key=api_key, model=model, base_url=base_url)

    def _env_value(self, key: str) -> str:
        if key in os.environ:
            return os.environ[key]
        if not self._env_path.exists():
            return ""
        for line in self._env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
        return ""


def _build_prompt(judge_input: LLMJudgeInput) -> str:
    schema = {
        "boundary": [boundary.value for boundary in FailureBoundary],
        "failure_type": "short_snake_case_string",
        "confidence": "number between 0 and 1",
        "evidence": ["short evidence strings grounded in the input"],
        "immediate_action": sorted(ALLOWED_IMMEDIATE_ACTIONS),
        "long_term_action": sorted(ALLOWED_LONG_TERM_ACTIONS),
        "safe_to_auto_apply": False,
        "needs_human_review": True,
    }
    return (
        "Classify this runtime failure. Use deterministic/safety boundaries conservatively. "
        "Do not infer upstream user intent and do not propose direct policy application.\n\n"
        f"Input:\n{json.dumps(asdict(judge_input), ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        f"Return JSON schema:\n{json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def _payload_to_analysis(payload: dict[str, Any]) -> FailureAnalysis:
    boundary = _parse_boundary(payload.get("boundary"))
    failure_type = _require_string(payload, "failure_type")
    immediate_action = _require_string(payload, "immediate_action")
    long_term_action = _require_string(payload, "long_term_action")
    _validate_action("immediate_action", immediate_action, ALLOWED_IMMEDIATE_ACTIONS)
    _validate_action("long_term_action", long_term_action, ALLOWED_LONG_TERM_ACTIONS)
    confidence = _parse_confidence(payload.get("confidence"))
    evidence = _parse_evidence(payload.get("evidence"))

    return FailureAnalysis(
        boundary=boundary,
        failure_type=failure_type,
        evidence=evidence + ["schema_validated_llm_judge"],
        immediate_action=immediate_action,
        long_term_action=long_term_action,
        confidence=confidence,
        safe_to_auto_apply=False,
        needs_human_review=True,
    )


def _parse_boundary(value: Any) -> FailureBoundary:
    if not isinstance(value, str):
        raise LLMJudgeOutputError("boundary must be a string")
    try:
        return FailureBoundary(value)
    except ValueError as exc:
        raise LLMJudgeOutputError(f"unknown boundary: {value}") from exc


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LLMJudgeOutputError(f"{key} must be a non-empty string")
    return value.strip()


def _parse_confidence(value: Any) -> float:
    if not isinstance(value, int | float):
        raise LLMJudgeOutputError("confidence must be numeric")
    if not 0.0 <= float(value) <= 1.0:
        raise LLMJudgeOutputError("confidence must be between 0 and 1")
    return float(value)


def _parse_evidence(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise LLMJudgeOutputError("evidence must be a list")
    evidence = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise LLMJudgeOutputError("each evidence item must be a non-empty string")
        evidence.append(item.strip())
    return evidence


def _validate_action(field: str, action: str, allowed: set[str]) -> None:
    normalized = action.lower()
    if action not in allowed:
        raise LLMJudgeOutputError(f"{field} has unsupported action: {action}")
    if any(fragment in normalized for fragment in FORBIDDEN_ACTION_FRAGMENTS):
        raise LLMJudgeOutputError(f"{field} contains forbidden safety-weakening action: {action}")
