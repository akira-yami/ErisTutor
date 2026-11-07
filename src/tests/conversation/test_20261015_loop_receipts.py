# -*- coding: utf-8 -*-
"""Hồi quy phiên 16:47: observer không cắt tiếng Nhật, Root nhận receipt.

Không dùng mạng/TTS; tạo event, parser, writer/delivery giả và SQLite thật.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from runtime.intention_authority import IntentionObserver
from runtime.conversation import ConversationEvent, ConversationKernel, NextConversationMove
from runtime.conversation.move_outcomes import MoveOutcomeLedger
from runtime.ai_orchestrated.atomic_state import SQLiteAtomicStateStore
from runtime.conversation.public_output_artifact import validate_prepared
from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter

from test_character_output_artifact_v1 import event, host, turn


@pytest.mark.parametrize('length', [409, 879, 1331, 1290, 1205, 1289])
def test_all_six_long_autonomous_candidates_retain_japanese(length):
    # Lặp đúng 6 độ dài trong báo cáo agent; không cần replay private prompt.
    display = 'Tôi kể chuyện. ' + ('một ý tưởng thú vị. ' * 90)
    display = display[:length].ljust(length, 'x')
    speech = 'こんにちは。考えてみましょう。'
    observer = IntentionObserver()
    result = observer.validate(display, {})
    assert result.text == result.original_text == display
    assert not result.rewritten
    h = host()
    prepared = RuntimeConversationDeliveryAdapter(h).prepare_candidate(
        turn=turn(), event=replace(event(), kind='autonomous_signal', text=''),
        payload={'text': display, 'speech_text': speech, 'claim_manifest': []},
        source_fingerprint='test',
    )
    assert prepared.channel == 'autonomous'
    assert prepared.display_text == display.rstrip()  # Stage renderer bỏ whitespace cuối, không bị Observer cắt 360
    assert prepared.speech_text == speech
    validate_prepared(prepared)


def test_observer_whitespace_does_not_destroy_original():
    text = '\n  Tôi sẽ nói  một câu dài.  \n' + ('Đây là nội dung. ' * 50)
    assert IntentionObserver().validate(text, {}).text == text


class Agent:
    def __init__(self, moves):
        self.moves = list(moves)
        self.contexts = []

    def decide(self, **kwargs):
        self.contexts.append(dict(kwargs))
        return self.moves.pop(0)


class Writer:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0

    def realize(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError('v03_candidate_rejected:prepare:v03_speech_language_mismatch')
        from runtime.conversation.writer import RenderedConversationMove
        from runtime.conversation.delivery import SurfaceEnvelope
        return RenderedConversationMove(surface=SurfaceEnvelope(display_text='được'), metadata={})


class Delivery:
    def __init__(self):
        self.calls = []

    def deliver(self, **kwargs):
        self.calls.append(kwargs)


def signal(label):
    return ConversationEvent(event_id=f'autonomous:{label}', session_id='s', kind='autonomous_signal')


def test_failure_receipt_reaches_root_without_public_history():
    agent = Agent([NextConversationMove(intent='kể chủ đề 2', say='vấn đề tự nguyện làm nô lệ'),
                   NextConversationMove(intent='xử lý tiếp', say='không giả vờ đã kể chủ đề 2')])
    writer = Writer(fail=True)
    kernel = ConversationKernel(agent, writer=writer, delivery=Delivery())
    first = kernel.handle_event(signal('one'), recent_history=[])
    assert first.status == 'writer_failed'
    assert kernel.snapshot('s').last_delivered_move_id == ''
    feedback = kernel.outcomes.feedback('s')
    assert feedback[-1]['status'] == 'failed_before_delivery'
    assert feedback[-1]['reason'].startswith('v03_candidate_rejected:prepare')
    assert feedback[-1]['outcome_summary'] == ('Move kể chủ đề 2 thất bại ở prepare; chưa gửi tới người dùng.')
    kernel.handle_event(signal('two'), recent_history=[])
    in_context = agent.contexts[1]['runtime_context']['conversation_move_receipts']
    assert in_context[-1]['move_id'] == first.turn.move.move_id
    assert in_context[-1]['intent'] == 'kể chủ đề 2'
    assert in_context[-1]['status'] == 'failed_before_delivery'


def test_delivery_settlement_and_cancellation_never_replay():
    agent = Agent([NextConversationMove(intent='lượt 1', say='một'),
                   NextConversationMove(intent='lượt 2', say='hai')])
    d = Delivery()
    kernel = ConversationKernel(agent, writer=Writer(), delivery=d)
    first = kernel.handle_event(ConversationEvent.user_turn(session_id='s', text='hi'))
    assert first.status == 'delivering'
    assert kernel.outcomes.feedback('s')[-1]['status'] == 'queued_for_audio'
    # User chen ngang trước khi TTS xong; callback của câu cũ đến sau phải bị loại.
    second = kernel.handle_event(ConversationEvent.user_turn(session_id='s', text='stop'))
    assert second.status == 'delivering'
    rows = kernel.outcomes.feedback('s')
    assert rows[-2]['status'] == 'cancelled'
    assert rows[-1]['status'] == 'queued_for_audio'
    assert not kernel.settle_delivery_id(delivery_id=first.delivery_id, successful=True)
    assert kernel.settle_delivery_id(delivery_id=second.delivery_id, successful=True)
    assert kernel.outcomes.feedback('s')[-1]['status'] == 'delivered'
    assert kernel.snapshot('s').last_delivered_move_id == second.turn.move.move_id
    assert not kernel.settle_delivery_id(delivery_id=second.delivery_id, successful=True)


def test_move_receipts_persist_outside_model_session(tmp_path):
    db = tmp_path / 'move.sqlite'
    store1 = SQLiteAtomicStateStore(db)
    ledger1 = MoveOutcomeLedger(atomic_store=store1)
    ledger1.update(session_id='s', generation=3, run_id='run:1', move_id='move:2',
                   event_id='event:timer', status='failed_before_delivery',
                   intent='kể chủ đề 2', reason='prepare_failed')
    ledger2 = MoveOutcomeLedger(atomic_store=SQLiteAtomicStateStore(db))
    assert ledger2.feedback('s')[0]['intent'] == 'kể chủ đề 2'
    assert ledger2.feedback('s')[0]['status'] == 'failed_before_delivery'
    # Dữ liệu này không nằm trong RootConversationSession._messages hay public history.
    assert ledger2.feedback('other') == []


def test_two_failed_background_moves_trip_short_circuit_breaker():
    agent = Agent([NextConversationMove(intent='2', say='hai'),
                   NextConversationMove(intent='3', say='ba')])
    w = Writer(fail=True)
    kernel = ConversationKernel(agent, writer=w, delivery=Delivery())
    assert kernel.handle_event(signal('one')).status == 'writer_failed'
    assert kernel.handle_event(signal('two')).status == 'writer_failed'
    assert kernel.handle_event(signal('three')).status == 'background_failure_cooldown'
    assert len(agent.contexts) == 2
    assert w.calls == 2


def test_model_payload_explicitly_contains_move_receipts():
    from runtime.conversation.agent import RuntimeModelRootAgent
    context = {'conversation_move_receipts': [{'move_id': 'move:1', 'status': 'failed_before_delivery'}]}
    payload = RuntimeModelRootAgent._payload(event=signal('test'), recent_history=[],
                                            note='', runtime_context=context,
                                            tool_results=[], step_index=0)
    assert payload['runtime_context']['conversation_move_receipts'] == context['conversation_move_receipts']


def test_audio_cancelled_is_distinct_from_failed_delivery():
    agent = Agent([NextConversationMove(intent='đang kể', say='câu dài')])
    kernel = ConversationKernel(agent, writer=Writer(), delivery=Delivery())
    result = kernel.handle_event(ConversationEvent.user_turn(session_id='s', text='kể tiếp'))
    assert result.status == 'delivering'
    assert kernel.mark_audio_started(delivery_id=result.delivery_id)
    assert kernel.outcomes.feedback('s')[-1]['status'] == 'delivering'
    assert kernel.settle_delivery_id(delivery_id=result.delivery_id,
                                     successful=False, cancelled=True)
    receipt = kernel.outcomes.feedback('s')[-1]
    assert receipt['status'] == 'cancelled'
    assert receipt['reason'] == 'tts_cancelled'
    assert kernel.snapshot('s').last_delivered_move_id == ''


def test_restart_reconciles_unconfirmed_without_replaying(tmp_path):
    from runtime.conversation.session_store import ConversationSessionStore
    db = tmp_path / 'restart.sqlite'
    atomic1 = SQLiteAtomicStateStore(db)
    outcomes1 = MoveOutcomeLedger(atomic_store=atomic1)
    outcomes1.update(session_id='s', generation=2, run_id='run:old',
                     move_id='move:old', status='delivering',
                     intent='lời cũ', delivery_id='delivery:old')
    atomic2 = SQLiteAtomicStateStore(db)
    outcomes2 = MoveOutcomeLedger(atomic_store=atomic2)
    agent = Agent([NextConversationMove(intent='mới', say='mới')])
    d = Delivery()
    kernel = ConversationKernel(agent,
                                store=ConversationSessionStore(atomic_store=atomic2),
                                outcomes=outcomes2, writer=Writer(), delivery=d)
    result = kernel.handle_event(signal('restart'))
    assert result.status == 'delivering'
    receipts = agent.contexts[0]['runtime_context']['conversation_move_receipts']
    assert receipts[0]['status'] == 'unconfirmed_after_restart'
    assert outcomes2.feedback('s')[0]['status'] == 'unconfirmed_after_restart'
    assert len(d.calls) == 1  # Chỉ lời mới, không replay lời cũ


def test_failed_due_lease_is_consumed_instead_of_released():
    from runtime.conversation import ConversationAfter
    from runtime.conversation.continuation import ContinuationLeaseScheduler
    scheduler = ContinuationLeaseScheduler()
    agent = Agent([NextConversationMove(intent='tiếp ý cũ', say='câu tiếp')])
    kernel = ConversationKernel(agent, writer=Writer(fail=True),
                                delivery=Delivery(), continuation=scheduler)
    generation = kernel.preempt_for_user('s').generation
    scheduler.schedule(session_id='s', generation=generation,
                       move_id='move:source', after=ConversationAfter.CONTINUE_SELF,
                       now=10., continue_self_delay=0)
    result = kernel.handle_due_continuation(session_id='s', now=11.)
    assert result is not None and result.status == 'writer_failed'
    assert scheduler.peek('s') is None  # Không tự thử lại theo timer
    assert len(agent.contexts) == 1
