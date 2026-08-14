# Open-Web Randomized Holdout Plan

Goal: turn the six fixed controlled open-web failures into reproducible seeded
variants with a locked, non-overlapping holdout, execute them through the shared
runtime and real Playwright browser, and persist auditable split-level evidence.

## Steps

1. **done** — Audit fixture/session capabilities and define a variant
   contract that changes observable fault parameters without changing labels.
2. **done** — Implement deterministic dev/holdout variant generation and
   materialization for all six failure families.
3. **done** — Extend the Playwright runner/report with split-aware execution,
   leakage checks, and per-family metrics.
4. **done** — Add unit and live-browser regression tests.
5. **done** — Generate persisted artifacts and run Ruff, Black, mypy, full
   tests, and live tests.

## Files produced or modified

- `.codex/open_web_randomized_holdout_plan.md` — this plan.
- `evaluation/open_web_randomized_holdout.py` — variant plan and split runner.
- `evaluation/open_web_playwright_fixture_runner.py` — page materialization and
  fresh-oracle evidence.
- `tests/test_open_web_randomized_holdout.py` — deterministic/leakage/report tests.
- `tests/test_live_browser_claims.py` — real Chromium variant verification.
- `artifacts/open_web_randomized_holdout/` — locked plan, split reports,
  transition/failure ledgers, and 72 browser screenshots for 36 episodes.

## Verification result

- Black: 251 files unchanged.
- Ruff: passed.
- mypy: 110 source files passed.
- pytest: 528 non-live and 9 live tests passed.
- Formal holdout: all 18 holdout failures detected across six families, zero
  false final successes, and all four leakage checks passed.

## Design decisions

- Seeds must affect injected page state/behavior; unique IDs alone do not count.
- Dev and holdout draw from disjoint parameter domains and their canonical
  signatures are checked for leakage before browser execution.
- Each split contains every failure family. The holdout is written to the plan
  before execution and is never used to select/tune runtime behavior.
- Existing six-case reports stay backward compatible; randomized evidence is a
  separate protocol and artifact directory.
