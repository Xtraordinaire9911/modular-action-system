"""An utterance drives the production runtime, end to end.

    python scripts/run_intent_episode.py --utterance "add the wireless headphones to my cart"

The review's standing criticism of this repository is that it has components
rather than an integrated system, and the clearest instance was the intent
layer: `src/planner/intent_planner.py` turned a sentence into a GoalSpec, and
nothing in `src/runtime/` consumed it. The narrated demo used it, but that demo
runs its own loop, so the layer had never actually reached the real runtime.

This closes that gap and nothing else. The chain is:

    utterance
      -> IntentPlanner              a model if configured, a labelled rule
                                    fallback if not
      -> GoalSpec(source=           the runtime's own declared handoff point
           "user_intent_parser")
      -> EnvironmentBinding         which page, and which control completes it
      -> RuntimeEpisodeSpec(goal_spec=...)
      -> RuntimeEpisodeRunner.run_goal_episode
           -> ContinuousInteractionManager, on a live Playwright browser

Every step after the first is code that already existed and is unchanged. What
is new is that the first step is now wired to it, and that a run writes down
which parts were model-derived so the claim can be checked rather than believed.

Nothing here decides what to click. The binding table names a *family* of
controls; which member applies comes from the parameter the intent layer
extracted, and the runtime picks the affordance from its own observation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_agent_on_env import _start_static_server  # noqa: E402
from src.benchmarks.runtime_web_adapter import RuntimeWebEnvironmentAdapter  # noqa: E402
from src.benchmarks.task_spec import BenchmarkTask  # noqa: E402
from src.benchmarks.web_benchmark_adapter import WebBenchmarkAdapter  # noqa: E402
from src.planner.environment_binding import binding_for  # noqa: E402
from src.planner.goal_state_adapter import GoalStateReportingAdapter  # noqa: E402
from src.planner.intent_planner import IntentPlanner, available_client  # noqa: E402
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec  # noqa: E402

_LINE = "=" * 78


def interpret(utterance: str) -> Any:
    """Layer 1: the sentence becomes a GoalSpec, with its provenance attached."""
    return IntentPlanner(client=available_client()).plan(utterance)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--utterance",
        default="add the wireless headphones to my cart",
        help="What a person said. This is the only task input.",
    )
    parser.add_argument("--headed", dest="headed", action="store_true", default=False)
    parser.add_argument("--headless", dest="headed", action="store_false")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    repo = Path(__file__).resolve().parents[1]
    out = repo / "eval_outputs" / "intent_episode" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    print(f"\n{_LINE}\n  INTENT -> RUNTIME - one utterance through the real episode runner\n{_LINE}")
    print(f'  said: "{args.utterance}"\n')

    plan = interpret(args.utterance)
    print(f"  layer 1   : {plan.source} (confidence {plan.confidence:.2f})")
    if not plan.ok:
        print(f"  refused   : {plan.error or 'the utterance was not understood'}")
        print("\n  Nothing is attempted on an ungrounded goal. This is the correct outcome,")
        print("  not a failure: acting on a guess would be worse than declining.\n")
        return 2
    goal = plan.goal
    print(f"  goal_state: {goal.goal_state}")
    print(f"  parameters: {json.dumps(goal.parameters)}")
    print(f"  provenance: GoalSpec.source={goal.source!r}, model_derived={plan.is_model_derived}")

    binding = binding_for(goal.goal_state)
    if binding is None:
        print(f"\n  No environment in this repository can satisfy {goal.goal_state!r}.")
        print("  Reported rather than approximated with the nearest thing that exists.\n")
        return 3

    completion = binding.completion_for(goal.parameters)
    if not completion:
        print(f"\n  The goal named no {binding.subject_parameter!r} that this environment offers.")
        print(f"  parameters were: {json.dumps(goal.parameters)}\n")
        return 3

    runtime_parameters = binding.runtime_parameters(goal.parameters)
    runtime_goal_state = binding.runtime_goal_state()
    proof_text = binding.success_for(goal.parameters)
    proof_region = binding.success_region(goal.parameters)
    print(f"  environment: {binding.page}")
    print(f"  completes when the agent uses: {completion}")
    print(f"  proven by  : {proof_text!r} appearing in {proof_region}")
    print(f"  runtime plans to bind: {json.dumps(binding.bindings_for(goal.parameters))}")
    print(f"  checkable as: {runtime_goal_state}")

    from src.perception.browser_session import BrowserSession  # lazy: needs Playwright
    from src.perception.session_thread import SessionThread, ThreadedSession

    httpd, port = _start_static_server(str(repo / "env" / "mock_envs"))
    url = f"http://127.0.0.1:{port}/{binding.page}"
    # The browser gets its own thread. Playwright's sync API owns the event loop
    # of whatever thread uses it, so the async runtime cannot be started on that
    # thread - the reason run_agent_on_env.py --planner runtime has never run.
    worker = SessionThread(lambda: BrowserSession.launch(url, headless=not args.headed))
    session = ThreadedSession(worker)
    try:
        adapter = WebBenchmarkAdapter(session)

        # Checked against the region the goal names, never the whole page. The
        # product title is printed in the listing before anything is added, so a
        # body-text proxy reports the goal as met before the agent has acted.
        def goal_reached(_adapter: Any) -> bool:
            observed = (session.text_content(proof_region) or "").lower()
            return bool(observed) and proof_text.lower() in observed

        task = BenchmarkTask("mock_envs", goal.goal_id, url, goal.description, success_check=goal_reached)
        runtime_adapter = RuntimeWebEnvironmentAdapter(
            adapter,
            task,
            bindings=binding.bindings_for(goal.parameters),
            completions={completion},
            goal_id=goal.goal_id,
            goal_state=runtime_goal_state,
        )
        # The runtime verifies by resolving the goal predicate against what it
        # observed. Nothing in the web adapter reports the cart, so the lookup
        # misses and a goal that was reached is recorded as a postcondition
        # failure - a false negative, and the mirror of the false success this
        # project exists to prevent.
        observed_adapter = GoalStateReportingAdapter(
            runtime_adapter,
            fact=binding.observed_fact,
            holds=lambda: goal_reached(adapter),
        )
        print("\n  runtime   : RuntimeEpisodeRunner.run_goal_episode -> ContinuousInteractionManager")
        outcome = asyncio.run(
            RuntimeEpisodeRunner().run_goal_episode(
                observed_adapter,
                # This is the integration. The GoalSpec the runtime plans from
                # was derived from the sentence, not written by hand.
                RuntimeEpisodeSpec(
                    task_id=f"intent:{goal.goal_state}",
                    goal_id=goal.goal_id,
                    goal_state=runtime_goal_state,
                    parameters=runtime_parameters,
                    goal_spec=replace(goal, goal_state=runtime_goal_state, parameters=runtime_parameters),
                    data_source="intent_driven_runtime",
                ),
            )
        )
        session.screenshot(str(out / "final.png"))
        solved = adapter.is_solved(task)
    finally:
        session.close()
        httpd.shutdown()

    print(f"  state     : {outcome.result.state.value}")
    print(f"  verified  : {outcome.result.final_outcome_verified}")
    if outcome.result.reason:
        print(f"  reason    : {outcome.result.reason}")
    if outcome.result.plan_validation_errors:
        print(f"  plan errors: {outcome.result.plan_validation_errors}")
    if outcome.result.primitive_plan:
        print(f"  primitives : {len(outcome.result.primitive_plan)}")
    print(f"  episode   : {outcome.result.episode_id}")
    # The goal predicate as the runtime saw it change, which is the evidence
    # that verification re-observed rather than trusted the executor.
    trail = [a.value for a in outcome.cognitive_map.state_assertions if a.entity_id == binding.state_entity]
    if trail:
        print(f"  {runtime_goal_state.split(' ')[0]} observed: {' -> '.join(str(v) for v in trail)}")
    print(f"  transitions: {len(outcome.transition_ledger.records)}")
    for record in outcome.transition_ledger.records:
        mark = "ok " if record.success else "FAIL"
        detail = f" - {record.failure_reason}" if record.failure_reason else ""
        print(
            f"    [{mark}] step {record.step} {record.skill_id} via {record.backend} on {record.affordance_key}{detail}"
        )
    print(f"\n  result    : {'GOAL REACHED' if solved else 'not reached'}")

    record = {
        "utterance": args.utterance,
        "layer1": plan.to_dict(),
        "goal_spec": {
            "goal_id": goal.goal_id,
            "goal_state": goal.goal_state,
            "runtime_goal_state": runtime_goal_state,
            "parameters": dict(goal.parameters),
            "runtime_parameters": runtime_parameters,
            "source": goal.source,
        },
        "environment": {
            "page": binding.page,
            "completion": completion,
            "proof_region": proof_region,
            "proof_text": proof_text,
        },
        "runtime": {
            "entrypoint": "RuntimeEpisodeRunner.run_goal_episode",
            "state": outcome.result.state.value,
            "reason": outcome.result.reason,
            "plan_validation_errors": list(outcome.result.plan_validation_errors),
            "primitive_plan": len(outcome.result.primitive_plan),
            "verified": outcome.result.final_outcome_verified,
            "episode_id": outcome.result.episode_id,
            "transitions": [
                {
                    "step": r.step,
                    "skill_id": r.skill_id,
                    "backend": r.backend,
                    "affordance_key": r.affordance_key,
                    "success": r.success,
                    "postcondition_passed": r.postcondition_passed,
                    "failure_reason": r.failure_reason,
                    "recovery_action": r.recovery_action,
                    "recovery_tier": r.recovery_tier,
                }
                for r in outcome.transition_ledger.records
            ],
            "metrics": dict(outcome.metrics.values),
        },
        "goal_state_trail": [
            {"value": a.value, "source": a.source}
            for a in outcome.cognitive_map.state_assertions
            if a.entity_id == binding.state_entity
        ],
        "goal_reached": solved,
    }
    (out / "intent_episode.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\n  artifacts : {out.relative_to(repo)}\n{_LINE}\n")
    return 0 if solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
