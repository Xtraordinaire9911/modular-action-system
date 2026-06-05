"""Tier 2 recovery: reroute to another backend."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.types import SkillTuple


@dataclass
class RerouteDecision:
    should_reroute: bool
    selected_backend: str
    reason: str


class ReroutePolicy:
    def decide(
        self,
        skill_tuple: SkillTuple,
        failed_backend: str,
        available_backends: list[str],
        tried_backends: list[str] | None = None,
    ) -> RerouteDecision:
        tried = set(tried_backends or [])
        candidates = [
            backend
            for backend in skill_tuple.allowed_backends
            if backend in available_backends and backend != failed_backend and backend not in tried
        ]
        if not candidates:
            return RerouteDecision(False, "", "no alternate backend available")

        for backend in skill_tuple.preferred_backends:
            if backend in candidates:
                return RerouteDecision(True, backend, f"rerouted to preferred backend {backend}")

        return RerouteDecision(True, candidates[0], f"rerouted away from {failed_backend}")
