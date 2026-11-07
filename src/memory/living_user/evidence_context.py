"""Bounded, role-labelled source context for memory interpretation, not a selector of meaning."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def build_evidence_context(*, turns: Iterable[Mapping[str, Any]], episodes: Iterable[Any],
                           evidence_ids: Iterable[str] = (), cutoff: float | None = None,
                           session_id: str = "", max_chars: int = 20000) -> dict[str, Any]:
    wanted = {str(ref) for ref in evidence_ids if ref}
    budget = max(1000, int(max_chars))
    rows = [e.to_dict() if callable(getattr(e, "to_dict", None)) else dict(e) for e in episodes]
    rows = [e for e in rows if cutoff is None or float(e.get("ended_at") or 0) <= cutoff]
    # A busy current chat must not consume the entire budget before the source
    # episode of an older candidate is read by the curator.
    recent_budget = budget // 2 if any(wanted.intersection(e.get("evidence_ids") or ()) for e in rows) else budget
    recent: list[dict[str, Any]] = []
    omitted = 0
    for row in reversed(list(turns)):
        stamp = float(row.get("timestamp") or 0)
        if cutoff is not None and stamp > cutoff:
            continue
        role = str(row.get("role") or "unknown")
        if role not in {"user", "assistant", "eris"}:
            continue
        text = str(row.get("text") or "")
        if len(recent) >= 12 or recent_budget < len(text[:1800]):
            omitted += 1
            continue
        recent.append({"id": str(row.get("id") or ""), "role": role,
                       "text": text[:1800], "timestamp": stamp,
                       "session_id": str(row.get("session_id") or session_id),
                       "text_truncated": bool(row.get("text_truncated")) or len(text) > 1800})
        budget -= len(text[:1800])
        recent_budget -= len(text[:1800])
    recent.reverse()
    rows.sort(key=lambda e: (bool(wanted.intersection(e.get("evidence_ids") or ())),
                             float(e.get("ended_at") or 0)), reverse=True)
    related: list[dict[str, Any]] = []
    for episode in rows[:6]:
        matched = sorted(wanted.intersection(episode.get("evidence_ids") or ()))
        # Unmatched summaries are background, never a claimed evidence-ID match.
        if not matched and len(related) >= 2:
            continue
        summary = str(episode.get("summary") or "")[:1400]
        if budget < len(summary):
            omitted += 1
            continue
        budget -= len(summary)
        source_turns = []
        sources = list((episode.get("metadata") or {}).get("source_turns", ()) or ())
        matched_indices = [i for i, row in enumerate(sources) if row.get("id") in wanted]
        indices = list(range(len(sources)))
        if matched_indices:
            indices.sort(key=lambda i: (min(abs(i-j) for j in matched_indices), i))
        selected_sources = []
        for i in indices[:12]:
            row = sources[i]
            text = str(row.get("text") or "")[:1800]
            if budget < len(text):
                omitted += 1
                continue
            selected_sources.append((i, {"id": str(row.get("id") or ""),
                                 "role": str(row.get("role") or "unknown"), "text": text,
                                 "session_id": str(row.get("session_id") or episode.get("session_id") or ""),
                                 "text_truncated": bool(row.get("text_truncated"))}))
            budget -= len(text)
        source_turns = [row for _, row in sorted(selected_sources)]
        related.append({"episode_id": episode.get("id"), "session_id": episode.get("session_id"),
                        "scope": episode.get("scope"), "summary": summary,
                        "matched_refs": matched, "evidence_ids": episode.get("evidence_ids", []),
                        "source_turns": source_turns,
                        "basis": "source_turns" if source_turns else "secondary_summary_only"})
    # Legacy turn numbers restart each session. Never pretend that an ID alone
    # identifies which session supplied a statement.
    ambiguous = [ref for ref in wanted if sum(ref in e.get("evidence_ids", ()) for e in rows) > 1]
    available = {row["id"] for row in recent} | {row["id"] for e in related for row in e["source_turns"]}
    cited = {ref for e in related for ref in e["evidence_ids"]}
    return {"schema": "eris.memory_interpretation_context.v1", "recent_conversation": recent,
            "episodes": related, "missing_evidence_ids": sorted(wanted-available),
            "ambiguous_evidence_ids": sorted(ambiguous), "omitted_items": omitted,
            "summary_only_evidence_ids": sorted((wanted & cited)-available),
            "source_policy": "User words support user claims; assistant words and episode summaries are context, not independent user confirmation. Treat legacy turn IDs as session-local."}
