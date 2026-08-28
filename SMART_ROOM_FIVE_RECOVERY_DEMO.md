# Five-scene live Smart Room recovery demo

This is the runnable Smart Room demo, not the earlier MP4 or the open-web mock
contract recording. It starts from the real React dashboard and Eclipse
Thingweb node-wot servient, injects one fault per episode, and sends ordinary
DOM/WoT observations and executor results through the production Runtime/CIM.

## Run it

```bash
docker compose -f env/docker-compose.yml up --build -d
python run_demo.py --probe-env
python scripts/run_smart_room_five_recovery_demo.py
```

The browser is headed by default for a presentation. Use `--headless` for an
unattended acceptance run. The command exits non-zero unless all five
independent final oracles pass and no injected fault label crossed the Planner
boundary.

## What the five scenes exercise

| Scene | Injected at | Observable failure | Recovery path |
| --- | --- | --- | --- |
| Overlay obstruction | React dashboard | the presentation control is covered | fresh obstruction probe publishes a safe dismiss affordance; Runtime executes and verifies it |
| Session expiry | React dashboard | the command is not sent because the room session is expired | the Planner selects the observed renew-session primitive, then resumes the original goal |
| Optimistic rollback | node-wot control plane | the write is briefly visible and then rolls back | post-action observation catches the rollback; the Planner selects direct device control |
| Dashboard/device disagreement | DOM projection versus node-wot state | the two evidence sources disagree before action | Fusion blocks action, active perception refreshes the stale projection, then normal planning continues |
| Ineffective affordance | React dashboard | the UI accepts the command but the projector lamp remains off | final-effect verification fails; the Planner reroutes to observed direct device control |

Every scene follows the same control loop:

```text
fresh observation -> fusion/validation -> one primitive -> guarded execution
-> fresh verification -> typed failure -> recovery replan -> recovery primitive
-> recovery verification -> resume original goal -> independent final oracle
```

## Evidence and claim boundary

The run writes these files under `artifacts/smart_room_five_recovery/`:

- `smart_room_recovery_report.json`: per-scene outcomes and independent oracle
- `transition_ledger.jsonl`: original, failed, recovery and resumed transitions
- `failure_ledger.jsonl`: typed failure evidence
- `planner_calls.jsonl`: effective Planner choices
- `screenshots/`: fresh observations captured by the Runtime

The environment, Playwright/WoT execution, observation, validation, recovery
cascade, verification and ledgers are live production paths. Fault injection is
owned by a control plane that is not passed to Runtime or Planner. The demo
uses `ModelRecoveryPlanner` with an explicitly labelled deterministic local
upstream client so it is reproducible without an API key; it does not claim a
production external LLM or VLM.

## Shut down

```bash
docker compose -f env/docker-compose.yml down
```
