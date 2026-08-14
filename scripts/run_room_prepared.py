"""Prepare a room from an utterance, against the real servient, and prove it.

    docker compose -f env/docker-compose.yml up -d
    python scripts/run_room_prepared.py
    python scripts/run_room_prepared.py --utterance "get the room ready, lights at 15"

Every step here refuses to use a fact that is not in evidence:

1. **discovery** - the Thing Descriptions come from the runtime directory at
   :8082. Nothing in this script or in the bindings names a device endpoint, so
   a device added to or removed from the room changes what the agent can do with
   no code edit. The run prints what was discovered.
2. **interpretation** - the sentence goes to the model when one is configured,
   and to the labelled phrasing fallback when not. Which one answered is
   recorded, never blurred.
3. **resolution** - "prepare the room" resolves to several writable properties.
   A Thing the directory did not offer is reported, not approximated with the
   nearest device that happens to exist.
4. **verification** - each property is read back after it is written, and the
   goal is met only where the value that comes back is the value asked for. The
   servient answers 204 to a write that changed nothing; that is exactly the
   failure this step exists to catch, and ``--ignore`` reproduces it on purpose.

The artifact holds all four, so the claim "the room was prepared" can be checked
rather than believed.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.effectors.wot_executor import WotExecutor  # noqa: E402
from src.perception.td_affordance_parser import TdAffordanceParser  # noqa: E402
from src.perception.thing_directory import (  # noqa: E402
    DEFAULT_DIRECTORY_URL,
    ThingDirectoryClient,
    ThingDirectoryError,
)
from src.planner.device_binding import composite_goal_for  # noqa: E402
from src.planner.intent_planner import IntentPlanner, available_client  # noqa: E402
from src.runtime.device_goal import pursue_composite_goal  # noqa: E402

_LINE = "=" * 78


def reset_room(control_url: str = "http://localhost:8081/reset") -> str:
    """Put the room back to its initial state, and say whether that worked.

    Without this the run is not verifiable: the previous run left the lights at
    30, so a write that is dropped still reads back 30 and a broken write looks
    confirmed. That is the exact mistake this script exists to catch, so it must
    not be able to make it.
    """
    request = urllib.request.Request(
        control_url, method="POST", data=b"{}", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - local demo endpoint
            json.loads(response.read().decode("utf-8"))
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def advertised_base(tds: list[dict[str, Any]]) -> str:
    """The scheme://host:port the TDs tell a client to use, or empty if none do."""
    for td in tds:
        for prop in (td.get("properties") or {}).values():
            for form in prop.get("forms") or []:
                href = str(form.get("href", ""))
                if href.startswith("http"):
                    parts = urllib.parse.urlsplit(href)
                    return f"{parts.scheme}://{parts.netloc}"
    return ""


def reachable(base: str, timeout_s: float = 1.5) -> bool:
    """Whether anything answers at ``base``, without caring what it answers."""
    parts = urllib.parse.urlsplit(base)
    host, port = parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def rewrite_base(tds: list[dict[str, Any]], old: str, new: str) -> list[dict[str, Any]]:
    """Replace the advertised base in every form href.

    A whole-text substitution on purpose: it rewrites exactly the string that was
    found to be unreachable and nothing else, which is easy to audit in the
    artifact. Anything cleverer would be harder to trust than the problem is.
    """
    return list(json.loads(json.dumps(tds).replace(old, new)))


class _IgnoringExecutor:
    """A servient that accepts a write to one property and changes nothing.

    Not a mock of the room - the real executor still does the work. This wraps it
    to drop one write, which is how the run can demonstrate that the read-back is
    load-bearing instead of asserting that it would be.
    """

    def __init__(self, inner: Any, ignore: str, titles: dict[str, str] | None = None) -> None:
        self._inner = inner
        self._ignore = ignore.strip().lower()
        # The directory identifies Things by UUID, so "lights.brightness" on the
        # command line has to be matched against the title as well. Without this
        # the flag silently matched nothing and the fault demonstration passed,
        # which is the failure mode this whole script is about.
        self._titles = titles or {}

    def _names(self, source: Any) -> set[str]:
        title = self._titles.get(str(source.thing_id), "")
        return {f"{n}.{source.property}".lower() for n in (str(source.thing_id), title) if n}

    def write_state(self, source: Any, value: Any) -> None:
        if self._ignore in self._names(source):
            return
        self._inner.write_state(source, value)

    def read_state(self, source: Any) -> Any:
        return self._inner.read_state(source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--utterance", default="prepare the room for my presentation")
    parser.add_argument("--directory", default=DEFAULT_DIRECTORY_URL, help="Thing Directory base URL.")
    parser.add_argument(
        "--ignore",
        default="",
        metavar="thing.property",
        help="Silently drop this write, to show the read-back catching it (e.g. lights.brightness).",
    )
    parser.add_argument(
        "--no-reset",
        dest="reset",
        action="store_false",
        default=True,
        help="Keep whatever the last run left in the room instead of resetting first.",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    out = repo / "artifacts" / "room_prepared"
    out.mkdir(parents=True, exist_ok=True)

    print(f"\n{_LINE}\n  PREPARE A ROOM, THEN CHECK EVERY PROPERTY\n{_LINE}")

    reset_error = reset_room() if args.reset else "skipped by --no-reset"
    if args.reset:
        print(f"  room reset   : {'yes, to its initial state' if not reset_error else f'FAILED - {reset_error}'}")

    # 1. discovery
    try:
        client = ThingDirectoryClient(args.directory)
        tds = client.discover_tds()
        models = client.discover_models()
    except ThingDirectoryError as exc:
        print(f"  the directory is not answering: {exc}")
        print("  start it with:  docker compose -f env/docker-compose.yml up -d\n")
        return 2
    print(f"  discovered   : {', '.join(m.title or m.thing_id for m in models)}  (from {args.directory}/things)")

    # The servient advertises the address it sees itself on. Inside compose that is
    # the container's bridge IP, which nothing outside the network can reach, so
    # every write from the host times out while discovery looks perfectly healthy.
    # Adapting is reported rather than done quietly: the environment is what needs
    # fixing, and a run that hid this would make it look fixed.
    rewrite: dict[str, str] = {}
    base = advertised_base(tds)
    if base and not reachable(base):
        target = f"{urllib.parse.urlsplit(args.directory).scheme or 'http'}://{urllib.parse.urlsplit(args.directory).hostname}:{urllib.parse.urlsplit(base).port or 8080}"
        print(f"  note         : the TDs advertise {base}, which is not reachable from here.")
        print(f"                 rewriting to {target} for this run; the servient should advertise")
        print("                 a reachable base (node-wot HttpServer baseUri).")
        tds = rewrite_base(tds, base, target)
        parser_ = TdAffordanceParser()
        models = [parser_.parse(td) for td in tds]
        rewrite = {"advertised": base, "used": target}

    # 2. interpretation
    client_model = available_client()
    plan = IntentPlanner(client=client_model).plan(args.utterance)
    if not plan.ok or plan.goal is None:
        print(f'  "{args.utterance}" produced no goal ({plan.source}); nothing is attempted\n')
        return 1
    print(f'  said         : "{args.utterance}"')
    print(f"  understood   : {plan.goal.goal_state} {plan.goal.parameters}  (by {plan.source})")

    goal = composite_goal_for(plan.goal.goal_state)
    if goal is None:
        print(f"  {plan.goal.goal_state} is not a composite device goal; use run_intent_episode.py\n")
        return 1

    # 3 and 4. resolution, writing, and reading each property back
    executor: Any = WotExecutor(tds)
    if args.ignore:
        executor = _IgnoringExecutor(executor, args.ignore, {m.thing_id: m.title for m in models})
        print(f"  fault        : writes to {args.ignore} will be dropped after being accepted")

    outcome = pursue_composite_goal(goal, models, plan.goal.parameters, executor)

    print(f"\n  {'part':<16}{'thing.property':<32}{'wanted':>10}{'read back':>12}  verified")
    print(f"  {'-' * 74}")
    for part in outcome.parts:
        # The title, not the UUID: the id is in the artifact, and a table nobody
        # can read is a table nobody checks.
        where = f"{part.thing_title or part.thing_id}.{part.property}" if part.thing_id else "-"
        wanted = "-" if part.wanted is None else str(part.wanted)
        observed = "-" if part.observed is None else str(part.observed)
        mark = "yes" if part.verified else ("skipped" if part.skipped_reason else "NO")
        print(f"  {part.goal_state:<16}{where:<32}{wanted:>10}{observed:>12}  {mark}")
        if part.error:
            print(f"  {'':<16}{part.error}")

    print(f"\n  goal         : {'PREPARED' if outcome.verified else 'NOT PREPARED'}")
    print(f"  why          : {outcome.summary()}")

    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "utterance": args.utterance,
        "intent": plan.to_dict(),
        "directory_url": args.directory,
        "discovered_things": [{"id": m.thing_id, "title": m.title} for m in models],
        "href_rewrite": rewrite,
        "reset_before_run": {"requested": args.reset, "error": reset_error},
        "injected_fault": args.ignore,
        "outcome": outcome.to_dict(),
    }
    (out / "room_prepared_report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  artifact     : {(out / 'room_prepared_report.json').relative_to(repo)}\n{_LINE}\n")
    return 0 if outcome.verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
