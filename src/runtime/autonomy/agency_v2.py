# -*- coding: utf-8 -*-
"""V2: Vòng tự chủ gắn với Subject, không sở hữu quyền phát ngôn.

Lưu ý phạm vi:
- Chủ động chọn ý định *đang tồn tại thật* trong SubjectService.
- Quyết định chọn goal ở đây chỉ cấp quyền cho phép THỬ NGHIỆM trong sandbox,
  không đại diện cho semantic winner của V103/Root hoặc quyền trên máy thật.
- Hành động qua SandboxWorld với capability thực; kết quả dùng làm evidence.
- Không lưu ảnh màn hình; chỉ nhận sự kiện đã chuyển thành metadata ngữ nghĩa.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Protocol

from .goal_events import GoalCandidate, GoalConflict, GoalEventLedger, SemanticCommitProof
from .action_bridge import GoalActionBridge
from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld
from runtime.world.contracts import SandboxCommand, SandboxCommandKind


def _string(value: Any, limit: int = 250) -> str:
    return " ".join(str(value or "").split())[:limit]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class SubjectChoice:
    """Lựa chọn của bộ thí nghiệm dựa trên ý định Subject hiện có."""
    intention_id: str
    semantic_goal: str
    motive_kind: str
    score: float
    source_revision: int
    evidence_refs: tuple[str, ...]


def choose_subject_goal(view: Mapping[str, Any]) -> SubjectChoice | None:
    """Arbitration tái lập được, không suy bịa ý định hay nhận quyền nói.

    Dùng áp lực và cam kết được Subject tính sẵn; tie-break bằng ID để test ổn định.
    Ngưỡng chỉ giới hạn tốn tài nguyên thí nghiệm, không điều khiển cá tính nói chuyện.
    """
    choices: list[SubjectChoice] = []
    try:
        revision = max(0, int(view.get("revision", 0)))
    except (ValueError, TypeError):
        revision = 0
    for raw in tuple(view.get("pending_intentions") or ())[:16]:
        if not isinstance(raw, Mapping) or str(raw.get("status", "pending")) != "pending":
            continue
        ident = _string(raw.get("intention_id"), 120)
        goal = _string(raw.get("semantic_goal"), 250)
        motive = _string(raw.get("motive_kind"), 80)
        if not ident or not goal or not motive:
            continue
        try:
            pressure = max(0.0, min(1.0, float(raw.get("pressure") or 0)))
            commitment = max(0.0, min(1.0, float(raw.get("commitment_strength") or 0)))
        except (TypeError, ValueError):
            continue
        score = pressure * (0.5 + 0.5 * commitment)
        if score < 0.3:
            continue
        refs = tuple(_string(r, 140) for r in tuple(raw.get("evidence_refs") or ())[:8] if str(r).strip())
        choices.append(SubjectChoice(ident, goal, motive, score, revision, refs))
    return sorted(choices, key=lambda c: (-c.score, c.intention_id))[0] if choices else None


class PlanPort(Protocol):
    """Port cho planner được cấp quyền riêng; không cho Root tự phát lệnh thô."""
    def build(self, choice: SubjectChoice, proof: SemanticCommitProof, goal_id: str) -> SandboxCommand: ...


class SimulationPlan:
    """Planner mặc định: một hành động mô phỏng có outcome thật từ SandboxWorld."""
    def build(self, choice: SubjectChoice, proof: SemanticCommitProof, goal_id: str) -> SandboxCommand:
        key = f"v2:{_hash(goal_id)}:step-1"
        return SandboxCommand(
            command_id=f"cmd:{key}", action_id=f"action:{key}",
            decision_id=proof.commit_id, kind=SandboxCommandKind.SIMULATE,
            arguments={}, requested_capability="simulate", idempotency_key=key,
            created_at=time.time(), simulate=True, subject_id="eris",
        )


class AutonomyLoopV2:
    """Một vòng nhỏ có state bền: Subject → Goal → Plan → Act → Receipt → Reflect.

    Không được gọi từ main thread đồng bộ nếu planner/World thực thi chậm.
    Stage/Root/SpeechAuthority giữ nguyên quyền công khai.
    """
    def __init__(self, ledger: GoalEventLedger, world: Any, capability_provider: Callable[[SandboxCommand], Any],
                 *, plan: PlanPort | None = None, allow_effects: bool = False):
        self.ledger = ledger
        self.bridge = GoalActionBridge(ledger, world, allow_effects=allow_effects)
        self.capability_provider = capability_provider
        self.plan = plan or SimulationPlan()

    async def cycle(self, view: Mapping[str, Any], *, preempted: Callable[[], bool]) -> dict[str, Any]:
        choice = choose_subject_goal(view)
        if choice is None:
            return {"status": "khong_co_y_dinh_phu_hop"}
        gid = "eris:subject:" + _hash(choice.intention_id)
        existing = self.ledger.get(gid)
        if existing is None:
            # Thành công ở đây chỉ là xác minh THAO TÁC THỬ trong sandbox;
            # không đồng nghĩa hoàn thành mục tiêu thực tế của Subject.
            candidate = GoalCandidate(
                gid, "Thử nghiệm ý định: " + choice.semantic_goal,
                "Kiểm chứng được một bước thí nghiệm trong sandbox",
                (choice.motive_kind,), 0,
                (*choice.evidence_refs, f"subject:initiative_revision:{choice.source_revision}"),
            )
            try:
                existing = self.ledger.propose(candidate, event_id=f"v2:propose:{gid}")
            except GoalConflict:
                existing = self.ledger.get(gid)
        if not existing:
            return {"status": "loi_goal"}
        if preempted():
            return {"status": "bi_ngat", "goal_id": gid}
        # Revision của initiative KHÔNG PHẢI revision của Identity.
        # Giá trị 0 = chưa có chứng thực Identity; không giả nhận V103.
        adoption = SemanticCommitProof(
            commit_id=f"v2:choice:{gid}", source="subject_sandbox_pilot_not_v103",
            identity_revision=0, motive_ids=(choice.motive_kind,),
            evidence_refs=choice.evidence_refs, episode_id=f"v2:episode:{gid}",
        )
        if existing.status == "proposed":
            try:
                existing = self.ledger.transition(
                    gid, expected_revision=existing.revision, status="active",
                    event_id=f"v2:adopt:{gid}", proof=adoption,
                )
            except GoalConflict:
                return {"status": "xung_dot_goal", "goal_id": gid}
        if existing.status != "active":
            return {"status": existing.status, "goal_id": gid}
        proof = SemanticCommitProof(
            commit_id=f"v2:action_decision:{gid}", source="sandbox_plan_v2",
            identity_revision=0, motive_ids=(choice.motive_kind,),
            evidence_refs=choice.evidence_refs, episode_id=f"v2:episode:{gid}",
        )
        command = self.plan.build(choice, proof, gid)
        # Một action đã có receipt tuyệt đối không replay. Khi process crash,
        # in-flight/unknown phải reconciliate, không tự lặp side-effect.
        past = self.bridge.get_action(command.action_id)
        if past is not None:
            return {"status": "da_co_receipt_can_doi_chieu", "goal_id": gid, "action_status": past["status"]}
        if preempted():
            return {"status": "bi_ngat", "goal_id": gid}
        token = self.capability_provider(command)
        outcome = await self.bridge.execute(
            gid, expected_revision=existing.revision, expected_epoch=existing.epoch,
            command=command, capability=token, decision=proof,
        )
        evidence = str(getattr(outcome, "outcome_id", "") or "")
        status = str(getattr(outcome, "status", "")).lower()
        result = {"status": "da_thuc_thi", "goal_id": gid, "outcome_ref": evidence,
                  "outcome_status": status}
        if preempted():
            # Outcome đã có thật và receipt vẫn còn, nhưng không còn quyền
            # đóng goal bằng quyết định của generation cũ.
            result["status"] = "ket_qua_sau_ngat_cho_doi_chieu"
            return result
        try:
            current = self.ledger.attach_outcome(
                gid, expected_revision=existing.revision,
                event_id=f"v2:outcome:{command.action_id}",
                outcome_ref=evidence, outcome_status=status,
                action_id=command.action_id,
            )
        except (GoalConflict, ValueError):
            result["status"] = "outcome_can_doi_chieu"
            return result
        if status in {"simulated", "succeeded"} and evidence and command.kind == SandboxCommandKind.SIMULATE:
            # Chỉ đánh dấu đạt tiêu chí *probe sandbox*, không tuyên bố xong
            # mục tiêu ngữ nghĩa thật của Eris.
            try:
                result["goal_status"] = self.ledger.transition(
                    gid, expected_revision=current.revision, status="achieved",
                    event_id=f"v2:probe_verified:{gid}", proof=proof,
                    evidence_ref=evidence,
                ).status
            except GoalConflict:
                result["goal_status"] = "khong_commit_do_state_da_doi"
        else:
            result["goal_status"] = "dang_cho_danh_gia_thuc_te"
        return result


class AutonomyRuntimeV2:
    """Bộ nối vào main loop; tối đa một background job, không blocking chat.

    `pump` không gọi model hay World. Chỉ snapshot Subject metadata rồi gửi worker.
    `preempt` vô hiệu hóa commit cuối của generation cũ, không giả vờ rollback tool.
    """
    def __init__(self, host: Any, *, mode: str, ledger: GoalEventLedger | None = None,
                 loop: AutonomyLoopV2 | None = None, clock=time.monotonic):
        if mode not in {"observe", "sandbox"}:
            raise ValueError("mode phải là observe hoặc sandbox")
        self.host = host
        self.mode = mode
        self.clock = clock
        self.ledger = ledger or GoalEventLedger(os.getenv("ERIS_AUTONOMY_V2_DB", "logs/eris_goals_v2.sqlite"))
        self.loop = loop
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Eris-Autonomy-V2") if mode == "sandbox" else None
        self._future: Future | None = None
        self._generation = 0
        self._guard = threading.Lock()
        self._last_tick = 0.0
        self._last_panel = "[]"
        self._events: list[str] = []
        self.last_result: dict[str, Any] = {}

    def note_event(self, reason: str) -> None:
        # Không chép screenshot hay văn bản người dùng vào hàng đợi.
        with self._guard:
            if len(self._events) >= 20:
                self._events.pop(0)
            self._events.append(_string(reason, 80))

    def preempt(self) -> None:
        with self._guard:
            self._generation += 1
            if self._future is not None and not self._future.done():
                self._future.cancel()  # nếu đã dispatch thì chỉ hủy được hợp tác

    def _stale(self, generation: int) -> bool:
        with self._guard:
            return generation != self._generation or bool(getattr(self.host, "chat_mode", False))

    def _send_panel(self) -> None:
        # Projection chỉ phát khi state đổi, không phát public speech.
        panel = self.ledger.panel_projection()[:12]
        packed = json.dumps(panel, ensure_ascii=False, sort_keys=True)
        if packed == self._last_panel:
            return
        transport = getattr(self.host, "transport", None)
        send = getattr(transport, "send_json", None)
        if callable(send):
            send({"parse_mode": "autonomy_goals", "goals": panel, "show": bool(panel and self._last_panel == "[]" and not getattr(self.host, "chat_mode", False))})
            self._last_panel = packed

    def pump(self) -> None:
        # Mọi lỗi của autonomy thí nghiệm không được phá lượt chat production.
        if self._future is not None and self._future.done():
            try:
                self.last_result = self._future.result()
            except BaseException as exc:
                self.last_result = {"status": "loi_background", "error_type": type(exc).__name__}
            finally:
                self._future = None
        self._send_panel()
        if bool(getattr(self.host, "chat_mode", False)):
            return
        now = self.clock()
        # Nhịp bảo vệ tài nguyên: không can thiệp quy tắc nói/Identity.
        if now - self._last_tick < 3.0:
            return
        self._last_tick = now
        subject = getattr(self.host, "subject_service", None)
        view_fn = getattr(subject, "initiative_view", None)
        if not callable(view_fn):
            return
        view = view_fn()
        if not isinstance(view, Mapping):
            return
        choice = choose_subject_goal(view)
        if choice is None:
            return
        if self.mode == "observe":
            self.last_result = {"status": "quan_sat", "intention_id": choice.intention_id}
            return
        if self._future is not None or self.loop is None or self._executor is None:
            return
        with self._guard:
            generation = self._generation
            self._events.clear()
        self._future = self._executor.submit(
            lambda: asyncio.run(self.loop.cycle(dict(view), preempted=lambda: self._stale(generation)))
        )

    def close(self) -> None:
        self.preempt()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)


def ensure_autonomy_v2(host: Any) -> AutonomyRuntimeV2 | None:
    """Opt-in: không tạo quyền chạy command từ biến môi trường đơn thuần."""
    mode = os.getenv("ERIS_AUTONOMY_V2_MODE", "off").strip().lower()
    runtime = getattr(host, "_autonomy_v2", None)
    if mode not in {"observe", "sandbox"}:
        # Kill switch phải thực sự dừng lập lịch mới khi đổi cờ lúc đang chạy.
        if runtime is not None:
            runtime.close()
            host._autonomy_v2 = None
        return None
    if runtime is not None and runtime.mode != mode:
        runtime.close()
        runtime = None
    if runtime is not None:
        return runtime
    # Mode sandbox production chỉ thực hiện SIMULATE; token được cấp scope hẹp.
    ledger = GoalEventLedger(os.getenv("ERIS_AUTONOMY_V2_DB", "logs/eris_goals_v2.sqlite"))
    loop = None
    if mode == "sandbox":
        authority = CapabilityAuthority(
            secret=os.urandom(32), issuer="eris-autonomy-v2-local", audience="sandbox",
        )
        policy = CapabilityPolicy(CommandPolicy(frozenset(), str(Path(os.getenv("ERIS_AUTONOMY_V2_WORKDIR", "logs/autonomy-v2-sandbox")).resolve())),
                                  token_verifier=authority.verify)
        world = SandboxWorld(policy=policy)
        def token_for(command: SandboxCommand):
            return authority.issue(subject_id="eris", capabilities=frozenset({"simulate"}),
                policy_version="v1", scope={
                    "command_ids": (command.command_id,), "action_ids": (command.action_id,),
                    "decision_ids": (command.decision_id,), "idempotency_keys": (command.idempotency_key,),
                }, ttl_seconds=60.0)
        loop = AutonomyLoopV2(ledger, world, token_for)
    runtime = AutonomyRuntimeV2(host, mode=mode, ledger=ledger, loop=loop)
    host._autonomy_v2 = runtime
    return runtime
