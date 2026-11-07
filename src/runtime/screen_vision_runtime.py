# -*- coding: utf-8 -*-
"""Screen evidence collection, grounding, compression and speakability policy.

Extracted from RuntimeCoordinator so the composition root stops owning every policy.
"""
from __future__ import annotations

from llm.model_catalog import LEGACY_OPEN_MODEL_MODE

import base64
import hashlib
import io
import os
import re
import time

from cortex.observer_contracts import ObserverFrame, SemanticSceneNote
from cortex.vision_semantic_compressor import RemoteCaptionClient, RemoteStructureClient
from runtime.vision_grounding import VisionGroundingPolicy, VisionGroundingReport
from llm.deepseek import supports_image_input

VISION_TRACE_MARKER = "vision_facts_attached"


class ScreenVisionRuntimeMixin:
    """Coordinator-facing mixin with no independent lifecycle."""

    def _load_vision_fact_settings(self) -> tuple[bool, int, int, int]:
        flag = os.environ.get("ERIS_VISION_FACTS", "1").strip().lower()
        enabled = flag not in {"0", "false", "no", "off"}

        # PDF annotation, page 14: ambient vision is delta-triggered. The
        # interval is only a cheap local sampling cadence, never permission to
        # call a remote captioner by itself.
        interval_raw = os.environ.get("ERIS_VISION_FACTS_INTERVAL", "45").strip()
        max_items_raw = os.environ.get("ERIS_VISION_FACTS_MAX", "3").strip()
        max_len_raw = os.environ.get("ERIS_VISION_FACTS_MAX_LEN", "120").strip()

        try:
            interval = int(interval_raw)
        except ValueError:
            interval = 60
        try:
            max_items = int(max_items_raw)
        except ValueError:
            max_items = 3
        try:
            max_len = int(max_len_raw)
        except ValueError:
            max_len = 120

        interval = max(30, min(interval, 900))
        max_items = max(1, min(max_items, 6))
        max_len = max(40, min(max_len, 240))
        return enabled, interval, max_items, max_len


    def _build_vision_caption_client(self, llm_service):
        """Tầng caption (ảnh → mô tả) chạy REMOTE khi llm_service có vision_client.

        vision_client được dựng ở process_manager.build_vision_client() khi bật
        ERIS_VISION_API_ENABLED và khai một endpoint multimodal riêng. DeepSeek
        hiện chỉ làm text ở lane này, nên thiếu vision client thì caption rơi về local.
        """
        remote = getattr(llm_service, "vision_client", None)
        # Nếu provider chính là multimodal nhưng chưa dựng lane riêng, thử dùng
        # chính main client. Adapter sẽ tự degrade nếu model từ chối image_url.
        # Nhờ vậy gpt-5.6 Terra không bị đứng ngoài cửa trong khi OCR-only giả
        # vờ là toàn bộ thị giác của Eris.
        if remote is None:
            use_main = os.environ.get("ERIS_VISION_USE_MAIN_MODEL", "1").strip().lower() not in {
                "0", "false", "no", "off",
            }
            if use_main:
                candidate = getattr(llm_service, "client", None)
                if (
                    candidate is not None
                    and callable(getattr(candidate, "create_chat_completion", None))
                    and supports_image_input(
                        getattr(candidate, "base_url", ""),
                        getattr(candidate, "model_name", ""),
                    )
                ):
                    remote = candidate
        if remote is None or not callable(getattr(remote, "create_chat_completion", None)):
            return None
        self.logger.info(
            "[vision caption] caption stage chạy REMOTE (%s); bỏ moondream local.",
            getattr(remote, "model_name", "remote"),
        )
        return RemoteCaptionClient(remote, logger=self.logger)


    def _build_vision_structure_client(self, llm_service):
        """Tầng cấu trúc-hoá vision (caption → JSON) chạy REMOTE.

        Tầng này chỉ nhận caption dạng text, nên có thể dùng DeepSeek main.
        ERIS_VISION_STRUCTURE_REMOTE:
          0/off       → tắt (structuring local nếu có)
          open_model/style → dùng style_client
          main/deepseek → dùng main client
          1/auto      → fallback_client, rồi style_client, rồi main client
        """
        mode = os.environ.get("ERIS_VISION_STRUCTURE_REMOTE", "auto").strip().lower()
        if mode in {"0", "false", "no", "off"}:
            return None
        if mode in {LEGACY_OPEN_MODEL_MODE, "style"}:
            remote = getattr(llm_service, "style_client", None)
        elif mode in {"main", "deepseek"}:
            remote = getattr(llm_service, "client", None)
        else:
            remote = (
                getattr(llm_service, "fallback_client", None)
                or getattr(llm_service, "style_client", None)
                or getattr(llm_service, "client", None)
            )
        if remote is None or not callable(getattr(remote, "create_chat_completion", None)):
            self.logger.warning(
                "[vision structure] bật nhưng không có remote client (fallback/style) — structuring rơi về local."
            )
            return None
        self.logger.info(
            "[vision structure] tầng-2 cấu trúc-hoá REMOTE qua %s (caption→JSON).",
            getattr(remote, "model_name", "remote"),
        )
        return RemoteStructureClient(remote, logger=self.logger)


    def _vision_queue_timeout_seconds(self, channel: str) -> float:
        env_name = "ERIS_VISION_QUEUE_TIMEOUT_CHAT_SEC" if channel == "chat" else "ERIS_VISION_QUEUE_TIMEOUT_SEC"
        default = 30.0 if channel == "chat" else 12.0
        raw = os.environ.get(env_name, "").strip()
        try:
            value = float(raw) if raw else default
        except ValueError:
            value = default
        return max(1.0, min(value, 120.0))


    def _vision_failure_backoff_seconds(self, stage: str, exc: Exception) -> float:
        text = f"{stage}:{exc}".lower()
        if any(marker in text for marker in ("model_queue_timeout", "resource_guard:", "tier_cooldown_active", "active_heavy_lease")):
            raw = os.environ.get("ERIS_VISION_SOFT_RETRY_BACKOFF_SEC", "2").strip()
            try:
                value = float(raw) if raw else 2.0
            except ValueError:
                value = 2.0
            return max(0.0, min(value, 10.0))
        return -1.0


    def _note_vision_failure(self, stage: str, exc: Exception) -> None:
        backoff = self._vision_failure_backoff_seconds(stage, exc)
        if backoff < 0.0:
            self.vision_failure_count += 1
            backoff = min(300, 15 * self.vision_failure_count)
        self.vision_disabled_until = time.time() + backoff
        self.logger.warning(
            "[vision_facts] %s failed: %s | backoff=%ss",
            stage,
            exc,
            backoff,
        )


    def _ocr_salvage_scene_note(self, ocr_text: str, *, channel: str, reason: str) -> SemanticSceneNote | None:
        """Giữ vision usable bằng OCR khi VLM/caption lane không tồn tại hoặc bị lỗi.

        No-fallback của cognition không nên biến một sensor còn OCR thành sensor mù.
        Fallback này chỉ phát visible text đã đọc được; nó tuyệt đối không tự suy
        scene, thời tiết, cảm xúc hay hành động của user.
        """

        clean = str(ocr_text or "").strip()
        if not clean:
            return None
        lines = [line.strip() for line in clean.splitlines() if line.strip()][:8]
        if not lines:
            return None
        digest = hashlib.sha1(clean.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return SemanticSceneNote(
            visible_context=lines[0],
            important_text=lines,
            activity_intent="reading",
            semantic_family="screen_text",
            novelty=0.35,
            salience=0.55,
            possible_error=0.0,
            should_interrupt_user=False,
            confidence=0.82,
            content_summary=lines[0],
            content_affect="neutral",
            relation_to_user="",
            delta="",
            source="screen_text_evidence",
            evidence=[
                "screen_capture:ambient" if channel != "chat" else "screen_capture:chat_current",
                f"ocr_text:{digest}",
            ],
            debug={"fallback_reason": str(reason or "vision_ocr_salvage")},
        )


    def _is_low_value_screen_fact(self, fact: str) -> bool:
        normalized = self._normalize_user_message(fact)
        if not normalized:
            return True

        # Content-free OCR placeholder from the vision compressor fallback
        # ("screen context inferred from OCR; focus=unknown; intent=unknown")
        # carries no real screen content. Drop it so it never leaks as evidence.
        if "screen context inferred from ocr" in normalized:
            return True

        useful_terms = {
            "build",
            "chat",
            "code",
            "crash",
            "diff",
            "error",
            "exception",
            "failed",
            "file",
            "git",
            "loi",
            "message",
            "meeting",
            "meet",
            "popup",
            "script",
            "terminal",
            "test",
            "traceback",
            "warning",
        }
        if any(term in normalized for term in useful_terms):
            return False

        low_value_patterns = (
            "bảng điều khiển bên phải",
            "bảng điều khiển bên trái",
            "panel bên phải",
            "panel bên trái",
            "right panel",
            "left panel",
            "inspector",
            "refcounted",
            "vài cửa sổ",
            "cửa sổ khác",
            "cửa sổ đang chạy",
            "ba cửa sổ",
            "bốn cửa sổ",
            "góc dưới",
            "góc dưới phải",
            "góc dưới cùng",
            "some windows",
            "other windows",
            "running windows",
            "pixel art",
            "cây pixel",
            "bốn cây",
            "ba cây",
            "nhân vật anime",
            "tóc hồng",
            "anime tóc hồng",
            "eris debug",
            "eris",
            "debug của tôi",
            "khung debug",
            "debug window",
            "facebook",
            "trang tin nhắn facebook",
        )
        if any(self._normalize_user_message(pattern) in normalized for pattern in low_value_patterns):
            return True

        layout_terms = tuple(
            self._normalize_user_message(item)
            for item in (
                "bên phải",
                "bên trái",
                "góc dưới",
                "góc trên",
                "right side",
                "left side",
                "bottom",
                "corner",
            )
        )
        chrome_terms = (
            "bảng điều khiển",
            "panel",
            "sidebar",
            "dock",
            "cửa sổ",
            "window",
            "debug",
        )
        return any(term in normalized for term in layout_terms) and any(
            term in normalized for term in chrome_terms
        )


    def _speech_evidence_score(self, text: str, *, kind: str = "") -> float:
        normalized = self._normalize_user_message(text)
        if not normalized:
            return 0.0
        if kind == "vision_fact" and self._is_low_value_screen_fact(text):
            return 0.0
        if kind == "recent_event" and self._is_generic_screen_event(text):
            return 0.0
        if kind == "evidence" and self._is_generic_screen_evidence(text):
            return 0.0

        strong_terms = (
            "traceback",
            "exception",
            "error",
            "failed",
            "failure",
            "build failed",
            "test failed",
            "crash",
            "warning",
            "compiler",
            "stack trace",
            "terminal",
            "loi",
        )
        if any(term in normalized for term in strong_terms):
            return 0.95

        message_terms = ("tin nhan", "message", "dm", "inbox")
        message_change_terms = ("moi", "unread", "vua nhan", "received", "sent", "reply", "ping")
        if any(term in normalized for term in message_terms):
            return 0.82 if any(term in normalized for term in message_change_terms) else 0.20

        meet_terms = ("meet", "meeting", "cuoc goi", "call")
        meet_change_terms = ("joined", "waiting", "started", "ended", "microphone", "camera", "invited", "request")
        if any(term in normalized for term in meet_terms):
            return 0.80 if any(term in normalized for term in meet_change_terms) else 0.18

        media_terms = (
            "nhac",
            "music",
            "bai hat",
            "song",
            "track",
            "artist",
            "lofi",
            "audio",
            "dang phat",
            "playing",
            "now playing",
            "youtube",
            "video",
            "danh sach video",
            "playlist",
            "trang web youtube",
            "tieu de",
            "title",
            "dang xem",
        )
        if any(term in normalized for term in media_terms):
            return 0.85

        task_change_terms = (
            "file_saved",
            "file saved",
            "active editor changed",
            "opened file",
            "modified file",
            "git diff",
            "pull request",
            "test passed",
            "build succeeded",
        )
        if any(term in normalized for term in task_change_terms):
            return 0.72

        return 0.0


    def _is_speakable_evidence(self, text: str, *, kind: str = "") -> bool:
        return self._speech_evidence_score(text, kind=kind) >= 0.65


    def _sanitize_screen_facts(self, facts: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        if not facts:
            return ()
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in facts:
            text = re.sub(r"\s+", " ", str(item or "").strip())
            if not text:
                continue
            if self._is_low_value_screen_fact(text):
                continue
            key = text.lower()
            if key in seen:
                continue
            if len(text) > self.vision_fact_max_len:
                trimmed = text[: self.vision_fact_max_len - 3].rstrip()
                text = f"{trimmed}..."
            cleaned.append(text)
            seen.add(key)
            if len(cleaned) >= self.vision_fact_max_items:
                break
        return tuple(cleaned)


    def _ground_semantic_scene_note(
        self,
        note: SemanticSceneNote | None,
        *,
        requested: bool = False,
        stale: bool = False,
    ) -> tuple[VisionGroundingReport, tuple[str, ...]]:
        """Kiểm từng claim vision trước khi biến thành ``screen_facts``.

        ``SemanticSceneNote`` là output của model, chưa phải bằng chứng đã được
        cấp quyền nói. Policy claim-level ở đây là van một chiều: claim yếu vẫn
        được giữ trong report để debug, nhưng không lọt vào prompt như fact.
        """

        policy = getattr(self, "vision_grounding_policy", None)
        if not isinstance(policy, VisionGroundingPolicy):
            policy = VisionGroundingPolicy()
            self.vision_grounding_policy = policy
        report = policy.evaluate(note, requested=requested, stale=stale)
        rollout = None
        try:
            from runtime.rollout_control import ensure_rollout_controller

            rollout = ensure_rollout_controller(self).decide(
                "vision_abstain",
                session_id=str(getattr(self, "runtime_session_id", "") or getattr(self, "session_id", "") or id(self)),
            )
        except Exception:
            rollout = None

        if rollout is None or rollout.apply:
            facts = self._sanitize_screen_facts(
                report.to_screen_facts(max_items=self.vision_fact_max_items)
            )
        else:
            # Control path cho dark/shadow/canary: giữ hành vi legacy đủ gần để
            # so sánh, trong khi report mới vẫn được log. Không surface relation
            # mềm vì đó chưa từng là tri giác trực tiếp đáng tin.
            legacy = []
            for value in (
                getattr(note, "content_summary", ""),
                getattr(note, "visible_context", ""),
                *tuple(getattr(note, "important_text", ()) or ()),
            ):
                text = str(value or "").strip()
                if text and text not in legacy:
                    legacy.append(text)
                if len(legacy) >= int(self.vision_fact_max_items):
                    break
            facts = self._sanitize_screen_facts(tuple(legacy))
        try:
            from runtime.observability import ensure_observability

            ensure_observability(self).record_vision(report, channel="requested" if requested else "ambient")
        except Exception:
            pass
        return report, facts


    def _semantic_scene_note_to_facts(self, note: SemanticSceneNote | None) -> tuple[str, ...]:
        """API tương thích cho caller cũ; mặc định coi đây là lane ambient."""

        _report, facts = self._ground_semantic_scene_note(note, requested=False, stale=False)
        return facts


    def _vision_trace_from_scene_note(self, note: SemanticSceneNote | None) -> str:
        if note is None:
            return ""
        source = str(getattr(note, "source", "") or "vision")
        family = str(getattr(note, "semantic_family", "") or "unknown")
        affect = str(getattr(note, "content_affect", "") or "").strip()
        has_delta = "1" if str(getattr(note, "delta", "") or "").strip() else "0"
        trace = f"{source}:{family}"
        if affect:
            trace += f":affect={affect}"
        trace += f":delta={has_delta}"
        return trace


    def _should_use_vision_compressor(
        self,
        *,
        observer_frame: ObserverFrame | None,
        force_refresh: bool,
    ) -> bool:
        compressor = getattr(self, "vision_semantic_compressor", None)
        if compressor is None:
            return False
        if force_refresh:
            return True
        if observer_frame is None:
            return False
        should_compress = getattr(compressor, "should_compress", None)
        if not callable(should_compress):
            return False
        try:
            return bool(
                should_compress(
                    observer_frame=observer_frame,
                    signal_diff=getattr(self, "last_signal_diff", None),
                )
            )
        except Exception as exc:
            self.logger.warning("[vision_facts] compressor policy failed: %s", exc)
            return False


    def _chat_screen_text_as_ocr_text(self, *, limit: int = 1600) -> str:
        """Gom OCR/text evidence của router thành một chuỗi cho vision fallback.

        PATCH: log cho thấy route có text_evidence nhưng vision compressor không
        nhận được ocr_text đủ rõ, dẫn đến no_claim_candidate. Helper này cho phép
        chat vision salvage từ chính evidence đã dùng để route.
        """

        lines: list[str] = []
        seen: set[str] = set()
        for block in tuple(getattr(self, "_chat_screen_text_evidence", ()) or ()):
            text = " ".join(str(getattr(block, "text", "") or "").split()).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(text)
        return "\n".join(lines)[: max(80, int(limit))]

    def _chat_screen_text_note_from_route(self) -> SemanticSceneNote | None:
        """Tạo SemanticSceneNote an toàn từ OCR route evidence khi VLM im lặng.

        PATCH: đây không giả vờ hiểu toàn cảnh. Nó chỉ biến các dòng text/OCR đã
        có provenance thành visible_text claims, để ERIS có thể nói “tôi đọc được
        vài chữ...” thay vì claims=0 hoặc bịa là đã thấy giao diện.
        """

        ocr_text = self._chat_screen_text_as_ocr_text()
        if not ocr_text:
            return None

        lines = [line.strip() for line in ocr_text.splitlines() if line.strip()][:8]
        if not lines:
            return None

        route = getattr(self, "_chat_screen_route_decision", None)
        route_confidence = float(getattr(route, "confidence", 0.0) or 0.0)
        confidence = max(0.78, min(0.90, route_confidence or 0.82))
        digest = hashlib.sha1(ocr_text.encode("utf-8", errors="ignore")).hexdigest()[:12]

        note = SemanticSceneNote(
            visible_context=lines[0],
            important_text=lines,
            activity_intent="reading",
            semantic_family="screen_text",
            novelty=0.35,
            salience=0.55,
            possible_error=0.0,
            should_interrupt_user=False,
            confidence=confidence,
            content_summary=lines[0],
            content_affect="neutral",
            relation_to_user="",
            delta="",
            source="screen_text_evidence",
            evidence=[
                "screen_capture:chat_current",
                f"ocr_text:{digest}",
            ],
            debug={
                "fallback_reason": "vision_claim_empty_salvaged_from_chat_screen_text",
                "route_confidence": route_confidence,
            },
        )
        assign = getattr(getattr(self, "vision_semantic_compressor", None), "_assign_scene_continuity", None)
        if callable(assign):
            try:
                assign(note)
            except Exception:
                pass
        return note


    def _refresh_screen_facts_from_data_uri(
        self,
        *,
        data_uri: str,
        snapshot=None,
        channel: str = "autonomous",
        user_text: str = "",
        observer_frame: ObserverFrame | None = None,
        force_refresh: bool = False,
        started_at: float | None = None,
    ) -> tuple[str, ...]:
        route = getattr(self, "_chat_screen_route_decision", None)
        route_refs_screen = bool(getattr(route, "references_screen", False))
        looks_like_request = bool(
            callable(getattr(self, "_looks_like_screen_vision_request", None))
            and self._looks_like_screen_vision_request(user_text)
        )
        # PATCH: requested phải tin cả router/anaphora. Nếu chỉ tin regex tại đây,
        # follow-up “còn mù vision hả” rơi thành requested=0 dù route đã cứu được.
        requested = bool(channel == "chat" and (route_refs_screen or looks_like_request))
        policy = getattr(self, "vision_grounding_policy", None)
        if not isinstance(policy, VisionGroundingPolicy):
            policy = VisionGroundingPolicy()
            self.vision_grounding_policy = policy

        if not data_uri:
            # Không có frame mới: cache còn hữu ích cho continuity nhưng phải mang
            # nhãn stale. Đừng đổi "lúc nãy" thành "tôi đang thấy" chỉ vì tuple
            # vẫn còn nằm trong RAM.
            self.vision_facts_are_stale = bool(self.vision_facts)
            self.vision_last_trace = ""
            self.vision_facts_novelty_hint = "cached_screen_fact" if self.vision_facts else "none"
            if self.last_semantic_scene_note is not None:
                self.last_vision_grounding_report = policy.evaluate(
                    self.last_semantic_scene_note,
                    requested=requested,
                    stale=True,
                )
            else:
                self.last_vision_grounding_report = policy.empty_report(
                    requested=requested,
                    stale=True,
                    reason="no_fresh_screen_frame",
                )
            return self.vision_facts

        if observer_frame is None and snapshot is not None:
            observer_frame = self._run_background_observer(
                snapshot=snapshot,
                channel=channel,
                user_text=user_text,
                vision_facts=(),
                started_at=started_at,
            )

        note: SemanticSceneNote | None = None
        trace = ""
        compressor = getattr(self, "vision_semantic_compressor", None)
        can_use_compressor = False
        if compressor is not None and callable(getattr(compressor, "compress", None)):
            config = getattr(compressor, "config", None)
            if config is None or getattr(config, "enabled", True):
                can_use_compressor = True

        signal_snapshot = None
        if snapshot is not None:
            try:
                signal_snapshot = self._build_signal_snapshot_for_observer(
                    snapshot=snapshot,
                    channel=channel,
                    user_text=user_text,
                    vision_facts=tuple(getattr(snapshot, "vision_facts", ()) or ()),
                    started_at=started_at,
                )
            except Exception as exc:
                self.logger.warning("[vision_facts] signal snapshot fallback: %s", exc)
        ocr_text_for_vision = (
            str(getattr(signal_snapshot, "ocr_text", "") or "")
            if signal_snapshot is not None
            else ""
        )
        if channel == "chat" and not ocr_text_for_vision:
            # PATCH: route đã có OCR evidence, compressor cũng phải được đọc nó.
            ocr_text_for_vision = self._chat_screen_text_as_ocr_text()

        if can_use_compressor:
            try:
                note = self.model_budget_policy.run_queued(
                    "vision_compressor",
                    compressor.compress,
                    reason=f"vision_compressor:{channel}",
                    priority=45 if channel == "chat" else 25,
                    timeout_seconds=self._vision_queue_timeout_seconds(channel),
                    image_data_uri=data_uri,
                    ocr_text=ocr_text_for_vision,
                    observer_frame=observer_frame,
                    signal_snapshot=signal_snapshot,
                    extra_context=user_text if channel == "chat" else "",
                )
                trace = self._vision_trace_from_scene_note(note)
            except Exception as exc:
                self._note_vision_failure("compressor", exc)
                # VLM hỏng/không có client vẫn còn OCR. Đừng vứt luôn bằng chứng
                # trực tiếp rồi để downstream tưởng rằng vision hoàn toàn mù.
                salvage_note = self._ocr_salvage_scene_note(
                    ocr_text_for_vision,
                    channel=channel,
                    reason=f"vision_compressor_failed:{type(exc).__name__}",
                )
                if salvage_note is not None:
                    self.logger.info(
                        "[vision_facts] salvage=ocr_after_compressor_failure channel=%s",
                        channel,
                    )
                    note = salvage_note
                    trace = self._vision_trace_from_scene_note(note)
                else:
                    self.vision_stale_facts = tuple(self.vision_facts)
                    self.vision_facts_are_stale = bool(self.vision_facts)
                    self.last_vision_grounding_report = policy.empty_report(
                        requested=requested,
                        stale=True,
                        reason=f"vision_compressor_failed:{type(exc).__name__}",
                        unknowns=("fresh_frame_not_interpreted",),
                    )
                    return self.vision_facts
        else:
            self.logger.info(
                "[vision_facts] source=ocr_salvage reason=no_compressor channel=%s",
                channel,
            )
            salvage_note = self._ocr_salvage_scene_note(
                ocr_text_for_vision,
                channel=channel,
                reason="vision_disabled_salvaged_from_ocr",
            )

            if salvage_note is not None:
                note = salvage_note
                trace = self._vision_trace_from_scene_note(note)
            else:
                self.vision_stale_facts = tuple(self.vision_facts)
                self.vision_facts_are_stale = bool(self.vision_facts)
                self.last_vision_grounding_report = policy.empty_report(
                    requested=requested,
                    stale=True,
                    reason="vision_compressor_unavailable_and_no_ocr",
                )
                return self.vision_facts

        report, cleaned = self._ground_semantic_scene_note(
            note,
            requested=requested,
            stale=False,
        )
        if channel == "chat" and requested and not tuple(getattr(report, "grounded_claims", ()) or ()):
            salvage_note = self._chat_screen_text_note_from_route()
            if salvage_note is not None:
                # PATCH: VLM/structure có thể im lặng, nhưng OCR route evidence vẫn là
                # bằng chứng trực tiếp. Salvage chỉ sinh visible_text, không bịa scene.
                self.logger.info(
                    "[vision_facts] salvage=screen_text_evidence reason=no_grounded_vision_claim"
                )
                note = salvage_note
                trace = self._vision_trace_from_scene_note(note)
                report, cleaned = self._ground_semantic_scene_note(
                    note,
                    requested=True,
                    stale=False,
                )
        self.last_vision_grounding_report = report
        self.vision_failure_count = 0
        self.vision_disabled_until = 0.0
        self.vision_last_capture_at = time.time()
        self.last_semantic_scene_note = note

        previous_facts = tuple(self.vision_facts)
        signature = "\n".join(cleaned)
        self.vision_facts_novelty_hint = (
            "new_screen_fact"
            if cleaned and signature != self.vision_facts_signature
            else ("repeated_screen_fact" if cleaned else "abstained_screen_fact")
        )
        self.vision_facts_signature = signature

        if cleaned:
            self.vision_stale_facts = previous_facts
            self.vision_facts = cleaned
            self.vision_facts_are_stale = False
        else:
            # Frame mới đã được đọc nhưng không có claim nào đủ chắc. Xóa current
            # facts để cache cũ không đội mồ sống dậy thành hiện tại. Vẫn giữ bản
            # cũ trong ``vision_stale_facts`` cho audit, không đưa vào prompt.
            if previous_facts:
                self.vision_stale_facts = previous_facts
            self.vision_facts = ()
            self.vision_facts_are_stale = False

        self.vision_last_trace = (
            f"{trace};grounding={report.recommendation};"
            f"strength={report.evidence_strength:.2f};"
            f"grounded={len(report.grounded_claims)}"
        ).strip(";")
        self.logger.info(
            "[vision_grounding] channel=%s requested=%d recommendation=%s strength=%.2f "
            "claims=%d grounded=%d contradictions=%s unknowns=%s",
            channel,
            int(requested),
            report.recommendation,
            report.evidence_strength,
            len(report.claims),
            len(report.grounded_claims),
            ",".join(report.contradictions) or "none",
            ",".join(report.unknowns) or "none",
        )
        self.logger.info(
            "[vision_facts] source=grounded_claims channel=%s count=%d novelty=%s trace=%s fact=%s",
            channel,
            len(cleaned),
            self.vision_facts_novelty_hint,
            self.vision_last_trace or "<empty>",
            cleaned[0] if cleaned else "<abstained>",
        )
        if snapshot is not None:
            snapshot.vision_facts = tuple(self.vision_facts)
        return self.vision_facts


    def _vision_frame_delta_score(self, data_uri: str) -> float:
        """Return a cheap local [0,1] visual delta without any remote model."""

        raw = str(data_uri or "")
        if not raw:
            return 0.0
        payload = raw.split(",", 1)[1] if "," in raw else raw
        try:
            blob = base64.b64decode(payload, validate=False)
        except Exception:
            blob = payload.encode("utf-8", "ignore")
        digest = hashlib.sha1(blob).hexdigest()
        if digest == self._vision_last_frame_digest:
            return 0.0

        score = 1.0
        try:
            from PIL import Image

            with Image.open(io.BytesIO(blob)) as image:
                gray = image.convert("L").resize((32, 18))
                pixels = tuple(int(value) for value in gray.getdata())
            previous = self._vision_last_thumbnail
            if previous and len(previous) == len(pixels):
                score = sum(abs(a - b) for a, b in zip(previous, pixels)) / (255.0 * len(pixels))
            self._vision_last_thumbnail = pixels
        except Exception:
            # Exact digest fallback still eliminates identical-frame spam.
            score = 1.0 if self._vision_last_frame_digest else 1.0
        self._vision_last_frame_digest = digest
        return max(0.0, min(1.0, float(score)))


    def _maybe_update_screen_facts(
        self,
        *,
        snapshot=None,
        channel: str = "autonomous",
        user_text: str = "",
        observer_frame: ObserverFrame | None = None,
        force_refresh: bool = False,
        started_at: float | None = None,
    ) -> tuple[str, ...]:
        if not self.vision_facts_enabled:
            self.vision_facts_novelty_hint = "none"
            return ()

        now = time.time()
        if now < self.vision_disabled_until:
            self.vision_facts_novelty_hint = "cached_screen_fact" if self.vision_facts else "none"
            return self.vision_facts

        # PDF annotation, pages 14 and 20: adaptive thresholds are a P2
        # requirement, not decorative telemetry. Ambient vision consults broker
        # pressure before even capturing a frame, then raises both cooldown and
        # delta threshold as quota gets warm. A direct user screen question still
        # uses force_refresh and the required chat-tool lane.
        vision_advice = {
            "allowed": True,
            "threshold_multiplier": 1.0,
            "cooldown_multiplier": 1.0,
            "pressure_score": 0.0,
        }
        if channel != "chat" and not force_refresh:
            try:
                from llm.token_budget import detect_provider
                from runtime.inference_broker import GLOBAL_INFERENCE_BROKER

                client = getattr(getattr(self, "llm_service", None), "client", None)
                provider = detect_provider(
                    str(getattr(client, "base_url", "") or ""),
                    str(getattr(client, "model_name", "") or ""),
                )
                vision_advice = GLOBAL_INFERENCE_BROKER.optional_lane_advice(
                    "vision_ambient", provider=provider
                )
            except Exception:
                pass
            if not bool(vision_advice.get("allowed", True)):
                self.vision_last_capture_at = now
                self.vision_facts_novelty_hint = "cached_screen_fact" if self.vision_facts else "none"
                self.logger.debug(
                    "[vision_facts] capture skipped by broker advice: reason=%s pressure=%.3f",
                    vision_advice.get("reason", "unknown"),
                    float(vision_advice.get("pressure_score", 0.0) or 0.0),
                )
                return self.vision_facts

        effective_interval = self.vision_interval_sec * max(
            1.0, float(vision_advice.get("cooldown_multiplier", 1.0) or 1.0)
        )
        if (
            not force_refresh
            and self.vision_last_capture_at
            and (now - self.vision_last_capture_at) < effective_interval
        ):
            self.vision_facts_novelty_hint = "cached_screen_fact" if self.vision_facts else "none"
            return self.vision_facts

        try:
            data_uri = self.telemetry_service.capture_screen_frame_data_uri()
        except Exception as exc:
            self._note_vision_failure("capture", exc)
            return self.vision_facts

        if channel != "chat" and not force_refresh:
            delta_score = self._vision_frame_delta_score(data_uri)
            try:
                base_threshold = max(0.01, min(1.0, float(os.environ.get("ERIS_VISION_SCREEN_DELTA_THRESHOLD", "0.15") or 0.15)))
            except (TypeError, ValueError):
                base_threshold = 0.15
            threshold = min(0.95, base_threshold * max(
                1.0, float(vision_advice.get("threshold_multiplier", 1.0) or 1.0)
            ))
            if delta_score < threshold:
                self.vision_last_capture_at = now
                self.vision_facts_novelty_hint = "cached_screen_fact" if self.vision_facts else "none"
                self.logger.debug(
                    "[vision_facts] remote summarize skipped: delta=%.3f threshold=%.3f",
                    delta_score,
                    threshold,
                )
                return self.vision_facts

        return self._refresh_screen_facts_from_data_uri(
            data_uri=data_uri,
            snapshot=snapshot,
            channel=channel,
            user_text=user_text,
            observer_frame=observer_frame,
            force_refresh=force_refresh,
            started_at=started_at or now,
        )


    def _attach_screen_facts(self, snapshot, facts: tuple[str, ...]) -> str:
        if not facts:
            return ""
        snapshot.vision_facts = tuple(facts)
        return VISION_TRACE_MARKER


    def _is_generic_screen_event(self, event: str) -> bool:
        se_lens = getattr(self.telemetry_service, "se_lens", None)
        checker = getattr(se_lens, "_is_generic_prompt_event", None)
        if callable(checker):
            return bool(checker(event))

        normalized = str(event or "").strip().lower()
        generic_events = {
            "coding_window_active",
            "reference_window_active",
            "communication_window_active",
            "entertainment_window_active",
            "general_window_active",
            "browser_url_visible",
            "many_browser_tabs_visible",
            "many_windows_open",
            "long_idle_period",
            "system_monitoring_visible",
        }
        return normalized in generic_events or normalized.endswith("_window_active")


    def _is_generic_screen_evidence(self, evidence: str) -> bool:
        se_lens = getattr(self.telemetry_service, "se_lens", None)
        checker = getattr(se_lens, "_is_generic_prompt_evidence", None)
        if callable(checker):
            return bool(checker(evidence))

        lowered = str(evidence or "").strip().lower()
        if not lowered:
            return True
        generic_prefixes = (
            "active window is ",
            "focus kind is ",
            "there has been no input for ",
            "browser target is visible:",
        )
        if lowered.startswith(generic_prefixes):
            return True
        if re.fullmatch(r"\d+ browser tabs are visible", lowered):
            return True
        if re.fullmatch(r"\d+ windows are open", lowered):
            return True
        if lowered.startswith("recent events include "):
            listed = lowered.removeprefix("recent events include ")
            events = [item.strip() for item in listed.split(",") if item.strip()]
            return bool(events) and all(self._is_generic_screen_event(item) for item in events)
        return False


    def _concrete_screen_signals(self, snapshot) -> tuple[str, ...]:
        return tuple((*self._concrete_vision_signals(snapshot), *self._concrete_telemetry_signals(snapshot))[:4])


    def _concrete_vision_signals(self, snapshot) -> tuple[str, ...]:
        vision_facts = tuple(getattr(snapshot, "vision_facts", ()) or ())
        return tuple(
            str(item)
            for item in vision_facts
            if str(item or "").strip() and self._is_speakable_evidence(str(item), kind="vision_fact")
        )[:2]


    def _concrete_telemetry_signals(self, snapshot) -> tuple[str, ...]:
        recent_events = tuple(getattr(snapshot, "recent_events", ()) or ())
        evidence = tuple(getattr(snapshot, "evidence", ()) or ())
        concrete_events = [
            str(event)
            for event in recent_events
            if self._is_speakable_evidence(str(event), kind="recent_event")
        ]
        concrete_evidence = [
            str(item)
            for item in evidence
            if self._is_speakable_evidence(str(item), kind="evidence")
        ]
        return tuple((*concrete_events, *concrete_evidence)[:2])


    def _has_concrete_screen_signal(self, snapshot) -> bool:
        return bool(self._concrete_screen_signals(snapshot))


__all__ = ["ScreenVisionRuntimeMixin"]
