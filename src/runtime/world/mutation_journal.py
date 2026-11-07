# -*- coding: utf-8 -*-
"""Mutation journal có rollback cho sandbox file write.

Backup nằm trong thư mục journal riêng, không cho Mind đọc/ghi trực tiếp. Rollback
chỉ hợp lệ khi file hiện tại vẫn khớp postcondition hash; nếu đã có thay đổi mới,
rollback bị từ chối thay vì giẫm lên lịch sử sau đó.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    receipt_id: str
    target_path: str
    precondition_hash: str
    postcondition_hash: str
    backup_path: str | None
    existed_before: bool
    created_at: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MutationJournal:
    def __init__(self, root: str | Path | None = None, *, clock: Callable[[], float]) -> None:
        self.root = Path(root) if root is not None else Path(tempfile.mkdtemp(prefix="eris-mutation-journal-"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._receipts: dict[str, MutationReceipt] = {}

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"

    def prepare(self, target: Path) -> tuple[str, str | None, bool]:
        receipt_id = str(uuid.uuid4())
        existed = target.exists()
        backup: str | None = None
        if existed:
            backup_path = self.root / f"{receipt_id}.bak"
            backup_path.write_bytes(target.read_bytes())
            backup = str(backup_path)
        return receipt_id, backup, existed

    def finalize(self, *, receipt_id: str, target: Path, backup_path: str | None, existed_before: bool, pre_hash: str) -> MutationReceipt:
        receipt = MutationReceipt(
            receipt_id=receipt_id,
            target_path=str(target),
            precondition_hash=pre_hash,
            postcondition_hash=self._hash(target),
            backup_path=backup_path,
            existed_before=existed_before,
            created_at=self._clock(),
        )
        self._receipts[receipt_id] = receipt
        (self.root / f"{receipt_id}.json").write_text(json.dumps(receipt.to_dict(), sort_keys=True), encoding="utf-8")
        return receipt

    def get(self, receipt_id: str) -> MutationReceipt | None:
        return self._receipts.get(str(receipt_id))

    def rollback(self, receipt_id: str) -> bool:
        receipt = self.get(receipt_id)
        if receipt is None:
            return False
        target = Path(receipt.target_path)
        if self._hash(target) != receipt.postcondition_hash:
            return False
        if receipt.existed_before:
            if not receipt.backup_path:
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(receipt.backup_path).read_bytes())
        else:
            target.unlink(missing_ok=True)
        return self._hash(target) == receipt.precondition_hash


__all__ = ["MutationJournal", "MutationReceipt"]
