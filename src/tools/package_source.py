"""Create reproducible source ZIP archives."""
from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path
from typing import Iterable

_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_EXCLUDED_PARTS = frozenset({".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"})


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise RuntimeError(f"source contains symlink: {relative}")
        if path.is_file():
            yield path


def package(source_root: str | Path, archive_path: str | Path) -> Path:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    archive = Path(archive_path).resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    source_name = root.name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in _files(root):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(f"{source_name}/{relative}", date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.flag_bits |= 0x800
            bundle.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return archive


__all__ = ["package", "sha256"]
