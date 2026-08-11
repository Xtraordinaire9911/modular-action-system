# merge: bring the PiP MVP onto develop

**Branch:** `integration/pip-mvp-on-develop` → `develop`
**Contains:** all of Fadi's `feature/project-pip-mvp`, merged with current `develop`
**Nobody's branch was rewritten** — this is a separate branch, `feature/project-pip-mvp` is untouched.

---

## Why this exists

`feature/project-pip-mvp` was **87 commits behind `develop`**. Merging it as it
stood would have deleted about **89,000 lines** — everything landed since 29 July.
So the one piece of work that answers the gap the supervisor named by name could
not be released, and the branch would only drift further.

Eight files conflicted. Four were additive on both sides and both sides are kept.
Four needed an actual decision.

## The four that needed a decision

### `src/runtime/live_environment.py`

The PiP side added `checkpoint()` / `restore()` and its own `inject()`. `develop`
had extended `inject()` with `read_delay_ms`, `drop_probability` and
`source_reliability`. Taking either side whole loses the other. Kept
checkpoint/restore **and** develop's richer signature.

### `env/node_wot_server/server.js` — two separate problems

**Both sides rewrote `guard()`.** The shared tail after the conflict returns
`generationAtStart` and calls `assertCurrentGeneration`, which only exist on the
PiP side — so taking develop's signature alone would have left the function
referring to undefined names. Merged into one `guard()` that does the generation
check *and* the injected read delay / drop.

**The second problem was invisible in the conflict.** The PiP refactor moved the
control plane into `processControlRequest`, whose `/failure` branch copies only
`type` and `delay_ms`, and whose validator matches keys **exactly**. The three
read-fault fields the ambiguous-fusion campaigns depend on would have been
rejected with a 400 and silently never reached `faults[]` — Yixin's holdout runs
would have started failing for a reason nowhere near the code that broke them.
The validator and the store now carry them.

### `tests/test_episode_isolation.py` (add/add)

The two sides test different things: the PiP side tests the isolation provider
and the input lease, develop's tests `BrowserSession` episode boundaries. Split
into `tests/test_isolation_provider.py` rather than merged into one 500-line file
covering two subjects. Fadi — say if you would rather it stayed in one file.

### `README.md` / `STATUS.md` — a contradiction, not a conflict

Both added table rows, and keeping both produced a direct contradiction: the new
PiP MVP row says a supervised interface exists, while the row above it said
Picture-in-Picture is not implemented. Both were true about different things, so
the claim is now split:

| | |
| --- | --- |
| **Implemented, for the web** | own browser context per episode, WoT checkpoint/restore, an input lease, and a supervised pause a person can take over from |
| **Not claimed, for Windows** | no child desktop over RDP, no OS-level input or process boundary |

The Terminology section is updated to match. Browser-context isolation on its own
is still not PiP, and still says so.

## Verified on the merged tree

| gate | result |
| --- | --- |
| `pytest -q` | **570 passed** (528 from develop + 42 from the PiP branch) |
| `pytest -m live` | **9 passed**, real Chromium |
| `ruff` / `black` / `mypy` | clean |
| `python run_demo.py` | clean |
| `scripts/run_agent_loop_demo.py` | 7 scenes, TSR 57.1%, RTA 100%, DA 100% — unchanged |
| `scripts/run_intent_episode.py` | `state=completed verified=True`, `cart.holds_item False -> True` |
| isolation / intervention / takeover suites | 67 passed |
| files of `develop` missing from this branch | **0** |

**`server.js` was not executed here** — no node on this machine — so it is checked
structurally only (brace balance, and every symbol both sides need is present).
CI's docker job and `server.test.js` should be the judge before this merges.

## Note

`develop` moved once during this work (Yixin's `add Friday demo ownership plan`),
so that commit is merged in too and nothing on `develop` is behind.
