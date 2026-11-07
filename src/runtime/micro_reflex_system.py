# -*- coding: utf-8 -*-
"""runtime/micro_reflex_system.py
=================================
Hệ thống Hiện diện Vi mô & Phản xạ Thân thể (Eris Micro-Presence & Bio-Reflex Subsystem).

Đặc điểm kiến trúc:
1. Hoàn toàn cục bộ (< 20ms), 0 token, không gọi LLM/ElevenLabs lúc runtime.
2. Quản lý 4 nhóm vi mô:
   - INTAKE: Phản xạ tức thì khi user gửi tin nhắn (ừm, hủh, huhu, hứ, hehe, haizz).
   - AMBIENT: Phản xạ sinh học lúc rảnh (thở dài, ngâm nga vu vơ, ngáp nhẹ ban đêm).
   - COGNITIVE_LEAKAGE: Rò rỉ nhận thức từ Não ngầm (ngẫm nghĩ dở, a/ồ ngộ ra).
   - GROUNDING: Phản xạ môi trường (quay lại cửa sổ, clipboard code).
3. Hợp đồng chuẩn hóa ``MicroBehaviorPacket`` đồng bộ đa kênh:
   - Audio PCM WAV (24kHz mono)
   - Envelope mở miệng LipSync (40ms RMS)
   - Chuyển động hình thể Live2D (nod_small, tilt_head, shake_head_small...)
   - Biểu cảm khuôn mặt & Offset tham số (ParamCheek, ParamEyeHighlight...)
4. Tránh lặp lại bằng Refractory Period và Cooldown co giãn theo nhịp điệu (Cadence).
"""

from __future__ import annotations

import io
import math
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from runtime.audio_lip_sync import build_lip_sync_envelope


class MicroCategory(str, Enum):
    """4 chiều phân loại của hệ thống vi mô."""
    INTAKE = "INTAKE"                      # Phản xạ tức thì khi user chat
    AMBIENT = "AMBIENT"                    # Phản xạ sinh học khi nhàn rỗi / không chat
    COGNITIVE_LEAKAGE = "COGNITIVE_LEAK"  # Rò rỉ nhận thức từ Private Mind
    GROUNDING = "GROUNDING"                # Phản xạ theo sự kiện môi trường


class MicroActKind(str, Enum):
    """Tập hữu hạn các loại vi phản xạ (Micro Acts)."""
    # Nhóm A: Intake / User Chat
    RECEIPT = "RECEIPT"                    # Lắng nghe ấm áp ("Ừm.", tiếng thở nhẹ)
    REPAIR_REQUEST = "REPAIR_REQUEST"      # Tò mò, thắc mắc ("Hủh?", "Ểh?")
    PROMPT_CONTINUE = "PROMPT_CONTINUE"    # Mời kể tiếp ("Rồi sao?")
    STOP_ACK = "STOP_ACK"                  # Xác nhận dừng ("Ừ, khoan.")
    CONTINUE_ACK = "CONTINUE_ACK"          # Nhường sàn nói ("Ừ, nói tiếp đi.")
    PLAYFUL_CRY = "PLAYFUL_CRY"            # Giả khóc, mè nheo ("Huhu...", "Hức...")
    PLAYFUL_CHUCKLE = "PLAYFUL_CHUCKLE"    # Cười khúc khích ("Hehe", "Hihi")
    SULK = "SULK"                          # Giả dỗi, phồng má ("Hứ...")
    DEADPAN = "DEADPAN"                    # Cạn lời, thở dài giả bộ ("Haizz...")

    # Nhóm B: Ambient / Vegetative
    AMBIENT_SIGH = "AMBIENT_SIGH"          # Thở dài thư giãn lúc rảnh
    AMBIENT_HUM = "AMBIENT_HUM"            # Ngâm nga vu vơ
    AMBIENT_POSTURE_RESET = "AMBIENT_POSTURE_RESET"  # Đổi thế ngồi, thở khẽ
    AMBIENT_DROWSY = "AMBIENT_DROWSY"      # Ngáp nhẹ, uể oải lúc muộn

    # Nhóm C: Cognitive Leakage
    LEAK_PONDER = "LEAK_PONDER"            # "Ừm... hửm...", mắt liếc ngẫm nghĩ
    LEAK_EUREKA = "LEAK_EUREKA"            # "A...", mắt lóe sáng ngộ ra
    LEAK_TENSION = "LEAK_TENSION"          # Thở ngắn / tặc lưỡi nhẹ

    # Nhóm D: Grounding
    USER_RETURN = "USER_RETURN"            # "Hửm?", ngước nhìn đón user
    CODE_CLIPBOARD = "CODE_CLIPBOARD"      # "Ồ...", ngạc nhiên nhỏ


@dataclass(frozen=True, slots=True)
class MicroBehaviorPacket:
    """Gói hợp đồng dữ liệu chuẩn hóa của một phản xạ vi mô."""
    act_kind: str
    category: str
    surface_text: str
    audio_path: str
    audio_bytes: bytes | None
    audio_envelope: tuple[float, ...] = ()
    audio_frame_ms: int = 40
    audio_duration_ms: int = 0
    motion_tag: str = "nod_small"
    emotion_preset: str = "warm_soft"
    cue_tags: tuple[str, ...] = ("soft_breath",)
    live2d_param_offsets: dict[str, float] = field(default_factory=dict)
    display_bubble: bool = False
    interruptible: bool = True
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.micro_behavior_packet.v1",
            "act_kind": self.act_kind,
            "category": self.category,
            "surface_text": self.surface_text,
            "audio_path": self.audio_path,
            "has_audio": bool(self.audio_bytes),
            "audio_frame_ms": self.audio_frame_ms,
            "audio_duration_ms": self.audio_duration_ms,
            "motion_tag": self.motion_tag,
            "emotion_preset": self.emotion_preset,
            "cue_tags": list(self.cue_tags),
            "live2d_param_offsets": dict(self.live2d_param_offsets),
            "display_bubble": self.display_bubble,
            "interruptible": self.interruptible,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class MicroActSpec:
    """Đặc tả hành vi và biểu diễn của một MicroAct."""
    category: MicroCategory
    vocal_subdirs: tuple[str, ...]
    motion: str
    emotion: str
    cue: str
    default_surface: str
    cooldown_seconds: float
    param_offsets: dict[str, float] = field(default_factory=dict)


# Bảng tra đặc tả chuẩn cho từng hành vi vi mô
_ACT_SPECS: dict[MicroActKind, MicroActSpec] = {
    # ── Nhóm A: Intake ────────────────────────────────────────────────────────
    MicroActKind.RECEIPT: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("voice_director_soft_hum", "voice_director_soft_sigh"),
        motion="nod_small",
        emotion="warm_soft",
        cue="soft_breath",
        default_surface="Ừm.",
        cooldown_seconds=3.0,
        param_offsets={"ParamBreath": 0.2},
    ),
    MicroActKind.REPAIR_REQUEST: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("curious_hm",),
        motion="tilt_head",
        emotion="curious",
        cue="curious",
        default_surface="Hủh?",
        cooldown_seconds=6.0,
        param_offsets={"ParamEyeHighlight": 1.0},
    ),
    MicroActKind.PROMPT_CONTINUE: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("curious_hm", "voice_director_soft_hum"),
        motion="lean_in_small",
        emotion="curious",
        cue="nod_small",
        default_surface="Rồi sao?",
        cooldown_seconds=5.0,
    ),
    MicroActKind.STOP_ACK: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("voice_director_soft_sigh",),
        motion="blink_slow",
        emotion="neutral",
        cue="soft_breath",
        default_surface="Ừ, khoan.",
        cooldown_seconds=4.0,
    ),
    MicroActKind.CONTINUE_ACK: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("voice_director_soft_hum",),
        motion="nod_small",
        emotion="warm_soft",
        cue="nod_small",
        default_surface="Ừ, nói tiếp đi.",
        cooldown_seconds=4.0,
    ),
    MicroActKind.PLAYFUL_CRY: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("fake_cry_refusal",),
        motion="shake_head_small",
        emotion="sad_empathy",
        cue="soft_breath",
        default_surface="Huhu...",
        cooldown_seconds=45.0,
        param_offsets={"ParamCheek": 0.45, "ParamEyeHighlight": 0.8},
    ),
    MicroActKind.PLAYFUL_CHUCKLE: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("playful_laugh",),
        motion="nod_small",
        emotion="teehee",
        cue="wink",
        default_surface="Hehe.",
        cooldown_seconds=30.0,
        param_offsets={"ParamCheek": 0.30},
    ),
    MicroActKind.SULK: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("fake_sulk",),
        motion="tilt_head",
        emotion="smirk_minor",
        cue="slow_blink",
        default_surface="Hứ.",
        cooldown_seconds=40.0,
        param_offsets={"ParamCheek": 0.35},
    ),
    MicroActKind.DEADPAN: MicroActSpec(
        category=MicroCategory.INTAKE,
        vocal_subdirs=("fake_annoyed_deadpan",),
        motion="blink_slow",
        emotion="lazy_deadpan",
        cue="slow_blink",
        default_surface="Haizz.",
        cooldown_seconds=35.0,
        param_offsets={"ParamEyeHighlight": 0.0},
    ),

    # ── Nhóm B: Ambient ───────────────────────────────────────────────────────
    MicroActKind.AMBIENT_SIGH: MicroActSpec(
        category=MicroCategory.AMBIENT,
        vocal_subdirs=("voice_director_soft_sigh",),
        motion="look_down_soft",
        emotion="warm_soft",
        cue="soft_breath",
        default_surface="",
        cooldown_seconds=60.0,
        param_offsets={"ParamBreath": 0.3},
    ),
    MicroActKind.AMBIENT_HUM: MicroActSpec(
        category=MicroCategory.AMBIENT,
        vocal_subdirs=("voice_director_soft_hum",),
        motion="soften",
        emotion="warm_soft",
        cue="nod_small",
        default_surface="",
        cooldown_seconds=75.0,
        param_offsets={"ParamCheek": 0.15},
    ),
    MicroActKind.AMBIENT_POSTURE_RESET: MicroActSpec(
        category=MicroCategory.AMBIENT,
        vocal_subdirs=("voice_director_soft_sigh",),
        motion="tilt_right",
        emotion="calm",
        cue="slow_blink",
        default_surface="",
        cooldown_seconds=90.0,
    ),
    MicroActKind.AMBIENT_DROWSY: MicroActSpec(
        category=MicroCategory.AMBIENT,
        vocal_subdirs=("voice_director_soft_sigh", "fake_annoyed_deadpan"),
        motion="slow_blink",
        emotion="lazy_deadpan",
        cue="soft_breath",
        default_surface="",
        cooldown_seconds=120.0,
    ),

    # ── Nhóm C: Cognitive Leakage ─────────────────────────────────────────────
    MicroActKind.LEAK_PONDER: MicroActSpec(
        category=MicroCategory.COGNITIVE_LEAKAGE,
        vocal_subdirs=("curious_hm", "voice_director_soft_hum"),
        motion="ponder",
        emotion="curious",
        cue="curious",
        default_surface="",
        cooldown_seconds=50.0,
    ),
    MicroActKind.LEAK_EUREKA: MicroActSpec(
        category=MicroCategory.COGNITIVE_LEAKAGE,
        vocal_subdirs=("curious_hm",),
        motion="nod_small",
        emotion="surprised_soft",
        cue="wink",
        default_surface="",
        cooldown_seconds=60.0,
        param_offsets={"ParamEyeHighlight": 1.0, "ParamCheek": 0.2},
    ),
    MicroActKind.LEAK_TENSION: MicroActSpec(
        category=MicroCategory.COGNITIVE_LEAKAGE,
        vocal_subdirs=("fake_annoyed_deadpan", "voice_director_soft_sigh"),
        motion="shake_small",
        emotion="concerned",
        cue="slow_blink",
        default_surface="",
        cooldown_seconds=60.0,
    ),

    # ── Nhóm D: Grounding ─────────────────────────────────────────────────────
    MicroActKind.USER_RETURN: MicroActSpec(
        category=MicroCategory.GROUNDING,
        vocal_subdirs=("curious_hm", "voice_director_soft_hum"),
        motion="look_up_soft",
        emotion="warm_soft",
        cue="curious",
        default_surface="",
        cooldown_seconds=45.0,
    ),
    MicroActKind.CODE_CLIPBOARD: MicroActSpec(
        category=MicroCategory.GROUNDING,
        vocal_subdirs=("curious_hm",),
        motion="ponder_right",
        emotion="focused",
        cue="curious",
        default_surface="",
        cooldown_seconds=60.0,
    ),
}


# Regex nhận diện cảm xúc nhanh từ tin nhắn user
_QUESTION_ONLY_RE = re.compile(
    r"^(\?+|ủa\??|hả\??|hủh\??|ểh\??|hở\??|sao\??|gì cơ\??|gì dạ\??|gì thế\??)$",
    re.I,
)
_LAUGH_RE = re.compile(
    r"(?:\b(?:ha(?:ha)+|he(?:he)+|hi(?:hi)+|kaka|kak+a|cười|lol|lmao|nhộn|hài quá)\b)",
    re.I,
)
_CRY_RE = re.compile(
    r"(?:\b(?:hu(?:hu)+|hức|khóc|bắt nạt|mắng|tội nghiệp|dỗi rồi|mếu|ác độc)\b)",
    re.I,
)
_SULK_RE = re.compile(
    r"(?:\b(?:hứ|hừ|chê|xấu tính|đáng ghét|dở ẹc|ngốc|lì lợm|bướng|ghét)\b)",
    re.I,
)
_DEADPAN_RE = re.compile(
    r"(?:\b(?:haizz|chán|cạn lời|mệt mỏi|bó tay|bó chiếu|ngu ngơ|ngáo)\b)",
    re.I,
)
_OPEN_STORY_TRAILERS = (
    "...", "…", "xong rồi thì", "thế là", "lúc đó", "biết sao không", "đoán xem", "chuyện là", "hôm qua mới",
)


class MicroReflexEngine:
    """Bộ điều phối và biên dịch toàn bộ hành vi vi mô của Eris."""

    def __init__(
        self,
        *,
        cache_root: Path | str | None = None,
        clock: Any = None,
        logger: Any = None,
    ) -> None:
        self.clock = clock or time.time
        self.logger = logger
        self._lock = threading.RLock()
        self._cache_root = self._resolve_cache_root(cache_root)
        self._last_emitted_by_kind: dict[str, float] = {}
        self._last_emitted_by_category: dict[str, float] = {}
        self._last_user_turn_at: float = 0.0
        self._recent_receipts: list[MicroBehaviorPacket] = []
        self._max_receipts = 32

    def _resolve_cache_root(self, root: Path | str | None) -> Path:
        if root is not None:
            p = Path(root)
            if p.is_dir():
                return p.resolve()
        raw = os.environ.get("ERIS_TTS_VOCAL_CACHE_DIR", "").strip()
        if raw:
            p = Path(raw)
            if p.is_dir():
                return p.resolve()
        cand1 = Path("voice_presets") / "tts_vocal_cache"
        if cand1.is_dir():
            return cand1.resolve()
        cand2 = Path(__file__).resolve().parent.parent.parent / "voice_presets" / "tts_vocal_cache"
        if cand2.is_dir():
            return cand2.resolve()
        return cand1

    def _get_cache_root(self) -> Path:
        raw = os.environ.get("ERIS_TTS_VOCAL_CACHE_DIR", "").strip()
        if raw:
            p = Path(raw)
            if p.is_dir():
                return p.resolve()
        return self._cache_root

    def can_emit(self, act_kind: MicroActKind | str, *, now: float | None = None) -> bool:
        """Kiểm tra cooldown xem loại phản xạ này có được phép phát không."""
        try:
            kind = act_kind if isinstance(act_kind, MicroActKind) else MicroActKind(str(getattr(act_kind, "value", act_kind)))
        except ValueError:
            return False
        spec = _ACT_SPECS.get(kind)
        if spec is None:
            return False

        ts = float(self.clock() if now is None else now)
        with self._lock:
            last_ts = self._last_emitted_by_kind.get(kind.value, 0.0)
            if (ts - last_ts) < spec.cooldown_seconds:
                return False
            # Cooldown danh mục (ví dụ ambient không phát dày hơn 35s)
            cat_cooldown = 35.0 if spec.category == MicroCategory.AMBIENT else 2.0
            last_cat_ts = self._last_emitted_by_category.get(spec.category.value, 0.0)
            if (ts - last_cat_ts) < cat_cooldown:
                return False
        return True

    def evaluate_intake(
        self,
        user_text: str,
        *,
        current_mood: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> MicroActKind:
        """Phân loại hành vi vi mô khi nhận tin nhắn từ user."""
        norm = str(user_text or "").strip().lower()

        # 1. Khớp theo mẫu câu đặc biệt
        if any(norm.endswith(t) for t in _OPEN_STORY_TRAILERS):
            return MicroActKind.PROMPT_CONTINUE
        if _QUESTION_ONLY_RE.match(norm):
            return MicroActKind.REPAIR_REQUEST
        if _CRY_RE.search(norm):
            return MicroActKind.PLAYFUL_CRY
        if _LAUGH_RE.search(norm):
            return MicroActKind.PLAYFUL_CHUCKLE
        if _SULK_RE.search(norm):
            return MicroActKind.SULK
        if _DEADPAN_RE.search(norm):
            return MicroActKind.DEADPAN

        # 2. Điều biến ngẫu nhiên theo tâm trạng nền (mischief, delight)
        mischief = float((current_mood or {}).get("mischief", 0.0) or 0.0)
        delight = float((current_mood or {}).get("delight", 0.0) or 0.0)
        roll = random.random()

        if mischief > 0.40 and roll < 0.20 and self.can_emit(MicroActKind.PLAYFUL_CHUCKLE, now=now):
            return MicroActKind.PLAYFUL_CHUCKLE
        if delight > 0.45 and roll < 0.25 and self.can_emit(MicroActKind.REPAIR_REQUEST, now=now):
            return MicroActKind.REPAIR_REQUEST

        return MicroActKind.RECEIPT

    def evaluate_ambient(
        self,
        idle_seconds: float,
        *,
        current_mood: Mapping[str, Any] | None = None,
        hour_of_day: int | None = None,
        now: float | None = None,
    ) -> MicroActKind | None:
        """Đánh giá phản xạ sinh học nhàn rỗi (khi user không chat trong thời gian dài)."""
        if idle_seconds < 45.0:
            return None

        hr = hour_of_day if hour_of_day is not None else time.localtime().tm_hour
        energy = float((current_mood or {}).get("energy", 0.5) or 0.5)
        delight = float((current_mood or {}).get("delight", 0.2) or 0.2)

        # Đêm muộn (> 23h hoặc < 5h) hoặc năng lượng rất thấp -> Ngáp uể oải
        if (hr >= 23 or hr < 5 or energy < 0.20) and self.can_emit(MicroActKind.AMBIENT_DROWSY, now=now):
            return MicroActKind.AMBIENT_DROWSY

        # Tâm trạng vui vẻ -> Ngâm nga vu vơ
        if delight > 0.38 and self.can_emit(MicroActKind.AMBIENT_HUM, now=now):
            return MicroActKind.AMBIENT_HUM

        # Đổi thế ngồi sau 90s
        if idle_seconds > 90.0 and self.can_emit(MicroActKind.AMBIENT_POSTURE_RESET, now=now):
            return MicroActKind.AMBIENT_POSTURE_RESET

        # Mặc định thở dài thư giãn
        if self.can_emit(MicroActKind.AMBIENT_SIGH, now=now):
            return MicroActKind.AMBIENT_SIGH

        return None

    def evaluate_cognitive_leakage(
        self,
        unexpressed_thoughts: list[Any] | None = None,
        *,
        tension: float = 0.0,
        eureka: bool = False,
        now: float | None = None,
    ) -> MicroActKind | None:
        """Đánh giá rò rỉ nhận thức từ Private Mind."""
        if eureka and self.can_emit(MicroActKind.LEAK_EUREKA, now=now):
            return MicroActKind.LEAK_EUREKA
        if tension > 0.45 and self.can_emit(MicroActKind.LEAK_TENSION, now=now):
            return MicroActKind.LEAK_TENSION
        if bool(unexpressed_thoughts):
            # Có mạch nghĩ ngầm đang dang dở -> "hửm...", ngẫm nghĩ
            if self.can_emit(MicroActKind.LEAK_PONDER, now=now):
                return MicroActKind.LEAK_PONDER
        return None

    def compile_packet(
        self,
        act_kind: MicroActKind | str,
        *,
        user_text: str = "",
        timestamp: float | None = None,
    ) -> MicroBehaviorPacket | None:
        """Biên dịch và đóng gói một MicroBehaviorPacket hoàn chỉnh."""
        try:
            kind = act_kind if isinstance(act_kind, MicroActKind) else MicroActKind(str(getattr(act_kind, "value", act_kind)))
        except ValueError:
            return None
        spec = _ACT_SPECS.get(kind)
        if spec is None:
            return None

        ts = float(self.clock() if timestamp is None else timestamp)
        wav_path, wav_bytes = self._pick_audio(spec.vocal_subdirs)

        lip_envelope: tuple[float, ...] = ()
        frame_ms = 40
        duration_ms = 0
        if wav_bytes:
            try:
                sync_obj = build_lip_sync_envelope(wav_bytes)
                lip_envelope = tuple(sync_obj.values)
                frame_ms = int(sync_obj.frame_ms)
                duration_ms = int(sync_obj.duration_ms)
            except Exception as exc:
                if self.logger:
                    self.logger.debug("[micro-reflex] lip sync envelope error: %s", exc)

        surface = spec.default_surface
        packet = MicroBehaviorPacket(
            act_kind=kind.value,
            category=spec.category.value,
            surface_text=surface,
            audio_path=str(wav_path or ""),
            audio_bytes=wav_bytes,
            audio_envelope=lip_envelope,
            audio_frame_ms=frame_ms,
            audio_duration_ms=duration_ms,
            motion_tag=spec.motion,
            emotion_preset=spec.emotion,
            cue_tags=(spec.cue,),
            live2d_param_offsets=dict(spec.param_offsets),
            display_bubble=False,
            interruptible=True,
            timestamp=ts,
        )

        with self._lock:
            self._last_emitted_by_kind[kind.value] = ts
            self._last_emitted_by_category[spec.category.value] = ts
            self._recent_receipts.append(packet)
            if len(self._recent_receipts) > self._max_receipts:
                del self._recent_receipts[:-self._max_receipts]

        return packet

    def _pick_audio(self, subdirs: tuple[str, ...]) -> tuple[Path | None, bytes | None]:
        """Chọn ngẫu nhiên một file WAV hợp lệ trong các thư mục chỉ định."""
        candidates: list[Path] = []
        cache_root = self._get_cache_root()
        for sub in subdirs:
            folder = cache_root / sub
            if folder.is_dir():
                candidates.extend(p for p in folder.glob("*.wav") if not p.name.endswith(".tmp"))

        if not candidates:
            return None, None
        chosen = random.choice(candidates)
        try:
            data = chosen.read_bytes()
            return chosen, data
        except Exception:
            return chosen, None

    def recent_receipts(self, limit: int = 5) -> tuple[MicroBehaviorPacket, ...]:
        with self._lock:
            return tuple(self._recent_receipts[-max(1, limit):])


# Singleton / Factory helper
_GLOBAL_ENGINE: MicroReflexEngine | None = None
_GLOBAL_LOCK = threading.RLock()


def get_micro_reflex_engine(cache_root: Path | str | None = None, logger: Any = None) -> MicroReflexEngine:
    global _GLOBAL_ENGINE
    if _GLOBAL_ENGINE is not None:
        return _GLOBAL_ENGINE
    with _GLOBAL_LOCK:
        if _GLOBAL_ENGINE is None:
            _GLOBAL_ENGINE = MicroReflexEngine(cache_root=cache_root, logger=logger)
        return _GLOBAL_ENGINE


__all__ = [
    "MicroCategory",
    "MicroActKind",
    "MicroBehaviorPacket",
    "MicroActSpec",
    "MicroReflexEngine",
    "get_micro_reflex_engine",
]
