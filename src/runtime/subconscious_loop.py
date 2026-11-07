# -*- coding: utf-8 -*-
"""Runtime adapter cho subconscious blackboard/synthesizer.

Phase 0 dựng ThoughtFragment/Blackboard/Synthesizer.
Phase 1-4 thêm worker nội tâm nền.
Phase 5 thêm MetaAudit có quyền dọn Blackboard.
Phase 6 thêm BeliefMutation proposal + commit gate có rollback.
Phase 7 thêm ShadowAccumulator chạy sau synthesis và vẫn phải qua audit.

File này vẫn chưa cắm sâu vào coordinator. Nó là adapter an toàn để test từng phase
trước khi nối vào wake/presence pipeline. Làm khác đi thì nhanh hơn khoảng 20 phút
và mất 2 ngày debug, một giao dịch rất loài người.
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from runtime.diagnostic_logging import safe_log

from cortex.contracts import SubconsciousReport, ThoughtSurface
from cortex.subconscious import (
    ConsciousSynthesizer,
    SubconsciousBlackboard,
    ThoughtFragment,
    ThoughtObject,
    WorkerContext,
    WanderingThoughtEngine,
)
from cortex.subconscious.worker_base import SubconsciousWorker
from cortex.subconscious.thought_fragment import now_timestamp


@dataclass(slots=True, frozen=True)
class SubconsciousTickResult:
    """Kết quả một tick subconscious."""

    submitted_count: int
    expired_ids: tuple[str, ...]
    thought_object: ThoughtObject | None
    surface_seed: dict[str, object] | None
    audit_report: dict[str, object] | None = None
    wandering_result: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "submitted_count": int(self.submitted_count),
            "expired_ids": list(self.expired_ids),
            "thought_object": self.thought_object.to_dict() if self.thought_object else None,
            "surface_seed": self.surface_seed,
            "audit_report": self.audit_report,
            "wandering_result": self.wandering_result,
        }


class SubconsciousLoop:
    """Vòng nền tối thiểu cho worker subconscious.

    Loop này không tự quyết định nói. Nó sinh `ThoughtObject`, `surface_seed`, và
    `audit_report`. SurfaceArbiter/Permission/SpeechCore vẫn là cổng public. Có
    áp lực nội tâm không đồng nghĩa được quyền nói. Một nguyên tắc kỳ lạ nhưng hữu
    ích, ước gì group chat cũng có.
    """

    def __init__(
        self,
        *,
        blackboard: SubconsciousBlackboard | None = None,
        synthesizer: ConsciousSynthesizer | None = None,
        workers: Iterable[SubconsciousWorker] = (),
        wandering_engine: WanderingThoughtEngine | None = None,
        logger: Any = None,
    ) -> None:
        self.blackboard = resolve_dependency(blackboard, lambda: SubconsciousBlackboard())
        self.synthesizer = resolve_dependency(synthesizer, lambda: ConsciousSynthesizer())
        self.workers = tuple(workers or ())
        self.wandering_engine = wandering_engine
        self.logger = logger
        self.failure_counts: dict[str, int] = {}
        self.last_failures: dict[str, dict[str, object]] = {}
        self.last_wandering_result: dict[str, object] | None = None
        self.last_tick = SubconsciousTickResult(
            submitted_count=0,
            expired_ids=(),
            thought_object=None,
            surface_seed=None,
            audit_report=None,
            wandering_result=None,
        )
        self.subconscious_report: SubconsciousReport = SubconsciousReport()
        self.subconscious_seed: dict[str, object] | None = None
        self.audit_report: dict[str, object] | None = None
        self._queued_surface_seeds: list[dict[str, object]] = []

    def submit(self, fragment: ThoughtFragment | Mapping[str, Any]) -> ThoughtFragment:
        """Submit thủ công một fragment vào Blackboard."""

        return self.blackboard.submit(fragment)

    def tick(
        self,
        *,
        frame: Any = None,
        snapshot: Any = None,
        memory_packet: Any = None,
        relation: Any = None,
        internal_state: Any = None,
        now: float | None = None,
        pre_worker_names: Iterable[str] | None = None,
        post_worker_names: Iterable[str] | None = None,
        audit_worker_names: Iterable[str] | None = None,
        run_pre_workers: bool = True,
        run_audit: bool = True,
        run_synthesis: bool = True,
        run_post_workers: bool = True,
        allow_surface_seed: bool = True,
        run_wandering: bool = True,
    ) -> SubconsciousTickResult:
        """Chạy một lượt worker -> audit -> synthesizer -> post-worker -> audit."""

        current = now_timestamp() if now is None else float(now)
        context = WorkerContext(
            frame=frame,
            snapshot=snapshot,
            memory_packet=memory_packet,
            relation=relation,
            internal_state=internal_state,
            audit_report=self.audit_report,
            now=current,
        )
        submitted = 0
        wandering_payload: dict[str, object] | None = None
        if run_wandering and self.wandering_engine is not None:
            try:
                wandering = self.wandering_engine.tick(
                    frame=frame,
                    snapshot=snapshot,
                    memory_packet=memory_packet,
                    internal_state=internal_state,
                    blackboard=self.blackboard,
                    now=current,
                    allow_fragments=True,
                )
                wandering_payload = wandering.to_dict()
                submitted += len(wandering.fragments)
                self.last_wandering_result = wandering_payload
            except Exception as exc:
                self._record_failure("wandering", "wandering_engine", exc)
                wandering_payload = {"error": "wandering_engine_failed", "error_type": type(exc).__name__}
                self.last_wandering_result = wandering_payload

        if run_pre_workers:
            submitted += self._run_workers(
                context,
                after_synthesis=False,
                worker_names=pre_worker_names,
            )

        expired = self.blackboard.decay(now=current)
        if run_audit:
            audit_report = self._run_audit_workers(
                self._context_with_snapshot(
                    frame=frame,
                    snapshot=snapshot,
                    memory_packet=memory_packet,
                    relation=relation,
                    internal_state=internal_state,
                    thought_object=None,
                    audit_report=self.audit_report,
                    now=current,
                ),
                worker_names=audit_worker_names,
            )
            self.audit_report = audit_report or self.audit_report

        thought_object = None
        if run_synthesis:
            thought_object = self.synthesizer.synthesize_from_blackboard(self.blackboard, now=current)

        # Phase 2/7: một số worker, đặc biệt RehearsalAgent và ShadowAccumulator,
        # cần ThoughtObject đã qua cổ chai + snapshot Blackboard. Chạy chúng sau
        # synthesis rồi audit lại trước khi synthesize lần cuối.
        if thought_object is not None and run_post_workers:
            post_context = self._context_with_snapshot(
                frame=frame,
                snapshot=snapshot,
                memory_packet=memory_packet,
                relation=relation,
                internal_state=internal_state,
                thought_object=thought_object,
                audit_report=self.audit_report,
                now=current,
            )
            post_submitted = self._run_workers(
                post_context,
                after_synthesis=True,
                worker_names=post_worker_names,
            )
            submitted += post_submitted
            if post_submitted and run_audit:
                post_audit_report = self._run_audit_workers(
                    self._context_with_snapshot(
                        frame=frame,
                        snapshot=snapshot,
                        memory_packet=memory_packet,
                        relation=relation,
                        internal_state=internal_state,
                        thought_object=thought_object,
                        audit_report=self.audit_report,
                        now=current,
                    ),
                    worker_names=audit_worker_names,
                )
                self.audit_report = post_audit_report or self.audit_report
                thought_object = self.synthesizer.synthesize_from_blackboard(self.blackboard, now=current)

        inner_court_report = self._inner_court_report_payload()
        if inner_court_report:
            self.audit_report = dict(self.audit_report or {})
            self.audit_report["lane3_inner_court"] = inner_court_report

        surface_seed = None
        if allow_surface_seed and thought_object and thought_object.should_surface:
            surface_seed = thought_object.to_surface_seed()
        self.subconscious_seed = surface_seed
        if surface_seed:
            self._queued_surface_seeds.append(dict(surface_seed))
            self._queued_surface_seeds = self._queued_surface_seeds[-4:]
        self.subconscious_report = self._compose_report(thought_object, surface_seed)
        self.last_tick = SubconsciousTickResult(
            submitted_count=submitted,
            expired_ids=expired,
            thought_object=thought_object,
            surface_seed=surface_seed,
            audit_report=self.audit_report,
            wandering_result=wandering_payload,
        )
        return self.last_tick

    def latest_report(self) -> SubconsciousReport:
        return self.subconscious_report

    def _inner_court_report_payload(self) -> dict[str, object] | None:
        """Expose Lane 3 API synthesis report cho debug/audit nội bộ."""

        report = getattr(self.synthesizer, "last_inner_court_report", None)
        to_dict = getattr(report, "to_dict", None)
        if not callable(to_dict):
            return None
        try:
            payload = to_dict()
        except Exception as exc:
            self._record_failure("projection", "inner_court_report", exc)
            return None
        return payload if isinstance(payload, dict) and payload else None

    def pop_surface(self) -> dict[str, object] | None:
        if not self._queued_surface_seeds:
            return None
        return self._queued_surface_seeds.pop(0)

    def stop(self) -> None:
        return None

    def diagnostics(self) -> dict[str, object]:
        return {
            "workers": len(self.workers),
            "queued_surface_seeds": len(self._queued_surface_seeds),
            "failure_counts": dict(self.failure_counts),
            "last_failures": {key: dict(value) for key, value in self.last_failures.items()},
            "last_tick": self.last_tick.to_dict(),
        }

    def _record_failure(self, stage: str, component: str, exc: BaseException) -> None:
        key = f"{stage}:{component}"
        self.failure_counts[key] = int(self.failure_counts.get(key, 0)) + 1
        self.last_failures[key] = {
            "stage": stage,
            "component": component,
            "error_type": type(exc).__name__,
            "message": str(exc)[:240],
            "count": self.failure_counts[key],
        }
        safe_log(self.logger, "warning", "Subconscious failure stage=%s component=%s: %s", stage, component, exc)

    def _context_with_snapshot(
        self,
        *,
        frame: Any,
        snapshot: Any,
        memory_packet: Any,
        relation: Any,
        internal_state: Any,
        thought_object: Any,
        audit_report: Any,
        now: float,
    ) -> WorkerContext:
        """Tạo WorkerContext có snapshot Blackboard bất biến."""

        return WorkerContext(
            frame=frame,
            snapshot=snapshot,
            memory_packet=memory_packet,
            relation=relation,
            internal_state=internal_state,
            thought_object=thought_object,
            blackboard_snapshot=self.blackboard.snapshot(now=now),
            audit_report=audit_report,
            now=now,
        )

    def _run_workers(
        self,
        context: WorkerContext,
        *,
        after_synthesis: bool,
        worker_names: Iterable[str] | None = None,
    ) -> int:
        """Chạy nhóm worker phù hợp với pass hiện tại và submit fragment.

        Worker lỗi bị cô lập. MetaAudit sẽ bắt rác nhận thức, còn loop runtime không
        nên chết vì một worker bẩn. Đó là lý tưởng thấp, nhưng chạy được.
        """

        submitted = 0
        selected = {str(name).strip() for name in (worker_names or ()) if str(name).strip()}
        for worker in self.workers:
            if bool(getattr(worker, "audit_blackboard_worker", False)):
                continue
            if selected and str(getattr(worker, "name", "") or "") not in selected:
                continue
            wants_post = bool(getattr(worker, "run_after_synthesis", False))
            if wants_post != bool(after_synthesis):
                continue
            try:
                fragments = tuple(worker.run(context) or ())
            except Exception as exc:
                self._record_failure("worker", str(getattr(worker, "name", type(worker).__name__) or type(worker).__name__), exc)
                continue
            for fragment in fragments:
                try:
                    self.blackboard.submit(fragment)
                except Exception as exc:
                    self._record_failure("submit", str(getattr(worker, "name", type(worker).__name__) or type(worker).__name__), exc)
                    continue
                submitted += 1
        return submitted

    def _run_audit_workers(
        self,
        context: WorkerContext,
        *,
        worker_names: Iterable[str] | None = None,
    ) -> dict[str, object] | None:
        """Chạy các worker audit có quyền mutate Blackboard."""

        merged: dict[str, object] | None = None
        selected = {str(name).strip() for name in (worker_names or ()) if str(name).strip()}
        for worker in self.workers:
            if not bool(getattr(worker, "audit_blackboard_worker", False)):
                continue
            if selected and str(getattr(worker, "name", "") or "") not in selected:
                continue
            audit = getattr(worker, "audit_blackboard", None)
            if not callable(audit):
                continue
            try:
                result = audit(self.blackboard, context.blackboard_snapshot, context)
            except Exception as exc:
                self._record_failure("audit", str(getattr(worker, "name", type(worker).__name__) or type(worker).__name__), exc)
                continue
            to_dict = getattr(result, "to_dict", None)
            payload = to_dict() if callable(to_dict) else result
            if isinstance(payload, dict):
                merged = payload if merged is None else {**merged, **payload}
        return merged

    def _compose_report(
        self,
        thought_object: ThoughtObject | None,
        surface_seed: dict[str, object] | None,
    ) -> SubconsciousReport:
        surface = self._surface_from_seed(surface_seed, thought_object)
        surface_pressure = float(getattr(thought_object, "surface_pressure", 0.0) or 0.0)
        suppressed_pressure = float(getattr(thought_object, "suppressed_pressure", 0.0) or 0.0)
        route = str(getattr(thought_object, "recommended_route", "") or "")
        inner_court = self._inner_court_report_payload()
        continuity_notes = tuple(
            item for item in (
                "subconscious_loop",
                f"subconscious_route={route}" if route else "",
                f"subconscious_fragments={len(getattr(thought_object, 'fragments', ()) or ())}" if thought_object is not None else "",
                f"lane3_inner_court={'ok' if bool((inner_court or {}).get('ok')) else 'fallback'}" if inner_court else "",
                "wandering_thought" if self.last_wandering_result else "",
            ) if item
        )
        return SubconsciousReport(
            latest_seed=surface,
            recent_seeds=(surface,) if surface.mode else (),
            surface_pressure=surface_pressure if surface_pressure > 0.0 else float(getattr(surface, "pressure", 0.0) or 0.0),
            suppression_reason="" if surface.mode else (route or "no_surface_seed"),
            latest_thought=thought_object if thought_object is not None else SubconsciousReport().latest_thought,
            recent_thoughts=(thought_object,) if thought_object is not None else (),
            stream_tension=max(surface_pressure, suppressed_pressure),
            unresolved_count=max(0, len(getattr(thought_object, "fragments", ()) or ())) if thought_object is not None else 0,
            continuity_notes=continuity_notes,
        )

    def _surface_from_seed(
        self,
        surface_seed: dict[str, object] | None,
        thought_object: ThoughtObject | None,
    ) -> ThoughtSurface:
        if not surface_seed:
            return ThoughtSurface()
        evidence = tuple(
            str(item) for item in (
                surface_seed.get("evidence")
                or surface_seed.get("evidence_ids")
                or ()
            )
            if str(item or "").strip()
        )
        route = str(surface_seed.get("recommended_route") or getattr(thought_object, "recommended_route", "") or "")
        return ThoughtSurface(
            mode="subconscious_seed",
            topic_seed=str(surface_seed.get("topic_seed") or getattr(thought_object, "topic_seed", "") or ""),
            pressure=float(surface_seed.get("pressure", getattr(thought_object, "surface_pressure", 0.0)) or 0.0),
            novelty=float(surface_seed.get("novelty", getattr(thought_object, "novelty", 0.0)) or 0.0),
            evidence=evidence,
            created_at=float(getattr(thought_object, "created_at", 0.0) or 0.0),
            thought_id=str(getattr(thought_object, "id", "") or ""),
            tension=float(getattr(thought_object, "suppressed_pressure", 0.0) or 0.0),
            unresolved_reason=route,
            partial_text=str(surface_seed.get("partial_text") or getattr(thought_object, "partial_text", "") or ""),
            recurrence_count=max(1, len(getattr(thought_object, "fragments", ()) or ())) if thought_object is not None else 0,
            momentum_score=float(surface_seed.get("momentum_score", getattr(thought_object, "momentum_score", 0.0)) or 0.0),
            impulse_kind=str(surface_seed.get("impulse_kind") or "inner_whim"),
            root_source=str(surface_seed.get("root_source") or "subconscious_synthesizer"),
            action_opportunity=route,
            public_seed=str(surface_seed.get("public_seed") or getattr(thought_object, "public_seed", "") or ""),
        )
