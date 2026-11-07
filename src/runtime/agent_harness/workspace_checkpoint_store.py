# -*- coding: utf-8 -*-
"""Filesystem half of an AgentHarness checkpoint.

LangGraph already checkpoints cognitive execution state.  That is only half of
rollback for a coding agent: restoring the graph while leaving the repository in
its future state is time travel with the inconvenient parts left in place.

This store binds a LangGraph checkpoint id to a workspace snapshot.  Git
workspaces use compact repository state snapshots (HEAD/branch + staged and
unstaged binary patches + untracked archive).  Non-git workspaces fall back to a
bounded tree archive and explicitly report when the snapshot is degraded.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile
from typing import Any, Iterable


_CHECKPOINT_VERSION = "agent_workspace_checkpoint.v1"
_DEFAULT_MAX_TREE_BYTES = 256 * 1024 * 1024
_DEFAULT_MAX_FILE_BYTES = 32 * 1024 * 1024
_SKIP_DIRS = {
    ".git", ".eris_state", ".idea", ".vscode", ".vs", ".godot",
    "__pycache__", "node_modules", ".venv", "venv", "dist", "build",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}


@dataclass(frozen=True, slots=True)
class WorkspaceCheckpoint:
    task_id: str
    checkpoint_id: str
    thread_id: str
    workspace_path: str
    snapshot_mode: str
    created_at: float
    step: int = 0
    reason: str = ""
    graph_checkpoint_id: str = ""
    complete: bool = True
    manifest_path: str = ""
    projection_path: str = ""
    head: str = ""
    branch: str = ""
    status_sha256: str = ""
    file_count: int = 0
    bytes_archived: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        return payload


class WorkspaceCheckpointStore:
    """Persistent workspace snapshots keyed by task + LangGraph checkpoint."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        max_tree_bytes: int = _DEFAULT_MAX_TREE_BYTES,
        max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_tree_bytes = max(1, int(max_tree_bytes))
        self.max_file_bytes = max(1, int(max_file_bytes))

    def capture(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        thread_id: str,
        workspace_path: str,
        step: int = 0,
        reason: str = "",
        task_projection: dict[str, Any] | None = None,
    ) -> WorkspaceCheckpoint:
        task = _safe_component(task_id or "task")
        checkpoint = _safe_component(checkpoint_id or "checkpoint")
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.is_dir():
            raise NotADirectoryError(str(workspace))
        target = self.root / task / checkpoint
        manifest_path = target / "manifest.json"
        if manifest_path.is_file():
            existing = self._read_manifest(manifest_path)
            if existing and Path(existing.workspace_path).resolve() == workspace:
                return existing
        target.mkdir(parents=True, exist_ok=True)
        projection_path = target / "task_projection.json"
        if isinstance(task_projection, dict):
            _atomic_json_write(projection_path, task_projection)

        if _is_git_workspace(workspace):
            record = self._capture_git(
                target,
                task_id=task_id,
                checkpoint_id=checkpoint_id,
                thread_id=thread_id,
                workspace=workspace,
                step=step,
                reason=reason,
                projection_path=projection_path,
            )
        else:
            record = self._capture_tree(
                target,
                task_id=task_id,
                checkpoint_id=checkpoint_id,
                thread_id=thread_id,
                workspace=workspace,
                step=step,
                reason=reason,
                projection_path=projection_path,
            )
        _atomic_json_write(manifest_path, record.to_dict())
        return record

    def restore(
        self,
        *,
        task_id: str,
        checkpoint_id: str = "latest",
        workspace_path: str = "",
    ) -> WorkspaceCheckpoint:
        record = self.resolve(task_id=task_id, checkpoint_id=checkpoint_id)
        expected = Path(record.workspace_path).expanduser().resolve()
        if workspace_path:
            supplied = Path(workspace_path).expanduser().resolve()
            if supplied != expected:
                raise RuntimeError(f"checkpoint_workspace_mismatch:{supplied}!={expected}")
        if not expected.is_dir():
            raise NotADirectoryError(str(expected))
        manifest_path = Path(record.manifest_path)
        snapshot_dir = manifest_path.parent
        if record.snapshot_mode == "git":
            self._restore_git(snapshot_dir, expected, record)
        elif record.snapshot_mode == "tree":
            self._restore_tree(snapshot_dir, expected, record)
        else:
            raise RuntimeError(f"unknown_snapshot_mode:{record.snapshot_mode}")
        return record

    def list(self, task_id: str, *, limit: int = 40) -> list[WorkspaceCheckpoint]:
        task_dir = self.root / _safe_component(task_id or "task")
        if not task_dir.is_dir():
            return []
        records: list[WorkspaceCheckpoint] = []
        for manifest in task_dir.glob("*/manifest.json"):
            record = self._read_manifest(manifest)
            if record is not None:
                records.append(record)
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records[: max(1, int(limit))]

    def resolve(self, *, task_id: str, checkpoint_id: str = "latest") -> WorkspaceCheckpoint:
        records = self.list(task_id, limit=200)
        if not records:
            raise FileNotFoundError(f"no_workspace_checkpoint:{task_id}")
        target = str(checkpoint_id or "latest").strip()
        if target.lower() in {"", "latest", "last"}:
            return records[0]
        for record in records:
            if target in {record.checkpoint_id, record.graph_checkpoint_id}:
                return record
        raise KeyError(f"workspace_checkpoint_not_found:{task_id}:{target}")

    def load_projection(self, record: WorkspaceCheckpoint) -> dict[str, Any]:
        path = Path(record.projection_path) if record.projection_path else Path(record.manifest_path).parent / "task_projection.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return dict(payload) if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}

    def _capture_git(
        self,
        target: Path,
        *,
        task_id: str,
        checkpoint_id: str,
        thread_id: str,
        workspace: Path,
        step: int,
        reason: str,
        projection_path: Path,
    ) -> WorkspaceCheckpoint:
        head = _git_text(workspace, ["rev-parse", "HEAD"], allow_fail=True).strip()
        branch = _git_text(workspace, ["branch", "--show-current"], allow_fail=True).strip()
        status = _git_bytes(workspace, ["status", "--porcelain=v1", "-z"], allow_fail=False)
        staged = _git_bytes(workspace, ["diff", "--binary", "--cached", "HEAD", "--", "."], allow_fail=not bool(head)) if head else b""
        unstaged = _git_bytes(workspace, ["diff", "--binary", "--", "."], allow_fail=False)
        (target / "staged.patch").write_bytes(staged)
        (target / "unstaged.patch").write_bytes(unstaged)

        raw_untracked = _git_bytes(workspace, ["ls-files", "--others", "--exclude-standard", "-z"], allow_fail=False)
        untracked = [item.decode("utf-8", errors="surrogateescape") for item in raw_untracked.split(b"\0") if item]
        archived, archived_bytes, warnings = self._archive_paths(workspace, target / "untracked.zip", untracked)
        complete = not warnings
        return WorkspaceCheckpoint(
            task_id=task_id,
            checkpoint_id=checkpoint_id,
            graph_checkpoint_id=checkpoint_id,
            thread_id=thread_id,
            workspace_path=str(workspace),
            snapshot_mode="git",
            created_at=time.time(),
            step=int(step),
            reason=str(reason or ""),
            complete=complete,
            manifest_path=str((target / "manifest.json").resolve()),
            projection_path=str(projection_path.resolve()) if projection_path.is_file() else "",
            head=head,
            branch=branch,
            status_sha256=hashlib.sha256(status).hexdigest(),
            file_count=archived,
            bytes_archived=archived_bytes + len(staged) + len(unstaged),
            warnings=tuple(warnings),
        )

    def _restore_git(self, snapshot_dir: Path, workspace: Path, record: WorkspaceCheckpoint) -> None:
        # Restore branch/HEAD first. This is an internal rollback operation invoked
        # by the control plane, not a shell command available to Gemini.
        if record.head:
            current_branch = _git_text(workspace, ["branch", "--show-current"], allow_fail=True).strip()
            if record.branch and current_branch != record.branch:
                _git_run(workspace, ["checkout", "-f", record.branch])
            elif not record.branch:
                _git_run(workspace, ["checkout", "--detach", "-f", record.head])
            _git_run(workspace, ["reset", "--hard", record.head])
        else:
            # Unborn repository: reset index/worktree as much as Git permits.
            _git_run(workspace, ["reset", "--hard"], allow_fail=True)

        # Delete only current *untracked, non-ignored* files. Ignored caches/build
        # products are deliberately outside the snapshot contract.
        raw_current = _git_bytes(workspace, ["ls-files", "--others", "--exclude-standard", "-z"], allow_fail=False)
        for raw in [item for item in raw_current.split(b"\0") if item]:
            rel = raw.decode("utf-8", errors="surrogateescape")
            _remove_safe(workspace, rel)

        staged = snapshot_dir / "staged.patch"
        if staged.is_file() and staged.stat().st_size:
            _git_run(workspace, ["apply", "--binary", "--index", "--whitespace=nowarn", str(staged)])
        unstaged = snapshot_dir / "unstaged.patch"
        if unstaged.is_file() and unstaged.stat().st_size:
            _git_run(workspace, ["apply", "--binary", "--whitespace=nowarn", str(unstaged)])
        archive = snapshot_dir / "untracked.zip"
        if archive.is_file():
            _safe_extract_zip(archive, workspace)

        actual = _git_bytes(workspace, ["status", "--porcelain=v1", "-z"], allow_fail=False)
        actual_hash = hashlib.sha256(actual).hexdigest()
        if record.complete and record.status_sha256 and actual_hash != record.status_sha256:
            raise RuntimeError(
                f"workspace_restore_verification_failed:expected={record.status_sha256[:12]} actual={actual_hash[:12]}"
            )

    def _capture_tree(
        self,
        target: Path,
        *,
        task_id: str,
        checkpoint_id: str,
        thread_id: str,
        workspace: Path,
        step: int,
        reason: str,
        projection_path: Path,
    ) -> WorkspaceCheckpoint:
        paths: list[str] = []
        warnings: list[str] = []
        total = 0
        for path in workspace.rglob("*"):
            rel = path.relative_to(workspace)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            if not path.is_file() and not path.is_symlink():
                continue
            try:
                size = path.lstat().st_size
            except OSError:
                warnings.append(f"unreadable:{rel.as_posix()}")
                continue
            if size > self.max_file_bytes:
                warnings.append(f"file_too_large:{rel.as_posix()}:{size}")
                continue
            if total + size > self.max_tree_bytes:
                warnings.append(f"snapshot_budget_exceeded:{self.max_tree_bytes}")
                break
            total += size
            paths.append(rel.as_posix())
        archived, archived_bytes, archive_warnings = self._archive_paths(workspace, target / "tree.zip", paths)
        warnings.extend(archive_warnings)
        tree_manifest = sorted(paths)
        (target / "tree_files.json").write_text(json.dumps(tree_manifest, ensure_ascii=False), encoding="utf-8")
        return WorkspaceCheckpoint(
            task_id=task_id,
            checkpoint_id=checkpoint_id,
            graph_checkpoint_id=checkpoint_id,
            thread_id=thread_id,
            workspace_path=str(workspace),
            snapshot_mode="tree",
            created_at=time.time(),
            step=int(step),
            reason=str(reason or ""),
            complete=not warnings,
            manifest_path=str((target / "manifest.json").resolve()),
            projection_path=str(projection_path.resolve()) if projection_path.is_file() else "",
            file_count=archived,
            bytes_archived=archived_bytes,
            warnings=tuple(warnings),
        )

    def _restore_tree(self, snapshot_dir: Path, workspace: Path, record: WorkspaceCheckpoint) -> None:
        if not record.complete:
            raise RuntimeError("degraded_tree_checkpoint_refuses_destructive_restore")
        listed = snapshot_dir / "tree_files.json"
        expected = set(json.loads(listed.read_text(encoding="utf-8"))) if listed.is_file() else set()
        # Remove current non-ignored files not present in the snapshot.
        for path in list(workspace.rglob("*")):
            rel = path.relative_to(workspace)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            rel_text = rel.as_posix()
            if (path.is_file() or path.is_symlink()) and rel_text not in expected:
                path.unlink(missing_ok=True)
        archive = snapshot_dir / "tree.zip"
        if not archive.is_file():
            raise FileNotFoundError(str(archive))
        _safe_extract_zip(archive, workspace)

    def _archive_paths(self, workspace: Path, archive_path: Path, relative_paths: Iterable[str]) -> tuple[int, int, list[str]]:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        total = 0
        warnings: list[str] = []
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for rel_text in relative_paths:
                try:
                    path = _resolve_within(workspace, rel_text)
                    if not path.exists() and not path.is_symlink():
                        continue
                    size = path.lstat().st_size
                    if size > self.max_file_bytes:
                        warnings.append(f"file_too_large:{rel_text}:{size}")
                        continue
                    if total + size > self.max_tree_bytes:
                        warnings.append(f"snapshot_budget_exceeded:{self.max_tree_bytes}")
                        break
                    archive.write(path, arcname=str(rel_text).replace("\\", "/"))
                    total += size
                    count += 1
                except (OSError, ValueError) as exc:
                    warnings.append(f"archive_failed:{rel_text}:{type(exc).__name__}")
        return count, total, warnings

    @staticmethod
    def _read_manifest(path: Path) -> WorkspaceCheckpoint | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
            return WorkspaceCheckpoint(
                task_id=str(payload.get("task_id", "") or ""),
                checkpoint_id=str(payload.get("checkpoint_id", "") or ""),
                thread_id=str(payload.get("thread_id", "") or ""),
                workspace_path=str(payload.get("workspace_path", "") or ""),
                snapshot_mode=str(payload.get("snapshot_mode", "") or ""),
                created_at=float(payload.get("created_at", 0.0) or 0.0),
                step=int(payload.get("step", 0) or 0),
                reason=str(payload.get("reason", "") or ""),
                graph_checkpoint_id=str(payload.get("graph_checkpoint_id", payload.get("checkpoint_id", "")) or ""),
                complete=bool(payload.get("complete", False)),
                manifest_path=str(path.resolve()),
                projection_path=str(payload.get("projection_path", "") or ""),
                head=str(payload.get("head", "") or ""),
                branch=str(payload.get("branch", "") or ""),
                status_sha256=str(payload.get("status_sha256", "") or ""),
                file_count=int(payload.get("file_count", 0) or 0),
                bytes_archived=int(payload.get("bytes_archived", 0) or 0),
                warnings=tuple(str(item) for item in warnings),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None


def _is_git_workspace(root: Path) -> bool:
    if shutil.which("git") is None:
        return False
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, check=False,
    )
    return completed.returncode == 0 and (completed.stdout or "").strip().lower() == "true"


def _git_text(root: Path, args: list[str], *, allow_fail: bool = False) -> str:
    completed = _git_run(root, args, allow_fail=allow_fail, text=True)
    return str(completed.stdout or "")


def _git_bytes(root: Path, args: list[str], *, allow_fail: bool = False) -> bytes:
    completed = _git_run(root, args, allow_fail=allow_fail, text=False)
    return bytes(completed.stdout or b"")


def _git_run(root: Path, args: list[str], *, allow_fail: bool = False, text: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0 and not allow_fail:
        stderr = completed.stderr if text else (completed.stderr or b"").decode("utf-8", errors="replace")
        stdout = completed.stdout if text else (completed.stdout or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"git_snapshot_command_failed:{' '.join(args)}:{(stderr or stdout)[:1000]}")
    return completed


def _resolve_within(root: Path, relative: str) -> Path:
    rel = Path(str(relative or "").replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe_snapshot_path:{relative}")
    target = (root / rel).resolve(strict=False)
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"snapshot_path_outside_workspace:{relative}") from exc
    return target


def _remove_safe(root: Path, relative: str) -> None:
    target = _resolve_within(root, relative)
    if target.is_symlink() or target.is_file():
        target.unlink(missing_ok=True)
    elif target.is_dir():
        shutil.rmtree(target)


def _safe_extract_zip(archive_path: Path, root: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as archive:
        for info in archive.infolist():
            target = _resolve_within(root, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)


def _safe_component(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or ""))
    return clean[:160] or "checkpoint"


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


__all__ = ["WorkspaceCheckpoint", "WorkspaceCheckpointStore"]
