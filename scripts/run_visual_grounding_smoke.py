"""One genuine image-in -> mark-out -> mark-to-click trace (Member B).

Every step below is observed, not authored:

  1. image in    a real PNG screenshot of a real rendered page
  2. geometry    getBoundingClientRect() measured in the live browser
  3. marks       Set-of-Marks entries built only from measured boxes
  4. selection   an honest label heuristic (select_mark) picks one mark_id
                 -- this is a documented heuristic, not a VLM
  5. click       the click uses the selected mark's centre coordinates, so the
                 action is driven by the visual mark rather than a CSS selector
  6. evidence    annotated screenshot plus a JSON trace of the whole chain

Usage (PowerShell):
  uv run python scripts/run_visual_grounding_smoke.py
  uv run python scripts/run_visual_grounding_smoke.py --headed --page shopping.html
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_agent_on_env import _start_static_server  # noqa: E402
from src.perception.som_parser import (  # noqa: E402
    annotate_screenshot,
    marks_from_affordances,
    select_mark,
)
from src.perception.visual_geometry import attach_measured_bboxes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Genuine visual-grounding smoke trace.")
    parser.add_argument("--page", default="shopping.html", help="File under env/mock_envs/.")
    # Matches the aria-label the page actually exposes ("Add Wireless Headphones
    # to cart"); select_mark does a substring match, so the hint must be a real
    # fragment of a real label rather than an invented button name.
    parser.add_argument("--label-hint", default="Headphones", help="Label fragment the heuristic looks for.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    parser.add_argument("--out", default="", help="Output directory (default: timestamped).")
    args = parser.parse_args()

    from src.perception.browser_session import BrowserSession  # lazy: needs Playwright

    out = Path(args.out or f"eval_outputs/visual_grounding/{datetime.now():%Y%m%d_%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)

    mock_dir = Path(__file__).resolve().parents[1] / "env" / "mock_envs"
    httpd, port = _start_static_server(str(mock_dir))
    url = f"http://127.0.0.1:{port}/{args.page}"

    session = BrowserSession.launch(url, headless=not args.headed)
    try:
        time.sleep(0.5)  # let the page finish rendering before anything is measured

        # 1. image in -- a real screenshot of a real page
        raw_png = session.screenshot(str(out / "01_input.png"))

        # 2-3. real geometry -> marks. Nothing here can come from a fixture: the
        # mock pages carry no data-bbox attributes at all.
        pam = session.state(page_id="visual_smoke")
        measured = attach_measured_bboxes(pam, session)
        marks = marks_from_affordances(pam.affordances)

        # 4. mark out -- honest label heuristic, stated as such
        selection = select_mark(marks, args.label_hint)

        # 5. mark to click -- the coordinates come from the selected mark
        clicked = False
        if selection is not None:
            session.click_xy(*selection.center)
            time.sleep(0.4)
            clicked = True

        # 6. evidence
        (out / "02_marks.png").write_bytes(annotate_screenshot(raw_png, marks))
        session.screenshot(str(out / "03_after_click.png"))

        body = (session.text_content("body") or "").lower()
        trace = {
            "url": url,
            "affordances": len(pam.affordances),
            "measured_boxes": measured,
            "marks": [
                {"mark_id": m.mark_id, "label": m.label, "bbox": m.bbox.as_xywh(), "center": list(m.bbox.center)}
                for m in marks
            ],
            "selection": selection.to_dict() if selection else None,
            "selector_strategy": "label_heuristic",  # not a VLM; named so nobody infers one
            "clicked_via": "mark_center" if clicked else None,
            "effect_observed": "added" in body or "cart" in body,
            "bbox_provenance": "measured_in_browser",
        }
        (out / "trace.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")

        print(f"affordances={len(pam.affordances)} measured={measured} marks={len(marks)}")
        print(f"selected={selection.mark_id if selection else None} clicked={clicked}")
        print(f"artifacts -> {out}")
        # The chain is only meaningful if real geometry produced a clickable mark.
        return 0 if (measured > 0 and marks and clicked) else 1
    finally:
        session.close()
        httpd.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
