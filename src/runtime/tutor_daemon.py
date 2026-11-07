# -*- coding: utf-8 -*-
"""
runtime/tutor_daemon.py
=======================

Orchestrator vòng đời Tutor_Episode (ngoài hot path) + phát Popup_Event cho body.

Mọi exception bị bắt trong `run_once`: một lỗi của Tutor KHÔNG bao giờ được phép ném vào
CognitivePipeline/autonomous/chat.
"""

from __future__ import annotations
from core.di import resolve_dependency

import logging
import threading
import time
from typing import Any, Callable, Optional

try:  # pragma: no cover
    from cortex.tutor.config import TutorConfig, TutorLogger, TutorRiskGuard, scrub_identity
    from cortex.tutor.contracts import KnowledgeGap, TutorEpisode, TutorQuestion, _MAX_QUESTION_LEN
except Exception:  # pragma: no cover
    from src.cortex.tutor.config import TutorConfig, TutorLogger, TutorRiskGuard, scrub_identity  # type: ignore
    from src.cortex.tutor.contracts import KnowledgeGap, TutorEpisode, TutorQuestion, _MAX_QUESTION_LEN  # type: ignore


_QUESTION_PREVIEW_LEN = 120


_LOG = logging.getLogger(__name__)


def _report_suppressed(logger: Any, event: str, exc: BaseException) -> None:
    """Record a recoverable tutor boundary without letting it escape."""

    warning = getattr(logger, "warn", None) or getattr(logger, "warning", None)
    if callable(warning):
        try:
            warning(f"{event}: {type(exc).__name__}: {exc}")
            return
        except Exception as logger_exc:
            _LOG.debug("tutor logger failed event=%s error=%s", event, logger_exc, exc_info=True)
    _LOG.debug("tutor boundary degraded event=%s error=%s", event, exc, exc_info=True)


class _CombinedCancellation:
    """View read-only của nhiều cancellation event cho đúng một request."""

    def __init__(self, *events: Any) -> None:
        self._events = tuple(event for event in events if event is not None)

    def is_set(self) -> bool:
        return any(bool(getattr(event, "is_set", lambda: False)()) for event in self._events)


# =============================================================================
# Popup_Event emitter (UDP 4242 → body)
# =============================================================================

class TutorStageEmitter:
    """Gửi Popup_Event qua transport. Lỗi gửi không bao giờ chặn episode."""

    def __init__(self, transport: Any = None, logger: Optional[TutorLogger] = None) -> None:
        self._transport = transport
        self._logger = logger

    def emit(self, episode: TutorEpisode, stage: str) -> None:
        try:
            question_preview = ""
            question_full = ""
            if episode.question is not None:
                # Hiển thị TRỌN câu hỏi (đúng giới hạn hợp lệ của contract), không
                # cắt ở 280 — câu formulator cho phép tới ~60 từ nên 280 hay làm
                # panel hiện câu hỏi cụt giữa chừng, dễ tưởng là "gửi bị ngắt".
                question_full = scrub_identity(episode.question.text)[:_MAX_QUESTION_LEN]
                question_preview = question_full[:_QUESTION_PREVIEW_LEN]
            answer_preview = ""
            if episode.answer is not None and getattr(episode.answer, "text", ""):
                answer_preview = scrub_identity(episode.answer.text)[:400]
            payload = {
                "parse_mode": "tutor_event",
                "should_speak": False,
                "animation": "",
                "tutor": {
                    "episode_id": episode.episode_id,
                    "stage": stage,
                    "question_preview": question_preview,
                    "question": question_full,
                    "answer": answer_preview,
                    "claims": int(episode.claims_committed),
                    "fail_reason": episode.fail_reason or "",
                },
            }
            if self._logger is not None:
                self._logger.log_stage(episode.episode_id, stage)
            transport = self._transport
            if transport is None:
                return
            send = getattr(transport, "send_json", None)
            if callable(send):
                send(payload)
        except Exception as exc:
            # Diễn cảnh là phụ; không được chặn việc học.
            _report_suppressed(self._logger, "popup emit failed", exc)

    def emit_research_status(
        self,
        *,
        episode: TutorEpisode,
        stage: str,
        mode: str,
        topic: str = "",
        verification: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Gửi status riêng cho panel research/search.

        Body cũ không hiểu field `research` thì bỏ qua; body mới có thể hiện
        “đang hỏi Tutor”, “đang kiểm chứng”, “xong nhưng nguồn yếu”. Một event
        nhỏ thôi, không cần dựng rạp xiếc websocket.
        """

        try:
            payload = {
                "parse_mode": "tutor_event",
                "should_speak": False,
                "animation": "",
                "tutor": {
                    "episode_id": episode.episode_id,
                    "stage": stage,
                    "question_preview": scrub_identity(topic)[:_QUESTION_PREVIEW_LEN],
                    "question": scrub_identity(topic)[:_MAX_QUESTION_LEN],
                    "answer": "",
                    "claims": int(getattr(episode, "claims_committed", 0) or 0),
                    "fail_reason": getattr(episode, "fail_reason", "") or "",
                },
                "research": {
                    "mode": mode,
                    "stage": stage,
                    "topic": scrub_identity(topic)[:260],
                    "verification": dict(verification or {}),
                    "metrics": dict(metrics or {}),
                },
            }
            if self._logger is not None:
                self._logger.log_stage(episode.episode_id, f"research:{stage}")
            transport = self._transport
            if transport is None:
                return
            send = getattr(transport, "send_json", None)
            if callable(send):
                send(payload)
        except Exception as exc:
            _report_suppressed(self._logger, "research status emit failed", exc)


# =============================================================================
# Daemon
# =============================================================================

class TutorDaemon:
    """Chạy vòng đời một Tutor_Episode mỗi `run_once` khi đủ điều kiện."""

    def __init__(
        self,
        config: Optional[TutorConfig] = None,
        *,
        collector: Any,
        gate: Any,
        formulator: Any,
        backend: Any,
        digest: Any,
        share_impulse: Any = None,
        classifier: Any = None,
        router: Any = None,
        emitter: Optional[TutorStageEmitter] = None,
        risk_guard: Optional[TutorRiskGuard] = None,
        logger: Optional[TutorLogger] = None,
        drive_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = resolve_dependency(config, TutorConfig).normalized()
        self.collector = collector
        self.gate = gate
        self.formulator = formulator
        self.backend = backend
        self.digest = digest
        self.share_impulse = share_impulse
        self.classifier = classifier
        self.router = router
        self.emitter = resolve_dependency(emitter, lambda: TutorStageEmitter())
        self.risk_guard = resolve_dependency(risk_guard, lambda: TutorRiskGuard(self.config))
        self.logger = resolve_dependency(logger, lambda: TutorLogger())
        self._drive_fn = drive_fn
        # Serialize episode: KHÔNG mở 2 browser cùng profile cùng lúc (autonomous vs chat).
        self._run_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._cancel_reason = ""
        # ContentEngine đọc các trường này để tận dụng "sư phụ Gemini" như một
        # nguồn seed thật, không cần mở browser hay hỏi lại mỗi lượt.
        self.last_episode: Optional[TutorEpisode] = None
        self.last_answer_text: str = ""
        self.last_question_text: str = ""
        self.last_answer_ts: float = 0.0
        self.last_research_bundle: dict[str, Any] = {}
        self.last_research_thread: dict[str, Any] = {}
        self.last_research_metrics: dict[str, Any] = {}
        self.last_research_memory_decision: dict[str, Any] = {}

    # ------------------------------------------------------------------

    def run_once(self, *, idle: bool, now: Optional[float] = None) -> Optional[TutorEpisode]:
        if not self.config.enabled:
            return None
        # Một episode tại một thời điểm. Bận thì bỏ qua (không xếp hàng chờ browser).
        if not self._run_lock.acquire(blocking=False):
            return None
        try:
            self._begin_request()
            return self._run_once_locked(idle=idle, now=now)
        finally:
            self._run_lock.release()

    def _run_once_locked(self, *, idle: bool, now: Optional[float] = None) -> Optional[TutorEpisode]:
        now = float(now if now is not None else time.time())
        if self.risk_guard.is_paused(now):
            return None

        try:
            candidates = self.collector.drain_candidates()
            # Forced gap (lệnh trực tiếp / "không biết") bỏ qua idle + rate-limit.
            gap = self.gate.select_forced(candidates) if hasattr(self.gate, "select_forced") else None
            if gap is None:
                gap = self.gate.select(
                    candidates,
                    idle=idle,
                    now=now,
                    drive_level=self._drive_level(),
                )
        except Exception as exc:
            _report_suppressed(self.logger, "gap selection failed", exc)
            return None
        if gap is None:
            return None

        # Gap rỗng (không mô tả, không ngữ cảnh) thì không có gì để hỏi —
        # bỏ qua ngay, KHÔNG bật thẻ "đang nghĩ câu hỏi" rồi hụt. Gap forced
        # (lệnh trực tiếp) vẫn cho qua vì formulator có fallback.
        if not getattr(gap, "force", False):
            if not (str(getattr(gap, "description", "") or "").strip()
                    or str(getattr(gap, "context", "") or "").strip()):
                return None

        episode = TutorEpisode(gap=gap, started_ts=now)
        self.gate.note_started(now)
        self._emit(episode, "forming")

        # 0. Định tuyến tò mò: skip (đã biết) / ask_user (về bạn) / ask_gemini (thế giới).
        action = "ask_gemini"
        route_reason = ""
        if self.router is not None:
            try:
                action, route_reason = self.router.route(gap)
            except Exception:
                action = "ask_gemini"
                route_reason = ""
        if action == "skip":
            # Tách rõ "đã biết rồi" với "vụn vặt không đáng hỏi" để nhãn không đánh lừa.
            skip_reason = "gate_rejected_known" if route_reason == "already_known" else "gate_rejected_trivial"
            return self._fail(episode, skip_reason, now)
        if action == "ask_user":
            return self._ask_user(episode, gap, now)

        # 1. Hình thành câu hỏi (không mở web nếu thất bại).
        try:
            question = self.formulator.formulate(gap)
        except Exception:
            question = None
        if question is None or not getattr(question, "is_valid", False):
            return self._fail(episode, "formulation_failed", now)
        episode.question = question

        # 2. Hỏi gia sư (backend tự emit opening/asking/reading qua callback).
        try:
            answer = self.backend.ask(
                question,
                on_stage=lambda s: self._emit(episode, s),
                cancel_event=self._cancel_event,
            )
        except Exception:
            return self._fail(episode, "network_error", now)
        if self._cancel_event.is_set():
            return self._fail(episode, "cancelled", now)
        episode.answer = answer
        if answer is None or not getattr(answer, "ok", False) or not getattr(answer, "text", ""):
            reason = getattr(answer, "fail_reason", None) or "empty_answer"
            return self._fail(episode, reason, now)

        # 3. Phân loại + định tuyến: durable lưu (embedding); context/banter ephemeral.
        kind = "durable"
        if self.classifier is not None:
            try:
                kind = self.classifier.classify(
                    question.text, answer.text,
                    gap_source=gap.source, kind_hint=getattr(gap, "kind_hint", ""),
                )
            except Exception:
                kind = "durable"

        if kind == "durable":
            self._emit(episode, "digesting")
            try:
                episode.claims_committed = self.digest.digest(episode)
            except Exception:
                episode.claims_committed = 0
            if episode.claims_committed > 0:
                try:
                    self.collector.resolve(gap)
                except Exception as exc:
                    _report_suppressed(self.logger, "gap resolution failed", exc)
                if self.share_impulse is not None:
                    try:
                        self.share_impulse.maybe_emit(episode, now=now)
                    except Exception as exc:
                        _report_suppressed(self.logger, "share impulse failed", exc)
        else:
            # context / banter: KHÔNG lưu trí nhớ dài hạn. Đẩy một câu ngắn cho vui/đoán.
            try:
                self.collector.resolve(gap)
            except Exception as exc:
                _report_suppressed(self.logger, "ephemeral gap resolution failed", exc)
            if self.share_impulse is not None:
                try:
                    remark = ""
                    formulator = getattr(self.share_impulse, "_formulator", None)
                    if formulator is not None and callable(getattr(formulator, "craft_remark", None)):
                        remark = formulator.craft_remark(episode, kind=kind)
                    if remark:
                        self.share_impulse.push_remark(remark, now=now, priority=0.6)
                        episode.shared = True
                except Exception as exc:
                    _report_suppressed(self.logger, "ephemeral remark failed", exc)

        episode.stage = "done"
        episode.ended_ts = float(time.time())
        self._emit(episode, "done")
        self.logger.log_episode(episode.to_dict())
        self.risk_guard.note_success(now)
        self._remember_episode(episode)
        return episode

    def ask_now(self, topic: str, *, context: str = "", now: Optional[float] = None) -> Optional[str]:
        """Hỏi gia sư NGAY và TRẢ VỀ nội dung câu trả lời (cho nhịp 2 của chat).

        Khác `run_once`: đây là lúc BẠN hỏi thẳng một câu kiến thức ⇒ luôn đi
        ask_gemini (bỏ qua router skip/ask_user) và trả lại text để chat nói ra.
        Vẫn dưới `_run_lock` (không mở 2 browser cùng lúc), vẫn tôn trọng risk_guard
        và Master_Switch. Mọi lỗi nuốt sạch → trả None (caller xử lý "tra không ra").
        """

        if not self.config.enabled:
            return None
        topic = str(topic or "").strip()
        if not topic:
            return None
        # Bận (autonomous/episode khác đang mở browser) ⇒ không xếp hàng, trả None.
        if not self._run_lock.acquire(blocking=False):
            return None
        try:
            self._begin_request()
            now = float(now if now is not None else time.time())
            if self.risk_guard.is_paused(now):
                return None

            gap = KnowledgeGap(
                description=topic,
                context=str(context or ""),
                source="epistemic_uncertainty",
                stake_relevance=0.7,
                prediction_error=0.7,
                created_ts=now,
                updated_ts=now,
                force=True,
                kind_hint="durable",
            )
            episode = TutorEpisode(gap=gap, started_ts=now)
            self._emit(episode, "forming")

            # Hình thành câu hỏi; fallback hỏi thẳng topic nếu formulator hụt.
            try:
                question = self.formulator.formulate(gap)
            except Exception:
                question = None
            if question is None or not getattr(question, "is_valid", False):
                question = TutorQuestion(text=topic[:_MAX_QUESTION_LEN], gap_id=gap.gap_id)
            episode.question = question

            try:
                answer = self.backend.ask(
                    question,
                    on_stage=lambda s: self._emit(episode, s),
                    cancel_event=self._cancel_event,
                )
            except Exception:
                self._fail(episode, "network_error", now)
                return None
            if self._cancel_event.is_set():
                self._fail(episode, "cancelled", now)
                return None
            episode.answer = answer
            if answer is None or not getattr(answer, "ok", False) or not getattr(answer, "text", ""):
                reason = getattr(answer, "fail_reason", None) or "empty_answer"
                self._fail(episode, reason, now)
                return None

            # Học luôn (durable) để lần sau khỏi tra lại. Không thành công cũng không sao.
            self._emit(episode, "digesting")
            try:
                episode.claims_committed = self.digest.digest(episode)
            except Exception:
                episode.claims_committed = 0

            episode.stage = "done"
            episode.ended_ts = float(time.time())
            self._emit(episode, "done")
            self.logger.log_episode(episode.to_dict())
            self.risk_guard.note_success(now)
            self._remember_episode(episode)
            return str(getattr(answer, "text", "") or "").strip() or None
        except Exception as exc:
            _report_suppressed(self.logger, "direct tutor request failed", exc)
            return None
        finally:
            self._run_lock.release()

    def _remember_research_artifact(self, *, topic: str, mode: str, relay: Any) -> None:
        """Lưu artifact nghiên cứu nhẹ để runtime ghi vào session ledger/panel.

        Không cần sửa dataclass TutorEpisode slots. Một slot riêng trên daemon là đủ,
        đỡ biến contracts thành cây thông Noel.
        """

        try:
            bundle_obj = getattr(relay, "evidence_bundle", None)
            bundle = bundle_obj.to_dict() if hasattr(bundle_obj, "to_dict") else {}
        except Exception:
            bundle = {}
        try:
            from cortex.tutor.discourse_research_bridge import build_research_thread_record
            from cortex.tutor.research_metrics import build_research_metrics

            thread = build_research_thread_record(topic=topic, mode=mode, bundle=bundle)
            metrics = build_research_metrics(bundle, mode=mode).to_dict()
        except Exception:
            thread = {}
            metrics = {}
        self.last_research_bundle = bundle if isinstance(bundle, dict) else {}
        self.last_research_thread = thread if isinstance(thread, dict) else {}
        self.last_research_metrics = metrics if isinstance(metrics, dict) else {}
        self.last_research_memory_decision = {}
        try:
            self.logger._emit({"kind": "research_thread", **self.last_research_thread})
            self.logger._emit({"kind": "research_metrics", **self.last_research_metrics})
        except Exception as exc:
            _report_suppressed(self.logger, "research artifact log failed", exc)

    def _emit_research_status(self, episode: TutorEpisode, stage: str, *, mode: str, topic: str, verification: dict[str, Any] | None = None) -> None:
        emitter = getattr(self, "emitter", None)
        emit_status = getattr(emitter, "emit_research_status", None)
        if callable(emit_status):
            try:
                emit_status(
                    episode=episode,
                    stage=stage,
                    mode=mode,
                    topic=topic,
                    verification=verification or {},
                    metrics=getattr(self, "last_research_metrics", {}) or {},
                )
            except Exception as exc:
                _report_suppressed(self.logger, "research status callback failed", exc)

    def research_now(
        self,
        topic: str,
        *,
        context: str = "",
        now: Optional[float] = None,
        critic_fn: Optional[Callable[[str], str]] = None,
    ) -> Optional[str]:
        """Tra cứu bằng đúng Tutor web hiện có, rồi chấm lại bằng local/open-model nếu có.

        Flow mới: local/open-model tạo prompt giao việc cho Tutor Gemini → Tutor trả raw
        answer → local/open-model/heuristic relay lọc claim yếu, nguồn, confidence → Eris
        mới surface. Vẫn KHÔNG dùng Deep Research API, Google grounding API hay
        background agent nhiều tầng. Đỡ biến một câu hỏi thành lễ hiến tế token.
        """

        if not self.config.enabled:
            return None
        topic = str(topic or "").strip()
        if not topic:
            return None
        if not self._run_lock.acquire(blocking=False):
            return None
        try:
            self._begin_request()
            from cortex.tutor.research_relay import (
                analyze_tutor_research_answer,
                build_gemini_research_prompt,
            )

            now = float(now if now is not None else time.time())
            if self.risk_guard.is_paused(now):
                return None

            gap = KnowledgeGap(
                description=topic,
                context=str(context or ""),
                source="epistemic_uncertainty",
                stake_relevance=0.78,
                prediction_error=0.72,
                created_ts=now,
                updated_ts=now,
                force=True,
                kind_hint="research_relay",
            )
            episode = TutorEpisode(gap=gap, started_ts=now)
            self._emit(episode, "forming")
            self._emit_research_status(episode, "queued", mode="research", topic=topic)

            prompt = build_gemini_research_prompt(
                topic,
                context=context,
                max_words=getattr(self.config, "research_answer_max_words", 280),
                max_chars=getattr(self.config, "research_prompt_max_chars", 395),
            )
            question = TutorQuestion(text=prompt, gap_id=gap.gap_id)
            if not question.is_valid:
                self._fail(episode, "formulation_failed", now)
                return None
            episode.question = question

            self._emit_research_status(episode, "asking_tutor", mode="research", topic=topic)
            try:
                answer = self.backend.ask(
                    question,
                    on_stage=lambda stage: self._emit(episode, stage),
                    cancel_event=self._cancel_event,
                )
            except Exception:
                self._fail(episode, "network_error", now)
                return None
            if self._cancel_event.is_set():
                self._fail(episode, "cancelled", now)
                return None
            episode.answer = answer
            if answer is None or not getattr(answer, "ok", False) or not getattr(answer, "text", ""):
                reason = getattr(answer, "fail_reason", None) or "empty_answer"
                self._fail(episode, reason, now)
                return None

            self._emit_research_status(episode, "verifying", mode="research", topic=topic)
            relay = analyze_tutor_research_answer(
                topic,
                getattr(answer, "text", ""),
                tutor_prompt=prompt,
                critic_fn=critic_fn,
                max_words=getattr(self.config, "research_answer_max_words", 280),
                mode="research",
            )
            self._remember_research_artifact(topic=topic, mode="research", relay=relay)
            self._emit_research_status(
                episode,
                "verified",
                mode="research",
                topic=topic,
                verification=(self.last_research_bundle.get("verification") or {}),
            )
            answer.text = relay.answer_text

            # Chỉ lưu khi policy cho phép. Mặc định false; nếu bật persist thì vẫn
            # phải qua verification, không cho web rác chui vào trí nhớ dài hạn.
            try:
                from cortex.tutor.research_memory_policy import should_persist_research_answer
                mem_decision = should_persist_research_answer(self.config, self.last_research_bundle, mode="research")
                self.last_research_memory_decision = mem_decision.to_dict()
            except Exception:
                mem_decision = None
                self.last_research_memory_decision = {"should_persist": False, "reason": "policy_error"}
            if bool(getattr(mem_decision, "should_persist", False)):
                self._emit(episode, "digesting")
                try:
                    episode.claims_committed = self.digest.digest(episode)
                except Exception:
                    episode.claims_committed = 0
            try:
                self.logger._emit({"kind": "research_memory_policy", **self.last_research_memory_decision})
            except Exception as exc:
                _report_suppressed(self.logger, "research memory policy log failed", exc)

            episode.stage = "done"
            episode.ended_ts = float(time.time())
            self._emit(episode, "done")
            self.logger.log_episode(episode.to_dict())
            self.risk_guard.note_success(now)
            self._remember_episode(episode)
            return relay.answer_text or None
        except Exception as exc:
            _report_suppressed(self.logger, "tutor_run_once_failed", exc)
            return None
        finally:
            self._run_lock.release()

    def search_now(
        self,
        topic: str,
        *,
        context: str = "",
        now: Optional[float] = None,
        critic_fn: Optional[Callable[[str], str]] = None,
        cancel_event: Any = None,
    ) -> Optional[str]:
        """Search web nhanh bằng Tutor Gemini, rồi guard lại trước khi surface.

        Đây là bản "search bình thường": dùng khi user nói tra cứu/search/check,
        hoặc câu có dữ kiện thời điểm như giá/lịch/phiên bản mới. Không ghi nhớ
        mặc định, không Deep Research, không Google AI API. Một cái búa nhỏ cho
        cái đinh nhỏ, cuối cùng văn minh cũng ló được cái móng tay.
        """

        if not self.config.enabled:
            return None
        topic = str(topic or "").strip()
        if not topic:
            return None
        if not self._run_lock.acquire(blocking=False):
            return None
        try:
            self._begin_request()
            request_cancel = _CombinedCancellation(self._cancel_event, cancel_event)
            if request_cancel.is_set():
                return None
            from cortex.tutor.research_relay import (
                analyze_tutor_research_answer,
                build_gemini_search_prompt,
            )

            now = float(now if now is not None else time.time())
            if self.risk_guard.is_paused(now):
                return None

            gap = KnowledgeGap(
                description=topic,
                context=str(context or ""),
                source="explicit_web_search",
                stake_relevance=0.66,
                prediction_error=0.58,
                created_ts=now,
                updated_ts=now,
                force=True,
                kind_hint="search_relay",
            )
            episode = TutorEpisode(gap=gap, started_ts=now)
            self._emit(episode, "forming")
            self._emit_research_status(episode, "queued", mode="quick_search", topic=topic)

            prompt = build_gemini_search_prompt(
                topic,
                context=context,
                max_words=min(180, int(getattr(self.config, "research_answer_max_words", 280) or 280)),
                max_chars=getattr(self.config, "research_prompt_max_chars", 395),
            )
            question = TutorQuestion(text=prompt, gap_id=gap.gap_id)
            if not question.is_valid:
                self._fail(episode, "formulation_failed", now)
                return None
            episode.question = question

            self._emit_research_status(episode, "asking_tutor", mode="quick_search", topic=topic)
            try:
                answer = self.backend.ask(
                    question,
                    on_stage=lambda stage: self._emit(episode, stage),
                    cancel_event=request_cancel,
                )
            except Exception:
                self._fail(episode, "network_error", now)
                return None
            if request_cancel.is_set():
                self._fail(episode, "cancelled", now)
                return None
            episode.answer = answer
            if answer is None or not getattr(answer, "ok", False) or not getattr(answer, "text", ""):
                reason = getattr(answer, "fail_reason", None) or "empty_answer"
                self._fail(episode, reason, now)
                return None

            self._emit_research_status(episode, "verifying", mode="quick_search", topic=topic)
            relay = analyze_tutor_research_answer(
                topic,
                getattr(answer, "text", ""),
                tutor_prompt=prompt,
                critic_fn=critic_fn,
                max_words=min(220, int(getattr(self.config, "research_answer_max_words", 280) or 280)),
                mode="quick_search",
            )
            self._remember_research_artifact(topic=topic, mode="quick_search", relay=relay)
            self._emit_research_status(
                episode,
                "verified",
                mode="quick_search",
                topic=topic,
                verification=(self.last_research_bundle.get("verification") or {}),
            )
            answer.text = relay.answer_text

            try:
                from cortex.tutor.research_memory_policy import should_persist_research_answer
                mem_decision = should_persist_research_answer(self.config, self.last_research_bundle, mode="quick_search")
                self.last_research_memory_decision = mem_decision.to_dict()
            except Exception:
                mem_decision = None
                self.last_research_memory_decision = {"should_persist": False, "reason": "policy_error"}
            if bool(getattr(mem_decision, "should_persist", False)):
                self._emit(episode, "digesting")
                try:
                    episode.claims_committed = self.digest.digest(episode)
                except Exception:
                    episode.claims_committed = 0
            try:
                self.logger._emit({"kind": "research_memory_policy", **self.last_research_memory_decision})
            except Exception as exc:
                _report_suppressed(self.logger, "research_memory_policy_emit_failed", exc)

            episode.stage = "done"
            episode.ended_ts = float(time.time())
            self._emit(episode, "done")
            self.logger.log_episode(episode.to_dict())
            self.risk_guard.note_success(now)
            self._remember_episode(episode)
            return relay.answer_text or None
        except Exception as exc:
            _report_suppressed(self.logger, "tutor_search_now_failed", exc)
            return None
        finally:
            self._run_lock.release()

    def _ask_user(self, episode: TutorEpisode, gap: Any, now: float) -> TutorEpisode:
        """Eris hỏi LẠI bạn (nói ra) thay vì lén hỏi Gemini. Không mở browser, không tab."""

        question = ""
        try:
            formulator = self.formulator
            if formulator is not None and callable(getattr(formulator, "craft_user_question", None)):
                question = formulator.craft_user_question(gap)
        except Exception:
            question = ""
        if not question:
            question = "Ơ, ý bạn là sao ạ?"

        pushed = False
        if self.share_impulse is not None:
            try:
                # Ưu tiên cao hơn khịa/khoe: đây là câu hỏi cần bạn trả lời.
                pushed = bool(self.share_impulse.push_remark(question, now=now, priority=0.9))
            except Exception:
                pushed = False
        try:
            self.collector.resolve(gap)
        except Exception as exc:
            _report_suppressed(self.logger, "ask-user gap resolution failed", exc)
        episode.shared = pushed
        episode.stage = "done"
        episode.ended_ts = float(time.time())
        self.logger.log_episode(episode.to_dict())
        self.risk_guard.note_success(now)
        return episode

    def _begin_request(self) -> None:
        self._cancel_reason = ""
        self._cancel_event.clear()

    def cancel_pending(self, *, reason: str = "cancelled") -> bool:
        """Hủy cooperative episode đang chạy, không đóng vĩnh viễn Tutor."""

        self._cancel_reason = str(reason or "cancelled")[:80]
        self._cancel_event.set()
        cancel = getattr(self.backend, "cancel_current", None)
        if callable(cancel):
            try:
                cancel(self._cancel_reason)
            except Exception as exc:
                _report_suppressed(self.logger, "tutor cancellation failed", exc)
        return self._run_lock.locked()

    def stop(self) -> None:
        self.cancel_pending(reason="tutor_stopped")
        try:
            close = getattr(self.backend, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            _report_suppressed(self.logger, "tutor backend close failed", exc)

    # ------------------------------------------------------------------

    def _drive_level(self) -> float:
        if self._drive_fn is None:
            return 1.0
        try:
            return float(self._drive_fn())
        except Exception:
            return 1.0

    def _emit(self, episode: TutorEpisode, stage: str) -> None:
        episode.stage = stage  # type: ignore[assignment]
        self.emitter.emit(episode, stage)

    def _remember_episode(self, episode: TutorEpisode) -> None:
        """Giữ lại insight cuối cho ContentHarvester.

        Không lưu raw browser state, không lưu key, chỉ giữ câu hỏi/câu trả lời
        đã scrub ngắn để lượt diễn sau có thể bám vào tri thức vừa học.
        """

        try:
            answer = getattr(episode, "answer", None)
            question = getattr(episode, "question", None)
            answer_text = scrub_identity(str(getattr(answer, "text", "") or "")).strip()
            if not answer_text:
                return
            self.last_episode = episode
            self.last_answer_text = answer_text[:1200]
            self.last_question_text = scrub_identity(str(getattr(question, "text", "") or "")).strip()[:320]
            self.last_answer_ts = float(getattr(episode, "ended_ts", 0.0) or time.time())
        except Exception as exc:
            _report_suppressed(self.logger, "remember episode failed", exc)

    def latest_content_seed(self, *, now: Optional[float] = None) -> Optional[dict[str, object]]:
        """Payload nhẹ để ContentHarvester biến thành ContentSeed.

        Đây là API nội bộ, cố tình không import ContentSeed để tránh vòng phụ
        thuộc runtime/tutor_daemon.py -> runtime/content_engine.py.
        """

        text = scrub_identity(str(self.last_answer_text or "")).strip()
        if not text:
            return None
        now_f = float(now if now is not None else time.time())
        observed = float(self.last_answer_ts or now_f)
        age = max(0.0, now_f - observed)
        question = scrub_identity(str(self.last_question_text or "")).strip()
        joined = (f"Q: {question} | A: {text}" if question else text)[:900]
        return {
            "source": "tutor_gemini",
            "text": joined,
            "evidence_level": "tool/tutor",
            "freshness": max(0.0, 1.0 - age / 7200.0),
            "confidence": 0.78,
            "risk": 0.28,
            "tags": ("tutor", "gemini", "knowledge"),
            "observed_at": observed,
            "public_ok": True,
        }

    def _fail(self, episode: TutorEpisode, reason: str, now: float) -> TutorEpisode:
        episode.stage = "failed"
        episode.fail_reason = reason  # type: ignore[assignment]
        episode.ended_ts = float(time.time())
        if reason == "cancelled":
            # User vừa lên tiếng không phải lỗi Tutor và không được kích hoạt
            # risk cooldown. Đây là preemption bình thường của scheduler.
            pass
        elif reason == "captcha_or_expired":
            self.risk_guard.note_captcha(now)
        else:
            self.risk_guard.note_failure(now, reason)
        self._emit(episode, "failed")
        self.logger.log_episode(episode.to_dict())
        return episode


__all__ = ["TutorDaemon", "TutorStageEmitter"]
