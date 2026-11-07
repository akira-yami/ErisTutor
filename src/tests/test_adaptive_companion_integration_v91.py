from __future__ import annotations

from pathlib import Path

from runtime.adaptive_companion import AdaptiveCompanionService, ensure_adaptive_companion_service


class _Owner:
    def __init__(self):
        self.engineering_mentor_service = None
        self.life_horizon_manager = None
        self.logger = None


def test_v91_ensure_companion_is_owner_scoped_singleton(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ERIS_ADAPTIVE_COMPANION_STATE_PATH", str(tmp_path / "companion.json"))
    owner = _Owner()

    first = ensure_adaptive_companion_service(owner)
    second = ensure_adaptive_companion_service(owner)

    assert first is second
    assert owner.adaptive_companion_service is first


def test_v91_prompt_context_is_small_behavior_directive_not_transcript_dump(tmp_path: Path) -> None:
    service = AdaptiveCompanionService(owner=_Owner(), state_path=tmp_path / "companion.json")

    block = service.prompt_context(
        "Tôi đang học C# và muốn hiểu dependency injection kỹ hơn.",
        turn_id="t1",
        now=1000.0,
    )

    assert block.startswith("ADAPTIVE_COMPANION_V91:\n")
    assert '"stance":"teach_guided"' in block
    assert '"teasing_is_authority":false' in block
    assert len(block) < 8000


def test_v91_orchestrator_injects_companion_context_without_eris_code_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "runtime" / "ai_orchestrated" / "orchestrator.py").read_text(encoding="utf-8")

    assert "ensure_adaptive_companion_service" in source
    assert "adaptive_companion.prompt_context" in source
    assert "ADAPTIVE_COMPANION_V91" not in source  # prefix owned by service, orchestration stays generic
    assert "eris_code_observer_link" not in source


def test_v91_engineering_mentor_is_subordinate_to_companion_delivery_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "runtime" / "engineering_mentor.py").read_text(encoding="utf-8")

    assert "ADAPTIVE_COMPANION_V91" in source
    assert "technical_evidence_provider" in source
    assert "delivery_owner" in source
    assert "No coding-extension telemetry is required" in source


def test_v91_companion_context_is_placed_before_other_discourse_for_budget_priority() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "runtime" / "ai_orchestrated" / "orchestrator.py").read_text(encoding="utf-8")
    marker = 'part for part in (\n                discourse_context,\n                handoff_runtime.prompt_context(),\n                life_horizon_manager.prompt_context(now=cycle_start),\n                companion_context,'
    assert marker not in source
    preferred = 'part for part in (\n                companion_context,\n                discourse_context,\n                handoff_runtime.prompt_context(),'
    assert preferred in source
