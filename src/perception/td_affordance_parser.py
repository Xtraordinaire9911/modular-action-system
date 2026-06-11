"""WoT Thing Description affordance parser.

Parses W3C TD JSON-LD at runtime so device endpoints, HTTP methods,
securityDefinitions, and rate limits come from hypermedia forms instead of
hard-coded assumptions. The primary API returns a ThingAffordanceModel; legacy
helpers returning plain affordance lists remain for earlier PRs and demos.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.contracts.types import Affordance
from src.perception.wot_security import (
    RateLimit,
    SecurityScheme,
    active_scheme,
    parse_rate_limit,
    parse_security_definitions,
)

_DEFAULT_METHOD = {
    "readproperty": "GET",
    "writeproperty": "PUT",
    "observeproperty": "GET",
    "invokeaction": "POST",
    "subscribeevent": "GET",
}
_OP_ACTION = {
    "readproperty": "read_property",
    "writeproperty": "write_property",
    "invokeaction": "invoke",
    "subscribeevent": "subscribe",
}


class TDParseError(ValueError):
    pass


@dataclass
class StateAssertionSource:
    thing_id: str
    property: str
    href: str
    method: str
    read_only: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "thing_id": self.thing_id,
            "property": self.property,
            "href": self.href,
            "method": self.method,
            "read_only": self.read_only,
        }


@dataclass
class ThingAffordanceModel:
    thing_id: str
    title: str
    affordances: list[Affordance]
    state_sources: list[StateAssertionSource]
    security: SecurityScheme | None
    rate_limit: RateLimit | None
    base: str = ""
    events: list[str] = field(default_factory=list)

    def action(self, name: str) -> Affordance | None:
        wanted = f"wot_{self.thing_id}_{name}"
        return next((a for a in self.affordances if a.id == wanted), None)


def _method_for(op: str, form: dict[str, Any]) -> str:
    return str(form.get("htv:methodName") or _DEFAULT_METHOD.get(op, "GET")).upper()


def _resolve_href(base: str, href: str) -> str:
    if not href:
        return href
    if href.startswith(("http://", "https://", "coap://", "mqtt://")):
        return href
    return base.rstrip("/") + "/" + href.lstrip("/") if base else href


def _declared_ops(form: dict[str, Any]) -> list[str]:
    declared = form.get("op")
    return [declared] if isinstance(declared, str) else list(declared or [])


def _first_form(forms: list[dict[str, Any]], ops: tuple[str, ...]) -> dict[str, Any] | None:
    for form in forms:
        if any(op in _declared_ops(form) for op in ops):
            return form
    return forms[0] if forms else None


def _schema_of(prop: dict[str, Any]) -> dict[str, Any]:
    return {k: prop[k] for k in ("type", "minimum", "maximum", "enum", "readOnly", "writeOnly") if k in prop}


def _security_summary(security: SecurityScheme | None) -> dict[str, Any]:
    if security is None:
        return {"scheme": "nosec", "in": "", "name": ""}
    return {"scheme": security.scheme, "in": security.location, "name": security.field_name}


def _rate_state(rate_limit: RateLimit | None) -> dict[str, Any] | None:
    if rate_limit is None:
        return None
    return {
        "max_requests": rate_limit.max_requests,
        "window_seconds": rate_limit.window_seconds,
        "min_interval_ms": round(rate_limit.min_interval_ms, 2),
    }


class TdAffordanceParser:
    """Parse a single Thing Description into a ThingAffordanceModel."""

    def parse(self, td: dict[str, Any]) -> ThingAffordanceModel:
        if not isinstance(td, dict):
            raise TDParseError("TD must be a JSON object")
        if "id" not in td and "title" not in td:
            raise TDParseError("TD must have at least 'id' or 'title'")

        thing_id = str(td.get("id") or td.get("title"))
        title = str(td.get("title", thing_id))
        base = str(td.get("base", ""))
        schemes = parse_security_definitions(td)
        security = active_scheme(td, schemes)
        thing_rate = parse_rate_limit(td.get("rateLimit") or td.get("wot:rateLimit") or td.get("rate_limit"))

        affordances: list[Affordance] = []
        state_sources: list[StateAssertionSource] = []

        for prop_name, prop in (td.get("properties") or {}).items():
            forms = list(prop.get("forms") or [])
            read_only = bool(prop.get("readOnly", False))
            write_only = bool(prop.get("writeOnly", False))
            read_form = _first_form(forms, ("readproperty", "observeproperty"))
            if read_form is not None and not write_only:
                href = _resolve_href(base, read_form.get("href", ""))
                method = _method_for("readproperty", read_form)
                state_sources.append(StateAssertionSource(thing_id, prop_name, href, method, read_only))
            if not read_only:
                write_form = _first_form(forms, ("writeproperty",)) or (forms[0] if forms else None)
                if write_form is not None:
                    affordances.append(
                        self._affordance(
                            thing_id,
                            prop_name,
                            "writeproperty",
                            write_form,
                            base,
                            input_schema=_schema_of(prop),
                            security=security,
                            rate_limit=parse_rate_limit(write_form.get("rateLimit")) or thing_rate,
                            type_="property",
                            extra_state={"read_only": read_only, "write_only": write_only},
                        )
                    )

        for action_name, action in (td.get("actions") or {}).items():
            form = _first_form(list(action.get("forms") or []), ("invokeaction",))
            if form is None:
                continue
            affordances.append(
                self._affordance(
                    thing_id,
                    action_name,
                    "invokeaction",
                    form,
                    base,
                    input_schema=action.get("input"),
                    output_schema=action.get("output"),
                    security=security,
                    rate_limit=parse_rate_limit(form.get("rateLimit")) or thing_rate,
                    type_="action",
                    safety_level=str(action.get("safety_level", "low")),
                )
            )

        events: list[str] = []
        for event_name, event in (td.get("events") or {}).items():
            events.append(event_name)
            form = _first_form(list(event.get("forms") or []), ("subscribeevent",))
            if form is None:
                continue
            affordances.append(
                self._affordance(
                    thing_id,
                    f"{event_name}_event",
                    "subscribeevent",
                    form,
                    base,
                    input_schema=event.get("data"),
                    security=security,
                    rate_limit=parse_rate_limit(form.get("rateLimit")) or thing_rate,
                    type_="event",
                )
            )

        return ThingAffordanceModel(
            thing_id=thing_id,
            title=title,
            affordances=affordances,
            state_sources=state_sources,
            security=security,
            rate_limit=thing_rate,
            base=base,
            events=events,
        )

    def _affordance(
        self,
        thing_id: str,
        name: str,
        op: str,
        form: dict[str, Any],
        base: str,
        *,
        input_schema: dict[str, Any] | None,
        security: SecurityScheme | None,
        rate_limit: RateLimit | None,
        type_: str,
        output_schema: dict[str, Any] | None = None,
        safety_level: str = "low",
        extra_state: dict[str, Any] | None = None,
    ) -> Affordance:
        href = _resolve_href(base, str(form.get("href", "")))
        if not href:
            raise TDParseError(f"affordance {thing_id}.{name} has no href")
        state: dict[str, Any] = {
            "input_schema": input_schema or {},
            "content_type": form.get("contentType", "application/json"),
            "security": security.scheme if security else "nosec",
            "security_definition": _security_summary(security),
        }
        if output_schema is not None:
            state["output_schema"] = output_schema
        if rate_limit is not None:
            state["rate_limit"] = _rate_state(rate_limit)
        if extra_state:
            state.update(extra_state)
        return Affordance(
            id=f"wot_{thing_id}_{name}",
            source="WOT",
            type="action" if type_ == "action" else "event" if type_ == "event" else "property",
            label=name.removesuffix("_event"),
            action=_OP_ACTION.get(op, "invoke"),
            locator={"thing_id": thing_id, "href": href, "method": _method_for(op, form)},
            confidence=1.0,
            state=state,
            safety_level=safety_level,
        )


def parse_things(tds: list[dict[str, Any]]) -> list[ThingAffordanceModel]:
    parser = TdAffordanceParser()
    models: list[ThingAffordanceModel] = []
    for td in tds:
        try:
            models.append(parser.parse(td))
        except (TDParseError, ValueError, KeyError, TypeError):
            continue
    return models


def parse_td(td: dict[str, Any]) -> list[Affordance]:
    """Backward-compatible plain-affordance parser."""
    if "@context" not in td:
        raise TDParseError("TD missing @context field")
    return TdAffordanceParser().parse(td).affordances


def parse_td_file(path: str | Path) -> list[Affordance]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_td(data)


def parse_td_directory(directory: str | Path) -> dict[str, list[Affordance]]:
    result: dict[str, list[Affordance]] = {}
    for path in sorted(Path(directory).glob("*.td.json")):
        result[path.stem] = parse_td_file(path)
    return result
