# -*- coding: utf-8 -*-
"""Domain adapters for the engagement kernel.

The kernel manages lifecycle and attention. Adapters own domain state, lifecycle
signals and their small planner projection. Adding a new long-running activity
should add an adapter, not another ``if kind == ...`` to the coordinator.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from .state_patch import apply_tri_state_patch
from .detector import normalize
from .contracts import (
    Engagement,
    EngagementEvent,
    LifecycleSignal,
    LifecycleStatus,
    PlannerProjection,
)


def _compact(value: Any, limit: int = 220) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _recent(state: Mapping[str, Any], text: str, *, limit: int = 8) -> list[str]:
    items = [str(item) for item in tuple(state.get("recent_progress", ()) or ()) if str(item).strip()]
    if text:
        items.append(_compact(text, 180))
    return list(dict.fromkeys(items))[-limit:]


def _status(payload: Mapping[str, Any]) -> str:
    return _compact(
        payload.get("status") or payload.get("state") or payload.get("result") or payload.get("outcome"),
        80,
    ).lower()


def _ends_with(kind: str, values: tuple[str, ...]) -> bool:
    return any(kind == item or kind.endswith(f".{item}") for item in values)


def _chess_companion_mode(objective: str) -> str:
    text = normalize(objective)
    if any(marker in text for marker in (
        "xem toi", "xem tui", "coi toi", "coi tui", "lam khan gia", "khan gia", "watch me",
    )):
        return "spectator"
    if any(marker in text for marker in (
        "chi toi", "goi y", "giup toi", "giup tui", "day toi", "huong dan", "phan tich", "nen di", "nen danh",
    )):
        return "coach"
    if any(marker in text for marker in (
        "choi voi toi", "choi voi tui", "dau voi toi", "dau voi tui", "play against me",
    )):
        return "opponent"
    return "companion"


def _explicit_chess_role_update(text: str) -> str:
    normalized = normalize(text)
    if any(marker in normalized for marker in (
        "lam khan gia", "em lam khan gia", "chi xem", "chi coi", "xem toi choi", "xem tui choi",
        "dung chi nuoc", "khong chi nuoc", "watch me",
    )):
        return "spectator"
    if any(marker in normalized for marker in (
        "chi toi", "goi y cho toi", "goi y cho tui", "coach toi", "coach tui", "phan tich cho toi",
    )):
        return "coach"
    if any(marker in normalized for marker in (
        "choi voi toi", "choi voi tui", "dau voi toi", "dau voi tui",
    )):
        return "opponent"
    return ""


def _explicit_chess_user_side(text: str) -> str:
    normalized = normalize(text)
    if any(marker in normalized for marker in ("toi ben trang", "tui ben trang", "toi cam trang", "tui cam trang")):
        return "white"
    if any(marker in normalized for marker in ("toi ben den", "tui ben den", "toi cam den", "tui cam den")):
        return "black"
    return ""


class EngagementAdapter(Protocol):
    adapter_id: str

    def initial_state(self, objective: str, *, metadata: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...

    def reduce(self, state: Mapping[str, Any], event: EngagementEvent) -> Mapping[str, Any]: ...

    def project(self, engagement: Engagement, *, now: float) -> PlannerProjection: ...

    def accepts_evidence(self, kind: str, *, source: str, payload: Mapping[str, Any]) -> bool: ...

    def signal_importance(self, event: EngagementEvent) -> float: ...

    def lifecycle_signal(
        self,
        engagement: Engagement,
        event: EngagementEvent,
        state: Mapping[str, Any],
    ) -> LifecycleSignal | None: ...


class GenericEngagementAdapter:
    adapter_id = "generic"
    summary_label = "Shared activity"
    expected_events = ("user progress", "activity change", "user request", "completion")

    _GENERIC_COMPLETION_EVENTS = {
        "task.completed",
        "session.completed",
        "session.ended",
        "routine.completed",
        "game.completed",
        "game.ended",
        "game.over",
    }

    def initial_state(self, objective: str, *, metadata: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        metadata = dict(metadata or {})
        return {
            "summary": _compact(objective, 300),
            "recent_progress": [],
            "next_expected_events": list(self.expected_events),
            "waiting_for_user": False,
            "metadata": metadata,
        }

    def accepts_evidence(self, kind: str, *, source: str, payload: Mapping[str, Any]) -> bool:
        del source, payload
        return bool(str(kind or "").strip())

    def signal_importance(self, event: EngagementEvent) -> float:
        kind = str(event.kind or "").strip().lower()
        fixed = {
            "domain.progress": 0.72,
            "task.progress": 0.68,
            "activity.changed": 0.52,
            "engagement.resumed": 0.40,
            "engagement.started": 0.35,
            "engagement.waiting": 0.50,
            "engagement.paused": 0.62,
            "engagement.completed": 0.95,
            "engagement.cancelled": 0.95,
        }
        if kind in fixed:
            return fixed[kind]
        if event.source == "domain" and kind not in {"assistant.spoke", "user.message_received"}:
            return 0.72
        return 0.0

    def lifecycle_signal(
        self,
        engagement: Engagement,
        event: EngagementEvent,
        state: Mapping[str, Any],
    ) -> LifecycleSignal | None:
        del state
        kind = str(event.kind or "").strip().lower()
        if kind.startswith("engagement."):
            return None
        if kind in self._GENERIC_COMPLETION_EVENTS:
            return LifecycleSignal(LifecycleStatus.COMPLETED, "terminal_domain_event", kind)
        if _ends_with(kind, ("cancelled", "canceled", "aborted")):
            return LifecycleSignal(LifecycleStatus.CANCELLED, "domain_cancelled", kind)
        if kind in {"task.waiting", "session.waiting", "task.blocked", "session.blocked"}:
            return LifecycleSignal(LifecycleStatus.WAITING, "domain_waiting", kind)
        if kind in {"task.resumed", "session.resumed"} and engagement.lifecycle in {
            LifecycleStatus.WAITING,
            LifecycleStatus.PAUSED,
        }:
            return LifecycleSignal(LifecycleStatus.ACTIVE, "domain_resumed", kind)
        return None

    def reduce(self, state: Mapping[str, Any], event: EngagementEvent) -> Mapping[str, Any]:
        data = dict(state or {})
        payload = dict(event.payload or {})
        kind = event.kind
        detail = _compact(payload.get("detail") or payload.get("text") or payload.get("event"), 220)
        if kind == "user.message_received":
            data["last_user_message"] = detail
            data["waiting_for_user"] = False
            data["recent_progress"] = _recent(data, f"User: {detail}" if detail else "User continued the activity")
        elif kind == "assistant.spoke":
            data["last_assistant_message"] = detail
            data["recent_progress"] = _recent(data, f"ERIS: {detail}" if detail else "ERIS responded")
        elif kind in {"activity.changed", "domain.progress", "task.progress"} or event.source == "domain":
            data["last_activity_event"] = detail or kind
            data["recent_progress"] = _recent(data, detail or kind)
        elif kind == "engagement.waiting":
            data["waiting_for_user"] = True
            data["recent_progress"] = _recent(data, detail or "Engagement waiting")
        elif kind == "engagement.paused":
            data["waiting_for_user"] = False
            data["recent_progress"] = _recent(data, detail or "Engagement paused")
        elif kind in {"engagement.resumed", "engagement.started"}:
            data["waiting_for_user"] = False
            data["recent_progress"] = _recent(data, detail or kind.replace("engagement.", "Engagement "))
        elif kind in {"engagement.completed", "engagement.cancelled"}:
            data["waiting_for_user"] = False
            data["recent_progress"] = _recent(data, detail or kind.replace("engagement.", "Engagement "))
        # Patch ba trạng thái: key vắng mặt giữ nguyên; None xóa; giá trị khác
        # ghi đè. Nhờ vậy suggestion cũ không sống dai sang nước mới.
        data = apply_tri_state_patch(
            data, payload,
            keys=(
                "fen", "pgn", "move_number", "ply", "file", "test_status", "step", "progress",
                "result", "status", "state", "outcome", "error", "url", "artifact",
                "mode", "full_assistance", "board_fen", "side_to_move", "last_move_uci",
                "last_move_san", "evaluation_cp", "evaluation_label",
                "evaluation_cp_white", "evaluation_label_white",
                "evaluation_cp_user", "evaluation_label_user", "advantage_side", "user_position",
                "suggested_move_uci", "suggested_move_san", "confidence",
                "evaluation_swing_cp", "move_quality", "hint_focus", "orientation",
                "user_side", "mover_side", "actor", "actor_is_user", "event", "speech_act",
                "commentary_focus", "speech_reason", "speech_priority", "should_speak",
                "should_wake", "suppress_generic_screen_topics", "suggestions_allowed",
                "companion_mode",
            ),
        )
        return data

    def _state_summary(self, engagement: Engagement) -> str:
        state = engagement.domain_state
        return _compact(state.get("summary") or engagement.objective, 360)

    def project(self, engagement: Engagement, *, now: float) -> PlannerProjection:
        state = engagement.domain_state
        constraints = tuple(
            [f"Allowed: {item}" for item in engagement.contract.allowed_actions]
            + [f"Forbidden: {item}" for item in engagement.contract.forbidden_actions]
        )
        return PlannerProjection(
            engagement_id=engagement.engagement_id,
            kind=engagement.kind,
            objective=engagement.objective,
            lifecycle=engagement.lifecycle.value,
            focus=engagement.focus.value,
            priority=engagement.priority,
            role=engagement.contract.role,
            initiative=engagement.contract.initiative.value,
            feedback_style=engagement.contract.feedback_style,
            current_state_summary=self._state_summary(engagement),
            recent_progress=tuple(str(item) for item in tuple(state.get("recent_progress", ()) or ())[-6:]),
            next_expected_events=tuple(
                str(item) for item in tuple(state.get("next_expected_events", self.expected_events) or self.expected_events)[:8]
            ),
            interaction_constraints=constraints[:12],
            age_seconds=max(0.0, now - engagement.started_at),
            seconds_since_progress=max(0.0, now - engagement.last_progress_at),
            current_state=dict(state),
            evidence_binding=engagement.evidence_binding.to_dict(),
            last_event_kind=engagement.last_event_kind,
            lifecycle_changed_at=engagement.lifecycle_changed_at,
            last_event_log_seq=engagement.last_event_log_seq,
        )


class ChessCompanionAdapter(GenericEngagementAdapter):
    adapter_id = "chess"
    summary_label = "Chess game"
    expected_events = ("board detected", "game started", "move played", "game ended")

    def initial_state(self, objective: str, *, metadata: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        """Tạo task xem cờ ở pha chờ bàn, không giả định người dùng đã vào ván."""

        data = dict(super().initial_state(objective, metadata=metadata))
        companion_mode = _chess_companion_mode(objective)
        data.update({
            "chess_phase": "waiting_for_board",
            "board_seen": False,
            "game_started": False,
            "game_over": False,
            "companion_mode": companion_mode,
            "exclusive_focus": True,
            "suppress_generic_screen_topics": True,
            "suggestions_allowed": companion_mode == "coach",
            "event_driven_only": False,
            "user_side": "",
            "last_commented_fingerprint": "",
            # Nhịp hỏi riêng cho pha chờ. Policy đọc hai khóa generic này nên
            # adapter khác cũng có thể dùng mà không phải nhét if-domain vào core.
            "interaction_cadence_seconds": 18.0,
            "preferred_interaction_action": "ask",
            "waiting_prompt_count": 0,
            "next_expected_events": [
                "Chess.com board snapshot",
                "bridge connection",
                "user confirms the board is open",
            ],
        })
        return data

    def reduce(self, state: Mapping[str, Any], event: EngagementEvent) -> Mapping[str, Any]:
        """Thu gọn event bridge thành state machine quan sát cờ tạm thời."""

        data = dict(super().reduce(state, event))
        kind = str(event.kind or "").strip().lower()
        payload = dict(event.payload or {})
        payload_phase = _compact(payload.get("phase"), 40).lower()

        if kind == "user.message_received":
            user_text = str(payload.get("text") or payload.get("detail") or "")
            role_update = _explicit_chess_role_update(user_text)
            side_update = _explicit_chess_user_side(user_text)
            if role_update:
                data["companion_mode"] = role_update
                data["suggestions_allowed"] = role_update == "coach"
                data["exclusive_focus"] = True
                data["event_driven_only"] = str(data.get("chess_phase", "")) == "active"
                data["recent_progress"] = _recent(data, f"Chess role updated: {role_update}")
            if side_update:
                data["user_side"] = side_update

        if kind in {"chess.board_detected", "chess.board_ready"}:
            data["board_seen"] = True
            data["chess_phase"] = "waiting_for_start"
            data["event_driven_only"] = False
            data["interaction_cadence_seconds"] = 22.0
            data["preferred_interaction_action"] = "ask"
            data["waiting_prompt_count"] = 0
            data["board_detected_at"] = event.timestamp
            data["next_expected_events"] = [
                "first legal move",
                "clock starts",
                "game started signal",
            ]
            data["recent_progress"] = _recent(data, "Đã thấy bàn cờ; đang chờ ván bắt đầu")
        elif kind == "chess.game_started":
            data["board_seen"] = True
            data["game_started"] = True
            data["chess_phase"] = "active"
            data["event_driven_only"] = True
            data["interaction_cadence_seconds"] = 45.0
            data["preferred_interaction_action"] = "speak" if bool(payload.get("should_speak", True)) else "wait"
            data["game_started_at"] = event.timestamp
            data["next_expected_events"] = ["move played", "position changed", "game ended"]
            data["recent_progress"] = _recent(data, "Ván cờ đã bắt đầu")
        elif kind in {"chess.move_played", "chess.position_changed"}:
            data["board_seen"] = True
            if payload_phase == "active" or kind == "chess.move_played":
                data["game_started"] = True
                data["chess_phase"] = "active"
                data["event_driven_only"] = True
                data["interaction_cadence_seconds"] = 45.0
                data["preferred_interaction_action"] = "speak" if bool(payload.get("should_speak", False)) else "wait"
                data["next_expected_events"] = ["move played", "position changed", "game ended"]
            # Ghi lịch sử chất lượng nước để companion_policy phát hiện xu hướng
            # (ví dụ: 3 blunder liên tiếp, user đang vào form tốt, v.v.).
            if kind == "chess.move_played":
                q_entry = {
                    "san": _compact(payload.get("last_move_san") or payload.get("last_move_uci"), 12),
                    "quality": _compact(payload.get("move_quality"), 16).lower(),
                    "actor": _compact(payload.get("actor"), 12),
                    "move_number": int(payload.get("move_number") or 0),
                    "swing": int(payload.get("evaluation_swing_cp") or 0),
                }
                history = list(data.get("recent_move_qualities", []) or [])
                history.append(q_entry)
                data["recent_move_qualities"] = history[-16:]  # giữ 16 nước gần nhất
        elif kind in {"chess.game_over", "chess.game.ended", "chess.game.completed"}:
            data["board_seen"] = True
            data["game_over"] = True
            data["chess_phase"] = "completed"
            data["interaction_cadence_seconds"] = 0.0
            data["preferred_interaction_action"] = "speak"
            data["game_ended_at"] = event.timestamp
            data["next_expected_events"] = ["final commentary", "game summary"]

        if kind.startswith("chess."):
            data["last_chess_snapshot_at"] = event.timestamp

        # Sau khi ERIS vừa nói ở pha chờ, giãn lần hỏi kế tiếp để tránh biến
        # "đã vô chưa" thành đồng hồ báo thức cứ 18 giây reo một lần.
        if kind == "assistant.spoke" and str(data.get("chess_phase", "")).startswith("waiting_"):
            count = max(0, int(data.get("waiting_prompt_count", 0) or 0)) + 1
            data["waiting_prompt_count"] = count
            data["interaction_cadence_seconds"] = 35.0 if count == 1 else 60.0
            if str(data.get("chess_phase", "")) == "waiting_for_start":
                data["board_acknowledged"] = True

        # Cho phép bridge truyền phase chính xác hơn, nhưng không cho snapshot cũ
        # kéo task đã completed quay ngược lại active.
        if payload_phase and data.get("chess_phase") != "completed":
            if payload_phase in {"waiting_for_start", "active", "possibly_ended"}:
                data["chess_phase"] = payload_phase
        return data

    def accepts_evidence(self, kind: str, *, source: str, payload: Mapping[str, Any]) -> bool:
        del source, payload
        return str(kind or "").strip().lower().startswith("chess.")

    def signal_importance(self, event: EngagementEvent) -> float:
        kind = str(event.kind or "").strip().lower()
        # Engagement vừa được tạo là tín hiệu để sau khoảng nghỉ tối thiểu ERIS
        # hỏi ngắn xem người dùng đã mở bàn/bridge chưa, thay vì im 55 giây.
        if kind == "engagement.started":
            return 0.72
        payload = dict(event.payload or {})
        try:
            typed_priority = max(0.0, min(1.0, float(payload.get("speech_priority", 0.0) or 0.0)))
        except (TypeError, ValueError):
            typed_priority = 0.0
        if kind in {"chess.board_detected", "chess.board_ready"}:
            # Phải vượt threshold 0.45 để kernel tạo signal và Eris biết
            # người dùng vừa mở bàn cờ. 0.42 cũ luôn bị drop.
            return max(0.62, typed_priority)
        if kind == "chess.game_started":
            return max(0.78, typed_priority)
        if kind == "chess.move_played":
            # companion_policy đã quyết định nước nào đáng wake; ở đây không filter
            # lại. Đảm bảo mọi move_played đều vượt threshold mặc định 0.45 để
            # engagement kernel tạo signal thay vì âm thầm drop event.
            return max(0.48, typed_priority)
        if kind == "chess.position_changed":
            return min(0.44, typed_priority)
        if kind in {"chess.game_over", "chess.game.ended", "chess.game.completed"}:
            return max(0.99, typed_priority)
        return super().signal_importance(event)

    def project(self, engagement: Engagement, *, now: float) -> PlannerProjection:
        base = super().project(engagement, now=now)
        state = engagement.domain_state
        # All modes (analysis, bot, private, live) now receive full assistance.
        # The only exception is UNKNOWN pages which send full_assistance=False.
        full = bool(state.get("full_assistance", True))
        companion_mode = str(state.get("companion_mode") or "companion").strip().lower()
        mode_rule = {
            "spectator": (
                "The user asked for a watch-along spectator. React to meaningful moves and consequences; "
                "do not volunteer engine moves or coaching unless the user explicitly asks."
            ),
            "coach": (
                "The user asked for coaching. Concrete suggestions are allowed only when present in typed state, "
                "and each suggestion needs one tactical or positional reason."
            ),
            "opponent": (
                "The user asked to play against ERIS, but this bridge only observes the external game. "
                "Do not pretend ERIS controls either side; clarify only when directly relevant."
            ),
            "companion": "Stay with the active game and comment selectively on meaningful changes.",
        }.get(companion_mode, "Stay with the active game and comment selectively on meaningful changes.")
        dynamic = (
            "Discuss the concrete position or consequence of the latest move. Light dry teasing is welcome when grounded. "
            "Never narrate bridge telemetry or OCR. " + mode_rule
            if full else
            "Unknown page: cannot confirm game context — only react socially, do not suggest moves or evaluate."
        )
        phase = str(state.get("chess_phase") or "waiting_for_board").strip().lower()
        phase_instruction = {
            "waiting_for_board": (
                "No chess-board snapshot has arrived. Do not claim to see the board. "
                "When interaction is due, ask briefly whether the user has opened the game and whether the Chess.com bridge is connected."
            ),
            "waiting_for_start": (
                "The position is available but play has not started. Invite the first move naturally; "
                "do not announce telemetry such as 'board detected/received'."
            ),
            "active": (
                "The game is active. Ground every comment in the latest typed state/FEN. React to the actual move, "
                "discuss a tactical or positional consequence, and use light teasing when deserved. "
                "Do not repeat commentary when no move changed."
            ),
            "possibly_ended": (
                "The game may have ended. Ask or wait for a result signal instead of declaring a result without evidence."
            ),
            "completed": (
                "The game is complete. Give one concise result-aware summary, then let the engagement close."
            ),
        }.get(phase, "Use only observed chess evidence; never invent the board state.")
        # Chỉ PlannerProjection giữ tối đa 12 constraint. Đặt chỉ dẫn theo pha
        # lên trước để nó không bị danh sách allowed_actions dài đẩy mất.
        constraints = (phase_instruction, dynamic) + tuple(base.interaction_constraints)
        return PlannerProjection(
            engagement_id=base.engagement_id, kind=base.kind, objective=base.objective,
            lifecycle=base.lifecycle, focus=base.focus, priority=base.priority, role=base.role,
            initiative=base.initiative, feedback_style=base.feedback_style,
            current_state_summary=base.current_state_summary, recent_progress=base.recent_progress,
            next_expected_events=base.next_expected_events, interaction_constraints=constraints[:12],
            age_seconds=base.age_seconds, seconds_since_progress=base.seconds_since_progress,
            current_state=base.current_state,
            evidence_binding=base.evidence_binding, last_event_kind=base.last_event_kind,
            lifecycle_changed_at=base.lifecycle_changed_at, last_event_log_seq=base.last_event_log_seq,
            resume_focus=base.resume_focus,
        )

    def lifecycle_signal(
        self,
        engagement: Engagement,
        event: EngagementEvent,
        state: Mapping[str, Any],
    ) -> LifecycleSignal | None:
        kind = str(event.kind or "").strip().lower()
        if kind in {"chess.game_over", "chess.game.ended", "chess.game.completed"}:
            return LifecycleSignal(LifecycleStatus.COMPLETED, "chess_game_finished", _status(event.payload) or kind)
        return super().lifecycle_signal(engagement, event, state)

    def _state_summary(self, engagement: Engagement) -> str:
        state = engagement.domain_state
        pieces = [engagement.objective]
        if state.get("chess_phase"):
            pieces.append(f"phase={_compact(state.get('chess_phase'), 32)}")
        if state.get("companion_mode"):
            pieces.append(f"companion_mode={_compact(state.get('companion_mode'), 24)}")
        if state.get("event"):
            pieces.append(f"event={_compact(state.get('event'), 32)}")
        if state.get("actor"):
            pieces.append(f"actor={_compact(state.get('actor'), 16)}")
        if state.get("speech_reason"):
            pieces.append(f"speech_reason={_compact(state.get('speech_reason'), 48)}")
        if state.get("commentary_focus"):
            pieces.append(f"focus={_compact(state.get('commentary_focus'), 160)}")
        if state.get("mode"):
            pieces.append(f"mode={_compact(state.get('mode'), 32)}")
        if state.get("move_number") is not None:
            pieces.append(f"move {state.get('move_number')}")
        if state.get("side_to_move"):
            pieces.append(f"to_move={_compact(state.get('side_to_move'), 16)}")
        if state.get("last_move_san") or state.get("last_move_uci"):
            pieces.append(f"last={_compact(state.get('last_move_san') or state.get('last_move_uci'), 24)}")
        if state.get("evaluation_label_user") and state.get("user_side"):
            pieces.append(f"user_position={_compact(state.get('evaluation_label_user'), 40)}")
        elif state.get("advantage_side"):
            pieces.append(f"advantage_side={_compact(state.get('advantage_side'), 16)}")
        elif state.get("evaluation_label"):
            pieces.append(f"evaluation_white={_compact(state.get('evaluation_label'), 40)}")
        if state.get("move_quality"):
            pieces.append(f"move_quality={_compact(state.get('move_quality'), 24)}")
        if state.get("hint_focus"):
            pieces.append(f"hint_focus={_compact(state.get('hint_focus'), 32)}")
        if state.get("suggested_move_san") or state.get("suggested_move_uci"):
            pieces.append(f"suggestion={_compact(state.get('suggested_move_san') or state.get('suggested_move_uci'), 24)}")
        if state.get("fen"):
            pieces.append(f"FEN={_compact(state.get('fen'), 120)}")
        if state.get("result"):
            pieces.append(f"result={_compact(state.get('result'), 40)}")
        return "; ".join(piece for piece in pieces if piece)


class CodingCompanionAdapter(GenericEngagementAdapter):
    adapter_id = "coding"
    summary_label = "Coding session"
    expected_events = ("file changed", "tests completed", "error appeared", "user asks for review")

    def lifecycle_signal(
        self,
        engagement: Engagement,
        event: EngagementEvent,
        state: Mapping[str, Any],
    ) -> LifecycleSignal | None:
        kind = str(event.kind or "").strip().lower()
        if kind in {"coding.session.completed", "coding.session.ended", "task.completed"}:
            return LifecycleSignal(LifecycleStatus.COMPLETED, "coding_session_finished", kind)
        return super().lifecycle_signal(engagement, event, state)

    def _state_summary(self, engagement: Engagement) -> str:
        state = engagement.domain_state
        pieces = [engagement.objective]
        if state.get("file"):
            pieces.append(f"file={_compact(state.get('file'), 120)}")
        if state.get("test_status"):
            pieces.append(f"tests={_compact(state.get('test_status'), 80)}")
        return "; ".join(piece for piece in pieces if piece)


class GuidedRoutineAdapter(GenericEngagementAdapter):
    adapter_id = "guided_routine"
    summary_label = "Guided routine"
    expected_events = ("step completed", "user asks for help", "pause", "routine completed")

    def lifecycle_signal(
        self,
        engagement: Engagement,
        event: EngagementEvent,
        state: Mapping[str, Any],
    ) -> LifecycleSignal | None:
        kind = str(event.kind or "").strip().lower()
        if kind in {
            "routine.completed", "workout.completed", "study.completed", "cooking.completed", "task.completed",
        }:
            return LifecycleSignal(LifecycleStatus.COMPLETED, "guided_routine_finished", kind)
        if kind in {"routine.waiting", "step.waiting", "step.blocked"}:
            return LifecycleSignal(LifecycleStatus.WAITING, "guided_routine_waiting", kind)
        return super().lifecycle_signal(engagement, event, state)

    def _state_summary(self, engagement: Engagement) -> str:
        state = engagement.domain_state
        pieces = [engagement.objective]
        if state.get("step") is not None:
            pieces.append(f"current step={state.get('step')}")
        if state.get("progress") is not None:
            pieces.append(f"progress={state.get('progress')}")
        return "; ".join(piece for piece in pieces if piece)


class MonitoringAdapter(GenericEngagementAdapter):
    adapter_id = "monitoring"
    summary_label = "Background monitor"
    expected_events = ("watched state changed", "success", "failure", "monitor stopped")

    def accepts_evidence(self, kind: str, *, source: str, payload: Mapping[str, Any]) -> bool:
        normalized = str(kind or "").strip().lower()
        if source == "activity_session" and normalized == "activity.changed":
            return True  # Router still requires an exact binding for background monitors.
        return normalized.startswith(("build.", "deployment.", "download.", "render.", "monitor.")) or normalized in {
            "domain.progress", "task.progress", "engagement.completed", "engagement.cancelled",
        }

    def signal_importance(self, event: EngagementEvent) -> float:
        normalized = str(event.kind or "").strip().lower()
        if normalized.startswith(("build.", "deployment.", "download.", "render.", "monitor.")):
            return 0.96 if _ends_with(normalized, ("completed", "succeeded", "failed", "cancelled", "canceled")) else 0.82
        return super().signal_importance(event)

    def lifecycle_signal(
        self,
        engagement: Engagement,
        event: EngagementEvent,
        state: Mapping[str, Any],
    ) -> LifecycleSignal | None:
        del state
        kind = str(event.kind or "").strip().lower()
        if kind.startswith("engagement."):
            return None
        watched = kind.startswith(("build.", "deployment.", "download.", "render.", "monitor.")) or kind in {
            "domain.progress", "task.progress",
        }
        if not watched:
            return super().lifecycle_signal(engagement, event, {})
        status = _status(event.payload)
        if _ends_with(kind, ("cancelled", "canceled", "aborted")) or status in {"cancelled", "canceled", "aborted"}:
            return LifecycleSignal(LifecycleStatus.CANCELLED, "monitor_cancelled", status or kind)
        if _ends_with(kind, ("completed", "succeeded", "failed", "finished", "done")) or status in {
            "success", "succeeded", "completed", "complete", "done", "failed", "failure", "error",
        }:
            return LifecycleSignal(LifecycleStatus.COMPLETED, "monitor_terminal_state", status or kind)
        if _ends_with(kind, ("queued", "pending", "started", "running", "waiting")) or status in {
            "queued", "pending", "started", "running", "waiting",
        }:
            return LifecycleSignal(LifecycleStatus.WAITING, "monitor_in_progress", status or kind)
        return None


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, EngagementAdapter] = {}
        for adapter in (
            GenericEngagementAdapter(),
            ChessCompanionAdapter(),
            CodingCompanionAdapter(),
            GuidedRoutineAdapter(),
            MonitoringAdapter(),
        ):
            self.register(adapter)

    def register(self, adapter: EngagementAdapter) -> None:
        adapter_id = _compact(getattr(adapter, "adapter_id", ""), 80)
        if not adapter_id:
            raise ValueError("adapter_id is required")
        self._adapters[adapter_id] = adapter

    def get(self, adapter_id: str) -> EngagementAdapter:
        return self._adapters.get(str(adapter_id or "").strip(), self._adapters["generic"])

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


__all__ = [
    "AdapterRegistry",
    "ChessCompanionAdapter",
    "CodingCompanionAdapter",
    "EngagementAdapter",
    "GenericEngagementAdapter",
    "GuidedRoutineAdapter",
    "MonitoringAdapter",
]
