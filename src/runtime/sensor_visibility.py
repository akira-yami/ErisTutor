from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


_TRUE_VALUES = {"1", "true", "yes", "on", "blocked", "denied", "fail", "failed", "timeout", "timed_out"}
_FALSE_VALUES = {"0", "false", "no", "off", "ok", "allowed", "pass", "passed"}


@dataclass(frozen=True, slots=True)
class SensorVisibilityState:
    has_current_screen_evidence: bool = False
    no_current_screen_evidence: bool = True
    vision_permission_blocked: bool = False
    vision_timeout: bool = False
    budget_blocked: bool = False
    recent_context_shift: bool = False
    context_shift_without_refresh: bool = False
    public_frustration_allowed: bool = False
    should_stay_quiet: bool = True
    confidence: float = 0.0
    reason: str = "no_current_screen_evidence"
    evidence: tuple[str, ...] = ()

    def to_context(self) -> dict[str, object]:
        return {
            "has_current_screen_evidence": bool(self.has_current_screen_evidence),
            "no_current_screen_evidence": bool(self.no_current_screen_evidence),
            "vision_permission_blocked": bool(self.vision_permission_blocked),
            "vision_timeout": bool(self.vision_timeout),
            "budget_blocked": bool(self.budget_blocked),
            "recent_context_shift": bool(self.recent_context_shift),
            "context_shift_without_refresh": bool(self.context_shift_without_refresh),
            "public_frustration_allowed": bool(self.public_frustration_allowed),
            "should_stay_quiet": bool(self.should_stay_quiet),
            "confidence": round(float(self.confidence), 3),
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


def derive_sensor_visibility_state(*, snapshot: Any = None, frame: Any = None) -> SensorVisibilityState:
    texts = tuple(_collect_text_signals(snapshot, frame))
    joined = "\n".join(texts).lower()

    has_current_screen_evidence = _has_current_screen_evidence(snapshot, frame)
    no_current_screen_evidence = not has_current_screen_evidence

    permission_blocked = _explicit_bool(
        snapshot,
        frame,
        names=(
            "vision_permission_blocked",
            "screen_permission_blocked",
            "screen_capture_permission_blocked",
            "capture_permission_blocked",
            "permission_blocked",
        ),
    )
    permission_ok = _explicit_bool(
        snapshot,
        frame,
        names=("vision_permission_ok", "screen_permission_ok", "capture_permission_ok"),
        default=None,
    )
    if permission_ok is False:
        permission_blocked = True

    vision_timeout = bool(
        _explicit_bool(snapshot, frame, names=("vision_timeout", "vision_timed_out", "vision_queue_timeout"))
        or (
            any(
                marker in joined
                for marker in (
                    "vision_timeout",
                    "vision timed out",
                    "vision_timed_out",
                    "vision_queue_timeout",
                    "model_queue_timeout",
                    "timeout:vision",
                    "vision facts failed",
                    "vision_facts",
                )
            )
            and "timeout" in joined
        )
    )

    budget_blocked = bool(
        _explicit_bool(snapshot, frame, names=("budget_blocked", "vision_budget_blocked", "resource_blocked"))
        or any(
            marker in joined
            for marker in (
                "resource_guard",
                "budget_blocked",
                "vision_budget_blocked",
                "tier_cooldown_active",
                "active_heavy_lease",
                "vram_headroom_low",
                "vram_busy",
                "compute_budget",
                "scheduler/budget",
            )
        )
    )

    recent_context_shift = bool(
        _explicit_bool(snapshot, frame, names=("recent_context_shift", "context_shift", "window_changed", "app_changed", "focus_changed"))
        or any(
            marker in joined
            for marker in (
                "recent_context_shift",
                "context_shift",
                "window_changed",
                "active_window_changed",
                "app_changed",
                "focus_changed",
                "screen_changed",
                "ocr_changed",
                "tab_changed",
                "window_switch",
                "window_switching",
            )
        )
    )
    context_shift_without_refresh = bool(recent_context_shift and no_current_screen_evidence)

    public_frustration_allowed = bool(
        not permission_blocked and (vision_timeout or budget_blocked or context_shift_without_refresh)
    )

    evidence: list[str] = []
    evidence.append(f"has_current_screen_evidence={str(has_current_screen_evidence).lower()}")
    evidence.append(f"no_current_screen_evidence={str(no_current_screen_evidence).lower()}")
    if permission_blocked:
        evidence.append("vision_permission_blocked=true")
    if vision_timeout:
        evidence.append("vision_timeout=true")
    if budget_blocked:
        evidence.append("budget_blocked=true")
    if recent_context_shift:
        evidence.append("recent_context_shift=true")
    else:
        evidence.append("recent_context_shift=false")
    if context_shift_without_refresh:
        evidence.append("context_shift_without_refresh=true")

    if permission_blocked:
        reason = "vision_permission_blocked"
        confidence = 0.78
    elif vision_timeout:
        reason = "vision_timeout"
        confidence = 0.66
    elif budget_blocked:
        reason = "budget_blocked"
        confidence = 0.62
    elif context_shift_without_refresh:
        reason = "context_shift_without_refresh"
        confidence = 0.60
    elif no_current_screen_evidence:
        reason = "no_current_screen_evidence"
        confidence = 0.44
    else:
        reason = "current_screen_evidence_available"
        confidence = 0.58

    return SensorVisibilityState(
        has_current_screen_evidence=has_current_screen_evidence,
        no_current_screen_evidence=no_current_screen_evidence,
        vision_permission_blocked=bool(permission_blocked),
        vision_timeout=vision_timeout,
        budget_blocked=budget_blocked,
        recent_context_shift=recent_context_shift,
        context_shift_without_refresh=context_shift_without_refresh,
        public_frustration_allowed=public_frustration_allowed,
        should_stay_quiet=not public_frustration_allowed,
        confidence=_clamp01(confidence),
        reason=reason,
        evidence=tuple(dict.fromkeys(evidence))[:10],
    )


def _has_current_screen_evidence(snapshot: Any = None, frame: Any = None) -> bool:
    for owner in (snapshot, frame):
        if owner is None:
            continue
        for name in (
            "vision_facts",
            "screen_facts",
            "current_screen_evidence",
            "screen_evidence",
            "ocr_text",
            "visible_text",
        ):
            if _has_payload(_get_value(owner, name)):
                return True
        evidence_items = _get_value(owner, "evidence")
        if isinstance(evidence_items, (list, tuple)):
            for item in evidence_items:
                source = str(_get_value(item, "source") or "").lower()
                label = str(_get_value(item, "label") or "").lower()
                tags = " ".join(str(x).lower() for x in (_get_value(item, "tags") or ()))
                claim = str(_get_value(item, "claim") or _get_value(item, "content") or "")
                if claim.strip() and any(marker in f"{source} {label} {tags}" for marker in ("vision", "ocr", "screen")):
                    return True
    return False


def _collect_text_signals(*owners: Any) -> list[str]:
    texts: list[str] = []
    for owner in owners:
        if owner is None:
            continue
        for name in (
            "reason",
            "debug_trace",
            "vision_status",
            "vision_error",
            "budget_reason",
            "active_window",
            "active_window_title",
            "active_app",
            "focus_summary",
        ):
            value = _get_value(owner, name)
            if value not in (None, ""):
                texts.append(str(value))
        for name in ("recent_events", "events", "debug_notes"):
            value = _get_value(owner, name)
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, (list, tuple, set)):
                texts.extend(str(item) for item in value if str(item or "").strip())
        metadata = _get_value(owner, "metadata")
        if isinstance(metadata, Mapping):
            for key, value in metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    texts.append(f"{key}={value}")
                elif isinstance(value, (list, tuple, set)):
                    texts.append(f"{key}=" + ",".join(str(item) for item in value))
        wake = _get_value(owner, "wake_decision") or _get_value(owner, "wake")
        if wake is not None and wake is not owner:
            texts.extend(_collect_text_signals(wake))
        debug = _get_value(owner, "debug")
        if isinstance(debug, Mapping):
            texts.append(" ".join(f"{key}={value}" for key, value in debug.items() if isinstance(value, (str, int, float, bool))))
    return [re.sub(r"\s+", " ", item).strip() for item in texts if str(item or "").strip()]


def _explicit_bool(*owners: Any, names: tuple[str, ...], default: bool | None = False) -> bool | None:
    for owner in owners:
        if owner is None:
            continue
        for name in names:
            sentinel = object()
            value = _get_value(owner, name, sentinel)
            if value is sentinel:
                continue
            parsed = _parse_bool(value)
            if parsed is not None:
                return parsed
        metadata = _get_value(owner, "metadata")
        if isinstance(metadata, Mapping):
            for name in names:
                if name in metadata:
                    parsed = _parse_bool(metadata.get(name))
                    if parsed is not None:
                        return parsed
        debug = _get_value(owner, "debug")
        if isinstance(debug, Mapping):
            for name in names:
                if name in debug:
                    parsed = _parse_bool(debug.get(name))
                    if parsed is not None:
                        return parsed
    return default


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return None


def _get_value(owner: Any, name: str, default: Any = None) -> Any:
    if owner is None:
        return default
    if isinstance(owner, Mapping):
        return owner.get(name, default)
    return getattr(owner, name, default)


def _has_payload(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return any(_has_payload(item) for item in value)
    return bool(value)


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if number != number:
        number = 0.0
    return max(0.0, min(1.0, number))
