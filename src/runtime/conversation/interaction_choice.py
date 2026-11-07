"""Root-owned decision summaries for learning; never a dialogue selector or filter."""
from __future__ import annotations

from typing import Any, Mapping


INTERACTION_CHOICE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["situation", "strategy", "context", "expected_effect", "listener_action"],
    "properties": {key: {"type": "string"} for key in (
        "situation", "strategy", "context", "expected_effect", "listener_action", "alternative", "uncertainty"
    )},
}

INTERACTION_CHOICE_PROMPT = (
    "\nKhi finalize move, ghi bản tóm tắt lựa chọn interaction_choice trước intent/say: "
    "situation là điều đang diễn ra giữa hai người từ bằng chứng hiện tại; "
    "strategy là tên ngắn, nhất quán do bạn tự đặt cho cách tiếp cận; context là hoàn cảnh áp dụng; "
    "expected_effect mô tả tác động dự kiến lên người nghe; listener_action mô tả việc mà chính "
    "lời say đề nghị người nghe phải làm tiếp (nếu có), để rỗng nếu không có; "
    "alternative và uncertainty ghi ngắn "
    "phương án khác hoặc điều chưa chắc nếu hữu ích. Đây là bản tóm tắt quyết định, không phải "
    "chuỗi suy nghĩ, thang điểm cảm xúc hoặc điều đã biết chắc về user. "
    "Cân nhắc cả điều người kia quan tâm lẫn gánh nặng mà lời đáp có thể tạo ra, rồi tự chọn "
    "cách ứng xử theo bằng chứng, cá tính và giá trị của mình. Tự kiểm lựa chọn có thực sự được "
    "thể hiện trong say hay chỉ được tuyên bố trong intent. Không có chiến lược luôn thắng: "
    "hỏi, nghe, cùng đùa, đưa ý kiến, phản biện hoặc im đều tùy hoàn cảnh; user có thể đổi mục đích. "
    "Lời đơn giản dùng bản ghi ngắn tương ứng. Kinh nghiệm pending/strategy là bằng chứng "
    "có phạm vi để diễn giải lại, không phải luật hoặc nghĩa vụ giữ một cách đáp. "
    "Lời đã phát và phản ứng thật mới xác nhận tác động; expected_effect không phải kết quả."
)


def normalize_interaction_choice(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    limits = {"situation": 280, "strategy": 100, "context": 80,
              "expected_effect": 240, "listener_action": 180, "alternative": 240, "uncertainty": 180}
    result = {key: " ".join(value.split())[:limit] for key, limit in limits.items()
              if isinstance((value := raw.get(key)), str) and value.strip()}
    if not all(result.get(key) for key in ("situation", "strategy", "context", "expected_effect")):
        return {}
    if not isinstance(raw.get("listener_action"), str):
        return {}
    result["listener_action"] = " ".join(raw["listener_action"].split())[:180]
    return result


def record_delivered_choice(host: Any, *, choice: Mapping[str, Any], delivery_id: str,
                            session_id: str, turn_seq: int, goal: str, assistant_text: str,
                            user_text: str = "") -> bool:
    """Called only by the terminal delivered callback, never by model preparation."""
    normalized = normalize_interaction_choice(choice)
    if not delivery_id or not session_id.strip() or turn_seq <= 0 or not (normalized or goal.strip()):
        return False
    from runtime.ai_orchestrated.contracts import StateEvent
    from runtime.ai_orchestrated.identity import PendingStrategyUse
    reducer = getattr(host, "ai_state_reducer", None)
    submit = getattr(reducer, "submit", None)
    if not callable(submit):
        return False
    pending = PendingStrategyUse(
        strategy_use_id=str(delivery_id)[:100], turn_seq=turn_seq,
        # Without an assessment keep the authored action as an unclassified
        # experience, not a guessed social strategy or a global policy reward.
        strategy=normalized.get("strategy", "root_move").lower(), goal=str(goal)[:220],
        context=(normalized.get("context", "unclassified").lower(),), assistant_text=str(assistant_text)[:420],
        session_id=str(session_id)[:180], situation=normalized.get("situation", str(user_text)[:280]),
        expected_effect=normalized.get("expected_effect", ""),
        listener_action=normalized.get("listener_action", ""),
    )
    return bool(submit(StateEvent.from_pending_strategy(pending)))


def record_user_reaction(host: Any, *, session_id: str, turn_seq: int,
                         turn_ref: str, user_text: str, now: float,
                         previous_assistant_turn_seq: int) -> bool:
    """Persist evidence, not an emotional interpretation, through the single writer."""
    if not turn_ref or turn_seq <= 0 or previous_assistant_turn_seq <= 0 or not user_text.strip():
        return False
    from runtime.ai_orchestrated.contracts import StateEvent
    submit = getattr(getattr(host, "ai_state_reducer", None), "submit", None)
    if not callable(submit):
        return False
    return bool(submit(StateEvent(
        kind="strategy_reaction", turn_seq=turn_seq, turn_id=turn_ref,
        payload={"session_id": str(session_id)[:180], "reaction_text": user_text[:2000],
                 "reaction_ref": turn_ref[:140], "received_at": now,
                 "previous_assistant_turn_seq": previous_assistant_turn_seq},
        source="conversation_user_ingress",
    )))
