"""Ask the live page specific questions, so a diagnosis rests on measurements.

The previous diagnosis compared a label and a pair of coordinates. That is
enough to tell "it moved" from "it is gone", and nothing more: it cannot see a
consent banner sitting on top of the button, a control that is present but
disabled, or a click that landed on a different element entirely. Those are the
failures that actually happen in production, and each of them needs a specific
question put to the page.

Each probe here answers one question with one measurement:

  hit_test          what element is really at these coordinates right now
  interactability   is the target disabled, hidden, or zero-sized
  occlusion         is something covering the target, and what is it
  text_snapshot     the visible text of a region, for before/after comparison

Every probe returns what it observed rather than a verdict, so the reasoning
that combines them stays in one place and can be read. A probe that cannot run
says so instead of returning a default that would look like a finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class _EvaluatingSession(Protocol):
    def evaluate(self, expression: str, arg: Any | None = ...) -> Any: ...


@dataclass
class HitTest:
    """What is actually at a point, compared with what was aimed at."""

    hit_tag: str = ""
    hit_text: str = ""
    hit_id: str = ""
    hit_classes: str = ""
    is_target: bool = False
    ok: bool = False  # False when the probe could not run

    def describe(self) -> str:
        if not self.ok:
            return "hit test could not run"
        if self.is_target:
            return "the element at the click point is the one that was aimed at"
        label = self.hit_text.strip()[:40] or self.hit_id or self.hit_classes or self.hit_tag
        return f"the click point is covered by <{self.hit_tag}> {label!r}"


@dataclass
class Interactability:
    """Whether the target could accept an action at all."""

    exists: bool = False
    disabled: bool = False
    aria_disabled: bool = False
    visible: bool = False
    width: int = 0
    height: int = 0
    pointer_events: str = ""
    ok: bool = False

    @property
    def actionable(self) -> bool:
        return self.exists and self.visible and not self.disabled and not self.aria_disabled

    def describe(self) -> str:
        if not self.ok:
            return "interactability probe could not run"
        if not self.exists:
            return "the target is no longer in the document"
        reasons = []
        if self.disabled:
            reasons.append("disabled attribute is set")
        if self.aria_disabled:
            reasons.append("aria-disabled is true")
        if not self.visible:
            reasons.append("not rendered (zero size or hidden)")
        if self.pointer_events == "none":
            reasons.append("pointer-events is none")
        return "the target accepts input" if not reasons else "the target cannot accept input: " + ", ".join(reasons)


@dataclass
class Occlusion:
    """What, if anything, sits on top of the target."""

    covered: bool = False
    missing: bool = False  # the target is not in the document at all
    coverer_tag: str = ""
    coverer_text: str = ""
    coverer_z: str = ""
    # Where the covering element sits. Recovery needs this: to deal with an
    # obstruction it has to find the obstruction's own controls, and the
    # rectangle is the only thing that locates them without naming the fault.
    coverer_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    ok: bool = False

    def describe(self) -> str:
        if not self.ok:
            return "occlusion probe could not run"
        if self.missing:
            return "the target is not in the document, so nothing can be covering it"
        if not self.covered:
            return "nothing is covering the target"
        label = self.coverer_text.strip()[:48] or self.coverer_tag
        return f"the target is covered by <{self.coverer_tag}> {label!r} (z-index {self.coverer_z or 'auto'})"


@dataclass
class Observation:
    """Everything the probes found, in one place."""

    hit: HitTest = field(default_factory=HitTest)
    interact: Interactability = field(default_factory=Interactability)
    occlusion: Occlusion = field(default_factory=Occlusion)
    text_before: str = ""
    text_after: str = ""

    @property
    def region_changed(self) -> bool:
        return self.text_before != self.text_after

    def evidence(self) -> list[str]:
        """One readable line per measurement, for the panel and the record."""
        return [
            self.hit.describe(),
            self.interact.describe(),
            self.occlusion.describe(),
            f"the region the goal names {'changed' if self.region_changed else 'did not change'} after acting",
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit": {"tag": self.hit.hit_tag, "text": self.hit.hit_text[:60], "is_target": self.hit.is_target},
            "interactable": self.interact.actionable,
            "disabled": self.interact.disabled or self.interact.aria_disabled,
            "covered_by": self.occlusion.coverer_tag if self.occlusion.covered else "",
            "region_changed": self.region_changed,
        }


_HIT_JS = """
(a)=>{
  const el = document.elementFromPoint(a.x, a.y);
  if (!el) return null;
  // Our own narration overlays are not part of the page under test.
  const real = el.closest('[id^="__cua"]') ? null : el;
  const t = real || el;
  const aimed = a.selector ? t.closest(a.selector) !== null : false;
  return {tag: t.tagName.toLowerCase(), text: (t.innerText||'').slice(0,80),
          id: t.id||'', classes: (t.className||'').toString().slice(0,60),
          is_target: aimed};
}
"""

_INTERACT_JS = """
(sel)=>{
  const el = document.querySelector(sel);
  if (!el) return {exists:false};
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return {exists:true,
          disabled: !!el.disabled || el.hasAttribute('disabled'),
          aria_disabled: el.getAttribute('aria-disabled') === 'true',
          visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none',
          width: Math.round(r.width), height: Math.round(r.height),
          pointer_events: s.pointerEvents};
}
"""

_OCCLUSION_JS = """
(sel)=>{
  const el = document.querySelector(sel);
  if (!el) return {covered:false, missing:true};
  const r = el.getBoundingClientRect();
  const x = r.left + r.width/2, y = r.top + r.height/2;
  const top = document.elementFromPoint(x, y);
  if (!top) return {covered:false};
  if (top.closest('[id^="__cua"]')) return {covered:false};   // our own overlay
  if (el.contains(top) || top === el) return {covered:false};
  const tr = top.getBoundingClientRect();
  return {covered:true, tag: top.tagName.toLowerCase(),
          text: (top.innerText||'').slice(0,80),
          z: getComputedStyle(top).zIndex,
          rect: [Math.round(tr.left), Math.round(tr.top),
                 Math.round(tr.width), Math.round(tr.height)]};
}
"""


def hit_test(session: _EvaluatingSession, x: int, y: int, selector: str = "") -> HitTest:
    """What is at (x, y) right now, and is it what we aimed at."""
    try:
        raw = session.evaluate(_HIT_JS, {"x": int(x), "y": int(y), "selector": selector})
    except Exception:
        return HitTest()
    if not isinstance(raw, dict):
        return HitTest()
    return HitTest(
        hit_tag=str(raw.get("tag", "")),
        hit_text=str(raw.get("text", "")),
        hit_id=str(raw.get("id", "")),
        hit_classes=str(raw.get("classes", "")),
        is_target=bool(raw.get("is_target")),
        ok=True,
    )


def interactability(session: _EvaluatingSession, selector: str) -> Interactability:
    """Whether the target could accept an action at all."""
    try:
        raw = session.evaluate(_INTERACT_JS, selector)
    except Exception:
        return Interactability()
    if not isinstance(raw, dict):
        return Interactability()
    return Interactability(
        exists=bool(raw.get("exists")),
        disabled=bool(raw.get("disabled")),
        aria_disabled=bool(raw.get("aria_disabled")),
        visible=bool(raw.get("visible")),
        width=int(raw.get("width", 0) or 0),
        height=int(raw.get("height", 0) or 0),
        pointer_events=str(raw.get("pointer_events", "")),
        ok=True,
    )


def occlusion(session: _EvaluatingSession, selector: str) -> Occlusion:
    """What, if anything, sits on top of the target's centre."""
    try:
        raw = session.evaluate(_OCCLUSION_JS, selector)
    except Exception:
        return Occlusion()
    if not isinstance(raw, dict):
        return Occlusion()
    rect = raw.get("rect") or [0, 0, 0, 0]
    return Occlusion(
        covered=bool(raw.get("covered")),
        missing=bool(raw.get("missing")),
        coverer_tag=str(raw.get("tag", "")),
        coverer_text=str(raw.get("text", "")),
        coverer_z=str(raw.get("z", "")),
        coverer_rect=(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])),
        ok=True,
    )


def text_snapshot(session: _EvaluatingSession, selector: str) -> str:
    """Visible text of a region, for comparing before against after."""
    try:
        return str(
            session.evaluate("(s)=>{const e=document.querySelector(s);return e?e.innerText:'';}", selector) or ""
        )
    except Exception:
        return ""


__all__ = [
    "HitTest",
    "Interactability",
    "Observation",
    "Occlusion",
    "hit_test",
    "interactability",
    "occlusion",
    "text_snapshot",
]
