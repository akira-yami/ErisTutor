# -*- coding: utf-8 -*-
"""Tóm tắt acceptance V5 từ ``eris_observability*.jsonl``.

Ví dụ:
    py tools/deep_environment_v5_acceptance.py logs/eris_observability.jsonl
    py tools/deep_environment_v5_acceptance.py logs/run.jsonl --last-seconds 30

Script chỉ đọc metric JSONL, không sửa runtime và không gửi dữ liệu ra ngoài.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable


METRICS = (
    "environment_source_callback_delay_ms",
    "environment_source_to_ingress_ms",
    "environment_callback_to_enqueue_ms",
    "environment_bus_publish_duration_ms",
    "environment_semantic_queue_wait_ms",
    "environment_semantic_processing_ms",
    "environment_enrichment_ms",
    "environment_ingress_to_semantic_ms",
    "environment_source_to_semantic_ms",
    "foreground_to_user_situation_ms",
    "environment_semantic_queue_depth",
)

TARGETS_P95_MS = {
    "environment_callback_to_enqueue_ms": 20.0,
    "environment_semantic_queue_wait_ms": 50.0,
    "environment_semantic_processing_ms": 100.0,
    "environment_source_to_semantic_ms": 250.0,
    "foreground_to_user_situation_ms": 200.0,
}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(q * len(ordered)) - 1)))
    return float(ordered[index])


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _window(rows: list[dict], last_seconds: float | None) -> list[dict]:
    if not rows or not last_seconds or last_seconds <= 0:
        return rows
    latest = max(float(row.get("timestamp", 0.0) or 0.0) for row in rows)
    cutoff = latest - float(last_seconds)
    return [row for row in rows if float(row.get("timestamp", 0.0) or 0.0) >= cutoff]


def _summarize(rows: Iterable[dict]) -> dict:
    values: dict[str, list[float]] = defaultdict(list)
    counters: dict[str, float] = defaultdict(float)
    errors = 0
    total = 0
    for row in rows:
        total += 1
        name = str(row.get("name", "") or "")
        signal = str(row.get("signal", "") or "")
        status = str(row.get("status", "ok") or "ok").lower()
        if status == "error":
            errors += 1
        if signal != "metric":
            continue
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if name in METRICS:
            values[name].append(value)
        if name in {
            "environment_false_wake_mechanical_total",
            "environment_scheduler_coalesced_total",
            "environment_scheduler_evicted_total",
            "environment_scheduler_drop_total",
            "environment_semantic_worker_error_total",
        }:
            counters[name] += value

    metrics = {}
    for name in METRICS:
        series = values.get(name, [])
        metrics[name] = {
            "count": len(series),
            "p50": _percentile(series, 0.50),
            "p95": _percentile(series, 0.95),
            "p99": _percentile(series, 0.99),
            "max": max(series) if series else None,
        }
    return {"rows": total, "errors": errors, "metrics": metrics, "counters": dict(counters)}


def _print(summary: dict, *, title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"JSONL rows: {summary['rows']}")
    print(f"status:error: {summary['errors']}")
    for name in METRICS:
        data = summary["metrics"][name]
        p95 = data["p95"]
        target = TARGETS_P95_MS.get(name)
        verdict = ""
        if target is not None and p95 is not None:
            verdict = " PASS" if p95 < target else " FAIL"
            verdict += f" (target < {target:.0f} ms)"
        p95_text = "n/a" if p95 is None else f"{p95:.2f} ms"
        print(f"{name:44s} p95={p95_text:>12s}{verdict}")

    counters = summary["counters"]
    print(f"environment_false_wake_mechanical_total: {counters.get('environment_false_wake_mechanical_total', 0):.0f}")
    print(f"scheduler coalesced: {counters.get('environment_scheduler_coalesced_total', 0):.0f}")
    print(f"scheduler evicted:   {counters.get('environment_scheduler_evicted_total', 0):.0f}")
    print(f"scheduler dropped:   {counters.get('environment_scheduler_drop_total', 0):.0f}")
    print(f"semantic worker err: {counters.get('environment_semantic_worker_error_total', 0):.0f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tóm tắt latency/queue acceptance Deep Environment V5 từ JSONL.")
    parser.add_argument("jsonl", type=Path, help="Đường dẫn logs/eris_observability*.jsonl")
    parser.add_argument("--last-seconds", type=float, default=30.0, help="Cửa sổ cuối để so với toàn run (mặc định 30 giây).")
    args = parser.parse_args()

    rows = _load(args.jsonl)
    if not rows:
        raise SystemExit(f"Không đọc được metric JSONL từ: {args.jsonl}")
    _print(_summarize(rows), title="ALL RUN")
    _print(_summarize(_window(rows, args.last_seconds)), title=f"LAST {args.last_seconds:g}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
