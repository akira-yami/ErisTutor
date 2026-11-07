# -*- coding: utf-8 -*-
"""Cách 1: Repo Map — Generic skeleton generator (multi-language).

Dùng lang_parsers để parse bất kỳ ngôn ngữ nào.
Không hardcode cho eris hay bất kỳ project cụ thể nào.

Output: plain text (repo_map.txt) + JSON summary (repo_map.json)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .lang_parsers import (
    FileEntry,
    SKIP_EXTENSIONS,
    SKIP_FILENAMES,
    entry_to_text,
    parse_file,
)
from .repo_detector import _SKIP_DIRS_GLOBAL, RepoInfo

# ─────────────────────────────────────────────────────────────────────────────
# File collection
# ─────────────────────────────────────────────────────────────────────────────

def collect_files(
    scope: list[Path] | None,
    repo_root: Path,
    *,
    skip_dirs: set[str] | None = None,
    skip_files: set[str] | None = None,
    skip_exts: set[str] | None = None,
) -> list[Path]:
    """Quét files trong scope (hoặc toàn repo nếu scope=None).

    Args:
        scope: Danh sách Path để quét. None = [repo_root].
        repo_root: Gốc repo.
        skip_dirs: Tên thư mục bỏ qua.
        skip_files: Tên file bỏ qua.
        skip_exts: Extensions bỏ qua.
    """
    _skip_dirs = skip_dirs or _SKIP_DIRS_GLOBAL
    _skip_files = skip_files or SKIP_FILENAMES
    _skip_exts = skip_exts or SKIP_EXTENSIONS

    roots = scope if scope else [repo_root]
    result: list[Path] = []
    seen: set[str] = set()

    for root in roots:
        if root.is_file():
            canonical = str(root.resolve())
            if canonical not in seen:
                seen.add(canonical)
                result.append(root)
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune skip dirs
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in _skip_dirs and not d.startswith(".")
            )
            dp = Path(dirpath)
            for fn in sorted(filenames):
                if fn in _skip_files:
                    continue
                fp = dp / fn
                suffix = fp.suffix.lower()
                if suffix in _skip_exts:
                    continue
                canonical = str(fp.resolve())
                if canonical not in seen:
                    seen.add(canonical)
                    result.append(fp)

    return sorted(result, key=lambda p: str(p))


# ─────────────────────────────────────────────────────────────────────────────
# Build repo map
# ─────────────────────────────────────────────────────────────────────────────

def build_repo_map(
    repo_root: Path,
    *,
    scope: list[Path] | None = None,
    verbose: bool = False,
    repo_info: RepoInfo | None = None,
) -> dict[str, Any]:
    """Build repo map cho bất kỳ repo nào.

    Args:
        repo_root: Gốc repo.
        scope: Giới hạn scan. None = toàn repo.
        verbose: In tiến trình.
        repo_info: RepoInfo đã detect (tùy chọn, để embed metadata).

    Returns:
        Dict với:
            - entries: list[FileEntry] — skeletons
            - stats: dict — thống kê
            - repo_info_summary: dict — thông tin repo
            - generated_at: float
    """
    t0 = time.perf_counter()
    files = collect_files(scope, repo_root)

    if verbose:
        print(f"[repo_map] {len(files)} files to parse...", file=sys.stderr)

    entries: list[FileEntry] = []
    skipped = 0
    errors = 0
    lang_counts: dict[str, int] = {}

    for i, fp in enumerate(files):
        if verbose and i % 100 == 0 and i > 0:
            print(f"[repo_map]   {i}/{len(files)}...", file=sys.stderr)

        entry = parse_file(fp)
        if entry is None:
            skipped += 1
            continue
        entries.append(entry)
        if entry.parse_error:
            errors += 1
        lang_counts[entry.language] = lang_counts.get(entry.language, 0) + 1

    elapsed = time.perf_counter() - t0

    total_classes = sum(len(e.classes) for e in entries)
    total_functions = sum(len(e.functions) for e in entries)
    total_methods = sum(
        sum(len(c.methods) for c in e.classes) for e in entries
    )
    total_bytes = sum(e.size_bytes for e in entries)

    stats = {
        "total_files": len(entries),
        "skipped_files": skipped,
        "total_classes": total_classes,
        "total_functions": total_functions,
        "total_methods": total_methods,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
        "parse_errors": errors,
        "elapsed_seconds": round(elapsed, 2),
        "languages": dict(sorted(lang_counts.items(), key=lambda x: -x[1])),
    }

    repo_meta: dict[str, Any] = {}
    if repo_info:
        repo_meta = {
            "name": repo_info.name,
            "repo_id": repo_info.repo_id,
            "dominant_lang": repo_info.dominant_lang,
        }

    if verbose:
        lang_str = ", ".join(f"{l}:{c}" for l, c in stats["languages"].items())
        print(
            f"[repo_map] Done: {stats['total_files']} files "
            f"({stats['total_classes']} classes, {stats['total_functions']} functions) "
            f"in {elapsed:.2f}s | Languages: {lang_str}",
            file=sys.stderr,
        )

    return {
        "entries": entries,
        "stats": stats,
        "repo_info_summary": repo_meta,
        "generated_at": time.time(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Output formatting
# ─────────────────────────────────────────────────────────────────────────────

def repo_map_to_text(result: dict[str, Any], repo_root: Path) -> str:
    """Chuyển repo map → text đẹp (human + AI readable)."""
    stats = result.get("stats", {})
    meta = result.get("repo_info_summary", {})

    header_lines = [
        "# ═══════════════════════════════════════",
        f"# REPO MAP: {meta.get('name', repo_root.name)}",
        "# ═══════════════════════════════════════",
        f"# Files  : {stats.get('total_files', 0)}",
        f"# Classes: {stats.get('total_classes', 0)}",
        f"# Funcs  : {stats.get('total_functions', 0)}",
        f"# Methods: {stats.get('total_methods', 0)}",
        f"# Size   : {stats.get('total_mb', 0)}MB source",
        f"# Time   : {stats.get('elapsed_seconds', 0)}s",
    ]
    if stats.get("languages"):
        lang_str = " | ".join(
            f"{l}({c})" for l, c in stats["languages"].items()
        )
        header_lines.append(f"# Langs  : {lang_str}")
    header_lines.append("# ─────────────────────────────────────────")

    body_lines = []
    for entry in result.get("entries", []):
        body_lines.append(entry_to_text(entry, repo_root))

    return "\n".join(header_lines) + "\n" + "\n".join(body_lines)


def save_repo_map(
    result: dict[str, Any],
    repo_root: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Lưu repo map ra text + JSON.

    Returns:
        (text_path, json_path)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    text_path = output_dir / "repo_map.txt"
    json_path = output_dir / "repo_map.json"

    # Text
    text_path.write_text(repo_map_to_text(result, repo_root), encoding="utf-8")

    # JSON summary (không ghi full entry objects vì quá lớn)
    json_data = {
        "generated_at": result.get("generated_at"),
        "repo_info": result.get("repo_info_summary", {}),
        "stats": result.get("stats", {}),
        "entries_summary": [
            {
                "path": str(Path(e.path).relative_to(repo_root).as_posix())
                if hasattr(e, "path") else str(e.path),
                "language": e.language,
                "size_bytes": e.size_bytes,
                "module_doc": e.module_doc,
                "class_count": len(e.classes),
                "function_count": len(e.functions),
            }
            for e in result.get("entries", [])
            if hasattr(e, "language")
        ],
    }
    json_path.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return text_path, json_path
