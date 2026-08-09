"""M1 must count what it claims to count.

The metric is "one action system works across different environments". An
utterance nobody could interpret, or a goal no environment here offers, says
nothing about that - counting either one would move the number without any
evidence behind the movement.
"""

from __future__ import annotations

from scripts.run_intent_episode import SUITE, Episode, _m1_report


def _attempt(env: str, *, reached: bool, latency: float = 100.0) -> Episode:
    return Episode(
        utterance="u",
        outcome="reached" if reached else "not_reached",
        env=env,
        goal_state="item_in_cart",
        transitions=[{"step": 1}],
        latency_ms=latency,
    )


def test_declined_utterances_are_excluded_from_the_rate():
    episodes = [
        _attempt("shopping.html", reached=True),
        _attempt("forum.html", reached=True),
        Episode(utterance="make me a sandwich", outcome="refused_intent"),
        Episode(utterance="turn on the projector", outcome="unsupported_goal"),
    ]

    summary = _m1_report(episodes)

    assert summary["n_tasks"] == 2, "a declined utterance is not an attempted task"
    assert summary["overall_success_rate"] == 1.0
    assert summary["declined"] == {"refused_intent": 1, "unsupported_goal": 1}


def test_a_failed_episode_lowers_the_rate():
    summary = _m1_report([_attempt("shopping.html", reached=True), _attempt("shopping.html", reached=False)])

    assert summary["overall_success_rate"] == 0.5
    assert summary["n_envs"] == 1


def test_environments_are_reported_separately_so_a_weak_one_is_visible():
    summary = _m1_report(
        [
            _attempt("shopping.html", reached=True),
            _attempt("shopping.html", reached=True),
            _attempt("forum.html", reached=False),
        ]
    )
    rates = {row["env"]: row["success_rate"] for row in summary["per_env_M1"]}

    assert rates == {"shopping.html": 1.0, "forum.html": 0.0}
    assert summary["n_envs"] == 2, "averaging two environments into one number hides the weak one"


def test_an_empty_run_reports_zero_rather_than_dividing_by_zero():
    summary = _m1_report([Episode(utterance="u", outcome="refused_intent")])

    assert summary["n_tasks"] == 0 and summary["overall_success_rate"] == 0.0


def test_model_derived_episodes_are_counted_separately():
    """With no API key this is zero, and the report must say so rather than imply a model ran."""
    summary = _m1_report([_attempt("shopping.html", reached=True)])

    assert summary["model_derived_episodes"] == 0


def test_the_suite_covers_both_environments_and_one_it_must_refuse():
    """A suite where everything succeeds only ever tests the happy path."""
    assert any("cart" in utterance for utterance in SUITE)
    assert any("upvote" in utterance for utterance in SUITE)
    assert "make me a sandwich" in SUITE
