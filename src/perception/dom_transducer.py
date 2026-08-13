"""DOM Transduction Pattern: raw HTML -> compact Page Affordance Model.

The reasoning core should consume stable affordances, not raw markup. This
module strips noisy tags, keeps interactive/ARIA nodes, derives selectors,
labels, actions, state, and optional visual bbox hints, then emits the shared
Affordance contract used by System 1 and the router.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Literal

from src.contracts.types import Affordance
from src.perception.page_affordance_model import PageAffordanceModel

_INTERACTIVE_TAGS = frozenset(["a", "button", "input", "select", "textarea", "label", "form", "option"])
_STRIP_TAGS = frozenset(["script", "style", "meta", "link", "noscript", "head", "svg"])
_VOID_STRIP_TAGS = frozenset(["meta", "link"])
_HTML_VOID_TAGS = frozenset(["area", "base", "br", "embed", "hr", "img", "input", "source", "track", "wbr"])
# Demo overlays (cursor, highlight ring, task badge) tag *live* page elements with
# __cua_* ids/classes while a run is being shown. Those are our own artifacts, so
# perception must ignore them: otherwise the transducer emits fabricated locators
# such as "#__cua_target" (confidence 1.0) that only exist mid-demo and change on
# every step, which is exactly the overlay-contamination / unstable-locator issue.
_OVERLAY_ATTR_PREFIX = "__cua_"
_ARIA_ACTION_MAP = {
    "button": "click",
    "link": "click",
    "textbox": "type",
    "combobox": "select",
    "listbox": "select",
    "checkbox": "click",
    "radio": "click",
    "spinbutton": "type",
    "searchbox": "type",
}
_TAG_ACTION = {"button": "click", "a": "click", "select": "select", "textarea": "type"}
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
_SELECTOR_CONFIDENCE = {"id": 1.0, "testid": 0.97, "name": 0.85, "class": 0.7, "positional": 0.55}
_RUNTIME_OVERLAY_IDS = frozenset(["__cua_cursor", "__cua_cap", "__cua_badge", "__cua_style"])


def _strip_overlay_attrs(attr: dict[str, str]) -> dict[str, str]:
    """Drop demo-overlay ids/classes so derived locators stay page-stable.

    The element itself is kept (it is a real page element that merely carries our
    marker); only the injected id/class are removed, so selector derivation falls
    through to the next genuine strategy instead of locking onto our own marker.
    """
    if attr.get("id", "").startswith(_OVERLAY_ATTR_PREFIX):
        attr = {k: v for k, v in attr.items() if k != "id"}
    classes = attr.get("class", "")
    if _OVERLAY_ATTR_PREFIX in classes:
        kept = " ".join(c for c in classes.split() if not c.startswith(_OVERLAY_ATTR_PREFIX))
        attr = {**attr, "class": kept} if kept else {k: v for k, v in attr.items() if k != "class"}
    return attr


class _InteractiveParser(HTMLParser):
    """Single-pass collector for interactable DOM nodes."""

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
        attr = {k: (v or "") for k, v in attrs}
        is_runtime_overlay = (
            attr.get("id") in _RUNTIME_OVERLAY_IDS
            or attr.get("data-agent-overlay") == "true"
            or attr.get("data-runtime-overlay") == "true"
        )
        if self._skip_depth is None and (tag in _STRIP_TAGS or is_runtime_overlay):
            if tag in _VOID_STRIP_TAGS or tag in _HTML_VOID_TAGS:
                self._depth = max(0, self._depth - 1)
                return
            self._skip_depth = self._depth
            return
        if self._skip_depth is not None:
            if tag in _VOID_STRIP_TAGS or tag in _HTML_VOID_TAGS:
                self._depth = max(0, self._depth - 1)
            return

        attr = _strip_overlay_attrs({k: (v or "") for k, v in attrs})
        role = attr.get("role", "")
        if "hidden" in attr or attr.get("aria-hidden") == "true":
            return
        if tag not in _INTERACTIVE_TAGS and role not in _ARIA_ACTION_MAP:
            return

        self._tag_counts[tag] = self._tag_counts.get(tag, 0) + 1
        node = {"tag": tag, "attr": attr, "nth": self._tag_counts[tag], "text_parts": []}
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

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        depth_before = self._depth
        self.handle_starttag(tag, attrs)
        if self._depth > depth_before:
            self.handle_endtag(tag)


def _escape_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _selector_for(node: dict[str, Any]) -> tuple[str, float]:
    attr, tag = node["attr"], node["tag"]
    if attr.get("id"):
        return f"#{attr['id']}", _SELECTOR_CONFIDENCE["id"]
    if attr.get("data-testid"):
        return f"[data-testid='{_escape_attr(attr['data-testid'])}']", _SELECTOR_CONFIDENCE["testid"]
    if attr.get("name"):
        return f"{tag}[name='{_escape_attr(attr['name'])}']", _SELECTOR_CONFIDENCE["name"]
    if attr.get("class"):
        return f"{tag}.{attr['class'].split()[0]}", _SELECTOR_CONFIDENCE["class"]
    return f"{tag}:nth-of-type({node['nth']})", _SELECTOR_CONFIDENCE["positional"]


# Attributes that tell otherwise-identical controls apart, most stable first.
# data-* hooks come before these because they exist to identify an element.
_DISAMBIGUATING_ATTRS = ("aria-label", "title", "value", "placeholder", "type", "href", "alt")


def _disambiguate(node: dict[str, Any], base: str, twins: list[dict[str, Any]]) -> str:
    """Narrow a selector that matches several elements down to one.

    A class name is shared by design, so "button.add-cart-btn" names every
    product on the page at once. Anything that then queries it - a probe asking
    whether the target is disabled, say - silently measures the first match
    instead of the intended element, and reports a healthy control while the
    real one is dead. The attributes tried here are the ones that actually
    distinguish the elements, and only a value unique among the twins is used.
    """
    attr = node["attr"]
    keys = [k for k in attr if k.startswith("data-") and k != "data-bbox"]
    keys += [k for k in _DISAMBIGUATING_ATTRS if attr.get(k)]
    for key in keys:
        value = attr.get(key, "").strip()
        if value and sum(1 for twin in twins if twin["attr"].get(key, "").strip() == value) == 1:
            return f"{base}[{key}='{_escape_attr(value)}']"
    return base


def _label_for(node: dict[str, Any]) -> str:
    attr = node["attr"]
    for key in ("aria-label", "value", "placeholder", "title", "alt", "name", "id"):
        if attr.get(key):
            return attr[key].strip()
    text = " ".join(node["text_parts"]).strip()
    return text or node["tag"]


def _action_for(node: dict[str, Any]) -> str:
    attr, tag = node["attr"], node["tag"]
    role = attr.get("role", "")
    if role in _ARIA_ACTION_MAP:
        return _ARIA_ACTION_MAP[role]
    if tag == "input":
        return _INPUT_TYPE_ACTION.get(attr.get("type", "text").lower(), "type")
    return _TAG_ACTION.get(tag, "click")


def _bbox_from_attrs(attr: dict[str, str]) -> list[int] | None:
    raw = attr.get("data-bbox")
    if not raw:
        return None
    try:
        parts = [int(float(p)) for p in raw.split(",")]
    except ValueError:
        return None
    return parts if len(parts) == 4 else None


def _map_action_to_type(action: str) -> Literal["button", "input", "property", "action", "event", "sensor"]:
    return _AFFORDANCE_TYPE.get(action, "button")  # type: ignore[return-value]


class DomTransducer:
    """Convert HTML/accessibility-augmented HTML into a PAM."""

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

        # A selector shared by four buttons is not a locator. Group the derived
        # selectors first, refine the collisions, and drop the confidence of any
        # that stays ambiguous so the rest of the system can tell.
        derived = [_selector_for(node) for node in parser.nodes]
        by_selector: dict[str, list[dict[str, Any]]] = {}
        for node, (selector, _) in zip(parser.nodes, derived):
            by_selector.setdefault(selector, []).append(node)

        affordances: list[Affordance] = []
        for node, (selector, confidence) in zip(parser.nodes, derived):
            attr = node["attr"]
            action = _action_for(node)
            twins = by_selector[selector]
            if len(twins) > 1:
                refined = _disambiguate(node, selector, twins)
                if refined == selector:
                    confidence = _SELECTOR_CONFIDENCE["positional"]  # still ambiguous, and says so
                selector = refined
            disabled = "disabled" in attr or attr.get("aria-disabled") == "true"
            locator: dict[str, Any] = {"selector": selector, "strategy": "css"}
            bbox = _bbox_from_attrs(attr)
            if bbox is not None:
                locator["bbox"] = bbox
            affordances.append(
                Affordance(
                    id=f"dom_{node['tag']}_{node['nth']}",
                    source="DOM",
                    type=_map_action_to_type(action),
                    label=_label_for(node),
                    action=action,
                    locator=locator,
                    confidence=0.0 if disabled else confidence,
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
    """Module-level convenience wrapper for the new API."""
    return DomTransducer().transduce(html, **kwargs)


def parse_html(html: str, page_id: str = "page") -> PageAffordanceModel:
    """Backward-compatible wrapper kept for earlier PR tests/demo scripts."""
    return DomTransducer().transduce(html, page_id=page_id)


def reset_id_counter() -> None:
    """Backward-compatible no-op; IDs are deterministic per parser instance."""
    return None
