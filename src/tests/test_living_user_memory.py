from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from memory.event_ledger import EventLedger
from memory.living_user import (
    LivingUserMemoryService,
    LivingUserModelProjection,
    MemorySourceTracker,
    ProfileOperation,
    UserMemoryScope,
    UserProfileStore,
)
from memory.memory_cognition import MemoryCognitionCore
from memory.memory_kernel import MemoryKernel


def op(path, value, evidence, **kwargs):
    return ProfileOperation(
        op=kwargs.pop("op", "reinforce"),
        path=path,
        value=value,
        confidence=kwargs.pop("confidence", 0.9),
        explicitness=kwargs.pop("explicitness", "explicit"),
        evidence_ids=(evidence,),
        source=kwargs.pop("source", "test"),
        reason="test",
        **kwargs,
    )


def test_single_value_update_keeps_superseded_history(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    first = store.apply((op("user.name", "Minh", "turn_1"),))
    second = store.apply((op("user.name", "Khang", "turn_2", op="update"),))
    assert first.changed and second.changed
    all_records = store.get("user.name", include_inactive=True)
    assert {record.value for record in all_records} == {"Minh", "Khang"}
    assert next(record for record in all_records if record.value == "Minh").status == "superseded"
    assert next(record for record in all_records if record.value == "Khang").status == "active"


def test_reinforcement_is_idempotent_for_same_evidence(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    assert store.apply((op("workflow.delivery_preference", "file hoàn chỉnh", "turn_1"),)).changed
    duplicate = store.apply((op("workflow.delivery_preference", "file hoàn chỉnh", "turn_1"),))
    assert not duplicate.changed
    assert any("duplicate_evidence" in reason for reason in duplicate.rejected)
    record = store.get("workflow.delivery_preference")[0]
    assert record.recurrence == 1


def test_multi_value_profile_keeps_distinct_preferences(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    store.apply((
        op("communication.response_rules", "phân tích kỹ", "turn_1", cardinality="multi"),
        op("communication.response_rules", "không nói máy móc", "turn_2", cardinality="multi"),
    ))
    values = {record.value for record in store.get("communication.response_rules")}
    assert values == {"phân tích kỹ", "không nói máy móc"}


def test_service_observes_model_delivery_and_comment_preferences(tmp_path):
    adapter = SimpleNamespace(propose_profile_operations=lambda payload: {"operations": [
        {"op":"add", "path":"workflow.delivery_preference", "value":"file hoàn chỉnh", "confidence":0.9, "explicitness":"explicit"},
        {"op":"add", "path":"technical.code_comment_language", "value":"vi", "confidence":0.9, "explicitness":"explicit"},
    ]})
    service = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    result = service.observe_turn(
        "Làm bản lớn rồi gửi file cho tôi, có chú thích tiếng Việt.",
        evidence_ids=("turn_1",),
    )
    assert result.applied >= 2
    assert service.profile_store.get("workflow.delivery_preference")
    assert service.profile_store.get("technical.code_comment_language")[0].value == "vi"


def test_semantic_sync_accepts_unknown_user_fact_key(tmp_path):
    @dataclass
    class Item:
        id: str = "sem_1"
        fact_key: str = "user.favorite_weather"
        semantic_value: str = "trời mưa"
        evidence_ids: list[str] = field(default_factory=lambda: ["turn_1"])
        confidence: float = 0.93
        status: str = "active"
        metadata: dict = field(default_factory=lambda: {"cardinality": "multi"})

    class Store:
        def load(self):
            return [Item()]

    service = LivingUserMemoryService(tmp_path, semantic_store=Store())
    result = service.sync_semantic_memory()
    assert result.changed
    record = service.profile_store.get("user.favorite_weather")[0]
    assert record.value == "trời mưa"
    assert record.cardinality == "multi"


def test_context_compiler_uses_project_scope_and_domain(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        op("communication.preferred_language", "vi", "turn_1"),
        op(
            "technical.architecture_preferences",
            "open schema và dependency injection",
            "turn_2",
            scope=UserMemoryScope("project", "eris"),
        ),
        op(
            "technical.architecture_preferences",
            "monolith đơn giản",
            "turn_3",
            scope=UserMemoryScope("project", "other"),
        ),
    ))
    packet = service.compile_context("refactor memory architecture", project_id="eris")
    values = {item.value for item in packet.items}
    assert "open schema và dependency injection" in values
    assert "monolith đơn giản" not in values
    assert "vi" in values
    assert packet.domain == "technical"


def test_episode_buffer_builds_summary_and_dreams_preferences(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.record_conversation_turn("user", "Memory này còn vá tay, làm elegant hơn.", session_id="s1", turn_id="u1")
    service.record_conversation_turn("assistant", "Tôi sẽ refactor kiến trúc.", session_id="s1", turn_id="a1")
    service.record_conversation_turn("user", "Làm đi rồi gửi file hoàn chỉnh có chú thích.", session_id="s1", turn_id="u2")
    service.record_conversation_turn("assistant", "Đã đóng gói source.", session_id="s1", turn_id="a2")
    episode = service.flush_session(run_dream=True)
    assert episode is not None
    assert episode.evidence_ids
    assert service.episode_store.recent(limit=1)[0].session_id == "s1"
    assert service.profile_store.get("workflow.delivery_preference")


def test_correction_and_forgetting_are_user_controllable(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.correct("user.name", "Minh", evidence_ids=("turn_1",))
    service.correct("user.name", "Khang", evidence_ids=("turn_2",))
    assert service.profile_store.get("user.name")[0].value == "Khang"
    changed = service.forget("user.name")
    assert changed >= 1
    assert not service.profile_store.get("user.name")


def test_source_tracker_explains_profile_evidence(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.json"))
    event = ledger.append_raw_event(
        frame_id="f1",
        source="chat",
        content="Tôi thích code có chú thích tiếng Việt",
        evidence_ids=("turn_1",),
    )
    service = LivingUserMemoryService(tmp_path / "living", event_ledger=ledger)
    service.profile_store.apply((op("technical.code_comment_language", "vi", "turn_1"),))
    explanation = service.explain("technical.code_comment_language")[0]
    assert explanation.note
    assert explanation.sources[0].source_type == "event_ledger"
    assert "chú thích" in explanation.sources[0].excerpt


def test_legacy_projection_writes_into_canonical_profile(tmp_path):
    @dataclass
    class Fragment:
        claim: str = "Người dùng không thích wording máy móc"
        evidence_ids: tuple[str, ...] = ("turn_1",)
        confidence: float = 0.88
        source: str = "user_model_evaluator"
        metadata: dict = field(default_factory=lambda: {"facet": "interaction_style"})

    service = LivingUserMemoryService(tmp_path)
    projection = LivingUserModelProjection(service)
    updated = projection.update_from_fragments((Fragment(),))
    assert updated
    assert service.profile_store.get("relationship.private_mind.interaction_style")
    assert projection.snapshot_for_dossier()["belief_count"] >= 1


def test_memory_kernel_merges_living_profile_into_prompt(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)
    core.living_user.profile_store.apply((
        op("technical.architecture_preferences", "open schema", "turn_1"),
    ))
    context = kernel.render_context("refactor architecture")
    assert "USER CONTEXT" in context
    assert "open schema" in context


def test_profile_persists_round_trip(tmp_path):
    path = tmp_path / "profile.json"
    first = UserProfileStore(path)
    first.apply((op("communication.preferred_language", "vi", "turn_1"),))
    second = UserProfileStore(path)
    record = second.get("communication.preferred_language")[0]
    assert record.value == "vi"
    assert record.evidence_ids == ("turn_1",)


def test_inference_cannot_overwrite_explicit_fact(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    store.apply((op("user.name", "Khang", "turn_1", explicitness="explicit", confidence=0.95),))
    result = store.apply((op("user.name", "Minh", "turn_2", explicitness="inferred", confidence=0.99),))
    assert result.changed
    assert store.get("user.name")[0].value == "Khang"
    disputed = [r for r in store.get("user.name", include_inactive=True) if r.status == "disputed"]
    assert disputed and disputed[0].value == "Minh"


def test_secret_paths_are_rejected(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    result = store.apply((op("user.api_key", "do-not-store", "turn_1"),))
    assert not result.changed
    assert any("forbidden_secret_path" in reason for reason in result.rejected)


def test_hard_forget_scrubs_profile_history(tmp_path):
    path = tmp_path / "profile.json"
    store = UserProfileStore(path)
    store.apply((op("user.name", "Khang", "turn_1"),))
    assert store.forget("user.name", hard=True) == 1
    reloaded = UserProfileStore(path)
    assert not reloaded.get("user.name", include_inactive=True)
    payload = path.read_text(encoding="utf-8")
    assert "Khang" not in payload


def test_readable_memory_summary_separates_explicit_and_observed(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        op("user.name", "Khang", "turn_1", explicitness="explicit"),
        op(
            "workflow.delivery_preference",
            "file hoàn chỉnh",
            "turn_2",
            explicitness="observed",
        ),
    ))
    summary = service.render_summary()
    assert "Tên: Khang" in summary
    assert "Qua các lần tương tác" in summary
    assert "cách muốn nhận kết quả" in summary.lower()
    assert "semantic" not in summary.lower()
    assert "fact" not in summary.lower()


def test_memory_summary_question_injects_safe_readable_summary(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)
    core.living_user.profile_store.apply((
        op("user.name", "Khang", "turn_1", explicitness="explicit"),
        op("relationship.private.note", "không được lộ", "turn_2", sensitivity="private"),
    ))
    context = kernel.render_context("bạn nhớ gì về tôi?")
    assert "MEMORY SUMMARY REQUEST" in context
    assert "Tên: Khang" in context
    assert "không được lộ" not in context


def test_llm_adapter_parses_structured_profile_patch():
    from memory.living_user import LlmServiceUserProfileAdapter

    class Client:
        def create_chat_completion(self, **kwargs):
            assert kwargs["response_format"] == {"type": "json_object"}
            return {
                "choices": [{
                    "message": {
                        "content": '{"operations":[{"op":"add","path":"preferences.favorite_weather","value":"trời mưa","confidence":0.9,"explicitness":"explicit","evidence_ids":["turn_1"]}]}'
                    }
                }]
            }

    class Service:
        client = Client()

    result = LlmServiceUserProfileAdapter(Service()).propose_profile_operations({"user_text": "tui thích trời mưa"})
    assert result is not None
    assert result["operations"][0]["path"] == "preferences.favorite_weather"


def test_llm_adapter_can_target_explicit_curator_client():
    from memory.living_user import LlmServiceUserProfileAdapter

    class Client:
        def __init__(self, value):
            self.value = value
            self.calls = 0

        def create_chat_completion(self, **kwargs):
            self.calls += 1
            return {"choices": [{"message": {"content": self.value}}]}

    companion = Client('{"operations":[]}')
    curator = Client('{"operations":[{"op":"add","path":"communication.response_rules","value":"ngắn","confidence":0.9,"explicitness":"explicit","evidence_ids":["turn_1"]}]}')

    class Service:
        client = companion

    adapter = LlmServiceUserProfileAdapter(Service(), client=curator)
    result = adapter.propose_profile_operations({"user_text": "trả lời ngắn", "evidence_ids": ["turn_1"]})

    assert result is not None
    assert result["operations"][0]["path"] == "communication.response_rules"
    assert curator.calls == 1
    assert companion.calls == 0


def test_memory_kernel_fast_path_never_observes_living_user(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)
    calls = []

    class Adapter:
        def propose_profile_operations(self, payload):
            calls.append(payload)
            return {"operations": []}

        def summarize_episode(self, payload):
            return None

    core.living_user.bind_model_adapter(Adapter())
    result = kernel.ingest(
        "tôi thích trời mưa",
        evidence_ids=["turn_1"],
        turn_id="turn_1",
        mode="fast",
    )

    assert calls == []
    assert result.debug["living_user"]["deferred"] is True
    assert result.debug["living_user"]["reason"] == "fast_path_background_curator"


def test_memory_kernel_semantic_digest_can_delegate_living_user_to_curator_lane(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)
    calls = []

    class Adapter:
        def propose_profile_operations(self, payload):
            calls.append(payload)
            return {"operations": []}

        def summarize_episode(self, payload):
            return None

    core.living_user.bind_model_adapter(Adapter())
    result = kernel.ingest(
        "tôi thích trời mưa",
        evidence_ids=["turn_1"],
        turn_id="turn_1",
        mode="semantic_digest",
        context={"skip_living_user_observe": True},
    )

    assert calls == []
    assert result.debug["living_user"]["reason"] == "dedicated_curator_lane"
