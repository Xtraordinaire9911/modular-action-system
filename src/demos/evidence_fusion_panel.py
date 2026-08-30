"""Browser-side flight recorder for the presentation smart-room episode.

The panel is a projection of evidence already owned by Runtime.  It never
decides whether an action may run and it never manufactures a conflict.  The
actual :class:`EpistemicArbiter` remains authoritative; this component only
renders its decision beside the live DOM and WoT environment.
"""

from __future__ import annotations

import asyncio
import html
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.contracts.types import ExecutionResult, Observation, SkillCall
from src.runtime.live_observation import LiveRuntimeObservation
from src.verification.conflict_detector import EpistemicArbiter, FusionDecision


class AsyncEvaluatingSession(Protocol):
    async def evaluate(self, expression: str, arg: Any | None = ...) -> Any: ...


PANEL_WIDTH = 520

_CSS = f"""
#__efp, #__efp * {{ box-sizing:border-box }}
#__efp {{ position:fixed; inset:0 0 0 auto; width:{PANEL_WIDTH}px; height:100vh;
  z-index:2147483646; background:#06182c; color:#f5f8fb;
  border-left:3px solid #0065bd; box-shadow:-12px 0 36px rgba(0,0,0,.24);
  font:13px/1.35 "Segoe UI",Arial,sans-serif; display:flex; flex-direction:column;
  overflow:hidden; letter-spacing:.005em }}
#__efp .ef-head {{ padding:17px 19px 14px; background:#071f38;
  border-bottom:1px solid #24415d }}
#__efp .ef-kicker {{ color:#59cbe8; font:800 10px/1.2 ui-monospace,Consolas,monospace;
  letter-spacing:.16em; text-transform:uppercase }}
#__efp .ef-intent {{ margin-top:7px; font-size:16px; line-height:1.3; font-weight:750 }}
#__efp .ef-meta {{ display:flex; justify-content:space-between; gap:12px; margin-top:9px;
  color:#9eb6ca; font:11px/1.2 ui-monospace,Consolas,monospace }}
#__efp .ef-steps {{ display:grid; grid-template-columns:repeat(5,1fr); gap:5px;
  padding:10px 14px; border-bottom:1px solid #24415d; background:#081b30 }}
#__efp .ef-step {{ padding:5px 2px; border:1px solid #29455e; color:#7792aa;
  text-align:center; font:800 9px/1.2 ui-monospace,Consolas,monospace;
  letter-spacing:.08em }}
#__efp .ef-step.on {{ color:white; background:#0065bd; border-color:#59cbe8 }}
#__efp .ef-body {{ padding:13px 14px; display:flex; flex:1; min-height:0;
  flex-direction:column; gap:11px }}
#__efp .ef-section-label {{ color:#9eb6ca; font:800 9px/1.2 ui-monospace,Consolas,monospace;
  letter-spacing:.15em; text-transform:uppercase; margin-bottom:6px }}
#__efp .ef-sources {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:7px }}
#__efp .ef-source {{ min-height:150px; border:1px solid #2d4d68; background:#0a223b;
  padding:9px 9px 8px; position:relative }}
#__efp .ef-source.dom {{ border-top:3px solid #0065bd }}
#__efp .ef-source.visual {{ border-top:3px solid #59cbe8 }}
#__efp .ef-source.wot {{ border-top:3px solid #62a744 }}
#__efp .ef-source-head {{ display:flex; align-items:center; justify-content:space-between;
  gap:5px; font:800 10px/1.2 ui-monospace,Consolas,monospace; letter-spacing:.08em }}
#__efp .ef-fresh {{ color:#8bd17c; font-size:9px }}
#__efp .ef-row {{ margin-top:8px; padding-top:7px; border-top:1px solid #24415d }}
#__efp .ef-row b {{ display:block; color:#f5f8fb; font-size:11px; line-height:1.25;
  overflow-wrap:anywhere }}
#__efp .ef-row span {{ display:block; margin-top:2px; color:#9eb6ca;
  font:9.5px/1.3 ui-monospace,Consolas,monospace; overflow-wrap:anywhere }}
#__efp .ef-empty {{ color:#68859e; font-size:10px; margin-top:10px }}
#__efp .ef-rail {{ height:18px; position:relative; margin:-2px 9% -1px }}
#__efp .ef-rail:before {{ content:""; position:absolute; left:0; right:0; top:2px;
  border-top:1px solid #4d6b84 }}
#__efp .ef-rail i {{ position:absolute; width:8px; height:8px; top:-2px; border-radius:50%;
  background:#59cbe8; box-shadow:0 0 0 3px rgba(89,203,232,.15) }}
#__efp .ef-rail i:nth-child(1) {{ left:12% }}
#__efp .ef-rail i:nth-child(2) {{ left:49% }}
#__efp .ef-rail i:nth-child(3) {{ right:12% }}
#__efp .ef-rail:after {{ content:""; position:absolute; left:50%; top:2px; height:16px;
  border-left:1px solid #4d6b84 }}
#__efp .ef-fusion {{ border:1px solid #2b5f83; background:#0a2948; padding:11px 12px }}
#__efp .ef-fusion.clean {{ border-left:5px solid #59cbe8 }}
#__efp .ef-fusion.blocked {{ border-left:5px solid #e37222; background:#352115 }}
#__efp .ef-fusion.recovering {{ border-left:5px solid #f5b335; background:#332a15 }}
#__efp .ef-fusion.verified {{ border-left:5px solid #62a744; background:#17301e }}
#__efp .ef-verdict {{ font-size:15px; font-weight:800; letter-spacing:.015em }}
#__efp .ef-detail {{ margin-top:4px; color:#bdd0df; font-size:11px; line-height:1.4 }}
#__efp .ef-conflict {{ margin-top:7px; color:#ffc58f;
  font:10px/1.35 ui-monospace,Consolas,monospace }}
#__efp .ef-next {{ border:1px solid #36546d; padding:10px 12px; background:#081c31 }}
#__efp .ef-next b {{ display:block; margin-top:3px; color:white; font-size:14px }}
#__efp .ef-next span {{ color:#8fa9be; font-size:10px }}
#__efp .ef-events {{ margin-top:auto; min-height:0 }}
#__efp .ef-event {{ display:grid; grid-template-columns:58px 1fr; gap:7px; padding:5px 0;
  border-top:1px solid #203c55; color:#9eb6ca; font-size:10px }}
#__efp .ef-event code {{ color:#59cbe8; font:9px/1.3 ui-monospace,Consolas,monospace }}
#__efp .ef-foot {{ padding:9px 14px; background:#04111f; border-top:1px solid #24415d;
  display:flex; justify-content:space-between; color:#7893aa;
  font:9px/1.2 ui-monospace,Consolas,monospace }}
body {{ margin-right:{PANEL_WIDTH}px !important }}
@media (prefers-reduced-motion:no-preference) {{
  #__efp .ef-fusion.blocked {{ animation:ef-pulse .7s ease-out 1 }}
  @keyframes ef-pulse {{ 0%{{box-shadow:0 0 0 0 rgba(227,114,34,.65)}}
    100%{{box-shadow:0 0 0 12px rgba(227,114,34,0)}} }}
}}
"""

_OPEN_JS = """({css}) => {
  let style = document.getElementById('__efp_css');
  if (!style) { style=document.createElement('style'); style.id='__efp_css';
    style.textContent=css; document.head.appendChild(style); }
  let panel=document.getElementById('__efp');
  if (!panel) { panel=document.createElement('aside'); panel.id='__efp';
    panel.setAttribute('data-runtime-overlay','true'); document.body.appendChild(panel); }
  return true;
}"""

_RENDER_JS = """({html}) => {
  const panel=document.getElementById('__efp'); if(!panel) return false;
  panel.innerHTML=html; return true;
}"""

_CLOSE_JS = """() => {
  for (const id of ['__efp','__efp_css']) { const node=document.getElementById(id); if(node) node.remove(); }
  document.body.style.marginRight=''; return true;
}"""


@dataclass(frozen=True)
class EvidenceRow:
    headline: str
    detail: str


@dataclass
class PanelEvent:
    at_ms: int
    phase: str
    message: str


@dataclass
class EvidenceFusionPanel:
    session: AsyncEvaluatingSession
    intent: str
    episode_id: str = "pending"
    phase: str = "OBSERVE"
    dom: list[EvidenceRow] = field(default_factory=list)
    visual: list[EvidenceRow] = field(default_factory=list)
    wot: list[EvidenceRow] = field(default_factory=list)
    verdict_kind: str = "clean"
    verdict: str = "Waiting for the first source-labelled observation"
    detail: str = "Runtime has not yet admitted evidence into the episode state."
    conflict: str = ""
    next_primitive: str = "—"
    events: list[PanelEvent] = field(default_factory=list)
    opened: bool = False
    recovery_pending: bool = False
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)

    async def open(self) -> bool:
        self.opened = bool(await self._evaluate(_OPEN_JS, {"css": _CSS}))
        if self.opened:
            await self.render()
        return self.opened

    async def close(self) -> None:
        await self.flush()
        await self._evaluate(_CLOSE_JS)
        self.opened = False

    def begin_episode(self, episode_id: str) -> None:
        if episode_id:
            self.episode_id = episode_id

    async def show_observation(self, observed: LiveRuntimeObservation | Observation, reason: str) -> None:
        live = observed if isinstance(observed, LiveRuntimeObservation) else None
        observation = live.observation if live is not None else observed
        self.phase = "OBSERVE"
        self.dom, self.visual, self.wot = _evidence_rows(observation, live)
        self._event("OBSERVE", f"fresh snapshot · {reason}")
        await self._ensure_open()
        await self.render()

    def capture_fusion(self, decision: FusionDecision) -> None:
        self.phase = "FUSE"
        if decision.allow_system1:
            self.verdict_kind = "clean"
            self.verdict = "FUSED · no blocking contradiction"
            self.detail = _fused_detail(decision)
            self.conflict = ""
        else:
            self.verdict_kind = "blocked"
            self.verdict = "FUSION BLOCKED · active perception required"
            self.detail = decision.reason
            self.conflict = _conflict_detail(decision)
        self._event("FUSE", self.verdict)
        self._schedule_render()

    async def show_action(self, call: SkillCall) -> None:
        self.phase = "CHOOSE"
        prefix = "same Agent remediation" if self.recovery_pending else "semantic primitive"
        self.next_primitive = f"{call.skill_id} · {prefix}"
        self._event("CHOOSE", call.skill_id)
        await self._ensure_open()
        await self.render()

    async def show_execution(self, result: ExecutionResult) -> None:
        self.phase = "EXECUTE"
        if result.success:
            self._event("EXECUTE", f"{result.backend_used} accepted · awaiting fresh verification")
            self.detail = "Executor acknowledgement is provisional; Runtime re-observes the environment."
        else:
            self.recovery_pending = True
            self.verdict_kind = "recovering"
            obstruction = "obstruction" in (result.failure_reason or "").casefold()
            self.verdict = "TYPED OBSTRUCTION" if obstruction else "TYPED EXECUTION FAILURE"
            self.detail = result.failure_reason or "The executor reported a typed failure."
            self.conflict = "This is a recovery input, not a fabricated fusion conflict."
            self._event("EXECUTE", self.verdict)
        await self.render()

    async def show_final(self, *, verified: bool, detail: str) -> None:
        if not verified and self.verdict.startswith("FUSION BLOCKED"):
            # Preserve the causal verdict long enough for the audience to see
            # it. "Not verified" is the consequence, not a replacement cause.
            self.phase = "FUSE"
            self.detail = f"{self.detail} · terminal oracle: not verified"
            self.next_primitive = "active perception / bounded handover"
            self._event("VERIFY", "terminal oracle withheld success")
            await self._ensure_open()
            await self.render()
            return
        self.phase = "VERIFY"
        self.verdict_kind = "verified" if verified else "blocked"
        self.verdict = "TERMINAL ORACLE · VERIFIED" if verified else "TERMINAL ORACLE · NOT VERIFIED"
        self.detail = detail
        self.conflict = ""
        self.next_primitive = "episode complete" if verified else "handover / bounded failure"
        self._event("VERIFY", self.verdict)
        await self._ensure_open()
        await self.render()

    async def render(self) -> bool:
        if not self.opened:
            return False
        return bool(await self._evaluate(_RENDER_JS, {"html": self.html()}))

    async def flush(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def html(self) -> str:
        steps = ["OBSERVE", "FUSE", "CHOOSE", "EXECUTE", "VERIFY"]
        step_html = "".join(f'<div class="ef-step{" on" if step == self.phase else ""}">{step}</div>' for step in steps)
        sources = "".join(
            _source_card(name, css, rows)
            for name, css, rows in (
                ("DOM", "dom", self.dom),
                ("VISUAL / GEOMETRY", "visual", self.visual),
                ("WoT", "wot", self.wot),
            )
        )
        events = "".join(
            f'<div class="ef-event"><code>{_esc(event.phase)}</code><span>{_esc(event.message)}</span></div>'
            for event in self.events[-4:]
        )
        conflict = f'<div class="ef-conflict">{_esc(self.conflict)}</div>' if self.conflict else ""
        return (
            '<div class="ef-head">'
            '<div class="ef-kicker">Evidence flight recorder</div>'
            f'<div class="ef-intent">{_esc(self.intent)}</div>'
            f'<div class="ef-meta"><span>episode {_esc(self.episode_id)}</span><span>source-labelled</span></div>'
            "</div>"
            f'<div class="ef-steps">{step_html}</div>'
            '<div class="ef-body">'
            '<div><div class="ef-section-label">Fresh observations</div>'
            f'<div class="ef-sources">{sources}</div></div>'
            '<div class="ef-rail"><i></i><i></i><i></i></div>'
            f'<div class="ef-fusion {self.verdict_kind}"><div class="ef-section-label">Runtime verdict</div>'
            f'<div class="ef-verdict">{_esc(self.verdict)}</div><div class="ef-detail">{_esc(self.detail)}</div>'
            f"{conflict}</div>"
            '<div class="ef-next"><span>NEXT PRIMITIVE</span>'
            f"<b>{_esc(self.next_primitive)}</b></div>"
            f'<div class="ef-events"><div class="ef-section-label">Episode trace</div>{events}</div>'
            "</div>"
            '<div class="ef-foot"><span>DOM · rendered geometry · W3C WoT</span>'
            "<span>projection only — Runtime owns truth</span></div>"
        )

    def write_trace(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "episode_id": self.episode_id,
            "intent": self.intent,
            "events": [asdict(event) for event in self.events],
            "terminal": {
                "verdict": self.verdict,
                "detail": self.detail,
                "next_primitive": self.next_primitive,
            },
        }
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return target

    async def _ensure_open(self) -> None:
        # Isolation can recreate the browser page inside one episode. Reapply
        # this idempotent projection so it follows Runtime across that boundary.
        self.opened = bool(await self._evaluate(_OPEN_JS, {"css": _CSS}))

    async def _evaluate(self, expression: str, arg: Any | None = None) -> Any:
        try:
            return await self.session.evaluate(expression, arg)
        except Exception:
            return None

    def _schedule_render(self) -> None:
        try:
            task = asyncio.create_task(self._ensure_and_render())
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _ensure_and_render(self) -> None:
        await self._ensure_open()
        await self.render()

    def _event(self, phase: str, message: str) -> None:
        self.events.append(PanelEvent(at_ms=int(time.time() * 1000), phase=phase, message=message))


class PresentationEpistemicArbiter(EpistemicArbiter):
    """The production arbiter with a read-only presentation subscriber."""

    def __init__(self, panel: EvidenceFusionPanel, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.panel = panel

    def fuse(self, cognitive_map: Any) -> FusionDecision:
        decision = super().fuse(cognitive_map)
        self.panel.capture_fusion(decision)
        return decision


def _evidence_rows(
    observation: Observation,
    live: LiveRuntimeObservation | None,
) -> tuple[list[EvidenceRow], list[EvidenceRow], list[EvidenceRow]]:
    dom: list[EvidenceRow] = []
    visual: list[EvidenceRow] = []
    wot: list[EvidenceRow] = []
    for assertion in observation.assertions:
        row = EvidenceRow(
            f"{assertion.entity_id}.{assertion.attribute} = {_short(assertion.value)}",
            f"conf {float(assertion.confidence or 0):.2f} · fresh",
        )
        if assertion.entity_id == "interaction_obstruction":
            visual.append(
                EvidenceRow(
                    f"target obstructed = {_short(assertion.value)}",
                    "rendered hit-test · browser geometry",
                )
            )
        elif assertion.source == "wot":
            wot.append(row)
        elif assertion.source == "visual":
            visual.append(row)
        elif assertion.source == "dom":
            dom.append(row)
    if live is not None:
        provenance = live.provenance
        dom.insert(
            0,
            EvidenceRow(
                f"{int(provenance.get('dom_affordance_count', 0))} live affordances",
                f"capture {live.captured_at_ms}",
            ),
        )
        wot.insert(
            0,
            EvidenceRow(
                f"{int(provenance.get('wot_affordance_count', 0))} TD affordances",
                "discovered at runtime",
            ),
        )
    visual.insert(
        0,
        EvidenceRow(
            f"screenshot {len(observation.screenshot)} bytes",
            "exact rendered frame retained",
        ),
    )
    return dom[:3], visual[:3], wot[:3]


def _fused_detail(decision: FusionDecision) -> str:
    if not decision.fused_states:
        return decision.reason
    states = ", ".join(
        f"{state.entity_id}.{state.attribute}={_short(state.value)}" for state in decision.fused_states[:3]
    )
    return f"{decision.reason}; selected {states}"


def _conflict_detail(decision: FusionDecision) -> str:
    if not decision.conflicts:
        return ""
    conflict = max(decision.conflicts, key=lambda item: item.conflict_mass)
    values = ", ".join(f"{key}={_short(value)}" for key, value in conflict.values.items())
    return f"{conflict.entity_id}.{conflict.attribute}: {values} · mass {conflict.conflict_mass:.2f}"


def _source_card(name: str, css: str, rows: list[EvidenceRow]) -> str:
    content = "".join(
        f'<div class="ef-row"><b>{_esc(row.headline)}</b><span>{_esc(row.detail)}</span></div>' for row in rows
    )
    if not content:
        content = '<div class="ef-empty">No assertion admitted in this snapshot.</div>'
    freshness = "FRESH" if rows else "WAIT"
    return (
        f'<div class="ef-source {css}"><div class="ef-source-head"><span>{_esc(name)}</span>'
        f'<span class="ef-fresh">{freshness}</span></div>{content}</div>'
    )


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _short(value: Any, limit: int = 24) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["EvidenceFusionPanel", "EvidenceRow", "PanelEvent", "PresentationEpistemicArbiter"]
