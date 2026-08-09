"""An in-page console that narrates what the agent is doing, and why.

Written for a viewer who has never seen the project. Each step shows, in this
order: which phase of the loop we are in, what is happening in plain language,
why that step exists at all, and only then the source that is executing. Code
first would lose the room.

The panel is stacked **inside the page**, not in a separate window. An earlier
version used the Document Picture-in-Picture API, which is a genuine PiP window
but sits outside the page: a screen recording then has two windows to follow and
a page screenshot captures none of it.

**On the name.** This module used to be called ``pip_console``, and that name
was wrong in the way the review specifically called out. In the referenced work
Picture-in-Picture means a *supervised interface*: the agent operates in a
visibly separate session that a person can watch live and take over from. It is
a human-oversight mechanism, not a window style. Displaying narration in a
floating panel is not that, and neither is running each episode in its own
browser context - both are weaker properties, and naming either of them "PiP"
made the requested capability look delivered when it was not.

What this is: a narration surface. What the project has instead of PiP is
browser-context isolation (``src/perception/browser_session.py``) plus a
tier-4 handover that pauses and records a human decision
(``src/recovery/supervised_takeover.py``). Neither is a supervised
picture-in-picture interface, and the claims table says so.
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
/* One div per source line so a single line can be highlighted as it executes.
   The panel used to hold the source as one text node, which is why it read as
   a screenshot of the file rather than as code running. */
#__cua_c .ln{padding:0 16px;white-space:pre-wrap;overflow-wrap:break-word}
#__cua_c .ln.on{background:#8383ff;color:#0f0f23;font-weight:700;
  box-shadow:0 0 14px rgba(131,131,255,.7)}
#__cua_c .vars{padding:8px 16px;background:#141430;border-top:1px solid #2a2a4a;
  color:#a5b4cf;font-size:10.5px;max-height:96px;overflow:auto}
#__cua_c .vars b{color:#8383ff;font-weight:700}
/* The running tallies the metrics are computed from. Deliberately the quietest
   thing in the panel: they must be checkable at any moment without competing
   with the step being narrated. */
#__cua_c .tally{padding:5px 16px;background:#0b0b1c;border-top:1px solid #1e1e3a;
  color:#4b5573;font-size:9.5px;letter-spacing:.02em;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
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
    '<div class="vars" id="__v"></div>'
    '<div class="res run" id="__r">running...</div>'
    '<div class="tally" id="__t"></div>'
    '<div class="steps" id="__l"></div>\';'
    "return 'inline';}"
)

_TALLY_JS = "(a)=>{const t=document.getElementById('__t');if(!t)return false;" "t.textContent=a.text;return true;}"

# Renders the source as one div per line, which is what makes a moving
# highlight possible at all.
_CODE_LINES_JS = (
    "(a)=>{const s=document.getElementById('__s');if(!s)return false;"
    "s.innerHTML='';"
    "a.lines.forEach((t,i)=>{const d=document.createElement('div');"
    "d.className='ln';d.id='__ln'+i;d.textContent=t||' ';s.appendChild(d);});"
    "const v=document.getElementById('__v');if(v)v.textContent='';"
    "return true;}"
)

# Moves the highlight to the line the interpreter is on, and shows the locals
# as they stand at that moment.
_MARK_LINE_JS = (
    "(a)=>{const s=document.getElementById('__s');if(!s)return false;"
    "const prev=s.querySelector('.ln.on');if(prev)prev.classList.remove('on');"
    "const el=document.getElementById('__ln'+a.index);"
    "if(el){el.classList.add('on');"
    "const top=el.offsetTop-s.clientHeight/2;s.scrollTop=top>0?top:0;}"
    "const v=document.getElementById('__v');"
    "if(v)v.innerHTML=a.vars.map(p=>'<b>'+p[0]+'</b> = '+p[1]).join('<br>');"
    "return true;}"
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

    def show_code(self, lines: list[str]) -> None:
        """Load the source to be executed, one element per line."""
        self._js(_CODE_LINES_JS, {"lines": lines})

    def mark_line(self, index: int, variables: dict[str, str]) -> None:
        """Move the highlight to the line now executing, and show its locals."""
        self._js(_MARK_LINE_JS, {"index": index, "vars": [[k, v] for k, v in variables.items()]})

    def run_traced(self, func: Any, *args: Any, line_delay: float = 0.06, **kwargs: Any) -> Any:
        """Run ``func(*args)`` with the panel following its real execution.

        The highlight is driven by sys.settrace on the actual call, so what a
        viewer watches is the interpreter's own path through the function, loops
        and early returns included - not a scripted animation of it.
        """
        from src.demos.live_tracer import run_traced as _run
        from src.demos.live_tracer import source_of as _source_of

        traced = _source_of(func)
        self.show_code(traced.lines)

        def on_line(lineno: int, variables: dict[str, str]) -> None:
            index = traced.index_of(lineno)
            if index >= 0:
                self.mark_line(index, variables)

        return _run(func, on_line, *args, line_delay=line_delay, **kwargs)

    def result(self, phase: str, detail: str, ok: bool, headline: str = "") -> None:
        """Report the outcome of the step that just ran."""
        self._js(
            _RESULT_JS,
            {"ok": ok, "text": headline or ("succeeded" if ok else "FAILED"), "phase": phase, "detail": detail},
        )

    def tally(self, text: str) -> None:
        """Show the running counts the metrics are derived from.

        Updated on every step, faulted or not, so the figures reported at the
        end can be traced back to something a viewer watched accumulate.
        """
        self._js(_TALLY_JS, {"text": text})

    def banner(self, text: str, color: str = "#4f46e5") -> None:
        """A full-width message across the page, for moments that need one."""
        self._js(_BANNER_JS, {"text": text, "color": color})

    def hide_banner(self) -> None:
        self._js(_HIDE_BANNER_JS)

    def close(self) -> None:
        self._js(_CLOSE_JS)
        self.opened = False


__all__ = ["AgentConsole", "source_of"]
