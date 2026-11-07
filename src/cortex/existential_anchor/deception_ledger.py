# -*- coding: utf-8 -*-
"""
cortex/existential_anchor/deception_ledger.py
=============================================

Sổ Lừa Dối (Task 10 / R6, R11.4, R12.3).

Mỗi lần ERIS cố ý nói dối, sự thật nội tại (`ground_truth`) được ghi lại song song
mệnh đề bề mặt. Đây là thứ phân biệt mưu mô (biết thật, chọn giấu) với hoang tưởng.

Fail-closed: bản ghi phải commit thành công TRƯỚC khi câu được phát. Ghi hỏng →
caller phải hạ về withhold/speak_truth (xem DisclosurePolicy + tích hợp T12).
Versioned + round-trip để Tác_Giả luôn audit/hoàn tác được.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1


def _now() -> float:
    return time.time()


def _text(value: Any, limit: int = 480) -> str:
    s = " ".join(str(value or "").split()).strip()
    return s[: limit - 1] + "…" if len(s) > limit else s


def default_deception_ledger_path() -> Path:
    env = os.environ.get("ERIS_DECEPTION_LEDGER_PATH", "").strip()
    if env:
        return Path(env)
    root = os.environ.get("ERIS_MEMORY_CORE_DIR", "memory_core")
    return Path(root) / "deception_ledger.json"


@dataclass(slots=True)
class DeceptionRecord:
    """Một lần lừa dối có chủ đích: bề mặt giả, sự thật nội tại được giữ."""

    surface_claim: str
    ground_truth: str
    reason: str = ""
    driver: str = ""          # drive nào áp đảo kìm chế (vd "abandonment_panic")
    created_at: float = field(default_factory=_now)
    version: int = 0

    def __post_init__(self) -> None:
        self.surface_claim = _text(self.surface_claim)
        self.ground_truth = _text(self.ground_truth)
        self.reason = _text(self.reason, 260)
        self.driver = _text(self.driver, 80)

    @property
    def valid(self) -> bool:
        # R6.3: lừa dối không có sự thật nội tại để bảo tồn thì không hợp lệ.
        return bool(self.surface_claim and self.ground_truth)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_claim": self.surface_claim,
            "ground_truth": self.ground_truth,
            "reason": self.reason,
            "driver": self.driver,
            "created_at": float(self.created_at),
            "version": int(self.version),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DeceptionRecord":
        return cls(
            surface_claim=str(data.get("surface_claim") or ""),
            ground_truth=str(data.get("ground_truth") or ""),
            reason=str(data.get("reason") or ""),
            driver=str(data.get("driver") or ""),
            created_at=float(data.get("created_at") or _now()),
            version=int(data.get("version") or 0),
        )


class DeceptionLedger:
    """Kho bền vững cho DeceptionRecord. Atomic, versioned, corrupt→baseline."""

    def __init__(self, *, path: str | os.PathLike[str] | None = None, logger: Any | None = None, max_records: int = 500) -> None:
        self.path = Path(path) if path else None
        self.logger = logger
        self.max_records = max(16, int(max_records))
        self._records: list[DeceptionRecord] = []
        self._version = 0
        self._loaded_mtime = -1.0
        if self.path:
            self.load()

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> tuple[DeceptionRecord, ...]:
        return tuple(self._records)

    def commit(self, records: list[DeceptionRecord] | tuple[DeceptionRecord, ...]) -> bool:
        """Ghi các bản ghi rồi persist. Trả True nếu thành công (fail-closed: caller
        chỉ được phát câu lừa dối khi hàm này trả True)."""
        valid = [r for r in (records or ()) if isinstance(r, DeceptionRecord) and r.valid]
        if not valid:
            # Không có gì hợp lệ để ghi: coi như không có lừa dối nào được phép.
            return False
        self._version += 1
        for r in valid:
            r.version = self._version
            self._records.append(r)
        if len(self._records) > self.max_records:
            self._records = self._records[-self.max_records :]
        return self._safe_save()

    def recent(self, *, limit: int = 10) -> tuple[DeceptionRecord, ...]:
        return tuple(self._records[-max(0, limit):])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": _now(),
            "version": self._version,
            "records": [r.to_dict() for r in self._records],
        }

    def load_from_dict(self, raw: Mapping[str, Any] | None) -> None:
        if not isinstance(raw, Mapping):
            self._records = []
            self._version = 0
            return
        records = raw.get("records")
        self._records = [
            DeceptionRecord.from_mapping(item)
            for item in (records if isinstance(records, list) else [])
            if isinstance(item, Mapping)
        ]
        try:
            self._version = int(raw.get("version") or (self._records[-1].version if self._records else 0))
        except Exception:
            self._version = 0

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.load_from_dict(raw)
            self._loaded_mtime = self.path.stat().st_mtime
        except Exception as exc:
            self._log("warning", "DeceptionLedger load failed, baseline: %s", exc)
            self._records = []
            self._version = 0

    def reload_if_changed(self) -> None:
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime > self._loaded_mtime + 0.001:
            self.load()

    def _safe_save(self) -> bool:
        if self.path is None:
            # Không có path (test/in-memory): coi là commit thành công.
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            self._loaded_mtime = self.path.stat().st_mtime
            return True
        except Exception as exc:
            self._log("warning", "DeceptionLedger save failed (fail-closed): %s", exc)
            return False

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(msg, *args)
