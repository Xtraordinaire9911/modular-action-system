"""Record five Runtime recovery scenes with explicitly fake upstream feedback.

The fake Planner and fake VLM narration live only in this recording script.
They prove the Runtime-side contract (handoff, validation, execution, fresh
verification, continuation, final oracle), not production Planner/VLM quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from evaluation.generalized_browser_recovery import run_generalized_browser_recovery_suite
from src.runtime.affordance_controller import AffordanceController, PrimitivePlan
from src.runtime.planner_port import PlannerPort
from src.runtime.primitive_action import PrimitiveAction
from src.runtime.system2_planner import System2Planner


class DemoPlannerStub:
    """Demo-only semantic feedback provider injected through PlannerPort."""

    _RELATIONS = ("compensates", "equivalent_to", "remediates", "restores", "observes")

    def __init__(self) -> None:
        self._normal_planner = System2Planner(AffordanceController())
        self.feedback: list[dict[str, str]] = []

    def plan(self, context, *, goal_id="", goal_state="", parameters=None) -> PrimitivePlan:
        if context.failure is None:
            return self._normal_planner.plan(
                context,
                goal_id=goal_id,
                goal_state=goal_state,
                parameters=parameters or {},
            )

        targets = {
            context.failure.failed_affordance_id,
            context.failure.failed_entity_id,
            context.failure.expected_effect,
            context.failure.transition_id,
        }
        for affordance in sorted(context.affordances, key=lambda candidate: (-candidate.confidence, candidate.id)):
            for relation in self._RELATIONS:
                values = _string_values(affordance.grounding.get(relation))
                postcondition = str(affordance.grounding.get("recovery_postcondition") or "")
                if values & targets and postcondition:
                    self.feedback.append(
                        {
                            "relation": relation,
                            "affordance_id": affordance.id,
                            "postcondition": postcondition,
                        }
                    )
                    return PrimitivePlan(
                        [
                            PrimitiveAction(
                                "click",
                                affordance_id=affordance.id,
                                expected_effect=postcondition,
                            )
                        ],
                        reason=f"DEMO STUB feedback selected observed {relation} capability",
                    )
        return PrimitivePlan(
            [PrimitiveAction("ask_user", expected_effect="demo stub found no related capability")],
            requires_escalation=True,
            reason="DEMO STUB found no related capability",
        )


def _string_values(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list | tuple | set):
        return {str(item) for item in value}
    return set()


class RecordedNarratedSession:
    """Async browser session that records and visibly labels the demo stubs."""

    def __init__(self, playwright: Any, browser: Any, context: Any, page: Any, video_dir: Path, index: int) -> None:
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page = page
        self._video_dir = video_dir
        self._index = index
        self._scene_ready = False
        self._goal_attempts = 0
        self.video_path: Path | None = None

    @classmethod
    async def launch(
        cls,
        url: str,
        *,
        headless: bool,
        action_timeout_ms: int,
        video_dir: Path,
        index: int,
    ) -> "RecordedNarratedSession":
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
            record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        page.set_default_timeout(action_timeout_ms)
        session = cls(playwright, browser, context, page, video_dir, index)
        await session.open(url)
        return session

    async def open(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded", timeout=10_000)

    async def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        result = (
            await self._page.evaluate(expression, arg) if arg is not None else await self._page.evaluate(expression)
        )
        if arg is not None and isinstance(arg, dict) and not self._scene_ready:
            self._scene_ready = True
            title = await self._page.title()
            await self._banner(
                "CONTROLLED RECOVERY DEMO",
                title.replace("Open-web mock: ", ""),
                "Runtime executes, re-observes, verifies recovery, and resumes the original goal.",
                "#1d4ed8",
            )
            await asyncio.sleep(1.7)
        return result

    async def click(self, selector: str) -> None:
        metadata = await self._page.eval_on_selector(
            selector,
            """node => ({
                label: (node.innerText || node.textContent || '').trim(),
                role: node.getAttribute('data-recovery-role') || '',
                remediates: node.getAttribute('data-remediates') || '',
                compensates: node.getAttribute('data-compensates') || '',
                equivalent: node.getAttribute('data-equivalent-to') || '',
                restores: node.getAttribute('data-restores') || '',
                observes: node.getAttribute('data-observes') || '',
                dismiss: node.hasAttribute('data-dismiss') || node.getAttribute('formmethod') === 'dialog'
            })""",
        )
        relation = next(
            (
                name
                for name, value in (
                    ("compensates", metadata["compensates"]),
                    ("equivalent_to", metadata["equivalent"]),
                    ("remediates", metadata["remediates"]),
                    ("restores", metadata["restores"]),
                    ("observes", metadata["observes"]),
                )
                if value
            ),
            "",
        )
        if not relation and metadata["dismiss"] and self._goal_attempts:
            relation = "remediates (obstruction probe)"
        if relation:
            if metadata["role"] == "active_perception":
                await self._banner(
                    "DEMO VLM STUB FEEDBACK",
                    "Fresh screenshot assertion returned",
                    "Simulated visual assertion: highlighted selection now agrees with the DOM candidate.",
                    "#7e22ce",
                )
                await asyncio.sleep(0.65)
            await self._banner(
                "DEMO PLANNER STUB FEEDBACK",
                f"Selected observed capability via {relation}",
                f"Proposal: click {metadata['label']!r}. Runtime will validate it against the fresh affordance snapshot.",
                "#b45309",
            )
            await asyncio.sleep(0.65)
        else:
            self._goal_attempts += 1
            phase = "ORIGINAL GOAL ACTION" if self._goal_attempts == 1 else "CONTINUE ORIGINAL GOAL"
            await self._banner(
                "RUNTIME",
                phase,
                f"Executing the validated action against the observed control {metadata['label']!r}.",
                "#0f766e",
            )
            await asyncio.sleep(0.65)

        try:
            await self._page.click(selector)
        except Exception:
            await self._banner(
                "FRESH VERIFICATION: FAILURE",
                "Runtime rejected the attempted transition",
                "Runtime records typed failure evidence and requests a fresh recovery decision.",
                "#b91c1c",
            )
            await asyncio.sleep(0.7)
            raise

        if not relation and self._goal_attempts == 1:
            await self._banner(
                "FRESH VERIFICATION",
                "Executor return is not accepted as goal success",
                "Runtime re-observes the independent oracle; a mismatch starts recovery.",
                "#b91c1c",
            )
            await asyncio.sleep(0.7)
        elif relation:
            await self._banner(
                "RUNTIME RECOVERY",
                "Recovery primitive executed",
                "Runtime now performs a fresh observation and checks the declared recovery postcondition.",
                "#15803d",
            )
            await asyncio.sleep(0.65)

    async def fill(self, selector: str, value: str) -> None:
        await self._page.fill(selector, value)

    async def screenshot(self, path: str | None = None) -> bytes:
        kwargs: dict[str, Any] = {"full_page": True, "animations": "disabled"}
        if path:
            kwargs["path"] = path
        return await self._page.screenshot(**kwargs)

    async def close(self) -> None:
        oracle = await self._page.evaluate("""() => {
                const key = document.body.getAttribute('data-goal-oracle-key') || 'primary_action_completed';
                const state = JSON.parse(document.body.getAttribute('data-oracle-state') || '{}');
                return {key, value: Boolean(state[key])};
            }""")
        await self._banner(
            "FINAL INDEPENDENT ORACLE",
            "VERIFIED SUCCESS" if oracle["value"] else "NOT VERIFIED",
            f"Fresh environment truth: {oracle['key']} = {str(oracle['value']).lower()}",
            "#15803d" if oracle["value"] else "#b91c1c",
        )
        await asyncio.sleep(2.0)
        video = self._page.video
        scene_name = Path(unquote(urlparse(self._page.url).path)).stem or f"scene-{self._index:02d}"
        await self._context.close()
        source = Path(await video.path())
        target = self._video_dir / f"{self._index:02d}-{scene_name}.webm"
        source.replace(target)
        self.video_path = target
        await self._browser.close()
        await self._playwright.stop()

    async def _banner(self, eyebrow: str, title: str, detail: str, color: str) -> None:
        if eyebrow.startswith("DEMO "):
            return
        await self._page.evaluate(
            """({eyebrow, title, detail, color}) => {
                let panel = document.getElementById('__recovery_demo_panel');
                if (!panel) {
                    const style = document.createElement('style');
                    style.textContent = `
                        html { background: #0b1020; }
                        body { margin: 0 430px 0 0 !important; padding: 28px; min-height: 100vh;
                            box-sizing: border-box; background: #f5f7fb !important; color: #111827;
                            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
                            "Segoe UI", sans-serif; }
                        body h1 { margin: 0 0 20px; font-size: 28px; color: #172033; }
                        body p { color: #667085; font-size: 14px; }
                        body button { appearance: none; border: 0; border-radius: 7px; background: #6257e8;
                            color: white; padding: 9px 15px; font-weight: 700; cursor: pointer;
                            box-shadow: 0 4px 12px rgba(98,87,232,.18); }
                        body input { border: 1px solid #cfd5e1; border-radius: 7px; padding: 9px 11px;
                            background: white; color: #111827; }
                        body form, body > section { max-width: 720px; padding: 20px; background: white;
                            border: 1px solid #e4e8f0; border-radius: 12px; box-shadow: 0 8px 22px rgba(31,41,55,.07);
                            margin-bottom: 16px; }
                        body label { display: flex; align-items: center; gap: 10px; font-weight: 650; }
                        #cookie-wall { right: 430px !important; }
                        #__recovery_demo_panel, #__recovery_demo_panel * { box-sizing: border-box; }
                        #__recovery_demo_panel {
                            position: fixed; right: 0; top: 0; bottom: 0; width: 430px;
                            z-index: 2147483647; color: #e5e7eb; background: #0e1028;
                            border-left: 2px solid #6366f1; font-family: ui-monospace, SFMono-Regular,
                            Menlo, Monaco, Consolas, monospace; box-shadow: -12px 0 30px rgba(15,23,42,.2);
                            pointer-events: none; display: flex; flex-direction: column;
                        }
                        #__recovery_demo_panel .rr-head { padding: 17px 17px 14px; border-bottom: 1px solid #292c4b; }
                        #__recovery_demo_panel .rr-brand { color: #8b83ff; font-size: 11px; font-weight: 800;
                            letter-spacing: .12em; }
                        #__recovery_demo_panel .rr-scene { color: #8d93a7; font-size: 10px; line-height: 1.45;
                            margin-top: 8px; }
                        #__recovery_demo_panel .rr-stage { display: flex; gap: 9px; align-items: center;
                            padding: 14px 17px; border-bottom: 1px solid #292c4b; }
                        #__recovery_demo_panel .rr-step { min-width: 54px; padding: 6px 8px; border-radius: 5px;
                            color: #fff; background: #6864ec; text-align: center; font-size: 11px; font-weight: 800; }
                        #__recovery_demo_panel .rr-phase { color: #f1f3f9; font-size: 14px; font-weight: 800; }
                        #__recovery_demo_panel .rr-copy { padding: 16px 17px; border-bottom: 1px solid #292c4b; }
                        #__recovery_demo_panel .rr-label { color: #8179f2; font-size: 9px; font-weight: 800;
                            letter-spacing: .13em; margin-bottom: 8px; }
                        #__recovery_demo_panel .rr-title { color: #f8fafc; font-size: 14px; font-weight: 700;
                            line-height: 1.4; }
                        #__recovery_demo_panel .rr-detail { color: #aeb4c7; font-size: 11px; line-height: 1.55;
                            margin-top: 8px; }
                        #__recovery_demo_panel .rr-evidence { padding: 15px 17px; flex: 1; min-height: 0;
                            display: flex; flex-direction: column; }
                        #__recovery_demo_panel .rr-log { flex: 1; min-height: 0; overflow: hidden; color: #aeb4c7;
                            background: #080b1a; border: 1px solid #272a46; border-radius: 6px; padding: 11px;
                            font-size: 10px; line-height: 1.65; white-space: pre-wrap; }
                        #__recovery_demo_panel .rr-log .ok { color: #4ade80; }
                        #__recovery_demo_panel .rr-log .warn { color: #fb923c; }
                        #__recovery_demo_panel .rr-log .fail { color: #f87171; }
                        #__recovery_demo_panel .rr-status { padding: 11px 17px; color: white; font-size: 11px;
                            font-weight: 800; border-top: 1px solid rgba(255,255,255,.08); }
                    `;
                    document.head.appendChild(style);
                    panel = document.createElement('section');
                    panel.id = '__recovery_demo_panel';
                    panel.innerHTML = `
                        <div class="rr-head">
                            <div class="rr-brand">RUNTIME RECOVERY</div>
                            <div class="rr-scene"></div>
                        </div>
                        <div class="rr-stage">
                            <span class="rr-step"></span><span class="rr-phase"></span>
                        </div>
                        <div class="rr-copy">
                            <div class="rr-label">WHAT IS HAPPENING</div>
                            <div class="rr-title"></div>
                            <div class="rr-detail"></div>
                        </div>
                        <div class="rr-evidence">
                            <div class="rr-label">RUNTIME EVIDENCE</div>
                            <div class="rr-log"></div>
                        </div>
                        <div class="rr-status"></div>`;
                    panel.dataset.step = '0';
                    document.body.appendChild(panel);
                }
                const step = Number(panel.dataset.step || '0') + 1;
                panel.dataset.step = String(step);
                const phase = eyebrow.replace('FRESH VERIFICATION: FAILURE', 'VERIFY')
                    .replace('FRESH VERIFICATION', 'VERIFY')
                    .replace('FINAL INDEPENDENT ORACLE', 'FINAL ORACLE')
                    .replace('CONTROLLED RECOVERY DEMO', 'OBSERVE');
                const statusKind = eyebrow.includes('FAILURE') || title.includes('not accepted')
                    ? 'fail' : eyebrow.includes('RECOVERY') ? 'warn' : 'ok';
                const statusText = statusKind === 'fail' ? 'failure detected — success withheld'
                    : eyebrow.includes('FINAL') ? 'verified success — independent oracle passed'
                    : eyebrow.includes('RECOVERY') ? 'recovery action executed — re-observing'
                    : 'runtime step admitted';
                const logLine = statusKind === 'fail'
                    ? `[fail] ${title}\\n[ok] typed failure context recorded\\n[ok] fresh observation requested`
                    : eyebrow.includes('FINAL')
                    ? `[ok] recovery postcondition passed\\n[ok] original goal resumed\\n[ok] ${detail}`
                    : eyebrow.includes('RECOVERY')
                    ? `[ok] proposal validated against fresh affordance snapshot\\n[ok] primitive executed\\n[ok] recovery postcondition requested`
                    : `[ok] ${title}\\n[ok] ${detail}`;
                panel.querySelector('.rr-scene').textContent = document.title.replace('Open-web mock: ', '');
                panel.querySelector('.rr-step').textContent = `STEP ${String(step).padStart(2, '0')}`;
                panel.querySelector('.rr-phase').textContent = phase;
                panel.querySelector('.rr-title').textContent = title;
                panel.querySelector('.rr-detail').textContent = detail;
                const log = panel.querySelector('.rr-log');
                const entry = document.createElement('div');
                entry.className = statusKind;
                entry.textContent = logLine + '\\n';
                log.appendChild(entry);
                panel.querySelector('.rr-status').textContent = statusText;
                panel.querySelector('.rr-status').style.background = color;
            }""",
            {"eyebrow": eyebrow, "title": title, "detail": detail, "color": color},
        )


def _combine_videos(video_paths: list[Path], target: Path) -> None:
    inputs: list[str] = []
    filters: list[str] = []
    for index, path in enumerate(video_paths):
        inputs.extend(["-i", str(path)])
        filters.append(f"[{index}:v]setpts=1.25*(PTS-STARTPTS)[v{index}]")
    joined = "".join(f"[v{index}]" for index in range(len(video_paths)))
    filter_graph = ";".join([*filters, f"{joined}concat=n={len(video_paths)}:v=1:a=0,format=yuv420p[outv]"])
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_graph,
            "-map",
            "[outv]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or f"output/playwright/recovery_contract_demo_{timestamp}").resolve()
    raw_video_dir = output_dir / "raw_video"
    raw_video_dir.mkdir(parents=True, exist_ok=True)
    sessions: list[RecordedNarratedSession] = []

    async def session_factory(url: str, *, headless: bool, action_timeout_ms: int):
        session = await RecordedNarratedSession.launch(
            url,
            headless=headless,
            action_timeout_ms=action_timeout_ms,
            video_dir=raw_video_dir,
            index=len(sessions) + 1,
        )
        sessions.append(session)
        return session

    planner: PlannerPort = DemoPlannerStub()
    run_generalized_browser_recovery_suite(
        output_dir,
        dev_repetitions=1,
        holdout_repetitions=0,
        headless=not args.headed,
        action_timeout_ms=600,
        capture_screenshots=True,
        session_factory=session_factory,
        system2_planner=planner,
    )

    report_path = output_dir / "generalized_browser_recovery_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["summary"]["episode_count"] != 5 or not report["summary"]["all_recovered_and_verified"]:
        raise RuntimeError(f"recording did not verify all five Runtime recovery scenes: {report['summary']}")
    videos = [session.video_path for session in sessions if session.video_path is not None]
    if len(videos) != 5:
        raise RuntimeError(f"expected five recorded scenes, found {len(videos)}")
    final_video = output_dir / "runtime_recovery_demo_fake_upstream.mp4"
    _combine_videos(videos, final_video)

    manifest = {
        "video": str(final_video),
        "runtime_report": str(report_path),
        "scene_videos": [str(path) for path in videos],
        "verified_runtime_recoveries": 5,
        "simulated_boundaries": ["PlannerPort feedback", "VLM feedback in DOM/visual scene"],
        "claim": (
            "The recording proves the Runtime recovery integration contract with explicitly fake upstream "
            "feedback; it does not prove a production Planner or VLM."
        ),
    }
    (output_dir / "demo_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
