"""Runtime evidence provenance and accepted fusion view tests."""

import pytest

from src.contracts.types import Affordance, Condition, ExecutionResult, Observation, ObservedAssertion
from src.runtime.cognitive_map import CognitiveMap, StateAssertion
from src.runtime.episode import abstract_state_id
from src.verification.condition_evaluator import evaluate_condition
from src.verification.conflict_detector import EpistemicArbiter


@pytest.mark.parametrize(
    ("backend", "expected_source", "channel"),
    [
        ("dom", "dom", "page_state"),
        ("wot", "wot", "device_states"),
        ("visual", "visual", "visual_state"),
    ],
)
def test_execution_delta_preserves_backend_provenance(backend, expected_source, channel):
    cognitive_map = CognitiveMap(task_id=f"delta:{backend}")

    cognitive_map.record_execution_result(
        ExecutionResult(
            skill_id="set_value",
            backend_used=backend,
            success=True,
            latency_ms=5,
            confidence=0.73,
            raw_observation_delta={"target": {"value": 7}},
            attempt=2,
            transition_id="transition-2",
        )
    )

    assertion = cognitive_map.get_latest_state("target", "value", source=expected_source)
    assert assertion is not None
    assert assertion.confidence == 0.73
    assert assertion.metadata["backend"] == backend
    assert assertion.metadata["attempt"] == 2
    assert assertion.metadata["transition_id"] == "transition-2"
    assert getattr(cognitive_map, channel)["target"]["value"] == 7
    other_channels = {"page_state", "device_states", "visual_state"} - {channel}
    assert all("target" not in getattr(cognitive_map, other) for other in other_channels)


def test_observed_assertion_overrides_default_channel_ingestion_without_duplication():
    cognitive_map = CognitiveMap(task_id="observed-confidence")
    observation = Observation(
        device_states={"thermostat": {"temperature": 22}},
        assertions=[
            ObservedAssertion(
                entity_id="thermostat",
                attribute="temperature",
                value=22,
                source="wot",
                confidence=0.64,
                timestamp_ms=1234,
                provenance={"transport": "http"},
            )
        ],
    )

    cognitive_map.update_from_observation(observation)

    matching = [
        assertion
        for assertion in cognitive_map.state_assertions
        if assertion.entity_id == "thermostat" and assertion.attribute == "temperature"
    ]
    assert len(matching) == 1
    assert matching[0].confidence == 0.64
    assert matching[0].timestamp_ms == 1234
    assert matching[0].metadata["transport"] == "http"
    assert matching[0].metadata["confidence_origin"] == "observed"


def test_verifier_prefers_accepted_fused_state_for_unqualified_predicate():
    cognitive_map = CognitiveMap(task_id="fused-verifier")
    cognitive_map.add_state_assertion(StateAssertion("thermostat", "temperature", 22, "wot", timestamp_ms=10))
    cognitive_map.add_state_assertion(StateAssertion("thermostat", "temperature", 22, "dom", timestamp_ms=11))
    cognitive_map.add_state_assertion(
        StateAssertion("thermostat", "temperature", 24, "visual", confidence=0.2, timestamp_ms=12)
    )

    decision = EpistemicArbiter(numeric_tolerances={"temperature": 2.0}).fuse(cognitive_map)
    result = evaluate_condition(Condition("thermostat.temperature == 22"), cognitive_map)

    assert decision.allow_system1 is True
    assert cognitive_map.fused_state == {"thermostat": {"temperature": 22}}
    assert result.passed


def test_blocking_fusion_never_exposes_candidate_as_accepted_state():
    cognitive_map = CognitiveMap(task_id="blocked-fusion")
    cognitive_map.add_state_assertion(StateAssertion("door", "lock", "locked", "dom", timestamp_ms=10))
    cognitive_map.add_state_assertion(StateAssertion("door", "lock", "unlocked", "wot", timestamp_ms=11))

    decision = EpistemicArbiter().fuse(cognitive_map)
    result = evaluate_condition(Condition("fused_state.door.lock == 'locked'"), cognitive_map)

    assert decision.allow_system1 is False
    assert cognitive_map.fused_state == {}
    assert cognitive_map.fused_assertions
    assert not result.passed


def test_abstract_state_identity_ignores_dynamic_url_ids_and_selector_layout_shift():
    first = CognitiveMap(task_id="first")
    second = CognitiveMap(task_id="second")
    first.update_from_observation(
        Observation(accessibility_tree={"page_state": {"page": {"url": "https://x.test/order/12345?a=1"}}})
    )
    second.update_from_observation(
        Observation(accessibility_tree={"page_state": {"page": {"url": "https://x.test/order/98765?a=2"}}})
    )
    first.update_affordances(
        [
            Affordance(
                "dom_1",
                "DOM",
                "button",
                "Checkout",
                "click",
                {"selector": "#cart > button:nth-child(2)"},
                1.0,
            )
        ]
    )
    second.update_affordances(
        [
            Affordance(
                "dom_99",
                "DOM",
                "button",
                "Checkout",
                "click",
                {"selector": "main button:nth-child(8)"},
                1.0,
            )
        ]
    )

    assert abstract_state_id(first) == abstract_state_id(second)
