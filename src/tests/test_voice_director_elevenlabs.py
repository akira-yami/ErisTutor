from __future__ import annotations

import io
import json
import wave
from types import SimpleNamespace

from runtime.stage_contracts import StageFrame
from runtime.stage_output import StageOutputPipeline
from runtime.voice_director import DeepSeekVoiceDirector, validate_voice_plan
import runtime.tts_engine as tts


class _FakeStyleClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.kwargs = None

    def create_chat_completion(self, **kwargs):
        self.kwargs = kwargs
        return {
            "choices": [
                {"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}
            ]
        }


def test_voice_director_uses_short_timeout_and_returns_single_take(monkeypatch):
    monkeypatch.setenv("ERIS_VOICE_DIRECTOR_ENABLED", "1")
    monkeypatch.setenv("ERIS_VOICE_DIRECTOR_TIMEOUT_S", "30")
    payload = {
        "reason": "quiet reassurance",
        "delivery": {
            "style": "warm_soft",
            "emphasis": ["hiểu rồi"],
            "speed": 0.97,
            "energy": 0.36,
            "warmth": 0.74,
            "pitch": "slightly_low",
            "vocalization": "soft_hum",
            "vocalization_position": "prefix",
        },
    }
    client = _FakeStyleClient(payload)
    service = SimpleNamespace(style_client=client)
    frame = StageFrame(
        tts_text="Tôi hiểu rồi. Cứ làm chậm thôi.",
        prosody_style="warm_soft",
        emotion="guarded_tender",
    )

    plan = DeepSeekVoiceDirector(service).plan(frame, channel="chat", user_text="Tôi hơi mệt")

    assert client.kwargs["timeout"] == 6.0
    assert client.kwargs["max_tokens"] == 360
    assert client.kwargs["response_format"] == {"type": "json_object"}
    assert plan["version"] == 3
    assert "segments" not in plan
    assert plan["delivery"]["style"] == "warm_soft"
    assert plan["delivery"]["warmth"] == 0.74
    assert plan["delivery"]["pause_after_ms"] == 0
    assert plan["delivery"]["vocalization"] == "soft_hum"
    assert plan["delivery"]["vocalization_position"] == "prefix"


def test_voice_plan_rejects_rewritten_transcript():
    plan = validate_voice_plan(
        {
            "delivery": {
                "text": "Tôi đã sửa câu này.",
                "style": "warm_soft",
            }
        },
        original_text="Tôi không cho phép sửa câu.",
        fallback_style="neutral",
    )
    assert plan == {}


def test_voice_plan_rejects_multiple_legacy_segments():
    plan = validate_voice_plan(
        {
            "segments": [
                {"text": "Tôi hiểu rồi.", "style": "warm_soft"},
                {"text": "Cứ làm chậm thôi.", "style": "low_quiet"},
            ]
        },
        original_text="Tôi hiểu rồi. Cứ làm chậm thôi.",
        fallback_style="neutral",
    )
    assert plan == {}


def test_stage_output_keeps_voice_director_as_one_full_frame():
    text = "Tôi hiểu rồi. Cứ làm chậm thôi."
    frame = StageFrame(
        turn_id="turn-1",
        display_text=text,
        tts_text=text,
        memory_text=text,
        prosody_style="warm_soft",
        voice_plan={
            "version": 2,
            "delivery": {
                "style": "warm_soft",
                "emphasis": ["hiểu rồi"],
                "speed": 0.97,
                "energy": 0.36,
                "warmth": 0.74,
                "pitch": "slightly_low",
                "vocalization": "small_laugh",
                "vocalization_position": "suffix",
            },
        },
    )

    parts = StageOutputPipeline().frames_for_tts(frame)

    assert len(parts) == 1
    assert parts[0].tts_text == text
    assert parts[0].display_text == text
    assert parts[0].memory_text == text
    meta = parts[0].delivery["voice_director_segment"]
    assert meta["previous_text"] == ""
    assert meta["next_text"] == ""
    assert meta["pause_after_ms"] == 0
    assert meta["vocalization"] == "small_laugh"
    assert meta["vocalization_position"] == "suffix"
    assert "active_beat" not in parts[0].delivery
    assert "performance_act" not in parts[0].delivery


def test_eleven_renderer_uses_warm_tag_without_touching_public_text():
    plan = SimpleNamespace(style="warm_soft")
    rendered = tts._render_elevenlabs_text(
        "Tôi hiểu rồi.",
        plan,
        language_hint="vi",
        voice_director_segment={
            "style": "warm_soft",
            "emphasis": ["hiểu rồi"],
            "warmth": 0.74,
            "pitch": "slightly_low",
        },
    )
    assert rendered.startswith("[warmly]")
    assert '"hiểu rồi"' in rendered
    assert rendered.endswith('Tôi "hiểu rồi".')


def test_elevenlabs_startup_auth_check_does_not_sync_pool(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ERIS_ELEVENLABS_API_KEY", raising=False)

    class _Pool:
        def has_accounts(self):
            return True

        def sync_subscription(self, _account_id):
            raise AssertionError("startup auth check must not sync ElevenLabs")

    monkeypatch.setattr(tts, "_direct_elevenlabs_api_key", lambda: "")

    import eris_secrets
    import runtime.elevenlabs_pool_db as pool_db

    monkeypatch.setattr(eris_secrets, "get_secret", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(pool_db, "GLOBAL_ELEVEN_POOL", _Pool())

    assert tts._elevenlabs_auth_configured_fast() is True


def test_eleven_stream_uses_single_clip_metadata(monkeypatch):
    monkeypatch.setattr(tts, "_get_elevenlabs_token", lambda **_kwargs: ("api_key", "test-key", 0))
    monkeypatch.setattr(tts, "_elevenlabs_base_url", lambda: "https://api.elevenlabs.io")
    monkeypatch.setattr(tts, "_tts_breaker_enabled", lambda: False)
    monkeypatch.setattr(tts, "audio_polish_enabled", lambda: False)
    captured = {}

    class _Response:
        status_code = 200
        text = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_content(self, chunk_size):
            assert chunk_size == 16 * 1024
            yield b"\x00\x00" * 240

    def _post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    import requests

    monkeypatch.setattr(requests, "post", _post)
    wav_bytes = tts._synthesize_elevenlabs_wav(
        "大丈夫だよ。ゆっくりでいい。",
        "voice-123",
        "eleven_v3",
        prosody_plan=SimpleNamespace(style="warm_soft"),
        language_hint="ja",
        voice_director_segment={
            "style": "warm_soft",
            "energy": 0.35,
            "warmth": 0.72,
            "speed": 0.97,
            "previous_text": "",
            "next_text": "",
        },
    )

    assert captured["headers"]["xi-api-key"] == "test-key"
    assert captured["params"] == {"output_format": "pcm_24000"}
    assert captured["json"]["language_code"] == "ja"
    # Transcript Nhật đã được SpeechLocalizer chuẩn hoá. Bật normalization lần
    # hai ở ElevenLabs làm tăng latency đáng kể nên mặc định phải tắt.
    assert captured["json"]["apply_language_text_normalization"] is False
    assert "previous_text" not in captured["json"]
    assert "next_text" not in captured["json"]
    assert captured["json"]["text"].startswith("[warmly]")
    with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
        assert handle.getframerate() == 24000
        assert handle.getsampwidth() == 2
        assert handle.getnchannels() == 1



def test_voice_plan_rejects_unknown_vocalization_without_rejecting_line():
    plan = validate_voice_plan(
        {
            "delivery": {
                "style": "warm_soft",
                "vocalization": "airhorn",
                "vocalization_position": "middle",
            }
        },
        original_text="Tôi hiểu rồi.",
        fallback_style="warm_soft",
    )
    assert plan["delivery"]["vocalization"] == "none"
    assert plan["delivery"]["vocalization_position"] == "prefix"


def test_eleven_renderer_adds_vietnamese_filler_in_same_text():
    rendered = tts._render_elevenlabs_text(
        "Tôi nghĩ cách này ổn hơn.",
        SimpleNamespace(style="warm_soft"),
        language_hint="vi",
        voice_director_segment={
            "style": "warm_soft",
            "warmth": 0.7,
            "vocalization": "soft_hum",
            "vocalization_position": "prefix",
        },
    )
    assert rendered.startswith("[warmly] Ừm…")
    assert rendered.endswith("Tôi nghĩ cách này ổn hơn.")


def test_eleven_renderer_adds_laugh_as_suffix_in_same_text():
    rendered = tts._render_elevenlabs_text(
        "Bạn cũng biết cách làm tôi bận đấy.",
        SimpleNamespace(style="dry_tease"),
        language_hint="vi",
        voice_director_segment={
            "style": "dry_tease",
            "warmth": 0.68,
            "vocalization": "small_laugh",
            "vocalization_position": "suffix",
        },
    )
    assert rendered.startswith("[dryly]")
    assert rendered.endswith("[laughs]")
    assert rendered.count("[laughs]") == 1


def test_eleven_renderer_adds_sigh_as_prefix_in_same_text():
    rendered = tts._render_elevenlabs_text(
        "Được rồi, sửa tiếp thôi.",
        SimpleNamespace(style="annoyed_soft"),
        language_hint="vi",
        voice_director_segment={
            "style": "annoyed_soft",
            "warmth": 0.58,
            "vocalization": "soft_sigh",
            "vocalization_position": "prefix",
        },
    )
    assert rendered.startswith("[slightly annoyed] [sighs]")
    assert rendered.count("[sighs]") == 1


def test_eleven_stream_with_vocalization_still_posts_once(monkeypatch):
    monkeypatch.setattr(tts, "_get_elevenlabs_token", lambda **_kwargs: ("api_key", "test-key", 0))
    monkeypatch.setattr(tts, "_elevenlabs_base_url", lambda: "https://api.elevenlabs.io")
    monkeypatch.setattr(tts, "_tts_breaker_enabled", lambda: False)
    monkeypatch.setattr(tts, "audio_polish_enabled", lambda: False)
    calls = []

    class _Response:
        status_code = 200
        text = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_content(self, chunk_size):
            yield b"\x00\x00" * 240

    def _post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    import requests

    monkeypatch.setattr(requests, "post", _post)
    tts._synthesize_elevenlabs_wav(
        "Bạn cũng biết cách làm tôi bận đấy.",
        "voice-123",
        "eleven_v3",
        prosody_plan=SimpleNamespace(style="dry_tease"),
        language_hint="vi",
        voice_director_segment={
            "style": "dry_tease",
            "warmth": 0.68,
            "vocalization": "small_laugh",
            "vocalization_position": "suffix",
        },
    )

    assert len(calls) == 1
    assert calls[0][1]["json"]["text"].endswith("[laughs]")
