# Supervised smart-room walkthrough

This is the short, shared demo for a weekly meeting. It takes about 3–5 minutes.

Use the five-slide deck first:

```text
output/presentations/Supervised_Smartroom_Session_Update.pptx
```

The simple speaker notes are already inside the PowerPoint. The intended order
is: idea and scope → ideal versus implemented → pipeline → evidence → demo and
code walkthrough.

## The idea in one sentence

The agent gets a clean browser and a saved copy of the room, works across the
dashboard and simulated devices, pauses before the risky final click, and puts
the room back exactly as it found it.

This is called **supervised session isolation**. It is not the separate Windows
desktop from the UFO2 paper.

## Before the meeting

From the repository root:

```bash
docker compose -f env/docker-compose.yml up --build -d
.venv/bin/python scripts/demo.py doctor
```

The doctor should report `browser` and `smart_room` as ready.

Run one unattended rehearsal:

```bash
.venv/bin/python scripts/run_supervised_smartroom_demo.py \
  --auto-approve --headless --step-delay 0
```

Expected ending:

```text
Runtime result   : completed
Backends used    : dom, wot
State restored   : True
```

This unattended run saves approval evidence. It proves that the shared runner
used DOM and WoT, verified the result, and restored the room. It is not takeover
evidence: because the operator approved the pending action, `reobserved` and
`replanned` are correctly `false`.

## What to say before running it

“Previously, my supervised isolation only existed in a separate booking demo.
Now it is connected to the shared RuntimeEpisodeRunner. The same episode uses
the web dashboard and the WoT devices, and it always restores the room.”

## Run the visual demo

```bash
.venv/bin/python scripts/run_supervised_smartroom_demo.py
```

What should happen:

1. A Chromium window opens with a fresh browser context.
2. The intent layer turns the sentence into `room_session_prepared`.
3. The reusable Skill `prepare_and_confirm_room` is selected.
4. The agent fills Room C and 15:30.
5. It sets lighting, projector power and thermostat target over WoT.
6. Every action is followed by a fresh observation and a check.
7. The runtime pauses before **Book Room**.

At the terminal, choose:

```text
t
```

Click **Book Room** yourself in Chromium. Return to the terminal and press
Enter.

The agent must not repeat the old click. It observes the page again, sees that
the booking is already complete, and finishes. Cleanup restores the exact room
state captured before the episode.

## Evidence to show

Open:

```text
artifacts/supervised_smartroom/episode.json
```

Point out these fields:

- `selected_skill`: the reusable Skill chosen from the semantic goal.
- `result.goal_skill_selection.parameters`: the Skill adds its declared device
  defaults; the original GoalSpec itself remains unchanged.
- `surfaces_used`: should include `dom` and `wot`.
- `transitions`: the typed actions and their verification result.
- `interventions`: in the saved unattended artifact the decision is `approve`
  and both `reobserved` and `replanned` are `false`.
- `room_state_restored`: should be `true`.
- `os_input_isolation`: deliberately `false`.

After the interactive `t` takeover, open the newly written artifact again. That
run should record `resume`, `reobserved: true`, and `replanned: true`, because
the runtime must inspect the human's change and build a fresh plan. The
`os_input_isolation` field keeps the claim honest: software agent actions are
gated by the lease, but this project does not block the computer's physical
mouse or keyboard.

## Very small code walkthrough

Show only these files:

1. `src/runtime/episode_runner.py`
   - This is now the shared entry point.
   - With an isolation provider, it provisions before the first observation.
   - It calls the isolated runtime path and cleanup runs in `finally`.

2. `src/isolation/episode.py` and `src/isolation/input_lease.py`
   - The provider saves/resets/restores the browser and room.
   - The lease changes `agent → human → agent → none`.
   - Guarded executors check the lease before every software action.

3. `src/planner/goal_skill_selector.py`
   - A stable semantic goal such as `room_session_prepared` selects the reusable
     `prepare_and_confirm_room` Skill.

4. `scripts/run_supervised_smartroom_demo.py`
   - This file only connects the real environment and prints the demo.
   - Planning, safety, intervention, verification and cleanup remain in the
     shared action-system modules.

5. `tests/test_runtime_episode_runner.py` and `tests/test_input_lease.py`
   - These prove lifecycle ordering, cleanup after failure and input ownership.

## Difference from UFO2 Windows PiP

What this milestone provides:

- Fresh browser state for each episode.
- Saved and restored simulated room state.
- Human pause/takeover/resume.
- Fresh observation and replan after takeover.
- Software-level agent input blocking while the human owns control.

What it does not provide:

- A separate Windows desktop.
- Independent OS keyboard and mouse queues.
- Separate applications and operating-system processes.
- A security boundary for files, network or credentials.

The WoT episode lease also coordinates only supervised sessions that use this
provider. A direct API client or another external writer can still change the
shared simulated room.

## If the live environment fails

Check:

```bash
.venv/bin/python scripts/demo.py doctor
docker compose -f env/docker-compose.yml ps
```

The older component rehearsal still works without Docker:

```bash
.venv/bin/python scripts/run_supervised_session_demo.py --dry-run
```

Say clearly that this is a synthetic contract rehearsal, not the live evidence.

## Copy into the weekly meeting protocol

> Fadi integrated supervised session isolation into the shared smart-room
> runtime. A natural-language request now produces an unchanged semantic
> GoalSpec, selects and instantiates a reusable Skill, runs DOM and WoT actions
> through RuntimeEpisodeRunner, pauses before the protected booking action, and
> restores the original room state. Person-specific demo names were removed.
> The feature is deliberately not called full PiP because it does not provide a
> separate Windows/RDP desktop or OS-level mouse and keyboard isolation. Current
> verification: 758 fast tests and 16 smart-room tests passed. Next possible
> step: a Windows/VM/RDP isolation provider if full desktop PiP is required.
