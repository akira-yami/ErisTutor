"""Active State Snapshot cho ERIS.

Snapshot giữ trạng thái hiện tại có thể overwrite: goal đang theo, session arc,
affect/homeostasis hiện thời, relation state, project active, thread đang mở.
Nó không phải event ledger và không phải semantic memory. Nó là bảng điều khiển hiện tại.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())



from .json_store_io import atomic_write_json


def compact_text(value: Any, *, limit: int = 320) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


@dataclass(slots=True)
class ActiveStateSnapshot:
    """State overwrite được, dùng cho runtime hiện tại.

    Memory dài hạn nói "đã từng". Snapshot nói "đang là".
    """

    version: int = 1
    updated_at: str = ""
    current_goal: str = ""
    current_goal_confidence: float = 0.0
    session_arc: str = "unknown"
    active_project: str = ""
    active_task: str = ""
    relation_state: dict[str, Any] = field(default_factory=dict)
    affect_state: dict[str, float] = field(default_factory=dict)
    homeostatic_state: dict[str, float] = field(default_factory=dict)
    working_threads: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    self_continuity: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "current_goal": self.current_goal,
            "current_goal_confidence": round(float(self.current_goal_confidence), 4),
            "session_arc": self.session_arc,
            "active_project": self.active_project,
            "active_task": self.active_task,
            "relation_state": dict(self.relation_state),
            "affect_state": dict(self.affect_state),
            "homeostatic_state": dict(self.homeostatic_state),
            "working_threads": list(self.working_threads),
            "open_questions": list(self.open_questions),
            "self_continuity": dict(self.self_continuity),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActiveStateSnapshot":
        return cls(
            version=int(data.get("version", 1) or 1),
            updated_at=str(data.get("updated_at") or ""),
            current_goal=str(data.get("current_goal") or ""),
            current_goal_confidence=float(data.get("current_goal_confidence", 0.0) or 0.0),
            session_arc=str(data.get("session_arc") or "unknown"),
            active_project=str(data.get("active_project") or ""),
            active_task=str(data.get("active_task") or ""),
            relation_state=dict(data.get("relation_state") or {}),
            affect_state={str(k): float(v) for k, v in dict(data.get("affect_state") or {}).items()},
            homeostatic_state={str(k): float(v) for k, v in dict(data.get("homeostatic_state") or {}).items()},
            working_threads=[compact_text(item, limit=160) for item in data.get("working_threads") or [] if compact_text(item, limit=160)],
            open_questions=[compact_text(item, limit=180) for item in data.get("open_questions") or [] if compact_text(item, limit=180)],
            self_continuity=dict(data.get("self_continuity") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


class ActiveStateStore:
    """Kho snapshot overwrite.

    Chỉ MemoryCognitionCore nên gọi apply_commit(). Module khác phải gửi StateCommitRequest.
    """

    def __init__(self, path: str):
        self.path = path

    def load(self) -> ActiveStateSnapshot:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return ActiveStateSnapshot(updated_at=iso_now())
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return ActiveStateSnapshot(updated_at=iso_now())
        return ActiveStateSnapshot.from_dict(data)

    def save(self, snapshot: ActiveStateSnapshot) -> None:
        snapshot.updated_at = iso_now()
        atomic_write_json(self.path, snapshot.to_dict())

    def apply_commit(self, request: Any) -> ActiveStateSnapshot:
        snapshot = self.load()
        target = str(getattr(request, "target", "") or "")
        metadata = dict(getattr(request, "metadata", {}) or {})
        raw_content = metadata.get("raw_content")
        content_payload = raw_content if isinstance(raw_content, dict) else getattr(request, "content", "")
        content = compact_text(content_payload, limit=500)
        confidence = float(getattr(request, "confidence", 0.0) or 0.0)
        reason = compact_text(getattr(request, "reason", "") or "", limit=260)

        if target == "active_state":
            if isinstance(content_payload, dict):
                snapshot.active_task = compact_text(content_payload.get("active_task") or content_payload.get("last_outcome") or snapshot.active_task, limit=220)
                snapshot.working_threads = [
                    compact_text(item, limit=160)
                    for item in content_payload.get("working_threads", []) or []
                    if compact_text(item, limit=160)
                ] or snapshot.working_threads
                internal_state = content_payload.get("internal_state")
                if isinstance(internal_state, dict):
                    snapshot.affect_state = {
                        key: float(value)
                        for key, value in internal_state.items()
                        if key in {"attachment", "silence_pressure", "social_pressure", "curiosity_pressure", "restraint_pressure"}
                        and isinstance(value, (int, float))
                    }
                    snapshot.metadata["internal_state"] = internal_state
            else:
                snapshot.active_task = content or snapshot.active_task
            snapshot.metadata["last_active_state_reason"] = reason
            snapshot.metadata["last_active_state_confidence"] = confidence
        elif target == "homeostatic_baseline":
            snapshot.homeostatic_state["baseline_update_confidence"] = confidence
            snapshot.metadata["homeostatic_note"] = content
        elif target == "self_continuity":
            snapshot.self_continuity["latest_note"] = content
            snapshot.self_continuity["confidence"] = confidence
            snapshot.self_continuity["reason"] = reason
        else:
            # Fallback có kiểm soát: lưu như metadata, không giả vờ hiểu target lạ.
            snapshot.metadata[f"unhandled_commit:{target or 'unknown'}"] = {
                "content": content,
                "confidence": confidence,
                "reason": reason,
                "updated_at": iso_now(),
            }

        evidence_ids = list(getattr(request, "evidence_ids", []) or [])
        if evidence_ids:
            snapshot.metadata["last_evidence_ids"] = [str(item) for item in evidence_ids]
        self.save(snapshot)
        return snapshot
