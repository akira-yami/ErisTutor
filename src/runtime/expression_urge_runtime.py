# -*- coding: utf-8 -*-
"""Content-first initiative cho ERIS V44.

V43 đã có mood sống và V42 đã có CognitiveEvent/ActiveConcern. V44 không thêm
một kho "ham muốn" mới. Module này chỉ giữ một projection ngắn hạn của những thứ
Eris *đã có nội dung để muốn biểu đạt*.

Luật quan trọng:
- connection/social pressure KHÔNG tự sinh lời thoại;
- contact_bid/user_probe chung chung không phải content, nên không được thành speech;
- một thought/concern/seed có nội dung có thể tạo urge và urge có thể sống qua
  nhiều tick khi self-rhythm DEFER hoặc Surface chưa hiện thực hóa được;
- cùng ý được paraphrase lại không tạo thêm urge, chỉ làm freshness giảm nếu
  không có evidence mới;
- public commit làm satisfaction tăng và pressure hạ. Surface text không feed
  ngược thành một event mới.

Nói ngắn: motive trả lời "tôi muốn gần bạn", urge trả lời "tôi muốn mang *cái gì*
sang phía bạn". Từ V102, urge KHÔNG tự cấp ExpressionCommitment nữa; production
phải đi qua ExpressionCommitmentTransitionV102. Neuro chỉ xếp hạng sau khi
transition đó đã commit semantic target.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from cortex.active_concern import clamp01, compact, semantic_overlap
from cortex.subject.expression_commitment import commitment_payload
from runtime.ai_orchestrated.affordance import ExecutiveAffordance
from runtime.ai_orchestrated.social_candidate import SocialSpeechCandidate


EXPRESSION_URGE_VERSION = "eris.initiative.v44_1.contentful_urge_lifecycle"

_GENERIC_SOCIAL_ACTS = {
    "contact_bid",
    "user_probe",
    "social_check_in",
    "curiosity_bid",
}
_GENERIC_TOPIC_MARKERS = (
    "ask one low-pressure",
    "ask one open",
    "make one low-pressure contact",
    "helps eris understand the user better",
    "tạo hoặc duy trì một nhịp tiếp xúc",
    "thăm dò một điều còn chưa biết về user",
)
_STRUCTURED_OWNERS = {
    "neuro_continuation",
}


def _refs(values: Iterable[Any], limit: int = 12) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(x) for x in values if str(x or "").strip()))[:limit]


def _digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:16]


def _candidate_contentful(candidate: Any) -> bool:
    topic = compact(getattr(candidate, "topic", ""), 600)
    if len(topic) < 18:
        return False
    act = str(getattr(candidate, "speech_act", "") or "").strip().lower()
    folded = topic.casefold()
    if act in _GENERIC_SOCIAL_ACTS and any(marker in folded for marker in _GENERIC_TOPIC_MARKERS):
        return False
    # Một generic contact/probe không có seed/evidence cụ thể vẫn chỉ là mong muốn
    # tiếp xúc. Giữ nó làm drive, không cho nó mặc áo "nội dung" để qua Neuro.
    if act in _GENERIC_SOCIAL_ACTS:
        seed_id = str(getattr(candidate, "seed_id", "") or "").strip()
        refs = _refs(getattr(candidate, "evidence_refs", ()) or ())
        if not seed_id and len(refs) <= 1:
            return False
    return True


def _motive_from_candidate(candidate: Any) -> str:
    declared = str(getattr(candidate, "eris_motive_kind", "") or "").strip().lower()
    act = str(getattr(candidate, "speech_act", "") or "").strip().lower()
    kind = str(getattr(candidate, "seed_event_kind", "") or getattr(candidate, "kind", "") or "").strip().lower()
    blob = f"{declared} {act} {kind}"
    if any(token in blob for token in ("play", "tease", "banter", "amuse", "joke", "challenge")):
        return "play"
    if any(token in blob for token in ("revisit", "memory", "callback", "continuation")):
        return "revisit"
    if any(token in blob for token in ("clarif", "repair", "correct", "be_understood")):
        return "clarify"
    if any(token in blob for token in ("contribute", "result", "finding", "discovery")):
        return "contribute"
    if any(token in blob for token in ("know", "explore", "curiosity", "question", "probe", "investigat")):
        return "explore"
    return "share"


def _speech_act_for(motive: str, original: str = "") -> str:
    """Chọn hình thức biểu đạt, không biến mọi curiosity thành phỏng vấn user."""
    original = str(original or "").strip().lower()
    if motive == "play":
        return "playful_observation"
    if motive == "revisit":
        return "memory_callback"
    if motive == "clarify":
        return "self_clarification"
    if motive == "contribute":
        return original or "share_grounded_runtime_finding"
    if motive == "explore":
        # V45: genuine question chỉ được material lifecycle phát ra khi nó có
        # expected_update cụ thể. Khi đã có contract đó thì giữ hình dạng hỏi;
        # curiosity khác vẫn mặc định là chia sẻ thought, không questionnaire.
        if original == "ask_genuine_question":
            return original
        return "share_self_originated_thought"
    return original if original and original not in _GENERIC_SOCIAL_ACTS else "share_self_originated_thought"


@dataclass(slots=True)
class ExpressionUrge:
    urge_id: str
    semantic_key: str
    motive: str
    content: str
    source: str
    evidence_refs: tuple[str, ...]
    concern_id: str = ""
    source_material_id: str = ""
    # V101: identity của private intention đã trưởng thành trước urge. Receipt này
    # cho public expression hạ activation upstream mà không biến Surface thành input.
    source_private_intent_id: str = ""
    base_strength: float = 0.0
    freshness: float = 0.0
    satisfaction: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_input_at: float = 0.0
    last_expressed_at: float = 0.0
    # Realization retry state. Desire/content may remain alive upstream, but a
    # failed public wording attempt gets bounded backoff and can be retired.
    surface_blocked_until: float = 0.0
    surface_duplicate_blocks: int = 0
    echo_count: int = 0
    original_speech_act: str = ""
    confidence: float = 0.72
    relevance: float = 0.60
    intrusion_risk: float = 0.14
    # V61: meaning có structured environment provenance cần được ưu tiên hơn một
    # urge nội bộ cũ khi cả hai cùng cạnh tranh. Đây không phải "màn hình luôn
    # thắng"; chỉ candidate đã qua EnvironmentMeaning + privacy mới có cờ này.
    environment_grounded: bool = False
    # V70: semantic development có thể reopen một phần desire đã consummate.
    semantic_development_mode: str = ""
    semantic_development_score: float = 0.0
    rumination_return: bool = False

    def effective_freshness(self, *, now: float) -> float:
        """Freshness sống theo thời gian, không phải con số đóng băng lúc tạo urge.

        V44 cũ giữ ``freshness=1.0`` vô hạn nếu không có input mới. Kết quả là
        một urge cũ có thể ngồi đầu hàng nhiều phút chỉ vì nó từng rất mới.
        Freshness ở đây có half-life khoảng 3 phút; evidence mới sẽ nâng lại
        stored freshness qua ``_ingest_content``.
        """
        since_input = max(0.0, float(now) - self.last_input_at)
        decay = 0.5 ** (since_input / 180.0)
        return clamp01(self.freshness * decay)

    def pressure(self, *, now: float, relational: Mapping[str, Any], affect: Mapping[str, Any]) -> float:
        age = max(0.0, float(now) - self.created_at)
        since_input = max(0.0, float(now) - self.last_input_at)
        effective_freshness = self.effective_freshness(now=now)
        # Hold chỉ giúp một ý "chín" khi nó còn tươi. Khi không có evidence/input
        # mới, freshness giảm và stale decay thắng dần. Pressure vì thế không thể
        # bò lên 1.0 rồi ở đó vĩnh viễn như V44.
        hold_gain = min(0.16, age / 240.0 * 0.16) * (0.25 + 0.75 * effective_freshness)
        stale_decay = min(0.34, max(0.0, since_input - 75.0) / 420.0 * 0.34)

        connection = clamp01(relational.get("connection_pressure", 0.0))
        outreach = clamp01(relational.get("outreach_drive", 0.0))
        social_energy = clamp01(affect.get("social_energy", affect.get("social", 0.5)), 0.5)
        curiosity = clamp01(affect.get("curiosity", 0.0))
        playful = clamp01(affect.get("playful_pull", affect.get("play", 0.0)))

        relational_boost = 0.12 * connection + 0.10 * outreach + 0.06 * social_energy
        motive_boost = 0.0
        if self.motive == "explore":
            motive_boost = 0.10 * curiosity
        elif self.motive == "play":
            motive_boost = 0.12 * playful
        elif self.motive in {"share", "revisit"}:
            motive_boost = 0.04 * curiosity

        return clamp01(
            self.base_strength
            + hold_gain
            + relational_boost
            + motive_boost
            - stale_decay
            - 0.62 * self.satisfaction
            - min(0.22, self.echo_count * 0.055)
        )

    def selection_priority(self, *, now: float, relational: Mapping[str, Any], affect: Mapping[str, Any]) -> float:
        """Độ ưu tiên giữa các urge, tách khỏi quyết định speak/silence của Neuro.

        Priority cần trả lời "ý nào đáng được mang ra cân *bây giờ*", nên ngoài
        pressure còn thưởng input mới và relevance. Nhờ vậy một event vừa xảy ra
        có thể preempt urge cũ đang tích pressure, thay vì bị starvation.
        """
        pressure = self.pressure(now=now, relational=relational, affect=affect)
        fresh = self.effective_freshness(now=now)
        since_input = max(0.0, float(now) - self.last_input_at)
        recency = clamp01(1.0 - since_input / 150.0)
        unsatisfied = clamp01(1.0 - self.satisfaction)
        return clamp01(
            0.58 * pressure
            + 0.20 * fresh
            + 0.12 * self.relevance
            + 0.10 * recency
            + (0.08 * fresh if self.environment_grounded else 0.0)
        ) * unsatisfied

    def to_dict(self, *, now: float, relational: Mapping[str, Any], affect: Mapping[str, Any]) -> dict[str, Any]:
        fresh = self.effective_freshness(now=now)
        return {
            "urge_id": self.urge_id,
            "motive": self.motive,
            "content": self.content,
            "source": self.source,
            "concern_id": self.concern_id,
            "source_material_id": self.source_material_id,
            "source_private_intent_id": self.source_private_intent_id,
            "pressure": round(self.pressure(now=now, relational=relational, affect=affect), 3),
            "priority": round(self.selection_priority(now=now, relational=relational, affect=affect), 3),
            "freshness": round(fresh, 3),
            "satisfaction": round(self.satisfaction, 3),
            "surface_blocked_seconds": round(max(0.0, self.surface_blocked_until - now), 1),
            "surface_duplicate_blocks": self.surface_duplicate_blocks,
            "environment_grounded": self.environment_grounded,
            "semantic_development_mode": self.semantic_development_mode,
            "semantic_development_score": round(self.semantic_development_score, 3),
            "rumination_return": self.rumination_return,
            "echo_count": self.echo_count,
            "age_seconds": round(max(0.0, now - self.created_at), 1),
            "evidence_refs": list(self.evidence_refs),
        }


class ExpressionUrgeRuntime:
    """Projection ngắn hạn: content -> desire-to-express -> Neuro."""

    def __init__(self, host: Any, *, logger: Any | None = None) -> None:
        self.host = host
        self.logger = logger or getattr(host, "logger", None)
        self._urges: dict[str, ExpressionUrge] = {}
        self._seen_inputs: dict[str, float] = {}
        self._last_log_signature = ""
        self._conversation_boundary_at: float = 0.0
        self._conversation_boundary_until: float = 0.0

    def _log(self, level: str, message: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(message, *args)

    def mark_conversation_boundary(
        self, *, now: float | None = None, reason: str = "topic_boundary", hold_seconds: float = 180.0
    ) -> int:
        """Close the *public* backlog at an explicit conversational boundary.

        Existing urges remain inspectable until prune, but are marked satisfied.
        For a short hold window, active-concern/mental-material sources may keep
        thinking privately but cannot immediately rebuild the same public monologue.
        Structured task continuations and new user-probe initiatives are unaffected.
        """
        current = float(now or time.time())
        self._conversation_boundary_at = max(self._conversation_boundary_at, current)
        self._conversation_boundary_until = max(
            self._conversation_boundary_until, current + max(15.0, float(hold_seconds))
        )
        changed = 0
        for urge in self._urges.values():
            if urge.created_at <= current and urge.satisfaction < 0.98:
                urge.satisfaction = 1.0
                urge.updated_at = current
                changed += 1
        self._log(
            "info", "[conversation rhythm] expression boundary reason=%s retired_urges=%s hold=%.0fs",
            reason, changed, max(0.0, self._conversation_boundary_until - current),
        )
        self._prune(current)
        return changed

    def _boundary_blocks_private_expression(self, now: float) -> bool:
        return bool(self._conversation_boundary_until > float(now))

    def _durable_topic_blocks(self, text: str, *, now: float) -> bool:
        try:
            from runtime.public_topic_boundary import ensure_public_topic_boundary_store
            return bool(ensure_public_topic_boundary_store(self.host).is_blocked(text, now=now))
        except Exception as exc:
            self._log("debug", "[topic boundary] urge check degraded: %s", exc)
            return False

    def _consummation_assessment(self, urge: ExpressionUrge, *, now: float) -> Mapping[str, Any]:
        """Đọc completion semantic trước khi urge được nâng thành commitment.

        Đây là lifecycle của desire biểu đạt, không phải một speech threshold mới.
        Nếu một ý đã được nói đủ, paraphrase mới phải kế thừa completion đó.
        """
        runtime = getattr(self.host, "expression_consummation_runtime", None)
        assess = getattr(runtime, "assess", None)
        if not callable(assess):
            return {}
        try:
            result = assess(
                content=urge.content,
                concern_id=urge.concern_id,
                evidence_refs=urge.evidence_refs,
                development_mode=urge.semantic_development_mode,
                development_score=urge.semantic_development_score,
                now=now,
            )
            to_dict = getattr(result, "to_dict", None)
            return dict(to_dict() if callable(to_dict) else result or {})
        except Exception as exc:
            self._log("debug", "[expression consummation] assessment degraded: %s", exc)
            return {}

    def _inherit_consummation(self, urge: ExpressionUrge, *, now: float) -> None:
        assessment = self._consummation_assessment(urge, now=now)
        if not assessment or not bool(assessment.get("matched", False)):
            return
        inherited = clamp01(assessment.get("inherited_satisfaction", 0.0))
        reopen = max(
            clamp01(assessment.get("reopen_credit", 0.0)),
            clamp01(assessment.get("novel_evidence", 0.0)),
            clamp01(assessment.get("development_reopen_credit", 0.0)),
        )
        # Bình thường completion chỉ tăng satisfaction. Chỉ user/evidence mới
        # được phép hạ nó để semantic family thực sự mở lại.
        if reopen >= 0.35:
            urge.satisfaction = min(urge.satisfaction, inherited)
            urge.freshness = clamp01(max(urge.freshness, 0.68 + 0.22 * reopen))
        else:
            urge.satisfaction = max(urge.satisfaction, inherited)
        urge.updated_at = max(urge.updated_at, now)

    def _refresh_consummation(self, now: float) -> None:
        for urge in self._urges.values():
            self._inherit_consummation(urge, now=now)

    def _find_similar(self, content: str) -> ExpressionUrge | None:
        best = None
        score = 0.0
        for urge in self._urges.values():
            current = semantic_overlap(content, urge.content)
            if current > score:
                best, score = urge, current
        return best if score >= 0.78 else None

    @staticmethod
    def _base_from_candidate(candidate: Any) -> float:
        relevance = clamp01(getattr(candidate, "relevance", 0.0))
        confidence = clamp01(getattr(candidate, "confidence", 0.0))
        stake = clamp01(getattr(candidate, "personal_stake", 0.0))
        quality = clamp01(getattr(candidate, "seed_quality_score", 0.0))
        return clamp01(0.18 + 0.30 * relevance + 0.16 * confidence + 0.16 * stake + 0.10 * quality)

    def _ingest_content(
        self,
        *,
        input_key: str,
        content: str,
        motive: str,
        source: str,
        evidence_refs: Sequence[Any],
        now: float,
        base_strength: float,
        freshness: float,
        concern_id: str = "",
        source_material_id: str = "",
        source_private_intent_id: str = "",
        original_speech_act: str = "",
        confidence: float = 0.72,
        relevance: float = 0.60,
        intrusion_risk: float = 0.14,
        environment_grounded: bool = False,
        semantic_development_mode: str = "",
        semantic_development_score: float = 0.0,
        rumination_return: bool = False,
    ) -> ExpressionUrge | None:
        content = compact(content, 520)
        if len(content) < 18:
            return None
        # Cùng input chỉ được tác động một lần. Scheduler nhìn lại không phải là
        # một trải nghiệm mới, cùng invariant với CognitiveEvent V42.
        if input_key in self._seen_inputs:
            similar = self._find_similar(content)
            return similar
        self._seen_inputs[input_key] = now

        refs = _refs(evidence_refs)
        similar = self._find_similar(content)
        if similar is not None:
            old_refs = set(similar.evidence_refs)
            new_evidence = bool(set(refs) - old_refs)
            development_reopen = float(semantic_development_score or 0.0) >= 0.62
            if new_evidence or development_reopen:
                similar.base_strength = clamp01(max(similar.base_strength, base_strength) + (0.07 if new_evidence else 0.04))
                similar.freshness = clamp01(max(similar.freshness, freshness) + 0.08)
                similar.echo_count = max(0, similar.echo_count - 1)
                similar.surface_blocked_until = 0.0
                similar.surface_duplicate_blocks = 0
                similar.satisfaction = min(similar.satisfaction, 0.48)
                similar.updated_at = now
                similar.last_input_at = now
                similar.evidence_refs = _refs((*similar.evidence_refs, *refs))
                # V101 provenance phải đi theo semantic urge kể cả khi reopen do
                # evidence mới chứ không chỉ semantic_development.
                similar.source_material_id = str(source_material_id or similar.source_material_id).strip()
                similar.source_private_intent_id = str(
                    source_private_intent_id or similar.source_private_intent_id
                ).strip()
                similar.environment_grounded = bool(similar.environment_grounded or environment_grounded)
                if concern_id:
                    similar.concern_id = concern_id
                # V70: nếu cùng semantic family nhưng insight thực sự phát triển,
                # urge phải mang *nội dung mới*, không giữ lại câu cũ rồi gọi là reopen.
                if development_reopen:
                    similar.content = content
                    similar.semantic_key = _digest(content)
                    similar.semantic_development_mode = str(semantic_development_mode or "").strip().lower()
                    similar.semantic_development_score = clamp01(semantic_development_score)
                    similar.rumination_return = bool(rumination_return)
            else:
                # Paraphrase cùng evidence không được tự phong thành novelty.
                similar.echo_count += 1
                similar.freshness = clamp01(similar.freshness * 0.86)
                similar.base_strength = clamp01(similar.base_strength * 0.97)
                similar.last_input_at = now
            self._inherit_consummation(similar, now=now)
            return similar

        urge_id = f"urge:{_digest(f'{source}|{content}')}"
        urge = ExpressionUrge(
            urge_id=urge_id,
            semantic_key=_digest(content),
            motive=motive,
            content=content,
            source=source,
            evidence_refs=refs,
            concern_id=concern_id,
            source_material_id=str(source_material_id or "").strip(),
            source_private_intent_id=str(source_private_intent_id or "").strip(),
            base_strength=clamp01(base_strength),
            freshness=clamp01(freshness),
            satisfaction=0.0,
            created_at=now,
            updated_at=now,
            last_input_at=now,
            original_speech_act=original_speech_act,
            confidence=clamp01(confidence, 0.72),
            relevance=clamp01(relevance, 0.60),
            intrusion_risk=clamp01(intrusion_risk, 0.14),
            environment_grounded=bool(environment_grounded),
            semantic_development_mode=str(semantic_development_mode or "").strip().lower(),
            semantic_development_score=clamp01(semantic_development_score),
            rumination_return=bool(rumination_return),
        )
        self._urges[urge_id] = urge
        self._inherit_consummation(urge, now=now)
        return urge

    def observe(
        self,
        affordances: Sequence[Any],
        *,
        active_concerns: Sequence[Mapping[str, Any]] = (),
        now: float | None = None,
    ) -> None:
        current = float(now or time.time())
        for affordance in tuple(affordances or ()):
            candidate = getattr(affordance, "candidate", None)
            if candidate is None or not _candidate_contentful(candidate):
                continue
            owner = str(getattr(affordance, "owner", "") or "")
            if owner in _STRUCTURED_OWNERS or str(getattr(candidate, "kind", "") or "") == "force_speak":
                continue
            if self._boundary_blocks_private_expression(current) and owner in {"mental_material", "active_concern"}:
                continue
            _candidate_topic = str(getattr(candidate, "topic", "") or "")
            if owner in {"mental_material", "active_concern"} and self._durable_topic_blocks(_candidate_topic, now=current):
                continue
            meta = dict(getattr(affordance, "metadata", {}) or {})
            # V66: MentalMaterial có hai lane khác nhau: ``concern`` chỉ nói rằng
            # thought đáng được giữ trong cognition; ``expression_ready`` mới là
            # quyết định nó đã bước sang desire biểu đạt. Không để việc bỏ legacy
            # readiness threshold vô tình biến mọi concern thành speech desire.
            if owner == "mental_material" and not bool(meta.get("expression_ready", False)):
                continue
            content = str(getattr(candidate, "topic", "") or "")
            motive = _motive_from_candidate(candidate)
            environment_grounded = bool(
                meta.get("environment_grounded", False)
                or str(getattr(candidate, "anchor_type", "") or "").strip().lower() == "environment"
            )
            base_strength = self._base_from_candidate(candidate)
            freshness = max(0.32, clamp01(getattr(candidate, "seed_specificity", 0.0)))
            if environment_grounded:
                # Structured environment meaning vừa xảy ra là evidence mới theo
                # thời gian thực. Cho nó đủ freshness để cạnh tranh với thought cũ;
                # OCR-only không có cờ environment_grounded nên không được boost.
                base_strength = max(base_strength, 0.72)
                freshness = max(freshness, 0.96)
            self._ingest_content(
                input_key=f"aff:{str(getattr(affordance, 'affordance_id', '') or _digest(content))}",
                content=content,
                motive=motive,
                source=owner or str(getattr(candidate, "source", "") or "affordance"),
                evidence_refs=tuple(getattr(candidate, "evidence_refs", ()) or ()),
                now=current,
                base_strength=base_strength,
                freshness=freshness,
                source_material_id=(
                    str(meta.get("material_id", "") or "")
                    if owner in {"mental_material", "private_intention_v101"}
                    else ""
                ),
                source_private_intent_id=(
                    str(meta.get("intent_id", "") or "")
                    if owner == "private_intention_v101"
                    else ""
                ),
                original_speech_act=str(getattr(candidate, "speech_act", "") or ""),
                confidence=clamp01(getattr(candidate, "confidence", 0.72), 0.72),
                relevance=clamp01(getattr(candidate, "relevance", 0.60), 0.60),
                intrusion_risk=max(
                    clamp01(getattr(candidate, "seed_intrusion_risk", 0.0)),
                    clamp01(getattr(candidate, "intrusion_risk", 0.0)),
                ),
                environment_grounded=environment_grounded,
                semantic_development_mode=str(getattr(candidate, "semantic_development_mode", "") or ""),
                semantic_development_score=clamp01(getattr(candidate, "semantic_development_score", 0.0)),
                rumination_return=bool(getattr(candidate, "rumination_return", False)),
            )

        # ActiveConcern là nguồn continuity. Concern không cần phải sinh từ seed;
        # memory/agenda/thought thật cũng có thể trở thành một urge có nội dung.
        # Sau explicit topic boundary, concern vẫn sống private nhưng tạm mất quyền
        # tái tạo public urge. Đây là conversational floor, không phải xoá suy nghĩ.
        _active_concern_inputs = () if self._boundary_blocks_private_expression(current) else tuple(active_concerns or ())
        for raw in _active_concern_inputs:
            concern = dict(raw)
            content = compact(concern.get("subject", ""), 520)
            if self._durable_topic_blocks(content, now=current):
                continue
            state = str(concern.get("state") or "simmering")
            novelty = clamp01(concern.get("novelty", 0.0))
            speech = clamp01(concern.get("speech_pressure", 0.0))
            activation = clamp01(concern.get("activation", 0.0))
            if len(content) < 18 or state in {"blocked", "habituated"}:
                continue
            if max(speech, activation * novelty) < 0.16:
                continue
            cid = str(concern.get("concern_id") or "")
            meta = dict(concern.get("metadata") or {})
            event_count = int(meta.get("event_count", 0) or 0)
            mutation_count = int(concern.get("mutation_count", 0) or 0)
            sources = " ".join(str(x) for x in tuple(concern.get("source_kinds", ()) or ())).lower()
            motive = "revisit" if float(concern.get("age_seconds", 0.0) or 0.0) >= 90.0 else "share"
            if "curiosity" in sources or "question" in sources or "?" in content:
                motive = "explore"
            if "agenda:" in sources:
                motive = "contribute"
            self._ingest_content(
                input_key=f"concern:{cid}:{event_count}:{mutation_count}",
                content=content,
                motive=motive,
                source="active_concern",
                evidence_refs=tuple(concern.get("evidence_refs", ()) or ()),
                now=current,
                base_strength=clamp01(0.16 + 0.46 * speech + 0.20 * activation + 0.12 * novelty),
                freshness=max(0.18, novelty),
                concern_id=cid,
                original_speech_act="",
                confidence=clamp01(0.52 + 0.30 * activation),
                relevance=clamp01(0.42 + 0.42 * max(speech, activation)),
                # Scene interruption không phải phẩm chất của thought. Giữ một
                # risk nền nhỏ; boundary/turn collision thuộc runtime phía sau.
                intrusion_risk=0.14,
            )
        self._refresh_consummation(current)
        self._prune(current)

    def _prune(self, now: float) -> None:
        # Projection sống theo session. Không biến một urge thoáng qua thành ký ức
        # mới chỉ vì nó chưa được nói.
        dead: list[str] = []
        for urge_id, urge in self._urges.items():
            age = max(0.0, now - urge.created_at)
            stale = max(0.0, now - urge.last_input_at)
            effective_freshness = urge.effective_freshness(now=now)
            if (
                urge.satisfaction >= 0.98
                or age > 1800.0
                or (stale > 480.0 and effective_freshness < 0.18)
            ):
                dead.append(urge_id)
        for urge_id in dead:
            self._urges.pop(urge_id, None)
        # Ledger input chỉ cần đủ dài để một scheduler/session không replay.
        for key, seen_at in tuple(self._seen_inputs.items()):
            if now - seen_at > 3600.0:
                self._seen_inputs.pop(key, None)

    def wake_pressure(self, *, now: float | None = None) -> float:
        current = float(now or time.time())
        self._refresh_consummation(current)
        self._prune(current)
        # Cheap path chưa cần đọc relationship/mood. Chỉ cần biết có một ý đang
        # đủ lực để TemporalMind nhìn lại. Modulation đầy đủ diễn ra sau wake.
        return max(
            (
                clamp01(
                    urge.base_strength
                    + min(0.14, max(0.0, current - urge.created_at) / 240.0 * 0.14)
                    * (0.25 + 0.75 * urge.effective_freshness(now=current))
                    - min(0.28, max(0.0, current - urge.last_input_at - 90.0) / 420.0 * 0.28)
                    - 0.60 * urge.satisfaction
                    - min(0.18, urge.echo_count * 0.05)
                )
                for urge in self._urges.values()
                if urge.surface_blocked_until <= current
            ),
            default=0.0,
        )

    def shape_affordances(
        self,
        affordances: Sequence[Any],
        *,
        active_concerns: Sequence[Mapping[str, Any]] = (),
        relational: Mapping[str, Any] | None = None,
        affect: Mapping[str, Any] | None = None,
        runtime_context: Mapping[str, Any] | None = None,
        now: float | None = None,
        limit: int = 3,
    ) -> tuple[Any, ...]:
        current = float(now or time.time())
        rel = dict(relational or {})
        aff = dict(affect or {})
        self.observe(affordances, active_concerns=active_concerns, now=current)

        # Structured continuations/task bindings giữ nguyên identity/claim owner.
        # Generic social openers bị loại; relation chỉ còn là lực modulation.
        preserved: list[Any] = []
        for affordance in tuple(affordances or ()):
            candidate = getattr(affordance, "candidate", None)
            owner = str(getattr(affordance, "owner", "") or "")
            if candidate is None:
                continue
            act = str(getattr(candidate, "speech_act", "") or "").strip().lower()
            kind = str(getattr(candidate, "kind", "") or "").strip().lower()
            if kind == "force_speak" or owner in _STRUCTURED_OWNERS:
                preserved.append(affordance)
                continue
            if act in _GENERIC_SOCIAL_ACTS and not _candidate_contentful(candidate):
                continue
            # Contentful seed/private candidate sẽ được đại diện bằng urge phía
            # dưới để không có hai coalition cùng một ý nhưng khác owner.

        ranked = sorted(
            self._urges.values(),
            key=lambda urge: (
                urge.selection_priority(now=current, relational=rel, affect=aff),
                urge.pressure(now=current, relational=rel, affect=aff),
                urge.effective_freshness(now=current),
                urge.updated_at,
            ),
            reverse=True,
        )
        emitted = 0
        for urge in ranked:
            if urge.surface_blocked_until > current:
                continue
            if self._durable_topic_blocks(urge.content, now=current):
                continue
            pressure = urge.pressure(now=current, relational=rel, affect=aff)
            effective_freshness = urge.effective_freshness(now=current)
            # V66: urge đã là một desire có nội dung. Không đặt thêm readiness
            # threshold để cấp quyền nói lần hai; stale/satisfied được lifecycle
            # phía trên tự prune.
            if urge.satisfaction >= 0.88:
                continue
            speech_act = _speech_act_for(urge.motive, urge.original_speech_act)

            # V102: ExpressionUrge chỉ là desire có nội dung; nó không còn quyền
            # tự biến mình thành semantic commitment trong production. Transition
            # runtime tích hợp urge qua thời gian, áp hysteresis + hard boundary
            # rồi mới trả payload. Fallback chỉ giữ compatibility cho unit/legacy
            # host không bootstrap V102; coordinator production luôn có runtime.
            transition = getattr(self.host, "expression_commitment_transition_v102", None)
            consider = getattr(transition, "consider_urge", None)
            if callable(consider):
                commitment = consider(
                    urge,
                    pressure=pressure,
                    freshness=effective_freshness,
                    runtime_context=runtime_context or {},
                    now=current,
                )
                if not commitment:
                    continue
            else:
                commitment = commitment_payload(
                    origin="expression_urge",
                    source_id=urge.urge_id,
                    semantic_target=urge.content,
                    motive=urge.motive,
                    pressure=pressure,
                    freshness=effective_freshness,
                    persistence=clamp01(1.0 - urge.satisfaction),
                )
            candidate = SocialSpeechCandidate(
                decision="candidate",
                kind="random_chat",
                topic=urge.content,
                speech_act=speech_act,
                anchor_type="internal",
                anchor_id=urge.concern_id or urge.urge_id,
                evidence_refs=urge.evidence_refs or ((urge.concern_id,) if urge.concern_id else (urge.urge_id,)),
                requires_grounding=False,
                confidence=max(0.50, urge.confidence),
                relevance=max(0.42, urge.relevance),
                urgency=clamp01(0.12 + 0.46 * pressure),
                tone="natural",
                reason=f"expression_urge:{urge.motive}:pressure={pressure:.2f}",
                source="expression_urge",
                eris_motive_kind=f"want_to_{urge.motive}",
                eris_motive_why_now="a contentful urge remained active long enough to be worth expressing",
                personal_stake=pressure,
                authenticity=1.0,
                uncertainty=clamp01(1.0 - urge.confidence),
                seed_intrusion_risk=urge.intrusion_risk,
                face_risk=clamp01(0.08 + 0.18 * urge.intrusion_risk),
                recovery_available=True,
                expression_urge_id=urge.urge_id,
                expression_pressure=pressure,
                expression_freshness=effective_freshness,
                expression_satisfaction=urge.satisfaction,
                expression_motive=urge.motive,
                expression_commitment=commitment,
                semantic_development_mode=urge.semantic_development_mode,
                semantic_development_score=urge.semantic_development_score,
                rumination_return=urge.rumination_return,
            )
            preserved.append(ExecutiveAffordance(
                affordance_id=f"expression-urge:{urge.urge_id}",
                owner="expression_urge",
                revision=int(urge.updated_at * 1000.0),
                candidate=candidate,
                claim_required=False,
                expires_at=current + 75.0,
                metadata={
                    "expression_urge": True,
                    "urge_id": urge.urge_id,
                    "concern_id": urge.concern_id,
                    "motive": urge.motive,
                    "commitment_transition_v102": callable(
                        getattr(getattr(self.host, "expression_commitment_transition_v102", None), "consider_urge", None)
                    ),
                },
            ))
            emitted += 1
            if emitted >= max(1, int(limit)):
                break

        self._log_state(current, rel, aff)
        return tuple(preserved)

    def _log_state(self, now: float, relational: Mapping[str, Any], affect: Mapping[str, Any]) -> None:
        if not self._urges:
            signature = "none"
        else:
            ranked = sorted(
                self._urges.values(),
                key=lambda urge: urge.selection_priority(now=now, relational=relational, affect=affect),
                reverse=True,
            )
            top = ranked[0]
            p = top.pressure(now=now, relational=relational, affect=affect)
            signature = f"{top.urge_id}:{round(p,1)}:{round(top.satisfaction,1)}:{top.echo_count}"
            if signature != self._last_log_signature:
                self._log(
                    "info",
                    "[expression urge] top id=%s motive=%s pressure=%.2f priority=%.2f fresh=%.2f satisfaction=%.2f echoes=%s source=%s content=%s",
                    top.urge_id,
                    top.motive,
                    p,
                    top.selection_priority(now=now, relational=relational, affect=affect),
                    top.effective_freshness(now=now),
                    top.satisfaction,
                    top.echo_count,
                    top.source,
                    compact(top.content, 180),
                )
        self._last_log_signature = signature

    @staticmethod
    def _realization_failure_kind(reason: str) -> str:
        value = str(reason or "").strip().lower()
        if any(token in value for token in (
            "timeout", "provider", "transport", "connection", "rate_limit",
            "temporarily", "model_unavailable", "service_unavailable", "unavailable", "exception",
        )):
            return "transient"
        if value.startswith("surface_duplicate_recent_utterance") or value.startswith(
            "surface_current_world_claim_without_evidence"
        ) or value.startswith("surface_bad_utterance") or value.startswith(
            "surface_claim_"
        ) or "claim_manifest" in value or "unknown_evidence" in value:
            return "reformulatable"
        return "bounded"

    def note_realization_failure(
        self, urge_id: str, *, reason: str = "surface_realization_failed", now: float | None = None
    ) -> bool:
        """Bound public realization retries without deleting the private thought.

        The semantic material/concern remains upstream.  What expires here is the
        *public attempt*.  A wording/grounding failure may be retried once after a
        short backoff; repeated semantic failures retire this urge so it cannot
        become a zombie candidate forever.  Transport/provider failures use a
        longer exponential backoff instead of semantic retirement.
        """
        urge = self._urges.get(str(urge_id or "").strip())
        if urge is None:
            return False
        current = float(now or time.time())
        kind = self._realization_failure_kind(reason)
        urge.surface_duplicate_blocks += 1
        blocks = urge.surface_duplicate_blocks
        urge.echo_count += 1
        urge.freshness = clamp01(urge.freshness * (0.88 if kind == "transient" else 0.80))
        urge.base_strength = clamp01(urge.base_strength * (0.98 if kind == "transient" else 0.90))
        urge.updated_at = current

        retired = False
        if kind == "transient" and blocks <= 5:
            # Keep the public commitment, but stop hammering a dead provider.
            urge.surface_blocked_until = current + min(90.0, 4.0 * (2 ** min(blocks - 1, 4)))
        elif kind == "transient":
            # A provider can be down for a long time. Do not let one public attempt
            # become immortal just because every failure is transport-shaped.
            retired = True
            urge.surface_blocked_until = current + 3600.0
            urge.satisfaction = max(urge.satisfaction, 0.99)
        elif blocks <= 1:
            # One later attempt is allowed with fresh Surface context/provenance.
            urge.surface_blocked_until = current + 10.0
        else:
            # Desire remains represented by MentalMaterial/ActiveConcern. This
            # public realization has exhausted its bounded attempts.
            retired = True
            urge.surface_blocked_until = current + 3600.0
            urge.satisfaction = max(urge.satisfaction, 0.99)

        self._log(
            "info",
            "[expression urge] realization-failed id=%s blocks=%s kind=%s reason=%s retry_in=%.1fs public_commitment_retired=%s desire_preserved_upstream=1",
            urge.urge_id,
            blocks,
            kind,
            compact(reason, 120),
            max(0.0, urge.surface_blocked_until - current),
            int(retired),
        )
        if retired:
            # Prune only the expression projection. Source material/concern is a
            # separate lifecycle and deliberately remains available to cognition.
            self._prune(current)
        return True

    def note_surface_duplicate(self, urge_id: str, *, now: float | None = None) -> bool:
        """Compatibility alias V61; V66 không còn suppress urge."""
        return self.note_realization_failure(
            urge_id, reason="surface_duplicate_recent_utterance", now=now
        )

    def note_expressed(
        self,
        urge_id: str,
        *,
        public_text: str = "",
        now: float | None = None,
    ) -> bool:
        urge = self._urges.get(str(urge_id or "").strip())
        if urge is None:
            return False
        current = float(now or time.time())
        urge.last_expressed_at = current
        urge.updated_at = current
        # V69: public receipt consummates semantic family, không chỉ đúng object urge.
        consummation = getattr(self.host, "expression_consummation_runtime", None)
        note_expression = getattr(consummation, "note_expression", None)
        if callable(note_expression):
            try:
                note_expression(
                    content=urge.content,
                    motive=urge.motive,
                    concern_id=urge.concern_id,
                    evidence_refs=urge.evidence_refs,
                    public_text=public_text,
                    development_mode=urge.semantic_development_mode,
                    development_score=urge.semantic_development_score,
                    now=current,
                )
            except Exception as exc:
                self._log("debug", "[expression consummation] public receipt degraded: %s", exc)
        urge.satisfaction = clamp01(max(urge.satisfaction, 0.90))
        urge.base_strength = clamp01(urge.base_strength * 0.42)
        urge.freshness = clamp01(urge.freshness * 0.64)
        if urge.source_material_id:
            material_runtime = getattr(self.host, "mental_material_runtime", None)
            note_material = getattr(material_runtime, "note_expressed", None)
            if callable(note_material):
                try:
                    note_material(urge.source_material_id, now=current)
                except Exception as exc:
                    self._log("debug", "[mental material] expression receipt degraded: %s", exc)
        # V102: public receipt đóng commitment transition riêng. Surface text
        # không quay lại làm evidence; chỉ urge_id receipt được truyền upstream.
        transition = getattr(self.host, "expression_commitment_transition_v102", None)
        note_transition = getattr(transition, "note_expressed", None)
        if callable(note_transition):
            try:
                note_transition(urge.urge_id, now=current)
            except Exception as exc:
                self._log("debug", "[commitment-transition-v102] expression receipt degraded: %s", exc)
        # V103: giải phóng public-workspace occupancy bằng semantic source id.
        # Không truyền public wording ngược vào competition/cognition.
        competition_v103 = getattr(self.host, "expression_competition_workspace_v103", None)
        note_competition = getattr(competition_v103, "note_expressed", None)
        if callable(note_competition):
            try:
                note_competition(urge.urge_id, now=current)
            except Exception as exc:
                self._log("debug", "[expression-workspace-v103] expression receipt degraded: %s", exc)
        if urge.source_private_intent_id:
            intention_runtime = getattr(self.host, "private_intention_runtime_v101", None)
            note_intention = getattr(intention_runtime, "note_expressed", None)
            if callable(note_intention):
                try:
                    note_intention(urge.source_private_intent_id, now=current)
                except Exception as exc:
                    self._log("debug", "[private-intention-v101] expression receipt degraded: %s", exc)
        self._log(
            "info",
            "[expression urge] expressed id=%s satisfaction=%.2f remaining=%.2f",
            urge.urge_id,
            urge.satisfaction,
            urge.base_strength,
        )
        return True

    def snapshot(
        self,
        *,
        now: float | None = None,
        relational: Mapping[str, Any] | None = None,
        affect: Mapping[str, Any] | None = None,
        limit: int = 8,
    ) -> tuple[dict[str, Any], ...]:
        current = float(now or time.time())
        rel = dict(relational or {})
        aff = dict(affect or {})
        self._refresh_consummation(current)
        self._prune(current)
        ranked = sorted(
            self._urges.values(),
            key=lambda urge: urge.selection_priority(now=current, relational=rel, affect=aff),
            reverse=True,
        )
        rows: list[dict[str, Any]] = []
        for urge in ranked[: max(0, int(limit))]:
            row = urge.to_dict(now=current, relational=rel, affect=aff)
            assessment = self._consummation_assessment(urge, now=current)
            if assessment:
                row["consummation"] = dict(assessment)
            rows.append(row)
        return tuple(rows)


__all__ = ["EXPRESSION_URGE_VERSION", "ExpressionUrge", "ExpressionUrgeRuntime"]
