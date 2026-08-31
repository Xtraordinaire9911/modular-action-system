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
    # The property that reports what the device has actually *reached*, when the
    # device has one. Writing a setpoint and reading it back confirms the device
    # was told; it does not confirm the room did it, and for anything with mass
    # those are different facts separated by real time. A goal is met only when
    # this one arrives.
    #
    # Left empty where the distinction would be invented rather than modelled: a
    # dimmer reaches its level in milliseconds, so `lighting_set` has no measured
    # property and its write is its own confirmation.
    measured_property_aliases: tuple[str, ...] = ()
    # What the measurement should read once the command has taken effect, when
    # that differs from the commanded value itself. A projector told power="on"
    # has arrived when its lamp reads "on", not when its switch does.
    measured_value: Any = None
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
    # The directory identifies a Thing by a UUID and names it in `title`. The id
    # is what the executor addresses; the title is the only part a person reading
    # a report can recognise, so both are kept.
    thing_title: str = ""
    read_href: str = ""
    read_method: str = "GET"
    # Where to read what the device has actually reached, when it reports that
    # separately, and what that reading should say once the goal is met. Empty
    # when the device has no such property, in which case reading the setpoint
    # back is the whole of the available evidence and a caller should not
    # pretend otherwise.
    measured_source: Any = None
    measured_property: str = ""
    measured_value: Any = None
    # The discovered source this resolved to, kept so a caller can write and read
    # back through the executor's own API instead of re-deriving the endpoint from
    # the strings above. Two derivations of one address is one too many.
    source: Any = None
    # Which Things the directory offered, so a run can show that the target was
    # picked from what was discovered rather than from a list in the code.
    discovered_things: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_state": self.binding.goal_state,
            "thing_id": self.thing_id,
            "thing_title": self.thing_title,
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
        # A room has mass. The setpoint is accepted at once and the temperature
        # arrives minutes later, so this is the property the goal is actually
        # about.
        measured_property_aliases=("currentTemperature", "temperature", "measured"),
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
        # A motor takes time to travel, and can stop short of where it was sent.
        measured_property_aliases=("measuredPosition", "actualPosition"),
        value_parameter="percent",
        state_entity="blinds",
        state_attribute="at_position",
        description="write the requested position to whichever blinds Thing the directory reports",
    ),
    "projector_on": DeviceBinding(
        goal_state="projector_on",
        thing_aliases=("projector", "beamer", "display"),
        property_aliases=("power", "state"),
        # The switch and the lamp are different facts. A lamp needs to strike and
        # warm before the room has an image, and a dead one reports the switch
        # thrown for as long as you care to ask.
        measured_property_aliases=("lamp", "lampState"),
        measured_value="on",
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
    # An empty candidate matches nothing. Without this guard "" is a substring of
    # every alias, so one TD with no title would resolve every device goal to it.
    lowered = candidate.strip().lower()
    if not lowered:
        return False
    # Both sides are lowered. This used to lower only the candidate, which meant
    # every alias containing a capital letter was dead: "measuredPosition" never
    # matched the property measuredPosition, and "currentTemperature" never matched
    # currentTemperature. Resolution still appeared to work because the short
    # lowercase aliases beside them ("target", "temperature") matched instead - and
    # "temperature" matches targetTemperature just as well as currentTemperature,
    # so the measured reading resolved to the setpoint. A dead alias is worse than
    # a missing one: it looks like coverage.
    return any(alias.strip().lower() in lowered or lowered in alias.strip().lower() for alias in aliases)


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

    # Match on the title as well as the id. Found by running this against the real
    # servient: its TDs identify Things by "urn:uuid:f3c4..." and carry the human
    # name in `title`, so matching the id alone resolved nothing at all in the live
    # room while passing every test that used friendly ids. Both are TD vocabulary;
    # neither is an endpoint.
    candidates = [
        m
        for m in models
        if _match(str(getattr(m, "thing_id", "")), binding.thing_aliases)
        or _match(str(getattr(m, "title", "")), binding.thing_aliases)
    ]
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
            # What the device reports having reached, if it reports that at all.
            # Resolved from the same discovered sources, so a Thing that does not
            # publish one simply has no measurement and the caller can say so
            # rather than inventing a second reading of the setpoint.
            #
            # The property being written is excluded, and that guard is load
            # bearing rather than defensive. `_match` is substring based, so
            # "temperature" matches "targetTemperature" as readily as
            # "currentTemperature", and without this the measured source resolved
            # to the setpoint itself. Every caller then read back the value it had
            # just written, arrival was instant, and the one distinction this
            # project rests on was silently gone - inside the demo that exists to
            # show it. A measured counterpart is by definition a different
            # property, so say that here instead of trusting the alias lists to
            # stay disjoint.
            measured = next(
                (
                    s
                    for s in sources
                    if str(getattr(s, "property", "")) != str(source.property)
                    and _match(str(getattr(s, "property", "")), binding.measured_property_aliases)
                ),
                None,
            )
            return ResolvedDeviceTarget(
                binding=binding,
                thing_id=str(source.thing_id),
                thing_title=str(getattr(model, "title", "") or ""),
                property=str(source.property),
                href=str(source.href),
                method=str(source.method),
                value=value,
                read_href=str(reader.href),
                read_method="GET",
                discovered_things=discovered,
                source=source,
                measured_source=measured,
                measured_property=str(getattr(measured, "property", "")) if measured else "",
                measured_value=binding.measured_value if binding.measured_value is not None else value,
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


# --- composite goals ------------------------------------------------------------
# "prepare the room" is not one write. It is several, each to a different Thing,
# and it is only met when every one of them is separately confirmed by reading
# the property back. Declaring it as a list of the single-device goals keeps one
# resolution path: a composite part cannot reach a device the corresponding
# single goal could not.


@dataclass(frozen=True)
class CompositePart:
    """One write a composite goal needs, and whether the goal fails without it."""

    goal_state: str  # a key in DEVICE_BINDINGS
    value: Any  # what "prepared" means for this property
    # A room with no blinds is still a room that can be prepared. A room with no
    # projector is not, for this goal. Optional parts that the directory does not
    # offer are reported as skipped - never dropped quietly, because a goal that
    # silently did less than it claimed is the failure this project is about.
    required: bool = True
    # Every parameter name that carries this part's value, in the order they are
    # tried. A tuple rather than one name because the layer that fills the dict is
    # a model: asked for a temperature it answers "temperature" as readily as
    # "degrees", and a part that recognised only "degrees" fell through to its
    # default while the run printed the number it had understood. The write was
    # confirmed, the report said PREPARED, and 22 had become 21 with nothing
    # anywhere saying so.
    #
    # The sets have to stay disjoint across parts, and there is a test for it.
    # Every part reads one shared dict, so a name two parts accept is a name that
    # cannot carry two values: with both lights and blinds on "percent", "blinds
    # at 50, lights at 15" was not an expressible request. "percent" is kept for
    # lighting alone, because the rule fallback and the script's own examples
    # already write it; blinds gives it up rather than the two sharing it.
    value_parameters: tuple[str, ...] = ()

    def consumed_parameter(self, parameters: dict[str, Any]) -> str:
        """Which name in ``parameters`` supplied this part's value, or empty.

        Returned separately from the value so a caller can tell a request that
        was carried out from one that was merely understood.
        """
        return next((name for name in self.value_parameters if parameters.get(name) is not None), "")

    def value_from(self, parameters: dict[str, Any]) -> Any:
        name = self.consumed_parameter(parameters)
        return parameters[name] if name else self.value


@dataclass(frozen=True)
class CompositeDeviceGoal:
    goal_state: str
    parts: tuple[CompositePart, ...]
    description: str = ""
    # Parameters that say what the goal is about rather than what to write. The
    # intent prompt requires a "target" on every goal, so counting these as
    # ignored would fire the warning on every run ever made, and a warning that
    # always fires is one nobody reads by the second week.
    context_parameters: tuple[str, ...] = ("target", "room", "time")

    def unconsumed_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """The values this request named that no part of this goal will write.

        This is the same divergence as a write that returns 204 and changes
        nothing, moved one layer earlier: the sentence asked for something, the
        agent parsed it, and then nothing was done about it. Returned so the
        caller can say so out loud instead of the value disappearing between the
        parse and the table.
        """
        consumed = {part.consumed_parameter(parameters) for part in self.parts}
        consumed.discard("")
        consumed.update(self.context_parameters)
        # A key whose value is None named nothing, so reporting it as ignored
        # would be a false alarm rather than a caught one.
        return {name: value for name, value in parameters.items() if name not in consumed and value is not None}


COMPOSITE_GOALS: dict[str, CompositeDeviceGoal] = {
    "room_prepared": CompositeDeviceGoal(
        goal_state="room_prepared",
        parts=(
            CompositePart(goal_state="projector_on", value="on"),
            CompositePart(
                goal_state="lighting_set",
                value=30,
                # "lights" and "light" are here because the model actually emits
                # them. The prompt now names `lighting` for this part, so these
                # are a net rather than the mechanism: a model that answers with
                # a reasonable synonym should be understood, not reported as
                # having named something nobody writes.
                value_parameters=("lighting", "lights", "light", "brightness", "lights_percent", "percent"),
            ),
            CompositePart(
                goal_state="blinds_set",
                value=20,
                required=False,
                # No "percent" here: see CompositePart.value_parameters. Two parts
                # sharing it is what made one of the two percentages in a sentence
                # unsayable.
                value_parameters=("blinds", "blinds_percent", "position"),
            ),
            CompositePart(
                goal_state="temperature_set",
                value=21,
                required=False,
                value_parameters=("degrees", "temperature", "temp"),
            ),
        ),
        description="projector on and lights down for a presentation, blinds and temperature if the room has them",
    ),
}


def composite_goal_for(goal_state: str) -> CompositeDeviceGoal | None:
    return COMPOSITE_GOALS.get(goal_state)


def resolve_composite_goal(
    goal: CompositeDeviceGoal,
    models: list[Any],
    parameters: dict[str, Any],
) -> list[tuple[CompositePart, ResolvedDeviceTarget | DeviceResolutionError]]:
    """Resolve every part against the discovered Things, keeping the failures.

    The failures are returned rather than raised because which part could not be
    reached is the interesting half of the answer: "prepared the room except the
    blinds, which this room does not have" is a different report from "prepared
    the room".
    """
    resolved: list[tuple[CompositePart, ResolvedDeviceTarget | DeviceResolutionError]] = []
    for part in goal.parts:
        binding = device_binding_for(part.goal_state)
        if binding is None:
            resolved.append(
                (part, DeviceResolutionError(reason="no_binding", detail=f"no binding for {part.goal_state}"))
            )
            continue
        value = part.value_from(parameters)
        resolved.append((part, resolve_device_target(binding, models, {binding.value_parameter or "value": value})))
    return resolved


__all__ = [
    "COMPOSITE_GOALS",
    "DEVICE_BINDINGS",
    "CompositeDeviceGoal",
    "CompositePart",
    "DeviceBinding",
    "DeviceResolutionError",
    "ResolvedDeviceTarget",
    "composite_goal_for",
    "device_binding_for",
    "resolve_composite_goal",
    "resolve_device_target",
]
