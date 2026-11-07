# -*- coding: utf-8 -*-
"""Semantic Tutor chạy nền theo sự kiện.

Mục tiêu của module này không phải tạo thêm một nhân cách biết nói. Tutor chỉ
phân rã dữ liệu thô (OCR/DOM/telemetry/vision/chess) thành một nền ngữ nghĩa dùng
chung cho chat, autonomous và các domain như cờ.

Nguyên tắc quan trọng:
- Không gọi model theo timer. Chỉ gọi khi scene bị invalidated bởi app/tab/URL,
  media, foreground entity, ván cờ hoặc thay đổi màn hình đủ lớn.
- Chỉ giữ một job mới nhất. Nếu user đổi tab liên tục, job cũ được bỏ kết quả.
- Chat không bị chặn. Khi packet chưa sẵn sàng, chat nhận raw facts an toàn.
- Fact, inference và unknown luôn tách riêng để model public không tự biến phỏng
  đoán thành sự thật. Con người đã có quảng cáo làm việc đó rồi.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


def _compact(value: Any, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _norm(value: Any) -> str:
    return _compact(value, 500).casefold()


def _stable_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raw = repr(value)
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(mapping: Mapping[str, Any], *paths: str) -> Any:
    """Lấy trường đầu tiên tồn tại từ mapping lồng nhau bằng đường dẫn a.b.c."""

    for path in paths:
        current: Any = mapping
        ok = True
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                ok = False
                break
            current = current[part]
        if ok and current not in (None, "", (), [], {}):
            return current
    return ""


def _extract_content(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], Mapping) else {}
    message = first.get("message") if isinstance(first, Mapping) else {}
    return str(message.get("content", "") or "") if isinstance(message, Mapping) else ""


def _safe_list(value: Any, *, limit: int = 10, item_limit: int = 360) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = list(value)
    else:
        items = []
    result: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            text = _compact(item.get("claim") or item.get("text") or item.get("summary"), item_limit)
        else:
            text = _compact(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


@dataclass(frozen=True, slots=True)
class SceneIdentity:
    """Danh tính cơ học của scene, dùng để quyết định khi nào phải phân tích lại."""

    app: str = ""
    window_title: str = ""
    tab_id: str = ""
    url: str = ""
    document_id: str = ""
    foreground_entity: str = ""
    media_id: str = ""
    chess_game_id: str = ""
    chess_phase: str = ""
    content_digest: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "app": self.app,
            "window_title": self.window_title,
            "tab_id": self.tab_id,
            "url": self.url,
            "document_id": self.document_id,
            "foreground_entity": self.foreground_entity,
            "media_id": self.media_id,
            "chess_game_id": self.chess_game_id,
            "chess_phase": self.chess_phase,
            "content_digest": self.content_digest,
        }

    @property
    def scene_id(self) -> str:
        # Scene ID phải ổn định qua timer/title/OCR delta nhỏ. window_title và
        # content_digest chỉ dùng để phát hiện thay đổi, không được làm packet
        # hiện hành mất hiệu lực trong lúc user vẫn ở cùng tab/bài/ván.
        stable = {
            "app": self.app,
            "tab_id": self.tab_id,
            "url": self.url,
            "document_id": self.document_id,
            "foreground_entity": self.foreground_entity,
            "media_id": self.media_id,
            "chess_game_id": self.chess_game_id,
            "chess_phase": self.chess_phase,
        }
        if not any(value for key, value in stable.items() if key != "app"):
            stable["window_title"] = self.window_title
        return _stable_hash(stable)


@dataclass(frozen=True, slots=True)
class SharedContextPacket:
    """Nền ngữ nghĩa dùng chung cho mọi public lane."""

    scene_id: str = ""
    version: int = 0
    generated_at: float = 0.0
    expires_at: float = 0.0
    status: str = "empty"
    trigger_reasons: tuple[str, ...] = ()
    activity: str = "unknown"
    foreground_summary: str = ""
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    conversation_options: tuple[str, ...] = ()
    research_queries: tuple[str, ...] = ()
    response_mode_scores: Mapping[str, float] = field(default_factory=dict)
    raw_source_summary: str = ""
    source_fingerprint: str = ""
    research_summary: str = ""

    def fresh(self, *, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        return bool(self.scene_id and self.status == "ready" and now <= self.expires_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "version": self.version,
            "generated_at": self.generated_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "trigger_reasons": list(self.trigger_reasons),
            "activity": self.activity,
            "foreground_summary": self.foreground_summary,
            "facts": list(self.facts),
            "inferences": list(self.inferences),
            "unknowns": list(self.unknowns),
            "conversation_options": list(self.conversation_options),
            "research_queries": list(self.research_queries),
            "response_mode_scores": dict(self.response_mode_scores),
            "raw_source_summary": self.raw_source_summary,
            "source_fingerprint": self.source_fingerprint,
            "research_summary": self.research_summary,
        }

    def prompt_context(self, *, channel: str) -> str:
        lines = [
            "SHARED SEMANTIC CONTEXT (DeepSeek Tutor; nền dùng chung, không phải lời đáp):",
            f"- scene_id: {self.scene_id}; version={self.version}; channel={channel}",
            f"- activity: {self.activity}",
            f"- foreground: {self.foreground_summary or '<unknown>'}",
        ]
        if self.facts:
            lines.append("- direct_facts:")
            lines.extend(f"  - {item}" for item in self.facts)
        if self.inferences:
            lines.append("- bounded_inferences (không được nói như fact):")
            lines.extend(f"  - {item}" for item in self.inferences)
        if self.unknowns:
            lines.append("- unknowns (không tự bịa để lấp chỗ trống):")
            lines.extend(f"  - {item}" for item in self.unknowns)
        if self.conversation_options:
            lines.append("- topic_options:")
            lines.extend(f"  - {item}" for item in self.conversation_options)
        if self.research_summary:
            lines.append(f"- related_research: {self.research_summary}")
        if self.response_mode_scores:
            ordered = sorted(self.response_mode_scores.items(), key=lambda item: item[1], reverse=True)
            lines.append("- suggested_response_modes: " + ", ".join(f"{k}={v:.2f}" for k, v in ordered))
        lines.extend((
            "semantic_contract:",
            "- Khi user hỏi trực tiếp, trả lời user trước; context này chỉ giúp hiểu chủ đề.",
            "- Chat có thể phát triển tự nhiên quanh foreground nhưng không được phát minh giá, tên, ý định hay trạng thái không có trong facts.",
            "- Autonomous ưu tiên screen-related small talk hoặc phân tích khi có giá trị; unrelated small talk chỉ khi không có anchor tốt.",
            "- Surface chỉ diễn đạt ý định đã chọn, không tự phân tích lại màn hình.",
        ))
        return "\n".join(lines)


class SceneChangeDetector:
    """So sánh scene mới với baseline và trả đúng lý do invalidation."""

    def __init__(self) -> None:
        self._last: SceneIdentity | None = None

    def evaluate(self, current: SceneIdentity, *, explicit: bool = False, significant_delta: bool = False) -> tuple[str, ...]:
        previous = self._last
        reasons: list[str] = []
        if previous is None:
            if any(current.to_dict().values()):
                reasons.append("initial_scene")
        else:
            comparisons = (
                ("app_changed", previous.app, current.app),
                ("tab_changed", previous.tab_id, current.tab_id),
                ("url_changed", previous.url, current.url),
                ("document_identity_changed", previous.document_id, current.document_id),
                ("foreground_entity_changed", previous.foreground_entity, current.foreground_entity),
                ("media_item_changed", previous.media_id, current.media_id),
                ("chess_game_started", previous.chess_game_id, current.chess_game_id),
                ("chess_phase_changed", previous.chess_phase, current.chess_phase),
            )
            for reason, old, new in comparisons:
                if new and old != new:
                    reasons.append(reason)
            # Window title thường chứa timer, số thông báo hoặc progress. Không
            # xem mọi thay đổi title là scene mới. Khi app không đổi, title chỉ
            # có quyền invalidation nếu đi kèm screen delta lớn và không có tab/
            # URL/document structured nào tốt hơn để định danh content.
            if (
                significant_delta
                and current.window_title
                and current.window_title != previous.window_title
                and not any((current.tab_id, current.url, current.document_id, current.media_id))
            ):
                reasons.append("window_content_changed")
            # OCR hash chỉ đủ quyền invalidation khi thay đổi lớn. Nếu không,
            # dòng timer/video progress sẽ gọi model đến lúc quota hóa tro bụi.
            if significant_delta and current.content_digest and current.content_digest != previous.content_digest:
                reasons.append("significant_screen_delta")
        if explicit:
            reasons.append("explicit_user_request")
        self._last = current
        return tuple(dict.fromkeys(reasons))


class EventDrivenSemanticTutor:
    """Một worker nền, một packet chung và cơ chế fallback không chặn chat."""

    def __init__(self, host: Any, *, client: Any = None, logger: Any = None) -> None:
        self.host = host
        self.client = client
        self.logger = logger or getattr(host, "logger", None)
        self.enabled = str(os.getenv("ERIS_SEMANTIC_TUTOR_ENABLED", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}
        self.packet_ttl_seconds = max(10.0, min(3600.0, float(os.getenv("ERIS_SEMANTIC_TUTOR_CONTEXT_TTL_SEC", "900") or 900)))
        self.timeout_seconds = max(4.0, min(120.0, float(os.getenv("ERIS_SEMANTIC_TUTOR_TIMEOUT_SEC", "24") or 24)))
        self.chat_wait_ms = max(0, min(1200, int(os.getenv("ERIS_SEMANTIC_TUTOR_CHAT_WAIT_MS", "250") or 250)))
        self.auto_search = str(os.getenv("ERIS_SEMANTIC_TUTOR_AUTO_SEARCH", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}
        self.screen_delta_threshold = max(0.20, min(0.90, float(os.getenv("ERIS_SEMANTIC_TUTOR_SCREEN_DELTA_THRESHOLD", "0.35") or 0.35)))
        self.screen_delta_cooldown_seconds = max(5.0, min(300.0, float(os.getenv("ERIS_SEMANTIC_TUTOR_SCREEN_DELTA_COOLDOWN_SEC", "30") or 30)))
        self._last_screen_delta_queued_at = 0.0
        self._detector = SceneChangeDetector()
        # Tracker riêng chỉ dùng để chuẩn hóa snapshot UIA/OCR thành contract;
        # nó không tự suy luận nghĩa. Semantic model mới làm việc đó.
        from runtime.ai_orchestrated.ambient_screen_context import AmbientScreenTracker
        self._screen_tracker = AmbientScreenTracker()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._wake = threading.Event()
        self._closed = False
        self._pending: tuple[int, SceneIdentity, dict[str, Any], tuple[str, ...]] | None = None
        self._generation = 0
        self._packet = SharedContextPacket()
        self._last_raw: dict[str, Any] = {}
        self._last_identity = SceneIdentity()
        self._thread = threading.Thread(target=self._worker_loop, name="eris-semantic-tutor", daemon=True)
        self._thread.start()

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.client is not None)

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.5)

    stop = close
    shutdown = close

    def observe(
        self,
        *,
        channel: str,
        telemetry: Mapping[str, Any] | None = None,
        screen_context: Any = None,
        vision_facts: Sequence[Any] = (),
        chess_state: Mapping[str, Any] | None = None,
        user_text: str = "",
        explicit: bool = False,
        trigger_hint: str = "",
    ) -> tuple[str, ...]:
        telemetry = _mapping(telemetry)
        chess_state = _mapping(chess_state)
        if screen_context is not None and not isinstance(screen_context, Mapping) and not callable(getattr(screen_context, "to_dict", None)):
            try:
                screen_context = self._screen_tracker.observe(screen_context, telemetry=telemetry, now=time.time())
            except Exception:
                screen_context = None
        raw = self._build_raw_context(
            channel=channel,
            telemetry=telemetry,
            screen_context=screen_context,
            vision_facts=vision_facts,
            chess_state=chess_state,
            user_text=user_text,
        )
        # Các nguồn cập nhật độc lập. Event cờ không được xóa scene màn hình;
        # một tick OCR cũng không được làm mất structured chess state.
        with self._lock:
            raw = self._merge_partial_raw(self._last_raw, raw)
        identity = self._build_identity(raw)
        changed_ratio = float(raw.get("screen", {}).get("changed_ratio", 0.0) or 0.0)
        changed_text = tuple(raw.get("screen", {}).get("changed_text", ()) or ())
        significant_delta = bool(
            changed_ratio >= self.screen_delta_threshold
            and len(changed_text) >= 2
        )
        reasons = list(self._detector.evaluate(identity, explicit=False, significant_delta=significant_delta))
        now_wall = time.time()
        with self._lock:
            packet_ready = bool(self._packet.fresh(now=now_wall) and self._packet.scene_id == identity.scene_id)
            pending_same_scene = bool(self._pending is not None and self._pending[1].scene_id == identity.scene_id)
        # User hỏi lại về cùng scene thì dùng baseline đã có; không đốt thêm một
        # lượt Tutor chỉ vì chat vừa nhắc chữ “màn hình”. Chỉ explicit-trigger khi
        # scene chưa từng có packet hoặc packet đã hết hạn.
        if explicit and not packet_ready and not pending_same_scene:
            reasons.append("explicit_user_request")
        if trigger_hint:
            reasons.append(_compact(trigger_hint, 80))
        reasons = list(dict.fromkeys(reason for reason in reasons if reason))

        screen_only = bool(reasons) and all(
            reason in {"window_content_changed", "significant_screen_delta"}
            for reason in reasons
        )
        if screen_only and now_wall - self._last_screen_delta_queued_at < self.screen_delta_cooldown_seconds:
            reasons = []
        with self._lock:
            self._last_raw = raw
            self._last_identity = identity
            if not reasons:
                return ()
            if any(reason in {"window_content_changed", "significant_screen_delta"} for reason in reasons):
                self._last_screen_delta_queued_at = now_wall
            self._generation += 1
            generation = self._generation
            self._pending = (generation, identity, raw, tuple(reasons))
            self._wake.set()
        if self.logger is not None:
            try:
                self.logger.info(
                    "[semantic-tutor] queued scene=%s reasons=%s channel=%s",
                    identity.scene_id,
                    ",".join(reasons),
                    channel,
                )
            except Exception:
                pass
        return tuple(reasons)

    def observe_chess(self, state: Mapping[str, Any], *, events: Sequence[str] = ()) -> tuple[str, ...]:
        """Entry point typed từ chess bridge; không bắt OCR nhìn lại bàn cờ."""

        trigger = ""
        event_set = {str(item or "") for item in events}
        if "chess.game_started" in event_set:
            trigger = "chess_game_started"
        elif "chess.game_over" in event_set:
            trigger = "chess_game_over"
        # Nước đi thường vẫn cập nhật raw state nhưng không ép phân tích sâu. Tutor
        # chỉ chạy lại khi ván/phase thay đổi hoặc policy khác gửi trigger rõ ràng.
        return self.observe(
            channel="chess",
            chess_state=state,
            trigger_hint=trigger,
        )

    def prompt_context(self, *, channel: str, wait_ms: int | None = None) -> str:
        wait_ms = self.chat_wait_ms if wait_ms is None and channel == "chat" else int(wait_ms or 0)
        deadline = time.monotonic() + max(0.0, wait_ms / 1000.0)
        with self._condition:
            while wait_ms > 0 and self._pending is not None and time.monotonic() < deadline:
                self._condition.wait(timeout=max(0.0, deadline - time.monotonic()))
            packet = self._packet
            identity = self._last_identity
            raw = dict(self._last_raw)
        if packet.fresh() and packet.scene_id == identity.scene_id:
            return packet.prompt_context(channel=channel)
        return self._raw_fallback_prompt(raw, channel=channel)

    def planner_context(self) -> dict[str, Any]:
        with self._lock:
            packet = self._packet
            identity = self._last_identity
            pending = self._pending is not None
            raw = dict(self._last_raw)
        packet_ready = bool(packet.scene_id == identity.scene_id and packet.fresh())
        return {
            "available": self.available,
            "pending": pending,
            "scene_id": identity.scene_id,
            "packet": packet.to_dict() if packet_ready else {},
            "raw_fallback": self._raw_fallback_mapping(raw),
        }

    # Contract generic cho StructuredEvidenceProvider. Nhờ đó source-grounding
    # có thể dùng fact semantic như provenance chính thức khi chat hỏi thẳng về
    # scene, thay vì coi packet chỉ là một đoạn prompt không nguồn gốc.
    structured_source_id = "semantic_tutor"
    engagement_kind = "semantic_scene"

    def is_engagement_active(self) -> bool:
        with self._lock:
            return bool(self._packet.fresh() and self._packet.scene_id == self._last_identity.scene_id)

    def is_state_fresh(self, *, max_age_seconds: float = 30.0) -> bool:
        with self._lock:
            packet = self._packet
        if not packet.fresh():
            return False
        return max(0.0, time.time() - packet.generated_at) <= max(1.0, float(max_age_seconds))

    def matches_turn(self, user_text: str) -> bool:
        text = _norm(user_text)
        if not text:
            return False
        markers = (
            "man hinh", "tren man hinh", "dang xem", "dang mo", "gia", "bai nhac",
            "video", "tab", "web", "trang nay", "cai nay", "thay gi", "nhin gi",
            "co vua", "ban co", "nuoc di", "the co",
        )
        return any(marker in text for marker in markers)

    def current_grounding_evidence(
        self,
        *,
        max_age_seconds: float = 30.0,
        relevant_to_turn: bool = False,
    ) -> tuple[dict[str, object], ...]:
        with self._lock:
            packet = self._packet
            identity = self._last_identity
        if packet.scene_id != identity.scene_id or not packet.fresh():
            return ()
        age = max(0.0, time.time() - packet.generated_at)
        if age > max(1.0, float(max_age_seconds)):
            return ()
        role = "primary_candidate" if relevant_to_turn else "ambient"
        return tuple(
            {
                "evidence_id": f"semantic:{packet.scene_id}:fact:{index}",
                "source": self.structured_source_id,
                "evidence_kind": "structured_runtime",
                "engagement_kind": self.engagement_kind,
                "engagement_id": packet.scene_id,
                "claim": fact,
                "confidence": 0.88,
                "grounded": True,
                "state_fresh": True,
                "relevant_to_turn": relevant_to_turn,
                "role": role,
                "tags": ["semantic_tutor", "shared_context", "direct_fact"],
            }
            for index, fact in enumerate(packet.facts)
            if fact
        )

    def _worker_loop(self) -> None:
        while not self._closed:
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if self._closed:
                return
            with self._lock:
                job = self._pending
            if job is None:
                continue
            generation, identity, raw, reasons = job
            packet = self._analyze(identity, raw, reasons, generation=generation)
            if packet is None:
                with self._condition:
                    if self._pending is not None and self._pending[0] == generation:
                        self._pending = None
                    self._condition.notify_all()
                continue
            with self._condition:
                # Nếu scene đã đổi trong lúc model đang trả lời, bỏ kết quả cũ.
                if generation != self._generation or identity.scene_id != self._last_identity.scene_id:
                    self._condition.notify_all()
                    continue
                self._packet = packet
                self._pending = None
                self._condition.notify_all()
            if self.logger is not None:
                try:
                    self.logger.info(
                        "[semantic-tutor] committed scene=%s version=%s activity=%s facts=%s",
                        packet.scene_id,
                        packet.version,
                        packet.activity,
                        len(packet.facts),
                    )
                except Exception:
                    pass
            self._maybe_research(packet, generation=generation)

    def _analyze(
        self,
        identity: SceneIdentity,
        raw: Mapping[str, Any],
        reasons: tuple[str, ...],
        *,
        generation: int,
    ) -> SharedContextPacket | None:
        if not self.available:
            return None
        prompt = self._analysis_prompt(raw, reasons)
        schema = {
            "type": "object",
            "properties": {
                "activity": {"type": "string"},
                "foreground_summary": {"type": "string"},
                "facts": {"type": "array"},
                "inferences": {"type": "array"},
                "unknowns": {"type": "array"},
                "conversation_options": {"type": "array"},
                "research_queries": {"type": "array"},
                "response_mode_scores": {"type": "object"},
            },
            "required": [
                "activity", "foreground_summary", "facts", "inferences",
                "unknowns", "conversation_options", "research_queries", "response_mode_scores",
            ],
        }
        try:
            payload = self.client.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Bạn là Semantic Tutor của ERIS. Bạn KHÔNG nói chuyện với user. "
                            "Hãy biến dữ liệu cảm biến thành JSON ngắn, phân biệt fact trực tiếp, inference và unknown. "
                            "Ưu tiên nội dung foreground thật, media đang phát và structured chess state; app nền chỉ là phụ. "
                            "Không đoán cảm xúc hoặc ý định chắc chắn. response_mode_scores dùng các key: "
                            "screen_analysis, screen_related_small_talk, deep_analysis, cross_context_connection, "
                            "unrelated_small_talk, silence. Mọi score từ 0 đến 1."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                max_tokens=1200,
                timeout=self.timeout_seconds,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "semantic_context_packet", "schema": schema},
                },
            )
            parsed = json.loads(_extract_content(payload))
        except Exception as exc:
            if self.logger is not None:
                try:
                    self.logger.warning("[semantic-tutor] analysis failed: %s", exc)
                except Exception:
                    pass
            return None
        now = time.time()
        with self._lock:
            version = max(self._packet.version + 1, generation)
        scores: dict[str, float] = {}
        for key, value in _mapping(parsed.get("response_mode_scores")).items():
            try:
                scores[_compact(key, 64)] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue
        return SharedContextPacket(
            scene_id=identity.scene_id,
            version=version,
            generated_at=now,
            expires_at=now + self.packet_ttl_seconds,
            status="ready",
            trigger_reasons=reasons,
            activity=_compact(parsed.get("activity") or "unknown", 100),
            foreground_summary=_compact(parsed.get("foreground_summary"), 500),
            facts=_safe_list(parsed.get("facts"), limit=12),
            inferences=_safe_list(parsed.get("inferences"), limit=8),
            unknowns=_safe_list(parsed.get("unknowns"), limit=8),
            conversation_options=_safe_list(parsed.get("conversation_options"), limit=8),
            research_queries=_safe_list(parsed.get("research_queries"), limit=4, item_limit=220),
            response_mode_scores=scores,
            raw_source_summary=self._raw_source_summary(raw),
            source_fingerprint=_stable_hash(raw),
        )

    def _maybe_research(self, packet: SharedContextPacket, *, generation: int) -> None:
        if not self.auto_search or not packet.research_queries:
            return
        search = getattr(self.host, "request_tutor_answer", None)
        if not callable(search):
            return

        # Dataclass slots/frozen không có __dict__, nên cập nhật an toàn bằng replace.
        def safe_work() -> None:
            query = packet.research_queries[0]
            try:
                result = search(
                    query,
                    context=f"activity={packet.activity}; foreground={packet.foreground_summary}",
                    search=True,
                )
            except Exception:
                result = None
            if not result:
                return
            from dataclasses import replace
            with self._condition:
                if generation != self._generation or self._packet.scene_id != packet.scene_id:
                    return
                self._packet = replace(self._packet, research_summary=_compact(result, 900))
                self._condition.notify_all()

        threading.Thread(target=safe_work, name="eris-semantic-research", daemon=True).start()


    def _merge_partial_raw(self, previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
        """Hợp nhất các sensor lane mà không để lane vắng mặt xóa lane khác."""

        merged = dict(previous) if isinstance(previous, Mapping) else {}
        for key, value in current.items():
            if key in {"channel", "user_text"}:
                merged[key] = value
                continue
            if isinstance(value, Mapping):
                old = dict(_mapping(merged.get(key)))
                for subkey, subvalue in value.items():
                    if subvalue not in (None, "", (), [], {}):
                        old[subkey] = subvalue
                    elif subkey not in old:
                        old[subkey] = subvalue
                merged[key] = old
            elif value not in (None, "", (), [], {}):
                merged[key] = value
        return merged

    def _build_raw_context(
        self,
        *,
        channel: str,
        telemetry: Mapping[str, Any],
        screen_context: Any,
        vision_facts: Sequence[Any],
        chess_state: Mapping[str, Any],
        user_text: str,
    ) -> dict[str, Any]:
        screen: dict[str, Any]
        if callable(getattr(screen_context, "to_dict", None)):
            screen = dict(screen_context.to_dict() or {})
        elif isinstance(screen_context, Mapping):
            screen = dict(screen_context)
        else:
            screen = {}
        content_meta = _mapping(telemetry.get("content_metadata"))
        media = _mapping(telemetry.get("media"))
        if not media:
            media = _mapping(content_meta.get("media"))
        browser = _mapping(telemetry.get("browser"))
        return {
            "channel": _compact(channel, 32),
            "user_text": _compact(user_text, 500),
            "screen": {
                "app": _compact(screen.get("process_name") or telemetry.get("active_app"), 100),
                "window_title": _compact(screen.get("window_title") or telemetry.get("active_window_title") or telemetry.get("window_title"), 220),
                "scene_kind": _compact(screen.get("scene_kind"), 80),
                "scene_confidence": screen.get("scene_confidence", 0.0),
                "changed_ratio": screen.get("changed_ratio", 0.0),
                "changed_text": list(_safe_list(screen.get("changed_text"), limit=8)),
                "visible_text": list(_safe_list(screen.get("visible_text"), limit=10)),
                "privacy_blocked": bool(screen.get("privacy_blocked", False)),
            },
            "browser": {
                "tab_id": _compact(_first(telemetry, "browser.tab_id", "tab_id", "active_tab_id"), 120),
                "url": _compact(_first(telemetry, "browser.url", "active_url", "url", "content_metadata.url"), 500),
                "document_id": _compact(_first(telemetry, "browser.document_id", "document_id", "content_metadata.document_id"), 180),
                "page_type": _compact(_first(telemetry, "browser.page_type", "content_metadata.page_type"), 100),
                "title": _compact(browser.get("title") or content_meta.get("title"), 220),
            },
            "media": {
                "title": _compact(_first(telemetry, "media.title", "content_metadata.media.title", "now_playing.title", "content_metadata.title"), 220),
                "artist": _compact(_first(telemetry, "media.artist", "content_metadata.media.artist", "now_playing.artist"), 160),
                "album": _compact(_first(telemetry, "media.album", "now_playing.album"), 160),
                "state": _compact(_first(telemetry, "media.state", "media.playback_state", "now_playing.state"), 40),
                "item_id": _compact(_first(telemetry, "media.item_id", "media.url", "now_playing.id"), 300),
            },
            "content_metadata": {
                "foreground_entity": _compact(content_meta.get("foreground_entity") or content_meta.get("entity_id") or content_meta.get("title"), 220),
                "visible_text_sample": _compact(content_meta.get("visible_text_sample") or content_meta.get("caption_sample"), 500),
                "source": _compact(content_meta.get("source"), 80),
            },
            "vision_facts": list(_safe_list(vision_facts, limit=10)),
            "chess": dict(chess_state),
        }

    def _build_identity(self, raw: Mapping[str, Any]) -> SceneIdentity:
        screen = _mapping(raw.get("screen"))
        browser = _mapping(raw.get("browser"))
        media = _mapping(raw.get("media"))
        content = _mapping(raw.get("content_metadata"))
        chess = _mapping(raw.get("chess"))
        media_id = _compact(media.get("item_id") or "|".join(filter(None, (str(media.get("title") or ""), str(media.get("artist") or "")))), 400)
        content_digest = _stable_hash({
            "changed_text": screen.get("changed_text", []),
            "visible_sample": list(screen.get("visible_text", []))[:4],
            "content_sample": content.get("visible_text_sample", ""),
        })
        return SceneIdentity(
            app=_norm(screen.get("app")),
            window_title=_norm(screen.get("window_title")),
            tab_id=_norm(browser.get("tab_id")),
            url=_norm(browser.get("url")),
            document_id=_norm(browser.get("document_id")),
            foreground_entity=_norm(content.get("foreground_entity")),
            media_id=_norm(media_id),
            chess_game_id=_norm(chess.get("game_id")),
            chess_phase=_norm(chess.get("phase")),
            content_digest=content_digest,
        )

    def _analysis_prompt(self, raw: Mapping[str, Any], reasons: tuple[str, ...]) -> str:
        return (
            "Phân rã scene mới cho ERIS. Chỉ xuất JSON theo schema.\n"
            f"TRIGGER_REASONS={json.dumps(reasons, ensure_ascii=False)}\n"
            "Yêu cầu:\n"
            "1. Xác định activity và foreground thật sự; media đang phát/chess structured có ưu tiên hơn danh sách app nền.\n"
            "2. facts chỉ chứa điều được dữ liệu trực tiếp hỗ trợ.\n"
            "3. inferences phải diễn đạt dè dặt; unknowns ghi rõ phần thiếu.\n"
            "4. conversation_options là chủ đề chat có thể phát triển từ nền này, không phải câu trả lời viết sẵn.\n"
            "5. research_queries chỉ có khi việc tra cứu công khai thật sự giúp hiểu content; tối đa 4 câu.\n"
            "6. Chọn score cho screen_analysis, screen_related_small_talk, deep_analysis, cross_context_connection, unrelated_small_talk, silence.\n"
            f"RAW_CONTEXT={json.dumps(raw, ensure_ascii=False, separators=(',', ':'))}"
        )

    def _raw_source_summary(self, raw: Mapping[str, Any]) -> str:
        screen = _mapping(raw.get("screen"))
        media = _mapping(raw.get("media"))
        chess = _mapping(raw.get("chess"))
        parts = [screen.get("app"), screen.get("window_title"), media.get("title"), chess.get("game_id")]
        return " | ".join(_compact(item, 160) for item in parts if _compact(item, 160))

    def _raw_fallback_mapping(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        screen = _mapping(raw.get("screen"))
        media = _mapping(raw.get("media"))
        browser = _mapping(raw.get("browser"))
        content = _mapping(raw.get("content_metadata"))
        chess = _mapping(raw.get("chess"))
        return {
            "semantic_state": "pending_or_unavailable",
            "perception_policy": "facts_only",
            "forbid_intent_inference": True,
            "app": screen.get("app", ""),
            "window_title": screen.get("window_title", ""),
            "url": browser.get("url", ""),
            "media": dict(media),
            "foreground_entity": content.get("foreground_entity", ""),
            "visible_text": list(screen.get("visible_text", []))[:8],
            "vision_facts": list(raw.get("vision_facts", []))[:8],
            "chess": dict(chess),
        }

    def _raw_fallback_prompt(self, raw: Mapping[str, Any], *, channel: str) -> str:
        fallback = self._raw_fallback_mapping(raw)
        if not any(value for key, value in fallback.items() if key not in {"semantic_state", "perception_policy", "forbid_intent_inference"}):
            return ""
        return (
            "RAW SCREEN CONTEXT FALLBACK (Semantic Tutor chưa có packet mới):\n"
            f"{json.dumps(fallback, ensure_ascii=False)}\n"
            f"channel={channel}\n"
            "Chỉ dùng fact trực tiếp. Không suy đoán user muốn gì, thích gì hoặc đang cảm thấy gì."
        )


__all__ = [
    "EventDrivenSemanticTutor",
    "SceneChangeDetector",
    "SceneIdentity",
    "SharedContextPacket",
]
