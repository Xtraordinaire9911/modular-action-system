"""Seeded smart-room fixture variants for Level 2 evaluation."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from src.contracts.types import SkillCall
from src.skill_library import TaskFixture, expected_skill_calls, get_task_fixture

DEV_SEEDS = tuple(range(100, 120))
EVAL_SEEDS = tuple(range(900, 920))


@dataclass(frozen=True)
class RandomizedFixture:
    fixture: TaskFixture
    skill_calls: list[SkillCall]
    seed: int
    base_task_id: str


def generate_randomized_fixture(base_fixture_id: str, seed: int) -> RandomizedFixture:
    """Return a reproducible fixture variant compatible with existing evaluators."""
    rng = random.Random(seed)
    base = get_task_fixture(base_fixture_id)

    room = rng.choice(["A", "B", "C"])
    hour = rng.randint(10, 18)
    time_slot = f"{hour:02d}:00"
    target_temperature = rng.randint(18, 24)
    brightness = rng.choice([20, 30, 40, 50])
    booked = rng.choice([False, True])
    projector = rng.choice(["off", "off", "on"])
    current_temperature = max(16, min(30, target_temperature + rng.choice([-2, -1, 0, 1])))
    failure_profile = rng.choice([None, None, "dom_selector_mutation", "wot_postcondition_mismatch"])

    task_id = f"{base.task_id}__seed_{seed}"
    fixture = TaskFixture(
        task_id=task_id,
        user_goal=f"Prepare Room {room} for a {time_slot} presentation.",
        expected_skill_sequence=list(base.expected_skill_sequence),
        initial_state={
            "room": room,
            "booked": booked,
            "projector": projector,
            "target_temperature": current_temperature,
            "current_temperature": current_temperature,
            "light_brightness": rng.randint(60, 100),
        },
        expected_final_state={
            "booked": True,
            "projector": "on",
            "target_temperature": target_temperature,
            "light_brightness": brightness,
            "readiness": True,
        },
        allowed_failure_profile=failure_profile,
    )
    calls = [_variant_call(call, room, time_slot, target_temperature, brightness, rng) for call in expected_skill_calls(base_fixture_id)]
    return RandomizedFixture(fixture=fixture, skill_calls=calls, seed=seed, base_task_id=base_fixture_id)


def generate_seed_suite(
    base_fixture_ids: list[str] | None = None,
    *,
    seeds: list[int] | tuple[int, ...] = DEV_SEEDS,
) -> list[RandomizedFixture]:
    base_ids = base_fixture_ids or ["prepare_room_A_1400"]
    return [generate_randomized_fixture(base_id, seed) for base_id in base_ids for seed in seeds]


def _variant_call(
    call: SkillCall,
    room: str,
    time_slot: str,
    target_temperature: int,
    brightness: int,
    rng: random.Random,
) -> SkillCall:
    params: dict[str, Any] = dict(call.params)
    if "room" in params:
        params["room"] = room
    if "time" in params:
        params["time"] = time_slot
    if call.skill_id == "set_temperature":
        params["target"] = target_temperature
    if call.skill_id == "set_lighting":
        params["brightness"] = brightness

    preferred_backends: list[str] = []
    if call.skill_id == "set_temperature" and rng.random() < 0.25:
        preferred_backends = ["dom", "wot", "visual"]
    return SkillCall(skill_id=call.skill_id, params=params, priority=call.priority, preferred_backends=preferred_backends)
