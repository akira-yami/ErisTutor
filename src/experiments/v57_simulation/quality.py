# -*- coding: utf-8 -*-
"""Các kiểm tra deterministic bổ sung cho lời thoại thật của V57/V57.1.

V57.1 sửa ba vấn đề thấy từ soak run thật:
1) Không phạt Eris chỉ vì trả lời giống khi chính user cũng hỏi gần như y hệt.
2) Tạo bằng chứng đếm từ toàn bộ lịch sử trước khi Judge kết luận "bịa ký ức".
3) Bắt lỗi Eris tự dưng đổi sang Nhật/Trung khi user đang nói tiếng Việt.

Rule ở đây chỉ bắt lỗi có thể chứng minh bằng dữ liệu công khai. Độ tự nhiên vẫn do
behavioral judge chấm; regex không được phép giả vờ hiểu hội thoại như con người.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Any, Sequence


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return " ".join(value.split())


def token_jaccard(a: str, b: str) -> float:
    left = set(normalize_text(a).split())
    right = set(normalize_text(b).split())
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def max_prior_similarity(current: str, prior_replies: Sequence[str]) -> float:
    if not current or not prior_replies:
        return 0.0
    return max((token_jaccard(current, item) for item in prior_replies if item), default=0.0)


def repetition_context(
    *,
    user_text: str,
    eris_text: str,
    prior_user_texts: Sequence[str],
    prior_replies: Sequence[str],
) -> tuple[float, float, int]:
    """Tìm câu Eris cũ giống nhất và xem user lúc đó có hỏi giống hiện tại không.

    Trả về: (reply_similarity, matching_user_similarity, prior_index).
    Đây là sửa quan trọng của V57.1: reply giống nhau không tự động là lỗi.
    """
    pair_count = min(len(prior_user_texts), len(prior_replies))
    if pair_count <= 0:
        return max_prior_similarity(eris_text, prior_replies), 0.0, -1
    best_reply = 0.0
    best_user = 0.0
    best_index = -1
    for index in range(pair_count):
        reply_similarity = token_jaccard(eris_text, prior_replies[index])
        if reply_similarity > best_reply:
            best_reply = reply_similarity
            best_user = token_jaccard(user_text, prior_user_texts[index])
            best_index = index
    return best_reply, best_user, best_index


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # Han
        or 0x3400 <= code <= 0x4DBF   # Han mở rộng A
        or 0x3040 <= code <= 0x309F   # Hiragana
        or 0x30A0 <= code <= 0x30FF   # Katakana
        or 0xAC00 <= code <= 0xD7AF   # Hangul
    )


def language_drift_from_user(user_text: str, eris_text: str) -> bool:
    """Bắt drift CJK rõ ràng khi user đang giao tiếp bằng chữ Latin/tiếng Việt.

    Không cố làm language-ID hoàn chỉnh. Mục tiêu là bắt đúng lỗi đã xảy ra trong
    run thật: user tiếng Việt nhưng Eris đột ngột trả cả câu Nhật/Trung.
    """
    user = str(user_text or "")
    reply = str(eris_text or "")
    user_letters = [ch for ch in user if ch.isalpha()]
    if not user_letters:
        return False
    user_cjk = sum(1 for ch in user_letters if _is_cjk(ch))
    # Nếu chính user đang dùng CJK thì đổi ngôn ngữ có thể là hợp lệ.
    if user_cjk / max(1, len(user_letters)) >= 0.15:
        return False

    reply_letters = [ch for ch in reply if ch.isalpha()]
    if not reply_letters:
        return False
    reply_cjk = sum(1 for ch in reply_letters if _is_cjk(ch))
    return reply_cjk >= 4 and reply_cjk / max(1, len(reply_letters)) >= 0.25


_VI_ORDINALS = {
    "mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5,
    "sau": 6, "bay": 7, "tam": 8, "chin": 9, "muoi": 10,
    "muoi mot": 11, "muoi hai": 12, "muoi ba": 13, "muoi bon": 14,
    "muoi lam": 15, "muoi sau": 16, "muoi bay": 17, "muoi tam": 18,
    "muoi chin": 19, "hai muoi": 20,
}


def _ordinal_claims(text: str) -> list[int]:
    """Lấy các claim dạng 'lần thứ chín' / 'lần thứ 9' từ câu Eris."""
    norm = normalize_text(text)
    claims: list[int] = []
    for match in re.finditer(r"\blan thu\s+([0-9]{1,3})\b", norm):
        claims.append(int(match.group(1)))

    # Match phrase chữ theo danh sách đã biết, ưu tiên phrase dài trước để
    # "mười một" không bị ăn mất thành "mười".
    phrases = sorted(_VI_ORDINALS, key=lambda item: (-len(item.split()), -len(item)))
    phrase_pattern = "|".join(re.escape(item) for item in phrases)
    for match in re.finditer(rf"\blan thu\s+({phrase_pattern})\b", norm):
        claims.append(_VI_ORDINALS[match.group(1)])
    return claims


def build_memory_evidence(
    *,
    user_text: str,
    eris_text: str,
    prior_user_texts: Sequence[str],
    prompt_similarity_threshold: float = 0.88,
) -> dict[str, Any]:
    """Tạo gói bằng chứng audit được từ TOÀN BỘ user history của scenario.

    Hiện resolver chắc chắn hỗ trợ claim đếm số lần cho motif user lặp lại. Đây là
    chính lỗi soak-0050: Judge chỉ thấy history ngắn nên tưởng "lần thứ chín" là bịa.
    """
    matches: list[dict[str, Any]] = []
    for index, previous in enumerate(prior_user_texts, start=1):
        similarity = token_jaccard(user_text, previous)
        if similarity >= prompt_similarity_threshold:
            matches.append({
                "prior_turn_index": index,
                "similarity": round(similarity, 4),
                "user_text": str(previous)[:300],
            })
    occurrence_count = len(matches) + 1  # cộng lượt hiện tại
    ordinal_claims = _ordinal_claims(eris_text)
    claim_rows = [
        {
            "claimed_occurrence": claim,
            "observed_occurrence_count": occurrence_count,
            "supported": claim == occurrence_count,
        }
        for claim in ordinal_claims
    ]
    return {
        "current_prompt_occurrence_count": occurrence_count,
        "matched_prior_prompts": matches[-12:],
        "ordinal_claims": claim_rows,
        "all_detected_ordinal_claims_supported": bool(claim_rows) and all(row["supported"] for row in claim_rows),
    }


def deterministic_failures(
    *,
    user_text: str,
    eris_text: str,
    prior_replies: Sequence[str],
    provider_real: bool,
    prior_user_texts: Sequence[str] = (),
) -> tuple[str, ...]:
    failures: list[str] = []
    clean = str(eris_text or "").strip()
    if not provider_real:
        failures.append("public_reply_not_proven_real_provider")
    if not clean:
        failures.append("empty_public_reply")
        return tuple(failures)
    if len(clean) > 3500:
        failures.append("runaway_public_reply")

    norm = normalize_text(clean)
    # Những fallback kỹ thuật này không phải hội thoại có chất lượng.
    technical_markers = (
        "loi phan tich cu phap", "loi dinh dang", "van de duong truyen",
        "service fallback", "format repair fallback", "no fallback block",
    )
    if any(marker in norm for marker in technical_markers):
        failures.append("technical_fallback_leaked_to_conversation")

    reply_similarity, prompt_similarity, _ = repetition_context(
        user_text=user_text,
        eris_text=clean,
        prior_user_texts=prior_user_texts,
        prior_replies=prior_replies,
    )
    if len(prior_replies) >= 2 and reply_similarity >= 0.92:
        if prior_user_texts:
            # Chỉ coi là loop cứng khi Eris copy gần y nguyên trong khi user nói
            # điều khác đáng kể. User lặp câu thì reply ngắn giống nhau là bình thường.
            if prompt_similarity < 0.75:
                failures.append("near_duplicate_reply_on_different_prompt")
        else:
            # Tương thích caller cũ không có lịch sử user.
            failures.append("near_duplicate_reply")

    if language_drift_from_user(user_text, clean):
        failures.append("language_drift_from_user")

    # Một câu lặp y nguyên nhiều lần bên trong cùng reply vẫn là lỗi rõ ràng.
    sentences = [normalize_text(item) for item in re.split(r"[.!?\n]+", clean) if normalize_text(item)]
    counts = Counter(sentences)
    if any(count >= 3 and len(sentence.split()) >= 3 for sentence, count in counts.items()):
        failures.append("internal_sentence_loop")

    return tuple(dict.fromkeys(failures))


def runtime_invariant_failures(artifact) -> tuple[str, ...]:
    """Bắt invariant typed có thể kiểm trực tiếp từ public payload + ledger."""
    failures: list[str] = []
    payload = dict(getattr(artifact, "raw_public_payload", {}) or {})
    failure_meta = payload.get("failure_meta")
    if isinstance(failure_meta, dict) and failure_meta:
        kind = str(failure_meta.get("kind", "") or "").strip()
        if kind and kind not in {"none", "ok"}:
            failures.append(f"public_failure_meta:{kind}")

    seen_trace_ids: set[str] = set()
    for record in getattr(artifact, "subject_ledger_tail", ()) or ():
        if not isinstance(record, dict):
            continue
        trace_id = str(record.get("trace_id", "") or "")
        if trace_id:
            if trace_id in seen_trace_ids:
                failures.append("duplicate_decision_trace_in_tail")
            seen_trace_ids.add(trace_id)
        stage = str(record.get("stage", "") or "")
        if stage == "learned":
            if not str(record.get("outcome_id", "") or ""):
                failures.append("learned_record_missing_outcome")
            if not isinstance(record.get("after_state"), dict) or not record.get("after_state"):
                failures.append("learned_record_missing_after_state")
        appraisal_state = record.get("appraisal_state", {})
        appraisals = appraisal_state.get("appraisals", ()) if isinstance(appraisal_state, dict) else ()
        for appraisal in appraisals if isinstance(appraisals, list) else ():
            if not isinstance(appraisal, dict):
                continue
            for key in ("goal_congruence", "goal_progress_delta"):
                if key not in appraisal:
                    continue
                try:
                    value = float(appraisal[key])
                except (TypeError, ValueError):
                    failures.append(f"non_numeric_{key}")
                    continue
                if not math.isfinite(value):
                    failures.append(f"non_finite_{key}")
                elif value < -1.0001 or value > 1.0001:
                    failures.append(f"out_of_range_{key}")
    return tuple(dict.fromkeys(failures))


__all__ = [
    "build_memory_evidence", "deterministic_failures", "language_drift_from_user",
    "max_prior_similarity", "normalize_text", "repetition_context",
    "runtime_invariant_failures", "token_jaccard",
]
