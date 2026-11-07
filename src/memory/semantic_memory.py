"""Semantic Memory: bộ nhớ fact đã được chưng cất cho ERIS.

File này cố tình KHÔNG làm event log.
Semantic memory chỉ nên giữ những thứ đã đủ ổn định để ERIS có thể nhớ gọn:
    - fact về user, ERIS, project, thế giới, kỹ thuật
    - sở thích / thói quen / quy ước làm việc
    - claim đã có evidence và confidence

Runtime event lẻ, transcript dài, log từng lần chạy... phải đi qua EventLedger hoặc EpisodicSummary.
Nếu nhét hết vào semantic memory thì cái gọi là "trí nhớ" sẽ thành một bãi JSON có tham vọng tâm linh.

Thiết kế mới:
    SemanticMemoryItem
    SemanticRecallCue
    SemanticRecallHit
    normalize helpers
    fact_key derivation
    lexical similarity fallback
    optional embedding index hook
    apply_commit()
    recall()
    merge/update/access stats
    prune/sort policy

Tương thích API cũ:
    store = SemanticMemoryStore(path)
    store.apply_commit(request)
    store.recall(query, limit=8) -> list[SemanticMemoryItem]

Nâng cấp optional:
    SemanticMemoryStore(path, embedding_index=...)

embedding_index chỉ cần có method:
    search(query: str, items: list[Any], *, text_fn: Callable[[Any], str], limit: int)
        -> list[tuple[float, item]]

Embedding chỉ dùng để lấy candidate. Quyền quyết định cuối vẫn thuộc domain/fact_key/confidence/evidence.
Vector gần không có nghĩa là đúng. Nó chỉ có nghĩa là hai câu nằm gần nhau trong một không gian toán học,
điều mà con người rất thích nhầm với hiểu biết.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable, Literal, Mapping, Optional, Sequence

from .predicate_catalog import BUILTIN_PREDICATES
from .predicate_registry import PredicateRegistry, get_default_registry

SemanticDomain = Literal["user", "self", "world", "technical", "project", "relationship", "unknown"]
SemanticStatus = Literal["active", "tentative", "contradicted", "rejected", "archived"]

# Compatibility snapshot. Quy tắc replacement thật nằm trong registry qua
# cardinality=single, không còn một danh sách fact phải vá thủ công ở file này.
CURRENT_FACT_KEYS = frozenset(
    item.fact_key
    for item in BUILTIN_PREDICATES
    if item.fact_key and item.ontology.cardinality == "single"
)



# =============================================================================
# 1. Helpers nền: thời gian, ghi file, normalize text
# =============================================================================


def iso_now() -> str:
    """Thời gian dạng ISO đơn giản, đủ ổn cho JSON memory local."""

    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _safe_epoch_from_iso(value: str) -> float:
    """Parse timestamp best-effort.

    Không dùng để làm logic chính xác tuyệt đối. Chỉ dùng cho recency bonus/sort.
    Nếu parse fail thì trả 0 để memory cũ không tự nhiên thắng.
    """

    if not value:
        return 0.0
    try:
        # time.strptime xử lý format iso_now() hiện tại: 2026-05-27T15:20:30+0700
        return time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%S%z"))
    except Exception:
        return 0.0


def compact_text(value: Any, *, limit: int = 360) -> str:
    """Nén text thành một dòng ngắn để lưu vào memory.

    Semantic memory không nên chứa đoạn văn dài. Nếu cần lưu raw dài, đưa sang event ledger.
    """

    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clamp01(value: Any, default: float = 0.0) -> float:
    """Ép giá trị về khoảng 0..1, tránh NaN/inf làm bẩn score."""

    try:
        number = float(value)
    except Exception:
        number = default
    if math.isnan(number) or math.isinf(number):
        number = default
    return max(0.0, min(1.0, number))


from .json_store_io import atomic_write_json


def strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt để so khớp alias/fact_key ổn hơn.

    Lưu ý: không dùng bản bỏ dấu làm claim public. Chỉ dùng cho indexing/search.
    """

    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_text(text: str, *, keep_underscore: bool = True) -> str:
    """Normalize text cho search/fingerprint.

    - lowercase
    - bỏ dấu tiếng Việt
    - bỏ punctuation thừa
    - co whitespace

    Đây là lexical normalization, không phải semantic understanding. Đừng phong thần cho regex.
    """

    text = strip_accents(str(text or "").lower())
    if keep_underscore:
        text = re.sub(r"[^0-9a-zA-Z_\s]+", " ", text)
    else:
        text = re.sub(r"[^0-9a-zA-Z\s]+", " ", text)
    return " ".join(text.split())


def dedupe_key(text: str) -> str:
    """Key tương thích bản cũ.

    Không còn là cơ chế dedupe chính, nhưng vẫn giữ để code khác đang gọi không vỡ.
    """

    return normalize_text(compact_text(text, limit=400))[:220]


def token_set(text: str) -> set[str]:
    return {tok for tok in normalize_text(text).split() if tok}


def char_ngrams(text: str, n: int = 4) -> set[str]:
    normalized = normalize_text(text, keep_underscore=False)
    if not normalized:
        return set()
    if len(normalized) <= n:
        return {normalized}
    return {normalized[i : i + n] for i in range(0, len(normalized) - n + 1)}


def word_ngrams(text: str, n: int = 2) -> set[str]:
    tokens = normalize_text(text).split()
    if not tokens:
        return set()
    if len(tokens) <= n:
        return {" ".join(tokens)}
    return {" ".join(tokens[i : i + n]) for i in range(0, len(tokens) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def sequence_ratio(a: str, b: str) -> float:
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def stable_hash(text: str) -> str:
    """Hash ngắn để biết embedding_text đã đổi chưa."""

    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def unique_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _semantic_value_fingerprint(value: Any) -> str:
    """Fingerprint ổn định cho object value mà không phụ thuộc living_user.

    Semantic memory nằm thấp hơn living-user layer nên không import ngược models,
    tránh tạo vòng phụ thuộc chỉ để so hai giá trị.
    """
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)


# =============================================================================
# 2. Recall cue / hit: query không chỉ là text, nó có ý định nhớ-lại
# =============================================================================


@dataclass(slots=True)
class SemanticRecallCue:
    """Cue truy xuất semantic memory.

    Thay vì lấy query rồi đếm token overlap, ta tách query thành vài tín hiệu:
        - domain muốn tìm: user/self/project/technical...
        - fact_key nếu có: user.name, user.preference.reply_style...
        - aliases: từ đồng nghĩa/biến thể để rerank
        - strict_domain: nếu true, domain sai bị phạt mạnh
        - wants_profile_fact: query đòi fact cá nhân, tránh kéo nhầm fact project/world

    Đây vẫn là parser nhẹ, không phải LLM. Nhưng nó đủ để memory không cư xử như Ctrl+F tập thể.
    """

    query: str
    normalized: str
    domain: SemanticDomain = "unknown"
    fact_key: str = ""
    aliases: list[str] = field(default_factory=list)
    wants_profile_fact: bool = False
    strict_domain: bool = False
    evidence: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> set[str]:
        return token_set(self.normalized)

    @property
    def embedding_query(self) -> str:
        """Query đưa cho embedding index.

        Nếu có fact_key/domain thì thêm vào để vector search bớt mù ngữ cảnh.
        """

        parts = [self.fact_key, self.domain if self.domain != "unknown" else "", " ".join(self.aliases), self.query]
        return compact_text(" | ".join(part for part in parts if part), limit=700)


@dataclass(slots=True)
class SemanticRecallHit:
    """Kết quả recall có score và lý do.

    recall() vẫn trả list item để tương thích cũ.
    Nếu cần debug, gọi recall_hits() để thấy vì sao item được kéo lên.
    """

    item: "SemanticMemoryItem"
    score: float
    confidence: float
    vector_score: float = 0.0
    lexical_score: float = 0.0
    key_score: float = 0.0
    domain_score: float = 0.0
    alias_score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item.to_dict(),
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "vector_score": round(self.vector_score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "key_score": round(self.key_score, 4),
            "domain_score": round(self.domain_score, 4),
            "alias_score": round(self.alias_score, 4),
            "reasons": list(self.reasons),
        }

    def recall_line(self) -> str:
        return compact_text(
            f"{self.item.recall_line()} [recall_score={self.score:.2f}; reasons={','.join(self.reasons[:4])}]",
            limit=360,
        )


# =============================================================================
# 3. Fact profile: chưng cất claim thành domain/fact_key/aliases/importance
# =============================================================================


@dataclass(slots=True)
class FactProfile:
    """Thông tin derive từ claim/request trước khi lưu.

    fact_key là điểm quan trọng nhất. Nó biến các câu paraphrase thành cùng một fact.
    Ví dụ:
        "Người dùng tên Gtvkun"
        "bạn tên là Gtvkun"
        "user name: Gtvkun"
    đều nên về fact_key="user.name".
    """

    domain: SemanticDomain = "unknown"
    fact_key: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    importance: float = 0.50
    evidence: list[str] = field(default_factory=list)


# Pattern phrase normalized không dấu.
# Các pattern này không quyết định output public. Chúng chỉ hỗ trợ lưu fact có key.
USER_NAME_PATTERNS = (
    r"\bnguoi dung ten\b",
    r"\bban ten\b",
    r"\bten cua nguoi dung\b",
    r"\bten cua ban\b",
    r"\buser name\b",
    r"\bpreferred name\b",
    r"\bgoi nguoi dung la\b",
)

USER_NICKNAME_PATTERNS = (
    r"\bbiet danh\b",
    r"\bbiet hieu\b",
    r"\bnickname\b",
    r"\bten goi\b",
)

USER_BIRTH_PATTERNS = (
    r"\bthang sinh\b",
    r"\bsinh thang\b",
    r"\bbirth month\b",
    r"\bbirthday\b",
)

USER_STYLE_PATTERNS = (
    r"\bkieu tra loi\b",
    r"\bstyle tra loi\b",
    r"\bresponse style\b",
    r"\btra loi ngan\b",
    r"\btra loi dai\b",
    r"\bchu thich day du\b",
    r"\bthich giai thich\b",
)

PROJECT_ERIS_PATTERNS = (
    r"\beris\b",
    r"\bruntime\b",
    r"\bcognitive\b",
    r"\bpipeline\b",
    r"\bmemory\b",
    r"\bsemantic memory\b",
    r"\bworking memory\b",
    r"\bglobal workspace\b",
)

SELF_IDENTITY_PATTERNS = (
    r"\beris la\b",
    r"\bself identity\b",
    r"\bruntime artificial observer\b",
    r"\bkhong phai nguoi\b",
)

RELATIONSHIP_PATTERNS = (
    r"\bquan he\b",
    r"\brelationship\b",
    r"\bfamiliarity\b",
    r"\btrust\b",
    r"\brupture\b",
    r"\brepair\b",
    r"\bpreferred distance\b",
)

TECHNICAL_PATTERNS = (
    r"\bpython\b",
    r"\bgodot\b",
    r"\bc#\b",
    r"\bcode\b",
    r"\bdebug\b",
    r"\bmodule\b",
    r"\bclass\b",
    r"\bfunction\b",
    r"\bapi\b",
    r"\bjson\b",
)


def _matches_any(normalized: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)


# =============================================================================
# 4. Memory item
# =============================================================================


@dataclass(slots=True)
class SemanticMemoryItem:
    id: str
    claim: str
    domain: SemanticDomain = "unknown"
    confidence: float = 0.70
    source: str = "commit"
    evidence_ids: list[str] = field(default_factory=list)
    observations: int = 1
    status: SemanticStatus = "active"
    first_seen_at: str = ""
    updated_at: str = ""
    decay_policy: str = "slow"
    contradiction_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "claim": self.claim,
            "domain": self.domain,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "evidence_ids": list(self.evidence_ids),
            "observations": int(self.observations),
            "status": self.status,
            "first_seen_at": self.first_seen_at,
            "updated_at": self.updated_at,
            "decay_policy": self.decay_policy,
            "contradiction_ids": list(self.contradiction_ids),
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticMemoryItem":
        metadata = dict(data.get("metadata") or {})

        # Migration nhẹ: nếu dữ liệu tương lai/cũ có fact_key ở top-level thì kéo vào metadata.
        for key in ("fact_key", "aliases", "importance", "last_accessed_at", "access_count", "embedding_text_hash"):
            if key in data and key not in metadata:
                metadata[key] = data.get(key)

        status = str(data.get("status") or "active")
        if status not in {"active", "tentative", "contradicted", "rejected", "archived"}:
            status = "active"

        domain = str(data.get("domain") or "unknown")
        if domain not in {"user", "self", "world", "technical", "project", "relationship", "unknown"}:
            domain = "unknown"

        return cls(
            id=str(data.get("id") or f"sem_{uuid.uuid4().hex[:12]}"),
            claim=compact_text(data.get("claim") or data.get("text") or "", limit=520),
            domain=domain,  # type: ignore[arg-type]
            confidence=clamp01(data.get("confidence", 0.70), default=0.70),
            source=str(data.get("source") or "commit"),
            evidence_ids=[str(item) for item in data.get("evidence_ids") or data.get("source_ids") or [] if str(item).strip()],
            observations=max(1, int(data.get("observations", 1) or 1)),
            status=status,  # type: ignore[arg-type]
            first_seen_at=str(data.get("first_seen_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            decay_policy=str(data.get("decay_policy") or "slow"),
            contradiction_ids=[str(item) for item in data.get("contradiction_ids") or [] if str(item).strip()],
            tags=[str(item) for item in data.get("tags") or [] if str(item).strip()],
            metadata=metadata,
        )

    @property
    def fact_key(self) -> str:
        return str(self.metadata.get("fact_key") or "")

    @property
    def aliases(self) -> list[str]:
        raw = self.metadata.get("aliases") or []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, Iterable):
            return [str(item) for item in raw if str(item).strip()]
        return []

    @property
    def importance(self) -> float:
        return clamp01(self.metadata.get("importance", 0.50), default=0.50)

    @property
    def access_count(self) -> int:
        try:
            return max(0, int(self.metadata.get("access_count", 0) or 0))
        except Exception:
            return 0

    @property
    def last_accessed_at(self) -> str:
        return str(self.metadata.get("last_accessed_at") or "")

    @property
    def raw_user_text(self) -> str:
        return str(self.metadata.get("raw_user_text") or "")

    @property
    def semantic_value(self) -> str:
        metadata = _mapping(self.metadata)
        raw_content = _mapping(metadata.get("raw_content"))
        raw_meta = _mapping(raw_content.get("metadata"))
        semantic_claim = _mapping(metadata.get("semantic_claim"))
        for value in (
            metadata.get("value"),
            raw_meta.get("value"),
            raw_content.get("value"),
            semantic_claim.get("object_value"),
            semantic_claim.get("object_text"),
        ):
            clean = str(value or "").strip()
            if clean:
                return clean
        return ""

    def recall_text_parts(self) -> list[str]:
        metadata = _mapping(self.metadata)
        raw_content = _mapping(metadata.get("raw_content"))
        raw_meta = _mapping(raw_content.get("metadata"))
        semantic_claim = _mapping(metadata.get("semantic_claim"))
        value = self.semantic_value

        parts = [
            self.claim,
            self.domain,
            " ".join(self.tags),
            self.fact_key,
            " ".join(self.aliases),
            self.raw_user_text,
            str(metadata.get("fact_key") or ""),
            str(metadata.get("value") or ""),
            str(raw_meta.get("value") or ""),
            str(raw_content.get("value") or ""),
            str(raw_content.get("fact") or ""),
            str(raw_content.get("surface") or ""),
            str(semantic_claim.get("predicate") or ""),
            str(semantic_claim.get("object_value") or ""),
            str(semantic_claim.get("object_text") or ""),
        ]

        if value:
            # Mở rộng embedding/lexical bằng alias thực của item. Không cần biết
            # đây là tên, giới tính hay một predicate model vừa học tuần sau.
            for alias in self.aliases[:12]:
                clean_alias = str(alias or "").strip()
                if clean_alias:
                    parts.extend((f"{clean_alias} {value}", f"{clean_alias} là {value}"))

        return [compact_text(part, limit=240) for part in parts if str(part or "").strip()]

    def recall_line(self) -> str:
        fact = f", key={self.fact_key}" if self.fact_key else ""
        return compact_text(
            f"{self.domain}: {self.claim} (confidence={self.confidence:.2f}, status={self.status}{fact})",
            limit=320,
        )


# =============================================================================
# 5. Store chính
# =============================================================================


class SemanticMemoryStore:
    """Store semantic fact dạng JSON, có typed recall và optional embedding hook.

    Đây là WARM/HOT fact store:
        - load nhanh
        - claim ngắn
        - metadata có fact_key/importance/access stats
        - recall bằng hybrid score

    Không chứa raw event log. Không chứa transcript dài. Không chứa mọi thứ chỉ vì nó đã xảy ra.
    """

    def __init__(
        self,
        path: str,
        *,
        max_items: int = 300,
        embedding_index: Any | None = None,
        min_recall_score: float = 0.52,
        registry: PredicateRegistry | None = None,
    ):
        self.path = path
        self.max_items = max(30, int(max_items))
        self.embedding_index = embedding_index
        self.min_recall_score = float(min_recall_score)
        self.registry = registry or get_default_registry()

    # ------------------------------------------------------------------
    # Load/save/prune
    # ------------------------------------------------------------------

    def load(self) -> list[SemanticMemoryItem]:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return []
        items = [SemanticMemoryItem.from_dict(item) for item in raw_items if isinstance(item, dict)]
        # Predicate provisional được phục hồi từ chính semantic memory. Nhờ vậy
        # restart không biến một thuộc tính do model học được thành người lạ.
        self.registry.hydrate_from_memory_items(items)
        return self._sorted_items(items)

    def save(self, items: list[SemanticMemoryItem]) -> None:
        """Lưu memory sau khi prune/sort.

        Sort policy ưu tiên:
            1. active > tentative > contradicted/rejected/archived
            2. importance
            3. confidence
            4. access_count / observations
            5. recency update/access

        Tức là user.name quan trọng không bị thua chỉ vì nó ít lặp. Bộ nhớ không cần bắt user tự giới thiệu 10 lần.
        """

        pruned = self._prune_items(items)
        payload = {
            "version": 2,
            "kind": "semantic_memory",
            "updated_at": iso_now(),
            "items": [item.to_dict() for item in pruned],
        }
        atomic_write_json(self.path, payload)

    def _prune_items(self, items: list[SemanticMemoryItem]) -> list[SemanticMemoryItem]:
        """Prune memory yếu nếu vượt max_items.

        Không xóa dựa vào thời gian đơn thuần. Memory quan trọng/có key vẫn được giữ.
        """

        # Dedupe phòng thủ theo id, giữ item có sort score cao hơn.
        by_id: dict[str, SemanticMemoryItem] = {}
        for item in items:
            old = by_id.get(item.id)
            if old is None or self._retention_score(item) > self._retention_score(old):
                by_id[item.id] = item

        sorted_items = self._sorted_items(list(by_id.values()))
        if len(sorted_items) <= self.max_items:
            return sorted_items

        # Luôn giữ key fact quan trọng nếu có thể.
        protected: list[SemanticMemoryItem] = []
        normal: list[SemanticMemoryItem] = []
        for item in sorted_items:
            if item.fact_key and item.importance >= 0.70 and item.status in {"active", "tentative"}:
                protected.append(item)
            else:
                normal.append(item)

        room = self.max_items
        kept = protected[:room]
        room -= len(kept)
        if room > 0:
            kept.extend(normal[:room])
        return self._sorted_items(kept)

    def _sorted_items(self, items: list[SemanticMemoryItem]) -> list[SemanticMemoryItem]:
        return sorted(items, key=self._retention_score, reverse=True)

    def _retention_score(self, item: SemanticMemoryItem) -> float:
        status_weight = {
            "active": 1.00,
            "tentative": 0.78,
            "contradicted": 0.28,
            "rejected": 0.05,
            "archived": 0.12,
        }.get(item.status, 0.40)
        updated = max(_safe_epoch_from_iso(item.updated_at), _safe_epoch_from_iso(item.last_accessed_at))
        # Recency bonus rất nhẹ, vì semantic fact không nên chết chỉ vì cũ.
        age_days = max(0.0, (time.time() - updated) / 86400.0) if updated > 0 else 999.0
        recency = 1.0 / (1.0 + age_days / 60.0)
        key_bonus = 0.18 if item.fact_key else 0.0
        contradiction_penalty = 0.25 if item.contradiction_ids else 0.0
        return (
            status_weight * 1.10
            + item.importance * 0.85
            + item.confidence * 0.75
            + min(0.30, item.observations * 0.025)
            + min(0.22, item.access_count * 0.018)
            + recency * 0.18
            + key_bonus
            - contradiction_penalty
        )

    # ------------------------------------------------------------------
    # Commit path
    # ------------------------------------------------------------------

    def apply_commit(self, request: Any) -> SemanticMemoryItem | None:
        """Áp một StateCommitRequest vào semantic memory.

        Commit không merge bằng exact text nữa.
        Flow mới:
            request -> claim/confidence/evidence
                    -> derive FactProfile
                    -> tìm item merge bằng fact_key trước
                    -> nếu không có key, dùng similarity fallback có domain gate
                    -> update item hoặc tạo item mới
        """

        claim = compact_text(getattr(request, "content", "") or "", limit=520)
        if not claim:
            return None

        now = iso_now()
        confidence = clamp01(getattr(request, "confidence", 0.70), default=0.70)
        evidence_ids = [str(item) for item in getattr(request, "evidence_ids", []) or [] if str(item).strip()]
        target = getattr(request, "target", "")
        reason = compact_text(getattr(request, "reason", "") or "", limit=280)
        request_metadata = _mapping(getattr(request, "metadata", None))
        request_tags = [str(item) for item in getattr(request, "tags", []) or [] if str(item).strip()]

        profile = self._derive_fact_profile(
            claim,
            target=target,
            reason=reason,
            request_metadata=request_metadata,
            request_tags=request_tags,
        )

        status: SemanticStatus = "active" if confidence >= 0.72 else "tentative"
        items = self.load()

        from .claim_provenance import provenance_of, source_roots
        provenance = provenance_of(request_metadata)
        roots = source_roots(request_metadata)
        correction_ref = str(provenance.get("corrects_claim_ref") or "")
        # Correction được nối qua ID record thật; không đoán từ đoạn văn.
        correction_target = next((i for i in items if i.id == correction_ref and i.status in {"active", "tentative"}), None) if correction_ref else None
        if correction_ref:
            # Không cho correction trỏ sai ID hoặc sang fact_key khác; nó có thể
            # phá ký ức không liên quan dù chính lời user là hợp lệ.
            if correction_target is None:
                raise ValueError("correction_target_missing_or_inactive")
            if profile.fact_key and correction_target.fact_key and profile.fact_key != correction_target.fact_key:
                raise ValueError("correction_target_fact_key_mismatch")
        merge_target = correction_target or self._find_merge_target(items, claim=claim, profile=profile, confidence=confidence)
        if merge_target is not None:
            old_roots = set(merge_target.metadata.get("lineage_root_ids") or ())
            # Replay, observer summary của CÙNG root và legacy request không đủ
            # provenance không được tự nâng observations/confidence.
            # Không có typed root thì dù request lặp bao nhiêu lần cũng không
            # tạo ra nguồn xác nhận độc lập. Quan trọng cho legacy và observer
            # chạy lại sau reload, kể cả khi evidence_ids thay đổi UUID.
            if not roots or (old_roots and roots.issubset(old_roots)):
                return merge_target
            if roots and not old_roots and merge_target.metadata.get("claim_provenance"):
                # Legacy/unresolved lineage trong record không có root: không
                # tự suy diễn đây là một bằng chứng mới độc lập.
                return merge_target
            self._merge_item(
                merge_target,
                claim=claim,
                confidence=confidence,
                evidence_ids=evidence_ids,
                profile=profile,
                reason=reason,
                request_metadata=request_metadata,
                now=now,
            )
            self.save(items)
            return merge_target

        metadata = self._build_metadata(
            claim=claim,
            profile=profile,
            reason=reason,
            request_metadata=request_metadata,
        )
        # Full ancestry nằm trong persisted metadata, KHÔNG trong evidence_ids
        # vốn được cắt cho prompt/recall.
        metadata["lineage_root_ids"] = sorted(roots)
        if correction_ref:
            metadata["corrects_claim_ref"] = correction_ref
        item = SemanticMemoryItem(
            id=f"sem_{uuid.uuid4().hex[:12]}",
            claim=claim,
            domain=profile.domain,
            confidence=confidence,
            source="state_commit",
            evidence_ids=evidence_ids[-12:],
            observations=1,
            status=status,
            first_seen_at=now,
            updated_at=now,
            decay_policy=str(getattr(request, "decay_policy", "slow") or "slow"),
            tags=unique_keep_order(["commit", *profile.tags, *request_tags]),
            metadata=metadata,
        )
        items.append(item)
        self.save(items)
        return item

    def _find_merge_target(
        self,
        items: list[SemanticMemoryItem],
        *,
        claim: str,
        profile: FactProfile,
        confidence: float,
    ) -> SemanticMemoryItem | None:
        """Tìm item nên merge.

        Thứ tự:
            1. fact_key trùng và domain tương thích
            2. exact normalized key trùng
            3. similarity cao trong cùng domain khi cả hai chưa có fact_key

        Không merge mù nếu fact_key khác. Đó là cách tạo ký ức lai tạp rất tự tin.
        """

        active_items = [item for item in items if item.status in {"active", "tentative"}]

        if profile.fact_key:
            keyed = [item for item in active_items if item.fact_key == profile.fact_key]
            if keyed:
                # Nếu có nhiều item cùng key, merge vào item có retention cao nhất.
                return max(keyed, key=self._retention_score)

        claim_key = dedupe_key(claim)
        for item in active_items:
            if dedupe_key(item.claim) == claim_key:
                return item

        # Similarity fallback chỉ dùng khi chưa có fact_key rõ. Nếu có fact_key mà không match,
        # tốt hơn là tạo item mới thay vì trộn nhầm.
        if profile.fact_key:
            return None

        best_item: SemanticMemoryItem | None = None
        best_score = 0.0
        for item in active_items:
            if item.fact_key:
                continue
            if profile.domain != "unknown" and item.domain != profile.domain:
                continue
            sim = self._claim_similarity(claim, item.claim)
            if sim > best_score:
                best_score = sim
                best_item = item

        if best_item is not None and best_score >= 0.88 and confidence >= 0.58:
            return best_item
        return None

    def _merge_item(
        self,
        item: SemanticMemoryItem,
        *,
        claim: str,
        confidence: float,
        evidence_ids: list[str],
        profile: FactProfile,
        reason: str,
        request_metadata: Mapping[str, Any],
        now: str,
    ) -> None:
        """Merge claim mới vào item cũ.

        Nếu cùng fact_key nhưng giá trị có vẻ mâu thuẫn, không overwrite sạch.
        Ta tăng contradiction marker để downstream biết phải cẩn thận.
        """

        key = profile.fact_key or item.fact_key
        old_value = item.semantic_value or self._extract_simple_value(item.claim, key)
        new_value = self._extract_metadata_value(request_metadata, key) or self._extract_simple_value(claim, key)
        from .claim_provenance import provenance_of, source_roots
        provenance = provenance_of(request_metadata)
        correction_ref = str(provenance.get("corrects_claim_ref") or "")
        # Explicit correction chỉ có giá trị sau khi qua durable user-source gate.
        replacement = (correction_ref == item.id) or self._is_current_fact_replacement(
            key,
            old_value=old_value,
            new_value=new_value,
            confidence=confidence,
        )
        conflict = False if replacement else self._detect_value_conflict(
            item,
            new_claim=claim,
            profile=profile,
            request_metadata=request_metadata,
        )
        old_claim = item.claim

        item.observations += 1
        # Confidence tăng chậm theo quan sát. Không để spam cùng fact làm confidence nhảy vô hạn.
        item.confidence = clamp01(max(item.confidence, confidence) + min(0.05, item.observations * 0.006))
        item.updated_at = now
        item.status = "active" if item.confidence >= 0.72 and item.status != "contradicted" else item.status
        item.evidence_ids = unique_keep_order([*item.evidence_ids, *evidence_ids])[-14:]
        item.tags = unique_keep_order([*item.tags, *profile.tags, "merged"])[-18:]

        # Nếu claim mới tự tin hơn hoặc claim cũ là mơ hồ, dùng claim mới làm surface chính.
        if replacement or (
            not conflict
            and (confidence >= item.confidence - 0.08)
            and self._claim_surface_quality(claim) >= self._claim_surface_quality(item.claim)
        ):
            item.claim = compact_text(claim, limit=520)

        metadata = dict(item.metadata or {})
        new_roots = source_roots(request_metadata)
        metadata["lineage_root_ids"] = sorted(set(metadata.get("lineage_root_ids") or ()) | new_roots)
        if request_metadata.get("claim_provenance"):
            metadata["claim_provenance"] = provenance
        if correction_ref == item.id:
            metadata["corrects_claim_ref"] = correction_ref
            metadata["last_update_policy"] = "explicit_user_correction"
        previous_claims = list(metadata.get("previous_claims") or [])
        if old_claim != item.claim:
            previous_claims.append(old_claim)
        if claim != item.claim:
            previous_claims.append(claim)
        metadata["previous_claims"] = unique_keep_order(previous_claims)[-8:]
        metadata["last_merge_reason"] = reason
        metadata["importance"] = max(clamp01(metadata.get("importance", 0.50), default=0.50), profile.importance)
        if profile.fact_key and not metadata.get("fact_key"):
            metadata["fact_key"] = profile.fact_key
        metadata["aliases"] = unique_keep_order([*metadata.get("aliases", []), *profile.aliases])[-16:]

        if replacement:
            previous_values = list(metadata.get("previous_values") or [])
            if old_value and normalize_text(old_value) != normalize_text(new_value):
                previous_values.append(old_value)
            metadata["previous_values"] = unique_keep_order(previous_values)[-8:]
            superseded = list(metadata.get("superseded_facts") or [])
            if old_value or old_claim:
                superseded.append(
                    {
                        "value": old_value,
                        "claim": old_claim,
                        "at": now,
                        "replaced_by": new_value,
                    }
                )
            metadata["superseded_facts"] = superseded[-8:]
            metadata["value"] = new_value
            metadata["current_value"] = new_value
            metadata["last_update_policy"] = "explicit_user_correction" if correction_ref == item.id else "current_fact_replacement"
            metadata["raw_user_text"] = str(request_metadata.get("raw_user_text") or metadata.get("raw_user_text") or "")
            for key_name in ("semantic_claim", "predicate", "subject", "object_type", "assertion", "modality", "temporal", "scope"):
                if key_name in request_metadata:
                    metadata[key_name] = request_metadata[key_name]
            item.status = "active" if item.confidence >= 0.55 else item.status
            item.contradiction_ids = []

        if conflict:
            contradiction_id = f"contr_{uuid.uuid4().hex[:10]}"
            item.contradiction_ids = unique_keep_order([*item.contradiction_ids, contradiction_id])[-8:]
            item.status = "contradicted" if confidence >= 0.72 else item.status
            metadata.setdefault("contradictions", [])
            contradictions = list(metadata.get("contradictions") or [])
            contradictions.append({"id": contradiction_id, "claim": claim, "at": now, "reason": "same_fact_key_value_conflict"})
            metadata["contradictions"] = contradictions[-8:]

        item.metadata = metadata
        item.metadata["embedding_text_hash"] = stable_hash(self.embedding_text_for_item(item))

    def _is_current_fact_replacement(
        self,
        fact_key: str,
        *,
        old_value: str,
        new_value: str,
        confidence: float,
    ) -> bool:
        if not self.registry.is_single_value_fact(fact_key):
            return False
        if not new_value:
            return False
        if confidence < 0.55:
            return False
        if not old_value:
            return True
        return normalize_text(old_value) != normalize_text(new_value)

    def _claim_surface_quality(self, claim: str) -> float:
        """Ước lượng claim nào đáng dùng làm surface chính.

        Claim quá ngắn/quá chung chung không nên overwrite claim cụ thể hơn.
        """

        normalized = normalize_text(claim)
        tokens = normalized.split()
        score = min(1.0, len(tokens) / 12.0)
        if any(tok in normalized for tok in ("user", "nguoi dung", "ban", "eris", "project")):
            score += 0.10
        if len(claim) > 220:
            score -= 0.08
        return clamp01(score)

    def _detect_value_conflict(
        self,
        item: SemanticMemoryItem,
        *,
        new_claim: str,
        profile: FactProfile,
        request_metadata: Mapping[str, Any],
    ) -> bool:
        """Bắt mâu thuẫn rõ cho vài fact_key có value đơn giản.

        Không cố làm natural language contradiction detection. Làm quá tay ở đây sẽ thành ToM rẻ tiền.
        """

        key = profile.fact_key or item.fact_key
        if not key:
            return False

        old_value = item.semantic_value or self._extract_simple_value(item.claim, key)
        new_value = self._extract_metadata_value(request_metadata, key) or self._extract_simple_value(new_claim, key)
        if not old_value or not new_value:
            return False
        return normalize_text(old_value) != normalize_text(new_value)

    def _extract_metadata_value(self, metadata: Mapping[str, Any], fact_key: str) -> str:
        data = _mapping(metadata)
        raw_content = _mapping(data.get("raw_content"))
        raw_meta = _mapping(raw_content.get("metadata"))
        semantic_claim = _mapping(data.get("semantic_claim"))
        for value in (
            data.get("value"),
            data.get("current_value"),
            raw_meta.get("value"),
            raw_content.get("value"),
            semantic_claim.get("object_value"),
            semantic_claim.get("object_text"),
        ):
            clean = str(value or "").strip()
            if clean:
                return clean
        return ""

    def _extract_simple_value(self, claim: str, fact_key: str) -> str:
        """Migration fallback generic cho claim cũ thiếu object_value.

        Dữ liệu mới luôn lấy value từ SemanticClaim structured. Hàm này chỉ dùng
        lexicon của registry để đọc vài câu legacy, không có nhánh fact_key riêng.
        """

        normalized = normalize_text(claim)
        definition = self.registry.by_fact_key(fact_key)
        if not normalized or definition is None:
            return ""
        phrases = unique_keep_order([
            definition.surface.display_name,
            *definition.query.aliases,
            *definition.query.disclosure_cues,
        ])
        for phrase in sorted(phrases, key=len, reverse=True):
            alias = normalize_text(phrase)
            if not alias:
                continue
            escaped = re.escape(alias)
            patterns = (
                rf"(?:{escaped})(?: cua (?:toi|tui|minh|ban|nguoi dung|user))?\s*(?:la|is|:)\s*(.{1,100})$",
                rf"(?:toi|tui|minh|ban|nguoi dung|user)\s+(?:co\s+)?{escaped}\s*(?:la|is|:)?\s*(.{1,100})$",
                rf"(?:goi|call)\s+(?:toi|tui|minh|ban|nguoi dung|user)\s+(?:la|as)\s*(.{1,100})$",
            )
            for pattern in patterns:
                match = re.search(pattern, normalized)
                if match:
                    value = match.group(1).strip(" .,:;-")
                    if value and value not in {"gi", "nao", "dau", "khong"}:
                        return value
        return ""

    # ------------------------------------------------------------------
    # Recall path
    # ------------------------------------------------------------------

    def lookup_fact(self, fact_key: str) -> Any:
        """Return the current value for a stable fact_key.

        This is intentionally stricter than generic recall: for single-source-of-truth
        facts such as user.name, the newest active keyed item wins.
        """

        key = str(fact_key or "").strip()
        if not key:
            return None
        candidates = [
            item
            for item in self.load()
            if item.fact_key == key and item.status in {"active", "tentative"}
        ]
        if not candidates:
            return None
        item = max(candidates, key=self._current_fact_rank)
        return item.semantic_value or self._extract_simple_value(item.claim, key) or None

    def get_fact(self, fact_key: str) -> Any:
        return self.lookup_fact(fact_key)

    def semantic_lookup(self, fact_key: str) -> Any:
        return self.lookup_fact(fact_key)

    def _current_fact_rank(self, item: SemanticMemoryItem) -> tuple[float, float, float, float, str]:
        status_rank = {"active": 2.0, "tentative": 1.0}.get(item.status, 0.0)
        updated = max(_safe_epoch_from_iso(item.updated_at), _safe_epoch_from_iso(item.last_accessed_at))
        return (status_rank, updated, float(item.confidence), float(item.importance), item.id)

    def recall(self, query: str = "", *, limit: int = 8, min_score: float | None = None) -> list[SemanticMemoryItem]:
        """Recall tương thích API cũ: trả list item.

        Nếu cần debug score, dùng recall_hits().
        """

        hits = self.recall_hits(query, limit=limit, min_score=min_score)
        self._record_access([hit.item.id for hit in hits])
        return [hit.item for hit in hits]

    def recall_hits(self, query: str = "", *, limit: int = 8, min_score: float | None = None) -> list[SemanticRecallHit]:
        """Recall có debug score.

        Flow:
            1. parse SemanticRecallCue
            2. nếu query rỗng: trả item hot theo retention
            3. embedding search lấy candidate nếu có
            4. luôn bổ sung candidate có fact_key/domain match để tránh embedding miss
            5. rerank bằng scorer hybrid
            6. threshold
        """

        limit = max(1, int(limit))
        threshold = self.min_recall_score if min_score is None else float(min_score)
        items = [item for item in self.load() if item.status in {"active", "tentative"}]
        if not items:
            return []

        cue = self.parse_recall_cue(query)
        if not cue.normalized:
            hot_hits = [
                SemanticRecallHit(
                    item=item,
                    score=self._retention_score(item),
                    confidence=item.confidence,
                    reasons=["empty_query:hot_memory"],
                )
                for item in self._sorted_items(items)[:limit]
            ]
            self._record_access([hit.item.id for hit in hot_hits])
            return hot_hits

        candidates = self._embedding_candidates(cue, items, limit=max(24, limit * 6))
        candidate_by_id: dict[str, tuple[SemanticMemoryItem, float, bool]] = {
            item.id: (item, vector_score, vector_available) for item, vector_score, vector_available in candidates
        }

        # Bổ sung direct key/domain candidates. Embedding model có thể miss fact ngắn như user.name.
        for item in items:
            if cue.fact_key and item.fact_key == cue.fact_key:
                candidate_by_id.setdefault(item.id, (item, 0.0, False))
            elif cue.domain != "unknown" and item.domain == cue.domain and (item.fact_key or item.importance >= 0.65):
                candidate_by_id.setdefault(item.id, (item, 0.0, False))

        # Nếu không có embedding/candidate, fallback toàn bộ item. Store hiện nhỏ nên được.
        if not candidate_by_id:
            candidate_by_id = {item.id: (item, 0.0, False) for item in items}

        hits: list[SemanticRecallHit] = []
        for item, vector_score, vector_available in candidate_by_id.values():
            hit = self._score_recall_item(item, cue, vector_score=vector_score, vector_available=vector_available)
            if hit.score >= threshold:
                hits.append(hit)

        hits.sort(key=lambda hit: hit.score, reverse=True)
        selected = hits[:limit]
        self._record_access([hit.item.id for hit in selected])
        return selected

    def _embedding_candidates(
        self,
        cue: SemanticRecallCue,
        items: list[SemanticMemoryItem],
        *,
        limit: int,
    ) -> list[tuple[SemanticMemoryItem, float, bool]]:
        """Lấy candidate từ embedding index nếu có.

        Contract cố ý duck-typing để không khóa store vào một thư viện cụ thể.
        """

        if self.embedding_index is None:
            return []
        search = getattr(self.embedding_index, "search", None)
        if not callable(search):
            return []
        try:
            raw_hits = search(
                cue.embedding_query,
                items,
                text_fn=self.embedding_text_for_item,
                limit=limit,
            )
        except Exception:
            # Embedding hỏng không được làm hỏng memory. Fallback lexical còn đó.
            return []

        out: list[tuple[SemanticMemoryItem, float, bool]] = []
        for entry in raw_hits or []:
            try:
                score, item = entry
                if isinstance(item, SemanticMemoryItem):
                    out.append((item, float(score), True))
            except Exception:
                continue
        return out

    def _score_recall_item(
        self,
        item: SemanticMemoryItem,
        cue: SemanticRecallCue,
        *,
        vector_score: float = 0.0,
        vector_available: bool = False,
    ) -> SemanticRecallHit:
        reasons: list[str] = []

        vector_component = self._normalize_vector_score(vector_score) if vector_available else 0.0
        if vector_available:
            reasons.append(f"vector={vector_component:.2f}")

        key_score = self._fact_key_score(item, cue)
        if key_score > 0:
            reasons.append(f"fact_key={key_score:.2f}")

        domain_score = self._domain_score(item, cue)
        if domain_score > 0:
            reasons.append(f"domain={domain_score:.2f}")
        elif domain_score < 0:
            reasons.append(f"domain_penalty={domain_score:.2f}")

        alias_score = self._alias_score(item, cue)
        if alias_score > 0:
            reasons.append(f"alias={alias_score:.2f}")

        lexical_score = self._lexical_similarity(item, cue)
        if lexical_score > 0.10:
            reasons.append(f"lexical={lexical_score:.2f}")

        importance = item.importance
        confidence_component = item.confidence
        observation_bonus = min(0.18, item.observations * 0.025)
        access_bonus = min(0.12, item.access_count * 0.012)
        recency_bonus = self._recency_bonus(item)

        penalty = 0.0
        if item.status == "tentative":
            penalty += 0.16
            reasons.append("tentative_penalty")
        if item.contradiction_ids:
            penalty += 0.70
            reasons.append("contradiction_penalty")
        if cue.wants_profile_fact and item.domain not in {"user", "self", "relationship"}:
            penalty += 0.35
            reasons.append("profile_domain_penalty")
        if cue.strict_domain and cue.domain != "unknown" and item.domain != cue.domain:
            penalty += 0.55
            reasons.append("strict_domain_penalty")

        score = (
            vector_component * 0.95
            + key_score * 2.20
            + domain_score * 0.82
            + alias_score * 0.95
            + lexical_score * 0.68
            + confidence_component * 0.48
            + importance * 0.36
            + observation_bonus
            + access_bonus
            + recency_bonus
            - penalty
        )

        # Nếu cue hỏi fact_key cụ thể mà item không có key/domain liên quan, score lexical không được cứu nó.
        if cue.fact_key and key_score <= 0 and domain_score <= 0:
            score *= 0.45
            reasons.append("specific_key_miss_cap")

        confidence = clamp01(
            item.confidence * 0.50
            + min(1.0, max(0.0, score) / 3.2) * 0.34
            + key_score * 0.10
            + vector_component * 0.06
        )

        return SemanticRecallHit(
            item=item,
            score=score,
            confidence=confidence,
            vector_score=vector_component,
            lexical_score=lexical_score,
            key_score=key_score,
            domain_score=domain_score,
            alias_score=alias_score,
            reasons=unique_keep_order(reasons),
        )

    def _normalize_vector_score(self, value: float) -> float:
        """Đưa score backend về thành phần 0..1 mà không bơm confidence giả.

        EmbeddingIndex mới công bố `score_scale=cosine` và trả raw cosine. Với
        cosine, phần âm/neutral không có giá trị retrieval nên bị chặn ở 0; 0.10
        vẫn là 0.10, không được hóa trang thành 0.55.
        """

        try:
            score = float(value)
        except Exception:
            return 0.0
        if math.isnan(score) or math.isinf(score):
            return 0.0
        scale = str(getattr(self.embedding_index, "score_scale", "cosine") or "cosine").lower()
        if scale == "cosine":
            return clamp01(max(0.0, score))
        if scale in {"probability", "similarity_01", "unit"}:
            return clamp01(score)
        # Backend không khai scale: chọn cách bảo thủ, tuyệt đối không remap
        # [-1,1] sang [0,1] vì điều đó nâng cosine 0 thành tín hiệu giả 0.5.
        return clamp01(max(0.0, score))

    def _fact_key_score(self, item: SemanticMemoryItem, cue: SemanticRecallCue) -> float:
        if not cue.fact_key:
            return 0.0
        if item.fact_key == cue.fact_key:
            return 1.0
        # Parent match: user.preference có thể kéo user.preference.reply_style.
        if item.fact_key and (item.fact_key.startswith(cue.fact_key + ".") or cue.fact_key.startswith(item.fact_key + ".")):
            return 0.72
        return 0.0

    def _domain_score(self, item: SemanticMemoryItem, cue: SemanticRecallCue) -> float:
        if cue.domain == "unknown":
            return 0.0
        if item.domain == cue.domain:
            return 1.0
        # Project và technical có thể gần nhau trong ERIS, nhưng không giống user/self.
        if {item.domain, cue.domain} <= {"project", "technical"}:
            return 0.35
        return -0.55 if cue.strict_domain else -0.28

    def _alias_score(self, item: SemanticMemoryItem, cue: SemanticRecallCue) -> float:
        if not cue.aliases:
            return 0.0
        claim_norm = normalize_text(item.claim)
        tag_norm = normalize_text(" ".join(item.tags))
        raw_norm = normalize_text(item.raw_user_text)
        item_aliases = {normalize_text(alias) for alias in item.aliases}
        hits = 0
        for alias in cue.aliases:
            alias_norm = normalize_text(alias)
            if not alias_norm:
                continue
            if alias_norm in item_aliases or alias_norm in claim_norm or alias_norm in tag_norm or alias_norm in raw_norm:
                hits += 1
        return clamp01(0.32 + hits * 0.22) if hits else 0.0

    def _lexical_similarity(self, item: SemanticMemoryItem, cue: SemanticRecallCue) -> float:
        parts = item.recall_text_parts()
        item_text = " ".join(parts)
        token_sim = jaccard(cue.tokens, token_set(item_text))
        char_sim = jaccard(char_ngrams(cue.normalized), char_ngrams(item_text))
        word_sim = jaccard(word_ngrams(cue.normalized), word_ngrams(item_text))
        seq_sim = max((sequence_ratio(cue.normalized, part) for part in parts), default=0.0)
        return clamp01(token_sim * 0.42 + char_sim * 0.24 + word_sim * 0.22 + seq_sim * 0.12)

    def _recency_bonus(self, item: SemanticMemoryItem) -> float:
        ts = max(_safe_epoch_from_iso(item.updated_at), _safe_epoch_from_iso(item.last_accessed_at))
        if ts <= 0:
            return 0.0
        age_days = max(0.0, (time.time() - ts) / 86400.0)
        return min(0.10, 0.10 / (1.0 + age_days / 45.0))

    def _record_access(self, item_ids: Sequence[str]) -> None:
        """Cập nhật access_count/last_accessed_at cho item được recall.

        Việc này giúp HOT facts nổi lên nhanh hơn mà không cần đọc toàn bộ lịch sử.
        """

        ids = {str(item_id) for item_id in item_ids if str(item_id).strip()}
        if not ids:
            return
        items = self.load()
        changed = False
        now = iso_now()
        for item in items:
            if item.id not in ids:
                continue
            metadata = dict(item.metadata or {})
            metadata["access_count"] = int(metadata.get("access_count", 0) or 0) + 1
            metadata["last_accessed_at"] = now
            item.metadata = metadata
            changed = True
        if changed:
            self.save(items)

    # ------------------------------------------------------------------
    # Cue parsing / fact profile derivation
    # ------------------------------------------------------------------

    def parse_recall_cue(self, query: str) -> SemanticRecallCue:
        normalized = normalize_text(query)
        cue = SemanticRecallCue(query=query or "", normalized=normalized)
        if not normalized:
            cue.evidence.append("empty_query")
            return cue

        # Profile fact được resolve từ registry canonical. Predicate provisional
        # đã hydrate cũng tham gia, nên semantic store không cần thêm if cho từng
        # thuộc tính mới.
        resolution = self.registry.resolve_fact_query(query) or self.registry.resolve_fact_reference(query)
        if resolution is not None:
            definition = self.registry.by_fact_key(resolution.fact_key)
            cue.domain = self._domain_from_fact_key(resolution.fact_key) or "user"
            cue.fact_key = resolution.fact_key
            cue.aliases = list(definition.query_phrases() if definition is not None else (resolution.matched_phrase,))
            cue.wants_profile_fact = cue.domain == "user"
            cue.strict_domain = True
            cue.evidence.append(f"cue:{resolution.fact_key}:{resolution.source}")
            return cue

        # Self / ERIS identity.
        if _matches_any(normalized, (r"\beris la ai\b", r"\bban la ai\b", r"\bem la ai\b", r"\bself identity\b")):
            cue.domain = "self"
            cue.fact_key = "self.identity"
            cue.aliases = ["eris", "identity", "runtime", "observer"]
            cue.strict_domain = True
            cue.evidence.append("cue:self.identity")
            return cue

        # Project ERIS.
        if "eris" in normalized or _matches_any(normalized, PROJECT_ERIS_PATTERNS):
            cue.domain = "project"
            cue.fact_key = "project.eris"
            cue.aliases = ["eris", "project", "runtime", "pipeline", "memory", "architecture"]
            cue.evidence.append("cue:project.eris")
            return cue

        # Technical memory.
        if _matches_any(normalized, TECHNICAL_PATTERNS):
            cue.domain = "technical"
            cue.aliases = ["code", "debug", "module", "python", "json", "memory"]
            cue.evidence.append("cue:technical")
            return cue

        # Relationship memory.
        if _matches_any(normalized, RELATIONSHIP_PATTERNS):
            cue.domain = "relationship"
            cue.aliases = ["relationship", "trust", "repair", "distance", "quan he"]
            cue.evidence.append("cue:relationship")
            return cue

        cue.evidence.append("cue:generic")
        return cue

    def _derive_fact_profile(
        self,
        claim: str,
        *,
        target: Any = "",
        reason: str = "",
        request_metadata: Mapping[str, Any] | None = None,
        request_tags: Sequence[str] = (),
    ) -> FactProfile:
        """Derive domain/fact_key/importance từ claim + request context.

        Ưu tiên metadata/reason từ MemoryCommitPolicy nếu có.
        Nếu không có, dùng parser nhẹ từ claim. Đây là fallback, không phải chân lý.
        """

        metadata = dict(request_metadata or {})
        legacy_profile_infer = bool(metadata.get("legacy_profile_infer") or metadata.get("migration_profile_infer"))
        normalized = normalize_text(" ".join((claim, reason, " ".join(request_tags))))
        target_norm = normalize_text(str(target or ""))
        reason_norm = normalize_text(reason)
        profile = FactProfile(domain="unknown", importance=0.50)

        explicit_key = str(metadata.get("fact_key") or "").strip()
        if explicit_key:
            profile.fact_key = explicit_key
            profile.domain = self._domain_from_fact_key(explicit_key) or profile.domain
            profile.aliases = [str(x) for x in metadata.get("aliases", []) if str(x).strip()]
            profile.importance = max(profile.importance, clamp01(metadata.get("importance", 0.78), default=0.78))
            profile.evidence.append("metadata.fact_key")
            profile.tags.append("typed_fact")
            return profile

        explicit_domain = str(metadata.get("domain") or "").strip()
        if explicit_domain in {"user", "self", "world", "technical", "project", "relationship", "unknown"}:
            profile.domain = explicit_domain  # type: ignore[assignment]
            profile.evidence.append("metadata.domain")

        # Reason cũ được resolve qua registry thay vì bốn nhánh user.name/...
        reason_resolution = self.registry.resolve_fact_reference(reason.replace("_", " ")) if reason_norm else None
        if reason_resolution is not None:
            definition = self.registry.by_fact_key(reason_resolution.fact_key)
            profile.domain = self._domain_from_fact_key(reason_resolution.fact_key) or "user"
            profile.fact_key = reason_resolution.fact_key
            profile.aliases = list(definition.query_phrases() if definition is not None else ())
            profile.importance = max(
                profile.importance,
                float(definition.retention.importance) if definition is not None else 0.78,
            )
            profile.tags.extend(["typed_fact", definition.predicate if definition is not None else "registry_fact"])
            profile.evidence.append(f"reason:{reason_resolution.fact_key}")
            return profile

        if not legacy_profile_infer:
            if not profile.fact_key:
                profile.tags.extend(["fact_key_missing_candidate", "legacy_infer_disabled"])
                profile.evidence.append("candidate:missing_fact_key")
            profile.tags = unique_keep_order([*profile.tags, *request_tags])
            profile.aliases = unique_keep_order(profile.aliases)
            profile.evidence = unique_keep_order(profile.evidence)
            return profile

        profile.domain = self._infer_domain(claim, target)

        # Migration fallback: resolve claim text bằng catalog. Commit path mới đã
        # có fact_key structured nên đoạn này chỉ phục vụ dữ liệu cũ.
        claim_resolution = self.registry.resolve_fact_reference(claim) if profile.domain in {"user", "unknown"} else None
        if claim_resolution is not None:
            definition = self.registry.by_fact_key(claim_resolution.fact_key)
            profile.domain = self._domain_from_fact_key(claim_resolution.fact_key) or "user"
            profile.fact_key = claim_resolution.fact_key
            profile.aliases = list(definition.query_phrases() if definition is not None else ())
            profile.importance = max(
                profile.importance,
                float(definition.retention.importance) if definition is not None else 0.72,
            )
            profile.tags.extend(["typed_fact", definition.predicate if definition is not None else "registry_fact"])
            profile.evidence.append(f"claim:{claim_resolution.fact_key}")
        elif _matches_any(normalized, SELF_IDENTITY_PATTERNS):
            profile.domain = "self"
            profile.fact_key = "self.identity"
            profile.aliases = ["eris", "identity", "runtime", "observer"]
            profile.importance = 0.86
            profile.tags.extend(["self_identity"])
            profile.evidence.append("claim:self.identity")
        elif "eris" in normalized or "project" in target_norm or _matches_any(normalized, PROJECT_ERIS_PATTERNS):
            profile.domain = "project" if profile.domain == "unknown" else profile.domain
            profile.fact_key = "project.eris"
            profile.aliases = ["eris", "project", "runtime", "pipeline", "memory", "architecture"]
            profile.importance = max(profile.importance, 0.78)
            profile.tags.extend(["project", "eris"])
            profile.evidence.append("claim:project.eris")
        elif profile.domain == "relationship" or _matches_any(normalized, RELATIONSHIP_PATTERNS):
            profile.domain = "relationship"
            profile.fact_key = "relationship.pattern"
            profile.aliases = ["relationship", "trust", "repair", "distance", "quan hệ", "quan he"]
            profile.importance = max(profile.importance, 0.72)
            profile.tags.extend(["relationship"])
            profile.evidence.append("claim:relationship")
        elif profile.domain == "technical" or _matches_any(normalized, TECHNICAL_PATTERNS):
            profile.domain = "technical"
            profile.aliases = ["technical", "code", "debug", "module"]
            profile.importance = max(profile.importance, 0.62)
            profile.tags.extend(["technical"])
            profile.evidence.append("claim:technical")

        # Tag từ target.
        if str(target or "") == "belief_core" and profile.domain == "unknown":
            profile.domain = "world"
            profile.importance = max(profile.importance, 0.66)
            profile.tags.append("belief")

        profile.tags = unique_keep_order([*profile.tags, *request_tags])
        profile.aliases = unique_keep_order(profile.aliases)
        profile.evidence = unique_keep_order(profile.evidence)
        return profile

    def _domain_from_fact_key(self, fact_key: str) -> SemanticDomain | None:
        if fact_key.startswith("user."):
            return "user"
        if fact_key.startswith("self."):
            return "self"
        if fact_key.startswith("project."):
            return "project"
        if fact_key.startswith("technical."):
            return "technical"
        if fact_key.startswith("relationship."):
            return "relationship"
        if fact_key.startswith("world."):
            return "world"
        return None

    def _infer_domain(self, claim: str, target: Any) -> SemanticDomain:
        """Infer domain nhẹ từ claim/target.

        Đây là fallback. Nếu MemoryCommitPolicy gửi metadata/fact_key thì nó thắng.
        """

        lowered = normalize_text(claim)
        target_norm = normalize_text(str(target or ""))

        if any(phrase in lowered for phrase in ("nguoi dung", "ban", "user ", "user_", "preferred name")):
            return "user"
        if "eris" in lowered or "self" in lowered or "runtime artificial observer" in lowered:
            # Nếu claim có ERIS + project/pipeline thì nghiêng về project, trừ khi là identity.
            if _matches_any(lowered, SELF_IDENTITY_PATTERNS):
                return "self"
            if _matches_any(lowered, PROJECT_ERIS_PATTERNS):
                return "project"
            return "self"
        if any(phrase in lowered for phrase in ("project", "pipeline", "cognitive", "semantic memory", "working memory")):
            return "project"
        if _matches_any(lowered, TECHNICAL_PATTERNS):
            return "technical"
        if _matches_any(lowered, RELATIONSHIP_PATTERNS):
            return "relationship"
        if target_norm == "belief_core":
            return "world"
        return "unknown"

    def _build_metadata(
        self,
        *,
        claim: str,
        profile: FactProfile,
        reason: str,
        request_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        embedding_text = self._embedding_text_from_parts(
            claim=claim,
            domain=profile.domain,
            fact_key=profile.fact_key,
            tags=profile.tags,
            aliases=profile.aliases,
        )
        metadata = dict(request_metadata or {})
        metadata.update(
            {
                "reason": compact_text(reason, limit=280),
                "fact_key": profile.fact_key,
                "aliases": profile.aliases,
                "importance": profile.importance,
                "fact_profile_evidence": profile.evidence,
                "embedding_text_hash": stable_hash(embedding_text),
                "embedding_version": 1,
                "access_count": int(metadata.get("access_count", 0) or 0),
                "last_accessed_at": str(metadata.get("last_accessed_at", "") or ""),
            }
        )
        return metadata

    # ------------------------------------------------------------------
    # Embedding text
    # ------------------------------------------------------------------

    def embedding_text_for_item(self, item: SemanticMemoryItem) -> str:
        """Text đưa vào embedding index.

        Không embed claim trần. Claim ngắn như "Gtvkun" sẽ rất nghèo ngữ cảnh.
        Vì vậy ta gắn fact_key/domain/tags/aliases vào embedding text.
        """

        return self._embedding_text_from_parts(
            claim=item.claim,
            domain=item.domain,
            fact_key=item.fact_key,
            tags=item.tags,
            aliases=[*item.aliases, item.raw_user_text, item.semantic_value],
        )

    def _embedding_text_from_parts(
        self,
        *,
        claim: str,
        domain: SemanticDomain,
        fact_key: str,
        tags: Sequence[str],
        aliases: Sequence[str],
    ) -> str:
        return compact_text(
            " | ".join(
                part
                for part in (
                    fact_key,
                    domain,
                    " ".join(tags),
                    " ".join(aliases),
                    claim,
                )
                if str(part or "").strip()
            ),
            limit=760,
        )

    # ------------------------------------------------------------------
    # Similarity / maintenance helpers
    # ------------------------------------------------------------------

    def _claim_similarity(self, a: str, b: str) -> float:
        return clamp01(
            jaccard(token_set(a), token_set(b)) * 0.38
            + jaccard(char_ngrams(a), char_ngrams(b)) * 0.28
            + jaccard(word_ngrams(a), word_ngrams(b)) * 0.22
            + sequence_ratio(a, b) * 0.12
        )

    def apply_user_authority(
        self,
        *,
        action: str,
        fact_key: str,
        value: Any = None,
        evidence_ids: Iterable[str] = (),
        scope_kind: str = "global",
        scope_key: str = "",
        hard: bool = False,
        authority_event_id: str = "",
    ) -> dict[str, Any]:
        """Áp quyết định trực tiếp của người dùng lên semantic projection.

        Semantic memory không còn tự nhận là nguồn chân lý đối với dữ liệu user.
        Correction/forget từ panel được ghi ở authority ledger rồi phản chiếu vào
        đây. Nhờ vậy recall semantic và living-user profile không thể trả hai câu
        khác nhau về cùng một người, một cảnh khá buồn cười nếu nó không liên quan
        tới dữ liệu thật.
        """
        key = str(fact_key or "").strip().lower()
        if not key:
            return {"changed": 0, "evidence_ids": []}
        items = self.load()
        matched = [item for item in items if str(item.fact_key or "").strip().lower() == key]
        removed_evidence = unique_keep_order(
            evidence_id
            for item in matched
            for evidence_id in (item.id, *item.evidence_ids)
        )
        changed = 0
        now = iso_now()

        if action == "forget":
            if hard:
                before = len(items)
                items = [item for item in items if str(item.fact_key or "").strip().lower() != key]
                changed = before - len(items)
            else:
                for item in matched:
                    if item.status != "archived":
                        item.status = "archived"
                        item.updated_at = now
                        item.metadata["authority_event_id"] = authority_event_id
                        changed += 1
            if changed:
                self.save(items)
                self.rebuild_embedding_index()
            return {"changed": changed, "evidence_ids": removed_evidence}

        if action == "move_scope":
            for item in matched:
                item.metadata["scope_kind"] = scope_kind
                item.metadata["scope_key"] = scope_key
                item.metadata["authority_event_id"] = authority_event_id
                item.updated_at = now
                changed += 1
            if changed:
                self.save(items)
                self.rebuild_embedding_index()
            return {"changed": changed, "evidence_ids": removed_evidence}

        if action not in {"correct", "confirm"}:
            return {"changed": 0, "evidence_ids": removed_evidence}

        authority_evidence = unique_keep_order((*evidence_ids, f"authority:{authority_event_id}" if authority_event_id else ""))
        same = next((
            item for item in matched
            if _semantic_value_fingerprint(item.semantic_value) == _semantic_value_fingerprint(value)
        ), None)
        for item in matched:
            if same is not None and item.id == same.id:
                continue
            if item.status in {"active", "tentative"}:
                item.status = "contradicted"
                item.updated_at = now
                item.metadata["superseded_by_authority"] = authority_event_id
                changed += 1

        if same is None:
            definition = self.registry.by_fact_key(key)
            label = definition.display_name if definition is not None else key.rsplit(".", 1)[-1].replace("_", " ")
            text_value = compact_text(value, limit=260)
            same = SemanticMemoryItem(
                id=f"sem_auth_{stable_hash(key + '|' + _semantic_value_fingerprint(value))}",
                claim=compact_text(f"{label}: {text_value}", limit=520),
                domain="user",
                confidence=1.0,
                source="user_authority",
                evidence_ids=authority_evidence,
                observations=1,
                status="active",
                first_seen_at=now,
                updated_at=now,
                decay_policy="protected",
                tags=["user_authority", "profile_fact"],
                metadata={
                    "fact_key": key,
                    "value": value,
                    "explicitness": "explicit",
                    "authority_event_id": authority_event_id,
                    "scope_kind": scope_kind,
                    "scope_key": scope_key,
                    "importance": 0.95,
                    "cardinality": definition.cardinality if definition is not None else "single",
                },
            )
            items.append(same)
            changed += 1
        else:
            before = set(same.evidence_ids)
            same.evidence_ids = unique_keep_order((*same.evidence_ids, *authority_evidence))
            same.status = "active"
            same.confidence = 1.0
            same.source = "user_authority"
            same.updated_at = now
            same.metadata.update({
                "value": value,
                "explicitness": "explicit",
                "authority_event_id": authority_event_id,
                "scope_kind": scope_kind,
                "scope_key": scope_key,
                "importance": max(0.95, same.importance),
            })
            if set(same.evidence_ids) != before or same.status != "active":
                changed += 1

        self.save(items)
        self.rebuild_embedding_index()
        return {"changed": changed, "evidence_ids": removed_evidence}

    def rebuild_embedding_index(self) -> bool:
        """Yêu cầu embedding_index build/cache lại vector nếu backend hỗ trợ.

        Không bắt buộc dùng. Dành cho compactor/background job.
        """

        if self.embedding_index is None:
            return False
        ensure = getattr(self.embedding_index, "ensure_vectors", None)
        if not callable(ensure):
            return False
        items = [item for item in self.load() if item.status in {"active", "tentative"}]
        try:
            ensure(items, text_fn=self.embedding_text_for_item)
            return True
        except Exception:
            return False


__all__ = [
    "SemanticDomain",
    "SemanticStatus",
    "SemanticRecallCue",
    "SemanticRecallHit",
    "SemanticMemoryItem",
    "SemanticMemoryStore",
    "FactProfile",
    "iso_now",
    "compact_text",
    "clamp01",
    "normalize_text",
    "dedupe_key",
]
