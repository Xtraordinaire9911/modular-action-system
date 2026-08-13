# Generalized Browser Recovery Plan

## Status

**Reopened — implementation exists for obstruction repair, but cross-family
recovery generalization is not verified.** The previous closed status proved
variation within one failure family and was too narrow for the Friday demo
claim. Dependent claims and release/push remain blocked until the convergence
exit criteria below are met.

## Reopening causal model

1. The original six-family suite treated fresh failure detection as recovery
   evidence even though every episode ended unsuccessfully.
2. The subsequent repair implementation closed the runtime gap for one family,
   but acceptance again counted parameter/layout variation as recovery
   generalization across failure mechanisms.
3. The shared cause is an ambiguous outcome algebra: `failure_detected`,
   `repairable`, `repair_verified`, `goal_retried`, `goal_verified`, and
   `safely_escalated` were not separately required per supported family.
4. The fixture runner owns labels and fault injection; CIM owns decisions and
   transitions; fresh observations own environment facts; oracle projections
   own final outcome validation. Evaluation reports must not collapse these
   authorities into one `passed` flag.

## Goal

Implement and verify a runtime-level browser recovery path that discovers
remediation actions from fresh observations and affordance semantics, executes
them through the canonical CIM/effectors path, re-observes, retries/replans the
original action, and verifies the original goal with a separate fresh oracle.

The implementation must not branch on fixture family, scenario ID, product
text, overlay selector, or a hard-coded accept-cookie action.

## Steps

| Step | Status | Evidence / files |
|---|---|---|
| Audit the current browser fixture runner, observation model, CIM, planner, recovery cascade, and tests | done | Existing runner only detected failures; no precondition-repair path |
| Define a generic obstruction/remediation contract based on observed affordance semantics and state deltas | done | `src/perception/browser_obstruction.py`, sanitized `ActionContext` recovery metadata |
| Implement generic remediation discovery, planning, canonical execution, and linked transitions | done | Existing `System2Planner` chooses; CIM validates/executes and links transitions |
| Integrate Agent loopback plus independent fresh remediation and goal verification | done | Typed `FailureContext`; unit and real-Chromium verification |
| Add varied training/holdout fixtures and negative tests that reject selector/family-specific recovery | done for obstruction only | Disjoint labels, IDs, geometry; ambiguous/destructive candidates refused |
| Define a closed cross-family recovery outcome/state algebra and owner boundaries | done | `YIXIN_RUNTIME_RECOVERY_DOSSIER.md` |
| Map all six families to supported automated repair/retry/reroute/rollback/escalation outcomes | done | Dossier capability matrix; one automated recovery, five honest fail-closed paths |
| Add property/state-machine tests across families and unsupported-state fail-closed behavior | pending | Pending |
| Record a multi-family canonical runtime demo and validate each episode against its typed expected outcome | partial | 30.72s video covers six detected families plus one canonical CIM recovery; five families still have only fail-closed outcomes |
| Re-run full checks and independent fresh-context review before restoring closed status | pending | Pending |

## Generalization acceptance criteria

1. No production recovery branch checks fixture family, scenario ID, injected
   parameter, product name, or known overlay selector.
2. Candidate remediation is derived from a fresh observation, measured blocker
   containment, structural/semantic dismissal evidence, and bounded safety
   checks. A single safe blocker control may use unseen wording.
3. Remediation runs through the same typed primitive/CIM/effectors boundary as
   normal actions.
4. Recovery requires a fresh post-remediation state change before retrying.
5. Original-goal success requires a later, separately acquired oracle state.
6. The same policy succeeds on unseen holdout labels/layouts and safely refuses
   ambiguous or destructive candidates.
7. The ledger links failure, diagnosis, remediation, verification, retry/replan,
   and final goal verification.

## Files modified

- `.codex/generalized_browser_recovery_plan.md` — execution tracker.
- `src/runtime/action_context.py` — sanitized typed failure/replan contract.
- `src/runtime/affordance_controller.py` — deterministic fallback in the existing Agent/Planner.
- `src/recovery/recovery_cascade.py` — transparent recovery and explicit Agent replan handoff.
- `src/runtime/continuous_interaction_manager.py` — canonical execution,
  re-observation, verification, Agent loopback, and linked transitions.
- `src/perception/browser_obstruction.py` — geometry/semantics-based blocker and
  safe remediation-affordance discovery.
- `evaluation/generalized_browser_recovery.py` — dev/locked-holdout Chromium
  evidence runner.
- `evaluation/open_web_randomized_holdout.py` — randomized unseen remediation
  labels, IDs, and geometry.
- `evaluation/open_web_playwright_fixture_runner.py` — variant materialization.
- `env/mock_envs/failure_overlay_obstruction.html` — real user-action behavior.
- `tests/test_precondition_repair.py` — same-Agent loopback, refusal, and linked-loop tests.
- `tests/test_agent_runtime_replan_contract.py` — generated-ID, ambiguity, stale-proposal, and fail-closed properties.
- `tests/test_live_browser_claims.py` — real Chromium recovery claim test.
- `tests/test_open_web_randomized_holdout.py` — split variation checks.
- `artifacts/generalized_browser_recovery/` — formal 3-dev/3-holdout report,
  18 linked transitions, and 24 browser screenshots.

## Verification result

- Ruff: passed.
- Black: 255 files compliant.
- mypy: 112 source files passed.
- pytest non-live: 533 passed.
- pytest live Chromium: 10 passed.
- Formal generalized recovery run: 6/6 episodes report both
  `recovery_succeeded=true` and `final_outcome_verified=true`.
- Holdout includes different control IDs, geometry, and labels. The first
  holdout label (`Carry on with browsing`) is absent from the positive semantic
  term set and is selected from measured single-safe-control structure.
- Production recovery source contains no fixture family, case ID, known overlay
  selector, known remediation selector, or randomized-parameter branch.

These results remain valid for obstruction repair, but no longer constitute
closure evidence for cross-family generalized recovery.

## Convergence exit criteria

1. Every supported family has an explicit typed expected outcome: automated
   recovery and goal verification, bounded retry/reroute/rollback, or verified
   fail-closed escalation when safe autonomous recovery is unavailable.
2. Runtime production code contains no branch keyed by fixture ID, failure
   family, injected parameter, known selector, or benchmark label.
3. Fresh observations, not fixture metadata, select transitions in the recovery
   state machine.
4. Property/state-machine tests cover legal ordering, retry budgets,
   idempotency, cancellation, stale observations, unsupported evidence, and
   partial success.
5. Held-out variants from at least three causally different failure families
   pass without adding a production branch per family; all six families have
   honest typed outcomes.
6. One multi-family video is generated from canonical runtime episodes and its
   displayed claims match persisted transition/oracle evidence.
7. Implementation, tests, README, STATUS, artifacts, and release claim all agree
   after an independent fresh-context review.

## Current six-family capability matrix

| Failure family | Environment provides a repair path? | Honest current typed outcome |
|---|---:|---|
| Overlay obstruction | yes | `failure -> agent_replan -> verify_repair -> normal_plan -> goal_verified` |
| Session expiry | no re-authentication affordance/token | `failure_detected -> safely_escalated` |
| Autocomplete mutation | no valid suggestion/acceptance contract | `value_mismatch -> clarification_or_escalation` |
| Optimistic backend rollback | backend remains rejected; goal is unreachable | `false_success_rejected -> rollback_or_escalation` |
| DOM/visual disagreement | fixture runner does not supply resolvable multi-source assertions | `conflict_detected -> active_perception_or_escalation` |
| Visible ineffective affordance | no equivalent alternate affordance | `ineffective_action -> safely_escalated` |

This matrix is bounded by what the current environments actually expose. Adding
an authored success flag or runner-side repair would be invalid. To claim
automated recovery for another family, its environment must expose a genuine
alternative path and the production policy must select it from fresh evidence
without a fixture-keyed branch.

## Current video artifacts

- `output/playwright/friday_multifailure_demo/six_failures_and_generalized_recovery.mp4`
  — six locked-holdout failure families followed by the complete canonical
  obstruction-repair episode.
- `output/playwright/friday_multifailure_demo/evidence/` — six failure-detection
  reports, screenshots, and transitions.
- `output/playwright/friday_generalized_recovery/evidence/` — linked failed
  action, verified remediation, retry, and fresh final oracle for the successful
  recovery scene.
