"""Layer 1 of the planner: a natural-language intent becomes a GoalSpec.

This is the layer the review has flagged as missing since July. Everything below
it - the deliberative planner that turns a GoalSpec into skills, and the
expansion of skills into primitive actions - already exists and works. What did
not exist was any way for the system to start from what a person actually says.
Runs began from a GoalSpec someone had written by hand, so the claim of an
autonomous agent rested on a step a human performed off-screen.

Three properties this is built around, in order of how much they matter.

**Provenance is never ambiguous.** Every result records whether it came from a
model or from the rule-based fallback. A fallback that quietly passes itself off
as interpretation would make the whole claim unverifiable, which is worse than
having no layer at all. ``GoalPlan.source`` is the field a reader should check
before believing anything else in this module.

**Every call is logged.** Prompt, raw response, parsed result, latency and model
are written to a JSONL ledger, because a planner that cannot be audited cannot
be evaluated - and evaluation was the explicit condition attached to adding it.

**The layer stays separate.** It emits a GoalSpec and stops. It does not choose
skills, does not touch effectors, and knows nothing about DOM or WoT. Keeping
the boundary sharp is what allows the model to be swapped, disabled, or compared
against the deterministic path without disturbing anything downstream.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from src.runtime.goal_spec import GoalSpec

DEFAULT_LEDGER = Path("artifacts/intent_planner/calls.jsonl")

# Kept explicit rather than free-form so the deliberative layer below can rely on
# a closed vocabulary. Adding a capability means adding it here on purpose.
KNOWN_GOAL_STATES = (
    "room_prepared",
    "temperature_set",
    "lighting_set",
    "projector_on",
    "projector_off",
    "blinds_set",
    "item_in_cart",
    "message_archived",
    "post_upvoted",
)

_SYSTEM_PROMPT = """You convert a user's request into a structured goal for a \
smart-room agent.

Reply with JSON only, no prose, using exactly these keys:
  goal_state   one of: {states}
  parameters   object of concrete values (room, target, item, degrees, percent)
  description  one sentence restating the request
  success_evidence  list of observable facts that would prove the goal was met
  safety_constraints  list of limits the agent must respect, may be empty
  confidence   number between 0 and 1

Rules:
- Choose the single goal_state that best fits. If none fit, use "unsupported".
- Put every concrete value in parameters; do not leave them in the description.
- success_evidence must be checkable by re-observing the environment, not by \
trusting that an action ran.
"""


class LLMClient(Protocol):
    """Minimal surface so the model can be swapped or faked in tests."""

    name: str

    def complete(self, system: str, user: str) -> str: ...


@dataclass
class GoalPlan:
    """A GoalSpec plus the provenance a reviewer needs to judge it."""

    goal: GoalSpec | None
    source: str  # "llm" | "rule_fallback" | "unsupported"
    model: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    latency_ms: float = 0.0
    raw_response: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.goal is not None

    @property
    def is_model_derived(self) -> bool:
        """False for anything the deterministic fallback produced."""
        return self.source == "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model": self.model,
            "confidence": round(self.confidence, 3),
            "latency_ms": round(self.latency_ms, 1),
            "goal_state": self.goal.goal_state if self.goal else None,
            "parameters": dict(self.goal.parameters) if self.goal else {},
            "error": self.error,
        }


class AnthropicClient:
    """Claude via the SDK already declared in pyproject."""

    def __init__(self, *, model: str = "claude-sonnet-5", api_key: str | None = None) -> None:
        self.name = model
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def complete(self, system: str, user: str) -> str:
        import anthropic  # lazy: only needed when a model is actually used

        client = anthropic.Anthropic(api_key=self._key)
        message = client.messages.create(
            model=self.name,
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # A reply is a list of blocks of several kinds; only text blocks carry
        # the answer. Checked with getattr on a widened value so the SDK can add
        # block types without this failing to type-check.
        parts: list[str] = []
        for block in message.content:
            candidate: Any = block
            if getattr(candidate, "type", "") == "text":
                parts.append(str(candidate.text))
        return "".join(parts)


class OpenAIClient:
    """Any OpenAI-compatible endpoint, using the SDK already declared."""

    def __init__(self, *, model: str = "gpt-4o-mini", api_key: str | None = None, base_url: str | None = None) -> None:
        self.name = model
        self._key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url
        if not self._key:
            raise RuntimeError("OPENAI_API_KEY is not set")

    def complete(self, system: str, user: str) -> str:
        from openai import OpenAI  # lazy

        client = OpenAI(api_key=self._key, base_url=self._base_url)
        response = client.chat.completions.create(
            model=self.name,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=800,
        )
        return response.choices[0].message.content or ""


def available_client() -> LLMClient | None:
    """The first configured client, or None when nothing is configured.

    Returning None rather than raising is deliberate: the caller then records a
    rule_fallback result, which is a claim it can defend, instead of a model
    result it cannot.
    """
    for factory in (AnthropicClient, OpenAIClient):
        try:
            return factory()  # type: ignore[abstract]
        except Exception:
            continue
    return None


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a reply that may be fenced or padded."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(candidate[start : end + 1])


# --- the deterministic fallback -------------------------------------------------
# Not an interpreter. It recognises a handful of phrasings so a run without an
# API key still produces something, and it labels itself so nobody mistakes it
# for the model path.

_RULES: list[tuple[str, str, str]] = [
    (r"\b(\d{1,2})\s*(?:degrees|degree|°c?|c)\b", "temperature_set", "degrees"),
    (r"\bbrightness\s*(?:to\s*)?(\d{1,3})\s*%?", "lighting_set", "percent"),
    (r"\bblinds?\b.*?(\d{1,3})\s*%?", "blinds_set", "percent"),
]
_KEYWORD_GOALS: list[tuple[str, str]] = [
    (r"\bprojector\b.*\b(on|start|turn on)\b", "projector_on"),
    (r"\bprojector\b.*\b(off|stop|turn off)\b", "projector_off"),
    (r"\b(add|put).*\bcart\b", "item_in_cart"),
    (r"\barchive\b", "message_archived"),
    (r"\bupvote\b", "post_upvoted"),
    (r"\bprepare\b.*\broom\b", "room_prepared"),
]


def rule_fallback(intent: str) -> GoalPlan:
    """Pattern matching, honestly labelled as such."""
    text = intent.lower()
    parameters: dict[str, Any] = {}
    goal_state = ""

    for pattern, state, key in _RULES:
        match = re.search(pattern, text)
        if match:
            goal_state, parameters[key] = state, int(match.group(1))
            break
    if not goal_state:
        for pattern, state in _KEYWORD_GOALS:
            if re.search(pattern, text):
                goal_state = state
                break

    room = re.search(r"\broom\s+([a-z0-9]+)\b", text)
    if room:
        parameters["room"] = room.group(1).upper()

    if not goal_state:
        return GoalPlan(
            goal=None,
            source="unsupported",
            reasoning="no rule matched, and no model was configured",
            error="unsupported intent",
        )
    return GoalPlan(
        goal=GoalSpec(
            goal_id=f"intent_{abs(hash(intent)) % 10**8}",
            goal_state=goal_state,
            parameters=parameters,
            description=intent.strip(),
        ),
        source="rule_fallback",
        confidence=0.4,  # a keyword hit is weak evidence and is scored as such
        reasoning="matched a known phrasing pattern; no interpretation was performed",
    )


@dataclass
class IntentPlanner:
    """Turns what a person said into the GoalSpec the runtime already accepts."""

    client: LLMClient | None = None
    ledger_path: Path = field(default_factory=lambda: DEFAULT_LEDGER)
    allow_fallback: bool = True

    def plan(self, intent: str, *, context: dict[str, Any] | None = None) -> GoalPlan:
        """Interpret ``intent``; fall back to rules only when no model is set."""
        if self.client is None:
            plan = (
                rule_fallback(intent)
                if self.allow_fallback
                else GoalPlan(goal=None, source="unsupported", error="no model configured")
            )
            self._log(intent, plan, prompt="")
            return plan

        system = _SYSTEM_PROMPT.format(states=", ".join(KNOWN_GOAL_STATES))
        user = intent if not context else f"{intent}\n\nEnvironment context:\n{json.dumps(context, indent=2)}"
        started = time.monotonic()
        try:
            raw = self.client.complete(system, user)
            payload = _extract_json(raw)
            plan = self._to_plan(intent, payload, raw)
        except Exception as exc:
            # A model failure must not silently become a model success. The
            # fallback still runs, but the result says where it came from.
            plan = rule_fallback(intent) if self.allow_fallback else GoalPlan(goal=None, source="unsupported")
            plan.error = f"{type(exc).__name__}: {exc}"
        plan.latency_ms = (time.monotonic() - started) * 1000.0
        plan.model = getattr(self.client, "name", "")
        self._log(intent, plan, prompt=user)
        return plan

    def _to_plan(self, intent: str, payload: dict[str, Any], raw: str) -> GoalPlan:
        state = str(payload.get("goal_state", "")).strip()
        if state == "unsupported" or state not in KNOWN_GOAL_STATES:
            return GoalPlan(
                goal=None,
                source="unsupported",
                raw_response=raw,
                reasoning=str(payload.get("description", "")),
                error=f"unsupported goal_state {state!r}",
            )
        return GoalPlan(
            goal=GoalSpec(
                goal_id=f"intent_{abs(hash(intent)) % 10**8}",
                goal_state=state,
                parameters=dict(payload.get("parameters") or {}),
                description=str(payload.get("description", intent)).strip(),
                safety_constraints=list(payload.get("safety_constraints") or []),
                success_evidence=list(payload.get("success_evidence") or []),
            ),
            source="llm",
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            reasoning=str(payload.get("description", "")),
            raw_response=raw,
        )

    def _log(self, intent: str, plan: GoalPlan, *, prompt: str) -> None:
        """Append the call to a JSONL ledger; never let logging break a run."""
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "at": datetime.now(timezone.utc).isoformat(),
                "intent": intent,
                "prompt": prompt,
                "raw_response": plan.raw_response,
                **plan.to_dict(),
            }
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass


__all__ = [
    "AnthropicClient",
    "GoalPlan",
    "IntentPlanner",
    "KNOWN_GOAL_STATES",
    "LLMClient",
    "OpenAIClient",
    "available_client",
    "rule_fallback",
]
