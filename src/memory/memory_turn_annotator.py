from __future__ import annotations

"""
memory_turn_annotator.py

Annotator biến turn thô thành TurnNode có speech_act/fact_mentions/semantic_tags.

Điểm thiết kế:
- Annotator KHÔNG trả lời memory.
- Annotator KHÔNG quyết định "user đang hỏi query nào".
- Nó chỉ gắn nhãn từng turn và dựng cạnh graph cơ bản.

Có pattern tiếng Việt trong file này, nhưng đây là linguistic annotation,
không phải keyword route. Nó không gọi nth_question hay answer_after.
"""

from dataclasses import dataclass
from typing import Any, Optional
import re
import unicodedata

from failure_meta import is_technical_failure_meta
from .memory_turn_graph import (
    FactMention,
    TurnEdge,
    TurnEdgeType,
    TurnGraph,
    TurnNode,
    TurnRole,
    TurnSpeechAct,
)


@dataclass(slots=True)
class TurnAnnotation:
    speech_act: TurnSpeechAct
    semantic_tags: tuple[str, ...]
    fact_mentions: tuple[FactMention, ...]
    confidence: float
    evidence: tuple[str, ...] = ()


class TurnAnnotator:
    """Annotator nhẹ, đủ tốt để build TurnGraph trước khi semantic layer sâu hơn."""

    def annotate(
        self,
        *,
        role: TurnRole,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
        previous_user_turn: Optional[TurnNode] = None,
    ) -> TurnAnnotation:
        metadata = metadata or {}
        norm = normalize_vi(text)
        tags: list[str] = []
        evidence: list[str] = []

        if self._is_service_fallback(norm, metadata):
            return TurnAnnotation(
                speech_act=TurnSpeechAct.FALLBACK,
                semantic_tags=("service_fallback",),
                fact_mentions=(),
                confidence=0.95,
                evidence=("fallback_marker",),
            )

        facts = self.extract_fact_mentions(role=role, text=text)

        if role == TurnRole.USER:
            if self._looks_like_correction(norm):
                tags.append("correction")
                evidence.append("correction_cue")
                act = TurnSpeechAct.CORRECTION
                conf = 0.84
            elif self._looks_like_command(norm):
                tags.append("command")
                evidence.append("command_cue")
                act = TurnSpeechAct.COMMAND
                conf = 0.78
            elif self._looks_like_question(text, norm):
                tags.append("question")
                evidence.append("question_cue")
                act = TurnSpeechAct.QUESTION
                conf = 0.84
            elif facts:
                tags.append("self_disclosure")
                evidence.append("fact_mention")
                act = TurnSpeechAct.FACT_DISCLOSURE
                conf = max(0.80, max(f.confidence for f in facts))
            else:
                act = TurnSpeechAct.STATEMENT
                conf = 0.62

        elif role == TurnRole.ASSISTANT:
            if metadata.get("repair") is True or "xin loi" in norm and "sua" in norm:
                act = TurnSpeechAct.REPAIR
                tags.append("repair")
                conf = 0.78
            elif is_technical_failure_meta(metadata.get("failure_meta")):
                act = TurnSpeechAct.FALLBACK
                tags.append("service_fallback")
                conf = 0.90
            elif metadata.get("intent") == "fallback_reply" or "van de duong truyen" in norm:
                act = TurnSpeechAct.FALLBACK
                tags.append("service_fallback")
                conf = 0.90
            else:
                # Assistant turn sau một user question thường là answer.
                # Nếu runtime có metadata intent/action thì dùng thêm.
                intent = str(metadata.get("intent", ""))
                if "answer" in intent or previous_user_turn and previous_user_turn.is_question:
                    act = TurnSpeechAct.ANSWER
                    tags.append("answer")
                    conf = 0.82
                else:
                    act = TurnSpeechAct.STATEMENT
                    conf = 0.62

        else:
            act = TurnSpeechAct.STATEMENT
            conf = 0.50

        return TurnAnnotation(
            speech_act=act,
            semantic_tags=tuple(dict.fromkeys(tags)),
            fact_mentions=tuple(facts),
            confidence=conf,
            evidence=tuple(evidence),
        )

    # ------------------------------------------------------------------
    # Edge building
    # ------------------------------------------------------------------

    def build_edges(self, graph: TurnGraph) -> None:
        """Dựng các cạnh ANSWERS/CORRECTS/REPEATS/STORES_FACT sau khi có nodes."""
        last_user_question: Optional[TurnNode] = None
        recent_by_norm: dict[str, TurnNode] = {}

        for node in graph.nodes:
            norm = normalize_vi(node.text)

            if node.is_question:
                last_user_question = node

            elif node.role == TurnRole.ASSISTANT and node.counts_as_answer and last_user_question:
                graph.add_edge(
                    TurnEdge(
                        type=TurnEdgeType.ANSWERS,
                        source_id=node.id,
                        target_id=last_user_question.id,
                        confidence=min(node.confidence, last_user_question.confidence, 0.90),
                        metadata={"rule": "annotator_nearest_question"},
                    )
                )
                last_user_question = None

            if node.speech_act == TurnSpeechAct.CORRECTION:
                target = self._find_recent_correctable(graph, before_index=node.index)
                if target:
                    graph.add_edge(
                        TurnEdge(
                            type=TurnEdgeType.CORRECTS,
                            source_id=node.id,
                            target_id=target.id,
                            confidence=0.72,
                            metadata={"rule": "recent_correctable_turn"},
                        )
                    )

            if norm in recent_by_norm and len(norm) > 8:
                graph.add_edge(
                    TurnEdge(
                        type=TurnEdgeType.REPEATS,
                        source_id=node.id,
                        target_id=recent_by_norm[norm].id,
                        confidence=0.88,
                        metadata={"rule": "normalized_text_match"},
                    )
                )
            recent_by_norm[norm] = node

            for fact in node.fact_mentions:
                graph.add_edge(
                    TurnEdge(
                        type=TurnEdgeType.STORES_FACT,
                        source_id=node.id,
                        target_id=node.id,
                        confidence=fact.confidence,
                        metadata={"fact_key": fact.key, "fact_value": fact.value},
                    )
                )

    def _find_recent_correctable(self, graph: TurnGraph, before_index: int) -> Optional[TurnNode]:
        for node in reversed(graph.nodes):
            if node.index >= before_index:
                continue
            if node.role == TurnRole.ASSISTANT and node.speech_act in {
                TurnSpeechAct.ANSWER,
                TurnSpeechAct.STATEMENT,
                TurnSpeechAct.REPAIR,
            }:
                return node
        return None

    # ------------------------------------------------------------------
    # Linguistic annotation helpers
    # ------------------------------------------------------------------

    def extract_fact_mentions(self, *, role: TurnRole, text: str) -> list[FactMention]:
        if role != TurnRole.USER:
            return []

        norm = normalize_vi(text)
        facts: list[FactMention] = []

        # Nhận cả "tôi tên là X" lẫn dạng chat rút gọn "tôi tên X".
        # Dạng rút gọn rất phổ biến trong test memory; nếu bỏ sót, TurnGraph
        # không biết đây là FACT_DISCLOSURE và recall có thể bám nhầm vào fact cũ.
        name_match = None
        name_pattern = ""
        for pattern_name, pattern in (
            ("pattern:toi_ten_la", r"\btoi\s+ten\s+la\s+([a-zA-Z0-9_ -]{1,40})"),
            ("pattern:toi_ten_short", r"\b(?:toi|minh|tui|tao|to|em)\s+ten\s+(?!(?:la|gi)\b)([a-zA-Z0-9_ -]{1,40})"),
        ):
            name_match = re.search(pattern, norm)
            if name_match:
                name_pattern = pattern_name
                break
        if name_match:
            value = name_match.group(1).strip().title()
            facts.append(
                FactMention(
                    key="user.name",
                    value=value,
                    confidence=0.92,
                    evidence=(name_pattern,),
                )
            )

        month_match = re.search(r"\b(?:toi\s+)?sinh\s+(?:vao\s+)?thang\s+([0-9]{1,2}|mot|hai|ba|bon|tu|nam|sau|bay|tam|chin|muoi|muoi mot|muoi hai)\b", norm)
        if month_match:
            month = parse_vietnamese_number(month_match.group(1))
            if month and 1 <= month <= 12:
                facts.append(
                    FactMention(
                        key="user.birth_month",
                        value=month,
                        confidence=0.90,
                        evidence=("pattern:sinh_thang",),
                    )
                )

        current_location = re.search(r"\btoi\s+(?:hien\s+)?(?:dang\s+)?o\s+(.{2,60})", norm)
        if current_location and "qua khu" not in norm and "tung" not in norm:
            value = current_location.group(1).strip()
            facts.append(
                FactMention(
                    key="user.current_location",
                    value=value,
                    confidence=0.82,
                    evidence=("pattern:hien_o",),
                )
            )

        past_location = re.search(r"\b(?:qua\s+khu\s+)?toi\s+(?:tung\s+)?(?:da\s+)?o\s+(.{2,60})", norm)
        if past_location and ("qua khu" in norm or "tung" in norm or "da o" in norm):
            value = past_location.group(1).strip()
            facts.append(
                FactMention(
                    key="user.past_location",
                    value=value,
                    confidence=0.80,
                    evidence=("pattern:past_location",),
                )
            )

        if "toi thich" in norm and ("tra loi" in norm or "noi ngan" in norm or "giai thich" in norm):
            facts.append(
                FactMention(
                    key="user.response_style",
                    value=text.strip(),
                    confidence=0.72,
                    evidence=("pattern:response_style",),
                )
            )

        return facts

    def _looks_like_question(self, raw: str, norm: str) -> bool:
        if "?" in raw:
            return True

        # Câu khai báo self-disclosure rõ ràng không được tính là question.
        statement_markers = (
            "toi ten la",
            "toi hien o",
            "toi dang o",
            "toi sinh",
            "qua khu toi",
            "toi tung",
            "san tien toi",
        )
        if any(marker in norm for marker in statement_markers):
            return False

        question_markers = (
            "la gi",
            "gi nhi",
            "khong",
            "chua",
            "bao nhieu",
            "luc nao",
            "khi nao",
            "o dau",
            "tai sao",
            "vi sao",
            "the nao",
            "nhu nao",
            "co phai",
            "dung khong",
            "ay nhi",
            "nhi",
        )
        return any(marker in norm for marker in question_markers)

    def _looks_like_correction(self, norm: str) -> bool:
        markers = (
            "khong phai",
            "y toi la",
            "sai roi",
            "khong dung",
            "thieu",
            "chua phai",
            "toi noi la",
            "doc lai",
        )
        return any(m in norm for m in markers)

    def _looks_like_command(self, norm: str) -> bool:
        markers = (
            "tao",
            "viet",
            "sua",
            "fix",
            "xoa",
            "them",
            "gui",
            "in ra",
            "phan tich",
        )
        # Chỉ xem là command nếu mở đầu hoặc có áp lực directive rõ.
        return any(norm.startswith(m + " ") or f" {m} " in norm for m in markers)

    def _is_service_fallback(self, norm: str, metadata: dict[str, Any]) -> bool:
        if metadata.get("is_service_fallback") is True:
            return True
        if is_technical_failure_meta(metadata.get("failure_meta")):
            return True
        intent = str(metadata.get("intent", "") or "")
        parse_mode = str(metadata.get("parse_mode", "") or "")
        # Fallback kỹ thuật không được tính là answer semantic. Nếu để nó nối
        # cạnh ANSWERS, lượt sau sẽ đọc "tôi bị lỗi định dạng" như nội dung thật.
        if intent in {"fallback_reply", "format_repair_fallback", "service_error"}:
            return True
        if parse_mode in {"format_repair_fallback", "service_error"}:
            return True
        return "van de duong truyen" in norm or "service_error" in norm or "loi dinh dang" in norm


# ---------------------------------------------------------------------------
# Normalize / number helpers
# ---------------------------------------------------------------------------


def normalize_vi(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9\s_.-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_VI_NUMBERS = {
    "mot": 1,
    "nhat": 1,
    "hai": 2,
    "ba": 3,
    "bon": 4,
    "tu": 4,
    "nam": 5,
    "sau": 6,
    "bay": 7,
    "tam": 8,
    "chin": 9,
    "muoi": 10,
    "muoi mot": 11,
    "muoi hai": 12,
}


def parse_vietnamese_number(text: str) -> Optional[int]:
    norm = normalize_vi(text)
    if norm.isdigit():
        return int(norm)
    return _VI_NUMBERS.get(norm)
