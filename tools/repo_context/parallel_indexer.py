# -*- coding: utf-8 -*-
"""Cách 3: Parallel Indexer — Dynamic partitioning, không hardcode.

Lấy partition plan từ RepoInfo.partitions (auto-generated từ top-level dirs).
Mỗi worker = 1 subprocess thực sự (ProcessPoolExecutor).

Dùng được cho bất kỳ repo nào.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout, as_completed
from pathlib import Path
from typing import Any

from .repo_detector import RepoInfo


# ─────────────────────────────────────────────────────────────────────────────
# Worker function (chạy trong subprocess)
# ─────────────────────────────────────────────────────────────────────────────

def _worker_index(
    worker_name: str,
    repo_root_str: str,
    paths: list[str],
    exclude_subdirs: list[str],
    tools_parent_str: str,
) -> dict[str, Any]:
    """Chạy trong subprocess worker — parse một phần repo."""
    import time as _time
    import sys as _sys

    t0 = _time.perf_counter()
    repo_root = Path(repo_root_str)

    # Add tools dir và repo root vào sys.path để import được
    for p in [tools_parent_str, repo_root_str]:
        if p not in _sys.path:
            _sys.path.insert(0, p)

    try:
        from tools.repo_context.repo_map import collect_files
        from tools.repo_context.lang_parsers import parse_file, SKIP_EXTENSIONS, SKIP_FILENAMES
        from tools.repo_context.repo_detector import _SKIP_DIRS_GLOBAL
    except ImportError:
        # Fallback path nếu chạy standalone
        rc_dir = Path(tools_parent_str) / "tools" / "repo_context"
        _sys.path.insert(0, str(rc_dir.parent.parent))
        from tools.repo_context.repo_map import collect_files
        from tools.repo_context.lang_parsers import parse_file, SKIP_EXTENSIONS, SKIP_FILENAMES
        from tools.repo_context.repo_detector import _SKIP_DIRS_GLOBAL

    skip_dirs = _SKIP_DIRS_GLOBAL | set(exclude_subdirs)

    scope: list[Path] = []
    for p in paths:
        full = repo_root / p
        if full.exists():
            scope.append(full)

    files = collect_files(scope, repo_root, skip_dirs=skip_dirs)

    entries_raw: list[dict[str, Any]] = []
    errors = 0
    lang_counts: dict[str, int] = {}

    for fp in files:
        entry = parse_file(fp)
        if entry is None:
            continue
        if entry.parse_error:
            errors += 1

        try:
            rel = str(fp.relative_to(repo_root).as_posix())
        except ValueError:
            rel = str(fp)

        entries_raw.append({
            "path": str(fp),
            "rel_path": rel,
            "size_bytes": entry.size_bytes,
            "language": entry.language,
            "module_doc": entry.module_doc,
            "classes": [
                {
                    "name": c.name,
                    "bases": c.bases,
                    "doc": c.doc,
                    "kind": c.kind,
                    "methods": [
                        {"name": m.name, "args": m.args, "returns": m.returns,
                         "is_async": m.is_async, "access": m.access}
                        for m in c.methods
                    ],
                }
                for c in entry.classes
            ],
            "functions": [
                {"name": f.name, "args": f.args, "returns": f.returns, "is_async": f.is_async}
                for f in entry.functions
            ],
            "parse_error": entry.parse_error,
        })
        lang_counts[entry.language] = lang_counts.get(entry.language, 0) + 1

    elapsed = _time.perf_counter() - t0

    return {
        "worker": worker_name,
        "entries_raw": entries_raw,
        "stats": {
            "files": len(entries_raw),
            "errors": errors,
            "elapsed_seconds": round(elapsed, 2),
            "languages": lang_counts,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run_parallel_index(
    repo_root: Path,
    *,
    repo_info: RepoInfo | None = None,
    partitions: list[dict[str, Any]] | None = None,
    max_workers: int | None = None,
    worker_timeout: float = 90.0,
    verbose: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Index repo dùng multiple subprocess workers song song.

    Args:
        repo_root: Gốc repo.
        repo_info: RepoInfo đã detect. None = auto-detect.
        partitions: Override partition plan. None = dùng từ repo_info.
        max_workers: Số workers tối đa.
        worker_timeout: Timeout per worker.
        verbose: In tiến trình.
        dry_run: Chỉ in plan, không index.

    Returns:
        Dict với entries (raw dicts), worker_stats, stats, generated_at.
    """
    # Get repo info
    if repo_info is None:
        from .repo_detector import detect_repo
        repo_info = detect_repo(repo_root=repo_root, verbose=verbose)

    # Get partitions
    if partitions is None:
        partitions = repo_info.partitions

    if not partitions:
        # Fallback: single partition covering whole repo
        partitions = [{"name": "all", "paths": ["."], "exclude_subdirs": []}]

    n_workers = max_workers or min(len(partitions), max(1, os.cpu_count() or 4))

    # tools parent dir (để subprocess import được)
    tools_dir = Path(__file__).parent.parent.parent  # repo root
    tools_parent = str(tools_dir)

    if verbose or dry_run:
        print(
            f"[parallel_indexer] Repo: {repo_info.name} | "
            f"{len(partitions)} partitions | {n_workers} workers",
            file=sys.stderr,
        )
        for p in partitions:
            print(f"  [{p['name']}] → {', '.join(p.get('paths', []))}", file=sys.stderr)

    if dry_run:
        return {
            "dry_run": True,
            "repo": repo_info.name,
            "partitions": partitions,
            "planned_workers": n_workers,
        }

    t0 = time.perf_counter()
    all_entries_raw: list[dict[str, Any]] = []
    worker_stats: list[dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        future_map: dict[Any, str] = {}

        for p in partitions:
            future = executor.submit(
                _worker_index,
                p["name"],
                str(repo_root),
                p.get("paths", []),
                p.get("exclude_subdirs", []),
                tools_parent,
            )
            future_map[future] = p["name"]

        for future in as_completed(future_map, timeout=worker_timeout * len(partitions)):
            wname = future_map[future]
            try:
                res = future.result(timeout=worker_timeout)
                all_entries_raw.extend(res.get("entries_raw", []))
                worker_stats.append({"worker": wname, **res.get("stats", {})})

                if verbose:
                    s = res.get("stats", {})
                    print(
                        f"[parallel_indexer] ✓ {wname}: "
                        f"{s.get('files', 0)} files in {s.get('elapsed_seconds', 0)}s",
                        file=sys.stderr,
                    )

            except FutureTimeout:
                worker_stats.append({"worker": wname, "error": "timeout", "files": 0})
                if verbose:
                    print(f"[parallel_indexer] ✗ {wname}: TIMEOUT", file=sys.stderr)

            except Exception as exc:
                worker_stats.append({"worker": wname, "error": str(exc), "files": 0})
                if verbose:
                    print(f"[parallel_indexer] ✗ {wname}: {exc}", file=sys.stderr)

    # Deduplicate by rel_path
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for e in all_entries_raw:
        key = e.get("rel_path", e.get("path", ""))
        if key not in seen:
            seen.add(key)
            unique.append(e)
    unique.sort(key=lambda e: e.get("rel_path", ""))

    total_elapsed = time.perf_counter() - t0
    total_files = sum(ws.get("files", 0) for ws in worker_stats if "error" not in ws)

    # Merge language counts
    merged_langs: dict[str, int] = {}
    for ws in worker_stats:
        for lang, cnt in ws.get("languages", {}).items():
            merged_langs[lang] = merged_langs.get(lang, 0) + cnt

    stats = {
        "total_files": len(unique),
        "total_errors": sum(ws.get("errors", 0) for ws in worker_stats if "error" not in ws),
        "total_elapsed_seconds": round(total_elapsed, 2),
        "workers_succeeded": sum(1 for ws in worker_stats if "error" not in ws),
        "workers_failed": sum(1 for ws in worker_stats if "error" in ws),
        "languages": dict(sorted(merged_langs.items(), key=lambda x: -x[1])),
    }

    if verbose:
        print(
            f"[parallel_indexer] Done: {len(unique)} files in {total_elapsed:.2f}s",
            file=sys.stderr,
        )

    return {
        "entries": unique,       # raw dicts (không phải FileEntry objects)
        "worker_stats": worker_stats,
        "stats": stats,
        "generated_at": time.time(),
    }


def print_parallel_stats(result: dict[str, Any]) -> None:
    """In thống kê ra stdout."""
    if result.get("dry_run"):
        print(f"[DRY RUN] Repo: {result.get('repo', '?')}")
        print(f"  Planned partitions: {len(result.get('partitions', []))}")
        for p in result.get("partitions", []):
            print(f"  - {p['name']}: {', '.join(p.get('paths', []))}")
        print(f"  Workers: {result.get('planned_workers', 0)}")
        return

    stats = result.get("stats", {})
    print(f"\n{'='*60}")
    print("PARALLEL INDEX RESULT")
    print(f"{'='*60}")
    print(f"Total files   : {stats.get('total_files', 0)}")
    print(f"Total time    : {stats.get('total_elapsed_seconds', 0)}s")
    print(f"Workers OK    : {stats.get('workers_succeeded', 0)}")
    print(f"Workers FAIL  : {stats.get('workers_failed', 0)}")
    if stats.get("languages"):
        lang_str = " | ".join(f"{l}:{c}" for l, c in stats["languages"].items())
        print(f"Languages     : {lang_str}")
    print("\nPer-worker:")
    for ws in result.get("worker_stats", []):
        if "error" in ws:
            print(f"  ✗ {ws['worker']}: {ws['error']}")
        else:
            print(
                f"  ✓ {ws['worker']}: "
                f"{ws.get('files', 0)} files in {ws.get('elapsed_seconds', 0)}s"
            )
