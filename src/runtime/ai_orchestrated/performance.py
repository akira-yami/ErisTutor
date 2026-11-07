# -*- coding: utf-8 -*-
"""Stage act cho self-initiated turn Architecture C.

Planner này chỉ art-direct nhịp từ ``IntentionPlan`` typed. Nó không cần permit,
opportunity, show arc hay scene director legacy. Nội dung/ý nghĩa vẫn thuộc main
model; stage chỉ quyết định hold, beat, gaze và settle sau khi câu đã được duyệt.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _intent_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
        except Exception:
            return {}
        return data if isinstance(data, Mapping) else {}
    return {}


@dataclass(frozen=True, slots=True)
class PerformanceBeat:
    beat_index: int
    kind: str
    text: str = ""
    # Bubble và TTS có thể khác ngôn ngữ. Giữ display riêng để segment Nhật
    # không vô tình thay luôn UI Việt, một lỗi khá sáng tạo nhưng không hữu ích.
    display_text: str = ""
    start_after_ms: int = 0
    duration_ms: int = 0
    pause_after_ms: int = 0
    tts_style: str = ""
    emotion: str = ""
    motion_intent: str = ""
    gaze_intent: str = ""
    head_intent: str = ""
    gesture_intent: str = ""
    actions: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["actions"] = list(self.actions)
        return payload


@dataclass(frozen=True, slots=True)
class AutonomousPerformanceAct:
    act_id: str
    opportunity_id: str
    opportunity_kind: str
    scene_mode: str
    intent: str
    session_phase: str
    cadence: str
    stage_preset_id: str
    performance_lane: str
    gesture_density: str
    body_first: bool
    audience_feedback_mode: str
    intrusion_pressure: float
    show_arc_id: str
    show_arc_family: str
    show_arc_stage: str
    show_format_id: str
    show_format_family: str
    show_format_variant: int
    stage_energy: float
    line_shape: str
    opening_mode: str
    closing_mode: str
    beats: tuple[PerformanceBeat, ...]
    total_budget_ms: int
    cooldown_ms: int
    opening_hold_ms: int
    closing_hold_ms: int
    director_note: str
    causal_intent_id: str = ""
    causal_thread_id: str = ""
    surface_level: str = ""
    evidence_refs: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["beats"] = [beat.to_payload() for beat in self.beats]
        return payload

    @property
    def speech_beats(self) -> tuple[PerformanceBeat, ...]:
        return tuple(beat for beat in self.beats if beat.kind == "speech" and beat.text)


class AutonomousPerformancePlanner:
    """Dựng nhịp từ semantic contract; không tạo thêm semantic authority."""

    def plan(
        self,
        text: str,
        *,
        autonomous_plan: Mapping[str, Any] | None = None,
        performance_context: Mapping[str, Any] | None = None,
        opening_plan: Mapping[str, Any] | None = None,
        performance_intent: Any = None,
        emotion: str = "neutral",
        motion: str = "none",
        tts_style: str = "neutral",
    ) -> AutonomousPerformanceAct | None:
        causal_intent = _intent_mapping(performance_intent)
        if causal_intent:
            return self.plan_from_intent(
                causal_intent,
                realized_text=text,
                autonomous_plan=autonomous_plan,
                emotion=emotion,
                motion=motion,
                tts_style=tts_style,
            )
        line = _clean(text)
        plan = _mapping(autonomous_plan)
        if not line or not plan or not bool(plan.get("speak", False)):
            return None
        semantics = _mapping(plan.get("semantics"))
        activity = _mapping(plan.get("activity"))
        perf = _mapping(performance_context)
        kind = str(semantics.get("turn_kind", "other") or "other")
        speech_act = str(semantics.get("speech_act", "unspecified") or "unspecified")
        hint = str(plan.get("performance_hint", perf.get("delivery_style", "neutral")) or "neutral")
        stage_emotion = str(perf.get("stage_emotion", emotion) or emotion or "neutral")
        stage_motion = str(perf.get("stage_motion", motion) or motion or "none")
        style = str(perf.get("tts_style", tts_style) or tts_style or "neutral")
        intrusion = max(0.0, min(1.0, float(plan.get("intrusion_risk", 0.0) or 0.0)))

        cadence, opening_hold, closing_hold, density = self._timing(kind, hint, intrusion)
        opening = _mapping(opening_plan)
        explicit_beats = tuple(
            item for item in (perf.get("opening_beats") or ())
            if isinstance(item, Mapping)
        )
        if opening:
            prosody = _mapping(opening.get("prosody_plan"))
            opening_hold = max(0, min(1000, int(prosody.get("opening_hold_ms", perf.get("opening_hold_ms", opening_hold)) or opening_hold)))
            closing_hold = max(0, min(1600, int(prosody.get("closing_settle_ms", perf.get("closing_settle_ms", closing_hold)) or closing_hold)))
            cadence = "opening_directed"

        speech_beats: list[PerformanceBeat] = []
        if explicit_beats:
            # Opening Director đã quyết định beat intent và pause. Stage chỉ map
            # sang contract cơ học, không chia lại theo dấu câu như thời đồ đá.
            for index, raw in enumerate(explicit_beats[:3]):
                part = _clean(raw.get("speech_text") or raw.get("display_text") or raw.get("text"))
                if not part:
                    continue
                beat_style = str(raw.get("delivery", style) or style)
                beat_motion = str(raw.get("motion", stage_motion if index == 0 else "hold_pose") or "none")
                beat_gaze = str(raw.get("gaze", "user_attention") or "user_attention")
                pause = max(0, min(1200, int(raw.get("pause_after_ms", 0) or 0)))
                emphasis = tuple(str(item) for item in (raw.get("emphasis") or ())[:4] if str(item).strip())
                speech_beats.append(PerformanceBeat(
                    beat_index=index + 1,
                    kind="speech",
                    text=part,
                    display_text=_clean(raw.get("display_text") or raw.get("text")),
                    duration_ms=self._duration(part),
                    pause_after_ms=pause,
                    tts_style=beat_style,
                    emotion=stage_emotion,
                    motion_intent=beat_motion,
                    gaze_intent=beat_gaze,
                    head_intent="neutral_small" if beat_motion in {"", "none"} else beat_motion,
                    gesture_intent="small_mark" if density == "low" and index == 0 else "none",
                    actions=tuple(f"emphasize:{item}" for item in emphasis),
                ))
        else:
            parts = self._split(line)[:2]
            for index, part in enumerate(parts):
                speech_beats.append(PerformanceBeat(
                    beat_index=index + 1,
                    kind="speech",
                    text=part,
                    display_text=part,
                    duration_ms=self._duration(part),
                    pause_after_ms=170 if index < len(parts) - 1 else 0,
                    tts_style=style,
                    emotion=stage_emotion,
                    motion_intent=stage_motion if index == 0 else "hold_pose",
                    gaze_intent="user_attention" if kind == "continuation" else "scene_focus",
                    head_intent="neutral_small" if stage_motion in {"", "none"} else stage_motion,
                    gesture_intent="small_mark" if density == "low" and index == 0 else "none",
                ))
        beats = (
            PerformanceBeat(
                beat_index=0,
                kind="opening_hold",
                duration_ms=opening_hold,
                emotion=stage_emotion,
                motion_intent=stage_motion,
                gaze_intent="user_attention" if kind == "continuation" else "scene_focus",
                head_intent="neutral_small",
            ),
            *speech_beats,
            PerformanceBeat(
                beat_index=len(speech_beats) + 1,
                kind="closing_settle",
                duration_ms=closing_hold,
                emotion="settle",
                motion_intent="settle",
                gaze_intent="soft_attention",
                head_intent="neutral_small",
            ),
        )
        total = opening_hold + closing_hold + sum(item.duration_ms + item.pause_after_ms for item in speech_beats)
        phase = str(activity.get("phase", "unknown") or "unknown")
        note = (
            f"Architecture C self-turn: kind={kind}; speech_act={speech_act}; cadence={cadence}; "
            f"activity_phase={phase}. Hold {opening_hold}ms, deliver {len(speech_beats)} beat(s), "
            "then settle. Do not add words or stage directions."
        )
        return AutonomousPerformanceAct(
            act_id=f"act_{uuid.uuid4().hex[:12]}",
            opportunity_id=str(plan.get("plan_id", "") or ""),
            opportunity_kind=kind,
            scene_mode=str(activity.get("activity_kind", "unknown") or "unknown"),
            intent=speech_act,
            session_phase=phase,
            cadence=cadence,
            stage_preset_id=f"architecture_c.{kind}",
            performance_lane="self_turn",
            gesture_density=density,
            body_first=True,
            audience_feedback_mode="mechanical_gate",
            intrusion_pressure=intrusion,
            show_arc_id="",
            show_arc_family="single_turn",
            show_arc_stage="complete",
            show_format_id="typed_self_turn",
            show_format_family="presence",
            show_format_variant=1,
            stage_energy=max(0.20, min(0.72, 0.58 - intrusion * 0.28)),
            line_shape=("multi_beat" if len(speech_beats) > 2 else "two_beat" if len(speech_beats) > 1 else "single_strike"),
            opening_mode="typed_hold",
            closing_mode="soft_return",
            beats=tuple(beats),
            total_budget_ms=total,
            cooldown_ms=0,  # InitiativeController sở hữu cooldown; stage không nhân đôi gate.
            opening_hold_ms=opening_hold,
            closing_hold_ms=closing_hold,
            director_note=note,
        )

    def plan_from_intent(
        self,
        performance_intent: Any,
        *,
        realized_text: str = "",
        autonomous_plan: Mapping[str, Any] | None = None,
        emotion: str = "neutral",
        motion: str = "none",
        tts_style: str = "neutral",
    ) -> AutonomousPerformanceAct | None:
        """Realize one typed causal intent across speech, gaze and body.

        The intent exists before text.  Lower surface levels may therefore emit
        a body-only act, while speech levels require a realized utterance.  The
        method refuses ungrounded intents rather than improvising provenance.
        """
        intent = _intent_mapping(performance_intent)
        refs_raw = intent.get("evidence_refs", ()) or ()
        if isinstance(refs_raw, str):
            refs_raw = (refs_raw,)
        refs = tuple(str(item) for item in refs_raw if str(item).strip())[:8]
        intent_id = str(intent.get("intent_id", "") or "")
        thread_id = str(intent.get("thread_id", "") or "")
        if not intent_id or not thread_id or not refs:
            return None

        surface = str(intent.get("surface_level", "private") or "private")
        speech_surface = surface in {"speech_fragment", "sentence", "initiative"}
        line = _clean(realized_text)
        if speech_surface and not line:
            return None
        max_words = max(1, min(80, int(intent.get("max_words", 18) or 18)))
        if line and len(line.split()) > max_words:
            # Do not rewrite or add words here. Refuse a realization that broke
            # the semantic/performance contract.
            return None

        plan = _mapping(autonomous_plan)
        semantics = _mapping(plan.get("semantics"))
        activity = _mapping(plan.get("activity"))
        kind = str(semantics.get("turn_kind", intent.get("event_kind", "causal_affect")) or "causal_affect")
        speech_act = str(intent.get("speech_act", "bounded_expression") or "bounded_expression")
        stage_emotion = str(intent.get("microexpression", emotion) or emotion or "neutral")
        stage_motion = str(intent.get("posture", motion) or motion or "none")
        style = str(tts_style or "neutral")
        opening_hold = max(0, min(1600, int(intent.get("opening_hold_ms", 0) or 0)))
        closing_hold = max(120, min(1800, int(intent.get("pause_after_ms", 320) or 320)))
        gaze_target = str(intent.get("gaze_target", "scene") or "scene")
        gaze_break = str(intent.get("gaze_break_trigger", "") or "")
        restraint = max(0.0, min(1.0, float(intent.get("restraint", 0.8) or 0.8)))
        vulnerability = max(0.0, min(1.0, float(intent.get("vulnerability", 0.0) or 0.0)))
        voice_energy = max(0.0, min(1.0, float(intent.get("voice_energy", 0.45) or 0.45)))

        beats: list[PerformanceBeat] = [
            PerformanceBeat(
                beat_index=0,
                kind="opening_hold",
                duration_ms=opening_hold,
                emotion=stage_emotion,
                motion_intent=stage_motion,
                gaze_intent=gaze_target,
                head_intent="gaze_break:" + gaze_break if gaze_break else "neutral_small",
                actions=(f"intent_id:{intent_id}", f"thread_id:{thread_id}"),
            )
        ]
        if speech_surface:
            parts = self._split(line)[:2]
            for index, part in enumerate(parts, start=1):
                beats.append(PerformanceBeat(
                    beat_index=index,
                    kind="speech",
                    text=part,
                    display_text=part,
                    duration_ms=self._duration(part),
                    pause_after_ms=closing_hold if index == len(parts) else 140,
                    tts_style=style,
                    emotion=stage_emotion,
                    motion_intent=stage_motion if index == 1 else "hold_pose",
                    gaze_intent=gaze_target,
                    head_intent="gaze_break:" + gaze_break if gaze_break else "neutral_small",
                    gesture_intent="none" if restraint >= 0.72 else "small_mark",
                    actions=(f"voice_energy:{voice_energy:.3f}", f"vulnerability:{vulnerability:.3f}"),
                ))
        elif surface in {"microexpression", "gaze_posture", "nonverbal_sound"}:
            beats.append(PerformanceBeat(
                beat_index=1,
                kind=surface,
                duration_ms=max(260, closing_hold),
                emotion=stage_emotion,
                motion_intent=stage_motion,
                gaze_intent=gaze_target,
                head_intent="gaze_break:" + gaze_break if gaze_break else "neutral_small",
                gesture_intent="none",
                actions=(f"restraint:{restraint:.3f}",),
            ))
        beats.append(PerformanceBeat(
            beat_index=len(beats),
            kind="closing_settle",
            duration_ms=closing_hold,
            emotion="settle",
            motion_intent="settle",
            gaze_intent="soft_attention",
            head_intent="neutral_small",
            actions=(f"intent_id:{intent_id}",),
        ))
        total = sum(beat.duration_ms + beat.pause_after_ms for beat in beats)
        phase = str(activity.get("phase", "unknown") or "unknown")
        speech_count = sum(1 for beat in beats if beat.kind == "speech")
        return AutonomousPerformanceAct(
            act_id=f"act_{uuid.uuid4().hex[:12]}",
            opportunity_id=str(plan.get("plan_id", thread_id) or thread_id),
            opportunity_kind=kind,
            scene_mode=str(activity.get("activity_kind", "unknown") or "unknown"),
            intent=speech_act,
            session_phase=phase,
            cadence="causal_intent",
            stage_preset_id=f"architecture_c.causal.{surface}",
            performance_lane="causal_affect",
            gesture_density="minimal" if restraint >= 0.70 else "low",
            body_first=True,
            audience_feedback_mode="thread_linked",
            intrusion_pressure=max(0.0, min(1.0, float(plan.get("intrusion_risk", 0.0) or 0.0))),
            show_arc_id="",
            show_arc_family="causal_thread",
            show_arc_stage="bounded_expression",
            show_format_id="typed_causal_intent",
            show_format_family="presence",
            show_format_variant=1,
            stage_energy=max(0.10, min(0.68, voice_energy)),
            line_shape="body_only" if speech_count == 0 else "single_strike" if speech_count == 1 else "two_beat",
            opening_mode="intent_hold",
            closing_mode="restrained_settle",
            beats=tuple(beats),
            total_budget_ms=total,
            cooldown_ms=0,
            opening_hold_ms=opening_hold,
            closing_hold_ms=closing_hold,
            director_note=(
                f"Causal performance intent {intent_id} for {thread_id}; surface={surface}. "
                "Text, TTS, gaze and motion must preserve the same intent and prohibited moves."
            ),
            causal_intent_id=intent_id,
            causal_thread_id=thread_id,
            surface_level=surface,
            evidence_refs=refs,
        )

    @staticmethod
    def _timing(kind: str, hint: str, intrusion: float) -> tuple[str, int, int, str]:
        if kind == "continuation":
            base = ("connected", 70, 300, "low")
        elif kind == "activity_event":
            base = ("responsive", 55, 250, "low")
        elif kind == "environment":
            base = ("observant", 95, 340, "low")
        elif kind in {"internal", "discovery"}:
            base = ("deliberate", 145, 430, "minimal")
        else:
            base = ("restrained", 110, 360, "minimal")
        cadence, opening, closing, density = base
        if "dry" in hint or "quiet" in hint:
            opening += 35
            closing += 60
        if intrusion >= 0.65:
            opening = min(opening, 70)
            closing = min(closing, 260)
            density = "minimal"
        return cadence, opening, closing, density

    @staticmethod
    def _split(text: str) -> tuple[str, ...]:
        parts = tuple(part.strip() for part in _SENTENCE_RE.split(text) if part.strip())
        return parts or (text,)

    @staticmethod
    def _duration(text: str) -> int:
        return max(420, min(4000, 260 + max(1, len(text.split())) * 255))


__all__ = ["AutonomousPerformanceAct", "AutonomousPerformancePlanner", "PerformanceBeat"]
