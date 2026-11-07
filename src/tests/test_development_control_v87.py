from __future__ import annotations

from pathlib import Path

from runtime.capability_projection import CapabilityProjectionService
from runtime.development_control import DevelopmentControlService
from runtime.engineering_mentor import EngineeringMentorService
from runtime.incoming_protocol_runtime import IncomingProtocolRuntimeMixin


def _mentor(tmp_path: Path) -> EngineeringMentorService:
    mentor = EngineeringMentorService(path=tmp_path / "skills.json")
    # Tạo evidence thật bằng public model API; không sửa raw JSON test fixture.
    mentor.model.observe_user_turn(
        "Tôi nghĩ lỗi nằm ở shadow_manager.gd vì ShadowManager.update() đổi state chung, cần review diff và test lại.",
        evidence_ref="turn-v87-1",
        now=1_900_000_000,
    )
    mentor.model.observe_user_turn(
        "Tôi nghĩ EntityShadow.gd gọi ShadowManager.update(), kiểm tra diff và test regression.",
        evidence_ref="turn-v87-2",
        now=1_900_000_010,
    )
    return mentor


def test_projection_exposes_sanitized_evidence_without_raw_chat(tmp_path: Path) -> None:
    mentor = _mentor(tmp_path)
    projection = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
    panel = projection.build(mentor.snapshot(), now=1_900_000_020)
    debug = next(x for x in panel["nhom_nang_luc"] if x["id"] == "debugging")
    skill = next(x for x in debug["ky_nang"] if x["id"] == "debugging.root_cause_reasoning")
    assert skill["bang_chung_gan_day"]
    serialized = str(skill["bang_chung_gan_day"])
    assert "shadow_manager.gd" not in serialized
    assert "User nêu hypothesis" in serialized


def test_user_correction_reduces_confidence_but_does_not_raise_level(tmp_path: Path) -> None:
    mentor = _mentor(tmp_path)
    skill_id = "debugging.root_cause_reasoning"
    before = mentor.model.skills[skill_id].to_dict()
    assert mentor.apply_panel_skill_feedback(skill_id, "wrong", now=1_900_000_030)
    after = mentor.model.skills[skill_id].to_dict()
    assert after["level"] == before["level"]
    assert after["confidence"] < before["confidence"]
    assert mentor.apply_panel_skill_feedback(skill_id, "confirmed", now=1_900_000_040)
    confirmed = mentor.model.skills[skill_id].to_dict()
    assert confirmed["level"] == after["level"]


def test_follow_goal_uses_evidence_delta_and_auto_completes(tmp_path: Path) -> None:
    mentor = _mentor(tmp_path)
    projection = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
    controls = DevelopmentControlService(path=tmp_path / "development.json")
    panel = projection.build(mentor.snapshot(), now=1_900_000_020)
    panel = controls.decorate(panel, now=1_900_000_020)
    rec = next(x for x in panel["de_xuat_dai_han"] if x["metric"]["kind"] == "group_evidence")
    goal = controls.follow_recommendation(rec, panel, now=1_900_000_030)
    assert goal and goal["status"] == "active"

    # Thêm đủ 3 evidence mới vào cùng nhóm debugging.
    for idx in range(3):
        mentor.model.observe_user_turn(
            f"Tôi nghĩ RootCause{idx}() trong file{idx}.gd là shared path, review diff và test.",
            evidence_ref=f"turn-more-{idx}",
            now=1_900_000_100 + idx,
        )
    newer = projection.build(mentor.snapshot(), now=1_900_000_200)
    newer = controls.decorate(newer, now=1_900_000_200)
    # Một grounded hypothesis tăng 2 skill trong debugging nên target +3 evidence đạt nhanh.
    assert not newer["muc_tieu_hien_tai"]
    stats = newer["thong_ke_phat_trien_30_ngay"]
    assert stats["muc_tieu_hoan_thanh"] == 1


def test_unknown_tools_are_not_converted_into_low_scores(tmp_path: Path) -> None:
    mentor = _mentor(tmp_path)
    projection = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
    controls = DevelopmentControlService(path=tmp_path / "development.json")
    panel = controls.decorate(projection.build(mentor.snapshot(), now=1_900_000_020), now=1_900_000_020)
    profiler = next(x for x in panel["cong_cu"] if x["id"] == "profiler")
    assert profiler["muc_do"] == "Chưa đủ dữ liệu"
    assert "phan_tram" not in profiler


def test_active_goals_are_bounded_to_three(tmp_path: Path) -> None:
    controls = DevelopmentControlService(path=tmp_path / "development.json")
    panel = {
        "nhom_nang_luc": [{"id": "debugging", "so_bang_chung": 1}],
        "cong_cu": [],
        "thoi_quen_ai_30_ngay": {},
    }
    for idx in range(3):
        rec = {
            "id": f"rec-{idx}",
            "tieu_de": f"Mục {idx}",
            "metric": {"kind": "group_evidence", "id": "debugging", "target_delta": 3},
        }
        assert controls.follow_recommendation(rec, panel, now=100 + idx) is not None
    fourth = {
        "id": "rec-4",
        "tieu_de": "Mục 4",
        "metric": {"kind": "group_evidence", "id": "debugging", "target_delta": 3},
    }
    assert controls.follow_recommendation(fourth, panel, now=200) is None


class _Owner(IncomingProtocolRuntimeMixin):
    def __init__(self, tmp_path: Path) -> None:
        self.engineering_mentor_service = _mentor(tmp_path)
        self.capability_projection_service = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
        self.development_control_service = DevelopmentControlService(path=tmp_path / "development.json")
        self.sent: list[tuple[str, dict]] = []

    def _send_runtime_json(self, payload: dict, *, channel: str = "control", required: bool = False) -> bool:
        self.sent.append((channel, payload))
        return True


def test_udp_trusted_action_can_correct_skill_and_returns_fresh_snapshot(tmp_path: Path) -> None:
    owner = _Owner(tmp_path)
    skill = "debugging.root_cause_reasoning"
    before = owner.engineering_mentor_service.model.skills[skill].confidence
    owner._handle_capability_panel_request({
        "request_id": "v87-1",
        "action": "skill_feedback",
        "skill_id": skill,
        "feedback": "wrong",
    })
    packet = owner.sent[-1][1]
    assert packet["status"] == "ok"
    assert owner.engineering_mentor_service.model.skills[skill].confidence < before
    assert packet["capability_panel"]["user_control"]["loopback_http_write_exposed"] is False


def test_invalid_capability_action_is_rejected_without_mutation(tmp_path: Path) -> None:
    owner = _Owner(tmp_path)
    before = owner.engineering_mentor_service.model.snapshot()
    owner._handle_capability_panel_request({"request_id": "v87-2", "action": "run_command"})
    packet = owner.sent[-1][1]
    assert packet["status"] == "error"
    assert owner.engineering_mentor_service.model.snapshot()["skills"] == before["skills"]
