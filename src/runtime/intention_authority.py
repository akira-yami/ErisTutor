# -*- coding: utf-8 -*-
"""Quan sát lời tự thuật hành động, không cấp hay tước quyền phát ngôn.

Tên file được giữ để tương thích import cũ. Định nghĩa kiến trúc mới:
``IntentionObserver`` chỉ xác định câu có target/handler/result hay chưa. Kết quả
được ghi thành observation và đưa vào hậu quả lượt sau; raw public text luôn giữ
nguyên. Không rewrite, không suppress, không body-only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Mapping

try:
    from actions.capability_registry import canonicalize_action_id, live_capability_ids
    from actions.contracts import VALID_SYSTEM_ACTIONS
except Exception:  # pragma: no cover - môi trường test tối giản
    VALID_SYSTEM_ACTIONS = set()

    def canonicalize_action_id(action_id: str) -> str:
        return str(action_id or "").strip().lower()

    def live_capability_ids(*args, **kwargs) -> tuple[str, ...]:
        return ()


def _clean(value: Any, limit: int = 360) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9_:=./\\\s-]", " ", text)).strip()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class IntentionObservation:
    accepted: bool
    text: str
    original_text: str
    mode: str
    operation: str
    reason: str
    target_refs: tuple[str, ...]
    handler_id: str
    result_contract: str
    missing_contract: tuple[str, ...]
    rewritten: bool = False
    body_only: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["target_refs"] = list(self.target_refs)
        payload["missing_contract"] = list(self.missing_contract)
        return payload


class IntentionObserver:
    """Quan sát lời hứa hành động và trạng thái execution contract.

    Contract tối thiểu:

    - ``target``: đang làm với cái gì;
    - ``handler``: module/tool nào thật sự thực thi;
    - ``result``: event hoặc ActionExecutionResult nào chứng minh đã chạy.

    Observer chỉ áp cho lời tự thuật hành động của Eris. Câu trạng thái, banter,
    lời từ chối hoặc speech act đang được thực hiện ngay trong chính câu nói
    không cần một tool riêng.
    """

    VERSION = "intention_authority.v2.semantic_execution"

    # Observer này chỉ được phép bắt lời claim về *hành động runtime bên ngoài*.
    # Nó không phải máy dò động từ. Nếu cứ thấy "tôi" + "làm/xem/mở" rồi kết án
    # thì banter, tình cảm và câu nói tự hoàn thành sẽ biến thành "nợ capability".
    _FIRST_PERSON = r"(?:toi|minh|em|ta|tui)"
    _AUX = r"(?:se|dang|da|vua|dinh|muon|can|phai|sap|chuan bi|thu|co the)"
    _FIRST_PERSON_RE = re.compile(rf"\b{_FIRST_PERSON}\b")

    # Hành động nội tại/speech act hoàn thành ngay trong chính câu nói, không cần
    # tool/handler. Đây là vùng Eris được phép là một sinh vật biết nói thay vì
    # một pipeline đang xin giấy phép thở.
    _SELF_EXECUTING_RE = re.compile(
        r"\b(?:"
        r"noi|hoi|nhac|ke|thu nhan|xin loi|tra loi|trieu|treu|dua|"
        r"thich|thuong|nho|so|ngai|muon quen|thay vui|thay buon|"
        r"giu khoang cach|diu lai|noi gon|noi thang|noi sen|noi ngot|"
        r"thu cach noi|thu noi|thu xem hop|xem hop|lam mau|lam gi|mo mieng"
        r")\b"
    )

    # Những verb này tự thân đã ám chỉ side effect bên ngoài runtime/model.
    _INHERENT_EXTERNAL_OP_RE = re.compile(
        r"\b(?:"
        r"chay|thu chay|luu|xoa|mo|dong|gui|tai|tai xuong|cap nhat|"
        r"sap xep|don dep|doi ten|di chuyen|sao chep|cai dat|go cai dat"
        r")\b"
    )

    # Verb mơ hồ chỉ thành execution claim khi đi cùng target bên ngoài. "Tôi
    # xem hợp không" không phải lời hứa gọi tool, còn "tôi xem lại file" thì có.
    _AMBIGUOUS_OP_RE = re.compile(
        r"\b(?:"
        r"ra soat|kiem|kiem tra|kiem lai|xem|xem lai|doc|doc lai|luc|luc lai|"
        r"doi chieu|tim|tim lai|tra|tra cuu|sua|xu ly|viet|ghi|lam"
        r")\b"
    )
    _EXTERNAL_TARGET_RE = re.compile(
        r"\b(?:"
        r"file|tep|repo|source|code|project|thu muc|folder|duong dan|path|"
        r"cua so|window|tab|trinh duyet|browser|app|ung dung|man hinh|"
        r"email|mail|tin nhan|message|lich|calendar|database|db|memory|"
        r"setting|cau hinh|config|key|api|server|process|service|log|test|"
        r"anh|image|video|audio|tts|body|godot|scene|node"
        r")\b"
    )
    _NEGATED_EXTERNAL_RE = re.compile(
        rf"\b{_FIRST_PERSON}\b(?:\s+\w+){{0,5}}\s+"
        r"(?:khong|chua|chang|khoi|khong can|chua can)\s+"
        r"(?:\w+\s+){0,3}(?:chay|luu|xoa|mo|dong|gui|tai|sua|viet|ghi|lam|xem)\b"
    )
    _CLAUSE_SPLIT_RE = re.compile(r"[,.!?;:\n]+|\s+-\s+|\s+—\s+")
    _NEGATION_RE = re.compile(r"\b(?:khong|chua|chang|khoi)\b")

    # Những câu này có hình thức tương lai nhưng không khoe một pipeline hành
    # động. Chặn chúng sẽ khiến Eris không hứa ngoan được, một thảm họa đạo đức.
    _SELF_LIMITING_RE = re.compile(
        r"\b(?:"
        r"khong hua se ngoan|khong can ngoan|khong ngoan|se im|khong chen|"
        r"chua can chen|khong doan|chua doan|khong phan|chua phan|"
        r"khong lam phien|o day|dung o day|giu im lang"
        r")\b"
    )
    _IMMEDIATE_SPEECH_RE = re.compile(
        r"\b(?:muon|se|de toi|de minh|toi|minh|em|tui)\s+"
        r"(?:noi|hoi|nhac|ke|thu nhan|xin loi|tra loi|treu|dua)\b"
    )

    _PRESSURE_ONLY_PREFIXES = (
        "silent_cycles=",
        "vital_pressure=",
        "boredom=",
        "thought=",
        "silence_pressure=",
        "source=",
        "surface_seed=",
        "low_claim_",
        "pattern_residue",
        "memory_resonance",
        "quiet continuity",
        "ambient_runtime",
    )
    _ANCHOR_RE = re.compile(
        r"\b(?:memory_id|event_id|vision_fact_id|claim_id|task_id|error_id|turn_id)\s*[:=]\s*[^\s|;]+",
        re.I,
    )
    _LEGACY_ANCHOR_RE = re.compile(
        r"\b(?:memory_activation:|runtime_event[:=]|vision_fact[:=]|dispatch:|observer:|"
        r"user_text[:=]|file[:=]|path[:=]|error[:=]|task[:=])[^\s|;]*",
        re.I,
    )

    _MEMORY_WORDS = ("ky uc", "dau vet", "manh nho", "chuyen cu", "memory", "nho lai")
    _TECH_WORDS = ("bug", "loi", "test", "repo", "code", "tts", "parser", "network", "file")
    _SCREEN_WORDS = ("man hinh", "cua so", "tab", "uia", "ocr", "vision")
    _DISCOURSE_WORDS = ("cau vua roi", "loi vua roi", "cho vua roi", "claim", "turn_id", "user_text")

    def validate(
        self,
        text: str,
        context: Mapping[str, Any] | None = None,
    ) -> IntentionObservation:
        # Raw public text là bất biến. Bản 360 ký tự chỉ phục vụ phát hiện claim.
        original = str(text or "")
        normalized = _norm(_clean(original))
        ctx = _as_mapping(context)
        operation = self._operation(normalized, ctx, original_text=original)

        if not original:
            return self._accepted(original, operation=operation, mode="empty_passthrough")

        target_refs = self._target_refs(ctx)
        handler_id, handler_available = self._handler(ctx)
        result_contract = self._result_contract(ctx, handler_id=handler_id)

        missing: list[str] = []
        looks_operational = bool(
            operation
            and self._looks_like_self_intention(
                normalized,
                operation,
                ctx,
                original_text=original,
            )
        )
        if looks_operational:
            if not target_refs:
                missing.append("target")
            if not handler_id or not handler_available:
                missing.append("handler")
            if not result_contract:
                missing.append("result")

        if not looks_operational:
            mode = "self_executing" if (
                self._SELF_LIMITING_RE.search(normalized)
                or self._IMMEDIATE_SPEECH_RE.search(normalized)
                or self._SELF_EXECUTING_RE.search(normalized)
            ) else "raw_passthrough"
            reason = "not_execution_claim_passthrough"
        elif missing:
            mode = "raw_passthrough_missing_contract"
            reason = "final_rewrite_disabled_ledger_will_record_claim"
        else:
            mode = "executable_intention"
            reason = "execution_contract_complete"

        return IntentionObservation(
            accepted=True,
            text=original,
            original_text=original,
            mode=mode,
            operation=operation,
            reason=reason,
            target_refs=target_refs,
            handler_id=handler_id,
            result_contract=result_contract,
            missing_contract=tuple(missing),
            rewritten=False,
            body_only=False,
        )

    def _accepted(self, text: str, *, operation: str, mode: str) -> IntentionObservation:
        return IntentionObservation(
            accepted=True,
            text=text,
            original_text=text,
            mode=mode,
            operation=operation,
            reason="no_execution_claim",
            target_refs=(),
            handler_id="",
            result_contract="",
            missing_contract=(),
        )

    def _looks_like_self_intention(
        self,
        normalized: str,
        operation: str,
        ctx: Mapping[str, Any],
        *,
        original_text: str = "",
    ) -> bool:
        # Runtime đã có tool/action contract thì text chỉ cần chứa một operation
        # phù hợp. Đây là evidence mạnh hơn việc đoán bằng từ khóa.
        explicit_action = canonicalize_action_id(
            _clean(ctx.get("system_action") or ctx.get("action") or ctx.get("execution_handler"))
        )
        if explicit_action and explicit_action not in {"none", "reply", "respond", "speak", "motioncommand"}:
            return True

        # ``_operation`` chỉ trả operation khi nó đã tìm thấy một clause tự nhận
        # hành động hợp lệ. Không chạy lại regex toàn câu ở đây, tránh hai bộ luật
        # lệch nhau rồi tự tạo thêm một loại bug mới để con người chăm sóc.
        return bool(operation)

    def _clauses(self, original_text: str, normalized: str) -> tuple[str, ...]:
        """Giữ ranh giới clause trước khi chuẩn hoá làm mất dấu câu.

        ``_norm`` cố ý loại dấu câu cho các matcher cũ. Nếu split sau bước đó thì
        ``"bạn mở miệng, tôi sửa file"`` thành một clause và động từ ``mở`` của
        user bị gắn sang ngôi ``tôi``. Split trên raw text trước, rồi mới normalize
        từng phần sẽ giữ đúng chủ thể của hành động.
        """

        source = str(original_text or "").strip()
        if not source:
            return tuple(
                part.strip()
                for part in self._CLAUSE_SPLIT_RE.split(normalized)
                if part.strip()
            )
        return tuple(
            clean
            for clean in (_norm(part) for part in self._CLAUSE_SPLIT_RE.split(source))
            if clean
        )

    def _operation(
        self,
        normalized: str,
        ctx: Mapping[str, Any],
        *,
        original_text: str = "",
    ) -> str:
        # Chọn operation trong clause có ngôi thứ nhất và chỉ xét phần nằm sau
        # ngôi đó. Không lấy động từ của User ở nửa đầu câu rồi dùng nó để đánh
        # giá lời Eris ở nửa sau.
        explicit_action = canonicalize_action_id(
            _clean(ctx.get("system_action") or ctx.get("action") or ctx.get("execution_handler"))
        )
        has_explicit_action = bool(
            explicit_action
            and explicit_action not in {"none", "reply", "respond", "speak", "motioncommand"}
        )
        for clause in self._clauses(original_text, normalized):
            person = self._FIRST_PERSON_RE.search(clause)
            if not person:
                continue
            tail = clause[person.end() :].strip()
            if not tail:
                continue

            candidates: list[tuple[int, str, str]] = []
            external = self._INHERENT_EXTERNAL_OP_RE.search(tail)
            if external:
                candidates.append((external.start(), str(external.group(0)), "external"))
            ambiguous = self._AMBIGUOUS_OP_RE.search(tail)
            if ambiguous:
                candidates.append((ambiguous.start(), str(ambiguous.group(0)), "ambiguous"))
            if not candidates:
                continue

            position, operation, kind = min(candidates, key=lambda item: item[0])
            before_operation = tail[:position]

            # Phủ định chỉ vô hiệu operation trong chính clause đó. Clause kế tiếp
            # vẫn có thể tạo claim thật: "tôi không mở A, tôi sẽ mở B".
            if self._NEGATION_RE.search(before_operation):
                continue

            # Không cho operation cách ngôi thứ nhất quá xa. Đây thường là một
            # mệnh đề phụ khác bị mất liên từ/dấu câu, không phải lời tự nhận.
            if len(before_operation.split()) > 5:
                continue

            if kind == "ambiguous" and not (
                has_explicit_action or self._EXTERNAL_TARGET_RE.search(tail[position:])
            ):
                continue
            return operation

        # Payload action thật có thể đi với câu rất ngắn/elliptical; khi đó lấy
        # operation ở toàn câu làm telemetry, nhưng vẫn không bịa operation mới.
        if has_explicit_action:
            external = self._INHERENT_EXTERNAL_OP_RE.search(normalized)
            if external:
                return str(external.group(0))
            ambiguous = self._AMBIGUOUS_OP_RE.search(normalized)
            if ambiguous:
                return str(ambiguous.group(0))
        return ""

    def _target_refs(self, ctx: Mapping[str, Any]) -> tuple[str, ...]:
        refs: list[str] = []
        for key in ("target_ref", "target_id", "intent_target", "task_id", "claim_id"):
            value = _clean(ctx.get(key))
            if value:
                refs.append(value)

        action_input = _clean(ctx.get("action_input"))
        if action_input and action_input not in {"{}", "[]", "null", "none"}:
            refs.append(f"action_input:{action_input[:120]}")

        for value in tuple(ctx.get("evidence_refs") or ctx.get("evidence") or ()):
            clean = _clean(value, 220)
            if self._is_real_anchor(clean):
                refs.append(clean)
        return tuple(dict.fromkeys(refs))[:8]

    def _is_real_anchor(self, value: str) -> bool:
        if not value:
            return False
        normalized = _norm(value)
        if any(normalized.startswith(prefix) for prefix in self._PRESSURE_ONLY_PREFIXES):
            return False
        if self._ANCHOR_RE.search(value) or self._LEGACY_ANCHOR_RE.search(value):
            return True
        # Một chuỗi evidence giàu nội dung vẫn dùng được, nhưng nhãn một từ hoặc
        # seed tự thuật của chính câu đang xét không được tự cấp giấy phép cho nó.
        return len(value) >= 18 and any(
            token in normalized
            for token in (
                "user said", "nguoi dung", "active_app", "screen", "window", "error", "failed",
                "test", "file", "memory", "episode", "conversation", "recent topic",
            )
        )

    def _handler(self, ctx: Mapping[str, Any]) -> tuple[str, bool]:
        explicit = _clean(
            ctx.get("execution_handler")
            or ctx.get("handler_id")
            or ctx.get("intent_handler")
        )
        if explicit:
            available = ctx.get("handler_available")
            return explicit, bool(True if available is None else available)

        action_result = _as_mapping(ctx.get("action_result"))
        result_action = canonicalize_action_id(_clean(action_result.get("action_name")))
        if result_action and bool(action_result.get("success") or action_result.get("dispatched") or action_result.get("observed")):
            return result_action, True

        action_id = canonicalize_action_id(_clean(ctx.get("system_action") or ctx.get("action")))
        if not action_id or action_id in {"none", "reply", "respond", "speak", "motioncommand"}:
            return "", False
        if action_id not in VALID_SYSTEM_ACTIONS:
            return action_id, bool(ctx.get("handler_available", False))
        try:
            live = set(live_capability_ids())
        except Exception:
            live = set()
        return action_id, bool(action_id in live or ctx.get("handler_available", False))

    def _result_contract(self, ctx: Mapping[str, Any], *, handler_id: str) -> str:
        explicit = _clean(
            ctx.get("observable_result")
            or ctx.get("result_event")
            or ctx.get("completion_event")
        )
        if explicit:
            return explicit
        action_result = _as_mapping(ctx.get("action_result"))
        if action_result:
            status = _clean(action_result.get("status") or action_result.get("public_mode"))
            evidence = tuple(action_result.get("evidence") or ())
            if status or evidence or action_result.get("success"):
                return status or "action_result"
        if handler_id and handler_id in VALID_SYSTEM_ACTIONS:
            # ActionExecutor luôn trả ActionExecutionResult, nên đây là result
            # contract thật chứ không phải lời hứa trang trí.
            return f"action_result:{handler_id}"
        return ""



# Tương thích tên cũ: code ngoài repo chưa cần đổi ngay, nhưng semantic role đã đổi.
IntentionAuthorityGate = IntentionObserver
IntentionAuthorityVerdict = IntentionObservation

__all__ = [
    "IntentionObserver",
    "IntentionObservation",
    "IntentionAuthorityGate",
    "IntentionAuthorityVerdict",
]
