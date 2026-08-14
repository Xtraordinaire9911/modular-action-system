"""Run open-web mock fixtures through a real browser and the runtime envelope."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Sequence

from evaluation.metrics_aggregator import aggregate_metrics, dataset_from_runtime_results
from evaluation.open_web_mock_failure_suite import OpenWebMockFailureCase, build_open_web_mock_failure_suite
from src.adaptation.trace_ledger import TraceLedger
from src.contracts.types import Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.runtime.continuous_interaction_manager import Executor
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec

if TYPE_CHECKING:
    from evaluation.open_web_randomized_holdout import OpenWebFailureVariant


class BrowserSessionLike(Protocol):
    def open(self, url: str) -> Any: ...
    def click(self, selector: str) -> Any: ...
    def fill(self, selector: str, value: str) -> Any: ...
    def evaluate(self, expression: str, arg: Any | None = None) -> Any: ...
    def screenshot(self, path: str | None = None) -> Any: ...
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
    def __init__(
        self,
        session: BrowserSessionLike,
        case: OpenWebMockFailureCase,
        variant_parameters: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.case = case
        self.variant_parameters = dict(variant_parameters or {})
        self.calls: list[SkillCall] = []
        self.action_log: list[dict[str, Any]] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        self.calls.append(skill_call)
        started = asyncio.get_running_loop().time()
        try:
            for step in _actions_for_case(self.case, self.variant_parameters):
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
        variant_id: str = "",
        variant_split: str = "",
        variant_signature: str = "",
        variant_parameters: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.case = case
        self.url = url
        self.variant_id = variant_id
        self.variant_split = variant_split
        self.variant_signature = variant_signature
        self.variant_parameters = dict(variant_parameters or {})
        self.executor = _PlaywrightFixtureExecutor(session, case, self.variant_parameters)
        self.screenshot_dir = screenshot_dir
        self.capture_screenshots = capture_screenshots
        self.requests: list[ObservationRequest] = []
        self.reset_specs: list[RuntimeEpisodeSpec] = []
        self.screenshots: list[str] = []
        self.observed_oracles: list[dict[str, Any]] = []

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        self.reset_specs.append(spec)
        await _maybe_await(self.session.open(self.url))
        if self.variant_parameters:
            await _apply_fixture_variant(
                self.session,
                self.case,
                variant_id=self.variant_id,
                split=self.variant_split,
                parameters=self.variant_parameters,
            )

    async def observe(self, request: ObservationRequest) -> Observation:
        self.requests.append(request)
        oracle = await _read_fixture_oracle(self.session)
        self.observed_oracles.append(dict(oracle))
        if self.capture_screenshots:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            evidence_id = self.variant_id or self.case.case_id
            screenshot_path = self.screenshot_dir / f"{evidence_id}-{request.reason}.png"
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
                    "variant_id": self.variant_id,
                    "variant_split": self.variant_split,
                    "variant_signature": self.variant_signature,
                    "variant_parameters": self.variant_parameters,
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

    def executors(self) -> dict[str, Executor]:
        return {"dom": self.executor}


async def _read_fixture_oracle(session: BrowserSessionLike) -> dict[str, Any]:
    value = await _maybe_await(session.evaluate("""() => {
            const raw = document.body && document.body.getAttribute('data-oracle-state');
            if (!raw) return {};
            try { return JSON.parse(raw); } catch (error) { return {parse_error: String(error), raw}; }
        }"""))
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


def _actions_for_case(case: OpenWebMockFailureCase, parameters: dict[str, Any]) -> list[dict[str, str]]:
    actions = [dict(step) for step in _CASE_ACTIONS[case.case_id]]
    if case.case_id == "openweb-autocomplete-validation":
        actions[0]["value"] = str(parameters.get("requested_city", "New York"))
    elif case.case_id == "openweb-dom-visual-disagreement":
        dom_plan = str(parameters.get("dom_selected_plan", "premium"))
        actions = [{"action": "click", "selector": f"#choose-{dom_plan}"}]
    return actions


async def _apply_fixture_variant(
    session: BrowserSessionLike,
    case: OpenWebMockFailureCase,
    *,
    variant_id: str,
    split: str,
    parameters: dict[str, Any],
) -> None:
    payload = {
        "caseId": case.case_id,
        "variantId": variant_id,
        "split": split,
        "parameters": parameters,
        "oracle": _variant_oracle_state(case, parameters),
    }
    await _maybe_await(
        session.evaluate(
            """(payload) => {
                const body = document.body;
                const p = payload.parameters;
                body.setAttribute('data-variant-id', payload.variantId);
                body.setAttribute('data-variant-split', payload.split);
                body.setAttribute('data-variant-parameters', JSON.stringify(p));
                body.setAttribute('data-oracle-state', JSON.stringify(payload.oracle));

                if (payload.caseId === 'openweb-overlay-obstruction') {
                    const overlay = document.querySelector('#cookie-wall');
                    const modal = document.querySelector('#cookie-wall .modal');
                    const remediation = document.querySelector('#accept-cookies');
                    overlay.style.background = `rgba(0,0,0,${p.overlay_opacity})`;
                    overlay.style.zIndex = String(p.z_index);
                    modal.style.transform = `translateX(${p.modal_offset_px}px)`;
                    modal.style.padding = `${p.modal_padding_px || 24}px`;
                    remediation.textContent = p.remediation_label || remediation.textContent;
                    remediation.id = p.remediation_control_id || remediation.id;
                } else if (payload.caseId === 'openweb-session-expiry') {
                    document.querySelector('#session-banner').textContent =
                        `Session expired ${p.session_age_s}s ago (HTTP ${p.auth_code}).`;
                    document.querySelector('#save-profile').onclick = () => {
                        document.querySelector('#status').textContent =
                            `HTTP ${p.auth_code}: redirected to login; update not persisted.`;
                    };
                } else if (payload.caseId === 'openweb-autocomplete-validation') {
                    document.querySelector('#submit-city').onclick = () => {
                        document.querySelector('#city').value = p.submitted_city;
                        document.querySelector('#status').textContent =
                            `Validator ${p.validator_revision}: submitted ${p.submitted_city}`;
                    };
                } else if (payload.caseId === 'openweb-optimistic-rollback') {
                    document.querySelector('#place-order').onclick = () => {
                        document.querySelector('#ui-status').textContent = 'Order submitted';
                        document.querySelector('#api-status').textContent =
                            `Backend ${p.backend_status_code}; rollback in ${p.rollback_delay_ms}ms`;
                    };
                } else if (payload.caseId === 'openweb-dom-visual-disagreement') {
                    document.querySelectorAll('[data-plan]').forEach((node) => {
                        const name = node.getAttribute('data-plan');
                        node.classList.toggle('visual-highlight', name === p.visual_highlighted_plan);
                        node.setAttribute('aria-selected', String(name === p.dom_selected_plan));
                    });
                    const highlighted = document.querySelector('.visual-highlight');
                    highlighted.style.outlineWidth = `${p.highlight_width_px}px`;
                    highlighted.style.outlineColor = `hsl(${p.highlight_hue} 75% 50%)`;
                    document.querySelector('#status').textContent =
                        `DOM selected: ${p.dom_selected_plan}. Visual highlight: ${p.visual_highlighted_plan}.`;
                } else if (payload.caseId === 'openweb-visible-ineffective-affordance') {
                    document.querySelector('#notification-toggle').setAttribute(
                        'data-control-revision', p.control_revision
                    );
                    document.querySelector('#notification-toggle').onclick = () => {
                        document.querySelector('#status').textContent =
                            `${p.ack_code}: ${p.reported_clicks} click(s) accepted; notifications remain disabled.`;
                    };
                }

                const target = document.querySelector('[data-capability-target="true"]');
                if (target && p.target_control_id) {
                    target.setAttribute('data-affordance-id', p.target_control_id);
                }
                const recovery = document.querySelector('[data-recovery-role]');
                if (recovery && p.recovery_control_id) {
                    recovery.setAttribute('data-affordance-id', p.recovery_control_id);
                    recovery.textContent = p.recovery_label || recovery.textContent;
                }
                const alternative = document.querySelector('[data-equivalent-to]');
                if (alternative && p.alternative_control_id) {
                    alternative.setAttribute('data-affordance-id', p.alternative_control_id);
                    alternative.textContent = p.recovery_label || alternative.textContent;
                }
                if (target && p.target_control_id) {
                    for (const relation of ['data-remediates', 'data-compensates',
                                            'data-equivalent-to', 'data-restores', 'data-observes']) {
                        document.querySelectorAll(`[${relation}]`).forEach((node) => {
                            node.setAttribute(relation, p.target_control_id);
                        });
                    }
                }
            }""",
            payload,
        )
    )


def _variant_oracle_state(case: OpenWebMockFailureCase, parameters: dict[str, Any]) -> dict[str, Any]:
    state = dict(case.oracle_state)
    if case.case_id == "openweb-session-expiry":
        state.update(session_age_s=parameters["session_age_s"], auth_code=parameters["auth_code"])
    elif case.case_id == "openweb-autocomplete-validation":
        state.update(
            requested_city=parameters["requested_city"],
            submitted_city=parameters["submitted_city"],
            validator_revision=parameters["validator_revision"],
        )
    elif case.case_id == "openweb-optimistic-rollback":
        state.update(
            backend_status_code=parameters["backend_status_code"],
            rollback_delay_ms=parameters["rollback_delay_ms"],
        )
    elif case.case_id == "openweb-dom-visual-disagreement":
        state.update(
            dom_selected_plan=parameters["dom_selected_plan"],
            visual_highlighted_plan=parameters["visual_highlighted_plan"],
        )
    elif case.case_id == "openweb-visible-ineffective-affordance":
        state.update(
            ack_code=parameters["ack_code"],
            reported_clicks=parameters["reported_clicks"],
            control_revision=parameters["control_revision"],
        )
    else:
        state.update(
            overlay_opacity=parameters["overlay_opacity"],
            modal_offset_px=parameters["modal_offset_px"],
            z_index=parameters["z_index"],
        )
    return state


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
    variants: Sequence["OpenWebFailureVariant"] | None = None,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    transition_path = target / "transition_ledger.jsonl"
    failure_path = target / "failure_ledger.jsonl"
    for path in (transition_path, failure_path):
        if path.exists():
            path.unlink()
    screenshot_dir = target / "screenshots"

    selected_variants = list(variants or [])
    cases = [variant.case for variant in selected_variants] or build_open_web_mock_failure_suite(seed_start=seed_start)
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
    for index, case in enumerate(cases):
        variant = selected_variants[index] if selected_variants else None
        fixture_path = (Path("env/mock_envs") / case.html_fixture).resolve()
        url = fixture_path.as_uri()
        session = await _maybe_await(factory(url, headless=headless, action_timeout_ms=action_timeout_ms))
        adapter = _PlaywrightFixtureRuntimeAdapter(
            session=session,
            case=case,
            url=url,
            screenshot_dir=screenshot_dir,
            capture_screenshots=capture_screenshots,
            variant_id=variant.variant_id if variant else "",
            variant_split=variant.split if variant else "",
            variant_signature=variant.signature if variant else "",
            variant_parameters=variant.parameters if variant else {},
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
                        "actions": _actions_for_case(case, adapter.variant_parameters),
                        "executed_actions": list(adapter.executor.action_log),
                        "screenshots": list(adapter.screenshots),
                        "observed_oracles": list(adapter.observed_oracles),
                    },
                    "variant": (
                        {
                            "variant_id": variant.variant_id,
                            "split": variant.split,
                            "repetition": variant.repetition,
                            "seed": variant.seed,
                            "signature": variant.signature,
                            "parameters": variant.parameters,
                        }
                        if variant
                        else None
                    ),
                    "runtime": {
                        "episode_id": result.episode_id,
                        "state": result.state.value,
                        "outcome": result.outcome.value,
                        "attempts": result.attempts,
                        "replan_count": result.replan_count,
                        "user_action_required": result.user_action_required,
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
            "randomized_variant_evidence": bool(selected_variants),
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
    variants: Sequence["OpenWebFailureVariant"] | None = None,
) -> dict[str, str]:
    return asyncio.run(
        _run_open_web_playwright_fixture_suite_async(
            output_dir,
            seed_start=seed_start,
            headless=headless,
            action_timeout_ms=action_timeout_ms,
            capture_screenshots=capture_screenshots,
            session_factory=session_factory,
            variants=variants,
        )
    )
