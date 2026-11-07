# -*- coding: utf-8 -*-
"""Regression cho Danger Zone mới.

Giữ các contract quan trọng:
- hành động an toàn không bắt xác nhận gây mỏi;
- reset phiên chỉ cần xác nhận đơn giản;
- xóa dài hạn dùng typed confirmation và tên hành động rõ;
- catalog trả metadata backup để Body không tự quét memory_core.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class _MemoryCore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = str(base_dir)
        self.semantic = None


class _Owner:
    def __init__(self, base_dir: Path) -> None:
        self.memory_core = _MemoryCore(base_dir)


def test_confirmation_levels_match_real_risk(tmp_path, monkeypatch) -> None:
    from runtime.maintenance_service import MaintenanceService

    monkeypatch.setenv("ERIS_TTS_VOCAL_CACHE_DIR", str(tmp_path / "vocal_cache"))
    service = MaintenanceService(_Owner(tmp_path / "memory_core"))

    safe = service.preview("clear_vocal_cache")
    assert safe["requires_confirmation"] is False
    assert safe["confirmation_mode"] == "none"
    assert safe["confirm_label"] == "Dọn cache"

    warning = service.preview("reset_session_state")
    assert warning["requires_confirmation"] is True
    assert warning["confirmation_mode"] == "simple"
    assert warning["confirm_label"] == "Reset phiên"

    destructive = service.preview("reset_long_term_relationship")
    assert destructive["requires_confirmation"] is True
    assert destructive["confirmation_mode"] == "typed"
    assert destructive["typed_confirmation"] == "XÓA QUAN HỆ"
    assert destructive["confirm_label"] == "Xóa quan hệ"


def test_factory_reset_is_not_mislabeled_as_restore() -> None:
    from runtime.maintenance_service import ACTION_SPECS, MaintenanceService

    spec = ACTION_SPECS["factory_reset"]
    assert spec.title == "Đặt lại toàn bộ Eris"
    assert "Khôi phục" not in spec.title
    assert spec.confirmation_mode == "typed"
    assert MaintenanceService.confirmation_phrase("factory_reset") == "ĐẶT LẠI TOÀN BỘ"


def test_composite_actions_have_complete_file_scope(tmp_path) -> None:
    from runtime.maintenance_service import MaintenanceService

    service = MaintenanceService(_Owner(tmp_path / "memory_core"))
    session_files = {path.name for path in service._files_for_action("reset_session_state", options={})}
    inner_files = {path.name for path in service._files_for_action("reset_inner_state", options={})}

    assert session_files == {"active_state_snapshot.json"}
    assert {
        "private_concerns.json",
        "thought_stream.jsonl",
        "inner_diary.json",
        "internal_agenda.json",
        "private_life_state.json",
    } <= inner_files


def test_catalog_reports_latest_backup(tmp_path, monkeypatch) -> None:
    from runtime.maintenance_service import MaintenanceService

    root = tmp_path / "memory_core"
    backup = root / "backups" / "maintenance_test_reset_relationship"
    backup.mkdir(parents=True)
    (backup / "relational_memory.json").write_text("{}", encoding="utf-8")
    created_at = time.time() - 10
    (backup / "manifest.json").write_text(
        json.dumps({"action": "reset_long_term_relationship", "created_at": created_at}),
        encoding="utf-8",
    )

    service = MaintenanceService(_Owner(root))
    monkeypatch.setattr(service, "_provider_health", lambda: {})
    monkeypatch.setattr(service, "_semantic_index_health", lambda: {})
    catalog = service.catalog()

    assert catalog["backup_root"] == str((root / "backups").resolve())
    summary = catalog["backup_summary"]
    assert summary["count"] == 1
    assert summary["latest"]["action"] == "reset_long_term_relationship"
    assert summary["latest"]["path"].endswith("maintenance_test_reset_relationship")
    assert summary["total_bytes"] > 0
