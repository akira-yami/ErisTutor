# -*- coding: utf-8 -*-
"""
runtime/private_life_daemon.py
==============================

Daemon đồng bộ cho PrivateLifeCore.

Tên daemon ở đây nghĩa là owner lifecycle, không phải thread riêng. Runtime chính
đã đủ nhiều vòng lặp; thêm thread chỉ để "sống hơn" là cách rất nhanh để biến
agent thành ổ khóa chết lâm sàng.

Daemon làm 5 việc:
1. load/save `memory_core/private_life_state.json`;
2. tick PrivateLifeCore theo nhịp runtime;
3. cung cấp state/memory cho owner công khai;
4. giữ surface queue cũ sau feature flag để migration;
5. không tự sinh lời thoại public.
"""

from __future__ import annotations
from core.di import resolve_dependency

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from cortex.private_life import PrivateLifeCore, PrivateLifeStore, SurfaceSeedCandidate, private_life_state_digest
from cortex.private_life.contracts import PrivateLifeTickResult, to_plain
from runtime.diagnostic_logging import safe_log
from runtime.architecture_flags import private_cognition_surface_export_enabled


@dataclass(slots=True)
class PrivateLifeDaemonConfig:
    enabled: bool = True
    min_interval_seconds: float = 15.0
    max_surface_queue: int = 8
    # PrivateLife chỉ cung cấp state/memory. Queue speech cũ giữ sau feature flag.
    surface_export_enabled: bool = False
    autosave: bool = True
    # Khi user vắng lâu, private life nên tick chậm mà đều. Không cần 60Hz cho đời sống nội tâm,
    # loài người đã thử overthinking rồi, kết quả không đẹp.
    solitude_tick_min_idle_seconds: float = 40.0

    @classmethod
    def from_env(cls) -> "PrivateLifeDaemonConfig":
        return cls(
            enabled=_env_bool("ERIS_PRIVATE_LIFE_ENABLED", True),
            min_interval_seconds=_env_float("ERIS_PRIVATE_LIFE_MIN_INTERVAL_SEC", 15.0, min_value=1.0),
            max_surface_queue=int(_env_float("ERIS_PRIVATE_LIFE_MAX_SURFACE_QUEUE", 8, min_value=1.0)),
            surface_export_enabled=private_cognition_surface_export_enabled(),
            autosave=_env_bool("ERIS_PRIVATE_LIFE_AUTOSAVE", True),
            solitude_tick_min_idle_seconds=_env_float("ERIS_PRIVATE_LIFE_SOLITUDE_IDLE_SEC", 40.0, min_value=0.0),
        )


@dataclass(slots=True)
class PrivateLifeDaemonTick:
    ticked: bool = False
    reason: str = ""
    core_result: PrivateLifeTickResult | None = None
    surface_queue_size: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticked": self.ticked,
            "reason": self.reason,
            "surface_queue_size": self.surface_queue_size,
            "created_at": self.created_at,
            "core_result": self.core_result.to_dict() if self.core_result is not None else None,
        }


class PrivateLifeDaemon:
    """Lifecycle owner cho PrivateLifeCore."""

    def __init__(
        self,
        *,
        core: PrivateLifeCore,
        store: PrivateLifeStore | None = None,
        config: PrivateLifeDaemonConfig | None = None,
        runtime_owner: Any = None,
        logger: Any = None,
    ) -> None:
        self.core = core
        self.store = store
        self.config = resolve_dependency(config, PrivateLifeDaemonConfig.from_env)
        self.runtime_owner = runtime_owner
        self.logger = logger
        self.surface_queue: list[Any] = []
        self.last_tick: PrivateLifeDaemonTick | None = None
        self.last_tick_at = 0.0
        self.tick_count = 0
        self.skipped_count = 0
        self.last_digest: dict[str, Any] = {}
        self.failure_count = 0
        self.sync_failure_count = 0
        self.last_error: dict[str, Any] = {}
        # Seed reservoir chạy background thread còn daemon tick đi theo runtime loop.
        # Một lock nhỏ rẻ hơn rất nhiều so với debug state JSON bị ghi xen kẽ lúc 3 giờ sáng.
        self._state_lock = threading.RLock()
        self._sync_runtime_owner()

    @classmethod
    def from_runtime(cls, *, runtime_owner: Any, logger: Any = None) -> "PrivateLifeDaemon | None":
        config = PrivateLifeDaemonConfig.from_env()
        if not config.enabled:
            return None
        store = PrivateLifeStore.from_env()
        state = store.load()
        root_dir = _infer_project_root(runtime_owner=runtime_owner, store=store)
        core = PrivateLifeCore(state=state, root_dir=root_dir)
        return cls(core=core, store=store, config=config, runtime_owner=runtime_owner, logger=logger)

    def tick(
        self,
        *,
        snapshot: Any = None,
        runtime_owner: Any = None,
        user_text: str = "",
        channel: str = "autonomous",
        idle_seconds: float | None = None,
        recent_events: Iterable[Any] = (),
        force: bool = False,
    ) -> PrivateLifeDaemonTick | None:
        if not self.config.enabled:
            return None
        now = time.time()
        owner = runtime_owner if runtime_owner is not None else self.runtime_owner
        inferred_idle = self._infer_idle(snapshot=snapshot, owner=owner, explicit=idle_seconds)
        if not force and now - self.last_tick_at < self.config.min_interval_seconds:
            # Cho phép tick sớm hơn nếu đã vào solitude rõ ràng; đây là đời sống riêng,
            # không phải stopwatch trang trí.
            if inferred_idle < self.config.solitude_tick_min_idle_seconds:
                self.skipped_count += 1
                tick = PrivateLifeDaemonTick(ticked=False, reason="rate_limited", surface_queue_size=len(self.surface_queue))
                self.last_tick = tick
                return tick
        try:
            subject_self: dict[str, Any] = {}
            subject_service = getattr(owner, "subject_service", None) if owner is not None else None
            self_view = getattr(subject_service, "self_view", None)
            if callable(self_view):
                try:
                    candidate = self_view()
                    if isinstance(candidate, Mapping):
                        subject_self = dict(candidate)
                except Exception as exc:
                    self._record_failure("subject_self_projection", exc)
            private_mind_digest = getattr(owner, "private_mind_digest", {}) if owner is not None else {}
            if not isinstance(private_mind_digest, Mapping):
                private_mind_digest = {}

            with self._state_lock:
                result = self.core.tick(
                    snapshot=snapshot,
                    runtime_owner=owner,
                    user_text=user_text,
                    channel=channel,
                    idle_seconds=inferred_idle,
                    recent_events=recent_events,
                    subject_self=subject_self,
                    private_mind_digest=private_mind_digest,
                    force=force,
                )
                self.last_tick_at = now
                self.tick_count += 1
                self._maybe_enqueue_surface_seed(result.surface_seed)
                if self.config.autosave and self.store is not None:
                    self.store.save(self.core.state)
                self.last_digest = self.digest()
                self._sync_runtime_owner()
            tick = PrivateLifeDaemonTick(
                ticked=True,
                reason="ticked",
                core_result=result,
                surface_queue_size=len(self.surface_queue),
                created_at=now,
            )
            self.last_tick = tick
            return tick
        except Exception as exc:
            self.skipped_count += 1
            self._record_failure("tick", exc)
            tick = PrivateLifeDaemonTick(ticked=False, reason=f"error:{type(exc).__name__}", surface_queue_size=len(self.surface_queue))
            self.last_tick = tick
            return tick

    def ingest_seed_mental_event(self, seed: Any) -> dict[str, Any] | None:
        """Nhận mental event từ background seed worker và persist vào private life.

        Đây là cầu nối có chủ đích giữa một DeepSeek seed worker và đời sống bền:
        reservoir vẫn chỉ chứa vài candidate, còn private_life nhớ các curiosity,
        thought và open-loop đủ đáng giữ. Không có speech nào được tạo ở đây.
        """

        if not self.config.enabled:
            return None
        try:
            with self._state_lock:
                event = self.core.ingest_seed_mental_event(seed)
                if event is None:
                    return None
                if self.config.autosave and self.store is not None:
                    self.store.save(self.core.state)
                self.last_digest = self.digest()
                self._sync_runtime_owner()
                return event.to_dict()
        except Exception as exc:
            self._record_failure("seed_mental_event", exc)
            return None

    def drain_surface_impulses(self, *, limit: int = 4) -> tuple[Any, ...]:
        """Đường migration cũ; mặc định PrivateLife không có quyền cấp speech."""

        if not self.config.surface_export_enabled:
            self.surface_queue.clear()
            return ()
        items: list[Any] = []
        while self.surface_queue and len(items) < max(1, int(limit or 4)):
            items.append(self.surface_queue.pop(0))
        self._sync_runtime_owner()
        return tuple(items)

    def digest(self, *, surface_queue: list[Any] | None = None) -> dict[str, Any]:
        state_digest = private_life_state_digest(self.core.state)
        queue = self.surface_queue if surface_queue is None else surface_queue
        tick_dict = self.last_tick.to_dict() if self.last_tick is not None else {}
        digest = {
            "last_tick_at": self.last_tick_at,
            "tick_count": self.tick_count,
            "skipped_count": self.skipped_count,
            "failure_count": self.failure_count,
            "sync_failure_count": self.sync_failure_count,
            "last_error": dict(self.last_error),
            "current": state_digest,
            "surface_queue": [self._impulse_digest(item) for item in list(queue or [])[:6]],
            "last_tick": tick_dict,
            "menu": {
                "habitat": state_digest.get("habitat", {}),
                "projects": state_digest.get("projects", []),
                "anchors": state_digest.get("anchors", []),
                "objects": state_digest.get("objects", []),
                "errands": state_digest.get("errands", []),
                "solitude": state_digest.get("solitude", {}),
                "last_events": state_digest.get("last_events", []),
                "surface_queue": [self._impulse_digest(item) for item in list(queue or [])[:6]],
            },
        }
        return to_plain(digest)

    def _maybe_enqueue_surface_seed(self, seed: SurfaceSeedCandidate | None) -> None:
        # PrivateLife là state/memory provider. Không tự tạo speech queue trừ khi
        # bật flag migration có chủ đích.
        if not self.config.surface_export_enabled:
            self.surface_queue.clear()
            return
        if seed is None or not seed.should_surface:
            return
        impulse = self._seed_to_surface_impulse(seed)
        if impulse is None:
            return
        key = (getattr(impulse, "source", ""), getattr(impulse, "kind", ""), getattr(impulse, "topic_seed", ""))
        for existing in self.surface_queue:
            existing_key = (getattr(existing, "source", ""), getattr(existing, "kind", ""), getattr(existing, "topic_seed", ""))
            if existing_key == key:
                return
        self.surface_queue.append(impulse)
        if len(self.surface_queue) > self.config.max_surface_queue:
            del self.surface_queue[: len(self.surface_queue) - self.config.max_surface_queue]

    def _seed_to_surface_impulse(self, seed: SurfaceSeedCandidate) -> Any | None:
        try:
            from cortex.surface_arbiter import SurfaceImpulse
        except (ImportError, AttributeError) as exc:
            self._record_failure("surface_import", exc)
            return seed.to_dict()
        return SurfaceImpulse(
            kind=seed.kind or "private_life_surface",
            source="private_life",
            confidence=seed.confidence,
            pressure=seed.pressure,
            score=min(1.0, 0.45 * seed.confidence + 0.35 * seed.pressure + 0.20 * seed.surface_permission),
            topic_seed=seed.topic_seed,
            speech_act="inner_whim",
            family="private_life",
            evidence=tuple(dict.fromkeys(("runtime_event:private_life", *tuple(seed.evidence_ids)))),
            reason="private_life_surface_seed",
            focus_family="private_life",
            body_action="idle_shift",
            missing_evidence_ids=True,
            is_presence=False,
            should_surface=True,
            needs_external_evidence=False,
            vital_pressure=seed.pressure,
            is_vital_pressure=seed.pressure >= 0.55,
            root_source="private_life",
            action_opportunity="one low-claim private-life line; no raw state dump",
            llm_contract=seed.llm_contract,
            emotional_anchor="private_life_residue",
            raw=seed.to_dict(),
        )

    def _infer_idle(self, *, snapshot: Any, owner: Any, explicit: float | None) -> float:
        if explicit is not None:
            return max(0.0, float(explicit or 0.0))
        for src in (snapshot, owner):
            for name in ("idle_seconds", "idle_time", "last_user_idle_seconds"):
                value = _value(src, name, None)
                if value is not None:
                    try:
                        return max(0.0, float(value or 0.0))
                    except (TypeError, ValueError, OverflowError) as exc:
                        self._record_failure("idle_parse", exc)
        return 0.0

    def _sync_runtime_owner(self) -> None:
        owner = self.runtime_owner
        if owner is None:
            return
        try:
            owner.private_life_core = self.core
            owner.private_life_surface_queue = self.surface_queue
            owner.private_life_digest = self.digest(surface_queue=self.surface_queue)
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            self.sync_failure_count += 1
            self._record_failure("sync_runtime_owner", exc, increment_total=False)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.config.enabled),
            "tick_count": self.tick_count,
            "skipped_count": self.skipped_count,
            "failure_count": self.failure_count,
            "sync_failure_count": self.sync_failure_count,
            "surface_queue_size": len(self.surface_queue),
            "last_error": dict(self.last_error),
        }

    def _record_failure(self, stage: str, exc: BaseException, *, increment_total: bool = True) -> None:
        if increment_total:
            self.failure_count += 1
        self.last_error = {
            "stage": stage,
            "error_type": type(exc).__name__,
            "message": str(exc)[:240],
            "count": self.failure_count,
        }
        safe_log(self.logger, "warning", "PrivateLife failure stage=%s: %s", stage, exc)

    @staticmethod
    def _impulse_digest(item: Any) -> dict[str, Any]:
        if isinstance(item, Mapping):
            return {
                "source": item.get("source", "private_life"),
                "kind": item.get("kind", "private_life_surface"),
                "topic_seed": item.get("topic_seed", ""),
                "confidence": item.get("confidence", 0.0),
                "pressure": item.get("pressure", 0.0),
            }
        return {
            "source": getattr(item, "source", "private_life"),
            "kind": getattr(item, "kind", "private_life_surface"),
            "topic_seed": getattr(item, "topic_seed", ""),
            "confidence": getattr(item, "confidence", 0.0),
            "pressure": getattr(item, "pressure", 0.0),
        }


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, *, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError, OverflowError):
        value = float(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _infer_project_root(*, runtime_owner: Any, store: PrivateLifeStore) -> Path:
    explicit = os.getenv("ERIS_PROJECT_ROOT", "").strip()
    if explicit:
        return Path(explicit).resolve()
    owner_file = getattr(runtime_owner, "__file__", None)
    if owner_file:
        return Path(owner_file).resolve().parents[1]
    # Nếu store nằm trong memory_core cạnh source root, parent của memory_core là root.
    try:
        parent = store.path.parent
        if parent.name == "memory_core":
            return parent.parent.resolve()
    except (AttributeError, TypeError, OSError, RuntimeError):
        pass
    return Path.cwd().resolve()
