# -*- coding: utf-8 -*-
"""Kiểm tra nhanh log runtime V61 để phát hiện các kiểu "sống mà như chết".

Không thay thế test/runtime health registry. Tool này dành cho operator: đưa một file
Eris run log vào và nhận tóm tắt các lỗi liveness từng xuất hiện ở V60.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, deque
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Quét log Eris cho startup crash, zombie urge và semantic wake storm.")
    parser.add_argument("log", help="Đường dẫn file Eris run log")
    args = parser.parse_args()

    path = Path(args.log)
    if not path.exists():
        print(f"[ERROR] Không tìm thấy log: {path}")
        return 2
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    startup_programmer = [line for line in lines if "[architecture C startup] failed" in line and any(x in line for x in ("NameError", "TypeError", "AttributeError", "UnboundLocalError"))]
    duplicate_lines = [line for line in lines if "surface_duplicate_recent_utterance" in line and "unavailable reason=" in line]
    suppressed_lines = [line for line in lines if "[expression urge] duplicate-suppressed" in line]
    busy_lines = [line for line in lines if "RoleWorkerBusyError" in line]

    wake_times: list[float] = []
    time_re = re.compile(r"\b(\d{2}):(\d{2}):(\d{2})\.(\d{3})")
    for line in lines:
        if "[autonomous wake] reason=environment_semantic:" not in line:
            continue
        m = time_re.search(line)
        if not m:
            continue
        hh, mm, ss, ms = map(int, m.groups())
        wake_times.append(hh * 3600 + mm * 60 + ss + ms / 1000.0)
    burst_max = 0
    q: deque[float] = deque()
    for stamp in wake_times:
        q.append(stamp)
        while q and stamp - q[0] > 3.0:
            q.popleft()
        burst_max = max(burst_max, len(q))

    problems = []
    if startup_programmer:
        problems.append(f"startup programmer error={len(startup_programmer)}")
    # Sau V61 một duplicate đầu tiên có thể hợp lệ, nhưng lặp cùng session mà không
    # có suppression receipt là dấu hiệu zombie lifecycle.
    if len(duplicate_lines) >= 2 and not suppressed_lines:
        problems.append(f"duplicate surface storm={len(duplicate_lines)} without suppression receipt")
    if burst_max >= 4:
        problems.append(f"environment wake storm=max {burst_max} wake/3s")

    print("=== ERIS V61 RUNTIME LOG HEALTH ===")
    print(f"file: {path}")
    print(f"startup_programmer_errors : {len(startup_programmer)}")
    print(f"surface_duplicate_blocks  : {len(duplicate_lines)}")
    print(f"duplicate_suppression_receipts: {len(suppressed_lines)}")
    print(f"environment_wakes         : {len(wake_times)} (max_burst_3s={burst_max})")
    print(f"private_mind_busy         : {len(busy_lines)} (transient, không coi là crash)")
    if problems:
        print("RESULT: FAIL")
        for item in problems:
            print(f"- {item}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
