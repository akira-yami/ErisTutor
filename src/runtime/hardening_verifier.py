# -*- coding: utf-8 -*-
"""Verify/analyze gate cho rollout hardening Eris.

Khác ``HardeningHealthGate`` chạy trong process và có thể kill-switch ngay,
verifier này dùng cho canary/offline: replay JSONL, tính rate theo đúng mẫu số,
kiểm provider health, rollout state và xuất một báo cáo có thể lưu cùng release.
Nó biến phần "chạy 20–30 turn rồi nhìn log bằng mắt" thành một contract lặp lại
được, vì mắt người không phải hệ thống giám sát dù rất thích giả vờ như vậy.
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from runtime.hardening_baseline import HardeningBaseline


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
    total = 0.0
    for item in _series(snapshot, metric):
        total += float(item.get("sum", item.get("gauge", 0.0)) or 0.0)
    return total


def _count(snapshot: Mapping[str, Any], metric: str) -> float:
    return sum(float(item.get("count", 0.0) or 0.0) for item in _series(snapshot, metric))


def _p95(snapshot: Mapping[str, Any], metric: str) -> float:
    return max((float(item.get("p95", 0.0) or 0.0) for item in _series(snapshot, metric)), default=0.0)


def _rate(snapshot: Mapping[str, Any], numerator: str, denominator: str) -> tuple[float, float, float]:
    numer = _sum(snapshot, numerator)
    denom = _sum(snapshot, denominator)
    if denom <= 0:
        denom = _count(snapshot, denominator)
    return (numer / denom if denom > 0 else 0.0, numer, denom)


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    name: str
    status: str
    observed: float | str
    threshold: float | str
    samples: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HardeningVerificationReport:
    generated_at: float
    status: str
    public_turns: int
    findings: tuple[VerificationFinding, ...]
    provider_health: Mapping[str, Any]
    rollout_status: Mapping[str, Any]
    baseline: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "public_turns": self.public_turns,
            "findings": [asdict(item) for item in self.findings],
            "provider_health": dict(self.provider_health),
            "rollout_status": dict(self.rollout_status),
            "baseline": dict(self.baseline),
        }


class HardeningVerifier:
    """Đánh giá canary theo metric/rollback criteria trong báo cáo."""

    def __init__(self, baseline: HardeningBaseline | None = None) -> None:
        self.min_public_turns = max(1, int(_env_float("ERIS_HARDENING_VERIFY_MIN_TURNS", 20)))
        self.min_action_samples = max(1, int(_env_float("ERIS_HARDENING_VERIFY_MIN_ACTIONS", 5)))
        self.baseline = baseline or self._load_baseline_from_env()
        if self.baseline is not None:
            self.false_success_baseline = max(0.0, float(self.baseline.false_success_action_rate))
            self.repair_loop_baseline = max(0.0, float(self.baseline.repair_loop_rate))
        else:
            self.false_success_baseline = max(0.0, _env_float("ERIS_BASELINE_FALSE_SUCCESS_RATE", 0.02))
            self.repair_loop_baseline = max(0.0, _env_float("ERIS_BASELINE_REPAIR_LOOP_RATE", 0.03))
        self.same_language_fallback_min = min(1.0, max(0.0, _env_float("ERIS_TTS_SAME_LANGUAGE_FALLBACK_MIN", 0.95)))
        self.chat_p95_sla_ms = max(1.0, _env_float("ERIS_CHAT_P95_SLA_MS", 8000.0))
        self.tts_p95_sla_ms = max(1.0, _env_float("ERIS_TTS_P95_SLA_MS", 12000.0))

    @staticmethod
    def _load_baseline_from_env() -> HardeningBaseline | None:
        path = str(os.getenv("ERIS_HARDENING_BASELINE_FILE", "") or "").strip()
        if not path:
            return None
        try:
            return HardeningBaseline.load(path)
        except Exception:
            return None

    def verify(
        self,
        snapshot: Mapping[str, Any],
        *,
        provider_health: Mapping[str, Any] | None = None,
        rollout_status: Mapping[str, Any] | None = None,
    ) -> HardeningVerificationReport:
        findings: list[VerificationFinding] = []
        public_turns = int(_sum(snapshot, "hardening_public_turn_total") or _count(snapshot, "director_turn_total"))
        findings.append(self._minimum_samples_finding(public_turns))
        if self.baseline is not None:
            findings.append(VerificationFinding(
                "baseline_sample",
                "pass" if self.baseline.public_turns >= self.min_public_turns else "warn",
                self.baseline.public_turns,
                f">={self.min_public_turns}",
                self.baseline.public_turns,
                f"source={self.baseline.source or '<unknown>'}",
            ))

        false_rate, false_count, action_samples = _rate(
            snapshot, "false_success_action_rate", "action_confirmation_total"
        )
        false_threshold = self.false_success_baseline * 1.20
        findings.append(self._rate_finding(
            "false_success_action_rate", false_rate, false_threshold, int(action_samples),
            min_samples=self.min_action_samples,
            detail=f"false_success={int(false_count)}",
        ))

        memory_rate, memory_bad, memory_samples = _rate(
            snapshot, "memory_assert_without_provenance_rate", "memory_provenance_total"
        )
        findings.append(self._rate_finding(
            "memory_assert_without_provenance_rate", memory_rate, 0.0, int(memory_samples),
            min_samples=1, detail=f"assert_without_provenance={int(memory_bad)}",
        ))

        repair_rate, repair_bad, repair_samples = _rate(
            snapshot, "repair_loop_rate", "repair_state_event_total"
        )
        findings.append(self._rate_finding(
            "repair_loop_rate", repair_rate, self.repair_loop_baseline * 1.20,
            int(repair_samples), min_samples=1, detail=f"exhausted_events={int(repair_bad)}",
        ))

        locale_switches = _sum(snapshot, "tts_locale_switch_rate")
        findings.append(self._zero_finding("tts_locale_switch_rate", locale_switches))

        sensitive_leaks = _sum(snapshot, "surface_sensitive_leak_total")
        findings.append(self._zero_finding("surface_sensitive_leak_total", sensitive_leaks))

        fallback_total = _sum(snapshot, "tts_fallback_total")
        fallback_same = _sum(snapshot, "tts_fallback_same_language_rate")
        fallback_ratio = fallback_same / fallback_total if fallback_total > 0 else 1.0
        findings.append(VerificationFinding(
            "tts_fallback_same_language_rate",
            "pass" if fallback_ratio >= self.same_language_fallback_min else "fail",
            round(fallback_ratio, 6),
            f">={self.same_language_fallback_min:.3f}",
            int(fallback_total),
            "Không có fallback được xem là đạt; khi có fallback, tất cả nên giữ cùng ngôn ngữ.",
        ))

        chat_p95 = max(_p95(snapshot, "end_to_end_latency_ms"), _p95(snapshot, "chat_latency_ms"))
        findings.append(self._latency_finding("chat_p95_latency_ms", chat_p95, self.chat_p95_sla_ms))
        tts_p95 = max(_p95(snapshot, "tts_latency_ms"), _p95(snapshot, "tts_end_to_end_latency_ms"))
        findings.append(self._latency_finding("tts_p95_latency_ms", tts_p95, self.tts_p95_sla_ms))

        normalized_health = self._normalize_health(provider_health or {})
        for kind, item in normalized_health.items():
            ready = bool(item.get("ready", False))
            findings.append(VerificationFinding(
                f"provider_ready:{kind}",
                "pass" if ready else "fail",
                "ready" if ready else "not_ready",
                "ready",
                1,
                str(item.get("reason", "") or ""),
            ))

        status = "fail" if any(item.status == "fail" for item in findings) else (
            "warn" if any(item.status == "warn" for item in findings) else "pass"
        )
        return HardeningVerificationReport(
            generated_at=time.time(),
            status=status,
            public_turns=public_turns,
            findings=tuple(findings),
            provider_health=normalized_health,
            rollout_status=dict(rollout_status or {}),
            baseline=self.baseline.to_dict() if self.baseline is not None else {
                "source": "environment_defaults",
                "false_success_action_rate": self.false_success_baseline,
                "repair_loop_rate": self.repair_loop_baseline,
            },
        )

    def _minimum_samples_finding(self, public_turns: int) -> VerificationFinding:
        if public_turns >= self.min_public_turns:
            status = "pass"
        elif public_turns > 0:
            status = "warn"
        else:
            status = "warn"
        return VerificationFinding(
            "public_turn_sample",
            status,
            public_turns,
            f">={self.min_public_turns}",
            public_turns,
            "Canary chưa đủ 20–30 turn thì kết quả chỉ là tín hiệu sớm, không phải giấy chứng nhận thần thánh.",
        )

    @staticmethod
    def _rate_finding(
        name: str,
        observed: float,
        threshold: float,
        samples: int,
        *,
        min_samples: int,
        detail: str,
    ) -> VerificationFinding:
        if samples < min_samples:
            status = "warn"
            detail = f"insufficient_samples:{samples}<{min_samples}; {detail}"
        else:
            status = "pass" if observed <= threshold else "fail"
        return VerificationFinding(name, status, round(observed, 6), f"<={threshold:.6f}", samples, detail)

    @staticmethod
    def _zero_finding(name: str, observed: float) -> VerificationFinding:
        return VerificationFinding(
            name,
            "pass" if observed <= 0.0 else "fail",
            int(observed) if float(observed).is_integer() else round(observed, 6),
            "=0",
            int(observed),
            "Hard-zero invariant.",
        )

    @staticmethod
    def _latency_finding(name: str, observed: float, threshold: float) -> VerificationFinding:
        # Chưa có latency sample không nên fail canary, nhưng phải hiện warning.
        if observed <= 0.0:
            return VerificationFinding(name, "warn", 0.0, f"<={threshold:.1f}ms", 0, "Chưa có latency sample.")
        return VerificationFinding(
            name,
            "pass" if observed <= threshold else "fail",
            round(observed, 3),
            f"<={threshold:.1f}ms",
            1,
            "",
        )

    @staticmethod
    def _normalize_health(health: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        for kind, item in health.items():
            if isinstance(item, Mapping):
                output[str(kind)] = {
                    "ready": bool(item.get("ready", False)),
                    "provider": str(item.get("provider", "") or ""),
                    "reason": str(item.get("reason", "") or ""),
                    "details": dict(item.get("details", {}) or {}),
                }
            else:
                output[str(kind)] = {
                    "ready": bool(getattr(item, "ready", False)),
                    "provider": str(getattr(item, "provider", "") or ""),
                    "reason": str(getattr(item, "reason", "") or ""),
                    "details": dict(getattr(item, "details", {}) or {}),
                }
        return output


__all__ = [
    "HardeningVerificationReport",
    "HardeningVerifier",
    "VerificationFinding",
]
