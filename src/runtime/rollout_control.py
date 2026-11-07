from __future__ import annotations

"""Progressive delivery và kill-switch cho các lane rủi ro của ERIS.

Bốn mode:
- disabled: không chạy policy mới;
- dark: chỉ instrument classifier/decision thô;
- shadow: chạy đầy đủ nhưng không tác động output/state;
- canary: áp dụng cho một phần session ổn định;
- enabled: áp dụng toàn bộ.

Bucket canary dùng SHA-256 của session id nên cùng một session không nhảy qua lại
chỉ vì process vừa chớp mắt. Trái với vài rollout ngoài đời, ta cố tránh xổ số.
"""

import hashlib
import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from runtime.diagnostic_logging import safe_log


class RolloutMode(str, Enum):
    DISABLED = "disabled"
    DARK = "dark"
    SHADOW = "shadow"
    CANARY = "canary"
    ENABLED = "enabled"

    @classmethod
    def parse(cls, value: Any, default: "RolloutMode" = None) -> "RolloutMode":
        fallback = default or cls.ENABLED
        text = str(value or "").strip().lower()
        aliases = {
            "off": cls.DISABLED,
            "0": cls.DISABLED,
            "instrument": cls.DARK,
            "instrument_only": cls.DARK,
            "shadow_mode": cls.SHADOW,
            "on": cls.ENABLED,
            "1": cls.ENABLED,
            "full": cls.ENABLED,
        }
        if text in aliases:
            return aliases[text]
        try:
            return cls(text)
        except ValueError:
            return fallback


FEATURES = (
    "social_intercept",
    "permission_bypass",
    "adaptive_candidates",
    "vision_abstain",
    # Hardening flags trong báo cáo kiến trúc.
    "director_semantic_gate",
    "action_confirmation",
    "tts_language_pin",
    "tts_breaker",
    "memory_provenance_guard",
    "repair_state_ttl",
    "semantic_repeat_guard",
    "stage_final_sanitizer",
    "token_budget_all_lanes",
    "singing_capability",
)

_LEGACY_FLAG_ENV = {
    "director_semantic_gate": "ERIS_DIRECTOR_SEMANTIC_GATE",
    "action_confirmation": "ERIS_ACTION_CONFIRMATION_MODE",
    "tts_language_pin": "ERIS_TTS_LANGUAGE_PIN",
    "tts_breaker": "ERIS_TTS_BREAKER_ENABLED",
    "memory_provenance_guard": "ERIS_MEMORY_PROVENANCE_GUARD",
    "repair_state_ttl": "ERIS_REPAIR_STATE_TTL_ENABLED",
    "semantic_repeat_guard": "ERIS_SEMANTIC_REPEAT_GUARD",
    "stage_final_sanitizer": "ERIS_STAGE_FINAL_SANITIZER",
    "token_budget_all_lanes": "ERIS_TOKEN_BUDGET_ALL_LANES",
    "singing_capability": "ERIS_SINGING_CAPABILITY",
}

GLOBAL_ROLLOUT_CONTROLLER: "RolloutController | None" = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _feature_env_key(feature: str) -> str:
    return str(feature or "").strip().upper().replace("-", "_")


@dataclass(slots=True)
class FeatureRolloutConfig:
    feature: str
    mode: RolloutMode = RolloutMode.ENABLED
    canary_percent: float = 5.0
    kill_switch: bool = False
    min_samples: int = 20
    max_failure_rate: float = 0.15
    max_intrusion_rate: float = 0.10
    notes: str = ""

    @classmethod
    def from_env(cls, feature: str) -> "FeatureRolloutConfig":
        key = _feature_env_key(feature)
        explicit_mode = os.getenv(f"ERIS_ROLLOUT_{key}_MODE", "")
        if explicit_mode:
            mode = RolloutMode.parse(explicit_mode)
        else:
            legacy_name = _LEGACY_FLAG_ENV.get(feature, "")
            legacy_raw = str(os.getenv(legacy_name, "") or "").strip().lower() if legacy_name else ""
            # Singing mặc định off; các hardening guard khác mặc định on để giữ
            # tương thích với Phase 1-3.
            default_enabled = feature != "singing_capability"
            if legacy_name == "ERIS_ACTION_CONFIRMATION_MODE" and legacy_raw:
                enabled = legacy_raw not in {"0", "false", "off", "no", "disabled"}
            elif legacy_raw:
                enabled = legacy_raw in {"1", "true", "yes", "on", "enabled", "strict"}
            else:
                enabled = default_enabled
            mode = RolloutMode.ENABLED if enabled else RolloutMode.DISABLED
        percent = max(0.0, min(100.0, _env_float(f"ERIS_ROLLOUT_{key}_PERCENT", 5.0)))
        kill = _env_bool(f"ERIS_KILL_{key}", False)
        min_samples = max(1, int(_env_float(f"ERIS_ROLLOUT_{key}_MIN_SAMPLES", 20)))
        max_failure = max(0.0, min(1.0, _env_float(f"ERIS_ROLLOUT_{key}_MAX_FAILURE_RATE", 0.15)))
        max_intrusion = max(0.0, min(1.0, _env_float(f"ERIS_ROLLOUT_{key}_MAX_INTRUSION_RATE", 0.10)))
        return cls(
            feature=feature,
            mode=mode,
            canary_percent=percent,
            kill_switch=kill,
            min_samples=min_samples,
            max_failure_rate=max_failure,
            max_intrusion_rate=max_intrusion,
        )


@dataclass(slots=True, frozen=True)
class RolloutDecision:
    feature: str
    mode: str
    apply: bool
    instrument: bool
    shadow: bool
    bucket: float
    canary_percent: float
    reason: str
    killed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FeatureHealth:
    outcomes: deque[tuple[bool, bool, float]] = field(default_factory=lambda: deque(maxlen=500))
    auto_killed: bool = False
    auto_kill_reason: str = ""
    last_evaluated_at: float = 0.0

    def add(self, *, failed: bool, intrusive: bool, timestamp: float) -> None:
        self.outcomes.append((bool(failed), bool(intrusive), float(timestamp)))

    def snapshot(self) -> dict[str, Any]:
        sample_count = len(self.outcomes)
        failures = sum(1 for failed, _intrusive, _ts in self.outcomes if failed)
        intrusions = sum(1 for _failed, intrusive, _ts in self.outcomes if intrusive)
        return {
            "samples": sample_count,
            "failures": failures,
            "intrusions": intrusions,
            "failure_rate": failures / sample_count if sample_count else 0.0,
            "intrusion_rate": intrusions / sample_count if sample_count else 0.0,
            "auto_killed": self.auto_killed,
            "auto_kill_reason": self.auto_kill_reason,
            "last_evaluated_at": self.last_evaluated_at,
        }


class RolloutController:
    """Điều phối feature rollout, shadow và rollback tại runtime."""

    def __init__(
        self,
        *,
        configs: Mapping[str, FeatureRolloutConfig] | None = None,
        observability: Any = None,
        state_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.configs = {
            feature: (dict(configs or {}).get(feature) or FeatureRolloutConfig.from_env(feature))
            for feature in FEATURES
        }
        self.observability = observability
        self.state_path = Path(state_path or os.getenv("ERIS_ROLLOUT_STATE_PATH", "logs/rollout_state.json"))
        self._health: dict[str, FeatureHealth] = defaultdict(FeatureHealth)
        self._manual_kills: dict[str, str] = {}
        self._lock = threading.RLock()
        self._load_state()
        global GLOBAL_ROLLOUT_CONTROLLER
        GLOBAL_ROLLOUT_CONTROLLER = self

    @staticmethod
    def bucket(feature: str, session_id: str) -> float:
        material = f"{feature}:{session_id or 'anonymous'}".encode("utf-8", errors="ignore")
        digest = hashlib.sha256(material).digest()
        integer = int.from_bytes(digest[:8], "big", signed=False)
        return (integer / float(2**64 - 1)) * 100.0

    def decide(self, feature: str, *, session_id: str = "") -> RolloutDecision:
        if feature not in self.configs:
            # Feature chưa đăng ký thì mặc định enabled. Không để typo âm thầm tắt
            # cả pipeline; nhưng status vẫn nói rõ đây là config implicit.
            config = FeatureRolloutConfig(feature=feature, mode=RolloutMode.ENABLED, canary_percent=100.0)
        else:
            config = self.configs[feature]
        bucket = self.bucket(feature, session_id)
        with self._lock:
            health = self._health[feature]
            manual_reason = self._manual_kills.get(feature, "")
            killed = bool(config.kill_switch or manual_reason or health.auto_killed)
            kill_reason = manual_reason or health.auto_kill_reason or ("environment_kill_switch" if config.kill_switch else "")

        if killed:
            decision = RolloutDecision(feature, config.mode.value, False, True, False, bucket, config.canary_percent, kill_reason, True)
        elif config.mode == RolloutMode.DISABLED:
            decision = RolloutDecision(feature, config.mode.value, False, False, False, bucket, config.canary_percent, "disabled")
        elif config.mode == RolloutMode.DARK:
            decision = RolloutDecision(feature, config.mode.value, False, True, False, bucket, config.canary_percent, "instrument_only")
        elif config.mode == RolloutMode.SHADOW:
            decision = RolloutDecision(feature, config.mode.value, False, True, True, bucket, config.canary_percent, "shadow_only")
        elif config.mode == RolloutMode.CANARY:
            apply = bucket < config.canary_percent
            decision = RolloutDecision(
                feature,
                config.mode.value,
                apply,
                True,
                not apply,
                bucket,
                config.canary_percent,
                "canary_selected" if apply else "canary_control",
            )
        else:
            decision = RolloutDecision(feature, config.mode.value, True, True, False, bucket, 100.0, "enabled")

        self._record_decision(decision)
        return decision

    def record_outcome(
        self,
        feature: str,
        *,
        failed: bool = False,
        intrusive: bool = False,
        fields: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            health = self._health[feature]
            health.add(failed=failed, intrusive=intrusive, timestamp=timestamp)
            config = self.configs.get(feature, FeatureRolloutConfig(feature=feature))
            snapshot = health.snapshot()
            health.last_evaluated_at = timestamp
            if snapshot["samples"] >= config.min_samples:
                reasons: list[str] = []
                if snapshot["failure_rate"] > config.max_failure_rate:
                    reasons.append(f"failure_rate={snapshot['failure_rate']:.3f}>{config.max_failure_rate:.3f}")
                if snapshot["intrusion_rate"] > config.max_intrusion_rate:
                    reasons.append(f"intrusion_rate={snapshot['intrusion_rate']:.3f}>{config.max_intrusion_rate:.3f}")
                if reasons and not health.auto_killed:
                    health.auto_killed = True
                    health.auto_kill_reason = "health_gate:" + ";".join(reasons)
                    self._persist_state_locked()
                    self._emit("rollback_trigger_total", 1.0, tags={"feature": feature, "reason": "health_gate"}, fields=fields)
            snapshot = health.snapshot()
        self._emit("canary_outcome_total", 1.0, tags={"feature": feature, "failed": str(bool(failed)).lower()}, fields=fields)
        return snapshot

    def request_kill(self, feature: str, *, reason: str = "manual_kill") -> None:
        with self._lock:
            self._manual_kills[feature] = str(reason or "manual_kill")
            self._persist_state_locked()
        self._emit("manual_disable_total", 1.0, tags={"feature": feature}, fields={"reason": reason})

    def clear_kill(self, feature: str) -> None:
        with self._lock:
            self._manual_kills.pop(feature, None)
            health = self._health[feature]
            health.auto_killed = False
            health.auto_kill_reason = ""
            self._persist_state_locked()
        self._emit("rollout_kill_cleared_total", 1.0, tags={"feature": feature})

    def set_mode(self, feature: str, mode: str | RolloutMode, *, canary_percent: float | None = None) -> None:
        parsed = RolloutMode.parse(mode)
        with self._lock:
            config = self.configs.setdefault(feature, FeatureRolloutConfig(feature=feature))
            config.mode = parsed
            if canary_percent is not None:
                config.canary_percent = max(0.0, min(100.0, float(canary_percent)))
            self._persist_state_locked()
        self._emit("rollout_mode_change_total", 1.0, tags={"feature": feature, "mode": parsed.value})

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "features": {
                    feature: {
                        "config": {
                            **asdict(config),
                            "mode": config.mode.value,
                        },
                        "health": self._health[feature].snapshot(),
                        "manual_kill_reason": self._manual_kills.get(feature, ""),
                    }
                    for feature, config in self.configs.items()
                },
                "generated_at": time.time(),
            }

    def _record_decision(self, decision: RolloutDecision) -> None:
        self._emit(
            "rollout_decision_total",
            1.0,
            tags={
                "feature": decision.feature,
                "mode": decision.mode,
                "apply": str(decision.apply).lower(),
                "shadow": str(decision.shadow).lower(),
                "killed": str(decision.killed).lower(),
            },
            fields={"bucket": round(decision.bucket, 4), "reason": decision.reason},
        )

    def _emit(self, name: str, value: float, *, tags: Mapping[str, Any] | None = None, fields: Mapping[str, Any] | None = None) -> None:
        target = self.observability
        if target is None:
            return
        try:
            target.increment(name, value=value, tags=tags, fields=fields)
        except Exception as exc:
            safe_log(getattr(self, "logger", None), "debug", "Rollout metric failed: %s", exc)

    def _load_state(self) -> None:
        if not self.state_path.exists() or bool(os.getenv("PYTEST_CURRENT_TEST")):
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            for feature, reason in dict(payload.get("manual_kills", {})).items():
                if reason:
                    self._manual_kills[str(feature)] = str(reason)
            for feature, value in dict(payload.get("overrides", {})).items():
                config = self.configs.setdefault(feature, FeatureRolloutConfig(feature=feature))
                if "mode" in value:
                    config.mode = RolloutMode.parse(value.get("mode"), config.mode)
                if "canary_percent" in value:
                    config.canary_percent = max(0.0, min(100.0, float(value.get("canary_percent", config.canary_percent))))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return

    def _persist_state_locked(self) -> None:
        if bool(os.getenv("PYTEST_CURRENT_TEST")):
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "manual_kills": dict(self._manual_kills),
                "overrides": {
                    feature: {
                        "mode": config.mode.value,
                        "canary_percent": config.canary_percent,
                    }
                    for feature, config in self.configs.items()
                },
                "updated_at": time.time(),
            }
            tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception:
            pass


def feature_enabled(feature: str, *, session_id: str = "") -> bool:
    """Đọc kill-switch/canary từ controller global với fallback env tương thích."""
    controller = GLOBAL_ROLLOUT_CONTROLLER
    if controller is None:
        controller = RolloutController(observability=None)
    actual_session = str(session_id or os.getenv("ERIS_RUNTIME_SESSION_ID", "runtime") or "runtime")
    try:
        return bool(controller.decide(feature, session_id=actual_session).apply)
    except Exception:
        legacy = _LEGACY_FLAG_ENV.get(feature, "")
        if not legacy:
            return True
        raw = str(os.getenv(legacy, "") or "").strip().lower()
        if not raw:
            return feature != "singing_capability"
        return raw not in {"0", "false", "off", "no", "disabled"}


def ensure_rollout_controller(owner: Any) -> RolloutController:
    current = getattr(owner, "rollout_controller", None)
    if isinstance(current, RolloutController):
        return current
    try:
        from runtime.observability import ensure_observability

        observability = ensure_observability(owner)
    except Exception:
        observability = None
    current = RolloutController(observability=observability)
    try:
        setattr(owner, "rollout_controller", current)
    except (AttributeError, TypeError):
        pass
    return current


__all__ = [
    "FEATURES",
    "FeatureHealth",
    "FeatureRolloutConfig",
    "RolloutController",
    "RolloutDecision",
    "RolloutMode",
    "ensure_rollout_controller",
    "feature_enabled",
    "GLOBAL_ROLLOUT_CONTROLLER",
]
