from __future__ import annotations

import asyncio

from src.contracts.types import ExecutionResult, Observation, ObservedAssertion, SkillCall
from src.demos.evidence_fusion_panel import EvidenceFusionPanel, PresentationEpistemicArbiter
from src.runtime.cognitive_map import CognitiveMap, StateAssertion
from src.runtime.live_observation import LiveRuntimeObservation


class FakeSession:
    def __init__(self) -> None:
        self.html = ""
        self.opened = False

    async def evaluate(self, expression: str, arg: object = None) -> bool:
        if isinstance(arg, dict) and "css" in arg:
            self.opened = True
        if isinstance(arg, dict) and "html" in arg:
            self.html = str(arg["html"])
        return True


def test_panel_renders_source_labelled_live_evidence() -> None:
    async def scenario() -> None:
        session = FakeSession()
        panel = EvidenceFusionPanel(session, "book room C and prepare it")
        observed = LiveRuntimeObservation(
            observation=Observation(
                screenshot=b"real rendered bytes",
                assertions=[
                    ObservedAssertion("booking", "confirmed", False, "dom", 0.95, 100),
                    ObservedAssertion("thermostat", "target_temperature", 20, "wot", 1.0, 100),
                ],
            ),
            provenance={"dom_affordance_count": 7, "wot_affordance_count": 12},
            captured_at_ms=100,
        )
        await panel.show_observation(observed, "initial_observation")
        assert "7 live affordances" in session.html
        assert "12 TD affordances" in session.html
        assert "screenshot 19 bytes" in session.html
        assert "projection only" in session.html

    asyncio.run(scenario())


def test_panel_projects_the_authoritative_arbiter_decision() -> None:
    async def scenario() -> None:
        session = FakeSession()
        panel = EvidenceFusionPanel(session, "prepare the room")
        await panel.open()
        cognitive_map = CognitiveMap("episode")
        cognitive_map.add_state_assertion(StateAssertion("thermostat", "target", 19, "dom", timestamp_ms=100))
        cognitive_map.add_state_assertion(StateAssertion("thermostat", "target", 22, "wot", timestamp_ms=100))
        decision = PresentationEpistemicArbiter(panel).fuse(cognitive_map)
        await panel.flush()
        assert decision.allow_system1 is False
        assert panel.verdict_kind == "blocked"
        assert "FUSION BLOCKED" in session.html
        assert "dom=19" in session.html and "wot=22" in session.html

    asyncio.run(scenario())


def test_executor_failure_is_not_relabeled_as_a_fusion_conflict() -> None:
    async def scenario() -> None:
        session = FakeSession()
        panel = EvidenceFusionPanel(session, "prepare the room")
        await panel.open()
        await panel.show_action(SkillCall("confirm_booking", {}))
        await panel.show_execution(
            ExecutionResult(
                skill_id="confirm_booking",
                backend_used="dom",
                success=False,
                latency_ms=8.0,
                confidence=1.0,
                failure_reason="overlay obstruction intercepted the click",
            )
        )
        assert "TYPED OBSTRUCTION" in session.html
        assert "not a fabricated fusion conflict" in session.html
        assert panel.recovery_pending is True

    asyncio.run(scenario())


def test_next_action_is_labelled_as_the_same_agent_remediation() -> None:
    async def scenario() -> None:
        session = FakeSession()
        panel = EvidenceFusionPanel(session, "prepare the room")
        panel.recovery_pending = True
        await panel.open()
        await panel.show_action(SkillCall("dismiss_obstruction", {}))
        assert "dismiss_obstruction · same Agent remediation" in session.html

    asyncio.run(scenario())


def test_terminal_failure_preserves_the_causal_fusion_verdict() -> None:
    async def scenario() -> None:
        session = FakeSession()
        panel = EvidenceFusionPanel(session, "prepare the room")
        panel.verdict_kind = "blocked"
        panel.verdict = "FUSION BLOCKED · active perception required"
        panel.detail = "thermostat values disagree"

        await panel.show_final(verified=False, detail="state=escalated")

        assert panel.phase == "FUSE"
        assert panel.verdict == "FUSION BLOCKED · active perception required"
        assert "terminal oracle: not verified" in panel.detail

    asyncio.run(scenario())
