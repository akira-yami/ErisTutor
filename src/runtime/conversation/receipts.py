# -*- coding: utf-8 -*-
"""Idempotency ledger cho delivery receipt V12.

In-memory set là fast path. Khi có ``atomic_store``, delivery id còn được commit
vào idempotency ledger SQLite để restart hoặc hai worker không thể settle cùng
một delivery lần thứ hai rồi vô tình schedule duplicate continuation.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from .contracts import DeliveryReceipt


class DeliveryReceiptLedger:
    def __init__(self, *, atomic_store: Any | None = None, logger: Any = None, owner_id: str = "") -> None:
        self.atomic_store = atomic_store
        self.logger = logger
        self.owner_id = str(owner_id or f"conversation-receipt:{uuid.uuid4().hex}")
        self._lock = threading.RLock()
        self._seen: set[str] = set()

    @staticmethod
    def _key(delivery_id: str) -> str:
        return f"eris.conversation_delivery.v12:{str(delivery_id or '')[:180]}"

    def commit_once(self, receipt: DeliveryReceipt) -> bool:
        delivery_id = str(receipt.delivery_id or "")[:180]
        if not delivery_id:
            return False
        with self._lock:
            if delivery_id in self._seen:
                return False

        if self.atomic_store is not None:
            key = self._key(delivery_id)
            try:
                claim = self.atomic_store.claim_idempotency(
                    key,
                    owner=self.owner_id,
                    lease_seconds=10.0,
                )
                if claim.state != "acquired":
                    return False
                if not self.atomic_store.complete_idempotency(
                    key,
                    owner=self.owner_id,
                    result=receipt.to_dict(),
                ):
                    # Không coi receipt là committed nếu SQLite không xác nhận.
                    # Làm vậy tránh trường hợp process local nghĩ đã settle nhưng
                    # worker khác sau restart lại schedule lần hai.
                    try:
                        self.atomic_store.abandon_idempotency(key, owner=self.owner_id)
                    except Exception:
                        pass
                    return False
            except Exception as exc:  # noqa: BLE001
                self._log("warning", "[conversation-v12] durable receipt degraded: %s", exc)
                # Fail-soft về local ledger để UI/TTS không kẹt chỉ vì persistence
                # tạm lỗi. Public text vẫn không được replay từ state V12.

        with self._lock:
            if delivery_id in self._seen:
                return False
            self._seen.add(delivery_id)
            if len(self._seen) > 4096:
                self._seen = set(list(self._seen)[-2048:])
        return True

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(method):
            try:
                method(message, *args)
            except Exception:
                pass


__all__ = ["DeliveryReceiptLedger"]
