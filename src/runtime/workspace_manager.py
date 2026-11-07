# -*- coding: utf-8 -*-
"""Quản lý nơi Eris sống và các workspace được người dùng giao.

ErisHome là dữ liệu runtime bền vững, không phải source repo và không cần Git.
Repo chỉ trở thành workspace sau một hành động rõ ràng: join từ link/path/ZIP,
hoặc switch sang workspace đã đăng ký. Tuyệt đối không suy source code của Eris
từ vị trí ``__file__`` rồi tự coi đó là nhà hay project đang active.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

ERIS_HOME_VERSION = "eris_home.v1.0"
WORKSPACE_REGISTRY_VERSION = 2


def default_eris_home() -> Path:
    """Trả về nhà runtime mặc định, độc lập với repo/source hiện tại."""
    explicit = str(os.getenv("ERIS_HOME", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    settings_env = str(os.getenv("ERIS_RUNTIME_SETTINGS_PATH", "") or "").strip()
    if settings_env:
        candidate_parent = Path(settings_env).parent.parent
        if candidate_parent.is_dir():
            return candidate_parent.resolve()
    for candidate in [
        Path.home() / "OneDrive" / "Tài liệu" / "ErisLive",
        Path.home() / "OneDrive" / "Documents" / "ErisLive",
        Path.home() / "Documents" / "ErisLive",
    ]:
        if candidate.is_dir():
            return candidate.resolve()
    return (Path.home() / "Documents" / "ErisLive").resolve()


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    workspace_id: str
    display_name: str
    path: str
    source_type: str
    source: str = ""
    remote_url: str = ""
    default_branch: str = ""
    joined_at: float = 0.0
    last_opened_at: float = 0.0
    managed_copy: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ErisHome:
    """Cây thư mục sống của bản runtime đã build."""

    DIRECTORY_NAMES = (
        "config", "memory", "logs", "cache", "patches", "workspaces",
        "downloads", "state", "runtime_data",
    )

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else default_eris_home()
        self.ensure()

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in self.DIRECTORY_NAMES:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        marker = self.root / "state" / "eris_home.json"
        if not marker.exists():
            _atomic_json_write(marker, {
                "schema": ERIS_HOME_VERSION,
                "created_at": time.time(),
                "root": str(self.root),
                "note_vi": "Đây là dữ liệu runtime của Eris, không phải source repo.",
            })
        return self.root

    def path(self, name: str) -> Path:
        if name not in self.DIRECTORY_NAMES:
            raise KeyError(name)
        return self.root / name


class WorkspaceManager:
    """Đăng ký, gia nhập và chuyển workspace mà không biết source nhà của Eris."""

    def __init__(self, owner: Any | None = None, *, logger: Any | None = None) -> None:
        self.owner = owner
        self.logger = logger
        self._lock = threading.RLock()
        configured_home = self._setting("eris_home", "")
        self.home = ErisHome(configured_home or None)
        configured_workspace_root = str(self._setting("workspace_root", "") or "").strip()
        self.workspace_root = (
            Path(configured_workspace_root).expanduser().resolve()
            if configured_workspace_root else self.home.path("workspaces")
        )
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.home.path("state") / "workspace_registry.json"
        self._registry = self._load_registry()

        # Chỉ bootstrap từ env có tên rõ là ACTIVE_WORKSPACE. Không đọc
        # ERIS_PROJECT_ROOT, owner.project_root, memory path hay vị trí source.
        explicit_active = str(os.getenv("ERIS_ACTIVE_WORKSPACE_ROOT", "") or "").strip()
        if explicit_active:
            try:
                record = self.register_local(explicit_active, activate=True, source_type="env")
                self._log("info", "[workspace] bootstrapped explicit active=%s", record.path)
            except Exception as exc:
                self._log("warning", "[workspace] explicit active path invalid: %s", exc)

    @property
    def active_workspace_id(self) -> str:
        return str(self._registry.get("active_workspace_id", "") or "")

    def active_record(self) -> WorkspaceRecord | None:
        workspace_id = self.active_workspace_id
        if not workspace_id:
            return None
        payload = self._registry.get("workspaces", {}).get(workspace_id)
        return _record_from_payload(payload) if isinstance(payload, Mapping) else None

    def active_path(self) -> Path | None:
        record = self.active_record()
        if record is None:
            return None
        path = Path(record.path).expanduser().resolve()
        return path if path.is_dir() else None

    def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        items = self._registry.get("workspaces", {})
        records = [_record_from_payload(value) for value in items.values() if isinstance(value, Mapping)]
        return tuple(sorted(records, key=lambda item: item.last_opened_at, reverse=True))

    @property
    def favorite_workspace_ids(self) -> tuple[str, ...]:
        values = self._registry.get("favorite_workspace_ids", [])
        if not isinstance(values, list):
            return ()
        known = self._registry.get("workspaces", {})
        return tuple(str(value) for value in values if str(value) in known)

    def set_favorite(self, workspace_ref: str, favorite: bool = True) -> WorkspaceRecord:
        """Pin/unpin a registered workspace for one-click access in Agent Console."""
        record = self._resolve_record(workspace_ref)
        with self._lock:
            favorites = [str(value) for value in self._registry.get("favorite_workspace_ids", [])]
            favorites = [value for value in favorites if value != record.workspace_id]
            if favorite:
                favorites.insert(0, record.workspace_id)
            self._registry["favorite_workspace_ids"] = favorites[:24]
            self._save_registry()
        return record

    def workspace_snapshot(self, *, limit: int = 16) -> dict[str, Any]:
        """Public UI projection: favorites first, then recently opened workspaces."""
        active_id = self.active_workspace_id
        favorites = set(self.favorite_workspace_ids)
        records = list(self.list_workspaces())
        records.sort(
            key=lambda item: (
                0 if item.workspace_id in favorites else 1,
                -float(item.last_opened_at or 0.0),
                item.display_name.casefold(),
            )
        )
        payload = []
        for record in records[: max(1, int(limit))]:
            item = record.to_dict()
            item["favorite"] = record.workspace_id in favorites
            item["active"] = record.workspace_id == active_id
            payload.append(item)
        active = self.active_record()
        return {
            "active_workspace_id": active_id,
            "active_workspace": active.to_dict() if active else None,
            "favorite_workspace_ids": list(self.favorite_workspace_ids),
            "workspaces": payload,
        }

    def join(self, source: str, *, workspace_id: str = "", activate: bool = True) -> WorkspaceRecord:
        raw = str(source or "").strip().strip('"\'')
        if not raw:
            raise ValueError("Thiếu link, path hoặc ZIP để gia nhập workspace.")
        if _looks_like_git_url(raw):
            return self.join_git(raw, workspace_id=workspace_id, activate=activate)
        path = Path(raw).expanduser()
        if path.is_file() and path.suffix.lower() == ".zip":
            return self.join_zip(path, workspace_id=workspace_id, activate=activate)
        if path.is_dir():
            return self.register_local(path, workspace_id=workspace_id, activate=activate)
        raise FileNotFoundError(f"Không nhận diện được workspace source: {raw}")

    def register_local(
        self,
        path: str | os.PathLike[str],
        *,
        workspace_id: str = "",
        activate: bool = True,
        source_type: str = "local",
    ) -> WorkspaceRecord:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise NotADirectoryError(str(resolved))
        existing = self._find_by_path(resolved)
        if existing is not None:
            return self.switch(existing.workspace_id) if activate else existing
        workspace_id = self._unique_id(workspace_id or resolved.name or "workspace")
        now = time.time()
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            display_name=resolved.name or workspace_id,
            path=str(resolved),
            source_type=source_type,
            source=str(resolved),
            remote_url=self._git_remote(resolved),
            default_branch=self._git_branch(resolved),
            joined_at=now,
            last_opened_at=now if activate else 0.0,
            managed_copy=False,
        )
        self._store_record(record, activate=activate)
        return record

    def join_git(self, url: str, *, workspace_id: str = "", activate: bool = True) -> WorkspaceRecord:
        clean_url = str(url or "").strip()
        if not _looks_like_git_url(clean_url):
            raise ValueError("Git URL không hợp lệ.")
        existing = self._find_by_remote(clean_url)
        if existing is not None:
            return self.switch(existing.workspace_id) if activate else existing
        workspace_id = self._unique_id(workspace_id or _repo_slug(clean_url))
        target = (self.workspace_root / workspace_id).resolve()
        _ensure_within(target, self.workspace_root)
        if target.exists() and any(target.iterdir()):
            raise FileExistsError(f"Workspace target đã có dữ liệu: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            ["git", "clone", "--", clean_url, str(target)],
            cwd=str(self.workspace_root), capture_output=True, text=True,
            timeout=max(30, int(self._setting("git_clone_timeout_seconds", 600))),
            check=False, encoding="utf-8", errors="replace", shell=False,
        )
        if completed.returncode != 0:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            message = (completed.stderr or completed.stdout or "git clone failed")[-2000:]
            raise RuntimeError(message)
        now = time.time()
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            display_name=target.name,
            path=str(target),
            source_type="git",
            source=_redact_git_url(clean_url),
            remote_url=_redact_git_url(clean_url),
            default_branch=self._git_branch(target),
            joined_at=now,
            last_opened_at=now if activate else 0.0,
            managed_copy=True,
        )
        self._store_record(record, activate=activate)
        return record

    def join_zip(self, path: str | os.PathLike[str], *, workspace_id: str = "", activate: bool = True) -> WorkspaceRecord:
        archive_path = Path(path).expanduser().resolve()
        if not archive_path.is_file():
            raise FileNotFoundError(str(archive_path))
        workspace_id = self._unique_id(workspace_id or archive_path.stem or "workspace")
        target = (self.workspace_root / workspace_id).resolve()
        _ensure_within(target, self.workspace_root)
        target.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    destination = (target / info.filename).resolve()
                    _ensure_within(destination, target)
                archive.extractall(target)
            project_root = _single_child_root(target)
            now = time.time()
            record = WorkspaceRecord(
                workspace_id=workspace_id,
                display_name=project_root.name,
                path=str(project_root),
                source_type="zip",
                source=str(archive_path),
                remote_url=self._git_remote(project_root),
                default_branch=self._git_branch(project_root),
                joined_at=now,
                last_opened_at=now if activate else 0.0,
                managed_copy=True,
            )
            self._store_record(record, activate=activate)
            return record
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    def switch(self, workspace_ref: str) -> WorkspaceRecord:
        key = str(workspace_ref or "").strip()
        if not key:
            raise ValueError("Thiếu workspace cần chuyển.")
        record = self._resolve_record(key)
        path = Path(record.path).expanduser().resolve()
        if not path.is_dir():
            raise FileNotFoundError(str(path))
        updated = WorkspaceRecord(**{
            **record.to_dict(),
            "last_opened_at": time.time(),
        })
        self._store_record(updated, activate=True)
        return updated

    @contextmanager
    def scoped_active(self, workspace_ref: str):
        """Temporarily bind ProjectOperations to one task workspace.

        This never persists or changes the workspace selected by the panel. The
        manager lock is held for the scoped tool call, so a UI switch waits rather
        than racing an executor and being accidentally restored afterward.
        """
        record = self._resolve_record(workspace_ref)
        path = Path(record.path).expanduser().resolve()
        if not path.is_dir():
            raise FileNotFoundError(str(path))
        with self._lock:
            previous = str(self._registry.get("active_workspace_id", "") or "")
            self._registry["active_workspace_id"] = record.workspace_id
            try:
                yield record
            finally:
                self._registry["active_workspace_id"] = previous

    def leave(self, workspace_ref: str = "", *, delete_managed_copy: bool = False) -> WorkspaceRecord:
        record = self._resolve_record(workspace_ref or self.active_workspace_id)
        with self._lock:
            workspaces = dict(self._registry.get("workspaces", {}))
            workspaces.pop(record.workspace_id, None)
            self._registry["workspaces"] = workspaces
            favorites = [str(value) for value in self._registry.get("favorite_workspace_ids", [])]
            self._registry["favorite_workspace_ids"] = [value for value in favorites if value != record.workspace_id]
            if self.active_workspace_id == record.workspace_id:
                self._registry["active_workspace_id"] = ""
            self._save_registry()
        if delete_managed_copy and record.managed_copy:
            path = Path(record.path).expanduser().resolve()
            if _is_within(path, self.workspace_root):
                shutil.rmtree(path, ignore_errors=True)
        return record

    def deactivate(self) -> None:
        with self._lock:
            self._registry["active_workspace_id"] = ""
            self._save_registry()

    def operation_dir(self, operation_id: str, *, create: bool = True) -> Path:
        workspace_id = self.active_workspace_id or "no_active_workspace"
        safe_op = _slug(operation_id or uuid.uuid4().hex)
        path = self.home.path("state") / "project_ops" / _slug(workspace_id) / safe_op
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def patch_output_dir(self) -> Path:
        workspace_id = self.active_workspace_id or "unassigned"
        path = self.home.path("patches") / _slug(workspace_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ledger_path(self) -> Path:
        return self.home.path("logs") / "project_ops_ledger.jsonl"

    def describe(self) -> dict[str, Any]:
        active = self.active_record()
        return {
            "eris_home": str(self.home.root),
            "workspace_root": str(self.workspace_root),
            "active_workspace_id": self.active_workspace_id,
            "active_workspace": active.to_dict() if active else None,
            "workspace_count": len(self._registry.get("workspaces", {})),
            "favorite_workspace_count": len(self.favorite_workspace_ids),
            "implicit_source_repo": False,
        }

    def _load_registry(self) -> dict[str, Any]:
        if self.registry_path.is_file():
            try:
                payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload["version"] = WORKSPACE_REGISTRY_VERSION
                    payload.setdefault("active_workspace_id", "")
                    payload.setdefault("workspaces", {})
                    payload.setdefault("favorite_workspace_ids", [])
                    return payload
            except Exception as exc:
                self._log("warning", "[workspace] registry lỗi, tạo mới: %s", exc)
        payload = {
            "version": WORKSPACE_REGISTRY_VERSION,
            "active_workspace_id": "",
            "workspaces": {},
            "favorite_workspace_ids": [],
        }
        _atomic_json_write(self.registry_path, payload)
        return payload

    def _save_registry(self) -> None:
        _atomic_json_write(self.registry_path, self._registry)

    def _store_record(self, record: WorkspaceRecord, *, activate: bool) -> None:
        with self._lock:
            workspaces = dict(self._registry.get("workspaces", {}))
            workspaces[record.workspace_id] = record.to_dict()
            self._registry["workspaces"] = workspaces
            if activate:
                self._registry["active_workspace_id"] = record.workspace_id
            self._save_registry()

    def _resolve_record(self, workspace_ref: str) -> WorkspaceRecord:
        key = str(workspace_ref or "").strip()
        workspaces = self._registry.get("workspaces", {})
        payload = workspaces.get(key)
        if isinstance(payload, Mapping):
            return _record_from_payload(payload)
        normalized = key.casefold()
        matches = []
        for value in workspaces.values():
            if not isinstance(value, Mapping):
                continue
            record = _record_from_payload(value)
            if normalized in {record.display_name.casefold(), Path(record.path).name.casefold()}:
                matches.append(record)
        if len(matches) == 1:
            return matches[0]
        raise KeyError(f"Không tìm thấy workspace duy nhất: {workspace_ref}")

    def _find_by_path(self, path: Path) -> WorkspaceRecord | None:
        for record in self.list_workspaces():
            try:
                if Path(record.path).expanduser().resolve() == path.resolve():
                    return record
            except Exception:
                continue
        return None

    def _find_by_remote(self, url: str) -> WorkspaceRecord | None:
        clean = _normalize_git_url(url)
        for record in self.list_workspaces():
            if record.remote_url and _normalize_git_url(record.remote_url) == clean:
                return record
        return None

    def _unique_id(self, value: str) -> str:
        base = _slug(value) or "workspace"
        existing = set(self._registry.get("workspaces", {}))
        if base not in existing:
            return base
        index = 2
        while f"{base}-{index}" in existing:
            index += 1
        return f"{base}-{index}"

    def _git_remote(self, root: Path) -> str:
        if not (root / ".git").exists() or shutil.which("git") is None:
            return ""
        completed = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, check=False,
            encoding="utf-8", errors="replace", shell=False,
        )
        return _redact_git_url((completed.stdout or "").strip()) if completed.returncode == 0 else ""

    def _git_branch(self, root: Path) -> str:
        if not (root / ".git").exists() or shutil.which("git") is None:
            return ""
        completed = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            capture_output=True, text=True, timeout=10, check=False,
            encoding="utf-8", errors="replace", shell=False,
        )
        return (completed.stdout or "").strip() if completed.returncode == 0 else ""

    def _setting(self, key: str, default: Any) -> Any:
        settings = getattr(self.owner, "runtime_settings", {}) if self.owner is not None else {}
        block = settings.get("project_ops", {}) if isinstance(settings, Mapping) else {}
        return block.get(key, default) if isinstance(block, Mapping) else default

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level, self.logger.info)(message, *args)
        except Exception:
            pass


def extract_workspace_source(text: str) -> str:
    """Lấy Git URL, ZIP hoặc path Windows/POSIX rõ ràng từ câu lệnh."""
    raw = str(text or "")
    url = re.search(r"(?:(?:https?|file|ssh|git)://[^\s<>\"]+|git@[^\s<>\"]+)", raw, flags=re.I)
    if url:
        return url.group(0).rstrip(".,;)")
    quoted = re.search(r"[\"']([^\"']+\.(?:zip))[\"']", raw, flags=re.I)
    if quoted:
        return quoted.group(1)
    windows = re.search(r"[A-Za-z]:[\\/][^\r\n<>\"|?*]+", raw)
    if windows:
        return windows.group(0).strip().rstrip(".,;)")
    return ""


def _looks_like_git_url(value: str) -> bool:
    text = str(value or "").strip()
    if text.startswith("git@"):
        return ":" in text
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return False
    return parsed.scheme in {"http", "https", "ssh", "git", "file"} and bool(parsed.netloc or parsed.path)


def _repo_slug(url: str) -> str:
    path = str(url or "").rstrip("/").rsplit("/", 1)[-1]
    if ":" in path and "/" not in path:
        path = path.rsplit(":", 1)[-1]
    return _slug(path.removesuffix(".git")) or "workspace"


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return text[:80].lower()


def _single_child_root(target: Path) -> Path:
    children = [item for item in target.iterdir() if item.name not in {"__MACOSX"}]
    if len(children) == 1 and children[0].is_dir():
        return children[0].resolve()
    return target.resolve()


def _record_from_payload(payload: Mapping[str, Any]) -> WorkspaceRecord:
    fields = WorkspaceRecord.__dataclass_fields__
    clean = {name: payload.get(name, field.default) for name, field in fields.items()}
    return WorkspaceRecord(**clean)


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _ensure_within(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PermissionError(f"Path vượt workspace root: {path}") from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _normalize_git_url(url: str) -> str:
    return _redact_git_url(str(url or "").strip()).rstrip("/").removesuffix(".git").casefold()


def _redact_git_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if text.startswith("git@"):
        return text
    try:
        parsed = urllib.parse.urlsplit(text)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return urllib.parse.urlunsplit((parsed.scheme, host + port, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return re.sub(r"//[^/@\s]+@", "//", text)


__all__ = [
    "ERIS_HOME_VERSION", "WORKSPACE_REGISTRY_VERSION", "ErisHome",
    "WorkspaceRecord", "WorkspaceManager", "default_eris_home",
    "extract_workspace_source",
]
