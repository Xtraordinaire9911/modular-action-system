# chore: absorb main into develop so a release stops conflicting

**Branch:** `chore/sync-main-into-develop` → `develop`
**Scope:** Member B (Ruiyao) — branch hygiene
**Merge this first.** It is the reason several pull requests are stuck.

---

## The problem, and why it looked like seven unrelated file conflicts

Every blocked pull request reported conflicts in the same seven files:

```
artifacts/recovery_metrics.json          evaluation/robustness_eval.py
evaluation/chaos_monkey.py               run_demo.py
evaluation/metrics_aggregator.py         tests/test_chaos_monkey.py
evaluation/randomized_fixture_generator.py
```

Those files are not the problem. `main` carries four commits `develop` does not,
and three of them are a **revert of "Add chaos-ready evaluation demo"** (#47,
reverted by #51). So `main` deliberately lacks the chaos evaluation modules,
while `develop` has them *and* has since rewritten `metrics_aggregator.py`,
`run_demo.py` and `recovery_metrics.json` around them.

Any branch touching that area conflicts with `main`, and any release from
`develop` would silently re-litigate a revert nobody was looking at.

## What was checked before resolving

| question | answer |
| --- | --- |
| Does any file exist on `main` but not on `develop`? | **No** — `comm` over both trees is empty. |
| Is `main`'s only non-revert commit already on `develop`? | **Yes** — the MiniWoB `alink`/overlap fix is present at `src/benchmarks/miniwob_tasks.py:232`. |
| Would taking `develop`'s side lose anything? | **No.** |

## What this does

Merges `origin/main` into `develop`, resolving all seven in favour of `develop`.
The result is verified to leave the tree **byte-identical to `develop`**:

```bash
git diff --name-only origin/develop    # empty
```

Six further files were being **deleted silently** rather than reported as
conflicts, because `main` removed them and `develop` had not touched them since
the merge base:

```
artifacts/chaos_demo_trace_live.json     tests/test_oracle_verifier.py
artifacts/robustness_eval_report.json    tests/test_randomized_fixture_generator.py
src/verification/oracle_verifier.py      tests/test_robustness_eval.py
```

`src/verification/oracle_verifier.py` breaks test collection outright when it
goes missing. All six are restored. This is the part a hand-resolution in the
GitHub web editor would have got wrong: the conflict list does not mention them.

## Verification

- `pytest -q` → **502 passed** on the merged tree
- `python run_demo.py` → clean
- `git diff --name-only origin/develop` → empty

## What it unblocks

- **`develop` → `main`** becomes conflict-free. Right now `main` is 113 commits
  behind and nothing on it is newer, so `main` shows the supervisor a codebase
  from before all of this work.
- **PR #67** stops conflicting. It is branched off `main`, is missing 113
  commits of `develop`, and its only own commit is an empty *"Initial plan"* by
  the Copilot bot — merging it into `develop` as it stands would delete ~95,000
  lines across 296 files. Once `develop` contains `main`, that PR adds nothing
  and should be **closed rather than merged**; its stated purpose (fixing CI
  lint) is not addressed by any commit on it.

## Note on the revert being superseded

This keeps the chaos evaluation modules that `main` had reverted. They are on
`develop`, their tests pass there, and `develop` has built on top of them since.
If the revert was meant to stand, that is a decision to make deliberately and
not by leaving two branches disagreeing — say so and this can be flipped, but it
should not keep blocking releases in the meantime.
