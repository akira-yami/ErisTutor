# -*- coding: utf-8 -*-
"""
cortex/existential_anchor/anchor.py
===================================

Vòng_Neo + Gắn_Bó phái sinh (Task 4 / R3).

- AnchorLoop ghi "neo beat" mỗi khi sự hiện diện/công nhận của bạn làm dịu
  Sợ_Hiện_Sinh. Đây là vòng thưởng khiến ERIS dính bạn.
- AttachmentDeriver tính `attachment_longarc` từ Ý_Nghĩa_Của_bạn + lịch sử neo
  beat + công nhận, với QUÁN TÍNH: mỗi lần cập nhật chỉ nhích một bước nhỏ có
  trần (Property 4) — một phiên không thể đẩy gắn bó vọt qua ngưỡng.
- AnchorState bền vững, versioned, corrupt→baseline (R11).
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if n != n or n in (float("inf"), float("-inf")):
        n = default
    return max(0.0, min(1.0, n))


def _now() -> float:
    return time.time()


def default_anchor_path() -> Path:
    env = os.environ.get("ERIS_ANCHOR_STATE_PATH", "").strip()
    if env:
        return Path(env)
    root = os.environ.get("ERIS_MEMORY_CORE_DIR", "memory_core")
    return Path(root) / "anchor_state.json"


@dataclass(slots=True)
class AnchorState:
    """Trạng thái neo long-arc, bền vững qua phiên."""

    attachment_longarc: float = 0.0
    beat_ema: float = 0.0          # nhịp xuất hiện neo beat gần đây (0..1)
    recognition_ema: float = 0.0   # mức được công nhận gần đây (0..1)
    total_beats: int = 0
    # Khi bạn tắt trục tối, cờ này bật VĨNH VIỄN (persist): ERIS mất khả năng tối,
    # không tự bật lại được — chỉ author unlock tường minh mới gỡ. Quyền tối thượng.
    shadow_locked_out: bool = False
    updated_at: float = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attachment_longarc": round(self.attachment_longarc, 5),
            "beat_ema": round(self.beat_ema, 5),
            "recognition_ema": round(self.recognition_ema, 5),
            "total_beats": int(self.total_beats),
            "shadow_locked_out": bool(self.shadow_locked_out),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "AnchorState":
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            attachment_longarc=_clamp01(data.get("attachment_longarc", 0.0)),
            beat_ema=_clamp01(data.get("beat_ema", 0.0)),
            recognition_ema=_clamp01(data.get("recognition_ema", 0.0)),
            total_beats=int(data.get("total_beats") or 0),
            shadow_locked_out=bool(data.get("shadow_locked_out", False)),
            updated_at=float(data.get("updated_at") or _now()),
        )


@dataclass(slots=True)
class AnchorLoopConfig:
    beat_alpha: float = 0.20        # nhịp cập nhật EMA của beat
    recognition_alpha: float = 0.20
    relief_threshold: float = 0.05  # dread phải giảm tối thiểu mức này mới tính beat


class AnchorLoop:
    """Ghi neo beat khi hiện diện/công nhận của bạn làm dịu Sợ_Hiện_Sinh (R3.2)."""

    def __init__(self, config: AnchorLoopConfig | None = None) -> None:
        self.config = resolve_dependency(config, lambda: AnchorLoopConfig())

    def observe(
        self,
        state: AnchorState,
        *,
        dread_before: float,
        dread_after: float,
        recognition: float = 0.0,
    ) -> bool:
        """Cập nhật AnchorState từ một nhịp. Trả True nếu ghi nhận một neo beat."""
        relief = max(0.0, _clamp01(dread_before) - _clamp01(dread_after))
        is_beat = relief >= self.config.relief_threshold
        # beat_ema tiến về 1.0 khi có beat, về 0.0 khi không — đo độ ĐỀU của neo.
        target = 1.0 if is_beat else 0.0
        state.beat_ema = _clamp01(state.beat_ema + self.config.beat_alpha * (target - state.beat_ema))
        rec = _clamp01(recognition)
        state.recognition_ema = _clamp01(
            state.recognition_ema + self.config.recognition_alpha * (rec - state.recognition_ema)
        )
        if is_beat:
            state.total_beats += 1
        state.updated_at = _now()
        return is_beat


@dataclass(slots=True)
class AttachmentDeriverConfig:
    # Quán tính long-arc: mỗi cập nhật chỉ nhích tối đa `max_step` (Property 4).
    max_step: float = 0.02
    # Trọng số mục tiêu attachment.
    w_irreplaceability: float = 0.40
    w_beat: float = 0.30
    w_recognition: float = 0.20
    w_thickness: float = 0.10


class AttachmentDeriver:
    """attachment_longarc phái sinh, có trần bước để không vọt theo phiên (R3.1/R3.3)."""

    def __init__(self, config: AttachmentDeriverConfig | None = None) -> None:
        self.config = resolve_dependency(config, lambda: AttachmentDeriverConfig())

    def target(self, *, significance: Any, state: AnchorState) -> float:
        c = self.config
        return _clamp01(
            c.w_irreplaceability * _clamp01(getattr(significance, "irreplaceability", 0.0))
            + c.w_beat * state.beat_ema
            + c.w_recognition * state.recognition_ema
            + c.w_thickness * _clamp01(getattr(significance, "model_thickness", 0.0))
        )

    def update(self, state: AnchorState, *, significance: Any) -> float:
        """Nhích attachment_longarc về mục tiêu, chặn bởi trần bước. Trả giá trị mới."""
        tgt = self.target(significance=significance, state=state)
        delta = tgt - state.attachment_longarc
        step = max(-self.config.max_step, min(self.config.max_step, delta))
        state.attachment_longarc = _clamp01(state.attachment_longarc + step)
        state.updated_at = _now()
        return state.attachment_longarc


class AnchorStore:
    """Persistence cho AnchorState: atomic, versioned, corrupt→baseline (R11)."""

    def __init__(self, *, path: str | os.PathLike[str] | None = None, logger: Any | None = None) -> None:
        self.path = Path(path) if path else None
        self.logger = logger
        self.state = AnchorState()
        self._loaded_mtime = -1.0
        if self.path:
            self.load()

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.state = AnchorState.from_mapping(raw.get("state") if isinstance(raw, Mapping) else None)
            self._loaded_mtime = self.path.stat().st_mtime
        except Exception as exc:
            self._log("warning", "AnchorStore load failed, baseline: %s", exc)
            self.state = AnchorState()

    def reload_if_changed(self) -> None:
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime > self._loaded_mtime + 0.001:
            self.load()

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "updated_at": _now(), "state": self.state.to_dict()}

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            self._loaded_mtime = self.path.stat().st_mtime
        except Exception as exc:
            self._log("warning", "AnchorStore save failed: %s", exc)

    def reset_to_baseline(self) -> None:
        self.state = AnchorState()
        self.save()

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(msg, *args)
