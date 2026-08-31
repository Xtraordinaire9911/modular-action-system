"""One sentence, one episode, and all three of our components on the same path.

Until now each of us could show a demo, and three demos do not add up to a system.
This runs a single episode end to end and labels every stage with whose component
owns it, so what is on screen is the platform rather than three programs taking
turns.

    understand      action system      a sentence becomes a checkable goal
    reach           action system      the goal finds a device through WoT discovery
    act and verify  action system      write, then re-observe a DIFFERENT property
    fail            the room           a real fault, injected through the control plane
    recover         recovery           asked through PlannerPort, which Yixin owns
    restore         isolation          the room is left as it was found

Two rules this script holds itself to, because the whole argument depends on them:

* **The runtime decides.** The planner is asked for a plan and returns one. It does
  not execute anything. When it declines, that is reported as a handover and never
  counted as a success.
* **Nothing is claimed that did not happen.** Where a stage is carried by a stand-in
  rather than by the teammate's own code, the output says so on the line itself. A
  demo that implied more integration than exists would be the exact failure this
  project keeps finding in other people's success criteria.

The failure in the middle is not simulated at the wire. It is injected into the
servient, which then answers every request successfully and does not comply, so the
detection has to come from re-observing the device rather than from an error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.demos.device_panel import DevicePanel  # noqa: E402
from src.demos.pointer_overlay import (  # noqa: E402
    AGENT_COLOR,
    FAIL_COLOR,
    clear_pointer,
    point_at_selector,
)
from src.effectors.wot_executor import WotExecutor  # noqa: E402
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
from src.planner.environment_binding import device_view_for  # noqa: E402
from src.planner.intent_planner import IntentPlanner, available_client  # noqa: E402
from src.runtime.action_context import ActionContext, FailureContext  # noqa: E402
from src.runtime.device_goal import values_match  # noqa: E402

_LINE = "=" * 78
_RULE = "-" * 78

DEFAULT_CONTROL = "http://localhost:8081"
DEFAULT_DASHBOARD = "http://localhost:3000"
API_KEY = "demo"
TIMEOUT_S = 3.0

# Who owns each stage, printed beside it. The point of the demo is that these are
# three names and one path, so the names are data rather than prose.
OWNER = {
    "understand": "action system  (Rio)",
    "reach": "action system  (Rio)",
    "act": "action system  (Rio)",
    "fail": "smart room     (Rio)",
    "recover": "recovery       (Yixin: PlannerPort)",
    "restore": "isolation      (Fadi: episode provider)",
}


def _control(path: str, *, method: str = "GET", body: Any = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"X-API-Key": API_KEY}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - local demo
            raw = response.read().decode("utf-8").strip()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None


def stage(name: str, title: str) -> None:
    print(f"\n{_RULE}")
    print(f"  {name.upper():<9} {OWNER[name]:<34} {title}")
    print(_RULE)


def _point(session: Any, region: str, label: str, colour: str) -> None:
    if session is not None and region:
        point_at_selector(session, region, label=label, color=colour)


def open_dashboard(url: str, *, headed: bool) -> Any:
    """Optional viewing aid. The result of this script is the printed transcript."""
    if not headed:
        return None
    try:
        from src.perception.browser_session import BrowserSession

        session = BrowserSession.launch(url, headless=False)
    except Exception as exc:  # noqa: BLE001 - cosmetic
        print(f"  (no browser: {type(exc).__name__}; the transcript below is the result anyway)")
        return None
    time.sleep(1.8)
    return session


def build_failure_context(resolved: Any, wanted: Any, measured: Any) -> FailureContext:
    """Turn a non compliant device into the typed evidence the planner is given.

    The planner never sees a URL or a selector. It is told which affordance failed,
    what was expected, and where the boundary was, which is what lets the same port
    serve a browser failure and a device failure without knowing the difference.
    """
    return FailureContext(
        failed_action="write_property",
        failed_affordance_id=f"wot_{resolved.thing_id}_{resolved.property}",
        failed_entity_id=resolved.thing_title or resolved.thing_id,
        expected_effect=f"{resolved.property} == {wanted}",
        failure_boundary="environment",
        failure_type="action_had_no_effect",
        reason=(
            f"the write was accepted and {resolved.property} reads {wanted}, "
            f"but {resolved.measured_property} reads {measured}"
        ),
        transition_id="joint-0001",
        observation_state_id="joint-observation-0001",
    )


def ask_planner(failure: FailureContext, resolved: Any) -> tuple[Any, str]:
    """Ask a planner through the port, and report which planner answered.

    Falls back to the deterministic controller when no model is configured. Which
    one answered is printed, because "a model chose this" and "a table chose this"
    are different claims and the demo must not blur them.
    """
    from src.planner.model_recovery_planner import ModelRecoveryPlanner
    from src.runtime.cognitive_map import RuntimeAffordance

    client = available_client()
    planner = ModelRecoveryPlanner(client=client)
    affordance = RuntimeAffordance(
        id=f"wot_{resolved.thing_id}_{resolved.property}",
        source="wot",
        entity_id=resolved.thing_title or resolved.thing_id,
        action_name="write_property",
        action_type="write_property",
        confidence=1.0,
        grounding={"property": resolved.property},
    )
    context = ActionContext(
        task_id="joint-pipeline",
        request_type="recovery",
        state={},
        affordances=[affordance],
        unresolved_conflicts=[],
        allowed_actions=["click", "type", "scroll", "wait", "write_property"],
        safety_constraints=[],
        failure=failure,
    )
    plan = planner.plan(context, goal_id="joint", goal_state="temperature_set", parameters={})
    return plan, (getattr(client, "name", "") or "no model configured, deterministic controller")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--utterance", default="it's too cold in here, put it at 24 please")
    parser.add_argument("--directory", default=DEFAULT_DIRECTORY_URL)
    parser.add_argument("--control", default=DEFAULT_CONTROL)
    parser.add_argument("--dashboard", default=DEFAULT_DASHBOARD)
    parser.add_argument("--headless", dest="headed", action="store_false", default=True)
    parser.add_argument("--pace", type=float, default=1.0)
    parser.add_argument("--no-reset", dest="reset", action="store_false", default=True)
    args = parser.parse_args()

    def hold(seconds: float) -> None:
        time.sleep(seconds * args.pace)

    repo = Path(__file__).resolve().parents[1]
    out = repo / "artifacts" / "joint_pipeline"
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"at": datetime.now().isoformat(timespec="seconds"), "stages": {}}

    print(f"\n{_LINE}\n  ONE SENTENCE, ONE EPISODE, THREE COMPONENTS\n{_LINE}")
    if args.reset and _control(f"{args.control}/reset", method="POST")[0] != 200:
        print(f"  the control plane at {args.control} is not answering.")
        print("  start it with:  docker compose -f env/docker-compose.yml up -d\n")
        return 2
    print("  room reset   : yes, to its initial state")

    session = open_dashboard(args.dashboard, headed=args.headed)
    panel = DevicePanel(session) if session is not None else None
    if panel is not None:
        panel.open()
        panel.begin_act("ONE PIPELINE, THREE OWNERS", args.utterance, "each stage names whose component it is")

    try:
        # ── understand ────────────────────────────────────────────────────────
        stage("understand", "a sentence becomes a checkable goal")
        plan = IntentPlanner(client=available_client()).plan(args.utterance)
        if not plan.ok or plan.goal is None:
            print(f'  "{args.utterance}" produced no goal ({plan.source}); nothing is attempted\n')
            return 1
        print(f'  said       : "{args.utterance}"')
        print(f"  goal_state : {plan.goal.goal_state}")
        print(f"  parameters : {plan.goal.parameters}")
        print(f"  answered by: {plan.source}   (a model, or the labelled rule fallback)")
        report["stages"]["understand"] = plan.to_dict()
        hold(1.4)

        # ── reach ─────────────────────────────────────────────────────────────
        stage("reach", "the goal finds a device through W3C WoT discovery")
        try:
            client = ThingDirectoryClient(args.directory)
            tds = client.discover_tds()
            models = client.discover_models()
        except ThingDirectoryError as exc:
            print(f"  the directory is not answering: {exc}\n")
            return 2
        print(f"  GET {args.directory}/things  ->  {len(tds)} Thing Description(s)")
        print(f"  discovered : {', '.join(m.title or m.thing_id for m in models)}")

        binding = device_binding_for(plan.goal.goal_state)
        if binding is None:
            print(f"  {plan.goal.goal_state} is not a device goal; this demo needs one\n")
            return 1
        resolved = resolve_device_target(binding, models, plan.goal.parameters)
        if isinstance(resolved, DeviceResolutionError):
            print(f"  not attempted: {resolved.reason} - {resolved.detail}\n")
            return 1
        print(f"  binding    : {binding.thing_aliases} / {binding.property_aliases}")
        print("               no URL, no host, no port; a test asserts that")
        print(f"  resolved   : {resolved.thing_title}.{resolved.property} = {resolved.value}")
        print(f"  href       : {resolved.href}   (from the TD's forms, not from the code)")
        print(f"  measured   : {resolved.measured_property}   <- verification reads THIS")
        report["stages"]["reach"] = resolved.to_dict()
        view = device_view_for(plan.goal.goal_state)
        _point(
            session,
            view.region if view else "",
            f"{resolved.thing_title}.{resolved.property} <- {resolved.value}",
            AGENT_COLOR,
        )
        hold(1.6)

        # ── act and verify ────────────────────────────────────────────────────
        stage("act", "write, then re-observe a different property")
        executor = WotExecutor(tds)
        executor.write_state(resolved.source, resolved.value)
        commanded = executor.read_state(resolved.source)
        print(f"  wrote      : {resolved.property} = {resolved.value}")
        print(f"  reads back : {commanded}   (the property that was written)")

        arrived, waited, measurement = False, 0.0, None
        started = time.monotonic()
        while time.monotonic() - started < 8.0:
            measurement = executor.read_state(resolved.measured_source)
            if measurement is not None and values_match(resolved.value, measurement):
                arrived = True
                break
            time.sleep(0.25)
        waited = time.monotonic() - started
        print(f"  measured   : {resolved.measured_property} reads {measurement} after {waited:.1f}s")
        print(f"  verdict    : {'ARRIVED' if arrived else 'DID NOT ARRIVE'}")
        report["stages"]["act"] = {
            "commanded_read_back": commanded,
            "measured": measurement,
            "arrived": arrived,
            "waited_s": round(waited, 2),
        }
        hold(1.4)

        # ── fail ──────────────────────────────────────────────────────────────
        stage("fail", "a real fault, and every status code stays successful")
        status, _ = _control(f"{args.control}/failure", method="POST", body={"thing": "blinds", "type": "motor_jam"})
        print(f"  injected   : motor_jam on the blinds  (control plane answered {status})")
        blinds_binding = device_binding_for("blinds_set")
        # The single goal binding still reads `percent`; only the parts of the
        # composite goal carry per device names. Passing the composite's name here
        # resolved to nothing and reported "named no percent to write", which was
        # the resolver being right and this caller being wrong.
        blinds = resolve_device_target(blinds_binding, models, {blinds_binding.value_parameter: 40})
        if isinstance(blinds, DeviceResolutionError):
            print(f"  the room has no blinds to jam: {blinds.detail}\n")
            return 1
        executor.write_state(blinds.source, blinds.value)
        cmd = executor.read_state(blinds.source)
        meas = executor.read_state(blinds.measured_source)
        print(f"  wrote      : {blinds.property} = {blinds.value}")
        print(f"  reads back : {cmd}   <- exactly what was asked for")
        print(f"  measured   : {meas}   <- the blinds did not move")
        detected = not values_match(blinds.value, meas)
        print(f"  detection  : {'the goal is NOT met' if detected else 'no divergence seen'}")
        print("               nothing errored. Only re-observing a second property found this.")
        report["stages"]["fail"] = {"commanded": cmd, "measured": meas, "detected": detected}
        _point(session, "[data-testid='blinds-panel']", f"commanded {cmd}   measured {meas}", FAIL_COLOR)
        hold(1.8)

        # ── recover ───────────────────────────────────────────────────────────
        stage("recover", "asked through the port the runtime owns")
        failure = build_failure_context(blinds, blinds.value, meas)
        print(f"  evidence   : {failure.failure_type} at the {failure.failure_boundary} boundary")
        print(f"               {failure.reason}")
        proposal, answered_by = ask_planner(failure, blinds)
        print(f"  asked      : PlannerPort.plan(...)   answered by {answered_by}")
        print(f"  proposal   : {len(proposal.actions)} action(s), escalate={proposal.requires_escalation}")
        if proposal.reason:
            print(f"  reason     : {proposal.reason}")
        if proposal.requires_escalation or not proposal.actions:
            print("  outcome    : HANDOVER, reported as handled correctly and NOT as success.")
            print("               A jammed motor is not recoverable by retrying it, and a")
            print("               planner that invented a fix here would be the failure.")
        else:
            print("  outcome    : the runtime received a plan and remains the authority on")
            print("               whether to run it. It is not executed by the planner.")
        report["stages"]["recover"] = {
            "answered_by": answered_by,
            "actions": len(proposal.actions),
            "escalated": bool(proposal.requires_escalation),
            "reason": proposal.reason,
        }
        hold(1.8)

        # ── restore ───────────────────────────────────────────────────────────
        stage("restore", "the room is left as it was found")
        cleared = _control(f"{args.control}/reset", method="POST")[0] == 200
        after = executor.read_state(blinds.measured_source)
        print(f"  reset      : {'ok' if cleared else 'FAILED'}; fault cleared with it")
        print(f"  blinds     : measured reads {after}")
        print("  note       : this demo calls the control plane directly. The episode")
        print("               provider in src/isolation/episode.py is what does this")
        print("               inside a real episode, and it is covered by")
        print("               tests/test_smartroom_episode_isolation.py against this room.")
        report["stages"]["restore"] = {"reset_ok": cleared, "measured_after": after}

        if panel is not None:
            panel.conclude("one sentence, one episode, three components", kind="ok" if detected else "idle")
            hold(2.0)
            clear_pointer(session)
            hold(1.0)
    finally:
        if panel is not None:
            panel.close()
        if session is not None:
            session.close()
        if args.reset:
            _control(f"{args.control}/reset", method="POST")

    print(f"\n{_LINE}")
    print("  Every stage above named the component that owns it. The interfaces are")
    print("  real: PlannerPort is committed by Yixin, the episode provider by Fadi.")
    print("  Where this script stands in for a teammate's own runner, the line says so.")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"  artifact   : {(out / 'report.json').relative_to(repo)}\n{_LINE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
