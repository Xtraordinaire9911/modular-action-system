"""One bounded Agent planner chooses forward and recovery primitive actions.

The runtime already returns typed failure evidence through
:class:`~src.runtime.planner_port.PlannerPort` and refuses to pick a recovery
itself, on the grounds that recovery semantics belong to the planner. Until now
the only implementation behind that port was the deterministic controller, so
the boundary existed and nothing external was on the other side of it.

This is that other side. The same planner owns both planning modes: forward mode
chooses the next primitive for the current goal, while recovery mode receives a
typed failure and the affordances observed *after* it. Runtime remains the
execution authority throughout: whatever comes back is still validated against
fresh affordances, still executed by the runtime, and still verified by
re-observation. Nothing here can act.

Four refusals, and each is a way this could have been dishonest instead:

* **An affordance the model invented is rejected outright.** The candidates are
  offered by id, and an id that was not offered is a hallucination, not a plan.
  Acting on one would be worse than escalating.
* **A model that is not configured does not silently become a deterministic
  plan wearing a model's name.** The fallback runs and is labelled
  ``deterministic``, which is what the ledger and the trace both record.
* **Forward and recovery decisions are explicitly distinguished.** A caller can
  keep forward planning deterministic, but both modes pass through this one
  PlannerPort authority and every recorded decision names its mode.
* **An unusable answer escalates with its reason stated.** "The model said
  nothing useful" is a defensible outcome; guessing an affordance because one
  had to be returned is not.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, cast, get_args

from src.runtime.action_context import ActionContext
from src.runtime.affordance_controller import AffordanceController, PrimitivePlan
from src.runtime.cognitive_map import RuntimeAffordance, canonical_state_name
from src.runtime.primitive_action import PrimitiveAction, PrimitiveActionType
from src.runtime.task_planner import primitive_for_affordance

# The action names the runtime can actually execute, read from the type itself so
# this cannot drift from it. `allowed_actions` on a context is a per-episode
# narrowing of these; a value outside *both* is not an action at all.
_EXECUTABLE_ACTIONS: frozenset[str] = frozenset(get_args(PrimitiveActionType))

DEFAULT_LEDGER = Path("artifacts/recovery_planner/calls.jsonl")

# The relations an affordance may declare about its role in a recovery. These are
# the environment's own words - the DOM transducer reads them from data-* and the
# TD parser from Thing Descriptions - so offering them to the model is passing on
# what the environment said, not an interpretation of it.
RECOVERY_RELATIONS = (
    "remediates",
    "compensates",
    "equivalent_to",
    "restores",
    "observes",
    "recovery_postcondition",
)

_RECOVERY_SYSTEM_PROMPT = """You choose how an agent should recover from one failed action \
in a smart-room environment.

You are given the failure and the affordances observed AFTER it. Reply with JSON \
only, no prose:
  affordance_id  the id of the affordance to use, copied exactly from the list
  action         one of: {actions}
  value          the value to enter, or null when the action needs none
  expected_effect  one sentence naming what this should achieve
  reason         one sentence saying why this recovers the failure
  confidence     number between 0 and 1

Rules:
- Choose only from the affordance ids given. Never invent one.
- Prefer an affordance that declares a recovery relation to the failed one
  (remediates, restores, compensates, equivalent_to).
- If nothing offered can recover this failure, reply with affordance_id "" and
  say so in reason. Refusing is a valid answer and a better one than guessing.
- Do not propose an action the allowed list does not contain.
"""

_FORWARD_SYSTEM_PROMPT = """You choose the next atomic action for an agent pursuing \
a structured goal.

You are given the current fresh state, prior attempts, and the affordances the \
runtime currently offers. Reply with JSON only, no prose:
  affordance_id  the id of the affordance to use, copied exactly from the list
  action         one of: {actions}
  value          the value to enter, or null when the action needs none
  expected_effect  one observable fact this action should establish
  reason         one sentence saying why this is the next action
  confidence     number between 0 and 1

Rules:
- Choose only from the affordance ids given. Never invent one.
- Use declared parameter bindings and goal-completion semantics when present.
- Completion affordances are withheld until fresh state proves every declared
  parameter binding has its requested value.
- Choose one action only. The runtime will execute it, re-observe, and call you again.
- Do not repeat an already successful action unless fresh state shows its effect is absent.
- If no offered affordance advances the goal, reply with affordance_id "" and say why.
- Do not propose an action the allowed list does not contain.
"""


class PlanningMode(str, Enum):
    """The two supported decisions owned by the unified Agent planner."""

    FORWARD = "forward"
    RECOVERY = "recovery"


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


@dataclass
class AgentChoice:
    """What was decided, by whom, and on what evidence."""

    mode: PlanningMode = PlanningMode.RECOVERY
    source: str = "none"  # llm | deterministic | unsupported
    affordance_id: str = ""
    action: str = ""
    value: Any = None
    expected_effect: str = ""
    reason: str = ""
    confidence: float = 0.0
    model: str = ""
    latency_ms: float = 0.0
    prompt: str = ""
    raw_response: str = ""
    error: str = ""
    offered: list[str] = field(default_factory=list)
    fallback_used: bool = False

    @property
    def is_model_derived(self) -> bool:
        return self.source == "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "source": self.source,
            "affordance_id": self.affordance_id,
            "action": self.action,
            "value": self.value,
            "expected_effect": self.expected_effect,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "prompt": self.prompt,
            "raw_response": self.raw_response,
            "is_model_derived": self.is_model_derived,
            "offered": list(self.offered),
            "error": self.error,
            "fallback_used": self.fallback_used,
        }


# Compatibility for existing reports and imports. AgentChoice is now the
# authoritative decision algebra; RecoveryChoice remains a source-compatible
# name while downstream consumers migrate.
RecoveryChoice = AgentChoice


PLANNING_SEMANTICS = (
    "state_attribute",
    "binds_parameter",
    "binds_parameters",
    "accepts_parameter",
    "accepts_parameters",
    "parameter",
    "parameters",
    "completion_for",
    "goal_id",
    "goal_ids",
    "achieves",
    "achieves_goal",
    "effects",
    *RECOVERY_RELATIONS,
)


def _semantics_of(affordance: Any) -> dict[str, Any]:
    """Planner-safe forward and recovery semantics declared by an affordance."""
    grounding = getattr(affordance, "grounding", {}) or {}
    return {name: grounding[name] for name in PLANNING_SEMANTICS if grounding.get(name)}


def _as_string_set(*values: Any) -> set[str]:
    strings: set[str] = set()
    for value in values:
        if isinstance(value, str):
            strings.add(value)
        elif isinstance(value, list | tuple | set):
            strings.update(item for item in value if isinstance(item, str))
    return strings


def _bound_parameters(affordance: RuntimeAffordance) -> set[str]:
    grounding = affordance.grounding
    return _as_string_set(
        grounding.get("binds_parameter"),
        grounding.get("binds_parameters"),
        grounding.get("parameter"),
        grounding.get("parameters"),
        grounding.get("accepts_parameter"),
        grounding.get("accepts_parameters"),
    )


def _declares_completion(affordance: RuntimeAffordance) -> bool:
    grounding = affordance.grounding
    return bool(
        _as_string_set(
            grounding.get("completion_for"),
            grounding.get("goal_id"),
            grounding.get("goal_ids"),
            grounding.get("achieves"),
            grounding.get("achieves_goal"),
            grounding.get("effects"),
        )
    )


def _completes_goal(affordance: RuntimeAffordance, goal_id: str, goal_state: str) -> bool:
    grounding = affordance.grounding
    declared = _as_string_set(
        grounding.get("completion_for"),
        grounding.get("goal_id"),
        grounding.get("goal_ids"),
        grounding.get("achieves"),
        grounding.get("achieves_goal"),
        grounding.get("effects"),
    )
    return goal_id in declared or goal_state in declared


def _observed_parameter_effect(
    context: ActionContext,
    affordance: RuntimeAffordance,
    parameter: str,
    expected: Any,
) -> bool:
    """Whether fresh planner-visible state proves one parameter binding."""

    declared_attribute = str(affordance.grounding.get("state_attribute") or parameter).strip()
    attributes = {
        declared_attribute,
        canonical_state_name(declared_attribute),
        parameter,
        canonical_state_name(parameter),
    }
    for source_state in context.state.values():
        if not isinstance(source_state, dict):
            continue
        entity_state = source_state.get(affordance.entity_id)
        if not isinstance(entity_state, dict):
            continue
        for attribute in attributes:
            if attribute in entity_state and entity_state[attribute] == expected:
                return True
    return False


def _unfinished_parameter_bindings(
    context: ActionContext,
    parameters: dict[str, Any],
) -> set[str]:
    unfinished: set[str] = set()
    for parameter, expected in parameters.items():
        bindings = [affordance for affordance in context.affordances if parameter in _bound_parameters(affordance)]
        if not bindings or not any(
            _observed_parameter_effect(context, affordance, parameter, expected) for affordance in bindings
        ):
            unfinished.add(parameter)
    return unfinished


def _effective_forward_context(
    context: ActionContext,
    *,
    goal_id: str,
    goal_state: str,
    parameters: dict[str, Any],
) -> ActionContext:
    """Withhold completion until every effective parameter value is observed."""

    unfinished = _unfinished_parameter_bindings(context, parameters)
    affordances = [
        affordance
        for affordance in context.affordances
        if not _declares_completion(affordance)
        # A primitive may both bind a parameter and declare the resulting goal
        # effect (for example a writable WoT property). It must remain offered
        # while that parameter is unfinished; otherwise completion gating
        # removes the only action capable of establishing its own prerequisite.
        or bool(_bound_parameters(affordance) & unfinished)
        or (_completes_goal(affordance, goal_id, goal_state) and not unfinished)
    ]
    return replace(context, affordances=affordances)


def candidate_lines(context: ActionContext) -> list[str]:
    """The affordances exactly as the model will see them.

    Ids and declared relations only. No selector, no href, no backend handle: a
    planner that could read those could route around the runtime, and the point
    of the port is that it cannot.
    """
    lines: list[str] = []
    for affordance in context.affordances:
        semantics = _semantics_of(affordance)
        described = f"  {affordance.id}  action={affordance.action_type}  entity={affordance.entity_id}"
        if getattr(affordance, "action_name", ""):
            described += f'  name="{affordance.action_name}"'
        label = str(getattr(affordance, "grounding", {}).get("label") or "").strip()
        if label and label != getattr(affordance, "action_name", ""):
            described += f"  label={json.dumps(label)}"
        if semantics:
            described += "  " + " ".join(f"{key}={value}" for key, value in semantics.items())
        lines.append(described)
    return lines


def _extract_json(text: str) -> dict[str, Any]:
    """The JSON object in a reply, allowing for a fenced code block."""
    body = text.strip()
    if "```" in body:
        chunks = [chunk for chunk in body.split("```") if "{" in chunk]
        if chunks:
            body = chunks[0]
            if body.lstrip().lower().startswith("json"):
                body = body.lstrip()[4:]
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in the reply")
    parsed = json.loads(body[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("the reply was not a JSON object")
    return parsed


@dataclass
class AgentPlanner:
    """The single PlannerPort authority for forward and recovery decisions."""

    client: LLMClient | None = None
    controller: AffordanceController = field(default_factory=AffordanceController)
    ledger_path: Path = field(default_factory=lambda: DEFAULT_LEDGER)
    # False retains a deterministic forward path while keeping one decision
    # authority. The formal Agent entrypoint enables this when model mode is on.
    plan_forward_with_model: bool = True
    # Opt-in for controlled demos and deployments whose policy permits the
    # planner to use explicit, observed, low-risk recovery relations without a
    # model.  The default preserves the conservative hand-off behavior.
    allow_deterministic_recovery: bool = False
    last_choice: AgentChoice = field(default_factory=AgentChoice)
    # Every decision this planner made, in order. `last_choice` alone is not
    # enough to read a finished episode: the runtime calls the port again after a
    # successful recovery to plan the retry, and that forward call overwrites the
    # recovery entry. Anyone asking "what recovered this episode" afterwards
    # would get the wrong answer without the mode-filtered history.
    choices: list[AgentChoice] = field(default_factory=list)

    def plan(
        self,
        context: ActionContext,
        *,
        goal_id: str = "",
        goal_state: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> PrimitivePlan:
        parameters = parameters or {}
        mode = PlanningMode.RECOVERY if context.failure is not None else PlanningMode.FORWARD
        planning_context = (
            _effective_forward_context(
                context,
                goal_id=goal_id,
                goal_state=goal_state,
                parameters=parameters,
            )
            if mode is PlanningMode.FORWARD
            else context
        )

        if mode is PlanningMode.FORWARD and not self.plan_forward_with_model:
            return self._deterministic_decision(
                planning_context,
                record_context=context,
                goal_id=goal_id,
                goal_state=goal_state,
                parameters=parameters,
                choice=AgentChoice(
                    mode=mode,
                    source="deterministic",
                    reason="forward model planning is disabled",
                    offered=[affordance.id for affordance in planning_context.affordances],
                ),
            )

        if self.client is None:
            return self._deterministic_decision(
                planning_context,
                record_context=context,
                goal_id=goal_id,
                goal_state=goal_state,
                parameters=parameters,
                choice=AgentChoice(
                    mode=mode,
                    source="deterministic",
                    reason="no model configured",
                    offered=[affordance.id for affordance in planning_context.affordances],
                ),
            )

        choice = self._ask(self.client, planning_context, goal_id, goal_state, parameters, mode)
        if not choice.is_model_derived:
            # The model attempt remains attributable as unsupported, while this
            # flag makes the effective deterministic fallback explicit.
            choice.fallback_used = True
            return self._deterministic_decision(
                planning_context,
                record_context=context,
                goal_id=goal_id,
                goal_state=goal_state,
                parameters=parameters,
                choice=choice,
            )

        plan = PrimitivePlan(
            actions=[
                PrimitiveAction(
                    action=cast(PrimitiveActionType, choice.action),
                    affordance_id=choice.affordance_id,
                    value=choice.value,
                    expected_effect=choice.expected_effect or choice.reason,
                )
            ],
            reason=f"model {mode.value}: {choice.reason}",
        )
        self._remember(choice)
        self._record(context, goal_state)
        return plan

    def _deterministic_decision(
        self,
        context: ActionContext,
        *,
        record_context: ActionContext,
        goal_id: str,
        goal_state: str,
        parameters: dict[str, Any],
        choice: AgentChoice,
    ) -> PrimitivePlan:
        """Return and record the one controller primitive effective now."""

        recovery_plan = self._declared_safe_recovery(context) if self.allow_deterministic_recovery else None
        controller_plan = recovery_plan or self.controller.plan(
            context,
            goal_id=goal_id,
            goal_state=goal_state,
            parameters=parameters,
        )
        if recovery_plan is not None:
            prefix = f"{choice.reason}; " if choice.reason else ""
            choice.reason = prefix + "selected an observed affordance that declares a safe recovery relation"
        plan = self._effective_controller_plan(controller_plan, context, parameters)
        if plan.actions:
            action = plan.actions[0]
            choice.affordance_id = action.affordance_id
            choice.action = action.action
            choice.value = action.value
            choice.expected_effect = action.expected_effect
        self._remember(choice)
        self._record(record_context, goal_state)
        return plan

    @staticmethod
    def _declared_safe_recovery(context: ActionContext) -> PrimitivePlan | None:
        """Choose only an explicitly related, low-risk observed remediation.

        This is the deterministic fallback *inside* the injected AgentPlanner,
        not a Runtime recovery policy.  Runtime still validates the returned
        primitive against the same fresh ActionContext, executes it through its
        normal effector, and verifies the declared postcondition.  Ambiguous,
        unrelated, irreversible, or unmarked capabilities remain escalations.
        """

        failure = context.failure
        if failure is None:
            return None

        failed_ids = {failure.failed_affordance_id, failure.transition_id}
        candidates: list[RuntimeAffordance] = []
        for affordance in context.affordances:
            grounding = affordance.grounding
            related = _as_string_set(
                grounding.get("remediates"),
                grounding.get("compensates"),
                grounding.get("restores"),
                grounding.get("equivalent_to"),
            )
            action = primitive_for_affordance(affordance)
            if (
                related & failed_ids
                and grounding.get("recovery_safe") is True
                and grounding.get("irreversible") is not True
                and action in context.allowed_actions
            ):
                candidates.append(affordance)

        if not candidates:
            return None

        chosen = sorted(candidates, key=lambda item: (-item.confidence, item.id))[0]
        expected_effect = str(
            chosen.grounding.get("recovery_postcondition")
            or f"recover from {failure.failed_affordance_id}"
        )
        return PrimitivePlan(
            actions=[
                PrimitiveAction(
                    primitive_for_affordance(chosen),
                    affordance_id=chosen.id,
                    expected_effect=expected_effect,
                )
            ],
            reason="deterministic semantic recovery from a fresh observed affordance",
        )

    @staticmethod
    def _effective_controller_plan(
        plan: PrimitivePlan,
        context: ActionContext,
        parameters: dict[str, Any],
    ) -> PrimitivePlan:
        """Normalize the controller output to the primitive Runtime will receive."""

        if plan.requires_escalation:
            action = next((candidate for candidate in plan.actions if candidate.action == "ask_user"), None)
            return replace(plan, actions=[action] if action is not None else plan.actions[:1])

        affordances = {affordance.id: affordance for affordance in context.affordances}
        for action in plan.actions:
            affordance = affordances.get(action.affordance_id)
            if affordance is not None:
                bound = _bound_parameters(affordance) & set(parameters)
                if bound and all(
                    _observed_parameter_effect(context, affordance, parameter, parameters[parameter])
                    for parameter in bound
                ):
                    continue
            return replace(plan, actions=[action])
        return replace(plan, actions=[])

    def _remember(self, choice: AgentChoice) -> None:
        self.last_choice = choice
        self.choices.append(choice)

    def recovery_choices(self) -> list[AgentChoice]:
        """Only the decisions taken in response to a failure.

        This is what a report should quote. Forward-planning calls around a
        recovery say nothing about which decision recovered the failed action.
        """
        return [choice for choice in self.choices if choice.mode is PlanningMode.RECOVERY]

    def forward_choices(self) -> list[AgentChoice]:
        """All decisions taken while advancing the original goal."""

        return [choice for choice in self.choices if choice.mode is PlanningMode.FORWARD]

    # ── asking ────────────────────────────────────────────────────────────────

    def _ask(
        self,
        client: LLMClient,
        context: ActionContext,
        goal_id: str,
        goal_state: str,
        parameters: dict[str, Any],
        mode: PlanningMode,
    ) -> AgentChoice:
        # The client is passed in rather than read from self, so that "there is a
        # model here" is guaranteed by the type rather than by remembering that
        # the caller checked.
        offered = [affordance.id for affordance in context.affordances]
        choice = AgentChoice(
            mode=mode,
            offered=offered,
            model=getattr(client, "name", "") or "unknown",
        )
        if not offered:
            choice.source = "unsupported"
            choice.error = "the observation offered no affordances to choose from"
            return choice

        prompt = _RECOVERY_SYSTEM_PROMPT if mode is PlanningMode.RECOVERY else _FORWARD_SYSTEM_PROMPT
        system = prompt.format(actions=", ".join(context.allowed_actions))
        user = self._describe(context, goal_id, goal_state, parameters, mode)
        choice.prompt = user

        started = time.monotonic()
        try:
            raw = client.complete(system, user)
        except Exception as exc:  # a model failure is recorded, never swallowed
            choice.latency_ms = (time.monotonic() - started) * 1000
            choice.source = "unsupported"
            choice.error = f"{type(exc).__name__}: {exc}"
            return choice
        choice.latency_ms = (time.monotonic() - started) * 1000
        choice.raw_response = raw

        try:
            parsed = _extract_json(raw)
        except ValueError as exc:
            choice.source = "unsupported"
            choice.error = f"unparseable reply: {exc}"
            return choice

        chosen = str(parsed.get("affordance_id", "") or "").strip()
        choice.reason = str(parsed.get("reason", "") or "").strip()
        choice.confidence = float(parsed.get("confidence", 0.0) or 0.0)
        choice.expected_effect = str(parsed.get("expected_effect", "") or "").strip()

        if not chosen:
            # A declared refusal. Recorded as such rather than as a failure,
            # because "nothing here recovers this" is information.
            choice.source = "unsupported"
            choice.error = choice.reason or f"the model declined to propose a {mode.value} action"
            return choice

        if chosen not in offered:
            # The failure mode this guard exists for: acting on an id nobody
            # offered would be executing a sentence, not a plan.
            choice.source = "unsupported"
            choice.error = f"model chose {chosen!r}, which was not offered"
            return choice

        action = str(parsed.get("action", "") or "").strip()
        # Both checks are needed and they are not the same check. The episode's
        # allowed list is a policy narrowing; the executable set is what the
        # runtime can carry out at all. Passing only the first would let a
        # mis-declared allowed list produce an action the runtime cannot run, and
        # the cast at the end of plan() would then be a lie the type checker had
        # been told to accept.
        if action not in _EXECUTABLE_ACTIONS:
            choice.source = "unsupported"
            choice.error = f"model chose action {action!r}, which is not an executable action"
            return choice
        if action not in context.allowed_actions:
            choice.source = "unsupported"
            choice.error = f"model chose action {action!r}, which is not allowed here"
            return choice

        choice.source = "llm"
        choice.affordance_id = chosen
        choice.action = action
        choice.value = parsed.get("value")
        return choice

    def _describe(
        self,
        context: ActionContext,
        goal_id: str,
        goal_state: str,
        parameters: dict[str, Any],
        mode: PlanningMode,
    ) -> str:
        failure = context.failure
        parts = [
            f"mode: {mode.value}",
            f"goal_id: {goal_id}",
            f"goal_state: {goal_state}",
            f"parameters: {json.dumps(parameters, default=str)}",
            f"current_state: {json.dumps(context.state, default=str)}",
        ]
        if failure is not None:
            parts += [
                "",
                "the failure:",
                f"  action           : {failure.failed_action}",
                f"  affordance       : {failure.failed_affordance_id}",
                f"  entity           : {failure.failed_entity_id}",
                f"  expected effect  : {failure.expected_effect}",
                f"  failure type     : {failure.failure_type}",
                f"  boundary         : {failure.failure_boundary}",
                f"  reason           : {failure.reason}",
            ]
        if context.attempted_actions:
            parts += ["", "already attempted:"]
            parts += [
                f"  {attempt.action} on {attempt.affordance_id} -> {attempt.outcome}"
                for attempt in context.attempted_actions
            ]
        if context.unresolved_conflicts:
            parts += ["", f"unresolved conflicts: {len(context.unresolved_conflicts)}"]
        heading = "affordances observed after the failure:" if failure is not None else "affordances observed now:"
        parts += ["", heading]
        parts += candidate_lines(context)
        if context.safety_constraints:
            parts += ["", "safety constraints: " + ", ".join(context.safety_constraints)]
        if context.remaining_retries is not None:
            parts.append(f"retries remaining: {context.remaining_retries}")
        return "\n".join(parts)

    # ── the record ────────────────────────────────────────────────────────────

    def _record(self, context: ActionContext, goal_state: str) -> None:
        """Append this decision to the ledger. A logging failure never breaks a run."""
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task_id": getattr(context, "task_id", ""),
            "goal_state": goal_state,
            "mode": self.last_choice.mode.value,
            "failure_type": getattr(context.failure, "failure_type", ""),
            "failed_affordance_id": getattr(context.failure, "failed_affordance_id", ""),
            **self.last_choice.to_dict(),
        }
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except Exception:  # noqa: BLE001 - a ledger must never break a run
            pass


@dataclass
class ModelRecoveryPlanner(AgentPlanner):
    """Backward-compatible recovery-first configuration of :class:`AgentPlanner`."""

    plan_forward_with_model: bool = False


__all__ = [
    "RECOVERY_RELATIONS",
    "PLANNING_SEMANTICS",
    "AgentChoice",
    "AgentPlanner",
    "LLMClient",
    "ModelRecoveryPlanner",
    "PlanningMode",
    "RecoveryChoice",
    "candidate_lines",
]
