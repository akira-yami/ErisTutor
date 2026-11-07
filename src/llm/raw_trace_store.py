# -*- coding: utf-8 -*-
"""Kho chứa RAW dạng content-addressed, nén lossless và kiểm tra checksum.

Một request lớn được chia theo *nội dung* (content-defined chunking), thay vì
cắt ở offset cố định. Nếu thêm một câu vào đầu prompt, phần dài còn lại vẫn
thường tái sử dụng các object cũ. Đây chỉ là cơ chế lưu trữ, KHÔNG thay prompt.

Object ghi atomically. Manifest giữ thứ tự byte nên có thể khôi phục chính xác
JSON/text được ghi, không phải bản tóm tắt. Không nạp file RAW vào memory lúc xem
bảng index; chỉ mở chúng khi được yêu cầu --full / --export.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

# Gear-based content-defined boundaries; 4 KB min, ~16 KB trung bình, 64 KB max.
_GEAR = tuple(int.from_bytes(hashlib.sha256(bytes([i])).digest()[:8], 'little') for i in range(256))
_MIN = 4096
_MAX = 65536
_MASK = (1 << 14) - 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def json_bytes(value: Any) -> bytes:
    """Đảm bảo giữ nguyên cấu trúc và Unicode (không áp dụng sanitize)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode('utf-8')


def _chunks(data: bytes):
    pos = 0
    end = len(data)
    while pos < end:
        offset = min(pos + _MIN, end)
        limit = min(pos + _MAX, end)
        rolling = 0
        while offset < limit:
            rolling = ((rolling << 1) + _GEAR[data[offset]]) & 0xffffffffffffffff
            offset += 1
            if rolling & _MASK == 0:
                break
        yield data[pos:offset]
        pos = offset


def _object_path(directory: Path, digest: str) -> Path:
    if not _SHA256.fullmatch(digest):
        raise ValueError('Invalid RAW trace object digest')
    return directory / 'objects' / digest[:2] / (digest + '.gz')


def _write_object(directory: Path, piece: bytes) -> str:
    digest = hashlib.sha256(piece).hexdigest()
    destination = _object_path(directory, digest)
    if destination.is_file():
        return digest
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Cùng một digest có thể được tạo đồng thời; rename atomically công bố file hoàn chỉnh.
    fd, temp = tempfile.mkstemp(prefix='.pending-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(gzip.compress(piece, compresslevel=6, mtime=0))
        os.chmod(temp, 0o600)
        if not destination.is_file():
            try:
                os.replace(temp, destination)
            except OSError:
                # Trên Windows, viewer hoặc tiến trình khác có thể giữ file.
                # Nếu object cùng hash đã tồn tại thì không cần ghi đè.
                if not destination.is_file():
                    raise
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return digest


def store_payload(directory: Path, data: bytes, *, kind: str) -> dict[str, Any]:
    """Trả manifest nhỏ dùng được trực tiếp trong một dòng events.jsonl."""
    pieces = [_write_object(directory, piece) for piece in _chunks(data)]
    return {
        'version': 1, 'encoding': kind, 'sha256': hashlib.sha256(data).hexdigest(),
        'byte_length': len(data), 'chunks': pieces,
    }


def restore_payload(directory: Path, manifest: dict[str, Any]) -> bytes:
    """Không trả dữ liệu sai một cách im lặng khi object thiếu/hỏng."""
    if manifest.get('version') != 1 or not isinstance(manifest.get('chunks'), list):
        raise ValueError('Invalid RAW trace manifest')
    data = bytearray()
    for digest in manifest['chunks']:
        with gzip.open(_object_path(directory, digest), 'rb') as stream:
            piece = stream.read()
        if hashlib.sha256(piece).hexdigest() != digest:
            raise ValueError('RAW trace chunk checksum mismatch')
        data.extend(piece)
    result = bytes(data)
    if len(result) != manifest.get('byte_length') or hashlib.sha256(result).hexdigest() != manifest.get('sha256'):
        raise ValueError('RAW trace payload checksum mismatch')
    return result
