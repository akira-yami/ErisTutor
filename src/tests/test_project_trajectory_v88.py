# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from runtime.project_trajectory import ProjectTrajectoryService


def _event(*, task: str, at: float, event_type: str, actor: str = "coder", files=None, metrics=None, tool="", status=""):
    row = {
        "schema": "eris.coding.observation.v1",
        "task_id": task,
        "session_id": "s-" + task,
        "workspace": "C:/repo/demo",
        "actor": actor,
        "event_type": event_type,
        "at": at,
    }
    if files:
        row["files"] = list(files)
    if metrics:
        row["metrics"] = dict(metrics)
    if tool:
        row["tool"] = tool
    if status:
        row["status"] = status
    return row


def _write(path: Path, rows: list[dict]):
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")


def test_v88_hotspot_and_candidate_debt_are_evidence_first(tmp_path: Path):
    now = 2_000_000_000.0
    rows = []
    for idx in range(1, 4):
        task = f"t{idx}"
        t = now - idx * 1000
        rows += [
            _event(task=task, at=t, event_type="task_started", actor="gitpilot"),
            _event(task=task, at=t + 1, event_type="tool_started", actor="explorer", tool="read_file"),
            _event(task=task, at=t + 2, event_type="file_changed", files=["C:/repo/demo/src/runtime/shadow.py"]),
            _event(task=task, at=t + 3, event_type="task_completed", actor="gitpilot"),
        ]
    journal = tmp_path / "coding.jsonl"
    _write(journal, rows)
    result = ProjectTrajectoryService(journal_path=journal).build(now=now)

    assert result["tong_quan"]["task_da_quan_sat"] == 3
    assert result["diem_nong"][0]["file"] == "src/runtime/shadow.py"
    assert result["diem_nong"][0]["so_task_cham"] == 3
    assert any(x["id"] == "hotspot-regression-coverage" for x in result["no_ky_thuat_ung_vien"])
    assert "score" not in json.dumps(result, ensure_ascii=False).lower()
    assert "không đồng nghĩa" in result["diem_nong"][0]["ghi_chu"].lower()


def test_v88_test_failure_is_signal_not_automatic_regression_claim(tmp_path: Path):
    now = 2_000_000_000.0
    rows = [
        _event(task="t1", at=now - 50, event_type="task_started", actor="gitpilot"),
        _event(task="t1", at=now - 40, event_type="test_result", actor="validation", metrics={"failed": 2}),
        _event(task="t1", at=now - 30, event_type="task_failed", actor="gitpilot"),
    ]
    journal = tmp_path / "coding.jsonl"
    _write(journal, rows)
    result = ProjectTrajectoryService(journal_path=journal).build(now=now)
    assert result["tong_quan"]["task_co_test_fail"] == 1
    raw = json.dumps(result, ensure_ascii=False).casefold()
    assert "regression đã xảy ra" not in raw
    assert "không suy causality" in result["ghi_chu"].casefold()


def test_v88_empty_journal_says_insufficient_data(tmp_path: Path):
    result = ProjectTrajectoryService(journal_path=tmp_path / "missing.jsonl").build(now=2_000_000_000.0)
    assert result["du_lieu_day_du"] is False
    assert result["tong_quan"]["task_da_quan_sat"] == 0
    assert "chưa đủ" in result["ghi_chu"].casefold()


def test_v88_project_prompt_only_for_project_level_question(tmp_path: Path):
    service = ProjectTrajectoryService(journal_path=tmp_path / "missing.jsonl")
    assert service.prompt_context("sửa bug này đi", now=2_000_000_000.0) == ""
    ctx = service.prompt_context("project này 2 tuần tới nên ưu tiên gì?", now=2_000_000_000.0)
    assert ctx.startswith("PROJECT_TRAJECTORY_V88:")
    assert "không tự sửa code" in ctx


def test_v88_panel_source_contains_project_statistics_section():
    source = Path(__file__).parents[1] / "body" / "UdpListener.CapabilityPanel.cs"
    text = source.read_text(encoding="utf-8")
    assert "SỨC KHỎE DỰ ÁN · 30 NGÀY" in text
    assert "BẠN × DỰ ÁN" in text
    assert "Tần suất sửa chỉ là tín hiệu quan sát" in text
