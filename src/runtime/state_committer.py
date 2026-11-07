# -*- coding: utf-8 -*-
"""
src/runtime/state_committer.py
================================

StateCommitter là cổng commit state/memory của ERIS.

Nguyên tắc chính:
- Không module runtime nào được ghi thẳng vào memory.
- Module chỉ được tạo StateCommitRequest.
- StateCommitter kiểm tra quyền, confidence, evidence và decay.
- Sau khi request hợp lệ, StateCommitter mới gọi MemoryCognitionCore.commit(...).

Nói gọn: đây là chốt kiểm soát trước khi ký ức biến thành sự thật dài hạn.
Nếu bỏ lớp này, memory lại thành cái thùng rác tự tin. Một thành tựu đáng buồn,
nhưng rất phổ biến trong các repo thích gọi list[str] là cognition.
"""

from __future__ import annotations
from core.di import resolve_dependency

import dataclasses
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence

from core.evidence import ModuleReport, StateCommitRequest


CommitTarget = Literal[
    "event_ledger",
    "active_state",
    "episodic_memory",
    "semantic_memory",
    "relational_memory",
    "affective_residue_memory",
    "procedural_memory",
    "repair_memory",
    "memory_candidate",
    "unresolved_memory",
    "worldview_memory",
    "sociocultural_memory",
    "spatial_memory",
    "temporal_rhythm_memory",
    "body_rhythm_memory",
    "belief_core",
    "tom_history",
    "homeostatic_baseline",
    "self_continuity",
]

DecayPolicy = Literal["none", "slow", "normal", "fast", "session_only"]
CommitStatus = Literal["committed", "tentative", "rejected", "error"]
RejectSeverity = Literal["info", "warning", "error"]


@dataclass(slots=True)
class StateCommitterConfig:
    """Cấu hình ngưỡng commit.

    Giữ ở dataclass để sau này bạn tune bằng config/env thay vì rải magic number.
    Magic number là cách người ta biến cognition thành bùa chú có dấu bằng.
    """

    # Request >= ngưỡng này được commit bình thường.
    min_confidence_commit: float = 0.55

    # Request dưới commit nhưng >= tentative sẽ được lưu với trạng thái tentative.
    min_confidence_tentative: float = 0.35

    # Repair memory được nới hơn vì user correction thường quan trọng dù evidence mỏng.
    min_confidence_repair: float = 0.30

    # Event ledger có thể ghi raw event với confidence thấp hơn vì nó là audit thô.
    min_confidence_event_ledger: float = 0.15

    # Nếu target nằm trong nhóm này mà thiếu evidence_ids thì reject.
    # `event_ledger` được nới vì chính nó là nơi giữ raw truth/audit.
    require_evidence_targets: frozenset[str] = frozenset(
        {
            "episodic_memory",
            "semantic_memory",
            "relational_memory",
            "affective_residue_memory",
            "procedural_memory",
            "repair_memory",
            "memory_candidate",
            "unresolved_memory",
            "worldview_memory",
            "sociocultural_memory",
            "spatial_memory",
            "temporal_rhythm_memory",
            "body_rhythm_memory",
            "belief_core",
            "tom_history",
            "homeostatic_baseline",
            "self_continuity",
        }
    )

    # Cho phép active_state cập nhật không evidence nếu caller đang lưu snapshot runtime.
    # Nếu bạn muốn cực nghiêm, đổi thành False.
    allow_active_state_without_evidence: bool = True

    # Cho phép event ledger ghi raw event không evidence_ids, vì raw event có thể là evidence gốc.
    allow_event_ledger_without_evidence: bool = True

    # Nếu evidence là private/sensitive + raw, cần quyền raw_evidence_storable từ frame.permission.
    enforce_private_raw_permission: bool = True

    # Nếu phát hiện user correction ở request target khác, reroute sang repair_memory.
    prioritize_user_correction_to_repair: bool = True

    # Khi low confidence nhưng vẫn tentative, ép decay nhanh hơn để ký ức yếu tự mục rữa.
    tentative_decay_policy: str = "fast"

    # Giới hạn batch để tránh bug upstream ném cả kho commit vào một turn.
    max_requests_per_commit: int = 128

    # Có de-duplicate request trong cùng batch không.
    deduplicate_batch: bool = True

    # Nếu memory core lỗi ở một request, tiếp tục request sau thay vì chết cả frame.
    continue_on_error: bool = True


@dataclass(slots=True)
class CommitValidationIssue:
    """Một vấn đề phát hiện trong quá trình validate."""

    code: str
    message: str
    severity: RejectSeverity = "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(slots=True)
class CommitDecision:
    """Kết quả kiểm định một request.

    `normalized_request` là request đã được chuẩn hóa/reroute/đánh tentative nếu cần.
    Nếu `accepted=False`, tuyệt đối không được đưa vào MemoryCognitionCore.
    """

    request_index: int
    original_target: str
    final_target: str
    accepted: bool
    status: CommitStatus
    confidence: float
    evidence_ids: list[str]
    reason: str
    issues: list[CommitValidationIssue] = field(default_factory=list)
    normalized_request: Any = None
    committed: bool = False
    commit_result: Any = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_index": self.request_index,
            "original_target": self.original_target,
            "final_target": self.final_target,
            "accepted": self.accepted,
            "status": self.status,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
            "issues": [issue.to_dict() for issue in self.issues],
            "committed": self.committed,
            "error": self.error,
        }


@dataclass(slots=True)
class CommitBatchReport:
    """Audit report cho một lần StateCommitter.commit(...)."""

    frame_id: Optional[str]
    requested: int = 0
    accepted: int = 0
    committed: int = 0
    tentative: int = 0
    rejected: int = 0
    errors: int = 0
    decisions: list[CommitDecision] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, (end - self.started_at) * 1000.0)

    def finish(self) -> "CommitBatchReport":
        self.finished_at = time.time()
        self.requested = len(self.decisions)
        self.accepted = sum(1 for d in self.decisions if d.accepted)
        self.committed = sum(1 for d in self.decisions if d.committed)
        self.tentative = sum(1 for d in self.decisions if d.status == "tentative")
        self.rejected = sum(1 for d in self.decisions if d.status == "rejected")
        self.errors = sum(1 for d in self.decisions if d.status == "error")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "requested": self.requested,
            "accepted": self.accepted,
            "committed": self.committed,
            "tentative": self.tentative,
            "rejected": self.rejected,
            "errors": self.errors,
            "duration_ms": round(self.duration_ms, 3),
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


# ---------------------------------------------------------------------------
# Helper đọc object/dict/dataclass mềm.
# ---------------------------------------------------------------------------

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except Exception:
        return default
    if result != result:  # NaN, vâng, Python cũng có những trò nhỏ đáng ghét.
        return default
    return max(0.0, min(1.0, result))


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "allowed", "allow"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "denied", "deny"}:
            return False
    return default


def _as_list_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Iterable):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return list(dict.fromkeys(out))
    text = str(value).strip()
    return [text] if text else []


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Đọc field từ dict/object/dataclass mà không kén kiểu.

    Dùng helper này để file không gãy khi bạn đổi contract một chút.
    Kiến trúc tốt nên cứng ở nguyên tắc, mềm ở giao diện. Ngược lại là spaghetti có tham vọng.
    """

    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _set_if_possible(obj: Any, name: str, value: Any) -> Any:
    if obj is None:
        return obj
    if isinstance(obj, dict):
        obj[name] = value
        return obj
    try:
        setattr(obj, name, value)
    except Exception:
        pass
    return obj


def _content_is_empty(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, (list, tuple, set, dict)):
        return len(content) == 0
    return False


def _normalize_target(target: Any) -> str:
    raw = str(target or "").strip()
    aliases = {
        "active_state_snapshot": "active_state",
        "active_state_memory": "active_state",
        "episodic": "episodic_memory",
        "semantic": "semantic_memory",
        "relationship_memory": "relational_memory",
        "relational": "relational_memory",
        "emotional_residue_memory": "affective_residue_memory",
        "affective": "affective_residue_memory",
        "procedural": "procedural_memory",
        "repair": "repair_memory",
        "candidate": "memory_candidate",
        "memory_candidate_store": "memory_candidate",
        "unresolved_memory": "memory_candidate",
        "unresolved": "memory_candidate",
        "worldview": "worldview_memory",
        "culture_memory": "sociocultural_memory",
        "sociocultural": "sociocultural_memory",
        "spatial": "spatial_memory",
        "temporal": "temporal_rhythm_memory",
        "body_rhythm": "body_rhythm_memory",
        "belief": "belief_core",
        "belief_store": "belief_core",
        "tom": "tom_history",
        "theory_of_mind_history": "tom_history",
        "homeostasis": "homeostatic_baseline",
        "self_model": "self_continuity",
        "identity_continuity": "self_continuity",
    }
    return aliases.get(raw, raw)


def _normalize_decay_policy(value: Any) -> str:
    raw = str(value or "normal").strip().lower()
    if raw in {"none", "slow", "normal", "fast", "session_only", "candidate"}:
        return raw
    if raw in {"session", "turn", "temporary", "temp"}:
        return "session_only"
    return "normal"


def _request_metadata(req: Any) -> dict[str, Any]:
    meta = _get(req, "metadata", {})
    if isinstance(meta, Mapping):
        return dict(meta)
    return {}


def _request_tags(req: Any) -> list[str]:
    tags = _as_list_str(_get(req, "tags", []))
    metadata = _request_metadata(req)
    tags.extend(_as_list_str(metadata.get("tags", [])))
    return list(dict.fromkeys(t.lower() for t in tags if t))


# ---------------------------------------------------------------------------
# StateCommitter
# ---------------------------------------------------------------------------

class StateCommitter:
    """Cổng commit chính cho runtime.

    Usage chuẩn trong CognitiveRuntime:

        self.state_committer = StateCommitter(self.memory_cognition)
        ...
        self.state_committer.commit(frame)

    Hoặc nếu caller chỉ có list request:

        self.state_committer.commit(requests, frame=frame)

    Lớp này không import event_ledger/semantic/repair store trực tiếp.
    Nó chỉ gọi MemoryCognitionCore.commit(...). Nếu StateCommitter tự route xuống từng store,
    ta lại có hai nguồn sự thật. Một thảm họa nhỏ mặc áo kiến trúc.
    """

    VALID_TARGETS: frozenset[str] = frozenset(
        {
            "event_ledger",
            "active_state",
            "episodic_memory",
            "semantic_memory",
            "relational_memory",
            "affective_residue_memory",
            "procedural_memory",
            "repair_memory",
            "memory_candidate",
            "unresolved_memory",
            "worldview_memory",
            "sociocultural_memory",
            "spatial_memory",
            "temporal_rhythm_memory",
            "body_rhythm_memory",
            "belief_core",
            "tom_history",
            "homeostatic_baseline",
            "self_continuity",
        }
    )

    def __init__(
        self,
        memory_cognition: Any,
        *,
        config: Optional[StateCommitterConfig] = None,
        logger: Optional[logging.Logger] = None,
        epistemic_kernel: Any = None,
    ) -> None:
        if memory_cognition is None:
            raise ValueError("StateCommitter cần MemoryCognitionCore, không phải None.")
        self.memory_cognition = memory_cognition
        self.config = resolve_dependency(config, lambda: StateCommitterConfig())
        self.logger = logger or logging.getLogger(__name__)
        self.epistemic_kernel = epistemic_kernel

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def commit(self, frame_or_requests: Any, *, frame: Any = None) -> CommitBatchReport:
        """Validate rồi commit batch request.

        Tham số:
        - frame_or_requests:
            + CognitiveFrame có `.commit_requests`, hoặc
            + list/tuple StateCommitRequest, hoặc
            + một StateCommitRequest đơn.
        - frame:
            Context frame tùy chọn nếu truyền list request riêng.

        Trả về:
        - CommitBatchReport để coordinator/debug_trace ghi lại.
        """

        actual_frame = frame if frame is not None else self._maybe_frame(frame_or_requests)
        frame_id = self._frame_id(actual_frame)
        requests = self._extract_requests(frame_or_requests)
        report = CommitBatchReport(frame_id=frame_id)

        if not requests:
            report.finish()
            self._run_canonical_decay_if_needed(actual_frame, report)
            self._attach_report_to_frame(actual_frame, report)
            return report

        if len(requests) > self.config.max_requests_per_commit:
            self.logger.warning(
                "StateCommitter nhận %s request, cắt còn %s để tránh batch flood.",
                len(requests),
                self.config.max_requests_per_commit,
            )
            requests = requests[: self.config.max_requests_per_commit]

        evidence_index = self._build_evidence_index(actual_frame)
        seen_keys: set[tuple[str, str, tuple[str, ...]]] = set()

        for index, request in enumerate(requests):
            decision = self.validate(
                request,
                request_index=index,
                frame=actual_frame,
                evidence_index=evidence_index,
            )

            if self.config.deduplicate_batch and decision.accepted:
                dedup_key = self._dedup_key(decision.normalized_request)
                if dedup_key in seen_keys:
                    decision.accepted = False
                    decision.status = "rejected"
                    decision.issues.append(
                        CommitValidationIssue(
                            code="duplicate_request",
                            message="Request trùng target/content/evidence trong cùng batch; bỏ để tránh ghi lặp.",
                            severity="info",
                        )
                    )
                else:
                    seen_keys.add(dedup_key)

            if decision.accepted:
                self._commit_one(decision, actual_frame)

            report.decisions.append(decision)

            if decision.status == "error" and not self.config.continue_on_error:
                break

        report.finish()
        self._run_canonical_decay_if_needed(actual_frame, report)
        self._attach_report_to_frame(actual_frame, report)
        return report

    def _run_canonical_decay_if_needed(
        self,
        frame: Any,
        report: CommitBatchReport,
    ) -> None:
        """Run long-term decay only on reflection or committed dream consolidation.

        DreamConsolidation is allowed to *request* durable updates, but it still
        cannot write stores directly. Once those requests survive StateCommitter,
        this hook performs evidence-preserving decay across canonical stores.
        Ordinary turns do not run decay. Otherwise every "ừ" would age the
        entire identity model, which is a rather theatrical way to corrupt state.
        """

        decay_fn = getattr(self.memory_cognition, "decay_canonical_long_term", None)
        if not callable(decay_fn):
            return

        budget_mode = str(_get(frame, "budget_mode", "") or "").strip().lower()
        reflection = budget_mode == "reflection"
        dream_commit = False
        for decision in report.decisions:
            if not decision.committed or decision.normalized_request is None:
                continue
            request = decision.normalized_request
            source_module = str(_get(request, "source_module", "") or "").strip().lower()
            tags = set(_request_tags(request))
            if source_module == "dream_consolidation" or "dream_consolidation" in tags:
                dream_commit = True
                break

        if not (reflection or dream_commit):
            return

        metadata = _get(frame, "metadata", None)
        try:
            decay_result = decay_fn()
        except Exception as exc:  # decay must never roll back already-valid commits
            self.logger.exception("Canonical long-term decay failed after consolidation commit")
            if isinstance(metadata, dict):
                metadata["canonical_decay_error"] = f"{type(exc).__name__}: {exc}"
            return

        if isinstance(metadata, dict):
            metadata["canonical_decay"] = {
                "trigger": "reflection" if reflection else "dream_consolidation",
                "result": decay_result,
                "timestamp": time.time(),
            }

        audit = _get(frame, "audit_mutations", None)
        if isinstance(audit, list):
            audit.append({
                "type": "canonical_long_term_decay",
                "trigger": "reflection" if reflection else "dream_consolidation",
                "result": decay_result,
            })

    def commit_frame(self, frame: Any) -> CommitBatchReport:
        """Alias rõ nghĩa cho coordinator thích gọi tên dài."""

        return self.commit(frame)

    def commit_requests(self, requests: Sequence[Any], *, frame: Any = None) -> CommitBatchReport:
        """Alias khi caller đã có list StateCommitRequest."""

        return self.commit(list(requests), frame=frame)

    def validate(
        self,
        request: Any,
        *,
        request_index: int = 0,
        frame: Any = None,
        evidence_index: Optional[dict[str, Any]] = None,
    ) -> CommitDecision:
        """Kiểm định một StateCommitRequest nhưng chưa ghi memory.

        Đây là nơi enforce các luật:
        - không có quyền thì không lưu;
        - confidence thấp thì tentative hoặc bỏ;
        - thiếu evidence thì bỏ ở target cần evidence;
        - private raw evidence phải có permission;
        - user correction ưu tiên repair_memory.
        """

        evidence_index = evidence_index or self._build_evidence_index(frame)
        issues: list[CommitValidationIssue] = []

        original_target = _normalize_target(_get(request, "target", ""))
        final_target = original_target
        confidence = _safe_float(_get(request, "confidence", 0.0), 0.0)
        evidence_ids = _as_list_str(_get(request, "evidence_ids", []))
        content = _get(request, "content", None)
        reason = str(_get(request, "reason", "") or "").strip()
        permission_required = _safe_bool(_get(request, "permission_required", True), True)
        storage_permission = _safe_bool(_get(request, "storage_permission", False), False)
        decay_policy = _normalize_decay_policy(_get(request, "decay_policy", "normal"))
        metadata = _request_metadata(request)
        tags = _request_tags(request)

        decision = CommitDecision(
            request_index=request_index,
            original_target=original_target,
            final_target=final_target,
            accepted=False,
            status="rejected",
            confidence=confidence,
            evidence_ids=evidence_ids,
            reason=reason,
            issues=issues,
        )

        # 1. Target phải thuộc registry commit protocol.
        if not final_target:
            issues.append(
                CommitValidationIssue(
                    code="missing_target",
                    message="Request thiếu target; không thể route memory.",
                    severity="error",
                )
            )
            return decision

        if final_target not in self.VALID_TARGETS:
            issues.append(
                CommitValidationIssue(
                    code="invalid_target",
                    message=f"Target '{final_target}' không nằm trong commit protocol.",
                    severity="error",
                )
            )
            return decision

        # 2. Không có quyền lưu thì bỏ. Không thương lượng.
        if permission_required and not storage_permission:
            issues.append(
                CommitValidationIssue(
                    code="storage_permission_denied",
                    message="Request yêu cầu permission nhưng storage_permission=False; không lưu.",
                    severity="warning",
                )
            )
            return decision

        if not permission_required and not storage_permission:
            # Vẫn không lưu nếu caller đã nói storage_permission=False.
            # `permission_required=False` chỉ nghĩa là không cần hỏi thêm, không phải giấy phép ghi bừa.
            issues.append(
                CommitValidationIssue(
                    code="storage_permission_false",
                    message="storage_permission=False; không lưu dù permission_required=False.",
                    severity="warning",
                )
            )
            return decision

        # 3. Content rỗng không được thành ký ức. Trừ khi upstream muốn lưu audit rỗng,
        # mà nếu vậy thì upstream cần đi làm lại. Ta reject cho sạch.
        if _content_is_empty(content):
            issues.append(
                CommitValidationIssue(
                    code="empty_content",
                    message="Request có content rỗng; bỏ để tránh memory nhiễu.",
                    severity="warning",
                )
            )
            return decision

        # 4. Evidence check. Một fact không evidence mà được lưu dài hạn là cách rất nhanh
        # để ERIS tiến hóa thành một máy bịa có trí nhớ.
        evidence_required = final_target in self.config.require_evidence_targets
        if final_target == "active_state" and self.config.allow_active_state_without_evidence:
            evidence_required = False
        if final_target == "event_ledger" and self.config.allow_event_ledger_without_evidence:
            evidence_required = False
        if decay_policy == "session_only":
            evidence_required = False

        if evidence_required and not evidence_ids:
            issues.append(
                CommitValidationIssue(
                    code="missing_evidence_ids",
                    message=f"Target '{final_target}' cần evidence_ids nhưng request không có evidence.",
                    severity="warning",
                )
            )
            return decision

        # 5. Evidence id lạ không reject tuyệt đối, vì nhiều module có thể tạo evidence ngoài frame.
        # Nhưng phải cảnh báo để debug biết upstream đang bắn id không thấy trong frame.
        if evidence_ids and evidence_index:
            missing = [eid for eid in evidence_ids if eid not in evidence_index]
            if missing:
                issues.append(
                    CommitValidationIssue(
                        code="evidence_not_in_frame",
                        message="Một số evidence_ids không có trong frame hiện tại: " + ", ".join(missing[:5]),
                        severity="info",
                    )
                )

        # 6. Private raw evidence cần quyền raw_evidence_storable.
        if self.config.enforce_private_raw_permission:
            if self._contains_private_raw_evidence(request, evidence_ids, evidence_index):
                raw_allowed = self._frame_permission_flag(frame, "raw_evidence_storable", default=False)
                memory_allowed = self._frame_permission_flag(frame, "memory_storable", default=storage_permission)
                if not (raw_allowed and memory_allowed):
                    issues.append(
                        CommitValidationIssue(
                            code="private_raw_evidence_not_allowed",
                            message="Request chạm private/sensitive raw evidence nhưng frame.permission không cho raw_evidence_storable.",
                            severity="warning",
                        )
                    )
                    return decision

        # 7. User correction ưu tiên RepairMemory.
        # Phải reroute TRƯỚC semantic grounding check. Nếu không, một correction bị upstream
        # gắn nhầm target=semantic_memory + ungrounded sẽ bị reject mất, thay vì thành repair rule.
        correction_detected = self._looks_like_user_correction(request, content, reason, tags, metadata)
        if (
            correction_detected
            and self.config.prioritize_user_correction_to_repair
            and final_target != "repair_memory"
        ):
            issues.append(
                CommitValidationIssue(
                    code="rerouted_user_correction_to_repair",
                    message=f"User correction được ưu tiên vào repair_memory thay vì '{final_target}'.",
                    severity="info",
                )
            )
            metadata = dict(metadata)
            metadata.setdefault("original_target", final_target)
            metadata["rerouted_by_state_committer"] = True
            tags = list(dict.fromkeys([*tags, "user_correction", "repair_priority"]))
            final_target = "repair_memory"
            decision.final_target = final_target

        # 8. Confidence gate. Keep this before semantic-source validation so
        # very weak requests report confidence_too_low as the primary issue.
        min_commit, min_tentative = self._confidence_thresholds(final_target)

        if confidence < min_tentative:
            issues.append(
                CommitValidationIssue(
                    code="confidence_too_low",
                    message=(
                        f"confidence={confidence:.2f} thấp hơn ngưỡng tentative "
                        f"{min_tentative:.2f}; không lưu."
                    ),
                    severity="warning",
                )
            )
            return decision

        status: CommitStatus = "committed"
        if confidence < min_commit:
            status = "tentative"
            issues.append(
                CommitValidationIssue(
                    code="confidence_tentative",
                    message=(
                        f"confidence={confidence:.2f} dưới ngưỡng commit {min_commit:.2f}; "
                        "lưu tentative với decay nhanh hơn."
                    ),
                    severity="info",
                )
            )
            if decay_policy not in {"session_only", "fast"}:
                decay_policy = self.config.tentative_decay_policy

        # 9. Metadata audit chuẩn hóa.
        semantic_issue = self._semantic_grounding_issue(
            final_target=final_target,
            content=content,
            metadata=metadata,
            frame=frame,
            status=status,
            decay_policy=decay_policy,
        )
        if semantic_issue is not None:
            issues.append(semantic_issue)
            return decision

        metadata = dict(metadata)
        metadata.setdefault("commit_status", status)
        metadata.setdefault("validated_by", "StateCommitter")
        metadata.setdefault("validated_at", time.time())
        if frame is not None:
            metadata.setdefault("frame_id", self._frame_id(frame))
        if correction_detected:
            metadata.setdefault("user_correction", True)

        normalized = self._clone_request(
            request,
            target=final_target,
            content=content,
            confidence=confidence,
            evidence_ids=evidence_ids,
            permission_required=permission_required,
            storage_permission=storage_permission,
            decay_policy=decay_policy,
            reason=reason,
            tags=tags,
            metadata=metadata,
            frame_id=metadata.get("frame_id") or _get(request, "frame_id", None) or self._frame_id(frame),
        )

        decision.accepted = True
        decision.status = status
        decision.final_target = final_target
        decision.normalized_request = normalized
        return decision

    # ------------------------------------------------------------------
    # Commit execution
    # ------------------------------------------------------------------

    def _commit_one(self, decision: CommitDecision, frame: Any = None) -> None:
        """Gọi MemoryCognitionCore.commit(...) với request đã validate."""

        request = decision.normalized_request
        if (
            decision.final_target == "belief_core"
            and self.epistemic_kernel is not None
            and getattr(self.epistemic_kernel, "authoritative", False)
        ):
            content = _get(request, "content", {})
            raw = content if isinstance(content, Mapping) else {"claim": str(content or "")}
            mutation = type("EpistemicCommitMutation", (), {
                "id": f"state_commit:{decision.request_index}:{int(time.time() * 1000)}",
                "value": raw,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "evidence_ids": tuple(decision.evidence_ids),
            })()
            decision.commit_result = self.epistemic_kernel.ingest_belief_mutation(mutation)
            decision.committed = True
            return
        try:
            result = self._call_memory_commit(request, frame=frame)
        except Exception as exc:  # pragma: no cover - phụ thuộc memory core thật
            decision.status = "error"
            decision.accepted = False
            decision.error = f"{type(exc).__name__}: {exc}"
            decision.committed = False
            decision.issues.append(
                CommitValidationIssue(
                    code="memory_core_commit_error",
                    message=decision.error,
                    severity="error",
                )
            )
            self.logger.exception("MemoryCognitionCore.commit lỗi trong StateCommitter")
            return

        decision.committed = True
        decision.commit_result = result

    def _call_memory_commit(self, request: Any, *, frame: Any = None) -> Any:
        """Gọi memory core bằng nhiều signature phổ biến.

        Lý do phải mềm:
        - nhánh này có thể đang dùng `commit(request)`;
        - nhánh khác có thể dùng `commit(request, frame=frame)`;
        - có nơi lại viết `commit_requests([...])` vì con người rất thích đặt tên lại
          cùng một hành động để repo trông như có thêm chiều sâu.
        """

        core = self.memory_cognition

        if hasattr(core, "commit"):
            commit_fn = getattr(core, "commit")
            try:
                return commit_fn(request, frame=frame)
            except TypeError as first_error:
                try:
                    return commit_fn(request)
                except TypeError:
                    # Nếu cả hai đều TypeError, giữ lỗi đầu tiên để signature có frame không bị che.
                    raise first_error

        if hasattr(core, "commit_request"):
            return getattr(core, "commit_request")(request)

        if hasattr(core, "commit_requests"):
            return getattr(core, "commit_requests")([request])

        raise AttributeError("MemoryCognitionCore không có commit/commit_request/commit_requests.")

    # ------------------------------------------------------------------
    # Request extraction / normalization
    # ------------------------------------------------------------------

    def _extract_requests(self, frame_or_requests: Any) -> list[Any]:
        if frame_or_requests is None:
            return []

        # CognitiveFrame chuẩn: có commit_requests.
        commit_requests = _get(frame_or_requests, "commit_requests", None)
        if commit_requests is not None and not isinstance(frame_or_requests, (list, tuple)):
            return list(commit_requests or [])

        # Nếu truyền trực tiếp list/tuple request.
        if isinstance(frame_or_requests, (list, tuple)):
            return list(frame_or_requests)

        # Nếu truyền một request đơn.
        if self._looks_like_commit_request(frame_or_requests):
            return [frame_or_requests]

        return []

    def _maybe_frame(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return None
        if self._looks_like_commit_request(value):
            return None
        if _get(value, "commit_requests", None) is not None:
            return value
        return None

    def _looks_like_commit_request(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, Mapping):
            return "target" in value and "confidence" in value
        return hasattr(value, "target") and hasattr(value, "confidence")

    def _clone_request(self, request: Any, **updates: Any) -> Any:
        """Clone request nhưng giữ class gốc nếu có thể.

        Điều này giúp tương thích với MemoryCognitionCore nếu nó kỳ vọng dataclass cụ thể.
        """

        # Dataclass gốc: dùng replace với các field có tồn tại.
        if dataclasses.is_dataclass(request):
            field_names = {field.name for field in dataclasses.fields(request)}
            safe_updates = {key: value for key, value in updates.items() if key in field_names}
            try:
                return replace(request, **safe_updates)
            except Exception:
                pass

        # Dict request: copy + update.
        if isinstance(request, Mapping):
            cloned = dict(request)
            cloned.update(updates)
            return cloned

        request_fields = {field.name for field in dataclasses.fields(StateCommitRequest)}
        payload = {name: _get(request, name, None) for name in request_fields}
        payload.update({key: value for key, value in updates.items() if key in request_fields})
        return StateCommitRequest(**payload)

    # ------------------------------------------------------------------
    # Evidence / permission helpers
    # ------------------------------------------------------------------

    def _build_evidence_index(self, frame: Any) -> dict[str, Any]:
        evidence_items = _get(frame, "evidence", []) or []
        index: dict[str, Any] = {}
        for item in evidence_items:
            eid = _get(item, "id", None)
            if eid is None:
                eid = _get(item, "evidence_id", None)
            if eid is not None:
                index[str(eid)] = item
        return index

    def _contains_private_raw_evidence(
        self,
        request: Any,
        evidence_ids: list[str],
        evidence_index: Mapping[str, Any],
    ) -> bool:
        # Trước hết đọc trực tiếp trên request/metadata nếu upstream đã gắn cờ.
        metadata = _request_metadata(request)
        req_privacy = str(
            _get(request, "privacy_level", metadata.get("privacy_level", "normal")) or "normal"
        ).lower()
        req_raw = str(
            _get(request, "raw_or_inferred", metadata.get("raw_or_inferred", metadata.get("claim_kind", ""))) or ""
        ).lower()
        if req_privacy in {"private", "sensitive"} and req_raw in {"raw", "raw_fact", "raw_evidence"}:
                return True

        # Sau đó đối chiếu evidence trong frame.
        for evidence_id in evidence_ids:
            item = evidence_index.get(evidence_id)
            if item is None:
                continue
            privacy = str(_get(item, "privacy_level", "normal") or "normal").lower()
            raw_kind = str(_get(item, "raw_or_inferred", "") or "").lower()
            if privacy in {"private", "sensitive"} and raw_kind == "raw":
                    return True
        return False

    def _semantic_grounding_issue(
        self,
        *,
        final_target: str,
        content: Any,
        metadata: Mapping[str, Any],
        frame: Any,
        status: CommitStatus = "committed",
        decay_policy: str = "normal",
    ) -> CommitValidationIssue | None:
        if final_target != "semantic_memory":
            return None

        semantic_source = ""
        if isinstance(content, Mapping):
            semantic_source = str(content.get("source", "") or metadata.get("semantic_source", "") or "")
        else:
            semantic_source = str(metadata.get("semantic_source", "") or metadata.get("source", "") or "")
        semantic_source = semantic_source.strip().lower()

        if not semantic_source:
            if status == "tentative" or decay_policy == "session_only":
                return None
            return CommitValidationIssue(
                code="semantic_fact_missing_source",
                message="Semantic fact thiếu semantic_source/source; reject để tránh fact không nguồn.",
                severity="warning",
            )

        if semantic_source in {"explicit_user_statement", "user_text", "user_statement"}:
            if metadata.get("source_grounded") is False or metadata.get("ungrounded") is True:
                return CommitValidationIssue(
                    code="semantic_fact_ungrounded",
                    message="Semantic fact bị metadata đánh dấu ungrounded; không lưu vào semantic_memory.",
                    severity="warning",
                )
            return None

        if metadata.get("source_grounded") is False or metadata.get("ungrounded") is True:
            return CommitValidationIssue(
                code="semantic_fact_ungrounded",
                message="Semantic fact suy diễn không có grounding đủ chắc; reject để tránh memory bịa.",
                severity="warning",
            )

        grounding = None
        if frame is not None:
            grounding = getattr(frame, "source_grounding", None) or getattr(frame, "grounding", None)
        missing = set(getattr(grounding, "missing_evidence", ()) or ())
        preempt_mode = str(getattr(grounding, "preempt_mode", "") or "")
        if preempt_mode or (missing & {"file_evidence", "screen_evidence", "web_access_or_search_result"}):
            return CommitValidationIssue(
                code="semantic_fact_ungrounded",
                message=(
                    "Semantic fact được trích từ turn đang thiếu grounding hiện tại "
                    f"(preempt_mode={preempt_mode or 'none'}; missing={','.join(sorted(missing)) or 'none'})."
                ),
                severity="warning",
            )
        return None

    def _frame_permission_flag(self, frame: Any, field_name: str, *, default: bool) -> bool:
        permission = _get(frame, "permission", None)
        if permission is None:
            # Một số runtime cũ gắn permission_report.
            permission = _get(frame, "permission_report", None)
        return _safe_bool(_get(permission, field_name, default), default)

    # ------------------------------------------------------------------
    # Correction / confidence logic
    # ------------------------------------------------------------------

    def _looks_like_user_correction(
        self,
        request: Any,
        content: Any,
        reason: str,
        tags: Sequence[str],
        metadata: Mapping[str, Any],
    ) -> bool:
        if _safe_bool(metadata.get("user_correction"), False):
            return True
        if _safe_bool(_get(request, "user_correction", False), False):
            return True
        if "user_correction" in tags or "correction" in tags or "repair" in tags:
            return True

        target = _normalize_target(_get(request, "target", ""))
        if target == "repair_memory":
            return True

        # State snapshots often contain innocent field names such as
        # `overread_correction`. Scanning repr(dict) turned those into fake user
        # corrections and rerouted active_state into RepairMemory. Structured
        # state is not prose; only explicit correction fields/tags may trigger.
        if isinstance(content, Mapping):
            content_text = " ".join(
                str(content.get(key, "") or "")
                for key in ("user_correction", "user_correction_text", "correction_text", "wrong_assumption", "failure_type")
            )
        else:
            content_text = str(content or "")[:800]
        if target in {"active_state", "event_ledger", "homeostatic_baseline", "self_continuity", "spatial_memory", "temporal_rhythm_memory", "body_rhythm_memory"} and not content_text.strip():
            return False

        haystack = " ".join(
            [
                str(reason or ""),
                str(metadata.get("failure_type", "") or ""),
                str(metadata.get("wrong_assumption", "") or ""),
                str(metadata.get("user_correction_text", "") or ""),
                content_text,
            ]
        ).lower()

        correction_markers = (
            "user corrected",
            "user correction",
            "correction",
            "misread",
            "wrong assumption",
            "sai rồi",
            "không phải",
            "khong phai",
            "thiếu là",
            "thieu la",
            "đính chính",
            "dinh chinh",
        )
        return any(marker in haystack for marker in correction_markers)

    def _confidence_thresholds(self, target: str) -> tuple[float, float]:
        if target == "repair_memory":
            return self.config.min_confidence_commit, self.config.min_confidence_repair
        if target == "event_ledger":
            return self.config.min_confidence_event_ledger, self.config.min_confidence_event_ledger
        return self.config.min_confidence_commit, self.config.min_confidence_tentative

    # ------------------------------------------------------------------
    # Audit integration
    # ------------------------------------------------------------------

    def _attach_report_to_frame(self, frame: Any, report: CommitBatchReport) -> None:
        if frame is None:
            return

        # Gắn debug_notes nếu có.
        note = (
            "state_commit: "
            f"requested={report.requested}, accepted={report.accepted}, "
            f"committed={report.committed}, tentative={report.tentative}, "
            f"rejected={report.rejected}, errors={report.errors}"
        )
        debug_notes = _get(frame, "debug_notes", None)
        if isinstance(debug_notes, list):
            debug_notes.append(note)

        # Nếu frame có audit_mutations thì lưu dict đầy đủ hơn.
        audit = _get(frame, "audit_mutations", None)
        if isinstance(audit, list):
            audit.append({"type": "state_commit", "report": report.to_dict()})

        # Nếu frame có add_report thì gắn ModuleReport chuẩn.
        add_report = getattr(frame, "add_report", None)
        if callable(add_report):
            try:
                add_report(
                    ModuleReport(
                        module_name="StateCommitter",
                        confidence=1.0 if report.errors == 0 else 0.65,
                        pressure=0.0,
                        evidence_used=[],
                        result=report.to_dict(),
                        warnings=[
                            issue.message
                            for decision in report.decisions
                            for issue in decision.issues
                            if issue.severity in {"warning", "error"}
                        ],
                        debug={"duration_ms": report.duration_ms},
                    )
                )
            except Exception:
                # Audit không được làm chết commit. Nếu audit làm chết runtime thì đúng là ta
                # đã phát minh thêm một cách thất bại rất văn minh.
                self.logger.debug("Không gắn được StateCommitter report vào frame.", exc_info=True)

    def _frame_id(self, frame: Any) -> Optional[str]:
        value = _get(frame, "frame_id", None)
        if value is None:
            value = _get(frame, "id", None)
        return str(value) if value is not None else None

    def _dedup_key(self, request: Any) -> tuple[str, str, tuple[str, ...]]:
        target = _normalize_target(_get(request, "target", ""))
        content = _get(request, "content", "")
        if isinstance(content, str):
            content_key = " ".join(content.split())[:512]
        else:
            content_key = repr(content)[:512]
        evidence_ids = tuple(sorted(_as_list_str(_get(request, "evidence_ids", []))))
        return target, content_key, evidence_ids


__all__ = [
    "CommitBatchReport",
    "CommitDecision",
    "CommitTarget",
    "CommitValidationIssue",
    "DecayPolicy",
    "StateCommitRequest",
    "StateCommitter",
    "StateCommitterConfig",
]
