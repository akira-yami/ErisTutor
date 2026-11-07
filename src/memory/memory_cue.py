from __future__ import annotations

"""
memory_cue.py
=============

MemoryCue là input chuẩn cho retrieval.

Không truyền raw query string lung tung nữa. Query string quá nghèo:
nó không biết user đang hỏi current screen, đang sửa sai, đang nhắc file,
hay đang muốn exact recall.

Cue gom:
- text hiện tại
- thread đang active
- semantic family
- anchors/file/module
- source grounding
- repair/affect/relation tags
- layer được phép dùng

Nói cách khác: memory retrieval đọc "bối cảnh có cấu trúc", không đọc một câu
và tự đoán mò như thầy bói nhìn log.
"""

from dataclasses import dataclass, field
from typing import Any
import re

try:
    from .memory_record import compact_text, normalize_text, tokenize, extract_anchors
except Exception:  # pragma: no cover - chạy standalone
    from memory_record import compact_text, normalize_text, tokenize, extract_anchors


@dataclass(slots=True)
class MemoryCue:
    query_text: str = ""

    user_text: str = ""
    dominant_thread: str = ""
    task_intent: str = ""
    semantic_family: str = ""

    anchors: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()

    current_source: str = "user_text"
    budget_mode: str = "normal"

    affect_tags: tuple[str, ...] = ()
    relation_tags: tuple[str, ...] = ()
    repair_tags: tuple[str, ...] = ()

    allowed_layers: tuple[str, ...] = ()
    forbidden_layers: tuple[str, ...] = ()

    needs_exact_recall: bool = False
    needs_current_evidence: bool = False
    privacy_sensitivity: float = 0.0

    def all_text(self) -> str:
        return compact_text(
            " ".join(
                part for part in (
                    self.query_text,
                    self.user_text,
                    self.dominant_thread,
                    self.task_intent,
                    self.semantic_family,
                    " ".join(self.anchors),
                    " ".join(self.files),
                    " ".join(self.modules),
                    " ".join(self.repair_tags),
                )
                if part
            ),
            limit=1000,
        )

    def token_set(self) -> set[str]:
        return set(tokenize(self.all_text()))

    def anchor_set(self) -> set[str]:
        return set(self.anchors) | set(self.files) | set(self.modules) | set(self.repair_tags)

    def layer_allowed(self, layer: str) -> bool:
        layer = str(layer or "")
        if layer in set(self.forbidden_layers):
            return False
        if self.allowed_layers:
            return layer in set(self.allowed_layers)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_text": self.query_text,
            "user_text": self.user_text,
            "dominant_thread": self.dominant_thread,
            "task_intent": self.task_intent,
            "semantic_family": self.semantic_family,
            "anchors": list(self.anchors),
            "entities": list(self.entities),
            "files": list(self.files),
            "modules": list(self.modules),
            "current_source": self.current_source,
            "budget_mode": self.budget_mode,
            "affect_tags": list(self.affect_tags),
            "relation_tags": list(self.relation_tags),
            "repair_tags": list(self.repair_tags),
            "allowed_layers": list(self.allowed_layers),
            "forbidden_layers": list(self.forbidden_layers),
            "needs_exact_recall": self.needs_exact_recall,
            "needs_current_evidence": self.needs_current_evidence,
            "privacy_sensitivity": round(float(self.privacy_sensitivity), 3),
        }


class MemoryCueBuilder:
    """Dựng cue từ query/frame mà không import CognitiveFrame cứng.

    Dùng best-effort getattr để module này sống được trong runtime cũ lẫn mới.
    """

    DEFAULT_LAYERS = ("repair", "procedural", "semantic", "relational", "affective_residue", "episodic")

    def from_query_or_frame(self, query: Any = "", *, frame: Any = None, budget_mode: str = "normal") -> MemoryCue:
        query_text = compact_text(getattr(query, "query", query) or "", limit=700)
        budget = str(getattr(query, "budget_mode", budget_mode) or budget_mode or "normal")
        parts: list[str] = [query_text]

        user_text = self._extract_user_text(frame)
        dominant_thread = self._extract_dominant_thread(frame)
        semantic_family = self._extract_semantic_family(frame)
        task_intent = self._extract_task_intent(frame)

        for part in (user_text, dominant_thread, semantic_family, task_intent):
            if part:
                parts.append(part)

        combined = compact_text(" ".join(parts), limit=1000)
        files = self._extract_files(combined)
        modules = self._extract_modules(combined)
        anchors = tuple(dict.fromkeys((*extract_anchors(combined), *files, *modules)))
        # Add coarse semantic anchors. This is not old keyword routing; it is a
        # weak retrieval cue so procedural rules like "technical_task" can be
        # recalled from a natural query containing "technical".
        norm = normalize_text(combined)
        coarse_anchors = []
        if any(tok in norm for tok in ("technical", "code", "debug", "module", "python", "guard", "prompt", "output")):
            coarse_anchors.extend(["technical_task", "post_generation_guard"])
        if any(tok in norm for tok in ("fake", "grounded", "bịa", "bia", "evidence", "nguon")):
            coarse_anchors.extend(["grounded_style", "source_grounding"])
        if any(tok in norm for tok in ("residue", "quiet", "night", "godot", "silent")):
            coarse_anchors.extend(["affective_residue", "silent_residue"])
        if coarse_anchors:
            anchors = tuple(dict.fromkeys((*anchors, *coarse_anchors)))

        needs_current = self._looks_like_current_evidence_request(combined)
        exact = self._looks_like_exact_recall_request(combined)

        allowed_layers = self._allowed_layers(
            needs_current_evidence=needs_current,
            needs_exact_recall=exact,
            task_intent=task_intent,
        )

        repair_tags = self._repair_tags(combined)
        affect_tags = self._affect_tags(frame)
        relation_tags = self._relation_tags(frame)

        return MemoryCue(
            query_text=query_text or combined,
            user_text=user_text,
            dominant_thread=dominant_thread,
            task_intent=task_intent,
            semantic_family=semantic_family,
            anchors=anchors,
            entities=(),
            files=files,
            modules=modules,
            current_source="frame" if frame is not None else "query",
            budget_mode=budget,
            affect_tags=affect_tags,
            relation_tags=relation_tags,
            repair_tags=repair_tags,
            allowed_layers=allowed_layers,
            forbidden_layers=("episodic", "semantic") if needs_current and not exact else (),
            needs_exact_recall=exact,
            needs_current_evidence=needs_current,
            privacy_sensitivity=self._privacy_sensitivity(combined),
        )

    def _extract_user_text(self, frame: Any) -> str:
        if frame is None:
            return ""
        for attr in ("user_text", "raw_user_text", "input_text"):
            value = compact_text(getattr(frame, attr, "") or "", limit=420)
            if value:
                return value
        evidence = getattr(frame, "evidence", []) or []
        for item in evidence:
            source = str(getattr(item, "source", "") or "")
            if source == "user_chat":
                return compact_text(getattr(item, "claim", item), limit=420)
        return ""

    def _extract_dominant_thread(self, frame: Any) -> str:
        if frame is None:
            return ""
        for attr in ("working_memory", "workspace", "salience", "meaning"):
            obj = getattr(frame, attr, None)
            if obj is None:
                continue
            for name in ("dominant_thread", "dominant_focus", "broadcast_reason", "meaning", "semantic_family"):
                value = compact_text(getattr(obj, name, "") or "", limit=260)
                if value:
                    return value
        return ""

    def _extract_semantic_family(self, frame: Any) -> str:
        if frame is None:
            return ""
        for attr in ("meaning", "speech_plan", "speech_opportunity"):
            obj = getattr(frame, attr, None)
            if obj is None:
                continue
            value = compact_text(getattr(obj, "semantic_family", "") or getattr(obj, "meaning_family", "") or "", limit=120)
            if value:
                return value
        return ""

    def _extract_task_intent(self, frame: Any) -> str:
        if frame is None:
            return ""
        for attr in ("agency", "speech_plan", "arbitration", "meaning"):
            obj = getattr(frame, attr, None)
            if obj is None:
                continue
            value = compact_text(
                getattr(obj, "intent", "")
                or getattr(obj, "speech_act", "")
                or getattr(obj, "winning_drive", "")
                or getattr(obj, "surface_intent", ""),
                limit=120,
            )
            if value:
                return value
        return ""

    def _extract_files(self, text: str) -> tuple[str, ...]:
        norm = normalize_text(text)
        files = re.findall(r"\b[\w\-]+\.(?:py|gd|cs|json|xml|md|txt|yaml|yml)\b", norm)
        return tuple(dict.fromkeys(files))

    def _extract_modules(self, text: str) -> tuple[str, ...]:
        norm = normalize_text(text)
        modules = re.findall(r"\b[a-z][a-z0-9]+_[a-z0-9_]+\b", norm)
        return tuple(dict.fromkeys(modules))

    def _looks_like_current_evidence_request(self, text: str) -> bool:
        norm = normalize_text(text)
        markers = ("dang mo", "man hinh", "screen", "file nay", "loi nay", "doan nay", "xem di", "hien tai")
        return any(marker in norm for marker in markers)

    def _looks_like_exact_recall_request(self, text: str) -> bool:
        norm = normalize_text(text)
        markers = ("toi vua noi", "ban vua noi", "cau thu", "bao nhieu cau", "nho lai", "hoi nay", "luc nay")
        return any(marker in norm for marker in markers)

    def _allowed_layers(self, *, needs_current_evidence: bool, needs_exact_recall: bool, task_intent: str) -> tuple[str, ...]:
        if needs_current_evidence and not needs_exact_recall:
            # Current evidence question không được để memory trả lời thay màn hình/file hiện tại.
            return ("repair", "procedural", "relational", "affective_residue")
        if needs_exact_recall:
            return ("episodic", "semantic", "repair", "procedural")
        if task_intent in {"repair", "technical_diagnosis", "answer", "practical_help"}:
            return self.DEFAULT_LAYERS
        return self.DEFAULT_LAYERS

    def _repair_tags(self, text: str) -> tuple[str, ...]:
        norm = normalize_text(text)
        tags: list[str] = []
        mapping = {
            "fallback": ("fallback", "keyword_rule"),
            "keyword_rule": ("keyword", "regex", "marker", "fallback"),
            "underacted": ("chua lam", "thieu", "lam di", "gui file"),
            "overread": ("suy dien", "overread", "doc tam"),
            "tone_mismatch": ("giong", "tone", "tho", "lam mau"),
        }
        for tag, markers in mapping.items():
            if any(marker in norm for marker in markers):
                tags.append(tag)
        return tuple(dict.fromkeys(tags))

    def _affect_tags(self, frame: Any) -> tuple[str, ...]:
        if frame is None:
            return ()
        obj = getattr(frame, "affect", None) or getattr(frame, "appraisal", None)
        if obj is None:
            return ()
        tags = []
        for name in ("tension", "curiosity", "restraint", "irritation", "tenderness", "fatigue"):
            try:
                if float(getattr(obj, name, 0.0) or 0.0) >= 0.45:
                    tags.append(name)
            except (TypeError, ValueError):
                pass
        return tuple(tags)

    def _relation_tags(self, frame: Any) -> tuple[str, ...]:
        if frame is None:
            return ()
        obj = getattr(frame, "relation", None) or getattr(frame, "relationship", None)
        if obj is None:
            return ()
        tags = []
        for name in ("repair_need", "rupture", "dignity_defense", "overread_correction", "technical_respect"):
            try:
                if float(getattr(obj, name, 0.0) or 0.0) >= 0.45:
                    tags.append(name)
            except (TypeError, ValueError):
                pass
        return tuple(tags)

    def _privacy_sensitivity(self, text: str) -> float:
        norm = normalize_text(text)
        markers = ("rieng", "private", "nhat ky", "email", "lich su", "tin nhan", "folder anh")
        return 0.75 if any(marker in norm for marker in markers) else 0.0
