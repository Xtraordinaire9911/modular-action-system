# Implementation Status

This page separates implemented behavior from partial integrations and future
research claims. The authoritative integration branch is `develop`; feature
branches must pass CI before being merged there, and `main` changes only through
a verified release pull request.

| Claim | Status | Code evidence | Test or demo evidence |
|---|---|---|---|
| Canonical runtime state | Implemented | `src/runtime/cognitive_map.py` is the only mutable episode map; `src/planner/cognitive_map.py` derives a read-only planner view. | `tests/test_architecture_unification.py` |
| Multi-source fusion gate | Implemented, calibrated rule-first | `src/verification/conflict_detector.py` retains raw assertions, handles conflicting and required-but-missing sources, writes accepted fused state, and blocks System 1 on unresolved uncertainty. | `tests/test_epistemic_runtime.py`, `tests/test_fusion_calibration.py`; live conflict/calibration cases |
| Active perception | Implemented | `src/verification/active_perception.py` re-observes before continue/escalate. | live `dom_wot_conflict_active_perception` case |
| Bounded zero-shot action planning | Implemented | `GoalSpec -> ActionContext -> DeclarativeRuntimeTaskPlanner -> PrimitiveAction`; no benchmark keyword/regex plan is hidden in the runtime. | `tests/test_runtime_goal_episode.py`; live normal-goal case |
| Natural-language intent to `GoalSpec` handoff | Implemented for bounded supported intents; model optional | `scripts/run_intent_episode.py` passes the `GoalSpec` produced by `IntentPlanner` through `RuntimeEpisodeSpec.goal_spec` into `RuntimeEpisodeRunner.run_goal_episode` and CIM. The labelled rule fallback is used when no model client is configured; unrestricted intent inference is not claimed. | `tests/test_intent_planner.py`, `tests/test_runtime_goal_episode.py`; `scripts/run_intent_episode.py` |
| Observe-plan-act-verify loop | Implemented | CIM executes one primitive at a time, re-observes, re-fuses, replans, and enforces episode budgets. | `tests/test_runtime_goal_episode.py`; live normal-goal case |
| Recovery decision and execution | Implemented | Retry, reroute, and rollback choices are actually executed and independently re-observed/verified. | `tests/test_runtime_episode_recovery.py`; live timeout and rollback cases |
| False-success detection | Implemented | Transition records separate executor success from postcondition success. | live postcondition-mismatch case |
| System-1 reflex cache | Implemented | Cached grounding is used only after fusion/safety/precondition gates and is invalidated after failure; the live repeat case reports warm-up/repeat episode ids, cache-hit rate, routing latency, total transition latency, and amortized latency. | `tests/test_epistemic_runtime.py`, `tests/test_live_runtime_demo.py`; live repeat case |
| Transition/failure ledger | Implemented | Append-only transition JSONL links episode, state ids, affordance key, backend, verification, and recovery. | `src/runtime/episode.py`, `src/adaptation/trace_ledger.py` |
| Trace-driven policy/skill evolution | Implemented as proposals only | Repeated verified evidence can create candidate proposals; production policy or skill semantics are never auto-modified. | `tests/test_skill_proposal.py`, `tests/test_policy_closed_loop.py` |
| Live runtime metrics | Implemented | Metrics are derived from runtime results and transition ledgers and carry `data_source` plus episode ids. | live `measured_metrics.json`; `tests/test_metrics_aggregator.py` |
| Unified episode runner | Implemented | `RuntimeEpisodeRunner` is the shared runtime envelope for structured `GoalSpec` episodes and scripted benchmark/demo envelopes; scripted solvers are labeled separately and are not claimed as agentic planning. | `tests/test_runtime_episode_runner.py`, `tests/test_miniwob_tasks.py`, `tests/test_pipeline.py` |
| Repeated fusion/recovery campaign | Implemented, initial full campaign complete | The campaign runner creates deterministic 7-condition × N repetition plans, records unique episode ids/seeds, reset evidence, independent oracle labels, replay config, and condition-level precision/recall/false-halt/miss/latency summaries. An initial 30×7 live campaign has been run and saved as evidence; broader publication claims still require independent reruns/holdout discipline. | `tests/test_live_fusion_campaign.py`; `artifacts/live_fusion_campaign_full/fusion_campaign_summary.json` |
| Browser isolation | Implemented as Playwright context isolation | Each task gets an isolated browser context. This is a web isolation analogue, not a claim of full Windows UFO2 PiP/RDP isolation. | `src/perception/browser_session.py` |
| Project PiP MVP | Implemented for serialized web/WoT episodes | The isolated entry points atomically lease/checkpoint/reset WoT, recreate the browser context before observation, restore in `finally`, and exclude runtime overlays. A server-held lease protects the single global mock room across separate providers. | `src/isolation/episode.py`; `tests/test_episode_isolation.py`, `tests/test_runtime_intervention.py` |
| Supervised Tier-4 intervention | Implemented | The runtime enters pausing/waiting/resuming states; approval authorizes one pending action, while takeover Resume forces fresh observation, fusion, cached-grounding invalidation, and replanning. Actor, decision, latency, correction, and resume evidence are recorded. | `src/runtime/intervention.py`; `tests/test_intervention.py`, `tests/test_runtime_intervention.py` |
| Full UFO2 Windows RDP PiP | Future | No Windows child session, nested desktop host, or independent OS input/process boundary is claimed. | Requires a Windows-specific isolation provider and host UI. |
| Visual observation | Partial | Screenshots and existing SoM/VAM contracts are available, but the live smart-room suite does not claim a trained visual detector. | `src/perception/som_parser.py`, `src/vam/` |
| Fusion calibration | Implemented, initial campaign | A labeled live campaign reports threshold ROC/confusion, false halt/miss, and detection latency. The campaign is intentionally small and should be expanded before publication. | `python -m src.pipeline --fusion-calibration` |
| Locked fusion holdout | Implemented, initial 20/10 split complete | The full 30×7 campaign can be split per condition into calibration and locked holdout; threshold is selected only on calibration and then reused unchanged on holdout. | `tests/test_fusion_holdout.py`; `artifacts/live_fusion_holdout/fusion_holdout_report.json` |
| Probabilistic/Bayesian fusion | Promotion review + gate-enabled impact complete | Bayesian can be evaluated as a report-only comparator, as shadow-mode evidence, or as a configurable `EpistemicArbiter(fusion_strategy="bayesian_gate")` gate that controls `allow_system1` while preserving existing fused-state selection. Initial/rerun holdouts favor Bayesian shadow; a 120-trial gate-enabled live ambiguous campaign reaches balanced accuracy 1.0 with no misses or false halts; a live runtime recovery-impact run shows no TSR/recovery regression. Promotion review recommends Bayesian gate as the default candidate while keeping rule-first configurable. | `tests/test_bayesian_fusion_comparator.py`, `tests/test_fusion_shadow_strategies.py`, `tests/test_bayesian_shadow_stability.py`, `tests/test_bayesian_gate_promotion_review.py`, `tests/test_gate_enabled_recovery_impact.py`, `tests/test_epistemic_runtime.py`; `artifacts/bayesian_gate_promotion_review/bayesian_gate_promotion_review.json`, `artifacts/gate_enabled_recovery_impact/gate_enabled_recovery_impact_report.json`, `artifacts/live_ambiguous_fusion_bayesian_gate_full/live_ambiguous_fusion_summary.json` |
| Noisy fusion stress set | Synthetic comparator stress only | Ambiguous/noisy source cases exercise weak stale evidence, delayed WoT recovery, low-reliability DOM, and partial missing WoT. Bayesian outperforms a fixed rule threshold on this synthetic stress set, but this is not live evidence and only motivates future live ambiguous cases. | `tests/test_noisy_fusion_stress.py`; `artifacts/noisy_fusion_stress/noisy_fusion_stress_report.json` |
| Live ambiguous fusion campaign | Initial/rerun/shadow + gate-enabled evidence complete | Ambiguous profiles use `stale_offset`, `read_delay_ms`, `drop_probability`, and `source_reliability` metadata in the smart-room environment. Two 120-trial campaigns have 20/10 per-profile locked holdouts favoring Bayesian shadow; a separate 120-trial `bayesian_gate` run confirms the configured gate can make the improved block/allow decisions live. | `tests/test_live_ambiguous_fusion_campaign.py`, `tests/test_live_ambiguous_fusion_holdout.py`; `artifacts/live_ambiguous_fusion_full/live_ambiguous_fusion_summary.json`, `artifacts/live_ambiguous_fusion_rerun/live_ambiguous_fusion_summary.json`, `artifacts/live_ambiguous_fusion_bayesian_gate_full/live_ambiguous_fusion_summary.json` |
| Open-web failure coverage | Randomized controlled browser holdout complete; real open-web still future | Open-web failure modes are explicitly separated into mechanism-ready, controlled/mock evidence, runtime-envelope evidence, controlled browser fixture evidence, and real open-web evidence levels. Six oracle-labeled local failure families now have seeded behavioral parameters, disjoint dev/locked-holdout domains and signatures, real Playwright Chromium execution, and fresh oracle verification through `RuntimeEpisodeRunner.run_skill_episode`. It still records zero real open-web evidence. | `tests/test_open_web_failure_coverage.py`, `tests/test_open_web_mock_failure_suite.py`, `tests/test_open_web_mock_runtime_runner.py`, `tests/test_open_web_playwright_fixture_runner.py`, `tests/test_open_web_randomized_holdout.py`, `tests/test_live_browser_claims.py`; `artifacts/open_web_failure_coverage/open_web_failure_coverage_report.json`, `artifacts/open_web_mock_failure_suite/open_web_mock_failure_suite_report.json`, `artifacts/open_web_mock_runtime_suite/open_web_mock_runtime_episode_report.json`, `artifacts/open_web_playwright_fixture_suite/open_web_playwright_fixture_report.json`, `artifacts/open_web_randomized_holdout/open_web_randomized_holdout_report.json` |

## Verification

Run the repository checks:

```bash
uv run pytest --tb=short -q
uv run ruff check .
uv run black --check .
uv run mypy src/ --ignore-missing-imports
```

Run the real Docker + Playwright tracer bullet after starting the smart-room
environment:

```bash
uv run python -m src.pipeline --live-demo
uv run python -m src.pipeline --live-ablation
uv run python -m src.pipeline --fusion-calibration
```

The live demo writes screenshots, an episode report, transition/failure ledgers,
a recovery report, and measured metrics. The report is successful only when its
checks are derived from the persisted runtime evidence.
