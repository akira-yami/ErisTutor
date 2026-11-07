# -*- coding: utf-8 -*-
"""V69 - Consummation / residual tension cho biểu đạt tự chủ của Eris.

Module này KHÔNG cấm topic bằng keyword và KHÔNG cấp/thu quyền SPEAK sau khi
ExpressionCommitment đã tồn tại. Nó chỉ trả lời một câu sớm hơn trong lifecycle:

    "Điều Eris muốn nói này đã được nói/thỏa mãn đến đâu rồi?"

Một semantic concern có thể tiếp tục sống trong private cognition, trong khi nhu
cầu *biểu đạt công khai* của cùng concern đã gần hoàn tất. Evidence mới hoặc user
chủ động mở lại chủ đề có thể tạo residual tension trở lại.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from cortex.active_concern import clamp01, compact, semantic_overlap, semantic_tokens


EXPRESSION_CONSUMMATION_VERSION = "eris.expression_consummation.v70.rumination_reopen"


def _digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:16]


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _refs(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))[:48]


def _substantive_refs(values: Iterable[Any]) -> set[str]:
    """Chỉ coi provenance thế giới/user/memory là evidence mới.

    concern/material/urge ID chỉ là đường ống nội bộ. Nếu tính chúng là evidence,
    mỗi lần thought-engine đẻ một object ID mới sẽ vô tình "hồi sinh" ý cũ.
    """
    out: set[str] = set()
    for raw in values:
        ref = str(raw or "").strip()
        folded = ref.lower()
        if not ref or folded.startswith(("concern", "urge", "material", "mental.", "seed", "thought", "cogevt:")):
            continue
        if any(token in folded for token in (
            "turn", "user_event", "session_turn", "environment", "telemetry", "screen",
            "memory", "episode", "observation", "action", "goal", "runtime", "event:",
        )):
            out.add(ref)
    return out


_SHIFT_RE = re.compile(
    r"(?:\bđổi\s+(?:chủ đề|đề tài|cái khác)|\bchuyển\s+(?:chủ đề|đề tài)|\bnói\s+(?:cái|chuyện|chủ đề)\s+khác"
    r"|\brẽ\s+sang\s+(?:hướng|chuyện|chủ đề)\s+khác|\b(?:bỏ|cho qua|khép|dừng|thôi)\s+(?:qua\s+)?(?:vụ|chuyện|chủ đề|cái)?\s*(?:này|đó|ấy)?\b"
    r"|\bđi vào dĩ vãng\b|\bđừng nhắc(?: lại)? nữa\b|\bkhông bàn(?: nữa)?\b"
    r"|\bnew\s+topic\b|\bchange\s+(?:the\s+)?topic\b|\bmove\s+on\b|\blet(?:'s| us)\s+(?:leave|drop)\s+(?:that|it)\b)",
    re.IGNORECASE,
)
_REPETITION_FATIGUE_RE = re.compile(
    r"(?:\bnhàm\b|\bchán\b|\bnghe\s+miết\b|\blặp\s+(?:lại|hoài|mãi)\b|\bnói\s+hoài\b"
    r"|\brepetit(?:ive|ion)\b|\bsame\s+thing\b|\bboring\b)",
    re.IGNORECASE,
)
_ACK_RE = re.compile(
    r"^(?:à\s*)?(?:ừ|ờ|ok|okay|hiểu rồi|tôi hiểu rồi|biết rồi|got it|i understand|makes sense)[.!… ]*$",
    re.IGNORECASE,
)
_INVITE_RE = re.compile(
    r"(?:\b(?:nói thêm|kể thêm|tiếp đi|đào thêm)\b|\b(?:tell me more|go on|continue)\b)",
    re.IGNORECASE,
)
_CORRECTION_RE = re.compile(
    r"(?:\bkhông phải\b|\bý tôi\b|\bý anh\b|\bsai rồi\b|\bnhầm rồi\b|\bnot what i mean\b|\bthat's not\b)",
    re.IGNORECASE,
)
_SELF_SHIFT_RE = re.compile(
    r"(?:\b(?:tôi|em|mình)\s+(?:sẽ|thử|nên)\s+(?:đổi|rẽ|chuyển)\b"
    r"|\b(?:tôi|em|mình)\s+(?:sẽ\s+)?(?:nói|chọn)\s+(?:chuyện|cái|chủ đề)\s+khác\b"
    r"|\bi(?:'ll| will)\s+(?:switch|change|move)\b)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class SemanticConsummationTrace:
    trace_id: str
    semantic_anchor: str
    motive: str = "share"
    concern_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    expressive_completion: float = 0.0
    epistemic_completion: float = 0.0
    relational_completion: float = 0.0
    user_receptivity: float = 0.68
    habituation: float = 0.0
    reopen_credit: float = 0.0
    self_commitment_away: float = 0.0
    expressed_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_expressed_at: float = 0.0
    last_user_outcome_at: float = 0.0
    last_shift_request_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ConsummationAssessment:
    matched: bool = False
    trace_id: str = ""
    match_score: float = 0.0
    expressive_completion: float = 0.0
    epistemic_completion: float = 0.0
    relational_completion: float = 0.0
    user_receptivity: float = 0.68
    habituation: float = 0.0
    reopen_credit: float = 0.0
    novel_evidence: float = 0.0
    development_reopen_credit: float = 0.0
    semantic_satiation: float = 0.0
    residual_tension: float = 1.0
    inherited_satisfaction: float = 0.0
    self_commitment_conflict: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in tuple(data.items()):
            if isinstance(value, float):
                data[key] = round(value, 3)
        return data


class ExpressionConsummationRuntime:
    """Session-local semantic completion ledger.

    ConcernStore vẫn là owner của concern dài hạn. Ledger này chỉ giữ quan hệ
    giữa *semantic family* và việc nó đã được biểu đạt/đón nhận ra sao trong phiên.
    """

    def __init__(self, host: Any, *, logger: Any | None = None) -> None:
        self.host = host
        self.logger = logger or getattr(host, "logger", None)
        self._traces: dict[str, SemanticConsummationTrace] = {}
        self._last_public_trace_id = ""

    def _log(self, level: str, message: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(message, *args)

    def _find(
        self,
        content: str,
        concern_id: str = "",
        evidence_refs: Iterable[Any] = (),
    ) -> tuple[SemanticConsummationTrace | None, float]:
        text = compact(content, 520)
        incoming_refs = set(_refs(evidence_refs))
        incoming_tokens = semantic_tokens(text)
        best: SemanticConsummationTrace | None = None
        best_score = 0.0
        for trace in self._traces.values():
            semantic = semantic_overlap(text, trace.semantic_anchor)
            shared_tokens = len(incoming_tokens & semantic_tokens(trace.semantic_anchor))
            same_concern = bool(concern_id and concern_id in trace.concern_ids)
            shared_provenance = bool(incoming_refs & set(trace.evidence_refs))
            # Provenance nội bộ (concern/material source) được phép chứng minh
            # *identity semantic*, nhưng không được tính là evidence mới để reopen.
            score = max(
                semantic,
                0.96 if same_concern else 0.0,
                min(0.90, semantic + 0.30) if shared_provenance and shared_tokens >= 2 else 0.0,
            )
            if score > best_score:
                best, best_score = trace, score
        # Semantic family rộng hơn câu-paraphrase exact. Yêu cầu ít nhất 3 concept
        # token chung nếu không có concern/provenance identity để tránh gom "cùng vibe".
        if best is None:
            return None, 0.0
        shared_tokens = len(incoming_tokens & semantic_tokens(best.semantic_anchor))
        same_concern = bool(concern_id and concern_id in best.concern_ids)
        shared_provenance = bool(incoming_refs & set(best.evidence_refs))
        if not same_concern and not shared_provenance and (best_score < 0.32 or shared_tokens < 3):
            return None, 0.0
        return best, clamp01(best_score)

    @staticmethod
    def _effective(trace: SemanticConsummationTrace, *, now: float) -> tuple[float, float, float, float, float, float]:
        age = max(0.0, now - max(trace.updated_at, trace.last_expressed_at, trace.created_at))
        # Completion biểu đạt giảm rất chậm: nói một ý 5 phút trước vẫn là đã nói.
        expressive = clamp01(trace.expressive_completion * (0.5 ** (age / 3600.0)))
        epistemic = clamp01(trace.epistemic_completion * (0.5 ** (age / 5400.0)))
        relational = clamp01(trace.relational_completion * (0.5 ** (age / 2700.0)))
        habituation = clamp01(trace.habituation * (0.5 ** (age / 1200.0)))
        reopen = clamp01(trace.reopen_credit * (0.5 ** (age / 420.0)))
        # Negative receptivity phục hồi dần về baseline, không biến một lời "đổi chủ
        # đề" thành lệnh cấm vĩnh viễn.
        recover = clamp01(age / 1200.0)
        receptivity = clamp01(trace.user_receptivity + (0.68 - trace.user_receptivity) * recover)
        return expressive, epistemic, relational, receptivity, habituation, reopen

    def assess(
        self,
        *,
        content: str,
        concern_id: str = "",
        evidence_refs: Iterable[Any] = (),
        development_mode: str = "",
        development_score: float = 0.0,
        now: float | None = None,
    ) -> ConsummationAssessment:
        current = float(now or time.time())
        trace, match = self._find(content, concern_id, evidence_refs)
        if trace is None:
            return ConsummationAssessment()
        expressive, epistemic, relational, receptivity, habituation, reopen = self._effective(trace, now=current)
        old_refs = _substantive_refs(trace.evidence_refs)
        incoming = _substantive_refs(evidence_refs)
        novel = clamp01(len(incoming - old_refs) / max(1, len(incoming))) if incoming else 0.0
        external_reopen = max(reopen, 0.86 * novel)
        # V70: private rumination có thể tự tạo một insight đủ mới để biểu đạt
        # lại *một phần*, nhưng yếu hơn user/evidence reopen. Đây là chỗ "hơi ám
        # ảnh" được phép có tiếng nói mà không biến paraphrase thành zombie urge.
        mode = str(development_mode or "").strip().lower()
        dev_score = clamp01(development_score)
        development_reopen = clamp01((0.46 + 0.30 * dev_score) if mode and dev_score >= 0.62 else 0.0)
        # Self-commitment-away chỉ được nới nhẹ bởi insight nội bộ. User/evidence
        # reopen mới có quyền tháo gần hết cam kết. Nếu user vừa bảo "đổi chủ đề",
        # Eris vẫn có thể nghĩ nhưng không được lấy ám ảnh làm cớ dí user.
        commitment_release = 0.88 * external_reopen
        commitment_release = max(commitment_release, 0.36 * development_reopen)
        commitment = clamp01(trace.self_commitment_away * max(0.0, 1.0 - commitment_release))
        # Insight nội bộ chỉ "tiêu" khoảng 58% sức reopen vào satisfaction; phần
        # còn lại vẫn bị expressive completion cũ giữ lại. User/evidence thì mở mạnh.
        satisfaction_reopen = max(external_reopen, 0.58 * development_reopen)
        inherited = clamp01(expressive * (1.0 - 0.82 * satisfaction_reopen))
        # Self-commitment là lời Eris tự chịu trách nhiệm, không phải user ban.
        # Khi chưa có reopen hợp lệ, nó giữ semantic family ở trạng thái đã
        # consummate ngay cả khi completion tự decay theo thời gian.
        inherited = max(inherited, clamp01(0.96 * commitment))
        satiation = clamp01(0.62 * expressive + 0.38 * habituation)
        residual = clamp01(
            (1.0 - inherited)
            * (0.55 + 0.45 * receptivity)
            * (1.0 - 0.42 * habituation)
            + 0.30 * external_reopen
            # Internal insight chỉ được chạm public residual khi social state còn
            # mở. Đây chính là ranh giới "vẫn ám ảnh" và "đem ám ảnh dí user".
            + 0.16 * development_reopen * receptivity * (1.0 - commitment)
        )
        return ConsummationAssessment(
            matched=True,
            trace_id=trace.trace_id,
            match_score=match,
            expressive_completion=expressive,
            epistemic_completion=epistemic,
            relational_completion=relational,
            user_receptivity=receptivity,
            habituation=habituation,
            reopen_credit=reopen,
            novel_evidence=novel,
            development_reopen_credit=development_reopen,
            semantic_satiation=satiation,
            residual_tension=residual,
            inherited_satisfaction=inherited,
            self_commitment_conflict=commitment,
        )

    def note_expression(
        self,
        *,
        content: str,
        motive: str = "share",
        concern_id: str = "",
        evidence_refs: Iterable[Any] = (),
        public_text: str = "",
        development_mode: str = "",
        development_score: float = 0.0,
        now: float | None = None,
    ) -> SemanticConsummationTrace:
        current = float(now or time.time())
        trace, _ = self._find(content, concern_id, evidence_refs)
        if trace is None:
            anchor = compact(content, 520)
            trace = SemanticConsummationTrace(
                trace_id=f"consummation:{_digest(anchor)}",
                semantic_anchor=anchor,
                motive=str(motive or "share"),
                concern_ids=((concern_id,) if concern_id else ()),
                evidence_refs=_refs(evidence_refs),
                created_at=current,
                updated_at=current,
            )
            self._traces[trace.trace_id] = trace
        elif concern_id and concern_id not in trace.concern_ids:
            trace.concern_ids = tuple((*trace.concern_ids, concern_id))[-12:]
        trace.evidence_refs = _refs((*trace.evidence_refs, *tuple(evidence_refs or ())))
        trace.expressed_count += 1
        trace.last_expressed_at = current
        trace.updated_at = current
        # Nói ra chủ yếu hoàn tất nhu cầu biểu đạt. Nó không tự giả định rằng Eris
        # đã hiểu hết vấn đề (epistemic) hay user đã hiểu/đồng ý (relational).
        trace.expressive_completion = clamp01(max(trace.expressive_completion, 0.90) + 0.035 * max(0, trace.expressed_count - 1))
        base_epistemic = 0.18 if motive in {"explore", "clarify"} else 0.10
        dev_score = clamp01(development_score)
        # Một insight phát triển thật làm Eris hiểu concern thêm chút ít; nói lại
        # chính thesis không được tự tăng epistemic completion.
        epistemic_gain = 0.12 * dev_score if str(development_mode or "").strip() and dev_score >= 0.62 else 0.0
        trace.epistemic_completion = clamp01(max(trace.epistemic_completion, base_epistemic) + epistemic_gain)
        trace.relational_completion = clamp01(max(trace.relational_completion, 0.26))
        trace.habituation = clamp01(max(trace.habituation, 0.34) + 0.16 * max(0, trace.expressed_count - 1))
        trace.reopen_credit = clamp01(trace.reopen_credit * 0.30)
        self._last_public_trace_id = trace.trace_id
        if public_text:
            self.note_public_text(public_text, now=current, trace_id=trace.trace_id)
        self._log(
            "info",
            "[expression consummation] expressed trace=%s count=%s expressive=%.2f relational=%.2f habituation=%.2f",
            trace.trace_id, trace.expressed_count, trace.expressive_completion,
            trace.relational_completion, trace.habituation,
        )
        return trace

    def observe_user_turn(self, user_text: str, *, now: float | None = None) -> Mapping[str, Any]:
        current = float(now or time.time())
        trace = self._traces.get(self._last_public_trace_id)
        if trace is None or current - max(trace.last_expressed_at, trace.updated_at) > 900.0:
            return {"matched": False, "outcome": "none"}
        text = compact(user_text, 520)
        normalized = _norm(text)
        shift = bool(_SHIFT_RE.search(text))
        fatigue = bool(_REPETITION_FATIGUE_RE.search(text))
        correction = bool(_CORRECTION_RE.search(text))
        # Dấu hỏi tự nó không phải lời mời nói tiếp *chủ đề cũ*. Chỉ coi question
        # là reopen khi semantic thật sự chạm trace vừa nói; câu hỏi về chuyện mới
        # không được vô tình hồi sinh attractor cũ.
        question_reopens = "?" in text and semantic_overlap(text, trace.semantic_anchor) >= 0.22
        invitation = bool(_INVITE_RE.search(text) or question_reopens) and not shift and not fatigue
        acknowledgement = bool(_ACK_RE.match(normalized))

        outcome = "neutral"
        if shift or fatigue:
            outcome = "topic_satiated"
            trace.user_receptivity = min(trace.user_receptivity, 0.08)
            trace.relational_completion = max(trace.relational_completion, 0.94)
            trace.expressive_completion = max(trace.expressive_completion, 0.96)
            trace.habituation = max(trace.habituation, 0.94)
            trace.reopen_credit = 0.0
            trace.last_shift_request_at = current
        elif correction:
            outcome = "correction_reopens"
            trace.user_receptivity = max(trace.user_receptivity, 0.80)
            trace.epistemic_completion = min(trace.epistemic_completion, 0.25)
            trace.reopen_credit = max(trace.reopen_credit, 0.86)
            trace.self_commitment_away *= 0.25
        elif invitation:
            outcome = "user_reopens"
            trace.user_receptivity = max(trace.user_receptivity, 0.92)
            trace.relational_completion = min(trace.relational_completion, 0.42)
            trace.reopen_credit = max(trace.reopen_credit, 0.90)
            trace.self_commitment_away = 0.0
        elif acknowledgement:
            outcome = "acknowledged"
            trace.relational_completion = max(trace.relational_completion, 0.84)
            trace.expressive_completion = max(trace.expressive_completion, 0.94)
            trace.user_receptivity = min(trace.user_receptivity, 0.62)
            trace.habituation = max(trace.habituation, 0.58)
        else:
            trace.relational_completion = clamp01(trace.relational_completion + 0.06)
            trace.user_receptivity = clamp01(0.86 * trace.user_receptivity + 0.14 * 0.68)

        trace.last_user_outcome_at = current
        trace.updated_at = current
        self._log(
            "info",
            "[expression consummation] user outcome=%s trace=%s receptivity=%.2f expressive=%.2f relational=%.2f reopen=%.2f",
            outcome, trace.trace_id, trace.user_receptivity, trace.expressive_completion,
            trace.relational_completion, trace.reopen_credit,
        )
        return {"matched": True, "outcome": outcome, "trace_id": trace.trace_id}

    def note_public_text(
        self,
        public_text: str,
        *,
        now: float | None = None,
        trace_id: str = "",
    ) -> bool:
        """Biến lời tự cam kết của Eris thành state, không để nó chỉ là wording."""
        text = compact(public_text, 700)
        if not text or not _SELF_SHIFT_RE.search(text):
            return False
        trace = self._traces.get(str(trace_id or self._last_public_trace_id))
        if trace is None:
            return False
        current = float(now or time.time())
        # Chỉ gắn commitment-away khi đang có dấu hiệu topic đã no/được user yêu
        # cầu chuyển. Tránh diễn giải mọi câu "tôi sẽ đổi cách nói" thành bỏ topic.
        if trace.last_shift_request_at <= 0.0 or current - trace.last_shift_request_at > 300.0:
            return False
        trace.self_commitment_away = max(trace.self_commitment_away, 0.88)
        trace.updated_at = current
        self._log(
            "info",
            "[expression consummation] self-commitment trace=%s kind=shift_topic strength=%.2f",
            trace.trace_id, trace.self_commitment_away,
        )
        return True

    def snapshot(self, *, now: float | None = None, limit: int = 12) -> tuple[dict[str, Any], ...]:
        current = float(now or time.time())
        rows: list[dict[str, Any]] = []
        for trace in sorted(self._traces.values(), key=lambda item: item.updated_at, reverse=True)[:max(0, int(limit))]:
            assessment = self.assess(
                content=trace.semantic_anchor,
                concern_id=(trace.concern_ids[-1] if trace.concern_ids else ""),
                evidence_refs=trace.evidence_refs,
                now=current,
            )
            rows.append({
                "trace_id": trace.trace_id,
                "semantic_anchor": compact(trace.semantic_anchor, 220),
                "motive": trace.motive,
                "expressed_count": trace.expressed_count,
                **assessment.to_dict(),
            })
        return tuple(rows)


__all__ = [
    "EXPRESSION_CONSUMMATION_VERSION",
    "SemanticConsummationTrace",
    "ConsummationAssessment",
    "ExpressionConsummationRuntime",
]
