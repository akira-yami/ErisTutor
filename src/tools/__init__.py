from __future__ import annotations

from pathlib import Path

_root_tools = Path(__file__).resolve().parents[2] / "tools"
if _root_tools.is_dir():
    root_tools_text = str(_root_tools)
    if root_tools_text not in __path__:
        __path__.append(root_tools_text)
