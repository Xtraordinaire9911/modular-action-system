# release: develop → main

**Branch:** `develop` → `main`
**Type:** Release pull request — the whole integration branch, not a feature
**Prerequisite:** already satisfied — `chore/sync-main-into-develop` (#70) is merged, so `main` is an ancestor of `develop` and **there are no conflicts to resolve**.

---

## Why this exists

`main` is **124 commits behind** `develop` and **0 commits ahead**. Everything on
`main` is already in `develop`; nothing on `main` is newer.

That matters beyond tidiness: `main` is the branch a supervisor clones. Right now
it shows a codebase from before the runtime unification, before episode
isolation, before the grounded diagnosis, before the intent layer reached the
runtime, and before any of the evidence was produced. The work being reported in
meetings is not on the branch being read.

## Scope

```
310 files changed, 100,090 insertions(+), 669 deletions(-)

  74 commits  Ruiyao Jiang     (Member B)
  43 commits  Yixin Yang       (Member A)
   2 commits  Fadi Ferjani     (Member C)
```

`main` is an ancestor of `develop`, so this is a clean merge with nothing to
decide during it.

### Member A (Yixin) — runtime
Primitive expected-effect verification, conflict freshness and re-observation,
recovery evidence linkage, ledger-derived live metrics, the 30×7 fusion campaign
with a locked calibration/holdout split, and the Bayesian gate promotion review.

### Member B (Ruiyao) — perception, recovery reasoning, evaluation, reproducibility
B-111 overlay isolation · B-112 CI and Docker reproducibility · B-113 measured
visual geometry · B-114 browser episode isolation · B-115 clean-clone bootstrap ·
B-116 WoT episode isolation · B-117 demo registry · B-118 grounded diagnosis and
metric intermediates · B-119 intent-driven runtime, M1, live tests, terminology.

### Member C (Fadi) — runtime integration, action context, adaptation loop

## Verification on the merge head

| gate | result |
| --- | --- |
| `pytest -q` | **520 passed** |
| `pytest -m live` (real Chromium) | **8 passed** |
| `python run_demo.py` | clean |
| `develop` → `main` conflicts | **0** |

`ruff` and `black` are **not clean repository-wide**: 13 ruff findings and 21
black files, all in `evaluation/` and a few `src/` modules owned by Member A.
They are pre-existing, unrelated to this release, and were left alone
deliberately rather than reformatted across a teammate's in-flight work. They
are the reason the `lint-test` job is red and they are worth fixing in a separate
pass. `mypy src/` reports 11 errors, all in the same set of files.

## What a reader of `main` will be able to do after this

```bash
python scripts/bootstrap.py --check          # a clean clone can install and run
python scripts/demo.py list                  # 10 demos, and which are runnable here
python scripts/run_agent_loop_demo.py        # the narrated loop, ~2.5 min
python scripts/run_intent_episode.py --suite # an utterance drives the real runtime; M1
pytest -m live                               # the live claims, against a real browser
```

## Evidence included in the release

| artifact | what it is |
| --- | --- |
| `artifacts/agent_loop_campaign_30x7/` | 210 episodes, **30 per condition** — the sample size §4 P2 requires |
| `artifacts/intent_cross_env/m1_cross_env.json` | M1 cross-environment generalisation, produced from a real run for the first time |
| `artifacts/live_runtime_demo_y_runtime_evidence/measured_metrics.json` | live runtime metrics from six Docker + Playwright episodes |
| `artifacts/live_ambiguous_fusion_*`, `artifacts/bayesian_*` | the fusion campaigns, holdouts and promotion review |

## Claims discipline

`README.md` carries a "What is implemented, and what is not" table that is
maintained in **both** directions — it was corrected twice in this cycle for
understating as well as overstating. It currently records, as unresolved:

- **No model has ever run here.** With no API key every intent and mark decision
  is `rule_fallback` / `heuristic`, and **no image is sent to a model anywhere**.
- **MiniWoB++ and the mock-environment results are scripted**, produced by
  hand-written solvers, and must not be read as agent benchmarks.
- **No real open-web evidence.** All of it is local mocks and controlled fixtures.
- **Picture-in-Picture is not implemented.** See the README Terminology section:
  PiP means a supervised interface, and browser-context isolation is not that.
- **Two agent loops exist.** The narrated demo runs its own; `run_intent_episode.py`
  is the one that drives the integrated runtime.
- **30 repetitions of a deterministic fault establish reproducibility, not
  variance.** RTA/DA at 100% is 30 identical correct answers, not a distribution.

Merging this does not resolve those. It puts them where they can be read.

## Known follow-ups, in order

1. Repository-wide `ruff`/`black`/`mypy` pass over `evaluation/` and the Member A
   modules, so the `lint-test` job goes green.
2. `scripts/run_agent_on_env.py --planner runtime` crashes on the first call
   (`asyncio.run` inside Playwright's sync loop) and has never run;
   `src/perception/session_thread.py` is the fix pattern.
3. `--success-text` in that runner checks whole-page text, so a product title in
   the listing satisfies it before the agent acts.
4. `STATUS.md` still says NL→GoalSpec is "future interface only", which B-119
   made out of date.
5. `evaluation/metrics_aggregator.py` publishes 16 of 34 metrics as `0.0` with
   empty denominators; §4 requires them marked `not_measured`.

Items 2–5 are in Member A's files and are reported rather than edited.
