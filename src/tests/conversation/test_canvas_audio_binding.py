from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import io
import json
import wave

import pytest

from domain import Decision
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.runtime_writer import RuntimeConversationWriter
from runtime.conversation.public_output_artifact import validate_prepared
from runtime.learning_canvas import LearningCanvasService
from runtime.learning_canvas.narration import CanvasPlayback
from runtime.learning_core import LearningService, LearningStore
from runtime.tts_task_manager import TtsTaskManager


def test_root_parts_are_realized_bound_and_sealed_without_text_position_guessing(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    canvas = LearningCanvasService(LearningService(store), store=store)
    focus = canvas.focus.navigate("javadi:v1:constructor", base_revision=0)
    scope = {"node_id": focus["node_id"], "focus_revision": focus["focus_revision"]}
    calls = []

    class Parser:
        def parse(self, raw, **kwargs):
            data = json.loads(raw)
            return Decision(text=data["text"], animation="Idle", speech_text=data.get("speech_text", ""))

    def generate(**kwargs):
        calls.append(kwargs)
        return {"text": "Giải thích Constructor Injection.", "speech_text": "コンストラクターで依存を渡します。"}

    move = NextConversationMove(intent="teach", say="Explain constructor", canvas_segments=(
        {"block_id": "unknown-block", "say": "Do not speak this part"},
        {"block_id": "di.constructor.meaning", "say": "Explain the constructor concept"},
        {"block_id": "di.constructor.java", "say": "Explain the authored Java example"},
    ))
    host = SimpleNamespace(parser=Parser(), ai_stage_planner=None, learning_canvas_service=canvas,
        ai_model_router=SimpleNamespace(generate_public_reply=generate), current_stage_user_text="")
    delivered = []
    kernel = ConversationKernel(SimpleNamespace(decide=lambda **_: move), writer=RuntimeConversationWriter(host),
        delivery=SimpleNamespace(deliver=lambda **kw: delivered.append(kw)))
    host.conversation_kernel = kernel
    try:
        result = kernel.handle_event(ConversationEvent(event_id="canvas-parts", session_id="s", kind="tutor_result"),
                                     runtime_context={"__canvas_scope": scope})
        assert result.status == "delivering", result.failure_reason
        prepared = delivered[0]["rendered"].metadata["prepared_public_output"]
        assert len(calls) == 2 and len(prepared.segments) >= 2
        assert "CANVAS_AUTHORED_NODE" in calls[0]["grounding_context"]
        assert {f.delivery["learning_canvas"]["block_id"] for f in prepared.segments} == {"di.constructor.meaning", "di.constructor.java"}
        assert [f.segment_id for f in prepared.segments] == list(range(len(prepared.segments)))
        validate_prepared(prepared)
        tampered = replace(prepared.segments[0], delivery={**prepared.segments[0].delivery, "learning_canvas": {"block_id": "forged"}})
        with pytest.raises(RuntimeError, match="seal_mismatch"):
            validate_prepared(replace(prepared, segments=(tampered, *prepared.segments[1:])))
    finally:
        canvas.close()
        store.close()


def test_canvas_highlight_follows_actual_manager_start_per_segment_even_forced_inline(monkeypatch):
    from runtime.learning_canvas.pack import load_pack
    from runtime.stage_contracts import StageFrame
    monkeypatch.setenv("ERIS_TTS_TASK_STRATEGY", "inline")
    tracker = CanvasPlayback(load_pack())
    scope = {"node_id": "javadi:v1:constructor", "focus_revision": 1}
    packets = [tracker.register("delivery:A", 3, scope, {0: "di.constructor.meaning", 1: "di.constructor.java"})]
    assert packets[0]["block_id"] == ""  # enqueue is not audio_start
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00" * 800)

    class Device:
        def synthesize(self, text, **kwargs):
            return output.getvalue()

        def play_audio(self, data, stop_event, on_started=None):
            on_started()

    def observed(event, frame):
        packet = tracker.event(event.type, event.turn_id, event.segment_id)
        if packet:
            packets.append(packet)

    frames = [StageFrame(turn_id="delivery:A", segment_id=i, tts_text="Text " + str(i),
        delivery={"learning_canvas": {**scope, "generation": 3}}) for i in range(2)]
    manager = TtsTaskManager()
    try:
        assert manager.dispatch_frames(frames, backend=Device(), valence=0, arousal=.3, stance="neutral", on_event=observed)
        assert manager.wait_until_idle(3)
    finally:
        manager.shutdown()
    starts = [p for p in packets if p["event_type"] == "audio_start"]
    assert [(p["segment_id"], p["block_id"]) for p in starts] == [(0, "di.constructor.meaning"), (1, "di.constructor.java")]
    assert all(p["generation"] == 3 for p in starts)
    assert packets[-1]["final"] and not tracker.snapshot()
    assert [p["seq"] for p in packets] == sorted(p["seq"] for p in packets)


def test_unknown_block_and_stale_turn_cannot_highlight_a_new_turn():
    from runtime.learning_canvas.pack import load_pack
    tracker = CanvasPlayback(load_pack())
    scope = {"node_id": "javadi:v1:constructor", "focus_revision": 1}
    tracker.register("old", 1, scope, {0: "unknown"})
    assert tracker.event("audio_start", "old", 0)["block_id"] == ""
    tracker.register("new", 2, scope, {0: "di.constructor.java"})
    assert tracker.event("audio_start", "old", 0) is None
    assert tracker.register("older-generation", 1, scope, {0: "di.constructor.java"}) is None
    assert tracker.event("audio_start", "new", 0)["block_id"] == "di.constructor.java"
    assert tracker.finish("old", successful=True) is None
    assert tracker.finish("new", cancelled=True)["event_type"] == "audio_cancelled"
