# -*- coding: utf-8 -*-
"""Health gate và auto-rollback cho các hardening feature.

PDF yêu cầu rollback theo metric thực, không theo linh cảm sau khi user đã chịu
đủ lỗi. Gate này đọc snapshot observability, so với baseline/SLA và bật kill
switch có trạng thái qua ``RolloutController``.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Mapping

from runtime.hardening_baseline import HardeningBaseline
from runtime.rollout_control import RolloutController


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _bare(name: str) -> str:
    return str(name or "").split("{", 1)[0]


def _series(snapshot: Mapping[str, Any], metric: str) -> list[Mapping[str, Any]]:
    return [value for key, value in snapshot.items() if _bare(key) == metric and isinstance(value, Mapping)]


def _sum(snapshot: Mapping[str, Any], metric: str) -> float:
    return sum(float(item.get("sum", item.get("gauge", 0.0)) or 0.0) for item in _series(snapshot, metric))


def _count(snapshot: Mapping[str, Any], metric: str) -> float:
    return sum(float(item.get("count", 0.0) or 0.0) for item in _series(snapshot, metric))


def _p95(snapshot: Mapping[str, Any], metric: str) -> float:
    values = [float(item.get("p95", 0.0) or 0.0) for item in _series(snapshot, metric)]
    return max(values, default=0.0)


@dataclass(frozen=True, slots=True)
class RollbackFinding:
    feature: str
    metric: str
    observed: float
    threshold: float
    triggered: bool
    reason: str


class HardeningHealthGate:
    def __init__(self, controller: RolloutController, baseline: HardeningBaseline | None = None) -> None:
        self.controller = controller
        self.min_samples = max(1, int(_env_float("ERIS_HARDENING_ROLLBACK_MIN_SAMPLES", 20)))
        self.baseline = baseline or self._load_baseline_from_env()
        if self.baseline is not None:
            self.false_success_baseline = max(0.0, float(self.baseline.false_success_action_rate))
            self.repair_loop_baseline = max(0.0, float(self.baseline.repair_loop_rate))
        else:
            self.false_success_baseline = max(0.0, _env_float("ERIS_BASELINE_FALSE_SUCCESS_RATE", 0.02))
            self.repair_loop_baseline = max(0.0, _env_float("ERIS_BASELINE_REPAIR_LOOP_RATE", 0.03))
        self.chat_p95_sla_ms = max(1.0, _env_float("ERIS_CHAT_P95_SLA_MS", 8000.0))
        self.tts_p95_sla_ms = max(1.0, _env_float("ERIS_TTS_P95_SLA_MS", 12000.0))
        self.last_evaluated_at = 0.0

    @staticmethod
    def _load_baseline_from_env() -> HardeningBaseline | None:
        path = str(os.getenv("ERIS_HARDENING_BASELINE_FILE", "") or "").strip()
        if not path:
            return None
        try:
            return HardeningBaseline.load(path)
        except Exception:
            return None

    @staticmethod
    def _rate(snapshot: Mapping[str, Any], numerator: str, denominator: str) -> tuple[float, float]:
        denom = _sum(snapshot, denominator)
        if denom <= 0:
            denom = _count(snapshot, denominator)
        numer = _sum(snapshot, numerator)
        return (numer / denom if denom > 0 else 0.0, denom)

    def evaluate(self, snapshot: Mapping[str, Any], *, auto_kill: bool = True) -> list[RollbackFinding]:
        findings: list[RollbackFinding] = []

        false_rate, action_samples = self._rate(snapshot, "false_success_action_rate", "action_confirmation_total")
        false_threshold = self.false_success_baseline * 1.20
        findings.append(RollbackFinding(
            "action_confirmation", "false_success_action_rate", false_rate, false_threshold,
            action_samples >= self.min_samples and false_rate > false_threshold,
            f"rate={false_rate:.4f}, baseline={self.false_success_baseline:.4f}, samples={int(action_samples)}",
        ))

        repair_rate, repair_samples = self._rate(snapshot, "repair_loop_rate", "repair_state_event_total")
        repair_threshold = self.repair_loop_baseline * 1.20
        findings.append(RollbackFinding(
            "repair_state_ttl", "repair_loop_rate", repair_rate, repair_threshold,
            repair_samples >= self.min_samples and repair_rate > repair_threshold,
            f"rate={repair_rate:.4f}, baseline={self.repair_loop_baseline:.4f}, samples={int(repair_samples)}",
        ))

        locale_switches = _sum(snapshot, "tts_locale_switch_rate")
        findings.append(RollbackFinding(
            "tts_language_pin", "tts_locale_switch_rate", locale_switches, 0.0,
            locale_switches > 0.0, f"locale_switches={locale_switches:.0f}",
        ))

        sensitive_leaks = _sum(snapshot, "surface_sensitive_leak_total")
        findings.append(RollbackFinding(
            "stage_final_sanitizer", "surface_sensitive_leak_total", sensitive_leaks, 0.0,
            sensitive_leaks > 0.0, f"sensitive_leaks={sensitive_leaks:.0f}",
        ))

        chat_p95 = max(_p95(snapshot, "end_to_end_latency_ms"), _p95(snapshot, "chat_latency_ms"))
        findings.append(RollbackFinding(
            "director_semantic_gate", "chat_p95_latency_ms", chat_p95, self.chat_p95_sla_ms,
            chat_p95 > self.chat_p95_sla_ms, f"p95={chat_p95:.1f}ms",
        ))

        tts_p95 = max(_p95(snapshot, "tts_latency_ms"), _p95(snapshot, "tts_end_to_end_latency_ms"))
        findings.append(RollbackFinding(
            "tts_breaker", "tts_p95_latency_ms", tts_p95, self.tts_p95_sla_ms,
            tts_p95 > self.tts_p95_sla_ms, f"p95={tts_p95:.1f}ms",
        ))

        if auto_kill:
            for finding in findings:
                if finding.triggered:
                    self.controller.request_kill(
                        finding.feature,
                        reason=f"auto_rollback:{finding.metric}:{finding.reason}",
                    )
        self.last_evaluated_at = time.time()
        return findings

    def evaluate_observability(self, observability: Any, *, auto_kill: bool = True) -> list[RollbackFinding]:
        snapshot = observability.metric_snapshot() if observability is not None else {}
        return self.evaluate(snapshot, auto_kill=auto_kill)


__all__ = ["HardeningHealthGate", "RollbackFinding"]
