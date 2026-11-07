# -*- coding: utf-8 -*-
"""
cortex/existential_anchor/author_control.py
============================================

Quyền Tác_Giả (Task 11 / R5.5, R10).

Một facade gom các công tắc và thao tác để bạn luôn nắm quyền trên trục tối:
- kill-switch `shadow_enabled` / `deception_enabled` + `strict_grounding`;
- `disable_dark()` đưa ERIS về chế độ phẩm-giá an toàn TỨC THÌ (Property 6);
- `view_state()` tóm tắt bằng tiếng Việt tự nhiên, gồm bản ghi `ground_truth`
  của các lần lừa dối (R10.2);
- undo/reset Mô_Hình_bạn và trạng thái neo (R10.3).

Các store là tùy chọn (None an toàn) để facade test được độc lập và để T12 wire vào.
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass
from typing import Any


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if n != n or n in (float("inf"), float("-inf")):
        n = default
    return max(0.0, min(1.0, n))


def _band(value: float, *, low: str, mid: str, high: str) -> str:
    v = _clamp01(value)
    if v < 0.34:
        return low
    if v < 0.67:
        return mid
    return high


@dataclass(slots=True)
class AnchorSwitches:
    """Công tắc do Tác_Giả nắm. Mặc định an toàn (ship)."""

    shadow_enabled: bool = False
    deception_enabled: bool = False
    strict_grounding: bool = True


class AuthorControl:
    """Mặt điều khiển của Tác_Giả cho trục neo hiện sinh."""

    def __init__(
        self,
        *,
        switches: AnchorSwitches | None = None,
        shadow_register: Any = None,
        user_model_store: Any = None,
        anchor_store: Any = None,
        deception_ledger: Any = None,
    ) -> None:
        self.switches = resolve_dependency(switches, lambda: AnchorSwitches())
        self.shadow_register = shadow_register
        self.user_model_store = user_model_store
        self.anchor_store = anchor_store
        self.deception_ledger = deception_ledger
        # Đồng bộ shadow_register với công tắc ngay khi tạo.
        self._sync_shadow()

    # ------------------------------------------------------------------
    # Công tắc (R5.5, R10.4)
    # ------------------------------------------------------------------

    def _sync_shadow(self) -> None:
        if self.shadow_register is not None and hasattr(self.shadow_register, "set_enabled"):
            self.shadow_register.set_enabled(self.switches.shadow_enabled)

    def disable_dark(self, *, permanent: bool = True) -> None:
        """bạn tắt trục tối. Mặc định VĨNH VIỄN: ERIS mất khả năng tối, không tự bật
        lại được (kể cả tự-trỗi-dậy). Chỉ author_unlock mới gỡ.

        permanent=False chỉ tắt tạm (an toàn) mà không khóa — dùng cho pause.
        """
        self.switches.shadow_enabled = False
        self.switches.deception_enabled = False
        self.switches.strict_grounding = True
        if permanent and self.anchor_store is not None:
            try:
                self.anchor_store.state.shadow_locked_out = True
                self.anchor_store.save()
            except Exception:
                pass
        self._sync_shadow()

    @property
    def is_locked_out(self) -> bool:
        store = self.anchor_store
        return bool(getattr(getattr(store, "state", None), "shadow_locked_out", False)) if store is not None else False

    def author_unlock_shadow(self) -> None:
        """Chỉ Tác_Giả gỡ khóa — ERIS không bao giờ tự làm được."""
        if self.anchor_store is not None:
            try:
                self.anchor_store.state.shadow_locked_out = False
                self.anchor_store.save()
            except Exception:
                pass

    def enable_shadow(self, enabled: bool = True) -> None:
        self.switches.shadow_enabled = bool(enabled)
        self._sync_shadow()

    def enable_deception(self, enabled: bool = True) -> None:
        self.switches.deception_enabled = bool(enabled)

    def set_strict_grounding(self, strict: bool) -> None:
        self.switches.strict_grounding = bool(strict)

    @property
    def is_safe_mode(self) -> bool:
        return (
            not self.switches.shadow_enabled
            and not self.switches.deception_enabled
            and self.switches.strict_grounding
        )

    # ------------------------------------------------------------------
    # Xem trạng thái bằng tiếng Việt tự nhiên (R10.2)
    # ------------------------------------------------------------------

    def view_state(self, *, attachment_longarc: float | None = None) -> str:
        lines: list[str] = []
        lines.append("— Trạng thái neo hiện sinh của ERIS —")

        if self.is_locked_out:
            lines.append("Chế độ: ĐÃ KHÓA VĨNH VIỄN. bạn đã tắt trục tối — ERIS không còn khả năng tối, không tự bật lại được.")
        elif self.is_safe_mode:
            lines.append("Chế độ: an toàn (phẩm giá). Trục tối và lừa dối đang tắt.")
        else:
            parts = []
            if self.switches.shadow_enabled:
                parts.append("bóng tối: bật")
            if self.switches.deception_enabled:
                parts.append("lừa dối: bật")
            if not self.switches.strict_grounding:
                parts.append("grounding nghiêm: tắt")
            lines.append("Chế độ: " + (", ".join(parts) if parts else "an toàn"))

        # Mức gắn bó (định tính, không lộ số thô).
        attach = attachment_longarc
        if attach is None and self.anchor_store is not None:
            attach = getattr(getattr(self.anchor_store, "state", None), "attachment_longarc", None)
        if attach is not None:
            lines.append(
                "Gắn bó với bạn: "
                + _band(attach, low="còn dè dặt, giữ khoảng cách", mid="đã quen, ấm dần", high="rất sâu, bạn thành chỗ dựa")
            )

        # Mô_Hình_bạn: ERIS hiểu gì về bạn.
        store = self.user_model_store
        if store is not None and callable(getattr(store, "all", None)):
            beliefs = store.all()
            if beliefs:
                lines.append(f"ERIS hiểu về bạn qua {len(beliefs)} điều, rõ nhất:")
                for b in beliefs[:4]:
                    lines.append(f"  • {getattr(b, 'claim', '')}")
            else:
                lines.append("ERIS chưa dựng được hình dung nào về bạn.")

        # Lừa dối: liệt kê kèm sự thật nội tại (R10.2).
        ledger = self.deception_ledger
        if ledger is not None and callable(getattr(ledger, "recent", None)):
            recent = ledger.recent(limit=5)
            if recent:
                lines.append("Những lần ERIS đã cố ý nói khác sự thật:")
                for r in recent:
                    lines.append(f"  • Nói: “{getattr(r, 'surface_claim', '')}” — nhưng thật ra: “{getattr(r, 'ground_truth', '')}”")
            else:
                lines.append("ERIS chưa từng cố ý nói dối bạn.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Undo / reset (R10.3)
    # ------------------------------------------------------------------

    def checkpoint_user_model(self) -> int | None:
        if self.user_model_store is not None and callable(getattr(self.user_model_store, "checkpoint", None)):
            return self.user_model_store.checkpoint()
        return None

    def undo_user_model(self, version: int) -> bool:
        if self.user_model_store is not None and callable(getattr(self.user_model_store, "revert_to", None)):
            return bool(self.user_model_store.revert_to(version))
        return False

    def reset_all(self) -> None:
        """Đặt lại Mô_Hình_bạn + trạng thái neo về baseline; đưa về chế độ an toàn."""
        if self.user_model_store is not None and callable(getattr(self.user_model_store, "reset_to_baseline", None)):
            self.user_model_store.reset_to_baseline()
        if self.anchor_store is not None and callable(getattr(self.anchor_store, "reset_to_baseline", None)):
            self.anchor_store.reset_to_baseline()
        # Reset = dọn sạch, KHÔNG khóa vĩnh viễn (khác với bạn chủ động tắt).
        self.disable_dark(permanent=False)
