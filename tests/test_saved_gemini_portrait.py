# -*- coding: utf-8 -*-
"""Kiểm thử cơ chế lưu chân dung Gemini, không cần kết nối mạng thật."""
import json
import threading
import time

from memory.living_user import LivingUserMemoryService, SavedGeminiMemoryPortrait
from runtime.living_user_panel import build_memory_panel_snapshot, handle_memory_panel_request


class GeminiStub:
    def __init__(self, release=None):
        self.calls = 0
        self.release = release

    def create_chat_completion(self, **kwargs):
        self.calls += 1
        if self.release is not None:
            assert self.release.wait(timeout=4)
        data = json.loads(kwargs["messages"][1]["content"])
        evidence = data["evidence"]
        item = evidence[0]
        # Văn bản trong fake có evidence gốc để test ref validation.
        content = {
            "opening": "Tôi nhớ bạn đã nói điều này.",
            "aside": "…Tôi không quên đâu.",
            "sections": [{
                "kind": "how_i_see_you", "title": "Trong mắt tôi",
                "items": [{"title": "Sở thích", "body": item["value"],
                           "source_refs": [item["ref"]]}],
            }],
        }
        return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}


def _ready(presenter, memory, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = build_memory_panel_snapshot(memory, portrait_presenter=presenter)
        if result["portrait"].get("generation_status") == "ready":
            return result
        time.sleep(0.025)
    raise AssertionError("Gemini không tạo bản lưu trong thời hạn kiểm thử")


def test_gemini_saves_once_and_restart_reads_without_calling(tmp_path):
    memory = LivingUserMemoryService(tmp_path)
    memory.correct("preferences.design", "Thích giao diện trong suốt", evidence_ids=("turn_1",))
    client = GeminiStub()
    presenter = SavedGeminiMemoryPortrait(tmp_path, client=client)
    first = build_memory_panel_snapshot(memory, portrait_presenter=presenter)
    assert first["portrait"]["generation_status"] in {"writing", "ready"}
    result = _ready(presenter, memory)
    assert result["portrait"]["renderer"] == "gemini_saved_portrait"
    assert client.calls == 1
    assert (tmp_path / "living_user_portrait.json").exists()
    assert "Thích giao diện trong suốt" in (tmp_path / "living_user_portrait.md").read_text(encoding="utf-8")

    # Restart: bản cũ đọc từ đĩa, dù Gemini đang không có mạng.
    offline = SavedGeminiMemoryPortrait(tmp_path, client=None)
    remembered = build_memory_panel_snapshot(memory, portrait_presenter=offline)
    assert remembered["portrait"]["generation_status"] == "ready"
    assert remembered["portrait"]["sections"][0]["items"][0]["body"] == "Thích giao diện trong suốt"


def test_change_removes_old_portrait_and_forget_cancels_inflight_write(tmp_path):
    memory = LivingUserMemoryService(tmp_path)
    memory.correct("preferences.theme", "Ghét màu đỏ", evidence_ids=("turn_1",))
    client = GeminiStub()
    presenter = SavedGeminiMemoryPortrait(tmp_path, client=client)
    _ready(presenter, memory)
    assert (tmp_path / "living_user_portrait.json").exists()

    # Thay đổi fact: không render / giữ file chứa thông tin cũ.
    hold = threading.Event()
    client.release = hold
    updated = handle_memory_panel_request(memory, {
        "action": "correct", "path": "preferences.theme", "value": "Thích màu xanh",
    }, portrait_presenter=presenter)
    assert updated.status == "ok"
    assert "Ghét màu đỏ" not in json.dumps(updated.payload, ensure_ascii=False)
    assert not (tmp_path / "living_user_portrait.json").exists()

    # Quên ngay khi Gemini đang viết: kết quả về muộn phải bị bỏ qua.
    forgotten = handle_memory_panel_request(memory, {
        "action": "forget", "path": "preferences.theme", "hard": True,
    }, portrait_presenter=presenter)
    assert forgotten.status == "ok"
    assert forgotten.payload["records"] == []
    hold.set()
    if presenter._worker:
        presenter._worker.join(timeout=4)
    assert not (tmp_path / "living_user_portrait.json").exists()
    assert not (tmp_path / "living_user_portrait.md").exists()


def test_unavailable_gemini_never_blocks_panel(tmp_path):
    memory = LivingUserMemoryService(tmp_path)
    memory.correct("preferences.answer", "Trả lời ngắn", evidence_ids=("turn_1",))
    presenter = SavedGeminiMemoryPortrait(tmp_path, client=None)
    start = time.monotonic()
    result = build_memory_panel_snapshot(memory, portrait_presenter=presenter)
    assert time.monotonic() - start < 1.0
    assert result["portrait"]["generation_status"] == "unavailable"
    assert result["records"][0]["display_value"] == "Trả lời ngắn"
