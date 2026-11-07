# -*- coding: utf-8 -*-
"""Format kết quả action theo bằng chứng thực thi, không theo độ tự tin của câu."""

from __future__ import annotations

from actions.contracts import ActionExecutionResult, ActionExecutionStatus


def _finish(result: ActionExecutionResult, text: str) -> str:
    result.publicized = True
    return str(text or "").strip()


def public_action_text(result: ActionExecutionResult) -> str:
    if not result.success or result.status == ActionExecutionStatus.FAILED:
        return _finish(result, result.message or "Action chưa thực thi được.")

    action = result.action_name
    cognitive_actions = {
        "review_discourse_claim", "inspect_unresolved_questions",
        "inspect_memory_candidates", "compare_recent_turns",
        "inspect_social_state", "detect_internal_contradictions",
        "summarize_open_loops", "inspect_capability_health",
    }
    if action in cognitive_actions:
        # Kết quả đã được verifier nội bộ xác nhận. Không format lại thành câu
        # chung chung rồi làm mất claim/evidence vừa kiểm tra.
        return _finish(result, result.message)
    if result.observed:
        if action == "set_volume":
            level = result.data.get("observed_level") or result.data.get("level")
            if level:
                return _finish(result, f"Âm lượng hiện ở khoảng {level} phần trăm.")
            if result.data.get("muted") == "true":
                return _finish(result, "Âm lượng đã được mute.")
        if action == "focus_window":
            title = result.data.get("observed_title") or result.data.get("matched_title") or result.action_input
            return _finish(result, f"Cửa sổ {title} đang ở foreground.")
        if action == "copy_text":
            return _finish(result, "Nội dung đã có trong clipboard.")
        return _finish(result, result.message or f"Action {action} đã được xác nhận hoàn tất.")

    # Read-only action có response trực tiếp, nên result đã được mặc định observed.
    if action == "get_local_time":
        return _finish(result, result.message)
    if action == "get_now_playing":
        title = result.data.get("title", "")
        artist = result.data.get("artist", "")
        if title and artist:
            return _finish(result, f"Tôi thấy bài đang phát là {title} của {artist}.")
        if title:
            return _finish(result, f"Tôi thấy bài đang phát là {title}.")
        return _finish(result, result.message)
    if action == "get_weather":
        return _finish(result, result.message)

    # Side-effect chỉ có dispatch evidence. Wording không được vượt quá bằng chứng.
    if action == "set_volume":
        level = result.data.get("level", result.action_input or "mức yêu cầu")
        return _finish(result, f"Tôi đã gửi yêu cầu chỉnh âm lượng về khoảng {level} phần trăm.")
    if action == "open_url":
        return _finish(result, f"Tôi đã gửi yêu cầu mở {result.data.get('url', result.action_input)}.")
    if action == "focus_window":
        target = result.data.get("matched_title", result.action_input)
        return _finish(result, f"Tôi đã gửi yêu cầu đưa cửa sổ {target} lên trước.")
    if action == "play_pause_media":
        return _finish(result, "Tôi đã gửi phím điều khiển media.")
    if action == "copy_text":
        return _finish(result, "Tôi đã gửi yêu cầu ghi nội dung vào clipboard.")
    if action == "launch_app":
        target = result.data.get("command", result.action_input)
        return _finish(result, f"Tôi đã gửi yêu cầu mở {target}.")
    return _finish(result, result.message or "Tôi đã gửi yêu cầu thực thi action.")


__all__ = ["public_action_text"]
