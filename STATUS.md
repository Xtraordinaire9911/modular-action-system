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
| Natural-language or LLM task decomposition | Future interface only | The action system accepts structured `GoalSpec`; it does not claim to infer unrestricted user intent. | `src/runtime/goal_spec.py`, `src/runtime/system2_planner.py` |
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
| Visual observation | Partial | Screenshots and existing SoM/VAM contracts are available, but the live smart-room suite does not claim a trained visual detector. | `src/perception/som_parser.py`, `src/vam/` |
| Fusion calibration | Implemented, initial campaign | A labeled live campaign reports threshold ROC/confusion, false halt/miss, and detection latency. The campaign is intentionally small and should be expanded before publication. | `python -m src.pipeline --fusion-calibration` |
| Locked fusion holdout | Implemented, initial 20/10 split complete | The full 30×7 campaign can be split per condition into calibration and locked holdout; threshold is selected only on calibration and then reused unchanged on holdout. | `tests/test_fusion_holdout.py`; `artifacts/live_fusion_holdout/fusion_holdout_report.json` |
| Probabilistic/Bayesian fusion | Experimental comparator implemented | A Bayesian posterior comparator can be evaluated against the locked rule-first holdout without replacing the production gate. On the current holdout it ties rule-first and therefore recommends keeping rule-first as default. | `tests/test_bayesian_fusion_comparator.py`; `artifacts/bayesian_fusion_comparator/bayesian_fusion_comparator_report.json` |
| Noisy fusion stress set | Synthetic comparator stress only | Ambiguous/noisy source cases exercise weak stale evidence, delayed WoT recovery, low-reliability DOM, and partial missing WoT. Bayesian outperforms a fixed rule threshold on this synthetic stress set, but this is not live evidence and only motivates future live ambiguous cases. | `tests/test_noisy_fusion_stress.py`; `artifacts/noisy_fusion_stress/noisy_fusion_stress_report.json` |

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
