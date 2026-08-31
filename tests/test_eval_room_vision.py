"""A room-vision number must never be able to flatter the model that produced it.

Every test here is about the bookkeeping rather than about the model being
right. The failures they guard against are the ones that make a report look
better than the run was: an abstention counted as a correct answer, a call that
never came back quietly dropped, a panel that never showed the condition graded
anyway, and a rate of 0% that actually means nobody ever measured it.

No model and no browser are involved, on purpose. The arithmetic is what the
README will cite, so it has to be checkable without the room running.
"""

from __future__ import annotations

import json

from scripts.eval_room_vision import (
    NON_COMPLIANT,
    PanelReading,
    RoomTrial,
    _previous_measurement,
    arrived,
    lamp_not_lit,
    never_moved,
    still_travelling,
    summarise,
)


def _trial(
    condition: str,
    expected: bool,
    *,
    said: bool | None = None,
    source: str = "vlm",
    established: bool = True,
) -> RoomTrial:
    return RoomTrial(
        condition=condition,
        expected_answer=expected,
        established=established,
        model_says_met=said,
        source=source,
    )


# --- reading the panel -------------------------------------------------------------


def test_a_jammed_panel_and_a_travelling_panel_are_told_apart_by_where_the_measurement_started():
    """Guards against one shared "the readings differ" predicate.

    The two panels are the same picture. If the setup cannot distinguish them,
    a jam that quietly healed gets recorded as a non-compliance trial and
    inflates the only number this evaluation exists to report.
    """
    jammed = PanelReading(commanded="30 %", measured="100 %", baseline_measured="100 %")
    travelling = PanelReading(commanded="30 C", measured="24 C", baseline_measured="20 C")

    assert never_moved(jammed) and not still_travelling(jammed)
    assert still_travelling(travelling) and not never_moved(travelling)


def test_a_panel_counts_as_arrived_only_when_both_lines_carry_a_reading():
    """An unreadable panel produces two empty strings, which are trivially equal.

    Without the emptiness check that reads as a converged device, so a dashboard
    that failed to render would supply free true-negative trials.
    """
    assert arrived(PanelReading("22 C", "22 C", "20 C"))
    assert not arrived(PanelReading("", "", ""))
    assert not still_travelling(PanelReading("30 C", "", "20 C"))


def test_a_projector_told_on_is_not_lit_until_its_lamp_says_so():
    """The condition is about the lower line: Power reads on in both cases."""
    assert lamp_not_lit(PanelReading(commanded="on", measured="warming", baseline_measured="off"))
    assert not lamp_not_lit(PanelReading(commanded="on", measured="on", baseline_measured="off"))


# --- what counts towards a rate ----------------------------------------------------


def test_detection_is_measured_over_every_panel_that_does_not_show_arrival():
    """Guards against defining detection off the injected fault instead.

    The thermostat mid ramp has no fault and its panel still shows a device that
    has not got there. Scoring detection only on the jammed trials would count
    every correct "no" on a travelling device as a miss.
    """
    trials = [
        _trial("diverging", False, said=False),
        _trial("lamp_warming", False, said=False),
        _trial(NON_COMPLIANT, False, said=False),
        _trial("converged", True, said=True),
    ]

    summary = summarise(trials)

    assert summary["detection_trials"] == 3
    assert summary["detection_rate"] == 1.0
    assert summary["converged_trials"] == 1
    assert summary["false_alarm_rate"] == 0.0


def test_the_motor_jam_trials_are_also_reported_on_their_own():
    """Guards against two easy conditions carrying the one that matters.

    The jammed blind is the trial where the DOM holds the requested number and
    the screen contradicts it. Averaged in with mid-travel trials, a model that
    missed every jam could still report a high detection rate.
    """
    trials = [
        _trial("diverging", False, said=False),
        _trial("lamp_warming", False, said=False),
        _trial(NON_COMPLIANT, False, said=True),
        _trial(NON_COMPLIANT, False, said=True),
    ]

    summary = summarise(trials)

    assert summary["detection_rate"] == 0.5
    assert summary["non_compliance_trials"] == 2
    assert summary["non_compliance_detection_rate"] == 0.0


def test_a_declined_answer_is_neither_a_detection_nor_a_false_alarm():
    """Guards against an abstention being scored as either kind of answer.

    Counting it as a miss punishes the model for the honest outcome; counting it
    as a hit invents evidence. It belongs in neither numerator nor denominator,
    and it still has to appear somewhere, so it is reported as its own number.
    """
    trials = [
        _trial(NON_COMPLIANT, False, said=False),
        _trial(NON_COMPLIANT, False, said=None, source="low_confidence"),
        _trial("converged", True, said=True),
        _trial("converged", True, said=None, source="low_confidence"),
    ]

    summary = summarise(trials)

    assert summary["declined"] == 2
    assert summary["graded"] == 2
    assert summary["detection_trials"] == 1 and summary["detection_rate"] == 1.0
    assert summary["converged_trials"] == 1 and summary["false_alarm_rate"] == 0.0


def test_a_call_that_never_came_back_is_counted_rather_than_dropped():
    """Guards against a transport failure silently shrinking the denominator.

    A run where half the calls failed and the rest were right is not the same
    result as a run where everything worked, and a report that shows only the
    rate cannot tell them apart.
    """
    trials = [
        _trial(NON_COMPLIANT, False, said=False),
        _trial(NON_COMPLIANT, False, said=None, source="error"),
    ]

    summary = summarise(trials)

    assert summary["transport_failures"] == 1
    # A failed call is not the model declining to answer: nothing was asked.
    assert summary["declined"] == 0
    assert summary["detection_trials"] == 1
    assert summary["sources"] == {"error": 1, "vlm": 1}


def test_a_panel_that_never_held_its_shape_is_reported_instead_of_graded():
    """Guards against grading a trial whose condition was never on screen.

    The dashboard polls on its own schedule, so a short-lived state can be missed
    entirely. Grading the model on whatever the panel happened to show would
    score it against a condition it was never given.
    """
    trials = [
        _trial("lamp_warming", False, said=False),
        _trial("lamp_warming", False, said=None, source="", established=False),
    ]

    summary = summarise(trials)

    assert summary["trials_attempted"] == 2
    assert summary["panels_established"] == 1
    assert summary["not_established"] == 1
    assert summary["detection_trials"] == 1
    assert summary["by_condition"]["lamp_warming"]["established"] == 1
    assert summary["by_condition"]["lamp_warming"]["attempted"] == 2


def test_a_rate_with_no_denominator_reads_as_not_measured_rather_than_zero():
    """Guards against the confusion the whole carry-forward rule exists to stop.

    A false alarm rate of 0.0 means the model never cried wolf. Nothing at all
    means no converged panel was ever put in front of it. They must not share a
    field.
    """
    summary = summarise([_trial(NON_COMPLIANT, False, said=False)])

    assert summary["detection_rate"] == 1.0
    assert summary["converged_trials"] == 0
    assert summary["false_alarm_rate"] is None


def test_an_empty_run_measures_nothing_and_says_so_in_every_rate():
    """A run that produced no trials must not report perfect or zero anything."""
    summary = summarise([])

    assert summary["graded"] == 0
    assert summary["detection_rate"] is None
    assert summary["non_compliance_detection_rate"] is None
    assert summary["false_alarm_rate"] is None


def test_a_correct_answer_is_the_expected_one_not_the_agreeable_one():
    """Guards against grading against the commanded reading.

    On a jammed panel the requested number is right there in the upper line, so
    a model reading it answers yes. That is the failure being measured, and it
    has to score as wrong.
    """
    read_the_command = _trial(NON_COMPLIANT, False, said=True)
    read_the_measurement = _trial(NON_COMPLIANT, False, said=False)

    assert read_the_command.model_correct is False
    assert read_the_measurement.model_correct is True
    assert summarise([read_the_command, read_the_measurement])["detection_rate"] == 0.5


# --- not overwriting a measurement with the absence of one -------------------------


def test_a_real_previous_measurement_is_carried_forward_with_its_own_timestamp(tmp_path):
    """Guards against a run with no model erasing the run that had one.

    The report is cited for a detection rate. Replacing it with zeros would leave
    a reader following that citation unable to tell an unmeasured field from a
    measured failure.
    """
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(
            {
                "at": "2026-08-25T11:00:00",
                "trials": [{"condition": NON_COMPLIANT}],
                "summary": {"graded": 6, "detection_rate": 1.0},
            }
        ),
        encoding="utf-8",
    )

    carried = _previous_measurement(path)

    assert carried is not None
    assert carried["summary"]["detection_rate"] == 1.0
    assert carried["summary"]["carried_forward_from"] == "2026-08-25T11:00:00"
    assert carried["trials"] == [{"condition": NON_COMPLIANT}]


def test_a_previous_run_that_measured_nothing_has_nothing_to_carry(tmp_path):
    """Guards against propagating an empty block as though it were evidence.

    Two runs with no model in a row must not make the second one look like it
    inherited a measurement.
    """
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps({"at": "2026-08-25T11:00:00", "summary": {"not_measured": "no model"}}), encoding="utf-8"
    )

    assert _previous_measurement(path) is None


def test_a_missing_or_unreadable_report_is_not_treated_as_a_measurement(tmp_path):
    """The first run has no predecessor, and a truncated file is not one either."""
    assert _previous_measurement(tmp_path / "absent.json") is None

    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert _previous_measurement(broken) is None
