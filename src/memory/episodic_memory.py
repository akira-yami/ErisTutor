"""Episodic Memory: chuyện cụ thể đã xảy ra.

Episodic không phải semantic fact. Nó giữ cảnh, lượt, outcome, evidence và độ nổi bật.
Một câu user sửa sai hôm nay là episode. Một fact ổn định chỉ lên semantic sau khi commit phù hợp.

Bản này sửa phần recall:
- Không còn cộng điểm kiểu "query token nào xuất hiện trong chuỗi là +1".
- Không dùng substring token in haystack làm trung tâm.
- Dùng token chuẩn hóa, stopword, phrase match, token overlap có trọng số, bigram,
  field weight, salience/confidence/recency và decay policy.
- Một token lẻ không đủ kéo memory lên, trừ khi match tag/evidence rất rõ.
- Recall trả theo độ liên quan của episode, không phải theo việc chữ "lỗi" vô tình
  nằm trong summary. Một bước nhỏ để ERIS bớt nhớ như cái search box bị thiếu ngủ.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any
from .json_store_io import atomic_write_json, locked_path


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def compact_text(value: Any, *, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clamp01(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric:  # NaN guard.
        return 0.0
    return max(0.0, min(1.0, numeric))



def distillation_hash(row: dict[str, Any]) -> str:
    content = {key: row.get(key) for key in ("id", "timestamp", "frame_id", "summary", "scene",
                                            "user_input", "eris_action", "outcome", "evidence_ids")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Text normalization / matching
# ---------------------------------------------------------------------------

# Stopword nhỏ, chủ yếu để chặn recall bị kéo bởi từ quá chung.
# Đây không phải blacklist ý nghĩa. Nó chỉ nói: "mấy từ này không đủ tư cách
# một mình kéo ký ức lên". Một tiêu chuẩn thấp đến đau lòng, nhưng cần thiết.
STOPWORDS = {
    "a", "ai", "anh", "ay", "ban", "bang", "bi", "bo", "cai", "cac", "can",
    "cho", "co", "con", "cua", "cung", "da", "dang", "day", "de", "den",
    "di", "do", "duoc", "em", "gi", "ha", "hay", "hoi", "hon", "khi",
    "khong", "kia", "la", "lai", "lam", "len", "luc", "ma", "may", "minh",
    "mot", "nay", "neu", "nguoi", "nhu", "nhung", "noi", "nua",
    "o", "qua", "ra", "rang", "roi", "sao", "se", "thi", "thoi", "toi",
    "trong", "tu", "van", "ve", "vi", "vua", "voi",
}


def normalize_text(text: Any) -> str:
    """Normalize text để so khớp ổn hơn.

    Bỏ dấu tiếng Việt để recall không tạch vì dữ liệu có/không dấu.
    Không dùng substring sau normalize nữa; ta token hóa và so đúng token/bigram.
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


def normalized_tokens(text: Any) -> tuple[str, ...]:
    """Token hóa nhẹ, bỏ stopword và token quá ngắn.

    Token dạng file/module như micro_policy.py, relationship_dynamics.py được giữ
    vì chúng là anchor mạnh trong project ERIS.
    """
    norm = normalize_text(text)
    raw_tokens = re.findall(r"[a-z0-9_./:#@+-]+", norm, flags=re.IGNORECASE)
    tokens: list[str] = []
    for token in raw_tokens:
        clean = token.strip("._-:/#@+")
        if not clean:
            continue
        # Giữ file/module anchors dù ngắn hơn rule thường.
        is_anchor = bool(re.search(r"\.(py|gd|cs|json|xml|md|txt)$", token)) or "_" in token
        if not is_anchor and len(clean) < 3:
            continue
        if clean in STOPWORDS and not is_anchor:
            continue
        tokens.append(token)
    return tuple(tokens)


def token_bigrams(tokens: tuple[str, ...]) -> set[tuple[str, str]]:
    if len(tokens) < 2:
        return set()
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def weighted_counter(tokens: tuple[str, ...]) -> Counter[str]:
    """Tạo counter có trọng số nhẹ cho anchor kỹ thuật.

    Episodic memory trong project ERIS thường cần recall theo file/module/action.
    Nếu token là tên file hoặc có underscore, nó đáng tin hơn chữ chung như "bug".
    """
    counter: Counter[str] = Counter()
    for token in tokens:
        weight = 1.0
        if re.search(r"\.(py|gd|cs|json|xml|md|txt)$", token):
            weight += 1.2
        if "_" in token:
            weight += 0.6
        if len(token) >= 10:
            weight += 0.2
        counter[token] += weight
    return counter


def weighted_jaccard(left: Counter[str], right: Counter[str]) -> float:
    """Jaccard có trọng số giữa hai counter."""
    if not left or not right:
        return 0.0
    keys = set(left) | set(right)
    inter = sum(min(float(left.get(k, 0.0)), float(right.get(k, 0.0))) for k in keys)
    union = sum(max(float(left.get(k, 0.0)), float(right.get(k, 0.0))) for k in keys)
    if union <= 0.0:
        return 0.0
    return inter / union


def phrase_match_score(query_norm: str, hay_norm: str) -> float:
    """Score phrase match mềm.

    Khác với token in haystack:
    - Chỉ tính phrase đủ dài.
    - Query đầy đủ nằm trong field thì score cao.
    - Một token đơn không bao giờ được tính là phrase.
    """
    if not query_norm or not hay_norm:
        return 0.0
    if len(query_norm) >= 12 and query_norm in hay_norm:
        return 1.0

    parts = [part.strip() for part in re.split(r"[,.!?;:\n]+", query_norm) if len(part.strip()) >= 12]
    if not parts:
        return 0.0
    hits = sum(1 for part in parts if part in hay_norm)
    return hits / max(1, len(parts))


def parse_timestamp(timestamp: str) -> float:
    """Parse timestamp best-effort.

    Nếu parse không được thì trả 0, coi như rất cũ. Không để timestamp lỗi làm
    recall bay màu bằng exception. Bộ nhớ mà ngã vì format ngày thì đúng là quá mỏng.
    """
    raw = str(timestamp or "").strip()
    if not raw:
        return 0.0

    # Format hiện tại: YYYY-mm-ddTHH:MM:SS+0700
    for _fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, TypeError):
            continue
    return 0.0


def recency_boost(timestamp: str, *, now: float | None = None) -> float:
    """Boost nhẹ cho episode gần đây.

    Recency không được thắng relevance. Nó chỉ là tie-breaker mềm.
    """
    ts = parse_timestamp(timestamp)
    if ts <= 0:
        return 0.0
    current = time.time() if now is None else float(now)
    age_hours = max(0.0, (current - ts) / 3600.0)
    # Half-life khoảng 7 ngày.
    return clamp01(0.18 * (0.5 ** (age_hours / 168.0)))


@dataclass(frozen=True, slots=True)
class RecallScore:
    """Debug score cho một item recall.

    Giữ lớp này để sau này log "vì sao ký ức này được kéo lên" thay vì nhìn
    một con số tổng rồi cầu nguyện. Cầu nguyện với retrieval hiếm khi ra bug tốt.
    """

    total: float
    lexical: float = 0.0
    phrase: float = 0.0
    bigram: float = 0.0
    tag: float = 0.0
    salience: float = 0.0
    confidence: float = 0.0
    recency: float = 0.0
    access_penalty: float = 0.0
    matched_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 4),
            "lexical": round(self.lexical, 4),
            "phrase": round(self.phrase, 4),
            "bigram": round(self.bigram, 4),
            "tag": round(self.tag, 4),
            "salience": round(self.salience, 4),
            "confidence": round(self.confidence, 4),
            "recency": round(self.recency, 4),
            "access_penalty": round(self.access_penalty, 4),
            "matched_terms": list(self.matched_terms),
        }


# ---------------------------------------------------------------------------
# Episodic item
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EpisodicMemoryItem:
    id: str
    timestamp: str
    frame_id: str = ""
    summary: str = ""
    scene: str = ""
    user_input: str = ""
    eris_action: str = ""
    outcome: str = ""
    confidence: float = 0.65
    salience: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    decay_policy: str = "normal"
    access_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "frame_id": self.frame_id,
            "summary": self.summary,
            "scene": self.scene,
            "user_input": self.user_input,
            "eris_action": self.eris_action,
            "outcome": self.outcome,
            "confidence": round(float(self.confidence), 4),
            "salience": round(float(self.salience), 4),
            "evidence_ids": list(self.evidence_ids),
            "tags": list(self.tags),
            "decay_policy": self.decay_policy,
            "access_count": int(self.access_count),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EpisodicMemoryItem":
        return cls(
            id=str(data.get("id") or f"epi_{uuid.uuid4().hex[:12]}"),
            timestamp=str(data.get("timestamp") or iso_now()),
            frame_id=str(data.get("frame_id") or ""),
            summary=compact_text(data.get("summary") or ""),
            scene=compact_text(data.get("scene") or ""),
            user_input=compact_text(data.get("user_input") or "", limit=500),
            eris_action=compact_text(data.get("eris_action") or ""),
            outcome=compact_text(data.get("outcome") or ""),
            confidence=clamp01(float(data.get("confidence", 0.65) or 0.65)),
            salience=clamp01(float(data.get("salience", 0.0) or 0.0)),
            evidence_ids=[str(item) for item in data.get("evidence_ids") or [] if str(item).strip()],
            tags=[str(item) for item in data.get("tags") or [] if str(item).strip()],
            decay_policy=str(data.get("decay_policy") or "normal"),
            access_count=int(data.get("access_count", 0) or 0),
            metadata=dict(data.get("metadata") or {}),
        )

    def recall_line(self) -> str:
        parts = [self.summary, self.outcome]
        return compact_text(" -> ".join(part for part in parts if part), limit=280)

    def search_fields(self) -> dict[str, str]:
        """Fields phục vụ recall, có trọng số riêng trong scorer.

        Không gom tất cả vào một haystack rồi token-in-string nữa.
        Summary/user_input thường quan trọng hơn scene. Tags là anchor phụ.
        """
        return {
            "summary": self.summary,
            "scene": self.scene,
            "user_input": self.user_input,
            "eris_action": self.eris_action,
            "outcome": self.outcome,
            "tags": " ".join(self.tags),
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class EpisodicMemoryStore:
    def __init__(self, path: str, *, max_items: int = 200):
        self.path = path
        self.max_items = max(20, int(max_items))

    def load(self) -> list[EpisodicMemoryItem]:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return []
        return [EpisodicMemoryItem.from_dict(item) for item in raw_items if isinstance(item, dict)]

    def save(self, items: list[EpisodicMemoryItem]) -> None:
        with locked_path(self.path):
            # Recall/access decay may save a snapshot obtained before distillation.
            # Preserve its consumer checkpoint when the source content is identical.
            current = {x.id: x for x in self.load()}
            for item in items:
                old = current.get(item.id)
                if old and old.metadata.get("distilled_hash") == distillation_hash(item.to_dict()):
                    item.metadata["distilled_hash"] = old.metadata["distilled_hash"]
                    item.metadata["distill_decision"] = old.metadata.get("distill_decision", "accepted")
                    if "distilled" not in item.tags:
                        item.tags.append("distilled")
            items = self._trim(items)
            atomic_write_json(self.path, {"version": 1, "items": [item.to_dict() for item in items]})

    def mark_distilled(self, snapshots: list[dict[str, Any]], *, decision: str = "accepted") -> int:
        with locked_path(self.path):
            wanted = {row["id"]: distillation_hash(row) for row in snapshots}
            items = self.load()
            changed = 0
            for item in items:
                fingerprint = distillation_hash(item.to_dict())
                if wanted.get(item.id) == fingerprint:
                    item.metadata["distilled_hash"] = fingerprint
                    item.metadata["distill_decision"] = decision
                    if "distilled" not in item.tags:
                        item.tags.append("distilled")
                    changed += 1
            if changed:
                self.save(items)
            return changed

    def distillation_sources_current(self, snapshots: list[dict[str, Any]]) -> bool:
        with locked_path(self.path):
            current = {x.id: distillation_hash(x.to_dict()) for x in self.load()}
            return all(current.get(row["id"]) == distillation_hash(row) for row in snapshots)

    def apply_commit(self, request: Any, *, frame: Any = None) -> EpisodicMemoryItem | None:
        with locked_path(self.path):
            return self._apply_commit(request, frame=frame)

    def _apply_commit(self, request: Any, *, frame: Any = None) -> EpisodicMemoryItem | None:
        content = compact_text(getattr(request, "content", "") or "", limit=600)
        if not content:
            return None
        item = EpisodicMemoryItem(
            id=f"epi_{uuid.uuid4().hex[:12]}",
            timestamp=iso_now(),
            frame_id=str(getattr(frame, "frame_id", "") or getattr(request, "frame_id", "") or ""),
            summary=content,
            scene=compact_text(getattr(request, "reason", "") or "", limit=280),
            confidence=clamp01(float(getattr(request, "confidence", 0.65) or 0.65)),
            salience=clamp01(float(getattr(request, "salience", 0.0) or 0.0)),
            evidence_ids=[str(eid) for eid in getattr(request, "evidence_ids", []) or [] if str(eid).strip()],
            tags=["commit", str(getattr(request, "target", "episodic_memory") or "episodic_memory")],
            decay_policy=str(getattr(request, "decay_policy", "normal") or "normal"),
        )
        items = self.load()
        # Dedup nhẹ: nếu đã có item với summary na ná trong 48h gần nhất thì merge thay vì tạo mới.
        # Episodic không dedup cứng như semantic vì sự kiện có thể lặp có chủ đích,
        # nhưng log spam (cùng 1 câu commit lặp lại) thì không cần giữ N bản.
        now_ts = float(time.time())
        for existing in items[-24:]:  # Chỉ xét 24 item gần nhất để tránh O(n²)
            try:
                age = now_ts - parse_timestamp(existing.timestamp)
            except Exception:
                age = 999999
            if age > 172800:  # > 48h thì không merge
                continue
            sim = weighted_jaccard(
                weighted_counter(normalized_tokens(content)),
                weighted_counter(normalized_tokens(existing.summary)),
            )
            if sim >= 0.85:
                # Merge: tăng salience/confidence nhẹ, cập nhật timestamp
                existing.confidence = clamp01(max(existing.confidence, item.confidence) + 0.02)
                existing.salience = clamp01(max(existing.salience, item.salience) + 0.01)
                existing.access_count = int(existing.access_count or 0) + 1
                for eid in item.evidence_ids:
                    if eid not in existing.evidence_ids:
                        existing.evidence_ids.append(eid)
                self.save(items)
                return existing
        items.append(item)
        self.save(items)
        return item

    def recall(self, query: str = "", *, limit: int = 6) -> list[EpisodicMemoryItem]:
        """Recall episode theo relevance score có bằng chứng.

        Bản cũ:
            tokens = query.lower().split()
            overlap = sum(1 for token in tokens if token in hay)

        Lỗi:
            - "py" match mọi chỗ có py.
            - "lỗi" kéo mọi episode có chữ lỗi.
            - substring làm token ngắn nguy hiểm.
            - không phân biệt tag, summary, outcome, user_input.
            - không có ngưỡng tối thiểu cho query yếu.

        Bản mới:
            - query không có token chất lượng => trả recent items.
            - token match đúng token, không substring.
            - field có weight riêng.
            - phrase/bigram/tag/anchor match được cộng điểm.
            - single weak token không đủ recall, trừ khi match tag/anchor rõ.
        """
        items = self.load()
        limit = max(1, int(limit or 6))

        query_norm = normalize_text(query)
        query_tokens = normalized_tokens(query)
        if not query_tokens:
            return items[-limit:]

        query_counter = weighted_counter(query_tokens)
        query_bigrams = token_bigrams(query_tokens)

        scored: list[tuple[float, EpisodicMemoryItem, RecallScore]] = []
        for item in items:
            score = self._score_item(
                item,
                query_norm=query_norm,
                query_tokens=query_tokens,
                query_counter=query_counter,
                query_bigrams=query_bigrams,
            )
            if score.total > 0.0:
                scored.append((score.total, item, score))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        selected = [item for _, item, _ in scored[:limit]]
        if selected:
            self._mark_accessed(selected)
        return selected

    def recall_with_scores(self, query: str = "", *, limit: int = 6) -> list[tuple[EpisodicMemoryItem, RecallScore]]:
        """Debug recall: trả cả item và breakdown score.

        Dùng khi muốn biết vì sao ERIS kéo ký ức nào lên.
        Runtime bình thường có thể chỉ dùng recall().
        """
        items = self.load()
        limit = max(1, int(limit or 6))
        query_norm = normalize_text(query)
        query_tokens = normalized_tokens(query)
        if not query_tokens:
            recent = items[-limit:]
            return [
                (
                    item,
                    RecallScore(
                        total=0.01,
                        recency=recency_boost(item.timestamp),
                        matched_terms=(),
                    ),
                )
                for item in recent
            ]

        query_counter = weighted_counter(query_tokens)
        query_bigrams = token_bigrams(query_tokens)

        scored: list[tuple[float, EpisodicMemoryItem, RecallScore]] = []
        for item in items:
            score = self._score_item(
                item,
                query_norm=query_norm,
                query_tokens=query_tokens,
                query_counter=query_counter,
                query_bigrams=query_bigrams,
            )
            if score.total > 0.0:
                scored.append((score.total, item, score))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected = [(item, score) for _, item, score in scored[:limit]]
        if selected:
            self._mark_accessed([item for item, _ in selected])
        return selected

    def _score_item(
        self,
        item: EpisodicMemoryItem,
        *,
        query_norm: str,
        query_tokens: tuple[str, ...],
        query_counter: Counter[str],
        query_bigrams: set[tuple[str, str]],
    ) -> RecallScore:
        """Chấm một episodic item.

        Điểm tổng là relevance + small priors.
        Priors như salience/confidence/recency không được tạo recall nếu relevance = 0.
        """
        field_weights = {
            "summary": 1.00,
            "user_input": 0.92,
            "outcome": 0.82,
            "eris_action": 0.72,
            "scene": 0.52,
            "tags": 1.10,
        }

        lexical_score = 0.0
        phrase_score = 0.0
        bigram_score = 0.0
        tag_score = 0.0
        matched_terms: set[str] = set()

        for field_name, field_text in item.search_fields().items():
            if not field_text:
                continue
            weight = field_weights.get(field_name, 0.5)
            field_norm = normalize_text(field_text)
            field_tokens = normalized_tokens(field_text)
            field_counter = weighted_counter(field_tokens)

            lexical = weighted_jaccard(query_counter, field_counter)
            lexical_score = max(lexical_score, lexical * weight)

            phrase = phrase_match_score(query_norm, field_norm)
            phrase_score = max(phrase_score, phrase * weight)

            field_bigrams = token_bigrams(field_tokens)
            if query_bigrams and field_bigrams:
                bigram = len(query_bigrams & field_bigrams) / max(1, len(query_bigrams))
                bigram_score = max(bigram_score, bigram * weight)

            if field_name == "tags":
                tag_overlap = len(set(query_tokens) & set(field_tokens)) / max(1, len(set(query_tokens)))
                tag_score = max(tag_score, tag_overlap)

            for token in set(query_tokens) & set(field_tokens):
                matched_terms.add(token)

        # Nếu query chỉ còn một token yếu và không match tag/phrase/bigram, không recall.
        # Đây là điểm chặn chính cho bug "token nào xuất hiện trong chuỗi là cộng điểm".
        single_token_query = len(set(query_tokens)) <= 1
        weak_single = single_token_query and phrase_score < 0.45 and bigram_score <= 0.0 and tag_score < 0.8
        if weak_single:
            return RecallScore(total=0.0, matched_terms=tuple(sorted(matched_terms)))

        relevance = clamp01(
            lexical_score * 0.50
            + phrase_score * 0.24
            + bigram_score * 0.18
            + tag_score * 0.08
        )

        # Ngưỡng tối thiểu: không có relevance đủ thì priors không được cứu.
        if relevance < 0.055:
            return RecallScore(
                total=0.0,
                lexical=lexical_score,
                phrase=phrase_score,
                bigram=bigram_score,
                tag=tag_score,
                matched_terms=tuple(sorted(matched_terms)),
            )

        salience_prior = clamp01(item.salience) * 0.12
        confidence_prior = clamp01(item.confidence) * 0.08
        recent_prior = recency_boost(item.timestamp)
        access_penalty = min(0.08, max(0, int(item.access_count or 0)) * 0.012)

        decay_prior = {
            "none": 0.04,
            "slow": 0.025,
            "normal": 0.0,
            "fast": -0.025,
            "session_only": -0.045,
        }.get(str(item.decay_policy or "normal"), 0.0)

        total = clamp01(relevance + salience_prior + confidence_prior + recent_prior + decay_prior - access_penalty)

        return RecallScore(
            total=total,
            lexical=lexical_score,
            phrase=phrase_score,
            bigram=bigram_score,
            tag=tag_score,
            salience=salience_prior,
            confidence=confidence_prior,
            recency=recent_prior,
            access_penalty=access_penalty,
            matched_terms=tuple(sorted(matched_terms)),
        )

    def _mark_accessed(self, selected: list[EpisodicMemoryItem]) -> None:
        with locked_path(self.path):
            self._mark_accessed_locked(selected)

    def _mark_accessed_locked(self, selected: list[EpisodicMemoryItem]) -> None:
        """Tăng access_count cho item đã recall.

        Mục đích:
        - Debug xem ký ức nào bị kéo lên nhiều.
        - Tạo penalty rất nhẹ để một ký ức không thống trị mãi.
        """
        selected_ids = {item.id for item in selected}
        if not selected_ids:
            return
        items = self.load()
        changed = False
        for item in items:
            if item.id in selected_ids:
                item.access_count = int(item.access_count or 0) + 1
                changed = True
        if changed:
            self.save(items)

    def _trim(self, items: list[EpisodicMemoryItem]) -> list[EpisodicMemoryItem]:
        def weight(item: EpisodicMemoryItem) -> tuple[float, str]:
            decay_bonus = {"slow": 0.25, "none": 0.4, "normal": 0.0, "fast": -0.2, "session_only": -0.5}.get(item.decay_policy, 0.0)
            return (item.confidence + item.salience + decay_bonus, item.timestamp)

        items.sort(key=weight, reverse=True)
        return items[: self.max_items]
