"""A real screenshot goes to a real vision model, and the answer joins the fusion.

Everything visual in this repository up to now has been geometry: elements
measured in the browser, numbered as Set-of-Marks targets, selected by a
deterministic scorer. No image has ever been sent to a model. That is the gap
the review keeps naming, and a caption rendered next to a screenshot would not
close it - the model's answer has to be able to change what the run does.

So this produces an :class:`ObservedAssertion` with ``source="visual"``, which is
the channel the runtime already fuses against DOM and WoT evidence. Two
consequences follow, and both are the point:

* When the model agrees with the DOM, the goal is verified by two independent
  sources rather than one.
* When it disagrees, the arbiter raises a conflict and the runtime does what it
  already does with unresolved uncertainty - re-observe, or refuse to let System
  1 continue. A wrong answer from the model degrades into caution, not into a
  wrong action.

The confidence travelling with the assertion is the model's own, never 1.0. The
state-tree channel would have stamped 1.0 on it, which is why this uses the
explicit assertion list instead: a guess presented as certainty is worse than no
evidence at all.

Nothing here decides what to click. It answers one yes/no question about what is
visible, and it says how sure it is.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from src.config.secrets import load_local_env
from src.contracts.types import ObservedAssertion

DEFAULT_LEDGER = Path("artifacts/vlm_observer/calls.jsonl")

# Below this the answer is not used as evidence. A model that is unsure is more
# useful as an abstention than as a coin flip the arbiter has to fuse.
#
# Calibrated from measurement rather than guessed. scripts/eval_model_value.py
# puts qwen-vl-plus in front of four conditions and reads the confidence back:
# it returns 1.00 on every clear one - the item plainly present, the region
# plainly blank, a different item plainly shown - and 0.90 on a region cut off
# mid-word. That is the whole range. The first value here was 0.55, which no
# answer ever came near, so the gate could not fire and the abstention path was
# decorative.
#
# Two consequences worth stating. This number is specific to this model: another
# one with a different calibration needs the evaluation re-run, and a model that
# reports 1.00 on everything cannot be gated on confidence at all. And the gate
# is not what makes a wrong answer safe - a confident wrong answer passes it.
# What makes it safe is that a disagreement becomes a conflict in the arbiter,
# which does not consult confidence.
MIN_USABLE_CONFIDENCE = 0.95

_SYSTEM_PROMPT = """You are looking at a screenshot of a web page or device \
dashboard and answering one factual question about what is visible.

Reply with JSON only, no prose:
  answer      true or false
  confidence  number between 0 and 1, how certain you are from the image alone
  evidence    one short sentence naming what in the image made you answer that

Rules:
- Judge only from the image. Do not assume what a page usually looks like.
- The image is often a small crop of a page rather than a whole page. Answer \
about what is inside the crop; do not reason about which part of the page it \
came from, and do not answer false merely because you cannot see the \
surrounding context.
- A blank or empty region is a clear observation, not an unclear one. If you can \
see the image plainly and the thing asked about is not in it, answer false with \
high confidence.
- Use a confidence below 0.5 only when you genuinely cannot tell: the image is \
cut off mid-content, the text is illegible, or something is obscuring the view. \
Not being able to find the thing is an answer, not an obstacle to answering.
"""


class VisionClient(Protocol):
    """Minimal surface so the model can be swapped or faked in tests."""

    name: str

    def describe(self, system: str, question: str, image_png: bytes) -> str: ...


@dataclass
class VisualJudgement:
    """What the model said, and everything needed to audit it."""

    answer: bool = False
    confidence: float = 0.0
    evidence: str = ""
    model: str = ""
    screenshot_sha256: str = ""
    region: str = ""
    question: str = ""
    latency_ms: float = 0.0
    source: str = "unavailable"  # vlm | low_confidence | unavailable | error
    error: str = ""
    retries: int = 0  # how many attempts were needed, so flakiness stays visible

    @property
    def is_model_derived(self) -> bool:
        """True only when a model actually answered and was confident enough."""
        return self.source == "vlm"

    @property
    def usable(self) -> bool:
        return self.is_model_derived

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "answer": self.answer,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "model": self.model,
            "screenshot_sha256": self.screenshot_sha256,
            "region": self.region,
            "question": self.question,
            "latency_ms": round(self.latency_ms, 1),
            "is_model_derived": self.is_model_derived,
            "retries": self.retries,
            "error": self.error,
        }

    def as_assertion(self, entity_id: str, attribute: str) -> ObservedAssertion | None:
        """The visual evidence, in the form the runtime fuses.

        Returns None when the judgement is not usable, so an unavailable or
        unsure model contributes nothing rather than contributing noise.
        """
        if not self.usable:
            return None
        return ObservedAssertion(
            entity_id=entity_id,
            attribute=attribute,
            value=self.answer,
            source="visual",
            confidence=self.confidence,
            timestamp_ms=int(time.time() * 1000),
            provenance={
                "model": self.model,
                "screenshot_sha256": self.screenshot_sha256,
                "region": self.region,
                "question": self.question,
                "evidence": self.evidence,
                "is_model_derived": True,
            },
        )


class AnthropicVisionClient:
    """Claude with an image block, using the SDK already declared in pyproject."""

    def __init__(self, *, model: str = "claude-sonnet-5", api_key: str | None = None) -> None:
        self.name = model
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def describe(self, system: str, question: str, image_png: bytes) -> str:
        import anthropic  # lazy: only needed when a model is actually used

        client = anthropic.Anthropic(api_key=self._key)
        message = client.messages.create(
            model=self.name,
            max_tokens=400,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(image_png).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": question},
                    ],
                }
            ],
        )
        parts: list[str] = []
        for block in message.content:
            candidate: Any = block
            if getattr(candidate, "type", "") == "text":
                parts.append(str(candidate.text))
        return "".join(parts)


class OpenAIVisionClient:
    """Any OpenAI-compatible vision endpoint, using the SDK already declared."""

    def __init__(self, *, model: str = "gpt-4o-mini", api_key: str | None = None, base_url: str | None = None) -> None:
        self.name = model
        self._key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url
        if not self._key:
            raise RuntimeError("OPENAI_API_KEY is not set")

    def describe(self, system: str, question: str, image_png: bytes) -> str:
        from openai import OpenAI  # lazy

        client = OpenAI(api_key=self._key, base_url=self._base_url)
        data_url = "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
        response = client.chat.completions.create(
            model=self.name,
            max_tokens=400,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": question},
                    ],
                },
            ],
        )
        return response.choices[0].message.content or ""


# Ordered cheapest first, because the only reason to pay more for this question
# would be if a costlier model answered "is the cart non-empty" better, and it
# does not. Each entry is (env var holding the key, model, OpenAI-compatible
# base URL or None for the vendor default).
#
# Alibaba Model Studio leads for two measured reasons rather than a preference:
# qwen-vl-plus bills input at about a fifteenth of Claude Sonnet's rate, and a
# new Singapore-region account carries a free grant large enough that this
# project's entire remaining schedule fits inside it. DeepSeek is deliberately
# absent - its API takes text only, so it cannot answer a question about a
# screenshot at any price.
VISION_PROVIDERS: tuple[tuple[str, str, str | None], ...] = (
    ("DASHSCOPE_API_KEY", "qwen-vl-plus", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ("ZHIPU_API_KEY", "glm-4v-flash", "https://open.bigmodel.cn/api/paas/v4"),
    ("OPENAI_API_KEY", "gpt-4o-mini", None),
)


def available_vision_client() -> VisionClient | None:
    """The cheapest configured vision client, or None when nothing is configured.

    Precedence is cost, not preference, and it is overridable: setting
    ``VLM_API_KEY`` (with optional ``VLM_MODEL`` and ``VLM_BASE_URL``) selects any
    OpenAI-compatible endpoint and takes priority over the table. Anthropic is
    tried last because it is the most expensive of the options for this
    particular question.

    Returning None rather than raising is deliberate: the caller then records an
    unavailable judgement, which is a claim it can defend, instead of a model
    result it cannot.
    """
    load_local_env()
    explicit = os.environ.get("VLM_API_KEY", "")
    if explicit:
        return OpenAIVisionClient(
            model=os.environ.get("VLM_MODEL", "qwen-vl-plus"),
            api_key=explicit,
            base_url=os.environ.get("VLM_BASE_URL") or None,
        )

    for env_var, model, base_url in VISION_PROVIDERS:
        key = os.environ.get(env_var, "")
        if key:
            try:
                return OpenAIVisionClient(model=model, api_key=key, base_url=base_url)
            except Exception:
                continue
    try:
        return AnthropicVisionClient()
    except Exception:
        return None


def _extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(text[start : end + 1])


@dataclass
class VlmObserver:
    """Asks a vision model one question about a screenshot, and logs the call."""

    client: VisionClient | None = None
    ledger_path: Path = DEFAULT_LEDGER
    min_confidence: float = MIN_USABLE_CONFIDENCE
    # A hard ceiling on paid calls per observer. The runtime observes several
    # times per episode, so an unguarded second opinion bills once per
    # observation rather than once per question - and a recovery loop would bill
    # once per attempt. This is the difference between a run costing a fraction
    # of a cent and a runaway loop costing whatever it feels like.
    max_calls: int = 2
    calls: list[dict[str, Any]] = field(default_factory=list)
    billed_calls: int = 0
    # Same pixels, same question, same answer. Reusing it is free and is also
    # more honest than asking twice and possibly getting two answers.
    _seen: dict[tuple[str, str], VisualJudgement] = field(default_factory=dict, repr=False)

    def look(self, image_png: bytes, question: str, *, region: str = "") -> VisualJudgement:
        """Answer ``question`` about ``image_png``, or say why it could not."""
        digest = hashlib.sha256(image_png).hexdigest()[:16]
        base = VisualJudgement(screenshot_sha256=digest, region=region, question=question)

        cached = self._seen.get((digest, question))
        if cached is not None:
            return cached
        if self.client is None:
            base.source = "unavailable"
            base.error = "no vision client configured"
            self._record(base)
            return base
        if self.billed_calls >= self.max_calls:
            base.source = "budget_exhausted"
            base.error = f"the per-run ceiling of {self.max_calls} vision calls was reached"
            self._record(base)
            return base
        if not image_png:
            base.source = "error"
            base.error = "empty screenshot"
            self._record(base)
            return base

        base.model = getattr(self.client, "name", "unknown")
        self.billed_calls += 1
        started = time.monotonic()
        # One retry. Measured over repeated evaluation runs, roughly one call in
        # twelve came back as a transport error or an unparseable reply - enough
        # to lose a verification during a demo. A single retry is the cheapest
        # honest answer: the failures observed were transient, and retrying twice
        # would start hiding a service that is actually down.
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                raw = self.client.describe(_SYSTEM_PROMPT, question, image_png)
                payload = _extract_json(raw)
                base.answer = bool(payload.get("answer"))
                base.confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0) or 0.0)))
                base.evidence = str(payload.get("evidence", "")).strip()
                base.retries = attempt
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.4)
        if last_error is not None:
            base.source = "error"
            base.retries = 1
            base.error = f"{type(last_error).__name__}: {last_error}"
            base.latency_ms = (time.monotonic() - started) * 1000.0
            self._record(base)
            return base

        base.latency_ms = (time.monotonic() - started) * 1000.0
        # An unsure model abstains instead of voting. The answer is still
        # recorded so the run can show what it said and why it was not used.
        base.source = "vlm" if base.confidence >= self.min_confidence else "low_confidence"
        self._record(base)
        self._seen[(digest, question)] = base
        return base

    def _record(self, judgement: VisualJudgement) -> None:
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **judgement.to_dict()}
        self.calls.append(entry)
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # a run must not fail because its audit log could not be written


__all__ = [
    "VISION_PROVIDERS",
    "AnthropicVisionClient",
    "MIN_USABLE_CONFIDENCE",
    "OpenAIVisionClient",
    "VisionClient",
    "VisualJudgement",
    "VlmObserver",
    "available_vision_client",
]
