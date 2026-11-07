# -*- coding: utf-8 -*-
"""V3 thử nghiệm: ý định Subject -> công cụ sandbox thật -> bằng chứng -> phản tỉnh.

Đây là runtime nghiên cứu có hành động THẬT trong thư mục sandbox, mặc định bật.
Chưa có bằng chứng một mô hình ngôn ngữ tự lập kế hoạch đa nhiệm hay V103 ký
semantic commit; không tự nhận những khả năng chưa được nối vào runtime.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from .agency_v2 import AutonomyRuntimeV2, SubjectChoice, choose_subject_goal, _hash, _string
from .action_bridge import GoalActionBridge
from .goal_events import GoalCandidate, GoalConflict, GoalEventLedger, SemanticCommitProof
from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld
from runtime.world.contracts import SandboxCommand, SandboxCommandKind


def _intention_choices(view: Mapping[str, Any]) -> list[SubjectChoice]:
    """Chọn ứng viên từ Subject; tránh lập lại ý định đã có artifact hoàn chỉnh."""
    pending = list(view.get('pending_intentions') or [])
    choices: list[SubjectChoice] = []
    for item in pending[:24]:
        if not isinstance(item, Mapping):
            continue
        picked = choose_subject_goal({'revision': view.get('revision', 0), 'pending_intentions': [item]})
        if picked:
            choices.append(picked)
    # Nếu Subject chưa chuyển động cơ thành intention, V3 được phép thực hiện
    # một thí nghiệm nội bộ từ chính motive Subject đã hình thành. Không tạo
    # nội dung động cơ ngẫu nhiên hoặc nhận đây là commit Executive/V103.
    if not choices:
        for raw in tuple(view.get('active_motives') or ())[:12]:
            if not isinstance(raw, Mapping):
                continue
            motive_id = _string(raw.get('motive_id'), 110)
            semantic = _string(raw.get('semantic_goal'), 250)
            kind = _string(raw.get('kind'), 80)
            if not (motive_id and semantic and kind):
                continue
            try:
                pressure = max(0., min(1., float(raw.get('pressure') or 0.)))
                epistemic = max(0., min(1., float(raw.get('epistemic_value') or 0.)))
            except (TypeError, ValueError):
                continue
            score = pressure * (0.5 + 0.5 * epistemic)
            if score < 0.3:
                continue
            refs = tuple(_string(x, 120) for x in tuple(raw.get('evidence_refs') or ())[:8])
            choices.append(SubjectChoice('motive:' + motive_id, semantic, kind,
                                         score, max(0, int(view.get('revision', 0) or 0)), refs))
    return sorted(choices, key=lambda i: (-i.score, i.intention_id))


class WorkspacePlanV3:
    """Hai hành động không mô phỏng: tạo sổ mục tiêu và đọc lại để xác thực.

    Bước kế tiếp có thể được thay bằng planner thật sau này. Mã ở đây chỉ ghi
    tóm tắt ý định Subject và chứng cứ phi nhạy cảm, không tự biến một ý nghĩ
    thành quyền thay đổi dữ liệu ngoài sandbox.
    """
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()

    def path(self, goal_id: str) -> str:
        return f'plans/{_hash(goal_id)}.md'

    def content(self, choice: SubjectChoice) -> str:
        refs = '\n'.join('- ' + _string(x, 120) for x in choice.evidence_refs) or '- Chưa có tham chiếu'
        return (
            '# Nhật ký tự chủ của Eris\n\n'
            f'Ý định: {_string(choice.semantic_goal, 250)}\n\n'
            f'Động cơ: {_string(choice.motive_kind, 80)}\n\n'
            f'ID ý định: {_string(choice.intention_id, 120)}\n\n'
            '## Bằng chứng đã biết\n' + refs + '\n\n'
            '## Đánh giá\nChưa có bằng chứng mục tiêu ngữ nghĩa đã hoàn thành.\n'
        )

    def build(self, choice: SubjectChoice, proof: SemanticCommitProof, goal_id: str, step: int) -> SandboxCommand:
        if step not in (1, 2):
            raise ValueError('Chỉ hỗ trợ hai bước có thể kiểm tra')
        key = f'v3:{_hash(goal_id)}:step-{step}'
        kind = SandboxCommandKind.WRITE_FILE if step == 1 else SandboxCommandKind.READ_FILE
        args = {'path': self.path(goal_id)}
        if step == 1:
            args['content'] = self.content(choice)
        else:
            args['max_bytes'] = 16_384
        return SandboxCommand(
            command_id='cmd:' + key, action_id='action:' + key,
            decision_id=proof.commit_id, kind=kind, arguments=args,
            requested_capability=kind.value, idempotency_key=key,
            created_at=time.time(), subject_id='eris', simulate=False,
        )

    def expected_hash(self, choice: SubjectChoice) -> str:
        return hashlib.sha256(self.content(choice).encode('utf-8')).hexdigest()


class AutonomyLoopV3:
    """Thực hiện tối đa một bước mỗi tick; bảo toàn receipt và không replay tool.

    Commit từng bước phải kiểm tra goal revision. Các hành động có side effect
    được claim trong cùng transaction SQLite trước khi dispatch. Nếu crash ở
    giữa, chỉ đối chiếu receipt, KHÔNG tự chạy lại tác dụng phụ không rõ kết quả.
    """
    def __init__(self, ledger: GoalEventLedger, world: Any,
                 capability_provider: Callable[[SandboxCommand], Any], *, workspace: str | Path):
        self.ledger = ledger
        self.world = world
        self.bridge = GoalActionBridge(ledger, world, allow_effects=True)
        self.plan = WorkspacePlanV3(workspace)
        self.capability_provider = capability_provider

    def _proof(self, choice: SubjectChoice, gid: str, operation: str) -> SemanticCommitProof:
        return SemanticCommitProof(
            commit_id=f'v3:{operation}:{gid}', source=('subject_motive_experiment_not_v103' if choice.intention_id.startswith('motive:')
                    else 'subject_intention_experiment_not_v103'),
            identity_revision=0, motive_ids=(choice.motive_kind,), evidence_refs=choice.evidence_refs,
            episode_id=f'v3:episode:{gid}',
        )

    async def cycle(self, view: Mapping[str, Any], *, preempted: Callable[[], bool]) -> dict[str, Any]:
        for choice in _intention_choices(view):
            gid = 'eris:subject:' + _hash(choice.intention_id)
            goal = self.ledger.get(gid)
            if goal and goal.status in {'achieved', 'abandoned', 'failed', 'suspended'}:
                continue
            if goal and goal.payload.get('last_reflection', {}).get('assessment') in {'workspace_verified', 'needs_review'}:
                # Tiếp tục quan sát ý định, không ghi đè workspace ở mỗi tick.
                continue
            if goal is None:
                candidate = GoalCandidate(
                    goal_id=gid, title=choice.semantic_goal,
                    success_criteria='Chờ bằng chứng từ môi trường để đánh giá mục tiêu thực',
                    motive_ids=(choice.motive_kind,), identity_revision=0,
                    evidence_refs=(*choice.evidence_refs, f'initiative:rev:{choice.source_revision}'),
                )
                try:
                    goal = self.ledger.propose(candidate, event_id=f'v3:proposal:{gid}')
                except GoalConflict:
                    goal = self.ledger.get(gid)
            if not goal:
                continue
            if preempted():
                return {'status': 'bi_ngat', 'goal_id': gid}
            if goal.status == 'proposed':
                try:
                    goal = self.ledger.transition(
                        gid, expected_revision=goal.revision, status='active',
                        event_id=f'v3:adopt:{gid}', proof=self._proof(choice, gid, 'adopt'),
                    )
                except GoalConflict:
                    return {'status': 'xung_dot_muc_tieu', 'goal_id': gid}
            if goal.status != 'active':
                continue
            # Tiến độ nằm trong ledger; không dựa vào biến đếm RAM khi restart.
            last = goal.payload.get('last_outcome') or {}
            step = 2 if str(last.get('action_id') or '').endswith((':step-1', ':step-2')) else 1
            proof = self._proof(choice, gid, 'action')
            command = self.plan.build(choice, proof, gid, step)
            prior = self.bridge.get_action(command.action_id)
            if prior is not None:
                if prior.get('status') != 'completed':
                    return {'status': 'can_doi_chieu_receipt', 'goal_id': gid, 'step': step}
                # Nếu task bị preempt sau world.apply nhưng trước attach_outcome,
                # lấy receipt thật để nối bước tiếp theo, không replay tool.
                details = json.loads(prior.get('outcome_json') or '{}')
                outcome_ref = str(details.get('outcome_id') or '')
                outcome_status = str(details.get('status') or '').lower()
                observed = tuple(details.get('observed_changes') or ())
            else:
                if preempted():
                    return {'status': 'bi_ngat', 'goal_id': gid}
                token = self.capability_provider(command)
                outcome = await self.bridge.execute(
                    gid, expected_revision=goal.revision, expected_epoch=goal.epoch,
                    command=command, capability=token, decision=proof,
                )
                outcome_ref = str(getattr(outcome, 'outcome_id', '') or '')
                outcome_status = str(getattr(outcome, 'status', '')).lower()
                observed = tuple(str(x) for x in getattr(outcome, 'observed_changes', ()) or ())
            if preempted():
                return {'status': 'ket_qua_sau_ngat_cho_doi_chieu', 'goal_id': gid, 'step': step}
            if not outcome_ref or not outcome_status:
                return {'status': 'receipt_khong_hop_le', 'goal_id': gid, 'step': step}
            try:
                goal = self.ledger.attach_outcome(
                    gid, expected_revision=goal.revision,
                    event_id=f'v3:outcome:{command.action_id}',
                    outcome_ref=outcome_ref, outcome_status=outcome_status,
                    action_id=command.action_id,
                )
            except GoalConflict:
                return {'status': 'outcome_can_doi_chieu', 'goal_id': gid, 'step': step}
            if outcome_status != 'succeeded':
                self.ledger.record_reflection(
                    gid, expected_revision=goal.revision,
                    event_id=f'v3:reflection:{command.action_id}',
                    assessment='needs_review', evidence_ref=outcome_ref,
                )
                return {'status': 'tool_khong_thanh_cong', 'goal_id': gid, 'step': step,
                        'outcome_status': outcome_status}
            if step == 1:
                return {'status': 'da_tao_workspace', 'goal_id': gid, 'step': 1,
                        'outcome_ref': outcome_ref}
            expected = 'sha256:' + self.plan.expected_hash(choice)
            if expected not in observed or 'truncated:true' in observed:
                self.ledger.record_reflection(
                    gid, expected_revision=goal.revision,
                    event_id=f'v3:reflection:{command.action_id}',
                    assessment='needs_review', evidence_ref=outcome_ref,
                )
                return {'status': 'noi_dung_khong_khop', 'goal_id': gid, 'step': 2,
                        'outcome_ref': outcome_ref}
            goal = self.ledger.record_reflection(
                gid, expected_revision=goal.revision,
                event_id=f'v3:reflection:{command.action_id}',
                assessment='workspace_verified', evidence_ref=outcome_ref,
            )
            # Không đánh dấu achieved: đã xác minh workspace, KHÔNG có nghĩa
            # ý định dài hạn của Eris được giải quyết.
            return {'status': 'da_xac_minh_workspace', 'goal_id': gid, 'step': 2,
                    'goal_status': goal.status, 'outcome_ref': outcome_ref}
        return {'status': 'dang_quan_sat_khong_co_viec_moi'}


class AutonomyRuntimeV3(AutonomyRuntimeV2):
    """Lập lịch ít tốn tài nguyên, không khởi động worker khi mọi goal đã sẵn sàng."""
    def pump(self) -> None:
        if self._future is not None and self._future.done():
            try:
                self.last_result = self._future.result()
            except BaseException as exc:
                self.last_result = {'status': 'loi_worker', 'error_type': type(exc).__name__}
            finally:
                self._future = None
        self._send_panel()
        if bool(getattr(self.host, 'chat_mode', False)):
            return
        if self._future is not None:
            return
        now = self.clock()
        if now - self._last_tick < 2.0:
            return
        self._last_tick = now
        subject = getattr(self.host, 'subject_service', None)
        getter = getattr(subject, 'initiative_view', None)
        if not callable(getter):
            return
        view = getter()
        if not isinstance(view, Mapping):
            return
        choices = _intention_choices(view)
        runnable = []
        for choice in choices:
            gid = 'eris:subject:' + _hash(choice.intention_id)
            goal = self.ledger.get(gid)
            if goal and (goal.status != 'active' and goal.status != 'proposed'):
                continue
            if goal and goal.payload.get('last_reflection', {}).get('assessment') in {'workspace_verified', 'needs_review'}:
                continue
            runnable.append(choice)
        if not runnable:
            self.last_result = {'status': 'khong_co_goal_can_thuc_thi'}
            return
        if self.mode == 'observe':
            self.last_result = {'status': 'quan_sat', 'intention_id': runnable[0].intention_id}
            return
        if self.loop is None or self._executor is None:
            return
        with self._guard:
            generation = self._generation
            # Không lưu ảnh màn hình/chuỗi chat từ các sự kiện wake.
            self._events.clear()
        self._future = self._executor.submit(
            lambda: asyncio.run(self.loop.cycle(dict(view), preempted=lambda: self._stale(generation)))
        )


def ensure_autonomy_v3(host: Any) -> AutonomyRuntimeV3 | None:
    """V3 mặc định bật trong sandbox nghiên cứu; OFF là công tắc dừng."""
    mode = os.getenv('ERIS_AUTONOMY_V3_MODE', 'sandbox').strip().lower()
    existing = getattr(host, '_autonomy_v3', None)
    if mode not in {'sandbox', 'observe'}:
        if existing is not None:
            existing.close()
            host._autonomy_v3 = None
        return None
    if existing is not None and existing.mode == mode:
        return existing
    if existing is not None:
        existing.close()
    ledger = GoalEventLedger(os.getenv('ERIS_AUTONOMY_V3_DB', 'logs/eris_goals_v3.sqlite'))
    loop = None
    if mode == 'sandbox':
        root = Path(os.getenv('ERIS_AUTONOMY_V3_WORKDIR', 'logs/autonomy-v3-sandbox')).resolve()
        root.mkdir(parents=True, exist_ok=True)
        authority = CapabilityAuthority(secret=os.urandom(32), issuer='eris-autonomy-v3', audience='sandbox')
        policy = CapabilityPolicy(CommandPolicy(frozenset(), str(root)), token_verifier=authority.verify)
        world = SandboxWorld(policy=policy)
        def provide(cmd: SandboxCommand):
            return authority.issue(
                subject_id='eris', capabilities=frozenset({'write_file', 'read_file'}),
                policy_version='v1', ttl_seconds=60,
                scope={'command_ids': (cmd.command_id,), 'action_ids': (cmd.action_id,),
                       'decision_ids': (cmd.decision_id,), 'idempotency_keys': (cmd.idempotency_key,),
                       'workspace_prefixes': ('plans',), 'max_output_bytes': 16_384},
            )
        loop = AutonomyLoopV3(ledger, world, provide, workspace=root)
    runtime = AutonomyRuntimeV3(host, mode=mode, ledger=ledger, loop=loop)
    host._autonomy_v3 = runtime
    return runtime
