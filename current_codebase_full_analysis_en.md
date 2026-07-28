# Team 2 Codebase Review Interpretation and Required Actions

> Review basis: `Project Review - Team 2 - July 21.pdf`, 21 July 2026, Version 01.00.
>
> Current code basis: merge commit `1ef27f4` on `origin/develop`, 22 July 2026. PR #56 is merged with green CI, so all `[Yixin - Completed]` items in this document are now in `develop`; `main` remains on the older release.
>
> Planning scope for the expanded `[Yixin]` section: incremental work on the current `develop` runtime. It intentionally excludes branch synchronization, a major CIM/module decomposition, a new natural-language planner, and ownership of the VAM/PiP implementation.

## Status Labels

- `[Yixin - Completed]`: supported by code, tests, or live evidence.
- `[Yixin - TODO]`: remaining runtime/control/fusion work.
- `[Yixin - Conditional TODO]`: only starts after an explicit data or evidence gate passes.
- `[Ruiyao/Fadi - To assign]`: left for Ruiyao and Fadi to divide between themselves.
- `[Team/Release]`: requires merge, release, or a shared claim decision.

## 1. Updated Interpretation of the Supervisor Summary

The PDF summary states that the repository was a strong component library but not yet an agent: it lacked a planner, one composed perceive-decide-act-verify-recover loop, a real VAM, genuine end-to-end execution, and claim/release integrity (PDF §0, p.1). That was accurate for the reviewed snapshot, but several statements are now outdated.

### 1.1 Summary Statements That Are Now Outdated

1. **“There is no planner of any kind” is outdated.** `[Yixin - Completed]` The runtime has a bounded planner over structured `GoalSpec` input and explicit affordance schemas. It is not unrestricted natural-language or LLM intent recognition.
2. **“There is no composed loop” is outdated.** `[Yixin - Completed]` CIM now runs an episode-level `observe -> map -> fuse -> plan -> act -> re-observe -> verify -> recover/replan` loop.
3. **“Recovery is selected but never executed” is outdated.** `[Yixin - Completed]` Retry, reroute, and rollback invoke real executors and are verified from fresh observations.
4. **“Every end-to-end demo is choreographed” is no longer accurate.** `[Yixin - Completed]` The live runtime suite goes through CIM against Docker, Playwright, DOM, Thing Directory, and WoT, with evidence derived from persisted transitions and final observations.
5. **The collection failure has been repaired.** The current branch passes 252 tests, Ruff, Black, mypy, and GitHub CI; the fixture API exists.

### 1.2 Summary Gaps That Still Hold

1. **Release integrity is closed on `develop`, but not yet on `main`.** `[Team/Release]` PR #56 has merged with green CI; a reviewer who checks only `main` will still see the older state (PDF §1; §7.1).
2. **A real VAM/VLM path is still missing.** `[Ruiyao/Fadi - To assign]` The repository still contains heuristic/text-only behavior and lacks reliable image grounding and genuine SoM marks (PDF §1; §4.3; §7.6).
3. **Natural-language/LLM task interpretation is still absent.** `[Ruiyao/Fadi - To assign]` The action runtime starts from structured `GoalSpec`; the team must either narrow the claim or add an upstream intent-to-GoalSpec layer (PDF §1; §6; §7.7).
4. **Full UFO2-style PiP remains unimplemented.** `[Ruiyao/Fadi - To assign]` Playwright context isolation is not a virtual desktop with independent input, complete side-effect containment, and supervised takeover (PDF §4.5).
5. **The evaluation sample is too small for statistical claims.** `[Yixin - TODO]` Fusion calibration currently has seven labelled live scenarios, while PDF §9.5 requests at least 30 episodes per condition.

### 1.3 Corrected Summary

The project is now a **structured-goal action-system runtime**, rather than only a component library. It can scan a real DOM/WoT environment, maintain one CognitiveMap, fuse source-attributed state, perform bounded planning, execute and re-observe actions, verify outcomes, run recovery, and preserve auditable transition evidence. Remaining gaps are unrestricted natural-language planning, real visual-model grounding, desktop-level PiP, release synchronization, and benchmark-scale statistical evaluation.

## 2. Layered Gaps and Current Status

| Layer | Supervisor finding and PDF reference | Current assessment | Responsibility status |
|---|---|---|---|
| Release and claims | Reported code absent from main; clean-clone/CI mismatch (§0; §1; §5 D1; §7.1) | PR #56 is merged into `develop` with green CI; `main` is not yet synchronized | `[Yixin - Completed]` implementation, tests, and develop merge; `[Team/Release]` clean-clone replay and main release |
| Composition | No single planner-to-recovery execution path (§2; §7.3) | The CIM episode loop now composes the runtime path with fresh observations | `[Yixin - Completed]` |
| Planner | No goal-to-action planner (§0; §1; §6; §7.7) | Bounded schema-driven planning exists; unrestricted NL-to-GoalSpec does not | `[Yixin - Completed]` runtime planner; `[Ruiyao/Fadi - To assign]` upstream layer or claim reduction |
| Fusion | Duplicate maps/arbiters, gate only, no fused estimate, uncalibrated parameters (§3; §5 D3-D5; §7.2/7.4; §9) | One canonical map/arbiter/router; fused state feeds verification; missing/stale source handling, clean re-observation resolution, and active perception exist; initial calibration completed | `[Yixin - Completed]` freshness lifecycle; `[Yixin - TODO]` repeated campaign and holdout evaluation |
| Input integrity | All deltas labelled WoT; confidence fixed at 1.0 (§3.3; §5 D3-D4; §9.1) | Write-backs are source-attributed; confidence/timestamp/provenance contracts exist and default origins are explicit | `[Yixin - Completed]` runtime contract; `[Ruiyao/Fadi - To assign]` measured sensor confidence |
| Verification | Executor success and task success must remain empirically separated (§2; §5 D2/D7; §7.3; §7.5) | Skill-level postconditions, primitive-level declared-effect verification, and final goal verification are now separated | `[Yixin - Completed]` primitive expected-effect verification and transition evidence |
| Recovery | Tier selected but retry/reroute/rollback not executed; ambiguous states (§2; §5 D2/D6; §7.3) | Recovery actions execute and are freshly verified; failed transitions are explicitly linked to retry/reroute/rollback recovery transitions; result semantics and retry budgets are explicit | `[Yixin - Completed]` recovery evidence linkage; `[Yixin - TODO]` reroute-equivalence hardening |
| System 1 | ReflexLibrary has no production consumer (§4.4; §5 D9; §8.5) | CIM consumes verified cache entries, invalidates failures, and records fast-path evidence | `[Yixin - Completed]`; `[Yixin - TODO]` repeated amortized-latency evidence |
| Effectors/visual | Legacy hardcoded maps; no genuine visual marks or VLM (§4.1-4.3; §5 D8; §7.6) | Legacy paths remain and real visual grounding remains incomplete | `[Ruiyao/Fadi - To assign]` |
| Isolation/PiP | BrowserContext presented as full PiP; no takeover; shared WoT state; overlay contamination (§4.5) | Documentation now says browser-session isolation, but the missing lifecycle and supervision features remain | `[Ruiyao/Fadi - To assign]` |
| Metrics | Authored outcomes, RUR drift, insufficient real campaign (§1; §5 D7; §6; §7.5/7.8) | Live metrics derive from episode evidence; trigger and success rates are distinct; RTA uses an independent oracle | `[Yixin - Completed]` core corrections; `[Yixin - TODO]` fill remaining ledger-derived rows and mark unsupported metrics as not measured; `[Ruiyao/Fadi - To assign]` external benchmark campaign |
| PAM/evolution | No cross-snapshot identity, transitions, events, or trace-based evolution (§8.1-8.3) | Abstract state identity, stable keys, transition/event linkage, and review-gated skill proposals now exist | `[Yixin - Completed]`; `[Ruiyao/Fadi - To assign]` stable perception locators |
| Fusion v2 | Hand-set weights lack data; probability model must follow a hard gate (§9.1-9.6) | The hard gate and calibrated heuristic fallback are delivered; seven cases do not justify a Bayesian likelihood model | `[Yixin - Completed]` fallback; `[Yixin - TODO]` collect and hold out data; `[Yixin - Conditional TODO]` Bayesian comparison only after the gate |
| Reproducibility | Missing setup entrypoints and path/port drift (§5 D10; §6) | Still incomplete | `[Ruiyao/Fadi - To assign]`; `[Team/Release]` final acceptance |

## 3. `[Yixin]` Completed Work

| Completed item | Evidence | PDF mapping |
|---|---|---|
| Episode-level observe-plan-act-verify-recover loop | CIM and `src/runtime/episode.py` | §2; §5 D2/D6; §7.3 |
| Bounded zero-shot planning over explicit affordances | `src/runtime/task_planner.py`, `GoalSpec`, live normal-goal case | §0; §6; §7.7, with a strict action-system boundary |
| One canonical CognitiveMap, arbiter, and router | Runtime source of truth plus planner/legacy adapters | §2; §3.2; §5 D5; §7.2; §9.5 |
| Fused state and active perception | Accepted fused view, required-source uncertainty, real probe | §3.1-3.3; §8.4; §9.1 |
| Provenance/confidence ingestion repair | Backend-attributed assertions and explicit metadata origins | §3.3; §5 D3-D4; §7.4 |
| Executed and verified recovery | Retry, reroute, rollback, escalation, budgets, false-success separation | §2; §5 D2/D6; §7.3 |
| System-1 reflex integration | Production consumer, invalidation, cache-hit/latency evidence | §4.4; §5 D9; §8.5 |
| Transition ledger and controlled internalization | State IDs, stable keys, JSONL ledger, event links, review-gated proposals | §8.1-8.3 |
| Genuine live tracer bullet | Normal, timeout, rollback, conflict/active perception, System-1 repeat | §1 live-demo critique; §7.5; §9.1 |
| Live ablation and initial fusion calibration | Four runtime modes; seven labelled scenarios | §6; §8.4-8.5; §9.2/9.5/9.6 |
| Metric-integrity fixes | Episode-derived values and independent recovery-tier oracle | §5 D7; §6 |
| Primitive expected-effect verification | Non-empty primitive effects are checked after fresh observation and fusion; undeclared effects are recorded as not checked | §2; §5 D2/D7; §7.3; §7.5 |
| Conflict freshness lifecycle | New agreeing evidence resolves old conflicts; restored required sources clear missing-source conflicts; optional absolute assertion age prevents stale evidence from passing as fresh | §3; §7.2; §7.4; §9.1 |
| Recovery evidence linkage | Retry, reroute, and rollback transitions now store `recovery_of_transition_id`, making the failed-transition to recovery-transition chain directly auditable | §2; §5 D2/D6; §7.3 |

## 4. Required TODOs by Priority

| Priority | Required TODO | Owner label | Acceptance criterion | Original PDF |
|---|---|---|---|---|
| P0 | Rerun checks/live demo from a clean clone of latest `develop`, release verified code to `main`, and align README claims | `[Team/Release]`; `[Yixin - Completed]` develop merge and fix support | Reproducible release commit with green CI and branch-accurate claims | §0; §1; §5 D1; §7.1 |
| P0 | Mark synthetic/authored artifacts as illustrative; use episode IDs and ledgers for final reported numbers | `[Ruiyao/Fadi - To assign]`; `[Yixin - Completed]` live metric path | Every final metric traces to executed episodes | §1 offline-demo finding; §7.5 |
| P1 | Verify every non-empty primitive `expected_effect` after fresh observation and fusion; do not record an unchecked primitive as postcondition success | `[Yixin - Completed]` | Executor success without the declared state effect becomes a false-success/recovery case; undeclared effects are recorded as not checked | §2; §5 D2/D7; §7.3; §7.5 |
| P1 | Refresh conflict state from current evidence and add stale/clean re-observation regressions without redesigning CognitiveMap | `[Yixin - Completed]` | A previous conflict cannot continue blocking after newer agreeing evidence; stale required-source evidence is identified deterministically | §3; §7.2; §7.4; §9.1 |
| P1 | Link failed transitions to the retry/reroute/rollback transitions that recover them and harden primitive reroute equivalence | `[Yixin - Completed]` evidence linkage; `[Yixin - TODO]` reroute-equivalence hardening | Every successful recovery has a failed parent transition, an executed recovery transition, and fresh verification evidence | §2; §5 D2/D6; §7.3 |
| P1 | Complete ledger-derived live metric rows and distinguish `not measured` from measured zero | `[Yixin - TODO]` | Live reports derive primitive/verification/recovery evidence from executed episodes and do not publish empty-denominator metrics as `0.0` | §5 D7; §6; §7.5 |
| P1 | Build a real visual path: screenshot input, real model or honest heuristic label, Playwright bounding boxes, no fabricated marks | `[Ruiyao/Fadi - To assign]` | One genuine image-in/model-out/mark-to-click smoke trace | §1 VAM/SoM; §4.3; §7.6 |
| P1 | Remove or isolate legacy `_SKILL_TO_*` tables and use affordance contracts on live paths | `[Ruiyao/Fadi - To assign]` | Live execution no longer depends on hardcoded skill mappings | §4.1-4.3; §5 D8 |
| P1 | Decide the planner claim: structured GoalSpec boundary or an upstream NL/LLM-to-GoalSpec implementation | `[Ruiyao/Fadi - To assign]`; `[Team/Release]` claim decision | README/report no longer conflates bounded planning with unrestricted intent understanding | §1; §6; §7.7 |
| P1 | Complete browser/WoT episode isolation: snapshot/restore, context recreation, overlay filtering, supervised tier-4 pause/resume | `[Ruiyao/Fadi - To assign]` | Verified rollback, no cross-session contamination, measurable HITL correction | §4.5 recommendations 1-3 |
| P2 | Expand the existing seven-condition fusion calibration to at least 30 independent episodes per condition | `[Yixin - TODO]`, with shared environment support | At least 210 uniquely identified trials with reset evidence, deterministic seeds, per-condition counts, and independent oracle labels | §9.2; §9.5 |
| P2 | Split fusion trials into calibration and locked holdout sets; freeze the threshold before holdout scoring | `[Yixin - TODO]` | Holdout report includes false-halt, miss, precision, balanced accuracy, and detection-latency summaries without seed leakage | §9.2; §9.5; §9.6 |
| P2 | Measure System-1 warm-up, cache-hit rate, routing latency, and amortized episode latency over repeated live runs | `[Yixin - TODO]` | Report first-run versus repeated-run distributions and make no `<50 ms` claim unless the measurements support it | §8.5; §9.5 |
| P2 | Compare Bayesian fusion only if calibration data is sufficient; otherwise retain the calibrated heuristic fallback | `[Yixin - Conditional TODO]` | Posterior is consumed by verifier/CIM and improves locked-holdout performance without worse false-halt/miss behavior, or a documented no-go decision is produced | §9.3-9.6 |
| P2 | Rerun the agentic benchmark path and report scripted replay only as an upper bound; broaden baseline ablations | `[Ruiyao/Fadi - To assign]` | Separate agentic/scripted tables under one protocol | §1 M1; §6; §7.8 |
| P2 | Add or formally revise setup entrypoints, ports, and the clean-clone runbook | `[Ruiyao/Fadi - To assign]`; `[Team/Release]` acceptance | A new machine can install, test, and run the demo from one documented path | §5 D10; §6 |

## 4.1 `[Yixin]` Detailed Incremental TODO Plan

The work packages below extend the existing runtime in small reviewable changes. They do **not** require a main/develop synchronization decision, a major decomposition of CIM, a replacement planner, or implementation of the real VAM/PiP stack.

### Recommended execution order

| Order | Work package | Depends on | Primary deliverable |
|---:|---|---|---|
| 1 | Y-01 Primitive expected-effect verification | Existing goal loop and condition evaluator | Verified primitive transition semantics |
| 2 | Y-02 Conflict freshness and clean re-observation regressions | Existing CognitiveMap and arbiter | Stable conflict lifecycle under repeated observation |
| 3 | Y-03 Recovery evidence linkage and reroute-equivalence tests | Existing recovery cascade and transition ledger | Auditable failure-to-recovery chain |
| 4 | Y-04 Ledger-derived metric completion | Y-01 and Y-03 | Evidence-complete live metric dataset |
| 5 | Y-05 Repeatable fusion campaign runner | Y-02 and Y-04 | At least 30 trials per current condition |
| 6 | Y-06 Calibration/holdout evaluation | Y-05 | Frozen-threshold holdout report |
| 7 | Y-07 System-1 amortized-latency campaign | Y-04 | Repeated-run cache/latency evidence |
| 8 | Y-08 Bayesian fusion decision gate | Y-06 | Implement-and-compare decision or documented no-go |

### Y-01 — Primitive expected-effect verification (`P1`)

**Status:** `[Yixin - Completed]` in the current implementation branch. The remaining related work is Y-03 transition parent/recovery linkage, not primitive effect checking itself.

**Target files**

- `src/runtime/continuous_interaction_manager.py`
- `src/verification/condition_evaluator.py`
- `src/runtime/episode.py`
- `tests/test_runtime_goal_episode.py`
- new focused tests in `tests/test_primitive_verification.py`

**Implementation steps**

1. After each primitive execution, keep the existing fresh re-observation and fusion step.
2. When `PrimitiveAction.expected_effect` is non-empty, evaluate it as an empirical condition against the newly accepted runtime state.
3. Set `TransitionRecord.postcondition_passed` to:
   - `True` only when the declared effect is observed;
   - `False` when the executor reported success but the effect is absent;
   - `None` when no primitive effect was declared.
4. Route `False` through the existing failure classifier and recovery cascade as a false-success/postcondition failure.
5. Preserve final goal-state verification as the task-level success oracle; primitive verification does not replace it.
6. Record the checked predicate and a compact observed/expected summary in transition evidence.

**Required tests**

- executor success with no state change triggers recovery;
- declared primitive effect passes after fresh observation;
- missing `expected_effect` is `not checked`, not silently `True`;
- failed primitive verification prevents a false successful transition;
- final goal verification still determines `final_outcome_verified`.

**Acceptance criterion**

No primitive is marked verified solely because the executor returned success and the observation call did not throw.

### Y-02 — Conflict freshness and clean re-observation regressions (`P1`)

**Status:** `[Yixin - Completed]` in the current implementation branch for conflict lifecycle regression coverage and optional absolute assertion age. Larger repeated-campaign calibration remains Y-05/Y-06.

**Target files**

- `src/runtime/cognitive_map.py`
- `src/verification/conflict_detector.py`
- `src/verification/active_perception.py`
- `tests/test_epistemic_runtime.py`
- `tests/test_fusion_calibration.py`

**Implementation steps**

1. Add an incremental refresh rule for conflict IDs produced by the current fusion pass.
2. When newer evidence for the same entity/attribute agrees, mark the prior conflict resolved with a deterministic decision reason instead of leaving it active.
3. Add an optional absolute assertion-age limit to `EpistemicArbiter`; keep the default backward compatible.
4. Evaluate required-source freshness against the current observation/reference time, not only against the newest assertion in the same group.
5. Ensure active perception records whether resolution came from agreement, source restoration, or remaining uncertainty.
6. Keep raw assertion history for audit; only the current blocking status changes.

**Required tests**

- a clean re-observation resolves a previous value mismatch;
- a restored required source resolves a missing-source conflict;
- two equally old sources are not treated as fresh merely because they agree in age;
- an unresolved fresh conflict still blocks System 1;
- repeated fusion does not duplicate the same active conflict.

**Acceptance criterion**

A conflict reflects current evidence: newer agreement clears the blocking state, while stale or still-disagreeing evidence remains explicit and auditable.

### Y-03 — Recovery evidence linkage and reroute equivalence (`P1`)

**Status:** `[Yixin - Completed]` for transition evidence linkage in the current implementation branch. Retry, reroute, and rollback recovery transitions now carry `recovery_of_transition_id`. The remaining part of this work package is stricter primitive reroute-equivalence hardening.

**Target files**

- `src/runtime/episode.py`
- `src/runtime/continuous_interaction_manager.py`
- `src/recovery/recovery_cascade.py`
- `tests/test_runtime_episode_recovery.py`
- `tests/test_runtime_goal_episode.py`

**Implementation steps**

1. `[Completed]` Add a backward-compatible transition field `recovery_of_transition_id`.
2. `[Completed]` Link every retry, reroute, or rollback execution to the failed transition that triggered it.
3. `[Completed]` Mark recovery success only after the recovery execution has a fresh observation and verification result.
4. `[Remaining]` For primitive reroute, require the alternative affordance to preserve:
   - the primitive action type;
   - the declared `expected_effect` or completion semantics;
   - compatible parameter binding;
   - no weaker safety metadata.
5. `[Completed]` Record the selected tier, considered tiers, failed backend, recovery backend, and verification outcome in one episode chain.
6. `[Completed]` Preserve rollback semantics: a verified rollback may count as recovery success while the original task remains unsuccessful.

**Required tests**

- `[Completed]` retry transition points to the original failed transition;
- `[Completed]` reroute transition points to the original failed transition;
- `[Completed]` rollback transition records `reversible_result=True` only after verification;
- a semantically unrelated affordance is rejected as a reroute;
- `[Completed]` recovery success and final task success remain separate.

**Acceptance criterion**

Every reported recovered case can be reconstructed from one failed transition, one selected recovery decision, one executed recovery transition, and fresh verification evidence.

### Y-04 — Ledger-derived metric completion (`P1`)

**Target files**

- `evaluation/metrics_aggregator.py`
- `evaluation/live_runtime_demo.py`
- `src/runtime/episode.py`
- `tests/test_metrics_aggregator.py`
- new focused tests in `tests/test_metric_evidence_derivation.py`

**Implementation steps**

1. Extend `dataset_from_runtime_results()` to derive primitive-action rows from transition records.
2. Derive verification rows from `postcondition_passed` and the new primitive verification evidence.
3. Preserve independent expected recovery tiers for RTA; never infer the oracle from the runtime-selected tier.
4. Add episode wall-clock duration to runtime results or episode metadata; keep executor/action latency as a separate measure.
5. Store numerator, denominator, episode IDs, and data source for each reported live metric.
6. When a metric has no supporting evidence rows, omit it from measured values and list it under `metadata.not_measured` rather than publishing `0.0`.
7. Do not report action-level UAR until attempted/blocked/executed safety rows exist; mark it not measured in that case.
8. Keep synthetic demo metrics explicitly labelled `synthetic`.

**Required tests**

- transition records generate primitive latency rows;
- checked/failed/unchecked primitive effects generate correct verification rows;
- empty routing or safety evidence does not become a measured zero;
- RTA is absent without an independent oracle;
- task wall-clock latency and summed action latency are distinguishable;
- each measured live metric carries episode evidence.

**Acceptance criterion**

Every metric shown in a final live table has a real numerator, denominator, data source, and episode evidence; unsupported metrics are visibly not measured.

### Y-05 — Repeatable live fusion campaign (`P2`)

**Target files**

- `evaluation/live_fusion_calibration.py`
- `evaluation/fusion_calibration.py`
- `src/pipeline.py`
- `tests/test_fusion_calibration.py`

**Implementation steps**

1. Preserve the current seven labelled scenarios as the initial frozen condition set.
2. Add CLI/config inputs for:
   - `trials_per_condition`;
   - deterministic seed or seed list;
   - optional shuffled execution order;
   - output directory.
3. Give every trial a unique `trial_id` and `episode_id`; do not reuse the literal `calibration` episode ID.
4. Reset and verify the control-plane baseline before each trial and clear injected faults in a `finally` path.
5. Record scenario, seed, expected label, observed sources, conflict score, selected decision, detection latency, and error status in JSONL.
6. Continue the campaign after an individual failed trial while preserving the failure record.
7. Produce a summary with requested, completed, failed, and valid trial counts per condition.
8. Run at least 30 independent trials for each of the seven current conditions: at least 210 total trials.

**Required tests**

- the same seed produces the same trial order and IDs;
- every trial has a unique episode ID;
- one trial failure does not abort the campaign;
- reset/cleanup is invoked for every trial;
- summary counts match JSONL rows;
- each condition has at least the configured number of valid trials.

**Acceptance criterion**

The calibration artifact is a repeatable multi-episode dataset, not seven one-shot observations.

### Y-06 — Calibration and locked holdout evaluation (`P2`)

**Target files**

- `evaluation/fusion_calibration.py`
- `evaluation/live_fusion_calibration.py`
- `tests/test_fusion_calibration.py`

**Implementation steps**

1. Split seeds deterministically into calibration and holdout sets, for example 20 and 10 trials per condition.
2. Select the threshold only from the calibration set.
3. Freeze the selected threshold before reading holdout outcomes.
4. Apply the frozen threshold to the holdout set without retuning.
5. Report calibration and holdout results separately:
   - true positives and true negatives;
   - false halts and misses;
   - precision and balanced accuracy;
   - mean, median, and p95 detection latency;
   - per-condition confusion counts.
6. Store the exact seed split and selected threshold in the report.
7. Add a regression test that fails if holdout rows influence threshold selection.

**Acceptance criterion**

The final fusion result includes an untouched holdout evaluation and can be reproduced from the recorded seeds and threshold.

### Y-07 — System-1 repeated-run and amortized latency evidence (`P2`)

**Target files**

- `evaluation/live_runtime_demo.py`
- `evaluation/metrics_aggregator.py`
- `tests/test_live_runtime_demo.py`

**Implementation steps**

1. Extend the existing System-1 reflex case into one warm-up execution plus at least 30 verified repeated executions.
2. Record per episode:
   - cache hit or miss;
   - routing latency;
   - executor latency;
   - total episode latency;
   - final verification outcome.
3. Report cache-hit rate and warm-up versus repeated-run latency distributions.
4. Compute amortized latency over the complete sequence, including the warm-up cost.
5. Keep this result scoped to the current runtime cache; the real VAM-only comparison remains part of the shared/team baseline work.
6. Do not claim the supervisor's `<50 ms` target unless the measured routing or action definition actually satisfies it.

**Required tests**

- the first execution is a cache miss and verified;
- repeated executions are cache hits and still verified;
- cache invalidation after failure removes the hit;
- amortized latency includes warm-up;
- failed repeats are not counted as successful fast paths.

**Acceptance criterion**

System-1 latency and cache benefit are supported by repeated live episodes rather than one illustrative repeat.

### Y-08 — Data-gated Bayesian fusion decision (`P2`, conditional)

**Start gate**

This work starts only after Y-05 and Y-06 produce the minimum repeated dataset and locked holdout report.

**Decision steps**

1. First retain the calibrated heuristic as the baseline.
2. Fit a probabilistic model only on the calibration split.
3. Evaluate it on the same locked holdout split.
4. Compare false-halt rate, miss rate, balanced accuracy, calibration quality, latency, and downstream runtime decisions.
5. Integrate a posterior into verifier/CIM only if the model improves the holdout result without weakening the hard safety/conflict gate.
6. Otherwise write a short no-go decision and keep the calibrated heuristic.

**Acceptance criterion**

Either a genuinely consumed posterior beats the baseline under the locked protocol, or the repository explicitly records that the data does not justify Bayesian production fusion. Implementing a Bayesian model is not itself a success criterion.

## 4.2 `[Yixin]` PR-sized Delivery Boundaries

| Suggested PR | Included work | Explicitly excluded |
|---|---|---|
| PR-Y1 | Y-01 primitive verification | Planner replacement, CIM decomposition |
| PR-Y2 | Y-02 conflict freshness regressions | New world-model architecture |
| PR-Y3 | Y-03 recovery evidence linkage | Recovery policy redesign |
| PR-Y4 | Y-04 metric derivation completion | External benchmark execution |
| PR-Y5 | Y-05 repeatable campaign runner | Bayesian model |
| PR-Y6 | Y-06 locked holdout report | Production posterior integration |
| PR-Y7 | Y-07 System-1 repeated latency | Real VAM-only baseline |
| PR-Y8, only if gated | Y-08 Bayesian comparison | Any claim unsupported by holdout evidence |

Each PR should add focused tests and one machine-readable artifact schema change at most. This keeps the remaining work reviewable without reopening the completed runtime architecture.

## 5. Final Boundary

- `[Yixin]` has delivered the runtime-control loop, fusion/recovery integration, transition evidence, and review-gated adaptation boundary.
- `[Yixin]` next focuses on correctness evidence at the existing boundaries: primitive verification, current conflict state, recovery trace linkage, metric derivation, repeated fusion trials, holdout evaluation, and System-1 latency.
- The plan does not require a main/develop synchronization decision or a major CIM/module decomposition.
- Bayesian fusion is not mandatory. PDF §9.6 explicitly accepts a calibrated heuristic fallback, which remains the defensible choice until the repeated locked-holdout evidence supports something stronger.
- Natural-language intent recognition, real visual-model grounding, and full PiP remain outside the implemented action-runtime boundary and outside this expanded `[Yixin]` TODO list.
