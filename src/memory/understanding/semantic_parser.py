from __future__ import annotations

"""
memory.understanding.semantic_parser
====================================

Semantic parser cho memory intake.

Đường đẹp cần dùng model.gguf:
    user_text -> model trả JSON frames -> StructuredFrameParser verify -> MeaningFrame

Đường fallback trong file này chỉ là phanh an toàn khi chưa gắn model. Nó cố tình
nghèo, tập trung, và trả confidence vừa phải. Fallback không được tự tin như thần
linh hạng rẻ. Khi bạn nối model.gguf, hãy dùng `ModelSemanticParser` với một
callable trả dict JSON.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Iterable, Mapping, Protocol

from .entity_linker import clean_surface, link_entity, normalize, strip_accents
from .meaning_frame import EntityRef, MeaningFrame, MeaningParseResult
from .polarity_temporal import analyse_surface, split_clauses
from ..name_validation import is_plausible_user_name


class SemanticParser(Protocol):
    """Protocol chung để runtime có thể thay fallback bằng model.gguf."""

    def parse(self, text: str, *, structured: Mapping[str, Any] | None = None) -> MeaningParseResult:
        ...


@dataclass(slots=True)
class ModelSemanticParser:
    """Adapter cho model/LLM parser bên ngoài.

    `parse_callable` là hàm của bạn. Nó nhận text và trả dict/list đã parse.
    File này không biết backend inference. Nó chỉ biết verify output.

    Expected JSON gợi ý:
    {
      "frames": [
        {
          "frame_type": "location",
          "relation": "hometown",
          "subject": "user",
          "target": {"text": "Hà Tĩnh", "kind": "location"},
          "polarity": "asserted",
          "time_scope": "stable",
          "certainty": 0.86,
          "evidence_span": "quê tôi là Hà Tĩnh"
        }
      ]
    }
    """

    parse_callable: Callable[[str], Mapping[str, Any] | list[Any] | None]
    fallback: SemanticParser | None = None

    def parse(self, text: str, *, structured: Mapping[str, Any] | None = None) -> MeaningParseResult:
        payload: Mapping[str, Any] | list[Any] | None = structured
        if payload is None:
            try:
                payload = self.parse_callable(text)
            except Exception as exc:  # model lỗi thì không làm chết memory intake
                if self.fallback is not None:
                    result = self.fallback.parse(text)
                    return MeaningParseResult(frames=result.frames, source="model_failed_fallback", confidence=result.confidence, diagnostics=(f"model_error:{type(exc).__name__}", *result.diagnostics))
                return MeaningParseResult(source="model_failed", confidence=0.0, diagnostics=(f"model_error:{type(exc).__name__}",))
        return structured_frames_to_result(text, payload, source="model_semantic_parser")


@dataclass(slots=True)
class StructuredFrameParser:
    """Parser chỉ verify JSON structured đã có sẵn."""

    def parse(self, text: str, *, structured: Mapping[str, Any] | None = None) -> MeaningParseResult:
        if structured is None:
            return MeaningParseResult(source="structured_missing", confidence=0.0, diagnostics=("missing_structured_payload",))
        return structured_frames_to_result(text, structured, source="structured_payload")


@dataclass(slots=True)
class FallbackSurfaceSemanticParser:
    """Fallback rất nhỏ khi chưa có model.gguf.

    Nó không cố hiểu sâu. Nó chỉ nhận ra vài frame rất phổ thông bằng các detector
    tập trung. Quan trọng: detector sinh MeaningFrame, không sinh SemanticClaim và
    không quyết định lưu lâu.
    """

    def parse(self, text: str, *, structured: Mapping[str, Any] | None = None) -> MeaningParseResult:
        if structured:
            result = structured_frames_to_result(text, structured, source="structured_payload")
            if result.frames:
                return result

        surface = analyse_surface(text)
        if not surface.raw:
            return MeaningParseResult(source="fallback_surface", confidence=0.0, diagnostics=("empty",))
        if surface.is_greeting_like:
            return MeaningParseResult(
                frames=(MeaningFrame(frame_type="session_event", relation="greeting", certainty=0.85, time_scope="session", stability="session", evidence_span=surface.raw, warnings=("not_long_term",)),),
                source="fallback_surface",
                confidence=0.60,
                diagnostics=surface.evidence,
            )
        normalized_question = normalize(surface.raw)
        if surface.is_question and "ten gi" in normalized_question and any(token in normalized_question for token in ("ban nho", "co nho", "nho toi")):
            return MeaningParseResult(
                frames=(
                    MeaningFrame(
                        frame_type="session_event",
                        relation="asked_about",
                        target=link_entity("user.name", hinted_kind="memory_query"),
                        polarity="asserted",
                        modality="observation",
                        time_scope="session",
                        scope="self",
                        certainty=0.84,
                        stability="session",
                        evidence_span=surface.raw,
                        warnings=("not_long_term",),
                    ),
                ),
                source="fallback_surface",
                confidence=0.62,
                diagnostics=surface.evidence,
            )
        if surface.is_question and not surface.has_explicit_memory_request:
            return MeaningParseResult(source="fallback_surface", confidence=0.0, diagnostics=("question_not_commit", *surface.evidence))

        frames: list[MeaningFrame] = []
        for clause in split_clauses(surface.raw) or [surface.raw]:
            frames.extend(_parse_clause_as_frames(clause, surface))

        if not frames and surface.has_explicit_memory_request:
            frames.append(MeaningFrame(
                frame_type="session_event",
                relation="asked_about" if surface.is_question else "remember_unresolved",
                target=link_entity("user.name", hinted_kind="memory_query") if surface.is_question and "ten gi" in normalized_question else link_entity(surface.raw, hinted_kind="topic"),
                polarity="asserted",
                modality="observation" if surface.is_question else "memory",
                time_scope="session" if surface.is_question else "stable",
                scope="self",
                certainty=0.66,
                stability="session" if surface.is_question else "medium",
                evidence_span=surface.raw,
                warnings=("not_long_term",) if surface.is_question else ("unresolved_memory_request",),
            ))

        confidence = max((f.certainty for f in frames), default=0.0)
        return MeaningParseResult(frames=tuple(frames), source="fallback_surface", confidence=confidence, diagnostics=surface.evidence)


def structured_frames_to_result(text: str, payload: Mapping[str, Any] | list[Any] | None, *, source: str) -> MeaningParseResult:
    """Verify structured JSON thành MeaningFrame.

    Hỗ trợ cả {"frames": [...]} lẫn list trực tiếp. Những frame thiếu target vẫn
    được giữ nếu loại frame là feedback/instruction/session_event, còn lại bị hạ
    confidence và gắn warning.
    """

    if payload is None:
        return MeaningParseResult(source=source, confidence=0.0, diagnostics=("empty_payload",))
    raw_frames = payload.get("frames") if isinstance(payload, Mapping) else payload
    if not isinstance(raw_frames, list):
        # Tương thích bản cũ nếu model trả {claims:[...]}; coi mỗi claim như frame thô.
        raw_claims = payload.get("claims") if isinstance(payload, Mapping) else None
        if isinstance(raw_claims, list):
            raw_frames = [_claim_like_to_frame(item) for item in raw_claims if isinstance(item, Mapping)]
        else:
            return MeaningParseResult(source=source, confidence=0.0, diagnostics=("frames_not_list",))

    frames: list[MeaningFrame] = []
    diagnostics: list[str] = []
    for item in raw_frames:
        if not isinstance(item, Mapping):
            diagnostics.append("skip_non_mapping_frame")
            continue
        frame = _frame_from_mapping(item, fallback_evidence=text)
        if frame is None:
            diagnostics.append("skip_invalid_frame")
            continue
        frames.append(frame)

    confidence = max((f.certainty for f in frames), default=0.0)
    return MeaningParseResult(frames=tuple(frames), source=source, confidence=confidence, diagnostics=tuple(diagnostics))


def _frame_from_mapping(item: Mapping[str, Any], *, fallback_evidence: str) -> MeaningFrame | None:
    relation = str(item.get("relation") or item.get("predicate") or "").strip()
    if not relation:
        return None
    target_raw = item.get("target")
    target: EntityRef | None = None
    if isinstance(target_raw, Mapping):
        target = EntityRef.from_dict(target_raw)
    else:
        obj = item.get("object") or item.get("object_text") or item.get("value")
        if obj not in (None, ""):
            target = link_entity(obj, hinted_kind=str(item.get("object_type") or item.get("target_kind") or "unknown"))
    certainty = _clamp01(item.get("certainty", item.get("confidence", 0.55)))
    warnings: list[str] = [str(x) for x in item.get("warnings") or () if str(x).strip()]
    if target is None and str(item.get("frame_type") or "") not in {"feedback", "instruction", "session_event", "memory_request"}:
        warnings.append("missing_target")
        certainty = min(certainty, 0.45)
    return MeaningFrame(
        frame_type=str(item.get("frame_type") or _infer_frame_type(relation)),  # type: ignore[arg-type]
        relation=relation,
        subject=str(item.get("subject") or "user"),
        target=target,
        polarity=str(item.get("polarity") or item.get("assertion") or "asserted"),  # type: ignore[arg-type]
        modality=str(item.get("modality") or "fact"),
        time_scope=str(item.get("time_scope") or item.get("temporal") or "unknown"),  # type: ignore[arg-type]
        scope=str(item.get("scope") or "self"),
        certainty=certainty,
        stability=str(item.get("stability") or "unknown"),
        evidence_span=str(item.get("evidence_span") or item.get("evidence_text") or fallback_evidence),
        source=str(item.get("source") or "user_text"),
        # Giữ raw dict để ClaimBuilder đọc schema mới (memory_type, storage_target, decay, ...)
        # khi Gemini dùng AI classification thay vì schema frame cũ. Gắn luôn
        # evidence gốc; nếu không, model có thể biến claim tóm tắt thành "bằng chứng"
        # rồi identity guard không còn biết user thực sự đã nói gì.
        slots={
            **dict(item.get("slots") or {}),
            "_raw": {
                **dict(item),
                "_source_evidence": str(item.get("evidence_span") or item.get("evidence_text") or fallback_evidence),
            },
        },
        warnings=tuple(warnings),
    )


def _claim_like_to_frame(item: Mapping[str, Any]) -> dict[str, Any]:
    predicate = str(item.get("predicate") or "")
    return {
        "frame_type": _infer_frame_type(predicate),
        "relation": predicate,
        "subject": item.get("subject", "user"),
        "target": {"text": item.get("object") or item.get("object_text") or "", "kind": item.get("object_type") or "unknown"},
        "polarity": item.get("assertion", "asserted"),
        "modality": item.get("modality", "fact"),
        "time_scope": item.get("temporal", "unknown"),
        "certainty": item.get("confidence", 0.55),
        "evidence_span": item.get("evidence_text", ""),
    }


def _parse_clause_as_frames(clause: str, surface: Any) -> list[MeaningFrame]:
    """Fallback detector tập trung.

    Đây là phần còn lexical, nhưng chỉ làm fallback và chỉ sinh MeaningFrame. Nếu
    bạn nối model.gguf, function này gần như không còn chạy trong hot path.
    """

    raw = clean_surface(clause)
    norm = normalize(raw)
    if not norm:
        return []
    frames: list[MeaningFrame] = []

    # Các pattern ở đây là frame-level, không phải predicate final.
    # Bản cũ chỉ bắt "tôi tên là X", nên câu đời thường "tôi tên Hòa" hoặc
    # correction "thực ra tên thật tôi là Minh" bị rơi xuống unknown. Parser fallback
    # chỉ mở rộng MeaningFrame; claim/retention vẫn do tầng sau phán xét.
    identity = _extract_identity_name(raw)
    if identity:
        identity_value, is_correction = identity
        frames.append(_mk_frame(
            "identity",
            "name",
            identity_value,
            "person_name",
            raw,
            0.92 if is_correction else 0.88,
            "stable",
            "fact",
            surface,
            polarity="corrected" if is_correction else None,
        ))

    nickname = _extract_identity_nickname(raw)
    if nickname:
        # Biệt danh là fact identity riêng, KHÔNG đè tên thật. "tôi tên Hoà" và
        # "biệt danh của tôi là Gtvkun" phải sống song song trong semantic memory.
        frames.append(_mk_frame(
            "identity",
            "nickname",
            nickname,
            "person_name",
            raw,
            0.88,
            "stable",
            "fact",
            surface,
        ))

    gender_identity = _extract_gender_identity(raw)
    if gender_identity:
        frames.append(_mk_frame(
            "identity",
            "gender_identity",
            gender_identity,
            "gender_identity",
            raw,
            0.82,
            "stable",
            "fact",
            surface,
        ))

    birth_year = _extract_year(norm)
    if birth_year is not None and _has_self(norm):
        frames.append(_mk_frame("identity", "birth_year", str(birth_year), "year", raw, 0.84, "stable", "fact", surface))

    hometown = _extract_after(raw, (("que toi la", "hometown"), ("toi que o", "hometown"), ("toi que", "hometown")))
    if hometown:
        frames.append(_mk_frame("location", "hometown", hometown, "location", raw, 0.78, "stable", "fact", surface))

    living = _extract_after(raw, (("toi dang song o", "living_location"), ("toi song o", "living_location"), ("noi o cua toi la", "living_location")))
    if living:
        frames.append(_mk_frame("location", "living_location", living, "location", raw, 0.76, "current", "fact", surface, stability="medium"))

    current = _extract_after(raw, (("toi dang o", "current_location"), ("toi hien o", "current_location"), ("hien tai toi o", "current_location")))
    if current:
        frames.append(_mk_frame("location", "current_location", current, "location", raw, 0.70, "current", "observation", surface, stability="transient"))

    pref = _extract_preference(norm, raw)
    if pref:
        target, kind = pref
        frames.append(_mk_frame("preference", "likes", target, kind, raw, 0.66 if kind == "unknown" else 0.72, "stable", "preference", surface, stability="medium"))

    study_field = _extract_study_field(raw)
    if study_field:
        # "Tôi đang học IT" là profile học tập/ngành học, không phải preference.
        # Đưa thành frame project/profile để ontology lưu đúng fact_key thay vì nhét
        # bừa vào general_preference rồi sau này recall sai như một trò xiếc token.
        frames.append(_mk_frame("project", "study_field", study_field, "study_field", raw, 0.78, "current", "fact", surface, stability="medium"))

    if _looks_like_style_instruction(norm):
        frames.append(MeaningFrame(
            frame_type="instruction",
            relation="response_style",
            target=link_entity(raw, hinted_kind="style_rule"),
            polarity=surface.polarity,
            modality="instruction",
            time_scope="stable" if surface.time_scope == "unknown" else surface.time_scope,
            scope="self",
            certainty=0.70,
            stability="medium",
            evidence_span=raw,
            warnings=("fallback_style_instruction",),
        ))

    if _looks_like_feedback(norm):
        frames.append(MeaningFrame(
            frame_type="feedback",
            relation="tone_feedback",
            target=link_entity(raw, hinted_kind="style_rule"),
            polarity="asserted",
            modality="feedback",
            time_scope="stable",
            scope="relationship",
            certainty=0.68,
            stability="medium",
            evidence_span=raw,
            warnings=("fallback_relational_feedback",),
        ))

    return frames


def _mk_frame(frame_type: str, relation: str, value: str, kind: str, evidence: str, certainty: float, time_scope: str, modality: str, surface: Any, *, stability: str = "stable", polarity: str | None = None) -> MeaningFrame:
    return MeaningFrame(
        frame_type=frame_type,  # type: ignore[arg-type]
        relation=relation,
        target=link_entity(value, hinted_kind=kind),
        polarity=polarity or surface.polarity,
        modality=modality,
        time_scope=time_scope if surface.time_scope == "unknown" else surface.time_scope,
        scope="self",
        certainty=certainty,
        stability=stability,
        evidence_span=evidence,
        warnings=("fallback_surface_frame",),
    )


def _extract_identity_name(raw: str) -> tuple[str, bool] | None:
    """Bắt các câu khai báo/correction tên ở tầng MeaningFrame.

    Dùng `_extract_after()` để match trên bản normalize không dấu nhưng lấy lại
    surface gốc có dấu. Regex trực tiếp với tiếng Việt dễ trượt ở chữ như "tên"
    vì dấu thanh nằm trên nguyên âm ghép. Một trò Unicode nhỏ, đủ làm memory ngu.
    """

    corrected_cues = (
        ("thuc ra ten that day du cua toi la", "name"),
        ("thuc ra ten day du cua toi la", "name"),
        ("thuc ra toi ten day du la", "name"),
        ("thuc ra ten that cua toi la", "name"),
        ("thuc ra ten that toi la", "name"),
        ("thuc ra toi ten la", "name"),
        ("thuc ra toi ten", "name"),
        ("ten that cua toi la", "name"),
        ("ten that toi la", "name"),
        ("ten that cua toi", "name"),
        ("ten that toi", "name"),
    )
    value = _extract_after(raw, corrected_cues)
    if value and len(value) <= 60 and is_plausible_user_name(value):
        return value, True

    regular_cues = (
        ("ten that day du cua toi la", "name"),
        ("ten day du cua toi la", "name"),
        ("ten day du toi la", "name"),
        ("toi ten day du la", "name"),
        ("toi ten la", "name"),
        ("toi ten", "name"),
        ("minh ten la", "name"),
        ("minh ten", "name"),
        ("tui ten la", "name"),
        ("tui ten", "name"),
        ("em ten la", "name"),
        ("em ten", "name"),
        ("ten cua toi la", "name"),
        ("ten toi la", "name"),
        ("my name is", "name"),
        ("call me", "name"),
    )
    value = _extract_after(raw, regular_cues)
    if value and len(value) <= 60 and is_plausible_user_name(value):
        return value, False
    return None

def _extract_identity_nickname(raw: str) -> str | None:
    """Bắt câu khai báo biệt danh/nickname ở tầng MeaningFrame.

    Tách hẳn khỏi `_extract_identity_name`: tên thật và biệt danh là hai fact
    identity khác nhau. Chỉ nhận các cue chứa "biệt danh"/"biệt hiệu"/"nickname"
    kèm bộ phân tách ("là"/":") để không nuốt nhầm cả câu.
    """

    cues = (
        ("biet danh cua toi la", "nickname"),
        ("biet hieu cua toi la", "nickname"),
        ("nickname cua toi la", "nickname"),
        ("ten goi cua toi la", "nickname"),
        ("biet danh cua minh la", "nickname"),
        ("biet danh toi la", "nickname"),
        ("toi co biet danh la", "nickname"),
        ("minh co biet danh la", "nickname"),
        ("biet danh la", "nickname"),
        ("biet hieu la", "nickname"),
        ("nickname la", "nickname"),
        ("my nickname is", "nickname"),
        ("nickname is", "nickname"),
    )
    value = _extract_after(raw, cues)
    if value and len(value) <= 60 and is_plausible_user_name(value):
        return value
    return None

def _extract_gender_identity(raw: str) -> str:
    """Fallback local cho self-disclosure giới tính.

    Structured semantic parser vẫn là đường chính cho identity đa dạng. Hàm này
    chỉ nhận vài mẫu explicit, không suy luận giới tính từ tên, giọng hoặc cách
    xưng hô. Đoán identity từ dấu hiệu gián tiếp là cách nhanh để memory vừa sai
    vừa tự tin, một tổ hợp không ai đặt hàng.
    """

    patterns = (
        r"(?:^|\b)giới\s*tính\s+(?:của\s+)?(?:tôi|mình|tui)\s*(?:là|:)\s*([^.,;!?]{2,40})",
        r"(?:^|\b)(?:tôi|toi|mình|minh|tui|em)\s+(?:là|la|thuộc\s+giới\s+tính|thuoc\s+gioi\s+tinh)\s+(nam|nữ|nu|non[- ]?binary|phi nhị nguyên|phi nhi nguyen|chuyển giới|chuyen gioi)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.UNICODE)
        if match:
            value = clean_surface(match.group(1))
            if value:
                return value
    return ""


def _extract_study_field(raw: str) -> str:
    """Rút ngành/lĩnh vực học từ self-disclosure ngắn.

    Đây là fact profile hiện tại, không phải world/current-info. Vì vậy nó nên đi
    theo user_text -> semantic_memory thay vì bị source grounding kéo sang web.
    """

    patterns = (
        r"(?:^|\b)(?:tôi|toi|mình|minh|tui|em|i)\s+(?:đang\s+)?học\s+(?:ngành\s+)?(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
        r"(?:^|\b)(?:ngành\s+học\s+(?:của\s+)?(?:tôi|toi|mình|minh|tui|em)\s+(?:là|la)\s+)(.+?)(?=$|[.,;!?])",
        r"(?:^|\b)(?:i\s+study|i\s+am\s+studying)\s+(.+?)(?=$|[.,;!?])",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.UNICODE)
        if not match:
            continue
        value = clean_surface(match.group(1))
        if value and 1 <= len(value) <= 80:
            return value
    return ""


def _extract_year(norm: str) -> int | None:
    import re
    m = re.search(r"\b((?:19|20)\d{2}|2k\d{1,2})\b", norm)
    if not m:
        return None
    tok = m.group(1)
    if tok.startswith("2k"):
        return 2000 + int(tok[2:])
    return int(tok)


def _extract_after(raw: str, cues: Iterable[tuple[str, str]]) -> str:
    norm, offsets = _normalized_with_offsets(raw)
    for cue, _relation in cues:
        cue_norm = normalize(cue)
        if not cue_norm:
            continue
        start = norm.find(cue_norm)
        if start < 0:
            continue
        start += len(cue_norm)
        while start < len(norm) and norm[start].isspace():
            start += 1
        raw_start = offsets[start] if start < len(offsets) else len(raw)
        tail = clean_surface(raw[raw_start:])
        if tail:
            return tail
    return ""


def _extract_preference(norm: str, raw: str) -> tuple[str, str] | None:
    for cue in ("toi thich", "minh thich", "tui thich", "i like", "i prefer"):
        if cue in norm:
            value = _extract_after(raw, ((cue, "preference"),))
            if value:
                kind = "location" if link_entity(value, hinted_kind="unknown").kind == "location" else "unknown"
                return value, kind
    return None


def _normalized_with_offsets(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    offsets: list[int] = []
    previous_was_space = True
    for index, char in enumerate(str(text or "")):
        normalized = strip_accents(char.lower()).replace("đ", "d")
        emitted = False
        for piece in normalized:
            if piece.isalnum() or piece in "_-":
                chars.append(piece)
                offsets.append(index)
                previous_was_space = False
                emitted = True
        if emitted:
            continue
        if chars and not previous_was_space:
            chars.append(" ")
            offsets.append(index)
            previous_was_space = True
    if chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return "".join(chars), offsets


def _has_self(norm: str) -> bool:
    return any(f" {x} " in f" {norm} " for x in ("toi", "minh", "tui", "em", "i"))


def _looks_like_style_instruction(norm: str) -> bool:
    if not any(x in norm for x in ("tra loi", "giai thich", "viet", "chu thich", "comment", "noi ngan", "noi dai", "tone", "style")):
        return False
    return any(x in norm for x in ("toi muon", "toi thich", "tu gio", "ve sau", "hay", "nen", "dung"))


def _looks_like_feedback(norm: str) -> bool:
    return any(x in norm for x in ("lanh lung", "xa cach", "robot", "may moc", "vo hon", "qua formal", "cung qua", "tone te"))


def _infer_frame_type(relation: str) -> str:
    r = relation.lower()
    if r in {"name", "name_is", "nickname", "nickname_is", "birth_year", "birth_year_is", "birth_month", "birth_month_is", "age", "age_observed_is", "gender", "gender_identity", "gender_identity_is"}:
        return "identity"
    if r in {"study_field", "study_field_is", "education_field", "education_field_is"}:
        return "project"
    if "location" in r or r in {"hometown", "living_location", "current_location", "hometown_is", "living_location_is", "current_location_is"}:
        return "location"
    if "pref" in r or r in {"likes", "dislikes", "general_preference", "preference_location"}:
        return "preference"
    if "style" in r or "instruction" in r:
        return "instruction"
    if "feedback" in r or "tone" in r:
        return "feedback"
    return "unknown"


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if number != number or number in (float("inf"), float("-inf")):
        number = 0.0
    return max(0.0, min(1.0, number))


__all__ = [
    "SemanticParser",
    "ModelSemanticParser",
    "StructuredFrameParser",
    "FallbackSurfaceSemanticParser",
    "structured_frames_to_result",
]
