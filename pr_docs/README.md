# Pull request drafts

One file per open branch. Paste the body into the GitHub PR description.

All five branches are cut from `develop`, touch disjoint files, and were verified
to merge cleanly both into `develop` and with each other (10 of 10 pairs clean).
Merging them in any order works.

| # | Branch | Title | Files |
|---|---|---|---|
| 1 | `feature/B-113-visual-som-real-geometry` | Derive visual marks from measured browser geometry | 4 |
| 2 | `feature/B-114-episode-isolation` | Real episode isolation and an observable tier-4 handover | 4 |
| 3 | `feature/B-115-clean-clone-setup` | One documented path from a clean clone to a running demo | 3 |
| 4 | `feature/B-116-wot-episode-isolation` | Snapshot and restore WoT device state between episodes | 3 |
| 5 | `feature/B-117-demo-registry` | Demo registry so every demo is discoverable and self-checking | 5 |

Integration check with all five merged together: **383 tests pass**.

> Note for every PR: `ruff check .`, `black --check .` and `mypy src/` currently
> fail on `develop` itself, from unrelated files under `evaluation/` (the fusion
> and open-web modules). None of those files are touched by these branches, and
> every file each branch changes passes all three checks individually. CI will
> show red until `develop`'s own lint gate is restored.
