# Yixin Runtime/Recovery Convergence Execution Plan

## Objective

Create an evidence-backed responsibility dossier for Yixin's runtime, fusion,
verification, and recovery scope, then implement the smallest coherent
architecture change that returns semantic recovery decisions to the existing
Agent/Planner instead of introducing a second recovery planner or embedding
fixture-specific decisions in Runtime.

## Status

**Bounded Agent/Runtime implementation complete and verified; broader recovery
evidence is not closed.** Existing obstruction repair proves one supported
recovery family. Autocomplete is excluded from Yixin's delivery scope; four
other in-scope environment capabilities remain external dependencies.

## Constraints

1. Preserve unrelated user files and worktree changes.
2. Do not branch production behavior on fixture ID, failure-family label,
   injected parameter, known selector, or benchmark answer.
3. Environment/oracle state remains authoritative; Agent and Runtime cannot
   author success.
4. The existing Agent/Planner owns semantic replanning. No second
   `RecoveryPlanner` architecture may be introduced.
5. Runtime owns typed execution outcomes, fresh observation, safety, budgets,
   deterministic validation, execution, verification, and ledger evidence.
6. Environment capabilities that do not exist must be recorded as dependencies
   or typed unsupported outcomes, not simulated in Runtime.

## Steps

| Step | Status | Evidence/output |
|---|---|---|
| Audit relevant authorities, owners, consumers, lifecycle, contracts, exceptional paths, projections, tests, docs, and release state | completed | Dossier sections 2-7; legacy planner naming gap recorded |
| Write Yixin responsibility and recovery-gap dossier | completed | `YIXIN_RUNTIME_RECOVERY_DOSSIER.md` |
| Define closed terminal outcomes and typed Agent↔Runtime recovery transitions | completed | `RuntimeOutcome`, `PlannerHandoff`, `RecoveryActionType`, dossier section 5 |
| Refactor semantic repair out of CIM's internal decision path and return failure context to the existing planning loop | completed | CIM no longer imports/calls a repair planner |
| Extend the existing ActionContext/Planner input with fresh failure evidence and attempted-action history | completed | `src/runtime/action_context.py` |
| Preserve bounded transparent Runtime recovery and deterministic fail-closed behavior | completed | No replan without a fresh observation; retry/reroute remain bounded |
| Add state-machine/property tests plus representative browser integration witnesses | completed for bounded supported scope | Generated IDs/labels, ambiguity, stale proposal/observation, linked Agent loopback; Chromium holdout |
| Reconcile README, STATUS, artifacts, demo claims, and ownership boundaries | completed | README/STATUS/dossier separate one recovered family, four in-scope dependencies, and the upstream autocomplete witness |
| Run focused, full, live-browser, anti-cheating, and fresh-context review | completed for implementation scope | Fresh reviews produced adversarial counterexamples; nonce/capture binding, cancellation/deadline ordering, reroute freshness, primitive compatibility, final-oracle linkage, and metadata handling were corrected and revalidated |

## Files modified

- `.codex/yixin_recovery_convergence_execution_plan.md` — this tracker.
- `YIXIN_RUNTIME_RECOVERY_DOSSIER.md` — authoritative gap/ownership archive.
- `src/runtime/action_context.py` — typed sanitized planner handoff.
- `src/runtime/affordance_controller.py` — recovery choice in existing planner fallback.
- `src/runtime/continuous_interaction_manager.py` — Agent loopback and fresh-evidence enforcement.
- `src/runtime/episode.py` — request binding and post-attempt terminal checks.
- `src/runtime/plan_validator.py` — observed-affordance and primitive compatibility validation.
- `src/runtime/live_observation.py` — typed live observation response contract.
- `src/recovery/recovery_cascade.py` — explicit replan disposition.
- `src/perception/browser_obstruction.py` — measured obstruction/recovery-affordance observation.
- `evaluation/generalized_browser_recovery.py` — real-browser one-family recovery evidence.
- `tests/test_agent_runtime_replan_contract.py` — invariant/property witnesses.
- `tests/test_precondition_repair.py` — integrated same-planner recovery witnesses.

## Latest verification

- Ruff: all files pass.
- Black: 264 files compliant.
- mypy: 115 source files pass.
- pytest non-live: 616 passed, 14 deselected.
- pytest live Chromium: 14 passed, 616 deselected.
- Formal obstruction recovery: 3 dev + 3 locked holdout, 6/6 recovered and final-oracle verified.
- Every formal episode has exactly one Agent replan and explicitly links
  `final_verification_transition_id` to its last transition.
- Production anti-cheat scan: no open-web case ID, fixture family, known selector,
  control ID, positive remediation-label allowlist, or injected parameter in
  runtime/recovery/obstruction policy.
- Environment warning: the active Python 3.13 shell lacks the pytest plugin that
  owns `asyncio_mode`; tests pass and this is not a product/runtime failure.

## Exit criteria

Implementation completion and verified closure are separate. Closure requires:

1. One causal model accounts for the detection-as-recovery and
   single-family-as-generalization reopenings.
2. Agent, Runtime, Perception, Environment, and Oracle each have one explicit
   authority boundary.
3. Supported outcomes and legal transitions are typed; unsupported states fail
   closed deterministically.
4. Semantic recovery loops back through the existing Agent/Planner.
5. Runtime cannot execute an unobserved, unsafe, stale, over-budget, or
   unverifiable proposal.
6. Property/state-machine tests cover success, replan, transparent retry,
   escalation, cancellation, stale evidence, partial success, and budget
   exhaustion.
7. Held-out cases pass without a production branch per case.
8. Code, tests, docs, status, artifacts, and demo claims agree after a fresh
   independent review.
