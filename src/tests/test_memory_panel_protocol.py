from __future__ import annotations

from memory.living_user import LivingUserMemoryService
from runtime.living_user_panel import build_memory_panel_snapshot, handle_memory_panel_request


def test_memory_panel_snapshot_and_mutations(tmp_path):
    memory = LivingUserMemoryService(tmp_path)
    memory.correct(
        "communication.response_style",
        "thẳng, kỹ và có file hoàn chỉnh",
        evidence_ids=("turn_1",),
    )

    snapshot = build_memory_panel_snapshot(memory)
    assert snapshot["counts"]["confirmed"] == 1
    assert snapshot["records"][0]["explicitness_label"] == "Bạn đã nói"
    assert snapshot["records"][0]["scope_label"] == "Mọi cuộc trò chuyện"

    corrected = handle_memory_panel_request(memory, {
        "action": "correct",
        "path": "communication.response_style",
        "value": "thẳng, có chú thích tiếng Việt",
    })
    assert corrected.status == "ok"
    assert corrected.payload["records"][0]["display_value"] == "thẳng, có chú thích tiếng Việt"

    moved = handle_memory_panel_request(memory, {
        "action": "move_scope",
        "path": "communication.response_style",
        "target_scope_kind": "project",
        "target_scope_key": "eris",
    })
    assert moved.status == "ok"
    assert any(row["scope_key"] == "eris" for row in moved.payload["records"])

    forgotten = handle_memory_panel_request(memory, {
        "action": "forget",
        "path": "communication.response_style",
        "project_id": "eris",
        "hard": True,
    })
    assert forgotten.status == "ok"
    assert forgotten.payload["records"] == []


def test_memory_panel_hides_sensitive_and_can_confirm(tmp_path):
    memory = LivingUserMemoryService(tmp_path)
    memory.correct("user.name", "Khang", evidence_ids=("turn_name",))
    memory.profile_store.correct(
        "private.api_token",
        "do-not-render",
        evidence_ids=("turn_secret",),
    )
    secret = memory.profile_store.get("private.api_token")[0]
    secret.sensitivity = "sensitive"
    memory.profile_store.save()

    snapshot = build_memory_panel_snapshot(memory)
    assert [row["path"] for row in snapshot["records"]] == ["user.name"]

    result = handle_memory_panel_request(memory, {
        "action": "confirm",
        "path": "user.name",
    })
    assert result.status == "ok"
    assert result.payload["records"][0]["certainty_label"] == "Đã xác nhận"


def test_memory_panel_returns_friendly_error(tmp_path):
    memory = LivingUserMemoryService(tmp_path)
    result = handle_memory_panel_request(memory, {"action": "explain"})
    assert result.status == "error"
    assert "Thiếu đường dẫn" in result.message


def test_memory_panel_payload_stays_within_udp_budget(tmp_path):
    import json

    memory = LivingUserMemoryService(tmp_path)
    for index in range(100):
        memory.correct(
            f"preferences.synthetic_{index}",
            "x" * 900,
            evidence_ids=(f"turn_{index}",),
        )
    payload = {
        "type": "memory_panel_ack",
        "memory_panel": build_memory_panel_snapshot(memory),
    }
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) < 63_000
