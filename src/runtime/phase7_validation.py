# -*- coding: utf-8 -*-
"""Production validation cho Eris hardening Phase 7.

Phase 6 đã có canary chung, nhưng production validation cần trả lời những câu cụ
thể hơn: Body có thật sự nhận panel không, lời chào có còn bị evidence gate đập
nhầm không, provider thật có 429/shape lỗi, và fallback TTS có giữ locale không.
Module này dùng event thật của runtime; không giả request mạng và không biến mock
thành giấy chứng nhận production.
"""
from __future__ import annotations
from core.di import resolve_dependency

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.hardening_config import HardeningConfig
from runtime.hardening_verifier import HardeningVerifier
from runtime.observability import ErisObservability


def _bare(name: str) -> str:
    return str(name or "").split("{", 1)[0]


def _series(snapshot: Mapping[str, Any], metric: str) -> list[Mapping[str, Any]]:
    return [value for key, value in snapshot.items() if _bare(key) == metric and isinstance(value, Mapping)]


def _sum(snapshot: Mapping[str, Any], metric: str) -> float:
    return sum(float(item.get("sum", item.get("gauge", 0.0)) or 0.0) for item in _series(snapshot, metric))


def _count(snapshot: Mapping[str, Any], metric: str) -> float:
    return sum(float(item.get("count", 0.0) or 0.0) for item in _series(snapshot, metric))


def _p95(snapshot: Mapping[str, Any], metric: str) -> float:
    return max((float(item.get("p95", 0.0) or 0.0) for item in _series(snapshot, metric)), default=0.0)


def _ratio(snapshot: Mapping[str, Any], numerator: str, denominator: str, *, empty: float = 0.0) -> tuple[float, int, int]:
    numer = int(_sum(snapshot, numerator))
    denom = int(_sum(snapshot, denominator))
    if denom <= 0:
        denom = int(_count(snapshot, denominator))
    return (numer / denom if denom > 0 else empty, numer, denom)


@dataclass(frozen=True, slots=True)
class Phase7Finding:
    name: str
    status: str
    observed: float | int | str
    threshold: float | int | str
    samples: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Phase7Report:
    schema_version: int
    validation_id: str
    release_id: str
    generated_at: float
    started_at: float
    completed_at: float
    status: str
    public_turns: int
    findings: tuple[Phase7Finding, ...]
    hardening_verification: Mapping[str, Any]
    provider_health: Mapping[str, Any]
    scenario_coverage: Mapping[str, Any]
    environment: Mapping[str, Any]
    events_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "validation_id": self.validation_id,
            "release_id": self.release_id,
            "generated_at": self.generated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "public_turns": self.public_turns,
            "findings": [asdict(item) for item in self.findings],
            "hardening_verification": dict(self.hardening_verification),
            "provider_health": dict(self.provider_health),
            "scenario_coverage": dict(self.scenario_coverage),
            "environment": dict(self.environment),
            "events_path": self.events_path,
        }


@dataclass(slots=True)
class Phase7State:
    validation_id: str
    release_id: str
    started_at: float
    target_turns: int
    public_turns: int = 0
    status: str = "running"
    completed_at: float = 0.0
    report_path: str = ""
    events_path: str = ""
    scenario_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Phase7ValidationManager:
    """Quản lý một cửa sổ validation thật 20–30 turn."""

    REQUIRED_SCENARIOS = (
        "greeting",
        "panel_delivery",
        "normal_chat",
        "repair_after_correction",
        "action_confirmation",
        "memory_provenance",
        "tts_same_language",
        "provider_recovery",
        "surface_sanitizer",
        "personality_banter",
    )

    def __init__(
        self,
        *,
        observability: ErisObservability,
        provider_health_fn: Callable[[], Mapping[str, Any]] | None = None,
        rollout_status_fn: Callable[[], Mapping[str, Any]] | None = None,
        config: HardeningConfig | None = None,
        release_id: str = "",
        environment_fn: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.observability = observability
        self.provider_health_fn = provider_health_fn or (lambda: {})
        self.rollout_status_fn = rollout_status_fn or (lambda: {})
        self.config = resolve_dependency(config, HardeningConfig.load)
        self.release_id = str(release_id or "").strip()
        self.environment_fn = environment_fn or (lambda: {})
        self.state: Phase7State | None = None
        self.last_report: Phase7Report | None = None

    @property
    def active(self) -> bool:
        return bool(self.state is not None and self.state.status == "running")

    def start(self, *, validation_id: str = "") -> Phase7State | None:
        if not self.config.production_validation_enabled:
            return None
        if self.active:
            return self.state
        actual_id = str(validation_id or f"phase7-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
        self.state = Phase7State(
            validation_id=actual_id,
            release_id=self.release_id,
            started_at=time.time(),
            target_turns=self.config.production_validation_turns,
        )
        self.observability.log(
            "phase7_validation_started",
            tags={"validation": actual_id, "release": self.release_id or "unversioned"},
            fields={"target_turns": self.state.target_turns, "required_scenarios": self.REQUIRED_SCENARIOS},
        )
        self._persist_state()
        return self.state

    def mark_scenario(
        self,
        name: str,
        *,
        passed: bool,
        turn_id: str = "",
        detail: str = "",
        fields: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.active or self.state is None:
            return
        scenario = str(name or "").strip().lower()
        if not scenario:
            return
        current = self.state.scenario_results.setdefault(scenario, {"pass": 0, "fail": 0, "last_detail": "", "turn_ids": []})
        key = "pass" if passed else "fail"
        current[key] = int(current.get(key, 0) or 0) + 1
        current["last_detail"] = str(detail or "")[:300]
        ids = list(current.get("turn_ids", []) or [])
        if turn_id and turn_id not in ids:
            ids.append(str(turn_id)[:80])
        current["turn_ids"] = ids[-12:]
        self.observability.increment(
            "phase7_scenario_total",
            tags={"scenario": scenario, "result": key},
            fields={"turn_id": turn_id, "detail": detail, **dict(fields or {})},
        )
        self._persist_state()

    def record_public_turn(self, *, turn_id: str = "", channel: str = "chat") -> Phase7Report | None:
        if not self.active or self.state is None:
            return None
        self.state.public_turns += 1
        self.observability.increment(
            "phase7_public_turn_total",
            tags={"validation": self.state.validation_id, "channel": channel},
            fields={"turn_id": str(turn_id or ""), "index": self.state.public_turns},
        )
        self._persist_state()
        if self.state.public_turns >= self.state.target_turns:
            return self.finalize(reason="target_reached")
        return None

    def finalize(self, *, reason: str = "manual") -> Phase7Report | None:
        if not self.active:
            return self.last_report
        assert self.state is not None
        state = self.state
        snapshot = self.observability.metric_snapshot_since(state.started_at)
        provider_health = dict(self.provider_health_fn() or {})
        rollout_status = dict(self.rollout_status_fn() or {})
        hardening = HardeningVerifier().verify(
            snapshot,
            provider_health=provider_health,
            rollout_status=rollout_status,
        )
        self._auto_mark_scenarios(snapshot, provider_health)
        findings = self._build_findings(snapshot)
        status = "fail" if hardening.status == "fail" or any(item.status == "fail" for item in findings) else (
            "warn" if hardening.status == "warn" or any(item.status == "warn" for item in findings) else "pass"
        )
        completed_at = time.time()
        evidence_dir = Path(self.config.production_validation_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        events_path = evidence_dir / f"{state.validation_id}.events.jsonl"
        self.observability.export_window(events_path, since=state.started_at)
        coverage = self._scenario_coverage()
        environment = dict(self.environment_fn() or {})
        report = Phase7Report(
            schema_version=1,
            validation_id=state.validation_id,
            release_id=state.release_id,
            generated_at=completed_at,
            started_at=state.started_at,
            completed_at=completed_at,
            status=status,
            public_turns=state.public_turns,
            findings=tuple(findings),
            hardening_verification=hardening.to_dict(),
            provider_health=provider_health,
            scenario_coverage=coverage,
            environment=environment,
            events_path=str(events_path),
        )
        report_path = evidence_dir / f"{state.validation_id}.report.json"
        self._atomic_json(report_path, report.to_dict())
        self._atomic_json(evidence_dir / "latest.json", report.to_dict())
        state.status = status
        state.completed_at = completed_at
        state.report_path = str(report_path)
        state.events_path = str(events_path)
        self._persist_state()
        self.observability.log(
            "phase7_validation_completed",
            tags={"validation": state.validation_id, "status": status},
            fields={
                "reason": reason,
                "public_turns": state.public_turns,
                "report_path": str(report_path),
                "events_path": str(events_path),
                "report_sha256": self.file_sha256(report_path),
            },
            status="error" if status == "fail" else "ok",
        )
        self.last_report = report
        return report

    def status(self) -> dict[str, Any]:
        if self.state is not None:
            return self.state.to_dict()
        return {
            "status": "disabled" if not self.config.production_validation_enabled else "not_started",
            "target_turns": self.config.production_validation_turns,
        }

    def _build_findings(self, snapshot: Mapping[str, Any]) -> list[Phase7Finding]:
        findings: list[Phase7Finding] = []
        public_turns = int(_sum(snapshot, "phase7_public_turn_total") or _sum(snapshot, "hardening_public_turn_total"))
        findings.append(Phase7Finding(
            "phase7_public_turns",
            "pass" if public_turns >= self.config.production_validation_turns else "warn",
            public_turns,
            f">={self.config.production_validation_turns}",
            public_turns,
            "Chỉ cửa sổ đủ 20–30 turn mới được dùng để đóng release stable.",
        ))

        greeting_total = int(_sum(snapshot, "greeting_validation_total"))
        greeting_misfires = int(_sum(snapshot, "greeting_grounding_misfire_total"))
        findings.append(Phase7Finding(
            "greeting_grounding_misfire",
            "pass" if greeting_total > 0 and greeting_misfires == 0 else ("fail" if greeting_misfires > 0 else "warn"),
            greeting_misfires,
            0,
            greeting_total,
            "Phải thử ít nhất một lời chào trực tiếp; không được rơi về câu thiếu căn cứ.",
        ))

        panel_ratio, panel_ack, panel_sent = _ratio(
            snapshot, "panel_state_ack_complete_total", "panel_state_sent_total", empty=1.0
        )
        panel_threshold = 0.90
        if not self.config.panel_ack_required:
            panel_status = "pass"
        elif panel_sent <= 0:
            panel_status = "warn"
        else:
            panel_status = "pass" if panel_ratio >= panel_threshold else "fail"
        findings.append(Phase7Finding(
            "panel_delivery_ack_rate",
            panel_status,
            round(panel_ratio, 6),
            f">={panel_threshold:.2f}",
            panel_sent,
            f"complete_ack={panel_ack}; sent={panel_sent}; body phải xác nhận private_mind/emotion/depth.",
        ))

        panel_p95 = _p95(snapshot, "panel_state_ack_latency_ms")
        findings.append(Phase7Finding(
            "panel_delivery_p95_ms",
            "pass" if panel_p95 <= 1500.0 or panel_p95 == 0.0 else "warn",
            round(panel_p95, 2),
            "<=1500",
            int(_count(snapshot, "panel_state_ack_latency_ms")),
        ))

        provider_429 = int(_sum(snapshot, "429_per_provider_per_lane"))
        breaker_opens = int(_sum(snapshot, "tts_breaker_open_count"))
        findings.append(Phase7Finding(
            "provider_429_recovery",
            "pass" if provider_429 == 0 or breaker_opens > 0 else "fail",
            f"429={provider_429}; breaker_open={breaker_opens}",
            "429=0 hoặc breaker_open>0",
            provider_429,
            "Nếu provider nóng, breaker phải hấp thụ thay vì key-pool quay vô hạn.",
        ))

        wrong_locale = int(_sum(snapshot, "tts_locale_switch_rate"))
        findings.append(Phase7Finding(
            "tts_locale_stability",
            "pass" if wrong_locale == 0 else "fail",
            wrong_locale,
            0,
            int(_count(snapshot, "tts_locale_switch_rate")),
        ))

        missing = [name for name in self.REQUIRED_SCENARIOS if not self._scenario_passed(name)]
        findings.append(Phase7Finding(
            "scenario_coverage",
            "pass" if not missing else "warn",
            len(self.REQUIRED_SCENARIOS) - len(missing),
            len(self.REQUIRED_SCENARIOS),
            len(self.REQUIRED_SCENARIOS),
            "missing=" + ",".join(missing) if missing else "all_required_scenarios_passed",
        ))
        return findings

    def _scenario_passed(self, name: str) -> bool:
        if self.state is None:
            return False
        item = self.state.scenario_results.get(name, {})
        return int(item.get("pass", 0) or 0) > 0 and int(item.get("fail", 0) or 0) == 0

    def _auto_mark_scenarios(self, snapshot: Mapping[str, Any], provider_health: Mapping[str, Any]) -> None:
        """Suy scenario coverage từ evidence máy đo được.

        Manual mark vẫn hữu ích cho banter/provider recovery, nhưng những thứ đã
        có metric rõ thì không bắt người vận hành click xác nhận lại như một lễ
        nghi hành chính dành cho phần mềm.
        """

        if self.state is None:
            return

        def apply(name: str, passed: bool, detail: str) -> None:
            current = self.state.scenario_results.setdefault(
                name, {"pass": 0, "fail": 0, "last_detail": "", "turn_ids": []}
            )
            if int(current.get("pass", 0) or 0) == 0 and int(current.get("fail", 0) or 0) == 0:
                current["pass" if passed else "fail"] = 1
                current["last_detail"] = detail[:300]

        greeting_total = int(_sum(snapshot, "greeting_validation_total"))
        greeting_bad = int(_sum(snapshot, "greeting_grounding_misfire_total"))
        if greeting_total > 0:
            apply("greeting", greeting_bad == 0, f"samples={greeting_total}; misfires={greeting_bad}")

        panel_sent = int(_sum(snapshot, "panel_state_sent_total"))
        panel_ack = int(_sum(snapshot, "panel_state_ack_complete_total"))
        if panel_sent > 0:
            apply("panel_delivery", panel_ack / panel_sent >= 0.90, f"ack={panel_ack}/{panel_sent}")

        public_turns = int(_sum(snapshot, "phase7_public_turn_total") or _sum(snapshot, "hardening_public_turn_total"))
        if public_turns > 0:
            apply("normal_chat", True, f"public_turns={public_turns}")
            leaks = int(_sum(snapshot, "surface_sensitive_leak_total"))
            apply("surface_sanitizer", leaks == 0, f"sensitive_leaks={leaks}")

        action_total = int(_sum(snapshot, "action_confirmation_total"))
        false_success = int(_sum(snapshot, "false_success_action_rate"))
        if action_total > 0:
            apply("action_confirmation", false_success == 0, f"false_success={false_success}/{action_total}")

        memory_total = int(_sum(snapshot, "memory_provenance_total"))
        memory_bad = int(_sum(snapshot, "memory_assert_without_provenance_rate"))
        if memory_total > 0:
            apply("memory_provenance", memory_bad == 0, f"bad={memory_bad}/{memory_total}")

        repair_total = int(_sum(snapshot, "repair_state_event_total"))
        repair_bad = int(_sum(snapshot, "repair_loop_rate"))
        if repair_total > 0:
            apply("repair_after_correction", repair_bad == 0, f"loop={repair_bad}/{repair_total}")

        tts_samples = int(_count(snapshot, "tts_latency_ms") or _count(snapshot, "tts_end_to_end_latency_ms"))
        locale_bad = int(_sum(snapshot, "tts_locale_switch_rate"))
        if tts_samples > 0:
            apply("tts_same_language", locale_bad == 0, f"locale_switch={locale_bad}; samples={tts_samples}")

        providers = [item for item in provider_health.values() if isinstance(item, Mapping)]
        if providers:
            all_ready = all(bool(item.get("ready", False)) for item in providers)
            provider_429 = int(_sum(snapshot, "429_per_provider_per_lane"))
            breaker_open = int(_sum(snapshot, "tts_breaker_open_count"))
            recovered = all_ready and (provider_429 == 0 or breaker_open > 0)
            apply(
                "provider_recovery",
                recovered,
                f"all_ready={all_ready}; 429={provider_429}; breaker_open={breaker_open}",
            )

    def _scenario_coverage(self) -> dict[str, Any]:
        data = dict(self.state.scenario_results if self.state is not None else {})
        missing = [name for name in self.REQUIRED_SCENARIOS if not self._scenario_passed(name)]
        return {
            "required": list(self.REQUIRED_SCENARIOS),
            "results": data,
            "missing": missing,
            "complete": not missing,
        }

    def _persist_state(self) -> None:
        if self.state is None:
            return
        path = Path(self.config.production_validation_dir) / "current.json"
        self._atomic_json(path, self.state.to_dict())

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def file_sha256(path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


__all__ = [
    "Phase7Finding",
    "Phase7Report",
    "Phase7State",
    "Phase7ValidationManager",
]
