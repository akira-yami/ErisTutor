from __future__ import annotations

"""
memory_candidate.py
===================

Candidate retrieval layer.

Layer store cũ vẫn có thể scan JSON và trả item. File này chuyển item đó thành
MemoryRecord rồi tạo MemoryCandidate có các score thô:
- lexical
- phrase
- bigram
- anchor
- temporal

Candidate chưa phải activation. Nó chỉ nói:
    "Record này có dấu hiệu liên quan ở mức nào?"

Quyết định cuối thuộc MemoryScorer. Không để store tự làm quan tòa nữa.
"""

from dataclasses import dataclass
from typing import Any

try:
    from .memory_record import (
        MemoryRecord,
        record_from_layer_item,
        normalize_text,
        tokenize,
        token_bigrams,
        weighted_counter,
        weighted_jaccard,
        phrase_score,
        recency_score,
        compact_text,
        clamp01,
    )
    from .memory_cue import MemoryCue
except Exception:  # pragma: no cover
    from memory_record import (
        MemoryRecord,
        record_from_layer_item,
        normalize_text,
        tokenize,
        token_bigrams,
        weighted_counter,
        weighted_jaccard,
        phrase_score,
        recency_score,
        compact_text,
        clamp01,
    )
    from memory_cue import MemoryCue


@dataclass(slots=True)
class MemoryCandidate:
    record: MemoryRecord
    retrieval_source: str = "layer_scan"

    lexical_score: float = 0.0
    anchor_score: float = 0.0
    phrase_score: float = 0.0
    bigram_score: float = 0.0
    temporal_score: float = 0.0

    matched_terms: tuple[str, ...] = ()
    matched_anchors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def rough_score(self) -> float:
        return clamp01(
            self.lexical_score * 0.30
            + self.anchor_score * 0.30
            + self.phrase_score * 0.18
            + self.bigram_score * 0.16
            + self.temporal_score * 0.06
        )


class MemoryLayerAdapter:
    """Adapter đọc memory store bằng load()/recall() nếu có.

    Ưu tiên load() để scorer mới làm việc trên toàn item.
    Nếu store không có load(), fallback sang recall() rồi convert.
    """

    def __init__(self, *, layer: str, store: Any):
        self.layer = layer
        self.store = store

    def retrieve_candidates(self, cue: MemoryCue, *, limit: int = 24) -> list[MemoryCandidate]:
        if not cue.layer_allowed(self.layer):
            return []

        raw_items = self._load_items(cue, limit=max(limit * 4, limit))
        candidates: list[MemoryCandidate] = []

        for item in raw_items:
            record = record_from_layer_item(self.layer, item)
            candidate = self._candidate_for_record(record, cue)
            # Best architecture: retrieval should not let the adapter silently drop
            # candidate objects. Scorer owns relevance/permission/background policy.
            # Otherwise a layer can never create a useful background bias because
            # rough lexical score was zero. A tiny gate pretending to be cognition.
            candidates.append(candidate)

        candidates.sort(key=lambda item: item.rough_score(), reverse=True)
        return candidates[:limit]

    def _load_items(self, cue: MemoryCue, *, limit: int) -> list[Any]:
        if hasattr(self.store, "load"):
            try:
                items = self.store.load()
                if isinstance(items, list):
                    return items[-max(limit, 1) * 3 :]
            except Exception:
                pass
        if hasattr(self.store, "recall"):
            try:
                items = self.store.recall(cue.all_text(), limit=limit)
                if isinstance(items, list):
                    return items
            except Exception:
                pass
        return []

    def _candidate_for_record(self, record: MemoryRecord, cue: MemoryCue) -> MemoryCandidate:
        cue_text = cue.all_text()
        cue_norm = normalize_text(cue_text)
        record_norm = record.normalized_text()

        # Preserve token order for bigram scoring. cue.token_set() is intentionally unordered;
        # using it here made phrases like "fake output" randomly disappear.
        cue_tokens = tokenize(cue.all_text())
        record_tokens = record.tokens()

        cue_counter = weighted_counter(cue_tokens)
        record_counter = weighted_counter(record_tokens)

        lexical = weighted_jaccard(cue_counter, record_counter)
        phrase = phrase_score(cue_norm, record_norm)

        cue_bigrams = token_bigrams(cue_tokens)
        record_bigrams = token_bigrams(record_tokens)
        bigram = 0.0
        if cue_bigrams and record_bigrams:
            bigram = len(cue_bigrams & record_bigrams) / max(1, len(cue_bigrams))

        cue_anchors = cue.anchor_set()
        record_anchors = record.anchor_set()
        anchor_overlap = cue_anchors & record_anchors
        anchor = len(anchor_overlap) / max(1, len(cue_anchors)) if cue_anchors else 0.0

        matched_terms = tuple(sorted(set(cue_tokens) & set(record_tokens)))
        matched_anchors = tuple(sorted(anchor_overlap))

        warnings: list[str] = []
        if cue.needs_current_evidence and record.layer in {"semantic", "episodic"}:
            warnings.append("current_evidence_query_memory_cannot_answer_directly")
        if cue.privacy_sensitivity >= 0.6 and record.privacy_level in {"private", "sensitive"}:
            warnings.append("privacy_sensitive_record")

        return MemoryCandidate(
            record=record,
            retrieval_source=f"{self.layer}:layer_adapter",
            lexical_score=lexical,
            anchor_score=anchor,
            phrase_score=phrase,
            bigram_score=bigram,
            temporal_score=recency_score(record.created_at),
            matched_terms=matched_terms,
            matched_anchors=matched_anchors,
            warnings=tuple(warnings),
        )
