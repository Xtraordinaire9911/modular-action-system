# Yixin Runtime / Recovery Responsibility Dossier

> Baseline: local `develop` worktree, 2026-08-13. This is a convergence
> dossier, not a completion claim. Implementation and verified closure are
> tracked separately in `.codex/yixin_recovery_convergence_execution_plan.md`.

## 1. Current finding

The six open-web fixtures prove six-family failure detection. Five families now
expose bounded recovery capabilities: overlay obstruction, session continuation,
compensation plus alternate commit, visual-state re-observation, and
equivalent-alternative execution. Runtime passes those capabilities and fresh
failure evidence through `PlannerPort`; it does not select one. Autocomplete is explicitly
outside Yixin's Runtime/recovery delivery scope: its suggestions and constraints
belong to environment/perception, and choosing a valid value belongs to the
Agent/Planner. It remains only as a false-success detection witness.

The earlier 30/30 artifact depended on a deterministic recovery selector added
to the default Planner fallback. That selector crossed the ownership boundary
and has been removed. Current evidence must therefore stop at the typed Planner
handoff until the Planner owner supplies an implementation; the old result is
not an active autonomous-recovery claim.

The main chain defect was also architectural: after a primitive failure,
`ContinuousInteractionManager` called a separate `PreconditionRepairPlanner`
inside Runtime. That made Runtime both executor/verifier and semantic decision
maker. The original Agent/Planner never received the failure. The implemented
boundary is now:

```text
GoalSpec -> externally owned PlannerPort implementation -> validated PrimitiveAction
         -> Runtime execute -> fresh observe/fuse/verify
         -> typed FailureContext -> the same PlannerPort is called again
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

## 3. Bounded capability status by failure family

| Family | What exists now | Missing environment/perception capability | Owner/dependency | Bounded outcome and present evidence |
|---|---|---|---|---|
| Overlay obstruction | Blocking element, structurally marked dismiss control, target, mutable oracle | Planner must choose the observed `remediates` capability | Perception exposes; Planner selects; Runtime executes/verifies | Runtime handoff ready; autonomous scene pending Planner integration |
| Session expiry | Expired session, stale form, observed restoration capability, persisted oracle | Real credentials and Planner selection remain external | Environment authors; Planner selects; Runtime executes/verifies | Runtime handoff ready; full scene pending dependencies |
| Optimistic UI rollback | False optimistic state, compensation relation, alternative commit, backend oracle | Planner selection and production compensation contract | Skill/environment authors; Planner selects; Runtime executes/verifies | Runtime handoff ready; full scene pending dependencies |
| DOM/visual disagreement | Observed recheck capability and generic active-perception interface | VLM implementation/evidence and Planner selection | Perception supplies; Planner selects; Runtime fuses/verifies | Runtime interfaces ready; no Yixin VLM implementation claim |
| Visible ineffective affordance | Ineffective click plus observed `equivalent_to` alternative | Planner must choose the alternative | Environment declares; Planner selects; Runtime executes/verifies | Runtime handoff ready; autonomous scene pending Planner integration |

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
3. A failed non-transparent action returns through the same injected
   `PlannerPort`.
4. Runtime contains no relation-ranking or recovery-selection policy; the
   default controller deterministically escalates until a Planner owner is injected.
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
16. PiP/human intervention is integrated as Tier 4 after the Planner handoff:
    a recoverable semantic failure first returns through `PlannerPort`
    first; takeover resume re-observes and then re-enters that planner rather
    than becoming a second recovery authority.

### Still open

1. The production recovery implementation behind `PlannerPort` is absent. The
   default controller deliberately escalates on `FailureContext`. Selection
   belongs to the Agent/Planner owner.
2. Real credential login/token refresh and production compensation still depend
   on environment/Skill providers; bounded controls are contract witnesses.
3. Runtime exposes the generic active-perception interface. VLM implementation,
   provider configuration, and evidence belong to the perception owner.
4. Real unrestricted open-web evidence is absent; the runner uses controlled
   local fixtures even though production planning has no family/case branch.
5. `src/planner/system2_recovery.py` is a legacy planner-layer request builder
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
| HITL pause/resume | Done | Typed token; no execution while paused; resume requires fresh observation and revalidation |
| Goal-path compensation witness | Done, bounded | Compensation is executable, idempotency is declared, alternate commit is independently verified |
| Active-perception recovery | Done, bounded | VLM probe result has source/provenance/time; unresolved or ambiguous evidence remains blocked |
| Alternative-affordance replanning | Done, bounded | Agent chooses an observed equivalent relation and Runtime prevents stale execution |
| Evaluation de-specialization | Done for supported local scope | One environment-adapter protocol runs five families; fixture ID remains evaluation metadata only |
| Held-out generative validation | Done for supported local scope | New labels/IDs/timings/state variants pass without new production branches |

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

Yixin's bounded Runtime side may be called complete when the Planner and
perception owners pass their proposals through the published interfaces and
full integration tests verify execution, continuation, and final oracle truth.
Until then, five-family autonomous recovery and a finished video must remain
open. Autocomplete does not gate Yixin's Runtime scope.
