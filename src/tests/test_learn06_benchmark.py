# -*- coding: utf-8 -*-
"""LEARN-06 — tests về benchmark, boundary, simulation (không gọi network)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools.learner_benchmark import (
    AuditRouter, dump_report, load_cases, observation_for, one_call, run, score, wire_validation,
)
from tools.learner_simulation import scenario
from runtime.tutor.model_reasoner import RuntimeTutorReasoner
from runtime.tutor.contracts import SemanticAssessment
from runtime.learning_core import LearningService, LearningStore, LearningPath, KnowledgePoint
from runtime.tutor.executive import TutorExecutive
from runtime.tutor.session_store import TutorSessionLedger


GOOD = {
    'kp_id':'fake.target', 'verdict':'fail', 'confidence':.9,
    'evidence_type':'feynman_fail', 'strength':.8, 'summary':'sai điều kiện dừng',
    'error_code':'off_by_one', 'misconception':'Tính cả bound'
}


class Router:
    def __init__(self, result=None, failure=None):
        self.result = result if result is not None else dict(GOOD)
        self.failure = failure
        self.calls = []

    def invoke_structured_worker(self, client, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise self.failure
        return self.result


def test_dataset_has_distinct_cases_and_balanced_important_labels():
    rows = load_cases()
    assert len(rows) >= 24
    assert len({r['id'] for r in rows}) == len(rows)
    labels = {r['gold'] for r in rows}
    assert labels == {'pass','fail','partial','uncertain'}
    for case in rows:
        obs, state, move = observation_for(case)
        assert obs.user_text == case['answer']
        assert state.last_delivery_text == case['question']
        assert move.kp_id == case['kp_id']


@pytest.mark.parametrize('field,value', [
    ('verdict','unknown'),('confidence',float('nan')),('confidence',1.1),
    ('strength',-0.1),('evidence_type',9),('kp_id',None),
    ('summary','a'*601),
])
def test_production_wire_gate_rejects_malformed_assessment(field,value):
    assert wire_validation(dict(GOOD))
    assert not wire_validation(dict(GOOD, **{field:value}))


def test_production_wire_gate_rejects_missing_extra_or_empty():
    assert not wire_validation({k:v for k,v in GOOD.items() if k != 'summary'})
    assert not wire_validation(dict(GOOD, unexpected='something'))
    assert not wire_validation({})


def test_model_kp_redirection_is_not_committed_to_wrong_knowledge_point():
    rows = load_cases()
    router = AuditRouter(Router(), object())
    reasoner = RuntimeTutorReasoner(router, learner_client=router.deep_client)
    record = one_call(rows[0], reasoner, router)
    assert record['schema_valid'] is True
    assert record['kp_bound'] is True
    assert record['verdict'] == 'fail'
    assert record['commit_allowed'] is True
    assert router.router.calls[0]['cognitive_lane'].startswith('tutor:')


def test_malformed_raw_response_cannot_become_valid_assessment():
    rows = load_cases()
    router = AuditRouter(Router(result={'verdict':'pass','confidence':.99}), object())
    reasoner = RuntimeTutorReasoner(router, learner_client=router.deep_client)
    record = one_call(rows[0], reasoner, router)
    assert record['verdict'] == 'no_result'
    assert record['schema_valid'] is False
    assert record['commit_allowed'] is False


def test_provider_errors_never_become_fake_passes_or_leak_secrets():
    rows = load_cases()
    router = AuditRouter(Router(failure=RuntimeError('TOP_SECRET_KEY-NEVER_LOG')), object())
    reasoner = RuntimeTutorReasoner(router, learner_client=router.deep_client)
    record = one_call(rows[0], reasoner, router)
    assert record['verdict'] == 'no_result'
    assert record['error'] == 'provider_RuntimeError'
    assert 'TOP_SECRET_KEY' not in json.dumps(record)


def test_uncertain_or_low_confidence_never_commits_learning_truth(tmp_path):
    learning = LearningService(LearningStore(tmp_path/'guard.db'))
    learning.register_path(LearningPath('p','P','P', (KnowledgePoint('kp','KP','concept'),)))
    assert learning.activate_path('p')
    executive = TutorExecutive(learning, session_ledger=TutorSessionLedger())
    for verdict, conf in [('uncertain',.99),('pass',.50)]:
        accepted = executive._apply_semantic_assessment(
            SemanticAssessment('kp',verdict,conf,'feynman_pass',.99),
            interaction_id='ix-'+verdict,turn_id='turn-'+verdict,now=1_800_000_000)
        assert accepted is False
    assert learning.get_mastery('kp').attempt_count == 0
    learning._store.close()


def test_model_confidence_is_not_reported_as_mastery_probability():
    rows = [
        {'case_id':'1','domain':'d','gold':'fail','verdict':'pass','confidence':.94,
         'commit_allowed':True,'schema_valid':True,'elapsed_ms':100,'error':''},
        {'case_id':'2','domain':'d','gold':'pass','verdict':'uncertain','confidence':.30,
         'commit_allowed':False,'schema_valid':True,'elapsed_ms':200,'error':''},
        {'case_id':'3','domain':'d','gold':'partial','verdict':'partial','confidence':.82,
         'commit_allowed':True,'schema_valid':True,'elapsed_ms':90,'error':''},
    ]
    metric = score(rows,mode='live')
    assert metric['unsafe_false_passes'] == 1
    assert metric['accuracy_including_abstention'] == pytest.approx(1/3)
    assert metric['confidence_correctness_brier_NOT_mastery'] is not None
    assert 'P(mastered)' in metric['note']


def test_dry_run_does_not_fake_model_metrics_or_call_network(tmp_path):
    output=tmp_path/'report'
    assert run(['--mode','dry-run','--out',str(output)]) == 0
    summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
    assert summary['settings']['calls']==0
    assert summary['metrics']['count']==0
    assert summary['metrics']['accuracy_including_abstention'] is None
    assert (output/'rows.csv').read_text(encoding='utf-8-sig').startswith('case_id,')


def test_real_calls_require_explicit_confirmation_and_do_not_start_implicitly():
    with pytest.raises(SystemExit):
        run(['--mode','live','--out','unused-path'])


def test_simulated_root_receipt_multi_session_and_restart(tmp_path):
    report=scenario(tmp_path/'isolated.db')
    assert report['status']=='pass'
    assert {r['step'] for r in report['trace']} >= {
        'failed_delivery','wrong_answer_1','independent_labeled_evidence_2',
        'post_error_strategy','duplicate_event','restart','spaced_recall'}
    assert next(x for x in report['trace'] if x['step']=='restart')['belief_preserved']
