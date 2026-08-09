# feat(perception): derive visual marks from measured browser geometry

**Branch:** `feature/B-113-visual-som-real-geometry` → `develop`
**Scope:** Member B (Ruiyao) — real visual path / no fabricated marks
**Reference:** `current_codebase_full_analysis_en.md`, PDF §1 (VAM/SoM), §4.3, §7.6

---

## Problem

The Set-of-Marks path had **no genuine geometry to stand on**.

The only source of `locator["bbox"]` was a `data-bbox` HTML attribute:

```python
def _bbox_from_attrs(attr: dict[str, str]) -> list[int] | None:
    raw = attr.get("data-bbox")
```

That attribute appears in exactly one place in the repository:

| Location | Has `data-bbox`? |
|---|---|
| `tests/test_dom_transducer.py` | yes — fixture only |
| `env/react_dashboard/` (the real dashboard) | **no** |
| `env/mock_envs/*.html` | **no** |

So on any real page every affordance arrived without a box, and
`marks_from_affordances` skipped all of them:

```python
bbox_data = affordance.locator.get("bbox")
if bbox_data is None:
    continue          # -> zero marks on every real page
```

The visual path produced nothing, and the only boxes that ever existed were
hand-written strings in a test fixture.

## Fix

New `src/perception/visual_geometry.py` asks the **live page** for
`getBoundingClientRect()` across all affordance selectors in a single
`evaluate` round trip, and attaches the result as `locator["bbox"]` together
with `locator["bbox_source"]`.

The honesty properties are the point of the change:

| Situation | Behaviour |
|---|---|
| Element absent, detached, or zero-sized | no box, **and any previously attached box is removed** |
| Selector invalid | caught per element; cannot abort the pass or fall back to a guess |
| Probe fails entirely | every entry becomes `None` — measures nothing, invents nothing |
| Coordinate frame | viewport-relative CSS pixels, the same frame as a default screenshot |

## Second defect, found by running it

The first version was measured against `env/mock_envs/shopping.html`: seven
affordances, but only **four distinct rectangles**.

The four "Add to cart" buttons all derive the same class selector
`button.add-cart-btn`, and `querySelector` returns the first match every time —
so three marks described the *first* button's geometry. That is a fabricated
mark by aliasing, exactly what this layer exists to prevent.

Measurement now sends `[selector, occurrence]` pairs and indexes into
`querySelectorAll`, binding the nth affordance carrying a selector to the nth
element that selector matches. Affordances are produced in document order and
`querySelectorAll` returns document order, so the two line up.

| | Distinct mark geometry |
|---|---|
| before | 4 of 7 (`M002`–`M005` all `[84, 310, 378, 33]`) |
| after | **7 of 7**, matching the rendered 2×2 product grid (x=84/518, y=310/600) |

## Acceptance criterion — one genuine image-in / mark-out / mark-to-click trace

`scripts/run_visual_grounding_smoke.py` runs the whole chain and writes evidence:

1. **image in** — a real PNG screenshot of a real rendered page
2. **geometry** — `getBoundingClientRect()` measured in the live browser
3. **marks** — Set-of-Marks entries built *only* from measured boxes
4. **selection** — `select_mark` label heuristic picks one `mark_id`; recorded as
   `"selector_strategy": "label_heuristic"` so nobody can infer a VLM that is not there
5. **click** — driven by the selected mark's centre, not by a CSS selector
6. **evidence** — annotated screenshot plus a JSON trace

Latest run:

```
affordances=7  measured=7  marks=7
selected=M002  clicked=True
effect_observed=true  bbox_provenance=measured_in_browser
```

The script exits non-zero unless real geometry produced a clickable mark, so it
fails loudly instead of reporting an empty success.

## Tests

`tests/test_visual_geometry.py` — 11 cases:

| Area | Asserts |
|---|---|
| measurement | all selectors measured in a **single** round trip |
| measurement | zero-area and malformed rects are not boxes |
| measurement | **repeated selectors resolve to distinct elements** (the aliasing regression) |
| measurement | an occurrence beyond the match count is not measured |
| measurement | probe failure measures nothing and invents nothing |
| attachment | measured boxes are attached and tagged with their source |
| attachment | **a fixture-authored box is discarded when the element cannot be measured** |
| attachment | an authored box is replaced by the measured one |
| end-to-end | marks are built only from measured elements |
| end-to-end | a page with **no `data-bbox` anywhere** still yields marks |

## Verification

| Check | Result |
|---|---|
| `ruff` on changed files | All checks passed |
| `black --check` on changed files | 3 files unchanged |
| `mypy` on `visual_geometry.py` | no issues |
| `pytest --tb=short -q` (full suite) | **327 passed** |
| smoke script | exit 0, trace written |

> Note: `ruff check .` / `black --check .` / `mypy src/` currently fail on
> `develop` itself due to unrelated files under `evaluation/` (fusion / open-web
> modules). Those are untouched here; every file changed by this branch passes
> all three checks individually.

## Compatibility

- Additive: one new module, one new script, one new test file.
- No existing signature changed; `data-bbox` parsing is left in place, it is
  simply superseded by measured geometry when a session is available.
- `.gitignore` gains `eval_outputs/` (per-run screenshots and traces).
- Merges cleanly into `develop`; no file overlap with `B-111` or `B-112`.
