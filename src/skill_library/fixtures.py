"""Task fixture compatibility layer for offline runtime evaluation.

The latest scaffold keeps skill contracts in ``config/skills_seed.json`` and no
longer ships separate fixture JSON files. The robustness and randomized
evaluation harnesses still need a small task-level fixture API, so this module
provides built-in smart-room defaults and optionally loads legacy JSON files
when present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.contracts.types import SkillCall
from src.skill_library.library import SkillLibraryError

TASK_FIXTURES_PATH = "fixtures/task_fixtures.json"
EXPECTED_SEQUENCES_PATH = "fixtures/expected_skill_sequences.json"
FAILURE_PROFILES_PATH = "fixtures/failure_profiles.json"


@dataclass(frozen=True)
class TaskFixture:
    task_id: str
    user_goal: str
    expected_skill_sequence: list[str]
    initial_state: dict[str, Any]
    expected_final_state: dict[str, Any]
    allowed_failure_profile: str | None = None


@dataclass(frozen=True)
class ExpectedStep:
    skill_id: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_skill_call(self) -> SkillCall:
        return SkillCall(skill_id=self.skill_id, params=dict(self.params))


@dataclass(frozen=True)
class FailureProfile:
    failure_id: str
    target: str
    description: str
    expected_behavior: str
    expected_recovery_tier: int
    injection: dict[str, Any] = field(default_factory=dict)


_DEFAULT_TASK = TaskFixture(
    task_id="prepare_room_A_1400",
    user_goal="Prepare Room A for a 14:00 presentation.",
    expected_skill_sequence=[
        "confirm_booking",
        "turn_on_projector",
        "set_temperature",
        "set_lighting",
        "verify_readiness",
    ],
    initial_state={
        "room": "A",
        "booked": False,
        "projector": "off",
        "target_temperature": 20,
        "current_temperature": 20,
        "light_brightness": 100,
    },
    expected_final_state={
        "booked": True,
        "projector": "on",
        "target_temperature": 22,
        "light_brightness": 30,
        "readiness": True,
    },
)

_DEFAULT_SEQUENCE = [
    ExpectedStep("confirm_booking", {"room": "A", "time": "14:00"}),
    ExpectedStep("turn_on_projector", {"room": "A"}),
    ExpectedStep("set_temperature", {"room": "A", "target": 22}),
    ExpectedStep("set_lighting", {"room": "A", "brightness": 30}),
    ExpectedStep("verify_readiness", {"room": "A"}),
]

_DEFAULT_FAILURE_PROFILES = [
    FailureProfile(
        failure_id="dom_selector_mutation",
        target="confirm_booking",
        description="The DOM selector for the booking confirmation changed.",
        expected_behavior="reroute to a visual affordance",
        expected_recovery_tier=2,
        injection={"failure_reason": "selector_not_found"},
    ),
    FailureProfile(
        failure_id="wot_postcondition_mismatch",
        target="set_temperature",
        description="The WoT action returns success but observed state does not change.",
        expected_behavior="verify final state and recover instead of trusting HTTP success",
        expected_recovery_tier=3,
        injection={"failure_reason": "postcondition_mismatch"},
    ),
]


def load_task_fixtures(path: str | Path = TASK_FIXTURES_PATH) -> list[TaskFixture]:
    target = Path(path)
    if not target.exists():
        return [_DEFAULT_TASK]
    payload = _read_json(target)
    tasks = payload["tasks"] if isinstance(payload, dict) else payload
    return [
        TaskFixture(
            task_id=item["task_id"],
            user_goal=item["user_goal"],
            expected_skill_sequence=list(item["expected_skill_sequence"]),
            initial_state=dict(item.get("initial_state", {})),
            expected_final_state=dict(item.get("expected_final_state", {})),
            allowed_failure_profile=item.get("allowed_failure_profile"),
        )
        for item in tasks
    ]


def get_task_fixture(task_id: str, path: str | Path = TASK_FIXTURES_PATH) -> TaskFixture:
    for fixture in load_task_fixtures(path):
        if fixture.task_id == task_id:
            return fixture
    raise SkillLibraryError(f"unknown task fixture: {task_id}")


def load_expected_skill_sequences(path: str | Path = EXPECTED_SEQUENCES_PATH) -> dict[str, list[ExpectedStep]]:
    target = Path(path)
    if not target.exists():
        return {_DEFAULT_TASK.task_id: list(_DEFAULT_SEQUENCE)}
    payload = _read_json(target)
    sequences = payload["sequences"] if isinstance(payload, dict) and "sequences" in payload else payload
    return {
        task_id: [ExpectedStep(skill_id=step["skill_id"], params=dict(step.get("params", {}))) for step in steps]
        for task_id, steps in sequences.items()
    }


def expected_skill_calls(task_id: str, path: str | Path = EXPECTED_SEQUENCES_PATH) -> list[SkillCall]:
    sequences = load_expected_skill_sequences(path)
    if task_id not in sequences:
        raise SkillLibraryError(f"no expected skill sequence for task: {task_id}")
    return [step.to_skill_call() for step in sequences[task_id]]


def load_failure_profiles(path: str | Path = FAILURE_PROFILES_PATH) -> list[FailureProfile]:
    target = Path(path)
    if not target.exists():
        return list(_DEFAULT_FAILURE_PROFILES)
    payload = _read_json(target)
    profiles = payload["profiles"] if isinstance(payload, dict) and "profiles" in payload else payload
    return [
        FailureProfile(
            failure_id=item["failure_id"],
            target=item.get("target", ""),
            description=item.get("description", ""),
            expected_behavior=item.get("expected_behavior", ""),
            expected_recovery_tier=int(item.get("expected_recovery_tier", 0)),
            injection=dict(item.get("injection", {})),
        )
        for item in profiles
    ]


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
