"""Tests for the Member A demo fixtures and the generated skill contracts.

These tests make the fixtures a real, enforced contract: every task references
skills that exist, the declared postconditions match the SkillLibrary, and the
committed contracts/demo_skill_contracts.json stays in sync with the library
(so it never becomes a stale second source of truth).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.skill_library import (
    DEMO_SKILL_CONTRACTS_PATH,
    build_demo_skill_contracts,
    expected_skill_calls,
    get_task_fixture,
    load_expected_postconditions,
    load_expected_skill_sequences,
    load_failure_profiles,
    load_skill_library,
    load_task_fixtures,
    validate_fixtures_against_library,
)


def test_task_fixtures_load():
    fixtures = load_task_fixtures()
    task_ids = {fixture.task_id for fixture in fixtures}
    assert "prepare_room_A_1400" in task_ids
    assert len(fixtures) == len(task_ids), "task_ids must be unique"


def test_fixtures_validate_against_library():
    problems = validate_fixtures_against_library()
    assert problems == [], "\n".join(problems)


def test_every_sequence_skill_exists_in_library():
    library = load_skill_library()
    sequences = load_expected_skill_sequences()
    for task_id, steps in sequences.items():
        for step in steps:
            assert step.skill_id in library, f"{task_id} references unknown skill {step.skill_id}"


def test_expected_postconditions_match_library():
    library = load_skill_library()
    for skill_id, spec in load_expected_postconditions().items():
        library_predicates = [c.predicate for c in library.get(skill_id).postconditions]
        assert spec.predicates == library_predicates


def test_expected_skill_calls_round_trip():
    calls = expected_skill_calls("prepare_room_A_1400")
    assert [call.skill_id for call in calls] == [
        "confirm_booking",
        "turn_on_projector",
        "set_temperature",
        "set_lighting",
        "verify_readiness",
    ]
    set_temperature = next(call for call in calls if call.skill_id == "set_temperature")
    assert set_temperature.params["target"] == 22


def test_allowed_failure_profiles_resolve():
    failure_ids = {profile.failure_id for profile in load_failure_profiles()}
    for fixture in load_task_fixtures():
        if fixture.allowed_failure_profile is not None:
            assert fixture.allowed_failure_profile in failure_ids


def test_failure_profiles_have_integer_tiers():
    for profile in load_failure_profiles():
        assert isinstance(profile.expected_recovery_tier, int)
        assert 0 <= profile.expected_recovery_tier <= 4


def test_demo_skill_contracts_in_sync_with_library():
    expected = build_demo_skill_contracts()
    actual = json.loads(Path(DEMO_SKILL_CONTRACTS_PATH).read_text(encoding="utf-8"))
    assert actual == expected, (
        "contracts/demo_skill_contracts.json is stale. "
        "Regenerate with src.skill_library.export_demo_skill_contracts()."
    )


def test_get_task_fixture_unknown_raises():
    import pytest

    from src.skill_library import SkillLibraryError

    with pytest.raises(SkillLibraryError):
        get_task_fixture("does_not_exist")
