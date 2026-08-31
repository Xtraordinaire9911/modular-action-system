"""Are these standard W3C WoT Thing Descriptions, and how does the agent find them?

That question was asked five times in the supervision meeting on 21 August and did
not get a clear answer, because the answers given were about the physics simulation
instead. The capability was already there; what was missing was a way to show it in
one screen. This is that screen.

It prints four things, and every one of them is read from the running room rather
than written here:

1. **Discovery.** What ``GET :8082/things`` returns, and how many Things came back.
   The agent asks a Thing Directory at runtime; nothing is compiled in.
2. **Conformance.** The ``@context``, ``@type`` and security definitions of a real
   TD, so a reader can check the document against the W3C specification instead of
   taking a claim about it.
3. **The affordance.** One property's complete ``forms`` array, with the ``op``
   values, ``readOnly`` and ``observable``. This is what the agent reads to decide
   where a write goes and whether it is allowed at all.
4. **Where the address came from.** The binding table entry beside the href that was
   resolved from it. The entry names a kind of Thing and a property; the URL is not
   in it. That is the difference between an agent that discovered a device and a
   demo that had the endpoint typed into it.

Read only, and safe to run at any time: it writes nothing and touches no device.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.perception.thing_directory import (  # noqa: E402
    DEFAULT_DIRECTORY_URL,
    ThingDirectoryClient,
    ThingDirectoryError,
)
from src.planner.device_binding import (  # noqa: E402
    DeviceResolutionError,
    device_binding_for,
    resolve_device_target,
)

_LINE = "=" * 78
_RULE = "-" * 78

# The specification the documents have to match. Compared rather than asserted:
# printing the context the room actually sent, next to the one the standard
# defines, is checkable by anyone in the room.
TD_CONTEXTS = (
    "https://www.w3.org/2022/wot/td/v1.1",
    "https://www.w3.org/2019/wot/td/v1",
)


def _fmt(value: Any) -> str:
    return json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value)


def show_discovery(client: ThingDirectoryClient, url: str) -> list[dict[str, Any]]:
    print(f"\n{_LINE}\n  1. DISCOVERY  -  the agent asks a directory, at run time\n{_LINE}")
    print(f"  GET {url}/things")
    tds = client.discover_tds()
    print(f"  -> {len(tds)} Thing Description(s)\n")
    # Widths measured from the data: a urn:uuid is 45 characters and ran into the
    # next column at 44, which on a projector reads as a broken tool.
    id_width = max(len("id"), *(len(str(td.get("id", ""))) for td in tds)) + 2
    print(f"  {'title':<13}{'id':<{id_width}}properties")
    print(f"  {_RULE[:74]}")
    for td in tds:
        props = ", ".join(sorted((td.get("properties") or {}).keys()))
        print(f"  {str(td.get('title', '')):<13}{str(td.get('id', '')):<{id_width}}{props}")
    print("\n  Nothing above is in the source. Remove a Thing from the room and it")
    print("  disappears from this list, and any goal that needed it becomes")
    print("  unsupported rather than being sent to a device that looks similar.")
    return tds


def show_conformance(td: dict[str, Any]) -> bool:
    print(f"\n{_LINE}\n  2. CONFORMANCE  -  is this the standard document, or our own format?\n{_LINE}")
    context = td.get("@context")
    flat = context if isinstance(context, list) else [context]
    ok = any(str(entry) in TD_CONTEXTS for entry in flat)
    print(f"  @context : {_fmt(context)}")
    print(f"  @type    : {td.get('@type')}")
    print(f"  id       : {td.get('id')}")
    print(f"  title    : {td.get('title')}")
    print(f"  security : {_fmt(td.get('security'))}")
    print(f"  securityDefinitions: {_fmt(td.get('securityDefinitions'))}")
    print()
    print(f"  W3C WoT Thing Description: {'YES' if ok else 'NO'}")
    if ok:
        print("  The context is the W3C one, so this document can be checked against the")
        print("  specification rather than against a description of it.")
    else:
        print("  The context is not a W3C one. This is a custom format and should be")
        print("  described as such rather than as a Thing Description.")
    return ok


def show_affordance(td: dict[str, Any]) -> None:
    print(f"\n{_LINE}\n  3. AFFORDANCE  -  what the agent reads to decide where a write goes\n{_LINE}")
    properties = td.get("properties") or {}
    # Prefer a writable property: a read only one would not show the op values
    # that matter, and those are the reason the agent can refuse before trying.
    writable = [(name, spec) for name, spec in properties.items() if not spec.get("readOnly", False)]
    chosen = (writable or list(properties.items()))[:1]
    for name, spec in chosen:
        print(f"  {td.get('title')}.{name}")
        print(f"    type      : {spec.get('type')}")
        for bound in ("minimum", "maximum", "enum"):
            if bound in spec:
                print(f"    {bound:<10}: {spec[bound]}")
        print(f"    readOnly  : {spec.get('readOnly')}")
        print(f"    observable: {spec.get('observable')}")
        print("    forms     :")
        for form in spec.get("forms") or []:
            print(f"      href        : {form.get('href')}")
            print(f"      contentType : {form.get('contentType')}")
            print(f"      op          : {form.get('op')}")
            print()

    read_only = [n for n, s in properties.items() if s.get("readOnly")]
    if read_only:
        print(f"  read only in this Thing: {', '.join(read_only)}")
        print("  A goal that would write one of these is refused before any request is")
        print("  sent, because the TD says it cannot be written.")


def show_provenance(models: list[Any], goal_state: str, parameters: dict[str, Any]) -> None:
    print(f"\n{_LINE}\n  4. PROVENANCE  -  the address is not in the code\n{_LINE}")
    binding = device_binding_for(goal_state)
    if binding is None:
        print(f"  no device binding for {goal_state}")
        return

    print(f"  The binding table entry for {goal_state!r}, in full:\n")
    print(f"    thing_aliases            = {binding.thing_aliases}")
    print(f"    property_aliases         = {binding.property_aliases}")
    print(f"    measured_property_aliases= {binding.measured_property_aliases}")
    print(f"    value_parameter          = {binding.value_parameter!r}")
    print("\n  There is no URL, no host and no port in it. It names a kind of Thing and")
    print("  a kind of property, and that is all the code is allowed to know.\n")

    resolved = resolve_device_target(binding, models, parameters)
    if isinstance(resolved, DeviceResolutionError):
        print(f"  resolution failed: {resolved.reason} - {resolved.detail}")
        return

    print(f"  Resolved against the discovered Thing Descriptions with {parameters}:\n")
    print(f"    thing        : {resolved.thing_title or resolved.thing_id}")
    print(f"    property     : {resolved.property}")
    print(f"    value        : {resolved.value}")
    print(f"    read back    : {resolved.read_method} {resolved.read_href}")
    # The write is a separate affordance with its own op, and its method comes
    # from the form: htv:methodName when the TD states one, otherwise the default
    # binding for writeproperty, which is PUT. Labelling the state source's
    # method as "write" printed GET next to a write target, which was simply
    # wrong, so the affordance the executor will actually use is looked up here
    # the same way the executor looks it up.
    writer = next(
        (
            a
            for model in models
            for a in getattr(model, "affordances", [])
            if getattr(a, "id", "") == f"wot_{resolved.thing_id}_{resolved.property}"
            and getattr(a, "action", "") == "write_property"
        ),
        None,
    )
    if writer is not None:
        locator = getattr(writer, "locator", {}) or {}
        print(f"    write        : {locator.get('method')} {locator.get('href')}")
    else:
        print("    write        : no writeproperty form in the TD for this property")
    if resolved.measured_property:
        print(f"    measured     : {resolved.measured_property}")
        print("                   verification reads THIS, not the property it wrote")
    else:
        print("    measured     : none published; the setpoint read back is all the")
        print("                   evidence there is, and a caller must not imply more")
    print("\n  That href came out of the TD's forms array. It is the same string printed")
    print("  in section 3 above, which is the point: the room decided the address, and")
    print("  the agent found it.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--directory", default=DEFAULT_DIRECTORY_URL, help="Thing Directory base URL.")
    parser.add_argument("--thing", default="thermostat", help="Which discovered Thing to show in detail.")
    parser.add_argument(
        "--goal",
        default="temperature_set",
        help="Which device goal to resolve, to show that the address is not in the code.",
    )
    args = parser.parse_args()

    print(f"\n{_LINE}\n  STANDARD W3C WOT THING DESCRIPTIONS, AND HOW THE AGENT FINDS THEM\n{_LINE}")
    print("  Every value below is read from the running room. Nothing is quoted from")
    print("  a document or restated from memory.")

    try:
        client = ThingDirectoryClient(args.directory)
        tds = show_discovery(client, args.directory)
        models = client.discover_models()
    except ThingDirectoryError as exc:
        print(f"\n  the directory is not answering: {exc}")
        print("  start it with:  docker compose -f env/docker-compose.yml up -d\n")
        return 2

    if not tds:
        print("\n  the directory answered but reported no Things\n")
        return 1

    chosen = next((td for td in tds if td.get("title") == args.thing), tds[0])
    conformant = show_conformance(chosen)
    show_affordance(chosen)
    show_provenance(models, args.goal, {"degrees": 22})

    print(f"\n{_LINE}")
    print("  Summary, in one line each:")
    print(f"    standard document      : {'yes, W3C WoT TD' if conformant else 'NO'}")
    print(f"    discovered at run time : yes, from {args.directory}/things")
    print("    address in the code    : none; it comes from the TD's forms")
    print("    write permission       : taken from readOnly in the TD")
    print(f"{_LINE}\n")
    return 0 if conformant else 1


if __name__ == "__main__":
    raise SystemExit(main())
