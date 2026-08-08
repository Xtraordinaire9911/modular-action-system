"""The registry has to stay correct as demos are added and branches move.

Two properties are load-bearing and are pinned here:

*Extensible* — a new demo is one entry. These tests validate every entry
structurally, so a malformed addition fails in CI rather than in a meeting.

*Backward compatible* — the registry only points at demo scripts. It must never
require them to change, and it must tolerate a script being absent, which is the
normal state while a feature branch is still in review.
"""

from __future__ import annotations

import sys

from src.demos.registry import (
    CAPABILITIES,
    DEMOS,
    REPO_ROOT,
    Demo,
    DemoStatus,
    build_argv,
    capability_report,
    check_capability,
    find,
    runnable,
    status_of,
)

# ── registry integrity ───────────────────────────────────────────────────────────


def test_names_are_unique():
    names = [d.name for d in DEMOS]
    assert len(names) == len(set(names))


def test_every_entry_is_populated():
    for demo in DEMOS:
        assert demo.name and " " not in demo.name, f"{demo.name!r} must be a single token"
        assert demo.title and demo.summary
        assert demo.command, f"{demo.name} has no command"


def test_declared_capabilities_all_exist():
    """A typo in `requires` would otherwise silently make a demo unrunnable."""
    for demo in DEMOS:
        for capability in demo.requires:
            assert capability in CAPABILITIES, f"{demo.name} requires unknown capability {capability!r}"


def test_script_backed_demos_point_at_paths_inside_the_repo():
    for demo in DEMOS:
        path = demo.script_path
        if path is None:
            continue  # module invocation, e.g. -m src.pipeline
        assert not path.is_absolute() or REPO_ROOT in path.parents or path == REPO_ROOT


def test_module_demos_are_recognised_and_always_present():
    module_demos = [d for d in DEMOS if d.script_path is None]
    assert module_demos, "expected at least one `-m module` demo"
    for demo in module_demos:
        assert demo.exists_here, "a module invocation is not checkout-dependent"


# ── backward compatibility ───────────────────────────────────────────────────────


def test_absent_script_is_reported_not_raised():
    """A demo from an unmerged branch must degrade, not explode."""
    ghost = Demo(
        name="ghost",
        title="Not merged yet",
        summary="Lives on a feature branch.",
        command=("scripts/definitely_not_here.py",),
    )
    status = status_of(ghost)

    assert status.state == DemoStatus.NOT_IN_CHECKOUT
    assert not status.ready
    assert "definitely_not_here" in status.detail


def test_missing_capability_is_reported_with_a_remedy():
    blocked = Demo(
        name="blocked",
        title="Needs something",
        summary="",
        command=("run_demo.py",),  # exists, so only the capability can fail
        requires=("smart_room",),
    )
    status = status_of(blocked)

    if not status.ready:  # smart-room services are usually not up during tests
        assert status.state == DemoStatus.MISSING_CAPABILITY
        assert "smart_room" in status.missing
        assert status.detail, "a blocked demo must say how to unblock it"


def test_unknown_capability_never_raises():
    ok, detail = check_capability("does-not-exist")
    assert ok is False and "unknown" in detail


def test_capability_report_covers_every_capability():
    report = capability_report()
    assert set(report) == set(CAPABILITIES)
    for ok, detail in report.values():
        assert isinstance(ok, bool) and detail


# ── argv construction ────────────────────────────────────────────────────────────


def test_argv_starts_with_this_interpreter():
    """Uses sys.executable so a demo runs in the same environment as the runner."""
    argv = build_argv(DEMOS[0])
    assert argv[0] == sys.executable


def test_headed_args_are_only_added_when_asked():
    demo = next(d for d in DEMOS if d.headed_args)
    assert not set(demo.headed_args) & set(build_argv(demo, headed=False))
    assert set(demo.headed_args) <= set(build_argv(demo, headed=True))


def test_extra_args_are_passed_through_last():
    argv = build_argv(DEMOS[0], extra=["--sentinel"])
    assert argv[-1] == "--sentinel"


def test_module_demo_argv_keeps_the_m_flag():
    demo = next(d for d in DEMOS if d.script_path is None)
    assert argv_contains_in_order(build_argv(demo), ["-m", demo.command[1]])


def argv_contains_in_order(argv: list[str], items: list[str]) -> bool:
    idx = 0
    for token in argv:
        if idx < len(items) and token == items[idx]:
            idx += 1
    return idx == len(items)


# ── lookup ───────────────────────────────────────────────────────────────────────


def test_find_returns_none_for_unknown_name():
    assert find("no-such-demo") is None
    assert find(DEMOS[0].name) is DEMOS[0]


def test_runnable_is_a_subset_that_all_report_ready():
    for demo in runnable():
        assert status_of(demo).ready


def test_offline_demo_is_always_runnable():
    """The one demo that needs no browser, no clone and no Docker."""
    offline = find("offline")
    assert offline is not None and offline.requires == ()
    assert status_of(offline).ready, "the fallback demo must work on a bare checkout"
