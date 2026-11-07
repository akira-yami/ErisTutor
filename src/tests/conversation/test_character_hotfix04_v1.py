# -*- coding: utf-8 -*-
"""HOTFIX 04: ranh giới DS2API JSON schema và chẩn đoán lỗi, không bypass fact gate."""
from types import SimpleNamespace

from runtime.claim_domain_contract import inspect_claim_manifest
from runtime.conversation.claim_verifier import (
    CharacterClaimVerifier, validate_verifier_result,
)
from runtime.conversation.claim_evidence import build_evidence_registry
from test_character_output_artifact_v1 import prepared, event, turn


def _registry():
    return build_evidence_registry(event=event(), state=None, affect=None,
        report=None, ledger_turns=(), memory_records=(), sampled_at=1.0)


def _pass(**extra):
    return dict(status='pass', surface_equivalence=True,
                memory_equivalence=True, findings=[], **extra)


def test_extra_top_level_metadata_does_not_invalidate_verdict():
    _, output = prepared()
    checked = validate_verifier_result(_pass(explanation='thừa nhưng không chứng minh fact'),
                                       prepared=output, registry=_registry())
    assert checked.status == 'pass'


def test_extra_finding_metadata_does_not_invalidate_verdict():
    _, output = prepared()
    candidate = _pass()
    candidate['findings'] = [{
        'surface':'display', 'segment_id':-1, 'span':'Chào cậu.',
        'domain':'opinion', 'support_refs':[], 'verdict':'supported',
        'reason_code':'', 'note':'không dùng cho quyền PASS',
    }]
    assert validate_verifier_result(candidate, prepared=output,
                                    registry=_registry()).status == 'pass'


def test_missing_required_field_and_bad_type_still_unknown():
    _, output = prepared()
    for response in (
        {'status':'pass', 'surface_equivalence':True, 'findings':[]},
        {'status':'pass','surface_equivalence':'true','memory_equivalence':True,'findings':[]},
        {'status':'pass','surface_equivalence':True,'memory_equivalence':True,'findings':{}},
    ):
        checked=validate_verifier_result(response, prepared=output, registry=_registry())
        assert checked.status == 'unknown'
        assert 'VERIFIER_SCHEMA_INVALID' in checked.reason_codes


def test_extra_field_never_grants_fake_ref_or_wrong_scope():
    _, output = prepared()
    payload = _pass(extra='ignored')
    payload['findings'] = [{
        'surface':'display','segment_id':-1,'span':'Chào cậu.',
        'domain':'shared_episode', 'support_refs':['fake:source'],
        'verdict':'supported','reason_code':'', 'metadata':'ignore this',
    }]
    result=validate_verifier_result(payload,prepared=output,registry=_registry())
    assert result.status == 'fail'
    assert 'UNKNOWN_REF' in result.reason_codes


def test_extra_field_does_not_override_false_equivalence():
    _, output = prepared()
    candidate=_pass(explanation='all fine')
    candidate['surface_equivalence']=False
    assert validate_verifier_result(candidate,prepared=output,
                                    registry=_registry()).status == 'fail'


def test_real_verifier_router_passes_ds2api_extra_fields():
    _, output=prepared()
    class FakeRouter:
        observer_client=object()
        surface_client=None
        logger=None
        def invoke_structured_worker(self, client, **kwargs):
            assert kwargs['cognitive_lane']=='public_claim_verifier'
            return _pass(extra_model_explanation='informational')
    result=CharacterClaimVerifier(FakeRouter()).verify(
        prepared=output,manifest=inspect_claim_manifest({'claim_manifest':[]}),
        move=turn().move,registry=_registry(),portrait=None)
    assert result.status == 'pass'


def test_verifier_shape_error_logs_only_category_not_response_text():
    _, output=prepared()
    calls=[]
    class FakeRouter:
        observer_client=object()
        surface_client=None
        logger=SimpleNamespace(warning=lambda fmt,*args: calls.append(fmt % args))
        def invoke_structured_worker(self,client,**kwargs):
            return {'status':'pass', 'findings':[],
                    'surface_equivalence':True, 'secret':'MY_PRIVATE_CONTENT'}
    checked=CharacterClaimVerifier(FakeRouter()).verify(
        prepared=output,manifest=inspect_claim_manifest({'claim_manifest':[]}),
        move=turn().move,registry=_registry(),portrait=None)
    assert checked.status=='unknown'
    assert any('shape=missing_top_level' in line for line in calls)
    assert all('MY_PRIVATE_CONTENT' not in line for line in calls)
