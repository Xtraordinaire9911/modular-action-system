# Modular Action System Architecture

Smart-room project for the TUM Automatic Agents Praktikum. The system perceives
and acts across three surfaces through one affordance contract, so no part of
the agent hard-codes a selector, a device API, or a screen coordinate:

- **DOM** — a live page is transduced into a Page Affordance Model (PAM):
  stable locators, labels, action types, ranked by how reliable each locator is.
- **WoT** — W3C Thing Descriptions are parsed at runtime into the same
  affordance shape, including forms, security schemes and rate limits.
- **Visual** — elements are numbered as Set-of-Marks targets, so the visual path
  selects a `mark_id` rather than a raw coordinate.

Around that contract sits a complete loop — observe, plan, act, verify,
recover — with backend routing, pre/postcondition checks, a recovery cascade,
and failure-injection evaluation.


https://github.com/user-attachments/assets/534785b5-c984-429d-98cd-01703a5dd41b


## What is implemented, and what is not

This table is the one to read before believing anything else in this file. It is
kept deliberately blunt because the value of the project rests on its claims
matching its code.

| Capability | State | What that means precisely |
|---|---|---|
| Observe → plan → act → verify → recover | **Implemented** | Runs end to end in one process; see `scripts/run_agent_loop_demo.py`. |
| Affordance contract across DOM / WoT / Visual | **Implemented** | One planner drives all three; no per-surface branching in the planning path. |
| Intent (natural language) → GoalSpec | **Implemented, and it reaches the runtime** | `src/planner/intent_planner.py`. With an API key a model interprets; **without one a phrasing-rule fallback runs and is labelled `rule_fallback`**, never as understanding. `scripts/run_intent_episode.py` takes the resulting `GoalSpec` (stamped `source="user_intent_parser"`) into `RuntimeEpisodeRunner.run_goal_episode` and the `ContinuousInteractionManager` on a live page. `STATUS.md` still says "future interface only" and is now out of date on this row. |
| Set-of-Marks target selection | **Implemented, demo path only** | `src/planner/mark_selector.py`. Same rule: a model answers with a `mark_id` when configured, otherwise deterministic scoring answers and is labelled `heuristic`. Unlike the intent layer above, this one is still consumed only by the narrated demo — the runtime picks affordances through its own action context. |
| A model actually running | **Not exercised** | No API key is configured, so every recorded intent and mark decision in this repository is `rule_fallback` / `heuristic`. The model paths have unit tests against fakes and have never run against a real model. **No image is ever sent to a model** — there is no VLM anywhere in the repository. |
| Verification independent of the executor | **Implemented** | The page or device is re-read; a backend reporting success is not treated as task success. |
| Failure diagnosis | **Implemented** | Four probes measure the live page after a failure (`src/demos/probes.py`); the conclusion is drawn from those measurements and nothing is told which fault was injected. |
| Recovery | **Implemented, four tiers** | All four are exercised by the loop demo and are genuinely different actions: retry, clear the obstruction, satisfy the precondition, hand over. Which tier is used is decided at run time from what was measured. Scored against ground truth the diagnosis never sees. |
| Generalisation evidence (M1) | **Produced, and small** | `scripts/run_intent_episode.py --suite` runs seven spoken requests over two environments through the real runtime and writes the M1 table (`artifacts/intent_cross_env/`). Six tasks over two local mocks of similar shape: a working generalisation harness, not a generalisation result. |
| Sample sizes behind the demo metrics | **At the bar, with a caveat** | `--repeat 30` gives 210 episodes, 30 per condition, saved in `artifacts/agent_loop_campaign_30x7/`. The faults and the diagnosis are deterministic, so 30 repetitions establish **reproducibility, not variance** — RTA/DA at 100% means 30 identical correct answers, not an estimated distribution. A default single run is n=1 per fault and must not be quoted. |
| Live behaviour is tested | **Implemented** | `pytest -m live` opens a real Chromium and asserts the claims in this table against a real page (selector uniqueness, measured geometry, episode isolation, region-scoped verification, the probes). CI runs it as its own job. The fast suite excludes it and cannot corroborate any live claim on its own. |
| Two agent loops in the repository | **Known duplication** | `scripts/run_agent_loop_demo.py` implements its own observe/plan/act/verify/recover rather than driving `src/runtime/continuous_interaction_manager.py`. It is the narration surface and is honest about what it runs, but it is a second loop. `scripts/run_intent_episode.py` is the one that drives the integrated runtime; the demo has not been migrated onto it. |
| MiniWoB++ 12/12 result | **Scripted, not agent-driven** | Those tasks are solved by hand-written solvers in `src/benchmarks/`. The number measures the solvers, not the agent, and must not be read as an agent benchmark. |
| Real open-web validation | **Not implemented** | All evidence is local mock environments and controlled fixtures. |
| Picture-in-Picture supervised interface | **Not implemented** | See Terminology below. What exists is browser-context isolation plus a tier-4 handover that pauses and records a human decision. Both are weaker than a supervised PiP interface and neither is a substitute for it. |

Every runtime decision records whether it came from a model or a deterministic
fallback, and both paths are written to a JSONL ledger under `artifacts/`, so
the distinction can be audited rather than taken on trust.

## Terminology: what Picture-in-Picture means here

The review corrected this team on the term, and the correction is recorded here
rather than only in a commit message, because the misreading had propagated into
a module name, a docstring and a claims row.

**Picture-in-Picture, in the referenced work, is a supervised interface.** The
agent operates in a visibly separate session that a person can watch while it
runs and take over from at any point. It is a human-oversight mechanism. It is
not a window style, and it is not the same thing as giving each episode its own
sandbox.

Two properties in this repository were being described with that word and are
not it:

| what it is | what it gives you | what it is not |
|---|---|---|
| **Browser-context isolation** (`src/perception/browser_session.py`) | one episode cannot observe or disturb another: separate cookies, storage, cache | no human can watch or intervene; there is nothing to take over |
| **Narration panel** (`src/demos/narration_console.py`) | a viewer can read what the agent is doing and why, while it happens | read-only; it displays, it does not hand control to anyone |

The closest thing the project actually has to human oversight is the tier-4
handover in `src/recovery/supervised_takeover.py`, which pauses the episode,
records what the supervisor decided, and reports a correction rate. That is a
real oversight mechanism and it is still not a PiP interface.

The module formerly called `pip_console` is now `narration_console`, for the
same reason.

## Current Release State

`main` contains the integrated **Week-8** release.

| Branch group | What it adds |
|---|---|
| B-101 – B-108 (Week 6) | DOM/WoT/Visual perception, System-1 effectors, cost-aware router, backend eval, browser-session retry |
| B-109 (Week 7-8) | External CUA benchmark environments: MiniWoB++ plus three WebArena-style local mock envs (shopping, email, forum), and a cross-environment runner reporting per-environment task success. Solvers are scripted, so the figures measure the suite, not the agent. |
| B-111 to B-117 (Week 9-11) | Perception hardened against demo-overlay contamination; browser and WoT episode isolation with verified rollback; visual marks measured in the live browser instead of read from fixtures; a demo registry; and the intent and Set-of-Marks planning layers, each recording whether a model or a deterministic fallback produced its answer |

Branch discipline:

- feature branches merge into `develop`;
- `develop` is the integration branch for cross-member testing;
- `main` is only updated from a verified `develop` release.

## Quick Start

### 0. From a clean clone, in one command

On a machine that has only Python 3.11+ and `git`:

```bash
git clone <repo-url> && cd A-Modular-Action-System-Architecture
python scripts/bootstrap.py --demo --headed
```

That installs the project and dev dependencies, downloads the one browser the
demos need, runs the full test suite, and then runs the visual demo. It uses the
standard library only, so it works before any dependency is installed, and it
echoes every command it runs so a failure points at something you can repeat by
hand.

| Invocation | Does |
|---|---|
| `python scripts/bootstrap.py --check` | reports the environment and stops |
| `python scripts/bootstrap.py` | install + test |
| `python scripts/bootstrap.py --demo` | install + test + demo (headless) |
| `python scripts/bootstrap.py --demo --headed` | same, with a visible browser |
| `python scripts/bootstrap.py --skip-install --demo` | re-run on an already-set-up machine |

Notes:

- The MiniWoB++ clone is optional. Without it the demo runs the local mock
  environments only and says so; see step 3 to add it.
- **No fixed ports.** Every local server binds to `127.0.0.1:0`, so the OS picks
  a free port at run time. This avoids the Windows reserved-port ranges that
  Docker/Hyper-V claim (which surface as `WinError 10013`) and makes concurrent
  runs safe. The only fixed ports in the project belong to the optional Docker
  smart-room environment (3000, 8080, 8081), listed in step 4.

The sections below are the individual pieces, for when you want to run just one.

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

Run synthetic ambiguous/noisy fusion stress cases to see whether the Bayesian
comparator has a plausible role before designing live ambiguous cases:

```bash
uv run python -m src.pipeline --noisy-fusion-stress --repetitions 30
```

Generate the controlled open-web mock failure suite. This writes oracle-labeled
local fixtures and a coverage report for open-web-style failure modes such as
overlay obstruction, session expiry, async validation mutation, DOM/visual
disagreement, optimistic UI/backend mismatch, and visible-but-ineffective
affordances. It is controlled mock evidence, not real open-web evidence:

```bash
uv run python -m src.pipeline --open-web-mock-failure-suite
```

Run those controlled mock cases through the same runtime episode envelope. This
uses an in-memory mock executor plus fresh oracle re-observation to verify that
executor success is not treated as task success:

```bash
uv run python -m src.pipeline --open-web-mock-runtime-suite
```

Run the same local fixtures through real Playwright Chromium execution before
the runtime verifies the fresh oracle state:

```bash
uv run python -m src.pipeline --open-web-playwright-fixture-suite
```

Run seeded behavioral variants for all six families with a locked holdout.
The plan is written before execution, dev/holdout parameter signatures are
checked for leakage, and both splits are verified from fresh page oracle state:

```bash
uv run python -m src.pipeline --open-web-randomized-holdout \
  --open-web-dev-repetitions 3 --open-web-holdout-repetitions 3
```

This remains controlled local-browser evidence, not real open-web evidence.

Plan or smoke-test live ambiguous profiles mapped onto the current smart-room
fault API:

```bash
uv run python -m src.pipeline --live-ambiguous-fusion-dry-run --repetitions 30
uv run python -m src.pipeline --live-ambiguous-fusion --repetitions 1
uv run python -m src.pipeline --live-ambiguous-fusion --repetitions 30
```

The live ambiguous profiles use fine-grained smart-room fault controls such as
`stale_offset`, `read_delay_ms`, `drop_probability`, and
`source_reliability`; these are evaluation hooks, not changes to the production
fusion gate.

The runtime arbiter can also be constructed with
`EpistemicArbiter(fusion_strategy="bayesian_gate")` for gated evaluation. This
uses the Bayesian posterior to decide `allow_system1` / active perception while
keeping the existing fused-state selection logic. The default remains
`rule_first`.

Build a locked holdout from the live ambiguous campaign and compare the
production rule-first gate against the Bayesian strategy in shadow mode:

```bash
uv run python -m src.pipeline --live-ambiguous-fusion-holdout \
  --live-ambiguous-summary artifacts/live_ambiguous_fusion_full/live_ambiguous_fusion_summary.json \
  --calibration-repetitions 20 \
  --holdout-repetitions 10

uv run python -m src.pipeline --fusion-ablation-report \
  --holdout-report artifacts/live_ambiguous_fusion_holdout/live_ambiguous_fusion_holdout_report.json

uv run python -m src.pipeline --live-ambiguous-fusion \
  --repetitions 30 \
  --seed-start 5300 \
  --output-dir artifacts/live_ambiguous_fusion_rerun

uv run python -m src.pipeline --live-ambiguous-fusion \
  --fusion-strategy bayesian_gate \
  --repetitions 30 \
  --seed-start 7300 \
  --output-dir artifacts/live_ambiguous_fusion_bayesian_gate_full

uv run python -m src.pipeline --live-ambiguous-fusion-holdout \
  --live-ambiguous-summary artifacts/live_ambiguous_fusion_rerun/live_ambiguous_fusion_summary.json \
  --output-dir artifacts/live_ambiguous_fusion_rerun_holdout \
  --calibration-repetitions 20 \
  --holdout-repetitions 10

uv run python -m src.pipeline --bayesian-shadow-stability \
  --holdout-reports \
    artifacts/live_ambiguous_fusion_holdout/live_ambiguous_fusion_holdout_report.json \
    artifacts/live_ambiguous_fusion_rerun_holdout/live_ambiguous_fusion_holdout_report.json
```

Review promotion/impact/open-web coverage artifacts after the gate-enabled
runs:

```bash
uv run python - <<'PY'
from evaluation.bayesian_gate_promotion_review import write_bayesian_gate_promotion_review
from evaluation.gate_enabled_recovery_impact import write_gate_enabled_recovery_impact_report
from evaluation.open_web_failure_coverage import write_open_web_failure_coverage_report

write_bayesian_gate_promotion_review(
    "artifacts/live_ambiguous_fusion_bayesian_gate_full/live_ambiguous_fusion_summary.json",
    "artifacts/bayesian_shadow_stability/bayesian_shadow_stability_report.json",
    "artifacts/bayesian_gate_promotion_review",
)
write_gate_enabled_recovery_impact_report(
    "artifacts/live_runtime_demo_y_runtime_evidence/measured_metrics.json",
    "artifacts/live_runtime_demo_bayesian_gate/measured_metrics.json",
    "artifacts/gate_enabled_recovery_impact",
)
write_open_web_failure_coverage_report("artifacts/open_web_failure_coverage")
PY
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

## The narrated agent loop

One command, one browser window, roughly five minutes:

```bash
python scripts/run_agent_loop_demo.py
```

A side panel narrates every step — which phase of the loop it is, what is
happening in plain language, why the step exists, and the source that is
executing, with the highlight following the interpreter's real path through it.
The running counts the metrics are computed from sit along the bottom.

Seven scenes across a shop, a forum and a WoT device. Six inject a different
fault taken from things that break real automation, ordered easy to hard, and
each says on screen why that fault happens in practice:

| scene | fault | what the agent has to work out |
| --- | --- | --- |
| shop | layout shift (CLS) | the click missed; look again — tier 1 |
| forum | consent banner | present and enabled, but something else took the click — tier 2 |
| shop | unmet precondition | it refuses input; satisfy what it depends on — tier 3 |
| shop | optimistic rollback | accepted, then undone; retrying is provably useless — tier 4 |
| forum | session expiry | the page is gone; no route remains — tier 4 |
| shop | none | a clean run, for contrast |
| smart room | silent device write | 204 with no state change, caught only by reading back — tier 4 |

Nothing tells the recovery code which fault was injected. It measures the page
after the failure — what is really at the click point, whether the target
accepts input, what covers it, whether the region changed — and those
measurements pick the tier. The expected answers live in the scene definition,
which the diagnosis never sees.

```bash
python scripts/run_agent_loop_demo.py --headless --pace 0.05 --hold 0   # fast check, ~20s
python scripts/run_agent_loop_demo.py --repeat 30                       # 210 episodes, the campaign metrics
python scripts/run_agent_loop_demo.py --pace 1.5 --trace-delay 0.3 --record   # the recording settings
python scripts/run_agent_loop_demo.py --scene forum.html                # one surface
```

The recording at the top of this file is one of these runs, so the loop can be
watched without installing a browser.

The run takes about two and a half minutes. The first scene is narrated at full
length because it teaches the loop; from the second scene on, the beats
explaining a phase already shown are shortened and the ones carrying new
information - which fault, what was measured, which tier and why - keep their
timing. Nothing is skipped, and `--pace` scales all of it.

Artifacts land in `eval_outputs/agent_loop/<timestamp>/`: a screenshot per
scene, `trajectory.json`, `campaign.json` and `metric_ledger.json` — the last
of which states the division performed behind every figure, so a number can be
checked rather than trusted.

## Running the demos

Demos live across several scripts and `src.pipeline` flags. One command lists
them all and says which are runnable on this machine:

```bash
python scripts/demo.py list
python scripts/demo.py doctor          # why something is not runnable, and the fix
python scripts/demo.py run cross-env --headed
python scripts/demo.py run --all       # every currently runnable demo
```

```text
DEMO               STATUS       TIME     TITLE
------------------------------------------------------------------------------
offline            ready        ~5s      Deterministic offline trace
visual-grounding   ready        ~15s     Visual grounding smoke trace
mock-envs          ready        ~1min    WebArena-style mock environments
cross-env          ready        ~2min    Cross-environment suite (academic + industrial)
miniwob            ready        ~1min    MiniWoB++ curated suite
live-runtime       needs setup  ~2min    Live runtime tracer bullet
adaptation         ready        ~10s     Adaptation and policy proposal
```

`offline` needs no browser, no clone and no Docker, so there is always something
to show. The individual scripts are unchanged and still run directly; the
registry only discovers them.

**Adding a demo** is one entry in `src/demos/registry.py` — no runner change:

```python
Demo(
    name="my-demo",
    title="What it shows",
    summary="One line for the listing.",
    command=("scripts/run_my_demo.py",),
    requires=("browser",),          # browser | miniwob | smart_room
    headed_args=("--headed",),
    duration_hint="~30s",
)
```

A demo whose script is not in the current checkout is listed as `not here`
rather than raising, so the registry stays valid while a feature is in review.

## Architecture Map

| Requirement | Implementation |
| --- | --- |
| Experiment with SOTA ideas | SoM/VAM path follows OmniParser-style mark selection; code separates perception, grounding, execution, and recovery. |
| WoT environment in Docker | `env/docker-compose.yml`, `env/node_wot_server/server.js`, `config/wot_td/*.td.json`. |
| React dashboard / CUA surface | `env/react_dashboard/src/App.jsx` at port `3000`. |
| External CUA benchmarks | `src/benchmarks/miniwob_tasks.py` (MiniwobController + MockEnvController + animated primitives), `src/benchmarks/mock_env_tasks.py` (six WebArena-style mock tasks), `scripts/run_fancy_demo.py` (unified cross-env runner). |
| Session isolation | `src/perception/browser_session.py` creates an isolated Playwright context and exposes DOM/visual action protocols. This is browser-context isolation, **not** Picture-in-Picture — see Terminology below. |
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
