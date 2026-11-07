# -*- coding: utf-8 -*-
"""Biến event sân khấu và affect nội tại thành parameter Live2D liên tục.

Model INFJ chưa có ``exp3``/``motion3`` nên biểu cảm được phối trực tiếp từ
parameter đã rig. Có ba lớp độc lập:

1. affect nền từ ``PersistentInternalState`` / private mind;
2. emotion + motion tạm thời của câu đang nói;
3. idle director tạo vi chuyển động có lịch khi im lặng.

Raw chain-of-thought không bao giờ đi vào renderer. Runtime chỉ gửi vector số
đã chuẩn hóa, bởi vì để từng dòng suy luận giật lông mày trực tiếp là một ý
kiến rất con người và rất tệ.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping

from .affect import AffectPose, AffectVector, NEUTRAL_AFFECT
from .idle import IdleDirector
from .parameter_profile import INFJ_PARAMETER_PROFILE
from .speech_animation import SpeechAnimator, SpeechContext


@dataclass(frozen=True, slots=True)
class Live2DSnapshot:
    emotion: str
    motion: str
    speaking: bool
    parameters: Mapping[str, float]
    eye_open_left: float = 1.0
    eye_open_right: float = 1.0
    gaze_bias_x: float = 0.0
    gaze_bias_y: float = 0.0
    affect: Mapping[str, float] | None = None
    affect_source: str = ""
    idle_action: str = "settle"
    explicit_emotion_weight: float = 0.0
    last_event_type: str = ""
    last_event_at: float = 0.0
    mouth_open_ratio: float = 0.0
    speech_cue: str = "silent"
    mouth_shape: float = 0.0


@dataclass(frozen=True, slots=True)
class ExpressionTarget:
    mouth_form: float = 0.0
    brow_left: float = 0.0
    brow_right: float = 0.0
    angle_x: float = 0.0
    angle_z: float = 0.0
    eye_open_left: float = 1.0
    eye_open_right: float = 1.0
    # Params mới từ moc3 v2
    mouth_corner_l: float = 0.0
    mouth_corner_r: float = 0.0
    lip_press: float = 0.0
    tongue: float = 0.0
    cheek: float = 0.0
    eye_highlight: float = 1.0

    def parameters(self) -> dict[str, float]:
        params = {
            "ParamMouthForm": self.mouth_form,
            "ParamBrowLAngle": self.brow_left,
            "ParamBrowRAngle": self.brow_right,
            "ParamAngleX": self.angle_x,
            "ParamAngleZ": self.angle_z,
        }
        params.update({
            "ParamErisMouthCornerL": self.mouth_corner_l,
            "ParamErisMouthCornerR": self.mouth_corner_r,
            "ParamErisLipPress": self.lip_press,
            "ParamErisTongue": self.tongue,
            "ParamCheek": self.cheek,
            "ParamEyeHighlight": self.eye_highlight,
        })
        return params

    @classmethod
    def from_affect_pose(cls, pose: AffectPose) -> "ExpressionTarget":
        return cls(
            mouth_form=pose.mouth_form,
            mouth_corner_l=pose.mouth_corner_l,
            mouth_corner_r=pose.mouth_corner_r,
            lip_press=pose.lip_press,
            tongue=pose.tongue,
            brow_left=pose.brow_left,
            brow_right=pose.brow_right,
            angle_x=pose.angle_x,
            angle_z=pose.angle_z,
            eye_open_left=pose.eye_open_left,
            eye_open_right=pose.eye_open_right,
            cheek=getattr(pose, "cheek", 0.0),
        )

    def blend(self, other: "ExpressionTarget", weight: float) -> "ExpressionTarget":
        ratio = max(0.0, min(1.0, float(weight)))
        return ExpressionTarget(
            mouth_form=self.mouth_form + (other.mouth_form - self.mouth_form) * ratio,
            brow_left=self.brow_left + (other.brow_left - self.brow_left) * ratio,
            brow_right=self.brow_right + (other.brow_right - self.brow_right) * ratio,
            angle_x=self.angle_x + (other.angle_x - self.angle_x) * ratio,
            angle_z=self.angle_z + (other.angle_z - self.angle_z) * ratio,
            eye_open_left=self.eye_open_left + (other.eye_open_left - self.eye_open_left) * ratio,
            eye_open_right=self.eye_open_right + (other.eye_open_right - self.eye_open_right) * ratio,
            mouth_corner_l=self.mouth_corner_l + (other.mouth_corner_l - self.mouth_corner_l) * ratio,
            mouth_corner_r=self.mouth_corner_r + (other.mouth_corner_r - self.mouth_corner_r) * ratio,
            lip_press=self.lip_press + (other.lip_press - self.lip_press) * ratio,
            tongue=self.tongue + (other.tongue - self.tongue) * ratio,
            cheek=self.cheek + (other.cheek - self.cheek) * ratio,
            eye_highlight=self.eye_highlight + (other.eye_highlight - self.eye_highlight) * ratio,
        )


# Brow dương mang sắc thái nhướng/đặt câu hỏi, không phải mặc định của nụ cười.
_EMOTION_TARGETS: dict[str, ExpressionTarget] = {
    "neutral": ExpressionTarget(),
    "flat_calm": ExpressionTarget(
        mouth_form=-0.04,
        brow_left=-0.08,
        brow_right=-0.07,
        eye_open_left=0.96,
        eye_open_right=0.96,
    ),
    "warm_soft": ExpressionTarget(
        mouth_form=0.30,
        brow_left=-0.04,
        brow_right=-0.03,
        angle_x=0.8,
        angle_z=0.8,
        eye_open_left=0.86,
        eye_open_right=0.88,
    ),
    "soft_near": ExpressionTarget(
        mouth_form=0.24,
        brow_left=-0.07,
        brow_right=-0.05,
        angle_x=1.4,
        angle_z=1.3,
        eye_open_left=0.91,
        eye_open_right=0.93,
    ),
    "dry_tease": ExpressionTarget(
        mouth_form=0.16,
        brow_left=0.34,
        brow_right=-0.08,
        angle_x=-2.8,
        angle_z=-1.5,
        eye_open_left=0.90,
        eye_open_right=0.96,
    ),
    "mischief_soft": ExpressionTarget(
        mouth_form=0.38,
        brow_left=0.28,
        brow_right=-0.10,
        angle_x=-3.8,
        angle_z=-1.8,
        eye_open_left=0.88,
        eye_open_right=0.95,
    ),
    "curious": ExpressionTarget(
        mouth_form=0.05,
        brow_left=0.42,
        brow_right=0.16,
        angle_x=2.0,
        angle_z=2.7,
        eye_open_left=1.0,
        eye_open_right=1.0,
    ),
    "focused": ExpressionTarget(
        mouth_form=-0.10,
        brow_left=-0.22,
        brow_right=-0.20,
        angle_x=-1.8,
        angle_z=0.4,
        eye_open_left=0.88,
        eye_open_right=0.88,
    ),
    "thinking": ExpressionTarget(
        mouth_form=-0.02,
        brow_left=0.24,
        brow_right=-0.12,
        angle_x=-4.6,
        angle_z=2.2,
        eye_open_left=0.91,
        eye_open_right=0.96,
    ),
    "annoyed_soft": ExpressionTarget(
        mouth_form=-0.32,
        brow_left=-0.34,
        brow_right=-0.27,
        angle_x=-3.8,
        angle_z=-1.0,
        eye_open_left=0.80,
        eye_open_right=0.84,
    ),
    "quiet_low": ExpressionTarget(
        mouth_form=-0.20,
        brow_left=-0.18,
        brow_right=-0.16,
        angle_x=-2.0,
        angle_z=0.7,
        eye_open_left=0.84,
        eye_open_right=0.86,
    ),
    "distant": ExpressionTarget(
        mouth_form=-0.18,
        brow_left=-0.14,
        brow_right=-0.13,
        angle_x=-6.5,
        angle_z=1.1,
        eye_open_left=0.79,
        eye_open_right=0.81,
    ),
    "surprise": ExpressionTarget(
        mouth_form=0.04,
        brow_left=0.56,
        brow_right=0.54,
        eye_open_left=1.0,
        eye_open_right=1.0,
    ),
    # BIEUCAM: 9 biểu cảm mới theo tài liệu Word (trang 1–9).
    # Trường thiếu trong Word được để trung tính, không bịa chuyển động đầu/mắt.
    # MouthOpen=0 khi im lặng do stage quản lý; lúc nói TTS nắm quyền lip-sync.
    "smirk_minor": ExpressionTarget(mouth_form=-0.3, mouth_corner_l=1.0),
    "smirk_disdain": ExpressionTarget(mouth_form=-0.3, mouth_corner_l=1.0,
                                        eye_open_left=0.56, eye_open_right=0.78),
    "blush_affection": ExpressionTarget(cheek=1.0),
    "cat_mouth": ExpressionTarget(mouth_form=-1.0),
    "sad_empathy": ExpressionTarget(mouth_form=-0.3, brow_left=1.0, brow_right=1.0),
    # Hình minh họa nháy mắt trái. Word cho phép đổi qua mắt phải; cần preset
    # riêng khi tác giả muốn đảo bên, không đảo ngẫu nhiên trong một frame.
    "wink_correct": ExpressionTarget(mouth_form=0.0, brow_left=-1.0, brow_right=-1.0,
                                       eye_open_left=0.0, eye_open_right=1.0),
    "teehee": ExpressionTarget(mouth_form=0.0, brow_left=-1.0, brow_right=-1.0,
                                 eye_open_left=0.0, eye_open_right=1.0, tongue=0.6),
    # Biến thể nháy mắt phải đúng theo tùy chọn "EyeL hoặc EyeR" trong Word.
    "wink_correct_right": ExpressionTarget(mouth_form=0.0, brow_left=-1.0, brow_right=-1.0,
                                             eye_open_left=1.0, eye_open_right=0.0),
    "teehee_right": ExpressionTarget(mouth_form=0.0, brow_left=-1.0, brow_right=-1.0,
                                       eye_open_left=1.0, eye_open_right=0.0, tongue=0.6),
    "yan_warning": ExpressionTarget(mouth_form=-0.3, eye_highlight=0.0),
    # Anime pass 05/11: lowered lids and near-flat brows distinguish a lazy
    # stare from the raised, sad brows of sad_empathy. Eye caps match exp3.
    "lazy_deadpan": ExpressionTarget(mouth_form=-0.15, brow_left=0.0, brow_right=0.10,
                                      eye_open_left=0.48, eye_open_right=0.52),
    "teasing": ExpressionTarget(
        mouth_form=0.08,
        brow_left=0.26,
        brow_right=-0.12,
        angle_x=-1.2,
        angle_z=-0.6,
        eye_open_left=0.62,
        eye_open_right=0.86,
        mouth_corner_l=0.12,
        mouth_corner_r=0.12,
        tongue=1.0,
    ),
}

_EMOTION_ALIASES = {
    "warm_playful": "mischief_soft",
    "mischief": "mischief_soft",
    "deadpan": "flat_calm",
    "quiet_soft": "quiet_low",
    "curious_soft": "curious",
    "tired_soft": "quiet_low",
    "flat_cold": "distant",
    "soft": "warm_soft",
    "soft_smile": "warm_soft",
    "playful": "mischief_soft",
    "smug": "dry_tease",
    "annoyed": "annoyed_soft",
    "sad": "quiet_low",
    "sad_distant": "distant",
    "thinking_soft": "thinking",
    "tease": "teasing",
    "tongue": "teasing",
    # Bí danh mang ngữ nghĩa sử dụng từ tutor/Root; không có heuristic đọc
    # văn bản câu trả lời để tự gán người học vào nhóm "lười" / "sai".
    "small_smirk": "smirk_minor",
    "disdain_smirk": "smirk_disdain",
    "blush": "blush_affection",
    "cat_smile": "cat_mouth",
    "empathetic_sad": "sad_empathy",
    "wink_success": "wink_correct",
    "playful_teehee": "teehee",
    "yandere_warning": "yan_warning",
    "lazy_stare": "lazy_deadpan",
}

# Preserve asset names so native playback can select exact expressions.
_EMOTION_TARGETS.update({
    "calm": _EMOTION_TARGETS["flat_calm"],
    "warm": _EMOTION_TARGETS["warm_soft"],
    "dry": _EMOTION_TARGETS["dry_tease"],
    "low": _EMOTION_TARGETS["quiet_low"],
    "amused": ExpressionTarget(mouth_form=.24, mouth_corner_l=.18, mouth_corner_r=.18, eye_open_left=.72, eye_open_right=.76),
    "skeptical": ExpressionTarget(mouth_form=-.08, brow_left=.55, brow_right=-.28, mouth_corner_l=-.08, lip_press=.14, angle_z=-2,
                                  eye_open_left=.58, eye_open_right=.90),
    "concerned": ExpressionTarget(mouth_form=-.18, brow_left=.22, brow_right=.16, mouth_corner_l=-.12, mouth_corner_r=-.12, lip_press=.18),
    "surprised_soft": ExpressionTarget(mouth_form=.02, brow_left=.34, brow_right=.32, eye_open_left=1, eye_open_right=1),
})

_MOTION_OFFSETS: dict[str, dict[str, float]] = {
    "read": {"ParamAngleX": -1.8, "ParamAngleZ": 1.6},
    "thinking_small": {"ParamAngleX": -1.8, "ParamAngleZ": 2.2},
    "tilt_head": {"ParamAngleZ": 2.6},
    "look_aside": {"ParamAngleX": -4.8},
    "look_away": {"ParamAngleX": -5.5},
    "look_down": {"ParamAngleX": -2.8, "ParamAngleZ": 0.6},
    "lean_in_small": {"ParamAngleX": 1.8, "ParamAngleZ": 0.8},
    "sad": {"ParamAngleX": -2.2, "ParamAngleZ": 0.8},
    "stand": {},
    "idle": {},
}

_PARAMETER_RESPONSE = {
    # Native opening and closing responses are 48 / 64 per second.
    "ParamMouthOpenY": 48.0,
    "ParamMouthForm": 7.5,
    "ParamBrowLAngle": 6.5,
    "ParamBrowRAngle": 6.5,
    "ParamAngleX": 5.2,
    "ParamAngleZ": 5.0,
    "ParamErisMouthCornerL": 6.0,
    "ParamErisMouthCornerR": 6.0,
    "ParamErisLipPress": 5.5,
    # Lưỡi: chuyển nhanh khi ra, về chậm hơn để không bị flash
    "ParamErisTongue": 8.0,
    "ParamCheek": 4.5,
    "ParamBreath": 5.0,
    "ParamEyeHighlight": 8.0,
}


def _clamp01(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


class Live2DStageState:
    """State thread-safe giữa UDP receiver và render loop."""

    def __init__(self, *, idle_seed: int | None = None) -> None:
        self._lock = threading.RLock()
        self._emotion = "neutral"
        self._motion = "stand"
        self._speaking = False
        self._thinking = False
        # Agent-owned turn projection. Renderer chỉ biểu diễn tín hiệu này; nó
        # không tự suy floor từ voice length, continuation hay timeout.
        self._turn_floor = "open"
        self._continuation_feel = "neutral"
        self._last_event_type = ""
        now = time.monotonic()
        self._last_event_at = now
        self._emotion_at = -1.0e9
        self._motion_at = now

        self._affect_target = NEUTRAL_AFFECT
        self._affect_current = NEUTRAL_AFFECT
        self._affect_last_at = -1.0e9
        self._affect_source = ""
        self._thought_pressure = 0.0
        self._thought_confidence = 0.0
        self._idle = IdleDirector(seed=idle_seed)
        self._speech = SpeechAnimator()
        self._speech_deadline = -1.0e9
        self._speech_contexts: dict[tuple[str, int], SpeechContext] = {}
        self._latest_speech_context = SpeechContext()
        self._latest_speech_key: tuple[str, int] = ("", 0)
        # Turn/segment đang thật sự sở hữu audio. Stage bus có cả event lifecycle
        # mirror không mang turn_id; chúng không được phép cắt miệng của audio có
        # owner rõ ràng chỉ vì cùng mang chữ ``interrupted`` hoặc ``degraded``.
        self._active_speech_key: tuple[str, int] = ("", 0)
        self._recent_event_ids: deque[str] = deque(maxlen=256)
        self._recent_event_id_set: set[str] = set()

        neutral = _EMOTION_TARGETS["neutral"]
        self._current: dict[str, float] = neutral.parameters()
        self._current["ParamMouthOpenY"] = INFJ_PARAMETER_PROFILE.mouth_closed
        self._eye_open_left = neutral.eye_open_left
        self._eye_open_right = neutral.eye_open_right

    def handle_payload(self, payload: Mapping[str, Any]) -> bool:
        if not isinstance(payload, Mapping):
            return False
        protocol = str(payload.get("protocol", "") or "")
        event_type = str(payload.get("type", "") or "").strip().lower()
        with self._lock:
            if protocol == "eris.stage" and self._is_duplicate_stage_event(payload):
                return False
            if protocol == "eris.stage":
                self._handle_stage_event(payload, event_type)
            else:
                self._handle_legacy_payload(payload)
            self._last_event_type = event_type
            self._last_event_at = time.monotonic()
            return True

    def _is_duplicate_stage_event(self, payload: Mapping[str, Any]) -> bool:
        event_id = str(payload.get("event_id", "") or "").strip()
        if not event_id:
            return False
        if event_id in self._recent_event_id_set:
            return True
        if len(self._recent_event_ids) == self._recent_event_ids.maxlen:
            expired = self._recent_event_ids.popleft()
            self._recent_event_id_set.discard(expired)
        self._recent_event_ids.append(event_id)
        self._recent_event_id_set.add(event_id)
        return False

    def _handle_stage_event(self, payload: Mapping[str, Any], event_type: str) -> None:
        speech_context = SpeechContext.from_payload(payload)
        if event_type not in {"affect_state", "cognitive_affect", "inner_affect"}:
            self._remember_speech_context(payload, speech_context)

        if event_type in {"affect_state", "cognitive_affect", "inner_affect"}:
            self._handle_affect_event(payload)
            return

        if event_type == "turn_projection":
            self._handle_turn_projection(payload)
            return

        # Một số frontend gói affect chung trong stage_state. Nhận nhưng không
        # cho nó thay đổi emotion tường minh nếu payload không yêu cầu.
        data = payload.get("data")
        if isinstance(data, Mapping) and isinstance(data.get("affect"), Mapping):
            self._handle_affect_event(payload)

        emotion = str(payload.get("emotion", "") or "").strip().lower()
        motion = str(payload.get("motion", "") or "").strip().lower()
        now = time.monotonic()
        if emotion:
            self._emotion = self._normalize_emotion(emotion)
            self._emotion_at = now
        if motion:
            self._motion = motion
            self._motion_at = now

        if event_type in {"received", "thinking_started", "content_committed", "audio_buffering"}:
            # received/thinking_started đánh dấu cognition mới: projection của
            # lượt trước hết hiệu lực cho đến khi root agent commit projection mới.
            if event_type in {"received", "thinking_started"}:
                self._turn_floor = "open"
                self._continuation_feel = "neutral"
            # Content may already be committed while its bubble remains hidden
            # waiting for ElevenLabs. The body keeps the same visible thinking
            # state until playback truly starts.
            self._thinking = True
            self._motion = motion or "thinking_small"
            self._motion_at = now
            if not emotion:
                self._emotion = "thinking"
                self._emotion_at = now
        elif event_type in {"audio_start", "audio_playback_started"}:
            self._thinking = False
            self._speaking = True
            resolved_context = self._resolve_speech_context(payload, speech_context)
            event_key = self._speech_context_key(payload)
            # Event mirror rỗng thường đến ngay sau event audio có turn_id. Giữ
            # owner có khóa thay vì để payload rỗng xóa danh tính câu đang phát.
            if event_key[0] or not self._active_speech_key[0]:
                self._active_speech_key = event_key
            self._speech.start(now=now, context=resolved_context, audio_playback=True)
            if resolved_context.audio_duration_ms > 0:
                self._speech_deadline = now + resolved_context.audio_duration_ms / 1000.0
            else:
                # Cứu hộ cho callback thiếu audio_done. Ước lượng rộng để không
                # cắt câu dài, nhưng không cho miệng mắc kẹt đến tận thế.
                expected_seconds = max(1.2, min(28.0, len(resolved_context.text) * 0.085 + 0.9))
                self._speech_deadline = now + expected_seconds + 1.8
        elif event_type in {
            "done",
            "degraded",
            "interrupted",
            "audio_playback_done",
            "audio_done",
            "audio_cancelled",
            "audio_failed",
            "audio_localization_failed",
            "stage_segment_cancelled",
            "stage_segment_failed",
            "continuation_cancelled",
        }:
            terminal_key = self._speech_context_key(payload)
            if not self._terminal_event_owns_active_speech(terminal_key):
                # Chỉ bỏ qua terminal mirror vô chủ; event có đúng turn/segment
                # vẫn dừng ngay. Nhờ vậy log có thể lặp, nhưng cái miệng không bị
                # ba bản sao hành chính cùng lao vào bóp cổ giữa một câu.
                return
            self._speaking = False
            self._speech.stop()
            self._speech_deadline = -1.0e9
            self._active_speech_key = ("", 0)
            if event_type in {"audio_playback_done", "audio_done"} and self._turn_floor == "hold":
                # Agent nói "tôi chưa xong": giữ một pose chuẩn bị nói tiếp thay
                # vì settle về stand. Đây chỉ là body-language projection.
                self._thinking = True
                hold_motion, hold_emotion = self._projection_pose(self._continuation_feel)
                self._motion = hold_motion
                self._motion_at = now
                self._emotion = hold_emotion
                self._emotion_at = now
            else:
                self._thinking = False
                self._motion = motion or "stand"
                self._motion_at = now
        elif event_type == "thinking_finished":
            self._thinking = False
            self._motion = motion or "stand"
            self._motion_at = now


    def _handle_turn_projection(self, payload: Mapping[str, Any]) -> None:
        """Nhận exact floor projection do root AgentLoop phát ra.

        Không có heuristic semantic ở đây. ``continuation_feel`` chỉ được map
        sang pose trình diễn; nội dung/lifecycle continuation vẫn thuộc agent.
        """

        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
        assert isinstance(data, Mapping)
        floor = str(data.get("floor", "open") or "open").strip().lower()
        if floor not in {"hold", "yield", "open"}:
            floor = "open"
        feel = str(data.get("continuation_feel", "neutral") or "neutral").strip().lower()
        if feel not in {"neutral", "thinking", "eager", "soft", "playful"}:
            feel = "neutral"
        self._turn_floor = floor
        self._continuation_feel = feel

        # Khi agent đã quyết HOLD trong lúc writer/TTS còn chuẩn bị, cho body biết
        # trước bằng pose nhỏ. Nếu đang nói thì speech animation vẫn ưu tiên.
        if floor == "hold" and not self._speaking:
            motion, emotion = self._projection_pose(feel)
            self._thinking = True
            self._motion = motion
            self._motion_at = time.monotonic()
            self._emotion = emotion
            self._emotion_at = self._motion_at
        elif floor == "yield" and not self._speaking:
            self._thinking = False
            self._motion = "stand"
            self._motion_at = time.monotonic()

    @staticmethod
    def _projection_pose(feel: str) -> tuple[str, str]:
        return {
            "thinking": ("thinking_small", "thinking"),
            "eager": ("lean_in_small", "curious"),
            "soft": ("lean_in_small", "warm_soft"),
            "playful": ("tilt_head", "mischief_soft"),
            "neutral": ("thinking_small", "focused"),
        }.get(str(feel or "neutral"), ("thinking_small", "thinking"))


    def _terminal_event_owns_active_speech(self, event_key: tuple[str, int]) -> bool:
        """Chỉ terminal của đúng turn/segment mới được dừng audio đang nói."""

        if not self._speaking:
            return True
        active_turn, active_seq = self._active_speech_key
        event_turn, event_seq = event_key
        if not active_turn:
            return True
        if not event_turn or event_turn != active_turn:
            return False
        # Seq=0 thường có nghĩa frontend cũ không biết segment. Chỉ so cứng khi
        # cả hai phía đều có seq thực, để vẫn tương thích event legacy.
        if active_seq and event_seq and active_seq != event_seq:
            return False
        return True

    def _handle_affect_event(self, payload: Mapping[str, Any]) -> None:
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
        assert isinstance(data, Mapping)
        raw_affect = data.get("affect") if isinstance(data.get("affect"), Mapping) else data
        if not isinstance(raw_affect, Mapping):
            return
        self._affect_target = AffectVector.from_mapping(raw_affect, fallback=self._affect_target)
        self._affect_last_at = time.monotonic()
        self._affect_source = str(data.get("source", payload.get("source", "")) or "")[:120]
        self._thought_pressure = max(
            self._thought_pressure * 0.72,
            _clamp01(data.get("thought_pressure", data.get("pressure", 0.0))),
        )
        self._thought_confidence = _clamp01(data.get("confidence", 0.0))
        self._idle.notify_affect(
            now=self._affect_last_at,
            source=self._affect_source,
            thought_pressure=self._thought_pressure,
            confidence=self._thought_confidence,
        )

    def _handle_legacy_payload(self, payload: Mapping[str, Any]) -> None:
        emotion = str(
            payload.get("preset_emotion", payload.get("emotion", "")) or ""
        ).strip().lower()
        motion = str(
            payload.get("stage_motion", payload.get("animation", "")) or ""
        ).strip().lower()
        now = time.monotonic()
        if emotion:
            self._emotion = self._normalize_emotion(emotion)
            self._emotion_at = now
        if motion:
            self._motion = motion
            self._motion_at = now
        if "should_speak" in payload:
            # A text decision expresses intent to speak, not device playback.
            # It may arrive after audio_start and must not reset its clock.
            legacy_context = SpeechContext(
                text=str(payload.get("tts_text", payload.get("text", "")) or ""),
                prosody_style=str(payload.get("prosody_style", "neutral") or "neutral").strip().lower(),
                speech_act=str(payload.get("speech_act", "") or "").strip().lower(),
                actions=tuple(str(item or "").strip().lower() for item in (payload.get("actions") or ()) if str(item or "").strip()),
                motion=motion,
            )
            self._latest_speech_context = self._latest_speech_context.merged(legacy_context)
        raw_affect = payload.get("affect")
        if isinstance(raw_affect, Mapping):
            self._handle_affect_event({"data": {"affect": raw_affect, "source": "legacy"}})

    @staticmethod
    def _speech_context_key(payload: Mapping[str, Any]) -> tuple[str, int]:
        turn_id = str(payload.get("turn_id", "") or "")
        try:
            seq = int(payload.get("seq", payload.get("segment_id", 0)) or 0)
        except (TypeError, ValueError):
            seq = 0
        return turn_id, seq

    def _remember_speech_context(
        self,
        payload: Mapping[str, Any],
        context: SpeechContext,
    ) -> None:
        has_surface_data = bool(
            context.text
            or context.actions
            or context.motion
            or context.speech_act
            or (context.prosody_style and context.prosody_style != "neutral")
        )
        if not has_surface_data:
            return
        key = self._speech_context_key(payload)
        previous = self._speech_contexts.get(key, SpeechContext())
        merged = previous.merged(context)
        self._speech_contexts[key] = merged
        self._latest_speech_context = merged
        self._latest_speech_key = key
        # Cache chỉ để nối stage_segment với audio_start. Giữ vô hạn vì vài chuỗi
        # text ngắn là một cách rất chậm để phát minh memory leak.
        while len(self._speech_contexts) > 24:
            oldest = next(iter(self._speech_contexts))
            self._speech_contexts.pop(oldest, None)

    def _resolve_speech_context(
        self,
        payload: Mapping[str, Any],
        event_context: SpeechContext,
    ) -> SpeechContext:
        key = self._speech_context_key(payload)
        cached = self._speech_contexts.get(key)
        if cached is not None:
            resolved = cached.merged(event_context)
        elif not key[0] or key[0] == self._latest_speech_key[0]:
            resolved = self._latest_speech_context.merged(event_context)
        else:
            # Turn mới không được thừa kế wink/look-away của turn trước chỉ vì
            # audio event đến thiếu stage_segment tương ứng. Kịch câm có giới hạn.
            resolved = event_context
        self._speech_contexts[key] = resolved
        self._latest_speech_context = resolved
        self._latest_speech_key = key
        return resolved

    @staticmethod
    def _normalize_emotion(value: str) -> str:
        normalized = _EMOTION_ALIASES.get(value, value)
        return normalized if normalized in _EMOTION_TARGETS else "neutral"

    def advance(self, dt: float, *, now: float | None = None) -> Live2DSnapshot:
        """Tiến state một frame và trả parameter đã làm mượt."""

        timestamp = time.monotonic() if now is None else float(now)
        dt = max(0.0, min(0.1, float(dt)))
        with self._lock:
            affect_blend = 1.0 - math.exp(-1.8 * dt) if dt > 0.0 else 1.0
            self._affect_current = self._affect_current.lerp(self._affect_target, affect_blend)
            self._thought_pressure *= math.exp(-dt / 48.0) if dt > 0.0 else 1.0

            affect_age = max(0.0, timestamp - self._affect_last_at)
            if self._affect_last_at < 0.0:
                freshness = 0.0
            elif affect_age <= 75.0:
                freshness = 1.0
            else:
                freshness = math.exp(-(affect_age - 75.0) / 240.0)
            if self._speaking and timestamp >= self._speech_deadline:
                self._speaking = False
                self._speech.stop()
                self._speech_deadline = -1.0e9
                self._active_speech_key = ("", 0)

            visual_affect = NEUTRAL_AFFECT.lerp(self._affect_current, freshness)
            affect_pose = visual_affect.visual_pose(thought_pressure=self._thought_pressure)
            affect_target = ExpressionTarget.from_affect_pose(affect_pose)

            emotion_weight = self._explicit_emotion_weight(timestamp)
            expression = _EMOTION_TARGETS.get(self._emotion, _EMOTION_TARGETS["neutral"])
            composed = affect_target.blend(expression, emotion_weight)
            target = composed.parameters()

            motion_weight = self._motion_weight(timestamp)
            for key, value in _MOTION_OFFSETS.get(self._motion, {}).items():
                target[key] = target.get(key, 0.0) + value * motion_weight

            # Nhịp thở sinh học tự nhiên: ~16 nhịp/phút, hơi thở nhanh hơn nhẹ khi arousal cao
            breath_speed = 1.65 + visual_affect.arousal * 0.45
            target["ParamBreath"] = 0.5 + 0.5 * math.sin(timestamp * breath_speed)

            allow_idle_gesture = (
                not self._speaking
                and self._motion in {"stand", "idle"}
                and emotion_weight < 0.72
            )
            idle_active = not self._speaking
            idle_pose = self._idle.advance(
                now=timestamp,
                active=idle_active,
                allow_gesture=allow_idle_gesture,
                affect=visual_affect,
                thought_pressure=self._thought_pressure,
                motion=self._motion,
            )
            idle_scale = 0.42 if self._thinking else 1.0
            target["ParamAngleX"] += idle_pose.angle_x * idle_scale
            target["ParamAngleZ"] += idle_pose.angle_z * idle_scale
            target["ParamBrowLAngle"] += idle_pose.brow_left * idle_scale
            target["ParamBrowRAngle"] += idle_pose.brow_right * idle_scale
            target["ParamMouthForm"] += idle_pose.mouth_form * idle_scale

            speech_pose = self._speech.sample(now=timestamp) if self._speaking else None
            if speech_pose is not None:
                target["ParamAngleX"] += speech_pose.angle_x
                target["ParamAngleZ"] += speech_pose.angle_z
                target["ParamBrowLAngle"] += speech_pose.brow_left
                target["ParamBrowRAngle"] += speech_pose.brow_right
                # MouthForm giữ mood của mặt; MouthOpenY xử lý độ há riêng.
                # Hai trục được Cubism phối trên cùng lưới keyform, nên runtime
                # chỉ cộng style cảm xúc nhỏ chứ không trung hòa mood theo âm lượng.
                target["ParamMouthForm"] += speech_pose.mouth_form_offset

            if self._speaking and speech_pose is not None:
                # Ghép hai trục miệng thành một pose duy nhất mỗi frame:
                # MouthForm giữ mood (-1 bực, 0 thường, +1 vui), MouthOpenY bám
                # voice (0 đóng, .5 nửa mở, 1 mở hết). Mood chỉ bias độ há rất
                # nhẹ để mặt vui/bực không mất nét khi đang nói.
                mouth_form, mouth_open = INFJ_PARAMETER_PROFILE.compose_speaking_mouth(
                    mood_form=target.get("ParamMouthForm", 0.0),
                    speech_ratio=speech_pose.mouth_open_ratio,
                )
            else:
                mouth_form = INFJ_PARAMETER_PROFILE.clamp_mouth_form(
                    target.get("ParamMouthForm", 0.0)
                )
                mouth_open = INFJ_PARAMETER_PROFILE.mouth_closed
            target["ParamMouthForm"] = mouth_form
            target["ParamMouthOpenY"] = mouth_open

            keys = set(self._current) | set(target)
            for key in keys:
                current = self._current.get(key, 0.0)
                desired = INFJ_PARAMETER_PROFILE.sanitize(key, target.get(key, 0.0))
                response = _PARAMETER_RESPONSE.get(key, 7.0)
                if key == "ParamMouthOpenY" and desired < current:
                    # Đóng nhanh hơn mở để giữa hai âm tiết thật sự có khe khép,
                    # nhưng không giật về 0 chỉ trong một frame.
                    response = 64.0
                blend = 1.0 - math.exp(-response * dt) if dt > 0.0 else 1.0
                self._current[key] = current + (desired - current) * blend

            speech_eye_left = speech_pose.eye_open_left_scale if speech_pose is not None else 1.0
            speech_eye_right = speech_pose.eye_open_right_scale if speech_pose is not None else 1.0
            desired_eye_left = composed.eye_open_left * idle_pose.eye_open_left_scale * speech_eye_left
            desired_eye_right = composed.eye_open_right * idle_pose.eye_open_right_scale * speech_eye_right
            if emotion_weight >= 0.5:
                if self._emotion in {"wink_correct", "teehee"}:
                    desired_eye_left = 0.0
                elif self._emotion in {"wink_correct_right", "teehee_right"}:
                    desired_eye_right = 0.0
            eye_blend = 1.0 - math.exp(-7.0 * dt) if dt > 0.0 else 1.0
            self._eye_open_left += (desired_eye_left - self._eye_open_left) * eye_blend
            self._eye_open_right += (desired_eye_right - self._eye_open_right) * eye_blend

            visible_emotion = self._emotion if emotion_weight >= 0.22 else visual_affect.dominant_label()
            return Live2DSnapshot(
                emotion=visible_emotion,
                motion=self._motion,
                speaking=self._speaking,
                parameters=dict(self._current),
                eye_open_left=INFJ_PARAMETER_PROFILE.clamp_eye_open(self._eye_open_left),
                eye_open_right=INFJ_PARAMETER_PROFILE.clamp_eye_open(self._eye_open_right),
                gaze_bias_x=INFJ_PARAMETER_PROFILE.clamp_eye_axis(
                    affect_pose.gaze_bias_x
                    + idle_pose.gaze_bias_x * idle_scale
                    + (speech_pose.gaze_bias_x if speech_pose is not None else 0.0)
                ),
                gaze_bias_y=INFJ_PARAMETER_PROFILE.clamp_eye_axis(
                    affect_pose.gaze_bias_y
                    + idle_pose.gaze_bias_y * idle_scale
                    + (speech_pose.gaze_bias_y if speech_pose is not None else 0.0)
                ),
                affect=visual_affect.to_dict(),
                affect_source=self._affect_source,
                idle_action=idle_pose.action,
                explicit_emotion_weight=emotion_weight,
                last_event_type=self._last_event_type,
                last_event_at=self._last_event_at,
                mouth_open_ratio=(speech_pose.mouth_open_ratio if speech_pose is not None else 0.0),
                mouth_shape=(speech_pose.mouth_shape if speech_pose is not None else 0.0),
                speech_cue=(speech_pose.cue if speech_pose is not None else "silent"),
            )

    def _explicit_emotion_weight(self, timestamp: float) -> float:
        if self._emotion_at < 0.0:
            return 0.0
        age = max(0.0, timestamp - self._emotion_at)
        if self._speaking:
            return 0.92
        if self._thinking:
            return max(0.58, math.exp(-age / 18.0))
        # Emotion của lời thoại còn dư âm rồi tan dần về affect nền.
        return math.exp(-age / 12.0)

    def _motion_weight(self, timestamp: float) -> float:
        if self._motion in {"stand", "idle"}:
            return 1.0
        if self._thinking and self._motion in {"thinking_small", "read"}:
            return 1.0
        age = max(0.0, timestamp - self._motion_at)
        if self._speaking:
            return 1.0
        return math.exp(-age / 2.8)
