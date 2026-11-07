from __future__ import annotations

from pathlib import Path

import pytest

from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxCommand, SandboxCommandKind, SandboxWorld

AUTHORITY = CapabilityAuthority(
    secret=b"mutation-journal-test-secret-32bytes!",
    issuer="test-suite",
    audience="sandbox",
    clock=lambda: 0.0,
    id_factory=lambda: "token",
)


def _policy(root: Path) -> CapabilityPolicy:
    return CapabilityPolicy(CommandPolicy(frozenset(), str(root)), token_verifier=AUTHORITY.verify)


def _token():
    return AUTHORITY.issue(
        subject_id="eris",
        capabilities=frozenset({"write_file"}),
        policy_version="v1",
        scope={"command_ids": ("cmd",), "action_ids": ("action",), "decision_ids": ("decision",), "idempotency_keys": ("idem",)},
        ttl_seconds=300.0,
    )


@pytest.mark.asyncio
async def test_write_file_returns_rollback_handle_and_fenced_rollback(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    sandbox = SandboxWorld(policy=_policy(tmp_path), clock=lambda: 10.0, id_factory=lambda: "out")
    command = SandboxCommand("cmd", "action", "decision", SandboxCommandKind.WRITE_FILE, {"path": "a.txt", "content": "after"}, "write_file", "idem", 1.0, False)
    outcome = await sandbox.apply(command, _token())
    handle = next(item.split(":", 1)[1] for item in outcome.observed_changes if item.startswith("rollback_handle:"))
    assert target.read_text(encoding="utf-8") == "after"
    assert sandbox.rollback_mutation(handle) is True
    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_rollback_refuses_to_clobber_later_change(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    sandbox = SandboxWorld(policy=_policy(tmp_path), clock=lambda: 10.0, id_factory=lambda: "out")
    command = SandboxCommand("cmd", "action", "decision", SandboxCommandKind.WRITE_FILE, {"path": "a.txt", "content": "after"}, "write_file", "idem", 1.0, False)
    outcome = await sandbox.apply(command, _token())
    handle = next(item.split(":", 1)[1] for item in outcome.observed_changes if item.startswith("rollback_handle:"))
    target.write_text("newer", encoding="utf-8")
    assert sandbox.rollback_mutation(handle) is False
    assert target.read_text(encoding="utf-8") == "newer"
