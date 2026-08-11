# feat(setup): one documented path from a clean clone to a running demo

**Branch:** `feature/B-115-clean-clone-setup` → `develop`
**Scope:** Member B (Ruiyao) — setup entrypoints, ports, clean-clone runbook
**Reference:** `current_codebase_full_analysis_en.md`, PDF §5 D10, §6

---

## Problem

Setup was spread across `README.md` and `env/RUNBOOK_external_envs.md`, each
assuming a different tool (`uv` vs `pip`) and a different starting point. There
was **no single command a new machine could follow**, and no entry point that
installs, verifies, and then *shows* the demo.

"Clone it and show me" therefore meant reading two documents and knowing which
parts to skip — which is exactly the failure mode the analysis document flags.

## Fix

`scripts/bootstrap.py` is the one path:

```bash
git clone <repo-url> && cd A-Modular-Action-System-Architecture
python scripts/bootstrap.py --demo --headed
```

| Invocation | Does |
|---|---|
| `--check` | reports the environment and stops |
| *(none)* | install + test |
| `--demo` | install + test + demo (headless) |
| `--demo --headed` | same, with a visible browser |
| `--skip-install --demo` | re-run on an already-set-up machine |

### Standard library only — the point, not a detail

The script runs **before** the project dependencies exist. If it imported
anything installable it would fail at the exact moment it is meant to help, and
the failure would look like a broken repository rather than a missing
dependency. `tests/test_bootstrap_entrypoint.py` parses the file and enforces
this.

### Degrades instead of erroring

| Situation | Behaviour |
|---|---|
| No `uv` | falls back to `pip` |
| No MiniWoB++ clone | reported; demo runs the local mock environments and says so |
| Chromium download fails | reported; setup continues, only browser demos are affected |
| A demo script is absent from this checkout | skipped, not an error |

Every command is echoed before it runs, so a failure names something you can
repeat by hand.

## Ports

The README now states the port model explicitly, because "path/port drift" is
part of the same finding:

- **No fixed ports** for local runs. Every local server binds `127.0.0.1:0`, so
  the OS picks a free port. This avoids the Windows reserved ranges claimed by
  Docker/Hyper-V (which surface as `WinError 10013`) and makes concurrent runs
  safe.
- The only fixed ports (3000, 8080, 8081) belong to the **optional** Docker
  smart-room environment, and are listed with it.

## Tests

`tests/test_bootstrap_entrypoint.py` (4):

| Test | Asserts |
|---|---|
| `test_entrypoint_exists_and_parses` | the documented path exists and is valid Python |
| `test_entrypoint_imports_only_the_standard_library` | **nothing installable is imported** |
| `test_entrypoint_does_not_import_the_project` | no `src` / `scripts` / `evaluation` imports |
| `test_check_reports_this_repository_as_usable` | `check()` passes here; `MIN_PYTHON == (3, 11)` |

## Verification

| Check | Result |
|---|---|
| `ruff` on changed files | All checks passed |
| `black --check` on changed files | unchanged |
| `pytest --tb=short -q` (full suite) | **326 passed** |
| `bootstrap.py --check` | exit 0, environment reported |
| `bootstrap.py --skip-install` | exit 0, 322 tests run through the entry point |

> `ruff check .` / `black --check .` / `mypy src/` currently fail on `develop`
> itself because of unrelated files under `evaluation/`. Those are untouched
> here; every file changed by this branch passes individually.

## Compatibility

- Additive: one new script, one new test file, one new README section.
- No existing command, script or module changed.
- Merges cleanly into the current `develop`; no file overlap with `B-113` or `B-114`.
