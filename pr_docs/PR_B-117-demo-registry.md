# feat(demos): add a registry so every demo is discoverable and self-checking

**Branch:** `feature/B-117-demo-registry` → `develop`
**Scope:** Member B (Ruiyao) — demo infrastructure
**Reference:** `current_codebase_full_analysis_en.md`, PDF §5 D10, §6

---

## Problem

Demos had accumulated across `run_demo.py`, six `scripts/run_*.py` entry points
and several `src.pipeline` flags. **Nothing enumerated them.** The only way to
learn what could be shown on a given machine was to try each one and read the
traceback.

That is how a demo ends up missing from a meeting.

## Fix

`src/demos/registry.py` declares each demo once and answers, at runtime, whether
it can run here. `scripts/demo.py` is the entry point:

```bash
python scripts/demo.py list          # what exists, and what is runnable now
python scripts/demo.py doctor        # what is missing, and the command to fix it
python scripts/demo.py run <name>    # run one
python scripts/demo.py run --all     # everything currently runnable
```

```text
DEMO               STATUS       TIME     TITLE
------------------------------------------------------------------------------
offline            ready        ~5s      Deterministic offline trace
visual-grounding   not here     ~15s     Visual grounding smoke trace
mock-envs          ready        ~1min    WebArena-style mock environments
cross-env          ready        ~2min    Cross-environment suite (academic + industrial)
miniwob            ready        ~1min    MiniWoB++ curated suite
live-runtime       needs setup  ~2min    Live runtime tracer bullet
adaptation         ready        ~10s     Adaptation and policy proposal
```

`doctor` returns a remedy, not an abstract error:

```text
NO  smart_room   dashboard :3000, WoT :8080 unreachable - docker compose -f env/docker-compose.yml up
visual-grounding  scripts/run_visual_grounding_smoke.py is not in this checkout
```

## The two properties this is built around

### Extensible — a new demo is one entry

```python
Demo(
    name="my-demo",
    title="What it shows",
    summary="One line for the listing.",
    command=("scripts/run_my_demo.py",),
    requires=("browser",),          # browser | miniwob | smart_room
    headed_args=("--headed",),
    duration_hint="~30s",
)
```

No runner change, no CLI change, no README edit needed for it to be
discoverable. Capability requirements are declared as tokens whose probes live
in one place, so the CLI and the tests agree on what "available" means. The tests
validate every entry structurally, including a typo in `requires`, so a malformed
addition fails in CI rather than in a meeting.

### Backward compatible — nothing existing is touched

No demo script is modified or wrapped. They keep their own flags and stay
runnable directly; the registry only points at them.

A demo whose script is **absent from the current checkout** is reported as
`not here` rather than raising. That keeps the registry valid on any branch and
while a feature is still in review — `visual-grounding` reports `not here` on
`develop` today for exactly that reason, and that is intended behaviour, not a
gap. Once `B-113` merges it becomes `ready` with no change here.

## Capability probes are side-effect free

The browser check looks for the Playwright browser cache rather than launching a
browser; the smart-room check is a short TCP connect rather than an HTTP round
trip. Listing demos never starts anything.

## Tests

`tests/test_demo_registry.py` — 16 cases:

| Area | Asserts |
|---|---|
| integrity | names unique; every entry populated; commands non-empty |
| integrity | every `requires` token is a known capability |
| integrity | module (`-m`) demos are recognised and never checkout-dependent |
| compatibility | an absent script degrades to `not-in-checkout`, never raises |
| compatibility | a blocked demo always carries a remedy string |
| compatibility | an unknown capability never raises |
| argv | built from `sys.executable`, so a demo runs in the runner's environment |
| argv | `--headed` args only appear when asked; extra args pass through last |
| lookup | `offline` stays runnable on a bare checkout — there is always something to show |

## Verification

| Check | Result |
|---|---|
| `ruff` on changed files | All checks passed |
| `black --check` on changed files | 4 files unchanged |
| `mypy src/demos/` | no issues in 2 source files |
| `pytest --tb=short -q` (full suite) | **338 passed** |
| `demo.py list` / `doctor` / `run offline` | exercised end to end, exit 0 |

## Compatibility

- Additive: two new modules, one new script, one new test file, one new README
  section placed outside Quick Start.
- The README section was deliberately moved out of Quick Start: it originally
  sat at the same anchor as `B-115`'s clean-clone section, which made the two
  branches unmergeable in either order. Now 10 of 10 branch pairs merge cleanly.
