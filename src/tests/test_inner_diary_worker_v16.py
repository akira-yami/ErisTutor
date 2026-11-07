from __future__ import annotations

import logging

from cortex.private_mind.inner_diary_renderer import InnerDiaryRenderer
from llm.process_manager import RemoteModelProcessManager
from llm.service import LlmService


def test_llm_service_keeps_a_distinct_diary_owner():
    companion = object()
    inner = object()
    diary = object()

    service = LlmService(companion, inner_client=inner, diary_client=diary)

    assert service.inner_client is inner
    assert service.diary_client is diary
    assert service.diary_client is not service.inner_client


def test_default_diary_owner_reuses_the_existing_inner_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://127.0.0.1:6011/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ERIS_DS_INNER_API_KEY", "inner-gateway-key")
    monkeypatch.delenv("ERIS_INNER_DIARY_WORKER_ROLE", raising=False)
    manager = RemoteModelProcessManager(
        logger=logging.getLogger("test.inner-diary.same-worker"),
        project_root=str(tmp_path),
    )

    inner = manager.build_inner_client()
    diary = manager.build_diary_client()

    assert diary is inner
    assert diary.worker_role == "inner"


def test_process_manager_selects_one_existing_worker_role(monkeypatch, tmp_path):
    manager = RemoteModelProcessManager(
        logger=logging.getLogger("test.inner-diary"),
        project_root=str(tmp_path),
    )
    selected: list[str] = []
    expected = object()

    def _build(role: str):
        selected.append(role)
        return expected

    monkeypatch.setenv("ERIS_INNER_DIARY_WORKER_ROLE", "critic")
    monkeypatch.setattr(manager, "build_deepseek_worker_client", _build)

    assert manager.build_diary_client() is expected
    assert selected == ["critic"]


def test_invalid_diary_role_falls_back_to_inner(monkeypatch, tmp_path):
    manager = RemoteModelProcessManager(
        logger=logging.getLogger("test.inner-diary.invalid"),
        project_root=str(tmp_path),
    )
    selected: list[str] = []

    monkeypatch.setenv("ERIS_INNER_DIARY_WORKER_ROLE", "imaginary_worker")
    monkeypatch.setattr(manager, "build_deepseek_worker_client", lambda role: selected.append(role) or object())

    manager.build_diary_client()

    assert selected == ["inner"]


def test_renderer_memory_echo_keeps_real_provenance_and_writer():
    renderer = InnerDiaryRenderer()
    digest = {
        "menu": {
            "current_thought": {"dominant_judgment": "Tôi đang suy nghĩ."},
            "evidence_dossier": {
                "memory_hits": [
                    {
                        "text": "Bạn từng chọn ở lại khi cuộc trò chuyện khó khăn.",
                        "evidence_id": "memory-42",
                        "source": "living_user_memory",
                    }
                ]
            },
        }
    }
    voice = {
        "hien_tai": "Tôi đang nghĩ về điều ấy.",
        "memory_echo": "Tôi nhớ lần bạn đã chọn ở lại.",
        "_writer": {
            "owner": "deepseek_worker",
            "role": "inner",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "configured": True,
        },
    }

    rendered = renderer.render(digest, voice)

    assert rendered["memory_echo"] == {
        "text": "Tôi nhớ lần bạn đã chọn ở lại.",
        "evidence_id": "memory-42",
        "source": "living_user_memory",
        "authored": True,
    }
    assert rendered["writer"]["role"] == "inner"
