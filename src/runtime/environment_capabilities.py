# -*- coding: utf-8 -*-
"""Năng lực đọc môi trường có evidence cho Eris.

Module này chuẩn hóa sáu thao tác read-only:

- ứng dụng/cửa sổ đang active;
- text UIA/OCR đang nhìn thấy;
- log runtime mới nhất;
- lỗi cụ thể gần nhất;
- file đang được chọn/mở;
- trạng thái project Git.

Điểm quan trọng không nằm ở việc "có hàm đọc". Mỗi kết quả đều mang nguồn,
độ tươi và evidence id. Nếu runtime chỉ biết VS Code đang active, Eris chỉ được
nói điều đó. Nó không được tự tiến hóa thành "tôi thấy lỗi TTS" chỉ vì hai chữ
VS Code làm model phấn khích quá mức.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import weakref
import unicodedata
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from actions.contracts import ActionExecutionResult, ActionExecutionStatus
from runtime.workspace_manager import default_eris_home

ENVIRONMENT_CAPABILITY_VERSION = "environment_capability.v1.0"


class EnvironmentActionState(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    FAILED_VERIFICATION = "failed_verification"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class EnvironmentCapabilityContract:
    capability_id: str
    public_name_vi: str
    description_vi: str
    handler_id: str
    required_sources: tuple[str, ...]
    timeout_seconds: float = 3.0
    permission_mode: str = "read_only_auto"
    reversible: bool = True
    side_effects: tuple[str, ...] = ()
    verification_handler: str = "environment_evidence_bundle"


@dataclass(frozen=True, slots=True)
class EnvironmentEvidence:
    evidence_id: str
    source: str
    summary: str
    captured_at: float = 0.0
    confidence: float = 1.0
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "summary": self.summary,
            "captured_at": round(float(self.captured_at or 0.0), 3),
            "confidence": round(max(0.0, min(1.0, float(self.confidence))), 3),
            "data": dict(self.data),
        }


@dataclass(slots=True)
class EnvironmentActionResult:
    action_id: str
    capability_id: str
    state: EnvironmentActionState
    public_text: str
    started_at: float
    completed_at: float
    executed: bool = False
    verified: bool = False
    evidence: tuple[EnvironmentEvidence, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    error_kind: str = ""
    error_message: str = ""

    @property
    def success(self) -> bool:
        return self.state == EnvironmentActionState.SUCCEEDED and self.executed and self.verified

    def to_action_result(self, *, action_input: str = "") -> ActionExecutionResult:
        payload = {
            key: _stringify(value)
            for key, value in self.data.items()
            if value is not None
        }
        payload.update({
            "environment_action_id": self.action_id,
            "environment_state": self.state.value,
            "executed": str(bool(self.executed)).lower(),
            "verified": str(bool(self.verified)).lower(),
            "environment_version": ENVIRONMENT_CAPABILITY_VERSION,
        })
        return ActionExecutionResult(
            action_name=self.capability_id,
            action_input=action_input,
            success=self.success,
            message=self.public_text or self.error_message or "Năng lực đọc môi trường chưa chạy được.",
            data=payload,
            status=ActionExecutionStatus.OBSERVED if self.success else ActionExecutionStatus.FAILED,
            dispatched=bool(self.executed),
            observed=bool(self.verified),
            evidence=tuple(item.evidence_id for item in self.evidence),
            error_kind=self.error_kind,
            observer_reason="environment_evidence_verified" if self.verified else "environment_verification_failed",
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        payload["success"] = self.success
        return payload


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:4000]
    except (TypeError, ValueError, OverflowError):
        return str(value)[:4000]


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/=]{16,}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[:=]\s*[\"']?[^\s,;\"']+"),
    re.compile(r"\b(?:sk|gsk|AIza|agt_codex)_[A-Za-z0-9._-]{12,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
)


def _redact(text: str) -> str:
    value = str(text or "")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            value = pattern.sub(lambda match: f"{match.group(1)}[đã ẩn]", value)
        else:
            value = pattern.sub("[khóa bí mật đã ẩn]", value)
    return value


def _hash_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(str(value or "").encode("utf-8", "ignore")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _norm(text: Any) -> str:
    value = str(text or "").lower().replace("đ", "d")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9_./:\\+\-\s]", " ", value)).strip()


ENVIRONMENT_CAPABILITY_CONTRACTS: dict[str, EnvironmentCapabilityContract] = {
    "get_active_application": EnvironmentCapabilityContract(
        capability_id="get_active_application",
        public_name_vi="xem ứng dụng đang active",
        description_vi="đọc app, title và nguồn telemetry hiện tại; không suy ra nội dung file",
        handler_id="environment.get_active_application",
        required_sources=("telemetry_snapshot", "screen_window_snapshot"),
    ),
    "read_visible_ui_text": EnvironmentCapabilityContract(
        capability_id="read_visible_ui_text",
        public_name_vi="đọc text UI đang hiện",
        description_vi="đọc các block UIA/OCR đủ tươi và tôn trọng privacy guard",
        handler_id="environment.read_visible_ui_text",
        required_sources=("screen_evidence_store",),
    ),
    "inspect_runtime_log": EnvironmentCapabilityContract(
        capability_id="inspect_runtime_log",
        public_name_vi="đọc log runtime",
        description_vi="đọc phần cuối log eris_run mới nhất, có lọc và che khóa bí mật",
        handler_id="environment.inspect_runtime_log",
        required_sources=("runtime_log_file",),
        timeout_seconds=2.0,
    ),
    "read_current_error": EnvironmentCapabilityContract(
        capability_id="read_current_error",
        public_name_vi="đọc lỗi hiện tại",
        description_vi="tìm error/traceback cụ thể gần nhất từ log hoặc text màn hình",
        handler_id="environment.read_current_error",
        required_sources=("runtime_log_file", "screen_evidence_store"),
        timeout_seconds=2.0,
    ),
    "inspect_selected_file": EnvironmentCapabilityContract(
        capability_id="inspect_selected_file",
        public_name_vi="xem file đang được chọn",
        description_vi="xác định file từ bridge/path/title; chỉ đọc nội dung khi có path thật và nằm trong project",
        handler_id="environment.inspect_selected_file",
        required_sources=("selected_file_bridge", "telemetry_snapshot", "screen_window_snapshot"),
    ),
    "get_project_status": EnvironmentCapabilityContract(
        capability_id="get_project_status",
        public_name_vi="xem trạng thái project",
        description_vi="đọc Git branch, staged/modified/untracked bằng lệnh read-only",
        handler_id="environment.get_project_status",
        required_sources=("project_root", "git"),
        timeout_seconds=4.0,
    ),
}

ENVIRONMENT_CAPABILITY_IDS = tuple(ENVIRONMENT_CAPABILITY_CONTRACTS)


class EnvironmentCapabilityRuntime:
    """Executor read-only cho môi trường máy và project."""

    def __init__(self, owner: Any, *, logger: Any | None = None, max_audit: int = 80) -> None:
        try:
            self._owner_ref = weakref.ref(owner)
        except TypeError:
            self._owner_ref = lambda: owner
        self.logger = logger
        self.last_source_error: dict[str, str] = {}
        self._lock = threading.RLock()
        self._audit: deque[EnvironmentActionResult] = deque(maxlen=max(20, int(max_audit)))
        self._handlers: dict[str, Callable[[str], tuple[str, dict[str, Any], tuple[EnvironmentEvidence, ...]]]] = {
            "get_active_application": self._get_active_application,
            "read_visible_ui_text": self._read_visible_ui_text,
            "inspect_runtime_log": self._inspect_runtime_log,
            "read_current_error": self._read_current_error,
            "inspect_selected_file": self._inspect_selected_file,
            "get_project_status": self._get_project_status,
        }

    @property
    def owner(self) -> Any | None:
        try:
            return self._owner_ref()
        except ReferenceError:
            return None

    def _record_source_failure(self, source: str, exc: BaseException) -> None:
        self.last_source_error = {
            "source": str(source or "unknown")[:80],
            "error": type(exc).__name__,
            "message": str(exc)[:240],
        }
        debug = getattr(self.logger, "debug", None)
        if callable(debug):
            try:
                debug("[environment-capability] source=%s failed: %s", source, exc)
            except (TypeError, ValueError, OSError):
                return

    def contracts(self) -> tuple[EnvironmentCapabilityContract, ...]:
        return tuple(ENVIRONMENT_CAPABILITY_CONTRACTS.values())

    def availability(self, capability_id: str) -> tuple[bool, str]:
        cap = str(capability_id or "").strip()
        owner = self.owner
        if cap not in ENVIRONMENT_CAPABILITY_CONTRACTS:
            return False, "unknown_environment_capability"
        if owner is None:
            return False, "runtime_owner_unavailable"
        if cap == "get_active_application":
            if any((
                getattr(owner, "last_signal_snapshot", None) is not None,
                getattr(owner, "telemetry_service", None) is not None,
                getattr(owner, "screen_evidence_store", None) is not None,
            )):
                return True, "machine_snapshot_source_ready"
            return False, "machine_snapshot_source_unavailable"
        if cap == "read_visible_ui_text":
            store = getattr(owner, "screen_evidence_store", None)
            return (True, "screen_evidence_store_ready") if callable(getattr(store, "snapshot", None)) else (False, "screen_evidence_store_unavailable")
        if cap in {"inspect_runtime_log", "read_current_error"}:
            path = self._latest_runtime_log()
            store = getattr(owner, "screen_evidence_store", None)
            if path is not None:
                return True, "runtime_log_ready"
            if cap == "read_current_error" and callable(getattr(store, "snapshot", None)):
                return True, "screen_error_source_ready"
            return False, "runtime_log_not_found"
        if cap == "inspect_selected_file":
            if os.getenv("ERIS_SELECTED_FILE_PATH", "").strip():
                return True, "selected_file_bridge_configured"
            if any((getattr(owner, "telemetry_service", None), getattr(owner, "screen_evidence_store", None), getattr(owner, "last_signal_snapshot", None))):
                return True, "selected_file_observation_sources_ready"
            return False, "selected_file_sources_unavailable"
        if cap == "get_project_status":
            root = self._project_root()
            if root is None or not root.exists():
                return False, "active_workspace_required"
            if shutil.which("git") is None:
                return False, "git_executable_missing"
            return True, "active_workspace_and_git_ready"
        return False, "unavailable"

    def execute(self, capability_id: str, action_input: str = "") -> EnvironmentActionResult:
        cap = str(capability_id or "").strip()
        action_id = f"env_{uuid.uuid4().hex[:12]}"
        started = time.time()
        available, reason = self.availability(cap)
        if not available:
            return self._finish(EnvironmentActionResult(
                action_id=action_id,
                capability_id=cap,
                state=EnvironmentActionState.FAILED,
                public_text="Nguồn đọc môi trường cho thao tác này chưa sẵn sàng.",
                started_at=started,
                completed_at=time.time(),
                executed=False,
                verified=False,
                error_kind="CapabilityUnavailable",
                error_message=reason,
                data={"availability_reason": reason},
            ))
        handler = self._handlers.get(cap)
        if handler is None:
            return self._finish(EnvironmentActionResult(
                action_id=action_id,
                capability_id=cap,
                state=EnvironmentActionState.FAILED,
                public_text="Năng lực đọc môi trường chưa có handler.",
                started_at=started,
                completed_at=time.time(),
                error_kind="HandlerMissing",
            ))
        try:
            text, data, evidence = handler(str(action_input or ""))
            verified = self._verify(cap, data=data, evidence=evidence)
            return self._finish(EnvironmentActionResult(
                action_id=action_id,
                capability_id=cap,
                state=EnvironmentActionState.SUCCEEDED if verified else EnvironmentActionState.FAILED_VERIFICATION,
                public_text=text if verified else "Tôi đã đọc nguồn môi trường nhưng evidence chưa đủ để xác nhận.",
                started_at=started,
                completed_at=time.time(),
                executed=True,
                verified=verified,
                evidence=evidence,
                data=data,
                error_kind="" if verified else "VerificationFailed",
            ))
        except Exception as exc:
            return self._finish(EnvironmentActionResult(
                action_id=action_id,
                capability_id=cap,
                state=EnvironmentActionState.FAILED,
                public_text="Tôi không hoàn tất được lần đọc môi trường này.",
                started_at=started,
                completed_at=time.time(),
                executed=True,
                verified=False,
                error_kind=type(exc).__name__,
                error_message=_clean(exc, 240),
            ))


    def audit_snapshot(self, limit: int = 20) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(item.to_dict() for item in list(self._audit)[-max(1, int(limit)):])

    @staticmethod
    def _verify(capability_id: str, *, data: Mapping[str, Any], evidence: Sequence[EnvironmentEvidence]) -> bool:
        if data.get("verification_key") != capability_id or not evidence:
            return False
        return all(bool(item.evidence_id and item.source) for item in evidence)

    def _finish(self, result: EnvironmentActionResult) -> EnvironmentActionResult:
        with self._lock:
            self._audit.append(result)
        if self.logger is not None:
            try:
                self.logger.info(
                    "[environment-capability] capability=%s state=%s executed=%s verified=%s evidence=%s",
                    result.capability_id, result.state.value, result.executed, result.verified, len(result.evidence),
                )
            except (TypeError, ValueError, OSError) as exc:
                self._record_source_failure("audit_logger", exc)
        return result

    # ------------------------------------------------------------------
    # Nguồn dữ liệu chung
    # ------------------------------------------------------------------

    def _capture_snapshot(self) -> Any | None:
        owner = self.owner
        if owner is None:
            return None
        # Snapshot đang sống được ưu tiên. Nếu chưa có, mới chụp telemetry mới.
        snapshot = getattr(owner, "last_signal_snapshot", None)
        if snapshot is not None and any(getattr(snapshot, key, None) for key in ("active_app", "active_window", "active_window_title")):
            return snapshot
        service = getattr(owner, "telemetry_service", None)
        capture = getattr(service, "capture_snapshot", None)
        if callable(capture):
            try:
                return capture(
                    mood_score=int(getattr(owner, "mood_score", 50) or 50),
                    inferred_pos_x=float(getattr(owner, "inferred_pos_x", 0.5) or 0.5),
                )
            except Exception as exc:
                self._record_source_failure("telemetry.capture_snapshot", exc)
                return None
        return None

    def _screen_snapshot(self) -> Any | None:
        owner = self.owner
        store = getattr(owner, "screen_evidence_store", None) if owner is not None else None
        getter = getattr(store, "snapshot", None)
        if callable(getter):
            try:
                return getter()
            except Exception as exc:
                self._record_source_failure("screen.snapshot", exc)
                return None
        return None

    def _project_root(self) -> Path | None:
        """Chỉ dùng workspace đã được người dùng join/switch rõ ràng."""
        owner = self.owner
        manager = getattr(owner, "workspace_manager", None) if owner is not None else None
        getter = getattr(manager, "active_path", None)
        if callable(getter):
            try:
                return getter()
            except Exception as exc:
                self._record_source_failure("workspace.active_path", exc)
                return None
        # Bootstrap dành cho bản chạy headless/tooling. Tên env cố ý là ACTIVE;
        # không đọc ERIS_PROJECT_ROOT và không suy từ source hay memory path.
        explicit = os.getenv("ERIS_ACTIVE_WORKSPACE_ROOT", "").strip()
        if explicit:
            try:
                path = Path(explicit).expanduser().resolve()
                return path if path.is_dir() else None
            except (OSError, RuntimeError):
                return None
        return None

    def _candidate_log_dirs(self) -> tuple[Path, ...]:
        dirs: list[Path] = []
        explicit = os.getenv("ERIS_RUNTIME_LOG_DIR", "").strip()
        if explicit:
            dirs.append(Path(explicit).expanduser())
        dirs.append(default_eris_home() / "logs")
        dirs.extend((Path.cwd() / "logs", Path.cwd()))
        unique: list[Path] = []
        seen: set[str] = set()
        for item in dirs:
            try:
                key = str(item.resolve())
            except (OSError, RuntimeError):
                key = str(item)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return tuple(unique)

    def _latest_runtime_log(self) -> Path | None:
        explicit = os.getenv("ERIS_RUNTIME_LOG_PATH", "").strip()
        if explicit:
            path = Path(explicit).expanduser()
            if path.is_file():
                return path
        candidates: list[Path] = []
        for directory in self._candidate_log_dirs():
            if not directory.is_dir():
                continue
            for pattern in ("eris_run_*.txt", "eris_run_*.log", "*.log"):
                try:
                    candidates.extend(path for path in directory.glob(pattern) if path.is_file())
                except OSError as exc:
                    self._record_source_failure("runtime_log.glob", exc)
                    continue
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    @staticmethod
    def _read_tail(path: Path, *, max_bytes: int = 160_000) -> str:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            data = handle.read(max_bytes)
        return data.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Sáu handler read-only
    # ------------------------------------------------------------------

    def _get_active_application(self, action_input: str) -> tuple[str, dict[str, Any], tuple[EnvironmentEvidence, ...]]:
        del action_input
        snapshot = self._capture_snapshot()
        screen = self._screen_snapshot()
        window = getattr(screen, "window", None)
        process_name = _clean(getattr(window, "process_name", ""), 120)
        title = _clean(
            getattr(snapshot, "active_window_title", "")
            or getattr(snapshot, "active_window", "")
            or getattr(snapshot, "active_app", "")
            or getattr(window, "title", ""),
            300,
        )
        raw_app = _clean(getattr(snapshot, "active_app", "") or process_name or title, 180)
        app = self._friendly_app_name(process_name or raw_app or title)
        source = "telemetry_snapshot" if snapshot is not None else "screen_window_snapshot"
        captured_at = float(getattr(screen, "captured_at", 0.0) or time.time())
        if not app and not title:
            text = "Tôi đã kiểm tra nhưng chưa xác định được ứng dụng đang active."
            summary = "active application unavailable"
            confidence = 0.45
        else:
            shown = app or title
            text = f"Ứng dụng đang active: {shown}."
            if title and title != shown:
                text += f" Tiêu đề cửa sổ: “{title}”."
            summary = f"app={shown}; title={title}"
            confidence = 0.92 if process_name else 0.82
        evidence = (EnvironmentEvidence(
            _hash_id("active_window", f"{app}|{title}|{captured_at}"),
            source,
            summary,
            captured_at=captured_at,
            confidence=confidence,
            data={"application": app, "window_title": title, "process_name": process_name},
        ),)
        return text, {
            "verification_key": "get_active_application",
            "application": app,
            "window_title": title,
            "process_name": process_name,
            "source": source,
        }, evidence

    @staticmethod
    def _friendly_app_name(value: str) -> str:
        text = _clean(value, 220)
        folded = text.lower()
        known = (
            (("visual studio code", "code.exe", " code "), "VS Code"),
            (("godot",), "Godot"),
            (("chrome", "google chrome"), "Chrome"),
            (("msedge", "microsoft edge", "edge.exe"), "Microsoft Edge"),
            (("firefox",), "Firefox"),
            (("powershell",), "PowerShell"),
            (("windows terminal", "wt.exe"), "Windows Terminal"),
            (("explorer.exe", "file explorer"), "File Explorer"),
        )
        padded = f" {folded} "
        for markers, label in known:
            if any(marker in folded or marker in padded for marker in markers):
                return label
        if text.lower().endswith(".exe"):
            return Path(text).stem
        # Nếu chỉ có title dài, không gọi cả câu đó là tên app. Lấy vế cuối sau dấu gạch.
        parts = [part.strip() for part in re.split(r"\s+[—-]\s+", text) if part.strip()]
        if len(parts) >= 2 and len(parts[-1]) <= 80:
            return parts[-1]
        return text[:120]

    def _read_visible_ui_text(self, action_input: str) -> tuple[str, dict[str, Any], tuple[EnvironmentEvidence, ...]]:
        owner = self.owner
        store = getattr(owner, "screen_evidence_store", None) if owner is not None else None
        snapshot = self._screen_snapshot()
        if snapshot is None:
            raise RuntimeError("screen evidence snapshot unavailable")
        if bool(getattr(snapshot, "privacy_blocked", False)):
            evidence = (EnvironmentEvidence(
                f"screen_snapshot:revision:{int(getattr(snapshot, 'revision', 0) or 0)}",
                "screen_evidence_store",
                "privacy_blocked=true",
                captured_at=float(getattr(snapshot, "captured_at", 0.0) or time.time()),
                confidence=1.0,
            ),)
            return "Cửa sổ hiện tại bị privacy guard chặn, nên tôi không đọc text bên trong.", {
                "verification_key": "read_visible_ui_text",
                "privacy_blocked": True,
                "block_count": 0,
            }, evidence

        max_age = max(1.0, float(os.getenv("ERIS_ENV_UI_TEXT_MAX_AGE_SEC", "8") or 8.0))
        query = _clean(action_input, 240)
        generic = _norm(query) in {"", "current", "screen", "visible", "ui", "text", "latest"}
        blocks: tuple[Any, ...]
        if not generic and callable(getattr(store, "query", None)):
            blocks = tuple(store.query(query, max_age_seconds=max_age, max_blocks=10, max_chars=1800, generic_screen_request=False))
        else:
            now = time.time()
            blocks = tuple(
                block for block in tuple(getattr(snapshot, "blocks", ()) or ())
                if now - float(getattr(block, "captured_at", 0.0) or 0.0) <= max_age
            )[:10]
        captured_at = float(getattr(snapshot, "captured_at", 0.0) or time.time())
        if not blocks:
            evidence = (EnvironmentEvidence(
                f"screen_snapshot:revision:{int(getattr(snapshot, 'revision', 0) or 0)}",
                "screen_evidence_store",
                f"fresh_blocks=0 backend={_clean(getattr(snapshot, 'backend', ''), 40)}",
                captured_at=captured_at,
                confidence=0.8,
            ),)
            return "Tôi đã kiểm tra nhưng chưa có text UIA/OCR đủ tươi để đọc.", {
                "verification_key": "read_visible_ui_text",
                "privacy_blocked": False,
                "block_count": 0,
                "backend": _clean(getattr(snapshot, "backend", ""), 40),
            }, evidence

        lines: list[str] = []
        evidence_items: list[EnvironmentEvidence] = []
        for block in blocks:
            raw = _redact(_clean(getattr(block, "text", ""), 500))
            if not raw or raw in lines:
                continue
            lines.append(raw)
            evidence_items.append(EnvironmentEvidence(
                str(getattr(block, "block_id", "") or _hash_id("screen_block", raw)),
                f"screen_text:{_clean(getattr(block, 'source', 'unknown'), 32)}",
                raw[:220],
                captured_at=float(getattr(block, "captured_at", captured_at) or captured_at),
                confidence=float(getattr(block, "confidence", 0.5) or 0.5),
                data={"window_title": _clean(getattr(block, "window_title", ""), 180)},
            ))
        preview = lines[:4]
        text = f"Tôi đọc được {len(lines)} block text qua UIA/OCR."
        if preview:
            text += " Phần rõ nhất: " + " | ".join(f"“{line[:180]}”" for line in preview) + "."
        return text, {
            "verification_key": "read_visible_ui_text",
            "block_count": len(lines),
            "backend": _clean(getattr(snapshot, "backend", ""), 40),
            "window_title": _clean(getattr(getattr(snapshot, "window", None), "title", ""), 180),
            "visible_text": "\n".join(lines)[:3000],
            "query": query,
        }, tuple(evidence_items) or (EnvironmentEvidence(
            f"screen_snapshot:revision:{int(getattr(snapshot, 'revision', 0) or 0)}",
            "screen_evidence_store", "blocks existed but were empty after sanitation", captured_at=captured_at,
        ),)

    def _inspect_runtime_log(self, action_input: str) -> tuple[str, dict[str, Any], tuple[EnvironmentEvidence, ...]]:
        path = self._latest_runtime_log()
        if path is None:
            raise FileNotFoundError("runtime log not found")
        tail = _redact(self._read_tail(path))
        lines = [line.rstrip() for line in tail.splitlines() if line.strip()]
        query = _clean(action_input, 120)
        generic = _norm(query) in {"", "latest", "current", "runtime", "log", "tail"}
        selected = lines
        if not generic:
            tokens = [token for token in _norm(query).split() if token not in {"doc", "xem", "kiem", "log", "runtime", "loc"}]
            if tokens:
                selected = [line for line in lines if all(token in _norm(line) for token in tokens)]
        selected = selected[-24:]
        stat = path.stat()
        preview = selected[-5:] if selected else lines[-5:]
        text = f"Tôi đã đọc phần cuối log {path.name}."
        if query and not generic:
            text += f" Có {len(selected)} dòng khớp bộ lọc “{query}”."
        if preview:
            text += " Dòng mới nhất: " + " | ".join(_clean(line, 220) for line in preview[-3:])
        evidence = (EnvironmentEvidence(
            _hash_id("runtime_log", f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"),
            "runtime_log_file",
            f"file={path.name} size={stat.st_size} selected_lines={len(selected)}",
            captured_at=float(stat.st_mtime),
            confidence=0.98,
            data={"file_name": path.name, "size": stat.st_size},
        ),)
        return text, {
            "verification_key": "inspect_runtime_log",
            "file_name": path.name,
            "file_mtime": stat.st_mtime,
            "file_size": stat.st_size,
            "query": query,
            "matched_line_count": len(selected),
            "tail": "\n".join(selected)[-6000:],
        }, evidence

    def _read_current_error(self, action_input: str) -> tuple[str, dict[str, Any], tuple[EnvironmentEvidence, ...]]:
        del action_input
        candidates: list[dict[str, Any]] = []
        evidence: list[EnvironmentEvidence] = []
        path = self._latest_runtime_log()
        if path is not None:
            content = _redact(self._read_tail(path, max_bytes=220_000))
            cluster = self._extract_latest_error_cluster(content)
            stat = path.stat()
            evidence.append(EnvironmentEvidence(
                _hash_id("runtime_log", f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"),
                "runtime_log_file",
                f"error_scan file={path.name}",
                captured_at=float(stat.st_mtime),
                confidence=0.98,
            ))
            if cluster:
                candidates.append({"source": "runtime_log", "text": cluster, "captured_at": stat.st_mtime, "confidence": 0.95})

        snapshot = self._screen_snapshot()
        if snapshot is not None and not bool(getattr(snapshot, "privacy_blocked", False)):
            blocks = tuple(getattr(snapshot, "blocks", ()) or ())
            error_blocks = []
            for block in blocks:
                text = _redact(_clean(getattr(block, "text", ""), 1000))
                if self._looks_like_concrete_error(text):
                    error_blocks.append((float(getattr(block, "captured_at", 0.0) or 0.0), text, block))
            evidence.append(EnvironmentEvidence(
                f"screen_snapshot:revision:{int(getattr(snapshot, 'revision', 0) or 0)}",
                "screen_evidence_store",
                f"error_blocks={len(error_blocks)}",
                captured_at=float(getattr(snapshot, "captured_at", 0.0) or time.time()),
                confidence=0.8,
            ))
            if error_blocks:
                captured_at, text, _block = max(error_blocks, key=lambda item: item[0])
                candidates.append({"source": "screen_text", "text": text, "captured_at": captured_at, "confidence": 0.78})

        if not evidence:
            raise RuntimeError("no error observation source")
        candidates.sort(key=lambda item: (float(item["captured_at"]), float(item["confidence"])), reverse=True)
        if not candidates:
            return "Tôi đã kiểm tra log và text màn hình hiện có. Chưa thấy lỗi cụ thể nào đủ evidence.", {
                "verification_key": "read_current_error",
                "found": False,
                "source_count": len(evidence),
            }, tuple(evidence)
        best = candidates[0]
        error_text = _clean(best["text"], 900)
        text = f"Lỗi cụ thể gần nhất từ {best['source']}: {error_text}"
        return text, {
            "verification_key": "read_current_error",
            "found": True,
            "source": best["source"],
            "error_text": error_text,
            "captured_at": best["captured_at"],
            "candidate_count": len(candidates),
        }, tuple(evidence)

    @staticmethod
    def _looks_like_concrete_error(text: str) -> bool:
        folded = _norm(text)
        if not folded or any(marker in folded for marker in ("errors 0", "error 0", "error none", "no error", "khong co loi")):
            return False
        return bool(re.search(r"\b(traceback|exception|error|fatal|assertionerror|nameerror|typeerror|valueerror|permissionerror|winerror|failed|failure|crash|loi)\b", folded))

    def _extract_latest_error_cluster(self, content: str) -> str:
        lines = content.splitlines()
        hit = -1
        for index, line in enumerate(lines):
            if self._looks_like_concrete_error(line):
                hit = index
        if hit < 0:
            return ""
        start = max(0, hit - 2)
        # Nếu đang nằm trong traceback, lùi về dòng Traceback gần nhất.
        for index in range(hit, max(-1, hit - 30), -1):
            if "traceback" in _norm(lines[index]):
                start = index
                break
        end = min(len(lines), hit + 5)
        chunk = [line.strip() for line in lines[start:end] if line.strip()]
        return " | ".join(_clean(line, 260) for line in chunk)[-1200:]

    def _inspect_selected_file(self, action_input: str) -> tuple[str, dict[str, Any], tuple[EnvironmentEvidence, ...]]:
        root = self._project_root()
        candidate, source, confidence = self._selected_file_candidate()
        if not candidate:
            evidence = (EnvironmentEvidence(
                "selected_file:none",
                "selected_file_resolver",
                "no concrete file candidate",
                captured_at=time.time(),
                confidence=0.65,
            ),)
            return "Tôi đã kiểm tra nhưng chưa xác định được file nào đang được chọn.", {
                "verification_key": "inspect_selected_file",
                "found": False,
                "content_read": False,
            }, evidence

        path: Path | None = None
        try:
            maybe = Path(candidate).expanduser()
            if maybe.is_absolute():
                path = maybe.resolve()
            elif root is not None and (root / maybe).exists():
                path = (root / maybe).resolve()
            elif root is not None and (root / "src" / maybe).exists():
                path = (root / "src" / maybe).resolve()
        except Exception:
            path = None

        content_read = False
        preview = ""
        size = 0
        safe_path = False
        if path is not None and path.is_file():
            safe_path = self._file_read_allowed(path, root=root)
            size = path.stat().st_size
            if safe_path and size <= 1_000_000 and path.suffix.lower() in self._text_extensions():
                try:
                    preview = _redact(path.read_text(encoding="utf-8", errors="replace")[:2600])
                    content_read = True
                except Exception:
                    preview = ""
        shown = path.name if path is not None else Path(candidate).name
        text = f"File đang được cửa sổ/bridge chỉ tới: {shown}."
        if path is None or not path.exists():
            text += " Tôi chỉ có tên từ title, chưa có path thật nên chưa đọc nội dung."
        elif not safe_path:
            text += " Path nằm ngoài project và quyền đọc ngoài project đang tắt."
        else:
            text += f" Kích thước {size} byte."
            if content_read and any(token in _norm(action_input) for token in ("content", "noi dung", "doc file", "preview")):
                first = " ".join(preview.splitlines()[:4]).strip()[:500]
                if first:
                    text += f" Preview: “{first}”."
        evidence_summary = f"candidate={candidate}; resolved={str(path) if path else ''}; content_read={content_read}"
        evidence = (EnvironmentEvidence(
            _hash_id("selected_file", evidence_summary),
            source,
            evidence_summary,
            captured_at=time.time(),
            confidence=confidence,
            data={"file_name": shown, "resolved": bool(path and path.exists()), "content_read": content_read},
        ),)
        return text, {
            "verification_key": "inspect_selected_file",
            "found": True,
            "file_name": shown,
            "resolved_path": str(path) if path is not None and path.exists() else "",
            "source": source,
            "confidence": confidence,
            "size_bytes": size,
            "content_read": content_read,
            "preview": preview[:2600] if content_read else "",
            "inside_project": safe_path,
        }, evidence

    def _selected_file_candidate(self) -> tuple[str, str, float]:
        explicit = os.getenv("ERIS_SELECTED_FILE_PATH", "").strip()
        if explicit:
            return explicit, "selected_file_bridge:env", 1.0
        owner = self.owner
        for attr in ("selected_file", "active_file", "current_file"):
            value = _clean(getattr(owner, attr, "") if owner is not None else "", 1200)
            if value:
                return value, f"runtime_owner:{attr}", 0.95
        snapshot = self._capture_snapshot()
        metadata = getattr(snapshot, "content_metadata", {}) if snapshot is not None else {}
        if isinstance(metadata, Mapping):
            for key in ("selected_file", "active_file", "current_file", "file_path", "path"):
                value = _clean(metadata.get(key), 1200)
                if value:
                    return value, f"telemetry_content_metadata:{key}", 0.9
        screen = self._screen_snapshot()
        title = _clean(
            getattr(getattr(screen, "window", None), "title", "")
            or getattr(snapshot, "active_window_title", "")
            or getattr(snapshot, "active_window", "")
            or getattr(snapshot, "active_app", ""),
            500,
        )
        match = re.search(
            r"(?i)([A-Za-z0-9_ .()\[\]@+-]+\.(?:py|gd|cs|tscn|tres|json|jsonl|yaml|yml|toml|ini|md|txt|log|ps1|sh|js|ts|tsx|jsx|html|css|cpp|h|hpp|java|kt|rs|go))",
            title,
        )
        if match:
            return match.group(1).strip(), "active_window_title", 0.68
        return "", "selected_file_resolver", 0.0

    @staticmethod
    def _text_extensions() -> set[str]:
        return {
            ".py", ".gd", ".cs", ".tscn", ".tres", ".json", ".jsonl", ".yaml", ".yml",
            ".toml", ".ini", ".md", ".txt", ".log", ".ps1", ".sh", ".js", ".ts",
            ".tsx", ".jsx", ".html", ".css", ".cpp", ".h", ".hpp", ".java", ".kt",
            ".rs", ".go", ".xml", ".cfg",
        }

    @staticmethod
    def _file_read_allowed(path: Path, *, root: Path | None) -> bool:
        if _env_flag("ERIS_ENV_ALLOW_EXTERNAL_FILE_READ", False):
            return True
        if root is None:
            return False
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except Exception:
            return False

    def _get_project_status(self, action_input: str) -> tuple[str, dict[str, Any], tuple[EnvironmentEvidence, ...]]:
        del action_input
        root = self._project_root()
        if root is None or not root.exists():
            raise FileNotFoundError("project root unavailable")
        completed = self._run_git(root, "status", "--porcelain=v1", "--branch")
        if completed.returncode != 0:
            stderr = _redact(_clean(completed.stderr or completed.stdout, 500))
            evidence = (EnvironmentEvidence(
                _hash_id("project_root", str(root)),
                "project_root",
                f"git_status_failed={stderr}",
                captured_at=time.time(),
                confidence=0.95,
            ),)
            return "Tôi tìm được project root nhưng Git chưa trả được trạng thái repo.", {
                "verification_key": "get_project_status",
                "project_root": str(root),
                "git_available": True,
                "is_git_repo": False,
                "error": stderr,
            }, evidence
        lines = [line.rstrip() for line in (completed.stdout or "").splitlines() if line.strip()]
        branch_line = lines[0] if lines and lines[0].startswith("##") else ""
        entries = lines[1:] if branch_line else lines
        branch, ahead, behind = self._parse_branch_line(branch_line)
        staged = modified = untracked = conflicts = 0
        for line in entries:
            code = (line + "  ")[:2]
            if code == "??":
                untracked += 1
                continue
            if "U" in code or code in {"AA", "DD"}:
                conflicts += 1
            if code[0] not in {" ", "?"}:
                staged += 1
            if code[1] not in {" ", "?"}:
                modified += 1
        dirty = len(entries)
        repo_name = root.name
        text = f"Project {repo_name}: branch {branch or 'không rõ'}, {dirty} mục thay đổi"
        if dirty:
            text += f" ({staged} staged, {modified} modified, {untracked} untracked, {conflicts} conflict)"
        else:
            text += ", working tree sạch"
        if ahead or behind:
            text += f", ahead {ahead}, behind {behind}"
        text += "."
        evidence = (EnvironmentEvidence(
            _hash_id("git_status", f"{root}|{completed.stdout}"),
            "git_status_porcelain",
            f"branch={branch} dirty={dirty} staged={staged} modified={modified} untracked={untracked}",
            captured_at=time.time(),
            confidence=0.99,
            data={"project_root": str(root)},
        ),)
        return text, {
            "verification_key": "get_project_status",
            "project_root": str(root),
            "project_name": repo_name,
            "git_available": True,
            "is_git_repo": True,
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "dirty_count": dirty,
            "staged_count": staged,
            "modified_count": modified,
            "untracked_count": untracked,
            "conflict_count": conflicts,
            "porcelain": "\n".join(entries)[:5000],
        }, evidence

    @staticmethod
    def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            encoding="utf-8",
            errors="replace",
        )

    @staticmethod
    def _parse_branch_line(line: str) -> tuple[str, int, int]:
        text = line[2:].strip() if line.startswith("##") else line.strip()
        head = text.split("...", 1)[0].strip()
        if head.startswith("No commits yet on "):
            head = head.replace("No commits yet on ", "", 1).strip()
        ahead_match = re.search(r"ahead\s+(\d+)", text)
        behind_match = re.search(r"behind\s+(\d+)", text)
        return head, int(ahead_match.group(1)) if ahead_match else 0, int(behind_match.group(1)) if behind_match else 0


__all__ = [
    "ENVIRONMENT_CAPABILITY_CONTRACTS",
    "ENVIRONMENT_CAPABILITY_IDS",
    "ENVIRONMENT_CAPABILITY_VERSION",
    "EnvironmentActionResult",
    "EnvironmentActionState",
    "EnvironmentCapabilityContract",
    "EnvironmentCapabilityRuntime",
    "EnvironmentEvidence",
]
