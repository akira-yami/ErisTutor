# -*- coding: utf-8 -*-
"""Điều phối perception cho một lượt chat.

Luồng duy nhất:

1. luôn đọc OCR/UIA cache tươi làm ambient context;
2. ưu tiên structured evidence khi nguồn domain thật sự phủ câu hỏi;
3. chỉ refresh OCR khi user đang trỏ màn hình;
4. OCR mơ hồ ở lượt N chỉ đặt cờ, vision chạy ở lượt N+1;
5. pending vision bị hủy nếu structured source mới đã đủ trả lời.

Module này lấy phần điều phối cảm giác ra khỏi ``ChatCycleMixin``. Vòng chat giờ
chỉ nhận kết quả và chuyển tiếp sang grounding, thay vì vừa làm router, cache,
TTL, camera và trọng tài domain. Một class có năm nghề thường không kết thúc đẹp.
"""
from __future__ import annotations
from core.di import resolve_dependency

import os
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from runtime.cognition.normalization import normalize_vi
from runtime.structured_evidence import StructuredEvidenceContext, StructuredEvidenceProvider
from screen.evidence_models import PendingVisionEscalation, ScreenRouteDecision
from screen.ocr_config import screen_ocr_enabled
from screen.screen_anaphora_rescue import ScreenAnaphoraRescue


@dataclass(frozen=True, slots=True)
class ChatPerceptionResult:
    """Kết quả perception bất biến của một lượt chat."""

    data_uri: str = ""
    route: Any | None = None
    screen_blocks: tuple[object, ...] = ()
    structured_facts: tuple[dict[str, object], ...] = ()
    structured_contexts: tuple[StructuredEvidenceContext, ...] = ()
    structured_relevant: bool = False
    capture_present: bool = False
    capture_interpreted: bool = False


class ChatPerceptionCoordinator:
    """Điểm tập trung cho OCR, route, structured evidence và vision escalation."""

    def __init__(
        self,
        *,
        screen_evidence_store: Any | None = None,
        screen_text_worker: Any | None = None,
        screen_relevance_router: Any | None = None,
        structured_provider: StructuredEvidenceProvider | None = None,
        logger: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.screen_evidence_store = screen_evidence_store
        self.screen_text_worker = screen_text_worker
        self.screen_relevance_router = screen_relevance_router
        self.structured_provider = resolve_dependency(structured_provider, lambda: StructuredEvidenceProvider(logger=logger))
        self.logger = logger
        self.clock = clock or time.time
        self.pending_vision = PendingVisionEscalation()

    def update_dependencies(
        self,
        *,
        screen_evidence_store: Any | None = None,
        screen_text_worker: Any | None = None,
        screen_relevance_router: Any | None = None,
        structured_provider: StructuredEvidenceProvider | None = None,
    ) -> None:
        """Cho bootstrap cập nhật dependency mà không dựng coordinator thứ hai."""

        if screen_evidence_store is not None:
            self.screen_evidence_store = screen_evidence_store
        if screen_text_worker is not None:
            self.screen_text_worker = screen_text_worker
        if screen_relevance_router is not None:
            self.screen_relevance_router = screen_relevance_router
        if structured_provider is not None:
            self.structured_provider = structured_provider

    def looks_like_screen_request(self, user_text: str, *, previous_assistant_text: str = "") -> bool:
        # Typed intent is shared with ChatCycle/WorldState.  Keep only anaphora
        # rescue here because it depends on the previous assistant utterance.
        try:
            from runtime.intent_model import normalize_intent
            intent = normalize_intent(user_text)
            if intent.environment_sufficient:
                return False
            if intent.domain == "screen":
                return True
        except Exception:
            pass
        resolution = ScreenAnaphoraRescue().resolve(
            user_text,
            previous_assistant_text=previous_assistant_text,
            explicit_reference=False,
        )
        return bool(resolution.references_screen)

    def ambient_screen_blocks(self, *, max_age_seconds: float = 8.0, max_items: int = 8) -> tuple[object, ...]:
        """Đọc cache tươi; tuyệt đối không ép OCR ở chat thường."""

        store = self.screen_evidence_store
        ambient = getattr(store, "ambient_blocks", None)
        if callable(ambient):
            try:
                return tuple(ambient(max_age_seconds=max_age_seconds, max_items=max_items) or ())
            except Exception as exc:
                self._log("debug", "[screen text] ambient cache read failed: %s", exc)
        snapshot_fn = getattr(store, "snapshot", None)
        if not callable(snapshot_fn):
            return ()
        try:
            snapshot = snapshot_fn()
        except Exception:
            return ()
        if bool(getattr(snapshot, "privacy_blocked", False)):
            return ()
        now = self.clock()
        blocks = [
            block
            for block in tuple(getattr(snapshot, "blocks", ()) or ())
            if now - float(getattr(block, "captured_at", 0.0) or 0.0) <= max_age_seconds
            and str(getattr(block, "text", "") or "").strip()
        ]
        blocks.sort(
            key=lambda block: (
                float(getattr(block, "confidence", 0.0) or 0.0),
                float(getattr(block, "captured_at", 0.0) or 0.0),
            ),
            reverse=True,
        )
        return tuple(blocks[:max_items])

    @staticmethod
    def merge_screen_blocks(*groups: Any) -> tuple[object, ...]:
        merged: list[object] = []
        seen: set[str] = set()
        for group in groups:
            for block in tuple(group or ()):
                key = str(
                    getattr(block, "block_id", "")
                    or getattr(block, "content_hash", "")
                    or getattr(block, "text", "")
                    or id(block)
                ).strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(block)
        return tuple(merged)

    def structured_contexts(self, user_text: str, *, max_age_seconds: float = 30.0) -> tuple[StructuredEvidenceContext, ...]:
        return self.structured_provider.contexts_for_turn(user_text, max_age_seconds=max_age_seconds)

    def structured_facts(self, user_text: str, *, max_age_seconds: float = 30.0) -> tuple[dict[str, object], ...]:
        return self.structured_provider.evidence_for_turn(user_text, max_age_seconds=max_age_seconds)

    def has_fresh_structured_answer(self, user_text: str, *, max_age_seconds: float = 30.0) -> bool:
        return self.structured_provider.has_fresh_relevant_state(user_text, max_age_seconds=max_age_seconds)

    def active_engagement_id(self, *, engagement_kind: str = "") -> str:
        return self.structured_provider.active_engagement_id(engagement_kind=engagement_kind)

    def window_identity(self) -> tuple[str, int]:
        snapshot_fn = getattr(self.screen_evidence_store, "snapshot", None)
        if not callable(snapshot_fn):
            return "", 0
        try:
            snapshot = snapshot_fn()
        except Exception:
            return "", 0
        window = getattr(snapshot, "window", None)
        key = "|".join(
            (
                str(getattr(window, "process_name", "") or "").strip().lower(),
                str(getattr(window, "hwnd", 0) or 0),
                str(getattr(window, "title", "") or "").strip().lower(),
            )
        )
        return key, int(getattr(snapshot, "revision", 0) or 0)

    def queue_next_turn_vision(self, *, reason: str, user_text: str) -> None:
        now = self.clock()
        try:
            ttl = max(
                8.0,
                min(90.0, float(os.environ.get("ERIS_CHAT_VISION_PENDING_TTL_SEC", "30") or 30.0)),
            )
        except (TypeError, ValueError):
            ttl = 30.0
        window_key, revision = self.window_identity()
        pending = self.pending_vision
        pending.pending = True
        pending.reason = str(reason or "ocr_ambiguous")[:160]
        pending.requested_at = now
        pending.expires_at = now + ttl
        pending.window_key = window_key
        pending.screen_revision = revision
        pending.engagement_id = self.active_engagement_id()
        pending.query_hint = " ".join(str(user_text or "").split())[:240]
        self._log(
            "info",
            "[vision pending] queued reason=%s ttl=%.0fs revision=%d",
            pending.reason,
            ttl,
            revision,
        )

    def consume_next_turn_vision(self, *, user_text: str) -> bool:
        pending = self.pending_vision
        now = self.clock()
        if not pending.active(now=now):
            pending.clear()
            return False

        # Structured state tươi và đúng câu hỏi thắng screenshot đã xếp hàng.
        if self.has_fresh_structured_answer(user_text):
            pending.clear()
            self._log("info", "[vision pending] cancelled reason=fresh_structured_evidence")
            return False

        current_window, _revision = self.window_identity()
        current_engagement = self.active_engagement_id()
        same_window = bool(not pending.window_key or pending.window_key == current_window)
        same_engagement = bool(
            pending.engagement_id
            and current_engagement
            and pending.engagement_id == current_engagement
        )
        if not same_window and not same_engagement:
            pending.clear()
            self._log("info", "[vision pending] dropped reason=context_changed")
            return False

        reason = pending.reason
        pending.clear()
        self._log("info", "[vision pending] consumed reason=%s", reason or "ocr_ambiguous")
        return True

    def ocr_needs_next_turn_vision(self, route: Any | None) -> bool:
        if route is None or not bool(getattr(route, "references_screen", False)):
            return False
        if bool(getattr(route, "needs_visual", False)):
            return False
        mode = str(getattr(route, "mode", "none") or "none")
        evidence = tuple(getattr(route, "evidence", ()) or ())
        confidence = float(getattr(route, "confidence", 0.0) or 0.0)
        useful_chars = sum(
            len("".join(str(getattr(block, "text", "") or "").split()))
            for block in evidence[:6]
        )
        try:
            threshold = max(
                0.48,
                min(
                    0.78,
                    float(os.environ.get("ERIS_CHAT_OCR_VISION_ESCALATION_CONFIDENCE", "0.62") or 0.62),
                ),
            )
        except (TypeError, ValueError):
            threshold = 0.62
        return bool(
            mode == "vision"
            or not evidence
            or (mode == "text_evidence_soft" and confidence < threshold)
            or useful_chars < 16
        )

    def ensure_screen_text_worker(self) -> None:
        worker = self.screen_text_worker
        if worker is None:
            return
        try:
            worker.start()
        except Exception as exc:
            self._log("warning", "[screen text] lazy start error: %s", exc)

    def refresh_screen_text(self) -> bool:
        """Làm mới ngay một lượt UIA khi user thật sự hỏi màn hình.

        Chỉ ép OCR khi ``ERIS_SCREEN_OCR_ENABLED`` bật; mặc định tắt thì chat
        không bao giờ truyền ``force_ocr=True`` và vision lo phần nhìn.
        """

        refresh = getattr(self.screen_text_worker, "refresh_now", None)
        if not callable(refresh):
            return False
        try:
            snapshot = refresh(force_ocr=screen_ocr_enabled())
        except TypeError:
            snapshot = refresh()
        except Exception as exc:
            self._log("warning", "[screen text] chat refresh error: %s", exc)
            return False
        if snapshot is None:
            return False
        self._log(
            "info",
            "[screen text] chat refresh revision=%s backend=%s blocks=%d privacy=%s",
            int(getattr(snapshot, "revision", 0) or 0),
            str(getattr(snapshot, "backend", "unknown") or "unknown"),
            len(tuple(getattr(snapshot, "blocks", ()) or ())),
            int(bool(getattr(snapshot, "privacy_blocked", False))),
        )
        return True

    def prepare_turn(
        self,
        user_text: str,
        *,
        previous_assistant_text: str = "",
        force_chat_vision: bool = False,
        capture_fn: Callable[[], str] | None = None,
        interpret_fn: Callable[[str], Any] | None = None,
    ) -> ChatPerceptionResult:
        """Chạy evidence ladder và trả một kết quả duy nhất cho ChatCycle."""

        ambient_blocks = self.ambient_screen_blocks()
        pending_force_vision = self.consume_next_turn_vision(user_text=user_text)
        explicit_request = self.looks_like_screen_request(
            user_text,
            previous_assistant_text=previous_assistant_text,
        )
        structured_contexts = self.structured_contexts(user_text)
        structured_relevant = any(context.usable_for_turn for context in structured_contexts)
        structured_facts = tuple(
            item for context in structured_contexts for item in context.evidence
        )

        if explicit_request and not structured_relevant:
            self.ensure_screen_text_worker()

        route = self._route(user_text, previous_assistant_text=previous_assistant_text)
        if (
            not structured_relevant
            and route is not None
            and bool(getattr(route, "references_screen", False))
            and (not tuple(getattr(route, "evidence", ()) or ()) or bool(getattr(route, "stale", False)))
            and self.refresh_screen_text()
        ):
            route = self._route(user_text, previous_assistant_text=previous_assistant_text)

        route_mode = str(getattr(route, "mode", "none") or "none") if route is not None else "none"
        if route is None or (route_mode == "none" and explicit_request):
            if route is None and not explicit_request and not force_chat_vision and not pending_force_vision:
                self._log("debug", "[screen route] mode=none reason=no_screen_reference")
                return ChatPerceptionResult(
                    route=None,
                    screen_blocks=ambient_blocks,
                    structured_facts=structured_facts,
                    structured_contexts=structured_contexts,
                    structured_relevant=structured_relevant,
                )
            route = self._forced_route(
                previous=route,
                pending_force_vision=pending_force_vision,
                reason="pending_vision_escalation" if pending_force_vision else "explicit_request_overrode_none_route",
            )

        if pending_force_vision and str(getattr(route, "mode", "none") or "none") != "vision":
            route = self._forced_route(
                previous=route,
                pending_force_vision=True,
                reason="pending_vision_escalation",
            )

        route_blocks = tuple(getattr(route, "evidence", ()) or ())
        screen_blocks = self.merge_screen_blocks(ambient_blocks, route_blocks)
        self._log_route(route, route_blocks=route_blocks, ambient_blocks=ambient_blocks)

        if structured_relevant:
            # Structured source chỉ thắng khi chính source xác nhận nó phủ câu hỏi.
            # Câu hỏi về màu highlight/bố cục vẫn đi vision vì FEN không biết màu UI.
            self.pending_vision.clear()
            source_ids = ",".join(
                context.source_id for context in structured_contexts if context.usable_for_turn
            )
            self._log("info", "[structured route] selected sources=%s; vision skipped", source_ids or "unknown")
            return ChatPerceptionResult(
                route=route,
                screen_blocks=screen_blocks,
                structured_facts=structured_facts,
                structured_contexts=structured_contexts,
                structured_relevant=True,
            )

        mode = str(getattr(route, "mode", "none") or "none")
        should_capture_now = bool(
            force_chat_vision
            or pending_force_vision
            or (mode == "vision" and bool(getattr(route, "needs_visual", False)))
        )
        if not should_capture_now and self.ocr_needs_next_turn_vision(route):
            self.queue_next_turn_vision(
                reason=str(getattr(route, "reason", "ocr_ambiguous") or "ocr_ambiguous"),
                user_text=user_text,
            )
            return ChatPerceptionResult(
                route=route,
                screen_blocks=screen_blocks,
                structured_facts=structured_facts,
                structured_contexts=structured_contexts,
            )
        if not should_capture_now:
            return ChatPerceptionResult(
                route=route,
                screen_blocks=screen_blocks,
                structured_facts=structured_facts,
                structured_contexts=structured_contexts,
            )
        if mode != "vision" and force_chat_vision:
            self._log("info", "[vision chat] forcing main vision despite route mode=%s", mode)

        if not callable(capture_fn):
            self._log("info", "[vision chat] telemetry service không hỗ trợ chụp màn hình.")
            if pending_force_vision:
                self.queue_next_turn_vision(reason="pending_capture_unavailable", user_text=user_text)
            return ChatPerceptionResult(route=route, screen_blocks=screen_blocks, structured_facts=structured_facts)
        try:
            data_uri = str(capture_fn() or "").strip()
        except Exception as exc:
            self._log("warning", "[vision chat] không chụp được màn hình: %s", exc)
            if pending_force_vision:
                self.queue_next_turn_vision(reason="pending_capture_failed", user_text=user_text)
            return ChatPerceptionResult(route=route, screen_blocks=screen_blocks, structured_facts=structured_facts)
        if not data_uri:
            self._log("warning", "[vision chat] screen capture trả dữ liệu rỗng")
            if pending_force_vision:
                self.queue_next_turn_vision(reason="pending_capture_empty", user_text=user_text)
            return ChatPerceptionResult(route=route, screen_blocks=screen_blocks, structured_facts=structured_facts)

        self._log("info", "[vision chat] captured=1 bytes=%d", len(data_uri))
        capture_interpreted = False
        report = None
        if callable(interpret_fn):
            try:
                report = interpret_fn(data_uri)
                grounded_count = len(tuple(getattr(report, "grounded_claims", ()) or ())) if report is not None else 0
                capture_interpreted = grounded_count > 0
                self._log(
                    "info",
                    "[vision chat] interpreted=%s grounded_claims=%d recommendation=%s source=%s",
                    int(capture_interpreted),
                    grounded_count,
                    str(getattr(report, "recommendation", "none") or "none"),
                    str(getattr(report, "source", "unknown") or "unknown"),
                )
            except Exception as exc:
                self._log("warning", "[vision chat] không nén được screenshot: %s", exc)
        return ChatPerceptionResult(
            data_uri=data_uri,
            route=route,
            screen_blocks=screen_blocks,
            structured_facts=structured_facts,
            structured_contexts=structured_contexts,
            capture_present=True,
            capture_interpreted=capture_interpreted,
        )

    def build_screen_grounding_items(
        self,
        *,
        screen_blocks: tuple[object, ...],
        route: Any | None,
        vision_report: Any | None,
        screen_image_data_uri: str,
        capture_interpreted: bool,
    ) -> tuple[object, ...]:
        """Chuẩn hóa ambient OCR và grounded vision thành provenance items."""

        now = self.clock()
        blocks = self.merge_screen_blocks(
            self.ambient_screen_blocks(),
            screen_blocks,
            tuple(getattr(route, "evidence", ()) or ()) if route is not None else (),
        )
        items: list[object] = []
        seen: set[str] = set()
        for block in blocks:
            to_item = getattr(block, "to_grounding_item", None)
            item = None
            if callable(to_item):
                try:
                    item = dict(to_item(now=now, stale_after=8.0))
                except Exception:
                    item = None
            if item is None:
                text = str(getattr(block, "text", "") or "").strip()
                if not text:
                    continue
                captured_at = float(getattr(block, "captured_at", 0.0) or 0.0)
                item = {
                    "id": str(getattr(block, "block_id", "screen_text") or "screen_text"),
                    "source": str(getattr(block, "source", "screen_text_index") or "screen_text_index"),
                    "claim": text,
                    "confidence": float(getattr(block, "confidence", 0.65) or 0.65),
                    "grounded": True,
                    "stale": bool(captured_at and now - captured_at > 8.0),
                    "captured_at": captured_at,
                    "tags": ["screen", "screen_text", "ocr_ambient"],
                }
            item.setdefault("role", "ambient")
            tags = list(item.get("tags", ()) or ())
            if "ocr_ambient" not in tags:
                tags.append("ocr_ambient")
            item["tags"] = tags
            item_id = str(item.get("id", "") or item.get("claim", ""))
            if item_id in seen:
                continue
            seen.add(item_id)
            items.append(item)

        if vision_report is not None and screen_image_data_uri and not bool(getattr(vision_report, "stale", False)):
            to_items = getattr(vision_report, "to_evidence_items", None)
            if callable(to_items):
                try:
                    for raw in tuple(to_items(grounded_only=True) or ()):
                        item = dict(raw) if isinstance(raw, dict) else raw
                        item_id = str(item.get("id", "") if isinstance(item, dict) else getattr(item, "id", ""))
                        if item_id and item_id in seen:
                            continue
                        if item_id:
                            seen.add(item_id)
                        items.append(item)
                except Exception:
                    pass

        if screen_image_data_uri and not capture_interpreted:
            items.append({
                "id": "chat_screen_frame_unresolved",
                "source": "screen_capture",
                "claim": "fresh screenshot captured but no grounded semantic claim",
                "confidence": 0.25,
                "grounded": False,
                "stale": False,
                "tags": ["vision", "screen", "chat", "ungrounded"],
            })
        return tuple(items)

    @staticmethod
    def grounding_items_stale(items: tuple[object, ...]) -> bool:
        grounded: list[object] = []
        for item in tuple(items or ()):
            marker = item.get("grounded") if isinstance(item, dict) else getattr(item, "grounded", None)
            if marker is False or str(marker).strip().lower() == "false":
                continue
            grounded.append(item)
        if not grounded:
            return False
        return all(
            bool(item.get("stale", False)) if isinstance(item, dict) else bool(getattr(item, "stale", False))
            for item in grounded
        )

    def _route(self, user_text: str, *, previous_assistant_text: str) -> Any | None:
        router = self.screen_relevance_router
        if router is None:
            return None
        try:
            return router.route(user_text, previous_assistant_text=previous_assistant_text)
        except TypeError:
            try:
                return router.route(user_text)
            except Exception as exc:
                self._log("warning", "[screen route] router lỗi: %s", exc)
                return None
        except Exception as exc:
            self._log("warning", "[screen route] router lỗi: %s", exc)
            return None

    @staticmethod
    def _forced_route(*, previous: Any | None, pending_force_vision: bool, reason: str) -> Any:
        kwargs = {
            "mode": "vision",
            "references_screen": True,
            "needs_visual": bool(pending_force_vision),
            "requires_strong_evidence": bool(getattr(previous, "requires_strong_evidence", False)),
            "reason": reason,
            "evidence": tuple(getattr(previous, "evidence", ()) or ()) if previous is not None else (),
            "confidence": float(getattr(previous, "confidence", 0.70) or 0.70) if previous is not None else 0.70,
            "stale": bool(getattr(previous, "stale", False)) if previous is not None else False,
            "debug": dict(getattr(previous, "debug", {}) or {}) if previous is not None else {},
        }
        try:
            return ScreenRouteDecision(**kwargs)
        except Exception:
            return SimpleNamespace(**kwargs)

    def _log_route(self, route: Any, *, route_blocks: tuple[object, ...], ambient_blocks: tuple[object, ...]) -> None:
        debug = dict(getattr(route, "debug", {}) or {})
        self._log(
            "info",
            "[screen route] mode=%s refs=%s visual=%s evidence=%d ambient=%d confidence=%.2f stale=%s reason=%s anaphora=%s",
            str(getattr(route, "mode", "none") or "none"),
            int(bool(getattr(route, "references_screen", False))),
            int(bool(getattr(route, "needs_visual", False))),
            len(route_blocks),
            len(ambient_blocks),
            float(getattr(route, "confidence", 0.0) or 0.0),
            int(bool(getattr(route, "stale", False))),
            str(getattr(route, "reason", "") or "none"),
            int(bool(debug.get("anaphora_inherited", False))),
        )

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = ["ChatPerceptionCoordinator", "ChatPerceptionResult"]
