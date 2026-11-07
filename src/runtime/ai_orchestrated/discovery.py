# -*- coding: utf-8 -*-
"""Discovery & Local Signal Runtime cho Architecture C.

Mục tiêu của module này rất hẹp:
- Tutor/search chỉ dùng cho dữ liệu mới, local hoặc cần kiểm chứng.
- Main model vẫn sở hữu việc đánh giá, hình thành quan điểm và lời public.
- Không bịa activity nền; mọi activity phải có id, trạng thái và timestamp thật.
- Không coi nhiệt độ ngoài trời là nhiệt độ phòng. Vật lý vẫn chưa chịu nhường
  chỗ cho văn phong, đáng tiếc cho nhiều chatbot.

Discovery state là archive ngoại vi, không phải personality state. Identity
Kernel chỉ học shared_curiosity/reciprocity qua observer sau phản ứng thật của
User. Nhờ vậy một fact được tìm thấy không tự sửa tính cách chỉ vì nó nghe hay.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.workspace_manager import default_eris_home
from runtime.diagnostic_logging import safe_log


DISCOVERY_SCHEMA = "eris.discovery.v1"
DISCOVERY_LANES = {
    "vietnam_local_signal",
    "fresh_curiosity",
    "user_environment",
    "tutor_artifact",
}


def _compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    lane: str = "none"
    topic: str = ""
    needs_external: bool = False
    freshness_required: bool = False
    verification_required: bool = False
    eris_interest: float = 0.0
    user_relevance: float = 0.0
    novelty: float = 0.0
    importance: float = 0.0
    confidence: float = 0.0
    reason: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "DiscoveryPlan":
        data = payload or {}
        lane = _compact(data.get("lane", "none"), 48).lower() or "none"
        if lane not in {"none", *DISCOVERY_LANES}:
            lane = "none"
        return cls(
            lane=lane,
            topic=_compact(data.get("topic", ""), 320),
            needs_external=bool(data.get("needs_external", False)),
            freshness_required=bool(data.get("freshness_required", False)),
            verification_required=bool(data.get("verification_required", False)),
            eris_interest=_clamp01(data.get("eris_interest", 0.0)),
            user_relevance=_clamp01(data.get("user_relevance", 0.0)),
            novelty=_clamp01(data.get("novelty", 0.0)),
            importance=_clamp01(data.get("importance", 0.0)),
            confidence=_clamp01(data.get("confidence", 0.0)),
            reason=_compact(data.get("reason", ""), 260),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DiscoveryRecord:
    discovery_id: str
    lane: str
    topic: str
    summary: str
    source: str = "tutor_gemini"
    why_eris_cared: str = ""
    eris_take: str = ""
    source_confidence: float = 0.68
    freshness: float = 0.70
    novelty: float = 0.60
    eris_interest: float = 0.70
    user_relevance: float = 0.50
    share_readiness: float = 0.62
    verified: bool = True
    evidence: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)
    shared: bool = False
    shared_at: float = 0.0
    shared_text: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "DiscoveryRecord":
        data = payload or {}
        raw_evidence = _sequence(data.get("evidence", ()))
        evidence = tuple(_compact(item, 180) for item in raw_evidence[:8] if _compact(item, 180))
        lane = _compact(data.get("lane", "tutor_artifact"), 48).lower() or "tutor_artifact"
        if lane not in DISCOVERY_LANES:
            lane = "tutor_artifact"
        return cls(
            discovery_id=_compact(data.get("discovery_id", ""), 100) or f"disc-{uuid.uuid4().hex[:12]}",
            lane=lane,
            topic=_compact(data.get("topic", ""), 320),
            summary=_compact(data.get("summary", ""), 1400),
            source=_compact(data.get("source", "tutor_gemini"), 80) or "tutor_gemini",
            why_eris_cared=_compact(data.get("why_eris_cared", ""), 360),
            eris_take=_compact(data.get("eris_take", ""), 420),
            source_confidence=_clamp01(data.get("source_confidence", 0.68), 0.68),
            freshness=_clamp01(data.get("freshness", 0.70), 0.70),
            novelty=_clamp01(data.get("novelty", 0.60), 0.60),
            eris_interest=_clamp01(data.get("eris_interest", 0.70), 0.70),
            user_relevance=_clamp01(data.get("user_relevance", 0.50), 0.50),
            share_readiness=_clamp01(data.get("share_readiness", 0.62), 0.62),
            verified=bool(data.get("verified", True)),
            evidence=evidence,
            created_at=max(0.0, float(data.get("created_at", time.time()) or time.time())),
            shared=bool(data.get("shared", False)),
            shared_at=max(0.0, float(data.get("shared_at", 0.0) or 0.0)),
            shared_text=_compact(data.get("shared_text", ""), 520),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "discovery_id": self.discovery_id,
            "lane": self.lane,
            "topic": self.topic,
            "verified_summary": self.summary,
            "why_eris_cared": self.why_eris_cared,
            "eris_take": self.eris_take,
            "source": self.source,
            "source_confidence": round(self.source_confidence, 3),
            "freshness": round(self.freshness, 3),
            "evidence": list(self.evidence[:5]),
        }

    def panel_payload(self) -> dict[str, Any]:
        """Contract đầy đủ cho panel, tách khỏi payload tối giản gửi planner.

        Panel cần readiness, novelty và trạng thái chia sẻ thật. Không để C# tự
        đoán các giá trị này từ màu sắc hoặc thứ tự archive, vì dashboard đã đủ
        cơ hội bịa chuyện trong lịch sử loài người rồi.
        """

        payload = self.prompt_payload()
        payload.update(
            {
                "summary": self.summary,
                "share_readiness": round(self.share_readiness, 3),
                "novelty": round(self.novelty, 3),
                "eris_interest": round(self.eris_interest, 3),
                "user_relevance": round(self.user_relevance, 3),
                "verified": bool(self.verified),
                "created_at": self.created_at,
                "shared": bool(self.shared),
                "shared_at": self.shared_at,
                "shared_text": self.shared_text,
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    activity_id: str = ""
    kind: str = "idle"
    topic: str = ""
    status: str = "idle"
    detail: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "ActivityRecord":
        data = payload or {}
        return cls(
            activity_id=_compact(data.get("activity_id", ""), 100),
            kind=_compact(data.get("kind", "idle"), 60).lower() or "idle",
            topic=_compact(data.get("topic", ""), 320),
            status=_compact(data.get("status", "idle"), 40).lower() or "idle",
            detail=_compact(data.get("detail", ""), 420),
            started_at=max(0.0, float(data.get("started_at", 0.0) or 0.0)),
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
            evidence=tuple(_compact(item, 180) for item in _sequence(data.get("evidence", ()))[:6] if _compact(item, 180)),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload

    @property
    def active(self) -> bool:
        return bool(self.activity_id and self.status in {"queued", "searching", "verifying", "curating"})


@dataclass(frozen=True, slots=True)
class DiscoveryState:
    version: int = 0
    records: tuple[DiscoveryRecord, ...] = ()
    current_activity: ActivityRecord = field(default_factory=ActivityRecord)
    last_tutor_answer_ts: float = 0.0
    last_plan_at: float = 0.0
    last_shared_at: float = 0.0
    lane_last_started: Mapping[str, float] = field(default_factory=dict)
    failed_topics: Mapping[str, float] = field(default_factory=dict)
    updated_at: float = 0.0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "DiscoveryState":
        data = payload or {}
        records = tuple(
            DiscoveryRecord.from_mapping(item)
            for item in _sequence(data.get("records", ()))[-80:]
            if isinstance(item, Mapping)
        )
        return cls(
            version=max(0, int(data.get("version", 0) or 0)),
            records=records,
            current_activity=ActivityRecord.from_mapping(_mapping(data.get("current_activity", {}))),
            last_tutor_answer_ts=max(0.0, float(data.get("last_tutor_answer_ts", 0.0) or 0.0)),
            last_plan_at=max(0.0, float(data.get("last_plan_at", 0.0) or 0.0)),
            last_shared_at=max(0.0, float(data.get("last_shared_at", 0.0) or 0.0)),
            lane_last_started={
                _compact(key, 60).lower(): max(0.0, float(value or 0.0))
                for key, value in _mapping(data.get("lane_last_started", {})).items()
                if _compact(key, 60)
            },
            failed_topics={
                _compact(key, 180).casefold(): max(0.0, float(value or 0.0))
                for key, value in _mapping(data.get("failed_topics", {})).items()
                if _compact(key, 180)
            },
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DISCOVERY_SCHEMA,
            "version": self.version,
            "records": [item.to_dict() for item in self.records],
            "current_activity": self.current_activity.to_dict(),
            "last_tutor_answer_ts": self.last_tutor_answer_ts,
            "last_plan_at": self.last_plan_at,
            "last_shared_at": self.last_shared_at,
            "lane_last_started": dict(self.lane_last_started),
            "failed_topics": dict(self.failed_topics),
            "updated_at": self.updated_at,
        }


class ExternalNeedGate:
    """Chỉ cho Tutor xử lý thứ cần dữ liệu ngoài model.

    Gate không cố đo IQ của Terra. Nó chỉ hỏi một câu thực dụng hơn: dữ kiện này
    có thể đã đổi, có tính địa phương, phụ thuộc sensor hay cần nguồn xác minh hay
    không. Kiến thức phổ thông ổn định bị chặn, đỡ gọi Gemini để hỏi nước có ướt.
    """

    _FRESH_MARKERS = {
        "hôm nay", "hiện tại", "mới nhất", "gần đây", "24 giờ", "tuần này",
        "today", "current", "latest", "recent", "breaking", "2026",
    }
    _LOCAL_MARKERS = {
        "việt nam", "trong nước", "địa phương", "thành phố", "tỉnh", "quận",
        "vietnam", "local", "nearby",
    }

    def allow(
        self,
        plan: DiscoveryPlan,
        *,
        room_sensor_available: bool = False,
        configured_location: bool = False,
    ) -> tuple[bool, str]:
        if plan.lane == "none" or not plan.topic.strip():
            return False, "empty_plan"
        text = plan.topic.casefold()
        if plan.lane == "user_environment":
            if "nhiệt độ phòng" in text or "room temperature" in text:
                return (room_sensor_available, "room_sensor" if room_sensor_available else "room_sensor_missing")
            weather_markers = ("thời tiết", "nhiệt độ ngoài trời", "outdoor temperature", "weather")
            if any(marker in text for marker in weather_markers):
                if not configured_location:
                    return False, "configured_location_missing"
                return True, "outdoor_weather_requires_fresh_data"
            # Vị trí của User là runtime setting/telemetry, không phải một câu hỏi
            # để search engine suy đoán.
            return False, "environment_requires_runtime_sensor_or_setting"
        if plan.lane == "vietnam_local_signal":
            return True, "local_fresh_signal"
        if plan.freshness_required or plan.verification_required:
            return True, "freshness_or_verification"
        if any(marker in text for marker in self._FRESH_MARKERS | self._LOCAL_MARKERS):
            return True, "query_contains_external_marker"
        return False, "stable_knowledge_main_model_can_handle"


class DiscoveryRuntime:
    """Archive + scheduler nhẹ, không chạm lời public trực tiếp."""

    def __init__(self, host: Any, router: Any, *, logger: Any | None = None, state_path: Path | None = None) -> None:
        self.host = host
        self.router = router
        self.logger = logger
        self.gate = ExternalNeedGate()
        self._lock = threading.RLock()
        self._job_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._started_at = time.time()
        self._last_planning_attempt_at = 0.0
        self._state_path = state_path or (default_eris_home() / "state" / "discovery_state.json")
        self._state = self._load()
        self._last_planned: DiscoveryPlan = DiscoveryPlan()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @staticmethod
    def _setting(kind: str, default: Any) -> Any:
        try:
            from runtime.runtime_settings_store import runtime_setting
            return runtime_setting("discovery", kind, default=default)
        except Exception:
            env_key = "ERIS_DISCOVERY_" + kind.upper()
            return os.getenv(env_key, default)

    @classmethod
    def _bool(cls, kind: str, default: bool) -> bool:
        value = cls._setting(kind, default)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _float(cls, kind: str, default: float) -> float:
        try:
            return float(cls._setting(kind, default))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _text(cls, kind: str, default: str) -> str:
        return str(cls._setting(kind, default) or default).strip()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> DiscoveryState:
        try:
            if self._state_path.is_file():
                return DiscoveryState.from_mapping(json.loads(self._state_path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "[discovery] load failed: %s", exc)
        return DiscoveryState()

    def _persist_locked(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "[discovery] persist failed: %s", exc)

    def snapshot(self) -> DiscoveryState:
        with self._lock:
            return DiscoveryState.from_mapping(self._state.to_dict())

    def _replace_state(self, **changes: Any) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                version=self._state.version + 1,
                updated_at=time.time(),
                **changes,
            )
            self._persist_locked()

    # ------------------------------------------------------------------
    # Tutor ingestion
    # ------------------------------------------------------------------
    def sync_tutor_artifact(self) -> DiscoveryRecord | None:
        daemon = getattr(self.host, "tutor_daemon", None)
        if daemon is None:
            return None
        answer_ts = float(getattr(daemon, "last_answer_ts", 0.0) or 0.0)
        if answer_ts <= 0.0:
            return None
        discovery_meta = _mapping(getattr(daemon, "_eris_discovery_meta", {}))
        # Chỉ ingest artifact do DiscoveryRuntime tự yêu cầu. Search trực tiếp của
        # User đã được trả trong chat; đem nó ra khoe lại vài phút sau chỉ chứng
        # minh rằng archive có trí nhớ nhưng thiếu tự trọng.
        if not discovery_meta:
            return None
        with self._lock:
            if answer_ts <= self._state.last_tutor_answer_ts:
                return None
        answer = _compact(getattr(daemon, "last_answer_text", ""), 1400)
        if not answer:
            return None
        bundle = _mapping(getattr(daemon, "last_research_bundle", {}))
        thread = _mapping(getattr(daemon, "last_research_thread", {}))
        question = _compact(getattr(daemon, "last_question_text", ""), 320)
        topic = _compact(thread.get("topic", ""), 320) or question or "Tutor discovery"
        lane = _compact(bundle.get("lane", ""), 48).lower()
        if lane not in DISCOVERY_LANES:
            mode = _compact(bundle.get("mode", ""), 40).lower()
            lane = "vietnam_local_signal" if "local" in mode or "news" in topic.casefold() else "tutor_artifact"
        plan = DiscoveryPlan.from_mapping(discovery_meta.get("plan") if isinstance(discovery_meta.get("plan"), Mapping) else {})
        if not plan.topic:
            plan = self._last_planned if self._last_planned.topic else DiscoveryPlan(
                lane=lane,
                topic=topic,
                eris_interest=0.72,
                user_relevance=0.55,
                novelty=0.65,
                importance=0.62,
                confidence=0.68,
                reason="Tutor produced a verified artifact.",
            )
        record = self._record_from_answer(
            plan=replace(plan, lane=lane, topic=topic),
            answer=answer,
            bundle=bundle,
            created_at=answer_ts,
        )
        self._upsert_record(record, last_tutor_answer_ts=answer_ts)
        return record

    def _record_from_answer(
        self,
        *,
        plan: DiscoveryPlan,
        answer: str,
        bundle: Mapping[str, Any],
        created_at: float,
    ) -> DiscoveryRecord:
        verification = _mapping(bundle.get("verification", {}))
        sources = _sequence(bundle.get("sources", ()))
        source_conf = _clamp01(
            verification.get("confidence", bundle.get("confidence", plan.confidence or 0.68)),
            0.68,
        )
        explicitly_failed = (
            verification.get("verified") is False
            or verification.get("passed") is False
            or str(verification.get("overall_status", "") or "").strip().lower() == "failed"
        )
        source_policy_pass = bool(verification.get("source_policy_pass", False))
        try:
            citation_coverage = float(verification.get("citation_coverage", 0.0) or 0.0)
        except (TypeError, ValueError):
            citation_coverage = 0.0
        try:
            orphan_claims = int(verification.get("orphan_claim_count", 0) or 0)
        except (TypeError, ValueError):
            orphan_claims = 0
        # Discovery có thể được Eris chủ động kể lại, nên chuẩn nguồn phải cao
        # hơn một câu search trực tiếp. Không archive tin nguồn yếu rồi vô tình
        # biến nó thành "điều Eris biết" ở lượt sau.
        verified = bool(
            answer.strip()
            and not explicitly_failed
            and source_conf >= 0.52
            and source_policy_pass
            and citation_coverage >= 0.65
            and orphan_claims <= 1
        )
        evidence: list[str] = []
        for item in sources[:5]:
            if isinstance(item, Mapping):
                label = _compact(item.get("title", item.get("url", item.get("source", ""))), 180)
            else:
                label = _compact(item, 180)
            if label:
                evidence.append(label)
        if not evidence:
            evidence.append(f"tutor_artifact:{int(created_at)}")
        readiness = _clamp01(
            0.24 * source_conf
            + 0.22 * (plan.novelty or 0.60)
            + 0.27 * (plan.eris_interest or 0.70)
            + 0.17 * (plan.user_relevance or 0.50)
            + 0.10 * (plan.importance or 0.60),
            0.62,
        )
        return DiscoveryRecord(
            discovery_id=f"disc-{uuid.uuid4().hex[:12]}",
            lane=plan.lane if plan.lane in DISCOVERY_LANES else "tutor_artifact",
            topic=plan.topic,
            summary=_compact(answer, 1400),
            source="tutor_gemini",
            why_eris_cared=_compact(plan.reason, 360),
            eris_take="",
            source_confidence=source_conf,
            freshness=0.92 if plan.freshness_required or plan.lane == "vietnam_local_signal" else 0.72,
            novelty=plan.novelty or 0.60,
            eris_interest=plan.eris_interest or 0.70,
            user_relevance=plan.user_relevance or 0.50,
            share_readiness=readiness,
            verified=verified,
            evidence=tuple(evidence),
            created_at=created_at,
        )

    def _upsert_record(self, record: DiscoveryRecord, *, last_tutor_answer_ts: float | None = None) -> None:
        with self._lock:
            records = [item for item in self._state.records if item.topic.casefold() != record.topic.casefold()]
            records.append(record)
            records = sorted(records, key=lambda item: item.created_at)[-80:]
            changes: dict[str, Any] = {"records": tuple(records)}
            if last_tutor_answer_ts is not None:
                changes["last_tutor_answer_ts"] = max(self._state.last_tutor_answer_ts, last_tutor_answer_ts)
            self._state = replace(
                self._state,
                version=self._state.version + 1,
                updated_at=time.time(),
                **changes,
            )
            self._persist_locked()

    # ------------------------------------------------------------------
    # Planning and background search
    # ------------------------------------------------------------------
    def maybe_tick(self, planner_snapshot: Mapping[str, Any], *, now: float | None = None) -> None:
        now = float(now if now is not None else time.time())
        self.sync_tutor_artifact()
        if not self._bool("enabled", True):
            return
        thread = self._job_thread
        if thread is not None and thread.is_alive():
            return
        if bool(getattr(self.host, "chat_mode", False)) or bool(getattr(self.host, "_chat_processing", False)):
            return
        # Không vừa boot xong đã mở browser đi săn tin. Startup cần ổn định UI,
        # TTS và chat trước; discovery là việc nhàn rỗi thật sự, không phải nghi
        # lễ chào ngày mới bằng cách đốt quota.
        startup_grace = max(120.0, self._float("startup_grace_seconds", 600.0))
        if now - self._started_at < startup_grace:
            return
        last_user_at = float(getattr(self.host, "_chat_last_activity_at", 0.0) or 0.0)
        minimum_idle = max(60.0, self._float("minimum_idle_seconds", 240.0))
        if last_user_at > 0.0 and now - last_user_at < minimum_idle:
            return

        with self._lock:
            if now - self._state.last_plan_at < max(300.0, self._float("planning_interval_seconds", 1800.0)):
                return
            attempt_gap = max(120.0, self._float("planning_attempt_interval_seconds", 300.0))
            if now - self._last_planning_attempt_at < attempt_gap:
                return
            self._last_planning_attempt_at = now

        # Để trí khôn của Eris chọn lý do tìm kiếm trước: nối mạch đang dang dở,
        # kiếm một điều hay để trêu/bắt chuyện, hoặc theo đuổi tò mò riêng có căn
        # cứ. Tín hiệu tin địa phương chỉ là fallback khi planner không có ý hay,
        # không còn quyền chen lên đầu hàng đợi như một trang chủ tin tức biết nói.
        try:
            plan = self.router.plan_discovery(dict(planner_snapshot))
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[discovery] planner failed: %s", exc)
            plan = DiscoveryPlan()
        if not isinstance(plan, DiscoveryPlan):
            plan = DiscoveryPlan.from_mapping(plan if isinstance(plan, Mapping) else {})
        if plan.lane == "none" or not plan.topic.strip():
            due_local = self._choose_due_local_plan(now)
            if due_local is not None:
                plan = due_local
        self._last_planned = plan
        if plan.confidence < 0.60 or plan.importance < 0.52 or plan.eris_interest < 0.52:
            return

        telemetry = _mapping(planner_snapshot.get("telemetry", {}))
        room_sensor_available = self._room_temperature(telemetry) is not None
        allowed, reason = self.gate.allow(
            plan,
            room_sensor_available=room_sensor_available,
            configured_location=bool(self._text("user_location", "")),
        )
        if not allowed or not plan.needs_external:
            self._log("debug", "[discovery] external gate rejected lane=%s reason=%s", plan.lane, reason)
            return
        with self._lock:
            # last_plan_at chỉ ghi khi plan thật sự qua gate và sắp chạy. Plan
            # rỗng/không đủ chuẩn không được làm scheduler ngủ oan nửa giờ.
            self._state = replace(self._state, last_plan_at=now, version=self._state.version + 1, updated_at=now)
            self._persist_locked()
        self._start_search(plan, now=now)

    def _choose_due_local_plan(self, now: float) -> DiscoveryPlan | None:
        if not self._bool("local_signal_enabled", True):
            return None
        interval = max(2 * 3600.0, self._float("local_signal_interval_seconds", 8 * 3600.0))
        with self._lock:
            last = float(self._state.lane_last_started.get("vietnam_local_signal", 0.0) or 0.0)
        if last <= 0.0:
            first_delay = max(300.0, self._float("first_local_signal_delay_seconds", 1800.0))
            if now - self._started_at < first_delay:
                return None
        if now - last < interval:
            return None
        location = self._text("local_signal_scope", "Việt Nam") or "Việt Nam"
        topic = (
            f"Tìm đúng một chuyện mới đáng chú ý liên quan {location} trong 24 giờ gần nhất mà Eris có thể "
            "dùng để chủ động bắt chuyện, trêu nhẹ hoặc chia sẻ vì thật sự thú vị. "
            "Ưu tiên công nghệ, AI, game, sự kiện lạ hoặc chuyện đang được bàn luận; bỏ qua tin ngẫu nhiên không có góc nói chuyện. "
            "Có thể chọn tin giải trí/lá cải đang lan truyền, nhưng phải gắn nhãn nguồn yếu hoặc chưa kiểm chứng; "
            "nêu nguồn và mức chắc chắn, không biến lời đồn thành fact."
        )
        return DiscoveryPlan(
            lane="vietnam_local_signal",
            topic=topic,
            needs_external=True,
            freshness_required=True,
            verification_required=True,
            eris_interest=0.72,
            user_relevance=0.68,
            novelty=0.70,
            importance=0.66,
            confidence=0.82,
            reason="Eris muốn kiếm một chuyện đủ hay để tự mở lời với User, không phải lấp im lặng bằng tin ngẫu nhiên.",
        )

    def _start_search(self, plan: DiscoveryPlan, *, now: float) -> None:
        daemon = getattr(self.host, "tutor_daemon", None)
        search_now = getattr(daemon, "search_now", None) if daemon is not None else None
        if not callable(search_now):
            self._log("debug", "[discovery] tutor search unavailable")
            return
        topic_key = plan.topic.casefold()
        self._cancel_event.clear()
        with self._lock:
            failed_at = float(self._state.failed_topics.get(topic_key, 0.0) or 0.0)
            if failed_at > 0.0 and now - failed_at < 3 * 3600.0:
                return
            lane_started = dict(self._state.lane_last_started)
            previous_lane_started = float(lane_started.get(plan.lane, 0.0) or 0.0)
            lane_started[plan.lane] = now
            activity = ActivityRecord(
                activity_id=f"activity-{uuid.uuid4().hex[:10]}",
                kind="external_search",
                topic=plan.topic,
                status="searching",
                detail="Đang nhờ Tutor lấy dữ liệu mới và nguồn xác minh.",
                started_at=now,
                updated_at=now,
                evidence=(f"discovery_plan:{plan.lane}",),
            )
            self._state = replace(
                self._state,
                current_activity=activity,
                lane_last_started=lane_started,
                version=self._state.version + 1,
                updated_at=now,
            )
            self._persist_locked()

        def job() -> None:
            answer: str | None = None
            try:
                if not self._cancel_event.is_set():
                    try:
                        daemon._eris_discovery_meta = {
                            "plan": plan.to_dict(),
                            "activity_id": activity_id,
                            "requested_at": started,
                        }
                    except (AttributeError, TypeError) as exc:
                        self._log("debug", "[discovery] metadata bind failed: %s", exc)
                    answer = search_now(
                        plan.topic,
                        context=(
                            "Đây là discovery riêng của Eris. Chỉ trả dữ kiện mới có nguồn; "
                            "không viết lời thoại, không giả cảm xúc, không suy đoán nhiệt độ phòng từ thời tiết ngoài trời."
                        ),
                        now=time.time(),
                        cancel_event=self._cancel_event,
                    )
                if self._cancel_event.is_set():
                    answer = None
            except Exception as exc:  # noqa: BLE001
                self._log("warning", "[discovery] tutor search failed: %s", exc)
            finally:
                # Meta chỉ có hiệu lực cho đúng job này. Nếu để nó nằm lại trên
                # TutorDaemon, một search trực tiếp do User yêu cầu sau đó có thể bị
                # archive nhận vơ thành discovery của Eris. Khá người, nhưng sai.
                try:
                    meta = _mapping(getattr(daemon, "_eris_discovery_meta", {}))
                    if _compact(meta.get("activity_id", ""), 100) == activity_id:
                        daemon._eris_discovery_meta = {}
                except (AttributeError, TypeError) as exc:
                    self._log("debug", "[discovery] metadata clear failed: %s", exc)
            finished = time.time()
            if answer:
                bundle = _mapping(getattr(daemon, "last_research_bundle", {}))
                record = self._record_from_answer(plan=plan, answer=answer, bundle=bundle, created_at=finished)
                if record.verified:
                    self._upsert_record(record, last_tutor_answer_ts=float(getattr(daemon, "last_answer_ts", finished) or finished))
                    activity = ActivityRecord(
                        activity_id=activity_id,
                        kind="external_search",
                        topic=plan.topic,
                        status="done",
                        detail=f"Đã tạo discovery {record.discovery_id}; đang chờ một khoảng trống phù hợp để kể.",
                        started_at=started,
                        updated_at=finished,
                        evidence=(f"discovery:{record.discovery_id}",),
                    )
                    self._replace_state(current_activity=activity)
                    return
                answer = None

            cancelled = self._cancel_event.is_set()
            with self._lock:
                failures = dict(self._state.failed_topics)
                lane_started = dict(self._state.lane_last_started)
                if cancelled:
                    # Preemption không phải thất bại và không được tiêu luôn lịch
                    # chạy của lane. Sau khi User rời chat, scheduler có thể thử
                    # lại theo idle/planning cooldown bình thường.
                    if previous_lane_started > 0.0:
                        lane_started[plan.lane] = previous_lane_started
                    else:
                        lane_started.pop(plan.lane, None)
                else:
                    failures[topic_key] = finished
                activity = ActivityRecord(
                    activity_id=activity_id,
                    kind="external_search",
                    topic=plan.topic,
                    status="cancelled" if cancelled else "failed",
                    detail=(
                        "Discovery đã nhường chỗ cho cuộc trò chuyện của User."
                        if cancelled
                        else "Tutor không trả về artifact đủ nguồn; không có gì được phép đem đi khoe."
                    ),
                    started_at=started,
                    updated_at=finished,
                    evidence=(("preempted_by_chat",) if cancelled else ("tutor_result:empty_or_weak",)),
                )
                self._state = replace(
                    self._state,
                    current_activity=activity,
                    failed_topics=failures,
                    lane_last_started=lane_started,
                    version=self._state.version + 1,
                    updated_at=finished,
                )
                self._persist_locked()

        with self._lock:
            activity_id = self._state.current_activity.activity_id
            started = self._state.current_activity.started_at
        self._job_thread = threading.Thread(target=job, name="eris-discovery-search", daemon=True)
        self._job_thread.start()

    def preempt_for_chat(self, *, reason: str = "user_chat_started") -> bool:
        """Nhường Tutor/discovery nền ngay khi User bắt đầu nói.

        Search trực tiếp do User yêu cầu không mang ``_eris_discovery_meta`` nên
        không bị đụng. Chỉ job do DiscoveryRuntime tự mở mới bị preempt.
        """

        thread = self._job_thread
        if thread is None or not thread.is_alive():
            return False
        self._cancel_event.set()
        daemon = getattr(self.host, "tutor_daemon", None)
        cancel = getattr(daemon, "cancel_pending", None)
        if callable(cancel):
            try:
                cancel(reason=reason)
            except Exception as exc:
                self._log("warning", "[discovery] cancel failed reason=%s error=%s", reason, exc)
        self._log("info", "[discovery] preempted reason=%s", reason)
        return True

    # ------------------------------------------------------------------
    # Planner/public bridge
    # ------------------------------------------------------------------
    def ready_records(self, *, now: float | None = None, limit: int = 4) -> tuple[DiscoveryRecord, ...]:
        now = float(now if now is not None else time.time())
        share_cooldown = max(600.0, self._float("share_cooldown_seconds", 90 * 60.0))
        with self._lock:
            if self._state.last_shared_at > 0.0 and now - self._state.last_shared_at < share_cooldown:
                return ()
            candidates = [
                item for item in self._state.records
                if item.verified and not item.shared and item.share_readiness >= 0.58
            ]
        candidates.sort(
            key=lambda item: (
                item.share_readiness,
                item.eris_interest,
                item.user_relevance,
                item.created_at,
            ),
            reverse=True,
        )
        return tuple(candidates[: max(1, int(limit or 1))])

    def planner_context(self, telemetry: Mapping[str, Any] | None = None, *, now: float | None = None) -> dict[str, Any]:
        now = float(now if now is not None else time.time())
        ready = self.ready_records(now=now, limit=3)
        with self._lock:
            activity = self._state.current_activity
            total = len(self._state.records)
        environment = self.environment_signal(telemetry or {})
        return {
            "schema": DISCOVERY_SCHEMA,
            "ready_to_share": [item.prompt_payload() for item in ready],
            "current_activity": activity.to_dict() if activity.active or activity.status in {"done", "failed"} else {},
            "environment": environment,
            "archive_count": total,
            "rules": {
                "share_at_most_one": True,
                "do_not_invent_activity": True,
                "room_temperature_requires_sensor": True,
                "tutor_is_for_fresh_local_or_verified_information": True,
            },
        }

    def bind_plan(self, plan: Any) -> tuple[Any, str, str]:
        """Gắn artifact thật vào plan đã chọn.

        Trả về (plan mới, grounding, discovery_id). Không sửa strategy planner nếu
        planner không chọn semantic turn_kind tương ứng.
        """
        semantics = getattr(plan, "semantics", None)
        turn_kind = str(getattr(semantics, "turn_kind", "other") or "other")
        speech_act = str(getattr(semantics, "speech_act", "unspecified") or "unspecified")
        if turn_kind == "discovery":
            ready = self.ready_records(limit=1)
            if not ready:
                return plan, "", ""
            record = ready[0]
            evidence_refs = tuple(dict.fromkeys((*tuple(getattr(plan, "evidence_refs", ()) or ()), f"discovery:{record.discovery_id}")))
            topic = (
                f"Kể ngắn cho User discovery '{record.topic}'. Chỉ dùng verified_summary và evidence; "
                "thêm nhận xét riêng tự nhiên, không đọc như bản tin."
            )
            try:
                plan = replace(plan, topic=topic, evidence_refs=evidence_refs)
            except (TypeError, ValueError) as exc:
                self._log("debug", "[discovery] plan enrichment failed: %s", exc)
            grounding = "Verified discovery artifact:\n" + json.dumps(record.prompt_payload(), ensure_ascii=False)
            return plan, grounding, record.discovery_id
        if turn_kind == "activity_event" and speech_act == "update_own_activity":
            with self._lock:
                activity = self._state.current_activity
            if not activity.activity_id or activity.status == "idle":
                return plan, "", ""
            grounding = "Verified current activity:\n" + json.dumps(activity.to_dict(), ensure_ascii=False)
            try:
                plan = replace(
                    plan,
                    topic=(
                        "Kể một câu ngắn về activity thật hiện tại. Không biến telemetry thành thơ, "
                        "không nói đang làm nếu status đã failed/done trừ khi nói đúng thì quá khứ."
                    ),
                    evidence_refs=tuple(dict.fromkeys((*tuple(getattr(plan, "evidence_refs", ()) or ()), f"activity:{activity.activity_id}"))),
                )
            except (TypeError, ValueError) as exc:
                self._log("debug", "[discovery] activity plan enrichment failed: %s", exc)
            return plan, grounding, ""
        return plan, "", ""

    def mark_spoken(self, discovery_id: str, text: str, *, now: float | None = None) -> None:
        discovery_id = _compact(discovery_id, 100)
        if not discovery_id:
            return
        now = float(now if now is not None else time.time())
        with self._lock:
            changed = False
            records: list[DiscoveryRecord] = []
            for item in self._state.records:
                if item.discovery_id == discovery_id and not item.shared:
                    item = replace(item, shared=True, shared_at=now, shared_text=_compact(text, 520))
                    changed = True
                records.append(item)
            if not changed:
                return
            self._state = replace(
                self._state,
                records=tuple(records),
                last_shared_at=now,
                version=self._state.version + 1,
                updated_at=now,
            )
            self._persist_locked()

    def panel_view(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            ready_records = self.ready_records(limit=4)
            shared_count = sum(1 for item in state.records if item.shared)
            verified_count = sum(1 for item in state.records if item.verified)
            return {
                "schema": DISCOVERY_SCHEMA,
                "version": state.version,
                "archive_count": len(state.records),
                "shared_count": shared_count,
                "verified_count": verified_count,
                "unshared_count": max(0, len(state.records) - shared_count),
                "ready_count": len(ready_records),
                "ready_to_share": [item.panel_payload() for item in ready_records],
                "current_activity": state.current_activity.to_dict(),
                "last_shared_at": state.last_shared_at,
                "lane_last_started": dict(state.lane_last_started),
                "recent": [item.panel_payload() for item in state.records[-8:]],
                "authority": {
                    "facts": "tutor_or_runtime_evidence",
                    "selection_and_voice": "main_model",
                    "identity_learning": "post_user_reaction_via_identity_kernel",
                },
            }

    # ------------------------------------------------------------------
    # Environment truth
    # ------------------------------------------------------------------
    @staticmethod
    def _room_temperature(telemetry: Mapping[str, Any]) -> float | None:
        for key in ("room_temperature_c", "ambient_temperature_c", "indoor_temperature_c"):
            value = telemetry.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if -20.0 <= number <= 70.0:
                return number
        return None

    def environment_signal(self, telemetry: Mapping[str, Any]) -> dict[str, Any]:
        room = self._room_temperature(telemetry)
        configured_location = self._text("user_location", "")
        result: dict[str, Any] = {
            "configured_location": {
                "available": bool(configured_location),
                "value": configured_location,
                "source": "runtime_setting" if configured_location else "missing",
                "precision": "user_configured_approximate",
            },
            "room_temperature": {
                "available": room is not None,
                "value_c": room,
                "source": "runtime_sensor" if room is not None else "missing",
            }
        }
        for key in ("gpu_temperature_c", "active_app", "active_window_title", "idle_seconds"):
            if key in telemetry and telemetry.get(key) not in (None, ""):
                result[key] = telemetry.get(key)
        return result

    def reset(self) -> None:
        """Xóa archive/activity theo lệnh panel debug, giữ file state hợp lệ."""
        with self._lock:
            self._state = DiscoveryState(version=self._state.version + 1, updated_at=time.time())
            self._last_planned = DiscoveryPlan()
            self._last_planning_attempt_at = 0.0
            self._started_at = time.time()
            self._cancel_event.clear()
            self._persist_locked()

    def shutdown(self) -> None:
        self._cancel_event.set()
        daemon = getattr(self.host, "tutor_daemon", None)
        cancel = getattr(daemon, "cancel_pending", None)
        if callable(cancel):
            try:
                cancel(reason="discovery_shutdown")
            except Exception as exc:
                self._log("warning", "[discovery] shutdown cancel failed: %s", exc)
        thread = self._job_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.25)

    def _log(self, level: str, message: str, *args: Any) -> None:
        safe_log(self.logger, level, message, *args)


__all__ = [
    "DISCOVERY_LANES",
    "DISCOVERY_SCHEMA",
    "ActivityRecord",
    "DiscoveryPlan",
    "DiscoveryRecord",
    "DiscoveryRuntime",
    "DiscoveryState",
    "ExternalNeedGate",
]
