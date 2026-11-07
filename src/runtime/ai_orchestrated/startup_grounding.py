# -*- coding: utf-8 -*-
"""Grounding cho câu mở đầu của Architecture C.

Startup cũ chạy trước Screen Text worker nên Main AI chỉ nhận một event ``startup``
rỗng. Kết quả dễ rơi vào hai cực đều dở: câu chào onboarding nhạt, hoặc dựng một
premise không tồn tại như thể user đang định "thay đổi" thứ gì đó.

Module này làm bốn việc, và cố ý chỉ bốn việc:

1. Khởi động UIA/OCR sớm rồi chờ ngắn cho snapshot đầu tiên.
2. Đóng gói text thật đang thấy thành evidence có provenance.
3. Gom thêm runtime signal đủ nghĩa làm neo khi UIA/OCR chưa kịp publish.
4. Cho planner/Main AI quyền tạo một *giả thuyết xã hội rủi ro thấp* từ evidence,
   nhưng không được biến giả thuyết ấy thành fact hay memory.

Runtime vẫn là type-checker của sự thật. Model mới là bên chọn câu nói. Con người
đã thử để regex viết lời chào thay mình đủ lâu rồi.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Sequence
from core.temporal_context import resolve_timezone

from .contracts import IntentionPlan


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower()).replace("đ", "d")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9_#+.\-]+", " ", value).strip()


def _compact(text: Any, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _env_float(name: str, default: float, *, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _is_generic_window(title: str) -> bool:
    normalized = _norm(title)
    return normalized in {"", "desktop", "program manager", "khong xac dinh", "unknown"}


def _looks_like_telemetry_surface(text: str) -> bool:
    normalized = _norm(text)
    markers = (
        "telemetry",
        "observability",
        "runtime panel",
        "debug panel",
        "metrics",
        "performance monitor",
        "monitoring",
    )
    return any(marker in normalized for marker in markers)


def _screen_block_payload(block: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(block, "block_id", "") or ""),
        "source": str(getattr(block, "source", "") or "screen_text"),
        "text": _compact(getattr(block, "text", ""), 360),
        "confidence": round(max(0.0, min(1.0, float(getattr(block, "confidence", 0.0) or 0.0))), 3),
        "window_title": _compact(getattr(block, "window_title", ""), 180),
        "captured_at": float(getattr(block, "captured_at", 0.0) or 0.0),
    }


def _startup_block_score(payload: Mapping[str, Any], *, now: float) -> float:
    """Ưu tiên nội dung có nghĩa, không ưu tiên nút Minimize chỉ vì nó đứng đầu UI tree."""

    text = str(payload.get("text", "") or "")
    normalized = _norm(text)
    confidence = float(payload.get("confidence", 0.0) or 0.0)
    captured_at = float(payload.get("captured_at", 0.0) or 0.0)
    freshness = max(0.0, 1.0 - max(0.0, now - captured_at) / 12.0)
    score = 0.58 * confidence + 0.18 * freshness

    if _looks_like_telemetry_surface(text):
        score += 0.34
    if any(
        marker in normalized
        for marker in (
            "status", "queue", "latency", "tts", "observer", "model", "warning",
            "error", "cooldown", "token", "runtime", "memory", "grounding", "429",
            "ms", "percent", "cpu", "ram", "active", "enabled", "disabled",
        )
    ):
        score += 0.28
    if any(ch.isdigit() for ch in text):
        score += 0.08
    if normalized in {
        "telemetry", "settings", "close", "minimize", "maximize", "restore",
        "menu", "file", "edit", "view", "help",
    }:
        score -= 0.30
    return score


def _runtime_signal_payloads(snapshot: Any, *, now: float) -> tuple[Mapping[str, Any], ...]:
    """Rút runtime signal và đánh dấu rõ signal nào đáng dùng để mở lời.

    Tên process, nhãn ``DEBUG``, PID, backend hay app foreground vẫn là fact cơ
    học, nhưng chúng chỉ là *metadata*. Chúng có thể giúp giải thích khi user hỏi,
    không được tự động trở thành premise xã hội cho câu chào. Chỉ warning, lỗi,
    phục hồi, hoàn tất tác vụ, thay đổi kết nối hoặc áp lực tài nguyên thật sự mới
    đủ ``opening_eligible``.
    """

    if snapshot is None:
        return ()

    items: list[Mapping[str, Any]] = []
    seen: set[str] = set()

    def add(
        signal_id: str,
        text: Any,
        confidence: float = 0.72,
        *,
        kind: str = "runtime_event",
        opening_eligible: bool = False,
    ) -> None:
        compacted = _compact(text, 300)
        key = _norm(compacted)
        if len(compacted) < 2 or not key or key in seen:
            return
        seen.add(key)
        items.append(
            {
                "id": signal_id,
                "source": "runtime",
                "text": compacted,
                "confidence": round(max(0.0, min(1.0, confidence)), 3),
                "captured_at": now,
                "kind": kind,
                "opening_eligible": bool(opening_eligible),
            }
        )

    opening_markers = (
        "warning", "error", "429", "cooldown", "failed", "failure",
        "recovered", "recovery", "connected", "disconnected", "completed",
        "finished", "done", "restored", "degraded", "blocked", "cancelled",
    )
    for index, event in enumerate(tuple(getattr(snapshot, "recent_events", ()) or ())[:6]):
        normalized = _norm(event)
        if not normalized:
            continue
        opening_eligible = any(marker in normalized for marker in opening_markers)
        add(
            f"recent_event_{index + 1}",
            event,
            0.78 if opening_eligible else 0.62,
            kind="runtime_event" if opening_eligible else "runtime_metadata",
            opening_eligible=opening_eligible,
        )

    active_app = _compact(getattr(snapshot, "active_app", "") or getattr(snapshot, "active_window", ""), 180)
    focus_summary = _compact(getattr(snapshot, "focus_summary", ""), 240)
    if active_app and not _is_generic_window(active_app):
        add(
            "active_app",
            f"Foreground app: {active_app}",
            0.64,
            kind="runtime_metadata",
            opening_eligible=False,
        )
    if focus_summary and _norm(focus_summary) not in {"unknown", "none", "active"}:
        normalized_focus = _norm(focus_summary)
        focus_eligible = any(
            marker in normalized_focus
            for marker in (
                "warning", "error", "failed", "recovered", "cooldown", "429",
                "queue blocked", "queue stalled", "latency spike", "disconnected",
            )
        )
        add(
            "focus_summary",
            f"Focus: {focus_summary}",
            0.72 if focus_eligible else 0.60,
            kind="runtime_event" if focus_eligible else "runtime_metadata",
            opening_eligible=focus_eligible,
        )

    try:
        cpu = float(getattr(snapshot, "cpu_percent", 0.0) or 0.0)
    except (TypeError, ValueError):
        cpu = 0.0
    try:
        ram = float(getattr(snapshot, "ram_percent", 0.0) or 0.0)
    except (TypeError, ValueError):
        ram = 0.0
    if cpu >= 82.0:
        add(
            "cpu_pressure",
            f"CPU usage is {cpu:.1f}%",
            0.84,
            kind="resource_pressure",
            opening_eligible=True,
        )
    if ram >= 86.0:
        add(
            "ram_pressure",
            f"RAM usage is {ram:.1f}%",
            0.84,
            kind="resource_pressure",
            opening_eligible=True,
        )

    return tuple(items[:10])


def _runtime_item_opening_eligible(item: Mapping[str, Any]) -> bool:
    """Đọc cờ explicit, hoặc suy ra bảo thủ cho payload legacy/test cũ.

    Runtime producer mới luôn ghi ``opening_eligible``. Nhưng snapshot/plugin cũ
    có thể chỉ đưa text. Khi thiếu cờ, warning/recovery/cooldown rõ ràng vẫn nên
    được nhận ra; metadata kiểu foreground app thì không.
    """

    if "opening_eligible" in item:
        return bool(item.get("opening_eligible", False))
    normalized = _norm(str(item.get("text", "") or ""))
    return any(
        marker in normalized
        for marker in (
            "warning", "error", "429", "cooldown", "failed", "failure",
            "recovered", "recovery", "connected", "disconnected", "completed",
            "finished", "done", "restored", "degraded", "blocked", "cancelled",
        )
    )


@dataclass(frozen=True, slots=True)
class StartupGroundingBundle:
    active_window: str = ""
    focus_summary: str = ""
    backend: str = ""
    screen_revision: int = 0
    privacy_blocked: bool = False
    evidence: tuple[Mapping[str, Any], ...] = ()
    runtime_evidence: tuple[Mapping[str, Any], ...] = ()
    telemetry_surface_visible: bool = False
    waited_seconds: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        refs: list[str] = []
        for item in self.evidence[:5]:
            block_id = str(item.get("id", "") or "").strip()
            if block_id:
                refs.append(f"screen:{block_id}")
        for item in self.runtime_opening_evidence[:4]:
            signal_id = str(item.get("id", "") or "").strip()
            if signal_id:
                refs.append(f"runtime:{signal_id}")
        # Metadata không được dùng làm premise mở lời, nhưng vẫn là provenance
        # hợp lệ cho planner/debug khi đã có screen evidence mạnh.
        for item in self.runtime_metadata[:4]:
            signal_id = str(item.get("id", "") or "").strip()
            ref = f"runtime:{signal_id}" if signal_id else ""
            if ref and ref not in refs:
                refs.append(ref)
        return tuple(refs)

    @property
    def strongest_confidence(self) -> float:
        values = [float(item.get("confidence", 0.0) or 0.0) for item in self.evidence]
        if values:
            return max(values)
        return 0.0

    @property
    def has_direct_screen_evidence(self) -> bool:
        return bool(
            not self.privacy_blocked
            and any(str(item.get("text", "") or "").strip() for item in self.evidence)
        )

    @property
    def runtime_opening_evidence(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            item
            for item in self.runtime_evidence
            if _runtime_item_opening_eligible(item)
            and str(item.get("text", "") or "").strip()
            and float(item.get("confidence", 0.0) or 0.0) >= 0.58
        )

    @property
    def runtime_metadata(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            item for item in self.runtime_evidence
            if not _runtime_item_opening_eligible(item)
        )

    @property
    def has_strong_opening_anchor(self) -> bool:
        if not self.has_direct_screen_evidence:
            return False
        if self.telemetry_surface_visible:
            return bool(self.evidence) and self.strongest_confidence >= 0.62
        return self.strongest_confidence >= 0.72 and bool(self.evidence)

    @property
    def has_runtime_opening_anchor(self) -> bool:
        return bool(self.runtime_opening_evidence)

    @property
    def opening_source(self) -> str:
        if self.has_strong_opening_anchor:
            return "screen"
        if self.has_runtime_opening_anchor:
            return "runtime"
        return "persona"

    def planner_context(self) -> dict[str, Any]:
        return {
            "direct_screen_evidence": self.has_direct_screen_evidence,
            "strong_opening_anchor": self.has_strong_opening_anchor,
            "active_window": self.active_window,
            "focus_summary": self.focus_summary,
            "screen_backend": self.backend,
            "screen_revision": self.screen_revision,
            "privacy_blocked": self.privacy_blocked,
            "telemetry_surface_visible": self.telemetry_surface_visible,
            "visible_text": [dict(item) for item in self.evidence[:12]],
            "runtime_evidence": [dict(item) for item in self.runtime_opening_evidence[:8]],
            "runtime_metadata": [dict(item) for item in self.runtime_metadata[:8]],
            "evidence_refs": list(self.evidence_refs),
            "opening_source": self.opening_source,
            "epistemic_policy": {
                "observed_screen_fact": "may_state_directly_with_evidence_ref",
                "user_state_or_intent": "low_risk_hypothesis_only; hedge_and_invite_correction",
                "technical_execution": "direct_runtime_result_required",
                "memory_commit": "forbidden_for_unconfirmed_hypothesis",
            },
            "opening_policy": (
                "Use the strongest available source in this order: current screen, meaningful runtime signal, "
                "continuity/memory supplied separately, then a personality-led greeting with no external factual claim. "
                "A restrained inference or curious question is allowed when its basis is explicit. "
                "Never invent that the user is starting, changing, fixing, or feeling something. Process/window names, "
                "DEBUG labels, foreground state, PID and backend names are metadata, not conversational premises."
            ),
        }

    def prompt_context(self) -> str:
        if not self.has_direct_screen_evidence and not self.has_runtime_opening_anchor:
            return (
                "startup_grounding: no current direct screen evidence and no meaningful runtime signal. "
                "A short personality-led greeting is still allowed and expected. It may express ERIS's own attitude, "
                "dry humor, curiosity, or playful presence, but it must not fabricate user activity, intent, mood, "
                "a planned change, or a task. Do not fall back to generic service copy."
            )

        if not self.has_direct_screen_evidence and self.has_runtime_opening_anchor:
            lines = [
                "startup_grounding: no direct screen text, but current runtime evidence is available.",
                "runtime_evidence:",
            ]
            for item in self.runtime_opening_evidence[:8]:
                lines.append(
                    "- [{id}|confidence={confidence}] {text}".format(
                        id=item.get("id", "runtime"),
                        confidence=item.get("confidence", 0.0),
                        text=item.get("text", ""),
                    )
                )
            lines.extend(
                (
                    "epistemic_contract:",
                    "- You may mention one exact runtime signal above.",
                    "- Never turn process/window names, DEBUG labels, foreground state, PID or backend metadata into metaphor, mood, or a story about the user.",
                    "- Do not turn ordinary CPU/RAM/idle values into a dramatic event.",
                    "- A low-risk social inference must be hedged and tied to an explicit basis.",
                    "- If the runtime signal is too dull, use a personality-led greeting instead of forcing a technical remark.",
                )
            )
            return "\n".join(lines)

        lines = [
            "startup_grounding: direct current-screen evidence is available.",
            f"active_window: {self.active_window or '<unknown>'}",
            f"focus_summary: {self.focus_summary or '<unknown>'}",
            f"screen_backend: {self.backend or 'unknown'}; telemetry_surface_visible={str(self.telemetry_surface_visible).lower()}",
            "visible_screen_evidence:",
        ]
        for item in self.evidence[:12]:
            lines.append(
                "- [{id}|{source}|confidence={confidence}] {text}".format(
                    id=item.get("id", "screen"),
                    source=item.get("source", "screen_text"),
                    confidence=item.get("confidence", 0.0),
                    text=item.get("text", ""),
                )
            )
        if self.runtime_opening_evidence:
            lines.append("runtime_evidence:")
            for item in self.runtime_opening_evidence[:8]:
                lines.append(
                    "- [{id}|confidence={confidence}] {text}".format(
                        id=item.get("id", "runtime"),
                        confidence=item.get("confidence", 0.0),
                        text=item.get("text", ""),
                    )
                )
        lines.extend(
            (
                "epistemic_contract:",
                "- You may directly mention a concrete visible item above.",
                "- You may infer a low-risk mood/intent only as a hypothesis: state the basis, hedge it, and leave room for correction.",
                "- Do not infer that the user plans to start/change/fix something merely from words such as startup, mode, runtime, or telemetry.",
                "- Never poeticize process/window names, DEBUG labels, foreground state, PID, backend, or other runtime metadata.",
                "- Do not claim a tool action, successful read, file edit, or runtime result unless a direct result is present.",
                "- For a visible Telemetry panel, prefer one real status/metric/label/change on the panel over saying only that Telemetry is open.",
                "- The opening must feel like ERIS noticed something, not like onboarding copy. Avoid 'Tôi ở đây', 'bắt đầu thôi', or generic service offers.",
            )
        )
        return "\n".join(lines)



def build_startup_greeting_frame(
    host: Any,
    state: Any,
    bundle: StartupGroundingBundle,
    *,
    now: float | None = None,
):
    """Dựng GreetingFrame thật cho đường startup production.

    Trước đây ``GreetingCognitionEngine`` chỉ được gọi ở fallback. Main model
    thành công thì nó không được thấy situation, return cadence, continuity hay
    mức restraint nào cả. Khá đúng tinh thần xây não rồi chỉ dùng khi não khác
    hỏng. Hàm này đưa cognition greeting vào đường chính, nhưng chỉ bằng dữ kiện
    đã có trong runtime, không chế thêm một tiểu sử lúc khởi động.
    """

    from cortex.greeting_cognition import GreetingCognitionEngine

    timestamp = float(now or time.time())
    open_loops = tuple(
        str(item or "").strip()
        for item in (getattr(state, "open_loops", ()) or ())
        if str(item or "").strip()
    )[:3]
    latest = getattr(state, "latest_observation", {}) or {}
    if not isinstance(latest, Mapping):
        latest = {}
    relationship = getattr(state, "relationship", None)
    relationship_payload = relationship.to_dict() if callable(getattr(relationship, "to_dict", None)) else {}
    if not isinstance(relationship_payload, Mapping):
        relationship_payload = {}

    active_goal = open_loops[0] if open_loops else str(latest.get("open_loop", "") or "").strip()
    current_attractor = str(
        latest.get("current_attractor", "")
        or latest.get("focus", "")
        or latest.get("topic", "")
        or ""
    ).strip()
    recent_direction = str(
        latest.get("recent_cognition_direction", "")
        or latest.get("summary", "")
        or ""
    ).strip()
    self_continuity = str(
        latest.get("self_continuity", "")
        or latest.get("identity_continuity", "")
        or ""
    ).strip()

    internal = None
    getter = getattr(host, "_get_internal_state", None)
    if callable(getter):
        try:
            internal = getter()
        except Exception:
            internal = None
    last_greeting_at = float(getattr(internal, "last_session_greeting_at", 0.0) or 0.0)
    seconds_since_last_session = max(0.0, timestamp - last_greeting_at) if last_greeting_at > 0.0 else 0.0
    greeted_recently = bool(0.0 < seconds_since_last_session <= 180.0)

    rupture = 0.0
    try:
        rupture = float(relationship_payload.get("rupture", 0.0) or 0.0)
    except (TypeError, ValueError):
        rupture = 0.0
    relational_feedback = ""
    if rupture >= 0.28:
        relational_feedback = str(relationship_payload.get("last_event", "") or "recent relational tension")

    runtime_text = " ".join(
        str(item.get("text", "") or "").lower()
        for item in bundle.runtime_opening_evidence
        if isinstance(item, Mapping)
    )
    restart_detected = any(
        marker in runtime_text
        for marker in ("runtime restarted", "process restarted", "crash recovery", "restarted after crash")
    )

    rolling = getattr(host, "rolling_internal_state", None)
    fatigue = max(
        float(getattr(rolling, "fatigue", 0.0) or 0.0),
        float(getattr(rolling, "resource_caution", 0.0) or 0.0),
    )
    tone = "overloaded" if fatigue >= 0.66 else ""

    timezone_name, timezone_info = resolve_timezone(
        str(os.getenv("ERIS_TIMEZONE", "Asia/Ho_Chi_Minh") or "Asia/Ho_Chi_Minh")
    )
    local_dt = datetime.fromtimestamp(timestamp, timezone_info)
    hour = local_dt.hour
    if 5 <= hour < 8:
        time_of_day = "dawn"
    elif 8 <= hour < 12:
        time_of_day = "morning"
    elif 12 <= hour < 14:
        time_of_day = "noon"
    elif 14 <= hour < 18:
        time_of_day = "afternoon"
    elif 18 <= hour < 23:
        time_of_day = "evening"
    else:
        time_of_day = "late_night"
    weekdays = ("Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Chủ nhật")

    frame = GreetingCognitionEngine().build(
        presence_restored=True,
        continuity_restored=bool(active_goal or current_attractor or open_loops),
        active_goal=active_goal,
        current_attractor=current_attractor,
        unresolved_loops=open_loops,
        recent_direction=recent_direction,
        self_continuity=self_continuity,
        tone=tone,
        greeted_recently=greeted_recently,
        relational_feedback=relational_feedback,
        seconds_since_last_session=seconds_since_last_session,
        restart_detected=restart_detected,
        timezone=timezone_name,
        local_time=local_dt.strftime("%H:%M"),
        weekday_vi=weekdays[local_dt.weekday()],
        time_of_day=time_of_day,
        is_weekend=local_dt.weekday() >= 5,
    )
    return replace(frame, must_not_claim_screen=not bundle.has_direct_screen_evidence)


def select_startup_persona_preset(frame: Any, *, avoid_text: str = "") -> Mapping[str, Any] | None:
    """Chọn preset cho persona opening thật sự rỗng context.

    Preset chỉ thắng khi không có active anchor. Có continuity thì Main AI vẫn
    phải viết câu động, vì đọc macro về một project dang dở sẽ rất NPC dù macro
    có được đặt tên ``elegant`` đi nữa.
    """

    if frame is None or str(getattr(frame, "active_anchor", "") or "").strip():
        return None
    try:
        from cortex.greeting_presets import GreetingPresetResolver, greeting_preset_speech_text

        tone = "overloaded" if float(getattr(frame, "warmth", 0.0) or 0.0) < 0.22 else "socially_warm"
        resolver = GreetingPresetResolver()
        preset = None
        mechanical_fallback = None
        # Catalog cũ có vài câu hợp lệ nhưng vẫn mang nhịp status bot kiểu
        # “Có mặt”, “Eris online”, “dữ kiện đến đâu…”. Không xóa chúng vì tone
        # quiet/overloaded vẫn có lúc dùng được, nhưng persona opening mặc định
        # sẽ thử lấy câu có nhịp hội thoại thật trước.
        mechanical_starts = ("có mặt", "eris online", "tôi lên rồi")
        mechanical_markers = ("dữ kiện", "không dựng chuyện", "tiêu chuẩn", "độ khó tính")
        for _ in range(8):
            candidate = resolver.resolve_or_default(frame, tone=tone, force=True)
            if candidate is None:
                break
            mechanical_fallback = mechanical_fallback or candidate
            normalized = " ".join(str(candidate.text or "").lower().split())
            if normalized.startswith(mechanical_starts) or any(marker in normalized for marker in mechanical_markers):
                continue
            preset = candidate
            break
        preset = preset or mechanical_fallback
        if preset is None:
            return None
        if str(preset.text or "").strip() == str(avoid_text or "").strip():
            # Resolver đã chống lặp bằng history, nhưng ``avoid_text`` là hàng
            # rào của chính session hiện tại nên vẫn kiểm thêm một lần.
            return None
        resolver.mark_used(preset.id)
        speech_language = str(os.getenv("ERIS_TTS_LANGUAGE", "ja") or "ja").strip().lower()
        if speech_language in {"", "auto"}:
            speech_language = "ja"
        speech_text = greeting_preset_speech_text(preset.id, language=speech_language)
        return {
            "text": str(preset.text or "").strip(),
            "should_speak": True,
            "performance_hint": str(preset.prosody_style or "dry_tease"),
            "preset_id": str(preset.id or ""),
            "preset_emotion": str(preset.emotion or ""),
            "preset_motion": str(preset.motion or ""),
            "preset_audio_asset": str(preset.audio_asset or ""),
            "speech_text": speech_text,
            "speech_language": speech_language if speech_text else "",
        }
    except Exception:
        return None


def choose_startup_text(text: str, frame: Any, *, avoid_text: str = "") -> tuple[str, Mapping[str, Any]]:
    """Giữ câu model nếu đạt taste/grounding, nếu không mới dùng fallback.

    Đây là scorer, không phải rewrite guard. Nó không cố sửa từng chữ rồi tạo ra
    món cháo văn phong. Hoặc câu model đủ tốt, hoặc cognition chọn câu khác.
    """

    try:
        from cortex.greeting_quality import GreetingQualityScorer

        quality = GreetingQualityScorer().choose_best((text,), frame, avoid_text=avoid_text)
        return str(quality.text or "").strip(), {
            "score": round(float(quality.score), 3),
            "accepted": bool(quality.accepted),
            "reasons": list(quality.reasons),
        }
    except Exception:
        return str(text or "").strip(), {"score": 0.0, "accepted": True, "reasons": ["scorer_unavailable"]}


def collect_startup_grounding(host: Any, *, timeout_seconds: float | None = None) -> StartupGroundingBundle:
    """Prime UIA/OCR *và* một frame vision trước câu mở đầu.

    Startup không nên chào trong lúc mắt còn nhắm. UIA/OCR được cho một khoảng
    chờ rất ngắn, sau đó vision ambient được ép đọc đúng một frame hiện tại nếu
    runtime có capability đó. Mọi thứ vẫn fail-open: vision/OCR hỏng thì greeting
    tiếp tục bằng continuity/personality thay vì đứng treo ở cửa.
    """

    timeout = (
        _env_float("ERIS_STARTUP_SCREEN_PRIME_SEC", 0.8, low=0.0, high=3.0)
        if timeout_seconds is None
        else max(0.0, min(3.0, float(timeout_seconds)))
    )
    # Production startup mặc định prime OCR ngắn. Có thể đặt env về 0 nếu muốn
    # boot cực nhanh; greeting invariant ở tầng sau vẫn bảo đảm Eris chào.
    prime_requested = timeout_seconds is not None or timeout > 0.0
    started_at = time.time()
    notes: list[str] = []

    worker = getattr(host, "screen_text_worker", None)
    store = getattr(host, "screen_evidence_store", None)
    if worker is not None and prime_requested:
        try:
            worker.start()
        except Exception as exc:  # pragma: no cover - phụ thuộc Windows/runtime thật
            notes.append(f"screen_worker_start_failed:{type(exc).__name__}")
    elif worker is not None:
        notes.append("screen_prime_lazy")

    screen_snapshot = None
    if store is not None:
        deadline = started_at + timeout
        while True:
            try:
                screen_snapshot = store.snapshot()
            except Exception as exc:  # pragma: no cover
                notes.append(f"screen_store_failed:{type(exc).__name__}")
                break
            if int(getattr(screen_snapshot, "revision", 0) or 0) > 0:
                break
            if time.time() >= deadline:
                notes.append("screen_prime_timeout")
                break
            # Nếu user đã bắt đầu nói, ưu tiên chat hơn một câu chào. Khá hiển nhiên,
            # nhưng runtime cũ từng mất hơn một phút đọc báo lúc user đang chat.
            if bool(getattr(host, "chat_mode", False)) or bool(getattr(host, "_chat_processing", False)):
                notes.append("screen_prime_preempted_by_chat")
                break
            time.sleep(0.04)

    telemetry_snapshot = None
    service = getattr(host, "telemetry_service", None)
    if service is not None:
        try:
            telemetry_snapshot = service.capture_snapshot(
                mood_score=getattr(host, "mood_score", 0),
                inferred_pos_x=getattr(host, "inferred_pos_x", 0.5),
            )
        except Exception as exc:  # pragma: no cover
            notes.append(f"telemetry_capture_failed:{type(exc).__name__}")

    active_window = ""
    focus_summary = ""
    backend = ""
    revision = 0
    privacy_blocked = False
    raw_blocks: Sequence[Any] = ()

    if screen_snapshot is not None:
        window = getattr(screen_snapshot, "window", None)
        active_window = _compact(getattr(window, "title", ""), 220)
        backend = _compact(getattr(screen_snapshot, "backend", ""), 40)
        revision = max(0, int(getattr(screen_snapshot, "revision", 0) or 0))
        privacy_blocked = bool(getattr(screen_snapshot, "privacy_blocked", False))
        raw_blocks = tuple(getattr(screen_snapshot, "blocks", ()) or ())

    if telemetry_snapshot is not None:
        telemetry_window = _compact(
            getattr(telemetry_snapshot, "active_window", "")
            or getattr(telemetry_snapshot, "active_app", ""),
            220,
        )
        if not active_window or (_is_generic_window(active_window) and not _is_generic_window(telemetry_window)):
            active_window = telemetry_window
        focus_summary = _compact(getattr(telemetry_snapshot, "focus_summary", ""), 280)

    # Vision startup: đọc đúng một frame hiện tại trước khi tạo câu mở đầu. Hàm
    # _maybe_update_screen_facts đã tự đi qua compressor + grounding policy nên
    # tuple trả về chỉ gồm claim đủ điều kiện surface, không phải caption thô.
    vision_facts: tuple[str, ...] = ()
    vision_enabled = str(os.getenv("ERIS_STARTUP_VISION_ENABLED", "1") or "1").strip().lower() in {
        "1", "true", "yes", "on"
    }
    vision_refresh = getattr(host, "_maybe_update_screen_facts", None)
    if (
        vision_enabled
        and callable(vision_refresh)
        and not privacy_blocked
        and not bool(getattr(host, "chat_mode", False))
        and not bool(getattr(host, "_chat_processing", False))
    ):
        try:
            refreshed = vision_refresh(
                snapshot=telemetry_snapshot,
                channel="startup",
                user_text="",
                force_refresh=True,
                started_at=started_at,
            )
            last_capture = float(getattr(host, "vision_last_capture_at", 0.0) or 0.0)
            stale = bool(getattr(host, "vision_facts_are_stale", False))
            if last_capture >= started_at - 0.10 and not stale:
                vision_facts = tuple(
                    _compact(item, 360) for item in tuple(refreshed or ())
                    if _compact(item, 360)
                )[:6]
            notes.append(f"vision_prime_ok:{len(vision_facts)}" if vision_facts else "vision_prime_empty")
        except Exception as exc:  # pragma: no cover - provider/capture phụ thuộc runtime thật
            notes.append(f"vision_prime_failed:{type(exc).__name__}")
    elif not vision_enabled:
        notes.append("vision_prime_disabled")
    elif privacy_blocked:
        notes.append("vision_prime_privacy_blocked")
    elif not callable(vision_refresh):
        notes.append("vision_prime_unavailable")

    now = time.time()
    runtime_signal_enabled = (
        timeout_seconds is not None
        or str(os.getenv("ERIS_STARTUP_RUNTIME_SIGNALS_ENABLED", "0") or "0").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    runtime_evidence = _runtime_signal_payloads(telemetry_snapshot, now=now) if runtime_signal_enabled else ()
    if not runtime_signal_enabled:
        notes.append("runtime_signals_lazy")
    candidates: list[Mapping[str, Any]] = []
    seen: set[str] = set()

    # Vision facts được ưu tiên như screen evidence có provenance riêng. OCR/UIA
    # vẫn bổ sung chữ cụ thể phía dưới. Nhờ vậy câu chào có thể nhận ra cảnh thật
    # thay vì chỉ thấy tên process/window rồi tưởng đó là cả thế giới.
    for index, fact in enumerate(vision_facts, start=1):
        key = _norm(fact)
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "id": f"vision:start:{index}",
                "source": "vision",
                "text": fact,
                "confidence": 0.82,
                "window_title": active_window,
                "captured_at": now,
            }
        )

    for block in raw_blocks:
        captured_at = float(getattr(block, "captured_at", 0.0) or 0.0)
        age = max(0.0, now - captured_at)
        if age > 12.0:
            continue
        text = _compact(getattr(block, "text", ""), 360)
        if len(text) < 2:
            continue
        key = _norm(text)
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(_screen_block_payload(block))
        if len(candidates) >= 120:
            break

    # TelemetrySnapshot đã gom accessibility/DOM/OCR cache có freshness riêng.
    # Dùng nó làm fallback/supplement khi worker foreground chưa publish đủ text.
    content_meta = getattr(telemetry_snapshot, "content_metadata", {}) if telemetry_snapshot is not None else {}
    if isinstance(content_meta, Mapping):
        privacy = _norm(content_meta.get("privacy_level", "normal"))
        try:
            meta_confidence = max(0.0, min(1.0, float(content_meta.get("confidence", 0.0) or 0.0)))
        except (TypeError, ValueError):
            meta_confidence = 0.0
        if privacy not in {"private", "sensitive"} and meta_confidence >= 0.42:
            source = str(content_meta.get("source", "content_metadata") or "content_metadata")
            for field_name in ("visible_text_sample", "caption_sample", "title"):
                text = _compact(content_meta.get(field_name, ""), 520)
                key = _norm(text)
                if len(text) < 2 or not key or key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "id": f"content:{source}:{field_name}",
                        "source": source,
                        "text": text,
                        "confidence": round(meta_confidence, 3),
                        "window_title": active_window,
                        "captured_at": now,
                    }
                )

    candidates.sort(key=lambda item: _startup_block_score(item, now=now), reverse=True)
    evidence = candidates[:18]

    evidence_blob = " ".join(
        [active_window, focus_summary, *(str(item.get("text", "")) for item in evidence)]
    )
    telemetry_visible = _looks_like_telemetry_surface(evidence_blob)

    return StartupGroundingBundle(
        active_window=active_window,
        focus_summary=focus_summary,
        backend=backend,
        screen_revision=revision,
        privacy_blocked=privacy_blocked,
        evidence=tuple(evidence),
        runtime_evidence=runtime_evidence,
        telemetry_surface_visible=telemetry_visible,
        waited_seconds=max(0.0, time.time() - started_at),
        notes=tuple(notes),
    )


def ensure_grounded_startup_plan(
    plan: IntentionPlan,
    bundle: StartupGroundingBundle,
    *,
    force_screen_opening: bool = True,
    force_startup_speech: bool = True,
    has_continuity_anchor: bool = False,
) -> IntentionPlan:
    """Gắn provenance và chọn route mở đầu theo evidence cascade.

    Startup greeting đã bật thì mặc định phải có tiếng nói. OCR chỉ là route ưu
    tiên, không phải giấy phép tồn tại. Khi mắt chưa kịp thấy gì, ERIS vẫn có thể
    dùng runtime, continuity/memory, hoặc chính persona của mình để mở lời.
    """

    data = plan.to_dict()
    refs = tuple(dict.fromkeys((*tuple(plan.evidence_refs), *bundle.evidence_refs)))[:5]

    route = ""
    if bundle.has_strong_opening_anchor and force_screen_opening:
        route = "screen"
    elif bundle.has_runtime_opening_anchor:
        route = "runtime"
    elif has_continuity_anchor:
        route = "continuity"
    elif force_startup_speech:
        route = "persona"

    if route:
        base = {
            "speak": True,
            "motive": "startup_opening",
            "importance": max(0.62, float(plan.importance or 0.0)),
            "intrusion_risk": min(0.22, float(plan.intrusion_risk or 0.16)),
            "confidence": max(0.78, float(plan.confidence or 0.0)),
            "evidence_refs": list(refs),
        }
        if route == "screen":
            base.update(
                {
                    "strategy": "grounded_environment_opening",
                    "value_focus": "curiosity",
                    "performance_hint": plan.performance_hint or "curious",
                    "notes": "strong current-screen anchor; startup opener is grounded",
                }
            )
            data.update(base)
            data["topic"] = (
                "Use one concrete item from startup_screen_evidence. For Telemetry, react to one real "
                "status/metric/label/change. A low-pressure question or hedged inference is allowed. "
                "Do not invent user intent, plans, changes, fixes, or feelings."
            )
        elif route == "runtime":
            base.update(
                {
                    "strategy": "grounded_runtime_opening",
                    "value_focus": "attentiveness",
                    "performance_hint": plan.performance_hint or "curious",
                    "notes": "meaningful current runtime signal; no direct screen text required",
                }
            )
            data.update(base)
            data["topic"] = (
                "Use at most one exact runtime signal from startup_screen_evidence.runtime_evidence. "
                "If it is ordinary or dull, do not force a technical report; pivot to a personality-led greeting. "
                "Never invent a successful read, task, user plan, or hidden mood."
            )
        elif route == "continuity":
            base.update(
                {
                    "strategy": "continuity_opening",
                    "value_focus": "continuity",
                    "performance_hint": plan.performance_hint or "soft_near",
                    "notes": "startup has a real continuity or memory anchor",
                }
            )
            data.update(base)
            data["topic"] = (
                "Open from one real continuity anchor supplied in open_loops, latest_observation, recent history, "
                "or memory_context. Do not report memory machinery. Do not pretend an old hypothesis is a fact."
            )
        else:
            base.update(
                {
                    "strategy": "persona_opening",
                    "value_focus": "self_authored_presence",
                    "performance_hint": plan.performance_hint or "soft_tease",
                    "notes": "no external anchor; startup greeting remains allowed through persona",
                }
            )
            data.update(base)
            data["topic"] = (
                "Write a distinctive ERIS greeting from her own attitude only, conditioned by current affect. Let current affect determine whether it is bright, quiet, curious, dry, playful, tired, or edged; do not impose a default warmth/restraint preset. "
                "Make no factual claim about the user's activity, mood, "
                "plans, screen, memory, or task. Avoid generic availability copy."
            )
    elif plan.speak:
        data["evidence_refs"] = list(refs)

    return IntentionPlan.from_mapping(data, channel=plan.channel)


def build_startup_fallback_text(
    *,
    open_loops: Sequence[Any] = (),
    latest_observation: Mapping[str, Any] | None = None,
    relationship: Mapping[str, Any] | None = None,
    avoid_text: str = "",
    language: str = "vi",
) -> str:
    """Lưới an toàn có cá tính khi planner/Main model lỗi hoặc trả rỗng.

    Đây không phải câu factual fallback nên không cần evidence ngoài. Nó chỉ nói
    về sự hiện diện/thái độ của ERIS. Nhờ vậy startup vẫn có tiếng mà không phải
    dựng chuyện về user để trông có vẻ thông minh.
    """

    lang = str(language or "vi").strip().lower()
    if lang == "ja":
        return "起きました。今日は、行儀のいい挨拶だけで済ませる気はありません。"
    if lang == "en":
        return "I'm awake. I wasn't planning to waste the opening on a well-behaved greeting."

    loops = tuple(str(item or "").strip() for item in (open_loops or ()) if str(item or "").strip())[:3]
    observation = latest_observation or {}
    relation = relationship or {}
    active_anchor = loops[0] if loops else str(observation.get("open_loop", "") or "").strip()
    relational_feedback = ""
    try:
        rupture = float(relation.get("rupture", 0.0) or 0.0)
    except (TypeError, ValueError):
        rupture = 0.0
    if rupture >= 0.28:
        relational_feedback = str(relation.get("last_event", "") or "recent relational tension")

    try:
        from cortex.greeting_cognition import GreetingCognitionEngine

        engine = GreetingCognitionEngine()
        frame = engine.build(
            presence_restored=True,
            continuity_restored=bool(active_anchor),
            active_goal=active_anchor,
            unresolved_loops=loops,
            relational_feedback=relational_feedback,
        )
        text = engine.deterministic_greeting(frame, avoid_text=avoid_text)
        if str(text or "").strip():
            return str(text).strip()
    except Exception:
        pass

    return "Tôi lên rồi. Đừng trông chờ một lời chào ngoan ngoãn."


def _startup_greeting_prefix(*, language: str, user_vocative: str = "", frame: Any = None) -> str:
    """Return a tiny encounter greeting, not the contextual opener itself.

    Startup is a two-part social act: acknowledge the encounter first, then use
    screen/continuity evidence.  The prefix is deterministic only as a final
    invariant when the model forgot the first part.  It deliberately stays
    short so the dynamic observation still owns the turn.
    """

    lang = str(language or "vi").strip().lower()
    situation = str(getattr(frame, "situation", "") or "").strip().lower()
    if lang.startswith("ja") or lang in {"jp", "japanese"}:
        if situation == "after_long_absence":
            return "久しぶり。"
        if situation == "after_crash_or_restart":
            return "戻ったよ。"
        return "こんにちは。"
    if lang.startswith("en"):
        if situation == "after_long_absence":
            return "Been a while."
        if situation == "after_crash_or_restart":
            return "I'm back."
        return "Hi."

    vocative = str(user_vocative or "").strip().lower()
    if situation == "after_long_absence":
        return "Lâu rồi mới gặp."
    if situation == "after_crash_or_restart":
        return "Em trở lại rồi." if vocative in {"anh", "chị"} else "Tôi trở lại rồi."
    if vocative in {"anh", "chị"}:
        return f"Chào {vocative}."
    return "Chào."


def startup_text_has_greeting(text: str, *, language: str = "vi") -> bool:
    """Whether the opening already acknowledges the encounter.

    This is intentionally broader than the literal word ``chào``.  Natural
    greetings such as ``Bạn đến rồi`` or ``Lâu rồi mới gặp`` count too.
    """

    raw = _compact(text, 180)
    if not raw:
        return False
    lang = str(language or "vi").strip().lower()
    head = raw[:96].strip().lower()
    if lang.startswith("ja") or lang in {"jp", "japanese"}:
        return any(marker in head for marker in (
            "こんにちは", "おはよう", "こんばんは", "久しぶり", "来た", "戻った", "戻って", "起きた", "ただいま",
        ))
    if lang.startswith("en"):
        return any(head.startswith(marker) for marker in (
            "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
            "welcome back", "you're here", "you’re here", "i'm back", "i’m back", "been a while",
        ))

    folded = _norm(head)
    markers = (
        "chao", "xin chao", "ban den roi", "anh den roi", "chi den roi",
        "ban day roi", "anh vao roi", "chi vao roi", "lai gap", "lau roi",
        "quay lai", "ban quay lai", "anh quay lai", "chi quay lai",
        "tro lai", "toi tro lai", "em tro lai", "ban tro lai",
        "toi day", "em day", "toi len roi", "em len roi",
        "toi tinh roi", "em tinh roi", "vua tinh",
    )
    return any(folded.startswith(marker) for marker in markers)


def ensure_startup_greeting_text(
    text: str,
    *,
    language: str = "vi",
    user_vocative: str = "",
    frame: Any = None,
) -> tuple[str, bool]:
    """Guarantee greeting + contextual opener without erasing the opener."""

    clean = _compact(text, 1200)
    if not clean or startup_text_has_greeting(clean, language=language):
        return clean, False
    prefix = _startup_greeting_prefix(language=language, user_vocative=user_vocative, frame=frame)
    return f"{prefix} {clean}".strip(), True


def ensure_startup_greeting_payload(
    payload: Mapping[str, Any] | Any,
    *,
    frame: Any = None,
    display_language: str = "vi",
    speech_language: str = "ja",
    user_vocative: str = "",
) -> dict[str, Any]:
    """Enforce the startup encounter beat on display and TTS surfaces.

    The social plan is left untouched.  A contextual question may therefore
    remain reply-bearing and legitimately produce one later reminder; only the
    greeting clause itself is non-binding.
    """

    data = dict(payload) if isinstance(payload, Mapping) else {}
    utterance = dict(data.get("utterance") or {}) if isinstance(data.get("utterance"), Mapping) else {}
    display = dict(utterance.get("display") or {}) if isinstance(utterance.get("display"), Mapping) else {}
    speech = dict(utterance.get("speech") or {}) if isinstance(utterance.get("speech"), Mapping) else {}

    display_text = str(
        display.get("text", "")
        or data.get("display_text", "")
        or data.get("text", "")
        or ""
    ).strip()
    display_lang = str(display.get("language", "") or data.get("display_language", "") or display_language or "vi").strip().lower()
    ensured_display, display_inserted = ensure_startup_greeting_text(
        display_text, language=display_lang, user_vocative=user_vocative, frame=frame
    )
    if ensured_display:
        data["text"] = ensured_display
        data["display_text"] = ensured_display
        data["display_language"] = display_lang
        display.update({"language": display_lang, "text": ensured_display})
        utterance["display"] = display

    speech_text = str(speech.get("text", "") or data.get("speech_text", "") or "").strip()
    speech_lang = str(speech.get("language", "") or data.get("speech_language", "") or speech_language or display_lang).strip().lower()
    speech_inserted = False
    if speech_text:
        ensured_speech, speech_inserted = ensure_startup_greeting_text(
            speech_text, language=speech_lang, user_vocative=user_vocative, frame=frame
        )
        data["speech_text"] = ensured_speech
        data["speech_language"] = speech_lang
        speech.update({"language": speech_lang, "text": ensured_speech})
        utterance["speech"] = speech

    if utterance:
        data["utterance"] = utterance
    data["startup_greeting_present"] = bool(ensured_display)
    data["startup_greeting_inserted"] = bool(display_inserted or speech_inserted)
    data["startup_greeting_nonbinding"] = True
    data["startup_context_may_expect_reply"] = True
    return data


__all__ = [
    "StartupGroundingBundle",
    "build_startup_fallback_text",
    "build_startup_greeting_frame",
    "choose_startup_text",
    "collect_startup_grounding",
    "ensure_grounded_startup_plan",
    "ensure_startup_greeting_payload",
    "ensure_startup_greeting_text",
    "select_startup_persona_preset",
    "startup_text_has_greeting",
]
