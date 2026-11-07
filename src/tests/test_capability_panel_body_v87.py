from pathlib import Path


def test_v87_panel_has_vietnamese_evidence_controls_and_goals() -> None:
    text = (Path(__file__).parents[1] / "body" / "UdpListener.CapabilityPanel.cs").read_text(encoding="utf-8")
    for phrase in (
        "Mục tiêu đang theo dõi",
        "Đánh giá sai",
        "Tôi đã biết",
        "Theo dõi mục này",
        "Dừng theo dõi",
        "Thống kê phát triển · 30 ngày",
        "Đặt lại mô hình",
    ):
        assert phrase in text
    assert 'private void SendSkillFeedback' in text
    assert 'SendCapabilityAction("skill_feedback"' in text
    assert 'SendCapabilityAction("follow_recommendation"' in text
    assert 'SendCapabilityAction("stop_goal"' in text
