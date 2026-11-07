from __future__ import annotations

import warnings
import zipfile
from pathlib import Path

import pytest

from tools.package_source import package, sha256
from tools.verify_release import inspect_archive, safe_extract_archive


def test_source_archives_are_reproducible(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "b.txt").write_text("beta\n", encoding="utf-8")
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    package(root, first)
    package(root, second)

    assert sha256(first) == sha256(second)
    assert inspect_archive(first).source_root == "source"


def test_archive_verifier_rejects_path_traversal(tmp_path: Path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("source/../../escape.txt", "nope")

    with pytest.raises(RuntimeError, match="unsafe archive path"):
        inspect_archive(archive)


def test_archive_verifier_rejects_duplicate_entries(tmp_path: Path):
    archive = tmp_path / "duplicate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("source/a.txt", "one")
            bundle.writestr("source/a.txt", "two")

    with pytest.raises(RuntimeError, match="duplicate archive entry"):
        inspect_archive(archive)


def test_archive_verifier_rejects_symlinks(tmp_path: Path):
    archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("source/link")
    info.create_system = 3
    info.external_attr = (0o120777 & 0xFFFF) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(info, "target")

    with pytest.raises(RuntimeError, match="symlink"):
        inspect_archive(archive)


def test_safe_extract_keeps_files_under_target(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "hello.txt").write_text("hello", encoding="utf-8")
    archive = tmp_path / "safe.zip"
    package(root, archive)
    target = tmp_path / "extract"

    inspection = safe_extract_archive(archive, target)

    assert inspection.file_count == 1
    assert (target / "source" / "hello.txt").read_text(encoding="utf-8") == "hello"
