"""Opt-in fenced command dispatch for autonomous goals.

Requires a committed goal, caller-supplied semantic action proof, a real
SandboxWorld-compatible port and a scoped CapabilityToken. No extra LLM,
TTS or side-effect authority is created by this bridge.

Crash during dispatch yields UNKNOWN (not automatic retry). This avoids
re-executing potentially irreversible effects without a sandbox receipt.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .goal_events import GoalConflict, GoalEventLedger, SemanticCommitProof, _canonical
from runtime.world.contracts import SandboxCommand, CapabilityToken, SandboxCommandKind


class GoalActionBridge:
    def __init__(self, ledger: GoalEventLedger, world, *, allow_effects: bool = False):
        self.ledger = ledger
        self.world = world
        self.allow_effects = allow_effects

    async def execute(
        self, goal_id: str, *, expected_revision: int, expected_epoch: int,
        command: SandboxCommand, capability: CapabilityToken, decision: SemanticCommitProof,
    ) -> Any:
        """One dispatch per command/action id; no cross-database atomicity claim.

        `decision` is provenance supplied by the already-authoritative winner.
        For non-simulation effects, caller must explicitly opt in to the world
        boundary and the world must still authorize signed capabilities.
        """
        if not isinstance(command, SandboxCommand) or not isinstance(capability, CapabilityToken):
            raise TypeError('typed command and capability required')
        if not self.allow_effects and command.kind != SandboxCommandKind.SIMULATE:
            raise PermissionError('sandbox effects disabled in autonomous experiment')
        if command.decision_id != decision.commit_id:
            raise GoalConflict('command must reference its action semantic commit')
        command_hash = hashlib.sha256(_canonical({
            'action_id': command.action_id, 'command_id': command.command_id,
            'decision_id': command.decision_id,
            'kind': str(command.kind), 'args': dict(command.arguments),
            'capability': command.requested_capability, 'key': command.idempotency_key,
            'simulate': command.simulate,
        }).encode()).hexdigest()
        now = self.ledger.clock()

        def claim(conn):
            goal = self.ledger._snap(conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (goal_id,)).fetchone())
            if not goal or goal.status != 'active' or goal.revision != expected_revision or goal.epoch != expected_epoch:
                raise GoalConflict('stale or inactive goal cannot dispatch tool')
            adopted = goal.payload.get('adoption_proof', {})
            if not adopted or decision.episode_id != adopted.get('episode_id') or decision.identity_revision < adopted.get('identity_revision', 0):
                raise GoalConflict('action commit has no compatible adopted episode')
            row = conn.execute('SELECT * FROM autonomy_actions WHERE action_id=?', (command.action_id,)).fetchone()
            if row:
                if row['command_hash'] != command_hash or row['goal_id'] != goal_id:
                    raise GoalConflict('action id reused with different command')
                # Even completed actions should not be resubmitted here;
                # consult receipt via get_action instead.
                raise GoalConflict('action already dispatched; reconcile receipt, do not re-execute')
            conn.execute('INSERT INTO autonomy_actions(action_id,goal_id,goal_revision,goal_epoch,status,command_hash,command_key,outcome_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (command.action_id, goal_id, goal.revision, goal.epoch, 'in_flight', command_hash,
                 command.idempotency_key, '{}', now))
        self.ledger._transaction(claim)
        try:
            outcome = await self.world.apply(command, capability)
        except BaseException as exc:
            # Unknown side effect: do not auto-retry under same or new action id.
            self._write_receipt(command.action_id, 'unknown', {'error_type': type(exc).__name__})
            raise
        self._write_receipt(command.action_id, 'completed', {
            'outcome_id': str(getattr(outcome, 'outcome_id', '')),
            'status': str(getattr(outcome, 'status', '')),
            'denial_reason': str(getattr(outcome, 'denial_reason', '') or ''),
            'observed_changes': list(getattr(outcome, 'observed_changes', ()) or ())[:16],
        })
        return outcome

    def _write_receipt(self, action_id: str, state: str, payload: dict) -> None:
        def write(conn):
            conn.execute('UPDATE autonomy_actions SET status=?,outcome_json=?,updated_at=? WHERE action_id=? AND status=?',
                         (state, _canonical(payload), self.ledger.clock(), action_id, 'in_flight'))
        self.ledger._transaction(write)

    def get_action(self, action_id: str) -> dict | None:
        with self.ledger._db() as conn:
            row = conn.execute('SELECT * FROM autonomy_actions WHERE action_id=?', (action_id,)).fetchone()
            return dict(row) if row else None

    def unknown_actions(self) -> tuple[str, ...]:
        """Includes in-flight rows after process restart; requires reconciliation."""
        with self.ledger._db() as conn:
            return tuple(row['action_id'] for row in conn.execute(
                "SELECT action_id FROM autonomy_actions WHERE status IN ('unknown','in_flight') ORDER BY updated_at"
            ))
