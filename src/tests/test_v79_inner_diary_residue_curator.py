from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from cortex.private_mind.inner_diary_renderer import InnerDiaryRenderer
from cortex.private_mind.residue_curator import DiaryResidueStore, ResidueStoreConfig
from llm.process_manager import RemoteModelProcessManager
from runtime.private_mind_model_adapter import PrivateMindAdapterPool, PrivateMindModelAdapterConfig
from runtime.runtime_settings_store import RuntimeSettingsStore


def _store(tmp_path: Path) -> DiaryResidueStore:
    return DiaryResidueStore(
        config=ResidueStoreConfig(
            enabled=True,
            path=str(tmp_path / "residue.json"),
            max_items=48,
            max_entries=24,
            batch_threshold=2,
            high_salience=0.75,
            consolidate_interval_seconds=60.0,
            active_seconds=3600.0,
            fading_seconds=7200.0,
        )
    )


def test_unprocessed_stimulus_cannot_be_promoted_to_diary(tmp_path):
    store = _store(tmp_path)
    rid = store.capture_unprocessed_stimulus(
        text="Người dùng vừa đổi cửa sổ sang editor.",
        reason="singleflight_coalesced_overwritten",
        salience=0.9,
    )
    applied = store.apply_curator_output(
        {
            "decisions": [
                {
                    "residue_ids": [rid],
                    "action": "diary",  # curator cố nâng sai provenance
                    "kind": "reflection",
                    "text": "Tôi đã nghĩ về việc người dùng đổi cửa sổ.",
                    "confidence": 0.9,
                    "novelty": 0.8,
                }
            ]
        },
        curator_meta={"configured": True, "model": "gemma-test"},
        now=100.0,
    )
    assert applied == 1
    assert store.diary_entries(now=100.0) == []
    continuity = store.continuity_for_prompt(now=100.0)
    assert continuity
    assert "đổi cửa sổ" in continuity[0]["text"]
    assert "Tôi đã nghĩ" not in continuity[0]["text"]


def test_actual_private_fragment_survives_as_retained_diary(tmp_path):
    store = _store(tmp_path)
    rid = store.capture(
        kind="private_fragment",
        text="Có lẽ lúc này im lặng hợp hơn hỏi thêm.",
        source_ids=("evidence-1",),
        salience=0.82,
        provenance="deepseek_fragment:intent_evaluator",
        now=100.0,
    )
    applied = store.apply_curator_output(
        {
            "decisions": [
                {
                    "residue_ids": [rid],
                    "action": "diary",
                    "kind": "reflection",
                    "text": "Có lẽ lúc này im lặng hợp hơn hỏi thêm.",
                    "confidence": 0.85,
                    "novelty": 0.7,
                }
            ]
        },
        curator_meta={"configured": True, "model": "gemma-test"},
        now=110.0,
    )
    assert applied == 1
    entries = store.diary_entries(now=120.0)
    assert entries[0]["kind"] == "reflection"
    assert entries[0]["state"] == "active"
    assert entries[0]["source_ids"] == ["evidence-1"]

    # Persistence: restart không làm diary biến về trắng.
    reloaded = _store(tmp_path)
    assert reloaded.diary_entries(now=120.0)[0]["text"] == entries[0]["text"]


def test_renderer_uses_retained_residue_when_current_tick_is_empty():
    rendered = InnerDiaryRenderer().render(
        {
            "retained": {
                "diary_entries": [
                    {
                        "kind": "reflection",
                        "text": "Tôi đã muốn hỏi chỉ vì không chắc nên làm gì.",
                        "state": "active",
                    }
                ]
            },
            "diary_residue": {"pending_count": 0, "curator": {"model": "gemma"}},
        }
    )
    section = next(item for item in rendered["sections"] if item["title"] == "Phân tích gần đây")
    assert "không chắc" in section["items"][0]["text"]
    assert rendered["residue"]["retained_count"] == 1


class _FakeResidueClient:
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    model_name = "gemma-4-31b-it"

    def __init__(self):
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        payload = {
            "decisions": [
                {
                    "residue_ids": ["residue:1"],
                    "action": "diary",
                    "kind": "reflection",
                    "text": "Một mảnh đã được gom.",
                    "confidence": 0.8,
                    "novelty": 0.6,
                }
            ]
        }
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


def test_private_mind_pool_does_not_use_retired_residue_client():
    residue = _FakeResidueClient()
    service = SimpleNamespace(
        inner_client=None,
        diary_client=None,
        residue_curator_client=residue,
    )
    config = PrivateMindModelAdapterConfig(
        enabled=True,
        provider="deepseek",
        model="deepseek-v4-flash",
        base_url="http://127.0.0.1:6011/v1",
        api_key="worker-managed",
        transient_max_retries=0,
    )
    pool = PrivateMindAdapterPool(config=config, llm_service=service)
    result = pool.curate_residue(
        {
            "items": [
                {
                    "id": "residue:1",
                    "kind": "private_fragment",
                    "text": "Một mảnh.",
                    "provenance": "deepseek_fragment:test",
                }
            ]
        }
    )
    assert result is None
    assert residue.calls == []
    assert pool.residue_metadata()["configured"] is False


def test_process_manager_does_not_build_retired_residue_curator(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_PRIVATE_RESIDUE_CURATOR_ENABLED", "1")
    monkeypatch.setenv("ERIS_PRIVATE_RESIDUE_CURATOR_API_KEY", "test-key")
    monkeypatch.setenv("ERIS_PRIVATE_RESIDUE_CURATOR_MODEL", "gemma-4-31b-it")
    manager = RemoteModelProcessManager(
        logger=logging.getLogger("test.residue-curator"),
        project_root=str(tmp_path),
    )
    client = manager.build_residue_curator_client()
    assert client is None


def test_bundled_runtime_settings_enable_diary_and_residue():
    path = Path(__file__).resolve().parents[1] / "settings" / "runtime_settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] >= 18
    assert data["architecture"]["inner_diary_model_enabled"] is True
    assert data["architecture"]["private_residue_curator_enabled"] is False


def test_v17_settings_migration_turns_inner_diary_on_once():
    migrated, changed = RuntimeSettingsStore._migrate_payload({
        "version": 17,
        "architecture": {
            "inner_diary_model_enabled": False,
        },
    })
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["architecture"]["inner_diary_model_enabled"] is True
    assert migrated["architecture"]["private_residue_curator_enabled"] is False
