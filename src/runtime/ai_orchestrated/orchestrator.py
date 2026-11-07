# -*- coding: utf-8 -*-
"""Đường chạy production của kiến trúc C.

File này cố ý không gọi Ni/Ti/Fe/appraisal/repair legacy trước main model. Những
thứ cần cho continuity đến từ StateReducer, những thứ cần cho sự thật đến từ
grounding/runtime. Main model không phải đi qua một phiên họp trước khi nói.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Mapping

from failure_meta import build_failure_meta

from runtime.no_fallback_policy import no_fallback_mode_enabled
from runtime.grounding_surface_gate import evaluate_grounded_surface, is_grounding_correction
from runtime.claim_domain_contract import validate_claim_manifest
from runtime.self_biography_guard import embodied_self_history_without_evidence
from runtime.cognition.turn_anchor import anchor_from_semantic_frame
from runtime.social_stance_policy import guard_subjective_stance_payload
from runtime.public_reply_context import compile_public_reply_context
from runtime.affective_voice_state import project_affective_voice_state
from runtime.reply_obligation import enforce_reply_obligation
from runtime.cognition.normalization import normalize_vi
from runtime.subject_initiative_runtime import build_initiative_runtime_context
from runtime.observability import ensure_observability
from runtime.pedagogical_contracts import TeachingDirective, TeachingReceipt, normalize_episode
from runtime.pedagogical_controller import (
    infer_user_evidence,
    select_next_action,
    start_episode as start_teaching_episode,
)
from runtime.pedagogical_plan_builder import TeachingPlanBuilder
from runtime.pedagogical_runtime import ensure_pedagogical_runtime
from runtime.runtime_status import emit_runtime_status
from runtime.runtime_settings_store import runtime_bool
from runtime.world_state import attach_world_state_projection
from runtime.chess.surface import (
    authoritative_chess_answer_text,
    chess_move_question,
    chess_surface_text_grounded,
    chess_turn_relevant,
)
from runtime.discourse_state import assistant_proposition_link
from runtime.discourse_surface import DiscourseSurface, DiscourseSurfaceRenderer
from runtime.json_boundary import public_payload
from runtime.semantic_frame import (
    AssistantAct,
    SemanticFrame,
    validate_assistant_act,
    validate_semantic_frame,
)
from semantic_authority import SemanticAuthorityPolicy

from .contracts import AI_RUNTIME_VERSION
from .identity import PendingStrategyUse, PRIMARY_RELATIONSHIP_ID
from .self_context import build_autonomous_self_context


_LOG = logging.getLogger(__name__)


def _foreground_hard_deadline_seconds() -> float:
    """Ngân sách remote tuyệt đối cho một user turn foreground.

    V96 không áp deadline này lên TTS playback: text/voice vẫn giữ sync theo
    speech pipeline. Nó chỉ chặn các model call/rewrite/fallback khỏi cộng dồn
    thành hàng phút khi provider lỗi.
    """

    raw = str(os.getenv("ERIS_FOREGROUND_HARD_DEADLINE_SEC", "") or "").strip()
    try:
        if raw:
            value = float(raw)
        else:
            from runtime.runtime_settings_store import runtime_float
            value = runtime_float("chat", "foreground_hard_deadline_seconds", default=20.0)
    except (TypeError, ValueError, ImportError):
        value = 20.0
    return max(5.0, min(90.0, float(value)))


def _orchestrator_boundary_note(host: Any, operation: str, exc: Exception) -> None:
    logger = getattr(host, "logger", None)
    method = getattr(logger, "debug", None)
    if callable(method):
        try:
            method("[ai orchestrator] %s degraded: %s", operation, exc)
            return
        except Exception as log_exc:
            _LOG.debug("AI orchestrator logger failed: %s", log_exc, exc_info=log_exc)
    _LOG.debug("[ai orchestrator] %s degraded: %s", operation, exc, exc_info=exc)


def _merge_previous_public_context(grounding_context: str, previous_public_context: str) -> str:
    """Keep the reply-window public turn visible to chat planning/writing."""

    return "\n\n".join(
        part for part in (str(grounding_context or ""), str(previous_public_context or ""))
        if part.strip()
    )



def _assistant_intent_probe(user_text: str) -> bool:
    """User đang hỏi ý định/lý do phía sau chính phát ngôn của Eris."""
    text = normalize_vi(user_text)
    markers = (
        "tai sao ban", "sao ban lai", "tai sao em", "sao em lai",
        "ban dang muon noi gi", "em dang muon noi gi", "ban dinh sao",
        "em dinh sao", "y la sao ban", "y la sao em", "y ban la gi", "y em la gi",
    )
    return any(marker in text for marker in markers)


def _deterministic_fail_closed_chat_repair(
    *,
    user_text: str,
    previous_assistant_text: str = "",
    violation_codes: tuple[str, ...] | list[str] = (),
) -> str:
    """Surface tối thiểu có nghĩa khi model rewrite không vượt grounding gate.

    Chú thích VI: direct user turn không được biến thành “đợi 30 giây rồi im”.
    Hàm này chỉ nói về giới hạn/ý định của chính Eris, không thêm fact môi trường,
    ký ức hay cảm xúc của user nên an toàn để dùng sau fail-closed.
    """
    violations = {str(item or "").strip().upper() for item in tuple(violation_codes or ())}
    if previous_assistant_text and _assistant_intent_probe(user_text):
        return (
            "Không có một kế hoạch ngầm nào phía sau. Câu trả lời vừa rồi đã diễn giải xa hơn "
            "mức căn cứ của cuộc trò chuyện; tôi nên giải thích đúng điều mình vừa nói thay vì "
            "gán thêm ý đồ hoặc trạng thái cho bạn."
        )
    if "UNGROUNDED_PERCEPTION_OR_ACTION_CLAIM" in violations:
        return (
            "Tôi chưa có đủ bằng chứng hiện tại để khẳng định phần đó mà không đoán. "
            "Tôi sẽ giữ câu trả lời ở đúng những gì đã có căn cứ."
        )
    return (
        "Câu trả lời vừa tạo đã vượt quá phạm vi căn cứ của lượt này. "
        "Tôi không nên phát phần suy diễn đó như một điều chắc chắn."
    )


class _ChatLatencyTrace:
    def __init__(self, host: Any, *, cycle_start: float, user_text: str = "") -> None:
        self.host = host
        self.user_len = len(str(user_text or ""))
        self.turn_id = ""
        self.started_perf = time.perf_counter()
        self.last_perf = self.started_perf
        try:
            self.started_wall = float(cycle_start)
        except (TypeError, ValueError):
            self.started_wall = time.time()
        self.spans: list[tuple[str, float]] = []
        self.finished = False
        try:
            raw = os.environ.get("ERIS_CHAT_LATENCY_SLOW_SPAN_MS", "750")
            self.slow_span_ms = max(0.0, float(raw))
        except (TypeError, ValueError):
            self.slow_span_ms = 750.0

    def set_turn(self, turn_id: str) -> None:
        value = str(turn_id or "").strip()
        if value:
            self.turn_id = value

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        elapsed_ms = max(0.0, (now - self.last_perf) * 1000.0)
        self.last_perf = now
        label = str(name or "unknown").strip() or "unknown"
        self.spans.append((label, elapsed_ms))
        if self.slow_span_ms > 0.0 and elapsed_ms >= self.slow_span_ms:
            logger = getattr(self.host, "logger", None)
            if logger is not None:
                try:
                    logger.warning(
                        "[chat latency slow] turn=%s span=%s elapsed=%.0fms",
                        self.turn_id or "unknown",
                        label,
                        elapsed_ms,
                    )
                except Exception:
                    pass

    def finish(self, *, status: str = "ok") -> None:
        if self.finished:
            return
        self.finished = True
        total_ms = max(0.0, (time.perf_counter() - self.started_perf) * 1000.0)
        wall_ms = max(0.0, (time.time() - self.started_wall) * 1000.0)
        collapsed: dict[str, float] = {}
        for name, value in self.spans:
            collapsed[name] = collapsed.get(name, 0.0) + value
        ordered = " ".join(
            f"{name}={value:.0f}ms"
            for name, value in collapsed.items()
            if value >= 0.5
        )
        slow = ",".join(
            name
            for name, value in collapsed.items()
            if self.slow_span_ms > 0.0 and value >= self.slow_span_ms
        ) or "none"
        logger = getattr(self.host, "logger", None)
        if logger is not None:
            try:
                logger.info(
                    "[chat latency] turn=%s status=%s %s total=%.0fms e2e=%.0fms slow=%s user_chars=%d",
                    self.turn_id or "unknown",
                    str(status or "ok"),
                    ordered,
                    total_ms,
                    wall_ms,
                    slow,
                    self.user_len,
                )
            except Exception:
                pass
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY

            GLOBAL_OBSERVABILITY.observe(
                "chat_orchestrator_latency_ms",
                total_ms,
                tags={"status": str(status or "ok")},
                fields={
                    "turn_id": self.turn_id,
                    "e2e_ms": round(wall_ms, 2),
                    "spans": {name: round(value, 2) for name, value in collapsed.items()},
                    "slow_spans": slow,
                },
            )
            for name, value in collapsed.items():
                GLOBAL_OBSERVABILITY.observe(
                    "chat_orchestrator_span_ms",
                    value,
                    tags={"span": name, "status": str(status or "ok")},
                    fields={"turn_id": self.turn_id},
                )
        except Exception:
            pass


def _opening_telemetry(
    host: Any,
    name: str,
    *,
    tags: Mapping[str, Any] | None = None,
    fields: Mapping[str, Any] | None = None,
    status: str = "ok",
) -> None:
    """Phát event nhỏ đã redaction; telemetry không được làm hỏng startup."""

    try:
        ensure_observability(host).log(name, tags=tags or {}, fields=fields or {}, status=status)
    except Exception as exc:
        _orchestrator_boundary_note(host, f"telemetry.{name}", exc)


def _opening_hook_subject_class(source: str, subject: str) -> str:
    """Lưu loại hook, không lưu raw OCR/URL vào history."""

    source_key = str(source or "none").strip().lower()
    text = str(subject or "").strip().lower()
    if source_key == "continuity":
        return "continuity_loop"
    if source_key == "runtime":
        return "runtime_status"
    if any(token in text for token in ("facebook", "instagram", "twitter", "x.com", "tiktok", "zalo")):
        return "social_media"
    if any(token in text for token in ("code", "python", "visual studio", "github", "terminal", "repo")):
        return "development_tool"
    return "screen_application" if source_key == "screen" else "none"


def architecture_c_enabled() -> bool:
    """Cortex V2 là kiến trúc production duy nhất.

    Không còn cờ môi trường để âm thầm quay về legacy pipeline. Bootstrap lỗi
    phải lộ rõ và dừng lượt, thay vì đổi sang một bộ contract nhận thức khác.
    """
    return True


def _deepseek_web_authority_mode() -> bool:
    provider = str(os.getenv("ERIS_REMOTE_PROVIDER", "") or "").strip().lower().replace("-", "_")
    return provider in {"deepseek_web", "ds2api", "whale2api", "whale"}


def _runtime_language() -> str:
    """Ngôn ngữ câu public của Main AI.

    Không đọc ``tts.language``: giọng/locale là tầng phát âm, không có quyền đổi
    ngôn ngữ suy nghĩ của Eris. Bản cũ ghép hai contract này nên ``auto`` ở Body
    bị chuẩn hóa thành ``ja`` và startup đột nhiên nói tiếng Nhật.
    """
    try:
        from runtime.runtime_settings_store import runtime_text
        raw = str(runtime_text("chat", "language", default="vi") or "vi").strip().lower()
    except Exception:
        raw = "vi"
    aliases = {
        "": "vi", "auto": "vi", "vn": "vi", "vi-vn": "vi", "vietnamese": "vi",
        "jp": "ja", "ja-jp": "ja", "japanese": "ja",
        "en-us": "en", "english": "en",
    }
    language = aliases.get(raw, raw)
    return language if language in {"vi", "ja", "en"} else "vi"


def _runtime_speech_language() -> str:
    """Ngôn ngữ transcript TTS do cùng semantic authority render."""
    try:
        from runtime.runtime_settings_store import runtime_text
        raw = str(runtime_text("tts", "language", default="ja") or "ja").strip().lower()
    except Exception:
        raw = "ja"
    aliases = {
        "": "ja", "auto": "ja", "jp": "ja", "ja-jp": "ja", "japanese": "ja",
        "vn": "vi", "vi-vn": "vi", "vietnamese": "vi",
        "en-us": "en", "english": "en",
    }
    language = aliases.get(raw, raw)
    return language if language in {"vi", "ja", "en"} else "ja"


def _next_ai_turn_seq(host: Any, *, fallback: int = 1) -> int:
    lock = getattr(host, "_ai_turn_seq_lock", None)
    if lock is None:
        return max(1, int(fallback or 1))
    with lock:
        host._ai_turn_seq = max(
            int(getattr(host, "_ai_turn_seq", 0) or 0),
            int(host.ai_state_reducer.snapshot().last_applied_turn_seq or 0),
        ) + 1
        return int(host._ai_turn_seq)


def _chat_commit_idempotency_key(host: Any, turn_id: str) -> str:
    """Tạo khóa commit duy nhất cho đúng một lượt chat trong đúng một phiên chạy.

    ``SessionTurnLedger`` đánh số lại từ 1 sau mỗi lần khởi động, trong khi receipt
    durable của public-turn sống qua nhiều phiên. Chỉ dùng ``session_turn:<n>`` sẽ
    làm lượt mới đụng receipt cũ và bỏ qua toàn bộ side effect UI/TTS. Khóa vì thế
    phải mang cả runtime session và lifecycle turn nhận từ transport.
    """

    runtime_session_id = str(getattr(host, "runtime_session_id", "") or "").strip()
    lifecycle_turn_id = str(
        getattr(host, "_active_public_lifecycle_turn_id", "") or ""
    ).strip()
    logical_turn_id = str(turn_id or "session_turn:unknown").strip()

    parts = ["chat"]
    if runtime_session_id:
        parts.append(runtime_session_id)
    if lifecycle_turn_id:
        parts.append(lifecycle_turn_id)
    parts.extend((logical_turn_id, "assistant"))
    return ":".join(parts)


# V12 hard cutover:
# Không giữ startup IntentionPlan/static planner ở orchestrator nữa. Opening và
# autonomous initiative chỉ được phát thành ConversationEvent rồi quay về
# ConversationKernel/RootAgent; nếu giữ helper dead-code ở đây rất dễ bị caller
# mới vô tình hồi sinh một semantic authority song song.
def _serializable_public_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Chuẩn hóa payload public trước JSON và bỏ cache object typed nội bộ."""

    return public_payload(payload)


def _authoritative_chess_result_text(report: Any, user_text: str) -> str:
    """Lấy tool result có thẩm quyền cho câu hỏi nước đi trực tiếp.

    Không dùng cho autonomous và không giả lập banter. Chỉ structured chess
    state có provenance mới được phép trả một nước analyzer đã tính.
    """

    if str(getattr(report, "structured_kind", "") or "") != "chess":
        return ""
    state = getattr(report, "structured_state", None) or {}
    if not state or not chess_turn_relevant(user_text, engagement_active=True):
        return ""
    return authoritative_chess_answer_text(state, user_text=user_text)


def _previous_active_proposition_root(ledger: Any) -> int:
    """Lấy root đang active mà không coi acknowledgement là proposition mới."""

    try:
        turns = ledger.all_turns()
    except Exception:
        return 0
    for turn in reversed(turns):
        if getattr(turn, "role", "") != "assistant":
            continue
        discourse = dict(getattr(turn, "meta", {}).get("discourse", {}) or {})
        root = int(
            discourse.get("active_proposition_root_turn_index", 0)
            or discourse.get("proposition_root_turn_index", 0)
            or 0
        )
        if root:
            return root
    return 0


def _decision_from_discourse_surface(host: Any, surface: DiscourseSurface, *, user_text: str):
    payload = {
        "text": surface.text,
        "should_speak": bool(surface.text),
        "animation": "stand",
        "next_animation": "stand",
        "direction": 0,
        "target_x": -1.0,
        "bounce_action": "",
        "system_action": "",
        "action_input": "",
        "delivery_hint": "focused",
        "performance_hint": surface.performance_hint,
    }
    decision = host.parser.parse(json.dumps(payload, ensure_ascii=False), user_hint=user_text)
    decision.intent = surface.intent
    decision.parse_mode = "typed_discourse"
    decision.reasoning_tags = ("answer_first", "typed_discourse", surface.intent)
    return decision


def _pedagogy_enabled(host: Any) -> bool:
    explicit = getattr(host, "pedagogical_controller_enabled", None)
    if explicit is not None:
        return bool(explicit)
    return runtime_bool("pedagogy", "enabled", default=False)


def _explicit_pedagogical_activation(user_text: str) -> bool:
    text = normalize_vi(user_text)
    markers = (
        "chi toi", "chi minh", "day toi", "day minh", "teach me", "tutor me",
        "help me learn", "help me study", "i want to learn", "i need to learn",
        "muon hoc", "dang hoc", "muon hieu", "that su hieu", "hieu cho toi goc",
        "giai thich ky", "giai thich chi tiet", "giai thich sau", "tu goc",
        "explain deeply", "in detail", "from first principles", "walk me through",
    )
    return any(marker in text for marker in markers)


def _bounded_pedagogical_history(history: Any) -> tuple[dict[str, str], ...]:
    projected: list[dict[str, str]] = []
    for item in tuple(history or ())[-6:]:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role", "") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = " ".join(str(item.get("content", "") or "").split()).strip()[:600]
        if content:
            projected.append({"role": role, "content": content})
    return tuple(projected)


def _bounded_pedagogical_turn(user_text: Any) -> str:
    return " ".join(str(user_text or "").split()).strip()[:1200]


def _notify_pedagogical_audio_finished(
    host: Any,
    timestamp: float,
    *,
    source_turn_id: str = "",
) -> bool:
    """Forward a real playback edge to the enabled teaching runtime."""
    if not _pedagogy_enabled(host) or float(timestamp or 0.0) <= 0.0:
        return False
    runtime = ensure_pedagogical_runtime(host)
    if runtime is None:
        return False
    try:
        runtime.notify_audio_finished(
            float(timestamp), source_turn_id=str(source_turn_id or "")[:120],
        )
        return True
    except Exception:
        return False


def _prepare_pedagogical_turn(
    host: Any,
    *,
    user_text: str,
    turn_id: str,
    recent_history: Any,
    now: float,
    evidence: tuple[str, ...] | None = None,
) -> tuple[Any | None, TeachingDirective | None]:
    """Select one foreground teaching directive before ordinary chat planning."""
    if not _pedagogy_enabled(host):
        return None, None
    runtime = ensure_pedagogical_runtime(host)
    if runtime is None:
        return None, None
    try:
        snapshot = runtime.snapshot(now=now)
    except Exception:
        return runtime, None
    active = snapshot.get("active_episode") if isinstance(snapshot, Mapping) else None
    status = str(active.get("status", "") or "").strip().lower() if isinstance(active, Mapping) else ""
    if isinstance(active, Mapping) and status not in {"completed", "abandoned", "expired", "failed"}:
        try:
            bounded_turn = _bounded_pedagogical_turn(user_text)
            episode = normalize_episode(active)
            current_concept = next(
                (
                    concept
                    for concept in (episode.concepts if episode is not None else ())
                    if concept.concept_id == episode.comprehension.current_concept_id
                ),
                None,
            )
            result = runtime.observe_user_turn(
                bounded_turn,
                evidence=infer_user_evidence(
                    bounded_turn,
                    explicit_evidence=evidence,
                    current_concept=current_concept,
                ),
                now=now,
            )
        except Exception:
            return runtime, None
        directive = getattr(getattr(result, "action", None), "directive", None)
        return runtime, directive if isinstance(directive, TeachingDirective) else None

    if not _explicit_pedagogical_activation(user_text):
        return runtime, None
    builder = (
        getattr(host, "ai_teaching_plan_builder", None)
        or getattr(host, "pedagogical_plan_builder", None)
    )
    if builder is None:
        router = getattr(host, "ai_model_router", None)
        if router is None:
            return runtime, None
        builder = TeachingPlanBuilder(router)
    request_context = {
        "explicit_plan_request": True,
        "turn_id": str(turn_id or "")[:120],
        "current_turn": _bounded_pedagogical_turn(user_text),
        "recent_public_history": list(_bounded_pedagogical_history(recent_history)),
    }
    try:
        plan = builder.build(request_context)
        if plan is None:
            return runtime, None
        episode = start_teaching_episode(plan, now=now)
        if not runtime.start_episode(episode):
            return runtime, None
        return runtime, select_next_action(episode, now=now).directive
    except Exception:
        return runtime, None


def _commit_pedagogical_receipt(
    runtime: Any,
    directive: TeachingDirective | None,
    *,
    public_text: str,
    turn_id: str,
    decision: Any,
    payload: Mapping[str, Any] | None,
    assistant_meta: Mapping[str, Any] | None,
    now: float,
) -> bool:
    if runtime is None or not isinstance(directive, TeachingDirective):
        return False
    parse_mode = str(getattr(decision, "parse_mode", "") or "").strip().lower()
    grounding_risk = payload.get("grounding_risk") if isinstance(payload, Mapping) else None
    grounding_failed = isinstance(grounding_risk, Mapping) and grounding_risk.get("allowed") is False
    deterministic_fallback = bool(payload and payload.get("utterance_degraded"))
    if (
        not str(public_text or "").strip()
        or assistant_meta is None
        or not bool(getattr(decision, "should_speak", False))
        or bool(getattr(decision, "failure_meta", None))
        or parse_mode in {
            "typed_discourse_fallback", "authoritative_chess_result", "chess_surface_unavailable",
        }
        or grounding_failed
        or deterministic_fallback
    ):
        return False
    signature = "sha256:" + hashlib.sha256(
        str(public_text).encode("utf-8", errors="replace")
    ).hexdigest()
    receipt = TeachingReceipt(
        episode_id=directive.episode_id,
        plan_revision=directive.plan_revision,
        chunk_id=directive.chunk_id,
        turn_id=turn_id,
        text_signature=signature,
        successful=True,
    )
    try:
        return bool(runtime.commit_receipt(receipt, now=now))
    except Exception:
        return False


def _ensure_root_capability_registry(host: Any) -> RootCapabilityRegistry:
    from runtime.capabilities import ActionCapability, ResearchCapability, TutorCapability, WorkCapability
    from runtime.conversation.context import RootCapabilityRegistry
    from runtime.capabilities.canvas import canvas_capability_for_host

    tutor_capability = getattr(host, "tutor_capability", None)
    if not isinstance(tutor_capability, TutorCapability):
        tutor_capability = TutorCapability(
            getattr(host, "tutor_executive", None),
            logger=getattr(host, "logger", None),
        )
        host.tutor_capability = tutor_capability

    work_capability = getattr(host, "work_capability", None)
    if not isinstance(work_capability, WorkCapability):
        work_capability = WorkCapability(
            getattr(host, "agent_harness", None),
            logger=getattr(host, "logger", None),
            host=host,
        )
        host.work_capability = work_capability
    work_capability.host = host

    research_capability = getattr(host, "research_capability", None)
    if not isinstance(research_capability, ResearchCapability):
        research_capability = ResearchCapability(host, logger=getattr(host, "logger", None))
        host.research_capability = research_capability
    else:
        research_capability.host = host
        research_capability.logger = getattr(host, "logger", None)

    action_capability = getattr(host, "action_capability", None)
    if not isinstance(action_capability, ActionCapability):
        action_capability = ActionCapability(
            getattr(host, "action_executor", None),
            logger=getattr(host, "logger", None),
        )
        host.action_capability = action_capability

    return RootCapabilityRegistry({
        "tutor": tutor_capability,
        "work": work_capability,
        "research": research_capability,
        "action": action_capability,
        "canvas": canvas_capability_for_host(host),
    })


class AIChatOrchestrator:
    def __init__(self, host: Any) -> None:
        self.host = host

    def run(
        self,
        user_text: str,
        *,
        cycle_start: float,
        previous_public_context: str = "",
        previous_public_turn: Any | None = None,
        speech_interrupt_context: str = "",
        speech_interrupt_event: Any | None = None,
        speech_interrupt_reaction: Any | None = None,
    ) -> None:
        # PDF annotation, pages 7 and 19-20: a user turn is a cancellation
        # scope. Entering it preempts queued optional lanes and prevents new
        # background requests from competing with the public reply.
        from runtime.inference_broker import GLOBAL_INFERENCE_BROKER, inference_context

        scope = f"user_turn:{int(float(cycle_start) * 1000)}:{id(self):x}"
        hard_deadline_at = float(cycle_start) + _foreground_hard_deadline_seconds()
        # V96: toàn bộ model work của một user turn dùng CHUNG một deadline.
        # Writer, claim rewrite và provider fallback chỉ được ăn phần còn lại;
        # không tầng con nào được tự tạo thêm 90 giây sau khi tầng trước đã chậm.
        with GLOBAL_INFERENCE_BROKER.user_turn(scope), inference_context(
            "chat_main",
            deadline_at=hard_deadline_at,
            cancel_scope=scope,
            optional=False,
            metadata={"foreground_hard_deadline_v96": True},
        ):
            preempt_background = getattr(self.host, "_preempt_background_for_chat", None)
            if callable(preempt_background):
                try:
                    preempt_background("user_turn")
                except Exception as exc:
                    logger = getattr(self.host, "logger", None)
                    if logger is not None:
                        try:
                            logger.debug("background preempt failed: %s", exc)
                        except Exception:
                            pass
            result = self._run_v12(
                user_text,
                cycle_start=cycle_start,
                previous_public_context=previous_public_context,
                previous_public_turn=previous_public_turn,
                speech_interrupt_context=speech_interrupt_context,
                speech_interrupt_event=speech_interrupt_event,
                speech_interrupt_reaction=speech_interrupt_reaction,
            )
            from runtime.conversation.projection import infer_turn_category
            GLOBAL_INFERENCE_BROKER.set_user_turn_category(scope, infer_turn_category(self.host))
            return result

    def _run_v12(
        self,
        user_text: str,
        *,
        cycle_start: float,
        previous_public_context: str = "",
        previous_public_turn: Any | None = None,
        speech_interrupt_context: str = "",
        speech_interrupt_event: Any | None = None,
        speech_interrupt_reaction: Any | None = None,
    ) -> Any:
        del speech_interrupt_event, speech_interrupt_reaction
        host = self.host
        from runtime.conversation import ConversationEvent
        from runtime.conversation.bootstrap import ensure_conversation_kernel

        sid = str(
            getattr(host, "conversation_id", "")
            or getattr(host, "thread_id", "")
            or "foreground:primary"
        ).strip()[:180]
        user_turn = (getattr(host, "_active_input_batch_turn", None)
                     if getattr(host, "_chat_processing", False) else None)
        new_user_batch = user_turn is None
        ledger_factory = getattr(host, "_ensure_session_turn_ledger", None)
        if callable(ledger_factory) and new_user_batch:
            user_turn = ledger_factory().record_user(
                str(user_text or ""),
                timestamp=cycle_start,
                meta={"channel": "chat", "architecture": "conversation_kernel_v12"},
            )
            host._active_input_batch_turn = user_turn
        # V12 write-back: user turn phải nuôi trajectory/handoff/memory/identity
        # trước khi RootAgent đọc context. Bridge chỉ observe evidence, không route
        # intent và không có quyền quyết định câu trả lời.
        try:
            from runtime.conversation.observation_bridge import ensure_conversation_observation_bridge
            ledger = ledger_factory() if callable(ledger_factory) else None
            assistant_turns = ledger.assistant_turns() if ledger is not None else []
            previous_assistant = assistant_turns[-1] if assistant_turns else None
            previous_assistant_text = str(getattr(previous_assistant, "text", "") or "")
            if previous_assistant is not None:
                delivery_status = str(getattr(previous_assistant, "meta", {}).get("delivery_status", "") or "")
                if delivery_status in {"pending", "cancelled", "failed"}:
                    # Observer cũng phải biết câu cũ chỉ hiện trên panel hoặc
                    # bị cắt tiếng, không suy rằng user đã nghe trọn câu.
                    previous_assistant_text = f"[TTS {delivery_status}; chưa xác nhận nghe trọn] {previous_assistant_text}"
            ensure_conversation_observation_bridge(host).observe_user_turn(
                str(user_text or ""),
                turn_ref=(f"session_turn:{int(getattr(user_turn, 'index', 0) or 0)}" if user_turn is not None else ""),
                turn_index=int(getattr(user_turn, "index", 0) or 0),
                previous_assistant_text=previous_assistant_text or (str(getattr(host, "last_text", "") or "") if previous_assistant is None else ""),
                previous_assistant_turn_ref=(f"session_turn:{int(getattr(previous_assistant, 'index', 0) or 0)}" if previous_assistant is not None else ""),
                previous_assistant_turn_index=int(getattr(previous_assistant, "index", 0) or 0),
                now=cycle_start,
            )
        except Exception as exc:
            _orchestrator_boundary_note(host, "conversation_v12.observe_user_turn", exc)

        # V12: RootAgent/SurfaceWriter nhận current turn qua ``event.text`` /
        # ``user_text``. Vì vậy history ở đây PHẢI chỉ chứa các lượt trước; nếu
        # append current user trước khi chụp history, cùng một câu sẽ xuất hiện
        # hai lần trong cả root payload lẫn writer messages.
        # P0: foreground trước đây dùng trực tiếp raw chat_history; metadata
        # delivery_status không được dịch ra context nên Root có thể tưởng
        # audio bị huỷ đã nói xong. Dùng cùng projection với Autonomous.
        history_reader = getattr(host, "_chat_history_for_llm", None)
        if callable(history_reader):
            history = tuple(history_reader(limit=32))
        else:
            history = tuple(
                item for item in tuple(getattr(host, "chat_history", ()) or ())[-32:]
                if isinstance(item, Mapping)
            )
        if not new_user_batch and history and history[-1].get("role") == "user":
            # This same batch is event.text; do not replay it as older history.
            history = history[:-1]
        append_history = getattr(host, "_append_chat_history", None)
        if callable(append_history) and new_user_batch:
            append_history("user", str(user_text or ""))
        turn_index = int(getattr(user_turn, "index", 0) or 0)
        event_id = f"user:{turn_index or time.time_ns()}"
        if not new_user_batch:
            revision = int(getattr(getattr(host, "input_revision_buffer", None), "current_revision", 0) or 0)
            event_id += f":revision:{revision}"
        event = ConversationEvent(
            event_id=event_id,
            session_id=sid,
            kind="user_turn",
            text=str(user_text or "")[:16000],
        )
        # V12 hard cutover: foreground bootstrap chỉ *đăng ký* capability. Không
        # gọi tutor/work/research trước RootAgent vì làm vậy sẽ biến router thành
        # semantic authority thứ hai và khiến small-talk cũng phải chạy domain
        # planner không cần thiết. DeepSeek tự quyết định capability nào đáng gọi.
        capability_registry: RootCapabilityRegistry = _ensure_root_capability_registry(host)
        # ContextAssembler chỉ projection state/evidence; nó không route intent và
        # không chọn nội dung nói. RootAgent vẫn là semantic/social authority duy
        # nhất. Đây là chỗ nối lại identity/relationship/memory/world/affect đã bị
        # đứt khi hard-cutover V12, thay vì hồi sinh planner cũ.
        from runtime.conversation.context_assembler import ensure_conversation_context_assembler

        from runtime.conversation.evidence_assembler import ensure_conversation_evidence_assembler
        evidence_context = ensure_conversation_evidence_assembler(host).assemble_chat(
            str(user_text or ""),
            previous_public_turn=previous_public_turn,
        )
        # Cơ chế auto-path chỉ mở cửa cho TutorExecutive. Không tự phát lời,
        # không bật Hệ 2 và không chiếm semantic authority của Root.
        try:
            from runtime.tutor.auto_path import maybe_autostart_path
            maybe_autostart_path(host, user_text=str(user_text or ""),
                                 recent_history=history, now=cycle_start)
        except Exception as exc:
            _orchestrator_boundary_note(host, "conversation_v12.tutor_auto_path", exc)
        runtime_context = ensure_conversation_context_assembler(host).assemble(
            user_text=str(user_text or ""),
            channel="chat",
            previous_public_context=str(previous_public_context or ""),
            interrupt_context=str(speech_interrupt_context or ""),
            recent_history=history,
            event=event,
            source_report=evidence_context.get("__source_grounding_report"),
        )
        source_context = str(evidence_context.get("source_grounding_context", "") or "").strip()
        if source_context:
            runtime_context["grounding_context"] = "\n\n".join(
                part for part in (str(runtime_context.get("grounding_context", "") or ""), source_context) if part.strip()
            )[:48000]
        # Truy xuất ký ức có chọn lọc là công cụ ROOT sở hữu. Nó chỉ hoạt động
        # nếu model tự gọi recall_memory; không áp thêm memory-search ở mọi turn.
        from runtime.conversation.context import memory_recall_tools_for_host
        runtime_context.update({
            "__root_memory_recall_tools": memory_recall_tools_for_host(host, session_id=sid),
            "__source_grounding_report": evidence_context.get("__source_grounding_report"),
            "__source_grounding_engine": evidence_context.get("__source_grounding_engine"),
            # Dependency nội bộ cho root loop; RuntimeModelRootAgent không
            # serialize key ``__*`` vào prompt và writer không dùng registry này.
            "__root_capabilities": capability_registry,
            "language_hint": _runtime_language(),
            "speech_language_hint": _runtime_speech_language(),
        })
        collect_batch = getattr(host, "_collect_user_input_batch", None)
        if callable(collect_batch):
            batch_view: dict[str, Any] = {}
            admitted_messages = int(getattr(host, "_input_batch_admitted_messages", 0) or 0)

            def refresh_input_batch(*, claim: bool = False):
                nonlocal admitted_messages
                snapshot = collect_batch(claim=claim)
                batch_view["snapshot"] = snapshot
                metadata = snapshot.to_dict()
                metadata.pop("canonical_text", None)  # text appears once in event.text
                ledger = ledger_factory() if callable(ledger_factory) else None
                update = getattr(ledger, "update_user_batch", None)
                if callable(update) and user_turn is not None:
                    update(user_turn.index, snapshot.canonical_text, metadata=metadata)
                for item in reversed(getattr(host, "chat_history", ())):
                    if isinstance(item, dict) and item.get("role") == "user":
                        item["content"] = snapshot.canonical_text
                        if "raw_content" in item:
                            item["raw_content"] = snapshot.canonical_text
                        item["user_input_batch"] = metadata
                        break
                host.current_stage_user_text = snapshot.canonical_text
                ingest = getattr(host, "_ingest_user_turn_memory_hot_path", None)
                if callable(ingest) and user_turn is not None:
                    for n, entry in enumerate(snapshot.entries[admitted_messages:], start=admitted_messages + 1):
                        try:
                            ingest(entry.text, turn_id=f"session_turn:{user_turn.index}:message:{n}")
                        except Exception as exc:
                            _orchestrator_boundary_note(host, "conversation_v12.batch_memory", exc)
                admitted_messages = len(snapshot.entries)
                host._input_batch_admitted_messages = admitted_messages
                return snapshot

            runtime_context["__root_input_batch"] = refresh_input_batch
            runtime_context["__root_input_batch_view"] = batch_view
        host.current_stage_user_text = str(user_text or "")
        kernel = ensure_conversation_kernel(host)
        _install_speculative_dispatch(host, kernel)
        result = kernel.handle_event(
            event,
            recent_history=history,
            runtime_context=runtime_context,
        )
        decision_dict = (
            result.decision.to_dict()
            if result.decision is not None and callable(getattr(result.decision, "to_dict", None))
            else {}
        )
        from runtime.autonomy.authority_trace import emit as autonomy_trace
        autonomy_trace("conversation.kernel_result", result_status=str(result.status),
                       event_id=str(result.event.event_id), session_id=sid)
        host.last_agent_turn = decision_dict
        host.last_conversation_run = {
            "schema": "eris.conversation_run.v12",
            "event_id": result.event.event_id,
            "generation": result.event.generation,
            "status": result.status,
            # Chỉ có mã lỗi đã lọc ở Kernel; không ghi raw provider response.
            "failure_reason": result.failure_reason,
            "delivery_id": result.delivery_id,
            "decision": decision_dict,
        }
        return result

def _install_speculative_dispatch(host: Any, kernel: Any) -> None:
    """Dispatch root/writer prepare after commit; never choose its semantic content."""
    import threading

    def dispatch(lease: Any) -> None:
        def prepare() -> None:
            try:
                AIAutonomousOrchestrator(host)._run_v12_signal(
                    "autonomous_signal", sleep_after=False, speculative_lease=lease,
                )
            except Exception as exc:
                _orchestrator_boundary_note(host, "conversation_v12.speculative", exc)
        threading.Thread(target=prepare, daemon=True,
                         name="eris-speculative-continuation").start()

    kernel.speculative_preparer = dispatch


class AIAutonomousOrchestrator:
    def __init__(self, host: Any) -> None:
        self.host = host
        # P2: đường body_only cũ không có consumer production, nhưng vẫn có
        # thể gửi trực tiếp qua _send_decision_to_body ngoài Kernel. Đã bỏ để
        # giữ đúng một cửa phát ngôn. Nonverbal tương lai phải có adapter stage
        # không được giả làm một câu chat public.

    def run_startup(self) -> bool:
        """Prime continuity state only; startup no longer fabricates public speech.

        V71 removes the historical startup-greeting path entirely. That path could
        log ``spoke`` and feed PostObserver even when no bubble/TTS/public receipt
        existed. A public act that the user never received must not become history.

        The normal autonomous/chat pipeline owns every real utterance after startup.
        If ERIS genuinely wants to greet or resume a thread, it must win Neuro and
        reach the same public commit path as every other sentence.
        """
        host = self.host
        if bool(getattr(host, "_ai_startup_ran", False)):
            return False
        host._ai_startup_ran = True

        try:
            state = host.ai_state_reducer.snapshot()
        except Exception as exc:
            _orchestrator_boundary_note(host, "startup.continuity_snapshot", exc)
            state = None
        try:
            recent_history = tuple(host._chat_history_for_llm(limit=8))
        except Exception as exc:
            _orchestrator_boundary_note(host, "startup.continuity_history", exc)
            recent_history = ()

        open_loops = tuple(getattr(state, "open_loops", ()) or ()) if state is not None else ()
        latest_observation = (
            dict(getattr(state, "latest_observation", {}) or {})
            if state is not None and isinstance(getattr(state, "latest_observation", {}), Mapping)
            else {}
        )
        try:
            from runtime.conversation_handoff import ensure_conversation_handoff_runtime

            handoff = ensure_conversation_handoff_runtime(host).prime_from_state(
                open_loops=open_loops,
                latest_observation=latest_observation,
                recent_history=recent_history,
                now=time.time(),
            )
            try:
                from runtime.conversation_trajectory import ensure_conversation_trajectory_runtime
                ensure_conversation_trajectory_runtime(host).prime_from_history(
                    recent_history, now=time.time()
                )
            except Exception as exc:
                _orchestrator_boundary_note(host, "startup.conversation_trajectory_prime", exc)
            # V76: startup là re-entry appraisal, tuyệt đối không tự chiếm một
            # public turn. State chỉ ghi khoảng cách từ lần mở trước + daypart để
            # autonomous planner có thể comment/mutter/silence theo ngữ cảnh sau đó.
            try:
                from runtime.persistent_individual import ensure_persistent_individual_runtime
                startup_view = ensure_persistent_individual_runtime(host).note_startup(now=time.time())
                host.logger.info(
                    "[persistent-individual-v76] startup gap=%.1fs late_night=%s short_return=%s opening=%s",
                    float(startup_view.get("reentry_gap_seconds", 0.0) or 0.0),
                    bool(startup_view.get("late_night", False)),
                    bool(startup_view.get("short_return", False)),
                    ",".join(startup_view.get("opening_modes", ()) or ()),
                )
            except Exception as exc:
                _orchestrator_boundary_note(host, "startup.persistent_individual_prime", exc)
            # V12: handoff chỉ prime provenance/observation. Không log owner,
            # pressure hay pending-goal vì các semantic control state đó đã bị xóa.
            host.logger.info(
                "[architecture C startup] handoff_observation_primed rev=%s opening_signal=kernel",
                handoff.revision,
            )
        except Exception as exc:
            _orchestrator_boundary_note(host, "startup.conversation_handoff_prime", exc)
            host.logger.info(
                "[architecture C startup] opening_signal=kernel continuity_prime=unavailable"
            )

        # Startup owns only an opportunity signal. Root may choose NoReply; any
        # positive move still uses the same writer and delivery path as chat.
        result = self._run_v12_signal(
            "opening_signal",
            metadata={
                "reason": "runtime_started",
                "open_loop_count": len(open_loops),
                "has_recent_history": bool(recent_history),
            },
            sleep_after=False,
        )
        return bool(getattr(result, "status", "") == "delivering")

    def _run_v12_signal(
        self,
        kind: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        sleep_after: bool = True,
        event_id: str = "",
        speculative_lease: Any | None = None,
    ) -> Any:
        """Submit a non-user opportunity to the single conversation authority."""

        signal_kind = str(kind or "").strip().lower()
        if signal_kind not in {
            "autonomous_signal", "social_signal", "opening_signal",
            "tutor_result", "work_result", "research_result",
        }:
            raise ValueError(f"unsupported_conversation_signal:{signal_kind}")
        host = self.host
        if signal_kind != "opening_signal":
            body_running = getattr(host, "body_running_fn", None)
            if callable(body_running) and not body_running():
                stop = getattr(host, "stop", None)
                if callable(stop):
                    stop()
                return None
            process_pending = getattr(host, "_process_pending_user_input", None)
            if callable(process_pending) and process_pending():
                return None

        from runtime.conversation import ConversationEvent
        from runtime.conversation.bootstrap import ensure_conversation_kernel

        sid = str(
            getattr(host, "conversation_id", "")
            or getattr(host, "thread_id", "")
            or "foreground:primary"
        ).strip()[:180]
        kernel = ensure_conversation_kernel(host)
        _install_speculative_dispatch(host, kernel)
        if signal_kind != "opening_signal":
            phase = getattr(kernel.snapshot(sid).phase, "value", "")
            if phase in {"thinking", "working", "writing", "delivering"}:
                sleep = getattr(host, "_sleep_interruptible", None)
                if sleep_after and callable(sleep):
                    sleep(0.5)
                return None
        history_reader = getattr(host, "_chat_history_for_llm", None)
        if callable(history_reader):
            try:
                recent_history = tuple(history_reader(limit=32))
            except Exception as exc:
                _orchestrator_boundary_note(host, "conversation_signal.history", exc)
                recent_history = ()
        else:
            recent_history = tuple(getattr(host, "chat_history", ()) or ())[-32:]

        clock = getattr(host, "interaction_clock", None)
        signal_metadata = {
            "reason": str(getattr(host, "_autonomous_wakeup_reason", "timer") or "timer")[:180],
            "chat_mode": bool(getattr(host, "chat_mode", False)),
            **dict(metadata or {}),
        }
        if clock is not None:
            for source in ("seconds_since_user", "seconds_since_assistant"):
                value = getattr(clock, source, None)
                try:
                    signal_metadata[source] = float(value() if callable(value) else value or 0.0)
                except (TypeError, ValueError):
                    pass

        state = None
        state_runtime = getattr(host, "ai_state_reducer", None)
        snapshot = getattr(state_runtime, "snapshot", None)
        if callable(snapshot):
            try:
                state = snapshot()
            except Exception as exc:
                _orchestrator_boundary_note(host, "conversation_signal.state", exc)
        from runtime.conversation.context_assembler import ensure_conversation_context_assembler
        from runtime.conversation.evidence_assembler import ensure_conversation_evidence_assembler

        # Gán ID một lần trước assemble. Signal thường dùng cùng ID này khi vào
        # kernel; continuation_due do scheduler sinh ID riêng nên chưa được cấp
        # registry từ signal giả. Kernel sẽ fail-closed nếu IDs không khớp.
        prospective_event = ConversationEvent(
            event_id=str(event_id or f"{signal_kind}:{time.time_ns()}")[:240],
            session_id=sid, kind=signal_kind,
            generation=kernel.snapshot(sid).generation,
        )
        scope = signal_metadata.get("canvas_scope", {})
        from runtime.learning_canvas.events import allow_canvas_result
        if not allow_canvas_result(host, signal_kind, signal_metadata, event_id=prospective_event.event_id):
            from runtime.conversation.kernel import ConversationRunResult
            return ConversationRunResult(event=prospective_event, decision=None, status="stale_focus")
        evidence_context = ensure_conversation_evidence_assembler(host).assemble_autonomous()
        runtime_context = ensure_conversation_context_assembler(host).assemble(
            user_text="",
            channel="autonomous",
            previous_public_context=str(getattr(host, "last_text", "") or ""),
            recent_history=recent_history,
            event=prospective_event,
            source_report=evidence_context.get("__source_grounding_report"),
        )
        # Khôi phục self-context từng bị hard-cut: đây chỉ là public-safe
        # projection của state nội tại hiện có, không phải planner và không được
        # tự chọn câu nói. RootAgent vẫn quyết định có biến seed thành speech hay không.
        try:
            runtime_context["autonomous_self_context"] = build_autonomous_self_context(host).to_dict()
        except Exception as exc:
            _orchestrator_boundary_note(host, "conversation_signal.self_context", exc)
        source_context = str(evidence_context.get("source_grounding_context", "") or "").strip()
        if source_context:
            runtime_context["grounding_context"] = "\n\n".join(
                part for part in (str(runtime_context.get("grounding_context", "") or ""), source_context) if part.strip()
            )[:48000]
        # Event autonomous cũng có quyền tự nhớ lại, nhưng không được tự phát
        # ngôn: mọi công cụ phải qua RootAgent/ConversationKernel.
        from runtime.conversation.context import memory_recall_tools_for_host
        runtime_context["__root_memory_recall_tools"] = memory_recall_tools_for_host(host, session_id=sid)
        runtime_context["__source_grounding_report"] = evidence_context.get("__source_grounding_report")
        runtime_context["__source_grounding_engine"] = evidence_context.get("__source_grounding_engine")
        if scope:
            runtime_context["__canvas_scope"] = dict(scope)
        runtime_context["__root_capabilities"] = _ensure_root_capability_registry(host)
        status = dict(runtime_context.get("conversation_status", {}) or {})
        status.update({
            "signal_kind": signal_kind,
            "chat_mode": bool(getattr(host, "chat_mode", False)),
        })
        runtime_context["conversation_status"] = status
        for capability_key in ("tutor_capability", "work_capability", "research_capability", "canvas_capability"):
            capability_value = signal_metadata.get(capability_key)
            if isinstance(capability_value, Mapping):
                runtime_context[capability_key] = dict(capability_value)
        if state is not None:
            latest_observation = getattr(state, "latest_observation", None)
            open_loops = getattr(state, "open_loops", ())
            if isinstance(latest_observation, Mapping):
                signal_metadata["latest_observation"] = dict(latest_observation)
            signal_metadata["open_loops"] = [str(item)[:500] for item in tuple(open_loops or ())[:5]]

        if speculative_lease is not None:
            result = kernel.prepare_continuation_candidate(
                lease=speculative_lease,
                recent_history=recent_history,
                runtime_context=runtime_context,
            )
            # Speculative result CHƯA được public. Không ghi last_agent_turn,
            # last_conversation_run và không phát ngôn sớm.
            return result
        if signal_kind == "autonomous_signal":
            result = kernel.handle_due_continuation(
                session_id=sid,
                recent_history=recent_history,
                runtime_context=runtime_context,
            )
        else:
            result = None
        if result is None:
            result = kernel.handle_event(
                __import__("dataclasses").replace(prospective_event, metadata=signal_metadata),
                recent_history=recent_history,
                runtime_context=runtime_context,
            )

        if scope and getattr(result, "status", "") in {"stale", "stale_focus", "delivery_unavailable"}:
            allow_canvas_result(host, signal_kind, signal_metadata, event_id=prospective_event.event_id)
        decision = getattr(result, "decision", None)
        decision_dict = decision.to_dict() if callable(getattr(decision, "to_dict", None)) else {}
        host.last_agent_turn = decision_dict
        host.last_conversation_run = {
            "schema": "eris.conversation_run.v12",
            "event_id": result.event.event_id,
            "generation": result.event.generation,
            "status": result.status,
            "delivery_id": result.delivery_id,
            "decision": decision_dict,
        }
        if getattr(result, "status", "") != "delivering":
            maybe_ambient = getattr(host, "_maybe_emit_ambient_micro_reflex", None)
            if callable(maybe_ambient):
                try:
                    maybe_ambient(now=time.time())
                except Exception as ambient_exc:
                    logger = getattr(host, "logger", None)
                    if logger:
                        logger.debug("[ambient-reflex] loop trigger error: %s", ambient_exc)
        if sleep_after:
            sleep = getattr(host, "_sleep_interruptible", None)
            if callable(sleep):
                lease = kernel.continuation.peek(sid)
                if lease is not None:
                    delay = max(0.05, min(5.0, float(lease.due_at) - time.time()))
                else:
                    delay = 2.5 if result.status == "delivering" else 15.0
                sleep(delay)
        return result

    def run_social_signal(self, metadata: Mapping[str, Any] | None = None) -> Any:
        return self._run_v12_signal("social_signal", metadata=metadata, sleep_after=False)

    def run_force_speak(self) -> None:
        self._run_v12_signal(
            "autonomous_signal",
            metadata={"reason": "explicit_presence_request", "explicit_user_pull": True},
            sleep_after=False,
        )

    def run_iteration(self) -> bool:
        """Run one mechanical opportunity through the V12 conversation authority."""

        from runtime.conversation.bootstrap import ensure_conversation_kernel

        host = self.host
        sid = str(
            getattr(host, "conversation_id", "")
            or getattr(host, "thread_id", "")
            or "foreground:primary"
        ).strip()[:180]
        kernel = ensure_conversation_kernel(host)
        pending = getattr(host, "_pending_conversation_capability_events", None)
        lease = kernel.continuation.peek(sid)
        if not pending and lease is not None and lease.due_at <= time.time():
            self._run_v12_signal("autonomous_signal")
            return bool(getattr(host, "running", True))

        item = pending[0] if pending else None
        if isinstance(item, Mapping):
            result = self._run_v12_signal(
                str(item.get("kind", "") or ""),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {},
                event_id=str(item.get("event_id", "") or ""),
            )
            # busy is transient; the exact same event must be retried. A stale
            # run is deliberately discarded after a newer user turn won it.
            if result is not None and getattr(result, "status", "") != "busy":
                if pending and pending[0] is item:
                    pending.popleft()
        else:
            self._run_v12_signal("autonomous_signal")
        return bool(getattr(self.host, "running", True))


__all__ = [
    "AIChatOrchestrator",
    "AIAutonomousOrchestrator",
    "architecture_c_enabled",
]
