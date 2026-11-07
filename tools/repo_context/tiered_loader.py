# -*- coding: utf-8 -*-
"""Cách 2: Tiered Context Loader — Generic, không hardcode module names.

Phân tầng T0→T3. Module filter dựa trên top-level dirs thực tế của repo,
không phải list cứng. Dùng RepoInfo từ repo_detector.

T0  — File tree + notable root files                      (~5KB)
T1  — Module summaries (1-2 dòng/file theo top dir)      (~20KB)
T2  — Function signatures (AST skeleton)                  (~50KB)
T3  — Full file content (on-demand)                       (unlimited)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from .repo_detector import RepoInfo, detect_repo, _SKIP_DIRS_GLOBAL
from .lang_parsers import entry_to_text, parse_file, SKIP_EXTENSIONS, SKIP_FILENAMES
from .repo_map import collect_files, build_repo_map


# ─────────────────────────────────────────────────────────────────────────────
# Tier helpers
# ─────────────────────────────────────────────────────────────────────────────

def _file_tree(info: RepoInfo, *, max_per_dir: int = 25) -> str:
    """Sinh file tree dựa trên RepoInfo (top dirs đã biết)."""
    root = info.root
    lines = [f"# {info.name}/ [{info.total_files} source files]"]
    lines.append(f"# Languages: " + ", ".join(
        f"{l}({c})" for l, c in list(info.languages.items())[:6]
    ))
    lines.append("")

    for d in info.top_dirs[:12]:
        dp = root / d
        count = info.dir_file_counts.get(d, 0)
        lines.append(f"├── {d}/  ({count} files)")
        if dp.is_dir():
            try:
                children = sorted(dp.iterdir())[:max_per_dir]
            except PermissionError:
                continue
            for child in children:
                if child.is_dir() and child.name not in _SKIP_DIRS_GLOBAL and not child.name.startswith("."):
                    lines.append(f"│   ├── {child.name}/")
                elif child.is_file() and child.suffix.lower() not in SKIP_EXTENSIONS:
                    size_kb = child.stat().st_size // 1024
                    lines.append(f"│   ├── {child.name} [{size_kb}KB]")

    return "\n".join(lines)


def _read_file_safe(path: Path, *, max_bytes: int = 6000) -> str:
    """Đọc file giới hạn kích thước."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content.encode()) > max_bytes:
            content = content[:max_bytes] + f"\n... [truncated at {max_bytes//1024}KB]"
        return content
    except Exception as e:
        return f"[error: {e}]"


def _module_summary_line(fp: Path, repo_root: Path) -> str:
    """1-2 dòng tóm tắt cho một file."""
    try:
        rel = fp.relative_to(repo_root).as_posix()
    except ValueError:
        rel = str(fp)

    size_kb = fp.stat().st_size // 1024

    entry = parse_file(fp)
    if entry is None:
        return f"  {rel} [{size_kb}KB]"

    parts = [f"  {rel} [{size_kb}KB] [{entry.language}]"]

    if entry.module_doc:
        parts.append(f"    # {entry.module_doc}")

    class_names = [c.name for c in entry.classes[:4]]
    if class_names:
        extra = f"+{len(entry.classes)-4}" if len(entry.classes) > 4 else ""
        parts.append(f"    # classes: {', '.join(class_names)}{extra}")

    fn_names = [f.name for f in entry.functions[:4]]
    if fn_names and not class_names:
        extra = f"+{len(entry.functions)-4}" if len(entry.functions) > 4 else ""
        parts.append(f"    # funcs: {', '.join(fn_names)}{extra}")

    return "\n".join(parts)


def _resolve_module_paths(modules: list[str], info: RepoInfo) -> list[Path]:
    """Giải quyết tên module → list Path thực.

    Dùng top-level dirs của repo thực tế (không hardcode).
    """
    root = info.root
    result: list[Path] = []

    for mod in modules:
        # Thử trực tiếp như path
        direct = root / mod
        if direct.exists():
            result.append(direct)
            continue

        # Tìm trong top dirs
        found = False
        for top_dir in info.top_dirs:
            candidate = root / top_dir / mod
            if candidate.exists():
                result.append(candidate)
                found = True
                break
            # Partial match (mod là prefix của top_dir)
            if top_dir.startswith(mod) or mod.startswith(top_dir):
                result.append(root / top_dir)
                found = True
                break

        if not found:
            # Glob search
            matches = list(root.glob(f"**/{mod}"))
            result.extend(m for m in matches[:3] if m.is_dir())

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def load_tier(
    level: int,
    info: RepoInfo,
    *,
    modules: list[str] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Load context ở một tầng cụ thể.

    Args:
        level: 0, 1, 2, 3.
        info: RepoInfo từ detect_repo().
        modules: Danh sách module/dir để filter (áp dụng T1+).
        verbose: In tiến trình.

    Returns:
        Dict chuẩn: content, tier, size_kb, files_included, next_tier_hint.
    """
    t0 = time.perf_counter()
    sections: list[str] = []
    files_included: list[str] = []

    if level == 0:
        sections.append("=" * 60)
        sections.append(f"TIER 0: {info.name} — File Tree + Core Files")
        sections.append("=" * 60)

        # File tree
        sections.append(_file_tree(info))
        files_included.append("[file tree]")

        # Notable root files
        for fname in info.notable_root_files:
            fp = info.root / fname
            if fp.exists():
                sections.append(f"\n{'─'*40}\n# {fname}\n{'─'*40}")
                sections.append(_read_file_safe(fp, max_bytes=4096))
                files_included.append(fname)

        hint = (
            f"Dùng tier=1 để xem module summaries. "
            f"Available top-dirs: {', '.join(info.top_dirs[:8])}. "
            f"Dùng --modules <dir1,dir2> để lọc."
        )

    elif level == 1:
        sections.append("=" * 60)
        sections.append(f"TIER 1: {info.name} — Module Summaries")
        sections.append("=" * 60)
        sections.append("(path [KB] [lang] | docstring | class/fn names)")

        # Resolve target dirs
        if modules:
            target_paths = _resolve_module_paths(modules, info)
        else:
            target_paths = [info.root / d for d in info.top_dirs if (info.root / d).exists()]

        py_files = collect_files(target_paths, info.root)

        for fp in py_files[:400]:
            summary = _module_summary_line(fp, info.root)
            sections.append(summary)
            try:
                files_included.append(str(fp.relative_to(info.root).as_posix()))
            except ValueError:
                files_included.append(str(fp))

        hint = (
            "Dùng tier=2 --modules <dir> để xem full signatures. "
            "Dùng tier=3 --modules <path/to/file.py> để đọc full 1 file."
        )

    elif level == 2:
        sections.append("=" * 60)
        sections.append(f"TIER 2: {info.name} — Function Signatures (Skeleton)")
        sections.append("=" * 60)

        if modules:
            scope_paths = _resolve_module_paths(modules, info)
        else:
            # Default: top 2 dirs (thường quan trọng nhất)
            scope_paths = [info.root / d for d in info.top_dirs[:2] if (info.root / d).exists()]

        if not scope_paths:
            scope_paths = [info.root]

        result = build_repo_map(info.root, scope=scope_paths, verbose=verbose, repo_info=info)
        stats = result.get("stats", {})
        sections.append(
            f"# {stats.get('total_files', 0)} files | "
            f"{stats.get('total_classes', 0)} classes | "
            f"{stats.get('total_functions', 0)} functions"
        )

        for entry in result.get("entries", []):
            sections.append(entry_to_text(entry, info.root))
            files_included.append(str(entry.path))

        hint = (
            "Dùng tier=3 --modules <path/to/file> để xem full nội dung của file cụ thể."
        )

    elif level == 3:
        sections.append("=" * 60)
        sections.append(f"TIER 3: {info.name} — Full File Content")
        sections.append("=" * 60)

        targets = modules or []
        for mod_or_path in targets:
            fp = info.root / mod_or_path
            if fp.is_file():
                sections.append(f"\n{'─'*50}\n# {mod_or_path}\n{'─'*50}")
                sections.append(_read_file_safe(fp, max_bytes=150_000))
                files_included.append(mod_or_path)
            elif fp.is_dir():
                # Nếu là dir: lấy tối đa 3 files
                py_files = collect_files([fp], info.root)
                for f in py_files[:3]:
                    rel = str(f.relative_to(info.root).as_posix())
                    sections.append(f"\n{'─'*50}\n# {rel}\n{'─'*50}")
                    sections.append(_read_file_safe(f, max_bytes=80_000))
                    files_included.append(rel)
            else:
                # Tìm trong toàn repo
                matches = list(info.root.glob(f"**/{mod_or_path}"))
                for m in matches[:2]:
                    rel = str(m.relative_to(info.root).as_posix())
                    sections.append(f"\n{'─'*50}\n# {rel}\n{'─'*50}")
                    sections.append(_read_file_safe(m, max_bytes=80_000))
                    files_included.append(rel)

        hint = "Bạn đang ở tầng cao nhất. Edit trực tiếp trong editor."

    else:
        return {"tier": level, "error": f"Invalid level {level}. Valid: 0-3."}

    content = "\n".join(sections)
    elapsed = time.perf_counter() - t0

    return {
        "tier": level,
        "repo": info.name,
        "modules": modules or [],
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
        "size_kb": round(len(content.encode("utf-8")) / 1024, 1),
        "elapsed_seconds": round(elapsed, 3),
        "files_included": files_included,
        "next_tier_hint": hint,
    }


def print_tier_result(result: dict[str, Any]) -> None:
    """In kết quả tier ra stdout."""
    if "error" in result:
        print(f"[ERROR] {result['error']}", file=sys.stderr)
        return
    print(result.get("content", ""))
    print(f"\n{'─'*60}", file=sys.stderr)
    print(
        f"[Tier {result['tier']}] repo={result.get('repo','?')} "
        f"{result.get('size_kb', 0)}KB | "
        f"{len(result.get('files_included', []))} files | "
        f"{result.get('elapsed_seconds', 0)}s",
        file=sys.stderr,
    )
    print(f"[Hint] {result.get('next_tier_hint', '')}", file=sys.stderr)
