# feat(demos): ground failure diagnosis in measurements, and show the working behind every metric

**Branch:** `feature/B-118-grounded-diagnosis` → `develop`
**Scope:** Member B (Ruiyao) — perception, recovery reasoning, demo evaluation
**Reference:** `current_codebase_full_analysis_en.md`, PDF §4 (recovery cascade), §5 D8/D10, §6 (metrics)

---

## Problem

Three separate gaps, all in the same place — what happens after a step fails.

**1. The diagnosis was not grounded in anything.** It compared a label and two
coordinates. That distinguishes "it moved" from "it is gone" and nothing else,
so three failures that look identical from outside collapsed into one answer:

| what actually happened | what the old diagnosis could see |
| --- | --- |
| a banner is covering the button | present, same place, click did nothing |
| the control refuses input | present, same place, click did nothing |
| the click was accepted and ignored | present, same place, click did nothing |

Each needs a different response. The cascade had four tiers and the demo could
only ever justify two of them.

**2. The faults were not realistic.** A button teleporting 150px, or being
deleted outright, does not happen on any real page. Recovering from them proved
little: the agent was solving a puzzle nobody has. Difficulty was also flat —
every fault was equally easy once you knew the trick.

**3. The metrics arrived with no working.** The campaign printed TSR, RTR, RSR,
RTA and DA at the end and nothing before it. A reader had to take the figures
on faith; there was no way to see which episodes contributed to which number,
or to recompute one by hand.

## Fix

### Probes: ask the page, do not infer

`src/demos/probes.py` puts four specific questions to the live page after a
failure:

| probe | question |
| --- | --- |
| `hit_test` | what element is really at the click point right now |
| `interactability` | is the target disabled, hidden, or zero-sized |
| `occlusion` | is something covering it, what is it, and where |
| `text_snapshot` | the visible text of the region, before against after |

Every probe returns **what it measured, not a verdict**, and a probe that cannot
run reports `ok=False` rather than a default that would read as a finding. The
reasoning that combines them lives in one place so it can be read and disagreed
with, and each conclusion carries a plain-language account of why it follows.

The demo shows measurement and conclusion as two separate steps, in that order,
so a viewer can see which is which.

### Two decisions the evidence forced

- **A target that moved only explains the failure if the click missed it.**
  When the hit test says the intended element received the click, the action was
  delivered; where the element sits now is a consequence of the page reacting,
  not a cause. Without this, an optimistic rollback was diagnosed as staleness
  and answered with a retry that could never work.
- **An action undone a moment later is the same conclusion as one that never
  took effect.** Both are "accepted, and the goal state did not follow".
  Splitting them by whether the region happened to change made a rollback
  undiagnosable.

### Faults drawn from production

`src/demos/realistic_faults.py` — each carries the reason it occurs, and the
difficulty is deliberately uneven:

| fault | real-world cause | difficulty | correct tier |
| --- | --- | --- | --- |
| Layout shift (CLS) | an image or ad above the target finishes loading without reserved space | easy | 1 retry |
| Consent banner obstruction | a privacy banner mounts asynchronously over the control | moderate | 2 clear then retry |
| Disabled by an unmet precondition | a required field the control depends on is empty | moderate | 3 satisfy then retry |
| Optimistic UI rollback | the interface confirms before the server agrees, then reverts | hard | 4 escalate |
| Session expiry | the token expired, so acting lands on a login wall | hard | 4 escalate |
| Silent device write | the device answers 204 and stores nothing (WoT) | hard | 4 escalate |

Two reuse the taxonomy already in `evaluation/open_web_mock_failure_suite.py`
(`overlay_modal_obstruction`, `session_auth_expiry`) so the vocabulary stays
shared with the rest of the evaluation code.

**None is on a timer.** A fault that clears itself after N seconds makes
recovery a matter of how fast the demo happened to be running, not of what the
agent worked out. The disabled-control fault is gated on a field being filled;
the agent reads what the control declares it depends on (`aria-controls`) and
satisfies it.

### Four tiers that actually differ

Recovery is no longer one retry wearing four labels:

- **tier 1** — look again and repeat
- **tier 2** — find what intercepted the click (from the occlusion probe's
  rectangle, never a named selector) and deal with it first
- **tier 3** — read what the control declares it depends on, satisfy it, and
  re-measure; refuse to act if it still will not accept input
- **tier 4** — deliberately do not act; hand over

### The working behind every metric

`src/demos/ledger.py` accumulates the raw tallies as the loop runs —
observations, elements seen and measured, candidates scored, actions,
verifications passed and failed, probes, diagnoses, recoveries, escalations.

Every reported metric states the division it performed next to its value:

```text
  goal reached           goals met 4 / episodes 7                     =  57.1%
  failure detected       failures detected 6 / episodes 7             =  85.7%
  recovery attempted     recoveries applied 3 / failures detected 6   =  50.0%
  handed over            escalations 3 / failures detected 6          =  50.0%
```

The rows are named for what they literally count, deliberately **not** TSR/RSR:
those names belong to the campaign, which applies the project's scoring rules on
top. Two quantities under one name read as a contradiction, and that is exactly
what happened before this was separated.

The panel carries the running strip at **every** step, faulted or not, as the
quietest line in the layout:

```text
obs 7 | seen 46 | meas 46 | cand 46 | act 7 | ver 4/10 | probe 21 | diag 6 | rec 3 | esc 3
```

These are working numbers. They should be checkable at any moment without
competing with the step being narrated.

## A real bug the probes found

The probes reported a healthy control while the real one was dead. The cause:
`button.add-cart-btn` matches **four** buttons on the shop page, so anything
querying that selector silently measured the first match.

A selector shared by four elements is not a locator. `DomTransducer` now
narrows colliding selectors with the attributes that actually tell the elements
apart, preferring `data-*` hooks and then the accessible name:

```text
before:  button.add-cart-btn                          x4
after:   button.add-cart-btn[data-id='headphones']
         button.add-cart-btn[data-id='laptop']
         button.add-cart-btn[data-id='keyboard']
         button.add-cart-btn[data-id='monitor']
```

A selector that cannot be narrowed keeps its shared form but drops to
positional confidence, so the rest of the system can tell it is weak rather
than being quietly misled.

`VisualMark` now also carries the selector of the affordance it came from, so a
probe can question the element itself instead of only its rectangle. Acting
still goes through the bounding box — the Set-of-Marks path is unchanged.

## Two fixes the run surfaced

- **Verification waits for the page to settle** before reading. An optimistic
  interface confirms before the server agrees, so reading immediately records a
  state that may not survive.
- **A missing region is a failure to report, not an error to raise.** A session
  expiry tears the page down; the old code raised a Playwright timeout and
  aborted the run.
- The per-fault table no longer scores a clean run as 0% diagnosis accuracy when
  there was nothing to diagnose.

## Result

Seven scenes across three surfaces (shop, forum, WoT device), six with a
different fault, ordered easy to hard:

```text
  TSR  task success rate             57.1%   (goal reached; a handover is not a success)
  RTR  recovery trigger rate         85.7%
  RSR  recovery success rate         50.0%
  RTA  recovery tier accuracy       100.0%
  DA   diagnosis accuracy           100.0%
       handled correctly            100.0%   (goal reached, or refused correctly)
       escalations                  3

  fault                  eps  handled      DA     RTA  tiers
  --------------------------------------------------------------
  consent_overlay          1        1   100%   100%  2
  disabled_until_valid     1        1   100%   100%  3
  layout_shift             1        1   100%   100%  1
  none                     1        1      -      -  -
  optimistic_rollback      1        1   100%   100%  4
  session_expiry           1        1   100%   100%  4
  silent_write             1        1   100%   100%  4
```

TSR counts goals actually reached and nothing else: three of the seven faults
are unrecoverable by design, and handing those over correctly is right
behaviour but not a solved task. That is reported separately as "handled
correctly". An earlier version folded the two together and published 100% as
TSR, which contradicted both this module's own docstring and the ledger
printed directly above it.

**These are n=1 per fault.** RTA and DA at 100% mean one correct answer per
condition, against a project requirement of thirty. Run with `--repeat` before
quoting them anywhere.

All four tiers are exercised, and which tier each episode used is decided at run
time from what the agent measured. The expected cause and tier live in the scene
definition, which the diagnosis never sees — scoring an answer against something
it could not read is what makes the accuracy figures mean anything.

Stable across `--pace 0.05`, `1.0` and `1.2`, and across `--repeat 2`.

## How to run it

```bash
python scripts/run_agent_loop_demo.py                    # the narrated run
python scripts/run_agent_loop_demo.py --headless --pace 0.05 --hold 0   # fast check
python scripts/run_agent_loop_demo.py --repeat 5         # campaign metrics
```

Artifacts land in `eval_outputs/agent_loop/<timestamp>/`: screenshots per scene,
`trajectory.json`, `campaign.json`, `metric_ledger.json`, `compiled_experience.json`.

## Tests

35 new tests across four files, all of which care most about the failure paths:

- `tests/test_probes_and_faults.py` — a probe that cannot run says so rather
  than guessing; the fault catalogue expects varied causes and tiers, and every
  fault states a real cause rather than a label.
- `tests/test_diagnosis_probes.py` — the five situations produce five distinct
  conclusions; occlusion is checked before anything else; a control that moved
  *after* receiving the click is not a retry; no probe running means no
  conclusion.
- `tests/test_ledger.py` — every metric states the division it performed, and
  the stated working matches the value; an empty run reports zero rather than
  dividing by zero.
- `tests/test_dom_transducer.py` — colliding selectors are narrowed to one, and
  one that cannot be narrowed says so in its confidence.

Full suite: **499 passed**. `ruff`, `black` and `mypy` clean on every file this
branch touches.

## Files

| file | change |
| --- | --- |
| `src/demos/probes.py` | new — four grounded probes |
| `src/demos/realistic_faults.py` | new — five production faults with their causes |
| `src/demos/ledger.py` | new — metric intermediates and their arithmetic |
| `src/demos/diagnosis.py` | `diagnose_with_probes`, occlusion cause, plain-language accounts |
| `src/demos/campaign.py` | per-fault table does not score what cannot be scored |
| `src/demos/pip_console.py` | the quiet tally line |
| `src/perception/dom_transducer.py` | selector uniqueness |
| `src/perception/som_parser.py` | marks carry their affordance's selector |
| `scripts/run_agent_loop_demo.py` | wired to all of the above; verify hardened |

Nothing existing was removed. The previous coordinate-based `diagnose()` is
retained and still tested — the probe-based path is additive.
