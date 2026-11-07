# -*- coding: utf-8 -*-
"""Cấu hình authority cho hardening Phase 7/8.

Các phase trước dùng nhiều biến môi trường vì rollout cần bật/tắt nhanh. Đến lúc
consolidation, để chúng tiếp tục phân tán khắp repo chỉ khiến một lần chạy có thể
mang ba sự thật khác nhau. Module này đọc runtime_settings trước, env chỉ còn là
compatibility/bootstrap và không chứa secret.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _setting(*path: str, default: Any = None) -> Any:
    try:
        from runtime.runtime_settings_store import runtime_bool, runtime_int, runtime_text

        if isinstance(default, bool):
            return runtime_bool(*path, default=default)
        if isinstance(default, int):
            return runtime_int(*path, default=default)
        return runtime_text(*path, default=str(default or ""))
    except Exception:
        return default


@dataclass(frozen=True, slots=True)
class HardeningConfig:
    profile: str = "development"
    production_validation_enabled: bool = False
    production_validation_turns: int = 24
    production_validation_dir: str = "logs/phase7_validation"
    panel_ack_required: bool = True
    provider_registry_strict: bool = True
    release_manifest_path: str = "config/hardening_release.json"
    release_guard_enabled: bool = True
    release_guard_fail_closed: bool = False
    legacy_cross_language_tts: bool = False
    legacy_romaji_tts: bool = False

    @classmethod
    def load(cls) -> "HardeningConfig":
        profile = str(
            _setting("hardening", "profile", default=os.getenv("ERIS_HARDENING_PROFILE", "development"))
            or "development"
        ).strip().lower()
        if profile not in {"development", "canary", "stable"}:
            profile = "development"
        turns_default = _env_int("ERIS_PHASE7_VALIDATION_TURNS", 24, minimum=20, maximum=30)
        turns = int(_setting("hardening", "production_validation_turns", default=turns_default) or turns_default)
        turns = max(20, min(30, turns))
        return cls(
            profile=profile,
            production_validation_enabled=bool(
                _setting(
                    "hardening",
                    "production_validation_enabled",
                    default=_env_bool("ERIS_PHASE7_VALIDATION_ENABLED", False),
                )
            ),
            production_validation_turns=turns,
            production_validation_dir=str(
                _setting(
                    "hardening",
                    "production_validation_dir",
                    default=os.getenv("ERIS_PHASE7_VALIDATION_DIR", "logs/phase7_validation"),
                )
                or "logs/phase7_validation"
            ).strip(),
            panel_ack_required=bool(
                _setting("hardening", "panel_ack_required", default=_env_bool("ERIS_PANEL_ACK_REQUIRED", True))
            ),
            provider_registry_strict=bool(
                _setting(
                    "hardening",
                    "provider_registry_strict",
                    default=_env_bool("ERIS_PROVIDER_REGISTRY_STRICT", True),
                )
            ),
            release_manifest_path=str(
                _setting(
                    "hardening",
                    "release_manifest_path",
                    default=os.getenv("ERIS_HARDENING_RELEASE_MANIFEST", "config/hardening_release.json"),
                )
                or "config/hardening_release.json"
            ).strip(),
            release_guard_enabled=bool(
                _setting(
                    "hardening",
                    "release_guard_enabled",
                    default=_env_bool("ERIS_HARDENING_RELEASE_GUARD", True),
                )
            ),
            release_guard_fail_closed=bool(
                _setting(
                    "hardening",
                    "release_guard_fail_closed",
                    default=_env_bool("ERIS_HARDENING_RELEASE_FAIL_CLOSED", False),
                )
            ),
            legacy_cross_language_tts=bool(
                _setting(
                    "hardening",
                    "legacy_cross_language_tts",
                    default=_env_bool("ERIS_TTS_LEGACY_CROSS_LANGUAGE", False),
                )
            ),
            legacy_romaji_tts=bool(
                _setting(
                    "hardening",
                    "legacy_romaji_tts",
                    default=_env_bool("ERIS_TTS_EDGE_ROMAJI", False),
                )
            ),
        )

    @property
    def stable(self) -> bool:
        return self.profile == "stable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["HardeningConfig"]
