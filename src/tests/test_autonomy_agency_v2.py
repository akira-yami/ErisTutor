# -*- coding: utf-8 -*-
"""Kiểm thử quyền tự chủ V2: không dùng remote LLM hoặc tool ngoài sandbox."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import threading
import time

import pytest

from runtime.autonomy.agency_v2 import AutonomyLoopV2, AutonomyRuntimeV2, choose_subject_goal
from runtime.autonomy.goal_events import GoalEventLedger
from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld


def view(ids=("one", "two")):
    return {"revision": 12, "pending_intentions": [
        {"intention_id": name, "semantic_goal": "Tự kiểm tra " + name,
         "status": "pending", "motive_kind": "explore",
         "pressure": (0.9 if name == "two" else 0.6),
         "commitment_strength": 0.9, "evidence_refs": ["semantic:observation"]}
        for name in ids
    ]}


def make_loop(root):
    ledger = GoalEventLedger(root / "autonomy.sqlite")
    authority = CapabilityAuthority(secret=b"sandbox-v2-test-signing-secret-32!", issuer="test", audience="sandbox")
    sandbox = SandboxWorld(policy=CapabilityPolicy(CommandPolicy(frozenset(), str(root)), token_verifier=authority.verify))
    def token(cmd):
        return authority.issue(subject_id="eris", capabilities=frozenset({"simulate"}),
            policy_version="v1", scope={"command_ids": (cmd.command_id,),
                "action_ids": (cmd.action_id,), "decision_ids": (cmd.decision_id,),
                "idempotency_keys": (cmd.idempotency_key,)}, ttl_seconds=60)
    return AutonomyLoopV2(ledger, sandbox, token), ledger


def test_selection_based_on_actual_intention_fields():
    selection = choose_subject_goal(view())
    assert selection.intention_id == "two"
    assert selection.score > 0.8
    assert choose_subject_goal({"revision": 1, "pending_intentions": []}) is None
    assert choose_subject_goal({"pending_intentions": [{"status": "satisfied", "intention_id":"x"}]}) is None


def test_full_closed_loop_and_no_replay(tmp_path):
    loop, ledger = make_loop(tmp_path)
    first = asyncio.run(loop.cycle(view(), preempted=lambda: False))
    assert first["goal_status"] == "achieved", first
    assert first["outcome_ref"]
    goal = ledger.get(first["goal_id"])
    assert goal.status == "achieved"
    assert len(ledger.events(goal.goal_id)) == 4
    second = asyncio.run(loop.cycle(view(), preempted=lambda: False))
    assert second["status"] == "achieved"
    assert len(ledger.events(goal.goal_id)) == 4
    # SQLite giữ trạng thái qua restart, không dựa vào bộ nhớ process.
    assert GoalEventLedger(tmp_path / "autonomy.sqlite").get(goal.goal_id).status == "achieved"


def test_user_preempt_before_action(tmp_path):
    loop, ledger = make_loop(tmp_path)
    result = asyncio.run(loop.cycle(view(ids=("one",)), preempted=lambda: True))
    assert result["status"] == "bi_ngat"
    goal = ledger.get(result["goal_id"])
    assert goal.status == "proposed"
    with ledger._db() as conn:
        assert conn.execute("SELECT count(*) FROM autonomy_actions").fetchone()[0] == 0


class PreemptWorld:
    def __init__(self, flag, real):
        self.flag = flag
        self.real = real
    async def apply(self, command, capability):
        outcome = await self.real.apply(command, capability)
        self.flag["cancel"] = True
        return outcome


def test_user_preempt_after_tool_receipt_does_not_commit(tmp_path):
    loop, ledger = make_loop(tmp_path)
    flag = {"cancel": False}
    loop.bridge.world = PreemptWorld(flag, loop.bridge.world)
    result = asyncio.run(loop.cycle(view(ids=("one",)), preempted=lambda: flag["cancel"]))
    assert result["status"] == "ket_qua_sau_ngat_cho_doi_chieu"
    assert ledger.get(result["goal_id"]).status == "active"
    action = loop.bridge.get_action("action:v2:" + __import__('hashlib').sha256(result["goal_id"].encode()).hexdigest()[:20] + ":step-1")
    assert action and action["status"] == "completed"
    again = asyncio.run(loop.cycle(view(ids=("one",)), preempted=lambda: False))
    assert again["status"] == "da_co_receipt_can_doi_chieu"


def test_runtime_pump_observe_and_panel(tmp_path):
    sent = []
    host = SimpleNamespace(chat_mode=False, transport=SimpleNamespace(send_json=sent.append),
                           subject_service=SimpleNamespace(initiative_view=lambda: view()))
    runtime = AutonomyRuntimeV2(host, mode="observe", ledger=GoalEventLedger(tmp_path / "g.sqlite"), clock=lambda: 200)
    runtime.pump()
    assert runtime.last_result["status"] == "quan_sat"
    assert sent == []
    runtime.note_event("screen_changed")
    runtime.preempt()
    assert runtime._generation == 1
    runtime.close()


def test_runtime_sandbox_background_does_not_block(tmp_path):
    loop, ledger = make_loop(tmp_path)
    packets = []
    host = SimpleNamespace(chat_mode=False, transport=SimpleNamespace(send_json=packets.append),
                           subject_service=SimpleNamespace(initiative_view=lambda: view()))
    runtime = AutonomyRuntimeV2(host, mode="sandbox", ledger=ledger, loop=loop, clock=lambda: 1000)
    start = time.monotonic()
    runtime.pump()
    assert time.monotonic() - start < 0.3
    assert runtime._future is not None
    runtime._future.result(timeout=5)
    runtime.pump()
    assert packets and packets[-1]["parse_mode"] == "autonomy_goals"
    assert ledger.list_goals()[0].status == "achieved"
    runtime.close()


def test_chat_mode_blocks_new_worker(tmp_path):
    loop, ledger = make_loop(tmp_path)
    host = SimpleNamespace(chat_mode=True, transport=SimpleNamespace(send_json=lambda _: None),
                           subject_service=SimpleNamespace(initiative_view=lambda: view()))
    runtime = AutonomyRuntimeV2(host, mode="sandbox", ledger=ledger, loop=loop, clock=lambda: 1000)
    runtime.pump()
    assert runtime._future is None
    runtime.close()

class LocalWritePlanner:
    """Planner thử nghiệm từ bên ngoài; không được cấp mặc định cho production."""
    def build(self, choice, proof, goal_id):
        from runtime.world.contracts import SandboxCommand, SandboxCommandKind
        return SandboxCommand(
            command_id="cmd:write-local", action_id="act:write-local", decision_id=proof.commit_id,
            kind=SandboxCommandKind.WRITE_FILE,
            arguments={"path": "allowed/proof.txt", "content": "Eris test V2"},
            requested_capability="write_file", idempotency_key="v2:write-once",
            created_at=time.time(), simulate=False, subject_id="eris",
        )


def test_injected_planner_can_write_only_into_scoped_sandbox(tmp_path):
    from runtime.world.contracts import SandboxCommandKind
    from runtime.autonomy.action_bridge import GoalActionBridge
    from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld
    ledger = GoalEventLedger(tmp_path / "write.sqlite")
    authority = CapabilityAuthority(secret=b"v2-explicit-local-sandbox-test-only!!", issuer="test", audience="sandbox")
    policy = CapabilityPolicy(CommandPolicy(frozenset(), str(tmp_path)), token_verifier=authority.verify)
    world = SandboxWorld(policy=policy)
    def capability(cmd):
        return authority.issue(subject_id="eris", capabilities=frozenset({"write_file"}),
          policy_version="v1", scope={"command_ids": (cmd.command_id,),
          "action_ids": (cmd.action_id,), "decision_ids": (cmd.decision_id,),
          "idempotency_keys": (cmd.idempotency_key,), "workspace_prefixes": ("allowed",)}, ttl_seconds=60)
    loop = AutonomyLoopV2(ledger, world, capability, plan=LocalWritePlanner(), allow_effects=True)
    result = asyncio.run(loop.cycle(view(ids=("one",)), preempted=lambda: False))
    assert result["outcome_status"] == "succeeded", result
    assert (tmp_path / "allowed" / "proof.txt").read_text() == "Eris test V2"
    # Ghi được file chưa đồng nghĩa đã hoàn thành mục tiêu chủ quan của Eris.
    assert ledger.get(result["goal_id"]).status == "active"
    after = asyncio.run(loop.cycle(view(ids=("one",)), preempted=lambda: False))
    assert after["status"] == "da_co_receipt_can_doi_chieu"


def test_opt_in_environment_uses_real_subject_projection_and_sandbox(tmp_path, monkeypatch):
    from runtime.autonomy.agency_v2 import ensure_autonomy_v2
    monkeypatch.setenv("ERIS_AUTONOMY_V2_MODE", "sandbox")
    monkeypatch.setenv("ERIS_AUTONOMY_V2_DB", str(tmp_path / "production-like.sqlite"))
    monkeypatch.setenv("ERIS_AUTONOMY_V2_WORKDIR", str(tmp_path / "workspace"))
    packets = []
    host = SimpleNamespace(chat_mode=False, transport=SimpleNamespace(send_json=packets.append),
                           subject_service=SimpleNamespace(initiative_view=lambda: view(ids=("one",))))
    runtime = ensure_autonomy_v2(host)
    assert runtime is not None and ensure_autonomy_v2(host) is runtime
    runtime._last_tick = -100
    runtime.pump()
    runtime._future.result(timeout=5)
    runtime.pump()
    assert runtime.last_result["goal_status"] == "achieved"
    assert runtime.ledger.list_goals()[0].title.startswith("Thử nghiệm ý định:")
    assert packets[-1]["parse_mode"] == "autonomy_goals"
    runtime.close()


def test_default_disabled_does_not_create_worker_or_database(tmp_path, monkeypatch):
    from runtime.autonomy.agency_v2 import ensure_autonomy_v2
    monkeypatch.delenv("ERIS_AUTONOMY_V2_MODE", raising=False)
    monkeypatch.setenv("ERIS_AUTONOMY_V2_DB", str(tmp_path / "missing.sqlite"))
    host = SimpleNamespace()
    assert ensure_autonomy_v2(host) is None
    assert not (tmp_path / "missing.sqlite").exists()


def test_kill_switch_closes_runtime_without_autonomy_side_effect(tmp_path, monkeypatch):
    from runtime.autonomy.agency_v2 import ensure_autonomy_v2
    monkeypatch.setenv("ERIS_AUTONOMY_V2_MODE", "observe")
    monkeypatch.setenv("ERIS_AUTONOMY_V2_DB", str(tmp_path / "kill.sqlite"))
    host = SimpleNamespace(chat_mode=False, transport=SimpleNamespace(send_json=lambda _: None),
                           subject_service=SimpleNamespace(initiative_view=lambda: view()))
    runtime = ensure_autonomy_v2(host)
    assert runtime is not None
    monkeypatch.setenv("ERIS_AUTONOMY_V2_MODE", "off")
    assert ensure_autonomy_v2(host) is None
    assert host._autonomy_v2 is None
