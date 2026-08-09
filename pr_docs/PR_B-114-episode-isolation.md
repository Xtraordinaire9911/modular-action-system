# feat: real episode isolation and an observable tier-4 handover

**Branch:** `feature/B-114-episode-isolation` → `develop`
**Scope:** Member B (Ruiyao) — browser/WoT episode isolation, supervised takeover
**Reference:** `current_codebase_full_analysis_en.md`, PDF §4.5 (recommendations 1–3)

---

## Part 1 — `reset()` was never an episode boundary

`reset()` was the only per-run entry point, and it re-navigates and nothing else:

```python
def reset(self) -> None:
    if self._url:
        self._page.goto(self._url)      # cookies / localStorage / sessionStorage all survive
```

So a later episode could still observe what an earlier one wrote, while the
session was documented as isolated.

### Measured on a real Chromium (`env/mock_envs/shopping.html`)

```
episode 1 writes localStorage['episode1'] = 'leaked'
after reset()        -> 'leaked'      state carried over
after new_episode()  -> None          boundary holds
after restore        -> 'leaked'      rollback reproduces the snapshot
```

### What was added to `BrowserSession`

| Member | Purpose |
|---|---|
| `new_episode(url=None, storage_state=None)` | Closes the live context and opens a fresh one — this is what actually drops cookies, storage and cache. Reapplies the fail-fast action timeout, re-navigates, bumps `episode_index`. |
| `storage_snapshot()` | Cookies + per-origin storage; `{}` when there is no real context, so "nothing to restore" stays distinct from "restored empty". |
| `episode_index` | How many boundaries have been crossed. |

`reset()` keeps its behaviour and now documents that it is navigation only.
Context creation moved into one `_fresh_context` helper so `launch()` and
`new_episode()` cannot drift on viewport or scale factor.

## Part 2 — tier-4 escalation had no takeover

`HumanEscalationPolicy` decides *whether* to escalate, but the run never stopped
for anyone. Escalation was a label on a result: no pause, no handover, and
nothing to report about how often a human was needed or what they did.

`src/recovery/supervised_takeover.py` adds the missing boundary. An episode is
paused with a reason, a supervisor resumes it, and the resume records whether
anything was changed.

**The distinction that makes the metric honest:** resuming with no correction
records `corrected=False`. "Looked and approved" stays separate from "applied a
fix", so `correction_rate` reflects *interventions*, not *interruptions* —
counting pauses alone would overstate how often a human was required.

```python
metrics() -> {"pauses", "completed", "corrections",
              "correction_rate", "mean_wait_ms", "total_wait_ms"}
```

Other properties:

- `wait_ms` is `None` while a handover is open, and `metrics()` derives rate and
  wait only from **completed** handovers — an unfinished pause cannot be
  silently scored as a non-correction.
- `pause()` while already paused, and `resume()` with nothing paused, both raise
  rather than quietly discarding the first handover's timing.
- The clock is injectable and monotonic: a long pause is not corrupted by a
  wall-clock jump, and tests need no sleeping.

## Tests

`tests/test_episode_isolation.py` (9) — Playwright-shaped fakes assert that
`new_episode` replaces **and closes** the context, that `reset` does **not**,
that a fresh episode carries no `storage_state`, that a snapshot round-trips
into the new context, that the action timeout and navigation are reapplied, and
that an injected page driver degrades to re-navigation instead of failing.

`tests/test_supervised_takeover.py` (9) — pause/resume wait, uncorrected resume,
open handover, both ordering guards, reported rate and mean wait, the empty
case, and one end-to-end test that takes a real `EscalationDecision` from
`HumanEscalationPolicy` and turns it into a recorded handover.

## Verification

| Check | Result |
|---|---|
| `ruff` on changed files | All checks passed |
| `black --check` on changed files | unchanged |
| `mypy` on changed modules | no issues |
| `pytest --tb=short -q` (full suite) | **340 passed** |
| real-browser isolation proof | reset leaks · new_episode isolates · snapshot restores |

> `ruff check .` / `black --check .` / `mypy src/` currently fail on `develop`
> itself because of unrelated files under `evaluation/` (fusion / open-web
> modules). Those are untouched here; every file changed by this branch passes
> all three checks individually.

## Compatibility

- Additive only: `reset()`, `launch()`, `open()` and `close()` keep their
  behaviour; `__init__` gains a keyword argument with a default.
- Two new files, one modified (`src/perception/browser_session.py`).
- Merges cleanly into the current `develop`; no file overlap with `B-113`.
