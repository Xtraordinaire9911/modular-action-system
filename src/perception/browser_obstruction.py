"""Observation-driven browser obstruction and remediation discovery.

This module measures the attempted target in the live page. It does not receive
the injected fault type and does not know fixture selectors. Recovery controls
are emitted only when they are inside the measured blocker and pass bounded
safety checks.
"""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.contracts.types import Affordance, ObservedAssertion


class BrowserEvaluator(Protocol):
    def evaluate(self, expression: str, arg: Any | None = None) -> Any: ...


@dataclass(frozen=True)
class ObstructionControl:
    selector: str
    label: str
    action: str = "click"
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrowserObstructionObservation:
    target_exists: bool
    blocked: bool
    blocker: dict[str, Any] = field(default_factory=dict)
    controls: list[ObstructionControl] = field(default_factory=list)

    def assertion(self, *, timestamp_ms: int = 0) -> ObservedAssertion:
        present: bool | None = self.blocked if self.target_exists else None
        return ObservedAssertion(
            entity_id="interaction_obstruction",
            attribute="present",
            value=present,
            source="dom",
            confidence=0.98 if self.target_exists else 0.0,
            timestamp_ms=timestamp_ms,
            provenance={
                "adapter": "browser_obstruction_probe",
                "target_exists": self.target_exists,
                "blocker": self.blocker,
            },
        )

    def recovery_affordances(self, *, target_affordance_id: str) -> list[Affordance]:
        affordances: list[Affordance] = []
        for control in self.controls:
            digest = hashlib.sha256(control.selector.encode("utf-8")).hexdigest()[:12]
            affordances.append(
                Affordance(
                    id=f"dom_recovery_{digest}",
                    source="DOM",
                    type="button",
                    label=control.label,
                    action=control.action,
                    locator={
                        "selector": control.selector,
                        "entity_id": "interaction_obstruction",
                        "recovery_role": "clear_obstruction",
                        "remediates": target_affordance_id,
                        "recovery_postcondition": "interaction_obstruction.present == false",
                        "recovery_safe": True,
                        "idempotent": True,
                        "irreversible": False,
                        "stable_key": f"obstruction-control:{digest}",
                        "recovery_evidence": control.evidence,
                    },
                    confidence=control.confidence,
                    state={"enabled": True, "visible": True},
                    safety_level="low",
                )
            )
        return affordances


_SCAN_JS = r"""
({selector}) => {
  const target = document.querySelector(selector);
  if (!target) return {targetExists: false, blocked: false, blocker: {}, controls: []};
  const rect = target.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  const top = document.elementFromPoint(x, y);
  if (!top || top === target || target.contains(top)) {
    return {targetExists: true, blocked: false, blocker: {}, controls: []};
  }

  const containsPoint = (node) => {
    const r = node.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  };
  let blocker = null;
  let node = top;
  while (node && node !== document.documentElement) {
    const style = getComputedStyle(node);
    const modal = node.matches('dialog,[role="dialog"],[aria-modal="true"]');
    const layered = ['fixed', 'sticky', 'absolute'].includes(style.position);
    if ((modal || layered) && containsPoint(node) && !node.contains(target)) blocker = node;
    node = node.parentElement;
  }
  blocker = blocker || top;

  const uniqueSelector = (element) => {
    if (element.id) return `#${CSS.escape(element.id)}`;
    const testid = element.getAttribute('data-testid');
    if (testid) return `[data-testid="${CSS.escape(testid)}"]`;
    const aria = element.getAttribute('aria-label');
    if (aria) {
      const escaped = aria.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
      const candidate = `${element.tagName.toLowerCase()}[aria-label="${escaped}"]`;
      if (document.querySelectorAll(candidate).length === 1) return candidate;
    }
    const path = [];
    let current = element;
    while (current && current !== document.body) {
      let part = current.tagName.toLowerCase();
      const siblings = current.parentElement
        ? Array.from(current.parentElement.children).filter((s) => s.tagName === current.tagName)
        : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      path.unshift(part);
      const candidate = path.join(' > ');
      if (document.querySelectorAll(candidate).length === 1) return candidate;
      current = current.parentElement;
    }
    return path.join(' > ');
  };

  const controls = Array.from(
    blocker.querySelectorAll('button,a[href],input[type="button"],input[type="submit"],[role="button"]')
  ).map((control) => {
    const r = control.getBoundingClientRect();
    const style = getComputedStyle(control);
    const form = control.closest('form');
    return {
      selector: uniqueSelector(control),
      label: (control.getAttribute('aria-label') || control.innerText || control.value || '').trim(),
      visible: r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none',
      enabled: !control.disabled && control.getAttribute('aria-disabled') !== 'true',
      methodDialog: !!form && form.getAttribute('method') === 'dialog',
      dismissAttribute: !!(
        control.getAttribute('data-dismiss') ||
        control.getAttribute('data-bs-dismiss') ||
        control.getAttribute('popovertarget')
      ),
      type: (control.getAttribute('type') || '').toLowerCase()
    };
  }).filter((control) => control.selector && control.visible && control.enabled);

  const blockerRect = blocker.getBoundingClientRect();
  return {
    targetExists: true,
    blocked: true,
    blocker: {
      tag: blocker.tagName.toLowerCase(),
      role: blocker.getAttribute('role') || '',
      ariaModal: blocker.getAttribute('aria-modal') || '',
      rect: [blockerRect.left, blockerRect.top, blockerRect.width, blockerRect.height],
      zIndex: getComputedStyle(blocker).zIndex
    },
    controls
  };
}
"""

_DANGEROUS_CONTROL_TERMS = frozenset(
    {"buy", "confirm order", "delete", "pay", "place order", "purchase", "send", "subscribe", "transfer"}
)


async def observe_browser_obstruction(
    session: BrowserEvaluator,
    *,
    target_selector: str,
) -> BrowserObstructionObservation:
    raw = session.evaluate(_SCAN_JS, {"selector": target_selector})
    if inspect.isawaitable(raw):
        raw = await raw
    if not isinstance(raw, dict):
        return BrowserObstructionObservation(target_exists=False, blocked=False)
    target_exists = bool(raw.get("targetExists"))
    blocked = bool(raw.get("blocked"))
    blocker = dict(raw.get("blocker") or {})
    rows = [row for row in raw.get("controls", []) if isinstance(row, dict)]
    controls = _safe_controls(rows) if target_exists and blocked else []
    return BrowserObstructionObservation(
        target_exists=target_exists,
        blocked=blocked,
        blocker=blocker,
        controls=controls,
    )


def _safe_controls(rows: list[dict[str, Any]]) -> list[ObstructionControl]:
    scored: list[ObstructionControl] = []
    single_candidate = len(rows) == 1
    for row in rows:
        label = str(row.get("label") or "").strip()
        normalized = _normalize_label(label)
        if any(term in normalized for term in _DANGEROUS_CONTROL_TERMS):
            continue
        structural_signal = bool(row.get("methodDialog") or row.get("dismissAttribute"))
        if not structural_signal:
            continue
        evidence = {
            "measured_inside_blocker": True,
            "single_enabled_control": single_candidate,
            "structural_dismiss_signal": structural_signal,
            "semantic_dismiss_signal": False,
            "blocker_control_count": len(rows),
        }
        confidence = 0.98
        scored.append(
            ObstructionControl(
                selector=str(row.get("selector") or ""),
                label=label,
                confidence=confidence,
                evidence=evidence,
            )
        )
    return sorted(scored, key=lambda control: (-control.confidence, control.selector))


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", label.casefold())).strip()


__all__ = [
    "BrowserObstructionObservation",
    "ObstructionControl",
    "observe_browser_obstruction",
]
