"""Offline, bounded proof of the proposed autonomy plumbing.

This is NOT a live, self-generated Eris decision: the demo seeds a synthetic
Subject/Executive commit and runs only SandboxWorld.SIMULATE. No networking,
file-writing capability, chat, or TTS is enabled.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
import time

from runtime.autonomy.action_bridge import GoalActionBridge
from runtime.autonomy.goal_events import GoalCandidate, GoalEventLedger, SemanticCommitProof
from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld
from runtime.world.contracts import SandboxCommand, SandboxCommandKind


async def demo(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    goals = GoalEventLedger(root / 'goals.sqlite')
    candidate = GoalCandidate(
        'demo:discover-and-review', 'Verify an observed failing test',
        'Simulation receipt exists and was evaluated', ('resolve',), 1,
        ('demo:observed-test-fail',),
    )
    current = goals.propose(candidate, event_id='demo:proposed')
    adopted = SemanticCommitProof('demo:adopted', 'demo:neuro_winner', 1,
        ('resolve',), ('demo:observed-test-fail',), 'demo:episode')
    goal = goals.transition(candidate.goal_id, expected_revision=current.revision,
        status='active', event_id='demo:adopt', proof=adopted)
    authority = CapabilityAuthority(
        secret=b'offline-autonomy-proof-secret-32-bytes!!',
        issuer='offline-proof', audience='sandbox', id_factory=lambda: 'demo:token',
    )
    policy = CapabilityPolicy(CommandPolicy(frozenset(), str(root)), token_verifier=authority.verify)
    sandbox = SandboxWorld(policy=policy)
    cmd = SandboxCommand('demo:cmd', 'demo:action', 'demo:perform',
                         SandboxCommandKind.SIMULATE, {}, 'simulate',
                         'demo:action:once', time.time(), True)
    token = authority.issue(subject_id='eris', capabilities=frozenset({'simulate'}),
                            policy_version='v1', scope={
        'command_ids': ('demo:cmd',), 'action_ids': ('demo:action',),
        'decision_ids': ('demo:perform',), 'idempotency_keys': ('demo:action:once',),
    }, ttl_seconds=60.0)
    bridge = GoalActionBridge(goals, sandbox)
    outcome = await bridge.execute(candidate.goal_id, expected_revision=goal.revision,
        expected_epoch=goal.epoch, command=cmd, capability=token,
        decision=SemanticCommitProof('demo:perform', 'demo:neuro_winner', 1,
                                     ('resolve',), (), 'demo:episode'))
    if str(outcome.status) not in {'simulated', 'succeeded'}:
        raise RuntimeError(f'demo sandbox failed: {outcome.status} {outcome.denial_reason}')
    achieved = goals.transition(candidate.goal_id, expected_revision=goal.revision,
        status='achieved', event_id='demo:outcome', proof=adopted,
        evidence_ref=str(outcome.outcome_id))
    return {
        'demo': 'synthetic_semantic_commit_only',
        'goal_id': achieved.goal_id,
        'goal_state': achieved.status,
        'events': len(goals.events(achieved.goal_id)),
        'action_receipt': bridge.get_action('demo:action')['status'],
        'world_status': str(outcome.status),
        'panel_view': goals.panel_projection(),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, help='optional directory; default temp directory')
    args = p.parse_args()
    if args.output:
        result = asyncio.run(demo(args.output))
    else:
        with tempfile.TemporaryDirectory(prefix='eris-autonomy-proof-') as d:
            result = asyncio.run(demo(Path(d)))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
