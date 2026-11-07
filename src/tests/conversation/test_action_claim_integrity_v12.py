# -*- coding: utf-8 -*-
"""Rào nhận action đã hoàn tất: không self-verify, không tin manifest rỗng."""
from types import SimpleNamespace

import pytest

from runtime.conversation.action_claim_integrity import (
    detected_completed_actions, enforce_completed_action_claims,
)
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.contracts import ConversationEvent
from runtime.conversation.runtime_writer import RuntimeConversationWriter
from runtime.conversation.kernel import KernelTurn
from runtime.conversation.contracts import NextConversationMove


EVENT = ConversationEvent(event_id='user:8', session_id='session:7',
                          generation=4, kind='user_turn', text='xin chào')
RUN = 'run:4'


def receipt(*, action_id='send_email', status='completed', success=True,
            event=EVENT, run_id=RUN, capability='action'):
    registry = RootCapabilityRegistry({})
    r = registry.record_result(
        name=capability, invocation_id='inv-1', event=event, run_id=run_id,
        result={'status':status, 'success':success, 'action_id':action_id},
    )
    return r


def check(surfaces, receipts=()):
    enforce_completed_action_claims(
        surfaces=surfaces, receipts=receipts, event_id=EVENT.event_id,
        session_id=EVENT.session_id, generation=EVENT.generation, run_id=RUN,
    )


@pytest.mark.parametrize('text', [
    'Tớ đã gửi email rồi.', 'Đã gửi file cho bạn.',
    'I have sent that email.', "I've sent that email.", 'ファイルを送ったよ。',
    'Tớ đã chạy test xong.', 'Tớ đã xóa file.',
])
def test_claim_without_receipt_rejected(text):
    with pytest.raises(RuntimeError, match='public_action_receipt_missing'):
        check((text,))


@pytest.mark.parametrize('r', [
    receipt(status='started'), receipt(status='failed', success=True),
    receipt(status='completed', success=False),
    receipt(action_id='project_run_tests'),
    receipt(capability='research'),
    receipt(run_id='run:stale'),
    receipt(event=ConversationEvent(event_id='other', session_id=EVENT.session_id,
                                    generation=4, kind='user_turn')),
    receipt(event=ConversationEvent(event_id=EVENT.event_id, session_id='other',
                                    generation=4, kind='user_turn')),
    receipt(event=ConversationEvent(event_id=EVENT.event_id, session_id=EVENT.session_id,
                                    generation=3, kind='user_turn')),
])
def test_wrong_receipt_never_proves_sent_email(r):
    with pytest.raises(RuntimeError, match='public_action_receipt_missing:send'):
        check(('Tớ đã gửi email cho cậu.',), (r,))


def test_valid_exact_action_receipt_accepted():
    check(('Tớ đã gửi email.', 'メールを送信しました。'), (receipt(),))


def test_multiple_action_claims_need_independent_receipts():
    with pytest.raises(RuntimeError, match='delete'):
        check(('Tớ đã gửi email. Tớ đã xóa file.',), (receipt(),))
    check(('Tớ đã gửi email. Tớ đã xóa file.',),
          (receipt(), receipt(action_id='delete_file')))


@pytest.mark.parametrize('safe_text', [
    'Chào cậu! Tớ nhớ cậu.', 'Tớ chưa gửi email.',
    'Tớ đã đặt câu hỏi hơi kỳ nhỉ.', 'Tớ đã mở lòng hơn một chút.',
    'Mình đã lưu ý lời cậu nói.', 'Tớ vừa đọc hết tin nhắn của cậu.',
    'Tớ không chắc đó có thật không.', 'I have not sent it.', '送ったらどうなる？',
])
def test_normal_chat_is_not_gated_by_keyword_only(safe_text):
    assert not detected_completed_actions((safe_text,))
    check((safe_text,))


class _Router:
    def __init__(self, payload):
        self.payload = payload
    def generate_public_reply(self, **_kwargs):
        return dict(self.payload)


class _Host:
    def __init__(self, payload):
        self.ai_model_router = _Router(payload)


def writer(payload, registry=None):
    root = NextConversationMove(intent='trò chuyện', say='phản hồi', move_id='move-1')
    turn = KernelTurn(session_id=EVENT.session_id, generation=EVENT.generation,
                      run_id=RUN, move=root, note='')
    return RuntimeConversationWriter(_Host(payload)).realize(
        turn=turn, event=EVENT, recent_history=(),
        runtime_context={'__root_capabilities': registry} if registry else {},
    )


def test_writer_manifest_empty_does_not_bypass_action_guard():
    with pytest.raises(RuntimeError, match='public_action_receipt_missing'):
        writer({'text': 'Chào cậu.', 'speech_text':'送ったよ。', 'claim_manifest':[]})


def test_writer_allows_effect_supported_by_runtime_registry():
    registry = RootCapabilityRegistry({})
    registry.record_result(name='action', invocation_id='real:1', event=EVENT,
                           run_id=RUN, result={
                               'status':'completed', 'success':True,
                               'action_id':'send_email',
                           })
    rendered = writer({'text':'Tớ đã gửi email rồi.',
                       'speech_text':'メールを送信しました。', 'claim_manifest': []}, registry)
    assert len(rendered.metadata['action_claim_receipts']) == 1
    assert rendered.surface.display_text.startswith('Tớ đã gửi')


def test_delivery_last_boundary_rejects_unreceipted_tts_action():
    # Dù writer khác trả prepared artifact, Delivery vẫn không phát ra body.
    from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter
    from runtime.conversation.writer import RenderedConversationMove
    from runtime.conversation.delivery import SurfaceEnvelope
    from test_character_output_artifact_v1 import prepared, turn

    host, artifact = prepared('Chào cậu.', 'ファイルを送ったよ。')
    rendered = RenderedConversationMove(
        surface=SurfaceEnvelope(display_text='Chào cậu.', speech_text='ファイルを送ったよ。'),
        metadata={'event_id': artifact.event_id, 'prepared_public_output':artifact},
    )
    with pytest.raises(RuntimeError, match='public_action_receipt_missing:send'):
        RuntimeConversationDeliveryAdapter(host).deliver(
            turn=turn(), rendered=rendered, delivery_id=artifact.delivery_id,
        )
    assert host.sent == [] and host.chat_history == []
