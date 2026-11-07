from __future__ import annotations

"""
memory_kernel_contracts.py
==========================

Các contract nhỏ cho MemoryKernel.

File này cố tình chỉ chứa dataclass/config/result, không chứa logic store.
Lý do: nếu contract bị trộn với commit/recall thật, mỗi lần sửa flow memory
sẽ kéo theo import vòng tròn. Repo đã đủ trò xiếc rồi, không cần thêm món đó.

Nguyên tắc:
- Runtime chỉ nên đọc các object trong file này để biết kernel đã làm gì.
- Logic ingest/recall/maintain nằm ở memory_kernel.py và memory_maintenance.py.
- Các field debug để phục vụ dev mode, không phải để surface thẳng cho user.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional


MemoryIngestMode = Literal["fast", "semantic_digest", "reflection"]
"""Chế độ ingest.

fast:
    Đường nóng trong chat. Không gọi model semantic parser.
    Chỉ dùng rule/policy rẻ để lưu fact rõ như tên, tháng sinh, style trả lời.

semantic_digest:
    Đường nền. Có thể dùng parser remote đã inject.
    Dùng để phân rã meaning sâu hơn khi remote API đang pending hoặc lúc idle.

reflection:
    Đường ngẫm lại. Dùng trong session end/dream/maintenance để thử chưng cất
    candidate hoặc sửa contradiction. Không nên chạy sau mọi lượt chat.
"""


MemoryAnswerMode = Literal["answer", "packet"]
"""Hai kiểu recall chính.

answer:
    Trả lời deterministic cho câu hỏi trí nhớ: tên tôi là gì, câu đầu tôi nói gì,
    tôi sinh tháng mấy. Đường này không nên để LLM tự đoán.

packet:
    Dựng MemoryPacket/resonance context cho prompt/attention/thought stream.
    Packet là bias nội bộ, không phải câu trả lời exact cho user.
"""


MaintenanceBudget = Literal["small", "normal", "deep"]
"""Mức ngân sách maintain.

small:
    Chạy nhẹ, hợp idle ngắn. Chủ yếu consolidate candidate ít.
normal:
    Chạy vừa, hợp sau vài turn hoặc session end.
deep:
    Chạy sâu hơn, hợp reflection/dream. Không nên gọi trong hot path.
"""


@dataclass(frozen=True, slots=True)
class PublicMemoryContext:
    """Một lần chọn memory cho cả prose và typed records.

    degraded=True khi có profile/active_state chưa truy nguyên được record.
    Điều này không gán 'unknown' thành fact và không làm mất legacy context.
    """
    text: str
    records: tuple[Any, ...] = ()
    degraded: bool = False


@dataclass(slots=True)
class MemoryKernelConfig:
    """Config runtime cho MemoryKernel.

    Các ngưỡng ở đây là ngưỡng orchestration, không thay thế worthiness/retention.
    Worthiness/retention vẫn là luật chuyên trách của memory layer.
    """

    # Confidence tối thiểu để answer deterministic được coi là đáng surface.
    answer_min_confidence: float = 0.60

    # Ambiguity quá cao thì kernel không intercept answer, tránh trả lời sai chắc.
    answer_max_ambiguity: float = 0.78

    # Commit từ semantic digest vẫn phải đủ confidence, nếu không nên rơi về candidate.
    digest_commit_min_confidence: float = 0.62

    # Cho phép ingest tạo unresolved candidate khi câu user explicit muốn nhớ nhưng mờ.
    allow_candidate_by_default: bool = True

    # Maintain mặc định không drop destructive. Muốn xóa thật phải bật bằng tay.
    destructive_maintenance: bool = False

    # Số candidate tối đa thử consolidate mỗi lần maintain small/normal/deep.
    candidate_limit_small: int = 6
    candidate_limit_normal: int = 20
    candidate_limit_deep: int = 48


@dataclass(slots=True)
class MemoryIngestPlan:
    """Side-effect-free ingest proposal produced before durable commit.

    The hot chat path may inspect this object without taking the global memory
    commit lock. Background workers can later commit the exact annotated
    requests, which gives extraction and persistence an explicit ownership
    boundary instead of letting every caller improvise one.
    """

    mode: MemoryIngestMode
    source: str
    input_text: str
    evidence_ids: tuple[str, ...] = ()
    decisions: tuple[Any, ...] = ()
    commit_requests: tuple[Any, ...] = ()
    warnings: tuple[str, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def decision_count(self) -> int:
        return len(self.decisions)

    @property
    def request_count(self) -> int:
        return len(self.commit_requests)


@dataclass(slots=True)
class MemoryIngestResult:
    """Kết quả ingest một mảnh text/evidence.

    `decisions` là output từ MemoryIngestPolicy/MemoryCommitPolicy.
    `commit_results` là output từ MemoryCognitionCore.commit().
    Tách hai thứ này để debug được lỗi nằm ở extraction hay commit permission.
    """

    accepted: bool
    mode: MemoryIngestMode
    source: str
    input_text: str
    decisions: list[Any] = field(default_factory=list)
    commit_results: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def committed_count(self) -> int:
        """Số commit được nhận bởi store.

        Một số commit_result dùng enum/string khác nhau, nên check mềm.
        """

        total = 0
        for item in self.commit_results:
            outcome = str(getattr(item, "outcome", "") or "")
            if outcome in {"accepted", "tentative"}:
                total += 1
        return total

    def to_dict(self) -> dict[str, Any]:
        """Dump debug an toàn cho log/dev panel."""

        return {
            "accepted": self.accepted,
            "mode": self.mode,
            "source": self.source,
            "input_text": self.input_text,
            "decision_count": len(self.decisions),
            "committed_count": self.committed_count,
            "warnings": list(self.warnings),
            "debug": dict(self.debug),
        }


@dataclass(slots=True)
class MemoryAnswerResult:
    """Kết quả answer deterministic từ MemoryKernel.

    `answer` thường là RecallAnswer từ memory_query_ir.
    Kernel không convert thẳng sang Decision public; runtime/chat adapter làm việc đó.
    Như vậy memory layer không phải import domain/decision.py, bớt dính tầng.
    """

    intercepted: bool
    answer: Optional[Any] = None
    compile_result: Optional[Any] = None
    raw_result: Optional[Any] = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Text public nếu có answer."""

        return str(getattr(self.answer, "text", "") or "") if self.answer is not None else ""

    @property
    def confidence(self) -> float:
        """Confidence mềm để runtime quyết có dùng answer hay không."""

        try:
            return float(getattr(self.answer, "confidence", 0.0) or 0.0)
        except Exception:
            return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "intercepted": self.intercepted,
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "warnings": list(self.warnings),
            "debug": dict(self.debug),
        }


@dataclass(slots=True)
class MemoryMaintenanceReport:
    """Báo cáo maintain memory.

    Maintain là việc nền: consolidate candidate, prune expired, check contradiction.
    Báo cáo này cố tình định lượng đơn giản để log dễ đọc.
    """

    reason: str
    budget: MaintenanceBudget
    consolidated: dict[str, Any] = field(default_factory=dict)
    pruned: dict[str, Any] = field(default_factory=dict)
    contradictions: dict[str, Any] = field(default_factory=dict)
    access_decay: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "budget": self.budget,
            "consolidated": dict(self.consolidated),
            "pruned": dict(self.pruned),
            "contradictions": dict(self.contradictions),
            "access_decay": dict(self.access_decay),
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


__all__ = [
    "MemoryIngestMode",
    "MemoryAnswerMode",
    "MaintenanceBudget",
    "MemoryKernelConfig",
    "MemoryIngestPlan",
    "MemoryIngestResult",
    "MemoryAnswerResult",
    "MemoryMaintenanceReport",
]
