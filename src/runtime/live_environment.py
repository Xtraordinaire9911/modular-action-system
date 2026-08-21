"""Typed adapters between live browser/WoT surfaces and runtime control.

The adapters in this module deliberately do not parse user intent or implement
environment-specific planners.  They turn already-owned perception and
effector components into the two runtime protocols needed by CIM:

* a fresh ``ObservationProvider`` with source-attributed assertions; and
* executors that resolve the planner's affordance id at execution time.

Environment semantics are supplied as declarative bindings.  This keeps raw
selectors and deployment URLs outside the planner while allowing a real demo
to state which field binds which structured parameter and which observation is
the independent success oracle.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from src.contracts.types import Affordance, ExecutionResult, Observation, ObservedAssertion, SkillCall
from src.perception.browser_session import BrowserSession
from src.perception.td_affordance_parser import ThingAffordanceModel, parse_things
from src.runtime.episode import ObservationRequest
from src.runtime.live_observation import LiveRuntimeObservation

ProbeExtraction = Literal["text", "value"]
ProbeValueType = Literal["string", "number", "integer", "boolean"]


@dataclass(frozen=True)
class LiveEnvironmentConfig:
    dashboard_url: str = "http://127.0.0.1:3000"
    thing_directory_url: str = "http://127.0.0.1:8082/things"
    wot_public_base_url: str = "http://127.0.0.1:8080"
    control_url: str = "http://127.0.0.1:8081"
    request_timeout_s: float = 2.0
    settle_after_action_s: float = 0.0
    output_dir: Path = Path("artifacts/live_runtime_demo")


@dataclass(frozen=True)
class DomStateProbe:
    """Declarative DOM-to-state extraction used as an independent oracle."""

    selector: str
    entity_id: str
    attribute: str
    extraction: ProbeExtraction = "text"
    value_type: ProbeValueType = "string"
    true_pattern: str = ""
    false_pattern: str = ""
    confidence: float = 0.95


@dataclass(frozen=True)
class AffordanceSemanticBinding:
    """Attach task semantics to a discovered affordance without changing its grounding."""

    source: Literal["DOM", "WOT", "VISUAL"]
    entity_id: str = ""
    state_attribute: str = ""
    # Optional semantic identity for a separately read WoT property. Actions
    # such as ``setBrightness`` are verified from the ``brightness`` state
    # source, so their names need not be identical.
    state_source_property: str = ""
    affordance_id: str = ""
    selector: str = ""
    thing_id: str = ""
    label: str = ""
    binds_parameter: str = ""
    completion_for: str = ""
    achieves: str = ""
    stable_key: str = ""
    idempotent: bool = False
    skill_id: str = ""
    safety_level: Literal["low", "medium", "high"] | None = None

    def matches(self, affordance: Affordance) -> bool:
        if affordance.source != self.source:
            return False
        if self.affordance_id and affordance.id != self.affordance_id:
            return False
        if self.selector and affordance.locator.get("selector") != self.selector:
            return False
        if self.thing_id and affordance.locator.get("thing_id") != self.thing_id:
            return False
        if self.label and affordance.label != self.label:
            return False
        return bool(self.affordance_id or self.selector or self.thing_id or self.label)


@dataclass(frozen=True)
class SkillActionBinding:
    """Resolve a durable skill call to one live affordance and payload."""

    skill_id: str
    source: Literal["DOM", "WOT", "VISUAL"]
    affordance_id: str = ""
    thing_id: str = ""
    label: str = ""
    parameter: str = ""
    constant: Any = None


class ContractAffordanceEffector(Protocol):
    def execute(
        self,
        target: Affordance | SkillCall,
        observation: Observation | None = None,
        *,
        value: Any | None = None,
        skill_id: str = "",
    ) -> ExecutionResult | Any: ...


class LiveEnvironmentError(RuntimeError):
    pass


class ThreadedBrowserSession:
    """Serialize every sync Playwright operation on one dedicated thread."""

    def __init__(self, url: str, *, headless: bool = True, action_timeout_ms: int = 8000) -> None:
        self.url = url
        self.headless = headless
        self.action_timeout_ms = action_timeout_ms
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-browser")
        self._session: BrowserSession | None = None
        self._closed = False
        self.context_generation = 0

    async def start(self) -> None:
        if self._closed:
            raise LiveEnvironmentError("browser session has been closed")
        if self._session is not None:
            return

        def launch() -> BrowserSession:
            return BrowserSession.launch(
                self.url,
                headless=self.headless,
                action_timeout_ms=self.action_timeout_ms,
            )

        self._session = await self._submit(launch)
        self.context_generation += 1

    async def recreate(self) -> None:
        """Replace the current BrowserContext while keeping adapter references stable."""

        await self.stop()
        await self.start()

    async def stop(self) -> None:
        """Close only the current context; the worker remains reusable."""

        if self._session is not None:
            await self._session_call("close")
            self._session = None

    async def open(self, url: str) -> None:
        await self._session_call("open", url)

    async def state(self, *, page_id: str, captured_at_ms: int) -> Any:
        return await self._session_call("state", page_id=page_id, captured_at_ms=captured_at_ms)

    async def screenshot(self, path: str | None = None) -> bytes:
        return await self._session_call("screenshot", path)

    async def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        return await self._session_call("evaluate", expression, arg)

    async def execute_dom(self, affordance: Affordance, *, value: Any, skill_id: str) -> ExecutionResult:
        from src.effectors.dom_executor import DomExecutor

        def execute() -> ExecutionResult:
            session = self._require_session()
            produced = DomExecutor(session).execute(affordance, value=value, skill_id=skill_id)
            if not isinstance(produced, ExecutionResult):
                raise TypeError(f"DomExecutor returned {type(produced).__name__}")
            return produced

        return await self._submit(execute)

    async def close(self) -> None:
        if self._closed:
            return
        await self.stop()
        self._worker.shutdown(wait=True, cancel_futures=True)
        self._closed = True

    async def _session_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        def call() -> Any:
            target = getattr(self._require_session(), method)
            return target(*args, **kwargs)

        return await self._submit(call)

    async def _submit(self, function: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._worker, function)

    def _require_session(self) -> BrowserSession:
        if self._session is None:
            raise LiveEnvironmentError("browser session has not been started")
        return self._session


class ThreadedDomEffector:
    def __init__(self, session: ThreadedBrowserSession) -> None:
        self.session = session

    async def execute(
        self,
        target: Affordance | SkillCall,
        observation: Observation | None = None,
        *,
        value: Any | None = None,
        skill_id: str = "",
    ) -> ExecutionResult:
        _ = observation
        if not isinstance(target, Affordance):
            raise TypeError("ThreadedDomEffector requires a resolved Affordance")
        return await self.session.execute_dom(target, value=value, skill_id=skill_id)


class SmartRoomLiveEnvironment:
    """Own one browser session and fresh typed observations of a live smart room."""

    def __init__(
        self,
        session: ThreadedBrowserSession,
        config: LiveEnvironmentConfig,
        *,
        dom_state_probes: list[DomStateProbe] | None = None,
        semantic_bindings: list[AffordanceSemanticBinding] | None = None,
        include_wot_state: bool = True,
        allowed_affordance_sources: set[str] | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.dom_state_probes = list(dom_state_probes or [])
        self.semantic_bindings = list(semantic_bindings or [])
        self.include_wot_state = include_wot_state
        self.allowed_affordance_sources = (
            {source.upper() for source in allowed_affordance_sources}
            if allowed_affordance_sources is not None
            else None
        )
        self.tds: list[dict[str, Any]] = []
        self.thing_models: list[ThingAffordanceModel] = []
        self.latest_affordances: dict[str, Affordance] = {}
        self._observation_index = 0
        self._episode_id = "preflight"

    def begin_episode(self, episode_id: str) -> None:
        """Clear per-episode perception caches after a new context is provisioned."""

        self._episode_id = episode_id
        self.latest_affordances.clear()
        self._observation_index = 0

    async def initialize(self) -> None:
        """Discover live TDs and rewrite container-local forms for the host runtime."""

        try:
            async with httpx.AsyncClient(timeout=self.config.request_timeout_s) as client:
                response = await client.get(self.config.thing_directory_url)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise LiveEnvironmentError(
                f"Thing Directory unavailable: {self.config.thing_directory_url}: {exc}"
            ) from exc
        if not isinstance(payload, list) or not payload:
            raise LiveEnvironmentError("Thing Directory returned no Thing Descriptions")
        self.tds = [_prepare_runtime_td(td, self.config.wot_public_base_url) for td in payload if isinstance(td, dict)]
        self.thing_models = parse_things(self.tds)
        if not self.thing_models:
            raise LiveEnvironmentError("no valid Thing Description could be parsed")

    async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation:
        if not self.thing_models:
            await self.initialize()
        if request.previous_result is not None and self.config.settle_after_action_s > 0:
            await asyncio.sleep(self.config.settle_after_action_s)

        self._observation_index += 1
        captured_at_ms = int(time.time() * 1000)
        page = await self.session.state(page_id=request.task_id, captured_at_ms=captured_at_ms)
        page_affordances = [self._annotate(affordance) for affordance in page.affordances]
        wot_affordances = [
            self._annotate(affordance) for model in self.thing_models for affordance in model.affordances
        ]
        affordances = [*page_affordances, *wot_affordances]
        if self.allowed_affordance_sources is not None:
            affordances = [
                affordance for affordance in affordances if affordance.source in self.allowed_affordance_sources
            ]
        self.latest_affordances = {affordance.id: affordance for affordance in affordances}

        episode_id = request.episode_id or self._episode_id
        screenshot_dir = self.config.output_dir / "screenshots" / request.task_id / episode_id
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / f"{self._observation_index:03d}_{request.reason}.png"
        screenshot = await self.session.screenshot(str(screenshot_path))

        assertions = await self._read_dom_assertions(captured_at_ms)
        device_states: dict[str, Any] = {}
        if self.include_wot_state:
            wot_assertions, device_states = await self._read_wot_assertions(captured_at_ms)
            assertions.extend(wot_assertions)

        observation = Observation(
            screenshot=screenshot,
            dom_tree=None,
            accessibility_tree={
                "page_state": {
                    "page": {
                        "url": page.url,
                        "page_id": page.page_id,
                        "affordance_count": len(page.affordances),
                    }
                }
            },
            wot_tds=self.tds,
            device_states=device_states,
            assertions=assertions,
        )
        return LiveRuntimeObservation(
            observation=observation,
            affordances=affordances,
            provenance={
                "capture": "live",
                "request_reason": request.reason,
                "step": request.step,
                "screenshot_path": str(screenshot_path),
                "dom_affordance_count": len(page_affordances),
                "wot_affordance_count": len(wot_affordances),
            },
            complete_affordance_snapshot=True,
            response_to_request_id=request.request_id,
            captured_at_ms=captured_at_ms,
        )

    def find_affordance(self, affordance_id: str) -> Affordance | None:
        return self.latest_affordances.get(affordance_id)

    def find_skill_affordance(self, binding: SkillActionBinding) -> Affordance | None:
        candidates = [
            affordance for affordance in self.latest_affordances.values() if affordance.source == binding.source
        ]
        for affordance in candidates:
            if binding.affordance_id and affordance.id != binding.affordance_id:
                continue
            if binding.thing_id and affordance.locator.get("thing_id") != binding.thing_id:
                continue
            if binding.label and affordance.label != binding.label:
                continue
            if binding.affordance_id or binding.thing_id or binding.label:
                return affordance
        return None

    def _annotate(self, affordance: Affordance) -> Affordance:
        locator = dict(affordance.locator)
        safety_level = affordance.safety_level
        for binding in self.semantic_bindings:
            if not binding.matches(affordance):
                continue
            if binding.entity_id:
                locator["entity_id"] = binding.entity_id
            if binding.state_attribute:
                locator["state_attribute"] = binding.state_attribute
            if binding.binds_parameter:
                locator["binds_parameter"] = binding.binds_parameter
            if binding.completion_for:
                locator["completion_for"] = binding.completion_for
            if binding.achieves:
                locator["achieves"] = binding.achieves
            if binding.stable_key:
                locator["stable_key"] = binding.stable_key
            if binding.idempotent:
                locator["idempotent"] = True
            if binding.skill_id:
                locator["skill_id"] = binding.skill_id
            if binding.safety_level is not None:
                safety_level = binding.safety_level
        return replace(affordance, locator=locator, safety_level=safety_level)

    async def _read_dom_assertions(self, captured_at_ms: int) -> list[ObservedAssertion]:
        assertions: list[ObservedAssertion] = []
        for probe in self.dom_state_probes:
            raw = await self.session.evaluate(
                """({selector, extraction}) => {
                    const node = document.querySelector(selector);
                    if (!node) return null;
                    return extraction === 'value' ? node.value : node.textContent;
                }""",
                {"selector": probe.selector, "extraction": probe.extraction},
            )
            if raw is None:
                continue
            try:
                value = _parse_probe_value(str(raw), probe)
            except ValueError:
                continue
            assertions.append(
                ObservedAssertion(
                    entity_id=probe.entity_id,
                    attribute=probe.attribute,
                    value=value,
                    source="dom",
                    confidence=probe.confidence,
                    timestamp_ms=captured_at_ms,
                    provenance={
                        "adapter": "dom_state_probe",
                        "selector": probe.selector,
                        "extraction": probe.extraction,
                    },
                )
            )
        return assertions

    async def _read_wot_assertions(self, captured_at_ms: int) -> tuple[list[ObservedAssertion], dict[str, Any]]:
        sources = [source for model in self.thing_models for source in model.state_sources]

        async def read(source: Any) -> tuple[Any, Any | None, str | None]:
            try:
                async with httpx.AsyncClient(timeout=self.config.request_timeout_s) as client:
                    response = await client.request(source.method, source.href)
                    response.raise_for_status()
                    return source, response.json(), None
            except Exception as exc:
                return source, None, f"{type(exc).__name__}: {exc}"

        assertions: list[ObservedAssertion] = []
        device_states: dict[str, Any] = {}
        for source, value, error in await asyncio.gather(*(read(source) for source in sources)):
            if error is not None:
                continue
            device_states.setdefault(source.thing_id, {})[source.property] = value
            entity_id = source.thing_id
            attribute = source.property
            for binding in self.semantic_bindings:
                if binding.source != "WOT" or binding.state_source_property != source.property:
                    continue
                if binding.thing_id and binding.thing_id != source.thing_id:
                    continue
                entity_id = binding.entity_id or entity_id
                attribute = binding.state_attribute or attribute
                break
            assertions.append(
                ObservedAssertion(
                    entity_id=entity_id,
                    attribute=attribute,
                    value=value,
                    source="wot",
                    confidence=1.0,
                    timestamp_ms=captured_at_ms,
                    provenance={
                        "adapter": "td_state_source",
                        "href": source.href,
                        "method": source.method,
                    },
                )
            )
        return assertions, device_states


class RuntimeAffordanceExecutor:
    """Execute the currently observed affordance selected by the runtime planner."""

    def __init__(
        self,
        backend: str,
        environment: SmartRoomLiveEnvironment,
        effector: ContractAffordanceEffector,
        *,
        skill_bindings: list[SkillActionBinding] | None = None,
    ) -> None:
        self.backend = backend
        self.environment = environment
        self.effector = effector
        self.skill_bindings = {binding.skill_id: binding for binding in (skill_bindings or [])}

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        affordance_id = str(skill_call.params.get("affordance_id", ""))
        affordance = self.environment.find_affordance(affordance_id) if affordance_id else None
        binding = self.skill_bindings.get(skill_call.skill_id)
        if affordance is None and binding is not None:
            affordance = self.environment.find_skill_affordance(binding)
        if affordance is None:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=self.backend,
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason="live_affordance_not_found",
                observation_source=_observation_source(self.backend),
                metadata={"affordance_id": affordance_id},
            )
        value = skill_call.params.get("value")
        if binding is not None:
            value = (
                skill_call.params.get(binding.parameter, binding.constant) if binding.parameter else binding.constant
            )
        produced = self.effector.execute(affordance, observation, value=value, skill_id=skill_call.skill_id)
        result = await produced if inspect.isawaitable(produced) else produced
        if not isinstance(result, ExecutionResult):
            raise TypeError(f"{type(self.effector).__name__}.execute returned {type(result).__name__}")
        result.backend_used = self.backend
        result.observation_source = _observation_source(self.backend)
        result.metadata = {
            **result.metadata,
            "affordance_id": affordance.id,
            "affordance_source": affordance.source,
            "live_execution": True,
        }
        return result


class SmartRoomControlClient:
    def __init__(self, control_url: str, *, timeout_s: float = 2.0) -> None:
        self.control_url = control_url.rstrip("/")
        self.timeout_s = timeout_s
        self._lease_id = ""

    @property
    def lease_id(self) -> str:
        return self._lease_id

    async def acquire_lease(self, episode_id: str) -> dict[str, Any] | None:
        """Atomically checkpoint and reset the server, or return ``None`` while busy."""

        if self._lease_id:
            raise LiveEnvironmentError("this control client already holds an episode lease")
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(
                f"{self.control_url}/lease/acquire",
                json={"episode_id": episode_id},
            )
            if response.status_code == 409:
                return None
            response.raise_for_status()
            payload = response.json()
        lease_id = payload.get("lease_id") if isinstance(payload, dict) else None
        checkpoint = payload.get("checkpoint") if isinstance(payload, dict) else None
        if not isinstance(lease_id, str) or not lease_id or not isinstance(checkpoint, dict):
            raise LiveEnvironmentError("control plane returned an invalid episode lease")
        self._lease_id = lease_id
        return copy.deepcopy(payload)

    async def restore_lease(self) -> dict[str, Any]:
        """Restore the server-held baseline without releasing this client's lease."""

        self._require_lease()
        return await self._post("/lease/restore", None)

    async def release_lease(self) -> dict[str, Any]:
        """Restore the server-held baseline and release this client's lease."""

        self._require_lease()
        lease_id = self._lease_id
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(
                f"{self.control_url}/lease/release",
                json=None,
                headers={"X-Episode-Lease": lease_id},
            )
            if response.status_code == 409:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("error") in {
                    "no episode lease is active",
                    "stale episode lease",
                }:
                    self._lease_id = ""
                    return {"status": "already_released", **payload}
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "released":
            raise LiveEnvironmentError("control plane returned an invalid lease release")
        if self._lease_id == lease_id:
            self._lease_id = ""
        return payload

    async def reset(self) -> dict[str, Any]:
        return await self._post("/reset", None)

    async def checkpoint(self) -> dict[str, Any]:
        """Capture an independent copy of the complete simulated WoT state."""

        return copy.deepcopy(await self.state())

    async def restore(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        """Restore a checkpoint previously returned by :meth:`checkpoint`."""

        if not isinstance(checkpoint, dict):
            raise TypeError("checkpoint must be a dictionary")
        return await self._post("/restore", copy.deepcopy(checkpoint))

    async def inject(
        self,
        thing: str,
        failure_type: str,
        *,
        delay_ms: int = 0,
        read_delay_ms: int | None = None,
        drop_probability: float | None = None,
        source_reliability: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"thing": thing, "type": failure_type, "delay_ms": delay_ms}
        if read_delay_ms is not None:
            payload["read_delay_ms"] = read_delay_ms
        if drop_probability is not None:
            payload["drop_probability"] = drop_probability
        if source_reliability is not None:
            payload["source_reliability"] = source_reliability
        return await self._post(
            "/failure",
            payload,
        )

    async def clear(self, thing: str) -> dict[str, Any]:
        return await self._post("/failure", {"thing": thing, "clear": True})

    async def state(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.get(f"{self.control_url}/state")
            response.raise_for_status()
            return response.json()

    async def _post(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            kwargs: dict[str, Any] = {"json": payload}
            if self._lease_id:
                kwargs["headers"] = {"X-Episode-Lease": self._lease_id}
            response = await client.post(f"{self.control_url}{path}", **kwargs)
            response.raise_for_status()
            return response.json()

    def _require_lease(self) -> None:
        if not self._lease_id:
            raise LiveEnvironmentError("this control client does not hold an episode lease")


class FaultClearingExecutor:
    """Explicit checkpoint-restore adapter used only for rollback execution."""

    def __init__(
        self,
        backend: str,
        control: SmartRoomControlClient,
        thing: str,
        delegate: RuntimeAffordanceExecutor,
    ) -> None:
        self.backend = backend
        self.control = control
        self.thing = thing
        self.delegate = delegate

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        await self.control.clear(self.thing)
        result = await self.delegate.execute(skill_call, observation)
        result.backend_used = self.backend
        result.observation_source = "wot"
        result.metadata = {**result.metadata, "checkpoint_restore": True, "cleared_fault_for": self.thing}
        return result


class LiveActivePerceptionProbe:
    """Take a fresh multi-source scan; optionally clear an injected DOM-only fault."""

    def __init__(
        self,
        environment: SmartRoomLiveEnvironment,
        *,
        clear_dom_faults: bool = False,
        settle_s: float = 1.7,
    ) -> None:
        self.environment = environment
        self.clear_dom_faults = clear_dom_faults
        self.settle_s = settle_s
        self.attempts = 0

    async def observe(self, conflicts: list[Any], cognitive_map: Any, original_observation: Observation) -> Observation:
        _ = (conflicts, original_observation)
        self.attempts += 1
        if self.clear_dom_faults:
            await self.environment.session.evaluate("window.__clearFaults && window.__clearFaults()")
            await asyncio.sleep(self.settle_s)
        live = await self.environment.observe(
            ObservationRequest(
                task_id=cognitive_map.task_id,
                episode_id=f"active-perception-{self.attempts}",
                reason="active_perception",
                step=self.attempts,
            )
        )
        live.apply_affordances_to(cognitive_map)
        return live.observation


def _rewrite_td_forms(td: dict[str, Any], public_base_url: str) -> dict[str, Any]:
    rewritten = copy.deepcopy(td)
    target = urlsplit(public_base_url)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            href = value.get("href")
            if isinstance(href, str) and href.startswith(("http://", "https://")):
                parsed = urlsplit(href)
                value["href"] = urlunsplit((target.scheme, target.netloc, parsed.path, parsed.query, ""))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(rewritten)
    return rewritten


def _prepare_runtime_td(td: dict[str, Any], public_base_url: str) -> dict[str, Any]:
    """Create a deployment-reachable TD with a stable cross-channel entity id.

    node-wot generates an instance UUID when no explicit TD id is supplied.  The
    DOM and task schemas identify the same Thing by its stable title.  Keeping
    the generated UUID as provenance while using the title as the runtime alias
    prevents every container restart from creating a different cognitive entity.
    """

    rewritten = _rewrite_td_forms(td, public_base_url)
    title = str(rewritten.get("title", "")).strip()
    source_id = str(rewritten.get("id", "")).strip()
    if title and source_id and source_id != title:
        rewritten["x-runtime-source-id"] = source_id
        rewritten["id"] = title
    return rewritten


def _parse_probe_value(raw: str, probe: DomStateProbe) -> Any:
    value = raw.strip()
    if probe.value_type == "string":
        return value
    if probe.value_type == "boolean":
        if probe.true_pattern and re.search(probe.true_pattern, value, flags=re.IGNORECASE):
            return True
        if probe.false_pattern and re.search(probe.false_pattern, value, flags=re.IGNORECASE):
            return False
        raise ValueError(f"DOM value {value!r} matches neither boolean pattern")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
    if match is None:
        raise ValueError(f"DOM value {value!r} contains no number")
    number = float(match.group(0))
    return int(number) if probe.value_type == "integer" else number


def _observation_source(backend: str) -> Literal["dom", "visual", "wot", "system"]:
    if backend in {"dom", "visual", "wot", "system"}:
        return backend  # type: ignore[return-value]
    return "system"
