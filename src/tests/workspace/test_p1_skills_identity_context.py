# -*- coding: utf-8 -*-
"""P1: Skills, context dài và mục tiêu tự chủ gắn với Work Lane P0."""
import json
import threading
import time

import pytest

from runtime.agent_harness.work_skill_context import WorkSkillContext
from runtime.agent_harness.unified_sessions import UnifiedAgentSessions
from runtime.autonomy.goal_events import GoalEventLedger, GoalCandidate, SemanticCommitProof


def until(check, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.01)
    assert check(), 'Quá hạn chờ trạng thái'


def make_powers(tmp_path):
    root = tmp_path / 'powers'
    folder = root / 'algebra'
    folder.mkdir(parents=True)
    (folder/'metadata.json').write_text(json.dumps({
        'name': 'algebra', 'description': 'Toán ma trận và định thức',
        'keywords': ['matrix', 'math'],
        'tools': ['math_workspace', 'project_apply_patch']
    }), encoding='utf-8')
    (folder/'SKILL.md').write_text('# Hướng dẫn giải ma trận\n\nChỉ gọi tool khi cần.\n', encoding='utf-8')
    return root


def create_active_goal(db):
    ledger = GoalEventLedger(db)
    cand = GoalCandidate(goal_id='eris:curiosity:matrix', title='Kiểm tra ma trận 0-1',
                         success_criteria='Có kiểm chứng và lý giải',
                         identity_revision=7, motive_ids=('curiosity',),
                         evidence_refs=('screen:evidence-1',))
    p = ledger.propose(cand, event_id='propose:matrix')
    proof = SemanticCommitProof(commit_id='exec-commit-007', source='subject',
                                identity_revision=7, motive_ids=('curiosity',),
                                evidence_refs=('screen:evidence-1',), episode_id='episode-7')
    a = ledger.transition(p.goal_id, expected_revision=p.revision, status='active',
                          event_id='adopt:matrix', proof=proof)
    assert a.status == 'active'
    return ledger


def test_search_activate_skill_va_giu_context_qua_restart(tmp_path):
    powers = make_powers(tmp_path)
    seen = []
    def planner(ctx):
        seen.append(ctx)
        events = [x['kind'] for x in ctx['recent_events']]
        if not events or 'skill_result' not in events:
            return {'kind':'run_tool', 'action_id':'search_capabilities',
                    'text':'Tìm kỹ năng tính toán', 'arguments':{'query':'matrix'}}
        if not ctx.get('active_skills'):
            return {'kind':'run_tool', 'action_id':'activate_skill',
                    'text':'Đọc kỹ năng', 'arguments':{'skill_name':'algebra'}}
        return {'kind':'wait_user', 'action_id':'', 'text':'Có hướng dẫn rồi', 'arguments':{}}
    s = UnifiedAgentSessions(tmp_path/'jobs.sqlite', planner=planner, powers_root=powers)
    jid = s.start('Tính định thức')['job_id']
    until(lambda: s.snapshot(jid)['status']=='waiting_user')
    assert len(seen) >= 3
    assert any(x['name']=='algebra' for x in seen[-1]['active_skills'])
    assert 'project_apply_patch' not in seen[-1]['active_skills'][0]['available_tools']
    assert 'math_workspace' in seen[-1]['active_skills'][0]['available_tools']
    assert not any(e['kind']=='tool_dispatched' for e in s.events(jid))
    again = WorkSkillContext(tmp_path/'jobs.sqlite', powers_root=powers)
    assert again.activated(jid) == ['algebra']
    context = again.context(jid, allowed_tools=frozenset({'math_workspace'}),
                            events=s.events(jid), evidence='kết quả vẫn lưu')
    assert 'Hướng dẫn giải ma trận' in context['active_skills'][0]['instructions']
    assert 'kết quả' in context['last_evidence']


def test_skill_khong_tu_cap_quyen_va_tu_choi_path_khong_hop_le(tmp_path):
    powers=make_powers(tmp_path)
    s=WorkSkillContext(tmp_path/'db.sqlite', powers_root=powers)
    assert s.activate('job-1','algebra')['activated']
    assert s.activate('job-1','khong-co')['error']=='skill_not_found'
    # Dù metadata khai báo project_apply_patch, nó không nằm trong Work Lane allowlist.
    ctx=s.context('job-1',allowed_tools=frozenset({'math_workspace'}),events=[],evidence='')
    assert ctx['active_skills'][0]['available_tools']==['math_workspace']


def test_goal_chua_adopt_khong_duoc_chay(tmp_path):
    path=tmp_path/'goal.sqlite'
    ledger=GoalEventLedger(path)
    candidate=GoalCandidate(goal_id='eris:pending',title='Thử nghiệm',
                            success_criteria='Có kết quả',identity_revision=1,motive_ids=('curiosity',))
    ledger.propose(candidate,event_id='p')
    s=UnifiedAgentSessions(tmp_path/'work.sqlite', planner=None,goal_ledger_path=path)
    assert s.execute({'operation':'start_goal','goal_id':'eris:pending'})['error']=='goal_not_adopted'
    assert s.execute({'operation':'start_goal','goal_id':'not-found'})['error']=='goal_not_found'
    assert len(s.execute({'operation':'goals'})['goals']) == 1


def test_goal_da_adopt_chay_worker_luu_outcome_nhung_khong_tu_achieved(tmp_path):
    ledger=create_active_goal(tmp_path/'goal.sqlite')
    s=UnifiedAgentSessions(tmp_path/'work.sqlite',goal_ledger_path=tmp_path/'goal.sqlite',
                           planner=lambda ctx: {'kind':'complete','action_id':'',
                                                'text':'Đã kiểm tra', 'arguments':{}})
    created=s.execute({'operation':'start_goal','goal_id':'eris:curiosity:matrix'})
    jid=created['job_id']
    until(lambda: s.snapshot(jid)['status']=='completed')
    until(lambda: s.execute({'operation':'goal_status','goal_id':'eris:curiosity:matrix'})['outcome_ref']!='')
    g=ledger.get('eris:curiosity:matrix')
    assert g.status=='active'
    assert g.payload['last_outcome']['action_id']==jid
    assert g.payload['last_outcome']['outcome_status']=='completed'
    assert s.execute({'operation':'start_goal','goal_id':'eris:curiosity:matrix'})['already_claimed']
    assert len([x for x in ledger.events(g.goal_id) if x['event_type']=='outcome_observed'])==1
    restarted=UnifiedAgentSessions(tmp_path/'work.sqlite',goal_ledger_path=tmp_path/'goal.sqlite',planner=None)
    assert restarted.execute({'operation':'goal_status','goal_id':g.goal_id})['job_id']==jid
    assert restarted.execute({'operation':'start_goal','goal_id':g.goal_id})['already_claimed']


def test_goal_bi_doi_trang_thai_truoc_khi_worker_xong_khong_attach_outcome(tmp_path):
    ledger=create_active_goal(tmp_path/'goal.sqlite')
    ready=threading.Event(); release=threading.Event()
    def planner(ctx):
        ready.set();release.wait(2)
        return {'kind':'complete','action_id':'','text':'muộn','arguments':{}}
    s=UnifiedAgentSessions(tmp_path/'work.sqlite',goal_ledger_path=tmp_path/'goal.sqlite',planner=planner)
    jid=s.execute({'operation':'start_goal','goal_id':'eris:curiosity:matrix'})['job_id']
    assert ready.wait(2)
    goal=ledger.get('eris:curiosity:matrix')
    proof=SemanticCommitProof(commit_id='stop-007',source='subject',identity_revision=7,
                              motive_ids=('curiosity',),evidence_refs=(),episode_id='episode-7')
    ledger.transition(goal.goal_id,expected_revision=goal.revision,status='suspended',
                      event_id='suspend-007',proof=proof)
    release.set()
    until(lambda:s.snapshot(jid)['status']=='cancelled')
    until(lambda:s.execute({'operation':'goal_status','goal_id':goal.goal_id})['link_state']=='goal_changed')
    assert ledger.get(goal.goal_id).payload.get('last_outcome') is None


def test_parallel_work_capability_co_api_p1(tmp_path):
    from actions.capability_registry import validate_capability_arguments
    for op in ['goals', 'goal_status', 'start_goal']:
        args={'operation':op}
        if op!='goals':args['goal_id']='eris:goal-1'
        assert validate_capability_arguments('parallel_work',args)[0]


def test_activated_skill_khong_mo_duoc_action_ngoai_allowlist(tmp_path):
    powers = make_powers(tmp_path)
    calls = []
    def planner(ctx):
        if not ctx['active_skills']:
            return {'kind':'run_tool','text':'đọc kỹ năng','action_id':'activate_skill',
                    'arguments':{'skill_name':'algebra'}}
        return {'kind':'run_tool','text':'thử quyền ngoài allowlist',
                'action_id':'project_apply_patch', 'arguments':{'patch':'x'}}
    s=UnifiedAgentSessions(tmp_path/'job.sqlite',powers_root=powers,planner=planner,
                           tool=lambda *args: calls.append(args))
    jid=s.start('Kiểm tra phân quyền')['job_id']
    until(lambda:s.snapshot(jid)['status']=='failed')
    assert calls == []
    assert 'project_apply_patch' not in s.work._skill_context.context(
        jid,allowed_tools=frozenset({'math_workspace'}),events=[],evidence='')['active_skills'][0]['available_tools']


def test_goal_tranh_chap_start_cung_luc_chi_tao_mot_job(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    create_active_goal(tmp_path/'goals.sqlite')
    s=UnifiedAgentSessions(tmp_path/'work.sqlite',goal_ledger_path=tmp_path/'goals.sqlite',
                           planner=lambda ctx: {'kind':'wait_user','text':'Dừng để chờ',
                                                'action_id':'','arguments':{}})
    def attempt():
        return s.execute({'operation':'start_goal','goal_id':'eris:curiosity:matrix'})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:attempt(),range(8)))
    ids={r.get('job_id') for r in results if r.get('job_id')}
    assert len(ids)==1
    assert sum(1 for r in results if r.get('already_claimed'))>=7
    assert len(s.recent()) == 1


def test_bang_chung_con_duoc_nho_sau_nhieu_event(tmp_path):
    from runtime.workspace.parallel_work import ParallelWorkService, WorkJob
    svc=ParallelWorkService(tmp_path/'work.sqlite',planner=None)
    job=WorkJob(job_id='work-long',task='Bài nhiều bước')
    svc._store(job,'created')
    svc._store(job,'tool_result','evidence-that-matters')
    for i in range(40):
        svc._store(job,'progress',f'Phân tích bước {i}')
    ctx=svc._skill_context.context(job.job_id,allowed_tools=frozenset(),events=svc.events(job.job_id,10),evidence='')
    assert any('evidence-that-matters' in x['detail'] for x in ctx['evidence_digest'])


def test_executor_p1_goal_start_va_goals(tmp_path):
    from actions.executor import ActionExecutor
    ledger=create_active_goal(tmp_path/'goals.sqlite')
    sessions=UnifiedAgentSessions(tmp_path/'work.sqlite',goal_ledger_path=tmp_path/'goals.sqlite',
        planner=lambda _: {'kind':'complete','text':'đã xong','action_id':'','arguments':{}})
    exe=ActionExecutor()
    exe._parallel_work_service=sessions
    goals=exe._parallel_work(json.dumps({'operation':'goals'}))
    assert goals.success
    assert json.loads(goals.data['job'])['goals'][0]['goal_id']=='eris:curiosity:matrix'
    start=exe._parallel_work(json.dumps({'operation':'start_goal','goal_id':'eris:curiosity:matrix'}))
    assert start.success
    jid=json.loads(start.data['job'])['job_id']
    until(lambda:sessions.snapshot(jid)['status']=='completed')
    assert ledger.get('eris:curiosity:matrix').status=='active'


def test_goal_suspended_trong_luc_model_suy_luan_thi_khong_goi_tool(tmp_path):
    ledger=create_active_goal(tmp_path/'goal.sqlite')
    ready=threading.Event(); release=threading.Event(); effects=[]
    def planner(ctx):
        ready.set();release.wait(2)
        return {'kind':'run_tool','text':'muộn','action_id':'math_workspace','arguments':{'kind':'matrix'}}
    s=UnifiedAgentSessions(tmp_path/'work.sqlite',goal_ledger_path=tmp_path/'goal.sqlite',
                           planner=planner,tool=lambda *args: effects.append(args))
    jid=s.execute({'operation':'start_goal','goal_id':'eris:curiosity:matrix'})['job_id']
    assert ready.wait(2)
    goal=ledger.get('eris:curiosity:matrix')
    proof=SemanticCommitProof(commit_id='stop-work',source='subject',identity_revision=7,
                              motive_ids=('curiosity',),evidence_refs=(),episode_id='episode-7')
    ledger.transition(goal.goal_id,expected_revision=goal.revision,status='suspended',
                      event_id='stop-work',proof=proof)
    release.set()
    until(lambda:s.snapshot(jid)['status']=='cancelled')
    assert effects==[]


def test_goal_co_action_in_flight_chua_doi_chieu_khong_tao_work_moi(tmp_path):
    ledger=create_active_goal(tmp_path/'goal.sqlite')
    with ledger._db() as db:
        db.execute('''INSERT INTO autonomy_actions
            (action_id,goal_id,goal_revision,goal_epoch,status,command_hash,command_key,outcome_json,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',('v3:cmd-old','eris:curiosity:matrix',2,1,'unknown','hash','k1','{}',0.))
    s=UnifiedAgentSessions(tmp_path/'work.sqlite',goal_ledger_path=tmp_path/'goal.sqlite',planner=None)
    result=s.execute({'operation':'start_goal','goal_id':'eris:curiosity:matrix'})
    assert result['error']=='goal_has_unreconciled_action'
    assert s.recent()==[]


def test_suspend_goal_khi_tool_dang_chay_loai_receipt_den_muon(tmp_path):
    ledger=create_active_goal(tmp_path/'goal.sqlite')
    ready=threading.Event(); release=threading.Event()
    def tool(*_):
        ready.set();release.wait(2)
        class Receipt:
            success=True; evidence=('receipt:late',); message='đến muộn'; data={'x':1}
        return Receipt()
    def planner(ctx):
        return {'kind':'run_tool','text':'đang chạy','action_id':'math_workspace',
                'arguments':{'kind':'matrix'}}
    svc=UnifiedAgentSessions(tmp_path/'work.sqlite',goal_ledger_path=tmp_path/'goal.sqlite',
                            planner=planner,tool=tool)
    jid=svc.execute({'operation':'start_goal','goal_id':'eris:curiosity:matrix'})['job_id']
    assert ready.wait(2)
    g=ledger.get('eris:curiosity:matrix')
    proof=SemanticCommitProof(commit_id='suspend-in-tool',source='subject',identity_revision=7,
                              motive_ids=('curiosity',),evidence_refs=(),episode_id='episode-7')
    ledger.transition(g.goal_id,expected_revision=g.revision,status='suspended',
                      event_id='suspend-tool',proof=proof)
    release.set()
    until(lambda:svc.snapshot(jid)['status']=='cancelled')
    assert any(e['kind']=='late_tool_result_discarded' for e in svc.events(jid))
    assert not any(e['kind']=='tool_result' for e in svc.events(jid))
    # Không nhận outcome muộn, nhưng cũng không tuyên bố tool đã được rollback.
    assert ledger.get(g.goal_id).payload.get('last_outcome') is None
