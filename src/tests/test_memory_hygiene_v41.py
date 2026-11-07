from __future__ import annotations

import json
import time
from pathlib import Path

from cortex.private_mind.thought_continuity import ThoughtContinuityStore, ThoughtEntry
from memory.living_user import LivingUserMemoryService
from memory.memory_admission import MemoryAdmissionPolicy
from memory.memory_cue import MemoryCue
from memory.memory_feed_trace import MemoryFeedTraceJournal
from memory.memory_record import MemoryRecord
from memory.memory_retrieval_engine import MemoryRetrievalEngine, MemoryStores
from runtime.behavior_observation import BehaviorObservationJournal, BehaviorSignal
from runtime.living_user_panel import handle_memory_panel_request


class _Store:
    def __init__(self, items):
        self.items = list(items)

    def load(self):
        return list(self.items)


def test_admission_blocks_audit_runtime_even_when_text_matches():
    policy = MemoryAdmissionPolicy()
    record = MemoryRecord(
        id="audit_1",
        layer="semantic",
        text="user likes python architecture",
        confidence=0.99,
        evidence_ids=("turn_1",),
        metadata={"audit_only": True, "source": "executive_commit"},
    )
    decision = policy.decide(record, cue=MemoryCue(query_text="python architecture"))
    assert decision.admitted is False
    assert decision.reason == "audit_runtime_never_prompt"


def test_retrieval_trace_records_admitted_and_rejected_without_memory_text(tmp_path):
    good = MemoryRecord(
        id="sem_good",
        layer="semantic",
        text="user prefers architecture fixes over downstream guards",
        confidence=0.95,
        evidence_ids=("turn_good",),
        metadata={"fact_key": "technical.architecture_preference", "source": "direct_user"},
    )
    junk = MemoryRecord(
        id="sem_junk",
        layer="semantic",
        text="architecture downstream guards",
        confidence=0.99,
        evidence_ids=("evt_debug",),
        metadata={"runtime_only": True, "source": "deliberation_frame"},
    )
    trace = MemoryFeedTraceJournal(tmp_path / "feed.jsonl")
    engine = MemoryRetrievalEngine(MemoryStores(semantic=_Store([good, junk])), feed_trace=trace)
    result = engine.retrieve(MemoryCue(query_text="architecture downstream guards"), max_activations=5)

    assert any(item.record_id == "sem_good" for item in result.activations)
    assert all(item.record_id != "sem_junk" for item in result.activations)
    raw = (tmp_path / "feed.jsonl").read_text(encoding="utf-8")
    assert "sem_good" in raw and "sem_junk" in raw
    assert "user prefers architecture fixes" not in raw
    row = json.loads(raw.splitlines()[-1])
    assert row["retrieved"] == 2
    assert row["rejected"] == 1
    assert "query" not in row
    assert row["query_hash"]


def test_behavior_observation_persistence_strips_public_text_by_default(tmp_path):
    path = tmp_path / "behavior.jsonl"
    journal = BehaviorObservationJournal(path=path, ttl_seconds=3600)
    journal.commit((BehaviorSignal(
        family="unverified_execution_claim",
        source="test",
        public_text="Chuyện lúc trước, bạn vẫn còn để ý không?",
        consequence="trust debt",
        next_turn_hint="verify first",
        turn_id="t1",
    ),))
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert row["public_text"] == ""
    assert row["public_text_hash"]
    assert row["prompt_eligible"] is False
    assert row["source_class"] == "audit_runtime"


def test_thought_stream_compacts_expired_transient_entries(tmp_path):
    path = tmp_path / "thought_stream.jsonl"
    store = ThoughtContinuityStore(path=path, max_age_seconds=7 * 86400)
    store.append(ThoughtEntry(
        ts=time.time() - 2 * 86400,
        text="một thought resolved cũ",
        theme="old",
        status="resolved",
        pressure=0.1,
        confidence=0.2,
    ))
    store.append(ThoughtEntry(
        ts=time.time(),
        text="mạch đang sống",
        theme="live",
        status="open",
        pressure=0.7,
        confidence=0.8,
    ))
    report = store.compact()
    assert report["after"] == 1
    assert store.recent(4)[0].theme == "live"


class _CleanupService:
    def __init__(self):
        self.enabled = False
        self.calls = 0
        self.last_result = {}

    @property
    def auto_cleanup_enabled(self):
        return self.enabled

    def set_auto_cleanup(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled

    def status_snapshot(self):
        return {"available": True, "auto_cleanup": self.enabled, "last_result": self.last_result}

    def run_once(self, *, force=True):
        self.calls += 1

        class _Report:
            def to_dict(self_inner):
                return {
                    "ds_reviewed": 4,
                    "living_reviewed": 3,
                    "ds_dropped": 1,
                    "living_applied": 2,
                    "behavior_removed": 5,
                    "thought_removed": 6,
                }

        report = _Report()
        self.last_result = report.to_dict()
        return report


def test_memory_panel_can_toggle_and_force_deepseek_cleanup(tmp_path):
    living = LivingUserMemoryService(tmp_path / "living")
    cleanup = _CleanupService()

    toggled = handle_memory_panel_request(
        living, {"action": "set_auto_cleanup", "enabled": True}, cleanup_service=cleanup
    )
    assert toggled.status == "ok"
    assert toggled.payload["cleanup"]["auto_cleanup"] is True

    cleaned = handle_memory_panel_request(living, {"action": "cleanup_now"}, cleanup_service=cleanup)
    assert cleaned.status == "ok"
    assert cleanup.calls == 1
    assert cleaned.payload["cleanup_report"]["behavior_removed"] == 5


def test_memory_panel_keeps_character_portrait_separate_from_memory_manager():
    body_source = Path(__file__).parents[1] / "body" / "UdpListener.MemoryPanel.cs"
    if not body_source.exists():
        body_source = Path(__file__).parents[1] / "integration" / "body_contracts" / "UdpListener.MemoryPanel.cs"
    source = body_source.read_text(encoding="utf-8")
    assert "Những chuyện tôi vẫn chưa quên" in source
    assert "ERIS // MEMORY" in source
    assert "HÔM NAY TÔI NGHĨ VỀ BẠN THẾ NÀY" in source
    assert "Quản lý trí nhớ" in source
    assert "Dọn trí nhớ" in source
    assert "_memoryPortraitLeftList" in source
    assert "_memoryPortraitRightList" in source
    assert 'section.Kind is "our_memories" or "not_sure"' in source
    assert "DeepSeek quản lý ký ức" not in source
    assert "FormatMemoryFeedTrace" not in source
    assert 'SendMemoryPanelRequest("cleanup_now")' in source
    assert "StartMemoryCleanup" in source

class _AsyncCleanupService(_CleanupService):
    def __init__(self):
        super().__init__()
        self.request_calls = 0

    def request_cleanup(self):
        self.request_calls += 1
        return {"accepted": True, "reason": "manual_cleanup_started"}


def test_memory_panel_prefers_async_cleanup_request(tmp_path):
    living = LivingUserMemoryService(tmp_path / "living_async")
    cleanup = _AsyncCleanupService()
    reply = handle_memory_panel_request(living, {"action": "cleanup_now"}, cleanup_service=cleanup)
    assert reply.status == "ok"
    assert cleanup.request_calls == 1
    assert cleanup.calls == 0
    assert reply.payload["cleanup_request"]["accepted"] is True


def test_memory_feed_trace_hot_path_compacts_without_copying_text(tmp_path):
    path = tmp_path / "feed_many.jsonl"
    trace = MemoryFeedTraceJournal(path, max_records=32)
    for idx in range(70):
        trace.record_retrieval(
            query=f"q{idx}",
            retrieved=1,
            admitted=[{"record_id": f"m{idx}", "layer": "semantic", "reason": "ok"}],
            rejected=[],
            rendered_ids=[f"m{idx}"],
        )
    trace.compact()
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) <= 32
    assert "m69" in rows[-1]
