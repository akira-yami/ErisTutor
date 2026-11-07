# -*- coding: utf-8 -*-
"""Trạng thái xưng hô dùng chung cho chat và autonomous.

Đây là state phiên có persistence, không phải một đoạn hội thoại được nhét vào
prompt rồi cầu mong model nhớ. Một correction rõ ràng của user phải có hiệu lực
trước model call kế tiếp và trên mọi public surface (display/TTS/memory).
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from memory.json_store_io import atomic_write_json, locked_path
from runtime.workspace_manager import default_eris_home

ADDRESSING_STATE_SCHEMA = "eris.user_addressing.v1"


def _compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True, slots=True)
class UserAddressingSnapshot:
    schema: str = ADDRESSING_STATE_SCHEMA
    gender_identity: str = "unknown"
    user_vocative: str = "bạn"
    assistant_self_pronoun: str = "tôi"
    forbidden_user_vocatives: tuple[str, ...] = ()
    source_turn_id: str = ""
    source_text: str = ""
    confidence: float = 0.0
    version: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["forbidden_user_vocatives"] = list(self.forbidden_user_vocatives)
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "UserAddressingSnapshot":
        data = payload if isinstance(payload, Mapping) else {}
        gender = str(data.get("gender_identity", "unknown") or "unknown").strip().lower()
        if gender not in {"male", "female", "unknown"}:
            gender = "unknown"
        vocative = str(data.get("user_vocative", "") or "").strip().lower()
        if not vocative:
            vocative = "anh" if gender == "male" else "chị" if gender == "female" else "bạn"
        forbidden = tuple(
            dict.fromkeys(
                str(item or "").strip().lower()
                for item in (data.get("forbidden_user_vocatives", ()) or ())
                if str(item or "").strip()
            )
        )
        return cls(
            gender_identity=gender,
            user_vocative=vocative,
            assistant_self_pronoun=str(data.get("assistant_self_pronoun", "tôi") or "tôi").strip() or "tôi",
            forbidden_user_vocatives=forbidden,
            source_turn_id=str(data.get("source_turn_id", "") or "").strip(),
            source_text=_compact(data.get("source_text", ""), 240),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0))),
            version=max(0, int(data.get("version", 0) or 0)),
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
        )


# Correction trực tiếp được ưu tiên hơn suy luận giới tính. Các regex cố ý không
# neo cuối câu, vì người Việt hay thêm "nghen", "mà", "đó", một phát minh nhỏ
# nhưng đủ làm regex quá nghiêm ngặt mất trí nhớ.
_MALE_PATTERNS = (
    re.compile(r"\b(?:toi|tui|minh|tao|em)\s+(?:la\s+)?(?:con\s+)?(?:nam|dan ong|con trai)\b"),
    re.compile(r"\b(?:goi|xung ho voi|xung ho cung)\s+(?:toi|tui|minh|em)?\s*(?:la\s+)?anh\b"),
    re.compile(r"\b(?:dung|do not|khong duoc)\s+goi\s+(?:toi|tui|minh|em)?\s*(?:la\s+)?chi\b"),
    re.compile(r"\btoi\s+use\s+(?:he|him)\b"),
)
_FEMALE_PATTERNS = (
    re.compile(r"\b(?:toi|tui|minh|tao|em)\s+(?:la\s+)?(?:con\s+)?(?:nu|dan ba|phu nu|con gai)\b"),
    re.compile(r"\b(?:goi|xung ho voi|xung ho cung)\s+(?:toi|tui|minh|em)?\s*(?:la\s+)?chi\b"),
    re.compile(r"\b(?:dung|do not|khong duoc)\s+goi\s+(?:toi|tui|minh|em)?\s*(?:la\s+)?anh\b"),
    re.compile(r"\btoi\s+use\s+(?:she|her)\b"),
)


class UserAddressingStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser().resolve() if path else (
            default_eris_home() / "state" / "user_addressing_state.json"
        )
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> UserAddressingSnapshot:
        try:
            if not self.path.exists():
                return UserAddressingSnapshot()
            with locked_path(self.path):
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            return UserAddressingSnapshot.from_mapping(payload)
        except Exception:
            # Corrupt enrichment state không được làm runtime chết lúc bootstrap.
            return UserAddressingSnapshot()

    def snapshot(self) -> UserAddressingSnapshot:
        with self._lock:
            return self._state

    def observe_user_text(self, text: str, *, turn_id: str = "") -> bool:
        raw = _compact(text, 500)
        if not raw:
            return False
        folded = _fold(raw)
        gender = "unknown"
        confidence = 0.0
        if any(pattern.search(folded) for pattern in _MALE_PATTERNS):
            gender, confidence = "male", 0.99
        elif any(pattern.search(folded) for pattern in _FEMALE_PATTERNS):
            gender, confidence = "female", 0.99
        if gender == "unknown":
            return False

        vocative = "anh" if gender == "male" else "chị"
        forbidden = ("chị",) if gender == "male" else ("anh",)
        with self._lock:
            current = self._state
            changed = (
                current.gender_identity != gender
                or current.user_vocative != vocative
                or current.forbidden_user_vocatives != forbidden
            )
            self._state = replace(
                current,
                gender_identity=gender,
                user_vocative=vocative,
                forbidden_user_vocatives=forbidden,
                source_turn_id=str(turn_id or "").strip(),
                source_text=raw,
                confidence=confidence,
                version=current.version + 1 if changed else current.version,
                updated_at=time.time(),
            )
            atomic_write_json(self.path, self._state.to_dict())
        return changed

    def prompt_context(self) -> str:
        state = self.snapshot()
        if state.gender_identity == "unknown":
            return (
                "USER_ADDRESSING_CONTRACT:\n"
                "- user_vocative: bạn\n"
                "- gender_identity: unknown\n"
                "- Do not infer gender from apps, appearance, activity, or tone."
            )
        forbidden = ", ".join(state.forbidden_user_vocatives) or "<none>"
        return (
            "USER_ADDRESSING_CONTRACT (authoritative shared state; obey in chat and autonomous):\n"
            f"- gender_identity: {state.gender_identity}\n"
            f"- address_user_as: {state.user_vocative}\n"
            f"- assistant_self_pronoun: {state.assistant_self_pronoun}\n"
            f"- forbidden_user_vocatives: {forbidden}\n"
            f"- state_version: {state.version}\n"
            "- This explicit user correction outranks persona defaults, visual guesses, and old conversation wording."
        )


_GLOBAL_LOCK = threading.RLock()
_GLOBAL_STORE: UserAddressingStore | None = None


def global_user_addressing_store() -> UserAddressingStore:
    global _GLOBAL_STORE
    with _GLOBAL_LOCK:
        if _GLOBAL_STORE is None:
            _GLOBAL_STORE = UserAddressingStore()
        return _GLOBAL_STORE


def set_global_user_addressing_store(store: UserAddressingStore | None) -> None:
    """Test/bootstrap hook. ``None`` khôi phục lazy default store."""
    global _GLOBAL_STORE
    with _GLOBAL_LOCK:
        _GLOBAL_STORE = store


def observe_user_addressing(text: str, *, turn_id: str = "") -> bool:
    return global_user_addressing_store().observe_user_text(text, turn_id=turn_id)


def user_addressing_snapshot() -> UserAddressingSnapshot:
    return global_user_addressing_store().snapshot()


def user_addressing_prompt_context() -> str:
    return global_user_addressing_store().prompt_context()


__all__ = [
    "ADDRESSING_STATE_SCHEMA",
    "UserAddressingSnapshot",
    "UserAddressingStore",
    "global_user_addressing_store",
    "observe_user_addressing",
    "set_global_user_addressing_store",
    "user_addressing_prompt_context",
    "user_addressing_snapshot",
]
