"""A panel that shows the model working, side by side with the rules.

The narration console explains what the agent is doing. That is the wrong
surface for the question this panel exists to answer, which is: *is there a
model in here at all, and what did it change?* Narration cannot answer it — a
line of text saying "sent to a language model" is a claim a viewer has to take
on trust, and it looks identical whether a model ran or not.

So this panel shows the evidence instead of describing it:

- the **request** actually sent, including the sentence and the system prompt's
  size, and the **raw reply**, typed out as it is revealed rather than pasted, so
  it reads as something that arrived rather than something that was written;
- the **rules running on the same sentence**, in the left column, with each
  pattern that was tried and whether it matched;
- the **image** the vision model was given, embedded in the page as the exact
  bytes that were sent, next to the question and the model's own words back;
- **latency and provider-reported token counts** for every call, and a running
  total, so cost is measured on screen rather than asserted afterwards.

Both columns stay visible for the whole scene. That is the point: the difference
between the two paths is a thing a viewer sees, not a claim in a slide.
"""

from __future__ import annotations

import base64
import html
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


class _EvaluatingSession(Protocol):
    def evaluate(self, expression: str, arg: Any | None = ...) -> Any: ...


PANEL_WIDTH = 640

_CSS = f"""
#__mp{{position:fixed;top:0;right:0;width:{PANEL_WIDTH}px;height:100vh;z-index:2147483646;
  background:#0b0b18;color:#e2e8f0;font:12px/1.55 ui-monospace,Consolas,monospace;
  box-shadow:-8px 0 32px rgba(0,0,0,.5);border-left:2px solid #8383ff;
  display:flex;flex-direction:column;overflow:hidden}}
#__mp .hd{{padding:10px 14px;background:#181835;border-bottom:1px solid #2a2a4a;flex:none}}
#__mp .sc{{color:#8383ff;font-weight:700;font-size:11px;letter-spacing:.1em}}
#__mp .said{{color:#f8fafc;font-size:15px;margin-top:3px;font-weight:700}}
#__mp .why{{color:#94a3b8;font-size:11px;margin-top:3px;font-style:italic}}
#__mp .cols{{display:flex;flex:none;border-bottom:1px solid #2a2a4a;min-height:0}}
#__mp .col{{width:50%;padding:9px 12px;box-sizing:border-box}}
#__mp .col+.col{{border-left:1px solid #2a2a4a;background:#0e0e22}}
#__mp .ch{{font-size:10px;letter-spacing:.1em;font-weight:700;margin-bottom:6px}}
#__mp .ch.r{{color:#64748b}}
#__mp .ch.m{{color:#8383ff}}
#__mp .rx{{color:#5b6478;font-size:10.5px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}}
#__mp .rx.hit{{color:#4ade80}}
#__mp .meta{{color:#64748b;font-size:10px;margin:5px 0}}
#__mp .raw{{background:#05050f;border:1px solid #23234a;border-radius:5px;padding:7px 9px;
  color:#a5f3d0;font-size:10.5px;white-space:pre-wrap;overflow-wrap:break-word;
  max-height:150px;overflow:auto;min-height:46px}}
/* Sized so the whole panel fits an 800px viewport without scrolling: the reply,
   the image that was sent and the answer have to be visible at the same time,
   which is the entire point of the layout. */
#__mp .col .raw{{max-height:112px}}
#__mp .vis .raw{{max-height:92px}}
#__mp .raw.q{{color:#cbd5e1}}
#__mp .cur{{color:#8383ff;font-weight:700}}
#__mp .v{{display:inline-block;margin-top:6px;padding:2px 8px;border-radius:4px;
  font-weight:700;font-size:11.5px}}
#__mp .v.ok{{background:#052e16;color:#4ade80}}
#__mp .v.no{{background:#3f0d0d;color:#fca5a5}}
#__mp .v.wait{{background:#1e1b4b;color:#a5b4fc}}
/* The text oracle sits outside the scrolling area on purpose: in the last scene
   it is the claim the vision model contradicts, so it has to stay on screen
   while the contradiction arrives. */
#__mp .orc{{padding:6px 14px;border-bottom:1px solid #2a2a4a;flex:none}}
#__mp .vis{{padding:9px 14px;border-bottom:1px solid #2a2a4a;flex:1;overflow:auto;min-height:0}}
#__mp .shot{{display:flex;gap:10px;align-items:flex-start;margin:5px 0}}
#__mp .shot img{{max-width:230px;max-height:74px;border:1px solid #8383ff;border-radius:4px;
  background:#fff}}
#__mp .q{{color:#cbd5e1;font-size:11px;flex:1}}
#__mp .verd{{padding:9px 14px;font-weight:700;font-size:13px;flex:none}}
#__mp .verd.ok{{background:#052e16;color:#4ade80}}
#__mp .verd.no{{background:#3f0d0d;color:#fca5a5}}
#__mp .verd.idle{{background:#12122b;color:#64748b}}
#__mp .score{{padding:7px 14px;background:#05050f;border-top:1px solid #23234a;flex:none;
  display:flex;gap:14px;font-size:11px;color:#64748b;flex-wrap:wrap}}
#__mp .score b{{color:#e2e8f0}}
body{{margin-right:{PANEL_WIDTH}px !important}}
"""

_OPEN_JS = (
    "(a)=>{if(!document.getElementById('__mp_css')){"
    "const s=document.createElement('style');s.id='__mp_css';s.textContent=a.css;"
    "document.head.appendChild(s);}"
    "let c=document.getElementById('__mp');"
    "if(!c){c=document.createElement('div');c.id='__mp';document.body.appendChild(c);}"
    "return true;}"
)

# Whole-panel replacement. The state lives in Python, so there is one renderer
# and no way for the panel to drift from what actually happened.
_RENDER_JS = (
    "(a)=>{const c=document.getElementById('__mp');if(!c)return false;"
    "c.innerHTML=a.html;"
    # While a reply is still arriving the newest line is the interesting one;
    # once it is complete the first line is, because that is the answer.
    "c.querySelectorAll('.raw').forEach(e=>{e.scrollTop=a.tail?e.scrollHeight:0;});"
    # The vision block is last and the tallest; without this its answer sits
    # below the fold on a short viewport, which is the one thing that must not
    # be missable.
    "const v=c.querySelector('.vis');if(v&&a.focus)v.scrollTop=v.scrollHeight;"
    "return true;}"
)

_CLOSE_JS = (
    "()=>{for(const i of ['__mp','__mp_css']){const e=document.getElementById(i);if(e)e.remove();}"
    "document.body.style.marginRight='';return true;}"
)


def _esc(text: str) -> str:
    return html.escape(str(text))


def _tokens(usage: dict[str, int] | None) -> str:
    if not usage:
        return ""
    return f"{usage.get('input', 0)} in / {usage.get('output', 0)} out tokens"


@dataclass
class _Call:
    """One model call, as the panel needs to show it."""

    model: str = ""
    request: str = ""
    raw: str = ""
    shown: str = ""  # how much of raw has been revealed so far
    latency_ms: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    verdict: str = ""
    ok: bool | None = None
    waiting: bool = False
    # False when this answer cost nothing - a cache hit, or a recap of calls
    # already counted. The note says which, because a spend guard that saves
    # money silently looks exactly like one that is switched off.
    billed: bool = True
    note: str = ""


@dataclass
class ModelPanel:
    """Shows both paths on the same sentence, and what each call really cost."""

    session: _EvaluatingSession
    scene: str = ""
    said: str = ""
    why: str = ""
    patterns: list[tuple[str, bool]] = field(default_factory=list)
    rules_verdict: str = ""
    rules_ok: bool | None = None
    text: _Call = field(default_factory=_Call)
    vision: _Call = field(default_factory=_Call)
    image_b64: str = ""
    question: str = ""
    caption: str = ""
    oracle_text: str = ""
    oracle_ok: bool | None = None
    verdict: str = ""
    verdict_kind: str = "idle"
    _focus_vision: bool = False
    totals: dict[str, Any] = field(default_factory=lambda: {"calls": 0, "in": 0, "out": 0, "ms": 0.0})
    counts: dict[str, int] = field(default_factory=lambda: {"rules": 0, "model": 0, "caught": 0, "scenes": 0})

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(self) -> bool:
        return bool(self._js(_OPEN_JS, {"css": _CSS}))

    def close(self) -> None:
        self._js(_CLOSE_JS)

    def _js(self, expression: str, arg: Any | None = None) -> Any:
        try:
            return self.session.evaluate(expression, arg) if arg is not None else self.session.evaluate(expression)
        except Exception:
            return None  # the panel must never break a run

    # ── content ──────────────────────────────────────────────────────────────
    def begin_scene(self, scene: str, said: str, why: str) -> None:
        self.scene, self.said, self.why = scene, said, why
        self.patterns, self.rules_verdict, self.rules_ok = [], "", None
        self.text, self.vision = _Call(), _Call()
        self.image_b64, self.question, self.caption = "", "", ""
        self.oracle_text, self.oracle_ok = "", None
        self.verdict, self.verdict_kind = "", "idle"
        self._focus_vision = False
        self.render()

    def show_oracle(self, text: str, ok: bool) -> None:
        """What the text check concluded, shown before the model is asked.

        Order matters here: in the last scene this line says the goal was
        reached, and the model then contradicts it. Showing it afterwards would
        turn a caught error into a footnote.
        """
        self.oracle_text, self.oracle_ok = text, ok
        self.render()

    def show_rules(self, patterns: list[tuple[str, bool]], verdict: str, ok: bool) -> None:
        self.patterns, self.rules_verdict, self.rules_ok = patterns, verdict, ok
        self.render()

    def sending(self, model: str, request: str) -> None:
        self.text = _Call(model=model, request=request, waiting=True)
        self.render()

    def reply(
        self,
        raw: str,
        *,
        latency_ms: float,
        usage: dict[str, int] | None,
        verdict: str,
        ok: bool,
        type_delay: float = 0.0,
        billed: bool = True,
        note: str = "",
    ) -> None:
        """Reveal the reply, optionally a chunk at a time so it reads as arriving."""
        self.text.waiting = False
        self.text.raw, self.text.latency_ms = raw, latency_ms
        self.text.usage = dict(usage or {})
        self.text.billed, self.text.note = billed, note
        if billed:
            self._count(latency_ms, usage)
        if type_delay > 0:
            self._type(self.text, raw, type_delay)
        self.text.shown = raw
        self.text.verdict, self.text.ok = verdict, ok
        self.render()

    def looking(self, model: str, question: str, image_png: bytes, *, caption: str = "") -> None:
        """Show the exact bytes being sent, so the input is evidence too."""
        self.vision = _Call(model=model, waiting=True)
        self.question = question
        self.caption = caption
        self.image_b64 = base64.b64encode(image_png).decode("ascii") if image_png else ""
        self.render()

    def saw(
        self,
        raw: str,
        *,
        latency_ms: float,
        usage: dict[str, int] | None,
        verdict: str,
        ok: bool,
        type_delay: float = 0.0,
        billed: bool = True,
        note: str = "",
    ) -> None:
        self.vision.waiting = False
        self.vision.raw, self.vision.latency_ms = raw, latency_ms
        self.vision.usage = dict(usage or {})
        self.vision.billed, self.vision.note = billed, note
        self._focus_vision = True
        if billed:
            self._count(latency_ms, usage)
        if type_delay > 0:
            self._type(self.vision, raw, type_delay)
        self.vision.shown = raw
        self.vision.verdict, self.vision.ok = verdict, ok
        self.render()

    def conclude(self, verdict: str, kind: str) -> None:
        self.verdict, self.verdict_kind = verdict, kind
        self.render()

    def score(self, *, rules: int, model: int, caught: int, scenes: int) -> None:
        self.counts = {"rules": rules, "model": model, "caught": caught, "scenes": scenes}
        self.render()

    # ── internals ────────────────────────────────────────────────────────────
    def _count(self, latency_ms: float, usage: dict[str, int] | None) -> None:
        self.totals["calls"] += 1
        self.totals["ms"] += latency_ms
        self.totals["in"] += (usage or {}).get("input", 0)
        self.totals["out"] += (usage or {}).get("output", 0)

    def _type(self, call: _Call, raw: str, delay: float) -> None:
        """Reveal the reply a line at a time.

        Lines rather than characters: a JSON reply is eight or nine lines, which
        is about two seconds of reading at a pace a viewer can follow, where
        per-character typing of the same text takes half a minute.
        """
        lines = raw.splitlines() or [raw]
        for index in range(1, len(lines) + 1):
            call.shown = "\n".join(lines[:index])
            self.render(tail=True)
            time.sleep(delay)

    def render(self, *, tail: bool = False) -> bool:
        return bool(self._js(_RENDER_JS, {"html": self._html(), "tail": tail, "focus": self._focus_vision}))

    def _call_html(self, call: _Call) -> str:
        if not call.model:
            return '<div class="meta">no model configured</div>'
        parts = [f'<div class="meta">POST /chat/completions &middot; {_esc(call.model)}</div>']
        if call.request:
            parts.append(f'<div class="raw q">{_esc(call.request)}</div>')
        if call.waiting:
            parts.append('<div class="v wait">waiting for the model...</div>')
            return "".join(parts)
        shown = call.shown or call.raw
        if shown:
            cursor = '<span class="cur">&#9608;</span>' if shown != call.raw else ""
            parts.append(f'<div class="raw">{_esc(shown)}{cursor}</div>')
        stamp = f"{call.latency_ms:.0f} ms"
        if call.billed and _tokens(call.usage):
            stamp += f" &middot; {_tokens(call.usage)}"
        if call.note:
            stamp += f" &middot; {_esc(call.note)}"
        parts.append(f'<div class="meta">{stamp}</div>')
        if call.verdict:
            css = "ok" if call.ok else "no"
            parts.append(f'<div class="v {css}">{_esc(call.verdict)}</div>')
        return "".join(parts)

    def _html(self) -> str:
        rules = "".join(
            f'<div class="rx{" hit" if hit else ""}">{"[match]" if hit else "[  no ]"} {_esc(pattern)}</div>'
            for pattern, hit in self.patterns
        )
        if self.rules_verdict:
            css = "ok" if self.rules_ok else "no"
            rules += f'<div class="v {css}">{_esc(self.rules_verdict)}</div>'

        oracle = ""
        if self.oracle_text:
            css = "ok" if self.oracle_ok else "no"
            oracle = (
                f'<div class="orc"><div class="ch r">TEXT ORACLE &mdash; what the DOM says</div>'
                f'<div class="v {css}">{_esc(self.oracle_text)}</div></div>'
            )

        vision = ""
        if self.vision.model or self.image_b64:
            image = (
                f'<img src="data:image/png;base64,{self.image_b64}" alt="the region sent to the model">'
                if self.image_b64
                else '<div class="q">no screenshot</div>'
            )
            vision = (
                f'<div class="ch m">VISION MODEL &mdash; the pixels below are what was sent</div>'
                f'<div class="shot">{image}'
                f'<div class="q">{_esc(self.question)}'
                f'<div class="meta">{_esc(self.caption)}</div></div></div>'
                f"{self._call_html(self.vision)}"
            )

        totals = self.totals
        counts = self.counts
        return (
            f'<div class="hd"><div class="sc">{_esc(self.scene)}</div>'
            f'<div class="said">&ldquo;{_esc(self.said)}&rdquo;</div>'
            f'<div class="why">{_esc(self.why)}</div></div>'
            f'<div class="cols">'
            f'<div class="col"><div class="ch r">RULES &mdash; regular expressions, no model</div>{rules}</div>'
            f'<div class="col"><div class="ch m">LANGUAGE MODEL &mdash; same sentence</div>'
            f"{self._call_html(self.text)}</div></div>"
            f'{oracle}<div class="vis">{vision}</div>'
            f'<div class="verd {self.verdict_kind}">{_esc(self.verdict) or "&nbsp;"}</div>'
            f'<div class="score"><span>scenes <b>{counts["scenes"]}</b></span>'
            f'<span>rules solved <b>{counts["rules"]}</b></span>'
            f'<span>model solved <b>{counts["model"]}</b></span>'
            f'<span>false success caught <b>{counts["caught"]}</b></span>'
            f'<span>model calls <b>{totals["calls"]}</b></span>'
            f'<span>tokens <b>{totals["in"]} in / {totals["out"]} out</b></span>'
            f'<span>model time <b>{totals["ms"] / 1000:.1f}s</b></span></div>'
        )


__all__ = ["ModelPanel", "PANEL_WIDTH"]
