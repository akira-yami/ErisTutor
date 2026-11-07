# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from types import SimpleNamespace

from llm.contracts import AUTONOMOUS_BEHAVIOR_PROMPT, RUNTIME_RULES_PROMPT
from llm.prompt_compaction import CACHEABLE_CHAT_PREFIX_V2
from llm.service import LlmService
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.eris_voice_contract import (
    VOICE_CONTRACT_VERSION,
    CHAT_VOICE_CORE_V38,
    CHAT_STYLE_RULES_V38,
    surface_voice_rules,
)
from runtime.public_reply_context import compile_public_reply_context


def test_v38_chat_contract_is_positive_infj_voice_not_shortness_target() -> None:
    prompt = AIRuntimeModelRouter._public_reply_prompt("vi", "ja")
    assert "giọng nói trực tiếp của ERIS" in prompt
    assert "AFFECTIVE_VOICE_STATE" in prompt
    assert "preset ấm/lạnh/điềm/restrained" in prompt
    assert "Độ dài đi theo nội dung" in prompt
    assert "public writer cho một lượt chat" not in prompt
    assert "one concise" not in prompt.lower()


def test_v38_cacheable_prefix_keeps_same_voice_before_dynamic_context() -> None:
    assert "VOICE V39" in CACHEABLE_CHAT_PREFIX_V2
    assert CHAT_VOICE_CORE_V38 in CACHEABLE_CHAT_PREFIX_V2
    assert "Độ dài đi theo nội dung" in CACHEABLE_CHAT_PREFIX_V2


def test_v38_surface_voice_rules_preserve_cognitive_shape() -> None:
    rules = "\n".join(surface_voice_rules(2))
    assert VOICE_CONTRACT_VERSION in rules
    assert "status ping" in rules
    assert "AFFECTIVE_VOICE_STATE" in rules
    assert "Không biến premise thành châm ngôn" in rules
    assert "do not optimize for brevity" in rules.lower()
    assert "sentence budget=2" in rules


def test_v38_legacy_autonomous_prompt_no_longer_defaults_to_cold_short_line() -> None:
    joined = RUNTIME_RULES_PROMPT + "\n" + AUTONOMOUS_BEHAVIOR_PROMPT
    assert "exactly one short Vietnamese line" not in joined
    assert "Style: lạnh vừa đủ, gọn, sắc" not in joined
    assert "no fixed emotional temperature" in joined
    assert "Do not optimize for shortness itself" in joined


def test_v38_chat_style_block_encodes_rhythm_and_guarded_warmth() -> None:
    service = object.__new__(LlmService)
    block = service._build_chat_style_block()
    assert "Kín chỉ nghĩa là tự bộc lộ có chọn lọc" in block
    assert "mood vui" in block
    assert "nhịp câu không hoàn toàn đối xứng" in block
    assert "Dry humor" in block
    assert all(rule in block for rule in CHAT_STYLE_RULES_V38)


def test_v38_technical_context_keeps_enough_personality_to_not_switch_persona() -> None:
    personality = "P" * 800
    compiled = compile_public_reply_context(
        route={"reasons": ["technical_depth"]},
        personality=personality,
        grounding="ground",
    )
    # V37 chỉ giữ ~260 ký tự nên phần guarded warmth/relational softening thường
    # bị cắt trước khi writer đọc tới. V38 giữ đủ một voice snapshot nhỏ.
    assert 500 <= len(compiled.personality) <= 540


def test_v38_surface_system_prompt_treats_followup_as_continuity_not_notification() -> None:
    """Voice contract vẫn cấm notification/châm-ngôn dù surface realizer cũ đã retire."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / "runtime" / "eris_voice_contract.py").read_text(encoding="utf-8")
    assert "notification template" in source
    assert "châm ngôn" in source
    assert not hasattr(AIRuntimeModelRouter, "_surface_realizer_prompt")


class _Client:
    model_name = "unit-test-model"
    base_url = "http://localhost/v1"

    def __init__(self) -> None:
        self.kwargs = None

    def create_chat_completion(self, **kwargs):
        self.kwargs = kwargs
        return {
            "choices": [{
                "message": {
                    "content": json.dumps(
                        {
                            "should_speak": True,
                            "text": "Ừ, chỗ này khô thật. Tôi sửa nhịp lại rồi.",
                            "speech_text": "うん、ここはちょっと硬かった。話し方を直したよ。",
                        },
                        ensure_ascii=False,
                    )
                }
            }]
        }


def test_v38_direct_writer_uses_slightly_more_wording_entropy_without_changing_authority() -> None:
    client = _Client()
    router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None), logger=None)
    router._run_inference = lambda _tier, func, **_kwargs: func()
    out = router.generate_public_reply(chat_history=[], user_text="nói tự nhiên hơn đi")
    assert out["should_speak"] is True
    assert client.kwargs["temperature"] == 0.56
    assert client.kwargs["response_format"]["json_schema"]["schema"]["required"] == [
        "should_speak", "text", "speech_text"
    ]


def test_v38_personality_seed_no_longer_equates_guarded_with_cold() -> None:
    from llm.contracts import STYLE_FALLBACK_PROMPT, SELF_IDENTITY_PROFILE_PROMPT
    from runtime.voice_policy import PROMPT_VOICE_CONSTRAINT

    assert "ấm rất ít" not in STYLE_FALLBACK_PROMPT
    assert "precision không yêu cầu đổi sang giọng lạnh" in STYLE_FALLBACK_PROMPT
    assert "cold precision in technical tasks" not in SELF_IDENTITY_PROFILE_PROMPT
    assert "same-person technical clarity" in SELF_IDENTITY_PROFILE_PROMPT
    assert "technical chat is still the same ERIS" in PROMPT_VOICE_CONSTRAINT
