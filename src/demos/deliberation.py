"""Make the agent's choice inspectable: candidates, scores, and why each lost.

The planning step used to be a single substring match that returned a winner and
nothing else. A viewer could see the source of the function but not the decision:
what else was on the page, how close the runner-up was, or why the winner won.
That is not an opaque display of reasoning -- there was no record of reasoning to
display.

This produces one. Scoring is deterministic and every term is named, so the
decision can be audited rather than trusted. It is explicitly **not** a language
model: no prompt, no sampling, no hidden state. Calling deterministic scoring
"reasoning" would be the same overstatement as calling context isolation
Picture-in-Picture.

What it does buy: the choice is derived from the current observation, the
alternatives are on the record, and a wrong choice can be explained after the
fact instead of guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# Weights are separated so the panel can show which term decided a close call.
W_LABEL = 60.0  # goal words present in the element's label
W_ACTION = 20.0  # the element's action type suits the goal's verb
W_CONFIDENCE = 15.0  # how reliable the perceived locator is
W_SIZE = 5.0  # a plausible click target rather than a sliver

_VERB_TO_ACTION = {
    "add": "click",
    "click": "click",
    "upvote": "click",
    "archive": "click",
    "open": "click",
    "submit": "click",
    "send": "click",
    "checkout": "click",
    "type": "type",
    "enter": "type",
    "fill": "type",
    "search": "type",
}


@dataclass
class Candidate:
    """One option the agent considered, with the score it earned and why."""

    mark_id: str
    label: str
    action: str
    score: float = 0.0
    terms: dict[str, float] = field(default_factory=dict)
    verdict: str = ""

    def line(self) -> str:
        return f"{self.mark_id}  {self.score:5.1f}  {self.label[:34]:<34} {self.verdict}"


@dataclass
class Decision:
    """The full deliberation: what was considered, what won, and by how much."""

    goal: str
    candidates: list[Candidate] = field(default_factory=list)
    chosen: Candidate | None = None
    margin: float = 0.0
    # The mark object behind the winner, so a caller can act on it without
    # having to look it up again and risk picking a different element.
    chosen_mark: Any | None = None

    @property
    def considered(self) -> int:
        return len(self.candidates)

    @property
    def runner_up(self) -> Candidate | None:
        return self.candidates[1] if len(self.candidates) > 1 else None

    def explain(self, top: int = 5) -> str:
        """A block a viewer can read: ranked options and the deciding term."""
        if not self.candidates:
            return f"goal: {self.goal}\n\nno interactive element scored above zero."
        lines = [f"goal: {self.goal}", f"{self.considered} candidates considered, ranked:", ""]
        lines += [("> " if c is self.chosen else "  ") + c.line() for c in self.candidates[:top]]
        if len(self.candidates) > top:
            lines.append(f"  ... {len(self.candidates) - top} more scored lower")
        if self.chosen is not None:
            lines += ["", "why the winner won:"]
            for name, value in sorted(self.chosen.terms.items(), key=lambda kv: -kv[1]):
                if value:
                    lines.append(f"  {name:<12} +{value:5.1f}")
            if self.runner_up is not None:
                lines.append(f"  margin over runner-up: {self.margin:.1f}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "considered": self.considered,
            "chosen": self.chosen.mark_id if self.chosen else None,
            "margin": round(self.margin, 2),
            "ranking": [
                {
                    "mark_id": c.mark_id,
                    "label": c.label,
                    "score": round(c.score, 2),
                    "terms": {k: round(v, 2) for k, v in c.terms.items()},
                    "verdict": c.verdict,
                }
                for c in self.candidates
            ],
        }


def _label_term(goal: str, label: str) -> float:
    """Share of the goal's words present in the label, ignoring filler."""
    stop = {"the", "a", "an", "to", "in", "on", "and", "it", "this", "that", "post"}
    words = [w for w in goal.lower().replace(",", " ").split() if w not in stop and len(w) > 2]
    if not words:
        return 0.0
    low = label.lower()
    return W_LABEL * (sum(1 for w in words if w in low) / len(words))


def _action_term(goal: str, action: str) -> float:
    """Reward an element whose action type matches the goal's verb."""
    for verb, wanted in _VERB_TO_ACTION.items():
        if verb in goal.lower():
            return W_ACTION if action == wanted else 0.0
    return 0.0


def _size_term(mark: Any) -> float:
    """A believable target: neither a sliver nor the whole viewport."""
    try:
        box = mark.bbox
        width, height = box.w, box.h
    except AttributeError:
        return 0.0
    if width < 8 or height < 8:
        return 0.0  # too small to be a real control
    if width > 1000 and height > 600:
        return 0.0  # a container, not a control
    return W_SIZE


def deliberate(marks: Iterable[Any], goal: str) -> Decision:
    """Score every perceived mark against the goal and record the ranking."""
    decision = Decision(goal=goal)
    by_id: dict[str, Any] = {}
    for mark in marks:
        by_id[mark.mark_id] = mark
        action = str(getattr(mark, "extra", {}).get("action", "click"))
        terms = {
            "label": _label_term(goal, mark.label),
            "action": _action_term(goal, action),
            "confidence": W_CONFIDENCE * float(getattr(mark, "confidence", 0.0) or 0.0),
            "size": _size_term(mark),
        }
        candidate = Candidate(
            mark_id=mark.mark_id,
            label=mark.label,
            action=action,
            score=sum(terms.values()),
            terms=terms,
        )
        decision.candidates.append(candidate)

    decision.candidates.sort(key=lambda c: -c.score)
    for candidate in decision.candidates:
        if candidate.terms["label"] <= 0:
            candidate.verdict = "rejected: goal words absent from label"
        elif candidate.terms["action"] <= 0 and _action_term(goal, "click") > 0:
            candidate.verdict = "rejected: wrong action type for this goal"
        else:
            candidate.verdict = "eligible"

    eligible = [c for c in decision.candidates if c.verdict == "eligible"]
    if eligible:
        decision.chosen = eligible[0]
        decision.chosen.verdict = "CHOSEN"
        decision.chosen_mark = by_id.get(decision.chosen.mark_id)
        others = [c for c in decision.candidates if c is not decision.chosen]
        decision.margin = decision.chosen.score - (others[0].score if others else 0.0)
    return decision


__all__ = ["Candidate", "Decision", "deliberate"]
