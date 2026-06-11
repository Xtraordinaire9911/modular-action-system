"""WoT Hypermedia Affordance Parser — runtime TD ingestion (advisor §4).

Implements the Hypermedia Affordances Recognition Pattern: the agent knows only
TD *seed URLs* and parses W3C Thing Descriptions **at runtime** to discover
device capabilities. Nothing about device endpoints is hard-coded — every
``href``/method/contentType is read out of the TD's ``forms`` (HATEOAS), and the
``securityDefinitions`` + rate-limit annotations are extracted dynamically.

Each interaction affordance (property read/write, action, event) becomes a
unified ``Affordance`` with ``source="WOT"`` so it routes through the same
contract as DOM and Visual affordances. Property affordances additionally yield
``StateAssertion`` sources used for empirical postcondition checking.

Pure-stdlib; the executor (``wot_executor``) performs the actual HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.contracts.types import Affordance
from src.perception.wot_security import (
    RateLimit,
    SecurityScheme,
    active_scheme,
    parse_rate_limit,
    parse_security_definitions,
)

# Default HTTP method per WoT operation when a form omits htv:methodName.
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


@dataclass
class StateAssertionSource:
    """Where a device property lives, so postcondition checks can read it back."""

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
    """All affordances + provenance discovered from one Thing Description."""

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
    if base:
        return base.rstrip("/") + "/" + href.lstrip("/")
    return href


def _first_form(forms: list[dict[str, Any]], ops: tuple[str, ...]) -> dict[str, Any] | None:
    """Pick the first form whose declared op matches; return None if none match."""
    for form in forms:
        declared = form.get("op")
        names = [declared] if isinstance(declared, str) else list(declared or [])
        if any(o in names for o in ops):
            return form
    return None


class TdAffordanceParser:
    """Parse a single Thing Description into a ThingAffordanceModel."""

    def parse(self, td: dict[str, Any]) -> ThingAffordanceModel:
        if not isinstance(td, dict) or "title" not in td and "id" not in td:
            raise ValueError("malformed Thing Description: missing id/title")

        thing_id = str(td.get("id") or td.get("title"))
        title = str(td.get("title", thing_id))
        base = str(td.get("base", ""))
        schemes = parse_security_definitions(td)
        security = active_scheme(td, schemes)
        thing_rate = parse_rate_limit(td.get("rateLimit") or td.get("wot:rateLimit"))

        affordances: list[Affordance] = []
        state_sources: list[StateAssertionSource] = []

        # ── Properties (read / write) ────────────────────────────────────────
        for prop_name, prop in (td.get("properties") or {}).items():
            forms = prop.get("forms") or []
            read_only = bool(prop.get("readOnly", False))
            write_only = bool(prop.get("writeOnly", False))
            has_explicit_ops = any("op" in f for f in forms)

            read_form = _first_form(forms, ("readproperty",))
            if read_form is None and forms and not has_explicit_ops:
                read_form = forms[0]
            if read_form is not None:
                href = _resolve_href(base, read_form.get("href", ""))
                method = _method_for("readproperty", read_form)
                state_sources.append(StateAssertionSource(thing_id, prop_name, href, method, read_only))
            if not read_only:
                w_form = _first_form(forms, ("writeproperty",))
                if w_form is None and forms and not has_explicit_ops:
                    w_form = forms[0]
                if w_form is not None:
                    affordances.append(
                        self._affordance(
                            thing_id, prop_name, "writeproperty", w_form, base,
                            input_schema=_schema_of(prop), security=security,
                            rate_limit=parse_rate_limit(w_form.get("rateLimit")) or thing_rate,
                            type_="property",
                        )
                    )
            _ = write_only  # reserved for write-only sensors

        # ── Actions ──────────────────────────────────────────────────────────
        for action_name, action in (td.get("actions") or {}).items():
            forms = action.get("forms") or []
            form = _first_form(forms, ("invokeaction",))
            if form is None:
                continue
            affordances.append(
                self._affordance(
                    thing_id, action_name, "invokeaction", form, base,
                    input_schema=action.get("input"), security=security,
                    rate_limit=parse_rate_limit(form.get("rateLimit")) or thing_rate,
                    type_="action",
                    safety_level=str(action.get("safety_level", "low")),
                )
            )

        events = list((td.get("events") or {}).keys())
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
        safety_level: str = "low",
    ) -> Affordance:
        href = _resolve_href(base, form.get("href", ""))
        if not href:
            raise ValueError(f"affordance {thing_id}.{name} has no href (HATEOAS violation)")
        method = _method_for(op, form)
        state: dict[str, Any] = {
            "input_schema": input_schema or {},
            "content_type": form.get("contentType", "application/json"),
            "security": security.scheme if security else "nosec",
        }
        if rate_limit is not None:
            state["rate_limit"] = {
                "max_requests": rate_limit.max_requests,
                "window_seconds": rate_limit.window_seconds,
                "min_interval_ms": round(rate_limit.min_interval_ms, 2),
            }
        return Affordance(
            id=f"wot_{thing_id}_{name}",
            source="WOT",
            type="action" if type_ == "action" else "property",
            label=name,
            action=_OP_ACTION.get(op, "invoke"),
            locator={"thing_id": thing_id, "href": href, "method": method},
            confidence=1.0,
            state=state,
            safety_level=safety_level,
        )


def _schema_of(prop: dict[str, Any]) -> dict[str, Any]:
    return {k: prop[k] for k in ("type", "minimum", "maximum", "enum") if k in prop}


def parse_things(tds: list[dict[str, Any]]) -> list[ThingAffordanceModel]:
    """Parse a batch of discovered TDs, skipping malformed ones gracefully."""
    parser = TdAffordanceParser()
    models: list[ThingAffordanceModel] = []
    for td in tds:
        try:
            models.append(parser.parse(td))
        except (ValueError, KeyError, TypeError):
            continue  # malformed TD → recovery layer is informed via missing affordance
    return models
