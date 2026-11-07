from __future__ import annotations

"""Turn-owned memory pipeline for realtime chat.

A user turn enters this service once. Realtime admission is side-effect free;
expensive extraction may run concurrently, but all per-turn durable memory
mutation crosses one reducer boundary.

Ownership is intentionally boring:
- realtime: notify + deterministic preview + enqueue only;
- semantic worker: parse/plan only;
- curator worker: profile extraction/proposal only;
- mutation reducer: canonical memory commit + profile mutation only.

Boring ownership is a feature. Threads are already dramatic enough without
letting each one become a tiny sovereign state.
"""

import itertools
import os
from runtime.curator_inbox import CuratorInbox
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from memory.memory_kernel import MemoryKernel
from runtime.background_memory_digest_kernel import (
    BackgroundMemoryDigestKernelConfig,
    BackgroundMemoryDigestKernelService,
    MemoryDigestKernelJob,
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return max(1, default)


@dataclass(frozen=True, slots=True)
class TurnMemoryPipelineConfig:
    enabled: bool = field(default_factory=lambda: _env_flag("ERIS_TURN_MEMORY_PIPELINE", True))
    curator_enabled: bool = field(default_factory=lambda: _env_flag("ERIS_LIVING_USER_CURATOR_LANE", True))
    curator_max_queue: int = field(default_factory=lambda: _env_int("ERIS_LIVING_USER_CURATOR_MAX_QUEUE", 64))
    mutation_max_queue: int = field(default_factory=lambda: _env_int("ERIS_TURN_MEMORY_MUTATION_MAX_QUEUE", 128))


@dataclass(frozen=True, slots=True)
class TurnMemoryEnvelope:
    text: str
    turn_id: str
    evidence_ids: tuple[str, ...]
    source: str = "chat_turn"
    priority: str = "normal"
    context: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    envelope_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class TurnMemoryMutation:
    """One proposed state mutation waiting for the single reducer."""

    kind: str
    envelope_id: str
    payload: Any
    priority: str = "normal"
    context: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    mutation_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class TurnMemoryReceipt:
    accepted: bool
    envelope_id: str = ""
    preview_decisions: int = 0
    preview_requests: int = 0
    semantic_enqueued: bool = False
    curator_enqueued: bool = False
    reason: str = ""

    def __bool__(self) -> bool:
        return self.accepted

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "envelope_id": self.envelope_id,
            "preview_decisions": self.preview_decisions,
            "preview_requests": self.preview_requests,
            "semantic_enqueued": self.semantic_enqueued,
            "curator_enqueued": self.curator_enqueued,
            "reason": self.reason,
        }


@dataclass(slots=True)
class TurnMemoryPipelineStats:
    admitted: int = 0
    dropped: int = 0
    curator_enqueued: int = 0
    curator_processed: int = 0
    curator_failed: int = 0
    curator_proposed_operations: int = 0
    curator_applied: int = 0
    mutation_enqueued: int = 0
    mutation_processed: int = 0
    mutation_failed: int = 0
    semantic_committed: int = 0
    last_curator_latency_s: float = 0.0
    last_mutation_latency_s: float = 0.0
    last_error: str = ""
    last_receipt: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "dropped": self.dropped,
            "curator_enqueued": self.curator_enqueued,
            "curator_processed": self.curator_processed,
            "curator_failed": self.curator_failed,
            "curator_proposed_operations": self.curator_proposed_operations,
            "curator_applied": self.curator_applied,
            "mutation_enqueued": self.mutation_enqueued,
            "mutation_processed": self.mutation_processed,
            "mutation_failed": self.mutation_failed,
            "semantic_committed": self.semantic_committed,
            "last_curator_latency_s": self.last_curator_latency_s,
            "last_mutation_latency_s": self.last_mutation_latency_s,
            "last_error": self.last_error,
            "last_receipt": dict(self.last_receipt),
            "semantic": {},
        }


class TurnMemoryPipeline:
    """Single ingress and single mutation owner for per-user-turn memory."""

    def __init__(
        self,
        memory_kernel: MemoryKernel,
        *,
        semantic_digest: BackgroundMemoryDigestKernelService | None = None,
        living_user: Any = None,
        logger: Any = None,
        config: TurnMemoryPipelineConfig | None = None,
    ) -> None:
        self.memory_kernel = memory_kernel
        self.semantic_digest = semantic_digest
        self.living_user = living_user
        self.logger = logger
        self.config = config or TurnMemoryPipelineConfig()
        self.enabled = bool(
            self.config.enabled
            and memory_kernel is not None
            and (semantic_digest is not None or living_user is not None)
        )
        self.stats = TurnMemoryPipelineStats()
        self._curator_queue: queue.Queue[TurnMemoryEnvelope] = queue.Queue(
            maxsize=max(1, int(self.config.curator_max_queue))
        )
        self._mutation_queue: queue.PriorityQueue[tuple[int, int, TurnMemoryMutation]] = queue.PriorityQueue(
            maxsize=max(1, int(self.config.mutation_max_queue))
        )
        self._mutation_sequence = itertools.count()
        # Inbox SQLite nằm cùng vùng dữ liệu MemoryCore; với fake kernel trong
        # test không có base_dir thì chạy queue RAM như hành vi cũ.
        core = getattr(memory_kernel, "core", None)
        base_dir = str(getattr(core, "base_dir", "") or "").strip()
        db_path = os.getenv("ERIS_CURATOR_INBOX_DB", "").strip()
        self._curator_inbox = CuratorInbox(db_path or os.path.join(base_dir, "curator_inbox.sqlite3")) if (db_path or base_dir) else None
        self._queued_profile_ops: set[str] = set()
        self._proposal_lock = threading.RLock()
        self._stop = threading.Event()
        self._curator_thread: threading.Thread | None = None
        self._mutation_thread: threading.Thread | None = None

        # The semantic worker is a planner when owned by this pipeline. Legacy
        # standalone digest services still commit directly because commit_sink
        # defaults to None.
        if self.semantic_digest is not None:
            self.semantic_digest.commit_sink = self._submit_semantic_plan

    @property
    def curator_enabled(self) -> bool:
        return bool(
            self.enabled
            and self.config.curator_enabled
            and self.living_user is not None
            and bool(getattr(getattr(self.living_user, "config", None), "auto_observe_turns", True))
        )

    def start(self) -> None:
        if not self.enabled:
            return
        self._stop.clear()
        if not (self._mutation_thread and self._mutation_thread.is_alive()):
            self._mutation_thread = threading.Thread(
                target=self._run_mutation_reducer,
                name="ERIS-TurnMemoryReducer",
                daemon=True,
            )
            self._mutation_thread.start()
        if self.semantic_digest is not None:
            self.semantic_digest.start()
        if self.curator_enabled and not (self._curator_thread and self._curator_thread.is_alive()):
            self._curator_thread = threading.Thread(
                target=self._run_curator,
                name="ERIS-LivingUserCurator",
                daemon=True,
            )
            self._curator_thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        # Stop semantic production first. Then ask curator to stop accepting more
        # work, and finally let the reducer drain already-produced mutations.
        if self.semantic_digest is not None:
            self.semantic_digest.stop(timeout=timeout)
        self._stop.set()
        if self._curator_thread is not None and self._curator_thread.is_alive():
            self._curator_thread.join(timeout=max(0.0, float(timeout)))
        if self._mutation_thread is not None and self._mutation_thread.is_alive():
            self._mutation_thread.join(timeout=max(0.0, float(timeout)))

    close = stop
    shutdown = stop

    def notify_remote_inflight(self, inflight: bool) -> None:
        if self.semantic_digest is not None:
            self.semantic_digest.notify_remote_inflight(bool(inflight))

    def notify_user_input(self) -> None:
        if self.semantic_digest is not None:
            self.semantic_digest.notify_user_input()

    def enqueue_user_turn(
        self,
        text: str,
        *,
        turn_id: str = "",
        source: str = "chat_turn",
        priority: str = "normal",
        context: Mapping[str, Any] | None = None,
        storage_permission: bool = True,
    ) -> TurnMemoryReceipt:
        raw = str(text or "").strip()
        actual_turn_id = str(turn_id or "").strip()
        if not self.enabled or not raw:
            receipt = TurnMemoryReceipt(False, reason="pipeline_disabled_or_empty")
            self._remember_receipt(receipt, dropped=True)
            return receipt

        from memory.claim_provenance import classify_ingress
        # API này sở hữu USER turn; assistant/observer phải dùng lane riêng.
        # Reject trước cả curator, vì curator cũng có quyền ghi user profile.
        if classify_ingress(source, turn_id=actual_turn_id)["origin_kind"] != "user_statement":
            receipt = TurnMemoryReceipt(False, reason="non_user_source_for_user_turn_pipeline")
            self._remember_receipt(receipt, dropped=True)
            return receipt

        # Preempt stale digest work before realtime even performs its cheap plan.
        self.notify_user_input()
        evidence_ids = (actual_turn_id,) if actual_turn_id else ()
        # Mọi lane (semantic/curator/preview) dùng cùng nguồn của turn.
        actual_context = dict(context or {})
        actual_context["role"] = "user"
        actual_context["claim_provenance"] = classify_ingress(source, turn_id=actual_turn_id)
        envelope = TurnMemoryEnvelope(
            text=raw,
            turn_id=actual_turn_id,
            evidence_ids=evidence_ids,
            source=str(source or "chat_turn"),
            priority=str(priority or "normal"),
            context=actual_context,
        )

        # Deterministic, zero-write preview. It exists only to prioritize obvious
        # facts; the semantic lane later creates the canonical commit plan.
        preview = self.memory_kernel.plan_ingest(
            raw,
            evidence_ids=list(evidence_ids),
            turn_id=actual_turn_id or None,
            source=envelope.source,
            mode="fast",
            allow_candidate=False,
            storage_permission=bool(storage_permission),
            permission_required=False,
            context={**dict(envelope.context), "turn_memory_envelope_id": envelope.envelope_id},
        )

        semantic_ok = False
        semantic = self.semantic_digest
        if semantic is not None and bool(getattr(semantic, "enabled", False)):
            semantic_priority = "high" if preview.request_count > 0 else envelope.priority
            semantic_ok = bool(semantic.enqueue(
                envelope.text,
                turn_id=envelope.turn_id,
                evidence_ids=list(envelope.evidence_ids),
                role="user",
                source=envelope.source,
                priority=semantic_priority if semantic_priority in {"high", "normal", "low"} else "normal",
                context={**dict(envelope.context), "turn_memory_envelope_id": envelope.envelope_id},
            ))

        curator_ok = self._enqueue_curator(envelope)
        accepted = semantic_ok or curator_ok
        receipt = TurnMemoryReceipt(
            accepted=accepted,
            envelope_id=envelope.envelope_id,
            preview_decisions=preview.decision_count,
            preview_requests=preview.request_count,
            semantic_enqueued=semantic_ok,
            curator_enqueued=curator_ok,
            reason="background_admitted" if accepted else "no_background_lane",
        )
        self._remember_receipt(receipt, dropped=not accepted)
        return receipt

    def enqueue_assistant_turn(self, text: str, *, turn_id: str,
                               context: Mapping[str, Any] | None = None) -> bool:
        """Chỉ gọi sau delivery; curator đọc lời assistant, không ingest user fact."""
        if not self.enabled or not str(text or "").strip() or not str(turn_id or "").strip():
            return False
        return self._enqueue_curator(TurnMemoryEnvelope(
            text=str(text), turn_id=turn_id, evidence_ids=(turn_id,),
            source="delivered_assistant", priority="low",
            context={**dict(context or {}), "role": "assistant"},
        ))

    def _enqueue_curator(self, envelope: TurnMemoryEnvelope) -> bool:
        if not self.curator_enabled:
            return False
        capture = getattr(self.living_user, "capture_interpretation_context", None)
        if callable(capture) and "interpretation_context" not in envelope.context:
            try:
                captured = capture(evidence_ids=envelope.evidence_ids, cutoff=envelope.created_at, context=envelope.context)
                if not isinstance(captured, Mapping):
                    raise TypeError("invalid_context_snapshot")
            except Exception as exc:
                captured = {"source_context_available": False, "error_type": type(exc).__name__}
            envelope = replace(envelope, context={**dict(envelope.context), "interpretation_context": captured})
        inbox = self._curator_inbox
        if inbox is not None:
            try:
                inbox.put(envelope)  # Write-ahead trước khi trả accepted.
            except Exception as exc:
                self._log("warning", "living_user_curator_inbox_write_failed:%s:%s",
                          envelope.envelope_id, type(exc).__name__)
                return False
        try:
            self._curator_queue.put_nowait(envelope)
            self.stats.curator_enqueued += 1
            return True
        except queue.Full:
            # Inbox durable giữ envelope. Background polling sẽ xử lý khi hết tắc.
            self._log("warning", "living_user_curator_queue_full:%s", envelope.envelope_id)
            return inbox is not None

    @staticmethod
    def _priority_rank(priority: str) -> int:
        return {"high": 0, "normal": 1, "low": 2}.get(str(priority or "normal"), 1)

    def _enqueue_mutation(self, mutation: TurnMemoryMutation) -> bool:
        with self._proposal_lock:
            if mutation.kind == "profile_ops" and mutation.envelope_id in self._queued_profile_ops:
                return True
            try:
                self._mutation_queue.put_nowait((
                    self._priority_rank(mutation.priority),
                    next(self._mutation_sequence),
                    mutation,
                ))
                if mutation.kind == "profile_ops":
                    self._queued_profile_ops.add(mutation.envelope_id)
                self.stats.mutation_enqueued += 1
                return True
            except queue.Full:
                self.stats.mutation_failed += 1
                self.stats.last_error = f"mutation_queue_full:{mutation.kind}:{mutation.envelope_id}"
                self._log("warning", "turn_memory_mutation_queue_full:%s:%s", mutation.kind, mutation.envelope_id)
                return False

    def _submit_semantic_plan(self, plan: Any, job: MemoryDigestKernelJob) -> bool:
        context = dict(getattr(plan, "context", {}) or {})
        envelope_id = str(context.get("turn_memory_envelope_id") or job.job_id)
        return self._enqueue_mutation(TurnMemoryMutation(
            kind="semantic_plan",
            envelope_id=envelope_id,
            payload=plan,
            priority=str(getattr(job, "priority", "normal") or "normal"),
            context={"background_job_id": job.job_id, **context},
        ))

    def _run_curator(self) -> None:
        while not self._stop.is_set():
            queued = True
            try:
                envelope = self._curator_queue.get(timeout=0.20)
            except queue.Empty:
                queued = False
                due = self._curator_inbox.due() if self._curator_inbox is not None else None
                if due is None:
                    continue
                envelope_id, data, status, serialized_ops = due
                if status == "proposed":
                    # Mutation đã được gửi reducer: không enqueue lại trong RAM
                    # trong khi reducer vẫn đang áp dụng nó.
                    with self._proposal_lock:
                        waiting = envelope_id in self._queued_profile_ops
                    if waiting:
                        time.sleep(0.20)
                        continue
                    # Có proposal đã lưu nhưng mutation reducer chưa ACK (restart
                    # hoặc queue đầy). Khôi phục ops, KHÔNG gọi lại model.
                    from memory.living_user.models import ProfileOperation
                    from memory.living_user.action_memory import ActionMemoryOperation
                    operations = tuple(
                        ActionMemoryOperation.from_dict(op) if op.get("memory_kind") == "action"
                        else ProfileOperation.from_dict(op) for op in serialized_ops
                    )
                    self._enqueue_mutation(TurnMemoryMutation(
                        kind="profile_ops", envelope_id=envelope_id, payload=operations,
                        priority=data.get("priority", "normal"), context=data.get("context", {})))
                    continue
                envelope = TurnMemoryEnvelope(
                    text=data["text"], turn_id=data["turn_id"], evidence_ids=tuple(data["evidence_ids"]),
                    source=data.get("source", "chat_turn"), priority=data.get("priority", "normal"),
                    context=data.get("context", {}), created_at=data.get("created_at", time.time()),
                    envelope_id=envelope_id)
            try:
                self._process_curator(envelope)
            except Exception as exc:  # background tuyệt đối không chết
                self.stats.curator_failed += 1
                self.stats.last_error = f"curator:{type(exc).__name__}"
                if self._curator_inbox is not None:
                    self._curator_inbox.retry(envelope.envelope_id)
                self._log("warning", "living_user_curator_deferred:%s:%s", envelope.envelope_id, type(exc).__name__)
            finally:
                if queued:
                    self._curator_queue.task_done()

    def _process_curator(self, envelope: TurnMemoryEnvelope) -> None:
        if self.living_user is None:
            return
        proposer = getattr(self.living_user, "propose_turn_operations", None)
        if not callable(proposer):
            raise RuntimeError("living_user_missing_proposal_boundary")
        started = time.perf_counter()
        proposal_context = {
            **dict(envelope.context),
            "source": envelope.source,
            "turn_memory_envelope_id": envelope.envelope_id,
            "background_lane": "memory_curator",
            "evidence_created_at": envelope.created_at,
        }
        operations = tuple(proposer(
            envelope.text,
            evidence_ids=envelope.evidence_ids,
            context=proposal_context,
        ) or ())
        self.stats.curator_processed += 1
        self.stats.curator_proposed_operations += len(operations)
        self.stats.last_curator_latency_s = max(0.0, time.perf_counter() - started)

        submitted = True
        if self._curator_inbox is not None:
            if operations:
                # Durable outbox trước reducer: sau restart không gọi lại model.
                self._curator_inbox.proposed(envelope.envelope_id, [op.to_dict() for op in operations])
            else:
                # success_empty khác hoàn toàn timeout/parse_error.
                self._curator_inbox.done(envelope.envelope_id)
        if operations:
            submitted = self._enqueue_mutation(TurnMemoryMutation(
                kind="profile_ops",
                envelope_id=envelope.envelope_id,
                payload=operations,
                priority=envelope.priority,
                context={key: value for key, value in proposal_context.items()
                         if key not in {"interpretation_context", "recent_conversation"}},
            ))
        self._log(
            "debug",
            "living_user_curator_proposed:%s:ops=%s:submitted=%s:latency=%.3fs",
            envelope.envelope_id,
            len(operations),
            int(bool(submitted)),
            self.stats.last_curator_latency_s,
        )

    def _run_mutation_reducer(self) -> None:
        while not self._stop.is_set() or not self._mutation_queue.empty():
            try:
                _rank, _seq, mutation = self._mutation_queue.get(timeout=0.20)
            except queue.Empty:
                continue
            try:
                self._apply_mutation(mutation)
            except Exception as exc:  # reducer lỗi vẫn giữ durable proposal chờ retry
                if mutation.kind == "profile_ops":
                    with self._proposal_lock:
                        self._queued_profile_ops.discard(mutation.envelope_id)
                    if self._curator_inbox is not None:
                        self._curator_inbox.retry(mutation.envelope_id)
                self.stats.mutation_failed += 1
                self.stats.last_error = f"reducer:{type(exc).__name__}:{exc}"
                self._log(
                    "exception",
                    "turn_memory_mutation_failed:%s:%s",
                    mutation.kind,
                    mutation.envelope_id,
                    exc_info=exc,
                )
            finally:
                self._mutation_queue.task_done()

    def _apply_mutation(self, mutation: TurnMemoryMutation) -> None:
        started = time.perf_counter()
        if mutation.kind == "semantic_plan":
            result = self.memory_kernel.commit_plan(
                mutation.payload,
                observe_living_user=False,
                sync_living_user_semantic=True,
            )
            committed = int(getattr(result, "committed_count", 0) or 0)
            self.stats.semantic_committed += committed
            # Keep the compatibility digest stats useful even though the actual
            # commit owner has moved one level up.
            semantic_stats = getattr(self.semantic_digest, "stats", None)
            if semantic_stats is not None:
                semantic_stats.committed += committed
                semantic_stats.last_result = result.to_dict() if hasattr(result, "to_dict") else {}
            self._log(
                "debug",
                "turn_memory_reducer_semantic:%s:committed=%s",
                mutation.envelope_id,
                committed,
            )
        elif mutation.kind == "profile_ops":
            applier = getattr(self.living_user, "apply_turn_operations", None) if self.living_user is not None else None
            if not callable(applier):
                raise RuntimeError("living_user_missing_apply_boundary")
            result = applier(mutation.payload, trace_context=mutation.context)
            applied = int(getattr(result, "applied", 0) or 0)
            self.stats.curator_applied += applied
            if self._curator_inbox is not None:
                self._curator_inbox.done(mutation.envelope_id)
            with self._proposal_lock:
                self._queued_profile_ops.discard(mutation.envelope_id)
            self._log(
                "debug",
                "turn_memory_reducer_profile:%s:applied=%s",
                mutation.envelope_id,
                applied,
            )
        else:
            raise ValueError(f"unknown_turn_memory_mutation:{mutation.kind}")

        self.stats.mutation_processed += 1
        self.stats.last_mutation_latency_s = max(0.0, time.perf_counter() - started)

    def drain_once_for_tests(self) -> int:
        """Drain curator proposals then reducer mutations without background threads."""
        processed = 0
        while True:
            try:
                envelope = self._curator_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._process_curator(envelope)
                processed += 1
            finally:
                self._curator_queue.task_done()
        processed += self.drain_mutations_for_tests()
        return processed

    def drain_mutations_for_tests(self) -> int:
        processed = 0
        while True:
            try:
                _rank, _seq, mutation = self._mutation_queue.get_nowait()
            except queue.Empty:
                return processed
            try:
                self._apply_mutation(mutation)
                processed += 1
            finally:
                self._mutation_queue.task_done()

    def snapshot(self) -> dict[str, Any]:
        payload = self.stats.to_dict()
        semantic = self.semantic_digest
        if semantic is not None and hasattr(semantic, "stats"):
            to_dict = getattr(semantic.stats, "to_dict", None)
            payload["semantic"] = to_dict() if callable(to_dict) else {}
        payload["curator_queue_depth"] = self._curator_queue.qsize()
        payload["curator_durable_backlog"] = self._curator_inbox.count() if self._curator_inbox is not None else None
        payload["mutation_queue_depth"] = self._mutation_queue.qsize()
        payload["curator_enabled"] = self.curator_enabled
        payload["mutation_owner"] = "turn_memory_reducer"
        return payload

    def _remember_receipt(self, receipt: TurnMemoryReceipt, *, dropped: bool) -> None:
        if dropped:
            self.stats.dropped += 1
        else:
            self.stats.admitted += 1
        self.stats.last_receipt = receipt.to_dict()

    def _log(self, level: str, message: str, *args: Any, **kwargs: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(message, *args, **kwargs)
            except Exception:
                pass


def build_turn_memory_pipeline(
    *,
    memory_kernel: MemoryKernel | None,
    semantic_parser: Any = None,
    living_user: Any = None,
    logger: Any = None,
    idle_probe: Any = None,
    config: TurnMemoryPipelineConfig | None = None,
) -> TurnMemoryPipeline | None:
    if memory_kernel is None:
        return None
    # Always keep a semantic planning lane. If no enrichment parser exists the
    # digest policy stays deterministic, but canonical persistence still remains
    # off the realtime thread.
    semantic_digest = BackgroundMemoryDigestKernelService(
        memory_kernel,
        config=BackgroundMemoryDigestKernelConfig(),
        logger=logger,
        idle_probe=idle_probe,
        observe_living_user=False,
    )
    pipeline = TurnMemoryPipeline(
        memory_kernel,
        semantic_digest=semantic_digest,
        living_user=living_user,
        logger=logger,
        config=config,
    )
    if not pipeline.enabled:
        return None
    pipeline.start()
    return pipeline


__all__ = [
    "TurnMemoryEnvelope",
    "TurnMemoryMutation",
    "TurnMemoryPipeline",
    "TurnMemoryPipelineConfig",
    "TurnMemoryPipelineStats",
    "TurnMemoryReceipt",
    "build_turn_memory_pipeline",
]
