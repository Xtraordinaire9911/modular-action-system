# Modular Action System Architecture

Smart-room demo for the TUM Automatic Agents Praktikum. The repository
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

## Current Release State

`main` contains the integrated **Week-8** release.

| Branch group | What it adds |
|---|---|
| B-101 – B-108 (Week 6) | DOM/WoT/Visual perception, System-1 effectors, cost-aware router, backend eval, browser-session retry |
| B-109 (Week 7-8) | External CUA benchmark environments: MiniWoB++ (academic) + three WebArena-style local mock envs (shopping, email, forum); cross-environment fancy demo runner with periwinkle cursor trail, env badge overlay, and M1 generalisation score table |

Branch discipline:

- feature branches merge into `develop`;
- `develop` is the integration branch for cross-member testing;
- `main` is only updated from a verified `develop` release.

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

Expected result: all tests pass. The current suite covers contracts,
DOM/WoT/SoM perception, System-1 effectors, VAM adapter, router, recovery cascade,
backend evaluation, runtime smoke path, and external CUA benchmark controllers.

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

### 3. External CUA benchmark demo (no Docker)

Two demo entry points, both in a visible Chromium window with a periwinkle arrow
cursor, glowing trail, and per-action element highlight:

**Prerequisites (one-time):**

```powershell
uv run playwright install chromium
# Only needed for the MiniWoB++ tasks:
git clone https://github.com/Farama-Foundation/miniwob-plusplus.git .external_envs/miniwob-plusplus
uv pip install miniwob
```

**Full cross-environment fancy demo** (MiniWoB++ academic + WebArena-style mock envs):

```powershell
uv run python scripts/run_fancy_demo.py --headed --step-delay 1.3
```

Runs 3 MiniWoB++ tasks (login-user, click-dialog, click-link) plus 6 mock-env
tasks across shopping, email, and forum surfaces, then prints a colour-coded M1
cross-environment generalisation score table and saves per-task screenshots to
`eval_outputs/external_runs/`. Add `--skip-miniwob` to demo the mock envs only
(no MiniWoB++ clone required).

**MiniWoB++ only:**

```powershell
uv run python scripts/run_miniwob_demo.py --step-delay 1.4 --pause-between --headed
```

For full install/troubleshooting details see `env/RUNBOOK_external_envs.md` § A2.

### 4. Live smart-room environment (Docker)

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

### 5. Live runtime-control tracer bullet

With the smart-room services running, execute the complete observe-plan-act-
verify-recover loop through one entry point:

```bash
uv run python -m src.pipeline --live-demo
```

The command runs a normal structured goal, transient WoT timeout recovery,
postcondition-mismatch rollback, DOM/WoT conflict resolution, and a repeated
System-1 grounding-cache episode. It writes live screenshots, transition and
failure JSONL ledgers, a recovery report, and episode-derived metrics under
`artifacts/live_runtime_demo/`. The System-1 repeat case includes a
`system1_latency_report` that links the warm-up and repeat episode ids and
reports cache-hit rate, routing latency, total transition latency, and
amortized latency from the same transition ledger.

Run the same seeded normal/timeout episodes under full, no-recovery, DOM-only,
and WoT-only modes:

```bash
uv run python -m src.pipeline --live-ablation
```

Calibrate the current rule-first fusion threshold over labeled live clean,
DOM-fault, WoT-timeout/offline, and postcondition-mismatch scenarios:

```bash
uv run python -m src.pipeline --fusion-calibration
```

Generate the next-stage repeated fusion/recovery campaign plan without starting
the long live run:

```bash
uv run python -m src.pipeline --fusion-campaign-dry-run --repetitions 30
```

Run the live repeated campaign when the smart-room environment is available:

```bash
uv run python -m src.pipeline --fusion-campaign --repetitions 30
```

After a full campaign is saved, create a locked calibration/holdout report:

```bash
uv run python -m src.pipeline --fusion-holdout \
  --campaign-summary artifacts/live_fusion_campaign_full/fusion_campaign_summary.json \
  --calibration-repetitions 20 \
  --holdout-repetitions 10
```

Compare an experimental Bayesian posterior model against the locked rule-first
holdout. This is a comparator report only; it does not replace the production
fusion gate:

```bash
uv run python -m src.pipeline --bayesian-fusion-comparator \
  --holdout-report artifacts/live_fusion_holdout/fusion_holdout_report.json
```

Use `--dashboard-url`, `--thing-directory-url`, `--wot-base-url`, and
`--control-url` when Docker is mapped to non-default host ports. These are live
measurements; `python -m src.pipeline --demo` remains the deterministic synthetic
white-box path.

## Demo

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
| External CUA benchmarks | `src/benchmarks/miniwob_tasks.py` (MiniwobController + MockEnvController + animated primitives), `src/benchmarks/mock_env_tasks.py` (six WebArena-style mock tasks), `scripts/run_fancy_demo.py` (unified cross-env runner). |
| PiP/session isolation | `src/perception/browser_session.py` creates an isolated Playwright context and exposes DOM/visual action protocols. |
| DOM processing | `src/perception/dom_transducer.py` strips noisy tags, extracts interactables, derives selectors, labels, actions, state, and PAM metadata. |
| PAM | `src/perception/page_affordance_model.py`. |
| WoT TD parsing | `src/perception/td_affordance_parser.py`, including HATEOAS forms, methods, security, rate limits, state sources. |
| System-1 reflex library | `src/effectors/system1_reflex_library.py`. |
| Execution effectors | `src/effectors/dom_executor.py`, `src/effectors/wot_executor.py`, `src/effectors/visual_executor.py`. |
| VAM / System 2 | `src/vam/vam_adapter.py`, `src/vam/vam_payload.py`. |
| CMap / SSG-style runtime state | `src/runtime/cognitive_map.py` is the canonical episode state; `src/planner/cognitive_map.py` derives a read-only Semantic Scene Graph view from it. |
| Epistemic arbitration | `src/verification/conflict_detector.py` is the canonical fusion/arbiter implementation used by CIM and the planner-facing gate. |
| Backend routing | `src/runtime/backend_router.py` is the canonical routing core; `src/backend_router/router.py` preserves cost-aware and legacy APIs as adapters. |
| Preconditions/postconditions | `src/verification/precondition_checker.py`, `src/verification/postcondition_checker.py`. |
| Recovery/failure injection | `src/recovery/*`, `scripts/inject_failures.py`, `evaluation/backend_eval.py`. |

## Repository Layout

```text
config/
  default.yaml              Runtime defaults for router/executors/env URLs
  skills_seed.json          Initial smart-room skills
  wot_td/                   Canonical TD fixtures
env/
  docker-compose.yml        Smart-room Docker environment (node-wot + React dashboard)
  mock_envs/                Self-contained HTML mock environments (Week 7)
    shopping.html             WebArena-style e-commerce surface
    email_inbox.html          WebArena-style email client surface
    forum.html                WebArena-style discussion board surface
  node_wot_server/          node-wot servient + failure control plane
  react_dashboard/          React/Vite dashboard
  RUNBOOK_external_envs.md  Setup and troubleshooting for external CUA benchmarks
evaluation/
  backend_eval.py           Backend B1-B5 table harness + CLI baseline
  cross_env_eval.py         Cross-environment M1 generalisation metric
  integration_eval.py       Deterministic normal/recovery traces
scripts/
  inject_failures.py        Failure catalogue and live WoT fault injector
  run_fancy_demo.py         Cross-environment fancy demo (MiniWoB++ + mock envs)
  run_miniwob_demo.py       MiniWoB++-only curated demo suite
  run_agent_on_env.py       Single-task agent runner with static file server
src/
  backend_router/           Cost-aware compatibility adapters and confidence tracking
  benchmarks/               External CUA benchmark controllers and task suites
    miniwob_tasks.py          MiniwobController, MockEnvController, DEMO_TASKS
    mock_env_tasks.py         MockEnvTask definitions and solvers for mock envs
  contracts/                Shared dataclasses (Affordance, ExecutionResult, …)
  effectors/                DOM/WoT/Visual executors and System-1 reflexes
  perception/               DOM transducer, TD parser, SoM parser, browser session
  planner/                  Read-only runtime-map view, planning gate, System-2 packaging
  recovery/                 Retry/reroute/rollback/escalation policies
  runtime/                  Canonical state, planning, routing, and interaction control
  safety/                   Unsafe-action detector, rate limiter
  skill_library/            Canonical skill definitions and fixture loader
  vam/                      VAM adapter and recovery payload
  verification/             Preconditions, postconditions, conflict detection
tests/                      Unit, integration, recovery, and live-adapter tests
.external_envs/             Cloned external benchmark repos (MiniWoB++, WebArena, …)
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
  artifacts from earlier branches. Use `env/docker-compose.yml` for the smart-room env.
