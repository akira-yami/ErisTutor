from __future__ import annotations

import json
from pathlib import Path

from runtime.capability_projection import CapabilityProjectionService


def _state() -> dict:
    return {
        "model": {
            "overreliance_risk": 0.23,
            "skills": {
                "debugging.failure_localization": {"level": 0.80, "confidence": 0.70, "evidence_count": 5, "last_observed_at": 1000},
                "debugging.call_flow_tracing": {"level": 0.70, "confidence": 0.60, "evidence_count": 4, "last_observed_at": 1000},
                "debugging.root_cause_reasoning": {"level": 0.66, "confidence": 0.50, "evidence_count": 3, "last_observed_at": 1000},
                "verification.testing": {"level": 0.62, "confidence": 0.50, "evidence_count": 4, "last_observed_at": 1000},
                "verification.diff_review": {"level": 0.55, "confidence": 0.44, "evidence_count": 3, "last_observed_at": 1000},
                "architecture.system_boundaries": {"level": 0.73, "confidence": 0.53, "evidence_count": 3, "last_observed_at": 1000},
                "ai_operation.task_scoping": {"level": 0.64, "confidence": 0.40, "evidence_count": 3, "last_observed_at": 1000},
                "ai_operation.evidence_review": {"level": 0.59, "confidence": 0.36, "evidence_count": 2, "last_observed_at": 1000},
            },
            "recent_signals": [
                {"at": 1_900_000_000, "signals": ["grounded_hypothesis", "verification_intent"]},
                {"at": 1_900_000_001, "signals": ["blind_execution_request"]},
            ],
        },
        "tool_literacy": {
            "tools": {
                "git_diff": {"stage": "familiar", "demonstrated_count": 5, "exposures": 8, "confidence": 0.40},
                "debugger": {"stage": "demonstrated", "demonstrated_count": 1, "exposures": 3, "confidence": 0.20},
                "profiler": {"stage": "unobserved", "demonstrated_count": 0, "exposures": 0, "confidence": 0.05},
            }
        },
        "mentor_calibration": {"metrics": {"interventions": 8, "helpful": 3, "user_corrected_eris": 1}},
    }


def test_unknown_skill_does_not_fake_50_percent(tmp_path: Path) -> None:
    state = _state()
    state["model"]["skills"]["architecture.system_boundaries"] = {
        "level": 0.5, "confidence": 0.05, "evidence_count": 0
    }
    service = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
    snap = service.build(state, now=1_900_000_010)
    arch = next(x for x in snap["nhom_nang_luc"] if x["id"] == "architecture")
    assert arch["da_du_bang_chung"] is False
    assert arch["phan_tram"] is None
    assert arch["do_tin_cay"] == "Chưa đủ dữ liệu"


def test_observed_groups_and_tools_are_vietnamese_statistics(tmp_path: Path) -> None:
    service = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
    snap = service.build(_state(), now=1_900_000_010)
    debugging = next(x for x in snap["nhom_nang_luc"] if x["id"] == "debugging")
    assert debugging["ten"] == "Gỡ lỗi"
    assert debugging["phan_tram"] == 72
    assert debugging["so_bang_chung"] == 12
    profiler = next(x for x in snap["cong_cu"] if x["id"] == "profiler")
    assert profiler["muc_do"] == "Chưa đủ dữ liệu"
    assert snap["thoi_quen_ai_30_ngay"]["rui_ro_phu_thuoc_ai"] == 23


def test_first_day_trend_is_unknown_not_invented(tmp_path: Path) -> None:
    service = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
    snap = service.build(_state(), now=1_900_000_010)
    # Refresh trong cùng ngày cũng phải tiếp tục "chưa đủ lịch sử".
    snap = service.build(_state(), now=1_900_000_020)
    assert snap["xu_huong_30_ngay"]
    assert all(row["delta"] is None for row in snap["xu_huong_30_ngay"])
    assert all(row["nhan"] == "Chưa đủ lịch sử" for row in snap["xu_huong_30_ngay"])


def test_daily_history_is_append_only_one_point_per_day(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    service = CapabilityProjectionService(history_path=path)
    service.build(_state(), now=1_900_000_010)
    service.build(_state(), now=1_900_000_020)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["schema"] == "eris.capability_history.v1"


def test_recommendations_are_bounded_and_evidence_based(tmp_path: Path) -> None:
    service = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
    state = _state()
    state["model"]["recent_signals"].extend([
        {"at": 1_900_000_002, "signals": ["blind_execution_request"]},
        {"at": 1_900_000_003, "signals": ["blind_execution_request"]},
    ])
    snap = service.build(state, now=1_900_000_010)
    recs = snap["de_xuat_dai_han"]
    assert 1 <= len(recs) <= 3
    assert all(x["ly_do"] and x["thuc_hanh"] and x["dau_hieu_tien_bo"] for x in recs)
    assert any("ownership" in x["tieu_de"].lower() for x in recs)
