"""A recovery planner may be wrong; it must never be able to act on nothing.

The runtime hands this port typed failure evidence and takes back a proposal it
will validate, execute and verify itself. So the risk here is not a bad plan -
the runtime can survive one - it is a plan that looks model-derived when it is
not, or that names something nobody offered. Both would make the recovery trace
unreadable as evidence, which is the only thing it is for.

Every test below is about provenance and refusal. None of them assert that the
model chose well, because that is not a property this module can guarantee.
"""

from __future__ import annotations

import json

from src.planner.agent_planner import AgentPlanner, PlanningMode
from src.planner.model_recovery_planner import ModelRecoveryPlanner, candidate_lines
from src.runtime.action_context import ActionContext, AttemptedAction, FailureContext
from src.runtime.cognitive_map import RuntimeAffordance


class _Client:
    name = "fake-planner-1"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return self.reply


class _Exploding:
    name = "fake-planner-1"

    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("model unreachable")


class _SequenceClient:
    name = "fake-unified-agent-1"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.system_prompts: list[str] = []
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.system_prompts.append(system)
        self.prompts.append(user)
        return self.replies.pop(0)


def _affordance(identifier: str, **grounding: object) -> RuntimeAffordance:
    return RuntimeAffordance(
        id=identifier,
        source="dom",
        entity_id="page",
        action_name=identifier.replace("_", " "),
        action_type="click",
        confidence=0.9,
        grounding=dict(grounding),
    )


def _context(*, failure: bool = True, affordances: list[RuntimeAffordance] | None = None) -> ActionContext:
    if affordances is None:
        affordances = [
            _affordance("accept_cookies", remediates="confirm_plan"),
            _affordance("confirm_plan"),
        ]
    return ActionContext(
        task_id="t1",
        request_type="primitive_action",
        state={},
        affordances=affordances,
        unresolved_conflicts=[],
        allowed_actions=["click", "type", "ask_user", "done"],
        safety_constraints=[],
        failure=(
            FailureContext(
                failed_action="click",
                failed_affordance_id="confirm_plan",
                failed_entity_id="page",
                expected_effect="the plan is confirmed",
                failure_boundary="postcondition",
                failure_type="target_occluded",
                reason="another element received the click",
                transition_id="tr1",
                observation_state_id="s1",
            )
            if failure
            else None
        ),
        attempted_actions=[AttemptedAction("click", "confirm_plan", "confirmed", "failed", "tr1")],
    )


def _reply(**fields: object) -> str:
    base = {
        "affordance_id": "accept_cookies",
        "action": "click",
        "value": None,
        "expected_effect": "the banner is dismissed",
        "reason": "it declares that it remediates the failed affordance",
        "confidence": 0.9,
    }
    base.update(fields)
    return json.dumps(base)


# ── what the model is shown ──────────────────────────────────────────────────────


def test_the_model_is_offered_ids_and_declared_relations_only(tmp_path):
    """A planner that could see a selector could route around the runtime."""
    context = _context()
    lines = candidate_lines(context)

    assert any("accept_cookies" in line and "remediates=confirm_plan" in line for line in lines)
    joined = "\n".join(lines)
    assert "href" not in joined and "selector" not in joined and "css" not in joined


def test_candidate_lines_include_safe_human_labels_and_recovery_postconditions():
    context = _context(
        affordances=[
            _affordance(
                "dynamic_button_7",
                label="Renew room session",
                remediates="failed-action",
                recovery_postcondition="session.valid == true",
            )
        ]
    )

    line = candidate_lines(context)[0]

    assert 'label="Renew room session"' in line
    assert "recovery_postcondition=session.valid == true" in line


def test_every_offered_affordance_reaches_the_prompt(tmp_path):
    client = _Client(_reply())
    ModelRecoveryPlanner(client=client, ledger_path=tmp_path / "l.jsonl").plan(
        _context(), goal_state="plan.confirmed == true"
    )

    prompt = client.prompts[0]
    assert "accept_cookies" in prompt and "confirm_plan" in prompt
    assert "target_occluded" in prompt, "the failure type is evidence the planner needs"


# ── refusals ─────────────────────────────────────────────────────────────────────


def test_an_invented_affordance_is_refused_rather_than_acted_on(tmp_path):
    """The guard this module exists for."""
    planner = ModelRecoveryPlanner(
        client=_Client(_reply(affordance_id="dismiss_modal")), ledger_path=tmp_path / "l.jsonl"
    )

    plan = planner.plan(_context(), goal_state="plan.confirmed == true")

    assert not planner.last_choice.is_model_derived
    assert "was not offered" in planner.last_choice.error
    assert all(action.affordance_id != "dismiss_modal" for action in plan.actions)


def test_an_action_the_runtime_cannot_execute_is_refused(tmp_path):
    """ "navigate" is not in the primitive action type at all."""
    planner = ModelRecoveryPlanner(client=_Client(_reply(action="navigate")), ledger_path=tmp_path / "l.jsonl")

    planner.plan(_context(), goal_state="plan.confirmed == true")

    assert not planner.last_choice.is_model_derived
    assert "not an executable action" in planner.last_choice.error


def test_an_executable_action_this_episode_disallows_is_refused(tmp_path):
    """A separate guard from the one above, and it needs a separate case to prove it.

    "scroll" is a real primitive the runtime can execute; this episode's policy
    just does not permit it. Testing the two with one value would leave whichever
    check runs second unexercised.
    """
    context = _context()
    assert "scroll" not in context.allowed_actions

    planner = ModelRecoveryPlanner(client=_Client(_reply(action="scroll")), ledger_path=tmp_path / "l.jsonl")
    planner.plan(context, goal_state="plan.confirmed == true")

    assert not planner.last_choice.is_model_derived
    assert "not allowed here" in planner.last_choice.error


def test_a_declared_refusal_is_recorded_as_one(tmp_path):
    """ "Nothing here recovers this" is information, not a malfunction."""
    planner = ModelRecoveryPlanner(
        client=_Client(_reply(affordance_id="", reason="no affordance addresses this failure")),
        ledger_path=tmp_path / "l.jsonl",
    )

    planner.plan(_context(), goal_state="plan.confirmed == true")

    assert planner.last_choice.source == "unsupported"
    assert "no affordance addresses this failure" in planner.last_choice.error


def test_an_unparseable_reply_does_not_become_a_plan(tmp_path):
    planner = ModelRecoveryPlanner(client=_Client("I would click the banner"), ledger_path=tmp_path / "l.jsonl")

    planner.plan(_context(), goal_state="plan.confirmed == true")

    assert not planner.last_choice.is_model_derived
    assert "unparseable" in planner.last_choice.error


def test_a_broken_client_falls_back_without_claiming_the_model(tmp_path):
    planner = ModelRecoveryPlanner(client=_Exploding(), ledger_path=tmp_path / "l.jsonl")

    planner.plan(_context(), goal_state="plan.confirmed == true")

    assert not planner.last_choice.is_model_derived
    assert "model unreachable" in planner.last_choice.error


# ── provenance ───────────────────────────────────────────────────────────────────


def test_a_real_choice_is_labelled_as_model_derived_and_carries_its_reason(tmp_path):
    planner = ModelRecoveryPlanner(client=_Client(_reply()), ledger_path=tmp_path / "l.jsonl")

    plan = planner.plan(_context(), goal_state="plan.confirmed == true")

    assert planner.last_choice.is_model_derived
    assert planner.last_choice.affordance_id == "accept_cookies"
    assert plan.actions[0].affordance_id == "accept_cookies"
    assert "remediates" in planner.last_choice.reason
    assert "model recovery" in plan.reason


def test_no_model_configured_is_never_reported_as_a_model_decision(tmp_path):
    planner = ModelRecoveryPlanner(client=None, ledger_path=tmp_path / "l.jsonl")

    planner.plan(_context(), goal_state="plan.confirmed == true")

    assert planner.last_choice.source == "deterministic"
    assert not planner.last_choice.is_model_derived


def test_a_context_with_no_failure_costs_nothing(tmp_path):
    """Forward planning is the controller's job; a model call there is waste."""
    client = _Client(_reply())
    planner = ModelRecoveryPlanner(client=client, ledger_path=tmp_path / "l.jsonl")

    planner.plan(_context(failure=False), goal_state="plan.confirmed == true")

    assert client.prompts == [], "the model was asked to plan work that had not failed"
    assert planner.last_choice.source == "deterministic"
    assert planner.last_choice.mode is PlanningMode.FORWARD
    assert planner.last_choice.offered == ["accept_cookies", "confirm_plan"]


def test_an_observation_offering_nothing_is_reported_not_guessed(tmp_path):
    planner = ModelRecoveryPlanner(client=_Client(_reply()), ledger_path=tmp_path / "l.jsonl")

    planner.plan(_context(affordances=[]), goal_state="plan.confirmed == true")

    assert planner.last_choice.source == "unsupported"
    assert "no affordances" in planner.last_choice.error


# ── the ledger ───────────────────────────────────────────────────────────────────


def test_every_decision_is_written_down_including_the_refused_ones(tmp_path):
    ledger = tmp_path / "l.jsonl"
    planner = ModelRecoveryPlanner(client=_Client(_reply(affordance_id="invented")), ledger_path=ledger)

    planner.plan(_context(), goal_state="plan.confirmed == true")

    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["is_model_derived"] is False
    assert entries[0]["failure_type"] == "target_occluded"
    assert "accept_cookies" in entries[0]["offered"]


def test_a_broken_ledger_path_still_lets_the_run_continue(tmp_path):
    """A record that cannot be written must not be able to stop a recovery."""
    unwritable = tmp_path / "file.txt"
    unwritable.write_text("not a directory", encoding="utf-8")
    planner = ModelRecoveryPlanner(client=_Client(_reply()), ledger_path=unwritable / "l.jsonl")

    plan = planner.plan(_context(), goal_state="plan.confirmed == true")

    assert plan.actions[0].affordance_id == "accept_cookies"


# ── against the real runtime ─────────────────────────────────────────────────────
# The tests above use a context this file builds. That proves the refusals, and
# proves nothing about whether the runtime will accept what comes back. These two
# put the planner behind the real ContinuousInteractionManager, on the fixtures
# the existing recovery-contract test already uses, so "drop-in" is demonstrated
# rather than asserted from a signature.


def _runtime_with(planner, ledger):
    from src.runtime.cognitive_map import CognitiveMap
    from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
    from src.runtime.episode import EpisodePolicy
    from tests.test_precondition_repair import _ForbiddenInterventionBroker, _RecoveryExecutor, _RecoveryObservations

    return ContinuousInteractionManager(
        {},
        {"dom": _RecoveryExecutor()},
        CognitiveMap(task_id="model-recovery"),
        observation_provider=_RecoveryObservations(),
        episode_policy=EpisodePolicy(
            max_steps=6,
            deadline_s=2,
            max_retry_attempts=1,
            max_attempts_per_backend=6,
            require_fresh_observation=True,
        ),
        transition_ledger=ledger,
        system2_planner=planner,
        intervention_broker=_ForbiddenInterventionBroker(),
    )


def test_the_runtime_carries_out_a_recovery_this_planner_chose(tmp_path):
    """End to end: a model names the repair and the runtime completes the goal."""
    import asyncio

    from src.runtime.episode import TransitionLedger
    from src.runtime.state_machine import RuntimeState
    from tests.test_precondition_repair import _live_observation

    # The id is the one the fixture publishes; the model is not being helped to
    # guess it, it is being offered it like every other affordance.
    client = _Client(
        _reply(
            affordance_id="freshly-observed-repair",
            expected_effect="interaction_obstruction.present == false",
            reason="it declares that it remediates the failed target",
        )
    )
    planner = ModelRecoveryPlanner(client=client, ledger_path=tmp_path / "l.jsonl")

    result = asyncio.run(
        _runtime_with(planner, TransitionLedger()).run_observed_goal(
            _live_observation(done=False, obstruction=False, include_repair=False),
            goal_id="generic_goal",
            goal_state="oracle.done == true",
        )
    )

    assert result.state == RuntimeState.COMPLETED
    assert result.recovery_attempted and result.recovery_succeeded

    # Read the recovery decision, not the last one. The runtime calls the port
    # again after a successful repair to plan the retry, and that call has no
    # failure - so `last_choice` at the end of an episode describes the retry,
    # not what recovered it.
    recoveries = planner.recovery_choices()
    assert len(recoveries) == 1, f"expected one recovery decision, got {len(recoveries)}"
    assert recoveries[0].is_model_derived, "the recovery must be attributable to the model"
    assert recoveries[0].affordance_id == "freshly-observed-repair"


def test_with_no_model_the_port_escalates_exactly_as_it_does_today(tmp_path):
    """Without a model this must behave like the runtime already does - escalate.

    Worth pinning rather than assuming. The deterministic controller deliberately
    refuses to choose a recovery, because recovery semantics are the planner's to
    own; escalating is that refusal, not a defect. So wiring this adapter in
    before anyone has an API key changes nothing, which is what makes it safe to
    merge ahead of the key.
    """
    import asyncio

    from src.runtime.episode import TransitionLedger
    from tests.test_precondition_repair import _live_observation

    planner = ModelRecoveryPlanner(client=None, ledger_path=tmp_path / "l.jsonl")

    # _ForbiddenInterventionBroker raises if the runtime reaches Tier 4, which is
    # precisely the outcome expected here.
    try:
        asyncio.run(
            _runtime_with(planner, TransitionLedger()).run_observed_goal(
                _live_observation(done=False, obstruction=False, include_repair=False),
                goal_id="generic_goal",
                goal_state="oracle.done == true",
            )
        )
        escalated = False
    except AssertionError as exc:
        escalated = "Tier-4 intervention" in str(exc)

    assert escalated, "with no model the port should hand over, not invent a recovery"
    recoveries = planner.recovery_choices()
    assert recoveries and all(not choice.is_model_derived for choice in recoveries)
    assert recoveries[0].reason == "no model configured"


# ── unified forward + recovery authority ────────────────────────────────────────


def test_agent_planner_uses_a_distinct_forward_mode_over_safe_affordance_semantics(tmp_path):
    input_affordance = RuntimeAffordance(
        id="temperature-input",
        source="dom",
        entity_id="thermostat",
        action_name="input",
        action_type="input",
        confidence=0.95,
        grounding={"binds_parameter": "target_temperature", "selector": "#must-not-leak"},
    )
    client = _SequenceClient(
        [
            _reply(
                affordance_id="temperature-input",
                action="type",
                value=24,
                expected_effect="target_temperature == 24",
                reason="the affordance binds the requested target_temperature",
            )
        ]
    )
    planner = AgentPlanner(client=client, ledger_path=tmp_path / "agent.jsonl")

    plan = planner.plan(
        _context(failure=False, affordances=[input_affordance]),
        goal_id="set_temperature",
        goal_state="thermostat.target_temperature == 24",
        parameters={"target_temperature": 24},
    )

    assert plan.actions[0].action == "type"
    assert plan.actions[0].value == 24
    assert planner.last_choice.mode is PlanningMode.FORWARD
    assert planner.last_choice.is_model_derived
    assert "mode: forward" in client.prompts[0]
    assert "binds_parameter=target_temperature" in client.prompts[0]
    assert "must-not-leak" not in client.prompts[0]
    assert "next atomic action" in client.system_prompts[0]


def test_unsupported_forward_model_output_records_and_uses_deterministic_fallback(tmp_path):
    completion = RuntimeAffordance(
        id="finish",
        source="dom",
        entity_id="oracle",
        action_name="click",
        action_type="button",
        confidence=0.95,
        grounding={"completion_for": "generic_goal", "achieves": "oracle.done == true"},
    )
    planner = AgentPlanner(
        client=_Client(_reply(affordance_id="invented")),
        ledger_path=tmp_path / "agent.jsonl",
    )

    plan = planner.plan(
        _context(failure=False, affordances=[completion]),
        goal_id="generic_goal",
        goal_state="oracle.done == true",
    )

    assert [action.affordance_id for action in plan.actions] == ["finish"]
    assert planner.last_choice.mode is PlanningMode.FORWARD
    assert planner.last_choice.source == "unsupported"
    assert planner.last_choice.fallback_used is True
    entry = json.loads((tmp_path / "agent.jsonl").read_text(encoding="utf-8"))
    assert entry["mode"] == "forward"
    assert entry["fallback_used"] is True


def test_one_agent_planner_drives_forward_recovery_and_resumed_forward_actions(tmp_path):
    import asyncio

    from src.runtime.episode import TransitionLedger
    from src.runtime.state_machine import RuntimeState
    from tests.test_precondition_repair import _live_observation, _RecoveryExecutor

    client = _SequenceClient(
        [
            _reply(
                affordance_id="observed-target",
                expected_effect="oracle.done == true",
                reason="the target declares completion of the goal",
            ),
            _reply(
                affordance_id="freshly-observed-repair",
                expected_effect="interaction_obstruction.present == false",
                reason="the affordance remediates the failed target",
            ),
            _reply(
                affordance_id="observed-target",
                expected_effect="oracle.done == true",
                reason="the obstruction is cleared so the goal can resume",
            ),
        ]
    )
    planner = AgentPlanner(client=client, ledger_path=tmp_path / "agent.jsonl")
    runtime = _runtime_with(planner, TransitionLedger())
    executor = runtime.executors["dom"]

    result = asyncio.run(
        runtime.run_observed_goal(
            _live_observation(done=False, obstruction=False, include_repair=False),
            goal_id="generic_goal",
            goal_state="oracle.done == true",
        )
    )

    assert isinstance(executor, _RecoveryExecutor)
    assert result.state is RuntimeState.COMPLETED
    assert result.final_outcome_verified
    assert executor.calls == ["observed-target", "freshly-observed-repair", "observed-target"]
    assert [choice.mode for choice in planner.choices] == [
        PlanningMode.FORWARD,
        PlanningMode.RECOVERY,
        PlanningMode.FORWARD,
    ]
    assert all(choice.is_model_derived for choice in planner.choices)
