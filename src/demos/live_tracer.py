"""Follow a function's real execution, line by line, while it runs.

The console used to print a function's source and leave it there. A viewer saw
the code that was about to run but never saw it running: no cursor moved, no
value appeared, and the panel was indistinguishable from a screenshot of the
file. "Show the code the agent is executing" was answered with static text.

This traces the actual frame. Every line the interpreter executes inside the
target function is reported as it happens, together with the local variables at
that moment, so the panel can move a highlight through the source and show
values changing. Nothing is simulated or replayed: the events come from
``sys.settrace`` on the real call.

Scope is deliberately narrow. Only the target function's own frame is reported -
not the whole call tree - because a viewer needs to follow one story, and
tracing every nested call would bury it. Tracing is removed as soon as the call
returns, whether it returned or raised, so a demo cannot leave the interpreter
slowed down.
"""

from __future__ import annotations

import inspect
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# Values that would swamp the panel are summarised rather than printed. The
# limit is generous enough for the numbers and short strings that carry meaning
# in this loop, and small enough that one line stays one line.
_MAX_VALUE_CHARS = 46
_SKIP_NAMES = {"self", "session", "console", "traj", "scene"}


def format_value(value: Any) -> str:
    """A short, readable rendering of a local variable."""
    try:
        if isinstance(value, (int, float, bool)) or value is None:
            return repr(value)
        if isinstance(value, str):
            text = repr(value)
            return text if len(text) <= _MAX_VALUE_CHARS else text[: _MAX_VALUE_CHARS - 4] + "...'"
        if isinstance(value, (list, tuple, set)):
            return f"{type(value).__name__}[{len(value)}]"
        if isinstance(value, dict):
            return f"dict[{len(value)}]"
        name = type(value).__name__
        for attr in ("mark_id", "goal_state", "source", "name"):
            if hasattr(value, attr):
                return f"{name}({attr}={getattr(value, attr)!r})"
        return name
    except Exception:
        return "<unreadable>"


@dataclass
class TracedSource:
    """The source a viewer reads, and where in the file it starts."""

    lines: list[str] = field(default_factory=list)
    first_lineno: int = 0

    def index_of(self, lineno: int) -> int:
        """Position of a file line within this snippet, or -1 if outside it."""
        index = lineno - self.first_lineno
        return index if 0 <= index < len(self.lines) else -1


def source_of(target: Callable[..., Any]) -> TracedSource:
    try:
        lines, first = inspect.getsourcelines(target)
    except (OSError, TypeError):
        return TracedSource(lines=[f"# source unavailable for {target!r}"], first_lineno=0)
    return TracedSource(lines=[line.rstrip("\n") for line in lines], first_lineno=first)


def interesting_locals(frame_locals: dict[str, Any], *, skip: Iterable[str] = ()) -> dict[str, str]:
    """The variables worth showing: named, small, and not plumbing."""
    skipped = set(_SKIP_NAMES) | set(skip)
    return {
        name: format_value(value)
        for name, value in frame_locals.items()
        if not name.startswith("_") and name not in skipped
    }


def run_traced(
    func: Callable[..., Any],
    on_line: Callable[[int, dict[str, str]], None],
    *args: Any,
    line_delay: float = 0.0,
    **kwargs: Any,
) -> Any:
    """Call ``func(*args)``, reporting each executed line of its own frame.

    The function and its arguments are passed separately rather than wrapped in
    a lambda: the tracer matches on the target's code object, and a wrapper
    would make it follow the wrapper instead.

    ``on_line`` receives the file line number and the locals at that point.
    ``line_delay`` is applied per executed line, not per source line, so a loop
    visibly repeats.
    """
    code = getattr(func, "__code__", None)
    if code is None:  # not a plain Python function; run it untraced
        return func(*args, **kwargs)

    def tracer(frame, event, _arg):  # type: ignore[no-untyped-def]
        if frame.f_code is code and event == "line":
            try:
                on_line(frame.f_lineno, interesting_locals(frame.f_locals))
            except Exception:
                pass  # narration must never break the run it is narrating
            if line_delay:
                time.sleep(line_delay)
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        return func(*args, **kwargs)
    finally:
        # Restored on every path: leaving a tracer installed would slow every
        # later call in the process, including the rest of the demo.
        sys.settrace(previous)


__all__ = [
    "TracedSource",
    "format_value",
    "interesting_locals",
    "run_traced",
    "source_of",
]
