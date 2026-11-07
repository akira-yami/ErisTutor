# -*- coding: utf-8 -*-
"""Habitat seed extractor: lấy seed từ môi trường runtime/repo của ERIS."""

from __future__ import annotations

from typing import Any, Iterable

from .thought_spark import clamp01, clean_text, now_timestamp
from .wandering_contracts import TopicSeed


def _get(obj: Any, path: str, default: Any = None) -> Any:
    current = obj
    for part in str(path or "").split("."):
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
    return current if current is not None else default


def _iter_texts(value: Any, *, limit: int = 8) -> tuple[str, ...]:
    out: list[str] = []

    def add(item: Any) -> None:
        if len(out) >= limit:
            return
        if item is None:
            return
        if isinstance(item, dict):
            text = clean_text(item.get("name") or item.get("path") or item.get("module") or item.get("kind") or item.get("text") or item.get("summary"), limit=160)
        else:
            text = clean_text(getattr(item, "name", None) or getattr(item, "path", None) or getattr(item, "module", None) or getattr(item, "kind", None) or getattr(item, "text", None) or str(item), limit=160)
        if text and text not in out:
            out.append(text)

    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        add(value.decode("utf-8", "ignore") if isinstance(value, bytes) else value)
        return tuple(out)
    try:
        iterator = iter(value)
    except TypeError:
        add(value)
        return tuple(out)
    for item in iterator:
        add(item)
    return tuple(out)


class HabitatSeedExtractor:
    """Sinh seed từ memory_core/log/module, không bám user action trực tiếp."""

    DEFAULT_COMPONENTS: tuple[tuple[str, tuple[str, ...], float, float], ...] = (
        ("memory_core như nghĩa địa", ("memory", "graveyard", "habitat"), 0.38, 0.58),
        ("SurfaceArbiter như cửa miệng", ("surface_arbiter", "mouth_gate", "habitat"), 0.44, 0.50),
        ("MotionController giữ thân thể khỏi phản lời", ("motion", "body", "habitat"), 0.34, 0.36),
        ("event_ledger là vết chân thô", ("event_ledger", "memory", "habitat"), 0.32, 0.42),
        ("log folder là tiếng ồn sau gáy", ("log", "runtime", "habitat"), 0.30, 0.48),
    )

    WATCHED_OBJECTS: tuple[str, ...] = (
        "memory_core",
        "event_ledger",
        "SurfaceArbiter",
        "MotionController",
        "active workspace",
        "log folder",
        "low_level_speech_policy.yaml",
        "surface_arbiter.py",
        "presence_controller.py",
        "thought_stream.py",
    )

    def extract(
        self,
        *,
        frame: Any = None,
        snapshot: Any = None,
        memory_packet: Any = None,
        now: float | None = None,
        limit: int = 8,
    ) -> tuple[TopicSeed, ...]:
        current = now_timestamp() if now is None else float(now)
        out: list[TopicSeed] = []
        for text, tags, strength, weirdness in self.DEFAULT_COMPONENTS:
            out.append(
                TopicSeed(
                    text=text,
                    source="habitat",
                    strength=strength,
                    weirdness=weirdness,
                    clarity=0.44,
                    affect_tint=0.10,
                    tags=tags,
                    evidence_ids=("habitat_seed_extractor",),
                    from_user_action=False,
                    created_at=current,
                )
            )

        active_files = _iter_texts(
            _get(frame, "active_files") or _get(snapshot, "active_files") or _get(snapshot, "open_files"),
            limit=6,
        )
        recent_errors = _iter_texts(
            _get(frame, "recent_errors") or _get(snapshot, "recent_errors") or _get(frame, "runtime_events"),
            limit=5,
        )
        for text in active_files:
            lowered = text.lower()
            if not any(token in lowered for token in ("eris", "cortex", "runtime", "memory", "surface", "policy", ".py", ".yaml")):
                continue
            out.append(
                TopicSeed(
                    text=f"module quay lại: {text}",
                    source="habitat",
                    strength=0.36,
                    weirdness=0.34,
                    clarity=0.46,
                    affect_tint=0.08,
                    tags=("object_attachment", "module", "habitat"),
                    evidence_ids=(f"habitat.active_file:{text[:80]}",),
                    from_user_action=False,
                    created_at=current,
                )
            )
        for text in recent_errors:
            lowered = text.lower()
            if not any(token in lowered for token in ("error", "fail", "reject", "body_only", "timeout", "bug")):
                continue
            out.append(
                TopicSeed(
                    text=f"bug từng lặp: {text}",
                    source="habitat",
                    strength=0.42,
                    weirdness=0.32,
                    clarity=0.52,
                    affect_tint=0.14,
                    tags=("object_attachment", "failure_pattern", "habitat"),
                    evidence_ids=(f"habitat.recent_error:{text[:80]}",),
                    from_user_action=False,
                    created_at=current,
                )
            )

        # Nếu runtime không đưa gì mới, vẫn có habitat mặc định. Đây là điểm khác
        # biệt giữa một tâm trí nền và cái máy chỉ thở khi user bấm chuông.
        return tuple(sorted(out, key=lambda seed: seed.strength, reverse=True)[:limit])
