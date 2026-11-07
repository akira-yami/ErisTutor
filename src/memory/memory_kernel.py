"""
memory_kernel.py
================

MemoryKernel là cổng thần kinh duy nhất cho memory runtime.

Mục tiêu của file này:
- Runtime không gọi trực tiếp MemoryCommitPolicy, MemoryQueryExecutor, PacketBuilder,
  CandidateStore, Pruner lung tung nữa.
- Runtime chỉ biết 4 động từ:
    ingest()       : đưa text/evidence vào memory lifecycle.
    answer()       : trả lời deterministic câu hỏi trí nhớ.
    build_packet() : dựng resonance/context packet cho prompt/cognition.
    maintain()     : chạy chưng cất/prune/repair nền.

Lưu ý quan trọng:
- MemoryKernel KHÔNG thay model thành quan tòa trí nhớ.
- Parser/model chỉ được đọc nghĩa ban đầu trong semantic_digest.
- Ontology, entailment, worthiness, retention và commit vẫn do code quyết.

Đây là facade thật, không phải thêm một lớp trang trí chỉ để repo trông có vẻ
kiến trúc hơn. Thế giới đã có đủ facade vô dụng rồi.
"""
from __future__ import annotations
from core.di import resolve_dependency

import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from .memory_cognition import CommitPolicy, MemoryCognitionCore, MemoryQuery
from .memory_commit_policy import MemoryCommitPolicy
from .memory_kernel_contracts import (
    MaintenanceBudget,
    MemoryAnswerResult,
    MemoryIngestMode,
    MemoryIngestPlan,
    MemoryIngestResult,
    MemoryKernelConfig,
    PublicMemoryContext,
    MemoryMaintenanceReport,
)
from .memory_maintenance import MemoryMaintenanceRunner
from .memory_packet_builder import MemoryPacket
from .memory_query_compiler import MemoryQueryCompiler
from .memory_query_executor import MemoryQueryExecutor
from .memory_recall_policy import MemoryRecallSurfacePolicy
from .memory_turn_annotator import TurnAnnotator
from .memory_turn_graph import TurnGraph
from .predicate_registry import PredicateRegistry, get_default_registry


@dataclass(slots=True)
class MemoryKernelDebugSnapshot:
    """Snapshot nhỏ cho dev mode.

    Không đưa thẳng ra public chat. Đây là để log/debug UI.
    """

    base_dir: str
    has_semantic_parser: bool
    fast_policy_has_parser: bool
    digest_policy_has_parser: bool
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_dir": self.base_dir,
            "has_semantic_parser": self.has_semantic_parser,
            "fast_policy_has_parser": self.fast_policy_has_parser,
            "digest_policy_has_parser": self.digest_policy_has_parser,
            "timestamp": self.timestamp,
        }


class MemoryKernel:
    """Facade chính cho toàn bộ memory runtime.

    Cách dùng khuyến nghị:

        kernel = MemoryKernel(base_dir="memory_core", semantic_parser=parser)
        kernel.ingest(user_text, mode="fast")
        kernel.ingest(user_text, mode="semantic_digest")  # worker nền gọi
        answer = kernel.answer("tôi tên gì", session_ledger=ledger)
        packet = kernel.build_packet("current task", budget_mode="normal")
        kernel.maintain(reason="idle", budget="small")

    Nếu runtime cũ vẫn đang truyền MemoryCognitionCore vào, dùng from_existing_core().
    """

    def __init__(
        self,
        base_dir: str | None = None,
        *,
        core: MemoryCognitionCore | None = None,
        semantic_parser: Any | None = None,
        config: MemoryKernelConfig | None = None,
        commit_policy: CommitPolicy | None = None,
        query_compiler: MemoryQueryCompiler | None = None,
        query_executor: MemoryQueryExecutor | None = None,
        surface_policy: MemoryRecallSurfacePolicy | None = None,
        turn_annotator: TurnAnnotator | None = None,
        registry: PredicateRegistry | None = None,
        embedding_index: Any | None = None,
    ) -> None:
        if core is None and not base_dir:
            raise ValueError("MemoryKernel cần base_dir hoặc core hiện có")

        self.registry = registry or getattr(core, "registry", None) or get_default_registry()
        self.core = resolve_dependency(core, lambda: MemoryCognitionCore(
            str(base_dir),
            policy=commit_policy,
            registry=self.registry,
            embedding_index=embedding_index,
        ))
        self.config = resolve_dependency(config, lambda: MemoryKernelConfig())
        self.semantic_parser = semantic_parser

        # fast_policy cố tình KHÔNG nhận semantic_parser.
        # Đây là policy cho hot path, không được gọi model local/remote.
        self.fast_policy = MemoryCommitPolicy(semantic_parser=None, registry=self.registry)

        # digest_policy được quyền dùng parser đã nối, thường chạy ở worker nền.
        self.digest_policy = MemoryCommitPolicy(semantic_parser=semantic_parser, registry=self.registry)

        self.query_compiler = resolve_dependency(query_compiler, lambda: MemoryQueryCompiler(registry=self.registry))
        self.query_executor = resolve_dependency(query_executor, lambda: MemoryQueryExecutor(registry=self.registry))
        self.surface_policy = resolve_dependency(surface_policy, lambda: MemoryRecallSurfacePolicy(registry=self.registry))
        self.turn_annotator = resolve_dependency(turn_annotator, lambda: TurnAnnotator())
        # Projection hồ sơ người dùng sống cạnh core, dùng chung semantic/episodic truth.
        self.living_user = getattr(self.core, "living_user", None)

    @classmethod
    def from_existing_core(
        cls,
        core: MemoryCognitionCore,
        *,
        semantic_parser: Any | None = None,
        config: MemoryKernelConfig | None = None,
        registry: PredicateRegistry | None = None,
    ) -> "MemoryKernel":
        """Bọc MemoryCognitionCore hiện có mà không migrate store ngay.

        Dùng trong runtime chuyển tiếp: coordinator vẫn build memory_core cũ,
        nhưng các path mới gọi qua MemoryKernel.
        """

        return cls(core=core, semantic_parser=semantic_parser, config=config, registry=registry)

    # ------------------------------------------------------------------
    # Ingest path
    # ------------------------------------------------------------------

    def plan_ingest(
        self,
        text: str,
        *,
        evidence_ids: list[str] | None = None,
        turn_id: str | None = None,
        source: str = "chat_turn",
        mode: MemoryIngestMode = "fast",
        allow_candidate: bool | None = None,
        storage_permission: bool = True,
        permission_required: bool = False,
        context: Mapping[str, Any] | None = None,
    ) -> MemoryIngestPlan:
        """Build an ingest proposal without touching durable memory state.

        This is the only operation the realtime chat path should need. It may run
        deterministic policy code, but it never calls ``core.commit`` and never
        observes/synchronizes living-user state. The resulting requests are safe
        to hand to a background commit owner later.
        """

        raw = str(text or "").strip()
        if not raw:
            return MemoryIngestPlan(
                mode=mode,
                source=source,
                input_text="",
                warnings=("empty_ingest_text",),
                context=dict(context or {}),
            )

        allow_candidate = self.config.allow_candidate_by_default if allow_candidate is None else bool(allow_candidate)
        policy = self.fast_policy if mode == "fast" else self.digest_policy
        effective_evidence = list(evidence_ids or [])
        if not effective_evidence and turn_id:
            effective_evidence = [str(turn_id)]

        decisions = policy.decide_from_user_turn(
            raw,
            turn_id=turn_id or (effective_evidence[0] if effective_evidence else None),
            allow_candidate=allow_candidate,
        )

        requests: list[Any] = []
        warnings: list[str] = []
        for decision in decisions:
            confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
            if mode != "fast" and confidence < self.config.digest_commit_min_confidence:
                warnings.append(f"digest_decision_confidence_low:{confidence:.2f}")
                continue
            try:
                request = decision.to_state_commit_request(
                    storage_permission=storage_permission,
                    permission_required=permission_required,
                )
                requests.append(self._annotate_request(
                    request,
                    mode=mode,
                    source=source,
                    context=context,
                    evidence_ids=effective_evidence,
                ))
            except Exception as exc:
                warnings.append(f"plan_failed:{type(exc).__name__}:{exc}")

        return MemoryIngestPlan(
            mode=mode,
            source=source,
            input_text=raw,
            evidence_ids=tuple(effective_evidence),
            decisions=tuple(decisions),
            commit_requests=tuple(requests),
            warnings=tuple(warnings),
            context=dict(context or {}),
            debug={
                "decision_count": len(decisions),
                "planned_request_count": len(requests),
                "has_semantic_parser": self.semantic_parser is not None,
                "policy": "fast_policy" if policy is self.fast_policy else "digest_policy",
                "side_effect_free": True,
            },
        )

    def commit_plan(
        self,
        plan: MemoryIngestPlan,
        *,
        observe_living_user: bool = False,
        sync_living_user_semantic: bool = False,
    ) -> MemoryIngestResult:
        """Durably commit one previously planned ingest batch.

        Living-user work is explicit here. Background semantic digestion normally
        requests semantic projection sync only; the curator lane owns raw-turn
        profile observation. This prevents duplicate model work and makes ordering
        visible in code instead of hiding it inside ``mode`` folklore.
        """

        commit_results: list[Any] = []
        warnings = list(plan.warnings)
        for request in plan.commit_requests:
            try:
                commit_results.append(self.core.commit(request))
            except Exception as exc:  # pragma: no cover - background commit must degrade, not crash runtime
                warnings.append(f"commit_failed:{type(exc).__name__}:{exc}")

        user_profile_debug: dict[str, Any] = {}
        living_user = self.living_user
        # Observer/assistant có thể sở hữu evidence_id thật, nhưng không có
        # quyền đưa đoạn tóm tắt vào living-user extractor như một lời user.
        from .claim_provenance import classify_ingress
        plan_role = str(dict(plan.context or {}).get("background_role") or "")
        is_user_input = classify_ingress(
            plan.source, turn_id=plan.evidence_ids[0] if plan.evidence_ids else "",
            role=plan_role,
        )["origin_kind"] == "user_statement"
        if living_user is not None and plan.evidence_ids:
            if observe_living_user and is_user_input:
                try:
                    observation = living_user.observe_turn(
                        plan.input_text,
                        evidence_ids=list(plan.evidence_ids),
                        context={**dict(plan.context or {}), "source": plan.source, "mode": plan.mode},
                    )
                    user_profile_debug.update({
                        "applied": observation.applied,
                        "rejected": observation.rejected,
                        "operation_count": len(observation.operations),
                    })
                except Exception as exc:  # pragma: no cover
                    warnings.append(f"living_user_observe_failed:{type(exc).__name__}:{exc}")
            else:
                # Ownership phải hiện rõ trong debug contract. Fast path chỉ
                # ingest deterministic rồi giao profile observation cho curator
                # nền; semantic digest cũng có thể được caller đánh dấu là do
                # dedicated curator lane sở hữu để tránh gọi model hai lần.
                user_profile_debug["deferred"] = True
                if bool(dict(plan.context or {}).get("skip_living_user_observe", False)):
                    user_profile_debug["reason"] = "dedicated_curator_lane"
                elif not is_user_input:
                    user_profile_debug["reason"] = "non_user_provenance"
                elif str(plan.mode) == "fast":
                    user_profile_debug["reason"] = "fast_path_background_curator"
                else:
                    user_profile_debug["reason"] = "living_user_observe_disabled"
            if sync_living_user_semantic:
                try:
                    sync_result = living_user.sync_semantic_memory()
                    user_profile_debug["semantic_sync_changed"] = bool(sync_result.changed)
                except Exception as exc:  # pragma: no cover
                    warnings.append(f"living_user_sync_failed:{type(exc).__name__}:{exc}")

        debug = dict(plan.debug)
        debug["living_user"] = user_profile_debug
        debug["commit_owner"] = "memory_kernel.commit_plan"
        return MemoryIngestResult(
            accepted=bool(commit_results),
            mode=plan.mode,
            source=plan.source,
            input_text=plan.input_text,
            decisions=list(plan.decisions),
            commit_results=commit_results,
            warnings=warnings,
            debug=debug,
        )

    def ingest(
        self,
        text: str,
        *,
        evidence_ids: list[str] | None = None,
        turn_id: str | None = None,
        source: str = "chat_turn",
        mode: MemoryIngestMode = "fast",
        allow_candidate: bool | None = None,
        storage_permission: bool = True,
        permission_required: bool = False,
        context: Mapping[str, Any] | None = None,
    ) -> MemoryIngestResult:
        """Compatibility convenience: plan then commit in the current thread.

        ``fast`` is now strictly deterministic memory only: no profile model and
        no living-user JSON/trace work. ``semantic_digest`` retains the legacy
        full behavior for callers outside the new turn-memory pipeline.
        """

        plan = self.plan_ingest(
            text,
            evidence_ids=evidence_ids,
            turn_id=turn_id,
            source=source,
            mode=mode,
            allow_candidate=allow_candidate,
            storage_permission=storage_permission,
            permission_required=permission_required,
            context=context,
        )
        if not plan.input_text:
            return MemoryIngestResult(
                accepted=False,
                mode=mode,
                source=source,
                input_text="",
                warnings=list(plan.warnings),
                debug=dict(plan.debug),
            )
        context_map = dict(context or {})
        skip_living_user_observe = bool(context_map.get("skip_living_user_observe", False))
        return self.commit_plan(
            plan,
            observe_living_user=(mode != "fast" and not skip_living_user_observe),
            sync_living_user_semantic=(mode != "fast"),
        )

    # ------------------------------------------------------------------
    # Answer path
    # ------------------------------------------------------------------

    def answer(
        self,
        query: str,
        *,
        session_ledger: Any | None = None,
        context: Mapping[str, Any] | None = None,
        min_confidence: float | None = None,
        max_ambiguity: float | None = None,
    ) -> MemoryAnswerResult:
        """Trả lời deterministic cho câu hỏi memory nếu đủ chắc.

        Hàm này không gọi LLM để trả lời. Nếu compiler cần LLM structured parser thì
        nó chỉ được trả IR, không được trả text public bịa từ trí nhớ.
        """

        raw = str(query or "").strip()
        if not raw:
            return MemoryAnswerResult(intercepted=False, reason="empty_query")

        min_confidence = self.config.answer_min_confidence if min_confidence is None else float(min_confidence)
        max_ambiguity = self.config.answer_max_ambiguity if max_ambiguity is None else float(max_ambiguity)

        compile_result = self.query_compiler.compile(raw)
        if not compile_result.has_query or compile_result.best is None:
            return MemoryAnswerResult(
                intercepted=False,
                compile_result=compile_result,
                reason="not_memory_query",
                debug=self._compile_debug(compile_result),
            )

        best = compile_result.best
        # P0: Compiler có quyền đánh dấu một IR là "chỉ dùng làm hint".
        # Ví dụ semantic_hint có thể đoán gần đúng vùng nhớ, nhưng không được
        # chặn hot path chat để trả public answer. Trước đây kernel bỏ qua
        # intercept_permission nên câu hỏi mơ hồ như "sở thích của tôi là gì"
        # có thể bị surface nhầm thành user.name. Memory chỉ được cầm mic khi
        # compiler cấp quyền intercept rõ ràng.
        if not bool(getattr(best, "intercept_permission", False)):
            return MemoryAnswerResult(
                intercepted=False,
                compile_result=compile_result,
                reason="query_no_intercept_permission",
                debug=self._compile_debug(compile_result),
                warnings=["memory_query_hint_not_allowed_to_surface"],
            )

        confidence = float(getattr(best, "confidence", 0.0) or 0.0)
        ambiguity = float(getattr(best, "ambiguity", 1.0) or 1.0)
        if confidence < min_confidence:
            return MemoryAnswerResult(
                intercepted=False,
                compile_result=compile_result,
                reason=f"query_confidence_low:{confidence:.2f}",
                debug=self._compile_debug(compile_result),
            )
        if ambiguity > max_ambiguity:
            return MemoryAnswerResult(
                intercepted=False,
                compile_result=compile_result,
                reason=f"query_ambiguity_high:{ambiguity:.2f}",
                debug=self._compile_debug(compile_result),
            )

        turn_graph = None
        if session_ledger is not None:
            turn_graph = TurnGraph.from_session_ledger(session_ledger, annotator=self.turn_annotator)

        raw_result = self.query_executor.execute(
            best,
            turn_graph=turn_graph,
            memory_core=self.core,
        )
        answer = self.surface_policy.surface(raw_result)
        answer_text = str(getattr(answer, "text", "") or "").strip()
        intercepted = bool(answer_text) and float(getattr(answer, "confidence", 0.0) or 0.0) >= min_confidence

        return MemoryAnswerResult(
            intercepted=intercepted,
            answer=answer,
            compile_result=compile_result,
            raw_result=raw_result,
            reason="answered" if intercepted else "answer_not_confident_enough",
            debug={
                **self._compile_debug(compile_result),
                "raw_found": bool(getattr(raw_result, "found", False)),
                "answer_confidence": float(getattr(answer, "confidence", 0.0) or 0.0),
                "context_keys": sorted((context or {}).keys()),
            },
        )

    # ------------------------------------------------------------------
    # Packet/resonance path
    # ------------------------------------------------------------------

    def build_packet(
        self,
        query: str = "",
        *,
        budget_mode: str = "normal",
        max_items: int = 18,
        token_budget: int = 900,
        frame: Any | None = None,
    ) -> MemoryPacket:
        """Dựng MemoryPacket/resonance context cho cognition.

        Packet không phải exact answer. Packet là áp lực nội bộ được quota theo layer.
        Runtime/prompt builder dùng nó như context có kiểm soát.
        """

        q = MemoryQuery(
            query=str(query or ""),
            budget_mode=budget_mode if budget_mode in {"fast", "normal", "deep", "reflection"} else "normal",
            max_items=max_items,
            token_budget=token_budget,
        )
        return self.core.recall(q, frame=frame)

    # Alias cho code đọc thuận miệng hơn.
    recall_packet = build_packet

    # ------------------------------------------------------------------
    # Maintain path
    # ------------------------------------------------------------------

    def maintain(
        self,
        *,
        budget: MaintenanceBudget = "small",
        reason: str = "idle",
        destructive: bool | None = None,
    ) -> MemoryMaintenanceReport:
        """Chạy maintenance nền.

        Không gọi trong hot path chat. Gọi từ idle/reflection/session end.
        """

        runner = MemoryMaintenanceRunner(
            self.core,
            ingest_policy=self.digest_policy,
            destructive=self.config.destructive_maintenance,
            llm_service=getattr(self, "_llm_service", None),
        )
        report = runner.run(budget=budget, reason=reason, destructive=destructive)
        living_user = self.living_user
        rollup = getattr(living_user, "rollup_episodes", None)
        if callable(rollup):
            try:
                result = rollup()
                report.notes.append(f"episode_rollup:{result.get('status')} calls={result.get('calls', 0)}")
            except Exception as exc:
                report.warnings.append(f"episode_rollup_failed:{type(exc).__name__}")
        if living_user is not None and getattr(living_user.config, "auto_dream_on_maintenance", False):
            try:
                self.last_user_memory_dream = living_user.dream(reason=reason)
            except Exception:
                self.last_user_memory_dream = None
        return report

    # ------------------------------------------------------------------
    # Compatibility helpers: để migration không phải sửa cả repo trong một nhát.
    # ------------------------------------------------------------------

    def commit(self, request: Any, *, frame: Any = None) -> Any:
        """Compat: route commit cũ xuống core.

        Runtime mới nên gọi ingest(), nhưng StateCommitter cũ vẫn có thể gọi commit().
        """

        return self.core.commit(request, frame=frame)

    def commit_many(self, requests: list[Any], *, frame: Any = None) -> list[Any]:
        return self.core.commit_many(requests, frame=frame)

    def recall(self, query: Any = "", *, frame: Any = None) -> MemoryPacket:
        """Compat với MemoryCognitionCore.recall()."""

        return self.core.recall(query, frame=frame)

    def retrieve(self, query: Any = "", *, frame: Any = None) -> MemoryPacket:
        return self.core.retrieve(query, frame=frame)

    def load(self) -> dict[str, Any]:
        return self.core.load()

    def save(self, snapshot: Mapping[str, Any] | None = None) -> None:
        self.core.save(snapshot)

    def build_public_context(self, query: str, *, session_id: str,
                             user_context_token_budget: int = 6000,
                             max_lines: int = 64,
                             session_context: Mapping[str, Any] | None = None) -> PublicMemoryContext:
        """CÙNG một selection tạo text + records. Tuyệt đối không recall lần hai.

        ProfileContextPacket có items/episodes nhưng không phải toàn bộ đều có
        canonical MemoryRecord. Phần chưa resolve phải đánh dấu degraded thay vì
        dựng giả record hoặc suy provenance từ câu prose đã render.
        """
        packet = self.core.recall(query)
        core_text = self.core.render_context_for_packet(packet, max_lines=max_lines)
        records = tuple(getattr(packet, "source_records", ()) or ())
        degraded = False
        profile_text = ""
        living_user = self.living_user
        if living_user is not None:
            try:
                profile = living_user.compile_context(
                    str(query or ""), session_id=session_id,
                    token_budget=int(user_context_token_budget),
                    session_context=session_context,
                )
                profile_text = profile.render_prompt_context()
                # Profile projection không đồng nghĩa canonical proof. Chỉ giữ
                # typed records đã thực sự được packet core chọn; còn lại unknown.
                if profile_text and (profile.items or profile.episodes or profile.action_context or getattr(profile, "proactive_cue", "")):
                    degraded = True
            except Exception:
                degraded = True
        combined = "\n\n".join(part for part in (core_text, profile_text) if part).strip()
        return PublicMemoryContext(text=combined, records=records, degraded=degraded)

    def render_context(self, *args: Any, **kwargs: Any) -> str:
        """Render memory context và ghép profile đúng ngữ cảnh.

        Profile packet chỉ được thêm khi có record liên quan; vì vậy runtime cũ
        không bị đổi prompt nếu user model còn rỗng.
        """
        core_kwargs = dict(kwargs)
        for private_key in ("project_id", "session_id", "user_context_token_budget", "session_context"):
            core_kwargs.pop(private_key, None)
        core_context = self.core.render_context(*args, **core_kwargs)
        living_user = self.living_user
        if living_user is None:
            return core_context
        query = ""
        if args:
            first = args[0]
            query = str(getattr(first, "query", first) or "")
        elif "query" in kwargs:
            query = str(getattr(kwargs.get("query"), "query", kwargs.get("query")) or "")
        try:
            packet = living_user.compile_context(
                query,
                project_id=str(kwargs.get("project_id") or ""),
                session_id=str(kwargs.get("session_id") or ""),
                token_budget=int(kwargs.get("user_context_token_budget") or 900),
                session_context=kwargs.get("session_context"),
            )
            profile_context = packet.render_prompt_context()
            if _is_user_memory_summary_query(query):
                # Khi user hỏi trực tiếp "bạn nhớ gì về tôi", đưa bản summary
                # có kiểm soát vào prompt. Không inject inferred/sensitive mặc định.
                # Model chỉ việc diễn đạt tự nhiên, không phải tự đoán profile.
                readable = living_user.render_summary(
                    include_inferred=False,
                    include_sensitive=False,
                    limit=36,
                )
                profile_context = (
                    f"{profile_context}\n\nMEMORY SUMMARY REQUEST:\n{readable}".strip()
                    if profile_context
                    else f"MEMORY SUMMARY REQUEST:\n{readable}"
                )
        except Exception:
            profile_context = ""
        if not profile_context:
            return core_context
        return f"{core_context}\n\n{profile_context}".strip() if core_context else profile_context

    def compile_user_context(
        self,
        query: str,
        *,
        project_id: str = "",
        session_id: str = "",
        domain: str | None = None,
        token_budget: int = 900,
    ) -> Any:
        """API tường minh cho caller cần structured profile packet."""
        if self.living_user is None:
            return None
        return self.living_user.compile_context(
            query,
            project_id=project_id,
            session_id=session_id,
            domain=domain,
            token_budget=token_budget,
        )

    def consolidate_user_session(
        self,
        turns: Any,
        *,
        session_id: str,
        project_id: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> Any:
        if self.living_user is None:
            return None
        from .living_user import UserMemoryScope
        scope = UserMemoryScope("project", project_id) if project_id else UserMemoryScope()
        return self.living_user.consolidate_session(
            turns,
            session_id=session_id,
            scope=scope,
            metadata=metadata,
        )

    def user_memory_summary(self, *, include_sensitive: bool = False) -> dict[str, Any]:
        if self.living_user is None:
            return {"profile": {"records": []}, "recent_episodes": []}
        return self.living_user.summary(include_sensitive=include_sensitive)

    def render_user_memory_summary(
        self,
        *,
        include_inferred: bool = False,
        include_sensitive: bool = False,
        limit: int = 36,
    ) -> str:
        if self.living_user is None:
            return "Tôi chưa có đủ thông tin bền vững để tổng hợp về bạn."
        return self.living_user.render_summary(
            include_inferred=include_inferred,
            include_sensitive=include_sensitive,
            limit=limit,
        )

    def explain_user_memory(self, path: str, *, project_id: str = "") -> Any:
        return () if self.living_user is None else self.living_user.explain(path, project_id=project_id)

    def forget_user_memory(self, path_or_prefix: str, *, project_id: str = "", hard: bool = False) -> int:
        return 0 if self.living_user is None else self.living_user.forget(path_or_prefix, project_id=project_id, hard=hard)

    def correct_user_memory(
        self,
        path: str,
        value: Any,
        *,
        evidence_ids: list[str],
        project_id: str = "",
    ) -> Any:
        if self.living_user is None:
            return None
        return self.living_user.correct(path, value, evidence_ids=evidence_ids, project_id=project_id)

    def lookup_fact(self, fact_key: str) -> Any:
        return self.core.lookup_fact(fact_key)

    def debug_snapshot(self) -> MemoryKernelDebugSnapshot:
        return MemoryKernelDebugSnapshot(
            base_dir=str(getattr(self.core, "base_dir", "")),
            has_semantic_parser=self.semantic_parser is not None,
            fast_policy_has_parser=getattr(self.fast_policy, "semantic_parser", None) is not None,
            digest_policy_has_parser=getattr(self.digest_policy, "semantic_parser", None) is not None,
            timestamp=time.time(),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _annotate_request(
        self,
        request: Any,
        *,
        mode: MemoryIngestMode,
        source: str,
        context: Mapping[str, Any] | None,
        evidence_ids: list[str],
    ) -> Any:
        """Gắn metadata kernel vào StateCommitRequest/dict.

        request hiện có thể là dict từ memory_models.to_state_commit_request().
        Dùng dict để tránh phụ thuộc StateCommitRequest cụ thể.
        """

        from .claim_provenance import classify_ingress
        # Provenance từ context có thể bắt nguồn từ worker. Channel `source`
        # và event_id do ingress cấp mới là authority. Không cho metadata
        # tự xưng user_statement để vượt qua observer/assistant boundary.
        runtime_role = str(dict(context or {}).get("background_role") or "")
        provenance = classify_ingress(
            source, turn_id=evidence_ids[0] if evidence_ids else "", role=runtime_role,
        )
        if isinstance(request, dict):
            metadata = dict(request.get("metadata") or {})
            # Override policy default bằng nguồn runtime: observer/assistant
            # không bao giờ được đi qua dưới tên explicit_user_statement.
            metadata["claim_provenance"] = provenance
            if provenance["origin_kind"] != "user_statement":
                metadata["semantic_source"] = provenance["origin_kind"]
                metadata["source_grounded"] = False
                if request.get("target") == "semantic_memory":
                    content = request.get("content")
                    if isinstance(content, dict):
                        request["content"] = {**content, "source": provenance["origin_kind"]}
            metadata.setdefault("memory_kernel_mode", mode)
            metadata.setdefault("memory_kernel_source", source)
            metadata.setdefault("memory_kernel_context", dict(context or {}))
            metadata.setdefault("source", metadata.get("source") or "MemoryKernel")
            request["metadata"] = metadata
            if evidence_ids and not request.get("evidence_ids"):
                request["evidence_ids"] = list(evidence_ids)
            return request

        metadata = dict(getattr(request, "metadata", {}) or {})
        metadata["claim_provenance"] = provenance
        if provenance["origin_kind"] != "user_statement":
            metadata["semantic_source"] = provenance["origin_kind"]
            metadata["source_grounded"] = False
        metadata.setdefault("memory_kernel_mode", mode)
        metadata.setdefault("memory_kernel_source", source)
        metadata.setdefault("memory_kernel_context", dict(context or {}))
        try:
            request.metadata = metadata
        except Exception:
            pass
        try:
            current_evidence = list(getattr(request, "evidence_ids", []) or [])
            if evidence_ids and not current_evidence:
                request.evidence_ids = list(evidence_ids)
        except Exception:
            pass
        return request

    def _compile_debug(self, compile_result: Any) -> dict[str, Any]:
        debug = getattr(compile_result, "debug", None)
        best = getattr(compile_result, "best", None)
        return {
            "has_query": bool(getattr(compile_result, "has_query", False)),
            "best_intent": str(getattr(best, "intent", "") or ""),
            "best_confidence": float(getattr(best, "confidence", 0.0) or 0.0) if best is not None else 0.0,
            "best_ambiguity": float(getattr(best, "ambiguity", 1.0) or 1.0) if best is not None else 1.0,
            "slot_hypotheses": int(getattr(debug, "slot_hypotheses", 0) or 0) if debug is not None else 0,
            "semantic_confidence": float(getattr(debug, "semantic_confidence", 0.0) or 0.0) if debug is not None else 0.0,
            "semantic_ambiguity": float(getattr(debug, "semantic_ambiguity", 1.0) or 1.0) if debug is not None else 1.0,
            "llm_called": bool(getattr(debug, "llm_called", False)) if debug is not None else False,
            "notes": list(getattr(debug, "notes", ()) or ()) if debug is not None else [],
        }


def _fold_memory_query(text: str) -> str:
    """Chuẩn hóa nhẹ để nhận câu hỏi summary có/không dấu."""
    raw = unicodedata.normalize("NFKD", str(text or "").lower())
    return " ".join("".join(ch for ch in raw if not unicodedata.combining(ch)).split())


def _is_user_memory_summary_query(query: str) -> bool:
    """Nhận lệnh hỏi tổng quan memory mà không phụ thuộc một câu cố định."""
    text = _fold_memory_query(query)
    phrases = (
        "ban nho gi ve toi",
        "ban biet gi ve toi",
        "nho gi ve tui",
        "biet gi ve tui",
        "tong hop ve toi",
        "tong hop ve tui",
        "memory cua toi",
        "what do you remember about me",
        "what do you know about me",
    )
    return any(phrase in text for phrase in phrases)


# Tên alias để migrate từ MemoryCognitionCore không bị gãy import ngay.
MemoryCoreKernel = MemoryKernel


__all__ = ["MemoryKernel", "MemoryCoreKernel", "MemoryKernelDebugSnapshot"]
