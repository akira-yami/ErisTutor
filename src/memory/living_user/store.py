# -*- coding: utf-8 -*-
"""Persistence và mutation engine cho living user profile.

Store chỉ nhận ``ProfileOperation`` đã được validator thông qua. Nó không gọi LLM,
không đoán tâm lý và không tự tạo nội dung. Nhờ vậy mọi thay đổi đều audit được,
undo được và có thể giải thích bằng evidence.
"""
from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from memory.json_store_io import atomic_write_json, locked_path

from .models import (
    ProfileOperation,
    ProfileRecord,
    UserMemoryScope,
    clamp01,
    determine_memory_tier,
    normalize_path,
    now_ts,
    value_fingerprint,
)

SCHEMA_VERSION = 2


@dataclass(slots=True)
class UserProfileStoreConfig:
    max_records: int = 320
    max_history: int = 40
    autosave: bool = True
    inferred_decay_per_day: float = 0.006
    observed_decay_per_day: float = 0.002
    explicit_decay_per_day: float = 0.0
    forget_after_days: float = 120.0
    drop_below_confidence: float = 0.08
    sensitive_requires_explicit: bool = True
    forbidden_path_fragments: tuple[str, ...] = (
        "password", "credential", "api_key", "secret_key", "access_token", "refresh_token",
    )


@dataclass(slots=True)
class ApplyResult:
    changed: bool
    record_ids: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    version: int = 0
    # Thống kê tùy chọn cho batch curator. Giữ ở cuối để không phá caller cũ
    # đang khởi tạo ApplyResult bằng positional arguments.
    details: dict[str, Any] = field(default_factory=dict)


class UserProfileStore:
    """Kho profile canonical, versioned và thread-safe."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        config: UserProfileStoreConfig | None = None,
    ) -> None:
        self.path = Path(path)
        self.config = resolve_dependency(config, lambda: UserProfileStoreConfig())
        self._records: dict[str, ProfileRecord] = {}
        self._history: list[dict[str, Any]] = []
        self._audit: list[dict[str, Any]] = []
        self._version = 0
        self._lock = threading.RLock()
        self.load()

    @property
    def version(self) -> int:
        return self._version

    def all(self, *, include_inactive: bool = False) -> tuple[ProfileRecord, ...]:
        records = list(self._records.values())
        if not include_inactive:
            records = [r for r in records if r.is_current]
        return tuple(sorted(records, key=lambda r: (-r.trust_weight, -r.updated_at, r.path)))

    def get(self, path: str, *, scope: UserMemoryScope | None = None, include_inactive: bool = False) -> tuple[ProfileRecord, ...]:
        key = normalize_path(path)
        records = [r for r in self._records.values() if r.path == key]
        if scope is not None:
            records = [r for r in records if r.scope.identity == scope.identity]
        if not include_inactive:
            records = [r for r in records if r.is_current]
        return tuple(sorted(records, key=lambda r: (-r.trust_weight, -r.updated_at)))

    def query_prefix(self, prefix: str, *, scope: UserMemoryScope | None = None) -> tuple[ProfileRecord, ...]:
        root = normalize_path(prefix)
        return tuple(
            r for r in self.all()
            if (r.path == root or r.path.startswith(root + ".")) and (scope is None or r.scope.matches(scope))
        )

    def snapshot(self, *, include_sensitive: bool = False, limit: int = 120) -> dict[str, Any]:
        records = [
            r for r in self.all()
            if include_sensitive or r.sensitivity not in {"private", "sensitive"}
        ][: max(0, limit)]
        return {
            "schema_version": SCHEMA_VERSION,
            "version": self._version,
            "record_count": len(records),
            "records": [r.to_dict() for r in records],
        }

    def apply(self, operations: Iterable[ProfileOperation], *, checkpoint_reason: str = "") -> ApplyResult:
        ops = tuple(operations)
        if not ops:
            return ApplyResult(False, version=self._version)
        changed_ids: list[str] = []
        rejected: list[str] = []
        with self._lock:
            before = [r.to_dict() for r in self.all(include_inactive=True)]
            for op in ops:
                ok, ids, reason = self._apply_one(op)
                if ok:
                    changed_ids.extend(ids)
                    self._audit.append({"at": now_ts(), "operation": self._audit_safe_operation(op), "record_ids": list(ids)})
                else:
                    rejected.append(reason)
            if changed_ids:
                self._version += 1
                self._history.append({
                    "version": self._version,
                    "at": now_ts(),
                    "reason": checkpoint_reason or "profile_operations",
                    "records_before": before,
                })
                self._history = self._history[-self.config.max_history :]
                self._trim()
                if self.config.autosave:
                    self.save()
        return ApplyResult(bool(changed_ids), tuple(dict.fromkeys(changed_ids)), tuple(rejected), self._version)

    def _apply_one(self, op: ProfileOperation) -> tuple[bool, tuple[str, ...], str]:
        if not op.path:
            return False, (), "empty_path"
        if any(fragment in op.path for fragment in self.config.forbidden_path_fragments):
            return False, (), f"forbidden_secret_path:{op.path}"
        if not op.evidence_ids and op.op not in {"archive", "delete", "weaken"}:
            return False, (), f"missing_evidence:{op.path}"
        if self.config.sensitive_requires_explicit and op.sensitivity == "sensitive" and op.explicitness != "explicit":
            return False, (), f"sensitive_not_explicit:{op.path}"

        matches = self._match_records(op)
        now = now_ts()

        if op.op in {"archive", "delete", "supersede"}:
            if not matches:
                return False, (), f"target_not_found:{op.path}"
            status = "deleted" if op.op == "delete" else ("archived" if op.op == "archive" else "superseded")
            for record in matches:
                record.status = status  # type: ignore[assignment]
                record.updated_at = now
                record.metadata["mutation_reason"] = op.reason
            return True, tuple(r.id for r in matches), ""

        if op.op == "weaken":
            if not matches:
                return False, (), f"target_not_found:{op.path}"
            for record in matches:
                record.confidence = clamp01(record.confidence - max(0.03, 0.35 * op.confidence))
                record.updated_at = now
                if record.confidence < self.config.drop_below_confidence:
                    record.status = "archived"
            return True, tuple(r.id for r in matches), ""

        if op.op == "merge":
            if len(matches) < 2:
                return False, (), f"merge_needs_multiple:{op.path}"
            primary = max(matches, key=lambda r: (r.trust_weight, r.recurrence, r.updated_at))
            evidence = list(primary.evidence_ids)
            for record in matches:
                if record.id == primary.id:
                    continue
                evidence.extend(record.evidence_ids)
                record.status = "superseded"
                record.updated_at = now
                record.metadata["merged_into"] = primary.id
            primary.evidence_ids = tuple(dict.fromkeys((*evidence, *op.evidence_ids)))[:96]
            primary.recurrence += sum(r.recurrence for r in matches if r.id != primary.id)
            primary.confidence = clamp01(max(primary.confidence, op.confidence) + 0.04)
            primary.updated_at = now
            return True, tuple(r.id for r in matches), ""

        existing = self._best_match(op, matches)
        if existing is not None and self._same_value(existing.value, op.value):
            new_evidence = tuple(eid for eid in op.evidence_ids if eid not in existing.evidence_ids)
            if not new_evidence:
                return False, (), f"duplicate_evidence:{op.path}"
            existing.recurrence += 1
            if getattr(existing, "tier", "working") != "core_anchor":
                existing.tier = determine_memory_tier(existing.path, existing.explicitness, existing.recurrence, existing.value)
            existing.confidence = clamp01(max(existing.confidence * 0.97, op.confidence) + min(0.08, existing.recurrence * 0.008))
            existing.evidence_ids = tuple(dict.fromkeys((*existing.evidence_ids, *new_evidence)))[:96]
            existing.updated_at = now
            existing.last_confirmed_at = now
            existing.metadata.setdefault("reinforcement_sources", [])
            sources = list(existing.metadata.get("reinforcement_sources") or [])
            if op.source and op.source not in sources:
                sources.append(op.source)
            existing.metadata["reinforcement_sources"] = sources[-20:]
            return True, (existing.id,), ""

        # Suy luận yếu không được ghi đè fact user nói rõ. Khi evidence xung đột
        # nhưng chưa đủ mạnh, giữ một record disputed để dreaming/review xử lý sau.
        active_single = [
            record for record in self._records.values()
            if record.path == op.path and record.scope.identity == op.scope.identity
            and record.status == "active" and record.cardinality == "single"
        ] if op.cardinality == "single" else []
        conflicting = [record for record in active_single if not self._same_value(record.value, op.value)]
        if conflicting and not self._may_supersede(op, conflicting):
            disputed = self._new_record(op, now=now, status="disputed")
            disputed.metadata["conflicts_with"] = [record.id for record in conflicting]
            self._records[disputed.id] = disputed
            return True, (disputed.id,), ""

        # Single-valued update phải giữ lịch sử: mark record cũ là superseded.
        superseded_ids: list[str] = []
        if op.cardinality == "single":
            for record in active_single:
                record.status = "superseded"
                record.valid_until = now
                record.updated_at = now
                record.metadata["superseded_by_operation"] = op.reason or op.source
                superseded_ids.append(record.id)

        record = self._new_record(op, now=now, status="active")
        self._records[record.id] = record
        return True, tuple((*superseded_ids, record.id)), ""

    def _new_record(self, op: ProfileOperation, *, now: float, status: str) -> ProfileRecord:
        expires_at = float(op.metadata.get("expires_at")) if op.metadata.get("expires_at") is not None else None
        if expires_at is None and op.scope.kind == "session":
            expires_at = now + 86400.0
        elif expires_at is None and op.scope.kind == "temporary":
            expires_at = now + 86400.0 * 7.0
        tier = op.tier or determine_memory_tier(op.path, op.explicitness, 1, op.value)
        return ProfileRecord(
            id="",
            path=op.path,
            value=op.value,
            confidence=op.confidence,
            explicitness=op.explicitness,
            sensitivity=op.sensitivity,
            tier=tier,
            scope=op.scope,
            cardinality=op.cardinality,
            status=status,  # type: ignore[arg-type]
            evidence_ids=op.evidence_ids,
            recurrence=1,
            source=op.source,
            valid_from=float(op.metadata.get("valid_from")) if op.metadata.get("valid_from") is not None else now,
            valid_until=float(op.metadata.get("valid_until")) if op.metadata.get("valid_until") is not None else None,
            expires_at=expires_at,
            metadata={"reason": op.reason, **op.metadata},
        )

    @staticmethod
    def _may_supersede(op: ProfileOperation, existing: list[ProfileRecord]) -> bool:
        rank = {"inferred": 1, "observed": 2, "explicit": 3}
        strongest = max(existing, key=lambda r: (rank[r.explicitness], r.confidence, r.last_confirmed_at))
        if rank[op.explicitness] < rank[strongest.explicitness]:
            return False
        if op.explicitness == "explicit":
            return True
        # Pattern quan sát/suy luận chỉ thay pattern cùng hạng nếu mạnh hơn rõ ràng.
        return op.confidence >= strongest.confidence + 0.10

    @staticmethod
    def _audit_safe_operation(op: ProfileOperation) -> dict[str, Any]:
        payload = op.to_dict()
        if op.sensitivity in {"private", "sensitive"}:
            payload["value"] = "<redacted>"
            payload["old_value"] = "<redacted>" if payload.get("old_value") is not None else None
        return payload

    def _match_records(self, op: ProfileOperation) -> list[ProfileRecord]:
        records = [
            r for r in self._records.values()
            if r.path == op.path and r.scope.identity == op.scope.identity and r.status == "active"
        ]
        if op.old_value is not None:
            records = [r for r in records if self._same_value(r.value, op.old_value)]
        elif op.value is not None and op.cardinality == "multi":
            # Multi-valued path chỉ reinforce đúng value trùng. Nếu không có exact
            # match thì phải tạo record mới, không được "củng cố" value hàng xóm.
            records = [r for r in records if self._same_value(r.value, op.value)]
        return records

    @staticmethod
    def _best_match(op: ProfileOperation, matches: list[ProfileRecord]) -> ProfileRecord | None:
        if not matches:
            return None
        exact = [r for r in matches if UserProfileStore._same_value(r.value, op.value)]
        return max(exact or matches, key=lambda r: (r.trust_weight, r.updated_at))

    @staticmethod
    def _same_value(left: Any, right: Any) -> bool:
        return value_fingerprint(left) == value_fingerprint(right)

    def forget(self, path_or_prefix: str, *, scope: UserMemoryScope | None = None, hard: bool = False, reason: str = "user_request") -> int:
        root = normalize_path(path_or_prefix)
        if not root:
            return 0
        changed = 0
        with self._lock:
            matched_ids = []
            for record in list(self._records.values()):
                if not (record.path == root or record.path.startswith(root + ".")):
                    continue
                if scope is not None and record.scope.identity != scope.identity:
                    continue
                matched_ids.append(record.id)
                if hard:
                    self._records.pop(record.id, None)
                else:
                    record.status = "archived"
                    record.updated_at = now_ts()
                    record.metadata["forget_reason"] = reason
                changed += 1
            if changed:
                self._version += 1
                if hard:
                    # Hard delete phải scrub cả snapshot lịch sử và audit payload.
                    for entry in self._history:
                        rows = entry.get("records_before")
                        if isinstance(rows, list):
                            entry["records_before"] = [
                                row for row in rows
                                if not (isinstance(row, Mapping) and str(row.get("id") or "") in matched_ids)
                            ]
                    self._audit = [
                        row for row in self._audit
                        if not (isinstance(row, Mapping) and str((row.get("operation") or {}).get("path") or "").startswith(root))
                    ]
                self.save()
        return changed

    def hard_delete(self, record_ids: Iterable[str]) -> int:
        """Xóa vĩnh viễn các record theo ID khỏi working store, lịch sử và audit."""
        target_ids = {str(x) for x in record_ids if str(x).strip()}
        if not target_ids:
            return 0
        with self._lock:
            removed: list[str] = []
            for record_id in target_ids:
                if record_id not in self._records:
                    continue
                removed.append(record_id)
                self._records.pop(record_id, None)
            if removed:
                self._version += 1
                for entry in self._history:
                    rows = entry.get("records_before")
                    if isinstance(rows, list):
                        entry["records_before"] = [
                            row for row in rows
                            if not (isinstance(row, Mapping) and str(row.get("id") or "") in target_ids)
                        ]
                self._audit = [
                    row for row in self._audit
                    if not (isinstance(row, Mapping) and any(str(rid) in target_ids for rid in (row.get("record_ids") or ())))
                ]
                if self.config.autosave:
                    self.save()
        return len(removed)

    def correct(self, path: str, value: Any, *, evidence_ids: Iterable[str], scope: UserMemoryScope | None = None, confidence: float = 1.0) -> ApplyResult:
        return self.apply((ProfileOperation(
            op="update",
            path=path,
            value=value,
            confidence=confidence,
            explicitness="explicit",
            scope=scope or UserMemoryScope(),
            evidence_ids=tuple(evidence_ids),
            reason="Người dùng sửa trực tiếp memory.",
            source="user_correction",
        ),), checkpoint_reason="user_correction")

    def decay(self, *, now: float | None = None) -> int:
        current = float(now or now_ts())
        changed = 0
        cutoff = self.config.forget_after_days * 86400.0
        with self._lock:
            for record in self._records.values():
                if record.status != "active" or getattr(record, "tier", "working") == "core_anchor" or (record.explicitness == "explicit" and self.config.explicit_decay_per_day <= 0):
                    continue
                age = current - record.last_confirmed_at
                if age <= cutoff:
                    continue
                days = (age - cutoff) / 86400.0
                rate = self.config.observed_decay_per_day if record.explicitness == "observed" else self.config.inferred_decay_per_day
                new_conf = clamp01(record.confidence - rate * days)
                if abs(new_conf - record.confidence) > 1e-9:
                    record.confidence = new_conf
                    record.updated_at = current
                    changed += 1
                if record.confidence < self.config.drop_below_confidence:
                    record.status = "archived"
            if changed and self.config.autosave:
                self.save()
        return changed

    def compact(self, *, inactive_after_days: float = 90.0, now: float | None = None) -> int:
        """Thu gọn record inactive cũ nhưng giữ record active và lịch sử gần.

        Compression không được chạm explicit record đang hoạt động. Record disputed,
        superseded, archived hoặc deleted quá cũ mới bị bỏ khỏi projection. Evidence
        gốc vẫn thuộc event/semantic store canonical, không bị xóa lén tại đây.
        """
        current = float(now or now_ts())
        cutoff = current - max(1.0, float(inactive_after_days)) * 86400.0
        removed: list[str] = []
        with self._lock:
            for record_id, record in list(self._records.items()):
                if record.status == "active":
                    continue
                if record.updated_at >= cutoff:
                    continue
                removed.append(record_id)
                self._records.pop(record_id, None)
            if removed:
                self._version += 1
                # Snapshot lịch sử chỉ là undo window, không phải cold archive vô hạn.
                for entry in self._history:
                    rows = entry.get("records_before")
                    if isinstance(rows, list):
                        entry["records_before"] = [
                            row for row in rows
                            if not (isinstance(row, Mapping) and str(row.get("id") or "") in removed)
                        ]
                if self.config.autosave:
                    self.save()
        return len(removed)

    def revert_to(self, version: int) -> bool:
        with self._lock:
            entry = next((x for x in reversed(self._history) if int(x.get("version", -1)) == int(version)), None)
            if entry is None:
                return False
            records = entry.get("records_before")
            if not isinstance(records, list):
                return False
            self._records = {
                r.id: r for r in (ProfileRecord.from_dict(x) for x in records if isinstance(x, Mapping))
            }
            self._version += 1
            self.save()
            return True


    def apply_curator_operations(
        self,
        operations: Iterable[Mapping[str, Any]],
        *,
        protected_record_ids: Iterable[str] = (),
        checkpoint_reason: str = "living_user_curator",
    ) -> ApplyResult:
        """Áp dụng batch curator theo record ID một cách atomic.

        Curator là authority cấu trúc của working profile. Store không còn
        bảo vệ representation user-confirmed khỏi curator; authority evidence vẫn
        được giữ trong record/audit để meaning có thể được truy nguyên.
        """
        rows = tuple(dict(row) for row in operations if isinstance(row, Mapping))
        if not rows:
            return ApplyResult(False, version=self._version, details={"applied": 0})

        # Param giữ compatibility cho caller cũ; V68 không có record miễn dọn.
        _ = tuple(protected_record_ids)
        changed_ids: list[str] = []
        rejected: list[str] = []
        details = {"applied": 0, "archived": 0, "deleted": 0, "weakened": 0, "merged": 0, "rewritten": 0, "hidden": 0, "canonicalized": 0}

        with self._lock:
            before = [r.to_dict() for r in self.all(include_inactive=True)]
            for row in rows:
                action = str(row.get("action") or "").strip().lower()
                record_ids = [str(x) for x in row.get("record_ids", ()) if str(x).strip()]
                record_ids = list(dict.fromkeys(record_ids))
                reason = " ".join(str(row.get("reason") or "").split())[:420]
                if action not in {"archive", "delete", "weaken", "merge", "rewrite", "hide", "canonicalize"} or not record_ids:
                    rejected.append(f"unsupported_curator_operation:{action or 'empty'}")
                    continue
                records = [self._records.get(record_id) for record_id in record_ids]
                if any(record is None for record in records):
                    rejected.append(f"record_not_found:{','.join(record_ids)}")
                    continue
                selected = [record for record in records if record is not None]
                if any(record.status not in {"active", "disputed"} for record in selected):
                    rejected.append(f"record_not_current:{','.join(record_ids)}")
                    continue

                if action in {"archive", "delete", "weaken"} and any(getattr(r, "tier", "working") == "core_anchor" for r in selected):
                    rejected.append(f"core_anchor_protected:{','.join(record_ids)}")
                    continue

                now = now_ts()
                if action == "archive":
                    if len(selected) != 1:
                        rejected.append("archive_requires_one_record")
                        continue
                    record = selected[0]
                    record.status = "archived"
                    record.updated_at = now
                    record.valid_until = now
                    record.metadata["curator_reason"] = reason
                    record.metadata["curated_by"] = "living_user_curator"
                    changed_ids.append(record.id)
                    details["archived"] += 1

                elif action == "delete":
                    if len(selected) != 1:
                        rejected.append("delete_requires_one_record")
                        continue
                    record = selected[0]
                    record.status = "deleted"
                    record.updated_at = now
                    record.valid_until = now
                    record.metadata["curator_reason"] = reason
                    record.metadata["curated_by"] = "living_user_curator"
                    changed_ids.append(record.id)
                    details["deleted"] += 1

                elif action == "weaken":
                    if len(selected) != 1:
                        rejected.append("weaken_requires_one_record")
                        continue
                    try:
                        amount = float(row.get("amount") or 0.12)
                    except Exception:
                        amount = 0.12
                    amount = max(0.05, min(0.30, amount))
                    record = selected[0]
                    record.confidence = clamp01(record.confidence - amount)
                    record.updated_at = now
                    record.metadata["curator_reason"] = reason
                    record.metadata["curator_weaken_amount"] = amount
                    if record.confidence < self.config.drop_below_confidence:
                        record.status = "archived"
                        record.valid_until = now
                        details["archived"] += 1
                    changed_ids.append(record.id)
                    details["weakened"] += 1

                elif action == "merge":
                    if len(selected) < 2:
                        rejected.append("merge_requires_multiple_records")
                        continue
                    if len({record.scope.identity for record in selected}) != 1:
                        rejected.append("merge_scope_mismatch")
                        continue
                    primary = max(selected, key=lambda r: (r.trust_weight, r.recurrence, r.updated_at))
                    evidence = list(primary.evidence_ids)
                    recurrence = primary.recurrence
                    for record in selected:
                        if record.id == primary.id:
                            continue
                        evidence.extend(record.evidence_ids)
                        recurrence += record.recurrence
                        record.status = "superseded"
                        record.valid_until = now
                        record.updated_at = now
                        record.metadata["merged_into"] = primary.id
                        record.metadata["curator_reason"] = reason
                    primary.evidence_ids = tuple(dict.fromkeys(evidence))[:96]
                    primary.recurrence = max(primary.recurrence, recurrence)
                    primary.confidence = clamp01(max(record.confidence for record in selected) + 0.02)
                    primary.updated_at = now
                    primary.last_confirmed_at = max(record.last_confirmed_at for record in selected)
                    primary.metadata["curator_reason"] = reason
                    primary.metadata["curated_by"] = "living_user_curator"
                    primary.metadata["canonical_user_memory"] = True
                    primary.metadata["curated_merge_ids"] = [r.id for r in selected if r.id != primary.id][-40:]
                    changed_ids.extend(record.id for record in selected)
                    details["merged"] += 1

                elif action == "canonicalize":
                    target_path = normalize_path(str(row.get("path") or ""))
                    if not target_path:
                        rejected.append("canonicalize_requires_path")
                        continue
                    target_scope = UserMemoryScope.from_value(row.get("scope")) if isinstance(row.get("scope"), Mapping) else None
                    cardinality = str(row.get("cardinality") or "").strip().lower()

                    # A path is a category, not semantic identity. Only the IDs
                    # chosen by the curator may be changed or supply evidence.
                    # A single-valued destination needs an explicit resolution;
                    # never silently absorb an unlisted record to make it unique.
                    primary = max(selected, key=lambda r: (r.trust_weight, r.recurrence, r.updated_at))
                    desired_scope = target_scope or selected[0].scope
                    desired_cardinality = cardinality if cardinality in {"single", "multi"} else primary.cardinality
                    selected_ids = {record.id for record in selected}
                    collisions = [
                        record for record in self._records.values()
                        if record.id not in selected_ids
                        and record.status in {"active", "disputed"}
                        and record.path == target_path
                        and record.scope.identity == desired_scope.identity
                    ]
                    if collisions and (desired_cardinality == "single" or any(record.cardinality == "single" for record in collisions)):
                        rejected.append("canonicalize_target_conflict:" + ",".join(record.id for record in collisions))
                        continue

                    evidence: list[str] = []
                    recurrence = 0
                    last_confirmed = 0.0
                    for record in selected:
                        evidence.extend(record.evidence_ids)
                        recurrence += record.recurrence
                        last_confirmed = max(last_confirmed, record.last_confirmed_at)
                        if record.id != primary.id:
                            record.status = "superseded"
                            record.valid_until = now
                            record.updated_at = now
                            record.metadata["merged_into"] = primary.id
                            record.metadata["curator_reason"] = reason
                            record.metadata["curated_by"] = "living_user_curator"
                    primary.path = target_path
                    if row.get("value") is not None:
                        primary.value = row.get("value")
                    if target_scope is not None:
                        primary.scope = target_scope
                    if cardinality in {"single", "multi"}:
                        primary.cardinality = cardinality  # type: ignore[assignment]
                    primary.evidence_ids = tuple(dict.fromkeys(evidence))[:96]
                    primary.recurrence = max(1, recurrence)
                    primary.confidence = clamp01(max(record.confidence for record in selected))
                    primary.last_confirmed_at = max(primary.last_confirmed_at, last_confirmed)
                    primary.updated_at = now
                    primary.valid_until = None
                    primary.status = "active"
                    primary.metadata["curator_reason"] = reason
                    primary.metadata["curated_by"] = "living_user_curator"
                    primary.metadata["canonical_user_memory"] = True
                    primary.metadata["canonicalized_from"] = [r.id for r in selected][-60:]
                    changed_ids.extend(record.id for record in selected)
                    details["canonicalized"] += 1

                elif action == "rewrite":
                    if len(selected) != 1:
                        rejected.append("rewrite_requires_one_record")
                        continue
                    value = row.get("value")
                    if not value or not isinstance(value, str):
                        rejected.append("rewrite_requires_string_value")
                        continue
                    record = selected[0]
                    record.value = value.strip()[:1000]
                    record.updated_at = now
                    record.metadata["curator_reason"] = reason
                    record.metadata["curated_by"] = "living_user_curator"
                    record.metadata["canonical_user_memory"] = True
                    changed_ids.append(record.id)
                    details["rewritten"] += 1

                elif action == "hide":
                    if len(selected) != 1:
                        rejected.append("hide_requires_one_record")
                        continue
                    record = selected[0]
                    record.updated_at = now
                    record.metadata["hidden_from_ui"] = True
                    record.metadata["curator_reason"] = reason
                    record.metadata["curated_by"] = "living_user_curator"
                    changed_ids.append(record.id)
                    details["hidden"] += 1

                details["applied"] += 1
                self._audit.append({
                    "at": now,
                    "operation": {
                        "op": f"curator_{action}",
                        "record_ids": record_ids,
                        "reason": reason,
                    },
                    "record_ids": record_ids,
                })

            if changed_ids:
                self._version += 1
                self._history.append({
                    "version": self._version,
                    "at": now_ts(),
                    "reason": checkpoint_reason,
                    "records_before": before,
                })
                self._history = self._history[-self.config.max_history :]
                self._trim()
                if self.config.autosave:
                    self.save()

        return ApplyResult(
            bool(changed_ids),
            tuple(dict.fromkeys(changed_ids)),
            tuple(rejected),
            self._version,
            details,
        )

    def compact_history(self, *, max_snapshots: int = 8) -> int:
        """Giữ một undo window ngắn thay vì photocopy cả profile 40 lần.

        History là rollback window, không phải kho lưu trữ lạnh. Evidence thật vẫn
        nằm trong event/semantic store và authority ledger. Với JSON vài chục record,
        giữ 6-8 snapshot là đủ cứu một thao tác lỗi mà không biến file thành vali.
        """
        keep = max(2, int(max_snapshots))
        with self._lock:
            removed = max(0, len(self._history) - keep)
            if removed <= 0:
                return 0
            self._history = self._history[-keep:]
            if self.config.autosave:
                self.save()
            return removed

    def _trim(self) -> None:
        if len(self._records) <= self.config.max_records:
            return
        ranked = sorted(
            self._records.values(),
            key=lambda r: (
                r.status == "active",
                r.explicitness == "explicit",
                r.trust_weight,
                r.updated_at,
            ),
            reverse=True,
        )
        self._records = {r.id: r for r in ranked[: self.config.max_records]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "version": self._version,
            "updated_at": now_ts(),
            "records": [r.to_dict() for r in self.all(include_inactive=True)],
            "history": self._history[-self.config.max_history :],
            "audit": self._audit[-500:],
        }

    def load(self) -> None:
        if not self.path.is_file() or self.path.stat().st_size <= 0:
            return
        try:
            with locked_path(self.path):
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            records = raw.get("records") if isinstance(raw, Mapping) else []
            self._records = {
                r.id: r for r in (ProfileRecord.from_dict(x) for x in records or [] if isinstance(x, Mapping))
            }
            self._version = int(raw.get("version") or 0) if isinstance(raw, Mapping) else 0
            self._history = [dict(x) for x in (raw.get("history") or []) if isinstance(x, Mapping)] if isinstance(raw, Mapping) else []
            self._audit = [dict(x) for x in (raw.get("audit") or []) if isinstance(x, Mapping)] if isinstance(raw, Mapping) else []
        except Exception:
            # Corrupt profile không được làm runtime chết. Giữ file để người dùng còn cứu thủ công.
            self._records = {}
            self._history = []
            self._audit = []
            self._version = 0

    def save(self) -> None:
        atomic_write_json(self.path, self.to_dict())


__all__ = ["ApplyResult", "UserProfileStore", "UserProfileStoreConfig"]
