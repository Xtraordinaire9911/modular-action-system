# Capability-driven Browser Recovery Demo

## What this demo proves

One existing Agent/Planner receives a fresh `FailureContext`, selects an
observed recovery capability by semantic relation, and hands one primitive back
to Runtime. Runtime validates, executes, freshly observes, verifies the
capability postcondition, and then lets the same Agent resume the original
goal.

The production path does not branch on fixture ID, failure-family name, button
text, or a known selector. The five browser scenes are witnesses for one
contract:

```text
failed transition
  -> fresh FailureContext + fresh observed affordances
  -> same System2Planner
  -> generic relation: remediates / restores / compensates / observes / equivalent_to
  -> Runtime validation + primitive execution
  -> fresh recovery_postcondition
  -> resume original goal when needed
  -> independent final oracle
```

Autocomplete is intentionally absent. It remains a false-success detection
witness and is not part of Runtime recovery ownership.

## Friday live command

Run one visible episode per supported family:

```bash
python -m src.pipeline \
  --generalized-browser-recovery \
  --open-web-dev-repetitions 1 \
  --open-web-holdout-repetitions 0 \
  --headed \
  --output-dir artifacts/friday_capability_recovery_demo
```

For formal headless evidence, use three development and three locked-holdout
variants per family:

```bash
python -m src.pipeline \
  --generalized-browser-recovery \
  --open-web-dev-repetitions 3 \
  --open-web-holdout-repetitions 3 \
  --output-dir artifacts/generalized_browser_recovery
```

## Five visible scenes

| Scene | First failure | Capability chosen from fresh observation | What the audience sees |
|---|---|---|---|
| Overlay obstruction | Target click is blocked | `remediates` | Agent closes the observed obstruction, then retries the original target. |
| Session expiry | Save executes but session oracle remains invalid | `restores` / `remediates` | Agent renews the session, verifies it, then resumes the same save goal. |
| Optimistic rollback | UI suggests success but backend oracle is false | `compensates` | Agent executes compensation, fresh observation exposes a valid alternate commit path, then completes through it. |
| DOM/visual disagreement | Selection is not consistent across observations | `observes` / `remediates` | Agent requests a fresh state recheck, verifies consistency, then resumes the original selection. |
| Ineffective affordance | Click executes but required state does not change | `equivalent_to` | Agent selects a freshly observed equivalent affordance and reaches the original goal without retry looping. |

The DOM/visual browser scene proves the active-reobservation contract. The
separate VLM adapter captures a fresh PNG and converts model judgements into
provenance-bearing assertions, but the checked-in evidence uses a fake vision
client. Do not claim a real external VLM run unless a configured provider is
actually used and its artifact is retained.

## Code walkthrough positions

| Responsibility | Code location |
|---|---|
| DOM capability metadata becomes observed affordance metadata | `src/perception/dom_transducer.py` |
| Fresh failure evidence enters the existing Agent context | `src/runtime/action_context.py` |
| Same Planner ranks generic capability relations | `src/runtime/affordance_controller.py` |
| Runtime executes, re-observes, verifies, and returns to Agent | `src/runtime/continuous_interaction_manager.py` |
| Fresh screenshot-to-VLM active-perception adapter | `src/perception/vlm_active_probe.py` |
| Five-family browser orchestration and evidence writing | `evaluation/generalized_browser_recovery.py` |
| Randomized development/holdout IDs and labels | `evaluation/open_web_randomized_holdout.py` |
| Environment-provided recovery capabilities | `env/mock_envs/failure_*.html` |

## Result locations and truthful claim boundary

- Summary: `artifacts/generalized_browser_recovery/generalized_browser_recovery_report.json`
- Runtime transitions: `artifacts/generalized_browser_recovery/transition_ledger.jsonl`
- Failure contexts: `artifacts/generalized_browser_recovery/failure_ledger.jsonl`
- Fresh screenshots: `artifacts/generalized_browser_recovery/screenshots/`

Current formal result: 30 episodes across five families, split into 15
development and 15 locked holdout episodes; 30/30 recovered and passed the
independent final oracle, with exactly one semantic Agent replan per episode.

This supports a bounded capability-generalization claim over randomized local
browser fixtures. It does not establish unrestricted open-web recovery, real
credential-provider integration, or an external-VLM run.
