from __future__ import annotations

import json

from memory.living_user import LivingUserMemoryService, ProfileOperation, UserMemoryScope
from memory.living_user.curator import LivingUserCuratorConfig, LivingUserMemoryCurator
from runtime.memory_gc_service import MemoryGCService


def _op(path: str, value, evidence: str, *, source: str = "profile_model", cardinality: str = "single"):
    return ProfileOperation(
        op="add",
        path=path,
        value=value,
        confidence=0.90,
        explicitness="explicit",
        scope=UserMemoryScope(),
        cardinality=cardinality,
        evidence_ids=(evidence,),
        reason="test",
        source=source,
    )


class _FakeEmbedding:
    """Vector giả để test luồng gom cụm mà không dựng Qwen thật trong CI."""

    def embed_documents(self, texts):
        rows = []
        for text in texts:
            if "interaction_style" in text:
                rows.append((1.0, 0.0, 0.0))
            else:
                rows.append((0.0, 1.0, 0.0))
        return rows


def test_deterministic_curator_archives_null_and_resolved_open_loop(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        _op("preferences.chess_sharing", None, "turn_1"),
        _op("open_loops.file_creation", "Việc tạo file đã hoàn tất.", "turn_2"),
    ))

    curator = LivingUserMemoryCurator(
        service,
        reviewer=None,
        config=LivingUserCuratorConfig(history_snapshots=4),
    )
    report = curator.run()

    assert report.archived == 1
    assert report.deleted == 1
    assert not service.profile_store.get("preferences.chess_sharing")
    assert not service.profile_store.get("open_loops.file_creation")


def test_curator_can_restructure_user_authority_record(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.correct("user.name", "Khang", evidence_ids=("turn_1",))
    record = service.profile_store.get("user.name")[0]

    def reviewer(system: str, user: str, max_tokens: int):
        return json.dumps({
            "operations": [
                {
                    "action": "canonicalize",
                    "record_ids": [record.id],
                    "path": "identity.name",
                    "value": "Khang",
                    "reason": "normalize path while preserving user-authority meaning",
                }
            ]
        })

    curator = LivingUserMemoryCurator(service, reviewer=reviewer)
    report = curator.run()

    rows = service.profile_store.get("identity.name")
    assert report.canonicalized == 1
    assert rows and rows[0].value == "Khang"
    assert "turn_1" in rows[0].evidence_ids


def test_qwen_cluster_then_deepseek_merge_same_path(tmp_path):
    service = LivingUserMemoryService(tmp_path, embedding_index=_FakeEmbedding())
    service.profile_store.apply((
        _op(
            "preferences.interaction_style",
            "Không thích câu trả lời máy móc",
            "turn_1",
            cardinality="multi",
        ),
        _op(
            "preferences.interaction_style",
            "Ưu tiên phản hồi tự nhiên, tránh máy móc",
            "turn_2",
            cardinality="multi",
        ),
    ))
    records = service.profile_store.get("preferences.interaction_style")
    ids = [record.id for record in records]

    def reviewer(system: str, user: str, max_tokens: int):
        assert "cluster_" in user
        return json.dumps({
            "operations": [
                {
                    "action": "merge",
                    "record_ids": ids,
                    "reason": "Hai record cùng preference và cùng scope",
                }
            ]
        })

    curator = LivingUserMemoryCurator(
        service,
        reviewer=reviewer,
        embedding_index=service.embedding_index,
        config=LivingUserCuratorConfig(duplicate_cosine=0.85),
    )
    report = curator.run()

    assert report.merged == 1
    assert len(service.profile_store.get("preferences.interaction_style")) == 1


def test_profile_history_is_compacted(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    for index in range(12):
        service.profile_store.apply((
            _op(f"preferences.item_{index}", f"value {index}", f"turn_{index}"),
        ))
    assert len(service.profile_store._history) >= 10

    curator = LivingUserMemoryCurator(
        service,
        reviewer=None,
        config=LivingUserCuratorConfig(history_snapshots=4),
    )
    report = curator.run()

    assert report.history_removed >= 6
    assert len(service.profile_store._history) == 4


class _FakeProcessManager:
    def __init__(self, critic):
        self.critic = critic
        self.roles = []

    def build_deepseek_worker_client(self, role: str, required: bool = False):
        self.roles.append(role)
        return self.critic if role == "critic" else None


class _FakeLLMService:
    def __init__(self, critic):
        self._process_manager = _FakeProcessManager(critic)


class _FakeCritic:
    def create_chat_completion(self, **kwargs):
        return {"choices": [{"message": {"content": '{"decisions": []}'}}]}


def test_gc_uses_gemini_curator_and_defaults_to_ten_minutes(tmp_path, monkeypatch):
    monkeypatch.delenv("ERIS_MEMORY_GC_INTERVAL_SEC", raising=False)
    critic = _FakeCritic()
    llm = _FakeLLMService(critic)
    core = type("Core", (), {"semantic": None, "living_user": None})()
    service = MemoryGCService(memory_core=core, llm_service=llm)

    assert service.interval_seconds == 600
    from memory.living_user.gemini_adapter import GeminiWebUserProfileAdapter
    monkeypatch.setenv("ERIS_MEMORY_GEMINI_ENABLED", "1")
    assert isinstance(service._get_curator_client(), GeminiWebUserProfileAdapter)
    assert llm._process_manager.roles == []
