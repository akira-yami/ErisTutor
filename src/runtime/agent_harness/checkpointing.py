# -*- coding: utf-8 -*-
"""Durable LangGraph checkpoint factory.

SQLite là checkpoint authority cho task local. JSON task store chỉ là projection
cho UI/control plane, tuyệt đối không thay thế graph state.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any


def default_langgraph_checkpoint_path(task_store: Any = None) -> Path:
    explicit = str(os.getenv("ERIS_LANGGRAPH_CHECKPOINT_DB", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    store_path = Path(getattr(task_store, "path", Path(".eris_state") / "agent_console" / "tasks.json"))
    return (store_path.parent / "langgraph.sqlite").resolve()


class DurableSqliteCheckpointer:
    """Giữ connection sống cùng AgentHarness và đóng rõ ràng khi shutdown."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        from langgraph.checkpoint.sqlite import SqliteSaver

        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # SqliteSaver có lock nội bộ; check_same_thread=False cho phép các worker
        # task khác nhau dùng chung saver mà không bị giới hạn theo thread tạo DB.
        self.connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self.saver = SqliteSaver(self.connection)

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:
            pass


__all__ = ["DurableSqliteCheckpointer", "default_langgraph_checkpoint_path"]
