# -*- coding: utf-8 -*-
import io
import re
import threading
from types import SimpleNamespace
import wave
import pytest

from runtime.stage_output import SentenceSegmenter
import runtime.tts_engine as tts


def test_japanese_sentence_segmentation_without_spaces():
    text = (
        "AIが得意なことを学ぶのではなく、AIが苦手なことを学ぶべきです。"
        "つまり、システム全体を俯瞰して読む力、デバッグする力です。"
        "今開いているSpring Bootの環境に目を向けてみてください。"
    )
    splitter = SentenceSegmenter()
    parts = splitter.split(text)
    assert len(parts) == 3
    assert parts[0].endswith("べきです。")
    assert parts[1].endswith("力です。")
    assert parts[2].endswith("みてください。")


def test_bearer_auth_uses_standard_text_to_speech_stream(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200
        headers = {"Content-Type": "audio/pcm"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def iter_content(self, chunk_size=16384):
            # 24000Hz 16-bit mono PCM silence
            return [b"\x00\x00" * 2400]

    def _post(url, *args, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    import requests
    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(tts, "_tts_breaker_enabled", lambda: False)

    # Mock bearer token pool account
    dummy_acc = SimpleNamespace(
        id=8,
        token="test-bearer-token",
        auth_type="bearer",
        label="acc8",
        status="active",
        character_count=1000,
        character_limit=10000,
    )
    monkeypatch.setattr(tts, "_get_elevenlabs_token", lambda *a, **k: (dummy_acc.auth_type, dummy_acc.token, dummy_acc.id))

    wav_bytes = tts._synthesize_elevenlabs_wav(
        "こんにちは、エリスです。",
        voice="fUjY9K2nAIwlALOwSiwc",
        model="",  # Should default to eleven_v3
        prosody_plan=None,
        language_hint="ja",
    )

    assert "/v1/text-to-speech/fUjY9K2nAIwlALOwSiwc/stream" in captured["url"]
    assert captured["headers"]["authorization"] == "Bearer test-bearer-token"
    assert captured["json"]["model_id"] == "eleven_v3"
    assert captured["params"] == {"output_format": "pcm_24000"}
    assert captured["json"]["language_code"] == "ja"

    with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
        assert handle.getframerate() == 24000
        assert handle.getsampwidth() == 2
        assert handle.getnchannels() == 1
