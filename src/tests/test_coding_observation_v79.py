# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from runtime.coding_observation import CodingObservationService, sanitize_coding_observation
from runtime.eris_code_observer_link import ErisCodeObserverLink


def _event(kind: str, *, task: str = "t1", actor: str = "coder", **extra):
    return {
        "schema": "eris.coding.observation.v1",
        "source": "eris-code/gitpilot",
        "task_id": task,
        "session_id": "s1",
        "thread_id": "th1",
        "workspace": "C:/repo",
        "actor": actor,
        "event_type": kind,
        "at": 100.0,
        **extra,
    }


def test_sanitizer_is_allow_list_and_rejects_unknown_event():
    clean = sanitize_coding_observation(_event("tool_started", tool="Read local file", secret_blob="NOPE"))
    assert clean["tool"] == "Read local file"
    assert "secret_blob" not in clean

    bad = _event("raw_chain_of_thought")
    try:
        sanitize_coding_observation(bad)
    except ValueError as exc:
        assert "unsupported_event_type" in str(exc)
    else:
        raise AssertionError("unknown coding event must be rejected")


def test_flow_detects_patch_before_inspection_and_scope_expansion(tmp_path: Path):
    service = CodingObservationService(path=tmp_path / "coding.jsonl")
    service.ingest(_event("task_started", actor="gitpilot"))
    service.ingest(_event("file_changed", files=["a.py"]))
    for index in range(1, 8):
        service.ingest(_event("file_changed", files=[f"f{index}.py"]))
    service.ingest(_event("task_completed", actor="gitpilot"))

    flow = service.flow("t1")
    codes = {item["code"] for item in flow["findings"]}
    assert "patch_before_inspection" in codes
    assert "scope_expansion" in codes
    assert "no_structured_verification" in codes
    assert "review_not_observed" in codes


def test_flow_does_not_flag_clean_inspect_edit_test_review(tmp_path: Path):
    service = CodingObservationService(path=tmp_path / "coding.jsonl")
    service.ingest(_event("task_started", actor="gitpilot"))
    service.ingest(_event("phase_changed", actor="explorer", status="explorer"))
    service.ingest(_event("tool_started", actor="explorer", tool="Read local file", files=["a.py"]))
    service.ingest(_event("file_changed", actor="coder", files=["a.py"]))
    service.ingest(_event("test_result", actor="validation", status="passed", metrics={"passed": 3, "failed": 0}))
    service.ingest(_event("phase_changed", actor="reviewer", status="reviewer"))
    service.ingest(_event("task_completed", actor="gitpilot"))
    assert service.flow("t1")["findings"] == []


def test_journal_replays_after_restart(tmp_path: Path):
    path = tmp_path / "coding.jsonl"
    first = CodingObservationService(path=path)
    first.ingest(_event("task_started", actor="gitpilot"))
    first.ingest(_event("tool_started", actor="explorer", tool="Search in files"))

    second = CodingObservationService(path=path)
    assert len(second.recent(task_id="t1")) == 2
    assert second.flow("t1")["tools_started"] == 1


def test_loopback_bridge_accepts_only_observation_ingress(tmp_path: Path):
    service = CodingObservationService(path=tmp_path / "coding.jsonl")
    link = ErisCodeObserverLink(
        lambda workspace: {"protocol": "eris.link.v2", "workspace": workspace},
        observation_sink=service.ingest,
        journal_provider=lambda task_id, workspace, limit: service.recent(task_id=task_id, workspace=workspace, limit=limit),
        flow_provider=service.flow,
        port=0,
    )
    assert link.start()
    assert link._server is not None  # test-only: lấy ephemeral port do OS cấp
    port = link._server.server_address[1]
    try:
        body = json.dumps(_event("task_started", actor="gitpilot")).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/coding/observations",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as response:
            assert response.status == 202

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/coding/journal?task_id=t1", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert len(payload["events"]) == 1

        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/coding/control",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(bad, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
        else:
            raise AssertionError("coding control endpoint must not exist")
    finally:
        link.stop()


def test_phase_summary_is_explicit_evidence_not_hidden_reasoning(tmp_path: Path):
    service = CodingObservationService(path=tmp_path / "coding.jsonl")
    summary = "Explorer found shared shadow path in runtime/shadow.py; inspect caller next."
    service.ingest(_event("phase_summary", actor="explorer", evidence=summary))
    flow = service.flow("t1")
    assert flow["phase_summaries"]["explorer"] == summary
