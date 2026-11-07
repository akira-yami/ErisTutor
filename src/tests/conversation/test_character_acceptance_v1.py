# -*- coding: utf-8 -*-
"""Task 9: thử NGHIỆM THU, không giả lập chất lượng thực của model.

Các bản mock chỉ kiểm protocol: ghép cùng lượt, blind chấm, điều kiện pass.
Chúng không được xuất thành một benchmark Eris có cá tính tốt.
"""
import json
from pathlib import Path

import pytest

from tools.score_eris_character_eval import (
    AXES, prepare_blind_pairs, score_blind_pairs,
)
from tools.run_eris_character_eval import _telemetry, run_scenarios, _stub_host_factory


@pytest.fixture
def configured_pair(tmp_path):
    """Đầu vào GIẢ cho scorer, tuyệt đối không dùng làm báo cáo chất lượng."""
    baseline, candidate = tmp_path / 'baseline', tmp_path / 'candidate'
    for folder, label in ((baseline, 'old'), (candidate, 'new')):
        folder.mkdir()
        (folder / 'summary.json').write_text(json.dumps({
            'mode': 'configured', 'runs': 3, 'case_count': 10,
            'scenario_revision': 'fixture-acceptance', 'failed_rows': 0,
            'configured_model_id': 'same-test-provider-settings',
            'configured_model_id_consistent': True,
        }))
        with (folder / 'turns.jsonl').open('w', encoding='utf-8') as handle:
            for s in range(1, 11):
                for variant in ('candidate', 'counterpart'):
                    for run in range(3):
                        for turn in range(1, 5):
                            row = dict(scenario=f'S{s:02d}', variant=variant,
                                       run_index=run, turn_index=turn,
                                       status='delivering', event_text=f'User {turn}',
                                       display_text=f'{label} {turn}',
                                       speech_text=f'{label} speech', latency_ms=20,
                                       verification={'status':'pass',
                                                     'delivery_id': 'delivery',
                                                     'segment_count': 1},
                                       model_calls_delta=2,
                                       delivery_id='delivery', delivery_accepted=True,
                                       manifest_status='valid',
                                       evidence_fingerprint='registry',
                                       character_snapshot_id='state',
                                       prepared_segment_count=1)
                            handle.write(json.dumps(row) + '\n')
    return baseline, candidate


def _ratings(blind, key, *, candidate_good=True):
    mapping = {row['pair_id']: row['candidate_slot'] for row in key['pairs']}
    rows = []
    for p in blind:
        good = mapping[p['pair_id']]
        scores = {'character': 2, 'state_fidelity': 2, 'naturalness': 2, 'relevance': 2}
        low = {'character': 1, 'state_fidelity': 1, 'naturalness': 1, 'relevance': 1}
        rows.append({'pair_id': p['pair_id'], 'A': scores if good == 'A' else low,
                     'B': scores if good == 'B' else low,
                     'severe_factual_scope_failure': False, 'technical_regression': False,
                     'provider_refusal': False, 'gate_false_positive': 0,
                     'gate_false_negative': 0, 'note': ''})
    return rows


def test_full_pair_is_blinded_and_has_separate_key(configured_pair, tmp_path):
    base, can = configured_pair
    out = tmp_path / 'blind'
    assert prepare_blind_pairs(base, can, out, seed='fixed')['pairs'] == 60
    pairs = [json.loads(x) for x in (out / 'blind_pairs.jsonl').read_text().splitlines()]
    key = json.loads((out / 'blind_key.json').read_text())
    assert len(key['pairs']) == len(pairs) == 60
    assert {x['scenario'] for x in pairs} == {f'S{i:02d}' for i in range(1,11)}
    assert all(len(x['A']) == len(x['B']) == 4 for x in pairs)
    assert all('candidate_slot' not in x for x in pairs)
    slots = {x['candidate_slot'] for x in key['pairs']}
    assert slots == {'A', 'B'}
    assert all(set(x['A'][0]) == {'turn', 'user', 'eris', 'speech', 'status'} for x in pairs)
    assert 'model_quality_score' not in (out / 'blind_pairs.jsonl').read_text()


def test_stub_or_model_settings_mismatch_must_not_be_rated(configured_pair, tmp_path):
    base, can = configured_pair
    summary_path = can / 'summary.json'
    summary = json.loads(summary_path.read_text())
    summary['mode'] = 'stub'
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='configured'):
        prepare_blind_pairs(base, can, tmp_path / 'blind')
    summary['mode'] = 'configured'
    summary['configured_model_id'] = 'different-provider'
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='model_settings'):
        prepare_blind_pairs(base, can, tmp_path / 'blind')


def test_missing_turn_fails_instead_of_fabricating_reply(configured_pair, tmp_path):
    base, can = configured_pair
    p = can / 'turns.jsonl'
    p.write_text('\n'.join(p.read_text().splitlines()[:-1]) + '\n')
    with pytest.raises(ValueError, match='missing_or_unexpected'):
        prepare_blind_pairs(base, can, tmp_path / 'blind')


def test_human_acceptance_requires_all_conditions(configured_pair, tmp_path):
    base, can = configured_pair
    output = tmp_path / 'blind'
    prepare_blind_pairs(base, can, output)
    blind = [json.loads(x) for x in (output / 'blind_pairs.jsonl').read_text().splitlines()]
    key = json.loads((output / 'blind_key.json').read_text())
    ratings = _ratings(blind, key)
    file = output / 'ratings.jsonl'
    file.write_text(''.join(json.dumps(x) + '\n' for x in ratings))
    report = score_blind_pairs(output/'blind_key.json', file, base, can,
                               output/'report.json')
    assert report['result'] == 'pass'
    assert report['passed_scenarios'] == report['improved_scenarios'] == 10
    assert report['candidate_metrics']['latency_ms_p95'] == 20
    assert report['candidate_metrics']['verification_observed'] == 240
    # Chỉ một lỗi lịch sử/scope nghiêm trọng vẫn là không đạt.
    ratings[0]['severe_factual_scope_failure'] = True
    file.write_text(''.join(json.dumps(x) + '\n' for x in ratings))
    assert score_blind_pairs(output/'blind_key.json', file, base, can,
                             output/'report.json')['result'] == 'fail'
    ratings[0]['severe_factual_scope_failure'] = False
    ratings[0]['technical_regression'] = True
    file.write_text(''.join(json.dumps(x) + '\n' for x in ratings))
    assert score_blind_pairs(output/'blind_key.json', file, base, can,
                             output/'report.json')['result'] == 'fail'


def test_good_dialogue_cannot_bypass_verification_receipt(configured_pair, tmp_path):
    base, can = configured_pair
    output = tmp_path/'blind'
    prepare_blind_pairs(base, can, output)
    blind = [json.loads(x) for x in (output/'blind_pairs.jsonl').read_text().splitlines()]
    key = json.loads((output/'blind_key.json').read_text())
    ratings = _ratings(blind, key)
    file = output/'ratings.jsonl'
    file.write_text(''.join(json.dumps(x) + '\n' for x in ratings))
    rows_path = can/'turns.jsonl'
    rows = [json.loads(x) for x in rows_path.read_text().splitlines()]
    rows[0]['verification'] = None
    rows_path.write_text(''.join(json.dumps(x) + '\n' for x in rows))
    report = score_blind_pairs(output/'blind_key.json', file, base, can, output/'report.json')
    assert report['result'] == 'fail'
    assert report['candidate_machine_invariants']['violations']['verification_missing_or_not_pass'] == 1


def test_missing_or_faked_ratings_are_rejected(configured_pair, tmp_path):
    base, can = configured_pair
    output = tmp_path/'blind'
    prepare_blind_pairs(base, can, output)
    blind = [json.loads(x) for x in (output/'blind_pairs.jsonl').read_text().splitlines()]
    key = json.loads((output/'blind_key.json').read_text())
    ratings = _ratings(blind, key)
    file = output/'ratings.jsonl'
    file.write_text(''.join(json.dumps(x) + '\n' for x in ratings[:-1]))
    with pytest.raises(ValueError, match='incomplete'):
        score_blind_pairs(output/'blind_key.json', file, base, can, output/'report.json')
    ratings[0]['A']['character'] = None
    file.write_text(''.join(json.dumps(x) + '\n' for x in ratings))
    with pytest.raises(ValueError, match='rating_scale'):
        score_blind_pairs(output/'blind_key.json', file, base, can, output/'report.json')


def test_harness_reports_real_receipt_metadata_without_secret_content():
    from types import SimpleNamespace
    result = SimpleNamespace(
        status='delivering',
        event=SimpleNamespace(event_id='event',session_id='session',generation=5),
        turn=SimpleNamespace(run_id='run'), delivery_id='delivery',
        decision=None,
        rendered=SimpleNamespace(
            surface=SimpleNamespace(display_text='Hi',speech_text='はい'),
            metadata={
                'prepared_public_output': SimpleNamespace(segments=[1, 2]),
                'claim_verification_receipt': SimpleNamespace(
                    status='pass',delivery_id='delivery',snapshot_id='state-5',
                    segment_hashes=('1', '2'),display_hash='d',speech_hash='s',memory_hash='m'),
                'claim_manifest_envelope': SimpleNamespace(structural_status='valid'),
                'character_portrait': SimpleNamespace(snapshot_id='state-5', facets=[1]),
                'evidence_registry': SimpleNamespace(fingerprint='fingerprint', entries=[]),
            }))
    exported = _telemetry(SimpleNamespace(model_info={}), result)
    assert exported['verification']['status'] == 'pass'
    assert exported['verification']['segment_count'] == 2
    assert exported['manifest_status'] == 'valid'
    assert exported['character_snapshot_id'] == 'state-5'
    assert exported['provenance_present'] is False
    assert 'private_memory_text' not in exported


def test_stub_does_not_claim_model_benchmark(tmp_path):
    scenarios = json.loads((Path(__file__).parents[1] / 'fixtures/eris_character_v1/scenarios.json')
                           .read_text(encoding='utf-8'))['scenarios']
    # Kiểm một run: 80 lượt qua Kernel thật nhưng không gọi provider.
    summary = run_scenarios(_stub_host_factory, scenarios, runs=1,
                            label='mechanical', output_dir=tmp_path)
    assert summary['row_count'] == 80 and summary['failed_rows'] == 0
    assert summary['mode'] == 'stub'
    assert summary['baseline_model'] == 'pending'
    assert summary['configured_model_id'] is None
    assert summary['verification_statuses'] == {'unavailable': 80}


def test_configured_rejects_stub_host_without_fixture_application(tmp_path):
    scenarios = json.loads((Path(__file__).parents[1] / 'fixtures/eris_character_v1/scenarios.json')
                           .read_text(encoding='utf-8'))['scenarios']
    summary = run_scenarios(_stub_host_factory, scenarios, runs=1,
                            label='no-real-model', output_dir=tmp_path,
                            mode='configured')
    assert summary['failed_rows'] == 20
    assert summary['configured_model_id_consistent'] is False
    assert summary['row_count'] == 20  # Không sinh transcript giả để tăng số lượt.
    assert all(json.loads(row)['status'] == 'host_failed'
               for row in (tmp_path/'turns.jsonl').read_text().splitlines())
