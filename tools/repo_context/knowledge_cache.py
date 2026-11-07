# -*- coding: utf-8 -*-
"""Cách 4: Knowledge Cache — Multi-repo, lưu tại ~/.gitpilot/repo_cache/.

Mỗi repo có cache riêng, nhận diện bằng repo_id duy nhất.
Không cần write quyền vào repo target.

Cache tự invalidate khi > STALE_THRESHOLD files thay đổi (mtime/size).

Cache location: ~/.gitpilot/repo_cache/<repo_id>/cache.json
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .repo_detector import RepoInfo

CACHE_SCHEMA_VERSION = 3
STALE_THRESHOLD = 0.10  # Rebuild nếu > 10% files stale


# ─────────────────────────────────────────────────────────────────────────────
# Cache path helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_cache_root() -> Path:
    """Thư mục gốc lưu cache cho tất cả repos."""
    return Path.home() / ".gitpilot" / "repo_cache"


def get_cache_dir(repo_info: RepoInfo) -> Path:
    """Thư mục cache của một repo cụ thể."""
    return get_cache_root() / repo_info.repo_id


def get_cache_path(repo_info: RepoInfo) -> Path:
    return get_cache_dir(repo_info) / "cache.json"


# ─────────────────────────────────────────────────────────────────────────────
# Build cache record per file
# ─────────────────────────────────────────────────────────────────────────────

def _entry_to_cache_record(entry: dict[str, Any], repo_root: Path) -> tuple[str, dict[str, Any]] | None:
    """Chuyển một raw entry (từ parallel_indexer) → (rel_path, cache_record)."""
    abs_path = entry.get("path", "")
    if not abs_path:
        abs_path = entry.get("rel_path", "")
        if not abs_path:
            return None

    fp = Path(abs_path)
    try:
        rel = str(fp.relative_to(repo_root).as_posix())
    except ValueError:
        rel = entry.get("rel_path", str(fp))

    try:
        stat = fp.stat()
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        return None

    # Build compact skeleton text
    skel_lines: list[str] = []
    for cls in entry.get("classes", [])[:10]:
        bases = f"({', '.join(cls['bases'])})" if cls.get("bases") else ""
        kind = cls.get("kind", "class")
        prefix = "" if kind == "class" else f"{kind} "
        skel_lines.append(f"{prefix}class {cls['name']}{bases}:")
        for m in cls.get("methods", [])[:8]:
            ret = f" -> {m['returns']}" if m.get("returns") else ""
            skel_lines.append(f"  def {m['name']}({m.get('args', '')[:60]}){ret}")
    for fn in entry.get("functions", [])[:10]:
        ret = f" -> {fn['returns']}" if fn.get("returns") else ""
        skel_lines.append(f"def {fn['name']}({fn.get('args', '')[:60]}){ret}")

    return rel, {
        "mtime": mtime,
        "size": size,
        "language": entry.get("language", "unknown"),
        "module_doc": entry.get("module_doc", ""),
        "skeleton": "\n".join(skel_lines),
        "classes": [c["name"] for c in entry.get("classes", [])],
        "functions": [f["name"] for f in entry.get("functions", [])],
        "parse_error": entry.get("parse_error", False),
    }


def _build_module_index(files: dict[str, Any]) -> dict[str, list[str]]:
    """Build {module_name: [rel_paths]} index."""
    index: dict[str, list[str]] = {}
    for rel in files:
        parts = rel.split("/")
        # Module = top-level dir (hoặc "root" nếu là file trực tiếp)
        module = parts[0] if len(parts) > 1 else "root"
        index.setdefault(module, []).append(rel)
    return {k: sorted(v) for k, v in sorted(index.items())}


# ─────────────────────────────────────────────────────────────────────────────
# Build + save cache
# ─────────────────────────────────────────────────────────────────────────────

def build_cache(
    repo_info: RepoInfo,
    *,
    use_parallel: bool = True,
    max_workers: int | None = None,
    verbose: bool = False,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    """Build và lưu cache cho một repo.

    Args:
        repo_info: RepoInfo từ detect_repo().
        use_parallel: Dùng parallel_indexer (nhanh hơn).
        max_workers: Số workers.
        verbose: In tiến trình.
        force_rebuild: Ignore cache cũ, build lại toàn bộ.

    Returns:
        Cache dict đầy đủ.
    """
    import sys

    cache_path = get_cache_path(repo_info)

    # Load existing entries để incremental update
    existing_files: dict[str, Any] = {}
    if not force_rebuild and cache_path.exists():
        try:
            old = json.loads(cache_path.read_text(encoding="utf-8"))
            if old.get("schema_version") == CACHE_SCHEMA_VERSION:
                existing_files = old.get("files", {})
                if verbose:
                    print(
                        f"[cache] Loaded existing cache: {len(existing_files)} entries",
                        file=sys.stderr,
                    )
        except Exception:
            pass

    if verbose:
        print(
            f"[cache] Building cache for '{repo_info.name}' "
            f"(parallel={use_parallel})...",
            file=sys.stderr,
        )

    t0 = time.perf_counter()

    # Index
    if use_parallel:
        from .parallel_indexer import run_parallel_index
        index_result = run_parallel_index(
            repo_info.root,
            repo_info=repo_info,
            max_workers=max_workers,
            verbose=verbose,
        )
        raw_entries = index_result.get("entries", [])
    else:
        from .repo_map import build_repo_map
        map_result = build_repo_map(repo_info.root, verbose=verbose, repo_info=repo_info)
        # Convert FileEntry objects to raw dicts for compatibility
        raw_entries = []
        for entry in map_result.get("entries", []):
            if hasattr(entry, "__dict__"):
                raw_entries.append({
                    "path": str(entry.path),
                    "size_bytes": entry.size_bytes,
                    "language": entry.language,
                    "module_doc": entry.module_doc,
                    "classes": [
                        {"name": c.name, "bases": c.bases, "doc": c.doc, "kind": c.kind,
                         "methods": [{"name": m.name, "args": m.args, "returns": m.returns,
                                      "is_async": m.is_async, "access": m.access}
                                     for m in c.methods]}
                        for c in entry.classes
                    ],
                    "functions": [
                        {"name": f.name, "args": f.args, "returns": f.returns}
                        for f in entry.functions
                    ],
                    "parse_error": entry.parse_error,
                })
            else:
                raw_entries.append(entry)

    # Build cache records
    files_cache: dict[str, Any] = {}
    for raw in raw_entries:
        result = _entry_to_cache_record(raw, repo_info.root)
        if result is None:
            continue
        rel, record = result
        files_cache[rel] = record

    # Keep entries from existing cache that weren't re-indexed (không bị xóa)
    for rel, old_rec in existing_files.items():
        if rel not in files_cache:
            fp = repo_info.root / rel
            if fp.exists():
                files_cache[rel] = old_rec

    elapsed = time.perf_counter() - t0

    module_index = _build_module_index(files_cache)
    total_classes = sum(len(r.get("classes", [])) for r in files_cache.values())
    total_functions = sum(len(r.get("functions", [])) for r in files_cache.values())

    # Language summary
    lang_counts: dict[str, int] = {}
    for r in files_cache.values():
        lang = r.get("language", "unknown")
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

    cache_data: dict[str, Any] = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "generated_at": time.time(),
        "repo_name": repo_info.name,
        "repo_id": repo_info.repo_id,
        "repo_root": str(repo_info.root),
        "dominant_lang": repo_info.dominant_lang,
        "files": files_cache,
        "module_index": module_index,
        "stats": {
            "total_files": len(files_cache),
            "total_classes": total_classes,
            "total_functions": total_functions,
            "languages": dict(sorted(lang_counts.items(), key=lambda x: -x[1])),
            "build_time_seconds": round(elapsed, 2),
            "cache_size_kb": 0,
        },
    }

    # Lưu
    cache_dir = get_cache_dir(repo_info)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_json = json.dumps(cache_data, ensure_ascii=False, separators=(",", ":"))
    cache_path.write_text(cache_json, encoding="utf-8")
    cache_size_kb = len(cache_json.encode()) // 1024
    cache_data["stats"]["cache_size_kb"] = cache_size_kb

    if verbose:
        lang_str = " | ".join(f"{l}:{c}" for l, c in cache_data["stats"]["languages"].items())
        print(
            f"[cache] ✓ {repo_info.name}: "
            f"{len(files_cache)} files, {total_classes} classes, "
            f"{total_functions} functions in {elapsed:.2f}s → {cache_size_kb}KB",
            file=sys.stderr,
        )
        print(f"[cache] Saved: {cache_path}", file=sys.stderr)

    return cache_data


# ─────────────────────────────────────────────────────────────────────────────
# Load + validate
# ─────────────────────────────────────────────────────────────────────────────

def _count_stale(files: dict[str, Any], repo_root: Path) -> int:
    stale = 0
    for rel, rec in files.items():
        fp = repo_root / rel
        try:
            stat = fp.stat()
            if abs(stat.st_mtime - rec.get("mtime", 0)) > 1.0 or stat.st_size != rec.get("size", -1):
                stale += 1
        except OSError:
            stale += 1
    return stale


def load_cache(
    repo_info: RepoInfo,
    *,
    auto_rebuild: bool = True,
    verbose: bool = False,
) -> dict[str, Any] | None:
    """Load cache. Auto-rebuild nếu stale.

    Returns:
        Cache dict hoặc None.
    """
    import sys

    cache_path = get_cache_path(repo_info)
    if not cache_path.exists():
        if verbose:
            print(f"[cache] No cache for '{repo_info.name}'.", file=sys.stderr)
        return None

    t0 = time.perf_counter()
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as e:
        if verbose:
            print(f"[cache] Load error: {e}", file=sys.stderr)
        return None

    if data.get("schema_version") != CACHE_SCHEMA_VERSION:
        if verbose:
            print(f"[cache] Schema mismatch for '{repo_info.name}', rebuilding.", file=sys.stderr)
        if auto_rebuild:
            return build_cache(repo_info, verbose=verbose)
        return None

    elapsed = time.perf_counter() - t0
    if verbose:
        stats = data.get("stats", {})
        print(
            f"[cache] Loaded '{repo_info.name}': "
            f"{stats.get('total_files', 0)} files in {elapsed:.3f}s "
            f"({stats.get('cache_size_kb', 0)}KB)",
            file=sys.stderr,
        )

    # Staleness check
    if auto_rebuild:
        files = data.get("files", {})
        stale = _count_stale(files, repo_info.root)
        ratio = stale / max(len(files), 1)
        if ratio > STALE_THRESHOLD:
            if verbose:
                print(
                    f"[cache] {stale}/{len(files)} files stale ({ratio:.0%}), rebuilding...",
                    file=sys.stderr,
                )
            return build_cache(repo_info, verbose=verbose)

    return data


def check_cache_health(repo_info: RepoInfo) -> dict[str, Any]:
    """Kiểm tra sức khỏe cache mà không rebuild."""
    cache_path = get_cache_path(repo_info)

    if not cache_path.exists():
        return {
            "repo": repo_info.name,
            "status": "missing",
            "cache_path": str(cache_path),
        }

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"repo": repo_info.name, "status": "corrupt", "error": str(e)}

    if data.get("schema_version") != CACHE_SCHEMA_VERSION:
        return {
            "repo": repo_info.name,
            "status": "schema_mismatch",
            "found": data.get("schema_version"),
            "expected": CACHE_SCHEMA_VERSION,
        }

    files = data.get("files", {})
    stale = _count_stale(files, repo_info.root)
    ratio = stale / max(len(files), 1)
    stats = data.get("stats", {})

    age_hours = (time.time() - data.get("generated_at", 0)) / 3600

    return {
        "repo": repo_info.name,
        "repo_id": repo_info.repo_id,
        "status": "ok" if ratio <= STALE_THRESHOLD else "stale",
        "total_files": len(files),
        "stale_files": stale,
        "stale_ratio": round(ratio, 3),
        "age_hours": round(age_hours, 1),
        "build_time_seconds": stats.get("build_time_seconds"),
        "cache_size_kb": stats.get("cache_size_kb"),
        "dominant_lang": data.get("dominant_lang"),
        "languages": stats.get("languages", {}),
        "cache_path": str(cache_path),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Multi-repo listing
# ─────────────────────────────────────────────────────────────────────────────

def list_all_repos() -> list[dict[str, Any]]:
    """Liệt kê tất cả repos đã cache trong ~/.gitpilot/repo_cache/."""
    cache_root = get_cache_root()
    if not cache_root.exists():
        return []

    repos = []
    for repo_dir in sorted(cache_root.iterdir()):
        if not repo_dir.is_dir():
            continue
        cache_file = repo_dir / "cache.json"
        if not cache_file.exists():
            continue
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        stats = data.get("stats", {})
        age_hours = (time.time() - data.get("generated_at", 0)) / 3600
        stale = 0
        repo_root_path = Path(data.get("repo_root", ""))
        files = data.get("files", {})
        if repo_root_path.exists():
            stale = _count_stale(files, repo_root_path)
        ratio = stale / max(len(files), 1)

        repos.append({
            "repo_id": repo_dir.name,
            "repo_name": data.get("repo_name", "?"),
            "repo_root": data.get("repo_root", ""),
            "total_files": stats.get("total_files", 0),
            "dominant_lang": data.get("dominant_lang", "?"),
            "languages": stats.get("languages", {}),
            "cache_size_kb": stats.get("cache_size_kb", 0),
            "age_hours": round(age_hours, 1),
            "stale_ratio": round(ratio, 3),
            "status": "ok" if ratio <= STALE_THRESHOLD else "stale",
        })

    return repos


def format_repo_list(repos: list[dict[str, Any]]) -> str:
    """Format danh sách repos thành text đẹp."""
    if not repos:
        return "[cache] No repos cached yet. Run: cli.py cache --build"

    lines = [
        f"{'='*70}",
        f"CACHED REPOS ({len(repos)}) — ~/.gitpilot/repo_cache/",
        f"{'='*70}",
        f"{'Repo':<30} {'Files':>6} {'Lang':<10} {'Size':>7} {'Age':>8} {'Status'}",
        f"{'─'*70}",
    ]
    for r in repos:
        lang_str = r.get("dominant_lang", "?")
        age_str = f"{r['age_hours']:.1f}h"
        status = "✓ OK" if r["status"] == "ok" else f"⚠ STALE {r['stale_ratio']:.0%}"
        name = r["repo_name"][:29]
        lines.append(
            f"{name:<30} {r['total_files']:>6} {lang_str:<10} "
            f"{r['cache_size_kb']:>5}KB {age_str:>7} {status}"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Query
# ─────────────────────────────────────────────────────────────────────────────

def _score_file(rel: str, rec: dict[str, Any], keywords: list[str]) -> float:
    score = 0.0
    text = " ".join([
        rel,
        rec.get("module_doc", ""),
        rec.get("skeleton", ""),
        " ".join(rec.get("classes", [])),
        " ".join(rec.get("functions", [])),
    ]).lower()

    for kw in keywords:
        kw_l = kw.lower()
        if kw_l in rel.lower():
            score += 3.0
        if any(kw_l in c.lower() for c in rec.get("classes", [])):
            score += 2.0
        if any(kw_l in f.lower() for f in rec.get("functions", [])):
            score += 1.5
        if kw_l in rec.get("module_doc", "").lower():
            score += 1.0
        score += min(text.count(kw_l) * 0.1, 1.0)

    # Bonus: file size
    size = rec.get("size", 0)
    if size > 50_000:
        score *= 1.1
    elif size > 20_000:
        score *= 1.05

    # Bonus: ngôn ngữ source code (không phải data/text)
    lang = rec.get("language", "")
    if lang in {"python", "gdscript", "csharp", "typescript", "javascript"}:
        score *= 1.05

    return score


def query_cache(
    data: dict[str, Any],
    query: str,
    *,
    top_n: int = 10,
    min_score: float = 0.5,
    module_filter: list[str] | None = None,
    include_skeleton: bool = True,
) -> list[dict[str, Any]]:
    """Query cache với keyword search.

    Args:
        data: Cache dict từ load_cache().
        query: Keywords (space-separated).
        top_n: Số kết quả tối đa.
        min_score: Ngưỡng điểm.
        module_filter: Chỉ search trong các modules/dirs này.
        include_skeleton: Có trả về skeleton text không.

    Returns:
        List files theo relevance giảm dần.
    """
    keywords = [k for k in re.split(r"[\s,;]+", query.strip()) if k]
    if not keywords:
        return []

    files = data.get("files", {})
    if module_filter:
        # Lọc theo top-level dir
        files = {
            rel: rec for rel, rec in files.items()
            if any(rel.startswith(m + "/") or rel.startswith(m) for m in module_filter)
        }

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for rel, rec in files.items():
        s = _score_file(rel, rec, keywords)
        if s >= min_score:
            scored.append((s, rel, rec))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for score, rel, rec in scored[:top_n]:
        item: dict[str, Any] = {
            "path": rel,
            "score": round(score, 2),
            "language": rec.get("language", "?"),
            "size_kb": rec.get("size", 0) // 1024,
            "summary": rec.get("module_doc", ""),
            "classes": rec.get("classes", [])[:6],
            "functions": rec.get("functions", [])[:6],
        }
        if include_skeleton:
            item["skeleton"] = rec.get("skeleton", "")
        results.append(item)

    return results


def format_query_results(results: list[dict[str, Any]], query: str = "") -> str:
    """Format query results thành text."""
    if not results:
        return f"[cache query] No results for: {query!r}"

    lines = [
        f"# Query: {query!r} → {len(results)} results",
        "─" * 60,
    ]
    for r in results:
        lang = r.get("language", "?")
        lines.append(f"\n## {r['path']}  [score={r['score']}, {lang}, {r['size_kb']}KB]")
        if r.get("summary"):
            lines.append(f"   {r['summary']}")
        if r.get("classes"):
            lines.append(f"   Classes  : {', '.join(r['classes'])}")
        if r.get("functions"):
            lines.append(f"   Functions: {', '.join(r['functions'])}")
        if r.get("skeleton"):
            for sl in r["skeleton"].split("\n")[:15]:
                lines.append(f"   {sl}")

    return "\n".join(lines)
