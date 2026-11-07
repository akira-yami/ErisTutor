# -*- coding: utf-8 -*-
"""Quyền phát ngôn cuối cùng cho một lượt public.

Trước đây Director, social surface, mirror fallback và vài guard đều có thể sửa
``Decision.text``. Kết quả là model viết đúng nhưng một tầng phía sau lại phát ra
câu khác. Module này tạo đúng một cửa khóa:

    validators/director/authority -> seal -> UI/TTS/memory chỉ đọc

Decision vẫn là dataclass mutable để tương thích code cũ. Vì vậy "khóa" ở đây
là snapshot + hash: nếu downstream sửa nhầm, ``verify_or_restore`` khôi phục câu
đã được duyệt. Cứng ở quyền hạn, mềm ở migration. Đó mới là elegant; đóng băng
cả object giữa một repo Python sống động chỉ để khoe purity sẽ khiến nửa runtime
vỡ vì những lý do rất triết học và hoàn toàn vô dụng.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


_SPEECH_FIELDS = (
    "text",
    "should_speak",
    "intent",
    "animation",
    "delivery_hint",
    "nonverbal_cue",
    "stage_motion",
    "preset_emotion",
    "speech_act",
)


def _snapshot(decision: Any) -> dict[str, Any]:
    return {name: getattr(decision, name, None) for name in _SPEECH_FIELDS}


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FinalSpeechVerification:
    valid: bool
    restored: bool = False
    expected_digest: str = ""
    actual_digest: str = ""


class FinalSpeechAuthority:
    """Khóa và xác minh các field có thể thay đổi lời/diễn xuất public."""

    def seal(self, decision: Any, *, owner: str = "final_speech_authority") -> Any:
        if decision is None:
            return decision
        payload = _snapshot(decision)
        digest = _digest(payload)
        decision.final_speech_locked = True
        decision.final_speech_digest = digest
        decision.final_speech_meta = {
            "owner": owner,
            "snapshot": payload,
            "digest": digest,
        }
        tags = tuple(getattr(decision, "reasoning_tags", ()) or ())
        decision.reasoning_tags = tuple(dict.fromkeys((*tags, "final_speech_locked")))
        return decision

    def verify_or_restore(self, decision: Any) -> FinalSpeechVerification:
        if decision is None or not bool(getattr(decision, "final_speech_locked", False)):
            return FinalSpeechVerification(valid=True)
        meta = getattr(decision, "final_speech_meta", None)
        if not isinstance(meta, dict) or not isinstance(meta.get("snapshot"), dict):
            return FinalSpeechVerification(valid=False)
        expected = str(meta.get("digest", "") or getattr(decision, "final_speech_digest", "") or "")
        actual = _digest(_snapshot(decision))
        if expected and actual == expected:
            return FinalSpeechVerification(valid=True, expected_digest=expected, actual_digest=actual)

        # Một tầng hậu kỳ đã sửa câu sau khi khóa. Khôi phục snapshot thay vì
        # phát câu không có authority. Không crash runtime chỉ vì module cũ chưa
        # kịp di cư; log/metric sẽ cho biết ai còn vi phạm contract.
        for name, value in dict(meta.get("snapshot") or {}).items():
            try:
                setattr(decision, name, value)
            except Exception:
                continue
        return FinalSpeechVerification(
            valid=False,
            restored=True,
            expected_digest=expected,
            actual_digest=actual,
        )


GLOBAL_FINAL_SPEECH_AUTHORITY = FinalSpeechAuthority()


__all__ = [
    "FinalSpeechAuthority",
    "FinalSpeechVerification",
    "GLOBAL_FINAL_SPEECH_AUTHORITY",
]
