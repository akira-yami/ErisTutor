"""Hồi quy session Whale2API: tính đúng của request quan trọng hơn tái sử dụng."""
from __future__ import annotations

import pytest

from llm.web_session_guard import WebSessionGuard


def _reserve(guard, messages, *, contract=None, credential="sk-test"):
    return guard.reserve(
        base_session_id="runtime:chat:contract",
        base_url="http://localhost:6011/v1",
        model="deepseek-v4-flash",
        credential=credential,
        messages=messages,
        request_contract=contract or {"response_format": {"type": "json_object"}},
    )


def test_reuses_only_verified_append_only_transcript():
    guard = WebSessionGuard()
    first_messages = [
        {"role": "system", "content": "Eris: Root chỉ thị A"},
        {"role": "user", "content": "Câu hỏi đầu tiên"},
    ]
    first = _reserve(guard, first_messages)
    guard.commit(first, "Trả lời 1")

    next_messages = first_messages + [
        {"role": "assistant", "content": "Trả lời 1"},
        {"role": "user", "content": "Câu hỏi tiếp theo"},
    ]
    second = _reserve(guard, next_messages)
    assert second.session_id == first.session_id
    guard.commit(second, "Trả lời 2")

    third = _reserve(guard, next_messages + [
        {"role": "assistant", "content": "Trả lời 2"},
        {"role": "user", "content": "Vẫn là phiên đó"},
    ])
    assert third.session_id == first.session_id


@pytest.mark.parametrize("replacement", [
    [{"role": "system", "content": "Root chỉ thị MỚI"}, {"role": "user", "content": "Câu hỏi mới"}],
    [{"role": "system", "content": "Root chỉ thị cũ"}, {"role": "user", "content": "Câu hỏi cũ"}, {"role": "user", "content": "Thêm câu hỏi"}],
    [{"role": "system", "content": "Root chỉ thị cũ"}, {"role": "user", "content": "Câu hỏi cũ"}, {"role": "assistant", "content": "Câu trả lời BỊ SỬA"}, {"role": "user", "content": "Câu hỏi mới"}],
])
def test_forks_when_request_is_not_exact_conversation_continuation(replacement):
    guard = WebSessionGuard()
    first = _reserve(guard, [
        {"role": "system", "content": "Root chỉ thị cũ"},
        {"role": "user", "content": "Câu hỏi cũ"},
    ])
    guard.commit(first, "Câu trả lời cũ")
    candidate = _reserve(guard, replacement)
    assert candidate.session_id != first.session_id


def test_changes_to_schema_and_provider_account_force_new_session():
    guard = WebSessionGuard()
    messages = [{"role": "user", "content": "Câu hỏi"}]
    first = _reserve(guard, messages)
    guard.commit(first, "JSON 1")
    follow = messages + [
        {"role": "assistant", "content": "JSON 1"},
        {"role": "user", "content": "Câu hỏi mới"},
    ]
    schema_changed = _reserve(guard, follow, contract={"response_format": {"type": "text"}})
    assert schema_changed.session_id != first.session_id
    other_account = _reserve(guard, follow, credential="sk-other")
    assert other_account.session_id != first.session_id


def test_failed_attempt_and_parallel_request_cannot_poison_remote_state():
    guard = WebSessionGuard()
    messages = [{"role": "user", "content": "Xin chào"}]
    first = _reserve(guard, messages)
    # Lượt hai đến trước khi lượt đầu thành công: không được cùng session.
    concurrent = _reserve(guard, messages)
    assert concurrent.session_id != first.session_id
    guard.commit(first, "Câu này tới chậm")  # Không được ghi đè phiên mới.
    guard.abort(concurrent)
    retry = _reserve(guard, messages)
    assert retry.session_id not in {first.session_id, concurrent.session_id}
    guard.commit(retry, "Đã trả lời")
    next_turn = _reserve(guard, messages + [
        {"role": "assistant", "content": "Đã trả lời"},
        {"role": "user", "content": "Tiếp tục"},
    ])
    assert next_turn.session_id == retry.session_id
    guard.abort(next_turn)
    after_cancel = _reserve(guard, messages)
    assert after_cancel.session_id != retry.session_id


def test_e2e_openai_client_passes_full_prompt_and_forks_on_new_root(monkeypatch):
    import llm.client as client_module
    from runtime.inference_broker import inference_context

    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "test-run")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "isolated")
    requests_sent = []

    class Response:
        headers = {}
        status_code = 200
        reason = "OK"
        url = "http://localhost:6011/v1/chat/completions"
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ACK"}, "finish_reason": "stop"}]}

    def fake_post(**kwargs):
        requests_sent.append(kwargs)
        return Response()

    monkeypatch.setattr(client_module, "_brokered_post", fake_post)
    client = client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1",
        api_key="sk-e2e-test",
        model_name="deepseek-v4-flash",
    )

    def send(messages):
        with inference_context("chat_main", metadata={"router_reason": "public_replies"}):
            client.create_chat_completion(
                messages=messages, temperature=0.0, frequency_penalty=0.0,
                presence_penalty=0.0, max_tokens=32,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "RootOutput", "schema": {"type": "object"}
                }},
            )

    first = [{"role": "system", "content": "Root A"}, {"role": "user", "content": "Câu 1"}]
    send(first)
    follow = first + [{"role": "assistant", "content": "ACK"}, {"role": "user", "content": "Câu 2"}]
    send(follow)
    new_root = [{"role": "system", "content": "Root B"}, {"role": "user", "content": "Câu 3"}]
    send(new_root)

    # Payload phải giữ nguyên hội thoại ngoài chỉ thị schema được bổ sung.
    sent = [r["json"]["messages"] for r in requests_sent]
    assert [m[1:] for m in sent] == [m[1:] for m in (first, follow, new_root)]
    assert [m[0]["content"].split("\n\n", 1)[0] for m in sent] == ["Root A", "Root A", "Root B"]
    assert all("JSON Schema:" in m[0]["content"] for m in sent)
    sessions = [r["headers"]["X-Session-ID"] for r in requests_sent]
    assert sessions[0] == sessions[1]
    assert sessions[2] != sessions[1]


def test_off_mode_leaves_request_stateless(monkeypatch):
    import llm.client as client_module
    from runtime.inference_broker import inference_context

    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "test-run")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "off")
    recorded = {}

    class Response:
        headers = {}
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ACK"}, "finish_reason": "stop"}]}

    def fake_post(**kwargs):
        recorded.update(kwargs)
        return Response()

    monkeypatch.setattr(client_module, "_brokered_post", fake_post)
    client = client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1", api_key="sk-off", model_name="deepseek-v4-flash",
    )
    with inference_context("chat_main"):
        client.create_chat_completion(
            messages=[{"role": "system", "content": "Root B"}, {"role": "user", "content": "Câu hỏi mới"}],
            temperature=0.0, frequency_penalty=0.0, presence_penalty=0.0,
            max_tokens=32,
        )
    assert "X-Session-ID" not in recorded["headers"]
    assert recorded["json"]["messages"][1]["content"] == "Câu hỏi mới"


def test_schema_change_with_same_name_is_not_discarded(monkeypatch):
    import llm.client as client_module
    from runtime.inference_broker import inference_context

    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "schema-run")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "isolated")
    seen = []

    class Response:
        status_code = 200
        headers = {}
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}]}

    def post(**kwargs):
        seen.append(kwargs)
        return Response()

    monkeypatch.setattr(client_module, "_brokered_post", post)
    client = client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1", api_key="sk-schema", model_name="deepseek-v4-flash",
    )

    def send(field):
        with inference_context("chat_main", metadata={"router_reason": "schema_test"}):
            client.create_chat_completion(
                messages=[{"role": "user", "content": "Trả dữ liệu theo schema"}],
                temperature=0.0, frequency_penalty=0.0, presence_penalty=0.0,
                max_tokens=128, response_format={"type": "json_schema", "json_schema": {
                    "name": "Output", "schema": {"type": "object", "properties": {field: {"type": "boolean"}}}
                }},
            )

    send("ok")
    send("success")
    assert seen[0]["headers"]["X-Session-ID"] != seen[1]["headers"]["X-Session-ID"]
    assert '"ok"' in seen[0]["json"]["messages"][0]["content"]
    assert '"success"' in seen[1]["json"]["messages"][0]["content"]
    assert seen[0]["json"]["response_format"] == {"type": "json_object"}
    assert seen[1]["json"]["response_format"] == {"type": "json_object"}


def test_broker_cannot_silently_trim_persistent_history(monkeypatch):
    """Session đã xác nhận không được mang lịch sử khác với prompt thực gửi."""
    from types import SimpleNamespace
    import llm.client as client_module

    flags = []

    class FakeBroker:
        def request_from_context(self, **kwargs):
            flags.append(kwargs["metadata"]["input_degrade_supported"])
            return SimpleNamespace(
                estimated_input_tokens=kwargs["estimated_input_tokens"],
                reserved_output_tokens=kwargs["reserved_output_tokens"],
            )

        def run(self, request, send):
            return "ok"

    monkeypatch.setattr(client_module, "GLOBAL_INFERENCE_BROKER", FakeBroker())
    kwargs = dict(
        base_url="http://localhost:6011/v1", model_name="deepseek-v4-flash",
        messages=[{"role": "user", "content": "Câu hỏi thực"}],
        max_tokens=100, timeout_seconds=3,
        url="http://localhost:6011/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Câu hỏi thực"}], "max_tokens": 100},
    )
    client_module._brokered_post(headers={"X-Session-ID": "session-verified"}, **kwargs)
    client_module._brokered_post(headers={}, **kwargs)
    assert flags == [False, True]
