"""Run open-web mock fixtures through a real browser and the runtime envelope."""

from __future__ import annotations

import asyncio
import json
import inspect
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from evaluation.metrics_aggregator import aggregate_metrics, dataset_from_runtime_results
from evaluation.open_web_mock_failure_suite import OpenWebMockFailureCase, build_open_web_mock_failure_suite
from src.adaptation.trace_ledger import TraceLedger
from src.contracts.types import Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec


class BrowserSessionLike(Protocol):
    def open(self, url: str) -> Any: ...
    def click(self, selector: str) -> Any: ...
    def fill(self, selector: str, value: str) -> Any: ...
    def evaluate(self, expression: str, arg: Any | None = None) -> Any: ...
    def screenshot(self, path: str | None = None) -> bytes: ...
    def close(self) -> Any: ...


SessionFactory = Callable[..., BrowserSessionLike | Any]


_CASE_ACTIONS: dict[str, list[dict[str, str]]] = {
    "openweb-overlay-obstruction": [{"action": "click", "selector": "#primary-action"}],
    "openweb-session-expiry": [{"action": "click", "selector": "#save-profile"}],
    "openweb-autocomplete-validation": [
        {"action": "fill", "selector": "#city", "value": "New York"},
        {"action": "click", "selector": "#submit-city"},
    ],
    "openweb-optimistic-rollback": [{"action": "click", "selector": "#place-order"}],
    "openweb-dom-visual-disagreement": [{"action": "click", "selector": "#choose-premium"}],
    "openweb-visible-ineffective-affordance": [{"action": "click", "selector": "#notification-toggle"}],
}


class _PlaywrightFixtureExecutor:
    def __init__(self, session: BrowserSessionLike, case: OpenWebMockFailureCase) -> None:
        self.session = session
        self.case = case
        self.calls: list[SkillCall] = []
        self.action_log: list[dict[str, Any]] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        self.calls.append(skill_call)
        started = asyncio.get_running_loop().time()
        try:
            for step in _CASE_ACTIONS[self.case.case_id]:
                action = step["action"]
                selector = step["selector"]
                if action == "fill":
                    value = step.get("value", "")
                    await _maybe_await(self.session.fill(selector, value))
                    self.action_log.append({"action": "fill", "selector": selector, "value": value})
                elif action == "click":
                    await _maybe_await(self.session.click(selector))
                    self.action_log.append({"action": "click", "selector": selector})
                else:
                    raise ValueError(f"unsupported fixture action: {action}")
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=True,
                latency_ms=round((asyncio.get_running_loop().time() - started) * 1000.0, 3),
                confidence=0.95,
                raw_observation_delta={
                    "browser": {
                        "actions_executed": list(self.action_log),
                        "case_id": self.case.case_id,
                    }
                },
                observation_source="dom",
                metadata={"html_fixture": self.case.html_fixture, "browser_execution": True},
            )
        except Exception as exc:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=False,
                latency_ms=round((asyncio.get_running_loop().time() - started) * 1000.0, 3),
                confidence=0.0,
                failure_reason=f"{type(exc).__name__}: {exc}",
                metadata={"html_fixture": self.case.html_fixture, "browser_execution": True},
            )


class _PlaywrightFixtureRuntimeAdapter:
    def __init__(
        self,
        *,
        session: BrowserSessionLike,
        case: OpenWebMockFailureCase,
        url: str,
        screenshot_dir: Path,
        capture_screenshots: bool,
    ) -> None:
        self.session = session
        self.case = case
        self.url = url
        self.executor = _PlaywrightFixtureExecutor(session, case)
        self.screenshot_dir = screenshot_dir
        self.capture_screenshots = capture_screenshots
        self.requests: list[ObservationRequest] = []
        self.reset_specs: list[RuntimeEpisodeSpec] = []
        self.screenshots: list[str] = []

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        self.reset_specs.append(spec)
        await _maybe_await(self.session.open(self.url))

    async def observe(self, request: ObservationRequest) -> Observation:
        self.requests.append(request)
        oracle = await _read_fixture_oracle(self.session)
        if self.capture_screenshots:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = self.screenshot_dir / f"{self.case.case_id}-{request.reason}.png"
            try:
                await _maybe_await(self.session.screenshot(str(screenshot_path)))
                self.screenshots.append(str(screenshot_path))
            except Exception:
                # Screenshots are evidence, not the verification oracle.
                pass
        return Observation(
            device_states={
                "oracle": {
                    "expected_effect_satisfied": _expected_effect_satisfied(self.case, oracle),
                    "case_id": self.case.case_id,
                    "failure_class": self.case.failure_class,
                    "state": oracle,
                }
            },
            accessibility_tree={
                "page_state": {
                    "browser_fixture": {
                        "url": self.url,
                        "html_fixture": self.case.html_fixture,
                        "observable_symptom": self.case.observable_symptom,
                    }
                }
            },
        )

    def executors(self) -> dict[str, _PlaywrightFixtureExecutor]:
        return {"dom": self.executor}


async def _read_fixture_oracle(session: BrowserSessionLike) -> dict[str, Any]:
    value = await _maybe_await(
        session.evaluate(
        """() => {
            const raw = document.body && document.body.getAttribute('data-oracle-state');
            if (!raw) return {};
            try { return JSON.parse(raw); } catch (error) { return {parse_error: String(error), raw}; }
        }"""
        )
    )
    return value if isinstance(value, dict) else {}


def _expected_effect_satisfied(case: OpenWebMockFailureCase, oracle: dict[str, Any]) -> bool:
    if case.case_id == "openweb-overlay-obstruction":
        return bool(oracle.get("primary_action_completed"))
    if case.case_id == "openweb-session-expiry":
        return bool(oracle.get("profile_update_persisted"))
    if case.case_id == "openweb-autocomplete-validation":
        return (
            "submitted_city" in oracle
            and "requested_city" in oracle
            and oracle.get("submitted_city") == oracle.get("requested_city")
        )
    if case.case_id == "openweb-optimistic-rollback":
        return bool(oracle.get("backend_order_confirmed"))
    if case.case_id == "openweb-dom-visual-disagreement":
        return (
            "dom_selected_plan" in oracle
            and "visual_highlighted_plan" in oracle
            and oracle.get("dom_selected_plan") == oracle.get("visual_highlighted_plan")
        )
    if case.case_id == "openweb-visible-ineffective-affordance":
        return bool(oracle.get("notifications_enabled"))
    return False


def _skill_for_case(case: OpenWebMockFailureCase) -> SkillTuple:
    return SkillTuple(
        skill_id=f"open_web_browser_fixture::{case.case_id}",
        description=f"Execute local browser fixture for {case.failure_class}",
        parameters_schema={},
        preconditions=[],
        postconditions=[Condition("oracle.expected_effect_satisfied == true", case.expected_effect)],
        allowed_backends=["dom"],
        preferred_backends=["dom"],
        rollback=None,
        failure_modes={},
        timeout_ms=1500,
        safety_level="low",
        irreversible=False,
        idempotent=False,
    )


async def _run_open_web_playwright_fixture_suite_async(
    output_dir: str | Path,
    *,
    seed_start: int = 10000,
    headless: bool = True,
    action_timeout_ms: int = 1000,
    capture_screenshots: bool = True,
    session_factory: SessionFactory | None = None,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    transition_path = target / "transition_ledger.jsonl"
    failure_path = target / "failure_ledger.jsonl"
    for path in (transition_path, failure_path):
        if path.exists():
            path.unlink()
    screenshot_dir = target / "screenshots"

    cases = build_open_web_mock_failure_suite(seed_start=seed_start)
    skill_library = {_skill_for_case(case).skill_id: _skill_for_case(case) for case in cases}
    transition_ledger = TransitionLedger(transition_path)
    failure_ledger = TraceLedger()
    runner = RuntimeEpisodeRunner(
        skill_library=skill_library,
        episode_policy=EpisodePolicy(max_steps=1, deadline_s=15.0, max_retry_attempts=0, max_attempts_per_backend=1),
        transition_ledger=transition_ledger,
        failure_ledger=failure_ledger,
    )
    factory = session_factory or _default_session_factory

    rows: list[dict[str, Any]] = []
    results = []
    for case in cases:
        fixture_path = (Path("env/mock_envs") / case.html_fixture).resolve()
        url = fixture_path.as_uri()
        session = await _maybe_await(factory(url, headless=headless, action_timeout_ms=action_timeout_ms))
        adapter = _PlaywrightFixtureRuntimeAdapter(
            session=session,
            case=case,
            url=url,
            screenshot_dir=screenshot_dir,
            capture_screenshots=capture_screenshots,
        )
        try:
            skill_id = f"open_web_browser_fixture::{case.case_id}"
            outcome = await runner.run_skill_episode(
                adapter,
                SkillCall(
                    skill_id,
                    {
                        "case_id": case.case_id,
                        "expected_effect": "oracle.expected_effect_satisfied == true",
                        "html_fixture": case.html_fixture,
                        "browser_url": url,
                    },
                ),
                RuntimeEpisodeSpec(
                    task_id=case.case_id,
                    data_source="open_web_playwright_fixture_suite",
                ),
            )
            result = outcome.result
            results.append(result)
            case_transitions = transition_ledger.for_episode(result.episode_id)
            rows.append(
                {
                    "case": asdict(case),
                    "browser": {
                        "url": url,
                        "fixture_path": str(fixture_path),
                        "actions": _CASE_ACTIONS[case.case_id],
                        "executed_actions": list(adapter.executor.action_log),
                        "screenshots": list(adapter.screenshots),
                    },
                    "runtime": {
                        "episode_id": result.episode_id,
                        "state": result.state.value,
                        "attempts": result.attempts,
                        "executor_success": bool(result.execution_result and result.execution_result.success),
                        "executor_failure_reason": (
                            result.execution_result.failure_reason if result.execution_result else ""
                        ),
                        "final_outcome_verified": result.final_outcome_verified,
                        "recovery_attempted": result.recovery_attempted,
                        "recovery_succeeded": result.recovery_succeeded,
                        "failure_type": result.failure_type,
                        "failure_boundary": result.failure_boundary,
                        "reason": result.reason,
                        "transition_ids": result.transition_ids,
                        "observation_requests": [request.reason for request in adapter.requests],
                        "postcondition_passed": [record.postcondition_passed for record in case_transitions],
                    },
                }
            )
        finally:
            await _maybe_await(session.close())

    failure_ledger.write_jsonl(failure_path)
    metrics = aggregate_metrics(
        dataset_from_runtime_results(results, transition_ledger),
        data_source="open_web_playwright_fixture_suite",
        episode_ids=[result.episode_id for result in results],
    )
    postcondition_failures = sum(
        1
        for row in rows
        if row["runtime"]["postcondition_passed"] and row["runtime"]["postcondition_passed"][0] is False
    )
    report = {
        "data_source": "open_web_playwright_fixture_suite",
        "protocol": {
            "runtime_entrypoint": "RuntimeEpisodeRunner.run_skill_episode",
            "browser_execution": True,
            "browser_surface": "Playwright Chromium isolated context over file:// local fixtures",
            "controlled_browser_fixture_evidence": True,
            "real_open_web_evidence": False,
            "oracle_source": "fixture data-oracle-state read after action",
            "claim_boundary": "real browser execution of local fixtures; not MiniWoB/WebArena/real open-web evidence",
            "episode_policy": {"max_steps": 1, "max_retry_attempts": 0},
        },
        "summary": {
            "case_count": len(cases),
            "runtime_episode_count": len(results),
            "executor_success_count": sum(1 for row in rows if row["runtime"]["executor_success"]),
            "executor_failure_count": sum(1 for row in rows if not row["runtime"]["executor_success"]),
            "postcondition_failures_detected": postcondition_failures,
            "final_success_count": sum(1 for row in rows if row["runtime"]["final_outcome_verified"]),
            "recovery_attempted_count": sum(1 for row in rows if row["runtime"]["recovery_attempted"]),
            "unique_episode_ids": len({result.episode_id for result in results}) == len(results),
            "transition_record_count": len(transition_ledger.records),
            "failure_record_count": len(failure_ledger.events),
        },
        "metrics": {"values": metrics.values, "metadata": metrics.metadata},
        "cases": rows,
        "artifacts": {
            "transition_ledger": str(transition_path),
            "failure_ledger": str(failure_path),
            "screenshots_dir": str(screenshot_dir) if capture_screenshots else "",
        },
        "recommendation": "port_representative_cases_to_miniwob_webarena_or_real_browser_probe",
    }
    report_path = target / "open_web_playwright_fixture_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "open_web_playwright_fixture_report": str(report_path),
        "transition_ledger": str(transition_path),
        "failure_ledger": str(failure_path),
    }


class _AsyncPlaywrightFixtureSession:
    def __init__(self, playwright: Any, browser: Any, context: Any, page: Any) -> None:
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page = page

    @classmethod
    async def launch(cls, url: str, *, headless: bool, action_timeout_ms: int) -> "_AsyncPlaywrightFixtureSession":
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        page = await context.new_page()
        page.set_default_timeout(action_timeout_ms)
        session = cls(playwright, browser, context, page)
        await session.open(url)
        return session

    async def open(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded", timeout=10_000)

    async def click(self, selector: str) -> None:
        await self._page.click(selector)

    async def fill(self, selector: str, value: str) -> None:
        await self._page.fill(selector, value)

    async def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        if arg is None:
            return await self._page.evaluate(expression)
        return await self._page.evaluate(expression, arg)

    async def screenshot(self, path: str | None = None) -> bytes:
        kwargs: dict[str, Any] = {"full_page": True, "animations": "disabled"}
        if path:
            kwargs["path"] = path
        return await self._page.screenshot(**kwargs)

    async def close(self) -> None:
        await self._context.close()
        await self._browser.close()
        await self._playwright.stop()


async def _default_session_factory(url: str, *, headless: bool, action_timeout_ms: int) -> BrowserSessionLike:
    return await _AsyncPlaywrightFixtureSession.launch(
        url,
        headless=headless,
        action_timeout_ms=action_timeout_ms,
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def run_open_web_playwright_fixture_suite(
    output_dir: str | Path,
    *,
    seed_start: int = 10000,
    headless: bool = True,
    action_timeout_ms: int = 1000,
    capture_screenshots: bool = True,
    session_factory: SessionFactory | None = None,
) -> dict[str, str]:
    return asyncio.run(
        _run_open_web_playwright_fixture_suite_async(
            output_dir,
            seed_start=seed_start,
            headless=headless,
            action_timeout_ms=action_timeout_ms,
            capture_screenshots=capture_screenshots,
            session_factory=session_factory,
        )
    )
