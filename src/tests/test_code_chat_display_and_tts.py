# -*- coding: utf-8 -*-
"""Tests verifying code display in chat and silent exclusion from TTS."""
from __future__ import annotations

import pytest

from runtime.stage_transformers import DisplayFilter, TTSFilter
from runtime.final_surface_sanitizer import FinalSurfaceSanitizer
from runtime.addressing_policy import clean_public_addressing, sanitize_legacy_addressing
from runtime.conversation.speech_math_strip import strip_math_for_speech
from runtime.speech_localizer import SpeechLocalizer, _normalize_transcript
from runtime.tts_markup import render_tts_markup, _normalize_lines
from runtime.tts_engine import _clean_text, TtsEngine, TtsPlaybackEvent
from runtime.conversation.runtime_writer import _prelocalize_v03_speech
from runtime.conversation.root_prompt_contract import ROOT_SYSTEM_CORE
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter


def test_root_prompt_and_model_router_contracts_allow_code():
    assert "code" in ROOT_SYSTEM_CORE.lower()
    prompt = AIRuntimeModelRouter._public_reply_prompt("vi", "vi")
    assert "code" in prompt.lower()
    assert "markdown" in prompt.lower()


def test_display_filter_preserves_code_blocks_and_indentation():
    sample = (
        "Dưới đây là hàm tính tổng:\n"
        "```python\n"
        "def sum_two_numbers(a, b):\n"
        "    result = a + b\n"
        "    return result\n"
        "```\n"
        "Bạn chạy thử xem sao nhé."
    )
    cleaned = DisplayFilter().clean(sample)
    # Must preserve code block verbatim
    assert "```python" in cleaned
    assert "def sum_two_numbers(a, b):" in cleaned
    assert "    result = a + b" in cleaned
    assert "    return result" in cleaned
    assert "```" in cleaned
    # Must NOT replace with [code]
    assert "[code]" not in cleaned
    # Must preserve surrounding conversational text
    assert "Dưới đây là hàm tính tổng:" in cleaned
    assert "Bạn chạy thử xem sao nhé." in cleaned


def test_display_filter_preserves_tilde_fences():
    sample = (
        "Script JavaScript nè:\n"
        "~~~javascript\n"
        "const greet = (name) => {\n"
        "  console.log(`Hello ${name}`);\n"
        "};\n"
        "~~~\n"
        "Xong rồi nha."
    )
    cleaned = DisplayFilter().clean(sample)
    assert "~~~javascript" in cleaned
    assert "const greet = (name) => {" in cleaned
    assert "  console.log(`Hello ${name}`);" in cleaned
    assert "~~~" in cleaned
    assert "[code]" not in cleaned


def test_code_blocks_protected_from_honorific_sanitization():
    # 'Master' and 'em' in code should NOT be transformed into 'bạn' or 'tôi'
    sample = (
        "Đây là class điều khiển:\n"
        "```python\n"
        "class MasterController:\n"
        "    def __init__(self):\n"
        "        self.margin = '1.5em'\n"
        "        self.master_mode = True\n"
        "```\n"
        "Thưa bạn, code đã sẵn sàng."
    )
    cleaned_addressing = clean_public_addressing(sample, surface="display")
    assert "class MasterController:" in cleaned_addressing
    assert "self.margin = '1.5em'" in cleaned_addressing
    assert "self.master_mode = True" in cleaned_addressing

    cleaned_display = DisplayFilter().clean(sample)
    assert "class MasterController:" in cleaned_display
    assert "self.margin = '1.5em'" in cleaned_display


def test_final_surface_sanitizer_preserves_code_fences():
    sample = (
        "Code mẫu:\n"
        "```bash\n"
        "git checkout master\n"
        "npm install\n"
        "```\n"
        "Chạy lệnh trên nhé."
    )
    display_out = FinalSurfaceSanitizer().sanitize(sample, surface="display").text
    assert "```bash" in display_out
    assert "git checkout master" in display_out
    assert "npm install" in display_out
    assert "```" in display_out


def test_tts_filter_strips_code_blocks():
    sample = (
        "Mình viết code này cho bạn:\n"
        "```python\n"
        "for i in range(10):\n"
        "    print(i)\n"
        "```\n"
        "Bạn kiểm tra nhé."
    )
    tts_out = TTSFilter().clean(sample)
    assert "for i in range" not in tts_out
    assert "print" not in tts_out
    assert "```" not in tts_out
    assert "Mình viết code này cho bạn" in tts_out
    assert "Bạn kiểm tra nhé" in tts_out


def test_tts_filter_pure_code_fallback():
    pure_code = (
        "```python\n"
        "def hello():\n"
        "    pass\n"
        "```"
    )
    tts_out = TTSFilter().clean(pure_code)
    # Should not be empty, must give friendly spoken message
    assert tts_out == "Mình gửi code ở đây nhé."


def test_speech_math_strip_removes_code_blocks():
    sample = (
        "Đây là thuật toán:\n"
        "```python\n"
        "def solve(n):\n"
        "    return n * 2\n"
        "```\n"
        "Đơn giản vậy thôi."
    )
    speech = strip_math_for_speech(sample)
    assert "def solve" not in speech
    assert "return" not in speech
    assert "```" not in speech
    assert "Đây là thuật toán" in speech
    assert "Đơn giản vậy thôi" in speech


def test_speech_localizer_strips_code_blocks():
    sample = (
        "Xem đoạn code sau:\n"
        "```python\n"
        "import os\n"
        "print(os.getcwd())\n"
        "```\n"
        "Đường dẫn hiện tại đó."
    )
    norm = _normalize_transcript(sample)
    assert "import os" not in norm
    assert "print" not in norm
    assert "```" not in norm
    assert "Xem đoạn code sau" in norm


def test_speech_localizer_pure_code_fallback():
    pure_code = "```python\nprint('hello')\n```"
    localizer = SpeechLocalizer()
    out = localizer.localize(pure_code, language_hint="ja")
    assert out == "コードを書いておいたよ。"


def test_tts_markup_strips_code_blocks():
    sample = (
        "Dòng đầu nè\n"
        "```python\n"
        "a = 1 + 2\n"
        "```\n"
        "Dòng cuối nè"
    )
    lines = _normalize_lines(sample)
    assert "Dòng đầu nè" in lines
    assert "Dòng cuối nè" in lines
    assert not any("a = 1 + 2" in line for line in lines)
    assert not any("```" in line for line in lines)


def test_tts_engine_clean_text_strips_fences():
    sample = (
        "Xin chào.\n"
        "```python\n"
        "while True:\n"
        "    break\n"
        "```\n"
        "~~~js\n"
        "let x = 1;\n"
        "~~~\n"
        "Hẹn gặp lại."
    )
    clean = _clean_text(sample)
    assert "while True" not in clean
    assert "let x = 1" not in clean
    assert "```" not in clean
    assert "~~~" not in clean
    assert "Xin chào." in clean
    assert "Hẹn gặp lại." in clean


def test_runtime_writer_pure_code_vietnamese_fallback():
    payload = {
        "text": "```python\nprint('Eris')\n```",
        "speech_text": "",
    }
    class DummyHost:
        pass

    result = _prelocalize_v03_speech(payload, host=DummyHost(), language_hint="vi")
    assert result["speech_text"] == "Mình gửi code ở đây nhé."
    assert result["speech_language"] == "vi"


def test_runtime_writer_pure_code_japanese_fallback():
    payload = {
        "text": "```python\nprint('Eris')\n```",
        "speech_text": "",
    }
    class DummyHost:
        pass

    result = _prelocalize_v03_speech(payload, host=DummyHost(), language_hint="ja")
    assert result["speech_text"] == "コードを書いておいたよ。"
    assert result["speech_language"] == "ja"


def test_tts_engine_pure_code_does_not_cancel_audio(monkeypatch):
    engine = TtsEngine()
    monkeypatch.setattr(engine, "_ok", True)
    requests = []
    class DummyWorker:
        def speak(self, req, *, replace=True):
            requests.append(req)
    monkeypatch.setattr(engine, "_worker", DummyWorker())
    events: list[TtsPlaybackEvent] = []
    engine.speak(
        "```python\ndef run():\n    pass\n```",
        language_hint="vi",
        on_event=events.append,
    )
    # Should not be cancelled with 'empty_text'
    cancelled_empty = [e for e in events if e.type == "audio_cancelled" and e.reason == "empty_text"]
    assert not cancelled_empty, f"Unexpected audio_cancelled: {events}"
    assert len(requests) == 1
    assert requests[0].text == "Mình gửi code ở đây nhé."


def test_action_tag_extractor_preserves_code_fences_and_inline_code():
    from runtime.stage_transformers import ActionTagExtractor

    sample = (
        "(cười khẽ) Đây là hàm xử lý:\n"
        "```python\n"
        "def get_item(arr):\n"
        "    # style: modern\n"
        "    return arr[index]\n"
        "```\n"
        "Bạn dùng kiểu `List<String>` nhé."
    )
    marks = ActionTagExtractor().extract(sample)
    # Action tag outside code should be extracted
    assert "laugh_soft" in marks.actions
    # Inside code: style: modern must NOT be extracted as emotion
    assert marks.emotion_hint != "modern"
    # Inside code: arr[index] must keep [index]
    assert "arr[index]" in marks.text
    # Inside code: List<String> must keep <String>
    assert "`List<String>`" in marks.text
    # Indentation and newlines in code block must be preserved
    assert "    return arr[index]" in marks.text
    assert "```python\ndef get_item(arr):" in marks.text


def test_stage_transformer_chain_preserves_code_and_cleans_tts():
    from runtime.stage_transformers import StageTransformerChain

    sample = (
        "Dưới đây là hàm tính Fibonacci:\n\n"
        "```python\n"
        "def fib(n):\n"
        "    if n <= 1:\n"
        "        return n\n"
        "    return fib(n - 1) + fib(n - 2)\n"
        "```\n\n"
        "Bạn thử xem nhé."
    )
    res = StageTransformerChain().transform(sample, language_hint="vi")
    # Display must preserve code block with indentation and newlines
    assert "```python\ndef fib(n):\n    if n <= 1:\n        return n" in res.display_text
    assert "Bạn thử xem nhé." in res.display_text
    # TTS must strip code block and keep conversational text
    assert "def fib" not in res.tts_text
    assert "```" not in res.tts_text
    assert "Dưới đây là hàm tính Fibonacci: Bạn thử xem nhé." == res.tts_text


def test_global_public_surface_committer_preserves_code_block():
    from runtime.public_surface_commit import GLOBAL_PUBLIC_SURFACE_COMMITTER

    sample = (
        "Đây là script build:\n\n"
        "```bash\n"
        "npm run build\n"
        "dotnet test\n"
        "```\n\n"
        "Chạy xong thì báo mình."
    )
    commit_surfaces = GLOBAL_PUBLIC_SURFACE_COMMITTER.build(sample)
    assert "```bash\nnpm run build\ndotnet test\n```" in commit_surfaces.display_text
    assert "npm run build" not in commit_surfaces.tts_text
    assert "Chạy xong thì báo mình." in commit_surfaces.tts_text


def test_tts_filter_japanese_mode_fallback():
    pure_code = "```python\nprint(1)\n```"
    filter_inst = TTSFilter()
    assert filter_inst.clean(pure_code, language_hint="ja") == "コードを書いておいたよ。"
    assert filter_inst.clean(pure_code, language_hint="vi") == "Mình gửi code ở đây nhé."


def test_inline_code_stripped_from_speech():
    sample = "Bạn dùng lệnh `git push origin main` để đẩy code lên."
    speech = TTSFilter().clean(sample)
    assert "`" not in speech
    assert "git push origin main" not in speech
    assert "để đẩy code lên." in speech


def test_unclosed_code_fence_stripped_from_tts():
    sample = "Code bị cắt nửa chừng nè:\n```python\ndef unfinished():\n    x = 1"
    clean = _clean_text(sample)
    assert "unfinished" not in clean
    assert "def" not in clean
    assert "Code bị cắt nửa chừng nè:" in clean

    clean_markup = _normalize_lines(sample)
    assert not any("unfinished" in line for line in clean_markup)


def test_prepare_public_output_pure_code_flow(monkeypatch):
    from runtime.conversation.public_output_artifact import prepare_public_output
    from domain import Decision
    import runtime.runtime_settings_store as rss

    class DummyMove:
        move_id = "move_code_1"

    class DummyTurn:
        delivery_id = "deliv_code_1"
        session_id = "sess_1"
        generation = 1
        run_id = "run_1"
        move = DummyMove()

    # 1. Vietnamese flow
    monkeypatch.setattr(rss, "runtime_text", lambda section, key, default="": "vi")
    decision_vi = Decision(
        text="```python\ndef hello():\n    return 'hi'\n```",
        animation="idle",
        should_speak=True,
    )
    payload_vi = {
        "text": decision_vi.text,
        "speech_text": "Mình gửi code ở đây nhé.",
        "display_language": "vi",
        "speech_language": "vi",
    }
    prepared_vi = prepare_public_output(
        event_id="evt_vi_1",
        turn=DummyTurn(),
        channel="chat",
        decision=decision_vi,
        payload=payload_vi,
    )
    assert "def hello():" in prepared_vi.display_text
    assert "    return 'hi'" in prepared_vi.display_text
    assert "def hello" not in prepared_vi.speech_text
    assert "Mình gửi code ở đây nhé." in prepared_vi.speech_text

    # 2. Japanese flow
    monkeypatch.setattr(rss, "runtime_text", lambda section, key, default="": "ja")
    decision_ja = Decision(
        text="```python\ndef hello():\n    return 'hi'\n```",
        animation="idle",
        should_speak=True,
    )
    payload_ja = {
        "text": decision_ja.text,
        "speech_text": "コードを書いておいたよ。",
        "display_language": "vi",
        "speech_language": "ja",
    }
    prepared_ja = prepare_public_output(
        event_id="evt_ja_1",
        turn=DummyTurn(),
        channel="chat",
        decision=decision_ja,
        payload=payload_ja,
    )
    assert "def hello():" in prepared_ja.display_text
    assert "    return 'hi'" in prepared_ja.display_text
    assert "def hello" not in prepared_ja.speech_text
    assert "コードを書いておいたよ。" in prepared_ja.speech_text
