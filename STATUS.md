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
| System-1 reflex cache | Implemented | Cached grounding is used only after fusion/safety/precondition gates and is invalidated after failure. | `tests/test_epistemic_runtime.py`; live repeat case |
| Transition/failure ledger | Implemented | Append-only transition JSONL links episode, state ids, affordance key, backend, verification, and recovery. | `src/runtime/episode.py`, `src/adaptation/trace_ledger.py` |
| Trace-driven policy/skill evolution | Implemented as proposals only | Repeated verified evidence can create candidate proposals; production policy or skill semantics are never auto-modified. | `tests/test_skill_proposal.py`, `tests/test_policy_closed_loop.py` |
| Live runtime metrics | Implemented | Metrics are derived from runtime results and transition ledgers and carry `data_source` plus episode ids. | live `measured_metrics.json`; `tests/test_metrics_aggregator.py` |
| Project PiP MVP | Implemented for serialized web/WoT episodes | The isolated entry points atomically lease/checkpoint/reset WoT, recreate the browser context before observation, restore in `finally`, and exclude runtime overlays. A server-held lease protects the single global mock room across separate providers. | `src/isolation/episode.py`; `tests/test_episode_isolation.py`, `tests/test_runtime_intervention.py` |
| Supervised Tier-4 intervention | Implemented | The runtime enters pausing/waiting/resuming states; approval authorizes one pending action, while takeover Resume forces fresh observation, fusion, cached-grounding invalidation, and replanning. Actor, decision, latency, correction, and resume evidence are recorded. | `src/runtime/intervention.py`; `tests/test_intervention.py`, `tests/test_runtime_intervention.py` |
| Full UFO2 Windows RDP PiP | Future | No Windows child session, nested desktop host, or independent OS input/process boundary is claimed. | Requires a Windows-specific isolation provider and host UI. |
| Visual observation | Partial | Screenshots and existing SoM/VAM contracts are available, but the live smart-room suite does not claim a trained visual detector. | `src/perception/som_parser.py`, `src/vam/` |
| Fusion calibration | Implemented, initial campaign | A labeled live campaign reports threshold ROC/confusion, false halt/miss, and detection latency. The campaign is intentionally small and should be expanded before publication. | `python -m src.pipeline --fusion-calibration` |
| Probabilistic/Bayesian fusion | Future, data-gated | Current production fusion is auditable and calibrated heuristic. Seven initial live scenarios are not enough evidence to justify a Bayesian likelihood model. | See runtime-control TODO plan |

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
