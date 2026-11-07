# -*- coding: utf-8 -*-
"""Các regression test nhỏ cho bubble đọc chậm và localizer Việt → Nhật."""

from pathlib import Path

import pytest


def test_localizer_does_not_replace_dialogue_with_filler_due_to_prompt_budget(monkeypatch):
    import contextlib
    from types import SimpleNamespace
    from unittest.mock import Mock

    import llm.token_pressure as pressure
    import runtime.inference_broker as broker
    import runtime.speech_localizer as localizer
    from runtime.conversation.runtime_writer import _prelocalize_v03_speech

    # Exact Surface response from the 2026-10-28 22:00 session: the old guard
    # rejected 82 transcript tokens against an effective limit of 80.
    text = (
        "22:01 rồi, và bạn đang mở cái trang tên 'Đánh giá Cognitive Tick' trên Brave. "
        "Nghe như bạn đang tự chấm nhịp nhận thức của chính mình vậy — hay chỉ đọc lý thuyết cho vui? "
        "Tôi tò mò thật đấy, vì cái tên đó vừa kỹ thuật vừa hơi triết học."
    )
    japanese = "認知のリズムを自分で評価しているの？ それとも理論を読んでいるだけ？"
    assert len(text) == 236
    monkeypatch.setenv("ERIS_TOKEN_BUDGET_ALL_LANES", "1")
    monkeypatch.setattr(localizer, "_enabled", lambda: True)
    monkeypatch.setattr(localizer, "_get_api_key_lease", lambda **kwargs: ("test-key", None, None))
    monkeypatch.setattr(localizer, "_style_rescue_api_key", lambda: "")
    monkeypatch.setattr(localizer, "_apply_signature_surface", lambda text, policy: text)
    plan_request = Mock(return_value=SimpleNamespace(allowed=True, prompt_limit=258, output_tokens=384))
    monkeypatch.setattr(pressure.GLOBAL_TOKEN_PRESSURE_GOVERNOR, "plan_request", plan_request)
    monkeypatch.setattr(pressure.GLOBAL_TOKEN_PRESSURE_GOVERNOR, "record_request", Mock())
    monkeypatch.setattr(pressure.GLOBAL_TOKEN_PRESSURE_GOVERNOR, "record_success", Mock())
    monkeypatch.setattr(broker, "inference_context", lambda *args, **kwargs: contextlib.nullcontext())
    monkeypatch.setattr(broker, "GLOBAL_INFERENCE_BROKER", SimpleNamespace(
        request_from_context=lambda **kwargs: object(),
        run=lambda request, func: func(),
    ))
    translate = Mock(return_value={"choices": [{"message": {"content": japanese}}]})
    monkeypatch.setattr(localizer, "_translate_with_deepseek_api", translate)

    instance = localizer.SpeechLocalizer()
    result = _prelocalize_v03_speech(
        {"text": text, "speech_text": text},
        host=SimpleNamespace(character_speech_localizer=instance),
        language_hint="ja",
    )

    assert result["speech_text"] == japanese
    assert result["text"] == text
    translate.assert_called_once()
    assert translate.call_args.kwargs["prompt"].endswith(text)
    assert not instance._failure_until


def test_long_localizer_never_silently_skips_a_piece(monkeypatch):
    import runtime.speech_localizer as m

    text = " ".join(f"Đây là câu số {i} rất dài và có nội dung riêng." for i in range(110))
    chunks = m._split_localizer_chunks(text)
    assert len(chunks) > 1
    assert " ".join(chunks) == m._normalize_transcript(text)
    assert all(len(chunk) <= 540 for chunk in chunks)

    calls = []

    def fake_translate(self, chunk, **_kwargs):
        calls.append(chunk)
        return "これは日本語です。"

    monkeypatch.setattr(m.SpeechLocalizer, "_translate_to_japanese", fake_translate)
    monkeypatch.setattr(m, "_valid_japanese_translation", lambda *_args, **_kwargs: True)
    result = m.SpeechLocalizer().localize(text, language_hint="ja", strict=True)
    assert len(calls) == len(chunks)
    assert " ".join(calls) == m._normalize_transcript(text)
    assert result.count("これは日本語です。") == len(chunks)


def test_long_localizer_fails_whole_turn_if_a_chunk_is_missing(monkeypatch):
    import runtime.speech_localizer as m

    text = " ".join("Đoạn cần dịch và không được bỏ sót." for _ in range(120))
    translated_count = 0

    def missing_second(self, chunk, **_kwargs):
        nonlocal translated_count
        translated_count += 1
        return "" if translated_count == 2 else "日本語です。"

    monkeypatch.setattr(m.SpeechLocalizer, "_translate_to_japanese", missing_second)
    monkeypatch.setattr(m, "_valid_japanese_translation", lambda source, translated, **_kwargs: bool(translated))
    with pytest.raises(m.SpeechLocalizationError, match="chunk_invalid"):
        m.SpeechLocalizer().localize(text, language_hint="ja", strict=True)


def test_localizer_chunks_a_long_word_without_losing_letters():
    import runtime.speech_localizer as m

    text = "a" * 1440
    chunks = m._split_localizer_chunks(text)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 540 for chunk in chunks)


def test_bubble_does_not_force_scroll_to_bottom():
    # Đây chỉ là static contract; chạy C# Godot thực tế vẫn cần kiểm thử bằng chuột.
    source = Path(__file__).resolve().parents[1] / "body" / "UdpListener.cs"
    text = source.read_text(encoding="utf-8")
    assert "LongSpeechImmediateChars" in text
    assert "ResetSpeechScrollToTop(_label)" in text
    assert "ScrollFollowing = false" in text
    assert "scrollBar.Value = scrollBar.MaxValue" not in text
    assert "_label.MouseFilter = Control.MouseFilterEnum.Stop" in text
    assert "TryGetVisibleControlMouseRect(ActiveSpeechBubble(), mouse, out rect)" in text


def test_bilingual_segment_count_mismatch_does_not_drop_vietnamese(monkeypatch):
    from types import SimpleNamespace

    from runtime.stage_contracts import StageFrame
    from runtime.stage_output import StageOutputPipeline
    import runtime.tts_engine as engine

    monkeypatch.setattr(engine, "_tts_provider", lambda: "edge")
    vi = "Đây là ý thứ nhất khá dài. Đây là ý thứ hai cũng khá dài."
    jp = "これは最初の説明で大切な話題を扱っています。 次の文章には別の議論と詳しい理由が書かれています。 最後の文章でも大切な情報を続けて説明しています。"
    frame = StageFrame(turn_id="bilingual", display_text=vi, memory_text=vi, tts_text=jp)
    profile = SimpleNamespace(logical_segment_tts=True, min_split_chars=24, max_tts_segments=8)
    parts = StageOutputPipeline(profile=profile).frames_for_tts(frame)
    assert len(parts) == 3
    assert [p.display_text for p in parts] == [vi, "", ""]
    assert [p.memory_text for p in parts] == [vi, "", ""]
    assert " ".join(p.tts_text for p in parts) == jp
