# -*- coding: utf-8 -*-
"""Weekly consolidation cho Open-ended Personality Runtime.

Consolidator không có đích đạo đức và không tự gọi model. Nó chỉ đọc trajectory
đã được StateReducer xác nhận, tìm chiến lược/diễn giải lặp qua nhiều session và
dịch ``temperament_attractor`` rất nhỏ. Khi evidence kể hai câu chuyện ngược
nhau, nó ghi uncertainty rồi không ép một kết luận đẹp cho panel.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from .personality import DEFAULT_TEMPERAMENT, NarrativeState, PersonalityProfile, clamp01, compact

WEEK_SECONDS = 7 * 86400.0
WINDOW_SECONDS = 14 * 86400.0
MAX_WEEKLY_SHIFT = 0.006


def _sign(value: float, *, epsilon: float = 1e-9) -> int:
    if value > epsilon:
        return 1
    if value < -epsilon:
        return -1
    return 0


def _session_key(item: Mapping[str, Any]) -> str:
    session_id = compact(item.get("session_id", ""), 100)
    if session_id:
        return session_id
    # Dữ liệu migration cũ không có session id. Không giả vờ mỗi turn là một
    # session khác; gom vào một bucket legacy để nó không vượt corroboration.
    return "legacy_unknown_session"


def _new_entries(profile: PersonalityProfile, *, now: float) -> list[dict[str, Any]]:
    lower_bound = max(
        now - WINDOW_SECONDS,
        float(profile.last_consolidated_at or 0.0),
    )
    return [
        dict(item)
        for item in profile.trajectory
        if float(item.get("timestamp", 0.0) or 0.0) > lower_bound
    ]


def should_consolidate(profile: PersonalityProfile, *, now: float | None = None) -> bool:
    current = float(now if now is not None else time.time())
    entries = _new_entries(profile, now=current)
    if not entries:
        return False
    if profile.last_consolidated_at <= 0.0:
        sessions = {_session_key(item) for item in entries}
        return len(entries) >= 8 and len(sessions) >= 2
    return current - profile.last_consolidated_at >= WEEK_SECONDS


def consolidate_personality(
    profile: PersonalityProfile,
    *,
    now: float | None = None,
    force: bool = False,
) -> PersonalityProfile:
    """Dịch attractor từ evidence lặp, không sửa trait hiện tại trực tiếp."""

    current = float(now if now is not None else time.time())
    if not force and not should_consolidate(profile, now=current):
        return profile

    entries = _new_entries(profile, now=current)
    stable_entries = [
        item
        for item in entries
        if str(item.get("category", "") or "") == "temperament"
        and abs(float(item.get("applied_delta", 0.0) or 0.0)) > 1e-9
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in stable_entries:
        trait = str(item.get("trait", "") or "")
        if trait in DEFAULT_TEMPERAMENT:
            grouped[trait].append(item)

    attractor = dict(profile.temperament_attractor)
    shifts: dict[str, float] = {}
    skipped: dict[str, str] = {}
    uncertainty: dict[str, float] = {}

    for trait, group in grouped.items():
        sessions = {_session_key(item) for item in group}
        if len(group) < 2 or len(sessions) < 2:
            skipped[trait] = "needs_repetition_across_sessions"
            continue

        directions = [_sign(float(item.get("applied_delta", 0.0) or 0.0)) for item in group]
        direction_counts = Counter(value for value in directions if value)
        if not direction_counts:
            continue
        dominant_direction, dominant_count = direction_counts.most_common(1)[0]
        direction_consistency = dominant_count / max(1, sum(direction_counts.values()))
        if direction_consistency < 0.67:
            uncertainty[trait] = round(1.0 - direction_consistency, 4)
            skipped[trait] = "conflicting_direction"
            continue

        selected = [
            item for item in group
            if _sign(float(item.get("applied_delta", 0.0) or 0.0)) == dominant_direction
        ]
        frames = [
            str(item.get("interpretation_frame", "") or "")
            for item in selected
            if str(item.get("interpretation_frame", "") or "") not in {"", "uncertain"}
        ]
        frame_consistency = 0.72
        if frames:
            frame_consistency = Counter(frames).most_common(1)[0][1] / len(frames)
        reinforcement_signs = [
            _sign(float(item.get("reinforcement", 0.0) or 0.0), epsilon=0.05)
            for item in selected
        ]
        reinforcement_signs = [value for value in reinforcement_signs if value]
        consequence_consistency = 0.70
        if reinforcement_signs:
            consequence_consistency = (
                Counter(reinforcement_signs).most_common(1)[0][1]
                / len(reinforcement_signs)
            )
        if frame_consistency < 0.60 or consequence_consistency < 0.60:
            uncertainty[trait] = round(
                max(1.0 - frame_consistency, 1.0 - consequence_consistency),
                4,
            )
            skipped[trait] = "interpretation_or_outcome_conflict"
            continue

        average_confidence = sum(
            float(item.get("confidence", 0.0) or 0.0) for item in selected
        ) / max(1, len(selected))
        sources = {
            str(item.get("source", "") or "") for item in selected
            if str(item.get("source", "") or "")
        }
        source_balance = min(1.0, 0.55 + 0.15 * len(sources))
        ema_like_delta = sum(
            float(item.get("applied_delta", 0.0) or 0.0)
            * (0.72 ** index)
            for index, item in enumerate(reversed(selected[-8:]))
        )
        shift = (
            ema_like_delta
            * max(0.45, average_confidence)
            * max(0.45, frame_consistency)
            * max(0.45, consequence_consistency)
            * source_balance
            * 0.35
        )
        shift = max(-MAX_WEEKLY_SHIFT, min(MAX_WEEKLY_SHIFT, shift))
        if abs(shift) < 1e-7:
            continue
        before = float(attractor.get(trait, DEFAULT_TEMPERAMENT[trait]))
        after = clamp01(before + shift)
        applied = after - before
        if abs(applied) < 1e-7:
            continue
        attractor[trait] = after
        shifts[trait] = round(applied, 6)

    narrative = profile.narrative
    if narrative.text:
        matching_frames = [
            item for item in stable_entries
            if str(item.get("interpretation_frame", "") or "") == narrative.frame
        ]
        conflicting_frames = [
            item for item in stable_entries
            if str(item.get("interpretation_frame", "") or "")
            not in {"", "uncertain", narrative.frame}
        ]
        confidence = narrative.confidence
        if len(matching_frames) >= 2:
            confidence = clamp01(confidence + 0.03)
        if len(conflicting_frames) > len(matching_frames):
            confidence = clamp01(confidence - 0.05)
        narrative = NarrativeState.from_mapping(
            {
                **narrative.to_dict(),
                "confidence": confidence,
                "provenance": [
                    *narrative.provenance,
                    f"weekly_consolidation:{int(current)}",
                ][-8:],
                "updated_at": current,
            }
        )

    ledger = [dict(item) for item in profile.consolidation_ledger]
    ledger.append(
        {
            "timestamp": current,
            "window_start": max(current - WINDOW_SECONDS, float(profile.last_consolidated_at or 0.0)),
            "events_read": len(entries),
            "stable_events": len(stable_entries),
            "sessions": sorted({_session_key(item) for item in entries})[:12],
            "attractor_shifts": shifts,
            "skipped": skipped,
            "uncertainty": uncertainty,
        }
    )

    return PersonalityProfile.from_mapping(
        {
            **profile.to_dict(),
            "version": profile.version + 1,
            "temperament_attractor": attractor,
            "narrative": narrative.to_dict(),
            "consolidation_ledger": ledger[-24:],
            "last_consolidated_at": current,
            "last_updated_at": max(profile.last_updated_at, current),
        }
    )


__all__ = [
    "MAX_WEEKLY_SHIFT",
    "WEEK_SECONDS",
    "consolidate_personality",
    "should_consolidate",
]
