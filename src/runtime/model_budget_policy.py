# -*- coding: utf-8 -*-
"""
src/runtime/model_budget_policy.py
==================================

Module sinh tử của tầng tự chủ.

Bản này không chỉ là gatekeeper nữa. Nó vẫn đo CPU/RAM/VRAM và veto như cũ,
nhưng có thêm hàng đợi inference dùng chung:

- global inference lock cho các model nặng;
- request lease để mark_started/mark_finished luôn đi theo vòng đời thật;
- queue có priority, timeout và cancel_event;
- hook unload sau khi request xong;
- snapshot biết main cognition đang active/recent, thay vì chỉ đoán bằng log đẹp.

Nói ngắn: mọi model *local nặng* phải xin chìa khóa trước khi chui vào VRAM. Một
phát minh vĩ đại ngang việc đóng cửa tủ lạnh sau khi mở, nhưng repo AI thường quên
cả chuyện đó.

V4 tách ba khái niệm từng bị trộn vào cùng một chuỗi ``ModelTier``:

- ``cognitive_lane``: request đang làm vai trò nhận thức gì;
- ``backend``: request đi qua DS2API/API/local transport nào;
- ``resource_class``: inference thật sự dùng tài nguyên local hay remote.

``ModelTier`` vẫn tồn tại để tương thích ngược, nhưng scheduler không còn được phép
suy ra "DS2API == local 4B" chỉ vì caller dùng nhãn ``subconscious_4b``.
"""

from __future__ import annotations
from core.di import resolve_dependency

import math
import os
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator, Literal

# ``ModelTier`` là tên legacy đã tồn tại từ trước. Tên này từng bị dùng để
# biểu diễn cả "vai trò nhận thức" lẫn "model local nào đang chiếm VRAM".
# V4 giữ lại nó để không phá caller cũ, nhưng không còn xem nó là nguồn sự thật
# duy nhất để suy ra tài nguyên nữa.
ModelTier = Literal["sensory_0_6b", "subconscious_4b", "main_cognition", "vision_compressor", "unknown"]

# Tài nguyên thật của một inference. Đây mới là thứ scheduler phải dùng để
# quyết định VRAM/cooldown. Ví dụ: một worker DeepSeek đi qua DS2API có thể vẫn
# mang legacy tier ``subconscious_4b`` nhưng resource_class phải là ``remote_api``.
ResourceClass = Literal["remote_api", "local_light", "local_heavy", "vision_local", "unknown"]

BudgetVerdict = Literal["allow", "block", "kill"]
QueueStatus = Literal["queued", "running", "cancelled", "finished", "vetoed", "timeout"]


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if math.isnan(n) or math.isinf(n):
        n = default
    return max(0.0, min(1.0, n))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        return default
    if math.isnan(n) or math.isinf(n):
        return default
    return n


def _env_float(name: str, default: float) -> float:
    return _safe_float(os.getenv(name), default)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        return default


@dataclass(slots=True)
class ResourceSnapshot:
    timestamp: float = field(default_factory=time.time)
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    gpu_util_percent: float = 0.0
    vram_used_mb: float = 0.0
    vram_total_mb: float = 0.0
    gpu_temperature_c: float = 0.0
    active_app: str = ""
    active_window_title: str = ""
    is_game_or_heavy_app: bool = False
    is_compile_or_godot: bool = False
    main_cognition_recent: bool = False
    telemetry_ok: bool = True
    active_model_tiers: tuple[str, ...] = ()
    eris_reserved_vram_mb: float = 0.0
    external_vram_used_mb: float = 0.0
    queue_depth: int = 0
    notes: tuple[str, ...] = ()

    @property
    def vram_percent(self) -> float:
        if self.vram_total_mb <= 0:
            return 0.0
        return max(0.0, min(100.0, self.vram_used_mb / self.vram_total_mb * 100.0))

    @property
    def resource_pressure(self) -> float:
        return _clamp01(max(self.cpu_percent / 100.0, self.ram_percent / 100.0, self.gpu_util_percent / 100.0, self.vram_percent / 100.0))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["vram_percent"] = self.vram_percent
        data["resource_pressure"] = self.resource_pressure
        return data


@dataclass(slots=True)
class BudgetDecision:
    verdict: BudgetVerdict
    allowed: bool
    tier: ModelTier
    reason: str
    resource_snapshot: ResourceSnapshot
    cooldown_seconds: float = 0.0
    hard_veto: bool = False
    debug: dict[str, Any] = field(default_factory=dict)
    # Ba trục này có ý nghĩa khác nhau:
    # - tier: nhãn legacy để caller cũ vẫn chạy;
    # - resource_class: tài nguyên thật local/remote;
    # - backend/cognitive_lane: telemetry để biết request đến từ đâu và đang làm gì.
    resource_class: ResourceClass = "unknown"
    backend: str = ""
    cognitive_lane: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "allowed": self.allowed,
            "tier": self.tier,
            "reason": self.reason,
            "resource_snapshot": self.resource_snapshot.to_dict(),
            "cooldown_seconds": self.cooldown_seconds,
            "hard_veto": self.hard_veto,
            "resource_class": self.resource_class,
            "backend": self.backend,
            "cognitive_lane": self.cognitive_lane,
            "debug": self.debug,
        }


@dataclass(slots=True)
class ModelQueueEntry:
    request_id: str
    tier: ModelTier
    reason: str = ""
    resource_class: ResourceClass = "unknown"
    backend: str = ""
    cognitive_lane: str = ""
    priority: int = 0
    enqueued_at: float = field(default_factory=time.time)
    timeout_seconds: float = 0.0
    sequence: int = 0
    status: QueueStatus = "queued"
    cancel_event: threading.Event | None = None
    last_budget_decision: BudgetDecision | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tier": self.tier,
            "reason": self.reason,
            "resource_class": self.resource_class,
            "backend": self.backend,
            "cognitive_lane": self.cognitive_lane,
            "priority": self.priority,
            "enqueued_at": self.enqueued_at,
            "age_seconds": max(0.0, time.time() - self.enqueued_at),
            "timeout_seconds": self.timeout_seconds,
            "sequence": self.sequence,
            "status": self.status,
            "last_budget_reason": self.last_budget_decision.reason if self.last_budget_decision else "",
        }


@dataclass(slots=True)
class InferenceLease:
    request_id: str
    tier: ModelTier
    reason: str
    priority: int
    started_at: float
    decision: BudgetDecision
    cancel_event: threading.Event | None = None
    resource_class: ResourceClass = "unknown"
    backend: str = ""
    cognitive_lane: str = ""

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)

    @property
    def cancelled(self) -> bool:
        return bool(self.cancel_event is not None and self.cancel_event.is_set())

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError(f"model_request_cancelled:{self.request_id}:{self.tier}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tier": self.tier,
            "reason": self.reason,
            "priority": self.priority,
            "resource_class": self.resource_class,
            "backend": self.backend,
            "cognitive_lane": self.cognitive_lane,
            "started_at": self.started_at,
            "age_seconds": self.age_seconds,
            "cancelled": self.cancelled,
            "budget": self.decision.to_dict(),
        }


@dataclass(slots=True)
class ModelBudgetPolicyConfig:
    enabled: bool = field(default_factory=lambda: _env_flag("ERIS_MODEL_BUDGET_POLICY", True))
    queue_enabled: bool = field(default_factory=lambda: _env_flag("ERIS_MODEL_QUEUE_ENABLED", True))
    resource_guard_enabled: bool = field(default_factory=lambda: _env_flag("ERIS_RESOURCE_GUARD_ENABLED", False))
    remote_api_enabled: bool = field(default_factory=lambda: _env_flag("ERIS_REMOTE_API_ENABLED", True))
    cpu_block_percent: float = field(default_factory=lambda: _env_float("ERIS_BUDGET_CPU_BLOCK", 92.0))
    ram_block_percent: float = field(default_factory=lambda: _env_float("ERIS_BUDGET_RAM_BLOCK", 90.0))
    gpu_block_percent: float = field(default_factory=lambda: _env_float("ERIS_BUDGET_GPU_BLOCK", 70.0))
    vram_block_percent: float = field(default_factory=lambda: _env_float("ERIS_BUDGET_VRAM_BLOCK", 72.0))
    gpu_temp_block_c: float = field(default_factory=lambda: _env_float("ERIS_BUDGET_GPU_TEMP_BLOCK", 82.0))
    main_cooldown_seconds: float = field(default_factory=lambda: _env_float("ERIS_BUDGET_MAIN_COOLDOWN", 18.0))
    four_b_cooldown_seconds: float = field(default_factory=lambda: _env_float("ERIS_BUDGET_4B_COOLDOWN", 60.0))
    default_queue_timeout_seconds: float = field(default_factory=lambda: _env_float("ERIS_MODEL_QUEUE_TIMEOUT", 90.0))
    queue_poll_seconds: float = field(default_factory=lambda: _env_float("ERIS_MODEL_QUEUE_POLL", 0.20))
    max_queue_size: int = field(default_factory=lambda: _env_int("ERIS_MODEL_QUEUE_MAX", 32))
    assume_gtx_1650ti_4gb: bool = field(default_factory=lambda: _env_flag("ERIS_ASSUME_4GB_VRAM", True))
    vision_bypass_vram_guard: bool = field(default_factory=lambda: _env_flag("ERIS_VISION_BYPASS_VRAM_GUARD", False))
    nvidia_smi_timeout: float = 0.35
    sensory_cpu_soft_block_percent: float = field(default_factory=lambda: _env_float("ERIS_SENSORY_CPU_SOFT_BLOCK", 96.0))
    sensory_ram_soft_block_percent: float = field(default_factory=lambda: _env_float("ERIS_SENSORY_RAM_SOFT_BLOCK", 94.0))
    heavy_foreground_gpu_percent: float = field(default_factory=lambda: _env_float("ERIS_HEAVY_FOREGROUND_GPU_BLOCK", 55.0))
    heavy_foreground_vram_percent: float = field(default_factory=lambda: _env_float("ERIS_HEAVY_FOREGROUND_VRAM_BLOCK", 60.0))
    local_main_vram_reservation_mb: float = field(default_factory=lambda: _env_float("ERIS_MAIN_VRAM_MB", 2300.0))
    four_b_vram_reservation_mb: float = field(default_factory=lambda: _env_float("ERIS_4B_VRAM_MB", 2200.0))
    vision_vram_reservation_mb: float = field(default_factory=lambda: _env_float("ERIS_VISION_VRAM_MB", 1100.0))
    sensory_vram_reservation_mb: float = field(default_factory=lambda: _env_float("ERIS_SENSORY_VRAM_MB", 450.0))
    vision_force_run: bool = field(default_factory=lambda: _env_flag("ERIS_VISION_FORCE_RUN", True))
    vision_force_run_priority: int = field(default_factory=lambda: _env_int("ERIS_VISION_FORCE_RUN_PRIORITY", 5000))
    vision_force_run_queue_timeout_seconds: float = field(default_factory=lambda: _env_float("ERIS_VISION_FORCE_RUN_QUEUE_TIMEOUT", 0.0))
    # Khi remote API tắt, main_cognition chạy bằng local GGUF — đây là bộ não DUY NHẤT.
    # Chặn nó vì áp lực tài nguyên/cooldown đồng nghĩa ERIS câm hẳn, nên ép chạy giống
    # vision_force_run: vẫn tôn trọng hard veto nhiệt độ GPU, nhưng vượt qua soft guard.
    force_local_gguf_enabled: bool = field(default_factory=lambda: _env_flag("ERIS_FORCE_LOCAL_GGUF", False))
    force_local_gguf_priority: int = field(default_factory=lambda: _env_int("ERIS_FORCE_LOCAL_GGUF_PRIORITY", 9000))
    force_local_gguf_queue_timeout_seconds: float = field(default_factory=lambda: _env_float("ERIS_FORCE_LOCAL_GGUF_QUEUE_TIMEOUT", 0.0))


class ModelBudgetPolicy:
    """Budget gatekeeper + scheduler nhẹ cho model inference.

    Đây chưa phải Kubernetes mini. Nó không thể giết request HTTP đã bay vào provider ngoài,
    vì Python không phải phù thủy. Nhưng nó ngăn request mới chạy chồng, giữ cooldown thật,
    cho phép cancel trước khi request bắt đầu, và gọi unload hook khi request kết thúc.
    """

    HEAVY_TIERS = {"main_cognition", "subconscious_4b", "vision_compressor"}

    def __init__(self, config: ModelBudgetPolicyConfig | None = None, logger: Any = None) -> None:
        self.config = resolve_dependency(config, lambda: ModelBudgetPolicyConfig())
        self.logger = logger
        self.last_main_started_at = 0.0
        self.last_main_finished_at = 0.0
        self.last_4b_started_at = 0.0
        self.last_4b_finished_at = 0.0
        self.last_snapshot = ResourceSnapshot()

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._queue: list[ModelQueueEntry] = []
        self._active_leases: dict[str, InferenceLease] = {}
        self._sequence = 0

    # ------------------------------------------------------------------
    # Snapshot / veto
    # ------------------------------------------------------------------
    def capture(self, *, active_app: str = "", active_window_title: str = "") -> ResourceSnapshot:
        """Đo tài nguyên best-effort.

        Không có psutil/nvidia-smi thì vẫn trả snapshot thay vì chết. Đáng tiếc là runtime
        cần sống trong thế giới thật, nơi dependency thiếu như cơm bữa.
        """

        cpu = 0.0
        ram = 0.0
        notes: list[str] = []
        try:
            import psutil  # type: ignore

            cpu = float(psutil.cpu_percent(interval=0.0))
            ram = float(psutil.virtual_memory().percent)
        except Exception as exc:
            notes.append(f"psutil_unavailable:{type(exc).__name__}")

        gpu_util, vram_used, vram_total, temp = self._capture_nvidia_smi(notes)
        app_blob = f"{active_app} {active_window_title}".lower()
        heavy = any(x in app_blob for x in ("game", "steam", "unity", "unreal", "genshin", "wuthering", "elden", "skyrim"))
        compile_or_godot = any(x in app_blob for x in ("godot", "visual studio", "msbuild", "compile", "pytest", "cmake", "ninja", "gcc", "clang"))
        now = time.time()
        with self._lock:
            active_tiers = tuple(lease.tier for lease in self._active_leases.values())
            active_main = any(lease.tier == "main_cognition" for lease in self._active_leases.values())
            reserved_vram = self._reserved_vram_for_active_leases_locked()
            queue_depth = len(self._queue)
        main_recent = active_main or (now - max(self.last_main_started_at, self.last_main_finished_at)) < self.config.main_cooldown_seconds
        if active_tiers:
            notes.append("active_model:" + ",".join(active_tiers))
        external_vram = max(0.0, vram_used - reserved_vram)
        snapshot = ResourceSnapshot(
            cpu_percent=cpu,
            ram_percent=ram,
            gpu_util_percent=gpu_util,
            vram_used_mb=vram_used,
            vram_total_mb=vram_total,
            gpu_temperature_c=temp,
            active_app=str(active_app or ""),
            active_window_title=str(active_window_title or ""),
            is_game_or_heavy_app=heavy,
            is_compile_or_godot=compile_or_godot,
            main_cognition_recent=main_recent,
            telemetry_ok=not any(note.startswith("psutil_unavailable") for note in notes),
            active_model_tiers=active_tiers,
            eris_reserved_vram_mb=reserved_vram,
            external_vram_used_mb=external_vram,
            queue_depth=queue_depth,
            notes=tuple(notes),
        )
        self.last_snapshot = snapshot
        return snapshot

    def can_run(
        self,
        tier: ModelTier,
        *,
        resource_snapshot: ResourceSnapshot | None = None,
        reason: str = "",
        ignore_active_lock: bool = False,
        resource_class: ResourceClass | str | None = None,
        backend: str = "",
        cognitive_lane: str = "",
    ) -> BudgetDecision:
        """Quyết định một inference có được chạy hay không.

        QUAN TRỌNG V4:
        ``tier`` chỉ còn là nhãn compatibility. Scheduler ưu tiên
        ``resource_class`` để biết request có chiếm local VRAM hay không.
        Vì vậy DeepSeek qua DS2API có thể mang tier ``subconscious_4b`` mà vẫn
        không bị áp cooldown/VRAM của model local 4B.
        """
        resolved_class = self._normalize_resource_class(tier, resource_class)
        if not self.config.enabled:
            snapshot = resource_snapshot or self.capture()
            return BudgetDecision(
                "allow",
                True,
                tier,
                "budget_policy_disabled",
                snapshot,
                debug={"reason": reason},
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )
        snapshot = resource_snapshot or self.capture()
        now = time.time()
        cooldown = self._cooldown_for(tier, now, resolved_class)
        uses_remote_api = self._uses_remote_api(tier, resolved_class)
        heavy_tier = self._is_heavy_tier(tier, resolved_class)
        reserved_vram = self._active_reserved_vram_mb(snapshot)
        external_vram_used = max(0.0, snapshot.external_vram_used_mb or (snapshot.vram_used_mb - reserved_vram))
        external_vram_percent = 0.0
        if snapshot.vram_total_mb > 0:
            external_vram_percent = max(0.0, min(100.0, external_vram_used / snapshot.vram_total_mb * 100.0))

        debug = {
            "request_reason": reason,
            "remote_api_enabled": bool(getattr(self.config, "remote_api_enabled", False)),
            "uses_remote_api": uses_remote_api,
            "queue_depth": len(self._queue),
            "heavy_tier": heavy_tier,
            "active_lock_ignored": bool(ignore_active_lock),
            "vision_vram_guard_bypassed": self._should_bypass_vision_vram_guard(tier),
            "reserved_vram_mb": round(reserved_vram, 2),
            "external_vram_used_mb": round(external_vram_used, 2),
            "external_vram_percent": round(external_vram_percent, 2),
            "resource_guard_enabled": bool(getattr(self.config, "resource_guard_enabled", False)),
            "resource_class": resolved_class,
            "backend": str(backend or ""),
            "cognitive_lane": str(cognitive_lane or ""),
        }

        if not getattr(self.config, "resource_guard_enabled", False):
            return BudgetDecision(
                verdict="allow",
                allowed=True,
                tier=tier,
                reason="scheduler_allow",
                resource_snapshot=snapshot,
                hard_veto=False,
                cooldown_seconds=0.0,
                debug=debug,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )

        if snapshot.gpu_temperature_c >= self.config.gpu_temp_block_c > 0:
            debug["overheat"] = round(snapshot.gpu_temperature_c, 2)
            return BudgetDecision(
                verdict="kill",
                allowed=False,
                tier=tier,
                reason="gpu_overheat",
                resource_snapshot=snapshot,
                hard_veto=True,
                debug=debug,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )

        if self._should_force_vision(tier, resolved_class):
            ignored_reasons = self._resource_block_reasons(
                tier,
                snapshot=snapshot,
                external_vram_percent=external_vram_percent,
                uses_remote_api=uses_remote_api,
            )
            debug["vision_force_run"] = True
            debug["ignored_resource_reasons"] = ignored_reasons
            if cooldown > 0.0:
                debug["ignored_cooldown_seconds"] = round(cooldown, 2)
            return BudgetDecision(
                verdict="allow",
                allowed=True,
                tier=tier,
                reason="vision_force_run",
                resource_snapshot=snapshot,
                hard_veto=False,
                cooldown_seconds=0.0,
                debug=debug,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )

        if self._should_force_local_gguf_main(tier, resolved_class):
            ignored_reasons = self._resource_block_reasons(
                tier,
                snapshot=snapshot,
                external_vram_percent=external_vram_percent,
                uses_remote_api=uses_remote_api,
            )
            debug["force_local_gguf"] = True
            debug["ignored_resource_reasons"] = ignored_reasons
            if cooldown > 0.0:
                debug["ignored_cooldown_seconds"] = round(cooldown, 2)
            return BudgetDecision(
                verdict="allow",
                allowed=True,
                tier=tier,
                reason="force_local_gguf_main",
                resource_snapshot=snapshot,
                hard_veto=False,
                cooldown_seconds=0.0,
                debug=debug,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )

        if heavy_tier and cooldown > 0.0:
            return BudgetDecision(
                verdict="block",
                allowed=False,
                tier=tier,
                reason="tier_cooldown_active",
                resource_snapshot=snapshot,
                cooldown_seconds=cooldown,
                hard_veto=False,
                debug=debug,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )

        resource_reasons = self._resource_block_reasons(
            tier,
            snapshot=snapshot,
            external_vram_percent=external_vram_percent,
            uses_remote_api=uses_remote_api,
        )
        if resource_reasons:
            debug["resource_reasons"] = resource_reasons
            return BudgetDecision(
                verdict="block",
                allowed=False,
                tier=tier,
                reason="resource_guard:" + ",".join(resource_reasons),
                resource_snapshot=snapshot,
                hard_veto=False,
                cooldown_seconds=max(cooldown, self.config.queue_poll_seconds),
                debug=debug,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )

        if heavy_tier and not ignore_active_lock and self._has_incompatible_active_lease(tier, resolved_class):
            return BudgetDecision(
                verdict="block",
                allowed=False,
                tier=tier,
                reason="active_heavy_lease",
                resource_snapshot=snapshot,
                hard_veto=False,
                cooldown_seconds=max(cooldown, self.config.queue_poll_seconds),
                debug=debug,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )

        return BudgetDecision(
            verdict="allow",
            allowed=True,
            tier=tier,
            reason="scheduler_allow",
            resource_snapshot=snapshot,
            hard_veto=False,
            cooldown_seconds=cooldown,
            debug=debug,
            resource_class=resolved_class,
            backend=str(backend or ""),
            cognitive_lane=str(cognitive_lane or ""),
        )

    # ------------------------------------------------------------------
    # Lease / queue / cancel
    # ------------------------------------------------------------------
    @contextmanager
    def lease(
        self,
        tier: ModelTier,
        *,
        reason: str = "",
        priority: int = 0,
        timeout_seconds: float | None = None,
        resource_snapshot: ResourceSnapshot | None = None,
        cancel_event: threading.Event | None = None,
        request_id: str | None = None,
        resource_class: ResourceClass | str | None = None,
        backend: str = "",
        cognitive_lane: str = "",
    ) -> Iterator[InferenceLease]:
        lease = self.acquire_lease(
            tier,
            reason=reason,
            priority=priority,
            timeout_seconds=timeout_seconds,
            resource_snapshot=resource_snapshot,
            cancel_event=cancel_event,
            request_id=request_id,
            resource_class=resource_class,
            backend=backend,
            cognitive_lane=cognitive_lane,
        )
        try:
            yield lease
        finally:
            self.release_lease(lease)

    def run_queued(
        self,
        tier: ModelTier,
        func: Callable[..., Any],
        *args: Any,
        reason: str = "",
        priority: int = 0,
        timeout_seconds: float | None = None,
        resource_snapshot: ResourceSnapshot | None = None,
        cancel_event: threading.Event | None = None,
        request_id: str | None = None,
        unload_callback: Callable[[], Any] | None = None,
        resource_class: ResourceClass | str | None = None,
        backend: str = "",
        cognitive_lane: str = "",
        **kwargs: Any,
    ) -> Any:
        """Chạy một inference qua queue và lease.

        `unload_callback` được gọi khi model call kết thúc nhưng trước khi thả lock, để unload
        không chạy song song với request tiếp theo. Đời vốn đã đủ hỗn loạn, không cần unload và
        load tranh nhau trên cùng VRAM.
        """

        with self.lease(
            tier,
            reason=reason,
            priority=priority,
            timeout_seconds=timeout_seconds,
            resource_snapshot=resource_snapshot,
            cancel_event=cancel_event,
            request_id=request_id,
            resource_class=resource_class,
            backend=backend,
            cognitive_lane=cognitive_lane,
        ) as active_lease:
            active_lease.check_cancelled()
            try:
                return func(*args, **kwargs)
            finally:
                if unload_callback is not None:
                    try:
                        unload_callback()
                    except Exception as exc:
                        self._log("warning", "[model queue] unload hook failed tier=%s request=%s error=%s", tier, active_lease.request_id, exc)

    def acquire_lease(
        self,
        tier: ModelTier,
        *,
        reason: str = "",
        priority: int = 0,
        timeout_seconds: float | None = None,
        resource_snapshot: ResourceSnapshot | None = None,
        cancel_event: threading.Event | None = None,
        request_id: str | None = None,
        resource_class: ResourceClass | str | None = None,
        backend: str = "",
        cognitive_lane: str = "",
    ) -> InferenceLease:
        resolved_class = self._normalize_resource_class(tier, resource_class)
        force_vision = self._should_force_vision(tier, resolved_class)
        force_local_main = self._should_force_local_gguf_main(tier, resolved_class)
        if force_vision:
            priority = max(int(priority), int(self.config.vision_force_run_priority))
            timeout_seconds = self.config.vision_force_run_queue_timeout_seconds
        elif force_local_main:
            priority = max(int(priority), int(self.config.force_local_gguf_priority))
            timeout_seconds = self.config.force_local_gguf_queue_timeout_seconds
        timeout = self.config.default_queue_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        request_id = request_id or f"{tier}:{uuid.uuid4().hex[:12]}"

        if not self.config.enabled or not self.config.queue_enabled:
            decision = self.can_run(
                tier,
                resource_snapshot=resource_snapshot,
                reason=reason,
                resource_class=resolved_class,
                backend=backend,
                cognitive_lane=cognitive_lane,
            )
            if not decision.allowed:
                raise RuntimeError(f"model_budget_veto:{tier}:{decision.reason}")
            lease = InferenceLease(
                request_id=request_id,
                tier=tier,
                reason=reason,
                priority=priority,
                started_at=time.time(),
                decision=decision,
                cancel_event=cancel_event,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )
            with self._lock:
                self._active_leases[lease.request_id] = lease
                self.mark_started(tier, resource_class=resolved_class)
            return lease

        with self._condition:
            if len(self._queue) >= self.config.max_queue_size and not (force_vision or force_local_main):
                raise RuntimeError(f"model_queue_full:{tier}:{len(self._queue)}")
            self._sequence += 1
            entry = ModelQueueEntry(
                request_id=request_id,
                tier=tier,
                reason=reason,
                priority=int(priority),
                timeout_seconds=max(0.0, timeout),
                sequence=self._sequence,
                cancel_event=cancel_event,
                resource_class=resolved_class,
                backend=str(backend or ""),
                cognitive_lane=str(cognitive_lane or ""),
            )
            self._queue.append(entry)
            self._sort_queue_locked()
            self._condition.notify_all()
            self._log("debug", "[model queue] queued tier=%s request=%s priority=%s reason=%s", tier, request_id, priority, reason)

            while True:
                now = time.time()
                if self._entry_cancelled(entry):
                    entry.status = "cancelled"
                    self._remove_queue_entry_locked(entry.request_id)
                    self._condition.notify_all()
                    raise RuntimeError(f"model_request_cancelled:{entry.request_id}:{tier}")
                if timeout > 0 and now - entry.enqueued_at >= timeout:
                    entry.status = "timeout"
                    self._remove_queue_entry_locked(entry.request_id)
                    self._condition.notify_all()
                    raise TimeoutError(f"model_queue_timeout:{entry.request_id}:{tier}:{reason}")
                if self._queue_head_locked() is not entry:
                    self._condition.wait(self._wait_seconds(entry, now))
                    continue
                if self._has_incompatible_active_lease_locked(tier, resolved_class):
                    self._condition.wait(self._wait_seconds(entry, now))
                    continue

                # Đến lượt mình rồi mới đo/veto. Nếu tài nguyên đang căng nhưng không hard veto,
                # request được giữ ở đầu queue và chờ tài nguyên hạ xuống.
                decision = self.can_run(
                    tier,
                    resource_snapshot=resource_snapshot,
                    reason=reason,
                    ignore_active_lock=True,
                    resource_class=resolved_class,
                    backend=backend,
                    cognitive_lane=cognitive_lane,
                )
                entry.last_budget_decision = decision
                if not decision.allowed:
                    if decision.hard_veto or decision.verdict == "kill":
                        entry.status = "vetoed"
                        self._remove_queue_entry_locked(entry.request_id)
                        self._condition.notify_all()
                        raise RuntimeError(f"model_budget_veto:{tier}:{decision.reason}")
                    self._condition.wait(self._wait_seconds(entry, now, decision=decision))
                    continue

                self._remove_queue_entry_locked(entry.request_id)
                entry.status = "running"
                lease = InferenceLease(
                    request_id=entry.request_id,
                    tier=tier,
                    reason=reason,
                    priority=priority,
                    started_at=time.time(),
                    decision=decision,
                    cancel_event=cancel_event,
                    resource_class=resolved_class,
                    backend=str(backend or ""),
                    cognitive_lane=str(cognitive_lane or ""),
                )
                self._active_leases[lease.request_id] = lease
                self.mark_started(tier, resource_class=resolved_class)
                self._condition.notify_all()
                self._log("debug", "[model queue] acquired tier=%s request=%s reason=%s", tier, request_id, reason)
                return lease

    def release_lease(self, lease: InferenceLease | None) -> None:
        if lease is None:
            return
        with self._condition:
            existing = self._active_leases.pop(lease.request_id, None)
            if existing is not None:
                self.mark_finished(existing.tier, resource_class=existing.resource_class)
                self._log("debug", "[model queue] released tier=%s request=%s age=%.2fs", existing.tier, existing.request_id, existing.age_seconds)
            self._condition.notify_all()

    def cancel_queued_request(self, request_id: str) -> bool:
        """Hủy một request chưa chạy. Request đã vào HTTP call thì không giết được ở đây."""
        with self._condition:
            for entry in self._queue:
                if entry.request_id == request_id:
                    entry.status = "cancelled"
                    if entry.cancel_event is not None:
                        entry.cancel_event.set()
                    self._remove_queue_entry_locked(request_id)
                    self._condition.notify_all()
                    return True
            return False

    def cancel_all_queued(self, *, tier: ModelTier | None = None, reason: str = "") -> int:
        with self._condition:
            ids = [entry.request_id for entry in self._queue if tier is None or entry.tier == tier]
            for request_id in ids:
                self._remove_queue_entry_locked(request_id)
            self._condition.notify_all()
            if ids:
                self._log("info", "[model queue] cancelled queued=%d tier=%s reason=%s", len(ids), tier or "*", reason)
            return len(ids)

    def queue_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.config.queue_enabled,
                "queued": [entry.to_dict() for entry in self._queue],
                "active": [lease.to_dict() for lease in self._active_leases.values()],
                "last_main_started_at": self.last_main_started_at,
                "last_main_finished_at": self.last_main_finished_at,
                "last_4b_started_at": self.last_4b_started_at,
                "last_4b_finished_at": self.last_4b_finished_at,
            }

    # ------------------------------------------------------------------
    # Legacy-compatible markers
    # ------------------------------------------------------------------
    def mark_started(self, tier: ModelTier, *, resource_class: ResourceClass | str | None = None) -> None:
        # Remote API không được làm bẩn cooldown marker của model local. Nếu không,
        # một DS2API call mang nhãn ``subconscious_4b`` sẽ làm local 4B bị cooldown
        # oan dù nó không hề dùng VRAM máy người dùng.
        resolved = self._normalize_resource_class(tier, resource_class)
        if resolved == "remote_api":
            return
        now = time.time()
        if tier == "main_cognition":
            self.last_main_started_at = now
        elif tier in {"subconscious_4b", "vision_compressor"}:
            self.last_4b_started_at = now

    def mark_finished(self, tier: ModelTier, *, resource_class: ResourceClass | str | None = None) -> None:
        resolved = self._normalize_resource_class(tier, resource_class)
        if resolved == "remote_api":
            return
        now = time.time()
        if tier == "main_cognition":
            self.last_main_finished_at = now
        elif tier in {"subconscious_4b", "vision_compressor"}:
            self.last_4b_finished_at = now

    def veto_or_raise(
        self,
        tier: ModelTier,
        *,
        resource_snapshot: ResourceSnapshot | None = None,
        reason: str = "",
        resource_class: ResourceClass | str | None = None,
        backend: str = "",
        cognitive_lane: str = "",
    ) -> BudgetDecision:
        decision = self.can_run(
            tier,
            resource_snapshot=resource_snapshot,
            reason=reason,
            resource_class=resource_class,
            backend=backend,
            cognitive_lane=cognitive_lane,
        )
        if not decision.allowed:
            raise RuntimeError(f"model_budget_veto:{tier}:{decision.reason}")
        return decision

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _cooldown_for(self, tier: str, now: float, resource_class: ResourceClass | str | None = None) -> float:
        if self._normalize_resource_class(tier, resource_class) == "remote_api":
            return 0.0
        if tier == "main_cognition":
            return max(0.0, self.config.main_cooldown_seconds - (now - self.last_main_started_at))
        if tier in {"subconscious_4b", "vision_compressor"}:
            return max(0.0, self.config.four_b_cooldown_seconds - (now - self.last_4b_started_at))
        return 0.0

    def _capture_nvidia_smi(self, notes: list[str]) -> tuple[float, float, float, float]:
        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=self.config.nvidia_smi_timeout,
            )
            if proc.returncode != 0:
                notes.append("nvidia_smi_nonzero")
                return 0.0, 0.0, 0.0, 0.0
            lines = (proc.stdout or "").strip().splitlines()
            if not lines:
                notes.append("nvidia_smi_empty")
                return 0.0, 0.0, 0.0, 0.0
            parts = [x.strip() for x in lines[0].split(",")]
            return (_safe_float(parts[0]), _safe_float(parts[1]), _safe_float(parts[2]), _safe_float(parts[3]))
        except Exception as exc:
            notes.append(f"nvidia_smi_unavailable:{type(exc).__name__}")
            return 0.0, 0.0, 0.0, 0.0

    def _normalize_resource_class(
        self,
        tier: str,
        resource_class: ResourceClass | str | None = None,
    ) -> ResourceClass:
        """Chuẩn hoá hint tài nguyên về một giá trị ổn định.

        Caller V4 nên truyền explicit ``resource_class``. Fallback bên dưới chỉ
        tồn tại để caller legacy vẫn giữ nghĩa cũ trong giai đoạn migration.
        """
        raw = str(resource_class or "").strip().lower()
        if raw in {"remote_api", "local_light", "local_heavy", "vision_local", "unknown"}:
            return raw  # type: ignore[return-value]
        if tier == "sensory_0_6b":
            return "local_light"
        if tier == "vision_compressor":
            return "vision_local"
        if tier == "main_cognition":
            return "remote_api" if bool(getattr(self.config, "remote_api_enabled", False)) else "local_heavy"
        if tier == "subconscious_4b":
            return "local_heavy"
        return "unknown"

    def _is_heavy_tier(self, tier: str, resource_class: ResourceClass | str | None = None) -> bool:
        return self._normalize_resource_class(tier, resource_class) in {"local_heavy", "vision_local"}

    def _uses_remote_api(self, tier: str, resource_class: ResourceClass | str | None = None) -> bool:
        return self._normalize_resource_class(tier, resource_class) == "remote_api"

    def _should_force_vision(self, tier: str, resource_class: ResourceClass | str | None = None) -> bool:
        return (
            tier == "vision_compressor"
            and self._normalize_resource_class(tier, resource_class) == "vision_local"
            and bool(getattr(self.config, "vision_force_run", False))
        )

    def _should_force_local_gguf_main(self, tier: str, resource_class: ResourceClass | str | None = None) -> bool:
        """main_cognition chạy local GGUF (remote API tắt) → ép chạy: nó là não duy nhất."""
        return (
            tier == "main_cognition"
            and not self._uses_remote_api(tier, resource_class)
            and bool(getattr(self.config, "force_local_gguf_enabled", False))
        )

    def _estimated_vram_need_mb(self, tier: str, resource_class: ResourceClass | str | None = None) -> float:
        resolved = self._normalize_resource_class(tier, resource_class)
        if resolved == "remote_api":
            return 0.0
        if tier == "main_cognition":
            return max(0.0, float(self.config.local_main_vram_reservation_mb))
        if tier == "subconscious_4b":
            return max(0.0, float(self.config.four_b_vram_reservation_mb))
        if tier == "vision_compressor":
            return max(0.0, float(self.config.vision_vram_reservation_mb))
        if tier == "sensory_0_6b":
            return max(0.0, float(self.config.sensory_vram_reservation_mb))
        return 0.0

    def _should_bypass_vision_vram_guard(self, tier: str) -> bool:
        if tier != "vision_compressor":
            return False
        return bool(
            getattr(self.config, "vision_bypass_vram_guard", False)
            or getattr(self.config, "assume_gtx_1650ti_4gb", False)
        )

    def _reserved_vram_for_active_leases_locked(self) -> float:
        return sum(self._estimated_vram_need_mb(lease.tier, lease.resource_class) for lease in self._active_leases.values())

    def _active_reserved_vram_mb(self, snapshot: ResourceSnapshot) -> float:
        reserved = float(getattr(snapshot, "eris_reserved_vram_mb", 0.0) or 0.0)
        if reserved > 0.0:
            return reserved
        with self._lock:
            return self._reserved_vram_for_active_leases_locked()

    def _has_incompatible_active_lease(
        self, tier: ModelTier, resource_class: ResourceClass | str | None = None
    ) -> bool:
        with self._lock:
            return self._has_incompatible_active_lease_locked(tier, resource_class)

    def _resource_block_reasons(
        self,
        tier: ModelTier,
        *,
        snapshot: ResourceSnapshot,
        external_vram_percent: float,
        uses_remote_api: bool,
    ) -> list[str]:
        reasons: list[str] = []
        cpu = float(snapshot.cpu_percent or 0.0)
        ram = float(snapshot.ram_percent or 0.0)
        gpu = float(snapshot.gpu_util_percent or 0.0)
        local_tier = not uses_remote_api and tier != "unknown"
        sensory_tier = tier == "sensory_0_6b"

        if sensory_tier:
            if cpu >= self.config.sensory_cpu_soft_block_percent:
                reasons.append("cpu_busy")
            if ram >= self.config.sensory_ram_soft_block_percent:
                reasons.append("ram_busy")
            return reasons

        if cpu >= self.config.cpu_block_percent:
            reasons.append("cpu_busy")
        if ram >= self.config.ram_block_percent:
            reasons.append("ram_busy")

        if local_tier:
            if gpu >= self.config.gpu_block_percent:
                reasons.append("gpu_busy")
            if external_vram_percent >= self.config.vram_block_percent and not self._should_bypass_vision_vram_guard(tier):
                reasons.append("vram_busy")
            if snapshot.is_game_or_heavy_app and gpu >= self.config.heavy_foreground_gpu_percent:
                reasons.append("heavy_foreground_gpu_owner")
            if snapshot.is_compile_or_godot and tier in {"subconscious_4b", "vision_compressor"} and (cpu >= 70.0 or ram >= 75.0):
                reasons.append("compile_or_editor_pressure")

        return list(dict.fromkeys(reasons))

    def _entry_cancelled(self, entry: ModelQueueEntry) -> bool:
        return bool(entry.cancel_event is not None and entry.cancel_event.is_set())

    def _sort_queue_locked(self) -> None:
        self._queue.sort(key=lambda entry: (-entry.priority, entry.enqueued_at, entry.sequence))

    def _queue_head_locked(self) -> ModelQueueEntry | None:
        self._sort_queue_locked()
        return self._queue[0] if self._queue else None

    def _remove_queue_entry_locked(self, request_id: str) -> None:
        self._queue = [entry for entry in self._queue if entry.request_id != request_id]

    def _has_incompatible_active_lease_locked(
        self, tier: ModelTier, resource_class: ResourceClass | str | None = None
    ) -> bool:
        if not self._is_heavy_tier(tier, resource_class):
            return False
        return any(self._is_heavy_tier(lease.tier, lease.resource_class) for lease in self._active_leases.values())

    def _wait_seconds(self, entry: ModelQueueEntry, now: float, *, decision: BudgetDecision | None = None) -> float:
        poll = max(0.05, float(self.config.queue_poll_seconds))
        wait = poll
        if decision is not None and decision.cooldown_seconds > 0:
            wait = min(max(poll, decision.cooldown_seconds), 1.0)
        if entry.timeout_seconds > 0:
            remaining = max(0.0, entry.timeout_seconds - (now - entry.enqueued_at))
            wait = min(wait, remaining or poll)
        return wait

    def _log(self, level: str, msg: str, *args: Any) -> None:
        logger = self.logger
        if logger is None:
            return
        method = getattr(logger, level, None)
        if callable(method):
            try:
                method(msg, *args)
            except Exception:
                pass
