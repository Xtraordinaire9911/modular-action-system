# Team 2 Codebase Review Interpretation and Required Actions

> Review basis: `Project Review - Team 2 - July 21.pdf`, 21 July 2026, Version 01.00.
>
> Current code basis: merge commit `1ef27f4` on `origin/develop`, 22 July 2026. PR #56 is merged with green CI, so all `[Yixin - Completed]` items in this document are now in `develop`; `main` remains on the older release.

## Status Labels

- `[Yixin - Completed]`: supported by code, tests, or live evidence.
- `[Yixin - TODO]`: remaining runtime/control/fusion work.
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
| Fusion | Duplicate maps/arbiters, gate only, no fused estimate, uncalibrated parameters (§3; §5 D3-D5; §7.2/7.4; §9) | One canonical map/arbiter/router; fused state feeds verification; missing/stale source handling and active perception exist; initial calibration completed | `[Yixin - Completed]`; `[Yixin - TODO]` larger campaign |
| Input integrity | All deltas labelled WoT; confidence fixed at 1.0 (§3.3; §5 D3-D4; §9.1) | Write-backs are source-attributed; confidence/timestamp/provenance contracts exist and default origins are explicit | `[Yixin - Completed]` runtime contract; `[Ruiyao/Fadi - To assign]` measured sensor confidence |
| Recovery | Tier selected but retry/reroute/rollback not executed; ambiguous states (§2; §5 D2/D6; §7.3) | Recovery actions execute and are freshly verified; result semantics and retry budgets are explicit | `[Yixin - Completed]` |
| System 1 | ReflexLibrary has no production consumer (§4.4; §5 D9; §8.5) | CIM consumes verified cache entries, invalidates failures, and records fast-path evidence | `[Yixin - Completed]` |
| Effectors/visual | Legacy hardcoded maps; no genuine visual marks or VLM (§4.1-4.3; §5 D8; §7.6) | Legacy paths remain and real visual grounding remains incomplete | `[Ruiyao/Fadi - To assign]` |
| Isolation/PiP | BrowserContext presented as full PiP; no takeover; shared WoT state; overlay contamination (§4.5) | Documentation now says browser-session isolation, but the missing lifecycle and supervision features remain | `[Ruiyao/Fadi - To assign]` |
| Metrics | Authored outcomes, RUR drift, insufficient real campaign (§1; §5 D7; §6; §7.5/7.8) | Live metrics derive from episode evidence; trigger and success rates are distinct; RTA uses an independent oracle | `[Yixin - Completed]` runtime metrics; `[Ruiyao/Fadi - To assign]` benchmark campaign |
| PAM/evolution | No cross-snapshot identity, transitions, events, or trace-based evolution (§8.1-8.3) | Abstract state identity, stable keys, transition/event linkage, and review-gated skill proposals now exist | `[Yixin - Completed]`; `[Ruiyao/Fadi - To assign]` stable perception locators |
| Fusion v2 | Hand-set weights lack data; probability model must follow a hard gate (§9.1-9.6) | The hard gate and calibrated heuristic fallback are delivered; seven cases do not justify a Bayesian likelihood model | `[Yixin - Completed]` fallback; `[Yixin - TODO]` collect data before any Bayesian run |
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

## 4. Required TODOs by Priority

| Priority | Required TODO | Owner label | Acceptance criterion | Original PDF |
|---|---|---|---|---|
| P0 | Rerun checks/live demo from a clean clone of latest `develop`, release verified code to `main`, and align README claims | `[Team/Release]`; `[Yixin - Completed]` develop merge and fix support | Reproducible release commit with green CI and branch-accurate claims | §0; §1; §5 D1; §7.1 |
| P0 | Mark synthetic/authored artifacts as illustrative; use episode IDs and ledgers for final reported numbers | `[Ruiyao/Fadi - To assign]`; `[Yixin - Completed]` live metric path | Every final metric traces to executed episodes | §1 offline-demo finding; §7.5 |
| P1 | Build a real visual path: screenshot input, real model or honest heuristic label, Playwright bounding boxes, no fabricated marks | `[Ruiyao/Fadi - To assign]` | One genuine image-in/model-out/mark-to-click smoke trace | §1 VAM/SoM; §4.3; §7.6 |
| P1 | Remove or isolate legacy `_SKILL_TO_*` tables and use affordance contracts on live paths | `[Ruiyao/Fadi - To assign]` | Live execution no longer depends on hardcoded skill mappings | §4.1-4.3; §5 D8 |
| P1 | Decide the planner claim: structured GoalSpec boundary or an upstream NL/LLM-to-GoalSpec implementation | `[Ruiyao/Fadi - To assign]`; `[Team/Release]` claim decision | README/report no longer conflates bounded planning with unrestricted intent understanding | §1; §6; §7.7 |
| P1 | Complete browser/WoT episode isolation: snapshot/restore, context recreation, overlay filtering, supervised tier-4 pause/resume | `[Ruiyao/Fadi - To assign]` | Verified rollback, no cross-session contamination, measurable HITL correction | §4.5 recommendations 1-3 |
| P2 | Expand fusion evaluation to at least 30 episodes per condition, with calibration, false-halt, miss, latency, and downstream metrics | `[Yixin - TODO]`, with shared environment support | Identical seeded episodes and independent oracle labels | §9.2; §9.5 |
| P2 | Compare Bayesian fusion only if calibration data is sufficient; otherwise retain the calibrated heuristic fallback | `[Yixin - Conditional TODO]` | Posterior is consumed by verifier/CIM and beats the baseline, or no Bayesian claim is made | §9.3-9.6 |
| P2 | Rerun the agentic benchmark path and report scripted replay only as an upper bound; broaden baseline ablations | `[Ruiyao/Fadi - To assign]` | Separate agentic/scripted tables under one protocol | §1 M1; §6; §7.8 |
| P2 | Add or formally revise setup entrypoints, ports, and the clean-clone runbook | `[Ruiyao/Fadi - To assign]`; `[Team/Release]` acceptance | A new machine can install, test, and run the demo from one documented path | §5 D10; §6 |

## 5. Final Boundary

- `[Yixin]` has delivered the runtime-control loop, fusion/recovery integration, transition evidence, and review-gated adaptation boundary.
- The next priority is evidence and release discipline, not adding more architectural vocabulary.
- Bayesian fusion is not mandatory now. PDF §9.6 explicitly accepts a calibrated heuristic fallback, which is the defensible choice with only seven scenarios.
- Natural-language intent recognition is outside the implemented action-runtime boundary. The team must either state that boundary clearly or add an upstream parser.
