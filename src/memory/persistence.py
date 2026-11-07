from __future__ import annotations

"""
Persistence JSON cho memory ERIS.

File này chỉ làm nhiệm vụ đọc/ghi bền vững, atomic và có backup nhẹ. Nó không
kiểm permission, không đo confidence, không route user correction. Những việc đó
thuộc commit protocol. Persistence mà biết quá nhiều policy là dấu hiệu một
module vừa tự thăng chức trái phép.
"""

import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, TypeVar

from .schemas import (
    SCHEMA_VERSION,
    ActiveStateSnapshot,
    CommitDecision,
    MemoryStateBundle,
    bundle_from_mapping,
    bundle_to_mapping,
    coerce_dataclass,
    now_iso,
    to_plain_data,
)

T = TypeVar("T")


@dataclass(frozen=True)
class MemoryPersistenceConfig:
    """Cấu hình đọc/ghi memory JSON."""

    path: str | Path
    schema_version: int = SCHEMA_VERSION
    backup_count: int = 3
    indent: int = 2
    ensure_ascii: bool = False
    create_parent: bool = True
    write_backup: bool = True

    def resolved_path(self) -> Path:
        """Trả path đã expand user/env."""

        return Path(os.path.expandvars(os.path.expanduser(str(self.path)))).resolve()


class MemoryPersistenceError(RuntimeError):
    """Lỗi persistence có ngữ cảnh rõ ràng hơn JSONDecodeError trần trụi."""


class JsonMemoryPersistence:
    """Lớp persistence chính cho MemoryStateBundle.

    Ghi file bằng atomic replace:
    1. ghi vào temporary file cùng thư mục,
    2. fsync,
    3. os.replace sang file thật.

    Cách này tránh cảnh runtime chết giữa chừng rồi để lại JSON cụt. Một loại
    bi kịch rất thấp cấp, nhưng vẫn đủ làm memory load nổ vào sáng hôm sau.
    """

    def __init__(self, config: MemoryPersistenceConfig | str | Path):
        if not isinstance(config, MemoryPersistenceConfig):
            config = MemoryPersistenceConfig(path=config)
        self.config = config
        self.path = config.resolved_path()
        self._lock = threading.RLock()
        if self.config.create_parent:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # API cấp cao cho bundle
    # ------------------------------------------------------------------
    def load_bundle(self) -> MemoryStateBundle:
        """Load toàn bộ memory bundle. File chưa tồn tại thì trả bundle rỗng."""

        with self._lock:
            raw = self.load_raw()
            migrated = self._migrate_raw(raw)
            return bundle_from_mapping(migrated)

    def save_bundle(self, bundle: MemoryStateBundle) -> None:
        """Ghi toàn bộ bundle xuống disk bằng atomic write."""

        with self._lock:
            data = bundle_to_mapping(bundle)
            data["schema_version"] = self.config.schema_version
            data["updated_at"] = now_iso()
            self.save_raw(data)

    def update_bundle(self, mutator: Callable[[MemoryStateBundle], MemoryStateBundle | None]) -> MemoryStateBundle:
        """Load -> mutate -> save trong cùng một lock.

        mutator có thể sửa in-place và trả None, hoặc trả bundle mới.
        """

        with self._lock:
            bundle = self.load_bundle()
            result = mutator(bundle)
            if result is not None:
                bundle = result
            self.save_bundle(bundle)
            return bundle

    # ------------------------------------------------------------------
    # API tiện dụng cho collection
    # ------------------------------------------------------------------
    def append_to_collection(self, collection_name: str, record: Any) -> MemoryStateBundle:
        """Append một record vào collection trong bundle rồi lưu."""

        def mutate(bundle: MemoryStateBundle) -> MemoryStateBundle:
            current = tuple(getattr(bundle, collection_name, ()) or ())
            if not hasattr(bundle, collection_name):
                raise MemoryPersistenceError(f"Unknown memory collection: {collection_name}")
            setattr(bundle, collection_name, (*current, record))
            bundle.updated_at = now_iso()
            return bundle

        return self.update_bundle(mutate)

    def replace_collection(self, collection_name: str, records: tuple[Any, ...] | list[Any]) -> MemoryStateBundle:
        """Thay toàn bộ một collection. Dùng cho compaction/decay."""

        def mutate(bundle: MemoryStateBundle) -> MemoryStateBundle:
            if not hasattr(bundle, collection_name):
                raise MemoryPersistenceError(f"Unknown memory collection: {collection_name}")
            setattr(bundle, collection_name, tuple(records or ()))
            bundle.updated_at = now_iso()
            return bundle

        return self.update_bundle(mutate)

    def set_active_state(self, snapshot: ActiveStateSnapshot | Mapping[str, Any]) -> MemoryStateBundle:
        """Overwrite active_state snapshot."""

        coerced = coerce_dataclass(ActiveStateSnapshot, snapshot)

        def mutate(bundle: MemoryStateBundle) -> MemoryStateBundle:
            bundle.active_state = coerced
            bundle.updated_at = now_iso()
            return bundle

        return self.update_bundle(mutate)

    def append_commit_audit(self, decision: CommitDecision | Mapping[str, Any]) -> MemoryStateBundle:
        """Append audit decision để debug vì sao memory được lưu/bị bỏ."""

        record = coerce_dataclass(CommitDecision, decision)
        return self.append_to_collection("commit_audit", record)

    def compact(self, *, limits: Mapping[str, int]) -> MemoryStateBundle:
        """Cắt bớt collection theo giới hạn giữ lại gần nhất.

        Hàm này không quyết định decay semantic phức tạp. Nó chỉ cắt đuôi vật lý
        khi file quá lớn. Memory pruning thông minh nên nằm ở DreamConsolidation.
        """

        def mutate(bundle: MemoryStateBundle) -> MemoryStateBundle:
            for collection_name, limit in limits.items():
                if not hasattr(bundle, collection_name):
                    continue
                safe_limit = max(0, int(limit))
                current = tuple(getattr(bundle, collection_name, ()) or ())
                if safe_limit and len(current) > safe_limit:
                    setattr(bundle, collection_name, current[-safe_limit:])
            bundle.updated_at = now_iso()
            return bundle

        return self.update_bundle(mutate)

    # ------------------------------------------------------------------
    # API raw JSON
    # ------------------------------------------------------------------
    def load_raw(self) -> dict[str, Any]:
        """Load dict JSON thô. File chưa tồn tại thì trả dict rỗng."""

        with self._lock:
            if not self.path.exists():
                return {}
            try:
                text = self.path.read_text(encoding="utf-8")
                if not text.strip():
                    return {}
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise MemoryPersistenceError(f"Memory JSON is corrupted: {self.path}: {exc}") from exc
            except OSError as exc:
                raise MemoryPersistenceError(f"Cannot read memory file: {self.path}: {exc}") from exc
            if not isinstance(data, MutableMapping):
                raise MemoryPersistenceError(f"Memory file root must be an object: {self.path}")
            return dict(data)

    def save_raw(self, data: Mapping[str, Any]) -> None:
        """Ghi dict JSON thô bằng atomic write."""

        with self._lock:
            payload = dict(data or {})
            payload.setdefault("schema_version", self.config.schema_version)
            payload["updated_at"] = now_iso()
            if self.config.write_backup and self.path.exists():
                self._rotate_backups()
            text = json.dumps(
                to_plain_data(payload),
                ensure_ascii=self.config.ensure_ascii,
                indent=self.config.indent,
                sort_keys=True,
            )
            self._atomic_write_text(text + "\n")

    def exists(self) -> bool:
        return self.path.exists()

    def backup_now(self, suffix: str | None = None) -> Path | None:
        """Tạo backup thủ công và trả path backup."""

        with self._lock:
            if not self.path.exists():
                return None
            suffix = suffix or time_safe_suffix()
            backup_path = self.path.with_name(f"{self.path.name}.{suffix}.bak")
            shutil.copy2(self.path, backup_path)
            return backup_path

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------
    def _atomic_write_text(self, text: str) -> None:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            raise MemoryPersistenceError(f"Cannot write memory file atomically: {self.path}: {exc}") from exc
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def _rotate_backups(self) -> None:
        """Giữ một vòng backup đơn giản: file.bak1, file.bak2, ..."""

        count = max(0, int(self.config.backup_count or 0))
        if count <= 0 or not self.path.exists():
            return
        for index in range(count, 0, -1):
            src = self.path.with_name(f"{self.path.name}.bak{index}")
            dst = self.path.with_name(f"{self.path.name}.bak{index + 1}")
            if src.exists():
                if index >= count:
                    src.unlink(missing_ok=True)
                else:
                    os.replace(src, dst)
        shutil.copy2(self.path, self.path.with_name(f"{self.path.name}.bak1"))

    def _migrate_raw(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Migration rất nhẹ cho schema cũ/thiếu field.

        Không cố đoán nghiệp vụ. Chỉ đảm bảo các key nền tồn tại để bundle load
        được. Những migration sâu hơn nên viết thành script rõ ràng, đừng giấu
        trong load path như một quả mìn lịch sự.
        """

        data = dict(raw or {})
        data.setdefault("schema_version", self.config.schema_version)
        data.setdefault("updated_at", now_iso())
        data.setdefault("event_ledger", [])
        data.setdefault("active_state", {})
        data.setdefault("episodic_memory", [])
        data.setdefault("semantic_memory", [])
        data.setdefault("relational_memory", [])
        data.setdefault("affective_residue_memory", [])
        data.setdefault("procedural_memory", [])
        data.setdefault("repair_memory", [])
        data.setdefault("commit_audit", [])
        data.setdefault("metadata", {})

        # Một vài alias legacy thường gặp. Không tự merge quá thông minh.
        alias_pairs = {
            "episodic": "episodic_memory",
            "semantic": "semantic_memory",
            "relationship": "relational_memory",
            "relational": "relational_memory",
            "emotional_residue": "affective_residue_memory",
            "procedural": "procedural_memory",
            "repair": "repair_memory",
            "events": "event_ledger",
        }
        for old_key, new_key in alias_pairs.items():
            if old_key in data and not data.get(new_key):
                data[new_key] = data.get(old_key) or []
        return data


def time_safe_suffix() -> str:
    """Timestamp dùng được trong tên file backup."""

    return now_iso().replace(":", "").replace("+", "p").replace("-", "m")


def load_memory_bundle(path: str | Path) -> MemoryStateBundle:
    """Helper ngắn cho code không muốn giữ instance persistence."""

    return JsonMemoryPersistence(path).load_bundle()


def save_memory_bundle(path: str | Path, bundle: MemoryStateBundle) -> None:
    """Helper ngắn để ghi bundle."""

    JsonMemoryPersistence(path).save_bundle(bundle)


def update_memory_bundle(
    path: str | Path,
    mutator: Callable[[MemoryStateBundle], MemoryStateBundle | None],
) -> MemoryStateBundle:
    """Helper ngắn cho thao tác load-mutate-save."""

    return JsonMemoryPersistence(path).update_bundle(mutator)
