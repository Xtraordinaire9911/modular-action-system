"""Five recovery families on the live Smart Room, through production Runtime.

The control plane injects faults but is never passed to Runtime or Planner.
Runtime receives only normal DOM/WoT observations and executor results.  The
campaign uses a clearly labelled local model-client stub when no external model
is configured; the stub answers through :class:`ModelRecoveryPlanner`, and its
proposal is still validated and executed by Runtime.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.adaptation.trace_ledger import TraceLedger
from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.effectors.wot_executor import WotExecutor
from src.planner.model_recovery_planner import ModelRecoveryPlanner
from src.recovery.recovery_cascade import RecoveryCascade
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager, RuntimeStepResult
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.goal_spec import GoalSpec
from src.runtime.live_environment import (
    AffordanceSemanticBinding,
    ContractAffordanceEffector,
    DomStateProbe,
    LiveActivePerceptionProbe,
    LiveEnvironmentConfig,
    RuntimeAffordanceExecutor,
    SmartRoomControlClient,
    SmartRoomLiveEnvironment,
    ThreadedBrowserSession,
    ThreadedDomEffector,
)
from src.verification.active_perception import ActivePerceptionResolver
from src.verification.conflict_detector import EpistemicArbiter


class DemoRecoveryClient:
    """Local deterministic feedback behind the real model recovery boundary.

    It sees exactly the text :class:`ModelRecoveryPlanner` would send to a
    model.  It cannot inspect the page, selectors, control plane, or injected
    scenario.  Selection is based on observed capability names/relations only.
    """

    name = "smart-room-demo-client (simulated upstream)"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        _ = system
        self.prompts.append(user)
        goal_state = _field(user, "goal_state")
        candidates = _candidate_rows(user)
        chosen: tuple[str, str, str] | None = None

        for affordance_id, action, line in candidates:
            if "remediates=" in line:
                chosen = (affordance_id, _primitive_for_candidate(action), "clear the measured obstruction")
                postcondition = re.search(r"recovery_postcondition=(.+)$", line)
                if postcondition:
                    goal_state = postcondition.group(1).strip()
                break
        if chosen is None:
            for affordance_id, action, line in candidates:
                normalized = line.casefold()
                if "renew" in normalized and "session" in normalized:
                    chosen = (affordance_id, _primitive_for_candidate(action), "renew the observed expired session")
                    goal_state = "session.valid == true"
                    break
        if chosen is None:
            for affordance_id, action, line in candidates:
                if "direct projector control" in line.casefold():
                    chosen = (affordance_id, _primitive_for_candidate(action), "use the observed direct device path")
                    break

        if chosen is None:
            return json.dumps(
                {
                    "affordance_id": "",
                    "action": "ask_user",
                    "value": None,
                    "expected_effect": "",
                    "reason": "no observed capability can recover the failure",
                    "confidence": 1.0,
                }
            )
        affordance_id, action, reason = chosen
        return json.dumps(
            {
                "affordance_id": affordance_id,
                "action": action,
                "value": None,
                "expected_effect": goal_state,
                "reason": reason,
                "confidence": 0.95,
            }
        )


def _field(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _candidate_rows(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        match = re.match(r"^\s{2}(\S+)\s+action=(\S+)", line)
        if match:
            rows.append((match.group(1), match.group(2), line))
    return rows


def _primitive_for_candidate(action_type: str) -> str:
    if action_type in {"action", "invoke"}:
        return "invoke"
    if action_type in {"property", "sensor", "read"}:
        return "read"
    return "click"


def _with_query(url: str, **values: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class VideoNarrator:
    """Mirror Runtime milestones into the recorded page and terminal."""

    def __init__(self, session: ThreadedBrowserSession, index: int, title: str) -> None:
        self.session = session
        self.index = index
        self.title = title
        self.action_count = 0

    async def show(self, phase: str, headline: str, detail: str, color: str = "#2563eb") -> None:
        print(f"[scene {self.index}/5] {self.title} | {phase}: {headline}", flush=True)
        await self.session.evaluate(
            """({phase, headline, detail, color, title}) => {
                let panel = document.getElementById('__smart_room_recovery_panel');
                if (!panel) {
                    panel = document.createElement('aside');
                    panel.id = '__smart_room_recovery_panel';
                    panel.setAttribute('data-runtime-overlay', 'true');
                    Object.assign(panel.style, {
                        position: 'fixed', right: '14px', top: '14px', width: '390px', zIndex: '2147483647',
                        background: '#0f172a', color: '#e2e8f0', border: '2px solid #6366f1', borderRadius: '12px',
                        padding: '16px 18px', boxShadow: '0 12px 35px rgba(0,0,0,.35)', pointerEvents: 'none',
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace'
                    });
                    panel.innerHTML = '<div class="sr-title"></div><div class="sr-phase"></div>' +
                        '<div class="sr-head"></div><div class="sr-detail"></div>';
                    document.body.appendChild(panel);
                }
                panel.querySelector('.sr-title').textContent = title;
                panel.querySelector('.sr-title').style.cssText = 'font-size:11px;color:#a5b4fc;margin-bottom:9px';
                panel.querySelector('.sr-phase').textContent = phase;
                panel.querySelector('.sr-phase').style.cssText = `display:inline-block;background:${color};color:white;` +
                    'padding:4px 7px;border-radius:5px;font-size:11px;font-weight:800;margin-bottom:10px';
                panel.querySelector('.sr-head').textContent = headline;
                panel.querySelector('.sr-head').style.cssText = 'font-size:15px;font-weight:800;line-height:1.4';
                panel.querySelector('.sr-detail').textContent = detail;
                panel.querySelector('.sr-detail').style.cssText = 'font-size:11px;color:#cbd5e1;line-height:1.5;margin-top:8px';
            }""",
            {"phase": phase, "headline": headline, "detail": detail, "color": color, "title": self.title},
        )
        await asyncio.sleep(0.45)


class NarratedEffector:
    def __init__(self, inner: ContractAffordanceEffector, narrator: VideoNarrator) -> None:
        self.inner = inner
        self.narrator = narrator

    async def execute(
        self,
        target: Affordance | SkillCall,
        observation: Observation | None = None,
        *,
        value: Any | None = None,
        skill_id: str = "",
    ) -> ExecutionResult:
        self.narrator.action_count += 1
        label = target.label if isinstance(target, Affordance) else target.skill_id
        recovery = self.narrator.action_count > 1
        await self.narrator.show(
            "RECOVERY ACTION" if recovery else "ORIGINAL ACTION",
            f"Executing observed capability: {label}",
            "Runtime validated the proposal against the latest affordance snapshot.",
            "#d97706" if recovery else "#0f766e",
        )
        produced = self.inner.execute(target, observation, value=value, skill_id=skill_id)
        result = await produced if inspect.isawaitable(produced) else produced
        if not isinstance(result, ExecutionResult):
            raise TypeError(f"{type(self.inner).__name__} returned {type(result).__name__}")
        await self.narrator.show(
            "EXECUTOR RETURN",
            "Executor returned success" if result.success else "Executor reported failure",
            "Runtime will not close the goal until a fresh observation passes the oracle.",
            "#2563eb" if result.success else "#b91c1c",
        )
        return result


class NarratedObservationProvider:
    def __init__(self, inner: SmartRoomLiveEnvironment, narrator: VideoNarrator) -> None:
        self.inner = inner
        self.narrator = narrator

    async def observe(self, request: ObservationRequest):
        observed = await self.inner.observe(request)
        if request.previous_result is not None:
            await self.narrator.show(
                "FRESH VERIFICATION",
                "Runtime re-observed dashboard and device truth",
                f"request={request.reason}; executor success alone is not accepted as goal success.",
                "#b91c1c",
            )
        return observed


def _presentation_bindings() -> list[AffordanceSemanticBinding]:
    goal = "projector.lamp == on"
    return [
        AffordanceSemanticBinding(
            "DOM",
            entity_id="presentation",
            state_attribute="requested",
            selector="[data-testid='presentation-mode-button']",
            completion_for="enable_presentation",
            achieves=goal,
            stable_key="presentation.enable",
        ),
    ]


def _presentation_probes() -> list[DomStateProbe]:
    return [
        DomStateProbe(
            "[data-testid='session-state']",
            "session",
            "valid",
            value_type="boolean",
            true_pattern=r"^valid$",
            false_pattern=r"^expired$",
        )
    ]


def _temperature_bindings() -> list[AffordanceSemanticBinding]:
    return [
        AffordanceSemanticBinding(
            "WOT",
            entity_id="thermostat",
            state_attribute="target_temperature",
            state_source_property="targetTemperature",
            thing_id="thermostat",
            label="targetTemperature",
            binds_parameter="target",
            achieves="thermostat.target_temperature == 22",
            stable_key="thermostat.set_target_temperature",
            idempotent=True,
        )
    ]


def _temperature_probe() -> DomStateProbe:
    return DomStateProbe(
        "[data-testid='target-temp']",
        "thermostat",
        "target_temperature",
        value_type="number",
    )


def _policy() -> EpisodePolicy:
    return EpisodePolicy(
        max_steps=12,
        deadline_s=45.0,
        max_retry_attempts=1,
        max_attempts_per_backend=8,
        require_fresh_observation=True,
    )


async def _presentation_case(
    *,
    index: int,
    title: str,
    dashboard_url: str,
    config: LiveEnvironmentConfig,
    control: SmartRoomControlClient,
    transitions: TransitionLedger,
    failures: TraceLedger,
    planner_ledger: Path,
    record_video_dir: Path | None,
    headless: bool,
    inject_rollback: bool = False,
) -> tuple[dict[str, Any], str]:
    await control.reset()
    if inject_rollback:
        await control.inject("projector", "delayed_rollback", delay_ms=650)
    before = set(record_video_dir.glob("*.webm")) if record_video_dir else set()
    session = ThreadedBrowserSession(
        dashboard_url,
        headless=headless,
        action_timeout_ms=900,
        record_video_dir=str(record_video_dir) if record_video_dir else None,
    )
    await session.start()
    narrator = VideoNarrator(session, index, title)
    await narrator.show("OBSERVE", "Smart Room episode started", "The injected fault is hidden from Runtime/Planner.")
    client = DemoRecoveryClient()
    planner = ModelRecoveryPlanner(client=client, ledger_path=planner_ledger)
    environment = SmartRoomLiveEnvironment(
        session,
        config,
        dom_state_probes=_presentation_probes(),
        semantic_bindings=_presentation_bindings(),
    )
    initial = await environment.observe(
        ObservationRequest(task_id=f"smart-room-{index}", episode_id="preflight", reason="initial_scan", step=0)
    )
    dom = RuntimeAffordanceExecutor("dom", environment, NarratedEffector(ThreadedDomEffector(session), narrator))
    wot = RuntimeAffordanceExecutor(
        "wot",
        environment,
        NarratedEffector(WotExecutor(environment.tds, timeout_s=2.0), narrator),
    )
    manager = ContinuousInteractionManager(
        {},
        {"dom": dom, "wot": wot},
        CognitiveMap(task_id=f"smart-room-{index}"),
        observation_provider=NarratedObservationProvider(environment, narrator),
        episode_policy=_policy(),
        transition_ledger=transitions,
        failure_ledger=failures,
        recovery_cascade=RecoveryCascade(),
        system2_planner=planner,
    )
    try:
        result = await manager.run_observed_goal(
            initial,
            goal_spec=GoalSpec(
                goal_id="enable_presentation",
                goal_state="projector.lamp == on",
                parameters={},
                source="smart_room_recovery_campaign",
                success_evidence=["fresh node-wot projector lamp property equals on"],
            ),
        )
        oracle = await control.state()
        verified = (
            result.final_outcome_verified
            and oracle["state"]["projector"]["power"] == "on"
            and oracle["state"]["projector"]["lamp"] == "on"
        )
        await narrator.show(
            "FINAL ORACLE",
            "VERIFIED SUCCESS" if verified else "NOT VERIFIED",
            f"projector.power={oracle['state']['projector']['power']}; lamp={oracle['state']['projector']['lamp']}",
            "#15803d" if verified else "#b91c1c",
        )
        await asyncio.sleep(1.1)
    finally:
        await session.close()
    video = _new_video(record_video_dir, before, index, title) if record_video_dir else ""
    row = _result_row(title, result, verified, oracle, planner, client)
    return row, video


async def _conflict_case(
    *,
    index: int,
    title: str,
    config: LiveEnvironmentConfig,
    control: SmartRoomControlClient,
    transitions: TransitionLedger,
    failures: TraceLedger,
    planner_ledger: Path,
    record_video_dir: Path | None,
    headless: bool,
) -> tuple[dict[str, Any], str]:
    await control.reset()
    url = _with_query(config.dashboard_url, fault="stale_temperature", stale_offset="-3")
    before = set(record_video_dir.glob("*.webm")) if record_video_dir else set()
    session = ThreadedBrowserSession(
        url,
        headless=headless,
        action_timeout_ms=900,
        record_video_dir=str(record_video_dir) if record_video_dir else None,
    )
    await session.start()
    narrator = VideoNarrator(session, index, title)
    await narrator.show(
        "CONFLICT",
        "Dashboard projection and WoT device state disagree",
        "Runtime blocks action and requests a fresh active-perception observation.",
        "#7e22ce",
    )
    client = DemoRecoveryClient()
    planner = ModelRecoveryPlanner(client=client, ledger_path=planner_ledger)
    environment = SmartRoomLiveEnvironment(
        session,
        config,
        dom_state_probes=[_temperature_probe()],
        semantic_bindings=_temperature_bindings(),
    )
    initial = await environment.observe(
        ObservationRequest(task_id=f"smart-room-{index}", episode_id="preflight", reason="initial_scan", step=0)
    )
    probe = LiveActivePerceptionProbe(environment, clear_dom_faults=True, settle_s=0.25)
    wot = RuntimeAffordanceExecutor(
        "wot",
        environment,
        NarratedEffector(WotExecutor(environment.tds, timeout_s=2.0), narrator),
    )
    manager = ContinuousInteractionManager(
        {},
        {"wot": wot},
        CognitiveMap(task_id=f"smart-room-{index}"),
        observation_provider=NarratedObservationProvider(environment, narrator),
        episode_policy=_policy(),
        transition_ledger=transitions,
        failure_ledger=failures,
        recovery_cascade=RecoveryCascade(),
        system2_planner=planner,
        active_perception_resolver=ActivePerceptionResolver(probe, max_attempts=2),
        epistemic_arbiter=EpistemicArbiter(fusion_strategy="rule_first"),
    )
    try:
        result = await manager.run_observed_goal(
            initial,
            goal_spec=GoalSpec(
                goal_id="set_temperature_live",
                goal_state="thermostat.target_temperature == 22",
                parameters={"target": 22},
                source="smart_room_recovery_campaign",
                success_evidence=["fresh node-wot thermostat target equals 22"],
            ),
        )
        oracle = await control.state()
        verified = (
            result.final_outcome_verified
            and bool(result.active_perception_trace)
            and oracle["state"]["thermostat"]["targetTemperature"] == 22
        )
        await narrator.show(
            "FINAL ORACLE",
            "VERIFIED SUCCESS" if verified else "NOT VERIFIED",
            f"active probe steps={len(result.active_perception_trace)}; targetTemperature="
            f"{oracle['state']['thermostat']['targetTemperature']}",
            "#15803d" if verified else "#b91c1c",
        )
        await asyncio.sleep(1.1)
    finally:
        await session.close()
    video = _new_video(record_video_dir, before, index, title) if record_video_dir else ""
    row = _result_row(title, result, verified, oracle, planner, client)
    return row, video


def _new_video(directory: Path, before: set[Path], index: int, title: str) -> str:
    candidates = sorted(set(directory.glob("*.webm")) - before, key=lambda path: path.stat().st_mtime)
    if not candidates:
        return ""
    source = candidates[-1]
    slug = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")
    target = directory / f"{index:02d}_{slug}.webm"
    if source != target:
        source.replace(target)
    return str(target)


def _result_row(
    title: str,
    result: RuntimeStepResult,
    verified: bool,
    oracle: dict[str, Any],
    planner: ModelRecoveryPlanner,
    client: DemoRecoveryClient,
) -> dict[str, Any]:
    forbidden = (
        "overlay_obstruction",
        "session_expiry",
        "optimistic_rollback",
        "stale_temperature",
        "ineffective_affordance",
        "delayed_rollback",
    )
    prompt_text = "\n".join(client.prompts)
    return {
        "scene": title,
        "runtime": {
            "episode_id": result.episode_id,
            "state": result.state.value,
            "attempts": result.attempts,
            "recovery_attempted": result.recovery_attempted,
            "recovery_succeeded": result.recovery_succeeded,
            "final_outcome_verified": result.final_outcome_verified,
            "failure_type": result.failure_type,
            "transition_ids": result.transition_ids,
            "active_perception_trace": result.active_perception_trace,
        },
        "planner_choices": [choice.to_dict() for choice in planner.recovery_choices()],
        "fault_labels_absent_from_planner_prompt": not any(label in prompt_text for label in forbidden),
        "independent_oracle_verified": verified,
        "oracle": oracle.get("state", {}),
    }


async def _run(
    output_dir: Path,
    *,
    dashboard_url: str,
    directory_url: str,
    wot_url: str,
    control_url: str,
    record_video: bool,
    headless: bool,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    transition_path = output_dir / "transition_ledger.jsonl"
    failure_path = output_dir / "failure_ledger.jsonl"
    planner_path = output_dir / "planner_calls.jsonl"
    for path in (transition_path, failure_path, planner_path):
        path.unlink(missing_ok=True)
    raw_video_dir = output_dir / "raw_video" if record_video else None
    if raw_video_dir:
        raw_video_dir.mkdir(parents=True, exist_ok=True)
        for stale in raw_video_dir.glob("*.webm"):
            stale.unlink()

    config = LiveEnvironmentConfig(
        dashboard_url=dashboard_url.rstrip("/"),
        thing_directory_url=directory_url.rstrip("/") + "/things",
        wot_public_base_url=wot_url.rstrip("/"),
        control_url=control_url.rstrip("/"),
        request_timeout_s=2.0,
        settle_after_action_s=1.35,
        output_dir=output_dir,
    )
    control = SmartRoomControlClient(config.control_url, timeout_s=3.0)
    transitions = TransitionLedger(transition_path)
    failures = TraceLedger()
    rows: list[dict[str, Any]] = []
    videos: list[str] = []

    specs = [
        (1, "Overlay obstruction", "overlay_obstruction", False),
        (2, "Session expiry", "session_expiry", False),
        (3, "Optimistic rollback", "optimistic_rollback", True),
    ]
    for index, title, query_fault, rollback in specs:
        row, video = await _presentation_case(
            index=index,
            title=title,
            dashboard_url=_with_query(config.dashboard_url, fault=query_fault),
            config=config,
            control=control,
            transitions=transitions,
            failures=failures,
            planner_ledger=planner_path,
            record_video_dir=raw_video_dir,
            headless=headless,
            inject_rollback=rollback,
        )
        rows.append(row)
        if video:
            videos.append(video)

    row, video = await _conflict_case(
        index=4,
        title="Dashboard/device disagreement",
        config=config,
        control=control,
        transitions=transitions,
        failures=failures,
        planner_ledger=planner_path,
        record_video_dir=raw_video_dir,
        headless=headless,
    )
    rows.append(row)
    if video:
        videos.append(video)

    row, video = await _presentation_case(
        index=5,
        title="Ineffective affordance",
        dashboard_url=_with_query(config.dashboard_url, fault="ineffective_affordance"),
        config=config,
        control=control,
        transitions=transitions,
        failures=failures,
        planner_ledger=planner_path,
        record_video_dir=raw_video_dir,
        headless=headless,
    )
    rows.append(row)
    if video:
        videos.append(video)

    await control.reset()
    failures.write_jsonl(failure_path)
    summary = {
        "scene_count": len(rows),
        "final_verified_count": sum(1 for row in rows if row["independent_oracle_verified"]),
        "all_final_oracles_verified": all(row["independent_oracle_verified"] for row in rows),
        "fault_labels_hidden_from_planner": all(row["fault_labels_absent_from_planner_prompt"] for row in rows),
    }
    report = {
        "data_source": "live_smart_room",
        "environment": {
            "dashboard": config.dashboard_url,
            "thing_directory": config.thing_directory_url,
            "wot": config.wot_public_base_url,
            "control_plane": config.control_url,
        },
        "summary": summary,
        "scenes": rows,
        "scene_videos": videos,
        "claim_boundary": {
            "runtime": "production Runtime/CIM, validation, execution, observation, verification and ledgers",
            "planner": "ModelRecoveryPlanner with explicitly simulated local client",
            "vision": "active-perception probe; no production VLM claim",
            "environment": "live React dashboard and Eclipse Thingweb node-wot servient",
        },
    }
    report_path = output_dir / "smart_room_recovery_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return {
        "report": str(report_path),
        "transition_ledger": str(transition_path),
        "failure_ledger": str(failure_path),
        "planner_ledger": str(planner_path),
        "raw_video_dir": str(raw_video_dir or ""),
    }


def run_smart_room_recovery_campaign(
    output_dir: str | Path = "artifacts/smart_room_recovery",
    *,
    dashboard_url: str = "http://127.0.0.1:3000",
    directory_url: str = "http://127.0.0.1:8082",
    wot_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    record_video: bool = False,
    headless: bool = True,
) -> dict[str, str]:
    return asyncio.run(
        _run(
            Path(output_dir),
            dashboard_url=dashboard_url,
            directory_url=directory_url,
            wot_url=wot_url,
            control_url=control_url,
            record_video=record_video,
            headless=headless,
        )
    )


__all__ = ["DemoRecoveryClient", "run_smart_room_recovery_campaign"]
