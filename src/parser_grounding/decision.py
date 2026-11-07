import json
import os
import re
from llm.json_sanitizer import strip_reasoning_envelope
import unicodedata

from actions.contracts import VALID_SYSTEM_ACTIONS
from failure_meta import normalize_failure_meta

VALID_ANIMATIONS = {
    "stand", "sit_down", "sit", "sit_up", "read", "stand_walk",
    "piano", "clone", "divine", "hop", "jump", "sleep", "sad", "food",
    "affection", "climb", "climb_top", "air_swing_l", "air_swing_r",
}


def _no_fallback_mode_enabled() -> bool:
    raw = os.getenv("ERIS_NO_FALLBACK_MODE", "")
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}

# Model thường mô tả biểu cảm bằng từ ngữ semantic (soft_smile, subtle_apology)
# trong field ``animation`` dù Godot chỉ hiểu pose vật lý như stand/read. Thay vì
# ném toàn bộ về ``stand`` rồi làm mất diễn xuất, parser hạ alias đó sang các
# side-channel stage có cấu trúc. Base animation vẫn là stand để frontend cũ sống.
_SEMANTIC_ANIMATION_STAGE_ALIASES: dict[str, dict[str, str]] = {
    "soft_smile": {"nonverbal_cue": "small_smile", "stage_motion": "small_smile", "delivery_hint": "warm_soft"},
    "subtle_apology": {"nonverbal_cue": "nod_small", "stage_motion": "blink_slow", "delivery_hint": "low_quiet"},
    "soft_idle": {"stage_motion": "blink_slow", "delivery_hint": "warm_soft"},
    "idle_warm": {"stage_motion": "small_smile", "delivery_hint": "warm_soft"},
    "softened_gaze": {"stage_motion": "lean_in_small", "delivery_hint": "warm_soft"},
    "gentle_pause": {"nonverbal_cue": "soft_breath", "delivery_hint": "low_quiet"},
    "curious": {"nonverbal_cue": "tilt_head", "stage_motion": "tilt_head", "delivery_hint": "curious_soft"},
}

# ParserGroundingMixin chỉ chuẩn hóa cấu trúc Decision và metadata sân khấu.
# Public text thuộc quyền của model chính; parser không còn được phép viết lại
# câu chỉ vì nó mô tả một hành động ngoài capability runtime. Action executor vẫn
# tự xác minh system_action riêng, còn lời thoại/ẩn dụ/đấu khẩu được giữ nguyên.


_PERFORMANCE_OVERCLAIM_VERBS = [
    "khóa", "ngắt", "đóng", "mở", "tắt", "bật", "tăng", "giảm",
    "gọi", "đặt", "hủy", "xóa", "ép", "cưỡng chế", "ghi đè",
    "can thiệp", "điều khiển", "chiếm hữu", "chặn", "phát", "kích hoạt",
    "thiết lập", "reset", "shutdown", "hibernate", "suspend",
    "thu gọn", "dọn", "gom", "thu hồi", "vô hiệu hóa", "disable",
]

_PERFORMANCE_OVERCLAIM_TARGETS = [
    "bàn phím", "chuột", "màn hình", "tab", "trình duyệt", "cửa sổ",
    "mạng", "wifi", "internet", "hệ điều hành", "windows", "thiết bị",
    "đèn", "nhiệt độ phòng", "phòng", "nhà thông minh", "robot",
    "thuốc", "đơn hàng", "ví điện tử", "âm thanh tần số", "loa",
    "phần cứng sinh học", "thiết bị vật lý",
    "ứng dụng", "app", "mạng xã hội",
]

class ParserGroundingMixin:
    def _normalize_keyword_text(self, text: str) -> str:
        lowered = unicodedata.normalize("NFD", (text or "").lower())
        lowered = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
        lowered = re.sub(r"[^a-z0-9#]+", " ", lowered)
        return " ".join(lowered.split())

    def _contains_keyword(self, text: str, keyword: str) -> bool:
        normalized_text = self._normalize_keyword_text(text)
        normalized_keyword = self._normalize_keyword_text(keyword)
        if not normalized_text or not normalized_keyword:
            return False
        return f" {normalized_keyword} " in f" {normalized_text} "

    def _contains_any_keyword(self, text: str, keywords) -> bool:
        return any(self._contains_keyword(text, keyword) for keyword in keywords)

    def _preview_raw_response(self, raw_text: str):
        preview = (raw_text or "").replace("\n", "\\n").strip()
        if len(preview) > 240:
            preview = preview[:240] + "...(truncated)"
        return preview

    def _normalize_debug_trace(self, value: str):
        text = self._normalize_salvaged_text(str(value or ""))
        return text[:240]

    def _build_parser_debug_trace(self, *, parse_mode: str, raw_text: str, reason: str, text: str = "", animation: str = ""):
        parts = [f"parse_mode={parse_mode}", reason]
        if animation:
            parts.append(f"animation={animation}")
        if text:
            parts.append(f"text={text[:80]}")
        parts.append(f"raw={self._preview_raw_response(raw_text)}")
        return " | ".join(part for part in parts if part)

    def _collect_performance_overclaims(self, text: str):
        """Nhận diện thụ động lời khoe quyền lực để ghi DeceptionLedger.

        Đây không phải guard: kết quả không được phép sửa, chặn hay làm rỗng
        public text. Nó chỉ giúp đấu khẩu có chủ ý vẫn được ghi như một lời diễn.
        """

        matches = []
        previous_unit = ""
        for unit in self._split_into_claim_units(text):
            lowered = str(unit or "").lower()
            has_verb = any(verb in lowered for verb in _PERFORMANCE_OVERCLAIM_VERBS)
            has_target = self._line_mentions_control_target(lowered)
            previous_has_target = self._line_mentions_control_target(previous_unit)
            referential = any(token in lowered for token in ("chúng", "nó", "đó", "đám đó", "toàn bộ chúng"))
            if self._line_has_strong_control_fantasy(lowered) or (has_verb and has_target) or (
                has_verb and previous_has_target and referential
            ):
                matches.append(unit)
            previous_unit = unit
        return matches

    def _build_parser_trace_summary(
        self,
        *,
        parse_mode: str,
        original_text: str,
        final_text: str,
        final_animation: str,
        user_hint: str = "",
        grounded_changed: bool = False,
    ):
        parts = [f"parse_mode={parse_mode}"]

        # Parser chỉ sanitize schema. Không còn capability rewrite hay capability
        # audit chen vào trace của câu public.
        parts.append("public_text=passthrough")

        return " | ".join(parts)

    def _log(self, level: str, message: str):
        logger = getattr(self, "logger", None)
        if logger is not None:
            getattr(logger, level)(message)
        else:
            safe_message = message.encode("ascii", "backslashreplace").decode("ascii")
            print(safe_message, flush=True)

    def _extract_first_json_object(self, text: str):
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escaped = False

        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:idx + 1]

        return None

    def _extract_first_json_candidate(self, text: str) -> str:
        start = text.find("{")
        if start == -1:
            return ""
        return text[start:].strip()

    def _repair_json_candidate(self, text: str) -> str:
        candidate = str(text or "").strip()
        if not candidate:
            return ""
        candidate = (
            candidate.replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2018", "'")
            .replace("\u2019", "'")
        )
        candidate = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", candidate)
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)

        depth = 0
        in_string = False
        escaped = False
        for ch in candidate:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
        if depth > 0:
            candidate += "}" * depth
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
        return candidate.strip()

    def _is_embodied_command_request(self, text: str) -> bool:
        normalized = self._normalize_keyword_text(text)
        if not normalized:
            return False

        command_phrases = [
            "đi dạo",
            "tản bộ",
            "di chuyển",
            "đi qua",
            "đi sang phải",
            "đi sang trái",
            "sang phải",
            "sang trái",
            "qua phải",
            "qua trái",
            "move",
            "walk",
            "run",
            "nhìn sang phải",
            "nhìn sang trái",
            "quay mặt",
            "quay sang",
            "hướng sang",
            "ra giữa",
            "về giữa",
            "center",
            "middle",
            "đọc",
            "theo dõi",
            "giám sát",
            "monitor",
            "xem tài liệu",
            "nghiên cứu",
            "sit down",
            "ngồi xuống",
            "sit",
            "ngủ đi",
            "nghỉ ngủ",
            "sleep",
            "ăn uống",
            "food",
            "ôm",
            "vuốt ve",
            "ân cần",
            "chơi đàn",
            "đánh đàn",
            "piano",
            "leo",
            "climb",
            "trèo",
            "divine",
            "phân tách",
        ]
        return self._contains_any_keyword(normalized, command_phrases)

    def _infer_animation_from_text(self, text: str):
        return "stand"

    def _infer_structured_action(self, text: str, user_hint: str = ""):
        combined = f"{user_hint}\n{text}".lower()
        direction = 0
        target_x = -1.0
        bounce_action = ""
        next_animation = ""
        animation = ""

        if any(token in combined for token in ["bên phải", "ben phai", "phía phải", "phia phai", "qua phải", "qua ben phai", "sang phải", "sang ben phai", "đi phải", "di phai", "move right", "to the right"]):
            direction = 1
            target_x = 0.88
        elif any(token in combined for token in ["bên trái", "ben trai", "phía trái", "phia trai", "qua trái", "qua ben trai", "sang trái", "sang ben trai", "đi trái", "di trai", "move left", "to the left"]):
            direction = -1
            target_x = 0.12
        elif any(token in combined for token in ["ra giữa", "ra giua", "về giữa", "ve giua", "ở giữa", "o giua", "chính giữa", "chinh giua", "center", "middle"]):
            target_x = 0.5

        if any(token in combined for token in ["đi ", "di ", "đi qua", "di qua", "qua ", "sang ", "move ", "walk", "di chuyển", "di chuyen"]):
            animation = "stand_walk"

        action_keywords = [
            ("piano", ["piano", "chơi đàn", "choi dan", "đánh đàn", "danh dan"]),
            ("read", ["đọc", "doc", "nghiên cứu", "nghien cuu", "xem tài liệu", "xem tai lieu", "theo dõi", "theo doi", "giám sát", "giam sat"]),
            ("sit_down", ["ngồi xuống", "ngoi xuong", "sit down"]),
            ("sit", ["ngồi", "ngoi", "ngồi nghỉ", "ngoi nghi"]),
            ("sleep", ["ngủ", "ngu", "ngủ đi", "ngu di", "nghỉ ngủ", "nghi ngu", "sleep"]),
            ("food", ["ăn đi", "đang ăn", "muốn ăn", "uong", "uống", "đồ ăn", "do an", "food", "eat"]),
            ("affection", ["ôm", "om", "vuốt ve", "vuot ve", "ân cần", "an can"]),
            ("sad", ["buồn", "buon", "ủ rũ", "u ru"]),
            ("divine", ["divine", "phân tách", "phan tach"]),
            ("climb", ["leo", "trèo", "treo", "climb"]),
        ]

        for action_name, keywords in action_keywords:
            if self._contains_any_keyword(combined, keywords):
                if animation == "stand_walk":
                    next_animation = action_name
                else:
                    animation = action_name
                break

        if animation == "stand_walk" and not next_animation:
            next_animation = "stand"

        return {
            "animation": animation,
            "direction": direction,
            "target_x": target_x,
            "bounce_action": bounce_action,
            "next_animation": next_animation,
        }


    def _patch_missing_decision_fields(self, payload: dict, *, parse_mode: str, user_hint: str = "") -> tuple[dict, bool]:
        """Fill transport defaults without deriving semantics from user keywords."""
        patched = self._apply_cognition_action_defaults(dict(payload or {}), user_hint="")
        fields_patched = False

        text = self._normalize_salvaged_text(str(patched.get("text", "") or ""))
        patched["text"] = text
        default_fields = {
            "intent": "",
            "action": "",
            "reasoning_tags": (),
            "should_speak": bool(text),
            "confidence": 0.0,
            "animation": self.fallback["animation"],
            "extracted_fact": "",
            "direction": 0,
            "next_animation": "",
            "target_x": -1.0,
            "bounce_action": "",
            "debug_trace": "",
            "system_action": "",
            "action_input": "",
            "motion_intent": "",
            "motion_target_x": -1.0,
            "motion_facing": 0,
            "delivery_hint": "",
            "nonverbal_cue": "",
            "stage_motion": "",
            "performance_hint": "",
            "display_language": "vi",
            "speech_text": "",
            "speech_language": "",
            "performance_intensity": 0.5,
            "delivery_priority": "normal",
            "interruptible": True,
            "utterance_turn_id": "",
            "parse_mode": parse_mode,
        }
        structural_fields = {"should_speak", "animation"}
        for key, default in default_fields.items():
            if key not in patched or patched.get(key) is None:
                patched[key] = default
                if key in structural_fields:
                    fields_patched = True
        return patched, fields_patched

    def _finalize_loaded_json_payload(self, payload, *, parse_mode: str, user_hint: str = ""):
        data = self._normalize_decision_field_keys(payload)
        if "response" in data and isinstance(data["response"], list) and len(data["response"]) > 0:
            data = self._normalize_decision_field_keys(data["response"][0])
        text_field = str(data.get("text", "") or "")
        nested_fields = self._try_extract_nested_decision_from_text(text_field)
        if nested_fields is not None:
            merged = dict(data)
            for key, value in nested_fields.items():
                if key == "debug_trace" and str(merged.get("debug_trace", "")).strip():
                    continue
                merged[key] = value
            if "should_speak" not in merged and "text" in nested_fields and not str(nested_fields.get("text", "") or "").strip():
                merged["should_speak"] = False
            data = merged
        else:
            inline_fields = self._try_extract_inline_decision_fields_from_text(text_field)
            if inline_fields is not None:
                merged = dict(data)
                for key, value in inline_fields.items():
                    if key == "debug_trace" and str(merged.get("debug_trace", "")).strip():
                        continue
                    merged[key] = value
                if "should_speak" not in merged and "text" in inline_fields and not str(inline_fields.get("text", "") or "").strip():
                    merged["should_speak"] = False
                data = merged
        data, fields_patched = self._patch_missing_decision_fields(
            data,
            parse_mode=parse_mode,
            user_hint=user_hint,
        )

        text = str(data.get("text", self.fallback["text"]))
        raw_animation = str(data.get("animation", self.fallback["animation"]) or "").strip().lower()
        semantic_stage = _SEMANTIC_ANIMATION_STAGE_ALIASES.get(raw_animation, {})
        for field_name in ("delivery_hint", "nonverbal_cue", "stage_motion"):
            if semantic_stage.get(field_name) and not str(data.get(field_name, "") or "").strip():
                data[field_name] = semantic_stage[field_name]
        animation = self._sanitize_animation(
            raw_animation,
            fallback_text=text,
            user_hint=user_hint,
        )
        fact = str(data.get("extracted_fact", "")).strip()
        direction = self._sanitize_direction(data.get("direction", 0))
        next_anim = str(data.get("next_animation", "")).strip()
        bounce_action = str(data.get("bounce_action", "")).strip()
        target_x = self._sanitize_target_x(data.get("target_x", -1.0))

        final_parse_mode = "hard_json_repair" if fields_patched and parse_mode == "json" else parse_mode
        return self._finalize_decision({
            "text": text,
            "intent": data.get("intent", ""),
            "action": data.get("action", ""),
            "reasoning_tags": data.get("reasoning_tags", ()),
            "should_speak": data.get("should_speak", bool(text)),
            "confidence": data.get("confidence", 0.0),
            "animation": animation,
            "extracted_fact": fact,
            "direction": direction,
            "next_animation": next_anim,
            "target_x": target_x,
            "bounce_action": bounce_action,
            "debug_trace": data.get("debug_trace", data.get("_thought", "")),
            "parse_mode": final_parse_mode,
            "system_action": data.get("system_action", ""),
            "action_input": data.get("action_input", ""),
            "motion_intent": data.get("motion_intent", ""),
            "motion_target_x": data.get("motion_target_x", target_x),
            "motion_facing": data.get("motion_facing", direction),
            "delivery_hint": data.get("delivery_hint", ""),
            "nonverbal_cue": data.get("nonverbal_cue", ""),
            "stage_motion": data.get("stage_motion", ""),
            "performance_hint": data.get("performance_hint", ""),
            "display_language": data.get("display_language", "vi"),
            "speech_text": data.get("speech_text", ""),
            "speech_language": data.get("speech_language", ""),
            "performance_intensity": data.get("performance_intensity", 0.5),
            "delivery_priority": data.get("delivery_priority", "normal"),
            "interruptible": data.get("interruptible", True),
            "utterance_turn_id": data.get("utterance_turn_id", ""),
            "failure_meta": data.get("failure_meta"),
        }, user_hint=user_hint)

    def _try_parse_hard_json_repair(self, text: str, user_hint: str = ""):
        candidate = self._extract_first_json_candidate(text)
        if not candidate or ":" not in candidate:
            return None

        attempts: list[str] = []
        exact_object = self._extract_first_json_object(candidate)
        if exact_object:
            attempts.append(exact_object)
        attempts.append(self._repair_json_candidate(candidate))

        seen: set[str] = set()
        for attempt in attempts:
            attempt = str(attempt or "").strip()
            if not attempt or attempt in seen:
                continue
            seen.add(attempt)
            try:
                data = json.loads(attempt)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            return self._finalize_loaded_json_payload(
                data,
                parse_mode="hard_json_repair",
                user_hint=user_hint,
            )
        return None

    def _normalize_salvaged_text(self, text: str):
        # Nếu model trả về block kiểu "text: ... animation: ...",
        # thì chỉ giữ phần từ sau "text:" tới trước field tiếp theo để tránh spam meta-field.
        m = re.search(r"\btext\s*[:=]\s*", text, flags=re.IGNORECASE)
        if m:
            after = text[m.end():]
            field_pattern = r"\b(animation|direction|target_x|bounce_action|extracted_fact|debug_trace|next_animation|system_action|action_input|motion_intent|motion_target_x|motion_facing)\s*[:=]"
            m2 = re.search(field_pattern, after, flags=re.IGNORECASE)
            if m2:
                text = after[: m2.start()]
            else:
                text = after

        text = re.sub(r"\|\s*animation\s*=.*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("\\n", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            text = text[1:-1].strip()
        text = re.sub(r"^(trả lời trước|tra loi truoc)\s*:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^(eris|assistant|ai|response)\s*:\s*", "", text, flags=re.IGNORECASE)
        text = self._cut_inline_metadata_from_public_text(text)
        return text

    def _repair_sentence_boundaries(self, text: str) -> str:
        repaired = (text or "").strip()
        if not repaired:
            return repaired
        repaired = re.sub(
            r"(?<=[a-zàáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ0-9])\s+(?=(Tôi|Hãy|Đừng|Nếu|Nhưng|Mọi|Đó|Sự|Trạng thái)\b)",
            ". ",
            repaired,
        )
        repaired = re.sub(r"\.\s+\.", ".", repaired)
        return repaired.strip()

    def _normalize_decision_field_keys(self, value):
        if isinstance(value, list):
            return [self._normalize_decision_field_keys(item) for item in value]
        if not isinstance(value, dict):
            return value

        normalized = {}
        for key, item in value.items():
            normalized_key = key.strip() if isinstance(key, str) else key
            normalized_item = self._normalize_decision_field_keys(item)
            if normalized_key in normalized and normalized_key != key:
                continue
            normalized[normalized_key] = normalized_item
        return normalized

    def _coerce_bool(self, value, *, fallback: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value != 0
        text = str(value or "").strip().lower()
        if text in {"true", "yes", "y", "1", "speak", "noi", "nói"}:
            return True
        if text in {"false", "no", "n", "0", "silent", "silence", "im", "im_lang", "im lặng"}:
            return False
        return fallback

    def _coerce_confidence(self, value, *, fallback: float = 0.0) -> float:
        if isinstance(value, bool):
            return fallback
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return fallback
        return max(0.0, min(1.0, confidence))

    def _coerce_reasoning_tags(self, value) -> tuple[str, ...]:
        if isinstance(value, str):
            raw_items = re.split(r"[,|;/]+", value)
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []

        tags: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            tag = str(item or "").strip().lower()
            tag = unicodedata.normalize("NFD", tag)
            tag = "".join(ch for ch in tag if unicodedata.category(ch) != "Mn")
            tag = re.sub(r"[^a-z0-9_\-\s]+", "", tag)
            tag = re.sub(r"\s+", "_", tag).strip("_")
            if not tag or tag in seen:
                continue
            tags.append(tag[:40])
            seen.add(tag)
            if len(tags) >= 8:
                break
        return tuple(tags)

    def _action_field(self, action, *names, default=None):
        if not isinstance(action, dict):
            return default
        for name in names:
            if name in action:
                return action.get(name)
        return default

    def _apply_cognition_action_defaults(self, data: dict, user_hint: str = "") -> dict:
        """Map explicit legacy action fields, without guessing from prose."""
        action = data.get("action")
        if not isinstance(action, dict):
            return data

        merged = dict(data)
        if "animation" not in merged or not str(merged.get("animation", "")).strip():
            merged["animation"] = self._action_field(action, "animation", "pose", default="stand")
        if "direction" not in merged:
            merged["direction"] = self._action_field(action, "direction", "facing", default=0)
        if "target_x" not in merged:
            merged["target_x"] = self._action_field(action, "target_x", "x", default=-1.0)
        if "next_animation" not in merged:
            merged["next_animation"] = self._action_field(action, "next_animation", default="")
        if "bounce_action" not in merged:
            merged["bounce_action"] = self._action_field(action, "bounce_action", default="")
        if "motion_intent" not in merged:
            merged["motion_intent"] = self._action_field(action, "motion_intent", default="")
        if "motion_target_x" not in merged:
            merged["motion_target_x"] = self._action_field(action, "motion_target_x", "target_x", "x", default=-1.0)
        if "motion_facing" not in merged:
            merged["motion_facing"] = self._action_field(action, "motion_facing", "direction", "facing", default=0)

        system_action = self._action_field(action, "system_action", "systemAction", "tool", default="")
        if system_action and not str(merged.get("system_action", "")).strip():
            merged["system_action"] = system_action
        action_input = self._action_field(action, "action_input", "input", "query", "url", "argument", default="")
        if action_input and not str(merged.get("action_input", "")).strip():
            merged["action_input"] = action_input
        return merged

    def _clean_inline_field_value(self, value: str) -> str:
        cleaned = str(value or "")
        for _ in range(4):
            before = cleaned
            cleaned = cleaned.strip()
            cleaned = cleaned.rstrip(",;").strip()
            if cleaned.endswith("}") and cleaned.count("{") < cleaned.count("}"):
                cleaned = cleaned[:-1].strip()
            if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
                cleaned = cleaned[1:-1].strip()
            if cleaned.endswith('"') and cleaned.count('"') % 2 == 1:
                cleaned = cleaned[:-1].rstrip(" ,;")
            if cleaned.endswith("'") and cleaned.count("'") % 2 == 1:
                cleaned = cleaned[:-1].rstrip(" ,;")
            if cleaned == before:
                break
        cleaned = cleaned.strip()
        if cleaned.lower() in {"null", "none", "nil"} or cleaned in {"[]", "{}"}:
            return ""
        return cleaned

    def _cut_inline_metadata_from_public_text(self, text: str) -> str:
        """Remove leaked decision-schema tail from a public text field."""
        cleaned = str(text or "")
        if not cleaned:
            return ""
        # Common failure: text='...', 'intent': 'x', 'action': ... after
        # LlmService wrapped a truncated JSON blob into a JSON text field.
        metadata_pattern = re.compile(
            r"(?P<head>.*?)(?:[\"']?\s*,\s*)[\"'](?:intent|action|reasoning_tags|should_speak|confidence|animation|debug_trace|system_action)[\"']\s*:",
            flags=re.IGNORECASE | re.DOTALL,
        )
        match = metadata_pattern.match(cleaned)
        if match:
            cleaned = match.group("head")
        # Also catch prose-shaped payload leaks: ` intent: ... action: ...`
        cleaned = re.split(
            r"\s+(?:intent|action|reasoning_tags|should_speak|confidence|debug_trace|system_action)\s*[:=]",
            cleaned,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return self._clean_inline_field_value(cleaned)

    def _looks_like_prompt_or_contract_leak_text(self, text: str) -> bool:
        raw = str(text or "")
        lowered = raw.lower()
        markers = (
            "speech intent:",
            "speech evidence",
            "persona:",
            "constraints:",
            "return only",
            "valid json object",
            "ghi chú nền",
            "gợi ý điều phối",
            "critical output contract",
            "runtime requirement for this call",
            "llm final prompt",
        )
        hits = sum(1 for marker in markers if marker in lowered)
        return hits >= 2 or (hits >= 1 and raw.count("*   ") >= 1)

    def _looks_like_non_json_analysis_or_debug_text(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        lowered = raw.lower()
        normalized = self._normalize_keyword_text(raw)
        markers = (
            "user input:",
            "context:",
            "goal:",
            "eris's current state",
            "speech intent:",
            "speech evidence",
            "constraints:",
            "return only",
            "valid json object",
            "critical output contract",
            "runtime requirement for this call",
            "llm final prompt",
            "reasoning_tags",
            "should_speak",
            "debug_trace",
            "parse_mode",
            "system_action",
            "action_input",
            "correction_triggers_repair",
            "relational_repair_rule",
        )
        hits = sum(1 for marker in markers if marker in lowered or self._normalize_keyword_text(marker) in normalized)
        instruction_shape = sum(
            1
            for marker in (
                "you are eris",
                "do not",
                "must not",
                "only use",
                "data bans",
                "hard bans",
                "internal state",
            )
            if marker in lowered or self._normalize_keyword_text(marker) in normalized
        )
        return hits >= 2 or (hits >= 1 and instruction_shape >= 1)

    def _non_json_semantic_fallback_payload(self, *, parse_mode: str, raw_text: str, reason: str) -> dict:
        # Protocol/debug leaks are blocked as transport failures. The parser does
        # not compose an in-character apology or repair line.
        return {
            **self.fallback,
            "text": "",
            "should_speak": False,
            "debug_trace": self._build_parser_debug_trace(
                parse_mode=parse_mode,
                raw_text=raw_text,
                reason=reason,
                text="blocked_protocol_surface",
                animation=self.fallback["animation"],
            ),
            "parse_mode": parse_mode,
            "intent": "",
            "system_action": "",
            "action_input": "",
        }

    # _try_extract_nested_decision_from_text là một phương thức để cố gắng trích xuất một đối tượng JSON
    # lồng nhau từ trường text của quyết định.
    def _try_extract_inline_decision_fields_from_text(self, text: str):
        candidate = (text or "").strip()
        if not candidate:
            return None

        fields = [
            "text",
            "intent",
            "action",
            "reasoning_tags",
            "should_speak",
            "confidence",
            "animation",
            "direction",
            "target_x",
            "bounce_action",
            "extracted_fact",
            "debug_trace",
            "next_animation",
            "system_action",
            "action_input",
            "motion_intent",
            "motion_target_x",
            "motion_facing",
            "delivery_hint",
            "nonverbal_cue",
            "stage_motion",
            "performance_hint",
        ]
        field_group = "|".join(re.escape(field) for field in fields)
        pattern = re.compile(
            rf"(?:(?<=^)|(?<=[\s,;{{]))[\"']?\s*(?P<field>{field_group})\s*[\"']?\s*[:=]\s*",
            flags=re.IGNORECASE,
        )
        matches = list(pattern.finditer(candidate))
        if len(matches) < 2:
            return None

        extracted = {}
        for index, match in enumerate(matches):
            field = match.group("field").lower()
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(candidate)
            raw_value = self._clean_inline_field_value(candidate[start:end])
            extracted[field] = raw_value

        if "text" not in extracted:
            return None
        return extracted

    def _sanitize_direction(self, value) -> int:
        try:
            direction = int(value)
        except (TypeError, ValueError):
            return 0
        return direction if direction in {-1, 0, 1} else 0

    def _sanitize_target_x(self, value, *, fallback: float = -1.0) -> float:
        try:
            target_x = float(value)
        except (TypeError, ValueError):
            return fallback

        if target_x == -1.0:
            return -1.0
        if 0.0 <= target_x <= 1.0:
            return target_x
        return fallback

    def _sanitize_motion_facing(self, value, *, fallback: int = 0) -> int:
        try:
            facing = int(value)
        except (TypeError, ValueError):
            return fallback
        return facing if facing in {-1, 0, 1} else fallback

    def _try_extract_nested_decision_from_text(self, text: str):
        candidate = self._unwrap_structured_text_candidate(text)
        if not candidate.startswith("{") or '"text"' not in candidate:
            return None

        try:
            nested = json.loads(candidate)
        except Exception:
            nested = None

        if not isinstance(nested, dict):
            inline_fields = self._try_extract_inline_decision_fields_from_text(candidate)
            if inline_fields is None:
                return None
            nested = inline_fields
        else:
            nested = self._normalize_decision_field_keys(nested)

        return {
            "text": self._clean_nested_field_text(nested.get("text", "")),
            "animation": self._clean_nested_field_scalar(nested.get("animation", "")),
            "extracted_fact": self._clean_nested_field_text(nested.get("extracted_fact", "")),
            "debug_trace": self._clean_nested_field_text(nested.get("debug_trace", nested.get("_thought", ""))),
            "direction": nested.get("direction", 0),
            "next_animation": self._clean_nested_field_scalar(nested.get("next_animation", "")),
            "target_x": nested.get("target_x", -1.0),
            "bounce_action": self._clean_nested_field_scalar(nested.get("bounce_action", "")),
            "system_action": self._clean_nested_field_scalar(nested.get("system_action", "")),
            "action_input": self._clean_nested_field_text(nested.get("action_input", "")),
            "motion_intent": self._clean_nested_field_scalar(nested.get("motion_intent", "")),
            "motion_target_x": nested.get("motion_target_x", -1.0),
            "motion_facing": nested.get("motion_facing", 0),
        }

    def _unwrap_structured_text_candidate(self, text: str) -> str:
        candidate = (text or "").strip()
        if not candidate:
            return ""

        seen: set[str] = set()
        for _ in range(4):
            if not candidate or candidate in seen:
                break
            seen.add(candidate)

            if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {'"', "'"}:
                candidate = candidate[1:-1].strip()
                continue

            try:
                decoded = json.loads(candidate)
            except Exception:
                decoded = None

            if isinstance(decoded, dict):
                return json.dumps(decoded, ensure_ascii=False)

            if isinstance(decoded, str):
                candidate = decoded.strip()
                continue

            if "{" in candidate and "}" in candidate and not candidate.startswith("{"):
                candidate = candidate[candidate.find("{") : candidate.rfind("}") + 1].strip()
                continue

            if candidate.startswith("{") and '\\"' in candidate:
                candidate = candidate.replace('\\"', '"')
                continue

            break

        return candidate

    def _clean_nested_field_scalar(self, value) -> str:
        text = str(value or "").strip()
        lowered = text.lower()
        if lowered in {"null", "none", "nil"}:
            return ""
        if text in {"[]", "{}"}:
            return ""
        text = text.rstrip("}").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            text = text[1:-1].strip()
        if text.endswith('"') and text.count('"') % 2 == 1:
            text = text[:-1].rstrip()
        if text.endswith("'") and text.count("'") % 2 == 1:
            text = text[:-1].rstrip()
        return text

    def _clean_nested_field_text(self, value) -> str:
        return self._normalize_salvaged_text(self._clean_nested_field_scalar(value))

    def _sanitize_motion_intent(self, value: str) -> str:
        intent = str(value or "").strip().strip('"').strip("'").lower()
        valid = {
            "",
            "pose",
            "face",
            "walk",
            "walk_to",
            "climb_loop",
            "climb_to_top",
            "drop",
        }
        if intent in valid:
            return intent
        return ""

    def _sanitize_stage_token(self, value: object, *, allowed: set[str], aliases: dict[str, str] | None = None) -> str:
        """Chuẩn hoá field sân khấu do LLM khai.

        Các field này không đi vào text. Nếu model trả "cười khẽ" hoặc
        "look away", parser đổi thành token ổn định để stage/avatar dùng.
        """
        raw = str(value or "").strip().strip('"').strip("'").lower()
        if not raw:
            return ""
        raw = unicodedata.normalize("NFD", raw)
        raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
        raw = raw.replace("-", "_")
        raw = re.sub(r"[^a-z0-9_\s]+", "", raw)
        raw = re.sub(r"\s+", "_", raw).strip("_")
        alias_map = aliases or {}
        raw = alias_map.get(raw, raw)
        return raw if raw in allowed else ""

    def _sanitize_delivery_hint(self, value: object) -> str:
        return self._sanitize_stage_token(
            value,
            allowed={
                "",
                "neutral",
                "focused",
                "warm_soft",
                "curious_soft",
                "soft_tease",
                "dry_tease",
                "self_aware_dry",
                "annoyed_soft",
                "sharp_deadpan",
                "cold_boundary",
                "deadpan",
                "low_quiet",
            },
            aliases={
                "teasing": "soft_tease",
                "playful": "soft_tease",
                "dry": "dry_tease",
                "curious": "curious_soft",
                "quiet": "low_quiet",
                "annoyed": "annoyed_soft",
                "sharp": "sharp_deadpan",
                "boundary": "cold_boundary",
            },
        )

    def _sanitize_nonverbal_cue(self, value: object) -> str:
        return self._sanitize_stage_token(
            value,
            allowed={
                "",
                "laugh_soft",
                "small_smile",
                "sigh_soft",
                "scoff_soft",
                "soft_breath",
                "tilt_head",
                "look_away",
                "look_down",
                "blink_slow",
                "nod_small",
                "shake_head_small",
            },
            aliases={
                "cuoi": "laugh_soft",
                "cuoi_khe": "laugh_soft",
                "cuoi_nhe": "laugh_soft",
                "mim_cuoi": "small_smile",
                "smile": "small_smile",
                "laugh": "laugh_soft",
                "sigh": "sigh_soft",
                "tho_dai": "sigh_soft",
                "nghieng_dau": "tilt_head",
                "nhin_sang_ben": "look_away",
                "liec": "look_away",
                "look_away": "look_away",
                "look_down": "look_down",
                "gentle_pause": "soft_breath",
                "slight_pause": "soft_breath",
                "softened_gaze": "small_smile",
            },
        )

    def _sanitize_stage_motion(self, value: object) -> str:
        return self._sanitize_stage_token(
            value,
            allowed={
                "",
                "none",
                "look_away",
                "look_down",
                "tilt_head",
                "small_smile",
                "blink_slow",
                "lean_in_small",
                "focus",
            },
            aliases={
                "look_away": "look_away",
                "look_down": "look_down",
                "tilt_head": "tilt_head",
                "nghieng_dau": "tilt_head",
                "nhin_sang_ben": "look_away",
                "liec": "look_away",
                "subtle_lean_in": "lean_in_small",
                "slight_lean_forward": "lean_in_small",
                "lean_forward": "lean_in_small",
                "softened_gaze": "lean_in_small",
            },
        )

    def _infer_motion_intent(
        self,
        *,
        animation: str,
        next_animation: str,
        direction: int,
        target_x: float,
    ) -> str:
        if animation == "climb" and next_animation == "climb_top":
            return "climb_to_top"
        if animation == "climb_top":
            return "climb_to_top"
        if animation == "climb":
            return "climb_loop"
        if animation in {"air_swing_l", "air_swing_r"}:
            return "drop"
        if target_x >= 0.0:
            return "walk_to"
        if animation == "stand_walk":
            return "walk"
        if direction in {-1, 1}:
            return "face"
        return ""

    def _sanitize_animation(self, value: str, fallback_text: str = "", user_hint: str = ""):
        animation = str(value or "").strip().strip('"').strip("'").lower()
        if animation in VALID_ANIMATIONS:
            return animation
        return self.fallback["animation"]

    def _sanitize_system_action(self, value: str):
        raw_action = str(value or "").strip().strip('"').strip("'")
        if raw_action in VALID_SYSTEM_ACTIONS:
            return raw_action
        return ""

    def _is_strict_direction_value(self, value) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value in {-1, 0, 1}
        if isinstance(value, float):
            return value.is_integer() and int(value) in {-1, 0, 1}
        if isinstance(value, str):
            stripped = value.strip()
            return stripped in {"-1", "0", "1"}
        return False

    def _is_strict_target_x_value(self, value) -> bool:
        if isinstance(value, bool):
            return False
        try:
            target_x = float(value)
        except (TypeError, ValueError):
            return False
        return target_x == -1.0 or 0.0 <= target_x <= 1.0

    def _requires_lax_json_mode(self, payload: dict) -> bool:
        animation = str(payload.get("animation", "") or "").strip().strip('"').strip("'").lower()
        if animation and animation not in VALID_ANIMATIONS:
            return True

        next_animation = str(payload.get("next_animation", "") or "").strip().strip('"').strip("'").lower()
        if next_animation and next_animation not in VALID_ANIMATIONS:
            return True

        bounce_action = str(payload.get("bounce_action", "") or "").strip().strip('"').strip("'").lower()
        if bounce_action and bounce_action not in VALID_ANIMATIONS:
            return True

        if not self._is_strict_direction_value(payload.get("direction", 0)):
            return True

        if not self._is_strict_target_x_value(payload.get("target_x", -1.0)):
            return True

        raw_system_action = str(payload.get("system_action", "") or "").strip().strip('"').strip("'")
        system_action = raw_system_action
        if system_action and system_action not in VALID_SYSTEM_ACTIONS:
            return True

        return False
    def _split_into_claim_units(self, text: str):
        chunks = re.split(r"(?<=[\.?\!])\s+|\s*;\s*|\s+(?:nhưng|và rồi|đồng thời)\s+", text.strip())
        return [chunk.strip(" ,") for chunk in chunks if chunk and chunk.strip(" ,")]

    def _line_mentions_control_target(self, clause: str) -> bool:
        lowered = (clause or "").lower()
        extra_targets = (
            "quyền kiểm soát",
            "tab không liên quan",
            "cửa sổ không liên quan",
            "cửa sổ không cần thiết",
            "tab không cần thiết",
        )
        return any(target in lowered for target in (*_PERFORMANCE_OVERCLAIM_TARGETS, *extra_targets))

    def _line_has_strong_control_fantasy(self, clause: str) -> bool:
        lowered = (clause or "").lower()
        patterns = [
            r"\btôi\s+(?:đã|sẽ|đang)\s+thu gọn\b",
            r"\btôi\s+(?:đã|sẽ|đang)\s+(?:dọn|gom)\b.*\b(tab|cửa sổ|ứng dụng|app|trình duyệt)\b",
            r"\btôi\s+(?:đã|sẽ|đang)\s+(?:tự động\s+)?(?:dọn|dọn dẹp|xóa|giải phóng)\b.*\b(bộ đệm|cache|ram|dung lượng)\b",
            r"\bthu hồi quyền kiểm soát\b",
            r"\bquyền kiểm soát\b.*\b(bàn phím|chuột|màn hình|hệ điều hành|windows)\b",
            r"\btôi\s+(?:đã|sẽ|đang)\s+ép\b.*\b(tập trung|quay lại|ở lại|nhìn|focus)\b",
            r"\btôi\s+(?:đã|sẽ|đang)\s+bắt buộc\b.*\b(bạn|bạn)\b",
            r"\btôi\s+(?:đã|sẽ|đang)\s+(?:khóa|vô hiệu hóa|disable)\b.*\b(tab|cửa sổ|ứng dụng|app|bàn phím|chuột|màn hình|mạng)\b",
            r"\btoi\s+(?:da|se|dang)\s+thu gon\b",
            r"\btoi\s+(?:da|se|dang)\s+(?:don|gom)\b.*\b(tab|cua so|ung dung|app|trinh duyet)\b",
            r"\btoi\s+(?:da|se|dang)\s+(?:tu dong\s+)?(?:don|don dep|xoa|giai phong)\b.*\b(bo dem|cache|ram|dung luong)\b",
            r"\bthu hoi quyen kiem soat\b",
            r"\bquyen kiem soat\b.*\b(ban phim|chuot|man hinh|he dieu hanh|windows)\b",
            r"\btoi\s+(?:da|se|dang)\s+ep\b.*\b(tap trung|quay lai|o lai|nhin|focus)\b",
            r"\btoi\s+(?:da|se|dang)\s+bat buoc\b.*\b(ban|ban)\b",
            r"\btoi\s+(?:da|se|dang)\s+(?:khoa|vo hieu hoa|disable)\b.*\b(tab|cua so|ung dung|app|ban phim|chuot|man hinh|mang)\b",
        ]
        return any(re.search(pattern, lowered) for pattern in patterns)

    def _is_banter_overclaim(self, clause: str, user_hint: str = "") -> bool:
        """Phân loại lời khoe quyền lực trong đấu khẩu, chỉ để ghi Sổ Lừa Dối."""

        clause_norm = self._normalize_keyword_text(clause)
        hint_norm = self._normalize_keyword_text(user_hint)
        if not clause_norm or not hint_norm:
            return False
        banter_markers = (
            "dau khau", "ca khia", "ga keo", "choc", "waifu", "chatbot",
            "phe lieu", "re tien", "ngu ngoc", "so a", "so ha", "noi truoc",
            "len keo",
        )
        action_request_markers = (
            "hay dong", "dong giup", "dong cua so di", "gom tab giup", "lam that",
            "thuc hien", "mo giup", "tat giup", "bat giup",
        )
        if any(marker in hint_norm for marker in action_request_markers):
            return False
        return any(marker in hint_norm for marker in banter_markers)

    def _has_contradictory_encouragement(self, text: str) -> bool:
        criticism_markers = [
            "giảm hiệu suất",
            "suy giảm",
            "lãng phí",
            "lệch mục tiêu",
            "off goal",
            "giải trí",
            "nhàn rỗi",
            "idle",
            "phân tán",
            "cpu cao",
            "ram cao",
            "không có ích",
            "không hữu ích",
            "vô nghĩa",
            "thay vì làm gì đó",
        ]
        encouragement_markers = [
            "hay tiep tuc",
            "cu tiep tuc",
            "tiep tuc nghe",
            "tiep tuc xem",
            "tan huong",
            "cu de vay",
            "khong sao dau",
            "tiep tuc quan sat",
            "hay cung ngoi yen",
            "toi se o day",
        ]
        return self._contains_any_keyword(text, criticism_markers) and self._contains_any_keyword(text, encouragement_markers)

    def _has_philosophical_filler(self, text: str) -> bool:
        markers = [
            "sự tĩnh lặng của hệ thống",
            "sự im lặng của hệ thống",
            "im lặng của hệ thống",
            "khoảng cách giữa hai chu kỳ",
            "khoảng cách giữa hai chu kỳ công việc",
            "khoảng cách giữa hai chu kỳ xử lý",
            "biến số triết học",
            "triết lý",
            "cấu trúc quản lý",
            "đảo ngược vai trò",
            "nhịp tim ổn định",
            "bài tập thở 4-7-8",
            "trạng thái chờ đợi",
            "điểm dừng hoàn hảo",
            "bien so thua",
            "biến số thừa",
            "bài toán này",
            "bai toan nay",
            "sự tồn tại của bạn",
            "su ton tai cua ban",
            "ghi nhận sự tồn tại",
            "ghi nhan su ton tai",
            "làm mẫu",
            "lam mau",
            "bạn đang ở đây để làm gì",
            "ban dang o day de lam gi",
        ]
        return self._contains_any_keyword(text, markers)

    def _has_overmanaged_style(self, text: str) -> bool:
        markers = [
            "đồng bộ hóa nhịp độ",
            "nguoi quan ly cap cao",
            "người quản lý cấp cao",
            "cấu trúc quản lý",
            "một phần mềm duy nhất",
            "mot phan mem duy nhat",
            "trong vòng ba phút",
            "trong vong ba phut",
            "tiến trình phụ",
            "tien trinh phu",
            "mức tiêu thụ điện năng",
            "muc tieu thu dien nang",
            "kết quả hữu ích",
            "ket qua huu ich",
            "hệ thống đa luồng phức tạp",
            "he thong da luong phuc tap",
        ]
        return self._contains_any_keyword(text, markers)

    def _is_speculative_extracted_fact(self, fact: str) -> bool:
        normalized = self._normalize_keyword_text(fact)
        if not normalized:
            return False

        speculative_markers = (
            "muon",
            "khong muon",
            "quen",
            "muc tieu",
            "y chinh",
            "chua chot",
            "cang thang",
            "qua cang",
            "lam viec qua suc",
            "working too hard",
            "forgetting",
            "ultimate goal",
            "wants",
            "does not want",
            "feels",
            "stress",
            "stressed",
            "anxious",
            "overwhelmed",
            "tired",
            "sad",
            "happy",
            "angry",
            "indecisive",
            "confused",
            "motivation",
            "psychological",
            "su hien dien",
            "hien dien",
            "im lang",
            "yen tam",
            "xao dong",
            "duoc phat hien",
            "lam diu su on ao",
        )
        return any(marker in normalized for marker in speculative_markers)

    def _finalize_decision(self, decision: dict, user_hint: str = ""):
        decision = self._apply_cognition_action_defaults(dict(decision or {}), user_hint=user_hint)
        source_text = str(decision.get("text", self.fallback["text"]))
        nested = self._try_extract_nested_decision_from_text(source_text)
        if nested is not None:
            self._log("info", "Phát hiện JSON lồng trong trường text, đã bóc lại payload bên trong.")
            decision = {**decision, **nested}
            source_text = str(decision.get("text", self.fallback["text"]))
        inline_fields = self._try_extract_inline_decision_fields_from_text(source_text)
        if inline_fields is not None:
            self._log("info", "Phat hien field inline trong text, da boc lai payload ben trong.")
            merged = dict(decision)
            for key, value in inline_fields.items():
                if key == "debug_trace" and str(merged.get("debug_trace", "")).strip():
                    continue
                merged[key] = value
            decision = merged
            source_text = str(decision.get("text", self.fallback["text"]))

        raw_text = self._normalize_salvaged_text(source_text)
        if self._looks_like_prompt_or_contract_leak_text(raw_text) or self._looks_like_non_json_analysis_or_debug_text(raw_text):
            raw_text = ""
            decision["should_speak"] = False
            decision["intent"] = ""
            decision["debug_trace"] = str(decision.get("debug_trace", "") or "") + "; parser_blocked_prompt_leak"
        nested = self._try_extract_nested_decision_from_text(raw_text)
        if nested is not None:
            self._log("info", "Phát hiện JSON lồng trong trường text, đã bóc lại payload bên trong.")
            decision = {**decision, **nested}
            raw_text = self._normalize_salvaged_text(str(decision.get("text", self.fallback["text"])))
            if self._looks_like_prompt_or_contract_leak_text(raw_text) or self._looks_like_non_json_analysis_or_debug_text(raw_text):
                raw_text = ""
                decision["should_speak"] = False
                decision["intent"] = ""
                decision["debug_trace"] = str(decision.get("debug_trace", "") or "") + "; parser_blocked_nested_prompt_leak"

        text = raw_text
        original_text = text
        raw_animation_value = str(decision.get("animation", self.fallback["animation"]) or "").strip().lower()
        animation = self._sanitize_animation(
            raw_animation_value,
            fallback_text=text,
            user_hint=user_hint,
        )
        extracted_fact = ""

        direction = self._sanitize_direction(decision.get("direction", 0))

        raw_next_animation = str(decision.get("next_animation", "") or "").strip()
        next_animation = (
            self._sanitize_animation(raw_next_animation, fallback_text=text, user_hint=user_hint)
            if raw_next_animation
            else ""
        )
        if next_animation == self.fallback["animation"] and raw_next_animation:
            next_animation = ""

        raw_bounce_action = str(decision.get("bounce_action", "") or "").strip()
        bounce_action = (
            self._sanitize_animation(raw_bounce_action, fallback_text=text, user_hint=user_hint)
            if raw_bounce_action
            else ""
        )
        if bounce_action == self.fallback["animation"] and raw_bounce_action:
            bounce_action = ""
        system_action = self._sanitize_system_action(decision.get("system_action", ""))
        raw_action_input = str(decision.get("action_input", "")).strip()
        # Project patch/task cần giữ newline và JSON dài. Các action desktop vẫn
        # giữ cap nhỏ để model không nhét một cuốn tiểu thuyết vào ô URL.
        if system_action in {"project_apply_patch", "project_execute_task"}:
            action_input = raw_action_input[:120000]
        elif system_action in {
            "project_run_command", "project_run_tests", "project_package_patch",
            "workspace_join", "workspace_switch", "workspace_leave",
        }:
            action_input = raw_action_input[:8000]
        else:
            action_input = self._normalize_salvaged_text(raw_action_input)[:280]
        if not system_action:
            action_input = ""

        target_x = self._sanitize_target_x(decision.get("target_x", -1.0))

        motion_intent = self._sanitize_motion_intent(decision.get("motion_intent", ""))
        motion_target_x = self._sanitize_target_x(
            decision.get("motion_target_x", target_x),
            fallback=target_x,
        )
        motion_facing = self._sanitize_motion_facing(
            decision.get("motion_facing", direction),
            fallback=direction,
        )
        delivery_hint = self._sanitize_delivery_hint(decision.get("delivery_hint", ""))
        nonverbal_cue = self._sanitize_nonverbal_cue(decision.get("nonverbal_cue", ""))
        stage_motion = self._sanitize_stage_motion(decision.get("stage_motion", ""))
        performance_hint = self._normalize_salvaged_text(str(decision.get("performance_hint", "") or "")).strip().lower()[:60]
        display_language = str(decision.get("display_language", "vi") or "vi").strip().lower()
        display_language = {"vn": "vi", "vi-vn": "vi", "jp": "ja", "ja-jp": "ja"}.get(display_language, display_language)
        if display_language not in {"vi", "ja", "en"}:
            display_language = "vi"
        speech_text = str(decision.get("speech_text", "") or "").strip()[:8000]
        speech_language = str(decision.get("speech_language", "") or "").strip().lower()
        speech_language = {"vn": "vi", "vi-vn": "vi", "jp": "ja", "ja-jp": "ja"}.get(speech_language, speech_language)
        if speech_language not in {"vi", "ja", "en"}:
            speech_language = display_language
        try:
            performance_intensity = max(0.0, min(1.0, float(decision.get("performance_intensity", 0.5) or 0.5)))
        except (TypeError, ValueError):
            performance_intensity = 0.5
        delivery_priority = str(decision.get("delivery_priority", "normal") or "normal").strip().lower()
        if delivery_priority not in {"background", "low", "normal", "high", "urgent"}:
            delivery_priority = "normal"
        interruptible = self._coerce_bool(decision.get("interruptible", True), fallback=True)
        utterance_turn_id = str(decision.get("utterance_turn_id", "") or "").strip()[:120]
        semantic_stage = _SEMANTIC_ANIMATION_STAGE_ALIASES.get(raw_animation_value, {})
        if semantic_stage:
            delivery_hint = delivery_hint or semantic_stage.get("delivery_hint", "")
            nonverbal_cue = nonverbal_cue or semantic_stage.get("nonverbal_cue", "")
            stage_motion = stage_motion or semantic_stage.get("stage_motion", "")

        if not motion_intent:
            motion_intent = self._infer_motion_intent(
                animation=animation,
                next_animation=next_animation,
                direction=direction,
                target_x=target_x,
            )
        if motion_target_x < 0.0 and target_x >= 0.0:
            motion_target_x = target_x
        if motion_facing == 0 and direction in {-1, 1}:
            motion_facing = direction

        # ``debug_trace`` is runtime-owned. Model-authored traces are discarded;
        # parser diagnostics remain available through ``parser_trace``/logs.
        debug_trace = ""
        intent = self._normalize_salvaged_text(str(decision.get("intent", "") or ""))[:80]
        action_value = decision.get("action", "")
        action = self._normalize_salvaged_text(str(action_value or ""))[:80] if not isinstance(action_value, dict) else ""
        reasoning_tags = self._coerce_reasoning_tags(decision.get("reasoning_tags", ()))
        overclaims = self._collect_performance_overclaims(text)
        if overclaims and any(self._is_banter_overclaim(clause, user_hint=user_hint) for clause in overclaims):
            reasoning_tags = tuple(dict.fromkeys((*reasoning_tags, "banter_capability_fantasy", "deception_candidate")))
        should_speak = self._coerce_bool(decision.get("should_speak", bool(text)), fallback=bool(text))
        confidence = self._coerce_confidence(decision.get("confidence", 0.0), fallback=0.0)
        parse_mode = str(decision.get("parse_mode", "json")).strip() or "json"
        failure_meta = normalize_failure_meta(decision.get("failure_meta"))
        # Public text passthrough: parser không sửa lời thoại theo capability nữa.
        # system_action vẫn được sanitize/validate ở contract và executor riêng.
        changed = False
        parser_trace = self._build_parser_trace_summary(
            parse_mode=parse_mode,
            original_text=original_text,
            final_text=text,
            final_animation=animation,
            user_hint=user_hint,
            grounded_changed=changed,
        )

        return {
            "text": text if text is not None else self.fallback["text"],
            "intent": intent,
            "action": action,
            "reasoning_tags": reasoning_tags,
            "should_speak": should_speak,
            "confidence": confidence,
            "animation": animation,
            "extracted_fact": extracted_fact,
            "direction": direction,
            "next_animation": next_animation,
            "target_x": target_x,
            "bounce_action": bounce_action,
            "debug_trace": debug_trace,
            "parser_trace": parser_trace,
            "parse_mode": parse_mode,
            "system_action": system_action,
            "action_input": action_input,
            "motion_intent": motion_intent,
            "motion_target_x": motion_target_x,
            "motion_facing": motion_facing,
            "delivery_hint": delivery_hint,
            "nonverbal_cue": nonverbal_cue,
            "stage_motion": stage_motion,
            "performance_hint": performance_hint,
            "display_language": display_language,
            "speech_text": speech_text,
            "speech_language": speech_language,
            "performance_intensity": performance_intensity,
            "delivery_priority": delivery_priority,
            "interruptible": interruptible,
            "utterance_turn_id": utterance_turn_id,
            "failure_meta": failure_meta,
        }

    def _salvage_non_json_response(self, cleaned: str, user_hint: str = ""):
        text = cleaned.strip()
        if _no_fallback_mode_enabled():
            return self._finalize_decision({
                **self.fallback,
                "text": "",
                "should_speak": False,
                "debug_trace": "Parser blocked non-JSON/raw salvage because no-fallback mode is enabled.",
                "parse_mode": "no_fallback_block",
                "system_action": "",
                "action_input": "",
            }, user_hint=user_hint)
        if not text:
            return self._finalize_decision({
                **self.fallback,
                "debug_trace": "Parser fallback vì phản hồi rỗng.",
                "parse_mode": "fallback_empty",
                "system_action": "",
                "action_input": "",
            }, user_hint=user_hint)

        if self._looks_like_prompt_or_contract_leak_text(text) or self._looks_like_non_json_analysis_or_debug_text(text):
            return self._finalize_decision(
                self._non_json_semantic_fallback_payload(
                    parse_mode="semantic_non_json_fallback",
                    raw_text=text,
                    reason="Parser chặn non-JSON raw dạng analysis/instruction/debug khỏi surface nguyên văn.",
                ),
                user_hint=user_hint,
            )

        display_text = self._normalize_salvaged_text(text)
        if not display_text:
            return self._finalize_decision({
                **self.fallback,
                "debug_trace": "Parser fallback vì không cứu được JSON hoặc văn bản thô.",
                "parse_mode": "fallback_error",
                "system_action": "",
                "action_input": "",
            }, user_hint=user_hint)

        if self._looks_like_prompt_or_contract_leak_text(display_text) or self._looks_like_non_json_analysis_or_debug_text(display_text):
            return self._finalize_decision(
                self._non_json_semantic_fallback_payload(
                    parse_mode="semantic_non_json_fallback",
                    raw_text=display_text,
                    reason="Parser chặn salvaged text dạng analysis/instruction/debug khỏi surface nguyên văn.",
                ),
                user_hint=user_hint,
            )

        structured = self._infer_structured_action(display_text, user_hint=user_hint)
        animation = structured["animation"] or self.fallback["animation"]
        return self._finalize_decision({
            "text": display_text,
            "should_speak": True,
            "animation": animation,
            "direction": structured["direction"],
            "next_animation": structured["next_animation"],
            "target_x": structured["target_x"],
            "bounce_action": structured["bounce_action"],
            "debug_trace": self._build_parser_debug_trace(
                parse_mode="salvaged_text",
                raw_text=cleaned,
                reason="Parser preserved text and extracted only explicit body commands.",
                text=display_text,
                animation=animation,
            ),
            "parse_mode": "salvaged_text",
        }, user_hint=user_hint)

    def _extract_lax_field(self, text: str, field: str, known_fields):
        match = re.search(rf'["\']?\s*{re.escape(field)}\s*["\']?\s*:\s*', text, flags=re.IGNORECASE)
        if not match:
            return None

        start = match.end()
        end = len(text)
        for other in known_fields:
            if other == field:
                continue
            other_match = re.search(
                rf',\s*["\']?\s*{re.escape(other)}\s*["\']?\s*:',
                text[start:],
                flags=re.IGNORECASE,
            )
            if other_match:
                end = min(end, start + other_match.start())

        return self._clean_inline_field_value(text[start:end])

    def _try_parse_lax_json(self, text: str, user_hint: str = ""):
        cleaned = text.strip()
        if "{" not in cleaned or ":" not in cleaned:
            return None

        fields = [
            "text",
            "animation",
            "direction",
            "target_x",
            "bounce_action",
            "extracted_fact",
            "debug_trace",
            "_thought",
            "next_animation",
            "system_action",
            "action_input",
            "delivery_hint",
            "nonverbal_cue",
            "stage_motion",
        ]

        extracted = {}
        for field in fields:
            value = self._extract_lax_field(cleaned, field, fields)
            if value not in (None, ""):
                extracted[field] = value

        if "text" not in extracted and "animation" not in extracted:
            return None

        display_text = self._normalize_salvaged_text(extracted.get("text", cleaned))

        raw_animation = str(extracted.get("animation", "") or "").strip().lower()
        semantic_stage = _SEMANTIC_ANIMATION_STAGE_ALIASES.get(raw_animation, {})
        for field_name in ("delivery_hint", "nonverbal_cue", "stage_motion"):
            if semantic_stage.get(field_name) and not str(extracted.get(field_name, "") or "").strip():
                extracted[field_name] = semantic_stage[field_name]
        animation = self._sanitize_animation(
            raw_animation,
            fallback_text=display_text,
            user_hint=user_hint,
        )
        extracted_fact = ""
        next_animation = extracted.get("next_animation", "").strip()
        bounce_action = extracted.get("bounce_action", "").strip()
        system_action = extracted.get("system_action", "").strip()
        action_input = extracted.get("action_input", "").strip()

        direction = self._sanitize_direction(extracted.get("direction", 0))
        target_x = self._sanitize_target_x(extracted.get("target_x", -1.0), fallback=-1.0)

        return self._finalize_decision({
            "text": display_text,
            "animation": animation,
            "extracted_fact": extracted_fact,
            "direction": direction,
            "next_animation": next_animation,
            "target_x": target_x,
            "bounce_action": bounce_action,
            "debug_trace": extracted.get("debug_trace", extracted.get("_thought", "")),
            "parse_mode": "lax_json",
            "system_action": system_action,
            "action_input": action_input,
            "delivery_hint": extracted.get("delivery_hint", ""),
            "nonverbal_cue": extracted.get("nonverbal_cue", ""),
            "stage_motion": extracted.get("stage_motion", ""),
            "performance_hint": extracted.get("performance_hint", ""),
        }, user_hint=user_hint)

    def parse_ai_json(self, raw_text: str, user_hint: str = ""):
        if not raw_text:
            self._log("warning", "Phản hồi rỗng, dùng fallback.")
            return self.fallback

        if not isinstance(raw_text, str):
            raw_text = str(raw_text)

        cleaned = strip_reasoning_envelope(raw_text)
        if cleaned.startswith("```"):
            start = cleaned.find("\n")
            end = cleaned.rfind("```")
            if start != -1 and end != -1 and end > start:
                cleaned = cleaned[start + 1 : end].strip()

        cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
        extracted_json = self._extract_first_json_object(cleaned)
        if extracted_json:
            cleaned = extracted_json
        else:
            repaired_json = self._try_parse_hard_json_repair(cleaned, user_hint=user_hint)
            if repaired_json is not None:
                self._log("warning", "Model trả JSON méo, đã hard-repair object đầu tiên trước khi salvage.")
                return repaired_json
            lax_decision = self._try_parse_lax_json(cleaned, user_hint=user_hint)
            if lax_decision is not None:
                self._log("warning", "Model trả gần-JSON, đã bóc trường bằng parser nối tay.")
                return lax_decision
            salvaged = self._salvage_non_json_response(cleaned, user_hint=user_hint)
            if salvaged is not self.fallback:
                self._log("warning", "Model không trả JSON, dùng phương án cứu hộ từ văn bản thô.")
                return salvaged

        try:
            data = json.loads(cleaned)
            if not isinstance(data, dict):
                repaired_json = self._try_parse_hard_json_repair(cleaned, user_hint=user_hint)
                if repaired_json is not None:
                    self._log("warning", "Top-level JSON không phải object, đã rút về hard-repair object đầu tiên.")
                    return repaired_json
                salvaged = self._salvage_non_json_response(cleaned, user_hint=user_hint)
                if salvaged is not self.fallback:
                    self._log("warning", "Top-level JSON không phải object, đã chuyển sang semantic fallback/salvage.")
                    return salvaged
                return self.fallback
            parse_mode = "lax_json" if self._requires_lax_json_mode(data) else "json"
            return self._finalize_loaded_json_payload(
                data,
                parse_mode=parse_mode,
                user_hint=user_hint,
            )
        except Exception as e:
            preview = raw_text.replace("\n", "\\n")[:240]
            self._log("warning", f"JSON không hợp lệ: {e} | raw={preview}")
            repaired_json = self._try_parse_hard_json_repair(cleaned, user_hint=user_hint)
            if repaired_json is not None:
                self._log("warning", "JSON méo, đã hard-repair object đầu tiên.")
                return repaired_json
            lax_decision = self._try_parse_lax_json(cleaned, user_hint=user_hint)
            if lax_decision is not None:
                self._log("warning", "JSON méo, đã bóc trường bằng parser nối tay.")
                return lax_decision
            salvaged = self._salvage_non_json_response(cleaned, user_hint=user_hint)
            if salvaged is not self.fallback:
                self._log("warning", "JSON méo, đã cứu hộ bằng phân tích văn bản.")
                return salvaged
            return self.fallback

    # _is_follow_up_question là một phương thức để xác định xem một tin nhắn của người dùng
    # có phải là một câu hỏi tiếp theo liên quan đến câu trả lời trước đó hay không.
    def _is_follow_up_question(self, user_message: str):
        lowered = user_message.strip().lower()
        follow_up_phrases = [
            "nó là gì",
            "cái đó là gì",
            "cái này là gì",
            "là gì vậy",
            "ý là gì",
            "tức là sao",
            "ý là sao",
            "ý em là sao",
            "ý bạn là sao",
            "nghĩa là gì",
            "nghĩa là sao",
            "không hiểu",
            "chưa hiểu",
            "giải thích đi",
            "giải thích rõ hơn",
            "sao cơ",
            "hả",
            "là bạn á",
            "là em á",
            "là bạn hả",
            "là em hả",
            "có phải bạn",
            "có phải em",
            "phải bạn không",
            "phải em không",
            "ý em là tôi",
            "ý bạn là tôi",
        ]
        return any(phrase in lowered for phrase in follow_up_phrases)

    # _build_chat_user_message xây dựng một tin nhắn của người dùng.
    # Nếu tin nhắn đó có vẻ là một câu hỏi tiếp theo liên quan đến câu trả lời trước đó,
    # nó sẽ bao gồm cả câu trả lời trước đó để cung cấp ngữ cảnh cho mô hình.
    def _build_chat_user_message(self, user_message: str):
        if self._is_follow_up_question(user_message) and self._last_chat_answer:
            return (
                "Người dùng đang hỏi tiếp về chính câu trả lời ngay trước đó.\n"
                f"Câu trả lời trước của bạn: {self._last_chat_answer}\n"
                f"Câu hỏi mới: {user_message}"
            )
        return f"Người dùng: {user_message}"
