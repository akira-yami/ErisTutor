from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from domain.decision import Decision
from llm import process_manager
from llm.service import LlmService
from runtime.ai_orchestrated.contracts import CognitiveObservation
from runtime.public_lifecycle import PublicLifecycleController
from runtime.stage_contracts import StageFrameBuilder
from runtime.utterance_spec import UtteranceSpec, degrade_surface_payload
from runtime.voice_profile_compiler import compile_voice_plan_from_payload


def _canonical_payload() -> dict:
    return {
        "should_speak": True,
        "utterance": {
            "turn_id": "turn_1842",
            "display": {
                "language": "vi",
                "text": "Tôi đang tính. Nước này không đơn giản.",
            },
            "speech": {
                "language": "ja",
                "text": "考えている。この手は単純じゃない。",
            },
            "performance": {
                "preset": "focused",
                "intensity": 0.55,
                "nonverbal": "small_breath",
            },
            "delivery": {
                "priority": "normal",
                "interruptible": True,
            },
        },
    }


def test_utterance_spec_parses_one_bilingual_commit():
    spec = UtteranceSpec.from_mapping(_canonical_payload())

    assert spec.turn_id == "turn_1842"
    assert spec.display.language == "vi"
    assert spec.display.text.startswith("Tôi đang tính")
    assert spec.speech.language == "ja"
    assert spec.speech.text.startswith("考えている")
    assert spec.performance.preset == "focused"
    assert spec.performance.intensity == 0.55
    assert spec.delivery.interruptible is True


def test_utterance_spec_recovers_mild_provider_shape_drift():
    string_spec = UtteranceSpec.from_mapping(
        {
            "utterance": "Ừ, tôi nghe đây.",
            "speech_text": "うん、聞いてる。",
        },
        default_display_language="vi",
        default_speech_language="ja",
    )
    flat_spec = UtteranceSpec.from_mapping(
        {
            "display_text": "Tôi đây.",
            "speech": "ここにいる。",
        },
        default_display_language="vi",
        default_speech_language="ja",
    )

    assert string_spec.display.text == "Ừ, tôi nghe đây."
    assert string_spec.speech.text == "うん、聞いてる。"
    assert flat_spec.display.text == "Tôi đây."
    assert flat_spec.speech.text == "ここにいる。"


def test_utterance_spec_does_not_label_display_fallback_as_japanese_speech():
    spec = UtteranceSpec.from_mapping(
        {"text": "Chào bạn. Tôi đây."},
        default_display_language="vi",
        default_speech_language="ja",
    )

    # Chưa có speech_text thật thì transcript chỉ là display fallback. Nó phải
    # mang ngôn ngữ thật (vi); Stage/TTS mới là nơi pin target ja và localize.
    assert spec.speech.text == "Chào bạn. Tôi đây."
    assert spec.speech.language == "vi"


def test_split_web_envelope_keeps_top_level_surfaces():
    spec = UtteranceSpec.from_mapping(
        {
            "display": {"text": "display survives"},
            "speech": {"text": "speech survives"},
            "utterance": {
                "performance": {
                    "preset": "casual_warm",
                    "intensity": 0.6,
                    "nonverbal": "none",
                }
            },
        }
    )

    assert spec.display.text == "display survives"
    assert spec.speech.text == "speech survives"


def test_structured_worker_parser_unwraps_schema_payload_without_public_salvage():
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    class Client:
        model_name = "deepseek-v4-flash"

        def create_chat_completion(self, **_kwargs):
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "turn_id": "probe-1",
                            "user_text": "echoed input",
                            "observation": {
                                "user_act": "direct_request",
                                "confidence": 0.82,
                                "notes": "kept",
                            },
                        })
                    }
                }]
            }

    schema = {
        "type": "object",
        "required": ["user_act", "confidence"],
        "properties": {
            "user_act": {"type": "string"},
            "confidence": {"type": "number"},
            "notes": {"type": "string"},
        },
    }
    payload = AIRuntimeModelRouter(SimpleNamespace())._call(
        Client(),
        system="return observation json",
        payload={"user_text": "probe"},
        schema=schema,
        name="observer_probe",
        max_tokens=128,
    )

    assert payload == {
        "user_act": "direct_request",
        "confidence": 0.82,
        "notes": "kept",
    }


def test_style_safety_rejects_new_question_intent():
    service = LlmService(object())

    assert service._style_rewrite_is_safe(
        "Original statement remains declarative.",
        "Original statement becomes a question?",
    ) is False
    assert service._style_rewrite_is_safe(
        "Toi chua the di nuoc.",
        "Toi chua the di nuoc, ki\u1ec3u g\u00ec.",
    ) is False


def test_web_reply_parser_blocks_malformed_json_tail_from_public_text():
    from runtime.ai_orchestrated.model_router import _json

    payload = _json(
        'もう一回ね…深く吸って…ゆっくり吐いて…聞こえた？ 😌" }, '
        '"performance": { "preset": "warmtease" }, "responsemove": "direct_answer'
    )

    assert payload["text"] == "もう一回ね…深く吸って…ゆっくり吐いて…聞こえた？"
    assert "performance" not in payload["text"]
    assert "responsemove" not in payload["text"]
    assert all(ord(ch) <= 0xFFFF for ch in payload["text"])


def test_utterance_spec_strips_godot_unsafe_emoji_surrogates():
    spec = UtteranceSpec.from_mapping(
        {
            "text": "Ừ thì nghe đây 😏",
            "speech_text": "うん、聞いてる 😏",
        },
        default_display_language="vi",
        default_speech_language="ja",
    )

    assert spec.display.text == "Ừ thì nghe đây"
    assert spec.speech.text == "うん、聞いてる"
    assert all(ord(ch) <= 0xFFFF for ch in spec.display.text + spec.speech.text)


def test_stage_uses_committed_speech_without_localizer():
    class FailingLocalizer:
        def localize(self, *_args, **_kwargs):
            raise AssertionError("canonical UtteranceSpec must not call a localizer")

    payload = _canonical_payload()
    payload.update(UtteranceSpec.from_mapping(payload).to_flat_payload())
    decision = Decision(
        text=payload["text"],
        animation="stand",
        speech_text=payload["speech_text"],
        speech_language=payload["speech_language"],
        display_language=payload["display_language"],
        performance_hint=payload["performance_hint"],
        performance_intensity=payload["performance_intensity"],
        nonverbal_cue=payload["nonverbal_cue"],
        utterance_turn_id=payload["utterance_turn_id"],
    )

    frame = StageFrameBuilder(localizer=FailingLocalizer(), language_hint="ja").build(
        decision,
        payload=payload,
        channel="chat",
    )

    assert frame.display_text == "Tôi đang tính. Nước này không đơn giản."
    assert frame.tts_text == "考えている。この手は単純じゃない。"
    assert frame.memory_text == frame.display_text
    assert frame.turn_id == "turn_1842"
    assert frame.speech_contract["language"] == "ja"
    assert frame.speech_contract["prelocalized"] is True


def test_stage_pins_japanese_speech_contract_even_if_payload_downgrades_it():
    payload = _canonical_payload()
    payload["speech_text"] = "Xin chào."
    payload["speech_language"] = "vi"
    payload["utterance"]["speech"]["text"] = "Xin chào."
    payload["utterance"]["speech"]["language"] = "vi"

    decision = Decision(
        text="Xin chào.",
        animation="stand",
        speech_text="Xin chào.",
        speech_language="vi",
        display_language="vi",
    )

    frame = StageFrameBuilder(language_hint="ja").build(
        decision,
        payload=payload,
        channel="chat",
    )

    assert frame.speech_contract["language"] == "ja"
    assert frame.tts_text == "Xin chào."
    assert frame.speech_contract["can_translate"] is True
    assert frame.speech_contract.get("prelocalized") is not True


def test_post_commit_rewrite_degrades_both_surfaces_honestly():
    rewritten = degrade_surface_payload(
        _canonical_payload(),
        text="Không. Tôi vẫn nghĩ vậy.",
        language="vi",
        reason="deterministic_stance_guard",
        performance_preset="soft_warm",
    )

    assert rewritten["text"] == "Không. Tôi vẫn nghĩ vậy."
    assert rewritten["speech_text"] == rewritten["text"]
    assert rewritten["speech_language"] == "vi"
    assert rewritten["utterance_degraded"] is True
    assert rewritten["utterance_degraded_reason"] == "deterministic_stance_guard"


def test_voice_profile_is_compiled_locally_from_performance():
    plan = compile_voice_plan_from_payload(_canonical_payload())

    assert plan["global_style"] == "focused"
    assert plan["source"] == "deterministic_voice_profile_compiler_v1"
    assert 0.0 <= plan["delivery"]["energy"] <= 1.0


def test_lifecycle_keeps_thinking_until_speaking_and_rejects_regression():
    events: list[tuple[str, str]] = []

    class Owner:
        def _emit_stage_protocol_state(self, event_type, *, reason, data):
            events.append((event_type, data["state"]))

    lifecycle = PublicLifecycleController(Owner())
    lifecycle.begin(turn_id="turn-1", channel="chat", received=True)
    assert lifecycle.transition("CONTENT_COMMITTED", turn_id="turn-1")
    assert lifecycle.transition("AUDIO_BUFFERING", turn_id="turn-1")
    assert lifecycle.transition("SPEAKING", turn_id="turn-1")
    assert lifecycle.transition("AUDIO_BUFFERING", turn_id="turn-1") is False
    assert lifecycle.transition("DONE", turn_id="turn-1")
    assert lifecycle.interrupt() is False

    assert ("thinking_started", "COMPOSING") in events
    assert ("content_committed", "CONTENT_COMMITTED") in events
    assert ("audio_buffering", "AUDIO_BUFFERING") in events
    assert ("thinking_finished", "SPEAKING") in events
    assert ("speaking", "SPEAKING") in events


def test_deepseek_web_provider_builds_only_companion_worker(monkeypatch):
    companion = object()
    manager = process_manager.RemoteModelProcessManager(
        logger=logging.getLogger("deepseek-web-test"),
        project_root=".",
    )
    calls: list[tuple[str, bool]] = []

    def build(role: str, *, required: bool = False):
        calls.append((role, required))
        return companion

    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek_web")
    monkeypatch.setattr(manager, "build_deepseek_worker_client", build)
    monkeypatch.setattr(
        process_manager,
        "load_worker_spec",
        lambda _role: SimpleNamespace(base_url="http://127.0.0.1:6011/v1", model="deepseek-v4-flash"),
    )

    assert manager._build_remote_client() is companion
    assert calls == [("companion", True)]


def test_web_semantic_turn_uses_one_deadlined_remote_attempt(monkeypatch):
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    class Client:
        model_name = "deepseek-v4-flash"
        base_url = "http://127.0.0.1:6011/v1"

        def __init__(self, content: str):
            self.content = content
            self.calls: list[dict] = []

        def create_chat_completion(self, **kwargs):
            self.calls.append(kwargs)
            return {"choices": [{"message": {"content": self.content}}]}

    primary = Client("{}")
    fallback = Client('{"text":"late fallback must not run"}')
    service = SimpleNamespace(client=primary, fallback_client=fallback)
    router = AIRuntimeModelRouter(service)

    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek_web")
    monkeypatch.setenv("ERIS_MAIN_SEMANTIC_DEADLINE_SEC", "12")
    router.generate_public_reply(
        chat_history=[],
        user_text="nội dung kiểm thử không thuộc fast form 937",
        allow_fallback=True,
    )

    assert len(primary.calls) == 1
    assert primary.calls[0]["timeout"] == 12.0
    assert fallback.calls == []


def test_casual_memory_does_not_trigger_personality_reflection():
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    observation = CognitiveObservation(
        turn_id="turn-casual-memory",
        turn_seq=1,
        event_kind="chat",
        confidence=0.9,
        memory_worthy=True,
        relational_delta=0.2,
        playful_safety_delta=0.2,
    )

    assert AIRuntimeModelRouter._should_reflect_personality(observation) is False


def test_utterance_spec_cleans_japanese_brackets_from_display_text():
    raw_payload = {
        "text": (
            "Vậy thì không cần ép mình làm gì cả. Chỉ là chiều thứ Sáu, có thể ngồi yên một chút cũng được, "
            "hoặc nếu bạn thích, tôi có thể gợi ý vài thứ linh tinh. "
            "「じゃあ、無理に何かしなくていいよ。金曜の夕方、ただぼんやりしてるのもありだし、もし何か思いついたら教えてくれる？」"
        )
    }
    spec = UtteranceSpec.from_mapping(raw_payload, default_display_language="vi", default_speech_language="ja")

    assert "「" not in spec.display.text
    assert spec.display.text.startswith("Vậy thì không cần ép mình")
    assert spec.speech.text.startswith("じゃあ、無理に何かしなくていいよ")
