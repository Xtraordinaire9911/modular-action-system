"""Task-aware driver for MiniWoB++ tasks.

Pure DOM-semantic transduction cannot see MiniWoB's START gate (it is a
``<div id="sync-task-cover">`` with no role) or read the episode reward, so a
generic reflex flounders. This adapter encapsulates the small amount of
MiniWoB-specific protocol knowledge (verified against the upstream core.js):

  * START an episode by clicking ``#sync-task-cover``
  * read the instruction from ``#query``  ->  e.g.  Click on the "okay" button.
  * click the matching control inside ``#area``
  * read the outcome from the JS globals ``WOB_REWARD_GLOBAL`` / ``WOB_DONE_GLOBAL``

The session is injected (BrowserSession or a fake), keeping this unit-testable.
The generic perception/execution stack stays generic; env-specific glue lives
here, which is exactly what an environment adapter is for.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

_QUOTED = re.compile(r'"([^"]+)"')


class MiniwobSession(Protocol):
    def click(self, selector: str) -> Any: ...
    def text_content(self, selector: str) -> str | None: ...
    def evaluate(self, expression: str, arg: Any | None = ...) -> Any: ...


def parse_target(utterance: str) -> str | None:
    """Extract the quoted target from an utterance like 'Click on the "X" button.'"""
    match = _QUOTED.search(utterance or "")
    return match.group(1) if match else None


class MiniwobAdapter:
    """Drive one MiniWoB++ ``click-button`` style task to completion."""

    def __init__(self, session: MiniwobSession) -> None:
        self._session = session

    def start(self) -> None:
        # Clicking the START cover begins the episode and renders #area + #query.
        self._session.click("#sync-task-cover")

    def utterance(self) -> str:
        return self._session.text_content("#query") or ""

    def reward(self) -> float:
        try:
            return float(self._session.evaluate("WOB_REWARD_GLOBAL") or 0.0)
        except Exception:
            return 0.0

    def done(self) -> bool:
        return bool(self._session.evaluate("WOB_DONE_GLOBAL"))

    def run_click_button(self) -> dict[str, Any]:
        """START -> read instruction -> click the matching button -> read reward."""
        self.start()
        utterance = self.utterance()
        target = parse_target(utterance)
        clicked = False
        if target is not None:
            # :text-is matches the button whose exact text equals the target word.
            self._session.click(f'#area button:text-is("{target}")')
            clicked = True
        reward = self.reward() if clicked else 0.0
        return {
            "utterance": utterance,
            "target": target,
            "clicked": clicked,
            "reward": reward,
            "success": reward > 0.0,
        }
