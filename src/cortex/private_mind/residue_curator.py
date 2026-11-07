# -*- coding: utf-8 -*-
"""Kho vụn nội tâm cho Inner Diary.

Mục tiêu của module này rất hẹp:
- nhặt mảnh cognition đã tồn tại nhưng không được public surface;
- giữ stimulus bị coalesce/busy/preempt dưới provenance ``unprocessed_stimulus``;
- cho một model nhỏ (Gemma) gom/dedupe/nén chúng thành diary/continuity candidate;
- giữ entry đủ lâu để Inner Diary không biến về ``(chưa có)`` chỉ vì tick mới nhất yên.

Model curator KHÔNG phải authority tạo suy nghĩ mới. Output của nó chỉ được chấp
nhận khi trỏ về residue id thật; stimulus chưa xử lý không được biến thành
"Eris đã nghĩ" chỉ bằng một câu văn đẹp.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "none"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except (TypeError, ValueError):
        return int(default)


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return max(0.0, min(1.0, float(default)))


def _short(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split()).strip()


def _stable_id(prefix: str, *parts: object) -> str:
    blob = "\x1f".join(str(x or "") for x in parts)
    return f"{prefix}:{hashlib.sha1(blob.encode('utf-8')).hexdigest()[:20]}"


@dataclass(slots=True)
class ResidueStoreConfig:
    enabled: bool = field(default_factory=lambda: _env_flag("ERIS_PRIVATE_RESIDUE_ENABLED", True))
    path: str = field(default_factory=lambda: str(os.getenv(
        "ERIS_PRIVATE_RESIDUE_PATH",
        os.path.join(os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core"), "inner_diary_residue.json"),
    ) or "memory_core/inner_diary_residue.json"))
    max_items: int = field(default_factory=lambda: max(24, _env_int("ERIS_PRIVATE_RESIDUE_MAX_ITEMS", 96)))
    max_entries: int = field(default_factory=lambda: max(12, _env_int("ERIS_PRIVATE_RESIDUE_MAX_ENTRIES", 64)))
    batch_threshold: int = field(default_factory=lambda: max(2, _env_int("ERIS_PRIVATE_RESIDUE_BATCH_THRESHOLD", 6)))
    high_salience: float = field(default_factory=lambda: _clamp01(_env_float("ERIS_PRIVATE_RESIDUE_HIGH_SALIENCE", 0.75)))
    consolidate_interval_seconds: float = field(default_factory=lambda: max(60.0, _env_float("ERIS_PRIVATE_RESIDUE_CONSOLIDATE_SEC", 600.0)))
    active_seconds: float = field(default_factory=lambda: max(600.0, _env_float("ERIS_INNER_DIARY_ENTRY_ACTIVE_SEC", 21600.0)))
    fading_seconds: float = field(default_factory=lambda: max(1800.0, _env_float("ERIS_INNER_DIARY_ENTRY_FADING_SEC", 86400.0)))


class DiaryResidueStore:
    """Append-ish spool + retained diary state, có provenance và persistence."""

    ACTUAL_COGNITIVE_KINDS = frozenset({
        "private_fragment",
        "private_judgment",
        "suppressed_private_candidate",
        "suppressed_public_candidate",
        "suppressed_expression_urge",
        "packed_fragment",
    })

    DIARY_KINDS = frozenset({"current", "reflection", "concern", "future", "desire"})

    def __init__(self, *, config: ResidueStoreConfig | None = None, logger: Any = None) -> None:
        self.config = config or ResidueStoreConfig()
        self.logger = logger
        self._lock = threading.RLock()
        self._items: list[dict[str, Any]] = []
        self._entries: list[dict[str, Any]] = []
        self._continuity: list[dict[str, Any]] = []
        self._last_consolidated_at = 0.0
        self._last_curator = {"configured": False, "used": False, "model": "", "error": ""}
        self._loaded = False
        self._load()

    @classmethod
    def from_env(cls, *, logger: Any = None) -> "DiaryResidueStore":
        return cls(config=ResidueStoreConfig(), logger=logger)

    # ---------------------------------------------------------------- capture

    def capture(
        self,
        *,
        kind: str,
        text: Any,
        source_ids: Sequence[object] = (),
        salience: float = 0.5,
        provenance: str = "private_runtime",
        lane_hint: str = "diary",
        metadata: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> str:
        if not self.config.enabled:
            return ""
        clean = _short(text, 640)
        if not clean:
            return ""
        now = time.time() if now is None else float(now)
        kind = _short(kind or "residue", 64)
        provenance = _short(provenance or "private_runtime", 96)
        lane_hint = "continuity" if str(lane_hint).strip().lower() == "continuity" else "diary"
        refs = tuple(dict.fromkeys(_short(x, 160) for x in source_ids if _short(x, 160)))[:24]
        key = _stable_id("residue", kind, _norm(clean), provenance)
        with self._lock:
            for item in reversed(self._items[-32:]):
                if item.get("id") == key or (
                    item.get("kind") == kind
                    and _norm(item.get("text")) == _norm(clean)
                    and item.get("provenance") == provenance
                ):
                    item["last_seen_at"] = round(now, 3)
                    item["salience"] = round(max(float(item.get("salience", 0.0) or 0.0), _clamp01(salience)), 3)
                    item["recurrence"] = int(item.get("recurrence", 1) or 1) + 1
                    item["source_ids"] = list(dict.fromkeys(list(item.get("source_ids") or []) + list(refs)))[:24]
                    self._persist_locked()
                    return str(item.get("id") or key)
            self._items.append({
                "id": key,
                "kind": kind,
                "text": clean,
                "source_ids": list(refs),
                "created_at": round(now, 3),
                "last_seen_at": round(now, 3),
                "salience": round(_clamp01(salience, 0.5), 3),
                "provenance": provenance,
                "lane_hint": lane_hint,
                "metadata": dict(metadata or {}),
                "recurrence": 1,
                "status": "pending",
                "curated_at": 0.0,
            })
            self._trim_locked(now)
            self._persist_locked()
        return key

    def capture_unprocessed_stimulus(
        self,
        *,
        text: Any,
        source_ids: Sequence[object] = (),
        reason: str,
        salience: float = 0.55,
        metadata: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> str:
        # Đây là stimulus CHƯA được cognition xử lý. Chỉ continuity, tuyệt đối
        # không tự render như một suy nghĩ đã xảy ra.
        meta = dict(metadata or {})
        meta["unprocessed_reason"] = _short(reason, 96)
        return self.capture(
            kind="unprocessed_stimulus",
            text=text,
            source_ids=source_ids,
            salience=salience,
            provenance="unprocessed_stimulus",
            lane_hint="continuity",
            metadata=meta,
            now=now,
        )

    # --------------------------------------------------------------- curate

    def should_curate(self, *, now: float | None = None) -> bool:
        if not self.config.enabled:
            return False
        now = time.time() if now is None else float(now)
        with self._lock:
            pending = [item for item in self._items if item.get("status") == "pending"]
            if not pending:
                return False
            if len(pending) >= self.config.batch_threshold:
                return True
            if max((float(item.get("salience", 0.0) or 0.0) for item in pending), default=0.0) >= self.config.high_salience:
                return True
            return (now - self._last_consolidated_at) >= self.config.consolidate_interval_seconds

    def curator_packet(self, *, limit: int = 24) -> dict[str, Any]:
        with self._lock:
            pending = [dict(item) for item in self._items if item.get("status") == "pending"]
        pending.sort(key=lambda item: (float(item.get("salience", 0.0) or 0.0), float(item.get("last_seen_at", 0.0) or 0.0)), reverse=True)
        rows = []
        for item in pending[: max(1, limit)]:
            rows.append({
                "id": item.get("id"),
                "kind": item.get("kind"),
                "text": _short(item.get("text"), 420),
                "salience": item.get("salience", 0.0),
                "recurrence": item.get("recurrence", 1),
                "provenance": item.get("provenance", ""),
                "lane_hint": item.get("lane_hint", "diary"),
                "source_ids": list(item.get("source_ids") or [])[:12],
            })
        return {
            "schema": "eris.private_residue.packet.v1",
            "rule": (
                "unprocessed_stimulus is not a thought; it may only become continuity or discard. "
                "Diary text must be a faithful compression of actual cognitive residue."
            ),
            "items": rows,
        }

    def apply_curator_output(
        self,
        output: Mapping[str, Any] | None,
        *,
        curator_meta: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> int:
        now = time.time() if now is None else float(now)
        decisions = list(output.get("decisions") or []) if isinstance(output, Mapping) else []
        applied = 0
        with self._lock:
            index = {str(item.get("id") or ""): item for item in self._items}
            for raw in decisions[:12]:
                if not isinstance(raw, Mapping):
                    continue
                residue_ids = [str(x) for x in (raw.get("residue_ids") or raw.get("source_ids") or []) if str(x) in index][:12]
                if not residue_ids:
                    continue
                sources = [index[rid] for rid in residue_ids]
                action = str(raw.get("action") or "discard").strip().lower()
                if action not in {"diary", "continuity", "discard"}:
                    action = "discard"
                actual = any(str(item.get("kind") or "") in self.ACTUAL_COGNITIVE_KINDS for item in sources)
                # Không cho Gemma nâng một stimulus chưa từng được DeepSeek/Subject
                # xử lý thành câu "tôi đã nghĩ". Nó chỉ được giữ để cognition sau đọc.
                if action == "diary" and not actual:
                    action = "continuity"
                    # Không dùng câu văn do curator viết lại cho stimulus chưa từng
                    # được cognition xử lý. Giữ nguyên material gốc để provenance
                    # không bị biến thành một "suy nghĩ của Eris" giả.
                    text = _short(" ".join(str(item.get("text") or "") for item in sources), 420)
                else:
                    text = _short(raw.get("text"), 420)
                if action != "discard" and not text:
                    action = "discard"
                kind = str(raw.get("kind") or "reflection").strip().lower()
                if kind not in self.DIARY_KINDS:
                    kind = "reflection"
                confidence = _clamp01(raw.get("confidence"), 0.65)
                novelty = _clamp01(raw.get("novelty"), 0.55)
                salience = max(float(item.get("salience", 0.0) or 0.0) for item in sources)
                provenance = sorted({str(item.get("provenance") or "") for item in sources if item.get("provenance")})
                if action == "diary":
                    self._upsert_entry_locked({
                        "id": _stable_id("diary", kind, _norm(text)),
                        "kind": kind,
                        "text": text,
                        "residue_ids": residue_ids,
                        "source_ids": list(dict.fromkeys(sum((list(item.get("source_ids") or []) for item in sources), [])))[:24],
                        "created_at": round(now, 3),
                        "last_reinforced_at": round(now, 3),
                        "salience": round(_clamp01(salience), 3),
                        "confidence": round(confidence, 3),
                        "novelty": round(novelty, 3),
                        "state": "active",
                        "provenance": provenance,
                        "curator": dict(curator_meta or {}),
                    })
                    applied += 1
                elif action == "continuity":
                    self._upsert_continuity_locked({
                        "id": _stable_id("continuity", _norm(text)),
                        "text": text,
                        "residue_ids": residue_ids,
                        "created_at": round(now, 3),
                        "last_reinforced_at": round(now, 3),
                        "salience": round(_clamp01(salience), 3),
                        "provenance": provenance,
                    })
                    applied += 1
                for item in sources:
                    item["status"] = "curated" if action != "discard" else "discarded"
                    item["curated_at"] = round(now, 3)
            self._last_consolidated_at = now
            self._last_curator = {
                "configured": bool((curator_meta or {}).get("configured", False)),
                "used": bool(decisions),
                "model": _short((curator_meta or {}).get("model"), 96),
                "error": _short((curator_meta or {}).get("error"), 160),
            }
            self._trim_locked(now)
            self._persist_locked()
        return applied

    def deterministic_consolidate(self, *, now: float | None = None) -> int:
        """Fallback trung thực khi Gemma vắng/lỗi.

        Mảnh cognition thật có thể được giữ nguyên văn ngắn như diary residue;
        stimulus chưa xử lý chỉ đi vào continuity. Không sáng tác thêm câu nào.
        """
        now = time.time() if now is None else float(now)
        packet = self.curator_packet(limit=12)
        decisions: list[dict[str, Any]] = []
        for item in packet.get("items", []):
            kind = str(item.get("kind") or "")
            action = "diary" if kind in self.ACTUAL_COGNITIVE_KINDS else "continuity"
            diary_kind = "reflection"
            if kind == "private_judgment":
                diary_kind = "current"
            elif "candidate" in kind or "urge" in kind:
                diary_kind = "desire"
            decisions.append({
                "residue_ids": [item.get("id")],
                "action": action,
                "kind": diary_kind,
                "text": _short(item.get("text"), 300),
                "confidence": 0.62,
                "novelty": 0.5,
            })
            if len(decisions) >= 4:
                break
        return self.apply_curator_output(
            {"decisions": decisions},
            curator_meta={"configured": False, "model": "deterministic", "error": ""},
            now=now,
        )

    # ------------------------------------------------------------- projection

    def diary_entries(self, *, now: float | None = None, limit: int = 10) -> list[dict[str, Any]]:
        now = time.time() if now is None else float(now)
        with self._lock:
            self._age_entries_locked(now)
            entries = [dict(item) for item in self._entries if item.get("state") in {"active", "fading"}]
        entries.sort(key=lambda item: (item.get("state") == "active", float(item.get("last_reinforced_at", 0.0) or 0.0)), reverse=True)
        return entries[: max(1, limit)]

    def continuity_for_prompt(self, *, now: float | None = None, limit: int = 6) -> list[dict[str, Any]]:
        now = time.time() if now is None else float(now)
        with self._lock:
            rows = [dict(item) for item in self._continuity]
            # Pending unprocessed stimuli cũng được chiếu vào dossier ngay cả trước
            # khi Gemma kịp consolidate, nhưng luôn có nhãn provenance rõ.
            rows.extend({
                "id": item.get("id"),
                "text": item.get("text"),
                "salience": item.get("salience", 0.0),
                "provenance": [item.get("provenance")],
                "created_at": item.get("created_at", 0.0),
                "last_reinforced_at": item.get("last_seen_at", 0.0),
                "unprocessed": True,
            } for item in self._items if item.get("status") == "pending" and item.get("lane_hint") == "continuity")
        rows = [row for row in rows if now - float(row.get("last_reinforced_at", row.get("created_at", 0.0)) or 0.0) <= self.config.fading_seconds]
        rows.sort(key=lambda item: (float(item.get("salience", 0.0) or 0.0), float(item.get("last_reinforced_at", 0.0) or 0.0)), reverse=True)
        return rows[: max(1, limit)]

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else float(now)
        with self._lock:
            self._age_entries_locked(now)
            pending = [item for item in self._items if item.get("status") == "pending"]
            return {
                "schema": "eris.private_residue.v1",
                "pending_count": len(pending),
                "pending_high_salience": round(max((float(item.get("salience", 0.0) or 0.0) for item in pending), default=0.0), 3),
                "last_consolidated_at": round(float(self._last_consolidated_at or 0.0), 3),
                "curator": dict(self._last_curator),
                "diary_entries": self.diary_entries(now=now, limit=12),
                "continuity_candidates": self.continuity_for_prompt(now=now, limit=8),
            }

    # --------------------------------------------------------------- internal

    def _upsert_entry_locked(self, entry: dict[str, Any]) -> None:
        for current in self._entries:
            if current.get("id") == entry.get("id") or _norm(current.get("text")) == _norm(entry.get("text")):
                current["last_reinforced_at"] = entry["last_reinforced_at"]
                current["salience"] = max(float(current.get("salience", 0.0) or 0.0), float(entry.get("salience", 0.0) or 0.0))
                current["confidence"] = max(float(current.get("confidence", 0.0) or 0.0), float(entry.get("confidence", 0.0) or 0.0))
                current["novelty"] = max(float(current.get("novelty", 0.0) or 0.0), float(entry.get("novelty", 0.0) or 0.0))
                current["state"] = "active"
                current["residue_ids"] = list(dict.fromkeys(list(current.get("residue_ids") or []) + list(entry.get("residue_ids") or [])))[:24]
                current["source_ids"] = list(dict.fromkeys(list(current.get("source_ids") or []) + list(entry.get("source_ids") or [])))[:24]
                return
        self._entries.append(entry)

    def _upsert_continuity_locked(self, entry: dict[str, Any]) -> None:
        for current in self._continuity:
            if current.get("id") == entry.get("id") or _norm(current.get("text")) == _norm(entry.get("text")):
                current["last_reinforced_at"] = entry["last_reinforced_at"]
                current["salience"] = max(float(current.get("salience", 0.0) or 0.0), float(entry.get("salience", 0.0) or 0.0))
                current["residue_ids"] = list(dict.fromkeys(list(current.get("residue_ids") or []) + list(entry.get("residue_ids") or [])))[:24]
                return
        self._continuity.append(entry)

    def _age_entries_locked(self, now: float) -> None:
        for item in self._entries:
            age = max(0.0, now - float(item.get("last_reinforced_at", item.get("created_at", now)) or now))
            if age <= self.config.active_seconds:
                item["state"] = "active"
            elif age <= self.config.fading_seconds:
                item["state"] = "fading"
            else:
                item["state"] = "archived"

    def _trim_locked(self, now: float) -> None:
        self._age_entries_locked(now)
        if len(self._items) > self.config.max_items:
            # Giữ pending/high-salience trước, sau đó mới theo recency.
            self._items.sort(key=lambda item: (
                item.get("status") == "pending",
                float(item.get("salience", 0.0) or 0.0),
                float(item.get("last_seen_at", 0.0) or 0.0),
            ), reverse=True)
            self._items = self._items[: self.config.max_items]
        if len(self._entries) > self.config.max_entries:
            self._entries.sort(key=lambda item: (
                item.get("state") != "archived",
                float(item.get("last_reinforced_at", 0.0) or 0.0),
            ), reverse=True)
            self._entries = self._entries[: self.config.max_entries]
        if len(self._continuity) > self.config.max_entries:
            self._continuity.sort(key=lambda item: float(item.get("last_reinforced_at", 0.0) or 0.0), reverse=True)
            self._continuity = self._continuity[: self.config.max_entries]

    def _load(self) -> None:
        if self._loaded or not self.config.enabled:
            return
        self._loaded = True
        try:
            if not os.path.isfile(self.config.path):
                return
            with open(self.config.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, Mapping):
                return
            self._items = [dict(x) for x in data.get("items", []) if isinstance(x, Mapping)][-self.config.max_items :]
            self._entries = [dict(x) for x in data.get("entries", []) if isinstance(x, Mapping)][-self.config.max_entries :]
            self._continuity = [dict(x) for x in data.get("continuity", []) if isinstance(x, Mapping)][-self.config.max_entries :]
            self._last_consolidated_at = float(data.get("last_consolidated_at", 0.0) or 0.0)
            last_curator = data.get("last_curator")
            if isinstance(last_curator, Mapping):
                self._last_curator = dict(last_curator)
        except Exception as exc:
            self._log("warning", "Residue store load failed, starting fresh: %s", exc)
            self._items, self._entries, self._continuity = [], [], []

    def _persist_locked(self) -> None:
        if not self.config.enabled:
            return
        payload = {
            "schema": "eris.private_residue.v1",
            "updated_at": round(time.time(), 3),
            "last_consolidated_at": round(float(self._last_consolidated_at or 0.0), 3),
            "last_curator": dict(self._last_curator),
            "items": self._items,
            "entries": self._entries,
            "continuity": self._continuity,
        }
        directory = os.path.dirname(self.config.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".inner_residue_", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.config.path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        except Exception as exc:
            self._log("warning", "Residue store persist failed: %s", exc)

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(msg, *args)
            except Exception:
                pass


__all__ = ["DiaryResidueStore", "ResidueStoreConfig"]
