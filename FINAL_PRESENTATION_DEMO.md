# Final presentation demo

This is the runbook for the presentation-grade demo implemented by
`scripts/run_final_presentation_demo.py`.

The central scene is one real request:

> Book Room C at 15:30 and prepare it for my presentation.

That request runs through the canonical `RuntimeEpisodeRunner` once. The runner
then offers focused chapters for technical components that genuinely live on a
separate path in this repository. It records those boundaries in one manifest
instead of pretending that VLM, Set-of-Marks, the five-scene campaign, and
adaptation all ran inside the same episode.

## What has been implemented

The combined runner provides:

- strict read-only preflight for Chromium and all four smart-room endpoints;
- one timestamped, non-overwriting output directory;
- a canonical DOM + WoT episode with intent, `GoalSpec`, Skill selection,
  planning, execution, verification, safety, isolation, and restoration;
- a deterministic, occurrence-bound modal fault immediately before the first
  approved `Book Room` execution;
- a typed failure returned to the same `AgentPlanner`;
- observation-driven discovery of the modal's safe dismiss control;
- a linked and freshly verified recovery transition, followed by resumption of
  the original goal;
- optional human takeover and fresh re-observation/replanning;
- a complete Runtime laboratory for retry, rollback, conflict handling, and a
  verified System-1 cache hit;
- the five live recovery families;
- live or recorded text/VLM evidence with exact provenance;
- live Set-of-Marks geometry, mark selection, `VisualExecutor`, and visible
  effect verification;
- review-gated adaptation evidence;
- scientific outcome validators, SHA-256 artifact inventory, component
  coverage matrix, git snapshot, presenter cues, and a consolidated manifest.

The main runtime loop is:

```mermaid
sequenceDiagram
    actor U as User / Supervisor
    participant I as Intent + GoalSpec + Skill
    participant O as Observe + Fuse
    participant A as AgentPlanner
    participant R as Runtime / CIM
    participant E as DOM + WoT environment

    U->>I: Book Room C at 15:30 and prepare it
    I->>O: bounded goal + effective parameters
    O->>A: fresh sanitized ActionContext
    A->>R: one semantic primitive
    R->>E: validate, ground, execute
    E-->>O: fresh DOM/WoT evidence
    O-->>R: postcondition result
    loop Until all parameters are established
        R->>A: fresh context
        A->>R: next primitive
        R->>E: execute through DOM or WoT
        E-->>O: re-observe
    end
    R->>U: Tier-4 confirmation before Book Room
    U-->>R: approve exact pending action
    R->>E: Book Room attempt
    E-->>R: modal causes typed timeout
    R->>O: observe after failure
    O->>A: FailureContext + observed safe remediation
    A->>R: dismiss observed obstruction
    R->>E: click and verify modal absent
    R->>U: re-authorize high-risk booking
    U-->>R: approve or take over
    E-->>R: fresh evidence confirms goal
    R-->>U: verified completion; restore checkpoint
```

## Profiles and claim boundaries

| Chapter | `presentation` | `complete` | What it proves | Important boundary |
|---|---:|---:|---|---|
| `canonical` | yes | yes | One real `RuntimeEpisodeRunner` request over DOM and WoT, with recovery, verification, intervention, and restoration | This is the main story and canonical composition |
| `runtime_lab` | no | yes | Retry, rollback, source conflict, active perception, and System-1 repeat | Controlled live Runtime cases, not an open-web benchmark |
| `recovery` | yes | yes | Five live recovery families with independent final oracles | Production CIM/environment, but simulated upstream recovery client |
| `models` | yes | yes | Text-model value and VLM detection of a DOM false success | Separate measured chapter; VLM is evidence, not canonical end-to-end VAM |
| `visual` | yes | yes | Measured geometry → SoM → mark selection → `VisualExecutor` → visible effect | Focused prototype path, not canonical stages 1–3 |
| `adaptation` | yes | yes | Repeated failures → proposal → release gate | Synthetic white-box evidence; no automatic Skill mutation |

`presentation` is the curated set. `complete` additionally runs the dense live
Runtime/System-1 laboratory. The complete profile covers the important
implemented logic, but it deliberately reports prototype, recorded, and
synthetic components as such.

## One-time setup

From the repository root:

```bash
python scripts/bootstrap.py --check
```

If dependencies or Chromium are missing:

```bash
python scripts/bootstrap.py
```

Start the smart room:

```bash
docker compose -f env/docker-compose.yml up --build -d
```

The runner uses:

- dashboard: `http://127.0.0.1:3000`
- public WoT servient: `http://127.0.0.1:8080`
- failure/lease control plane: `http://127.0.0.1:8081`
- Thing Directory: `http://127.0.0.1:8082/things`

Run strict preflight. Unlike `scripts/demo.py doctor`, this command checks all
four endpoints semantically and returns non-zero when a required one is absent:

```bash
.venv/bin/python scripts/run_final_presentation_demo.py \
  --profile complete \
  --model-mode recorded \
  --check
```

Inspect the exact commands and claim boundaries without executing anything:

```bash
.venv/bin/python scripts/run_final_presentation_demo.py \
  --profile complete \
  --model-mode recorded \
  --plan
```

## Recommended rehearsal commands

First run the exhaustive technical acceptance profile. This is unattended,
headless, and fast; it is the command to run after code changes:

```bash
.venv/bin/python scripts/run_final_presentation_demo.py \
  --profile complete \
  --model-mode recorded \
  --headless \
  --auto-approve \
  --fast
```

A successful run ends with `FINAL DEMO: PASSED`. It validates all six chapters,
including linked recovery evidence, and writes a timestamped directory under
`artifacts/final_presentation_demo/`.

Run the curated headed presentation profile with handoff pauses:

```bash
.venv/bin/python scripts/run_final_presentation_demo.py \
  --profile presentation \
  --model-mode recorded \
  --pause-between-chapters
```

For a short rehearsal of only the central story:

```bash
.venv/bin/python scripts/run_final_presentation_demo.py \
  --profile presentation \
  --model-mode recorded \
  --only canonical
```

`runtime_lab` belongs only to the complete profile:

```bash
.venv/bin/python scripts/run_final_presentation_demo.py \
  --profile complete \
  --model-mode recorded \
  --only runtime_lab
```

Useful runner controls:

- `--headless`: hide Chromium for CI/rehearsal;
- `--auto-approve`: approve both high-risk attempts automatically;
- `--fast`: minimize presentation delays, not runtime verification;
- `--only canonical,visual`: run selected chapters;
- `--continue-on-error`: collect later evidence after a chapter fails;
- `--output-dir PATH`: choose an exact empty run directory;
- `--plan`: print commands and exit;
- `--check`: preflight and exit.

The output directory must be empty. This prevents a new run from looking as if
it produced artifacts left by an older run.

## Operator choices during the canonical scene

The best live sequence demonstrates both automatic recovery and human control:

1. At the first Tier-4 prompt, choose **`a`**. This authorizes the exact pending
   `Book Room` action.
2. The controlled policy modal appears. Do not touch it. Let the attempted click
   time out. Runtime knows only that the action failed; it is not given the fault
   label.
3. A fresh observation finds one structurally safe dismiss control. The same
   `AgentPlanner`, now in recovery mode, selects it. Runtime validates, executes,
   and verifies that the obstruction disappeared.
4. The high-risk booking action is proposed again. At this second Tier-4 prompt:
   - choose **`t`** for the strongest presentation: click `Book Room` yourself,
     then return to the terminal and press Enter; or
   - choose **`a`** for an entirely agent-executed rehearsal.
5. With takeover, the software input lease prevents agent executors from acting
   while the operator owns control. On resume, Runtime takes a fresh observation,
   discards the stale plan, sees that the human completed the goal, and does not
   duplicate the click.
6. Runtime verifies the final booking and restores the exact pre-episode room
   checkpoint.

This is cooperative **software input isolation**. It is not a separate desktop,
VM, Windows RDP session, or physical keyboard/mouse lock. Do not call it full
PiP isolation in the presentation.

## Ten-minute live cut and three presenters

Do not execute every complete-profile chapter live inside the ten-minute slot.
Use the complete profile as acceptance evidence and make the canonical request
the live spine. A practical timing is:

| Time | Presenter | Live content | Diagram handoff |
|---:|---|---|---|
| 0:00–1:30 | A | Say the request; show actual intent provenance, `GoalSpec`, selected Skill, defaults, and current DOM/WoT affordances | Stages 1–3 |
| 1:30–4:15 | B | Let the Agent fill room/time and actuate lights, projector, thermostat; narrate one action as validate → ground → execute → re-observe → verify | Stage 4 |
| 4:15–7:45 | C | First approval, modal failure, typed `FailureContext`, observed recovery, second authorization/takeover, final oracle, restoration | Stage 5 |
| 7:45–8:45 | A | Play a pre-cut model/SoM segment or show its evidence stills | Optional model/VAM boundary |
| 8:45–9:30 | C | Show the five-family recovery matrix and review-gated adaptation proposal from accepted artifacts | Recovery + dashed learning path |
| 9:30–10:00 | All | One-sentence conclusion and boundary statement | Whole loop |

Recommended closing line:

> This is one auditable observe–fuse–decide–act–verify loop across DOM and WoT,
> with typed recovery and human authority; visual/VLM and adaptation are explicit
> bounded extensions, not hidden inside the canonical claim.

The runner writes `presenter_cues.md` into every run directory with the chapter
order, presenter, diagram stages, and claim boundary.

## Should the LLM/VLM be configured?

No model is required for the canonical presentation. Without one:

- intent provenance is honestly `rule_fallback`;
- forward planning is deterministic and affordance-driven;
- the presentation-only recovery policy selects only a fresh affordance that
  explicitly declares a matching safe recovery relation;
- the checked-in model recording and report are validated by `--model-mode recorded`.

For the final presentation, **recorded mode is the safer default**. It removes
provider latency, quota, Wi-Fi, and response-variance risk while retaining the
measured model evidence and raw calls already checked into the repository.

Configure live models only if the team specifically wants to demonstrate a live
provider call and has rehearsed it repeatedly on the presentation network. The
simplest supported setup uses one Alibaba Model Studio key for both text and
vision in a git-ignored `.env.local` file:

```text
DASHSCOPE_API_KEY=sk-...
```

Never paste the key into a command, chat, slide, log, or commit. Check the setup
without revealing the value:

```bash
.venv/bin/python scripts/check_api_key.py
.venv/bin/python scripts/check_api_key.py --call
```

Then run the live model chapter:

```bash
.venv/bin/python scripts/run_final_presentation_demo.py \
  --profile presentation \
  --model-mode live \
  --pause-between-chapters
```

To use the text model in the canonical intent and forward/recovery planner too:

```bash
.venv/bin/python scripts/run_final_presentation_demo.py \
  --profile presentation \
  --model-mode live \
  --canonical-model \
  --pause-between-chapters
```

`--model-mode auto` chooses live only when both text and vision clients are
configured, otherwise recorded evidence when present, otherwise skip. Pin the
mode explicitly on presentation day so the behavior cannot change because an
environment variable happens to be present.

See `docs_setup/VLM_SETUP.md` for providers, cost controls, and override names.

## Evidence produced

Each run contains:

```text
<run>/
  preflight.json
  presentation_manifest.json
  component_coverage.json
  presenter_cues.md
  01_canonical/
    episode.json
    transition_ledger.jsonl
    failure_ledger.jsonl
    intervention_ledger.jsonl
    agent_planner_calls.jsonl
    screenshots/...
  02_runtime_lab/...          # complete profile only
  02_or_03_recovery/...
  04_or_05_visual/...
  05_or_06_adaptation/...
```

Recorded model evidence remains in `artifacts/llm_demo/`; the manifest records
it as repository evidence and hashes both the report and video.

The canonical validator requires all of the following:

- final goal freshly verified;
- both DOM and WoT used;
- controlled obstruction applied;
- at least one failed transition;
- the same `AgentPlanner` entered recovery mode;
- a successful recovery transition links to that exact failure;
- protected action intervention recorded;
- original room state restored.

The manifest also records every chapter's execution mode, command, duration,
return code, validation checks, artifacts, hashes, presenter, diagram stages,
claim boundary, model names, configured secret **names only**, git commit, and
dirty-worktree status.

## Recording a fallback

For the final backup video, record the headed canonical run with the macOS screen
recorder or OBS so the capture contains both the browser and terminal prompts.
Playwright-only video captures the page but not the safety prompt, model
provenance, or the evidence verdict, which are central to the story.

Recommended recording sequence:

1. Run the complete headless acceptance command and keep its passed manifest.
2. Start screen recording.
3. Run the headed presentation profile or `--only canonical`.
4. Use the `a` then `t` operator sequence above.
5. Stop after the final verified/restored summary.
6. Keep the accepted manifest beside the video and rehearse switching to it.

Label a replay on the slide as **Recorded fallback**. Do not present the existing
LLM-loop recording as if it were the canonical runtime episode; it is a separate
model-value chapter.

## Troubleshooting

### Preflight reports one or more endpoints unavailable

```bash
docker compose -f env/docker-compose.yml ps
docker compose -f env/docker-compose.yml up --build -d
```

Then rerun `--check`. The four required ports are 3000, 8080, 8081, and 8082.

### Chromium is missing

```bash
.venv/bin/python -m playwright install chromium
```

### The output directory is not empty

Omit `--output-dir` to get a fresh timestamped directory, or select a new empty
path. Do not delete an accepted run merely to reuse its name.

### The runner chooses recorded models

That is expected when a text or vision client is absent. Run
`scripts/check_api_key.py`; use `--model-mode live` to fail closed if live models
are required.

### The booking action pauses twice

That is intentional. Recovery changes the execution context, and the retried
high-risk commit requires fresh authorization. Use `a` then `t` to demonstrate
both automated recovery and supervised takeover.

### A crashed rehearsal leaves the room busy or dirty

The isolation provider restores and releases in `finally`. If the process or
Docker daemon was killed before cleanup, restart the smart-room services and run
preflight again before presenting.

### The manifest says `failed` even though a child exited zero

The runner validates scientific outcomes, not just process exit codes. Read the
chapter's `validation_checks` and `chapter.log`; the missing claim will be named.

## Day-of checklist

- Run the complete headless acceptance profile once.
- Confirm `FINAL DEMO: PASSED` and keep its manifest.
- Pin `--model-mode recorded` unless live models are intentional.
- Close unrelated browser windows and notifications.
- Open the TUM deck, dashboard, terminal, evidence folder, and fallback video.
- Use a large terminal font and the headed browser.
- Rehearse the first choice `a`, second choice `t`, manual click, and Enter.
- Keep Docker running and do not run another smart-room episode concurrently.
- Keep at least two minutes for recovery variance and presenter handoffs.
- Never claim OS-level PiP, physical-room validation, canonical VAM, or automatic
  Skill learning.
