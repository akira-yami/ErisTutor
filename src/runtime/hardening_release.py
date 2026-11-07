# -*- coding: utf-8 -*-
"""Release manifest + guard cho hardening Phase 8.

Một bản ``stable`` chỉ có ý nghĩa khi nó trỏ tới báo cáo Phase 7 đã pass, source
được fingerprint và profile legacy đã khóa. Không có manifest, chữ stable chỉ là
một cảm xúc tích cực được đặt cạnh file zip.
"""
from __future__ import annotations
from core.di import resolve_dependency

import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.hardening_config import HardeningConfig


REQUIRED_STABLE_FEATURES = (
    "director_semantic_gate",
    "action_confirmation",
    "tts_language_pin",
    "tts_breaker",
    "memory_provenance_guard",
    "stage_final_sanitizer",
    "token_budget_all_lanes",
    "repair_state_ttl",
    "semantic_repeat_guard",
)

DEFAULT_AUTHORITY_FILES = (
    "runtime/semantic_equivalence_gate.py",
    "runtime/final_surface_sanitizer.py",
    "runtime/public_surface_commit.py",
    "runtime/speech_contract.py",
    "runtime/speech_localizer.py",
    "runtime/speech_persona_polish.py",
    "runtime/tts_engine.py",
    "runtime/runtime_settings_store.py",
    "runtime/ai_orchestrated/orchestrator.py",
    "runtime/ai_orchestrated/model_router.py",
    "runtime/ai_orchestrated/ambient_screen_context.py",
    "runtime/ai_orchestrated/activity_session.py",
    "runtime/ai_orchestrated/discourse_state.py",
    "runtime/ai_orchestrated/autonomous_trace.py",
    "runtime/ai_orchestrated/initiative.py",
    "runtime/ai_orchestrated/self_context.py",
    "core/cognition/contracts.py",
    "runtime/cognition/engine.py",
    "runtime/cognition/acceptance.py",
    "runtime/ai_orchestrated/startup_grounding.py",
    "screen/screen_anaphora_rescue.py",
    "screen/screen_relevance_router.py",
    "screen/ocr_worker.py",
    "runtime/tts_circuit_breaker.py",
    "runtime/speech_provider_chain.py",
    "runtime/provider_interfaces.py",
    "runtime/provider_adapters.py",
    "runtime/phase7_validation.py",
    "runtime/hardening_release.py",
    "actions/contracts.py",
    "actions/executor.py",
    "memory/provenance_policy.py",
    "settings/runtime_settings.json",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str | Path) -> str:
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return _sha256_bytes(raw.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class HardeningReleaseManifest:
    schema_version: int
    release_id: str
    created_at: float
    status: str
    validation_report: str
    validation_sha256: str
    validation_id: str
    validation_public_turns: int
    source_root: str
    source_files: Mapping[str, str]
    settings_sha256: str
    required_features: tuple[str, ...]
    disabled_legacy_paths: tuple[str, ...]
    python_version: str
    platform: str
    rollout_state_path: str = ""
    rollout_state_sha256: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_files"] = dict(self.source_files)
        payload["required_features"] = list(self.required_features)
        payload["disabled_legacy_paths"] = list(self.disabled_legacy_paths)
        return payload

    @classmethod
    def load(cls, path: str | Path) -> "HardeningReleaseManifest":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Release manifest phải là JSON object")
        return cls(
            schema_version=int(payload.get("schema_version", 1) or 1),
            release_id=str(payload.get("release_id", "") or ""),
            created_at=float(payload.get("created_at", 0.0) or 0.0),
            status=str(payload.get("status", "") or ""),
            validation_report=str(payload.get("validation_report", "") or ""),
            validation_sha256=str(payload.get("validation_sha256", "") or ""),
            validation_id=str(payload.get("validation_id", "") or ""),
            validation_public_turns=int(payload.get("validation_public_turns", 0) or 0),
            source_root=str(payload.get("source_root", "") or ""),
            source_files=dict(payload.get("source_files", {}) or {}),
            settings_sha256=str(payload.get("settings_sha256", "") or ""),
            required_features=tuple(str(item) for item in payload.get("required_features", ()) or ()),
            disabled_legacy_paths=tuple(str(item) for item in payload.get("disabled_legacy_paths", ()) or ()),
            python_version=str(payload.get("python_version", "") or ""),
            platform=str(payload.get("platform", "") or ""),
            rollout_state_path=str(payload.get("rollout_state_path", "") or ""),
            rollout_state_sha256=str(payload.get("rollout_state_sha256", "") or ""),
            notes=str(payload.get("notes", "") or ""),
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target


@dataclass(frozen=True, slots=True)
class ReleaseGuardFinding:
    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReleaseGuardResult:
    ready: bool
    release_id: str
    status: str
    findings: tuple[ReleaseGuardFinding, ...]
    manifest_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "release_id": self.release_id,
            "status": self.status,
            "findings": [asdict(item) for item in self.findings],
            "manifest_path": self.manifest_path,
        }


class HardeningReleaseBuilder:
    def __init__(self, source_root: str | Path) -> None:
        self.source_root = Path(source_root).resolve()

    def build(
        self,
        *,
        release_id: str,
        validation_report: str | Path,
        authority_files: Iterable[str] = DEFAULT_AUTHORITY_FILES,
        notes: str = "",
    ) -> HardeningReleaseManifest:
        validation_path = Path(validation_report).resolve()
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        if not isinstance(validation, Mapping):
            raise ValueError("Báo cáo Phase 7 phải là JSON object")
        if str(validation.get("status", "")) != "pass":
            raise ValueError("Không thể đóng stable: báo cáo Phase 7 chưa pass")
        public_turns = int(validation.get("public_turns", 0) or 0)
        if public_turns < 20:
            raise ValueError("Không thể đóng stable: Phase 7 chưa đủ 20 public turn")
        coverage = validation.get("scenario_coverage", {})
        if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
            raise ValueError("Không thể đóng stable: scenario coverage chưa đủ")
        results = coverage.get("results", {})
        try:
            from runtime.phase7_validation import Phase7ValidationManager

            required_scenarios = tuple(Phase7ValidationManager.REQUIRED_SCENARIOS)
        except Exception:
            required_scenarios = ()
        if not isinstance(results, Mapping):
            raise ValueError("Không thể đóng stable: scenario results không hợp lệ")
        incomplete = []
        for name in required_scenarios:
            item = results.get(name, {})
            if not isinstance(item, Mapping):
                incomplete.append(name)
                continue
            if int(item.get("pass", 0) or 0) <= 0 or int(item.get("fail", 0) or 0) > 0:
                incomplete.append(name)
        if incomplete:
            raise ValueError("Không thể đóng stable: scenario chưa pass: " + ", ".join(incomplete))
        failed_findings = [
            str(item.get("name", "unknown"))
            for item in validation.get("findings", ())
            if isinstance(item, Mapping) and str(item.get("status", "")) == "fail"
        ]
        if failed_findings:
            raise ValueError("Không thể đóng stable: finding fail: " + ", ".join(failed_findings))
        events_raw = str(validation.get("events_path", "") or "").strip()
        events_path = Path(events_raw) if events_raw else Path()
        if events_raw and not events_path.is_absolute():
            events_path = (validation_path.parent / events_path).resolve()
        if not events_raw or not events_path.is_file():
            raise ValueError("Không thể đóng stable: thiếu evidence events của Phase 7")

        hashes: dict[str, str] = {}
        missing: list[str] = []
        for relative in authority_files:
            rel = str(relative).replace("\\", "/")
            path = self.source_root / rel
            if not path.is_file():
                missing.append(rel)
                continue
            hashes[rel] = file_sha256(path)
        if missing:
            raise FileNotFoundError("Thiếu authority files: " + ", ".join(missing))

        settings_path = self.source_root / "settings/runtime_settings.json"
        settings_hash = file_sha256(settings_path)
        rollout_path = self.source_root / "logs/rollout_state.json"
        rollout_path_text = str(rollout_path) if rollout_path.is_file() else ""
        rollout_hash = file_sha256(rollout_path) if rollout_path.is_file() else ""
        return HardeningReleaseManifest(
            schema_version=1,
            release_id=str(release_id or "").strip() or f"eris-hardening-{time.strftime('%Y%m%d-%H%M%S')}",
            created_at=time.time(),
            status="stable",
            validation_report=str(validation_path),
            validation_sha256=file_sha256(validation_path),
            validation_id=str(validation.get("validation_id", "") or ""),
            validation_public_turns=public_turns,
            source_root=str(self.source_root),
            source_files=hashes,
            settings_sha256=settings_hash,
            required_features=REQUIRED_STABLE_FEATURES,
            disabled_legacy_paths=(
                "tts_cross_language_fallback",
                "tts_romaji_localizer",
                "unconfirmed_action_success",
                "public_stage_parenthetical",
                "memory_assert_without_provenance",
            ),
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            rollout_state_path=rollout_path_text,
            rollout_state_sha256=rollout_hash,
            notes=str(notes or "").strip(),
        )


class HardeningReleaseGuard:
    def __init__(
        self,
        *,
        source_root: str | Path,
        manifest_path: str | Path | None = None,
        config: HardeningConfig | None = None,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.config = resolve_dependency(config, HardeningConfig.load)
        raw = manifest_path or self.config.release_manifest_path
        path = Path(raw)
        self.manifest_path = path if path.is_absolute() else self.source_root / path

    def verify(self) -> ReleaseGuardResult:
        if not self.config.release_guard_enabled:
            return ReleaseGuardResult(True, "", "disabled", (), str(self.manifest_path))
        findings: list[ReleaseGuardFinding] = []
        if not self.manifest_path.is_file():
            status = "fail" if self.config.stable or self.config.release_guard_fail_closed else "warn"
            findings.append(ReleaseGuardFinding("manifest_exists", status, str(self.manifest_path)))
            return self._result(findings, release_id="")
        try:
            manifest = HardeningReleaseManifest.load(self.manifest_path)
        except Exception as exc:
            findings.append(ReleaseGuardFinding("manifest_parse", "fail", type(exc).__name__))
            return self._result(findings, release_id="")

        findings.append(ReleaseGuardFinding(
            "manifest_status", "pass" if manifest.status == "stable" else "fail", manifest.status
        ))
        validation = Path(manifest.validation_report)
        validation_ok = validation.is_file() and file_sha256(validation) == manifest.validation_sha256
        findings.append(ReleaseGuardFinding(
            "validation_report_hash", "pass" if validation_ok else "fail", str(validation)
        ))
        if validation_ok:
            try:
                payload = json.loads(validation.read_text(encoding="utf-8"))
                validation_status = str(payload.get("status", ""))
                turns = int(payload.get("public_turns", 0) or 0)
                okay = validation_status == "pass" and turns >= 20
                findings.append(ReleaseGuardFinding(
                    "validation_contract", "pass" if okay else "fail", f"status={validation_status}; turns={turns}"
                ))
            except Exception as exc:
                findings.append(ReleaseGuardFinding("validation_contract", "fail", type(exc).__name__))

        for rel, expected in manifest.source_files.items():
            path = self.source_root / rel
            actual = file_sha256(path) if path.is_file() else "missing"
            findings.append(ReleaseGuardFinding(
                f"source_hash:{rel}", "pass" if actual == expected else "fail", actual
            ))

        settings_path = self.source_root / "settings/runtime_settings.json"
        actual_settings = file_sha256(settings_path) if settings_path.is_file() else "missing"
        findings.append(ReleaseGuardFinding(
            "settings_hash", "pass" if actual_settings == manifest.settings_sha256 else "fail", actual_settings
        ))
        findings.extend(self._rollout_findings(manifest))
        findings.extend(self._config_findings())
        return self._result(findings, release_id=manifest.release_id)

    def _rollout_findings(self, manifest: HardeningReleaseManifest) -> list[ReleaseGuardFinding]:
        if not manifest.rollout_state_path:
            status = "fail" if self.config.stable else "warn"
            return [ReleaseGuardFinding("rollout_state_manifest", status, "missing")]
        rollout_path = Path(manifest.rollout_state_path)
        actual = file_sha256(rollout_path) if rollout_path.is_file() else "missing"
        findings = [ReleaseGuardFinding(
            "rollout_state_hash",
            "pass" if actual == manifest.rollout_state_sha256 else "fail",
            actual,
        )]
        if actual != manifest.rollout_state_sha256:
            return findings
        try:
            payload = json.loads(rollout_path.read_text(encoding="utf-8"))
            overrides = payload.get("overrides", {}) if isinstance(payload, Mapping) else {}
            manual_kills = payload.get("manual_kills", {}) if isinstance(payload, Mapping) else {}
        except Exception as exc:
            findings.append(ReleaseGuardFinding("rollout_state_parse", "fail", type(exc).__name__))
            return findings
        for feature in manifest.required_features:
            item = overrides.get(feature, {}) if isinstance(overrides, Mapping) else {}
            mode = str(item.get("mode", "") or "") if isinstance(item, Mapping) else ""
            percent = float(item.get("canary_percent", 0.0) or 0.0) if isinstance(item, Mapping) else 0.0
            killed = bool(isinstance(manual_kills, Mapping) and manual_kills.get(feature))
            okay = mode == "enabled" and percent >= 100.0 and not killed
            findings.append(ReleaseGuardFinding(
                f"rollout_feature:{feature}",
                "pass" if okay else "fail",
                f"mode={mode or 'missing'}; percent={percent:.1f}; killed={str(killed).lower()}",
            ))
        return findings

    def _config_findings(self) -> list[ReleaseGuardFinding]:
        return [
            ReleaseGuardFinding(
                "stable_profile",
                "pass" if self.config.profile == "stable" else ("warn" if self.config.profile == "canary" else "fail"),
                self.config.profile,
            ),
            ReleaseGuardFinding(
                "legacy_cross_language_tts_disabled",
                "pass" if not self.config.legacy_cross_language_tts else "fail",
                str(self.config.legacy_cross_language_tts).lower(),
            ),
            ReleaseGuardFinding(
                "legacy_romaji_tts_disabled",
                "pass" if not self.config.legacy_romaji_tts else "fail",
                str(self.config.legacy_romaji_tts).lower(),
            ),
            ReleaseGuardFinding(
                "provider_registry_strict",
                "pass" if self.config.provider_registry_strict else "fail",
                str(self.config.provider_registry_strict).lower(),
            ),
        ]

    def _result(self, findings: list[ReleaseGuardFinding], *, release_id: str) -> ReleaseGuardResult:
        has_fail = any(item.status == "fail" for item in findings)
        has_warn = any(item.status == "warn" for item in findings)
        status = "fail" if has_fail else "warn" if has_warn else "pass"
        ready = not has_fail
        if self.config.release_guard_fail_closed and status != "pass":
            ready = False
        return ReleaseGuardResult(ready, release_id, status, tuple(findings), str(self.manifest_path))


def runtime_source_root() -> Path:
    raw = str(os.getenv("ERIS_PROJECT_ROOT", "") or "").strip()
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parents[1]


__all__ = [
    "DEFAULT_AUTHORITY_FILES",
    "HardeningReleaseBuilder",
    "HardeningReleaseGuard",
    "HardeningReleaseManifest",
    "REQUIRED_STABLE_FEATURES",
    "ReleaseGuardFinding",
    "ReleaseGuardResult",
    "canonical_json_hash",
    "file_sha256",
    "runtime_source_root",
]
