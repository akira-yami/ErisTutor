# -*- coding: utf-8 -*-
"""Declarative profiles for long-running engagement detection.

Profiles separate language recognition and interaction contracts from the
lifecycle detector. New domains can be registered without teaching the core
another chain of ``if domain == ...`` statements.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import ContinuityPolicy, FocusMode, InactivityBehavior, InitiativeMode, InteractionContract


def _normalize_token(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()


@dataclass(frozen=True, slots=True)
class EngagementProfile:
    profile_id: str
    adapter_id: str
    kind: str
    phrases: tuple[str, ...]
    contract: InteractionContract
    continuity: ContinuityPolicy
    focus: FocusMode = FocusMode.FOREGROUND
    priority: int = 60
    shared_session: bool = True
    direct_start_is_sufficient: bool = False
    confidence_continuity: float = 0.92
    confidence_shared: float = 0.82
    confidence_direct: float = 0.78

    def __post_init__(self) -> None:
        if not _normalize_token(self.profile_id):
            raise ValueError("profile_id is required")
        if not _normalize_token(self.adapter_id):
            raise ValueError("adapter_id is required")
        if not _normalize_token(self.kind):
            raise ValueError("kind is required")
        if not self.phrases:
            raise ValueError("at least one detection phrase is required")


class EngagementProfileRegistry:
    def __init__(self, profiles: Iterable[EngagementProfile] = ()) -> None:
        self._profiles: dict[str, EngagementProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: EngagementProfile, *, replace: bool = False) -> None:
        key = _normalize_token(profile.profile_id)
        if key in self._profiles and not replace:
            raise ValueError(f"engagement profile already exists: {key}")
        self._profiles[key] = profile

    def unregister(self, profile_id: str) -> bool:
        return self._profiles.pop(_normalize_token(profile_id), None) is not None

    def get(self, profile_id: str) -> EngagementProfile | None:
        return self._profiles.get(_normalize_token(profile_id))

    def all(self) -> tuple[EngagementProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def match(self, normalized_text: str, *, phrase_matcher) -> EngagementProfile | None:
        candidates: list[tuple[int, int, EngagementProfile]] = []
        for profile in self._profiles.values():
            matched = [phrase for phrase in profile.phrases if phrase_matcher(normalized_text, (phrase,))]
            if not matched:
                continue
            longest = max(len(_normalize_token(item)) for item in matched)
            candidates.append((longest, profile.priority, profile))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], item[1], item[2].profile_id))[2]

    @classmethod
    def defaults(cls) -> "EngagementProfileRegistry":
        return cls(default_profiles())


def default_profiles() -> tuple[EngagementProfile, ...]:
    return (
        EngagementProfile(
            profile_id="chess",
            adapter_id="chess",
            kind="chess",
            phrases=(
                "cờ vua", "co vua", "chơi cờ", "choi co", "chess", "ván cờ", "van co",
                "đánh cờ", "danh co", "pém cờ", "pem co", "pém thôi", "pem thoi",
            ),
            contract=InteractionContract(
                role="chess_companion",
                initiative=InitiativeMode.PROACTIVE,
                feedback_style="sarcastic_but_helpful",
                allowed_actions=(
                    "ask_if_board_is_open", "acknowledge_board_detected", "announce_game_started",
                    "comment_on_progress", "give_hint", "point_out_mistake", "ask_for_position",
                    "evaluate_position", "state_suggested_move_when_available", "summarize_game",
                ),
                forbidden_actions=("invent_board_state", "ignore_assistance_mode"),
                preferences={
                    "light_mockery_allowed": True, "refuse_trivial_hints": True,
                    "occasionally_reveal_direct_move": True,
                },
            ),
            continuity=ContinuityPolicy(55, 125, 8, 8, 7200),
            priority=78,
            direct_start_is_sufficient=True,
        ),
        EngagementProfile(
            profile_id="coding",
            adapter_id="coding",
            kind="coding",
            phrases=("code", "coding", "lập trình", "lap trinh", "debug", "sửa lỗi", "sua loi", "programming"),
            contract=InteractionContract(
                role="pair_programmer",
                initiative=InitiativeMode.EVENT_DRIVEN,
                feedback_style="direct",
                allowed_actions=("review_change", "explain_error", "suggest_patch", "track_tests"),
                forbidden_actions=("claim_unseen_code", "rewrite_large_scope_without_request"),
                preferences={"prefer_minimal_changes": True},
            ),
            continuity=ContinuityPolicy(90, 210, 8, 10, 10800),
            priority=75,
        ),
        EngagementProfile(
            profile_id="study",
            adapter_id="guided_routine",
            kind="study",
            phrases=("học", "hoc", "ôn bài", "on bai", "study", "learn", "revision"),
            contract=InteractionContract(
                role="study_coach",
                initiative=InitiativeMode.GUIDED,
                feedback_style="encouraging_and_direct",
                allowed_actions=("guide_next_step", "check_progress", "correct_mistake", "pause_on_request"),
                forbidden_actions=("invent_progress", "skip_safety_constraints"),
            ),
            continuity=ContinuityPolicy(75, 180, 8, 10, 7200),
            priority=70,
        ),
        EngagementProfile(
            profile_id="workout",
            adapter_id="guided_routine",
            kind="workout",
            phrases=("tập gym", "tap gym", "tập luyện", "tap luyen", "workout", "exercise", "reps"),
            contract=InteractionContract(
                role="workout_companion",
                initiative=InitiativeMode.GUIDED,
                feedback_style="encouraging_and_direct",
                allowed_actions=("guide_next_step", "check_progress", "correct_mistake", "pause_on_request"),
                forbidden_actions=("invent_progress", "skip_safety_constraints"),
            ),
            continuity=ContinuityPolicy(75, 180, 8, 10, 7200),
            priority=70,
        ),
        EngagementProfile(
            profile_id="cooking",
            adapter_id="guided_routine",
            kind="cooking",
            phrases=("nấu", "nau", "cooking", "cook", "recipe"),
            contract=InteractionContract(
                role="cooking_guide",
                initiative=InitiativeMode.GUIDED,
                feedback_style="encouraging_and_direct",
                allowed_actions=("guide_next_step", "check_progress", "correct_mistake", "pause_on_request"),
                forbidden_actions=("invent_progress", "skip_safety_constraints"),
            ),
            continuity=ContinuityPolicy(75, 180, 8, 10, 7200),
            priority=70,
        ),
        EngagementProfile(
            profile_id="monitoring",
            adapter_id="monitoring",
            kind="monitoring",
            phrases=("theo dõi", "theo doi", "monitor", "đợi build", "doi build", "download", "render", "deployment"),
            contract=InteractionContract(
                role="state_monitor",
                initiative=InitiativeMode.MONITORING,
                feedback_style="concise",
                allowed_actions=("notify_state_change", "report_success", "report_failure"),
                forbidden_actions=("chatter_without_change", "claim_unobserved_result"),
            ),
            continuity=ContinuityPolicy(300, 900, 4, 4, 21600, inactivity_behavior=InactivityBehavior.KEEP),
            focus=FocusMode.BACKGROUND,
            priority=68,
            shared_session=False,
            direct_start_is_sufficient=True,
        ),
        EngagementProfile(
            profile_id="watch_along",
            adapter_id="generic",
            kind="watch_along",
            phrases=("xem phim", "xem video", "watch movie", "watch this", "watch along"),
            contract=InteractionContract(
                role="watch_along",
                initiative=InitiativeMode.AMBIENT,
                feedback_style="wry_companion",
                allowed_actions=("comment_on_event", "react", "ask_short_question"),
                forbidden_actions=("talk_over_important_moment", "invent_screen_event"),
            ),
            continuity=ContinuityPolicy(80, 170, 10, 12, 10800),
            priority=66,
        ),
        EngagementProfile(
            profile_id="game_companion",
            adapter_id="generic",
            kind="game_companion",
            phrases=("chơi game", "choi game", "game", "trận", "tran", "match"),
            contract=InteractionContract(
                role="game_companion",
                initiative=InitiativeMode.AMBIENT,
                feedback_style="wry_companion",
                allowed_actions=("comment_on_event", "react", "ask_short_question"),
                forbidden_actions=("talk_over_important_moment", "invent_screen_event"),
            ),
            continuity=ContinuityPolicy(80, 170, 10, 12, 10800),
            priority=66,
        ),
    )


def collaboration_profile() -> EngagementProfile:
    return EngagementProfile(
        profile_id="collaboration",
        adapter_id="generic",
        kind="collaboration",
        phrases=("hỗ trợ",),
        contract=InteractionContract(
            role="collaborative_companion",
            initiative=InitiativeMode.PROACTIVE,
            feedback_style="direct_but_kind",
            allowed_actions=("track_progress", "ask_relevant_question", "offer_next_step"),
            forbidden_actions=("invent_progress", "abandon_without_lifecycle_event"),
        ),
        continuity=ContinuityPolicy(),
        priority=62,
    )


__all__ = [
    "EngagementProfile",
    "EngagementProfileRegistry",
    "collaboration_profile",
    "default_profiles",
]
