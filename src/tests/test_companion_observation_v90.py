# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from runtime.coding_observation import CodingObservationService, sanitize_coding_observation
from runtime.engineering_mentor import EngineeringMentorService
from runtime.eris_code_observer_link import ErisCodeObserverLink


def _event(kind: str, *, source: str = "eris-code/workspace", actor: str = "vscode", task: str = "ws1", **extra):
    return {
        "schema": "eris.coding.observation.v1",
        "source": source,
        "task_id": task,
        "session_id": "session1",
        "thread_id": "",
        "workspace": "C:/repo",
        "actor": actor,
        "event_type": kind,
        "at": 100.0,
        **extra,
    }


def test_v90_accepts_workspace_user_and_external_agent_sources():
    assert sanitize_coding_observation(_event("editor_focus"))["source"] == "eris-code/workspace"
    assert sanitize_coding_observation(_event("user_intent", source="eris-code/user", actor="user"))["actor"] == "user"
    assert sanitize_coding_observation(_event("task_started", source="eris-code/external-agent", actor="external_agent"))["source"] == "eris-code/external-agent"


def test_passive_workspace_activity_updates_companion_but_not_agent_flow(tmp_path: Path):
    service = CodingObservationService(path=tmp_path / "coding.jsonl")
    service.ingest(_event("workspace_session_started", status="active"))
    service.ingest(_event("editor_focus", files=["runtime/ai.py"]))
    service.ingest(_event("file_saved", files=["runtime/ai.py"]))
    service.ingest(_event("diagnostics", files=["runtime/ai.py"], status="failed", metrics={"errors": 2, "warnings": 1}))
    service.ingest(_event("terminal_command", command="pytest -q", status="failed", metrics={"exit_code": 1}))

    assert service.flow("ws1") == {}
    companion = service.companion_snapshot(workspace="C:/repo")
    assert companion["active_file"] == "runtime/ai.py"
    assert companion["recent_files"][0] == "runtime/ai.py"
    assert companion["diagnostics"]["errors"] == 2
    assert companion["recent_commands"][0]["command"] == "pytest -q"
    assert "eris-code/workspace" in companion["sources"]


def test_user_intent_is_linked_to_agent_task_and_companion_context(tmp_path: Path):
    service = CodingObservationService(path=tmp_path / "coding.jsonl")
    intent = "Tôi nghĩ bug nằm ở target_sensor.gd; đừng đụng navigation, chỉ sửa perception boundary."
    service.ingest(_event("user_intent", source="eris-code/user", actor="user", task="t1", evidence=intent))
    service.ingest(_event("task_started", source="eris-code/gitpilot", actor="gitpilot", task="t1", status="running"))
    service.ingest(_event("phase_changed", source="eris-code/gitpilot", actor="explorer", task="t1", status="explorer"))

    flow = service.flow("t1")
    assert flow["intent"] == intent
    companion = service.companion_snapshot(workspace="C:/repo")
    assert companion["current_intent"] == intent
    assert companion["current_task_id"] == "t1"
    assert companion["agent_actor"] == "explorer"


def test_v90_external_intent_bridge_is_legacy_noop_after_companion_decoupling(tmp_path: Path):
    mentor = EngineeringMentorService(path=tmp_path / "skills.json")
    before = mentor.model.snapshot()["skills"]["architecture.system_boundaries"]["evidence_count"]

    mentor.observe_companion_context({
        "workspace": "C:/repo",
        "last_event_at": 100.0,
        "active_file": "runtime/ai.py",
        "recent_files": ["runtime/ai.py"],
        "current_task_id": "hidden-task",
        "sources": ["eris-code/workspace"],
    })
    signals = mentor.observe_external_user_intent(
        "Repo này sai boundary: target_sensor.gd phải chịu trách nhiệm perception; đừng đụng navigation, chỉ sửa file đó.",
        evidence_ref="observer:t1:1",
        now=200.0,
    )

    after = mentor.model.snapshot()["skills"]["architecture.system_boundaries"]["evidence_count"]
    assert signals == ()
    assert after == before
    assert mentor.snapshot()["last_companion_context"] == {}


def test_companion_context_is_exposed_read_only_over_loopback(tmp_path: Path):
    service = CodingObservationService(path=tmp_path / "coding.jsonl")
    service.ingest(_event("editor_focus", files=["src/main.py"]))
    link = ErisCodeObserverLink(
        lambda workspace: {"protocol": "eris.link.v2", "workspace": workspace},
        observation_sink=service.ingest,
        companion_provider=lambda workspace: service.companion_snapshot(workspace=workspace),
        port=0,
    )
    assert link.start()
    assert link._server is not None
    port = link._server.server_address[1]
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/v1/companion/state?workspace=C%3A%2Frepo", timeout=2
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert payload["schema"] == "eris.companion.workspace.v1"
            assert payload["active_file"] == "src/main.py"
    finally:
        link.stop()
