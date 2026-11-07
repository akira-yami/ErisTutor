# -*- coding: utf-8 -*-
"""Cấp và xác minh capability token bằng keyring HMAC có thể xoay khóa.

V6 không lưu một khóa duy nhất trong bootstrap nữa. ``CapabilityKeyRing`` giữ
một khóa đang ký và các khóa chỉ dùng để xác minh. Nhờ vậy process mới có thể
phát token bằng khóa mới trong khi token ngắn hạn đã phát bằng khóa cũ vẫn còn
được chấp nhận cho tới khi hết hạn.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import CapabilityToken

_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


def _canonical_json(value: Any) -> str:
    """Chuẩn hóa JSON để cùng payload luôn tạo cùng chữ ký."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_key_id(key_id: str) -> str:
    normalized = str(key_id or "").strip()
    if not _KEY_ID_PATTERN.fullmatch(normalized):
        raise ValueError("key_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,95}")
    return normalized


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Ghi file nguyên tử; không để lại key nửa chừng khi process chết."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        try:
            os.fchmod(descriptor, mode)
        except (AttributeError, OSError):
            pass
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        try:
            path.chmod(mode)
        except OSError:
            pass
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


class CapabilityKeyRing:
    """Tập khóa HMAC gồm một khóa ký và nhiều khóa xác minh.

    Thư mục keyring có cấu trúc::

        keyring.json
        keys/<key_id>.key

    Manifest chỉ chứa metadata, tuyệt đối không chứa secret. V6 cố tình không
    tự xóa khóa cũ khi rotate vì token đang bay ngoài process có thể vẫn còn TTL.
    """

    MANIFEST_VERSION = 1
    MINIMUM_SECRET_BYTES = 32

    def __init__(
        self,
        *,
        secrets_by_key_id: Mapping[str, bytes],
        active_key_id: str,
        directory: str | os.PathLike[str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        active = _validate_key_id(active_key_id)
        normalized: dict[str, bytes] = {}
        for raw_key_id, raw_secret in secrets_by_key_id.items():
            key_id = _validate_key_id(raw_key_id)
            secret = bytes(raw_secret)
            if len(secret) < self.MINIMUM_SECRET_BYTES:
                raise ValueError(
                    f"capability signing secret {key_id!r} must be at least {self.MINIMUM_SECRET_BYTES} bytes"
                )
            normalized[key_id] = secret
        if active not in normalized:
            raise ValueError("active_key_id is not present in keyring")
        self._secrets = normalized
        self._active_key_id = active
        self._directory = Path(directory).resolve() if directory is not None else None
        self._clock = clock
        self._lock = threading.RLock()
        manifest = self._manifest_path(self._directory) if self._directory is not None else None
        self._manifest_mtime_ns = manifest.stat().st_mtime_ns if manifest is not None and manifest.exists() else 0

    def _reload_from_disk_if_changed(self) -> None:
        """Tự nạp manifest mới để nhiều process thấy rotation mà không downtime."""
        if self._directory is None:
            return
        manifest_path = self._manifest_path(self._directory)
        try:
            mtime_ns = manifest_path.stat().st_mtime_ns
        except OSError:
            return
        if mtime_ns == self._manifest_mtime_ns:
            return
        with self._lock:
            if mtime_ns == self._manifest_mtime_ns:
                return
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if int(manifest.get("version", 0)) != self.MANIFEST_VERSION:
                raise ValueError("unsupported capability keyring manifest version")
            active = _validate_key_id(str(manifest.get("active_key_id") or ""))
            raw_keys = manifest.get("keys")
            if not isinstance(raw_keys, dict) or not raw_keys:
                raise ValueError("capability keyring has no keys")
            loaded: dict[str, bytes] = {}
            for raw_key_id, raw_meta in raw_keys.items():
                key_id = _validate_key_id(str(raw_key_id))
                meta = raw_meta if isinstance(raw_meta, dict) else {}
                if str(meta.get("status") or "") == "retired":
                    continue
                key_path = self._key_path(self._directory, key_id)
                secret = key_path.read_bytes()
                if len(secret) < self.MINIMUM_SECRET_BYTES:
                    raise ValueError(f"capability key {key_id!r} is too short")
                loaded[key_id] = secret
            if active not in loaded:
                raise ValueError("active capability key is missing")
            self._secrets = loaded
            self._active_key_id = active
            self._manifest_mtime_ns = mtime_ns

    @property
    def active_key_id(self) -> str:
        self._reload_from_disk_if_changed()
        return self._active_key_id

    @property
    def key_ids(self) -> tuple[str, ...]:
        self._reload_from_disk_if_changed()
        return tuple(sorted(self._secrets))

    @property
    def directory(self) -> Path | None:
        return self._directory

    def secret_for(self, key_id: str) -> bytes | None:
        self._reload_from_disk_if_changed()
        return self._secrets.get(str(key_id))

    @classmethod
    def from_single_secret(cls, *, secret: bytes, key_id: str, clock: Callable[[], float] = time.time) -> "CapabilityKeyRing":
        """Tạo keyring trong RAM cho unit test hoặc runtime nhúng."""
        return cls(secrets_by_key_id={key_id: secret}, active_key_id=key_id, clock=clock)

    @staticmethod
    def _manifest_path(directory: Path) -> Path:
        return directory / "keyring.json"

    @staticmethod
    def _key_path(directory: Path, key_id: str) -> Path:
        return directory / "keys" / f"{_validate_key_id(key_id)}.key"

    @classmethod
    def _create_key_file(cls, directory: Path, key_id: str) -> bytes:
        path = cls._key_path(directory, key_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_bytes(48)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_bytes()
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(secret)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            path.unlink(missing_ok=True)
            raise
        return secret

    @classmethod
    def load_or_create(
        cls,
        directory: str | os.PathLike[str],
        *,
        initial_key_id: str = "local-hmac-v1",
        clock: Callable[[], float] = time.time,
    ) -> "CapabilityKeyRing":
        """Nạp keyring từ disk hoặc tạo keyring đầu tiên theo cách nguyên tử."""
        root = Path(directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = cls._manifest_path(root)
        initial = _validate_key_id(initial_key_id)

        if not manifest_path.exists():
            cls._create_key_file(root, initial)
            now = float(clock())
            manifest = {
                "version": cls.MANIFEST_VERSION,
                "active_key_id": initial,
                "keys": {
                    initial: {
                        "status": "active",
                        "created_at": now,
                        "activated_at": now,
                    }
                },
            }
            # os.replace bảo đảm manifest hoặc cũ hoặc mới, không có trạng thái nửa JSON.
            if not manifest_path.exists():
                _atomic_write_bytes(manifest_path, (_canonical_json(manifest) + "\n").encode("utf-8"))

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid capability keyring manifest: {manifest_path}") from exc
        if int(manifest.get("version", 0)) != cls.MANIFEST_VERSION:
            raise ValueError("unsupported capability keyring manifest version")
        active = _validate_key_id(str(manifest.get("active_key_id") or ""))
        raw_keys = manifest.get("keys")
        if not isinstance(raw_keys, dict) or not raw_keys:
            raise ValueError("capability keyring has no keys")

        loaded: dict[str, bytes] = {}
        for raw_key_id, raw_meta in raw_keys.items():
            key_id = _validate_key_id(str(raw_key_id))
            meta = raw_meta if isinstance(raw_meta, dict) else {}
            if str(meta.get("status") or "") == "retired":
                continue
            key_path = cls._key_path(root, key_id)
            if not key_path.is_file():
                raise ValueError(f"capability key file missing: {key_path}")
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
            loaded[key_id] = key_path.read_bytes()
        return cls(secrets_by_key_id=loaded, active_key_id=active, directory=root, clock=clock)

    def rotate(self, *, new_key_id: str | None = None) -> str:
        """Tạo khóa ký mới và giữ khóa cũ ở chế độ verify-only.

        Việc rotate là thao tác vận hành hiếm. Hãy chạy một tiến trình quản trị tại
        một thời điểm. Các runtime dùng chung keyring sẽ tự reload khi manifest đổi.
        """
        if self._directory is None:
            raise RuntimeError("in-memory keyring cannot be rotated on disk")
        self._reload_from_disk_if_changed()
        now = float(self._clock())
        key_id = _validate_key_id(new_key_id or time.strftime("local-hmac-%Y%m%d-%H%M%S", time.gmtime(now)))
        if key_id in self._secrets:
            raise ValueError(f"key_id already exists: {key_id}")
        secret = self._create_key_file(self._directory, key_id)
        manifest_path = self._manifest_path(self._directory)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        keys = manifest.setdefault("keys", {})
        old_active = str(manifest.get("active_key_id") or self._active_key_id)
        if old_active in keys and isinstance(keys[old_active], dict):
            keys[old_active]["status"] = "verify_only"
            keys[old_active]["deactivated_at"] = now
        keys[key_id] = {"status": "active", "created_at": now, "activated_at": now}
        manifest["active_key_id"] = key_id
        _atomic_write_bytes(manifest_path, (_canonical_json(manifest) + "\n").encode("utf-8"))
        self._secrets[key_id] = secret
        self._active_key_id = key_id
        self._manifest_mtime_ns = manifest_path.stat().st_mtime_ns
        return key_id


class CapabilityAuthority:
    """Phát token ngắn hạn và xác minh token qua toàn bộ keyring."""

    MINIMUM_SECRET_BYTES = CapabilityKeyRing.MINIMUM_SECRET_BYTES

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        keyring: CapabilityKeyRing | None = None,
        secret: bytes | None = None,
        key_id: str = "local-hmac-v1",
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        # ``secret`` chỉ dành cho test/embedding. Bootstrap production luôn truyền keyring.
        if keyring is None:
            if secret is None:
                raise TypeError("keyring is required when secret is not provided")
            keyring = CapabilityKeyRing.from_single_secret(secret=secret, key_id=key_id, clock=clock)
        for label, value in (("issuer", issuer), ("audience", audience)):
            if not str(value or "").strip():
                raise ValueError(f"{label} cannot be empty")
        self._keyring = keyring
        self.issuer = str(issuer)
        self.audience = str(audience)
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    @property
    def key_id(self) -> str:
        return self._keyring.active_key_id

    @property
    def keyring(self) -> CapabilityKeyRing:
        return self._keyring

    @classmethod
    def from_keyring_directory(
        cls,
        directory: str | os.PathLike[str],
        *,
        issuer: str,
        audience: str,
        initial_key_id: str = "local-hmac-v1",
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> "CapabilityAuthority":
        keyring = CapabilityKeyRing.load_or_create(directory, initial_key_id=initial_key_id, clock=clock)
        return cls(
            keyring=keyring,
            issuer=issuer,
            audience=audience,
            clock=clock,
            id_factory=id_factory,
        )

    @staticmethod
    def _signature(secret: bytes, payload: Mapping[str, Any]) -> str:
        message = _canonical_json(dict(payload)).encode("utf-8")
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    def issue(
        self,
        *,
        subject_id: str,
        capabilities: frozenset[str] | set[str] | tuple[str, ...] | list[str],
        policy_version: str,
        scope: Mapping[str, Any],
        ttl_seconds: float = 60.0,
    ) -> CapabilityToken:
        if ttl_seconds <= 0 or ttl_seconds > 300:
            raise ValueError("capability token ttl must be in (0, 300]")
        capabilities_set = frozenset(str(item) for item in capabilities if str(item))
        if not capabilities_set:
            raise ValueError("at least one capability is required")
        now = float(self._clock())
        active_key_id = self._keyring.active_key_id
        secret = self._keyring.secret_for(active_key_id)
        if secret is None:
            raise RuntimeError("active capability key is unavailable")
        unsigned = CapabilityToken(
            token_id=f"cap:{self._id_factory()}",
            subject_id=str(subject_id),
            capabilities=capabilities_set,
            issued_at=now,
            expires_at=now + float(ttl_seconds),
            policy_version=str(policy_version),
            issuer=self.issuer,
            audience=self.audience,
            key_id=active_key_id,
            signature="pending",
            scope=dict(scope),
        )
        return replace(unsigned, signature=self._signature(secret, unsigned.signing_payload()))

    def verify(self, token: CapabilityToken) -> tuple[bool, str]:
        if token.issuer != self.issuer:
            return False, "token_issuer_mismatch"
        if token.audience != self.audience:
            return False, "token_audience_mismatch"
        secret = self._keyring.secret_for(token.key_id)
        if secret is None:
            return False, "token_key_id_unknown"
        expected = self._signature(secret, token.signing_payload())
        if not hmac.compare_digest(expected, token.signature):
            return False, "token_signature_invalid"
        return True, ""


__all__ = ["CapabilityAuthority", "CapabilityKeyRing"]
