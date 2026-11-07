# -*- coding: utf-8 -*-
"""stage_show_runtime.py - biên dịch StageFrame thành timeline sân khấu thật.

Phase 8 không thêm avatar cụ thể. Runtime chỉ phát các cue trung tính như giữ
nhịp, hướng nhìn, cử chỉ, cửa sổ nói và settle. Frontend hiện tại có thể bỏ qua;
avatar 3D sau này chỉ cần map cue sang blendshape/gesture của nó.

Quan trọng: module không ``sleep`` và không sở hữu TTS. Nó tạo timeline tương
đối để frontend hoặc transport tự schedule, tránh block cognition chỉ vì một
cái nghiêng đầu kéo dài 180ms. Phần mềm đủ chậm rồi, không cần diễn chậm bằng
cách khóa luôn thread chính.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


SHOW_PLAN_VERSION = "eris.show.v1"


def _text(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split()).strip()[: max(1, int(limit))]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True, slots=True)
class StageShowCue:
    cue_id: str
    cue_index: int
    at_ms: int
    kind: str
    duration_ms: int = 0
    segment_index: int = -1
    text: str = ""
    emotion: str = ""
    motion_intent: str = ""
    gaze_intent: str = ""
    head_intent: str = ""
    gesture_intent: str = ""
    interruptible: bool = True
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["data"] = dict(self.data)
        return {k: v for k, v in payload.items() if v not in ("", None, {}, [])}


@dataclass(frozen=True, slots=True)
class StageShowPlan:
    show_id: str
    turn_id: str
    version: str
    created_at_ms: int
    total_duration_ms: int
    body_only: bool
    interruptible: bool
    source: str
    cues: tuple[StageShowCue, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "show_id": self.show_id,
            "turn_id": self.turn_id,
            "version": self.version,
            "created_at_ms": self.created_at_ms,
            "total_duration_ms": self.total_duration_ms,
            "body_only": self.body_only,
            "interruptible": self.interruptible,
            "source": self.source,
            "cue_count": len(self.cues),
            "cues": [cue.to_payload() for cue in self.cues],
        }


class StageShowRuntime:
    """Biên dịch contract nhịp thành cue timeline, không chạm provider/avatar."""

    def compile(self, stage_frame: Any) -> StageShowPlan:
        now_ms = int(time.time() * 1000)
        turn_id = str(getattr(stage_frame, "turn_id", "") or uuid.uuid4().hex)
        show_id = f"show_{turn_id[:12]}_{uuid.uuid4().hex[:6]}"
        source = str(getattr(stage_frame, "source", "unknown") or "unknown")
        body_only = bool(getattr(stage_frame, "body_only", False))
        interruptible = bool(getattr(stage_frame, "interruptible", True))
        emotion = str(getattr(stage_frame, "emotion", "neutral") or "neutral")
        motion = str(getattr(stage_frame, "motion", "none") or "none")
        text = _text(getattr(stage_frame, "tts_text", ""))
        act = getattr(stage_frame, "stage_act", {})
        act = dict(act) if isinstance(act, Mapping) else {}
        beats = act.get("beats") if isinstance(act.get("beats"), list) else []

        cues: list[StageShowCue] = []
        cursor = 0

        def add(kind: str, *, duration_ms: int = 0, segment_index: int = -1,
                cue_text: str = "", beat: Mapping[str, Any] | None = None,
                data: Mapping[str, Any] | None = None) -> None:
            beat = beat or {}
            cues.append(
                StageShowCue(
                    cue_id=f"{show_id}:cue:{len(cues)}",
                    cue_index=len(cues),
                    at_ms=max(0, cursor),
                    kind=kind,
                    duration_ms=max(0, int(duration_ms)),
                    segment_index=segment_index,
                    text=_text(cue_text),
                    emotion=str(beat.get("emotion", emotion) or emotion),
                    motion_intent=str(beat.get("motion_intent", motion) or motion),
                    gaze_intent=str(beat.get("gaze_intent", "") or ""),
                    head_intent=str(beat.get("head_intent", "") or ""),
                    gesture_intent=str(beat.get("gesture_intent", "") or ""),
                    interruptible=interruptible,
                    data=dict(data or {}),
                )
            )

        add(
            "act_open",
            data={
                "act_id": str(act.get("act_id", "") or ""),
                "opportunity_kind": str(act.get("opportunity_kind", "") or ""),
                "show_format_id": str(act.get("show_format_id", "") or ""),
            },
        )

        if body_only and not beats:
            duration = max(240, _int(getattr(stage_frame, "delivery", {}).get("body_hold_ms", 650), 650))
            add("body_only_presence", duration_ms=duration)
            cursor += duration
        elif beats:
            speech_index = 0
            for raw in beats:
                if not isinstance(raw, Mapping):
                    continue
                kind = str(raw.get("kind", "") or "")
                duration = max(0, _int(raw.get("duration_ms"), 0))
                start_after = max(0, _int(raw.get("start_after_ms"), 0))
                pause_after = max(0, _int(raw.get("pause_after_ms"), 0))
                cursor += start_after
                if kind == "speech":
                    cue_text = _text(raw.get("text", ""))
                    add("speech_prepare", segment_index=speech_index, cue_text=cue_text, beat=raw)
                    add("speech_window", duration_ms=duration, segment_index=speech_index, cue_text=cue_text, beat=raw)
                    cursor += duration
                    if pause_after:
                        add("rhetorical_pause", duration_ms=pause_after, segment_index=speech_index, beat=raw)
                        cursor += pause_after
                    speech_index += 1
                elif kind == "opening_hold":
                    add("opening_hold", duration_ms=duration, beat=raw)
                    cursor += duration
                elif kind == "closing_settle":
                    add("closing_settle", duration_ms=duration, beat=raw)
                    cursor += duration
                else:
                    add(kind or "stage_hold", duration_ms=duration, beat=raw)
                    cursor += duration + pause_after
        elif text:
            # Chat thường vẫn có một timeline tối thiểu để avatar 3D sau này biết
            # lúc nào chuẩn bị, nói và settle; không cần đợi autonomous mới được diễn.
            estimated = max(420, min(5_500, 260 + len(text.split()) * 245))
            add("speech_prepare", segment_index=0, cue_text=text)
            add("speech_window", duration_ms=estimated, segment_index=0, cue_text=text)
            cursor += estimated
            add("closing_settle", duration_ms=260)
            cursor += 260

        add("act_close", data={"final_motion": "idle_return"})
        return StageShowPlan(
            show_id=show_id,
            turn_id=turn_id,
            version=SHOW_PLAN_VERSION,
            created_at_ms=now_ms,
            total_duration_ms=max(0, cursor),
            body_only=body_only,
            interruptible=interruptible,
            source=source,
            cues=tuple(cues),
        )


__all__ = [
    "SHOW_PLAN_VERSION",
    "StageShowCue",
    "StageShowPlan",
    "StageShowRuntime",
]
