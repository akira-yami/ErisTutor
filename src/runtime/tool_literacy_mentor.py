# -*- coding: utf-8 -*-
"""V82 - Tool Literacy Mentor cho Eris Desktop.

Module này KHÔNG chạy debugger, test, git hay Godot thay user.
Nhiệm vụ duy nhất của nó là:
1) nhận diện câu hỏi kỹ thuật hiện tại cần loại evidence nào;
2) đề cử một công cụ chính + tối đa hai công cụ phụ đã có sẵn;
3) nói rõ user cần nhìn tín hiệu nào trong công cụ đó;
4) giữ một projection rất nhỏ về tool literacy dựa trên evidence user thật sự dùng tool.

Triết lý V82: dùng tool để trả lời một câu hỏi cụ thể, không "mở tất cả tool cho chắc".
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.workspace_manager import default_eris_home

_SCHEMA = "eris.engineering_tool_literacy.v1"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool_id: str
    title_vi: str
    family: str
    question_vi: str
    first_action_vi: str
    evidence_vi: str
    common_mistake_vi: str


_TOOL_CATALOG: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "code_search",
        "Search / References / Call hierarchy",
        "navigation",
        "Code nào sở hữu behavior này và data/control flow đi qua đâu?",
        "Tìm symbol/call-site hẹp trước; lần từ nơi gọi -> implementation -> nơi state bị đổi.",
        "Danh sách call-site/symbol liên quan và mắt xích chung giữa các nơi cùng hỏng.",
        "Search cả repo bằng từ khóa mơ hồ rồi đọc hàng chục file không có giả thuyết.",
    ),
    ToolDefinition(
        "vscode_diagnostics",
        "VS Code Problems / Diagnostics",
        "diagnostics",
        "Compiler/LSP đang chỉ ra lỗi tĩnh cụ thể ở đâu?",
        "Mở Problems; xử lý lỗi đầu tiên thuộc scope task, xem source + file + range trước khi sửa dây chuyền.",
        "Diagnostic code/source, file/range và lỗi gốc đứng trước các lỗi cascade.",
        "Sửa mọi dòng đỏ từ dưới lên mà không phân biệt lỗi gốc và lỗi kéo theo.",
    ),
    ToolDefinition(
        "git_diff",
        "Git diff / Source Control",
        "change_review",
        "AI hoặc mình vừa thay đổi chính xác cái gì, có vượt scope không?",
        "Đọc từng hunk diff; đối chiếu intent của task với file/hunk bị đổi trước khi tin test pass.",
        "File ngoài scope, đổi behavior không chủ đích, xóa guard/test, hoặc refactor chen vào fix nhỏ.",
        "Chỉ nhìn danh sách file changed hoặc tin summary của agent mà không đọc hunk.",
    ),
    ToolDefinition(
        "targeted_tests",
        "Targeted test / Regression test",
        "verification",
        "Behavior nào đang fail và fix này có chứng minh đúng đúng scope không?",
        "Chạy test nhỏ nhất tái hiện lỗi; giữ failure message ổn định rồi mới sửa, sau đó chạy regression gần nhất.",
        "Một failure reproducible trước fix và pass sau fix; regression lân cận không vỡ.",
        "Chạy full suite quá sớm hoặc coi 'có test pass' là đủ dù test không chạm bug.",
    ),
    ToolDefinition(
        "stack_log",
        "Stack trace / Runtime log",
        "runtime_debugging",
        "Lỗi runtime bắt đầu từ frame/state nào trong code dự án?",
        "Giữ nguyên error + stack; tìm frame đầu tiên thuộc project rồi lần ngược input/state dẫn tới đó.",
        "Exception type/message, frame đầu tiên thuộc project, giá trị/context ngay trước failure.",
        "Chỉ đọc dòng exception cuối hoặc grep log mà bỏ call stack/time ordering.",
    ),
    ToolDefinition(
        "debugger",
        "Debugger / Breakpoint / Watch",
        "runtime_debugging",
        "State thực tế sai ở bước nào, trước hay sau mutation nào?",
        "Đặt breakpoint ngay trước mutation nghi ngờ; xem call stack và giá trị trước/sau thay vì thêm print khắp nơi.",
        "Call stack, branch thực sự chạy, giá trị state trước/sau và frame nơi divergence bắt đầu.",
        "Đặt breakpoint tùy tiện ở symptom cuối hoặc sửa code trong khi chưa biết state sai từ đâu.",
    ),
    ToolDefinition(
        "profiler",
        "Profiler",
        "performance",
        "Thời gian/CPU/frame budget thực sự bị tiêu ở đâu?",
        "Đo trước khi tối ưu; tìm hotspot theo self/total time hoặc frame spike rồi mới đọc đúng path đó.",
        "Hotspot lặp lại, frame spike và tỷ trọng thời gian; không dùng cảm giác 'file này nặng'.",
        "Micro-optimize code đẹp mắt trước khi có số đo.",
    ),
    ToolDefinition(
        "godot_remote_scene",
        "Godot Remote Scene Tree",
        "godot_runtime",
        "Node/transform/collision/process state lúc game đang chạy có khác scene editor không?",
        "Chạy game, chuyển Scene dock sang Remote, chọn node thật đang lỗi và so transform/collision/process state với Local.",
        "Node thực tế được instance, global/local transform, visibility/process state, collision shape/layer/mask.",
        "Chỉ nhìn .tscn/editor rồi kết luận runtime giống hệt, nhất là object được spawn/scale bằng code.",
    ),
    ToolDefinition(
        "godot_debugger",
        "Godot Debugger / Errors / Monitors",
        "godot_runtime",
        "Godot đang báo runtime error hoặc frame/physics signal nào đúng lúc bug xảy ra?",
        "Mở Debugger; ưu tiên Errors/Debugger và Monitor liên quan, tái hiện bug một lần sạch rồi đối chiếu timestamp.",
        "Error/warning có stack, physics/process spike, orphan/node issue hoặc signal lặp đúng lúc tái hiện.",
        "Đọc log cũ lẫn nhiều lần chạy và đoán correlation bằng mắt.",
    ),
)
_TOOL_BY_ID = {item.tool_id: item for item in _TOOL_CATALOG}


@dataclass(slots=True)
class ToolState:
    exposures: int = 0
    demonstrated_count: int = 0
    confidence: float = 0.05
    last_recommended_at: float = 0.0
    last_demonstrated_at: float = 0.0

    def clamp(self) -> None:
        self.exposures = max(0, int(self.exposures))
        self.demonstrated_count = max(0, int(self.demonstrated_count))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def stage(self) -> str:
        if self.demonstrated_count >= 3 and self.confidence >= 0.18:
            return "familiar"
        if self.demonstrated_count >= 1:
            return "demonstrated"
        if self.exposures >= 1:
            return "introduced"
        return "unobserved"

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return {
            "stage": self.stage(),
            "exposures": self.exposures,
            "demonstrated_count": self.demonstrated_count,
            "confidence": round(self.confidence, 4),
            "last_recommended_at": self.last_recommended_at,
            "last_demonstrated_at": self.last_demonstrated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolState":
        item = cls(
            exposures=int(raw.get("exposures", 0) or 0),
            demonstrated_count=int(raw.get("demonstrated_count", 0) or 0),
            confidence=float(raw.get("confidence", 0.05) or 0.05),
            last_recommended_at=float(raw.get("last_recommended_at", 0.0) or 0.0),
            last_demonstrated_at=float(raw.get("last_demonstrated_at", 0.0) or 0.0),
        )
        item.clamp()
        return item


@dataclass(slots=True)
class ToolLiteracyDirective:
    active: bool = False
    issue_kind: str = ""
    question_vi: str = ""
    primary_tool: str = ""
    supporting_tools: tuple[str, ...] = ()
    reason: str = ""
    first_action_vi: str = ""
    evidence_vi: str = ""
    common_mistake_vi: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        primary = _TOOL_BY_ID.get(self.primary_tool)
        supporting = [_TOOL_BY_ID[x] for x in self.supporting_tools if x in _TOOL_BY_ID]
        return {
            "schema": "eris.tool_literacy.directive.v1",
            "active": self.active,
            "issue_kind": self.issue_kind,
            "question_vi": self.question_vi,
            "primary": {
                "tool_id": self.primary_tool,
                "title_vi": primary.title_vi if primary else "",
            } if self.primary_tool else {},
            "supporting": [
                {"tool_id": item.tool_id, "title_vi": item.title_vi} for item in supporting
            ],
            "reason": self.reason,
            "first_action_vi": self.first_action_vi,
            "evidence_vi": self.evidence_vi,
            "common_mistake_vi": self.common_mistake_vi,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
        }


class EngineeringToolLiteracy:
    """Advisor + projection nhỏ về cách user vận hành engineering tools.

    Không semantic-grade toàn bộ chat. Demonstration chỉ được ghi khi câu user có:
    - động từ cho thấy đã dùng tool;
    - tên/tool marker;
    - và evidence/result marker tương đối cụ thể.
    """

    _ACTION_MARKERS = (
        "mở", "xem", "đọc", "chạy", "đặt breakpoint", "inspect", "trace", "check", "kiểm tra",
        "so diff", "review diff", "profile", "debug", "tái hiện", "reproduce",
    )
    _RESULT_MARKERS = (
        "thấy", "ra", "báo", "fail", "pass", "frame", "file", "dòng", "line", "node", "value",
        "giá trị", "stack", "hunk", "hotspot", "ms", "fps", "collision", "transform", "scope",
    )
    _TOOL_TEXT_MARKERS: dict[str, tuple[str, ...]] = {
        "code_search": ("search", "references", "reference", "call hierarchy", "find references", "tìm symbol"),
        "vscode_diagnostics": ("problems", "diagnostic", "lsp", "dòng đỏ", "compiler error", "type error"),
        "git_diff": ("git diff", "source control", "diff", "hunk"),
        "targeted_tests": ("test", "pytest", "regression"),
        "stack_log": ("stack trace", "stack", "runtime log", "log"),
        "debugger": ("debugger", "breakpoint", "watch", "call stack"),
        "profiler": ("profiler", "profile", "hotspot", "frame time"),
        "godot_remote_scene": ("remote scene", "remote scene tree", "scene remote"),
        "godot_debugger": ("godot debugger", "monitor", "monitors", "godot error"),
    }

    def __init__(self, *, path: Path | None = None, logger: Any = None) -> None:
        self.path = path or (default_eris_home() / "mentor" / "engineering_tool_literacy.json")
        self.logger = logger
        self.tools: dict[str, ToolState] = {item.tool_id: ToolState() for item in _TOOL_CATALOG}
        self.recent_events: list[dict[str, Any]] = []
        self.updated_at = 0.0
        self._lock = threading.RLock()
        self._load()

    def observe_user_turn(self, user_text: str, *, evidence_ref: str = "", now: float | None = None) -> tuple[str, ...]:
        """Ghi evidence tool use hẹp; không lưu raw user text."""
        ts = float(now or time.time())
        text = " ".join(str(user_text or "").split())
        low = text.casefold()
        if not text:
            return ()
        action = any(marker in low for marker in self._ACTION_MARKERS)
        result = any(marker in low for marker in self._RESULT_MARKERS)
        # Câu hỏi "debugger là gì?" không phải demonstration.
        is_question = "?" in text or any(x in low for x in ("là gì", "dùng sao", "dùng thế nào", "nên dùng"))
        demonstrated: list[str] = []
        if action and result and not is_question:
            for tool_id, markers in self._TOOL_TEXT_MARKERS.items():
                if any(marker in low for marker in markers):
                    demonstrated.append(tool_id)
        if not demonstrated:
            return ()
        with self._lock:
            for tool_id in dict.fromkeys(demonstrated):
                state = self.tools[tool_id]
                state.demonstrated_count += 1
                state.last_demonstrated_at = ts
                state.confidence += 0.07 * (1.0 - state.confidence)
                state.clamp()
            event_codes = [f"tool_demonstrated:{tool_id}" for tool_id in dict.fromkeys(demonstrated)]
            self._event_locked("tool_demonstrated", ts, tools=demonstrated, ref=evidence_ref)
            self.updated_at = ts
            self._save_locked()
        return tuple(event_codes)

    def plan(
        self,
        user_text: str,
        *,
        decision_mode: str,
        findings: Sequence[str] = (),
        recent_flow: Mapping[str, Any] | None = None,
        environment_hint: Mapping[str, Any] | None = None,
        compact: bool = False,
        now: float | None = None,
    ) -> ToolLiteracyDirective:
        """Chọn một tool chính để trả lời đúng câu hỏi evidence hiện tại."""
        text = " ".join(str(user_text or "").split()).casefold()
        if not text or decision_mode == "none":
            return ToolLiteracyDirective()
        findings = tuple(str(x) for x in findings)
        flow = recent_flow or {}

        # V83/V89 contract: explicit user intent / concrete flow finding wins.
        # Environment correlation only fills the gap before the generic
        # "changed_files -> git diff" fallback.
        issue_kind, primary, support, reason, confidence = self._infer(text, findings, {})
        hint = dict(environment_hint or {})
        if not primary and bool(hint.get("active")):
            hinted = str(hint.get("primary_tool_hint") or "")
            if hinted in _TOOL_BY_ID:
                primary = hinted
                support = tuple(str(x) for x in (hint.get("supporting_tool_hints") or ()) if str(x) in _TOOL_BY_ID)[:2]
                issue_kind = str(hint.get("correlation_kind") or "cross_environment")
                reason = "fresh_environment_correlation_fills_evidence_gap"
                confidence = max(0.55, min(0.95, float(hint.get("confidence", 0.0) or 0.0)))
        if not primary:
            issue_kind, primary, support, reason, confidence = self._infer(text, findings, flow)
        if not primary:
            return ToolLiteracyDirective()
        definition = _TOOL_BY_ID[primary]
        # Tool phụ chỉ hỗ trợ trả lời câu hỏi của tool chính, không tạo checklist dài.
        support = tuple(x for x in dict.fromkeys(support) if x != primary and x in _TOOL_BY_ID)[:2]
        directive = ToolLiteracyDirective(
            active=True,
            issue_kind=issue_kind,
            question_vi=definition.question_vi,
            primary_tool=primary,
            supporting_tools=support,
            reason=reason,
            first_action_vi=definition.first_action_vi,
            evidence_vi=definition.evidence_vi,
            common_mistake_vi=definition.common_mistake_vi,
            confidence=confidence,
        )
        if compact:
            # Không thay nội dung, chỉ để public writer biết không cần giảng tool phụ.
            directive.supporting_tools = directive.supporting_tools[:1]
        return directive

    def register_recommendation(self, directive: ToolLiteracyDirective, *, now: float | None = None) -> None:
        if not directive.active or directive.primary_tool not in self.tools:
            return
        ts = float(now or time.time())
        with self._lock:
            state = self.tools[directive.primary_tool]
            state.exposures += 1
            state.last_recommended_at = ts
            state.clamp()
            self._event_locked(
                "tool_recommended", ts,
                tools=[directive.primary_tool, *directive.supporting_tools],
                code=directive.issue_kind,
            )
            self.updated_at = ts
            self._save_locked()

    def projection(self, tool_ids: Sequence[str]) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {}
            for tool_id in tool_ids:
                definition = _TOOL_BY_ID.get(str(tool_id))
                state = self.tools.get(str(tool_id))
                if not definition or not state:
                    continue
                out[tool_id] = {
                    "title_vi": definition.title_vi,
                    "family": definition.family,
                    **state.to_dict(),
                }
            return out

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": _SCHEMA,
                "updated_at": self.updated_at,
                "tools": {
                    item.tool_id: {
                        "title_vi": item.title_vi,
                        "family": item.family,
                        **self.tools[item.tool_id].to_dict(),
                    }
                    for item in _TOOL_CATALOG
                },
                "recent_events": list(self.recent_events[-40:]),
            }

    @staticmethod
    def _infer(text: str, findings: Sequence[str], flow: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...], str, float]:
        fset = set(findings)

        # Flow finding có evidence cụ thể được ưu tiên hơn keyword của câu chat.
        if "review_not_observed" in fset or "scope_expansion" in fset:
            return "change_review", "git_diff", ("targeted_tests",), "recent_flow_requires_change_review", 0.94
        if "repeated_test_failure" in fset:
            return "failing_test", "targeted_tests", ("stack_log", "debugger"), "same_test_is_repeating", 0.95
        if "no_structured_verification" in fset:
            return "verification_gap", "targeted_tests", ("git_diff",), "recent_flow_has_no_structured_verification", 0.92
        if "patch_before_inspection" in fset:
            return "premature_patch", "code_search", ("git_diff",), "recent_flow_patched_before_inspection", 0.93
        if "repeated_same_failure" in fset:
            return "root_cause_unknown", "debugger", ("stack_log", "code_search"), "repeated_retry_needs_runtime_evidence", 0.90

        # Performance phải đo trước; không khuyên đọc code mù.
        if any(x in text for x in ("lag", "giật", "fps", "chậm", "performance", "hiệu năng", "frame time", "spike")):
            return "performance", "profiler", ("stack_log",), "performance_question_needs_measurement", 0.92

        is_godot = any(x in text for x in ("godot", ".gd", ".tscn", "node", "scene"))
        runtime_state = any(x in text for x in (
            "collision", "va chạm", "transform", "scale", "position", "vị trí", "anchor", "shadow", "bóng",
            "animation", "spawn", "instance", "runtime", "remote scene",
        ))
        if is_godot and runtime_state:
            return "godot_runtime_state", "godot_remote_scene", ("godot_debugger", "code_search"), "godot_runtime_state_should_be_inspected_live", 0.90
        if is_godot and any(x in text for x in ("error", "lỗi", "warning", "physics", "process", "monitor")):
            return "godot_runtime_error", "godot_debugger", ("stack_log", "godot_remote_scene"), "godot_runtime_error_needs_engine_evidence", 0.88

        if any(x in text for x in ("stack trace", "exception", "crash", "traceback", "runtime error", "log báo", "error log")):
            return "runtime_exception", "stack_log", ("debugger", "code_search"), "runtime_exception_has_ordered_evidence", 0.91

        if any(x in text for x in ("type error", "compiler", "compile", "diagnostic", "problems", "lsp", "dòng đỏ", "import error")):
            return "static_diagnostic", "vscode_diagnostics", ("code_search", "targeted_tests"), "static_error_should_start_from_diagnostics", 0.91

        if any(x in text for x in ("diff", "ai sửa", "agent sửa", "gitpilot sửa", "sửa sai file", "vượt scope", "đổi gì")):
            return "change_review", "git_diff", ("targeted_tests", "code_search"), "recent_change_should_be_read_as_diff", 0.89

        if any(x in text for x in ("test fail", "test lỗi", "regression", "test nào", "verify", "verification")):
            return "verification", "targeted_tests", ("git_diff", "stack_log"), "verification_question_needs_small_reproduction", 0.88

        if any(x in text for x in ("breakpoint", "debugger", "state sai", "giá trị sai", "branch", "call stack")):
            return "runtime_state", "debugger", ("stack_log", "code_search"), "runtime_state_needs_breakpoint_evidence", 0.87

        if any(x in text for x in (
            "file nào", "đọc cái nào", "bắt đầu từ đâu", "ở đâu", "call flow", "luồng", "trace", "reference",
            "hàm nào", "function nào", "class nào", "shared system",
        )):
            return "code_navigation", "code_search", ("vscode_diagnostics",), "location_or_flow_is_unknown", 0.86

        # Nếu flow có changed files nhưng user đang bàn luận task chung, diff là evidence
        # an toàn nhất; chỉ kích hoạt khi flow thật sự có thay đổi.
        if flow.get("changed_files"):
            return "recent_change_review", "git_diff", ("targeted_tests",), "recent_flow_changed_files", 0.70

        return "", "", (), "", 0.0

    def _event_locked(
        self,
        event_type: str,
        ts: float,
        *,
        tools: Sequence[str] = (),
        code: str = "",
        ref: str = "",
    ) -> None:
        self.recent_events.append({
            "at": ts,
            "event": str(event_type)[:80],
            "tools": [str(x)[:80] for x in tools[:4]],
            "code": str(code or "")[:80],
            "evidence_ref": str(ref or "")[:120],
        })
        self.recent_events = self.recent_events[-60:]

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or str(raw.get("schema") or "") != _SCHEMA:
                return
            tools = raw.get("tools")
            if isinstance(tools, Mapping):
                for tool_id in self.tools:
                    item = tools.get(tool_id)
                    if isinstance(item, Mapping):
                        self.tools[tool_id] = ToolState.from_dict(item)
            self.recent_events = [dict(x) for x in (raw.get("recent_events") or []) if isinstance(x, Mapping)][-60:]
            self.updated_at = float(raw.get("updated_at", 0.0) or 0.0)
        except Exception:
            return

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            # Tool literacy chỉ hỗ trợ mentoring; persistence lỗi không được làm hỏng chat.
            pass


__all__ = [
    "EngineeringToolLiteracy",
    "ToolDefinition",
    "ToolLiteracyDirective",
    "ToolState",
]
