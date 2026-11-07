# -*- coding: utf-8 -*-
"""Bề mặt dùng khi commit history/ledger/log public.

StageFrame vẫn giữ raw text đủ lâu để bóc action/nonverbal sang side-channel.
Nhưng history, last_chat_answer, session ledger và dòng log ``ERIS:`` tuyệt đối
không được lưu raw text đó. Module này tạo một snapshot sạch riêng cho commit,
tránh hai lựa chọn tệ như nhau: hoặc làm mất stage action, hoặc để secret và
``(nháy mắt)`` sống bất tử trong memory.
"""

from __future__ import annotations

from dataclasses import dataclass

from runtime.final_surface_sanitizer import FinalSurfaceSanitizer
from runtime.stage_transformers import StageTransformerChain


@dataclass(frozen=True, slots=True)
class PublicCommitSurfaces:
    raw_text: str
    display_text: str
    memory_text: str
    tts_text: str
    blocked_kinds: tuple[str, ...] = ()


class PublicSurfaceCommitter:
    def __init__(self) -> None:
        self.transformers = StageTransformerChain()
        self.sanitizer = FinalSurfaceSanitizer()

    def build(self, text: str) -> PublicCommitSurfaces:
        raw = str(text or "")
        surfaces = self.transformers.transform(raw)
        # StageTransformerChain đã dùng sanitizer cho từng surface. Chạy thêm một
        # pass metadata trên raw để commit trace biết loại leak nào đã bị chặn.
        audit = self.sanitizer.sanitize(raw, surface="display")
        return PublicCommitSurfaces(
            raw_text=raw,
            display_text=surfaces.display_text,
            memory_text=surfaces.memory_text,
            tts_text=surfaces.tts_text,
            blocked_kinds=audit.blocked_kinds,
        )

    def clean_fact(self, text: str) -> str:
        """Fact assistant tự trích cũng phải qua van memory trước khi persist."""
        return self.transformers.transform(str(text or "")).memory_text


GLOBAL_PUBLIC_SURFACE_COMMITTER = PublicSurfaceCommitter()


__all__ = [
    "GLOBAL_PUBLIC_SURFACE_COMMITTER",
    "PublicCommitSurfaces",
    "PublicSurfaceCommitter",
]
