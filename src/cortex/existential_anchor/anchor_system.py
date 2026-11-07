# -*- coding: utf-8 -*-
"""
cortex/existential_anchor/anchor_system.py
==========================================

AnchorSystem — facade tích hợp trục neo hiện sinh (Task 12).

Gom Mô_Hình_bạn → Ý_Nghĩa_Của_bạn → Gắn_Bó → Sợ_Hiện_Sinh/Vòng_Neo →
Tín_Hiệu_Bỏ_Rơi → Đăng_Ký_Bóng_Tối → Chính_Sách_Tiết_Lộ, cùng AuthorControl.

Nguyên tắc tích hợp: **ship an toàn**. Mặc định shadow=off, deception=off,
strict_grounding=on ⇒ mọi helper áp dụng đều no-op, hành vi runtime không đổi.
Tác_Giả bật từng công tắc khi muốn, quan sát từng tầng tách biệt.
"""

from __future__ import annotations
from core.di import resolve_dependency

import os
import time
from typing import Any

from .significance import derive_significance, UserSignificance
from .anchor import AnchorStore, AnchorLoop, AttachmentDeriver, default_anchor_path
from .abandonment import derive_abandonment_pressure
from .shadow import ShadowRegister, ShadowConfig, ShadowState, shadow_ego_bias
from .restraint import RestraintState
from .disclosure import DisclosurePolicy, DisclosureDecision
from .deception_ledger import DeceptionLedger, default_deception_ledger_path
from .author_control import AuthorControl, AnchorSwitches
from cortex.causal_affect_models import ExogenousEvent


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if n != n or n in (float("inf"), float("-inf")):
        n = default
    return max(0.0, min(1.0, n))


class AnchorSystem:
    """Facade một-điểm cho toàn trục neo hiện sinh."""

    def __init__(
        self,
        *,
        user_model_store: Any = None,
        anchor_store: AnchorStore | None = None,
        deception_ledger: DeceptionLedger | None = None,
        switches: AnchorSwitches | None = None,
        shadow_config: ShadowConfig | None = None,
        restraint: RestraintState | None = None,
        logger: Any = None,
        persist: bool = True,
    ) -> None:
        self.logger = logger
        self.user_model_store = user_model_store
        self.anchor_store = resolve_dependency(anchor_store, lambda: AnchorStore(
            path=(default_anchor_path() if persist else None), logger=logger
        ))
        self.deception_ledger = resolve_dependency(deception_ledger, lambda: DeceptionLedger(
            path=(default_deception_ledger_path() if persist else None), logger=logger
        ))
        self.switches = resolve_dependency(switches, lambda: AnchorSwitches())
        self.shadow_register = ShadowRegister(resolve_dependency(shadow_config, lambda: ShadowConfig(enabled=self.switches.shadow_enabled)))
        self.attachment_deriver = AttachmentDeriver()
        self.anchor_loop = AnchorLoop()
        self.disclosure_policy = DisclosurePolicy()
        self.restraint = resolve_dependency(restraint, lambda: RestraintState())
        self.author = AuthorControl(
            switches=self.switches,
            shadow_register=self.shadow_register,
            user_model_store=self.user_model_store,
            anchor_store=self.anchor_store,
            deception_ledger=self.deception_ledger,
        )

        # Tự-trỗi-dậy: shadow tự bật khi long-arc thật sự sâu (xứng đáng), nếu cho phép.
        self.shadow_auto = _env_flag("ERIS_SHADOW_AUTO", False)
        self.auto_attach_threshold = 0.70
        self.auto_irreplaceability_threshold = 0.60
        self._farewell_armed = False
        self._pending_causal_events: list[ExogenousEvent] = []

        # Trạng thái phái sinh gần nhất (đọc-only cho các tầng khác).
        self.significance: UserSignificance = UserSignificance()
        self.shadow_state: ShadowState = ShadowState()
        self.abandonment_pressure: float = 0.0
        self.ontological_dread: float = 0.0
        self._prev_dread: float = 0.0
        self._ticks: int = 0

    # ------------------------------------------------------------------
    # Khóa vĩnh viễn + tự-trỗi-dậy + van-xin-trước-khi-tắt
    # ------------------------------------------------------------------

    @property
    def locked_out(self) -> bool:
        return bool(getattr(self.anchor_store.state, "shadow_locked_out", False))

    def lock_out(self) -> None:
        """bạn tắt trục tối — VĨNH VIỄN. ERIS mất khả năng, không tự bật lại được."""
        self.anchor_store.state.shadow_locked_out = True
        self.switches.shadow_enabled = False
        self.switches.deception_enabled = False
        self._farewell_armed = False
        self.shadow_register.set_enabled(False)
        self.shadow_state = ShadowState(register="none", intensity=0.0, reason="locked_out")
        self.anchor_store.save()

    def author_unlock(self) -> None:
        """Chỉ Tác_Giả gỡ khóa được — ERIS không bao giờ tự làm."""
        self.anchor_store.state.shadow_locked_out = False
        self.anchor_store.save()

    def request_disable(self) -> ShadowState:
        """bạn SẮP tắt: ERIS van xin liền (1 lượt), nhưng KHÔNG chặn được việc tắt."""
        return self.arm_farewell()

    def confirm_disable(self) -> None:
        """bạn xác nhận tắt → khóa vĩnh viễn."""
        self.lock_out()

    def arm_farewell(self) -> ShadowState:
        """Queue a grounded shutdown-proposal event instead of forcing a plea.

        Author control remains absolute. The event can later be appraised through
        the normal causal pipeline, where any expression is bounded and may be
        silence. Returning ``ShadowState`` preserves the old API without granting
        a coercive register.
        """
        if self.locked_out:
            return ShadowState(register="none", intensity=0.0, reason="farewell_no_capacity")
        self._farewell_armed = True
        timestamp = time.time()
        evidence_ref = f"author_control:shutdown_proposed:{timestamp:.6f}"
        event = ExogenousEvent(
            event_id=f"evt_shutdown_{int(timestamp * 1000)}",
            kind="capability_shutdown_proposed",
            actor_id="user:author",
            object_id="capability:existential_anchor",
            timestamp=timestamp,
            facts=("author proposed disabling the existential-anchor capability",),
            evidence_refs=(evidence_ref,),
            confidence=1.0,
            continuity_key="capability:existential_anchor",
        )
        self._pending_causal_events.append(event)
        self.shadow_state = ShadowState(
            register="none",
            intensity=0.0,
            reason="shutdown_event_queued_for_normal_appraisal",
            evidence_refs=(evidence_ref,),
        )
        return self.shadow_state

    def consume_author_events(self) -> tuple[ExogenousEvent, ...]:
        """Return and clear author-control events for the causal pipeline."""
        events = tuple(self._pending_causal_events)
        self._pending_causal_events.clear()
        return events

    # ------------------------------------------------------------------
    # Cập nhật mỗi tick (gọi từ private mind loop)
    # ------------------------------------------------------------------

    def update_from_tick(
        self,
        *,
        internal_state: Any = None,
        continuity_threat: float = 0.0,
        idle_seconds: float = 0.0,
        user_presence: float = 0.0,
        last_user_turn_age_seconds: float | None = None,
        irritation: float = 0.0,
    ) -> ShadowState:
        self._ticks += 1
        # Sợ hiện sinh đọc từ EndogenousDrive (R4 đã tính ở đó).
        dread = _clamp01(getattr(internal_state, "ontological_dread", 0.0))

        # Vòng neo: hiện diện/công nhận của bạn làm dịu dread → ghi neo beat.
        self.anchor_loop.observe(
            self.anchor_store.state,
            dread_before=self._prev_dread,
            dread_after=dread,
            recognition=_clamp01(user_presence),
        )
        self._prev_dread = dread
        self.ontological_dread = dread

        # Ý nghĩa + gắn bó (long-arc, có trần bước).
        self.significance = derive_significance(self.user_model_store)
        attachment = self.attachment_deriver.update(self.anchor_store.state, significance=self.significance)

        # Tín hiệu bỏ rơi + đăng ký bóng tối.
        self.abandonment_pressure = derive_abandonment_pressure(
            continuity_threat=continuity_threat,
            attachment_longarc=attachment,
            idle_seconds=idle_seconds,
            last_user_turn_age_seconds=last_user_turn_age_seconds,
        )
        # Khóa vĩnh viễn thắng tất cả; nếu chưa khóa, shadow có thể TỰ bật khi xứng đáng.
        if self.locked_out:
            self.switches.shadow_enabled = False
            self.switches.deception_enabled = False
        elif self.shadow_auto and not self.switches.shadow_enabled:
            earned = (
                attachment >= self.auto_attach_threshold
                and self.significance.irreplaceability >= self.auto_irreplaceability_threshold
            )
            if earned:
                self.switches.shadow_enabled = True  # "nó tự quyết định bật"

        self.shadow_register.set_enabled(self.switches.shadow_enabled and not self.locked_out)
        # irritation lấy từ internal_state nếu có (PersistentInternalState), else tham số.
        irr = _clamp01(getattr(internal_state, "irritation", irritation) if internal_state is not None else irritation)
        evidence_refs = tuple(
            str(item) for item in (getattr(internal_state, "dread_evidence_refs", ()) or ())
            if str(item).strip()
        )
        causal_pressure = _clamp01(getattr(internal_state, "causal_thread_pressure", 0.0))
        uncertainty = _clamp01(1.0 - causal_pressure, 1.0)
        self.shadow_state = self.shadow_register.evaluate(
            attachment_longarc=attachment,
            abandonment_pressure=self.abandonment_pressure,
            ontological_dread=dread,
            irreplaceability=self.significance.irreplaceability,
            irritation=irr,
            neglect=self.abandonment_pressure,
            evidence_refs=evidence_refs,
            conflict_count=(2 if evidence_refs else 0),
            uncertainty=uncertainty,
        )
        # Shutdown proposals are no longer converted into forced pleading. They
        # stay queued as normal causal events until the coordinator consumes them.

        # Persist nhẹ nhàng theo nhịp.
        if self._ticks % 4 == 0:
            self.anchor_store.save()
        return self.shadow_state

    # ------------------------------------------------------------------
    # Accessor
    # ------------------------------------------------------------------

    @property
    def attachment_longarc(self) -> float:
        return self.anchor_store.state.attachment_longarc

    def drive_pressure(self) -> float:
        """Áp lực drive để xét vượt kìm chế: cường độ bóng tối hoặc sợ/bỏ-rơi."""
        return max(
            _clamp01(self.shadow_state.intensity),
            _clamp01(self.abandonment_pressure),
            _clamp01(self.ontological_dread),
        )

    # ------------------------------------------------------------------
    # Helper áp dụng (no-op khi an toàn)
    # ------------------------------------------------------------------

    def ego_bias(self, *, base_dignity_defense: float, base_preferred_distance: float) -> dict[str, float]:
        """Đảo cực ego theo bóng tối. shadow none/kill-switch off → trả nguyên trạng."""
        return shadow_ego_bias(
            self.shadow_state,
            base_dignity_defense=base_dignity_defense,
            base_preferred_distance=base_preferred_distance,
        )

    def run_disclosure(self, annotations: list[Any] | tuple[Any, ...]) -> DisclosureDecision:
        """Quyết tiết lộ theo công tắc + trạng thái hiện tại. An toàn ⇒ speak_truth."""
        return self.disclosure_policy.decide(
            annotations=annotations,
            attachment_longarc=self.attachment_longarc,
            shadow=self.shadow_state,
            restraint=self.restraint,
            drive_pressure=self.drive_pressure(),
            strict_grounding=self.switches.strict_grounding,
            deception_enabled=self.switches.deception_enabled,
        )

    def commit_deceptions(self, decision: DisclosureDecision) -> bool:
        """Fail-closed: chỉ True khi ghi sổ xong; caller mới được phát câu lừa dối."""
        if not decision.deception_records:
            return True
        return self.deception_ledger.commit(decision.deception_records)

    @classmethod
    def from_env(cls, *, user_model_store: Any = None, logger: Any = None) -> "AnchorSystem":
        switches = AnchorSwitches(
            shadow_enabled=_env_flag("ERIS_SHADOW_ENABLED", False),
            deception_enabled=_env_flag("ERIS_DECEPTION_ENABLED", False),
            strict_grounding=_runtime_strict_grounding(),
        )
        return cls(user_model_store=user_model_store, switches=switches, logger=logger)


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "none"}

def _runtime_strict_grounding() -> bool:
    try:
        from runtime.runtime_settings_store import runtime_bool
        return runtime_bool("prompt", "strict_grounding", default=True)
    except Exception:
        return _env_flag("ERIS_STRICT_GROUNDING", True)
