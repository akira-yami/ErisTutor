from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

import pytest

from domain import Decision
from runtime import runtime_settings_store as settings
from runtime import tts_engine as tts
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import KernelTurn
from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter
from runtime.conversation.runtime_writer import RuntimeConversationWriter, _prelocalize_v03_speech


VI_VOICE = "aHC3XZOMg3HZuXVYK6hy"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("ERIS_TTS_LANGUAGE", raising=False)
    monkeypatch.delenv("ERIS_ELEVENLABS_VOICE_ID", raising=False)
    path = tmp_path / "runtime_settings.json"
    path.write_text(json.dumps({"version": 18, "tts": {
        "provider": "elevenlabs", "language": "ja",
        "elevenlabs_voice_id": "original-ja-voice", "elevenlabs_model": "eleven_v3",
    }}), encoding="utf-8")
    current = settings.RuntimeSettingsStore(path)
    monkeypatch.setattr(settings, "_DEFAULT_STORE", current)
    current.load()
    return current


def test_switch_chooses_vietnamese_voice_despite_legacy_japanese_env(store, monkeypatch):
    # Khi JA không có voice riêng (để rỗng), JA mới rơi về env legacy.
    store.apply_patch({"tts": {"elevenlabs_voice_id_ja": ""}})
    monkeypatch.setenv("ERIS_ELEVENLABS_VOICE_ID", "legacy-ja-voice")
    assert tts._voice() == "legacy-ja-voice"
    store.apply_patch({"tts": {"language": "vi"}})
    assert tts._voice() == VI_VOICE
    store.apply_patch({"tts": {"language": "ja"}})
    assert tts._voice() == "legacy-ja-voice"


def test_ja_uses_configured_japanese_voice_over_legacy_env(store, monkeypatch):
    # JA có voice riêng thì thắng env legacy ERIS_ELEVENLABS_VOICE_ID.
    monkeypatch.setenv("ERIS_ELEVENLABS_VOICE_ID", "legacy-ja-voice")
    store.apply_patch({"tts": {"language": "ja", "elevenlabs_voice_id_ja": "configured-ja-voice"}})
    assert tts._voice() == "configured-ja-voice"


def test_language_specific_voice_can_be_changed_live(store):
    result = store.apply_patch({"tts": {
        "language": "vi", "elevenlabs_voice_id_vi": "another-vi-voice",
    }})
    assert not result.rejected
    assert tts._voice() == "another-vi-voice"


def test_saved_language_survives_restart_with_legacy_japanese_env(store, monkeypatch):
    store.apply_patch({"tts": {"language": "vi"}})
    monkeypatch.setenv("ERIS_TTS_LANGUAGE", "ja")
    reopened = settings.RuntimeSettingsStore(store.path)
    assert reopened.get("tts", "language") == "vi"


def test_compat_envelope_labels_bare_vietnamese_speech_with_selected_language(store):
    store.apply_patch({"tts": {"language": "vi"}})
    router = AIRuntimeModelRouter.__new__(AIRuntimeModelRouter)
    router.logger = None
    result = router._compat_public_envelope({"text": "Tôi hiểu rồi.", "speech_text": "Tôi hiểu rồi."})
    assert result["speech_language"] == "vi"


def test_vietnamese_surface_discards_japanese_speech_and_ending_without_translating(store):
    payload = {
        "text": "Tôi hiểu rồi.", "speech_text": "ふふ。分かりましたね。",
        "speech_language": "ja", "tts_language": "ja",
        "speech": {"text": "ふふ。分かりましたね。", "language": "ja"},
        "utterance": {"speech": {"text": "ふふ。分かりましたね。", "language": "ja"}},
        "speech_contract": {"language": "ja", "locale": "ja-JP"},
    }

    class NoJapaneseLocalizer:
        def localize(self, *args, **kwargs):
            pytest.fail("Vietnamese mode must not call the Japanese localizer")

    result = _prelocalize_v03_speech(
        payload, host=SimpleNamespace(character_speech_localizer=NoJapaneseLocalizer()),
        language_hint="vi",
    )
    assert result["speech_text"] == "Tôi hiểu rồi."
    assert result["speech_language"] == "vi"
    assert result["speech"]["text"] == "Tôi hiểu rồi."
    assert result["utterance"]["speech"]["text"] == "Tôi hiểu rồi."
    assert result["speech_contract"]["locale"] == "vi-VN"
    assert payload["speech_text"] == "ふふ。分かりましたね。"


@pytest.mark.parametrize("prefix", ["ふふ。", "ん…", "うん。", "えっと…"])
@pytest.mark.parametrize("language, expected", [("vi", b""), ("ja", b"cached-japanese-audio")])
def test_cached_japanese_prefix_is_only_available_in_japanese_mode(store, monkeypatch, prefix, language, expected):
    monkeypatch.setattr(tts, "vocal_cache_enabled", lambda: True)
    worker = tts._TtsWorker.__new__(tts._TtsWorker)
    worker._language_hint = language
    worker._model = "eleven_v3"
    worker._epoch_validator = None
    worker._stop_event = threading.Event()
    worker._vocal_cache = SimpleNamespace(get=lambda key: b"cached-japanese-audio")
    request = tts._SpeakRequest("Tôi hiểu rồi.", language_hint=language, vocal_prefix=prefix)
    assert worker._vocal_prefix_wav(request, prefix, SimpleNamespace(style="neutral")) == expected


@pytest.mark.parametrize("cue", ["soft_hum", "gentle_ack", "small_laugh", "soft_sigh", "curious_hm", "soft_surprise"])
def test_vietnamese_director_cues_do_not_insert_japanese(cue):
    rendered = tts._render_elevenlabs_text(
        "Tôi hiểu rồi.", SimpleNamespace(style="neutral"), language_hint="vi",
        voice_director_segment={"vocalization": cue},
    )
    assert "Tôi hiểu rồi." in rendered
    assert not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", rendered)


def test_language_refresh_revokes_old_speech_epoch(store, monkeypatch):
    backend = SimpleNamespace(shutdown=lambda: None)
    monkeypatch.setattr(tts, "_build_worker", lambda *args, **kwargs: backend)
    engine = tts.TtsEngine.__new__(tts.TtsEngine)
    engine._closed = False
    engine._ok = True
    engine._worker = backend
    engine._config_lock = threading.RLock()
    engine._speech_lock = threading.RLock()
    engine._speech_epoch = 1
    engine._speech_turn_id = "old-turn"
    engine._config_signature = "elevenlabs|original-ja-voice|eleven_v3|ja"
    store.apply_patch({"tts": {"language": "vi"}})
    assert engine.refresh_configuration()
    assert not engine.is_speech_epoch_current(1, "old-turn")


def test_stopped_worker_cannot_accept_late_synthesis(store):
    worker = tts._TtsWorker.__new__(tts._TtsWorker)
    worker._epoch_validator = lambda epoch, turn_id: True
    worker._stop_event = threading.Event()
    worker._stop_event.set()
    assert not worker._request_is_current(tts._SpeakRequest("古い音声", speech_epoch=1))


def test_new_vietnamese_worker_rejects_sealed_japanese_reply(store):
    worker = tts._TtsWorker.__new__(tts._TtsWorker)
    worker._epoch_validator = lambda epoch, turn_id: True
    worker._stop_event = threading.Event()
    worker._language_hint = "vi"
    request = tts._SpeakRequest("古い音声", language_hint="ja", verified_transcript_hash="sealed")
    assert not worker._request_is_current(request)


def test_writer_rejects_reply_started_before_language_switch(store):
    class Router:
        def generate_public_reply(self, **kwargs):
            store.apply_patch({"tts": {"language": "vi"}})
            return {"text": "Tôi hiểu rồi.", "speech_text": "Tôi hiểu rồi.", "speech_language": "vi"}

    host = SimpleNamespace(
        ai_model_router=Router(),
        parser=SimpleNamespace(parse=lambda raw, **kwargs: Decision(
            text="Tôi hiểu rồi.", animation="stand", should_speak=True)),
        logger=None,
    )
    turn = KernelTurn(session_id="s", generation=1, run_id="run:1",
                      move=NextConversationMove(intent="answer", say="answer", move_id="move:1"),
                      note="", delivery_id="delivery:1")
    event = ConversationEvent(event_id="e", session_id="s", generation=1, kind="user_turn", text="chào")
    with pytest.raises(RuntimeError, match="speech_configuration_changed"):
        RuntimeConversationWriter(host).realize(
            turn=turn, event=event, recent_history=(), runtime_context={},
        )


def test_prepared_old_language_is_rejected_before_body_dispatch(store):
    store.apply_patch({"tts": {"language": "vi"}})
    sent = []
    host = SimpleNamespace(
        ai_model_router=SimpleNamespace(generate_public_reply=lambda **kwargs: {
            "text": "Tôi hiểu rồi.", "speech_text": "Tôi hiểu rồi.", "speech_language": "vi",
        }),
        parser=SimpleNamespace(parse=lambda raw, **kwargs: Decision(
            text="Tôi hiểu rồi.", animation="stand", should_speak=True)),
        _send_decision_to_body=lambda *args, **kwargs: sent.append(args),
        logger=None,
    )
    turn = KernelTurn(session_id="s", generation=1, run_id="run:1",
                      move=NextConversationMove(intent="answer", say="answer", move_id="move:1"),
                      note="", delivery_id="delivery:1")
    event = ConversationEvent(event_id="e", session_id="s", generation=1, kind="user_turn", text="chào")
    rendered = RuntimeConversationWriter(host).realize(
        turn=turn, event=event, recent_history=(), runtime_context={},
    )
    store.apply_patch({"tts": {"language": "ja"}})
    with pytest.raises(RuntimeError, match="speech_configuration_changed"):
        RuntimeConversationDeliveryAdapter(host).deliver(
            turn=turn, rendered=rendered, delivery_id=turn.delivery_id,
        )
    assert not sent
