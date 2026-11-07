from __future__ import annotations

from types import SimpleNamespace


class _Response:
    status_code = 200
    reason = "OK"
    url = "http://localhost:6011/v1/chat/completions"
    headers = {}
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}]}


def _invoke_local(monkeypatch, *, lane: str, reason: str, system: str = "You are ERIS.\n\nDynamic context"):
    import llm.client as client_module
    from runtime.inference_broker import inference_context

    captured = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(client_module, "_brokered_post", fake_post)
    client = client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1",
        api_key="sk-test",
        model_name="deepseek-v4-flash",
        key_pool=None,
    )
    with inference_context(lane, metadata={"router_reason": reason}):
        client.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "hi"},
            ],
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=32,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": reason, "strict": False, "schema": {"type": "object"}},
            },
        )
    return captured


def test_web_session_is_persistent_only_for_conversation_lanes(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "runtime-abc")

    chat = _invoke_local(
        monkeypatch,
        lane="chat_main",
        reason="architecture_c_public_reply",
    )
    digest = _invoke_local(
        monkeypatch,
        lane="memory_digest",
        reason="episode_summary",
    )

    assert "X-Session-ID" in chat["headers"]
    assert "chat_main" in chat["headers"]["X-Session-ID"]
    assert "X-Session-ID" not in digest["headers"]


def test_web_session_rolls_generation_when_grounding_or_role_changes(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "runtime-abc")

    first = _invoke_local(
        monkeypatch,
        lane="chat_main",
        reason="architecture_c_public_reply",
        system="You are ERIS.\n\nGrounding A",
    )["headers"]["X-Session-ID"]
    second = _invoke_local(
        monkeypatch,
        lane="chat_main",
        reason="architecture_c_public_reply",
        system="You are ERIS.\n\nGrounding B",
    )["headers"]["X-Session-ID"]
    other = _invoke_local(
        monkeypatch,
        lane="chat_main",
        reason="different_public_contract",
        system="You are ERIS.\n\nGrounding B",
    )["headers"]["X-Session-ID"]

    # Cùng role nhưng grounding mới không thể dùng session cũ, vì gateway cũ
    # sẽ chỉ gửi latest user message và bỏ mất grounding của request mới.
    assert first != second
    assert second != other


def test_web_session_mode_can_be_disabled_or_rolled_back(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "runtime-abc")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "off")
    disabled = _invoke_local(
        monkeypatch,
        lane="chat_main",
        reason="architecture_c_public_reply",
    )
    assert "X-Session-ID" not in disabled["headers"]

    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "legacy")
    legacy = _invoke_local(
        monkeypatch,
        lane="memory_digest",
        reason="episode_summary",
    )
    assert legacy["headers"]["X-Session-ID"] == "runtime-abc"


def test_context_compiler_does_not_feed_memory_archive_to_greeting() -> None:
    from runtime.public_reply_context import compile_public_reply_context

    route = {"reasons": ("simple_social_turn",)}
    compiled = compile_public_reply_context(
        route=route,
        personality="P" * 5000,
        relationship="R" * 5000,
        grounding="G" * 5000,
        memory="M" * 5000,
        discourse="D" * 5000,
    )

    assert compiled.memory == ""
    assert compiled.grounding == ""
    assert len(compiled.personality) <= 640
    assert len(compiled.relationship) <= 340
    assert compiled.total_chars < 2000


def test_context_compiler_keeps_evidence_for_technical_turn() -> None:
    from runtime.public_reply_context import compile_public_reply_context

    route = {"reasons": ("technical_depth",)}
    compiled = compile_public_reply_context(
        route=route,
        personality="personality",
        relationship="relationship",
        grounding="evidence\n" * 500,
        memory="memory\n" * 500,
        discourse="discourse\n" * 500,
    )

    assert compiled.grounding
    assert compiled.relationship == ""
    assert compiled.total_chars <= 9000


def test_required_greeting_cannot_silently_disappear() -> None:
    from runtime.reply_obligation import ReplyRequirement, enforce_reply_obligation
    from runtime.semantic_frame import SemanticFrame

    frame = SemanticFrame(user_act="greeting", target="assistant")
    result = enforce_reply_obligation(
        {"text": "", "should_speak": False, "semantic_frame": frame.to_dict()},
        user_text="xin chào",
        frame=frame,
        language_hint="vi",
    )

    assert result.requirement is ReplyRequirement.REQUIRED
    assert result.repaired is True
    assert result.payload["should_speak"] is True
    assert result.payload["text"] == "Chào. Tôi nhận được tin nhắn rồi."
    assert result.payload["speech_text"] == "こんにちは。メッセージは受け取ったよ。"
    assert result.payload["speech_language"] == "ja"


def test_required_reply_keeps_model_text_when_silence_flag_is_wrong() -> None:
    from runtime.reply_obligation import ReplyRequirement, enforce_reply_obligation
    from runtime.semantic_frame import SemanticFrame

    frame = SemanticFrame(user_act="relational_check_in", target="assistant")
    result = enforce_reply_obligation(
        {
            "text": "Ừ, tôi hơi khựng một nhịp. Không phải tôi biến mất đâu.",
            "speech_text": "うん、少し止まっただけ。消えたわけじゃない。",
            "speech_language": "ja",
            "should_speak": False,
            "semantic_frame": frame.to_dict(),
        },
        user_text="dị hả",
        frame=frame,
        language_hint="vi",
    )

    assert result.requirement is ReplyRequirement.REQUIRED
    assert result.repaired is True
    assert result.reason == "required_reply_should_speak"
    assert result.payload["should_speak"] is True
    assert result.payload["text"].startswith("Ừ, tôi hơi khựng")
    assert result.payload["speech_text"].startswith("うん、少し")


def test_public_turn_transaction_runs_side_effect_once() -> None:
    from runtime.public_turn_transaction import PublicTurnTransactionRegistry

    registry = PublicTurnTransactionRegistry()
    counter = SimpleNamespace(value=0)

    def operation():
        counter.value += 1
        return {"value": counter.value}

    first = registry.run_once("chat:1:assistant", operation)
    second = registry.run_once("chat:1:assistant", operation)

    assert first == second == {"value": 1}
    assert counter.value == 1


def test_public_turn_ledger_deduplicates_stable_turn_id() -> None:
    from runtime.public_turn_ledger import PublicTurnLedger

    ledger = PublicTurnLedger()
    first = ledger.append(
        channel="chat",
        text="Một câu.",
        parse_mode="json",
        turn_id="chat:1:assistant",
    )
    second = ledger.append(
        channel="chat",
        text="Một câu bị retry.",
        parse_mode="json",
        turn_id="chat:1:assistant",
    )

    assert first is second
    assert len(ledger.all()) == 1
    assert ledger.all()[0].text == "Một câu."
