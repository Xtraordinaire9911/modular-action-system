"""Demo registry: one place that knows every demo and whether it can run."""

from src.demos.registry import (
    DEMOS,
    Demo,
    DemoStatus,
    build_argv,
    capability_report,
    check_capability,
    find,
    runnable,
    status_of,
)

__all__ = [
    "DEMOS",
    "Demo",
    "DemoStatus",
    "build_argv",
    "capability_report",
    "check_capability",
    "find",
    "runnable",
    "status_of",
]
