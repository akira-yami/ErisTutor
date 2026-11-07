from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from runtime.ai_orchestrated.discourse_state import DiscourseState
from runtime.ai_orchestrated.followup_appraisal import FollowUpAppraisalLayer, FollowUpPolicy
from runtime.session_turn_ledger import SessionTurnLedger
from cortex.vision_semantic_compressor import RemoteCaptionClient
import runtime.inference_broker as inference_broker


def _waiting_discourse() -> DiscourseState:
    return DiscourseState(
        mode="ambient",
        last_role="assistant",
        reply_expected=True,
        assistant_may_continue=False,
        thread_key="old-thread",
        seconds_since_assistant=0.0,
        confidence=0.9,
    )


def test_first_user_turn_in_new_session_closes_old_followup_cycle():
    layer = FollowUpAppraisalLayer(FollowUpPolicy(
        remind_after_seconds=30.0,
        abandon_after_reminder_seconds=48.0,
        abandon_without_reminder_seconds=105.0,
        reserved_distance=0.2,
    ))
    discourse = _waiting_discourse()
    ledger = SessionTurnLedger()
    ledger.record_assistant(
        "Bạn muốn xem lại chứ?", timestamp=100.0,
        meta={"assistant_act": {"kind": "question", "proposition_mode": "new", "confidence": 0.9}},
    )

    first = layer.evaluate(discourse, now=131.0, ledger=ledger)
    assert first.followup_reminder_due is True

    # Public reminder is an event, not a private flag in FollowUpAppraisalLayer.
    ledger.record_assistant(
        "Nhắc nhẹ thôi.", timestamp=132.0,
        meta={"speech_act": "followup_reminder"},
    )
    after_reminder = layer.evaluate(discourse, now=140.0, ledger=ledger)
    assert after_reminder.followup_reminder_count == 1

    # The first user response deterministically closes the old wait.
    ledger.record_user("Ừ, xem đi.", timestamp=141.0)
    after_user = layer.evaluate(discourse, now=142.0, ledger=ledger)
    assert after_user.reply_expected is False
    assert after_user.followup_reminder_due is False
    assert after_user.followup_reminder_count == 0
    assert not hasattr(layer, "_question_started_at")
    assert not hasattr(layer, "_reminder_spoken_at")


def test_new_assistant_question_after_user_can_start_new_cycle():
    layer = FollowUpAppraisalLayer()
    discourse = _waiting_discourse()
    ledger = SessionTurnLedger()
    ledger.record_assistant(
        "Câu cũ?", timestamp=100.0,
        meta={"assistant_act": {"kind": "question", "proposition_mode": "new", "confidence": 0.9}},
    )
    ledger.record_user("Trả lời cũ", timestamp=140.0)
    closed = layer.evaluate(discourse, now=141.0, ledger=ledger)
    assert closed.reply_expected is False

    ledger.record_assistant(
        "Câu mới?", timestamp=150.0,
        meta={"assistant_act": {"kind": "question", "proposition_mode": "new", "confidence": 0.9}},
    )
    newer = layer.evaluate(discourse, now=151.0, ledger=ledger)
    assert newer.reply_expected is True
    assert newer.followup_state == "waiting"


class _VisionClient:
    model_name = "test-vision"

    def __init__(self) -> None:
        self.calls = 0

    def create_chat_completion(self, **_kwargs):
        self.calls += 1
        return {"choices": [{"message": {"content": "should not be reached"}}]}


def test_optional_broker_rejection_is_not_retried(monkeypatch):
    entries = 0

    @contextmanager
    def rejected_context(*_args, **_kwargs):
        nonlocal entries
        entries += 1
        raise inference_broker.InferenceRejected("shed_optional_user_active")
        yield  # pragma: no cover

    monkeypatch.setattr(inference_broker, "inference_context", rejected_context)
    client = _VisionClient()
    captioner = RemoteCaptionClient(client)

    assert captioner.caption_image("ZmFrZS1wbmc=") == ""
    assert entries == 1
    assert client.calls == 0
