# -*- coding: utf-8 -*-
"""Policy knobs for low-level surface speech.

Module này chỉ quyết định *cách sinh* câu thấp tầng: im, body-only, normal LLM,
hay force LLM. Nó không viết câu public. Nếu policy mà cũng viết lời thì repo lại
thêm một cái miệng phụ, và ta đều biết chuyện đó kết thúc bằng log rất xấu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Mapping


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    if numeric != numeric:
        numeric = default
    return max(0.0, min(1.0, numeric))


def _int_from_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(minimum, int(raw))
    except ValueError:
        return max(minimum, int(default))


def _float_from_env(name: str, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        numeric = float(raw)
    except ValueError:
        numeric = float(default)
    return max(minimum, min(maximum, numeric))


def _tuple_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _tuple_from_value(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        values = tuple(item.strip() for item in value.split(",") if item.strip())
        return values or default
    if isinstance(value, (list, tuple, set)):
        values = tuple(str(item).strip() for item in value if str(item).strip())
        return values or default
    return default


def _parse_scalar(value: str) -> Any:
    text = value.strip().strip('"').strip("'")
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    try:
        if any(ch in text for ch in (".", "e", "E")):
            return float(text)
        return int(text)
    except ValueError:
        return text


def _load_simple_yaml(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load một file YAML rất nhỏ mà không bắt buộc PyYAML.

    Hỗ trợ đủ cho config tuning:
      key: value
      key: [a, b, c]
      key:
        - a
        - b

    Nếu PyYAML có sẵn thì dùng luôn. Nếu không, parser tối giản này tránh kéo thêm
    dependency chỉ để đọc vài threshold. Loài người đã có đủ dependency đau đầu.
    """
    file_path = Path(path)
    if not file_path.exists():
        return {}
    raw = file_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(raw) or {}
        return dict(loaded) if isinstance(loaded, Mapping) else {}
    except Exception:
        pass

    data: dict[str, Any] = {}
    current_key = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and current_key:
            value = _parse_scalar(stripped[1:].strip())
            current = data.setdefault(current_key, [])
            if isinstance(current, list):
                current.append(value)
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if not value:
            data[key] = []
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = tuple(_parse_scalar(item) for item in inner.split(",") if item.strip())
        else:
            data[key] = _parse_scalar(value)
    return data


def _default_policy_config_path() -> Path:
    # Repo thật thường là <repo>/src/cortex/... nên config nằm ở <repo>/config.
    # Bản zip debug đôi khi chỉ chứa nội dung src, khi đó config có thể nằm cạnh
    # các package ở <src>/config. Tìm cả hai để người dùng không phải thờ Pathlib.
    here = Path(__file__).resolve()
    candidates = (
        here.parents[2] / "config" / "low_level_speech_policy.yaml",
        here.parents[1] / "config" / "low_level_speech_policy.yaml",
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


@dataclass(slots=True)
class LowLevelSpeechPolicyConfig:
    """Config dễ tune cho SurfaceArbiter.

    Có thể override bằng env:
    - ERIS_SURFACE_POLICY_CONFIG: đường dẫn YAML tùy chọn
    - ERIS_SURFACE_FORCE_LLM_AFTER_SILENT
    - ERIS_SURFACE_HIGH_CONF_THRESHOLD
    - ERIS_SURFACE_MIN_CONFIDENCE
    - ERIS_SURFACE_BODY_ONLY_MIN_CONFIDENCE
    - ERIS_SURFACE_ALWAYS_LLM_KINDS
    - ERIS_SURFACE_MIN_INTERVAL_SEC
    - ERIS_SURFACE_FAMILY_COOLDOWN_SEC
    - ERIS_SURFACE_MAX_BODY_ONLY_STREAK
    - ERIS_SURFACE_PRESENCE_FORCE_LLM_AFTER_SILENT
    - ERIS_SURFACE_PRESENCE_HIGH_CONF_THRESHOLD
    - ERIS_SURFACE_PRESENCE_MIN_CONFIDENCE
    - ERIS_SURFACE_PRESENCE_BODY_ONLY_MIN_CONFIDENCE
    - ERIS_SURFACE_PRESENCE_VITAL_FORCE_LLM_THRESHOLD
    - ERIS_SURFACE_PRESENCE_ALWAYS_LLM_KINDS
    """

    # Global path: dùng cho thought_stream / inner_whim / random_chatter / các impulse không thuộc presence.
    # Global mặc định bảo thủ hơn để đừng kéo mọi impulse vào LLM chỉ vì im lâu.
    force_llm_after_silent: int = 6
    high_conf_threshold: float = 0.58
    min_surface_confidence: float = 0.42
    body_only_min_confidence: float = 0.30
    normal_llm_probability: float = 1.0
    min_surface_interval_seconds: float = 12.0
    family_cooldown_seconds: float = 90.0
    max_body_only_streak: int = 2
    body_only_after_llm_decline: bool = True
    vital_pressure_force_llm_threshold: float = 0.62
    always_llm_kinds: tuple[str, ...] = field(
        default_factory=lambda: (
            # Sensor/action-like routes may still deserve a global force path after upstream gates.
            "blind_frustration",
            "technical_help",
        )
    )

    # Presence path: dùng riêng cho micro/re-engagement/continuity/memory-resonance.
    # Đây là cửa hẹp cho hiện diện êm, không phải giấy phép cho mọi thought_stream lên tiếng.
    presence_force_llm_after_silent: int = 4
    presence_high_conf_threshold: float = 0.48
    presence_min_confidence: float = 0.34
    presence_body_only_min_confidence: float = 0.22
    presence_vital_force_llm_threshold: float = 0.52
    presence_always_llm_kinds: tuple[str, ...] = field(
        default_factory=lambda: (
            "presence_maintenance",
            "micro_presence",
            "re_engagement",
            "reengagement",
            "continuity_presence",
            "memory_resonance",
        )
    )

    @classmethod
    def from_env(cls) -> "LowLevelSpeechPolicyConfig":
        base = cls()
        path = os.environ.get("ERIS_SURFACE_POLICY_CONFIG", "").strip()
        if not path:
            default_path = _default_policy_config_path()
            if default_path.exists():
                path = str(default_path)
        if path:
            base = cls.from_mapping(_load_simple_yaml(path), use_env=False)
        env_data = {
            "force_llm_after_silent": _int_from_env("ERIS_SURFACE_FORCE_LLM_AFTER_SILENT", base.force_llm_after_silent, minimum=1),
            "high_conf_threshold": _float_from_env("ERIS_SURFACE_HIGH_CONF_THRESHOLD", base.high_conf_threshold),
            "min_surface_confidence": _float_from_env("ERIS_SURFACE_MIN_CONFIDENCE", base.min_surface_confidence),
            "body_only_min_confidence": _float_from_env("ERIS_SURFACE_BODY_ONLY_MIN_CONFIDENCE", base.body_only_min_confidence),
            "normal_llm_probability": _float_from_env("ERIS_SURFACE_NORMAL_LLM_PROBABILITY", base.normal_llm_probability),
            "min_surface_interval_seconds": _float_from_env("ERIS_SURFACE_MIN_INTERVAL_SEC", base.min_surface_interval_seconds, maximum=3600.0),
            "family_cooldown_seconds": _float_from_env("ERIS_SURFACE_FAMILY_COOLDOWN_SEC", base.family_cooldown_seconds, maximum=3600.0),
            "max_body_only_streak": _int_from_env("ERIS_SURFACE_MAX_BODY_ONLY_STREAK", base.max_body_only_streak, minimum=1),
            "body_only_after_llm_decline": _bool_value(os.environ.get("ERIS_SURFACE_BODY_ONLY_AFTER_LLM_DECLINE"), base.body_only_after_llm_decline),
            "vital_pressure_force_llm_threshold": _float_from_env("ERIS_SURFACE_VITAL_FORCE_LLM_THRESHOLD", base.vital_pressure_force_llm_threshold),
            "always_llm_kinds": _tuple_from_env("ERIS_SURFACE_ALWAYS_LLM_KINDS", base.always_llm_kinds),
            "presence_force_llm_after_silent": _int_from_env("ERIS_SURFACE_PRESENCE_FORCE_LLM_AFTER_SILENT", base.presence_force_llm_after_silent, minimum=1),
            "presence_high_conf_threshold": _float_from_env("ERIS_SURFACE_PRESENCE_HIGH_CONF_THRESHOLD", base.presence_high_conf_threshold),
            "presence_min_confidence": _float_from_env("ERIS_SURFACE_PRESENCE_MIN_CONFIDENCE", base.presence_min_confidence),
            "presence_body_only_min_confidence": _float_from_env("ERIS_SURFACE_PRESENCE_BODY_ONLY_MIN_CONFIDENCE", base.presence_body_only_min_confidence),
            "presence_vital_force_llm_threshold": _float_from_env("ERIS_SURFACE_PRESENCE_VITAL_FORCE_LLM_THRESHOLD", base.presence_vital_force_llm_threshold),
            "presence_always_llm_kinds": _tuple_from_env("ERIS_SURFACE_PRESENCE_ALWAYS_LLM_KINDS", base.presence_always_llm_kinds),
        }
        return cls.from_mapping(env_data, use_env=False)

    @classmethod
    def from_yaml_file(cls, path: str | os.PathLike[str]) -> "LowLevelSpeechPolicyConfig":
        return cls.from_mapping(_load_simple_yaml(path), use_env=True)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None, *, use_env: bool = True) -> "LowLevelSpeechPolicyConfig":
        if not data:
            return cls.from_env() if use_env else cls()
        base = cls.from_env() if use_env else cls()
        always = _tuple_from_value(data.get("always_llm_kinds"), base.always_llm_kinds)
        presence_always = _tuple_from_value(data.get("presence_always_llm_kinds"), base.presence_always_llm_kinds)
        return cls(
            force_llm_after_silent=max(1, int(data.get("force_llm_after_silent", base.force_llm_after_silent))),
            high_conf_threshold=_clamp01(data.get("high_conf_threshold", base.high_conf_threshold), base.high_conf_threshold),
            min_surface_confidence=_clamp01(data.get("min_surface_confidence", base.min_surface_confidence), base.min_surface_confidence),
            body_only_min_confidence=_clamp01(data.get("body_only_min_confidence", base.body_only_min_confidence), base.body_only_min_confidence),
            normal_llm_probability=_clamp01(data.get("normal_llm_probability", base.normal_llm_probability), base.normal_llm_probability),
            min_surface_interval_seconds=max(0.0, float(data.get("min_surface_interval_seconds", base.min_surface_interval_seconds))),
            family_cooldown_seconds=max(0.0, float(data.get("family_cooldown_seconds", base.family_cooldown_seconds))),
            max_body_only_streak=max(1, int(data.get("max_body_only_streak", base.max_body_only_streak))),
            body_only_after_llm_decline=_bool_value(data.get("body_only_after_llm_decline"), base.body_only_after_llm_decline),
            vital_pressure_force_llm_threshold=_clamp01(data.get("vital_pressure_force_llm_threshold", base.vital_pressure_force_llm_threshold), base.vital_pressure_force_llm_threshold),
            always_llm_kinds=always,
            presence_force_llm_after_silent=max(1, int(data.get("presence_force_llm_after_silent", base.presence_force_llm_after_silent))),
            presence_high_conf_threshold=_clamp01(data.get("presence_high_conf_threshold", base.presence_high_conf_threshold), base.presence_high_conf_threshold),
            presence_min_confidence=_clamp01(data.get("presence_min_confidence", base.presence_min_confidence), base.presence_min_confidence),
            presence_body_only_min_confidence=_clamp01(data.get("presence_body_only_min_confidence", base.presence_body_only_min_confidence), base.presence_body_only_min_confidence),
            presence_vital_force_llm_threshold=_clamp01(data.get("presence_vital_force_llm_threshold", base.presence_vital_force_llm_threshold), base.presence_vital_force_llm_threshold),
            presence_always_llm_kinds=presence_always,
        )


class LowLevelSpeechPolicy:
    """Quyết định generation mode cho utterance thấp tầng.

    Trả về một trong:
    - reject: không surface
    - body_only: chỉ motion nhỏ
    - normal_llm: gọi LLM nếu generator rảnh/được phép
    - force_llm: cue đủ mạnh, no-fallback vẫn phải thử LLM
    """

    def __init__(self, config: LowLevelSpeechPolicyConfig | Mapping[str, Any] | str | os.PathLike[str] | None = None) -> None:
        if isinstance(config, LowLevelSpeechPolicyConfig):
            self.config = config
        elif isinstance(config, (str, os.PathLike)):
            self.config = LowLevelSpeechPolicyConfig.from_yaml_file(config)
        else:
            self.config = LowLevelSpeechPolicyConfig.from_mapping(config)

    def _is_presence_kind(self, kind: str) -> bool:
        return kind in set(self.config.presence_always_llm_kinds)

    def decide_generation_mode(self, impulse, silent_cycles: int, *, speak_bias: float = 0.0) -> str:
        if impulse is None or not bool(getattr(impulse, "should_surface", False)):
            return "reject"

        confidence = _clamp01(getattr(impulse, "confidence", 0.0))
        kind = str(getattr(impulse, "kind", "") or "").strip()
        vital = _clamp01(getattr(impulse, "vital_pressure", 0.0))

        # speak_bias (signed, từ ExpressionEnvelope): dương → ERIS đang muốn nói
        # (im lâu + còn năng lượng) nên ta nâng confidence hiệu dụng để hạ ngưỡng;
        # âm → đang kìm/mệt nên siết lại. Kẹp để không phá thang đo.
        try:
            bias = max(-0.15, min(0.20, float(speak_bias)))
        except (TypeError, ValueError):
            bias = 0.0
        if bias:
            confidence = _clamp01(confidence + bias)

        # Presence-like impulses get their own threshold lane.
        # This lets long silence become a tiny speakable line without making
        # random_chatter / thought_stream_surface inherit the same easy path.
        if self._is_presence_kind(kind):
            if confidence < self.config.presence_body_only_min_confidence and vital < 0.42:
                return "reject"
            if (
                silent_cycles >= self.config.presence_force_llm_after_silent
                or vital >= self.config.presence_vital_force_llm_threshold
                or confidence >= self.config.presence_high_conf_threshold
            ):
                return "force_llm"
            if confidence >= self.config.presence_min_confidence:
                return "force_llm"
            if confidence >= self.config.presence_body_only_min_confidence or vital >= 0.42:
                return "normal_llm"
            return "body_only"

        # Global non-presence path stays more conservative.
        if confidence < self.config.body_only_min_confidence and vital < 0.52:
            return "reject"
        if silent_cycles >= self.config.force_llm_after_silent or vital >= self.config.vital_pressure_force_llm_threshold:
            return "force_llm"
        if kind in set(self.config.always_llm_kinds) and confidence >= self.config.min_surface_confidence:
            return "force_llm"
        if confidence >= self.config.high_conf_threshold:
            return "force_llm"
        if confidence >= self.config.min_surface_confidence or vital >= 0.48:
            return "normal_llm"
        return "body_only"
