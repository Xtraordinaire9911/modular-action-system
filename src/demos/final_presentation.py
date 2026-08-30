"""Chaptered, evidence-backed orchestration for the final presentation demo.

The canonical supervised smart-room episode remains the main story.  Focused
chapters run beside it only where the repository has a separate implementation
boundary (model/VLM evidence, visual grounding, recovery campaign, adaptation).
Every chapter records that boundary in one manifest so a successful animation
cannot be mistaken for evidence from a different runtime path.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from src.config.secrets import configured_key_names
from src.demos.registry import check_capability
from src.perception.vlm_observer import available_vision_client
from src.planner.intent_planner import available_client

REPO_ROOT = Path(__file__).resolve().parents[2]
RECORDED_MODEL_REPORT = REPO_ROOT / "artifacts" / "llm_demo" / "run-report.json"
RECORDED_MODEL_VIDEO = REPO_ROOT / "artifacts" / "llm_demo" / "llm-vs-rules-smartroom.mp4"

ModelMode = Literal["auto", "live", "recorded", "skip"]
Profile = Literal["presentation", "complete"]
ChapterStatus = Literal["passed", "failed", "recorded", "skipped", "not_run"]


@dataclass(frozen=True)
class FinalDemoConfig:
    output_dir: Path
    profile: Profile = "presentation"
    model_mode: ModelMode = "auto"
    canonical_model: bool = False
    headless: bool = False
    auto_approve: bool = False
    pause_between_chapters: bool = False
    continue_on_error: bool = False
    fast: bool = False
    only: tuple[str, ...] = ()
    dashboard_url: str = "http://127.0.0.1:3000"
    thing_directory_url: str = "http://127.0.0.1:8082/things"
    wot_base_url: str = "http://127.0.0.1:8080"
    control_url: str = "http://127.0.0.1:8081"
    utterance: str = "book room C at 15:30 and prepare it for my presentation"

    @property
    def directory_base_url(self) -> str:
        return self.thing_directory_url.removesuffix("/things").rstrip("/")


@dataclass(frozen=True)
class CapabilitySnapshot:
    browser: bool
    browser_detail: str
    smart_room: bool
    smart_room_detail: str
    text_model: str = ""
    vision_model: str = ""
    configured_key_names: tuple[str, ...] = ()
    recorded_model_evidence: bool = False

    @property
    def models_live(self) -> bool:
        return bool(self.text_model and self.vision_model)


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass(frozen=True)
class ChapterSpec:
    chapter_id: str
    title: str
    presenter: str
    diagram_stages: str
    purpose: str
    execution_mode: str
    claim_boundary: str
    command: tuple[str, ...] = ()
    output_dir: Path | None = None
    requirements: tuple[str, ...] = ()
    required: bool = True


@dataclass
class ChapterResult:
    chapter_id: str
    title: str
    status: ChapterStatus
    execution_mode: str
    claim_boundary: str
    presenter: str
    diagram_stages: str
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    started_at: str = ""
    ended_at: str = ""
    duration_s: float = 0.0
    validation_checks: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    log: str = ""
    reason: str = ""


@dataclass(frozen=True)
class CoverageItem:
    component_id: str
    name: str
    module: str
    chapter_id: str
    level: str
    claim: str


COVERAGE: tuple[CoverageItem, ...] = (
    CoverageItem(
        "intent",
        "Intent planner and provenance",
        "src/planner/intent_planner.py",
        "canonical",
        "direct",
        "Utterance becomes a typed GoalSpec with its actual source recorded.",
    ),
    CoverageItem(
        "goal_skill",
        "Goal-to-Skill selection",
        "src/planner/goal_skill_selector.py",
        "canonical",
        "direct",
        "The composite prepare_and_confirm_room Skill is selected from the library.",
    ),
    CoverageItem(
        "skill_library",
        "Declarative Skill library",
        "src/skill_library/library.py",
        "canonical",
        "direct",
        "Defaults, backends, safety, timeout and postconditions come from the Skill contract.",
    ),
    CoverageItem(
        "agent_planner",
        "Unified forward/recovery AgentPlanner",
        "src/planner/agent_planner.py",
        "canonical",
        "direct",
        "One PlannerPort selects both forward and recovery primitives.",
    ),
    CoverageItem(
        "plan_validator",
        "Primitive plan validator",
        "src/runtime/plan_validator.py",
        "canonical",
        "direct",
        "Planner output is checked against fresh offered affordances and allowed actions.",
    ),
    CoverageItem(
        "dom_perception",
        "DOM transducer and PAM",
        "src/perception/dom_transducer.py",
        "canonical",
        "direct",
        "Live HTML is reduced to normalized DOM affordances.",
    ),
    CoverageItem(
        "td_discovery",
        "Thing Directory and TD parsing",
        "src/runtime/live_environment.py + src/perception/td_affordance_parser.py",
        "canonical",
        "direct",
        "Live TDs, affordances and forms are fetched and parsed at runtime.",
    ),
    CoverageItem(
        "cognitive_map",
        "Cognitive map",
        "src/runtime/cognitive_map.py",
        "canonical",
        "direct",
        "Fresh source-attributed assertions form the planner-visible state.",
    ),
    CoverageItem(
        "backend_router",
        "Runtime backend routing",
        "src/runtime/backend_router.py",
        "canonical",
        "direct",
        "Each primitive is routed to an eligible DOM or WoT executor.",
    ),
    CoverageItem(
        "dom_executor",
        "DOM executor",
        "src/effectors/dom_executor.py",
        "canonical",
        "direct",
        "Booking fields and the protected commit use resolved DOM affordances.",
    ),
    CoverageItem(
        "wot_executor",
        "WoT executor",
        "src/effectors/wot_executor.py",
        "canonical",
        "direct",
        "Lights, projector and thermostat are changed through TD forms and read back.",
    ),
    CoverageItem(
        "cim",
        "Continuous Interaction Manager",
        "src/runtime/continuous_interaction_manager.py",
        "canonical",
        "direct",
        "The canonical observe-plan-act-verify-recover loop owns execution state.",
    ),
    CoverageItem(
        "verification",
        "Fresh postcondition verification",
        "src/verification/postcondition_checker.py",
        "canonical",
        "direct",
        "Executor success is never accepted without a fresh observation.",
    ),
    CoverageItem(
        "safety",
        "Safety and human confirmation",
        "src/safety/unsafe_action_detector.py",
        "canonical",
        "direct",
        "The high-safety final booking action pauses for operator authority.",
    ),
    CoverageItem(
        "intervention",
        "Intervention broker and ledger",
        "src/runtime/intervention.py",
        "canonical",
        "direct",
        "Approval/takeover decisions and re-observation are auditable.",
    ),
    CoverageItem(
        "isolation",
        "Browser/WoT episode isolation",
        "src/isolation/episode.py",
        "canonical",
        "direct",
        "A fresh session and room checkpoint are restored in finally.",
    ),
    CoverageItem(
        "input_lease",
        "Cooperative software input lease",
        "src/isolation/input_lease.py",
        "canonical",
        "direct",
        "Agent executors require the agent lease; OS input isolation is not claimed.",
    ),
    CoverageItem(
        "typed_recovery",
        "Typed failure and recovery handoff",
        "src/runtime/action_context.py",
        "canonical",
        "direct",
        "The injected booking obstruction is inferred from failed execution plus fresh observation.",
    ),
    CoverageItem(
        "recovery_cascade",
        "Four-tier recovery cascade",
        "src/recovery/recovery_cascade.py",
        "recovery",
        "direct",
        "Five controlled live scenes cover replan, active perception, rollback and safe handling.",
    ),
    CoverageItem(
        "fusion",
        "Epistemic fusion and conflict handling",
        "src/verification/conflict_detector.py",
        "recovery",
        "direct",
        "A dashboard/device disagreement triggers an active-perception resolution.",
    ),
    CoverageItem(
        "active_perception",
        "Active perception",
        "src/verification/active_perception.py",
        "recovery",
        "direct",
        "Runtime requests a bounded fresh scan when sources conflict.",
    ),
    CoverageItem(
        "system1",
        "System-1 reflex cache",
        "src/effectors/system1_reflex_library.py",
        "runtime_lab",
        "direct",
        "The complete profile includes a warm-up and verified cache-hit repeat.",
    ),
    CoverageItem(
        "intent_model",
        "Text-model value",
        "src/planner/intent_planner.py",
        "models",
        "model_or_recorded",
        "Rules and the configured model are compared on the same smart-room requests.",
    ),
    CoverageItem(
        "vlm",
        "Vision verification",
        "src/perception/vlm_observer.py",
        "models",
        "model_or_recorded",
        "A screenshot contradicts a DOM false success with explicit model provenance.",
    ),
    CoverageItem(
        "visual_geometry",
        "Measured visual geometry",
        "src/perception/visual_geometry.py",
        "visual",
        "direct",
        "Bounding boxes are measured in the live browser, not authored in fixtures.",
    ),
    CoverageItem(
        "som",
        "Set-of-Marks",
        "src/perception/som_parser.py",
        "visual",
        "direct",
        "Measured boxes become numbered marks.",
    ),
    CoverageItem(
        "mark_selector",
        "Mark selection",
        "src/planner/mark_selector.py",
        "visual",
        "direct",
        "The selector returns a current mark ID and records whether it was model or heuristic.",
    ),
    CoverageItem(
        "visual_executor",
        "Visual executor",
        "src/effectors/visual_executor.py",
        "visual",
        "direct",
        "The click is executed at the selected mark center without a selector.",
    ),
    CoverageItem(
        "adaptation",
        "Trace-driven adaptation",
        "src/adaptation/pattern_miner.py",
        "adaptation",
        "synthetic_white_box",
        "Repeated labelled failures produce a candidate proposal.",
    ),
    CoverageItem(
        "release_gate",
        "Human review release gate",
        "src/adaptation/release_gate.py",
        "adaptation",
        "synthetic_white_box",
        "The proposal remains unapproved, non-auto-applied, and review-gated.",
    ),
    CoverageItem(
        "vam_boundary",
        "End-to-end VAM adapter",
        "src/vam/vam_adapter.py",
        "visual",
        "prototype_boundary",
        "The visual chapter proves SoM-to-execution plumbing, not a canonical end-to-end VAM replacement.",
    ),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def capability_snapshot() -> CapabilitySnapshot:
    browser, browser_detail = check_capability("browser")
    smart_room, smart_room_detail = check_capability("smart_room")
    text = available_client()
    vision = available_vision_client()
    return CapabilitySnapshot(
        browser=browser,
        browser_detail=browser_detail,
        smart_room=smart_room,
        smart_room_detail=smart_room_detail,
        text_model=getattr(text, "name", "") if text is not None else "",
        vision_model=getattr(vision, "name", "") if vision is not None else "",
        configured_key_names=tuple(configured_key_names()),
        recorded_model_evidence=RECORDED_MODEL_REPORT.is_file() and RECORDED_MODEL_VIDEO.is_file(),
    )


def resolve_model_mode(requested: ModelMode, capabilities: CapabilitySnapshot) -> ModelMode:
    if requested != "auto":
        return requested
    if capabilities.models_live:
        return "live"
    if capabilities.recorded_model_evidence:
        return "recorded"
    return "skip"


def _http_json(url: str, *, timeout: float = 2.0, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local demo URLs by design
        if not 200 <= int(response.status) < 300:
            raise RuntimeError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _http_status(url: str, *, timeout: float = 2.0) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - local demo URLs by design
        return int(response.status)


def strict_preflight(config: FinalDemoConfig, capabilities: CapabilitySnapshot) -> list[PreflightCheck]:
    """Probe the exact services the combined run needs without changing them."""

    checks = [PreflightCheck("playwright/chromium", capabilities.browser, capabilities.browser_detail)]
    probes: list[tuple[str, Callable[[], Any], Callable[[Any], bool]]] = [
        ("dashboard HTTP", lambda: _http_status(config.dashboard_url), lambda value: value == 200),
        (
            "Thing Directory",
            lambda: _http_json(config.thing_directory_url),
            lambda value: isinstance(value, list) and bool(value),
        ),
        (
            "control-plane state",
            lambda: _http_json(f"{config.control_url.rstrip('/')}/state"),
            lambda value: isinstance(value, dict) and isinstance(value.get("state"), dict),
        ),
        (
            "WoT property read",
            lambda: _http_json(
                f"{config.wot_base_url.rstrip('/')}/thermostat/properties/targetTemperature",
                headers={"X-API-Key": "demo"},
            ),
            lambda value: isinstance(value, int | float),
        ),
    ]
    for name, probe, valid in probes:
        try:
            value = probe()
            ok = bool(valid(value))
            detail = "reachable and semantically valid" if ok else f"unexpected response: {type(value).__name__}"
        except Exception as exc:  # preflight is a report, not a traceback
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        checks.append(PreflightCheck(name, ok, detail))

    selected_model_mode = resolve_model_mode(config.model_mode, capabilities)
    if selected_model_mode == "live":
        checks.append(
            PreflightCheck(
                "text model configured",
                bool(capabilities.text_model),
                capabilities.text_model or "none",
            )
        )
        checks.append(
            PreflightCheck(
                "vision model configured",
                bool(capabilities.vision_model),
                capabilities.vision_model or "none",
            )
        )
    elif selected_model_mode == "recorded":
        checks.append(
            PreflightCheck(
                "recorded model evidence",
                capabilities.recorded_model_evidence,
                f"{RECORDED_MODEL_REPORT.relative_to(REPO_ROOT)} + {RECORDED_MODEL_VIDEO.relative_to(REPO_ROOT)}",
            )
        )
    if config.canonical_model:
        checks.append(
            PreflightCheck(
                "canonical text model",
                bool(capabilities.text_model),
                capabilities.text_model or "requested but not configured",
            )
        )
    return checks


def _chapter_dir(config: FinalDemoConfig, index: int, chapter_id: str) -> Path:
    return config.output_dir / f"{index:02d}_{chapter_id}"


def build_chapters(config: FinalDemoConfig, capabilities: CapabilitySnapshot) -> list[ChapterSpec]:
    """Build exact child commands; no command is executed here."""

    python = sys.executable
    model_mode = resolve_model_mode(config.model_mode, capabilities)
    delays = {
        "step": "0.05" if config.fast else "0.8",
        "settle": "0.05" if config.fast else "0.25",
        "fault": "0.05" if config.fast else "0.2",
        "model_pace": "0.05" if config.fast else "1.0",
        "model_type": "0" if config.fast else "0.06",
        "model_hold": "0.1" if config.fast else "2.0",
    }
    all_specs: list[ChapterSpec] = []

    canonical_dir = _chapter_dir(config, 1, "canonical")
    canonical = [
        python,
        "scripts/run_supervised_smartroom_demo.py",
        "--utterance",
        config.utterance,
        "--evidence",
        str(canonical_dir / "episode.json"),
        "--step-delay",
        delays["step"],
        "--settle-delay",
        delays["settle"],
        "--fault-settle-delay",
        delays["fault"],
        "--inject-booking-obstruction",
        "--dashboard-url",
        config.dashboard_url,
        "--thing-directory-url",
        config.thing_directory_url,
        "--wot-base-url",
        config.wot_base_url,
        "--control-url",
        config.control_url,
    ]
    if config.headless:
        canonical.append("--headless")
    if config.auto_approve:
        canonical.append("--auto-approve")
    if config.canonical_model:
        canonical.append("--use-model")
    all_specs.append(
        ChapterSpec(
            "canonical",
            "One request through the canonical supervised pipeline",
            "All three (handoff: intent → execution → recovery)",
            "1–5",
            "Execute the full smart-room request, approve the protected commit, recover from one observed modal, verify, and restore.",
            "live canonical RuntimeEpisodeRunner",
            "Intent, GoalSpec, Skill, AgentPlanner, DOM/WoT execution, CIM, verification, intervention and isolation are one shared runtime episode.",
            tuple(canonical),
            canonical_dir,
            ("browser", "smart_room"),
        )
    )

    runtime_dir = _chapter_dir(config, 2, "runtime_lab")
    runtime = [
        python,
        "-m",
        "src.pipeline",
        "--live-demo",
        "--output-dir",
        str(runtime_dir),
        "--dashboard-url",
        config.dashboard_url,
        "--thing-directory-url",
        config.thing_directory_url,
        "--wot-base-url",
        config.wot_base_url,
        "--control-url",
        config.control_url,
    ]
    if not config.headless:
        runtime.append("--headed")
    all_specs.append(
        ChapterSpec(
            "runtime_lab",
            "Complete Runtime laboratory: retry, rollback, conflict and System-1",
            "Presenter 3",
            "4–5",
            "Exercise the live Runtime cases that are too dense for the main story, including a verified System-1 repeat.",
            "live Runtime laboratory",
            "Production CIM and live environment; controlled cases, not an open-world benchmark.",
            tuple(runtime),
            runtime_dir,
            ("browser", "smart_room"),
        )
    )

    recovery_index = 3 if config.profile == "complete" else 2
    recovery_dir = _chapter_dir(config, recovery_index, "recovery")
    recovery = [
        python,
        "scripts/run_smart_room_five_recovery_demo.py",
        "--output-dir",
        str(recovery_dir),
        "--dashboard-url",
        config.dashboard_url,
        "--directory-url",
        config.directory_base_url,
        "--wot-url",
        config.wot_base_url,
        "--control-url",
        config.control_url,
    ]
    if config.headless:
        recovery.append("--headless")
    all_specs.append(
        ChapterSpec(
            "recovery",
            "Five controlled recovery families",
            "Presenter 3",
            "5",
            "Show obstruction, session expiry, rollback, source conflict and ineffective-affordance handling.",
            "live focused recovery campaign",
            "Production CIM/environment/verification with an explicitly simulated upstream recovery client; not the canonical RuntimeEpisodeRunner composition or a production VLM.",
            tuple(recovery),
            recovery_dir,
            ("browser", "smart_room"),
        )
    )

    model_index = recovery_index + 1
    model_dir = _chapter_dir(config, model_index, "models")
    model_command: list[str] = []
    model_execution = "skipped by request"
    model_requirements: tuple[str, ...] = ()
    model_required = False
    if model_mode == "live":
        model_command = [
            python,
            "scripts/run_llm_demo.py",
            "--out",
            str(model_dir),
            "--pace",
            delays["model_pace"],
            "--type-delay",
            delays["model_type"],
            "--hold",
            delays["model_hold"],
            "--dashboard",
            config.dashboard_url,
            "--directory",
            config.directory_base_url,
        ]
        if config.headless:
            model_command.append("--headless")
        model_execution = "live text LLM + live VLM"
        model_requirements = ("browser", "smart_room", "text_model", "vision_model")
        model_required = True
    elif model_mode == "recorded":
        model_execution = "checked-in recorded model evidence"
        model_requirements = ("recorded_models",)
        model_required = True
    all_specs.append(
        ChapterSpec(
            "models",
            "What the text and vision models add",
            "Presenter 1",
            "1–3 plus verification",
            "Compare bounded rules with a model and show visual detection of a DOM false success.",
            model_execution,
            "Separate measured smart-room model-value chapter. VLM supplies verification evidence; it is not an end-to-end VAM controlling canonical stages 1–3.",
            tuple(model_command),
            model_dir,
            model_requirements,
            required=model_required,
        )
    )

    visual_index = model_index + 1
    visual_dir = _chapter_dir(config, visual_index, "visual")
    visual = [
        python,
        "scripts/run_visual_grounding_smoke.py",
        "--url",
        config.dashboard_url,
        "--label-hint",
        "Book Room",
        "--goal",
        "confirm the room booking",
        "--expect-text",
        "booked:",
        "--out",
        str(visual_dir),
    ]
    if not config.headless:
        visual.append("--headed")
    if config.canonical_model:
        visual.append("--use-model")
    all_specs.append(
        ChapterSpec(
            "visual",
            "Measured Set-of-Marks to visual execution",
            "Presenter 1",
            "Optional VAM path around 1–3",
            "Capture the live dashboard, measure geometry, select a mark, and execute through VisualExecutor.",
            "live visual prototype path",
            "Real screenshot/geometry/mark/executor path. It is a focused prototype, not the canonical smart-room planner and not proof of an end-to-end VAM.",
            tuple(visual),
            visual_dir,
            ("browser", "smart_room"),
        )
    )

    adaptation_index = visual_index + 1
    adaptation_dir = _chapter_dir(config, adaptation_index, "adaptation")
    adaptation = [python, "scripts/run_adaptation_demo.py", "--output-dir", str(adaptation_dir)]
    all_specs.append(
        ChapterSpec(
            "adaptation",
            "From failure traces to a review-gated proposal",
            "Presenter 3",
            "Dashed learning path",
            "Show classification, repeated-pattern mining, and a proposal that cannot auto-apply.",
            "deterministic white-box adaptation evidence",
            "Synthetic repeated incidents exercise the adaptation contract. The live episode alone has insufficient support; no Skill is changed automatically.",
            tuple(adaptation),
            adaptation_dir,
        )
    )

    profile_ids = (
        ("canonical", "recovery", "models", "visual", "adaptation")
        if config.profile == "presentation"
        else ("canonical", "runtime_lab", "recovery", "models", "visual", "adaptation")
    )
    selected = set(config.only or profile_ids)
    unknown = selected - {spec.chapter_id for spec in all_specs}
    if unknown:
        raise ValueError(f"unknown chapters: {', '.join(sorted(unknown))}")
    outside_profile = selected - set(profile_ids)
    if outside_profile:
        required_profile = "complete" if "runtime_lab" in outside_profile else config.profile
        raise ValueError(
            f"chapters unavailable in profile {config.profile!r}: {', '.join(sorted(outside_profile))}; "
            f"use --profile {required_profile}"
        )
    return [spec for spec in all_specs if spec.chapter_id in selected and spec.chapter_id in profile_ids]


def requirement_status(name: str, capabilities: CapabilitySnapshot) -> tuple[bool, str]:
    mapping = {
        "browser": (capabilities.browser, capabilities.browser_detail),
        "smart_room": (capabilities.smart_room, capabilities.smart_room_detail),
        "text_model": (bool(capabilities.text_model), capabilities.text_model or "no text model configured"),
        "vision_model": (bool(capabilities.vision_model), capabilities.vision_model or "no vision model configured"),
        "recorded_models": (
            capabilities.recorded_model_evidence,
            (
                "checked-in report and video present"
                if capabilities.recorded_model_evidence
                else "recorded report/video missing"
            ),
        ),
    }
    return mapping.get(name, (False, f"unknown requirement {name}"))


def _execute_command(spec: ChapterSpec, *, interactive: bool) -> tuple[int, str]:
    if not spec.command:
        return 0, ""
    assert spec.output_dir is not None
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = spec.output_dir / "chapter.log"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if interactive:
        completed = subprocess.run(spec.command, cwd=REPO_ROOT, env=env, check=False)
        log_path.write_text(
            "Interactive child inherited the presentation terminal; structured evidence is stored beside this file.\n",
            encoding="utf-8",
        )
        return completed.returncode, str(log_path)

    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            spec.command,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        returncode = process.wait()
    return returncode, str(log_path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return value


def validate_chapter(spec: ChapterSpec) -> tuple[bool, list[str]]:
    """Validate scientific outcomes, not only child-process exit codes."""

    checks: list[tuple[bool, str]] = []
    root = spec.output_dir or REPO_ROOT
    try:
        if spec.chapter_id == "canonical":
            report = _read_json(root / "episode.json")
            decisions = report.get("agent_planner", {}).get("decisions", [])
            transitions = report.get("transitions", [])
            failed_ids = {
                item.get("transition_id")
                for item in transitions
                if isinstance(item, dict) and item.get("success") is False
            }
            linked_recoveries = [
                item
                for item in transitions
                if isinstance(item, dict)
                and item.get("success") is True
                and item.get("recovery_of_transition_id") in failed_ids
            ]
            checks = [
                (report.get("result", {}).get("verified") is True, "final goal freshly verified"),
                (set(report.get("surfaces_used", [])) == {"dom", "wot"}, "both DOM and WoT surfaces used"),
                (report.get("room_state_restored") is True, "room checkpoint restored"),
                (report.get("controlled_fault", {}).get("applied") is True, "booking obstruction applied once"),
                (any(item.get("mode") == "recovery" for item in decisions), "same AgentPlanner entered recovery mode"),
                (bool(failed_ids), "controlled failure was recorded"),
                (bool(linked_recoveries), "successful recovery links to the failed transition"),
                (bool(report.get("interventions")), "protected action produced an intervention record"),
            ]
            if "--use-model" in spec.command:
                checks += [
                    (report.get("agent_planner", {}).get("model_configured") is True, "canonical model configured"),
                    (
                        any(item.get("is_model_derived") is True for item in decisions),
                        "canonical planner recorded a model-derived decision",
                    ),
                ]
        elif spec.chapter_id == "runtime_lab":
            report = _read_json(root / "episode_report.json")
            by_name = {case.get("case"): case for case in report.get("cases", [])}
            reflex = by_name.get("system1_reflex_repeat", {})
            checks = [
                (report.get("all_evidence_checks_passed") is True, "all live Runtime evidence checks passed"),
                (
                    reflex.get("result", {}).get("system1_cache_hit") is True
                    or reflex.get("evidence_checks", {}).get("repeat_cache_hit") is True,
                    "verified System-1 repeat hit the cache",
                ),
            ]
        elif spec.chapter_id == "recovery":
            report = _read_json(root / "smart_room_recovery_report.json")
            summary = report.get("summary", {})
            checks = [
                (summary.get("scene_count") == 5, "all five recovery families ran"),
                (summary.get("all_final_oracles_verified") is True, "all independent final oracles passed"),
                (summary.get("fault_labels_hidden_from_planner") is True, "fault labels were hidden from planner"),
            ]
        elif spec.chapter_id == "models":
            report_path = root / "llm_demo.json" if spec.command else RECORDED_MODEL_REPORT
            report = _read_json(report_path)
            checks = [
                (int(report.get("model_solved", 0)) == 4, "text model solved all four bounded scenes"),
                (int(report.get("false_successes_caught", 0)) >= 1, "VLM caught the controlled DOM false success"),
            ]
            if not spec.command:
                checks.append((RECORDED_MODEL_VIDEO.is_file(), "recorded model video is present"))
        elif spec.chapter_id == "visual":
            report = _read_json(root / "trace.json")
            checks = [
                (int(report.get("measured_boxes", 0)) > 0, "geometry measured in live browser"),
                (bool(report.get("selection")), "a current mark ID was selected"),
                (report.get("executor") == "VisualExecutor", "production VisualExecutor performed the action"),
                (report.get("execution", {}).get("success") is True, "mark-center execution succeeded"),
                (report.get("effect_observed") is True, "independent visible effect observed"),
            ]
        elif spec.chapter_id == "adaptation":
            report = _read_json(root / "adaptation_report.json")
            proposals = _read_json(root / "policy_proposals.json")
            proposal = (proposals.get("proposals") or [{}])[0]
            checks = [
                (report.get("summary", {}).get("policy_proposals") == 1, "one repeated pattern produced a proposal"),
                (proposal.get("safe_to_auto_apply") is False, "proposal cannot auto-apply"),
                (proposal.get("release_gate", {}).get("approved") is False, "release gate requires review"),
            ]
    except (OSError, ValueError, json.JSONDecodeError, IndexError) as exc:
        return False, [f"validation could not read evidence: {type(exc).__name__}: {exc}"]
    passed = [detail for ok, detail in checks if ok]
    failed = [f"FAILED: {detail}" for ok, detail in checks if not ok]
    return not failed, [*passed, *failed]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_inventory(spec: ChapterSpec, config: FinalDemoConfig) -> list[dict[str, Any]]:
    if spec.chapter_id == "models" and not spec.command:
        files = [RECORDED_MODEL_REPORT, RECORDED_MODEL_VIDEO]
    elif spec.output_dir is not None and spec.output_dir.exists():
        files = sorted(path for path in spec.output_dir.rglob("*") if path.is_file())
    else:
        files = []
    inventory: list[dict[str, Any]] = []
    for path in files:
        try:
            relative = path.relative_to(config.output_dir)
            scope = "run"
        except ValueError:
            relative = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
            scope = "repository_evidence"
        inventory.append(
            {
                "path": str(relative),
                "scope": scope,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return inventory


def git_snapshot() -> dict[str, Any]:
    def command(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    status = command("status", "--short")
    return {"commit": command("rev-parse", "HEAD"), "dirty": bool(status), "status": status.splitlines()}


def coverage_report(chapters: Iterable[ChapterSpec], results: Iterable[ChapterResult]) -> list[dict[str, Any]]:
    selected = {chapter.chapter_id for chapter in chapters}
    statuses = {result.chapter_id: result.status for result in results}
    report: list[dict[str, Any]] = []
    for item in COVERAGE:
        status: ChapterStatus = statuses.get(item.chapter_id, "not_run")
        if item.chapter_id not in selected:
            status = "not_run"
        report.append({**asdict(item), "status": status})
    return report


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_presenter_cues(path: Path, chapters: Iterable[ChapterSpec]) -> None:
    lines = [
        "# Final presentation demo — live cue sheet",
        "",
        "Main request: **Book Room C at 15:30 and prepare it for my presentation.**",
        "",
        "At the canonical Tier-4 prompt, choose **`a` (approve)**. This lets the protected agent click proceed,",
        "the controlled modal obstruct it, and the same AgentPlanner demonstrate recovery. Choosing takeover",
        "would let the human bypass the automatic recovery scene.",
        "",
    ]
    for index, chapter in enumerate(chapters, start=1):
        lines += [
            f"## {index}. {chapter.title}",
            "",
            f"- Presenter: {chapter.presenter}",
            f"- Diagram stages: {chapter.diagram_stages}",
            f"- Say: {chapter.purpose}",
            f"- Boundary: {chapter.claim_boundary}",
            "",
        ]
    lines += [
        "## Closing sentence",
        "",
        "> This is one auditable observe–fuse–decide–act–verify loop across DOM and WoT, with typed recovery and human authority; visual/VLM and adaptation are explicit bounded extension chapters.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def print_preflight(checks: Iterable[PreflightCheck], capabilities: CapabilitySnapshot) -> None:
    print("\nFINAL PRESENTATION DEMO — PREFLIGHT")
    print("=" * 78)
    for check in checks:
        print(f"  {'OK ' if check.ok else 'NO '} {check.name:<25} {check.detail}")
    print(f"  {'INFO':<4} {'text model':<25} {capabilities.text_model or 'not configured'}")
    print(f"  {'INFO':<4} {'vision model':<25} {capabilities.vision_model or 'not configured'}")
    print("=" * 78)


CommandExecutor = Callable[[ChapterSpec, bool], tuple[int, str]]


def run_final_demo(
    config: FinalDemoConfig,
    *,
    capabilities: CapabilitySnapshot | None = None,
    executor: CommandExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    capabilities = capabilities or capability_snapshot()
    checks = strict_preflight(config, capabilities)
    print_preflight(checks, capabilities)
    if any(check.required and not check.ok for check in checks):
        return 2, {"status": "preflight_failed", "preflight": [asdict(check) for check in checks]}

    if config.output_dir.exists() and any(config.output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {config.output_dir}")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    chapters = build_chapters(config, capabilities)
    write_presenter_cues(config.output_dir / "presenter_cues.md", chapters)
    _write_json(config.output_dir / "preflight.json", [asdict(check) for check in checks])

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": config.output_dir.name,
        "status": "running",
        "started_at": now_iso(),
        "ended_at": "",
        "git": git_snapshot(),
        "configuration": {
            **asdict(config),
            "output_dir": str(config.output_dir),
            "effective_model_mode": resolve_model_mode(config.model_mode, capabilities),
        },
        "models": {
            "text": capabilities.text_model or None,
            "vision": capabilities.vision_model or None,
            "configured_key_names": list(capabilities.configured_key_names),
            "secret_values_recorded": False,
        },
        "preflight": [asdict(check) for check in checks],
        "chapters": [],
        "coverage": [],
        "claim_scope": {
            "environment": "local React dashboard plus simulated/time-scaled node-wot smart room",
            "input_isolation": "cooperative software lease; no OS-level keyboard/mouse isolation",
            "models": "VLM is a verification source; visual SoM is a prototype path, not canonical VAM",
            "learning": "review-gated proposal generation; no automatic Skill mutation",
        },
    }
    manifest_path = config.output_dir / "presentation_manifest.json"
    _write_json(manifest_path, manifest)

    runner = executor or (lambda spec, interactive: _execute_command(spec, interactive=interactive))
    results: list[ChapterResult] = []
    for index, spec in enumerate(chapters, start=1):
        missing = [name for name in spec.requirements if not requirement_status(name, capabilities)[0]]
        print(f"\n{'=' * 78}\n  CHAPTER {index}/{len(chapters)} — {spec.title}\n{'=' * 78}")
        print(f"  presenter : {spec.presenter}")
        print(f"  stages    : {spec.diagram_stages}")
        print(f"  boundary  : {spec.claim_boundary}\n")
        if config.pause_between_chapters and index > 1:
            try:
                input("Press Enter when the next presenter is ready ... ")
            except EOFError:
                pass

        if missing:
            reason = "; ".join(f"{name}: {requirement_status(name, capabilities)[1]}" for name in missing)
            result = ChapterResult(
                spec.chapter_id,
                spec.title,
                "failed" if spec.required else "skipped",
                spec.execution_mode,
                spec.claim_boundary,
                spec.presenter,
                spec.diagram_stages,
                reason=reason,
            )
        elif spec.chapter_id == "models" and not spec.command and "recorded" in spec.execution_mode:
            valid, validation = validate_chapter(spec)
            result = ChapterResult(
                spec.chapter_id,
                spec.title,
                "recorded" if valid else "failed",
                spec.execution_mode,
                spec.claim_boundary,
                spec.presenter,
                spec.diagram_stages,
                validation_checks=validation,
                artifacts=artifact_inventory(spec, config),
                reason="Live models unavailable or recorded mode selected; use the checked-in measured segment.",
            )
        elif not spec.command:
            result = ChapterResult(
                spec.chapter_id,
                spec.title,
                "skipped",
                spec.execution_mode,
                spec.claim_boundary,
                spec.presenter,
                spec.diagram_stages,
                reason="chapter disabled by configuration",
            )
        else:
            display_command = ["python", *spec.command[1:]] if spec.command[0] == sys.executable else list(spec.command)
            print("  $ " + " ".join(display_command) + "\n", flush=True)
            started_at = now_iso()
            started = time.monotonic()
            interactive = spec.chapter_id == "canonical" and not config.auto_approve
            returncode, log_path = runner(spec, interactive)
            duration = time.monotonic() - started
            valid, validation = validate_chapter(spec) if returncode == 0 else (False, ["child command failed"])
            result = ChapterResult(
                spec.chapter_id,
                spec.title,
                "passed" if returncode == 0 and valid else "failed",
                spec.execution_mode,
                spec.claim_boundary,
                spec.presenter,
                spec.diagram_stages,
                command=list(spec.command),
                returncode=returncode,
                started_at=started_at,
                ended_at=now_iso(),
                duration_s=round(duration, 3),
                validation_checks=validation,
                artifacts=artifact_inventory(spec, config),
                log=(str(Path(log_path).relative_to(config.output_dir)) if log_path else ""),
            )

        results.append(result)
        print(f"  => {result.status.upper()}")
        for check in result.validation_checks:
            print(f"     - {check}")
        if result.reason:
            print(f"     - {result.reason}")
        manifest["chapters"] = [asdict(item) for item in results]
        manifest["coverage"] = coverage_report(chapters, results)
        _write_json(manifest_path, manifest)
        if result.status == "failed" and not config.continue_on_error:
            break

    failed_required = {
        spec.chapter_id
        for spec in chapters
        if spec.required
        and next((item.status for item in results if item.chapter_id == spec.chapter_id), "not_run")
        not in {"passed", "recorded"}
    }
    manifest["status"] = "passed" if not failed_required else "failed"
    manifest["ended_at"] = now_iso()
    manifest["chapters"] = [asdict(item) for item in results]
    manifest["coverage"] = coverage_report(chapters, results)
    manifest["summary"] = {
        "selected_chapters": len(chapters),
        "passed": sum(item.status == "passed" for item in results),
        "recorded": sum(item.status == "recorded" for item in results),
        "skipped": sum(item.status == "skipped" for item in results),
        "failed": sum(item.status == "failed" for item in results),
        "required_failures": sorted(failed_required),
        "coverage_items_run": sum(item["status"] in {"passed", "recorded"} for item in manifest["coverage"]),
        "coverage_items_total": len(manifest["coverage"]),
    }
    _write_json(config.output_dir / "component_coverage.json", manifest["coverage"])
    _write_json(manifest_path, manifest)
    print(f"\n{'=' * 78}\n  FINAL DEMO: {manifest['status'].upper()}")
    print(
        f"  manifest : {manifest_path.relative_to(REPO_ROOT) if manifest_path.is_relative_to(REPO_ROOT) else manifest_path}"
    )
    print(
        f"  cues     : {(config.output_dir / 'presenter_cues.md').relative_to(REPO_ROOT) if config.output_dir.is_relative_to(REPO_ROOT) else config.output_dir / 'presenter_cues.md'}"
    )
    print(f"{'=' * 78}\n")
    return (0 if manifest["status"] == "passed" else 1), manifest


def port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    """Small public helper used by tests and future custom preflight UIs."""

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


__all__ = [
    "COVERAGE",
    "CapabilitySnapshot",
    "ChapterResult",
    "ChapterSpec",
    "FinalDemoConfig",
    "PreflightCheck",
    "artifact_inventory",
    "build_chapters",
    "capability_snapshot",
    "coverage_report",
    "port_open",
    "resolve_model_mode",
    "run_final_demo",
    "strict_preflight",
    "validate_chapter",
]
