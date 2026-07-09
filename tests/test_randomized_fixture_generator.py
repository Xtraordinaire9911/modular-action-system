"""Tests for seeded Level 2 fixture generation."""

from evaluation.randomized_fixture_generator import generate_randomized_fixture, generate_seed_suite


def test_randomized_fixture_is_deterministic_by_seed():
    left = generate_randomized_fixture("prepare_room_A_1400", 123)
    right = generate_randomized_fixture("prepare_room_A_1400", 123)

    assert left.fixture == right.fixture
    assert left.skill_calls == right.skill_calls


def test_randomized_fixture_changes_task_but_keeps_runtime_shape():
    item = generate_randomized_fixture("prepare_room_A_1400", 124)

    assert item.fixture.task_id.endswith("__seed_124")
    assert [call.skill_id for call in item.skill_calls] == item.fixture.expected_skill_sequence
    assert item.fixture.expected_final_state["booked"] is True
    assert "target_temperature" in item.fixture.expected_final_state


def test_generate_seed_suite_uses_all_requested_seeds():
    suite = generate_seed_suite(["prepare_room_A_1400"], seeds=[1, 2, 3])

    assert [item.seed for item in suite] == [1, 2, 3]
