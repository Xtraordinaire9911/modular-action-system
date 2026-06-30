"""A curated, demo-worthy suite of MiniWoB++ tasks + a watchable controller.

`click-button` is one trivial click — boring to watch. This module provides a
small set of richer, multi-step tasks (type+submit, login form, ordered clicks,
read-and-click-link, close-dialog) whose DOM/utterance shapes are verified
against the upstream task HTML, plus a ``MiniwobController`` that makes each run
*legible*: it highlights the target element on the page, narrates the step, and
paces the actions so an audience can follow.

Defensive by design: every primitive first resolves its target in-page; a
missing/wrong selector is narrated and skipped rather than hanging the default
action timeout or throwing. So a selector that differs on some MiniWoB version
degrades to "skipped step", never a crash.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

# JS injected into the page: animate a periwinkle arrow cursor to the target
# with a glowing trail, highlight the element, show a caption. Returns whether
# the selector resolved (callers skip safely on false). Cursor persists across
# steps on the same page; appears at target without trail on fresh page load.
_FLASH_JS = """
(a)=>{
  const el=document.querySelector(a.sel); if(!el) return false;
  el.scrollIntoView({block:'center',inline:'center'});
  const r=el.getBoundingClientRect();
  const tx=r.left+r.width/2, ty=r.top+r.height/2;
  if(!document.getElementById('__cua_style')){
    const st=document.createElement('style'); st.id='__cua_style';
    st.textContent='@keyframes __cua_fade{from{opacity:.9}to{opacity:0}}'
      +'.__cua_dot{position:fixed;width:10px;height:10px;margin:-5px 0 0 -5px;border-radius:50%;'
      +'background:radial-gradient(circle,#b0b0ff,#8383ff);box-shadow:0 0 8px 2px rgba(131,131,255,.85);'
      +'pointer-events:none;z-index:99998;animation:__cua_fade .6s ease-out forwards}';
    document.head.appendChild(st);
  }
  let cur=document.getElementById('__cua_cursor'); let cx=tx, cy=ty;
  if(!cur){
    cur=document.createElement('div'); cur.id='__cua_cursor';
    cur.style.cssText='position:fixed;z-index:100000;pointer-events:none;'
      +'transition:left .5s ease,top .5s ease;filter:drop-shadow(0 0 6px rgba(131,131,255,.9));'
      +'left:'+tx+'px;top:'+ty+'px';
    cur.innerHTML='<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2 L4 20 L9 15 '
      +'L12.5 22 L15 21 L11.5 14 L18 14 Z" fill="#8383ff" stroke="#fff" stroke-width="1.3" '
      +'stroke-linejoin="round"/></svg>';
    document.body.appendChild(cur);
  } else { cx=parseFloat(cur.style.left)||tx; cy=parseFloat(cur.style.top)||ty; }
  for(let i=1;i<=10;i++){ const t=i/11;
    const d=document.createElement('div'); d.className='__cua_dot';
    d.style.left=(cx+(tx-cx)*t)+'px'; d.style.top=(cy+(ty-cy)*t)+'px';
    d.style.animationDelay=(t*0.18)+'s';
    document.body.appendChild(d); setTimeout(()=>d.remove(),900);
  }
  cur.style.left=tx+'px'; cur.style.top=ty+'px';
  document.querySelectorAll('.__cua_hl').forEach(e=>{e.style.outline='';e.style.boxShadow='';e.classList.remove('__cua_hl')});
  el.style.outline='3px solid #8383ff'; el.style.boxShadow='0 0 0 6px rgba(131,131,255,.25)'; el.classList.add('__cua_hl');
  const o=document.getElementById('__cua_cap'); if(o)o.remove();
  const c=document.createElement('div'); c.id='__cua_cap'; c.textContent=a.label;
  c.style.cssText='position:fixed;z-index:99999;left:16px;bottom:16px;background:#111;color:#fff;'
    +'padding:6px 10px;border-radius:6px;font:14px sans-serif;max-width:80vw';
  document.body.appendChild(c); return true;
}
"""

# JS: find a descendant of `scope` with exact text, tag it, return its selector or null.
_TAG_JS = (
    "(a)=>{const sc=document.querySelector(a.scope)||document;"
    "const prior=document.getElementById('__cua_target'); if(prior) prior.removeAttribute('id');"
    "const el=[...sc.querySelectorAll(a.tag)].find(e=>e.textContent.trim()===a.text);"
    "if(!el)return null;el.id='__cua_target';return '#__cua_target';}"
)

# JS: inject/update the env+task badge overlay (top-right corner, persists per page load).
_BADGE_JS = (
    "(a)=>{"
    "let b=document.getElementById('__cua_badge');"
    "if(!b){b=document.createElement('div');b.id='__cua_badge';"
    "b.style.cssText='position:fixed;top:10px;right:10px;z-index:100001;"
    "background:rgba(15,15,35,.9);color:#e2e8f0;font:bold 11px/1.6 monospace;"
    "padding:6px 12px;border-radius:7px;border:1.5px solid #8383ff;"
    "box-shadow:0 0 10px rgba(131,131,255,.45);pointer-events:none';"
    "document.body.appendChild(b);}"
    "b.innerHTML='<span style=\"color:#8383ff\">'+a.env+'</span><br>'+a.task;}"
)


class ControllerSession(Protocol):
    def click(self, selector: str) -> Any: ...
    def fill(self, selector: str, value: str) -> Any: ...
    def text_content(self, selector: str) -> str | None: ...
    def evaluate(self, expression: str, arg: Any | None = ...) -> Any: ...


def quoted_values(text: str) -> list[str]:
    """All double-quoted substrings, in order (utterances embed targets in quotes)."""
    return re.findall(r'"([^"]+)"', text or "")


class MiniwobController:
    """Narrated, highlighted, paced primitives over a BrowserSession-like object."""

    def __init__(self, session: ControllerSession, *, step_delay: float = 1.2, narrate: Callable[[str], None] = print):
        self._s = session
        self._delay = step_delay
        self._say = narrate

    def _pause(self, factor: float = 1.0) -> None:
        time.sleep(max(0.0, self._delay * factor))

    # ── episode lifecycle ──────────────────────────────────────────────────────
    def start(self) -> None:
        self._s.click("#sync-task-cover")  # START gate begins the episode
        self._pause(0.6)

    def query(self) -> str:
        return self._s.text_content("#query") or ""

    def reward(self) -> float:
        try:
            return float(self._s.evaluate("WOB_REWARD_GLOBAL") or 0.0)
        except Exception:
            return 0.0

    def setup_badge(self, env_name: str, task_name: str) -> None:
        """Inject/refresh the env+task overlay badge (top-right corner)."""
        try:
            self._s.evaluate(_BADGE_JS, {"env": env_name, "task": task_name})
        except Exception:
            pass  # badge is cosmetic; never fail the task for it

    # ── safe, visible primitives ───────────────────────────────────────────────
    def _highlight(self, css: str, label: str) -> bool:
        try:
            return bool(self._s.evaluate(_FLASH_JS, {"sel": css, "label": label}))
        except Exception:
            return False

    def click_css(self, css: str, why: str) -> bool:
        self._say(f"  -> {why}")
        if not self._highlight(css, why):  # resolve first; never hang on a bad selector
            self._say(f"     (skip: '{css}' not found)")
            return False
        self._pause()
        self._s.click(css)
        self._pause(0.5)
        return True

    def fill(self, css: str, value: str, why: str) -> bool:
        self._say(f"  -> {why}")
        if not self._highlight(css, why):
            self._say(f"     (skip: '{css}' not found)")
            return False
        self._pause()
        self._s.fill(css, str(value))
        self._pause(0.5)
        return True

    def click_text(self, scope_css: str, tag: str, text: str, why: str) -> bool:
        self._say(f"  -> {why}")
        try:
            selector = self._s.evaluate(_TAG_JS, {"scope": scope_css, "tag": tag, "text": text})
        except Exception:
            selector = None
        if not selector:
            self._say(f"     (skip: no <{tag}> with text {text!r})")
            return False
        self._highlight(selector, why)
        self._pause()
        self._s.click(selector)
        self._pause(0.5)
        return True


class MockEnvController(MiniwobController):
    """MiniwobController variant for non-MiniWoB pages (no #sync-task-cover gate,
    no WOB_REWARD_GLOBAL). Reuses all highlight/fill/click primitives unchanged."""

    def start(self) -> None:
        # No START gate on mock pages — just pause briefly for the page to settle.
        self._pause(0.3)

    def query(self) -> str:
        return ""  # goal is provided externally by the task spec

    def reward(self) -> float:
        return 0.0  # success evaluated by the runner, not a JS global


# ── task solvers (each grounded in the real MiniWoB task HTML) ──────────────────
def solve_enter_text(c: MiniwobController) -> None:
    vals = quoted_values(c.query())
    if vals:
        c.fill("#tt", vals[0], f'Type "{vals[0]}" into the field')
    c.click_css("#subbtn", "Press Submit")


def solve_login_user(c: MiniwobController) -> None:
    vals = quoted_values(c.query())  # "username" then "password"
    if len(vals) >= 2:
        c.fill("#username", vals[0], f'Enter username "{vals[0]}"')
        c.fill("#password", vals[1], f'Enter password "{vals[1]}"')
    c.click_css("#subbtn", "Click Login")


def solve_enter_password(c: MiniwobController) -> None:
    vals = quoted_values(c.query())
    if vals:
        c.fill("#password", vals[0], f'Enter password "{vals[0]}"')
        c.fill("#verify", vals[0], "Repeat the same password")
    c.click_css("#subbtn", "Submit")


def solve_click_link(c: MiniwobController) -> None:
    vals = quoted_values(c.query())
    if vals:
        c.click_text("#area", "a", vals[0], f'Click the link "{vals[0]}"')


def solve_click_button_sequence(c: MiniwobController) -> None:
    c.click_css("#subbtn", "Click button ONE")
    c.click_css("#subbtn2", "Then click button TWO")


def solve_click_dialog(c: MiniwobController) -> None:
    c.click_css(".ui-dialog-titlebar-close", "Close the dialog via its x")


@dataclass
class MiniwobDemoTask:
    name: str  # task HTML stem under miniwob/, e.g. "login-user"
    title: str  # human-facing narration title
    solve: Callable[[MiniwobController], None]


# Escalating, varied, audience-friendly order.
DEMO_TASKS: list[MiniwobDemoTask] = [
    MiniwobDemoTask("enter-text", "Type text and submit", solve_enter_text),
    MiniwobDemoTask("login-user", "Fill a login form (username + password)", solve_login_user),
    MiniwobDemoTask("enter-password", "Enter a password into two fields", solve_enter_password),
    MiniwobDemoTask("click-link", "Read the instruction and click the right link", solve_click_link),
    MiniwobDemoTask("click-button-sequence", "Click two buttons in the correct order", solve_click_button_sequence),
    MiniwobDemoTask("click-dialog", "Close a popup dialog", solve_click_dialog),
]


def run_task(controller: MiniwobController, task: MiniwobDemoTask) -> dict[str, Any]:
    """Start the episode, run the solver, and read the reward."""
    controller.start()
    utterance = controller.query()
    task.solve(controller)
    reward = controller.reward()
    return {
        "name": task.name,
        "title": task.title,
        "utterance": utterance,
        "reward": reward,
        "success": reward > 0.0,
    }
