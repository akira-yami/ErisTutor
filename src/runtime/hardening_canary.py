# -*- coding: utf-8 -*-
"""Vòng đời canary hardening chạy ngay trong runtime.

Verifier offline của Phase 5 đã biết chấm điểm, nhưng chưa biết một canary bắt đầu
ở đâu, kết thúc lúc nào và metric nào thuộc đúng cửa sổ đó. Module này tạo window
20–30 turn, tự đóng khi đủ mẫu, lưu evidence đã redaction và có thể kích rollback.
"""
from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.hardening_verifier import HardeningVerificationReport, HardeningVerifier
from runtime.observability import ErisObservability


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


@dataclass(frozen=True, slots=True)
class CanaryConfig:
    enabled: bool = False
    target_turns: int = 24
    auto_rollback: bool = False
    evidence_dir: str = "logs/hardening_canary"
    release_id: str = ""

    @classmethod
    def from_env(cls) -> "CanaryConfig":
        return cls(
            enabled=_env_bool("ERIS_HARDENING_CANARY_ENABLED", False),
            target_turns=_env_int("ERIS_HARDENING_CANARY_TURNS", 24, minimum=20, maximum=30),
            auto_rollback=_env_bool("ERIS_HARDENING_CANARY_AUTO_ROLLBACK", False),
            evidence_dir=str(os.getenv("ERIS_HARDENING_CANARY_DIR", "logs/hardening_canary") or "logs/hardening_canary"),
            release_id=str(os.getenv("ERIS_RELEASE_ID", "") or "").strip(),
        )


@dataclass(slots=True)
class CanaryState:
    session_id: str
    release_id: str
    started_at: float
    target_turns: int
    public_turns: int = 0
    status: str = "running"
    completed_at: float = 0.0
    evidence_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CanaryCompletion:
    state: Mapping[str, Any]
    report: Mapping[str, Any]
    rollback_applied: bool
    evidence_path: str
    events_path: str


class HardeningCanaryManager:
    def __init__(
        self,
        *,
        observability: ErisObservability,
        rollout_controller: Any,
        provider_health_fn: Callable[[], Mapping[str, Any]] | None = None,
        config: CanaryConfig | None = None,
        verifier: HardeningVerifier | None = None,
    ) -> None:
        self.observability = observability
        self.rollout_controller = rollout_controller
        self.provider_health_fn = provider_health_fn or (lambda: {})
        self.config = resolve_dependency(config, CanaryConfig.from_env)
        self.verifier = resolve_dependency(verifier, lambda: HardeningVerifier())
        self.state: CanaryState | None = None
        self.last_completion: CanaryCompletion | None = None

    @property
    def active(self) -> bool:
        return bool(self.state is not None and self.state.status == "running")

    def start(self, *, session_id: str = "") -> CanaryState | None:
        if not self.config.enabled:
            return None
        if self.active:
            return self.state
        actual_id = str(session_id or f"canary-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
        self.state = CanaryState(
            session_id=actual_id,
            release_id=self.config.release_id,
            started_at=time.time(),
            target_turns=self.config.target_turns,
        )
        self.observability.log(
            "hardening_canary_started",
            tags={"session": actual_id, "release": self.config.release_id or "unknown"},
            fields={"target_turns": self.config.target_turns, "auto_rollback": self.config.auto_rollback},
        )
        self._persist_state()
        return self.state

    def record_public_turn(self, *, turn_id: str = "", channel: str = "chat") -> CanaryCompletion | None:
        if not self.active:
            return None
        assert self.state is not None
        self.state.public_turns += 1
        self.observability.increment(
            "hardening_canary_turn_total",
            tags={"session": self.state.session_id, "channel": channel},
            fields={"turn_id": str(turn_id or ""), "index": self.state.public_turns},
        )
        self._persist_state()
        if self.state.public_turns >= self.state.target_turns:
            return self.finalize(reason="target_reached")
        return None

    def finalize(self, *, reason: str = "manual") -> CanaryCompletion | None:
        if not self.active:
            return self.last_completion
        assert self.state is not None
        state = self.state
        provider_health = dict(self.provider_health_fn() or {})
        rollout_status = self._rollout_status()
        snapshot = self.observability.metric_snapshot_since(state.started_at)
        report = self.verifier.verify(
            snapshot,
            provider_health=provider_health,
            rollout_status=rollout_status,
        )
        rollback_applied = False
        if report.status == "fail" and self.config.auto_rollback:
            rollback_applied = self._apply_rollback_window(state.started_at)
        state.status = report.status
        state.completed_at = time.time()

        evidence_dir = Path(self.config.evidence_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        events_path = evidence_dir / f"{state.session_id}.events.jsonl"
        self.observability.export_window(events_path, since=state.started_at)
        evidence_path = evidence_dir / f"{state.session_id}.verification.json"
        payload = {
            "schema_version": 1,
            "reason": reason,
            "state": state.to_dict(),
            "verification": report.to_dict(),
            "rollback_applied": rollback_applied,
            "provider_health": provider_health,
            "rollout_status": rollout_status,
            "events_path": str(events_path),
        }
        self._atomic_json(evidence_path, payload)
        state.evidence_path = str(evidence_path)
        self._persist_state()
        self.observability.log(
            "hardening_canary_completed",
            tags={"session": state.session_id, "status": report.status},
            fields={
                "public_turns": state.public_turns,
                "rollback_applied": rollback_applied,
                "evidence_path": str(evidence_path),
                "reason": reason,
            },
            status="error" if report.status == "fail" else "ok",
        )
        completion = CanaryCompletion(
            state=state.to_dict(),
            report=report.to_dict(),
            rollback_applied=rollback_applied,
            evidence_path=str(evidence_path),
            events_path=str(events_path),
        )
        self.last_completion = completion
        return completion

    def status(self) -> dict[str, Any]:
        return self.state.to_dict() if self.state is not None else {
            "status": "disabled" if not self.config.enabled else "not_started",
            "target_turns": self.config.target_turns,
        }

    def _apply_rollback_window(self, started_at: float) -> bool:
        try:
            from runtime.hardening_rollout import HardeningHealthGate

            window_store = ErisObservability(enabled=True)
            window_store.write_jsonl = False
            for event in self.observability.events_since(started_at):
                window_store.emit(
                    event.signal,
                    event.name,
                    value=event.value,
                    tags=event.tags,
                    fields=event.fields,
                    trace_id=event.trace_id,
                    span_id=event.span_id,
                    parent_span_id=event.parent_span_id,
                    duration_ms=event.duration_ms,
                    status=event.status,
                    timestamp=event.timestamp,
                )
            findings = HardeningHealthGate(
                self.rollout_controller, baseline=getattr(self.verifier, "baseline", None)
            ).evaluate_observability(window_store, auto_kill=True)
            return any(bool(getattr(item, "triggered", False)) for item in findings)
        except Exception:
            return False

    def _rollout_status(self) -> dict[str, Any]:
        status = getattr(self.rollout_controller, "status", None)
        if not callable(status):
            return {}
        try:
            return dict(status() or {})
        except Exception:
            return {}

    def _persist_state(self) -> None:
        if self.state is None:
            return
        path = Path(self.config.evidence_dir) / "current.json"
        self._atomic_json(path, self.state.to_dict())

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


__all__ = [
    "CanaryCompletion",
    "CanaryConfig",
    "CanaryState",
    "HardeningCanaryManager",
]
