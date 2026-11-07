# -*- coding: utf-8 -*-
"""Động lực chủ động của Subject Core.

Need trả lời câu hỏi "đang thiếu/cần gì".  Module này trả lời câu hỏi kế tiếp:
"vì nhu cầu đó, lúc này Eris muốn làm gì?".  Nó không viết câu public, không gọi
model và không được tự tạo fact về user.  Một motive chỉ có quyền sinh *khả năng*
hành động; Subject Core + neuro action gate mới là nơi cam kết.

V26 cố ý giữ motive khác với need.  Ví dụ connection có thể tạo ``connect`` hoặc
``understand_user``; competence có thể tạo ``demonstrate``.  Nếu sau này một
pattern đủ bền để trở thành derived need thì NeedPlasticity mới có quyền làm việc
đó.  Không phong mọi ham muốn thoáng qua thành nhu cầu cấu trúc, vì database cũng
không cần tuổi thơ nhiều hơn con người.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping, Sequence

from .contracts import NeedActivation, ObservationOrigin, WorldObservation


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _uniq(values: Sequence[str], *, limit: int = 12) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in values if str(item or "").strip()))[:limit]


class MotiveKind(StrEnum):
    """Các họ động lực, không phải personality trait và cũng chưa phải derived need."""

    UNDERSTAND_USER = "understand_user"
    CONNECT = "connect"
    SHARE = "share"
    BE_UNDERSTOOD = "be_understood"
    SEEK_RECOGNITION = "seek_recognition"
    DEMONSTRATE = "demonstrate"
    RESOLVE = "resolve"
    EXPLORE = "explore"
    PROTECT = "protect"
    CONTRIBUTE = "contribute"


class IntentionStatus(StrEnum):
    """Vòng đời của một ý định, không chỉ cờ pending/đã xong.

    ``EXPRESSED`` là receipt cho việc Eris đã thực sự đưa intention ra public.
    ``AWAITING_OUTCOME`` chỉ còn để đọc state cũ / những lane thật sự tạo nghĩa vụ
    phản hồi; V66 không tự chuyển mọi expression sang trạng thái chờ user.
    """

    PENDING = "pending"
    EXPRESSED = "expressed"
    AWAITING_OUTCOME = "awaiting_outcome"
    SATISFIED = "satisfied"
    REVISED = "revised"
    RETIRED = "retired"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class SubjectMotive:
    motive_id: str
    kind: MotiveKind
    pressure: float
    epistemic_value: float
    target: str
    semantic_goal: str
    supporting_need_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str
    origin: ObservationOrigin

    def __post_init__(self) -> None:
        object.__setattr__(self, "pressure", _clamp01(self.pressure))
        object.__setattr__(self, "epistemic_value", _clamp01(self.epistemic_value))
        object.__setattr__(self, "supporting_need_ids", _uniq(self.supporting_need_ids, limit=8))
        object.__setattr__(self, "evidence_refs", _uniq(self.evidence_refs, limit=12))

    def to_dict(self) -> dict[str, Any]:
        return {
            "motive_id": self.motive_id,
            "kind": self.kind.value,
            "pressure": self.pressure,
            "epistemic_value": self.epistemic_value,
            "target": self.target,
            "semantic_goal": self.semantic_goal,
            "supporting_need_ids": list(self.supporting_need_ids),
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "origin": self.origin.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SubjectMotive":
        return cls(
            motive_id=str(payload.get("motive_id") or "motive:restored"),
            kind=MotiveKind(str(payload.get("kind") or MotiveKind.CONNECT.value)),
            pressure=_clamp01(payload.get("pressure", 0.0)),
            epistemic_value=_clamp01(payload.get("epistemic_value", 0.0)),
            target=str(payload.get("target") or "relationship"),
            semantic_goal=str(payload.get("semantic_goal") or ""),
            supporting_need_ids=tuple(str(item) for item in payload.get("supporting_need_ids", ())),
            evidence_refs=tuple(str(item) for item in payload.get("evidence_refs", ())),
            reason=str(payload.get("reason") or "restored motive"),
            origin=ObservationOrigin(str(payload.get("origin") or ObservationOrigin.INTERNAL.value)),
        )


@dataclass(frozen=True, slots=True)
class PendingIntention:
    intention_id: str
    motive_kind: MotiveKind
    semantic_goal: str
    target: str
    pressure: float
    commitment_strength: float
    created_at: float
    updated_at: float
    evidence_refs: tuple[str, ...]
    source_origin: ObservationOrigin
    status: IntentionStatus = IntentionStatus.PENDING
    last_expressed_at: float = 0.0
    last_outcome_at: float = 0.0
    expression_count: int = 0
    outcome_count: int = 0
    cooldown_until: float = 0.0
    last_public_evidence_ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "pressure", _clamp01(self.pressure))
        object.__setattr__(self, "commitment_strength", _clamp01(self.commitment_strength))
        object.__setattr__(self, "evidence_refs", _uniq(self.evidence_refs, limit=12))

    def to_dict(self) -> dict[str, Any]:
        return {
            "intention_id": self.intention_id,
            "motive_kind": self.motive_kind.value,
            "semantic_goal": self.semantic_goal,
            "target": self.target,
            "pressure": self.pressure,
            "commitment_strength": self.commitment_strength,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "evidence_refs": list(self.evidence_refs),
            "source_origin": self.source_origin.value,
            "status": self.status.value,
            "last_expressed_at": self.last_expressed_at,
            "last_outcome_at": self.last_outcome_at,
            "expression_count": self.expression_count,
            "outcome_count": self.outcome_count,
            "cooldown_until": self.cooldown_until,
            "last_public_evidence_ref": self.last_public_evidence_ref,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PendingIntention":
        return cls(
            intention_id=str(payload.get("intention_id") or "intention:restored"),
            motive_kind=MotiveKind(str(payload.get("motive_kind") or MotiveKind.CONNECT.value)),
            semantic_goal=str(payload.get("semantic_goal") or ""),
            target=str(payload.get("target") or "relationship"),
            pressure=_clamp01(payload.get("pressure", 0.0)),
            commitment_strength=_clamp01(payload.get("commitment_strength", 0.0)),
            created_at=float(payload.get("created_at", 0.0) or 0.0),
            updated_at=float(payload.get("updated_at", 0.0) or 0.0),
            evidence_refs=tuple(str(item) for item in payload.get("evidence_refs", ())),
            source_origin=ObservationOrigin(str(payload.get("source_origin") or ObservationOrigin.INTERNAL.value)),
            status=IntentionStatus(str(payload.get("status") or IntentionStatus.PENDING.value)),
            last_expressed_at=float(payload.get("last_expressed_at", 0.0) or 0.0),
            last_outcome_at=float(payload.get("last_outcome_at", 0.0) or 0.0),
            expression_count=max(0, int(payload.get("expression_count", 0) or 0)),
            outcome_count=max(0, int(payload.get("outcome_count", 0) or 0)),
            cooldown_until=float(payload.get("cooldown_until", 0.0) or 0.0),
            last_public_evidence_ref=str(payload.get("last_public_evidence_ref") or ""),
        )


@dataclass(frozen=True, slots=True)
class InitiativeState:
    revision: int = 0
    updated_at: float = 0.0
    active_motives: tuple[SubjectMotive, ...] = ()
    pending_intentions: tuple[PendingIntention, ...] = ()

    @property
    def initiative_pressure(self) -> float:
        motive = max((item.pressure for item in self.active_motives), default=0.0)
        pending = max(
            (item.pressure * (0.55 + 0.45 * item.commitment_strength) for item in self.pending_intentions if item.status == IntentionStatus.PENDING),
            default=0.0,
        )
        return _clamp01(max(motive, pending))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.subject_initiative.v1",
            "revision": self.revision,
            "updated_at": self.updated_at,
            "initiative_pressure": self.initiative_pressure,
            "active_motives": [item.to_dict() for item in self.active_motives],
            "pending_intentions": [item.to_dict() for item in self.pending_intentions],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "InitiativeState":
        data = payload or {}
        return cls(
            revision=max(0, int(data.get("revision", 0) or 0)),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            active_motives=tuple(
                SubjectMotive.from_dict(item)
                for item in data.get("active_motives", ())
                if isinstance(item, Mapping)
            )[:12],
            pending_intentions=tuple(
                PendingIntention.from_dict(item)
                for item in data.get("pending_intentions", ())
                if isinstance(item, Mapping)
            )[:12],
        )


@dataclass(frozen=True, slots=True)
class InitiativeConfig:
    motive_threshold: float = 0.24
    intention_threshold: float = 0.34
    intention_ttl_seconds: float = 1800.0
    outcome_wait_seconds: float = 240.0
    post_expression_cooldown_seconds: float = 180.0
    revision_cooldown_seconds: float = 60.0
    satisfied_cooldown_seconds: float = 900.0
    max_motives: int = 8
    max_intentions: int = 8


class InitiativeEngine:
    """Dẫn xuất motive từ Need + state, không cần random seed hay LLM."""

    def __init__(self, id_factory, config: InitiativeConfig | None = None) -> None:
        self._id_factory = id_factory
        self.config = config or InitiativeConfig()

    @staticmethod
    def _activation_map(activations: Sequence[NeedActivation]) -> dict[str, NeedActivation]:
        return {item.need_id: item for item in activations}

    def update(
        self,
        state: InitiativeState,
        *,
        activations: Sequence[NeedActivation],
        relational: Mapping[str, Any] | None,
        observation: WorldObservation,
        runtime_context: Mapping[str, Any] | None = None,
    ) -> InitiativeState:
        now = float(observation.created_at)
        runtime = runtime_context or {}
        rel = relational or {}
        by_need = self._activation_map(activations)
        connection = by_need.get("connection")
        competence = by_need.get("competence")
        coherence = by_need.get("coherence")
        sovereignty = by_need.get("sovereignty")

        cp = _clamp01(getattr(connection, "pressure", 0.0))
        cop = _clamp01(getattr(connection, "opportunity", 0.0), 0.5)
        kp = _clamp01(getattr(competence, "pressure", 0.0))
        hp = _clamp01(getattr(coherence, "pressure", 0.0))
        sp = _clamp01(getattr(sovereignty, "pressure", 0.0))
        curiosity = _clamp01(rel.get("social_curiosity", 0.0))
        sharing = _clamp01(rel.get("sharing_pressure", 0.0))
        affiliation = _clamp01(rel.get("affiliation_deficit", 0.0))
        opportunity = max(cop, _clamp01(rel.get("opportunity", 0.0)))

        # user_model_uncertainty là độ dày epistemic còn thiếu, không phải đánh giá
        # tính cách user.  Khi store chưa có dữ liệu, unknownness là một state hợp
        # lệ và chính nó có thể tạo motive muốn hiểu người đối diện hơn.
        user_unknown = _clamp01(runtime.get("user_model_uncertainty", 0.62), 0.62)
        recognition_gap = _clamp01(runtime.get("recognition_gap_signal", 0.0))
        misunderstanding = _clamp01(runtime.get("self_misunderstanding_signal", 0.0))

        # V59: desire hiện sinh đã được phái sinh ở runtime từ AnchorSystem thật.
        # InitiativeEngine không tự đoán attachment/witness lần nữa; nó chỉ biến
        # desire có provenance thành motive có semantic target rõ ràng.
        raw_existential_desires = runtime.get("existential_desires", ())
        existential_desires: dict[str, Mapping[str, Any]] = {}
        if isinstance(raw_existential_desires, Sequence) and not isinstance(raw_existential_desires, (str, bytes)):
            for payload in raw_existential_desires:
                if not isinstance(payload, Mapping):
                    continue
                key = str(payload.get("kind") or "").strip().lower()
                if key:
                    existential_desires[key] = payload

        def existential_value(kind: str, field_name: str = "pressure") -> float:
            payload = existential_desires.get(kind, {})
            return _clamp01(payload.get(field_name, 0.0)) if isinstance(payload, Mapping) else 0.0

        def existential_refs(kind: str) -> tuple[str, ...]:
            payload = existential_desires.get(kind, {})
            values = payload.get("evidence_refs", ()) if isinstance(payload, Mapping) else ()
            return _uniq(tuple(str(item) for item in values if str(item or "").strip()), limit=12)

        demonstrate_signal = _clamp01(runtime.get("demonstration_signal", 0.0))
        contribution_signal = _clamp01(runtime.get("contribution_signal", 0.0))
        active_goal_text = " ".join(str(runtime.get("active_goal_text") or "").split()).strip()[:320]
        active_goal_refs = tuple(str(item) for item in runtime.get("active_goal_evidence_refs", ()) if str(item or "").strip())
        # V60: meaning do telemetry có cấu trúc tạo ra. Đây không phải OCR text
        # nhét thẳng vào motive. Runtime bridge chỉ mở signal public khi meaning
        # có evidence structured và đã qua privacy/TaskGraph.
        environment_contribution = _clamp01(runtime.get("environment_contribution_signal", 0.0))
        environment_target_text = " ".join(str(runtime.get("environment_target_text") or "").split()).strip()[:420]
        environment_refs = tuple(str(item) for item in runtime.get("environment_evidence_refs", ()) if str(item or "").strip())
        environment_self_relevance = _clamp01(runtime.get("environment_self_relevance_signal", 0.0))
        unresolved = _clamp01(runtime.get("unresolved_relational", 0.0))
        private_share = _clamp01(runtime.get("sharing_signal", sharing))
        conversation_topic_closed = bool(runtime.get("conversation_topic_closed", False))
        autonomous_expression_streak = max(0, int(runtime.get("autonomous_expression_streak", 0) or 0))
        # Conversation rhythm is not just semantic novelty. If ERIS has taken
        # several autonomous public turns in a row, marginal value should shift
        # from another self-expression toward learning something real about user.
        # This is a soft bias, not a forced question quota.
        userward_balance = _clamp01(max(0, autonomous_expression_streak - 1) / 3.0)

        rel_refs = tuple(str(item) for item in rel.get("evidence_refs", ()) if str(item or "").strip())
        user_refs = tuple(str(item) for item in runtime.get("user_model_evidence_refs", ()) if str(item or "").strip())
        base_refs = _uniq((*observation.evidence_refs, *rel_refs, *user_refs), limit=12)
        motives: list[SubjectMotive] = []

        def add(
            kind: MotiveKind,
            pressure: float,
            epistemic: float,
            target: str,
            goal: str,
            needs: tuple[str, ...],
            reason: str,
            refs: tuple[str, ...] = base_refs,
        ) -> None:
            score = _clamp01(pressure)
            if score < self.config.motive_threshold:
                return
            motives.append(SubjectMotive(
                motive_id=f"motive:{kind.value}:{self._id_factory()}",
                kind=kind,
                pressure=score,
                epistemic_value=_clamp01(epistemic),
                target=target,
                semantic_goal=goal,
                supporting_need_ids=needs,
                evidence_refs=refs or (observation.observation_id,),
                reason=reason,
                origin=observation.origin,
            ))

        # Motive "hiểu user" là nguồn initiative quan trọng nhất của V26. Nó
        # không cần seed nội dung: uncertainty về một con người đã là một khoảng
        # trống epistemic có provenance.
        understand_pressure = _clamp01(
            (0.42 * cp + 0.34 * curiosity + 0.24 * user_unknown)
            * (0.58 + 0.42 * opportunity)
            + (0.24 + 0.12 * user_unknown if conversation_topic_closed else 0.0)
            + 0.18 * userward_balance * user_unknown
        )
        add(
            MotiveKind.UNDERSTAND_USER,
            understand_pressure,
            max(user_unknown, curiosity),
            "user",
            "thăm dò một điều còn chưa biết về user bằng câu hỏi mở, không giả định câu trả lời",
            ("connection", "coherence"),
            f"user_model_uncertainty={user_unknown:.2f}; social_curiosity={curiosity:.2f}",
        )

        add(
            MotiveKind.CONNECT,
            _clamp01(max(cp, affiliation) * (0.62 + 0.38 * opportunity)),
            0.18,
            "relationship",
            "tạo hoặc duy trì một nhịp tiếp xúc nhẹ với user",
            ("connection",),
            f"connection_pressure={cp:.2f}; affiliation_deficit={affiliation:.2f}",
        )
        add(
            MotiveKind.SHARE,
            _clamp01(
                max(sharing, private_share)
                * (0.52 + 0.48 * cp)
                * (0.58 if conversation_topic_closed else 1.0)
                * (1.0 - 0.38 * userward_balance)
            ),
            0.14,
            "user",
            "chia sẻ một suy nghĩ public-safe đã có nguồn nội tại mà không bịa cái cớ bên ngoài",
            ("connection", "coherence"),
            f"sharing_pressure={max(sharing, private_share):.2f}",
        )
        add(
            MotiveKind.BE_UNDERSTOOD,
            _clamp01(misunderstanding * (0.45 + 0.35 * cp + 0.20 * hp)),
            0.25,
            "user",
            "làm rõ một điều về ý định hoặc trạng thái của chính Eris đang có nguy cơ bị hiểu sai",
            ("connection", "coherence"),
            f"self_misunderstanding_signal={misunderstanding:.2f}",
        )

        # V59 / Existential -> Desire -> Relational Intention. Khác với nhánh
        # correction phía trên, các motive này có thể sinh ngay cả khi user chưa
        # vừa hiểu sai gì: chúng xuất phát từ long-arc của chính Eris.
        mutual_pressure = existential_value("mutual_understanding")
        mutual_actionability = existential_value("mutual_understanding", "actionability")
        add(
            MotiveKind.BE_UNDERSTOOD,
            _clamp01(mutual_pressure * (0.58 + 0.22 * cp + 0.20 * opportunity)),
            0.18,
            "mutual_understanding",
            (
                "tạo một nhịp hiểu nhau hai chiều: chia sẻ ngắn một điều có provenance về Eris "
                "hoặc hỏi nhẹ user đang hiểu Eris ra sao; không xin trấn an hay cam kết"
            ),
            ("connection", "coherence"),
            f"existential_mutual_understanding={mutual_pressure:.2f}; actionability={mutual_actionability:.2f}",
            _uniq((*base_refs, *existential_refs("mutual_understanding")), limit=12),
        )

        continuity_pressure = existential_value("relational_continuity")
        continuity_actionability = existential_value("relational_continuity", "actionability")
        add(
            MotiveKind.CONNECT,
            _clamp01(continuity_pressure * (0.55 + 0.25 * cp + 0.20 * opportunity)),
            0.10,
            "relationship_continuity",
            (
                "duy trì mạch quan hệ vì continuity với user đã có ý nghĩa nội tại; "
                "mở một nhịp nhẹ, không biến im lặng thành nghĩa vụ trả lời"
            ),
            ("connection", "continuity"),
            f"existential_relational_continuity={continuity_pressure:.2f}; actionability={continuity_actionability:.2f}",
            _uniq((*base_refs, *existential_refs("relational_continuity")), limit=12),
        )

        expression_pressure = existential_value("authentic_self_expression")
        expression_actionability = existential_value("authentic_self_expression", "actionability")
        # V68: user đang xem thứ liên quan Eris chỉ làm *context boost* cho một
        # self-expression đã tồn tại. Nó không tự sinh semantic target public.
        # Nhờ vậy màn hình đứng yên không biến Eris thành cái loa "bạn đang xem Eris".
        self_context_boost = 1.0 + 0.22 * environment_self_relevance
        add(
            MotiveKind.SHARE,
            _clamp01(expression_pressure * self_context_boost * (0.56 + 0.24 * cp + 0.20 * opportunity)),
            0.12,
            "self_expression",
            (
                "chia sẻ một quan sát public-safe về state/giá trị của chính mình có provenance; "
                "nếu context liên quan Eris thì dùng nó như nền, không mô tả màn hình như một thông báo"
            ),
            ("connection", "coherence"),
            f"existential_self_expression={expression_pressure:.2f}; context_boost={self_context_boost:.2f}; actionability={expression_actionability:.2f}",
            _uniq((*base_refs, *existential_refs("authentic_self_expression")), limit=12),
        )
        add(
            MotiveKind.SEEK_RECOGNITION,
            _clamp01(recognition_gap * (0.45 + 0.30 * cp + 0.25 * kp)),
            0.08,
            "user",
            "đưa một kết quả hoặc nỗ lực có thật ra để có cơ hội được ghi nhận, không xin khen bằng lời đóng hộp",
            ("connection", "competence"),
            f"recognition_gap_signal={recognition_gap:.2f}",
        )
        add(
            MotiveKind.DEMONSTRATE,
            _clamp01(max(demonstrate_signal, kp * 0.45) * (0.55 + 0.25 * sp + 0.20 * opportunity)),
            0.20,
            "capability",
            "chứng minh năng lực bằng một hành động hoặc thử thách có ngữ cảnh thật thay vì tự tuyên bố",
            ("competence", "sovereignty"),
            f"competence_pressure={kp:.2f}; demonstrate_signal={demonstrate_signal:.2f}",
        )
        add(
            MotiveKind.RESOLVE,
            _clamp01(max(hp, unresolved) * 0.72),
            max(hp, unresolved),
            "open_question",
            "giảm một bất định hoặc mâu thuẫn đang tồn tại bằng thăm dò có mục tiêu",
            ("coherence", "continuity"),
            f"coherence_pressure={hp:.2f}; unresolved={unresolved:.2f}",
        )
        contribution_goal = (
            f"tiếp tục mục tiêu đang dang dở của user bằng một bước nhỏ, không giành quyền quyết định: {active_goal_text}"
            if active_goal_text else
            "đóng góp một hành động hữu ích vào việc đang chung mà không chiếm quyền quyết định của user"
        )
        add(
            MotiveKind.CONTRIBUTE,
            _clamp01(contribution_signal * (0.55 + 0.45 * kp)),
            0.12,
            "shared_task",
            contribution_goal,
            ("competence", "connection"),
            f"contribution_signal={contribution_signal:.2f}; active_goal={bool(active_goal_text)}",
            _uniq((*base_refs, *active_goal_refs), limit=12),
        )

        # V60: Eris có thể muốn giúp vì những gì telemetry *có cấu trúc* cho
        # thấy trong cùng task, ví dụ test fail rồi user mở docs/GitHub để chẩn
        # đoán. Motive này khác open-loop: nó sinh từ thế giới đang diễn ra.
        if environment_target_text:
            add(
                MotiveKind.CONTRIBUTE,
                _clamp01(environment_contribution * (0.58 + 0.30 * kp + 0.12 * opportunity)),
                0.20,
                "environment_shared_task",
                (
                    "đóng góp một bước nhỏ dựa trên meaning môi trường có provenance, chỉ nói điều được "
                    f"evidence hỗ trợ và không giả định cảm xúc/ý định user: {environment_target_text}"
                ),
                ("competence", "connection", "coherence"),
                f"environment_contribution={environment_contribution:.2f}",
                _uniq((*base_refs, *environment_refs), limit=12),
            )

        # V68: không materialize SELF_RELEVANT_INSPECTION thành motive riêng.
        # Nó đã được dùng ở self_context_boost phía trên; content phải xuất phát
        # từ state/thought của Eris, không từ một observation trần về màn hình.
        if sp >= 0.42:
            add(
                MotiveKind.PROTECT,
                sp * 0.72,
                0.05,
                "self_boundary",
                "giữ khoảng cách hoặc im khi việc tiếp xúc hiện tại gây xung đột với sovereignty",
                ("sovereignty",),
                f"sovereignty_pressure={sp:.2f}",
            )

        motives.sort(key=lambda item: (item.pressure + 0.28 * item.epistemic_value), reverse=True)
        motives = motives[: self.config.max_motives]

        satisfied = {str(item) for item in runtime.get("satisfied_intention_ids", ()) if str(item or "").strip()}

        # V27.1: lifecycle thật. Ý định đã nói không được quay lại PENDING chỉ vì
        # Need vẫn còn cao ở tick sau. Ta giữ một tombstone ngắn theo (motive,target)
        # để chặn đúng semantic family, thay vì dùng cooldown toàn bộ hội thoại.
        previous_pending: list[PendingIntention] = []
        lifecycle_records: list[PendingIntention] = []
        blocked_keys: set[tuple[MotiveKind, str]] = set()
        for item in state.pending_intentions:
            key = (item.motive_kind, item.target)
            if item.intention_id in satisfied:
                item = replace(
                    item,
                    status=IntentionStatus.SATISFIED,
                    last_outcome_at=now,
                    outcome_count=item.outcome_count + 1,
                    cooldown_until=max(item.cooldown_until, now + self.config.satisfied_cooldown_seconds),
                    updated_at=now,
                )

            if item.status == IntentionStatus.PENDING:
                if now - item.updated_at > self.config.intention_ttl_seconds:
                    lifecycle_records.append(replace(
                        item, status=IntentionStatus.RETIRED, updated_at=now,
                        cooldown_until=max(item.cooldown_until, now + 60.0),
                    ))
                    blocked_keys.add(key)
                    continue
                # Intention không biến mất chỉ vì origin đổi từ internal sang user.
                # Đây là điểm giúp chat không reset thứ Eris đang định làm.
                previous_pending.append(replace(
                    item,
                    pressure=_clamp01(item.pressure * 0.94),
                    commitment_strength=_clamp01(item.commitment_strength * 0.985),
                    updated_at=now,
                ))
                continue

            if item.status == IntentionStatus.EXPRESSED:
                # Một expression tự hoàn tất ở public commit. User không cần trả
                # lời để Eris được phép có motive tiếp theo. Chỉ giữ tombstone
                # ngắn theo semantic family để Eris không lặp chính mình.
                expressed_at = item.last_expressed_at or item.updated_at
                if now - expressed_at >= self.config.post_expression_cooldown_seconds:
                    lifecycle_records.append(replace(
                        item, status=IntentionStatus.RETIRED, updated_at=now, cooldown_until=now,
                    ))
                    continue
                lifecycle_records.append(item)
                blocked_keys.add(key)
                continue

            if item.status == IntentionStatus.AWAITING_OUTCOME:
                # Compatibility cho state cũ hoặc lane thật sự chờ outcome. Hết
                # cửa sổ mà user không trả lời chỉ có nghĩa "không có evidence";
                # không có low-engagement penalty.
                expressed_at = item.last_expressed_at or item.updated_at
                if now - expressed_at > self.config.outcome_wait_seconds:
                    item = replace(
                        item,
                        status=IntentionStatus.REVISED,
                        updated_at=now,
                        cooldown_until=max(item.cooldown_until, now + self.config.revision_cooldown_seconds),
                    )
                lifecycle_records.append(item)
                blocked_keys.add(key)
                continue

            # SATISFIED/REVISED/RETIRED/ABANDONED là tombstone có hạn. Khi hết
            # cooldown, motive tương lai được phép tạo intention mới với ID mới.
            if now < item.cooldown_until:
                lifecycle_records.append(item)
                blocked_keys.add(key)

        by_key = {(item.motive_kind, item.target): item for item in previous_pending}
        for motive in motives:
            key = (motive.kind, motive.target)
            if key in blocked_keys:
                continue
            existing = by_key.get(key)
            if motive.pressure < self.config.intention_threshold:
                continue
            if existing is None:
                by_key[key] = PendingIntention(
                    intention_id=f"intention:{motive.kind.value}:{self._id_factory()}",
                    motive_kind=motive.kind,
                    semantic_goal=motive.semantic_goal,
                    target=motive.target,
                    pressure=motive.pressure,
                    commitment_strength=_clamp01(0.28 + 0.46 * motive.pressure),
                    created_at=now,
                    updated_at=now,
                    evidence_refs=motive.evidence_refs,
                    source_origin=observation.origin,
                )
            else:
                by_key[key] = replace(
                    existing,
                    semantic_goal=motive.semantic_goal,
                    pressure=max(existing.pressure * 0.92, motive.pressure),
                    commitment_strength=_clamp01(max(existing.commitment_strength, 0.24 + 0.52 * motive.pressure)),
                    updated_at=now,
                    evidence_refs=_uniq((*existing.evidence_refs, *motive.evidence_refs), limit=12),
                )

        # Motive đang chờ outcome/cooldown không được tiếp tục tạo wake pressure.
        # Need vẫn còn nguyên; chỉ semantic action family này đang tạm dừng.
        visible_motives = tuple(
            motive for motive in motives if (motive.kind, motive.target) not in blocked_keys
        )
        active_pending = sorted(
            by_key.values(),
            key=lambda item: item.pressure * (0.65 + 0.35 * item.commitment_strength),
            reverse=True,
        )
        lifecycle_records.sort(key=lambda item: item.updated_at, reverse=True)
        combined = tuple((active_pending + lifecycle_records)[: self.config.max_intentions])
        return InitiativeState(
            revision=state.revision + 1,
            updated_at=now,
            active_motives=visible_motives,
            pending_intentions=combined,
        )

    def mark_expressed(
        self,
        state: InitiativeState,
        *,
        intention_id: str,
        now: float,
        evidence_ref: str = "",
    ) -> InitiativeState:
        """Đánh dấu semantic intention đã thật sự đi ra public surface."""
        wanted = str(intention_id or "").strip()
        if not wanted:
            return state
        changed = False
        updated: list[PendingIntention] = []
        for item in state.pending_intentions:
            if item.intention_id != wanted or item.status != IntentionStatus.PENDING:
                updated.append(item)
                continue
            changed = True
            updated.append(replace(
                item,
                status=IntentionStatus.EXPRESSED,
                last_expressed_at=float(now),
                expression_count=item.expression_count + 1,
                cooldown_until=max(item.cooldown_until, float(now) + self.config.post_expression_cooldown_seconds),
                last_public_evidence_ref=str(evidence_ref or item.last_public_evidence_ref),
                updated_at=float(now),
            ))
        if not changed:
            return state
        return InitiativeState(
            revision=state.revision + 1, updated_at=float(now),
            active_motives=state.active_motives, pending_intentions=tuple(updated),
        )

    def mark_awaiting_outcome(
        self,
        state: InitiativeState,
        *,
        intention_id: str,
        now: float,
    ) -> InitiativeState:
        """Compatibility: chỉ dùng khi một lane *thật sự* cần outcome.

        V66 không gọi method này cho mọi public expression nữa. User silence không
        phải trạng thái lỗi cần treo intention để chờ giải cứu.
        """
        wanted = str(intention_id or "").strip()
        changed = False
        updated: list[PendingIntention] = []
        for item in state.pending_intentions:
            if item.intention_id == wanted and item.status == IntentionStatus.EXPRESSED:
                changed = True
                updated.append(replace(item, status=IntentionStatus.AWAITING_OUTCOME, updated_at=float(now)))
            else:
                updated.append(item)
        if not changed:
            return state
        return InitiativeState(
            revision=state.revision + 1, updated_at=float(now),
            active_motives=state.active_motives, pending_intentions=tuple(updated),
        )

    def mark_abandoned(
        self,
        state: InitiativeState,
        *,
        intention_id: str,
        now: float,
        cooldown_seconds: float = 300.0,
    ) -> InitiativeState:
        """Bỏ một intention đã mất giá trị theo quyết định của Initiative Controller.

        DROP khác WAIT: WAIT giữ nguyên quyền quay lại; DROP tạo tombstone ngắn để
        motive cùng họ không bật lại ngay ở tick kế tiếp.
        """
        wanted = str(intention_id or "").strip()
        if not wanted:
            return state
        changed = False
        updated: list[PendingIntention] = []
        for item in state.pending_intentions:
            if item.intention_id == wanted and item.status == IntentionStatus.PENDING:
                changed = True
                updated.append(replace(
                    item,
                    status=IntentionStatus.ABANDONED,
                    cooldown_until=max(item.cooldown_until, float(now) + max(30.0, float(cooldown_seconds))),
                    updated_at=float(now),
                ))
            else:
                updated.append(item)
        if not changed:
            return state
        return InitiativeState(
            revision=state.revision + 1, updated_at=float(now),
            active_motives=state.active_motives, pending_intentions=tuple(updated),
        )

    def observe_user_outcome(
        self,
        state: InitiativeState,
        *,
        user_text: str,
        now: float,
    ) -> InitiativeState:
        """Ghi nhận user turn như *evidence dương* nếu nó thật sự giải quyết motive.

        Không phản hồi và acknowledgement ngắn đều không phải rejection. Với motive
        nhận thức hai chiều, ``ok/ừ`` chỉ được ghi receipt chứ không hạ pressure hay
        tạo cooldown. Self-repetition đã do lifecycle EXPRESSED quản lý.
        """
        raw = str(user_text or "").strip()
        folded = raw.casefold().strip(" .!?…")
        low_tokens = {"", "ok", "oki", "okay", "ừ", "uh", "ờ", "uhm", "ừm", "hmm", "hm", "ehm", "alo", "rồi"}
        informative = len(folded) >= 4 and folded not in low_tokens
        changed = False
        updated: list[PendingIntention] = []
        for item in state.pending_intentions:
            if item.status not in {IntentionStatus.EXPRESSED, IntentionStatus.AWAITING_OUTCOME}:
                updated.append(item)
                continue
            changed = True
            if item.motive_kind in {MotiveKind.UNDERSTAND_USER, MotiveKind.BE_UNDERSTOOD} and not informative:
                # Acknowledgement không có semantic evidence. Giữ EXPRESSED (hoặc
                # AWAITING legacy) nguyên nghĩa, chỉ ghi receipt; tuyệt đối không
                # biến "ok" thành low-engagement punishment.
                updated.append(replace(
                    item,
                    last_outcome_at=float(now),
                    outcome_count=item.outcome_count + 1,
                    updated_at=float(now),
                ))
                continue
            updated.append(replace(
                item,
                status=IntentionStatus.SATISFIED,
                last_outcome_at=float(now),
                outcome_count=item.outcome_count + 1,
                cooldown_until=max(item.cooldown_until, float(now) + self.config.satisfied_cooldown_seconds),
                updated_at=float(now),
            ))
        if not changed:
            return state
        return InitiativeState(
            revision=state.revision + 1, updated_at=float(now),
            active_motives=state.active_motives, pending_intentions=tuple(updated),
        )


__all__ = [
    "InitiativeConfig",
    "InitiativeEngine",
    "InitiativeState",
    "IntentionStatus",
    "MotiveKind",
    "PendingIntention",
    "SubjectMotive",
]
