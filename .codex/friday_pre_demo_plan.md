# Ownership by demo

Each member owns one runnable demo and the matching code walkthrough. The three
demos together cover the architecture from natural-language intent and
perception, through skill selection, to primitive execution, verification, and
recovery.

## 1. Ruiyao — Intent-driven Smart-room + real VLM

### Tasks to complete

1. Use one natural-language request as the only task input, for example:
   "Prepare Room A for a presentation at 14:00; set 22 degrees and lights to
   40 percent."
2. Run the request through `IntentPlanner` and emit a provenance-labelled
   `GoalSpec`; do not substitute a hand-authored GoalSpec in the demo runner.
3. Pass the GoalSpec into the existing `RuntimeEpisodeRunner + CIM` path rather
   than creating another orchestration loop.
4. Connect the live smart-room environment through the current React dashboard,
   node-wot Things, Thing Directory, BrowserSession/PAM, and runtime TD
   discovery. Do not use a runner-side list of static selectors or endpoints as
   the plan.
5. Capture a fresh screenshot from the live environment and send the screenshot,
   or an explicitly identified crop, to a real VLM. A text-only model request or
   scripted visual mark does not count as VLM integration.
6. Convert the VLM response into a structured observation or affordance with
   model name, screenshot/region identity, confidence, and provenance.
7. Make the VLM result affect the same runtime episode—for example by
   disambiguating observation, verification, or action selection. Displaying an
   unused caption is insufficient.
8. Implement a clear fallback for model unavailability or low-confidence output.
9. Use fresh environment observations for postcondition and final-readiness
   verification; HTTP 200 or executor success alone is insufficient.
10. Reset the environment through the current episode-isolation boundary and
    show that a second run does not inherit room, browser, or device state.
11. Produce one episode artifact containing intent provenance, GoalSpec, VLM
    evidence, observations, selected runtime operations, primitives, backends,
    final oracle state, and transition IDs.
12. Add a real Chromium integration test. CI may mock the external VLM call,
    but the Friday live demo must use the actual VLM.

### Code walkthrough

- `src/planner/intent_planner.py`
- `scripts/run_intent_episode.py`
- Smart-room React/node-wot environment and Thing Directory discovery
- BrowserSession/PAM and the added VLM adapter/integration
- The episode artifact showing that the VLM result entered the runtime

The old `run_demo.py --live-agent` replay alone is not sufficient. The demo
must show the latest intent, runtime, isolation, and VLM integration in one
episode.

## 2. Fadi — GoalSpec to Skill + supervised takeover/PiP

### Tasks to complete

1. Accept an actual `GoalSpec` and show how it selects and instantiates a Skill.
2. Make the `GoalSpec -> Skill -> typed Primitive Actions` path explicit and
   executable; do not present only contracts or a slide diagram.
3. Show the Skill Library and Skill Contract implementation, including parameter
   binding, preconditions, postconditions, and failure reporting.
4. Ensure the runner does not manually invoke a pre-authored sequence of skills.
5. Hand the selected Skill into the existing runtime/CIM primitive-execution
   path and retain the selected skill and generated primitives in the trace.
6. Demonstrate isolated task/session state.
7. Trigger tier-4 supervised takeover and show pause, human decision, resume or
   termination, and persisted audit evidence.
8. Show that the agent respects the human decision after control is returned.
9. If claiming PiP, provide a real interface where the human can observe the
   live task and take control. Browser-context isolation or a read-only panel
   alone is not full PiP.
10. If that interface is not completed, title the demo and slides
    `Supervised takeover / isolation toward PiP`.

### Code walkthrough

- Goal/Skill contracts and Skill Library
- GoalSpec-to-Skill selection and parameter binding
- Skill-to-Primitive handoff into Runtime/CIM
- Session isolation and supervised takeover implementation
- Trace containing selected Skill, primitives, human decision, and resumed state

## 3. Yixin — Live-browser failure, verification, and recovery

### Tasks to complete

1. Maintain six behavior-changing HTML failure fixtures covering overlay
   obstruction, session expiry, autocomplete validation mutation, optimistic
   rollback, DOM/visual disagreement, and visible-but-ineffective affordances.
2. Keep randomized development and locked holdout variants disjoint and record
   the injected parameters and seeds so the browser episodes are reproducible.
3. Run the fixtures in real Chromium and show that each fault changes observable
   page behavior rather than merely changing a fixture label.
4. Feed fresh DOM, visual, and execution evidence into the existing fusion and
   conflict-detection path.
5. Explain the Bayesian gate parameters and show how confidence, disagreement,
   source reliability, and observation freshness determine whether System 1 may
   continue or active perception/escalation is required.
6. Separately explain how `RecoveryCascade` selects retry, reroute, rollback,
   human escalation, or abort from execution result, idempotency, safety level,
   backend availability, retry budget, rollback availability, and unresolved
   conflict. Preserve the selected tier and decision steps in the episode trace.
7. Use one failure—preferably randomized overlay obstruction—as the complete
   live recovery demonstration.
8. Show the original primitive action failing while the randomized fault is
   active; executor completion must not be treated as task success.
9. Use a fresh observation to identify the changed environment state, blocking
   cause, and available remediation affordance.
10. Represent the remediation as a runtime action and execute it through the
    existing CIM/effectors path rather than direct runner-side JavaScript, DOM
    removal, or an authored success flag.
11. Verify the remediation postcondition with another fresh observation before
    retrying or replanning the original action.
12. Retry or replan the original task and verify the user's original goal with
    a separately acquired fresh oracle observation.
13. Link the failed transition, diagnosis, remediation, remediation verification,
    retried action, and final verification in the transition ledger.
14. Produce a compact Friday artifact containing the randomized fixture
    parameters, evidence inputs, gate decision, recovery tier, primitives,
    observations, oracle result, and final outcome.
15. Add a real-Chromium integration test that fails if remediation is bypassed,
    stale evidence is reused, or the original goal is not achieved.
16. Run the six-family detection/holdout suite and the complete recovery episode
    before the rehearsal, then preserve the commands and results used in the
    demo.
17. Clearly distinguish three claims in the presentation: six controlled failure
    families are detected, one family demonstrates end-to-end recovery, and no
    real open-web evidence is claimed.

### Code walkthrough

- `src/runtime/episode_runner.py`
- `src/runtime/continuous_interaction_manager.py`
- `src/runtime/task_planner.py` and typed primitive actions
- `src/runtime/live_environment.py` and browser effectors
- `src/verification/conflict_detector.py` — fusion/conflict logic and Bayesian
  gate configuration
- `src/verification/condition_evaluator.py`
- `src/recovery/recovery_cascade.py`
- `evaluation/open_web_playwright_fixture_runner.py` and the randomized holdout
  fixture definitions
- The real-Chromium recovery integration test
- The transition ledger, gate/strategy evidence, and final fresh-oracle result

## Shared Friday tasks

1. Use the formal TUM corporate template for all slides.
2. Include the three-layer architecture:
   `NL intent -> GoalSpec -> Skill -> Primitive Actions`.
3. Connect the three independent demos on one architecture/ownership slide.
4. Show current status and contribution ownership without claiming unfinished
   VLM, real open-web, or full-PiP functionality.
5. Release the tested `develop` state to `main` before the clean-clone rehearsal.
6. Perform one clean-clone rehearsal, verify commands and ports, preload code and
   trace files, and record a backup video.
7. Keep paper/report work outside the Friday task board.

## Friday acceptance checklist

- Each member runs one demo and opens code they actually own.
- Ruiyao shows one integrated Smart-room episode containing a real screenshot,
  real VLM output, provenance, runtime consumption, and fallback behavior.
- Fadi shows an executed GoalSpec-to-Skill-to-Primitive path and supervised
  takeover/isolation evidence.
- Yixin shows browser failure, fresh diagnosis, runtime remediation, retry or
  replan, and fresh final verification.
- Execution traces distinguish executor success from verified task success.
- Claims shown in slides match the evidence produced by the live demos.
- The latest release branch passes CI and can be run from a clean clone.
