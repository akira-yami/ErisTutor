# -*- coding: utf-8 -*-
"""Kiểm tra tĩnh và hợp đồng cho Inner Diary (Private Mind panel) trong Architecture C."""
from pathlib import Path
from types import SimpleNamespace
from runtime.ai_orchestrated.contracts import RuntimeMindState
from runtime.ai_orchestrated.panel_adapter import build_ai_panel_payload
from runtime.gemini_inner_diary import GeminiInnerDiaryService, get_gemini_inner_diary_service
from transport.udp import _compact_private_mind_for_udp_overflow, _slim_private_mind_core

SRC = Path(__file__).parents[1]
UDP_LISTENER = SRC / "body" / "UdpListener.cs"
IDENTITY_PRIVATE_MIND = SRC / "body" / "UdpListener.IdentityPrivateMind.cs"


def test_inner_diary_csharp_wires_modern_identity_digest() -> None:
    udp_source = UDP_LISTENER.read_text(encoding="utf-8")
    identity_source = IDENTITY_PRIVATE_MIND.read_text(encoding="utf-8")

    # Gate check must accept modern identity digest
    assert "if (IsModernIdentityDigest(root))" in udp_source
    assert "return ModernIdentityDigestHasContent(root);" in udp_source

    # Writer summary must identify modern architecture or Gemini
    assert 'return $"Gemini ({model}) · Nhật ký Eris";' in udp_source
    assert 'return $"Identity Kernel · {arch}";' in udp_source

    # RefreshPrivateMindPanel must call modern builders
    assert "slots = BuildModernIdentityDiarySlots(root);" in udp_source
    assert "RefreshModernIdentityConcernStack(root);" in udp_source
    assert "RefreshModernIdentityMemoryEcho(root);" in udp_source
    assert "_privateMindText.Text = RenderModernIdentityDigest(root);" in udp_source

    # IdentityPrivateMind must define those methods
    assert "IsModernIdentityDigest(JsonElement root)" in identity_source
    assert "ModernIdentityDigestHasContent(JsonElement root)" in identity_source
    assert "BuildModernIdentityDiarySlots(JsonElement root)" in identity_source
    assert "RefreshModernIdentityConcernStack(JsonElement root)" in identity_source
    assert "RefreshModernIdentityMemoryEcho(JsonElement root)" in identity_source
    assert "RenderModernIdentityDigest(JsonElement root)" in identity_source


def test_inner_diary_payload_contains_expected_modern_fields() -> None:
    state = RuntimeMindState(
        open_loops=["Nhớ nhắc user kiểm tra panel"],
    )
    payload = build_ai_panel_payload(state)
    assert "private_mind_digest" in payload
    digest = payload["private_mind_digest"]

    assert digest["schema"] == "eris.inner_state.panel.v1"
    assert digest["architecture"].startswith("ai_orchestrated.")
    assert "open_loops" in digest
    assert digest["open_loops"] == ["Nhớ nhắc user kiểm tra panel"]
    assert "active_motives" in digest
    assert "active_conflict" in digest
    assert "activated_defenses" in digest
    assert "latest_intention" in digest
    assert "self_interpretation" in digest
    assert "discovery" in digest

    # Verify inner_diary is attached
    assert "inner_diary" in digest
    inner_diary = digest["inner_diary"]
    assert "sections" in inner_diary
    assert len(inner_diary["sections"]) >= 5
    assert "text" in inner_diary

    # Check compaction preserves modern keys including inner_diary
    compacted = _compact_private_mind_for_udp_overflow(digest)
    assert compacted["schema"] == "eris.inner_state.panel.v1"
    assert "active_motives" in compacted
    assert "open_loops" in compacted
    assert "inner_diary" in compacted

    slimmed = _slim_private_mind_core(digest)
    assert slimmed["schema"] == "eris.inner_state.panel.v1"
    assert "active_motives" in slimmed
    assert "inner_diary" in slimmed


def test_gemini_inner_diary_service_persists_to_disk_and_gates_on_tension(tmp_path: Path) -> None:
    svc = GeminiInnerDiaryService(base_dir=tmp_path, filename="test_diary.json")
    disk_file = tmp_path / "test_diary.json"
    assert disk_file.exists()

    # Peaceful state: should not regenerate
    peaceful_state = SimpleNamespace(
        identity={"active_conflict": {"residue": 0.05, "activation": 0.1}},
        open_loops=[],
    )
    is_tense, score, reason = svc.evaluate_tension(state=peaceful_state)
    assert not is_tense
    assert score < 0.35
    assert not svc.maybe_trigger_generation(state=peaceful_state)

    # High tension state: triggers regeneration
    tense_state = SimpleNamespace(
        identity={
            "active_conflict": {"conflict_id": "test_crisis", "residue": 0.88, "activation": 0.9},
            "active_motives": [{"key": "autonomy", "strength": 0.8}],
            "active_defenses": [{"key": "suppression", "strength": 0.7}],
        },
        open_loops=["critical loop"],
    )
    is_tense, score, reason = svc.evaluate_tension(
        state=tense_state,
        observation={"repair_needed": True},
    )
    assert is_tense
    assert score >= 0.35
    assert "conflict(test_crisis" in reason

    # Loading from disk gives full 5 sections
    diary = svc.get_current_diary()
    assert len(diary["sections"]) == 5
    section_titles = [s["title"] for s in diary["sections"]]
    assert "Suy nghĩ hiện tại" in section_titles
    assert "Suy nghĩ tương lai" in section_titles
    assert "Đang bận tâm" in section_titles
    assert "Phân tích gần đây" in section_titles
    assert "Muốn nói" in section_titles
