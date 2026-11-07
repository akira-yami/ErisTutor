# -*- coding: utf-8 -*-
"""Quản lý secret theo version với activate/rollback nguyên tử.

Không dùng tên ``latest`` trong production. Manifest luôn trỏ tới một version cụ
thể; version cũ được giữ để rollback. Payload không bao giờ được log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _root() -> Path:
    configured = str(os.getenv("ERIS_PROJECT_ROOT", "") or "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parent


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SecretVersionStatus:
    active_version: str = ""
    previous_version: str = ""
    pending_version: str = ""
    active_fingerprint: str = ""
    updated_at: float = 0.0


class SecretVersionManager:
    def __init__(self, *, root: Path | None = None) -> None:
        base = Path(root) if root is not None else _root()
        self.directory = Path(os.getenv("ERIS_SECRET_VERSIONS_DIR", str(base / "config" / "secret_versions")))
        self.manifest_path = Path(os.getenv("ERIS_SECRET_MANIFEST_PATH", str(base / "config" / "secrets_manifest.json")))

    @staticmethod
    def validate_version(version: str) -> str:
        normalized = str(version or "").strip()
        if not _VERSION_RE.fullmatch(normalized) or normalized.lower() == "latest":
            raise ValueError("Version secret phải là tên cụ thể, không được dùng 'latest'.")
        return normalized

    def version_path(self, version: str) -> Path:
        return self.directory / f"{self.validate_version(version)}.json"

    def stage(self, version: str, payload: Mapping[str, Any]) -> str:
        version = self.validate_version(version)
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError("Payload secret phải là mapping không rỗng.")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.version_path(version)
        fingerprint = _digest(payload)
        if target.exists():
            current = json.loads(target.read_text(encoding="utf-8"))
            if _digest(current) != fingerprint:
                raise FileExistsError("Version đã tồn tại với nội dung khác; không ghi đè lịch sử.")
            return fingerprint[:12]
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(target)
        return fingerprint[:12]

    def _read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_manifest(self, payload: Mapping[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.manifest_path)

    def activate(self, version: str) -> SecretVersionStatus:
        version = self.validate_version(version)
        path = self.version_path(version)
        if not path.is_file():
            raise FileNotFoundError(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload:
            raise ValueError("Secret version không hợp lệ.")
        manifest = self._read_manifest()
        active = str(manifest.get("active_version") or "")
        next_manifest = {
            "active_version": version,
            "previous_version": active if active and active != version else str(manifest.get("previous_version") or ""),
            "pending_version": "",
            "active_fingerprint": _digest(payload)[:12],
            "updated_at": time.time(),
        }
        self._write_manifest(next_manifest)
        return SecretVersionStatus(**next_manifest)

    def rollback(self) -> SecretVersionStatus:
        manifest = self._read_manifest()
        previous = str(manifest.get("previous_version") or "")
        if not previous:
            raise RuntimeError("Không có secret version trước để rollback.")
        return self.activate(previous)

    def active_path(self) -> Path | None:
        version = str(self._read_manifest().get("active_version") or "")
        if not version:
            return None
        path = self.version_path(version)
        return path if path.is_file() else None

    def active_payload(self) -> dict[str, Any]:
        path = self.active_path()
        if path is None:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def status(self) -> SecretVersionStatus:
        manifest = self._read_manifest()
        allowed = {field: manifest.get(field, "" if field != "updated_at" else 0.0) for field in SecretVersionStatus.__dataclass_fields__}
        return SecretVersionStatus(**allowed)


__all__ = ["SecretVersionManager", "SecretVersionStatus"]
