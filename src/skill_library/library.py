"""Skill catalog loading and lookup for Member A planning.

The protocol treats skills as reusable high-level capabilities, not primitive
clicks or HTTP calls. This module turns the seeded JSON catalog into typed
SkillTuple objects that the planner, router, verifier, and recovery layers can
share without each re-parsing raw dictionaries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from src.contracts.types import Condition, RecoveryPolicy, RollbackSpec, SkillTuple


class SkillLibraryError(ValueError):
    pass


class SkillLibrary:
    """Typed registry for project SkillTuple contracts."""

    def __init__(self, skills: Iterable[SkillTuple] = ()) -> None:
        self._skills: dict[str, SkillTuple] = {}
        for skill in skills:
            self.add(skill)

    def add(self, skill: SkillTuple) -> None:
        if skill.skill_id in self._skills:
            raise SkillLibraryError(f"duplicate skill_id: {skill.skill_id}")
        self._skills[skill.skill_id] = skill

    def get(self, skill_id: str) -> SkillTuple:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise SkillLibraryError(f"unknown skill_id: {skill_id}") from exc

    def all(self) -> list[SkillTuple]:
        return list(self._skills.values())

    def ids(self) -> list[str]:
        return list(self._skills)

    def by_backend(self, backend: str) -> list[SkillTuple]:
        return [skill for skill in self._skills.values() if backend in skill.allowed_backends]

    def preferred_for_backend(self, backend: str) -> list[SkillTuple]:
        return [skill for skill in self._skills.values() if backend in skill.preferred_backends]


def load_skill_library(path: str | Path = "config/skills_seed.json") -> SkillLibrary:
    """Load the seeded smart-room skills from JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SkillLibraryError("skill catalog must be a list")
    return SkillLibrary(_parse_skill(item) for item in data)


def _parse_skill(raw: dict) -> SkillTuple:
    required = {
        "skill_id",
        "description",
        "parameters_schema",
        "preconditions",
        "postconditions",
        "allowed_backends",
        "preferred_backends",
        "timeout_ms",
        "safety_level",
        "irreversible",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise SkillLibraryError(f"skill {raw.get('skill_id', '<unknown>')} missing fields: {missing}")

    return SkillTuple(
        skill_id=raw["skill_id"],
        description=raw["description"],
        parameters_schema=dict(raw["parameters_schema"]),
        preconditions=[_parse_condition(item) for item in raw.get("preconditions", [])],
        postconditions=[_parse_condition(item) for item in raw.get("postconditions", [])],
        allowed_backends=list(raw["allowed_backends"]),
        preferred_backends=list(raw["preferred_backends"]),
        rollback=_parse_rollback(raw.get("rollback")),
        failure_modes=_parse_recovery_policies(raw.get("failure_modes", {})),
        timeout_ms=int(raw["timeout_ms"]),
        safety_level=raw["safety_level"],
        irreversible=bool(raw["irreversible"]),
        idempotent=bool(raw.get("idempotent", False)),
    )


def _parse_condition(raw: dict | str) -> Condition:
    if isinstance(raw, str):
        return Condition(predicate=raw)
    return Condition(predicate=raw["predicate"], description=raw.get("description", ""))


def _parse_rollback(raw: dict | None) -> RollbackSpec | None:
    if raw is None:
        return None
    return RollbackSpec(skill_id=raw["skill_id"], params=dict(raw.get("params", {})))


def _parse_recovery_policies(raw: dict) -> dict[str, RecoveryPolicy]:
    policies: dict[str, RecoveryPolicy] = {}
    for failure_type, value in raw.items():
        if isinstance(value, str):
            policies[failure_type] = RecoveryPolicy(tier=0, action=value)
        else:
            policies[failure_type] = RecoveryPolicy(
                tier=int(value["tier"]),
                action=value["action"],
                description=value.get("description", ""),
            )
    return policies
