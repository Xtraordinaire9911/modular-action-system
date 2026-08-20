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


### What the language model actually changes

- **[Play the current recording](artifacts/llm_demo/llm-vs-rules-smartroom.mp4)** — the
  smart room, four scenes, 2.1 MB. *(Not embedded above: an inline player needs a
  GitHub-hosted copy of the file, which has to be uploaded through the web UI. The
  repository-relative link plays after download.)*
- [Scene 3, the one that leaves the browser](artifacts/llm_demo/scene3-device-over-wot.png) —
  the thermostat panel after a setpoint was written over WoT and the room caught up
- [Inspect the run report](artifacts/llm_demo/run-report.json) — carries the surface
  each scene acted on, so a run that only touched the page cannot look like one that
  touched a device
- [Inspect every vision call this recording made](artifacts/llm_demo/vision-calls.jsonl), and [every intent call](artifacts/llm_demo/intent-calls.jsonl) — model, latency, raw reply, screenshot digest
- [Inspect the evaluation behind the claims](artifacts/model_value/model_value_report.json)
- [The previous recording, on the shopping mock](artifacts/llm_demo/llm-vs-rules.mp4) — kept
  because it is a real run, but it is the earlier scenario, not the use case

Four scenes **in the smart room**, each running the rule-based path and the model
path **on the same sentence, at the same time**, because a caption saying "sent
to a language model" looks identical whether a model ran or not. On screen: all
twelve fallback patterns and which matched; the request sent and the model's raw
reply, revealed line by line, with latency and provider-reported token counts;
the exact image bytes handed to the vision model, rendered in the page, beside
its answer in its own words; and running totals for calls, tokens and model time.

The room has two surfaces and the run touches both, which is the point of the use
case rather than a detail of it. **What the device layer is** is stated plainly
below the table, because "physical" is a word worth being careful with here:

| scene | what is said | surface | why it is there |
|---|---|---|---|
| 1 | "book room A at 14:00" | dashboard (DOM) | the control — a keyword sentence, where the model earns nothing |
| 2 | "I need somewhere to present at 15:00, room B please" | dashboard (DOM) | same goal, no pattern matches; the model interprets it |
| 3 | "it's too cold, put it at 22 please" | **device (WoT)** | no control on the page can do it — the target is resolved from the room's own Thing Descriptions and read back from the device |
| 4 | "hold room C for me at 16:00" | dashboard (DOM) | the confirmation is in the DOM and painted over on screen |

Scene 3 is the surface an ordinary browser agent does not have: the action leaves
the browser, and the dashboard is where it becomes visible again. Scene 4 is the
decisive one for the vision model — every text-based check in this repository
passes there, and only looking catches it. Result of the recorded run: **rules
1/4, model 4/4, one false success caught**, on 8 model calls.
`qwen-plus` for intent, `qwen-vl-plus` for vision.

#### What the device layer actually is

It is a node-wot servient: real W3C Thing Descriptions, discovered at runtime,
with forms, methods, security schemes and `readOnly` honoured. Where to write is
resolved from what the room publishes, never from a table in the code, and a test
asserts no binding contains a URL or a port. That much is a faithful WoT
environment and the agent's device path is exercised end to end.

It models **timing and compliance**, which is the part that makes actuating
something different from setting a value. A setpoint is accepted at once and the
room arrives later: `targetTemperature` changes immediately, `currentTemperature`
ramps; blinds have travel time; the projector lamp warms before it is on. Two
faults produce a 2xx write, a setpoint that reads back correctly, and a room that
never complies — a dead lamp, and a jammed motor. Nothing in the transport goes
wrong in either.

It does **not** model thermodynamics, ambient coupling, sensor noise or drift,
physical interaction between devices, or any hardware. It runs at **30× real
time** so a demo fits in a meeting, and `GET :8081/state` reports that scale
alongside the real per-device rates rather than leaving a reader to infer that a
room reaching temperature in two seconds is not a claim about rooms.

So: this is a protocol-faithful and timing-faithful **stand-in** for a device
layer. It is evidence about the agent — that it separates having commanded from
having achieved, and that it can be shown a failure only a physical actuator
produces. It is **not** evidence about physical systems, and no number measured
here transfers to real hardware.

```bash
python scripts/run_llm_demo.py --pace 1.25 --type-delay 0.1 --hold 2.5
```

The browser is visible by default; add `--headless` for a dry run with no window,
and `--record` to write the mp4 (which adds its own encoding time after the run,
so time the demo without it). Needs `docker compose up` first — the room is the
environment, not a fixture.

Timed on this machine against the real endpoints, headed, no recording:
**1 minute 50 seconds**, with about 10 real seconds of margin under two minutes
for a slower network on the day. It is 11 seconds longer than the shopping
version it replaced, and the extra time is scene 3 doing something that scene
could not: resolving a device from the room's Thing Descriptions, writing to it,
and reading the value back before the page is consulted at all.

`--pace` scales every beat by the same factor, so the beats that carry new
information — the resolved device, the read-back, the injected fault, the model's
own words — stay proportionally longer than the ones that exist to move the scene
along. The verdict at the end of each scene is deliberately one of the short
ones: it is a single line, and the panel stays up while the next scene loads.

### The narrated agent loop, with no model in it


https://github.com/user-attachments/assets/534785b5-c984-429d-98cd-01703a5dd41b


Seven scenes over a shop, a forum and a WoT device. Six inject a different
real-world fault; the agent measures the page after each failure and picks a
recovery tier from what it measured, without being told which fault was
injected. Entirely deterministic — no model is involved anywhere in it, which is
the reason the recording above exists as a separate demo. Run it with
`python scripts/run_agent_loop_demo.py`.

### Five-family Runtime recovery contract demo


https://github.com/user-attachments/assets/ee7b469a-8176-44da-81bd-c7f6ff4945e7


- [Open the same file from the repository](artifacts/runtime_recovery_demo/five-family-runtime-recovery.mp4)
- [Open the full-size visual preview](artifacts/runtime_recovery_demo/preview.png)
- [Inspect the Runtime run report](artifacts/runtime_recovery_demo/runtime-report.json)
- [Inspect the transition ledger](artifacts/runtime_recovery_demo/transition_ledger.jsonl)
- [Inspect the failure ledger](artifacts/runtime_recovery_demo/failure_ledger.jsonl)
- [Inspect the raw screenshots](artifacts/runtime_recovery_demo/screenshots/)
- [Inspect the claim-boundary manifest](artifacts/runtime_recovery_demo/demo-manifest.json)

The recording contains five controlled recovery scenes and all five finish with
a fresh, independent final oracle. It demonstrates the real Runtime handoff,
proposal validation, execution, re-observation, recovery-postcondition check,
continuation, and final verification path. Upstream Planner feedback and the
VLM feedback used by the DOM/visual scene are explicitly simulated; this video
does not claim a production Planner, production VLM, or unrestricted open-web
run.


## What is implemented, and what is not

This table is the one to read before believing anything else in this file. It is
kept deliberately blunt because the value of the project rests on its claims
matching its code.

| Capability | State | What that means precisely |
|---|---|---|
| Observe → plan → act → verify → recover | **Implemented** | Runs end to end in one process; see `scripts/run_agent_loop_demo.py`. |
| Affordance contract across DOM / WoT / Visual | **Implemented** | One planner drives all three; no per-surface branching in the planning path. |
| Intent (natural language) → GoalSpec | **Implemented, and it reaches the runtime** | `src/planner/intent_planner.py`. With an API key a model interprets; **without one a phrasing-rule fallback runs and is labelled `rule_fallback`**, never as understanding. `scripts/run_intent_episode.py` takes the resulting `GoalSpec` (stamped `source="user_intent_parser"`) into `RuntimeEpisodeRunner.run_goal_episode` and the `ContinuousInteractionManager` on a live page. |
| Set-of-Marks target selection | **Implemented, demo path only** | `src/planner/mark_selector.py`. Same rule: a model answers with a `mark_id` when configured, otherwise deterministic scoring answers and is labelled `heuristic`. Unlike the intent layer above, this one is still consumed only by the narrated demo — the runtime picks affordances through its own action context. |
| A model actually running | **Running, and measured** | Both layers run against a real endpoint (`qwen-plus`, `qwen-vl-plus`), and `scripts/eval_model_value.py` asks whether they earn their place. **Intent** — on twelve requests phrased to avoid the fallback's keywords the rules score **0/12** and the model **12/12**, with no regression on the three the rules already handled and **5/5** correct refusals. Three of the twelve are room bookings, which is what the demo runs on, so the demonstrated capability is not the one without evidence behind it. Two of the refusals are booking requests this agent cannot serve — a flight and a restaurant table — because a model that had learned "booking words mean `room_booked`" would take both, and refusing them is what separates understanding the capability from matching its keywords. **Vision** — against three separate ways a page can be right in the DOM and wrong on screen (painted over, rendered in the background colour, laid out off-screen): detection **100%** over 11 trials where the DOM is wrong, false alarm **0%** over 8 where it is right, and the model never changed its mind between repetitions. One call in this run failed in transport and is reported as such rather than dropped, which is why the DOM-wrong sample is 11 and not the 12 the conditions would otherwise give. Evidence in `artifacts/model_value/`. |
| Verification independent of the executor | **Implemented, two modalities** | The page or device is re-read; a backend reporting success is not treated as task success. A vision model independently judges the region the goal names, and its answer enters the arbiter as a `source="visual"` assertion, so a goal is confirmed by two sources or a disagreement becomes a conflict. That catches the one class of false success a text oracle cannot see: a confirmation present in the DOM and absent from the screen. |
| Devices resolved from discovery, not from code | **Implemented, verified against the servient** | `src/planner/device_binding.py` names a *kind* of Thing and a property; the concrete write target comes from the Thing Descriptions fetched at runtime from the directory. No binding contains a URL or a port, and a test asserts that. A Thing the directory does not offer makes the goal unsupported rather than being approximated with the nearest device. `pytest -m smartroom` asserts this against the running servient. |
| Composite device goal (`room_prepared`) | **Implemented, verified per property** | One sentence resolves to four writable properties across four Things. Each is written and then **read back**, and the goal is met only where the value that comes back is the value asked for — the servient answers a write that changed nothing with a success status, so the status is not the evidence. A part the room does not have is reported as skipped when the declaration marks it optional and fails the goal when it does not. `scripts/run_room_prepared.py --ignore lights.brightness` drops one write on purpose and the run reports NOT PREPARED. |
| Failure diagnosis | **Implemented** | Four probes measure the live page after a failure (`src/demos/probes.py`); the conclusion is drawn from those measurements and nothing is told which fault was injected. |
| Recovery | **Runtime boundary implemented; Planner integration pending** | Runtime returns typed `FailureContext` plus fresh observed capabilities through `PlannerPort`, validates any returned primitive, executes it, re-observes, verifies, and resumes. Runtime does not choose recovery semantics. The five capability fixtures are ready, but end-to-end autonomous recovery now waits for the Planner owner. Autocomplete remains outside Runtime recovery scope. |
| Generalisation evidence (M1) | **Produced, and small** | `scripts/run_intent_episode.py --suite` runs seven spoken requests over two environments through the real runtime and writes the M1 table (`artifacts/intent_cross_env/`). Six tasks over two local mocks of similar shape: a working generalisation harness, not a generalisation result. |
| Model confidence as a safety gate | **Weak, and calibrated rather than assumed** | `qwen-vl-plus` reports 1.00 on every clear observation and 0.90 on a region cut off mid-word; that is its whole range. The abstention threshold was 0.55 — a value no answer ever came near, so the gate could not fire. It is now 0.95, set from the measurement, and it is model-specific: another model needs `scripts/eval_model_value.py` re-run. **A confident wrong answer still passes the gate.** What makes a wrong answer safe is the arbiter turning a disagreement into a conflict, which does not consult confidence. |
| Sample sizes behind the demo metrics | **At the bar, with a caveat** | `--repeat 30` gives 210 episodes, 30 per condition, saved in `artifacts/agent_loop_campaign_30x7/`. The faults and the diagnosis are deterministic, so 30 repetitions establish **reproducibility, not variance** — RTA/DA at 100% means 30 identical correct answers, not an estimated distribution. A default single run is n=1 per fault and must not be quoted. |
| Live behaviour is tested | **Implemented** | `pytest -m live` opens a real Chromium and asserts the claims in this table against a real page (selector uniqueness, measured geometry, episode isolation, region-scoped verification, the probes). CI runs it as its own job. The fast suite excludes it and cannot corroborate any live claim on its own. |
| Two agent loops in the repository | **Known duplication** | `scripts/run_agent_loop_demo.py` implements its own observe/plan/act/verify/recover rather than driving `src/runtime/continuous_interaction_manager.py`. It is the narration surface and is honest about what it runs, but it is a second loop. `scripts/run_intent_episode.py` is the one that drives the integrated runtime; the demo has not been migrated onto it. |
| MiniWoB++ 12/12 result | **Scripted, not agent-driven** | Those tasks are solved by hand-written solvers in `src/benchmarks/`. The number measures the solvers, not the agent, and must not be read as an agent benchmark. |
| Real open-web validation | **Not implemented** | All evidence is local mock environments and controlled fixtures. |
| Picture-in-Picture supervised interface | **Implemented for the web, not for Windows** | See Terminology below. `src/isolation/episode.py` and `src/runtime/intervention.py` give a serialized episode its own browser context, checkpoint/restore of WoT state, an input lease, and a supervised pause a person can take over from. That is a genuine supervised interface. What is **not** claimed is the UFO2 Windows form: no child desktop, no OS-level input or process boundary. Browser-context isolation on its own is still not PiP. |

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

Human oversight now exists in two places. `src/recovery/supervised_takeover.py`
pauses a tier-4 episode, records what the supervisor decided and reports a
correction rate. On top of that, `src/isolation/episode.py` and
`src/runtime/intervention.py` give an episode its own browser context and WoT
checkpoint, and hand the input lease to a person who can take over mid-episode -
which does meet the definition above, for the web.

What is still not claimed is the Windows form in the paper: a child desktop over
RDP with an independent OS input and process boundary. Two properties in this
repository were being described with the word "PiP" before any of that existed,
and both are still not it on their own:

The module formerly called `pip_console` is now `narration_console`, for the
same reason.

## Branch Discipline

`main` is a verified release of `develop`, kept content-identical after each
release merge. Feature branches merge into `develop`; `develop` is where
cross-member integration and CI happen; `main` only moves forward from a
`develop` state that has already passed the full suite, including the `live`
and `smartroom` markers.

## Commands

Trimmed hard on purpose: dozens of `src.pipeline` evaluation flags from
earlier weeks (fusion calibration, Bayesian holdouts, open-web randomized
suites) used to live here and are now in
[`YIXIN_RUNTIME_RECOVERY_DOSSIER.md`](YIXIN_RUNTIME_RECOVERY_DOSSIER.md),
which is where anyone still running them should be looking anyway. What is
left is the current demo, the full test suite, and the handful of commands
this project actually reaches for.

### The demo

Needs the smart room up (see below). Same command as under the recording at the
top of this file:

```bash
docker compose -f env/docker-compose.yml up -d
python scripts/run_llm_demo.py --pace 1.25 --type-delay 0.1 --hold 2.5
```

Measured headed against the real endpoints: **1:50**. See the paragraph under
the first recording above for what the parameters do and why.

### Clean clone to running demo, one command

```bash
git clone <repo-url> && cd A-Modular-Action-System-Architecture
python scripts/bootstrap.py --demo --headed
```

Installs, downloads the one browser the demos need, runs the full test suite,
then runs the visual demo. Standard library only, so it works before any
dependency is installed. `--check` reports the environment and stops;
`--skip-install --demo` re-runs it on a machine already set up.

### Full test suite

```bash
uv run --with pytest pytest        # fast: contracts, perception, effectors,
                                    # router, recovery, runtime — a few seconds
uv run --with pytest pytest -m live       # + 14 tests against a real Chromium
uv run --with pytest pytest -m smartroom  # + 6 tests against the running servient
```

The fast suite never opens a browser or a socket, so it cannot corroborate a
live claim on its own — that is what the other two markers are for. `smartroom`
needs the Docker services below running first.

### Start the smart room

```bash
docker compose -f env/docker-compose.yml up --build
```

Dashboard on `:3000`; the Things and their Thing Descriptions on `:8080`; the
failure control plane on `:8081`; the runtime Thing Directory on `:8082`, which
is what the agent asks which devices exist. `python run_demo.py --probe-env`
checks all of it is reachable and reports `environment.all_ok`.

### Drive the production runtime from one sentence

```bash
python scripts/run_intent_episode.py --utterance "add the wireless headphones to my cart"
python scripts/run_intent_episode.py --suite    # 7 utterances, 2 environments, the M1 table
```

The sentence goes through `IntentPlanner`, the resulting `GoalSpec` into
`RuntimeEpisodeRunner` on a live page, and a vision model verifies the region
the goal names. `--suite` also prints which utterances the rule fallback could
not have handled.

This one runs on the **local mock environments**, not the smart room: it serves
them from its own static server, which is what lets it run with no Docker and no
fixed port. It is the generalisation harness — the same runtime over a second
kind of page — and it says so rather than pretending the shop is the use case. A
goal that belongs to the dashboard is declined here by name, with the command
that does serve it.

### Prepare the room from one sentence

```bash
python scripts/run_room_prepared.py
python scripts/run_room_prepared.py --utterance "get the room ready, lights at 15"
python scripts/run_room_prepared.py --ignore lights.brightness   # drop one write on purpose
```

Resolves against whatever the Thing Directory actually reports — no device
endpoint is named anywhere in the code — writes each property, and reads every
one back before calling the goal met. `--ignore` reproduces the servient
answering 204 to a write that changed nothing, which is exactly the failure
the read-back step exists to catch.

### What else is runnable, and why something isn't

```bash
python scripts/demo.py list      # every registered demo and its status
python scripts/demo.py doctor    # why one is not runnable on this machine, and the fix
```

## Architecture Map

| Requirement | Implementation |
| --- | --- |
| Experiment with SOTA ideas | SoM/VAM path follows OmniParser-style mark selection; code separates perception, grounding, execution, and recovery. |
| WoT environment in Docker | `env/docker-compose.yml`, `env/node_wot_server/server.js`, `config/wot_td/*.td.json`. |
| React dashboard / CUA surface | `env/react_dashboard/src/App.jsx` at port `3000`. |
| External CUA benchmarks | `src/benchmarks/miniwob_tasks.py` (MiniwobController + MockEnvController + animated primitives), `src/benchmarks/mock_env_tasks.py` (six WebArena-style mock tasks), `scripts/run_fancy_demo.py` (unified cross-env runner). |
| Session isolation | `src/perception/browser_session.py` creates an isolated Playwright context and exposes DOM/visual action protocols. This is browser-context isolation, **not** Picture-in-Picture — see Terminology below. |
| Project PiP MVP | `src/isolation/episode.py` provisions a fresh browser context, checkpoints/resets/restores WoT state, serializes episodes, and transfers the input lease during human takeover. `src/runtime/intervention.py` records supervised Tier-4 decisions. |
| Full UFO2 Windows PiP | Future Windows-specific provider; RDP child desktop and independent OS input/process isolation are not claimed by this MVP. |
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
