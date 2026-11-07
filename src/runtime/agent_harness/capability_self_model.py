# -*- coding: utf-8 -*-
"""Runtime capability self-model for Eris.

The registry says which concrete capabilities exist.  This module answers the
more useful operational question: what can Eris do *now*, how long will it take,
how risky/reversible is it, does it require a workspace, and should Eris do it
natively or route it through the dedicated coding subbrain/Agent Harness.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import re
import unicodedata
from typing import Any

from actions.capability_registry import iter_public_capabilities


@dataclass(frozen=True)
class CapabilityProfile:
    capability_id: str
    description: str
    effect: str
    risk_level: str
    health: str
    health_reason: str
    available: bool
    latency_class: str
    estimated_time: str
    cost_class: str
    executor: str
    workspace_required: bool
    reversible: bool
    can_modify: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilitySelfAssessment:
    lane: str = "conversation"
    reason: str = "normal_chat"
    confidence: float = 0.5
    estimated_time: str = ""
    executor: str = "eris"
    can_modify: bool = False
    matched_capabilities: tuple[str, ...] = ()
    matched_skills: tuple[str, ...] = ()
    clarification: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["matched_capabilities"] = list(self.matched_capabilities)
        payload["matched_skills"] = list(self.matched_skills)
        return payload


def _fold(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or "").lower())
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = raw.replace("đ", "d")
    return re.sub(r"\s+", " ", raw).strip()


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


_INSTANT_CUES = {
    "get_now_playing": ("dang nghe", "nghe nhac gi", "bai gi", "now playing", "media dang phat"),
    "get_active_application": ("app dang mo", "app dang dung", "cua so dang", "active app"),
    "read_visible_ui_text": ("doc chu man hinh", "chu tren man hinh", "ui text"),
    "read_current_error": ("loi hien tai", "traceback hien tai", "error hien tai"),
    "inspect_runtime_log": ("doc log", "xem log", "runtime log"),
    "inspect_selected_file": ("file dang mo", "file dang chon", "selected file"),
    "get_project_status": ("git status", "project status", "trang thai project"),
    "launch_app": ("mo chrome", "mo app", "mo ung dung", "launch"),
    "focus_window": ("focus window", "dua cua so", "chuyen sang cua so"),
    "set_volume": ("am luong", "volume", "mute"),
    "play_pause_media": ("pause nhac", "phat nhac", "next bai", "previous bai"),
    "copy_text": ("copy", "clipboard"),
    "open_url": ("mo link", "mo url", "open url"),
    "get_weather": ("thoi tiet", "weather"),
    "get_local_time": ("may gio", "gio may", "ngay may", "local time"),
}

_TECH_TARGETS = (
    "repo", "code", "source", "file", "module", "class", "function", "tool", "skill",
    "agent", "langgraph", "gemini", "router", "routing", "backend", "frontend", "godot",
    "python", "c#", "test", "bug", "loi", "panel", "ui", "runtime", "capability", "memory",
    "tts", "live2d", "git", "architecture", "kien truc",
)
_LONG_VERBS = (
    "sua", "fix", "debug", "refactor", "trien khai", "implement", "viet code", "patch",
    "chay test", "build", "compile", "toi uu", "optimize", "lam lai", "cap nhat code",
    "them chuc nang", "bo chuc nang", "lam tiep", "execute", "thuc thi",
)
_LONG_SCOPE = (
    "toan bo", "full", "end to end", "e2e", "nhieu file", "nhieu buoc", "lau dai", "dai han",
    "tu dong", "tu sua", "tu debug", "sua luon", "lam luon", "nghien cuu ky roi sua",
)
_CONSULT_VERBS = (
    "soi", "xem ky", "review", "danh gia", "kiem tra", "nghien cuu", "phan tich", "tim root cause",
    "tai sao", "vi sao", "co can", "hop ly khong", "can can", "kien truc nay",
)
_BROAD_QUALITY = (
    "ngon hon", "tot hon", "xin hon", "elegant hon", "dep hon", "thong minh hon", "toi uu hon",
    "hoan thien hon", "cai thien", "nang cap",
)
_SPECIFIC = (
    ".py", ".cs", ".gd", ".json", "src/", "runtime/", "actions/", "tests/", "traceback",
    "timeout", "latency", "khong chay", "fail", "die", "capability_", "project_", "get_", "set_",
)


class CapabilitySelfModel:
    """Refreshable, health-aware projection of Eris' operational reach."""

    def __init__(self, *, harness: Any = None, harness_ready: bool | None = None) -> None:
        self.harness = harness
        self._harness_ready_override = harness_ready
        self._native: dict[str, Any] = {}
        self._profiles: dict[str, CapabilityProfile] = {}
        self._skills: dict[str, dict[str, Any]] = {}
        self._runtime_revision = 0
        self.refresh()

    def refresh(self) -> None:
        try:
            from actions.capability_runtime import runtime_capability_snapshot
            runtime = runtime_capability_snapshot(refresh=False)
        except Exception:
            runtime = {}
        statuses = dict(runtime.get("statuses", {}) or {}) if isinstance(runtime, dict) else {}
        self._runtime_revision = int(runtime.get("revision", 0) or 0) if isinstance(runtime, dict) else 0

        capabilities = tuple(iter_public_capabilities(include_unavailable=True))
        self._native = {cap.id: cap for cap in capabilities if cap.callable}
        self._profiles = {}
        for cap_id, cap in self._native.items():
            status = statuses.get(cap_id) if isinstance(statuses.get(cap_id), dict) else {}
            available = bool(status.get("available", getattr(cap, "live_available", False)))
            reason = str(status.get("reason", "") or "")
            self._profiles[cap_id] = self._build_profile(cap, available=available, reason=reason)

        discovery = getattr(self.harness, "discovery", None)
        raw_skills = list(getattr(discovery, "capabilities", []) or [])
        self._skills = {
            str(item.get("name", "") or ""): dict(item)
            for item in raw_skills
            if isinstance(item, dict) and str(item.get("name", "") or "").strip()
        }

    @property
    def harness_ready(self) -> bool:
        if self._harness_ready_override is not None:
            return bool(self._harness_ready_override)
        return self.harness is not None

    def profile(self, capability_id: str) -> CapabilityProfile | None:
        return self._profiles.get(str(capability_id or "").strip())

    def capability_profiles(self, *, include_unavailable: bool = True) -> tuple[CapabilityProfile, ...]:
        values = list(self._profiles.values())
        if not include_unavailable:
            values = [item for item in values if item.available]
        return tuple(sorted(values, key=lambda item: (not item.available, item.latency_class, item.capability_id)))

    def skill_profiles(self) -> tuple[dict[str, Any], ...]:
        results: list[dict[str, Any]] = []
        harness_specs = dict(getattr(self.harness, "tool_specs", {}) or {}) if self.harness is not None else {}
        for name, item in self._skills.items():
            tools = [str(x) for x in item.get("tools", []) if str(x).strip()]
            tool_health: list[bool] = []
            for tool in tools:
                profile = self._profiles.get(tool)
                if profile is not None:
                    tool_health.append(profile.available)
                elif tool in harness_specs:
                    tool_health.append(True)
            available_tools = sum(1 for value in tool_health if value)
            health = "ready" if tools and available_tools == len(tools) else "degraded" if available_tools else "unavailable"
            if not self.harness_ready:
                health = "unavailable"
            results.append({
                "name": name,
                "description": str(item.get("description", "") or ""),
                "mode": str(item.get("mode", "long") or "long"),
                "latency": str(item.get("latency", "minutes") or "minutes"),
                "workspace_required": bool(item.get("workspace_required", any(tool.startswith("project_") for tool in tools))),
                "risk_level": str(item.get("risk_level", "medium") or "medium"),
                "tools": tools,
                "available_tools": available_tools,
                "tool_count": len(tools),
                "health": health,
                "executor": "gemini/langgraph",
            })
        return tuple(sorted(results, key=lambda item: (item["health"] != "ready", item["name"])))

    def catalog(self, query: str = "", *, include_unavailable: bool = True, limit: int = 40) -> dict[str, Any]:
        folded = _fold(query)
        caps = []
        for profile in self.capability_profiles(include_unavailable=include_unavailable):
            if folded:
                haystack = _fold(f"{profile.capability_id} {profile.description} {profile.effect} {profile.executor}")
                if not all(token in haystack for token in folded.split() if len(token) > 1):
                    continue
            caps.append(profile.to_dict())
        skills = []
        for item in self.skill_profiles():
            if folded:
                haystack = _fold(" ".join([item["name"], item["description"], *item["tools"]]))
                if not any(token in haystack for token in folded.split() if len(token) > 1):
                    continue
            skills.append(dict(item))
        return {
            "runtime_revision": self._runtime_revision,
            "harness_ready": self.harness_ready,
            "capabilities": caps[: max(1, int(limit))],
            "skills": skills[: max(1, int(limit))],
        }

    def instant_capabilities(self) -> tuple[str, ...]:
        return tuple(
            cap_id for cap_id in _INSTANT_CUES
            if (self._profiles.get(cap_id) is not None and self._profiles[cap_id].available)
        )

    def long_skills(self) -> tuple[str, ...]:
        if not self.harness_ready:
            return ()
        return tuple(item["name"] for item in self.skill_profiles() if item["health"] != "unavailable")

    def prompt_summary(self) -> str:
        self.refresh()
        ready = [p for p in self._profiles.values() if p.available]
        unavailable = [p for p in self._profiles.values() if not p.available]
        instant = self.instant_capabilities()
        skills = self.long_skills()
        unavailable_text = ", ".join(f"{p.capability_id}:{p.health_reason or 'unavailable'}" for p in unavailable[:6]) or "none"
        return (
            "CAPABILITY SELF MODEL: "
            f"runtime_revision={self._runtime_revision}; ready={len(ready)} unavailable={len(unavailable)}; "
            f"instant_native=[{', '.join(instant[:16]) or 'none'}]; "
            f"long_executor={'gemini/langgraph' if self.harness_ready else 'unavailable'}; "
            f"long_skills=[{', '.join(skills[:12]) or 'none'}]; "
            f"unavailable_reasons=[{unavailable_text}]. "
            "Prefer healthy low-latency native capabilities for one-step work. Delegate multi-step technical work to the long executor. "
            "A project capability requires a locked workspace; do not claim a capability is usable when its health says unavailable."
        )

    def assess(self, user_text: str) -> CapabilitySelfAssessment:
        self.refresh()
        folded = _fold(user_text)
        if not folded:
            return CapabilitySelfAssessment()

        matched_instant: list[str] = []
        for cap_id, cues in _INSTANT_CUES.items():
            profile = self._profiles.get(cap_id)
            if profile is None or not profile.available:
                continue
            if _contains_any(folded, cues):
                matched_instant.append(cap_id)
        if matched_instant:
            profiles = [self._profiles[x] for x in matched_instant]
            return CapabilitySelfAssessment(
                lane="instant",
                reason="healthy_single_step_capability",
                confidence=0.94,
                estimated_time=max((p.estimated_time for p in profiles), key=len, default="< 3s"),
                executor="eris-native",
                can_modify=any(p.can_modify for p in profiles),
                matched_capabilities=tuple(matched_instant[:6]),
            )

        has_target = _contains_any(folded, _TECH_TARGETS)
        has_long_verb = _contains_any(folded, _LONG_VERBS)
        has_long_scope = _contains_any(folded, _LONG_SCOPE)
        has_consult = _contains_any(folded, _CONSULT_VERBS)
        broad = _contains_any(folded, _BROAD_QUALITY)
        specific = _contains_any(folded, _SPECIFIC) or bool(
            re.search(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\.(?:py|cs|gd|json|md|txt)\b", str(user_text or ""))
        )

        matched_skills = self._match_skills(folded)
        if has_target and broad and not specific and not has_long_scope:
            return CapabilitySelfAssessment(
                lane="clarify",
                reason="materially_branching_long_task",
                confidence=0.87,
                estimated_time="unknown until clarified",
                executor="eris",
                matched_skills=matched_skills,
                clarification=(
                    "Chốt nhánh chính trước khi giao coding subbrain: hành vi/chức năng cần đạt, kiến trúc bên trong, "
                    "hiệu năng, hay giao diện/trải nghiệm. Nếu hội thoại đã có các phương án cụ thể thì hỏi bằng "
                    "2-4 lựa chọn đó, không hỏi chung chung."
                ),
            )

        if has_target and (has_long_verb or has_long_scope):
            if not self.harness_ready:
                return CapabilitySelfAssessment(
                    lane="unavailable",
                    reason="long_executor_unavailable",
                    confidence=0.95,
                    estimated_time="unavailable",
                    executor="official-api-coding/langgraph",
                    can_modify=True,
                    matched_skills=matched_skills,
                )
            return CapabilitySelfAssessment(
                lane="long_execution",
                reason="multi_step_technical_execution",
                confidence=0.90 if specific or has_long_scope else 0.80,
                estimated_time="minutes",
                executor="official-api-coding/langgraph",
                can_modify=True,
                matched_skills=matched_skills,
            )

        if has_target and has_consult:
            if not self.harness_ready:
                return CapabilitySelfAssessment(
                    lane="conversation",
                    reason="consult_executor_unavailable",
                    confidence=0.72,
                    executor="eris",
                    matched_skills=matched_skills,
                )
            return CapabilitySelfAssessment(
                lane="consult",
                reason="technical_backstage_consult",
                confidence=0.80 if specific else 0.72,
                estimated_time="seconds to minutes",
                executor="official-api-coding/langgraph",
                can_modify=False,
                matched_skills=matched_skills,
            )

        return CapabilitySelfAssessment(
            lane="conversation",
            reason="normal_chat",
            confidence=0.66,
            executor="eris",
            matched_skills=matched_skills,
        )

    def _build_profile(self, cap: Any, *, available: bool, reason: str) -> CapabilityProfile:
        cap_id = str(getattr(cap, "id", "") or "")
        effect = str(getattr(cap, "effect", "inspect") or "inspect").lower()
        risk = str(getattr(cap, "risk_level", "low") or "low").lower()
        latency_class, estimated = _latency_for(cap_id, effect)
        workspace_required = cap_id.startswith("project_")
        reversible = effect in {"observe", "inspect", "read"} or cap_id in {
            "project_apply_patch", "project_rollback", "copy_text", "set_volume", "play_pause_media",
        }
        return CapabilityProfile(
            capability_id=cap_id,
            description=str(getattr(cap, "description_vi", "") or ""),
            effect=effect,
            risk_level=risk,
            health="ready" if available else "unavailable",
            health_reason=reason or ("runtime_ready" if available else "runtime_unavailable"),
            available=bool(available),
            latency_class=latency_class,
            estimated_time=estimated,
            cost_class="low" if latency_class == "instant" else "medium" if latency_class in {"fast", "medium"} else "high",
            executor="eris-native",
            workspace_required=workspace_required,
            reversible=reversible,
            can_modify=effect in {"write", "control"},
        )

    def _match_skills(self, folded_query: str) -> tuple[str, ...]:
        if not self._skills:
            return ()
        query_tokens = set(re.findall(r"[a-z0-9_\-]+", folded_query))
        ranked: list[tuple[int, str]] = []
        for name, item in self._skills.items():
            haystack = _fold(" ".join([
                name,
                str(item.get("description", "") or ""),
                *[str(x) for x in item.get("keywords", []) if str(x).strip()],
            ]))
            tokens = set(re.findall(r"[a-z0-9_\-]+", haystack))
            score = len(query_tokens & tokens)
            if score:
                ranked.append((score, name))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]))
        return tuple(name for _score, name in ranked[:5])


def _latency_for(capability_id: str, effect: str) -> tuple[str, str]:
    if capability_id == "project_execute_task":
        return "long", "minutes"
    if capability_id in {"project_run_tests", "project_run_command", "project_package_patch"}:
        return "medium", "seconds to minutes"
    if capability_id.startswith("project_") or capability_id.startswith("workspace_"):
        return "fast", "1-10s"
    if effect in {"observe", "inspect", "read"}:
        return "instant", "< 3s"
    return "fast", "< 10s"


__all__ = ["CapabilityProfile", "CapabilitySelfAssessment", "CapabilitySelfModel"]
