from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from .contracts import Belief, CognitiveEpisode, LearningUpdate


SCHEMA_VERSION = 2


def default_epistemic_path() -> Path:
    configured = os.getenv("ERIS_EPISTEMIC_V2_PATH", "").strip()
    return Path(configured) if configured else Path("memory_core") / "epistemic_v2.json"


class EpistemicStore:
    """Atomic persistence for V2 state. V1 files are never imported or mutated."""

    def __init__(self, path: str | os.PathLike[str] | None = None, *, max_episodes: int = 500, mode: str = "authoritative") -> None:
        self._memory_only = str(path or "") == ":memory:"
        self.path = Path("epistemic_v2.memory") if self._memory_only else (Path(path) if path else default_epistemic_path())
        self.max_episodes = max(20, int(max_episodes))
        if str(mode).strip().lower() != "authoritative":
            raise ValueError("V6 supports authoritative epistemic storage only")
        self.mode = "authoritative"
        self.beliefs: dict[str, Belief] = {}
        self.episodes: list[CognitiveEpisode] = []
        self.projections: dict[str, dict[str, Any]] = {}
        self.procedural_policies: dict[str, dict[str, Any]] = {}
        self.calibration: dict[str, dict[str, float]] = {}
        self.pending_hypotheses: list[dict[str, Any]] = []
        self.learning_log: list[LearningUpdate] = []
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        if self._memory_only:
            return
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return
        if not isinstance(raw, Mapping) or int(raw.get("schema_version", 0) or 0) != SCHEMA_VERSION:
            return
        with self._lock:
            self.beliefs = {
                belief.id: belief
                for item in raw.get("beliefs", ())
                if isinstance(item, Mapping)
                for belief in (Belief.from_mapping(item),)
            }
            self.episodes = [CognitiveEpisode.from_mapping(item) for item in raw.get("episodes", ()) if isinstance(item, Mapping)]
            self.mode = "authoritative"
            self.projections = {
                str(key): dict(value)
                for key, value in dict(raw.get("projections") or {}).items()
                if isinstance(value, Mapping)
            }
            self.procedural_policies = dict(raw.get("procedural_policies") or {})
            self.calibration = dict(raw.get("calibration") or {})
            self.pending_hypotheses = [dict(item) for item in raw.get("pending_hypotheses", ()) if isinstance(item, Mapping)]
            self.learning_log = [LearningUpdate.from_mapping(item) for item in raw.get("learning_log", ()) if isinstance(item, Mapping)]
            self.created_at = float(raw.get("created_at") or time.time())
            self.updated_at = float(raw.get("updated_at") or self.created_at)

    def save(self) -> None:
        with self._lock:
            self.updated_at = time.time()
            if self._memory_only:
                return
            payload = {
                "schema_version": SCHEMA_VERSION,
                "mode": self.mode,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "beliefs": [item.to_dict() for item in self.beliefs.values()],
                "episodes": [item.to_dict() for item in self.episodes[-self.max_episodes :]],
                "projections": self.projections,
                "procedural_policies": self.procedural_policies,
                "calibration": self.calibration,
                "pending_hypotheses": self.pending_hypotheses[-100:],
                "learning_log": [item.to_dict() for item in self.learning_log[-1000:]],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def add_episode(self, episode: CognitiveEpisode) -> None:
        with self._lock:
            self.episodes = [item for item in self.episodes if item.id != episode.id]
            self.episodes.append(episode)
            self.episodes = self.episodes[-self.max_episodes :]
            self.save()

    def open_episode(self) -> CognitiveEpisode | None:
        with self._lock:
            return next((item for item in reversed(self.episodes) if item.status == "open"), None)
