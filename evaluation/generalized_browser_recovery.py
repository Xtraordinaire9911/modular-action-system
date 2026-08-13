"""Real-browser evidence for observation-driven, generalized recovery."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Sequence

from evaluation.open_web_playwright_fixture_runner import (
    BrowserSessionLike,
    SessionFactory,
    _apply_fixture_variant,
    _default_session_factory,
    _maybe_await,
    _read_fixture_oracle,
)
from evaluation.open_web_randomized_holdout import OpenWebFailureVariant, build_open_web_failure_variants
from src.adaptation.trace_ledger import TraceLedger
from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.perception.browser_obstruction import observe_browser_obstruction
from src.perception.dom_transducer import DomTransducer
from src.runtime.continuous_interaction_manager import Executor
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec
from src.runtime.live_observation import LiveRuntimeObservation

_GOAL_ID = "confirm_plan"
_GOAL_STATE = "oracle.expected_effect_satisfied == true"
_TARGET_SELECTOR = "#primary-action"


class _ObservedDomExecutor:
    """Execute only affordances present in the adapter's latest observation."""

    def __init__(self, adapter: "_GeneralizedRecoveryAdapter") -> None:
        self.adapter = adapter
        self.calls: list[dict[str, Any]] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        started = time.perf_counter()
        affordance_id = str(skill_call.params.get("affordance_id") or "")
        affordance = self.adapter.latest_affordances.get(affordance_id)
        if affordance is None:
            return _execution_result(
                skill_call,
                False,
                started,
                failure_reason="fresh_affordance_not_found",
                metadata={"affordance_id": affordance_id},
            )
        selector = str(affordance.locator.get("selector") or "")
        if not selector:
            return _execution_result(
                skill_call,
                False,
                started,
                failure_reason="observed_affordance_has_no_selector",
                metadata={"affordance_id": affordance_id},
            )
        action = str(skill_call.params.get("primitive_action") or affordance.action)
        value = skill_call.params.get("value")
        try:
            if action == "type":
                await _maybe_await(self.adapter.session.fill(selector, "" if value is None else str(value)))
            elif action == "click":
                await _maybe_await(self.adapter.session.click(selector))
            else:
                raise ValueError(f"unsupported observed DOM action: {action}")
        except Exception as exc:
            result = _execution_result(
                skill_call,
                False,
                started,
                failure_reason=f"{type(exc).__name__}: {exc}",
                metadata={
                    "affordance_id": affordance_id,
                    "target_selector": selector,
                    "observed_execution": True,
                },
            )
            self.calls.append(_call_payload(skill_call, affordance, result))
            return result
        result = _execution_result(
            skill_call,
            True,
            started,
            metadata={
                "affordance_id": affordance_id,
                "target_selector": selector,
                "observed_execution": True,
            },
        )
        self.calls.append(_call_payload(skill_call, affordance, result))
        return result


class _GeneralizedRecoveryAdapter:
    def __init__(
        self,
        *,
        session: BrowserSessionLike,
        variant: OpenWebFailureVariant,
        url: str,
        screenshot_dir: Path,
        capture_screenshots: bool,
    ) -> None:
        self.session = session
        self.variant = variant
        self.url = url
        self.screenshot_dir = screenshot_dir
        self.capture_screenshots = capture_screenshots
        self.latest_affordances: dict[str, Affordance] = {}
        self.executor = _ObservedDomExecutor(self)
        self.requests: list[ObservationRequest] = []
        self.oracles: list[dict[str, Any]] = []
        self.obstruction_observations: list[dict[str, Any]] = []
        self.screenshots: list[str] = []
        self._blocked_target: tuple[str, str] | None = None

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        _ = spec
        self._blocked_target = None
        await _maybe_await(self.session.open(self.url))
        await _apply_fixture_variant(
            self.session,
            self.variant.case,
            variant_id=self.variant.variant_id,
            split=self.variant.split,
            parameters=self.variant.parameters,
        )

    async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation:
        self.requests.append(request)
        oracle = await _read_fixture_oracle(self.session)
        self.oracles.append(dict(oracle))
        html = await _maybe_await(self.session.evaluate("() => document.documentElement.outerHTML"))
        page = DomTransducer().transduce(
            str(html or ""),
            page_id=self.variant.variant_id,
            url=self.url,
            captured_at_ms=int(time.time() * 1000),
        )
        affordances = [_bind_goal_affordance(affordance) for affordance in page.affordances]

        previous = request.previous_result
        if self._blocked_target is None and previous is not None and not previous.success:
            target_selector = str(previous.metadata.get("target_selector") or "")
            target_affordance_id = str(previous.metadata.get("affordance_id") or "")
            if target_selector and target_affordance_id:
                self._blocked_target = (target_affordance_id, target_selector)

        assertions = []
        if self._blocked_target is not None:
            target_affordance_id, target_selector = self._blocked_target
            obstruction = await observe_browser_obstruction(self.session, target_selector=target_selector)
            assertions.append(obstruction.assertion(timestamp_ms=int(time.time() * 1000)))
            affordances.extend(obstruction.recovery_affordances(target_affordance_id=target_affordance_id))
            self.obstruction_observations.append(
                {
                    "request_reason": request.reason,
                    "target_affordance_id": target_affordance_id,
                    "target_selector": target_selector,
                    "target_exists": obstruction.target_exists,
                    "blocked": obstruction.blocked,
                    "blocker": obstruction.blocker,
                    "controls": [asdict(control) for control in obstruction.controls],
                }
            )

        self.latest_affordances = {affordance.id: affordance for affordance in affordances}
        await self._capture(request)
        return LiveRuntimeObservation(
            observation=Observation(
                device_states={
                    "oracle": {
                        "expected_effect_satisfied": bool(oracle.get("primary_action_completed")),
                        "state": oracle,
                    }
                },
                assertions=assertions,
                accessibility_tree={
                    "page_state": {
                        "browser": {
                            "url": self.url,
                            "variant_id": self.variant.variant_id,
                            "split": self.variant.split,
                        }
                    }
                },
            ),
            affordances=affordances,
            provenance={"capture": "live_browser", "request_reason": request.reason},
            complete_affordance_snapshot=True,
            response_to_request_id=request.request_id,
            captured_at_ms=int(time.time() * 1000),
        )

    def executors(self) -> dict[str, Executor]:
        return {"dom": self.executor}

    async def _capture(self, request: ObservationRequest) -> None:
        if not self.capture_screenshots:
            return
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / f"{self.variant.variant_id}-{len(self.requests):02d}-{request.reason}.png"
        try:
            await _maybe_await(self.session.screenshot(str(path)))
        except Exception:
            return
        self.screenshots.append(str(path))


def _bind_goal_affordance(affordance: Affordance) -> Affordance:
    if affordance.locator.get("selector") != _TARGET_SELECTOR:
        return affordance
    locator = {
        **affordance.locator,
        "entity_id": "oracle",
        "completion_for": _GOAL_ID,
        "achieves": _GOAL_STATE,
        "stable_key": "goal:confirm-plan",
    }
    return replace(affordance, locator=locator)


def _execution_result(
    skill_call: SkillCall,
    success: bool,
    started: float,
    *,
    failure_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        skill_id=skill_call.skill_id,
        backend_used="dom",
        success=success,
        latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
        confidence=0.98 if success else 0.0,
        failure_reason=failure_reason,
        observation_source="dom",
        metadata=dict(metadata or {}),
    )


def _call_payload(skill_call: SkillCall, affordance: Affordance, result: ExecutionResult) -> dict[str, Any]:
    return {
        "skill_id": skill_call.skill_id,
        "primitive_action": skill_call.params.get("primitive_action"),
        "affordance_id": affordance.id,
        "label": affordance.label,
        "selector": affordance.locator.get("selector"),
        "success": result.success,
        "failure_reason": result.failure_reason or "",
    }


async def _run_generalized_browser_recovery_suite_async(
    output_dir: str | Path,
    *,
    variants: Sequence[OpenWebFailureVariant],
    headless: bool,
    action_timeout_ms: int,
    capture_screenshots: bool,
    session_factory: SessionFactory | None,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    transition_path = target / "transition_ledger.jsonl"
    failure_path = target / "failure_ledger.jsonl"
    for path in (transition_path, failure_path):
        if path.exists():
            path.unlink()
    ledger = TransitionLedger(transition_path)
    failure_ledger = TraceLedger()
    runner = RuntimeEpisodeRunner(
        episode_policy=EpisodePolicy(
            max_steps=8,
            deadline_s=20.0,
            max_retry_attempts=1,
            max_attempts_per_backend=8,
            require_fresh_observation=True,
        ),
        transition_ledger=ledger,
        failure_ledger=failure_ledger,
    )
    factory = session_factory or _default_session_factory
    rows: list[dict[str, Any]] = []
    for variant in variants:
        if variant.case.case_id != "openweb-overlay-obstruction":
            raise ValueError("generalized recovery suite currently evaluates obstruction variants only")
        fixture_path = (Path("env/mock_envs") / variant.case.html_fixture).resolve()
        url = fixture_path.as_uri()
        session = await _maybe_await(factory(url, headless=headless, action_timeout_ms=action_timeout_ms))
        adapter = _GeneralizedRecoveryAdapter(
            session=session,
            variant=variant,
            url=url,
            screenshot_dir=target / "screenshots",
            capture_screenshots=capture_screenshots,
        )
        try:
            outcome = await runner.run_goal_episode(
                adapter,
                RuntimeEpisodeSpec(
                    task_id=variant.variant_id,
                    data_source="generalized_browser_recovery",
                    goal_id=_GOAL_ID,
                    goal_state=_GOAL_STATE,
                ),
            )
            result = outcome.result
            transitions = ledger.for_episode(result.episode_id)
            rows.append(
                {
                    "variant": variant.to_dict(),
                    "runtime": {
                        "episode_id": result.episode_id,
                        "state": result.state.value,
                        "attempts": result.attempts,
                        "recovery_attempted": result.recovery_attempted,
                        "recovery_succeeded": result.recovery_succeeded,
                        "final_outcome_verified": result.final_outcome_verified,
                        "outcome": result.outcome.value,
                        "replan_count": result.replan_count,
                        "user_action_required": result.user_action_required,
                        "recovery_tier": result.recovery_tier,
                        "transition_ids": result.transition_ids,
                        "final_verification_transition_id": result.final_verification_transition_id,
                        "recovery_trace": result.recovery_trace,
                    },
                    "browser": {
                        "calls": adapter.executor.calls,
                        "observation_requests": [request.reason for request in adapter.requests],
                        "oracles": adapter.oracles,
                        "obstruction_observations": adapter.obstruction_observations,
                        "screenshots": adapter.screenshots,
                    },
                    "transitions": [asdict(transition) for transition in transitions],
                    "failures": [
                        asdict(event) for event in failure_ledger.events if event.episode_id == result.episode_id
                    ],
                }
            )
        finally:
            await _maybe_await(session.close())

    failure_ledger.write_jsonl(failure_path)
    report = {
        "data_source": "generalized_browser_recovery",
        "protocol": {
            "runtime_entrypoint": "RuntimeEpisodeRunner.run_goal_episode",
            "browser_execution": True,
            "canonical_cim_execution": True,
            "policy_inputs": [
                "fresh obstruction measurement",
                "observed remediation affordance relation",
                "safety and reversibility metadata",
                "fresh remediation postcondition",
                "fresh original-goal oracle",
            ],
            "policy_forbidden_inputs": [
                "fixture family",
                "case id",
                "known overlay selector",
                "known remediation selector",
                "product or button text",
            ],
            "claim_boundary": "controlled local-browser holdout evidence; not unrestricted open-web evidence",
        },
        "summary": {
            "episode_count": len(rows),
            "dev_count": sum(1 for row in rows if row["variant"]["split"] == "dev"),
            "holdout_count": sum(1 for row in rows if row["variant"]["split"] == "holdout"),
            "recovery_success_count": sum(1 for row in rows if row["runtime"]["recovery_succeeded"]),
            "final_verified_count": sum(1 for row in rows if row["runtime"]["final_outcome_verified"]),
            "all_recovered_and_verified": all(
                row["runtime"]["recovery_succeeded"] and row["runtime"]["final_outcome_verified"] for row in rows
            ),
        },
        "episodes": rows,
        "artifacts": {
            "transition_ledger": str(transition_path),
            "failure_ledger": str(failure_path),
        },
    }
    report_path = target / "generalized_browser_recovery_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "generalized_browser_recovery_report": str(report_path),
        "transition_ledger": str(transition_path),
        "failure_ledger": str(failure_path),
    }


def run_generalized_browser_recovery_suite(
    output_dir: str | Path,
    *,
    dev_repetitions: int = 3,
    holdout_repetitions: int = 3,
    headless: bool = True,
    action_timeout_ms: int = 500,
    capture_screenshots: bool = True,
    session_factory: SessionFactory | None = None,
) -> dict[str, str]:
    variants = [
        variant
        for split, repetitions in (("dev", dev_repetitions), ("holdout", holdout_repetitions))
        for variant in build_open_web_failure_variants(split, repetitions=repetitions)  # type: ignore[arg-type]
        if variant.case.case_id == "openweb-overlay-obstruction"
    ]
    return asyncio.run(
        _run_generalized_browser_recovery_suite_async(
            output_dir,
            variants=variants,
            headless=headless,
            action_timeout_ms=action_timeout_ms,
            capture_screenshots=capture_screenshots,
            session_factory=session_factory,
        )
    )


__all__ = ["run_generalized_browser_recovery_suite"]
