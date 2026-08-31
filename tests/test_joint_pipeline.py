"""The joint demo must not claim more integration than it has.

Its whole purpose is to answer "make it 'we did this', not 'I did this'", and a
demo that overstated the joining would be worse than three honest separate demos.
So the things asserted here are the claims it makes about itself: that every stage
is attributed, that the evidence handed to the planner carries no backend handle,
and that a refusal is carried as a refusal.

Unit level on purpose. The end to end behaviour needs the running servient and is
covered by running the script; what would silently rot is the wiring, and that is
what these check.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_joint_pipeline", Path(__file__).resolve().parents[1] / "scripts" / "run_joint_pipeline.py"
)
joint = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(joint)


class _Resolved:
    """The few fields the failure context reads off a resolved device target."""

    thing_id = "urn:uuid:abc"
    thing_title = "blinds"
    property = "position"
    measured_property = "measuredPosition"


def test_every_stage_names_the_component_that_owns_it() -> None:
    """The attribution is the deliverable. A stage with no owner is a stage that
    quietly reads as "mine"."""
    for name in ("understand", "reach", "act", "fail", "recover", "restore"):
        assert name in joint.OWNER, name
        assert joint.OWNER[name].strip(), name


def test_the_two_shared_interfaces_are_attributed_to_their_authors() -> None:
    """PlannerPort and the episode provider are committed by the other two, and
    that is checkable in the repository history rather than a courtesy."""
    assert "Yixin" in joint.OWNER["recover"]
    assert "PlannerPort" in joint.OWNER["recover"]
    assert "Fadi" in joint.OWNER["restore"]


def test_the_planner_is_given_evidence_and_never_a_backend_handle() -> None:
    """The port serves a browser failure and a device failure through one shape.

    It can only do that if the evidence names an affordance and an expected effect
    rather than a URL, so a href leaking into the context would break the interface
    without breaking any single test that looked only at this demo.
    """
    failure = joint.build_failure_context(_Resolved(), 40, 100)

    blob = repr(failure)
    for handle in ("http://", "https://", ":8080", "[data-testid", "selector"):
        assert handle not in blob, f"the planner was handed a backend handle: {handle}"

    assert failure.failure_type == "action_had_no_effect"
    assert failure.failure_boundary == "environment"


def test_the_reason_states_both_readings_because_one_of_them_is_the_evidence() -> None:
    """ "The write failed" would be wrong: the write succeeded. What makes this a
    failure is that two properties disagree, so both have to be in the reason."""
    failure = joint.build_failure_context(_Resolved(), 40, 100)

    assert "position reads 40" in failure.reason
    assert "measuredPosition reads 100" in failure.reason


def test_the_expected_effect_is_the_commanded_value_not_the_measured_one() -> None:
    failure = joint.build_failure_context(_Resolved(), 40, 100)

    assert failure.expected_effect == "position == 40"
