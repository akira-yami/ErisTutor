# -*- coding: utf-8 -*-
"""Năng lực nhận thức nội bộ có thực thi và kiểm chứng cho Eris.

Module này biến các câu kiểu "tôi sẽ kiểm lại" thành một action đọc nội bộ thật:

    chọn capability -> kiểm tra dependency -> chạy handler -> gom evidence
    -> verifier xác nhận -> mới được phép nói kết quả.

Các capability ở đây chỉ đọc state nội bộ. Chúng không sửa memory, không chỉnh
quan hệ và không ghi đè claim. Một hệ thống tự soi mình đã đủ dễ rối rồi, không
cần vừa soi vừa tự phẫu thuật trong cùng một nhịp.
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import uuid
import weakref
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from actions.contracts import ActionExecutionResult, ActionExecutionStatus

INTERNAL_COGNITIVE_VERSION = "internal_cognitive.v1.0"


class CognitivePermissionMode(str, Enum):
    READ_ONLY_AUTO = "read_only_auto"
    REQUIRE_APPROVAL = "require_approval"


class CognitiveActionState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    FAILED_VERIFICATION = "failed_verification"
    CANCELLED = "cancelled"

from runtime.diagnostic_logging import safe_log


@dataclass(frozen=True, slots=True)
class CognitiveCapabilityContract:
    capability_id: str
    public_name_vi: str
    description_vi: str
    handler_id: str
    required_sources: tuple[str, ...]
    permission_mode: CognitivePermissionMode = CognitivePermissionMode.READ_ONLY_AUTO
    timeout_seconds: float = 1.5
    reversible: bool = True
    side_effects: tuple[str, ...] = ()
    verification_handler: str = "evidence_bundle"


@dataclass(frozen=True, slots=True)
class CognitiveEvidence:
    evidence_id: str
    source: str
    summary: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "summary": self.summary,
            "data": dict(self.data),
        }


@dataclass(slots=True)
class CognitiveActionResult:
    action_id: str
    capability_id: str
    state: CognitiveActionState
    public_text: str
    started_at: float
    completed_at: float
    executed: bool = False
    verified: bool = False
    evidence: tuple[CognitiveEvidence, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    error_kind: str = ""
    error_message: str = ""

    @property
    def success(self) -> bool:
        return self.state == CognitiveActionState.SUCCEEDED and self.executed and self.verified

    def to_action_result(self, *, action_input: str = "") -> ActionExecutionResult:
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        payload = {
            key: _stringify(value)
            for key, value in self.data.items()
            if value is not None
        }
        payload.update({
            "cognitive_action_id": self.action_id,
            "cognitive_state": self.state.value,
            "executed": str(bool(self.executed)).lower(),
            "verified": str(bool(self.verified)).lower(),
            "cognitive_version": INTERNAL_COGNITIVE_VERSION,
        })
        return ActionExecutionResult(
            action_name=self.capability_id,
            action_input=action_input,
            success=self.success,
            message=self.public_text or self.error_message or "Năng lực nhận thức nội bộ chưa chạy được.",
            data=payload,
            status=ActionExecutionStatus.OBSERVED if self.success else ActionExecutionStatus.FAILED,
            dispatched=bool(self.executed),
            observed=bool(self.verified),
            evidence=evidence_ids,
            error_kind=self.error_kind,
            observer_reason="cognitive_evidence_verified" if self.verified else "cognitive_verification_failed",
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        payload["success"] = self.success
        return payload


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:2000]
    except Exception:
        return str(value)[:2000]


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn").replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9_:=./\\\s-]", " ", text)).strip()


def _token_set(value: str) -> set[str]:
    stop = {"toi", "minh", "em", "ban", "la", "thi", "ma", "cai", "do", "nay", "roi", "se", "dang"}
    return {token for token in _norm(value).split() if len(token) > 1 and token not in stop and token not in {"khong", "chua"}}


def _has_negation(value: str) -> bool:
    tokens = set(_norm(value).split())
    return bool(tokens & {"khong", "chua", "chang", "chẳng", "no", "not"})


COGNITIVE_CAPABILITY_CONTRACTS: dict[str, CognitiveCapabilityContract] = {
    "review_discourse_claim": CognitiveCapabilityContract(
        capability_id="review_discourse_claim",
        public_name_vi="kiểm lại claim hội thoại",
        description_vi="đọc claim public đang bị user sửa và chỉ ra Eris đã gán nghĩa gì",
        handler_id="internal_cognitive.review_discourse_claim",
        required_sources=("public_turn_ledger",),
    ),
    "inspect_unresolved_questions": CognitiveCapabilityContract(
        capability_id="inspect_unresolved_questions",
        public_name_vi="xem câu hỏi còn bỏ ngỏ",
        description_vi="đọc discourse focus để tìm câu hỏi hoặc correction chưa được đóng",
        handler_id="internal_cognitive.inspect_unresolved_questions",
        required_sources=("public_turn_ledger",),
    ),
    "inspect_memory_candidates": CognitiveCapabilityContract(
        capability_id="inspect_memory_candidates",
        public_name_vi="kiểm tra mảnh ký ức chưa xử lý",
        description_vi="đếm và phân loại memory candidate chưa consolidation; không coi candidate là fact",
        handler_id="internal_cognitive.inspect_memory_candidates",
        required_sources=("memory_kernel.core.memory_candidate",),
    ),
    "compare_recent_turns": CognitiveCapabilityContract(
        capability_id="compare_recent_turns",
        public_name_vi="so sánh các lượt nói gần đây",
        description_vi="so sánh câu public gần nhất để phát hiện lặp ý hoặc đổi nghĩa",
        handler_id="internal_cognitive.compare_recent_turns",
        required_sources=("public_turn_ledger",),
    ),
    "inspect_social_state": CognitiveCapabilityContract(
        capability_id="inspect_social_state",
        public_name_vi="xem trạng thái xã hội hiện tại",
        description_vi="đọc social state đã được runtime tính, không suy đoán cảm xúc user",
        handler_id="internal_cognitive.inspect_social_state",
        required_sources=("ai_state_reducer",),
    ),
    "detect_internal_contradictions": CognitiveCapabilityContract(
        capability_id="detect_internal_contradictions",
        public_name_vi="tìm mâu thuẫn trong lời Eris",
        description_vi="đối chiếu các claim public gần đây và báo cặp phủ định có nội dung trùng",
        handler_id="internal_cognitive.detect_internal_contradictions",
        required_sources=("public_turn_ledger",),
    ),
    "summarize_open_loops": CognitiveCapabilityContract(
        capability_id="summarize_open_loops",
        public_name_vi="tổng kết việc nhận thức còn dang dở",
        description_vi="gom correction, câu hỏi bỏ ngỏ và memory candidate thành danh sách open loop có evidence",
        handler_id="internal_cognitive.summarize_open_loops",
        required_sources=("ai_state_reducer", "memory_kernel"),
    ),
    "inspect_capability_health": CognitiveCapabilityContract(
        capability_id="inspect_capability_health",
        public_name_vi="kiểm tra sức khỏe năng lực runtime",
        description_vi="đọc snapshot capability đang available/degraded/unavailable",
        handler_id="internal_cognitive.inspect_capability_health",
        required_sources=("capability_runtime_snapshot",),
    ),
}

COGNITIVE_CAPABILITY_IDS = tuple(COGNITIVE_CAPABILITY_CONTRACTS)


class InternalCognitiveCapabilityRuntime:
    """Executor read-only cho khả năng tự kiểm tra của Eris."""

    def __init__(self, owner: Any, *, logger: Any | None = None, max_audit: int = 80) -> None:
        try:
            self._owner_ref = weakref.ref(owner)
        except TypeError:
            self._owner_ref = lambda: owner
        self.logger = logger
        self._lock = threading.RLock()
        self._audit: deque[CognitiveActionResult] = deque(maxlen=max(20, int(max_audit)))
        self._handlers: dict[str, Callable[[str], tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]]] = {
            "review_discourse_claim": self._review_discourse_claim,
            "inspect_unresolved_questions": self._inspect_unresolved_questions,
            "inspect_memory_candidates": self._inspect_memory_candidates,
            "compare_recent_turns": self._compare_recent_turns,
            "inspect_social_state": self._inspect_social_state,
            "detect_internal_contradictions": self._detect_internal_contradictions,
            "summarize_open_loops": self._summarize_open_loops,
            "inspect_capability_health": self._inspect_capability_health,
        }

    @property
    def owner(self) -> Any | None:
        try:
            return self._owner_ref()
        except (ReferenceError, TypeError):
            return None

    def contracts(self) -> tuple[CognitiveCapabilityContract, ...]:
        return tuple(COGNITIVE_CAPABILITY_CONTRACTS.values())

    def availability(self, capability_id: str) -> tuple[bool, str]:
        cap = str(capability_id or "").strip()
        owner = self.owner
        if owner is None:
            return False, "runtime_owner_unavailable"
        if cap not in self._handlers:
            return False, "cognitive_handler_missing"
        if cap in {"review_discourse_claim", "detect_internal_contradictions"}:
            if getattr(owner, "public_turn_ledger", None) is None:
                return False, "public_turn_ledger_unavailable"
        if cap in {"inspect_unresolved_questions", "inspect_social_state"}:
            if getattr(owner, "ai_state_reducer", None) is None:
                return False, "ai_state_reducer_unavailable"
        if cap == "inspect_memory_candidates":
            if self._memory_candidate_store() is None:
                return False, "memory_candidate_store_unavailable"
        if cap == "compare_recent_turns" and getattr(owner, "public_turn_ledger", None) is None:
            return False, "public_turn_ledger_unavailable"
        return True, "cognitive_handler_ready"

    def availability_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            cap: {"available": available, "reason": reason, "source": "internal_cognitive_runtime"}
            for cap in COGNITIVE_CAPABILITY_IDS
            for available, reason in (self.availability(cap),)
        }

    def execute(self, capability_id: str, action_input: str = "") -> CognitiveActionResult:
        cap = str(capability_id or "").strip()
        action_id = f"cog_{uuid.uuid4().hex[:12]}"
        started = time.time()
        available, reason = self.availability(cap)
        if not available:
            return self._finish(CognitiveActionResult(
                action_id=action_id,
                capability_id=cap,
                state=CognitiveActionState.FAILED,
                public_text="Năng lực tự kiểm tra này hiện chưa có nguồn dữ liệu sống để chạy.",
                started_at=started,
                completed_at=time.time(),
                error_kind="CapabilityUnavailable",
                error_message=reason,
                data={"availability_reason": reason},
            ))

        handler = self._handlers[cap]
        try:
            text, data, evidence = handler(str(action_input or ""))
            verified = self._verify(cap, data=data, evidence=evidence)
            state = CognitiveActionState.SUCCEEDED if verified else CognitiveActionState.FAILED_VERIFICATION
            result = CognitiveActionResult(
                action_id=action_id,
                capability_id=cap,
                state=state,
                public_text=text if verified else "Tôi đã chạy kiểm tra nhưng chưa có evidence đủ để xác nhận kết quả.",
                started_at=started,
                completed_at=time.time(),
                executed=True,
                verified=verified,
                evidence=evidence,
                data=data,
                error_kind="" if verified else "VerificationFailed",
            )
        except Exception as exc:
            result = CognitiveActionResult(
                action_id=action_id,
                capability_id=cap,
                state=CognitiveActionState.FAILED,
                public_text="Tôi không hoàn tất được lần tự kiểm tra này.",
                started_at=started,
                completed_at=time.time(),
                executed=True,
                verified=False,
                error_kind=type(exc).__name__,
                error_message=_clean(exc, 220),
            )
        return self._finish(result)


    def audit_snapshot(self, limit: int = 20) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(item.to_dict() for item in list(self._audit)[-max(1, int(limit)):])

    def _finish(self, result: CognitiveActionResult) -> CognitiveActionResult:
        with self._lock:
            self._audit.append(result)
        if self.logger is not None:
            try:
                self.logger.info(
                    "[internal-cognitive] capability=%s state=%s executed=%s verified=%s evidence=%s",
                    result.capability_id, result.state.value, result.executed, result.verified,
                    len(result.evidence),
                )
            except Exception as exc:
                safe_log(self.logger, "debug", "[internal-cognitive] result log failed: %s", exc)
        return result

    @staticmethod
    def _verify(capability_id: str, *, data: Mapping[str, Any], evidence: Sequence[CognitiveEvidence]) -> bool:
        if not evidence:
            return False
        if any(not item.evidence_id or not item.source for item in evidence):
            return False
        return bool(data.get("verification_key") == capability_id)

    def _mind_state(self) -> Any | None:
        owner = self.owner
        reducer = getattr(owner, "ai_state_reducer", None) if owner is not None else None
        snapshot = getattr(reducer, "snapshot", None)
        if not callable(snapshot):
            return None
        try:
            return snapshot()
        except Exception as exc:
            safe_log(self.logger, "debug", "[internal-cognitive] snapshot failed: %s", exc)
            return None

    def _memory_candidate_store(self) -> Any | None:
        owner = self.owner
        if owner is None:
            return None
        kernel = getattr(owner, "memory_kernel", None)
        core = getattr(kernel, "core", None) if kernel is not None else None
        store = getattr(core, "memory_candidate", None) if core is not None else None
        if store is not None:
            return store
        memory_core = getattr(owner, "memory_core", None)
        return getattr(memory_core, "memory_candidate", None)

    def _review_discourse_claim(self, action_input: str) -> tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]:
        del action_input
        turns = [turn for turn in self._recent_public_turns(20) if _clean(getattr(turn, "text", ""))]
        if not turns:
            evidence = (CognitiveEvidence("public_turns:empty", "public_turn_ledger", "ledger không có câu public"),)
            return (
                "Tôi đã kiểm tra. Hiện không có câu public nào đủ cụ thể để rà lại.",
                {"verification_key": "review_discourse_claim", "found": False},
                evidence,
            )
        turn = turns[-1]
        proposition = _clean(getattr(turn, "text", ""), 260)
        state = self._mind_state()
        observation = dict(getattr(state, "latest_observation", {}) or {}) if state is not None else {}
        fault = float(observation.get("assistant_fault_probability", 0.0) or 0.0)
        repair = bool(observation.get("repair_needed", False))
        status = "needs_review" if repair or fault >= 0.50 else "active"
        text = f"Tôi đã kiểm lại câu public gần nhất: “{proposition}”."
        if status == "needs_review":
            text += " Observer hậu kỳ đánh dấu câu này có khả năng cần sửa."
        evidence = (CognitiveEvidence(
            f"turn_id:{getattr(turn, 'turn_id', 'unknown')}",
            "public_turn_ledger",
            proposition,
            {"status": status, "assistant_fault_probability": round(fault, 4)},
        ),)
        return text, {
            "verification_key": "review_discourse_claim",
            "found": True,
            "turn_id": getattr(turn, "turn_id", ""),
            "claim_status": status,
            "claim_text": proposition,
            "assistant_fault_probability": fault,
        }, evidence

    def _inspect_unresolved_questions(self, action_input: str) -> tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]:
        del action_input
        state = self._mind_state()
        observation = dict(getattr(state, "latest_observation", {}) or {}) if state is not None else {}
        open_loop = _clean(observation.get("open_loop", ""), 260)
        if open_loop:
            text = f"Điều đang bỏ ngỏ là: “{open_loop}”."
            summary = open_loop
        else:
            text = "Tôi đã kiểm tra. State hiện không đánh dấu câu hỏi nào còn bỏ ngỏ."
            summary = "none"
        evidence = (CognitiveEvidence(
            f"mind_state:turn:{int(getattr(state, 'last_applied_turn_seq', 0) or 0)}",
            "ai_state_reducer.latest_observation",
            summary,
        ),)
        return text, {
            "verification_key": "inspect_unresolved_questions",
            "unresolved_question": open_loop,
        }, evidence

    def _inspect_memory_candidates(self, action_input: str) -> tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]:
        store = self._memory_candidate_store()
        items = list(store.load())
        active = [item for item in items if str(getattr(item, "extraction_status", "")) in {"unresolved", "partial", "failed"}]
        by_status: dict[str, int] = {}
        for item in active:
            status = str(getattr(item, "extraction_status", "unresolved"))
            by_status[status] = by_status.get(status, 0) + 1
        # Không surface raw memory trong autonomous/public mode. User hỏi summary
        # thì chỉ trả count/status; candidate chưa được phép đóng vai fact.
        if not active:
            text = "Tôi đã kiểm tra. Hiện không có ký ức nào đang mắc lại."
        else:
            # Public surface nói bằng ngôn ngữ của Eris; status kỹ thuật vẫn nằm
            # trong evidence/meta cho panel debug. Không đọc ``failed/candidate``
            # ra TTS như một dashboard vừa được cấp dây thanh quản.
            text = (
                f"Tôi đã kiểm tra. Có {len(active)} ký ức cũ chưa sắp xếp xong. "
                "Tôi sẽ chưa coi chúng là điều chắc chắn."
            )
        evidence_ids = [str(getattr(item, "id", "")) for item in active[:8] if str(getattr(item, "id", ""))]
        evidence = (CognitiveEvidence(
            "memory_candidate_store:snapshot",
            "memory_kernel.core.memory_candidate",
            f"active={len(active)} total={len(items)}",
            {"candidate_ids": evidence_ids, "status_counts": by_status},
        ),)
        return text, {
            "verification_key": "inspect_memory_candidates",
            "active_count": len(active),
            "total_count": len(items),
            "status_counts": by_status,
            "candidate_ids": evidence_ids,
        }, evidence

    def _recent_public_turns(self, limit: int = 8) -> list[Any]:
        owner = self.owner
        ledger = getattr(owner, "public_turn_ledger", None) if owner is not None else None
        return list(ledger.recent(limit)) if ledger is not None else []

    def _compare_recent_turns(self, action_input: str) -> tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]:
        turns = [turn for turn in self._recent_public_turns(10) if _clean(getattr(turn, "text", ""))]
        if len(turns) < 2:
            evidence = (CognitiveEvidence("public_turns:insufficient", "public_turn_ledger", f"count={len(turns)}"),)
            return "Tôi chưa có đủ hai lượt public để so sánh.", {
                "verification_key": "compare_recent_turns", "turn_count": len(turns), "similarity": 0.0,
            }, evidence
        left, right = turns[-2], turns[-1]
        left_text, right_text = _clean(left.text, 500), _clean(right.text, 500)
        a, b = _token_set(left_text), _token_set(right_text)
        similarity = len(a & b) / max(1, len(a | b))
        relation = "gần như lặp cùng ý" if similarity >= 0.72 else "có phần trùng ý" if similarity >= 0.42 else "khác ý rõ"
        text = f"Hai lượt public gần nhất {relation}, độ trùng từ khóa khoảng {similarity:.2f}."
        evidence = (
            CognitiveEvidence(f"turn_id:{left.turn_id}", "public_turn_ledger", left_text),
            CognitiveEvidence(f"turn_id:{right.turn_id}", "public_turn_ledger", right_text),
        )
        return text, {
            "verification_key": "compare_recent_turns",
            "left_turn_id": left.turn_id,
            "right_turn_id": right.turn_id,
            "similarity": round(similarity, 4),
            "relation": relation,
        }, evidence

    def _inspect_social_state(self, action_input: str) -> tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]:
        del action_input
        state = self._mind_state()
        relationship = getattr(state, "relationship", None)
        appraisal = getattr(state, "appraisal", None)
        payload = {
            "relational_trust": float(getattr(relationship, "relational_trust", 0.0) or 0.0),
            "familiarity": float(getattr(relationship, "familiarity", 0.0) or 0.0),
            "closeness": float(getattr(relationship, "closeness", 0.0) or 0.0),
            "playful_safety": float(getattr(relationship, "playful_safety", 0.0) or 0.0),
            "rupture": float(getattr(relationship, "rupture", 0.0) or 0.0),
            "irritation": float(getattr(appraisal, "irritation", 0.0) or 0.0),
            "tenderness": float(getattr(appraisal, "tenderness", 0.0) or 0.0),
        }
        text = (
            "State quan hệ hiện tại: "
            f"trust {payload['relational_trust']:.2f}, familiarity {payload['familiarity']:.2f}, "
            f"closeness {payload['closeness']:.2f}, rupture {payload['rupture']:.2f}. "
            "Đây là read-model của Eris, không phải chẩn đoán cảm xúc của bạn."
        )
        seq = int(getattr(state, "last_applied_turn_seq", 0) or 0)
        evidence = (CognitiveEvidence(
            f"ai_state:turn:{seq}",
            "ai_state_reducer",
            f"trust={payload['relational_trust']:.2f} closeness={payload['closeness']:.2f} rupture={payload['rupture']:.2f}",
            payload,
        ),)
        return text, {"verification_key": "inspect_social_state", **payload}, evidence

    def _detect_internal_contradictions(self, action_input: str) -> tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]:
        del action_input
        turns = [turn for turn in self._recent_public_turns(20) if _clean(getattr(turn, "text", ""))][-16:]
        contradictions: list[tuple[Any, Any, float]] = []
        for idx, left in enumerate(turns):
            left_text = _clean(getattr(left, "text", ""))
            for right in turns[idx + 1:]:
                right_text = _clean(getattr(right, "text", ""))
                if _has_negation(left_text) == _has_negation(right_text):
                    continue
                a, b = _token_set(left_text), _token_set(right_text)
                score = len(a & b) / max(1, min(len(a), len(b)))
                if score >= 0.62 and len(a & b) >= 2:
                    contradictions.append((left, right, score))
        if not contradictions:
            text = "Tôi đã đối chiếu các câu public gần đây. Chưa thấy cặp phủ định mâu thuẫn đủ rõ."
            evidence = (CognitiveEvidence("public_turn_scan:none", "public_turn_ledger", f"scanned={len(turns)}"),)
        else:
            left, right, score = contradictions[-1]
            text = (
                "Tôi thấy một cặp có khả năng mâu thuẫn: "
                f"“{_clean(getattr(left, 'text', ''), 180)}” và “{_clean(getattr(right, 'text', ''), 180)}”."
            )
            evidence = (
                CognitiveEvidence(f"turn_id:{left.turn_id}", "public_turn_ledger", _clean(left.text, 220)),
                CognitiveEvidence(f"turn_id:{right.turn_id}", "public_turn_ledger", _clean(right.text, 220)),
            )
        return text, {
            "verification_key": "detect_internal_contradictions",
            "scanned_claims": len(turns),
            "contradiction_count": len(contradictions),
            "pairs": [
                {"left": left.turn_id, "right": right.turn_id, "score": round(score, 4)}
                for left, right, score in contradictions[:6]
            ],
        }, evidence

    def _summarize_open_loops(self, action_input: str) -> tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]:
        del action_input
        state = self._mind_state()
        observation = dict(getattr(state, "latest_observation", {}) or {}) if state is not None else {}
        open_loop = _clean(observation.get("open_loop", ""), 220)
        repair = bool(observation.get("repair_needed", False))
        store = self._memory_candidate_store()
        candidates = []
        if store is not None:
            try:
                candidates = [
                    item for item in store.load()
                    if str(getattr(item, "extraction_status", "")) in {"unresolved", "partial", "failed"}
                ]
            except Exception:
                candidates = []
        loops: list[str] = []
        if repair:
            loops.append("một lượt có thể cần sửa")
        if open_loop:
            loops.append("một ý chưa đóng")
        if candidates:
            loops.append(f"{len(candidates)} ký ức cũ chưa sắp xếp")
        text = "Tôi đã kiểm tra. " + ("Hiện còn: " + "; ".join(loops) + "." if loops else "Hiện không còn điều gì đáng giữ mở.")
        evidence = (CognitiveEvidence(
            "open_loops:snapshot",
            "ai_state_reducer+memory_kernel",
            f"repair={repair} open_loop={bool(open_loop)} memory_candidates={len(candidates)}",
            {"repair_needed": repair, "open_loop": open_loop, "memory_candidate_count": len(candidates)},
        ),)
        return text, {
            "verification_key": "summarize_open_loops",
            "open_loop_count": len(loops),
            "repair_needed": repair,
            "unresolved_question": open_loop,
            "memory_candidate_count": len(candidates),
        }, evidence

    def _inspect_capability_health(self, action_input: str) -> tuple[str, dict[str, Any], tuple[CognitiveEvidence, ...]]:
        del action_input
        from actions.capability_runtime import runtime_capability_snapshot

        snapshot = runtime_capability_snapshot(refresh=True)
        statuses = dict(snapshot.get("statuses", {}) or {})
        available = sorted(key for key, value in statuses.items() if bool((value or {}).get("available")))
        unavailable = sorted(key for key, value in statuses.items() if not bool((value or {}).get("available")))
        text = f"Capability runtime hiện có {len(available)} năng lực available và {len(unavailable)} unavailable."
        if unavailable:
            text += " Các năng lực chưa sẵn sàng gồm: " + ", ".join(unavailable[:8]) + ("…" if len(unavailable) > 8 else "") + "."
        evidence = (CognitiveEvidence(
            f"capability_snapshot:revision:{snapshot.get('revision', 0)}",
            "actions.capability_runtime",
            f"available={len(available)} unavailable={len(unavailable)}",
        ),)
        return text, {
            "verification_key": "inspect_capability_health",
            "available_count": len(available),
            "unavailable_count": len(unavailable),
            "available_ids": available,
            "unavailable_ids": unavailable,
            "runtime_revision": snapshot.get("revision", 0),
        }, evidence


__all__ = [
    "COGNITIVE_CAPABILITY_CONTRACTS",
    "COGNITIVE_CAPABILITY_IDS",
    "CognitiveActionResult",
    "CognitiveActionState",
    "CognitiveCapabilityContract",
    "CognitiveEvidence",
    "CognitivePermissionMode",
    "INTERNAL_COGNITIVE_VERSION",
    "InternalCognitiveCapabilityRuntime",
]
