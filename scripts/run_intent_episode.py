"""An utterance drives the production runtime, end to end.

    python scripts/run_intent_episode.py --utterance "add the wireless headphones to my cart"
    python scripts/run_intent_episode.py --suite          # every utterance, both environments

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
is new is that the first step is wired to it, and that a run writes down which
parts were model-derived so the claim can be checked rather than believed.

Nothing here decides what to click. The binding table names a *family* of
controls; which member applies comes from the parameter the intent layer
extracted, and the runtime picks the affordance from its own observation.

``--suite`` runs the whole utterance set across both environments and writes the
cross-environment generalisation report (M1). That report had been definable
since the project began and had never been produced from a real run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.cross_env_eval import aggregate as aggregate_m1  # noqa: E402
from scripts.run_agent_on_env import _start_static_server  # noqa: E402
from src.benchmarks.runtime_web_adapter import RuntimeWebEnvironmentAdapter  # noqa: E402
from src.benchmarks.task_spec import BenchmarkRunResult, BenchmarkTask  # noqa: E402
from src.benchmarks.web_benchmark_adapter import WebBenchmarkAdapter  # noqa: E402
from src.planner.environment_binding import binding_for  # noqa: E402
from src.planner.goal_state_adapter import GoalStateReportingAdapter  # noqa: E402
from src.planner.intent_planner import IntentPlanner, available_client  # noqa: E402
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec  # noqa: E402

_LINE = "=" * 78

# Phrased the way someone would actually ask, across both surfaces, including
# one request nothing here can satisfy. A suite where every item succeeds tests
# the happy path only.
SUITE: tuple[str, ...] = (
    "add the wireless headphones to my cart",
    "put the pro laptop in my cart",
    "add the mechanical keyboard to my cart please",
    "add the 4k monitor to my cart",
    "upvote the top post",
    "upvote the browser automation post",
    "make me a sandwich",
)


@dataclass
class Episode:
    """One utterance, and everything that happened to it."""

    utterance: str
    outcome: str  # reached | not_reached | refused_intent | unsupported_goal
    env: str = ""
    goal_state: str = ""
    runtime_goal_state: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    model_derived: bool = False
    completion: str = ""
    runtime_state: str = ""
    verified: bool = False
    reason: str = ""
    episode_id: str = ""
    transitions: list[dict[str, Any]] = field(default_factory=list)
    goal_state_trail: list[dict[str, Any]] = field(default_factory=list)
    primitive_plan: list[dict[str, Any]] = field(default_factory=list)
    backends_used: list[str] = field(default_factory=list)
    final_oracle: dict[str, Any] = field(default_factory=dict)
    # What the vision model was asked, what it said, and whether its answer was
    # used. Kept even when unusable, so a run can show that a model abstained
    # rather than leaving a reader to assume one was never consulted.
    visual_evidence: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0

    @property
    def reached(self) -> bool:
        return self.outcome == "reached"

    @property
    def attempted(self) -> bool:
        """Whether a browser episode was run at all."""
        return self.outcome in {"reached", "not_reached"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "utterance": self.utterance,
            "outcome": self.outcome,
            "env": self.env,
            "goal_state": self.goal_state,
            "runtime_goal_state": self.runtime_goal_state,
            "parameters": self.parameters,
            "layer1_source": self.source,
            "model_derived": self.model_derived,
            "completion": self.completion,
            "runtime_state": self.runtime_state,
            "verified": self.verified,
            "reason": self.reason,
            "episode_id": self.episode_id,
            "transitions": self.transitions,
            "goal_state_trail": self.goal_state_trail,
            "primitive_plan": self.primitive_plan,
            "backends_used": self.backends_used,
            "final_oracle": self.final_oracle,
            "visual_evidence": self.visual_evidence,
            "latency_ms": round(self.latency_ms, 1),
        }


def interpret(utterance: str) -> Any:
    """Layer 1: the sentence becomes a GoalSpec, with its provenance attached."""
    return IntentPlanner(client=available_client()).plan(utterance)


def run_episode(
    utterance: str,
    *,
    repo: Path,
    headed: bool,
    verbose: bool = True,
    vision_client: Any | None = None,
) -> Episode:
    """Take one utterance all the way through the runtime and report what happened.

    ``vision_client`` is injectable so CI can exercise the visual path against a
    fake. The Friday demo passes nothing and gets the configured model, or the
    labelled abstention if none is configured.
    """
    say = print if verbose else (lambda *a, **k: None)

    plan = interpret(utterance)
    say(f"  layer 1   : {plan.source} (confidence {plan.confidence:.2f})")
    if not plan.ok:
        say(f"  refused   : {plan.error or 'the utterance was not understood'}")
        say("  Nothing is attempted on an ungrounded goal: acting on a guess would be worse.")
        return Episode(utterance=utterance, outcome="refused_intent", source=plan.source)

    goal = plan.goal
    say(f"  goal_state: {goal.goal_state}   parameters: {json.dumps(goal.parameters)}")
    say(f"  provenance: GoalSpec.source={goal.source!r}, model_derived={plan.is_model_derived}")

    binding = binding_for(goal.goal_state)
    if binding is None:
        say(f"  unsupported: no environment here can satisfy {goal.goal_state!r}")
        return Episode(utterance=utterance, outcome="unsupported_goal", goal_state=goal.goal_state, source=plan.source)

    if binding.surface != "mock_env":
        # This runner serves the mock environments from its own static server on
        # a free port. A goal that lives on the smart-room dashboard needs the
        # Docker services instead, so it is declined by name rather than fetched
        # as a missing file and reported as a missing control.
        say(f"  unsupported here: {goal.goal_state!r} lives on the {binding.surface}, which this runner does not serve")
        say("  run it with:  python scripts/run_llm_demo.py   (smart-room dashboard, needs docker compose up)")
        return Episode(
            utterance=utterance,
            outcome="unsupported_goal",
            env=binding.surface,
            goal_state=goal.goal_state,
            parameters=dict(goal.parameters),
            source=plan.source,
        )

    completion = binding.completion_for(goal.parameters)
    if not completion:
        say(f"  unsupported: the goal named no {binding.subject_parameter!r} this environment offers")
        return Episode(
            utterance=utterance,
            outcome="unsupported_goal",
            env=binding.page,
            goal_state=goal.goal_state,
            parameters=dict(goal.parameters),
            source=plan.source,
        )

    runtime_parameters = binding.runtime_parameters(goal.parameters)
    runtime_goal_state = binding.runtime_goal_state()
    proof_text = binding.success_for(goal.parameters)
    proof_region = binding.success_region(goal.parameters)
    say(f"  environment: {binding.page}   completes on: {completion}")
    say(f"  checkable as: {runtime_goal_state}   proven by {proof_text!r} in {proof_region}")

    from src.perception.browser_session import BrowserSession  # lazy: needs Playwright
    from src.perception.session_thread import SessionThread, ThreadedSession
    from src.perception.vlm_observer import VlmObserver, available_vision_client

    httpd, port = _start_static_server(str(repo / "env" / "mock_envs"))
    url = f"http://127.0.0.1:{port}/{binding.page}"
    # The browser gets its own thread. Playwright's sync API owns the event loop
    # of whatever thread uses it, so the async runtime cannot be started on that
    # thread - the reason run_agent_on_env.py --planner runtime has never run.
    worker = SessionThread(lambda: BrowserSession.launch(url, headless=not headed))
    session = ThreadedSession(worker)
    started = time.monotonic()
    try:
        adapter = WebBenchmarkAdapter(session)

        # Checked against the region the goal names, never the whole page. The
        # product title is printed in the listing before anything is added, so a
        # body-text proxy reports the goal as met before the agent has acted.
        def goal_reached(_adapter: Any = None) -> bool:
            # WebBenchmarkAdapter uses an immediate querySelector probe when
            # available.  A post-action-only selector such as ``.voted`` must
            # return false before the click, not wait for Playwright's timeout.
            observed = adapter.text_content(proof_region).lower()
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
        # A real screenshot goes to a real vision model, and its answer enters
        # the episode as a second source on the same fact. Agreement means the
        # goal was verified by two independent sources; disagreement means the
        # arbiter raises a conflict and the runtime re-observes rather than
        # trusting either one. With no model configured it abstains, and the run
        # reports that instead of pretending a model looked.
        # One paid call per episode. The runtime observes more than once, so an
        # unguarded second opinion would bill per observation rather than per
        # question, and the answer to "is the cart non-empty" does not change
        # between two observations of the same pixels.
        observer = VlmObserver(client=vision_client or available_vision_client(), max_calls=1)
        judgements: list[Any] = []
        # Ask in the words the person used, not the page's internal hook. The
        # cart shows "4K Monitor x1"; asking whether "monitor" is shown produced
        # a confident False, because that is not what the region says.
        # The question comes from the binding, because only the binding knows what
        # its verification region looks like when the goal holds. Asking about the
        # post title against a 32x32 arrow button got a confident False, and the
        # model was right: the crop contained a triangle, not a title.
        question = binding.visual_question(goal.parameters)

        def visual_second_opinion() -> Any:
            # Spend the one paid call where a wrong answer would do damage: on
            # the claim that the goal was reached. Before the action the DOM and
            # the model would only agree that nothing has happened yet, which
            # corroborates nothing and costs the same. This direction is the one
            # that matters because a false success is the failure this project
            # exists to prevent - an optimistic rollback shows a full cart in the
            # DOM and an empty one on screen, and only a second modality sees it.
            if not goal_reached():
                return None

            # The region the goal names, not the whole viewport. A model asked
            # about the cart should be shown the cart: it is a better question,
            # and an image is billed by area, so it is also about fifty times
            # cheaper. Falls back to the full page if the region is not rendered.
            image = session.screenshot_element(proof_region) or session.screenshot()
            judgement = observer.look(image, question, region=proof_region)
            judgements.append(judgement)
            return judgement.as_assertion(binding.state_entity, binding.state_attribute)

        observed_adapter = GoalStateReportingAdapter(
            runtime_adapter,
            fact=binding.observed_fact,
            holds=goal_reached,
            second_opinion=visual_second_opinion,
        )
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
        solved = goal_reached()
        # Read once more here, while the session is still open: this is the
        # number the claim rests on, and it is taken independently of anything
        # the runtime reported.
        oracle_observed = adapter.text_content(proof_region)
    finally:
        session.close()
        httpd.shutdown()

    episode = Episode(
        utterance=utterance,
        outcome="reached" if solved else "not_reached",
        env=binding.page,
        goal_state=goal.goal_state,
        runtime_goal_state=runtime_goal_state,
        parameters=dict(goal.parameters),
        source=plan.source,
        model_derived=plan.is_model_derived,
        completion=completion,
        runtime_state=outcome.result.state.value,
        verified=outcome.result.final_outcome_verified,
        reason=outcome.result.reason,
        episode_id=outcome.result.episode_id,
        transitions=[
            {
                "transition_id": r.transition_id,
                "step": r.step,
                "skill_id": r.skill_id,
                "backend": r.backend,
                "affordance_key": r.affordance_key,
                "params": dict(r.params),
                "success": r.success,
                "execution_success": r.execution_success,
                "postcondition_passed": r.postcondition_passed,
                "latency_ms": round(r.latency_ms, 2),
                "attempt": r.attempt,
                "recovery_action": r.recovery_action,
                "recovery_tier": r.recovery_tier,
                "recovery_of_transition_id": r.recovery_of_transition_id,
                "failure_reason": r.failure_reason,
            }
            for r in outcome.transition_ledger.records
        ],
        # The primitives the runtime planned, in full. A count says the plan
        # existed; this says what it was, which is what a reader needs to tell
        # a schema-driven plan from a pre-authored sequence.
        primitive_plan=[dict(step) for step in outcome.result.primitive_plan],
        backends_used=sorted({r.backend for r in outcome.transition_ledger.records if r.backend}),
        # The oracle read once more at the end, independently of anything the
        # runtime reported. This is the number the claim rests on.
        final_oracle={
            "region": proof_region,
            "expected": proof_text,
            "observed": oracle_observed,
            "goal_reached": solved,
        },
        # The goal predicate as the runtime saw it change: evidence that
        # verification re-observed rather than trusted the executor.
        goal_state_trail=[
            {"value": a.value, "source": a.source}
            for a in outcome.cognitive_map.state_assertions
            if a.entity_id == binding.state_entity
        ],
        visual_evidence=[j.to_dict() for j in judgements],
        latency_ms=(time.monotonic() - started) * 1000.0,
    )
    say(f"  runtime   : state={episode.runtime_state} verified={episode.verified} ({episode.reason})")
    for record in episode.transitions:
        mark = "ok " if record["success"] else "FAIL"
        detail = f" - {record['failure_reason']}" if record["failure_reason"] else ""
        say(f"    [{mark}] step {record['step']} on {record['affordance_key']}{detail}")
    used = [j for j in episode.visual_evidence if j["is_model_derived"]]
    if episode.visual_evidence:
        first = episode.visual_evidence[0]
        if used:
            say(
                f"  vision    : {first['model']} answered {first['answer']} "
                f"at confidence {first['confidence']:.2f} on screenshot {first['screenshot_sha256']}"
            )
            say(f"              {first['evidence']}")
        else:
            say(f"  vision    : not used ({first['source']}{': ' + first['error'] if first['error'] else ''})")
    trail = " -> ".join(str(step["value"]) for step in episode.goal_state_trail)
    if trail:
        say(f"  {runtime_goal_state.split(' ')[0]} observed: {trail}")
    say(f"  result    : {'GOAL REACHED' if solved else 'not reached'}")
    return episode


def _m1_report(episodes: list[Episode]) -> dict[str, Any]:
    """Cross-environment generalisation, over the episodes that were attempted.

    Utterances the intent layer refused, and goals no environment here can
    satisfy, are excluded from M1 and counted separately: they measure the
    vocabulary, not whether one action system works across environments.
    """
    attempted = [episode for episode in episodes if episode.attempted]
    results = [
        BenchmarkRunResult(
            env=episode.env,
            task_id=episode.goal_state,
            success=episode.reached,
            steps=len(episode.transitions),
            latency_ms=round(episode.latency_ms, 3),
        )
        for episode in attempted
    ]
    summary = aggregate_m1(results)
    summary["declined"] = {
        "refused_intent": sum(1 for e in episodes if e.outcome == "refused_intent"),
        "unsupported_goal": sum(1 for e in episodes if e.outcome == "unsupported_goal"),
    }
    summary["model_derived_episodes"] = sum(1 for e in attempted if e.model_derived)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--utterance",
        default="add the wireless headphones to my cart",
        help="What a person said. This is the only task input.",
    )
    parser.add_argument(
        "--suite",
        action="store_true",
        help="Run every utterance across both environments and write the M1 report.",
    )
    # Headed by default, like every other demo script here. A demo whose window
    # has to be asked for is a demo that gets presented as a wall of terminal
    # text, and the point of this one is that the runtime acts on a real page.
    # CI and the live tests call run_episode directly with headed=False, so the
    # default costs them nothing.
    parser.add_argument("--headed", dest="headed", action="store_true", default=True)
    parser.add_argument("--headless", dest="headed", action="store_false")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    repo = Path(__file__).resolve().parents[1]
    out = repo / "eval_outputs" / "intent_episode" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    if not args.suite:
        print(f"\n{_LINE}\n  INTENT -> RUNTIME - one utterance through the real episode runner\n{_LINE}")
        print(f'  said: "{args.utterance}"\n')
        episode = run_episode(args.utterance, repo=repo, headed=args.headed)
        (out / "intent_episode.json").write_text(json.dumps(episode.to_dict(), indent=2), encoding="utf-8")
        print(f"\n  artifacts : {out.relative_to(repo)}\n{_LINE}\n")
        return 0 if episode.reached else 1

    print(f"\n{_LINE}\n  INTENT -> RUNTIME suite - {len(SUITE)} utterances, both environments\n{_LINE}")
    episodes: list[Episode] = []
    for index, utterance in enumerate(SUITE, start=1):
        print(f'\n  --- {index}/{len(SUITE)}: "{utterance}"')
        episodes.append(run_episode(utterance, repo=repo, headed=args.headed))

    summary = _m1_report(episodes)
    print(f"\n{_LINE}\n  M1 - cross-environment generalisation\n{_LINE}")
    print(f"  {'environment':<18} {'tasks':>6} {'solved':>7} {'success':>9} {'mean latency':>14}")
    print(f"  {'-' * 58}")
    for row in summary["per_env_M1"]:
        print(
            f"  {row['env']:<18} {row['tasks']:>6} {row['solved']:>7} "
            f"{row['success_rate']:>8.1%} {row['mean_latency_ms']:>12.0f}ms"
        )
    print(f"\n  overall            {summary['n_tasks']:>6} {'':>7} {summary['overall_success_rate']:>8.1%}")
    print(f"  environments       {summary['n_envs']:>6}")
    print(
        f"  declined           {summary['declined']['refused_intent']} utterance(s) not understood, "
        f"{summary['declined']['unsupported_goal']} goal(s) no environment here can satisfy"
    )
    print("\n  Declined utterances are excluded from M1 on purpose: they measure the")
    print("  intent vocabulary, not whether one action system works across environments.")

    report = {"summary": summary, "episodes": [e.to_dict() for e in episodes]}
    (out / "m1_cross_env.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    stable = repo / "artifacts" / "intent_cross_env"
    stable.mkdir(parents=True, exist_ok=True)
    (stable / "m1_cross_env.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  artifacts : {out.relative_to(repo)}  and  {stable.relative_to(repo)}\n{_LINE}\n")
    return 0 if summary["overall_success_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
