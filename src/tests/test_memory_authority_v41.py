from __future__ import annotations

from dataclasses import dataclass, field

from memory.event_ledger import EventLedger
from memory.living_user import LivingUserMemoryService
from memory.semantic_memory import SemanticMemoryItem, SemanticMemoryStore
from runtime.living_user_panel import build_memory_panel_snapshot


@dataclass
class _SemanticItem:
    id: str = "sem_old_name"
    fact_key: str = "user.name"
    semantic_value: str = "Minh"
    evidence_ids: list[str] = field(default_factory=lambda: ["turn_old_name"])
    confidence: float = 0.98
    status: str = "active"
    metadata: dict = field(default_factory=lambda: {"cardinality": "single"})


class _ReloadingSemanticStore:
    """Store giả luôn trả dữ liệu cũ để mô phỏng sync sau restart."""

    def load(self):
        return [_SemanticItem()]


def test_user_correction_survives_semantic_resync(tmp_path):
    service = LivingUserMemoryService(tmp_path, semantic_store=_ReloadingSemanticStore())
    assert service.sync_semantic_memory().changed
    assert service.get_memory("user.name").value == "Minh"

    service.correct("user.name", "Khang", evidence_ids=("panel_fix",))
    service.sync_semantic_memory()

    assert service.get_memory("user.name").value == "Khang"
    assert any("conflicts_with_user_correction:user.name" in item for item in service.sync_semantic_memory().rejected)


def test_hard_forget_tombstone_blocks_resurrection_after_restart(tmp_path):
    service = LivingUserMemoryService(tmp_path, semantic_store=_ReloadingSemanticStore())
    service.sync_semantic_memory()
    assert service.get_memory("user.name") is not None

    service.forget("user.name", hard=True)
    assert service.get_memory("user.name") is None

    restarted = LivingUserMemoryService(tmp_path, semantic_store=_ReloadingSemanticStore())
    restarted.sync_semantic_memory()
    assert restarted.get_memory("user.name") is None
    assert build_memory_panel_snapshot(restarted)["records"] == []


def test_real_semantic_projection_is_corrected_and_hard_deleted(tmp_path):
    semantic = SemanticMemoryStore(str(tmp_path / "semantic.json"))
    semantic.save([
        SemanticMemoryItem(
            id="sem_old",
            claim="Tên người dùng là Minh",
            domain="user",
            confidence=0.98,
            evidence_ids=["turn_old"],
            status="active",
            metadata={"fact_key": "user.name", "value": "Minh", "cardinality": "single"},
        )
    ])
    service = LivingUserMemoryService(tmp_path / "living", semantic_store=semantic)
    service.sync_semantic_memory()

    service.correct("user.name", "Khang", evidence_ids=("panel_fix",))
    active = [item for item in semantic.load() if item.fact_key == "user.name" and item.status == "active"]
    assert len(active) == 1
    assert active[0].semantic_value == "Khang"
    assert active[0].source == "user_authority"

    service.forget("user.name", hard=True)
    assert all(item.fact_key != "user.name" for item in semantic.load())


def test_hard_forget_scrubs_event_evidence(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.json"))
    ledger.append_raw_event(
        frame_id="f1",
        source="chat",
        content="Tên tôi là Minh",
        evidence_ids=("turn_name",),
    )
    service = LivingUserMemoryService(tmp_path / "living", event_ledger=ledger)
    service.correct("user.name", "Minh", evidence_ids=("turn_name",))
    service.forget("user.name", hard=True)
    assert ledger.load() == []


def test_panel_protocol_does_not_bypass_service_boundary():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "runtime" / "living_user_panel.py").read_text(encoding="utf-8")
    assert "living_user.profile_store" not in source
    assert "living_user.action_store" not in source
    assert '"authority_mode": "deepseek_canonical_projection"' in source


def test_hard_forget_scrubs_authority_values_but_keeps_tombstone(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.correct("user.name", "Khang", evidence_ids=("turn_name",))
    service.forget("user.name", hard=True)

    payload = (tmp_path / "living_user_authority.json").read_text(encoding="utf-8")
    assert "Khang" not in payload
    assert "turn_name" not in payload
    assert '"action": "forget"' in payload
    assert '"path": "user.name"' in payload
