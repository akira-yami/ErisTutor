"""Durable Canvas cursor. UI navigation is never a conversational user turn."""
from __future__ import annotations

import threading

META_KEY = "learning_canvas_v1"


class CanvasFocusStore:
    def __init__(self, store, pack):
        self.store, self.pack = store, pack
        self._lock = threading.RLock()

    def snapshot(self) -> dict:
        raw = self.store.get_meta(META_KEY, None)
        if raw is None:
            return {"revision": 0, "focus_revision": 0, "group_id": self.pack.group_id, "node_id": self.pack.path.kp_ids[0],
                    "set_by": "eris", "pinned": False, "cause_id": "", "acknowledged_cause_id": "",
                    "suggested_node_id": "", "suggested_cause_id": ""}
        if isinstance(raw, dict):
            raw.setdefault("suggested_cause_id", "")
            raw.setdefault("focus_revision", raw.get("revision", 0))
        if (not isinstance(raw, dict) or type(raw.get("revision")) is not int or raw["revision"] < 0
                or type(raw.get("focus_revision")) is not int or not 0 <= raw["focus_revision"] <= raw["revision"]
                or raw.get("group_id") != self.pack.group_id or raw.get("node_id") not in self.pack.path.kp_ids
                or raw.get("set_by") not in {"user", "eris"} or type(raw.get("pinned")) is not bool
                or any(not isinstance(raw.get(k), str) for k in
                       ("cause_id", "acknowledged_cause_id", "suggested_node_id", "suggested_cause_id"))
                or raw["suggested_node_id"] not in ("", *self.pack.path.kp_ids)):
            raise ValueError("invalid_saved_canvas_focus")
        return dict(raw)

    def _change(self, base_revision, update):
        if type(base_revision) is not int or base_revision < 0:
            raise ValueError("invalid_base_revision")
        with self._lock:
            before = self.snapshot()
            if before["revision"] != base_revision:
                raise ValueError("stale_canvas_revision")
            after = {**before, **update, "revision": base_revision + 1}
            if "node_id" in update:
                after["focus_revision"] = base_revision + 1
            if not self.store.compare_and_set_meta_revision(META_KEY, base_revision, after):
                raise ValueError("stale_canvas_revision")
            return after

    def navigate(self, node_id, *, base_revision):
        self.pack.node(node_id)
        with self._lock:
            before = self.snapshot()
            if node_id == before["suggested_node_id"] and before.get("suggested_cause_id"):
                return self.accept_suggestion(node_id, base_revision=base_revision)
            return self._change(base_revision, {"node_id": node_id, "set_by": "user", "pinned": True,
                                                "cause_id": "", "acknowledged_cause_id": ""})

    def accept_suggestion(self, node_id, *, base_revision):
        with self._lock:
            before = self.snapshot()
            if node_id != before["suggested_node_id"] or not before.get("suggested_cause_id"):
                raise ValueError("unknown_canvas_suggestion")
            return self._change(base_revision, {"node_id": node_id, "set_by": "eris", "pinned": False,
                "cause_id": before["suggested_cause_id"], "acknowledged_cause_id": "",
                "suggested_node_id": "", "suggested_cause_id": ""})

    def eris_focus(self, node_id, *, base_revision, cause_id):
        """Data operation used by Root's Canvas capability; no speech authority."""
        self.pack.node(node_id)
        if not isinstance(cause_id, str) or not cause_id or len(cause_id) > 120:
            raise ValueError("invalid_canvas_cause")
        with self._lock:
            before = self.snapshot()
            if before["pinned"]:
                return self._change(base_revision, {"suggested_node_id": node_id, "suggested_cause_id": cause_id})
            return self._change(base_revision, {"node_id": node_id, "set_by": "eris", "cause_id": cause_id,
                                                "pinned": False, "acknowledged_cause_id": "", "suggested_node_id": "",
                                                "suggested_cause_id": ""})

    def acknowledge(self, *, base_revision, cause_id):
        with self._lock:
            before = self.snapshot()
            if not cause_id or before["set_by"] != "eris" or cause_id != before["cause_id"]:
                raise ValueError("unknown_canvas_cause")
            return self._change(base_revision, {"acknowledged_cause_id": cause_id})
