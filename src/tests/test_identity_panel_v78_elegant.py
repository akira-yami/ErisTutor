from __future__ import annotations

from pathlib import Path

from runtime.ai_orchestrated.contracts import RuntimeMindState
from runtime.ai_orchestrated.panel_adapter import build_ai_panel_payload


def test_identity_panel_v78_contract_carries_only_needed_new_authorities() -> None:
    horizon = {
        "schema": "eris.life_horizon.v74",
        "revision": 7,
        "goals": [{"goal_id": "g", "level": "H4", "title": "Grow", "status": "active"}],
    }
    individual = {
        "schema": "eris.persistent_individual.v77",
        "revision": 11,
        "operational_self_model": {"silence_tolerance": 0.7},
        "continuous_self": {"continuity_strength": 0.8},
    }

    runtime = build_ai_panel_payload(
        RuntimeMindState(),
        life_horizon=horizon,
        persistent_individual=individual,
    )["identity_runtime"]

    assert runtime["schema"] == "eris.identity_runtime.panel.v2"
    assert runtime["life_horizon"] == horizon
    assert runtime["individual_runtime"] == individual


def test_identity_panel_v78_main_navigation_does_not_duplicate_other_panels() -> None:
    body = Path(__file__).parents[1] / "body" / "UdpListener.DepthPanel.cs"
    text = body.read_text(encoding="utf-8")

    # Màn Identity mặc định chỉ còn ba nghề: overview, identity, development.
    assert 'AddDepthTab(tabs, "overview", "Tổng quan"' in text
    assert 'AddDepthTab(tabs, "identity", "Bản sắc"' in text
    assert 'AddDepthTab(tabs, "development", "Phát triển"' in text
    assert 'AddDepthTab(tabs, "relationship"' not in text
    assert 'AddDepthTab(tabs, "discovery"' not in text
    assert 'AddDepthTab(tabs, "audit"' not in text

    # Runtime path mới không gọi các tab legacy vốn giẫm chức năng panel khác.
    switch_region = text[text.index("switch (_depthActiveTab)") : text.index("Callable.From(UpdateDepthScrollDecor)", text.index("switch (_depthActiveTab)"))]
    assert "RenderElegantOverviewTab" in switch_region
    assert "RenderElegantIdentityTab" in switch_region
    assert "RenderElegantDevelopmentTab" in switch_region
    assert "RenderRelationshipTab" not in switch_region
    assert "RenderDiscoveryTab" not in switch_region
    assert "RenderAuditTab" not in switch_region

    # Footer chính không còn reset mood/relationship/discovery vì panel chuyên trách đã sở hữu chúng.
    build_region = text[text.index("private void BuildDepthPanelUi") : text.index("private void BuildDepthScrollDecoration")]
    assert 'NewFooterButton("Reset mood"' not in build_region
    assert 'NewFooterButton("Reset quan hệ"' not in build_region
    assert 'NewFooterButton("Reset discovery"' not in build_region
