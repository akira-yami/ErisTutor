# -*- coding: utf-8 -*-
from __future__ import annotations

"""Windows E2E probe cho Deep Environment.

Probe này không tự động click/gõ vào app và không dump accessibility text. Nó
chỉ kiểm tra sensor/provider có hoạt động, đếm window/tab và đo latency cục bộ.
Dùng trên máy Windows thật với Chrome/Edge, VS Code, Windows Terminal, Explorer,
Task Manager và media app đang mở để hoàn tất acceptance matrix của PDF.
"""

import argparse
import ctypes
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native.chromium_accessibility import ChromiumAccessibilityAdapter
from native.conpty_session import OwnedTerminalSession
from native.ia2_accessibility import IA2AccessibilityBackend
from native.media_monitor import MediaMonitor
from native.process_resolver import ProcessResolver
from native.uia_event_client import UIAEventClient
from native.uiaccess_broker import UIAccessBrokerClient


EXPECTED_APPS = {
    "chromium": {"chrome.exe", "msedge.exe", "brave.exe", "chromium.exe"},
    "ide": {"code.exe", "devenv.exe", "pycharm64.exe", "rider64.exe", "idea64.exe"},
    "terminal": {"windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe"},
    "explorer": {"explorer.exe"},
    "task_manager": {"taskmgr.exe"},
}


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = max(0, min(len(values) - 1, int(len(values) * 0.95 + 0.999) - 1))
    return values[index]


def _enumerate_windows() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    resolver = ProcessResolver()
    user32 = ctypes.windll.user32
    found: list[dict[str, Any]] = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):  # pragma: no cover - Windows E2E
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            identity = resolver.resolve(int(hwnd))
            if identity.process_name:
                found.append({
                    "hwnd": int(hwnd),
                    "pid": int(identity.pid),
                    "process_name": identity.process_name,
                    "window_class": identity.window_class,
                })
        except Exception:
            pass
        return True

    user32.EnumWindows(PROC(cb), 0)
    return found


def build_report(*, conpty_smoke: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "platform": os.name,
        "windows_required": True,
        "timestamp": time.time(),
        "privacy_note": "Không lưu title/tab text/raw accessibility content trong probe report.",
    }
    if os.name != "nt":
        report["status"] = "SKIPPED_NOT_WINDOWS"
        return report

    windows = _enumerate_windows()
    counts: dict[str, int] = {}
    processes = [str(item.get("process_name", "")).lower() for item in windows]
    for category, names in EXPECTED_APPS.items():
        counts[category] = sum(1 for name in processes if name in names)
    report["visible_window_counts"] = counts

    ia2 = IA2AccessibilityBackend()
    uia = UIAEventClient()
    report["providers"] = {
        "ia2_available": ia2.available,
        "ia2_unavailable_reason": ia2.unavailable_reason if not ia2.available else "",
        "uia_available": uia.enabled,
    }

    chromium = ChromiumAccessibilityAdapter(ia2=ia2, uia=uia).start()
    chromium_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    for hwnd in chromium.discover_windows():
        started = time.perf_counter()
        snap = chromium.snapshot_window(hwnd, force=True)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        latencies.append(elapsed_ms)
        if snap is not None:
            chromium_results.append({
                "hwnd": int(hwnd),
                "provider": snap.provider,
                "tab_count": len(snap.tabs),
                "selected_tab_present": bool(snap.selected_tab),
                "active_domain_present": bool(snap.active_domain),
                "snapshot_ms": round(elapsed_ms, 2),
            })
    report["chromium"] = {
        "windows": chromium_results,
        "snapshot_p95_ms": round(_p95(latencies), 2) if latencies else None,
        "ia2_window_count": sum(1 for item in chromium_results if item["provider"] == "ia2"),
        "uia_fallback_window_count": sum(1 for item in chromium_results if item["provider"] == "uia"),
    }
    chromium.stop()

    media_events: list[Any] = []
    media = MediaMonitor(media_events.append, include_content_metadata=False)
    report["media"] = {"gsmtc_available": media.available, "content_metadata_default": False}

    broker = UIAccessBrokerClient()
    broker_status: dict[str, Any] = {"configured": broker.configured}
    if broker.configured:
        response = broker.request(__import__("native.uiaccess_broker.protocol", fromlist=["BrokerRequest"]).BrokerRequest(operation="ping"))
        broker_status["ping_ok"] = response.ok
        broker_status["error"] = response.error
    report["uiaccess_broker"] = broker_status

    terminal_events: list[Any] = []
    terminal = OwnedTerminalSession(terminal_events.append)
    report["conpty"] = {"available": terminal.conpty_available, "smoke_ran": False}
    if conpty_smoke:
        result = terminal.run(["cmd.exe", "/d", "/s", "/c", "echo ERIS_CONPTY_OK"], timeout=5.0)
        report["conpty"].update({
            "smoke_ran": True,
            "returncode": result.returncode,
            "output_marker_present": "ERIS_CONPTY_OK" in (result.stdout or ""),
        })

    missing = [category for category, count in counts.items() if count <= 0]
    report["coverage"] = {
        "missing_expected_app_categories": missing,
        "all_expected_app_categories_present": not missing,
        "note": "Media app được xác nhận qua GSMTC availability/session scenario riêng, không dựa tên process cứng.",
    }
    report["status"] = "READY_FOR_SCRIPTED_SCENARIOS" if not missing else "PARTIAL_APP_COVERAGE"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Windows Deep Environment providers")
    parser.add_argument("--conpty-smoke", action="store_true", help="Launch cmd echo qua OwnedTerminalSession")
    parser.add_argument("--output", help="Ghi JSON report")
    args = parser.parse_args()
    report = build_report(conpty_smoke=args.conpty_smoke)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if report.get("status") not in {"SKIPPED_NOT_WINDOWS"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
