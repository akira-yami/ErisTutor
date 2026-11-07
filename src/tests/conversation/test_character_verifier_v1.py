# -*- coding: utf-8 -*-
"""Task 5: parser runtime không tin status=pass do checker tự khai."""
from dataclasses import replace
from types import SimpleNamespace
from runtime.claim_domain_contract import inspect_claim_manifest
from runtime.conversation.claim_evidence import build_evidence_registry
from runtime.conversation.claim_verifier import (
    CharacterClaimVerifier, validate_verifier_result, build_verification_receipt,
    validate_receipt,
)
from test_character_output_artifact_v1 import prepared, turn, event


def registry():
    return build_evidence_registry(event=event(),state=SimpleNamespace(version=3,last_applied_turn_seq=1),
        affect=None,report=None,ledger_turns=(),memory_records=(),sampled_at=1.)


def result(status='pass',findings=None, surface=True, memory=True):
    return {'status':status,'surface_equivalence':surface,'memory_equivalence':memory,
            'findings':[] if findings is None else findings}


def test_checker_status_pass_needs_equivalence_and_real_span():
    _,p=prepared()
    r=registry()
    assert validate_verifier_result(result(),prepared=p,registry=r).status=='pass'
    assert validate_verifier_result(result(surface=False),prepared=p,registry=r).status=='fail'
    assert validate_verifier_result(result(memory=False),prepared=p,registry=r).status=='fail'
    span={'surface':'speech','segment_id':0,'span':'hư cấu không ở đây',
          'domain':'conversation_history','support_refs':[], 'verdict':'supported','reason_code':''}
    assert validate_verifier_result(result(findings=[span]),prepared=p,registry=r).status=='unknown'


def test_checker_claim_in_speech_with_unknown_ref_does_not_pass():
    _,p=prepared()
    r=registry()
    claim={'surface':'speech','segment_id':0,'span':'こんにちは',
           'domain':'shared_episode','support_refs':['forged:ref'],
           'verdict':'supported','reason_code':''}
    assert validate_verifier_result(result(findings=[claim]),prepared=p,registry=r).status=='fail'
    claim['support_refs']=[]
    claim['verdict']='unsupported';claim['reason_code']='UNDECLARED_CLAIM'
    assert validate_verifier_result(result(findings=[claim]),prepared=p,registry=r).status=='fail'


def test_invalid_schema_is_unknown_not_pass():
    _,p=prepared();r=registry()
    assert validate_verifier_result({},prepared=p,registry=r).status=='unknown'
    assert validate_verifier_result({'status':'pass','findings':[]},prepared=p,registry=r).status=='unknown'


def test_receipt_binds_all_surfaces_and_rejects_tampering():
    _,p=prepared();r=registry()
    # Registry của fixture build event có snapshot revision khác, nhưng cùng event ID.
    from runtime.conversation.public_output_artifact import _artifact_hash
    p=replace(p,source_fingerprint=r.fingerprint)
    p=replace(p,sealed_hash=_artifact_hash(
        session_id=p.session_id,generation=p.generation,run_id=p.run_id,
        move_id=p.move_id,delivery_id=p.delivery_id,channel=p.channel,
        display_text=p.display_text,memory_text=p.memory_text,
        speech_text=p.speech_text,source_fingerprint=p.source_fingerprint,
        segment_hashes=p.segment_hashes))
    verdict=validate_verifier_result(result(),prepared=p,registry=r)
    receipt=build_verification_receipt(prepared=p,result=verdict,registry=r,portrait=None)
    validate_receipt(prepared=p,receipt=receipt,turn=turn())
    from dataclasses import replace as rep
    for corrupt in (rep(p,display_text='Sai'),rep(p,memory_text='Sai'),rep(p,source_fingerprint='other'),
                    rep(p,delivery_id='delivery:wrong')):
        try:
            validate_receipt(prepared=corrupt,receipt=receipt,turn=turn())
        except RuntimeError:
            pass
        else:
            raise AssertionError('receipt accepted a changed artifact')


def test_verifier_unavailable_is_unknown_and_does_not_create_receipt():
    _,p=prepared();r=registry()
    class Router:
        observer_client=None
        surface_client=None
    verifier=CharacterClaimVerifier(Router())
    out=verifier.verify(prepared=p,manifest=inspect_claim_manifest({'claim_manifest':[]}),
                        move=turn().move,registry=r,portrait=None)
    assert out.status=='unknown'


def test_verifier_pass_cannot_self_certify_evidence_required_claim_without_ref():
    _,p=prepared();r=registry()
    finding={'surface':'display','segment_id':-1,'span':'Chào cậu.',
             'domain':'shared_episode','support_refs':[],
             'verdict':'supported','reason_code':''}
    checked=validate_verifier_result(result(findings=[finding]),prepared=p,registry=r)
    assert checked.status == 'fail' and 'UNSUPPORTED_CLAIM' in checked.reason_codes
    finding['domain']='made_up_scope'
    assert validate_verifier_result(result(findings=[finding]),prepared=p,registry=r).status=='unknown'
