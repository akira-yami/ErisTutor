# -*- coding: utf-8 -*-
from __future__ import annotations

"""Replay/acceptance cho Eris Deep Environment.

PDF yêu cầu đo chất lượng environment cognition thay vì chỉ OCR accuracy. Tool
này chạy được trong CI không-Windows: replay NormalizedEnvironmentEvent qua
DesktopSemanticRuntime, đo false wake/sensitive leak và, nếu JSONL có label,
đo thêm structured precision, task accuracy, interrupt precision cùng latency.
Metric nào không có dữ liệu sẽ trả ``None`` thay vì bịa benchmark cho vui.
"""

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Mapping, Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.ai_orchestrated.desktop_semantics import DesktopSemanticRuntime, UserSituation
from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent


MECHANICAL_KINDS = {
    "clock_tick", "counter_update", "progress_update", "scroll_churn",
    "ocr_jitter", "mechanical_value_update", "mechanical_screen_delta",
}

PDF_TARGETS = {
    "event_to_semantic_p95_ms": 250.0,
    "foreground_transition_p95_ms": 200.0,
    "structured_event_precision": 0.95,
    "false_wake_per_min": 0.05,
    "ocr_calls_per_minute_idle": 0.2,
    "ocr_calls_reduction": 0.80,
    "task_top1_accuracy": 0.85,
    "task_switch_false_positive": 0.05,
    "interrupt_precision": 0.90,
    "sensitive_payload_leaks": 0,
    "screen_noise_deliberations": 0,
}


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[index]


@dataclass(slots=True)
class AcceptanceMetrics:
    total_events: int = 0
    mechanical_events: int = 0
    semantic_events: int = 0
    cognition_wakes: int = 0
    mechanical_wakes: int = 0
    interrupt_candidates: int = 0
    sensitive_payload_leaks: int = 0
    ocr_calls: int = 0
    structured_labelled: int = 0
    structured_correct: int = 0
    task_labelled: int = 0
    task_correct: int = 0
    task_switch_labelled: int = 0
    task_switch_false_positive_count: int = 0
    interrupt_labelled: int = 0
    interrupt_true_positive: int = 0
    interrupt_predicted: int = 0
    first_timestamp: float = 0.0
    last_timestamp: float = 0.0
    _event_to_semantic_ms: list[float] = field(default_factory=list, repr=False)
    _foreground_transition_ms: list[float] = field(default_factory=list, repr=False)

    @property
    def duration_minutes(self) -> float:
        if not self.first_timestamp or self.last_timestamp <= self.first_timestamp:
            return 0.0
        return max(1e-6, (self.last_timestamp - self.first_timestamp) / 60.0)

    @property
    def false_wake_rate(self) -> float:
        if self.mechanical_events <= 0:
            return 0.0
        return self.mechanical_wakes / float(self.mechanical_events)

    @property
    def false_wake_per_min(self) -> float:
        if self.duration_minutes <= 0:
            return float(self.mechanical_wakes)
        return self.mechanical_wakes / self.duration_minutes

    @property
    def ocr_calls_per_minute(self) -> float:
        if self.duration_minutes <= 0:
            return float(self.ocr_calls)
        return self.ocr_calls / self.duration_minutes

    @property
    def structured_event_precision(self) -> float | None:
        return self.structured_correct / self.structured_labelled if self.structured_labelled else None

    @property
    def task_top1_accuracy(self) -> float | None:
        return self.task_correct / self.task_labelled if self.task_labelled else None

    @property
    def task_switch_false_positive(self) -> float | None:
        return self.task_switch_false_positive_count / self.task_switch_labelled if self.task_switch_labelled else None

    @property
    def interrupt_precision(self) -> float | None:
        return self.interrupt_true_positive / self.interrupt_predicted if self.interrupt_predicted else None

    def to_dict(self, *, baseline_ocr_calls_per_minute: float | None = None) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("_event_to_semantic_ms", None)
        payload.pop("_foreground_transition_ms", None)
        payload.update({
            "duration_minutes": round(self.duration_minutes, 4),
            "false_wake_rate": round(self.false_wake_rate, 6),
            "false_wake_per_min": round(self.false_wake_per_min, 6),
            "ocr_calls_per_minute": round(self.ocr_calls_per_minute, 6),
            "event_to_semantic_p95_ms": _p95(self._event_to_semantic_ms),
            "foreground_transition_p95_ms": _p95(self._foreground_transition_ms),
            "structured_event_precision": self.structured_event_precision,
            "task_top1_accuracy": self.task_top1_accuracy,
            "task_switch_false_positive": self.task_switch_false_positive,
            "interrupt_precision": self.interrupt_precision,
            "screen_noise_deliberations": self.mechanical_wakes,
            "pass_mechanical_zero_wake": self.mechanical_wakes == 0,
            "pass_sensitive_zero_leak": self.sensitive_payload_leaks == 0,
        })
        if baseline_ocr_calls_per_minute and baseline_ocr_calls_per_minute > 0:
            reduction = 1.0 - (self.ocr_calls_per_minute / baseline_ocr_calls_per_minute)
            payload["ocr_calls_reduction"] = max(-1.0, min(1.0, reduction))
        else:
            payload["ocr_calls_reduction"] = None
        payload["target_evaluation"] = evaluate_targets(payload)
        return payload


def evaluate_targets(metrics: Mapping[str, Any]) -> dict[str, str]:
    """PASS/FAIL/NO_DATA cho target đề xuất trong PDF."""
    result: dict[str, str] = {}
    lower_is_better = {
        "event_to_semantic_p95_ms", "foreground_transition_p95_ms", "false_wake_per_min",
        "ocr_calls_per_minute_idle", "task_switch_false_positive", "sensitive_payload_leaks",
        "screen_noise_deliberations",
    }
    aliases = {"ocr_calls_per_minute_idle": "ocr_calls_per_minute"}
    for target_name, target in PDF_TARGETS.items():
        metric_name = aliases.get(target_name, target_name)
        value = metrics.get(metric_name)
        if value is None:
            result[target_name] = "NO_DATA"
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            result[target_name] = "NO_DATA"
            continue
        if target_name in lower_is_better:
            result[target_name] = "PASS" if number <= float(target) else "FAIL"
        else:
            result[target_name] = "PASS" if number >= float(target) else "FAIL"
    return result


def _contains_sensitive_payload(event: NormalizedEnvironmentEvent) -> bool:
    if str(event.privacy_scope).upper() in {"OFF", "METADATA"} and (event.title or event.value):
        return True
    text = json.dumps(event.metadata, ensure_ascii=False).lower()
    forbidden = ("password_text", "clipboard_text", "raw_keystrokes", "cookie_value", "secret_value")
    return any(token in text for token in forbidden)


def replay(events: Iterable[NormalizedEnvironmentEvent | Mapping[str, Any]]) -> AcceptanceMetrics:
    runtime = DesktopSemanticRuntime()
    metrics = AcceptanceMetrics()
    base = UserSituation(timestamp=time.time())
    previous_task_id = runtime.task_graph.current.task_id

    for raw in events:
        event = raw if isinstance(raw, NormalizedEnvironmentEvent) else NormalizedEnvironmentEvent.from_mapping(raw)
        metrics.total_events += 1
        metrics.first_timestamp = event.timestamp if not metrics.first_timestamp else min(metrics.first_timestamp, event.timestamp)
        metrics.last_timestamp = max(metrics.last_timestamp, event.timestamp)

        mechanical = event.event_type in MECHANICAL_KINDS or event.attention_level == 0 or not event.semantic
        if mechanical:
            metrics.mechanical_events += 1
        else:
            metrics.semantic_events += 1
        if str(event.source or "").lower().startswith("ocr") or str(event.source or "").lower() == "screen_ocr":
            metrics.ocr_calls += 1
        if _contains_sensitive_payload(event):
            metrics.sensitive_payload_leaks += 1

        meta = dict(event.metadata or {})
        for key, target in (("event_to_semantic_ms", metrics._event_to_semantic_ms), ("foreground_transition_ms", metrics._foreground_transition_ms)):
            try:
                if key in meta:
                    target.append(float(meta[key]))
            except (TypeError, ValueError):
                pass

        situation = runtime.observe_events(
            [event],
            base_situation=base,
            telemetry={"active_process": event.process_name, "idle_seconds": float(meta.get("idle_seconds", 1) or 1)},
            now=event.timestamp,
        )
        wake = bool(situation.max_attention_level >= 2 and situation.semantic_delta)
        if wake:
            metrics.cognition_wakes += 1
            if mechanical:
                metrics.mechanical_wakes += 1
        predicted_interrupt = situation.max_attention_level >= 4
        if predicted_interrupt:
            metrics.interrupt_candidates += 1
            metrics.interrupt_predicted += 1

        expected_kind = str(meta.get("expected_event_type", "") or "")
        if expected_kind:
            metrics.structured_labelled += 1
            metrics.structured_correct += int(event.event_type == expected_kind)

        expected_task = str(meta.get("expected_task", "") or "")
        if expected_task:
            metrics.task_labelled += 1
            metrics.task_correct += int(situation.task_hint == expected_task)

        if "expected_same_task" in meta:
            metrics.task_switch_labelled += 1
            current_task_id = runtime.task_graph.current.task_id
            should_same = bool(meta.get("expected_same_task"))
            if should_same and current_task_id != previous_task_id:
                metrics.task_switch_false_positive_count += 1
            previous_task_id = current_task_id

        if "expected_interrupt" in meta:
            metrics.interrupt_labelled += 1
            expected_interrupt = bool(meta.get("expected_interrupt"))
            if predicted_interrupt and expected_interrupt:
                metrics.interrupt_true_positive += 1

        base = situation
    return metrics


def load_jsonl(path: str | Path) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, Mapping):
            items.append(payload)
    return items


def mechanical_smoke_events(*, count: int = 120) -> list[NormalizedEnvironmentEvent]:
    start = time.time()
    events: list[NormalizedEnvironmentEvent] = []
    kinds = ("clock_tick", "counter_update", "progress_update", "scroll_churn")
    for index in range(max(1, int(count))):
        kind = kinds[index % len(kinds)]
        events.append(NormalizedEnvironmentEvent(
            timestamp=start + index * 0.25,
            source="acceptance_fixture",
            event_type=kind,
            process_name="Taskmgr.exe" if kind == "counter_update" else "explorer.exe",
            title=f"value {index}",
            semantic=False,
            attention_level=0,
            confidence=0.98,
            source_confidence=0.98,
            novelty=0.02,
            importance=0.0,
            user_relevance=0.1,
        ))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Eris Deep Environment normalized events")
    parser.add_argument("jsonl", nargs="?", help="Normalized environment event JSONL")
    parser.add_argument("--mechanical-smoke", action="store_true", help="Chạy workload clock/counter/scroll built-in")
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--baseline-ocr-per-minute", type=float, default=None, help="Baseline fixed-poll OCR để tính % giảm")
    args = parser.parse_args()

    if args.mechanical_smoke or not args.jsonl:
        events = mechanical_smoke_events(count=args.count)
    else:
        events = load_jsonl(args.jsonl)
    metrics = replay(events)
    report = metrics.to_dict(baseline_ocr_calls_per_minute=args.baseline_ocr_per_minute)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if metrics.mechanical_wakes == 0 and metrics.sensitive_payload_leaks == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
