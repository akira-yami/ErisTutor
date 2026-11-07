from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cortex.subject.contracts import ActionRequest, OutcomeStatus
from memory.subject import EventIntegrityError, SubjectRepository
from runtime.subject_bootstrap import build_subject_core_components
from runtime.world import (
    CapabilityAuthority,
    CapabilityPolicy,
    CommandPolicy,
    SandboxCommand,
    SandboxCommandKind,
    SandboxWorld,
)


class MutableClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


@pytest.mark.asyncio
async def test_tampered_capability_signature_is_denied(tmp_path: Path) -> None:
    clock = MutableClock()
    authority = CapabilityAuthority(
        secret=b"fenced-capability-test-secret-32bytes",
        issuer="test-suite",
        audience="sandbox",
        clock=clock,
        id_factory=lambda: "token",
    )
    policy = CapabilityPolicy(
        CommandPolicy(frozenset(), str(tmp_path), policy_version="v5"),
        token_verifier=authority.verify,
    )
    command = SandboxCommand(
        command_id="command",
        action_id="action",
        decision_id="decision",
        kind=SandboxCommandKind.SIMULATE,
        arguments={},
        requested_capability="simulate",
        idempotency_key="idem",
        created_at=clock(),
        simulate=True,
        subject_id="eris",
    )
    token = authority.issue(
        subject_id="eris",
        capabilities={"simulate"},
        policy_version="v5",
        scope={
            "command_ids": ("command",),
            "action_ids": ("action",),
            "decision_ids": ("decision",),
            "idempotency_keys": ("idem",),
        },
    )
    tampered = replace(token, capabilities=frozenset({"simulate", "write_file"}))

    outcome = await SandboxWorld(policy=policy, clock=clock, id_factory=lambda: "out").apply(command, tampered)

    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.denial_reason == "token_signature_invalid"


@pytest.mark.asyncio
async def test_capability_is_bound_to_command_and_idempotency_key(tmp_path: Path) -> None:
    clock = MutableClock()
    authority = CapabilityAuthority(
        secret=b"request-scope-test-secret-32-bytes!!",
        issuer="test-suite",
        audience="sandbox",
        clock=clock,
    )
    policy = CapabilityPolicy(CommandPolicy(frozenset(), str(tmp_path)), token_verifier=authority.verify)
    token = authority.issue(
        subject_id="eris",
        capabilities={"simulate"},
        policy_version="v1",
        scope={
            "command_ids": ("command-a",),
            "action_ids": ("action",),
            "decision_ids": ("decision",),
            "idempotency_keys": ("idem-a",),
        },
    )
    wrong_command = SandboxCommand(
        command_id="command-b",
        action_id="action",
        decision_id="decision",
        kind=SandboxCommandKind.SIMULATE,
        arguments={},
        requested_capability="simulate",
        idempotency_key="idem-a",
        created_at=clock(),
        simulate=True,
        subject_id="eris",
    )
    wrong_key = replace(wrong_command, command_id="command-a", idempotency_key="idem-b")

    command_outcome = await SandboxWorld(policy=policy, clock=clock, id_factory=lambda: "out-a").apply(wrong_command, token)
    key_outcome = await SandboxWorld(policy=policy, clock=clock, id_factory=lambda: "out-b").apply(wrong_key, token)

    assert command_outcome.denial_reason == "command_id_outside_token_scope"
    assert key_outcome.denial_reason == "idempotency_key_outside_token_scope"


def test_bootstrap_issues_only_requested_capability(tmp_path: Path) -> None:
    components = build_subject_core_components(
        data_root=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    try:
        request = ActionRequest(
            action_id="action",
            decision_id="decision",
            kind="read_file",
            arguments={"path": "a.txt"},
            expected_outcomes=(),
            requested_capabilities=("read_file",),
            idempotency_key="idem",
            authorship_draft_id="draft",
            created_at=1.0,
        )
        token = components.token_provider(request, "command")
        assert token.capabilities == frozenset({"read_file"})
        assert token.scope["command_ids"] == ("command",)
        assert token.scope["idempotency_keys"] == ("idem",)
        assert components.capability_authority.verify(token) == (True, "")
    finally:
        components.close()


def test_fencing_generation_rejects_stale_owner_after_takeover(tmp_path: Path) -> None:
    clock = MutableClock(100.0)
    repository = SubjectRepository(tmp_path / "subject.db", clock=clock)
    try:
        first = repository.claim_idempotency(
            scope="sandbox",
            idempotency_key="idem",
            request_hash="hash",
            owner_id="owner-a",
            lease_seconds=5.0,
        )
        assert first.acquired and first.generation == 1

        clock.value = 106.0
        second = repository.claim_idempotency(
            scope="sandbox",
            idempotency_key="idem",
            request_hash="hash",
            owner_id="owner-b",
            lease_seconds=5.0,
        )
        assert second.acquired and second.generation == 2

        payload = {
            "outcome_id": "out",
            "action_id": "action",
            "decision_id": "decision",
            "status": "succeeded",
            "observed_changes": [],
            "unintended_changes": [],
            "denial_reason": None,
            "evidence_refs": [],
            "started_at": 100.0,
            "completed_at": 106.0,
            "thread_id": "",
            "delivery_id": "",
        }
        with pytest.raises(EventIntegrityError, match="fencing token rejected"):
            repository.record_sandbox_outcome(
                idempotency_key="idem",
                request_hash="hash",
                outcome_id="out",
                action_id="action",
                decision_id="decision",
                status="succeeded",
                payload=payload,
                claim_owner_id="owner-a",
                claim_generation=first.generation,
            )

        stored, created = repository.record_sandbox_outcome(
            idempotency_key="idem",
            request_hash="hash",
            outcome_id="out",
            action_id="action",
            decision_id="decision",
            status="succeeded",
            payload=payload,
            claim_owner_id="owner-b",
            claim_generation=second.generation,
        )
        assert created
        assert stored["outcome_id"] == "out"
    finally:
        repository.close()


def test_heartbeat_renewal_prevents_premature_takeover(tmp_path: Path) -> None:
    clock = MutableClock(50.0)
    repository = SubjectRepository(tmp_path / "subject.db", clock=clock)
    try:
        first = repository.claim_idempotency(
            scope="sandbox",
            idempotency_key="idem",
            request_hash="hash",
            owner_id="owner-a",
            lease_seconds=5.0,
        )
        clock.value = 54.0
        assert repository.renew_idempotency_claim(
            scope="sandbox",
            idempotency_key="idem",
            request_hash="hash",
            owner_id="owner-a",
            generation=first.generation,
            lease_seconds=5.0,
        )
        clock.value = 56.0
        second = repository.claim_idempotency(
            scope="sandbox",
            idempotency_key="idem",
            request_hash="hash",
            owner_id="owner-b",
            lease_seconds=5.0,
        )
        assert second.status == "busy"
        assert second.generation == first.generation
    finally:
        repository.close()

@pytest.mark.asyncio
async def test_invalid_token_cannot_poison_idempotency_key(tmp_path: Path) -> None:
    clock = MutableClock()
    authority = CapabilityAuthority(
        secret=b"poisoning-test-secret-32-bytes-long!",
        issuer="test-suite",
        audience="sandbox",
        clock=clock,
        id_factory=lambda: "token",
    )
    policy = CapabilityPolicy(
        CommandPolicy(frozenset(), str(tmp_path), policy_version="v5"),
        token_verifier=authority.verify,
    )
    repository = SubjectRepository(tmp_path / "subject.db", clock=clock)
    command = SandboxCommand(
        command_id="command",
        action_id="action",
        decision_id="decision",
        kind=SandboxCommandKind.SIMULATE,
        arguments={},
        requested_capability="simulate",
        idempotency_key="idem",
        created_at=clock(),
        simulate=True,
        subject_id="eris",
    )
    valid = authority.issue(
        subject_id="eris",
        capabilities={"simulate"},
        policy_version="v5",
        scope={
            "command_ids": ("command",),
            "action_ids": ("action",),
            "decision_ids": ("decision",),
            "idempotency_keys": ("idem",),
        },
    )
    invalid = replace(valid, signature="0" * 64)
    ids = iter(("denied", "allowed"))
    sandbox = SandboxWorld(policy=policy, repository=repository, clock=clock, id_factory=lambda: next(ids))
    try:
        denied = await sandbox.apply(command, invalid)
        assert denied.status is OutcomeStatus.DENIED
        assert repository.get_idempotent(scope="sandbox", idempotency_key="idem") is None

        allowed = await sandbox.apply(command, valid)
        assert allowed.status is OutcomeStatus.SIMULATED
        assert repository.get_idempotent(scope="sandbox", idempotency_key="idem") is not None
    finally:
        repository.close()
