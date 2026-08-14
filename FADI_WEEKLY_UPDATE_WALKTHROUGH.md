# Fadi's weekly update: supervised takeover toward PiP

This is a simple 5–7 minute update. The safest title is **GoalSpec-to-Skill with
supervised takeover / isolation toward PiP**. It is not the full Windows RDP PiP
from the UFO2 paper.

## Before the meeting

For the visual version, start the smart-room environment and check that the
dashboard opens:

```bash
docker compose -f env/docker-compose.yml up --build -d
.venv/bin/python scripts/run_fadi_demo.py --headed --step-delay 2
```

`--step-delay 2` leaves two seconds after each browser action, which makes the
room and time changes easier to explain while everyone watches.

Keep this fallback command ready. It shows the same action-system flow without
Docker or a browser:

```bash
.venv/bin/python scripts/run_fadi_demo.py --dry-run
```

Both commands write the main evidence file to:

```text
artifacts/fadi_weekly_demo/episode.json
```

## What to say and show

### 1. Explain the small problem (about 45 seconds)

Say:

> We already had GoalSpec, a Skill Library, primitive actions, isolation, and
> human takeover. The missing part was one clear path that joins them together.
> A structured booking goal did not actually select the reusable booking Skill.

Show `src/runtime/goal_spec.py` and `config/skills_seed.json` only briefly.

### 2. Explain the change (about 60 seconds)

Say:

> I added a small selector. It matches the GoalSpec ID to a Skill, checks the
> parameters, and creates a SkillCall. The existing action-system runtime then
> turns that SkillCall into typed actions such as type, type, and click.

Show these files in this order:

1. `src/planner/goal_skill_selector.py` — the small GoalSpec-to-Skill bridge.
2. `src/runtime/continuous_interaction_manager.py` — calls the selector and
   records the selected contract in the result.
3. `src/runtime/live_environment.py` — lets a semantic binding mark the final
   booking button as high risk.

Do not read every line. Point at the class/function names and explain their job.

### 3. Run the visual demo (about 2 minutes)

Run:

```bash
.venv/bin/python scripts/run_fadi_demo.py --headed
```

Expected flow:

1. A clean browser context is created for the episode.
2. The terminal prints the GoalSpec and selected `confirm_booking` Skill.
3. The agent types the room and time into the visible browser.
4. The runtime pauses before the high-risk **Book Room** click.
5. Enter `t` in the terminal to take control.
6. Click **Book Room** yourself in the browser.
7. Return to the terminal and press Enter.
8. The agent takes a fresh observation, sees the booking is already confirmed,
   and does not click the button again.
9. The episode finishes, restores the saved WoT state, and closes the isolated
   browser context.

If the live services are unavailable, run `--dry-run`. Say clearly that this is
a deterministic rehearsal with fake browser/WoT adapters, while the real CIM,
isolation provider, intervention broker, planner, and ledgers still run.

### 4. Show the result (about 60 seconds)

Open `artifacts/fadi_weekly_demo/episode.json` and point to:

- `goal_spec` and `selected_skill`: the goal selected a real contract.
- `generated_primitive_plan`: the runtime produced `type → type → click`.
- `human_interventions`: the decision is `resume`, with `reobserved` and
  `replanned` set to `true`.
- `agent_executor_calls`: only the two typing actions ran when the human did the
  final click.
- `result.final_outcome_verified`: the fresh observation confirmed success.
- `isolation.room_state_restored`: cleanup restored the original state.

### 5. End with the scope (about 30 seconds)

Say:

> This is the first project-level milestone: clean browser/WoT sessions and a
> real pause/takeover/resume flow. It is useful for our current web smart-room
> project. It is not yet UFO2's Windows RDP child desktop. That future version
> would add a separate visible Windows session, independent desktop input, and a
> Windows host application.

## Very short code walkthrough

Use this one-sentence flow:

```text
GoalSpec -> GoalSkillSelector -> SkillCall -> CIM primitive planner
         -> safe actions -> Tier-4 pause -> human takeover
         -> fresh observation/replan -> verified result -> isolation cleanup
```

The important point is that the demo script only prepares the environment and
shows the operator menu. The main planning, execution, pause/resume, verification,
and cleanup remain in the existing action-system components.

## Likely questions

**Is this action-system logic?**  Yes. The selector and CIM integration are part
of the action-system control path. The demo script is only a thin adapter around
that path.

**Is this full PiP?**  No. It is the cross-platform Project PiP MVP: browser/WoT
isolation plus supervised takeover. The Windows RDP child desktop remains future
work.

**Why is the final click marked high risk?**  To demonstrate a deterministic
human checkpoint before a committing action. Other actions remain normal.

**Does Resume continue the old plan?**  No. Resume forces a new observation and
replan. If the human already finished the task, the agent does not repeat it.

**Do the old demos still work?**  Yes. This adds a focused demo and keeps the old
zero-shot goal path for goals that do not match a Skill. The existing smart-room
Docker environment and runtime components are reused.
