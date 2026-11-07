from __future__ import annotations

from pathlib import Path

import runtime.learning_core as learning_core


def test_learning_core_documents_deeptutor_notice_and_agent_boundary():
    root = Path(__file__).resolve().parents[1]
    notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    boundary = (root / "runtime" / "learning_core" / "BOUNDARY_VI.md").read_text(encoding="utf-8")

    assert "HKUDS/DeepTutor" in notice
    assert "ef2d9e5c" in notice
    assert "Apache License 2.0" in notice
    assert "TurnAgentLoop" in boundary
    assert "LLM" in boundary and "kernel" in boundary


def test_learning_core_package_does_not_import_public_writer_or_tts():
    """Learning kernel phai la substrate; khong duoc leo len so huu conversation."""
    root = Path(__file__).resolve().parents[1] / "runtime" / "learning_core"
    forbidden = (
        "public_writer",
        "speech_localizer",
        "text_to_speech",
        "ConversationDirector",
        "TurnAgentRuntime",
    )

    offenders: list[str] = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            # __init__/service docstring co the *nhac den* boundary bang ten; chi
            # cam import/usage code, khong cam documentation giai thich kien truc.
            if f"import {token}" in text or f"from {token}" in text:
                offenders.append(f"{path.name}:{token}")
    assert offenders == []


def test_learning_core_public_api_still_exposes_deterministic_service_not_agent_loop():
    assert hasattr(learning_core, "LearningService")
    assert hasattr(learning_core, "compute_mastery")
    assert not hasattr(learning_core, "TutorExecutive")
    assert not hasattr(learning_core, "TurnAgentRuntime")
