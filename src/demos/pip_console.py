"""An in-page console that narrates what the agent is doing, and why.

Written for a viewer who has never seen the project. Each step shows, in this
order: which phase of the loop we are in, what is happening in plain language,
why that step exists at all, and only then the source that is executing. Code
first would lose the room.

The panel is stacked **inside the page**, not in a separate window. An earlier
version used the Document Picture-in-Picture API, which is a genuine PiP window
but sits outside the page: a screen recording then has two windows to follow and
a page screenshot captures none of it. Session isolation via a real PiP
interface is a separate piece of work owned by another team member; this is a
narration surface and does not claim to be that.

Set ``surface="pip"`` to opt into the detached window anyway.
"""

from __future__ import annotations

import inspect
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

_MAX_SOURCE_LINES = 16


class _EvaluatingSession(Protocol):
    def evaluate(self, expression: str, arg: Any | None = ...) -> Any: ...


_PANEL_CSS = """
#__cua_c{position:fixed;top:0;right:0;width:430px;height:100vh;z-index:2147483646;
  background:#0f0f23;color:#e2e8f0;font:13px/1.6 ui-monospace,Consolas,monospace;
  box-shadow:-8px 0 32px rgba(0,0,0,.45);display:flex;flex-direction:column;
  border-left:2px solid #8383ff}
#__cua_c .hd{padding:12px 16px;background:#181835;border-bottom:1px solid #2a2a4a}
#__cua_c .ttl{color:#8383ff;font-weight:700;font-size:12px;letter-spacing:.08em}
#__cua_c .goal{color:#94a3b8;font-size:11.5px;margin-top:4px}
#__cua_c .phase{padding:12px 16px;background:#15152e;border-bottom:1px solid #2a2a4a;
  display:flex;align-items:center;gap:10px}
#__cua_c .num{background:#8383ff;color:#0f0f23;font-weight:700;font-size:12px;
  padding:3px 9px;border-radius:5px;white-space:nowrap}
#__cua_c .pname{font-size:15px;font-weight:700;color:#e8e8ff}
#__cua_c .sec{padding:11px 16px;border-bottom:1px solid #1e1e3a}
#__cua_c .lbl{color:#8383ff;font-size:10px;letter-spacing:.12em;font-weight:700;
  margin-bottom:5px}
#__cua_c .what{color:#f1f5f9;font-size:13.5px;line-height:1.55}
#__cua_c .why{color:#a5b4cf;font-size:12px;line-height:1.55;font-style:italic}
/* pre-wrap, not pre: a long docstring line was being cut off at the panel
   edge, so the explanation a viewer most needed was the part they could not
   read. Continuation lines are indented so wrapped code stays scannable. */
#__cua_c .code{flex:1;overflow-y:auto;overflow-x:hidden;padding:11px 16px;
  white-space:pre-wrap;overflow-wrap:break-word;padding-left:30px;text-indent:-14px;
  color:#cdd0f0;font-size:11px;line-height:1.5;background:#0b0b1c}
#__cua_c .res{padding:12px 16px;font-size:13px;font-weight:700;
  border-top:1px solid #2a2a4a}
#__cua_c .ok{background:#052e16;color:#4ade80}
#__cua_c .fail{background:#3f0d0d;color:#fca5a5}
#__cua_c .run{background:#181835;color:#94a3b8}
#__cua_c .steps{padding:9px 16px;background:#0b0b1c;border-top:1px solid #2a2a4a;
  font-size:10.5px;color:#64748b;max-height:112px;overflow:auto}
#__cua_c .steps div{padding:1px 0}
#__cua_c .steps .d{color:#4ade80}
#__cua_c .steps .x{color:#fca5a5}
body{margin-right:430px !important}
"""

_OPEN_JS = (
    "(a)=>{"
    "if(!document.getElementById('__cua_c_css')){"
    "const s=document.createElement('style');s.id='__cua_c_css';s.textContent=a.css;"
    "document.head.appendChild(s);}"
    "let c=document.getElementById('__cua_c');"
    "if(!c){c=document.createElement('div');c.id='__cua_c';document.body.appendChild(c);}"
    "c.innerHTML="
    '\'<div class="hd"><div class="ttl">AUTONOMOUS AGENT</div>'
    "<div class=\"goal\">'+a.goal+'</div></div>"
    '<div class="phase"><span class="num" id="__n">--</span>'
    '<span class="pname" id="__p">starting</span></div>'
    '<div class="sec"><div class="lbl">WHAT IS HAPPENING</div>'
    '<div class="what" id="__w"></div></div>'
    '<div class="sec"><div class="lbl">WHY THIS STEP EXISTS</div>'
    '<div class="why" id="__y"></div></div>'
    '<div class="lbl" style="padding:9px 16px 0">CODE RUNNING NOW</div>'
    '<div class="code" id="__s"></div>'
    '<div class="res run" id="__r">running...</div>'
    '<div class="steps" id="__l"></div>\';'
    "return 'inline';}"
)

_STEP_JS = (
    "(a)=>{const g=(i)=>document.getElementById(i);"
    "if(!g('__cua_c'))return false;"
    "g('__n').textContent=a.num;g('__p').textContent=a.phase;"
    "g('__w').textContent=a.what;g('__y').textContent=a.why;"
    "g('__s').textContent=a.code;g('__s').scrollTop=0;"
    "const r=g('__r');r.className='res run';r.textContent='running...';"
    "return true;}"
)

_RESULT_JS = (
    "(a)=>{const g=(i)=>document.getElementById(i);"
    "if(!g('__cua_c'))return false;"
    "const r=g('__r');r.className='res '+(a.ok?'ok':'fail');r.textContent=a.text;"
    "const l=g('__l');const d=document.createElement('div');"
    "d.className=a.ok?'d':'x';d.textContent=(a.ok?'[ok] ':'[!!] ')+a.phase+' - '+a.detail;"
    "l.appendChild(d);l.scrollTop=l.scrollHeight;return true;}"
)

_BANNER_JS = (
    "(a)=>{let b=document.getElementById('__cua_b');"
    "if(!b){b=document.createElement('div');b.id='__cua_b';"
    "b.style.cssText='position:fixed;top:0;left:0;right:430px;z-index:2147483647;"
    "padding:14px;text-align:center;font:700 17px/1.4 system-ui;color:#fff;"
    "transition:opacity .3s';document.body.appendChild(b);}"
    "b.style.background=a.color;b.textContent=a.text;b.style.opacity='1';"
    "return true;}"
)

_HIDE_BANNER_JS = "()=>{const b=document.getElementById('__cua_b');if(b)b.style.opacity='0';return true;}"

_CLOSE_JS = (
    "()=>{for(const i of ['__cua_c','__cua_c_css','__cua_b']){"
    "const e=document.getElementById(i);if(e)e.remove();}"
    "document.body.style.marginRight='';return true;}"
)


def source_of(target: Callable[..., Any] | str) -> str:
    """The source a viewer should read for this step, trimmed to fit the panel."""
    if isinstance(target, str):
        return target
    try:
        raw = inspect.getsource(target)
    except (OSError, TypeError):
        return f"# source unavailable for {getattr(target, '__name__', target)!r}"
    lines = [ln for ln in textwrap.dedent(raw).splitlines()]
    if len(lines) > _MAX_SOURCE_LINES:
        lines = lines[:_MAX_SOURCE_LINES] + ["    ..."]
    return "\n".join(lines)


@dataclass
class AgentConsole:
    """Narrates the loop inside the page the agent is driving."""

    session: _EvaluatingSession
    goal: str = ""
    surface: str = "inline"
    opened: bool = False
    _log: list[str] = field(default_factory=list)

    def _js(self, expression: str, arg: Any | None = None) -> Any:
        try:
            return self.session.evaluate(expression, arg) if arg is not None else self.session.evaluate(expression)
        except Exception:
            return None  # narration must never break a run

    def open(self, goal: str = "") -> bool:
        self.goal = goal or self.goal
        self.opened = self._js(_OPEN_JS, {"css": _PANEL_CSS, "goal": self.goal}) == "inline"
        return self.opened

    def step(self, num: str, phase: str, what: str, why: str, code: Callable[..., Any] | str) -> None:
        """Announce a step before it runs: phase, plain language, reason, source."""
        self._js(_STEP_JS, {"num": num, "phase": phase, "what": what, "why": why, "code": source_of(code)})

    def result(self, phase: str, detail: str, ok: bool, headline: str = "") -> None:
        """Report the outcome of the step that just ran."""
        self._js(
            _RESULT_JS,
            {"ok": ok, "text": headline or ("succeeded" if ok else "FAILED"), "phase": phase, "detail": detail},
        )

    def banner(self, text: str, color: str = "#4f46e5") -> None:
        """A full-width message across the page, for moments that need one."""
        self._js(_BANNER_JS, {"text": text, "color": color})

    def hide_banner(self) -> None:
        self._js(_HIDE_BANNER_JS)

    def close(self) -> None:
        self._js(_CLOSE_JS)
        self.opened = False


__all__ = ["AgentConsole", "source_of"]
