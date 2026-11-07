# -*- coding: utf-8 -*-
"""Progressive capability discovery cho Unified Agent Harness.

Search chỉ lộ metadata nhẹ. Schema/tool cụ thể chỉ được lộ sau activate_skill,
đúng tinh thần progressive disclosure thay vì nhét cả kho dụng cụ vào prompt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-À-ỹ]+", re.UNICODE)


class CapabilityDiscovery:
    def __init__(self, powers_root: str):
        self.powers_root = Path(powers_root)
        self.capabilities = self._scan_capabilities()

    def refresh(self) -> None:
        self.capabilities = self._scan_capabilities()

    def _scan_capabilities(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not self.powers_root.exists():
            return results
        for root, _dirs, files in os.walk(self.powers_root):
            if "metadata.json" not in files:
                continue
            meta_path = Path(root) / "metadata.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            name = str(meta.get("name", "") or "").strip()
            description = str(meta.get("description", "") or "").strip()
            if not name or not description:
                continue
            results.append({
                "name": name,
                "description": description,
                "keywords": [str(x).strip() for x in meta.get("keywords", []) if str(x).strip()],
                "tools": [str(x).strip() for x in meta.get("tools", []) if str(x).strip()],
                # Operational metadata feeds Eris' self-model and the Agent Console,
                # while search still exposes only name+description until activation.
                "mode": str(meta.get("mode", "long") or "long"),
                "latency": str(meta.get("latency", "minutes") or "minutes"),
                "workspace_required": bool(meta.get("workspace_required", False)),
                "risk_level": str(meta.get("risk_level", "medium") or "medium"),
                "reversible": bool(meta.get("reversible", True)),
                "path": str(Path(root)),
            })
        return sorted(results, key=lambda item: item["name"])

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {token.lower() for token in _TOKEN_RE.findall(value or "") if len(token) > 1}

    def search_matches(self, query: str = "", *, limit: int = 6) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return [dict(cap) for cap in self.capabilities[: max(1, limit)]]
        q_tokens = self._tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for cap in self.capabilities:
            haystack = " ".join([cap["name"], cap["description"], *cap.get("keywords", [])])
            h_tokens = self._tokens(haystack)
            overlap = len(q_tokens & h_tokens)
            phrase_bonus = 3.0 if query.lower() in haystack.lower() else 0.0
            name_bonus = 1.5 if any(tok in cap["name"].lower() for tok in q_tokens) else 0.0
            score = float(overlap) + phrase_bonus + name_bonus
            if score > 0:
                scored.append((score, cap))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
        return [dict(cap) for _score, cap in scored[: max(1, limit)]]

    def search(self, query: str = "") -> str:
        matches = self.search_matches(query)
        if not matches:
            return f"No skills found matching '{query}'. Try a broader capability description."
        lines = [f"Found {len(matches)} matching skills:"]
        for cap in matches:
            # Không lộ tool/schema ở bước search. Đó là việc của activate_skill.
            lines.append(f"- {cap['name']}: {cap['description']}")
        lines.append("\nUse activate_skill(skill_name) to load its instructions and exact tool schemas.")
        return "\n".join(lines)

    def get(self, skill_name: str) -> dict[str, Any] | None:
        target = str(skill_name or "").strip()
        for cap in self.capabilities:
            if cap["name"] == target:
                return dict(cap)
        return None


__all__ = ["CapabilityDiscovery"]
