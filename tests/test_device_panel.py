"""The right-hand half of a device demo must be honest and harmless.

Harmless first: this renders on the page a live demo is running against, so a
panel that raises, that the agent can perceive, or that lets a value from the
room become markup would each be a worse failure than having no panel at all.

Honest second: the panel's numbers are the argument. A counter that ticks once
per sample would report thirty divergences for one jammed motor, and the tally
under the table would contradict the table. That is the specific mistake these
tests exist for.
"""

from __future__ import annotations

from typing import Any

from src.demos.device_panel import DevicePanel
from src.runtime.device_goal import values_match


class _Recorder:
    """A session that keeps the HTML it was handed."""

    def __init__(self) -> None:
        self.renders: list[str] = []

    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        if isinstance(arg, dict) and "html" in arg:
            self.renders.append(arg["html"])
        return True

    @property
    def last(self) -> str:
        return self.renders[-1] if self.renders else ""


class _Exploding:
    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        raise RuntimeError("the page navigated away mid-render")


def test_a_broken_page_cannot_fail_the_demo() -> None:
    """Every entry point, because any one of them raising would end a run."""
    panel = DevicePanel(_Exploding())

    assert panel.open() is False
    panel.begin_act("act", "said", "why")
    panel.sent("PUT", "/x", 1)
    panel.answered(204)
    panel.show_readings([(0.0, 1, 2)], commanded="a", measured="b")
    panel.settled(converged=False)
    panel.show_source(values_match, highlight="NUMERIC_TOLERANCE")
    panel.show_verdicts([("n", "e", True, "")])
    panel.conclude("done", kind="ok")
    panel.close()  # no exception is the assertion


def test_sampling_does_not_tick_the_counters_but_settling_does() -> None:
    """One jammed motor is one divergence, not one per frame.

    The table above the tally is filled in tick by tick; if showing it also
    counted, the panel would contradict the table it sits under.
    """
    panel = DevicePanel(_Recorder())

    for i in range(9):
        panel.show_readings([(i * 0.3, 30, 100)], commanded="position", measured="measuredPosition")
    assert (panel.agreed, panel.diverged) == (0, 0)

    panel.settled(converged=False)
    assert (panel.agreed, panel.diverged) == (0, 1)

    panel.settled(converged=True)
    assert (panel.agreed, panel.diverged) == (1, 1)


def test_only_writes_are_counted_as_writes_and_only_2xx_as_accepted() -> None:
    """ "Four writes, four accepted" has to mean what it says: a read counted as a
    write would inflate the number the audience is invited to check."""
    panel = DevicePanel(_Recorder())

    panel.sent("GET", "/blinds/properties/position")
    panel.answered(200)
    assert (panel.writes, panel.accepted) == (0, 1)

    panel.sent("PUT", "/blinds/properties/position", 30)
    panel.answered(204)
    assert (panel.writes, panel.accepted) == (1, 2)

    panel.sent("PUT", "/blinds/properties/position", 30)
    panel.answered(503)
    assert (panel.writes, panel.accepted) == (2, 2), "a 503 is not an accepted write"


def test_a_request_is_on_screen_before_its_status_is_known() -> None:
    """Two steps, so a slow call is visible as a slow call.

    A wire line that only appeared once the answer arrived could not show a
    request in flight, and "the write was accepted" is half of what these demos
    are about.
    """
    session = _Recorder()
    panel = DevicePanel(session)

    panel.sent("PUT", "/blinds/properties/position", 30)
    assert "..." in session.last

    panel.answered(204)
    assert "204" in session.last


def test_the_source_shown_is_read_from_the_function_that_runs() -> None:
    """``inspect.getsource``, so there is no second copy to fall out of date."""
    panel = DevicePanel(_Recorder())

    panel.show_source(values_match, title="device_goal.py")

    assert "def values_match" in panel.source
    assert "NUMERIC_TOLERANCE" in panel.source


def test_the_highlight_finds_its_line_by_content_not_by_number() -> None:
    """A line index would silently point at the wrong statement the first time
    anyone edited the function above it, and the panel would then be asserting
    that some unrelated line decides the verdict."""
    panel = DevicePanel(_Recorder())

    panel.show_source(values_match, highlight="NUMERIC_TOLERANCE")

    lines = panel.source.splitlines()
    assert panel.source_active >= 0
    assert "NUMERIC_TOLERANCE" in lines[panel.source_active]


def test_a_highlight_that_matches_nothing_highlights_nothing() -> None:
    """Better no spotlight than one on an arbitrary line."""
    panel = DevicePanel(_Recorder())

    panel.show_source(values_match, highlight="this text is not in the function")

    assert panel.source_active == -1


def test_a_value_from_the_room_cannot_become_markup() -> None:
    """Everything on this panel arrives from a servient or an utterance.

    Neither is under this project's control, and a property value rendered as
    markup would let the environment rewrite the panel that is supposed to be
    reporting on it.
    """
    session = _Recorder()
    panel = DevicePanel(session)

    panel.begin_act("<b>act</b>", "<script>alert(1)</script>", "why & then")
    panel.sent("PUT", "/x/properties/<img src=x>", "<i>30</i>")
    panel.answered(204)
    panel.show_readings([(0.0, "<u>30</u>", "<u>100</u>")], commanded="<a>", measured="<b>")
    panel.show_verdicts([("<h1>n</h1>", "<h2>e</h2>", False, "<h3>note</h3>")])
    panel.conclude("<marquee>done</marquee>")

    rendered = session.last
    for smuggled in ("<script>", "<marquee>", "<img src=x>", "<u>30</u>", "<h1>", "<i>30</i>"):
        assert smuggled not in rendered, smuggled
    # Escaped, not dropped: the value still has to be readable.
    assert "&lt;script&gt;" in rendered
    assert "&amp;" in rendered


def test_the_table_shows_both_readings_and_marks_the_ones_that_differ() -> None:
    session = _Recorder()
    panel = DevicePanel(session)

    panel.show_readings(
        [(0.0, 30, 30), (0.4, 30, 100)],
        commanded="position",
        measured="measuredPosition",
    )

    rendered = session.last
    assert "position" in rendered and "measuredPosition" in rendered
    # One converged row and one divergent row, and only the latter is flagged.
    assert rendered.count('class="diff"') == 1
    assert "&ne;" in rendered


def test_a_float_reading_is_shown_at_one_decimal() -> None:
    """A projector is not a debugger: integrating a ramp in floating point yields
    21.200000000000003, which is fifteen digits of distraction in the middle of
    the table the audience is meant to read."""
    session = _Recorder()
    panel = DevicePanel(session)

    panel.show_readings([(0.0, 25, 21.200000000000003)], commanded="target", measured="current")

    assert "21.2" in session.last
    assert "21.200000000000003" not in session.last


def test_a_verdict_renders_its_pass_or_fail_and_its_note() -> None:
    session = _Recorder()
    panel = DevicePanel(session)

    panel.show_verdicts(
        [
            ("transport", "HTTP 204", True, "a browser agent stops here"),
            ("measured read-back", "reads 100, asked 30", False, "the only one that is right"),
        ]
    )

    rendered = session.last
    assert "PASS" in rendered and "FAIL" in rendered
    assert "a browser agent stops here" in rendered


def test_beginning_an_act_clears_the_previous_act_but_keeps_the_tally() -> None:
    """The readings belong to one observation; the counters are the run's total.

    Clearing the counters per act would make the closing "writes 4, accepted 4"
    describe only the last thing that happened.
    """
    panel = DevicePanel(_Recorder())
    panel.sent("PUT", "/a", 1)
    panel.answered(204)
    panel.show_readings([(0.0, 1, 2)], commanded="a", measured="b")
    panel.settled(converged=False)
    panel.show_verdicts([("n", "e", True, "")])

    panel.begin_act("next", "said", "why")

    assert panel.readings == []
    assert panel.verdicts == []
    assert (panel.writes, panel.accepted, panel.diverged) == (1, 1, 1)
    assert panel.wire, "the wire is the run's transcript, not one act's"
