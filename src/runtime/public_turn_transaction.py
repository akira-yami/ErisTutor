# -*- coding: utf-8 -*-
"""Exactly-once guard for public turn side effects.

The in-process condition variable remains the fast path. When the durable social
state backend is enabled, the same idempotency key is also leased in SQLite so
two workers cannot both commit the same UI/TTS turn. A committed result is stored
and returned to retries after restart.

No local ledger can make an external speaker transactionally atomic with SQLite.
There is still a narrow crash window after the audio side effect and before the
receipt commit. The durable lease removes normal retries and concurrent double
playback, which is the practical failure mode this layer owns.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar
from weakref import WeakKeyDictionary

from runtime.ai_orchestrated.atomic_state import (
    SQLiteAtomicStateStore,
    atomic_store_from_env,
    unique_worker_id,
)


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class TurnCommitRecord(Generic[T]):
    idempotency_key: str
    result: T
    committed_at: float


class PublicTurnTransactionRegistry:
    def __init__(
        self,
        *,
        durable_store: SQLiteAtomicStateStore | None = None,
        owner_id: str = "",
        durable_lease_seconds: float = 30.0,
    ) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._inflight: set[str] = set()
        self._records: dict[str, TurnCommitRecord[Any]] = {}
        self._durable_store = durable_store
        self._owner_id = str(owner_id or unique_worker_id("public-turn"))
        self._durable_lease_seconds = max(1.0, min(300.0, float(durable_lease_seconds)))

    def run_once(self, idempotency_key: str, operation: Callable[[], T], *, wait_timeout: float = 5.0) -> T:
        key = str(idempotency_key or "").strip()
        if not key:
            return operation()

        deadline = time.monotonic() + max(0.05, float(wait_timeout))
        with self._condition:
            while key in self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise TimeoutError(f"public_turn_commit_inflight:{key}")
                self._condition.wait(timeout=remaining)
            existing = self._records.get(key)
            if existing is not None:
                return existing.result
            self._inflight.add(key)

        durable_acquired = False
        try:
            if self._durable_store is not None:
                while True:
                    claim = self._durable_store.claim_idempotency(
                        key,
                        owner=self._owner_id,
                        lease_seconds=self._durable_lease_seconds,
                    )
                    if claim.state == "committed":
                        result = claim.result
                        self._record_local(key, result)
                        return result
                    if claim.state == "acquired":
                        durable_acquired = True
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        raise TimeoutError(f"public_turn_commit_durable_inflight:{key}")
                    time.sleep(min(0.05, max(0.005, remaining)))

            result = operation()
            if self._durable_store is not None and durable_acquired:
                if not self._durable_store.complete_idempotency(
                    key,
                    owner=self._owner_id,
                    result=result,
                ):
                    raise RuntimeError(f"public_turn_commit_receipt_lost:{key}")
        except Exception:
            if self._durable_store is not None and durable_acquired:
                try:
                    self._durable_store.abandon_idempotency(key, owner=self._owner_id)
                except Exception:
                    pass
            with self._condition:
                self._inflight.discard(key)
                self._condition.notify_all()
            raise

        self._record_local(key, result)
        return result

    def _record_local(self, key: str, result: T) -> None:
        with self._condition:
            self._records[key] = TurnCommitRecord(
                idempotency_key=key,
                result=result,
                committed_at=time.time(),
            )
            self._inflight.discard(key)
            if len(self._records) > 512:
                oldest = sorted(self._records.values(), key=lambda item: item.committed_at)[:128]
                for record in oldest:
                    self._records.pop(record.idempotency_key, None)
            self._condition.notify_all()

    def get(self, idempotency_key: str) -> TurnCommitRecord[Any] | None:
        with self._condition:
            return self._records.get(str(idempotency_key or "").strip())


_REGISTRIES: "WeakKeyDictionary[Any, PublicTurnTransactionRegistry]" = WeakKeyDictionary()
_REGISTRY_LOCK = threading.RLock()
_FALLBACK_REGISTRY: PublicTurnTransactionRegistry | None = None


def _durable_public_turn_store() -> SQLiteAtomicStateStore | None:
    raw = str(os.getenv("ERIS_DURABLE_PUBLIC_TURNS", "1") or "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return None
    return atomic_store_from_env()


def _new_registry() -> PublicTurnTransactionRegistry:
    return PublicTurnTransactionRegistry(durable_store=_durable_public_turn_store())


def registry_for(owner: Any) -> PublicTurnTransactionRegistry:
    existing = getattr(owner, "_public_turn_transaction_registry", None)
    if isinstance(existing, PublicTurnTransactionRegistry):
        return existing
    registry = _new_registry()
    try:
        setattr(owner, "_public_turn_transaction_registry", registry)
        return registry
    except Exception:
        pass
    try:
        with _REGISTRY_LOCK:
            current = _REGISTRIES.get(owner)
            if current is None:
                _REGISTRIES[owner] = registry
                current = registry
            return current
    except Exception:
        global _FALLBACK_REGISTRY
        with _REGISTRY_LOCK:
            if _FALLBACK_REGISTRY is None:
                _FALLBACK_REGISTRY = _new_registry()
            return _FALLBACK_REGISTRY


__all__ = ["PublicTurnTransactionRegistry", "TurnCommitRecord", "registry_for"]
