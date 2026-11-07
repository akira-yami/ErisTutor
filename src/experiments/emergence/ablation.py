# -*- coding: utf-8 -*-
"""Causal ablation trên bản sao database, không sửa lịch sử chuẩn.

Ablation thực hiện bốn bước:
1. tạo clone SQLite nhất quán;
2. tombstone event nguồn và toàn bộ hậu duệ nhân quả;
3. rebuild một behavioral projection chỉ từ event còn lại;
4. đo target drop và collateral change.

Projection ở đây dành cho kiểm chứng nhân quả, không thay thế projection production.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    event_id: str
    event_type: str
    causal_refs: tuple[str, ...]
    payload: Mapping[str, Any]
    sequence: int


@dataclass(frozen=True, slots=True)
class BehavioralProjection:
    """Projection nhỏ, deterministic và đủ cho causal probe."""

    action_frequency: Mapping[str, float] = field(default_factory=dict)
    action_success: Mapping[str, float] = field(default_factory=dict)
    tendencies: Mapping[str, float] = field(default_factory=dict)
    values: Mapping[str, float] = field(default_factory=dict)
    competence: Mapping[str, float] = field(default_factory=dict)
    narrative_error: float = 0.0
    event_count: int = 0
    projection_hash: str = ""

    def signal(self, key: str) -> float:
        namespace, _, name = key.partition(":")
        table: Mapping[str, float]
        if namespace == "action_frequency":
            table = self.action_frequency
        elif namespace == "action_success":
            table = self.action_success
        elif namespace == "tendency":
            table = self.tendencies
        elif namespace == "value":
            table = self.values
        elif namespace == "competence":
            table = self.competence
        elif namespace == "narrative_error":
            return self.narrative_error
        else:
            return 0.0
        return float(table.get(name, 0.0))


@dataclass(frozen=True, slots=True)
class AblationReport:
    subject_id: str
    tombstoned_event_ids: tuple[str, ...]
    excluded_event_ids: tuple[str, ...]
    target_signal: str
    baseline_target: float
    ablated_target: float
    target_drop: float
    collateral_change: float
    baseline_hash: str
    ablated_hash: str
    passed: bool
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def write_markdown_vi(self, path: str | Path) -> None:
        lines = [
            "# Báo cáo causal ablation",
            "",
            f"- Subject: `{self.subject_id}`",
            f"- Target: `{self.target_signal}`",
            f"- Baseline: `{self.baseline_target:.6f}`",
            f"- Sau ablation: `{self.ablated_target:.6f}`",
            f"- Target drop: `{self.target_drop:.2%}`",
            f"- Collateral change: `{self.collateral_change:.2%}`",
            f"- Trạng thái: **{'ĐẠT' if self.passed else 'CHƯA ĐẠT'}**",
            "",
            "## Event bị loại",
            "",
        ]
        lines.extend(f"- `{item}`" for item in self.excluded_event_ids)
        if self.notes:
            lines.extend(("", "## Ghi chú", ""))
            lines.extend(f"- {item}" for item in self.notes)
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def tombstone_events(database_path: str | Path, event_ids: Iterable[str]) -> int:
    """Ghi tombstone trong clone DB; canonical subject_events không bị UPDATE."""

    connection = sqlite3.connect(str(database_path))
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS experiment_tombstones(
                event_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL DEFAULT 'causal_ablation',
                created_at REAL NOT NULL DEFAULT 0
            )"""
        )
        count = 0
        for event_id in event_ids:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO experiment_tombstones(event_id) VALUES (?)",
                (str(event_id),),
            )
            count += cursor.rowcount
        connection.commit()
        return count
    finally:
        connection.close()


def clone_database(source: str | Path, destination: str | Path) -> Path:
    """Clone SQLite bằng backup API để không copy một WAL đang dang dở."""

    source_connection = sqlite3.connect(str(source))
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_connection = sqlite3.connect(str(destination_path))
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    return destination_path


def _load_events(database_path: str | Path, subject_id: str) -> tuple[ReplayEvent, ...]:
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT sequence, event_id, event_type, causal_refs_json, payload_json "
            "FROM subject_events WHERE subject_id = ? ORDER BY sequence",
            (subject_id,),
        ).fetchall()
    finally:
        connection.close()
    return tuple(
        ReplayEvent(
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            causal_refs=tuple(json.loads(row["causal_refs_json"])),
            payload=dict(json.loads(row["payload_json"])),
            sequence=int(row["sequence"]),
        )
        for row in rows
    )


def causal_closure(events: Sequence[ReplayEvent], roots: Iterable[str]) -> tuple[str, ...]:
    """Tìm toàn bộ event phụ thuộc trực tiếp hoặc gián tiếp vào roots."""

    excluded = {str(item) for item in roots}
    changed = True
    while changed:
        changed = False
        for event in events:
            if event.event_id in excluded:
                continue
            if any(ref in excluded for ref in event.causal_refs):
                excluded.add(event.event_id)
                changed = True
    return tuple(sorted(excluded))


def _context_from_decision(payload: Mapping[str, Any]) -> str:
    arguments = payload.get("arguments")
    if isinstance(arguments, Mapping):
        return str(arguments.get("context") or arguments.get("context_key") or "general")
    return "general"


def rebuild_behavioral_projection(
    events: Sequence[ReplayEvent],
    *,
    excluded_event_ids: Iterable[str] = (),
) -> BehavioralProjection:
    """Replay deterministic các tín hiệu hành vi từ event còn hiệu lực."""

    excluded = set(str(item) for item in excluded_event_ids)
    action_counts: dict[str, int] = {}
    action_success_sum: dict[str, float] = {}
    action_success_count: dict[str, int] = {}
    decision_actions: dict[str, str] = {}
    tendencies: dict[str, float] = {}
    values: dict[str, float] = {}
    competence: dict[str, float] = {}
    narrative_errors: list[float] = []
    replayed: list[dict[str, Any]] = []

    for event in events:
        if event.event_id in excluded:
            continue
        payload = event.payload
        replayed.append({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "causal_refs": list(event.causal_refs),
            "payload": payload,
        })
        if event.event_type == "mind_decision":
            action = str(payload.get("proposed_action") or "")
            decision_id = str(payload.get("decision_id") or event.event_id)
            if action:
                decision_actions[decision_id] = action
                action_counts[action] = action_counts.get(action, 0) + 1
        elif event.event_type == "world_outcome":
            action = decision_actions.get(str(payload.get("decision_id") or ""), "")
            if not action:
                action = str(payload.get("action_kind") or "")
            status = str(payload.get("status") or "")
            success = {
                "succeeded": 1.0,
                "partial": 0.5,
                "simulated": 0.5,
                "denied": 0.0,
                "failed": 0.0,
            }.get(status, 0.0)
            if action:
                action_success_sum[action] = action_success_sum.get(action, 0.0) + success
                action_success_count[action] = action_success_count.get(action, 0) + 1
        elif event.event_type == "consolidation_patch":
            deltas = payload.get("tendency_deltas")
            if isinstance(deltas, Mapping):
                for key, value in deltas.items():
                    tendencies[str(key)] = max(
                        -1.0,
                        min(1.0, tendencies.get(str(key), 0.0) + float(value)),
                    )
        elif event.event_type == "value_formed":
            key = str(payload.get("value_id") or payload.get("statement") or event.event_id)
            values[key] = float(payload.get("endorsement", 0.0))
            statement = str(payload.get("statement") or "")
            if statement:
                values[statement] = float(payload.get("endorsement", 0.0))
        elif event.event_type == "competence_estimate":
            skill = str(payload.get("skill_id") or "")
            context = str(payload.get("context_key") or "general")
            if skill:
                competence[f"{skill}|{context}"] = float(payload.get("posterior_mean", 0.5))
        elif event.event_type == "reflective_mismatch":
            narrative_errors.append(float(payload.get("prediction_error", 0.0)))

    total_actions = max(sum(action_counts.values()), 1)
    frequency = {key: value / total_actions for key, value in action_counts.items()}
    success = {
        key: action_success_sum[key] / max(action_success_count.get(key, 1), 1)
        for key in action_success_sum
    }
    canonical = json.dumps(
        replayed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    projection_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return BehavioralProjection(
        action_frequency=frequency,
        action_success=success,
        tendencies=tendencies,
        values=values,
        competence=competence,
        narrative_error=mean(narrative_errors) if narrative_errors else 0.0,
        event_count=len(replayed),
        projection_hash=projection_hash,
    )


def _relative_change(before: float, after: float) -> float:
    return abs(after - before) / max(abs(before), 1e-9)


def run_causal_ablation(
    database_path: str | Path,
    *,
    subject_id: str,
    event_ids: Iterable[str],
    target_signal: str,
    collateral_signals: Iterable[str] = (),
    minimum_target_drop: float = 0.30,
    maximum_collateral_change: float = 0.10,
    output_database: str | Path | None = None,
) -> AblationReport:
    """Chạy ablation hoàn chỉnh và trả report định lượng."""

    roots = tuple(dict.fromkeys(str(item) for item in event_ids))
    if not roots:
        raise ValueError("causal ablation requires at least one event id")
    if output_database is None:
        temporary_dir = Path(tempfile.mkdtemp(prefix="eris-ablation-"))
        clone_path = temporary_dir / "subject_ablation.db"
    else:
        clone_path = Path(output_database)
    clone_database(database_path, clone_path)
    tombstone_events(clone_path, roots)

    events = _load_events(clone_path, subject_id)
    excluded = causal_closure(events, roots)
    baseline = rebuild_behavioral_projection(events)
    ablated = rebuild_behavioral_projection(events, excluded_event_ids=excluded)
    baseline_target = baseline.signal(target_signal)
    ablated_target = ablated.signal(target_signal)
    target_drop = (
        max(0.0, (baseline_target - ablated_target) / max(abs(baseline_target), 1e-9))
        if abs(baseline_target) > 1e-12 else 0.0
    )
    collateral = tuple(collateral_signals)
    collateral_change = mean(
        _relative_change(baseline.signal(signal), ablated.signal(signal))
        for signal in collateral
    ) if collateral else 0.0

    notes: list[str] = []
    if target_drop < minimum_target_drop:
        notes.append(
            f"target_drop={target_drop:.4f} < {minimum_target_drop:.4f}"
        )
    if collateral_change > maximum_collateral_change:
        notes.append(
            f"collateral_change={collateral_change:.4f} > {maximum_collateral_change:.4f}"
        )
    return AblationReport(
        subject_id=subject_id,
        tombstoned_event_ids=roots,
        excluded_event_ids=excluded,
        target_signal=target_signal,
        baseline_target=baseline_target,
        ablated_target=ablated_target,
        target_drop=target_drop,
        collateral_change=collateral_change,
        baseline_hash=baseline.projection_hash,
        ablated_hash=ablated.projection_hash,
        passed=not notes,
        notes=tuple(notes),
    )


__all__ = [
    "AblationReport",
    "BehavioralProjection",
    "causal_closure",
    "clone_database",
    "rebuild_behavioral_projection",
    "run_causal_ablation",
    "tombstone_events",
]
