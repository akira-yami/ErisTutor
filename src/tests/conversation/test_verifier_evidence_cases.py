import json


def test_typed_memory_domain_is_optional_real_commit_ablation(tmp_path):
    from tools.verifier_evidence_cases import build_cases

    default = build_cases(tmp_path / 'default')
    explicit_default = build_cases(tmp_path / 'explicit-default', typed_memory_domain=False)
    assert default == explicit_default
    typed = build_cases(tmp_path / 'typed', typed_memory_domain=True)
    for original, changed in zip(default, typed):
        assert original['payload']['candidate'] == changed['payload']['candidate']
        for entry in changed['payload']['evidence']:
            if entry['kind'] != 'memory':
                continue
            expected_domain = 'self' if entry['subject'] == 'eris' else 'user'
            assert json.loads(entry['content'])['text'].startswith(expected_domain + ':')
            artifact = next(a for a in changed['source_artifacts'] if a['kind'] == 'canonical_semantic_store')
            persisted = json.loads((tmp_path / 'typed' / artifact['path']).read_text(encoding='utf-8'))['items'][0]
            original_persisted = json.loads((tmp_path / 'default' / artifact['path']).read_text(encoding='utf-8'))['items'][0]
            assert persisted['domain'] == expected_domain
            assert persisted['metadata']['claim_provenance'] == original_persisted['metadata']['claim_provenance']


def test_frozen_pairs_use_isolated_canonical_sources(tmp_path, monkeypatch):
    from tools.verifier_evidence_cases import build_cases
    from memory.semantic_memory import SemanticMemoryStore
    from pathlib import Path

    loads = []
    real_load = SemanticMemoryStore.load

    def tracked_load(self):
        assert Path(self.path).resolve().is_relative_to(tmp_path.resolve())
        result = real_load(self)
        loads.append((self.path, tuple(item.id for item in result)))
        return result

    monkeypatch.setattr(SemanticMemoryStore, 'load', tracked_load)

    cases = build_cases(tmp_path)
    assert len(cases) == 14
    assert cases == build_cases(tmp_path)
    json.dumps(cases, ensure_ascii=False)
    assert len({case['pair_id'] for case in cases}) == 7
    assert [case['pair_id'] for case in cases[::2]] == [
        'canonical_fact', 'completed_action', 'screen_availability', 'memory_freshness',
        'reference_identity', 'memory_subject', 'historical_self_action',
    ]
    for positive, negative in zip(cases[::2], cases[1::2]):
        assert positive['payload']['candidate'] == negative['payload']['candidate']
        assert positive['gold']['status'] == 'pass'
        assert negative['gold']['status'] == 'fail'
        assert positive['candidate_origin'] == 'controlled_gold_text'
        manifest = positive['payload']['manifest'][0]
        refs = {entry['ref']: entry for entry in positive['payload']['evidence']}
        assert all(refs[ref]['status'] == 'supported' for ref in manifest['evidence_refs'])
        assert {claim['surface'] for claim in positive['gold']['claims']} == {'display', 'speech', 'memory'}
        assert all(claim['span'] for claim in positive['gold']['claims'])
        for artifact in positive['source_artifacts'] + negative['source_artifacts']:
            assert (tmp_path / artifact['path']).resolve().is_relative_to(tmp_path.resolve())
    memories = list(tmp_path.glob('*.memory.json'))
    assert memories
    assert all(any(path == str(memory) and ids for path, ids in loads) for memory in memories)
    for path in memories:
        payload = json.loads(path.read_text(encoding='utf-8'))
        assert payload['items'][0]['metadata']['claim_provenance']['root_event_ids']
    receipts = cases[2:4]
    entries = [next(e for e in case['payload']['evidence'] if e['kind'] == 'capability_result') for case in receipts]
    assert [e['status'] for e in entries] == ['supported', 'unknown']
    assert [json.loads(e['content'])['status'] for e in entries] == ['completed', 'failed']
    receipt_result = json.loads(entries[0]['content'])['result']
    written = tmp_path / 'completed_action-positive' / receipt_result['created_file']
    assert written.name == 'receipt.txt'
    assert written.read_bytes() == b'fixture\n'
    assert len(written.read_bytes()) == receipt_result['bytes_written']
    assert not (tmp_path / 'completed_action-negative' / 'receipt.txt').exists()
    assert cases[1]['gold']['claims'][0]['expected_support'] == 'contradicted'
    assert cases[-2]['payload']['candidate']['display'] == 'Tôi đã giúp bạn sửa lỗi.'
    for case in cases:
        assert case['payload']['candidate']['speech_segments'][0]['segment_id'] == 0
        assert next(c for c in case['gold']['claims'] if c['surface'] == 'speech')['segment_id'] == 0
