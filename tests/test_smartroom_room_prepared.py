"""The composite device goal, against the servient rather than against fakes.

    docker compose -f env/docker-compose.yml up -d
    pytest -m smartroom

The unit tests for this path used fake Things with friendly ids and passed while
the live room resolved nothing at all: the real directory identifies Things by
``urn:uuid:...`` and puts the human name in ``title``. That is the class of bug
only a live run finds, so these assertions are deliberately about what the real
environment publishes.

Excluded from the default suite and from ``-m live``, because CI's live job has a
browser but no Docker; a failure there would say nothing about the code.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from src.effectors.wot_executor import WotExecutor
from src.perception.thing_directory import ThingDirectoryClient
from src.planner.device_binding import composite_goal_for, device_binding_for, resolve_device_target
from src.runtime.device_goal import pursue_composite_goal

pytestmark = pytest.mark.smartroom

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_room_prepared.py"
_spec = importlib.util.spec_from_file_location("run_room_prepared", _PATH)
assert _spec and _spec.loader
runner = importlib.util.module_from_spec(_spec)
sys.modules["run_room_prepared"] = runner
_spec.loader.exec_module(runner)


@pytest.fixture()
def room() -> Any:
    """Discovered Things and an executor that can reach them, or skip."""
    try:
        client = ThingDirectoryClient()
        tds = client.discover_tds()
    except Exception as exc:  # not running: say so rather than fail
        pytest.skip(f"smart room not available: {exc}")

    base = runner.advertised_base(tds)
    rewritten = ""
    if base and not runner.reachable(base):
        rewritten = f"http://localhost:{base.rsplit(':', 1)[-1]}"
        tds = runner.rewrite_base(tds, base, rewritten)
    from src.perception.td_affordance_parser import TdAffordanceParser

    parser = TdAffordanceParser()
    models = [parser.parse(td) for td in tds]
    assert not runner.reset_room(), "the control plane refused to reset the room"
    return models, WotExecutor(tds), rewritten


def test_the_directory_publishes_the_room(room: Any):
    models, _executor, _rewritten = room
    titles = {m.title for m in models}
    assert {"projector", "lights"} <= titles, titles


def test_a_thing_named_by_uuid_is_still_resolved_by_its_title(room: Any):
    """The live finding: matching the id alone resolved nothing in the real room."""
    models, _executor, _rewritten = room
    target = resolve_device_target(device_binding_for("lighting_set"), models, {"percent": 40})
    assert getattr(target, "property", "") == "brightness"
    assert target.thing_id.startswith("urn:uuid:"), target.thing_id
    assert target.thing_title == "lights"


def test_a_read_only_property_is_refused_before_anything_is_written(room: Any):
    """occupancy is a sensor. The TD says so, and that has to be enough."""
    models, _executor, _rewritten = room
    occupancy = [m for m in models if m.title == "occupancy"]
    assert occupancy, "the room should publish an occupancy sensor"
    assert all(s.read_only for s in occupancy[0].state_sources)


def test_preparing_the_room_confirms_every_property_by_reading_it_back(room: Any):
    models, executor, _rewritten = room
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), models, {}, executor)
    assert outcome.verified, outcome.summary()
    for part in outcome.parts:
        assert part.written and part.verified, part.to_dict()
        assert part.observed is not None


def test_an_utterance_value_reaches_the_device(room: Any):
    models, executor, _rewritten = room
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), models, {"percent": 55}, executor)
    lights = next(p for p in outcome.parts if p.goal_state == "lighting_set")
    assert lights.wanted == 55
    assert lights.verified, lights.to_dict()


def test_a_write_the_room_accepts_and_ignores_fails_the_goal(room: Any):
    """The reason every write is read back: 204 is not evidence of a change."""
    models, executor, _rewritten = room
    titles = {m.thing_id: m.title for m in models}
    dropping = runner._IgnoringExecutor(executor, "lights.brightness", titles)
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), models, {"percent": 45}, dropping)
    lights = next(p for p in outcome.parts if p.goal_state == "lighting_set")
    assert lights.written, "the write was accepted"
    assert not lights.verified, "and it changed nothing, which must not count as success"
    assert not outcome.verified
    assert [p.goal_state for p in outcome.unmet] == ["lighting_set"]
