from domain import Decision

from .decision import ParserGroundingMixin

# DecisionParser là một lớp để phân tích và "ground" (định hình)
#  các quyết định được tạo ra bởi một mô hình ngôn ngữ lớn (LLM)
# thành các đối tượng Decision có thể thực thi trong runtime của ứng dụng.
# Lớp này kế thừa từ ParserGroundingMixin, cung cấp các phương thức để xử lý
# văn bản và đảm bảo rằng các quyết định được tạo ra phù hợp với khả năng
# của hệ thống, đồng thời cung cấp một quyết định dự phòng (fallback)
# để sử dụng khi LLM không thể tạo ra một quyết định hợp lệ.
class DecisionParser(ParserGroundingMixin):
    def __init__(self, fallback: Decision, logger=None):
        self.fallback_decision = fallback
        self.logger = logger
        self.fallback = {
            "text": fallback.text,
            "animation": fallback.animation,
            "extracted_fact": fallback.extracted_fact,
        }

    def _is_climb_request(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(token in lowered for token in ("leo", "treo", "climb", "leo tuong", "climb wall", "bam tuong"))

    def _infer_look_direction(self, text: str) -> int:
        lowered = (text or "").lower()
        left_markers = ("nhin sang trai", "nhìn sang trái", "quay sang trai", "quay sang trái", "quay mat sang trai", "quay mặt sang trái", "ngoanh sang trai", "ngoảnh sang trái", "huong sang trai", "hướng sang trái")
        right_markers = ("nhin sang phai", "nhìn sang phải", "quay sang phai", "quay sang phải", "quay mat sang phai", "quay mặt sang phải", "ngoanh sang phai", "ngoảnh sang phải", "huong sang phai", "hướng sang phải")
        if any(token in lowered for token in left_markers):
            return -1
        if any(token in lowered for token in right_markers):
            return 1
        return 0

    def _is_look_only_request(self, text: str) -> bool:
        lowered = (text or "").lower()
        if self._infer_look_direction(lowered) == 0:
            return False

        move_markers = (
            "di ",
            "đi ",
            "di chuyen",
            "di chuyển",
            "chay ",
            "chạy ",
            "buoc ",
            "bước ",
            "walk",
            "move",
            "run",
            "leo",
            "climb",
        )
        return not any(token in lowered for token in move_markers)

    def _infer_move_direction(self, text: str) -> int:
        lowered = (text or "").lower()
        left_markers = ("di sang trai", "đi sang trái", "move left", "walk left", "run left", "sang trai", "qua trai", "ve ben trai")
        right_markers = ("di sang phai", "đi sang phải", "move right", "walk right", "run right", "sang phai", "qua phai", "ve ben phai")
        if any(token in lowered for token in left_markers):
            return -1
        if any(token in lowered for token in right_markers):
            return 1
        return 0

    def _is_drop_from_climb_request(self, text: str) -> bool:
        lowered = (text or "").lower()
        drop_markers = (
            "tha ra",
            "buong tay",
            "rot xuong",
            "roi xuong",
            "nga xuong",
            "tha tay",
            "thả ra",
            "buông tay",
            "rớt xuống",
            "rơi xuống",
            "ngã xuống",
            "fall down",
            "drop down",
            "let go",
            "fall",
            "drop",
        )
        return self._is_climb_request(lowered) and any(token in lowered for token in drop_markers)

    def _is_reverse_climb_request(self, text: str) -> bool:
        lowered = (text or "").lower()
        descend_markers = (
            "leo xuong",
            "treo xuong",
            "climb down",
            "di xuong",
            "xuong duoi",
            "tro xuong",
            "leo nguoc",
            "leo nguoc lai",
            "quay xuong",
        )
        return self._is_climb_request(lowered) and any(token in lowered for token in descend_markers)

    def _is_climb_top_request(self, text: str) -> bool:
        lowered = (text or "").lower()
        top_markers = (
            "climb top",
            "climb_top",
            "mep tren",
            "len mep",
            "len dinh",
            "toi dinh",
            "tren dinh",
            "dinh cao nhat",
            "cao nhat",
            "top edge",
            "reach the top",
            "highest point",
        )
        return self._is_climb_request(lowered) and any(token in lowered for token in top_markers)

    def _is_top_hold_request(self, text: str) -> bool:
        lowered = (text or "").lower()
        if not self._is_climb_request(lowered):
            return False
        if not any(token in lowered for token in ("mep", "dinh", "top", "cao nhat", "tren cao")):
            return False
        return any(token in lowered for token in ("dung lai", "dừng lại", "o yen", "ở yên", "giu nguyen", "giữ nguyên", "hold", "stay"))

    def _infer_top_platform_target(self, text: str) -> float:
        lowered = (text or "").lower()
        if not self._is_climb_request(lowered):
            return -1.0
        if not any(token in lowered for token in ("mep", "dinh", "top", "cao nhat", "tren cao")):
            return -1.0
        if any(token in lowered for token in ("ra giua", "giua", "middle", "center", "chinh giua")):
            return 0.5
        if any(token in lowered for token in ("ben trai", "sang trai", "mep trai", "left edge", "top left")):
            return 0.12
        if any(token in lowered for token in ("ben phai", "sang phai", "mep phai", "right edge", "top right")):
            return 0.88
        return -1.0

    def _infer_air_swing_from_text(self, text: str) -> str:
        lowered = (text or "").lower()
        if any(token in lowered for token in ("ben trai", "phia trai", "sang trai", "qua trai", "to the left", "move left")):
            return "air_swing_l"
        if any(token in lowered for token in ("ben phai", "phia phai", "sang phai", "qua phai", "to the right", "move right")):
            return "air_swing_r"
        return "air_swing_r"

    def _infer_animation_from_text(self, text: str):
        if self._is_drop_from_climb_request(text):
            return self._infer_air_swing_from_text(text)
        if self._is_climb_top_request(text):
            return "climb_top"
        if self._is_climb_request(text):
            return "climb"
        return super()._infer_animation_from_text(text)

    def _infer_structured_action(self, text: str, user_hint: str = ""):
        structured = super()._infer_structured_action(text, user_hint=user_hint)
        combined = f"{user_hint}\n{text}".lower()
        look_direction = self._infer_look_direction(combined)
        top_platform_target = self._infer_top_platform_target(combined)

        if self._is_look_only_request(combined):
            structured["direction"] = look_direction
            structured["target_x"] = -1.0
            if structured["animation"] == "stand_walk":
                structured["animation"] = ""
            if structured["next_animation"] == "stand":
                structured["next_animation"] = ""
        elif self._is_top_hold_request(combined):
            structured["animation"] = "climb"
            structured["next_animation"] = "climb_top"
            structured["target_x"] = self._infer_top_platform_target(combined)
        elif top_platform_target >= 0.0:
            structured["animation"] = "climb"
            structured["next_animation"] = "climb_top"
            structured["target_x"] = top_platform_target
        elif self._is_drop_from_climb_request(combined):
            structured["animation"] = self._infer_air_swing_from_text(combined)
            structured["next_animation"] = ""
            structured["bounce_action"] = ""
        elif self._is_climb_top_request(combined):
            structured["animation"] = "climb_top"
            structured["next_animation"] = ""
        elif self._is_reverse_climb_request(combined):
            structured["animation"] = "climb"
            structured["next_animation"] = ""
        elif structured["animation"] not in {"climb", "climb_top", "air_swing_l", "air_swing_r"} and self._is_climb_request(combined):
            structured["animation"] = "climb"
        elif structured["animation"] == "stand_walk":
            move_direction = self._infer_move_direction(combined)
            if move_direction != 0:
                structured["direction"] = move_direction
                structured["target_x"] = 0.88 if move_direction > 0 else 0.12
                if not structured["next_animation"]:
                    structured["next_animation"] = "stand"
        return structured

    # _normalize_salvaged_text là một phương thức để làm sạch và
    # chuẩn hóa văn bản được trích xuất từ LLM,
    #  loại bỏ các tiền tố không cần thiết như "Eris:", "Assistant:", "AI:", hoặc "Response:"
    #  để đảm bảo rằng chỉ còn lại phần nội dung chính của văn bản.
    def _is_embodied_command_request(self, text: str) -> bool:
        lowered = (text or "").lower()
        command_markers = (
            "leo",
            "climb",
            "di ",
            "đi ",
            "di chuyen",
            "di chuyển",
            "nhin",
            "nhìn",
            "quay mat",
            "quay mặt",
            "sang trai",
            "sang phai",
            "ben trai",
            "ben phai",
            "mep",
            "dinh",
            "top",
            "air_swing",
        )
        return super()._is_embodied_command_request(text) or any(token in lowered for token in command_markers)

    def _should_suppress_salvaged_text(self, finalized: dict, user_hint: str) -> bool:
        if finalized.get("parse_mode") not in {"salvaged_text", "lax_json"}:
            return False
        if not self._is_embodied_command_request(user_hint):
            return False

        animation = finalized.get("animation", "")
        direction = int(finalized.get("direction", 0) or 0)
        target_x = float(finalized.get("target_x", -1.0) or -1.0)
        next_animation = finalized.get("next_animation", "")

        return (
            animation in {"stand_walk", "climb", "climb_top", "air_swing_l", "air_swing_r"}
            or next_animation in {"climb_top", "stand"}
            or direction != 0
            or target_x >= 0.0
        )

    def _finalize_decision(self, decision: dict, user_hint: str = ""):
        finalized = super()._finalize_decision(decision, user_hint=user_hint)
        if self._should_suppress_salvaged_text(finalized, user_hint):
            finalized["text"] = ""
        return Decision(**finalized)

    def parse(self, raw_text: str, user_hint: str = "") -> Decision:
        return self.parse_ai_json(raw_text, user_hint=user_hint)

    def build_chat_user_message(self, *, user_message: str, last_chat_answer: str) -> str:
        if self._is_follow_up_question(user_message) and last_chat_answer:
            return (
                "Người dùng đang hỏi tiếp về chính câu trả lời ngay trước đó.\n"
                f"Câu trả lời trước của bạn: {last_chat_answer}\n"
                f"Câu hỏi mới: {user_message}"
            )
        return f"Người dùng: {user_message}"
