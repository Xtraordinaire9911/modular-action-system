"""The right-hand half of a device demo, in the same language as ``ModelPanel``.

``ModelPanel`` exists because a caption saying "sent to a language model" looks
identical whether a call happened or not, so the demo shows the raw reply. The
device demos have the same problem in a different place: a dashboard whose
numbers change looks identical whether the agent wrote to a Thing or whether the
page was reloaded with different defaults. So the right half carries the wire.

Four sections, and the order is an argument:

* **wire** - the actual requests and status codes, newest last. This is the part
  that cannot be faked by a nicer-looking page.
* **readings** - commanded beside measured, on the same tick. Two columns because
  the entire claim of this project is that they are different facts.
* **code** - the real source of the predicate being evaluated, read with
  ``inspect.getsource``. Not a screenshot of code: if the function changes, this
  changes, because there is no second copy.
* **stats** - counters that make the boring answer visible. "Four writes, four
  2xx, three measurements agreed" is the sentence a viewer can check against the
  table above it.

The panel holds no logic of its own. Every value it shows is passed in from the
script that just observed it, and the whole panel is re-rendered from that state
on every update - one renderer, so the panel cannot drift from what happened.

Deliberately the same palette, width and border as ``ModelPanel``: two demos that
look like two systems teach the audience that there are two systems.
"""

from __future__ import annotations

import html
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class _EvaluatingSession(Protocol):
    def evaluate(self, expression: str, arg: Any | None = ...) -> Any: ...


PANEL_WIDTH = 640

_CSS = f"""
#__dp{{position:fixed;top:0;right:0;width:{PANEL_WIDTH}px;height:100vh;z-index:2147483646;
  background:#0b0b18;color:#e2e8f0;font:12px/1.55 ui-monospace,Consolas,monospace;
  box-shadow:-8px 0 32px rgba(0,0,0,.5);border-left:2px solid #8383ff;
  display:flex;flex-direction:column;overflow:hidden}}
#__dp .hd{{padding:10px 14px;background:#181835;border-bottom:1px solid #2a2a4a;flex:none}}
#__dp .sc{{color:#8383ff;font-weight:700;font-size:11px;letter-spacing:.1em}}
#__dp .said{{color:#f8fafc;font-size:15px;margin-top:3px;font-weight:700}}
#__dp .why{{color:#94a3b8;font-size:11px;margin-top:3px;font-style:italic}}
#__dp .sec{{padding:8px 14px;border-bottom:1px solid #2a2a4a;flex:none;min-height:0}}
#__dp .sec.grow{{flex:1;overflow:auto}}
#__dp .ch{{font-size:10px;letter-spacing:.1em;font-weight:700;color:#8383ff;margin-bottom:5px}}
#__dp .wire{{background:#05050f;border:1px solid #23234a;border-radius:5px;padding:6px 8px;
  font-size:10.5px;max-height:96px;overflow:auto}}
#__dp .wl{{color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
#__dp .wl b{{color:#a5f3d0;font-weight:700}}
#__dp .wl .st{{color:#4ade80;font-weight:700}}
#__dp .wl .st.bad{{color:#fca5a5}}
/* width:auto, not 100%. Stretched across 640px the two readings end up at
   opposite edges of the panel, and the whole point is to compare them - they
   have to sit next to each other to be read as a pair. */
#__dp table{{width:auto;border-collapse:collapse;font-size:10.5px}}
#__dp th{{color:#64748b;text-align:right;font-weight:700;padding:2px 14px 2px 0;
  border-bottom:1px solid #23234a;font-size:9.5px;letter-spacing:.06em}}
#__dp th.l,#__dp td.l{{text-align:left}}
#__dp td{{text-align:right;padding:2px 14px 2px 0;color:#cbd5e1}}
#__dp tr.diff td{{color:#fcd34d}}
#__dp tr.diff td.tag{{color:#b45309;font-weight:700}}
#__dp .cmd{{color:#a5b4fc}}
#__dp .mea{{color:#5eead4}}
#__dp pre{{margin:0;background:#05050f;border:1px solid #23234a;border-radius:5px;
  padding:6px 8px;font-size:10px;line-height:1.5;max-height:150px;overflow:auto;
  color:#94a3b8;white-space:pre}}
#__dp pre .on{{display:block;background:#1e1b4b;color:#e2e8f0}}
#__dp .stats{{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:#64748b}}
#__dp .stats b{{color:#e2e8f0}}
#__dp .verd{{padding:8px 14px;flex:none;font-size:11px}}
#__dp .vrow{{display:flex;gap:8px;align-items:baseline;padding:2px 0}}
#__dp .vrow .nm{{flex:1;color:#94a3b8}}
#__dp .vrow .ev{{color:#cbd5e1}}
#__dp .pill{{padding:1px 7px;border-radius:4px;font-weight:700;font-size:10px}}
#__dp .pill.ok{{background:#052e16;color:#4ade80}}
#__dp .pill.no{{background:#3f0d0d;color:#fca5a5}}
#__dp .concl{{padding:9px 14px;font-weight:700;font-size:12.5px;flex:none}}
#__dp .concl.no{{background:#3f0d0d;color:#fca5a5}}
#__dp .concl.ok{{background:#052e16;color:#4ade80}}
#__dp .concl.idle{{background:#12122b;color:#64748b}}
body{{margin-right:{PANEL_WIDTH}px !important}}
"""

_OPEN_JS = (
    "(a)=>{if(!document.getElementById('__dp_css')){"
    "const s=document.createElement('style');s.id='__dp_css';s.textContent=a.css;"
    "document.head.appendChild(s);}"
    "let c=document.getElementById('__dp');"
    "if(!c){c=document.createElement('div');c.id='__dp';document.body.appendChild(c);}"
    "return true;}"
)

_RENDER_JS = (
    "(a)=>{const c=document.getElementById('__dp');if(!c)return false;"
    "c.innerHTML=a.html;"
    # The newest wire line and the newest reading are the interesting ones.
    "c.querySelectorAll('.wire,.sec.grow').forEach(e=>{e.scrollTop=e.scrollHeight;});"
    # The highlighted source line, on the other hand, has to be in view wherever
    # it happens to be in the function.
    "const on=c.querySelector('pre .on');if(on)on.scrollIntoView({block:'nearest'});"
    "return true;}"
)

_CLOSE_JS = (
    "()=>{for(const i of ['__dp','__dp_css']){const e=document.getElementById(i);if(e)e.remove();}"
    "document.body.style.marginRight='';return true;}"
)


def _esc(text: Any) -> str:
    return html.escape(str(text))


def _shown(value: Any) -> str:
    """One decimal at most; a projector is not a debugger.

    Integrating a ramp in floating point yields ``21.200000000000003``, which is
    fifteen digits of distraction in the middle of the table the audience is
    meant to read. Display only - callers compare the raw values.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:g}" if float(value).is_integer() else f"{value:.1f}"


@dataclass
class _Exchange:
    """One request, as the wire section needs to show it."""

    method: str
    path: str
    body: Any = None
    status: int | None = None


@dataclass
class _Verdict:
    name: str
    evidence: str
    passed: bool
    note: str = ""


@dataclass
class DevicePanel:
    """Everything the audience needs that a dashboard cannot show by itself."""

    session: _EvaluatingSession
    scene: str = ""
    said: str = ""
    why: str = ""
    wire: list[_Exchange] = field(default_factory=list)
    readings: list[tuple[float, Any, Any]] = field(default_factory=list)
    commanded_name: str = ""
    measured_name: str = ""
    source: str = ""
    source_active: int = -1
    source_title: str = ""
    verdicts: list[_Verdict] = field(default_factory=list)
    conclusion: str = ""
    conclusion_kind: str = "idle"
    # Counters, kept here so the numbers on screen are the ones this object saw
    # rather than a second tally that can disagree with the wire above it.
    writes: int = 0
    accepted: int = 0
    agreed: int = 0
    diverged: int = 0

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def open(self) -> bool:
        return bool(self._js(_OPEN_JS, {"css": _CSS}))

    def close(self) -> None:
        self._js(_CLOSE_JS)

    def _js(self, expression: str, arg: Any | None = None) -> Any:
        """A panel must never be able to fail a demo.

        Same reasoning as the pointer overlay: if the page navigates while the
        panel is rendering, the run should carry on and the readings should still
        be printed to the terminal.
        """
        try:
            return self.session.evaluate(expression, arg)
        except Exception:  # noqa: BLE001 - presentation layer, deliberately swallowed
            return False

    # ── content ────────────────────────────────────────────────────────────────

    def begin_act(self, scene: str, said: str, why: str) -> None:
        self.scene = scene
        self.said = said
        self.why = why
        self.readings = []
        self.verdicts = []
        self.conclusion = ""
        self.conclusion_kind = "idle"
        self.render()

    def sent(self, method: str, path: str, body: Any = None) -> None:
        """Record a request before its status is known.

        Two steps rather than one, so the panel shows the request in flight. A
        line that only appears once the answer arrived cannot show a slow call,
        and "the write was accepted" is half of what these demos are about.
        """
        self.wire.append(_Exchange(method=method, path=path, body=body))
        if method.upper() in ("PUT", "POST"):
            self.writes += 1
        self.render()

    def answered(self, status: int) -> None:
        if self.wire:
            self.wire[-1].status = status
            if 200 <= status < 300:
                self.accepted += 1
        self.render()

    def show_readings(
        self,
        frames: list[tuple[float, Any, Any]],
        *,
        commanded: str,
        measured: str,
    ) -> None:
        """Replace the table. Safe to call once per frame while sampling.

        Counting deliberately does not happen here: this is called on every tick
        so the table fills in as the room is watched, and a counter bumped per
        tick would report thirty divergences for one jammed motor. The tally is
        ``settled`` below, called once when the observation is over.
        """
        self.readings = frames
        self.commanded_name = commanded
        self.measured_name = measured
        self.render()

    def settled(self, *, converged: bool) -> None:
        """One observation finished, and this is what it concluded."""
        if converged:
            self.agreed += 1
        else:
            self.diverged += 1
        self.render()

    def show_source(
        self,
        target: Callable[..., Any] | str,
        *,
        active: int = -1,
        highlight: str = "",
        title: str = "",
    ) -> None:
        """Put the real predicate on screen, read from the function itself.

        ``inspect.getsource`` rather than a pasted string: there is no second
        copy to fall out of date, so what the audience reads is what ran.

        ``highlight`` names the line by a substring of it rather than by number.
        A line index would be silently wrong the first time anyone edits the
        function above it - and pointing the spotlight at the wrong line is worse
        than not pointing it, because the panel would be asserting that some
        unrelated statement is the one that decides the verdict.

        The highlight is honest about what it is: a spotlight this script points,
        not a tracer following the interpreter.
        """
        if callable(target):
            try:
                self.source = inspect.getsource(target)
            except (OSError, TypeError):  # pragma: no cover - source not available
                self.source = f"# source for {getattr(target, '__name__', target)} is not available"
            self.source_title = title or getattr(target, "__qualname__", "")
        else:
            self.source = str(target)
            self.source_title = title
        self.source_active = active
        if highlight:
            self.source_active = next(
                (i for i, line in enumerate(self.source.splitlines()) if highlight in line),
                -1,
            )
        self.render()

    def show_verdicts(self, rows: list[tuple[str, str, bool, str]]) -> None:
        self.verdicts = [_Verdict(name=n, evidence=e, passed=p, note=note) for n, e, p, note in rows]
        self.render()

    def conclude(self, text: str, *, kind: str = "no") -> None:
        self.conclusion = text
        self.conclusion_kind = kind
        self.render()

    # ── rendering ──────────────────────────────────────────────────────────────

    def _wire_html(self) -> str:
        if not self.wire:
            return ""
        lines = []
        for exchange in self.wire[-14:]:
            body = "" if exchange.body is None else f" {_esc(exchange.body)}"
            if exchange.status is None:
                status = '<span class="st">...</span>'
            else:
                bad = "" if 200 <= exchange.status < 300 else " bad"
                status = f'<span class="st{bad}">{exchange.status}</span>'
            lines.append(
                f'<div class="wl"><b>{_esc(exchange.method)}</b> {_esc(exchange.path)}' f"{body} &rarr; {status}</div>"
            )
        return f'<div class="sec"><div class="ch">WIRE</div><div class="wire">{"".join(lines)}</div></div>'

    def _readings_html(self) -> str:
        if not self.readings:
            return ""
        rows = []
        for elapsed, commanded, measured in self.readings[-12:]:
            differs = commanded != measured
            tag = "&ne;" if differs else ""
            rows.append(
                f'<tr class="{"diff" if differs else ""}">'
                f'<td class="l">{elapsed:.2f}s</td>'
                f'<td class="cmd">{_esc(_shown(commanded))}</td>'
                f'<td class="mea">{_esc(_shown(measured))}</td>'
                f'<td class="tag">{tag}</td></tr>'
            )
        return (
            '<div class="sec grow"><div class="ch">READINGS &mdash; same tick, two facts</div>'
            f'<table><tr><th class="l">t</th><th>{_esc(self.commanded_name)}</th>'
            f"<th>{_esc(self.measured_name)}</th><th></th></tr>{''.join(rows)}</table></div>"
        )

    def _source_html(self) -> str:
        if not self.source:
            return ""
        lines = self.source.rstrip().splitlines()
        out = []
        for index, line in enumerate(lines):
            marked = index == self.source_active
            text = _esc(line) or "&nbsp;"
            out.append(f'<span class="on">{text}</span>' if marked else f"{text}\n")
        title = f" &mdash; {_esc(self.source_title)}" if self.source_title else ""
        return f'<div class="sec"><div class="ch">CODE{title}</div><pre>{"".join(out)}</pre></div>'

    def _stats_html(self) -> str:
        return (
            '<div class="sec"><div class="ch">COUNTED</div><div class="stats">'
            f"<span>writes <b>{self.writes}</b></span>"
            f"<span>accepted 2xx <b>{self.accepted}</b></span>"
            f"<span>measured agreed <b>{self.agreed}</b></span>"
            f"<span>measured diverged <b>{self.diverged}</b></span>"
            "</div></div>"
        )

    def _verdicts_html(self) -> str:
        if not self.verdicts:
            return ""
        rows = []
        for verdict in self.verdicts:
            pill = "ok" if verdict.passed else "no"
            label = "PASS" if verdict.passed else "FAIL"
            note = f' <span class="nm">{_esc(verdict.note)}</span>' if verdict.note else ""
            rows.append(
                f'<div class="vrow"><span class="nm">{_esc(verdict.name)}</span>'
                f'<span class="ev">{_esc(verdict.evidence)}</span>'
                f'<span class="pill {pill}">{label}</span>{note}</div>'
            )
        return f'<div class="verd"><div class="ch">VERDICT</div>{"".join(rows)}</div>'

    def render(self) -> None:
        head = (
            f'<div class="hd"><div class="sc">{_esc(self.scene)}</div>'
            f'<div class="said">{_esc(self.said)}</div>'
            f'<div class="why">{_esc(self.why)}</div></div>'
        )
        concl = f'<div class="concl {self.conclusion_kind}">{_esc(self.conclusion)}</div>' if self.conclusion else ""
        html_out = "".join(
            [
                head,
                self._wire_html(),
                self._readings_html(),
                self._source_html(),
                self._stats_html(),
                self._verdicts_html(),
                concl,
            ]
        )
        self._js(_RENDER_JS, {"html": html_out})
