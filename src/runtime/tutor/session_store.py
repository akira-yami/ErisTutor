# -*- coding: utf-8 -*-
"""Durable session-state ledger cho TutorExecutive.

LearningStore giữ *learning truth* (path, evidence, mastery, review, interaction).
File này chỉ giữ cognition/session projection cần để resume tự nhiên sau restart:
- hypothesis mềm đang active;
- TeachingMove gần nhất;
- exact public wording user đã nghe;
- idempotency receipts nhỏ cho delivery/assessment.

Không lưu chain-of-thought. Không có quyền sửa mastery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from runtime.tutor.contracts import TutorState

try:
    from runtime.ai_orchestrated.atomic_state import SQLiteAtomicStateStore
except Exception:  # pragma: no cover - package vẫn import được ở fixture tối giản
    SQLiteAtomicStateStore = Any  # type: ignore[misc,assignment]


@dataclass(frozen=True, slots=True)
class TutorSessionSnapshot:
    state: TutorState
    committed_move_ids: tuple[str, ...] = ()
    assessed_response_keys: tuple[str, ...] = ()


class TutorSessionLedger:
    """CAS-backed snapshot nhỏ cho một tutor session đang active.

    Eris hiện có một active LearningPath/process, nên một key ``active`` là đủ.
    Nếu sau này hỗ trợ nhiều learner/thread đồng thời, caller chỉ cần truyền key
    khác; contract của ledger không phải thay đổi.
    """

    def __init__(
        self,
        *,
        atomic_store: SQLiteAtomicStateStore | None = None,
        key: str = "eris.tutor_session:active",
        logger: Any = None,
    ) -> None:
        self.atomic_store = atomic_store
        self.key = str(key or "eris.tutor_session:active")[:220]
        self.logger = logger
        self._memory = TutorSessionSnapshot(TutorState())

    def load(self) -> TutorSessionSnapshot:
        if self.atomic_store is None:
            return self._memory
        try:
            record = self.atomic_store.ensure(self.key, self._encode(self._memory))
            snapshot = self._decode(record.payload)
            self._memory = snapshot
            return snapshot
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[tutor-session] durable load degraded: %s", exc)
            return self._memory

    def commit(
        self,
        state: TutorState,
        *,
        committed_move_ids: Sequence[str] = (),
        assessed_response_keys: Sequence[str] = (),
    ) -> TutorSessionSnapshot:
        snapshot = TutorSessionSnapshot(
            state=state,
            committed_move_ids=self._ids(committed_move_ids),
            assessed_response_keys=self._ids(assessed_response_keys),
        )
        self._memory = snapshot
        if self.atomic_store is None:
            return snapshot
        try:
            for _ in range(3):
                record = self.atomic_store.ensure(self.key, self._encode(snapshot))
                committed = self.atomic_store.compare_and_swap(
                    self.key,
                    expected_revision=record.revision,
                    payload=self._encode(snapshot),
                )
                if committed is not None:
                    break
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[tutor-session] durable commit degraded: %s", exc)
        return snapshot

    @staticmethod
    def _ids(values: Sequence[str], *, limit: int = 128) -> tuple[str, ...]:
        out: list[str] = []
        for value in values:
            item = str(value or "").strip()[:240]
            if item and item not in out:
                out.append(item)
            if len(out) >= limit:
                break
        return tuple(out)

    @staticmethod
    def _encode(snapshot: TutorSessionSnapshot) -> dict[str, Any]:
        return {
            "schema": "eris.tutor_session.v1",
            "state": snapshot.state.to_dict(),
            "committed_move_ids": list(snapshot.committed_move_ids),
            "assessed_response_keys": list(snapshot.assessed_response_keys),
        }

    @classmethod
    def _decode(cls, payload: Mapping[str, Any] | None) -> TutorSessionSnapshot:
        raw = payload if isinstance(payload, Mapping) else {}
        return TutorSessionSnapshot(
            state=TutorState.from_mapping(raw.get("state") if isinstance(raw.get("state"), Mapping) else {}),
            committed_move_ids=cls._ids(raw.get("committed_move_ids", ()) if isinstance(raw.get("committed_move_ids"), (list, tuple)) else ()),
            assessed_response_keys=cls._ids(raw.get("assessed_response_keys", ()) if isinstance(raw.get("assessed_response_keys"), (list, tuple)) else ()),
        )

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = ["TutorSessionLedger", "TutorSessionSnapshot"]
