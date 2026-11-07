# -*- coding: utf-8 -*-
"""Chính sách capability tại ranh giới Sandbox World.

Policy chỉ quyết định một command *có được phép thử thực thi hay không*. Nó không
sửa motive, không thay action thành phiên bản "ngoan" hơn và không ghi rằng side
effect đã xảy ra. Những việc ấy thuộc Mind, Actuator và WorldOutcome riêng biệt.
"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .contracts import CapabilityToken, SandboxCommand, SandboxCommandKind


def _normalize_host(value: str) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("host required")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid hostname") from exc


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    executable_allowlist: frozenset[str]
    workspace_root: str
    network_enabled: bool = False
    allowed_network_hosts: frozenset[str] = frozenset()
    allowed_http_methods: frozenset[str] = frozenset({"GET", "HEAD", "POST"})
    allowed_http_headers: frozenset[str] = frozenset({"accept", "content-type", "user-agent", "if-none-match"})
    max_runtime_seconds: int = 30
    max_output_bytes: int = 1_000_000
    max_memory_mb: int = 512
    max_command_age_seconds: float = 300.0
    max_future_skew_seconds: float = 30.0
    max_http_request_bytes: int = 256_000
    max_http_redirects: int = 0
    environment_allowlist: frozenset[str] = frozenset()
    writable: bool = True
    allow_symlinks: bool = False
    allow_executable_paths: bool = False
    require_https: bool = True
    allow_private_networks: bool = False
    policy_version: str = "v1"

    def __post_init__(self) -> None:
        if self.max_runtime_seconds <= 0 or self.max_output_bytes <= 0 or self.max_memory_mb <= 0:
            raise ValueError("resource limits must be positive")
        if self.max_command_age_seconds <= 0 or self.max_future_skew_seconds < 0:
            raise ValueError("command time limits must be valid")
        if self.max_http_request_bytes < 0 or not 0 <= self.max_http_redirects <= 10:
            raise ValueError("HTTP limits must be valid")
        object.__setattr__(self, "workspace_root", str(Path(self.workspace_root).resolve()))
        object.__setattr__(self, "executable_allowlist", frozenset(str(item) for item in self.executable_allowlist))
        object.__setattr__(self, "allowed_network_hosts", frozenset(_normalize_host(item) for item in self.allowed_network_hosts))
        object.__setattr__(self, "allowed_http_methods", frozenset(str(item).upper() for item in self.allowed_http_methods))
        object.__setattr__(self, "allowed_http_headers", frozenset(str(item).lower() for item in self.allowed_http_headers))


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    transforms: tuple[str, ...] = ()
    resolved_path: str | None = None


class CapabilityPolicy:
    def __init__(
        self,
        command_policy: CommandPolicy,
        *,
        token_verifier: Callable[[CapabilityToken], tuple[bool, str]],
    ) -> None:
        if not callable(token_verifier):
            raise TypeError("token_verifier is required")
        self.command_policy = command_policy
        self._token_verifier = token_verifier

    def _contains_symlink(self, root: Path, candidate: Path) -> bool:
        if self.command_policy.allow_symlinks:
            return False
        current = root
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return True
        for part in relative.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                return True
        return False

    def _resolve_workspace_path(self, raw_path: str) -> Path:
        if not raw_path.strip():
            raise ValueError("path required")
        if "\x00" in raw_path:
            raise ValueError("nul byte denied")
        root = Path(self.command_policy.workspace_root).resolve()
        raw = Path(raw_path)
        lexical = Path(os.path.abspath(str(root / raw if not raw.is_absolute() else raw)))
        try:
            lexical.relative_to(root)
        except ValueError as exc:
            raise PermissionError("path escapes workspace") from exc
        if self._contains_symlink(root, lexical):
            raise RuntimeError("symlink path denied")
        candidate = lexical.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError("path escapes workspace") from exc
        return candidate

    def revalidate_path(self, path: str | os.PathLike[str]) -> Path:
        """Kiểm tra lại path ngay trước I/O để giảm cửa sổ TOCTOU."""
        root = Path(self.command_policy.workspace_root).resolve()
        candidate = Path(path).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError("path escapes workspace") from exc
        if self._contains_symlink(root, candidate):
            raise PermissionError("symlink path denied")
        return candidate

    @staticmethod
    def _scope_values(scope: Mapping[str, Any], key: str) -> frozenset[str]:
        raw = scope.get(key)
        if raw is None:
            return frozenset()
        if isinstance(raw, str):
            return frozenset({raw})
        if isinstance(raw, (list, tuple, set, frozenset)):
            return frozenset(str(item) for item in raw)
        return frozenset()

    def _scope_denial(self, command: SandboxCommand, token: CapabilityToken) -> str | None:
        scope = token.scope
        if command.subject_id and command.subject_id != token.subject_id:
            return "token_subject_mismatch"
        for field_name, scope_key in (
            (command.command_id, "command_ids"),
            (command.action_id, "action_ids"),
            (command.decision_id, "decision_ids"),
            (command.idempotency_key, "idempotency_keys"),
        ):
            allowed = self._scope_values(scope, scope_key)
            if allowed and field_name not in allowed:
                return f"{scope_key[:-1]}_outside_token_scope"
        return None

    def _http_denial(self, args: Mapping[str, Any], token: CapabilityToken) -> str | None:
        if not self.command_policy.network_enabled:
            return "network_disabled"
        raw_url = str(args.get("url") or "").strip()
        raw_host = str(args.get("host") or "").strip()
        try:
            if raw_url:
                if len(raw_url) > 16_384:
                    return "url_too_large"
                parsed = urlsplit(raw_url)
                if parsed.scheme.lower() not in {"http", "https"}:
                    return "http_scheme_denied"
                if self.command_policy.require_https and parsed.scheme.lower() != "https":
                    return "https_required"
                if parsed.username is not None or parsed.password is not None:
                    return "url_credentials_denied"
                if parsed.fragment:
                    return "url_fragment_denied"
                host = _normalize_host(parsed.hostname or "")
                if raw_host and host != _normalize_host(raw_host):
                    return "url_host_mismatch"
            else:
                host = _normalize_host(raw_host)
                scheme = str(args.get("scheme") or "https").lower()
                if scheme not in {"http", "https"}:
                    return "http_scheme_denied"
                if self.command_policy.require_https and scheme != "https":
                    return "https_required"
        except ValueError:
            return "host_required"

        token_hosts = frozenset(_normalize_host(item) for item in self._scope_values(token.scope, "network_hosts"))
        if host not in self.command_policy.allowed_network_hosts or (token_hosts and host not in token_hosts):
            return "host_not_allowlisted"

        method = str(args.get("method") or "GET").upper()
        if method not in self.command_policy.allowed_http_methods:
            return "http_method_not_allowlisted"
        headers = args.get("headers") or {}
        if not isinstance(headers, Mapping):
            return "http_headers_invalid"
        for raw_name, raw_value in headers.items():
            name = str(raw_name).strip().lower()
            if name in {"host", "connection", "proxy-authorization", "proxy-connection", "transfer-encoding"}:
                return "http_header_denied"
            if name not in self.command_policy.allowed_http_headers:
                return "http_header_not_allowlisted"
            if "\r" in str(raw_value) or "\n" in str(raw_value):
                return "http_header_injection_denied"
        body = args.get("body")
        if body is not None:
            body_bytes = body if isinstance(body, bytes) else str(body).encode("utf-8")
            if len(body_bytes) > self.command_policy.max_http_request_bytes:
                return "http_request_body_too_large"
            if method in {"GET", "HEAD"} and body_bytes:
                return "http_body_not_allowed_for_method"
        return None

    def authorize(self, command: SandboxCommand, token: CapabilityToken, *, now: float) -> PolicyDecision:
        # Xác minh token trước idempotency để token giả không thể chiếm key hợp lệ.
        verified, verification_reason = self._token_verifier(token)
        if not verified:
            return PolicyDecision(False, verification_reason or "token_signature_invalid")
        if token.policy_version != self.command_policy.policy_version:
            return PolicyDecision(False, "policy_version_mismatch")
        if not token.permits(command.requested_capability, now=now):
            return PolicyDecision(False, "capability_missing_or_expired")
        scope_denial = self._scope_denial(command, token)
        if scope_denial:
            return PolicyDecision(False, scope_denial)
        if command.created_at > now + self.command_policy.max_future_skew_seconds:
            return PolicyDecision(False, "command_from_future")
        if now - command.created_at > self.command_policy.max_command_age_seconds:
            return PolicyDecision(False, "command_expired")
        try:
            kind = command.kind if isinstance(command.kind, SandboxCommandKind) else SandboxCommandKind(str(command.kind))
        except ValueError:
            return PolicyDecision(False, "unsupported_command_kind")
        args = dict(command.arguments)

        if kind in (SandboxCommandKind.READ_FILE, SandboxCommandKind.WRITE_FILE):
            raw_path = str(args.get("path") or "")
            try:
                path = self._resolve_workspace_path(raw_path)
            except ValueError as exc:
                return PolicyDecision(False, "path_required" if "required" in str(exc) else "path_invalid")
            except PermissionError:
                return PolicyDecision(False, "path_outside_workspace")
            except RuntimeError:
                return PolicyDecision(False, "symlink_path_denied")
            prefixes = self._scope_values(token.scope, "workspace_prefixes")
            if prefixes:
                rel = path.relative_to(Path(self.command_policy.workspace_root)).as_posix()
                if not any(rel == prefix.rstrip("/") or rel.startswith(prefix.rstrip("/") + "/") for prefix in prefixes):
                    return PolicyDecision(False, "path_outside_token_scope")
            if kind is SandboxCommandKind.WRITE_FILE and not self.command_policy.writable:
                return PolicyDecision(False, "workspace_read_only")
            return PolicyDecision(True, resolved_path=str(path))

        if kind is SandboxCommandKind.RUN_COMMAND:
            argv = args.get("argv")
            if not isinstance(argv, (list, tuple)) or not argv:
                return PolicyDecision(False, "argv_required")
            if len(argv) > 64 or sum(len(str(item)) for item in argv) > 16_384:
                return PolicyDecision(False, "argv_too_large")
            raw_executable = str(argv[0])
            if not self.command_policy.allow_executable_paths and (Path(raw_executable).is_absolute() or "/" in raw_executable or "\\" in raw_executable):
                return PolicyDecision(False, "executable_path_denied")
            executable = Path(raw_executable).name
            if executable not in self.command_policy.executable_allowlist:
                return PolicyDecision(False, "executable_not_allowlisted")
            metacharacters = (";", "&&", "||", "`", "$(", "\n", "\r", "\x00")
            if any(any(marker in str(arg) for marker in metacharacters) for arg in argv):
                return PolicyDecision(False, "shell_metacharacters_denied")
            return PolicyDecision(True)

        if kind is SandboxCommandKind.HTTP_REQUEST:
            denial = self._http_denial(args, token)
            return PolicyDecision(denial is None, denial or "")

        if kind is SandboxCommandKind.SYSTEM_ACTION:
            action_name = str(args.get("action_name") or "").strip()
            if not action_name:
                return PolicyDecision(False, "action_name_required")
            if action_name != command.requested_capability:
                return PolicyDecision(False, "system_action_capability_mismatch")
            if action_name in {"click_mouse", "type_text"}:
                try:
                    desktop_input = json.loads(str(args.get("action_input") or ""))
                except ValueError:
                    return PolicyDecision(False, "desktop_input_invalid")
                hwnd = desktop_input.get("hwnd") if isinstance(desktop_input, dict) else None
                if type(hwnd) is not int or hwnd <= 0:
                    return PolicyDecision(False, "desktop_input_invalid")
                if str(hwnd) not in self._scope_values(token.scope, "desktop_window_handles"):
                    return PolicyDecision(False, "desktop_window_outside_token_scope")
            return PolicyDecision(True)

        if kind in (SandboxCommandKind.BODY_FRAME, SandboxCommandKind.TTS, SandboxCommandKind.SIMULATE):
            return PolicyDecision(True)
        return PolicyDecision(False, "unsupported_command_kind")

    def filtered_environment(self, source: Mapping[str, str] | None = None) -> dict[str, str]:
        """Chỉ chuyển các biến môi trường đã allowlist vào process con."""
        source = dict(source or os.environ)
        return {key: value for key, value in source.items() if key in self.command_policy.environment_allowlist}
