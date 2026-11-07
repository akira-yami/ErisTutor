from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "body" / "UdpListener.CapabilityPanel.cs"
LISTENER = ROOT / "body" / "UdpListener.cs"


def _panel() -> str:
    return PANEL.read_text(encoding="utf-8")


def test_capability_panel_is_companion_first_not_score_dashboard() -> None:
    text = _panel()
    assert 'ERIS HIỂU BẠN THẾ NÀO' in text
    assert 'Working model, không phải bảng điểm' in text
    assert 'ERIS ĐANG HIỂU BẠN' in text
    assert 'CÁCH ERIS NÊN NÓI' in text
    assert 'Vì sao Eris nghĩ vậy?' in text


def test_capability_panel_has_overview_and_evidence_modes() -> None:
    text = _panel()
    assert 'CapabilityViewMode' in text
    assert 'RenderCapabilityOverview' in text
    assert 'RenderCapabilityEvidence' in text
    assert 'Tổng quan' in text
    assert 'Bằng chứng' in text


def test_overview_uses_qualitative_stage_instead_of_group_progress_bar() -> None:
    text = _panel()
    start = text.index('private Control CreateCapabilityOverviewRow')
    end = text.index('private Control CreateCapabilityGuidanceCard', start)
    region = text[start:end]
    assert 'ProgressBar' not in region
    assert 'CapabilityStageLabel' in region
    assert 'Mạnh' in text
    assert 'Đang phát triển' in text
    assert 'Đang học' in text
    assert 'Chưa rõ' in text


def test_panel_keeps_eris_primary_light_pink_design_language() -> None:
    text = _panel()
    # Panel mới phải tái dùng design token chủ đạo, không tự dựng dark theme riêng.
    assert 'MemoryInk' in text
    assert 'MemoryInkSoft' in text
    assert 'MemoryPink' in text
    assert 'MemoryBorder' in text
    assert 'CreateMemoryCard' in text
    assert 'new Color(0.04f' not in text


def test_legacy_gitpilot_project_telemetry_is_not_rendered_in_companion_panel() -> None:
    text = _panel()
    apply_start = text.index('private void ApplyCapabilitySnapshot')
    apply_end = text.index('private Control CreateCapabilitySectionHeader', apply_start)
    active_render = text[apply_start:apply_end]
    assert 'du_an_30_ngay' not in active_render
    assert 'ghep_user_du_an' not in active_render
    assert 'CodingObservation' not in active_render


def test_context_menu_names_panel_as_user_model_not_exam_score() -> None:
    text = LISTENER.read_text(encoding="utf-8")
    assert 'ConfigureContextMenuButton(_contextCapabilityButton, "Eris hiểu bạn"' not in text
