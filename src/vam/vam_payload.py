"""VAM recovery payload — the exact structured input passed to System 2.

The assessment (§2.1) demands we show *precisely* what is handed to the VAM when
the recovery cascade escalates. This dataclass is that contract: it carries the
failed skill, why it failed, the screenshot, the Page Affordance Model, a
Cognitive-Map snapshot, the candidate Set-of-Marks affordances, and the prior
attempts — everything the supervisor needs to pick a *mark id* (not a raw
coordinate) without re-deriving the world from scratch.
"""

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
                {"id": a.id, "source": a.source, "label": a.label, "locator": a.locator, "confidence": a.confidence}
                for a in self.candidate_affordances
            ],
            "previous_attempts": [
                {"backend": r.backend_used, "success": r.success, "failure_reason": r.failure_reason}
                for r in self.previous_attempts
            ],
        }
