"""Run controlled open-web mock failures through the shared runtime envelope."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evaluation.metrics_aggregator import aggregate_metrics, dataset_from_runtime_results
from evaluation.open_web_mock_failure_suite import OpenWebMockFailureCase, build_open_web_mock_failure_suite
from src.adaptation.trace_ledger import TraceLedger
from src.contracts.types import Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec


class _MockExecutor:
    """Executor that reports successful DOM interaction for a mock fixture.

    The independent oracle observation supplied by the adapter decides whether
    the expected effect actually happened.  This mirrors open-web false-success
    cases where a click/submit completes at the executor layer but the page or
    backend did not reach the declared state.
    """

    def __init__(self, case: OpenWebMockFailureCase) -> None:
        self.case = case
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        self.calls.append(skill_call)
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="dom",
            success=True,
            latency_ms=1.0,
            confidence=0.9,
            raw_observation_delta={
                "oracle": {
                    "executor_reported_success": True,
                    "case_id": self.case.case_id,
                }
            },
            observation_source="dom",
            metadata={
                "case_id": self.case.case_id,
                "html_fixture": self.case.html_fixture,
                "failure_class": self.case.failure_class,
            },
        )


class _OpenWebMockRuntimeAdapter:
    def __init__(self, case: OpenWebMockFailureCase) -> None:
        self.case = case
        self.executor = _MockExecutor(case)
        self.requests: list[ObservationRequest] = []
        self.reset_specs: list[RuntimeEpisodeSpec] = []

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        self.reset_specs.append(spec)

    async def observe(self, request: ObservationRequest) -> Observation:
        self.requests.append(request)
        return Observation(
            device_states={
                "oracle": {
                    "expected_effect_satisfied": False,
                    "case_id": self.case.case_id,
                    "failure_class": self.case.failure_class,
                    "state": self.case.oracle_state,
                }
            },
            accessibility_tree={
                "page_state": {
                    "mock_fixture": {
                        "html_fixture": self.case.html_fixture,
                        "observable_symptom": self.case.observable_symptom,
                    }
                }
            },
        )

    def executors(self) -> dict[str, _MockExecutor]:
        return {"dom": self.executor}


def _skill_for_case(case: OpenWebMockFailureCase) -> SkillTuple:
    return SkillTuple(
        skill_id=f"open_web_mock::{case.case_id}",
        description=f"Exercise controlled mock failure: {case.failure_class}",
        parameters_schema={},
        preconditions=[],
        postconditions=[Condition("oracle.expected_effect_satisfied == true", case.expected_effect)],
        allowed_backends=["dom"],
        preferred_backends=["dom"],
        rollback=None,
        failure_modes={},
        timeout_ms=1000,
        safety_level="low",
        irreversible=False,
        idempotent=False,
    )


async def _run_open_web_mock_runtime_suite_async(
    output_dir: str | Path,
    *,
    seed_start: int = 9000,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    transition_path = target / "transition_ledger.jsonl"
    failure_path = target / "failure_ledger.jsonl"
    for path in (transition_path, failure_path):
        if path.exists():
            path.unlink()

    cases = build_open_web_mock_failure_suite(seed_start=seed_start)
    skill_library = {_skill_for_case(case).skill_id: _skill_for_case(case) for case in cases}
    transition_ledger = TransitionLedger(transition_path)
    failure_ledger = TraceLedger()
    runner = RuntimeEpisodeRunner(
        skill_library=skill_library,
        episode_policy=EpisodePolicy(max_steps=1, deadline_s=10.0, max_retry_attempts=0, max_attempts_per_backend=1),
        transition_ledger=transition_ledger,
        failure_ledger=failure_ledger,
    )

    rows: list[dict[str, Any]] = []
    results = []
    for case in cases:
        adapter = _OpenWebMockRuntimeAdapter(case)
        skill_id = f"open_web_mock::{case.case_id}"
        outcome = await runner.run_skill_episode(
            adapter,
            SkillCall(
                skill_id,
                {
                    "case_id": case.case_id,
                    "expected_effect": "oracle.expected_effect_satisfied == true",
                    "html_fixture": case.html_fixture,
                },
            ),
            RuntimeEpisodeSpec(
                task_id=case.case_id,
                data_source="open_web_mock_runtime_suite",
            ),
        )
        result = outcome.result
        results.append(result)
        case_transitions = transition_ledger.for_episode(result.episode_id)
        rows.append(
            {
                "case": asdict(case),
                "runtime": {
                    "episode_id": result.episode_id,
                    "state": result.state.value,
                    "attempts": result.attempts,
                    "executor_success": bool(result.execution_result and result.execution_result.success),
                    "final_outcome_verified": result.final_outcome_verified,
                    "recovery_attempted": result.recovery_attempted,
                    "recovery_succeeded": result.recovery_succeeded,
                    "failure_type": result.failure_type,
                    "failure_boundary": result.failure_boundary,
                    "reason": result.reason,
                    "transition_ids": result.transition_ids,
                    "observation_requests": [request.reason for request in adapter.requests],
                    "postcondition_passed": [
                        record.postcondition_passed for record in case_transitions
                    ],
                },
            }
        )

    failure_ledger.write_jsonl(failure_path)
    metrics = aggregate_metrics(
        dataset_from_runtime_results(results, transition_ledger),
        data_source="open_web_mock_runtime_suite",
        episode_ids=[result.episode_id for result in results],
    )
    report = {
        "data_source": "open_web_mock_runtime_suite",
        "protocol": {
            "runtime_entrypoint": "RuntimeEpisodeRunner.run_skill_episode",
            "controlled_mock_evidence": True,
            "real_open_web_evidence": False,
            "executor_model": "in_memory_mock_executor_reports_success",
            "oracle_source": "fixture_oracle_state_reobserved_after_action",
            "episode_policy": {
                "max_steps": 1,
                "max_retry_attempts": 0,
                "purpose": "false-success / expected-effect detection only",
            },
            "claim_boundary": "runtime envelope evidence for local mock cases; not browser-click or real open-web evidence",
        },
        "summary": {
            "case_count": len(cases),
            "runtime_episode_count": len(results),
            "executor_success_count": sum(
                1 for row in rows if row["runtime"]["executor_success"]
            ),
            "postcondition_failures_detected": sum(
                1
                for row in rows
                if row["runtime"]["postcondition_passed"]
                and row["runtime"]["postcondition_passed"][0] is False
            ),
            "final_success_count": sum(1 for row in rows if row["runtime"]["final_outcome_verified"]),
            "recovery_attempted_count": sum(1 for row in rows if row["runtime"]["recovery_attempted"]),
            "unique_episode_ids": len({result.episode_id for result in results}) == len(results),
            "transition_record_count": len(transition_ledger.records),
            "failure_record_count": len(failure_ledger.events),
        },
        "metrics": {
            "values": metrics.values,
            "metadata": metrics.metadata,
        },
        "cases": rows,
        "artifacts": {
            "transition_ledger": str(transition_path),
            "failure_ledger": str(failure_path),
        },
        "recommendation": "replace_in_memory_executor_with_playwright_fixture_adapter_then_probe_real_open_web",
    }
    report_path = target / "open_web_mock_runtime_episode_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "open_web_mock_runtime_episode_report": str(report_path),
        "transition_ledger": str(transition_path),
        "failure_ledger": str(failure_path),
    }


def run_open_web_mock_runtime_suite(
    output_dir: str | Path,
    *,
    seed_start: int = 9000,
) -> dict[str, str]:
    return asyncio.run(_run_open_web_mock_runtime_suite_async(output_dir, seed_start=seed_start))
