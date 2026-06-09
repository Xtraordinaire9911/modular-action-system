"""DOM Transducer — converts raw HTML into a Page Affordance Model.

Strips script/style/tracking tags, keeps only interactive and semantic nodes,
and returns a compact list of Affordance objects the rest of the pipeline can
route through without ever reading raw HTML.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from src.contracts.types import Affordance

_INTERACTIVE_TAGS = frozenset(
    ["a", "button", "input", "select", "textarea", "label", "form", "option"]
)
_STRIP_TAGS = frozenset(["script", "style", "meta", "link", "noscript", "head"])
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


class _InteractiveParser(HTMLParser):
    """Single-pass HTML parser collecting interactive elements."""

    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self._skip_depth: int | None = None
        self._nodes: list[dict[str, Any]] = []
        self._interactive_stack: list[tuple[str, int]] = []

    # ------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        if self._skip_depth is not None:
            return
        if tag in _STRIP_TAGS:
            self._skip_depth = self._depth
            return
        if tag not in _INTERACTIVE_TAGS:
            return
        attr_map = dict(attrs)
        node: dict[str, Any] = {
            "tag": tag,
            "id": attr_map.get("id", ""),
            "name": attr_map.get("name", ""),
            "type": attr_map.get("type", ""),
            "role": attr_map.get("role", ""),
            "aria_label": attr_map.get("aria-label", ""),
            "placeholder": attr_map.get("placeholder", ""),
            "class": attr_map.get("class", ""),
            "href": attr_map.get("href", ""),
            "text": "",
            "data_attrs": {k: v for k, v in attr_map.items() if k.startswith("data-")},
        }
        self._nodes.append(node)
        self._interactive_stack.append((tag, len(self._nodes) - 1))

    def handle_data(self, data: str) -> None:
        if self._skip_depth is not None or not self._interactive_stack:
            return
        text = data.strip()
        if not text:
            return
        _, node_index = self._interactive_stack[-1]
        node = self._nodes[node_index]
        node["text"] = f'{node["text"]} {text}'.strip()

    def handle_endtag(self, tag: str) -> None:
        if self._interactive_stack and self._interactive_stack[-1][0] == tag:
            self._interactive_stack.pop()
        if self._skip_depth is not None and self._depth == self._skip_depth:
            self._skip_depth = None
        self._depth -= 1

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return self._nodes


def _build_locator(node: dict[str, Any]) -> dict[str, Any]:
    """Return the most stable CSS selector for a node, preferring id."""

    def _escape_attr(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    if node["id"]:
        return {"selector": f'[id="{_escape_attr(node["id"])}"]'}
    if node["name"]:
        tag = node["tag"]
        return {"selector": f'{tag}[name="{_escape_attr(node["name"])}"]'}
    if node["class"]:
        first_cls = node["class"].split()[0]
        return {"selector": f"{node['tag']}.{first_cls}"}
    return {"selector": node["tag"]}


def _infer_action(node: dict[str, Any]) -> str:
    explicit_role = node.get("role", "")
    if explicit_role in _ARIA_ACTION_MAP:
        return _ARIA_ACTION_MAP[explicit_role]
    tag = node["tag"]
    itype = node.get("type", "").lower()
    if tag == "a":
        return "click"
    if tag == "button":
        return "click"
    if tag in ("input", "textarea"):
        if itype in ("submit", "button", "checkbox", "radio"):
            return "click"
        return "type"
    if tag == "select":
        return "select"
    return "click"


def _infer_label(node: dict[str, Any]) -> str:
    for key in ("aria_label", "placeholder", "name", "id", "text"):
        val = node.get(key, "")
        if val:
            return str(val)
    return node["tag"]


_COUNTER: dict[str, int] = {}


def _make_id(tag: str) -> str:
    _COUNTER[tag] = _COUNTER.get(tag, 0) + 1
    return f"dom_{tag}_{_COUNTER[tag]}"


def reset_id_counter() -> None:
    """Call between parse sessions in tests to get deterministic IDs."""
    _COUNTER.clear()


class PageAffordanceModel:
    """Lightweight container for the parsed affordances of one page."""

    def __init__(self, page_id: str, affordances: list[Affordance]) -> None:
        self.page_id = page_id
        self.affordances = affordances

    def __repr__(self) -> str:
        return (
            f"PageAffordanceModel(page_id={self.page_id!r}, n={len(self.affordances)})"
        )

    def find_by_label(self, text: str) -> Affordance | None:
        text_lower = text.lower()
        for a in self.affordances:
            if text_lower in a.label.lower():
                return a
        return None

    def find_by_selector(self, selector: str) -> Affordance | None:
        for a in self.affordances:
            if a.locator.get("selector") == selector:
                return a
        return None


def parse_html(html: str, page_id: str = "page") -> PageAffordanceModel:
    """Parse *html* and return a PageAffordanceModel.

    Skips script/style/noscript blocks. Only interactive tags produce
    Affordance entries so the downstream LLM/router never sees raw markup.
    """
    reset_id_counter()
    parser = _InteractiveParser()
    parser.feed(html)

    affordances: list[Affordance] = []
    for node in parser.nodes:
        aff = Affordance(
            id=_make_id(node["tag"]),
            source="DOM",
            type=_map_tag_to_type(node),
            label=_infer_label(node),
            action=_infer_action(node),
            locator=_build_locator(node),
            confidence=1.0,
            state={},
            safety_level="low",
        )
        affordances.append(aff)

    return PageAffordanceModel(page_id=page_id, affordances=affordances)


def _map_tag_to_type(node: dict[str, Any]) -> str:
    tag = node["tag"]
    itype = node.get("type", "").lower()
    if tag in ("button",) or itype in ("submit", "button"):
        return "button"
    if tag in ("input", "textarea"):
        return "input"
    return "button"
