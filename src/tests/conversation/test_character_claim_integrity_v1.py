# -*- coding: utf-8 -*-
"""Task 6: thử kernel/writer/prepared receipt với fake model, không gọi dịch vụ trả phí."""
from dataclasses import replace
from types import SimpleNamespace
import pytest

from runtime.conversation.claim_evidence import build_evidence_registry
from runtime.conversation.claim_verifier import ClaimVerificationResult
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.runtime_writer import RuntimeConversationWriter
from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter
from test_character_output_artifact_v1 import Parser


class Router:
    def __init__(self):
        self.writer_calls=[]
        self.payloads=[{'text':'Chào cậu.','speech_text':'こんにちは。','claim_manifest':[]}]
        self.observer_client=object()
        self.surface_client=None
        self.worker_calls=[]
        self.verdicts=[]
    def generate_public_reply(self,**kwargs):
        self.writer_calls.append(kwargs)
        return self.payloads[min(len(self.writer_calls)-1,len(self.payloads)-1)]
    def invoke_structured_worker(self,client,**kwargs):
        self.worker_calls.append(kwargs)
        if self.verdicts:
            return self.verdicts.pop(0)
        return {'status':'pass','surface_equivalence':True,'memory_equivalence':True,'findings':[]}


def host(router):
    return SimpleNamespace(ai_model_router=router,parser=Parser(),
                           ai_stage_planner=None,current_stage_user_text='xin chào',
                           chat_history=[], sent=[],last_text='')


def event():
    return ConversationEvent(event_id='user:1',session_id='s',kind='user_turn',text='xin chào')


def test_kernel_root_writer_delivers_without_semantic_verifier():
    router=Router();h=host(router)
    router.payloads=[{'text':'Tớ chưa chắc điều đó.', 'speech_text':'それはわからない。', 'claim_manifest':[]}]
    deliveries=[]
    class Agent:
        def decide(self,**kwargs): return NextConversationMove(intent='đáp',say='chào lại')
    class Delivery:
        def deliver(self,**kwargs): deliveries.append(kwargs)
    kernel=ConversationKernel(Agent(),writer=RuntimeConversationWriter(h),delivery=Delivery())
    e=event();r=build_evidence_registry(event=e,state=None,affect=None,
        report=None,ledger_turns=(),memory_records=(),sampled_at=1.)
    outcome=kernel.handle_event(e,runtime_context={'__evidence_registry':r})
    assert outcome.status=='delivering'
    assert len(router.writer_calls)==1 and router.worker_calls==[]
    assert 'claim_verification_receipt' not in outcome.rendered.metadata
    assert outcome.rendered.metadata['prepared_public_output'].delivery_id==outcome.delivery_id
    assert len(deliveries)==1 and h.sent==[] and h.chat_history==[]


def test_missing_manifest_is_not_reason_for_silence():
    router=Router(); h=host(router)
    router.payloads=[{'text':'Tớ chưa chắc điều đó.','speech_text':'それはわからない。'}]
    class Agent:
        def decide(self,**kwargs):return NextConversationMove(intent='answer',say='answer')
    class Delivery:
        def __init__(self):self.calls=[]
        def deliver(self,**kwargs):self.calls.append(kwargs)
    d=Delivery();e=event();r=build_evidence_registry(event=e,state=None,affect=None,
        report=None,ledger_turns=(),memory_records=(),sampled_at=1.)
    outcome=ConversationKernel(Agent(),writer=RuntimeConversationWriter(h),delivery=d).handle_event(
        e,runtime_context={'__evidence_registry':r})
    assert outcome.status=='delivering' and len(d.calls)==1
    assert len(router.writer_calls)==1 and not router.worker_calls


def test_semantic_verifier_failure_cannot_block_writer():
    router=Router();h=host(router)
    router.payloads=[{'text':'Tớ chưa chắc điều đó.','speech_text':'それはわからない。'}]
    router.verdicts=[{'status':'fail','surface_equivalence':False,'memory_equivalence':True,'findings':[]}]
    class Agent:
        def decide(self,**kwargs):return NextConversationMove(intent='answer',say='answer')
    class Delivery:
        def __init__(self):self.calls=[]
        def deliver(self,**kwargs):self.calls.append(kwargs)
    d=Delivery();e=event();r=build_evidence_registry(event=e,state=None,affect=None,
        report=None,ledger_turns=(),memory_records=(),sampled_at=1.)
    outcome=ConversationKernel(Agent(),writer=RuntimeConversationWriter(h),delivery=d).handle_event(
        e,runtime_context={'__evidence_registry':r})
    assert outcome.status=='delivering' and len(d.calls)==1
    assert len(router.writer_calls)==1 and router.worker_calls==[]


def _verified_delivery_fixture():
    """Host giả ghi đúng side effects, còn prepared/receipt là object thật."""
    from runtime.conversation.writer import RenderedConversationMove
    from runtime.conversation.delivery import SurfaceEnvelope
    t=__import__('test_character_output_artifact_v1', fromlist=['turn']).turn()
    ev=event()
    # Id của event và source phải cùng nhau (chống chứng cứ xuyên lượt).
    r=build_evidence_registry(event=ev,state=None,affect=None,
        report=None,ledger_turns=(),memory_records=(),sampled_at=1.)
    h=host(Router());h.sent=[];h.history=[];h.positions=[];h.chat_history=[]
    h.conversation_kernel=SimpleNamespace(store=SimpleNamespace(load=lambda sid: SimpleNamespace(
        generation=t.generation,active_run_id=t.run_id,active_delivery_id=t.delivery_id)))
    h._send_decision_to_body=lambda dec,**kw: h.sent.append((dec,kw)) or True
    h._append_chat_history=lambda role,msg,**kw: h.history.append((role,msg))
    h._apply_position_update=lambda dec:h.positions.append(dec)
    h._ensure_session_turn_ledger=lambda:SimpleNamespace(record_assistant=lambda text,**kw:SimpleNamespace(index=1))
    p=RuntimeConversationDeliveryAdapter(h).prepare_candidate(
        turn=t,event=ev,payload={'text':'Chào cậu.','speech_text':'こんにちは。','claim_manifest':[]},
        source_fingerprint=r.fingerprint)
    rendered=RenderedConversationMove(surface=SurfaceEnvelope(display_text=p.display_text,
        speech_text=p.speech_text,language='vi'),decision_payload=t.move.to_dict(),
        metadata={'prepared_public_output':p,'evidence_registry':r,'event_kind':'user_turn','user_text':ev.text})
    return h,t,rendered


def test_real_delivery_adapter_uses_locked_verified_artifact_once():
    h,t,rendered=_verified_delivery_fixture()
    adapter=RuntimeConversationDeliveryAdapter(h)
    adapter.deliver(turn=t,rendered=rendered,delivery_id=t.delivery_id)
    adapter.deliver(turn=t,rendered=rendered,delivery_id=t.delivery_id)
    assert len(h.sent)==1 and len(h.history)==1 and len(h.positions)==1
    sent,kwargs=h.sent[0]
    assert sent is rendered.metadata['prepared_public_output'].decision
    assert kwargs['prepared'].segments[0].speech_contract['verified_transcript_hash']
    assert h.history[0]==('assistant',rendered.metadata['prepared_public_output'].memory_text)


def test_real_delivery_adapter_tampered_artifact_has_no_public_commit():
    from dataclasses import replace as rep
    h,t,rendered=_verified_delivery_fixture()
    meta=dict(rendered.metadata)
    meta['prepared_public_output']=rep(meta['prepared_public_output'], speech_text='sai')
    corrupt=rep(rendered,metadata=meta)
    with pytest.raises(RuntimeError,match='public_artifact_seal_mismatch'):
        RuntimeConversationDeliveryAdapter(h).deliver(turn=t,rendered=corrupt,
                                                       delivery_id=t.delivery_id)
    assert h.sent==[] and h.history==[] and h.positions==[]


def test_real_delivery_adapter_stale_generation_blocks_public_effects():
    h,t,rendered=_verified_delivery_fixture()
    h.conversation_kernel.store.load=lambda sid:SimpleNamespace(
        generation=t.generation+1,active_run_id='',active_delivery_id='')
    with pytest.raises(RuntimeError,match='v03_preempted_before_dispatch'):
        RuntimeConversationDeliveryAdapter(h).deliver(turn=t,rendered=rendered,
                                                       delivery_id=t.delivery_id)
    assert h.sent==[] and h.history==[] and h.positions==[]
