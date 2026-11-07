# -*- coding: utf-8 -*-
"""Kiểm tra tĩnh cho giao diện Moonlit Diary, không thay thế build Godot.

Kiểm tra đúng file C# được build và asset do AI tạo có mặt trong project.
"""
from pathlib import Path
from PIL import Image

SRC = Path(__file__).parents[1]
BODY = SRC / 'body' / 'UdpListener.MemoryPanel.cs'
CONTRACT = SRC / 'integration' / 'body_contracts' / 'UdpListener.MemoryPanel.cs'
ART = SRC / 'body' / 'assets' / 'memory' / 'moonlit_illustration.png'
GLOW = SRC / 'body' / 'assets' / 'memory' / 'moonlit_header_glow.png'
PROJECT = SRC.parent / 'Eris.csproj'


def test_godot_uses_the_updated_runtime_panel():
    source = BODY.read_text(encoding='utf-8')
    assert BODY.read_bytes() == CONTRACT.read_bytes()
    assert 'Compile Remove="src\\integration\\body_contracts\\**"' in PROJECT.read_text(encoding='utf-8')
    assert 'BuildMemoryPanelUi()' in source
    assert 'MemoryPanelPreferredSize = new(1040f, 680f)' in source


def test_moonlit_assets_are_loaded_from_real_paths():
    source = BODY.read_text(encoding='utf-8')
    for path in (ART, GLOW):
        assert path.is_file() and path.stat().st_size > 1000
        with Image.open(path) as image:
            assert image.format == 'PNG'
            assert image.mode == 'RGBA'
            assert min(image.size) > 150
        assert f'res://{path.relative_to(SRC.parent).as_posix()}' in source
    assert 'ResourceLoader.Exists(memoryIllustrationPath)' in source
    assert 'ResourceLoader.Exists(memoryGlowPath)' in source


def test_portrait_is_fully_readable_and_selectable_in_one_scroll():
    source = BODY.read_text(encoding='utf-8')
    assert '_memoryPortraitScroll = new ScrollContainer' in source
    assert '_memorySummaryLabel.Text = _memorySummaryText;' in source
    assert '_memoryPortraitHeroSurface' in source
    assert '_memoryPortraitLeftList.Visible = overview' in source
    assert 'SelectMemoryPortraitView' in source
    assert '"how_i_see_you"' in source
    assert 'SetPressedNoSignal' in source
    assert '_memorySummaryLabel.MaxLinesVisible = 7' not in source
    assert '_memorySummaryLabel.Text = CompactSummaryForPanel' not in source


def test_saved_gemini_and_manager_functions_still_exist():
    source = BODY.read_text(encoding='utf-8')
    for token in ('memory_panel_request', 'generation_status', 'generated_at', 'version',
                  'SaveSelectedMemory', 'ConfirmSelectedMemory', 'ForgetSelectedMemory',
                  'MoveSelectedMemoryScope', 'ExplainSelectedMemory',
                  'StartMemoryCleanup', '"refresh_portrait"'):
        assert token in source
    assert 'create_chat_completion' not in source
