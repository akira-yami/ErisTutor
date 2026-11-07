# -*- coding: utf-8 -*-
"""Regression cho Basic Settings và credential não chính.

Giữ ba contract người dùng nhìn thấy được:
- main cognition có pool riêng, không mượn key Groq/Gemini phụ;
- health snapshot nêu rõ model/provider/fingerprint mà không lộ raw key;
- connection test dùng đúng client main và trả ACK kỹ thuật, không chen vào chat.
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace


def test_remote_main_pool_is_distinct_from_groq_and_gemini(monkeypatch) -> None:
    import eris_secrets
    from llm import key_pool

    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek")
    monkeypatch.setenv("ERIS_REMOTE_FALLBACK_API_KEYS", "remote-fallback-1,remote-fallback-2")
    monkeypatch.setattr(
        eris_secrets,
        "get_provider_profile",
        lambda provider, slot="default": {"api_key": "remote-primary"} if slot == "remote_main" else {},
    )
    monkeypatch.setattr(
        eris_secrets,
        "get_secret",
        lambda *names, default="": "legacy-remote" if "ERIS_REMOTE_API_KEY" in names else default,
    )
    monkeypatch.setattr(
        eris_secrets,
        "_load_secrets",
        lambda: {"api_keys": {"ERIS_REMOTE_FALLBACK_API_KEYS": ["remote-file-fallback"]}},
    )

    assert key_pool._normalize_kind("remote_main") == "remote"
    assert key_pool._normalize_kind("deepseek") == "remote"
    assert key_pool._normalize_kind("groq") == "groq"
    assert key_pool._normalize_kind("gemini") == "gemini"
    assert key_pool._collect_remote_keys() == [
        "remote-primary",
        "legacy-remote",
        "remote-file-fallback",
        "remote-fallback-1",
        "remote-fallback-2",
    ]
    # Remote key không được rơi ngược vào pool Gemini phụ.
    assert key_pool._collect_gemini_keys() == []


def test_process_manager_attaches_remote_pool_to_main_openai_client(monkeypatch) -> None:
    import llm.process_manager as process_manager

    class _Pool:
        def acquire(self, allow_cooling=False):
            return SimpleNamespace(key="main-pool-key")

    captured = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek")
    monkeypatch.setenv("ERIS_REMOTE_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("ERIS_REMOTE_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(process_manager, "apply_secrets_to_environ", lambda: None)
    monkeypatch.setattr(process_manager, "get_provider_profile", lambda *a, **k: {})
    monkeypatch.setattr(process_manager, "get_secret", lambda *a, **k: "")
    monkeypatch.setattr(process_manager, "get_key_pool", lambda kind: _Pool() if kind == "remote_main" else None)
    monkeypatch.setattr(process_manager, "OpenAICompatibleClient", _Client)

    manager = process_manager.RemoteModelProcessManager(logger=logging.getLogger("test"), project_root=".")
    manager._build_remote_client()

    assert captured["model_name"] == "deepseek-v4-flash"
    assert captured["api_key"] == "main-pool-key"
    assert isinstance(captured["key_pool"], _Pool)


def test_main_brain_health_and_connection_ack_hide_raw_key(monkeypatch) -> None:
    from runtime.coordinator import RuntimeCoordinator

    class _Pool:
        def snapshot(self):
            return {
                "size": 2,
                "available": 2,
                "keys": [
                    {"fingerprint": "abc123def456", "active": True, "status": "ready"},
                    {"fingerprint": "fed654cba321", "active": False, "status": "ready"},
                ],
            }

    class _Client:
        base_url = "https://api.deepseek.com"
        model_name = "deepseek-v4-flash"
        api_key = "SECRET-MUST-NOT-LEAK"
        key_pool = _Pool()

        def create_chat_completion(self, **kwargs):
            return {"choices": [{"message": {"content": "OK"}}]}

    class _Transport:
        def __init__(self):
            self.messages = []

        def send_json(self, payload):
            self.messages.append(payload)

    owner = object.__new__(RuntimeCoordinator)
    owner.logger = logging.getLogger("test")
    owner.transport = _Transport()
    owner.llm_service = SimpleNamespace(
        client=_Client(),
        fallback_client=SimpleNamespace(model_name="gpt-5.5"),
        _extract_content=lambda response: "OK",
    )
    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek")

    snapshot = owner._main_brain_health_snapshot()
    assert snapshot["model"] == "deepseek-v4-flash"
    assert snapshot["active_key_fingerprint"] == "abc123def456"
    assert "SECRET-MUST-NOT-LEAK" not in repr(snapshot)

    owner._run_settings_connection_test("test-1")
    ack = owner.transport.messages[-1]["settings_connection_test_ack"]
    assert ack["ok"] is True
    assert ack["latency_ms"] >= 0
    assert ack["main_brain"]["model"] == "deepseek-v4-flash"
    assert "SECRET-MUST-NOT-LEAK" not in repr(ack)


def test_tts_text_sync_timeout_defaults_to_audio_start_window(monkeypatch) -> None:
    from runtime.tts_sync_policy import text_sync_timeout_seconds

    monkeypatch.delenv("ERIS_TTS_TEXT_SYNC_TIMEOUT", raising=False)
    assert text_sync_timeout_seconds() == 30.0

    monkeypatch.setenv("ERIS_TTS_TEXT_SYNC_TIMEOUT", "99")
    assert text_sync_timeout_seconds() == 99.0

    monkeypatch.setenv("ERIS_TTS_TEXT_SYNC_TIMEOUT", "bad")
    assert text_sync_timeout_seconds() == 30.0
