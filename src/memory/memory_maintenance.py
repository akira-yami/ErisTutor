from __future__ import annotations

"""
memory_maintenance.py
=====================

Runner maintain cho MemoryKernel.

File này gom các việc nền vốn đang nằm rải rác:
- consolidate unresolved memory candidates;
- prune/đánh dấu memory hết hạn theo retention metadata;
- kiểm tra contradiction ở mức nhẹ;
- access decay theo công thức half-life;
- AI curation: episodic distillation + concern bridge (budget=normal/deep).

Điểm quan trọng:
- Mặc định KHÔNG xóa dữ liệu thật. Nó chỉ audit/prune plan.
- Muốn drop item thật phải bật destructive=True rõ ràng.
- Maintain không được gọi trong hot path chat. Gọi nó trong idle/reflection/session_end.
"""

import time
from dataclasses import asdict
from typing import Any, Iterable

from .memory_consolidator import MemoryConsolidator
from .memory_pruner import MemoryPruner
from .memory_kernel_contracts import MaintenanceBudget, MemoryMaintenanceReport


class MemoryMaintenanceRunner:
    """Orchestrator maintain nhẹ cho memory stores."""

    def __init__(
        self,
        memory_core: Any,
        *,
        ingest_policy: Any = None,
        destructive: bool = False,
        llm_service: Any = None,
    ) -> None:
        self.memory_core = memory_core
        self.ingest_policy = ingest_policy
        self.destructive = bool(destructive)
        self.llm_service = llm_service
        self.pruner = MemoryPruner()

    def run(
        self,
        *,
        budget: MaintenanceBudget = "small",
        reason: str = "idle",
        destructive: bool | None = None,
    ) -> MemoryMaintenanceReport:
        """Chạy một lượt maintain.

        destructive=None nghĩa là dùng default từ runner.
        destructive=False nghĩa là chỉ audit, không drop item.
        destructive=True nghĩa là được save lại store sau khi bỏ item action=drop.
        """

        allow_destructive = self.destructive if destructive is None else bool(destructive)
        report = MemoryMaintenanceReport(reason=reason, budget=budget)

        # 1. Chưng cất candidate trước. Candidate là vùng đệm của câu user muốn nhớ
        # nhưng parser chưa đủ chắc. Nếu không consolidate, candidate thành nghĩa địa.
        try:
            limit = self._candidate_limit(budget)
            consolidator = MemoryConsolidator(self.memory_core, intake_policy=self.ingest_policy)
            result = consolidator.run_once(limit=limit)
            report.consolidated = result.to_dict() if hasattr(result, "to_dict") else asdict(result)
        except Exception as exc:  # pragma: no cover - maintain không được làm chết runtime
            report.warnings.append(f"consolidation_failed:{type(exc).__name__}:{exc}")

        # 2. Prune audit. Mặc định chỉ thống kê. Drop thật chỉ khi explicit bật.
        try:
            report.pruned = self._run_prune_audit(destructive=allow_destructive)
        except Exception as exc:  # pragma: no cover
            report.warnings.append(f"prune_failed:{type(exc).__name__}:{exc}")

        # 3. Contradiction repair — store-level cần index fact_key trước, chưa làm.
        report.contradictions = {
            "checked": False,
            "reason": "store_level_contradiction_repair_not_enabled_yet",
        }

        # 4. Access decay — áp half-life lên access_count để hot fact không đóng băng.
        try:
            report.access_decay = self._run_access_decay(budget=budget, destructive=allow_destructive)
        except Exception as exc:
            report.access_decay = {"applied": False, "error": f"{type(exc).__name__}:{exc}"}
            report.warnings.append(f"access_decay_failed:{type(exc).__name__}")

        # 5. AI curation: episodic distillation + concern bridge.
        # Chỉ chạy ở budget normal/deep để không tốn Gemini call khi idle ngắn.
        if budget in {"normal", "deep"} and self.llm_service is not None:
            try:
                from .memory_ai_curator import MemoryAICurator
                curator = MemoryAICurator(llm_service=self.llm_service, memory_core=self.memory_core)
                distill_result = curator.run_distillation(budget=budget)
                bridge_result = curator.run_concern_bridge(budget=budget)
                report.notes.append(
                    f"ai_curator: distilled={distill_result.distilled} bridged={bridge_result.bridged}"
                    f" skipped={distill_result.skipped + bridge_result.skipped}"
                )
                if distill_result.warnings:
                    report.warnings.extend(distill_result.warnings[:4])
                if bridge_result.warnings:
                    report.warnings.extend(bridge_result.warnings[:4])
            except Exception as exc:
                report.warnings.append(f"ai_curator_failed:{type(exc).__name__}:{exc}")

        # 6. Warm embedding cache khi idle.
        try:
            semantic_store = getattr(self.memory_core, "semantic", None)
            embedding_index = getattr(semantic_store, "embedding_index", None)
            ensure_fn = getattr(embedding_index, "ensure_vectors", None)
            text_fn = getattr(semantic_store, "embedding_text_for_item", lambda x: str(getattr(x, "claim", x)))
            if callable(ensure_fn) and callable(getattr(semantic_store, "load", None)):
                items = list(semantic_store.load())
                if items:
                    warmed = ensure_fn(items, text_fn=text_fn)
                    if warmed > 0:
                        report.notes.append(f"embedding_warmed:{warmed}_new_vectors")
        except Exception as exc:
            report.warnings.append(f"embedding_warm_failed:{type(exc).__name__}")

        if not allow_destructive:
            report.notes.append("maintenance_ran_in_audit_mode_no_items_deleted")
        return report

    def _candidate_limit(self, budget: MaintenanceBudget) -> int:
        if budget == "deep":
            return 48
        if budget == "normal":
            return 20
        return 6

    def _run_access_decay(self, *, budget: str, destructive: bool) -> dict[str, Any]:
        """Áp half-life decay lên access_count trong semantic và episodic store.

        Mục đích: hot fact không chiếm ưu thế mãi chỉ vì được recall nhiều trong
        một session. Sau mỗi maintenance, access_count giảm dần theo half-life ~7 ngày.

        Không xóa item — chỉ giảm số. Destructive flag không ảnh hưởng vì đây là
        số liệu thống kê, không phải item.
        """
        now = time.time()
        HALF_LIFE_SECONDS = 7 * 24 * 3600  # 7 ngày
        stores_to_decay = ["semantic", "episodic"]
        summary: dict[str, Any] = {"applied": True, "stores": {}}

        for name in stores_to_decay:
            store = getattr(self.memory_core, name, None)
            if store is None or not hasattr(store, "load") or not hasattr(store, "save"):
                continue
            items = list(store.load())
            changed = 0
            for item in items:
                metadata = getattr(item, "metadata", None)
                if not isinstance(metadata, dict):
                    continue
                count = int(metadata.get("access_count", 0) or 0)
                if count <= 0:
                    continue
                last_accessed = str(metadata.get("last_accessed_at", "") or "")
                # Parse last_accessed — best effort
                last_ts = 0.0
                if last_accessed:
                    try:
                        import time as _time
                        last_ts = _time.mktime(_time.strptime(last_accessed[:19], "%Y-%m-%dT%H:%M:%S"))
                    except Exception:
                        pass
                age = max(0.0, now - last_ts) if last_ts > 0 else HALF_LIFE_SECONDS
                decay_factor = 0.5 ** (age / HALF_LIFE_SECONDS)
                new_count = max(0, int(count * decay_factor))
                if new_count < count:
                    metadata["access_count"] = new_count
                    changed += 1
            if changed and destructive:
                store.save(items)
            summary["stores"][name] = {"items": len(items), "decayed": changed, "saved": changed and destructive}

        return summary

    def _run_prune_audit(self, *, destructive: bool) -> dict[str, Any]:
        """Chạy prune trên các store có load/save.

        Các action:
        - keep: giữ nguyên.
        - archive: hiện chỉ thống kê vì chưa có archive store chuẩn.
        - drop: chỉ drop thật nếu destructive=True.
        """

        store_names = (
            "semantic",
            "relational",
            "episodic",
            "procedural",
            "affective_residue",
            "repair",
        )
        summary: dict[str, Any] = {
            "destructive": destructive,
            "stores": {},
            "dropped_total": 0,
            "archive_candidates_total": 0,
        }

        for name in store_names:
            store = getattr(self.memory_core, name, None)
            if store is None or not hasattr(store, "load"):
                continue
            items = list(store.load())
            kept: list[Any] = []
            dropped = 0
            archive_candidates = 0
            decisions: list[str] = []

            for item in items:
                decision = self.pruner.decide(item)
                decisions.append(f"{decision.action}:{decision.reason}")
                if decision.action == "drop" and destructive:
                    dropped += 1
                    continue
                if decision.action == "archive":
                    archive_candidates += 1
                kept.append(item)

            if destructive and dropped and hasattr(store, "save"):
                store.save(kept)

            summary["stores"][name] = {
                "items": len(items),
                "kept": len(kept),
                "dropped": dropped,
                "archive_candidates": archive_candidates,
                "sample_decisions": decisions[:8],
            }
            summary["dropped_total"] += dropped
            summary["archive_candidates_total"] += archive_candidates

        return summary


__all__ = ["MemoryMaintenanceRunner"]
