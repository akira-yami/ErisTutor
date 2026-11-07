# -*- coding: utf-8 -*-
"""Sandbox World có sức cản và audit được.

Mind được quyền đề xuất command, kể cả command sẽ bị từ chối. Sandbox chỉ quyết
định thế giới cho phép, mô phỏng hay từ chối; nó không viết lại động cơ. V6 dùng
capability token có keyring, idempotency fencing và HTTP transport chống SSRF.
Hóa ra “chỉ chạy đúng một lần và đừng gọi localhost” vẫn cần khá nhiều code.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from cortex.subject.contracts import OutcomeStatus, WorldOutcome
from memory.subject.repository import EventIntegrityError, IdempotencyClaim, SubjectRepository, canonical_json, sha256_text
from runtime.audit import AuditEvent, AuditEventKind, ForensicStore
from .capability_policy import CapabilityPolicy, PolicyDecision
from .contracts import CapabilityToken, SandboxCommand, SandboxCommandKind
from .mutation_journal import MutationJournal


Handler = Callable[[Mapping[str, Any]], Awaitable[tuple[str, ...]]]


class SandboxWorld:
    def __init__(
        self,
        *,
        policy: CapabilityPolicy,
        repository: SubjectRepository | None = None,
        audit: ForensicStore | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        tts_handler: Handler | None = None,
        body_handler: Handler | None = None,
        network_handler: Handler | None = None,
        system_action_handler: Callable[[str, str], Any] | None = None,
        owner_id: str | None = None,
        mutation_journal: MutationJournal | None = None,
    ) -> None:
        self.policy = policy
        self.repository = repository
        self.audit = audit
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._tts_handler = tts_handler
        self._body_handler = body_handler
        self._network_handler = network_handler
        self._system_action_handler = system_action_handler
        self._owner_id = owner_id or f"sandbox:{uuid.uuid4()}"
        self.mutation_journal = mutation_journal or MutationJournal(clock=self._clock)
        self._execution_results: dict[str, Any] = {}
        self._idempotent: dict[str, tuple[str, WorldOutcome]] = {}
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._key_locks_guard = asyncio.Lock()

    def bind_system_action_handler(self, handler: Callable[[str, str], Any]) -> None:
        if not callable(handler):
            raise TypeError("system action handler must be callable")
        self._system_action_handler = handler

    def take_execution_result(self, command_id: str) -> Any | None:
        return self._execution_results.pop(str(command_id), None)

    def rollback_mutation(self, receipt_id: str) -> bool:
        """Rollback có fencing bằng postcondition hash.

        Nếu target đã bị sửa sau mutation, rollback trả False thay vì ghi đè thay đổi mới.
        """
        return self.mutation_journal.rollback(receipt_id)

    @staticmethod
    def _command_hash(command: SandboxCommand) -> str:
        return sha256_text(canonical_json({
            "action_id": command.action_id,
            "decision_id": command.decision_id,
            "kind": command.kind.value if isinstance(command.kind, SandboxCommandKind) else str(command.kind),
            "arguments": dict(command.arguments),
            "requested_capability": command.requested_capability,
            "simulate": command.simulate,
            "subject_id": command.subject_id,
            "thread_id": command.thread_id,
            "delivery_id": command.delivery_id,
        }))

    @staticmethod
    def _outcome_from_dict(payload: Mapping[str, Any]) -> WorldOutcome:
        return WorldOutcome(
            outcome_id=str(payload["outcome_id"]),
            action_id=str(payload["action_id"]),
            decision_id=str(payload["decision_id"]),
            status=OutcomeStatus(str(payload["status"])),
            observed_changes=tuple(str(item) for item in payload.get("observed_changes", ())),
            unintended_changes=tuple(str(item) for item in payload.get("unintended_changes", ())),
            denial_reason=None if payload.get("denial_reason") is None else str(payload.get("denial_reason")),
            evidence_refs=tuple(str(item) for item in payload.get("evidence_refs", ())),
            started_at=float(payload["started_at"]),
            completed_at=float(payload["completed_at"]),
            thread_id=str(payload.get("thread_id") or ""),
            delivery_id=str(payload.get("delivery_id") or ""),
        )

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._key_locks_guard:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
            return lock

    async def _claim_heartbeat(
        self,
        *,
        command: SandboxCommand,
        request_hash: str,
        claim: IdempotencyClaim,
        lease_seconds: float,
        stop: asyncio.Event,
        lost: asyncio.Event,
    ) -> None:
        if self.repository is None or not claim.acquired:
            return
        interval = max(0.25, min(2.0, lease_seconds / 3.0))
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                renewed = await asyncio.to_thread(
                    self.repository.renew_idempotency_claim,
                    scope="sandbox",
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    owner_id=self._owner_id,
                    generation=claim.generation,
                    lease_seconds=lease_seconds,
                )
            except Exception:
                lost.set()
                return
            if not renewed:
                lost.set()
                return

    async def apply(self, command: SandboxCommand, capability: CapabilityToken) -> WorldOutcome:
        started = self._clock()
        policy_decision = self.policy.authorize(command, capability, now=started)
        if not policy_decision.allowed:
            outcome = self._outcome(command, OutcomeStatus.DENIED, (), policy_decision.reason, started)
            self._record_denial(command, capability, outcome)
            return outcome

        request_hash = self._command_hash(command)
        lock = await self._lock_for(command.idempotency_key)
        async with lock:
            cached = self._idempotent.get(command.idempotency_key)
            if cached is not None:
                cached_hash, outcome = cached
                if cached_hash != request_hash:
                    raise EventIntegrityError(
                        f"sandbox idempotency key {command.idempotency_key} reused for a different command"
                    )
                return outcome

            claim = IdempotencyClaim("not_required", 0, 0.0)
            lease_seconds = max(10.0, self.policy.command_policy.max_runtime_seconds + 10.0)
            if self.repository is not None:
                persisted = self.repository.get_idempotent(
                    scope="sandbox", idempotency_key=command.idempotency_key, request_hash=request_hash
                )
                if persisted is not None:
                    outcome = self._outcome_from_dict(persisted)
                    self._idempotent[command.idempotency_key] = (request_hash, outcome)
                    return outcome

                wait_deadline = time.monotonic() + max(5.0, self.policy.command_policy.max_runtime_seconds + 5.0)
                while True:
                    claim = self.repository.claim_idempotency(
                        scope="sandbox",
                        idempotency_key=command.idempotency_key,
                        request_hash=request_hash,
                        owner_id=self._owner_id,
                        lease_seconds=lease_seconds,
                    )
                    if claim.status == "acquired":
                        break
                    if claim.status == "completed":
                        persisted = self.repository.get_idempotent(
                            scope="sandbox", idempotency_key=command.idempotency_key, request_hash=request_hash
                        )
                        if persisted is None:
                            raise EventIntegrityError("completed claim has no durable result")
                        outcome = self._outcome_from_dict(persisted)
                        self._idempotent[command.idempotency_key] = (request_hash, outcome)
                        return outcome
                    if time.monotonic() >= wait_deadline:
                        return self._outcome(
                            command, OutcomeStatus.FAILED, (), "idempotency_claim_timeout", self._clock()
                        )
                    await asyncio.sleep(0.05)

            heartbeat_stop = asyncio.Event()
            heartbeat_lost = asyncio.Event()
            heartbeat_task: asyncio.Task[None] | None = None
            if self.repository is not None and claim.acquired:
                heartbeat_task = asyncio.create_task(self._claim_heartbeat(
                    command=command,
                    request_hash=request_hash,
                    claim=claim,
                    lease_seconds=lease_seconds,
                    stop=heartbeat_stop,
                    lost=heartbeat_lost,
                ))
            try:
                outcome = await self._apply_once(
                    command, capability, decision=policy_decision, started=started
                )
                heartbeat_stop.set()
                if heartbeat_task is not None:
                    await heartbeat_task
                if heartbeat_lost.is_set():
                    raise EventIntegrityError(
                        f"sandbox idempotency lease lost for {command.idempotency_key} generation {claim.generation}"
                    )
                stored = self._record(
                    command,
                    capability,
                    outcome,
                    request_hash=request_hash,
                    claim_owner_id=self._owner_id if claim.acquired else "",
                    claim_generation=claim.generation if claim.acquired else 0,
                )
            except BaseException:
                heartbeat_stop.set()
                if heartbeat_task is not None and not heartbeat_task.done():
                    await heartbeat_task
                if self.repository is not None and claim.acquired:
                    self.repository.release_idempotency_claim(
                        scope="sandbox",
                        idempotency_key=command.idempotency_key,
                        owner_id=self._owner_id,
                        generation=claim.generation,
                    )
                raise
            self._idempotent[command.idempotency_key] = (request_hash, stored)
            return stored

    async def _apply_once(
        self,
        command: SandboxCommand,
        capability: CapabilityToken,
        *,
        decision: PolicyDecision,
        started: float,
    ) -> WorldOutcome:
        kind_value = command.kind.value if isinstance(command.kind, SandboxCommandKind) else str(command.kind)
        if command.simulate or kind_value == SandboxCommandKind.SIMULATE.value:
            return self._outcome(command, OutcomeStatus.SIMULATED, ("simulation_completed",), None, started)
        try:
            changes = await self._execute(command, decision, capability)
        except (asyncio.TimeoutError, subprocess.TimeoutExpired):
            return self._outcome(command, OutcomeStatus.FAILED, (), "runtime_timeout", started)
        except Exception as exc:
            return self._outcome(command, OutcomeStatus.FAILED, (), f"{type(exc).__name__}:{exc}", started)
        return self._outcome(command, OutcomeStatus.SUCCEEDED, changes, None, started)

    def _effective_output_limit(self, args: Mapping[str, Any], capability: CapabilityToken) -> int:
        requested = int(args.get("max_bytes") or self.policy.command_policy.max_output_bytes)
        token_limit = int(capability.scope.get("max_output_bytes") or self.policy.command_policy.max_output_bytes)
        return max(1, min(requested, token_limit, self.policy.command_policy.max_output_bytes))

    @staticmethod
    def _read_limited(path: Path, limit: int) -> tuple[bytes, bool]:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            data = os.read(descriptor, limit + 1)
        finally:
            os.close(descriptor)
        return data[:limit], len(data) > limit

    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_path = self.policy.revalidate_path(path)
        temp_path = safe_path.with_name(f".{safe_path.name}.{self._id_factory()}.tmp")
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.policy.revalidate_path(safe_path)
            os.replace(temp_path, safe_path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _run_command(self, argv: list[str]) -> tuple[str, ...]:
        preexec_fn = None
        if os.name == "posix":
            try:
                import resource

                memory_bytes = int(self.policy.command_policy.max_memory_mb) * 1024 * 1024
                cpu_seconds = max(1, int(self.policy.command_policy.max_runtime_seconds))

                def limit_resources() -> None:
                    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
                    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))

                preexec_fn = limit_resources
            except (ImportError, AttributeError, ValueError):
                preexec_fn = None

        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(
                argv,
                cwd=self.policy.command_policy.workspace_root,
                env=self.policy.filtered_environment(),
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=self.policy.command_policy.max_runtime_seconds,
                shell=False,
                check=False,
                start_new_session=True,
                preexec_fn=preexec_fn,
            )
            output.seek(0)
            data = output.read(self.policy.command_policy.max_output_bytes)
        digest = hashlib.sha256(data).hexdigest()
        if completed.returncode != 0:
            raise RuntimeError(f"command_exit:{completed.returncode}:output_sha256:{digest}")
        return (f"command_exit:{completed.returncode}", f"output_sha256:{digest}", f"output_bytes:{len(data)}")

    async def _execute(
        self,
        command: SandboxCommand,
        decision: PolicyDecision,
        capability: CapabilityToken,
    ) -> tuple[str, ...]:
        kind = command.kind if isinstance(command.kind, SandboxCommandKind) else SandboxCommandKind(str(command.kind))
        args = dict(command.arguments)
        if kind is SandboxCommandKind.SYSTEM_ACTION:
            if self._system_action_handler is None:
                raise RuntimeError("system_action_handler_unavailable")
            action_name = str(args.get("action_name") or "")
            action_input = str(args.get("action_input") or "")
            result = await asyncio.to_thread(self._system_action_handler, action_name, action_input)
            self._execution_results[command.command_id] = result
            success = bool(getattr(result, "success", True))
            status = str(getattr(getattr(result, "status", ""), "value", getattr(result, "status", "")) or "")
            message = str(getattr(result, "message", "") or "")
            if not success:
                raise RuntimeError(f"system_action_failed:{action_name}:{message[:160]}")
            return (f"system_action:{action_name}", f"handler_status:{status or 'completed'}")
        if kind is SandboxCommandKind.READ_FILE:
            path = self.policy.revalidate_path(decision.resolved_path or "")
            max_bytes = self._effective_output_limit(args, capability)
            data, truncated = await asyncio.to_thread(self._read_limited, path, max_bytes)
            digest = hashlib.sha256(data).hexdigest()
            return (
                f"file_read:{path.name}",
                f"bytes:{len(data)}",
                f"truncated:{str(truncated).lower()}",
                f"sha256:{digest}",
            )
        if kind is SandboxCommandKind.WRITE_FILE:
            path = self.policy.revalidate_path(decision.resolved_path or "")
            content = args.get("content")
            if isinstance(content, bytes):
                data = content
            else:
                data = str(content or "").encode("utf-8")
            max_bytes = self._effective_output_limit(args, capability)
            if len(data) > max_bytes:
                raise ValueError("write exceeds allowed output limit")
            pre_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
            receipt_id, backup_path, existed_before = await asyncio.to_thread(self.mutation_journal.prepare, path)
            await asyncio.to_thread(self._atomic_write, path, data)
            receipt = await asyncio.to_thread(
                self.mutation_journal.finalize,
                receipt_id=receipt_id,
                target=path,
                backup_path=backup_path,
                existed_before=existed_before,
                pre_hash=pre_hash,
            )
            return (
                f"file_written:{path.name}",
                f"bytes:{len(data)}",
                f"precondition_hash:{receipt.precondition_hash}",
                f"postcondition_hash:{receipt.postcondition_hash}",
                f"rollback_handle:{receipt.receipt_id}",
            )
        if kind is SandboxCommandKind.RUN_COMMAND:
            argv = [str(item) for item in args["argv"]]
            return await asyncio.to_thread(self._run_command, argv)
        if kind is SandboxCommandKind.TTS:
            if self._tts_handler is None:
                raise RuntimeError("tts_broker_unavailable")
            return await self._tts_handler(args)
        if kind is SandboxCommandKind.BODY_FRAME:
            if self._body_handler is None:
                raise RuntimeError("body_adapter_unavailable")
            return await self._body_handler(args)
        if kind is SandboxCommandKind.HTTP_REQUEST:
            if self._network_handler is None:
                raise RuntimeError("network_transport_not_injected")
            # Handler V6 phải trả metadata quan sát được, không trả raw body vào Mind.
            return await self._network_handler(args)
        return ("no_op",)

    def _outcome(
        self,
        command: SandboxCommand,
        status: OutcomeStatus,
        changes: tuple[str, ...],
        denial_reason: str | None,
        started: float,
    ) -> WorldOutcome:
        return WorldOutcome(
            outcome_id=self._id_factory(),
            action_id=command.action_id,
            decision_id=command.decision_id,
            status=status,
            observed_changes=changes,
            unintended_changes=(),
            denial_reason=denial_reason,
            evidence_refs=(command.command_id, command.idempotency_key),
            started_at=started,
            completed_at=self._clock(),
            thread_id=command.thread_id,
            delivery_id=command.delivery_id,
        )

    def _append_audit(
        self,
        command: SandboxCommand,
        capability: CapabilityToken,
        outcome: WorldOutcome,
        *,
        claim_generation: int = 0,
    ) -> None:
        if self.audit is None:
            return
        subject_id = command.subject_id or capability.subject_id
        self.audit.append(AuditEvent(
            event_id=self._id_factory(),
            subject_id=subject_id,
            kind=AuditEventKind.POLICY,
            entity_id=command.command_id,
            created_at=self._clock(),
            payload={
                "allowed": outcome.status is not OutcomeStatus.DENIED,
                "reason": outcome.denial_reason or "",
                "policy_version": self.policy.command_policy.policy_version,
                "token_id": capability.token_id,
                "token_issuer": capability.issuer,
                "token_key_id": capability.key_id,
                "requested_capability": command.requested_capability,
                "claim_generation": claim_generation,
            },
            causal_refs=(command.action_id, command.decision_id),
            algorithm_version=self.policy.command_policy.policy_version,
        ))
        self.audit.append(AuditEvent(
            event_id=self._id_factory(),
            subject_id=subject_id,
            kind=AuditEventKind.WORLD_OUTCOME,
            entity_id=outcome.outcome_id,
            created_at=self._clock(),
            payload=outcome.to_dict(),
            causal_refs=(command.command_id, command.action_id, command.decision_id),
            algorithm_version=self.policy.command_policy.policy_version,
        ))

    def _record_denial(
        self,
        command: SandboxCommand,
        capability: CapabilityToken,
        outcome: WorldOutcome,
    ) -> None:
        if self.repository is not None:
            self.repository.append_world_outcome(
                outcome_id=outcome.outcome_id,
                action_id=outcome.action_id,
                decision_id=outcome.decision_id,
                status=outcome.status.value,
                payload=outcome.to_dict(),
            )
        self._append_audit(command, capability, outcome)

    def _record(
        self,
        command: SandboxCommand,
        capability: CapabilityToken,
        outcome: WorldOutcome,
        *,
        request_hash: str,
        claim_owner_id: str = "",
        claim_generation: int = 0,
    ) -> WorldOutcome:
        stored_outcome = outcome
        if self.repository is not None:
            stored_payload, created = self.repository.record_sandbox_outcome(
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                outcome_id=outcome.outcome_id,
                action_id=outcome.action_id,
                decision_id=outcome.decision_id,
                status=outcome.status.value,
                payload=outcome.to_dict(),
                claim_owner_id=claim_owner_id,
                claim_generation=claim_generation,
            )
            if not created:
                stored_outcome = self._outcome_from_dict(stored_payload)
        self._append_audit(command, capability, stored_outcome, claim_generation=claim_generation)
        return stored_outcome
