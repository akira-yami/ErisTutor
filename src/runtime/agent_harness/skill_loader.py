# -*- coding: utf-8 -*-
"""Load SKILL.md + tool IDs sau khi agent chủ động activate skill."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .capability_discovery import CapabilityDiscovery


class SkillLoader:
    def __init__(self, discovery: CapabilityDiscovery):
        self.discovery = discovery

    def activate_skill(self, skill_name: str) -> str:
        target = self.discovery.get(skill_name)
        if not target:
            return f"Error: Skill '{skill_name}' not found. Use search_capabilities first."
        skill_md_path = Path(target["path"]) / "SKILL.md"
        if not skill_md_path.exists():
            return f"Error: SKILL.md not found for '{skill_name}' at {skill_md_path}."
        try:
            content = skill_md_path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"Error reading SKILL.md for '{skill_name}': {exc}"
        tools = list(target.get("tools", []) or [])
        tool_text = ", ".join(tools) if tools else "(no native tools declared)"
        return (
            f"Successfully activated skill: {skill_name}\n"
            f"TOOLS: {tool_text}\n"
            f"--- SKILL INSTRUCTIONS ---\n{content}\n--- END OF INSTRUCTIONS ---"
        )

    def tools_for_skill(self, skill_name: str) -> list[str]:
        target = self.discovery.get(skill_name)
        return list(target.get("tools", []) or []) if target else []

    def tools_for_skills(self, skills: list[str] | tuple[str, ...]) -> list[str]:
        result: list[str] = []
        for skill in skills:
            for tool in self.tools_for_skill(skill):
                if tool not in result:
                    result.append(tool)
        return result


__all__ = ["SkillLoader"]
