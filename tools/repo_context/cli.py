# -*- coding: utf-8 -*-
"""CLI Entry Point — Repo Context Compression Tool.

Tự detect repo từ cwd (hoặc --repo để chỉ định explicit).
Dùng được cho bất kỳ repo nào.

Usage:
    # Từ bất kỳ thư mục nào trong repo:
    python tools/repo_context/cli.py map
    python tools/repo_context/cli.py tier --level 1 --modules scripts,scenes
    python tools/repo_context/cli.py index --workers 4
    python tools/repo_context/cli.py cache --build
    python tools/repo_context/cli.py cache --query "dialog textbox"
    python tools/repo_context/cli.py cache --list-repos

    # Chỉ định repo explicit:
    python tools/repo_context/cli.py --repo C:\\path\\to\\repo map
    python tools/repo_context/cli.py --repo C:\\path\\to\\ashes cache --build
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Ensure package importable regardless of cwd
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_importable() -> None:
    """Thêm repo root của tools package vào sys.path."""
    this_file = Path(__file__).resolve()
    # tools/repo_context/cli.py -> go up 3 levels to repo root
    repo_tools_parent = this_file.parent.parent.parent
    if str(repo_tools_parent) not in sys.path:
        sys.path.insert(0, str(repo_tools_parent))

    # Force UTF-8 stdout/stderr on Windows (avoids charmap errors)
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ensure_importable()

from tools.repo_context.repo_detector import detect_repo, RepoInfo


# ─────────────────────────────────────────────────────────────────────────────
# Command implementations
# ─────────────────────────────────────────────────────────────────────────────

def cmd_map(args: argparse.Namespace, info: RepoInfo) -> int:
    """Sinh repo map (text + JSON skeleton)."""
    from tools.repo_context.repo_map import build_repo_map, save_repo_map

    scope = None
    if args.scope:
        scope_paths = []
        for s in args.scope.split(","):
            fp = info.root / s.strip()
            if fp.exists():
                scope_paths.append(fp)
            else:
                print(f"[map] WARNING: scope path not found: {fp}", file=sys.stderr)
        scope = scope_paths if scope_paths else None

    print(f"[map] Building repo map for '{info.name}'...", file=sys.stderr)
    result = build_repo_map(info.root, scope=scope, verbose=args.verbose, repo_info=info)

    # Output dir
    output_dir = Path.home() / ".gitpilot" / "repo_cache" / info.repo_id
    text_path, json_path = save_repo_map(result, info.root, output_dir)

    stats = result.get("stats", {})
    print(f"\n[map] ✓ Done: {stats.get('total_files', 0)} files, "
          f"{stats.get('total_classes', 0)} classes, "
          f"{stats.get('total_functions', 0)} functions, "
          f"{stats.get('elapsed_seconds', 0)}s", file=sys.stderr)
    print(f"[map] Text: {text_path}", file=sys.stderr)
    print(f"[map] JSON: {json_path}", file=sys.stderr)

    if args.print:
        from tools.repo_context.repo_map import repo_map_to_text
        print(repo_map_to_text(result, info.root))

    return 0


def cmd_tier(args: argparse.Namespace, info: RepoInfo) -> int:
    """Load context ở tầng cụ thể."""
    from tools.repo_context.tiered_loader import load_tier, print_tier_result

    level = args.level
    modules = [m.strip() for m in args.modules.split(",")] if args.modules else None

    result = load_tier(level, info, modules=modules, verbose=args.verbose)
    print_tier_result(result)
    return 0


def cmd_index(args: argparse.Namespace, info: RepoInfo) -> int:
    """Parallel index repo."""
    from tools.repo_context.parallel_indexer import run_parallel_index, print_parallel_stats

    result = run_parallel_index(
        info.root,
        repo_info=info,
        max_workers=args.workers,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )
    print_parallel_stats(result)

    if not args.dry_run:
        print(f"\n[index] ✓ {result['stats']['total_files']} files indexed in "
              f"{result['stats']['total_elapsed_seconds']}s", file=sys.stderr)
    return 0


def cmd_cache(args: argparse.Namespace, info: RepoInfo | None) -> int:
    """Build, query, status, hoặc list repos cache."""
    from tools.repo_context.knowledge_cache import (
        build_cache,
        load_cache,
        check_cache_health,
        list_all_repos,
        format_repo_list,
        query_cache,
        format_query_results,
        get_cache_path,
    )

    # --list-repos: không cần info
    if args.list_repos:
        repos = list_all_repos()
        print(format_repo_list(repos))
        return 0

    if info is None:
        print("[cache] ERROR: Cannot detect repo. Use --repo <path>.", file=sys.stderr)
        return 1

    if args.build:
        data = build_cache(
            info,
            use_parallel=not args.sequential,
            max_workers=args.workers,
            verbose=True,
            force_rebuild=args.force,
        )
        stats = data.get("stats", {})
        print(f"\n[cache] ✓ Built cache for '{info.name}':", file=sys.stderr)
        print(f"  Files: {stats.get('total_files', 0)}", file=sys.stderr)
        print(f"  Classes: {stats.get('total_classes', 0)}", file=sys.stderr)
        print(f"  Functions: {stats.get('total_functions', 0)}", file=sys.stderr)
        print(f"  Time: {stats.get('build_time_seconds', 0)}s", file=sys.stderr)
        print(f"  Size: {stats.get('cache_size_kb', 0)}KB", file=sys.stderr)

        # Remove any pending lock left by a gitpilot background rebuild trigger
        from tools.repo_context.knowledge_cache import get_cache_path
        lock_file = get_cache_path(info).parent / ".rebuild.lock"
        if lock_file.exists():
            lock_file.unlink(missing_ok=True)
        return 0


    if args.query:
        data = load_cache(info, auto_rebuild=False, verbose=args.verbose)
        if data is None:
            print(f"[cache] No cache found for '{info.name}'. Run: cli.py cache --build", file=sys.stderr)
            return 1
        modules = [m.strip() for m in args.modules.split(",")] if args.modules else None
        results = query_cache(data, args.query, top_n=args.top, module_filter=modules)
        print(format_query_results(results, query=args.query))
        return 0

    if args.status:
        health = check_cache_health(info)
        print(f"\n{'='*50}")
        print(f"Cache Status: {info.name}")
        print(f"{'='*50}")
        for k, v in health.items():
            print(f"  {k:<22}: {v}")
        return 0

    # Default: show status
    health = check_cache_health(info)
    print(f"[cache] '{info.name}': {health['status']} "
          f"({health.get('total_files', 0)} files, "
          f"stale={health.get('stale_files', 0)})")
    cache_p = get_cache_path(info)
    print(f"[cache] Path: {cache_p}")
    return 0


def cmd_info(args: argparse.Namespace, info: RepoInfo) -> int:
    """Hiển thị thông tin repo đã detect."""
    print(f"\n{'='*60}")
    print(f"REPO INFO: {info.name}")
    print(f"{'='*60}")
    print(f"Root    : {info.root}")
    print(f"Repo ID : {info.repo_id}")
    print(f"Files   : {info.total_files}")
    print(f"Dominant: {info.dominant_lang}")

    lang_str = " | ".join(f"{l}:{c}" for l, c in list(info.languages.items())[:8])
    print(f"Langs   : {lang_str}")

    print(f"\nTop directories:")
    for d in info.top_dirs[:10]:
        count = info.dir_file_counts.get(d, 0)
        print(f"  {d:<30} ({count} files)")

    print(f"\nAuto-generated partitions ({len(info.partitions)}):")
    for p in info.partitions:
        print(f"  [{p['name']}] -> {', '.join(p.get('paths', []))}")

    print(f"\nNotable root files: {', '.join(info.notable_root_files)}")

    from tools.repo_context.knowledge_cache import get_cache_path
    cp = get_cache_path(info)
    print(f"\nCache path: {cp}")
    print(f"Cache exists: {cp.exists()}")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo_context",
        description="Repo Context Compression — Hiểu bất kỳ repo nào nhanh chóng.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect repo từ cwd:
  python tools/repo_context/cli.py info
  python tools/repo_context/cli.py map
  python tools/repo_context/cli.py tier --level 0
  python tools/repo_context/cli.py cache --build
  python tools/repo_context/cli.py cache --query "dialog textbox" --top 5

  # Chỉ định repo explicit:
  python tools/repo_context/cli.py --repo C:\\path\\to\\ashes map
  python tools/repo_context/cli.py --repo C:\\path\\to\\eris cache --build

  # Multi-repo listing:
  python tools/repo_context/cli.py cache --list-repos
        """,
    )

    # Global options
    parser.add_argument(
        "--repo", "-r",
        metavar="PATH",
        help="Đường dẫn tới repo (mặc định: auto-detect từ cwd)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="In tiến trình chi tiết",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── info ──
    sub_info = subparsers.add_parser("info", help="Hiển thị thông tin repo đã detect")

    # ── map ──
    sub_map = subparsers.add_parser("map", help="Sinh repo map (AST skeleton)")
    sub_map.add_argument(
        "--scope", "-s",
        metavar="PATHS",
        help="Giới hạn scan (comma-separated relative paths). Mặc định: toàn repo",
    )
    sub_map.add_argument(
        "--print",
        action="store_true",
        help="In kết quả ra stdout (mặc định chỉ lưu file)",
    )

    # ── tier ──
    sub_tier = subparsers.add_parser("tier", help="Load context theo tầng T0-T3")
    sub_tier.add_argument(
        "--level", "-l",
        type=int,
        default=0,
        choices=[0, 1, 2, 3],
        help="Tầng context (0=tree, 1=summary, 2=skeleton, 3=full). Mặc định: 0",
    )
    sub_tier.add_argument(
        "--modules", "-m",
        metavar="NAMES",
        help="Comma-separated module/dir names để filter",
    )

    # ── index ──
    sub_index = subparsers.add_parser("index", help="Parallel index toàn repo")
    sub_index.add_argument(
        "--workers", "-w",
        type=int,
        default=None,
        help="Số worker subprocess (mặc định: auto)",
    )
    sub_index.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ hiện plan, không index",
    )

    # ── cache ──
    sub_cache = subparsers.add_parser("cache", help="Quản lý knowledge cache")
    cache_grp = sub_cache.add_mutually_exclusive_group()
    cache_grp.add_argument("--build", action="store_true", help="Build/update cache")
    cache_grp.add_argument("--query", "-q", metavar="QUERY", help="Query cache với keywords")
    cache_grp.add_argument("--status", action="store_true", help="Kiểm tra trạng thái cache")
    cache_grp.add_argument("--list-repos", action="store_true", help="Liệt kê tất cả repos đã cache")

    sub_cache.add_argument("--force", "-f", action="store_true", help="Force rebuild cache")
    sub_cache.add_argument(
        "--sequential",
        action="store_true",
        help="Dùng sequential indexer thay vì parallel",
    )
    sub_cache.add_argument(
        "--workers", "-w",
        type=int,
        default=None,
        help="Số worker cho parallel build",
    )
    sub_cache.add_argument(
        "--modules", "-m",
        metavar="NAMES",
        help="Filter query theo module/dir",
    )
    sub_cache.add_argument(
        "--top",
        type=int,
        default=10,
        help="Số kết quả tối đa khi query (mặc định: 10)",
    )

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Detect repo (không cần cho cache --list-repos)
    info: RepoInfo | None = None
    if args.command != "cache" or not getattr(args, "list_repos", False):
        try:
            repo_path = Path(args.repo).resolve() if args.repo else None
            info = detect_repo(
                repo_root=repo_path,
                verbose=args.verbose,
            )
            if args.verbose:
                print(f"[cli] Detected: {info.name} @ {info.root}", file=sys.stderr)
        except ValueError as e:
            if args.command == "cache" and getattr(args, "list_repos", False):
                pass  # OK — list-repos không cần repo
            else:
                print(f"[cli] ERROR: {e}", file=sys.stderr)
                print("[cli] Tip: cd vào thư mục repo, hoặc dùng --repo <path>", file=sys.stderr)
                return 1

    # Dispatch
    try:
        if args.command == "info":
            return cmd_info(args, info)
        elif args.command == "map":
            return cmd_map(args, info)
        elif args.command == "tier":
            return cmd_tier(args, info)
        elif args.command == "index":
            return cmd_index(args, info)
        elif args.command == "cache":
            return cmd_cache(args, info)
        else:
            parser.print_help()
            return 0
    except KeyboardInterrupt:
        print("\n[cli] Interrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"[cli] FATAL ERROR: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
