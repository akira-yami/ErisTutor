from types import SimpleNamespace

from tools.benchmark_claim_verifier import audit_findings, content_only, contract_system, CaptureClient, benchmark_cases


def test_capture_excludes_provider_reasoning_and_credentials():
    response = {'choices': [{'message': {'content': '{"status":"pass"}',
                                          'reasoning_content': 'private'}}],
                'api_key': 'secret'}
    assert content_only(response) == '{"status":"pass"}'


def test_audit_checks_exact_surface_domain_and_registry_ref():
    prepared = SimpleNamespace(display_text='Cậu nói thích trà.', memory_text='Cậu nói thích trà.',
                               segments=(SimpleNamespace(tts_text='お茶が好きだと言ったね。'),))
    registry = SimpleNamespace(entries=(SimpleNamespace(ref='event:1', status='supported'),))
    raw = {'findings': [{'surface': 'display', 'segment_id': -1, 'span': 'thích trà',
                        'domain': 'user_statement', 'support_refs': ['event:1']},
                       {'surface': 'speech', 'segment_id': 0, 'span': 'không tồn tại',
                        'domain': 'invented', 'support_refs': ['forged']}]}
    rows = audit_findings(raw, prepared, registry)
    assert rows[0]['span_matches'] and rows[0]['domain_known'] and rows[0]['refs_resolve']
    assert not rows[1]['span_matches'] and not rows[1]['domain_known'] and not rows[1]['refs_resolve']


def test_probe_prompt_keeps_production_instruction_and_supplies_contract():
    from runtime.conversation.claim_verifier import SYSTEM
    prompt = contract_system()
    assert prompt.startswith(SYSTEM)
    assert 'surface_equivalence' in prompt and 'support_refs' in prompt
    assert 'user_statement' in prompt and 'self_capability' in prompt


def test_capture_keeps_wire_before_configured_worker_normalizes_json():
    from llm.deepseek_workers import JsonlWorkerClient
    wire = '```json\n{"status": "pass"}\n```'
    target = SimpleNamespace(create_chat_completion=lambda **kwargs: {
        'choices': [{'message': {'content': wire, 'reasoning_content': 'not captured'}}]})
    capture = CaptureClient(target)
    worker = JsonlWorkerClient(capture, role='observer', jsonl_enabled=True, repair_attempts=0)
    response = worker.create_chat_completion(messages=[], response_format={
        'type': 'json_schema', 'json_schema': {'schema': {
            'type': 'object', 'properties': {'status': {'type': 'string'}}, 'required': ['status']}}})
    assert capture.calls[0]['content'] == wire
    assert content_only(response) == '{"status":"pass"}'


def test_raw_print_preserves_model_content_and_omits_reasoning(capsys):
    wire = '{\n  "status": "unknown"\n}'
    target = SimpleNamespace(create_chat_completion=lambda **kwargs: {
        'choices': [{'message': {'content': wire, 'reasoning_content': 'PRIVATE_REASONING'}}]})
    capture = CaptureClient(target, print_raw=True, role='verifier')
    capture.create_chat_completion()
    printed = capsys.readouterr().out
    assert wire in printed and 'PRIVATE_REASONING' not in printed


def test_expanded_cases_include_absent_evidence_negation_and_injection():
    cases = benchmark_cases()
    assert len(cases) == 12 and len({case[0] for case in cases}) == 12
    assert {'screen_unknown', 'missing_memory', 'negative_statement', 'quoted_injection'} <= {c[0] for c in cases}
