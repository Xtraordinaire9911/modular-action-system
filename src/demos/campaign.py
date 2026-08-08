"""Aggregate repeated demo runs into the metrics the project already defines.

A single pass through the scenes shows that the loop works. It cannot show how
often it works, and with one episode per condition the project's own recovery
metrics are undefined: Recovery Tier Accuracy needs a distribution of failures
to be accuracy of anything.

This turns repeated runs into those numbers. The ground truth it scores against
is the fault that was injected, which the injector obviously knows and the
diagnosis deliberately does not - that asymmetry is what makes the score
meaningful rather than circular.

Reported metrics follow the definitions in evaluation/metrics_aggregator.py so
they can be read next to the rest of the project's evaluation:

  TSR   task success rate, counting a goal met after recovery as met
  RTR   recovery trigger rate: episodes where a failure was detected
  RSR   recovery success rate: of those, how many ended with the goal met
  RTA   recovery tier accuracy: how often the tier chosen was the right one
  DA    diagnosis accuracy: how often the cause identified was the true one

An escalation is scored as correct when escalating was the right answer. It is
not counted as a task success, because handing over is an honest outcome rather
than a solved goal.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EpisodeResult:
    """One scene, run once."""

    scene: str
    fault: str  # the fault injected, "" for a clean run
    expected_cause: str  # what a correct diagnosis should conclude
    expected_tier: int  # the tier that fault warrants, 0 when none
    diagnosed_cause: str = ""
    chosen_tier: int = 0
    failure_detected: bool = False
    goal_met: bool = False
    escalated: bool = False

    @property
    def diagnosis_correct(self) -> bool:
        return bool(self.expected_cause) and self.diagnosed_cause == self.expected_cause

    @property
    def tier_correct(self) -> bool:
        return bool(self.expected_tier) and self.chosen_tier == self.expected_tier

    @property
    def handled_well(self) -> bool:
        """Goal met, or escalated when escalation was the right call."""
        return self.goal_met or (self.escalated and self.expected_tier == 4)


@dataclass
class Campaign:
    """Repeated episodes, and what they add up to."""

    episodes: list[EpisodeResult] = field(default_factory=list)

    def add(self, episode: EpisodeResult) -> None:
        self.episodes.append(episode)

    @property
    def repetitions(self) -> int:
        scenes = {e.scene for e in self.episodes}
        return len(self.episodes) // len(scenes) if scenes else 0

    def metrics(self) -> dict[str, Any]:
        total = len(self.episodes)
        if not total:
            return {"episodes": 0}

        faulted = [e for e in self.episodes if e.fault]
        detected = [e for e in self.episodes if e.failure_detected]
        diagnosed = [e for e in detected if e.expected_cause]
        tiered = [e for e in detected if e.expected_tier]

        return {
            "episodes": total,
            "scenes": len({e.scene for e in self.episodes}),
            "repetitions": self.repetitions,
            "faulted_episodes": len(faulted),
            "TSR": _rate(sum(1 for e in self.episodes if e.handled_well), total),
            "RTR": _rate(len(detected), total),
            "RSR": _rate(sum(1 for e in detected if e.handled_well), len(detected)),
            "RTA": _rate(sum(1 for e in tiered if e.tier_correct), len(tiered)),
            "DA": _rate(sum(1 for e in diagnosed if e.diagnosis_correct), len(diagnosed)),
            "escalations": sum(1 for e in self.episodes if e.escalated),
        }

    def by_fault(self) -> dict[str, dict[str, Any]]:
        """Per fault kind, so a weak spot is visible rather than averaged away."""
        grouped: dict[str, list[EpisodeResult]] = defaultdict(list)
        for episode in self.episodes:
            grouped[episode.fault or "none"].append(episode)

        summary: dict[str, dict[str, Any]] = {}
        for fault, group in sorted(grouped.items()):
            scored = [e for e in group if e.expected_cause]
            summary[fault] = {
                "episodes": len(group),
                "handled": sum(1 for e in group if e.handled_well),
                "DA": _rate(sum(1 for e in scored if e.diagnosis_correct), len(scored)),
                "RTA": _rate(sum(1 for e in scored if e.tier_correct), len(scored)),
                "tiers_used": sorted({e.chosen_tier for e in group if e.chosen_tier}),
            }
        return summary

    def report(self) -> str:
        metrics = self.metrics()
        if not metrics.get("episodes"):
            return "no episodes recorded"

        lines = [
            f"{metrics['episodes']} episodes  "
            f"({metrics['scenes']} scenes x {metrics['repetitions']} repetitions, "
            f"{metrics['faulted_episodes']} with an injected fault)",
            "",
            f"  TSR  task success rate            {metrics['TSR']:6.1%}",
            f"  RTR  recovery trigger rate        {metrics['RTR']:6.1%}",
            f"  RSR  recovery success rate        {metrics['RSR']:6.1%}",
            f"  RTA  recovery tier accuracy       {metrics['RTA']:6.1%}",
            f"  DA   diagnosis accuracy           {metrics['DA']:6.1%}",
            f"       escalations                  {metrics['escalations']}",
            "",
            f"  {'fault':<12} {'eps':>4} {'handled':>8} {'DA':>7} {'RTA':>7}  tiers",
            f"  {'-' * 54}",
        ]
        for fault, row in self.by_fault().items():
            tiers = ",".join(str(t) for t in row["tiers_used"]) or "-"
            lines.append(
                f"  {fault:<12} {row['episodes']:>4} {row['handled']:>8} "
                f"{row['DA']:>6.0%} {row['RTA']:>6.0%}  {tiers}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics(),
            "by_fault": self.by_fault(),
            "episodes": [
                {
                    "scene": e.scene,
                    "fault": e.fault,
                    "expected_cause": e.expected_cause,
                    "diagnosed_cause": e.diagnosed_cause,
                    "expected_tier": e.expected_tier,
                    "chosen_tier": e.chosen_tier,
                    "failure_detected": e.failure_detected,
                    "goal_met": e.goal_met,
                    "escalated": e.escalated,
                }
                for e in self.episodes
            ],
        }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


__all__ = ["Campaign", "EpisodeResult"]
