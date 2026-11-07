# -*- coding: utf-8 -*-
"""Conservative proposal and lifecycle-control detector for engagements."""
from __future__ import annotations
from core.di import resolve_dependency

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from .contracts import EngagementProposal
from .profiles import EngagementProfile, EngagementProfileRegistry, collaboration_profile


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(text or "").lower())
    plain = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", plain).split())


def _has_phrase(text: str, phrases: Iterable[str]) -> bool:
    padded = f" {text} "
    return any(f" {normalize(phrase)} " in padded for phrase in phrases)


def _semantic_key(kind: str, objective: str) -> str:
    meaningful = [token for token in normalize(objective).split() if len(token) >= 3][:12]
    digest = hashlib.sha1(" ".join(meaningful).encode("utf-8", "ignore")).hexdigest()[:10]
    return f"{kind}:{digest}"


@dataclass(frozen=True, slots=True)
class ControlIntent:
    action: str = ""
    confidence: float = 0.0
    target_kind: str = ""
    target_profile_id: str = ""


class EngagementDetector:
    _CONTINUITY = (
        "cả ván", "ca van", "cả buổi", "ca buoi", "suốt buổi", "suot buoi",
        "liên tục", "lien tuc", "lâu dài", "lau dai", "cho đến khi", "cho den khi",
        "từ giờ đến khi", "tu gio den khi", "trong lúc", "trong luc", "cùng tôi", "cung toi",
        "đồng hành", "dong hanh", "theo dõi", "theo doi", "từng bước", "tung buoc",
        "nhắc tôi trong", "nhac toi trong", "stay with me", "throughout", "until it is done",
        "until done", "for the whole", "keep helping", "walk me through", "monitor",
    )
    _START_VERBS = (
        "hãy", "hay", "giúp", "giup", "hỗ trợ", "ho tro", "cùng", "cung", "theo dõi", "theo doi",
        "đồng hành", "dong hanh", "hướng dẫn", "huong dan", "nhắc", "nhac", "chơi", "choi",
        "help", "support", "guide", "play", "watch", "monitor", "coach", "stay",
        "pém", "pem", "đánh", "danh",
    )
    _SHARED = ("với tôi", "voi toi", "cùng tôi", "cung toi", "help me", "with me")
    _STOP = (
        "dừng việc này", "dung viec nay", "dừng task", "dung task", "kết thúc việc này", "ket thuc viec nay",
        "không cần hỗ trợ nữa", "khong can ho tro nua", "thôi việc này", "thoi viec nay",
        "dừng theo dõi", "dung theo doi", "hủy theo dõi", "huy theo doi",
        "stop this", "end this", "cancel this", "stop monitoring", "we are done", "done with this",
    )
    _PAUSE = (
        "tạm dừng", "tam dung", "pause this", "pause task", "để lát tiếp", "de lat tiep",
        "nghỉ một lát", "nghi mot lat",
    )
    _RESUME = (
        "tiếp tục việc này", "tiep tuc viec nay", "tiếp tục task", "tiep tuc task",
        "tiếp tục ván", "tiep tuc van", "tiếp tục học", "tiep tuc hoc", "tiếp tục code", "tiep tuc code",
        "tiếp tục theo dõi", "tiep tuc theo doi", "resume this", "continue this", "resume", "continue",
    )
    _COMPLETE = (
        "xong việc này", "xong task", "hoàn thành rồi", "hoan thanh roi", "hết ván", "het van",
        "xong ván", "xong van", "xong rồi", "xong roi", "finished this", "task completed", "game over",
    )

    def __init__(self, profiles: EngagementProfileRegistry | None = None) -> None:
        self.profiles = resolve_dependency(profiles, EngagementProfileRegistry.defaults)

    def register_profile(self, profile: EngagementProfile, *, replace: bool = False) -> None:
        self.profiles.register(profile, replace=replace)

    def control(self, text: str) -> ControlIntent:
        normalized = normalize(text)
        if not normalized or _has_phrase(normalized, ("chưa xong", "chua xong", "not done", "not finished")):
            return ControlIntent()
        profile = self.profiles.match(normalized, phrase_matcher=_has_phrase)
        target_kind = profile.kind if profile is not None else ""
        target_profile_id = profile.profile_id if profile is not None else ""
        if _has_phrase(normalized, self._PAUSE):
            return ControlIntent("pause", 0.90, target_kind, target_profile_id)
        if _has_phrase(normalized, self._STOP):
            return ControlIntent("cancel", 0.94, target_kind, target_profile_id)
        if _has_phrase(normalized, self._RESUME):
            return ControlIntent("resume", 0.90, target_kind, target_profile_id)
        if _has_phrase(normalized, self._COMPLETE):
            return ControlIntent("complete", 0.90, target_kind, target_profile_id)
        return ControlIntent()

    def propose(self, text: str) -> EngagementProposal | None:
        normalized = normalize(text)
        if not normalized:
            return None
        continuity = _has_phrase(normalized, self._CONTINUITY)
        direct = _has_phrase(normalized, self._START_VERBS)
        profile = self.profiles.match(normalized, phrase_matcher=_has_phrase) or collaboration_profile()
        shared_session = profile.shared_session and _has_phrase(normalized, self._SHARED)
        direct_profile = profile.direct_start_is_sufficient and direct
        if not (direct and (continuity or shared_session or direct_profile)):
            return None

        objective = " ".join(str(text or "").split()).strip()[:360]
        confidence = (
            profile.confidence_continuity if continuity
            else profile.confidence_shared if shared_session
            else profile.confidence_direct
        )
        return EngagementProposal(
            semantic_key=_semantic_key(profile.kind, objective),
            kind=profile.kind,
            objective=objective,
            adapter_id=profile.adapter_id,
            confidence=confidence,
            expected_duration="long_running",
            contract=profile.contract,
            continuity=profile.continuity,
            priority=profile.priority,
            focus=profile.focus,
            source="user_explicit",
        )


__all__ = ["ControlIntent", "EngagementDetector", "normalize"]
