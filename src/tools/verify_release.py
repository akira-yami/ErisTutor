"""Inspect and safely extract release ZIP archives."""
from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    archive: Path
    source_root: str
    file_count: int
    total_uncompressed_bytes: int
    sha256: str


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_name(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or path.is_absolute():
        raise RuntimeError(f"unsafe archive path: {name}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"unsafe archive path: {name}")
    if path.parts and ":" in path.parts[0]:
        raise RuntimeError(f"unsafe archive path: {name}")
    return path


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def inspect_archive(archive_path: str | Path) -> ArchiveInspection:
    archive = Path(archive_path).resolve()
    seen: set[str] = set()
    roots: set[str] = set()
    file_count = 0
    total = 0
    with zipfile.ZipFile(archive, "r") as bundle:
        for info in bundle.infolist():
            path = _validate_name(info.filename)
            canonical = path.as_posix()
            if canonical in seen:
                raise RuntimeError(f"duplicate archive entry: {canonical}")
            seen.add(canonical)
            roots.add(path.parts[0])
            if _is_symlink(info):
                raise RuntimeError(f"symlink archive entry: {canonical}")
            if not info.is_dir():
                file_count += 1
                total += int(info.file_size)
    if len(roots) != 1:
        raise RuntimeError("archive must contain exactly one source root")
    return ArchiveInspection(
        archive=archive,
        source_root=next(iter(roots)),
        file_count=file_count,
        total_uncompressed_bytes=total,
        sha256=_digest(archive),
    )


def safe_extract_archive(archive_path: str | Path, target_dir: str | Path) -> ArchiveInspection:
    inspection = inspect_archive(archive_path)
    target = Path(target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(inspection.archive, "r") as bundle:
        for info in bundle.infolist():
            relative = _validate_name(info.filename)
            destination = (target / Path(*relative.parts)).resolve()
            try:
                destination.relative_to(target)
            except ValueError as exc:
                raise RuntimeError(f"unsafe archive path: {info.filename}") from exc
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info, "r") as source, destination.open("wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            os.chmod(destination, 0o644)
    return inspection


__all__ = ["ArchiveInspection", "inspect_archive", "safe_extract_archive"]
