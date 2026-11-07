from __future__ import annotations

"""
claim_extractor.py
==================

Facade cho memory intake.

Bản cũ của file này cố tự parse predicate bằng regex. Đó là kiểu code nhìn như
ontology nhưng ruột vẫn là marker-word. Bản này chỉ còn là cầu nối:

    SemanticParser/model.gguf -> MeaningFrame -> ClaimBuilder -> ExtractedClaim

Nếu bạn muốn gắn model.gguf, truyền parser vào `ClaimExtractor(parser=...)` hoặc
đưa payload JSON qua `llm_structured`. Extractor không commit memory và không tự
quyết định retention.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .memory_surface_filter import should_exclude_from_persistent_memory

try:
    from .semantic_claim import ExtractedClaim
    from .understanding.semantic_parser import FallbackSurfaceSemanticParser, SemanticParser
    from .claims.claim_builder import ClaimBuilder
except Exception:  # pragma: no cover
    from semantic_claim import ExtractedClaim  # type: ignore
    from understanding.semantic_parser import FallbackSurfaceSemanticParser, SemanticParser  # type: ignore
    from claims.claim_builder import ClaimBuilder  # type: ignore


@dataclass(slots=True)
class ClaimExtractor:
    """Facade extract claim theo pipeline frame semantics.

    `parser` mặc định là fallback surface rất nhỏ. Runtime production nên thay bằng
    parser model.gguf trả `MeaningFrame` JSON. Extractor chỉ verify và build claim,
    nên model không có quyền ghi memory bừa. Đúng rồi, cuối cùng cũng có phân quyền.
    """

    parser: SemanticParser = field(default_factory=FallbackSurfaceSemanticParser)
    builder: ClaimBuilder = field(default_factory=ClaimBuilder)

    def extract(
        self,
        user_text: str,
        *,
        evidence_ids: Iterable[str] = (),
        source: str = "user_text",
        llm_structured: Mapping[str, Any] | None = None,
    ) -> list[ExtractedClaim]:
        raw = str(user_text or "").strip()
        if not raw:
            return []
        # Defense-in-depth: extractor có thể được gọi trực tiếp ngoài MemoryKernel.
        # Giấy phép xã hội được lưu ở RelationalPermissionState/event session-only.
        if should_exclude_from_persistent_memory(raw):
            return []
        parsed = self.parser.parse(raw, structured=llm_structured)
        return self.builder.build_many(parsed, evidence_ids=tuple(str(x) for x in evidence_ids if str(x).strip()), source=source)


def extract_claims(user_text: str, *, evidence_ids: Iterable[str] = ()) -> list[ExtractedClaim]:
    return ClaimExtractor().extract(user_text, evidence_ids=evidence_ids)


# RelationFrame từng tồn tại ở bản marker-word. Giữ alias để import cũ không nổ,
# nhưng không còn dùng trong pipeline mới.
RelationFrame = object


__all__ = ["ClaimExtractor", "RelationFrame", "extract_claims"]
