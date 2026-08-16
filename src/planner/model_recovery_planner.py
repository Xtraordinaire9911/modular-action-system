"""A model chooses the recovery, from the affordances the runtime actually offers.

The runtime already returns typed failure evidence through
:class:`~src.runtime.planner_port.PlannerPort` and refuses to pick a recovery
itself, on the grounds that recovery semantics belong to the planner. Until now
the only implementation behind that port was the deterministic controller, so
the boundary existed and nothing external was on the other side of it.

This is that other side. Given a failure and the affordances observed *after* it,
it asks a model which one recovers the goal and why. Runtime remains the
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
* **Nothing is planned for a context with no failure.** Recovery is what this is
  for; ordinary forward planning stays with the controller, so a run cannot
  quietly start paying for model calls it did not need.
* **An unusable answer escalates with its reason stated.** "The model said
  nothing useful" is a defensible outcome; guessing an affordance because one
  had to be returned is not.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast, get_args

from src.runtime.action_context import ActionContext
from src.runtime.affordance_controller import AffordanceController, PrimitivePlan
from src.runtime.primitive_action import PrimitiveAction, PrimitiveActionType

# The action names the runtime can actually execute, read from the type itself so
# this cannot drift from it. `allowed_actions` on a context is a per-episode
# narrowing of these; a value outside *both* is not an action at all.
_EXECUTABLE_ACTIONS: frozenset[str] = frozenset(get_args(PrimitiveActionType))

DEFAULT_LEDGER = Path("artifacts/recovery_planner/calls.jsonl")

# The relations an affordance may declare about its role in a recovery. These are
# the environment's own words - the DOM transducer reads them from data-* and the
# TD parser from Thing Descriptions - so offering them to the model is passing on
# what the environment said, not an interpretation of it.
RECOVERY_RELATIONS = ("remediates", "compensates", "equivalent_to", "restores", "observes")

_SYSTEM_PROMPT = """You choose how an agent should recover from one failed action \
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


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


@dataclass
class RecoveryChoice:
    """What was decided, by whom, and on what evidence."""

    source: str = "none"  # llm | deterministic | unsupported
    affordance_id: str = ""
    action: str = ""
    value: Any = None
    expected_effect: str = ""
    reason: str = ""
    confidence: float = 0.0
    model: str = ""
    latency_ms: float = 0.0
    raw_response: str = ""
    error: str = ""
    offered: list[str] = field(default_factory=list)

    @property
    def is_model_derived(self) -> bool:
        return self.source == "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "affordance_id": self.affordance_id,
            "action": self.action,
            "value": self.value,
            "expected_effect": self.expected_effect,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "is_model_derived": self.is_model_derived,
            "offered": list(self.offered),
            "error": self.error,
        }


def _relations_of(affordance: Any) -> dict[str, Any]:
    """The recovery relations this affordance declares, if any."""
    grounding = getattr(affordance, "grounding", {}) or {}
    return {name: grounding[name] for name in RECOVERY_RELATIONS if grounding.get(name)}


def candidate_lines(context: ActionContext) -> list[str]:
    """The affordances exactly as the model will see them.

    Ids and declared relations only. No selector, no href, no backend handle: a
    planner that could read those could route around the runtime, and the point
    of the port is that it cannot.
    """
    lines: list[str] = []
    for affordance in context.affordances:
        relations = _relations_of(affordance)
        described = f"  {affordance.id}  action={affordance.action_type}  entity={affordance.entity_id}"
        if getattr(affordance, "action_name", ""):
            described += f'  name="{affordance.action_name}"'
        if relations:
            described += "  " + " ".join(f"{key}={value}" for key, value in relations.items())
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
class ModelRecoveryPlanner:
    """A :class:`PlannerPort` that asks a model, and can always answer without one."""

    client: LLMClient | None = None
    controller: AffordanceController = field(default_factory=AffordanceController)
    ledger_path: Path = field(default_factory=lambda: DEFAULT_LEDGER)
    # Recovery only. Forward planning is the controller's job and does not need a
    # model, so leaving this False keeps a normal episode free of model calls.
    plan_forward_with_model: bool = False
    last_choice: RecoveryChoice = field(default_factory=RecoveryChoice)
    # Every decision this planner made, in order. `last_choice` alone is not
    # enough to read a finished episode: the runtime calls the port again after a
    # successful recovery to plan the retry, and that call has no failure, so it
    # overwrites the recovery with a deterministic entry. Anyone asking "what
    # recovered this episode" afterwards would get the wrong answer.
    choices: list[RecoveryChoice] = field(default_factory=list)

    def plan(
        self,
        context: ActionContext,
        *,
        goal_id: str = "",
        goal_state: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> PrimitivePlan:
        parameters = parameters or {}
        deterministic = lambda: self.controller.plan(  # noqa: E731 - one call, three sites
            context, goal_id=goal_id, goal_state=goal_state, parameters=parameters
        )

        if context.failure is None and not self.plan_forward_with_model:
            self._remember(RecoveryChoice(source="deterministic", reason="no failure to recover from"))
            return deterministic()

        if self.client is None:
            self._remember(RecoveryChoice(source="deterministic", reason="no model configured"))
            self._record(context, goal_state)
            return deterministic()

        choice = self._ask(self.client, context, goal_state, parameters)
        self._remember(choice)
        self._record(context, goal_state)

        if not choice.is_model_derived:
            # The model was consulted and did not produce a usable choice. The
            # deterministic controller answers, and the trace says which one did.
            return deterministic()

        return PrimitivePlan(
            actions=[
                PrimitiveAction(
                    action=cast(PrimitiveActionType, choice.action),
                    affordance_id=choice.affordance_id,
                    value=choice.value,
                    expected_effect=choice.expected_effect or choice.reason,
                )
            ],
            reason=f"model recovery: {choice.reason}",
        )

    def _remember(self, choice: RecoveryChoice) -> None:
        self.last_choice = choice
        self.choices.append(choice)

    def recovery_choices(self) -> list[RecoveryChoice]:
        """Only the decisions taken in response to a failure.

        This is what a report should quote. The forward-planning calls around a
        recovery are deterministic by design and say nothing about who recovered.
        """
        return [c for c in self.choices if c.reason != "no failure to recover from"]

    # ── asking ────────────────────────────────────────────────────────────────

    def _ask(
        self,
        client: LLMClient,
        context: ActionContext,
        goal_state: str,
        parameters: dict[str, Any],
    ) -> RecoveryChoice:
        # The client is passed in rather than read from self, so that "there is a
        # model here" is guaranteed by the type rather than by remembering that
        # the caller checked.
        offered = [affordance.id for affordance in context.affordances]
        choice = RecoveryChoice(
            offered=offered,
            model=getattr(client, "name", "") or "unknown",
        )
        if not offered:
            choice.source = "unsupported"
            choice.error = "the observation offered no affordances to choose from"
            return choice

        system = _SYSTEM_PROMPT.format(actions=", ".join(context.allowed_actions))
        user = self._describe(context, goal_state, parameters)

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
            choice.error = choice.reason or "the model declined to propose a recovery"
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

    def _describe(self, context: ActionContext, goal_state: str, parameters: dict[str, Any]) -> str:
        failure = context.failure
        parts = [f"goal_state: {goal_state}", f"parameters: {json.dumps(parameters, default=str)}"]
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
        parts += ["", "affordances observed after the failure:"]
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


__all__ = [
    "RECOVERY_RELATIONS",
    "LLMClient",
    "ModelRecoveryPlanner",
    "RecoveryChoice",
    "candidate_lines",
]
