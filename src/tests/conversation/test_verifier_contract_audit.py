import copy

from tools.verifier_contract_audit import audit_capture, migrate_domains, v2_schema


def sample():
    return {'final_surfaces': {'display': 'Đã lưu file.', 'speech': ['保存した。'], 'memory': 'Đã lưu file.'},
            'evidence_registry': [{'ref': 'receipt:ok', 'kind': 'capability_result',
                                   'scope': 'capability_execution', 'status': 'supported'}],
            'verifier_json': {'status': 'pass', 'surface_equivalence': True, 'memory_equivalence': True,
                             'findings': []}}


def test_audit_collects_domain_span_ref_and_equivalence_violations_together():
    s = sample()
    s['verifier_json'].update(surface_equivalence=False, memory_equivalence=False, findings=[{
        'surface': 'display', 'segment_id': 5, 'span': 'Không có ở surface', 'domain': 'made_up',
        'support_refs': ['forged'], 'verdict': 'unknown', 'reason_code': 'x'}])
    codes = {v['code'] for v in audit_capture(s)['violations']}
    assert {'domain_unknown', 'segment_invalid', 'span_mismatch', 'ref_missing',
            'pass_with_non_supported', 'pass_surface_mismatch', 'pass_memory_mismatch'} <= codes


def test_v2_never_changes_claim_verdict_refs_span_or_equivalence():
    raw = sample()['verifier_json']
    raw['status'] = 'fail'
    raw['findings'] = [{'surface': 'display', 'segment_id': 0, 'span': 'Đã lưu file.',
                        'domain': 'self_action', 'support_refs': [], 'verdict': 'unsupported', 'reason_code': 'no_receipt'}]
    before = copy.deepcopy(raw)
    migrated = migrate_domains(raw)
    assert raw == before
    assert migrated['findings'][0]['domain'] == 'self_capability'
    assert migrated['findings'][0]['subtype'] == 'self_action'
    assert {k: migrated[k] for k in ('status', 'surface_equivalence', 'memory_equivalence')} == {
        k: raw[k] for k in ('status', 'surface_equivalence', 'memory_equivalence')}
    for k in ('span', 'verdict', 'reason_code', 'support_refs'):
        assert migrated['findings'][0][k] == raw['findings'][0][k]


def test_supported_unrecognized_domain_cannot_be_laundered_into_pass():
    s = sample()
    s['verifier_json']['findings'] = [{'surface': 'display', 'segment_id': 0,
        'span': 'Đã lưu file.', 'domain': 'self_action', 'support_refs': [],
        'verdict': 'supported', 'reason_code': 'claimed'}]
    audit = audit_capture(s, v2=True)
    assert audit['effective_status'] != 'pass'
    assert any(v['code'] == 'supported_unclassified' for v in audit['violations'])


def test_v2_domain_enum_is_fixed_but_subtype_is_open():
    schema = v2_schema()['properties']['findings']['items']['properties']
    assert 'enum' in schema['domain']
    assert schema['subtype'] == {'type': 'string'}

def test_transport_failure_is_not_invented_json_contract_violations():
    s=sample();s['verifier_json']={'_runtime_verifier_error':'VERIFIER_TIMEOUT'}
    result=audit_capture(s)
    assert [v['code'] for v in result['violations']]==['transport_failure']
    assert result['effective_status']=='unknown'
