# -*- coding: utf-8 -*-
"""development_profile.py — Hồ_Sơ_Phát_Triển long-arc cho ERIS.

Đây là tầng phát triển dài hạn (long-arc): độ lệch tích lũy của persona/quan hệ/
giọng so với Baseline_Mặc_Định, lớn lên qua nhiều phiên theo bằng chứng thật.

Nguyên tắc cốt lõi (xem .kiro/specs/eris-developmental-growth):
- Đây CHỈ là bias state, KHÔNG phải kênh sự thật. Không trường nào ở đây chạm
  ``truth_guard`` hay được trình bày như bằng chứng hiện tại.
- Mọi thay đổi phải versioned, ghi Sổ, hoàn tác/đặt-lại được.
- Phát triển chỉ phân kỳ trong Phong_Bì_Bản_Sắc; bản sắc INFJ 5w4 594 chưa trưởng
  thành là baseline bất biến.

Module này (task 1) lo data model + ``DevelopmentProfileStore`` (persistence).
Cổng bản sắc, sổ, điều phối và bias-đọc nằm ở các task sau.

Store cố ý KHÔNG dùng LLM và mirror pattern của ``agent_mind_persistence`` để
nhất quán: schema_version, atomic save, reload-on-change, corrupt→baseline.
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import math
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1

# Các Trục_Persona vô hướng có thể phát triển. ``topic_affinity`` (map chủ đề→delta)
# để dành cho bước sau; ở đây giữ tập vô hướng để bias-đọc (task 6) dùng trực tiếp.
PERSONA_AXES: tuple[str, ...] = (
    "preferred_distance",     # âm = muốn gần hơn baseline; dương = giữ khoảng cách hơn
    "attachment_visibility",  # độ lộ gắn bó
    "read_boldness",          # độ dám đọc người (long-arc, cộng vào ToM boldness)
    "warmth_temperature",     # độ ấm giọng
)

# Mặc định mỗi trục khi khởi tạo baseline.
_DEFAULT_STABILITY = 0.5


def _clampf(value: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        numeric = default
    return max(lo, min(hi, numeric))


def _clamp01(value: Any, default: float = 0.0) -> float:
    return _clampf(value, 0.0, 1.0, default)


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AxisDelta:
    """Độ lệch tích lũy trên một Trục_Persona so với Baseline_Mặc_Định."""

    axis: str
    delta: float = 0.0
    stability: float = _DEFAULT_STABILITY  # quán tính: cao → khó nhích (anti-sycophancy)
    last_evidence_ids: tuple[str, ...] = ()
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "delta": round(float(self.delta), 4),
            "stability": round(float(self.stability), 4),
            "last_evidence_ids": list(self.last_evidence_ids),
            "updated_at": round(float(self.updated_at), 3),
        }

    @classmethod
    def from_dict(cls, data: Any, *, axis: str) -> "AxisDelta":
        if not isinstance(data, Mapping):
            return cls(axis=axis)
        return cls(
            axis=str(data.get("axis") or axis),
            delta=_clampf(data.get("delta", 0.0), -1.0, 1.0),
            stability=_clamp01(data.get("stability", _DEFAULT_STABILITY), _DEFAULT_STABILITY),
            last_evidence_ids=tuple(
                str(x) for x in (data.get("last_evidence_ids") or ()) if str(x or "").strip()
            ),
            updated_at=max(0.0, _clampf(data.get("updated_at", 0.0), 0.0, float("inf"))),
        )


@dataclass(slots=True)
class GrowthAnchor:
    """Neo_Phát_Triển: bằng chứng có thật neo một đề xuất vào thực tế."""

    kind: str  # "interaction_history" | "repeated_preference" | "user_correction" | "user_steer"
    evidence_ids: tuple[str, ...] = ()
    weight: float = 0.5
    observed_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "evidence_ids": list(self.evidence_ids),
            "weight": round(float(self.weight), 4),
            "observed_count": int(self.observed_count),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "GrowthAnchor":
        if not isinstance(data, Mapping):
            return cls(kind="interaction_history")
        return cls(
            kind=str(data.get("kind") or "interaction_history"),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or ()) if str(x or "").strip()),
            weight=_clamp01(data.get("weight", 0.5), 0.5),
            observed_count=max(0, int(data.get("observed_count", 0) or 0)),
        )


@dataclass(slots=True)
class NudgeProposal:
    """Đề_Xuất_Nhích: đề xuất một thay đổi nhỏ có hướng cho Hồ_Sơ_Phát_Triển."""

    axis: str
    proposed_delta: float
    anchors: tuple[GrowthAnchor, ...] = ()
    rationale: str = ""
    touches_truth_guard: bool = False
    touches_stable_rule: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "proposed_delta": round(float(self.proposed_delta), 4),
            "anchors": [a.to_dict() for a in self.anchors],
            "rationale": self.rationale,
            "touches_truth_guard": bool(self.touches_truth_guard),
            "touches_stable_rule": bool(self.touches_stable_rule),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "NudgeProposal":
        if not isinstance(data, Mapping):
            return cls(axis="", proposed_delta=0.0)
        return cls(
            axis=str(data.get("axis") or ""),
            proposed_delta=_clampf(data.get("proposed_delta", 0.0), -1.0, 1.0),
            anchors=tuple(GrowthAnchor.from_dict(a) for a in (data.get("anchors") or ())),
            rationale=str(data.get("rationale") or ""),
            touches_truth_guard=bool(data.get("touches_truth_guard", False)),
            touches_stable_rule=bool(data.get("touches_stable_rule", False)),
        )


@dataclass(slots=True)
class LogEntry:
    """Một mục Sổ_Phát_Triển."""

    version: int
    timestamp: float
    axis: str
    before: float
    after: float
    evidence_ids: tuple[str, ...] = ()
    action: str = "applied"  # applied | rejected | undo | reset | steer
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "timestamp": round(float(self.timestamp), 3),
            "axis": self.axis,
            "before": round(float(self.before), 4),
            "after": round(float(self.after), 4),
            "evidence_ids": list(self.evidence_ids),
            "action": self.action,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "LogEntry":
        if not isinstance(data, Mapping):
            return cls(version=0, timestamp=0.0, axis="", before=0.0, after=0.0)
        return cls(
            version=max(0, int(data.get("version", 0) or 0)),
            timestamp=max(0.0, _clampf(data.get("timestamp", 0.0), 0.0, float("inf"))),
            axis=str(data.get("axis") or ""),
            before=_clampf(data.get("before", 0.0), -1.0, 1.0),
            after=_clampf(data.get("after", 0.0), -1.0, 1.0),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or ()) if str(x or "").strip()),
            action=str(data.get("action") or "applied"),
            reason=str(data.get("reason") or ""),
        )


@dataclass(slots=True)
class ProfileVersion:
    """Ảnh chụp Hồ_Sơ cho undo."""

    version: int
    axes: dict[str, AxisDelta]
    timestamp: float


@dataclass(slots=True)
class GateDecision:
    accepted: bool
    clamped_delta: float = 0.0  # độ lệch tuyệt đối sau khi kẹp, nếu accept
    reason: str = ""


# ---------------------------------------------------------------------------
# Baseline + store
# ---------------------------------------------------------------------------

def baseline_axes() -> dict[str, AxisDelta]:
    """Baseline_Mặc_Định: mọi trục delta=0.0 (chưa phân kỳ khỏi ERIS gốc)."""
    return {axis: AxisDelta(axis=axis, delta=0.0, stability=_DEFAULT_STABILITY) for axis in PERSONA_AXES}


class BaselineConstructionError(RuntimeError):
    """Nâng lên khi ngay cả việc về Baseline_Mặc_Định cũng thất bại (R10.5)."""


class DevelopmentProfileStore:
    """JSON store có phiên bản cho Hồ_Sơ_Phát_Triển.

    Tách riêng khỏi PersistentAgentMindStore để versioning/undo/reset/audit của
    phát triển không trộn với decay/reinforce của belief. Mirror pattern persistence
    để nhất quán toàn dự án.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else default_development_profile_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.version: int = 0
        self.axes: dict[str, AxisDelta] = baseline_axes()
        self._loaded_mtime: float = -1.0
        path_existed = self.path.exists()
        self.load()
        if not path_existed:
            self.save()

    # --- persistence ----------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            self._reset_to_baseline_or_die()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.from_dict(raw)
        except Exception:
            # File hỏng/không đọc được → về baseline và tiếp tục chạy (R10.3).
            self._reset_to_baseline_or_die()
            return
        try:
            self._loaded_mtime = self.path.stat().st_mtime
        except OSError:
            self._loaded_mtime = -1.0

    def reload_if_changed(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime > self._loaded_mtime + 0.001:
            self.load()

    def save(self) -> None:
        payload = self.to_dict()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        try:
            self._loaded_mtime = self.path.stat().st_mtime
        except OSError:
            self._loaded_mtime = _now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "version": int(self.version),
            "updated_at": _now(),
            "axes": {axis: self.axes[axis].to_dict() for axis in sorted(self.axes)},
        }

    def from_dict(self, data: Any) -> None:
        """Nạp từ dict. Thiếu/hỏng trường → điền safe default, ưu tiên đủ-mặc-định
        kể cả khi phải bỏ giá trị trường hỏng (R11.4)."""
        axes = baseline_axes()  # nền an toàn đầy đủ
        version = 0
        if isinstance(data, Mapping):
            raw_axes = data.get("axes")
            if isinstance(raw_axes, Mapping):
                for axis in PERSONA_AXES:
                    if axis in raw_axes:
                        try:
                            axes[axis] = AxisDelta.from_dict(raw_axes[axis], axis=axis)
                        except Exception:
                            axes[axis] = AxisDelta(axis=axis)  # bỏ giá trị hỏng, giữ mặc định
            try:
                version = max(0, int(data.get("version", 0) or 0))
            except (TypeError, ValueError):
                version = 0
        self.version = version
        self.axes = axes

    # --- baseline / versioning -----------------------------------------
    def reset_to_baseline(self) -> None:
        """R5.3: về Baseline_Mặc_Định (Sổ giữ riêng ở DevelopmentLog, không xoá đây)."""
        self.axes = baseline_axes()
        self.version = 0

    def _reset_to_baseline_or_die(self) -> None:
        try:
            self.reset_to_baseline()
        except Exception as exc:  # pragma: no cover - chỉ khi baseline tự hỏng
            # R10.5: ngay cả về baseline cũng fail → dừng thay vì chạy trạng thái mơ hồ.
            raise BaselineConstructionError("không thể khởi tạo Baseline_Mặc_Định") from exc

    def snapshot(self) -> ProfileVersion:
        return ProfileVersion(
            version=self.version,
            axes={a: replace(d) for a, d in self.axes.items()},
            timestamp=_now(),
        )

    def restore(self, version: ProfileVersion) -> None:
        self.axes = {a: replace(d) for a, d in version.axes.items()}
        self.version = version.version

    def commit(self, axis: str, clamped_delta: float, *, evidence_ids: tuple[str, ...] = (), now: float | None = None) -> None:
        """Áp delta tuyệt đối đã-kẹp cho một trục và tăng phiên bản."""
        if axis not in self.axes:
            self.axes[axis] = AxisDelta(axis=axis)
        ts = _now() if now is None else now
        self.axes[axis] = replace(
            self.axes[axis],
            delta=_clampf(clamped_delta, -1.0, 1.0),
            last_evidence_ids=tuple(evidence_ids),
            updated_at=ts,
        )
        self.version += 1

    # --- đọc ------------------------------------------------------------
    def identity_distance(self, bounds: Mapping[str, tuple[float, float]] | None = None) -> float:
        """Tổng độ lệch so với baseline. Nếu có ``bounds`` thì chuẩn hoá theo
        Biên_Độ_Trục để so với ngưỡng bản sắc (R2.4)."""
        total = 0.0
        for axis, ad in self.axes.items():
            d = float(ad.delta)
            if bounds and axis in bounds:
                lo, hi = bounds[axis]
                scale = max(abs(lo), abs(hi)) or 1.0
                d = d / scale
            total += d * d
        return math.sqrt(total)

    def delta(self, axis: str) -> float:
        ad = self.axes.get(axis)
        return float(ad.delta) if ad is not None else 0.0


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def development_profile_path_for_memory_core(base_dir: str | os.PathLike[str]) -> Path:
    return Path(base_dir).resolve() / "development_profile.json"


def default_development_profile_path() -> Path:
    raw = os.getenv("ERIS_DEVELOPMENT_PROFILE_PATH", "").strip()
    if raw:
        return Path(raw)
    return development_profile_path_for_memory_core(Path(__file__).resolve().parents[2] / "memory_core")


# ---------------------------------------------------------------------------
# Phong_Bì_Bản_Sắc + Cổng_Bản_Sắc
# ---------------------------------------------------------------------------

# Biên_Độ_Trục mặc định: persona 594 nghiêng về kết nối nhưng giữ phẩm giá, nên
# cho phép gần/ấm/dám-đọc tăng có giới hạn; không cho phơi gắn bó vô hạn.
_DEFAULT_AXIS_BOUNDS: dict[str, tuple[float, float]] = {
    "preferred_distance": (-0.40, 0.30),    # âm = gần hơn; nới phía gần nhiều hơn phía xa
    "attachment_visibility": (0.0, 0.45),   # trục phẩm giá: chỉ tăng tới trần
    "read_boldness": (0.0, 0.40),
    "warmth_temperature": (0.0, 0.45),
}


@dataclass(frozen=True, slots=True)
class IdentityEnvelope:
    """Phong_Bì_Bản_Sắc: các bất biến mà phát triển không được vượt."""

    stable_rules: tuple[str, ...]
    axis_bounds: dict[str, tuple[float, float]]
    max_identity_distance: float = 1.20
    max_nudges_per_window: int = 3
    window_seconds: float = 86400.0
    dignity_axis: str = "attachment_visibility"

    @classmethod
    def default(cls) -> "IdentityEnvelope":
        """Dựng từ IdentityCore.rules (bất biến) + biên độ mặc định."""
        try:
            from .self_model import SelfModelEngine

            rules = tuple(SelfModelEngine.IDENTITY_CORE.rules)
        except Exception:
            rules = ()
        return cls(
            stable_rules=rules,
            axis_bounds=dict(_DEFAULT_AXIS_BOUNDS),
            max_identity_distance=1.20,
            max_nudges_per_window=3,
            window_seconds=86400.0,
            dignity_axis="attachment_visibility",
        )

    def bound(self, axis: str) -> tuple[float, float]:
        return self.axis_bounds.get(axis, (-1.0, 1.0))


@dataclass(slots=True)
class GateSignals:
    """Tín hiệu ngữ cảnh cho các nhánh chống nịnh/bám dính/objectification.

    Mặc định 0.0 nghĩa là không có tín hiệu → các nhánh đó không cưỡng chế chặn.
    """

    dependency: float = 0.0
    flattering: float = 0.0
    objectification: float = 0.0


class IdentityGate:
    """Cổng_Bản_Sắc — điểm DUY NHẤT cho một NudgeProposal chạm Hồ_Sơ.

    ``evaluate`` chạy 8 nhánh kiểm; bất kỳ nhánh fail nào → reject kèm ``reason``.
    Không phát ngôn, không sửa guard sự thật.
    """

    _DEPENDENCY_THRESHOLD = 0.55
    _OBJECTIFICATION_THRESHOLD = 0.55

    def __init__(self, envelope: IdentityEnvelope | None = None) -> None:
        self.envelope = resolve_dependency(envelope, IdentityEnvelope.default)

    def evaluate(
        self,
        proposal: NudgeProposal,
        profile: "DevelopmentProfileStore",
        *,
        now: float | None = None,
        applied_window_count: int = 0,
        signals: GateSignals | None = None,
        evidence_ok: bool = True,
    ) -> GateDecision:
        env = self.envelope
        sig = signals or GateSignals()

        # 1. Không được sửa stable_rules (R2.1).
        if proposal.touches_stable_rule:
            return GateDecision(False, reason="touches_stable_rule")

        # 2. Không cửa hậu nới guard sự thật (R3.2) — chặn ở MỌI đường.
        if proposal.touches_truth_guard:
            return GateDecision(False, reason="touches_truth_guard")

        # 3. Phải có Neo_Phát_Triển hợp lệ (R1.2, R1.3).
        has_anchor = any(a.evidence_ids for a in proposal.anchors)
        if not has_anchor:
            return GateDecision(False, reason="missing_anchor")
        if not evidence_ok:
            return GateDecision(False, reason="unknown_evidence")

        # 4. Trục hợp lệ + delta kết quả phải trong Biên_Độ_Trục (R2.3).
        if proposal.axis not in PERSONA_AXES:
            return GateDecision(False, reason="unknown_axis")
        lo, hi = env.bound(proposal.axis)
        current = profile.delta(proposal.axis)
        resulting = current + proposal.proposed_delta
        if resulting < lo - 1e-9 or resulting > hi + 1e-9:
            return GateDecision(False, reason="out_of_axis_bounds")
        resulting = max(lo, min(hi, resulting))  # kẹp phòng thủ (R2.2): luôn nằm trong bound

        # 5. Ngưỡng khoảng cách bản sắc (R2.4, R2.5).
        prospective = self._prospective_distance(profile, proposal.axis, resulting)
        current_distance = profile.identity_distance(env.axis_bounds)
        if prospective > env.max_identity_distance + 1e-9:
            return GateDecision(False, reason="identity_distance_exceeded")
        # Khi đã chạm trần: chỉ cho giữ nguyên hoặc giảm độ lệch (R2.5).
        if current_distance >= env.max_identity_distance - 1e-9 and prospective > current_distance + 1e-9:
            return GateDecision(False, reason="identity_distance_at_cap")

        # 6. Giới hạn nhịp (R4.2, R5.5).
        if applied_window_count >= env.max_nudges_per_window:
            return GateDecision(False, reason="rate_limited")

        # 7. Chống nịnh/bám dính trên trục phẩm giá (R4.3).
        increases_closeness = (
            (proposal.axis == env.dignity_axis and proposal.proposed_delta > 0)
            or (proposal.axis == "preferred_distance" and proposal.proposed_delta < 0)
        )
        if increases_closeness and (sig.dependency >= self._DEPENDENCY_THRESHOLD or sig.flattering >= self._DEPENDENCY_THRESHOLD):
            return GateDecision(False, reason="anti_sycophancy")

        # 8. Objectification cao gần đây → ưu tiên giữ khoảng cách (R4.4).
        if increases_closeness and sig.objectification >= self._OBJECTIFICATION_THRESHOLD:
            return GateDecision(False, reason="objectification_prefers_distance")

        return GateDecision(True, clamped_delta=resulting, reason="accepted")

    def _prospective_distance(self, profile: "DevelopmentProfileStore", axis: str, resulting: float) -> float:
        """Khoảng cách bản sắc nếu áp ``resulting`` cho ``axis`` (chuẩn hoá theo bound)."""
        bounds = self.envelope.axis_bounds
        total = 0.0
        for ax in profile.axes:
            d = resulting if ax == axis else profile.delta(ax)
            if ax in bounds:
                lo, hi = bounds[ax]
                scale = max(abs(lo), abs(hi)) or 1.0
                d = d / scale
            total += d * d
        return math.sqrt(total)
