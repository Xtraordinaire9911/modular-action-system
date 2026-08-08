"""Where each part of the agent lives, for walking through the code.

The supervisor asked to be shown the components directly: where the planner is,
where the thing that reads affordances is, where the screenshot is taken. This
answers that in one place, and every entry is checked against the working tree
so the map cannot quietly drift from the code it describes.

Ownership is recorded per component because the walkthrough doubles as a review
of who built what.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Component:
    """One part of the architecture, and the file a reviewer should open."""

    layer: str
    name: str
    path: str
    symbol: str
    does: str
    owner: str

    @property
    def file(self) -> Path:
        return REPO_ROOT / self.path

    @property
    def exists(self) -> bool:
        return self.file.is_file()


# Ordered as the loop runs, so reading top to bottom follows one episode.
COMPONENTS: list[Component] = [
    Component(
        "1 PERCEIVE",
        "DOM transducer",
        "src/perception/dom_transducer.py",
        "DomTransducer.transduce",
        "Turns live HTML into a Page Affordance Model: a stable selector, label and "
        "action type per interactive element, ranked by how reliable the locator is.",
        "Ruiyao",
    ),
    Component(
        "1 PERCEIVE",
        "Page Affordance Model",
        "src/perception/page_affordance_model.py",
        "PageAffordanceModel",
        "The perception output the rest of the system consumes. Nothing downstream " "reads raw HTML.",
        "Ruiyao",
    ),
    Component(
        "1 PERCEIVE",
        "WoT Thing Description parser",
        "src/perception/td_affordance_parser.py",
        "TdAffordanceParser.parse",
        "Parses W3C Thing Descriptions at runtime into the same affordance shape, so "
        "devices and web pages present one interface to the planner.",
        "Ruiyao",
    ),
    Component(
        "1 PERCEIVE",
        "Screenshot and browser session",
        "src/perception/browser_session.py",
        "BrowserSession.screenshot / .state / .new_episode",
        "Owns the isolated browser context, takes the screenshot, and starts each "
        "episode in a fresh context so no state leaks between runs.",
        "Ruiyao",
    ),
    Component(
        "1 PERCEIVE",
        "Visual geometry",
        "src/perception/visual_geometry.py",
        "attach_measured_bboxes",
        "Measures every element's real position in the live browser. An element that "
        "cannot be measured gets no mark, so no mark is ever invented.",
        "Ruiyao",
    ),
    Component(
        "1 PERCEIVE",
        "Set-of-Marks",
        "src/perception/som_parser.py",
        "marks_from_affordances / select_mark",
        "Builds the numbered visual marks a vision model would choose between, and "
        "resolves a chosen mark back to a screen position.",
        "Ruiyao",
    ),
    Component(
        "2 PLAN",
        "Task planner (goal to skills)",
        "src/runtime/task_planner.py",
        "TaskPlanner",
        "Deliberative layer: converts a structured GoalSpec into an ordered skill " "sequence.",
        "Yixin",
    ),
    Component(
        "2 PLAN",
        "System-2 planner",
        "src/runtime/system2_planner.py",
        "System2Planner",
        "The slower reasoning path used when the reflex route is not enough.",
        "Yixin",
    ),
    Component(
        "2 PLAN",
        "Plan validator",
        "src/runtime/plan_validator.py",
        "PlanValidator",
        "Rejects a plan before anything is executed if it violates the goal spec.",
        "Yixin",
    ),
    Component(
        "2 PLAN",
        "Skill to primitive actions",
        "src/runtime/primitive_action.py",
        "PrimitiveAction",
        "Lowest planning layer: expands a skill into the concrete actions an effector " "can run.",
        "Yixin",
    ),
    Component(
        "3 ACT",
        "Backend router",
        "src/backend_router/router.py",
        "BackendRouter.route",
        "Chooses DOM, WoT or visual execution per action using cost, reliability and "
        "latency rather than a fixed preference.",
        "Ruiyao",
    ),
    Component(
        "3 ACT",
        "DOM executor",
        "src/effectors/dom_executor.py",
        "DomExecutor.execute",
        "Executes an action against a resolved DOM locator.",
        "Ruiyao",
    ),
    Component(
        "3 ACT",
        "WoT executor",
        "src/effectors/wot_executor.py",
        "WotExecutor.execute",
        "Executes device actions through Thing Description forms, honouring the "
        "security scheme and rate limits the TD declares.",
        "Ruiyao",
    ),
    Component(
        "3 ACT",
        "Visual executor",
        "src/effectors/visual_executor.py",
        "VisualExecutor.execute",
        "Acts on a mark_id rather than raw coordinates, so the visual path cannot "
        "click a position nothing was detected at.",
        "Ruiyao",
    ),
    Component(
        "4 VERIFY",
        "Postcondition checker",
        "src/verification/postcondition_checker.py",
        "PostconditionChecker",
        "Decides whether the world actually reached the state the skill promised.",
        "Ruiyao",
    ),
    Component(
        "4 VERIFY",
        "Oracle verifier",
        "src/verification/oracle_verifier.py",
        "OracleVerifier",
        "Re-observes independently of the executor, so a backend reporting success "
        "cannot by itself count as task success.",
        "Yixin",
    ),
    Component(
        "4 VERIFY",
        "Conflict detector",
        "src/verification/conflict_detector.py",
        "ConflictDetector",
        "Finds disagreement between what the DOM says and what the device says.",
        "Ruiyao",
    ),
    Component(
        "5 RECOVER",
        "Recovery cascade",
        "src/recovery/recovery_cascade.py",
        "RecoveryCascade.handle",
        "Escalates through retry, reroute, rollback and human escalation instead of " "failing at the first error.",
        "Fadi / Yixin",
    ),
    Component(
        "5 RECOVER",
        "Supervised takeover",
        "src/recovery/supervised_takeover.py",
        "SupervisedTakeover",
        "Tier 4 as an observable handover: the run pauses, a human resumes it, and "
        "whether they changed anything is recorded as a correction rate.",
        "Ruiyao",
    ),
    Component(
        "6 LEARN",
        "Experience compiler",
        "src/adaptation/experience_compiler.py",
        "ExperienceCompiler.compile_failure",
        "Turns a failed episode into a structured experience: which boundary failed, "
        "what to do now, what to change long term.",
        "Yixin",
    ),
    Component(
        "6 LEARN",
        "Skill proposal miner",
        "src/adaptation/skill_proposal.py",
        "SkillProposalMiner.mine",
        "Mines repeated transition chains into candidate skills. Proposals stay "
        "review-gated; nothing is applied automatically.",
        "Yixin",
    ),
    Component(
        "7 EVALUATE",
        "Cross-environment metric",
        "evaluation/cross_env_eval.py",
        "aggregate",
        "Per-environment and overall task success rate, the evidence for the " "generalisation claim.",
        "Ruiyao",
    ),
    Component(
        "7 EVALUATE",
        "External benchmark suites",
        "src/benchmarks/miniwob_tasks.py",
        "DEMO_TASKS / MiniwobController",
        "MiniWoB++ tasks plus the local WebArena-style environments the agent is " "measured on.",
        "Ruiyao",
    ),
    Component(
        "8 DEMO",
        "Narrated agent loop",
        "scripts/run_agent_loop_demo.py",
        "run_scene",
        "The loop end to end across three environments, narrated in-page with an "
        "injected fault so recovery is shown rather than described.",
        "Ruiyao",
    ),
    Component(
        "8 DEMO",
        "Demo registry",
        "src/demos/registry.py",
        "DEMOS / status_of",
        "Every demo in one list, each reporting whether it can run on this machine.",
        "Ruiyao",
    ),
]


def layers() -> list[str]:
    seen: list[str] = []
    for component in COMPONENTS:
        if component.layer not in seen:
            seen.append(component.layer)
    return seen


def missing() -> list[Component]:
    """Entries whose file is absent — the map drifting from the code."""
    return [c for c in COMPONENTS if not c.exists]


def by_owner() -> dict[str, list[Component]]:
    grouped: dict[str, list[Component]] = {}
    for component in COMPONENTS:
        grouped.setdefault(component.owner, []).append(component)
    return grouped


__all__ = ["COMPONENTS", "Component", "REPO_ROOT", "by_owner", "layers", "missing"]
