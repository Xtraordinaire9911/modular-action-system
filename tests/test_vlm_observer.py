"""A vision model may be wrong; it must never be able to launder a guess.

The failure this guards against is the one the review would catch immediately:
a model answer presented as certainty, or presented as evidence when no model
ran at all. Every test here is about the honesty of the provenance rather than
about the answer being right.
"""

from __future__ import annotations

import json

from src.perception.vlm_observer import MIN_USABLE_CONFIDENCE, VlmObserver


class _Client:
    name = "fake-vision-1"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.images: list[bytes] = []
        self.questions: list[str] = []

    def describe(self, system: str, question: str, image_png: bytes) -> str:
        self.images.append(image_png)
        self.questions.append(question)
        return self.reply


class _Exploding:
    name = "fake-vision-1"

    def describe(self, system: str, question: str, image_png: bytes) -> str:
        raise RuntimeError("model unreachable")


def _observer(client, tmp_path, **kwargs):
    return VlmObserver(client=client, ledger_path=tmp_path / "calls.jsonl", **kwargs)


def _reply(answer: bool, confidence: float) -> str:
    return json.dumps({"answer": answer, "confidence": confidence, "evidence": "the cart lists one item"})


# --- the image actually reaches the model ------------------------------------------


def test_the_screenshot_is_what_is_sent(tmp_path):
    """Not a description of the screenshot. The bytes."""
    client = _Client(_reply(True, 0.9))
    observer = _observer(client, tmp_path)

    observer.look(b"\x89PNG-pretend", "Is there an item in the cart?", region="#cart-items")

    assert client.images == [b"\x89PNG-pretend"]
    assert "cart" in client.questions[0]


def test_the_judgement_identifies_which_image_it_came_from(tmp_path):
    """Two runs against different screenshots must not be confusable."""
    observer = _observer(_Client(_reply(True, 0.9)), tmp_path)

    first = observer.look(b"one", "q")
    second = observer.look(b"two", "q")

    assert first.screenshot_sha256 and first.screenshot_sha256 != second.screenshot_sha256


# --- provenance -------------------------------------------------------------------


def test_a_confident_answer_becomes_a_visual_assertion_with_the_model_s_confidence(tmp_path):
    confident = 0.98  # above the calibrated threshold; see MIN_USABLE_CONFIDENCE
    observer = _observer(_Client(_reply(True, confident)), tmp_path)

    judgement = observer.look(b"png", "Is the cart non-empty?", region="#cart-items")
    assertion = judgement.as_assertion("cart", "holds_item")

    assert judgement.is_model_derived
    assert assertion is not None
    assert assertion.source == "visual"
    assert assertion.value is True
    assert assertion.confidence == confident, "the model's confidence, not 1.0"
    assert assertion.provenance["model"] == "fake-vision-1"
    assert assertion.provenance["screenshot_sha256"] == judgement.screenshot_sha256
    assert assertion.provenance["is_model_derived"] is True


def test_confidence_is_never_rounded_up_to_certainty(tmp_path):
    """The state-tree channel stamps 1.0 on everything, which is why it is not used."""
    observer = _observer(_Client(_reply(True, 0.97)), tmp_path)

    assertion = observer.look(b"png", "q").as_assertion("cart", "holds_item")

    assert assertion.confidence < 1.0


# --- abstention -------------------------------------------------------------------


def test_an_unsure_model_abstains_instead_of_voting(tmp_path):
    observer = _observer(_Client(_reply(True, 0.3)), tmp_path)

    judgement = observer.look(b"png", "q")

    assert judgement.source == "low_confidence"
    assert judgement.is_model_derived is False
    assert judgement.as_assertion("cart", "holds_item") is None, "a coin flip must not enter the fusion"
    assert judgement.confidence == 0.3, "what it said is still recorded"


def test_no_client_means_no_evidence_rather_than_a_default(tmp_path):
    observer = _observer(None, tmp_path)

    judgement = observer.look(b"png", "q")

    assert judgement.source == "unavailable"
    assert judgement.as_assertion("cart", "holds_item") is None
    assert "no vision client" in judgement.error


def test_a_failing_model_is_reported_not_swallowed(tmp_path):
    observer = _observer(_Exploding(), tmp_path)

    judgement = observer.look(b"png", "q")

    assert judgement.source == "error"
    assert "model unreachable" in judgement.error
    assert judgement.as_assertion("cart", "holds_item") is None


def test_a_reply_that_is_not_json_is_an_error_not_a_false_answer(tmp_path):
    observer = _observer(_Client("I think the cart looks full to me"), tmp_path)

    judgement = observer.look(b"png", "q")

    assert judgement.source == "error"
    assert judgement.as_assertion("cart", "holds_item") is None


def test_an_empty_screenshot_is_refused_before_the_model_is_called(tmp_path):
    client = _Client(_reply(True, 0.9))
    observer = _observer(client, tmp_path)

    judgement = observer.look(b"", "q")

    assert judgement.source == "error" and not client.images


# --- the audit trail ----------------------------------------------------------------


def test_every_call_is_written_to_the_ledger_including_the_unusable_ones(tmp_path):
    ledger = tmp_path / "calls.jsonl"
    observer = VlmObserver(client=_Client(_reply(True, 0.2)), ledger_path=ledger)

    observer.look(b"png", "q", region="#cart-items")

    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["source"] == "low_confidence"
    assert lines[0]["region"] == "#cart-items"
    assert lines[0]["is_model_derived"] is False


def test_the_threshold_is_configurable_and_reported(tmp_path):
    observer = _observer(_Client(_reply(True, 0.4)), tmp_path, min_confidence=0.3)

    assert observer.look(b"png", "q").is_model_derived is True


# --- spend guards ------------------------------------------------------------------


def test_the_same_screenshot_and_question_is_never_paid_for_twice(tmp_path):
    """The runtime observes repeatedly; the pixels and the question do not change."""
    client = _Client(_reply(True, 0.9))
    observer = _observer(client, tmp_path, max_calls=5)

    first = observer.look(b"png", "Is the cart non-empty?")
    second = observer.look(b"png", "Is the cart non-empty?")

    assert len(client.images) == 1, "the second look was billed"
    assert observer.billed_calls == 1
    assert second is first


def test_a_different_screenshot_is_a_new_question(tmp_path):
    client = _Client(_reply(True, 0.9))
    observer = _observer(client, tmp_path, max_calls=5)

    observer.look(b"before", "q")
    observer.look(b"after", "q")

    assert len(client.images) == 2


def test_the_ceiling_stops_a_runaway_loop_from_spending(tmp_path):
    """A recovery loop must not be able to bill once per attempt."""
    client = _Client(_reply(True, 0.9))
    observer = _observer(client, tmp_path, max_calls=2)

    judgements = [observer.look(f"png-{n}".encode(), "q") for n in range(5)]

    assert len(client.images) == 2, "the ceiling did not hold"
    assert [j.source for j in judgements[2:]] == ["budget_exhausted"] * 3
    assert all(j.as_assertion("cart", "holds_item") is None for j in judgements[2:])


def test_an_exhausted_budget_is_reported_not_silently_skipped(tmp_path):
    observer = _observer(_Client(_reply(True, 0.9)), tmp_path, max_calls=0)

    judgement = observer.look(b"png", "q")

    assert judgement.source == "budget_exhausted"
    assert "ceiling of 0" in judgement.error


# --- provider precedence -------------------------------------------------------------


def test_providers_are_ordered_cheapest_first_and_exclude_text_only_vendors(monkeypatch):
    """DeepSeek is absent on purpose: its API takes text, so it cannot answer this."""
    from src.perception.vlm_observer import VISION_PROVIDERS

    names = [model for _, model, _ in VISION_PROVIDERS]

    assert names[0] == "qwen-vl-plus", "the cheapest configured option should win"
    assert not any("deepseek" in name for name in names)


def test_an_explicit_endpoint_overrides_the_table(monkeypatch, tmp_path):
    from src.perception.vlm_observer import available_vision_client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "cheap")
    monkeypatch.setenv("VLM_API_KEY", "explicit")
    monkeypatch.setenv("VLM_MODEL", "some-other-vl")

    assert available_vision_client().name == "some-other-vl"


def test_nothing_configured_means_no_client(monkeypatch, tmp_path):
    """Run from a directory with no .env.local, or the developer's own key answers."""
    from src.perception.vlm_observer import available_vision_client

    monkeypatch.chdir(tmp_path)
    for var in ("VLM_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    assert available_vision_client() is None


def test_the_threshold_sits_inside_the_range_the_model_actually_uses(tmp_path):
    """A gate no answer ever approaches is not a gate.

    The first value here was 0.55, and the measured range for qwen-vl-plus is
    1.00 on clear evidence and 0.90 on a region cut off mid-word - so nothing
    ever fell below it and the abstention path could not fire. These two pin the
    calibrated boundary in both directions.
    """
    clear = _observer(_Client(_reply(True, 1.0)), tmp_path).look(b"a", "q")
    murky = _observer(_Client(_reply(True, 0.90)), tmp_path).look(b"b", "q")

    assert 0.9 < MIN_USABLE_CONFIDENCE <= 1.0, "the gate must sit inside the range the model uses"
    assert clear.usable, "a plainly readable region must count as evidence"
    assert not murky.usable, "a region cut off mid-word must not"
