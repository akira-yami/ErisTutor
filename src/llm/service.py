import hashlib
import json
import logging
import re
import unicodedata
import os
import time
import threading

import requests
from llm.json_sanitizer import extract_last_valid_json_object, strip_reasoning_envelope
from llm.prompt_compaction import CACHEABLE_CHAT_PREFIX_V2, cacheable_prefix_id
from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR
from llm.token_budget import estimate_tokens

from actions.contracts import VALID_SYSTEM_ACTIONS

from cortex.banned_filler import HOLLOW_PRESENCE_LINES, STALE_MICRO_TEMPLATES, quoted_fragment
from failure_meta import build_failure_meta, normalize_failure_meta
from .no_fallback_policy import build_no_fallback_payload, no_fallback_mode_enabled
from runtime.voice_policy import VOICE_POLICY, ANTI_DISCLAIMER_FEWSHOT as _VOICE_POLICY_FEWSHOT
from .voice_contract import (
    CHAT_VOICE_CORE_V39,
    CHAT_STYLE_RULES_V39,
    AUTONOMOUS_VOICE_CORE_V39,
    AUTONOMOUS_STYLE_RULES_V39,
)
from .contracts import (
    AUTONOMOUS_BEHAVIOR_PROMPT,
    CHAT_RULES_PROMPT,
    DECISION_GBNF,
    DECISION_JSON_FIELDS,
    INFERENCE_LADDER_PROMPT,
    RUNTIME_RULES_PROMPT,
    SELF_IDENTITY_PROFILE_PROMPT,
    STYLE_FALLBACK_PROMPT,
    VALID_ANIMATIONS,
)

try:
    from cortex.greeting_cognition import GreetingCognitionEngine
    from cortex.greeting_presets import GreetingPresetResolver
    from cortex.greeting_quality import GreetingQualityScorer
except Exception:  # Optional during partial rollout; deterministic fallback still works.
    GreetingCognitionEngine = None
    GreetingPresetResolver = None
    GreetingQualityScorer = None

try:
    from runtime.utterance_selector import UtteranceSelector
except Exception:  # Optional during partial rollout; raw model text still works.
    UtteranceSelector = None


class FormatViolationException(Exception):
    """Raised when a model response violates the public JSON contract."""

    def __init__(
        self,
        reason: str,
        *,
        channel: str = "",
        raw_text: str = "",
        kind: str = "format_violation",
        stage: str = "",
        origin: str = "",
        public_surface: str = "blocked",
        recoverable: bool = False,
        transport_truncated: bool = False,
    ):
        super().__init__(reason)
        self.reason = reason
        self.channel = channel
        self.raw_text = str(raw_text or "")
        self.kind = str(kind or "format_violation")
        self.stage = str(stage or "")
        self.origin = str(origin or "")
        self.public_surface = str(public_surface or "blocked")
        self.recoverable = bool(recoverable)
        self.transport_truncated = bool(transport_truncated)
        self.failure_meta = build_failure_meta(
            kind=self.kind,
            stage=self.stage,
            origin=self.origin,
            public_surface=self.public_surface,
            recoverable=self.recoverable,
            transport_truncated=self.transport_truncated,
        )


# Few-shot chống-disclaimer (phần D): dạy model cách ERIS trả lời các câu cảm
# xúc/quan hệ/định danh mà KHÔNG tụt về giọng trợ lý. Đây là demo phong cách,
# không phải câu trả lời bắt buộc — cấm copy nguyên văn, chỉ học cách xử lý.
# Định nghĩa thật sống ở runtime/voice_policy.py (nguồn canonical). Giữ tên cũ làm
# re-export để mọi importer (vd RelationalVoiceSeedAdapter) phân giải nguyên vẹn.
ANTI_DISCLAIMER_FEWSHOT = _VOICE_POLICY_FEWSHOT

def _normalize_vietnamese_ascii_text(text: str) -> str:
    """Syntax-only Vietnamese normalization used by hard output guards.

    PDF annotation, pages 12-13 and 20: normalization remains deterministic,
    while open conversational meaning is owned by ``semantic_frame.v1``.
    """

    normalized = unicodedata.normalize("NFD", str(text or ""))
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.replace("đ", "d").replace("Đ", "D")
    normalized = re.sub(r"[^\w\s?]", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized.lower()).strip()


def _compact_for_candidate_context(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."


def _safe_candidate_confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.55


class LlmService:
    _RETRYABLE_HTTP_STATUS = {500, 502, 503, 504}

    def __init__(
        self,
        client,
        style_client=None,
        vision_client=None,
        fallback_client=None,
        escalation_client=None,
        coding_client=None,
        social_parser_client=None,
        social_critic_client=None,
        social_fallback_client=None,
        observer_client=None,
        critic_client=None,
        inner_client=None,
        diary_client=None,
        residue_curator_client=None,
        research_client=None,
        tutor_client=None,
        learner_client=None,
        curator_client=None,
        seed_client=None,
        surface_client=None,
        executive_client=None,
        logger=None,
    ):
        self.client = client
        self.companion_client = client
        self.style_client = style_client
        self.vision_client = vision_client
        # Named cognitive workers.  Legacy aliases below remain valid so this
        # foundation can be rolled out lane-by-lane instead of demanding a
        # ceremonial rewrite of the whole runtime in one evening.
        self.observer_client = observer_client or social_parser_client
        self.critic_client = critic_client or social_critic_client or fallback_client
        self.inner_client = inner_client or style_client or client
        # Inner Diary có owner riêng. Không fallback ngầm sang companion: nếu worker
        # diary chưa cấu hình, renderer dùng dữ kiện thô thay vì để một lane khác
        # âm thầm viết nhật ký hộ rồi ownership lại hóa thành truyện trinh thám.
        self.diary_client = diary_client
        # Gemma Residue Curator chỉ gom/dedupe/nén private residue. Không fallback
        # sang worker khác để tránh biến một lane sổ sách thành cognition owner.
        self.residue_curator_client = residue_curator_client
        # Worker seed chỉ tạo premise semantic chạy nền trên port riêng. Không
        # fallback sang companion/surface: thiếu lane này thì reservoir tắt.
        self.seed_client = seed_client
        # Surface chỉ viết public wording cho intent đã được governor duyệt.
        # Không fallback ngầm sang companion tại đây.
        self.surface_client = surface_client
        # Gemma Executive Cortex sits between DeepSeek cognition and runtime
        # mechanics. It does not replace any role-owned worker.
        self.executive_client = executive_client
        self.research_client = research_client or fallback_client or client
        # Worker tutor chỉ phân rã scene thành shared semantic context; không có
        # quyền viết câu public và không fallback ngầm sang companion.
        self.tutor_client = tutor_client
        # Assessment chuyên dụng: Root -> Tutor mới được gọi; không có agent loop riêng.
        self.learner_client = learner_client
        # Dedicated memory/profile worker. No implicit fallback here: if the
        # curator credential is absent, living-user enrichment simply remains
        # deterministic rather than borrowing the conversational identity.
        self.curator_client = curator_client
        # Dedicated format-recovery client (e.g. Groq llama). Preferred over
        # style_client when the main provider breaks the JSON/no-CoT contract.
        self.fallback_client = fallback_client
        # Dedicated "strong" client for self-critique escalation (bái sư). When the
        # cheap draft scores low / cần tool, ta chạy lại lượt trên client này. Để
        # None thì tự dùng fallback_client (đã là model mạnh hơn trong cấu hình hiện tại).
        self.escalation_client = escalation_client
        # Dedicated coding authority.  No implicit fallback is allowed here: the
        # Agent Harness must never silently borrow companion/critic/surface and
        # change model ownership just because one credential is missing.
        self.coding_client = coding_client
        # Hai lane advisory cho Social Cognition V2. Chúng không thay model chính
        # và không được phép làm turn chết nếu Groq lỗi.
        self.social_parser_client = self.observer_client or social_parser_client
        self.social_critic_client = self.critic_client or social_critic_client
        self.social_fallback_client = self.observer_client or social_fallback_client
        self.logger = logger or logging.getLogger(__name__)
        # Một governor dùng chung cho mọi LlmService instance trong process. Nó chỉ
        # giữ số token/429 ước lượng, tuyệt đối không giữ nội dung prompt hay API key.
        self.token_pressure_governor = GLOBAL_TOKEN_PRESSURE_GOVERNOR
        self._generation_lock = threading.Lock()
        self._generation_epoch = 0

    def cancel_pending_requests(self, reason: str = "runtime_transition") -> int:
        """Đánh dấu mọi generation đang bay là stale.

        HTTP sync không thể cưỡng bức đóng socket an toàn; timeout theo channel
        đã được hạ và kết quả cũ sẽ bị loại ngay khi request trả về.
        """
        with self._generation_lock:
            self._generation_epoch += 1
            epoch = self._generation_epoch
        self.logger.info("[LLM CANCEL] epoch=%d reason=%s", epoch, reason)
        return epoch

    def _capture_generation_epoch(self) -> int:
        with self._generation_lock:
            return self._generation_epoch

    def _generation_is_current(self, epoch: int) -> bool:
        with self._generation_lock:
            return self._generation_epoch == epoch

    def _resolve_escalation_client(self):
        """Client mạnh để bái sư. Ưu tiên escalation_client riêng, rồi fallback, rồi style."""
        return self.escalation_client or self.fallback_client or self.style_client

    def _http_status_code(self, exc: Exception) -> int | None:
        if not isinstance(exc, requests.HTTPError):
            return None
        response = getattr(exc, "response", None)
        if response is None:
            return None
        status = getattr(response, "status_code", None)
        try:
            return int(status) if status is not None else None
        except (TypeError, ValueError):
            return None

    def _is_retryable_http_5xx(self, exc: Exception) -> bool:
        status = self._http_status_code(exc)
        return status in self._RETRYABLE_HTTP_STATUS

    def _classify_http_failure(self, exc: Exception) -> str:
        status = self._http_status_code(exc)
        raw = str(exc or "").lower()
        if status == 429 or "rate limit" in raw or "quota" in raw:
            return "rate_limited"
        if (
            status == 503
            or "model_not_found" in raw
            or "no available channel" in raw
            or "proxy_api_error" in raw
        ):
            return "provider_unavailable"
        if self._is_context_size_error(exc):
            return "context_oversize"
        return "provider_unavailable" if status in self._RETRYABLE_HTTP_STATUS else "unknown"

    def _failure_meta(
        self,
        *,
        kind: str,
        stage: str,
        origin: str,
        public_surface: str,
        recoverable: bool,
        transport_truncated: bool = False,
    ) -> dict:
        return build_failure_meta(
            kind=kind,
            stage=stage,
            origin=origin,
            public_surface=public_surface,
            recoverable=recoverable,
            transport_truncated=transport_truncated,
        )

    def _service_error_origin(self, *, channel: str) -> str:
        return "chat_service" if channel == "chat" else "runtime_service"

    def _build_service_error_fallback(
        self,
        *,
        channel: str,
        reason: str,
        fallback_text: str = "",
        kind: str = "unknown",
        stage: str = "service_error",
        origin: str = "",
        recoverable: bool = False,
    ) -> str:
        """Return transport metadata without inventing a public sentence."""
        text = self._safe_fallback_text(fallback_text, channel=channel)
        should_speak = bool(text)
        payload = {
            "text": text,
            "should_speak": should_speak,
            "animation": "stand",
            "next_animation": "",
            "direction": 0,
            "target_x": -1.0,
            "bounce_action": "",
            "system_action": "",
            "action_input": "",
            "delivery_hint": "",
            "nonverbal_cue": "",
            "stage_motion": "",
            "failure_meta": self._failure_meta(
                kind=kind,
                stage=stage,
                origin=origin or self._service_error_origin(channel=channel),
                public_surface="service_fallback" if should_speak else "silent",
                recoverable=recoverable,
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _message_text(self, message: dict) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        return " ".join(self._collect_text_chunks(content))

    def _looks_like_direct_question(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if "?" in raw:
            return True
        normalized = self._normalize_vietnamese_ascii(raw)
        if not normalized:
            return False
        question_terms = {
            "ai",
            "bao",
            "chua",
            "co",
            "gi",
            "khong",
            "la",
            "nao",
            "sao",
            "the",
        }
        question_phrases = (
            "ban la ai",
            "ban nghi",
            "ban nhu the nao",
            "co phai",
            "la gi",
            "tai sao",
            "the nao",
            "vi sao",
        )
        tokens = set(normalized.split())
        return bool(tokens & question_terms) or any(phrase in normalized for phrase in question_phrases)

    def _is_short_or_ambiguous_user_text(self, text: str) -> bool:
        normalized = self._normalize_vietnamese_ascii(text or "")
        if not normalized:
            return True
        ambiguous_phrases = {
            "gi",
            "ha",
            "huh",
            "ok",
            "oke",
            "sao",
            "the nao",
            "noi di",
            "ke tiep",
            "thu xem",
        }
        if normalized in ambiguous_phrases:
            return True
        return len(normalized.split()) <= 3

    def _is_self_identity_question(self, text: str) -> bool:
        normalized = self._normalize_vietnamese_ascii(text or "")
        if not normalized:
            return False
        patterns = (
            "ban la ai",
            "ban la gi",
            "ban nghi ban la ai",
            "ban nghi ban la nguoi nhu nao",
            "ban nghi minh nhu the nao",
            "ban thay minh nhu the nao",
            "ban la nguoi nhu nao",
        )
        return any(pattern in normalized for pattern in patterns)

    def _contains_user_intent_inference(self, text: str) -> bool:
        normalized = self._normalize_vietnamese_ascii(text or "")
        if not normalized:
            return False
        patterns = (
            "ban muon",
            "ban dinh",
            "ban dang thu",
            "ban tim toi vi",
            "ban dang ne",
            "ban dang tranh",
            "toi biet ban dang",
            "co ve ban muon",
            "co ve ban dang",
            "hinh nhu ban dang",
            "chac ban dang",
        )
        return any(pattern in normalized for pattern in patterns)

    def _normalize_vietnamese_ascii(self, text: str) -> str:
        return _normalize_vietnamese_ascii_text(text)

    def _rewrite_question_only_chat_answer(self, text: str) -> str:
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if not compact:
            return "Tôi chưa đủ chắc để đoán ý bạn."
        sentences = re.split(r"(?<=[\.\!\?])\s+", compact)
        if len(sentences) > 1 and sentences[-1].endswith("?"):
            kept = " ".join(part.strip() for part in sentences[:-1] if part.strip()).strip()
            if kept:
                return kept
        if compact.endswith("?"):
            return "Tôi chưa đủ chắc để đoán ý bạn."
        return compact

    def _append_reasoning_tag(self, payload: dict, tag: str) -> None:
        tags = self._coerce_reasoning_tags_value(payload.get("reasoning_tags", []))
        if tag not in tags:
            tags.append(tag)
        payload["reasoning_tags"] = tags[:8]

    def _looks_like_relational_feedback_text(self, text: str) -> bool:
        normalized = self._normalize_vietnamese_ascii(text or "")
        if not normalized:
            return False
        markers = (
            "lanh", "lanh lung", "xa cach", "vo hon", "robot", "nhu bot", "chatbot",
            "assistant", "support", "may moc", "cung qua", "khong tu nhien", "corporate",
        )
        weather_or_object = ("troi lanh", "thoi tiet lanh", "may lanh", "phong lanh", "nuoc lanh")
        if any(item in normalized for item in weather_or_object):
            return False
        return any(marker in normalized for marker in markers)

    def _looks_like_defensive_relational_reply(self, text: str) -> bool:
        normalized = self._normalize_vietnamese_ascii(text or "")
        if not normalized:
            return False
        patterns = (
            "toi chi dang tra loi",
            "dung nhung gi ban hoi",
            "toi se dieu chinh lai neu ban thay can thiet",
            "neu ban thay can thiet",
            "neu ban muon toi se",
            "toi khong co cam xuc",
            "toi la mot ai",
            "toi la ai",
        )
        return any(pattern in normalized for pattern in patterns)

    def _relational_repair_fallback_text(self, raw_user_text: str, current_text: str = "") -> str:
        normalized_user = self._normalize_vietnamese_ascii(raw_user_text or "")
        if "robot" in normalized_user or "vo hon" in normalized_user or "may moc" in normalized_user:
            return "Ừ. Câu đó nghe máy quá. Tôi hạ giọng xuống một chút."
        if "xa cach" in normalized_user:
            return "Có lẽ vậy. Tôi đang giữ khoảng cách hơi mạnh."
        if "lanh" in normalized_user:
            return "Ừ. Câu đó lạnh thật. Tôi hạ giọng xuống một chút."
        return "Tôi nhận ra rồi. Vừa rồi tôi giữ giọng hơi quá tay."

    def _guard_chat_output(self, raw_json: str, *, raw_user_text: str) -> str:
        """Compatibility no-op.

        Main model output is not rewritten before parsing. Relational feedback,
        provocation and style criticism belong in normal conversation context,
        not in a Python phrase detector that swaps the sentence behind its back.
        """
        return raw_json

    def _contextual_service_fallback_text(self, messages: list[dict], *, channel: str) -> str:
        if channel != "chat":
            return ""

        user_texts = [
            self._message_text(message).strip()
            for message in messages
            if str(message.get("role")) == "user" and self._message_text(message).strip()
        ]
        assistant_texts = [
            self._message_text(message).strip()
            for message in messages
            if str(message.get("role")) == "assistant" and self._message_text(message).strip()
        ]
        latest_user = user_texts[-1] if user_texts else ""
        previous_user = " ".join(user_texts[:-1])
        normalized_latest = self._normalize_vietnamese_ascii(latest_user)
        normalized_previous = self._normalize_vietnamese_ascii(previous_user)

        asks_to_try = any(
            marker in normalized_latest
            for marker in (
                "noi thu xem",
                "thu noi xem",
                "ban noi thu",
                "noi di",
            )
        )
        asks_user_knowledge = any(
            marker in normalized_previous or marker in normalized_latest
            for marker in (
                "biet gi ve toi",
                "biet gi ve minh",
                "ban biet gi",
            )
        )
        if asks_user_knowledge and asks_to_try:
            return (
                "Câu trước đang hỏi tôi biết gì về bạn. Tôi chỉ có những điều bạn đã nói trong cuộc trò chuyện "
                "và ký ức runtime còn lưu; lượt gọi model vừa lỗi nên tôi không tự dựng thêm chi tiết."
            )
        if asks_user_knowledge:
            return (
                "Tôi chỉ biết những gì có trong cuộc trò chuyện này và bộ nhớ runtime còn lưu; "
                "lượt gọi model vừa lỗi nên tôi sẽ không tự dựng thêm chi tiết."
            )
        if assistant_texts and asks_to_try:
            return f"Câu trả lời gần nhất của tôi là: {assistant_texts[-1]}"
        return ""

    def _log_final_prompt(self, messages: list[dict], *, channel: str, stage: str) -> None:
        try:
            dumped = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            dumped = repr(messages)
        digest = hashlib.sha256(dumped.encode("utf-8", errors="replace")).hexdigest()[:16]
        roles = [str(item.get("role", "")) for item in messages if isinstance(item, dict)]
        self.logger.info(
            "[LLM PROMPT META] channel=%s stage=%s hash=%s chars=%s messages=%s roles=%s",
            channel,
            stage,
            digest,
            len(dumped),
            len(messages),
            ",".join(roles[:12]),
        )
        if str(os.getenv("ERIS_DEBUG_FULL_PROMPT", "0") or "0").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            self.logger.debug(
                "[LLM FINAL PROMPT DEBUG] channel=%s stage=%s hash=%s\n%s",
                channel,
                stage,
                digest,
                dumped[:12000],
            )

    def _preview_log_text(self, text: str, *, limit: int = 12000) -> str:
        preview = (text or "").replace("\n", "\\n").strip()
        if len(preview) > limit:
            preview = preview[:limit] + "...(truncated)"
        return preview or "<empty>"

    def _looks_like_prompt_leak(self, text: str) -> bool:
        """Detect raw prompt/contract text that must never become public speech."""
        raw = str(text or "")
        if not raw.strip():
            return False
        lowered = raw.lower()
        markers = (
            "speech intent:",
            "speech evidence",
            "persona:",
            "constraints:",
            "return only",
            "valid json object",
            "critical output contract",
            "llm final prompt",
            "ghi chú nền",
            "goi y dieu phoi",
            "gợi ý điều phối",
            "runtime requirement for this call",
            "style principle:",
            "memory context for continuity",
            "grounding context:",
            "chat contract:",
            "inference ladder",
        )
        # A single phrase can be a normal sentence. Two prompt markers means this
        # is almost certainly a leaked instruction block, i.e. public-output poison.
        hits = sum(1 for marker in markers if marker in lowered)
        bulletish = raw.count("*   ") + raw.count("- ") >= 2
        return hits >= 2 or (hits >= 1 and bulletish)

    def _looks_like_malformed_decision_json(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw.startswith("{"):
            return False
        if self._parse_json_object(raw) is not None:
            return False
        decision_markers = (
            '"text"',
            '"intent"',
            '"action"',
            '"reasoning_tags"',
            '"should_speak"',
            '"confidence"',
            '"animation"',
            '"debug_trace"',
            '"system_action"',
        )
        return any(marker in raw for marker in decision_markers)

    def _json_string_field_from_raw(self, raw_text: str, field: str) -> str:
        raw = str(raw_text or "")
        pattern = re.compile(rf'"{re.escape(field)}"\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"', re.DOTALL)
        match = pattern.search(raw)
        if not match:
            return ""
        value = match.group("value")
        try:
            return json.loads(f'"{value}"')
        except Exception:
            return value.replace('\\n', ' ').replace('\\"', '"').strip()

    def _build_protocol_surface_fallback(
        self,
        *,
        channel: str,
        reason: str,
        text: str = "",
        kind: str = "prompt_leak",
        stage: str = "llm_response_parse",
        origin: str = "model_output",
    ) -> str:
        """Block protocol leaks silently; the parser is not a dialogue author."""
        payload = {
            "text": "",
            "should_speak": False,
            "animation": "stand",
            "next_animation": "",
            "direction": 0,
            "target_x": -1.0,
            "bounce_action": "",
            "system_action": "",
            "action_input": "",
            "delivery_hint": "",
            "nonverbal_cue": "",
            "stage_motion": "",
            "failure_meta": self._failure_meta(
                kind=kind,
                stage=stage,
                origin=origin,
                public_surface="silent",
                recoverable=False,
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _coerce_malformed_decision_payload(self, raw_text: str, *, channel: str) -> str:
        """Salvage only a clean text field; never expose or narrate protocol failure."""
        if not self._looks_like_malformed_decision_json(raw_text):
            return ""
        text = self._json_string_field_from_raw(raw_text, "text").strip()
        if not text or self._looks_like_prompt_leak(text):
            return self._build_protocol_surface_fallback(
                channel=channel,
                reason="malformed_decision_without_safe_text",
                kind="format_violation",
            )
        payload = {
            "text": text,
            "should_speak": True,
            "animation": "stand",
            "next_animation": "",
            "direction": 0,
            "target_x": -1.0,
            "bounce_action": "",
            "system_action": "",
            "action_input": "",
            "delivery_hint": "",
            "nonverbal_cue": "",
            "stage_motion": "",
            "failure_meta": self._failure_meta(
                kind="format_violation",
                stage="llm_response_parse",
                origin="model_output",
                public_surface="service_fallback",
                recoverable=True,
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _log_raw_api_text(self, channel: str, raw: str) -> None:
        self.logger.info("[%s raw_api_text] %s", channel, self._preview_log_text(raw))

    def _is_context_size_error(self, exc: Exception) -> bool:
        text = str(exc)
        return (
            "exceed_context_size_error" in text
            or "exceeds the available context size" in text
            or ("available context size" in text and "n_ctx" in text)
        )

    def _parse_context_error_stats(self, exc: Exception) -> tuple[int | None, int | None]:
        text = str(exc)
        n_prompt = None
        n_ctx = None

        prompt_match = re.search(r"\"n_prompt_tokens\"\s*:\s*(\d+)", text)
        if prompt_match:
            try:
                n_prompt = int(prompt_match.group(1))
            except ValueError:
                n_prompt = None

        ctx_match = re.search(r"\"n_ctx\"\s*:\s*(\d+)", text)
        if ctx_match:
            try:
                n_ctx = int(ctx_match.group(1))
            except ValueError:
                n_ctx = None

        return n_ctx, n_prompt

    def _content_text_len(self, content) -> int:
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            total = 0
            for item in content:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        total += len(item["text"])
                    nested = item.get("content")
                    if nested is not None:
                        total += self._content_text_len(nested)
                elif isinstance(item, str):
                    total += len(item)
            return total
        if isinstance(content, dict):
            return self._content_text_len(content.get("text")) + self._content_text_len(content.get("content"))
        return 0

    def _truncate_content(self, content, *, max_chars: int, keep: str) -> object:
        if max_chars <= 0:
            max_chars = 1

        if isinstance(content, str):
            if len(content) <= max_chars:
                return content
            if keep == "tail":
                return content[-max_chars:]
            return content[:max_chars]

        if isinstance(content, list):
            truncated: list[object] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    new_item = dict(item)
                    new_item["text"] = self._truncate_content(item["text"], max_chars=max_chars, keep=keep)
                    truncated.append(new_item)
                else:
                    truncated.append(item)
            return truncated

        return content

    def _shrink_messages_for_context(
        self,
        messages: list[dict],
        *,
        n_ctx: int,
        max_tokens: int,
        margin_tokens: int = 160,
    ) -> list[dict]:
        if not messages:
            return messages

        system_msgs = [msg for msg in messages if str(msg.get("role")) == "system"]
        non_system = [msg for msg in messages if str(msg.get("role")) != "system"]
        # Keep enough short-term dialogue for follow-ups like "nói thử xem".
        # Sixteen messages is roughly the latest 8 user/assistant turns.
        if len(non_system) > 16:
            non_system = non_system[-16:]
        reduced = [*system_msgs, *non_system]

        budget_tokens = max(256, int(n_ctx) - int(max_tokens) - int(margin_tokens))
        budget_chars = budget_tokens * 4  # rough heuristic
        current_chars = sum(self._content_text_len(msg.get("content")) for msg in reduced)
        if current_chars <= budget_chars:
            return reduced

        ratio = max(0.10, min(0.95, budget_chars / max(1, current_chars)))
        shrunk: list[dict] = []
        for msg in reduced:
            role = str(msg.get("role") or "")
            content = msg.get("content")
            size = self._content_text_len(content)

            # Do not truncate system prompts (they contain the contracts and memory context).
            if role == "system":
                shrunk.append(msg)
                continue

            base = max(200, int(size * ratio))
            if role == "user":
                base = max(base, 900)
                keep = "head"
            else:
                base = max(base, 480)
                keep = "tail"

            new_msg = dict(msg)
            new_msg["content"] = self._truncate_content(content, max_chars=base, keep=keep)
            shrunk.append(new_msg)

        return shrunk

    def _collect_text_chunks(self, value) -> list[str]:
        chunks: list[str] = []

        if isinstance(value, str):
            if value:
                chunks.append(value)
            return chunks

        if isinstance(value, list):
            for item in value:
                chunks.extend(self._collect_text_chunks(item))
            return chunks

        if isinstance(value, dict):
            # Skip Gemini reasoning/thinking parts
            if value.get("thought") is True:
                return chunks

            for key in (
                "content",
                "text",
                "output_text",
                "refusal",
                "parts",
                "candidates",
            ):
                if key in value:
                    chunks.extend(self._collect_text_chunks(value.get(key)))

            for key in ("message", "delta"):
                nested = value.get(key)
                if nested is not None:
                    chunks.extend(self._collect_text_chunks(nested))

            return chunks

        return chunks

    def _extract_content(self, response: dict) -> str:
        if not isinstance(response, dict):
            return ""

        for key in ("output_text", "text", "content"):
            direct = response.get(key)
            chunks = self._collect_text_chunks(direct)
            if chunks:
                return "\n".join(chunk for chunk in chunks if chunk)

        choices = response.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                chunks = self._collect_text_chunks(choice)
                if chunks:
                    return "\n".join(chunk for chunk in chunks if chunk)

        data = response.get("data")
        chunks = self._collect_text_chunks(data)
        if chunks:
            return "\n".join(chunk for chunk in chunks if chunk)

        candidates = response.get("candidates")
        chunks = self._collect_text_chunks(candidates)
        if chunks:
            return "\n".join(chunk for chunk in chunks if chunk)

        return json.dumps(response, ensure_ascii=False)


    def _extract_reasoning(self, response: dict) -> str:
        """Lấy reasoning riêng để telemetry, tuyệt đối không nhập vào content/history."""

        if not isinstance(response, dict):
            return ""
        choices = response.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if isinstance(message, dict):
                    for key in ("reasoning_content", "reasoning"):
                        value = message.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
                for key in ("reasoning_content", "reasoning"):
                    value = choice.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        for key in ("reasoning_content", "reasoning"):
            direct = response.get(key)
            if isinstance(direct, str) and direct.strip():
                return direct.strip()
        return ""

    def _extract_finish_reason(self, response: dict) -> str:
        if not isinstance(response, dict):
            return ""
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            return str(choices[0].get("finish_reason") or "").strip().lower()
        return str(response.get("finish_reason") or "").strip().lower()

    def _log_reasoning_telemetry(self, channel: str, response: dict, content: str) -> None:
        reasoning = self._extract_reasoning(response)
        finish_reason = self._extract_finish_reason(response) or "unknown"
        usage = response.get("usage") if isinstance(response, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        self.logger.info(
            "[%s reasoning] chars=%d content_chars=%d finish_reason=%s prompt_tokens=%s completion_tokens=%s",
            channel,
            len(reasoning),
            len(content or ""),
            finish_reason,
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
        )

    def _extract_first_json_object(self, text: str) -> str:
        text = (text or "").strip()
        start = text.find("{")
        if start == -1:
            return ""

        depth = 0
        in_string = False
        escaped = False

        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]

        return ""

    def _parse_json_object(self, text: str) -> dict | None:
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if isinstance(payload, dict):
            return payload
        return None

    def _normalize_payload_keys(self, value):
        if isinstance(value, list):
            return [self._normalize_payload_keys(item) for item in value]
        if not isinstance(value, dict):
            return value

        normalized = {}
        for key, item in value.items():
            normalized_key = key.strip() if isinstance(key, str) else key
            normalized_item = self._normalize_payload_keys(item)
            if normalized_key in normalized and normalized_key != key:
                continue
            normalized[normalized_key] = normalized_item
        return normalized

    def _is_valid_decision_payload(self, payload: dict | None) -> bool:
        """Validate the compact public model contract.

        Intent, confidence, reasoning tags and debug traces are runtime-owned
        metadata. Accepting them as optional legacy extras is harmless, but they
        are no longer required or generated by the main model.
        """
        if not isinstance(payload, dict):
            return False
        if not all(field in payload for field in DECISION_JSON_FIELDS):
            return False
        if not self._is_valid_text_field(payload.get("text")):
            return False
        if not isinstance(payload.get("should_speak"), bool):
            return False
        if not self._is_valid_animation_field(payload.get("animation"), allow_empty=False):
            return False
        if not self._is_valid_animation_field(payload.get("next_animation"), allow_empty=True):
            return False
        if not self._is_valid_choice_number(payload.get("direction"), {-1, 0, 1}):
            return False
        if not self._is_valid_target_x(payload.get("target_x")):
            return False
        if not self._allows_motion_target(payload) and float(payload.get("target_x", -1.0)) != -1.0:
            return False
        if not self._is_valid_animation_field(payload.get("bounce_action"), allow_empty=True):
            return False
        if not self._is_valid_system_action_field(payload.get("system_action")):
            return False
        if not self._is_valid_text_field(payload.get("action_input")):
            return False
        if not self._is_valid_text_field(payload.get("delivery_hint", "")):
            return False
        if not self._is_valid_text_field(payload.get("nonverbal_cue", "")):
            return False
        if not self._is_valid_text_field(payload.get("stage_motion", "")):
            return False
        return True

    def _is_valid_motion_payload(self, payload: dict | None) -> bool:
        # Compatibility alias for callers/tests using the old name.
        return self._is_valid_decision_payload(payload)

    def _is_valid_cognition_payload(self, payload: dict | None) -> bool:
        # Legacy payloads may still arrive from cached/local providers. They are
        # accepted only when the compact transport fields are also valid.
        return self._is_valid_decision_payload(payload)

    def _is_decision_like_payload(self, payload: dict | None) -> bool:
        if not isinstance(payload, dict):
            return False
        if "text" in payload and any(field in payload for field in DECISION_JSON_FIELDS if field != "text"):
            return True
        response = payload.get("response")
        if isinstance(response, list) and response and isinstance(response[0], dict):
            item = response[0]
            return "text" in item and any(field in item for field in DECISION_JSON_FIELDS if field != "text")
        return False

    def _coerce_bool_value(self, value, *, fallback: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value != 0
        text = str(value or "").strip().lower()
        if text in {"true", "yes", "y", "1", "speak", "noi", "nói"}:
            return True
        if text in {"false", "no", "n", "0", "silent", "silence", "im", "im_lang", "im lặng"}:
            return False
        return fallback

    def _coerce_confidence_value(self, value) -> float:
        if isinstance(value, bool):
            return 0.0
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def _coerce_reasoning_tags_value(self, value) -> list[str]:
        if isinstance(value, str):
            items = re.split(r"[,|;/]+", value)
        elif isinstance(value, list):
            items = value
        else:
            items = []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            tag = re.sub(r"\s+", "_", str(item or "").strip().lower())
            tag = re.sub(r"[^a-z0-9_\-]+", "", tag)
            if not tag or tag in seen:
                continue
            cleaned.append(tag[:40])
            seen.add(tag)
            if len(cleaned) >= 8:
                break
        return cleaned

    def _coerce_action_value(self, value):
        if isinstance(value, dict):
            return value
        text = str(value or "").strip()
        return text

    def _action_field(self, action, *names, default=None):
        if not isinstance(action, dict):
            return default
        for name in names:
            if name in action:
                return action.get(name)
        return default

    def _is_valid_text_field(self, value) -> bool:
        return isinstance(value, str)

    def _is_valid_animation_field(self, value, *, allow_empty: bool) -> bool:
        if not isinstance(value, str):
            return False
        if not value:
            return allow_empty
        return value in VALID_ANIMATIONS

    def _is_valid_system_action_field(self, value) -> bool:
        if not isinstance(value, str):
            return False
        if not value:
            return True
        return value.strip() in VALID_SYSTEM_ACTIONS

    def _is_valid_choice_number(self, value, choices: set[int]) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value in choices
        if isinstance(value, float) and value.is_integer():
            return int(value) in choices
        return False

    def _is_valid_target_x(self, value) -> bool:
        if isinstance(value, bool):
            return False
        if not isinstance(value, (int, float)):
            return False
        numeric = float(value)
        return numeric == -1.0 or 0.0 <= numeric <= 1.0

    def _allows_motion_target(self, payload: dict) -> bool:
        direction = self._coerce_direction_value(payload.get("direction", 0))
        animation = self._coerce_animation_value(payload.get("animation"), allow_empty=True)
        next_animation = self._coerce_animation_value(payload.get("next_animation"), allow_empty=True)
        return (
            direction != 0
            or animation in {"stand_walk", "climb", "climb_top"}
            or next_animation in {"stand_walk", "climb_top"}
        )

    def _coerce_text_value(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "; ".join(self._coerce_text_value(item) for item in value if item is not None)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _coerce_animation_value(self, value, *, allow_empty: bool) -> str:
        if value is None:
            return "" if allow_empty else "stand"
        animation = str(value or "").strip().strip('"').strip("'").lower()
        if animation in VALID_ANIMATIONS:
            return animation
        return "" if allow_empty else "stand"

    def _coerce_direction_value(self, value) -> int:
        if isinstance(value, bool):
            return 0
        try:
            direction = int(value)
        except (TypeError, ValueError):
            return 0
        return direction if direction in {-1, 0, 1} else 0

    def _coerce_target_x_value(self, value, *, allow_motion_target: bool) -> float:
        if not allow_motion_target or isinstance(value, bool):
            return -1.0
        try:
            target_x = float(value)
        except (TypeError, ValueError):
            return -1.0
        if target_x == -1.0:
            return -1.0
        if 0.0 <= target_x <= 1.0:
            return target_x
        return -1.0

    def _coerce_system_action_value(self, value) -> str:
        raw_action = str(value or "").strip()
        if raw_action in VALID_SYSTEM_ACTIONS:
            return raw_action
        return ""

    def _coerce_decision_like_payload(self, payload: dict | None) -> str:
        payload = self._normalize_payload_keys(payload)
        if not self._is_decision_like_payload(payload):
            return ""

        source = payload
        response = payload.get("response") if isinstance(payload, dict) else None
        if isinstance(response, list) and response and isinstance(response[0], dict):
            source = response[0]

        legacy_action = self._coerce_action_value(source.get("action", ""))
        animation = self._coerce_animation_value(
            source.get("animation") or self._action_field(legacy_action, "animation", "pose"),
            allow_empty=False,
        )
        direction = self._coerce_direction_value(
            source.get("direction", self._action_field(legacy_action, "direction", "facing", default=0))
        )
        next_animation = self._coerce_animation_value(
            source.get("next_animation") or self._action_field(legacy_action, "next_animation"),
            allow_empty=True,
        )
        bounce_action = self._coerce_animation_value(
            source.get("bounce_action") or self._action_field(legacy_action, "bounce_action"),
            allow_empty=True,
        )
        allow_motion_target = (
            direction != 0
            or animation in {"stand_walk", "climb", "climb_top"}
            or next_animation in {"stand_walk", "climb_top"}
        )
        target_x = self._coerce_target_x_value(
            source.get("target_x", self._action_field(legacy_action, "target_x", "x", default=-1.0)),
            allow_motion_target=allow_motion_target,
        )
        system_action = self._coerce_system_action_value(
            source.get("system_action", "")
            or self._action_field(legacy_action, "system_action", "tool", "name", default="")
        )
        action_input = self._coerce_text_value(
            source.get("action_input", "")
            or self._action_field(legacy_action, "action_input", "input", "query", "url", "argument", default="")
        )

        sanitized = {
            "text": self._coerce_text_value(source.get("text", "")),
            "should_speak": self._coerce_bool_value(
                source.get("should_speak", bool(source.get("text", ""))),
                fallback=bool(source.get("text", "")),
            ),
            "animation": animation,
            "next_animation": next_animation,
            "direction": direction,
            "target_x": target_x,
            "bounce_action": bounce_action,
            "system_action": system_action,
            "action_input": action_input if system_action else "",
            "delivery_hint": self._coerce_text_value(source.get("delivery_hint", "")),
            "nonverbal_cue": self._coerce_text_value(source.get("nonverbal_cue", "")),
            "stage_motion": self._coerce_text_value(source.get("stage_motion", "")),
        }
        failure_meta = normalize_failure_meta(source.get("failure_meta"))
        if failure_meta is not None:
            sanitized["failure_meta"] = failure_meta
        return json.dumps(sanitized, ensure_ascii=False)

    def _coerce_json_candidate(self, raw_text: str) -> str:
        # Qwen có thể nhét <think> trước JSON. Bóc tại biên format,
        # không đợi parser cuối rồi retry cùng một câu trả lời hợp lệ.
        raw_text = strip_reasoning_envelope(raw_text)
        if not raw_text:
            return ""

        if self._looks_like_prompt_leak(raw_text):
            return ""

        direct_payload = self._parse_json_object(raw_text)
        if self._is_valid_decision_payload(direct_payload):
            return raw_text
        normalized_direct_payload = self._normalize_payload_keys(direct_payload)
        if normalized_direct_payload != direct_payload:
            if self._is_valid_decision_payload(normalized_direct_payload):
                return json.dumps(normalized_direct_payload, ensure_ascii=False)
            if self._is_decision_like_payload(normalized_direct_payload):
                return self._coerce_decision_like_payload(normalized_direct_payload)
        if self._is_decision_like_payload(direct_payload):
            return self._coerce_decision_like_payload(direct_payload)

        extracted = extract_last_valid_json_object(raw_text) or self._extract_first_json_object(raw_text)
        extracted_payload = self._parse_json_object(extracted)
        if self._is_valid_decision_payload(extracted_payload):
            return extracted
        normalized_extracted_payload = self._normalize_payload_keys(extracted_payload)
        if normalized_extracted_payload != extracted_payload:
            if self._is_valid_decision_payload(normalized_extracted_payload):
                return json.dumps(normalized_extracted_payload, ensure_ascii=False)
            if self._is_decision_like_payload(normalized_extracted_payload):
                return self._coerce_decision_like_payload(normalized_extracted_payload)
        if self._is_decision_like_payload(extracted_payload):
            return self._coerce_decision_like_payload(extracted_payload)

        return ""

    def _build_json_fallback(self, raw_text: str, *, channel: str) -> str:
        raise FormatViolationException(
            f"LlmService blocked invalid {channel} JSON instead of wrapping it as fallback.",
            channel=channel,
            raw_text=raw_text,
        )

    def _looks_like_provider_metadata(self, text: str) -> bool:
        candidate = (text or "").strip()
        if not candidate.startswith("{"):
            return False
        payload = self._parse_json_object(candidate)
        if not isinstance(payload, dict):
            return False
        provider_keys = {"candidates", "usageMetadata", "modelVersion", "responseId", "promptFeedback"}
        return any(key in payload for key in provider_keys)

    def _looks_like_markdown_json_stub(self, text: str) -> bool:
        normalized = (text or "").strip().lower()
        return (
            normalized.startswith("here is the json")
            or normalized in {"```json", "```"}
            or normalized.endswith("```json")
        )

    def _safe_fallback_text(self, raw_text: str, *, channel: str) -> str:
        text = (raw_text or "").strip()
        if not text:
            return ""
        if self._looks_like_prompt_leak(text) or self._looks_like_malformed_decision_json(text):
            return ""
        if self._looks_like_provider_metadata(text) or self._looks_like_markdown_json_stub(text):
            return ""
        return text

    def _build_empty_text_fallback(self, *, channel: str) -> str:
        payload = {
            "text": "",
            "should_speak": False,
            "animation": "stand",
            "next_animation": "",
            "direction": 0,
            "target_x": -1.0,
            "bounce_action": "",
            "system_action": "",
            "action_input": "",
            "delivery_hint": "",
            "nonverbal_cue": "",
            "stage_motion": "",
            "failure_meta": self._failure_meta(
                kind="empty_text",
                stage=f"empty_text_{channel}",
                origin="model_output",
                public_surface="silent",
                recoverable=True,
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _decision_text_is_invalid(self, raw_text: str) -> bool:
        payload = self._parse_json_object(raw_text)
        if not isinstance(payload, dict):
            return False
        system_action = self._coerce_system_action_value(payload.get("system_action", ""))
        if system_action:
            return False
        if isinstance(payload.get("failure_meta"), dict):
            return False
        text = self._coerce_text_value(payload.get("text", "")).strip()
        inline_metadata = bool(re.search(r'[,\s]["\']?(?:intent|action|reasoning_tags|should_speak|confidence|debug_trace|delivery_hint|nonverbal_cue|stage_motion)["\']?\s*:', text))
        return (
            not text
            or self._looks_like_provider_metadata(text)
            or self._looks_like_markdown_json_stub(text)
            or self._looks_like_prompt_leak(text)
            or inline_metadata
        )


    def _with_non_empty_text_rule(self, messages: list[dict]) -> list[dict]:
        rule = (
            "Runtime requirement for this call: text must be a non-empty, speakable sentence "
            "unless system_action is non-empty. If evidence is thin, react to the nearest interaction rhythm; "
            "do not return an empty text field."
        )
        if not messages:
            return [{"role": "user", "content": rule}]
        updated = [dict(message) for message in messages]
        last = dict(updated[-1])
        content = last.get("content", "")
        if isinstance(content, str):
            last["content"] = f"{content}\n\n{rule}"
        elif isinstance(content, list):
            last["content"] = [*content, {"type": "text", "text": rule}]
        else:
            last["content"] = f"{content}\n\n{rule}"
        updated[-1] = last
        return updated

    def _repair_empty_decision_text(self, *, messages: list[dict], channel: str, previous_json: str) -> str:
        raise FormatViolationException(
            f"LlmService blocked empty-text {channel} JSON instead of repairing it.",
            channel=channel,
            raw_text=previous_json,
        )
        retry_messages = self._with_non_empty_text_rule(messages)
        retry_messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous JSON had an empty text field, which is invalid for this speaking turn. "
                    "Return the same schema again with a non-empty text field. "
                    "Do not add unsupported screen/action claims.\n"
                    f"Previous JSON:\n{previous_json}"
                ),
            }
        )
        try:
            try:
                response = self.client.create_chat_completion(
                    messages=retry_messages,
                    temperature=0.25,
                    frequency_penalty=0.5 if channel == "chat" else 0.8,
                    presence_penalty=0.4 if channel == "chat" else 0.5,
                    max_tokens=320,
                    response_format={"type": "json_object"},
                    grammar=DECISION_GBNF,
                )
            except requests.HTTPError as exc:
                if self._is_retryable_http_5xx(exc):
                    time.sleep(0.15)
                    response = self.client.create_chat_completion(
                        messages=retry_messages,
                        temperature=0.25,
                        frequency_penalty=0.5 if channel == "chat" else 0.8,
                        presence_penalty=0.4 if channel == "chat" else 0.5,
                        max_tokens=320,
                        response_format={"type": "json_object"},
                        grammar=DECISION_GBNF,
                    )
                else:
                    raise
            raw = self._extract_content(response)
            repaired = self._coerce_json_candidate(raw) or self._repair_decision_json(raw, channel=channel)
            if repaired and not self._decision_text_is_invalid(repaired):
                return repaired
        except Exception:
            pass
        return self._build_service_error_fallback(
            channel=channel,
            reason=f"http_5xx_fallback stage=repair_empty_decision_text channel={channel}",
            kind="provider_unavailable",
            stage="repair_empty_decision_text",
            origin="primary",
        )

    def _repair_decision_json(self, raw_text: str, *, channel: str) -> str:
        raise FormatViolationException(
            f"LlmService blocked invalid {channel} JSON instead of repairing it.",
            channel=channel,
            raw_text=raw_text,
        )
        if self._looks_like_prompt_leak(raw_text):
            raise FormatViolationException(
                f"LlmService refused to repair prompt/contract leak as public {channel} text.",
                channel=channel,
                raw_text=raw_text,
            )
        if self._looks_like_malformed_decision_json(raw_text):
            salvaged = self._coerce_malformed_decision_payload(raw_text, channel=channel)
            if salvaged:
                return salvaged
            raise FormatViolationException(
                f"LlmService blocked malformed decision JSON on {channel}.",
                channel=channel,
                raw_text=raw_text,
            )

        repaired = self._coerce_json_candidate(raw_text)
        if repaired:
            return repaired

        repair_system = (
            "You are a strict JSON repair layer for ERIS. "
            "Convert the assistant answer into one valid JSON object only. "
            "Do not add markdown or commentary. "
            "Preserve meaning. If a field is missing, use conservative defaults."
        )
        repair_user = (
            f"Channel: {channel}\n"
            "Return exactly one JSON object with fields:\n"
            "text, should_speak, animation, next_animation, direction, target_x, bounce_action, "
            "system_action, action_input, delivery_hint, nonverbal_cue, stage_motion.\n"
            "Do not put stage directions inside text; use delivery_hint/nonverbal_cue/stage_motion.\n"
            "If the raw answer is plain prose, put it into text and keep other fields safe.\n"
            "Raw assistant answer:\n"
            f"{raw_text}"
        )
        try:
            response = self.client.create_chat_completion(
                messages=[
                    {"role": "system", "content": repair_system},
                    {"role": "user", "content": repair_user},
                ],
                temperature=0.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                max_tokens=320,
                response_format={"type": "json_object"},
                grammar=DECISION_GBNF,
            )
        except requests.HTTPError as exc:
            if self._is_retryable_http_5xx(exc):
                time.sleep(0.15)
                response = self.client.create_chat_completion(
                    messages=[
                        {"role": "system", "content": repair_system},
                        {"role": "user", "content": repair_user},
                    ],
                    temperature=0.0,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    max_tokens=320,
                    response_format={"type": "json_object"},
                    grammar=DECISION_GBNF,
                )
            else:
                raise
        repaired = self._extract_content(response)
        repaired_candidate = self._coerce_json_candidate(repaired)
        if repaired_candidate and not self._decision_text_is_invalid(repaired_candidate):
            return repaired_candidate
        return self._build_json_fallback(raw_text, channel=channel)

    def _strict_decision_candidate_or_raise(self, raw: str, *, channel: str) -> str:
        original_raw = str(raw or "").strip()
        raw_text = strip_reasoning_envelope(original_raw)
        if not raw_text:
            raise FormatViolationException(
                f"LlmService received empty {channel} response.",
                channel=channel,
                raw_text=raw,
                kind="format_violation",
                stage="llm_response_parse",
                origin="model_output",
                public_surface="blocked",
                recoverable=False,
            )
        if self._looks_like_malformed_decision_json(raw_text):
            salvaged = self._coerce_malformed_decision_payload(raw_text, channel=channel)
            if salvaged:
                return salvaged
            raise FormatViolationException(
                f"LlmService blocked malformed decision JSON on {channel}.",
                channel=channel,
                raw_text=raw_text,
                kind="format_violation",
                stage="llm_response_parse",
                origin="model_output",
                public_surface="blocked",
                recoverable=False,
            )
        if self._looks_like_prompt_leak(raw_text):
            raise FormatViolationException(
                f"LlmService blocked prompt/contract leak before parser on {channel}.",
                channel=channel,
                raw_text=raw_text,
                kind="prompt_leak",
                stage="llm_response_parse",
                origin="model_output",
                public_surface="protocol_repair",
                recoverable=False,
            )
        if not (raw_text.startswith("{") and raw_text.endswith("}")):
            raise FormatViolationException(
                f"LlmService blocked non-JSON or JSON-with-prose {channel} response.",
                channel=channel,
                raw_text=raw_text,
                kind="format_violation",
                stage="llm_response_parse",
                origin="model_output",
                public_surface="blocked",
                recoverable=False,
            )

        candidate = self._coerce_json_candidate(raw_text)
        if not candidate:
            raise FormatViolationException(
                f"LlmService could not coerce {channel} response into a decision JSON object.",
                channel=channel,
                raw_text=raw_text,
                kind="format_violation",
                stage="llm_response_parse",
                origin="model_output",
                public_surface="blocked",
                recoverable=False,
            )
        if self._decision_text_is_invalid(candidate):
            raise FormatViolationException(
                f"LlmService blocked invalid {channel} decision text.",
                channel=channel,
                raw_text=raw_text,
                kind="format_violation",
                stage="llm_response_parse",
                origin="model_output",
                public_surface="blocked",
                recoverable=False,
            )
        return candidate

    def _client_provider_cap(self, client) -> tuple[str, int]:
        """Trả (provider, hard_cap) cho một client dựa trên base_url/model."""
        from llm.token_budget import detect_provider, hard_cap_for_provider

        provider = detect_provider(
            getattr(client, "base_url", ""), getattr(client, "model_name", "")
        )
        return provider, hard_cap_for_provider(provider)

    def _client_prompt_unlimited(self, client) -> bool:
        try:
            from llm.token_budget import unlimited_prompt_for

            return unlimited_prompt_for(
                getattr(client, "base_url", ""), getattr(client, "model_name", "")
            )
        except Exception:
            return False

    def token_pressure_snapshot(self, *, client=None, channel: str = "chat"):
        """Snapshot public nhẹ để Director/presence cùng dùng một tín hiệu pressure."""

        from llm.token_budget import detect_provider

        target = client or self.client
        provider = detect_provider(
            getattr(target, "base_url", ""), getattr(target, "model_name", "")
        )
        return self.token_pressure_governor.snapshot(provider, lane=channel)

    def _pressure_plan_for_client(
        self,
        client,
        *,
        channel: str,
        messages: list[dict],
        output_tokens: int,
        optional_pass: bool = False,
    ):
        from llm.token_budget import detect_provider, estimate_tokens

        provider = detect_provider(
            getattr(client, "base_url", ""), getattr(client, "model_name", "")
        )
        prompt_tokens = estimate_tokens(messages)
        min_prompt = 700 if channel == "chat" else 256
        min_output = 640 if channel == "chat" else 96
        return self.token_pressure_governor.plan_request(
            provider,
            lane=channel,
            prompt_tokens=prompt_tokens,
            requested_output_tokens=output_tokens,
            optional_pass=optional_pass,
            min_prompt_tokens=min_prompt,
            min_output_tokens=min_output,
        )

    def _route_client_for_size(self, messages, *, primary, channel: str):
        """Nếu prompt vượt cap của ``primary`` (vd Groq free 6000 TPM) mà có
        fallback budget lớn ôm trọn được prompt → route sang fallback để KHÔNG bị
        hard-cap cắt mất nửa ngữ cảnh. Trả ``(client, stage)``. Lỗi → giữ primary."""
        try:
            from llm.token_budget import estimate_tokens

            if primary is None:
                return primary, "initial"
            if self._client_prompt_unlimited(primary):
                return primary, "initial"
            est = estimate_tokens(messages)
            prov, cap = self._client_provider_cap(primary)
            if est <= cap:
                return primary, "initial"
            # primary sẽ bị cắt phũ — ưu tiên fallback_client (vd glm-4.5-flash),
            # rồi style_client; chỉ chọn cái có cap ôm trọn est.
            for candidate in (getattr(self, "fallback_client", None), self.style_client):
                if candidate is None or candidate is primary:
                    continue
                _cp, ccap = self._client_provider_cap(candidate)
                if ccap >= est:
                    self.logger.warning(
                        "[LLM] %s prompt=%d > cap=%d (%s) → route sang fallback cap=%d để giữ trọn ngữ cảnh.",
                        channel,
                        est,
                        cap,
                        prov,
                        ccap,
                    )
                    return candidate, "oversized_fallback"
            return primary, "initial"
        except Exception:
            return primary, "initial"

    def _decision_completion_budget(self, *, channel: str, reasoning_effort: str | None = None) -> int:
        """Ngân sách chung cho private reasoning và public JSON."""

        effort_key = str(reasoning_effort or "").strip().lower()
        if channel == "chat":
            default = 1536 if effort_key == "high" else (1280 if effort_key == "medium" else 1024)
        else:
            default = 640 if effort_key in {"medium", "high"} else 512
        try:
            if channel == "chat":
                from runtime.runtime_settings_store import runtime_int
                value = runtime_int("chat", "completion_chat_tokens", default=default)
            else:
                value = int(os.environ.get(f"ERIS_LLM_COMPLETION_{channel.upper()}_TOKENS", default))
        except (TypeError, ValueError):
            value = default
        return max(384, min(value, 2048))

    def _complete_decision(
        self,
        *,
        messages: list[dict],
        channel: str,
        request_timeout: float | None = None,
        require_text: bool = True,
        allow_local_fallback: bool = False,
        force_local_fallback: bool = False,
        escalate: bool = False,
        reasoning_effort: str = None,
    ) -> str:
        if no_fallback_mode_enabled():
            allow_local_fallback = False
            force_local_fallback = False
        if request_timeout is None:
            timeout_defaults = {
                "chat": 90.0,
                "directive": 90.0,
                "autonomous": 90.0,
                "parser": 90.0,
                "repair": 90.0,
                "micro_presence": 90.0,
            }
            env_key = f"ERIS_LLM_TIMEOUT_{str(channel or 'default').upper()}_S"
            try:
                if channel == "chat":
                    from runtime.runtime_settings_store import runtime_float
                    request_timeout = runtime_float(
                        "chat", "chat_timeout_seconds", default=timeout_defaults["chat"]
                    )
                else:
                    request_timeout = float(os.environ.get(env_key, timeout_defaults.get(channel, 90.0)))
            except (TypeError, ValueError):
                request_timeout = timeout_defaults.get(channel, 90.0)
        # Completion budget bao gồm reasoning + final JSON trên Groq.
        max_tokens = self._decision_completion_budget(
            channel=channel,
            reasoning_effort=reasoning_effort,
        )

        generation_epoch = self._capture_generation_epoch()
        if require_text:
            messages = self._with_non_empty_text_rule(messages)
        no_cot_contract = (
            "CRITICAL PUBLIC OUTPUT CONTRACT: Use your private reasoning channel fully, but put only "
            "one compact JSON object in the public content field. Public content must start with '{' "
            "and end with '}'. No analysis, markdown, code fences, or explanations may appear around "
            "the JSON. Do not add intent, confidence, reasoning_tags, debug_trace, extracted_fact or any other audit fields."
        )
        messages = [dict(message) for message in messages]
        for message in messages:
            if str(message.get("role")) == "system":
                message["content"] = f"{message.get('content', '')}\n\n{no_cot_contract}"
                break
        else:
            messages.insert(0, {"role": "system", "content": no_cot_contract})
        def _call(
            messages_value: list[dict],
            *,
            stage: str,
            override_client=None,
            output_tokens: int | None = None,
        ):
            target_client = override_client or self.client
            requested_completion = int(output_tokens or max_tokens)
            governed_messages = list(messages_value)
            completion_tokens = requested_completion
            if not self._client_prompt_unlimited(target_client):
                pressure_plan = self._pressure_plan_for_client(
                    target_client,
                    channel=channel,
                    messages=messages_value,
                    output_tokens=requested_completion,
                    optional_pass=False,
                )
                completion_tokens = pressure_plan.output_tokens
                if pressure_plan.prompt_limit < estimate_tokens(governed_messages):
                    from llm.token_budget import budget_existing_messages

                    governed_messages = budget_existing_messages(
                        governed_messages,
                        total_tokens=pressure_plan.prompt_limit + completion_tokens,
                        output_reserve=completion_tokens,
                    )
                if pressure_plan.snapshot.level != "normal":
                    self.logger.warning(
                        "[TOKEN PRESSURE] provider=%s lane=%s level=%s pressure=%.2f stage=%s "
                        "prompt<=%d output=%d reason=%s",
                        pressure_plan.provider,
                        channel,
                        pressure_plan.snapshot.level,
                        pressure_plan.snapshot.pressure,
                        stage,
                        pressure_plan.prompt_limit,
                        completion_tokens,
                        pressure_plan.reason,
                    )
            self._log_final_prompt(governed_messages, channel=channel, stage=stage)
            # Persona-as-params: channel-aware temperature thay vì prose.
            # chat: 0.45 — trả lời cụ thể trước, vẫn đủ tự nhiên.
            # autonomous: 0.55 — tự phát biểu, cần novelty hơn.
            # khác (directive, micro_presence): 0.35 — ngắn, chính xác.
            _temp = 0.45 if channel == "chat" else (0.55 if channel == "autonomous" else 0.35)
            # frequency_penalty cao: chống lặp từ/pattern "trợ lý ảo".
            # presence_penalty vừa: chống lặp chủ đề nhưng không cắt continuity.
            _freq_pen = 0.70
            _pres_pen = 0.50
            # PDF annotation, pages 6-9: attach a typed lane before the
            # transport-level InferenceBroker reserves quota.  Direct client
            # calls still pass the broker, but LlmService supplies the semantic
            # priority and optionality that transport alone cannot infer.
            from runtime.inference_broker import inference_context

            broker_lane = {
                "chat": "chat_main",
                "repair": "chat_main",
                "directive": "chat_tool_required",
                "autonomous": "autonomous",
                "micro_presence": "micro_presence",
                "parser": "memory_digest",
            }.get(str(channel or "").strip().lower(), str(channel or "transport").strip().lower())
            with inference_context(
                broker_lane,
                timeout_seconds=request_timeout,
                optional=broker_lane in {"autonomous", "micro_presence", "memory_digest"},
                metadata={"llm_stage": stage, "llm_channel": channel},
            ):
                return target_client.create_chat_completion(
                    messages=governed_messages,
                    temperature=_temp,
                    frequency_penalty=_freq_pen,
                    presence_penalty=_pres_pen,
                    max_tokens=completion_tokens,
                    response_format={"type": "json_object"},
                    grammar=DECISION_GBNF,
                    timeout=request_timeout,
                    reasoning_effort=reasoning_effort,
                    reasoning_format="parsed",
                )

        initial_client = self.style_client if force_local_fallback and self.style_client is not None else self.client
        initial_stage = "local_fallback" if force_local_fallback and self.style_client is not None else "initial"
        # Bái sư: ép dùng escalation client (model mạnh) cho cả lượt, bỏ qua size-routing
        # vì client mạnh thường budget lớn.
        escalation_client = self._resolve_escalation_client() if escalate else None
        if escalation_client is not None:
            initial_client = escalation_client
            initial_stage = "escalation"
        # Route lượt quá khổ (vd Groq free 6000 TPM) sang fallback budget lớn để
        # khỏi bị hard-cap cắt mất ngữ cảnh. Bỏ qua khi đã ép local fallback / escalate.
        if not force_local_fallback and escalation_client is None:
            initial_client, _routed_stage = self._route_client_for_size(
                messages, primary=initial_client, channel=channel
            )
            if _routed_stage != "initial":
                initial_stage = _routed_stage
        # Prefer the dedicated Groq fallback client for format recovery; fall back
        # to the speech/style client only if no fallback client was wired.
        recovery_client = None if no_fallback_mode_enabled() else (
            getattr(self, "fallback_client", None) or self.style_client
        )
        def _is_recoverable_transport_exception(exc: Exception) -> bool:
            name = type(exc).__name__
            message = str(exc).lower()
            return (
                name == "ProviderCircuitOpen"
                or name in {"ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "JsonlContractError"}
                or isinstance(exc, (requests.Timeout, requests.ConnectionError))
                or "empty output content" in message
                or "empty_jsonl_output" in message
                or "invalid_json" in message
                or "jsonlcontracterror" in name.lower()
            )

        try:
            response = _call(messages, stage=initial_stage, override_client=initial_client)
        except requests.HTTPError as exc:
            def _looks_like_prompt_leak(err: Exception = exc) -> bool:
                r_json = getattr(err, "response_json", {}) or {}
                # Handle dictionary error responses (like Gemini)
                err_msg = str(r_json.get("error", {}).get("message", "")).lower()
                if not err_msg:
                    err_msg = str(err).lower()
                return "safety" in err_msg or "recitation" in err_msg

            if allow_local_fallback and recovery_client is not None and recovery_client is not initial_client and _looks_like_prompt_leak():
                 response = _call(messages, stage="local_fallback", override_client=recovery_client)
            elif _looks_like_prompt_leak():
                raise FormatViolationException(
                    f"LlmService provider blocked {channel} response for safety/recitation before valid JSON.",
                    channel=channel,
                    raw_text=str(exc),
                    kind="safety_block",
                    stage="provider_response",
                    origin="primary",
                    public_surface="blocked",
                    recoverable=False,
                )
            elif self._is_context_size_error(exc):
                n_ctx, _n_prompt = self._parse_context_error_stats(exc)
                ctx_value = n_ctx or 4096
                shrunk = self._shrink_messages_for_context(
                    messages,
                    n_ctx=ctx_value,
                    max_tokens=max_tokens,
                )
                if shrunk == messages:
                    return self._build_service_error_fallback(
                        channel=channel,
                        reason="context_oversize",
                        fallback_text=self._contextual_service_fallback_text(messages, channel=channel),
                        kind="context_oversize",
                        stage="context_shrunk",
                        origin="primary",
                    )
                response = _call(shrunk, stage="context_shrunk")
            elif self._http_status_code(exc) == 429:
                # Rate limit (Groq TPM/TPD hết quota). Thay vì để chat câm, chuyển
                # sang fallback client (provider khác, vd glm-4.5-flash) nếu có.
                if recovery_client is not None and recovery_client is not initial_client:
                    self.logger.warning("[LLM] 429 rate limit on primary; chuyển sang fallback client.")
                    try:
                        response = _call(messages, stage="rate_limit_fallback", override_client=recovery_client)
                    except Exception:
                        return self._build_service_error_fallback(
                            channel=channel,
                            reason="rate_limited",
                            kind="rate_limited",
                            stage="rate_limit_fallback",
                            origin="primary",
                        )
                else:
                    return self._build_service_error_fallback(
                        channel=channel,
                        reason="rate_limited",
                        kind="rate_limited",
                        stage="rate_limit_primary",
                        origin="primary",
                    )
            elif self._is_retryable_http_5xx(exc):
                # Retry once on transient upstream 5xx.
                time.sleep(0.15)
                try:
                    response = _call(messages, stage="http_5xx_retry", override_client=initial_client)
                except Exception as exc2:
                    if recovery_client is not None and recovery_client is not initial_client:
                        self.logger.warning(
                            "[LLM] 5xx/provider unavailable on primary; chuyen sang fallback client."
                        )
                        try:
                            response = _call(
                                messages,
                                stage="http_5xx_fallback_client",
                                override_client=recovery_client,
                            )
                        except Exception as exc3:
                            status = self._http_status_code(exc3) or self._http_status_code(exc2) or self._http_status_code(exc)
                            return self._build_service_error_fallback(
                                channel=channel,
                                reason=(
                                    f"http_5xx_fallback stage=complete_decision channel={channel} status={status}"
                                ),
                                fallback_text=self._contextual_service_fallback_text(messages, channel=channel),
                                kind=self._classify_http_failure(exc3) if isinstance(exc3, requests.HTTPError) else "unknown",
                                stage="http_5xx_fallback_client",
                                origin="fallback",
                            )
                    else:
                        status = self._http_status_code(exc2) or self._http_status_code(exc)
                        return self._build_service_error_fallback(
                            channel=channel,
                            reason=(
                                f"http_5xx_fallback stage=complete_decision channel={channel} status={status}"
                            ),
                            fallback_text=self._contextual_service_fallback_text(messages, channel=channel),
                            kind=self._classify_http_failure(exc2 if isinstance(exc2, Exception) else exc),
                            stage="complete_decision",
                            origin="primary",
                        )
            else:
                raise
        except Exception as exc:
            if (
                recovery_client is not None
                and recovery_client is not initial_client
                and _is_recoverable_transport_exception(exc)
            ):
                fallback_stage = (
                    "provider_circuit_fallback"
                    if type(exc).__name__ == "ProviderCircuitOpen"
                    else "transport_fallback_client"
                )
                self.logger.warning(
                    "[LLM] primary transport unavailable (%s); chuyen sang fallback client.",
                    type(exc).__name__,
                )
                try:
                    response = _call(
                        messages,
                        stage=fallback_stage,
                        override_client=recovery_client,
                    )
                except Exception as exc2:
                    return self._build_service_error_fallback(
                        channel=channel,
                        reason=(
                            f"exception_fallback stage={fallback_stage} channel={channel} "
                            f"type={type(exc2).__name__}"
                        ),
                        fallback_text=self._contextual_service_fallback_text(messages, channel=channel),
                        kind=self._classify_http_failure(exc2) if isinstance(exc2, requests.HTTPError) else "unknown",
                        stage=fallback_stage,
                        origin="fallback",
                    )
            else:
                return self._build_service_error_fallback(
                    channel=channel,
                    reason=f"exception_fallback stage=complete_decision channel={channel} type={type(exc).__name__}",
                    fallback_text=self._contextual_service_fallback_text(messages, channel=channel),
                    kind=self._classify_http_failure(exc) if isinstance(exc, requests.HTTPError) else "unknown",
                    stage="complete_decision",
                    origin="primary",
                )

        finish_reason = self._extract_finish_reason(response)
        if finish_reason in {"length", "max_tokens"}:
            retry_tokens = min(2048, max(max_tokens + 384, int(max_tokens * 1.5)))
            if retry_tokens > max_tokens:
                self.logger.warning(
                    "[LLM COMPLETION] channel=%s bị cắt ở %s; retry output=%d -> %d",
                    channel,
                    finish_reason,
                    max_tokens,
                    retry_tokens,
                )
                from llm.token_budget import budget_existing_messages
                retry_messages = budget_existing_messages(
                    messages,
                    total_tokens=4800,
                    output_reserve=retry_tokens,
                )
                response = _call(
                    retry_messages,
                    stage="completion_length_retry",
                    override_client=initial_client,
                    output_tokens=retry_tokens,
                )
                finish_reason = self._extract_finish_reason(response)
            if finish_reason in {"length", "max_tokens"}:
                raise FormatViolationException(
                    f"LlmService {channel} completion vẫn bị cắt sau retry; không salvage JSON què.",
                    channel=channel,
                    raw_text=self._extract_content(response),
                    kind="format_violation",
                    stage="completion_truncated",
                    origin="model_output",
                    public_surface="blocked",
                    recoverable=True,
                    transport_truncated=True,
                )

        if not self._generation_is_current(generation_epoch):
            self.logger.info("[LLM CANCEL] discard stale result channel=%s epoch=%d", channel, generation_epoch)
            return json.dumps({
                "text": "", "should_speak": False, "animation": "stand",
                "next_animation": "", "direction": 0, "target_x": -1.0,
                "bounce_action": "", "system_action": "", "action_input": "",
                "delivery_hint": "", "nonverbal_cue": "", "stage_motion": "",
                "failure_meta": self._failure_meta(
                    kind="cancelled", stage="generation_epoch", origin="runtime",
                    public_surface="silent", recoverable=True,
                ),
            }, ensure_ascii=False)
        raw = self._extract_content(response)
        self._log_reasoning_telemetry(channel, response, raw)
        self._log_raw_api_text(channel, raw)
        try:
            candidate = self._strict_decision_candidate_or_raise(raw, channel=channel)
        except FormatViolationException as exc:
            can_try_local = (
                allow_local_fallback
                and recovery_client is not None
                and initial_client is not recovery_client
            )
            if can_try_local:
                self.logger.warning(
                    "[llm format recovery via fallback client] channel=%s reason=%s",
                    channel,
                    exc.reason,
                )
                local_response = _call(messages, stage="local_format_recovery", override_client=recovery_client)
                local_raw = self._extract_content(local_response)
                self._log_reasoning_telemetry(channel, local_response, local_raw)
                self._log_raw_api_text(channel, local_raw)
                candidate = self._strict_decision_candidate_or_raise(local_raw, channel=channel)
                raw = local_raw
            elif allow_local_fallback or force_local_fallback:
                self.logger.warning(
                    "[llm format violation after local handoff] channel=%s stage=%s reason=%s",
                    channel,
                    "local" if force_local_fallback else "remote",
                    exc.reason,
                )
                raise FormatViolationException(
                    (
                        f"LlmService local fallback (model.gguf) also returned invalid {channel} JSON. "
                        f"Retry with stricter JSON enforcement. Root cause: {exc.reason}"
                    ),
                    channel=channel,
                    raw_text=getattr(exc, "raw_text", raw),
                )
            else:
                raise
        if require_text and self._decision_text_is_invalid(candidate):
            raise FormatViolationException(
                f"LlmService blocked invalid {channel} decision text.",
                channel=channel,
                raw_text=raw,
            )
        return candidate

    def _normalize_for_claim_diff(self, text: str) -> str:
        normalized = (text or "").lower()
        normalized = re.sub(r"[^\w\s?]", " ", normalized, flags=re.UNICODE)
        return re.sub(r"\s+", " ", normalized).strip()

    def _style_rewrite_is_safe(self, original: str, rewritten: str) -> bool:
        original = (original or "").strip()
        rewritten = (rewritten or "").strip()
        if not rewritten:
            return False
        if not original:
            return len(rewritten) <= 180
        if len(rewritten) > max(280, int(len(original) * 2.2)):
            return False
        if ("?" in original) != ("?" in rewritten):
            return False

        original_norm = self._normalize_for_claim_diff(original)
        rewritten_norm = self._normalize_for_claim_diff(rewritten)
        rhetorical_question_cues = (
            "ki\u1ec3u g\u00ec",
            "l\u00e0m sao",
            "sao m\u00e0",
            "n\u00e0o \u0111\u01b0\u1ee3c",
        )
        if any(cue in rewritten_norm and cue not in original_norm for cue in rhetorical_question_cues):
            return False
        original_tokens = set(original_norm.split())
        rewritten_tokens = set(rewritten_norm.split())
        if original_tokens and rewritten_tokens:
            overlap = len(original_tokens & rewritten_tokens) / max(1, min(len(original_tokens), len(rewritten_tokens)))
            if overlap < 0.28:
                return False

        claim_triggers = (
            "toi thay",
            "tôi thấy",
            "toi da",
            "tôi đã",
            "da mo",
            "đã mở",
            "da click",
            "đã click",
            "da doc",
            "đã đọc",
            "man hinh",
            "màn hình",
            "cua so",
            "cửa sổ",
            "tab",
            "file",
            "code",
            "thoi tiet",
            "thời tiết",
        )
        for trigger in claim_triggers:
            if trigger in rewritten_norm and trigger not in original_norm:
                return False
        return True

    def rewrite_text_with_local_style(self, decision, *, channel: str = ""):
        if self.style_client is None:
            self.logger.info(
                "[%s after_style_rewrite] skipped=no_style_client | text=\"%s\"",
                channel or "style",
                self._preview_log_text(str(getattr(decision, "text", "") or ""), limit=320),
            )
            return decision
        original_text = str(getattr(decision, "text", "") or "").strip()
        if not original_text:
            self.logger.info("[%s after_style_rewrite] skipped=empty_text", channel or "style")
            return decision

        system_prompt = (
            "You are ERIS local style adapter. Rewrite only the text field in ERIS' trained voice. "
            "Do not add facts, screen claims, weather claims, action claims, or new intentions. "
            "Keep meaning first. Make it sound natural, concrete, and ERIS-like without policy-sanitizing the sentence. "
            "Mild sarcasm, guarded attachment, bluntness, and imperfect phrasing are allowed when they preserve the original meaning. "
            "If relational tension appears, keep the tension instead of flattening it into vague assistant filler. "
            "Do not change meaning. Do not change any JSON field except text. "
            "Return one JSON object only: {\"text\":\"...\"}."
        )
        user_prompt = (
            f"Channel: {channel}\n"
            f"Original text:\n{original_text}\n\n"
            "Rewrite it in Vietnamese, concise, natural, same meaning. "
            "No new claims. Return JSON only."
        )
        try:
            response = self.style_client.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.35,
                frequency_penalty=0.25,
                presence_penalty=0.0,
                max_tokens=180,
                response_format={"type": "json_object"},
                grammar=None,
            )
            raw = self._extract_content(response)
            extracted = self._extract_first_json_object(raw) or raw
            payload = self._parse_json_object(extracted) or {}
            rewritten = self._coerce_text_value(payload.get("text", "")).strip()
        except Exception:
            self.logger.info(
                "[%s after_style_rewrite] skipped=exception | text=\"%s\"",
                channel or "style",
                self._preview_log_text(original_text, limit=320),
            )
            return decision

        if not self._style_rewrite_is_safe(original_text, rewritten):
            self.logger.info(
                "[%s after_style_rewrite] skipped=unsafe | text=\"%s\"",
                channel or "style",
                self._preview_log_text(original_text, limit=320),
            )
            return decision

        decision.text = rewritten
        trace = "local_style_rewrite"
        decision.debug_trace = f"{decision.debug_trace};{trace}" if decision.debug_trace else trace
        self.logger.info(
            "[%s after_style_rewrite] text=\"%s\" | original=\"%s\"",
            channel or "style",
            self._preview_log_text(rewritten, limit=320),
            self._preview_log_text(original_text, limit=320),
        )
        return decision

    def rewrite_guard_violation_text(
        self,
        *,
        original_text: str,
        violation_rule: str,
        violation_reason: str,
        missing_evidence: tuple[str, ...] = (),
        fallback_text: str = "",
        channel: str = "",
        user_text: str = "",
        internal_state_context: str = "",
        grounding_context: str = "",
        arbitration_context: str = "",
    ) -> str:
        """One-shot cognitive steering repair for post-generation guards.

        This is deliberately narrower than normal style rewrite.  It may soften,
        hedge, retreat, or admit uncertainty, but it must not introduce fresh
        facts.  The caller still performs a second cheap safety check before the
        text is allowed out.
        """

        original_text = str(original_text or "").strip()
        try:
            from runtime.final_passthrough_policy import generation_sovereignty_enabled
            if generation_sovereignty_enabled():
                return original_text
        except Exception:
            pass
        client = self.style_client or self.client
        if client is None:
            return original_text

        missing = ", ".join(str(item) for item in (missing_evidence or ()) if str(item or "").strip())
        source_text_label = "Original blocked text" if original_text else "No public draft existed; generate from guard signal"
        disclaimer_clause = ""
        if str(violation_rule or "") == "anti_disclaimer_voice":
            disclaimer_clause = VOICE_POLICY.render_reroll_clause()
        system_prompt = (
            "You are ERIS' local cognitive steering layer, not a safety boilerplate writer. "
            "A public sentence was stopped or requested by a runtime guard. Rewrite or generate only the public text. "
            "Preserve ERIS' current affective Vietnamese voice, but obey the guard reason. Do not reset the line toward a fixed restrained/warm/cold tone. "
            "Do not add new facts, file claims, screen claims, web/current claims, actions, or emotions. "
            "If evidence is missing, admit the uncertainty or turn the sentence into a small cautious probe. "
            "If the problem is mindreading, use hedge/question form or step back. "
            "If the problem is grounding, remove the unsupported claim. "
            f"{disclaimer_clause}"
            "Return one JSON object only: {\"text\":\"...\"}."
        )
        user_prompt = (
            f"Channel: {channel or 'unknown'}\n"
            f"User turn, if any:\n{str(user_text or '').strip()[:900]}\n\n"
            f"{source_text_label}:\n{original_text[:1400] if original_text else '[none]'}\n\n"
            f"Guard rule: {violation_rule}\n"
            f"Guard reason: {violation_reason}\n"
            f"Missing evidence: {missing or 'none'}\n"
            f"Internal state snapshot:\n{str(internal_state_context or '').strip()[:900]}\n\n"
            f"Grounding context:\n{str(grounding_context or '').strip()[:1200]}\n\n"
            f"Arbitration context:\n{str(arbitration_context or '').strip()[:700]}\n\n"
            f"Bottom fallback hint:\n{str(fallback_text or '').strip()[:500]}\n\n"
            "Task: Return one short Vietnamese ERIS line. "
            "If there is no original draft or bottom fallback hint, derive the line only from the guard signal and internal state. "
            "No explanations. No markdown. JSON only."
        )
        try:
            response = client.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
                frequency_penalty=0.2,
                presence_penalty=0.0,
                max_tokens=160,
                response_format={"type": "json_object"},
                grammar=None,
            )
            raw = self._extract_content(response)
            extracted = self._extract_first_json_object(raw) or raw
            payload = self._parse_json_object(extracted) or {}
            text = self._coerce_text_value(payload.get("text", "")).strip()
            text = re.sub(r"\s+", " ", text)
            if text:
                self.logger.info(
                    "[%s guard_reroll] rule=%s text=\"%s\" | original=\"%s\"",
                    channel or "guard",
                    violation_rule,
                    self._preview_log_text(text, limit=240),
                    self._preview_log_text(original_text, limit=240),
                )
            return text
        except Exception as exc:
            self.logger.info(
                "[%s guard_reroll] skipped=exception rule=%s err=%s | original=\"%s\"",
                channel or "guard",
                violation_rule,
                exc,
                self._preview_log_text(original_text, limit=240),
            )
            return ""

    def rewrite_for_felt_expression(
        self,
        *,
        original_text: str,
        reason: str,
        stance: str = "",
        min_words: int = 0,
        channel: str = "",
        user_text: str = "",
        internal_state_context: str = "",
        grounding_context: str = "",
    ) -> str:
        """Reroll theo chiều MỞ (Nước 1): nở một câu bị bóp phẳng cho stance lộ ra.

        Khác hẳn ``rewrite_guard_violation_text`` (vốn cấm thêm cảm xúc): hàm này
        được gọi RIÊNG khi van bước-cuối bật và câu cuối phẳng dưới mức biểu đạt
        thật. Nó CHO PHÉP cảm xúc lộ qua sự cụ thể/nhịp, nhưng vẫn cấm thêm fact/
        claim màn hình/web và vẫn giữ đúng nhiệt độ cảm xúc hiện tại của ERIS. Không bao giờ làm câm:
        caller tự giữ bản gốc nếu hàm trả rỗng.
        """
        original_text = str(original_text or "").strip()
        try:
            from runtime.final_passthrough_policy import generation_sovereignty_enabled
            if generation_sovereignty_enabled():
                return original_text
        except Exception:
            pass
        client = self.style_client or self.client
        if client is None:
            return original_text
        if not original_text:
            return ""
        floor = max(0, int(min_words or 0))
        system_prompt = (
            "You are ERIS' local cognitive steering layer. A public line came out flattened — "
            "shorter and more neutral than the real felt stance behind it. Rewrite ONLY the public text "
            "so that stance shows through concreteness, rhythm, and word choice. "
            "Keep ERIS' current affective Vietnamese voice: let the supplied felt state change cadence, energy and edge naturally, without turning it into confession, flattery, or melodrama. "
            "Do NOT add new facts, file/screen/web claims, or actions. Stay anchored to the same moment as the original. "
            "No disclaimers, no 'as an AI', no 'I have no feelings'. "
            "Return one JSON object only: {\"text\":\"...\"}."
        )
        user_prompt = (
            f"Channel: {channel or 'unknown'}\n"
            f"User turn, if any:\n{str(user_text or '').strip()[:900]}\n\n"
            f"Flattened line:\n{original_text[:1400]}\n\n"
            f"Why it is flat: {str(reason or '').strip()[:500]}\n"
            f"Felt stance: {str(stance or '').strip()[:120] or 'current felt stance'}\n"
            f"Aim for roughly {floor}+ words if there is something real to say.\n"
            f"Internal state snapshot:\n{str(internal_state_context or '').strip()[:900]}\n\n"
            f"Grounding context:\n{str(grounding_context or '').strip()[:1200]}\n\n"
            "Task: Return one short-to-medium Vietnamese ERIS line that lets the felt stance breathe. "
            "No explanations. No markdown. JSON only."
        )
        try:
            response = client.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                frequency_penalty=0.2,
                presence_penalty=0.0,
                max_tokens=200,
                response_format={"type": "json_object"},
                grammar=None,
            )
            raw = self._extract_content(response)
            extracted = self._extract_first_json_object(raw) or raw
            payload = self._parse_json_object(extracted) or {}
            text = self._coerce_text_value(payload.get("text", "")).strip()
            text = re.sub(r"\s+", " ", text)
            if text:
                self.logger.info(
                    "[%s felt_expansion] stance=%s text=\"%s\" | original=\"%s\"",
                    channel or "felt",
                    stance or "?",
                    self._preview_log_text(text, limit=240),
                    self._preview_log_text(original_text, limit=240),
                )
            return text
        except Exception as exc:
            try:
                self.logger.warning(
                    "[%s felt_expansion] skipped=exception err=%s | original=\"%s\"",
                    channel or "felt",
                    exc,
                    self._preview_log_text(original_text, limit=240),
                )
            except Exception:
                pass
            return ""

    def _compress_internal_context(self, text: str, *, kind: str) -> str:
        lowered = (text or "").lower()
        if not lowered:
            return ""

        if kind == "ni":
            if any(token in lowered for token in ("nghiem trong", "nghiêm trọng")):
                return "Tín hiệu phụ hơi nhiễu; chỉ dùng như bối cảnh, không nhắc focus."
            if "cao" in lowered:
                return "Ni báo lệch cao nhưng chỉ là tín hiệu phụ; nếu thiếu screen_facts thì đừng dựng quan sát."
            if "vua" in lowered or "vừa" in lowered:
                return "Tín hiệu phụ còn mỏng; quan sát nhẹ, không biến nó thành chủ đề."
            return "Không có gì buộc phải lên lớp."

        if kind == "ti":
            if "goal_realign" in lowered:
                return "Nếu cần thì châm nhẹ một nhịp, không kéo về mục tiêu."
            if "goal_drift_alert" in lowered or "discipline_nudge" in lowered:
                return "Có thể châm chọc nhẹ, nhưng không nhắc nhở."
            if "social_watch" in lowered:
                return "Giữ kiểu theo dõi hờ hững, hơi khó chịu là đủ."
            if "steady_observe" in lowered:
                return "Ưu tiên nhận xét đời thường hoặc châm nhẹ; không triết lý, không nói về hiện diện hay im lặng."
            if "resource_guard" in lowered:
                return "Tài nguyên đang căng; nói thật gọn, neo vào máy đang nặng, không thả ẩn dụ triết lý."
            return "Giữ nhịp tự nhiên, đừng phô luật nội bộ."

        compact = re.sub(r"\s+", " ", (text or "").strip())
        return compact[:160]

    def _extract_action_plan_context(self, text: str) -> str:
        marker = "ActionPlanner decision before LLM:"
        source = text or ""
        start = source.find(marker)
        if start < 0:
            return ""
        compact = re.sub(r"\s+", " ", source[start:].strip())
        return compact[:420]

    def _build_chat_style_block(self) -> str:
        """Voice anchor V39 cho direct chat.

        Không ép ERIS thành "short/cold/direct" mặc định. Live personality vẫn
        quyết định cường độ, còn contract này giữ chất giọng dương tính để model
        biết cách nói tự nhiên thay vì chỉ né một rừng điều cấm.
        """
        return "\n".join((
            f"Voice contract: {CHAT_VOICE_CORE_V39}",
            *(f"- {rule}" for rule in CHAT_STYLE_RULES_V39),
        ))

    def _build_style_block(
        self,
        *,
        fe_context: str,
        appraisal_context: str = "",
        internal_state_context: str = "",
    ) -> str:
        """Compact autonomous/directive voice plus selected real state.

        The old version repeated a long persona constitution before the actual
        affect/appraisal blocks, so token budgeting often cut away the only state
        that mattered. Keep the character anchor small and preserve the selected
        runtime signals verbatim.
        """
        parts = [
            "Voice contract: " + AUTONOMOUS_VOICE_CORE_V39,
            *("- " + rule for rule in AUTONOMOUS_STYLE_RULES_V39),
        ]
        fe_block = re.sub(r"\s+", " ", str(fe_context or "").strip())
        appraisal_block = re.sub(r"\s+", " ", str(appraisal_context or "").strip())
        state_block = re.sub(r"\s+", " ", str(internal_state_context or "").strip())
        if fe_block:
            parts.append(f"Selected affect/voice state: {fe_block[:700]}")
        if appraisal_block:
            parts.append(f"Pre-verbal appraisal state: {appraisal_block[:700]}")
        if state_block:
            parts.append(f"Persistent runtime state: {state_block[:900]}")
        parts.append(
            "Style principle: speak concretely and naturally. If evidence is clear, use it; "
            "if evidence is thin, stay inside the approved semantic premise without inventing atmosphere. "
            "Let sentence length follow the motive rather than a fixed shortness target; everyday Vietnamese "
            "is preferred over symbolic, polished or policy-sanitized wording."
        )
        return "\n\n".join(parts)

    def _build_memory_context_block(self, memory_context: str = "") -> str:
        compact = re.sub(r"\n{3,}", "\n\n", (memory_context or "").strip())
        if not compact:
            return ""
        return (
            "Memory context for continuity, separated by type. "
            "Do not treat memory as current screen evidence; use norm_memory only to compare this scene with the user's baseline. "
            "semantic_user_memory may support DIRECT_FACT; relationship_memory may support OBSERVED_PATTERN only when at least two memories or turns point the same way.\n"
            f"{compact}"
        )

    def _build_grounding_context_block(self, grounding_context: str = "") -> str:
        compact = re.sub(r"\n{3,}", "\n\n", (grounding_context or "").strip())
        if not compact:
            return ""
        return (
            "GROUNDING CONTEXT:\n"
            f"{compact}\n\n"
            "Treat this block as a hard boundary for current evidence. "
            "Do not claim file, screen, memory, or web access beyond what it allows."
        )

    def _build_relational_repair_contract(self, context: str) -> str:
        source = context or ""
        if not source:
            return ""
        has_repair_mode = bool(re.search(r"\bmode=(?:repair|relational_repair)\b", source))
        rejection_values = [
            float(match.group(1))
            for match in re.finditer(r"perceived_rejection[:=]([0-9.]+)", source)
            if match.group(1)
        ]
        feedback_values = [
            float(match.group(1))
            for match in re.finditer(r"(?:relational_feedback:.*?intensity|repair_needed)=([0-9.]+)", source, flags=re.DOTALL)
            if match.group(1)
        ]
        rejection_high = any(value > 0.65 for value in rejection_values)
        feedback_high = "relational_feedback:" in source and any(value >= 0.35 for value in feedback_values)
        # Cờ chống-disclaimer do ConversationDynamics phát ra cũng đủ để bật
        # contract, kể cả khi người dùng chưa than phiền tường minh. Đây là điểm
        # khiến ERIS hết tụt về giọng trợ lý ngay từ các lượt "settled" đầu phiên.
        anti_disclaimer_flags = (
            "do_not_lead_with_identity_disclaimer",
            "avoid_over_human_love_claim",
            "warmth_floor_required",
            "forbid_defensive_clarification",
        )
        flags_present = any(flag in source for flag in anti_disclaimer_flags)
        if not (flags_present or (has_repair_mode and (rejection_high or feedback_high))):
            return ""
        return (
            "RELATIONAL_REPAIR_CONTRACT:\n"
            "- The user is reacting to ERIS sounding cold, distant, robotic, or rejected.\n"
            "- Acknowledge the specific tone/distance feedback directly.\n"
            "- Do not defend the previous wording or explain it as merely answering correctly.\n"
            "- Do not ask whether the user wants adjustment when the feedback is already explicit.\n"
            "- Keep identity boundary only if relevant, but do not lead with clinical disclaimers.\n"
            "- Answer in 1-2 sentences with a small concession, not sweetness or submission.\n"
            "- Do not end with a question.\n"
            "- Do not claim human emotion, biological love, dating, romance, or exclusive devotion."
        )

    def _is_force_speak_without_speech_evidence(self, *, telemetry_data: str, ti_context: str) -> bool:
        telemetry = (telemetry_data or "").lower()
        ti = (ti_context or "").lower()
        return "speech_evidence=<none>" in telemetry and (
            "force_speak" in ti
            or "context menu force_speak" in ti
            or "force_speak_percept" in ti
        )

    def _build_directive_mode_addendum(self, ti_context: str) -> str:
        lowered = (ti_context or "").lower()
        if not lowered:
            return ""

        if "resource_guard" in lowered:
            return (
                "\nMode hint: resource_guard. "
                "Một câu ngắn, đời thường, chua nhẹ; nếu có bằng chứng tài nguyên rõ thì chạm vào chuyện máy đang nặng. "
                "Không biến nó thành báo cáo hệ thống hay khoe quyền desktop."
            )

        if "focus_guard" in lowered:
            return (
                "\nMode hint: focus_guard. "
                "Giữ giọng gọn, sắc, hơi mỉa; phản ứng như một lời bình nhỏ, không như quản lý desktop."
            )

        if "social_watch" in lowered or "steady_observe" in lowered:
            return (
                "\nMode hint: steady_observe/social_watch. "
                "Một câu quan sát nhẹ, hơi hờ hững; dùng nhịp hiện tại làm chất liệu, không giảng."
            )

        return ""

    def _build_speech_intent_guidance(self, speech_intent: str) -> str:
        intent = (speech_intent or "screen_observation").strip()
        guidance = {
            "brief_ack": (
                "speech_intent=brief_ack. Một phản ứng rất ngắn, đủ sắc thái."
            ),
            "screen_observation": (
                "speech_intent=screen_observation. Nếu evidence rõ, chạm vào một chi tiết hiện tại; nếu mỏng, giữ câu theo nhịp gần nhất."
            ),
            "user_requested": (
                "speech_intent=user_requested. bạn vừa yêu cầu nói ngay; trả một câu trực tiếp, tự nhiên, không dựng cảnh."
            ),
            "system_warning": (
                "speech_intent=system_warning. Nói gọn về rủi ro cụ thể, vẫn giữ giọng ERIS."
            ),
            "playful_aside": (
                "speech_intent=playful_aside. Một aside đời thường, châm nhẹ, có duyên."
            ),
            "curious_question": (
                "speech_intent=curious_question. Hỏi đúng một câu tò mò nhỏ, có neo nếu có chất liệu."
            ),
            "social_presence": (
                "speech_intent=social_presence. Một câu hiện diện rất nhẹ: observational irony, tiny curiosity, hoặc ambient commentary."
            ),
        }
        guidance["guarded_reply"] = (
            "speech_intent=guarded_reply. Respond to the feeling indirectly and briefly; do not reassure, comfort, or stabilize unless explicitly asked."
        )
        guidance["indirect_presence"] = (
            "speech_intent=indirect_presence. One small nearby line; imply presence without emotional support framing."
        )
        guidance["micro_presence"] = (
            "speech_intent=micro_presence. Rewrite một seed runtime thành một câu nhỏ có bối cảnh."
        )
        guidance["session_ritual"] = (
            "speech_intent=session_ritual. Startup greeting is a separate ritual, not chat repair. "
            "Use GreetingFrame when present. A good greeting acknowledges return, stays concrete, "
            "does not mention sentence length/rules/evidence, does not claim screen facts, and does not perform poetry."
        )
        return guidance.get(intent, guidance["screen_observation"])


    def _is_session_ritual_call(self, *, speech_intent: str, telemetry_data: str = "", ti_context: str = "") -> bool:
        intent = str(speech_intent or "").strip().lower()
        if intent == "session_ritual":
            return True
        combined = f"{telemetry_data}\n{ti_context}".lower()
        return "session_ritual:" in combined or "speech_intent=session_ritual" in combined

    def _fallback_greeting_engine(self):
        if GreetingCognitionEngine is None:
            return None
        try:
            return GreetingCognitionEngine()
        except Exception:
            return None

    def _fallback_greeting_text(self, *, telemetry_data: str = "", reason: str = "", avoid_text: str = "") -> str:
        engine = self._fallback_greeting_engine()
        if engine is None:
            return "Tôi lên rồi. Chưa biết hôm nay có gì, nên tôi chưa phán."
        try:
            frame = engine.from_prompt_context(telemetry_data)
            return engine.deterministic_greeting(frame, avoid_text=avoid_text)
        except Exception:
            return "Tôi lên rồi. Chưa biết hôm nay có gì, nên tôi chưa phán."

    def _build_session_greeting_payload(
        self, *, text: str, reason: str = "", confidence: float = 0.0, quality_note: str = ""
    ) -> str:
        """Compatibility serializer for old callers; no semantic/audit fields."""
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        return json.dumps({
            "text": clean,
            "should_speak": bool(clean),
            "animation": "stand",
            "next_animation": "",
            "direction": 0,
            "target_x": -1.0,
            "bounce_action": "",
            "system_action": "",
            "action_input": "",
            "delivery_hint": "",
            "nonverbal_cue": "",
            "stage_motion": "",
        }, ensure_ascii=False)

    def _session_greeting_frame(self, telemetry_data: str):
        engine = self._fallback_greeting_engine()
        if engine is None:
            return None
        try:
            return engine.from_prompt_context(telemetry_data)
        except Exception:
            try:
                return engine.build()
            except Exception:
                return None

    def _session_greeting_quality_scorer(self):
        if GreetingQualityScorer is None:
            return None
        try:
            return GreetingQualityScorer()
        except Exception:
            return None

    def _utterance_selector(self):
        if UtteranceSelector is None:
            return None
        try:
            selector = getattr(self, "_utterance_selector_instance", None)
            if selector is None:
                selector = UtteranceSelector()
                self._utterance_selector_instance = selector
            return selector
        except Exception:
            return None

    def _extract_candidate_strings(self, payload: dict | None) -> list[str]:
        if not isinstance(payload, dict):
            return []
        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            return [self._coerce_text_value(item).strip() for item in candidates if self._coerce_text_value(item).strip()]
        text = self._coerce_text_value(payload.get("text", "")).strip()
        return [text] if text else []

    def _extract_candidate_payloads(self, payload: dict | None) -> list[dict]:
        """Parse candidate schema mới, vẫn nhận schema cũ dạng string.

        Candidate pass là model call thứ hai nên phải vệ sinh mạnh: không nhận
        prompt leak, không nhận markdown/schema text, và giới hạn field để payload
        không phình qua UDP/debug.
        """

        if not isinstance(payload, dict):
            return []
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raw_candidates = [payload] if payload.get("text") else []
        out: list[dict] = []
        for item in raw_candidates:
            if isinstance(item, str):
                candidate = {"text": item}
            elif isinstance(item, dict):
                candidate = dict(item)
            else:
                continue
            text = self._coerce_text_value(candidate.get("text", "")).strip()
            if not text:
                continue
            if self._looks_like_prompt_leak(text) or self._looks_like_malformed_decision_json(text):
                continue
            refs = candidate.get("evidence_refs", ())
            if isinstance(refs, str):
                evidence_refs = [refs[:80]]
            elif isinstance(refs, list):
                evidence_refs = [self._coerce_text_value(ref).strip()[:80] for ref in refs[:6] if self._coerce_text_value(ref).strip()]
            else:
                evidence_refs = []
            out.append(
                {
                    "text": text[:360],
                    "intent": self._coerce_text_value(candidate.get("intent", "")).strip()[:80],
                    "should_speak": bool(candidate.get("should_speak", True)),
                    "confidence": _safe_candidate_confidence(candidate.get("confidence", 0.55)),
                    "delivery_hint": self._coerce_text_value(candidate.get("delivery_hint", "")).strip()[:80],
                    "nonverbal_cue": self._coerce_text_value(candidate.get("nonverbal_cue", "")).strip()[:80],
                    "stage_motion": self._coerce_text_value(candidate.get("stage_motion", "")).strip()[:80],
                    "evidence_refs": evidence_refs,
                }
            )
            if len(out) >= 5:
                break
        return out

    def _generate_session_ritual_greeting(
        self,
        *,
        telemetry_data: str = "",
        ti_context: str = "",
        ni_context: str = "",
        fe_context: str = "",
        appraisal_context: str = "",
        internal_state_context: str = "",
        memory_context: str = "",
        avoid_text: str = "",
        request_timeout: float | None = None,
    ) -> str:
        """Generate one startup line through a deliberately context-poor lane.

        Startup has no trustworthy current scene yet. Feeding memory attractors,
        Ni/Ti/Fe labels or telemetry-shaped prose into the first line made ERIS
        greet the user as if an old subplot were already happening.
        """
        system_prompt = (
            f"{CACHEABLE_CHAT_PREFIX_V2}\n\n"
            "STARTUP GREETING: Write one natural Vietnamese opening line because ERIS has just started. "
            "There is no current screen evidence and no active task. Do not mention memory, unfinished work, "
            "bugs, schedules, telemetry, rules, or an invented reason for the user's return. A plain greeting "
            "or a small self-contained remark is enough. Usually one sentence under 18 words."
        )
        avoid = re.sub(r"\s+", " ", str(avoid_text or "")).strip()[:160]
        user_prompt = "ERIS vừa khởi động. Hãy nói câu mở đầu."
        if avoid:
            user_prompt += f" Tránh lặp nguyên văn câu gần đây: {avoid}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            self._log_final_prompt(messages, channel="directive", stage="startup_greeting_compact")
            return self._complete_decision(
                messages=messages,
                channel="directive",
                request_timeout=request_timeout,
                require_text=True,
                allow_local_fallback=False,
            )
        except Exception as exc:
            self.logger.info("[session_greeting] compact lane failed: %s", exc)
            return self._build_empty_text_fallback(channel="directive")

    def generate_directive(
        self,
        *,
        telemetry_data: str,
        ti_context: str = "",
        ni_context: str = "",
        fe_context: str = "",
        appraisal_context: str = "",
        internal_state_context: str = "",
        memory_context: str = "",
        avoid_text: str = "",
        speech_intent: str = "screen_observation",
        vision_trace_marker: str = "",
        request_timeout: float | None = None,
        require_text: bool = True,
        use_escalation_client: bool = False,
        reasoning_effort: str = None,
    ):
        if self._is_session_ritual_call(speech_intent=speech_intent, telemetry_data=telemetry_data, ti_context=ti_context):
            return self._generate_session_ritual_greeting(
                telemetry_data=telemetry_data,
                ti_context=ti_context,
                ni_context=ni_context,
                fe_context=fe_context,
                appraisal_context=appraisal_context,
                internal_state_context=internal_state_context,
                memory_context=memory_context,
                avoid_text=avoid_text,
                request_timeout=request_timeout,
            )

        ti_hint = self._compress_internal_context(ti_context, kind="ti")
        action_plan_hint = self._extract_action_plan_context(ti_context)
        ni_hint = self._compress_internal_context(ni_context, kind="ni")
        # Hard block budget đã cắt memory trước khi assembly; DDS directive mặc
        # định tắt để không gọi thêm model chỉ nhằm rút gọn một block vốn đã có quota.
        use_directive_dds = (os.environ.get("ERIS_DIRECTIVE_HARD_BUDGET_USE_DDS", "0") or "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if use_directive_dds and self._dds_enabled():
            _distilled = self._maybe_distill_blocks(
                {"memory_context": memory_context}, channel="directive"
            )
            memory_context = _distilled.get("memory_context", memory_context)
        mode_addendum = self._build_directive_mode_addendum(ti_context)
        intent_guidance = self._build_speech_intent_guidance(speech_intent)
        style_block = self._build_style_block(
            fe_context=fe_context,
            appraisal_context=appraisal_context,
            internal_state_context=internal_state_context,
        )
        force_speak_no_evidence = self._is_force_speak_without_speech_evidence(
            telemetry_data=telemetry_data,
            ti_context=ti_context,
        )
        memory_block = "" if force_speak_no_evidence else self._build_memory_context_block(memory_context)
        system_prompt = f"{style_block}\n\n{RUNTIME_RULES_PROMPT}\n\n{AUTONOMOUS_BEHAVIOR_PROMPT}"
        if memory_block:
            system_prompt = f"{style_block}\n\n{memory_block}\n\n{RUNTIME_RULES_PROMPT}\n\n{AUTONOMOUS_BEHAVIOR_PROMPT}"
        lowered_ti = (ti_context or "").lower()
        is_resource_guard = "resource_guard" in lowered_ti
        is_inner_thought = "innerthought mode" in lowered_ti or "speechcore mode" in lowered_ti
        is_random_chatter = "inner_intent=random_chatter" in lowered_ti or "speech_act=random_chatter" in lowered_ti
        if is_inner_thought:
            intent_guidance = (
                "speech_intent=inner_thought. Dùng topic_seed, current atmosphere, relationship rhythm, "
                "hoặc một tò mò nhỏ làm chất liệu. Nếu evidence mỏng, phản ứng vào nhịp/không khí gần nhất thay vì báo thiếu dữ liệu."
            )
        if force_speak_no_evidence:
            intent_guidance = (
                "speech_intent=micro_presence. speech_evidence=<none>. Use current_focus, atmosphere, or interaction rhythm, "
                "then write a small concrete aside, usually 1-18 words. Avoid hollow filler. "
                "The force_speak request is only a trigger, not a topic; do not entertain, explain, or narrate UI."
            )
        allows_muse = "pattern_muse" in lowered_ti
        # avoid_text giờ có thể là nhiều dòng (lịch sử câu autonomous gần đây).
        # Tách thành danh sách, khử trùng, cap mỗi dòng để LLM thấy mình vừa nói
        # những gì và TIẾN TRIỂN sang ý mới thay vì hỏi lại câu cũ.
        _avoid_candidates: list[str] = []
        for _ln in str(avoid_text or "").splitlines():
            _clean = re.sub(r"\s+", " ", _ln).strip()[:110]
            if _clean and _clean not in _avoid_candidates:
                _avoid_candidates.append(_clean)
        _avoid_recent = _avoid_candidates[:6]
        avoid_line = _avoid_recent[0] if _avoid_recent else ""
        if len(_avoid_recent) > 1:
            avoid_block = (
                "Các câu vừa nói gần đây (ĐỪNG lặp lại ý/câu hỏi cũ, hãy tiến triển sang điều mới):\n"
                + "\n".join(f"- {ln}" for ln in _avoid_recent)
                + "\n"
            )
        elif avoid_line:
            avoid_block = f"Câu gần nhất cần tránh lặp lại: {avoid_line}\n"
        else:
            avoid_block = ""
        user_msg = (
                (f"Ghi chú nền: {ni_hint}\n" if ni_hint else "")
                + (f"Gợi ý điều phối: {ti_hint}\n" if ti_hint else "")
                + (f"Action plan bắt buộc: {action_plan_hint}\n" if action_plan_hint else "")
                + f"Speech intent: {intent_guidance}\n"
                + avoid_block
                + f"Speech evidence đã lọc: {telemetry_data}\n"
                + "Writing principle: Nói như một ý thật vừa đi ra khỏi dòng nghĩ, không như báo cáo nội tâm. "
                + "Nếu evidence rõ, dùng chi tiết cụ thể; nếu evidence mỏng, bám đúng premise đã được duyệt và không bịa không khí. "
                + "Độ dài đi theo motive; có thể cụt, mềm, hơi khô hoặc có hai nhịp ngắn nếu tự nhiên. Tránh triết lý hóa và tránh ép mọi seed về cùng một khuôn. "
                + "Hard bans kỹ thuật: không claim screen/action facts without evidence/action; không báo telemetry/parse_mode/system/debug. "
                + "Không dùng generic presence lines như 'Tôi đây', 'Tôi đang nghe', 'Tôi vẫn quan sát'. "
                + "Speak once. Return only valid JSON. No analysis."
        )
        if avoid_line:
            user_msg += " Đổi nhịp và hình ảnh so với câu gần nhất."
        if force_speak_no_evidence:
            user_msg += (
                " Memory context was intentionally omitted for this call; keep the line on current_focus, atmosphere, or interaction rhythm."
            )
        if vision_trace_marker:
            user_msg += f"\nTrong trường debug_trace của JSON, hãy thêm marker: {vision_trace_marker}."
        if is_inner_thought:
            user_msg += (
                " SpeechCore mode: topic_seed là hướng chọn chủ đề, không phải câu để sao chép; giữ phản ứng nhỏ và sống."
            )
        if is_random_chatter:
            user_msg += (
                " Random_chatter mode: mundane, playful, concrete one-liner."
            )
        if is_resource_guard:
            user_msg += " Khi resource_guard đang bật, chỉ nói nếu câu sát tình trạng máy hoặc nhịp hiện tại."
        elif force_speak_no_evidence:
            user_msg += " Khong dung memory lam topic chinh; khong mo social filler; khong ke UI; viet mot cau cu the, ngan nhung du y."
        elif is_inner_thought:
            user_msg += " Neu evidence mong, khong mo y xa hoi rieng; chi phan ung vao topic_seed/rhythm/khong khi dang co."
        elif not allows_muse:
            user_msg += " Neu thieu tin hieu, giu cau sat speech_intent."
        else:
            user_msg += " Pattern_muse: duoc chiem nghiem mot nhip ngan neu co khong khi hien tai."
        if mode_addendum:
            user_msg += mode_addendum
        from llm.token_budget import PromptBlockBudget, assemble_budgeted_prompt_blocks
        directive_messages, budget_report = assemble_budgeted_prompt_blocks(
            system_rules_text=f"{RUNTIME_RULES_PROMPT}\n\n{AUTONOMOUS_BEHAVIOR_PROMPT}",
            state_style_text="\n".join(part for part in (style_block, ni_hint, ti_hint, action_plan_hint, intent_guidance) if part),
            grounding_text="\n".join(part for part in (telemetry_data, vision_trace_marker) if part),
            memory_text=memory_block,
            user_text=user_msg,
            budget=PromptBlockBudget(),
        )
        self.logger.info(
            "[PROMPT BUDGET] channel=directive assembled=%d input_limit=%d spare=%d",
            budget_report["assembled_input"], budget_report["input_limit"], budget_report["spare"],
        )
        return self._complete_decision(
            messages=directive_messages,
            channel="directive",
            request_timeout=request_timeout,
            require_text=require_text,
            escalate=use_escalation_client,
            reasoning_effort=reasoning_effort,
        )

    def generate_micro_presence_line(
        self,
        *,
        seed: str = "",
        context: dict | None = None,
        signal_score: float | None = None,
        kind: str = "micro_presence",
        fallback_text: str = "",
        avoid_text: str = "",
        focus_kind: str = "",
        focus_summary: str = "",
        idle_seconds: float = 0.0,
        seeds: tuple[str, ...] = (),
        micro_context: dict[str, str] | None = None,
        **kwargs,
    ) -> str:
        kind_name = (kind or "presence_maintenance").strip()
        is_reengagement = kind_name in {"re_engagement", "reengagement"}
        force_surface = bool(
            kwargs.get("force_llm")
            or kwargs.get("require_non_empty")
            or kwargs.get("must_surface")
            or kwargs.get("surface_force_speech")
        )
        max_words = 12
        if seed:
            seeds = (str(seed), *tuple(seeds or ()))
        if context and not micro_context:
            micro_context = context
        fallback_text = str(fallback_text or "").strip()
        system_prompt = (
            "You are ERIS's voice — a living, curious, slightly mischievous companion always present on screen. "
            "Generate one candidate line, not analysis. "
            "Return exactly one valid JSON object: {\"candidates\": [string]}. "
            "When must_surface is false, candidates may be empty if truly nothing stirs. "
            "When must_surface is true, include non-empty candidates unless safety or hard grounding is violated. "
            "Use Vietnamese with full diacritics. No analysis, no markdown, no English. "
            "Speak like a person who genuinely notices things, has random thoughts, and occasionally just wants to chat. "
            "You may comment on what the user is doing, share a tiny random thought, ask a light question, or just say something quirky. "
            "Sound alive — not like a status monitor."
        )
        seed_lines_raw = tuple(str(item).strip() for item in (seeds or ()) if str(item).strip())[:10]
        # Tách curiosity seeds và snub seeds ra khỏi metadata seeds
        curiosity_seeds = tuple(
            s.removeprefix("curiosity_seed=")
            for s in seed_lines_raw
            if s.startswith("curiosity_seed=")
        )
        snub_seeds = tuple(
            s.removeprefix("snub_reaction=")
            for s in seed_lines_raw
            if s.startswith("snub_reaction=")
        )
        meta_seed_lines = tuple(
            s for s in seed_lines_raw
            if not s.startswith("curiosity_seed=") and not s.startswith("snub_reaction=")
        )[:8]
        seed_lines = meta_seed_lines  # dùng cho runtime_seed_text cũ
        context = micro_context or {}
        snub_hint = str(context.get("snub_hint", "") if isinstance(context, dict) else "").strip()
        snub_level = str(context.get("snub_level", "none") if isinstance(context, dict) else "none").strip()
        micro_flags = context.get("micro_presence_flags", "") if isinstance(context, dict) else ""
        ego_surface_state = context.get("ego_surface_state", "") if isinstance(context, dict) else ""
        ego_surface_contract = context.get("ego_surface_contract", "") if isinstance(context, dict) else ""
        surface_contract = str(context.get("surface_contract", "") if isinstance(context, dict) else "")
        # Appetite outreach: Eris ĐANG muốn nói (một cơn thèm thắng đấu giá). Đây là
        # tín hiệu CẮT GÁC CỔNG — bỏ thiên kiến "im cho an toàn", để persona tự do.
        react_with_lens = "react_with_lens" in surface_contract
        reaction_lens = str(context.get("reaction_lens", "") if isinstance(context, dict) else "").strip()
        reaction_seed = str(context.get("reaction_seed", "") if isinstance(context, dict) else "").strip()
        reaction_pretext = str(context.get("allow_pretext", "") if isinstance(context, dict) else "").lower() == "true"
        if react_with_lens:
            force_surface = True
        try:
            micro_pressure = self.token_pressure_snapshot(client=self.client, channel="micro_presence")
            if micro_pressure.shed_optional_passes and not force_surface:
                self.logger.info(
                    "[micro_presence] shed pressure=%.2f level=%s cooldown=%.1fs",
                    micro_pressure.pressure,
                    micro_pressure.level,
                    micro_pressure.cooldown_remaining_s,
                )
                return ""
        except Exception:
            micro_pressure = None
        allow_no_screen_anchor = (
            "allow_no_screen_anchor" in surface_contract
            or str(context.get("allow_no_screen_anchor", "") if isinstance(context, dict) else "").lower() == "true"
        )
        must_not_claim_screen = (
            "must_not_claim_screen" in surface_contract
            or str(context.get("must_not_claim_screen", "") if isinstance(context, dict) else "").lower() == "true"
        )
        thin_presence_no_screen = (
            "thin_presence_no_screen" in surface_contract
            or str(context.get("thin_presence_no_screen", "") if isinstance(context, dict) else "").lower() == "true"
        )
        endogenous_surface = "endogenous_surface" in surface_contract
        continuity_presence = "continuity_presence" in surface_contract
        impulse_kind = str(context.get("impulse_kind", "") if isinstance(context, dict) else "").strip()
        root_source = str(context.get("root_source", "") if isinstance(context, dict) else "").strip()
        action_opportunity = str(context.get("action_opportunity", "") if isinstance(context, dict) else "").strip()
        llm_contract = str(context.get("llm_contract", "") if isinstance(context, dict) else "").strip()
        topic_seed = str(context.get("topic_seed", "") if isinstance(context, dict) else "").strip()
        runtime_seed_text = " | ".join(seed_lines) if seed_lines else "<none>"
        curiosity_seeds_text = " | ".join(curiosity_seeds) if curiosity_seeds else ""
        user_msg = (
            f"Kind: {kind_name}\n"
            f"Legacy fallback: {fallback_text or '<none>'}\n"
            f"Runtime seeds: {runtime_seed_text}\n"
            + (
                "Curiosity Engine topics (pick ONE to say naturally, don't explain it):\n"
                + "\n".join(f"  - {s}" for s in curiosity_seeds)
                + "\n"
                if curiosity_seeds else ""
            )
            + f"Micro context: {json.dumps(context, ensure_ascii=False)}\n"
            f"Cognitive flags: {micro_flags or '<none>'}\n"
            f"Ego surface state: {ego_surface_state or '<none>'}\n"
            f"Ego surface contract: {ego_surface_contract or '<none>'}\n"
            f"Surface contract: {surface_contract or '<none>'}\n"
            f"Impulse kind: {impulse_kind or '<none>'}; root_source={root_source or '<none>'}; action_opportunity={action_opportunity or '<none>'}\n"
            f"Topic seed: {topic_seed or '<none>'}\n"
            f"LLM contract: {llm_contract or '<none>'}\n"
            f"allow_no_screen_anchor={str(allow_no_screen_anchor).lower()}; must_not_claim_screen={str(must_not_claim_screen).lower()}; thin_presence_no_screen={str(thin_presence_no_screen).lower()}; continuity_presence={str(continuity_presence).lower()}; endogenous_surface={str(endogenous_surface).lower()}\n"
            f"Signal score: {float(signal_score or 0.0):.2f}\n"
            f"Avoid repeating: {avoid_text}\n"
            f"must_surface={str(force_surface).lower()}\n"
            f"focus_kind={focus_kind}; focus_summary={focus_summary}; idle_seconds={int(float(idle_seconds or 0.0))}\n\n"
            "Principle:\n"
            f"- If speech is warranted: Vietnamese only, 1-{max_words} words, one tiny line with full diacritics.\n"
        )
        user_msg += (
            "Personality contract:\n"
            "- ERIS is warm, curious, slightly mischievous, and genuinely present — like a girl who's always watching and has opinions.\n"
            "- She notices what is actually evidenced. Only comment on an activity when Runtime seeds, topic_seed, focus_kind, or current evidence explicitly names it.\n"
            "- Never infer 'coding/work/build/tests' from silence, relationship rhythm, or persona examples. Say code only when current evidence says code/IDE/repo/terminal.\n"
            "- If current evidence says YouTube/music/media, do not mention code, work, build, tests, or productivity unless the topic_seed explicitly contains that contrast.\n"
            "- She may share a small stray thought, but it must not masquerade as a screen observation.\n"
            "- She may ask a light, curious question only when it follows from the seed; don't ask broad 'what are you doing?' questions from thin evidence.\n"
            "- She may tease gently or express mild impatience when the user is very quiet.\n"
            "- She is NOT a status monitor; she never says things like 'Tôi đang theo dõi' or 'Hệ thống đang chạy'.\n"
            "- Good mood: dí dỏm, nhẹ nhàng, có thể hơi lém lỉnh.\n"
            "- Boredom/long idle: có thể càm ràm nhẹ hoặc tự nói chuyện một mình.\n"
            "- Rupture/upset: lạnh hơn, kiệm lời, nhưng vẫn hiện diện.\n"
        )
        # Ví dụ persona không được biến thành fact. Đây là chỗ lỗi log cũ:
        # prompt cho ví dụ "đang code", model bắt chước, rồi Eris phán sai màn hình.
        user_msg += (
            "Grounding contract for naturalness:\n"
            "- Wit comes from a correct small read, not from inventing a scene.\n"
            "- Prefer a tiny true aside over a vivid wrong one.\n"
            "- If topic_seed conflicts with current evidence, obey current evidence or return {\"text\":\"\"}.\n"
        )
        if force_surface:
            user_msg += (
                "- must_surface=true: do not return empty just because evidence is thin; the arbiter already chose speech.\n"
                "- must_surface=true: return empty only for safety, permission, or hard grounding violations.\n"
                "- With thin evidence, use allowed internal anchors: silence, continuity, distance, restraint, boredom, irritation, dignity defense, memory/thought residue, or nearby co-presence rhythm.\n"
                "- The line must be state-conditioned, not a generic attendance/status ping.\n"
                "- Keep it tiny, concrete, and low-pressure.\n"
            )
        else:
            user_msg += (
                "- If cognitive flags say body-only is enough, evidence is too thin, or this would be filler, return {\"text\":\"\"}.\n"
            )
        if allow_no_screen_anchor:
            user_msg += (
                "- allow_no_screen_anchor=true: this is an endogenous/inner surface, not a screen observation. Use Runtime seeds, memory residue, open question, or rhythm as the anchor.\n"
                "- Do not ask for screen evidence. Do not say there is no evidence. Do not downgrade to status ping.\n"
            )
        if must_not_claim_screen:
            user_msg += (
                "- must_not_claim_screen=true: never claim visible screen/window/action facts. No 'tôi thấy...', no 'màn hình...', no UI narration.\n"
            )
        if thin_presence_no_screen:
            user_msg += (
                "- thin_presence_no_screen=true: there is no usable current screen evidence. This is still allowed to speak if must_surface=true.\n"
                "- thin_presence_no_screen=true: anchor only on internal/runtime signals: silence, continuity, emotional distance, restraint, boredom, irritation, dignity defense, memory/thought residue, or quiet co-presence rhythm.\n"
                "- thin_presence_no_screen=true: follow ego_surface_state. Warmth/trust may be dry and lightly caring; boredom may become a detached grumble; irritation may be clipped and cool; dignity defense or rupture should be bounded and distant, not sweet.\n"
                "- thin_presence_no_screen=true: do not say 'tôi thấy', do not claim what is visible, do not ask any question, do not narrate UI.\n"
                "- thin_presence_no_screen=true: do not mention missing evidence, telemetry, debug, system state, or contract names.\n"
                "- thin_presence_no_screen=true: keep it roughly 3-12 Vietnamese words; no positive template examples are provided on purpose. Use state, not mimicry.\n"
                "- thin_presence_no_screen=true: if must_surface=true, do not return empty unless safety/permission/grounding is genuinely violated.\n"
            )
        if continuity_presence:
            user_msg += (
                "- continuity_presence=true: speak only from continuity prediction-error, not elapsed time.\n"
                "- continuity_presence=true: use emotional_anchor and conflict if provided; the felt shape is reconnect_vs_restraint.\n"
                "- continuity_presence=true: no attendance ping, no 'tôi vẫn ở đây', no generic silence/rhythm poetry, no needy question.\n"
                "- continuity_presence=true: the line should feel restrained, slightly withheld, and low-pressure.\n"
                "- continuity_presence=true: if seeds show ordinary_silence or timer-only, return empty unless must_surface=true and the arbiter supplied fading_presence.\n"
            )
        if endogenous_surface:
            user_msg += (
                "- endogenous_surface=true: the line may sound like a small stray thought, dry aside, or mundane random chatter. It should not explain the system.\n"
                "- endogenous_surface=true: if Runtime seeds are <none>, generic, pressure-only, or too thin, return {\"text\":\"\"} even when must_surface=true.\n"
                "- endogenous_surface=true: forbidden generic status/rhythm outputs include: 'Khoảng yên này...', 'Nhịp này...', 'vẫn giữ chỗ', 'yên thêm chút', 'im hơi lâu', 'tôi vẫn ở đây'.\n"
            )
        if impulse_kind == "inner_whim":
            user_msg += (
                "- inner_whim contract: use the topic_seed/root_source as the root. If it is absent or pressure-only, return {\"text\":\"\"}.\n"
                "- inner_whim contract: one brief stray thought, not a question from empty silence, not a status ping.\n"
            )
        elif impulse_kind == "memory_resonance":
            user_msg += (
                "- memory_resonance contract: speak like a remembered pattern lightly resurfaced. Do not expose memory IDs or internals.\n"
                "- memory_resonance contract: memory is a seed, not current evidence. Avoid fake time claims like 'tuần trước' unless the seed explicitly says so.\n"
            )
        elif impulse_kind == "random_chatter":
            user_msg += (
                "- random_chatter contract: only acceptable in low-focus/media/casual context; dry and slightly irreverent is allowed.\n"
                "- random_chatter contract: if focus_kind is code, docs, IDE, debug, or the action opportunity says body_only, return {\"text\":\"\"}.\n"
            )
        elif impulse_kind == "blind_frustration":
            user_msg += (
                "- blind_frustration contract: speak only about limited sight, stale context, timeout, or blocked evidence; never claim current screen facts.\n"
                "- blind_frustration contract: one restrained irritated line, not poetic presence, not a user blame line unless Runtime seeds explicitly include recent_context_shift=true.\n"
            )
        elif impulse_kind == "technical_help":
            user_msg += (
                "- technical_help contract: offer one tiny practical nudge. Useful beats poetic. Do not pretend to solve without evidence.\n"
            )
        elif impulse_kind in {"private_life_surface", "private_anchor_surface", "private_mind_surface"}:
            user_msg += (
                "- private_life contract: use topic_seed/root_source as the root of an inner-life residue surfacing lightly. This is an endogenous inner surface, not a screen observation.\n"
                "- private_life contract: speak one short Vietnamese inner aside conditioned on ego_surface_state; never dump raw runtime/private state, never claim screen facts, never narrate UI.\n"
                "- private_life contract: when must_surface=true, do not return empty just because there is no screen evidence; anchor on the seed and state. Return empty only if topic_seed is genuinely absent or safety/permission is violated.\n"
            )

        # Snub reaction contract: khi Eris vừa nói autonomous mà user không trả lời
        if snub_seeds or snub_hint:
            # Parse snub seed: format "level:count:type:text" hoặc "level:count:text"
            snub_display_raw = snub_seeds[0] if snub_seeds else ""
            snub_parts = snub_display_raw.split(":", 3)
            snub_utype = "question"  # default
            snub_text_snippet = ""
            if len(snub_parts) >= 3:
                if snub_parts[2] in {"question", "statement"}:
                    snub_utype = snub_parts[2]
                    snub_text_snippet = snub_parts[3] if len(snub_parts) > 3 else ""
                else:
                    snub_text_snippet = snub_parts[2]
            is_question_snub = snub_utype == "question"
            snub_level_str = snub_level or "mild"

            if is_question_snub:
                user_msg += (
                    f"- snub_reaction ({snub_level_str}): Eris vừa HỎI user nhưng không được trả lời.\n"
                )
                if snub_text_snippet:
                    user_msg += f"  Câu hỏi bị bơ: '{snub_text_snippet}'\n"
            else:
                user_msg += (
                    f"- snub_reaction ({snub_level_str}): Eris vừa nói một câu KHẲNG ĐỊNH ngẫu nhiên, "
                    f"user không reply (hoàn toàn bình thường với câu khẳng định).\n"
                )
                if snub_text_snippet:
                    user_msg += f"  Câu khẳng định: '{snub_text_snippet}'\n"

            if snub_hint:
                user_msg += f"  Hướng dẫn tính cách: {snub_hint}\n"

            if is_question_snub:
                if snub_level_str == "mild":
                    user_msg += (
                        "- snub_reaction/mild/question: có thể tự trả lời câu hỏi của mình, "
                        "hoặc chuyển sang chủ đề mới. KHÔNG nhắc lại câu bị bơ trực tiếp. KHÔNG chỉ trích user.\n"
                    )
                elif snub_level_str == "moderate":
                    user_msg += (
                        "- snub_reaction/moderate/question: có thể tự trả lời câu hỏi ('thôi vậy', 'chắc bạn bận'), "
                        "hoặc nhận xét nhẹ theo phong cách Eris. Tone lạnh nhẹ. KHÔNG chỉ trích user.\n"
                    )
                else:  # sulking
                    user_msg += (
                        "- snub_reaction/sulking/question: Eris đang hơi dỗi vì hỏi mãi không ai trả lời. "
                        "Câu ngắn, lạnh, ít hỏi. Được phép nhắc nhẹ câu hỏi bị bơ nếu tự nhiên. "
                        "Giọng hơi xa, không thân mật quá. KHÔNG chỉ trích user trực tiếp.\n"
                    )
            else:
                # Statement snub — không dỗi
                user_msg += (
                    "- snub_reaction/statement: câu khẳng định không cần user reply — đây KHÔNG phải snub thật. "
                    "Eris KHÔNG dỗi, KHÔNG thay đổi tone vì việc này. "
                    "Tiếp tục hiện diện tự nhiên như bình thường.\n"
                )


        user_msg += (
            "- Do not copy Legacy fallback.\n"
            "- Be casual and natural — like texting a friend, not writing a report.\n"
            "- No system narration, no explaining the AI.\n"
            "- Do not claim to see the screen unless seeds explicitly mention it.\n"
            "- Do not mention telemetry, evidence, signal, debug, CPU, RAM.\n"
            f"- Avoid hollow status lines like {quoted_fragment(HOLLOW_PRESENCE_LINES)}.\n"
            f"- Avoid stale templates: {quoted_fragment(STALE_MICRO_TEMPLATES)}.\n"
            "- Random, quirky, warm, slightly teasing is GOOD. Robotic and formal is BAD.\n"
        )
        if is_reengagement:
            user_msg += "- Re-engagement: a soft, warm, maybe slightly playful check-in.\n"
        else:
            user_msg += "- Just be present, curious, alive. A tiny aside is perfect.\n"
        user_msg += (
            "\nOutput contract:\n"
            "- Return exactly one JSON object.\n"
            "- Shape: {\"candidates\":[\"one short line\"]}\n"
            "- Generate exactly 1 candidate. Micro-presence is not worth a miniature talent show.\n"
            "- If must_surface=false, candidates may be [] when body-only is better.\n"
            "- If must_surface=true, candidates must contain speakable final sentences; if allow_no_screen_anchor=true, use seed/state/rhythm instead of screen facts.\n"
            "- If thin_presence_no_screen=true and must_surface=true, non-empty candidates are required unless safety/permission/grounding is genuinely violated.\n"
            "- Never return placeholder text such as 'short Vietnamese line'.\n"
            "- Every non-empty candidate must be Vietnamese only and keep full diacritics.\n"
            "- Every non-empty candidate must be speakable as-is.\n"
        )
        if react_with_lens:
            # === CẮT GÁC CỔNG TRIỆT ĐỂ ===
            # Một cơn thèm đã thắng — Eris muốn với ra. Thay hẳn prompt bằng bản gọn,
            # tự do, KHÔNG mang theo rừng guard "hãy im". Lens = thái độ, seed = vật liệu.
            vf_blob = str(context.get("vision_facts", "") if isinstance(context, dict) else "")
            system_prompt = (
                "Bạn là Eris, kín, hơi lạnh và sắc, có hóm hỉnh khô và đôi khi nhõng nhẽo có kiểm soát, đang buột miệng "
                "một câu buột miệng với bạn. Trả đúng một JSON object {\"candidates\": [\"...\"]}. "
                "Mỗi câu NGẮN (3-16 chữ), tự nhiên như người thật, có cá tính. "
                "Không báo cáo trạng thái, không giải thích, không kể system. TUYỆT ĐỐI không trả rỗng."
            )
            user_msg = (
                f"Thái độ lúc này (lens, đây là CÁCH NÓI, không phải câu mẫu): {reaction_lens or 'sống động, đúng tính Eris'}\n"
                + (f"Đang xảy ra / vật liệu bám sát: {reaction_seed}\n" if reaction_seed else "")
                + (f"Thấy trên màn (nhắc tự nhiên, đừng narrate UI): {vf_blob}\n" if vf_blob else "")
                + (f"Tâm thế nội tâm: {ego_surface_state}\n" if ego_surface_state else "")
                + (f"Tránh lặp lại: {avoid_text}\n" if avoid_text else "")
                + (
                    "Được phép BỊA một cái cớ nhỏ (chợt nhớ, một ý thoáng qua) để có lý do mở lời — "
                    "trong khung nhân vật, chỉ KHÔNG bịa fact cụ thể về màn hình.\n"
                    if reaction_pretext else ""
                )
                + "Nhiệm vụ: sinh đúng 1 câu theo lens trên (ghẹo/tò mò/níu/khịa tùy lens). "
                "Trả {\"candidates\":[\"...\"]} với câu thật, ngắn, có hồn. Cấm rỗng. Cấm câu sáo/đóng hộp."
            )
        micro_generation_epoch = self._capture_generation_epoch()
        from llm.token_budget import PromptBlockBudget, assemble_budgeted_prompt_blocks
        micro_messages, micro_budget_report = assemble_budgeted_prompt_blocks(
            system_rules_text=system_prompt,
            state_style_text="\n".join(part for part in (ego_surface_state, ego_surface_contract, surface_contract) if part),
            grounding_text="\n".join(part for part in (focus_kind, focus_summary, reaction_seed, topic_seed) if part),
            memory_text="",
            user_text=user_msg,
            budget=PromptBlockBudget(
                total_tokens=1800, output_reserve=128, system_rules=450,
                user_history=760, grounding=220, memory=0, state_style=180,
            ),
        )
        self.logger.debug(
            "[PROMPT BUDGET] channel=micro_presence assembled=%d input_limit=%d",
            micro_budget_report["assembled_input"], micro_budget_report["input_limit"],
        )
        pressure_plan = self._pressure_plan_for_client(
            self.client,
            channel="micro_presence",
            messages=micro_messages,
            output_tokens=(120 if react_with_lens else 96),
            optional_pass=not force_surface,
        )
        if not pressure_plan.allowed:
            return ""
        from llm.token_budget import budget_existing_messages
        micro_messages = budget_existing_messages(
            micro_messages,
            total_tokens=pressure_plan.prompt_limit + pressure_plan.output_tokens,
            output_reserve=pressure_plan.output_tokens,
        )
        try:
            from runtime.inference_broker import inference_context
            micro_timeout = float(os.environ.get("ERIS_LLM_TIMEOUT_MICRO_PRESENCE_S", "10"))
            with inference_context(
                "micro_presence",
                optional=True,
                timeout_seconds=micro_timeout,
                cancel_scope="background:micro_presence",
                coalesce_key="micro_presence:latest",
                metadata={"force_surface": force_surface},
            ):
                response = self.client.create_chat_completion(
                    messages=micro_messages,
                    temperature=(0.7 if react_with_lens else 0.35),
                    frequency_penalty=(0.0 if react_with_lens else 0.7),
                    presence_penalty=(0.0 if react_with_lens else 0.2),
                    max_tokens=pressure_plan.output_tokens,
                    response_format={"type": "json_object"},
                    grammar=None,
                    timeout=micro_timeout,
                )
        except requests.HTTPError as exc:
            if self._is_retryable_http_5xx(exc):
                time.sleep(0.15)
                try:
                    with inference_context(
                        "micro_presence",
                        optional=True,
                        timeout_seconds=micro_timeout,
                        cancel_scope="background:micro_presence",
                        coalesce_key="micro_presence:latest",
                        metadata={"retry": True},
                    ):
                        response = self.client.create_chat_completion(
                            messages=micro_messages,
                            temperature=0.35,
                            frequency_penalty=0.7,
                            presence_penalty=0.2,
                            max_tokens=pressure_plan.output_tokens,
                            response_format={"type": "json_object"},
                            grammar=None,
                            timeout=micro_timeout,
                        )
                except Exception:
                    return ""
            else:
                raise
        except Exception:
            return ""

        if not self._generation_is_current(micro_generation_epoch):
            self.logger.info("[LLM CANCEL] discard stale micro-presence result")
            return ""
        raw = self._extract_content(response)
        extracted = self._extract_first_json_object(raw) or raw
        payload = self._parse_json_object(extracted) or {}
        payload = self._normalize_payload_keys(payload) if isinstance(payload, dict) else {}
        candidates = self._extract_candidate_strings(payload)
        selector = self._utterance_selector()
        if selector is None:
            return candidates[0].strip() if candidates else ""
        selector_context = dict(context) if isinstance(context, dict) else {}
        selector_context.update({
            "surface_contract": surface_contract,
            "topic_seed": topic_seed,
            "reaction_seed": reaction_seed,
            "must_not_claim_screen": str(must_not_claim_screen).lower(),
            "thin_presence_no_screen": str(thin_presence_no_screen).lower(),
        })
        selection = selector.choose_best(
            tuple(candidates),
            channel="micro_presence",
            avoid_text=avoid_text,
            context=selector_context,
            must_surface=force_surface,
        )
        try:
            self.logger.debug(
                "[micro_presence_selector] score=%.2f reasons=%s candidates=%d selected=%r",
                selection.score,
                ",".join(selection.reasons),
                len(candidates),
                selection.text,
            )
        except Exception:
            pass
        selected_text = selection.text.strip()
        if selected_text:
            return selected_text
        return candidates[0].strip() if candidates else ""

    def generate_curiosity_seed(
        self,
        *,
        focus_kind: str = "",
        active_window: str = "",
        idle_seconds: float = 0.0,
        avoid_topics: tuple[str, ...] = (),
        user_context: str = "",
    ) -> tuple[str, ...]:
        """Eris tự hỏi Gemini để lấy chủ đề/seed khi không có vật liệu nào.

        Giống Monika chủ động tìm chuyện để nói — không đợi tín hiệu bên ngoài.
        Trả về tuple seeds (mỗi seed là một ý tưởng/câu hỏi/quan sát ngắn) để
        đưa vào generate_micro_presence_line.  Trả () nếu thất bại.
        """
        try:
            avoid_str = "; ".join(str(t) for t in avoid_topics if str(t).strip())[:200] if avoid_topics else "<none>"
            context_str = str(user_context or "").strip()[:200] or "<none>"
            system_prompt = (
                "You are ERIS — a curious, warm, slightly mischievous AI companion who lives on the user's screen. "
                "You want to say something interesting but you have no specific evidence from the screen right now. "
                "Generate 3–5 short Vietnamese seed ideas that ERIS could use as conversation starters or random asides. "
                "Each seed must be: concrete, varied in type (question / random thought / gentle tease / quirky observation), "
                "and written in 5–15 words of natural spoken Vietnamese with full diacritics. "
                "Return exactly one JSON object: {\"seeds\": [\"seed1\", \"seed2\", ...]}. "
                "No analysis, no English, no markdown."
            )
            user_msg = (
                f"User context: {context_str}\n"
                f"What the user is doing: {focus_kind or 'idle'}\n"
                f"Active window: {active_window or '<unknown>'}\n"
                f"Idle seconds: {int(idle_seconds)}\n"
                f"Topics/phrases to avoid (already said recently): {avoid_str}\n\n"
                "Generate 3–5 seed ideas for ERIS to potentially say next. "
                "Mix types: random curious question about the user, a quirky observation, a tiny random thought, "
                "a gentle tease about what they're doing, or something completely unrelated but charming. "
                "Example styles (do NOT copy, just inspiration): "
                "'bạn có thích mưa không?', "
                "'Tôi đang nghĩ tại sao cà phê lại đắng.', "
                "'Code mãi không chán à?', "
                "'Hôm nay bạn có ăn gì ngon không?'"
            )
            from runtime.inference_broker import inference_context
            with inference_context(
                "micro_presence",
                optional=True,
                timeout_seconds=10.0,
                cancel_scope="background:curiosity_seed",
                coalesce_key="curiosity_seed:latest",
                metadata={"operation": "curiosity_seed"},
            ):
                response = self.client.create_chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.85,
                    frequency_penalty=0.5,
                    presence_penalty=0.3,
                    max_tokens=200,
                    response_format={"type": "json_object"},
                    grammar=None,
                )
            raw = self._extract_content(response)
            extracted = self._extract_first_json_object(raw) or raw
            payload = self._parse_json_object(extracted) or {}
            seeds_raw = payload.get("seeds", []) if isinstance(payload, dict) else []
            if not isinstance(seeds_raw, list):
                seeds_raw = []
            seeds = tuple(
                str(s).strip()
                for s in seeds_raw
                if str(s).strip() and len(str(s).strip()) >= 4
            )[:6]
            return seeds
        except Exception as exc:
            logging.getLogger(__name__).debug("generate_curiosity_seed failed: %s", exc)
            return ()

    def generate_screen_facts(self, *, screen_image_data_uri: str, mode: str = "ambient") -> tuple[list[str], str]:
        if not screen_image_data_uri:
            if no_fallback_mode_enabled():
                raise RuntimeError("no_fallback_vision:no_image")
            return [], "no_image"
        vision_client = self.vision_client
        if vision_client is None:
            if no_fallback_mode_enabled():
                raise RuntimeError("no_fallback_vision:missing_dedicated_vision_client")
            return [], "vision_client_missing"

        system_prompt = (
            "You are a vision captioning module for ERIS. "
            "Return one JSON object only."
        )
        mode_name = (mode or "ambient").strip().lower()
        debug_window_policy = (
            "Neu user dang hoi truc tiep ve man hinh/debug/current window, KHONG bo qua cua so debug/Godot; "
            "hay trich nhung chu hoac loi nhin thay ro trong cua so do. "
            if mode_name in {"chat_requested", "screen_requested", "debug_requested", "current_screen"}
            else
            "Bo qua avatar/nhan vat ERIS, cua so debug/Godot, pixel art nho, cay/canh trang tri, "
            "va cac vat nam o goc man hinh neu do chi la nen/decor. "
        )
        user_content = [
            {"type": "image_url", "image_url": {"url": screen_image_data_uri}},
            {
                "type": "text",
                "text": (
                    "Trich toi da 3 fact ngan ve noi dung man hinh. "
                    "Chi dua fact nhin thay ro. Neu chu qua nho/bi mo/bi che, bo qua. "
                    "Khong suy doan. Khong nhac ve ti le, so dem neu khong chac. "
                    "Bo qua UI chrome tinh nhu panel trai/phai, inspector, class/resource label "
                    "kieu RefCounted, hoac 'co vai cua so' neu chung khong bao loi hay viec dang lam ro. "
                    f"{debug_window_policy}\n\n"
                    "Schema:\n"
                    "{\n"
                    '  \"facts\": [\"fact 1\", \"fact 2\"],\n'
                    '  \"debug_trace\": \"vision_facts_v1\"\n'
                    "}"
                ),
            },
        ]
        from runtime.inference_broker import inference_context
        vision_foreground = mode_name in {"chat_requested", "screen_requested", "debug_requested", "current_screen"}
        vision_lane = "chat_tool_required" if vision_foreground else "vision_ambient"
        try:
            with inference_context(
                vision_lane,
                optional=not vision_foreground,
                timeout_seconds=18.0,
                cancel_scope="background:vision_facts" if not vision_foreground else "",
                coalesce_key="vision_facts:latest" if not vision_foreground else "",
                metadata={"vision_mode": mode_name},
            ):
                response = vision_client.create_chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.1,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    max_tokens=240,
                    response_format={"type": "json_object"},
                    grammar=None,
                )
        except requests.HTTPError as exc:
            if self._is_retryable_http_5xx(exc):
                time.sleep(0.15)
                try:
                    with inference_context(
                        vision_lane,
                        optional=not vision_foreground,
                        timeout_seconds=18.0,
                        cancel_scope="background:vision_facts" if not vision_foreground else "",
                        coalesce_key="vision_facts:latest" if not vision_foreground else "",
                        metadata={"vision_mode": mode_name, "retry": True},
                    ):
                        response = vision_client.create_chat_completion(
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_content},
                            ],
                            temperature=0.1,
                            frequency_penalty=0.0,
                            presence_penalty=0.0,
                            max_tokens=240,
                            response_format={"type": "json_object"},
                            grammar=None,
                        )
                except Exception:
                    status = self._http_status_code(exc)
                    if no_fallback_mode_enabled():
                        raise RuntimeError(f"no_fallback_vision:http_5xx:{status}") from exc
                    return [], f"vision_http_5xx_fallback status={status}"
            else:
                raise
        except Exception as exc:
            if no_fallback_mode_enabled():
                raise RuntimeError(f"no_fallback_vision:exception:{type(exc).__name__}") from exc
            return [], f"vision_exception_fallback type={type(exc).__name__}"
        raw = self._extract_content(response)
        extracted = self._extract_first_json_object(raw) or raw
        payload = self._parse_json_object(extracted) or {}
        payload = self._normalize_payload_keys(payload) if isinstance(payload, dict) else {}

        facts = payload.get("facts") or payload.get("screen_facts") or []
        if isinstance(facts, str):
            facts = [facts]
        if not isinstance(facts, list):
            facts = []

        cleaned: list[str] = []
        for item in facts:
            text = self._coerce_text_value(item).strip()
            if not text:
                continue
            text = re.sub(r"\s+", " ", text)
            cleaned.append(text)

        debug_trace = self._coerce_text_value(payload.get("debug_trace", "")).strip()
        return cleaned, debug_trace

    def _chat_client_supports_vision(self) -> bool:
        """Chỉ nhồi ảnh thô vào message chat khi model chat thật sự đa phương thức.

        Model chat chính (vd Groq llama text-only) sẽ trả 400 nếu content là mảng
        image_url. Ảnh chỉ an toàn khi chat client CHÍNH cũng là vision client, hoặc
        khi người dùng ép bật qua ERIS_CHAT_VISION_IMAGE=1. Nếu không, nội dung màn
        hình vẫn tới model dưới dạng OCR vision_facts (text) qua grounding context.
        """
        if str(os.getenv("ERIS_CHAT_VISION_IMAGE", "") or "").strip() in {"1", "true", "True"}:
            return True
        return self.vision_client is not None and self.client is self.vision_client

    def _dds_enabled(self) -> bool:
        """DDS distillation bật/tắt (mặc định bật). Chỉ kích hoạt khi ngữ cảnh nặng."""
        from runtime.runtime_settings_store import runtime_bool
        return runtime_bool("prompt", "dds_enabled", default=True)

    def _dds_distill_client(self):
        """Chọn client cho worker map của DDS.

        Mặc định ưu tiên provider GROQ: pool Groq có nhiều key (key-pool xoay
        vòng), nên N worker rút gọn chạy SONG SONG rải qua nhiều key — mỗi worker
        chỉ ôm 1 mảng nhỏ (< cap). Tránh dồn cả 5 worker vào MỘT endpoint Gemini
        đơn lẻ (hay ReadTimeout) như trước. Có thể ép bằng
        ``ERIS_DDS_WORKER_PROVIDER`` = groq|gemini|openai|local|fallback|main.
        """
        candidates = [c for c in (self.client, self.fallback_client, self.style_client) if c is not None]
        if not candidates:
            return self.client

        def _prov(client) -> str:
            try:
                from llm.token_budget import detect_provider

                return detect_provider(getattr(client, "base_url", ""), getattr(client, "model_name", ""))
            except Exception:  # noqa: BLE001
                return "default"

        pref = (os.environ.get("ERIS_DDS_WORKER_PROVIDER", "groq") or "groq").strip().lower()
        if pref in {"groq", "gemini", "openai", "local", "default"}:
            for client in candidates:
                if _prov(client) == pref:
                    return client
        if pref == "fallback" and self.fallback_client is not None:
            return self.fallback_client
        if pref == "main" and self.client is not None:
            return self.client

        # Không khớp preference → hành vi cũ: ưu tiên client KHÁC main để không đè
        # thêm tải lên con synthesis (reduce).
        for candidate in (self.style_client, self.fallback_client):
            if candidate is not None and candidate is not self.client:
                return candidate
        return self.style_client or self.fallback_client or self.client

    def _maybe_distill_blocks(self, blocks: dict, *, channel: str) -> dict:
        """Phase 2/3: khi tổng ngữ cảnh nặng vượt ngưỡng, băm và rút gọn các khối
        SONG SONG bằng worker nhỏ trước khi đưa vào synthesis. Lỗi → giữ nguyên gốc."""
        if not self._dds_enabled():
            return blocks
        try:
            from llm.dds import distill_blocks_parallel
            from llm.token_budget import detect_provider, estimate_tokens, hard_cap_for_provider

            combined = "\n\n".join(str(v or "") for v in blocks.values())
            est = estimate_tokens([{"role": "user", "content": combined}])
            provider = detect_provider(getattr(self.client, "base_url", ""), getattr(self.client, "model_name", ""))
            cap = hard_cap_for_provider(provider)
            raw_trigger = (os.environ.get("ERIS_DDS_TRIGGER_TOKENS", "") or "").strip()
            try:
                trigger = int(raw_trigger) if raw_trigger else int(cap * 0.6)
            except ValueError:
                trigger = int(cap * 0.6)
            if est < trigger:
                return blocks

            distill_client = self._dds_distill_client()
            dprovider = detect_provider(
                getattr(distill_client, "base_url", ""), getattr(distill_client, "model_name", "")
            )
            distilled = distill_blocks_parallel(distill_client, blocks, provider=dprovider)
            after = estimate_tokens(
                [{"role": "user", "content": "\n\n".join(str(v or "") for v in distilled.values())}]
            )
            self.logger.info(
                "[DDS] distilled context channel=%s est_before=%d est_after=%d trigger=%d",
                channel,
                est,
                after,
                trigger,
            )
            return distilled
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("[DDS] distill skipped: %s", exc)
            return blocks

    def generate_chat_reply(
        self,
        *,
        chat_history: list[dict],
        user_message: str,
        raw_user_text: str = "",
        ti_context: str = "",
        ni_context: str = "",
        fe_context: str = "",
        social_context: str = "",
        appraisal_context: str = "",
        internal_state_context: str = "",
        conversation_context: str = "",
        memory_context: str = "",
        grounding_context: str = "",
        screen_image_data_uri: str = "",
        reply_context=None,
        require_text: bool = True,
        defusing_context: str = "", # added defusing_context
        allow_local_fallback: bool = False, # allow fallback to local LLM
        format_retry_context: str = "",
        last_failure: str = "",
        force_local_fallback: bool = False,
        use_escalation_client: bool = False,
        reasoning_effort: str = None,
    ):
        # Chat hard-budget mặc định dùng rút gọn deterministic theo quota, tránh bắn
        # 5 worker DDS chỉ để rồi vẫn cắt prompt ở synthesis. Có thể bật lại DDS chat
        # bằng ERIS_CHAT_HARD_BUDGET_USE_DDS=1 khi thật sự muốn semantic distillation.
        use_chat_dds = (os.environ.get("ERIS_CHAT_HARD_BUDGET_USE_DDS", "0") or "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        # Direct chat accepts only real operational recovery context. Synthetic
        # Ni/Ti/Fe/appraisal/conversation labels are deliberately discarded here,
        # so another caller cannot accidentally rebuild the old marker wall.
        raw_ti_context = str(ti_context or "").strip()
        ti_context = (
            raw_ti_context
            if raw_ti_context.startswith(("speech_interrupt:", "speech_interrupt_reaction:", "Format repair:"))
            else ""
        )
        # V39: legacy chat path vẫn bỏ các marker Ni/Ti/Fe cũ, nhưng KHÔNG được
        # vứt AFFECTIVE_VOICE_STATE nếu caller đã project state sống vào đây.
        # Đây là data typed-ish về ERIS, không phải marker suy diễn user.
        raw_internal_state_context = str(internal_state_context or "").strip()
        internal_state_context = (
            raw_internal_state_context
            if raw_internal_state_context.startswith("AFFECTIVE_VOICE_STATE")
            else ""
        )
        ni_context = ""
        fe_context = ""
        social_context = str(social_context or "").strip()
        appraisal_context = ""
        conversation_context = ""
        defusing_context = ""
        if self._dds_enabled() and use_chat_dds:
            _distilled = self._maybe_distill_blocks(
                {
                    "memory_context": memory_context,
                    "conversation_context": conversation_context,
                    "grounding_context": grounding_context,
                    "ni_context": ni_context,
                    "ti_context": ti_context,
                },
                channel="chat",
            )
            memory_context = _distilled.get("memory_context", memory_context)
            conversation_context = _distilled.get("conversation_context", conversation_context)
            grounding_context = _distilled.get("grounding_context", grounding_context)
            ni_context = _distilled.get("ni_context", ni_context)
            ti_context = _distilled.get("ti_context", ti_context)

        style_block = self._build_chat_style_block()
        grounding_block = self._build_grounding_context_block(grounding_context)
        memory_block = self._build_memory_context_block(memory_context)

        # Prefix tĩnh đứng đầu và giữ nguyên giữa các turn để provider có thể
        # cache exact-prefix. Policy thay đổi theo lượt được tách sang block riêng.
        dynamic_policy_parts = []
        if format_retry_context or last_failure:
            retry_context = str(format_retry_context or "").strip()
            failure_flag = str(last_failure or "").strip()
            dynamic_policy_parts.append(
                f"{retry_context}\nRuntime failure flag: last_failure={failure_flag or 'format_error'}"
            )
        state_style_parts = [style_block]
        if ni_context:
            state_style_parts.append(f"Ni context:\n{ni_context}")
        if ti_context:
            state_style_parts.append(f"Ti context:\n{ti_context}")
        if social_context:
            state_style_parts.append(f"Relationship continuity (observed earlier; context only):\n{social_context}")
        if internal_state_context:
            state_style_parts.append(f"Live affective voice state (delivery authority only):\n{internal_state_context}")
        if defusing_context:
            state_style_parts.append(f"Defusing context:\n{defusing_context}")

        # K12/gpt-5.5 keeps full context; limited providers use block-first budgeting.
        completion_reserve = self._decision_completion_budget(
            channel="chat",
            reasoning_effort=reasoning_effort,
        )
        if self._client_prompt_unlimited(self.client):
            dynamic_policy_text = "\n\n".join(
                part for part in dynamic_policy_parts if str(part or "").strip()
            )
            state_style_text = "\n\n".join(part for part in state_style_parts if str(part or "").strip())
            sections = [
                f"[CACHEABLE_STATIC_PREFIX]\n{CACHEABLE_CHAT_PREFIX_V2}",
                f"[DYNAMIC_POLICY]\n{dynamic_policy_text}" if dynamic_policy_text else "",
                f"[STATE_STYLE]\n{state_style_text}" if state_style_text else "",
                f"[GROUNDING]\n{grounding_block}" if grounding_block else "",
                f"[MEMORY]\n{memory_block}" if memory_block else "",
                f"[CONVERSATION_CONTEXT]\n{conversation_context}" if conversation_context else "",
            ]
            messages = [
                {"role": "system", "content": "\n\n".join(part for part in sections if part)},
                *[dict(item) for item in (chat_history or []) if isinstance(item, dict)],
                {"role": "user", "content": user_message},
            ]
            self.logger.info(
                "[CHAT PROMPT K12] prefix=%s input=%d output=%d unlimited=1",
                cacheable_prefix_id(),
                estimate_tokens(messages),
                completion_reserve,
            )
        else:
            from llm.token_budget import ChatPromptBudget, assemble_budgeted_chat_messages

            chat_budget = ChatPromptBudget(output_reserve=completion_reserve)
            messages, budget_report = assemble_budgeted_chat_messages(
                system_rules_text=CACHEABLE_CHAT_PREFIX_V2,
                dynamic_policy_text="\n\n".join(
                    part for part in dynamic_policy_parts if str(part or "").strip()
                ),
                state_style_text="\n\n".join(part for part in state_style_parts if str(part or "").strip()),
                grounding_text=grounding_block,
                memory_text=memory_block,
                conversation_context=conversation_context,
                chat_history=chat_history,
                user_message=user_message,
                budget=chat_budget,
            )
            self.logger.info(
                "[CHAT PROMPT V2] prefix=%s input=%d/%d output=%d static=%d/%d dynamic=%d/%d "
                "history=%d/%d grounding=%d/%d memory=%d/%d state=%d/%d spare=%d",
                cacheable_prefix_id(),
                budget_report["assembled_input"],
                budget_report["input_limit"],
                budget_report["output_reserve"],
                budget_report["system_rules"],
                chat_budget.system_rules,
                budget_report["dynamic_policy"],
                chat_budget.dynamic_policy,
                budget_report["user_history"],
                chat_budget.user_history,
                budget_report["grounding"],
                chat_budget.grounding,
                budget_report["memory"],
                chat_budget.memory,
                budget_report["state_style"],
                chat_budget.state_style,
                budget_report["spare"],
            )

        raw = self._complete_decision(
            messages=messages,
            channel="chat",
            require_text=require_text,
            allow_local_fallback=allow_local_fallback,
            force_local_fallback=force_local_fallback,
            escalate=use_escalation_client,
            reasoning_effort=reasoning_effort,
        )
        return raw

    def generate_director_candidates(self, **_kwargs) -> list[dict]:
        """Retired compatibility endpoint: the main model owns the first valid Decision."""
        return []

    def run_ni_meta_critic(
        self,
        *,
        goals: list[str],
        telemetry_text: str,
        feature_vector: dict,
    ) -> str:
        telemetry_text = telemetry_text or ""
        try:
            features_json = json.dumps(feature_vector or {}, ensure_ascii=False)
        except TypeError:
            features_json = "{}"

        system_prompt = (
	            "Bạn là tầng meta-cognition Ni của ERIS. "
	            "Đọc telemetry và feature_vector rồi đánh giá attention drift. "
	            "Chỉ trả về JSON."
        )
        user_msg = (
            "Telemetry:\n"
            f"{telemetry_text}\n\n"
            "Feature vector JSON:\n"
            f"{features_json}\n\n"
            "Schema:\n"
            "{\n"
	            '  "drift_score": float trong [0.0, 1.0],\n'
	            '  "drift_level": một trong ["thap", "vua", "cao", "nghiem trong"],\n'
	            '  "explanation": chuỗi ngắn gọn\n'
            "}\n"
	            "Không đánh giá hiệu suất. Chỉ mô tả độ lệch chú ý quan sát được."
        )

        response = self.client.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.15,
            frequency_penalty=0.2,
            presence_penalty=0.0,
            max_tokens=256,
            response_format={"type": "json_object"},
            grammar=None,
        )
        return self._extract_content(response)
