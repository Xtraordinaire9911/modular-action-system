# feat: an utterance drives the production runtime, and the evidence to back it

**Branch:** `feature/B-119-intent-driven-runtime` → `develop`
**Scope:** Member B (Ruiyao) — planner integration, evaluation, terminology
**Reference:** `current_codebase_full_analysis_en.md` §1 R2, §4 P1/P2, §6; supervisor meeting 2026-07-17 TODO-5

> Merge `chore/sync-main-into-develop` first — see `PR_SYNC-main-into-develop.md`.

---

## 1. The integration the review kept asking for

The standing criticism is **components, not an integrated system**. The clearest
instance: `src/planner/intent_planner.py` turned a sentence into a `GoalSpec`,
and *nothing in `src/runtime/` consumed it*. The narrated demo used it, but that
demo runs its own loop — so the layer had never reached the real runtime at all.

```
utterance
  → IntentPlanner                    model if configured, labelled rule fallback if not
  → GoalSpec(source="user_intent_parser")   the runtime's own declared handoff point
  → EnvironmentBinding               which page, which control completes it, which predicate
  → RuntimeEpisodeSpec(goal_spec=…)
  → RuntimeEpisodeRunner.run_goal_episode
  → ContinuousInteractionManager, on a live Chromium page
```

```
$ python scripts/run_intent_episode.py --utterance "add the wireless headphones to my cart"

  layer 1    : rule_fallback (confidence 0.40)
  goal_state : item_in_cart   parameters: {"item": "wireless headphones"}
  provenance : GoalSpec.source='user_intent_parser', model_derived=False
  environment: shopping.html   completes on: button.add-cart-btn[data-id='headphones']
  checkable as: cart.holds_item == true
  runtime    : state=completed verified=True (goal completed)
    [ok ] step 1 on dom:semantic::Add Wireless Headphones to cart
  cart.holds_item observed: False -> True
  result     : GOAL REACHED
```

The trail is the point: the runtime saw the predicate go **False → True by
re-observation**, not by trusting that the click returned success.

### Four defects had to be fixed, each of them real

**The runtime could not be started at all once a browser was open.** Playwright's
sync API owns the event loop of the thread using it, so `asyncio.run` raises
*"cannot be called from a running event loop"*; moving the coroutine to a worker
thread fails differently, because Playwright objects belong to their creating
thread (`TargetClosedError`). **This is why `scripts/run_agent_on_env.py
--planner runtime` has never run** — it crashes on the first invocation, before
doing anything. `src/perception/session_thread.py` gives the browser its own
thread and marshals calls to it.

**`GoalSpec.source` said `"manual"`.** A goal derived from a sentence arrived
labelled as though a person had typed it, in the one field the runtime reads to
tell those apart. `GoalSource` already declares `"user_intent_parser"`.

**The rule fallback never extracted what the request was about.** "Add something
to the cart" is not actionable until the something is named.

**A reached goal was recorded as a postcondition failure.** The runtime verifies
by resolving the goal as a predicate against observed state; the web adapter
reports affordances and a benchmark flag and nothing under the goal's own name,
so the lookup missed:

```
postcondition_failed: expected_effect='item_in_cart', observed=None,
reason='missing condition path: item_in_cart'
```

— with the item demonstrably in the cart. That is a **false negative**, the exact
mirror of the false success this project exists to prevent.
`GoalStateReportingAdapter` composes over any runtime adapter and adds that one
fact, re-read on every observation.

`src/planner/environment_binding.py` declares the bridge: which environment
satisfies a goal state, which **family** of controls completes it, and the
predicate the runtime checks. A family, not a target — the specific control comes
from the parameter the intent layer extracted, so this is not the hardcoded skill
map the review asked us to remove. A goal state with no entry is reported as
unsupported; an utterance matching nothing is refused with nothing attempted.

## 2. M1 cross-environment generalisation, produced for the first time

`evaluation/cross_env_eval.py` has defined M1 since the project began and had no
production consumer — only a test imported it, and `eval_outputs/cross_env` did
not exist. The headline generalisation metric had never been computed.

```
$ python scripts/run_intent_episode.py --suite

  environment         tasks  solved   success   mean latency
  forum.html              2       2   100.0%          450ms
  shopping.html           4       4   100.0%          966ms
  overall                 6           100.0%
  environments            2
  declined           1 utterance not understood, 0 goals unsupported here
```

Declined utterances are excluded from M1 and reported separately: *"make me a
sandwich"* is refused and nothing is attempted, and counting it either way would
move a generalisation number using evidence about the vocabulary. The suite
contains one on purpose so the refusal path is exercised.

Per-environment rows are kept beside the overall figure, because averaging two
environments into one number is how a weak surface hides. This is agent-driven,
not scripted — and it is still six tasks on two local mocks: a working
generalisation harness, not a generalisation result.

## 3. The statistical bar, met

```bash
python scripts/run_agent_loop_demo.py --headless --pace 0 --hold 0 --repeat 30
```

**210 episodes, 30 per condition** (`artifacts/agent_loop_campaign_30x7/`), which
is what §4 P2 requires. TSR 57.1%, RTA 100%, DA 100%, all four tiers exercised.

The README in that folder states plainly what 30 repetitions of a *deterministic*
fault do and do not establish: **reproducibility, not variance.** Reading RTA
100% as "accurate in general" would be wrong; a distribution needs randomised
fixtures, and this is not that.

## 4. "All tests pass" now means something

The suite ran in five seconds and never opened a browser, started Docker or
touched a device — so it could not corroborate a single live claim, which is the
supervisor's *"you say the tests pass and I don't believe they prove it"* in a
new form.

`pytest -m live` opens a real Chromium and asserts the claims in the README
table: selectors that locate exactly one element, geometry measured rather than
fixed, episode state that does not leak, verification scoped to the region the
goal names, and the probes measuring a real obstruction. CI runs it as its own
job so a browser problem is distinguishable from a logic problem at a glance.

Two of the eight are written to **fail if a documented weakness is silently
fixed** — `reset()` is documented as leaking, and the product title is documented
as appearing before anything is added — because a claims table is only useful
while it is accurate in both directions.

## 5. Picture-in-Picture: the term the review corrected

PiP, in the referenced work, is a **supervised interface**: the agent runs in a
visibly separate session a person can watch live and take over from. Two things
here were carrying that word and are not it — browser-context isolation (no human
involved) and the narration panel (read-only). The docstrings had been corrected;
the module name had not, and a file called `pip_console` that is explicitly not
PiP is how a misreading survives its own correction. It is now
`narration_console`, and the README has a Terminology section stating what PiP
means, what was confused with it, and that the closest real oversight mechanism
is the tier-4 handover — which is still not PiP.

## 6. Demo length

287s → **145s** at the new defaults, same results. The first scene is narrated at
full length because it teaches the loop; from the second scene the beats
explaining an already-shown phase are shortened while the ones carrying new
information keep their timing, with a floor so they stay readable. The line
tracer is treated the same way. Nothing is skipped and `--pace` scales all of it.

## Files

| file | change |
| --- | --- |
| `src/planner/environment_binding.py` | new — goal state → environment, completion family, checkable predicate |
| `src/planner/goal_state_adapter.py` | new — reports the goal fact so the runtime can verify it |
| `src/perception/session_thread.py` | new — browser on its own thread, so the async runtime can run |
| `scripts/run_intent_episode.py` | new — the utterance→runtime entry point, and `--suite` for M1 |
| `tests/test_live_browser_claims.py` | new — 8 live-browser claims, CI job |
| `src/planner/intent_planner.py` | correct provenance; extract the request's subject |
| `src/demos/narration_console.py` | renamed from `pip_console`, with the reasoning |
| `scripts/run_agent_loop_demo.py` | familiar-scene pacing; import updated |
| `.github/workflows/ci.yml` | `live-browser` job |
| `README.md` | terminology section, claims corrected in both directions |

## Verification

- `pytest -q` → **520 passed**
- `pytest -m live` → **8 passed** (real Chromium, ~16s)
- `ruff`, `black`, `mypy` clean on every file this branch touches
- `python run_demo.py` clean; loop demo re-run headed and headless
