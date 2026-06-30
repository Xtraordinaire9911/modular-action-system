"""Load Member A demo fixtures and bind them to the typed SkillLibrary.

These helpers turn the declarative JSON fixtures under ``fixtures/`` into typed
objects the runtime and evaluation can consume, and validate that every task
and postcondition references skills that actually exist in the SkillLibrary
(loaded from ``config/skills_seed.json``). The SkillLibrary remains the single
source of truth for skill contracts; the fixtures only add task-level intent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.contracts.types import SkillCall
from src.skill_library.library import SkillLibrary, SkillLibraryError, load_skill_library

TASK_FIXTURES_PATH = "fixtures/task_fixtures.json"
EXPECTED_SEQUENCES_PATH = "fixtures/expected_skill_sequences.json"
EXPECTED_POSTCONDITIONS_PATH = "fixtures/expected_postconditions.json"
FAILURE_PROFILES_PATH = "fixtures/failure_profiles.json"
DEMO_SKILL_CONTRACTS_PATH = "contracts/demo_skill_contracts.json"


@dataclass
class TaskFixture:
    task_id: str
    user_goal: str
    expected_skill_sequence: list[str]
    initial_state: dict[str, Any]
    expected_final_state: dict[str, Any]
    allowed_failure_profile: str | None = None


@dataclass
class ExpectedStep:
    skill_id: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_skill_call(self) -> SkillCall:
        return SkillCall(skill_id=self.skill_id, params=dict(self.params))


@dataclass
class SkillPostconditionSpec:
    skill_id: str
    description: str
    predicates: list[str]


@dataclass
class FailureProfile:
    failure_id: str
    target: str
    description: str
    expected_behavior: str
    expected_recovery_tier: int
    injection: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_task_fixtures(path: str | Path = TASK_FIXTURES_PATH) -> list[TaskFixture]:
    payload = _read_json(path)
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


def load_expected_skill_sequences(
    path: str | Path = EXPECTED_SEQUENCES_PATH,
) -> dict[str, list[ExpectedStep]]:
    payload = _read_json(path)
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


def load_expected_postconditions(
    path: str | Path = EXPECTED_POSTCONDITIONS_PATH,
) -> dict[str, SkillPostconditionSpec]:
    payload = _read_json(path)
    postconditions = payload["postconditions"] if isinstance(payload, dict) and "postconditions" in payload else payload
    return {
        skill_id: SkillPostconditionSpec(
            skill_id=skill_id,
            description=spec.get("description", ""),
            predicates=list(spec.get("predicates", [])),
        )
        for skill_id, spec in postconditions.items()
    }


def load_failure_profiles(path: str | Path = FAILURE_PROFILES_PATH) -> list[FailureProfile]:
    payload = _read_json(path)
    profiles = payload["profiles"] if isinstance(payload, dict) and "profiles" in payload else payload
    result: list[FailureProfile] = []
    for item in profiles:
        known = {"failure_id", "target", "description", "expected_behavior", "expected_recovery_tier", "injection"}
        result.append(
            FailureProfile(
                failure_id=item["failure_id"],
                target=item.get("target", ""),
                description=item.get("description", ""),
                expected_behavior=item.get("expected_behavior", ""),
                expected_recovery_tier=int(item.get("expected_recovery_tier", 0)),
                injection=dict(item.get("injection", {})),
                extra={key: value for key, value in item.items() if key not in known},
            )
        )
    return result


def validate_fixtures_against_library(
    library: SkillLibrary | None = None,
    *,
    task_path: str | Path = TASK_FIXTURES_PATH,
    sequence_path: str | Path = EXPECTED_SEQUENCES_PATH,
    postcondition_path: str | Path = EXPECTED_POSTCONDITIONS_PATH,
    failure_path: str | Path = FAILURE_PROFILES_PATH,
) -> list[str]:
    """Return a list of human-readable problems; an empty list means valid."""
    library = library or load_skill_library()
    known_skills = set(library.ids())
    problems: list[str] = []

    failure_ids = {profile.failure_id for profile in load_failure_profiles(failure_path)}
    sequences = load_expected_skill_sequences(sequence_path)

    for fixture in load_task_fixtures(task_path):
        for skill_id in fixture.expected_skill_sequence:
            if skill_id not in known_skills:
                problems.append(f"task {fixture.task_id}: unknown skill_id {skill_id!r}")
        if fixture.allowed_failure_profile and fixture.allowed_failure_profile not in failure_ids:
            problems.append(f"task {fixture.task_id}: unknown failure profile {fixture.allowed_failure_profile!r}")
        if fixture.task_id not in sequences:
            problems.append(f"task {fixture.task_id}: missing expected skill sequence")
        else:
            seq_ids = [step.skill_id for step in sequences[fixture.task_id]]
            if seq_ids != fixture.expected_skill_sequence:
                problems.append(
                    f"task {fixture.task_id}: expected_skill_sequence {fixture.expected_skill_sequence} "
                    f"does not match expected_skill_sequences.json {seq_ids}"
                )

    for skill_id, spec in load_expected_postconditions(postcondition_path).items():
        if skill_id not in known_skills:
            problems.append(f"postcondition spec for unknown skill_id {skill_id!r}")
            continue
        library_predicates = [condition.predicate for condition in library.get(skill_id).postconditions]
        if spec.predicates != library_predicates:
            problems.append(
                f"skill {skill_id}: expected_postconditions predicates {spec.predicates} "
                f"do not match library postconditions {library_predicates}"
            )

    return problems


def _condition_to_dict(predicate: str, description: str) -> dict[str, Any]:
    item: dict[str, Any] = {"predicate": predicate}
    if description:
        item["description"] = description
    return item


def skill_to_contract_dict(skill: Any) -> dict[str, Any]:
    """Serialize a SkillTuple back to the seed/contract JSON shape."""
    return {
        "skill_id": skill.skill_id,
        "description": skill.description,
        "parameters_schema": dict(skill.parameters_schema),
        "preconditions": [_condition_to_dict(c.predicate, c.description) for c in skill.preconditions],
        "postconditions": [_condition_to_dict(c.predicate, c.description) for c in skill.postconditions],
        "allowed_backends": list(skill.allowed_backends),
        "preferred_backends": list(skill.preferred_backends),
        "rollback": (
            None
            if skill.rollback is None
            else {"skill_id": skill.rollback.skill_id, "params": dict(skill.rollback.params)}
        ),
        "failure_modes": {
            failure_type: {"tier": policy.tier, "action": policy.action, "description": policy.description}
            for failure_type, policy in skill.failure_modes.items()
        },
        "timeout_ms": skill.timeout_ms,
        "safety_level": skill.safety_level,
        "irreversible": skill.irreversible,
    }


def build_demo_skill_contracts(library: SkillLibrary | None = None) -> dict[str, Any]:
    library = library or load_skill_library()
    return {
        "_about": (
            "Generated demo skill contracts. Do not edit by hand: this file is exported from "
            "config/skills_seed.json via src.skill_library.fixtures.export_demo_skill_contracts. "
            "config/skills_seed.json is the single source of truth."
        ),
        "skills": [skill_to_contract_dict(skill) for skill in library.all()],
    }


def export_demo_skill_contracts(
    library: SkillLibrary | None = None,
    path: str | Path = DEMO_SKILL_CONTRACTS_PATH,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    contracts = build_demo_skill_contracts(library)
    target.write_text(json.dumps(contracts, indent=2) + "\n", encoding="utf-8")
    return target
