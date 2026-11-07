from __future__ import annotations

"""Privacy boundary for Deep Environment.

The sensor layer is intentionally stronger than the old OCR poller, so the
policy is deliberately boring and strict: decide a scope before content is
read, treat password/UAC/credential surfaces as hard stops, and default
messaging applications to metadata-only.  Raw clipboard/password text is never
eligible for persistence through this module.
"""

import json
import os
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping
from urllib.parse import urlparse


class PrivacyScope(IntEnum):
    OFF = 0
    METADATA = 1
    SEMANTIC = 2
    CONTENT_EPHEMERAL = 3
    CONTENT_ALLOWED = 4

    @classmethod
    def parse(cls, value: Any, default: "PrivacyScope" = None) -> "PrivacyScope":
        if default is None:
            default = cls.SEMANTIC
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().upper()
        aliases = {
            "PRIVATE": cls.METADATA,
            "SENSITIVE": cls.OFF,
            "NORMAL": default,
            "CONTENT": cls.CONTENT_EPHEMERAL,
        }
        if text in aliases:
            return aliases[text]
        try:
            return cls[text]
        except Exception:
            return default


@dataclass(frozen=True, slots=True)
class PrivacyDecision:
    scope: PrivacyScope
    sensitive: bool = False
    reason: str = "default"
    content_retained: bool = False
    ttl_ms: int = 0

    @property
    def content_readable(self) -> bool:
        return self.scope >= PrivacyScope.CONTENT_EPHEMERAL and not self.sensitive


class EnvironmentPolicy:
    _OFF_APPS = (
        "1password",
        "bitwarden",
        "keepass",
        "credential",
        "windows security",
        "securityhealth",
    )
    _METADATA_APPS = (
        "discord",
        "telegram",
        "zalo",
        "messenger",
        "slack",
        "outlook",
        "teams",
        "signal",
        "whatsapp",
    )
    _BANKING_HINTS = ("bank", "banking", "paypal", "stripe dashboard", "wise")
    _INCOGNITO_HINTS = ("incognito", "inprivate", "private browsing")

    def __init__(self, *, overrides: Mapping[str, Any] | None = None, ephemeral_ttl_ms: int = 90_000) -> None:
        # Nếu caller không truyền policy riêng, cho phép launcher cấu hình scope
        # theo app/domain/workspace bằng JSON. Không có config thì vẫn giữ default
        # bảo thủ của module, không tự nâng quyền đọc content.
        loaded: dict[str, Any] = {}
        if overrides is None:
            raw = os.getenv("ERIS_ENVIRONMENT_PRIVACY_OVERRIDES", "").strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, Mapping):
                        loaded.update(parsed)
                except Exception:
                    pass
            for domain in os.getenv("ERIS_CONTENT_EPHEMERAL_DOMAINS", "").split(","):
                domain = domain.strip()
                if domain:
                    loaded[domain] = "CONTENT_EPHEMERAL"
            for workspace in os.getenv("ERIS_CONTENT_ALLOWED_WORKSPACES", "").split(os.pathsep):
                workspace = workspace.strip()
                if workspace:
                    loaded[workspace] = "CONTENT_ALLOWED"
        else:
            loaded.update(dict(overrides))
        self._overrides = {str(k).strip().lower(): PrivacyScope.parse(v) for k, v in loaded.items()}
        self.ephemeral_ttl_ms = max(1_000, int(ephemeral_ttl_ms))

    @staticmethod
    def _norm(value: Any) -> str:
        return " ".join(str(value or "").lower().split())

    def scope_for(
        self,
        *,
        process_name: str = "",
        window_title: str = "",
        url: str = "",
        workspace: str = "",
        is_password: bool = False,
        elevated_credential_ui: bool = False,
    ) -> PrivacyDecision:
        process = self._norm(process_name)
        title = self._norm(window_title)
        workspace_norm = self._norm(workspace)
        host = ""
        try:
            host = (urlparse(str(url or "")).hostname or "").lower()
        except Exception:
            pass
        haystack = " | ".join((process, title, host, workspace_norm))

        if is_password:
            return PrivacyDecision(PrivacyScope.OFF, True, "password_control")
        if elevated_credential_ui or "user account control" in title or "windows credential" in title:
            return PrivacyDecision(PrivacyScope.OFF, True, "credential_or_uac")
        if any(token in haystack for token in self._OFF_APPS):
            return PrivacyDecision(PrivacyScope.OFF, True, "sensitive_application")
        if any(token in title for token in self._INCOGNITO_HINTS):
            return PrivacyDecision(PrivacyScope.METADATA, True, "private_browsing")
        if any(token in haystack for token in self._BANKING_HINTS):
            return PrivacyDecision(PrivacyScope.OFF, True, "financial_surface")

        for key, scope in self._overrides.items():
            if key and key in haystack:
                ttl = self.ephemeral_ttl_ms if scope == PrivacyScope.CONTENT_EPHEMERAL else 0
                return PrivacyDecision(scope, scope <= PrivacyScope.METADATA, f"override:{key}", scope == PrivacyScope.CONTENT_ALLOWED, ttl)

        if any(token in haystack for token in self._METADATA_APPS):
            return PrivacyDecision(PrivacyScope.METADATA, True, "private_communications")

        # IDE/workspace content may be read transiently, but must not be persisted
        # unless an explicit override moves it to CONTENT_ALLOWED.
        if any(token in process for token in ("code.exe", "devenv.exe", "pycharm", "rider", "idea")) or workspace_norm:
            return PrivacyDecision(PrivacyScope.CONTENT_EPHEMERAL, False, "development_workspace", False, self.ephemeral_ttl_ms)

        # Browser defaults to semantic metadata.  Page text needs an explicit
        # ephemeral/allowed rule because browser accessibility is not a storage permit.
        if any(token in process for token in ("chrome", "msedge", "brave", "chromium", "firefox")):
            return PrivacyDecision(PrivacyScope.SEMANTIC, False, "browser_semantic_default")

        return PrivacyDecision(PrivacyScope.SEMANTIC, False, "semantic_default")

    def sanitize_payload(self, payload: Mapping[str, Any], decision: PrivacyDecision) -> dict[str, Any]:
        """Return the maximum payload permitted to cross the semantic bus."""
        if decision.scope == PrivacyScope.OFF:
            return {}
        data = dict(payload)
        always_drop = {
            "password",
            "password_text",
            "clipboard_text",
            "raw_clipboard",
            "cookies",
            "cookie",
            "secret",
            "secret_value",
            "form_value",
            "raw_keystrokes",
        }
        for key in tuple(data):
            if key.lower() in always_drop:
                data.pop(key, None)

        if decision.scope == PrivacyScope.METADATA:
            allow = {
                "pid", "hwnd", "process_name", "window_class", "event_type", "action",
                "role", "source", "format", "formats", "size_bucket", "workspace",
                "privacy_scope", "attention_level", "confidence",
            }
            data = {key: value for key, value in data.items() if key in allow}
        elif decision.scope == PrivacyScope.SEMANTIC:
            for key in ("raw_text", "text", "value", "selected_text", "page_text", "body", "content"):
                data.pop(key, None)
        return data

    @staticmethod
    def persistable_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Disk-safe representation. Raw content is never returned."""
        forbidden = ("text", "value", "body", "content", "clipboard", "password", "keystroke", "selected")
        return {
            key: value
            for key, value in dict(payload).items()
            if not any(token in str(key).lower() for token in forbidden)
        }


__all__ = ["EnvironmentPolicy", "PrivacyDecision", "PrivacyScope"]
