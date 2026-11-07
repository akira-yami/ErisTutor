# -*- coding: utf-8 -*-
"""development_system.py — Sổ_Phát_Triển + điều phối apply/undo/reset.

Tách khỏi ``development_profile`` (data + store + gate) để phần điều phối và
audit rõ ràng. Luồng áp dụng theo nguyên tắc **ghi-Sổ-trước-commit-sau** để giữ
bất biến fail-closed R5.6: không thay đổi nào tồn tại mà thiếu vết.
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from .development_profile import (
    SCHEMA_VERSION,
    PERSONA_AXES,
    DevelopmentProfileStore,
    GateSignals,
    GrowthAnchor,
    IdentityGate,
    LogEntry,
    NudgeProposal,
)


def _now() -> float:
    return time.time()


@dataclass(slots=True)
class ApplyResult:
    applied: bool
    reason: str = ""
    axis: str = ""
    before: float = 0.0
    after: float = 0.0
    message: str = ""


@dataclass(slots=True)
class DevelopmentBias:
    """Ảnh đọc read-only của các delta long-arc, để hợp vào mặt phẳng biểu đạt.

    Đây CHỈ là bias giọng/khoảng cách/độ-dám-đọc. Không trường nào chạm guard
    sự thật; xem ``development_bias`` adapters.
    """

    read_boldness: float = 0.0
    warmth_temperature: float = 0.0
    preferred_distance: float = 0.0
    attachment_visibility: float = 0.0


# ---------------------------------------------------------------------------
# Sổ_Phát_Triển
# ---------------------------------------------------------------------------

class DevelopmentLog:
    """Nhật ký có phiên bản, ghi mọi thay đổi/đề xuất cho audit + hoàn tác."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[LogEntry] = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.entries = []
            return
        items = raw.get("entries", []) if isinstance(raw, Mapping) else []
        self.entries = [LogEntry.from_dict(it) for it in items if isinstance(it, Mapping)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "entries": [e.to_dict() for e in self.entries],
        }

    def _save(self) -> None:
        payload = self.to_dict()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, entry: LogEntry) -> bool:
        """Ghi một mục. Trả True nếu ghi đĩa thành công, False nếu fail (caller
        sẽ rollback). Mục đã append in-memory sẽ được gỡ lại nếu ghi fail."""
        self.entries.append(entry)
        try:
            self._save()
            return True
        except Exception:
            # Ghi fail → gỡ mục vừa thêm để in-memory không lệch với đĩa (fail-closed).
            self.entries.pop()
            return False

    def count_applied_in_window(self, *, now: float, window_seconds: float) -> int:
        floor = now - max(0.0, window_seconds)
        return sum(1 for e in self.entries if e.action == "applied" and e.timestamp >= floor)

    def last_applied(self) -> LogEntry | None:
        for entry in reversed(self.entries):
            if entry.action == "applied":
                return entry
        return None


# ---------------------------------------------------------------------------
# Hệ_Phát_Triển — điều phối
# ---------------------------------------------------------------------------

class DevelopmentSystem:
    """Điều phối đề xuất → Cổng_Bản_Sắc → ghi Sổ → áp dụng. Sở hữu undo/reset."""

    def __init__(
        self,
        *,
        profile: DevelopmentProfileStore,
        log: DevelopmentLog,
        gate: IdentityGate | None = None,
    ) -> None:
        self.profile = profile
        self.log = log
        self.gate = resolve_dependency(gate, lambda: IdentityGate())

    def apply(
        self,
        proposal: NudgeProposal,
        *,
        now: float | None = None,
        signals: GateSignals | None = None,
        evidence_ok: bool = True,
    ) -> ApplyResult:
        ts = _now() if now is None else now
        window = self.gate.envelope.window_seconds
        count = self.log.count_applied_in_window(now=ts, window_seconds=window)

        decision = self.gate.evaluate(
            proposal,
            self.profile,
            now=ts,
            applied_window_count=count,
            signals=signals,
            evidence_ok=evidence_ok,
        )
        if not decision.accepted:
            self.log.record(
                LogEntry(
                    version=self.profile.version,
                    timestamp=ts,
                    axis=proposal.axis,
                    before=self.profile.delta(proposal.axis),
                    after=self.profile.delta(proposal.axis),
                    evidence_ids=_evidence_of(proposal),
                    action="rejected",
                    reason=decision.reason,
                )
            )
            return ApplyResult(False, reason=decision.reason, axis=proposal.axis)

        # GHI SỔ TRƯỚC, COMMIT SAU (R5.6 fail-closed).
        snapshot = self.profile.snapshot()
        before = self.profile.delta(proposal.axis)
        entry = LogEntry(
            version=self.profile.version + 1,
            timestamp=ts,
            axis=proposal.axis,
            before=before,
            after=decision.clamped_delta,
            evidence_ids=_evidence_of(proposal),
            action="applied",
        )
        if not self.log.record(entry):
            self.profile.restore(snapshot)  # log hỏng → bỏ thay đổi
            return ApplyResult(False, reason="log_write_failed", axis=proposal.axis)

        self.profile.commit(proposal.axis, decision.clamped_delta, evidence_ids=_evidence_of(proposal), now=ts)
        self.profile.save()
        return ApplyResult(True, reason="applied", axis=proposal.axis, before=before, after=decision.clamped_delta)

    def undo_last(self, *, now: float | None = None) -> ApplyResult:
        """R5.2: hoàn tác thay đổi đã-áp gần nhất, đưa trục về giá trị trước."""
        ts = _now() if now is None else now
        last = self.log.last_applied()
        if last is None:
            return ApplyResult(False, reason="nothing_to_undo")
        from .development_profile import AxisDelta

        current = self.profile.axes.get(last.axis) or AxisDelta(axis=last.axis)
        self.profile.axes[last.axis] = replace(current, delta=last.before, updated_at=ts)
        self.profile.version = max(0, self.profile.version - 1)
        self.log.record(
            LogEntry(
                version=self.profile.version,
                timestamp=ts,
                axis=last.axis,
                before=last.after,
                after=last.before,
                evidence_ids=last.evidence_ids,
                action="undo",
            )
        )
        self.profile.save()
        return ApplyResult(True, reason="undone", axis=last.axis, before=last.after, after=last.before)

    def reset(self, *, now: float | None = None) -> ApplyResult:
        """R5.3: về Baseline_Mặc_Định, giữ Sổ làm lịch sử."""
        ts = _now() if now is None else now
        self.profile.reset_to_baseline()
        self.log.record(
            LogEntry(version=0, timestamp=ts, axis="*", before=0.0, after=0.0, action="reset")
        )
        self.profile.save()
        return ApplyResult(True, reason="reset")

    # --- điều phối nền (R6.1) ------------------------------------------
    def tick(
        self,
        *,
        consolidator,
        observations,
        allowed_evidence_ids,
        now: float | None = None,
        signals: GateSignals | None = None,
        idle_pressure: float = 0.0,
        consolidation_pressure: float = 0.0,
        budget_allows: bool = True,
        user_interacting_high_risk: bool = False,
    ) -> list[ApplyResult]:
        """Một lượt nền: nếu được phép chạy → lấy đề xuất từ Tác_Tử_Nền → apply từng cái."""
        if not consolidator.should_run(
            idle_pressure=idle_pressure,
            consolidation_pressure=consolidation_pressure,
            budget_allows=budget_allows,
            user_interacting_high_risk=user_interacting_high_risk,
        ):
            return []
        ts = _now() if now is None else now
        proposals = consolidator.consolidate(observations, allowed_evidence_ids=allowed_evidence_ids, profile=self.profile)
        return [self.apply(p, now=ts, signals=signals) for p in proposals]

    # --- mặt phẳng đọc (R7.3) ------------------------------------------
    def development_bias(self) -> DevelopmentBias:
        """Snapshot read-only cho mặt phẳng biểu đạt. Không sửa state."""
        return DevelopmentBias(
            read_boldness=self.profile.delta("read_boldness"),
            warmth_temperature=self.profile.delta("warmth_temperature"),
            preferred_distance=self.profile.delta("preferred_distance"),
            attachment_visibility=self.profile.delta("attachment_visibility"),
        )

    # --- giao diện người dùng (R9) -------------------------------------
    def describe_for_user(self) -> str:
        """R9.1: tóm tắt tiếng Việt, KHÔNG lộ tên tham số/giá trị số thô."""
        parts: list[str] = []
        for axis in PERSONA_AXES:
            phrase = _describe_axis(axis, self.profile.delta(axis))
            if phrase:
                parts.append(phrase)
        if not parts:
            return "Tôi vẫn gần như lúc mới gặp bạn — chưa nghiêng đi đâu đáng kể."
        return "So với lúc đầu, " + "; ".join(parts) + "."

    def steer(self, user_direction: str, *, now: float | None = None) -> ApplyResult:
        """R9.2/R9.4: coi chỉ-hướng của bạn là Neo trọng số cao; áp phần trong
        phong bì, báo phần không áp được bằng ngôn ngữ tự nhiên."""
        ts = _now() if now is None else now
        axis, direction = _parse_steer(user_direction)
        if axis is None:
            return ApplyResult(False, reason="unclear_direction", message="Tôi chưa rõ bạn muốn tôi nghiêng theo hướng nào.")

        lo, hi = self.gate.envelope.bound(axis)
        current = self.profile.delta(axis)
        target = current + direction
        clamped_target = max(lo, min(hi, target))
        clamped_delta = clamped_target - current
        truncated = abs(clamped_target - target) > 1e-9

        anchor = GrowthAnchor(
            kind="user_steer",
            evidence_ids=(f"user_steer:{int(ts)}",),
            weight=1.0,
            observed_count=1,
        )
        proposal = NudgeProposal(axis=axis, proposed_delta=clamped_delta, anchors=(anchor,), rationale="user_steer")
        result = self.apply(proposal, now=ts, evidence_ok=True)
        if result.applied and truncated:
            result.message = "Tôi đã nghiêng theo ý bạn hết mức tôi cho phép mình, phần còn lại nằm ngoài giới hạn tôi giữ để vẫn là tôi."
        elif result.applied:
            result.message = "Tôi đã nghiêng theo ý bạn."
        return result


def _evidence_of(proposal: NudgeProposal) -> tuple[str, ...]:
    ids: list[str] = []
    for anchor in proposal.anchors:
        ids.extend(anchor.evidence_ids)
    return tuple(dict.fromkeys(ids))


# ---------------------------------------------------------------------------
# Helpers cho giao diện người dùng (không lộ tên trục/số thô)
# ---------------------------------------------------------------------------

import re
import unicodedata


def _norm(text: str) -> str:
    lowered = str(text or "").strip().lower().replace("đ", "d")
    lowered = unicodedata.normalize("NFD", lowered)
    lowered = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", lowered).strip()


def _degree(delta: float) -> str:
    return " một chút" if abs(delta) < 0.15 else " rõ"


def _describe_axis(axis: str, delta: float) -> str:
    if abs(delta) < 0.03:
        return ""
    d = _degree(delta)
    pos = delta > 0
    if axis == "warmth_temperature":
        return ("giọng tôi đã ấm hơn" if pos else "giọng tôi đã nguội hơn") + d
    if axis == "preferred_distance":
        # âm = gần hơn
        return ("tôi giữ khoảng cách hơn" if pos else "tôi đứng gần bạn hơn") + d
    if axis == "read_boldness":
        return ("tôi dám đoán ý bạn hơn" if pos else "tôi dè dặt hơn khi đọc bạn") + d
    if axis == "attachment_visibility":
        return ("tôi để lộ sự gắn bó nhiều hơn" if pos else "tôi giữ sự gắn bó kín hơn") + d
    return ""


_STEER_RULES: tuple[tuple[tuple[str, ...], str, float], ...] = (
    (("am hon", "am ap hon"), "warmth_temperature", 0.10),
    (("lanh hon", "nguoi lanh", "kho khan hon"), "warmth_temperature", -0.10),
    (("gan hon", "lai gan", "than mat hon"), "preferred_distance", -0.10),
    (("xa hon", "giu khoang cach", "cach xa"), "preferred_distance", 0.10),
    (("bao hon", "dam hon", "dam doan", "manh dan hon"), "read_boldness", 0.10),
    (("de dat hon", "dung doan", "bot doan"), "read_boldness", -0.10),
    (("lo ra hon", "gan bo hon", "the hien tinh cam"), "attachment_visibility", 0.10),
    (("kin hon", "giu y hon", "giau bot"), "attachment_visibility", -0.10),
)


def _parse_steer(text: str) -> tuple[str | None, float]:
    norm = _norm(text)
    for markers, axis, step in _STEER_RULES:
        if any(m in norm for m in markers):
            return axis, step
    return None, 0.0


# ---------------------------------------------------------------------------
# Singleton + feature flag (cho wiring runtime — task 7)
# ---------------------------------------------------------------------------

def development_growth_enabled() -> bool:
    """Cờ bật/tắt long-arc. MẶC ĐỊNH TẮT: runtime không đổi hành vi tới khi bạn
    đặt ERIS_DEVELOPMENT_GROWTH_ENABLED=1. Tránh destabilize companion đang chạy."""
    try:
        from .depth_flags import flag_enabled  # type: ignore

        return flag_enabled("ERIS_DEVELOPMENT_GROWTH_ENABLED", default=False)
    except Exception:
        return os.getenv("ERIS_DEVELOPMENT_GROWTH_ENABLED", "").strip() in {"1", "true", "yes", "on"}


_DEFAULT_SYSTEM: "DevelopmentSystem | None" = None


def get_default_development_system(*, memory_dir: str | os.PathLike[str] | None = None) -> "DevelopmentSystem":
    """Trả singleton DevelopmentSystem đặt cùng thư mục memory."""
    global _DEFAULT_SYSTEM
    try:
        from .epistemic import epistemic_development_system, get_default_kernel
        kernel = get_default_kernel()
        if kernel is not None and getattr(kernel, "authoritative", False):
            if _DEFAULT_SYSTEM is None or getattr(getattr(_DEFAULT_SYSTEM, "profile", None), "kernel", None) is not kernel:
                _DEFAULT_SYSTEM = epistemic_development_system(kernel)
            return _DEFAULT_SYSTEM
    except Exception:
        pass
    from .development_profile import default_development_profile_path, development_profile_path_for_memory_core

    if memory_dir is not None:
        profile_path = development_profile_path_for_memory_core(memory_dir)
    else:
        profile_path = default_development_profile_path()
    log_path = Path(profile_path).with_name("development_log.json")

    if _DEFAULT_SYSTEM is None or _DEFAULT_SYSTEM.profile.path != Path(profile_path):
        _DEFAULT_SYSTEM = DevelopmentSystem(
            profile=DevelopmentProfileStore(profile_path),
            log=DevelopmentLog(log_path),
            gate=IdentityGate(),
        )
    return _DEFAULT_SYSTEM
