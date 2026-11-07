# -*- coding: utf-8 -*-
"""
cortex/private_mind/thought_continuity.py
=========================================

Dòng suy nghĩ liên tục của Eris, lưu bền ra JSONL.

Mục đích: mỗi lần PrivateMind nghĩ, nó KHÔNG bắt đầu từ con số không. Nó nhớ vài
suy nghĩ gần nhất và những mạch còn dang dở, để nghĩ tiếp như một con người có
trí nhớ ngắn hạn — chứ không phải một con bot cứ đến giờ là phán một câu rời rạc.

Triết lý:
- Liên tục là chính: recent() trả mạch nghĩ gần đây để model nối tiếp.
- Chỉ nghĩ bổ sung cho cái CHƯA xong: unresolved() trả các thread mở, hiếm khi
  được khơi lại (có cooldown lớn) thay vì nghĩ lại liên tục.
- Bền qua restart: ghi từng dòng JSONL, nạp lại lúc khởi động.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def _now() -> float:
    return time.time()


def _clamp01(x: Any) -> float:
    try:
        v = float(x)
    except Exception:
        return 0.0
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


@dataclass
class ThoughtEntry:
    """Một suy nghĩ trong dòng liên tục."""

    ts: float = field(default_factory=_now)
    text: str = ""                 # độc thoại nội tâm, ngôi thứ nhất
    theme: str = ""                # nhãn ngắn của mạch nghĩ
    status: str = "open"           # open | resolved | drifting
    pressure: float = 0.0          # còn nặng lòng tới đâu
    confidence: float = 0.0
    mood: str = ""                 # tâm trạng lúc nghĩ (nếu có)
    concern_ids: tuple[str, ...] = ()
    source: str = "synthesis"
    revisit_count: int = 0
    last_revisit_at: float = 0.0

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["concern_ids"] = list(self.concern_ids)
        return d

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ThoughtEntry":
        return cls(
            ts=float(data.get("ts") or _now()),
            text=str(data.get("text") or ""),
            theme=str(data.get("theme") or ""),
            status=str(data.get("status") or "open"),
            pressure=_clamp01(data.get("pressure", 0.0)),
            confidence=_clamp01(data.get("confidence", 0.0)),
            mood=str(data.get("mood") or ""),
            concern_ids=tuple(str(x) for x in (data.get("concern_ids") or ()) if str(x).strip()),
            source=str(data.get("source") or "synthesis"),
            revisit_count=int(data.get("revisit_count") or 0),
            last_revisit_at=float(data.get("last_revisit_at") or 0.0),
        )


class ThoughtContinuityStore:
    """Lưu và gọi lại dòng suy nghĩ liên tục.

    Bộ nhớ trong RAM là nguồn chính; JSONL chỉ để bền qua restart. Mọi lỗi I/O
    đều nuốt êm — tính liên tục là tiện ích, tuyệt đối không được làm gãy tick.
    """

    def __init__(
        self,
        *,
        path: str | os.PathLike[str] | None = None,
        max_entries: int = 200,
        recent_default: int = 5,
        unresolved_cooldown_sec: float = 900.0,
        unresolved_min_pressure: float = 0.45,
        max_age_seconds: float = 7 * 86400.0,
        logger: Any = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.max_entries = max(10, int(max_entries or 200))
        self.recent_default = max(1, int(recent_default or 5))
        self.unresolved_cooldown_sec = max(60.0, float(unresolved_cooldown_sec or 900.0))
        self.unresolved_min_pressure = _clamp01(unresolved_min_pressure)
        self.max_age_seconds = max(3600.0, float(max_age_seconds or 7 * 86400.0))
        self.logger = logger
        self._entries: list[ThoughtEntry] = []
        self._load()

    @classmethod
    def from_env(cls, *, logger: Any = None) -> "ThoughtContinuityStore":
        raw_path = os.getenv("ERIS_THOUGHT_CONTINUITY_PATH", "") or ""
        path = raw_path.strip() or str(Path("memory_core") / "thought_stream.jsonl")
        return cls(
            path=path,
            max_entries=int(os.getenv("ERIS_THOUGHT_CONTINUITY_MAX", "200") or 200),
            recent_default=int(os.getenv("ERIS_THOUGHT_CONTINUITY_RECENT", "5") or 5),
            unresolved_cooldown_sec=float(os.getenv("ERIS_THOUGHT_FOLLOWUP_COOLDOWN_SEC", "900") or 900.0),
            unresolved_min_pressure=float(os.getenv("ERIS_THOUGHT_FOLLOWUP_MIN_PRESSURE", "0.45") or 0.45),
            max_age_seconds=float(os.getenv("ERIS_THOUGHT_CONTINUITY_MAX_AGE_SEC", str(7 * 86400)) or 7 * 86400),
            logger=logger,
        )

    # ------------------------------------------------------------------ I/O
    def _log(self, level: str, msg: str, *args: Any) -> None:
        if self.logger is None:
            return
        fn = getattr(self.logger, level, None)
        if callable(fn):
            try:
                fn(msg, *args)
            except Exception:
                pass

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        entries: list[ThoughtEntry] = []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(ThoughtEntry.from_json(json.loads(line)))
                    except Exception:
                        continue
        except Exception as exc:
            self._log("warning", "[ThoughtContinuity] load error: %s", exc)
            return
        # V41: thought stream là transient cognition, không phải nhật ký bất tử.
        # Open thought có thể sống lâu nếu còn pressure; resolved/drifting hết hạn nhanh hơn.
        entries.sort(key=lambda e: e.ts)
        self._entries = self._pruned(entries)[-self.max_entries :]

    def _rewrite_file(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as fh:
                for e in self._entries:
                    fh.write(json.dumps(e.to_json(), ensure_ascii=False) + "\n")
        except Exception as exc:
            self._log("warning", "[ThoughtContinuity] rewrite error: %s", exc)

    def _append_line(self, entry: ThoughtEntry) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")
        except Exception as exc:
            self._log("warning", "[ThoughtContinuity] append error: %s", exc)

    # -------------------------------------------------------------- mutators
    def append(self, entry: ThoughtEntry) -> None:
        if not entry.text.strip():
            return
        self._entries.append(entry)
        before = len(self._entries)
        self._entries = self._pruned(self._entries)[-self.max_entries :]
        trimmed = len(self._entries) != before
        if trimmed:
            self._rewrite_file()  # TTL/cắt nên ghi lại toàn bộ
        else:
            self._append_line(entry)

    def record_thought(
        self,
        *,
        text: str,
        theme: str = "",
        status: str = "open",
        pressure: float = 0.0,
        confidence: float = 0.0,
        mood: str = "",
        concern_ids: tuple[str, ...] = (),
        source: str = "synthesis",
    ) -> ThoughtEntry | None:
        text = " ".join(str(text or "").split())
        if not text:
            return None
        entry = ThoughtEntry(
            text=text,
            theme=str(theme or "").strip()[:120],
            status=status if status in {"open", "resolved", "drifting"} else "open",
            pressure=_clamp01(pressure),
            confidence=_clamp01(confidence),
            mood=str(mood or "").strip()[:40],
            concern_ids=tuple(str(c) for c in (concern_ids or ()) if str(c).strip())[:8],
            source=str(source or "synthesis")[:40],
        )
        self.append(entry)
        return entry

    def mark_revisited(self, theme: str) -> None:
        theme = str(theme or "").strip()
        if not theme:
            return
        now = _now()
        for e in reversed(self._entries):
            if e.theme == theme:
                e.revisit_count += 1
                e.last_revisit_at = now
                break

    # --------------------------------------------------------------- readers
    def recent(self, n: int | None = None) -> tuple[ThoughtEntry, ...]:
        count = self.recent_default if n is None else max(1, int(n))
        self._entries = self._pruned(self._entries)[-self.max_entries :]
        return tuple(self._entries[-count:])

    def recent_for_prompt(self, n: int | None = None) -> list[dict[str, Any]]:
        """Mạch nghĩ gần đây ở dạng gọn cho prompt — để model nối tiếp."""
        out: list[dict[str, Any]] = []
        now = _now()
        for e in self.recent(n):
            # Thought model có thể tự suy đoán nhất thời. Chỉ feed lại nếu mạch còn
            # pressure/confidence đủ để đáng nối; còn lại để nó chết trong transient state.
            if e.status == "resolved":
                continue
            if e.confidence < 0.35 and e.pressure < 0.55:
                continue
            ago = max(0, int((now - e.ts)))
            out.append({
                "khi": self._humanize_ago(ago),
                "nghi": e.text[:240],
                "trang_thai": e.status,
            })
        return out

    def unresolved(self, *, limit: int = 3) -> tuple[ThoughtEntry, ...]:
        """Các mạch nghĩ còn dang dở, đủ nặng và đã nguội đủ lâu để khơi lại.

        Đây là con đường DUY NHẤT để nghĩ-bổ-sung khi không có bằng chứng mới —
        cố ý hiếm (cooldown lớn + ngưỡng áp lực) để không thành con bot đếm giờ.
        """
        now = _now()
        seen_theme: set[str] = set()
        picked: list[ThoughtEntry] = []
        for e in reversed(self._entries):
            if e.status != "open" or e.pressure < self.unresolved_min_pressure:
                continue
            if e.theme and e.theme in seen_theme:
                continue
            last_touch = max(e.last_revisit_at, e.ts)
            if now - last_touch < self.unresolved_cooldown_sec:
                continue  # vừa nghĩ rồi, chưa đến lúc khơi lại
            if e.theme:
                seen_theme.add(e.theme)
            picked.append(e)
            if len(picked) >= max(1, int(limit)):
                break
        return tuple(picked)

    def unresolved_for_prompt(self, *, limit: int = 3) -> list[dict[str, Any]]:
        return [
            {"mach": e.theme or e.text[:60], "con_vuong": e.text[:200], "ap_luc": round(e.pressure, 2)}
            for e in self.unresolved(limit=limit)
        ]

    def has_followup_due(self) -> bool:
        return bool(self.unresolved(limit=1))

    def compact(self) -> dict[str, int]:
        """Dọn transient thought theo TTL và rewrite JSONL."""
        before = len(self._entries)
        self._entries = self._pruned(self._entries)[-self.max_entries :]
        self._rewrite_file()
        return {"before": before, "after": len(self._entries), "removed": max(0, before - len(self._entries))}

    def _pruned(self, entries: list[ThoughtEntry]) -> list[ThoughtEntry]:
        now = _now()
        kept: list[ThoughtEntry] = []
        for entry in entries:
            age = max(0.0, now - float(entry.ts or now))
            ttl = self.max_age_seconds
            if entry.status == "resolved":
                ttl = min(ttl, 12 * 3600.0)
            elif entry.status == "drifting":
                ttl = min(ttl, 36 * 3600.0)
            elif entry.pressure >= 0.72 and entry.confidence >= 0.55:
                ttl *= 1.5
            if age <= ttl:
                kept.append(entry)
        return kept

    @staticmethod
    def _humanize_ago(seconds: int) -> str:
        if seconds < 60:
            return "vừa nãy"
        mins = seconds // 60
        if mins < 60:
            return f"{mins} phút trước"
        hours = mins // 60
        if hours < 24:
            return f"{hours} giờ trước"
        return f"{hours // 24} ngày trước"

    def digest(self) -> dict[str, Any]:
        return {
            "total": len(self._entries),
            "recent": self.recent_for_prompt(),
            "unresolved": self.unresolved_for_prompt(),
        }
