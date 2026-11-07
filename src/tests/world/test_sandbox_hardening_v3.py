from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cortex.subject.contracts import OutcomeStatus
from memory.subject.repository import EventIntegrityError, SubjectRepository
from runtime.world import (
    CapabilityAuthority,
    CapabilityPolicy,
    CommandPolicy,
    SandboxCommand,
    SandboxCommandKind,
    SandboxWorld,
)


AUTHORITY = CapabilityAuthority(
    secret=b"sandbox-hardening-test-secret-32b!",
    issuer="test-suite",
    audience="sandbox",
    clock=lambda: 0.0,
    id_factory=lambda: "token",
)


def make_policy(command_policy: CommandPolicy) -> CapabilityPolicy:
    return CapabilityPolicy(command_policy, token_verifier=AUTHORITY.verify)


def make_token(*capabilities: str, policy_version: str = "v1", subject_id: str = "eris", scope=None):
    base_scope = {
        "command_ids": ("cmd",),
        "action_ids": ("action",),
        "decision_ids": ("decision",),
        "idempotency_keys": ("idem", "empty", "exe", "a", "b"),
    }
    base_scope.update(scope or {})
    return AUTHORITY.issue(
        subject_id=subject_id,
        capabilities=frozenset(capabilities),
        policy_version=policy_version,
        scope=base_scope,
        ttl_seconds=300.0,
    )


def make_command(
    kind: SandboxCommandKind,
    capability: str,
    arguments=None,
    *,
    key: str = "idem",
    subject_id: str = "eris",
    simulate: bool = False,
) -> SandboxCommand:
    return SandboxCommand(
        command_id="cmd",
        action_id="action",
        decision_id="decision",
        kind=kind,
        arguments=arguments or {},
        requested_capability=capability,
        idempotency_key=key,
        created_at=10.0,
        simulate=simulate,
        subject_id=subject_id,
        thread_id="thread",
        delivery_id="delivery",
    )


@pytest.mark.asyncio
async def test_policy_version_and_subject_are_bound_to_token(tmp_path: Path) -> None:
    policy = make_policy(CommandPolicy(frozenset(), str(tmp_path), policy_version="v3"))
    sandbox = SandboxWorld(policy=policy, clock=lambda: 10.0, id_factory=lambda: "out")
    mismatch = await sandbox.apply(
        make_command(SandboxCommandKind.SIMULATE, "simulate", simulate=True),
        make_token("simulate", policy_version="v2"),
    )
    assert mismatch.status is OutcomeStatus.DENIED
    assert mismatch.denial_reason == "policy_version_mismatch"

    other = await SandboxWorld(policy=policy, clock=lambda: 10.0, id_factory=lambda: "out2").apply(
        make_command(SandboxCommandKind.SIMULATE, "simulate", subject_id="other", simulate=True),
        make_token("simulate", policy_version="v3"),
    )
    assert other.denial_reason == "token_subject_mismatch"


@pytest.mark.asyncio
async def test_empty_path_and_explicit_executable_path_are_denied(tmp_path: Path) -> None:
    policy = make_policy(CommandPolicy(frozenset({"python"}), str(tmp_path)))
    sandbox = SandboxWorld(policy=policy, clock=lambda: 10.0, id_factory=lambda: "out")
    empty = await sandbox.apply(
        make_command(SandboxCommandKind.READ_FILE, "read_file", {"path": ""}, key="empty"),
        make_token("read_file"),
    )
    assert empty.denial_reason == "path_required"
    explicit = await sandbox.apply(
        make_command(SandboxCommandKind.RUN_COMMAND, "run", {"argv": ["/tmp/python", "-V"]}, key="exe"),
        make_token("run"),
    )
    assert explicit.denial_reason == "executable_path_denied"


@pytest.mark.asyncio
async def test_token_workspace_prefix_limits_file_access(tmp_path: Path) -> None:
    (tmp_path / "allowed").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "allowed" / "a.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "other" / "b.txt").write_text("no", encoding="utf-8")
    policy = make_policy(CommandPolicy(frozenset(), str(tmp_path)))
    scoped = make_token("read_file", scope={"workspace_prefixes": ["allowed"]})
    allowed = await SandboxWorld(policy=policy, clock=lambda: 10.0, id_factory=lambda: "a").apply(
        make_command(SandboxCommandKind.READ_FILE, "read_file", {"path": "allowed/a.txt"}, key="a"), scoped
    )
    denied = await SandboxWorld(policy=policy, clock=lambda: 10.0, id_factory=lambda: "b").apply(
        make_command(SandboxCommandKind.READ_FILE, "read_file", {"path": "other/b.txt"}, key="b"), scoped
    )
    assert allowed.status is OutcomeStatus.SUCCEEDED
    assert denied.denial_reason == "path_outside_token_scope"


@pytest.mark.asyncio
async def test_concurrent_same_key_executes_handler_once(tmp_path: Path) -> None:
    calls = 0

    async def body_handler(arguments):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return (f"body:{arguments['frame']}",)

    sandbox = SandboxWorld(
        policy=make_policy(CommandPolicy(frozenset(), str(tmp_path))),
        clock=lambda: 10.0,
        id_factory=iter(("out", "unused")).__next__,
        body_handler=body_handler,
    )
    cmd = make_command(SandboxCommandKind.BODY_FRAME, "body", {"frame": "x"})
    first, second = await asyncio.gather(sandbox.apply(cmd, make_token("body")), sandbox.apply(cmd, make_token("body")))
    assert calls == 1
    assert first is second
    assert first.thread_id == "thread"
    assert first.delivery_id == "delivery"


@pytest.mark.asyncio
async def test_idempotency_survives_restart_and_rejects_key_reuse(tmp_path: Path) -> None:
    database = tmp_path / "subject.db"
    repo1 = SubjectRepository(database)
    cmd = make_command(SandboxCommandKind.SIMULATE, "simulate", simulate=True)
    first = await SandboxWorld(
        policy=make_policy(CommandPolicy(frozenset(), str(tmp_path))),
        repository=repo1,
        clock=lambda: 10.0,
        id_factory=lambda: "out-1",
    ).apply(cmd, make_token("simulate"))
    repo1.close()

    repo2 = SubjectRepository(database)
    second = await SandboxWorld(
        policy=make_policy(CommandPolicy(frozenset(), str(tmp_path))),
        repository=repo2,
        clock=lambda: 10.0,
        id_factory=lambda: "should-not-be-used",
    ).apply(cmd, make_token("simulate"))
    assert second.to_dict() == first.to_dict()

    changed = make_command(SandboxCommandKind.SIMULATE, "simulate", {"different": True}, simulate=True)
    with pytest.raises(EventIntegrityError, match="different request"):
        await SandboxWorld(
            policy=make_policy(CommandPolicy(frozenset(), str(tmp_path))),
            repository=repo2,
            clock=lambda: 10.0,
        ).apply(changed, make_token("simulate"))
    repo2.close()


@pytest.mark.asyncio
async def test_symlink_path_is_denied_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    sandbox = SandboxWorld(
        policy=make_policy(CommandPolicy(frozenset(), str(tmp_path))),
        clock=lambda: 10.0,
        id_factory=lambda: "out",
    )
    outcome = await sandbox.apply(
        make_command(SandboxCommandKind.WRITE_FILE, "write_file", {"path": "link/a.txt", "content": "x"}),
        make_token("write_file"),
    )
    assert outcome.denial_reason == "symlink_path_denied"
