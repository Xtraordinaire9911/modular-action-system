"""One pointer, one ring, one fading trail, shared by every browser demo.

The cursor and the ring started out inline in ``run_agent_loop_demo``, where they
were the only place a viewer could see *where* the agent was acting. The
smart-room demos needed the same affordance, and a second copy of a twenty-line
JavaScript string is how two demos drift into looking like two unrelated
systems. So it lives here and both import it.

Three deliberate choices, because this is presentation code and presentation
code is where overclaiming happens:

* **The trail is new.** A cursor that teleports reads as a screenshot; a cursor
  that leaves a path reads as a movement. The audience is being asked to believe
  the agent acted, so the motion is worth the twelve lines.
* **The label names the property, not the widget.** ``thermostat.targetTemperature
  <- 22`` is the content of this project; "clicked the button" is not. The label
  is the one place the overlay carries information rather than decoration.
* **Everything is ``pointer-events: none`` and prefixed ``__cua_``.** The overlay
  must never be a thing the agent can perceive or hit. Any element it adds would
  otherwise show up in the DOM transducer's affordance list, and a demo that
  changes what the agent sees is not a demo of the agent.

Nothing here is required for the system to work. If the JS fails, the helpers
return ``False`` and the caller carries on; a missing cursor must never be able
to fail a run.
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = [
    "POINT_JS",
    "CLEAR_JS",
    "AGENT_COLOR",
    "OK_COLOR",
    "FAIL_COLOR",
    "point_at_selector",
    "point_at_box",
    "clear_pointer",
]

# Periwinkle, matching the cursor run_agent_loop_demo has always drawn. Kept
# deliberately off the dashboard's own palette (TUM blue / slate) so the agent's
# pointer never reads as part of the interface it is operating.
AGENT_COLOR = "#8383ff"
OK_COLOR = "#22c55e"
FAIL_COLOR = "#ef4444"

_TRAIL_MS = 700
_TRAIL_MAX = 14

# One expression handles both call styles: pass `selector` and the page resolves
# the geometry itself, or pass explicit coordinates when the caller already has
# them from the transducer. Doing the lookup in the page saves a round trip and
# keeps the "element vanished" case as a plain False instead of an exception.
POINT_JS = f"""
(a) => {{
  let x = a.x, y = a.y, bx = a.bx, by = a.by, bw = a.bw, bh = a.bh;
  if (a.selector) {{
    const el = document.querySelector(a.selector);
    if (!el) return false;
    const r = el.getBoundingClientRect();
    bx = r.left; by = r.top; bw = r.width; bh = r.height;
    x = r.left + r.width / 2;
    y = r.top + r.height / 2;
  }}
  if (x == null || y == null) return false;

  // ── cursor ─────────────────────────────────────────────────────────────
  // Created before the trail because the trail is drawn along the path *from*
  // where the cursor currently is, so it needs the previous position.
  let c = document.getElementById('__cua_cur');
  if (!c) {{
    c = document.createElement('div');
    c.id = '__cua_cur';
    c.style.cssText = 'position:fixed;width:26px;height:26px;z-index:2147483645;' +
      'pointer-events:none;transition:left .55s cubic-bezier(.4,0,.2,1),' +
      'top .55s cubic-bezier(.4,0,.2,1);' +
      'background:conic-gradient(from 135deg at 30% 30%,{AGENT_COLOR} 0 25%,transparent 0);' +
      'clip-path:polygon(0 0,0 78%,26% 58%,44% 96%,62% 86%,44% 50%,78% 46%);' +
      'filter:drop-shadow(0 0 7px rgba(131,131,255,.95))';
    document.body.appendChild(c);
  }}
  const fromX = c.dataset.x === undefined ? x : parseFloat(c.dataset.x);
  const fromY = c.dataset.y === undefined ? y : parseFloat(c.dataset.y);
  c.style.left = (x - 3) + 'px';
  c.style.top = (y - 3) + 'px';
  c.dataset.x = x;
  c.dataset.y = y;

  // ── trail ──────────────────────────────────────────────────────────────
  // Dots interpolated along the path the cursor is about to travel, revealed on
  // a stagger that matches the cursor's own transition. Dropping one dot per
  // call - at the destination only - looked like a cursor teleporting and
  // leaving a full stop behind it, which is not a trail; the audience is being
  // asked to believe the agent moved.
  let t = document.getElementById('__cua_trail');
  if (!t) {{
    t = document.createElement('div');
    t.id = '__cua_trail';
    t.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;' +
      'z-index:2147483643;pointer-events:none';
    document.body.appendChild(t);
  }}
  const span = Math.hypot(x - fromX, y - fromY);
  if (span > 12) {{
    // One dot per ~26px, capped: a long diagonal should not put fifty nodes on
    // a page the agent is perceiving.
    const steps = Math.min({_TRAIL_MAX}, Math.max(3, Math.round(span / 26)));
    // Per-call cap is not a bound: dots also expire on their own timers, so
    // calls arriving faster than they expire would still accumulate. Evict the
    // oldest so the live node count has a hard ceiling regardless of pacing.
    while (t.childElementCount + steps > {_TRAIL_MAX} * 2) t.removeChild(t.firstChild);
    for (let i = 1; i <= steps; i++) {{
      const p = i / (steps + 1);
      // Same easing as the cursor's transition, so the dots are where the
      // cursor actually is rather than evenly spread behind it.
      const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      const dx = fromX + (x - fromX) * e;
      const dy = fromY + (y - fromY) * e;
      const dot = document.createElement('div');
      // No transition on the way in, and the fade-out is armed by a *later*
      // timer rather than a requestAnimationFrame. Setting both in one frame
      // cancels the appearance before it paints: the dots exist, are counted,
      // and are never visible - which is exactly how the first version of this
      // shipped a trail that was not there.
      // 10px with a soft glow, and it holds for 260ms before fading. Sized for
      // a projector rather than for this monitor: at 9px and opacity .5 the
      // path was measurable in the DOM and effectively invisible in the room,
      // which is the same as not having it.
      dot.style.cssText = 'position:fixed;width:10px;height:10px;border-radius:50%;' +
        'pointer-events:none;background:' + (a.color || '{AGENT_COLOR}') + ';' +
        'box-shadow:0 0 8px ' + (a.color || '{AGENT_COLOR}') + ';' +
        'left:' + (dx - 5) + 'px;top:' + (dy - 5) + 'px;opacity:0';
      t.appendChild(dot);
      const born = 550 * p * 0.7;
      setTimeout(() => {{
        dot.style.opacity = '0.6';
      }}, born);
      setTimeout(() => {{
        dot.style.transition = 'opacity {_TRAIL_MS}ms linear,transform {_TRAIL_MS}ms ease-out';
        dot.style.opacity = '0';
        dot.style.transform = 'scale(.3)';
      }}, born + 260);
      setTimeout(() => dot.remove(), born + {_TRAIL_MS} + 350);
    }}
  }}

  // ── ring around the target ─────────────────────────────────────────────
  let r2 = document.getElementById('__cua_ring');
  if (!r2) {{
    r2 = document.createElement('div');
    r2.id = '__cua_ring';
    r2.style.cssText = 'position:fixed;z-index:2147483644;pointer-events:none;' +
      'border:3px solid {AGENT_COLOR};border-radius:10px;transition:all .5s;' +
      'box-shadow:0 0 22px rgba(131,131,255,.85),inset 0 0 22px rgba(131,131,255,.3)';
    document.body.appendChild(r2);
  }}
  if (bx != null && bw != null) {{
    r2.style.left = bx + 'px';
    r2.style.top = by + 'px';
    r2.style.width = bw + 'px';
    r2.style.height = bh + 'px';
    r2.style.opacity = '1';
  }} else {{
    r2.style.opacity = '0';
  }}
  r2.style.borderColor = a.color || '{AGENT_COLOR}';

  // ── label: the property being written, not the widget being touched ────
  let l = document.getElementById('__cua_label');
  if (a.label) {{
    if (!l) {{
      l = document.createElement('div');
      l.id = '__cua_label';
      l.style.cssText = 'position:fixed;z-index:2147483646;pointer-events:none;' +
        'font:600 12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;' +
        'padding:5px 9px;border-radius:7px;color:#fff;white-space:nowrap;' +
        'background:rgba(17,20,32,.93);box-shadow:0 6px 18px rgba(0,0,0,.28);' +
        'transition:left .55s cubic-bezier(.4,0,.2,1),top .55s cubic-bezier(.4,0,.2,1)';
      document.body.appendChild(l);
    }}
    l.textContent = a.label;
    l.style.borderLeft = '3px solid ' + (a.color || '{AGENT_COLOR}');
    // Flip above the target when pointing near the bottom of the viewport, so
    // the label never lands off-screen in a recording.
    const below = y + 34;
    l.style.left = Math.max(8, Math.min(x + 18, window.innerWidth - 320)) + 'px';
    l.style.top = (below > window.innerHeight - 40 ? y - 38 : below) + 'px';
    l.style.opacity = '1';
  }} else if (l) {{
    l.style.opacity = '0';
  }}
  return true;
}}
"""

CLEAR_JS = """
() => {
  for (const i of ['__cua_cur', '__cua_ring', '__cua_label', '__cua_trail']) {
    const e = document.getElementById(i);
    if (e) e.remove();
  }
  return true;
}
"""


class _Evaluator(Protocol):
    """The one method these helpers need, so any session type satisfies it."""

    def evaluate(self, expression: str, arg: Any | None = None) -> Any: ...


def _safe(session: _Evaluator, expression: str, arg: Any | None = None) -> bool:
    """Never let decoration fail a run.

    A demo whose overlay raises is worse than a demo with no overlay: the run
    dies for a reason that has nothing to do with what is being shown.
    """
    try:
        return bool(session.evaluate(expression, arg))
    except Exception:  # noqa: BLE001 - cosmetic layer, deliberately swallowed
        return False


def point_at_selector(
    session: _Evaluator,
    selector: str,
    *,
    label: str = "",
    color: str = AGENT_COLOR,
) -> bool:
    """Move the pointer onto whatever ``selector`` matches.

    Returns ``False`` when nothing matches, which is the interesting case: the
    element the demo meant to highlight is not on the page, and the caller may
    want to say so rather than silently pointing at nothing.
    """
    return _safe(session, POINT_JS, {"selector": selector, "label": label, "color": color})


def point_at_box(
    session: _Evaluator,
    *,
    x: float,
    y: float,
    box: tuple[float, float, float, float] | None = None,
    label: str = "",
    color: str = AGENT_COLOR,
) -> bool:
    """Point at coordinates the caller already resolved.

    ``box`` is ``(left, top, width, height)``. Used where geometry comes from the
    DOM transducer rather than from a selector, so the overlay marks exactly the
    rectangle the agent scored rather than one re-derived here.
    """
    arg: dict[str, Any] = {"x": x, "y": y, "label": label, "color": color}
    if box is not None:
        arg["bx"], arg["by"], arg["bw"], arg["bh"] = box
    return _safe(session, POINT_JS, arg)


def clear_pointer(session: _Evaluator) -> bool:
    """Remove every element this module added.

    Called before screenshots that feed the vision model: the cursor is not part
    of the page, and a model asked "is the confirmation visible" must not be
    answering about our own overlay.
    """
    return _safe(session, CLEAR_JS)
