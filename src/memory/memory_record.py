from __future__ import annotations

"""
memory_record.py
================

Contract trung tâm cho memory v2.

Mục tiêu:
- Mọi memory layer trả về cùng một hình dạng dữ liệu.
- Retrieval/scoring không còn phải đoán shape của SemanticMemoryItem,
  EpisodicMemoryItem, RepairMemoryItem, v.v.
- Memory không "ra lệnh" cho ERIS. Nó chỉ mang theo dấu vết, độ tin cậy,
  anchor, policy surface và hậu quả đã từng xảy ra.

Đây là lớp nền để ERIS có tự do hơn:
    Không cấm nghĩ.
    Không cấm nhớ.
    Chỉ buộc mỗi ký ức phải có nguồn, weight và hậu quả rõ ràng.
"""

from dataclasses import dataclass, field
from collections import Counter
from typing import Any, Mapping
import hashlib
import math
import re
import time
import unicodedata


STOPWORDS = {
    "a", "ai", "anh", "ay", "ban", "bang", "bi", "bo", "cai", "cac", "can",
    "cho", "co", "con", "cua", "cung", "da", "dang", "day", "de", "den",
    "di", "do", "duoc", "em", "gi", "ha", "hay", "hoi", "hon", "khi",
    "khong", "kia", "la", "lai", "lam", "len", "luc", "ma", "may", "minh",
    "mot", "nay", "neu", "ban", "nguoi", "nhu", "nhung", "noi", "nua",
    "o", "qua", "ra", "rang", "roi", "sao", "se", "thi", "thoi", "toi",
    "trong", "tu", "van", "ve", "vi", "vua", "voi",
}


def compact_text(value: Any, *, limit: int = 420) -> str:
    """Nén text để memory packet không kéo prompt thành bãi rác token."""
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clamp01(value: Any) -> float:
    """Ép số về [0, 1]."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric:  # NaN
        return 0.0
    return max(0.0, min(1.0, numeric))


def stable_hash(text: str, length: int = 16) -> str:
    digest = hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()
    return digest[: max(8, min(40, int(length or 16)))]


def normalize_text(text: Any) -> str:
    """Chuẩn hóa text để match ổn hơn.

    Chú ý:
    - Bỏ dấu để không vỡ khi data có/không dấu.
    - Không dùng normalize này để "hiểu nghĩa".
    - Nó chỉ phục vụ lexical/anchor matching có kiểm soát.
    """
    lowered = str(text or "").strip().lower()
    try:
        remojibake = lowered.encode("latin1").decode("utf-8")
        if remojibake:
            lowered = remojibake
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    lowered = unicodedata.normalize("NFD", lowered)
    lowered = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
    lowered = lowered.replace("đ", "d").replace("Đ", "d")
    lowered = re.sub(r"[^\w\s./:#@+-]", " ", lowered, flags=re.UNICODE)
    return " ".join(lowered.split())


def tokenize(text: Any) -> tuple[str, ...]:
    """Token hóa có lọc stopword.

    Token file/module được giữ lại vì trong project ERIS, anchor kỹ thuật
    thường quan trọng hơn từ chung.
    """
    norm = normalize_text(text)
    raw_tokens = re.findall(r"[a-z0-9_./:#@+-]+", norm, flags=re.IGNORECASE)
    out: list[str] = []
    for token in raw_tokens:
        clean = token.strip("._-:/#@+")
        if not clean:
            continue
        is_file = bool(re.search(r"\.(py|gd|cs|json|xml|md|txt|yaml|yml)$", token))
        is_symbol = "_" in token or "::" in token or "#" in token
        if not (is_file or is_symbol):
            if len(clean) < 3:
                continue
            if clean in STOPWORDS:
                continue
        out.append(token)
    return tuple(out)


def token_bigrams(tokens: tuple[str, ...]) -> set[tuple[str, str]]:
    if len(tokens) < 2:
        return set()
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def weighted_counter(tokens: tuple[str, ...]) -> Counter[str]:
    """Counter có trọng số cho anchor kỹ thuật."""
    counter: Counter[str] = Counter()
    for token in tokens:
        weight = 1.0
        if re.search(r"\.(py|gd|cs|json|xml|md|txt|yaml|yml)$", token):
            weight += 1.25
        if "_" in token:
            weight += 0.65
        if len(token) >= 12:
            weight += 0.20
        counter[token] += weight
    return counter


def weighted_jaccard(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    keys = set(left) | set(right)
    inter = sum(min(float(left.get(key, 0.0)), float(right.get(key, 0.0))) for key in keys)
    union = sum(max(float(left.get(key, 0.0)), float(right.get(key, 0.0))) for key in keys)
    return 0.0 if union <= 0.0 else inter / union


def phrase_score(query_norm: str, text_norm: str) -> float:
    """Phrase match đủ dài, không tính token đơn như chân lý."""
    if not query_norm or not text_norm:
        return 0.0
    relaxed_query = re.sub(r"(?<=\w)-(?=\w)", " ", query_norm)
    relaxed_text = re.sub(r"(?<=\w)-(?=\w)", " ", text_norm)
    if len(query_norm) >= 12 and (query_norm in text_norm or relaxed_query in relaxed_text):
        return 1.0
    chunks = [item.strip() for item in re.split(r"[,.!?;:\n]+", query_norm) if len(item.strip()) >= 12]
    if not chunks:
        return 0.0
    return sum(1 for chunk in chunks if chunk in text_norm or re.sub(r"(?<=\w)-(?=\w)", " ", chunk) in relaxed_text) / max(1, len(chunks))


def extract_anchors(text: Any) -> tuple[str, ...]:
    """Rút anchor mạnh: file, module, symbol, failure tags.

    Anchor không phải keyword chung. Nó là dấu móc có giá trị truy hồi:
    tên file, tên module, failure class, semantic family.
    """
    norm = normalize_text(text)
    anchors: list[str] = []

    for match in re.findall(r"\b[\w\-]+(?:\.[\w\-]+)+\b", norm):
        if re.search(r"\.(py|gd|cs|json|xml|md|txt|yaml|yml)$", match):
            anchors.append(match)

    for match in re.findall(r"\b[a-z][a-z0-9]+_[a-z0-9_]+\b", norm):
        anchors.append(match)

    failure_terms = (
        "misread", "overread", "underacted", "overexplained", "tone_mismatch",
        "privacy_intrusion", "body_mismatch", "assistant_flattening",
        "overhumanization", "keyword_rule", "fallback", "repair_memory",
        "source_grounding", "memory_recall", "semantic_repeat",
    )
    for term in failure_terms:
        if term in norm:
            anchors.append(term)

    # Dedupe giữ thứ tự.
    seen: set[str] = set()
    out: list[str] = []
    for anchor in anchors:
        if anchor and anchor not in seen:
            seen.add(anchor)
            out.append(anchor)
    return tuple(out[:24])


def parse_timestamp(value: Any) -> float:
    """Parse timestamp best-effort.

    Nếu parse không được, trả 0. Memory cũ không nên làm runtime chết vì format.
    """
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        # YYYY-mm-ddTHH:MM:SS+0700 hoặc không timezone.
        return time.mktime(time.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        pass
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def recency_score(timestamp: Any, *, now: float | None = None, half_life_hours: float = 168.0) -> float:
    ts = parse_timestamp(timestamp)
    if ts <= 0:
        return 0.0
    current = time.time() if now is None else float(now)
    age_hours = max(0.0, (current - ts) / 3600.0)
    return clamp01(0.5 ** (age_hours / max(1.0, half_life_hours)))


@dataclass(slots=True)
class MemoryRecord:
    """Record thống nhất cho mọi memory layer.

    Các layer cũ vẫn có thể giữ class riêng. Retrieval v2 chỉ yêu cầu adapter
    chuyển chúng về MemoryRecord trước khi scoring.
    """

    id: str
    layer: str
    text: str

    summary: str = ""
    raw_text: str = ""

    created_at: float = 0.0
    last_accessed_at: float = 0.0
    access_count: int = 0

    confidence: float = 0.5
    salience: float = 0.0
    emotional_weight: float = 0.0
    relational_weight: float = 0.0
    repair_weight: float = 0.0

    tags: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    decay_policy: str = "normal"
    privacy_level: str = "normal"
    surface_policy: str = "silent_bias"

    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_text(self) -> str:
        return normalize_text(" ".join(part for part in (self.text, self.summary, self.raw_text, " ".join(self.tags), " ".join(self.anchors)) if part))

    def tokens(self) -> tuple[str, ...]:
        return tokenize(self.normalized_text())

    def anchor_set(self) -> set[str]:
        return set(self.anchors) | set(extract_anchors(self.text)) | set(extract_anchors(self.summary))

    def recall_line(self, *, limit: int = 280) -> str:
        return compact_text(self.summary or self.text, limit=limit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "layer": self.layer,
            "text": self.text,
            "summary": self.summary,
            "raw_text": self.raw_text,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "confidence": round(float(self.confidence), 4),
            "salience": round(float(self.salience), 4),
            "emotional_weight": round(float(self.emotional_weight), 4),
            "relational_weight": round(float(self.relational_weight), 4),
            "repair_weight": round(float(self.repair_weight), 4),
            "tags": list(self.tags),
            "anchors": list(self.anchors),
            "evidence_ids": list(self.evidence_ids),
            "decay_policy": self.decay_policy,
            "privacy_level": self.privacy_level,
            "surface_policy": self.surface_policy,
            "metadata": dict(self.metadata),
        }


def record_from_layer_item(layer: str, item: Any) -> MemoryRecord:
    """Adapter mềm từ store item cũ sang MemoryRecord.

    Không đụng vào store cũ. Cứ để chúng sống thêm một lúc, như di sản kỹ thuật
    mà ai cũng biết sẽ phải thay nhưng chưa có thời gian làm tang lễ.
    """
    if isinstance(item, MemoryRecord):
        return item

    item_id = str(getattr(item, "id", "") or getattr(item, "record_id", "") or stable_hash(repr(item), 16))
    timestamp = getattr(item, "timestamp", None) or getattr(item, "created_at", None) or getattr(item, "updated_at", None)
    created_at = parse_timestamp(timestamp)

    if hasattr(item, "recall_line"):
        try:
            text = compact_text(item.recall_line(), limit=420)
        except Exception:
            text = ""
    else:
        text = ""

    summary = compact_text(getattr(item, "summary", "") or getattr(item, "fact", "") or getattr(item, "pattern", "") or text, limit=420)
    metadata = dict(getattr(item, "metadata", {}) or {}) if isinstance(getattr(item, "metadata", {}), Mapping) else {}
    raw_content = dict(metadata.get("raw_content") or {}) if isinstance(metadata.get("raw_content"), Mapping) else {}
    raw_content_meta = dict(raw_content.get("metadata") or {}) if isinstance(raw_content.get("metadata"), Mapping) else {}
    semantic_claim = dict(metadata.get("semantic_claim") or {}) if isinstance(metadata.get("semantic_claim"), Mapping) else {}

    raw_parts = [
        getattr(item, "summary", ""),
        getattr(item, "scene", ""),
        getattr(item, "user_input", ""),
        getattr(item, "eris_action", ""),
        getattr(item, "outcome", ""),
        getattr(item, "fact", ""),
        getattr(item, "claim", ""),
        getattr(item, "pattern", ""),
        getattr(item, "trigger", ""),
        getattr(item, "rule", ""),
        getattr(item, "handling_rule", ""),
        getattr(item, "description", ""),
        getattr(item, "residue_type", ""),
        getattr(item, "wrong_assumption", ""),
        getattr(item, "user_correction", ""),
        getattr(item, "repair_strategy", ""),
        getattr(item, "future_suppression_rule", ""),
        metadata.get("raw_user_text", ""),
        metadata.get("fact_key", ""),
        " ".join(str(alias) for alias in metadata.get("aliases", ()) if str(alias).strip()) if isinstance(metadata.get("aliases"), (list, tuple, set)) else metadata.get("aliases", ""),
        metadata.get("value", ""),
        raw_content.get("fact", ""),
        raw_content.get("surface", ""),
        raw_content.get("value", ""),
        raw_content_meta.get("value", ""),
        semantic_claim.get("predicate", ""),
        semantic_claim.get("object_value", ""),
        semantic_claim.get("object_text", ""),
    ]
    raw_text = compact_text(" ".join(str(part or "") for part in raw_parts if str(part or "").strip()), limit=1200)
    if not text:
        text = compact_text(summary or raw_text or str(item), limit=420)

    tags = tuple(str(tag) for tag in getattr(item, "tags", ()) or () if str(tag).strip())
    evidence_ids = tuple(str(eid) for eid in getattr(item, "evidence_ids", ()) or () if str(eid).strip())

    metadata_anchors = ()
    if isinstance(metadata.get("anchors"), (list, tuple, set)):
        metadata_anchors = tuple(str(x) for x in metadata.get("anchors") or () if str(x).strip())

    anchors = tuple(dict.fromkeys((
        *metadata_anchors,
        *extract_anchors(text),
        *extract_anchors(summary),
        *extract_anchors(raw_text),
        *extract_anchors(" ".join(tags)),
    )))

    surface_policy = str(metadata.get("surface_policy") or _default_surface_policy(layer))

    return MemoryRecord(
        id=item_id,
        layer=layer,
        text=text,
        summary=summary,
        raw_text=raw_text,
        created_at=created_at,
        last_accessed_at=parse_timestamp(getattr(item, "last_accessed_at", 0.0)),
        access_count=int(getattr(item, "access_count", 0) or 0),
        confidence=clamp01(getattr(item, "confidence", 0.5)),
        salience=clamp01(
            getattr(item, "salience", 0.0)
            or getattr(item, "importance", 0.0)
            or getattr(item, "intensity", 0.0) * 0.55
            or getattr(item, "observations", 0) * 0.04
        ),
        emotional_weight=clamp01(getattr(item, "emotional_weight", 0.0) or getattr(item, "intensity", 0.0)),
        relational_weight=clamp01(
            getattr(item, "relational_weight", 0.0)
            or abs(float(getattr(item, "trust_delta", 0.0) or 0.0))
            or abs(float(getattr(item, "distance_delta", 0.0) or 0.0))
            or getattr(item, "rupture_weight", 0.0)
        ),
        repair_weight=clamp01(getattr(item, "repair_weight", 0.0) or (0.70 if layer == "repair" else 0.0)),
        tags=tags,
        anchors=anchors,
        evidence_ids=evidence_ids,
        decay_policy=str(getattr(item, "decay_policy", "normal") or "normal"),
        privacy_level=str(getattr(item, "privacy_level", "normal") or "normal"),
        surface_policy=surface_policy,
        metadata=metadata,
    )


def _default_surface_policy(layer: str) -> str:
    return {
        "repair": "surface_if_needed_for_fix",
        "procedural": "silent_bias",
        "relational": "silent_bias",
        "affective_residue": "never_direct_claim",
        "semantic": "surface_if_contextual",
        "episodic": "surface_if_user_asks",
    }.get(str(layer or ""), "silent_bias")
