# -*- coding: utf-8 -*-
"""Chân dung người dùng do Gemini viết, lưu bền vững nhưng KHÔNG phải sự thật gốc.

- Fact/episode từ Living User Memory là nguồn duy nhất được phép dùng.
- Mở panel chỉ đọc đĩa: không chờ mạng, không gọi Gemini trên luồng UI.
- Khi nguồn thay đổi, chỉ một worker nền được phép biên tập bản mới.
- Mỗi bản viết có dấu vân tay của evidence; bản cũ bị vô hiệu hóa ngay khi
  khác nguồn (đặc biệt sau khi user yêu cầu sửa / quên memory).
- Không đưa lời văn Gemini ngược lại làm bằng chứng hay ghi vào canonical store.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .presenter import (
    _MEMORY_PORTRAIT_SYSTEM, _build_evidence, _extract_text, _fallback_portrait,
    _fingerprint, _json_safe, _parse_json, _validate_model_portrait,
)

_LOG = logging.getLogger(__name__)


class SavedGeminiMemoryPortrait:
    """Reader đồng bộ cực nhanh + một writer Gemini duy nhất ở background."""

    def __init__(self, base_dir: str | os.PathLike[str], *, client: Any = None,
                 retry_seconds: float = 90.0, timeout_seconds: float = 120.0):
        self.base_dir = Path(base_dir)
        self.json_path = self.base_dir / "living_user_portrait.json"
        self.markdown_path = self.base_dir / "living_user_portrait.md"
        self.client = client
        self.retry_seconds = max(1.0, float(retry_seconds))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._wanted_fingerprint = ""
        self._generation = 0
        self._failed_at: dict[str, float] = {}
        self._cached: dict[str, Any] | None = self._load()

    def _load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
            if (isinstance(data, dict) and data.get("schema_version") == 1
                    and isinstance(data.get("portrait"), dict)):
                return data
        except (OSError, ValueError, TypeError):
            pass
        return None

    def invalidate(self) -> None:
        """Xóa bản diễn đạt cũ; dùng ngay sau hành động sửa/quên của người dùng."""
        with self._lock:
            self._generation += 1
            self._wanted_fingerprint = ""
            self._cached = None
            # Không giữ bản cũ trên đĩa sau lệnh quên/sửa.
            for path in (self.json_path, self.markdown_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    _LOG.warning("[portrait] Không xóa được bản cũ %s: %s", path, exc)

    def render(self, *, records: Sequence[Mapping[str, Any]],
               episodes: Sequence[Mapping[str, Any]] = (),
               expression_lens: Mapping[str, Any] | None = None,
               force: bool = False) -> dict[str, Any]:
        """Trả snapshot ngay; chỉ xếp việc Gemini khi nguồn đã đổi hoặc user yêu cầu."""
        evidence = _build_evidence(records, episodes)
        # Không đưa mood/lens vào fingerprint: mood dao động không phải memory đổi.
        fingerprint = _fingerprint(evidence, {})
        fallback = _fallback_portrait(records, episodes, fingerprint=fingerprint).to_dict()
        with self._lock:
            if self._wanted_fingerprint != fingerprint:
                self._generation += 1
                self._wanted_fingerprint = fingerprint
            generation = self._generation

            # File đã lưu chỉ hợp lệ khi evidence giống hệt hiện tại.
            saved = self._cached
            if saved is None:
                saved = self._load()
                self._cached = saved
            valid = bool(saved and saved.get("fingerprint") == fingerprint)
            if not valid and saved is not None:
                # Khi evidence đổi, không render bản cũ (có thể chứa fact đã bị xóa).
                self._cached = None
                self._remove_files_locked()

            if not evidence:
                # Không sinh câu chuyện từ hồ sơ trống.
                self._remove_files_locked()
                self._cached = None
                return {**fallback, "generation_status": "empty"}

            working = bool(self._worker and self._worker.is_alive())
            can_write = callable(getattr(self.client, "create_chat_completion", None))
            throttle = time.monotonic() - self._failed_at.get(fingerprint, -1e9) < self.retry_seconds
            # Nút Viết lại do người dùng chủ động có thể bỏ thời gian giãn lỗi.
            if can_write and (force or not valid) and not working and (force or not throttle):
                self._worker = threading.Thread(
                    target=self._write_in_background,
                    args=(fingerprint, generation, list(evidence), _json_safe(expression_lens or {})),
                    daemon=True, name="ERIS-GeminiMemoryPortrait",
                )
                self._worker.start()
                working = True

            status = ("writing" if working else "ready" if valid else
                      "unavailable" if not can_write else "retry_later")
            if valid:
                portrait = dict(saved["portrait"])
                portrait.update({"renderer": "gemini_saved_portrait",
                                 "generation_status": status,
                                 "version": saved.get("version", 1),
                                 "generated_at": saved.get("generated_at", 0.0)})
                return portrait
            return {**fallback, "generation_status": status}

    def _write_in_background(self, fingerprint: str, generation: int,
                             evidence: list[dict[str, Any]], lens: Any) -> None:
        """Gemini chạy độc lập. Kết quả trễ KHÔNG ĐƯỢC ghi đè bản mới hơn."""
        try:
            payload = {"expression_lens": lens, "evidence": evidence}
            response = self.client.create_chat_completion(
                messages=[
                    {"role": "system", "content": _MEMORY_PORTRAIT_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
                ],
                temperature=0.65,
                max_tokens=2300,
                response_format={"type": "json_object"},
                timeout=self.timeout_seconds,
            )
            parsed = _parse_json(_extract_text(response))
            portrait = _validate_model_portrait(parsed, evidence=evidence, fingerprint=fingerprint)
            if portrait is None:
                raise ValueError("Gemini trả về portrait không đủ evidence/không hợp lệ")
            portrait_data = portrait.to_dict()
            portrait_data["renderer"] = "gemini_saved_portrait"
            with self._lock:
                if generation != self._generation or fingerprint != self._wanted_fingerprint:
                    return  # Có lệnh quên hoặc memory đã đổi trong lúc Gemini đang viết.
                version = (int(self._cached.get("version", 0)) + 1 if self._cached else 1)
                envelope = {
                    "schema_version": 1, "version": version,
                    "fingerprint": fingerprint, "generated_at": time.time(),
                    "source_refs": sorted({str(ref) for section in portrait_data["sections"]
                                           for item in section["items"] for ref in item["source_refs"]}),
                    "portrait": portrait_data,
                }
                self.base_dir.mkdir(parents=True, exist_ok=True)
                self._atomic_write(self.json_path, json.dumps(envelope, ensure_ascii=False, indent=2))
                # Markdown là bản đọc/biên tập; JSON là nguồn duy nhất để UI load.
                try:
                    self._atomic_write(self.markdown_path, self._to_markdown(portrait_data))
                except OSError as exc:
                    _LOG.warning("[portrait] Không lưu được markdown: %s", exc)
                self._cached = envelope
                self._failed_at.pop(fingerprint, None)
        except Exception as exc:
            with self._lock:
                self._failed_at[fingerprint] = time.monotonic()
            _LOG.warning("[portrait] Gemini không tạo được bản lưu; giữ bản dự phòng: %s", exc)

    def _remove_files_locked(self) -> None:
        for path in (self.json_path, self.markdown_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                _LOG.warning("[portrait] Không xóa được bản quá hạn %s: %s", path, exc)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        # File tạm cùng thư mục + replace: crash không làm hỏng bản lưu trước.
        temp = path.with_name(path.name + ".tmp")
        try:
            with open(temp, "w", encoding="utf-8", newline="\n") as file:
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _to_markdown(portrait: Mapping[str, Any]) -> str:
        lines = ["# Trong mắt Eris", "", str(portrait.get("opening") or ""), ""]
        if portrait.get("aside"):
            lines += [f"*{portrait['aside']}*", ""]
        for section in portrait.get("sections") or []:
            lines += [f"## {section['title']}", ""]
            for item in section.get("items") or []:
                lines += [f"### {item['title']}", "", str(item["body"]), ""]
                if item.get("aside"):
                    lines += [f"*{item['aside']}*", ""]
        return "\n".join(lines).rstrip() + "\n"
