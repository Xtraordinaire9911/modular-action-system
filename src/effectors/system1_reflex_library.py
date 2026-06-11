"""System-1 reflex library (advisor §7.2, June-5 ask).

Kahneman's dual-process split, execution side: System 1 is fast, deterministic,
and cheap — cached DOM selectors, cached Set-of-Marks templates, and direct
TD-derived WoT bindings — with a ``<50 ms`` budget for the cached fast path.
The heavy System-2 VAM supervisor is woken **only** under the strict trigger
conditions in :meth:`needs_system2` (confidence < τ=0.9, precondition or
postcondition failure, selector failure, or backend unavailable).

This class caches the best-known grounding per ``(skill_id, backend)`` so a
repeated skill never re-grounds, and reports whether a run met the latency
budget so the evaluator can show System-1 amortises latency vs a VAM-only path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from src.contracts.types import Affordance, ExecutionResult, SkillCall

TAU = 0.9  # confidence threshold below which System 1 must not act
SYSTEM1_BUDGET_MS = 50.0
_DETERMINISTIC = frozenset(["DOM", "WOT"])


@dataclass
class ReflexEntry:
    affordance: Affordance
    hits: int = 0
    avg_latency_ms: float = 0.0

    def record(self, latency_ms: float) -> None:
        self.hits += 1
        self.avg_latency_ms += (latency_ms - self.avg_latency_ms) / self.hits


@dataclass
class ReflexOutcome:
    result: ExecutionResult
    within_budget: bool
    fast_path: bool
    escalate: bool
    escalation_reason: str | None = None


class System1ReflexLibrary:
    """Grounding cache + fast-path executor + System-2 escalation gate."""

    def __init__(self, *, tau: float = TAU, budget_ms: float = SYSTEM1_BUDGET_MS) -> None:
        self._tau = tau
        self._budget_ms = budget_ms
        self._cache: dict[tuple[str, str], ReflexEntry] = {}

    # ── grounding cache ──────────────────────────────────────────────────────
    def remember(self, skill_id: str, affordance: Affordance) -> None:
        """Cache a successful grounding so the next run skips re-perception."""
        self._cache[(skill_id, affordance.source)] = ReflexEntry(affordance=affordance)

    def recall(self, skill_id: str) -> Affordance | None:
        """Return the most-reliable cached grounding for a skill, if any."""
        candidates = [e for (sid, _), e in self._cache.items() if sid == skill_id]
        if not candidates:
            return None
        best = max(candidates, key=lambda e: e.affordance.confidence)
        return best.affordance

    # ── System-1 / System-2 gate ─────────────────────────────────────────────
    def is_reflex(self, affordance: Affordance) -> bool:
        """A reflex is a deterministic backend grounded at high confidence."""
        return affordance.source in _DETERMINISTIC and affordance.confidence >= self._tau

    def needs_system2(
        self,
        *,
        affordance: Affordance | None = None,
        confidence: float | None = None,
        precondition_passed: bool = True,
        postcondition_passed: bool = True,
        selector_failed: bool = False,
        backend_available: bool = True,
    ) -> tuple[bool, str | None]:
        """Apply the strict escalation rules. Returns (escalate, reason)."""
        conf = confidence if confidence is not None else (affordance.confidence if affordance else 1.0)
        if not precondition_passed:
            return True, "precondition_failed"
        if not backend_available:
            return True, "backend_unavailable"
        if selector_failed:
            return True, "selector_failed"
        if conf < self._tau:
            return True, "low_confidence"
        if not postcondition_passed:
            return True, "postcondition_failed"
        return False, None

    # ── timed execution ──────────────────────────────────────────────────────
    def run(
        self,
        skill: SkillCall,
        affordance: Affordance,
        executor: Callable[..., ExecutionResult],
        *,
        value: Any | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> ReflexOutcome:
        """Execute via System 1, timing the fast path and updating the cache."""
        pre_escalate, reason = self.needs_system2(affordance=affordance)
        if pre_escalate:
            empty = ExecutionResult(
                skill_id=skill.skill_id,
                backend_used=affordance.source.lower(),
                success=False,
                latency_ms=0.0,
                confidence=affordance.confidence,
                failure_reason=f"system1_declined:{reason}",
            )
            return ReflexOutcome(empty, within_budget=True, fast_path=False, escalate=True, escalation_reason=reason)

        start = clock()
        result = executor(affordance, value=value, skill_id=skill.skill_id)
        latency = (clock() - start) * 1000.0
        entry = self._cache.setdefault((skill.skill_id, affordance.source), ReflexEntry(affordance=affordance))
        entry.record(latency)

        escalate, esc_reason = self.needs_system2(
            affordance=affordance,
            selector_failed=not result.success and affordance.source == "DOM",
            backend_available=not (not result.success and affordance.source == "WOT"),
        )
        return ReflexOutcome(
            result=result,
            within_budget=(result.latency_ms <= self._budget_ms),
            fast_path=self.is_reflex(affordance),
            escalate=escalate and not result.success,
            escalation_reason=esc_reason if not result.success else None,
        )

    def stats(self) -> dict[str, Any]:
        entries = list(self._cache.values())
        mean_latency = round(sum(e.avg_latency_ms for e in entries) / len(entries), 3) if entries else 0.0
        return {
            "entries": len(entries),
            "total_hits": sum(e.hits for e in entries),
            "mean_latency_ms": mean_latency,
        }
