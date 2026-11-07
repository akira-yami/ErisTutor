# -*- coding: utf-8 -*-
"""Test trace full raw của mọi lane DeepSeek, không cần gọi mạng thật."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm import raw_trace, raw_trace_store
from llm.deepseek_workers import JsonlWorkerClient


def _events(folder: Path) -> list[dict]:
    output = []
    for path in list(folder.glob("deepseek_*.jsonl")) + list(folder.glob("events_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if "raw_request_ref" in event:
                event["raw_request_json"] = json.loads(raw_trace_store.restore_payload(folder, event["raw_request_ref"]))
            if "raw_response_ref" in event:
                event["raw_response_text"] = raw_trace_store.restore_payload(folder, event["raw_response_ref"]).decode('utf-8')
            output.append(event)
    return output


def test_opt_in_off_creates_no_trace(monkeypatch, tmp_path):
    monkeypatch.delenv("ERIS_DEEPSEEK_RAW_TRACE", raising=False)
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))
    assert raw_trace.begin_http_attempt(url="http://localhost/v1/chat/completions", model="deepseek-v4-flash",
                                        headers={}, body={"messages": ["bí mật"]}, lane="chat") is None
    assert list(tmp_path.iterdir()) == []


def test_full_unicode_request_and_response_without_auth_header(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))
    long_prompt = "Eris nghĩ gì?" * 10000
    body = {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": long_prompt}],
            "response_format": {"type": "json_object"}}
    with raw_trace.worker_role_context("executive"):
        trace = raw_trace.begin_http_attempt(
            url="http://localhost:6011/v1/chat/completions?token=DONT_SAVE",
            model="deepseek-v4-flash", body=body,
            headers={"Authorization": "Bearer SUPER_SECRET", "X-Session-ID": "private-session"},
            lane="chat_main", cognitive_lane="root_conversation", router_reason="eris_root",
        )
        trace.response(SimpleNamespace(status_code=200, text='{"reasoning_content":"nghĩ thầm", "choices":[{"message":{"content":"đúng vậy"}}]}'))
    events = _events(tmp_path)
    assert [item["event"] for item in events] == ["request", "response"]
    assert events[0]["request_id"] == events[1]["request_id"]
    assert events[0]["raw_request_json"] == body
    assert events[1]["raw_response_text"].startswith('{"reasoning_content"')
    assert events[0]["worker_role"] == events[1]["worker_role"] == "executive"
    assert events[0]["cognitive_lane"] == "root_conversation"
    assert events[0]["session_hash"] and events[0]["session_hash"] != "private-session"
    raw_file = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "SUPER_SECRET" not in raw_file
    assert "DONT_SAVE" not in raw_file
    assert "private-session" not in raw_file
    assert long_prompt not in raw_file  # body chỉ nằm trong object gzip; index nhỏ.


def test_transport_error_and_429_are_visible(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))
    first = raw_trace.begin_http_attempt(url="http://localhost/v1/chat/completions", model="deepseek-v4-flash",
                                         body={"messages": []}, headers={}, lane="post_observer")
    first.error(TimeoutError("secret-dont-log"))
    second = raw_trace.begin_http_attempt(url="http://localhost/v1/chat/completions", model="deepseek-v4-flash",
                                          body={"messages": []}, headers={}, lane="post_observer")
    second.response(SimpleNamespace(status_code=429, text='{"error":"rate limited"}'))
    events = _events(tmp_path)
    assert [e["event"] for e in events] == ["request", "transport_error", "request", "response"]
    assert events[1]["exception_type"] == "TimeoutError"
    assert "secret-dont-log" not in str(events)
    assert events[3]["http_status"] == 429


def test_role_is_attached_per_worker_without_mutating_shared_client(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))

    class SharedTransport:
        base_url = "http://localhost:6011/v1"
        model_name = "deepseek-v4-flash"
        def create_chat_completion(self, **kwargs):
            trace = raw_trace.begin_http_attempt(url=self.base_url + "/chat/completions", model=self.model_name,
                                                 body={"messages": kwargs["messages"]}, headers={}, lane="transport")
            trace.response(SimpleNamespace(status_code=200, text='{"choices":[]}'))
            return {"choices": [{"message": {"content": "ok"}}]}

    shared = SharedTransport()
    for role in ("surface", "observer", "seed"):
        wrapper = JsonlWorkerClient(shared, role=role, jsonl_enabled=False)
        wrapper.create_chat_completion(messages=[{"role": "user", "content": role}])
    assert [event["worker_role"] for event in _events(tmp_path) if event["event"] == "request"] == [
        "surface", "observer", "seed"
    ]
    assert not hasattr(shared, "worker_role")


def test_trace_write_failure_does_not_break_inference(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path / "file_not_directory"))
    (tmp_path / "file_not_directory").write_text("occupied", encoding="utf-8")
    trace = raw_trace.begin_http_attempt(url="http://localhost/v1/chat/completions", model="deepseek-v4-flash",
                                         body={"messages": []}, headers={}, lane="chat")
    trace.response(SimpleNamespace(status_code=200, text="ok"))


def test_broker_captures_effective_payload_not_draft(monkeypatch, tmp_path):
    """Gateway nhận đúng payload sau broker trim. Trace phải ghi đúng payload ấy."""
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))
    from llm import client
    from runtime.inference_broker import inference_context

    sent = []
    class StubBroker:
        def request_from_context(self, **kwargs):
            return SimpleNamespace(estimated_input_tokens=1, reserved_output_tokens=3)
        def run(self, request, fn):
            return fn(inference_request=request)
        def record_response_headers(self, *args, **kwargs):
            pass
    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"okay"}}]}'
        headers = {}

    def fake_post(url, timeout=None, **kwargs):
        sent.append(kwargs["json"])
        return FakeResponse()

    monkeypatch.setattr(client, "GLOBAL_INFERENCE_BROKER", StubBroker())
    monkeypatch.setattr(client, "_enforce_prompt_cap", lambda msgs, cap: ([{"role":"user", "content":"ACTUAL"}], True))
    monkeypatch.setattr(client, "_record_pressure_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(client.requests, "post", fake_post)
    with inference_context("post_observer", metadata={"cognitive_lane": "observer_reaction", "router_reason": "post_observer"}):
        with raw_trace.worker_role_context("observer"):
            response = client._brokered_post(base_url="http://127.0.0.1:6011/v1", model_name="deepseek-v4-flash",
                                             messages=[{"role": "user", "content": "DRAFT"}], max_tokens=20,
                                             timeout_seconds=10, url="http://127.0.0.1:6011/v1/chat/completions",
                                             headers={"Authorization": "Bearer sk-test"},
                                             json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "DRAFT"}], "max_tokens": 20})
    assert response.status_code == 200
    events = _events(tmp_path)
    assert events[0]["raw_request_json"] == sent[0]
    assert events[0]["raw_request_json"]["messages"][0]["content"] == "ACTUAL"
    assert events[0]["raw_request_json"]["max_tokens"] == 3
    assert events[0]["worker_role"] == "observer"
    assert events[0]["lane"] == "post_observer"
    assert events[0]["cognitive_lane"] == "observer_reaction"


def test_not_logging_non_deepseek_transport(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))
    from llm import client
    from runtime.inference_broker import inference_context
    monkeypatch.setattr(client, "GLOBAL_INFERENCE_BROKER", SimpleNamespace(
        request_from_context=lambda **kwargs: SimpleNamespace(estimated_input_tokens=30, reserved_output_tokens=20),
        run=lambda request, callback: callback(inference_request=request),
        record_response_headers=lambda *args, **kwargs: None,
    ))
    monkeypatch.setattr(client, "_record_pressure_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: SimpleNamespace(status_code=200, text="{}", headers={}))
    with inference_context("transport"):
        client._brokered_post(base_url="http://localhost:1234/v1", model_name="qwen3.6",
                              messages=[{"role": "user", "content": "test"}], max_tokens=20, timeout_seconds=10,
                              url="http://localhost:1234/v1/chat/completions", json={"messages": []})
    assert _events(tmp_path) == []


def test_direct_tts_deepseek_localizer_is_traced(monkeypatch, tmp_path):
    """Không được bỏ sót request DeepSeek gọi trực tiếp ngoài llm/client."""
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))
    monkeypatch.setenv("ERIS_TTS_LOCALIZER_BASE_URL", "https://api.deepseek.com")
    from runtime import speech_localizer
    import requests

    class LocalizerResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"こんにちは"}}]}'
        def raise_for_status(self):
            return None
        def json(self):
            return {"choices": [{"message": {"content": "こんにちは"}}]}
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: LocalizerResponse())
    output = speech_localizer._translate_with_deepseek_api(
        api_key="test-private-token", model_name="deepseek-v4-flash",
        system_instruction="Dịch sang tiếng Nhật", prompt="Eris có ý kiến riêng", timeout_seconds=2,
    )
    assert output["choices"][0]["message"]["content"] == "こんにちは"
    events = _events(tmp_path)
    assert len(events) == 2
    assert events[0]["worker_role"] == "tts_localizer"
    assert events[0]["raw_request_json"]["messages"][1]["content"] == "Eris có ý kiến riêng"
    assert events[1]["raw_response_text"] == LocalizerResponse.text
    assert "test-private-token" not in str(events)


def test_viewer_filters_one_role_and_prints_complete_raw(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))
    for role in ("observer", "surface"):
        with raw_trace.worker_role_context(role):
            attempt = raw_trace.begin_http_attempt(
                url="http://localhost/v1/chat/completions", model="deepseek-v4-flash",
                body={"messages": [{"role": "user", "content": "đầy đủ-" + role}]},
                headers={}, lane="post_observer", cognitive_lane="reaction",
            )
            attempt.response(SimpleNamespace(status_code=200, text="phản hồi-" + role))
    from tools.view_deepseek_raw_trace import main
    assert main([str(tmp_path), "--role", "observer", "--full"]) == 0
    output = capsys.readouterr().out
    assert "đầy đủ-observer" in output
    assert "phản hồi-observer" in output
    assert "đầy đủ-surface" not in output
