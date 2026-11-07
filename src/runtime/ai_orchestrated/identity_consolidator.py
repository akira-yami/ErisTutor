# -*- coding: utf-8 -*-
"""Persist/audit dài hạn cho Identity Kernel.

Consolidator không tự bẻ trait. Nó ghi snapshot, append-only ledger và bản tóm
tắt imprint để state có thể truy nguyên, rollback và kiểm tra qua restart mà
không tạo reducer thứ hai.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from runtime.workspace_manager import default_eris_home

from .contracts import RuntimeMindState, StateEvent


class IdentityConsolidator:
    def __init__(self, *, root: Path | None = None, logger: Any | None = None) -> None:
        self.root = root or (default_eris_home() / "state")
        self.logger = logger
        self._lock = threading.RLock()
        self._known_fingerprints: set[str] = set()
        self._last_write_at = 0.0
        self._accepted_since_write = 0
        self._load_known_fingerprints()

    def observe(self, state: RuntimeMindState, event: StateEvent, accepted: bool) -> None:
        if not accepted:
            return
        with self._lock:
            self._accepted_since_write += 1
            now = time.time()
            should_write = (
                self._accepted_since_write >= 12
                or now - self._last_write_at >= 6 * 3600.0
                or event.kind == "admin_control"
            )
            if not should_write:
                return
            self._accepted_since_write = 0
            self._last_write_at = now
        self.write(state, reason=event.kind)

    def write(self, state: RuntimeMindState, *, reason: str = "manual") -> dict[str, Any]:
        now = time.time()
        snapshot = {
            "schema": "eris.identity.snapshot.v1",
            "written_at": now,
            "reason": reason,
            "state_version": state.version,
            "turn_seq": state.last_applied_turn_seq,
            "learning_enabled": bool(getattr(state, "identity_learning_enabled", True)),
            "relationship": state.relationship.to_dict(),
            "personality": state.personality.to_dict(),
            "identity": state.identity.to_dict(),
            "latest_observation": dict(state.latest_observation),
            "latest_intention": dict(state.latest_intention),
        }
        imprints = {
            "schema": "eris.identity.imprints.v1",
            "written_at": now,
            "state_version": state.version,
            "items": [item.to_dict() for item in state.identity.imprints],
            "active_forms": state.identity.panel_view().get("active_forms", []),
        }
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._write_json_atomic(self.root / "identity_snapshot.json", snapshot)
            self._write_json_atomic(self.root / "formative_imprints.json", imprints)
            self._append_new_ledger(state)
            with self._lock:
                self._last_write_at = now
                self._accepted_since_write = 0
            return {"ok": True, "path": str(self.root / "identity_snapshot.json"), "written_at": now}
        except Exception as exc:
            self._log("warning", "[identity consolidator] write failed: %s", exc)
            return {"ok": False, "error": str(exc), "written_at": now}

    def panel_view(self) -> dict[str, Any]:
        return {
            "schema": "eris.identity.consolidator.v1",
            "last_write_at": self._last_write_at,
            "accepted_since_write": self._accepted_since_write,
            "snapshot_path": str(self.root / "identity_snapshot.json"),
            "ledger_path": str(self.root / "identity_events.jsonl"),
            "imprints_path": str(self.root / "formative_imprints.json"),
        }

    def _append_new_ledger(self, state: RuntimeMindState) -> None:
        ledger = [dict(item) for item in state.identity.ledger]
        personality = [dict(item) for item in state.personality.trajectory]
        merged = [{"stream": "identity", **item} for item in ledger] + [
            {"stream": "personality", **item} for item in personality
        ]
        merged.sort(key=lambda item: (float(item.get("timestamp", 0.0) or 0.0), int(item.get("turn_seq", 0) or 0)))
        path = self.root / "identity_events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for item in merged:
                fingerprint = self._fingerprint(item)
                if not fingerprint or fingerprint in self._known_fingerprints:
                    continue
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                self._known_fingerprints.add(fingerprint)
        # State ledger đã có trần nên chỉ cần giữ một cửa sổ fingerprint gần đây.
        if len(self._known_fingerprints) > 4096:
            self._load_known_fingerprints(reset=True)

    def _load_known_fingerprints(self, *, reset: bool = False) -> None:
        path = self.root / "identity_events.jsonl"
        if reset:
            self._known_fingerprints.clear()
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-4096:]
            for line in lines:
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    fingerprint = self._fingerprint(item)
                    if fingerprint:
                        self._known_fingerprints.add(fingerprint)
        except Exception as exc:
            self._log("debug", "[identity consolidator] ledger fingerprint load failed: %s", exc)

    @staticmethod
    def _fingerprint(item: dict[str, Any]) -> str:
        return "|".join(
            (
                f"{float(item.get('timestamp', 0.0) or 0.0):020.6f}",
                f"{int(item.get('turn_seq', 0) or 0):012d}",
                str(item.get("stream", "")),
                str(item.get("dimension", item.get("category", ""))),
                str(item.get("key", item.get("trait", ""))),
                str(item.get("turn_id", item.get("strategy_use_id", ""))),
            )
        )

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = ["IdentityConsolidator"]
