# feat(effectors): snapshot and restore WoT device state between episodes

**Branch:** `feature/B-116-wot-episode-isolation` → `develop`
**Scope:** Member B (Ruiyao) — WoT half of episode isolation
**Reference:** `current_codebase_full_analysis_en.md`, PDF §4.5

---

## Problem

Recreating a browser context isolates the **web** half of an episode. The things
behind the Thing Descriptions are shared and persistent: an episode that sets the
thermostat to 26 leaves it there, and the next episode starts from that value.

Nothing reset device state, so "isolated episode" only ever described the browser.

## Fix

`src/effectors/wot_episode_isolation.py` — read every property the TDs expose
before an episode, write back the ones that changed afterwards.

```python
with WotEpisode(executor):
    run_task()          # devices are restored on the way out, even on failure
```

`WotEpisode` is a context manager because a failed run must not leave devices
mutated: that is precisely how one episode's failure becomes the next episode's
wrong starting point.

### The reporting is the point

A partial rollback that reports itself as complete is worse than no rollback,
because the number gets quoted as evidence. So the snapshot states what it
**cannot** undo:

| Situation | Reported as |
|---|---|
| Read-only property (a sensor) | `read_only` — observable, not restorable |
| Property that could not be read | `unreadable` — no baseline exists |
| Value never drifted | `unchanged` — and **not** rewritten, so restoring generates no device traffic |
| Write failed | `failed{}` and `ok` becomes False |

`is_complete` is true **only** when every exposed property can be rolled back.

## Supporting change to `WotExecutor`

Three additive accessors: `state_sources()`, `read_state()`, `write_state()`.
`write_state` prefers the TD's own `writeproperty` form and falls back to the
read href with `PUT`, which is the common WoT shape.

## Tests

`tests/test_wot_episode_isolation.py` — 12 cases:

| Area | Asserts |
|---|---|
| snapshot | every exposed property read; read-only and unreadable ones reported |
| snapshot | `is_complete` is false when coverage is partial |
| restore | only drifted properties are written back |
| restore | read-only properties are skipped, never attempted |
| restore | a failed write is recorded and clears `ok` |
| restore | a property with no baseline is skipped |
| episode | state restored on normal exit **and** when the episode raises |
| real TD | end-to-end through `WotExecutor` against a Thing Description: the writable setpoint is restored via the TD's `PUT` form, the read-only sensor is never written |

## Verification

| Check | Result |
|---|---|
| `ruff` on changed files | All checks passed |
| `black --check` on changed files | 3 files unchanged |
| `mypy` on changed modules | no issues in 2 source files |
| `pytest --tb=short -q` (full suite) | **334 passed** |

> `ruff check .` / `black --check .` / `mypy src/` currently fail on `develop`
> itself because of unrelated files under `evaluation/`. Those are untouched
> here; every file changed by this branch passes all three individually.

## Compatibility

- Additive only. `WotExecutor.execute`, `load_tds` and `get_affordance` keep
  their behaviour; the three new accessors are appended.
- Two new files, one modified (`src/effectors/wot_executor.py`).
- Merges cleanly into `develop` and with `B-113`, `B-114`, `B-115`, `B-117`.
