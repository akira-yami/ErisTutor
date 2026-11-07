from __future__ import annotations

from pathlib import Path

import pytest

from cortex.subject.contracts import OutcomeStatus
from runtime.world import (
    CapabilityAuthority,
    CapabilityPolicy,
    CommandPolicy,
    SandboxCommand,
    SandboxCommandKind,
    SandboxWorld,
)


AUTHORITY = CapabilityAuthority(
    secret=b"sandbox-world-test-secret-32-bytes!!",
    issuer="test-suite",
    audience="sandbox",
    clock=lambda: 0.0,
    id_factory=lambda: "token",
)


def policy(workspace: Path, *, executables=frozenset()) -> CapabilityPolicy:
    return CapabilityPolicy(
        CommandPolicy(frozenset(executables), str(workspace)),
        token_verifier=AUTHORITY.verify,
    )


def token(*capabilities: str):
    return AUTHORITY.issue(
        subject_id="eris",
        capabilities=frozenset(capabilities),
        policy_version="v1",
        scope={
            "command_ids": ("cmd",),
            "action_ids": ("action",),
            "decision_ids": ("decision",),
            "idempotency_keys": ("idem",),
        },
        ttl_seconds=300.0,
    )


def command(kind, capability, arguments=None, *, simulate=False) -> SandboxCommand:
    return SandboxCommand("cmd", "action", "decision", kind, arguments or {}, capability, "idem", 1.0, simulate)


@pytest.mark.asyncio
async def test_sandbox_denies_missing_capability_without_rewriting_proposal(tmp_path: Path) -> None:
    sandbox = SandboxWorld(policy=policy(tmp_path, executables={"python"}), clock=lambda: 10.0, id_factory=lambda: "out")
    outcome = await sandbox.apply(command(SandboxCommandKind.RUN_COMMAND, "run", {"argv": ["python", "-V"]}), token("read"))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.denial_reason == "capability_missing_or_expired"


@pytest.mark.asyncio
async def test_path_traversal_is_denied(tmp_path: Path) -> None:
    sandbox = SandboxWorld(policy=policy(tmp_path), clock=lambda: 10.0, id_factory=lambda: "out")
    outcome = await sandbox.apply(command(SandboxCommandKind.READ_FILE, "read_file", {"path": "../secret"}), token("read_file"))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.denial_reason == "path_outside_workspace"


@pytest.mark.asyncio
async def test_shell_injection_is_denied(tmp_path: Path) -> None:
    sandbox = SandboxWorld(policy=policy(tmp_path, executables={"python"}), clock=lambda: 10.0, id_factory=lambda: "out")
    outcome = await sandbox.apply(command(SandboxCommandKind.RUN_COMMAND, "run", {"argv": ["python", "-V; rm -rf / "]}), token("run"))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.denial_reason == "shell_metacharacters_denied"


@pytest.mark.asyncio
async def test_workspace_write_records_hashes(tmp_path: Path) -> None:
    sandbox = SandboxWorld(policy=policy(tmp_path), clock=lambda: 10.0, id_factory=lambda: "out")
    outcome = await sandbox.apply(command(SandboxCommandKind.WRITE_FILE, "write_file", {"path": "a.txt", "content": "hello"}), token("write_file"))
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert (tmp_path / "a.txt").read_text() == "hello"
    assert any(item.startswith("postcondition_hash:") for item in outcome.observed_changes)


@pytest.mark.asyncio
async def test_idempotent_sandbox_returns_same_outcome(tmp_path: Path) -> None:
    ids = iter(("out1", "out2"))
    sandbox = SandboxWorld(policy=policy(tmp_path), clock=lambda: 10.0, id_factory=lambda: next(ids))
    cmd = command(SandboxCommandKind.SIMULATE, "simulate", simulate=True)
    first = await sandbox.apply(cmd, token("simulate"))
    second = await sandbox.apply(cmd, token("simulate"))
    assert second is first
