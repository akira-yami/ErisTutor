# -*- coding: utf-8 -*-
"""Eris nói trực tiếp: test qua Kernel -> Writer -> Delivery, không verifier.

Dữ liệu hội thoại chỉ là fixture kiểm thử. Không dùng API ngoài, không ghi
canonical memory thật hoặc thực thi action bên ngoài.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from runtime.conversation.claim_evidence import build_evidence_registry
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.runtime_writer import RuntimeConversationWriter
from test_character_claim_integrity_v1 import Parser, Router, _verified_delivery_fixture


@pytest.mark.parametrize('scenario,text,speech', [
    ('casual','Ừ, tôi nghe đây.','うん、聞いてるよ。'),
    ('exact_number','7 trang rồi còn 13 trang, tổng 20 trang.','7ページと13ページで、全部20ページだね。'),
    ('screen_unknown','Tớ không có dữ liệu màn hình lúc này.','今は画面の情報がないよ。'),
    ('correction','À, ý cậu là cà phê chứ không phải trà.','お茶じゃなくてコーヒーだね。'),
    ('memory_uncertainty','Tôi không nhớ được chuyến đi đó.','その旅行は覚えていない。'),
    ('hypothetical','Nếu có một con mèo thì sao nhỉ?','猫がいたらどうかな。'),
    ('unsupported_memory','Chắc ta từng gặp nhau ở Kyoto.','前に京都で会ったかもね。'),
])
def test_direct_turn_always_bypasses_semantic_verifier(scenario,text,speech):
    router=Router()
    # Bỏ hẳn manifest như trường hợp provider trả thiếu metadata tùy chọn.
    router.payloads=[{'text':text,'speech_text':speech}]
    def forbidden(*args,**kwargs):
        raise AssertionError('Verifier must not be called: '+scenario)
    router.invoke_structured_worker=forbidden
    host=SimpleNamespace(ai_model_router=router,parser=Parser(),
        ai_stage_planner=None,current_stage_user_text='',chat_history=[],sent=[],last_text='',
        character_claim_verifier=SimpleNamespace(verify=forbidden))
    delivered=[]
    class Agent:
        def decide(self,**kw):return NextConversationMove(intent='đáp tự nhiên',say=text)
    class Delivery:
        def deliver(self,**kw):delivered.append(kw)
    event=ConversationEvent(event_id='user:'+scenario,session_id='direct:'+scenario,
                             kind='user_turn',text='nói tiếp')
    evidence=build_evidence_registry(event=event,state=None,affect=None,report=None,
        ledger_turns=(),memory_records=(),sampled_at=1.)
    result=ConversationKernel(Agent(),writer=RuntimeConversationWriter(host),
        delivery=Delivery()).handle_event(event,runtime_context={'__evidence_registry':evidence})
    assert result.status=='delivering' and len(delivered)==1
    assert len(router.writer_calls)==1 and router.worker_calls==[]
    rendered=delivered[0]['rendered']
    assert rendered.surface.display_text==text
    assert rendered.metadata['prepared_public_output'].sealed_hash
    assert 'claim_verification_receipt' not in rendered.metadata
    assert host.sent==[] and host.chat_history==[]


def test_missing_evidence_registry_does_not_silence_casual_chat():
    router=Router()
    host=SimpleNamespace(ai_model_router=router,parser=Parser(),ai_stage_planner=None,
                         current_stage_user_text='',chat_history=[],sent=[],last_text='')
    delivered=[]
    class Agent:
        def decide(self,**kw):return NextConversationMove(intent='chào',say='Chào cậu')
    class Delivery:
        def deliver(self,**kw):delivered.append(kw)
    event=ConversationEvent(event_id='user:no-evidence',session_id='direct:no-evidence',
                             kind='user_turn',text='chào')
    result=ConversationKernel(Agent(),writer=RuntimeConversationWriter(host),
        delivery=Delivery()).handle_event(event,runtime_context={})
    assert result.status=='delivering' and len(delivered)==1
    assert delivered[0]['rendered'].metadata['prepared_public_output'].source_fingerprint==''


def test_delivery_still_rejects_modified_transcript_without_verifier_receipt():
    h,turn,rendered=_verified_delivery_fixture()
    corrupt=replace(rendered,metadata={**rendered.metadata,
          'prepared_public_output':replace(rendered.metadata['prepared_public_output'],
                                            speech_text='違うよ。')})
    with pytest.raises(RuntimeError,match='public_artifact_seal_mismatch'):
        RuntimeConversationDeliveryAdapter(h).deliver(turn=turn,rendered=corrupt,
                                                       delivery_id=turn.delivery_id)
    assert h.sent==[] and h.history==[]


def test_real_delivery_is_once_and_does_not_require_verification_receipt():
    h,turn,rendered=_verified_delivery_fixture()
    adapter=RuntimeConversationDeliveryAdapter(h)
    adapter.deliver(turn=turn,rendered=rendered,delivery_id=turn.delivery_id)
    adapter.deliver(turn=turn,rendered=rendered,delivery_id=turn.delivery_id)
    assert len(h.sent)==1 and len(h.history)==1
    sent,options=h.sent[0]
    assert 'verification' not in options
    assert options['prepared'].segments[0].speech_contract['verified_transcript_hash']
    assert sent is rendered.metadata['prepared_public_output'].decision
