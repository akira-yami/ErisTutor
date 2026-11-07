# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from types import SimpleNamespace

from llm.deepseek_workers import JsonlWorkerClient
from runtime.ai_orchestrated.model_router import (
    AIRuntimeModelRouter,
    PUBLIC_REPLY_CORE_SCHEMA,
    _json,
)


def test_direct_public_contract_chi_block_public_text() -> None:
    """Soft metadata không còn là điều kiện để một direct reply tồn tại."""

    # Không bắt writer điền domain/ref để được trả lời user. Claim manifest
    # vẫn có thể xuất hiện làm metadata nghiên cứu, nhưng không là hard gate.
    assert PUBLIC_REPLY_CORE_SCHEMA["required"] == ["should_speak", "text", "speech_text"]
    for key in ("semantic_frame", "assistant_act", "self_capability_claims", "claim_manifest"):
        assert key not in PUBLIC_REPLY_CORE_SCHEMA["required"]
        assert key in PUBLIC_REPLY_CORE_SCHEMA["properties"]
    assert "social_plan" not in PUBLIC_REPLY_CORE_SCHEMA["properties"]


def test_json_parser_salvages_text_even_when_metadata_exists() -> None:
    raw = json.dumps(
        {
            "semantic_frame": True,
            "assistant_act": False,
            "should_speak": True,
            "text": "Ừ, câu vừa rồi nghe máy thật.",
            "speech_text": "うん、さっきの言い方はかなり機械的だった。",
        },
        ensure_ascii=False,
    )
    parsed = _json(raw)
    assert parsed["text"] == "Ừ, câu vừa rồi nghe máy thật."
    assert parsed["semantic_frame"] is True


def test_compat_envelope_keeps_public_text_when_soft_metadata_is_malformed() -> None:
    router = AIRuntimeModelRouter(llm_service=None, logger=None)
    payload = router._compat_public_envelope(
        {
            "semantic_frame": True,
            "assistant_act": False,
            "social_plan": "không phải object",
            "self_capability_claims": False,
            "should_speak": True,
            "text": "Tôi sửa giọng ngay đây.",
            "speech_text": "すぐに話し方を直す。",
        }
    )
    assert payload["text"] == "Tôi sửa giọng ngay đây."
    assert payload["speech_text"] == "すぐに話し方を直す。"
    assert payload["should_speak"] is True
    assert payload["assistant_act"]["kind"] == "answer"


class _RawClient:
    model_name = "deepseek-v4-flash"
    base_url = "http://127.0.0.1:6011/v1"

    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        content = self.outputs.pop(0)
        return {"choices": [{"message": {"content": content}}]}


def test_jsonl_recursive_validator_repairs_wrong_required_type() -> None:
    schema = {
        "type": "object",
        "required": ["should_speak", "text", "speech_text"],
        "properties": {
            "should_speak": {"type": "boolean"},
            "text": {"type": "string"},
            "speech_text": {"type": "string"},
        },
    }
    raw = _RawClient([
        '{"should_speak":true,"text":false,"speech_text":"x"}',
        '{"should_speak":true,"text":"Được.","speech_text":"うん。"}',
    ])
    worker = JsonlWorkerClient(raw, role="companion", jsonl_enabled=True, repair_attempts=1)
    payload = worker.create_chat_completion(
        messages=[{"role": "user", "content": "test"}],
        response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": schema}},
    )
    assert json.loads(payload["choices"][0]["message"]["content"])["text"] == "Được."
    assert len(raw.calls) == 2
    assert "schema_type:$.text:string" in raw.calls[1]["messages"][-1]["content"]


def test_direct_writer_uses_core_schema_not_full_semantic_tree(monkeypatch) -> None:
    captured: dict = {}

    class Client:
        model_name = "unit-test-model"
        base_url = "http://localhost/v1"

        def create_chat_completion(self, **kwargs):
            captured.update(kwargs)
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps(
                            {
                                "should_speak": True,
                                "text": "Tôi trả lời được rồi.",
                                "speech_text": "ちゃんと返せるようになった。",
                                "claim_manifest": [],
                                # Cố tình metadata sai kiểu: public text vẫn phải sống.
                                "semantic_frame": True,
                            },
                            ensure_ascii=False,
                        )
                    }
                }]
            }

    router = AIRuntimeModelRouter(SimpleNamespace(client=Client(), fallback_client=None), logger=None)
    router._run_inference = lambda _tier, func, **_kwargs: func()
    monkeypatch.setenv("ERIS_PUBLIC_WRITER_DEADLINE_SEC", "11")
    payload = router.generate_public_reply(chat_history=[], user_text="đừng nói như máy nữa")

    schema = captured["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["should_speak", "text", "speech_text"]
    assert "social_plan" not in schema["required"]
    assert captured["timeout"] == 11.0
    assert 560 <= captured["max_tokens"] <= 1000
    assert payload["text"] == "Tôi trả lời được rồi."
    assert payload["should_speak"] is True


def test_direct_writer_jsonl_wrapper_does_not_repair_soft_metadata_bool() -> None:
    """Tái hiện lỗi thật: semantic_frame=bool không được làm mất câu public."""

    raw = _RawClient([
        json.dumps(
            {
                "should_speak": True,
                "text": "Không, lần này tôi nói thẳng hơn.",
                "speech_text": "今回はもっと直接言う。",
                "claim_manifest": [],
                "semantic_frame": True,
                "assistant_act": False,
            },
            ensure_ascii=False,
        )
    ])
    worker = JsonlWorkerClient(raw, role="companion", jsonl_enabled=True, repair_attempts=1)
    router = AIRuntimeModelRouter(SimpleNamespace(client=worker, fallback_client=None), logger=None)
    router._run_inference = lambda _tier, func, **_kwargs: func()

    payload = router.generate_public_reply(chat_history=[], user_text="bớt máy đi")

    assert payload["text"] == "Không, lần này tôi nói thẳng hơn."
    assert payload["speech_text"] == "今回はもっと直接言う。"
    assert payload["should_speak"] is True
    # Soft metadata sai kiểu được sanitize sau boundary, không kích hoạt JSONL repair.
    assert len(raw.calls) == 1
