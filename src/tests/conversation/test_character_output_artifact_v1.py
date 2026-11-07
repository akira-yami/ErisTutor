# -*- coding: utf-8 -*-
"""Task 4: chuẩn bị output không gây public side effect."""
import json
from dataclasses import replace
from types import SimpleNamespace
import pytest

from domain import Decision
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import KernelTurn
from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter
from runtime.conversation.public_output_artifact import (
    prepare_public_output, tts_clean, sha, lock_verified_segments, validate_prepared,
)


class Parser:
    def parse(self, raw, **kwargs):
        obj = json.loads(raw)
        return Decision(text=obj['text'], speech_text=obj.get('speech_text',''),
                        animation='stand',should_speak=True)


def turn():
    return KernelTurn(session_id='s',generation=3,run_id='run:3',
                      move=NextConversationMove(intent='trả lời',say='nói',move_id='move:fixed'),
                      note='',delivery_id='delivery:abc')


def event():
    return ConversationEvent(event_id='e',session_id='s',generation=3,
                             kind='user_turn',text='xin chào')


def host():
    return SimpleNamespace(parser=Parser(), current_stage_user_text='xin chào',
                           chat_history=[], sent=[], events=[], ai_stage_planner=None)


def prepared(text='Chào cậu.',speech='こんにちは。'):
    h=host()
    return h,RuntimeConversationDeliveryAdapter(h).prepare_candidate(
        turn=turn(),event=event(),payload={'text':text,'speech_text':speech,
                                          'claim_manifest':[]},source_fingerprint='proof',
    )


def test_prepare_has_no_public_side_effects():
    h,p=prepared()
    assert h.sent == [] and h.chat_history == [] and h.events == []
    assert p.event_id == 'e' and p.delivery_id == 'delivery:abc'
    assert p.display_text == 'Chào cậu.' and p.memory_text == 'Chào cậu.'
    assert p.speech_text == 'こんにちは。'


def test_real_stage_segments_receive_exact_final_hash():
    _,p=prepared('Chào cậu.', 'こんにちは。')
    assert p.stage_frame.display_text == p.display_text
    assert p.segments and p.segment_hashes == tuple(sha(tts_clean(x.tts_text)) for x in p.segments)
    locked=lock_verified_segments(p)
    assert all(f.speech_contract['verified_transcript_hash']==p.segment_hashes[i] for i,f in enumerate(locked.segments))
    validate_prepared(locked)


def test_japanese_wrong_language_does_not_enter_late_tts_repair():
    with pytest.raises(RuntimeError,match='v03_speech_language_mismatch'):
        prepared(speech='Tiếng Việt chưa dịch')


def test_udp_surface_is_idempotently_sanitized_before_send():
    _,p=prepared(text='Chào 🙂')
    # Stage transformer đã loại non-BMP; transport chạy lại không được làm đổi.
    from runtime.conversation.public_output_artifact import udp_clean
    assert udp_clean(p.display_text)==p.display_text


def test_prepared_does_not_allow_stage_mutation():
    _,p=prepared()
    p2=replace(p,display_text='Câu khác')
    with pytest.raises(RuntimeError,match='stage_display_tampered'):
        validate_prepared(p2)
