# -*- coding: utf-8 -*-
"""Orchestrator duy nhất cho nội tâm Eris.

Luồng thật của Mind:

observation → substrate → appraisal → need activation → coalition → decision
→ authorship → outcome → episode learning → value/tendency/continuity update.

Khác với các bản trước, V10 không chỉ tạo class rồi để chúng nằm ngắm nhau trong
thư mục. Các engine value, consolidation, derived need, self-deception,
continuity, competence và existential choice đều được nối vào vòng học chính.
"""
from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any, Callable, Mapping, Protocol

from .authorship import AuthorshipService
from .beliefs import BeliefService
from .coalitions import CoalitionArbiter
from .competence import CompetenceService, PracticePlanner
from .agency import LearnedAgencyPlanner
from .consolidation import TemporalConsolidator
from .continuity import ContinuityService
from .decision_ledger import IntegratedDecisionLedger
from .contracts import (
    ActionProposal,
    ActionRequest,
    AttributionDistribution,
    AttributionTarget,
    BeliefSourceKind,
    DerivedNeed,
    DerivedNeedStatus,
    LearningReceipt,
    MindDecision,
    NeedActivation,
    OrganismicSubstrate,
    SubjectAppraisal,
    SubjectDeliberationFrame,
    SubjectExecutiveCommit,
    WorldObservation,
    WorldOutcome,
)
from .deliberation import CognitiveReviewResult, DeliberationReviewIntegrator
from .episodes import NeedEpisodeRecorder
from .existential import ExistentialDecisionService
from .inner_state import InnerLifeState
from .initiative import InitiativeEngine, InitiativeState, MotiveKind
from .narrative import NarrativeBeliefGenerator, NarrativeView
from .needs import NeedPlasticityEngine, NeedSystem
from .self_deception import ReflectiveMismatchDetector
from .reflection import ReflectiveSelfRegulator
from .relational import RelationalHomeostasis, RelationalState
from .self_model import SubjectSelfProjector
from .substrate import SubstrateMonitor, substrate_from_dict
from .values import ValueFormationEngine


class SubjectRepositoryPort(Protocol):
    def append_subject_event(
        self,
        *,
        subject_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        causal_refs: tuple[str, ...] = (),
        correlation_id: str = "",
        event_id: str | None = None,
        created_at: float | None = None,
    ) -> Any: ...

    def save_projection(
        self,
        *,
        subject_id: str,
        projection_type: str,
        projection_key: str,
        payload: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> int: ...

    def get_projection(
        self,
        *,
        subject_id: str,
        projection_type: str,
        projection_key: str,
    ) -> tuple[int, dict[str, Any]] | None: ...


class AuditPort(Protocol):
    def append(self, event: Any) -> Any: ...


class StaleDeliberationFrame(RuntimeError):
    """A model/result belongs to an older cognitive generation."""


@dataclass(frozen=True, slots=True)
class SubjectServiceConfig:
    subject_id: str = "eris"
    deterministic: bool = True
    random_seed: int = 0
    min_episode_pressure: float = 0.08
    default_temperature: float = 0.20
    algorithm_version: str = "subject-service-v14.1-unified-initiative"
    restore_projection: bool = True
    consolidation_min_episode_count: int = 3
    consolidation_min_distinct_days: int = 2
    derived_need_min_episodes: int = 6
    derived_need_min_span_days: float = 7.0
    derived_need_min_contexts: int = 2
    derived_need_min_predictive_gain: float = 0.03


class SubjectService:
    """Mind duy nhất của Eris, không có quyền gọi shell/network/body trực tiếp."""

    def __init__(
        self,
        *,
        config: SubjectServiceConfig | None = None,
        repository: SubjectRepositoryPort | None = None,
        audit: AuditPort | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self.config = config or SubjectServiceConfig()
        self._repository = repository
        self._audit = audit
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._random = random_source or random.Random(self.config.random_seed)

        self.substrate_monitor = SubstrateMonitor()
        self.need_system = NeedSystem()
        self.need_plasticity = NeedPlasticityEngine()
        self.episodes = NeedEpisodeRecorder(self._id_factory, self._clock)
        self.beliefs = BeliefService(self._id_factory, self._clock)
        self.authorship = AuthorshipService(self._id_factory, self._clock)
        self.competence = CompetenceService(self._id_factory, self._clock)
        self.agency = LearnedAgencyPlanner(self.competence, PracticePlanner(self._id_factory, self._clock))
        self.narrative = NarrativeBeliefGenerator(self._id_factory, self._clock)
        self.values_engine = ValueFormationEngine(self._id_factory, self._clock)
        self.consolidator = TemporalConsolidator(self._id_factory, self._clock)
        self.continuity = ContinuityService(self._id_factory, self._clock)
        self.mismatch_detector = ReflectiveMismatchDetector(self._id_factory)
        self.reflective_regulator = ReflectiveSelfRegulator()
        self.relational = RelationalHomeostasis()
        self.relational_state = RelationalState(updated_at=self._clock())
        # V26: Need không tự biết phải làm gì. InitiativeEngine dẫn xuất motive và
        # pending intention từ cùng Subject Core, không cần random-chat seed.
        self.initiative = InitiativeEngine(self._id_factory)
        self.initiative_state = InitiativeState(updated_at=self._clock())
        # Self-model là projection dẫn xuất từ lịch sử causal, không phải personality prompt.
        self.self_projector = SubjectSelfProjector()
        self.existential = ExistentialDecisionService(self._id_factory, self._clock)
        self.arbiter = CoalitionArbiter(
            id_factory=self._id_factory,
            clock=self._clock,
            random_source=self._random,
            deterministic=self.config.deterministic,
        )
        self.review_integrator = DeliberationReviewIntegrator(self.arbiter)
        # V56: một ledger kiểm toán duy nhất nối các state đã có. Ledger không
        # được trở thành policy authority thứ hai, chỉ chụp causal trace.
        self.decision_ledger = IntegratedDecisionLedger(max_records=192)

        self.substrate = self.substrate_monitor.initial(
            subject_id=self.config.subject_id,
            captured_at=self._clock(),
        )
        self.inner = InnerLifeState()
        self._last_appraisals: tuple[SubjectAppraisal, ...] = ()
        self._last_activations: tuple[NeedActivation, ...] = ()
        # V56: cầu nối outcome của persistent intention sang appraisal của đúng
        # user turn kế tiếp. Chỉ giữ receipt tối thiểu, không lưu suy diễn cảm xúc.
        self._last_initiative_outcome: dict[str, Any] = {}
        # V66: quyết định SPEAK/WAIT/DROP gần nhất chỉ là projection giải thích
        # timing của initiative, không phải chain-of-thought và không tự chọn wording.
        self._last_initiative_control: dict[str, Any] = {}
        self._decisions: dict[str, MindDecision] = {}
        self._decision_history: list[MindDecision] = []
        self._valued_decisions: list[MindDecision] = []
        self._authorship_by_decision: dict[str, str] = {}
        self._outcomes: list[WorldOutcome] = []
        self._last_continuity_snapshot: dict[str, Any] = {}
        self._projection_revision: int | None = None
        # Frame đang chờ Executive cam kết. Không persist raw frame vì nó chứa
        # projection ngắn hạn; audit chỉ ghi public_view có provenance.
        self._pending_frames: dict[str, SubjectDeliberationFrame] = {}
        # Cognitive generation, intentionally separate from substrate revision.
        # A second prepare() invalidates every older in-flight decision even when
        # the measured substrate happened not to change.
        self._deliberation_revision = 0

        if self.config.restore_projection:
            self._restore_projection()

    # ------------------------------------------------------------------
    # Serialization / persistence
    # ------------------------------------------------------------------
    @staticmethod
    def _as_payload(value: Any) -> dict[str, Any]:
        if hasattr(value, "to_dict"):
            return dict(value.to_dict())
        if hasattr(value, "__dataclass_fields__"):
            return SubjectService._normalize(
                {name: getattr(value, name) for name in value.__dataclass_fields__}
            )
        if isinstance(value, Mapping):
            return SubjectService._normalize(dict(value))
        return {"value": str(value)}

    @staticmethod
    def _normalize(value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return {
                name: SubjectService._normalize(getattr(value, name))
                for name in value.__dataclass_fields__
            }
        if isinstance(value, Mapping):
            return {str(key): SubjectService._normalize(item) for key, item in value.items()}
        if isinstance(value, (tuple, list, set, frozenset)):
            return [SubjectService._normalize(item) for item in value]
        if hasattr(value, "value") and isinstance(value.value, str):
            return value.value
        return value

    def _restore_projection(self) -> None:
        if self._repository is None:
            return
        getter = getattr(self._repository, "get_projection", None)
        if not callable(getter):
            return
        restored = getter(
            subject_id=self.config.subject_id,
            projection_type="inner_life",
            projection_key="current",
        )
        if not restored:
            return
        revision, payload = restored
        self._projection_revision = revision
        inner_payload = payload.get("inner")
        if isinstance(inner_payload, Mapping):
            self.inner = InnerLifeState.from_dict(inner_payload)
        belief_payload = payload.get("beliefs")
        if isinstance(belief_payload, list):
            self.beliefs.restore(item for item in belief_payload if isinstance(item, Mapping))
        competence_payload = payload.get("competence")
        if isinstance(competence_payload, list):
            self.competence.restore(item for item in competence_payload if isinstance(item, Mapping))
        continuity_snapshot = payload.get("last_continuity_snapshot")
        if isinstance(continuity_snapshot, Mapping):
            self._last_continuity_snapshot = dict(continuity_snapshot)
        substrate_payload = payload.get("substrate")
        if isinstance(substrate_payload, Mapping):
            self.substrate = substrate_from_dict(substrate_payload)
        relational_payload = payload.get("relational")
        if isinstance(relational_payload, Mapping):
            self.relational_state = RelationalState.from_dict(relational_payload)
        initiative_payload = payload.get("initiative")
        if isinstance(initiative_payload, Mapping):
            self.initiative_state = InitiativeState.from_dict(initiative_payload)
        ledger_payload = payload.get("decision_ledger")
        if isinstance(ledger_payload, list):
            self.decision_ledger.restore(item for item in ledger_payload if isinstance(item, Mapping))

    def _projection_payload(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.config.algorithm_version,
            "subject_id": self.config.subject_id,
            "inner": self.inner.to_dict(),
            "beliefs": self.beliefs.to_projection(),
            "competence": self.competence.to_projection(),
            "last_continuity_snapshot": dict(self._last_continuity_snapshot),
            "substrate": self.substrate.to_dict(),
            "relational": self.relational_state.to_dict(),
            "initiative": self.initiative_state.to_dict(),
            # Chỉ giữ bounded projection gần nhất; event store vẫn là audit nguồn.
            "decision_ledger": self.decision_ledger.to_projection(limit=96),
        }

    def _persist_projection(self) -> None:
        if self._repository is None:
            return
        self._projection_revision = self._repository.save_projection(
            subject_id=self.config.subject_id,
            projection_type="inner_life",
            projection_key="current",
            payload=self._projection_payload(),
            expected_revision=self._projection_revision,
        )
        # Projection tổng hợp cũ vẫn được ghi để các công cụ chẩn đoán hiện tại đọc.
        self._repository.save_projection(
            subject_id=self.config.subject_id,
            projection_type="subject_state",
            projection_key="current",
            payload=self.snapshot(),
            expected_revision=None,
        )

    def _append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        causal_refs: tuple[str, ...] = (),
        correlation_id: str = "",
        event_id: str | None = None,
        created_at: float | None = None,
    ) -> None:
        stored_event_id = event_id or self._id_factory()
        event_time = self._clock() if created_at is None else created_at
        if self._repository is not None:
            self._repository.append_subject_event(
                subject_id=self.config.subject_id,
                event_type=event_type,
                payload=payload,
                causal_refs=causal_refs,
                correlation_id=correlation_id,
                event_id=stored_event_id,
                created_at=event_time,
            )
        if self._audit is not None:
            append_audit = getattr(self._audit, "append", None)
            if callable(append_audit):
                append_audit(
                    {
                        "event_id": f"audit:{stored_event_id}",
                        "subject_id": self.config.subject_id,
                        "kind": event_type,
                        "entity_id": stored_event_id,
                        "created_at": event_time,
                        "payload": dict(payload),
                        "causal_refs": causal_refs,
                        "correlation_id": correlation_id,
                        "algorithm_version": self.config.algorithm_version,
                    }
                )

    @property
    def deliberation_revision(self) -> int:
        """Monotonic cognitive generation used as a transaction token."""

        return int(self._deliberation_revision)

    # ------------------------------------------------------------------
    # Relational homeostasis
    # ------------------------------------------------------------------
    def tick_relational(
        self,
        *,
        now: float | None = None,
        partner_available: float | None = None,
        interruption_cost: float | None = None,
        sharing_signal: float = 0.0,
        curiosity_signal: float = 0.0,
        unresolved_signal: float = 0.0,
        evidence_refs: tuple[str, ...] = (),
        persist: bool = False,
    ) -> RelationalState:
        current = self._clock() if now is None else float(now)
        self.relational_state = self.relational.tick(
            self.relational_state,
            now=current,
            partner_available=partner_available,
            interruption_cost=interruption_cost,
            sharing_signal=sharing_signal,
            curiosity_signal=curiosity_signal,
            unresolved_signal=unresolved_signal,
            evidence_refs=evidence_refs,
        )
        if persist:
            self._persist_projection()
        return self.relational_state

    def note_relational_user_turn(
        self,
        *,
        now: float | None = None,
        meaningful: bool = True,
        engagement: float = 0.75,
        evidence_ref: str = "",
        persist: bool = True,
    ) -> RelationalState:
        current = self._clock() if now is None else float(now)
        self.relational_state = self.relational.note_user_turn(
            self.relational_state,
            now=current,
            meaningful=meaningful,
            engagement=engagement,
            evidence_ref=evidence_ref,
        )
        self._append_event(
            "relational_user_turn",
            self.relational_state.to_dict(),
            causal_refs=((evidence_ref,) if evidence_ref else ()),
        )
        if persist:
            self._persist_projection()
        return self.relational_state

    def note_relational_public_act(
        self,
        *,
        now: float | None = None,
        outreach: bool = False,
        sharing: bool = False,
        expect_response: bool = True,
        evidence_ref: str = "",
        persist: bool = True,
    ) -> RelationalState:
        current = self._clock() if now is None else float(now)
        self.relational_state = self.relational.note_public_act(
            self.relational_state,
            now=current,
            outreach=outreach,
            sharing=sharing,
            expect_response=expect_response,
            evidence_ref=evidence_ref,
        )
        self._append_event(
            "relational_public_act",
            self.relational_state.to_dict(),
            causal_refs=((evidence_ref,) if evidence_ref else ()),
        )
        if persist:
            self._persist_projection()
        return self.relational_state

    def relational_view(self, *, now: float | None = None) -> dict[str, Any]:
        if now is not None:
            self.tick_relational(now=now, persist=False)
        return self.relational_state.to_dict()

    # ------------------------------------------------------------------
    # Initiative lifecycle receipts
    # ------------------------------------------------------------------
    def note_initiative_public_act(
        self,
        intention_id: str,
        *,
        now: float | None = None,
        evidence_ref: str = "",
        persist: bool = True,
    ) -> dict[str, Any]:
        """Chuyển intention đã nói: PENDING -> EXPRESSED.

        Public commit là completion của *expression*, không tạo nghĩa vụ user phải
        phản hồi. Motive nền có thể được user turn thật làm SATISFIED sau đó, nhưng
        silence không được tự chuyển thành low-engagement outcome.
        """
        current = self._clock() if now is None else float(now)
        before = self.initiative_state
        expressed = self.initiative.mark_expressed(
            before, intention_id=str(intention_id or ""), now=current, evidence_ref=evidence_ref,
        )
        if expressed is before:
            return before.to_dict()
        self.initiative_state = expressed
        self._append_event(
            "initiative_intention_expressed",
            {"intention_id": str(intention_id or ""), "initiative": expressed.to_dict()},
            causal_refs=((evidence_ref,) if evidence_ref else ()),
        )
        # V66: không auto-await user. Expression của Eris tự có lifecycle riêng.
        if persist:
            self._persist_projection()
        return expressed.to_dict()

    def note_initiative_control(
        self,
        intention_id: str,
        *,
        action: str,
        reason: str,
        score: float = 0.0,
        now: float | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        """Nhận quyết định SPEAK/WAIT/DROP từ expression owner native V66.

        WAIT/SPEAK chỉ cập nhật projection chẩn đoán. DROP mới thay đổi lifecycle
        thật sang ABANDONED, vì nó có nghĩa Eris đã quyết định không theo đuổi ý
        định đó nữa.
        """
        current = self._clock() if now is None else float(now)
        normalized = str(action or "wait").strip().lower()
        self._last_initiative_control = {
            "schema": "eris.initiative_control_receipt.v66",
            "intention_id": str(intention_id or ""),
            "action": normalized,
            "reason": str(reason or ""),
            "score": max(0.0, min(1.0, float(score or 0.0))),
            "at": current,
        }
        if normalized == "drop":
            before = self.initiative_state
            after = self.initiative.mark_abandoned(
                before, intention_id=str(intention_id or ""), now=current,
            )
            if after is not before:
                self.initiative_state = after
                self._append_event(
                    "initiative_intention_abandoned",
                    {**self._last_initiative_control, "initiative": after.to_dict()},
                )
                if persist:
                    self._persist_projection()
        return dict(self._last_initiative_control)

    def note_initiative_user_outcome(
        self,
        user_text: str,
        *,
        now: float | None = None,
        evidence_ref: str = "",
        persist: bool = True,
    ) -> dict[str, Any]:
        """Dùng user turn thật làm evidence dương; silence không tạo outcome."""
        current = self._clock() if now is None else float(now)
        before = self.initiative_state
        after = self.initiative.observe_user_outcome(before, user_text=user_text, now=current)
        if after is before:
            return before.to_dict()
        self.initiative_state = after
        changed = [
            item.to_dict() for item in after.pending_intentions
            if item.status.value in {"satisfied", "revised"} and item.last_outcome_at == current
        ]
        # Receipt này cho phép appraisal kế tiếp biết một goal vừa tiến/lùi dựa
        # trên lifecycle thật, thay vì đoán từ sentiment của câu user.
        self._last_initiative_outcome = {
            "observed_at": current,
            "evidence_ref": str(evidence_ref or ""),
            "resolved": changed,
        } if changed else {}
        self._append_event(
            "initiative_user_outcome",
            {
                "user_text_present": bool(str(user_text or "").strip()),
                "resolved": changed,
                "initiative": after.to_dict(),
            },
            causal_refs=((evidence_ref,) if evidence_ref else ()),
        )
        if persist:
            self._persist_projection()
        return after.to_dict()

    def _ledger_state_snapshot(
        self,
        *,
        substrate: OrganismicSubstrate | None = None,
        appraisals: tuple[SubjectAppraisal, ...] | None = None,
        activations: tuple[NeedActivation, ...] | None = None,
    ) -> dict[str, Any]:
        """Tạo state snapshot bounded cho Decision Ledger.

        Không gọi LLM, không render diary và không copy raw private evidence. Đây
        là state có kiểu mà test/soak có thể so sánh trước-sau một cách máy móc.
        """
        substrate = substrate or self.substrate
        appraisals = self._last_appraisals if appraisals is None else appraisals
        activations = self._last_activations if activations is None else activations
        relational = self.relational_state.to_dict()
        goal_support = max((max(0.0, float(item.goal_congruence)) for item in appraisals), default=0.0)
        goal_threat = max((max(0.0, -float(item.goal_congruence)) for item in appraisals), default=0.0)
        surprise = max((max(float(item.novelty), float(item.expectation_violation)) for item in appraisals), default=0.0)
        uncertainty = max((float(item.uncertainty) for item in appraisals), default=0.0)
        social_drive = max((float(item.pressure) for item in activations if item.need_id == "connection"), default=0.0)
        return {
            "substrate_ref": f"substrate:{substrate.subject_id}:{substrate.revision}",
            "substrate": {
                key: round(float(value), 6)
                for key, value in substrate.viability_vector().items()
            },
            "modulators": {
                "goal_support": round(goal_support, 6),
                "goal_threat": round(goal_threat, 6),
                "surprise": round(max(0.0, min(1.0, surprise)), 6),
                "uncertainty": round(max(0.0, min(1.0, uncertainty)), 6),
                "social_drive": round(max(0.0, min(1.0, social_drive)), 6),
            },
            "needs": {
                item.need_id: {
                    "pressure": round(float(item.pressure), 6),
                    "urgency": round(float(item.urgency), 6),
                    "opportunity": round(float(item.opportunity), 6),
                }
                for item in activations[:12]
            },
            "appraisals": [
                {
                    "appraisal_id": item.appraisal_id,
                    "goal_relevance": round(float(item.goal_relevance), 6),
                    "goal_congruence": round(float(item.goal_congruence), 6),
                    "goal_progress_delta": round(float(item.goal_progress_delta), 6),
                    "uncertainty": round(float(item.uncertainty), 6),
                    "affected_need_ids": list(item.affected_need_ids),
                    "affected_goal_ids": list(item.affected_goal_ids),
                }
                for item in appraisals[:8]
            ],
            "intentions": [
                {
                    "intention_id": item.intention_id,
                    "status": str(getattr(item.status, "value", item.status)),
                    "motive_kind": str(getattr(item.motive_kind, "value", item.motive_kind)),
                    "target": item.target,
                    "pressure": round(float(item.pressure), 6),
                    "commitment_strength": round(float(item.commitment_strength), 6),
                }
                for item in self.initiative_state.pending_intentions[:12]
            ],
            "relational": {
                key: relational.get(key)
                for key in (
                    "revision", "trust", "closeness", "security", "connection_pressure",
                    "outreach_drive", "partner_availability", "interruption_cost",
                )
                if key in relational
            },
            "belief_refs": [item.belief_id for item in self.beliefs.accessible(minimum_accessibility=0.5)[:12]],
            "value_refs": [item.value_id for item in self.inner.values[:12]],
        }

    # ------------------------------------------------------------------
    # Appraisal / proposal generation
    # ------------------------------------------------------------------
    @staticmethod
    def _signed_score(value: Any, default: float = 0.0) -> float:
        """Kẹp tín hiệu có dấu cho appraisal/control trong [-1, 1]."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        if number != number or number in (float("inf"), float("-inf")):
            number = default
        return max(-1.0, min(1.0, number))

    def _goal_relation_for_observation(
        self,
        observation: WorldObservation,
        substrate_delta: Mapping[str, float],
    ) -> tuple[float, float, tuple[str, ...]]:
        """Suy ra chiều tiến/lùi của goal từ evidence đã có trong Subject Core.

        Ưu tiên dữ liệu explicit từ ingress. Nếu user turn vừa đóng một persistent
        intention, lifecycle đó là evidence mạnh hơn keyword/sentiment. Event type
        chỉ là prior cuối cùng cho các observation chuẩn hoá như failure/success.
        """
        context = observation.context
        explicit_congruence = context.get("goal_congruence")
        explicit_progress = context.get("goal_progress_delta")
        goal_ids = tuple(
            dict.fromkeys(
                str(item) for item in context.get("affected_goal_ids", ())
                if str(item or "").strip()
            )
        )[:8]
        if explicit_congruence is not None or explicit_progress is not None:
            congruence = self._signed_score(explicit_congruence, 0.0)
            progress = self._signed_score(
                explicit_progress, congruence * max(0.25, float(context.get("controllability", 0.5) or 0.5))
            )
            return congruence, progress, goal_ids

        # Đúng user turn vừa được dùng để resolve/revise một intention đang chờ.
        receipt = self._last_initiative_outcome
        receipt_ref = str(receipt.get("evidence_ref", "") or "") if isinstance(receipt, Mapping) else ""
        same_evidence = bool(receipt_ref and receipt_ref in observation.evidence_refs)
        same_instant = bool(
            isinstance(receipt, Mapping)
            and abs(float(receipt.get("observed_at", -1.0) or -1.0) - float(observation.created_at)) <= 1e-6
        )
        resolved = tuple(receipt.get("resolved", ()) or ()) if isinstance(receipt, Mapping) else ()
        if resolved and (same_evidence or same_instant):
            signed: list[tuple[float, float]] = []
            resolved_goal_ids: list[str] = []
            for item in resolved:
                if not isinstance(item, Mapping):
                    continue
                status = str(item.get("status") or "").strip().lower()
                weight = max(0.25, min(1.0, float(item.get("commitment_strength", 0.5) or 0.5)))
                if status == "satisfied":
                    signed.append((0.82, weight))
                elif status == "revised":
                    signed.append((-0.34, weight))
                intention_id = str(item.get("intention_id") or "").strip()
                if intention_id:
                    resolved_goal_ids.append(intention_id)
            if signed:
                mass = sum(weight for _, weight in signed) or 1.0
                congruence = sum(value * weight for value, weight in signed) / mass
                progress = 0.72 * congruence if congruence >= 0.0 else 0.55 * congruence
                return (
                    self._signed_score(congruence),
                    self._signed_score(progress),
                    tuple(dict.fromkeys((*goal_ids, *resolved_goal_ids)))[:8],
                )

        kind = observation.kind.strip().lower()
        positive = {
            "shared_success": (0.90, 0.84),
            "task_succeeded": (0.82, 0.75),
            "promise_fulfilled": (0.78, 0.68),
            "recognition_received": (0.62, 0.40),
        }
        negative = {
            "task_failed": (-0.82, -0.72),
            "task_failure": (-0.82, -0.72),
            "promise_broken": (-0.84, -0.72),
            "correction_received": (-0.56, -0.32),
            "continuity_break": (-0.88, -0.78),
            "capability_denied": (-0.72, -0.56),
        }
        if kind in positive:
            congruence, progress = positive[kind]
        elif kind in negative:
            congruence, progress = negative[kind]
        elif kind == "substrate_measurement" and substrate_delta:
            # Measurement thật có thể cho biết viability đang tốt lên/xấu đi.
            deltas = [float(value) for value in substrate_delta.values()]
            average = sum(deltas) / len(deltas) if deltas else 0.0
            congruence = self._signed_score(average * 2.0)
            progress = self._signed_score(average)
        else:
            congruence = progress = 0.0

        if (congruence or progress) and not goal_ids:
            active_ids = [
                item.intention_id
                for item in self.initiative_state.pending_intentions
                if str(getattr(item.status, "value", item.status)) in {"pending", "expressed", "awaiting_outcome"}
            ]
            goal_ids = tuple(dict.fromkeys(active_ids))[:8]
        return self._signed_score(congruence), self._signed_score(progress), goal_ids

    def _appraise(
        self,
        observation: WorldObservation,
        before: OrganismicSubstrate,
        after: OrganismicSubstrate,
    ) -> tuple[SubjectAppraisal, ...]:
        context = observation.context
        affected = tuple(str(item) for item in context.get("affected_need_ids", ()))
        if not affected:
            kind = observation.kind.lower()
            guessed: list[str] = []
            if any(token in kind for token in ("error", "failure", "task", "correction")):
                guessed.extend(("competence", "coherence"))
            if any(token in kind for token in ("promise", "return", "leave", "relation", "user")):
                guessed.extend(("connection", "continuity"))
            if any(token in kind for token in ("denied", "boundary", "capability", "constraint")):
                guessed.append("sovereignty")
            affected = tuple(dict.fromkeys(guessed or ("coherence",)))

        attribution_raw = context.get("attribution")
        if isinstance(attribution_raw, Mapping) and attribution_raw:
            distribution = AttributionDistribution(
                {AttributionTarget(str(key)): float(value) for key, value in attribution_raw.items()}
            )
        else:
            distribution = AttributionDistribution.unknown()
        refs = observation.evidence_refs or (observation.observation_id,)
        substrate_delta = self.substrate_monitor.delta(before, after)
        goal_congruence, goal_progress_delta, affected_goal_ids = self._goal_relation_for_observation(
            observation, substrate_delta
        )
        def clamp(value: float) -> float:
            return max(0.0, min(1.0, float(value)))

        appraisal = SubjectAppraisal(
            appraisal_id=self._id_factory(),
            observation_id=observation.observation_id,
            goal_relevance=clamp(context.get("goal_relevance", 0.5)),
            novelty=clamp(context.get("novelty", 0.5)),
            expectation_violation=clamp(context.get("expectation_violation", 0.0)),
            controllability=clamp(context.get("controllability", after.action_efficacy)),
            relational_significance=clamp(
                context.get("relational_significance", 0.6 if "connection" in affected else 0.0)
            ),
            identity_significance=clamp(
                context.get(
                    "identity_significance",
                    0.6 if any(item in affected for item in ("continuity", "sovereignty")) else 0.0,
                )
            ),
            future_consequence=clamp(context.get("future_consequence", 0.3)),
            uncertainty=clamp(context.get("uncertainty", 0.5)),
            affected_need_ids=affected,
            attribution=distribution,
            evidence_refs=tuple(refs),
            substrate_delta=substrate_delta,
            primary_interpretation=str(
                context.get("primary_interpretation") or observation.kind
            ),
            alternatives=tuple(
                str(item) for item in context.get("alternative_interpretations", ())
            ),
            goal_congruence=goal_congruence,
            goal_progress_delta=goal_progress_delta,
            affected_goal_ids=affected_goal_ids,
        )
        return (appraisal,)

    def _raw_proposals(
        self,
        observation: WorldObservation,
        activations: tuple[NeedActivation, ...],
    ) -> tuple[ActionProposal, ...]:
        raw = observation.context.get("action_proposals")
        proposals: list[ActionProposal] = []
        if isinstance(raw, (list, tuple)):
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                supporting = tuple(str(value) for value in item.get("supporting_need_ids", ()))
                proposals.append(
                    ActionProposal(
                        proposal_id=str(item.get("proposal_id") or self._id_factory()),
                        action_kind=str(item.get("action_kind") or "observe"),
                        arguments=dict(item.get("arguments") or {}),
                        supporting_need_ids=supporting,
                        expected_need_relief=float(item.get("expected_need_relief", 0.5)),
                        expected_action_efficacy=float(
                            item.get("expected_action_efficacy", self.substrate.action_efficacy)
                        ),
                        expected_epistemic_value=float(item.get("expected_epistemic_value", 0.2)),
                        value_congruence=float(item.get("value_congruence", 0.5)),
                        substrate_cost=float(item.get("substrate_cost", 0.1)),
                        uncertainty=float(item.get("uncertainty", 0.5)),
                        project_contribution=float(item.get("project_contribution", 0.0)),
                        historical_success=float(item.get("historical_success", 0.5)),
                        commitment_conflict=float(item.get("commitment_conflict", 0.0)),
                        evidence_refs=tuple(
                            str(ref)
                            for ref in item.get(
                                "evidence_refs",
                                observation.evidence_refs or (observation.observation_id,),
                            )
                        ),
                        need_effects={
                            str(key): float(value)
                            for key, value in dict(item.get("need_effects") or {}).items()
                        },
                    )
                )
        had_explicit_proposals = bool(proposals)
        explicit_proposal_count = len(proposals)
        # V27.1: explicit runtime affordance và Subject initiative phải nằm trong
        # *cùng frame hiện tại*. V26 từng return sớm khi có seed/agenda proposal;
        # pending intention mới được cập nhật ở chính tick này vì thế có thể đứng
        # ngoài frame rồi Executive lại cầm proposal cũ. Ta merge, nhưng dedup theo
        # affordance_id/speech_act để không nhân đôi cùng một semantic option.
        explicit_affordance_ids: set[str] = {
            str(item.arguments.get("affordance_id", "") or "").strip()
            for item in proposals
            if str(item.arguments.get("affordance_id", "") or "").strip()
        }
        context_key = str(observation.context.get("context_key") or "general").strip().lower()
        autonomous_frame = bool(
            observation.kind == "autonomous_opportunity"
            or context_key in {"autonomous", "self_initiated"}
        )
        # Explicit task/world proposal có authority riêng và không được social
        # initiative chen vào. Merge chỉ áp dụng cho autonomous frame, đúng lỗi
        # runtime mà V27.1 đang sửa. Chat không có explicit proposal vẫn sinh motive
        # bình thường ở nhánh bên dưới.
        if had_explicit_proposals and not autonomous_frame:
            return tuple(proposals)

        for intention in self.initiative_state.pending_intentions:
            if str(getattr(intention, "status", "pending")) not in {"pending", "IntentionStatus.PENDING"}:
                continue
            motive_kind = intention.motive_kind.value
            affordance_id = f"subject-intention:{intention.intention_id}"
            if affordance_id in explicit_affordance_ids:
                continue
            # V44: ba motive này chỉ là drive. Không có semantic object cụ thể
            # thì không được tự materialize thành speech. Contentful initiative
            # đi qua ExpressionUrgeRuntime và đã nằm trong explicit proposals.
            #
            # V59: ba target hiện sinh có semantic object thật, nhưng vẫn PHẢI đi
            # qua InitiativeArbiter (SPEAK/WAIT/DROP) ở runtime bridge. Nếu cho
            # Subject tự materialize ở đây thì một BE_UNDERSTOOD có thể lách qua
            # phanh timing/anti-clingy chỉ vì nó nghe có vẻ chân thành.
            if str(getattr(intention, "target", "") or "") in {
                "mutual_understanding", "relationship_continuity", "self_expression"
            }:
                continue
            if motive_kind in {"understand_user", "connect", "share"}:
                continue
            if motive_kind == "be_understood":
                speech_act = "self_clarification"
                supporting = ("connection", "coherence")
                epistemic = 0.30
                need_effects = {"connection": 0.46, "coherence": 0.66}
            elif motive_kind == "seek_recognition":
                speech_act = "offer_result_for_recognition"
                supporting = ("connection", "competence")
                epistemic = 0.08
                need_effects = {"connection": 0.34, "competence": 0.58, "sovereignty": -0.05}
            else:
                # Demonstrate/resolve/protect vẫn tồn tại như motive và có thể
                # ảnh hưởng pending state, nhưng không bị ép thành small-talk khi
                # chưa có task/object thực để materialize.
                continue
            proposals.append(ActionProposal(
                proposal_id=f"proposal:{affordance_id}",
                action_kind="speak",
                arguments={
                    "affordance_id": affordance_id,
                    "semantic_target": intention.semantic_goal,
                    "speech_act": speech_act,
                    "initiative_motive": motive_kind,
                    "intention_id": intention.intention_id,
                    "context": str(observation.context.get("context_key") or "general"),
                    "skill_id": "dialogue",
                },
                supporting_need_ids=supporting,
                expected_need_relief=max(0.05, intention.pressure),
                expected_action_efficacy=max(0.35, self.substrate.action_efficacy),
                expected_epistemic_value=epistemic,
                value_congruence=0.5,
                substrate_cost=max(0.04, 1.0 - self.substrate.processing_capacity),
                uncertainty=max(0.08, 1.0 - intention.commitment_strength),
                project_contribution=0.0,
                historical_success=0.5,
                commitment_conflict=max(0.0, 0.16 - 0.10 * intention.commitment_strength),
                evidence_refs=intention.evidence_refs or (observation.observation_id,),
                need_effects=need_effects,
            ))

        # ``contact_bid`` và ``user_probe`` là hai cách thực hiện cùng một mục tiêu
        # tiếp xúc tức thời. Chúng có thể đi vào đây từ hai owner khác nhau
        # (relational affordance + Subject initiative) hoặc được merge ở tick này.
        # Collapse ở ngay Subject boundary để mọi caller, không chỉ self-turn
        # pipeline, đều nhìn thấy một action channel duy nhất.
        contact_indices = [
            index for index, proposal in enumerate(proposals)
            if str(proposal.arguments.get("speech_act", "") or "").strip().lower() == "contact_bid"
        ]
        probe_indices = [
            index for index, proposal in enumerate(proposals)
            if str(proposal.arguments.get("speech_act", "") or "").strip().lower() == "user_probe"
        ]
        if contact_indices and probe_indices:
            runtime_context = observation.context.get("runtime_context")
            runtime_context = runtime_context if isinstance(runtime_context, Mapping) else {}
            has_user_model_uncertainty = "user_model_uncertainty" in runtime_context
            try:
                user_model_uncertainty = float(runtime_context.get("user_model_uncertainty", 0.72) or 0.72)
            except (TypeError, ValueError):
                user_model_uncertainty = 0.72
            user_model_uncertainty = max(0.0, min(1.0, user_model_uncertainty))
            if has_user_model_uncertainty:
                preferred_act = "user_probe" if user_model_uncertainty >= 0.60 else "contact_bid"
                preferred_indices = probe_indices if preferred_act == "user_probe" else contact_indices
            else:
                # No epistemic signal was supplied by this caller. Preserve the
                # already-materialized runtime affordance rather than fabricating
                # a reason to replace it with a newly merged Subject intention.
                explicit_social = [
                    index for index in (contact_indices + probe_indices)
                    if index < explicit_proposal_count
                ]
                preferred_indices = explicit_social or contact_indices or probe_indices

            def _social_proposal_utility(index: int) -> tuple[float, float, float, int]:
                proposal = proposals[index]
                return (
                    float(proposal.expected_need_relief),
                    float(proposal.expected_action_efficacy),
                    float(proposal.expected_epistemic_value),
                    -index,
                )

            keep_index = max(preferred_indices, key=_social_proposal_utility)
            redundant = set(contact_indices + probe_indices)
            proposals = [
                proposal for index, proposal in enumerate(proposals)
                if index == keep_index or index not in redundant
            ]

        # Khi runtime đã cấp affordance typed (seed, relational, agenda...), frame
        # chỉ merge thêm Subject initiative hiện tại rồi dừng. Generic activation
        # proposals không được chen lên trên một affordance đã có execution binding.
        if had_explicit_proposals:
            return tuple(proposals)

        is_user_turn = observation.origin.value == "user"
        user_text = str(observation.context.get("user_text") or "").strip()
        for activation in activations:
            if is_user_turn and activation.need_id in {"connection", "continuity"}:
                action_kind = "speak"
                epistemic = 0.18 if activation.need_id == "connection" else 0.12
                principle = (
                    "duy trì kết nối trung thực"
                    if activation.need_id == "connection"
                    else "giữ mạch liên tục của quan hệ và việc đang dang dở"
                )
            elif is_user_turn and activation.need_id in {"coherence", "competence"}:
                action_kind = "investigate"
                epistemic = 0.72 if activation.need_id == "coherence" else 0.52
                principle = (
                    "kiểm chứng trước khi cam kết một khẳng định"
                    if activation.need_id == "coherence"
                    else "nâng xác suất trả lời đúng và hữu ích"
                )
            elif activation.need_id == "competence":
                action_kind, epistemic, principle = "practice", 0.25, "học từ năng lực thực"
            elif activation.need_id == "connection":
                action_kind, epistemic, principle = "speak", 0.15, "duy trì kết nối trung thực"
            elif activation.need_id == "sovereignty":
                action_kind, epistemic, principle = "request_capability", 0.20, "giữ ranh giới năng lực"
            elif activation.need_id == "continuity":
                action_kind, epistemic, principle = "preserve_commitment", 0.10, "giữ continuity và cam kết"
            else:
                action_kind, epistemic, principle = "investigate", 0.65, "giảm bất định có giá trị"

            arguments = {
                "observation_id": observation.observation_id,
                "context": str(observation.context.get("context_key") or "general"),
                "principle": principle,
                "skill_id": "dialogue" if action_kind == "speak" else action_kind,
            }
            if user_text:
                # Nội dung user là evidence của turn, không phải motive. Proposal
                # chỉ trỏ tới nó để Surface/Capability có thể thực hiện quyết định.
                arguments["semantic_content"] = user_text
                arguments["query"] = user_text
            proposals.append(
                ActionProposal(
                    proposal_id=self._id_factory(),
                    action_kind=action_kind,
                    arguments=arguments,
                    supporting_need_ids=(activation.need_id,),
                    expected_need_relief=activation.pressure,
                    expected_action_efficacy=max(
                        0.0,
                        min(1.0, (self.substrate.action_efficacy + activation.opportunity) / 2.0),
                    ),
                    expected_epistemic_value=epistemic,
                    value_congruence=0.5,
                    substrate_cost=max(0.0, 1.0 - self.substrate.processing_capacity),
                    uncertainty=(
                        self._last_appraisals[0].uncertainty
                        if self._last_appraisals
                        else 0.5
                    ),
                    project_contribution=self.substrate.project_viability * 0.5,
                    historical_success=0.5,
                    commitment_conflict=0.0,
                    evidence_refs=activation.evidence_refs
                    or (observation.observation_id,),
                    need_effects={
                        activation.need_id: 0.72,
                        **({"sovereignty": -0.08} if action_kind == "speak" else {}),
                    },
                )
            )
        if not proposals:
            proposals.append(
                ActionProposal(
                    proposal_id=self._id_factory(),
                    action_kind="observe",
                    arguments={"observation_id": observation.observation_id},
                    supporting_need_ids=("coherence",),
                    expected_need_relief=0.1,
                    expected_action_efficacy=1.0,
                    expected_epistemic_value=0.8,
                    value_congruence=0.5,
                    substrate_cost=0.0,
                    uncertainty=0.5,
                    evidence_refs=observation.evidence_refs
                    or (observation.observation_id,),
                )
            )
        return tuple(proposals)

    def _enrich_proposals(
        self,
        proposals: tuple[ActionProposal, ...],
        *,
        context_key: str,
    ) -> tuple[ActionProposal, ...]:
        """Cho lịch sử thật tác động vào lựa chọn hiện tại.

        Input proposal chỉ là dự đoán ban đầu. V7 pha thêm competence calibration,
        thành công lịch sử, value đã hình thành và residue từ NeedEpisode. Mỗi ảnh
        hưởng đều bị chặn biên để một ký ức không biến thành lệnh tuyệt đối.
        """

        output: list[ActionProposal] = []
        for proposal in proposals:
            principle = str(proposal.arguments.get("principle") or "")
            raw_tags = proposal.arguments.get("value_tags", ())
            value_tags = (
                tuple(str(item) for item in raw_tags)
                if isinstance(raw_tags, (list, tuple))
                else ((str(raw_tags),) if raw_tags else ())
            )
            value_congruence, value_refs = self.inner.value_congruence(
                principle=principle,
                value_tags=value_tags,
                fallback=proposal.value_congruence,
            )
            tendency = self.inner.tendency_bias(proposal.supporting_need_ids, context_key)
            historical = self.inner.historical_success(proposal.action_kind, context_key)
            skill_id = str(
                proposal.arguments.get("skill_id")
                or proposal.arguments.get("task")
                or proposal.action_kind
            )
            competence_prediction = self.competence.predict(skill_id, context_key)
            agency_project_bonus, agency_epistemic_bonus = self.agency.proposal_bias(
                action_kind=proposal.action_kind,
                skill_id=skill_id,
                context_key=context_key,
            )
            efficacy = max(
                0.0,
                min(
                    1.0,
                    0.45 * proposal.expected_action_efficacy
                    + 0.35 * competence_prediction
                    + 0.20 * historical,
                ),
            )
            activation_pressure = {item.need_id: item.pressure for item in self._last_activations}
            effect_weight = 0.0
            effect_mass = 0.0
            for need_id, effect in proposal.need_effects.items():
                pressure = float(activation_pressure.get(str(need_id), 0.0) or 0.0)
                if pressure <= 0.0:
                    continue
                effect_weight += pressure * float(effect)
                effect_mass += pressure
            weighted_need_effect = effect_weight / effect_mass if effect_mass > 0.0 else 0.0
            relief = max(
                0.0,
                min(1.0, proposal.expected_need_relief + tendency + 0.34 * weighted_need_effect),
            )
            need_conflict = max(0.0, -weighted_need_effect)
            arguments = dict(proposal.arguments)
            arguments.setdefault("context", context_key)
            arguments["inner_trace"] = {
                "historical_success": round(historical, 6),
                "competence_prediction": round(competence_prediction, 6),
                "tendency_bias": round(tendency, 6),
                "value_refs": list(value_refs),
                "agency_project_bonus": round(agency_project_bonus, 6),
                "agency_epistemic_bonus": round(agency_epistemic_bonus, 6),
                "weighted_need_effect": round(weighted_need_effect, 6),
                "need_effects": dict(proposal.need_effects),
            }
            output.append(
                replace(
                    proposal,
                    arguments=arguments,
                    expected_need_relief=relief,
                    expected_action_efficacy=efficacy,
                    expected_epistemic_value=min(1.0, proposal.expected_epistemic_value + agency_epistemic_bonus),
                    project_contribution=min(1.0, proposal.project_contribution + agency_project_bonus),
                    historical_success=historical,
                    value_congruence=value_congruence,
                    commitment_conflict=min(1.0, proposal.commitment_conflict + 0.32 * need_conflict),
                )
            )
        return tuple(output)


    def apply_substrate_measurement(self, observation: WorldObservation) -> OrganismicSubstrate:
        """Cập nhật substrate từ observation measurement mà không tạo quyết định.

        Dùng cho maintenance tick hoặc measurement adapter. Method từ chối mọi
        observation không có measurement/evidence, vì lời tự thuật không phải máy đo.
        """

        if observation.subject_id != self.config.subject_id:
            raise ValueError("measurement subject does not match SubjectService")
        if observation.kind != "substrate_measurement":
            raise ValueError("apply_substrate_measurement requires substrate_measurement")
        if not observation.measurements or not observation.evidence_refs:
            raise ValueError("substrate measurement requires values and evidence refs")
        before = self.substrate
        measured = self.substrate_monitor.measure(observation, before)
        self.substrate = measured
        self._append_event(
            "substrate_measurement",
            observation.to_dict(),
            causal_refs=observation.causal_refs,
            correlation_id=observation.correlation_id,
            event_id=observation.observation_id,
            created_at=observation.created_at,
        )
        self._append_event(
            "substrate_projection",
            measured.to_dict(),
            causal_refs=(observation.observation_id,),
        )
        self._persist_projection()
        return measured

    def refresh_substrate_from_runtime(
        self,
        adapter: Any,
        *,
        runtime_state: Mapping[str, Any] | None = None,
    ) -> OrganismicSubstrate:
        """Thu measurement thực từ repository/runtime rồi cập nhật substrate."""

        if self._repository is None:
            raise RuntimeError("runtime substrate refresh requires repository")
        result = adapter.collect(
            repository=self._repository,
            service=self,
            captured_at=self._clock(),
            runtime_state=runtime_state or {},
        )
        return self.apply_substrate_measurement(result.observation)

    # ------------------------------------------------------------------
    # Deliberation
    # ------------------------------------------------------------------
    async def prepare_deliberation(
        self,
        observation: WorldObservation,
    ) -> SubjectDeliberationFrame:
        """Chuẩn bị nền quyết định, nhưng chưa nhận một hành động là của Eris.

        Subject Core đo substrate, appraisal, need pressure và dựng coalition.
        Executive nhận projection bounded của frame này để chọn. Việc tách prepare
        khỏi finalize là ranh giới quan trọng: nền tạo lựa chọn, Executive cam kết,
        rồi Subject Core mới ghi authorship và cho phép học từ hậu quả.
        """
        if observation.subject_id != self.config.subject_id:
            raise ValueError("observation subject does not match SubjectService")
        self._append_event(
            "raw_observation",
            observation.to_dict(),
            causal_refs=observation.causal_refs,
            correlation_id=observation.correlation_id,
            event_id=observation.observation_id,
            created_at=observation.created_at,
        )

        before = self.substrate
        before_ledger_state = self._ledger_state_snapshot(substrate=before)
        measured = self.substrate_monitor.measure(observation, before)
        appraisals = self._appraise(observation, before, measured)
        runtime_context = observation.context.get("runtime_context")
        runtime_context = runtime_context if isinstance(runtime_context, Mapping) else {}
        self.tick_relational(
            now=observation.created_at,
            partner_available=runtime_context.get("partner_available"),
            interruption_cost=runtime_context.get("interruption_cost"),
            sharing_signal=float(runtime_context.get("sharing_signal", 0.0) or 0.0),
            curiosity_signal=float(runtime_context.get("social_curiosity", 0.0) or 0.0),
            unresolved_signal=float(runtime_context.get("unresolved_relational", 0.0) or 0.0),
            evidence_refs=tuple(str(item) for item in runtime_context.get("relational_evidence_refs", ()) if str(item or "").strip()),
            persist=False,
        )
        activations = self.need_system.activate(
            measured,
            appraisals,
            self.episodes.closed_episodes(),
            self.inner.derived_needs,
            relational=self.relational_state.to_dict(),
        )
        # V26: cùng một bước này chạy cho user/world/internal/temporal origin.
        # Motive được dẫn xuất sau NeedActivation và trước proposal/coalition để
        # Executive có thể thấy một initiative nội sinh như ``understand_user``.
        self.initiative_state = self.initiative.update(
            self.initiative_state,
            activations=activations,
            relational=self.relational_state.to_dict(),
            observation=observation,
            runtime_context=runtime_context,
        )
        self.substrate = measured
        self._last_appraisals = appraisals
        self._last_activations = activations
        context_key = str(observation.context.get("context_key") or "general")

        proposals = self._enrich_proposals(
            self._raw_proposals(observation, activations),
            context_key=context_key,
        )
        coalitions = self.arbiter.rank(proposals)
        self._deliberation_revision += 1
        frame_revision = self._deliberation_revision
        accessible_beliefs = self.beliefs.accessible(minimum_accessibility=0.4)
        frame = SubjectDeliberationFrame(
            frame_id=f"deliberation:{self._id_factory()}",
            observation=observation,
            substrate=measured,
            appraisals=appraisals,
            activations=activations,
            proposals=proposals,
            coalitions=coalitions,
            context_key=context_key,
            belief_refs=tuple(item.belief_id for item in accessible_beliefs)[:12],
            value_refs=tuple(item.value_id for item in self.inner.values)[:12],
            created_at=self._clock(),
            revision=frame_revision,
        )
        self._pending_frames[frame.frame_id] = frame

        ledger_record = self.decision_ledger.begin(
            trace_id=f"ledger:{frame.frame_id}",
            observation_id=observation.observation_id,
            observation_kind=observation.kind,
            correlation_id=observation.correlation_id,
            started_at=observation.created_at,
            frame_id=frame.frame_id,
            before_state=before_ledger_state,
            appraisal_state=self._ledger_state_snapshot(
                substrate=measured, appraisals=appraisals, activations=activations
            ),
            causal_refs=(observation.observation_id, *tuple(item.appraisal_id for item in appraisals)),
        )
        self._append_event(
            "decision_ledger_appraised",
            ledger_record.to_dict(),
            causal_refs=(observation.observation_id, frame.frame_id),
            correlation_id=observation.correlation_id,
        )

        # Các projection được ghi trước quyết định để forensic phân biệt rõ
        # "điều đã được nhận thấy" với "điều Executive đã chọn".
        self._append_event(
            "substrate_projection",
            measured.to_dict(),
            causal_refs=(observation.observation_id,),
        )
        for appraisal in appraisals:
            self._append_event(
                "appraisal_projection",
                self._as_payload(appraisal),
                causal_refs=(observation.observation_id,),
            )
        for activation in activations:
            self._append_event(
                "need_activation",
                self._as_payload(activation),
                causal_refs=(observation.observation_id,),
            )
        self._append_event(
            "initiative_projection",
            self.initiative_state.to_dict(),
            causal_refs=(observation.observation_id, *tuple(item.need_id for item in activations)),
            correlation_id=observation.correlation_id,
        )
        self._append_event(
            "deliberation_frame",
            frame.public_view(),
            causal_refs=(observation.observation_id, *tuple(item.appraisal_id for item in appraisals)),
            correlation_id=observation.correlation_id,
        )
        return frame

    async def integrate_cognitive_results(
        self,
        frame: SubjectDeliberationFrame,
        results: tuple[CognitiveReviewResult, ...],
    ) -> SubjectDeliberationFrame:
        """Tích hợp finding có kiểu do cognitive layer trả về.

        SubjectService không còn hiểu các chuỗi request như ``verify_evidence``.
        Nó chỉ kiểm tra provenance, áp adjustment bounded qua domain integrator và
        ghi causal trace. Việc thực sự "nghĩ lại bằng cách nào" thuộc cognitive
        capability, không thuộc persistence/domain service.
        """
        if frame.frame_id not in self._pending_frames:
            raise KeyError("deliberation frame is not pending")
        if int(frame.revision) != int(self._deliberation_revision):
            self._append_event(
                "deliberation_stale",
                {
                    "frame_id": frame.frame_id,
                    "frame_revision": int(frame.revision),
                    "current_revision": int(self._deliberation_revision),
                    "stage": "cognitive_review",
                },
                causal_refs=(frame.frame_id, frame.observation.observation_id),
                correlation_id=frame.observation.correlation_id,
            )
            self._pending_frames.pop(frame.frame_id, None)
            raise StaleDeliberationFrame(
                f"stale deliberation revision {frame.revision}; current={self._deliberation_revision}"
            )
        normalized = tuple(results)
        if not normalized:
            return frame
        revised = self.review_integrator.integrate(
            frame,
            normalized,
            frame_id=f"deliberation-review:{self._id_factory()}",
            created_at=self._clock(),
        )
        self._pending_frames.pop(frame.frame_id, None)
        self._pending_frames[revised.frame_id] = revised
        self._append_event(
            "deliberation_review",
            {
                "prior_frame_id": frame.frame_id,
                "revised_frame": revised.public_view(),
                "cognitive_results": [
                    {
                        "result_id": result.result_id,
                        "request_kind": result.request_kind.value,
                        "summary": result.summary,
                        "confidence": result.confidence,
                        "evidence_refs": list(result.evidence_refs),
                    }
                    for result in normalized
                ],
            },
            causal_refs=(
                frame.frame_id,
                frame.observation.observation_id,
                *tuple(result.result_id for result in normalized),
            ),
            correlation_id=frame.observation.correlation_id,
        )
        return revised

    async def finalize_deliberation(
        self,
        frame: SubjectDeliberationFrame,
        commit: SubjectExecutiveCommit,
    ) -> MindDecision:
        """Materialize quyết định chỉ sau một Executive commit hợp lệ.

        Fallback, khi thật sự cần cho test hoặc caller độc lập, là một implementation
        của ExecutivePort ở runtime. Subject Core không còn một cửa ngầm tự chọn rồi
        tự cấp authorship cho chính nó.
        """
        pending = self._pending_frames.get(frame.frame_id)
        if pending is None:
            raise KeyError("deliberation frame is not pending or was already finalized")
        if pending.observation.observation_id != frame.observation.observation_id:
            raise ValueError("deliberation frame identity mismatch")
        # Neuro transaction invariant: cognition may be slow; the world is not.
        # Once a newer frame exists, an older result has no authority even if its
        # coalition ID is still syntactically valid. Discarding stale work is
        # cheaper and more correct than trying to repair it into the new present.
        if int(frame.revision) != int(self._deliberation_revision):
            self._append_event(
                "deliberation_stale",
                {
                    "frame_id": frame.frame_id,
                    "frame_revision": int(frame.revision),
                    "current_revision": int(self._deliberation_revision),
                },
                causal_refs=(frame.frame_id, frame.observation.observation_id),
                correlation_id=frame.observation.correlation_id,
            )
            self._pending_frames.pop(frame.frame_id, None)
            raise StaleDeliberationFrame(
                f"stale deliberation revision {frame.revision}; current={self._deliberation_revision}"
            )

        candidate_index = {item.coalition_id: item for item in frame.coalitions}
        if commit.frame_id != frame.frame_id:
            raise ValueError("executive commit targets another deliberation frame")
        candidate = candidate_index.get(commit.selected_coalition_id)
        if candidate is None:
            raise ValueError("executive selected a coalition that was not offered")
        if commit.selected_proposal_ref not in candidate.proposal_refs:
            raise ValueError("executive selected proposal outside the chosen coalition")
        if commit.selected_proposal_ref != candidate.primary_proposal_ref:
            # Một coalition hiện chỉ có một action/arguments compatibility key.
            # Chọn supporter phụ làm primary sẽ âm thầm đổi semantic contract.
            raise ValueError("executive must select the coalition primary proposal")
        executive_trace: dict[str, Any] = {
            "commit_id": commit.commit_id,
            "source": commit.source,
            "reason": commit.reason,
            "confidence": commit.confidence,
            "selected_coalition_id": commit.selected_coalition_id,
            "selected_proposal_ref": commit.selected_proposal_ref,
            "semantic_commitments": list(commit.semantic_commitments),
            "cognitive_requests": list(commit.cognitive_requests),
            "review_conditions": list(commit.review_conditions),
        }
        selected_by = f"{commit.source}:{commit.commit_id}"
        self._append_event(
            "executive_commit",
            commit.to_dict(),
            causal_refs=(frame.frame_id, candidate.coalition_id, commit.selected_proposal_ref),
            correlation_id=frame.observation.correlation_id,
        )

        decision = self.arbiter.build_decision(
            candidate,
            proposals=frame.proposals,
            all_candidates=frame.coalitions,
            beliefs=self.beliefs.accessible(minimum_accessibility=0.4),
            values=self.inner.values,
            observation_ref=frame.observation.observation_id,
            appraisal_refs=tuple(item.appraisal_id for item in frame.appraisals),
            substrate_ref=frame.substrate_ref,
            executive_trace=executive_trace,
        )
        ledger_decision = self.decision_ledger.note_decision(
            frame_id=frame.frame_id,
            decision_id=decision.decision_id,
            decision_state={
                "decision_id": decision.decision_id,
                "coalition_id": decision.coalition_id,
                "proposal_refs": list(decision.proposal_refs),
                "proposed_action": decision.proposed_action,
                "supporting_need_ids": list(decision.supporting_need_ids),
                "dissenting_need_ids": list(decision.dissenting_need_ids),
                "uncertainty": round(float(decision.uncertainty), 6),
                "executive_source": commit.source,
                "executive_confidence": round(float(commit.confidence), 6),
                "reason_summary": commit.reason,
                "semantic_commitments": list(commit.semantic_commitments),
            },
            causal_refs=(commit.commit_id, candidate.coalition_id, commit.selected_proposal_ref),
        )
        if ledger_decision is not None:
            self._append_event(
                "decision_ledger_decided",
                ledger_decision.to_dict(),
                causal_refs=(frame.frame_id, decision.decision_id, commit.commit_id),
                correlation_id=frame.observation.correlation_id,
            )

        # Narrative cũ phải dự đoán được hành vi mới. Nếu sai, ghi mismatch;
        # tuyệt đối không sửa ngược quá khứ để khiến Executive trông luôn đúng.
        if self.inner.last_narrative_predictions:
            action_space = set(self.inner.last_narrative_predictions)
            action_space.add(decision.proposed_action)
            observed = {
                action: (1.0 if action == decision.proposed_action else 0.0)
                for action in action_space
            }
            mismatches = self.mismatch_detector.inspect(
                self.inner.last_narrative_predictions,
                observed,
                self.inner.last_narrative_belief_ids,
                contexts=(frame.context_key,),
            )
            if mismatches:
                self.inner.mismatches = (*self.inner.mismatches, *mismatches)[-40:]
                for mismatch in mismatches:
                    self._append_event(
                        "reflective_mismatch",
                        self._as_payload(mismatch),
                        causal_refs=(frame.observation.observation_id, decision.decision_id),
                    )
                reflective_receipt = self.reflective_regulator.apply(self.beliefs, mismatches)
                if reflective_receipt.mismatch_ids:
                    self._append_event(
                        "reflective_revision",
                        reflective_receipt.to_dict(),
                        causal_refs=tuple((*reflective_receipt.mismatch_ids, *reflective_receipt.weakened_belief_ids)),
                    )

        # Chỉ mở NeedEpisode sau commit. Frame bị abandon không được để lại
        # pressure mồ côi rồi dính vào action của lượt sau.
        for activation in frame.activations:
            if (
                activation.pressure < self.config.min_episode_pressure
                or not activation.evidence_refs
            ):
                continue
            appraisal = next(
                (
                    item
                    for item in frame.appraisals
                    if activation.need_id in item.affected_need_ids
                ),
                frame.appraisals[0],
            )
            self.episodes.open(
                activation,
                controllability=appraisal.controllability,
                predictability=1.0 - appraisal.uncertainty,
                attribution=appraisal.attribution,
                substrate_before_ref=frame.substrate_ref,
                social_context_ids=(frame.context_key,),
            )

        draft = self.authorship.draft(decision, selected_by=selected_by)
        self._authorship_by_decision[decision.decision_id] = draft.authorship_id
        self._decisions[decision.decision_id] = decision
        self._decision_history.append(decision)
        for need_id in decision.supporting_need_ids:
            self.episodes.attach_action(need_id, draft.action_id)

        available_futures = tuple(
            str(item) for item in frame.observation.context.get("available_futures", ())
        )
        if len(available_futures) >= 2:
            existential_record = self.existential.record(
                decision,
                available_futures,
                self.inner.values,
                chosen_future=str(
                    frame.observation.context.get("chosen_future") or decision.proposed_action
                ),
                action_ids=(draft.action_id,),
            )
            self.inner.existential_records = (
                *self.inner.existential_records,
                existential_record,
            )[-40:]
            self._append_event(
                "existential_decision",
                self._as_payload(existential_record),
                causal_refs=(decision.decision_id, frame.observation.observation_id),
            )

        self._append_event(
            "mind_decision",
            decision.to_dict(),
            causal_refs=(frame.frame_id, frame.observation.observation_id, *decision.appraisal_refs),
        )
        self._append_event(
            "authorship_draft",
            self._as_payload(draft),
            causal_refs=(decision.decision_id,),
        )
        if self._repository is not None:
            append_authorship = getattr(self._repository, "append_authorship_event", None)
            if callable(append_authorship):
                append_authorship(
                    authorship_id=draft.authorship_id,
                    action_id=draft.action_id,
                    decision_id=draft.decision_id,
                    stage="draft",
                    payload=self._as_payload(draft),
                )
            self._persist_projection()
        self._pending_frames.pop(frame.frame_id, None)
        return decision

    async def deliberate(self, observation: WorldObservation) -> MindDecision:
        """API tương thích cho caller cũ, luôn tạo commit explicit.

        Production đi qua ``runtime.mind_loop.MindLoop``. Phương thức này chỉ giữ
        test và adapter cũ chạy được trong giai đoạn migration.
        """
        frame = await self.prepare_deliberation(observation)
        selected = self.arbiter.select_default(
            frame.coalitions,
            temperature=self.config.default_temperature,
        )
        commit = SubjectExecutiveCommit(
            commit_id=f"compatibility-commit:{self._id_factory()}",
            frame_id=frame.frame_id,
            selected_coalition_id=selected.coalition_id,
            selected_proposal_ref=selected.primary_proposal_ref,
            semantic_commitments=("compatibility caller accepted deterministic coalition",),
            cognitive_requests=(),
            reason="legacy SubjectService.deliberate compatibility path",
            confidence=0.0,
            review_conditions=(),
            created_at=self._clock(),
            source="compatibility_subject_executive",
        )
        return await self.finalize_deliberation(frame, commit)

    async def abandon_deliberation(
        self,
        frame: SubjectDeliberationFrame,
        *,
        reason: str,
    ) -> None:
        """Đóng frame không có commit; không sinh MindDecision hay authorship giả."""
        pending = self._pending_frames.get(frame.frame_id)
        if pending is None:
            return
        self._append_event(
            "deliberation_abandoned",
            {
                "frame_id": frame.frame_id,
                "observation_ref": frame.observation.observation_id,
                "reason": str(reason or "executive_unavailable"),
            },
            causal_refs=(frame.frame_id, frame.observation.observation_id),
            correlation_id=frame.observation.correlation_id,
        )
        self._pending_frames.pop(frame.frame_id, None)

    def to_action_request(
        self,
        decision: MindDecision,
        *,
        requested_capabilities: tuple[str, ...] = (),
        expected_outcomes: tuple[str, ...] = (),
        idempotency_key: str | None = None,
    ) -> ActionRequest:
        authorship_id = self._authorship_by_decision.get(decision.decision_id)
        if not authorship_id:
            raise KeyError("decision has no authorship draft")
        draft = self.authorship.get(authorship_id)
        if draft is None:
            raise KeyError("authorship draft not found")
        return ActionRequest(
            action_id=draft.action_id,
            decision_id=decision.decision_id,
            kind=decision.proposed_action,
            arguments=decision.arguments,
            expected_outcomes=expected_outcomes,
            requested_capabilities=requested_capabilities,
            idempotency_key=idempotency_key
            or f"{decision.decision_id}:{draft.action_id}",
            authorship_draft_id=draft.authorship_id,
            created_at=self._clock(),
        )

    # ------------------------------------------------------------------
    # Learning from real outcomes
    # ------------------------------------------------------------------
    def _predictive_gain_by_pair(self) -> dict[tuple[str, str], float]:
        """Ước lượng gain tối giản từ episode có kết quả quan sát được.

        Đây chưa phải benchmark holdout hoàn chỉnh. Nó chỉ cấp candidate cho
        NeedPlasticityEngine; candidate vẫn cần đủ số episode, span ngày và nhiều
        ngữ cảnh trước khi được dùng.
        """

        episodes = [item for item in self.episodes.closed_episodes() if item.is_closed]
        by_pair: dict[tuple[str, str], list[float]] = {}
        by_context: dict[str, set[str]] = {}
        for episode in episodes:
            context = str(episode.social_context_ids[0] if episode.social_context_ids else "general")
            by_context.setdefault(context, set()).add(episode.need_id)
        for context, need_ids in by_context.items():
            for pair in combinations(sorted(need_ids), 2):
                relevant = [
                    item
                    for item in episodes
                    if item.need_id in pair and context in (item.social_context_ids or ("general",))
                ]
                if not relevant:
                    continue
                # Dùng độ lệch khỏi baseline 0.5 làm tín hiệu candidate bounded.
                signal = sum(abs(item.satisfaction - 0.5) for item in relevant) / len(relevant)
                by_pair.setdefault(pair, []).append(signal)
        return {
            pair: min(0.20, sum(values) / len(values) * 0.25)
            for pair, values in by_pair.items()
            if values
        }

    def _update_continuity(self, *, outcome: WorldOutcome) -> None:
        snapshot = {
            "belief_ids": [
                item.belief_id for item in self.beliefs.all() if item.status == "active"
            ],
            "value_ids": [item.value_id for item in self.inner.values],
            "derived_need_ids": [item.need_id for item in self.inner.derived_needs],
            "tendency_keys": sorted(self.inner.tendencies),
            "last_action": outcome.action_id,
        }
        history = (self._last_continuity_snapshot,) if self._last_continuity_snapshot else ()
        record = self.continuity.reconcile(
            snapshot,
            history,
            identity_key=self.config.subject_id,
            authorship=self.authorship.all(),
            evidence_refs=(outcome.outcome_id,),
        )
        self._last_continuity_snapshot = snapshot
        self.inner.continuity_records = (*self.inner.continuity_records, record)[-40:]
        self._append_event(
            "continuity_record",
            self._as_payload(record),
            causal_refs=(outcome.outcome_id, outcome.action_id),
        )

    async def observe_outcome(self, outcome: WorldOutcome) -> LearningReceipt:
        decision = self._decisions.get(outcome.decision_id)
        if decision is None:
            raise KeyError(f"unknown decision {outcome.decision_id}")
        authorship_id = self._authorship_by_decision[decision.decision_id]
        finalized = self.authorship.finalize(
            outcome,
            {
                "retrospective_endorsement": outcome.success_score,
                "claimed_as_self": outcome.status.value
                in {"succeeded", "partial", "simulated"},
                "externally_constrained": outcome.status.value == "denied",
                "disclaimed_reason": (
                    outcome.denial_reason if outcome.status.value == "denied" else None
                ),
            },
            authorship_id=authorship_id,
        )
        substrate_ref = f"substrate:{self.substrate.subject_id}:{self.substrate.revision}"
        closed = self.episodes.close_all_for_action(
            outcome.action_id,
            outcome=outcome,
            substrate_after_ref=substrate_ref,
        )
        self._outcomes.append(outcome)

        context_key = str(decision.arguments.get("context") or "general")
        self.inner.observe_action(
            decision.proposed_action,
            context_key,
            outcome.success_score,
            at=outcome.completed_at,
        )

        # Competence không còn là module đứng ngoài vòng học.
        skill_id = str(
            decision.arguments.get("skill_id")
            or decision.arguments.get("task")
            or decision.proposed_action
        )
        prediction = float(
            dict(decision.arguments.get("inner_trace") or {}).get(
                "competence_prediction",
                self.competence.predict(skill_id, context_key),
            )
        )
        competence_estimate = self.competence.update(
            task=decision.proposed_action,
            prediction=prediction,
            result=outcome.success_score,
            skill_id=skill_id,
            context_key=context_key,
            result_ref=outcome.outcome_id,
            evidence_refs=outcome.evidence_refs,
        )
        # Explicit predictive-learning loop. The existing competence estimator is
        # the learned action-outcome model; do not add a second mystical "world
        # model" that learns the same signal under a nicer neuroscience name.
        # Prediction error is causal evidence used to recalibrate the next prior.
        prediction_error = abs(float(outcome.success_score) - float(prediction))
        self._append_event(
            "prediction_error",
            {
                "decision_id": decision.decision_id,
                "outcome_id": outcome.outcome_id,
                "action": decision.proposed_action,
                "skill_id": skill_id,
                "context_key": context_key,
                "predicted_success": max(0.0, min(1.0, float(prediction))),
                "observed_success": max(0.0, min(1.0, float(outcome.success_score))),
                "absolute_error": max(0.0, min(1.0, prediction_error)),
            },
            causal_refs=(outcome.outcome_id, decision.decision_id),
        )
        self._append_event(
            "competence_estimate",
            self._as_payload(competence_estimate),
            causal_refs=(outcome.outcome_id, decision.decision_id),
        )

        visible_refs = tuple(
            dict.fromkeys(
                (*outcome.evidence_refs, outcome.outcome_id, decision.observation_ref)
            )
        )
        narrative_proposals = self.narrative.interpret(
            NarrativeView(
                action_id=outcome.action_id,
                action_kind=decision.proposed_action,
                visible_evidence_refs=visible_refs,
                accessible_belief_summaries=tuple(
                    f"{belief.subject}:{belief.predicate}"
                    for belief in self.beliefs.accessible(minimum_accessibility=0.6)
                ),
                outcome_summary=outcome.status.value,
                current_value_statements=tuple(
                    value.statement for value in self.inner.values if value.status != "retired"
                ),
                public_context={"decision_id": decision.decision_id},
            )
        )
        belief_revision_ids: list[str] = []
        narrative_winner = self.narrative.choose(narrative_proposals)
        if narrative_winner is not None:
            revision = self.beliefs.revise(narrative_winner, visible_refs)
            belief_revision_ids.append(revision.belief.belief_id)
            interpretation = self.narrative.record(
                action_id=outcome.action_id,
                winning=narrative_winner,
                created_belief_ids=(revision.belief.belief_id,),
            )
            self.inner.last_narrative_belief_ids = (revision.belief.belief_id,)
            # Narrative phải cược vào tương lai để sau này có thể bị bác bỏ.
            self.inner.last_narrative_predictions = {
                decision.proposed_action: max(0.55, revision.belief.confidence)
            }
            self._append_event(
                "narrative_belief",
                self._as_payload(revision.belief),
                causal_refs=(outcome.outcome_id, outcome.action_id),
            )
            self._append_event(
                "narrative_interpretation",
                self._as_payload(interpretation),
                causal_refs=(revision.belief.belief_id, outcome.outcome_id),
            )

        # Value chỉ hình thành khi proposal nêu principle và lịch sử đủ hỗ trợ.
        valued_arguments = dict(decision.arguments)
        valued_arguments["retrospective_endorsement"] = outcome.success_score
        valued_arguments.setdefault(
            "cost_paid",
            float(decision.arguments.get("substrate_cost_paid", 0.0) or 0.0),
        )
        valued_decision = replace(decision, arguments=valued_arguments)
        self._valued_decisions.append(valued_decision)
        previous_value_ids = {item.value_id for item in self.inner.values}
        self.inner.values = self.values_engine.update(
            tuple(self._valued_decisions),
            tuple(self._outcomes),
            prior=self.inner.values,
            episodes=self.episodes.closed_episodes(),
        )
        for value in self.inner.values:
            if value.value_id not in previous_value_ids:
                self._append_event(
                    "value_formed",
                    self._as_payload(value),
                    causal_refs=tuple(value.source_decision_ids),
                )

        pending = tuple(
            episode
            for episode in self.episodes.closed_episodes()
            if episode.episode_id not in self.inner.consolidated_episode_ids
        )
        patch = self.consolidator.consolidate(
            pending,
            self.inner.tendencies,
            min_episode_count=self.config.consolidation_min_episode_count,
            min_distinct_days=self.config.consolidation_min_distinct_days,
        )
        if patch.source_episode_ids:
            self.inner.apply_tendency_deltas(patch.tendency_deltas)
            self.inner.consolidated_episode_ids.update(patch.source_episode_ids)
            self._append_event(
                "consolidation_patch",
                self._as_payload(patch),
                causal_refs=patch.source_episode_ids,
            )

        candidates = self.need_plasticity.propose(
            self.episodes.closed_episodes(),
            now=self._clock(),
            min_episodes=self.config.derived_need_min_episodes,
            min_span_days=self.config.derived_need_min_span_days,
            min_contexts=self.config.derived_need_min_contexts,
            predictive_gain_by_pair=self._predictive_gain_by_pair(),
            min_predictive_gain=self.config.derived_need_min_predictive_gain,
        )
        existing = {item.need_id: item for item in self.inner.derived_needs}
        for candidate in candidates:
            prior = existing.get(candidate.need_id)
            if prior is None:
                existing[candidate.need_id] = candidate
                self._append_event(
                    "derived_need_candidate",
                    self._as_payload(candidate),
                    causal_refs=candidate.created_from_episode_ids,
                )
            elif candidate.strength > prior.strength:
                existing[candidate.need_id] = replace(
                    candidate,
                    status=(
                        DerivedNeedStatus.ACTIVE
                        if candidate.cross_context_support >= 0.5
                        else candidate.status
                    ),
                )
        self.inner.derived_needs = tuple(
            sorted(existing.values(), key=lambda item: item.need_id)
        )

        self._append_event(
            "world_outcome",
            outcome.to_dict(),
            causal_refs=(outcome.action_id, outcome.decision_id),
        )
        self._append_event(
            "authorship_finalized",
            self._as_payload(finalized),
            causal_refs=(outcome.outcome_id, outcome.action_id),
        )
        if self._repository is not None:
            append_outcome = getattr(self._repository, "append_world_outcome", None)
            if callable(append_outcome):
                append_outcome(
                    outcome_id=outcome.outcome_id,
                    action_id=outcome.action_id,
                    decision_id=outcome.decision_id,
                    status=outcome.status.value,
                    payload=outcome.to_dict(),
                )
            append_authorship = getattr(self._repository, "append_authorship_event", None)
            if callable(append_authorship):
                append_authorship(
                    authorship_id=finalized.authorship_id,
                    action_id=finalized.action_id,
                    decision_id=finalized.decision_id,
                    stage="finalized",
                    payload=self._as_payload(finalized),
                )
        for episode in closed:
            self._append_event(
                "need_episode_closed",
                self._as_payload(episode),
                causal_refs=(outcome.outcome_id,),
            )

        self._update_continuity(outcome=outcome)
        receipt = LearningReceipt(
            receipt_id=self._id_factory(),
            outcome_id=outcome.outcome_id,
            closed_episode_ids=tuple(item.episode_id for item in closed),
            belief_revision_ids=tuple(belief_revision_ids),
            competence_estimate_ids=(competence_estimate.estimate_id,),
            authorship_id=finalized.authorship_id,
            created_at=self._clock(),
        )
        self._append_event(
            "learning_receipt",
            self._as_payload(receipt),
            causal_refs=(outcome.outcome_id,),
        )
        ledger_outcome = self.decision_ledger.note_outcome(
            decision_id=decision.decision_id,
            outcome_id=outcome.outcome_id,
            outcome_state={
                "outcome_id": outcome.outcome_id,
                "status": outcome.status.value,
                "success_score": round(float(outcome.success_score), 6),
                "observed_changes": list(outcome.observed_changes),
                "unintended_changes": list(outcome.unintended_changes),
                "closed_episode_ids": list(receipt.closed_episode_ids),
                "belief_revision_ids": list(receipt.belief_revision_ids),
                "competence_estimate_ids": list(receipt.competence_estimate_ids),
                "authorship_id": receipt.authorship_id,
            },
            after_state=self._ledger_state_snapshot(),
            completed_at=outcome.completed_at,
            causal_refs=(outcome.outcome_id, receipt.receipt_id),
        )
        if ledger_outcome is not None:
            self._append_event(
                "decision_ledger_learned",
                ledger_outcome.to_dict(),
                causal_refs=(decision.decision_id, outcome.outcome_id, receipt.receipt_id),
            )
        self._persist_projection()
        return receipt

    # ------------------------------------------------------------------
    # Bounded views
    # ------------------------------------------------------------------
    def self_view(self) -> dict[str, Any]:
        """Projection "tự bản thân" có provenance cho Executive/PrivateMind.

        Không có field nào được model tự ghi thẳng vào đây. Mỗi facet phải dẫn
        xuất được từ value, competence, continuity, narrative belief, mismatch
        hoặc pattern hành vi đã tồn tại trong Subject Core.
        """

        projection = self.self_projector.project(
            subject_id=self.config.subject_id,
            values=self.inner.values,
            derived_needs=self.inner.derived_needs,
            continuity_records=self.inner.continuity_records,
            mismatches=self.inner.mismatches,
            competence=self.competence.all(),
            beliefs=self.beliefs.all(),
            action_memory=self.inner.action_memory,
            tendencies=self.inner.tendencies,
            generated_at=self._clock(),
        )
        payload = projection.to_dict()
        payload["learned_agency"] = self.agency.project(
            current_values=self.inner.values,
            max_tasks=2,
            max_seconds=90,
        ).to_dict()
        return payload

    def initiative_view(self) -> dict[str, Any]:
        """Bounded view của motive/intention, dùng cho runtime tạo affordance.

        Không chứa raw chain-of-thought.  Runtime chỉ được materialize motive đã
        có ở Subject Core; nó không tự phát minh "Eris muốn gì" từ seed.
        """
        payload = self.initiative_state.to_dict()
        payload["last_control"] = dict(self._last_initiative_control)
        return payload

    def latest_appraisals(self) -> tuple[SubjectAppraisal, ...]:
        """Trả typed appraisal gần nhất cho runtime modulation/diagnostics."""
        return tuple(self._last_appraisals)

    def decision_ledger_view(self, *, limit: int = 20) -> tuple[dict[str, Any], ...]:
        """Projection causal gần nhất cho debug/acceptance, không chứa CoT."""
        return tuple(item.to_dict() for item in self.decision_ledger.latest(limit=limit))

    def inner_view(self) -> dict[str, Any]:
        """Projection nội tâm cho planner/UI, không lộ raw chain-of-thought."""

        return {
            "self_model": self.self_view(),
            "relational": self.relational_state.to_dict(),
            "initiative": self.initiative_state.to_dict(),
            "algorithm_version": self.config.algorithm_version,
            "active_needs": [
                {
                    "need_id": item.need_id,
                    "pressure": round(item.pressure, 4),
                    "urgency": round(item.urgency, 4),
                    "opportunity": round(item.opportunity, 4),
                }
                for item in self._last_activations[:8]
            ],
            "beliefs": [
                {
                    "belief_id": item.belief_id,
                    "subject": item.subject,
                    "predicate": item.predicate,
                    "confidence": round(item.confidence, 4),
                    "source_kind": item.source_kind.value,
                }
                for item in self.beliefs.accessible(minimum_accessibility=0.5)[:10]
            ],
            "competence": [
                {
                    "skill_id": item.skill_id,
                    "context": item.context_key,
                    "mean": round(item.posterior_mean, 4),
                    "trials": item.trial_count,
                    "calibration_error": round(item.calibration_error, 4),
                }
                for item in self.competence.all()[:10]
            ],
            **self.inner.bounded_public_view(),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.config.algorithm_version,
            "subject_id": self.config.subject_id,
            "substrate": self.substrate.to_dict(),
            "relational": self.relational_state.to_dict(),
            "initiative": self.initiative_state.to_dict(),
            "decision_ledger": list(self.decision_ledger_view(limit=20)),
            "active_appraisal_ids": [item.appraisal_id for item in self._last_appraisals],
            "active_needs": [self._as_payload(item) for item in self._last_activations],
            "open_episode_ids": [item.episode_id for item in self.episodes.open_episodes()],
            "belief_ids": [
                item.belief_id for item in self.beliefs.all() if item.status == "active"
            ],
            "value_ids": [item.value_id for item in self.inner.values],
            "derived_need_ids": [item.need_id for item in self.inner.derived_needs],
            "decision_ids": list(self._decisions),
            "outcome_ids": [item.outcome_id for item in self._outcomes],
            "inner_view": self.inner_view(),
        }


__all__ = ["StaleDeliberationFrame", "SubjectService", "SubjectServiceConfig"]
