# -*- coding: utf-8 -*-
"""Snapshot availability runtime cho CapabilityRegistry.

Registry public không được đoán tool đang sống chỉ từ việc một id tồn tại trong
``VALID_SYSTEM_ACTION_LIST``. Module này chụp trạng thái thật từ Coordinator,
ActionExecutor, live capability runtimes, settings và credential presence. Snapshot chỉ giữ
bool/reason, tuyệt đối không giữ raw API key.
"""

from __future__ import annotations

import os
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, Mapping

from actions.contracts import VALID_SYSTEM_ACTION_LIST
from actions.capability_registry import _OBSERVATIONAL_CAPABILITY_IDS
from screen.ocr_config import screen_ocr_enabled


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeStatus:
    capability_id: str
    available: bool
    reason: str = ""
    source: str = "runtime"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.capability_id,
            "available": self.available,
            "reason": self.reason,
            "source": self.source,
        }


_LOCK = threading.RLock()
_OWNER_REF: weakref.ReferenceType[Any] | None = None
_STATUSES: dict[str, CapabilityRuntimeStatus] = {}
_REVISION = 0
_UPDATED_AT = 0.0

_WINDOWS_ONLY = {
    "focus_window",
    "set_volume",
    "play_pause_media",
    "copy_text",
    "get_now_playing",
}

_SETTING_KEYS = {
    "launch_app": "launch_app_enabled",
    "focus_window": "focus_window_enabled",
    "set_volume": "set_volume_enabled",
    "play_pause_media": "media_enabled",
    "copy_text": "copy_text_enabled",
    "open_url": "open_url_enabled",
    "get_weather": "weather_enabled",
    "get_now_playing": "now_playing_enabled",
}


def bind_runtime_owner(owner: Any | None) -> None:
    """Đăng ký owner bằng weakref rồi refresh snapshot ngay."""

    global _OWNER_REF
    with _LOCK:
        try:
            _OWNER_REF = weakref.ref(owner) if owner is not None else None
        except TypeError:
            _OWNER_REF = None
    refresh_runtime_capabilities(owner=owner)


def refresh_runtime_capabilities(
    *, owner: Any | None = None, settings: Mapping[str, Any] | None = None
) -> dict[str, CapabilityRuntimeStatus]:
    """Tính lại availability từ object runtime đang sống.

    Không ping mạng. Weather được coi là sẵn sàng khi executor có handler và
    credential tương ứng tồn tại. Việc request thật vẫn có thể lỗi, nhưng câu
    "em làm được gì" sẽ không khoe một tool còn chưa được nối hoặc chưa có key.
    """

    global _STATUSES, _REVISION, _UPDATED_AT
    if owner is None:
        with _LOCK:
            owner = _OWNER_REF() if _OWNER_REF is not None else None
    if settings is None:
        settings = getattr(owner, "runtime_settings", None) if owner is not None else None
    caps_settings = dict(settings.get("capabilities", {})) if isinstance(settings, Mapping) else {}
    respect_runtime = bool(caps_settings.get("respect_runtime_availability", True))

    executor = getattr(owner, "action_executor", None) if owner is not None else None
    statuses: dict[str, CapabilityRuntimeStatus] = {}

    for capability_id in VALID_SYSTEM_ACTION_LIST:
        enabled_key = _SETTING_KEYS.get(capability_id, "")
        if enabled_key and caps_settings.get(enabled_key) is False:
            statuses[capability_id] = CapabilityRuntimeStatus(
                capability_id, False, "disabled_by_runtime_settings", "settings"
            )
            continue

        if not respect_runtime:
            statuses[capability_id] = CapabilityRuntimeStatus(
                capability_id, True, "runtime_availability_check_disabled", "settings"
            )
            continue

        if capability_id in _cognitive_capability_ids():
            runtime = getattr(owner, "internal_cognitive_capabilities", None) if owner is not None else None
            if runtime is None:
                statuses[capability_id] = CapabilityRuntimeStatus(
                    capability_id, False, "internal_cognitive_runtime_unavailable", "runtime"
                )
            else:
                available, reason = runtime.availability(capability_id)
                statuses[capability_id] = CapabilityRuntimeStatus(
                    capability_id, bool(available), str(reason or ""), "internal_cognitive_runtime"
                )
            continue

        if capability_id in _environment_capability_ids():
            runtime = getattr(owner, "environment_capabilities", None) if owner is not None else None
            if runtime is None:
                statuses[capability_id] = CapabilityRuntimeStatus(
                    capability_id, False, "environment_runtime_unavailable", "runtime"
                )
            else:
                available, reason = runtime.availability(capability_id)
                statuses[capability_id] = CapabilityRuntimeStatus(
                    capability_id, bool(available), str(reason or ""), "environment_runtime"
                )
            continue

        if capability_id in _project_capability_ids():
            runtime = getattr(owner, "project_operations", None) if owner is not None else None
            if runtime is None:
                statuses[capability_id] = CapabilityRuntimeStatus(
                    capability_id, False, "project_operations_runtime_unavailable", "runtime"
                )
            else:
                available, reason = runtime.availability(capability_id)
                statuses[capability_id] = CapabilityRuntimeStatus(
                    capability_id, bool(available), str(reason or ""), "project_operations_runtime"
                )
            continue

        if owner is None:
            # Trước khi Coordinator bind, không nói dối là available. Registry vẫn
            # hoạt động nhưng prompt sẽ ghi rõ snapshot chưa sẵn sàng.
            statuses[capability_id] = CapabilityRuntimeStatus(
                capability_id, False, "runtime_owner_not_bound", "runtime"
            )
            continue
        if executor is None:
            statuses[capability_id] = CapabilityRuntimeStatus(
                capability_id, False, "action_executor_unavailable", "runtime"
            )
            continue
        if not callable(getattr(executor, f"_{capability_id}", None)):
            statuses[capability_id] = CapabilityRuntimeStatus(
                capability_id, False, "executor_handler_missing", "runtime"
            )
            continue
        if capability_id in _WINDOWS_ONLY and os.name != "nt":
            statuses[capability_id] = CapabilityRuntimeStatus(
                capability_id, False, "windows_only", "platform"
            )
            continue
        if capability_id == "python_workspace":
            from runtime.workspace.python_docker import DockerPythonWorkspace
            ready = DockerPythonWorkspace.available()
            statuses[capability_id] = CapabilityRuntimeStatus(
                capability_id, ready,
                'docker_cli_detected' if ready else 'docker_not_installed', 'runtime'
            )
            continue
        if capability_id == "get_weather":
            available, reason = _remote_tool_credential_available(capability_id)
            statuses[capability_id] = CapabilityRuntimeStatus(
                capability_id, available, reason, "credentials"
            )
            continue

        statuses[capability_id] = CapabilityRuntimeStatus(
            capability_id, True, "executor_handler_ready", "runtime"
        )

    statuses.update(_observational_statuses(owner))

    with _LOCK:
        _STATUSES = statuses
        _REVISION += 1
        _UPDATED_AT = time.time()
        return dict(_STATUSES)


def runtime_capability_snapshot(*, refresh: bool = False) -> dict[str, object]:
    if refresh:
        refresh_runtime_capabilities()
    with _LOCK:
        return {
            "revision": _REVISION,
            "updated_at": _UPDATED_AT,
            "statuses": {key: value.to_dict() for key, value in _STATUSES.items()},
        }


def runtime_capability_flags(*, refresh: bool = False) -> dict[str, bool]:
    """Mapping hỗ trợ cả capability id lẫn env-flag legacy."""

    if refresh:
        refresh_runtime_capabilities()
    from actions.capability_registry import capability_definition

    output: dict[str, bool] = {}
    with _LOCK:
        statuses = dict(_STATUSES)
    for capability_id, status in statuses.items():
        output[capability_id] = status.available
        definition = capability_definition(capability_id)
        if definition is not None and definition.runtime_flag:
            output[definition.runtime_flag] = status.available
    return output


def capability_runtime_reason(capability_id: str) -> str:
    with _LOCK:
        status = _STATUSES.get(str(capability_id or ""))
    return status.reason if status is not None else "runtime_snapshot_missing"


def capability_runtime_revision() -> int:
    with _LOCK:
        return _REVISION


def _cognitive_capability_ids() -> tuple[str, ...]:
    try:
        from runtime.internal_cognitive_capabilities import COGNITIVE_CAPABILITY_IDS
        return tuple(COGNITIVE_CAPABILITY_IDS)
    except Exception:
        return ()


def _environment_capability_ids() -> tuple[str, ...]:
    try:
        from runtime.environment_capabilities import ENVIRONMENT_CAPABILITY_IDS
        return tuple(ENVIRONMENT_CAPABILITY_IDS)
    except Exception:
        return ()


def _project_capability_ids() -> tuple[str, ...]:
    try:
        from runtime.project_operations import PROJECT_CAPABILITY_IDS
        return tuple(PROJECT_CAPABILITY_IDS)
    except Exception:
        return ()


def _observational_statuses(owner: Any | None) -> dict[str, CapabilityRuntimeStatus]:
    """Chụp năng lực quan sát thật; không suy từ prompt hay tên module."""
    if owner is None:
        return {
            cap_id: CapabilityRuntimeStatus(cap_id, False, "runtime_owner_not_bound", "runtime")
            for cap_id in _OBSERVATIONAL_CAPABILITY_IDS
        }
    telemetry = getattr(owner, "telemetry_service", None)
    signal_snapshot = getattr(owner, "last_signal_snapshot", None)
    screen_worker = getattr(owner, "screen_text_worker", None)
    compressor = getattr(owner, "vision_semantic_compressor", None)
    vision_enabled = os.environ.get("ERIS_VISION_COMPRESSOR_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}
    compressor_config = getattr(compressor, "config", None)
    config_enabled = bool(getattr(compressor_config, "enabled", vision_enabled))
    model_ready = bool(str(getattr(compressor_config, "model", "") or "").strip())
    caption_client = getattr(compressor, "caption_client", None)
    generic_client = getattr(compressor, "client", None)
    client_ready = bool(
        callable(getattr(caption_client, "caption_image", None))
        or callable(getattr(generic_client, "chat_json", None))
    )
    semantic_ready = bool(
        vision_enabled
        and config_enabled
        and model_ready
        and client_ready
        and callable(getattr(compressor, "compress", None))
    )
    return {
        "observe_active_app": CapabilityRuntimeStatus(
            "observe_active_app", telemetry is not None or signal_snapshot is not None,
            "machine_runtime_snapshot" if telemetry is not None or signal_snapshot is not None else "telemetry_unavailable",
            "machine_runtime",
        ),
        "observe_window_title": CapabilityRuntimeStatus(
            "observe_window_title", telemetry is not None or signal_snapshot is not None,
            "machine_runtime_snapshot" if telemetry is not None or signal_snapshot is not None else "telemetry_unavailable",
            "machine_runtime",
        ),
        "observe_idle_state": CapabilityRuntimeStatus(
            "observe_idle_state", telemetry is not None or signal_snapshot is not None,
            "machine_runtime_snapshot" if telemetry is not None or signal_snapshot is not None else "telemetry_unavailable",
            "machine_runtime",
        ),
        "read_uia_text": CapabilityRuntimeStatus(
            "read_uia_text", screen_worker is not None,
            "uia_worker_ready" if screen_worker is not None else "uia_worker_unavailable",
            "screen_text_runtime",
        ),
        "semantic_screen_vision": CapabilityRuntimeStatus(
            "semantic_screen_vision", semantic_ready,
            "vision_compressor_ready" if semantic_ready else "vision_compressor_unavailable",
            "vision_runtime",
        ),
        "camera_view": CapabilityRuntimeStatus(
            "camera_view", False, "camera_stream_not_connected", "sensor",
        ),
        "meet_participants": CapabilityRuntimeStatus(
            "meet_participants", False, "no_camera_or_semantic_meet_bridge", "sensor",
        ),
        "telemetry_summary": CapabilityRuntimeStatus(
            "telemetry_summary", telemetry is not None,
            "telemetry_service_ready" if telemetry is not None else "telemetry_service_unavailable",
            "runtime",
        ),
    }


def runtime_sensor_health_snapshot(*, owner: Any | None = None) -> dict[str, str]:
    """Unified health view for perception/runtime sensors.

    This complements action capability availability: a capability may exist while
    one of its sensor backends is degraded.  WorldState and public wording read
    this same snapshot instead of inventing per-lane health heuristics.
    """
    if owner is None:
        with _LOCK:
            owner = _OWNER_REF() if _OWNER_REF is not None else None
    if owner is None:
        return {
            "deep_environment": "unavailable", "uia": "unavailable",
            "ocr": "unavailable" if screen_ocr_enabled() else "disabled",
            "browser_bridge": "disabled", "vlm": "unavailable", "workspace": "unknown",
        }

    deep_worker = getattr(owner, "semantic_environment_worker", None)
    deep_status = "healthy" if deep_worker is not None else "unavailable"

    screen_worker = getattr(owner, "screen_text_worker", None)
    uia_status = "healthy" if screen_worker is not None else "unavailable"
    backend = ""
    store = getattr(owner, "screen_evidence_store", None)
    snapshot_fn = getattr(store, "snapshot", None)
    if callable(snapshot_fn):
        try:
            backend = str(getattr(snapshot_fn(), "backend", "") or "").strip().lower()
        except Exception:
            backend = ""
    if not screen_ocr_enabled():
        ocr_status = "disabled"
    elif any(name in backend for name in ("rapidocr", "tesseract")):
        ocr_status = "healthy"
    elif "uia_only" in backend or screen_worker is not None:
        ocr_status = "degraded"
    else:
        ocr_status = "unavailable"

    browser_status = "healthy" if getattr(owner, "browser_bridge_server", None) is not None else "disabled"

    compressor = getattr(owner, "vision_semantic_compressor", None)
    config = getattr(compressor, "config", None) if compressor is not None else None
    compressor_ready = bool(
        compressor is not None
        and callable(getattr(compressor, "compress", None))
        and bool(getattr(config, "enabled", True))
    )
    vlm_status = "available" if compressor_ready else "unavailable"

    workspace_status = "healthy" if getattr(owner, "project_operations", None) is not None else "unknown"
    return {
        "deep_environment": deep_status,
        "uia": uia_status,
        "ocr": ocr_status,
        "browser_bridge": browser_status,
        "vlm": vlm_status,
        "workspace": workspace_status,
    }


def _remote_tool_credential_available(capability_id: str) -> tuple[bool, str]:
    return False, "provider_retired"


__all__ = [
    "CapabilityRuntimeStatus",
    "bind_runtime_owner",
    "refresh_runtime_capabilities",
    "runtime_capability_snapshot",
    "runtime_capability_flags",
    "runtime_sensor_health_snapshot",
    "capability_runtime_reason",
    "capability_runtime_revision",
]
