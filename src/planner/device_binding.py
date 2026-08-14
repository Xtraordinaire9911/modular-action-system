"""Resolve a device goal against the Things the environment actually reports.

The web bindings in :mod:`src.planner.environment_binding` name a page and a
family of controls. A device goal cannot work that way: which Things exist, what
they are called, which of their properties are writable, and what URL to write
to are all facts the environment publishes in its Thing Descriptions, and they
change when someone adds a device.

So nothing here names an endpoint. A binding says only *what kind of thing* the
goal is about - a thermostat's target temperature, a lamp's brightness - and the
concrete write target is resolved from the TDs discovered at runtime through the
Thing Directory. Two consequences follow, and both are the point:

* A Thing that is not in the directory makes the goal **unsupported**, reported
  as such. It is not approximated with the nearest device that happens to exist.
* A property the TD marks ``readOnly`` is refused before anything is attempted,
  because writing to a sensor is not a thing the agent should discover by
  failing.

Matching is on the property name the TD declares, with a small alias list for
the words people actually say. The alias list maps language to TD vocabulary and
nothing else - it never maps a goal to a URL, which is what would quietly turn
this back into the hardcoded skill table the review asked us to remove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeviceBinding:
    """One device goal, expressed in Thing Description vocabulary."""

    goal_state: str
    # Words that may name the Thing in a directory. The first one present wins,
    # so a renamed device is a one-line change here rather than a code change.
    thing_aliases: tuple[str, ...]
    property_aliases: tuple[str, ...]
    # Which goal parameter carries the value to write, and what to do when the
    # utterance did not include one.
    value_parameter: str = ""
    default_value: Any = None
    state_entity: str = ""
    state_attribute: str = ""
    description: str = ""

    def runtime_goal_state(self) -> str:
        """The goal as a predicate the runtime's condition evaluator can check."""
        return f"{self.state_entity}.{self.state_attribute} == true"

    def observed_fact(self, satisfied: bool) -> dict[str, dict[str, Any]]:
        return {self.state_entity: {self.state_attribute: satisfied}}

    def value_from(self, parameters: dict[str, Any]) -> Any:
        """The value this goal wants written, or None if the utterance gave none."""
        if self.value_parameter and self.value_parameter in parameters:
            return parameters[self.value_parameter]
        return self.default_value


@dataclass
class ResolvedDeviceTarget:
    """A concrete, writable property found in the discovered Things."""

    binding: DeviceBinding
    thing_id: str
    property: str
    href: str
    method: str
    value: Any
    read_href: str = ""
    read_method: str = "GET"
    # Which Things the directory offered, so a run can show that the target was
    # picked from what was discovered rather than from a list in the code.
    discovered_things: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_state": self.binding.goal_state,
            "thing_id": self.thing_id,
            "property": self.property,
            "write": {"href": self.href, "method": self.method},
            "read": {"href": self.read_href, "method": self.read_method},
            "value": self.value,
            "discovered_things": list(self.discovered_things),
            "resolved_from": "thing_directory",
        }


@dataclass
class DeviceResolutionError:
    """Why a device goal could not be attempted, in terms a reader can act on."""

    reason: str
    detail: str = ""
    discovered_things: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "discovered_things": list(self.discovered_things),
        }


DEVICE_BINDINGS: dict[str, DeviceBinding] = {
    "temperature_set": DeviceBinding(
        goal_state="temperature_set",
        thing_aliases=("thermostat", "hvac", "climate"),
        property_aliases=("targetTemperature", "setpoint", "target"),
        value_parameter="degrees",
        state_entity="thermostat",
        state_attribute="at_target",
        description="write the requested setpoint to whichever thermostat the directory reports",
    ),
    "lighting_set": DeviceBinding(
        goal_state="lighting_set",
        thing_aliases=("lights", "lamp", "lighting"),
        property_aliases=("brightness", "level", "dim"),
        value_parameter="percent",
        state_entity="lights",
        state_attribute="at_brightness",
        description="write the requested brightness to whichever lighting Thing the directory reports",
    ),
    "blinds_set": DeviceBinding(
        goal_state="blinds_set",
        thing_aliases=("blinds", "shades", "curtains"),
        property_aliases=("position", "openness"),
        value_parameter="percent",
        state_entity="blinds",
        state_attribute="at_position",
        description="write the requested position to whichever blinds Thing the directory reports",
    ),
    "projector_on": DeviceBinding(
        goal_state="projector_on",
        thing_aliases=("projector", "beamer", "display"),
        property_aliases=("power", "state"),
        default_value="on",
        state_entity="projector",
        state_attribute="powered_on",
        description="turn on whichever projector the directory reports",
    ),
    "projector_off": DeviceBinding(
        goal_state="projector_off",
        thing_aliases=("projector", "beamer", "display"),
        property_aliases=("power", "state"),
        default_value="off",
        state_entity="projector",
        state_attribute="powered_off",
        description="turn off whichever projector the directory reports",
    ),
}


def device_binding_for(goal_state: str) -> DeviceBinding | None:
    return DEVICE_BINDINGS.get(goal_state)


def _match(candidate: str, aliases: tuple[str, ...]) -> bool:
    lowered = candidate.lower()
    return any(alias in lowered or lowered in alias for alias in aliases)


def resolve_device_target(
    binding: DeviceBinding,
    models: list[Any],
    parameters: dict[str, Any],
) -> ResolvedDeviceTarget | DeviceResolutionError:
    """Find the writable property this goal needs among the discovered Things.

    ``models`` are ``ThingAffordanceModel`` objects parsed from the Thing
    Descriptions the directory returned. Nothing else is consulted: if the
    environment did not publish it, the goal is not attempted.
    """
    discovered = [str(getattr(model, "thing_id", "")) for model in models]

    value = binding.value_from(parameters)
    if value is None:
        return DeviceResolutionError(
            reason="no_value",
            detail=f"the request named no {binding.value_parameter or 'value'} to write",
            discovered_things=discovered,
        )

    candidates = [m for m in models if _match(str(getattr(m, "thing_id", "")), binding.thing_aliases)]
    if not candidates:
        return DeviceResolutionError(
            reason="thing_not_discovered",
            detail=f"no Thing matching {binding.thing_aliases} is in the directory",
            discovered_things=discovered,
        )

    read_only_hits: list[str] = []
    for model in candidates:
        sources = list(getattr(model, "state_sources", []) or [])
        for source in sources:
            if not _match(str(getattr(source, "property", "")), binding.property_aliases):
                continue
            if getattr(source, "read_only", False):
                read_only_hits.append(f"{source.thing_id}.{source.property}")
                continue
            reader = next(
                (
                    s
                    for s in sources
                    if str(getattr(s, "property", "")) == str(source.property) and getattr(s, "read_only", False)
                ),
                source,
            )
            return ResolvedDeviceTarget(
                binding=binding,
                thing_id=str(source.thing_id),
                property=str(source.property),
                href=str(source.href),
                method=str(source.method),
                value=value,
                read_href=str(reader.href),
                read_method="GET",
                discovered_things=discovered,
            )

    if read_only_hits:
        # Writing to a sensor is not something to discover by failing at it.
        return DeviceResolutionError(
            reason="property_read_only",
            detail=f"the matching property is declared read-only: {', '.join(read_only_hits)}",
            discovered_things=discovered,
        )
    return DeviceResolutionError(
        reason="property_not_discovered",
        detail=f"the discovered Thing declares no property matching {binding.property_aliases}",
        discovered_things=discovered,
    )


__all__ = [
    "DEVICE_BINDINGS",
    "DeviceBinding",
    "DeviceResolutionError",
    "ResolvedDeviceTarget",
    "device_binding_for",
    "resolve_device_target",
]
