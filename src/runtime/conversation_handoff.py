# -*- coding: utf-8 -*-
"""Quan sát causal/provenance giữa các lượt hội thoại của ERIS V12.

V12 hard-cut quyền điều phối hội thoại khỏi handoff runtime.
Module này **không còn** quyết định ai phải nói tiếp, không giữ áp lực continuation,
không tạo repair action và không biến một open-loop thành nghĩa vụ public speech.

Nó chỉ giữ những bằng chứng quan sát được mà RootAgent có thể tham khảo:
- lượt user/assistant gần nhất và provenance của chúng;
- user có vừa đóng chủ đề một cách tường minh hay không;
- phản ứng hiện tại có khả năng liên quan đến câu ERIS vừa nói hay không;
- causal receipt của public move gần nhất;
- một số giả thuyết causal có confidence + evidence refs.

Ranh giới quan trọng:
    observation/evidence -> ConversationKernel/RootAgent -> quyết định semantic

Không có đường ngược lại kiểu:
    handoff -> speech candidate / next owner / continuation wake

Nhờ vậy module vẫn giữ được dữ liệu hữu ích cho coherence/repair, nhưng không trở
thành một "conversation director" thứ hai cạnh RootAgent.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
import re
import time
from typing import Any, Mapping, Sequence

from runtime.public_causal_receipt import PublicCausalReceipt, normalize_semantic_owner


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(text: Any, limit: int = 420) -> str:
    return " ".join(str(text or "").split())[: max(16, int(limit))]


def _norm(text: Any) -> str:
    return _compact(text, 2000).lower()


def _contains_any(text: Any, phrases: Sequence[str]) -> bool:
    lowered = _norm(text)
    return any(phrase in lowered for phrase in phrases)


_TOPIC_CLOSURE_RE = re.compile(
    r"(?:\b(?:đổi|chuyển)\s+(?:chủ đề|đề tài|chuyện|cái khác)\b"
    r"|\b(?:nói|bàn|chuyển sang)\s+(?:chuyện|cái|chủ đề)\s+khác\b"
    r"|\b(?:bỏ|cho qua|khép|dừng|thôi)\s+(?:qua\s+)?(?:vụ|chuyện|chủ đề|cái)?\s*(?:này|đó|ấy)?\b"
    r"|\b(?:coi như|cho)\s+(?:vụ|chuyện|cái)?\s*.+?\s+(?:đi vào dĩ vãng|qua rồi|xong rồi)\b"
    r"|\bđi vào dĩ vãng\b|\bđừng nhắc(?: lại)? nữa\b|\bkhông bàn(?: nữa)?\b"
    r"|\b(?:change|switch)\s+(?:the\s+)?topic\b|\bmove\s+on\b|\blet(?:'s| us)\s+(?:leave|drop)\s+(?:that|it)\b)",
    re.IGNORECASE,
)


def _explicit_topic_closure(text: Any) -> bool:
    cleaned = _compact(text, 1000)
    return bool(cleaned and _TOPIC_CLOSURE_RE.search(cleaned))


def _assistant_complexity(text: str) -> float:
    """Ước lượng *độ khó bề mặt* của câu trước để làm causal prior.

    Đây chỉ là feature quan sát. Nó không có quyền buộc RootAgent phải sửa câu,
    xin lỗi hay tiếp tục một chủ đề nào đó.
    """
    cleaned = _compact(text, 2400)
    if not cleaned:
        return 0.0
    words = re.findall(r"\w+", cleaned, flags=re.UNICODE)
    clauses = len(re.findall(r"[,;:—–-]", cleaned)) + cleaned.count(" và ") + cleaned.count(" nhưng ")
    length_score = _clamp01((len(words) - 18) / 48.0)
    clause_score = _clamp01(clauses / 8.0)
    abstract_markers = (
        "ý nghĩa", "bản chất", "hiện diện", "tự soi", "tự tham chiếu", "lăng kính",
        "mục đích", "nhị nguyên", "trừu tượng", "continuity", "identity", "semantic",
    )
    abstract_score = _clamp01(sum(1 for item in abstract_markers if item in cleaned.lower()) / 4.0)
    return _clamp01(0.48 * length_score + 0.30 * clause_score + 0.22 * abstract_score)


@dataclass(frozen=True, slots=True)
class CausalHypothesis:
    """Một giả thuyết quan sát, luôn kèm confidence và evidence refs."""

    kind: str
    confidence: float
    summary: str
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "confidence": round(_clamp01(self.confidence), 4),
            "summary": _compact(self.summary, 500),
            "evidence_refs": list(self.evidence_refs[:10]),
        }


@dataclass(frozen=True, slots=True)
class ConversationHandoff:
    """Snapshot quan sát public-safe; không chứa semantic control state."""

    revision: int = 0
    updated_at: float = 0.0
    last_user_text: str = ""
    last_assistant_text: str = ""
    last_user_turn_ref: str = ""
    last_assistant_turn_ref: str = ""
    last_user_turn_index: int = 0
    last_assistant_turn_index: int = 0

    # Provenance public gần nhất. Đây là evidence, không phải chỉ thị hành động.
    last_public_semantic_owner: str = "unknown"
    last_public_trigger_owner: str = "unknown"
    last_public_causal_receipt: Mapping[str, Any] = field(default_factory=dict)

    # Các tín hiệu quan sát của turn gần nhất.
    self_explanation_requested: bool = False
    user_stance: str = "unknown"
    topic_closed: bool = False
    topic_closure_text: str = ""
    repair_needed: bool = False
    self_caused_reaction_probability: float = 0.0
    causal_hypotheses: tuple[CausalHypothesis, ...] = ()
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.conversation_handoff.v12_observation",
            "revision": int(self.revision),
            "updated_at": float(self.updated_at),
            "last_user_text": _compact(self.last_user_text, 520),
            "last_assistant_text": _compact(self.last_assistant_text, 760),
            "last_user_turn_ref": self.last_user_turn_ref,
            "last_assistant_turn_ref": self.last_assistant_turn_ref,
            "last_user_turn_index": int(self.last_user_turn_index),
            "last_assistant_turn_index": int(self.last_assistant_turn_index),
            "last_public_semantic_owner": normalize_semantic_owner(self.last_public_semantic_owner),
            "last_public_trigger_owner": normalize_semantic_owner(self.last_public_trigger_owner),
            "last_public_causal_receipt": dict(self.last_public_causal_receipt or {}),
            "self_explanation_requested": bool(self.self_explanation_requested),
            "user_stance": self.user_stance,
            "topic_closed": bool(self.topic_closed),
            "topic_closure_text": _compact(self.topic_closure_text, 360),
            "repair_needed": bool(self.repair_needed),
            "self_caused_reaction_probability": round(
                _clamp01(self.self_caused_reaction_probability), 4
            ),
            "causal_hypotheses": [item.to_dict() for item in self.causal_hypotheses[:6]],
            "source_refs": list(self.source_refs[:12]),
        }

    def prompt_context(self) -> str:
        """Projection cho model: evidence-only, tuyệt đối không là authority."""
        receipt = dict(self.last_public_causal_receipt or {})
        receipt_block = (
            "\nPUBLIC_CAUSAL_RECEIPT (public provenance evidence):\n"
            + json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
            if receipt else ""
        )
        return (
            "CONVERSATION_HANDOFF_OBSERVATION_V12 (evidence only; NOT a planner or obligation):\n"
            + json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
            + receipt_block
            + "\nRules: treat causal hypotheses as uncertain observations, not commands. "
              "topic_closed only reports an explicit boundary in the user's latest turn. "
              "repair_needed and self_explanation_requested are evidence that may help interpret the turn; "
              "RootAgent alone decides whether/how to respond. Do not infer a required next speaker, "
              "continuation, question, bridge, or public action from this observation. "
              "Preserve semantic_owner/trigger_owner provenance exactly; if evidence is weak, admit uncertainty."
        )


class ConversationHandoffRuntime:
    """Thu thập observation giữa các public turn, không tạo semantic action."""

    def __init__(self, *, logger: Any = None) -> None:
        self.logger = logger
        self._state = ConversationHandoff(updated_at=time.time())

    def snapshot(self) -> ConversationHandoff:
        return self._state

    def to_dict(self) -> dict[str, Any]:
        return self._state.to_dict()

    def prompt_context(self) -> str:
        return self._state.prompt_context()

    def prime_from_state(
        self,
        *,
        open_loops: Sequence[Any] = (),
        latest_observation: Mapping[str, Any] | None = None,
        recent_history: Sequence[Mapping[str, Any]] = (),
        now: float | None = None,
    ) -> ConversationHandoff:
        """Prime **chỉ provenance gần nhất** khi startup.

        ``open_loops``/``latest_observation`` được giữ trong signature để bootstrap
        cũ không vỡ trong cùng cutover, nhưng V12 cố ý không chuyển chúng thành
        pending goal, next-owner hay continuation pressure. Semantic continuity phải
        đến từ conversation history/note và RootAgent.
        """
        del open_loops, latest_observation
        current = float(now if now is not None else time.time())
        last_user = ""
        last_assistant = ""
        for item in recent_history:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role", "") or "").lower()
            text = _compact(item.get("content", ""), 760)
            if role in {"user", "human"} and text:
                last_user = text
            elif role == "assistant" and text:
                last_assistant = text
        self._state = replace(
            self._state,
            revision=self._state.revision + 1,
            updated_at=current,
            last_user_text=last_user or self._state.last_user_text,
            last_assistant_text=last_assistant or self._state.last_assistant_text,
            source_refs=tuple(dict.fromkeys((*self._state.source_refs, "startup:handoff_observation"))),
        )
        self._log_state("startup_prime")
        return self._state

    def observe_user_turn(
        self,
        user_text: Any,
        *,
        turn_ref: str = "",
        turn_index: int = 0,
        previous_assistant_text: Any = "",
        previous_assistant_turn_ref: str = "",
        previous_assistant_turn_index: int = 0,
        now: float | None = None,
    ) -> ConversationHandoff:
        """Ghi nhận causal evidence của user turn hiện tại.

        Hàm có thể ước lượng *khả năng* user đang phản ứng với câu ERIS trước đó,
        nhưng không materialize repair candidate và không quyết định ERIS phải nói.
        """
        current = float(now if now is not None else time.time())
        user = _compact(user_text, 760)
        previous = _compact(previous_assistant_text or self._state.last_assistant_text, 1200)
        topic_closure = _explicit_topic_closure(user)

        confusion = _contains_any(user, (
            "không hiểu", "ko hiểu", "không hiểu gì", "khó hiểu", "ý gì", "nghĩa là gì",
            "em nói gì", "bạn nói gì", "what do you mean", "i don't understand", "dont understand",
        ))
        self_deprecation = _contains_any(user, (
            "tôi bị ngu", "mình bị ngu", "tôi ngu", "mình ngu", "tôi ngốc", "mình ngốc",
            "tôi dở", "mình dở", "stupid", "idiot", "i'm dumb", "im dumb",
        ))
        bored = _contains_any(user, (
            "nhàm", "chán", "nghe miết", "lặp lại", "đổi chủ đề", "chủ đề mới",
            "boring", "change topic", "same thing again",
        ))
        # P0 — Chỉ nhận diện lời sửa *cách Eris tham gia cuộc nói chuyện*,
        # không suy từ "bực" ra một hành động cố định. Đây là tín hiệu người
        # dùng đang bình luận lời Eris, không phải phân loại nhu cầu/cảm xúc.
        approach_feedback = bool(previous and _contains_any(user, (
            "sao cứ hỏi", "sao lại hỏi", "sao bạn hỏi", "sao em hỏi",
            "tại sao cứ hỏi", "đừng hỏi", "thôi hỏi", "hỏi kỹ thuật làm gì",
            "chỉ muốn kể mà", "chỉ đang kể mà", "tôi chỉ kể mà",
            "mình chỉ kể mà", "bạn cứ hỏi", "em cứ hỏi",
            "why do you keep asking", "stop asking", "i'm just venting",
        )))
        direct_assistant_reference = _contains_any(user, (
            "em nói", "bạn nói", "câu em", "câu bạn", "câu vừa", "lời em", "lời bạn",
            "you said", "what you said", "your question", "your sentence",
        ))
        self_explanation_requested = bool(
            self._state.last_public_causal_receipt
            and (
                _contains_any(user, (
                    "tại sao em", "sao em lại", "vì sao em", "tại sao bạn", "sao bạn lại",
                    "vì sao bạn", "em đang muốn nói gì", "bạn đang muốn nói gì",
                    "why did you", "why are you saying", "what were you trying to say",
                ))
                or (direct_assistant_reference and _contains_any(user, ("tại sao", "sao", "vì sao", "why")))
            )
        )

        reaction_signal = bool(confusion or self_deprecation or bored or approach_feedback)
        complexity = _assistant_complexity(previous)
        has_previous = bool(previous)
        explicit_external_cause = bool(
            re.search(r"\b(vì|do|tại)\s+(tôi|mình|cái|việc|code|bài|máy|app)\b", _norm(user))
            and not direct_assistant_reference
            and not confusion
        ) or _contains_any(user, (
            "làm sai", "bấm nhầm", "quên mất", "code lỗi", "máy lỗi", "app lỗi",
            "because i messed", "because i forgot", "i made a mistake",
        ))

        self_cause = 0.0
        if has_previous and direct_assistant_reference:
            self_cause = 0.96
        elif has_previous and confusion:
            self_cause = 0.90
        elif has_previous and self_deprecation:
            self_cause = 0.68 + 0.22 * complexity
        elif has_previous and bored:
            self_cause = 0.82
        elif has_previous and reaction_signal:
            self_cause = 0.62
        if explicit_external_cause and not direct_assistant_reference:
            self_cause *= 0.35
        self_cause = _clamp01(self_cause)

        evidence: list[str] = []
        if turn_ref:
            evidence.append(turn_ref)
        if previous_assistant_turn_ref:
            evidence.append(previous_assistant_turn_ref)
        elif previous_assistant_turn_index:
            evidence.append(f"session-turn:{int(previous_assistant_turn_index)}")
        if has_previous:
            evidence.append("discourse:immediately_previous_assistant_move")
        if complexity >= 0.55:
            evidence.append("discourse:previous_assistant_high_complexity")

        hypotheses: list[CausalHypothesis] = []
        if self_cause > 0.0:
            if approach_feedback:
                kind = "reaction_to_eris_approach"
                summary = "User đang nhận xét cách ERIS vừa tiếp cận; cần hiểu theo nguyên văn, không suy ra cách đáp bắt buộc."
            elif confusion:
                kind = "reaction_to_previous_eris_clarity"
                summary = "Phản ứng hiện tại có khả năng liên quan đến độ rõ của câu ERIS vừa nói."
            elif self_deprecation:
                kind = "reaction_to_previous_eris_move"
                summary = "User tự hạ thấp bản thân ngay sau lượt ERIS; có thể câu trước góp phần tạo phản ứng này."
            elif bored:
                kind = "reaction_to_previous_eris_repetition"
                summary = "Phản ứng hiện tại có khả năng nhắm vào mạch/nội dung ERIS vừa tiếp tục."
            else:
                kind = "reaction_to_previous_eris_move"
                summary = "Phản ứng hiện tại có quan hệ thời gian trực tiếp với lượt ERIS trước đó."
            hypotheses.append(CausalHypothesis(kind, self_cause, summary, tuple(evidence)))
        if reaction_signal:
            hypotheses.append(CausalHypothesis(
                "external_or_unstated_cause",
                _clamp01(1.0 - 0.72 * self_cause, 0.28),
                "Vẫn có khả năng phản ứng đến từ nguyên nhân khác chưa được nói ra.",
                tuple(ref for ref in evidence if ref == turn_ref),
            ))

        repair_needed = bool(
            confusion
            or approach_feedback
            or (self_deprecation and self_cause >= 0.72)
            or (bored and self_cause >= 0.70 and not topic_closure)
        )
        if confusion:
            stance = "confused"
        elif approach_feedback:
            stance = "approach_feedback"
        elif topic_closure:
            stance = "topic_closed"
        elif bored:
            stance = "topic_satiated"
        elif self_deprecation:
            stance = "self_deprecating_reaction"
        else:
            stance = "engaged"

        self._state = replace(
            self._state,
            revision=self._state.revision + 1,
            updated_at=current,
            last_user_text=user,
            last_user_turn_ref=str(turn_ref or ""),
            last_user_turn_index=max(0, int(turn_index or 0)),
            last_assistant_text=previous or self._state.last_assistant_text,
            last_assistant_turn_ref=str(previous_assistant_turn_ref or self._state.last_assistant_turn_ref),
            last_assistant_turn_index=max(
                int(previous_assistant_turn_index or 0), self._state.last_assistant_turn_index
            ),
            self_explanation_requested=self_explanation_requested,
            user_stance=stance,
            topic_closed=bool(topic_closure),
            topic_closure_text=(user if topic_closure else ""),
            repair_needed=repair_needed,
            self_caused_reaction_probability=self_cause,
            causal_hypotheses=tuple(hypotheses),
            source_refs=tuple(dict.fromkeys((*self._state.source_refs, *evidence))),
        )
        self._log_state("user_turn")
        return self._state

    def observe_assistant_commit(
        self,
        assistant_text: Any,
        *,
        turn_ref: str = "",
        turn_index: int = 0,
        speech_act: str = "",
        causal_receipt: PublicCausalReceipt | Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> ConversationHandoff:
        """Ghi public commit/provenance; không chuyển nó thành next-move obligation."""
        current = float(now if now is not None else time.time())
        text = _compact(assistant_text, 1200)
        if not text:
            return self._state
        prior = self._state
        if isinstance(causal_receipt, PublicCausalReceipt):
            receipt_payload = causal_receipt.to_dict()
        elif isinstance(causal_receipt, Mapping):
            receipt_payload = PublicCausalReceipt.from_mapping(causal_receipt).to_dict()
        else:
            receipt_payload = {}

        act = str(speech_act or "").strip().lower()
        repaired = prior.repair_needed and act in {
            "causal_repair", "clarify", "clarification", "repair", "self_clarification",
            "answer", "continuation",
        }
        explained_self = prior.self_explanation_requested and act in {
            "self_explanation", "causal_repair", "clarify", "clarification", "answer", "continuation",
        }

        self._state = replace(
            prior,
            revision=prior.revision + 1,
            updated_at=current,
            last_assistant_text=text,
            last_assistant_turn_ref=str(turn_ref or ""),
            last_assistant_turn_index=max(0, int(turn_index or 0)),
            repair_needed=False if repaired else prior.repair_needed,
            self_explanation_requested=False if explained_self else prior.self_explanation_requested,
            last_public_semantic_owner=(
                normalize_semantic_owner(receipt_payload.get("semantic_owner", "unknown"))
                if receipt_payload else prior.last_public_semantic_owner
            ),
            last_public_trigger_owner=(
                normalize_semantic_owner(receipt_payload.get("trigger_owner", "unknown"))
                if receipt_payload else prior.last_public_trigger_owner
            ),
            last_public_causal_receipt=(receipt_payload or prior.last_public_causal_receipt),
            self_caused_reaction_probability=(
                max(0.25, prior.self_caused_reaction_probability * 0.72)
                if repaired else prior.self_caused_reaction_probability
            ),
            source_refs=tuple(dict.fromkeys((*prior.source_refs, *((turn_ref,) if turn_ref else ())))),
        )
        self._log_state("assistant_commit")
        return self._state

    def _log_state(self, reason: str) -> None:
        logger = self.logger
        info = getattr(logger, "info", None)
        if not callable(info):
            return
        state = self._state
        try:
            info(
                "[conversation handoff v12] reason=%s rev=%s stance=%s repair=%s self_cause=%.2f topic_closed=%s",
                reason,
                state.revision,
                state.user_stance,
                int(state.repair_needed),
                state.self_caused_reaction_probability,
                int(state.topic_closed),
            )
        except Exception:
            pass


def ensure_conversation_handoff_runtime(host: Any) -> ConversationHandoffRuntime:
    runtime = getattr(host, "conversation_handoff_runtime", None)
    if isinstance(runtime, ConversationHandoffRuntime):
        return runtime
    runtime = ConversationHandoffRuntime(logger=getattr(host, "logger", None))
    setattr(host, "conversation_handoff_runtime", runtime)
    return runtime


__all__ = [
    "CausalHypothesis",
    "ConversationHandoff",
    "ConversationHandoffRuntime",
    "ensure_conversation_handoff_runtime",
]
