"""Retiring the graph must preserve canonical writes, recall and old data."""

import builtins
import io
from pathlib import Path

import pytest

from memory.living_user.models import ProfileOperation
from memory.memory_cognition import MemoryCognitionCore


@pytest.mark.parametrize("legacy_graph", [False, True])
def test_memory_works_without_reading_or_creating_a_graph(tmp_path, monkeypatch, legacy_graph):
    monkeypatch.setenv("ERIS_EMBEDDING_ENABLED", "0")
    graph_path = tmp_path / "concept_graph.json"
    old_graph = b'{"nodes":{"legacy":{}},"edges":{},"version":1}'
    if legacy_graph:
        graph_path.write_bytes(old_graph)

    graph_accesses = []

    def watch(original):
        def open_file(file, *args, **kwargs):
            if isinstance(file, (str, Path)) and Path(file).name == graph_path.name:
                graph_accesses.append(str(file))
                raise AssertionError("Retired graph was accessed")
            return original(file, *args, **kwargs)
        return open_file

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", watch(builtins.open))
        patch.setattr(io, "open", watch(io.open))
        core = MemoryCognitionCore(str(tmp_path))
        for target in ("belief_core", "worldview_memory"):
            result = core.commit({
                "target": target, "content": "Prefer explanations grounded in evidence",
                "confidence": 0.9, "evidence_ids": ["user:test"],
                "storage_permission": True, "permission_required": False,
            })
            assert result.outcome == "accepted", result
        profile = core.living_user.apply_turn_operations((ProfileOperation(
            op="add", path="communication.response_style", value="ngắn gọn",
            confidence=0.95, explicitness="explicit", evidence_ids=("user:style",),
        ),))
        assert profile.applied == 1, profile
        episode = core.living_user.consolidate_session((
            {"role": "user", "content": "Tôi muốn câu trả lời ngắn gọn"},
            {"role": "assistant", "content": "Tôi sẽ giải thích ngắn gọn"},
        ), session_id="test:session", run_dream=False)
        assert episode is not None
        packet = core.living_user.compile_context("trả lời ngắn gọn")
        assert any(item.path == "communication.response_style" for item in packet.items)
        assert all("assoc=" not in item.reason for item in packet.items)
        snapshot = core.load()
        assert "concept_graph" not in snapshot
        assert len(snapshot["belief_core"]) == len(snapshot["worldview_memory"]) == 1
        restarted = MemoryCognitionCore(str(tmp_path))
        assert len(restarted.belief_store.load()) == 1
        assert restarted.living_user.profile_store.get("communication.response_style")

    assert graph_accesses == []
    if legacy_graph:
        assert graph_path.read_bytes() == old_graph
    else:
        assert not graph_path.exists()
