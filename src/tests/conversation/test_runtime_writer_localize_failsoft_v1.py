# -*- coding: utf-8 -*-
"""Change 3 (rescue): localize fail-soft -> luôn CÓ TIẾNG, không bao giờ câm.

_prelocalize_v03_speech chốt bản TTS Nhật TRƯỚC prepare. Trước đây nếu writer
trả speech_text rỗng thì nó return nguyên payload -> prepare raise
v03_speech_language_mismatch -> kernel câm. Giờ:
  - speech_text rỗng/không-Nhật -> dịch display sang Nhật qua SpeechLocalizer.
  - localizer được retry 1 lần trong budget (timeout 45s giữ nguyên ở localizer).
  - fail SAU retry -> KHÔNG raise mute; phát câu đệm Nhật AN TOÀN (fallback (b),
    chọn bởi user) để prepare pass và vẫn có audio. Giữ global ja-pin nguyên vẹn.
  - cancellation là preempt hợp lệ -> vẫn raise v03_speech_localization_cancelled.

Fallback (b): câu đệm là một tiếng ngập ngừng Nhật ngắn, trung tính, không bịa,
không lộ secret, không tự nhận đã làm gì — và không nghe như lỗi hệ thống.
"""
import json
from types import SimpleNamespace

import pytest

from domain import Decision
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import KernelTurn
from runtime.conversation.runtime_writer import (
    RuntimeConversationWriter, _V03_SPEECH_JA_FILLER,
)
from runtime.conversation.writer import RenderedConversationMove
from runtime.stage_contracts import _contains_japanese_script


class Parser:
    def parse(self, raw, **kwargs):
        obj = json.loads(raw)
        return Decision(text=obj['text'], speech_text=obj.get('speech_text', ''),
                        animation='stand', should_speak=True)


class FakeRouter:
    """generate_public_reply trả đúng payload đã định; không gọi model thật."""

    def __init__(self, payload):
        self._payload = payload

    def generate_public_reply(self, **kwargs):
        return dict(self._payload)


class FakeLocalizer:
    """SpeechLocalizer giả: điều khiển thành công/thất bại theo kịch bản."""

    def __init__(self, *, results=None, exc=None):
        # results: list các giá trị trả lần lượt; exc: exception ném MỖI lần gọi.
        self._results = list(results or [])
        self._exc = exc
        self.calls = 0

    def localize(self, text, *, language_hint='auto', prosody_style='',
                 strict=False, cancel_event=None, authority_check=None):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        if self._results:
            return self._results.pop(0)
        return ''


def _host(payload, localizer):
    return SimpleNamespace(
        parser=Parser(),
        ai_model_router=FakeRouter(payload),
        character_speech_localizer=localizer,
        current_stage_user_text='xin chào',
        chat_history=[], sent=[], events=[], ai_stage_planner=None,
        logger=None,
    )


def _turn():
    return KernelTurn(session_id='s', generation=3, run_id='run:3',
                      move=NextConversationMove(intent='trả lời', say='nói',
                                                move_id='move:fixed'),
                      note='', delivery_id='delivery:abc')


def _event():
    return ConversationEvent(event_id='e', session_id='s', generation=3,
                             kind='user_turn', text='xin chào')


def _realize(payload, localizer):
    writer = RuntimeConversationWriter(_host(payload, localizer))
    return writer.realize(turn=_turn(), event=_event(), recent_history=(),
                          runtime_context={})


def test_empty_speech_text_is_localized_to_japanese():
    # Writer trả display Việt + speech_text RỖNG. Localizer dịch display -> Nhật.
    loc = FakeLocalizer(results=['こんにちは。'])
    move = _realize({'text': 'Chào cậu.', 'speech_text': '', 'claim_manifest': []}, loc)
    assert isinstance(move, RenderedConversationMove)
    assert move.surface.speech_text == 'こんにちは。'
    assert loc.calls == 1


def test_non_japanese_speech_text_is_localized():
    # speech_text là romaji/Việt -> cũng được dịch sang Nhật, không raise.
    loc = FakeLocalizer(results=['はい、どうぞ。'])
    move = _realize({'text': 'Được rồi nhé.', 'speech_text': 'Duoc roi he',
                     'claim_manifest': []}, loc)
    assert isinstance(move, RenderedConversationMove)
    assert move.surface.speech_text == 'はい、どうぞ。'


def test_localizer_retried_once_then_succeeds():
    # Lần đầu trả rỗng (fail mềm), lần hai trả Nhật hợp lệ. Gọi đúng 2 lần.
    loc = FakeLocalizer(results=['', 'ええ、わかった。'])
    move = _realize({'text': 'Ừ hiểu rồi.', 'speech_text': '', 'claim_manifest': []}, loc)
    assert isinstance(move, RenderedConversationMove)
    assert move.surface.speech_text == 'ええ、わかった。'
    assert loc.calls == 2


def test_localizer_failure_falls_back_to_sound_not_mute():
    # Localizer luôn ném (non-cancel). Turn KHÔNG bị drop: realize trả move với
    # câu đệm Nhật an toàn (fallback (b)), speech_text không rỗng và là tiếng Nhật.
    loc = FakeLocalizer(exc=RuntimeError('provider down'))
    move = _realize({'text': 'Chào cậu nhé.', 'speech_text': '', 'claim_manifest': []}, loc)
    assert isinstance(move, RenderedConversationMove)
    assert move.surface.speech_text
    assert _contains_japanese_script(move.surface.speech_text)
    assert move.surface.speech_text == _V03_SPEECH_JA_FILLER
    # Đã thử đủ 2 lần trước khi dùng filler.
    assert loc.calls == 2


def test_localizer_cancellation_still_raises_cancel_code():
    # Cancellation là preempt hợp lệ, KHÔNG phải lỗi mute -> vẫn raise.
    from runtime.speech_localizer import SpeechLocalizationCancelled
    loc = FakeLocalizer(exc=SpeechLocalizationCancelled())
    with pytest.raises(RuntimeError,
                       match='v03_candidate_rejected:localize=v03_speech_localization_cancelled'):
        _realize({'text': 'Chào.', 'speech_text': '', 'claim_manifest': []}, loc)
    # Cancellation ném ngay lần đầu, không retry.
    assert loc.calls == 1
