# -*- coding: utf-8 -*-
"""ERIS V103 FINAL - Continuous Activation Nervous System cutover + consolidation.

File này là lớp *consolidation* cuối của roadmap V98->V103 trong tài liệu
"Continuous Activation Nervous System". Nó không tạo thêm một candidate picker.
Ngược lại, nó gom ba trách nhiệm còn rời rạc trong migration thành một authority
liên tục có thể audit:

    SharedActivationBus (V98)
            +
    semantic targets đã có provenance (V99-V102 compatibility)
            |
            v
    UnifiedExpressionMotorV103
      silence / body / vocal / micro / proposition
            |
            v
    NervousAutonomousAuthorityV103
      - active path: motor chọn action family trước
      - Subject/Neuro coalition chỉ còn compatibility envelope
      - không vote một list autonomous speech candidate bình thường

Nguyên tắc bắt buộc:
- semantic content phải có provenance/evidence; motor không bịa topic;
- critic/grounding chỉ inject uncertainty + inhibition, hard safety vẫn veto tuyệt đối;
- STP/habituation làm concern/topic vừa surface bị depression tự nhiên;
- fresh evidence tạo facilitation/re-open, không cần retry scheduler 4 giây;
- randomness không sở hữu intent. File này deterministic hoàn toàn;
- mode migration: off / shadow / active;
- structured task/continuation/capability lane vẫn được phép dùng Subject authority;
  cutover ở đây nhắm *normal autonomous expression path*.

Chú thích VI được giữ nhiều hơn bình thường vì đây là file kiến trúc/migration,
không phải utility nhỏ.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import os
import statistics
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

from runtime.activation_nervous_system_v98 import (
    ACTIVATION_FIELDS,
    ActivationSnapshot,
    ModulationEvent,
    SharedActivationBus,
)


NERVOUS_FINAL_SCHEMA = "eris.nervous_system.v103_final"
NERVOUS_HEALTH_SCHEMA = "eris.nervous_health.v103"
NERVOUS_MOTOR_SCHEMA = "eris.expression_motor.v103"
NERVOUS_STP_SCHEMA = "eris.field_stp.v103"
NERVOUS_MIGRATION_SCHEMA = "eris.nervous_migration.v103_final2"
NERVOUS_TRACE_SCHEMA = "eris.public_turn.trace.v98_nervous"

_VALID_MODES = {"off", "shadow", "active"}
_STRUCTURED_OWNERS = {
    "neuro_continuation",
    "task_continuation",
    "capability_planner",
    "capability",
    "conversation_handoff",
    "repair",
}
_GENERIC_SENSOR_OWNERS = {"subject_initiative"}


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 320) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in _VALID_MODES else "active"


def _percentile(values: Sequence[float], q: float) -> float:
    rows = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not rows:
        return 0.0
    if len(rows) == 1:
        return rows[0]
    pos = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return rows[lo]
    alpha = pos - lo
    return rows[lo] * (1.0 - alpha) + rows[hi] * alpha


@dataclass(frozen=True, slots=True)
class NervousSystemConfigV103:
    """Một config duy nhất cho final nervous path.

    Trước V103 migration, threshold nằm rải trong V98/V101/V102/V103. Ở final
    cutover, các giá trị authority-level được gom vào đây để replay/calibration
    có một nguồn. Các lớp compatibility cũ vẫn giữ hằng số riêng để không phá
    backward tests, nhưng production active path đọc config này.
    """

    mode: str = "active"
    body_threshold: float = 0.43
    vocal_threshold: float = 0.52
    micro_threshold: float = 0.55
    proposition_threshold: float = 0.60
    release_hysteresis: float = 0.10
    body_dwell_seconds: float = 0.18
    vocal_dwell_seconds: float = 0.28
    micro_dwell_seconds: float = 0.34
    proposition_dwell_seconds: float = 0.46
    target_takeover_margin: float = 0.09
    target_takeover_dwell_seconds: float = 0.35
    speech_homeostasis_target: float = 0.05
    speech_homeostasis_tau_seconds: float = 180.0
    semantic_uncertainty_soft_block: float = 0.72
    hard_inhibition_block: float = 0.78
    stp_depression_per_commit: float = 0.48
    stp_recovery_tau_seconds: float = 110.0
    stp_facilitation_tau_seconds: float = 20.0

    @classmethod
    def from_env(cls) -> "NervousSystemConfigV103":
        # Chỉ mode là intended operator switch. Các số còn lại giữ centralized
        # code config để tránh môi trường production trở thành một bảng knob bí ẩn.
        return cls(mode=_mode(os.getenv("ERIS_NERVOUS_MODE", "active")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "thresholds": {
                "body": self.body_threshold,
                "vocalization": self.vocal_threshold,
                "micro_expression": self.micro_threshold,
                "proposition": self.proposition_threshold,
            },
            "dwell_seconds": {
                "body": self.body_dwell_seconds,
                "vocalization": self.vocal_dwell_seconds,
                "micro_expression": self.micro_dwell_seconds,
                "proposition": self.proposition_dwell_seconds,
            },
            "target_takeover_margin": self.target_takeover_margin,
            "target_takeover_dwell_seconds": self.target_takeover_dwell_seconds,
            "speech_homeostasis_target": self.speech_homeostasis_target,
            "hard_inhibition_block": self.hard_inhibition_block,
        }


@dataclass(frozen=True, slots=True)
class MotorSemanticTarget:
    source_id: str
    semantic_target: str
    motive: str
    pressure: float
    freshness: float
    persistence: float
    uncertainty: float
    evidence_refs: tuple[str, ...] = ()
    source_owner: str = "expression_urge"
    affordance_id: str = ""

    @property
    def evidence_quality(self) -> float:
        ref_bonus = min(0.20, 0.04 * len(self.evidence_refs))
        return _clamp01(0.42 * (1.0 - self.uncertainty) + 0.34 * self.freshness + 0.24 * self.persistence + ref_bonus)


@dataclass(frozen=True, slots=True)
class MotorDecisionV103:
    channel: str
    activation: float
    threshold: float
    committed: bool
    source_revision: int
    emitted_at: float
    semantic_source_id: str = ""
    semantic_target: str = ""
    local_text: str = ""
    reason: str = ""
    drives: Mapping[str, float] = field(default_factory=dict)
    hard_boundary: bool = False
    mode: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": NERVOUS_MOTOR_SCHEMA,
            "channel": self.channel,
            "activation": round(_clamp01(self.activation), 4),
            "threshold": round(_clamp01(self.threshold), 4),
            "committed": bool(self.committed),
            "source_revision": int(self.source_revision),
            "emitted_at": float(self.emitted_at),
            "semantic_source_id": self.semantic_source_id,
            "semantic_target": self.semantic_target,
            "local_text": self.local_text,
            "reason": self.reason,
            "drives": {key: round(_clamp01(value), 4) for key, value in dict(self.drives).items()},
            "hard_boundary": bool(self.hard_boundary),
            "mode": self.mode,
        }


@dataclass(slots=True)
class _STPTrace:
    source_id: str
    depression: float = 0.0
    facilitation: float = 0.0
    last_update_at: float = 0.0
    last_commit_at: float = 0.0
    last_evidence_signature: tuple[str, ...] = ()


class ShortTermPlasticityV103:
    """Tsodyks-Markram-inspired *engineering analogue* cho semantic source.

    Không cố mô phỏng synapse sinh học. Ta chỉ cần hai state:
    - depression: vừa surface/repeat nhiều -> gain giảm tạm thời;
    - facilitation: evidence mới thật -> source được mở lại nhanh hơn.
    """

    def __init__(self, config: NervousSystemConfigV103, *, clock=time.time) -> None:
        self.config = config
        self.clock = clock
        self._lock = threading.RLock()
        self._traces: dict[str, _STPTrace] = {}

    def gain(self, target: MotorSemanticTarget, *, now: float) -> float:
        source_id = str(target.source_id or "").strip()
        if not source_id:
            return 1.0
        refs = tuple(sorted(str(x) for x in target.evidence_refs if str(x)))
        with self._lock:
            trace = self._traces.get(source_id)
            if trace is None:
                trace = _STPTrace(source_id=source_id, last_update_at=now, last_evidence_signature=refs)
                self._traces[source_id] = trace
            self._recover(trace, now)
            # Evidence signature đổi nghĩa là có input mới từ bên ngoài/upstream,
            # cho facilitation nhỏ. Không dùng public wording làm evidence.
            if refs and refs != trace.last_evidence_signature:
                trace.facilitation = _clamp01(trace.facilitation + 0.24 + 0.20 * target.freshness)
                trace.last_evidence_signature = refs
            trace.last_update_at = now
            return _clamp01((1.0 - 0.82 * trace.depression) * (1.0 + 0.42 * trace.facilitation), 1.0)

    def note_commit(self, source_id: str, *, now: float | None = None) -> None:
        key = str(source_id or "").strip()
        if not key:
            return
        current = float(now if now is not None else self.clock())
        with self._lock:
            trace = self._traces.get(key)
            if trace is None:
                trace = _STPTrace(source_id=key, last_update_at=current)
                self._traces[key] = trace
            self._recover(trace, current)
            trace.depression = _clamp01(trace.depression + self.config.stp_depression_per_commit)
            trace.facilitation = _clamp01(trace.facilitation * 0.45)
            trace.last_commit_at = current
            trace.last_update_at = current

    def note_fresh_evidence(self, source_id: str, *, strength: float = 0.5, now: float | None = None) -> None:
        key = str(source_id or "").strip()
        if not key:
            return
        current = float(now if now is not None else self.clock())
        with self._lock:
            trace = self._traces.get(key)
            if trace is None:
                trace = _STPTrace(source_id=key, last_update_at=current)
                self._traces[key] = trace
            self._recover(trace, current)
            trace.facilitation = _clamp01(trace.facilitation + 0.20 + 0.45 * _clamp01(strength))
            trace.depression = _clamp01(trace.depression * (1.0 - 0.30 * _clamp01(strength)))
            trace.last_update_at = current

    def snapshot(self, *, now: float | None = None, limit: int = 16) -> tuple[dict[str, Any], ...]:
        current = float(now if now is not None else self.clock())
        with self._lock:
            for trace in self._traces.values():
                self._recover(trace, current)
            rows = sorted(self._traces.values(), key=lambda x: (x.depression, x.facilitation), reverse=True)
            return tuple({
                "schema": NERVOUS_STP_SCHEMA,
                "source_id": row.source_id,
                "depression": round(row.depression, 4),
                "facilitation": round(row.facilitation, 4),
                "last_commit_at": row.last_commit_at,
                "last_update_at": row.last_update_at,
            } for row in rows[: max(1, int(limit))])

    def _recover(self, trace: _STPTrace, now: float) -> None:
        # Không dùng ``last_update_at or now``: fake-clock/replay hợp lệ có thể
        # bắt đầu ở timestamp 0.0. Truthiness ở đây sẽ làm STP không recover.
        dt = max(0.0, now - float(trace.last_update_at))
        if dt <= 0.0:
            return
        dep_tau = max(1.0, self.config.stp_recovery_tau_seconds)
        fac_tau = max(1.0, self.config.stp_facilitation_tau_seconds)
        trace.depression *= math.exp(-dt / dep_tau)
        trace.facilitation *= math.exp(-dt / fac_tau)


class NervousHealthMonitorV103:
    """Rẻ, local, bounded: histogram field + latency p50/p95/p99.

    Đây là health telemetry, không phải decision authority. Chỉ giữ sliding
    windows nhỏ để tránh observability tự trở thành nguồn memory leak.
    """

    def __init__(self, *, max_samples: int = 2048) -> None:
        self._lock = threading.RLock()
        self._tick_ms: deque[float] = deque(maxlen=max(128, int(max_samples)))
        self._field_bins: dict[str, list[int]] = {name: [0] * 10 for name in ACTIVATION_FIELDS}
        self._samples = 0
        self._motor_commits: dict[str, int] = {name: 0 for name in ("silence", "body", "vocalization", "micro_expression", "proposition")}
        self._candidate_bypass_count = 0

    def observe_tick(self, snapshot: ActivationSnapshot, elapsed_ms: float) -> None:
        with self._lock:
            self._tick_ms.append(max(0.0, float(elapsed_ms)))
            self._samples += 1
            for name in ACTIVATION_FIELDS:
                value = snapshot.value(name)
                index = min(9, max(0, int(value * 10.0)))
                self._field_bins[name][index] += 1

    def observe_motor(self, decision: MotorDecisionV103) -> None:
        if not decision.committed:
            return
        with self._lock:
            if decision.channel in self._motor_commits:
                self._motor_commits[decision.channel] += 1

    def note_candidate_bypass(self) -> None:
        with self._lock:
            self._candidate_bypass_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rows = tuple(self._tick_ms)
            return {
                "schema": NERVOUS_HEALTH_SCHEMA,
                "samples": self._samples,
                "tick_ms": {
                    "p50": round(_percentile(rows, 0.50), 4),
                    "p95": round(_percentile(rows, 0.95), 4),
                    "p99": round(_percentile(rows, 0.99), 4),
                    "mean": round(statistics.fmean(rows), 4) if rows else 0.0,
                },
                "field_histograms": {name: list(bins) for name, bins in self._field_bins.items()},
                "motor_commits": dict(self._motor_commits),
                "candidate_bypass_count": self._candidate_bypass_count,
            }


class UnifiedExpressionMotorV103:
    """Unified motor continuum: silence/body/vocal/micro/proposition.

    Motor chọn *action family* từ continuous state, không chọn semantic wording.
    Proposition chỉ có thể commit khi có MotorSemanticTarget có evidence/provenance.
    """

    _CHANNEL_ORDER = ("body", "vocalization", "micro_expression", "proposition")

    def __init__(
        self,
        config: NervousSystemConfigV103,
        *,
        stp: ShortTermPlasticityV103,
        health: NervousHealthMonitorV103,
        clock=time.time,
        logger: Any | None = None,
    ) -> None:
        self.config = config
        self.stp = stp
        self.health = health
        self.clock = clock
        self.logger = logger
        self._lock = threading.RLock()
        self._above_since: dict[str, float | None] = {name: None for name in self._CHANNEL_ORDER}
        self._armed = {name: True for name in self._CHANNEL_ORDER}
        self._speech_activity = 0.0
        self._speech_activity_at = float(clock())
        self._target_id = ""
        self._target_drive = 0.0
        self._challenger_id = ""
        self._challenger_since = 0.0
        self._last_decision: MotorDecisionV103 | None = None

    def evaluate(
        self,
        snapshot: ActivationSnapshot,
        *,
        targets: Sequence[MotorSemanticTarget] = (),
        hard_boundary: bool = False,
        runtime_context: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> MotorDecisionV103:
        current = float(now if now is not None else self.clock())
        context = dict(runtime_context or {})
        hard_boundary = bool(
            hard_boundary
            or context.get("explicit_quiet", False)
            or context.get("awaiting_user_answer", False)
            or context.get("public_turn_active", False)
            or context.get("user_holds_turn", False)
        )
        self._decay_speech_activity(current)
        v = {name: snapshot.value(name) for name in ACTIVATION_FIELDS}
        target, target_drive = self._bind_target(tuple(targets or ()), now=current)

        homeostatic_bias = max(-0.08, min(0.16, (self._speech_activity - self.config.speech_homeostasis_target) * 0.85))
        drives = {
            "body": _clamp01(
                0.25 * v["curiosity"] + 0.20 * v["boredom"] + 0.18 * v["social_need"]
                + 0.17 * v["concern_salience"] + 0.12 * v["expressive_pressure"]
                - 0.18 * v["inhibition"] - 0.08 * v["fatigue"]
            ),
            "vocalization": _clamp01(
                0.48 * v["expressive_pressure"] + 0.16 * v["boredom"] + 0.14 * v["social_need"]
                + 0.08 * v["concern_salience"] - 0.28 * v["inhibition"]
                - 0.14 * v["task_focus"] - 0.10 * v["fatigue"] - homeostatic_bias
            ),
            "micro_expression": _clamp01(
                0.34 * v["concern_salience"] + 0.22 * v["curiosity"] + 0.18 * v["social_need"]
                + 0.14 * v["expressive_pressure"] + 0.06 * v["boredom"]
                - 0.24 * v["inhibition"] - 0.08 * v["fatigue"] - 0.65 * homeostatic_bias
            ),
            "proposition": _clamp01(
                (0.42 * v["expressive_pressure"] + 0.20 * v["concern_salience"] + 0.12 * v["curiosity"]
                 + 0.10 * v["social_need"] + 0.08 * v["task_focus"] + 0.08 * target_drive)
                * (0.48 + 0.52 * target_drive)
                - 0.34 * v["inhibition"] - 0.18 * v["uncertainty"] - 0.10 * v["fatigue"]
                - homeostatic_bias
            ) if target is not None else 0.0,
        }

        thresholds = {
            "body": self.config.body_threshold,
            "vocalization": self.config.vocal_threshold,
            "micro_expression": self.config.micro_threshold,
            "proposition": self.config.proposition_threshold,
        }
        dwell = {
            "body": self.config.body_dwell_seconds,
            "vocalization": self.config.vocal_dwell_seconds,
            "micro_expression": self.config.micro_dwell_seconds,
            "proposition": self.config.proposition_dwell_seconds,
        }

        # Hard boundary không xóa state/target. Nó chỉ khóa public motor output.
        if hard_boundary or v["inhibition"] >= self.config.hard_inhibition_block:
            decision = MotorDecisionV103(
                channel="silence",
                activation=max(v["inhibition"], 0.75 if hard_boundary else 0.0),
                threshold=1.0,
                committed=True,
                source_revision=snapshot.revision,
                emitted_at=current,
                reason="hard_boundary" if hard_boundary else "high_inhibition",
                drives=drives,
                hard_boundary=hard_boundary,
                mode=self.config.mode,
            )
            self._last_decision = decision
            self.health.observe_motor(decision)
            return decision

        ready: list[tuple[str, float]] = []
        with self._lock:
            for channel in self._CHANNEL_ORDER:
                drive = drives[channel]
                threshold = thresholds[channel]
                if drive <= threshold - self.config.release_hysteresis:
                    self._armed[channel] = True
                    self._above_since[channel] = None
                if drive >= threshold:
                    if self._above_since[channel] is None:
                        self._above_since[channel] = current
                    since = self._above_since[channel]
                    if self._armed[channel] and since is not None and current - since >= dwell[channel]:
                        ready.append((channel, drive))
                else:
                    self._above_since[channel] = None

            # Soft WTA chỉ ở motor channel, đúng như roadmap. Điểm tinh tế: channel
            # yếu hơn KHÔNG được fire trước chỉ vì dwell ngắn hơn. Nếu proposition
            # đang là drive trội nhưng cần thêm 100 ms để ổn định, micro/body phải
            # chờ. Nếu không, ở tick 20 Hz micro sẽ luôn "cướp" proposition rồi
            # refractory làm lời nói có nội dung không bao giờ xuất hiện.
            contenders = [
                (channel, drives[channel])
                for channel in self._CHANNEL_ORDER
                if self._armed[channel] and drives[channel] >= thresholds[channel]
            ]
            leader = max(
                contenders,
                key=lambda item: (item[1] - thresholds[item[0]], -self._CHANNEL_ORDER.index(item[0])),
                default=None,
            )
            ready_names = {name for name, _ in ready}
            if leader is None or leader[0] not in ready_names:
                strongest = leader or (max(drives.items(), key=lambda item: item[1]) if drives else ("silence", 0.0))
                decision = MotorDecisionV103(
                    channel="silence",
                    activation=_clamp01(strongest[1]),
                    threshold=thresholds.get(strongest[0], 1.0),
                    committed=False,
                    source_revision=snapshot.revision,
                    emitted_at=current,
                    semantic_source_id=target.source_id if target is not None else "",
                    semantic_target=target.semantic_target if target is not None else "",
                    reason=f"settling:{strongest[0]}",
                    drives=drives,
                    mode=self.config.mode,
                )
                self._last_decision = decision
                return decision

            channel, activation = leader
            self._armed[channel] = False

        local_text = ""
        semantic_source_id = ""
        semantic_target = ""
        if channel == "proposition" and target is not None:
            semantic_source_id = target.source_id
            semantic_target = target.semantic_target
        elif channel in {"vocalization", "micro_expression"}:
            # Lexeme cực nhỏ, deterministic, không mang factual proposition.
            # Surface/TTS phải giữ same-text nếu runtime chọn dùng local_text này.
            local_text = "haiz" if v["boredom"] >= max(v["curiosity"], v["concern_salience"]) else "hừm"

        decision = MotorDecisionV103(
            channel=channel,
            activation=activation,
            threshold=thresholds[channel],
            committed=True,
            source_revision=snapshot.revision,
            emitted_at=current,
            semantic_source_id=semantic_source_id,
            semantic_target=semantic_target,
            local_text=local_text,
            reason="stable_dwell_crossing",
            drives=drives,
            mode=self.config.mode,
        )
        self._last_decision = decision
        self.health.observe_motor(decision)
        return decision

    def note_public_commit(self, source_id: str = "", *, now: float | None = None) -> None:
        current = float(now if now is not None else self.clock())
        self._decay_speech_activity(current)
        with self._lock:
            # Moving average proxy: mỗi public speech là một impulse, sau đó decay.
            self._speech_activity = _clamp01(self._speech_activity + 0.22)
            self._speech_activity_at = current
            for channel in ("vocalization", "micro_expression", "proposition"):
                self._armed[channel] = False
                self._above_since[channel] = None
        if source_id:
            self.stp.note_commit(source_id, now=current)

    def last_decision(self) -> MotorDecisionV103 | None:
        return self._last_decision

    def _bind_target(self, targets: Sequence[MotorSemanticTarget], *, now: float) -> tuple[MotorSemanticTarget | None, float]:
        if not targets:
            self._target_id = ""
            self._target_drive = 0.0
            self._challenger_id = ""
            self._challenger_since = 0.0
            return None, 0.0

        scored: list[tuple[MotorSemanticTarget, float]] = []
        for target in targets:
            stp_gain = self.stp.gain(target, now=now)
            drive = _clamp01(
                (0.34 * target.pressure + 0.25 * target.persistence + 0.20 * target.freshness + 0.21 * target.evidence_quality)
                * stp_gain
                * (1.0 - 0.42 * target.uncertainty)
            )
            scored.append((target, drive))
        scored.sort(key=lambda row: (row[1], row[0].persistence, row[0].freshness, row[0].source_id), reverse=True)
        challenger, challenger_drive = scored[0]
        incumbent = next((row for row in scored if row[0].source_id == self._target_id), None)

        if incumbent is None:
            self._target_id = challenger.source_id
            self._target_drive = challenger_drive
            self._challenger_id = ""
            self._challenger_since = 0.0
            return challenger, challenger_drive

        current_target, current_drive = incumbent
        if challenger.source_id == current_target.source_id:
            self._target_drive = current_drive
            self._challenger_id = ""
            self._challenger_since = 0.0
            return current_target, current_drive

        if challenger_drive >= current_drive + self.config.target_takeover_margin:
            if self._challenger_id != challenger.source_id:
                self._challenger_id = challenger.source_id
                self._challenger_since = now
            elif now - self._challenger_since >= self.config.target_takeover_dwell_seconds:
                self._target_id = challenger.source_id
                self._target_drive = challenger_drive
                self._challenger_id = ""
                self._challenger_since = 0.0
                return challenger, challenger_drive
        else:
            self._challenger_id = ""
            self._challenger_since = 0.0

        self._target_drive = current_drive
        return current_target, current_drive

    def _decay_speech_activity(self, now: float) -> None:
        with self._lock:
            dt = max(0.0, now - self._speech_activity_at)
            if dt > 0.0:
                self._speech_activity *= math.exp(-dt / max(1.0, self.config.speech_homeostasis_tau_seconds))
                self._speech_activity_at = now


class NervousAutonomousAuthorityV103:
    """Final authority cho normal autonomous expression path.

    Compatibility rule quan trọng: Subject Core vẫn materialize frame/decision để
    các downstream contract V93-V97 không vỡ. Nhưng khi scope là normal autonomous
    expression và mode=active, Neuro không được chấm list candidate để quyết định
    action. Motor đã quyết action family; frame chỉ được dùng làm envelope typed.
    """

    def __init__(
        self,
        activation_bus: SharedActivationBus,
        *,
        host: Any | None = None,
        config: NervousSystemConfigV103 | None = None,
        clock=time.time,
        logger: Any | None = None,
    ) -> None:
        self.activation_bus = activation_bus
        self.host = host
        self.config = config or NervousSystemConfigV103.from_env()
        self.clock = clock
        self.logger = logger
        self.health = NervousHealthMonitorV103()
        self.stp = ShortTermPlasticityV103(self.config, clock=clock)
        self.motor = UnifiedExpressionMotorV103(
            self.config, stp=self.stp, health=self.health, clock=clock, logger=logger
        )
        self._lock = threading.RLock()
        self._pending_decision: MotorDecisionV103 | None = None
        self._pending_scope = ""
        self._history: deque[dict[str, Any]] = deque(maxlen=256)
        self._modulation_sequence = 0

    @property
    def mode(self) -> str:
        return self.config.mode

    def active(self) -> bool:
        return self.config.mode == "active"

    def preprocess_affordances(
        self,
        affordances: Sequence[Any],
        *,
        runtime_context: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> tuple[tuple[Any, ...], MotorDecisionV103]:
        from runtime.autonomy.authority_trace import emit as autonomy_trace
        autonomy_trace("v103.preprocess", mode=self.config.mode,
                       coalition_count=len(affordances or ()))
        current = float(now if now is not None else self.clock())
        context = dict(runtime_context or {})
        rows = tuple(affordances or ())

        # ``off`` phải là legacy thật sự: không lọc affordance và cũng không bơm
        # field mới. Đây là rollback switch vận hành, không phải "shadow nhưng
        # không log". Shadow/active mới tham gia nervous dynamics.
        if self.config.mode == "off":
            snapshot = self.activation_bus.snapshot()
            decision = MotorDecisionV103(
                channel="silence", activation=0.0, threshold=1.0, committed=False,
                source_revision=snapshot.revision, emitted_at=current,
                reason="nervous_mode_off", mode="off",
            )
            with self._lock:
                self._pending_decision = decision
                self._pending_scope = "off"
                self._history.append({"at": current, "event": "motor", "scope": "off", "decision": decision.to_dict()})
            return rows, decision

        # V100 roadmap compliance: module cũ chỉ bơm field modulation. Không
        # module nào trong bridge này được trả public candidate/action.
        self._emit_module_modulations(current)

        # SubjectInitiative V102-final semantics: sensor/opportunity only. Thay vì
        # nominate speech, nó inject modulation; semantic material/focus upstream
        # sẽ quyết định có gì đáng nghĩ/nói. Structured owners không bị xóa.
        filtered: list[Any] = []
        expression_rows: list[Any] = []
        structured_present = False
        for affordance in rows:
            owner = str(getattr(affordance, "owner", "") or "").strip().lower()
            candidate = getattr(affordance, "candidate", None)
            kind = str(getattr(candidate, "kind", "") or "").strip().lower() if candidate is not None else ""
            if owner in _GENERIC_SENSOR_OWNERS and self.active():
                self._inject_subject_initiative_modulation(candidate, now=current)
                continue
            if owner == "expression_urge":
                expression_rows.append(affordance)
                filtered.append(affordance)
                continue
            if owner in _STRUCTURED_OWNERS or kind == "force_speak":
                structured_present = True
            filtered.append(affordance)

        targets = self._targets_from_affordances(expression_rows)
        snapshot = self.activation_bus.snapshot()
        decision = self.motor.evaluate(
            snapshot,
            targets=targets,
            runtime_context=context,
            hard_boundary=False,
            now=current,
        )

        # Shadow mode chỉ trace divergence, tuyệt đối không đổi danh sách.
        if self.config.mode != "active" or structured_present:
            scope = "shadow" if self.config.mode == "shadow" else ("structured_legacy" if structured_present else "off")
            with self._lock:
                self._pending_decision = decision
                self._pending_scope = scope
                self._history.append({"at": current, "event": "motor", "scope": scope, "decision": decision.to_dict()})
            return tuple(filtered), decision

        # Normal autonomous path: candidate list không còn là authority. Chỉ giữ
        # compatibility affordance đúng semantic target motor đã bind. Nếu motor
        # chọn silence/body/vocal/micro thì expression candidate không được đưa
        # vào Neuro vote ở tick đó.
        if decision.channel == "proposition" and decision.semantic_source_id:
            selected = [
                row for row in expression_rows
                if self._affordance_source_id(row) == decision.semantic_source_id
            ]
            filtered = [row for row in filtered if str(getattr(row, "owner", "") or "").strip().lower() != "expression_urge"]
            filtered.extend(selected[:1])
        else:
            filtered = [row for row in filtered if str(getattr(row, "owner", "") or "").strip().lower() != "expression_urge"]

        with self._lock:
            self._pending_decision = decision
            self._pending_scope = "normal_autonomous"
            self._history.append({"at": current, "event": "motor", "scope": self._pending_scope, "decision": decision.to_dict()})
        return tuple(filtered), decision

    def selection_for_frame(self, frame: Any):
        """Trả SelectionResult one-hot khi motor đang sở hữu action authority.

        Import SelectionResult lazy để tránh vòng import neuro_control -> runtime.
        Không chấm coalition.score, không softmax candidate. Coalition chỉ được map
        theo action family/typed commitment mà motor đã commit.
        """
        if not self.active():
            return None
        with self._lock:
            decision = self._pending_decision
            scope = self._pending_scope
        from runtime.autonomy.authority_trace import emit as autonomy_trace
        if decision is None or scope != "normal_autonomous":
            autonomy_trace("v103.selection_unavailable", scope=str(scope))
            return None

        coalitions = tuple(getattr(frame, "coalitions", ()) or ())
        if not coalitions:
            return None

        desired_index = self._match_coalition_index(coalitions, decision)
        if desired_index is None:
            return None

        try:
            from cortex.subject.neuro_control import SelectionResult
        except Exception:
            return None

        scores = tuple(1.0 if i == desired_index else 0.0 for i in range(len(coalitions)))
        probabilities = scores
        try:
            # Workspace telemetry vẫn hữu ích, nhưng không được dùng để chọn winner.
            from cortex.subject.neuro_control import NeuroActionSelector
            broadcast = NeuroActionSelector().workspace(frame)
        except Exception:
            broadcast = None
        if broadcast is None:
            return None

        self.health.note_candidate_bypass()
        selection = SelectionResult(
            winner_index=desired_index,
            scores=scores,
            probabilities=probabilities,
            margin=1.0,
            entropy=0.0,
            requires_deliberation=False,
            parallel_review_recommended=False,
            reason=f"candidate_bypass:nervous_motor:{decision.channel}",
            broadcast=broadcast,
        )
        with self._lock:
            self._history.append({
                "at": float(self.clock()),
                "event": "candidate_bypass",
                "frame_id": str(getattr(frame, "frame_id", "") or ""),
                "winner_index": desired_index,
                "channel": decision.channel,
                "semantic_source_id": decision.semantic_source_id,
            })
        autonomy_trace("v103.selection", frame_id=str(getattr(frame, "frame_id", "")),
                       channel=str(decision.channel), scope=str(scope))
        return selection

    def inject_semantic_modulation(
        self,
        *,
        source_id: str,
        uncertainty: float,
        epistemic_support: float,
        hard_boundary: bool = False,
        now: float | None = None,
    ) -> ModulationEvent:
        """Semantic critic -> uncertainty/inhibition modulation, không vote.

        Hard boundary vẫn đi đường boolean ở caller. Event này chỉ đại diện phần
        soft epistemic restraint để dynamics nhớ nó qua nhiều tick.
        """
        current = float(now if now is not None else self.clock())
        u = _clamp01(uncertainty)
        support = _clamp01(epistemic_support)
        inhibition = _clamp01(0.10 + 0.62 * u + 0.28 * (1.0 - support) + (0.35 if hard_boundary else 0.0))
        self._modulation_sequence += 1
        event = ModulationEvent(
            source="semantic_critic_v103",
            targets={"uncertainty": u, "inhibition": inhibition},
            impulses={"expressive_pressure": -0.16 * u},
            strength=0.78,
            half_life_seconds=1.8,
            ttl_seconds=5.0,
            created_at=current,
            event_id=f"semantic_critic_v103:{source_id}:{self._modulation_sequence}",
            tags=("semantic_modulation", "critic_no_vote", "hard_boundary" if hard_boundary else "soft"),
        )
        self.activation_bus.submit(event)
        return event

    def note_public_commit(self, source_id: str = "", *, now: float | None = None) -> None:
        current = float(now if now is not None else self.clock())
        self.motor.note_public_commit(source_id, now=current)
        with self._lock:
            self._history.append({"at": current, "event": "public_commit", "source_id": source_id})
            self._pending_decision = None
            self._pending_scope = ""

    def note_external_stimulus(self, *, source_id: str = "", strength: float = 0.5, now: float | None = None) -> None:
        current = float(now if now is not None else self.clock())
        if source_id:
            self.stp.note_fresh_evidence(source_id, strength=strength, now=current)
        # Novel stimulus reset boredom theo roadmap; đây là modulation event chứ
        # không tạo candidate/topic.
        self._modulation_sequence += 1
        self.activation_bus.submit(ModulationEvent(
            source="novel_stimulus_v103",
            impulses={
                "curiosity": 0.18 * _clamp01(strength),
                "boredom": -0.46 * _clamp01(strength),
                "uncertainty": -0.08 * _clamp01(strength),
            },
            strength=0.78,
            half_life_seconds=0.9,
            ttl_seconds=3.0,
            created_at=current,
            event_id=f"novel_stimulus_v103:{self._modulation_sequence}",
            tags=("external_event", "attractor_escape", "fresh_evidence"),
        ))

    def trace_snapshot(self, *, now: float | None = None, top_inputs: int = 8) -> dict[str, Any]:
        """Projection public-safe cho causal trace field -> input -> motor.

        Không chứa chain-of-thought, raw prompt hay private material. ``top_inputs``
        chỉ là contribution số học gần đúng từ ModulationEvent gần nhất, đủ để
        debug câu hỏi kiểu "vì sao motor vừa thở dài?" mà không suy luận ngược prose.
        """
        current = float(now if now is not None else self.clock())
        snapshot = self.activation_bus.snapshot()
        with self._lock:
            decision = self._pending_decision
            scope = self._pending_scope
        motor = dict(decision.drives) if decision is not None else {}
        return {
            "nervous_schema": NERVOUS_TRACE_SCHEMA,
            "field_snapshot": {name: round(snapshot.value(name), 4) for name in ACTIVATION_FIELDS},
            "top_inputs": [list(row) for row in self._top_modulation_inputs(snapshot, limit=top_inputs)],
            "motor": {str(key): round(_clamp01(value), 4) for key, value in motor.items()},
            "commit": str(getattr(decision, "channel", "") or "") if bool(getattr(decision, "committed", False)) else "",
            "motor_committed": bool(getattr(decision, "committed", False)),
            "motor_reason": str(getattr(decision, "reason", "") or ""),
            "source_revision": int(getattr(decision, "source_revision", snapshot.revision) or snapshot.revision),
            "nervous_owner": "eris",
            "nervous_mode": self.config.mode,
            "nervous_scope": scope,
            "trace_at": current,
        }

    def migrate_legacy_state(
        self,
        payload: Mapping[str, Any],
        *,
        source_version: str,
        now: float | None = None,
    ) -> tuple[ModulationEvent, ...]:
        """Chuyển state V97/V102 sang modulation, không resurrect candidate authority.

        Migration cố tình lossy: chỉ giữ pressure/salience/task/inhibition cần cho
        continuous state. Candidate, winner index và retry timer cũ tuyệt đối không
        được phục hồi. Đây là contract cần cho upgrade state theo roadmap V103.
        """
        current = float(now if now is not None else self.clock())
        version = str(source_version or "").strip().lower().replace(".", "")
        data = dict(payload or {})
        events: list[ModulationEvent] = []
        self._modulation_sequence += 1
        seq = self._modulation_sequence

        if version in {"v97", "97", "erispublicturntracev97"}:
            action = str(data.get("authority_action") or data.get("governor_action") or data.get("action") or "").strip().lower()
            motive = str(data.get("motive") or "").strip().lower()
            goal_state = str(data.get("goal_state") or "").strip().lower()
            confidence = _clamp01(data.get("confidence", 0.6), 0.6)
            committed = bool(data.get("public_committed", False))
            targets: dict[str, float] = {}
            impulses: dict[str, float] = {}
            if goal_state in {"active", "running", "in_progress"} or any(token in motive for token in ("task", "goal", "continue")):
                targets["task_focus"] = _clamp01(0.48 + 0.28 * confidence)
            if action in {"speak", "speech"} or committed:
                # Public act vừa xảy ra: giữ refractory/homeostasis, không nạp lại
                # candidate đã nói như một desire mới.
                targets["inhibition"] = _clamp01(0.42 + 0.22 * confidence)
                impulses["expressive_pressure"] = -0.24
            elif action in {"body_only", "body"}:
                impulses["expressive_pressure"] = 0.05 * confidence
            else:
                impulses["curiosity"] = 0.06 * confidence
            if targets or impulses:
                events.append(ModulationEvent(
                    source="legacy_v97_state_migration", targets=targets, impulses=impulses,
                    strength=0.66, half_life_seconds=2.4, ttl_seconds=8.0, created_at=current,
                    event_id=f"legacy-v97:{seq}", tags=("migration", "v97", "no_candidate_restore"),
                ))

        elif version in {"v102", "102", "erisexpressioncommitmenttransitionv102"}:
            charge = _clamp01(data.get("charge", 0.0))
            signal = _clamp01(data.get("signal", charge))
            satisfaction = _clamp01(data.get("satisfaction", 0.0))
            state = str(data.get("state") or "forming").strip().lower()
            motive = str(data.get("motive") or "").strip().lower()
            persistence = _clamp01(1.0 - satisfaction)
            expressive = _clamp01(0.10 + 0.58 * charge + 0.22 * signal) * persistence
            concern = _clamp01(0.12 + 0.52 * max(charge, signal)) * persistence
            targets = {
                "expressive_pressure": expressive,
                "concern_salience": concern,
            }
            if any(token in motive for token in ("task", "goal", "continue", "followup")):
                targets["task_focus"] = _clamp01(0.34 + 0.42 * max(charge, signal))
            if state in {"committed", "deferred_committed"}:
                targets["inhibition"] = 0.28 if state == "committed" else 0.44
            events.append(ModulationEvent(
                source="legacy_v102_state_migration", targets=targets,
                strength=0.72, half_life_seconds=3.2, ttl_seconds=12.0, created_at=current,
                event_id=f"legacy-v102:{seq}", tags=("migration", "v102", "commitment_to_field"),
            ))
        else:
            raise ValueError(f"unsupported legacy nervous state: {source_version}")

        accepted: list[ModulationEvent] = []
        for event in events:
            if self.activation_bus.submit(event):
                accepted.append(event.normalized(now=current))
        with self._lock:
            self._history.append({
                "at": current, "event": "legacy_state_migration", "source_version": source_version,
                "events": [event.to_dict() for event in accepted],
            })
        return tuple(accepted)

    def _top_modulation_inputs(
        self, snapshot: ActivationSnapshot, *, limit: int = 8
    ) -> tuple[tuple[str, str, float], ...]:
        rows: list[tuple[str, str, float]] = []
        for event in self.activation_bus.recent_events(limit=32):
            source = str(event.get("source") or "unknown")[:96]
            strength = _clamp01(event.get("strength", 0.55), 0.55)
            impulses = event.get("impulses") if isinstance(event.get("impulses"), Mapping) else {}
            targets = event.get("targets") if isinstance(event.get("targets"), Mapping) else {}
            for field_name, raw in dict(impulses).items():
                if field_name in ACTIVATION_FIELDS:
                    try:
                        impact = max(-1.0, min(1.0, float(raw))) * strength
                    except (TypeError, ValueError):
                        continue
                    if abs(impact) >= 0.005:
                        rows.append((source, field_name, impact))
            for field_name, raw in dict(targets).items():
                if field_name in ACTIVATION_FIELDS:
                    try:
                        target = _clamp01(raw)
                    except Exception:
                        continue
                    # Target pull không phải delta thật của integrator; nhân 0.5 để
                    # trace chỉ biểu diễn "đóng góp tương đối", không giả vờ đo synapse.
                    impact = (target - snapshot.value(field_name)) * strength * 0.5
                    if abs(impact) >= 0.005:
                        rows.append((source, field_name, impact))
        rows.sort(key=lambda item: abs(item[2]), reverse=True)
        return tuple((source, field_name, round(impact, 4)) for source, field_name, impact in rows[: max(1, int(limit))])

    def status(self) -> dict[str, Any]:
        with self._lock:
            decision = self._pending_decision.to_dict() if self._pending_decision is not None else None
            scope = self._pending_scope
        return {
            "schema": NERVOUS_FINAL_SCHEMA,
            "mode": self.config.mode,
            "scope": scope,
            "config": self.config.to_dict(),
            "pending_motor": decision,
            "stp": list(self.stp.snapshot(limit=12)),
            "health": self.health.snapshot(),
            "contract": {
                "normal_autonomous_candidate_voting": False if self.active() else True,
                "subject_initiative_is_sensor": self.active(),
                "semantic_critic_votes": False,
                "hard_safety_veto_absolute": True,
                "expression_urge_role": "compatibility_facade" if self.active() else "legacy",
            },
        }

    def recent_history(self, limit: int = 32) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(list(self._history)[-max(1, int(limit)):])

    def _emit_module_modulations(self, now: float) -> None:
        """Bridge V100 final: mental material/concern/goal/affect -> ModulationEvent.

        Đây là migration adapter có chủ đích. Nó *đọc* state typed của module cũ
        rồi emit field input; không gọi candidate builder, không Surface, không LLM.
        Khi từng module native-emits ModulationEvent sau này, có thể xóa adapter
        này mà không đổi motor/authority.
        """
        host = self.host
        if host is None:
            return
        events: list[ModulationEvent] = []

        # MentalMaterial = semantic energy/source, không phải speech route.
        try:
            runtime = getattr(host, "mental_material_runtime", None)
            snapshot = getattr(runtime, "snapshot", None)
            rows = tuple(snapshot(now=now, limit=8) or ()) if callable(snapshot) else ()
            if rows:
                activation = max((_clamp01(row.get("activation", 0.0)) for row in rows if isinstance(row, Mapping)), default=0.0)
                concern = max((_clamp01(row.get("concern_pull", 0.0)) for row in rows if isinstance(row, Mapping)), default=0.0)
                expressive = max((_clamp01(row.get("expression_pull", 0.0)) for row in rows if isinstance(row, Mapping)), default=0.0)
                freshness = max((_clamp01(row.get("freshness", 0.0)) for row in rows if isinstance(row, Mapping)), default=0.0)
                if max(activation, concern, expressive) > 0.01:
                    self._modulation_sequence += 1
                    events.append(ModulationEvent(
                        source="mental_material_modulator_v103",
                        targets={
                            "curiosity": _clamp01(0.24 + 0.54 * activation + 0.12 * freshness),
                            "concern_salience": _clamp01(0.14 + 0.72 * concern),
                            "expressive_pressure": _clamp01(0.08 + 0.72 * expressive),
                        },
                        strength=0.58, half_life_seconds=2.0, ttl_seconds=5.0,
                        created_at=now, event_id=f"mental_material_modulator_v103:{self._modulation_sequence}",
                        tags=("modulation", "mental_material", "no_public_action"),
                    ))
        except Exception:
            pass

        # ActiveConcern chỉ là persistent salience source. Không resume goal.
        try:
            runtime = getattr(host, "active_concern_runtime", None)
            snapshot = getattr(runtime, "snapshot", None)
            rows = tuple(snapshot(now=now, limit=8) or ()) if callable(snapshot) else ()
            salience = max((
                max(_clamp01(row.get("activation", 0.0)), _clamp01(row.get("attractor_strength", 0.0)))
                for row in rows if isinstance(row, Mapping)
            ), default=0.0)
            if salience > 0.01:
                self._modulation_sequence += 1
                events.append(ModulationEvent(
                    source="active_concern_modulator_v103",
                    targets={"concern_salience": _clamp01(0.12 + 0.82 * salience)},
                    impulses={"curiosity": 0.05 * salience},
                    strength=0.62, half_life_seconds=3.0, ttl_seconds=7.0,
                    created_at=now, event_id=f"active_concern_modulator_v103:{self._modulation_sequence}",
                    tags=("modulation", "concern", "no_resume_shared_goal"),
                ))
        except Exception:
            pass

        # Goal/Life-Horizon modulate task_focus only. Self-owned goal không được
        # biến thành user obligation bởi adapter này.
        try:
            manager = getattr(host, "life_horizon_manager", None)
            snapshot = getattr(manager, "snapshot", None)
            state = snapshot(refresh=False, now=now) if callable(snapshot) else None
            goals = tuple(getattr(state, "goals", ()) or ())
            active = [g for g in goals if str(getattr(g, "status", "") or "") == "active"]
            if active:
                foreground = max((_clamp01(getattr(g, "strength", 0.0)) for g in active if str(getattr(g, "level", "") or "") in {"H0", "H1", "H2"}), default=0.0)
                if foreground > 0.0:
                    self._modulation_sequence += 1
                    events.append(ModulationEvent(
                        source="goal_lifecycle_modulator_v103",
                        targets={"task_focus": _clamp01(0.30 + 0.62 * foreground)},
                        impulses={"concern_salience": 0.05 * foreground},
                        strength=0.64, half_life_seconds=4.0, ttl_seconds=9.0,
                        created_at=now, event_id=f"goal_lifecycle_modulator_v103:{self._modulation_sequence}",
                        tags=("modulation", "goal", "task_focus_only"),
                    ))
        except Exception:
            pass

        # Affect đổi gain/pressure, không tạo topic.
        try:
            getter = getattr(host, "_get_internal_state", None)
            state = getter() if callable(getter) else None
            if state is not None:
                curiosity = _clamp01(getattr(state, "curiosity", 0.0))
                social = _clamp01(getattr(state, "social_energy", 0.5), 0.5)
                restraint = _clamp01(getattr(state, "restraint_pressure", 0.3), 0.3)
                self._modulation_sequence += 1
                events.append(ModulationEvent(
                    source="affect_modulator_v103",
                    targets={
                        "curiosity": curiosity,
                        "social_need": _clamp01(1.0 - social),
                        "inhibition": restraint,
                    },
                    strength=0.42, half_life_seconds=2.2, ttl_seconds=5.0,
                    created_at=now, event_id=f"affect_modulator_v103:{self._modulation_sequence}",
                    tags=("modulation", "affect", "gain_only"),
                ))
        except Exception:
            pass

        for event in events:
            self.activation_bus.submit(event)

    def _targets_from_affordances(self, rows: Sequence[Any]) -> tuple[MotorSemanticTarget, ...]:
        targets: list[MotorSemanticTarget] = []
        for affordance in rows:
            candidate = getattr(affordance, "candidate", None)
            if candidate is None:
                continue
            payload = getattr(candidate, "expression_commitment", None)
            payload = dict(payload) if isinstance(payload, Mapping) else {}
            source_id = _compact(payload.get("source_id", ""), 180) or _compact(getattr(candidate, "expression_urge_id", ""), 180)
            semantic_target = _compact(payload.get("semantic_target", ""), 1600) or _compact(getattr(candidate, "topic", ""), 1600)
            if not source_id or not semantic_target:
                continue
            refs = tuple(str(x) for x in (getattr(candidate, "evidence_refs", ()) or ()) if str(x))[:16]
            targets.append(MotorSemanticTarget(
                source_id=source_id,
                semantic_target=semantic_target,
                motive=_compact(payload.get("motive", getattr(candidate, "expression_motive", "")), 120),
                pressure=_clamp01(payload.get("pressure", getattr(candidate, "expression_pressure", 0.0))),
                freshness=_clamp01(payload.get("freshness", getattr(candidate, "expression_freshness", 0.0))),
                persistence=_clamp01(payload.get("persistence", 1.0 - float(getattr(candidate, "expression_satisfaction", 0.0) or 0.0))),
                uncertainty=_clamp01(getattr(candidate, "uncertainty", 0.0)),
                evidence_refs=refs,
                source_owner=str(getattr(affordance, "owner", "expression_urge") or "expression_urge"),
                affordance_id=str(getattr(affordance, "affordance_id", "") or ""),
            ))
        return tuple(targets)

    @staticmethod
    def _affordance_source_id(affordance: Any) -> str:
        candidate = getattr(affordance, "candidate", None)
        payload = getattr(candidate, "expression_commitment", None) if candidate is not None else None
        payload = dict(payload) if isinstance(payload, Mapping) else {}
        return _compact(payload.get("source_id", ""), 180) or _compact(getattr(candidate, "expression_urge_id", ""), 180)

    def _match_coalition_index(self, coalitions: Sequence[Any], decision: MotorDecisionV103) -> int | None:
        if decision.channel == "proposition":
            for index, coalition in enumerate(coalitions):
                if str(getattr(coalition, "action_kind", "") or "").strip().lower() != "speak":
                    continue
                arguments = dict(getattr(coalition, "arguments", {}) or {})
                commitment = arguments.get("expression_commitment")
                commitment = dict(commitment) if isinstance(commitment, Mapping) else {}
                source_id = _compact(commitment.get("source_id", ""), 180)
                if source_id and source_id == decision.semantic_source_id:
                    return index
            return None
        desired = "body_only" if decision.channel in {"body", "micro_expression", "vocalization"} else "silence"
        for index, coalition in enumerate(coalitions):
            action = str(getattr(coalition, "action_kind", "") or "").strip().lower()
            if action == desired:
                return index
        # Một số Subject frame chỉ có silence+speak; local motor degrade về silence
        # thay vì tự chế coalition ngoài frame.
        if desired == "body_only":
            for index, coalition in enumerate(coalitions):
                if str(getattr(coalition, "action_kind", "") or "").strip().lower() == "silence":
                    return index
        return None

    def _inject_subject_initiative_modulation(self, candidate: Any, *, now: float) -> None:
        if candidate is None:
            return
        act = str(getattr(candidate, "speech_act", "") or "").strip().lower()
        topic = _compact(getattr(candidate, "topic", ""), 500)
        relevance = _clamp01(getattr(candidate, "relevance", 0.5), 0.5)
        urgency = _clamp01(getattr(candidate, "urgency", 0.2), 0.2)
        self._modulation_sequence += 1
        impulses = {
            "curiosity": 0.08 + 0.10 * relevance,
            "concern_salience": 0.06 + 0.10 * urgency,
            "expressive_pressure": 0.04 + 0.08 * relevance,
        }
        if act in {"resume_shared_goal", "continue_task", "task_followup"}:
            impulses["task_focus"] = 0.12 + 0.12 * relevance
        self.activation_bus.submit(ModulationEvent(
            source="subject_initiative_sensor_v103",
            impulses=impulses,
            strength=0.58,
            half_life_seconds=1.5,
            ttl_seconds=4.0,
            created_at=now,
            event_id=f"subject_initiative_sensor_v103:{self._modulation_sequence}",
            tags=("modulation", "sensor_only", act or "initiative", "has_topic" if topic else "no_topic"),
        ))


__all__ = [
    "NERVOUS_FINAL_SCHEMA",
    "NERVOUS_HEALTH_SCHEMA",
    "NERVOUS_MIGRATION_SCHEMA",
    "NERVOUS_TRACE_SCHEMA",
    "MotorDecisionV103",
    "MotorSemanticTarget",
    "NervousAutonomousAuthorityV103",
    "NervousHealthMonitorV103",
    "NervousSystemConfigV103",
    "ShortTermPlasticityV103",
    "UnifiedExpressionMotorV103",
]
