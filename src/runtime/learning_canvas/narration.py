"""Validated narration IDs and playback read-model. Never creates speech."""
from __future__ import annotations

import threading
import uuid


def valid_parts(pack, scope, parts):
    if not scope or not parts:
        return ()
    try:
        allowed = {b["id"] for b in pack.node(scope.get("node_id"))["blocks"]}
    except ValueError:
        return ()
    return tuple({"block_id": p["block_id"], "say": p["say"][:1200]}
                 for p in parts[:6] if isinstance(p, dict) and p.get("block_id") in allowed
                 and isinstance(p.get("say"), str) and p["say"].strip())


class CanvasPlayback:
    def __init__(self, pack):
        self.pack = pack
        self.stream_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._seq = 0
        self._active = None
        self._generations = {}
        self._last = None

    def register(self, turn_id, generation, scope, blocks, *, session_id="foreground:primary"):
        with self._lock:
            if not scope or generation < self._generations.get(session_id, 0):
                return None
            allowed = {b["id"] for b in self.pack.node(scope["node_id"])["blocks"]}
            blocks = {int(k): v if v in allowed else "" for k, v in blocks.items()}
            if not blocks:
                return None
            self._generations[session_id] = generation
            self._active = {"turn_id": turn_id, "generation": generation, "session_id": session_id,
                            "node_id": scope["node_id"], "focus_revision": scope["focus_revision"], "blocks": blocks}
            return self._packet("queued", min(blocks), "", final=False)

    def _packet(self, event_type, segment, block, *, final):
        self._seq += 1
        result = {"type": "canvas_highlight" if event_type != "queued" else "canvas_playback",
                  "stream_id": self.stream_id,
                  **{k: v for k, v in self._active.items() if k != "blocks"},
                  "event_type": event_type, "segment_id": segment, "block_id": block,
                  "seq": self._seq, "final": final}
        self._last = result
        return result

    def event(self, event_type, turn_id, segment):
        with self._lock:
            active = self._active
            if not active or active["turn_id"] != turn_id or segment not in active["blocks"]:
                return None
            if event_type not in {"audio_start", "audio_done", "audio_failed", "audio_cancelled"}:
                return None
            final = event_type in {"audio_failed", "audio_cancelled"} or (
                event_type == "audio_done" and segment == max(active["blocks"]))
            packet = self._packet(event_type, segment, active["blocks"][segment] if event_type == "audio_start" else "", final=final)
            if final:
                self._active = None
            return packet

    def snapshot(self):
        with self._lock:
            return dict(self._last) if self._active and self._last else None

    def finish(self, turn_id, *, successful=False, cancelled=False):
        with self._lock:
            if not self._active or self._active["turn_id"] != turn_id:
                return None
            return self.event("audio_done" if successful else "audio_cancelled" if cancelled else "audio_failed",
                              turn_id, max(self._active["blocks"]))
