# Agent-loop campaign: 30 repetitions x 7 scenes

Produced by:

```bash
python scripts/run_agent_loop_demo.py --headless --pace 0 --trace-delay 0 --hold 0 --repeat 30
```

210 episodes, **30 per condition**, which is the sample size
`current_codebase_full_analysis_en.md` §4 P2 requires (">= 30 independent
episodes per condition"). Every episode ran against a live Chromium page; no
result here is replayed or simulated.

## What the run reports

```text
  obs 210 | seen 1380 | meas 1380 | cand 1380 | act 210 | ver 120/300
        | probe 630 | diag 180 | rec 90 | esc 90

  goal reached           goals met 120 / episodes 210                  =  57.1%
  failure detected       failures detected 180 / episodes 210          =  85.7%
  recovery attempted     recoveries applied 90 / failures detected 180 =  50.0%
  handed over            escalations 90 / failures detected 180        =  50.0%
  verify pass rate       passed 120 / verifications 300                =  40.0%
  measurement coverage   measured 1380 / seen 1380                     = 100.0%

  TSR  task success rate             57.1%   (goal reached; a handover is not a success)
  RTR  recovery trigger rate         85.7%
  RSR  recovery success rate         50.0%
  RTA  recovery tier accuracy       100.0%
  DA   diagnosis accuracy           100.0%
       handled correctly            100.0%
       escalations                  90

  fault                  eps  handled      DA     RTA  tiers
  consent_overlay         30       30   100%   100%  2
  disabled_until_valid    30       30   100%   100%  3
  layout_shift            30       30   100%   100%  1
  none                    30       30      -      -  -
  optimistic_rollback     30       30   100%   100%  4
  session_expiry          30       30   100%   100%  4
  silent_write            30       30   100%   100%  4
```

## How to read RTA and DA at 100%

**What 30 repetitions establish here is reproducibility, not variance.** The
injected faults are deterministic and, with no model configured, so is the
diagnosis, so the same measurements are taken and the same conclusion reached
every time. Thirty identical correct answers rule out a lucky single run; they
do not estimate a distribution, because there is nothing here that varies.

Reading 100% as "the diagnosis is 100% accurate in general" would be wrong. It
is 100% accurate **on these six fault classes, in these three environments,
under deterministic conditions**. Establishing a distribution needs randomised
fixtures — varying where the banner mounts, how long validation takes, which
element shifts — and that is not what this run does.

TSR is 57.1% and not higher because three of the six faults are unrecoverable by
construction: an optimistic rollback, an expired session and a silently ignored
device write cannot be fixed by the agent, and the correct behaviour is to
refuse and hand over. That is counted under "handled correctly" (100%) and
deliberately not under TSR.
