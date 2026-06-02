"""VAM recovery payload dataclass.

The recovery manager constructs one of these when escalating to System 2.
It is the complete context the VAM needs to decide an Epistemic Probing
Action or a recovery grounding without seeing raw logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.contracts.types import Affordance, ExecutionResult, SkillCall


@dataclass
class VAMRecoveryPayload:
    failed_skill: SkillCall
    failure_reason: str
    screenshot_path: str
    page_affordance_model: dict[str, Any]
    cognitive_map_snapshot: dict[str, Any]
    candidate_affordances: list[Affordance] = field(default_factory=list)
    previous_attempts: list[ExecutionResult] = field(default_factory=list)
