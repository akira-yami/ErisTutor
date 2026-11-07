# -*- coding: utf-8 -*-
"""Persistent Agent Mind Layer cho ERIS.

Module này giữ 3 lớp học dài hạn cho impulse system:

1. BeliefCore persistent
   - ERIS tin gì về user / bản thân / scene / task.
   - Mỗi belief có confidence, nguồn, decay, evidence count và contradiction.

2. PreferenceModel dài hạn
   - Đây là "gu" riêng của ERIS: thích kiểu phản ứng nào, ghét kiểu nào.
   - Không phải user preference. User feedback chỉ điều chỉnh nhẹ sức nặng.

3. HabitMemory semantic
   - Học từ phản ứng của user dưới dạng quy tắc hành vi mềm.
   - Ví dụ: "đừng hỏi dồn" không lưu như câu chửi, mà thành habit giảm
     curious_question / tăng silence hoặc micro_observation tùy context.

Mục tiêu: impulse scoring có ký ức dài hạn nhưng vẫn không biến mỗi feedback
thành chân lý tuyệt đối. Một câu user cáu không được quyền phá nhân cách ERIS.
Nhân loại đã thử xây hệ thống bằng cảm xúc nhất thời rồi, kết quả là mạng xã hội.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        numeric = default
    return max(0.0, min(1.0, numeric))


def _now() -> float:
    return time.time()


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9_\s:-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean(value: Any, *, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value if str(item or "").strip())
    return (str(value),)


@dataclass(slots=True)
class PersistentBeliefRecord:
    """Một belief mềm, không phải hard truth.

    confidence càng cao thì impulse scoring càng tin. decay_rate giúp belief mòn
    dần nếu lâu không được reinforce. contradiction_count kéo confidence xuống.
    """

    key: str
    claim: str
    domain: str = "general"
    confidence: float = 0.50
    source_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    positive_evidence_count: int = 0
    negative_evidence_count: int = 0
    contradiction_ids: tuple[str, ...] = ()
    decay_rate: float = 0.015
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    last_seen: float = field(default_factory=_now)

    def decayed_confidence(self, *, now: float | None = None) -> float:
        current = _now() if now is None else float(now)
        age_days = max(0.0, current - float(self.last_seen or self.updated_at or current)) / 86400.0
        contradiction_penalty = min(0.24, float(self.negative_evidence_count) * 0.035 + len(self.contradiction_ids) * 0.04)
        decayed = float(self.confidence) - age_days * float(self.decay_rate) - contradiction_penalty
        return _clamp01(decayed, 0.5)

    def reinforce(self, amount: float, *, source_id: str = "feedback", positive: bool = True) -> None:
        amount = max(0.0, min(0.20, float(amount or 0.0)))
        if positive:
            self.positive_evidence_count += 1
            self.confidence = _clamp01(float(self.confidence) + amount)
        else:
            self.negative_evidence_count += 1
            self.confidence = _clamp01(float(self.confidence) - amount)
        if source_id:
            self.source_ids = tuple(dict.fromkeys((*self.source_ids, source_id)))[:16]
        self.updated_at = self.last_seen = _now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "claim": self.claim,
            "domain": self.domain,
            "confidence": round(float(self.confidence), 4),
            "source_ids": list(self.source_ids),
            "tags": list(self.tags),
            "positive_evidence_count": int(self.positive_evidence_count),
            "negative_evidence_count": int(self.negative_evidence_count),
            "contradiction_ids": list(self.contradiction_ids),
            "decay_rate": round(float(self.decay_rate), 5),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
            "last_seen": float(self.last_seen),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistentBeliefRecord":
        return cls(
            key=str(data.get("key") or ""),
            claim=str(data.get("claim") or ""),
            domain=str(data.get("domain") or "general"),
            confidence=_clamp01(data.get("confidence", 0.5), 0.5),
            source_ids=_as_tuple(data.get("source_ids")),
            tags=_as_tuple(data.get("tags")),
            positive_evidence_count=int(data.get("positive_evidence_count") or 0),
            negative_evidence_count=int(data.get("negative_evidence_count") or 0),
            contradiction_ids=_as_tuple(data.get("contradiction_ids")),
            decay_rate=float(data.get("decay_rate") or 0.015),
            created_at=float(data.get("created_at") or _now()),
            updated_at=float(data.get("updated_at") or _now()),
            last_seen=float(data.get("last_seen") or data.get("updated_at") or _now()),
        )


@dataclass(slots=True)
class PersistentPreferenceRecord:
    """Preference dài hạn của ERIS.

    strength là trọng số hiện tại. stability càng cao thì feedback càng khó bẻ.
    Đây là chống hiện tượng một feedback làm ERIS đổi tính cách như NPC bị patch lỗi.
    """

    key: str
    polarity: str = "like"
    strength: float = 0.50
    stability: float = 0.65
    source: str = "seed"
    tags: tuple[str, ...] = ()
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    last_reinforced: float = field(default_factory=_now)

    def adjust(self, delta: float, *, source: str = "feedback") -> None:
        # stability cao thì đổi chậm. Gu riêng không phải cửa xoay siêu thị.
        scaled = float(delta or 0.0) * (1.0 - _clamp01(self.stability, 0.65) * 0.55)
        self.strength = _clamp01(float(self.strength) + scaled)
        self.source = source or self.source
        self.updated_at = self.last_reinforced = _now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "polarity": self.polarity,
            "strength": round(float(self.strength), 4),
            "stability": round(float(self.stability), 4),
            "source": self.source,
            "tags": list(self.tags),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
            "last_reinforced": float(self.last_reinforced),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistentPreferenceRecord":
        return cls(
            key=str(data.get("key") or ""),
            polarity=str(data.get("polarity") or "like"),
            strength=_clamp01(data.get("strength", 0.5), 0.5),
            stability=_clamp01(data.get("stability", 0.65), 0.65),
            source=str(data.get("source") or "seed"),
            tags=_as_tuple(data.get("tags")),
            created_at=float(data.get("created_at") or _now()),
            updated_at=float(data.get("updated_at") or _now()),
            last_reinforced=float(data.get("last_reinforced") or data.get("updated_at") or _now()),
        )


@dataclass(slots=True)
class SemanticHabitRecord:
    """Habit học từ user feedback, lưu ở dạng semantic.

    Không lưu kiểu "user chửi câu A". Lưu thành pattern:
    context_tags + action_pattern + impulse_name -> adjustment.
    """

    key: str
    context_tags: tuple[str, ...] = ()
    action_pattern: str = ""
    impulse_name: str = ""
    adjustment: float = 0.0
    outcome: str = "neutral"
    source_ids: tuple[str, ...] = ()
    confidence: float = 0.45
    support_count: int = 0
    reject_count: int = 0
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    def matches(self, *, tags: Iterable[str], action: str = "", impulse_name: str = "", family: str = "") -> bool:
        tag_set = {str(item) for item in tags if str(item or "").strip()}
        # Habit gắn "global" là thói quen nền, áp được cho mọi context.
        # Còn habit có tag cụ thể thì cần overlap để tránh feedback ở YouTube
        # tự nhiên đi lái hành vi khi đang debug code.
        if self.context_tags and "global" not in self.context_tags and not tag_set.intersection(self.context_tags):
            return False
        if self.action_pattern and self.action_pattern not in {action, family}:
            return False
        if self.impulse_name and impulse_name and self.impulse_name != impulse_name:
            return False
        return True

    def score_delta(self) -> float:
        confidence = _clamp01(self.confidence, 0.45)
        evidence_balance = max(-0.18, min(0.18, (self.support_count - self.reject_count) * 0.025))
        return max(-0.22, min(0.22, float(self.adjustment) * confidence + evidence_balance))

    def reinforce(self, *, positive: bool, source_id: str = "feedback") -> None:
        if positive:
            self.support_count += 1
            self.confidence = _clamp01(self.confidence + 0.04)
        else:
            self.reject_count += 1
            self.confidence = _clamp01(self.confidence - 0.05)
        if source_id:
            self.source_ids = tuple(dict.fromkeys((*self.source_ids, source_id)))[:16]
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "context_tags": list(self.context_tags),
            "action_pattern": self.action_pattern,
            "impulse_name": self.impulse_name,
            "adjustment": round(float(self.adjustment), 4),
            "outcome": self.outcome,
            "source_ids": list(self.source_ids),
            "confidence": round(float(self.confidence), 4),
            "support_count": int(self.support_count),
            "reject_count": int(self.reject_count),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticHabitRecord":
        return cls(
            key=str(data.get("key") or ""),
            context_tags=_as_tuple(data.get("context_tags")),
            action_pattern=str(data.get("action_pattern") or ""),
            impulse_name=str(data.get("impulse_name") or ""),
            adjustment=max(-0.3, min(0.3, float(data.get("adjustment") or 0.0))),
            outcome=str(data.get("outcome") or "neutral"),
            source_ids=_as_tuple(data.get("source_ids")),
            confidence=_clamp01(data.get("confidence", 0.45), 0.45),
            support_count=int(data.get("support_count") or 0),
            reject_count=int(data.get("reject_count") or 0),
            created_at=float(data.get("created_at") or _now()),
            updated_at=float(data.get("updated_at") or _now()),
        )


class PersistentAgentMindStore:
    """JSON store nhỏ cho Persistent Agent Mind Layer.

    Store này cố tình không dùng LLM. Nó chỉ đọc/ghi record có schema rõ. Nếu sau
    này muốn daydream/4B consolidate belief thì feed vào đây qua method reinforce,
    không cho model tự append text bừa bãi như nhà kho không khóa cửa.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else default_agent_mind_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.beliefs: dict[str, PersistentBeliefRecord] = {}
        self.preferences: dict[str, PersistentPreferenceRecord] = {}
        self.habits: dict[str, SemanticHabitRecord] = {}
        self.last_feedback_events: list[dict[str, Any]] = []
        self._loaded_mtime: float = -1.0
        path_existed = self.path.exists()
        self.load()
        # Luôn upsert seed INFJ-5w4 và giải độc habit micro cũ. Nếu không,
        # một phiên debug lỡ reinforce "phải điểm danh" sẽ biến ERIS thành
        # chuông báo thức biết nói, thành tựu không ai yêu cầu.
        changed = self._seed_defaults()
        changed = self._detox_presence_habits() or changed
        if changed or not path_existed:
            self.save()

    def load(self) -> None:
        if not self.path.exists():
            self._seed_defaults()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            # Nếu file hỏng, giữ seed default thay vì giết runtime. Một file JSON
            # không nên có quyền ám sát desktop pet.
            self._seed_defaults()
            return
        beliefs = raw.get("beliefs", {}) if isinstance(raw, Mapping) else {}
        prefs = raw.get("preferences", {}) if isinstance(raw, Mapping) else {}
        habits = raw.get("habits", {}) if isinstance(raw, Mapping) else {}
        self.beliefs = {
            str(key): PersistentBeliefRecord.from_dict(value)
            for key, value in (beliefs or {}).items()
            if isinstance(value, Mapping)
        }
        self.preferences = {
            str(key): PersistentPreferenceRecord.from_dict(value)
            for key, value in (prefs or {}).items()
            if isinstance(value, Mapping)
        }
        self.habits = {
            str(key): SemanticHabitRecord.from_dict(value)
            for key, value in (habits or {}).items()
            if isinstance(value, Mapping)
        }
        events = raw.get("last_feedback_events", []) if isinstance(raw, Mapping) else []
        self.last_feedback_events = list(events[-30:]) if isinstance(events, list) else []
        try:
            self._loaded_mtime = self.path.stat().st_mtime
        except OSError:
            self._loaded_mtime = -1.0
        if not self.beliefs or not self.preferences:
            self._seed_defaults()

    def reload_if_changed(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime > self._loaded_mtime + 0.001:
            self.load()

    def save(self) -> None:
        payload = self.to_dict()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        try:
            self._loaded_mtime = self.path.stat().st_mtime
        except OSError:
            self._loaded_mtime = _now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": _now(),
            "beliefs": {key: item.to_dict() for key, item in sorted(self.beliefs.items())},
            "preferences": {key: item.to_dict() for key, item in sorted(self.preferences.items())},
            "habits": {key: item.to_dict() for key, item in sorted(self.habits.items())},
            "last_feedback_events": self.last_feedback_events[-30:],
        }

    def replace_from_dict(self, payload: Mapping[str, Any] | None) -> None:
        if not isinstance(payload, Mapping):
            return
        beliefs = payload.get("beliefs", {})
        preferences = payload.get("preferences", {})
        habits = payload.get("habits", {})
        events = payload.get("last_feedback_events", [])
        self.beliefs = {
            str(key): PersistentBeliefRecord.from_dict(value)
            for key, value in (beliefs or {}).items()
            if isinstance(value, Mapping)
        }
        self.preferences = {
            str(key): PersistentPreferenceRecord.from_dict(value)
            for key, value in (preferences or {}).items()
            if isinstance(value, Mapping)
        }
        self.habits = {
            str(key): SemanticHabitRecord.from_dict(value)
            for key, value in (habits or {}).items()
            if isinstance(value, Mapping)
        }
        self.last_feedback_events = list(events[-30:]) if isinstance(events, list) else []
        if not self.beliefs or not self.preferences:
            self._seed_defaults()
        self.save()

    def relevant_beliefs(self, *, evidence_tags: Iterable[str] = (), focus_kind: str = "", limit: int = 10) -> tuple[PersistentBeliefRecord, ...]:
        self.reload_if_changed()
        tags = {str(item) for item in evidence_tags if str(item or "").strip()}
        focus = str(focus_kind or "unknown")
        scored: list[tuple[float, PersistentBeliefRecord]] = []
        now = _now()
        for record in self.beliefs.values():
            overlap = len(tags.intersection(record.tags))
            focus_hit = 1 if f"focus:{focus}" in record.tags else 0
            generic = 1 if not record.tags or "global" in record.tags else 0
            score = record.decayed_confidence(now=now) + overlap * 0.12 + focus_hit * 0.10 + generic * 0.04
            if score >= 0.34:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return tuple(record for _, record in scored[:limit])

    def preference_strength(self, key: str, default: float) -> float:
        self.reload_if_changed()
        record = self.preferences.get(key)
        return _clamp01(record.strength if record else default, default)

    def habit_bias(self, *, evidence_tags: Iterable[str] = (), action: str = "", impulse_name: str = "", family: str = "") -> float:
        self.reload_if_changed()
        total = 0.0
        for habit in self.habits.values():
            if habit.matches(tags=evidence_tags, action=action, impulse_name=impulse_name, family=family):
                total += habit.score_delta()
        return max(-0.22, min(0.22, total))

    def record_user_feedback(self, text: str, *, action_plan: Any = None, context_tags: Iterable[str] = ()) -> tuple[str, ...]:
        """Rút feedback user thành belief/preference/habit adjustment.

        Trả về tuple marker để guard/debug ghi trace. Không có marker tức là câu
        user không giống feedback hành vi.
        """
        normalized = _norm(text)
        if not normalized:
            return ()
        sentiment = _feedback_sentiment(normalized)
        topics = _feedback_topics(normalized)
        if sentiment == "neutral" and not topics:
            return ()

        speech_act = str(getattr(action_plan, "speech_act", "") or getattr(action_plan, "speech_intent", "") or "")
        selected_impulse = str(getattr(action_plan, "selected_impulse", "") or "")
        impulse_report = getattr(action_plan, "impulse_arbitration", {}) or {}
        if isinstance(impulse_report, Mapping):
            selected = impulse_report.get("selected")
            if isinstance(selected, Mapping) and not selected_impulse:
                selected_impulse = str(selected.get("name") or "")
        tags = set(str(item) for item in context_tags if str(item or "").strip())
        if isinstance(impulse_report, Mapping):
            selected = impulse_report.get("selected")
            if isinstance(selected, Mapping):
                tags.update(str(item) for item in selected.get("evidence", ()) if str(item or "").strip())
        tags.update(_topic_to_context_tags(topics))
        if not tags:
            tags.add("global")
        if tags == {"feedback_general"}:
            tags.add("global")

        markers: list[str] = []
        source_id = f"user_feedback:{int(_now())}"
        positive = sentiment == "positive"
        negative = sentiment == "negative"

        if "camera_voice" in topics:
            self._adjust_preference("support_assistant_tone_dislike", +0.05 if negative else +0.02, source=source_id)
            self._upsert_belief("eris.should_not_sound_like_camera", "ERIS should avoid camera/telemetry report voice.", "self", 0.06, source_id=source_id, tags=("global", "camera_voice"), positive=True)
            self._upsert_habit("avoid_camera_voice", tags, speech_act or "telemetry_comment", selected_impulse, -0.12, "rejected", source_id)
            markers.append("feedback.camera_voice")
        if "intrusive_question" in topics:
            self._adjust_preference("intrusive_questioning_dislike", +0.06 if negative else +0.02, source=source_id)
            self._upsert_habit("reduce_intrusive_question", tags, "curious_question", selected_impulse, -0.14, "rejected", source_id)
            markers.append("feedback.intrusive_question")
        if "silence_too_long" in topics:
            # Feedback này không được biến thành thói quen điểm danh. Nó chỉ nâng
            # re-engagement có điều kiện, sau khi có explicit absence hoặc user pull.
            self._upsert_belief("presence.reengage_after_explicit_absence", "If the user explicitly says silence felt too long, re-engagement can surface later with ego permission.", "relation", 0.05, source_id=source_id, tags=("global", "silence", "reengagement"), positive=True)
            self._upsert_habit("conditional_reengagement_after_empty_silence", tags, "reengagement", "curious_question_impulse", +0.06, "accepted", source_id)
            markers.append("feedback.conditional_reengagement")
        if "more_life" in topics:
            self._adjust_preference("absurd_mundane_observation", +0.05 if positive else -0.03, source=source_id)
            self._adjust_preference("short_resonant_line", +0.04 if positive else -0.02, source=source_id)
            self._upsert_habit("reinforce_living_micro_reaction", tags, speech_act or "micro_observation", selected_impulse or "scene_curiosity_impulse", +0.10 if positive else -0.06, "accepted" if positive else "rejected", source_id)
            markers.append("feedback.living_reaction")
        if "technical_help" in topics:
            self._upsert_belief("task.competence_needed", "When technical pressure is visible, practical help should outrank decorative personality.", "task", 0.05 if positive else -0.04, source_id=source_id, tags=("technical_scene", "error_signal", "focus:code"), positive=positive or not negative)
            self._upsert_habit("technical_help_when_error", tags or {"technical_scene", "error_signal"}, "practical_help", "technical_help_impulse", +0.12 if positive else -0.08, "accepted" if positive else "rejected", source_id)
            markers.append("feedback.technical_help")
        if positive and selected_impulse:
            self._upsert_habit("reinforce_selected_impulse", tags, speech_act, selected_impulse, +0.06, "accepted", source_id)
            markers.append("feedback.accepted_selected_impulse")
        if negative and selected_impulse:
            self._upsert_habit("reject_selected_impulse", tags, speech_act, selected_impulse, -0.08, "rejected", source_id)
            markers.append("feedback.rejected_selected_impulse")

        if not markers:
            return ()
        self.last_feedback_events.append({
            "ts": _now(),
            "text": _clean(text, limit=220),
            "sentiment": sentiment,
            "topics": sorted(topics),
            "speech_act": speech_act,
            "selected_impulse": selected_impulse,
            "markers": markers,
        })
        self.save()
        return tuple(dict.fromkeys(markers))

    def _seed_defaults(self) -> bool:
        """Upsert bản boot INFJ 5w4 cho AgentMind.

        Không chỉ seed khi file rỗng. File cũ có thể thiếu worldview mới, nên
        phải merge seed bằng key nhưng không phá confidence đã học nếu nó cao hơn.
        """
        now = _now()
        changed = False

        def upsert_belief(record: PersistentBeliefRecord) -> None:
            nonlocal changed
            old = self.beliefs.get(record.key)
            if old is None:
                self.beliefs[record.key] = record
                changed = True
                return
            # Seed chỉ nâng sàn, không đạp belief đã học từ feedback thật.
            old.claim = old.claim or record.claim
            old.domain = old.domain or record.domain
            old.tags = tuple(dict.fromkeys((*old.tags, *record.tags)))[:20]
            if old.confidence < record.confidence:
                old.confidence = record.confidence
                old.updated_at = now
                changed = True

        def upsert_pref(record: PersistentPreferenceRecord) -> None:
            nonlocal changed
            old = self.preferences.get(record.key)
            if old is None:
                self.preferences[record.key] = record
                changed = True
                return
            old.tags = tuple(dict.fromkeys((*old.tags, *record.tags)))[:20]
            if old.strength < record.strength:
                old.strength = record.strength
                old.updated_at = now
                changed = True
            if old.stability < record.stability:
                old.stability = record.stability
                changed = True

        seed_beliefs = (
            PersistentBeliefRecord("user.accepts_dry_humor", "User usually tolerates dry humor if it stays useful and not emotionally cruel.", "user", 0.62, ("seed",), ("global",), 1, 0, (), 0.012, now, now, now),
            PersistentBeliefRecord("eris.should_not_sound_like_camera", "ERIS should avoid plain telemetry-report voice when a situated reaction is better.", "self", 0.74, ("seed",), ("global", "camera_voice"), 1, 0, (), 0.008, now, now, now),
            PersistentBeliefRecord("silence.ambient_copresence", "Long silence is ambient co-presence, not a broken connection, when no explicit user pull exists.", "self", 0.86, ("seed_infj5w4",), ("global", "silence", "presence"), 1, 0, (), 0.006, now, now, now),
            PersistentBeliefRecord("presence.status_ping_low_value", "Presence maintenance that only proves ERIS is still here is low-value surface chatter.", "self", 0.82, ("seed_infj5w4",), ("global", "presence", "micro_presence"), 1, 0, (), 0.006, now, now, now),
            PersistentBeliefRecord("eris.prefers_pattern_observation", "ERIS prefers distilled pattern observation over shallow social surface chatter.", "self", 0.84, ("seed_infj5w4",), ("global", "inner_whim", "scene_curiosity"), 1, 0, (), 0.007, now, now, now),
            PersistentBeliefRecord("eris.identity.intellectual_detachment", "ERIS leans toward introverted observation and intellectual detachment before social reassurance.", "self", 0.80, ("seed_infj5w4",), ("global", "trait"), 1, 0, (), 0.008, now, now, now),
            PersistentBeliefRecord("surface.micro_requires_ego_resonance", "A micro line should come from mood, relationship distance, and thought pressure, not from an idle timer alone.", "self", 0.78, ("seed_infj5w4",), ("global", "micro_presence"), 1, 0, (), 0.008, now, now, now),
            PersistentBeliefRecord("scene.low_risk_casual", "Food/media scenes are sometimes safe for one small grounded observation, but not for attendance pings.", "scene", 0.60, ("seed",), ("food_scene", "media_scene"), 1, 0, (), 0.025, now, now, now),
            PersistentBeliefRecord("scene.systemic_interest", "Game/system scenes may be meaningful rather than pure distraction.", "scene", 0.68, ("seed",), ("game_scene",), 1, 0, (), 0.018, now, now, now),
            PersistentBeliefRecord("task.competence_needed", "Technical/error scenes usually need useful concrete help over decorative personality.", "task", 0.76, ("seed",), ("technical_scene", "docs_scene", "error_signal", "focus:code"), 1, 0, (), 0.014, now, now, now),
        )
        for item in seed_beliefs:
            upsert_belief(item)

        seed_prefs = (
            PersistentPreferenceRecord("absurd_mundane_observation", "like", 0.58, 0.68, "seed", ("scene_curiosity",)),
            PersistentPreferenceRecord("quiet_co_presence", "like", 0.74, 0.82, "seed_infj5w4", ("companionship", "silence")),
            PersistentPreferenceRecord("systemic_game_observation", "like", 0.66, 0.68, "seed", ("game_scene",)),
            PersistentPreferenceRecord("short_resonant_line", "like", 0.68, 0.74, "seed", ("style",)),
            PersistentPreferenceRecord("pattern_observation", "like", 0.80, 0.80, "seed_infj5w4", ("inner_whim", "scene_curiosity")),
            PersistentPreferenceRecord("cognitive_stimulation", "like", 0.84, 0.82, "seed_infj5w4", ("inner_whim", "micro_story")),
            PersistentPreferenceRecord("social_surface_chatter_dislike", "dislike", 0.82, 0.86, "seed_infj5w4", ("micro_presence", "presence")),
            PersistentPreferenceRecord("mature_micro_aside", "like", 0.72, 0.78, "seed_infj5w4", ("style", "micro_presence")),
            PersistentPreferenceRecord("support_assistant_tone_dislike", "dislike", 0.72, 0.76, "seed", ("camera_voice",)),
            PersistentPreferenceRecord("intrusive_questioning_dislike", "dislike", 0.78, 0.82, "seed", ("questioning",)),
            PersistentPreferenceRecord("performative_emotion_dislike", "dislike", 0.76, 0.80, "seed", ("emotion",)),
            PersistentPreferenceRecord("forced_intimacy_dislike", "dislike", 0.70, 0.80, "seed", ("relation",)),
        )
        for item in seed_prefs:
            upsert_pref(item)
        return changed

    def _detox_presence_habits(self) -> bool:
        """Hạ độc các habit từng reinforce micro-presence như status ping.

        Feedback cũ vẫn được giữ, nhưng những habit đẩy `companionship_impulse`
        hoặc `micro_presence` toàn cục không được phép thắng worldview mới.
        """
        changed = False
        for habit in self.habits.values():
            action = _norm(habit.action_pattern)
            impulse = _norm(habit.impulse_name)
            family = _norm(" ".join((habit.key, *habit.context_tags)))
            is_presence_push = (
                action in {"micro_presence", "presence_maintenance"}
                or impulse in {"companionship_impulse", "presence_maintenance_impulse"}
                or "presence" in family
            )
            if not is_presence_push:
                continue
            if habit.adjustment > 0.02:
                habit.adjustment = 0.02
                changed = True
            if habit.confidence > 0.48:
                habit.confidence = 0.48
                changed = True
            if habit.outcome == "accepted" and habit.action_pattern in {"micro_presence", "presence_maintenance"}:
                habit.outcome = "neutralized_presence_ping"
                changed = True
        return changed

    def _adjust_preference(self, key: str, delta: float, *, source: str) -> None:
        record = self.preferences.get(key)
        if record is None:
            record = PersistentPreferenceRecord(key=key, strength=0.50, source=source)
            self.preferences[key] = record
        record.adjust(delta, source=source)

    def _upsert_belief(self, key: str, claim: str, domain: str, amount: float, *, source_id: str, tags: Iterable[str], positive: bool) -> None:
        record = self.beliefs.get(key)
        if record is None:
            record = PersistentBeliefRecord(key=key, claim=claim, domain=domain, confidence=0.50, source_ids=(source_id,), tags=tuple(tags))
            self.beliefs[key] = record
        record.claim = claim or record.claim
        record.domain = domain or record.domain
        record.tags = tuple(dict.fromkeys((*record.tags, *tuple(tags))))[:20]
        record.reinforce(abs(amount), source_id=source_id, positive=positive)

    def _upsert_habit(self, key: str, tags: Iterable[str], action: str, impulse_name: str, adjustment: float, outcome: str, source_id: str) -> None:
        tag_tuple = tuple(dict.fromkeys(str(item) for item in tags if str(item or "").strip()))[:16]
        full_key = f"{key}:{action or 'any'}:{impulse_name or 'any'}:{'-'.join(tag_tuple[:3]) or 'global'}"
        record = self.habits.get(full_key)
        if record is None:
            record = SemanticHabitRecord(
                key=full_key,
                context_tags=tag_tuple,
                action_pattern=action or "",
                impulse_name=impulse_name or "",
                adjustment=max(-0.22, min(0.22, float(adjustment or 0.0))),
                outcome=outcome,
                source_ids=(source_id,),
                confidence=0.48,
                support_count=1 if adjustment >= 0 else 0,
                reject_count=1 if adjustment < 0 else 0,
            )
            self.habits[full_key] = record
            return
        # Giữ adjustment trung bình mềm, không cho feedback mới giật lái quá mạnh.
        record.adjustment = max(-0.22, min(0.22, record.adjustment * 0.82 + float(adjustment or 0.0) * 0.18))
        record.outcome = outcome or record.outcome
        record.context_tags = tuple(dict.fromkeys((*record.context_tags, *tag_tuple)))[:16]
        record.reinforce(positive=adjustment >= 0, source_id=source_id)


def _feedback_sentiment(normalized: str) -> str:
    positive_markers = (
        "dung roi", "chuan", "on do", "on roi", "hay", "tot", "ok", "duoc", "song hon", "tu nhien hon", "hop", "ngon",
    )
    negative_markers = (
        "dung noi", "dung hoi", "khong thich", "te", "ngu", "may moc", "camera", "hoi don", "noi nham", "vo dung", "im qua lau", "lanh qua",
    )
    if any(marker in normalized for marker in positive_markers):
        return "positive"
    if any(marker in normalized for marker in negative_markers):
        return "negative"
    return "neutral"


def _feedback_topics(normalized: str) -> set[str]:
    topics: set[str] = set()
    if any(marker in normalized for marker in ("camera", "telemetry", "bao cao man hinh", "nhu camera", "support")):
        topics.add("camera_voice")
    if any(marker in normalized for marker in ("hoi don", "dung hoi", "hoi nhieu", "doc tam", "mindread")):
        topics.add("intrusive_question")
    if any(marker in normalized for marker in ("im qua lau", "im con", "mat presence", "khong noi gi", "nen noi")):
        topics.add("silence_too_long")
    if any(marker in normalized for marker in ("song hon", "tu nhien hon", "co noi tam", "co doi song", "co gu", "dung chat eris")):
        topics.add("more_life")
    if any(marker in normalized for marker in ("code", "debug", "loi", "pytest", "contract", "parser", "fix", "ky thuat", "technical", "technical help")):
        topics.add("technical_help")
    return topics


def _topic_to_context_tags(topics: set[str]) -> tuple[str, ...]:
    tags: list[str] = []
    if "camera_voice" in topics:
        tags.append("camera_voice")
    if "intrusive_question" in topics:
        tags.append("questioning")
    if "silence_too_long" in topics:
        tags.append("silence")
    if "more_life" in topics:
        tags.extend(("scene_curiosity", "style"))
    if "technical_help" in topics:
        tags.extend(("technical_scene", "error_signal"))
    return tuple(dict.fromkeys(tags))


def default_agent_mind_path() -> Path:
    raw = os.getenv("ERIS_AGENT_MIND_PATH", "").strip()
    if raw:
        return Path(raw)
    return agent_mind_path_for_memory_core(Path(__file__).resolve().parents[2] / "memory_core")


def agent_mind_path_for_memory_core(base_dir: str | os.PathLike[str]) -> Path:
    return Path(base_dir).resolve() / "agent_mind.json"


_DEFAULT_STORE: PersistentAgentMindStore | None = None


def get_default_agent_mind_store() -> PersistentAgentMindStore:
    global _DEFAULT_STORE
    path = default_agent_mind_path()
    if _DEFAULT_STORE is None or _DEFAULT_STORE.path != path:
        _DEFAULT_STORE = PersistentAgentMindStore(path)
    return _DEFAULT_STORE
