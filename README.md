# Modular Action System Architecture

Week-6 smart-room demo for the TUM Automatic Agents Praktikum. The repository
shows how an agent can perceive and act across three surfaces without hard-coded
UI or device assumptions:

- DOM: a React dashboard is transduced into a Page Affordance Model (PAM).
- WoT: W3C Thing Descriptions are parsed at runtime into executable affordances.
- Visual: screenshots are represented through Set-of-Marks (SoM) targets so the
  VAM selects a `mark_id`, not raw coordinates.

The demo is intentionally small, but it is wired end to end around the core
architecture requested for this week: DOM Transduction Pattern -> PAM -> runtime
Cognitive Map, System-1 reflex execution, backend routing, pre/postcondition
checks, recovery hooks, and failure-injection evaluation.

## Current Git State

`main` should only receive code after `develop` contains the integrated demo and
the checks below pass. At the time this README was prepared, the Week-6 member-B
work lives in PR branches `feature/B-101` through `feature/B-105`; those branches
must be merged into `develop` before `develop` is promoted to `main`.

Recommended merge order into `develop`:

```bash
feature/B-101-perception-affordance-layer
feature/B-102-system1-effectors-vam
feature/B-103-cost-aware-router
feature/B-104-node-wot-react-env
feature/B-105-backend-eval-failure-injection
```

After all five are on `develop`:

```bash
git checkout develop
git pull origin develop
uv run --with pytest pytest
python run_demo.py
git checkout main
git pull origin main
git merge --no-ff develop
git push origin main
```

## Quick Start

### 1. Python verification

Use `uv` if available:

```bash
uv run --with pytest pytest
```

Or use a regular virtual environment:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

Expected result for the integrated Week-6 branch: all tests pass. The current
suite covers contracts, DOM/WoT/SoM perception, System-1 effectors, VAM adapter,
router, recovery cascade, backend evaluation, and the runtime smoke path.

### 2. Deterministic offline demo

This does not require Docker or a browser. It writes presentation artifacts:

```bash
python run_demo.py
```

Outputs:

- `artifacts/demo_trace_normal.json`
- `artifacts/demo_trace_recovery.json`
- `artifacts/recovery_metrics.json`

Use this path when the meeting room cannot run Docker. It still demonstrates the
runtime trace shape, postcondition verification, conflict detection, and recovery
metrics.

### 3. Live smart-room environment

Start the React dashboard and node-wot servient:

```bash
docker compose -f env/docker-compose.yml up --build
```

Open:

- Dashboard: http://localhost:3000
- WoT thermostat TD: http://localhost:8080/thermostat
- WoT lights TD: http://localhost:8080/lights
- WoT projector TD: http://localhost:8080/projector
- Failure control plane: http://localhost:8081/state

In another terminal, verify the environment and write demo artifacts:

```bash
python run_demo.py --probe-env
```

The `environment.all_ok` field should be `true` when Docker services are up.

## What To Show The Tutor

1. Open http://localhost:3000.
   Show the concrete DOM surface: booking inputs, Book Room button, thermostat,
   lighting, projector, and readiness panels.

2. Open http://localhost:8080/thermostat.
   Show that the device surface is a runtime Thing Description with `forms`,
   `href`, `htv:methodName`, `securityDefinitions`, and schemas.

3. Run:

   ```bash
   python run_demo.py --probe-env
   ```

   Explain that this proves the environment endpoints are reachable and that the
   agent-side demo artifacts are regenerated.

4. Run:

   ```bash
   uv run --with pytest pytest tests/test_dom_transducer.py tests/test_td_parser.py tests/test_som_parser.py
   ```

   Explain perception:

   - DOM is stripped of noise and converted to stable selectors and labels.
   - WoT TDs are parsed dynamically, including security and rate limits.
   - Visual regions become SoM marks with bbox/center metadata.

5. Run:

   ```bash
   uv run --with pytest pytest tests/test_system1_executors.py tests/test_backend_router.py tests/test_backend_eval.py
   ```

   Explain action:

   - System 1 executes cached DOM/WoT/Visual affordances.
   - Router uses cost, reliability, and latency.
   - VAM is a System-2 fallback, not the default path.
   - Backend evaluation produces B1-B5 style tables.

6. Show failure injection:

   ```bash
   curl -X POST http://localhost:8081/failure ^
     -H "Content-Type: application/json" ^
     -d "{\"thing\":\"thermostat\",\"type\":\"postcondition_mismatch\"}"
   curl -X POST http://localhost:8081/reset
   ```

   DOM-side faults can be shown by opening:

   ```text
   http://localhost:3000/?fault=layout_shift,selector_mutation
   ```

## Architecture Map

| Requirement | Implementation |
| --- | --- |
| Experiment with SOTA ideas | SoM/VAM path follows OmniParser-style mark selection; code separates perception, grounding, execution, and recovery. |
| WoT environment in Docker | `env/docker-compose.yml`, `env/node_wot_server/server.js`, `config/wot_td/*.td.json`. |
| React dashboard / CUA surface | `env/react_dashboard/src/App.jsx` at port `3000`. |
| PiP/session isolation | `src/perception/browser_session.py` creates an isolated Playwright context and exposes DOM/visual action protocols. |
| DOM processing | `src/perception/dom_transducer.py` strips noisy tags, extracts interactables, derives selectors, labels, actions, state, and PAM metadata. |
| PAM | `src/perception/page_affordance_model.py`. |
| WoT TD parsing | `src/perception/td_affordance_parser.py`, including HATEOAS forms, methods, security, rate limits, state sources. |
| System-1 reflex library | `src/effectors/system1_reflex_library.py`. |
| Execution effectors | `src/effectors/dom_executor.py`, `src/effectors/wot_executor.py`, `src/effectors/visual_executor.py`. |
| VAM / System 2 | `src/vam/vam_adapter.py`, `src/vam/vam_payload.py`. |
| CMap / SSG-style runtime state | `src/runtime/cognitive_map.py` and `src/planner/cognitive_map.py`. |
| Preconditions/postconditions | `src/verification/precondition_checker.py`, `src/verification/postcondition_checker.py`. |
| Recovery/failure injection | `src/recovery/*`, `scripts/inject_failures.py`, `evaluation/backend_eval.py`. |

## Repository Layout

```text
config/
  default.yaml              Runtime defaults for router/executors/env URLs
  skills_seed.json          Initial smart-room skills
  wot_td/                   Canonical TD fixtures
env/
  docker-compose.yml        Canonical Week-6 Docker environment
  node_wot_server/          node-wot servient + failure control plane
  react_dashboard/          React/Vite dashboard
evaluation/
  backend_eval.py           Backend B1-B5 table harness + CLI baseline
  integration_eval.py       Deterministic normal/recovery traces
scripts/
  inject_failures.py        Failure catalogue and live WoT fault injector
src/
  backend_router/           Cost-aware routing and confidence tracking
  contracts/                Shared dataclasses
  effectors/                DOM/WoT/Visual executors and System-1 reflexes
  perception/               DOM transducer, TD parser, SoM parser, browser session
  recovery/                 Retry/reroute/rollback/escalation policies
  runtime/                  State machine and runtime cognitive map
  safety/                   Unsafe-action/rate-limit helpers
  vam/                      VAM adapter and recovery payload
  verification/             Preconditions, postconditions, conflict detection
tests/                      Unit and integration-smoke tests
```

## Demo Troubleshooting

- If `python` opens the Microsoft Store on Windows, use `uv run ...` or activate
  a real virtual environment first.
- If `python run_demo.py --probe-env` reports endpoint failures, start Docker
  with `docker compose -f env/docker-compose.yml up --build`.
- If Docker cannot download npm packages, keep the offline demo path; it is
  deterministic and still produces the artifacts needed for discussion.
- If the dashboard loads but WoT values stay stale, check `http://localhost:8080/thermostat`
  and `http://localhost:8081/state`.
- `env/docker-compose.mock.yml` and the Flask `app.py` files are legacy mock
  artifacts from earlier branches. Use `env/docker-compose.yml` for Week-6.
