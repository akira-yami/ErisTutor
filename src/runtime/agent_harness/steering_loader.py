"""Eris Steering Files Loader

Doc cac file .eris/steering/*.md tu workspace va inject vao coding-team context.
Tuong tu Kiro steering files: product.md, structure.md, tech.md, v.v.

Usage:
    steering = load_steering(workspace_path)
    team_runner.run_task_blocking(..., steering_context=steering)
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Thu tu doc: global truoc, workspace sau (workspace override global)
_GLOBAL_STEERING_DIR = Path.home() / ".eris" / "steering"
_WORKSPACE_STEERING_DIR = ".eris/steering"

# Ten file co thu tu uu tien (doc truoc)
_PREFERRED_ORDER = ["product.md", "structure.md", "tech.md", "conventions.md"]


def load_steering(workspace_path: str | Path | None = None) -> str:
    """Doc tat ca steering files va tra ve string de inject vao system prompt.

    Tim theo thu tu:
    1. ~/.eris/steering/*.md  (global, ap dung moi project)
    2. <workspace>/.eris/steering/*.md  (project-specific, override global)

    Args:
        workspace_path: Path den workspace. None = bo qua workspace-specific.

    Returns:
        String chay ca hai nguon, format Markdown, san sang inject vao prompt.
        Tra ve "" neu khong co file nao.
    """
    sections: list[str] = []

    # 1. Global steering
    if _GLOBAL_STEERING_DIR.exists():
        global_content = _read_steering_dir(_GLOBAL_STEERING_DIR, label="Global")
        if global_content:
            sections.append(global_content)

    # 2. Workspace-specific steering
    if workspace_path:
        ws_dir = Path(workspace_path) / _WORKSPACE_STEERING_DIR
        if ws_dir.exists():
            ws_content = _read_steering_dir(ws_dir, label="Project")
            if ws_content:
                sections.append(ws_content)

    return "\n\n".join(sections)


def _read_steering_dir(steering_dir: Path, label: str = "") -> str:
    """Doc tat ca .md files trong thu muc, tra ve string."""
    try:
        md_files = [f for f in steering_dir.iterdir() if f.suffix == ".md" and f.is_file()]
    except PermissionError:
        return ""

    if not md_files:
        return ""

    # Sap xep: preferred order truoc, sau do alphabetical
    def sort_key(f: Path) -> tuple[int, str]:
        try:
            return (_PREFERRED_ORDER.index(f.name), f.name)
        except ValueError:
            return (len(_PREFERRED_ORDER), f.name)

    md_files.sort(key=sort_key)

    file_sections: list[str] = []
    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8").strip()
            if content:
                file_sections.append(f"### {md_file.stem}\n{content}")
                logger.debug("[steering] loaded %s (%d chars)", md_file.name, len(content))
        except Exception as exc:
            logger.debug("[steering] failed to read %s: %s", md_file.name, exc)

    if not file_sections:
        return ""

    header = f"## {label} Steering" if label else "## Steering"
    return header + "\n\n" + "\n\n".join(file_sections)


def ensure_steering_dir(workspace_path: str | Path) -> Path:
    """Tao thu muc .eris/steering neu chua co. Tra ve Path den thu muc."""
    steering_dir = Path(workspace_path) / _WORKSPACE_STEERING_DIR
    steering_dir.mkdir(parents=True, exist_ok=True)
    return steering_dir


def write_steering_file(
    workspace_path: str | Path,
    filename: str,
    content: str,
) -> Path:
    """Ghi mot steering file. Filename khong can .md extension (tu dong them).

    Returns: Path den file da ghi.
    """
    if not filename.endswith(".md"):
        filename = filename + ".md"
    steering_dir = ensure_steering_dir(workspace_path)
    file_path = steering_dir / filename
    file_path.write_text(content, encoding="utf-8")
    logger.info("[steering] wrote %s (%d chars)", file_path, len(content))
    return file_path
