from __future__ import annotations

from types import SimpleNamespace

from runtime.ai_orchestrated.startup_grounding import (
    ensure_startup_greeting_payload,
    ensure_startup_greeting_text,
    startup_text_has_greeting,
)


def test_contextual_startup_gets_greeting_without_losing_question() -> None:
    text, inserted = ensure_startup_greeting_text(
        "Hôm nay anh xem Facebook khá tập trung nhỉ? Có gì đáng chú ý không?",
        language="vi",
        user_vocative="anh",
        frame=SimpleNamespace(situation="plain_return"),
    )

    assert inserted is True
    assert text == "Chào anh. Hôm nay anh xem Facebook khá tập trung nhỉ? Có gì đáng chú ý không?"


def test_natural_arrival_acknowledgement_is_not_duplicated() -> None:
    text, inserted = ensure_startup_greeting_text(
        "Anh vào rồi à. Hôm nay xem gì thế?",
        language="vi",
        user_vocative="anh",
        frame=SimpleNamespace(situation="plain_return"),
    )

    assert inserted is False
    assert text == "Anh vào rồi à. Hôm nay xem gì thế?"
    assert startup_text_has_greeting(text, language="vi") is True


def test_startup_greeting_helper_is_not_a_production_speech_path() -> None:
    from pathlib import Path

    orchestrator = (
        Path(__file__).resolve().parents[1]
        / "runtime"
        / "ai_orchestrated"
        / "orchestrator.py"
    ).read_text(encoding="utf-8")

    assert "ensure_startup_greeting_payload" not in orchestrator
    assert "collect_startup_grounding" not in orchestrator


def test_existing_bilingual_greetings_are_not_duplicated() -> None:
    payload = {
        "text": "Chào anh. Hôm nay xem gì thế?",
        "display_language": "vi",
        "speech_text": "こんにちは。今日は何を見てるの？",
        "speech_language": "ja",
    }

    result = ensure_startup_greeting_payload(
        payload,
        frame=SimpleNamespace(situation="plain_return"),
        display_language="vi",
        speech_language="ja",
        user_vocative="anh",
    )

    assert result["text"] == payload["text"]
    assert result["speech_text"] == payload["speech_text"]
    assert result["startup_greeting_inserted"] is False
