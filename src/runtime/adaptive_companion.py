# -*- coding: utf-8 -*-
"""ERIS V91 - Adaptive Companion Brain độc lập coding extension.

Mục tiêu của module này không phải chấm điểm con người và cũng không phải tạo
một "gia sư" thứ hai. Nó giữ một working model nhỏ để public writer biết:
- user đang nói rõ là mình học gì / làm gì;
- skill kỹ thuật nào ERIS *có đủ evidence* để bỏ qua phần nhập môn;
- turn này nên nói như companion, peer hay mentor nhẹ;
- khi nào có cơ hội trêu/mỉa, nhưng KHÔNG tự cấp quyền mỉa.

Boundary quan trọng:
- không đọc telemetry VS Code/GitPilot;
- không lưu raw transcript, source code hay terminal output;
- không sở hữu durable user memory: Living User Memory là source-of-truth cho learning/activity;
- self-report "tôi biết"/"tôi đang học" không bao giờ thành mastery evidence;
- relationship + affective voice vẫn là authority cuối cho mức thân mật/trêu.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


_SCHEMA = "eris.adaptive_companion.v91"
_FAST_MARKERS = (
    "làm luôn", "triển đi", "cứ làm", "fix luôn", "sửa luôn", "code đi", "apply đi", "patch đi",
)
# Chỉ những tín hiệu thể hiện *ý định học* mới mở mentor-style rõ.
# Hỏi "tại sao" hay "giải thích" bình thường chỉ là user muốn một câu trả lời;
# biến mọi câu hỏi thành tiết học sẽ làm Eris mất chất companion.
_EXPLICIT_LEARNING_MARKERS = (
    "chỉ tôi", "dạy tôi", "tôi đang học", "đang học", "muốn học", "học phần",
    "giải thích kỹ", "giải thích chi tiết", "giải thích sâu", "từ gốc",
    "teach me", "i am learning", "i'm learning", "i want to learn", "i need to learn",
    "help me learn", "help me study", "explain deeply", "in detail", "from first principles",
)
_EXPLANATION_REQUEST_MARKERS = (
    "giải thích", "tại sao", "vì sao", "explain", "why",
)
_HEATED_MARKERS = (
    "dcm", "đm", "dm ", "vcl", "wtf", "đéo", "mẹ nó", "lại lỗi", "bực", "khó chịu",
)


def _default_state_path() -> Path:
    explicit = str(os.getenv("ERIS_ADAPTIVE_COMPANION_STATE_PATH", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = str(os.getenv("ERIS_STATE_DIR", ".eris_state") or ".eris_state").strip()
    return Path(root).expanduser() / "adaptive_companion_v91.json"


def _compact(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()[: max(32, int(limit))]


def _key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.casefold()).strip(" .,:;!?-_")
    return hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[\wÀ-ỹ#.+-]+", str(value or "").casefold(), flags=re.UNICODE)
        if len(token) >= 3
    }


@dataclass(slots=True)
class CompanionTopic:
    topic_id: str
    label: str
    stage: str = "learning"  # exploring|learning; không có mastered ở layer này.
    confidence: float = 0.35
    evidence_count: int = 1
    last_seen_at: float = field(default_factory=time.time)
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CompanionActivity:
    activity_id: str
    label: str
    confidence: float = 0.45
    evidence_count: int = 1
    last_seen_at: float = field(default_factory=time.time)
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AdaptiveCompanionDirective:
    stance: str = "companion"  # companion|peer|teach_light|teach_guided|challenge_light
    teaching_permission: str = "none"  # none|answer_if_asked|light|guided|challenge
    explanation_depth: str = "normal"  # minimal|normal|deep
    teasing_opportunity: str = "none"  # none|light|earned
    teasing_is_authority: bool = False
    ask_user_to_reason: bool = False
    skip_basics: bool = False
    reason: str = "ordinary_companion_turn"
    active_learning_topics: tuple[str, ...] = ()
    active_activities: tuple[str, ...] = ()
    technical_assumptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    delivery_owner: str = "adaptive_companion"
    mentor_is_evidence_provider: bool = True
    rules: tuple[str, ...] = (
        "Companion first: không biến mọi task thành bài học.",
        "AdaptiveCompanion là delivery authority duy nhất; EngineeringMentor chỉ cung cấp evidence/learning hint.",
        "Teasing cue không phải authority; relationship/affective voice quyết định có nên trêu và trêu tới đâu.",
        "Skill assumption chỉ là giả thuyết có evidence, không phải nhãn trình độ cố định của user.",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "stance": self.stance,
            "teaching_permission": self.teaching_permission,
            "explanation_depth": self.explanation_depth,
            "teasing_opportunity": self.teasing_opportunity,
            "teasing_is_authority": self.teasing_is_authority,
            "ask_user_to_reason": self.ask_user_to_reason,
            "skip_basics": self.skip_basics,
            "reason": self.reason,
            "active_learning_topics": list(self.active_learning_topics),
            "active_activities": list(self.active_activities),
            "technical_assumptions": dict(self.technical_assumptions),
            "delivery_owner": self.delivery_owner,
            "mentor_is_evidence_provider": bool(self.mentor_is_evidence_provider),
            "authorities": {
                "delivery": "adaptive_companion",
                "technical_skill_evidence": "engineering_mentor",
                "technical_learning_evidence": "adaptive_engineering_learning",
                "goals_and_current_activities": "life_horizon_plus_explicit_conversation",
                "tone_and_teasing_permission": "relationship_plus_affective_voice",
            },
            "rules": list(self.rules),
        }


class AdaptiveCompanionService:
    """Working model conversation-first cho Eris V91."""

    def __init__(
        self,
        owner: Any | None = None,
        *,
        state_path: str | Path | None = None,
        logger: Any | None = None,
    ) -> None:
        self.owner = owner
        self.logger = logger or getattr(owner, "logger", None)
        self.state_path = Path(state_path).expanduser() if state_path is not None else _default_state_path()
        self._lock = threading.RLock()
        # Hot cache trong process để bridge khoảng trễ của background memory curator.
        # Đây KHÔNG phải durable memory. Dữ liệu bền vững thuộc Living User Memory.
        # File V91 cũ chỉ được đọc như migration fallback, tuyệt đối không ghi lại.
        self._learning_topics: dict[str, CompanionTopic] = {}
        self._activities: dict[str, CompanionActivity] = {}
        self._last_directive = AdaptiveCompanionDirective()
        self._last_calibration_intervention_turn = ""
        self._load()

    @staticmethod
    def _explicit_learning_fragments(text: str) -> tuple[str, ...]:
        fragments: list[str] = []
        patterns = (
            r"(?:tôi\s+)?(?:đang|muốn|định|cần)?\s*(?:học|tìm hiểu|luyện|learn(?:ing)?|study)\s+([^,!?;\n]+)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                value = _compact(match.group(1), 180).strip(" .,:;!?-")
                value = re.split(
                    r"\b(?:còn|nhưng|để\s+làm|và\s+đồ\s+án|và\s+đang\s+(?:làm|xây|code|phát\s+triển))\b",
                    value,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()
                if value and len(value) >= 2:
                    fragments.append(value)
        return tuple(dict.fromkeys(fragments))[:4]

    @staticmethod
    def _explicit_activity_fragments(text: str) -> tuple[str, ...]:
        fragments: list[str] = []
        patterns = (
            r"(?:đồ\s*án|project|dự\s*án)[^,.!?;]{0,80}?(?:đang\s+làm|là|:)\s*([^,!?;\n]+)",
            r"(?:tôi\s+)?đang\s+(?:làm|xây|code|phát\s+triển)\s+([^,!?;\n]+)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                value = _compact(match.group(1), 180).strip(" .,:;!?-")
                if value and len(value) >= 2:
                    fragments.append(value)
        return tuple(dict.fromkeys(fragments))[:4]

    def _upsert_learning(self, label: str, *, source_ref: str, now: float) -> None:
        clean = _compact(label, 180)
        if not clean:
            return
        topic_id = f"learning:{_key(clean)}"
        old = self._learning_topics.get(topic_id)
        refs = tuple(dict.fromkeys((*(old.source_refs if old else ()), source_ref)))[:8] if source_ref else (old.source_refs if old else ())
        self._learning_topics[topic_id] = CompanionTopic(
            topic_id=topic_id,
            label=clean,
            stage="learning",
            confidence=min(0.78, (old.confidence if old else 0.30) + 0.08),
            evidence_count=(old.evidence_count if old else 0) + 1,
            last_seen_at=now,
            source_refs=refs,
        )

    def _upsert_activity(self, label: str, *, source_ref: str, now: float) -> None:
        clean = _compact(label, 180)
        if not clean:
            return
        activity_id = f"activity:{_key(clean)}"
        old = self._activities.get(activity_id)
        refs = tuple(dict.fromkeys((*(old.source_refs if old else ()), source_ref)))[:8] if source_ref else (old.source_refs if old else ())
        self._activities[activity_id] = CompanionActivity(
            activity_id=activity_id,
            label=clean,
            confidence=min(0.82, (old.confidence if old else 0.38) + 0.08),
            evidence_count=(old.evidence_count if old else 0) + 1,
            last_seen_at=now,
            source_refs=refs,
        )

    def _living_user(self) -> Any | None:
        """Resolve Living User Memory mà không tạo dependency ngược.

        Companion chỉ đọc canonical profile. Curator/background pipeline mới là owner
        mutation; nhờ vậy response policy không lén trở thành một memory system thứ hai.
        """
        candidates = (
            getattr(self.owner, "living_user", None),
            getattr(getattr(self.owner, "memory_kernel", None), "living_user", None),
            getattr(getattr(self.owner, "memory_core", None), "living_user", None),
        )
        return next((item for item in candidates if item is not None), None)

    def _canonical_context_labels(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Đọc learning/activity đã được Living User Memory chấp nhận.

        Không đọc inferred mastery ở đây. Hai path này chỉ mô tả việc đang diễn ra.
        """
        living = self._living_user()
        store = getattr(living, "profile_store", None)
        query_prefix = getattr(store, "query_prefix", None)
        if not callable(query_prefix):
            return (), ()

        def labels(prefix: str) -> tuple[str, ...]:
            try:
                records = tuple(query_prefix(prefix) or ())
            except Exception:
                return ()
            ordered = sorted(records, key=lambda item: float(getattr(item, "updated_at", 0.0) or 0.0), reverse=True)
            out: list[str] = []
            for record in ordered:
                # canonical store đã lọc inactive/expired. Thêm trust floor nhỏ để
                # record rác confidence thấp không chui vào companion prompt.
                confidence = float(getattr(record, "confidence", 0.0) or 0.0)
                if confidence < 0.45:
                    continue
                value = _compact(getattr(record, "value", ""), 180)
                if value and value not in out:
                    out.append(value)
                if len(out) >= 6:
                    break
            return tuple(out)

        return labels("learning.active_topics"), labels("projects.current_activities")

    def _mentor_model(self) -> Mapping[str, Any]:
        mentor = getattr(self.owner, "engineering_mentor_service", None)
        snapshot_fn = getattr(mentor, "snapshot", None)
        if not callable(snapshot_fn):
            return {}
        try:
            snapshot = snapshot_fn() or {}
        except Exception:
            return {}
        if not isinstance(snapshot, Mapping):
            return {}
        model = snapshot.get("model")
        return model if isinstance(model, Mapping) else {}

    def _mentor_calibration(self) -> dict[str, Any]:
        """Đọc calibration cũ như một input cho Companion, không cho Mentor tự chọn giọng.

        Đây là điểm consolidation quan trọng: V85 vẫn sở hữu feedback/outcome,
        nhưng chỉ AdaptiveCompanion được phép biến feedback đó thành delivery policy.
        """
        mentor = getattr(self.owner, "engineering_mentor_service", None)
        calibration = getattr(mentor, "calibration", None)
        directive_fn = getattr(calibration, "directive", None)
        if not callable(directive_fn):
            return {}
        try:
            directive = directive_fn()
        except Exception:
            return {}
        return {
            "confidence_mode": str(getattr(directive, "confidence_mode", "normal") or "normal"),
            "reduce_proactive_friction": bool(getattr(directive, "reduce_proactive_friction", False)),
            "reason": str(getattr(directive, "reason", "") or ""),
        }

    def _technical_assumptions(self) -> dict[str, dict[str, Any]]:
        model = self._mentor_model()
        skills = model.get("skills") if isinstance(model.get("skills"), Mapping) else {}
        projection: dict[str, dict[str, Any]] = {}
        for skill_id, raw_any in dict(skills or {}).items():
            if not isinstance(raw_any, Mapping):
                continue
            level = float(raw_any.get("level", 0.5) or 0.5)
            confidence = float(raw_any.get("confidence", 0.0) or 0.0)
            evidence = int(raw_any.get("evidence_count", 0) or 0)
            independent = int(raw_any.get("independent_evidence_count", evidence) or evidence)
            if independent >= 3 and evidence >= 4 and confidence >= 0.45 and level >= 0.72:
                stage = "strong"
            elif independent >= 2 and evidence >= 2 and confidence >= 0.25 and level >= 0.60:
                stage = "comfortable"
            elif evidence >= 1 and confidence >= 0.12:
                stage = "developing"
            else:
                continue
            projection[str(skill_id)] = {
                "stage": stage,
                "confidence": round(confidence, 3),
                "evidence_count": evidence,
                "independent_evidence_count": independent,
            }
        return projection

    @staticmethod
    def _relevant_skills(text: str) -> tuple[str, ...]:
        low = text.casefold()
        out: list[str] = []
        if any(x in low for x in ("root cause", "nguyên nhân gốc", "lỗi nằm", "hypothesis", "target acquisition", "nguyên nhân")):
            out.append("debugging.root_cause_reasoning")
        if any(x in low for x in ("call flow", "call chain", "luồng gọi", "caller", "callee")):
            out.append("debugging.call_flow_tracing")
        if any(x in low for x in ("boundary", "kiến trúc", "architecture", "ownership", "layer", "dependency")):
            out.append("architecture.system_boundaries")
        if any(x in low for x in ("test", "kiểm chứng", "verify", "build xanh", "runtime")):
            out.append("verification.testing")
        if any(x in low for x in ("diff", "review", "patch")):
            out.append("verification.diff_review")
        return tuple(dict.fromkeys(out))

    def _life_horizon_activities(self) -> tuple[str, ...]:
        """Chiếu H1-H3 thành activity context; không biến goal thành learning mastery."""
        manager = getattr(self.owner, "life_horizon_manager", None)
        snapshot_fn = getattr(manager, "snapshot", None)
        if not callable(snapshot_fn):
            return ()
        try:
            snapshot = snapshot_fn()
        except Exception:
            return ()
        out: list[str] = []
        for goal in tuple(getattr(snapshot, "goals", ()) or ()):
            if str(getattr(goal, "status", "") or "") != "active":
                continue
            if str(getattr(goal, "level", "") or "") not in {"H1", "H2", "H3"}:
                continue
            title = _compact(getattr(goal, "title", ""), 180)
            description = _compact(getattr(goal, "description", ""), 220)
            label = title if not description else f"{title} — {description}"
            if label and label not in out:
                out.append(label)
            if len(out) >= 6:
                break
        return tuple(out)

    @staticmethod
    def _overlaps_learning(text: str, learning_topics: tuple[str, ...]) -> bool:
        turn_tokens = _tokens(text)
        if not turn_tokens:
            return False
        stop = {"dang", "học", "hoc", "tôi", "toi", "làm", "lam", "luôn", "luon", "bug", "cái", "cai", "này", "nay"}
        turn_tokens -= stop
        for label in learning_topics:
            topic_tokens = _tokens(label) - stop
            if topic_tokens and len(turn_tokens & topic_tokens) >= 1:
                return True
        return False

    def _active_labels(
        self,
        now: float,
        *,
        current_learning: tuple[str, ...] = (),
        current_activities: tuple[str, ...] = (),
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Merge context theo authority: current turn -> canonical memory -> hot cache.

        Hot cache chỉ tồn tại trong process và legacy V91 state chỉ là fallback đọc.
        Durable truth thuộc Living User Memory/Life Horizon.
        """
        recent_cutoff = now - 120 * 24 * 3600.0
        cached_learning = tuple(
            item.label for item in sorted(self._learning_topics.values(), key=lambda x: -x.last_seen_at)
            if item.last_seen_at >= recent_cutoff
        )
        cached_activities = tuple(
            item.label for item in sorted(self._activities.values(), key=lambda x: -x.last_seen_at)
            if item.last_seen_at >= recent_cutoff
        )
        canonical_learning, canonical_activities = self._canonical_context_labels()
        learning = tuple(dict.fromkeys((*current_learning, *canonical_learning, *cached_learning)))[:6]
        activities = tuple(dict.fromkeys((
            *current_activities,
            *canonical_activities,
            *cached_activities,
            *self._life_horizon_activities(),
        )))[:6]
        return learning, activities

    def _policy(
        self,
        text: str,
        *,
        now: float,
        current_learning: tuple[str, ...] = (),
        current_activities: tuple[str, ...] = (),
    ) -> AdaptiveCompanionDirective:
        low = text.casefold()
        assumptions = self._technical_assumptions()
        relevant = self._relevant_skills(text)
        strong_relevant = [skill for skill in relevant if assumptions.get(skill, {}).get("stage") == "strong"]
        fast = any(marker in low for marker in _FAST_MARKERS)
        explicit_learning = any(marker in low for marker in _EXPLICIT_LEARNING_MARKERS)
        explanation_request = any(marker in low for marker in _EXPLANATION_REQUEST_MARKERS)
        heated = any(marker in low for marker in _HEATED_MARKERS)
        learning, activities = self._active_labels(
            now, current_learning=current_learning, current_activities=current_activities
        )
        model = self._mentor_model()
        reasoning_risk = float(model.get("overreliance_risk", 0.0) or 0.0)
        calibration = self._mentor_calibration()
        cautious_skill_assumption = calibration.get("confidence_mode") == "cautious"
        reduce_proactive = bool(calibration.get("reduce_proactive_friction", False))

        teasing = "light" if heated else "none"
        common = dict(
            teasing_opportunity=teasing,
            teasing_is_authority=False,
            active_learning_topics=learning,
            active_activities=activities,
            technical_assumptions=assumptions,
        )

        if fast and not explicit_learning:
            # Chỉ challenge delegation khi ba điều cùng đúng: user đang chủ động
            # học chủ đề đó, turn hiện tại thực sự overlap, và mentor đã có risk
            # tích lũy đáng kể. Không biến mọi câu "làm luôn" thành bài kiểm tra.
            if reasoning_risk >= 0.55 and self._overlaps_learning(text, learning):
                return AdaptiveCompanionDirective(
                    stance="challenge_light",
                    teaching_permission="challenge",
                    explanation_depth="minimal",
                    ask_user_to_reason=True,
                    skip_basics=False,
                    reason="protect_reasoning_ownership_on_active_learning_topic",
                    **common,
                )
            return AdaptiveCompanionDirective(
                stance="companion",
                teaching_permission="none",
                explanation_depth="minimal",
                ask_user_to_reason=False,
                skip_basics=False,
                reason="fast_execution_is_not_a_learning_failure",
                **common,
            )

        # V85 correction/annoyance được gom về đây thay vì để EngineeringMentor
        # tự đổi role. Explicit learning vẫn thắng vì đó là ý muốn trực tiếp của user.
        if reduce_proactive and relevant and not explicit_learning:
            return AdaptiveCompanionDirective(
                stance="companion",
                teaching_permission="none",
                explanation_depth="normal",
                ask_user_to_reason=False,
                skip_basics=False,
                reason="mentor_calibration_reduced_proactive_teaching",
                **common,
            )

        # Nếu model vừa bị user sửa đủ nhiều để calibration chuyển cautious, không
        # dùng estimate cũ để tự tin nhảy sang peer/skip-basics. Companion vẫn có thể
        # trả lời kỹ thuật bình thường nhưng phải để chỗ cho user sửa Eris.
        if cautious_skill_assumption and strong_relevant and not explicit_learning:
            return AdaptiveCompanionDirective(
                stance="companion",
                teaching_permission="light",
                explanation_depth="normal",
                ask_user_to_reason=False,
                skip_basics=False,
                reason="calibration_requires_cautious_skill_assumption",
                **common,
            )

        # Khi evidence kỹ thuật đã mạnh, Eris nên tranh luận như peer thay vì
        # tua lại tutorial nhập môn — kể cả user đang nhờ review reasoning.
        if strong_relevant and not explicit_learning and not any(x in low for x in ("tôi chưa hiểu", "từ đầu", "cơ bản")):
            return AdaptiveCompanionDirective(
                stance="peer",
                teaching_permission="answer_if_asked",
                explanation_depth="minimal",
                ask_user_to_reason=False,
                skip_basics=True,
                reason="strong_evidence_supports_peer_stance",
                **common,
            )

        if explicit_learning:
            return AdaptiveCompanionDirective(
                stance="teach_guided",
                teaching_permission="guided",
                explanation_depth="deep" if any(x in low for x in ("kỹ", "chi tiết", "sâu", "deep", "chi tiết")) else "normal",
                ask_user_to_reason=False,
                skip_basics=False,
                reason="user_explicitly_wants_to_learn",
                **common,
            )

        if explanation_request:
            # "Tại sao?" là nhu cầu được trả lời, không mặc định là lời mời mở lớp.
            # Eris giải thích đủ ý như companion; chỉ chuyển mentor-style khi user
            # nói rõ đang học / muốn được dạy / muốn đào sâu.
            return AdaptiveCompanionDirective(
                stance="companion",
                teaching_permission="answer_if_asked",
                explanation_depth="normal",
                ask_user_to_reason=False,
                skip_basics=False,
                reason="user_requested_explanation_not_mentor_mode",
                **common,
            )

        return AdaptiveCompanionDirective(
            stance="companion",
            teaching_permission="light" if relevant else "none",
            explanation_depth="normal",
            ask_user_to_reason=False,
            skip_basics=False,
            reason="ordinary_companion_turn",
            **common,
        )

    def _register_delivery_intervention(
        self,
        directive: AdaptiveCompanionDirective,
        *,
        text: str,
        turn_id: str,
        now: float,
    ) -> None:
        """Commit delivery outcome qua owner evidence sau khi stance đã được chọn.

        Nếu Mentor mới có handshake `note_companion_delivery`, Companion gọi method
        đó cho MỌI stance để pending hint của turn được clear đúng. Fallback trực
        tiếp vào calibration chỉ dành cho stub/compatibility cũ.
        """
        mentor = getattr(self.owner, "engineering_mentor_service", None)
        commit = getattr(mentor, "note_companion_delivery", None)
        if callable(commit):
            try:
                commit(
                    turn_id=turn_id,
                    stance=directive.stance,
                    teaching_permission=directive.teaching_permission,
                    reason=directive.reason,
                    now=now,
                )
            except Exception:
                pass
            return

        if directive.stance not in {"teach_light", "teach_guided", "challenge_light"}:
            return
        calibration = getattr(mentor, "calibration", None)
        register = getattr(calibration, "register_intervention", None)
        if not callable(register):
            return
        ident = _compact(turn_id, 120)
        if ident and ident == self._last_calibration_intervention_turn:
            return
        if not ident:
            ident = "companion:" + _key(f"{text}|{now:.6f}")
        try:
            register(
                intervention_id=ident,
                mode={
                    "teach_light": "hint",
                    "teach_guided": "guide",
                    "challenge_light": "challenge",
                }.get(directive.stance, "discuss"),
                reason=directive.reason,
                focus_skills=self._relevant_skills(text),
                primary_role=directive.stance,
                proactive=directive.stance != "teach_guided",
                now=now,
            )
            self._last_calibration_intervention_turn = ident
        except Exception:
            return

    def observe_user_turn(
        self,
        user_text: str,
        *,
        turn_id: str = "",
        now: float | None = None,
    ) -> AdaptiveCompanionDirective:
        current = float(now or time.time())
        text = _compact(user_text, 1400)
        source_ref = _compact(turn_id, 120)
        with self._lock:
            current_learning = self._explicit_learning_fragments(text)
            current_activities = self._explicit_activity_fragments(text)
            # Giữ hot cache để turn kế tiếp không phụ thuộc tốc độ background curator.
            # Không persist ở đây: Living User Memory sẽ sở hữu durable copy.
            for label in current_learning:
                self._upsert_learning(label, source_ref=source_ref, now=current)
            for label in current_activities:
                self._upsert_activity(label, source_ref=source_ref, now=current)
            self._last_directive = self._policy(
                text,
                now=current,
                current_learning=current_learning,
                current_activities=current_activities,
            )
            self._register_delivery_intervention(
                self._last_directive, text=text, turn_id=source_ref, now=current
            )
            return self._last_directive

    def prompt_context(
        self,
        user_text: str,
        *,
        turn_id: str = "",
        now: float | None = None,
    ) -> str:
        directive = self.observe_user_turn(user_text, turn_id=turn_id, now=now)
        return "ADAPTIVE_COMPANION_V91:\n" + json.dumps(
            directive.to_dict(), ensure_ascii=False, separators=(",", ":")
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": _SCHEMA,
                "memory_authority": "living_user_memory",
                "cache_mode": "process_only_plus_legacy_read_fallback",
                "learning_topics": [item.to_dict() for item in sorted(self._learning_topics.values(), key=lambda x: -x.last_seen_at)],
                "activities": [item.to_dict() for item in sorted(self._activities.values(), key=lambda x: -x.last_seen_at)],
                "last_directive": self._last_directive.to_dict(),
            }

    def _load(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, Mapping):
            return
        for item in raw.get("learning_topics") or ():
            if not isinstance(item, Mapping):
                continue
            try:
                topic = CompanionTopic(
                    topic_id=str(item.get("topic_id") or ""),
                    label=_compact(item.get("label"), 180),
                    stage="learning" if str(item.get("stage") or "learning") not in {"exploring", "learning"} else str(item.get("stage")),
                    confidence=max(0.0, min(1.0, float(item.get("confidence", 0.35) or 0.35))),
                    evidence_count=max(1, int(item.get("evidence_count", 1) or 1)),
                    last_seen_at=float(item.get("last_seen_at", 0.0) or 0.0),
                    source_refs=tuple(str(x)[:120] for x in (item.get("source_refs") or ())[:8]),
                )
                if topic.topic_id and topic.label:
                    self._learning_topics[topic.topic_id] = topic
            except Exception:
                continue
        for item in raw.get("activities") or ():
            if not isinstance(item, Mapping):
                continue
            try:
                activity = CompanionActivity(
                    activity_id=str(item.get("activity_id") or ""),
                    label=_compact(item.get("label"), 180),
                    confidence=max(0.0, min(1.0, float(item.get("confidence", 0.45) or 0.45))),
                    evidence_count=max(1, int(item.get("evidence_count", 1) or 1)),
                    last_seen_at=float(item.get("last_seen_at", 0.0) or 0.0),
                    source_refs=tuple(str(x)[:120] for x in (item.get("source_refs") or ())[:8]),
                )
                if activity.activity_id and activity.label:
                    self._activities[activity.activity_id] = activity
            except Exception:
                continue

    def _persist_locked(self) -> None:
        """Legacy no-op: AdaptiveCompanion không còn sở hữu durable user memory.

        Giữ method để code/test cũ không crash nếu còn gọi. File V91 cũ được đọc
        ở ``_load`` như migration fallback nhưng không bao giờ bị rewrite.
        """
        return None


def ensure_adaptive_companion_service(owner: Any) -> AdaptiveCompanionService:
    """Lấy companion service theo owner, không tạo dependency sang coding extension.

    Dùng lazy owner-scoped singleton giống các runtime khác của Eris. Nhờ vậy
    Desktop có companion kể cả khi VS Code/GitPilot chưa từng chạy.
    """
    existing = getattr(owner, "adaptive_companion_service", None)
    if isinstance(existing, AdaptiveCompanionService):
        return existing
    service = AdaptiveCompanionService(owner=owner, logger=getattr(owner, "logger", None))
    try:
        setattr(owner, "adaptive_companion_service", service)
    except Exception:
        pass
    return service


__all__ = [
    "AdaptiveCompanionDirective",
    "AdaptiveCompanionService",
    "CompanionActivity",
    "CompanionTopic",
    "ensure_adaptive_companion_service",
]
