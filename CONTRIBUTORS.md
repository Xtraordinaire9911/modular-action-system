# Contribution audit

Generated from this repository's own git history. Every number below is
reproducible with the command shown next to it — nothing here is asserted
without a way to check it.

Project span: **2026-05-14 → 2026-08-07**. Team of three plus an assistant bot.

## Commits by author

```bash
git shortlog -sne --all        # identities unified via .mailmap
```

| Author | Commits | Share of all | Share of human-authored |
|---|---:|---:|---:|
| **Ruiyao Jiang** | **137** | **56.1 %** | **58.8 %** |
| Yixin Yang | 67 | 27.5 % | 28.8 % |
| Fadi Ferjani | 29 | 11.9 % | 12.4 % |
| copilot-swe-agent[bot] | 11 | 4.5 % | — |
| **Total** | **244** | 100 % | 233 human |

Largest contributor by commit count, 2.04× the next contributor.

Two git identities were used for the same person during the project and are
unified in `.mailmap`, which changes reporting only and rewrites no history:

```
Ruiyao Jiang <ruiyao.jiang@tum.de> <ruiyao.jiang@alumni.polytechnique.org>
```

Without that mapping, 26 of the 137 commits are attributed to
`Xtraordinaire9911` and an audit that greps a single name undercounts them.

## Module ownership

Commit counts per directory. This is the more meaningful split: the three of us
owned different layers rather than sharing every file.

```bash
git log --all --author="ruiyao" --oneline -- src/perception | wc -l
```

| Module | Ruiyao | Yixin | Fadi | Ruiyao share |
|---|---:|---:|---:|---:|
| `src/vam` — visual action module, SoM payloads | 9 | 0 | 1 | **90 %** |
| `src/backend_router` — cost-aware backend routing | 12 | 1 | 1 | **86 %** |
| `src/perception` — DOM transducer, TD parser, SoM, browser session | 40 | 6 | 2 | **83 %** |
| `src/effectors` — DOM / WoT / visual executors | 16 | 4 | 5 | **64 %** |
| `scripts` — demo and benchmark runners | 12 | 7 | 1 | 60 % |
| `src/benchmarks` — external CUA benchmark adapters | 10 | 5 | 2 | 59 % |
| `src/adaptation` | 2 | 2 | 0 | 50 % |
| `src/recovery` — retry / reroute / escalation tiers | 2 | 6 | 5 | 15 % |
| `evaluation` — metric harnesses, fusion campaigns | 6 | 30 | 10 | 13 % |
| `src/runtime` — episode runner, state machine | 3 | 22 | 5 | 10 % |

Read plainly: **Ruiyao owned the perception-to-action path** (perception, VAM,
routing, effectors, benchmarks); **Yixin owned the runtime and evaluation
harnesses**; **Fadi contributed across recovery and effectors**.

## A note on line counts

Line-diff totals are not used as a contribution measure here, because they are
dominated by generated artifacts rather than authored code. Six generated JSON
reports account for roughly 33 000 added lines on their own:

```
6594  artifacts/live_ambiguous_fusion_rerun_holdout/..._report.json
6594  artifacts/live_ambiguous_fusion_holdout/..._report.json
5607  artifacts/live_ambiguous_fusion_bayesian_gate_full/..._summary.json
5591  artifacts/live_ambiguous_fusion_full/..._summary.json
5591  artifacts/live_ambiguous_fusion_rerun/..._summary.json
3362  artifacts/live_ambiguous_fusion_full/..._plan.json
```

For reference, `*.py` only: Ruiyao +9 473 / −911, Yixin +24 912 / −1 944,
Fadi +7 014 / −4 416. Commit count and module ownership are the figures quoted
above because they are the ones that survive this kind of distortion.
