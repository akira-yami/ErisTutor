# -*- coding: utf-8 -*-
"""Test tĩnh: bảo đảm UI Memory đã sửa là file Godot THỰC SỰ biên dịch.

Không dùng Godot/.NET trong môi trường CI Python nên test này bảo vệ
đường dẫn, dữ liệu và các điểm nối quan trọng; KHÔNG thay thế build Godot.
"""
from pathlib import Path

ROOT = Path(__file__).parents[1]
PROJECT = ROOT.parent / "Eris.csproj"
BODY = ROOT / "body" / "UdpListener.MemoryPanel.cs"
CONTRACT = ROOT / "integration" / "body_contracts" / "UdpListener.MemoryPanel.cs"


def test_memory_ui_file_that_godot_compiles_is_updated():
    project = PROJECT.read_text(encoding="utf-8")
    body = BODY.read_text(encoding="utf-8")
    assert 'Compile Remove="src\\integration\\body_contracts\\**"' in project
    assert 'CreateMemoryStatCard("Đã xác nhận"' in body
    assert 'CreateMemoryStatCard("Quan sát"' in body
    assert 'CreateMemoryStatCard("Chưa chắc"' in body
    assert 'CreateMemoryStatCard("Định hướng"' in body
    assert 'ERIS / LIVING MEMORY' in body
    assert 'RefreshMemoryPortraitHeader()' in body


def test_memory_ui_matches_integration_contract():
    assert BODY.read_bytes() == CONTRACT.read_bytes(), (
        "Bản UI thật phải đồng bộ bản integration; trước đây patch chỉ sửa "
        "integration nên Godot không đổi giao diện!"
    )


def test_memory_ui_shows_saved_portrait_without_gemini_call():
    body = BODY.read_text(encoding="utf-8")
    assert 'ReadJsonString(portrait, "generation_status")' in body
    assert 'ReadJsonDouble(portrait, "generated_at")' in body
    assert 'ReadJsonInt(portrait, "version")' in body
    assert 'CreateMemoryFullPortraitCard()' in body
    assert 'ReadMemoryPortrait(panel)' in body
    assert 'Gemini đang viết lại' in body
    assert 'create_chat_completion' not in body


def test_memory_ui_keeps_original_manager_actions():
    body = BODY.read_text(encoding="utf-8")
    for action in ('"correct"', '"confirm"', '"explain"', '"move_scope"', '"forget"'):
        assert action in body
    assert 'memory_panel_request' in body
    assert 'Quản lý' in body
