# -*- coding: utf-8 -*-
"""
cortex/private_life/persistence.py
==================================

Persistence JSON riêng cho PrivateLifeCore.

Lý do tách khỏi memory_cognition: private life là state vận hành của agent, không
phải fact user hay memory trả lời câu hỏi. Nó cần file riêng để debug đời sống
riêng mà không lẫn với semantic memory.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from .contracts import PrivateLifeState


def atomic_write_json(path: str | os.PathLike[str], payload: Any) -> None:
    """Ghi JSON bằng temp file rồi replace.

    State mà hỏng giữa lúc ghi thì tick sau thành khảo cổ học. Ta không cần thêm
    ngành nghề đó vào runtime.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, target)


class PrivateLifeStore:
    """Kho state bền cho PrivateLifeCore."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "PrivateLifeStore":
        """Tạo store theo env.

        ERIS_PRIVATE_LIFE_STATE_PATH thắng tuyệt đối. Nếu không có, dùng
        ERIS_MEMORY_CORE_DIR/private_life_state.json. Đây là đường ít gây nhầm nhất:
        cùng memory_core nhưng file riêng.
        """

        explicit = os.getenv("ERIS_PRIVATE_LIFE_STATE_PATH", "").strip()
        if explicit:
            return cls(explicit)
        memory_dir = os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core").strip() or "memory_core"
        return cls(Path(memory_dir) / "private_life_state.json")

    def load(self) -> PrivateLifeState:
        if not self.path.exists() or self.path.stat().st_size <= 0:
            return PrivateLifeState()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, Mapping):
                return PrivateLifeState()
            return PrivateLifeState.from_mapping(data)
        except Exception:
            # Không cho JSON bẩn làm chết runtime. Core sẽ rebuild state tối thiểu,
            # còn file hỏng nên được debug bằng audit riêng.
            return PrivateLifeState()

    def save(self, state: PrivateLifeState) -> None:
        atomic_write_json(self.path, state.to_dict())
