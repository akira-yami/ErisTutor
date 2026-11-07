from __future__ import annotations

"""
goal_persistence.py - bộ nhớ luồng nền bền cho ERIS
====================================================

File này thay thế hành vi cũ "text dài / keyword => goal đang hoạt động".

Căn bệnh cũ:
    - bất kỳ text user hơi dài nào cũng bị xem là đáng thành goal.
    - message trực tiếp của user quá dễ nhận strength=0.72.
    - Ni drift thổi phồng urgency, nên Ni tệ biến thành nhắc dai bền vững.
    - token overlap >= 2 merge các thread kỹ thuật ERIS không liên quan.
    - closure detection có thể đọc nhầm "chưa xong" thành "xong".

Model mới:
    User text / thought stream / Ti signal -> GoalCandidate -> merge/decay

Phân biệt then chốt:
    luồng nền đang hoạt động != quyền được chen ngang.

ERIS có thể giữ continuity mà không lôi nó ra nói. Đúng, trí nhớ không nhắc dai.
Một phép màu có vẻ vẫn ngoài tầm với của đa số app năng suất.
"""

import hashlib
import time
from dataclasses import dataclass

try:
    from .orientation_context import (
        GoalTextKind,
        GoalTextSignal,
        clean_text,
        clamp01,
        extract_anchors,
        goal_match_score,
        infer_closure_signal,
        infer_goal_text_signal,
        normalize,
        tokens,
        unique_recent,
    )
except ImportError:  # Hữu ích khi test trong thư mục nháp phẳng.
    from orientation_context import (  # type: ignore
        GoalTextKind,
        GoalTextSignal,
        clean_text,
        clamp01,
        extract_anchors,
        goal_match_score,
        infer_closure_signal,
        infer_goal_text_signal,
        normalize,
        tokens,
        unique_recent,
    )


def _stable_goal_id(summary: str) -> str:
    digest = hashlib.sha1(normalize(summary).encode("utf-8")).hexdigest()[:12]
    return f"goal_{digest}"


def _as_tuple(value: object, *, limit: int = 8) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())[-limit:]


@dataclass(slots=True)
class GoalPersistenceState:
    """Một luồng nền bền.

    Các field quan trọng:
        strength:
            Luồng này nên còn sẵn trong memory mạnh đến mức nào.

        urgency:
            Nó có thể sớm quan trọng lại đến mức nào. Urgency không phải lệnh phải nói.

        intervention_permission:
            ERIS có được phép chủ động nhắc nó về sau hay không.
            Field này cố ý tách khỏi strength.

        coherence:
            Luồng này sau các update gần đây còn ổn định/liên quan đến mức nào.
    """

    goal_id: str = ""
    summary: str = ""
    source: str = ""
    status: str = "empty"  # giá trị: empty | active | stale | closed

    # Các trường số giữ để đọc state cũ.
    strength: float = 0.0
    urgency: float = 0.0
    drift: float = 0.0
    progress: float = 0.0
    recurrence_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_reactivated_at: float = 0.0
    next_hint: str = ""
    evidence: tuple[str, ...] = ()
    recent_summaries: tuple[str, ...] = ()

    # Các trường ngữ nghĩa mới.
    goal_type: str = ""
    intent: str = ""
    confidence: float = 0.0
    actionability: float = 0.0
    intervention_permission: float = 0.0
    coherence: float = 0.0
    anchors: tuple[str, ...] = ()
    stale_reason: str = ""

    @property
    def active(self) -> bool:
        effective_confidence = self.confidence
        if effective_confidence <= 0.0 and self.status == "active" and self.summary and self.strength >= 0.18:
            effective_confidence = 0.50
        return (
            self.status == "active"
            and bool(self.summary)
            and self.strength >= 0.18
            and effective_confidence >= 0.22
        )

    @property
    def can_intervene(self) -> bool:
        """ERIS có được phép chủ động nhắc goal này hay không.

        Một luồng được nhớ có thể active trong khi hàm này trả False. Đó mới là
        toàn bộ ý chính. Continuity không phải dây xích. Tinh tế đến đáng sợ, đúng.

        Hai đường vào can_intervene:
        1. Task trực tiếp từ user (CONCRETE_TASK, DEBUG_THREAD, CORRECTION_THREAD):
           threshold thấp hơn (intervention_permission >= 0.45), vì user đã bày tỏ ý định.
        2. Luồng nền từ cognitive (runtime_thread, unresolved_thread):
           threshold cao hơn (intervention_permission >= 0.52), phải tái xuất hiện >= 2 lần
           và có coherence >= 0.50 để tránh Eris nhắc những chủ đề yếu/ngẫu nhiên.
        """

        effective_confidence = self.confidence
        if effective_confidence <= 0.0 and self.status == "active" and self.summary and self.strength >= 0.18:
            effective_confidence = 0.50
        effective_actionability = self.actionability
        if effective_actionability <= 0.0 and self.status == "active" and self.summary:
            effective_actionability = 0.35
        if not self.active:
            return False
        if effective_confidence < 0.42:
            return False

        # Đường 1: goal từ user message — threshold thấp, goal_type hẹp
        direct_user_types = {
            GoalTextKind.CONCRETE_TASK.value,
            GoalTextKind.DEBUG_THREAD.value,
            GoalTextKind.CORRECTION_THREAD.value,
        }
        if self.goal_type in direct_user_types:
            return (
                self.intervention_permission >= 0.45
                and effective_actionability >= 0.40
            )

        # Đường 2: luồng nền từ cognitive_momentum / cognitive_attractor
        # Phải tái xuất hiện ít nhất 2 lần (recurrence_count >= 2)
        # để tránh Eris nhảy vào sớm từ 1 thought ngẫu nhiên.
        cognitive_types = {"runtime_thread", "unresolved_thread"}
        if self.goal_type in cognitive_types:
            return (
                self.intervention_permission >= 0.52
                and effective_actionability >= 0.42
                and self.coherence >= 0.50
                and self.recurrence_count >= 2
            )

        # Các goal_type khác (DESIGN_DIRECTION, RESEARCH_THREAD, ...): dùng threshold gốc
        return (
            self.intervention_permission >= 0.55
            and effective_actionability >= 0.45
            and self.goal_type in direct_user_types
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "goal_id": self.goal_id,
            "summary": self.summary,
            "source": self.source,
            "status": self.status,
            "strength": round(float(self.strength), 3),
            "urgency": round(float(self.urgency), 3),
            "drift": round(float(self.drift), 3),
            "progress": round(float(self.progress), 3),
            "recurrence_count": int(self.recurrence_count),
            "created_at": round(float(self.created_at), 3),
            "updated_at": round(float(self.updated_at), 3),
            "last_reactivated_at": round(float(self.last_reactivated_at), 3),
            "next_hint": self.next_hint,
            "evidence": list(self.evidence),
            "recent_summaries": list(self.recent_summaries),
            # Các trường mới.
            "goal_type": self.goal_type,
            "intent": self.intent,
            "confidence": round(float(self.confidence), 3),
            "actionability": round(float(self.actionability), 3),
            "intervention_permission": round(float(self.intervention_permission), 3),
            "coherence": round(float(self.coherence), 3),
            "anchors": list(self.anchors),
            "stale_reason": self.stale_reason,
        }

    @classmethod
    def from_dict(cls, data: object) -> "GoalPersistenceState":
        if not isinstance(data, dict):
            return cls()
        summary = clean_text(data.get("summary"), limit=180)
        status = clean_text(data.get("status"), limit=40) or "empty"
        strength = clamp01(data.get("strength", 0.0))
        # Tương thích ngược: trạng thái cũ không có confidence/actionability.
        default_conf = 0.50 if summary and status == "active" and strength >= 0.18 else 0.0
        default_actionability = 0.35 if summary and status == "active" else 0.0
        return cls(
            goal_id=clean_text(data.get("goal_id"), limit=80) or (_stable_goal_id(summary) if summary else ""),
            summary=summary,
            source=clean_text(data.get("source"), limit=80),
            status=status,
            strength=strength,
            urgency=clamp01(data.get("urgency", 0.0)),
            drift=clamp01(data.get("drift", 0.0)),
            progress=clamp01(data.get("progress", 0.0)),
            recurrence_count=max(0, int(data.get("recurrence_count", 0) or 0)),
            created_at=max(0.0, float(data.get("created_at", 0.0) or 0.0)),
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
            last_reactivated_at=max(0.0, float(data.get("last_reactivated_at", 0.0) or 0.0)),
            next_hint=clean_text(data.get("next_hint"), limit=180),
            evidence=_as_tuple(data.get("evidence"), limit=10),
            recent_summaries=_as_tuple(data.get("recent_summaries"), limit=10),
            goal_type=clean_text(data.get("goal_type"), limit=80),
            intent=clean_text(data.get("intent"), limit=80),
            confidence=clamp01(data.get("confidence", default_conf)),
            actionability=clamp01(data.get("actionability", default_actionability)),
            intervention_permission=clamp01(data.get("intervention_permission", 0.18)),
            coherence=clamp01(data.get("coherence", default_conf)),
            anchors=_as_tuple(data.get("anchors"), limit=12),
            stale_reason=clean_text(data.get("stale_reason"), limit=160),
        )

    def render_prompt_context(self) -> str:
        """Văn bản dành cho prompt LLM.

        Đoạn này không được nghe như mệnh lệnh. Nó là context continuity, không phải
        bằng chứng màn hình, không phải chuông báo nhiệm vụ, và chắc chắn không phải
        giấy phép để ERIS biến thành giám thị hành lang cầm JSON.
        """

        if not self.active:
            return "goal_persistence: none active."
        evidence = " | ".join(self.evidence[-5:]) or "none"
        can_intervene = 1 if self.can_intervene else 0
        return (
            "goal_persistence: durable background thread; use as continuity, not current screen proof.\n"
            f"- thread={self.summary}; type={self.goal_type or 'unknown'}; source={self.source or 'unknown'}; "
            f"strength={self.strength:.2f}; urgency={self.urgency:.2f}; confidence={self.confidence:.2f}; "
            f"coherence={self.coherence:.2f}; recurrence={self.recurrence_count}.\n"
            f"- intervention_permission={self.intervention_permission:.2f}; can_intervene={can_intervene}; "
            "if can_intervene=0, keep it as quiet background only.\n"
            f"- next_hint={self.next_hint or 'preserve continuity only if current context still fits'}.\n"
            f"- evidence={evidence}."
        )

    def render_ni_context(self) -> str:
        """Ngữ cảnh goal nén, máy đọc được, dành cho Ni.
        """

        if not self.active:
            return "goal_signal: active=0;"
        return (
            "goal_signal: "
            f"active=1; type={self.goal_type or 'unknown'}; "
            f"strength={self.strength:.3f}; urgency={self.urgency:.3f}; "
            f"confidence={self.confidence:.3f}; actionability={self.actionability:.3f}; "
            f"intervention_permission={self.intervention_permission:.3f}; "
            f"summary={self.summary};"
        )


@dataclass(frozen=True, slots=True)
class GoalCandidate:
    summary: str
    source: str
    goal_type: str
    intent: str
    strength: float
    urgency: float
    confidence: float
    actionability: float
    intervention_permission: float
    coherence: float
    drift: float
    next_hint: str
    evidence: tuple[str, ...]
    anchors: tuple[str, ...]
    replace_priority: float = 0.0


class GoalPersistenceEngine:
    """Nâng lực kéo nhận thức lặp lại thật thành một luồng nền bền."""

    def update(
        self,
        state: GoalPersistenceState,
        *,
        channel: str,
        snapshot=None,
        ni_report=None,
        ti_report=None,
        thought_stream_report=None,
        broadcast=None,
        user_text: str = "",
        force_speak: bool = False,
        now: float | None = None,
    ) -> GoalPersistenceState:
        current_time = time.time() if now is None else max(0.0, float(now or 0.0))
        base = state if isinstance(state, GoalPersistenceState) else GoalPersistenceState()

        closure = infer_closure_signal(user_text, thought_stream_report=thought_stream_report)
        if closure.closed and base.active:
            user_tokens = tokens(user_text)
            match_score = goal_match_score(base.summary, user_text, left_anchors=base.anchors, right_anchors=extract_anchors(user_text))
            if len(user_tokens) <= 3 or match_score >= 0.42:
                return self._close(base, now=current_time, evidence="closure_signal:" + ",".join(closure.evidence))

        candidate = self._candidate(
            channel=channel,
            snapshot=snapshot,
            ni_report=ni_report,
            ti_report=ti_report,
            thought_stream_report=thought_stream_report,
            broadcast=broadcast,
            user_text=user_text,
            force_speak=force_speak,
        )
        if candidate is None:
            return self._decay(base, now=current_time)
        return self._merge(base, candidate, now=current_time)

    # ------------------------------------------------------------------
    # Sinh ứng viên
    # ------------------------------------------------------------------

    def _candidate(
        self,
        *,
        channel: str,
        snapshot,
        ni_report,
        ti_report,
        thought_stream_report,
        broadcast,
        user_text: str,
        force_speak: bool,
    ) -> GoalCandidate | None:
        direct = channel in {"chat", "force_speak"} or bool(force_speak)
        if direct and (user_text or "").strip():
            signal = infer_goal_text_signal(user_text, channel=channel, force_speak=force_speak)
            if signal.should_persist:
                return self._from_user_signal(signal, channel=channel, ni_report=ni_report)

        momentum_candidate = self._from_thought_momentum(thought_stream_report, ni_report=ni_report)
        if momentum_candidate is not None:
            return momentum_candidate

        attractor_candidate = self._from_thought_attractor(thought_stream_report, ni_report=ni_report)
        if attractor_candidate is not None:
            return attractor_candidate

        ti_candidate = self._from_ti_realign(ti_report, broadcast=broadcast, snapshot=snapshot, ni_report=ni_report)
        if ti_candidate is not None:
            return ti_candidate

        return None

    def _from_user_signal(self, signal: GoalTextSignal, *, channel: str, ni_report) -> GoalCandidate:
        # Ni chỉ được cộng urgency rất nhỏ nếu nó đã có áp lực can thiệp.
        ni_bump = 0.0
        if bool(getattr(ni_report, "should_raise_guard", False)):
            ni_bump = min(0.06, clamp01(getattr(ni_report, "drift_score", 0.0)) * 0.06)
        urgency = clamp01(signal.urgency + ni_bump)
        strength = clamp01(signal.strength)
        confidence = clamp01(signal.confidence)
        return GoalCandidate(
            summary=clean_text(signal.summary, limit=180),
            source="user_message",
            goal_type=signal.kind,
            intent=signal.kind,
            strength=strength,
            urgency=urgency,
            confidence=confidence,
            actionability=signal.actionability,
            intervention_permission=signal.intervention_permission,
            coherence=max(0.42, confidence * 0.82 + signal.actionability * 0.18),
            drift=min(0.35, clamp01(getattr(ni_report, "drift_score", 0.0)) * 0.40),
            next_hint=self._hint_for_kind(signal.kind),
            evidence=("direct_user_goal", f"channel={channel}", *signal.evidence),
            anchors=signal.anchors,
            replace_priority=0.82 if signal.kind == GoalTextKind.CONCRETE_TASK.value else 0.68,
        )

    def _from_thought_momentum(self, thought_stream_report, *, ni_report) -> GoalCandidate | None:
        momentum = getattr(thought_stream_report, "momentum", None)
        if momentum is None:
            return None
        if str(getattr(momentum, "affordance", "") or "") != "attention":
            return None
        score = clamp01(getattr(momentum, "score", 0.0))
        if score < 0.66:
            return None
        topic = clean_text(getattr(momentum, "topic_seed", ""), limit=160)
        if not topic:
            return None
        anchors = extract_anchors(topic)
        if not anchors and len(tokens(topic)) < 5:
            return None

        signal = infer_goal_text_signal(topic, channel="internal")
        if signal.kind in {GoalTextKind.CASUAL.value, GoalTextKind.RECOVERY.value, GoalTextKind.NONE.value}:
            return None

        tension = clamp01(getattr(momentum, "tension", 0.0))
        # Khi momentum mạnh (score >= 0.76) và có tension thực sự:
        # nâng intervention_permission để goal này có thể đạt can_intervene=True
        # sau khi tích lũy đủ recurrence. Cap 0.58 để vẫn cần recurrence >= 2.
        if score >= 0.76 and tension >= 0.35:
            max_intervention = 0.58
            max_actionability = 0.54
        elif score >= 0.68:
            max_intervention = 0.44
            max_actionability = 0.42
        else:
            max_intervention = 0.34
            max_actionability = 0.30

        return GoalCandidate(
            summary=topic,
            source="cognitive_momentum",
            goal_type=signal.kind if signal.kind != GoalTextKind.COMMENTARY.value else "runtime_thread",
            intent="runtime_thread",
            strength=clamp01(score * 0.72),
            urgency=clamp01(tension * 0.60),
            confidence=clamp01(max(0.42, signal.confidence, score * 0.66)),
            actionability=clamp01(max(signal.actionability, max_actionability if anchors else max_actionability * 0.60)),
            intervention_permission=clamp01(min(max_intervention, tension * max_intervention / 0.40)),
            coherence=clamp01(score * 0.72),
            drift=min(0.30, clamp01(getattr(ni_report, "drift_score", 0.0)) * 0.35),
            next_hint=(
                "bring this unresolved thread back naturally when current context reconnects to it"
                if score >= 0.76 else
                "keep this unresolved thread available, but do not surface it unless current context invites it"
            ),
            evidence=("thought_momentum", *tuple(getattr(momentum, "evidence", ()) or ())[:6]),
            anchors=anchors,
            replace_priority=0.38 if score >= 0.76 else 0.34,
        )

    def _from_thought_attractor(self, thought_stream_report, *, ni_report) -> GoalCandidate | None:
        attractor = getattr(thought_stream_report, "active_attractor", None)
        if attractor is None:
            return None
        if not bool(getattr(attractor, "attractor", False)):
            return None
        score = clamp01(getattr(attractor, "score", 0.0))
        if score < 0.70:
            return None
        topic = clean_text(getattr(attractor, "topic_seed", ""), limit=160)
        if not topic:
            return None
        anchors = extract_anchors(topic)
        if not anchors and len(tokens(topic)) < 6:
            return None

        signal = infer_goal_text_signal(topic, channel="internal")
        if signal.kind in {GoalTextKind.CASUAL.value, GoalTextKind.RECOVERY.value, GoalTextKind.NONE.value}:
            return None

        tension = clamp01(getattr(attractor, "tension", 0.0))
        # Attractor là vòng lặp nhận thức mạnh hơn momentum đơn thuần.
        # Khi score >= 0.78 và có tension: nâng caps để sau vài cycle tích lũy
        # goal có thể đạt can_intervene=True (vẫn cần recurrence >= 2).
        if score >= 0.78 and tension >= 0.30:
            max_intervention = 0.64
            max_actionability = 0.58
        elif score >= 0.72:
            max_intervention = 0.50
            max_actionability = 0.44
        else:
            max_intervention = 0.38
            max_actionability = 0.32

        return GoalCandidate(
            summary=topic,
            source="cognitive_attractor",
            goal_type=signal.kind if signal.kind != GoalTextKind.COMMENTARY.value else "unresolved_thread",
            intent="unresolved_thread",
            strength=clamp01(score * 0.76),
            urgency=clamp01(tension * 0.62),
            confidence=clamp01(max(0.45, signal.confidence, score * 0.68)),
            actionability=clamp01(max(signal.actionability, max_actionability if anchors else max_actionability * 0.60)),
            intervention_permission=clamp01(min(max_intervention, tension * max_intervention / 0.35)),
            coherence=clamp01(score * 0.74),
            drift=min(0.30, clamp01(getattr(ni_report, "drift_score", 0.0)) * 0.35),
            next_hint=(
                "reconnect to this attractor thread naturally when context overlaps — it has looped enough to surface"
                if score >= 0.78 else
                "bias attention toward this unresolved loop only when evidence reconnects to it"
            ),
            evidence=("thought_attractor", *tuple(getattr(attractor, "evidence", ()) or ())[:6]),
            anchors=anchors,
            replace_priority=0.42 if score >= 0.78 else 0.38,
        )

    def _from_ti_realign(self, ti_report, *, broadcast, snapshot, ni_report) -> GoalCandidate | None:
        mode = str(getattr(ti_report, "mode", "") or "")
        if mode not in {"goal_drift_alert", "goal_realign"}:
            return None
        content = clean_text(getattr(broadcast, "content", "") or getattr(snapshot, "focus_summary", ""), limit=160)
        if not content:
            return None

        # Ti/Ni có thể giữ một thread, không được bịa goal mạnh từ telemetry.
        signal = infer_goal_text_signal(content, channel="autonomous")
        anchors = signal.anchors or extract_anchors(content)
        if not anchors and len(tokens(content)) < 6:
            return None

        ni_conflict = clamp01(getattr(ni_report, "drift_score", 0.0))
        ni_guard = bool(getattr(ni_report, "should_raise_guard", False))
        strength = 0.34 + (0.10 if ni_guard else 0.0) + min(0.08, ni_conflict * 0.08)
        return GoalCandidate(
            summary=content,
            source="ti_realign_soft",
            goal_type=signal.kind if signal.kind not in {GoalTextKind.NONE.value, GoalTextKind.CASUAL.value} else "observed_thread",
            intent="soft_realign",
            strength=clamp01(strength),
            urgency=clamp01(0.18 + (0.10 if ni_guard else 0.0)),
            confidence=clamp01(max(0.36, signal.confidence * 0.72)),
            actionability=clamp01(max(0.20, signal.actionability * 0.70)),
            intervention_permission=0.18 if not ni_guard else 0.30,
            coherence=0.40,
            drift=min(0.32, ni_conflict * 0.40),
            next_hint="keep as soft background only; do not nag from telemetry alone",
            evidence=(f"ti_mode={mode}", f"ni_guard={int(ni_guard)}", f"ni_conflict={ni_conflict:.2f}"),
            anchors=anchors,
            replace_priority=0.22,
        )

    # ------------------------------------------------------------------
    # Gộp / suy giảm / đóng
    # ------------------------------------------------------------------

    def _merge(self, state: GoalPersistenceState, candidate: GoalCandidate, *, now: float) -> GoalPersistenceState:
        summary = clean_text(candidate.summary, limit=180)
        if not summary:
            return self._decay(state, now=now)

        match = self._match_score(state, candidate) if state.active else 0.0
        matched = state.active and match >= 0.58

        # Nếu một task trực tiếp mới và mạnh xuất hiện, cho nó thay thread active cũ.
        # Nếu ứng viên nội bộ yếu xuất hiện khi goal mạnh đang tồn tại, giữ cái cũ.
        if state.active and not matched:
            old_hold = state.strength * 0.78 + state.confidence * 0.16
            new_push = candidate.strength * 0.78 + candidate.replace_priority * 0.22
            if new_push < old_hold and candidate.source != "user_message":
                return self._decay(state, now=now, soft_reactivation=candidate.summary)

        old_strength = state.strength if matched else min(state.strength * 0.36, 0.24)
        old_urgency = state.urgency if matched else state.urgency * 0.42
        old_conf = state.confidence if matched else state.confidence * 0.50
        old_intervention = state.intervention_permission if matched else state.intervention_permission * 0.42

        strength = clamp01(max(old_strength, candidate.strength) + (0.06 if matched else 0.0))
        urgency = clamp01(max(old_urgency * (0.86 if matched else 0.55), candidate.urgency))
        confidence = clamp01(max(old_conf * (0.90 if matched else 0.60), candidate.confidence))
        actionability = clamp01(max(state.actionability * (0.88 if matched else 0.52), candidate.actionability))
        intervention = clamp01(max(old_intervention * (0.82 if matched else 0.48), candidate.intervention_permission))
        coherence = clamp01(max(state.coherence * (0.90 if matched else 0.52), candidate.coherence))

        recurrence = state.recurrence_count + 1 if matched else 1
        created_at = state.created_at if matched and state.created_at > 0.0 else now
        goal_id = state.goal_id if matched and state.goal_id else _stable_goal_id(summary)
        kept_summary = state.summary if matched else summary
        anchors = unique_recent((*(state.anchors if matched else ()), *candidate.anchors), limit=12)
        evidence = unique_recent(
            (
                *(state.evidence if matched else ()),
                *candidate.evidence,
                f"match={match:.2f}" if state.active else "new_goal",
            ),
            limit=10,
        )

        return GoalPersistenceState(
            goal_id=goal_id,
            summary=kept_summary,
            source=candidate.source,
            status="active",
            strength=strength,
            urgency=urgency,
            drift=candidate.drift,
            progress=state.progress if matched else 0.0,
            recurrence_count=recurrence,
            created_at=created_at,
            updated_at=now,
            last_reactivated_at=now,
            next_hint=clean_text(candidate.next_hint, limit=180),
            evidence=evidence,
            recent_summaries=unique_recent((*state.recent_summaries, summary), limit=10),
            goal_type=state.goal_type if matched and state.goal_type else candidate.goal_type,
            intent=candidate.intent,
            confidence=confidence,
            actionability=actionability,
            intervention_permission=intervention,
            coherence=coherence,
            anchors=anchors,
            stale_reason="",
        )

    def _decay(self, state: GoalPersistenceState, *, now: float, soft_reactivation: str = "") -> GoalPersistenceState:
        if not state.summary:
            return state

        age_minutes = max(0.0, (now - (state.updated_at or state.created_at or now)) / 60.0)
        keep = 0.987 ** min(360.0, age_minutes)
        strength = clamp01(state.strength * keep)
        urgency = clamp01(state.urgency * (keep ** 1.22))
        confidence = clamp01(state.confidence * (keep ** 0.82))
        intervention = clamp01(state.intervention_permission * (keep ** 1.35))
        coherence = clamp01(state.coherence * keep)

        status = state.status
        stale_reason = state.stale_reason
        if state.status == "active" and (strength < 0.12 or confidence < 0.20):
            status = "stale"
            stale_reason = "low_strength_or_confidence"

        recent = state.recent_summaries
        if soft_reactivation:
            recent = unique_recent((*recent, soft_reactivation), limit=10)

        return GoalPersistenceState(
            goal_id=state.goal_id,
            summary=state.summary,
            source=state.source,
            status=status,
            strength=strength,
            urgency=urgency,
            drift=state.drift,
            progress=state.progress,
            recurrence_count=state.recurrence_count,
            created_at=state.created_at,
            updated_at=now if state.status == "active" else state.updated_at,
            last_reactivated_at=state.last_reactivated_at,
            next_hint=state.next_hint,
            evidence=state.evidence,
            recent_summaries=recent,
            goal_type=state.goal_type,
            intent=state.intent,
            confidence=confidence,
            actionability=clamp01(state.actionability * (keep ** 0.90)),
            intervention_permission=intervention,
            coherence=coherence,
            anchors=state.anchors,
            stale_reason=stale_reason,
        )

    def _close(self, state: GoalPersistenceState, *, now: float, evidence: str) -> GoalPersistenceState:
        return GoalPersistenceState(
            goal_id=state.goal_id,
            summary=state.summary,
            source=state.source,
            status="closed",
            strength=clamp01(state.strength * 0.16),
            urgency=clamp01(state.urgency * 0.10),
            drift=state.drift,
            progress=1.0,
            recurrence_count=state.recurrence_count,
            created_at=state.created_at,
            updated_at=now,
            last_reactivated_at=state.last_reactivated_at,
            next_hint="goal closed by explicit closure signal",
            evidence=unique_recent((*state.evidence, evidence), limit=10),
            recent_summaries=state.recent_summaries,
            goal_type=state.goal_type,
            intent=state.intent,
            confidence=clamp01(state.confidence * 0.40),
            actionability=clamp01(state.actionability * 0.24),
            intervention_permission=0.0,
            coherence=clamp01(state.coherence * 0.28),
            anchors=state.anchors,
            stale_reason="closed",
        )

    def _match_score(self, state: GoalPersistenceState, candidate: GoalCandidate) -> float:
        if not state.summary or not candidate.summary:
            return 0.0
        return goal_match_score(
            state.summary,
            candidate.summary,
            left_anchors=state.anchors,
            right_anchors=candidate.anchors,
        )

    def _hint_for_kind(self, kind: str) -> str:
        if kind == GoalTextKind.CONCRETE_TASK.value:
            return "continue the concrete task only while current context still fits"
        if kind == GoalTextKind.DEBUG_THREAD.value:
            return "preserve the debugging thread; prefer direct fixes over motivational nudges"
        if kind == GoalTextKind.CORRECTION_THREAD.value:
            return "honor the correction; avoid repeating the failed frame"
        if kind == GoalTextKind.DESIGN_DIRECTION.value:
            return "keep the architectural direction as background, not an interruption trigger"
        if kind == GoalTextKind.RESEARCH_THREAD.value:
            return "keep the research thread quietly available"
        if kind == GoalTextKind.REVIEW_REQUEST.value:
            return "continue the review thread if similar context returns"
        return "preserve continuity quietly if context reconnects"

    # Hàm hỗ trợ trông như kế thừa được giữ lại để test/lời gọi private cũ không phát nổ.
    def _looks_goal_worthy(self, text: str) -> bool:
        return infer_goal_text_signal(text, channel="chat").should_persist

    def _has_closure(self, user_text: str, *, thought_stream_report) -> bool:
        return infer_closure_signal(user_text, thought_stream_report=thought_stream_report).closed

    def _matches(self, left: str, right: str) -> bool:
        return goal_match_score(left, right, left_anchors=extract_anchors(left), right_anchors=extract_anchors(right)) >= 0.58
