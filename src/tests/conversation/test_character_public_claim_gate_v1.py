# -*- coding: utf-8 -*-
"""Kiểm chứng risk-based trên pipeline thật; không gọi DeepSeek/TTS mạng."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from runtime.claim_domain_contract import inspect_claim_manifest
from runtime.conversation.claim_evidence import build_evidence_registry
from runtime.conversation.claim_verifier import (
    ClaimVerificationResult, build_verification_receipt, validate_receipt,
    normalize_verifier_wire_format, validate_verifier_result,
)
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.public_claim_gate import (
    certify_phatic, can_normalize_empty_manifest, undeclared_high_risk_claims,
)
from runtime.conversation.runtime_writer import RuntimeConversationWriter
from test_character_claim_integrity_v1 import Router, Parser
from test_character_output_artifact_v1 import prepared as make_prepared, turn as fixed_turn


class _Agent:
    def decide(self, **kwargs):
        return NextConversationMove(intent='tự nhiên', say='đáp đúng lượt')


class _Delivery:
    def __init__(self):
        self.calls = []

    def deliver(self, **kwargs):
        self.calls.append(kwargs)


def _run(payload, *, kind='user_turn', verdict=None):
    router = Router()
    router.payloads = [dict(payload)]
    if verdict is not None:
        router.invoke_structured_worker = lambda *_a, **_k: verdict
    host = SimpleNamespace(
        ai_model_router=router, parser=Parser(),
        current_stage_user_text='haloo', ai_stage_planner=None,
        chat_history=[], last_text='', sent=[],
    )
    delivery = _Delivery()
    event = ConversationEvent(event_id='user:hello' if kind == 'user_turn' else 'auto:hello',
                              session_id='phatic-test', kind=kind,
                              text='haloo' if kind == 'user_turn' else '')
    registry = build_evidence_registry(
        event=event, state=None, affect=None, report=None,
        ledger_turns=(), memory_records=(), sampled_at=1.0,
    )
    kernel = ConversationKernel(
        _Agent(), writer=RuntimeConversationWriter(host), delivery=delivery,
    )
    outcome = kernel.handle_event(event, runtime_context={'__evidence_registry':registry})
    return outcome, router, delivery, host


@pytest.mark.parametrize('manifest', [[], None, '[]'])
@pytest.mark.parametrize('kind', ['user_turn', 'autonomous_signal'])
def test_greeting_delivers_even_if_ds2api_manifest_is_empty_or_malformed(kind, manifest):
    payload = {'text':'Chào cậu.', 'speech_text':'こんにちは。'}
    if manifest is not None:
        payload['claim_manifest'] = manifest
    outcome, router, delivery, host = _run(payload, kind=kind)
    assert outcome.status == 'delivering'
    assert len(router.writer_calls) == 1
    assert router.worker_calls == []
    assert len(delivery.calls) == 1
    assert 'claim_verification_receipt' not in outcome.rendered.metadata
    assert outcome.rendered.metadata['prepared_public_output'].sealed_hash
    assert outcome.rendered.metadata['prepared_public_output'].display_text == 'Chào cậu.'
    assert host.sent == [] and host.chat_history == []


def test_empty_manifest_does_not_turn_unverified_action_into_phatic():
    outcome, router, delivery, _ = _run({
        'text':'Tớ đã gửi tài liệu cho cậu rồi.',
        'speech_text':'ファイルを送ったよ。', 'claim_manifest': [],
    }, verdict={'_runtime_verifier_error':'VERIFIER_PROVIDER_ERROR'})
    assert outcome.status == 'writer_failed' and len(delivery.calls)==0
    assert router.worker_calls == []


def test_history_claim_with_missing_manifest_is_never_recovered():
    outcome, router, delivery, host = _run({
        'text':'Chúng ta đã gặp nhau ở Tokyo.',
        'speech_text':'前に会ったよ。',
    })
    assert outcome.status == 'delivering' and len(delivery.calls)==1
    assert router.worker_calls == []
    assert host.chat_history == []


def test_false_claim_is_not_safe_by_declaring_empty_manifest():
    _, artifact = make_prepared(
        text='Chào cậu. Tớ đã xóa ký ức cũ rồi.',
        speech='昨日の記録を削除した。',
    )
    manifest = inspect_claim_manifest({'claim_manifest': []})
    assert certify_phatic(prepared=artifact, original_manifest=manifest) is None
    assert 'UNDECLARED_SELF_ACTION' in undeclared_high_risk_claims(
        prepared=artifact, manifest=manifest,
    )


def test_false_claim_only_in_japanese_speech_is_detected():
    _, artifact = make_prepared('Chào cậu.', '前に会ったよ。')
    manifest = inspect_claim_manifest({'claim_manifest': []})
    assert certify_phatic(prepared=artifact, original_manifest=manifest) is None
    assert 'UNDECLARED_SHARED_HISTORY' in undeclared_high_risk_claims(
        prepared=artifact, manifest=manifest,
    )


def test_safe_display_does_not_clear_memory_with_additional_claim():
    _, artifact = make_prepared()
    fake_memory = 'Chào cậu. Cậu từng nói điều đó.'
    bad = replace(artifact, memory_text=fake_memory,
                  stage_frame=replace(artifact.stage_frame, memory_text=fake_memory))
    # Mutation sau Stage phải bị ràng buộc hash bắt lại trước khi xét claim.
    with pytest.raises(RuntimeError, match='public_artifact_seal_mismatch'):
        certify_phatic(prepared=bad,
                       original_manifest=inspect_claim_manifest({'claim_manifest': []}))


def test_non_phatic_statement_always_calls_semantic_verifier():
    outcome, router, delivery, _ = _run({
        'text':'Tớ vẫn chưa hiểu chuyện đó.',
        'speech_text':'それはまだわからない。',
        'claim_manifest':[],
    }, verdict={'_runtime_verifier_error':'VERIFIER_TIMEOUT'})
    assert outcome.status=='delivering' and len(delivery.calls)==1
    assert router.worker_calls == []


def test_non_empty_manifest_disables_phatic_fast_path():
    outcome, router, delivery, _ = _run({
        'text':'Chào cậu.', 'speech_text':'こんにちは。',
        'claim_manifest':[{'domain':'opinion','claim':'Xin chào','evidence_refs':[]}],
    }, verdict={'_runtime_verifier_error':'VERIFIER_PROVIDER_ERROR'})
    assert outcome.status=='delivering' and len(delivery.calls)==1
    assert router.worker_calls == []


@pytest.mark.parametrize('malformed', [
    [{'domain':'shared_episode','claim':'từng gặp','evidence_refs':[]}],
    '{"domain":"shared_episode"}',
    [{'domain':'self_capability'}],
])
def test_malformed_manifest_with_claim_is_never_reinterpreted_as_empty(malformed):
    manifest = inspect_claim_manifest({'claim_manifest':malformed})
    assert not can_normalize_empty_manifest(manifest)


def test_receipt_cannot_claim_deterministic_proof_for_history():
    _, artifact = make_prepared('Tớ đã gửi tài liệu.', '送った。')
    ev = ConversationEvent(event_id=artifact.event_id, session_id=artifact.session_id,
                           kind='user_turn', text='hi')
    registry = build_evidence_registry(event=ev, state=None, affect=None,
                                       report=None, ledger_turns=(),
                                       memory_records=(), sampled_at=1.0)
    artifact = replace(artifact, source_fingerprint=registry.fingerprint)
    from runtime.conversation.public_output_artifact import _artifact_hash
    artifact = replace(artifact, sealed_hash=_artifact_hash(
        session_id=artifact.session_id,generation=artifact.generation,run_id=artifact.run_id,
        move_id=artifact.move_id,delivery_id=artifact.delivery_id,channel=artifact.channel,
        display_text=artifact.display_text,memory_text=artifact.memory_text,
        speech_text=artifact.speech_text,source_fingerprint=artifact.source_fingerprint,
        segment_hashes=artifact.segment_hashes))
    fake = ClaimVerificationResult('pass', (), True, True, (), 'deterministic_phatic')
    with pytest.raises(RuntimeError, match='phatic_receipt_requires_proof'):
        build_verification_receipt(prepared=artifact, result=fake,
                                   registry=registry, portrait=None)


def test_only_complete_json_envelopes_are_recovered():
    candidate = {'status':'pass', 'findings':[],
                 'surface_equivalence':True,'memory_equivalence':True}
    import json
    expected = candidate
    for raw in (json.dumps(candidate), '```json\n'+json.dumps(candidate)+'\n```',
                {'result':candidate}):
        assert normalize_verifier_wire_format(raw)==expected
    for raw in ('reasoning first: '+json.dumps(candidate),
                '{"status":"pass"} extra', 'garbage'):
        assert isinstance(normalize_verifier_wire_format(raw), str)
    assert normalize_verifier_wire_format({'result':candidate,'reason':'text'}) != candidate
    _, artifact = make_prepared()
    checked = validate_verifier_result('garbage', prepared=artifact, registry=SimpleNamespace(entries=[]))
    assert checked.reason_codes == ('VERIFIER_SCHEMA_INVALID',)


def test_phatic_text_cannot_carry_a_hidden_action():
    _, artifact = make_prepared()
    from dataclasses import replace as dc_replace
    # Decision.action là side channel, không được 'chứng minh' bằng việc lời
    # nói là lời chào. Stage/transport không được tự lén thêm tool action.
    artifact.decision.system_action = 'delete_file'
    assert certify_phatic(prepared=artifact,
                          original_manifest=inspect_claim_manifest({'claim_manifest': []})) is None
    artifact.decision.system_action = ''
    artifact.payload['system_action'] = 'delete_file'
    assert certify_phatic(prepared=artifact,
                          original_manifest=inspect_claim_manifest({'claim_manifest': []})) is None
