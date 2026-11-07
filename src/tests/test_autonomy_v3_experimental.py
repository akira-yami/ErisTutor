# -*- coding: utf-8 -*-
"""Kiểm chứng V3 với sandbox thật; không giả nhận chạy Executive/LLM live."""
import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

from runtime.autonomy.agency_v3 import AutonomyLoopV3, AutonomyRuntimeV3, ensure_autonomy_v3, _hash
from runtime.autonomy.goal_events import GoalEventLedger
from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld


def view():
    return {'revision': 7, 'pending_intentions': [
        {'intention_id': 'intrinsic-curiosity-007', 'motive_kind': 'explore',
         'semantic_goal': 'Tự khám phá công cụ mới', 'status': 'pending',
         'pressure': .89, 'commitment_strength': .86,
         'evidence_refs': ['observation:local:07']}]}


def setup(tmp_path):
    ledger = GoalEventLedger(tmp_path / 'autonomy.db')
    authority = CapabilityAuthority(secret=b'local-sandbox-experimental-key-32', issuer='test', audience='sandbox')
    world = SandboxWorld(policy=CapabilityPolicy(CommandPolicy(frozenset(), str(tmp_path)), token_verifier=authority.verify))
    def token(command):
        return authority.issue(subject_id='eris', capabilities=frozenset({'read_file', 'write_file'}),
            policy_version='v1', scope={
                'command_ids': [command.command_id], 'action_ids': [command.action_id],
                'decision_ids': [command.decision_id], 'idempotency_keys': [command.idempotency_key],
                'workspace_prefixes': ['plans']}, ttl_seconds=60)
    return AutonomyLoopV3(ledger, world, token, workspace=tmp_path), ledger


def test_hai_buoc_tool_that_va_phan_tinh(tmp_path):
    loop, ledger = setup(tmp_path)
    first = asyncio.run(loop.cycle(view(), preempted=lambda: False))
    assert first['status'] == 'da_tao_workspace', first
    path = tmp_path / loop.plan.path(first['goal_id'])
    assert path.is_file()
    assert 'Tự khám phá' in path.read_text(encoding='utf-8')
    second = asyncio.run(loop.cycle(view(), preempted=lambda: False))
    assert second['status'] == 'da_xac_minh_workspace', second
    goal = ledger.get(first['goal_id'])
    assert goal.status == 'active', 'Không giả nhận hoàn thành mục tiêu ngữ nghĩa'
    assert goal.payload['last_reflection']['assessment'] == 'workspace_verified'
    assert len(ledger.events(goal.goal_id)) == 5
    assert asyncio.run(loop.cycle(view(), preempted=lambda: False))['status'] == 'dang_quan_sat_khong_co_viec_moi'
    with ledger._db() as conn:
        assert conn.execute('SELECT count(*) FROM autonomy_actions').fetchone()[0] == 2


def test_khong_replay_khi_ngat_sau_side_effect(tmp_path):
    loop, ledger = setup(tmp_path)
    flag = {'preempt': False}
    original = loop.bridge.world
    class AfterApply:
        async def apply(self, command, capability):
            result = await original.apply(command, capability)
            flag['preempt'] = True
            return result
    loop.bridge.world = AfterApply()
    first = asyncio.run(loop.cycle(view(), preempted=lambda: flag['preempt']))
    assert first['status'] == 'ket_qua_sau_ngat_cho_doi_chieu'
    assert ledger.get(first['goal_id']).status == 'active'
    flag['preempt'] = False
    loop.bridge.world = original
    res = asyncio.run(loop.cycle(view(), preempted=lambda: False))
    assert res['status'] == 'da_tao_workspace', res
    assert asyncio.run(loop.cycle(view(), preempted=lambda: False))['status'] == 'da_xac_minh_workspace'
    with ledger._db() as conn:
        assert conn.execute('SELECT count(*) FROM autonomy_actions').fetchone()[0] == 2


def test_reflection_khoi_phuc_sau_ngat(tmp_path):
    loop, ledger = setup(tmp_path)
    asyncio.run(loop.cycle(view(), preempted=lambda: False))
    flag = {'preempt': False}
    original = loop.bridge.world
    class AfterRead:
        async def apply(self, command, capability):
            result = await original.apply(command, capability)
            flag['preempt'] = True
            return result
    loop.bridge.world = AfterRead()
    result = asyncio.run(loop.cycle(view(), preempted=lambda: flag['preempt']))
    assert result['status'] == 'ket_qua_sau_ngat_cho_doi_chieu'
    flag['preempt'] = False
    loop.bridge.world = original
    result = asyncio.run(loop.cycle(view(), preempted=lambda: False))
    assert result['status'] == 'da_xac_minh_workspace', result
    assert ledger.get(result['goal_id']).payload['last_reflection']['assessment'] == 'workspace_verified'


def test_default_runtime_bat_va_tat_theo_flag(monkeypatch, tmp_path):
    monkeypatch.delenv('ERIS_AUTONOMY_V3_MODE', raising=False)
    monkeypatch.setenv('ERIS_AUTONOMY_V3_DB', str(tmp_path / 'goals.db'))
    monkeypatch.setenv('ERIS_AUTONOMY_V3_WORKDIR', str(tmp_path / 'work'))
    host = SimpleNamespace(chat_mode=False, transport=SimpleNamespace(send_json=lambda _: None),
                           subject_service=SimpleNamespace(initiative_view=view))
    runtime = ensure_autonomy_v3(host)
    assert runtime is not None and runtime.mode == 'sandbox'
    runtime.pump()
    assert runtime._future is not None
    runtime._future.result(timeout=6)
    runtime.pump()
    # Tăng nhịp để tick tiếp, không chờ timer thực trong unit test.
    runtime._last_tick = 0
    runtime.pump()
    runtime._future.result(timeout=6)
    runtime.pump()
    assert runtime.ledger.list_goals()[0].payload['last_reflection']['assessment'] == 'workspace_verified'
    monkeypatch.setenv('ERIS_AUTONOMY_V3_MODE', 'off')
    assert ensure_autonomy_v3(host) is None
    assert host._autonomy_v3 is None


def test_restart_tiep_tuc_tu_buoc_hai(tmp_path):
    loop, ledger = setup(tmp_path)
    first = asyncio.run(loop.cycle(view(), preempted=lambda: False))
    assert first['step'] == 1
    # Tạo World và instance mới: dữ liệu duy nhất bền vững ở SQLite và workspace.
    other, restored = setup(tmp_path)
    second = asyncio.run(other.cycle(view(), preempted=lambda: False))
    assert second['status'] == 'da_xac_minh_workspace'
    assert restored.get(first['goal_id']).status == 'active'
    with restored._db() as conn:
        assert conn.execute('SELECT count(*) FROM autonomy_actions').fetchone()[0] == 2


def test_khong_cho_tool_mo_ra_ngoai_sandbox(tmp_path):
    loop, ledger = setup(tmp_path)
    from runtime.world.contracts import SandboxCommand, SandboxCommandKind
    from runtime.autonomy.goal_events import GoalConflict
    from dataclasses import replace
    first = asyncio.run(loop.cycle(view(), preempted=lambda: False))
    goal = ledger.get(first['goal_id'])
    choice = __import__('runtime.autonomy.agency_v3', fromlist=['_intention_choices'])._intention_choices(view())[0]
    proof = loop._proof(choice, first['goal_id'], 'action')
    command = loop.plan.build(choice, proof, first['goal_id'], 2)
    injected = replace(command, action_id=command.action_id + ':outside',
                       command_id=command.command_id + ':outside',
                       idempotency_key=command.idempotency_key + ':outside',
                       arguments={'path': '../escape.txt'})
    outcome = asyncio.run(loop.bridge.execute(
        first['goal_id'], expected_revision=goal.revision, expected_epoch=goal.epoch,
        command=injected, capability=loop.capability_provider(injected), decision=proof,
    ))
    assert str(outcome.status).lower() == 'denied'
    assert not (tmp_path.parent / 'escape.txt').exists()


def test_preemption_truoc_dispatch_khong_tao_action(tmp_path):
    loop, ledger = setup(tmp_path)
    first = asyncio.run(loop.cycle(view(), preempted=lambda: True))
    assert first['status'] == 'bi_ngat'
    with ledger._db() as conn:
        assert conn.execute('SELECT count(*) FROM autonomy_actions').fetchone()[0] == 0


def test_tu_khoi_phat_tu_dong_co_noi_sinh_khong_can_user(tmp_path):
    loop, ledger = setup(tmp_path)
    subject = {'revision': 81, 'pending_intentions': [], 'active_motives': [{
        'motive_id': 'curiosity:born-in-subject', 'semantic_goal': 'Tự tìm hiểu một hiện tượng',
        'kind': 'explore', 'pressure': .85, 'epistemic_value': .95,
        'evidence_refs': ['self:observation:81'],
    }]}
    first = asyncio.run(loop.cycle(subject, preempted=lambda: False))
    assert first['status'] == 'da_tao_workspace', first
    second = asyncio.run(loop.cycle(subject, preempted=lambda: False))
    assert second['status'] == 'da_xac_minh_workspace', second
    goal = ledger.get(first['goal_id'])
    assert goal.payload['adoption_proof']['source'] == 'subject_motive_experiment_not_v103'
    assert 'Tự tìm hiểu' in (tmp_path / loop.plan.path(first['goal_id'])).read_text(encoding='utf-8')
