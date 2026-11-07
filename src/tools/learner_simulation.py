# -*- coding: utf-8 -*-
"""LEARN-06 deterministic long-term probe, isolated from personal learning DB.

This is NOT a model intelligence score and never makes provider calls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time

from runtime.capabilities.tutor import TutorCapability
from runtime.conversation.contracts import ConversationEvent, DeliveryReceipt
from runtime.learning_core import LearningService, LearningStore, LearningPath, KnowledgePoint
from runtime.tutor.contracts import SemanticAssessment
from runtime.tutor.executive import TutorExecutive
from runtime.tutor.reasoner import ConservativeTutorReasoner
from runtime.tutor.session_store import TutorSessionLedger

DAY = 86400.0


class ScriptedReasoner(ConservativeTutorReasoner):
    """Không giả làm DeepSeek. Script chỉ kiểm tra side effects của production flow."""
    def __init__(self, verdict='fail', confidence=.95, error_code='off_by_one'):
        self.verdict, self.confidence, self.error_code = verdict, confidence, error_code

    def assess_response(self, observation, state, previous_move):
        return SemanticAssessment(kp_id=previous_move.kp_id, verdict=self.verdict,
            confidence=self.confidence, evidence_type='feynman_fail' if self.verdict=='fail' else 'feynman_pass',
            strength=.92, error_code=self.error_code if self.verdict == 'fail' else '',
            misconception='Nhầm điều kiện dừng' if self.verdict == 'fail' else '')


def event(ident: str, text: str) -> ConversationEvent:
    return ConversationEvent(event_id=ident, session_id='learn06:isolated', kind='user_turn', text=text)


def receipt(ident: str, success: bool) -> DeliveryReceipt:
    return DeliveryReceipt(delivery_id='delivery:' + ident, session_id='learn06:isolated',
                           generation=1, move_id='root:' + ident, successful=success)


def scenario(db: Path) -> dict:
    service = LearningService(LearningStore(db))
    service.register_path(LearningPath('learn06:python','Python', 'Hiểu vòng lặp for',(
        KnowledgePoint('python.for','for, range và điều kiện dừng','concept'),)))
    assert service.activate_path('learn06:python')
    learner = ScriptedReasoner()
    cap = TutorCapability(TutorExecutive(service, reasoner=learner,
                                         session_ledger=TutorSessionLedger()))
    trace = []

    # Root định phát, nhưng delivery fail; learner reply không được commit evidence.
    a = cap.invoke_for_root(arguments={}, event=event('s1','Hãy dạy tôi for'), recent_history=[])
    assert a['active']
    cap.observe_delivery_receipt(receipt('s1', False))
    cap.invoke_for_root(arguments={'response_to_previous':True},
                        event=event('s2','Tôi đoán 0,1,2,3'),recent_history=[])
    assert service.get_mastery('python.for').attempt_count == 0
    trace.append({'step':'failed_delivery','evidence_count':0,'verified':True})

    # Một bài thật qua Root + receipt, thêm một bằng chứng độc lập đã chấm
    # vào LearningService. Chưa giả định capability tự phát được 2 câu hỏi liền.
    for i in (1,):
        ask = cap.invoke_for_root(arguments={}, event=event(f'q{i}', 'Cho tôi câu hỏi kiểm tra'), recent_history=[])
        assert ask['expects_learner_response']
        cap.observe_delivery_receipt(receipt(f'q{i}', True))
        reply = cap.invoke_for_root(arguments={'response_to_previous':True},
                    event=event(f'a{i}', 'Em tính luôn cả điểm dừng cuối'),recent_history=[])
        assert reply['assessment']['verdict']=='fail'
        trace.append({'step':f'wrong_answer_{i}', 'evidence_count':service.get_mastery('python.for').attempt_count,
                     'repeated_error_patterns':len(service.repeated_error_patterns('python.for'))})

    assert service.commit_evidence('python.for','feynman_fail', correct=False,
        strength=.92, interaction_id='seeded-question-2',turn_id='seeded-answer-2',
        error_code='off_by_one')
    trace.append({'step':'independent_labeled_evidence_2',
                  'evidence_count':service.get_mastery('python.for').attempt_count})
    assert service.get_mastery('python.for').attempt_count == 2
    repeated = service.repeated_error_patterns('python.for')
    assert repeated, 'hai lỗi độc lập cần được ghi nhận là lặp lại'
    after_fail = cap.invoke_for_root(arguments={}, event=event('diagnose','Tiếp tục'),recent_history=[])
    trace.append({'step':'post_error_strategy', 'move_kind':after_fail['teaching_move'].get('kind'),
                 'learner_state_present':bool(after_fail['learner_state'])})
    # Input lặp/cùng interaction ID KHÔNG được tăng mastery.
    before = service.get_mastery('python.for').attempt_count
    assert service.commit_evidence('python.for','feynman_fail', correct=False,
        strength=.92, interaction_id='dup-check',turn_id='dup-turn',error_code='off_by_one')
    assert service.commit_evidence('python.for','feynman_fail', correct=False,
        strength=.92, interaction_id='dup-check',turn_id='dup-turn',error_code='off_by_one') is None
    assert service.get_mastery('python.for').attempt_count == before + 1
    trace.append({'step':'duplicate_event','idempotent':True})
    # Persistence across LearningService/SQLite restart.
    mastery_before = service.get_mastery('python.for').to_dict()
    belief_before = service.learner_belief('python.for').to_dict()
    service._store.close()
    again = LearningService(LearningStore(db))
    assert again.active_path() is not None
    assert again.get_mastery('python.for').to_dict() == mastery_before
    assert again.learner_belief('python.for').to_dict() == belief_before
    trace.append({'step':'restart','mastery_preserved':True,'belief_preserved':True})

    # Recall: new study can seed; only independent review can lengthen interval.
    now = time.time()
    assert again.commit_evidence('python.for','quiz_correct',correct=True,strength=.99,
                                 interaction_id='seed',turn_id='seed-turn',now=now)
    review_a = again.review_snapshot(['python.for'])['python.for']
    assert again.commit_evidence('python.for','application',correct=True,strength=.99,
                                 interaction_id='practice',turn_id='practice-turn',now=now+600)
    review_b = again.review_snapshot(['python.for'])['python.for']
    assert review_a['due_at'] == review_b['due_at']
    assert again.commit_evidence('python.for','quiz_correct',correct=True,strength=.96,
                                 interaction_id='independent-recall',turn_id='recall-turn',
                                 review_recall=True, now=now+DAY)
    review_c = again.review_snapshot(['python.for'])['python.for']
    assert review_c['memory']['recall_count'] == 1
    assert review_c['due_at'] > review_a['due_at']
    trace.append({'step':'spaced_recall', 'study_did_not_extend_review':True,
                  'independent_recall_extended_review':True})
    again._store.close()
    return {'scope':'scripted deterministic simulation (no DeepSeek / Writer / TTS)',
            'status':'pass', 'trace':trace, 'assertions':8}


def run(argv=None):
    parser=argparse.ArgumentParser(description='LEARN-06 isolated long-term scripted probe')
    parser.add_argument('--out',type=Path,default=Path('logs/learn06_simulation'))
    args=parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='eris_learn06_') as tmp:
        result=scenario(Path(tmp)/'only_synthetic_data.db')
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'simulation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'out':str(args.out),
                      'steps':[x['step'] for x in result['trace']]},ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(run())
