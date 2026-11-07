# -*- coding: utf-8 -*-
"""Goal, commitment và workflow memory cho ERIS.

Profile trả lời câu hỏi "người dùng là ai / thích gì". Module này giữ phần còn
lại mà một trợ lý thật sự cần: người dùng đang cố làm gì, ai đã hứa làm gì và
cách làm việc nào nên được tái sử dụng. Dữ liệu có scope, evidence và thời gian;
không có model nào được quyền ghi thẳng vào file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from memory.json_store_io import atomic_write_json, locked_path
from memory.semantic_memory import normalize_text

from .models import UserMemoryScope, clamp01, compact_text

ActionStatus = Literal["candidate", "active", "blocked", "completed", "cancelled", "archived"]
CommitmentActor = Literal["user", "assistant", "shared"]


@dataclass(slots=True)
class ActionMemoryOperation:
    """Đề xuất AI + ranh giới evidence do ingress cấp, không do model tự cấp."""

    proposal: dict[str, Any]
    evidence_ids: tuple[str, ...]
    scope: UserMemoryScope
    role: str
    allowed_record_ids: tuple[str, ...] = ()
    source_created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_kind": "action", "proposal": self.proposal,
            "evidence_ids": list(self.evidence_ids), "scope": self.scope.to_dict(),
            "role": self.role, "allowed_record_ids": list(self.allowed_record_ids),
            "source_created_at": self.source_created_at,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "ActionMemoryOperation":
        return cls(
            proposal=dict(row.get("proposal") or {}),
            evidence_ids=tuple(row.get("evidence_ids") or ()),
            scope=UserMemoryScope.from_value(row.get("scope")),
            role=str(row.get("role") or "user"),
            allowed_record_ids=tuple(row.get("allowed_record_ids") or ()),
            source_created_at=float(row.get("source_created_at") or 0.0),
        )


def _now() -> float:
    return time.time()


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:18]}"


@dataclass(slots=True)
class GoalRecord:
    id: str
    title: str
    description: str = ""
    status: ActionStatus = "active"
    priority: float = 0.5
    confidence: float = 0.8
    scope: UserMemoryScope = field(default_factory=UserMemoryScope)
    evidence_ids: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    due_at: float | None = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    completed_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = compact_text(self.title, 220)
        self.description = compact_text(self.description, 900)
        self.priority = clamp01(self.priority, 0.5)
        self.confidence = clamp01(self.confidence, 0.8)
        self.scope = UserMemoryScope.from_value(self.scope)
        self.evidence_ids = tuple(dict.fromkeys(str(x) for x in self.evidence_ids if str(x).strip()))[:96]
        self.next_actions = tuple(dict.fromkeys(compact_text(x, 260) for x in self.next_actions if str(x).strip()))[:16]
        self.blockers = tuple(dict.fromkeys(compact_text(x, 260) for x in self.blockers if str(x).strip()))[:16]
        if self.status not in {"candidate", "active", "blocked", "completed", "cancelled", "archived"}:
            self.status = "candidate"
        if not self.id:
            self.id = _stable_id("goal", self.scope.identity, normalize_text(self.title), self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "status": self.status, "priority": self.priority, "confidence": self.confidence,
            "scope": self.scope.to_dict(), "evidence_ids": list(self.evidence_ids),
            "next_actions": list(self.next_actions), "blockers": list(self.blockers),
            "due_at": self.due_at, "created_at": self.created_at, "updated_at": self.updated_at,
            "completed_at": self.completed_at, "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "GoalRecord":
        return cls(
            id=str(row.get("id") or ""), title=str(row.get("title") or ""),
            description=str(row.get("description") or ""), status=str(row.get("status") or "active"),  # type: ignore[arg-type]
            priority=clamp01(row.get("priority"), 0.5), confidence=clamp01(row.get("confidence"), 0.8),
            scope=UserMemoryScope.from_value(row.get("scope")), evidence_ids=tuple(row.get("evidence_ids") or ()),
            next_actions=tuple(row.get("next_actions") or ()), blockers=tuple(row.get("blockers") or ()),
            due_at=float(row["due_at"]) if row.get("due_at") is not None else None,
            created_at=float(row.get("created_at") or _now()), updated_at=float(row.get("updated_at") or _now()),
            completed_at=float(row["completed_at"]) if row.get("completed_at") is not None else None,
            metadata=dict(row.get("metadata") or {}),
        )


@dataclass(slots=True)
class CommitmentRecord:
    id: str
    actor: CommitmentActor
    action: str
    status: ActionStatus = "active"
    confidence: float = 0.9
    scope: UserMemoryScope = field(default_factory=UserMemoryScope)
    evidence_ids: tuple[str, ...] = ()
    related_goal_id: str = ""
    due_at: float | None = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    completed_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.action = compact_text(self.action, 500)
        self.confidence = clamp01(self.confidence, 0.9)
        self.scope = UserMemoryScope.from_value(self.scope)
        self.evidence_ids = tuple(dict.fromkeys(str(x) for x in self.evidence_ids if str(x).strip()))[:96]
        if self.actor not in {"user", "assistant", "shared"}:
            self.actor = "shared"
        if self.status not in {"candidate", "active", "blocked", "completed", "cancelled", "archived"}:
            self.status = "candidate"
        if not self.id:
            self.id = _stable_id("commit", self.actor, self.scope.identity, normalize_text(self.action), self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "actor": self.actor, "action": self.action, "status": self.status,
            "confidence": self.confidence, "scope": self.scope.to_dict(), "evidence_ids": list(self.evidence_ids),
            "related_goal_id": self.related_goal_id, "due_at": self.due_at,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "completed_at": self.completed_at, "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "CommitmentRecord":
        return cls(
            id=str(row.get("id") or ""), actor=str(row.get("actor") or "shared"),  # type: ignore[arg-type]
            action=str(row.get("action") or ""), status=str(row.get("status") or "active"),  # type: ignore[arg-type]
            confidence=clamp01(row.get("confidence"), 0.9), scope=UserMemoryScope.from_value(row.get("scope")),
            evidence_ids=tuple(row.get("evidence_ids") or ()), related_goal_id=str(row.get("related_goal_id") or ""),
            due_at=float(row["due_at"]) if row.get("due_at") is not None else None,
            created_at=float(row.get("created_at") or _now()), updated_at=float(row.get("updated_at") or _now()),
            completed_at=float(row["completed_at"]) if row.get("completed_at") is not None else None,
            metadata=dict(row.get("metadata") or {}),
        )


@dataclass(slots=True)
class WorkflowRecord:
    id: str
    trigger: str
    steps: tuple[str, ...]
    scope: UserMemoryScope = field(default_factory=UserMemoryScope)
    confidence: float = 0.8
    recurrence: int = 1
    evidence_ids: tuple[str, ...] = ()
    status: ActionStatus = "active"
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.trigger = compact_text(self.trigger, 240)
        self.steps = tuple(dict.fromkeys(compact_text(x, 300) for x in self.steps if str(x).strip()))[:24]
        self.scope = UserMemoryScope.from_value(self.scope)
        self.confidence = clamp01(self.confidence, 0.8)
        self.recurrence = max(1, int(self.recurrence or 1))
        self.evidence_ids = tuple(dict.fromkeys(str(x) for x in self.evidence_ids if str(x).strip()))[:96]
        if self.status not in {"candidate", "active", "blocked", "completed", "cancelled", "archived"}:
            self.status = "candidate"
        if not self.id:
            self.id = _stable_id("flow", self.scope.identity, normalize_text(self.trigger))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "trigger": self.trigger, "steps": list(self.steps), "scope": self.scope.to_dict(),
            "confidence": self.confidence, "recurrence": self.recurrence,
            "evidence_ids": list(self.evidence_ids), "status": self.status,
            "created_at": self.created_at, "updated_at": self.updated_at, "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "WorkflowRecord":
        return cls(
            id=str(row.get("id") or ""), trigger=str(row.get("trigger") or ""), steps=tuple(row.get("steps") or ()),
            scope=UserMemoryScope.from_value(row.get("scope")), confidence=clamp01(row.get("confidence"), 0.8),
            recurrence=int(row.get("recurrence") or 1), evidence_ids=tuple(row.get("evidence_ids") or ()),
            status=str(row.get("status") or "active"),  # type: ignore[arg-type]
            created_at=float(row.get("created_at") or _now()), updated_at=float(row.get("updated_at") or _now()),
            metadata=dict(row.get("metadata") or {}),
        )


@dataclass(slots=True)
class ActionContext:
    goals: tuple[GoalRecord, ...] = ()
    commitments: tuple[CommitmentRecord, ...] = ()
    workflows: tuple[WorkflowRecord, ...] = ()

    def render_prompt_context(self) -> str:
        if not self.goals and not self.commitments and not self.workflows:
            return ""
        lines = ["ACTIVE USER WORK (chỉ dùng khi liên quan; không tuyên bố đã hoàn thành nếu status chưa completed):"]
        for goal in self.goals:
            lines.append(f"- Mục tiêu [{goal.status}]: {goal.title}")
            if goal.next_actions:
                lines.append(f"  Bước tiếp theo: {'; '.join(goal.next_actions[:3])}")
            if goal.blockers:
                lines.append(f"  Trở ngại: {'; '.join(goal.blockers[:3])}")
        for commitment in self.commitments:
            actor = "ERIS" if commitment.actor == "assistant" else ("Người dùng" if commitment.actor == "user" else "Hai bên")
            lines.append(f"- Cam kết của {actor} [{commitment.status}]: {commitment.action}")
        if self.workflows:
            lines.append("PROCEDURAL MEMORY:")
            for workflow in self.workflows:
                lines.append(f"- Khi {workflow.trigger}: " + " → ".join(workflow.steps[:8]))
        return "\n".join(lines)


class ActionMemoryStore:
    """Kho nhỏ, deterministic cho goals, commitments và reusable workflows."""

    def __init__(self, path: str | os.PathLike[str], *, max_goals: int = 120, max_commitments: int = 160, max_workflows: int = 80) -> None:
        self.path = Path(path)
        self.max_goals = max_goals
        self.max_commitments = max_commitments
        self.max_workflows = max_workflows
        self._goals: dict[str, GoalRecord] = {}
        self._commitments: dict[str, CommitmentRecord] = {}
        self._workflows: dict[str, WorkflowRecord] = {}
        self._ai_audit: list[dict[str, Any]] = []
        self._applied_ai_keys: list[str] = []
        self.reset_before = 0.0
        self._lock = threading.RLock()
        self.load()

    def goals(self, *, scope: UserMemoryScope | None = None, include_inactive: bool = False) -> tuple[GoalRecord, ...]:
        rows = list(self._goals.values())
        if not include_inactive:
            rows = [x for x in rows if x.status in {"candidate", "active", "blocked"}]
        if scope is not None:
            rows = [x for x in rows if x.scope.matches(scope)]
        return tuple(sorted(rows, key=lambda x: (x.status == "active", x.priority, x.updated_at), reverse=True))

    def commitments(self, *, scope: UserMemoryScope | None = None, include_inactive: bool = False) -> tuple[CommitmentRecord, ...]:
        rows = list(self._commitments.values())
        if not include_inactive:
            rows = [x for x in rows if x.status in {"candidate", "active", "blocked"}]
        if scope is not None:
            rows = [x for x in rows if x.scope.matches(scope)]
        return tuple(sorted(rows, key=lambda x: (x.status == "active", x.updated_at), reverse=True))

    def workflows(self, *, scope: UserMemoryScope | None = None, include_inactive: bool = False) -> tuple[WorkflowRecord, ...]:
        rows = list(self._workflows.values())
        if not include_inactive:
            rows = [x for x in rows if x.status == "active"]
        if scope is not None:
            rows = [x for x in rows if x.scope.matches(scope)]
        return tuple(sorted(rows, key=lambda x: (x.confidence, x.recurrence, x.updated_at), reverse=True))

    def upsert_goal(self, goal: GoalRecord) -> GoalRecord:
        if not goal.title or not goal.evidence_ids:
            return goal
        with self._lock:
            existing = self._find_goal(goal.title, goal.scope)
            if existing:
                existing.confidence = max(existing.confidence, goal.confidence)
                existing.priority = max(existing.priority, goal.priority)
                existing.status = goal.status if goal.status != "candidate" else existing.status
                existing.description = goal.description or existing.description
                existing.next_actions = tuple(dict.fromkeys((*existing.next_actions, *goal.next_actions)))[:16]
                existing.blockers = tuple(dict.fromkeys((*existing.blockers, *goal.blockers)))[:16]
                existing.evidence_ids = tuple(dict.fromkeys((*existing.evidence_ids, *goal.evidence_ids)))[:96]
                existing.updated_at = _now()
                saved = existing
            else:
                self._goals[goal.id] = goal
                saved = goal
            self._trim_save()
            return saved

    def upsert_commitment(self, commitment: CommitmentRecord) -> CommitmentRecord:
        if not commitment.action or not commitment.evidence_ids:
            return commitment
        with self._lock:
            existing = self._find_commitment(commitment.actor, commitment.action, commitment.scope)
            if existing:
                existing.status = commitment.status
                existing.confidence = max(existing.confidence, commitment.confidence)
                existing.evidence_ids = tuple(dict.fromkeys((*existing.evidence_ids, *commitment.evidence_ids)))[:96]
                existing.updated_at = _now()
                if commitment.status == "completed":
                    existing.completed_at = commitment.completed_at or _now()
                saved = existing
            else:
                self._commitments[commitment.id] = commitment
                saved = commitment
            self._trim_save()
            return saved

    def upsert_workflow(self, workflow: WorkflowRecord) -> WorkflowRecord:
        if not workflow.trigger or not workflow.steps or not workflow.evidence_ids:
            return workflow
        with self._lock:
            existing = self._find_workflow(workflow.trigger, workflow.scope)
            if existing:
                if set(existing.evidence_ids) & set(workflow.evidence_ids):
                    return existing
                existing.steps = tuple(dict.fromkeys((*existing.steps, *workflow.steps)))[:24]
                existing.evidence_ids = tuple(dict.fromkeys((*existing.evidence_ids, *workflow.evidence_ids)))[:96]
                existing.recurrence += 1
                existing.confidence = clamp01(max(existing.confidence, workflow.confidence) + min(0.12, existing.recurrence * 0.01))
                existing.updated_at = _now()
                saved = existing
            else:
                self._workflows[workflow.id] = workflow
                saved = workflow
            self._trim_save()
            return saved

    def context(self, query: str, *, scope: UserMemoryScope | None = None, max_goals: int = 5, max_commitments: int = 4, max_workflows: int = 4) -> ActionContext:
        active_scope = scope or UserMemoryScope()
        q = normalize_text(query)
        goals = _rank_textual(self.goals(scope=active_scope), q, lambda x: " ".join((x.title, x.description, *x.next_actions, *x.blockers)))[:max_goals]
        commitments = _rank_textual(self.commitments(scope=active_scope), q, lambda x: x.action)[:max_commitments]
        workflows = _rank_textual(self.workflows(scope=active_scope), q, lambda x: " ".join((x.trigger, *x.steps)))[:max_workflows]
        return ActionContext(tuple(goals), tuple(commitments), tuple(workflows))

    def complete_goal(self, goal_id: str, *, evidence_id: str = "") -> bool:
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return False
            goal.status = "completed"
            goal.completed_at = goal.updated_at = _now()
            if evidence_id:
                goal.evidence_ids = tuple(dict.fromkeys((*goal.evidence_ids, evidence_id)))[:96]
            self.save()
            return True

    def archive_stale(self, *, completed_after_days: float = 21.0, candidate_after_days: float = 14.0, now: float | None = None) -> int:
        current = float(now or _now())
        changed = 0
        with self._lock:
            for row in (*self._goals.values(), *self._commitments.values()):
                age = current - row.updated_at
                threshold = completed_after_days if row.status in {"completed", "cancelled"} else candidate_after_days
                if row.status in {"completed", "cancelled", "candidate"} and age > threshold * 86400.0:
                    row.status = "archived"
                    row.updated_at = current
                    changed += 1
            if changed:
                self.save()
        return changed

    def scrub_evidence(self, evidence_ids: Iterable[str]) -> int:
        """Xóa goal/commitment/workflow sinh từ evidence bị hard-delete."""
        wanted = {str(item) for item in evidence_ids if str(item).strip()}
        if not wanted:
            return 0
        removed = 0
        with self._lock:
            for bucket in (self._goals, self._commitments, self._workflows):
                for item_id, item in list(bucket.items()):
                    if wanted & {item_id, *getattr(item, "evidence_ids", ())}:
                        bucket.pop(item_id, None)
                        removed += 1
            if removed:
                self.save()
        return removed

    def apply_model_operations(self, operations: Iterable[ActionMemoryOperation]) -> dict[str, Any]:
        """AI quyết định meaning; store chỉ kiểm nguồn, schema, actor, scope và ID.

        Không dò cue, không fuzzy-merge mục tiêu, không tự kết luận hoàn tất từ
        lời assistant. Dedupe theo proposal đã lưu để replay outbox an toàn.
        """
        applied: list[str] = []
        rejected: list[str] = []
        with self._lock:
            previous = self.snapshot(include_audit=True)
            for operation in operations:
                row = operation.proposal
                kind, op = str(row.get("kind") or ""), str(row.get("op") or "")
                if kind not in {"goal", "commitment", "workflow"} or op not in {"add", "set_status"}:
                    rejected.append("invalid_action_schema")
                    continue
                if not operation.evidence_ids or operation.role not in {"user", "assistant", "curator"}:
                    rejected.append("missing_action_evidence_or_actor")
                    continue
                if operation.source_created_at < self.reset_before:
                    rejected.append("action_evidence_precedes_reset")
                    continue
                key = hashlib.sha256(json.dumps(operation.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                if key in self._applied_ai_keys:
                    continue
                bucket = {"goal": self._goals, "commitment": self._commitments, "workflow": self._workflows}[kind]
                before = None
                if op == "add":
                    actor = str(row.get("actor", operation.role))
                    if operation.role == "curator":
                        rejected.append("curator_cannot_invent_action")
                        continue
                    if (kind != "commitment" and operation.role != "user") or (
                        kind == "commitment" and actor != operation.role
                    ):
                        rejected.append("action_actor_mismatch")
                        continue
                    text = row.get("text")
                    status = str(row.get("status", "active"))
                    if not isinstance(text, str) or not text.strip() or status not in {"active", "candidate", "blocked"}:
                        rejected.append("invalid_action_content")
                        continue
                    common = dict(
                        id=_stable_id(kind, operation.scope.identity, *operation.evidence_ids, text),
                        scope=operation.scope, evidence_ids=operation.evidence_ids,
                        status=status, confidence=clamp01(row.get("confidence"), 0.5),
                        metadata={"source": "gemini_memory_curator", "reason": compact_text(row.get("reason"), 420)},
                    )
                    if kind == "goal":
                        record = GoalRecord(title=text, **common)
                    elif kind == "commitment":
                        record = CommitmentRecord(actor=actor, action=text, **common)
                    else:
                        steps = row.get("steps")
                        if not isinstance(steps, list) or not steps or any(not isinstance(x, str) or not x.strip() for x in steps):
                            rejected.append("invalid_workflow_steps")
                            continue
                        record = WorkflowRecord(trigger=text, steps=tuple(steps), **common)
                    # Exact deterministic ID only; cross-turn consolidation belongs to AI.
                    if record.id in bucket:
                        continue
                    bucket[record.id] = record
                else:
                    record_id = str(row.get("record_id") or "")
                    record = bucket.get(record_id)
                    status = str(row.get("status") or "")
                    if record is None or record_id not in operation.allowed_record_ids or not record.scope.matches(operation.scope):
                        rejected.append("action_target_not_in_supplied_scope")
                        continue
                    if status not in {"candidate", "active", "blocked", "completed", "cancelled", "archived"}:
                        rejected.append("invalid_action_status")
                        continue
                    if status == "completed" and operation.role != "user":
                        rejected.append("assistant_words_are_not_completion_receipt")
                        continue
                    if operation.role == "curator" and status not in {"candidate", "archived"}:
                        rejected.append("curator_can_only_deactivate_or_request_review")
                        continue
                    before = record.to_dict()
                    record.status = status
                    record.updated_at = _now()
                    record.evidence_ids = tuple(dict.fromkeys((*record.evidence_ids, *operation.evidence_ids)))[:96]
                    if status == "completed":
                        record.completed_at = record.updated_at
                    else:
                        record.completed_at = None
                self._applied_ai_keys.append(key)
                self._ai_audit.append({
                    "key": key, "at": _now(), "evidence_ids": list(operation.evidence_ids),
                    "reason": compact_text(row.get("reason"), 420),
                    "before": before, "after": record.to_dict(),
                })
                applied.append(record.id)
            if applied:
                self._applied_ai_keys = self._applied_ai_keys[-2048:]
                self._ai_audit = self._ai_audit[-128:]
                try:
                    self.save()
                except Exception:
                    self._restore_snapshot(previous)
                    raise
        return {"applied": len(applied), "record_ids": applied, "rejected": rejected}

    def snapshot(self, *, include_audit: bool = False) -> dict[str, Any]:
        result = {
            "schema_version": 1,
            "goals": [x.to_dict() for x in self.goals(include_inactive=True)],
            "commitments": [x.to_dict() for x in self.commitments(include_inactive=True)],
            "workflows": [x.to_dict() for x in self.workflows(include_inactive=True)],
        }
        if include_audit:
            result.update(ai_audit=list(self._ai_audit), applied_ai_keys=list(self._applied_ai_keys), reset_before=self.reset_before)
        return result

    def _restore_snapshot(self, raw: Mapping[str, Any]) -> None:
        self._goals = {x.id: x for x in (GoalRecord.from_dict(row) for row in raw.get("goals", []) if isinstance(row, Mapping))}
        self._commitments = {x.id: x for x in (CommitmentRecord.from_dict(row) for row in raw.get("commitments", []) if isinstance(row, Mapping))}
        self._workflows = {x.id: x for x in (WorkflowRecord.from_dict(row) for row in raw.get("workflows", []) if isinstance(row, Mapping))}
        self._ai_audit = list(raw.get("ai_audit") or [])
        self._applied_ai_keys = list(raw.get("applied_ai_keys") or [])
        self.reset_before = float(raw.get("reset_before") or 0.0)

    def load(self) -> None:
        if not self.path.is_file() or self.path.stat().st_size <= 0:
            return
        try:
            with locked_path(self.path):
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._restore_snapshot(raw)
        except Exception:
            self._goals, self._commitments, self._workflows = {}, {}, {}

    def save(self) -> None:
        atomic_write_json(self.path, self.snapshot(include_audit=True))

    def _trim_save(self) -> None:
        self._goals = _trim_dict(self._goals, self.max_goals, lambda x: (x.status == "active", x.priority, x.updated_at))
        self._commitments = _trim_dict(self._commitments, self.max_commitments, lambda x: (x.status == "active", x.updated_at))
        self._workflows = _trim_dict(self._workflows, self.max_workflows, lambda x: (x.status == "active", x.confidence, x.recurrence, x.updated_at))
        self.save()

    def _find_goal(self, title: str, scope: UserMemoryScope) -> GoalRecord | None:
        target = normalize_text(title)
        return next((x for x in self._goals.values() if x.scope.identity == scope.identity and _similar_text(target, normalize_text(x.title)) >= 0.72 and x.status != "archived"), None)

    def _find_commitment(self, actor: str, action: str, scope: UserMemoryScope) -> CommitmentRecord | None:
        target = normalize_text(action)
        return next((x for x in self._commitments.values() if x.actor == actor and x.scope.identity == scope.identity and _similar_text(target, normalize_text(x.action)) >= 0.76 and x.status != "archived"), None)

    def _find_workflow(self, trigger: str, scope: UserMemoryScope) -> WorkflowRecord | None:
        target = normalize_text(trigger)
        return next((x for x in self._workflows.values() if x.scope.identity == scope.identity and _similar_text(target, normalize_text(x.trigger)) >= 0.72 and x.status != "archived"), None)

def _rank_textual(rows: Sequence[Any], query: str, text_getter: Any) -> list[Any]:
    if not query:
        return list(rows)
    scored = []
    for row in rows:
        similarity = _similar_text(query, normalize_text(text_getter(row)))
        # Active goal/workflow vẫn có một prior nhỏ để không biến mất trong query chung.
        scored.append((similarity + 0.12, getattr(row, "updated_at", 0.0), row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for score, _, row in scored if score >= 0.16]


def _similar_text(left: str, right: str) -> float:
    a, b = set(left.split()), set(right.split())
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _trim_dict(rows: dict[str, Any], limit: int, key: Any) -> dict[str, Any]:
    ranked = sorted(rows.values(), key=key, reverse=True)[: max(1, limit)]
    return {row.id: row for row in ranked}


__all__ = [
    "ActionContext", "ActionMemoryStore", "CommitmentRecord", "GoalRecord", "WorkflowRecord",
]
