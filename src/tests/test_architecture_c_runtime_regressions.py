# -*- coding: utf-8 -*-
"""Regression tests cho các lỗi thấy trong log 2026-07-19.

Các test cố ý không gọi mạng. Chúng bảo vệ semantic observer, discovery scheduler,
cooperative preemption và key-pool breaker khỏi quay lại lối vá bằng từ khóa.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

from cortex.tutor.config import TutorConfig
from cortex.tutor.contracts import TutorQuestion
from llm.key_pool import ApiKeyPool
from llm.tutor_backend import GeminiWebBackend, TutorCancelled
from runtime.ai_orchestrated.discovery import DiscoveryPlan, DiscoveryRuntime
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.intention_authority import IntentionObserver


def test_intention_observer_does_not_turn_banter_into_execution_debt() -> None:
    observer = IntentionObserver()
    harmless = (
        "Thì khỏi làm màu. Nói lười nói chuyện ý tứ là đủ ý rồi.",
        "Tôi không cần bày đặt văn vẻ làm gì, nói thẳng là được.",
        "Bạn chỉ cần mở miệng một câu, là tui đã thấy ngày mình đỡ nhạt đi.",
        "Nếu bạn cũng muốn, thì mình thử xem hợp không.",
        "Tôi sẽ nói ngắn hơn.",
    )
    for text in harmless:
        result = observer.validate(text, {})
        assert result.missing_contract == (), (text, result.to_payload())


def test_intention_observer_still_catches_real_external_claims() -> None:
    observer = IntentionObserver()
    for text in (
        "Tôi sẽ mở file config rồi sửa key cho bạn.",
        "Để tôi kiểm tra repo này.",
        "Tôi đã xóa file đó.",
        "Bạn chỉ cần mở miệng, tôi sẽ sửa file này.",
        "Tôi nói thật, tôi sẽ mở file.",
        "Tôi không mở file A, tôi sẽ mở file B.",
    ):
        result = observer.validate(text, {})
        assert set(result.missing_contract) == {"target", "handler", "result"}


def test_intention_observer_scopes_subject_and_negation_per_clause() -> None:
    observer = IntentionObserver()
    harmless = (
        "Tôi không mở file đâu.",
        "Bạn mở miệng một câu, tui thấy vui.",
        "Tui sến xíu với bạn nè: bạn chỉ cần mở miệng một câu, là tui đã thấy ngày mình đỡ nhạt đi.",
    )
    for text in harmless:
        result = observer.validate(text, {})
        assert result.missing_contract == (), (text, result.to_payload())


def test_key_pool_breaker_stops_before_exhausting_every_key(monkeypatch) -> None:
    monkeypatch.delenv("ERIS_KEY_POOL_429_BURST_KEYS", raising=False)
    monkeypatch.setenv("ERIS_KEY_POOL_429_GLOBAL_COOLDOWN_S", "2")
    pool = ApiKeyPool([f"key-{i}" for i in range(9)], provider="groq")

    attempts = []
    while True:
        lease = pool.acquire()
        if lease is None:
            break
        attempts.append(lease.index)
        may_rotate = pool.mark_rate_limited(
            10,
            lease=lease,
            reason="http_429:llama-3.1-8b-instant",
        )
        if not may_rotate:
            break

    assert attempts == [0, 1, 2]
    assert pool.stats()["rate_limit_bursts"] == 1
    assert pool.stats()["available"] == 0


class _Router:
    def __init__(self) -> None:
        self.calls = 0

    def plan_discovery(self, _snapshot):
        self.calls += 1
        return DiscoveryPlan()


class _Host:
    chat_mode = False
    _chat_processing = False
    _chat_last_activity_at = 0.0
    tutor_daemon = None


class _BlockingDaemon:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.started = threading.Event()
        self.last_research_bundle = {}
        self.last_answer_ts = 0.0
        self._eris_discovery_meta = {}

    def search_now(self, *_args, **_kwargs):
        self.started.set()
        self.cancelled.wait(2.0)
        return None

    def cancel_pending(self, *, reason: str = "") -> bool:
        self.cancelled.set()
        return True


def test_discovery_does_not_start_immediately_after_boot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        router = _Router()
        runtime = DiscoveryRuntime(_Host(), router, state_path=Path(tmp) / "state.json")
        runtime.maybe_tick({}, now=time.time())
        assert router.calls == 0


def test_discovery_is_preempted_by_chat_and_not_archived() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        host = _Host()
        daemon = _BlockingDaemon()
        host.tutor_daemon = daemon
        runtime = DiscoveryRuntime(host, _Router(), state_path=Path(tmp) / "state.json")
        plan = DiscoveryPlan(
            lane="fresh_curiosity",
            topic="test topic",
            needs_external=True,
            freshness_required=True,
            verification_required=True,
            eris_interest=0.8,
            user_relevance=0.7,
            novelty=0.8,
            importance=0.8,
            confidence=0.9,
            reason="test",
        )
        runtime._start_search(plan, now=time.time())
        assert daemon.started.wait(1.0)
        assert runtime.preempt_for_chat(reason="test_chat") is True
        runtime._job_thread.join(2.0)

        state = runtime.snapshot()
        assert state.current_activity.status == "cancelled"
        assert state.records == ()


class _CancelableSession:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def goto(self, _url: str) -> None:
        return None

    def is_blocked(self) -> bool:
        return False

    def submit_question(self, _text: str, *, delay_min_ms: int, delay_max_ms: int, cancel_event=None) -> None:
        return None

    def read_answer(self, *, stable_window_s: float, deadline: float, cancel_event=None) -> str:
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise TutorCancelled()
            time.sleep(0.01)
        return "late answer"

    def citations(self):
        return ()


def test_tutor_backend_supports_cooperative_cancel() -> None:
    backend = GeminiWebBackend(
        TutorConfig(enabled=True, episode_timeout_s=2.0),
        page_session_factory=lambda: _CancelableSession(),
    )
    question = TutorQuestion(text="test question", gap_id="gap")
    result = {}

    thread = threading.Thread(target=lambda: result.setdefault("answer", backend.ask(question)))
    thread.start()
    time.sleep(0.05)
    backend.cancel_current("chat")
    thread.join(1.0)

    assert result["answer"].fail_reason == "cancelled"


def test_observer_anchor_overrides_generic_feedback_unknown_frame() -> None:
    from runtime.ai_orchestrated.contracts import CognitiveObservation

    unknown = CognitiveObservation.from_mapping(
        {},
        turn_id="turn-style",
        turn_seq=4,
        source="groq_observer_fallback",
        latency_ms=4400,
    )
    anchored = AIRuntimeModelRouter._apply_deterministic_observation_anchor(
        unknown,
        user_text="raw text is deliberately ignored",
        assistant_text="Ừ, vì câu đó generic thật.",
        event_kind="chat",
        semantic_frame={
            "version": "semantic_frame.v1",
            "discourse_relation": "style_feedback",
            "user_act": "style_criticism",
            "target": "assistant",
        },
    )

    assert anchored.user_act == "style_criticism"
    assert anchored.repair_needed is True
    assert anchored.confidence >= 0.76
    assert "deterministic_anchor" in anchored.source


def test_chat_start_preserves_tts_until_final_interrupt_classification() -> None:
    """P0: chat_start không chứa chữ, không được suy user đang yêu cầu stop."""
    from runtime.chat_cycle import ChatCycleMixin

    class _Logger:
        def debug(self, *_args, **_kwargs):
            return None

        def info(self, *_args, **_kwargs):
            return None

    class _ChatHost(ChatCycleMixin):
        def __init__(self) -> None:
            self.chat_mode = False
            self.logger = _Logger()
            self.ai_discovery_runtime = None
            self.reasons: list[str] = []

        def _supersede_tts_for_new_user_turn(self, reason: str) -> bool:
            self.reasons.append(reason)
            return True

    host = _ChatHost()
    host._enter_chat_cycle()
    assert host.chat_mode is True
    assert host.reasons == []
    # Khi đã phân loại được user thật sự chen lời, mới huỷ đúng một lần.
    host._preempt_for_classified_user_turn(cancel_audio=True)
    assert host.reasons == ["user_chat_supersede"]


def test_conversation_profile_uses_independent_tts_turn_manager() -> None:
    from runtime.stage_profiles import stage_profile

    profile = stage_profile("conversation")
    assert profile.task_manager_enabled is True
    assert profile.prefetch_workers == 1


def test_japanese_localizer_fails_closed_in_strict_mode(monkeypatch) -> None:
    from runtime.speech_localizer import SpeechLocalizationError, SpeechLocalizer

    localizer = SpeechLocalizer()
    monkeypatch.setattr(
        localizer,
        "_translate_to_japanese",
        lambda *_args, **_kwargs: "Tôi vẫn là tiếng Việt",
    )
    try:
        localizer.localize(
            "Không có gì để nói thì cứ thả một câu lửng.",
            language_hint="ja",
            strict=True,
        )
    except SpeechLocalizationError as exc:
        assert "japanese_localization" in str(exc)
    else:
        raise AssertionError("strict Japanese contract accepted Vietnamese transcript")


def test_japanese_localizer_translate_path_uses_runtime_timeout(monkeypatch) -> None:
    import contextlib
    import sys
    import types

    from runtime.speech_localizer import SpeechLocalizer, _localizer_timeout_seconds

    monkeypatch.setenv("ERIS_TOKEN_BUDGET_ALL_LANES", "0")
    monkeypatch.setenv("ERIS_TTS_LOCALIZER_PROVIDER", "gemini")
    monkeypatch.setenv("ERIS_TTS_LOCALIZER_TIMEOUT_SEC", "1.5")
    monkeypatch.setenv("ERIS_TTS_API_KEY", "dummy-test-key")

    class _Result:
        text = "こんにちは"

    class _Models:
        def generate_content(self, model, contents, config=None):
            self.model = model
            self.contents = contents
            self.config = config
            return _Result()

    class _Client:
        last = None

        def __init__(self, api_key):
            self.api_key = api_key
            self.models = _Models()
            _Client.last = self

    google_module = types.ModuleType("google")
    google_module.genai = types.SimpleNamespace(Client=_Client)
    monkeypatch.setitem(sys.modules, "google", google_module)

    import runtime.inference_broker as broker_module
    import runtime.speech_localizer as speech_localizer

    captured_context = {}

    def _fake_inference_context(*args, **kwargs):
        captured_context["args"] = args
        captured_context["kwargs"] = kwargs
        return contextlib.nullcontext()

    monkeypatch.setattr(broker_module, "inference_context", _fake_inference_context)
    monkeypatch.setattr(speech_localizer, "_valid_japanese_translation", lambda *_args, **_kwargs: True)

    class _Broker:
        def request_from_context(self, **kwargs):
            self.request_kwargs = kwargs
            return object()

        def run(self, request, func):
            self.request = request
            return func()

    fake_broker = _Broker()
    monkeypatch.setattr(broker_module, "GLOBAL_INFERENCE_BROKER", fake_broker)

    localizer = SpeechLocalizer()
    out = localizer._translate_to_japanese("Hello", prosody_style="")
    assert out == "こんにちは"
    assert fake_broker.request_kwargs["timeout_seconds"] == 1.5
    assert captured_context["kwargs"]["optional"] is False
    assert _localizer_timeout_seconds() == 1.5
    assert _Client.last is not None
    assert "Preserve the exact meaning" in _Client.last.models.config["system_instruction"]


def test_japanese_localizer_default_timeout_requires_open_model_hygiene_friendly(monkeypatch) -> None:
    from runtime.speech_localizer import _localizer_timeout_seconds

    monkeypatch.delenv("ERIS_TTS_LOCALIZER_TIMEOUT_SEC", raising=False)
    assert _localizer_timeout_seconds() == 60.0


def test_tts_worker_repairs_invalid_prelocalized_japanese_surface(monkeypatch) -> None:
    import runtime.tts_engine as tts_engine

    class _Localizer:
        def __init__(self) -> None:
            self.called = False

        def localize(self, _text: str, **_kwargs) -> str:
            self.called = True
            return "話すことがなくても、無理に話さなくていいよ。"

    worker = tts_engine._TtsWorker.__new__(tts_engine._TtsWorker)
    worker._language_hint = "ja"
    worker._localizer = _Localizer()
    request = tts_engine._SpeakRequest(
        "Không có gì để nói thì khỏi ép.",
        prelocalized=True,
        language_hint="ja",
    )
    monkeypatch.setattr(tts_engine, "_prelocalize_japanese_enabled", lambda: True)

    text, localized, language = worker._resolve_main_transcript(request, request.text)
    # ``prelocalized`` là ownership claim, không phải giấy phép bỏ qua language
    # invariant. Transcript Việt đội nhãn ja phải được repair trước khi phát.
    assert worker._localizer.called is True
    assert text == "話すことがなくても、無理に話さなくていいよ。"
    assert localized is True
    assert language == "ja"


def test_tts_worker_keeps_valid_prelocalized_japanese_without_second_model_call(monkeypatch) -> None:
    import runtime.tts_engine as tts_engine

    class _Localizer:
        def __init__(self) -> None:
            self.called = False

        def localize(self, _text: str, **_kwargs) -> str:
            self.called = True
            return "SHOULD NOT RUN"

    worker = tts_engine._TtsWorker.__new__(tts_engine._TtsWorker)
    worker._language_hint = "ja"
    worker._localizer = _Localizer()
    request = tts_engine._SpeakRequest(
        "話すことがなくても、無理に話さなくていいよ。",
        prelocalized=True,
        language_hint="ja",
    )
    monkeypatch.setattr(tts_engine, "_prelocalize_japanese_enabled", lambda: True)

    text, localized, language = worker._resolve_main_transcript(request, request.text)
    assert worker._localizer.called is False
    assert text == request.text
    assert localized is True
    assert language == "ja"


def test_tts_localizer_uses_speech_model_when_no_override(monkeypatch) -> None:
    from runtime.speech_localizer import localizer_model_name

    monkeypatch.setenv("ERIS_TTS_LOCALIZER_PROVIDER", "gemini")
    monkeypatch.delenv("ERIS_TTS_LOCALIZER_MODEL", raising=False)
    monkeypatch.setenv("ERIS_SPEECH_MODEL", "gemma-4-31b-it")

    assert localizer_model_name() == "gemma-4-31b-it"


def test_deepseek_tts_localizer_ignores_speech_model(monkeypatch) -> None:
    from runtime.speech_localizer import localizer_model_name, localizer_provider_name

    monkeypatch.setenv("ERIS_TTS_LOCALIZER_PROVIDER", "deepseek")
    monkeypatch.delenv("ERIS_TTS_LOCALIZER_MODEL", raising=False)
    monkeypatch.setenv("ERIS_REMOTE_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ERIS_SPEECH_MODEL", "gemma-4-31b-it")

    assert localizer_provider_name() == "deepseek"
    assert localizer_model_name() == "deepseek-v4-flash"


def test_deepseek_tts_localizer_translation_path(monkeypatch) -> None:
    import contextlib

    from runtime.speech_localizer import SpeechLocalizer

    monkeypatch.setenv("ERIS_TOKEN_BUDGET_ALL_LANES", "0")
    monkeypatch.setenv("ERIS_TTS_LOCALIZER_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
    monkeypatch.setenv("ERIS_REMOTE_MODEL", "deepseek-v4-flash")

    import runtime.inference_broker as broker_module
    import runtime.speech_localizer as speech_localizer

    monkeypatch.setattr(speech_localizer, "_valid_japanese_translation", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        speech_localizer,
        "_translate_with_deepseek_api",
        lambda **_kwargs: {"choices": [{"message": {"content": "こんにちは"}}]},
    )
    captured_context = {}

    def _fake_inference_context(*args, **kwargs):
        captured_context["args"] = args
        captured_context["kwargs"] = kwargs
        return contextlib.nullcontext()

    monkeypatch.setattr(broker_module, "inference_context", _fake_inference_context)

    class _Broker:
        def request_from_context(self, **kwargs):
            self.request_kwargs = kwargs
            return object()

        def run(self, request, func):
            return func()

    fake_broker = _Broker()
    monkeypatch.setattr(broker_module, "GLOBAL_INFERENCE_BROKER", fake_broker)

    out = SpeechLocalizer()._translate_to_japanese("Hello", prosody_style="")

    assert out == "こんにちは"
    assert fake_broker.request_kwargs["provider"] == "deepseek"
    assert fake_broker.request_kwargs["model"] == "deepseek-v4-flash"
    assert captured_context["kwargs"]["optional"] is False


def test_vision_compressor_disabled_returns_empty_note() -> None:
    from cortex.vision_semantic_compressor import VisionCompressorConfig, VisionSemanticCompressor

    compressor = VisionSemanticCompressor(VisionCompressorConfig(enabled=False))
    note = compressor.compress(ocr_text="settings window")
    assert note.visible_context == ""
    assert note.debug["disabled_reason"] == "vision_compressor_disabled"
    assert note.evidence[0] == "vision_compressor_disabled"


def test_tts_worker_fails_closed_on_japanese_localizer_breaks_by_default(monkeypatch) -> None:
    import pytest
    import runtime.tts_engine as tts_engine
    from runtime.speech_localizer import SpeechLocalizationError

    class _BrokenLocalizer:
        def localize(self, _text: str, **_kwargs) -> str:
            raise SpeechLocalizationError("japanese_localization_failed:ClientError")

    worker = tts_engine._TtsWorker.__new__(tts_engine._TtsWorker)
    worker._language_hint = "ja"
    worker._localizer = _BrokenLocalizer()
    request = tts_engine._SpeakRequest(
        "Tôi vẫn nói được bằng tiếng Việt.",
        prelocalized=False,
        language_hint="ja",
    )
    monkeypatch.setattr(tts_engine, "_prelocalize_japanese_enabled", lambda: True)

    with pytest.raises(SpeechLocalizationError):
        worker._resolve_main_transcript(request, request.text)

    monkeypatch.setenv("ERIS_TTS_FAIL_OPEN_ON_LOCALIZATION_ERROR", "1")
    transcript, prelocalized, language = worker._resolve_main_transcript(request, request.text)

    assert transcript == request.text
    assert prelocalized is False
    assert language == "vi"


def test_tts_worker_uses_critic_key_rescue_when_primary_returns_non_japanese(monkeypatch) -> None:
    import runtime.speech_localizer as speech_localizer

    monkeypatch.setenv("ERIS_TTS_LOCALIZER_PROVIDER", "deepseek")
    monkeypatch.setenv("ERIS_TTS_LOCALIZER_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "primary-key")
    monkeypatch.setenv("ERIS_DS_CRITIC_API_KEY", "critic-key")
    monkeypatch.setenv("ERIS_TTS_FAIL_OPEN_ON_LOCALIZATION_ERROR", "0")

    monkeypatch.setattr(
        speech_localizer,
        "_valid_japanese_translation",
        lambda _source, translated, **_kwargs: translated == "こんにちは",
    )
    monkeypatch.setattr(speech_localizer, "_apply_signature_surface", lambda text, _policy: text)
    monkeypatch.setattr(
        speech_localizer,
        "_get_api_key_lease",
        lambda **_kwargs: ("primary-key", None, None),
    )

    calls: list[str] = []

    def fake_translate(*, api_key: str, **_kwargs):
        calls.append(api_key)
        content = "Tôi đây." if api_key == "primary-key" else "こんにちは"
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(speech_localizer, "_translate_with_deepseek_api", fake_translate)

    out = speech_localizer.SpeechLocalizer()._translate_to_japanese("Tôi đây.", prosody_style="")

    assert out == "こんにちは"
    assert calls == ["primary-key", "critic-key"]


def test_tts_attempts_default_is_bounded(monkeypatch) -> None:
    import runtime.tts_engine as tts_engine

    monkeypatch.delenv("ERIS_TTS_MAX_ATTEMPTS", raising=False)
    assert tts_engine._tts_max_attempts() == 4


def test_tts_broker_provider_is_key_scoped() -> None:
    from types import SimpleNamespace

    import runtime.tts_engine as tts_engine

    assert tts_engine._tts_broker_provider_name(SimpleNamespace(index=0)) == "gemini_tts_key_0"
    assert tts_engine._tts_broker_provider_name(SimpleNamespace(index=1)) == "gemini_tts_key_1"
    assert tts_engine._tts_broker_provider_name(None) == "gemini_tts"


def test_tts_broker_timeout_allows_slow_audio_admission(monkeypatch) -> None:
    import runtime.tts_engine as tts_engine

    monkeypatch.delenv("ERIS_TTS_BROKER_TIMEOUT_SEC", raising=False)
    assert tts_engine._tts_broker_timeout_seconds() == 45.0

    monkeypatch.setenv("ERIS_TTS_BROKER_TIMEOUT_SEC", "2")
    assert tts_engine._tts_broker_timeout_seconds() == 5.0

    monkeypatch.setenv("ERIS_TTS_BROKER_TIMEOUT_SEC", "999")
    assert tts_engine._tts_broker_timeout_seconds() == 120.0


def test_tts_current_turn_is_required_broker_lane() -> None:
    from runtime.inference_broker import lane_policy

    assert lane_policy("tts_current_turn").optional is False


def test_tts_manager_new_turn_does_not_wait_for_cancelled_http_request() -> None:
    from types import SimpleNamespace

    from runtime.tts_task_manager import TtsTaskManager

    class _Backend:
        def __init__(self) -> None:
            self.first_started = threading.Event()
            self.release_first = threading.Event()
            self.second_played = threading.Event()
            self.calls = 0

        def synthesize(self, text: str, **_kwargs) -> bytes:
            self.calls += 1
            if text == "old turn":
                self.first_started.set()
                self.release_first.wait(1.0)
                return b"old"
            return b"new"

        def play_audio(self, wav_bytes: bytes, _stop_event, on_started=None) -> None:
            if on_started is not None:
                on_started()
            if wav_bytes == b"new":
                self.second_played.set()

    def frame(turn_id: str, text: str):
        return SimpleNamespace(
            turn_id=turn_id,
            segment_id=0,
            tts_text=text,
            interruptible=True,
            prosody_style="",
            prosody_notes="",
            speech_contract={"language": "ja", "locale": "ja-JP", "prelocalized": True},
            delivery={},
            audio_asset="",
            emotion="neutral",
            motion="none",
        )

    backend = _Backend()
    manager = TtsTaskManager(prefetch_workers=1, max_prefetch_segments=1)
    assert manager.dispatch_frames(
        [frame("old", "old turn")],
        backend=backend,
        valence=0.0,
        arousal=0.2,
        stance="",
    )
    assert backend.first_started.wait(0.5)

    assert manager.dispatch_frames(
        [frame("new", "new turn")],
        backend=backend,
        valence=0.0,
        arousal=0.2,
        stance="",
    )
    assert backend.second_played.wait(0.5)
    backend.release_first.set()
    manager.shutdown()


def test_tts_key_lane_reservation_prevents_concurrent_reuse() -> None:
    from runtime.tts_key_health import TtsKeyLaneHealth

    health = TtsKeyLaneHealth()
    assert health.try_reserve(2) is True
    assert health.try_reserve(2) is False
    assert 2 in health.excluded_indices()
    assert health.snapshot()["2"]["in_flight"] == 1

    health.release(2)
    assert 2 not in health.excluded_indices()
    assert health.try_reserve(2) is True
    health.release(2)


def test_speech_persona_polish_is_subtle_and_style_aware(monkeypatch) -> None:
    from runtime.speech_persona_polish import resolve_speech_persona_policy

    monkeypatch.setenv("ERIS_TTS_SPEECH_PERSONA_POLISH", "1")
    monkeypatch.setenv("ERIS_TTS_SPEECH_PERSONA_WARMTH", "0.18")
    monkeypatch.setenv("ERIS_TTS_SPEECH_PERSONA_CUTENESS", "0.10")

    neutral = resolve_speech_persona_policy("Không có gì để nói thì cứ im cũng được.")
    technical = resolve_speech_persona_policy(
        "Mở Settings rồi đổi Language sang ja.", prosody_style="focused"
    )
    soft = resolve_speech_persona_policy(
        "Cứ thả một câu lửng, tôi bắt nhịp tiếp.", prosody_style="soft_tease"
    )

    assert neutral.enabled is True
    assert neutral.profile == "infj_5w4_subtle"
    # V39: neutral TTS không còn phủ warm-base cố định lên mọi mood.
    assert neutral.warmth == 0.0
    assert neutral.cuteness == 0.0
    assert technical.cuteness == 0.0
    assert 0.0 <= technical.warmth <= 0.16
    assert soft.warmth > neutral.warmth
    assert soft.cuteness > neutral.cuteness


def test_speech_persona_gate_blocks_unearned_intimacy_and_anime_baby_talk() -> None:
    from runtime.speech_persona_polish import (
        resolve_speech_persona_policy,
        translation_respects_persona_boundaries,
    )

    source = "Không có gì để nói thì cứ im một chút cũng được."
    policy = resolve_speech_persona_policy(source)

    assert translation_respects_persona_boundaries(
        source,
        "話すことがなくても、少し黙っていてもいいよ。",
        policy,
    )
    assert not translation_respects_persona_boundaries(
        source,
        "ずっとそばにいるよ。あなたのことが大好きだよ。",
        policy,
    )
    assert not translation_respects_persona_boundaries(
        source,
        "えへへ、ご主人様。黙っていてもいいんだよぉ。",
        policy,
    )


def test_speech_persona_gate_allows_relationship_words_when_source_contains_them() -> None:
    from runtime.speech_persona_polish import (
        resolve_speech_persona_policy,
        translation_respects_persona_boundaries,
    )

    source = "Tôi thích bạn, nhưng đừng vội làm quá lên."
    policy = resolve_speech_persona_policy(source)
    assert policy.allows_relationship_escalation is True
    assert translation_respects_persona_boundaries(
        source,
        "あなたのことは好き。でも、まだ大げさにしないで。",
        policy,
    )


def test_localizer_cache_key_changes_with_persona_policy(monkeypatch) -> None:
    import runtime.speech_persona_polish as persona_polish
    from runtime.speech_localizer import SpeechLocalizer

    localizer = SpeechLocalizer()
    calls: list[str] = []
    warmth = {"value": 0.18}

    def fake_translate(text: str, **kwargs) -> str:
        calls.append(kwargs["persona_policy"].cache_key)
        return "話すことがなくても、無理に話さなくていいよ。"

    def fake_runtime_float(name: str, default: float) -> float:
        if name == "speech_persona_warmth":
            return warmth["value"]
        return default

    monkeypatch.setattr(localizer, "_translate_to_japanese", fake_translate)
    monkeypatch.setattr(persona_polish, "_runtime_float", fake_runtime_float)
    first = localizer.localize("Không có gì để nói thì khỏi ép.", language_hint="ja", strict=True)
    warmth["value"] = 0.28
    second = localizer.localize("Không có gì để nói thì khỏi ép.", language_hint="ja", strict=True)

    assert first == second
    assert len(calls) == 2
    assert calls[0] != calls[1]


def test_signature_ending_desu_ne_is_deterministic_and_context_aware(monkeypatch) -> None:
    import runtime.speech_persona_polish as persona_polish

    monkeypatch.setattr(
        persona_polish,
        "_runtime_bool",
        lambda name, default: True if name == "signature_ending_enabled" else default,
    )
    monkeypatch.setattr(
        persona_polish,
        "_runtime_text",
        lambda name, default: "desu_ne" if name == "signature_ending" else default,
    )
    monkeypatch.setattr(
        persona_polish,
        "_runtime_float",
        lambda name, default: 1.0 if name == "signature_ending_rate" else default,
    )

    source = "Không có gì để nói thì cứ im một chút cũng được."
    first = persona_polish.resolve_speech_persona_policy(source)
    second = persona_polish.resolve_speech_persona_policy(source)
    question = persona_polish.resolve_speech_persona_policy("Bạn đang nghĩ gì vậy?")
    technical = persona_polish.resolve_speech_persona_policy(
        "Mở Settings rồi đổi Language sang ja.",
        prosody_style="focused",
    )

    assert first.signature_ending == "desu_ne"
    assert first.signature_surface in {"ですね", "だね", "よね"}
    assert first.signature_ending_selected is True
    assert first.signature_ending_rate == 1.0
    assert second.cache_key == first.cache_key
    assert "ですね" in first.prompt_directive()
    assert question.signature_ending_selected is False
    assert technical.signature_ending_selected is False


def test_signature_ending_can_be_disabled_without_disabling_persona(monkeypatch) -> None:
    import runtime.speech_persona_polish as persona_polish

    monkeypatch.setattr(
        persona_polish,
        "_runtime_bool",
        lambda name, default: False if name == "signature_ending_enabled" else default,
    )

    policy = persona_polish.resolve_speech_persona_policy("Tôi cũng nghĩ vậy.")
    assert policy.enabled is True
    assert policy.signature_ending == "off"
    assert policy.signature_ending_selected is False
    assert "Do not deliberately add ですね" in policy.prompt_directive()


def test_localizer_strips_standalone_signature_marker() -> None:
    from runtime.speech_localizer import _clean_translation_output

    assert _clean_translation_output("少しタイミングがずれているみたいだよ。だね。") == "少しタイミングがずれているみたいだよね。"
    assert _clean_translation_output("少しタイミングがずれているみたいです。ですね。") == "少しタイミングがずれているみたいですね。"
    assert _clean_translation_output("少し見ていました。だね。") == "少し見ていました。"
    assert _clean_translation_output("少しタイミングがずれているみたいだね。") == "少しタイミングがずれているみたいだね。"


def test_localizer_applies_signature_surface_when_safe(monkeypatch) -> None:
    import runtime.speech_persona_polish as persona_polish
    from runtime.speech_localizer import _apply_signature_surface

    monkeypatch.setattr(
        persona_polish,
        "_runtime_bool",
        lambda name, default: True if name == "signature_ending_enabled" else default,
    )
    monkeypatch.setattr(
        persona_polish,
        "_runtime_float",
        lambda name, default: 1.0 if name == "signature_ending_rate" else default,
    )
    policy = persona_polish.resolve_speech_persona_policy("Tôi hiểu rồi.")
    assert _apply_signature_surface("ちゃんと理解しているよ。", policy) == "ちゃんと理解しているよね。"
    assert _apply_signature_surface("ちゃんと理解しているだね。", policy) == "ちゃんと理解しているだね。"


def test_tts_prompt_emphasizes_only_actual_desu_ne_signature(monkeypatch) -> None:
    import runtime.tts_engine as tts_engine

    monkeypatch.setattr(tts_engine, "_pace_directive", lambda: "")
    with_signature = tts_engine._build_tts_prompt(
        "まあ、そうですね。",
        "",
        "ja",
        prelocalized=True,
    )
    without_signature = tts_engine._build_tts_prompt(
        "まあ、そう思います。",
        "",
        "ja",
        prelocalized=True,
    )

    assert "Signature ending performance" in with_signature
    assert "140–220ms" in with_signature
    assert "Signature ending performance" not in without_signature
    assert "Signature ending performance" in tts_engine._build_tts_prompt(
        "少しタイミングがずれているみたいだね。",
        "",
        "ja",
        prelocalized=True,
    )


def test_hardening_fingerprint_covers_signature_ending_runtime() -> None:
    from runtime.hardening_release import DEFAULT_AUTHORITY_FILES

    assert "runtime/speech_persona_polish.py" in DEFAULT_AUTHORITY_FILES
    assert "runtime/speech_localizer.py" in DEFAULT_AUTHORITY_FILES
    assert "runtime/tts_engine.py" in DEFAULT_AUTHORITY_FILES
    assert "runtime/runtime_settings_store.py" in DEFAULT_AUTHORITY_FILES


def test_startup_grounding_uses_fresh_telemetry_tab_evidence() -> None:
    from types import SimpleNamespace

    from runtime.ai_orchestrated.startup_grounding import collect_startup_grounding
    from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot

    now = time.time()
    block = ScreenTextBlock(
        block_id="telemetry-status",
        text="TTS queue: idle | Observer 429 burst: cooldown 30s",
        source="uia",
        confidence=0.86,
        window_title="Eris - Telemetry",
        captured_at=now,
    )
    snapshot = ScreenTextSnapshot(
        window=ActiveWindowInfo(title="Eris - Telemetry"),
        blocks=(block,),
        captured_at=now,
        revision=1,
        backend="uia_only",
    )

    class _Worker:
        def __init__(self) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

    class _Store:
        def snapshot(self):
            return snapshot

    class _Telemetry:
        def capture_snapshot(self, **_kwargs):
            return SimpleNamespace(
                active_window="Eris - Telemetry",
                active_app="Eris",
                focus_summary="Telemetry panel with runtime status",
            )

    host = SimpleNamespace(
        screen_text_worker=_Worker(),
        screen_evidence_store=_Store(),
        telemetry_service=_Telemetry(),
        mood_score=0,
        inferred_pos_x=0.5,
        chat_mode=False,
        _chat_processing=False,
    )

    bundle = collect_startup_grounding(host, timeout_seconds=0.0)
    assert host.screen_text_worker.started is True
    assert bundle.has_direct_screen_evidence is True
    assert bundle.has_strong_opening_anchor is True
    assert bundle.telemetry_surface_visible is True
    assert bundle.evidence_refs[0] == "screen:telemetry-status"
    assert "runtime:active_app" in bundle.evidence_refs
    assert "TTS queue" in bundle.prompt_context()
    assert "low-risk" in bundle.prompt_context()


def test_grounded_startup_plan_forces_specific_screen_opening() -> None:
    from runtime.ai_orchestrated.contracts import IntentionPlan
    from runtime.ai_orchestrated.startup_grounding import (
        StartupGroundingBundle,
        ensure_grounded_startup_plan,
    )

    plan = IntentionPlan.silence(channel="autonomous", reason="planner_uncertain")
    bundle = StartupGroundingBundle(
        active_window="Eris - Telemetry",
        backend="uia_only",
        screen_revision=1,
        telemetry_surface_visible=True,
        evidence=(
            {
                "id": "status-1",
                "source": "uia",
                "text": "Observer 429 burst: cooldown 30s",
                "confidence": 0.86,
            },
        ),
    )

    grounded = ensure_grounded_startup_plan(plan, bundle, force_screen_opening=True)
    assert grounded.speak is True
    assert grounded.motive == "startup_opening"
    assert grounded.strategy == "grounded_environment_opening"
    assert grounded.confidence >= 0.78
    assert grounded.importance >= 0.62
    assert grounded.evidence_refs == ("screen:status-1",)
    assert "one concrete item" in grounded.topic
    assert "Do not invent" in grounded.topic


def test_startup_without_evidence_allows_persona_opening_without_fake_fact() -> None:
    from runtime.ai_orchestrated.startup_grounding import StartupGroundingBundle

    bundle = StartupGroundingBundle()
    context = bundle.prompt_context()
    assert "personality-led greeting is still allowed and expected" in context
    assert "must not fabricate user activity" in context
    assert bundle.has_strong_opening_anchor is False


def test_startup_without_any_evidence_forces_persona_plan_when_enabled() -> None:
    from runtime.ai_orchestrated.contracts import IntentionPlan
    from runtime.ai_orchestrated.startup_grounding import (
        StartupGroundingBundle,
        ensure_grounded_startup_plan,
    )

    plan = IntentionPlan.silence(channel="autonomous", reason="planner_unavailable")
    forced = ensure_grounded_startup_plan(
        plan,
        StartupGroundingBundle(),
        force_startup_speech=True,
    )

    assert forced.speak is True
    assert forced.motive == "startup_opening"
    assert forced.strategy == "persona_opening"
    assert forced.confidence >= 0.78
    assert forced.importance >= 0.62
    assert "own attitude" in forced.topic


def test_startup_primes_continuity_and_offers_opening_to_root() -> None:
    import json
    from types import SimpleNamespace

    from runtime.ai_orchestrated.orchestrator import AIAutonomousOrchestrator

    class _Planner:
        calls = 0

        def plan_autonomous(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("empty startup should not call main planner")

    planner = _Planner()
    submitted = []
    sent = []

    state = SimpleNamespace(
        personality=SimpleNamespace(panel_view=lambda: {}),
        personality_prompt_context=lambda: "",
        identity=SimpleNamespace(panel_view=lambda: {}),
        identity_prompt_context=lambda: "",
        relationship=SimpleNamespace(to_dict=lambda: {}, prompt_summary=lambda: ""),
        open_loops=(),
        latest_observation={},
    )

    def parse(raw: str):
        payload = json.loads(raw)
        return SimpleNamespace(
            text=str(payload.get("text", "") or ""),
            should_speak=bool(payload.get("should_speak", False)),
            performance_hint=str(payload.get("performance_hint", "") or ""),
            system_action="",
        )

    host = SimpleNamespace(
        logger=SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            debug=lambda *args, **kwargs: None,
        ),
        chat_mode=False,
        _chat_processing=False,
        _ai_startup_ran=False,
        ai_state_reducer=SimpleNamespace(snapshot=lambda: state, submit=submitted.append),
        ai_intention_planner=planner,
        _chat_history_for_llm=lambda limit=4: [],
        last_text="",
        parser=SimpleNamespace(parse=parse),
        ai_stage_planner=SimpleNamespace(apply=lambda *args, **kwargs: None),
        _log_raw_llm_output=lambda *args, **kwargs: None,
        _note_autonomous_spoken=lambda **kwargs: None,
        _send_decision_to_body=lambda decision, channel: sent.append((decision, channel)),
    )
    runtime = AIAutonomousOrchestrator(host)
    # Startup primes continuity, then root may explicitly choose NoReply.
    assert runtime.run_startup() is False
    assert planner.calls == 0
    assert submitted == []
    assert sent == []
    assert not hasattr(host, "last_autonomous_spoken_at")
    assert hasattr(host, "conversation_handoff_runtime")
    assert host.last_conversation_run["status"] == "no_reply"
    assert host.last_conversation_run["decision"]["kind"] == "no_reply"


def test_startup_runtime_signal_beats_persona_when_screen_is_empty() -> None:
    from runtime.ai_orchestrated.contracts import IntentionPlan
    from runtime.ai_orchestrated.startup_grounding import (
        StartupGroundingBundle,
        ensure_grounded_startup_plan,
    )

    bundle = StartupGroundingBundle(
        runtime_evidence=(
            {
                "id": "recent_event_1",
                "source": "runtime",
                "text": "TTS recovered after cooldown",
                "confidence": 0.82,
            },
        ),
    )
    forced = ensure_grounded_startup_plan(
        IntentionPlan.silence(channel="autonomous", reason="planner_unavailable"),
        bundle,
        force_startup_speech=True,
    )

    assert bundle.has_runtime_opening_anchor is True
    assert forced.strategy == "grounded_runtime_opening"
    assert forced.evidence_refs == ("runtime:recent_event_1",)


def test_startup_continuity_beats_persona_without_screen_or_runtime() -> None:
    from runtime.ai_orchestrated.contracts import IntentionPlan
    from runtime.ai_orchestrated.startup_grounding import (
        StartupGroundingBundle,
        ensure_grounded_startup_plan,
    )

    forced = ensure_grounded_startup_plan(
        IntentionPlan.silence(channel="autonomous", reason="planner_unavailable"),
        StartupGroundingBundle(),
        force_startup_speech=True,
        has_continuity_anchor=True,
    )

    assert forced.strategy == "continuity_opening"
    assert "real continuity anchor" in forced.topic


def test_startup_persona_fallback_is_never_blank() -> None:
    from runtime.ai_orchestrated.startup_grounding import build_startup_fallback_text

    text = build_startup_fallback_text(language="vi")
    assert text.strip()
    assert "cần gì cứ nói" not in text.lower()


def test_screen_router_recognizes_telemetry_tab_capability_question() -> None:
    from screen.screen_evidence_store import ScreenEvidenceStore
    from screen.screen_relevance_router import ScreenRelevanceRouter

    route = ScreenRelevanceRouter(ScreenEvidenceStore()).route(
        "em đọc được tab telemetry tôi chưa"
    )
    assert route.references_screen is True
    assert route.reason != "no_screen_reference"


def test_hardening_fingerprint_covers_startup_screen_grounding() -> None:
    from runtime.hardening_release import DEFAULT_AUTHORITY_FILES

    assert "runtime/ai_orchestrated/orchestrator.py" in DEFAULT_AUTHORITY_FILES
    assert "runtime/ai_orchestrated/model_router.py" in DEFAULT_AUTHORITY_FILES
    assert "runtime/ai_orchestrated/startup_grounding.py" in DEFAULT_AUTHORITY_FILES
    assert "screen/screen_relevance_router.py" in DEFAULT_AUTHORITY_FILES


def test_startup_grounding_uses_telemetry_content_metadata_when_worker_is_sparse() -> None:
    from types import SimpleNamespace

    from runtime.ai_orchestrated.startup_grounding import collect_startup_grounding
    from screen.evidence_models import ActiveWindowInfo, ScreenTextSnapshot

    screen_snapshot = ScreenTextSnapshot(
        window=ActiveWindowInfo(title="Desktop"),
        blocks=(),
        captured_at=time.time(),
        revision=1,
        backend="uia_only",
    )

    class _Worker:
        def start(self) -> None:
            return None

    class _Store:
        def snapshot(self):
            return screen_snapshot

    class _Telemetry:
        def capture_snapshot(self, **_kwargs):
            return SimpleNamespace(
                active_window="Eris - Telemetry",
                active_app="Eris",
                focus_summary="Telemetry panel",
                content_metadata={
                    "source": "ocr_cache",
                    "confidence": 0.82,
                    "privacy_level": "normal",
                    "visible_text_sample": "TTS latency 240 ms | queue idle",
                },
            )

    host = SimpleNamespace(
        screen_text_worker=_Worker(),
        screen_evidence_store=_Store(),
        telemetry_service=_Telemetry(),
        mood_score=0,
        inferred_pos_x=0.5,
        chat_mode=False,
        _chat_processing=False,
    )

    bundle = collect_startup_grounding(host, timeout_seconds=0.0)
    assert bundle.active_window == "Eris - Telemetry"
    assert bundle.telemetry_surface_visible is True
    assert bundle.has_strong_opening_anchor is True
    assert bundle.evidence[0]["source"] == "ocr_cache"
    assert "TTS latency" in bundle.evidence[0]["text"]


def test_relationship_panel_contract_uses_real_ledger_and_belief_evidence() -> None:
    """Panel quan hệ không được tự bịa drift hoặc biến baseline thành live state."""

    from runtime.ai_orchestrated.contracts import RuntimeMindState
    from runtime.ai_orchestrated.identity import IdentityProfile
    from runtime.ai_orchestrated.panel_adapter import build_ai_panel_payload

    now = time.time()
    identity = IdentityProfile.from_mapping(
        {
            "beliefs": {
                "user.tolerates_disagreement": {
                    "strength": 0.61,
                    "confidence": 0.72,
                    "evidence_ids": ["turn-8", "turn-12", "turn-15"],
                    "contradictions": 1,
                    "last_turn_seq": 15,
                }
            },
            "relationships": {
                "primary_user": {
                    "relational_trust": 0.55,
                    "competence_trust": 0.66,
                    "respect": 0.62,
                    "closeness": 0.41,
                }
            },
            "ledger": [
                {
                    "timestamp": now - 3600,
                    "turn_seq": 15,
                    "dimension": "relationship",
                    "key": "relational_trust",
                    "applied_delta": 0.012,
                    "evidence": ["User tiếp tục tranh luận thay vì rời đi."],
                },
                {
                    "timestamp": now - 1800,
                    "turn_seq": 15,
                    "dimension": "belief",
                    "key": "user.tolerates_disagreement",
                    "applied_delta": 0.008,
                    "evidence": ["Bất đồng không làm cuộc trò chuyện kết thúc."],
                    "self_interpretation": "Tranh luận không đồng nghĩa bị xóa.",
                },
            ],
        }
    )
    state = RuntimeMindState(
        identity=identity,
        last_applied_turn_seq=15,
        latest_observation={
            "event_kind": "chat",
            "user_act": "disagreement",
            "relationship_meaning": "trust",
            "confidence": 0.84,
            "identity_signals": [
                {
                    "dimension": "relationship",
                    "key": "relational_trust",
                    "delta": 0.2,
                    "confidence": 0.82,
                    "context": "disagreement_without_exit",
                    "evidence": ["User vẫn tiếp tục trao đổi."],
                }
            ],
            "self_interpretation": {"summary": "Bất đồng này không phải từ chối."},
            "evidence": ["User tiếp tục trao đổi."],
        },
    )

    payload = build_ai_panel_payload(state)["identity_runtime"]["relationship_runtime"]
    metric = next(row for row in payload["metrics"] if row["key"] == "relational_trust")
    belief = next(row for row in payload["beliefs"] if row["key"] == "user.tolerates_disagreement")

    assert metric["delta_7d"] == 0.012
    assert metric["delta_30d"] == 0.012
    assert metric["has_history"] is True
    assert belief["status"] == "mixed"
    assert belief["evidence_count"] == 3
    assert belief["contradiction_count"] == 1
    assert "Bất đồng không làm" in belief["evidence_summaries"][0]
    assert payload["live"]["changed"] is True
    assert payload["live"]["signals"][0]["key"] == "relational_trust"


def test_relationship_panel_contract_marks_default_beliefs_as_baseline() -> None:
    from runtime.ai_orchestrated.contracts import RuntimeMindState
    from runtime.ai_orchestrated.panel_adapter import build_ai_panel_payload

    payload = build_ai_panel_payload(RuntimeMindState())["identity_runtime"]["relationship_runtime"]
    default_belief = next(row for row in payload["beliefs"] if row["key"] == "user.respects_independence")

    assert default_belief["status"] == "baseline"
    assert default_belief["evidence_count"] == 0
    assert payload["live"]["changed"] is False


def test_discovery_panel_payload_keeps_real_readiness_and_archive_counts() -> None:
    """Panel phải nhận readiness thật, không đoán từ vị trí hay màu card."""

    from runtime.ai_orchestrated.discovery import DiscoveryRecord, DiscoveryState

    with tempfile.TemporaryDirectory() as tmp:
        runtime = DiscoveryRuntime(_Host(), _Router(), state_path=Path(tmp) / "state.json")
        record = DiscoveryRecord(
            discovery_id="disc-panel-1",
            lane="fresh_curiosity",
            topic="Một chủ đề đã kiểm chứng",
            summary="Tóm tắt thật",
            eris_take="Điều đáng kể là cách dữ kiện này đổi góc nhìn.",
            share_readiness=0.83,
            novelty=0.77,
            eris_interest=0.88,
            user_relevance=0.69,
            verified=True,
            created_at=time.time(),
        )
        runtime._state = DiscoveryState(version=1, records=(record,), updated_at=time.time())

        panel = runtime.panel_view()
        ready = panel["ready_to_share"][0]

        assert ready["discovery_id"] == "disc-panel-1"
        assert ready["share_readiness"] == 0.83
        assert ready["novelty"] == 0.77
        assert ready["shared"] is False
        assert panel["archive_count"] == 1
        assert panel["verified_count"] == 1
        assert panel["ready_count"] == 1


def test_initiative_panel_exposes_typed_semantic_events() -> None:
    from runtime.ai_orchestrated.contracts import IntentionPlan
    from runtime.ai_orchestrated.initiative import InitiativeController
    from runtime.ai_orchestrated.semantic_contracts import TurnSemantics

    with tempfile.TemporaryDirectory() as tmp:
        controller = InitiativeController(state_path=Path(tmp) / "initiative.json")
        plan = IntentionPlan(
            plan_id="discovery:1", created_at=1_000.0, channel="autonomous",
            speak=True, topic="Một discovery đáng kể", importance=0.82, intrusion_risk=0.10,
            semantics=TurnSemantics(
                turn_kind="discovery", speech_act="share_verified_discovery",
                subject_key="eris_discovery_1", evidence_scope="discovery_artifact",
            ),
        )
        controller.record_spoken(plan, now=1_000.0)
        panel = controller.panel_view(now=1_100.0)

        assert panel["used_last_hour"] == 1
        assert panel["remaining_budget"] == max(0, panel["hourly_budget"] - 1)
        assert panel["next_allowed_at"] >= 1_000.0
        event = panel["recent_events"][0]
        assert event["kind"] == "discovery"
        assert event["speech_act"] == "share_verified_discovery"
        assert event["subject_key"] == "eris_discovery_1"

def test_discovery_runtime_panel_contract_separates_activity_ready_and_initiative() -> None:
    from runtime.ai_orchestrated.contracts import RuntimeMindState
    from runtime.ai_orchestrated.panel_adapter import build_ai_panel_payload

    now = time.time()
    discovery = {
        "archive_count": 3,
        "verified_count": 2,
        "shared_count": 1,
        "ready_count": 1,
        "current_activity": {
            "activity_id": "activity-1",
            "status": "verifying",
            "kind": "fresh_curiosity",
            "topic": "Tín hiệu mới",
            "detail": "Đang kiểm chứng nguồn.",
            "updated_at": now,
        },
        "ready_to_share": [
            {
                "discovery_id": "disc-2",
                "topic": "Artifact đã sẵn sàng",
                "eris_take": "Có một góc nhìn đáng kể.",
                "share_readiness": 0.81,
                "verified": True,
                "shared": False,
                "created_at": now - 60,
            }
        ],
        "recent": [],
        "authority": {
            "facts": "tutor_or_runtime_evidence",
            "selection_and_voice": "main_model",
        },
    }
    initiative = {
        "enabled": True,
        "hourly_budget": 2,
        "used_last_hour": 1,
        "remaining_budget": 1,
        "minimum_interval_seconds": 1200,
        "next_allowed_at": now - 1,
        "last_decision": {"allowed": True, "reason": "allowed", "at": now},
        "recent_events": [],
    }

    runtime = build_ai_panel_payload(RuntimeMindState(), discovery=discovery, initiative=initiative)["identity_runtime"]["discovery_runtime"]

    assert runtime["schema"] == "eris.discovery_runtime.panel.v1"
    assert runtime["headline"]["state"] == "active"
    assert runtime["activity"]["status"] == "verifying"
    assert runtime["ready"][0]["share_readiness"] == 0.81
    assert runtime["initiative"]["state"] == "open"
    assert next(step for step in runtime["flow"] if step["key"] == "research")["state"] == "active"


def test_startup_production_builds_real_greeting_frame_and_avoids_manifesto(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from runtime.ai_orchestrated.startup_grounding import (
        StartupGroundingBundle,
        build_startup_greeting_frame,
        choose_startup_text,
        select_startup_persona_preset,
    )

    monkeypatch.setenv("ERIS_GREETING_PRESET_HISTORY", str(tmp_path / "greeting_history.json"))

    host = SimpleNamespace(
        rolling_internal_state=None,
        _get_internal_state=lambda: SimpleNamespace(last_session_greeting_at=0.0),
    )
    state = SimpleNamespace(
        open_loops=(),
        latest_observation={},
        relationship=SimpleNamespace(to_dict=lambda: {}),
    )
    frame = build_startup_greeting_frame(
        host,
        state,
        StartupGroundingBundle(),
        now=1784628000.0,
    )

    assert frame.situation == "plain_return"
    assert frame.active_anchor is None
    assert frame.local_time
    preset = select_startup_persona_preset(frame)
    assert preset is not None
    assert str(preset["text"]).strip()
    assert "Tôi không hứa sẽ làm mọi thứ dịu đi" not in str(preset["text"])

    from dataclasses import replace

    continuity_frame = replace(
        frame,
        situation="resume_project",
        intent="resume_continuity",
        active_anchor="sửa lỗi TTS bị lệch",
        continuity_strength=0.82,
    )
    selected, quality = choose_startup_text(
        "Tôi không hứa sẽ làm mọi thứ dịu đi—nhưng có thể làm chúng rõ hơn.",
        continuity_frame,
    )
    assert "làm mọi thứ dịu đi" not in selected
    assert quality["accepted"] is True


def test_architecture_c_autonomous_reads_public_safe_cortex_context() -> None:
    from types import SimpleNamespace

    from runtime.ai_orchestrated.self_context import build_autonomous_self_context

    agenda_item = SimpleNamespace(
        id="agenda-1",
        kind="technical_followup",
        summary="Kiểm tra lại thứ tự TTS và bubble",
        evidence_ids=("turn:42",),
        pressure=0.78,
        urgency=0.64,
        confidence=0.88,
        speech_value=0.82,
        score=0.79,
    )
    host = SimpleNamespace(
        rolling_internal_state=SimpleNamespace(
            curiosity=0.72,
            fatigue=0.14,
            silence_pressure=0.31,
            continuity_pressure=0.68,
            attachment_pressure=0.22,
            consolidation_pressure=0.44,
            insight_pressure=0.61,
            agenda_pressure=0.77,
            speech_restraint=0.24,
            resource_caution=0.11,
            self_coherence=0.83,
            internal_drive=0.69,
        ),
        last_cortex_attention=SimpleNamespace(
            level="wake_main_cognition",
            should_speak=True,
            should_wake_main=True,
            reason="main_wake_allowed",
            confidence=0.86,
            internal_drive=0.69,
            speech_value=0.73,
            permission=0.91,
            interrupt_cost=0.09,
        ),
        internal_agenda=SimpleNamespace(top_items=lambda limit=5: [agenda_item]),
        private_mind_daemon_digest={
            "daemon": {
                "surface_queue_preview": [
                    {
                        "id": "mind-1",
                        "kind": "unfinished_thought",
                        "source": "private_mind",
                        "topic_seed": "TTS đang nói sau bubble một nhịp",
                        "confidence": 0.84,
                        "score": 0.76,
                    }
                ]
            }
        },
        private_life_digest={},
        private_mind_surface_queue=[],
        private_life_surface_queue=[],
    )

    context = build_autonomous_self_context(host)
    assert context.has_public_seed is True
    assert context.strongest_seed_score >= 0.48
    assert context.agenda[0]["evidence_refs"] == ["turn:42"]
    assert context.private_mind_surface[0]["topic_seed"] == "TTS đang nói sau bubble một nhịp"
    assert "TTS" in context.prompt_context()

def test_ambient_screen_tracker_surfaces_ocr_delta_without_keyword_scene_inference() -> None:
    from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot
    from runtime.ai_orchestrated.ambient_screen_context import AmbientScreenTracker

    now = time.time()
    tracker = AmbientScreenTracker()
    snapshot = ScreenTextSnapshot(
        window=ActiveWindowInfo(title="Mini World", process_name="miniworld.exe"),
        blocks=(
            ScreenTextBlock(
                block_id="hud-1", text="HP 92 Stamina 44 Inventory", source="ocr",
                confidence=0.87, window_title="Mini World", captured_at=now,
            ),
        ),
        captured_at=now, changed_ratio=0.18, revision=1, backend="rapidocr",
    )

    context = tracker.observe(snapshot, telemetry={"active_app": "Mini World"}, now=now)

    assert context.ocr_active is True
    assert context.scene_kind == "unknown"
    assert context.meaningful_delta is True
    assert "HP 92" in context.visible_text[0]
    assert context.changed_text

def test_ambient_screen_tracker_does_not_repeat_unchanged_ocr_as_delta() -> None:
    from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot
    from runtime.ai_orchestrated.ambient_screen_context import AmbientScreenTracker

    now = time.time()
    tracker = AmbientScreenTracker()
    block = ScreenTextBlock("hud", "Quest: Find the village", "ocr", 0.82, "Game", now)
    first = ScreenTextSnapshot(
        window=ActiveWindowInfo(title="Game", process_name="game.exe"),
        blocks=(block,), captured_at=now, changed_ratio=0.10, revision=1, backend="rapidocr",
    )
    second = ScreenTextSnapshot(
        window=ActiveWindowInfo(title="Game", process_name="game.exe"),
        blocks=(block,), captured_at=now + 1, changed_ratio=0.0, revision=2, backend="rapidocr",
    )

    assert tracker.observe(first, now=now).changed_text
    repeated = tracker.observe(second, now=now + 1)
    assert repeated.changed_text == ()
    assert repeated.content_changed is False


def test_ambient_screen_tracker_blocks_private_communication_text() -> None:
    from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot
    from runtime.ai_orchestrated.ambient_screen_context import AmbientScreenTracker

    now = time.time()
    snapshot = ScreenTextSnapshot(
        window=ActiveWindowInfo(title="Discord"),
        blocks=(ScreenTextBlock("private", "secret message", "uia", 0.9, "Discord", now),),
        captured_at=now,
        revision=1,
        backend="uia_only",
        privacy_blocked=True,
    )
    context = AmbientScreenTracker().observe(snapshot, now=now)
    assert context.privacy_blocked is True
    assert context.visible_text == ()
    assert context.changed_text == ()
    assert "do not mention" in context.prompt_context()


def test_autonomous_deliberation_uses_main_model_not_parser_lane(monkeypatch) -> None:
    from types import SimpleNamespace

    main = object()
    parser = object()
    critic = object()
    router = AIRuntimeModelRouter(
        SimpleNamespace(
            client=main,
            social_parser_client=parser,
            social_critic_client=critic,
        )
    )
    captured = {}

    def fake_call(client, **kwargs):
        captured["client"] = client
        captured.update(kwargs)
        return {
            "plan_id": "self-turn-1",
            "speak": False,
            "motive": "none",
            "strategy": "silence",
            "value_focus": "",
            "topic": "",
            "importance": 0.0,
            "intrusion_risk": 0.0,
            "confidence": 0.8,
            "performance_hint": "neutral",
            "tool_intent": "",
            "tool_input": "",
            "evidence_refs": [],
            "notes": "no grounded reason",
        }

    monkeypatch.setattr(router, "_call", fake_call)
    router.plan_autonomous({"event_kind": "self_initiated_turn"})

    assert captured["client"] is main
    assert captured["inference_tier"] == "main_cognition"
    assert captured["priority"] == 90
    assert captured["name"] == "eris_self_initiated_intention"


def test_hardening_fingerprint_covers_ambient_screen_autonomy() -> None:
    from runtime.hardening_release import DEFAULT_AUTHORITY_FILES

    assert "runtime/ai_orchestrated/ambient_screen_context.py" in DEFAULT_AUTHORITY_FILES
    assert "screen/ocr_worker.py" in DEFAULT_AUTHORITY_FILES


def test_runtime_settings_v2_migrates_only_old_warmth_baseline() -> None:
    from runtime.runtime_settings_store import RuntimeSettingsStore

    migrated, changed = RuntimeSettingsStore._migrate_payload(
        {
            "version": 2,
            "chat": {"language": "vi"},
            "tts": {"language": "vi", "speech_persona_warmth": 0.18},
        }
    )
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["tts"]["language"] == "vi"
    assert migrated["tts"]["speech_persona_warmth"] == 0.36

    custom, changed = RuntimeSettingsStore._migrate_payload(
        {
            "version": 2,
            "tts": {"language": "ja", "speech_persona_warmth": 0.31},
        }
    )
    assert changed is True
    assert custom["tts"]["speech_persona_warmth"] == 0.31


def test_grounding_surface_gate_uses_current_evidence_not_user_wording() -> None:
    from runtime.grounding_surface_gate import evaluate_grounded_surface
    from runtime.source_grounding import SourceGroundingEngine, SourceGroundingReport

    engine = SourceGroundingEngine()
    missing = SourceGroundingReport(
        user_text="", primary_source="runtime_state", confidence_cap=0.5,
        screen_evidence_present=False, screen_facts_are_stale=False,
        missing_evidence=["screen_evidence"],
    )
    grounded = SourceGroundingReport(
        user_text="", primary_source="current_screen", confidence_cap=0.8,
        screen_evidence_present=True, screen_facts_are_stale=False,
        evidence_ids=["screen:window_title"], allowed_sources=["current_screen"],
    )

    assert evaluate_grounded_surface(engine, "Tôi thấy cửa sổ Brave.", report=missing).allowed is False
    assert evaluate_grounded_surface(engine, "Tôi thấy cửa sổ Brave.", report=grounded).allowed is True


def test_force_speak_presence_line_is_allowed() -> None:
    from runtime.utterance_evaluator import UtteranceEvaluator

    evaluation = UtteranceEvaluator().evaluate("Toi day.", channel="force_speak")

    assert evaluation.verdict == "allow"
    assert "generic_presence_filler" not in evaluation.issues
    assert "too_thin_to_speak" not in evaluation.issues


def test_post_observer_skips_llm_for_plain_greeting() -> None:
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    payload = AIRuntimeModelRouter._fast_observer_payload("xin chào", event_kind="chat")
    assert payload is not None
    data, source = payload
    assert data["user_act"] == "greeting"
    assert "deterministic_anchor" in source


def test_post_observer_skips_llm_for_greeting_variant() -> None:
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    payload = AIRuntimeModelRouter._fast_observer_payload("hey eris oi", event_kind="chat")
    assert payload is not None
    data, source = payload
    assert data["user_act"] == "greeting"
    assert "deterministic_anchor" in source


def test_startup_grounding_default_primes_screen_before_greeting(monkeypatch) -> None:
    from runtime.ai_orchestrated.startup_grounding import collect_startup_grounding
    from types import SimpleNamespace

    monkeypatch.setenv("ERIS_STARTUP_SCREEN_PRIME_SEC", "0.01")
    monkeypatch.delenv("ERIS_STARTUP_RUNTIME_SIGNALS_ENABLED", raising=False)
    monkeypatch.setenv("ERIS_STARTUP_VISION_ENABLED", "0")

    class Worker:
        def __init__(self):
            self.started = False
        def start(self):
            self.started = True

    class Store:
        def snapshot(self):
            return SimpleNamespace(revision=0, blocks=(), backend="", privacy_blocked=False, window=None)

    class Telemetry:
        def capture_snapshot(self, **_kwargs):
            return SimpleNamespace(cpu_percent=99.0, ram_percent=99.0, active_window="", active_app="", focus_summary="")

    worker = Worker()
    host = SimpleNamespace(
        screen_text_worker=worker, screen_evidence_store=Store(), telemetry_service=Telemetry(),
        mood_score=0, inferred_pos_x=0.5, chat_mode=False, _chat_processing=False,
    )
    bundle = collect_startup_grounding(host)
    assert worker.started is True
    assert bundle.waited_seconds < 0.25
    assert bundle.runtime_opening_evidence == ()
    assert "screen_prime_timeout" in bundle.notes
    assert "vision_prime_disabled" in bundle.notes


def test_startup_grounding_adds_fresh_grounded_vision_fact(monkeypatch) -> None:
    from runtime.ai_orchestrated.startup_grounding import collect_startup_grounding
    from types import SimpleNamespace
    import time

    monkeypatch.setenv("ERIS_STARTUP_VISION_ENABLED", "1")

    class Store:
        def snapshot(self):
            return SimpleNamespace(revision=0, blocks=(), backend="", privacy_blocked=False, window=None)

    class Telemetry:
        def capture_snapshot(self, **_kwargs):
            return SimpleNamespace(cpu_percent=0.0, ram_percent=0.0, active_window="Editor", active_app="Editor", focus_summary="")

    host = SimpleNamespace(
        screen_text_worker=None,
        screen_evidence_store=Store(),
        telemetry_service=Telemetry(),
        mood_score=0,
        inferred_pos_x=0.5,
        chat_mode=False,
        _chat_processing=False,
        vision_last_capture_at=0.0,
        vision_facts_are_stale=False,
    )

    def refresh(**_kwargs):
        host.vision_last_capture_at = time.time()
        return ("Một trình soạn thảo đang mở với mã nguồn ở giữa màn hình",)

    host._maybe_update_screen_facts = refresh
    bundle = collect_startup_grounding(host, timeout_seconds=0.0)
    assert any(item.get("source") == "vision" for item in bundle.evidence)
    assert any("mã nguồn" in str(item.get("text", "")) for item in bundle.evidence)
    assert "vision_prime_ok:1" in bundle.notes


def test_screen_anaphora_rescue_inherits_media_reference_from_previous_turn() -> None:
    from screen.screen_anaphora_rescue import ScreenAnaphoraRescue

    result = ScreenAnaphoraRescue().resolve(
        "đang nghe mà",
        previous_assistant_text="Bạn đang nghe bài đó à?",
    )

    assert result.references_screen is True
    assert result.inherited is True
    assert "Bạn đang nghe bài đó" in result.query_text


def test_screen_anaphora_rescue_does_not_bind_bare_pronoun_without_screen_anchor() -> None:
    from screen.screen_anaphora_rescue import ScreenAnaphoraRescue

    result = ScreenAnaphoraRescue().resolve(
        "nó đó",
        previous_assistant_text="Tôi vẫn nghĩ cách hiểu ban đầu hợp lý hơn.",
    )

    assert result.references_screen is False
    assert result.inherited is False


def test_screen_router_uses_previous_public_turn_to_retrieve_ocr_media_text() -> None:
    import time

    from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot
    from screen.screen_evidence_store import ScreenEvidenceStore
    from screen.screen_relevance_router import ScreenRelevanceRouter

    now = time.time()
    store = ScreenEvidenceStore()
    store.publish(ScreenTextSnapshot(
        window=ActiveWindowInfo(title="YouTube"),
        blocks=(ScreenTextBlock(
            block_id="yt-title",
            text="From The Start - Laufey (Still In Love Version)",
            source="ocr",
            confidence=0.91,
            window_title="YouTube",
            captured_at=now,
        ),),
        captured_at=now,
        revision=1,
        backend="rapidocr",
    ))

    route = ScreenRelevanceRouter(store).route(
        "đang nghe mà",
        previous_assistant_text="Bạn đang nghe bài đó à?",
    )

    assert route.references_screen is True
    assert route.debug["anaphora_inherited"] is True
    assert route.mode in {"text_evidence", "text_evidence_soft"}
    assert route.evidence[0].block_id == "yt-title"


def test_chat_screen_route_forces_ocr_refresh_before_vision(monkeypatch) -> None:
    import time
    from types import SimpleNamespace

    from runtime.chat_cycle import ChatCycleMixin
    from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot
    from screen.screen_evidence_store import ScreenEvidenceStore
    from screen.screen_relevance_router import ScreenRelevanceRouter

    monkeypatch.setenv("ERIS_CHAT_MAIN_VISION", "0")
    monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", "1")
    store = ScreenEvidenceStore()

    class _Worker:
        def __init__(self) -> None:
            self.started = False
            self.refreshed = False

        def start(self) -> None:
            self.started = True

        def refresh_now(self, *, force_ocr: bool = True):
            self.refreshed = bool(force_ocr)
            now = time.time()
            snapshot = ScreenTextSnapshot(
                window=ActiveWindowInfo(title="YouTube"),
                blocks=(ScreenTextBlock(
                    block_id="video-title",
                    text="4 AI làm chúa và xây nền văn minh trong WorldBox",
                    source="ocr",
                    confidence=0.92,
                    window_title="YouTube",
                    captured_at=now,
                ),),
                captured_at=now,
                revision=2,
                backend="rapidocr",
            )
            store.publish(snapshot)
            return snapshot

    class _Logger:
        def info(self, *_args, **_kwargs) -> None:
            return None
        def warning(self, *_args, **_kwargs) -> None:
            return None
        def debug(self, *_args, **_kwargs) -> None:
            return None

    class _Host(ChatCycleMixin):
        pass

    host = _Host()
    host.screen_text_worker = _Worker()
    host.screen_relevance_router = ScreenRelevanceRouter(store)
    host.screen_evidence_store = store
    host.logger = _Logger()
    host.telemetry_service = SimpleNamespace(
        capture_screen_frame_data_uri=lambda: (_ for _ in ()).throw(
            AssertionError("Không được gọi vision khi OCR cưỡng bức đã đủ evidence")
        )
    )
    host.reply_context = SimpleNamespace(previous_public_turn=None)

    result = host._capture_screen_frame_for_chat(
        "tự mà xem đi",
        previous_public_turn=SimpleNamespace(text="Còn video mới thì sao?"),
    )

    assert result == ""
    assert host.screen_text_worker.started is True
    assert host.screen_text_worker.refreshed is True
    assert host._chat_screen_route_decision.mode in {"text_evidence", "text_evidence_soft"}
    assert host._chat_screen_text_evidence[0].block_id == "video-title"


def test_chat_main_vision_captures_regular_turn(monkeypatch) -> None:
    from types import SimpleNamespace

    from runtime.chat_cycle import ChatCycleMixin

    monkeypatch.setenv("ERIS_CHAT_MAIN_VISION", "1")

    class _Logger:
        def info(self, *_args, **_kwargs) -> None:
            return None
        def warning(self, *_args, **_kwargs) -> None:
            return None
        def debug(self, *_args, **_kwargs) -> None:
            return None

    class _Host(ChatCycleMixin):
        def __init__(self) -> None:
            self.refresh_calls = []

        def _refresh_screen_facts_from_data_uri(self, **kwargs):
            self.refresh_calls.append(kwargs)
            self.last_vision_grounding_report = SimpleNamespace(
                grounded_claims=("visible editor window",),
                recommendation="speak",
                source="groq",
            )
            snapshot = kwargs.get("snapshot")
            if snapshot is not None:
                snapshot.vision_facts = ("visible editor window",)
            return ("visible editor window",)

    host = _Host()
    host.screen_text_worker = None
    host.screen_relevance_router = None
    host.logger = _Logger()
    host.telemetry_service = SimpleNamespace(
        capture_screen_frame_data_uri=lambda: "data:image/png;base64,AAAA"
    )
    host.reply_context = SimpleNamespace(previous_public_turn=None)
    host._chat_snapshot_for_vision = SimpleNamespace(vision_facts=())

    result = host._capture_screen_frame_for_chat("ừ tiếp đi")

    assert result == "data:image/png;base64,AAAA"
    assert host._chat_screen_capture_present is True
    assert host._chat_screen_capture_interpreted is True
    assert host.refresh_calls[0]["channel"] == "chat"
    assert host.refresh_calls[0]["force_refresh"] is True


def test_tts_failure_reason_distinguishes_rate_limit_from_long_text() -> None:
    import runtime.tts_engine as tts_engine

    assert tts_engine._tts_failure_reason(RuntimeError("inference_rejected:provider_hot_optional")) == "provider_hot"
    assert tts_engine._tts_failure_reason(RuntimeError("HTTP 429 RESOURCE_EXHAUSTED")) == "provider_rate_limited"
    assert tts_engine._tts_failure_reason(RuntimeError("TTS transcript quá dài (500>260)")) == "transcript_too_long"


def test_hardening_fingerprint_covers_screen_anaphora_rescue() -> None:
    from runtime.hardening_release import DEFAULT_AUTHORITY_FILES

    assert "screen/screen_anaphora_rescue.py" in DEFAULT_AUTHORITY_FILES


def test_screen_anaphora_uses_wider_public_turn_window_than_general_reply_link(monkeypatch) -> None:
    import time
    from types import SimpleNamespace

    from runtime.chat_cycle import ChatCycleMixin

    now = time.time()

    class _Ledger:
        def latest_visible_before(self, _timestamp):
            return SimpleNamespace(
                text="Còn video mới thì sao?",
                timestamp=now - 45.0,
            )

    class _Host(ChatCycleMixin):
        pass

    host = _Host()
    host.reply_context = SimpleNamespace(previous_public_turn=None)
    host.public_turn_ledger = _Ledger()
    monkeypatch.setenv("ERIS_SCREEN_ANAPHORA_WINDOW_SEC", "90")

    assert host._previous_assistant_text_for_screen_route() == "Còn video mới thì sao?"


def test_observer_prompt_is_callable_from_router_instance_without_binding_self():
    """Regression: post-turn observer từng chết vì _observer_prompt thiếu @staticmethod.

    Hàm prompt chỉ nhận keyword ``deep``. Nếu thiếu decorator, Python sẽ bind ``self``
    thành positional argument và mọi completed turn đều fail trước khi observer gọi LLM.
    """
    from types import SimpleNamespace
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    router = AIRuntimeModelRouter(SimpleNamespace(client=None, fallback_client=None))
    shallow = router._observer_prompt(deep=False)
    deep = router._observer_prompt(deep=True)

    assert "observer hậu kỳ" in shallow
    assert "delta quan hệ" in deep
