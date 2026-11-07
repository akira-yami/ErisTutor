"""Memory Cognition Core cho ERIS.

Bản v2:
- Commit protocol vẫn giữ nguyên: module khác chỉ tạo StateCommitRequest.
- Recall không còn tự đo resonance bằng token overlap trong MemoryCognitionCore.
- MemoryCognitionCore giờ là facade: route commit + gọi MemoryRetrievalEngine.
- Retrieval mới dùng MemoryCue -> Candidate -> Score -> Activation -> Packet.

Tức là ký ức không còn ra lệnh bằng vài token trùng nhau.
Nó chỉ tạo áp lực có lý do, có score breakdown, có surface policy.
Một sự tiến hóa nhỏ khỏi cái hố "token overlap là trí nhớ".
"""

from __future__ import annotations
from core.di import resolve_dependency

import os
import time
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal, Mapping

try:
    from core.evidence import StateCommitRequest  # type: ignore
except Exception:  # pragma: no cover
    @dataclass(slots=True)
    class StateCommitRequest:  # type: ignore[no-redef]
        target: str
        content: str
        confidence: float
        evidence_ids: list[str]
        permission_required: bool = True
        storage_permission: bool = False
        decay_policy: str = "normal"
        reason: str = ""
        metadata: dict[str, Any] = field(default_factory=dict)

from .active_state_snapshot import ActiveStateSnapshot, ActiveStateStore
from .affective_residue_memory import AffectiveResidueItem, AffectiveResidueMemoryStore
from .episodic_memory import EpisodicMemoryItem, EpisodicMemoryStore
from .event_ledger import EventLedger, EventLedgerRecord
from .procedural_memory import ProceduralMemoryItem, ProceduralMemoryStore
from .relational_memory import RelationalMemoryItem, RelationalMemoryStore
from .repair_memory import RepairMemoryItem, RepairMemoryStore
from .semantic_memory import SemanticMemoryItem, SemanticMemoryStore
from .predicate_registry import PredicateRegistry, get_default_registry
from .unresolved_memory import UnresolvedMemoryCandidate, UnresolvedMemoryStore
from .canonical_long_term import (
    BeliefStore,
    WorldviewMemoryStore,
    SocioculturalMemoryStore,
    SpatialMemoryStore,
    TemporalRhythmMemoryStore,
    BodyRhythmMemoryStore,
    ToMHistoryStore,
    SelfContinuityMemoryStore,
    HomeostaticBaselineStore,
)

from .memory_record import compact_text, clamp01
from .memory_cue import MemoryCue, MemoryCueBuilder
from .memory_retrieval_engine import MemoryStores, MemoryRetrievalEngine
from .memory_packet_builder import MemoryPacket, MemoryPacketBuilder
from .memory_feed_trace import MemoryFeedTraceJournal
from .memory_activation import MemoryActivation
from .living_user import LivingUserMemoryService
from cortex.agent_mind_persistence import PersistentAgentMindStore, agent_mind_path_for_memory_core

BudgetMode = Literal["fast", "normal", "deep", "reflection"]
CommitOutcome = Literal["accepted", "rejected", "tentative", "ignored"]


def _request_get(request: Any, name: str, default: Any = None) -> Any:
    if isinstance(request, Mapping):
        return request.get(name, default)
    return getattr(request, name, default)


def _request_metadata(request: Any) -> dict[str, Any]:
    metadata = _request_get(request, "metadata", {}) or {}
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _as_text_list(values: Any, *, limit: int = 260) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        text = compact_text(values, limit=limit)
        return [text] if text else []
    if isinstance(values, Mapping):
        return [compact_text(f"{key}={value}", limit=limit) for key, value in values.items() if compact_text(value, limit=limit)]
    if isinstance(values, (list, tuple, set, frozenset)):
        return [compact_text(item, limit=limit) for item in values if compact_text(item, limit=limit)]
    text = compact_text(values, limit=limit)
    return [text] if text else []


def _content_for_target(target: str, content: Any) -> str:
    """Normalize structured commit payloads before sending them to layer stores."""
    if not isinstance(content, Mapping):
        return compact_text(content, limit=900)

    data = dict(content)
    if target == "semantic_memory":
        return compact_text(data.get("fact") or data.get("claim") or data.get("text") or data, limit=420)
    if target == "relational_memory":
        pattern = compact_text(data.get("pattern") or "relationship_reflection", limit=120)
        text = compact_text(data.get("text") or data.get("claim") or data, limit=420)
        return compact_text(f"{pattern}: {text}", limit=520)
    if target == "procedural_memory":
        trigger = compact_text(data.get("trigger") or data.get("pattern") or "", limit=120)
        rule = compact_text(data.get("handling_rule") or data.get("rule") or data.get("text") or data, limit=420)
        return compact_text(f"{trigger}: {rule}" if trigger else rule, limit=520)
    if target == "repair_memory":
        fields = ("failure_type", "trigger_context", "wrong_assumption", "user_correction", "repair_strategy", "future_suppression_rule")
        lines = [f"{field_name}: {compact_text(data.get(field_name), limit=360)}" for field_name in fields if compact_text(data.get(field_name), limit=360)]
        return "\n".join(lines) or compact_text(data, limit=640)
    if target == "affective_residue_memory":
        stance = compact_text(data.get("stance") or data.get("residue_type") or "affective_residue", limit=120)
        details = []
        for key in ("residue_strength", "threat", "intimacy", "tenderness", "fatigue", "outcome"):
            if key in data:
                details.append(f"{key}={data[key]}")
        return compact_text(f"{stance}: {'; '.join(details)}", limit=520)
    if target == "episodic_memory":
        scene = " | ".join(_as_text_list(data.get("scene"), limit=180))
        action = compact_text(data.get("eris_action") or data.get("said") or "", limit=220)
        outcome = compact_text(data.get("outcome") or "cycle", limit=120)
        return compact_text(" -> ".join(part for part in (scene, action, outcome) if part), limit=620)
    if target in {"memory_candidate", "unresolved_memory"}:
        return compact_text(data.get("raw_text") or data.get("reason") or data, limit=1200)
    return compact_text(data, limit=900)


@dataclass(slots=True)
class MemoryQuery:
    query: str = ""
    budget_mode: BudgetMode = "normal"
    max_items: int = 18
    include_repair: bool = True
    include_affective_residue: bool = True
    include_procedural: bool = True
    token_budget: int = 900


@dataclass(slots=True)
class MemoryCommitDecision:
    outcome: CommitOutcome
    target: str
    reason: str
    confidence: float
    evidence_ids: list[str] = field(default_factory=list)
    stored_id: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "target": self.target,
            "reason": self.reason,
            "confidence": round(float(self.confidence), 4),
            "evidence_ids": list(self.evidence_ids),
            "stored_id": self.stored_id,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class CommitPolicy:
    """Rule chặn ghi nhớ bừa.

    Đây là commit permission, không phải retrieval permission.
    Ghi nhớ vẫn cần giấy tờ. Truy hồi thì dùng consequence scoring.
    """

    min_confidence: float = 0.45
    semantic_min_confidence: float = 0.62
    relational_min_confidence: float = 0.52
    repair_min_confidence: float = 0.45
    require_evidence_ids: bool = True
    sensitive_requires_explicit_permission: bool = True
    allow_session_only_without_evidence: bool = True


class MemoryCognitionCore:
    """Facade chính của memory.

    Runtime chỉ nên giữ một instance class này.
    - commit(): ghi nhớ qua StateCommitRequest.
    - recall()/retrieve(): dựng MemoryCue rồi gọi MemoryRetrievalEngine.
    """

    def __init__(
        self,
        base_dir: str,
        *,
        policy: CommitPolicy | None = None,
        registry: PredicateRegistry | None = None,
        embedding_index: Any | None = None,
    ):
        self.base_dir = base_dir
        self.registry = registry or get_default_registry()
        os.makedirs(self.base_dir, exist_ok=True)
        self.policy = resolve_dependency(policy, lambda: CommitPolicy())
        # Tất cả commit qua core được serialize để chuỗi load -> mutate -> save
        # không mất update khi các pipeline chạy song song.
        self._commit_lock = threading.RLock()
        self._save_lock = threading.RLock()

        self.event_ledger = EventLedger(os.path.join(base_dir, "event_ledger.json"))
        self.active_state = ActiveStateStore(os.path.join(base_dir, "active_state_snapshot.json"))
        self.episodic = EpisodicMemoryStore(os.path.join(base_dir, "episodic_memory.json"))
        # Dùng EmbeddingIndex singleton dùng chung (memory + event_semantics +
        # situation_model chia sẻ đúng một model, nạp lazy một lần).
        # Nếu torch/transformers chưa install hoặc ERIS_EMBEDDING_ENABLED=0 thì trả None,
        # semantic store gracefully fallback về lexical scoring.
        _embedding_index = embedding_index
        if _embedding_index is None:
            try:
                from .embedding_index import get_shared_embedding_index
                _embedding_index = get_shared_embedding_index()
            except Exception:
                _embedding_index = None
        self.semantic = SemanticMemoryStore(
            os.path.join(base_dir, "semantic_memory.json"),
            embedding_index=_embedding_index,
            registry=self.registry,
        )
        self.relational = RelationalMemoryStore(os.path.join(base_dir, "relational_memory.json"))
        self.affective_residue = AffectiveResidueMemoryStore(os.path.join(base_dir, "affective_residue_memory.json"))
        self.procedural = ProceduralMemoryStore(os.path.join(base_dir, "procedural_memory.json"))
        self.repair = RepairMemoryStore(os.path.join(base_dir, "repair_memory.json"))
        self.memory_candidate = UnresolvedMemoryStore(os.path.join(base_dir, "memory_candidate.json"))
        self.belief_store = BeliefStore(os.path.join(base_dir, "belief_store.json"))
        self.worldview = WorldviewMemoryStore(os.path.join(base_dir, "worldview_memory.json"))
        self.sociocultural = SocioculturalMemoryStore(os.path.join(base_dir, "sociocultural_memory.json"))
        self.spatial = SpatialMemoryStore(os.path.join(base_dir, "spatial_memory.json"))
        self.temporal_rhythm = TemporalRhythmMemoryStore(os.path.join(base_dir, "temporal_rhythm_memory.json"))
        self.body_rhythm = BodyRhythmMemoryStore(os.path.join(base_dir, "body_rhythm_memory.json"))
        self.tom_history = ToMHistoryStore(os.path.join(base_dir, "tom_history.json"))
        self.self_continuity = SelfContinuityMemoryStore(os.path.join(base_dir, "self_continuity_memory.json"))
        self.homeostatic_baseline = HomeostaticBaselineStore(os.path.join(base_dir, "homeostatic_baseline.json"))
        self.agent_mind = PersistentAgentMindStore(agent_mind_path_for_memory_core(base_dir))

        self.cue_builder = MemoryCueBuilder()
        self.packet_builder = MemoryPacketBuilder()
        # V41: một trace chung cho candidate -> admission -> activation. Chỉ lưu ID/lý do,
        # không nhân bản nguyên văn memory sang một file debug khác.
        self.memory_feed_trace = MemoryFeedTraceJournal(os.path.join(base_dir, "memory_feed_trace.jsonl"))
        self.retrieval_engine = MemoryRetrievalEngine(
            MemoryStores(
                semantic=self.semantic,
                relational=self.relational,
                episodic=self.episodic,
                procedural=self.procedural,
                affective_residue=self.affective_residue,
                repair=self.repair,
            ),
            feed_trace=self.memory_feed_trace,
        )
        # Living user memory dùng lại đúng các store canonical ở trên. Nó không
        # sao chép semantic/episodic truth, chỉ giữ profile projection và episode digest.
        self.living_user = LivingUserMemoryService(
            base_dir,
            semantic_store=self.semantic,
            episodic_store=self.episodic,
            event_ledger=self.event_ledger,
            embedding_index=_embedding_index,
            memory_feed_trace=self.memory_feed_trace,
        )

    # ------------------------------------------------------------------
    # Commit path: giữ logic cũ, vì ghi nhớ cần ổn định hơn là hào nhoáng.
    # ------------------------------------------------------------------

    def commit(self, request: StateCommitRequest, *, frame: Any = None) -> MemoryCommitDecision:
        """Commit tuần tự qua một writer logic duy nhất.

        Atomic replace chỉ bảo vệ file khỏi rách; lock này mới bảo vệ chuỗi
        load -> mutate -> save khỏi lost update giữa các thread.
        """
        with self._commit_lock:
            return self._commit_unlocked(request, frame=frame)

    def _commit_unlocked(self, request: StateCommitRequest, *, frame: Any = None) -> MemoryCommitDecision:
        request = self._normalize_request(request)
        target = str(_request_get(request, "target", "") or "")
        confidence = clamp01(float(_request_get(request, "confidence", 0.0) or 0.0))
        evidence_ids = [str(item) for item in _request_get(request, "evidence_ids", []) or [] if str(item).strip()]
        content = compact_text(_request_get(request, "content", "") or "", limit=900)
        warnings: list[str] = []

        # V0.4: quyết định cuối tại canonical owner, không tin prompt/observer.
        from .claim_provenance import commit_provenance_check
        provenance_ok, provenance_reason = commit_provenance_check(target, getattr(request, "metadata", {}))
        if not provenance_ok:
            return MemoryCommitDecision(
                outcome="rejected", target=target, reason=provenance_reason,
                confidence=confidence, evidence_ids=evidence_ids, warnings=warnings,
            )

        allowed, reason = self._check_commit_permission(request, target=target, confidence=confidence, evidence_ids=evidence_ids)
        if not allowed:
            return MemoryCommitDecision(
                outcome="rejected",
                target=target,
                reason=reason,
                confidence=confidence,
                evidence_ids=evidence_ids,
                warnings=warnings,
            )
        if not content and target != "event_ledger":
            return MemoryCommitDecision(
                outcome="ignored",
                target=target,
                reason="empty_content",
                confidence=confidence,
                evidence_ids=evidence_ids,
            )

        stored_id = ""
        outcome: CommitOutcome = "accepted"
        try:
            if target == "event_ledger":
                record = self._commit_event_ledger(request, frame=frame)
                stored_id = record.id
            elif target == "active_state":
                snapshot = self.active_state.apply_commit(request)
                stored_id = snapshot.updated_at
            elif target == "homeostatic_baseline":
                item = self.homeostatic_baseline.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "self_continuity":
                item = self.self_continuity.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "episodic_memory":
                item = self.episodic.apply_commit(request, frame=frame)
                stored_id = item.id if item else ""
            elif target == "semantic_memory":
                item = self.semantic.apply_commit(request)
                stored_id = item.id if item else ""
                if confidence < self.policy.semantic_min_confidence:
                    outcome = "tentative"
            elif target == "relational_memory":
                item = self.relational.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "affective_residue_memory":
                item = self.affective_residue.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "procedural_memory":
                item = self.procedural.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "repair_memory":
                item = self.repair.apply_commit(request)
                stored_id = item.id if item else ""
            elif target in {"memory_candidate", "unresolved_memory"}:
                item = self.memory_candidate.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "belief_core":
                item = self.belief_store.apply_commit(request)
                stored_id = item.id if item else ""
                if confidence < 0.72:
                    outcome = "tentative"
            elif target == "worldview_memory":
                item = self.worldview.apply_commit(request)
                stored_id = item.id if item else ""
                if confidence < 0.74:
                    outcome = "tentative"
            elif target == "sociocultural_memory":
                item = self.sociocultural.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "spatial_memory":
                item = self.spatial.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "temporal_rhythm_memory":
                item = self.temporal_rhythm.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "body_rhythm_memory":
                item = self.body_rhythm.apply_commit(request)
                stored_id = item.id if item else ""
            elif target == "tom_history":
                item = self.tom_history.apply_commit(request)
                stored_id = item.id if item else ""
            else:
                return MemoryCommitDecision(
                    outcome="rejected",
                    target=target,
                    reason=f"unsupported_target:{target}",
                    confidence=confidence,
                    evidence_ids=evidence_ids,
                    warnings=["Target chưa được route trong MemoryCognitionCore."],
                )
        except Exception as exc:
            return MemoryCommitDecision(
                outcome="rejected",
                target=target,
                reason=f"commit_exception:{type(exc).__name__}: {exc}",
                confidence=confidence,
                evidence_ids=evidence_ids,
            )

        return MemoryCommitDecision(
            outcome=outcome,
            target=target,
            reason="stored_via_state_commit_request",
            confidence=confidence,
            evidence_ids=evidence_ids,
            stored_id=stored_id,
            warnings=warnings,
        )

    def commit_many(self, requests: list[StateCommitRequest], *, frame: Any = None) -> list[MemoryCommitDecision]:
        return [self.commit(request, frame=frame) for request in requests or []]

    # ------------------------------------------------------------------
    # Recall path v2
    # ------------------------------------------------------------------

    def lookup_fact(self, fact_key: str) -> Any:
        return self.semantic.lookup_fact(fact_key)

    def get_fact(self, fact_key: str) -> Any:
        return self.lookup_fact(fact_key)

    def semantic_lookup(self, fact_key: str) -> Any:
        return self.lookup_fact(fact_key)

    def recall(
        self,
        query: MemoryQuery | str = "",
        *,
        frame: Any = None,
        max_items: int | None = None,
        budget_mode: BudgetMode | None = None,
    ) -> MemoryPacket:
        if isinstance(query, MemoryQuery):
            q = query
            if max_items is not None or budget_mode is not None:
                q = MemoryQuery(
                    query=q.query,
                    budget_mode=budget_mode or q.budget_mode,
                    max_items=max_items if max_items is not None else q.max_items,
                    include_repair=q.include_repair,
                    include_affective_residue=q.include_affective_residue,
                    include_procedural=q.include_procedural,
                    token_budget=q.token_budget,
                )
        else:
            q = MemoryQuery(query=str(query or ""), max_items=max_items or 18, budget_mode=budget_mode or "normal")
        return self.retrieve(q, frame=frame)

    def retrieve(self, query: MemoryQuery | str = "", *, frame: Any = None) -> MemoryPacket:
        """Truy hồi memory bằng retrieval engine mới.

        Không gọi packet resonance cũ.
        Không đo lại overlap trên recall_line string.
        """
        if isinstance(query, str):
            q = MemoryQuery(query=query)
        else:
            q = query

        query_text = compact_text(q.query or self._query_from_frame(frame), limit=700)
        mode = q.budget_mode or self._budget_from_frame(frame)
        quotas = self._quotas(mode=mode, max_items=q.max_items)

        cue = self.cue_builder.from_query_or_frame(q, frame=frame, budget_mode=mode)

        if not q.include_repair:
            cue.forbidden_layers = tuple(dict.fromkeys((*cue.forbidden_layers, "repair")))
        if not q.include_affective_residue:
            cue.forbidden_layers = tuple(dict.fromkeys((*cue.forbidden_layers, "affective_residue")))
        if not q.include_procedural:
            cue.forbidden_layers = tuple(dict.fromkeys((*cue.forbidden_layers, "procedural")))

        result = self.retrieval_engine.retrieve(cue, quotas=quotas, max_activations=max(3, min(5, q.max_items)))
        packet = self.packet_builder.from_retrieval_result(result, token_budget=q.token_budget)
        return packet

    def load(self) -> dict[str, Any]:
        return {
            "event_ledger": [item.to_dict() for item in self.event_ledger.recent(limit=200)],
            "active_state": self.active_state.load().to_dict(),
            "episodic_memory": [item.to_dict() for item in self.episodic.load()],
            "semantic_memory": [item.to_dict() for item in self.semantic.load()],
            "relational_memory": [item.to_dict() for item in self.relational.load()],
            "affective_residue_memory": [item.to_dict() for item in self.affective_residue.load()],
            "procedural_memory": [item.to_dict() for item in self.procedural.load()],
            "repair_memory": [item.to_dict() for item in self.repair.load()],
            "memory_candidate": [item.to_dict() for item in self.memory_candidate.load()],
            "belief_core": [item.to_dict() for item in self.belief_store.load()],
            "worldview_memory": [item.to_dict() for item in self.worldview.load()],
            "sociocultural_memory": [item.to_dict() for item in self.sociocultural.load()],
            "spatial_memory": [item.to_dict() for item in self.spatial.load()],
            "temporal_rhythm_memory": [item.to_dict() for item in self.temporal_rhythm.load()],
            "body_rhythm_memory": [item.to_dict() for item in self.body_rhythm.load()],
            "tom_history": [item.to_dict() for item in self.tom_history.load()],
            "self_continuity": [item.to_dict() for item in self.self_continuity.load()],
            "homeostatic_baseline": [item.to_dict() for item in self.homeostatic_baseline.load()],
            "agent_mind": self.agent_mind.to_dict(),
            "living_user": self.living_user.summary(include_sensitive=False, limit=80),
        }

    def save(self, snapshot: Mapping[str, Any] | None = None) -> None:
        """Lưu các state được truyền vào, không rewrite toàn bộ memory store.

        Các semantic/episodic/... store đã tự persist ngay trong ``commit``.
        Việc load rồi save lại mọi file ở đây vừa thừa vừa tạo contention và
        có thể ghi đè dữ liệu mới bằng snapshot cũ.
        """
        with self._save_lock:
            if snapshot is None:
                # Không dựng full snapshot chỉ để ghi lại chính những file vừa đọc.
                self.agent_mind.save()
                return
            snapshot = dict(snapshot)
            if "internal_state" in snapshot:
                active_state = dict(snapshot.get("active_state") or {})
                metadata = dict(active_state.get("metadata") or {})
                metadata["internal_state"] = snapshot.get("internal_state") or {}
                active_state["metadata"] = metadata
                snapshot["active_state"] = active_state
            if "active_state" in snapshot and isinstance(snapshot["active_state"], Mapping):
                self.active_state.save(ActiveStateSnapshot.from_dict(dict(snapshot["active_state"])))
            if "agent_mind" in snapshot and isinstance(snapshot["agent_mind"], Mapping):
                self.agent_mind.replace_from_dict(snapshot["agent_mind"])
            else:
                self.agent_mind.save()

    def render_context(self, query: MemoryQuery | str = "", *, snapshot: Any = None, frame: Any = None, max_lines: int = 18) -> str:
        packet = self.recall(query or self._query_from_snapshot(snapshot), frame=frame)
        return self.render_context_for_packet(packet, max_lines=max_lines)

    def render_context_for_packet(self, packet: MemoryPacket, *, max_lines: int = 18) -> str:
        """Cùng renderer với render_context, nhưng dùng packet đã chọn sẵn."""
        parts = [packet.render_prompt_context(max_lines=max_lines)]
        active = self.active_state.load()
        active_lines = []
        for label, value in (
            ("current_goal", active.current_goal),
            ("active_project", active.active_project),
            ("active_task", active.active_task),
            ("session_arc", active.session_arc),
        ):
            text = compact_text(value, limit=180)
            if text and text != "unknown":
                active_lines.append(f"- {label}: {text}")
        if active_lines:
            parts.append("active_state:\n" + "\n".join(active_lines))
        return "\n\n".join(part for part in parts if part).strip()

    def build_commit_request(
        self,
        *,
        target: str,
        content: Any,
        confidence: float,
        evidence_ids: list[str],
        reason: str,
        storage_permission: bool,
        permission_required: bool = True,
        decay_policy: str = "normal",
    ) -> StateCommitRequest:
        return StateCommitRequest(
            target=target,
            content=content,
            confidence=confidence,
            evidence_ids=evidence_ids,
            permission_required=permission_required,
            storage_permission=storage_permission,
            decay_policy=decay_policy,
            reason=reason,
            metadata={
                "permission_required": permission_required,
                "storage_permission": storage_permission,
                "decay_policy": decay_policy,
            },
        )

    def _normalize_request(self, request: Any) -> Any:
        target = str(_request_get(request, "target", "") or "")
        metadata = _request_metadata(request)
        content = _request_get(request, "content", "")
        normalized_content = _content_for_target(target, content)
        metadata.setdefault("raw_content", content)
        return SimpleNamespace(
            target=target,
            content=normalized_content,
            confidence=clamp01(float(_request_get(request, "confidence", 0.0) or 0.0)),
            evidence_ids=[str(item) for item in _request_get(request, "evidence_ids", []) or [] if str(item).strip()],
            permission_required=bool(_request_get(request, "permission_required", metadata.get("permission_required", True))),
            storage_permission=bool(_request_get(request, "storage_permission", metadata.get("storage_permission", False))),
            decay_policy=str(_request_get(request, "decay_policy", metadata.get("decay_policy", "normal")) or "normal"),
            reason=str(_request_get(request, "reason", "") or ""),
            frame_id=_request_get(request, "frame_id", metadata.get("frame_id", "")),
            source_module=_request_get(request, "source_module", metadata.get("source_module", "")),
            tags=list(_request_get(request, "tags", metadata.get("tags", [])) or []),
            metadata=metadata,
        )


    def search_memory_candidates(self, query: str = "", *, limit: int = 8) -> list[UnresolvedMemoryCandidate]:
        return self.memory_candidate.search(query, limit=limit)

    def candidate_trace_for_query(self, query: str = "") -> str:
        candidates = self.search_memory_candidates(query, limit=1)
        if not candidates:
            return ""
        return candidates[0].recall_line()

    def load_active_state(self) -> ActiveStateSnapshot:
        return self.active_state.load()

    def _check_commit_permission(self, request: Any, *, target: str, confidence: float, evidence_ids: list[str]) -> tuple[bool, str]:
        metadata = getattr(request, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        storage_permission = bool(
            getattr(request, "storage_permission", False)
            or metadata.get("storage_permission")
            or metadata.get("trusted_internal_commit")
        )
        permission_required = bool(getattr(request, "permission_required", metadata.get("permission_required", True)))
        decay_policy = str(getattr(request, "decay_policy", metadata.get("decay_policy", "normal")) or "normal")
        if not storage_permission:
            return False, "storage_permission_false"
        if confidence < self.policy.min_confidence:
            return False, f"confidence_below_floor:{confidence:.2f}"
        if target == "relational_memory" and confidence < self.policy.relational_min_confidence:
            return False, f"relational_confidence_below_floor:{confidence:.2f}"
        if target == "repair_memory" and confidence < self.policy.repair_min_confidence:
            return False, f"repair_confidence_below_floor:{confidence:.2f}"
        if self.policy.require_evidence_ids and not evidence_ids and not (
            self.policy.allow_session_only_without_evidence and decay_policy == "session_only"
        ):
            return False, "missing_evidence_ids"
        if permission_required and not storage_permission:
            return False, "permission_required_but_not_granted"
        return True, "ok"

    def _commit_event_ledger(self, request: Any, *, frame: Any = None) -> EventLedgerRecord:
        request_tags = tuple(str(tag) for tag in (_request_get(request, "tags", []) or []) if str(tag).strip())
        request_metadata = _request_metadata(request)
        decay_policy = str(_request_get(request, "decay_policy", request_metadata.get("decay_policy", "normal")) or "normal")
        ephemeral_presence = bool(
            request_metadata.get("ephemeral_presence")
            or "ephemeral_presence" in request_tags
            or "signal_buffer_only" in request_tags
            or decay_policy == "session_only"
        )
        metadata = {
            "commit_reason": compact_text(_request_get(request, "reason", "") or "", limit=260),
            "commit_confidence": clamp01(float(_request_get(request, "confidence", 0.0) or 0.0)),
            "commit_target": str(_request_get(request, "target", "event_ledger") or "event_ledger"),
            "decay_policy": decay_policy,
            "request_tags": list(request_tags),
            "ephemeral_presence": ephemeral_presence,
        }
        metadata.update({key: value for key, value in request_metadata.items() if key not in metadata})
        return self.event_ledger.append_raw_event(
            frame_id=str(getattr(frame, "frame_id", "") or _request_get(request, "frame_id", "") or ""),
            source="state_commit_request",
            content=compact_text(_request_get(request, "content", "") or "", limit=1000),
            evidence_ids=_request_get(request, "evidence_ids", []) or [],
            reliability=clamp01(float(_request_get(request, "confidence", 1.0) or 1.0)),
            privacy_level="normal",
            tags=("commit", *request_tags),
            metadata=metadata,
        )

    def _query_from_frame(self, frame: Any) -> str:
        if frame is None:
            return ""
        parts: list[str] = []
        for attr in ("workspace", "working_memory", "salience", "meaning", "tom"):
            value = getattr(frame, attr, None)
            if value is None:
                continue
            parts.append(compact_text(getattr(value, "dominant_thread", "") or getattr(value, "dominant_focus", "") or getattr(value, "broadcast_reason", "") or value, limit=220))
        evidence = getattr(frame, "evidence", []) or []
        for item in evidence[:4]:
            parts.append(compact_text(getattr(item, "claim", item), limit=180))
        return compact_text(" ".join(part for part in parts if part), limit=640)

    def _query_from_snapshot(self, snapshot: Any) -> str:
        if snapshot is None:
            return ""
        parts = []
        for attr in ("focus_summary", "active_window_kind", "active_app", "active_window"):
            value = compact_text(getattr(snapshot, attr, "") or "", limit=180)
            if value:
                parts.append(value)
        return compact_text(" ".join(parts), limit=520)

    def _budget_from_frame(self, frame: Any) -> BudgetMode:
        mode = str(getattr(frame, "budget_mode", "normal") or "normal")
        if mode in {"fast", "normal", "deep", "reflection"}:
            return mode  # type: ignore[return-value]
        return "normal"

    def _quotas(self, *, mode: BudgetMode, max_items: int) -> dict[str, int]:
        if mode == "fast":
            base = {"semantic": 2, "relational": 1, "episodic": 1, "procedural": 1, "affective_residue": 1, "repair": 1}
        elif mode == "deep":
            base = {"semantic": 6, "relational": 4, "episodic": 4, "procedural": 4, "affective_residue": 3, "repair": 4}
        elif mode == "reflection":
            base = {"semantic": 4, "relational": 5, "episodic": 5, "procedural": 3, "affective_residue": 5, "repair": 6}
        else:
            base = {"semantic": 4, "relational": 3, "episodic": 3, "procedural": 2, "affective_residue": 2, "repair": 2}
        scale = max(1, int(max_items)) / max(1, sum(base.values()))
        return {key: max(1, int(value * scale)) for key, value in base.items()}

    def decay_canonical_long_term(self) -> dict[str, dict[str, int]]:
        """Run evidence-preserving decay across canonical long-term stores."""
        stores = {
            "belief_core": self.belief_store,
            "worldview_memory": self.worldview,
            "sociocultural_memory": self.sociocultural,
            "spatial_memory": self.spatial,
            "temporal_rhythm_memory": self.temporal_rhythm,
            "body_rhythm_memory": self.body_rhythm,
            "tom_history": self.tom_history,
            "self_continuity": self.self_continuity,
            "homeostatic_baseline": self.homeostatic_baseline,
        }
        return {name: store.decay() for name, store in stores.items()}

    # Legacy hook giữ lại để downstream cũ không chết nếu còn gọi nhầm.
    def _packet_with_retrieval_resonance(self, packet: MemoryPacket, *, query_text: str) -> MemoryPacket:
        """No-op compatibility helper.

        Resonance v2 đã nằm trong MemoryRetrievalEngine.
        Hàm này cố ý không làm gì nữa. Nếu thêm token overlap lại ở đây,
        repo sẽ quay về đúng cái hố vừa bò ra.
        """
        return packet
