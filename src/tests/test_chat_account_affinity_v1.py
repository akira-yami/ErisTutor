"""Chat account affinity: X-Whale2-Affinity-Key pins a conversation to one account.

Only user-facing chat lanes emit the header, it is stable across turns of one
runtime conversation, and it is gated by the SAME web_session_mode check as
X-Session-ID plus the ERIS_CHAT_ACCOUNT_AFFINITY kill-switch.
"""
from __future__ import annotations

import pytest


class _Response:
    headers = {}
    status_code = 200
    reason = "OK"
    url = "http://localhost:6011/v1/chat/completions"
    text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "ACK"}, "finish_reason": "stop"}]}


def _make_client(monkeypatch, capture):
    import llm.client as client_module

    def fake_post(**kwargs):
        capture.append(kwargs)
        return _Response()

    monkeypatch.setattr(client_module, "_brokered_post", fake_post)
    return client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1",
        api_key="sk-affinity-test",
        model_name="deepseek-v4-flash",
    )


def _send(client, lane, metadata=None):
    from runtime.inference_broker import inference_context

    with inference_context(lane, metadata=metadata or {"router_reason": "public_replies"}):
        client.create_chat_completion(
            messages=[{"role": "system", "content": "Root A"}, {"role": "user", "content": "Câu hỏi"}],
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=32,
        )


def test_chat_lane_same_session_pins_to_equal_affinity_key(monkeypatch):
    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "conv-fixed")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "isolated")
    captured = []
    client = _make_client(monkeypatch, captured)

    _send(client, "chat_main")
    _send(client, "chat_main")

    keys = [kwargs["headers"].get("X-Whale2-Affinity-Key") for kwargs in captured]
    assert all(key for key in keys), "chat lane must emit X-Whale2-Affinity-Key"
    assert keys[0] == keys[1], "same runtime session must pin to the same account key"


def test_background_lane_sends_no_affinity_header(monkeypatch):
    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "conv-fixed")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "isolated")
    captured = []
    client = _make_client(monkeypatch, captured)

    _send(client, "autonomous", metadata={})

    assert "X-Whale2-Affinity-Key" not in captured[-1]["headers"]


def test_kill_switch_off_sends_no_affinity_header(monkeypatch):
    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "conv-fixed")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "isolated")
    monkeypatch.setenv("ERIS_CHAT_ACCOUNT_AFFINITY", "0")
    captured = []
    client = _make_client(monkeypatch, captured)

    _send(client, "chat_main")

    assert "X-Whale2-Affinity-Key" not in captured[-1]["headers"]


def test_web_session_off_mode_sends_no_affinity_header(monkeypatch):
    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "conv-fixed")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "off")
    captured = []
    client = _make_client(monkeypatch, captured)

    _send(client, "chat_main")

    assert "X-Whale2-Affinity-Key" not in captured[-1]["headers"]
