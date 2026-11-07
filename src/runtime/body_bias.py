from __future__ import annotations

from typing import Callable

from cortex.internal_state import PersistentInternalState
from cortex.affective_dynamics import effective_affect_values


class BodyBiasProjector:
    """Projects cognitive state into the compact body-bias payload Godot consumes."""

    def __init__(self, state_provider: Callable[[], PersistentInternalState]):
        self._state_provider = state_provider

    def build_payload(self, observer_frame=None, impulse_hint: dict[str, object] | None = None) -> dict[str, object]:
        """Build compact body-bias payload.

        `impulse_hint` là phần nối sâu từ ArbitrationReport xuống body. Nó không
        override cứng thân thể; chỉ blend nhẹ lên curiosity/overload/anchor để body
        đồng bộ với lý do nói/im. Thiếu nó thì body vẫn chạy như cũ. Có nó thì
        scene_curiosity có thể nghiêng về quan sát, technical_help giữ code-side,
        silence/body_only giữ ít bubble hơn. Vâng, thân thể cũng cần đọc biên bản
        cuộc họp, thật phiền nhưng cần.
        """
        impulse_hint = dict(impulse_hint or {})
        state = self._state_provider()
        affect = effective_affect_values(state)
        focus_kind = self.normalize_focus_kind(str(getattr(state, "last_focus_kind", "") or "unknown"))
        observer_salience = self.clamp(getattr(observer_frame, "salience", 0.0) if observer_frame is not None else 0.0)
        observer_error = self.clamp(getattr(observer_frame, "possible_error", 0.0) if observer_frame is not None else 0.0)
        continuity_anxiety = self.clamp(
            max(
                float(getattr(state, "recursive_reactivation_pressure", 0.0) or 0.0),
                float(getattr(state, "memory_replay_pressure", 0.0) or 0.0) * 0.72,
                float(getattr(state, "silence_pressure", 0.0) or 0.0) * 0.82,
                min(1.0, float(getattr(state, "silence_ticks", 0) or 0) / 18.0),
                observer_error * 0.80,
            )
        )
        curiosity = self.clamp(
            max(
                float(affect.get("curiosity", getattr(state, "curiosity", 0.0)) or 0.0),
                float(getattr(state, "curiosity_pressure", 0.0) or 0.0),
                float(getattr(state, "association_pressure", 0.0) or 0.0) * 0.86,
                observer_salience * (0.74 if focus_kind == "code" else 0.52),
            )
            + (0.12 if focus_kind == "code" else 0.0)
        )
        overload = self.clamp(
            max(
                float(getattr(state, "fatigue", 0.0) or 0.0),
                float(getattr(state, "cognitive_tension", 0.0) or 0.0),
                float(getattr(state, "restraint_pressure", 0.0) or 0.0) * 0.55,
                observer_error * 0.68,
            )
        )
        solitude = self.clamp(
            max(
                float(getattr(state, "restraint_pressure", 0.0) or 0.0),
                0.22 + overload * 0.34,
                float(getattr(state, "subconscious_pressure", 0.0) or 0.0) * 0.44,
            )
        )
        preferred_anchor = "chat_near"
        if overload >= 0.58 or solitude >= 0.66:
            preferred_anchor = "bottom_right"
        elif focus_kind == "code" and curiosity >= max(0.48, continuity_anxiety + 0.06):
            preferred_anchor = "code_side"

        # Blend hint từ impulse_arbitration. Chỉ nhận các field an toàn; mọi thứ
        # khác để debug nhưng không cho điều khiển body tùy tiện. Không ai cần một
        # cái payload random biến body thành máy chạy lung tung.
        curiosity = self.clamp(curiosity + self.clamp(impulse_hint.get("curiosity_delta", 0.0)))
        overload = self.clamp(overload + self.clamp(impulse_hint.get("overload_delta", 0.0)))
        hinted_anchor = str(impulse_hint.get("preferred_anchor", "") or "")
        if hinted_anchor in {"chat_near", "code_side", "bottom_right"}:
            preferred_anchor = hinted_anchor
        hinted_focus = self.normalize_focus_kind(str(impulse_hint.get("focus_kind", "") or ""))
        if hinted_focus != "unknown":
            focus_kind = hinted_focus
        body_action_bias = str(impulse_hint.get("body_action_bias", "") or "")
        selected_impulse = str(impulse_hint.get("selected_impulse", "") or "")

        payload = {
            "continuity_anxiety": round(continuity_anxiety, 3),
            "curiosity": round(curiosity, 3),
            "overload": round(overload, 3),
            "solitude": round(solitude, 3),
            "focus_kind": focus_kind,
            "preferred_anchor": preferred_anchor,
            # V43: body nhận cùng affect hiệu dụng với writer/TTS. Godot cũ có
            # thể bỏ qua field mới; stage mới có dữ liệu mà không cần suy lại.
            "valence": round(float(affect.get("valence", 0.0)), 3),
            "arousal": round(float(affect.get("arousal", 0.0)), 3),
            "playfulness": round(float(affect.get("playfulness", 0.0)), 3),
            "delight": round(float(affect.get("delight", 0.0)), 3),
            "irritation": round(float(affect.get("irritation", 0.0)), 3),
            "tenderness": round(float(affect.get("tenderness", 0.0)), 3),
        }
        if body_action_bias:
            payload["body_action_bias"] = body_action_bias
        if selected_impulse:
            payload["selected_impulse"] = selected_impulse
        return payload

    @staticmethod
    def clamp(value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        if numeric != numeric:
            numeric = 0.0
        return max(0.0, min(1.0, numeric))

    @staticmethod
    def normalize_focus_kind(value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized in {"coding", "ide"}:
            return "code"
        if normalized in {"documentation", "doc", "reference"}:
            return "docs"
        if normalized in {"focus_aid_media", "entertainment"}:
            return "media"
        return normalized or "unknown"
