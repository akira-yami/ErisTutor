# -*- coding: utf-8 -*-
"""Learner Portrait Web v1: read-only, bounded projection of Tutor and Mentor.

Gemini ONLY writes optional, source-referenced observations. It never assigns
mastery, creates a skill, edits learning history or drives the Tutor loop.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Mapping

from cortex.tutor.secret_scrubber import scrub_secrets

SCHEMA = "eris.learner_portrait.v1"
MAX_PATHS = 48  # UDP payload is bounded; the response also reports truncation.
MAX_NODES = 48


def _safe(value: object, limit: int = 140) -> str:
    return scrub_secrets(value).strip()[:limit]


def _whole(value: Any) -> int:
    try:
        return max(0, min(100000, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _summary(data: Mapping[str, Any]) -> str:
    paths = data["spaces"]
    count = len(paths)
    if not count:
        return "Tôi chưa có lộ trình học được lưu để hiểu rõ bạn đang học gì."
    title = data.get("selection", {}).get("title", "")
    if not title:
        return f"Tôi tìm thấy {count} lộ trình học đã lưu. Những chủ đề chưa được kiểm tra không đồng nghĩa là bạn không biết."
    return (f"Tôi đã thấy {count} lộ trình học được lưu. Đang xem “{title}”. "
            "Chuyện đã thảo luận không tự động chứng minh bạn làm được độc lập.")


class GeminiPortraitNarrator:
    """One optional Gemini Web lane, never on a request/UI thread.

    Workers are coalesced by fingerprint. A late result for a superseded
    fingerprint is discarded, and every displayed observation must carry
    source identifiers from this exact snapshot. No disk copy of AI claims.
    """

    SYSTEM = """Bạn là Gemini, cộng tác viên diễn giải hồ sơ học tập cho ERIS.
Chỉ nhận dữ liệu đã được runtime kiểm chứng, KHÔNG tạo lộ trình/kỹ năng,
KHÔNG đánh giá IQ, tính cách, năng lực tổng quát hoặc tự suy từ tần suất hỏi.
Nhận xét phải cụ thể, thận trọng và có source_refs nguyên xi trong input.
Không suy diễn từ tên lĩnh vực rằng người dùng đã thành thạo.
Không lặp nhận xét hiển nhiên. Chỉ xuất JSON dạng:
{"observations":[{"text":"1 câu tiếng Việt ngắn, có căn cứ", "source_refs":["ref"]}]}
Tối đa 3 observations. Nếu chưa có căn cứ, trả {"observations":[]}.
"""

    def __init__(self, adapter: object | None = None):
        self._adapter = adapter
        self._lock = threading.Lock()
        self._fingerprint = ""
        self._pending = False
        self._done: list[dict[str, Any]] | None = None
        self._error = False
        self._generation = 0
        self._worker_active = False
        self._error_at = 0.0

    @staticmethod
    def _create_adapter():
        if os.environ.get("ERIS_LEARNER_PORTRAIT_GEMINI_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
            return None
        from memory.living_user.gemini_adapter import GeminiWebUserProfileAdapter
        return GeminiWebUserProfileAdapter.create_if_available(timeout=35.0)

    def describe(self, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        # The input is already concise and redacted; send no raw messages.
        signature = json.dumps(evidence, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        with self._lock:
            if self._fingerprint != fingerprint:
                self._fingerprint = fingerprint
                self._done = None
                self._pending = False
                self._error = False
                self._generation += 1
            if not evidence:
                return {"state": "empty", "observations": []}
            if self._done is not None:
                return {"state": "ready", "observations": self._done}
            if self._error:
                if time.monotonic() - self._error_at < 90.0:
                    return {"state": "unavailable", "observations": []}
                self._error = False
            if not self._pending and self._worker_active:
                return {"state": "generating", "observations": []}
            if not self._pending:
                try:
                    adapter = self._adapter if self._adapter is not None else self._create_adapter()
                except Exception:
                    adapter = None
                if adapter is None:
                    self._error = True
                    return {"state": "unavailable", "observations": []}
                self._pending = True
                self._worker_active = True
                generation = self._generation
                thread = threading.Thread(target=self._write, args=(adapter, evidence, generation), daemon=True,
                                          name="eris-learner-portrait-gemini")
                thread.start()
            return {"state": "generating", "observations": []}

    def _write(self, adapter, evidence: list[dict[str, Any]], generation: int) -> None:
        refs = {item["ref"] for item in evidence}
        try:
            response = adapter.create_chat_completion(messages=[
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": json.dumps({"evidence": evidence}, ensure_ascii=False)},
            ], max_tokens=750)
            from memory.living_user.runtime_adapter import _parse_json, _extract_text
            payload = _parse_json(_extract_text(response))
            if not isinstance(payload, Mapping):
                raise ValueError("invalid_gemini_json")
            items = payload.get("observations", [])
            if not isinstance(items, list):
                raise ValueError("invalid_gemini_observations")
            validated = []
            for item in items[:3]:
                if not isinstance(item, Mapping) or not isinstance(item.get("text"), str):
                    continue
                source_refs = item.get("source_refs")
                if not isinstance(source_refs, list):
                    continue
                accepted = [r for r in source_refs[:4] if isinstance(r, str) and r in refs]
                text = _safe(item["text"], 270)
                if text and accepted:
                    validated.append({"text": text, "source_refs": accepted})
            failed = False
        except Exception:
            validated, failed = [], True
        with self._lock:
            self._worker_active = False
            if generation != self._generation:
                return  # stale Gemini result cannot replace a newer snapshot.
            self._pending = False
            self._done = validated if not failed else None
            self._error = failed
            if failed:
                self._error_at = time.monotonic()


class LearnerPortraitService:
    """Read model only. Never calls activate_path or writes Tutor state."""

    def __init__(self, narrator: GeminiPortraitNarrator | None = None):
        self.narrator = narrator if narrator is not None else GeminiPortraitNarrator()

    def build(self, *, learning: object | None, mentor: Mapping[str, Any] | None = None,
              path_id: str = "", path_page: int = 0) -> dict[str, Any]:
        listing: Mapping[str, Any] = learning.learning_spaces() if learning is not None else {"paths": []}
        all_paths = list(listing.get("paths") or [])
        page_total = max(1, (len(all_paths) + MAX_PATHS - 1) // MAX_PATHS)
        page = max(0, min(page_total - 1, int(path_page)))
        paths = []
        for item in all_paths[page * MAX_PATHS:(page + 1) * MAX_PATHS]:
            if not isinstance(item, Mapping):
                continue
            paths.append({
                "path_id": str(item.get("path_id") or "")[:120],
                "domain_id": str(item.get("domain_id") or "")[:120],
                "title": _safe(item.get("title") or "Chưa đặt tên"),
                "goal": _safe(item.get("goal") or "", 220),
                "kp_count": _whole(item.get("kp_count")),
                "current_kp_title": _safe(item.get("current_kp_title")),
                "active": bool(item.get("active")),
            })
        available = {item["path_id"] for item in paths}
        selected = path_id if path_id in available else (str(listing.get("active_path_id") or "") if not path_id else "")
        if selected not in available:
            selected = paths[0]["path_id"] if paths else ""
        selection = next((x for x in paths if x["path_id"] == selected), None)
        insight = (learning.learning_insights(path_id=selected, max_kps=MAX_NODES)
                   if learning is not None and selected else {})
        nodes = []
        for point in list(insight.get("knowledge") or [])[:MAX_NODES]:
            if not isinstance(point, Mapping):
                continue
            signals = point.get("signals") if isinstance(point.get("signals"), Mapping) else {}
            nodes.append({
                "id": str(point.get("kp_id") or "")[:120],
                "title": _safe(point.get("title") or "Kiến thức"),
                "prerequisites": [str(x)[:120] for x in list(point.get("prerequisites") or [])[:12]],
                "mastery_stage": str(point.get("mastery_stage") or "unknown")[:48],
                "signal": str(signals.get("status") or "unassessed")[:48],
                "independent_correct": _whole(signals.get("independent_correct")),
                "independent_incorrect": _whole(signals.get("independent_incorrect")),
                "assisted_families": _whole(signals.get("assisted_families")),
                "review_due": point.get("review_due_at") is not None,
            })
        capabilities = []
        # Mentor is a DIFFERENT evidence model; do not combine its percentage with Tutor mastery.
        for group in list((mentor or {}).get("nhom_nang_luc") or [])[:8]:
            if not isinstance(group, Mapping):
                continue
            capabilities.append({"id": str(group.get("id") or "")[:80],
                                 "title": _safe(group.get("ten") or "Năng lực kỹ thuật"),
                                 "observed": bool(group.get("da_du_bang_chung")),
                                 "evidence_count": _whole(group.get("so_bang_chung")),
                                 "confidence": str(group.get("do_tin_cay") or "Chưa rõ")[:48]})
        evidence = []
        for item in paths[:12]:
            evidence.append({"ref": "path:" + item["path_id"], "kind": "learning_path",
                             "title": item["title"], "knowledge_count": item["kp_count"]})
        for item in nodes[:16]:
            evidence.append({"ref": "kp:" + selected + ":" + item["id"],
                             "kind": "learning_signal", "title": item["title"],
                             "signal": item["signal"], "correct": item["independent_correct"],
                             "incorrect": item["independent_incorrect"],
                             "assisted": item["assisted_families"]})
        for item in capabilities:
            if item["observed"]:
                evidence.append({"ref": "mentor:" + item["id"], "kind": "mentor_capability",
                                 "title": item["title"], "evidence_count": item["evidence_count"]})
        narrator = self.narrator.describe(evidence)
        result = {
            "schema": SCHEMA,
            "summary": _summary({"spaces": paths, "selection": selection or {}}),
            "spaces": paths,
            "spaces_total": len(all_paths),
            "spaces_page": page,
            "spaces_pages": page_total,
            "spaces_truncated": len(all_paths) > len(paths),
            "selection": {"path_id": selected, "title": selection["title"] if selection else "",
                          "kp_count": selection["kp_count"] if selection else 0},
            "knowledge": nodes,
            "knowledge_truncated": bool(selection and selection["kp_count"] > len(nodes)),
            "capabilities": capabilities,
            "gemini": narrator,
            "disclaimer": "Đã hỏi/đã tiếp xúc KHÔNG phải bằng chứng tự làm. Không biết ≠ chưa quan sát.",
        }
        # Never return a large UDP datagram: curriculum titles are model-generated.
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 49_000:
            result["knowledge"] = nodes[:20]
            result["knowledge_truncated"] = True
        return result


__all__ = ["LearnerPortraitService", "GeminiPortraitNarrator", "SCHEMA"]
