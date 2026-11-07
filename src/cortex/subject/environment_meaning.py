# -*- coding: utf-8 -*-
"""V60: biến telemetry có cấu trúc thành *ý nghĩa nhận thức* có provenance.

Mục tiêu của module này không phải đọc màn hình thêm lần nữa. Eris đã có sensor,
IA2/UIA, browser telemetry, TaskGraph và OCR fallback. Vấn đề trước V60 là các
mảnh đó chủ yếu trả lời "đang có gì trên desktop?" nhưng hiếm khi trả lời câu
quan trọng hơn: "chuyện vừa thấy có liên quan gì tới điều Eris/user đang làm?".

V60 giữ nguyên nguyên tắc an toàn:
- structured telemetry/accessibility được ưu tiên hơn OCR/vision;
- một tab đổi đơn lẻ KHÔNG đủ để sinh desire;
- OCR-only không được phép tự trở thành public claim;
- meaning có thể làm cognition thức dậy; user focus/busy là scene context, không
  phải quyền phủ quyết expression. Turn collision thật vẫn được chặn downstream.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from typing import Any, Mapping, Sequence


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _compact(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _refs(values: Sequence[Any], *, limit: int = 12) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in values if str(item or "").strip()))[:limit]


class EnvironmentMeaningKind(StrEnum):
    RESEARCH_AFTER_FAILURE = "research_after_failure"
    WORK_FRICTION = "work_friction"
    SELF_RELEVANT_INSPECTION = "self_relevant_inspection"


@dataclass(frozen=True, slots=True)
class EnvironmentMeaning:
    kind: EnvironmentMeaningKind
    pressure: float
    actionability: float
    confidence: float
    semantic_object: str
    summary: str
    evidence_refs: tuple[str, ...]
    source_grade: str
    public_claim_safe: bool
    semantic_key: str
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.environment_meaning.v60",
            "kind": self.kind.value,
            "pressure": round(_clamp01(self.pressure), 4),
            "actionability": round(_clamp01(self.actionability), 4),
            "confidence": round(_clamp01(self.confidence), 4),
            "semantic_object": self.semantic_object,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "source_grade": self.source_grade,
            "public_claim_safe": bool(self.public_claim_safe),
            "semantic_key": self.semantic_key,
            "revision": int(self.revision),
        }


class EnvironmentMeaningDeriver:
    """Ghép chuỗi event thành meaning; không gọi model và không tạo fact mới."""

    _VISUAL_SOURCES = {"ocr", "screen_ocr", "screen_pixel", "vision", "vlm", "screen_delta_classifier", "observer_screen_fusion", "ambient_screen_tracker"}
    _BROWSER_KINDS = {"browser_tab_activated", "browser_tab_title_changed", "navigation_changed"}
    _FAILURE_KINDS = {
        "command_failed", "test_failed", "build_failed", "error_candidate",
        "possible_error_visible", "possible_command_failure",
    }
    _SUCCESS_KINDS = {
        "command_succeeded", "test_passed", "build_succeeded", "task_completed",
        "completion_candidate",
    }
    _SELF_TOKENS = (
        "eris", "existential_desire", "initiative_control", "subject_initiative",
        "existential anchor", "anchor_system", "cortex/subject", "v58", "v59", "v60",
    )

    @staticmethod
    def _event_payload(event: Any) -> dict[str, Any]:
        return {
            "kind": str(_get(event, "kind", _get(event, "event_type", "")) or "").strip().lower(),
            "source": str(_get(event, "source", "") or "").strip().lower(),
            "detail": _compact(_get(event, "detail", _get(event, "title", "")), 260),
            "object_ref": _compact(_get(event, "object_ref", _get(event, "region_id", "")), 220),
            "confidence": _clamp01(_get(event, "confidence", 0.0)),
            "attention": max(0, min(4, int(_get(event, "attention_level", 0) or 0))),
            "privacy": str(_get(event, "privacy_level", _get(event, "privacy_scope", "normal")) or "normal").lower(),
            "evidence_refs": _refs(tuple(_get(event, "evidence_refs", ()) or ()), limit=8),
        }

    @classmethod
    def _structured(cls, payload: Mapping[str, Any]) -> bool:
        source = str(payload.get("source") or "").lower()
        privacy = str(payload.get("privacy") or "normal").lower()
        return source not in cls._VISUAL_SOURCES and privacy not in {"off", "metadata", "private", "sensitive"}

    @staticmethod
    def _semantic_key(kind: str, semantic_object: str) -> str:
        # Identity của meaning KHÔNG phụ thuộc evidence/event ID. Cùng một đối
        # tượng đứng yên trên desktop phải là cùng semantic state, không phải một
        # "sự kiện mới" chỉ vì accessibility phát ref mới ở tick sau.
        normalized = " ".join(str(semantic_object or "").casefold().split())[:320]
        digest = hashlib.sha1(f"{kind}|{normalized}".encode("utf-8", "ignore")).hexdigest()[:14]
        return f"{kind}:{digest}"

    @staticmethod
    def _revision(kind: str, refs: Sequence[str], semantic_object: str) -> int:
        raw = "|".join((kind, semantic_object, *refs))
        digest = hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:8]
        return max(1, int(digest, 16) & 0x7FFFFFFF)

    def derive(
        self,
        situation: Any,
        *,
        active_goal_text: str = "",
    ) -> tuple[EnvironmentMeaning, ...]:
        if situation is None:
            return ()

        phase = str(_get(situation, "phase", "unknown") or "unknown").strip().lower()
        activity = str(_get(situation, "activity", "unknown") or "unknown").strip().lower()
        task_hint = _compact(_get(situation, "task_hint", ""), 260)
        focused_object = _compact(_get(situation, "focused_object", ""), 260)
        last_outcome = str(_get(situation, "last_outcome", "unknown") or "unknown").strip().lower()
        situation_conf = _clamp01(_get(situation, "confidence", 0.0))
        recent_raw = tuple(_get(situation, "recent_events", ()) or ())
        events = [self._event_payload(item) for item in recent_raw]

        # V66: actionability phản ánh độ salient của event đối với Eris, không
        # phải "user có vẻ rảnh để nghe chưa". Attention level ở đây là mức quan
        # trọng của event sensor; trạng thái focus của user không tham gia score.
        event_salience = max((item["attention"] for item in events), default=0) / 4.0
        timing = _clamp01(0.58 + 0.42 * event_salience)

        structured_events = [item for item in events if self._structured(item)]
        visual_only = bool(events) and not structured_events
        browser_events = [item for item in structured_events if item["kind"] in self._BROWSER_KINDS]
        failure_events = [item for item in structured_events if item["kind"] in self._FAILURE_KINDS]
        success_events = [item for item in structured_events if item["kind"] in self._SUCCESS_KINDS]
        last_browser = browser_events[-1] if browser_events else None
        has_failure = bool(failure_events) or last_outcome in {"failed", "test_failed", "possible_problem"}
        success_after_failure = False
        if failure_events and success_events:
            # recent_events giữ thứ tự thời gian. Thành công sau failure làm giảm
            # concern thay vì Eris tiếp tục thương hại một lỗi đã chết từ lâu.
            last_fail_index = max(i for i, item in enumerate(events) if item in failure_events)
            last_success_index = max(i for i, item in enumerate(events) if item in success_events)
            success_after_failure = last_success_index > last_fail_index
        if success_after_failure:
            has_failure = False

        meanings: list[EnvironmentMeaning] = []

        def add(
            kind: EnvironmentMeaningKind,
            *,
            pressure: float,
            actionability: float,
            confidence: float,
            semantic_object: str,
            summary: str,
            refs: Sequence[str],
            source_grade: str,
            public_claim_safe: bool,
        ) -> None:
            refs_tuple = _refs(tuple(refs), limit=12)
            obj = _compact(semantic_object, 320)
            if not obj:
                return
            # Executive local semantic lane nhận diện direct evidence qua prefix
            # evidence:/grounded:/screen:/vision:. Structured telemetry có provenance
            # được chuẩn hóa thành một marker evidence:environment:<hash> nhưng vẫn
            # GIỮ refs gốc phía sau để audit biết marker đó dựa vào Chromium/terminal
            # event nào. OCR-only không được cấp marker này.
            if public_claim_safe and refs_tuple:
                marker_raw = "|".join((kind.value, obj, *refs_tuple))
                marker = "evidence:environment:" + hashlib.sha1(marker_raw.encode("utf-8", "ignore")).hexdigest()[:12]
                refs_tuple = _refs((marker, *refs_tuple), limit=12)
            meanings.append(EnvironmentMeaning(
                kind=kind,
                pressure=_clamp01(pressure),
                actionability=_clamp01(actionability),
                confidence=_clamp01(confidence),
                semantic_object=obj,
                summary=_compact(summary, 420),
                evidence_refs=refs_tuple,
                source_grade=source_grade,
                public_claim_safe=bool(public_claim_safe and refs_tuple),
                semantic_key=self._semantic_key(kind.value, obj),
                revision=self._revision(kind.value, refs_tuple, obj),
            ))

        # Pattern mạnh nhất: failure -> browser/reference trong cùng phase diagnosis.
        # Một browser_tab_activated level=1 đơn lẻ vẫn vô hại; chính quan hệ với
        # failure/task trước đó mới biến nó thành meaning đáng nghĩ.
        if has_failure and last_browser is not None and phase in {"diagnosis", "testing", "implementation"}:
            browser_object = last_browser.get("detail") or focused_object or task_hint
            refs = [*last_browser.get("evidence_refs", ())]
            for item in failure_events[-2:]:
                refs.extend(item.get("evidence_refs", ()))
            if not refs:
                refs.extend(tuple(_get(situation, "evidence_refs", ()) or ()))
            pressure = _clamp01(0.62 + 0.12 * min(2, len(failure_events)) + 0.12 * situation_conf)
            add(
                EnvironmentMeaningKind.RESEARCH_AFTER_FAILURE,
                pressure=pressure,
                actionability=pressure * (0.46 + 0.54 * timing),
                confidence=max(0.72, situation_conf, float(last_browser.get("confidence", 0.0) or 0.0)),
                semantic_object=browser_object,
                summary=(
                    f"Cùng một mạch {phase}: sau tín hiệu failure, user chuyển sang browser/reference "
                    f"để tiếp tục chẩn đoán; đối tượng hiện thấy: {browser_object}."
                ),
                refs=refs,
                source_grade="structured",
                public_claim_safe=True,
            )

        # Friction không cần browser: failure kéo dài trong task có object rõ vẫn
        # có thể tạo concern "muốn góp ý". User focus không làm concern mất quyền
        # trở thành desire; collision thật được xử lý ở expression boundary.
        if has_failure and phase == "diagnosis":
            semantic_object = task_hint if task_hint and task_hint != "unknown" else (focused_object or active_goal_text)
            refs: list[str] = []
            for item in failure_events[-3:]:
                refs.extend(item.get("evidence_refs", ()))
            if not refs:
                refs.extend(tuple(_get(situation, "evidence_refs", ()) or ()))
            pressure = _clamp01(0.52 + 0.10 * min(3, max(1, len(failure_events))) + 0.10 * situation_conf)
            add(
                EnvironmentMeaningKind.WORK_FRICTION,
                pressure=pressure,
                actionability=pressure * (0.38 + 0.62 * timing),
                confidence=max(0.62, situation_conf),
                semantic_object=semantic_object,
                summary=f"Task đang ở phase diagnosis sau failure: {semantic_object}.",
                refs=refs,
                source_grade="structured" if structured_events else "visual_fallback",
                public_claim_safe=bool(structured_events),
            )

        # Khi user đang xem chính source/khái niệm của Eris, đó có thể chạm vào
        # desire được-thấu-hiểu hoặc self-expression. Chỉ structured title/object
        # mới được phép tạo public initiative; OCR-only giữ ở private curiosity.
        # Self-relevance public-safe phải xuất hiện trong chính detail/title của
        # structured event. Không dùng một focused_object có thể đến từ OCR để
        # "mượn" provenance của một event structured không liên quan.
        structured_joined = " | ".join(
            _compact(item.get("detail", ""), 180).lower()
            for item in structured_events[-6:]
            if _compact(item.get("detail", ""), 180)
        )
        matched = next((token for token in self._SELF_TOKENS if token in structured_joined), "")
        if matched:
            refs: list[str] = []
            for item in structured_events[-4:]:
                if matched in str(item.get("detail", "")).lower():
                    refs.extend(item.get("evidence_refs", ()))
            if not refs:
                refs.extend(tuple(_get(situation, "evidence_refs", ()) or ()))
            semantic_object = focused_object or task_hint or matched
            pressure = _clamp01(0.54 + 0.12 * situation_conf)
            add(
                EnvironmentMeaningKind.SELF_RELEVANT_INSPECTION,
                pressure=pressure,
                actionability=pressure * (0.42 + 0.58 * timing),
                confidence=max(0.68, situation_conf),
                semantic_object=semantic_object,
                summary=(
                    "Structured desktop evidence cho thấy user đang ở một đối tượng liên quan trực tiếp "
                    f"đến Eris ({matched}); đây là cơ hội hiểu nhau, không phải bằng chứng về ý định của user."
                ),
                refs=refs,
                source_grade="structured",
                public_claim_safe=bool(refs),
            )
        elif visual_only:
            # OCR/vision có thể tăng private attention nhưng tuyệt đối không sinh
            # public screen claim chỉ vì vài ký tự trông giống source của Eris.
            visual_text = " | ".join(item.get("detail", "").lower() for item in events[-5:])
            matched_visual = next((token for token in self._SELF_TOKENS if token in visual_text), "")
            if matched_visual:
                semantic_object = focused_object or task_hint or matched_visual
                pressure = 0.34
                add(
                    EnvironmentMeaningKind.SELF_RELEVANT_INSPECTION,
                    pressure=pressure,
                    actionability=min(0.18, pressure * timing),
                    confidence=0.42,
                    semantic_object=semantic_object,
                    summary="OCR/vision gợi ý nội dung liên quan Eris nhưng chưa đủ provenance để nói ra ngoài.",
                    refs=tuple(_get(situation, "evidence_refs", ()) or ()),
                    source_grade="visual_fallback",
                    public_claim_safe=False,
                )

        meanings.sort(key=lambda item: (item.actionability, item.pressure, item.confidence), reverse=True)
        return tuple(meanings[:4])


__all__ = [
    "EnvironmentMeaning",
    "EnvironmentMeaningDeriver",
    "EnvironmentMeaningKind",
]
