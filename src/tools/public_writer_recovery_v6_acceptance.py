# -*- coding: utf-8 -*-
"""Tóm tắt acceptance cho Public Writer Recovery V6 từ observability JSONL.

Ví dụ:
    py tools/public_writer_recovery_v6_acceptance.py logs/eris_observability.jsonl --last-seconds 300

Tool chỉ đọc metric, không đọc/ghi nội dung chat. Như vậy ta đo được writer có
trả câu dùng được hay không mà không biến log nghiệm thu thành kho lưu hội thoại.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


METRICS = (
    "public_writer_provider_ms",
    "public_writer_content_chars",
    "public_writer_usable_text",
    "public_writer_text_salvaged",
    "public_writer_contract_valid",
    "public_writer_wording_retry",
    "public_writer_fallback",
    "public_writer_commit_ms",
    "user_turn_latency_ms",
)


def _p(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(math.ceil(q * len(ordered))) - 1))
    return round(float(ordered[index]), 2)


def _fmt(value: float | None) -> str:
    return "NO_DATA" if value is None else f"{value:.2f}"


def load(path: Path, *, last_seconds: float | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    if not rows or not last_seconds:
        return rows
    latest = max(float(row.get("timestamp", 0.0) or 0.0) for row in rows)
    floor = latest - max(0.0, float(last_seconds))
    return [row for row in rows if float(row.get("timestamp", 0.0) or 0.0) >= floor]


def main() -> int:
    parser = argparse.ArgumentParser(description="Acceptance Public Writer Recovery V6")
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--last-seconds", type=float, default=None)
    args = parser.parse_args()

    rows = load(args.jsonl, last_seconds=args.last_seconds)
    values: dict[str, list[float]] = {name: [] for name in METRICS}
    runtime_errors = 0
    fallback_total = 0.0

    for row in rows:
        name = str(row.get("name", "") or "")
        if str(row.get("status", "") or "").lower() == "error":
            runtime_errors += 1
        if name not in values:
            continue
        try:
            value = float(row.get("value", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        values[name].append(value)
        if name == "public_writer_fallback":
            fallback_total += value

    print(f"rows: {len(rows)}")
    print(f"runtime errors: {runtime_errors}")
    print(f"public_writer_fallback_total: {int(fallback_total)}")
    print()
    for name in METRICS:
        bucket = values[name]
        print(
            f"{name}: count={len(bucket)} p50={_fmt(_p(bucket, 0.50))} "
            f"p95={_fmt(_p(bucket, 0.95))} max={_fmt(max(bucket) if bucket else None)}"
        )

    usable = values["public_writer_usable_text"]
    if usable:
        ratio = 100.0 * sum(1 for item in usable if item >= 0.5) / len(usable)
        print(f"\nusable public reply ratio: {ratio:.1f}%")
    print("PASS nền tảng" if runtime_errors == 0 and fallback_total == 0 else "CHƯA PASS nền tảng")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
