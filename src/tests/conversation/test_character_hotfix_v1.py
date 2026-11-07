# -*- coding: utf-8 -*-
"""Hotfix production: mỗi lần Eris im lặng phải có reason code có thể truy vết.

Dùng fake provider, không gọi mạng hoặc ElevenLabs. Mọi lỗi đều fail-closed:
không phát lời, không ghi memory và không tự tạo receipt giả.
"""
from __future__ import annotations

from types import SimpleNamespace

from runtime.conversation.claim_evidence import build_evidence_registry
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.runtime_writer import RuntimeConversationWriter
from test_character_claim_integrity_v1 import Parser, Router


class _Log:
    def __init__(self):
        self.lines = []

    def warning(self, template, *args):
        self.lines.append(('warning', template % args))

    def info(self, template, *args):
        self.lines.append(('info', template % args))


class _Agent:
    def decide(self, **kwargs):
        return NextConversationMove(intent='chào hỏi', say='chào người dùng')


class _Delivery:
    def __init__(self):
        self.calls = []

    def deliver(self, **kwargs):
        self.calls.append(kwargs)


def _run_with_router(router):
    log = _Log()
    host = SimpleNamespace(
        ai_model_router=router, parser=Parser(), logger=log,
        ai_stage_planner=None, current_stage_user_text='xin chào',
        chat_history=[], sent=[], last_text='',
    )
    delivery = _Delivery()
    kernel = ConversationKernel(_Agent(), writer=RuntimeConversationWriter(host),
                                delivery=delivery, logger=log)
    event = ConversationEvent(event_id='chat:hotfix', session_id='hotfix',
                              kind='user_turn', text='xin chào')
    registry = build_evidence_registry(
        event=event, state=None, affect=None, report=None,
        ledger_turns=(), memory_records=(), sampled_at=1.0,
    )
    outcome = kernel.handle_event(event, runtime_context={'__evidence_registry': registry})
    return outcome, log, delivery, router


def test_missing_manifest_does_not_silence_valid_writer_output():
    router=Router()
    router.payloads=[{'text':'Tớ cần suy nghĩ thêm.', 'speech_text':'少し考えたい。'}]
    outcome,log,delivery,router=_run_with_router(router)
    assert outcome.status=='delivering' and len(delivery.calls)==1
    assert len(router.writer_calls)==1 and router.worker_calls==[]
    assert any('stage=prepared status=pass code=WRITER_DIRECT' in line for _,line in log.lines)
    assert all('xin chào' not in line for _,line in log.lines)


def test_dead_verifier_provider_is_never_consulted():
    router=Router()
    router.payloads=[{'text':'Tớ cần suy nghĩ thêm.', 'speech_text':'少し考えたい。','claim_manifest':[]}]
    router.invoke_structured_worker=lambda *_args,**_kwargs: (_ for _ in ()).throw(RuntimeError('must not call'))
    outcome,log,delivery,router=_run_with_router(router)
    assert outcome.status=='delivering' and len(delivery.calls)==1
    assert len(router.writer_calls)==1


def test_successful_greeting_needs_no_semantic_receipt():
    router=Router();outcome,log,delivery,router=_run_with_router(router)
    assert outcome.status=='delivering' and outcome.failure_reason==''
    assert len(router.writer_calls)==1 and router.worker_calls==[]
    assert len(delivery.calls)==1
    rendered=delivery.calls[0]['rendered']
    assert 'claim_verification_receipt' not in rendered.metadata
    assert rendered.metadata['prepared_public_output'].sealed_hash
    assert any('stage=prepared status=pass' in line for _,line in log.lines)


def test_provider_exception_message_never_reaches_log_or_rewrite():
    """Nội dung lỗi của provider có thể chứa key/response, chỉ dùng tên lớp lỗi."""
    from runtime.conversation.runtime_writer import _v03_error_code

    sensitive = RuntimeError('Authorization failed: Bearer sk-user-secret-example')
    assert _v03_error_code(sensitive) == 'RuntimeError'
    assert _v03_error_code(RuntimeError('v03_speech_language_mismatch')) == 'v03_speech_language_mismatch'
