"""Source import adapters. Text locators are real offsets; Docling stays optional."""

from __future__ import annotations

import hashlib
from pathlib import Path
from runtime.learning_core.records import SourceSpan, digest


def text_spans(*, domain_id, source_id, text, origin="", source_kind="original", now=0.0):
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 4_000_000:
        raise ValueError("invalid_source_text")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    spans = []
    for start in range(0, len(text), 4000):
        excerpt = text[start : start + 4000]
        if not excerpt.strip():
            continue
        locator = f"char:{start}:{start + len(excerpt)}"
        spans.append(
            SourceSpan(
                "span:" + digest([domain_id, source_id, content_hash, locator])[:32],
                source_id,
                domain_id,
                content_hash,
                locator,
                excerpt,
                origin,
                source_kind=source_kind,
                imported_at=now,
            )
        )
    return tuple(spans)


def import_document(path: Path, *, domain_id, source_id, now=0.0):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 30_000_000:
        raise ValueError("document_budget_exceeded")
    if path.suffix.lower() in {".txt", ".md"}:
        return text_spans(
            domain_id=domain_id, source_id=source_id, text=path.read_text(encoding="utf-8"), origin=str(path), now=now
        )
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError("docling_not_installed") from exc
    data = path.read_bytes()
    content_hash = hashlib.sha256(data).hexdigest()
    document = DocumentConverter().convert(path).document
    spans = []
    for item, _level in document.iterate_items():
        text = getattr(item, "text", "")
        if not text or len(text) > 12000:
            continue
        for index, provenance in enumerate(getattr(item, "prov", ())):
            # No invented page or region when the parser did not supply it.
            locator = f"page:{provenance.page_no};item:{item.self_ref};part:{index};bbox:{provenance.bbox.as_tuple()}"
            spans.append(
                SourceSpan(
                    "span:" + digest([domain_id, source_id, content_hash, locator])[:32],
                    source_id,
                    domain_id,
                    content_hash,
                    locator,
                    text,
                    str(path),
                    parser_version="docling-v1",
                    imported_at=now,
                )
            )
            if len(spans) >= 2048:
                raise ValueError("too_many_source_spans")
    if not spans:
        raise ValueError("document_has_no_locatable_text")
    return tuple(spans)
