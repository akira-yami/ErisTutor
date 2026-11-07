# -*- coding: utf-8 -*-
"""stage_performance_planner.py — biến yêu cầu "diễn" thành màn nói nhỏ.

LLM nhỏ thường hiểu được user bảo "cười đi" hoặc "nổi giận coi", nhưng nó chỉ
trả lời bằng chữ thường, hoặc khai ``nonverbal_cue=laugh_soft``.  Như vậy stage
biết "có cười", nhưng TTS không có âm cười để đọc.

Module này đứng ở tầng stage, sau Decision và trước TTS:

    user_text + decision_text
    -> PerformanceAct(audible_prefix, line, delivery, motion)
    -> display/tts có prefix nghe được
    -> memory_text bỏ prefix để không lưu rác diễn xuất

Đây là "diễn hành vi được yêu cầu", không phải claim ERIS có cảm xúc thật.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from runtime.performance_act_catalog import PERFORMANCE_ACT_CATALOG, PerformanceActSpec
from runtime.stage_transformers import StageTextSurfaces


@dataclass(frozen=True, slots=True)
class PerformanceAct:
    kind: str
    audible_prefix: str = ""
    fallback_line: str = ""
    delivery_hint: str = ""
    nonverbal_cue: str = ""
    stage_motion: str = ""
    prosody_note: str = ""

    def to_payload(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "audible_prefix": self.audible_prefix,
            "fallback_line": self.fallback_line,
            "delivery_hint": self.delivery_hint,
            "nonverbal_cue": self.nonverbal_cue,
            "stage_motion": self.stage_motion,
            "prosody_note": self.prosody_note,
        }


@dataclass(frozen=True, slots=True)
class PerformanceSurfaces:
    display_text: str
    tts_text: str
    memory_text: str
    delivery_hint: str
    nonverbal_cue: str
    stage_motion: str
    actions: tuple[str, ...]
    performance_act: PerformanceAct | None = None


_GENERIC_REPLY_RE = re.compile(
    r"^(?:"
    r"duoc|duoc roi|ok|oke|vang|ua|ừ|ừm|được|được rồi|"
    r"toi se|tôi sẽ|de toi|để tôi|thì|..."
    r")[\s.!?…]*$",
    re.I,
)


def _normalize_vi(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or "").lower())
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = raw.replace("đ", "d")
    raw = re.sub(r"[^a-z0-9\s]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _join_prefix(prefix: str, line: str) -> str:
    prefix = _clean_line(prefix)
    line = _clean_line(line)
    if not prefix:
        return line
    if not line:
        return prefix
    lower_line = line.lower()
    lower_prefix = prefix.lower()
    # Tránh nhân đôi nếu LLM đã tự trả "Hah... haha."
    if lower_line.startswith(lower_prefix):
        return line
    return f"{prefix} {line}".strip()


def _looks_generic_or_empty(text: str) -> bool:
    clean = _normalize_vi(text)
    if not clean:
        return True
    if len(clean) <= 16 and _GENERIC_REPLY_RE.match(clean):
        return True
    return clean in {"haha", "ha ha", "hahaha", "hmph", "haa"}


_LAUGH_REFUSAL_MARKERS = (
    "khong the cuoi", "khong cuoi", "khong muon cuoi", "khong nen cuoi",
    "khong the che gieu", "khong che gieu", "khong the mia mai", "khong mia mai",
    "cannot laugh", "can t laugh", "won t laugh", "will not laugh",
    "cannot mock", "won t mock", "will not mock",
)


def _act_conflicts_with_reply(act: PerformanceAct, text: str) -> bool:
    normalized = _normalize_vi(text)
    if act.kind not in {"playful_laugh", "requested_tease"}:
        return False
    return any(marker in normalized for marker in _LAUGH_REFUSAL_MARKERS)


def _merge_actions(*groups: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group or ():
            item = str(raw or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
    return tuple(out)


class PerformanceRequestPlanner:
    """Nhận ra yêu cầu biểu diễn và render thành text nghe được.

    Chỉ kích hoạt trên ``user_text``.  Autonomous/presence không có user_text thì
    không bị tự thêm tiếng cười hoặc tiếng thở.
    """

    def detect(self, user_text: str) -> PerformanceAct | None:
        normalized = _normalize_vi(user_text)
        if not normalized:
            return None

        for spec in PERFORMANCE_ACT_CATALOG:
            if self._matches_spec(normalized, spec):
                return self._act_from_spec(spec)

        return None

    def apply(
        self,
        surfaces: StageTextSurfaces,
        *,
        user_text: str = "",
        intent: str = "",
        delivery_hint: str = "",
        nonverbal_cue: str = "",
        stage_motion: str = "",
        extracted_actions: Iterable[str] = (),
    ) -> PerformanceSurfaces:
        del intent  # Giữ tham số để sau này route theo intent mà không đổi API.
        act = self.detect(user_text)
        if act is None:
            return PerformanceSurfaces(
                display_text=surfaces.display_text,
                tts_text=surfaces.tts_text,
                memory_text=surfaces.memory_text,
                delivery_hint=delivery_hint,
                nonverbal_cue=nonverbal_cue,
                stage_motion=stage_motion,
                actions=_merge_actions((nonverbal_cue,), extracted_actions),
                performance_act=None,
            )

        base_line = surfaces.tts_text or surfaces.display_text
        if _act_conflicts_with_reply(act, base_line):
            safe_cue = "" if str(nonverbal_cue or "").strip() in {"laugh_soft", "audible_laugh_soft"} else nonverbal_cue
            safe_actions = tuple(
                item for item in extracted_actions
                if str(item or "").strip() not in {"laugh_soft", "audible_laugh_soft"}
            )
            return PerformanceSurfaces(
                display_text=surfaces.display_text,
                tts_text=surfaces.tts_text,
                memory_text=surfaces.memory_text,
                delivery_hint=delivery_hint,
                nonverbal_cue=safe_cue,
                stage_motion=stage_motion,
                actions=_merge_actions((safe_cue,), safe_actions),
                performance_act=None,
            )
        if _looks_generic_or_empty(base_line):
            base_line = act.fallback_line

        display_text = _join_prefix(act.audible_prefix, base_line)
        tts_text = _join_prefix(act.audible_prefix, base_line)
        # Memory giữ ý nghĩa câu trả lời, không lưu tiếng "Hah... haha."
        memory_text = _clean_line(base_line)

        return PerformanceSurfaces(
            display_text=display_text,
            tts_text=tts_text,
            memory_text=memory_text,
            delivery_hint=delivery_hint or act.delivery_hint,
            nonverbal_cue=nonverbal_cue or act.nonverbal_cue,
            stage_motion=stage_motion or act.stage_motion,
            actions=_merge_actions((act.nonverbal_cue,), (nonverbal_cue,), extracted_actions),
            performance_act=act,
        )

    @staticmethod
    def _has(text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    def _matches_spec(self, normalized_text: str, spec: PerformanceActSpec) -> bool:
        if spec.requires_request_marker and not self._has_request_marker(normalized_text):
            # "gia vo noi gian" tự thân đã là request, dù user không nói "coi".
            if not any(token.startswith("gia vo") and token in normalized_text for token in spec.triggers):
                return False
        return self._has(normalized_text, spec.triggers)

    @staticmethod
    def _act_from_spec(spec: PerformanceActSpec) -> PerformanceAct:
        return PerformanceAct(
            kind=spec.kind,
            audible_prefix=spec.audible_prefix,
            fallback_line=spec.fallback_line,
            delivery_hint=spec.delivery_hint,
            nonverbal_cue=spec.nonverbal_cue,
            stage_motion=spec.stage_motion,
            prosody_note=spec.prosody_note,
        )

    @staticmethod
    def _has_request_marker(text: str) -> bool:
        return any(
            token in text
            for token in (
                "di",
                "coi",
                "xem",
                "nghe",
                "thu",
                "cho toi",
                "cho ng",
                "lam thu",
                "dien",
                "gia vo",
                "show",
            )
        )


__all__ = [
    "PerformanceAct",
    "PerformanceRequestPlanner",
    "PerformanceSurfaces",
]
