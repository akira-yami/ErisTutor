"""P3.6 — nhật ký học và vòng đời công việc Learning, độc lập với mastery.

Chỉ lưu metadata tối thiểu (môn/nút/trạng thái/mốc thời gian), không ghi
nội dung hội thoại hoặc screenshot. Mở nút KHÔNG đồng nghĩa đã hiểu bài.
Các bản ghi có CAS để UI, Root và Gemini không ghi đè lẫn nhau.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any

META_KEY = "learning_canvas_continuity_p36_v1"
STATES = ("exploring", "viewed", "practiced", "mastered")
ORDER = {name: index for index, name in enumerate(STATES)}
EVENT_LIMIT = 240
JOB_LIMIT = 24
NODE_LIMIT = 24 * 64


def _safe_text(value: Any, limit: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.strip() if ord(ch) >= 32)[:limit]


def _subject_recency(subject: dict) -> tuple[float, int]:
    return subject.get("updated_at", 0), subject.get("updated_revision", 0)


class LearningContinuity:
    """Timeline & state đọc được sau restart; KHÔNG ghi sang LearningCore evidence."""

    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._brief_cache = {}  # P3.7: vô hiệu khi có thay đổi; Root không đọc cả timeline.

    def _load(self):
        raw = self.store.get_meta(META_KEY)
        if raw is None:
            return {"schema": "eris.learning.continuity.p36", "revision": 0,
                    "subjects": {}, "nodes": {}, "events": [], "jobs": []}
        if (not isinstance(raw, dict) or raw.get("schema") != "eris.learning.continuity.p36"
                or type(raw.get("revision")) is not int or raw["revision"] < 0
                or not isinstance(raw.get("subjects"), dict) or not isinstance(raw.get("nodes"), dict)
                or not isinstance(raw.get("events"), list) or not isinstance(raw.get("jobs"), list)):
            raise ValueError("invalid_saved_learning_continuity")
        return raw

    def _apply(self, mutation):
        with self._lock:
            for _ in range(5):
                state = self._load()
                revision = state["revision"]
                result = mutation(state)
                state["revision"] = revision + 1
                if self.store.compare_and_set_meta_revision(META_KEY, revision, state):
                    self._brief_cache.clear()
                    return result
            raise ValueError("stale_learning_continuity")

    def visit(self, group_id: str, node_id: str, title: str, *, subject_title="", action="open",
              activate=True):
        """Ghi một lựa chọn chủ động; đã mở chỉ là exploring, không phải mastery."""
        if not all(isinstance(v, str) and 1 <= len(v) <= 120 for v in (group_id, node_id)):
            raise ValueError("invalid_continuity_target")
        title = _safe_text(title) or node_id[:120]
        subject_title = _safe_text(subject_title) or group_id[:120]
        if action not in {"created", "selected", "navigated", "opened", "resumed"}:
            raise ValueError("invalid_continuity_action")

        def update(state):
            now = round(time.time(), 3)
            subjects, nodes = state["subjects"], state["nodes"]
            subject = subjects.setdefault(group_id, {"title": subject_title, "visits": 0,
                                                    "last_node_id": node_id, "last_node_title": title,
                                                    "updated_at": now, "updated_revision": state["revision"] + 1})
            subject["title"] = subject_title
            if activate:
                subject.update(last_node_id=node_id, last_node_title=title, updated_at=now,
                               updated_revision=state["revision"] + 1)
                subject["visits"] = subject.get("visits", 0) + 1
            node_key = group_id + "/" + node_id
            previous = nodes.get(node_key, {})
            nodes[node_key] = {"group_id": group_id, "node_id": node_id, "title": title,
                               "status": previous.get("status", "exploring"),
                               "visited_at": now, "view_count": previous.get("view_count", 0) + (1 if activate else 0),
                               "estimated_seconds": previous.get("estimated_seconds", 0), "updated_at": now}
            if len(nodes) > NODE_LIMIT:
                oldest = min(nodes, key=lambda k: nodes[k].get("updated_at", 0))
                nodes.pop(oldest, None)
            if activate or action == "created":
                # Có lịch sử tạo môn, nhưng chưa coi là đã học khi Root chỉ chuẩn bị.
                tail = state["events"][-1] if state["events"] else {}
                if (tail.get("group_id") != group_id or tail.get("node_id") != node_id or
                        tail.get("action") != action or now - tail.get("at", 0) > 45):
                    state["events"].append({"action": action, "group_id": group_id,
                                            "node_id": node_id, "title": title, "at": now})
                    state["events"] = state["events"][-EVENT_LIMIT:]
        self._apply(update)

    def status(self, group_id: str, node_id: str, status: str, *, title="", subject_title="",
               verified=False):
        """Chỉ evidence có thẩm quyền mới có quyền ghi mastered."""
        if status not in STATES or (status == "mastered" and not verified):
            raise ValueError("unverified_mastery_forbidden")
        if not isinstance(group_id, str) or not isinstance(node_id, str) or not node_id:
            raise ValueError("invalid_continuity_target")

        def update(state):
            key = group_id + "/" + node_id
            existing = state["nodes"].get(key, {})
            before = existing.get("status", "exploring")
            if ORDER[status] <= ORDER.get(before, 0):
                return before
            now = round(time.time(), 3)
            subject = state["subjects"].setdefault(group_id, {"title": _safe_text(subject_title) or group_id,
                                        "visits": 0, "last_node_id": node_id,
                                        "last_node_title": _safe_text(title) or node_id, "updated_at": now})
            state["nodes"][key] = {**existing, "group_id": group_id, "node_id": node_id,
                "title": _safe_text(title) or existing.get("title", node_id), "status": status,
                "view_count": existing.get("view_count", 0), "visited_at": existing.get("visited_at", now),
                "estimated_seconds": existing.get("estimated_seconds", 0), "updated_at": now}
            state["events"].append({"action": "status_" + status, "group_id": group_id,
                                    "node_id": node_id, "title": _safe_text(title) or node_id, "at": now})
            state["events"] = state["events"][-EVENT_LIMIT:]
            subject["updated_at"] = now
            subject["updated_revision"] = state["revision"] + 1
            return status
        return self._apply(update)

    def add_study_time(self, group_id: str, node_id: str, *, seconds: int = 30):
        """Ước lượng thời gian khi tab đang tương tác; KHÔNG là bằng chứng đã hiểu."""
        if type(seconds) is not int or not 0 < seconds <= 30:
            raise ValueError("invalid_study_duration")
        def update(state):
            node = state["nodes"].get(group_id + "/" + node_id)
            if node is None:
                return False
            node["estimated_seconds"] = min(10000000, node.get("estimated_seconds", 0) + seconds)
            node["updated_at"] = round(time.time(), 3)
            return True
        return self._apply(update)

    def recover_interrupted_jobs(self):
        """Không để job queued cũ bị hiểu nhầm là worker vẫn chạy sau restart."""
        with self._lock:
            if not any(job["status"] == "queued" for job in self._load()["jobs"]):
                return
        def update(state):
            for job in state["jobs"]:
                if job["status"] == "queued":
                    job["status"] = "interrupted"
            return True
        self._apply(update)

    def queue_job(self, job_id: str, group_id: str, node_id: str, *, title: str, source="root", deferred=False):
        if not all(isinstance(v, str) and 1 <= len(v) <= 120 for v in (job_id, group_id, node_id)):
            raise ValueError("invalid_learning_job")
        def update(state):
            state["jobs"].append({"job_id": job_id, "group_id": group_id,
                "node_id": node_id, "title": _safe_text(title), "source": source,
                "status": "deferred" if deferred else "queued", "updated_at": round(time.time(), 3)})
            state["jobs"] = state["jobs"][-JOB_LIMIT:]
        self._apply(update)

    def start_deferred_job(self, group_id: str, node_id: str) -> dict | None:
        """Chỉ chuyển deferred -> queued sau khi đúng môn/nút được mở trong panel."""
        with self._lock:
            jobs = self._load()["jobs"]
            if not any(j["status"] == "deferred" and j["group_id"] == group_id
                       and j["node_id"] == node_id for j in jobs):
                return None
        def update(state):
            for job in reversed(state["jobs"]):
                if (job["status"] == "deferred" and job["group_id"] == group_id
                        and job["node_id"] == node_id):
                    job["status"] = "queued"
                    job["updated_at"] = round(time.time(), 3)
                    return copy.deepcopy(job)
            return None
        return self._apply(update)

    def brief(self, *, max_subjects=4, text_limit=52) -> dict:
        """Bản chiếu siêu ngắn của các môn; không có graph, bài, nhật ký, mastery.

        Cache tại process, vô hiệu khi có lượt visit/status/job mới.
        """
        max_subjects = max(1, min(5, int(max_subjects)))
        text_limit = max(12, min(80, int(text_limit)))
        key = (max_subjects, text_limit)
        with self._lock:
            if key not in self._brief_cache:
                state = self._load()
                recent = sorted(state["subjects"].values(),
                                key=_subject_recency, reverse=True)
                self._brief_cache[key] = {
                    "subjects": [{"title": _safe_text(s.get("title"), text_limit),
                                  "at": _safe_text(s.get("last_node_title"), text_limit)}
                                 for s in recent[:max_subjects]],
                    "other_subjects": max(0, len(recent) - max_subjects),
                }
            return copy.deepcopy(self._brief_cache[key])

    def finish_job(self, job_id: str, status: str):
        if status not in {"ready", "unavailable", "superseded", "unchanged"}:
            raise ValueError("invalid_learning_job_status")
        def update(state):
            for job in reversed(state["jobs"]):
                if job["job_id"] == job_id and job["status"] == "queued":
                    job["status"] = status
                    job["updated_at"] = round(time.time(), 3)
                    return copy.deepcopy(job)
            return None
        return self._apply(update)

    def snapshot(self, *, group_id: str = "") -> dict:
        with self._lock:
            state = copy.deepcopy(self._load())
            # P3.6: state packet có giới hạn 60KB. Chỉ gửi tiến độ của chủ đề
            # đang mở (hoặc các nút gần nhất), DB vẫn giữ đầy đủ lịch sử.
            candidates = [(key, value) for key, value in state["nodes"].items()
                          if not group_id or value.get("group_id") == group_id]
            candidates.sort(key=lambda item: item[1].get("updated_at", 0), reverse=True)
            # Projection nhẹ: UI chỉ cần status/lượt xem; dữ liệu đầy đủ ở SQLite.
            state["nodes"] = {key: {"status": value.get("status", "exploring"),
                                  "view_count": value.get("view_count", 0),
                                  "estimated_seconds": value.get("estimated_seconds", 0)}
                              for key, value in candidates[:64]}
            recent = sorted(state["subjects"].items(),
                            key=lambda kv: _subject_recency(kv[1]), reverse=True)
            state["recent_subjects"] = [{"group_id": key, **value} for key, value in recent[:12]]
            if group_id:
                # Tránh packet >60KB với 24 môn; Root/library vẫn đọc đầy đủ khi cần.
                state["subjects"] = dict(recent[:12])
            state["events"] = list(reversed(state["events"][-20:]))
            state["jobs"] = list(reversed(state["jobs"][-8:]))
            return state
