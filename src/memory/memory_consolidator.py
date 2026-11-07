from __future__ import annotations
from core.di import resolve_dependency

"""Consolidator cho unresolved memory candidates.

Consolidator chạy ở reflection/dream path hoặc sau turn rảnh:
- lấy candidate quan trọng nhưng chưa parse được;
- thử lại MemoryIntakePolicy với rule hiện tại;
- nếu ra semantic/procedural/episodic decision thì commit qua MemoryCognitionCore;
- nếu vẫn fail, tăng attempts và giữ unresolved.

Nó không được bịa fact mới. Nó chỉ thử chưng cất lại raw candidate. Nếu vẫn mờ
thì giữ mờ. Một nguyên tắc nhỏ mà nhiều hệ thống AI nên xăm lên trán ảo.
"""

from dataclasses import dataclass, field
from typing import Any

from .memory_commit_policy import MemoryCommitPolicy
from .memory_models import MemoryTarget


@dataclass(slots=True)
class ConsolidationResult:
    processed: int = 0
    promoted: int = 0
    failed: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "promoted": self.promoted,
            "failed": self.failed,
            "skipped": self.skipped,
            "notes": list(self.notes),
        }


class MemoryConsolidator:
    def __init__(self, memory_core: Any, *, intake_policy: MemoryCommitPolicy | None = None) -> None:
        self.memory_core = memory_core
        self.intake_policy = resolve_dependency(intake_policy, lambda: MemoryCommitPolicy())

    def run_once(self, *, limit: int = 20, max_attempts: int = 5) -> ConsolidationResult:
        store = getattr(self.memory_core, "memory_candidate", None)
        if store is None:
            return ConsolidationResult(skipped=1, notes=["memory_candidate_store_missing"])

        candidates = store.candidates_for_consolidation(limit=limit, max_attempts=max_attempts)
        result = ConsolidationResult()
        for candidate in candidates:
            result.processed += 1
            decisions = self.intake_policy.decide_from_user_turn(
                candidate.raw_text,
                turn_id=candidate.source_turn_id or (candidate.evidence_ids[0] if candidate.evidence_ids else None),
                allow_candidate=False,
            )
            typed = [decision for decision in decisions if decision.target != MemoryTarget.CANDIDATE]
            if not typed:
                store.mark_attempted(candidate.id, status="failed", reason="no_structured_extraction")
                result.failed += 1
                continue

            promoted_any = False
            for decision in typed:
                request = decision.to_state_commit_request(storage_permission=True, permission_required=False)
                commit_result = self.memory_core.commit(request)
                if getattr(commit_result, "outcome", "") in {"accepted", "tentative"}:
                    promoted_any = True
                    fact_key = str(decision.metadata.get("fact_key") or "")
                    store.mark_promoted(candidate.id, target=decision.target.value, fact_key=fact_key)
                    result.notes.append(f"promoted:{candidate.id}->{decision.target.value}:{fact_key or decision.kind.value}")
            if promoted_any:
                result.promoted += 1
            else:
                store.mark_attempted(candidate.id, status="failed", reason="commit_rejected")
                result.failed += 1
        return result
