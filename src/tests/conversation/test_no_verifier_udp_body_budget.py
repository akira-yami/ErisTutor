# -*- coding: utf-8 -*-
"""Regression từ log 2026-10-11: Writer PASS nhưng pre-dispatch UDP
vượt 63KB do identity_runtime/cognitive_views tích lũy qua từng lượt.

Không gọi DS2API. Mục tiêu: có panel nhiều dữ liệu thì Eris VẪN NÓI,
không cắt lời, không sửa transcript/TTS, không bypass generation guard.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from runtime.public_payload_policy import build_speech_body_payload, speech_body_udp_bytes
from transport.udp import MAX_UDP_PAYLOAD_BYTES


def test_panel_projection_more_than_udp_budget_cannot_block_speech():
    message = "Ừ, giờ AI phát triển nhanh thật. Cậu nghĩ bước tiếp theo sẽ thế nào?"
    large = "Lịch sử cảm xúc và ký ức " * 6000
    payload = {
        "text": message,
        "memory_text": message,
        "should_speak": True,
        "animation": "stand",
        "speech_text": "そうだね、AIが急速に進歩しているよ。",
        "stage": {"protocol": "eris.stage", "segment_count": 1},
        "identity_runtime": {"recent_identity_ledger": large},
        "cognitive_views": {"raw": large},
        "private_mind_digest": {"inner_diary": large},
        "private_life_digest": {"life": large},
        "emotion_debug": {"notes": large},
        "depth_v2": {"detail": large},
        "user_text": "những điều riêng của user",
    }
    assert speech_body_udp_bytes(payload) > MAX_UDP_PAYLOAD_BYTES
    body = build_speech_body_payload(payload)
    assert speech_body_udp_bytes(body) <= MAX_UDP_PAYLOAD_BYTES
    assert body["text"] == message and body["speech_text"] == payload["speech_text"]
    assert body["should_speak"] is True and body["stage"]["segment_count"] == 1
    assert all(key not in body for key in (
        "identity_runtime", "cognitive_views", "private_mind_digest",
        "private_life_digest", "depth_v2", "emotion_debug", "user_text",
    ))
    # Không mutate dữ liệu panel phục vụ lane khác.
    assert payload["identity_runtime"]["recent_identity_ledger"] == large


def test_oversized_nonpanel_metadata_uses_core_without_cutting_dialogue():
    phrase = "Eris vẫn trả lời trọn câu, kể cả khi telemetry lớn."
    payload = {
        "text": phrase,
        "should_speak": True,
        "system_action": "none",
        "action_input": "",
        "animation": "stand",
        "behavior_observation": {"trace": "x" * 80000},
        "performance_context": {"old_trace": "y" * 70000},
    }
    body = build_speech_body_payload(payload)
    assert body["text"] == phrase and body["should_speak"] is True
    assert body["system_action"] == "none" and body["action_input"] == ""
    assert speech_body_udp_bytes(body) <= MAX_UDP_PAYLOAD_BYTES
    assert "behavior_observation" not in body and "performance_context" not in body


def test_genuinely_unencodable_giant_speech_is_explicit_failure_not_silent_truncation():
    payload = {"text": "日本語" * 40000, "should_speak": True}
    with pytest.raises(RuntimeError, match="speech_body_core_exceeds_udp_budget"):
        build_speech_body_payload(payload)


def test_public_runtime_sends_speech_and_panel_separately_when_identity_grows(monkeypatch):
    from runtime.coordinator import RuntimeCoordinator
    from test_character_claim_integrity_v1 import _verified_delivery_fixture

    h, t, rendered = _verified_delivery_fixture()
    prepared = rendered.metadata["prepared_public_output"]
    assert prepared.stage_frame.display_text
    huge = "Ký ức riêng tư có thể rất dài. " * 4500
    fake_panel = {
        "identity_runtime": {"recent_identity_ledger": huge},
        "cognitive_views": {"history": huge},
        "private_mind_digest": {"inner_diary": "mình đang suy nghĩ"},
        "emotion_debug": {"mood": "neutral"},
        "depth_v2": {"status": "ready"},
    }
    monkeypatch.setattr("runtime.ai_orchestrated.panel_adapter.build_ai_panel_payload", lambda *a, **kw: fake_panel)
    monkeypatch.setenv("ERIS_TTS_SYNC_TEXT", "0")
    monkeypatch.setattr("runtime.runtime_settings_store.runtime_text",
                        lambda section, key, default=None: "ja" if (section, key) == ("tts", "language") else default)

    class Transport:
        def __init__(self):
            self.chat = []
            self.panel = []

        def send_json(self, data):
            self.chat.append(data)

        def send_panel_state(self, data):
            self.panel.append(data)
            return "panel:test"

    owner = object.__new__(RuntimeCoordinator)
    owner.logger = logging.getLogger("test-udp-fix")
    owner.transport = Transport()
    owner.conversation_kernel = h.conversation_kernel
    owner.ai_runtime_enabled = True
    owner.ai_state_reducer = SimpleNamespace(snapshot=lambda:object())
    owner.ai_discovery_runtime = None
    owner.ai_initiative_controller = None
    owner.ai_identity_consolidator = None
    owner._stage_channel_blocked = lambda _channel: False
    owner._record_public_turn = lambda *a, **kw: None
    owner._emit_stage_protocol_state = lambda *a, **kw: None
    owner._get_runtime_provider = lambda _kind: None
    owner.subconscious_runtime_enabled = False
    owner.stage_event_bus = None
    owner.current_stage_user_text = "giờ là thời đại AI nhỉ"
    owner.last_ai_intention_plan = None
    owner.observability = None

    assert owner._send_decision_to_body(prepared.decision, channel="chat", prepared=prepared) is True
    assert len(owner.transport.chat) == 1
    packet = owner.transport.chat[0]
    assert packet["text"] == prepared.display_text
    assert packet["should_speak"] is True
    assert speech_body_udp_bytes(packet) <= MAX_UDP_PAYLOAD_BYTES
    assert "identity_runtime" not in packet and "cognitive_views" not in packet
    assert len(owner.transport.panel) == 1
    assert owner.transport.panel[0]["identity_runtime"]["recent_identity_ledger"] == huge


def test_actual_udp_panel_sender_still_compacts_and_sends_identity():
    import json
    from transport.udp import UdpTransport

    sent = []
    # Không bind socket; chạy đúng logic send_panel_state vào fake send_text.
    transport = object.__new__(UdpTransport)
    transport._panel_seq = 0
    transport.send_text = lambda msg: sent.append(msg)
    giant = "quá dài " * 20000
    packet_id = transport.send_panel_state({
        "identity_runtime": {"summary": {"identity": giant}, "recent_identity_ledger": [giant]},
        "private_mind_digest": {"inner_diary": {"title": "Nội tâm", "text": giant}},
        "emotion_debug": {"items": [{"label": "warmth", "value": 0.5}]},
    })
    assert packet_id and len(sent) == 1
    assert len(sent[0].encode("utf-8")) <= MAX_UDP_PAYLOAD_BYTES
    data = json.loads(sent[0])
    assert data["type"] == "panel_state_update"
    assert isinstance(data["identity_runtime"], dict)
    assert isinstance(data["private_mind_digest"], dict)
    assert data["should_speak"] is False
