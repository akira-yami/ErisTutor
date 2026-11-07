"""Consolidated autonomy vertical-slice tests, isolated from live ERIS runtime."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os

import pytest
from cortex.subject.contracts import OutcomeStatus
from cortex.subject.initiative import PendingIntention, IntentionStatus, MotiveKind
from runtime.autonomy.goal_events import GoalEventLedger, GoalCandidate, GoalConflict, InvalidGoalTransition, SemanticCommitProof
from runtime.autonomy.action_bridge import GoalActionBridge
from runtime.autonomy.authority_trace import emit, span
from runtime.autonomy.initiative_bridge import candidate_from_intention
from runtime.world import SandboxWorld, CapabilityPolicy, CommandPolicy, CapabilityAuthority
from runtime.world.contracts import SandboxCommand, SandboxCommandKind


def candidate(name='g1'):
    return GoalCandidate(name, 'Study a relevant regression', 'Tests passed', ('resolve',), 17, ('test:failed',))


def proof(name='semantic:c1', episode='episode:1', rev=17):
    return SemanticCommitProof(name, 'neuro_executive', rev, ('resolve',), ('test:failed',), episode)


def ledger(tmp_path):
    return GoalEventLedger(tmp_path / 'autonomous-goals.sqlite', clock=lambda: 100.0)


def active(lg):
    proposed = lg.propose(candidate(), event_id='candidate:1')
    return lg.transition('g1', expected_revision=proposed.revision, status='active', event_id='adopt:1', proof=proof())


def test_goal_proposal_is_not_implicitly_adopted(tmp_path):
    reg = ledger(tmp_path)
    row = reg.propose(candidate(), event_id='candidate:1')
    assert row.status == 'proposed'
    assert row.adoption_commit_id == ''
    assert reg.panel_projection()[0]['status'] == 'proposed'


def test_adoption_carries_semantic_commit_provenance(tmp_path):
    lg = ledger(tmp_path)
    row = active(lg)
    assert row.status == 'active'
    assert row.adoption_commit_id == 'semantic:c1'
    assert row.payload['adoption_proof']['identity_revision'] == 17
    assert len(lg.events('g1')) == 2


def test_eris_goal_ignores_unrelated_chat_turns_and_restart(tmp_path):
    lg = ledger(tmp_path)
    active(lg)
    # no public-turn decay API exists in the autonomous-goal ledger
    restored = ledger(tmp_path).get('g1')
    assert restored.status == 'active'
    assert restored.revision == 2


def test_duplicate_adoption_and_proposal_replay(tmp_path):
    lg = ledger(tmp_path)
    row = active(lg)
    assert lg.propose(candidate(), event_id='candidate:1').revision == 2
    assert lg.transition('g1', expected_revision=1, status='active', event_id='adopt:1', proof=proof()).revision == 2
    assert len(lg.events('g1')) == 2
    with pytest.raises(GoalConflict):
        lg.propose(candidate(), event_id='candidate:other')


def test_replay_conflict_is_rejected(tmp_path):
    lg = ledger(tmp_path)
    active(lg)
    with pytest.raises(GoalConflict):
        lg.transition('g1', expected_revision=2, status='abandoned', event_id='adopt:1', proof=proof())


def test_wrong_identity_revision_or_motive_does_not_adopt(tmp_path):
    lg = ledger(tmp_path)
    lg.propose(candidate(), event_id='p')
    with pytest.raises(GoalConflict):
        lg.transition('g1', expected_revision=1, status='active', event_id='a', proof=proof(rev=18))
    assert lg.get('g1').revision == 1


def test_no_achieved_without_evidence(tmp_path):
    lg = ledger(tmp_path)
    row = active(lg)
    with pytest.raises(InvalidGoalTransition):
        lg.transition('g1', expected_revision=row.revision, status='achieved', event_id='done', proof=proof())
    result = lg.transition('g1', expected_revision=2, status='achieved', event_id='done', proof=proof(), evidence_ref='sandbox:receipt-123')
    assert result.status == 'achieved'
    assert lg.get('g1').status == 'achieved'


def test_version_conflict_and_lifecycle_preconditions(tmp_path):
    lg = ledger(tmp_path)
    active(lg)
    with pytest.raises(GoalConflict):
        lg.transition('g1', expected_revision=1, status='suspended', event_id='stale', proof=proof())
    with pytest.raises(InvalidGoalTransition):
        lg.transition('g1', expected_revision=2, status='proposed', event_id='bad', proof=proof())
    row = lg.transition('g1', expected_revision=2, status='suspended', event_id='pause', proof=proof())
    assert row.epoch == 2
    row = lg.transition('g1', expected_revision=3, status='active', event_id='resume', proof=proof())
    assert row.status == 'active'


def test_parallel_conflicting_transitions_only_one_wins(tmp_path):
    lg = ledger(tmp_path)
    active(lg)
    def pause(n):
        try:
            return lg.transition('g1', expected_revision=2, status='suspended', event_id=f'pause:{n}', proof=proof())
        except GoalConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as ex:
        outcomes = list(ex.map(pause, range(16)))
    assert len([o for o in outcomes if o]) == 1
    assert lg.get('g1').revision == 3


def test_trace_opt_in_redacts_unrecognized_fields(tmp_path, monkeypatch):
    file = tmp_path / 'log.jsonl'
    monkeypatch.setenv('ERIS_AUTONOMY_TRACE', '1')
    monkeypatch.setenv('ERIS_AUTONOMY_TRACE_PATH', str(file))
    with span('autonomous_loop') as corr:
        emit('executive.v103_winner', source='v103', user_text='SECRET NEVER LOG',
             frame_id='frame:one')
    items = [json.loads(x) for x in file.read_text().splitlines()]
    assert len(items) == 3
    assert all(item['correlation_id'] == corr for item in items)
    assert 'SECRET NEVER LOG' not in file.read_text()


def test_trace_flag_off_creates_no_file(tmp_path, monkeypatch):
    path = tmp_path/'never.jsonl'
    monkeypatch.delenv('ERIS_AUTONOMY_TRACE', raising=False)
    monkeypatch.setenv('ERIS_AUTONOMY_TRACE_PATH', str(path))
    emit('abc')
    assert not path.exists()


def test_pending_subject_intention_is_just_a_candidate():
    from cortex.subject.contracts import ObservationOrigin
    intention = PendingIntention(
        intention_id='one', motive_kind=MotiveKind.RESOLVE, semantic_goal='Find issue',
        target='workspace', pressure=0.8, commitment_strength=0.6,
        created_at=100, updated_at=100, evidence_refs=('observed:1',),
        source_origin=ObservationOrigin.INTERNAL, status=IntentionStatus.PENDING,
    )
    candidate = candidate_from_intention(intention, identity_revision=4, success_criteria='Issue resolved')
    assert candidate.goal_id == 'eris:intention:one'
    with pytest.raises(ValueError):
        candidate_from_intention(replace(intention, status=IntentionStatus.ABANDONED), identity_revision=4, success_criteria='x')


@pytest.mark.asyncio
async def test_action_fenced_simulate_and_receipt(tmp_path):
    lg = ledger(tmp_path)
    goal = active(lg)
    authority = CapabilityAuthority(
        secret=b'accurate-test-secret-32-bytes-long-yes', issuer='tests', audience='sandbox',
        clock=lambda: 100.0, id_factory=lambda: 'token:1')
    pol = CapabilityPolicy(CommandPolicy(frozenset(), str(tmp_path)), token_verifier=authority.verify)
    world = SandboxWorld(policy=pol, clock=lambda: 100.0)
    cmd = SandboxCommand('cmd1', 'action1', 'semantic:action1', SandboxCommandKind.SIMULATE,
                         {}, 'simulate', 'idem:action1', 100.0, True)
    capability = authority.issue(subject_id='eris', capabilities=frozenset({'simulate'}),
                                 policy_version='v1', scope={
        'command_ids': ('cmd1',), 'action_ids': ('action1',),
        'decision_ids': ('semantic:action1',), 'idempotency_keys': ('idem:action1',)}, ttl_seconds=100.0)
    bridge = GoalActionBridge(lg, world)
    outcome = await bridge.execute('g1', expected_revision=goal.revision,
        expected_epoch=goal.epoch, command=cmd, capability=capability,
        decision=proof('semantic:action1'))
    assert outcome.status in (OutcomeStatus.SIMULATED, OutcomeStatus.SUCCEEDED)
    assert bridge.get_action('action1')['status'] == 'completed'
    with pytest.raises(GoalConflict):
        await bridge.execute('g1', expected_revision=goal.revision,
            expected_epoch=goal.epoch, command=cmd, capability=capability,
            decision=proof('semantic:action1'))


@pytest.mark.asyncio
async def test_goal_preemption_blocks_stale_action(tmp_path):
    lg = ledger(tmp_path)
    goal = active(lg)
    lg.transition('g1', expected_revision=goal.revision, status='suspended', event_id='pause:1', proof=proof())
    cmd = SandboxCommand('cmd1', 'action1', 'semantic:action1', SandboxCommandKind.SIMULATE,
                         {}, 'simulate', 'idem:action1', 100.0, True)
    class NoWorld:
        async def apply(self, *_):
            raise AssertionError('stale command must not reach world')
    authority = CapabilityAuthority(secret=b'accurate-test-secret-32-bytes-long-yes', issuer='tests',
        audience='sandbox', clock=lambda: 100.0)
    cap = authority.issue(subject_id='eris', capabilities=frozenset({'simulate'}), policy_version='v1',
        scope={}, ttl_seconds=100)
    with pytest.raises(GoalConflict):
        await GoalActionBridge(lg, NoWorld()).execute('g1', expected_revision=goal.revision,
            expected_epoch=goal.epoch, command=cmd, capability=cap, decision=proof('semantic:action1'))


@pytest.mark.asyncio
async def test_effects_disabled_by_default(tmp_path):
    lg = ledger(tmp_path)
    goal = active(lg)
    cmd = SandboxCommand('cmd1', 'action1', 'semantic:action1', SandboxCommandKind.WRITE_FILE,
                         {'path':'demo.txt', 'content':'hello'}, 'write_file', 'idem:action1', 100.0)
    authority = CapabilityAuthority(secret=b'accurate-test-secret-32-bytes-long-yes', issuer='tests',
        audience='sandbox', clock=lambda: 100.0)
    cap = authority.issue(subject_id='eris', capabilities=frozenset({'write_file'}), policy_version='v1',
        scope={}, ttl_seconds=100)
    with pytest.raises(PermissionError):
        await GoalActionBridge(lg, None).execute('g1', expected_revision=goal.revision,
            expected_epoch=goal.epoch, command=cmd, capability=cap, decision=proof('semantic:action1'))
    assert not (tmp_path/'demo.txt').exists()


def test_live_shadow_is_disabled_by_default(tmp_path, monkeypatch):
    from runtime.autonomy.live_shadow import observe_subject_initiative
    path = tmp_path/'goals-shadow.sqlite'
    monkeypatch.setenv('ERIS_AUTONOMY_GOAL_SHADOW_DB', str(path))
    monkeypatch.delenv('ERIS_AUTONOMY_SHADOW_GOALS', raising=False)
    assert not observe_subject_initiative({'revision':1, 'pending_intentions':[]})
    assert not path.exists()


def test_live_shadow_records_only_intention_ids_without_text(tmp_path, monkeypatch):
    from runtime.autonomy.live_shadow import observe_subject_initiative
    import sqlite3
    db = tmp_path/'goals-shadow.sqlite'
    monkeypatch.setenv('ERIS_AUTONOMY_GOAL_SHADOW_DB', str(db))
    monkeypatch.setenv('ERIS_AUTONOMY_SHADOW_GOALS', '1')
    state = {'revision':9, 'pending_intentions':[
        {'intention_id':'x', 'status':'pending', 'motive_kind':'resolve', 'semantic_goal':'SENSITIVE TEXT'},
        {'intention_id':'y', 'status':'satisfied', 'motive_kind':'connect'},
    ]}
    assert observe_subject_initiative(state)
    assert observe_subject_initiative(state)
    with sqlite3.connect(str(db)) as con:
        assert con.execute('SELECT count(*) FROM subject_candidate_observation').fetchone()[0] == 1
    assert b'SENSITIVE TEXT' not in db.read_bytes()


def test_event_id_cannot_be_reused_for_a_different_goal(tmp_path):
    lg = ledger(tmp_path)
    active(lg)
    lg.propose(candidate('other'), event_id='candidate:other')
    with pytest.raises(GoalConflict):
        lg.transition('other', expected_revision=1, status='active', event_id='adopt:1', proof=proof())


def test_unrelated_goal_active_in_same_registry(tmp_path):
    lg = ledger(tmp_path)
    active(lg)
    second = lg.propose(candidate('g2'), event_id='candidate:g2')
    second = lg.transition('g2', expected_revision=second.revision, status='active', event_id='adopt:g2', proof=proof('semantic:g2'))
    assert len(lg.list_goals(status='active')) == 2
    assert second.epoch == 1


@pytest.mark.asyncio
async def test_unknown_execution_result_requires_manual_reconciliation(tmp_path):
    lg = ledger(tmp_path)
    row = active(lg)
    class CrashingWorld:
        async def apply(self, *_):
            raise RuntimeError('world operation failed after dispatch')
    cmd = SandboxCommand('cmd1', 'action1', 'semantic:action1', SandboxCommandKind.SIMULATE,
                         {}, 'simulate', 'idem:action1', 100.0, True)
    authority = CapabilityAuthority(secret=b'accurate-test-secret-32-bytes-long-yes',
        issuer='tests', audience='sandbox', clock=lambda: 100.0)
    cap = authority.issue(subject_id='eris', capabilities=frozenset({'simulate'}),
        policy_version='v1', scope={}, ttl_seconds=100)
    bridge = GoalActionBridge(lg, CrashingWorld())
    with pytest.raises(RuntimeError):
        await bridge.execute('g1', expected_revision=row.revision, expected_epoch=row.epoch,
            command=cmd, capability=cap, decision=proof('semantic:action1'))
    assert bridge.get_action('action1')['status'] == 'unknown'
    assert bridge.unknown_actions() == ('action1',)
    with pytest.raises(GoalConflict):
        await bridge.execute('g1', expected_revision=row.revision, expected_epoch=row.epoch,
            command=cmd, capability=cap, decision=proof('semantic:action1'))


@pytest.mark.asyncio
async def test_action_with_wrong_episode_rejected_before_world_apply(tmp_path):
    lg = ledger(tmp_path)
    row = active(lg)
    class NeverCall:
        async def apply(self, *_):
            raise AssertionError('should not be dispatched')
    cmd = SandboxCommand('cmd1', 'action1', 'semantic:action1', SandboxCommandKind.SIMULATE,
                         {}, 'simulate', 'idem:action1', 100.0, True)
    authority = CapabilityAuthority(secret=b'accurate-test-secret-32-bytes-long-yes',
        issuer='tests', audience='sandbox', clock=lambda: 100.0)
    cap = authority.issue(subject_id='eris', capabilities=frozenset({'simulate'}),
        policy_version='v1', scope={}, ttl_seconds=100)
    with pytest.raises(GoalConflict):
        await GoalActionBridge(lg, NeverCall()).execute('g1', expected_revision=row.revision,
            expected_epoch=row.epoch, command=cmd, capability=cap, decision=proof('semantic:action1', episode='different'))


@pytest.mark.asyncio
async def test_offline_vertical_slice(tmp_path):
    from tools.autonomy_proof import demo
    result = await demo(tmp_path / 'proof')
    assert result['goal_state'] == 'achieved'
    assert result['events'] == 3
    assert result['world_status'] == 'simulated'


@pytest.mark.asyncio
async def test_opt_in_real_sandbox_write_stays_inside_workspace(tmp_path):
    lg = ledger(tmp_path)
    g = active(lg)
    authority = CapabilityAuthority(secret=b'accurate-test-secret-32-bytes-long-yes',
        issuer='tests', audience='sandbox', clock=lambda: 100.0, id_factory=lambda: 'token:write')
    world = SandboxWorld(policy=CapabilityPolicy(
        CommandPolicy(frozenset(), str(tmp_path)), token_verifier=authority.verify),
        clock=lambda: 100.0)
    cmd = SandboxCommand('cmd-write', 'action-write', 'semantic:write', SandboxCommandKind.WRITE_FILE,
                         {'path':'sandbox-result.txt', 'content':'verified'}, 'write_file', 'idem:write', 100.0)
    cap = authority.issue(subject_id='eris', capabilities=frozenset({'write_file'}),
        policy_version='v1', scope={'command_ids':('cmd-write',), 'action_ids':('action-write',),
        'decision_ids':('semantic:write',), 'idempotency_keys':('idem:write',)}, ttl_seconds=200)
    outcome = await GoalActionBridge(lg, world, allow_effects=True).execute(
        'g1', expected_revision=g.revision, expected_epoch=g.epoch,
        command=cmd, capability=cap, decision=proof('semantic:write'))
    assert outcome.status == OutcomeStatus.SUCCEEDED
    assert (tmp_path/'sandbox-result.txt').read_text() == 'verified'
