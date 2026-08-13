import asyncio
import json
import time

from src.contracts.types import Observation
from src.perception.vlm_active_probe import VlmActivePerceptionProbe
from src.perception.vlm_observer import VlmObserver
from src.runtime.cognitive_map import CognitiveMap, Conflict, StateAssertion
from src.verification.active_perception import ActivePerceptionResolver


class _CandidateVision:
    name = "candidate-vision"

    def describe(self, system, question, image_png):
        _ = (system, image_png)
        answer = "premium" in question
        return json.dumps(
            {
                "answer": answer,
                "confidence": 0.91 if answer else 0.8,
                "evidence": "the visible highlight surrounds premium",
            }
        )


def _conflict():
    return Conflict(
        id="plan.selected",
        conflict_type="selected_mismatch",
        sources=["dom", "visual"],
        description="DOM and visual selection disagree",
        entity_id="plan",
        attribute="selected",
        values={"dom": "premium", "visual": "basic"},
        conflict_mass=1.0,
        severity="high",
    )


def test_vlm_active_probe_resolves_from_conflict_candidates_not_fixture_rules(tmp_path):
    observer = VlmObserver(client=_CandidateVision(), ledger_path=tmp_path / "vlm.jsonl")
    probe = VlmActivePerceptionProbe(observer, lambda: b"\x89PNG\r\n\x1a\nimage")

    observed = asyncio.run(probe.observe([_conflict()], CognitiveMap("vlm-probe"), Observation()))

    assert observed is not None
    assert observed.assertions[0].value == "premium"
    assert observed.assertions[0].source == "visual"
    assert observed.assertions[0].provenance["active_perception"] is True
    assert len(probe.judgements) == 2


def test_runtime_active_perception_resolver_consumes_fresh_vlm_assertion(tmp_path):
    now = int(time.time() * 1000)
    cognitive_map = CognitiveMap("vlm-resolver")
    cognitive_map.add_state_assertion(StateAssertion("plan", "selected", "premium", "dom", 0.95, now))
    cognitive_map.add_state_assertion(StateAssertion("plan", "selected", "basic", "visual", 0.9, now))
    cognitive_map.add_conflict(_conflict())
    observer = VlmObserver(client=_CandidateVision(), ledger_path=tmp_path / "vlm.jsonl")
    resolver = ActivePerceptionResolver(VlmActivePerceptionProbe(observer, lambda: b"\x89PNG\r\n\x1a\nimage"))

    result = asyncio.run(resolver.resolve([_conflict()], cognitive_map, Observation()))

    assert result.resolved
    assert result.trace[0]["action"] == "active_perception_probe"
    assert result.trace[0]["resolved"] is True
