# Runtime Recovery Integration Demo Contract

## What this demo proves

Runtime publishes a fresh `FailureContext` and observed recovery capabilities
through `PlannerPort`. It validates, executes, freshly observes, verifies, and
continues only after an externally owned Planner returns a primitive proposal.
This repository intentionally contains no Yixin-authored recovery selector.

The production path does not branch on fixture ID, failure-family name, button
text, or a known selector. The five browser scenes are witnesses for one
contract:

```text
failed transition
  -> fresh FailureContext + fresh observed affordances
  -> externally supplied PlannerPort implementation
  -> generic relation: remediates / restores / compensates / observes / equivalent_to
  -> Runtime validation + primitive execution
  -> fresh recovery_postcondition
  -> resume original goal when needed
  -> independent final oracle
```

Autocomplete is intentionally absent. It remains a false-success detection
witness and is not part of Runtime recovery ownership.

## Integration command

Without an injected Planner implementation, this command demonstrates the
typed handoff and deterministic escalation, not autonomous recovery:

```bash
python -m src.pipeline \
  --generalized-browser-recovery \
  --open-web-dev-repetitions 1 \
  --open-web-holdout-repetitions 0 \
  --headed \
  --output-dir artifacts/friday_capability_recovery_demo
```

After the Planner owner connects an implementation, the same runner can be used
for three development and three locked-holdout variants per family:

```bash
python -m src.pipeline \
  --generalized-browser-recovery \
  --open-web-dev-repetitions 3 \
  --open-web-holdout-repetitions 3 \
  --output-dir artifacts/generalized_browser_recovery
```

## Demo-only recording with simulated upstream feedback

For a Runtime recovery demonstration before the Planner and VLM owners finish
their integrations, run:

```bash
python -m scripts.record_recovery_contract_demo
```

The recording script injects `DemoPlannerStub` through `PlannerPort` and
simulates the visual feedback needed by the DOM/visual scene. These dependencies
are disclosed in `demo_manifest.json` but omitted from the on-screen narration;
the video cards describe only the Runtime path. Both stubs live only in the
recording script. Runtime validation, execution, fresh observation,
recovery-postcondition checking, continuation, transition ledger, and final
oracle are the real project path.

This video may be used to claim that the Runtime recovery contract works when
its upstream interfaces provide valid feedback. It must not be used to claim
that the production Planner or VLM is complete.

## Five visible scenes

| Scene | First failure | Capability chosen from fresh observation | What the audience sees |
|---|---|---|---|
| Overlay obstruction | Target click is blocked | `remediates` | Agent closes the observed obstruction, then retries the original target. |
| Session expiry | Save executes but session oracle remains invalid | `restores` / `remediates` | Agent renews the session, verifies it, then resumes the same save goal. |
| Optimistic rollback | UI suggests success but backend oracle is false | `compensates` | Agent executes compensation, fresh observation exposes a valid alternate commit path, then completes through it. |
| DOM/visual disagreement | Selection is not consistent across observations | `observes` / `remediates` | Agent requests a fresh state recheck, verifies consistency, then resumes the original selection. |
| Ineffective affordance | Click executes but required state does not change | `equivalent_to` | Agent selects a freshly observed equivalent affordance and reaches the original goal without retry looping. |

The DOM/visual scene depends on the generic active-perception interface. VLM
invocation and evidence belong to the perception owner and are not implemented
by Yixin's Runtime code.

## Code walkthrough positions

| Responsibility | Code location |
|---|---|
| DOM capability metadata becomes observed affordance metadata | `src/perception/dom_transducer.py` |
| Fresh failure evidence enters the existing Agent context | `src/runtime/action_context.py` |
| Planner injection contract | `src/runtime/planner_port.py` |
| Runtime executes, re-observes, verifies, and returns to Agent | `src/runtime/continuous_interaction_manager.py` |
| Generic active-perception interface | `src/verification/active_perception.py` |
| Five-family browser orchestration and evidence writing | `evaluation/generalized_browser_recovery.py` |
| Randomized development/holdout IDs and labels | `evaluation/open_web_randomized_holdout.py` |
| Environment-provided recovery capabilities | `env/mock_envs/failure_*.html` |

## Result locations and truthful claim boundary

- Summary: `artifacts/generalized_browser_recovery/generalized_browser_recovery_report.json`
- Runtime transitions: `artifacts/generalized_browser_recovery/transition_ledger.jsonl`
- Failure contexts: `artifacts/generalized_browser_recovery/failure_ledger.jsonl`
- Fresh screenshots: `artifacts/generalized_browser_recovery/screenshots/`

The previous 30/30 result used a local deterministic Planner fallback that has
now been removed as out of ownership scope. Current honest status is: Runtime
handoff, validation, execution, fresh verification, continuation, and evidence
contracts exist; autonomous five-family selection and the final recovery video
wait for the Planner and perception integrations.
