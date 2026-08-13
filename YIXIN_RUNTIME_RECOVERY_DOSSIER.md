# Yixin Runtime / Recovery Responsibility Dossier

> Baseline: local `develop` worktree, 2026-08-13. This is a convergence
> dossier, not a completion claim. Implementation and verified closure are
> tracked separately in `.codex/yixin_recovery_convergence_execution_plan.md`.

## 1. Current finding

The six open-web fixtures currently prove **six-family failure detection**, not
six-family successful recovery. Only overlay obstruction has a reachable,
automated success path. The other five environments either omit the capability
needed to recover or do not expose enough authoritative evidence to choose and
verify a recovery.

The main chain defect was also architectural: after a primitive failure,
`ContinuousInteractionManager` called a separate `PreconditionRepairPlanner`
inside Runtime. That made Runtime both executor/verifier and semantic decision
maker. The original Agent/Planner never received the failure. The implemented
boundary is now:

```text
GoalSpec -> existing System2Planner -> validated PrimitiveAction
         -> Runtime execute -> fresh observe/fuse/verify
         -> typed FailureContext -> the same System2Planner replans
         -> Runtime validates/executes/verifies again
```

Runtime retains only bounded, semantics-preserving operations: validation,
idempotent transient retry, equivalent-backend reroute, rollback when a real
rollback contract/executor exists, budgets, cancellation, logging, and
fail-closed escalation.

## 2. Responsibility and authority boundaries

| Surface | Authoritative owner | Yixin responsibility | Not Yixin's implementation obligation |
|---|---|---|---|
| User language -> `GoalSpec` | Ruiyao intent layer | Consume and validate typed `GoalSpec`; return typed insufficiency | Train/configure the NL/LLM planner |
| `GoalSpec` -> Skill | Fadi skill layer | Execute a supplied Skill contract and expose runtime outcomes | Skill library content, Skill selection policy, PiP demo logic |
| Goal/Skill -> next semantic action | Existing Agent/Planner; owner depends on entry layer | Supply sanitized `ActionContext`, fresh failure evidence, budgets; validate planner output | Build a second RecoveryPlanner or embed benchmark answers in Runtime |
| Screenshot/VLM/DOM/WoT perception | Ruiyao perception/environment | Fuse source-attributed assertions; reject stale/conflicting evidence | Implement or run the VLM; author environment recovery controls |
| Primitive execution | Backend/environment adapter | Route validated affordance IDs, enforce safety/timeouts, record result | Invent a successful side effect absent from the environment |
| Goal truth | Environment/backend oracle | Re-observe and verify against independent oracle/postcondition | Let planner, executor return value, page text, or fixture label author success |
| Recovery policy | Shared: Agent chooses semantics; Runtime enforces mechanics | Typed handoff, transparent retry/reroute/rollback, safety, budgets, ledger, escalation | Choose a domain-changing repair from hidden Runtime rules |
| Metrics/evidence | Runtime ledger + independent oracle | Derive RTA/DA/recovery/final-success fields from episodes | Claim open-web generalization from local scripted fixtures |

One fact has one owner: the environment owns world state; perception owns
observations; Agent/Planner owns semantic proposals; Runtime owns admissibility
and execution; verifier/oracle owns success; reports are non-authoritative
projections. The environment adapter is therefore the authority for capture
time and request binding. Runtime checks the adapter's nonce/time contract and
rejects missing, replayed, or pre-request observations; it does not pretend to
independently reconstruct when an adapter captured the world.

## 3. Environment-side gaps by failure family

| Family | What exists now | Missing environment/perception capability | Owner/dependency | Bounded outcome and present evidence |
|---|---|---|---|---|
| Overlay obstruction | Blocking element, structurally marked safe dismiss control, target, mutable oracle | No critical environment gap for the bounded case | Yixin consumes Ruiyao-style observation contract | **Verified:** failure -> Agent replan -> repair -> resume -> fresh goal oracle |
| Session expiry | Expired session and stale save form | Login/token-refresh affordance, credential/HITL handoff, post-login continuation state, persisted backend oracle | Environment + Fadi supervised/PiP handoff; Runtime pause/resume contract is Yixin | **Target:** `user_action_required`; current six-family artifact only proves `terminal_failure` detection |
| Autocomplete async mutation | Input and submit that always rewrites value | Valid suggestion list or constraint schema, selection affordance, clarification route, stable accepted-value oracle | Environment/perception + upstream Agent | **Target:** replan/clarification; current artifact only proves mismatch detection |
| Optimistic UI rollback | UI reports submit while backend oracle stays false | Alternative commit route and/or real compensation/rollback operation with oracle-visible completion | Environment/Skill contract; Yixin executes supplied rollback | **Target:** false-success rejection then rollback/escalation; current artifact proves rejection only |
| DOM/visual disagreement | Conflicting DOM and visual state | Real screenshot-derived visual assertion, provenance/confidence/timestamp, active probe that can resolve the conflict, action handlers that update oracle | Ruiyao VLM/perception + environment | **Target:** active perception/escalation; current artifact is scripted conflict detection only |
| Visible ineffective affordance | Accepted click with no state change | Equivalent alternative affordance/backend, repair action, or explicit unsupported-state signal | Environment/Skill contract | **Target:** replan/escalation; current artifact proves ineffective-action detection only |

Adding those capabilities is not “cheating” if they are environment contracts
available to any agent and varied in held-out tests. It is cheating if Runtime
branches on fixture ID, failure-family name, known selector/control text, seed,
or expected answer.

## 4. Chain gaps

### Fixed in this implementation

1. `ActionContext` now carries a sanitized `FailureContext`, attempted-action
   history, and remaining budgets.
2. Recovery metadata needed for planning is exposed without raw selectors or
   backend handles.
3. A failed non-transparent action returns to the same `System2Planner`.
4. The deterministic fallback in the existing Planner can select only a safe,
   idempotent, reversible, explicitly related, independently verifiable repair.
5. Runtime still validates that every planned action references a currently
   observed affordance before execution.
6. Ambiguous or absent repairs fail closed; Runtime does not invent one.
7. `RuntimeStepResult.outcome` projects terminal results onto a closed enum and
   `replan_count` exposes Agent loopbacks without treating them as success.
8. Semantic replanning requires a complete fresh affordance snapshot; a plain
   state delta cannot authorize actions from retained affordances. The snapshot
   must answer the Runtime-issued request nonce and be captured after it.
9. Runtime retains a minimal execution guard over declared risk metadata; this
   is a boundary invariant, not a separate safety research claim.
10. Failure, Agent repair, and resumed goal transitions carry tier-2 causal
    links; the failure ledger is persisted and marks recovery only after the
    final oracle passes.
11. Transparent reroute also requires a complete fresh affordance snapshot; a
    partial state delta cannot authorize a retained backend alternative.
12. Cancellation is preserved as `cancelled` at both loop and attempt
    boundaries and never projected as budget exhaustion.
13. Skill and Goal paths re-check cancellation/deadline after an admitted
    action, before projecting a newly satisfied postcondition as success.
14. Runtime plan validation checks both current affordance identity and
    primitive compatibility; observing a button does not authorize `type`.
15. Every verified terminal result names the transition whose fresh
    postcondition/oracle established success, so the recovery chain no longer
    relies on report-side inference.
16. PiP/human intervention is integrated as Tier 4 after autonomous recovery:
    a recoverable semantic failure still returns to the same `System2Planner`
    first; takeover resume re-observes and then re-enters that planner rather
    than becoming a second recovery authority.

### Still open

1. The production model-backed implementation of `System2Planner` is absent;
   the current default is a declared deterministic fallback. This belongs to
   the Agent/Planner owner, not to Yixin's model responsibility.
2. There is no resumable `user_action_required -> fresh observation -> resume`
   protocol for session login/HITL.
3. Rollback availability is not yet uniformly derived from executable Skill
   contracts in the GoalSpec path.
4. Cross-family browser evaluation still has fixture-specific action/oracle
   adapters and the generalized recovery runner explicitly filters to overlay.
5. DOM/visual conflict does not yet receive real VLM evidence in this suite.
6. `src/planner/system2_recovery.py` is a legacy planner-layer request builder
   used by `PlanningGate`, not by the CIM GoalSpec execution loop. It should be
   renamed or unified by its planner owner later to avoid architectural naming
   confusion; Runtime does not invoke it.

## 5. Terminal outcomes and recovery transitions

Terminal world-result projections are closed in `RuntimeOutcome`:

| Outcome | Meaning | Legal next transition |
|---|---|---|
| `verified_success` | Fresh independent goal postcondition passed | terminal only |
| `user_action_required` | Credentials, clarification, approval, or ambiguous recovery | pause/escalate; resume only from fresh observation |
| `cancelled` | Cancellation token observed | terminal; no further side effect |
| `budget_exhausted` | Step/deadline/backend/retry budget ended | terminal escalation |
| `unsupported` | Required environment/executor/oracle capability does not exist | deterministic fail-closed terminal |
| `terminal_failure` | Supported execution ended without verified goal or safe recovery | terminal only |

Non-terminal control is typed separately because it is not success truth:
`retry`, `reroute`, `rollback`, `escalate_human`, and `abort` are
`RecoveryActionType`; semantic loopback is
`PlannerHandoff.REPLAN_REQUIRED`. A replan transition must return to the same
Agent/Planner and every resulting primitive still passes Runtime validation.

Illegal transitions include: success without fresh verification; planner output
directly mutating world state; executing stale/unobserved affordance IDs;
retrying non-idempotent actions as transparent retry; resuming after
cancellation; and turning a missing capability into synthetic success.

## 6. Recovery implementation gaps

| Gap | Priority | Concrete acceptance criterion |
|---|---:|---|
| Agent replan state-machine properties | P0 | Generated/parameterized tests cover success, replan, ambiguity, stale proposal, budget, cancellation, and no production fixture branches |
| HITL pause/resume | P1 | Typed token; no execution while paused; resume requires fresh observation and revalidation |
| Goal-path rollback contract | P1 | Compensation is executable, idempotency is declared, checkpoint restoration is independently verified |
| Active-perception recovery | P1, dependency | Conflict context reaches Agent; probe result has source/provenance/time; unresolved conflict remains blocked |
| Alternative-affordance replanning | P1 | Agent can choose an equivalent newly observed affordance; Runtime validates equivalence/safety and prevents loops |
| Evaluation de-specialization | P1 | One environment-adapter protocol runs all supported families; fixture ID remains evaluation metadata only |
| Held-out generative validation | P1 | New labels/IDs/layouts/timings/state variants pass without new production branches |

## 7. Causal model for repeated reopenings

The reopenings share one cause: evidence and decision authority were collapsed.

- Six detected failures were reported near recovery evidence, so detection was
  mistaken for recovery.
- One overlay repair generalized across labels/layouts, so within-family
  invariance was mistaken for cross-family generalization.
- The repair selector was then placed inside Runtime, so a generic metadata
  rule was mistaken for agentic replanning.
- Fixture environments without reachable success paths were implicitly treated
  as if better Runtime logic alone could recover them.

These are respectively verification/claim, scope, architecture, and
environment-contract defects. The solution is not another case branch; it is
separate authorities, typed outcomes, reachable environment capabilities, and
invariant-based tests.

## 8. Reference-practice comparison

- WebArena evaluates functional end states because multiple valid trajectories
  may exist and identifies failure recovery as a major agent weakness
  ([Zhou et al., 2023](https://arxiv.org/abs/2307.13854)). This supports keeping
  goal truth in an independent oracle rather than in a scripted action path.
- BrowserGym/WorkArena unifies observation and action spaces and explicitly
  distinguishes real `env.step()` interaction from directly invoking task
  oracles ([Drouin et al., ICML 2024](https://proceedings.mlr.press/v235/drouin24a.html),
  [WorkArena repository](https://github.com/ServiceNow/WorkArena)).
- BrowserArena provides step-level failure annotations, including obstruction
  phenomena such as pop-up banners
  ([Anupam et al., 2025](https://arxiv.org/abs/2510.02418)).

Inference: current benchmark practice supports outcome-based verification and
rich failure traces, but does not by itself prove production recovery assurance.
This project therefore needs both representative browser episodes and
state-machine/invariant tests.

## 9. Non-goals

- No LLM/VLM training or provider configuration in Yixin's scope.
- No fake login, backend commit, suggestion, or alternative control added by
  Runtime.
- No unrestricted open-web claim from local HTML fixtures.
- No second RecoveryPlanner and no fixture-specific production policy.
- No broad framework rewrite beyond the proven Agent/Runtime boundary gap.

## 10. Closure evidence required

Implementation may be called complete when the typed handoff, loopback, tests,
and docs land and the bounded implementation checks pass. Cross-family
generalized recovery remains open until the five
missing environment/perception capabilities are supplied (or explicitly
declared unsupported), all supported outcomes pass held-out state-machine and
browser cases, full CI/live checks pass, and an independent fresh-context review
finds no hidden fixture branch or duplicated success authority.
