"""DOM Transducer — implements the DOM Transduction Pattern (advisor §5.1).

Answers the advisor's direct question *"How will you process the DOM?"*:

    raw HTML  ──▶  strip script/style/tracking  ──▶  keep interactable nodes
              ──▶  derive stable selector + role + label + state
              ──▶  Page Affordance Model (list[Affordance], source="DOM")

The reasoning core never sees raw HTML, which avoids the context-bloat /
token-exhaustion failure mode flagged in the assessment. The transducer is
pure-stdlib (``html.parser``) so it runs in well under the System-1 latency
budget and needs no browser to unit-test.

Selector preference (most → least stable) drives the confidence score so the
router can prefer DOM grounding only when a robust locator exists:

    #id  >  [data-testid=...]  >  [name=...]  >  tag:nth-of-type
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from src.contracts.types import Affordance
from src.perception.page_affordance_model import PageAffordanceModel

_INTERACTIVE_TAGS = frozenset(["a", "button", "input", "select", "textarea"])
_STRIP_TAGS = frozenset(["script", "style", "meta", "link", "noscript", "head", "svg"])

# HTML tag / ARIA role  →  primitive action the executor can perform.
_TAG_ACTION = {
    "button": "click",
    "a": "click",
    "select": "select",
    "textarea": "type",
}
_INPUT_TYPE_ACTION = {
    "text": "type",
    "number": "type",
    "email": "type",
    "password": "type",
    "search": "type",
    "tel": "type",
    "url": "type",
    "date": "type",
    "time": "type",
    "checkbox": "click",
    "radio": "click",
    "submit": "click",
    "button": "click",
    "reset": "click",
}
_AFFORDANCE_TYPE = {"click": "button", "type": "input", "select": "input"}

# Selector strategy → confidence. Stable hooks score high; positional ones low.
_SELECTOR_CONFIDENCE = {"id": 1.0, "testid": 0.97, "name": 0.85, "positional": 0.55}


class _InteractiveParser(HTMLParser):
    """Single-pass collector of interactive elements with their attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth: int | None = None
        self._depth = 0
        self._tag_counts: dict[str, int] = {}
        self._open: list[dict[str, Any]] = []
        self.total_nodes = 0
        self.nodes: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        self.total_nodes += 1
        if self._skip_depth is None and tag in _STRIP_TAGS:
            self._skip_depth = self._depth
            return
        if self._skip_depth is not None:
            return
        attr = {k: (v or "") for k, v in attrs}
        if attr.get("hidden") is not None or attr.get("aria-hidden") == "true":
            return
        if tag in _INTERACTIVE_TAGS:
            self._tag_counts[tag] = self._tag_counts.get(tag, 0) + 1
            node = {
                "tag": tag,
                "attr": attr,
                "nth": self._tag_counts[tag],
                "text_parts": [],
            }
            self.nodes.append(node)
            self._open.append(node)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth is not None and self._depth == self._skip_depth:
            self._skip_depth = None
        if self._open and self._open[-1]["tag"] == tag:
            self._open.pop()
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth is None and self._open:
            text = data.strip()
            if text:
                self._open[-1]["text_parts"].append(text)


def _selector_for(node: dict[str, Any]) -> tuple[str, float]:
    """Return (css_selector, confidence) using the most stable hook available."""
    attr, tag = node["attr"], node["tag"]
    if attr.get("id"):
        return f"#{attr['id']}", _SELECTOR_CONFIDENCE["id"]
    if attr.get("data-testid"):
        return f"[data-testid='{attr['data-testid']}']", _SELECTOR_CONFIDENCE["testid"]
    if attr.get("name"):
        return f"{tag}[name='{attr['name']}']", _SELECTOR_CONFIDENCE["name"]
    return f"{tag}:nth-of-type({node['nth']})", _SELECTOR_CONFIDENCE["positional"]


def _label_for(node: dict[str, Any]) -> str:
    attr = node["attr"]
    for key in ("aria-label", "value", "placeholder", "title", "alt"):
        if attr.get(key):
            return attr[key].strip()
    text = " ".join(node["text_parts"]).strip()
    if text:
        return text
    return attr.get("name") or attr.get("id") or node["tag"]


def _action_for(node: dict[str, Any]) -> str:
    attr, tag = node["attr"], node["tag"]
    if tag == "input":
        return _INPUT_TYPE_ACTION.get(attr.get("type", "text").lower(), "type")
    return _TAG_ACTION.get(tag, "click")


def _bbox_from_attrs(attr: dict[str, str]) -> list[int] | None:
    """Read an optional ``data-bbox='x,y,w,h'`` hint emitted by the dashboard."""
    raw = attr.get("data-bbox")
    if not raw:
        return None
    try:
        parts = [int(float(p)) for p in raw.split(",")]
        return parts if len(parts) == 4 else None
    except ValueError:
        return None


class DomTransducer:
    """Convert raw HTML (or accessibility-augmented HTML) into a PAM."""

    def transduce(
        self,
        html: str,
        *,
        page_id: str = "page",
        url: str = "",
        captured_at_ms: int = 0,
    ) -> PageAffordanceModel:
        parser = _InteractiveParser()
        parser.feed(html or "")
        parser.close()

        affordances: list[Affordance] = []
        for node in parser.nodes:
            attr = node["attr"]
            action = _action_for(node)
            selector, conf = _selector_for(node)
            disabled = attr.get("disabled") is not None or attr.get("aria-disabled") == "true"
            locator: dict[str, Any] = {"selector": selector, "strategy": "css"}
            bbox = _bbox_from_attrs(attr)
            if bbox is not None:
                locator["bbox"] = bbox
            affordances.append(
                Affordance(
                    id=f"dom_{node['tag']}_{node['nth']}",
                    source="DOM",
                    type=_AFFORDANCE_TYPE.get(action, "button"),  # type: ignore[arg-type]
                    label=_label_for(node),
                    action=action,
                    locator=locator,
                    confidence=0.0 if disabled else conf,
                    state={"enabled": not disabled, "visible": True},
                    safety_level="low",
                )
            )

        return PageAffordanceModel(
            page_id=page_id,
            url=url,
            affordances=affordances,
            captured_at_ms=captured_at_ms,
            raw_node_count=parser.total_nodes,
            kept_node_count=len(affordances),
        )


def transduce(html: str, **kwargs: Any) -> PageAffordanceModel:
    """Module-level convenience wrapper."""
    return DomTransducer().transduce(html, **kwargs)
