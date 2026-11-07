from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from actions.executor import ActionExecutor
from cortex.subject import SubjectService, SubjectServiceConfig
from cortex.subject.contracts import ActionRequest
from memory.subject import SubjectRepository
from runtime.world import (
    CapabilityAuthority,
    CapabilityPolicy,
    CommandPolicy,
    SandboxCommand,
    SandboxCommandKind,
    SandboxWorld,
)


AUTHORITY = CapabilityAuthority(
    secret=b"native-sandbox-test-secret-32-bytes!",
    issuer="test-suite",
    audience="sandbox",
    clock=time.time,
)


def _policy(workspace: Path) -> CapabilityPolicy:
    return CapabilityPolicy(
        CommandPolicy(
            executable_allowlist=frozenset({"python"}),
            workspace_root=str(workspace),
            policy_version="v5-test",
            max_runtime_seconds=2,
        ),
        token_verifier=AUTHORITY.verify,
    )


def _token(request: ActionRequest, command_id: str):
    return AUTHORITY.issue(
        subject_id="eris",
        capabilities=frozenset(request.requested_capabilities),
        policy_version="v5-test",
        scope={
            "command_ids": (command_id,),
            "action_ids": (request.action_id,),
            "decision_ids": (request.decision_id,),
            "idempotency_keys": (request.idempotency_key,),
        },
        ttl_seconds=60.0,
    )


def test_action_executor_has_no_unsandboxed_fallback() -> None:
    result = ActionExecutor().execute("get_local_time", "")
    assert not result.success
    assert result.error_kind == "SubjectCoreUnavailable"


def test_action_executor_runs_actual_handler_inside_sandbox(tmp_path: Path) -> None:
    sandbox = SandboxWorld(policy=_policy(tmp_path))
    executor = ActionExecutor()
    executor.bind_subject_service(SubjectService(config=SubjectServiceConfig(subject_id="eris")))
    executor.bind_sandbox_world(sandbox, _token)

    result = executor.execute("get_local_time", "")

    assert result.success
    assert result.action_name == "get_local_time"
    assert result.observed


@pytest.mark.asyncio
async def test_two_sandbox_instances_execute_same_idempotent_side_effect_once(tmp_path: Path) -> None:
    database = tmp_path / "subject.db"
    repository_a = SubjectRepository(database)
    repository_b = SubjectRepository(database)
    calls = 0

    def handler(action_name: str, action_input: str):
        nonlocal calls
        calls += 1
        time.sleep(0.15)
        return type("Result", (), {"success": True, "status": "observed", "message": "ok"})()

    sandbox_a = SandboxWorld(policy=_policy(tmp_path), repository=repository_a, system_action_handler=handler, owner_id="a")
    sandbox_b = SandboxWorld(policy=_policy(tmp_path), repository=repository_b, system_action_handler=handler, owner_id="b")

    def command(command_id: str) -> SandboxCommand:
        return SandboxCommand(
            command_id=command_id,
            action_id="action-1",
            decision_id="decision-1",
            kind=SandboxCommandKind.SYSTEM_ACTION,
            arguments={"action_name": "get_local_time", "action_input": ""},
            requested_capability="get_local_time",
            idempotency_key="idem-1",
            created_at=time.time(),
            subject_id="eris",
        )

    def direct_token(command_id: str):
        return AUTHORITY.issue(
            subject_id="eris",
            capabilities={"get_local_time"},
            policy_version="v5-test",
            scope={
                "command_ids": (command_id,),
                "action_ids": ("action-1",),
                "decision_ids": ("decision-1",),
                "idempotency_keys": ("idem-1",),
            },
            ttl_seconds=60.0,
        )

    try:
        first, second = await asyncio.gather(
            sandbox_a.apply(command("command-a"), direct_token("command-a")),
            sandbox_b.apply(command("command-b"), direct_token("command-b")),
        )
        assert calls == 1
        assert first.outcome_id == second.outcome_id
        assert repository_a.get_idempotent(scope="sandbox", idempotency_key="idem-1") is not None
    finally:
        repository_a.close()
        repository_b.close()
