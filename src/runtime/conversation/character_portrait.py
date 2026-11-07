# -*- coding: utf-8 -*-
"""Character V1 / v0.2: dựng chân dung nhân vật CHỈ-ĐỌC từ state thật.

Không gọi model, không cập nhật trait, không học memory, không quyết định câu trả lời.
StateReducer/PersistentInternalState vẫn giữ quyền sở hữu state; RootAgent quyết
định ý định, writer chỉ thực hiện. Portrait chỉ là một projection theo snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping

from .claim_evidence import EvidenceRegistry


@dataclass(frozen=True, slots=True)
class CharacterFacet:
    facet_id: str
    kind: str
    summary: str
    origin: str
    source_refs: tuple[str, ...] = ()
    temporal_scope: str = "state_snapshot"
    confidence: float = 1.0
    priority: int = 90
    required: bool = False


@dataclass(frozen=True, slots=True)
class CharacterPortrait:
    snapshot_id: str
    state_version: int
    sampled_at: float
    facets: tuple[CharacterFacet, ...]
    source_subset: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CharacterPortraitRender:
    text: str
    omitted_facets: tuple[str, ...]
    overflow: bool


def _number(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
        return max(0.0, min(1.0, n)) if n == n else default
    except (TypeError, ValueError, OverflowError):
        return default


def _top(values: Any, *, minimum: float = 0.52, max_items: int = 5) -> str:
    if not isinstance(values, Mapping):
        return ""
    pairs = sorted(((str(k), _number(v)) for k, v in values.items()), key=lambda p: (-p[1], p[0]))
    return ", ".join(f"{key}={val:.2f}" for key, val in pairs if val >= minimum) if max_items < 0 else ", ".join(
        f"{key}={val:.2f}" for key, val in [p for p in pairs if p[1] >= minimum][:max_items]
    )


def _fold_text(text: Any) -> str:
    """Chuẩn hóa chữ có dấu để truy xuất state; không suy luận ngữ nghĩa."""
    clean = unicodedata.normalize("NFD", str(text or "").casefold())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        "d" if c == "đ" else c for c in clean if unicodedata.category(c) != "Mn"
    )))


def _search_words(text: Any) -> set[str]:
    """Tách từ chỉ để *tìm state liên quan*, không phân loại đạo đức/ý định.

    Phép chiếu này cố ý không hỏi model thứ hai chọn ``Eris nên tin gì``.
    """
    stopwords = {"khong", "nhung", "dang", "dieu", "chuyen", "minh", "nguoi", "cho", "cua", "theo"}
    return {word for word in _fold_text(text).split() if len(word) >= 3 and word not in stopwords}


def _relevance(focus: str, key: str, summary: str = "") -> float:
    """Tính điểm truy xuất, không hề suy ra niềm tin hoặc chọn câu trả lời.

    Belief ID của Identity là tiếng Anh, trong khi người dùng thường nói tiếng
    Việt; map một vài CHỦ ĐỀ rộng để belief về ``world`` không bị bỏ sót khi
    người dùng nói ``thế giới/cuộc sống``. Đây không phải bảng đáp án đạo đức.
    """
    words = _search_words(focus)
    if not words:
        return 0.0
    overlap = len(words & (_search_words(key) | _search_words(summary)))
    score = min(3.0, float(overlap) * 0.8)
    key_root = str(key).split(".", 1)[0]
    normalized_focus = _fold_text(focus)
    # Kiểm tra cụm từ thay vì một từ dễ trùng: "đạo hàm" không phải đạo đức,
    # "thế giới" không phải "thay thế" trong quan hệ với user.
    world_topics = (
        "the gioi", "cuoc song", "dang song", "con song", "dang de song",
        "ton tai", "y nghia", "vo nghia", "hu vo", "dao duc", "long tot",
        "an ui", "cai chet", "tuyet vong", "mang song",
    )
    user_topics = ("quan he", "tin tuong", "phan boi", "thay the", "gan gui", "nguoi dung")
    self_topics = ("noi tam", "ban sac", "ban than", "danh tinh", "eris")
    if key_root == "world" and any(topic in normalized_focus for topic in world_topics):
        score += 3.0
    elif key_root == "self" and any(topic in normalized_focus for topic in self_topics):
        score += 2.0
    elif key_root == "user" and any(topic in normalized_focus for topic in user_topics):
        score += 2.0
    return score


def build_character_portrait(
    state: Any | None, *, affect: Any | None, snapshot_id: str,
    sampled_at: float, source_registry: EvidenceRegistry,
    focus_text: str = "", subject_self_view: Mapping[str, Any] | None = None,
) -> CharacterPortrait:
    """Projection deterministic: facet nguồn là state revision, KHÔNG là episode.

    Bất kỳ evidence_id nào trong Belief/Narrative chỉ được link nếu ref đó thực sự
    tồn tại trong registry. ID mơ hồ không được tự tạo nguồn lịch sử ngoài đời.
    """
    version = int(getattr(state, "version", 0) or 0)
    assert snapshot_id == source_registry.snapshot_id, "portrait_snapshot_mismatch"
    assert version == source_registry.state_version, "portrait_state_revision_mismatch"
    if state is None:
        return CharacterPortrait(snapshot_id, version, sampled_at, (), ())

    identity = getattr(state, "identity", None)
    personality = getattr(state, "personality", None)
    known = {entry.ref for entry in source_registry.entries if entry.status == "supported"}
    refs = (snapshot_id,) if snapshot_id in known else ()
    facets: list[CharacterFacet] = []

    def add(fid: str, kind: str, summary: str, *, priority: int, required: bool = False,
            origin: str = "runtime_state", confidence: float = 1.0,
            source_refs: tuple[str, ...] | None = None,
            scope: str = "state_snapshot") -> None:
        if summary.strip():
            facets.append(CharacterFacet(fid, kind, summary.strip(), origin,
                                         refs if source_refs is None else source_refs,
                                         scope, confidence, priority, required))

    # Không dùng prompt_summary() bị cắt còn 520/980 ký tự làm input.
    add("integrity", "source_rules", "Những cảm nhận, niềm tin và tự diễn giải của Eris có thể sai; state hiện tại không chứng minh một sự kiện ngoài đời. Không tự tạo lịch sử hay lời user.", priority=0, required=True, origin="projection_contract")
    if affect is not None:
        # Sample AffectiveVoiceState ở ContextAssembler đúng MỘT lần.
        mood = ", ".join(f"{key}={_number(getattr(affect, key, 0)):.2f}" for key in (
            "irritation", "delight", "sadness", "anxiety", "playfulness", "curiosity", "tenderness", "fatigue"
        ))
        add("current_affect", "current_affect", "Cảm xúc NGAY LÚC NÀY (khác xu hướng tính cách): " + mood,
            priority=5, required=True, scope="current_turn")

    conflict = getattr(identity, "active_conflict", None)
    if conflict is not None and getattr(conflict, "conflict_id", "") and _number(getattr(conflict, "activation", 0)) > 0:
        add("active_conflict", "conflict", (
            f"Mâu thuẫn đang có '{conflict.conflict_id}': "
            f"left_pull={_number(conflict.left_pull):.2f}; right_pull={_number(conflict.right_pull):.2f}; "
            f"activation={_number(conflict.activation):.2f}; "
            f"chosen_side={str(conflict.chosen_side or 'chưa chọn')}; "
            f"residue={_number(conflict.residue):.2f} (dư âm không tự mất khi đã chọn)."
        ), priority=10, required=True)
    choice = getattr(identity, "latest_value_choice", None)
    if choice is not None and getattr(choice, "chosen", ""):
        add("value_choice", "value_choice", (
            f"Lựa chọn giá trị trong snapshot: chosen={choice.chosen}; "
            f"rejected={choice.rejected or 'không rõ'}; "
            f"cost={choice.cost or 'không ghi nhận'}; confidence={_number(choice.confidence):.2f}. "
            "Không coi cost hoặc mặt bị từ chối là đã biến mất."
        ), priority=12, required=True, confidence=_number(choice.confidence))
    narrative = getattr(personality, "narrative", None)
    if narrative is not None and getattr(narrative, "text", ""):
        # Đây là cách Eris tự diễn giải, không phải chuyện đã xảy ra được chứng thực.
        resolved = tuple(x for x in getattr(narrative, "provenance", ()) if x in known)
        add("self_narrative", "self_interpretation", (
            f"Tự diễn giải có thể sai: {narrative.text}; frame={narrative.frame}; "
            f"confidence={_number(narrative.confidence):.2f}; source_turn_seq={getattr(narrative, 'source_turn_seq', 0)}."
        ), priority=20, origin="state_self_narrative", confidence=_number(narrative.confidence),
            source_refs=refs + resolved)

    if identity is not None:
        rel = getattr(identity, "primary_relationship", None)
        relation = rel() if callable(rel) else None
        if relation is not None:
            props = {key: getattr(relation, key, 0) for key in (
                "familiarity", "closeness", "respect", "competition_sensitivity", "attachment_intensity"
            )}
            add("relationship", "relationship", "Quan hệ trong state: " + _top(props, minimum=0.0, max_items=5), priority=25)
        beliefs = getattr(identity, "beliefs", {})
        if isinstance(beliefs, Mapping):
            # Trước đây lấy cứng 5 belief mạnh nhất, khiến belief vừa phải nhưng
            # liên quan trực tiếp đến vấn đề bị mất khỏi Root và Writer.
            # Giữ tối đa 5 belief, nhưng truy xuất theo câu hỏi hiện tại trước;
            # không nâng strength/confidence trong state và không cập nhật belief.
            ranked = sorted(beliefs.items(), key=lambda kv: (
                -_relevance(focus_text, str(kv[0])),
                -_number(getattr(kv[1], "strength", 0)) * _number(getattr(kv[1], "confidence", 0)),
                str(kv[0]),
            ))
            for name, belief in ranked[:5]:
                basis = tuple(str(ref) for ref in getattr(belief, "evidence_ids", ()) if str(ref) in known)
                relevance = _relevance(focus_text, str(name))
                add("belief:" + str(name), "belief", (
                    f"Niềm tin có thể sai '{name}': strength={_number(getattr(belief, 'strength', 0)):.2f}; "
                    f"confidence={_number(getattr(belief, 'confidence', 0)):.2f}; "
                    f"contradictions={getattr(belief, 'contradictions', 0)}; "
                    f"last_turn_seq={getattr(belief, 'last_turn_seq', 0)}."
                ), priority=32 if relevance else 36, origin="identity_belief", confidence=_number(getattr(belief, "confidence", 0)),
                    source_refs=refs + basis)
        for name, values in (("values", getattr(identity, "values", {})),
                             ("motives", getattr(identity, "motives", {})),
                             ("defenses", getattr(identity, "defenses", {}))):
            add(name, name, "Xu hướng " + name + " (không phải mệnh lệnh): " + _top(values), priority=46)

    # Subject Core là chủ của self-model dẫn xuất từ các lựa chọn và kết quả
    # thực tế. Projection chỉ đọc self_view() đã tồn tại; không sinh Self thứ hai.
    # Một source_ref không có trong evidence registry KHÔNG được biến thành
    # chứng cứ công khai. Nếu chưa có ref kiểm chứng, chỉ nói đây là tự diễn giải.
    subject = subject_self_view if isinstance(subject_self_view, Mapping) else {}
    raw_facets = subject.get("facets", ())
    if isinstance(raw_facets, (list, tuple)):
        candidates = [x for x in raw_facets[:48] if isinstance(x, Mapping)]
        # Chỉ lấy facet có causal refs, tránh dùng một phỏng đoán do model viết
        # mà không có trải nghiệm làm nền. Những ref không ở registry vẫn không
        # được tuyên bố là sự kiện độc lập đã được xác nhận.
        candidates = [x for x in candidates if x.get("evidence_refs") and
                      _number(x.get("confidence")) >= 0.35 and str(x.get("statement", "")).strip()]
        candidates.sort(key=lambda x: (
            -_relevance(focus_text, str(x.get("facet_id", "")), str(x.get("statement", ""))),
            -(_number(x.get("confidence")) + _number(x.get("stability")) + _number(x.get("pressure"))),
            str(x.get("facet_id", "")),
        ))
        for item in candidates[:3]:
            fid = str(item.get("facet_id", ""))[:100]
            if not fid:
                continue
            evidence_ids = item.get("evidence_refs", ())
            valid = tuple(str(r) for r in evidence_ids[:12] if str(r) in known) if isinstance(evidence_ids, (tuple, list)) else ()
            summary = " ".join(str(item.get("statement", "")).split())[:520]
            confidence = _number(item.get("confidence"))
            relevance = _relevance(focus_text, fid, summary)
            add("subject:" + fid, "subject_self", (
                f"Tự diễn giải từ Subject Core (không phải sự kiện ngoài đời): {summary}; "
                f"confidence={confidence:.2f}; stability={_number(item.get('stability')):.2f}."
            ), priority=34 if relevance else 44, origin="subject_self_projection",
                confidence=confidence, source_refs=refs + valid, scope="self_model_snapshot")

    if personality is not None:
        add("temperament", "disposition", "Xu hướng dài hạn, KHÔNG đồng nhất mood: " + _top(getattr(personality, "temperament", {}), max_items=9), priority=52)
        add("core", "core", "Giá trị nền hiện tại: " + _top(getattr(personality, "core", {}), max_items=6), priority=60)
        # Legacy trait không chứa original event/provenance; đánh dấu scope.
        mood = _top(getattr(personality, "mood", {}), max_items=6)
        add("mood_baseline", "disposition", "Nền mood của profile (khác affect realtime): " + mood,
            priority=70, origin="profile_disposition_legacy", confidence=0.65, scope="profile_not_episode")

    selected = tuple(sorted(facets, key=lambda x: (x.priority, x.facet_id)))
    return CharacterPortrait(snapshot_id, version, sampled_at, selected,
                             tuple(sorted({r for f in selected for r in f.source_refs if r in known})))


def render_character_portrait(portrait: CharacterPortrait, *, max_chars: int = 6000) -> CharacterPortraitRender:
    """Bỏ nguyên facet theo độ ưu tiên, không bao giờ cắt giữa một mệnh đề.

    ``overflow`` báo thẳng nếu facet bắt buộc cũng không còn đủ chỗ. Caller
    không được coi bản này là full portrait hay âm thầm xóa cờ omitted.
    """
    limit = max(0, int(max_chars))
    header = f"ERIS_CHARACTER_PORTRAIT_V1 snapshot={portrait.snapshot_id} revision={portrait.state_version}\n"
    content = header if len(header) <= limit else ""
    omitted: list[str] = []
    overflow = not content and bool(portrait.facets)
    for facet in sorted(portrait.facets, key=lambda x: (x.priority, x.facet_id)):
        line = f"[{facet.kind}/{facet.facet_id}; origin={facet.origin}; scope={facet.temporal_scope}; confidence={facet.confidence:.2f}] {facet.summary}\n"
        if len(content) + len(line) <= limit:
            content += line
        else:
            omitted.append(facet.facet_id)
            overflow = overflow or facet.required
    if omitted:
        # Metadata omission luôn hiện hữu trong result; nếu còn chỗ thì ghi text.
        note = "Omitted facets: " + ",".join(omitted) + "\n"
        if len(content) + len(note) <= limit:
            content += note
    return CharacterPortraitRender(content.rstrip(), tuple(omitted), overflow)


__all__ = ["CharacterFacet", "CharacterPortrait", "CharacterPortraitRender", "build_character_portrait", "render_character_portrait"]
