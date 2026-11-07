# -*- coding: utf-8 -*-
"""Repo Detector — Auto-detect bất kỳ repo nào.

Không hardcode cho bất kỳ project cụ thể. Tự:
- Tìm repo root (đi ngược tree tìm .git, pyproject.toml, package.json, ...)
- Detect ngôn ngữ dominant (đếm file theo extension)
- Auto-generate partition plan theo top-level dirs
- Tạo repo_id unique để lưu cache riêng biệt

Dùng được cho eris (Python), ashes-of-a-dying-world (GDScript+C#),
hoặc bất kỳ repo nào.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Markers nhận diện project root (theo thứ tự ưu tiên)
_ROOT_MARKERS = [
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "CMakeLists.txt",
    "project.godot",     # Godot project
    "*.sln",             # Visual Studio solution
    "composer.json",
    "Gemfile",
    "build.gradle",
    "pom.xml",
]

# Extension → tên ngôn ngữ chuẩn
_EXT_TO_LANG: dict[str, str] = {
    ".py":    "python",
    ".gd":    "gdscript",
    ".cs":    "csharp",
    ".ts":    "typescript",
    ".tsx":   "typescript",
    ".js":    "javascript",
    ".jsx":   "javascript",
    ".rs":    "rust",
    ".go":    "go",
    ".java":  "java",
    ".cpp":   "cpp",
    ".cc":    "cpp",
    ".cxx":   "cpp",
    ".c":     "c",
    ".h":     "c",
    ".hpp":   "cpp",
    ".rb":    "ruby",
    ".php":   "php",
    ".swift": "swift",
    ".kt":    "kotlin",
    ".lua":   "lua",
    ".shader": "shader",
    ".gdshader": "shader",
    ".hlsl":  "shader",
    ".glsl":  "shader",
    ".wgsl":  "shader",
}

# Thư mục luôn bỏ qua khi scan
_SKIP_DIRS_GLOBAL: set[str] = {
    ".git", ".svn", ".hg",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    "node_modules", ".yarn",
    ".venv", "venv", "env", ".env",
    "dist", "build", "out", "target", "bin", "obj",
    ".godot",
    # Bundled / vendored runtimes (not source code)
    "portable_python",
    "paraphrase-multilingual-MiniLM-L12-v2",
    "eris_runtime_source.egg-info",
    ".tmp_pytest", ".tmp_secrets_setup_test_71124d892a6c4dccb68195a5480d3c33",
    # Misc repo-specific non-source dirs
    "scratch", "eris-code", "v57_reports", ".v57_runs",
    "docker-data", "release", "installer",
    "embedding_model", "live2d_body",
}

# Thư mục ít thú vị (có thể index nhưng ưu tiên thấp)
_LOW_PRIORITY_DIRS: set[str] = {
    "assets", "asset", "data", "docs", "doc", "logs", "log",
    "config", "configs", "release", "releases", "dist_src",
    "voice_presets", "embedding_model",
}

# Max files per top-level dir để partition (tránh 1 worker quá nặng)
_MAX_FILES_PER_PARTITION = 80


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RepoInfo:
    """Thông tin về một repo đã được detect."""
    root: Path
    name: str
    repo_id: str

    # Language stats: {lang_name: file_count}
    languages: dict[str, int] = field(default_factory=dict)
    dominant_lang: str = "unknown"

    # Top-level dirs (đã lọc bỏ skip dirs)
    top_dirs: list[str] = field(default_factory=list)

    # File counts per top-level dir
    dir_file_counts: dict[str, int] = field(default_factory=dict)

    # Auto-generated partition plan
    partitions: list[dict[str, Any]] = field(default_factory=list)

    # Root-level files đáng chú ý
    notable_root_files: list[str] = field(default_factory=list)

    # Tổng files
    total_files: int = 0

    def summary(self) -> str:
        lang_str = ", ".join(
            f"{lang}:{count}" for lang, count in
            sorted(self.languages.items(), key=lambda x: -x[1])[:5]
        )
        return (
            f"Repo: {self.name} | "
            f"Root: {self.root} | "
            f"ID: {self.repo_id} | "
            f"Files: {self.total_files} | "
            f"Languages: {lang_str} | "
            f"Dominant: {self.dominant_lang}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Repo root detection
# ─────────────────────────────────────────────────────────────────────────────

def find_repo_root(start: Path | None = None) -> Path | None:
    """Đi ngược tree từ start để tìm repo root.

    Nhận diện qua các markers: .git, pyproject.toml, package.json, project.godot, ...

    Args:
        start: Thư mục bắt đầu. None = cwd.

    Returns:
        Path của repo root, hoặc None nếu không tìm thấy.
    """
    current = (start or Path.cwd()).resolve()

    # Walk up
    for candidate in [current, *current.parents]:
        for marker in _ROOT_MARKERS:
            if marker.startswith("*"):
                # Glob pattern
                ext = marker[1:]
                if any(candidate.glob(f"*{ext}")):
                    return candidate
            else:
                if (candidate / marker).exists():
                    return candidate

    return None


def make_repo_id(root: Path) -> str:
    """Tạo ID unique cho repo từ absolute path.

    Format: <sha1[:12]>_<repo-name>
    Ví dụ: a3f9c2d1b8e0_eris
    """
    path_hash = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]
    safe_name = root.name.replace(" ", "_").replace(".", "-")[:40]
    return f"{path_hash}_{safe_name}"


# ─────────────────────────────────────────────────────────────────────────────
# Language detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_languages(
    root: Path,
    *,
    skip_dirs: set[str] | None = None,
    max_files: int = 5000,
) -> dict[str, int]:
    """Đếm số file theo ngôn ngữ trong toàn repo.

    Returns:
        Dict {lang_name: file_count}, sorted by count descending.
    """
    skip = skip_dirs or _SKIP_DIRS_GLOBAL
    counts: dict[str, int] = {}
    total = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if total >= max_files:
                break
            ext = Path(fn).suffix.lower()
            lang = _EXT_TO_LANG.get(ext)
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
                total += 1

    return dict(sorted(counts.items(), key=lambda x: -x[1]))


# ─────────────────────────────────────────────────────────────────────────────
# Top-dir discovery + partitioning
# ─────────────────────────────────────────────────────────────────────────────

def _count_source_files(dir_path: Path, supported_exts: set[str], skip_dirs: set[str]) -> int:
    """Đếm số source files trong một thư mục (recursive)."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(dir_path):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if Path(fn).suffix.lower() in supported_exts:
                count += 1
    return count


def discover_top_dirs(
    root: Path,
    supported_langs: set[str],
    *,
    skip_dirs: set[str] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Tìm top-level dirs có source files đáng index.

    Returns:
        (sorted_dirs, dir_file_counts)
    """
    skip = skip_dirs or _SKIP_DIRS_GLOBAL
    supported_exts = {
        ext for ext, lang in _EXT_TO_LANG.items() if lang in supported_langs
    }

    dir_counts: dict[str, int] = {}
    try:
        top_entries = sorted(root.iterdir())
    except PermissionError:
        return [], {}

    for entry in top_entries:
        if not entry.is_dir():
            continue
        if entry.name in skip:
            continue
        if entry.name.startswith("."):
            continue
        count = _count_source_files(entry, supported_exts, skip)
        if count > 0:
            dir_counts[entry.name] = count

    sorted_dirs = sorted(dir_counts.keys(), key=lambda d: -dir_counts[d])
    return sorted_dirs, dir_counts


def _find_notable_root_files(root: Path) -> list[str]:
    """Tìm files đáng chú ý ở root level."""
    notable = []
    candidates = [
        "README.md", "README.rst", "readme.md",
        "pyproject.toml", "package.json", "Cargo.toml",
        "requirements.txt", "requirements-dev.txt",
        ".env.example", "env-example", ".env.example",
        "project.godot",
        "CHANGELOG.md", "CHANGELOG.rst",
    ]
    for fname in candidates:
        if (root / fname).exists():
            notable.append(fname)
    return notable


def build_partitions(
    top_dirs: list[str],
    dir_file_counts: dict[str, int],
    *,
    max_workers: int = 6,
    max_files_per_partition: int = _MAX_FILES_PER_PARTITION,
) -> list[dict[str, Any]]:
    """Tự động chia top-level dirs thành partitions cho workers.

    Strategy:
    - Dirs có nhiều files → có thể chia thành nhiều partitions
    - Dirs nhỏ → gộp chung vào 1 partition
    - Luôn giữ số partitions <= max_workers

    Returns:
        list of partition dicts: {"name": str, "paths": [str], "exclude_subdirs": []}
    """
    if not top_dirs:
        return []

    # Phân loại: large (cần partition riêng) vs small (gộp chung)
    large_dirs = []
    small_dirs = []
    for d in top_dirs:
        count = dir_file_counts.get(d, 0)
        if count >= max_files_per_partition:
            large_dirs.append(d)
        else:
            small_dirs.append(d)

    partitions: list[dict[str, Any]] = []

    # Mỗi large dir → 1 partition
    for d in large_dirs:
        partitions.append({
            "name": d,
            "paths": [d],
            "exclude_subdirs": [],
        })

    # Gộp small dirs thành nhóm (theo max_workers)
    remaining_slots = max(1, max_workers - len(partitions))
    if small_dirs:
        # Chia small dirs thành remaining_slots nhóm
        chunk_size = max(1, len(small_dirs) // remaining_slots)
        for i in range(0, len(small_dirs), chunk_size):
            chunk = small_dirs[i : i + chunk_size]
            if chunk:
                partitions.append({
                    "name": f"misc_{i//chunk_size}",
                    "paths": chunk,
                    "exclude_subdirs": [],
                })

    # Nếu quá nhiều partitions, merge bớt
    while len(partitions) > max_workers:
        last = partitions.pop()
        partitions[-1]["paths"].extend(last["paths"])

    return partitions


# ─────────────────────────────────────────────────────────────────────────────
# Main API
# ─────────────────────────────────────────────────────────────────────────────

def detect_repo(
    start: Path | None = None,
    *,
    repo_root: Path | None = None,
    max_workers: int = 6,
    verbose: bool = False,
) -> RepoInfo:
    """Detect và phân tích một repo tự động.

    Args:
        start: Thư mục bắt đầu tìm root. None = cwd.
        repo_root: Chỉ định explicit (bỏ qua auto-detect).
        max_workers: Số workers để tạo partition plan.
        verbose: In tiến trình.

    Returns:
        RepoInfo đầy đủ.

    Raises:
        ValueError: Nếu không tìm được repo root.
    """
    import sys

    if repo_root is not None:
        root = Path(repo_root).resolve()
    else:
        root = find_repo_root(start)
        if root is None:
            raise ValueError(
                f"Cannot find repo root from {start or Path.cwd()}. "
                "Make sure you are inside a git repo or project directory."
            )

    name = root.name
    repo_id = make_repo_id(root)

    if verbose:
        print(f"[detector] Repo: {name} @ {root}", file=sys.stderr)
        print(f"[detector] Repo ID: {repo_id}", file=sys.stderr)

    # Language detection
    languages = detect_languages(root)
    dominant_lang = next(iter(languages), "unknown") if languages else "unknown"

    if verbose:
        lang_str = ", ".join(f"{l}:{c}" for l, c in list(languages.items())[:5])
        print(f"[detector] Languages: {lang_str}", file=sys.stderr)

    # Top dirs
    supported_langs = set(languages.keys())
    if not supported_langs:
        supported_langs = set(_EXT_TO_LANG.values())  # fallback: all

    top_dirs, dir_counts = discover_top_dirs(root, supported_langs)
    total_files = sum(dir_counts.values())

    if verbose:
        dirs_str = ", ".join(f"{d}({dir_counts[d]})" for d in top_dirs[:8])
        print(f"[detector] Top dirs: {dirs_str}", file=sys.stderr)

    # Partitions
    partitions = build_partitions(
        top_dirs, dir_counts,
        max_workers=max_workers,
    )

    # Root files
    notable = _find_notable_root_files(root)

    info = RepoInfo(
        root=root,
        name=name,
        repo_id=repo_id,
        languages=languages,
        dominant_lang=dominant_lang,
        top_dirs=top_dirs,
        dir_file_counts=dir_counts,
        partitions=partitions,
        notable_root_files=notable,
        total_files=total_files,
    )

    if verbose:
        print(f"[detector] {info.summary()}", file=sys.stderr)
        print(f"[detector] Partitions: {len(partitions)}", file=sys.stderr)

    return info
