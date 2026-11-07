from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v86_body_panel_is_wired_into_desktop_menu_and_transport() -> None:
    listener = (ROOT / "body" / "UdpListener.cs").read_text(encoding="utf-8")
    panel = (ROOT / "body" / "UdpListener.CapabilityPanel.cs").read_text(encoding="utf-8")
    assert '"Eris hiểu bạn"' not in listener
    assert "InitializeCapabilityPanel();" in listener
    assert "TryHandleCapabilityPanelPacket(raw)" in listener
    assert '"ERIS HIỂU BẠN THẾ NÀO"' in panel
    assert '"capability_panel_request"' in panel
    assert '"Chưa đủ dữ liệu"' in panel
    assert '"Đề xuất dài hạn"' in panel


def test_v86_panel_has_no_score_mutation_protocol() -> None:
    panel = (ROOT / "body" / "UdpListener.CapabilityPanel.cs").read_text(encoding="utf-8")
    forbidden = ("set_skill", "set_score", "force_level", "coding/control", "approval/respond")
    assert all(token not in panel for token in forbidden)
