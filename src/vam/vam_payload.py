"""Structured payload passed to the VAM/System-2 recovery path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.contracts.types import Affordance, ExecutionResult, SkillCall


@dataclass
class VAMRecoveryPayload:
    failed_skill: SkillCall
    failure_reason: str
    screenshot_path: str = ""
    page_affordance_model: dict[str, Any] = field(default_factory=dict)
    cognitive_map_snapshot: dict[str, Any] = field(default_factory=dict)
    candidate_affordances: list[Affordance] = field(default_factory=list)
    previous_attempts: list[ExecutionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed_skill": {"skill_id": self.failed_skill.skill_id, "params": self.failed_skill.params},
            "failure_reason": self.failure_reason,
            "screenshot_path": self.screenshot_path,
            "page_affordance_model": self.page_affordance_model,
            "cognitive_map_snapshot": self.cognitive_map_snapshot,
            "candidate_affordances": [
                {
                    "id": affordance.id,
                    "source": affordance.source,
                    "label": affordance.label,
                    "locator": affordance.locator,
                    "confidence": affordance.confidence,
                }
                for affordance in self.candidate_affordances
            ],
            "previous_attempts": [
                {
                    "backend": result.backend_used,
                    "success": result.success,
                    "failure_reason": result.failure_reason,
                }
                for result in self.previous_attempts
            ],
        }
