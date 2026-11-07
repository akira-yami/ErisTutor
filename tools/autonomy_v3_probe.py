# -*- coding: utf-8 -*-
"""Chạy kiểm chứng V3 độc lập: dùng motive minh họa, SandboxWorld THẬT.

Quan trọng: fixture này chỉ kiểm chứng đường runtime + IO; nó KHÔNG phải
bằng chứng Subject/V103/LLM đã tự hình thành motive trong phiên live.
"""
import asyncio
import json
import tempfile
from pathlib import Path

from runtime.autonomy.agency_v3 import AutonomyLoopV3
from runtime.autonomy.goal_events import GoalEventLedger
from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld


def main():
    with tempfile.TemporaryDirectory(prefix='eris-v3-proof-') as temp:
        root = Path(temp)
        authority = CapabilityAuthority(
            secret=b'v3-proof-local-sandbox-signing-key', issuer='eris-v3-probe', audience='sandbox',
        )
        world = SandboxWorld(policy=CapabilityPolicy(
            CommandPolicy(frozenset(), str(root)), token_verifier=authority.verify,
        ))
        def token(command):
            return authority.issue(
                subject_id='eris', capabilities=frozenset({'write_file', 'read_file'}),
                policy_version='v1', ttl_seconds=60,
                scope={'command_ids': [command.command_id], 'action_ids': [command.action_id],
                       'decision_ids': [command.decision_id], 'idempotency_keys': [command.idempotency_key],
                       'workspace_prefixes': ['plans']},
            )
        ledger = GoalEventLedger(root / 'state.sqlite')
        loop = AutonomyLoopV3(ledger, world, token, workspace=root)
        fixture = {'revision': 10, 'pending_intentions': [], 'active_motives': [
            {'motive_id': 'probe-curiosity', 'semantic_goal': 'Tự khám phá một giả thuyết',
             'kind': 'explore', 'pressure': .9, 'epistemic_value': .9,
             'evidence_refs': ['probe:source:offline']},
        ]}
        steps = [asyncio.run(loop.cycle(fixture, preempted=lambda: False)) for _ in range(3)]
        goals = ledger.panel_projection()
        files = sorted(str(p.relative_to(root)) for p in (root / 'plans').glob('*.md'))
        with ledger._db() as conn:
            actions = conn.execute('SELECT count(*) FROM autonomy_actions').fetchone()[0]
        result = {'kind': 'offline_fixture_plus_real_sandbox', 'steps': steps,
                  'goals': goals, 'sandbox_files': files, 'action_count': actions,
                  'confirmed': actions == 2 and len(files) == 1 and goals[0]['last_reflection'] == 'workspace_verified'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        assert result['confirmed']


if __name__ == '__main__':
    main()
