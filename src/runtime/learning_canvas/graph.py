"""P3.5: đồ thị khám phá hé lộ dần; cache nhánh ẩn khác với mastery.

- P3 vẫn đọc được: các group cũ không có `prepared` tự coi như cache rỗng.
- Nhánh ẩn chỉ là gợi ý của Gemini: chưa hiển thị và không phải bài học đã xác minh.
- Mọi thay đổi dùng compare-and-set trên revision; nhánh AI không đổi lựa chọn của user.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import unicodedata
import uuid
from typing import Any

META_KEY = "learning_canvas_graphs_p3_v1"
MAX_GROUPS = 24
MAX_NODES = 64
MAX_GRAPH_BYTES = 35000
MAX_VISIBLE_SUGGESTIONS = 4
MAX_HIDDEN_SUGGESTIONS = 3
MAX_PREPARED_PARENTS = 16


def _title(value: Any, max_length: int = 120) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= max_length:
        raise ValueError("invalid_graph_title")
    value = value.strip()
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("invalid_graph_title")
    return value


def _summary(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 240 or any(ord(ch) < 32 and ch != "\n" for ch in value):
        raise ValueError("invalid_graph_summary")
    return value.strip()


def _id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9:_-]{1,120}", value):
        raise ValueError("invalid_graph_id")
    return value


def _key(title: str) -> str:
    return unicodedata.normalize("NFKC", title).casefold()


def _new_group_id(title: str) -> str:
    normalized = _key(title)
    return "topic-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8] + "-" + uuid.uuid4().hex[:8]


def _validated_nodes(nodes: list[dict], *, limit: int) -> list[dict]:
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= limit:
        raise ValueError("invalid_graph_nodes")
    output = []
    seen = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("invalid_graph_node")
        title = _title(node.get("title"))
        summary = _summary(node.get("summary", ""))
        if _key(title) in seen:
            raise ValueError("duplicate_graph_child")
        seen.add(_key(title))
        output.append({"title": title, "summary": summary})
    return output


class GraphWorkspace:
    """CRUD/đồ thị ẩn có CAS; không tạo bằng chứng học hay tác động focus Java."""

    def __init__(self, store, *, legacy_group_id="java-di"):
        self.store = store
        self.legacy_group_id = legacy_group_id
        self._lock = threading.RLock()

    def _load(self) -> dict:
        raw = self.store.get_meta(META_KEY, None)
        if raw is None:
            return {"schema": "eris.learning.graph.p3", "revision": 0,
                    "selected_group_id": self.legacy_group_id, "groups": []}
        if (not isinstance(raw, dict) or raw.get("schema") != "eris.learning.graph.p3"
                or type(raw.get("revision")) is not int or raw["revision"] < 0
                or not isinstance(raw.get("groups"), list) or len(raw["groups"]) > MAX_GROUPS):
            raise ValueError("invalid_saved_graph_workspace")
        return raw

    def snapshot(self) -> dict:
        with self._lock:
            state = self._load()
            selected = state["selected_group_id"]
            graph = next((group for group in state["groups"] if group["group_id"] == selected), None)
            if graph is not None:
                # Không truyền gợi ý ẩn tới web: chỉ gửi số lượng nhánh đã chuẩn bị.
                graph = {key: copy.deepcopy(value) for key, value in graph.items() if key != "prepared"}
                graph["prepared_counts"] = {key: len(value) for key, value in
                                            next(g for g in state["groups"] if g["group_id"] == selected).get("prepared", {}).items()
                                            if value}
            groups = []
            if not state.get("legacy_hidden", False):
                groups.append({"group_id": self.legacy_group_id, "title": "Java · Dependency Injection", "kind": "lesson"})
            groups.extend([{"group_id": item["group_id"], "title": item["title"],
                            "kind": "exploration", "node_count": len(item["nodes"])} for item in state["groups"]])
            return {"schema": "eris.learning.graph.p3", "revision": state["revision"],
                    "selected_group_id": selected,
                    "groups": groups,
                    "graph": graph}

    def inspect(self, group_id: str, node_id: str) -> dict:
        """Projection tối thiểu cho Gemini; không tiết lộ điểm số hay hội thoại."""
        with self._lock:
            state = self._load()
            group = next((g for g in state["groups"] if g["group_id"] == group_id), None)
            if not group:
                raise ValueError("unknown_graph_group")
            by_id = {node["id"]: node for node in group["nodes"]}
            if node_id not in by_id:
                raise ValueError("unknown_graph_node")
            trail = []
            cursor = node_id
            while cursor:
                node = by_id[cursor]
                trail.append(node["title"])
                cursor = node["parent_id"]
                if len(trail) > MAX_NODES:
                    raise ValueError("invalid_saved_graph_workspace")
            children = [node for node in group["nodes"] if node["parent_id"] == node_id]
            needed = [node for node in children[:MAX_VISIBLE_SUGGESTIONS]
                      if not group.get("prepared", {}).get(node["id"])
                      and not any(grandchild["parent_id"] == node["id"] for grandchild in group["nodes"])]
            return {"group_id": group_id, "node_id": node_id,
                    "topic": group["title"], "trail": list(reversed(trail)),
                    "node_summary": by_id[node_id].get("summary", "")[:240],
                    "children": [{"id": n["id"], "title": n["title"]} for n in children],
                    "needs_prefetch": [{"id": n["id"], "title": n["title"]} for n in needed],
                    "has_prepared": bool(group.get("prepared", {}).get(node_id))}

    def _write(self, state: dict, base_revision: int) -> None:
        if type(base_revision) is not int or base_revision < 0:
            raise ValueError("invalid_graph_revision")
        if base_revision != state["revision"]:
            raise ValueError("stale_graph_revision")
        for graph in state["groups"]:
            if len(json.dumps(graph, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_GRAPH_BYTES:
                raise ValueError("graph_payload_limit")
        state["revision"] += 1
        if not self.store.compare_and_set_meta_revision(META_KEY, base_revision, state):
            raise ValueError("stale_graph_revision")

    def create(self, title: str, *, base_revision: int | None = None, origin="user", follow=True) -> dict:
        title = _title(title)
        if origin not in {"user", "eris_proposed"}:
            raise ValueError("invalid_graph_origin")
        with self._lock:
            state = self._load()
            revision = state["revision"] if base_revision is None else base_revision
            if len(state["groups"]) >= MAX_GROUPS:
                raise ValueError("graph_group_limit")
            group_id = _new_group_id(title)
            root_id = group_id + ":root"
            group = {"group_id": group_id, "title": title, "selected_node_id": root_id,
                     "nodes": [{"id": root_id, "parent_id": "", "title": title, "summary": "", "origin": origin}]}
            state["groups"].append(group)
            if follow:
                state["selected_group_id"] = group_id
            self._write(state, revision)
            return copy.deepcopy(group)

    def delete(self, *, group_id: str = "", title: str = "", base_revision: int | None = None) -> bool:
        """Xoá một môn học/nhóm khám phá khỏi Learning Canvas theo group_id hoặc title."""
        with self._lock:
            state = self._load()
            revision = state["revision"] if base_revision is None else base_revision
            target_group = None
            norm_title = _key(title) if title else ""

            # Kiểm tra xem có trỏ tới legacy java-di không
            if (group_id and group_id == self.legacy_group_id) or (
                norm_title and any(k in norm_title for k in ("java-di", "java · dependency injection", "java dependency", "java depency"))
            ):
                state["legacy_hidden"] = True
                if state.get("selected_group_id") == self.legacy_group_id:
                    state["selected_group_id"] = state["groups"][0]["group_id"] if state["groups"] else ""
                self._write(state, revision)
                return True

            for g in state["groups"]:
                if (group_id and g["group_id"] == group_id) or (
                    norm_title and (_key(g["title"]) == norm_title or norm_title in _key(g["title"]))
                ):
                    target_group = g
                    break

            if target_group is None:
                return False

            state["groups"] = [g for g in state["groups"] if g["group_id"] != target_group["group_id"]]
            if state.get("selected_group_id") == target_group["group_id"]:
                state["selected_group_id"] = (
                    state["groups"][0]["group_id"]
                    if state["groups"]
                    else (self.legacy_group_id if not state.get("legacy_hidden") else "")
                )

            self._write(state, revision)
            return True

    def _promote(self, group: dict, node_id: str) -> list[dict]:
        """Mở cache khi user chọn nhánh; không tự hiện cả cây."""
        prepared = group.get("prepared", {}).pop(node_id, [])
        if not prepared:
            return []
        siblings = {_key(n["title"]) for n in group["nodes"] if n["parent_id"] == node_id}
        additions = []
        for entry in prepared:
            if len(group["nodes"]) + len(additions) >= MAX_NODES:
                break
            if _key(entry["title"]) in siblings:
                continue
            siblings.add(_key(entry["title"]))
            additions.append({"id": group["group_id"] + ":" + uuid.uuid4().hex[:12],
                              "parent_id": node_id, "title": entry["title"],
                              "summary": entry["summary"], "origin": "gemini_draft"})
        group["nodes"].extend(additions)
        return additions

    def select(self, group_id: str, *, base_revision: int | None = None, node_id: str = "") -> list[dict]:
        group_id = _id(group_id)
        with self._lock:
            state = self._load()
            revision = state["revision"] if base_revision is None else base_revision
            additions = []
            if group_id != self.legacy_group_id:
                group = next((item for item in state["groups"] if item["group_id"] == group_id), None)
                if group is None:
                    raise ValueError("unknown_graph_group")
                if node_id:
                    if node_id not in {node["id"] for node in group["nodes"]}:
                        raise ValueError("unknown_graph_node")
                    group["selected_node_id"] = node_id
                else:
                    node_id = group["selected_node_id"]
                additions = self._promote(group, node_id)
            elif node_id:
                raise ValueError("unknown_graph_node")
            state["selected_group_id"] = group_id
            self._write(state, revision)
            return additions

    def expand(self, group_id: str, parent_id: str, nodes: list[dict], *, base_revision: int | None = None,
               origin: str = "user", follow=True) -> list[dict]:
        group_id, parent_id = _id(group_id), _id(parent_id)
        if origin not in {"user", "eris_proposed"}:
            raise ValueError("invalid_graph_origin")
        clean = _validated_nodes(nodes, limit=8)
        with self._lock:
            state = self._load()
            revision = state["revision"] if base_revision is None else base_revision
            group = next((item for item in state["groups"] if item["group_id"] == group_id), None)
            if group is None:
                raise ValueError("unknown_graph_group")
            if parent_id not in {node["id"] for node in group["nodes"]}:
                raise ValueError("unknown_graph_parent")
            if len(group["nodes"]) + len(clean) > MAX_NODES:
                raise ValueError("graph_node_limit")
            siblings = {_key(node["title"]) for node in group["nodes"] if node["parent_id"] == parent_id}
            if {_key(n["title"]) for n in clean} & siblings:
                raise ValueError("duplicate_graph_child")
            additions = [{"id": group_id + ":" + uuid.uuid4().hex[:12],
                          "parent_id": parent_id, "title": n["title"],
                          "summary": n["summary"], "origin": origin} for n in clean]
            group["nodes"].extend(additions)
            # Nếu gợi ý Gemini chưa lộ, loại các mục trùng với nhánh người dùng thêm.
            cache = group.get("prepared", {}).get(parent_id, [])
            if cache:
                group["prepared"][parent_id] = [n for n in cache if _key(n["title"]) not in
                                                    {_key(add["title"]) for add in additions}]
            if follow:
                group["selected_node_id"] = additions[0]["id"]
                state["selected_group_id"] = group_id
            self._write(state, revision)
            return copy.deepcopy(additions)

    def apply_gemini(self, group_id: str, node_id: str, payload: dict) -> bool:
        """Một kết quả Gemini: mở 3–4 nhánh trước mắt, chỉ lưu tầng sau ở cache ẩn.

        AI không được tự đổi node đang chọn, không ghi đè dữ liệu/nhánh người dùng.
        Retry CAS một lần nếu Root/UI vừa sửa graph ở phiên khác.
        """
        if not isinstance(payload, dict):
            raise ValueError("invalid_gemini_graph")
        group_id, node_id = _id(group_id), _id(node_id)
        for attempt in range(2):
            with self._lock:
                state = self._load()
                group = next((g for g in state["groups"] if g["group_id"] == group_id), None)
                if group is None or node_id not in {n["id"] for n in group["nodes"]}:
                    return False
                existing = [n for n in group["nodes"] if n["parent_id"] == node_id]
                prepared = group.setdefault("prepared", {})
                changed = False
                if not existing:
                    proposals = payload.get("branches")
                    # Validate toàn bộ trước khi thêm, không nhận output tùy tiện từ AI.
                    cleaned = _validated_nodes(proposals, limit=MAX_VISIBLE_SUGGESTIONS)
                    titles = set()
                    for source, entry in zip(proposals, cleaned):
                        if _key(entry["title"]) in titles:
                            raise ValueError("duplicate_graph_child")
                        titles.add(_key(entry["title"]))
                        child_id = group_id + ":" + uuid.uuid4().hex[:12]
                        if len(group["nodes"]) >= MAX_NODES:
                            break
                        group["nodes"].append({"id": child_id, "parent_id": node_id,
                                                **entry, "origin": "gemini_draft"})
                        hidden = source.get("next", [])
                        if hidden:
                            prepared[child_id] = _validated_nodes(hidden, limit=MAX_HIDDEN_SUGGESTIONS)
                        changed = True
                else:
                    # Khi nhánh hiện tại đã có con, chỉ bổ sung cache ẩn cho các con.
                    proposals = payload.get("targets")
                    if not isinstance(proposals, list) or len(proposals) > MAX_VISIBLE_SUGGESTIONS:
                        raise ValueError("invalid_gemini_graph")
                    known = {n["id"] for n in existing[:MAX_VISIBLE_SUGGESTIONS]}
                    for entry in proposals:
                        if not isinstance(entry, dict) or entry.get("id") not in known:
                            raise ValueError("invalid_gemini_graph")
                        child_id = entry["id"]
                        if child_id in prepared or any(n["parent_id"] == child_id for n in group["nodes"]):
                            continue
                        hidden = _validated_nodes(entry.get("next"), limit=MAX_HIDDEN_SUGGESTIONS)
                        prepared[child_id] = hidden
                        changed = True
                if not changed:
                    return False
                # LRU đơn giản, tránh cache ẩn phát triển không giới hạn.
                while len(prepared) > MAX_PREPARED_PARENTS:
                    prepared.pop(next(iter(prepared)))
                try:
                    self._write(state, state["revision"])
                    return True
                except ValueError as exc:
                    if str(exc) == "graph_payload_limit":
                        return False
                    if str(exc) != "stale_graph_revision" or attempt:
                        raise
        return False
