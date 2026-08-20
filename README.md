# Modular Action System Architecture — personal archive

Mirror of a three-person TUM Praktikum project (*Automatic Agents*, summer 2026),
kept under my own account so the work stays inspectable after the course
repository's access changes. Full git history is preserved: all branches, all
authors, original timestamps.

**Upstream:** `Garrulus21yyx/A-Modular-Action-System-Architecture` (private)
**Mirrored:** 2026-08-08 · 244 commits · 15 branches · 6 rescued PR tags

This is a mirror, not a fork of convenience: it was created with
`git clone --mirror`, so commit hashes, authorship and dates are identical to
upstream and can be compared against it directly.

### What the mirror includes, precisely

All branches and tags were pushed unchanged. In addition, six `refs/pull/*/head`
refs held 25 commits that were **not reachable from any branch** — feature
branches deleted upstream after their pull request merged. Those are preserved
as tags `archive/pr-{33,34,35,36,53,54}-head`, so no authored commit is lost.

Not preserved: GitHub's synthetic `refs/pull/*/merge` refs. Those are generated
server-side to preview a merge for an open pull request, contain no authored
work, and in every case both parent commits are present here. GitHub also
refuses pushes to that namespace.

The project's own documentation is preserved unchanged as
[`README.upstream.md`](README.upstream.md) — the only file this archive renames.
Everything else, including full git history, is byte-identical to upstream.

---

## What the system does

An action system for autonomous agents that generalises across environments
without hard-coded UI or device assumptions. Three interaction surfaces are
reduced to one affordance contract:

- **DOM** — a live page is transduced into a Page Affordance Model: stable CSS
  locators, labels, action types, state.
- **WoT** — W3C Thing Descriptions are parsed at runtime into executable
  affordances, including forms, security schemes and rate limits.
- **Visual** — screenshots are represented as Set-of-Marks targets, so the visual
  model selects a `mark_id` rather than raw pixel coordinates.

A cost-aware router arbitrates between backends; a recovery cascade handles
retry, reroute, rollback and human escalation; pre/postcondition checks and
failure injection provide the evaluation surface.

## My part

I was the largest contributor by commit count (**137 of 244**, 56 %) and owned
the perception-to-action path. See [`CONTRIBUTORS.md`](CONTRIBUTORS.md) for the
per-module audit and the exact commands to reproduce every figure.

Work I am the primary author of:

| Area | What I built |
|---|---|
| **DOM transduction** | `src/perception/dom_transducer.py` — HTML to affordance model with a confidence-ranked locator strategy (id → testid → name → class → positional). Later hardened so demo overlay markers cannot leak into derived locators. |
| **WoT runtime parsing** | `src/perception/td_affordance_parser.py` — Thing Descriptions parsed at runtime rather than compiled in, including HATEOAS forms, `securityDefinitions` and rate limits. |
| **Browser session isolation** | `src/perception/browser_session.py` — one isolated Playwright context per episode; snapshot/restore of cookies and storage so an episode cannot inherit the previous one's state. |
| **Visual grounding** | `src/perception/visual_geometry.py`, `src/perception/som_parser.py`, `src/vam/` — bounding boxes measured from the live browser instead of fixture attributes, with explicit refusal to emit a mark for anything unmeasurable. |
| **Backend routing** | `src/backend_router/` — cost, reliability and latency aware selection with confidence tracking. |
| **External benchmarks** | `src/benchmarks/`, `env/mock_envs/` — MiniWoB++ adapters plus three self-contained WebArena-style environments (shopping, email, forum), and a cross-environment demo runner reporting a per-environment success metric. |
| **Reproducibility** | `scripts/bootstrap.py` — one standard-library-only entry point taking a clean clone to installed, tested and demoed; CI hardened so the container image is validated on pull requests. |

Two engineering decisions I would highlight in a conversation:

1. **DOM-first, vision-as-fallback.** The default path is deterministic DOM
   transduction — no model inference per step, and the locator is simultaneously
   the perception output and the action input, so there is no coordinate
   translation. The Set-of-Marks / visual path is a System-2 fallback for
   surfaces where DOM affordances are absent or ambiguous. This is the opposite
   priority to screenshot-first agents such as OmniParser, and the reasoning is
   about latency and grounding error rather than novelty.

2. **Refusing to fabricate evidence.** Several components report what they could
   *not* establish: unmeasurable elements yield no visual mark and any
   previously attached box is discarded; a partial device-state rollback cannot
   report itself as complete; an unfinished human handover is excluded from the
   correction-rate metric rather than counted as "no correction needed". A
   metric that quietly rounds up is worse than a missing one.

## Verifying this archive

```bash
git clone <this-repo> && cd <this-repo>

git shortlog -sne --all            # identities unified via .mailmap
git log --author="Ruiyao Jiang" --oneline | wc -l
git log --all --author="ruiyao" --oneline -- src/perception | wc -l
```

Running the project itself, from a clean clone, needs only Python 3.11+:

```bash
python scripts/bootstrap.py --demo --headed
```

That installs dependencies, downloads the one browser the demos need, runs the
test suite, and then runs the visual demo.

## Scope and honesty notes

- This is coursework, not a product. There are no users and no stars; it was
  built to a supervisor's architecture requirements over one semester.
- Evidence in the repository is confined to local mock environments, MiniWoB++
  and controlled fixtures. Real open-web validation was explicitly out of scope
  and is described as such in the project's own analysis document.
- Teammates' work is present in this mirror and remains theirs;
  `CONTRIBUTORS.md` states who owned which layer. Nothing here is claimed on
  their behalf.
