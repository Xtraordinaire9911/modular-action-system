"""Public entrypoint for the unified forward/recovery Agent planner.

The implementation remains in ``model_recovery_planner`` during the compatibility
migration so existing evaluation imports continue to work. New composition roots
should import from this module.
"""

from src.planner.model_recovery_planner import (
    AgentChoice,
    AgentPlanner,
    LLMClient,
    PlanningMode,
    candidate_lines,
)

__all__ = ["AgentChoice", "AgentPlanner", "LLMClient", "PlanningMode", "candidate_lines"]
