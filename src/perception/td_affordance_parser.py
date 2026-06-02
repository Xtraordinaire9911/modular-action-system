"""WoT Thing Description affordance parser.

Dynamically ingests W3C TD 1.1 JSON-LD documents at runtime. Extracts
Properties, Actions, Events, securityDefinitions, and rate-limit hints.
No endpoint is hard-coded; all hrefs come from the TD forms arrays.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.contracts.types import Affordance


class TDParseError(ValueError):
    pass


def _extract_security(td: dict[str, Any]) -> dict[str, Any]:
    """Return a flat security summary from the TD securityDefinitions block."""
    defs = td.get("securityDefinitions", {})
    active = td.get("security", "")
    if isinstance(active, list):
        active = active[0] if active else ""
    scheme_def = defs.get(active, {})
    return {
        "scheme": scheme_def.get("scheme", "nosec"),
        "in": scheme_def.get("in", ""),
        "name": scheme_def.get("name", ""),
    }


def _rate_limit(td: dict[str, Any]) -> str | None:
    """Return a rate-limit string if the TD carries one, else None."""
    hints = td.get("rateLimit", td.get("rate_limit", None))
    if hints:
        return str(hints)
    return None


def _first_href(forms: list[dict[str, Any]]) -> str:
    if not forms:
        return ""
    return forms[0].get("href", "")


def _http_method(forms: list[dict[str, Any]]) -> str:
    if not forms:
        return "GET"
    return forms[0].get("htv:methodName", "GET")


def parse_td(td: dict[str, Any]) -> list[Affordance]:
    """Parse a single W3C Thing Description dict into a list of Affordances.

    Raises TDParseError if the document is structurally invalid (missing
    required fields per TD 1.1).
    """
    if "@context" not in td:
        raise TDParseError("TD missing @context field")
    if "id" not in td and "title" not in td:
        raise TDParseError("TD must have at least 'id' or 'title'")

    thing_id = td.get("id", td.get("title", "unknown"))
    security = _extract_security(td)
    rate_limit = _rate_limit(td)
    affordances: list[Affordance] = []

    # ── Properties ────────────────────────────────────────────────────────────
    for name, prop in td.get("properties", {}).items():
        forms = prop.get("forms", [])
        href = _first_href(forms)
        aff = Affordance(
            id=f"wot_{thing_id}_{name}",
            source="WOT",
            type="property",
            label=name,
            action="read_property",
            locator={
                "thing_id": thing_id,
                "href": href,
                "method": _http_method(forms),
            },
            confidence=1.0,
            state={
                "schema": {k: v for k, v in prop.items() if k not in ("forms",)},
                "security": security,
                "rate_limit": rate_limit,
                "read_only": prop.get("readOnly", False),
            },
            safety_level="low",
        )
        affordances.append(aff)

    # ── Actions ───────────────────────────────────────────────────────────────
    for name, action in td.get("actions", {}).items():
        forms = action.get("forms", [])
        href = _first_href(forms)
        aff = Affordance(
            id=f"wot_{thing_id}_{name}",
            source="WOT",
            type="action",
            label=name,
            action="invoke",
            locator={
                "thing_id": thing_id,
                "href": href,
                "method": _http_method(forms),
            },
            confidence=1.0,
            state={
                "input_schema": action.get("input", {}),
                "output_schema": action.get("output", {}),
                "security": security,
                "rate_limit": rate_limit,
            },
            safety_level="low",
        )
        affordances.append(aff)

    # ── Events ────────────────────────────────────────────────────────────────
    for name, event in td.get("events", {}).items():
        forms = event.get("forms", [])
        href = _first_href(forms)
        aff = Affordance(
            id=f"wot_{thing_id}_{name}_event",
            source="WOT",
            type="event",
            label=name,
            action="subscribe",
            locator={
                "thing_id": thing_id,
                "href": href,
                "method": _http_method(forms),
            },
            confidence=1.0,
            state={
                "data_schema": event.get("data", {}),
                "security": security,
            },
            safety_level="low",
        )
        affordances.append(aff)

    return affordances


def parse_td_file(path: str | Path) -> list[Affordance]:
    """Load a TD JSON file from disk and return its affordances."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_td(data)


def parse_td_directory(directory: str | Path) -> dict[str, list[Affordance]]:
    """Parse all *.td.json files in *directory* and return a name→affordances map."""
    result: dict[str, list[Affordance]] = {}
    for path in sorted(Path(directory).glob("*.td.json")):
        result[path.stem] = parse_td_file(path)
    return result
