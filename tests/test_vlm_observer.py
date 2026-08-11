"""A vision model may be wrong; it must never be able to launder a guess.

The failure this guards against is the one the review would catch immediately:
a model answer presented as certainty, or presented as evidence when no model
ran at all. Every test here is about the honesty of the provenance rather than
about the answer being right.
"""

from __future__ import annotations

import json

from src.perception.vlm_observer import VlmObserver


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
    observer = _observer(_Client(_reply(True, 0.82)), tmp_path)

    judgement = observer.look(b"png", "Is the cart non-empty?", region="#cart-items")
    assertion = judgement.as_assertion("cart", "holds_item")

    assert judgement.is_model_derived
    assert assertion is not None
    assert assertion.source == "visual"
    assert assertion.value is True
    assert assertion.confidence == 0.82, "the model's confidence, not 1.0"
    assert assertion.provenance["model"] == "fake-vision-1"
    assert assertion.provenance["screenshot_sha256"] == judgement.screenshot_sha256
    assert assertion.provenance["is_model_derived"] is True


def test_confidence_is_never_rounded_up_to_certainty(tmp_path):
    """The state-tree channel stamps 1.0 on everything, which is why it is not used."""
    observer = _observer(_Client(_reply(True, 0.6)), tmp_path)

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
