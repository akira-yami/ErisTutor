# -*- coding: utf-8 -*-
"""Life-Horizon state cho ERIS V74.

Module này giữ hệ mục tiêu H0-H4 và trạng thái tiến độ bền qua phiên. Nó cố ý
không tự viết lời public và cũng không tự cấp quyền hành động. Nói cách khác,
đây là tầng điều phối mục tiêu dài hạn, không phải một nguồn phát sinh lời nói
public độc lập. Quyền phát ngôn vẫn thuộc pipeline quyết định hiện có.

Các tầng:
- H4: Life Direction, hướng/giá trị rất dài hạn.
- H3: Durable Goal, mục tiêu kéo dài nhiều ngày/tuần.
- H2: Project, dự án có cột mốc và next action.
- H1: Conversation Goal, việc đang dang dở giữa hai bên.
- H0: Immediate Action, hành động vừa/đang được commit.

Nguồn dữ liệu production ưu tiên state typed đã có: PrivateLife, Conversation
Handoff và ProjectOperations. Không suy diễn một câu user ngẫu nhiên thành "mục
tiêu đời người". Mọi mục tiêu dài hạn phải có nguồn/provenance rõ ràng.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence


HorizonLevel = Literal["H0", "H1", "H2", "H3", "H4"]
GoalStatus = Literal["active", "dormant", "blocked", "completed", "retired"]


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 520) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()[: max(16, int(limit))]


def _uniq(values: Iterable[Any], *, limit: int = 24) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = _compact(raw, 220)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= limit:
            break
    return tuple(out)


def _enabled_from_env() -> bool:
    raw = os.getenv("ERIS_LIFE_HORIZON_ENABLED")
    if raw is None:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _default_state_path() -> Path:
    explicit = str(os.getenv("ERIS_LIFE_HORIZON_STATE_PATH", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = str(os.getenv("ERIS_STATE_DIR", ".eris_state") or ".eris_state").strip()
    return Path(root).expanduser() / "life_horizon_v74.json"


@dataclass(slots=True)
class HorizonGoal:
    goal_id: str
    level: HorizonLevel
    title: str
    description: str = ""
    status: GoalStatus = "active"
    strength: float = 0.5
    progress: float = 0.0
    parent_goal_id: str = ""
    deadline_at: float = 0.0
    source_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    updated_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.goal_id = _compact(self.goal_id, 180)
        self.level = str(self.level or "H1").upper()  # type: ignore[assignment]
        if self.level not in {"H0", "H1", "H2", "H3", "H4"}:
            self.level = "H1"  # type: ignore[assignment]
        self.title = _compact(self.title, 260)
        self.description = _compact(self.description, 1000)
        if self.status not in {"active", "dormant", "blocked", "completed", "retired"}:
            self.status = "dormant"
        self.strength = _clamp01(self.strength, 0.5)
        self.progress = _clamp01(self.progress, 0.0)
        self.parent_goal_id = _compact(self.parent_goal_id, 180)
        self.source_refs = _uniq(self.source_refs, limit=24)
        self.evidence_refs = _uniq(self.evidence_refs, limit=24)
        self.tags = _uniq(self.tags, limit=16)
        self.deadline_at = max(0.0, float(self.deadline_at or 0.0))
        self.updated_at = max(0.0, float(self.updated_at or time.time()))
        self.created_at = max(0.0, float(self.created_at or self.updated_at))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HorizonGoal":
        return cls(
            goal_id=str(data.get("goal_id") or ""),
            level=str(data.get("level") or "H1"),  # type: ignore[arg-type]
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            status=str(data.get("status") or "dormant"),  # type: ignore[arg-type]
            strength=data.get("strength", 0.5),
            progress=data.get("progress", 0.0),
            parent_goal_id=str(data.get("parent_goal_id") or ""),
            deadline_at=float(data.get("deadline_at") or 0.0),
            source_refs=tuple(data.get("source_refs") or ()),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            tags=tuple(data.get("tags") or ()),
            updated_at=float(data.get("updated_at") or time.time()),
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclass(slots=True)
class ProjectMilestone:
    milestone_id: str
    project_goal_id: str
    title: str
    completion_condition: str = ""
    status: GoalStatus = "active"
    progress: float = 0.0
    evidence_refs: tuple[str, ...] = ()
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.milestone_id = _compact(self.milestone_id, 180)
        self.project_goal_id = _compact(self.project_goal_id, 180)
        self.title = _compact(self.title, 300)
        self.completion_condition = _compact(self.completion_condition, 720)
        if self.status not in {"active", "dormant", "blocked", "completed", "retired"}:
            self.status = "dormant"
        self.progress = _clamp01(self.progress)
        self.evidence_refs = _uniq(self.evidence_refs, limit=24)
        self.updated_at = max(0.0, float(self.updated_at or time.time()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActionPlanItem:
    action_id: str
    project_goal_id: str
    title: str
    capability_hint: str = ""
    status: GoalStatus = "active"
    priority: float = 0.5
    evidence_refs: tuple[str, ...] = ()
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.action_id = _compact(self.action_id, 180)
        self.project_goal_id = _compact(self.project_goal_id, 180)
        self.title = _compact(self.title, 420)
        self.capability_hint = _compact(self.capability_hint, 140)
        if self.status not in {"active", "dormant", "blocked", "completed", "retired"}:
            self.status = "dormant"
        self.priority = _clamp01(self.priority, 0.5)
        self.evidence_refs = _uniq(self.evidence_refs, limit=24)
        self.updated_at = max(0.0, float(self.updated_at or time.time()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LifeHorizonSnapshot:
    revision: int
    captured_at: float
    goals: tuple[HorizonGoal, ...]
    milestones: tuple[ProjectMilestone, ...]
    next_actions: tuple[ActionPlanItem, ...]

    def active(self, *, levels: Sequence[str] = ()) -> tuple[HorizonGoal, ...]:
        wanted = {str(item).upper() for item in levels if str(item).strip()}
        return tuple(
            item for item in self.goals
            if item.status == "active" and (not wanted or item.level in wanted)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.life_horizon.v74",
            "revision": int(self.revision),
            "captured_at": float(self.captured_at),
            "goals": [item.to_dict() for item in self.goals],
            "milestones": [item.to_dict() for item in self.milestones],
            "next_actions": [item.to_dict() for item in self.next_actions],
        }


class LifeHorizonManager:
    """Kho mục tiêu phân tầng H0-H4 có persistence và bridge sang runtime hiện có."""

    def __init__(
        self,
        owner: Any | None = None,
        *,
        state_path: str | Path | None = None,
        enabled: bool | None = None,
        logger: Any | None = None,
    ) -> None:
        self.owner = owner
        self.logger = logger or getattr(owner, "logger", None)
        self.enabled = _enabled_from_env() if enabled is None else bool(enabled)
        self.state_path = Path(state_path).expanduser() if state_path is not None else _default_state_path()
        self._lock = threading.RLock()
        self._revision = 0
        self._goals: dict[str, HorizonGoal] = {}
        self._milestones: dict[str, ProjectMilestone] = {}
        self._actions: dict[str, ActionPlanItem] = {}
        self._load()

    def snapshot(self, *, refresh: bool = False, now: float | None = None) -> LifeHorizonSnapshot:
        if refresh and self.enabled:
            self.refresh_from_runtime(now=now)
        with self._lock:
            goals = tuple(sorted(self._goals.values(), key=lambda x: (x.level, -x.strength, x.goal_id)))
            milestones = tuple(sorted(self._milestones.values(), key=lambda x: (x.project_goal_id, x.milestone_id)))
            actions = tuple(sorted(self._actions.values(), key=lambda x: (-x.priority, x.action_id)))
            return LifeHorizonSnapshot(self._revision, float(now or time.time()), goals, milestones, actions)

    def upsert_goal(
        self,
        goal_id: str,
        *,
        level: HorizonLevel,
        title: str,
        description: str = "",
        status: GoalStatus = "active",
        strength: float = 0.5,
        progress: float = 0.0,
        parent_goal_id: str = "",
        deadline_at: float = 0.0,
        source_refs: Sequence[Any] = (),
        evidence_refs: Sequence[Any] = (),
        tags: Sequence[Any] = (),
        now: float | None = None,
        persist: bool = True,
    ) -> HorizonGoal:
        current = float(now or time.time())
        clean_id = _compact(goal_id, 180)
        if not clean_id:
            raise ValueError("goal_id must not be empty")
        with self._lock:
            old = self._goals.get(clean_id)
            item = HorizonGoal(
                goal_id=clean_id,
                level=level,
                title=title or (old.title if old else clean_id),
                description=description or (old.description if old else ""),
                status=status,
                strength=max(_clamp01(strength), old.strength if old and status == "active" else 0.0),
                progress=max(_clamp01(progress), old.progress if old else 0.0),
                parent_goal_id=parent_goal_id or (old.parent_goal_id if old else ""),
                deadline_at=deadline_at or (old.deadline_at if old else 0.0),
                source_refs=_uniq((*((old.source_refs) if old else ()), *source_refs), limit=24),
                evidence_refs=_uniq((*((old.evidence_refs) if old else ()), *evidence_refs), limit=24),
                tags=_uniq((*((old.tags) if old else ()), *tags), limit=16),
                updated_at=current,
                created_at=old.created_at if old else current,
            )
            self._goals[clean_id] = item
            self._revision += 1
        if persist:
            self._persist()
        return item

    def set_milestone(self, milestone: ProjectMilestone, *, persist: bool = True) -> None:
        with self._lock:
            self._milestones[milestone.milestone_id] = milestone
            self._revision += 1
        if persist:
            self._persist()

    def set_next_action(self, action: ActionPlanItem, *, persist: bool = True) -> None:
        with self._lock:
            self._actions[action.action_id] = action
            self._revision += 1
        if persist:
            self._persist()

    def observe_user_turn(self, *, turn_ref: str = "", now: float | None = None) -> None:
        """User input kết thúc H0 'đang chờ'; H1 được đồng bộ từ handoff typed riêng."""
        if not self.enabled:
            return
        current = float(now or time.time())
        self.upsert_goal(
            "h0.current_user_turn",
            level="H0",
            title="Xử lý lượt người dùng hiện tại",
            description="Ưu tiên phản hồi lượt foreground hiện tại trước mọi chủ động nền.",
            strength=1.0,
            progress=0.0,
            source_refs=(turn_ref,) if turn_ref else (),
            tags=("foreground", "conversation"),
            now=current,
            persist=False,
        )
        self._persist()

    def observe_assistant_commit(
        self,
        *,
        speech_act: str = "",
        turn_ref: str = "",
        now: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        current = float(now or time.time())
        act = _compact(speech_act or "public_commit", 120).lower()
        self.upsert_goal(
            "h0.last_public_action",
            level="H0",
            title=f"Public action: {act}",
            description="Receipt của hành động public vừa được commit.",
            strength=0.86,
            progress=1.0,
            source_refs=(turn_ref,) if turn_ref else (),
            tags=("public", act),
            now=current,
            persist=False,
        )
        self._persist()

    def refresh_from_runtime(self, *, now: float | None = None) -> LifeHorizonSnapshot:
        """Đồng bộ H1/H2/H4 từ các store đã có, không suy diễn mục tiêu từ prose."""
        if not self.enabled:
            return self.snapshot(now=now)
        current = float(now or time.time())
        owner = self.owner
        if owner is None:
            return self.snapshot(now=current)

        touched: set[str] = set()
        changed = False

        # V12 hard cut: ConversationHandoff chỉ là causal/provenance observer.
        # Không project handoff thành H1 goal vì như vậy LifeHorizon sẽ tái tạo
        # một semantic continuation authority bên ngoài RootAgent. H1 hội thoại
        # hiện nằm trong history + plain-text conversation note của Kernel.

        # H2/H4: PrivateLife đã có provenance và lifecycle. Chúng ta chỉ project,
        # không copy raw private diary hay biến một mood thoáng qua thành sứ mệnh.
        private_core = getattr(owner, "private_life_core", None)
        state = getattr(private_core, "state", None)
        projects = getattr(state, "projects", {}) if state is not None else {}
        if isinstance(projects, Mapping):
            for project_id, project in list(projects.items())[:64]:
                if str(getattr(project, "origin", "") or "") in {"legacy_authored", "legacy_unknown"}:
                    continue
                title = _compact(getattr(project, "title", ""), 260)
                question = _compact(getattr(project, "question", ""), 720)
                if not title:
                    continue
                gid = f"h2.private_project.{_compact(project_id, 100)}"
                status_raw = str(getattr(project, "status", "dormant") or "dormant").lower()
                status: GoalStatus = "active" if status_raw in {"active", "simmering"} else (
                    "blocked" if status_raw == "blocked" else "completed" if status_raw == "resolved" else "dormant"
                )
                interest = _clamp01(getattr(project, "interest", 0.0))
                support_count = max(0, int(getattr(project, "support_count", 0) or 0))
                strength = _clamp01(0.52 * interest + 0.48 * min(1.0, support_count / 5.0))
                refs = tuple(getattr(project, "source_refs", ()) or ())
                evidence = tuple(getattr(project, "evidence_ids", ()) or ())
                self.upsert_goal(
                    gid,
                    level="H2",
                    title=title,
                    description=question,
                    status=status,
                    strength=max(0.25, strength),
                    source_refs=refs,
                    evidence_refs=evidence,
                    tags=("private_project",),
                    now=current,
                    persist=False,
                )
                touched.add(gid)
                changed = True

        anchors = getattr(state, "anchors", {}) if state is not None else {}
        if isinstance(anchors, Mapping):
            for anchor_id, anchor in list(anchors.items())[:64]:
                if str(getattr(anchor, "origin", "") or "") in {"legacy_authored", "legacy_unknown"}:
                    continue
                if _compact(getattr(anchor, "retired_reason", ""), 120):
                    continue
                kind = str(getattr(anchor, "kind", "") or "").lower()
                if kind not in {"identity", "obsession", "private_agenda"}:
                    continue
                title = _compact(getattr(anchor, "title", ""), 260)
                if not title:
                    continue
                gid = f"h4.private_anchor.{_compact(anchor_id, 100)}"
                pressure = _clamp01(getattr(anchor, "pressure", 0.0))
                continuity = _clamp01(getattr(anchor, "continuity", 0.0))
                strength = _clamp01(0.42 * pressure + 0.58 * continuity)
                refs = tuple(getattr(anchor, "source_refs", ()) or ())
                evidence = tuple(getattr(anchor, "evidence_ids", ()) or ())
                self.upsert_goal(
                    gid,
                    level="H4",
                    title=title,
                    description=_compact(getattr(anchor, "question", "") or getattr(anchor, "stance", ""), 900),
                    status="active" if strength >= 0.28 else "dormant",
                    strength=strength,
                    source_refs=refs,
                    evidence_refs=evidence,
                    tags=("private_anchor", kind),
                    now=current,
                    persist=False,
                )
                touched.add(gid)
                changed = True

        # Derived goal cũ không còn nguồn active thì hạ xuống dormant, không xóa.
        with self._lock:
            for gid, item in tuple(self._goals.items()):
                if not gid.startswith(("h1.conversation.", "h2.private_project.", "h4.private_anchor.")):
                    continue
                if gid in touched or item.status in {"completed", "retired"}:
                    continue
                if item.status != "dormant":
                    item.status = "dormant"
                    item.updated_at = current
                    self._revision += 1
                    changed = True
        if changed:
            self._persist()
        return self.snapshot(now=current)

    def note_project_operation(self, result: Any, *, now: float | None = None) -> None:
        """Nhận receipt từ ProjectOperations để H2 cập nhật tiến độ từ kết quả đã xác minh."""
        if not self.enabled or result is None:
            return
        current = float(now or time.time())
        capability = _compact(getattr(result, "capability_id", ""), 140)
        operation_id = _compact(getattr(result, "operation_id", ""), 180)
        verified = bool(getattr(result, "verified", False))
        success = bool(getattr(result, "success", False))
        evidence_refs = tuple(
            _compact(getattr(item, "evidence_id", ""), 220)
            for item in tuple(getattr(result, "evidence", ()) or ())
            if _compact(getattr(item, "evidence_id", ""), 220)
        )
        # Chỉ gắn receipt vào H2 khi mapping đủ rõ. Nếu result có project id typed
        # thì dùng nó; nếu không, chỉ tự gắn khi đúng một H2 đang active. Tránh
        # cập nhật nhầm tiến độ khi nhiều project chạy song song.
        result_data = getattr(result, "data", {})
        result_data = dict(result_data) if isinstance(result_data, Mapping) else {}
        explicit_project = _compact(
            result_data.get("project_goal_id") or result_data.get("private_project_id") or "",
            180,
        )
        with self._lock:
            active_projects = [
                item for item in self._goals.values()
                if item.level == "H2" and item.status == "active"
            ]
            active_projects.sort(key=lambda x: (-x.strength, -x.updated_at, x.goal_id))
            target = None
            if explicit_project:
                candidate_ids = (
                    explicit_project,
                    f"h2.private_project.{explicit_project}",
                )
                target = next((self._goals.get(gid) for gid in candidate_ids if self._goals.get(gid) is not None), None)
            elif len(active_projects) == 1:
                target = active_projects[0]
        if target is not None:
            delta = 0.08 if success and verified else 0.0
            status: GoalStatus = "active" if success else ("blocked" if bool(getattr(result, "executed", False)) else target.status)
            self.upsert_goal(
                target.goal_id,
                level="H2",
                title=target.title,
                description=target.description,
                status=status,
                strength=target.strength,
                progress=min(1.0, target.progress + delta),
                source_refs=(*target.source_refs, f"project-op:{operation_id}" if operation_id else capability),
                evidence_refs=(*target.evidence_refs, *evidence_refs),
                tags=target.tags,
                now=current,
                persist=False,
            )
        if operation_id:
            self.set_next_action(ActionPlanItem(
                action_id=f"project-operation.{operation_id}",
                project_goal_id=target.goal_id if target is not None else "",
                title=f"{capability}: {'đã xác minh' if success and verified else 'cần xem lại'}",
                capability_hint=capability,
                status="completed" if success and verified else "blocked",
                priority=0.22,
                evidence_refs=evidence_refs,
                updated_at=current,
            ), persist=False)
        self._persist()

    def prompt_context(self, *, now: float | None = None) -> str:
        snapshot = self.snapshot(refresh=True, now=now)
        active = [item.to_dict() for item in snapshot.goals if item.status == "active"]
        # Prompt chỉ lấy projection bounded. Không ném cả đời sống riêng vào model.
        active.sort(key=lambda x: (x.get("level", ""), -float(x.get("strength", 0.0) or 0.0)))
        payload = {
            "schema": "eris.life_horizon.prompt.v74",
            "revision": snapshot.revision,
            "active_goals": active[:10],
            "next_actions": [item.to_dict() for item in snapshot.next_actions if item.status == "active"][:6],
        }
        return (
            "LIFE_HORIZON_V74 (goal coordination, not speech authority):\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\nRules: H0/H1 foreground obligations outrank unrelated initiative. H2-H4 may modulate priority, "
              "but never fabricate evidence, override an explicit user boundary, or authorize public speech by themselves."
        )

    def _persist(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            payload = {
                "schema": "eris.life_horizon.state.v74",
                "revision": self._revision,
                "goals": [item.to_dict() for item in self._goals.values()],
                "milestones": [item.to_dict() for item in self._milestones.values()],
                "next_actions": [item.to_dict() for item in self._actions.values()],
            }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as exc:
            self._log("warning", "[life-horizon] không ghi được state: %s", exc)

    def _load(self) -> None:
        if not self.enabled or not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            self._log("warning", "[life-horizon] state hỏng, bỏ qua: %s", exc)
            return
        try:
            goals = {
                item.goal_id: item
                for item in (HorizonGoal.from_mapping(x) for x in raw.get("goals", ()) if isinstance(x, Mapping))
                if item.goal_id
            }
            milestones = {
                str(x.get("milestone_id") or ""): ProjectMilestone(**dict(x))
                for x in raw.get("milestones", ()) if isinstance(x, Mapping) and str(x.get("milestone_id") or "").strip()
            }
            actions = {
                str(x.get("action_id") or ""): ActionPlanItem(**dict(x))
                for x in raw.get("next_actions", ()) if isinstance(x, Mapping) and str(x.get("action_id") or "").strip()
            }
            with self._lock:
                self._goals = goals
                self._milestones = milestones
                self._actions = actions
                self._revision = max(int(raw.get("revision") or 0), len(goals))
        except (TypeError, ValueError) as exc:
            self._log("warning", "[life-horizon] không parse được state: %s", exc)

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None)
        if callable(method):
            try:
                method(message, *args)
            except Exception:
                pass


def ensure_life_horizon_manager(owner: Any, *, state_path: str | Path | None = None) -> LifeHorizonManager:
    manager = getattr(owner, "life_horizon_manager", None)
    if isinstance(manager, LifeHorizonManager):
        return manager
    manager = LifeHorizonManager(owner, state_path=state_path, logger=getattr(owner, "logger", None))
    try:
        setattr(owner, "life_horizon_manager", manager)
    except Exception:
        pass
    return manager


__all__ = [
    "HorizonLevel",
    "GoalStatus",
    "HorizonGoal",
    "ProjectMilestone",
    "ActionPlanItem",
    "LifeHorizonSnapshot",
    "LifeHorizonManager",
    "ensure_life_horizon_manager",
]
