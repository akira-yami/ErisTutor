# -*- coding: utf-8 -*-
from runtime.public_lifecycle import PublicLifecycleController


class _Owner:
    def __init__(self):
        self.events = []

    def _emit_stage_protocol_state(self, event_type, *, reason="", data=None, **kwargs):
        self.events.append((event_type, reason, dict(data or {})))


def test_v62_composing_can_finish_as_clean_silence_done() -> None:
    owner = _Owner()
    lifecycle = PublicLifecycleController(owner)
    lifecycle.begin(turn_id="auto-1", channel="autonomous")

    assert lifecycle.transition(
        "DONE",
        turn_id="auto-1",
        reason="surface_duplicate_recent_utterance",
    ) is True
    assert lifecycle.snapshot().state == "DONE"
    assert any(event[0] == "done" for event in owner.events)
    assert not any(event[0] == "degraded" for event in owner.events)
