from __future__ import annotations

import json
import pytest

from runtime.learning_canvas.lesson import (
    build_lesson_narration_script,
    validate_lesson,
)
from runtime.learning_canvas.narration import CanvasPlayback
from runtime.learning_canvas.pack import load_pack
from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_canvas.snapshot import build_snapshot
from runtime.learning_core import LearningService, LearningStore
from runtime.learning_core.contracts import LearningPath, KnowledgePoint


def test_build_lesson_narration_script_and_block_ids():
    raw_lesson = {
        "blocks": [
            {"kind": "text", "title": "Khái niệm", "text": "Định nghĩa về hàm bậc hai."},
            {"kind": "formula", "title": "Công thức nghiệm", "latex": r"x = \frac{-b \pm \sqrt{\Delta}}{2a}", "explanation": "Biệt thức delta"},
            {"kind": "code", "title": "Minh họa Python", "language": "python", "text": "def solve(): pass"},
            {"kind": "check", "title": "Câu hỏi nhanh", "question": "Delta âm thì sao?", "answer": "Vô nghiệm thực", "options": ["Vô nghiệm thực", "Có 2 nghiệm"]}
        ]
    }
    validated = validate_lesson(raw_lesson, group_id="g1", node_id="n1", title="Hàm số bậc hai")
    assert len(validated["blocks"]) == 4
    for b in validated["blocks"]:
        assert "id" in b and b["id"].startswith("b-")

    script = build_lesson_narration_script(validated)
    assert len(script) == 4
    assert script[0]["kind"] == "text"
    assert "Định nghĩa" in script[0]["say"]
    assert script[1]["kind"] == "formula"
    assert "Công thức nghiệm" in script[1]["say"]
    assert script[2]["kind"] == "code"
    assert script[3]["kind"] == "check"


def test_canvas_playback_register_lesson_narration():
    pack = load_pack()
    playback = CanvasPlayback(pack)

    blocks = {0: "b-0", 1: "b-1", 2: "b-2"}
    packet = playback.register_lesson_narration("turn-1", 1, "g-algo", "n-search", blocks)
    assert packet is not None
    assert packet["event_type"] == "queued"
    assert packet["node_id"] == "n-search"

    start = playback.event("audio_start", "turn-1", 0)
    assert start["event_type"] == "audio_start"
    assert start["block_id"] == "b-0"
    assert not start["final"]

    next_seg = playback.event("audio_start", "turn-1", 1)
    assert next_seg["block_id"] == "b-1"

    done = playback.event("audio_done", "turn-1", 2)
    assert done["final"] is True


def test_snapshot_includes_due_review_and_retention(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    learning = LearningService(store)
    pack = load_pack()

    focus = {"node_id": "javadi:v1:constructor", "revision": 1, "pinned": False}
    snapshot = build_snapshot(learning, pack, focus)
    assert "due_review" in snapshot
    assert "retention" in snapshot
    assert "recommendations" in snapshot
    assert isinstance(snapshot["due_review"], list)
    assert isinstance(snapshot["retention"], dict)


def test_service_lesson_narrate_action(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    learning = LearningService(store)
    service = LearningCanvasService(learning, store=store)
    events = []
    service.on_event = events.append

    # Setup active viewer
    service._view_session("open", "viewer-12345678")

    # Create topic and manually save a lesson
    topic = service.graphs.create("Giải tích", base_revision=0)
    group_id = topic["group_id"]
    node_id = topic["selected_node_id"]

    raw = {
        "blocks": [
            {"kind": "text", "title": "Giới hạn", "text": "Khái niệm giới hạn của dãy số."}
        ]
    }
    service.lessons.put(group_id, node_id, "Giới hạn", raw)

    # Call lesson_narrate action
    res = service.handle({
        "request_id": "req-narrate-1",
        "action": "lesson_narrate",
        "group_id": group_id,
        "node_id": node_id
    })
    assert res["status"] == "ok"
    assert "narration_script" in res
    assert len(res["narration_script"]) == 1
    assert "Giới hạn" in res["narration_script"][0]["say"]

    # Verify event emitted
    assert len(events) == 1
    assert events[0]["action"] == "lesson_narrate"
    assert events[0]["node_id"] == node_id

    service.close()
    store.close()
