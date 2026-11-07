# -*- coding: utf-8 -*-
"""V100 regression: associative material chỉ được sinh từ BoundFocus V99."""
from __future__ import annotations

from types import SimpleNamespace

from runtime.focus_material_authority_v100 import FocusMaterialAuthorityV100
from runtime.ai_orchestrated.random_chat_seed_service import RandomChatSeedReservoir
from runtime.motivational_focus_v99 import HostFocusObjectBridgeV99


class _FocusRuntime:
    def __init__(self, focuses):
        self.focuses = list(focuses)

    def snapshot(self, *, limit=16):
        return {
            "schema": "eris.motivational_focus.v99",
            "revision": 12,
            "focuses": self.focuses[:limit],
        }


def _focus(**overrides):
    row = {
        "object_id": "concern:c1",
        "kind": "active_concern",
        "label": "Eris vẫn còn một điểm chưa hiểu trong kiến trúc activation hiện tại.",
        "motive_kind": "develop_concern",
        "source": "active_concern_v99",
        "private_activation": 0.72,
        "surface_readiness": 0.02,
        "persistence": 0.62,
        "evidence_refs": ["concern:c1", "turn:42"],
        "tags": ["concern_state:active"],
    }
    row.update(overrides)
    return row


def test_v100_private_material_does_not_require_surface_readiness():
    authority = FocusMaterialAuthorityV100(_FocusRuntime([_focus(surface_readiness=0.0)]), clock=lambda: 100.0)
    motives = authority.causal_motives(context={"recent_user_events": [{"text": "ignored"}]}, now=100.0)
    assert len(motives) == 1
    motive = motives[0]
    assert motive.cause_kind == "active_concern"
    assert motive.motive_kind == "continue_concern"
    assert motive.semantic_focus.startswith("Eris vẫn còn")
    assert motive.development_required is True
    assert motive.cause_id == "focus:concern:c1"


def test_v100_context_alone_cannot_invent_material_topic():
    authority = FocusMaterialAuthorityV100(_FocusRuntime([]), clock=lambda: 100.0)
    motives = authority.causal_motives(
        context={
            "recent_user_events": [{"text": "Một event rất salient nhưng chưa thành BoundFocus"}],
            "user_memory": "rất nhiều dữ liệu",
        },
        now=100.0,
    )
    assert motives == ()
    assert authority.snapshot()["context_scan_authority"] is False


def test_v100_low_energy_focus_stays_private_dormant_without_worker_call():
    authority = FocusMaterialAuthorityV100(
        _FocusRuntime([_focus(private_activation=0.08, persistence=0.80)]),
        clock=lambda: 100.0,
    )
    assert authority.causal_motives(now=100.0) == ()


def test_v100_user_model_gap_has_higher_gate_to_avoid_interview_bot():
    weak = _focus(
        object_id="user-model:coverage-gap",
        kind="epistemic_gap",
        label="Khoảng trống mô hình user; quan sát trước khi hỏi.",
        motive_kind="understand_user",
        private_activation=0.40,
        persistence=0.50,
        evidence_refs=["user-model:thickness:0.1"],
    )
    strong = dict(weak, private_activation=0.66, persistence=0.40)
    assert FocusMaterialAuthorityV100(_FocusRuntime([weak])).causal_motives(now=10.0) == ()
    motive = FocusMaterialAuthorityV100(_FocusRuntime([strong])).causal_motives(now=10.0)[0]
    assert motive.motive_kind == "understand_user"
    assert "question" in motive.allowed_material_kinds
    assert motive.generation_budget == 1


def test_v100_reservoir_with_provider_never_falls_back_to_context_scan():
    class _Generator:
        enabled = True
        causal_material_generation = True

    reservoir = RandomChatSeedReservoir(
        _Generator(),
        causal_motive_provider=lambda **_: (),
        clock=lambda: 100.0,
    )
    reservoir._context_request = {
        "recent_user_events": [{"text": "Legacy derive_causal_motives would create a motive", "event_ref": "u1"}],
        "relationship": {"connection_pressure": 1.0},
    }
    with reservoir._condition:
        assert reservoir._eligible_causal_motives_locked() == ()
        assert reservoir._ready_to_generate_locked(100.0) is False
    assert reservoir.snapshot(now=100.0)["material_authority"] == "bound_focus_v100"


def test_v100_reservoir_generation_request_is_bound_to_provider_motive():
    class _Generator:
        enabled = True
        causal_material_generation = True

    authority = FocusMaterialAuthorityV100(_FocusRuntime([_focus()]), clock=lambda: 100.0)
    reservoir = RandomChatSeedReservoir(
        _Generator(),
        causal_motive_provider=authority.causal_motives,
        clock=lambda: 100.0,
    )
    reservoir._context_request = {"recent_user_events": [{"text": "Không được chọn trực tiếp", "event_ref": "u1"}]}
    with reservoir._condition:
        request = reservoir._generation_request_locked()
    assert request["material_authority"]["mode"] == "bound_focus_v100"
    assert request["material_authority"]["context_scan_can_choose_topic"] is False
    assert request["causal_motive"]["cause_id"] == "focus:concern:c1"
    assert request["causal_motive"]["semantic_focus"].startswith("Eris vẫn còn")


def test_v100_bridge_projects_conversation_trajectory_into_focus_field():
    class _Trajectory:
        def snapshot(self, **_kwargs):
            return SimpleNamespace(
                to_dict=lambda: {
                    "revision": 9,
                    "topic_id": "topic_activation",
                    "root_focus": "activation tạo động lực",
                    "current_focus": "activation phải biết mình đang quan tâm cái gì",
                    "depth": 3,
                    "momentum": 0.74,
                    "user_engagement": 0.81,
                    "energy": 0.88,
                    "lifecycle_state": "developing",
                    "private_budget": 2,
                    "active": True,
                    "closed": False,
                    "development_mode": "deepen",
                    "last_external_input_at": 1234.0,
                    "source_refs": ["turn:user:1234"],
                }
            )

    host = SimpleNamespace(
        conversation_trajectory_runtime=_Trajectory(),
        active_concern_runtime=None,
        ai_state_reducer=None,
        private_mind_daemon=None,
        private_mind_system=None,
        private_mind_loop=None,
        last_observer_frame=None,
    )
    bridge = HostFocusObjectBridgeV99(host, clock=lambda: 2000.0)
    rows = bridge.collect(now=2000.0)
    trajectory = next(item for item in rows if item.kind == "conversation_trajectory")
    assert trajectory.motive_kind == "continue_trajectory"
    assert trajectory.object_id == "trajectory:topic_activation:e1234"
    assert "turn:user:1234" in trajectory.evidence_refs
    assert "private_budget:2" in trajectory.tags
