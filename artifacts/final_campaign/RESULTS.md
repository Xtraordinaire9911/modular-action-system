# Final campaign — every number the slides and the report claim

Run **29 August 2026** against the running smart room (`docker compose -f
env/docker-compose.yml up -d`): real Chromium, real node-wot servient on :8080,
real Thing Directory on :8082, faults injected through the control plane on :8081.

Not the Flask stand-ins in `env/*/app.py`. Those are mocks; the deck says
"not on a mock", so the campaign was run against the containers.

---

## 1. Whole-system metrics — `artifacts/live_runtime_demo/measured_metrics.json`

Six episodes from five cases (the reflex case contributes a warm-up and a repeat).

| Metric | Value | n | Deck slide 15 said | Status |
|---|---|---|---|---|
| Task success rate (TSR) | **0.83** | 6 | 0.83 | ✅ unchanged |
| Safe action rate (SAR) | **1.00** | 6 | 1.00 | ✅ unchanged |
| Verified recovery (RecoverySuccessRate) | **1.00** | 2 | 1.00 | ✅ unchanged |
| Recovery utilisation (RecoveryTriggerRate) | **0.33** | 6 | 0.33 | ✅ unchanged |
| Recovery tier accuracy (RTA) | **1.00** | 2 | — | new, worth adding |
| Mean task latency (MTL) | **111 ms** | 6 | 115 ms | ⚠️ **CHANGED** |
| **Backend routing accuracy (BRA)** | **1.00** | **12** | *n/a*, n=0 | ⚠️ **NOW MEASURED** |
| **Conflict resolution rate (CRR)** | **1.00** | **1** | not reported | ⚠️ **NOW MEASURED** |
| Postcondition coverage (PCR) | 1.00 | 12 | — | |
| Postcondition success (PCS) | 0.83 | 12 | — | |

### Stability — 6 independent repetitions

Every rate metric was **identical in all six runs**. Only latency moved.

| rep | TSR | SAR | RTR | RSR | RTA | BRA | CRR | MTL |
|---|---|---|---|---|---|---|---|---|
| cold | 0.833 | 1.0 | 0.333 | 1.0 | 1.0 | 1.0 | 1.0 | 122.3 |
| 1 | 0.833 | 1.0 | 0.333 | 1.0 | 1.0 | 1.0 | 1.0 | 114.5 |
| 2 | 0.833 | 1.0 | 0.333 | 1.0 | 1.0 | 1.0 | 1.0 | 110.4 |
| 3 | 0.833 | 1.0 | 0.333 | 1.0 | 1.0 | 1.0 | 1.0 | 111.5 |
| 4 | 0.833 | 1.0 | 0.333 | 1.0 | 1.0 | 1.0 | 1.0 | 107.7 |
| 5 | 0.833 | 1.0 | 0.333 | 1.0 | 1.0 | 1.0 | 1.0 | 112.7 |

Latency, warm runs only (excluding the cold first run after container start):
**mean 111.4 ms, sd 2.5 ms, range 107.7–114.5**. All six: mean 113.2, range 107.7–122.3.

Per-run files: `artifacts/final_campaign/reps/rep1..4.json`.

**Recommended slide wording:** `111 ms` with `n = 6`, and say aloud that warm runs
span 108–115 ms and the first run after a cold container start is ~122 ms. A
single latency draw is not reproducible; the range is.

---

## 2. Why BRA and CRR stopped being `n/a`

This was recommendation #1 on the old deck ("logic exists, counters do not"), and
it is now done.

`evaluation/live_runtime_demo.py` computed both metrics but never passed the
counters. Two changes:

1. **`evaluation/live_runtime_demo.py`** now passes `expected_backends` and
   `conflicts_by_episode` into `dataset_from_runtime_results`.
2. **`evaluation/metrics_aggregator.py`** looks the backend oracle up by
   **`skill_id` first**, then episode, then task.

**Why skill-level and not episode-level.** The rollback episode dispatches two
different backends: `set_temperature_live` → `wot`, then
`restore_temperature_live` → `restore`. An episode-level label would score the
rollback effector against the goal's backend and call a correct dispatch a
mis-route, giving BRA 11/12 for a router that made no mistake.

**The oracle is not derived from the run.** The labels are written from the case
definitions — `book_room` drives the dashboard form, the temperature skills write
a WoT property, the rollback skill dispatches the restore effector:

```python
_EXPECTED_BACKENDS = {
    "book_room": "dom",
    "set_temperature_live": "wot",
    "set_temperature_reflex": "wot",
    "restore_temperature_live": "restore",
}
```

Comparing the router against its own choice would score 1.0 by construction. The
result here **is** 1.0, but it is 1.0 against labels fixed before the run, and a
mis-route would have shown.

CRR needs no oracle: a conflict exists because the sources disagreed and the
active-perception probe fired, and it is resolved or it is not. One conflict
occurred, in `live_conflict_resolution`, and it was resolved —
`{"action": "active_perception_probe", "reason": "fresh observation removed
blocking conflict", "resolved": true}`.

⚠️ **CRR n = 1.** One conflict is a demonstration that the path works, not a
rate. Say so on the slide.

---

## 3. Conflict arbitration — 120 trials

`artifacts/live_ambiguous_fusion_bayesian_gate_full/live_ambiguous_fusion_summary.json`

| | bal. acc. | miss | false halt | TP | TN | FN |
|---|---|---|---|---|---|---|
| Fixed threshold (`rule_first`) | **0.833** | **0.333** | **0.00** | 60 | 30 | 30 |
| Weighted gate (`bayesian`) | **1.00** | **0.00** | **0.00** | 90 | 30 | 0 |

Protocol, verified in the artifact:
- `trial_count`: **120**
- `profile_counts`: `delayed_wot_recovery` 30, `low_reliability_dom` 30,
  `partial_missing_wot` 30, `weak_stale_signal` 30 → **exactly 30 per profile**
- `oracle_source`: `ambiguous-fault-profile-label` (the label is the injected
  fault profile, not the detector's own output)
- `unique_seeds: true`, `unique_episode_ids: true`, `reset_evidence_complete: true`
- `balanced_accuracy_delta`: 0.166667

Held-out split: `artifacts/live_ambiguous_fusion_holdout/` — 80-trial shadow
comparison, `production_gate_changed: false` on that run.

✅ **Slide 12 is correct as printed.** Every number on it reproduces.

---

## 4. Understanding — 20 cases

`artifacts/model_value/model_value_report.json` → `intent.summary`

| Group | cases | rules correct | model correct |
|---|---|---|---|
| needs interpretation | 12 | **0/12** | **12/12** |
| rules already handle | 3 | **3/3** | **3/3** |
| out of scope | 5 | **5/5** | **5/5** |
| **total** | **20** | 8/20 | 20/20 |

Out-of-scope cases are refused by both (`model_source: "unsupported"`), which is
the correct behaviour, not a failure.

---

## 5. Looking — 24 trials

`artifacts/model_value/model_value_report.json` → `vision.summary`

| | value |
|---|---|
| trials | 24 |
| DOM wrong, caught | **12/12** (detection_rate 1.0) |
| DOM right, false alarm | **0/8** (false_alarm_rate 0.0) |
| declined (clipped view) | **4** |
| accuracy on answered trials | 1.00 |
| mean confidence, clear | 1.00 |
| mean confidence, ambiguous | 0.90 |
| transport errors | 0 |
| stability across repetitions | 1.00 on all five clear conditions |

12 + 8 + 4 = 24.

⚠️ **The older deck said `11/11`. The correct figure is `12/12`.** Fix this
wherever it appears in the report.

The four declined trials are the `clipped_view` condition, excluded from accuracy
and detection on purpose: a region cut off mid-word has no defensible right
answer, and grading against one would score the model against a self-invented
label.

---

## 6. Cross-environment transfer — 6 tasks, 2 environments

`artifacts/intent_cross_env/m1_cross_env.json`

| environment | tasks | solved | success | mean latency |
|---|---|---|---|---|
| forum.html | 2 | 2 | 100.0% | **2529 ms** |
| shopping.html | 4 | 4 | 100.0% | **2651 ms** |
| **overall** | **6** | **6** | **100.0%** | 2 envs |

Declined: 1 utterance not understood ("make me a sandwich"), 0 goals no
environment can satisfy. Reported separately, not counted either way.

⚠️ Latencies moved from the earlier 1995 / 2236 ms — these include model calls
and vary run to run. Task counts are stable.

---

## 7. Commanded vs measured — the asymmetry

`artifacts/commanded_vs_measured/report.json`

| check | reading | verdict | where an agent stops |
|---|---|---|---|
| transport | HTTP **204** | PASS | a browser agent stops here |
| commanded read-back (`position`) | reads **30**, asked 30 | PASS | a setpoint verifier stops here |
| measured read-back (`measuredPosition`) | reads **100**, asked 30 | **FAIL** | the only check that sees it |

Two of three checks report success on a room that never moved. Reproduces
exactly. ✅

---

## 8. Room prepared — 4/4 properties verified

`artifacts/room_prepared/room_prepared_report.json`

| part | thing.property | wanted | read back | verified |
|---|---|---|---|---|
| projector_on | projector.power | on | on | yes |
| lighting_set | lights.brightness | 30 | 30 | yes |
| blinds_set | blinds.position | 20 | 20 | yes |
| temperature_set | thermostat.targetTemperature | 21 | 21 | yes |

Goal: PREPARED, 4/4 confirmed by reading back.

---

## 9. WoT conformance

`python scripts/show_wot_conformance.py`

- **5 Thing Descriptions** discovered at run time from `http://localhost:8082/things`:
  thermostat, lights, projector, blinds, occupancy
- standard document: W3C WoT Thing Description 1.1
- address in the code: **none** — the href comes from the TD's `forms` array
- write permission: taken from `readOnly` in the TD
- write target: `PUT http://localhost:8080/thermostat/properties/targetTemperature`
- verification reads `currentTemperature` — **a different property than the one written**

---

## 10. Test tiers — the slide-8 claim

| tier | collected | result |
|---|---|---|
| fast (`pytest`) | **777** | 777 passed, 30 deselected, 5.64 s |
| live (`pytest -m live`) | **15** | 15 passed, 78.40 s |
| smartroom (`pytest -m smartroom`) | **15** | 15 passed, 14.59 s |
| **total** | **807** | all green |

The fast tier measured **775** before this session and **777** after: the two
tests added below now pin the skill-level backend oracle.

⚠️ **Slide 8 says `775 + 15 + 15`. It is now `777 + 15 + 15`.** Change it, or
drop the two new tests — but the count on the slide has to match what
`pytest --collect-only` prints when he runs it.

New tests in `tests/test_metrics_aggregator.py`:
- `test_skill_label_beats_episode_label_so_rollback_is_not_a_misroute` — pins
  that an episode-level label would score the restore dispatch as a mis-route
  (BRA 0.5) and the skill-level label does not (BRA 1.0).
- `test_unlabelled_skill_contributes_no_routing_case` — pins that an unlabelled
  record is scored neither right nor wrong.

---

## 11. Agent-loop campaign — 210 episodes

`artifacts/agent_loop_campaign_30x7/metric_ledger.json` (30 repetitions × 7 episodes)

| metric | working | value |
|---|---|---|
| goal reached | 120 / 210 | **0.5714** |
| failure detected | 180 / 210 | 0.8571 |
| recovery attempted | 90 / 180 | 0.50 |
| handed over | 90 / 180 | 0.50 |
| verify pass rate | 120 / 300 | 0.40 |
| measurement coverage | 1380 / 1380 | 1.00 |

Deterministic — reproduces identically. This is the campaign behind the technical
report's "goal reached 120 of 210, or 57.1 percent".

**Do not conflate this with TSR.** Handover is counted separately here, which is
the fix recorded in commit `cec3544`.

---

## 12. What changed, and what to edit

### Must change on slide 15
1. `Mean task latency 115 ms` → **`111 ms`** (or state the 108–115 warm range).
2. `Backend routing accuracy | n/a | 0` → **`1.00 | 12`**.
3. **Delete the whole "Why one row is n/a" block** — it no longer describes the table.
4. Consider adding `Conflict resolution rate 1.00 (n = 1)` and
   `Recovery tier accuracy 1.00 (n = 2)`.

### Must change on slide 14
The footnote still says "a metric whose denominator is zero prints n/a, never
0.0". Keep the rule — it is a good rule and still true — but it no longer
explains a row on slide 15.

### Must change on slide 8
`775 + 15 + 15` → **`777 + 15 + 15`**.

### Unchanged and verified
Slide 12 all six conflict numbers · slide 11 discovery and verification
narrative · slide 9 use-case table · TSR / SAR / recovery figures.

### For the technical report only
- `11/11` → **`12/12`** for DOM-wrong detection.
- Transfer latencies 1995 / 2236 ms → **2529 / 2651 ms**.
- The 210-episode campaign numbers are unchanged.

---

## Reproducing all of this

```bash
docker compose -f env/docker-compose.yml up -d
python scripts/demo.py doctor            # expect: everything is runnable
python scripts/demo.py run live-runtime  # TSR SAR RTR RSR RTA BRA CRR MTL
python scripts/demo.py run model-value   # understanding 20, looking 24
python scripts/demo.py run intent-cross-env      # transfer 6 tasks 2 envs
python scripts/demo.py run commanded-vs-measured # 204 / 30 / 100
python scripts/demo.py run room-prepared         # 4/4
python scripts/show_wot_conformance.py           # 5 TDs, no hard-coded address
pytest -q ; pytest -q -m live ; pytest -q -m smartroom
```
