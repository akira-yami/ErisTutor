from __future__ import annotations

"""Registry năng lực public của Eris, kèm availability runtime.

Một nguồn sự thật duy nhất cho câu hỏi "em làm được gì". Registry không chỉ
liệt kê chức năng trên giấy mà còn chụp trạng thái đang bật/tắt, vì khoe một tool
đang chết là kiểu quảng cáo rất người nhưng không cần bắt chước.
"""

from dataclasses import dataclass, replace
import hashlib
import json
import os
from typing import Any, Iterable, Mapping

from actions.contracts import VALID_SYSTEM_ACTION_LIST


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    id: str
    public_name_vi: str
    description_vi: str
    effect: str
    risk_level: str = "low"
    runtime_flag: str = ""
    requires_tool: str = ""
    visible_in_ui_group: str = "desktop"
    exposure: str = "executive"
    callable: bool = True
    live_available: bool = True


_OBSERVATIONAL_CAPABILITY_IDS = (
    "observe_active_app",
    "observe_window_title",
    "observe_idle_state",
    "read_uia_text",
    "semantic_screen_vision",
    "camera_view",
    "meet_participants",
    "telemetry_summary",
)


_CAPABILITY_DATA: dict[str, CapabilityDefinition] = {
    "canvas": CapabilityDefinition(
        id="canvas", effect="control", public_name_vi="Learning Canvas Java DI",
        description_vi="Root capability canvas mở nhóm DI, đọc nội dung và đề xuất focus trong Godot native; dùng guide canvas, không gọi qua action executor.",
        visible_in_ui_group="knowledge",
    ),
    "python_workspace": CapabilityDefinition(
        id="python_workspace", effect="control", public_name_vi="chạy Python sandbox Docker",
        description_vi="Chạy code Python do Eris viết trong Docker không mạng; xuất stdout và ảnh PNG lên Workspace Panel. Chỉ khả dụng khi có Docker",
        risk_level="medium", visible_in_ui_group="knowledge",
    ),
    "live_workspace_edit": CapabilityDefinition(
        id="live_workspace_edit", effect="control", public_name_vi="sửa tài liệu đang hiển thị",
        description_vi="Đọc revision hoặc chèn, thay, xóa khối trong Panel đang mở. Không thay transcript/TTS; dùng document_id và revision để chống ghi đè.",
        risk_level="low", visible_in_ui_group="knowledge",
    ),
    "parallel_work": CapabilityDefinition(
        id="parallel_work", effect="control", public_name_vi="giao việc nền",
        description_vi="Giao nhiệm vụ nền, xem goals đã adopt, chạy start_goal, đọc tiến độ và sửa Panel. Skills và context tồn tại qua các bước; Root vẫn giữ quyền nói.",
        risk_level="medium", visible_in_ui_group="knowledge",
    ),
    "math_workspace": CapabilityDefinition(
        id="math_workspace", effect="control", public_name_vi="tính toán và vẽ đồ thị",
        description_vi="Thực thi SymPy/NumPy để tính ma trận, đạo hàm, tích phân, nghiệm và dựng đồ thị/công thức vào Workspace Panel; nhận JSON có cấu trúc, không chạy Python tùy ý",
        risk_level="medium", visible_in_ui_group="knowledge",
    ),
    "get_local_time": CapabilityDefinition(
        id="get_local_time", effect="observe", public_name_vi="xem thời gian hệ thống",
        description_vi="đọc giờ/ngày hiện tại từ runtime hệ thống",
        visible_in_ui_group="observation",
    ),
    "get_active_application": CapabilityDefinition(
        id="get_active_application", effect="observe", public_name_vi="xem ứng dụng đang active",
        description_vi="đọc app và tiêu đề cửa sổ từ telemetry thật; không tự suy ra nội dung file",
        visible_in_ui_group="environment",
    ),
    "read_visible_ui_text": CapabilityDefinition(
        id="read_visible_ui_text", effect="observe", public_name_vi="đọc text UI đang hiện",
        description_vi="đọc block UIA/OCR đủ tươi, có confidence và privacy guard",
        visible_in_ui_group="environment",
    ),
    "inspect_runtime_log": CapabilityDefinition(
        id="inspect_runtime_log", effect="inspect", public_name_vi="đọc log runtime",
        description_vi="đọc phần cuối log eris_run mới nhất và che khóa bí mật",
        visible_in_ui_group="environment",
    ),
    "read_current_error": CapabilityDefinition(
        id="read_current_error", effect="inspect", public_name_vi="đọc lỗi hiện tại",
        description_vi="tìm traceback/error cụ thể gần nhất từ log hoặc text màn hình",
        visible_in_ui_group="environment",
    ),
    "inspect_selected_file": CapabilityDefinition(
        id="inspect_selected_file", effect="inspect", public_name_vi="xem file đang được chọn",
        description_vi="xác định file từ bridge/path/title; chỉ đọc nội dung khi có path thật trong project",
        visible_in_ui_group="environment",
    ),
    "get_project_status": CapabilityDefinition(
        id="get_project_status", effect="inspect", public_name_vi="xem trạng thái project",
        description_vi="đọc Git branch và các mục staged/modified/untracked bằng lệnh read-only",
        visible_in_ui_group="environment",
    ),
    "project_list_files": CapabilityDefinition(
        id="project_list_files", effect="read", public_name_vi="liệt kê file project",
        description_vi="đọc cây file trong workspace, tự lọc cache và binary", visible_in_ui_group="project",
    ),
    "project_read_file": CapabilityDefinition(
        id="project_read_file", effect="read", public_name_vi="đọc file project",
        description_vi="đọc nội dung file source trong project kèm hash evidence", visible_in_ui_group="project",
    ),
    "project_search_text": CapabilityDefinition(
        id="project_search_text", effect="read", public_name_vi="tìm text trong project",
        description_vi="tìm text hoặc regex trong source project", visible_in_ui_group="project",
    ),
    "project_find_symbol": CapabilityDefinition(
        id="project_find_symbol", effect="read", public_name_vi="tìm symbol",
        description_vi="tìm class, function hoặc symbol theo tên", visible_in_ui_group="project",
    ),
    "project_semantic_search": CapabilityDefinition(
        id="project_semantic_search", effect="read", public_name_vi="tìm code theo ngữ nghĩa",
        description_vi="tìm đoạn code liên quan theo ý nghĩa bằng semantic index; dùng để định hướng trước khi đọc source chính xác",
        visible_in_ui_group="project",
    ),
    "project_git_log": CapabilityDefinition(
        id="project_git_log", effect="read", public_name_vi="đọc lịch sử Git",
        description_vi="đọc commit history gần đây để hiểu ý định, convention và vùng code đang tiến hóa", visible_in_ui_group="project",
    ),
    "project_dependency_summary": CapabilityDefinition(
        id="project_dependency_summary", effect="read", public_name_vi="đọc dependency project",
        description_vi="tóm tắt dependency manifests theo tên package mà không nhét lockfile lớn vào context", visible_in_ui_group="project",
    ),
    "project_test_inventory": CapabilityDefinition(
        id="project_test_inventory", effect="read", public_name_vi="kiểm kê test suite",
        description_vi="liệt kê test file, config và framework để lập kế hoạch verification có căn cứ", visible_in_ui_group="project",
    ),
    "project_git_diff": CapabilityDefinition(
        id="project_git_diff", effect="read", public_name_vi="xem diff project",
        description_vi="đọc git diff hiện tại bằng evidence thật", visible_in_ui_group="project",
    ),
    "project_apply_patch": CapabilityDefinition(
        id="project_apply_patch", effect="write", public_name_vi="áp patch project",
        description_vi="snapshot file rồi áp unified diff trong workspace",
        risk_level="high", visible_in_ui_group="project",
    ),
    "project_run_tests": CapabilityDefinition(
        id="project_run_tests", effect="control", public_name_vi="chạy test project",
        description_vi="chạy test và chỉ báo đạt khi exit code bằng 0",
        risk_level="medium", visible_in_ui_group="project",
    ),
    "project_run_command": CapabilityDefinition(
        id="project_run_command", effect="control", public_name_vi="chạy lệnh project",
        description_vi="chạy command phát triển trong project root theo command policy",
        risk_level="high", visible_in_ui_group="project",
    ),
    "project_rollback": CapabilityDefinition(
        id="project_rollback", effect="write", public_name_vi="rollback thay đổi",
        description_vi="khôi phục snapshot operation gần nhất hoặc được chỉ định",
        risk_level="high", visible_in_ui_group="project",
    ),
    "project_package_patch": CapabilityDefinition(
        id="project_package_patch", effect="write", public_name_vi="đóng gói patch ZIP",
        description_vi="đóng gói đúng file đã thay đổi kèm ghi chú tiếng Việt",
        risk_level="medium", visible_in_ui_group="project",
    ),
    "project_execute_task": CapabilityDefinition(
        id="project_execute_task", effect="write", public_name_vi="tác vụ code one-shot (legacy)",
        description_vi="compatibility contract cũ; production coding phải đi qua Unified Agent Harness",
        risk_level="high", visible_in_ui_group="project", exposure="compatibility", callable=False,
    ),
    "workspace_join": CapabilityDefinition(
        id="workspace_join", effect="write", public_name_vi="gia nhập workspace",
        description_vi="nhận Git URL, path local hoặc ZIP rồi đăng ký dưới ErisLive",
        risk_level="medium", visible_in_ui_group="project",
    ),
    "workspace_switch": CapabilityDefinition(
        id="workspace_switch", effect="control", public_name_vi="chuyển workspace",
        description_vi="chuyển project đang active sang workspace đã đăng ký", visible_in_ui_group="project",
    ),
    "workspace_list": CapabilityDefinition(
        id="workspace_list", effect="read", public_name_vi="liệt kê workspace",
        description_vi="xem các repo/project người dùng đã giao cho Eris", visible_in_ui_group="project",
    ),
    "workspace_leave": CapabilityDefinition(
        id="workspace_leave", effect="write", public_name_vi="rời workspace",
        description_vi="gỡ workspace khỏi registry, mặc định giữ nguyên dữ liệu",
        risk_level="medium", visible_in_ui_group="project",
    ),
    "launch_app": CapabilityDefinition(
        id="launch_app", effect="control", public_name_vi="mở ứng dụng",
        description_vi="mở app/chương trình có trong cấu hình runtime",
        runtime_flag="ERIS_ACTION_LAUNCH_APP_ENABLED",
    ),
    "focus_window": CapabilityDefinition(
        id="focus_window", effect="control", public_name_vi="focus cửa sổ",
        description_vi="đưa một cửa sổ/app đang mở lên trước",
        runtime_flag="ERIS_ACTION_FOCUS_WINDOW_ENABLED",
    ),
    "set_volume": CapabilityDefinition(
        id="set_volume", effect="control", public_name_vi="chỉnh âm lượng",
        description_vi="tăng/giảm/mute âm lượng hệ thống",
        runtime_flag="ERIS_ACTION_SET_VOLUME_ENABLED",
    ),
    "play_pause_media": CapabilityDefinition(
        id="play_pause_media", effect="control", public_name_vi="điều khiển media",
        description_vi="play/pause/next/previous media bằng phím hệ thống",
        runtime_flag="ERIS_ACTION_MEDIA_ENABLED",
    ),
    "copy_text": CapabilityDefinition(
        id="copy_text", effect="write", public_name_vi="copy text",
        description_vi="đưa đoạn text vào clipboard",
        runtime_flag="ERIS_ACTION_COPY_TEXT_ENABLED",
    ),
    "open_url": CapabilityDefinition(
        id="open_url", effect="control", public_name_vi="mở đường dẫn",
        description_vi="mở URL hợp lệ trong trình duyệt",
        risk_level="medium", runtime_flag="ERIS_ACTION_OPEN_URL_ENABLED",
    ),
    "get_weather": CapabilityDefinition(
        id="get_weather", effect="observe", public_name_vi="xem thời tiết",
        description_vi="lấy dự báo/thời tiết theo địa điểm nếu có tool",
        runtime_flag="ERIS_WEATHER_ENABLED", requires_tool="weather",
        visible_in_ui_group="knowledge",
    ),
    "get_now_playing": CapabilityDefinition(
        id="get_now_playing", effect="observe", public_name_vi="xem media đang phát",
        description_vi="đọc nhanh track/app media đang phát nếu hệ điều hành cho phép",
        runtime_flag="ERIS_NOW_PLAYING_ENABLED",
    ),
    "observe_active_app": CapabilityDefinition(
        id="observe_active_app", effect="observe", public_name_vi="biết ứng dụng đang active",
        description_vi="nhận tên ứng dụng đang được focus từ machine runtime",
        visible_in_ui_group="observation",
        exposure="context_only", callable=False,
    ),
    "observe_window_title": CapabilityDefinition(
        id="observe_window_title", effect="observe", public_name_vi="biết tiêu đề cửa sổ",
        description_vi="nhận tiêu đề cửa sổ active khi hệ điều hành cung cấp",
        visible_in_ui_group="observation",
        exposure="context_only", callable=False,
    ),
    "observe_idle_state": CapabilityDefinition(
        id="observe_idle_state", effect="observe", public_name_vi="biết trạng thái idle/gõ",
        description_vi="nhận thời gian idle và tín hiệu user đang hoạt động/gõ",
        visible_in_ui_group="observation",
        exposure="context_only", callable=False,
    ),
    "read_uia_text": CapabilityDefinition(
        id="read_uia_text", effect="observe", public_name_vi="đọc một phần chữ qua UI Automation",
        description_vi="đọc text control mà Windows UI Automation cho phép; không đồng nghĩa nhìn toàn màn hình",
        visible_in_ui_group="observation",
        exposure="context_only", callable=False,
    ),
    "semantic_screen_vision": CapabilityDefinition(
        id="semantic_screen_vision", effect="observe", public_name_vi="hiểu nội dung ảnh màn hình",
        description_vi="phân tích semantic từ frame màn hình khi vision compressor thực sự khả dụng",
        visible_in_ui_group="observation", live_available=False,
        exposure="context_only", callable=False,
    ),
    "camera_view": CapabilityDefinition(
        id="camera_view", effect="observe", public_name_vi="nhìn camera",
        description_vi="nhìn video/khuôn mặt từ camera",
        visible_in_ui_group="observation", live_available=False,
        exposure="context_only", callable=False,
    ),
    "meet_participants": CapabilityDefinition(
        id="meet_participants", effect="observe", public_name_vi="nhìn người trong Meet",
        description_vi="nhận diện người/khuôn mặt trong cuộc gọi Meet",
        visible_in_ui_group="observation", live_available=False,
        exposure="context_only", callable=False,
    ),
    "telemetry_summary": CapabilityDefinition(
        id="telemetry_summary", effect="observe", public_name_vi="đọc telemetry runtime",
        description_vi="đọc snapshot như active app, idle, scene và health nội bộ đã được runtime cấp",
        visible_in_ui_group="observation",
        exposure="context_only", callable=False,
    ),
    "review_discourse_claim": CapabilityDefinition(
        id="review_discourse_claim", effect="inspect", public_name_vi="kiểm lại claim hội thoại",
        description_vi="đọc claim public đang bị sửa và nêu đúng phần Eris đã hiểu lệch",
        visible_in_ui_group="cognition",
    ),
    "inspect_unresolved_questions": CapabilityDefinition(
        id="inspect_unresolved_questions", effect="inspect", public_name_vi="xem câu hỏi còn bỏ ngỏ",
        description_vi="kiểm tra discourse focus để tìm câu hỏi hoặc correction chưa được đóng",
        visible_in_ui_group="cognition",
    ),
    "inspect_memory_candidates": CapabilityDefinition(
        id="inspect_memory_candidates", effect="inspect", public_name_vi="kiểm tra memory candidate",
        description_vi="đếm mảnh ký ức chưa consolidation mà không coi chúng là fact",
        visible_in_ui_group="cognition",
    ),
    "compare_recent_turns": CapabilityDefinition(
        id="compare_recent_turns", effect="inspect", public_name_vi="so sánh lượt nói gần đây",
        description_vi="đối chiếu hai câu public gần nhất để phát hiện lặp ý",
        visible_in_ui_group="cognition",
    ),
    "inspect_social_state": CapabilityDefinition(
        id="inspect_social_state", effect="inspect", public_name_vi="xem social state của Eris",
        description_vi="đọc warmth, irritation, play-contract và mức cáu của chính Eris",
        visible_in_ui_group="cognition",
    ),
    "detect_internal_contradictions": CapabilityDefinition(
        id="detect_internal_contradictions", effect="inspect", public_name_vi="tìm mâu thuẫn trong lời Eris",
        description_vi="đối chiếu claim public gần đây để phát hiện cặp phủ định mâu thuẫn",
        visible_in_ui_group="cognition",
    ),
    "summarize_open_loops": CapabilityDefinition(
        id="summarize_open_loops", effect="inspect", public_name_vi="tổng kết open loop nhận thức",
        description_vi="gom claim bị sửa, câu hỏi bỏ ngỏ và memory candidate chưa xử lý",
        visible_in_ui_group="cognition",
    ),
    "inspect_capability_health": CapabilityDefinition(
        id="inspect_capability_health", effect="inspect", public_name_vi="kiểm tra capability health",
        description_vi="đọc snapshot năng lực nào đang available hoặc unavailable",
        visible_in_ui_group="cognition",
    ),
}




def canonicalize_action_id(action_id: str) -> str:
    """Normalize an exact capability ID without guessing aliases."""
    return str(action_id or "").strip().lower()



# Exact-ID argument contracts. These are mechanical interfaces, not language
# markers: they validate the JSON object selected by Gemma and adapt it to the
# legacy string boundary while handlers are migrated incrementally.
def _object_schema(properties: Mapping[str, Any] | None = None, required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties or {}),
        "required": list(required),
    }


def _string(description: str = "", *, enum: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if description:
        schema["description"] = description
    if enum:
        schema["enum"] = list(enum)
    return schema


def _integer(description: str = "", *, minimum: int | None = None, maximum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if description:
        schema["description"] = description
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def _boolean(description: str = "") -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "boolean"}
    if description:
        schema["description"] = description
    return schema


_EMPTY_INPUT = _object_schema()

_CAPABILITY_INPUT_CONTRACTS: dict[str, tuple[dict[str, Any], str]] = {
    "canvas": (_object_schema({
        "operation": _string("Root Canvas operation", enum=("library", "open_group", "snapshot", "focus", "accept_focus")),
        "base_revision": _integer("Revision từ snapshot", minimum=0),
        "group_id": _string("ID nhóm từ Canvas library"),
        "node_id": _string("ID nút từ snapshot"),
    }, ("operation",)), "json"),
    "python_workspace": (_object_schema({
        "code": _string("Source Python <= 12 KB, chạy trong Docker không mạng"),
    }, ("code",)), "json"),
    "live_workspace_edit": (_object_schema({
        "document_id": _string("ID tài liệu hiện có"),
        "operation": _string("inspect / insert_block / replace_block / remove_block", enum=("inspect","insert_block","replace_block","remove_block")),
        "block_id": _string("ID khối cần thay đổi"),
        "base_revision": _integer("Phiên bản đã đọc; không dùng revision cũ", minimum=0),
        "block": {"type":"object", "description":"Khối paragraph, heading, formula, code, list hoặc table"},
    }, ("document_id","operation")), "json"),
    "parallel_work": (_object_schema({
        "operation": _string("start / status / recent / resume / cancel / document / edit_document / goals / goal_status / start_goal", enum=("start", "status", "recent", "resume", "cancel", "document", "edit_document", "goals", "goal_status", "start_goal")),
        "task": _string("Nhiệm vụ mà Root/Identity tự quyết định giao; không phải lời thoại"),
        "job_id": _string("Định danh công việc đã nhận từ start/recent"),
        "goal_id": _string("Goal do Eris đã commit trong Goal Ledger; start_goal không tự adopt"),
        "answer": _string("Thông tin mới từ người dùng để resume job đang waiting_user"),
        "document_operation": _string("insert_block / replace_block / remove_block", enum=("insert_block", "replace_block", "remove_block")),
        "block_id": _string("ID khối tài liệu của phiên Work"),
        "base_revision": _integer("Phiên bản tài liệu đã đọc (CAS)", minimum=0),
        "block": {"type": "object", "description": "Khối heading/paragraph/formula/code/table"},
    }, ("operation",)), "json"),
    "math_workspace": (_object_schema({
        "kind": _string("matrix / simplify / differentiate / integrate / solve / plot / binary_determinant_max", enum=("matrix","simplify","differentiate","integrate","solve","plot","binary_determinant_max")),
        "size": _integer("Cấp của ma trận 0/1; binary_determinant_max hỗ trợ size=3", minimum=3, maximum=3),
        "expression": _string("Biểu thức toán chỉ dùng x,y; với ma trận, dùng rows"),
        "rows": {"type": "array", "description": "Ma trận 2 chiều, tối đa 8x8"},
        "xmin": {"type": "number"},
        "xmax": {"type": "number"},
        "title": _string("Tiêu đề trên panel"),
    }, ("kind",)), "json"),
    "get_local_time": (_EMPTY_INPUT, "none"),
    "get_now_playing": (_EMPTY_INPUT, "none"),
    "focus_window": (_object_schema({"title": _string("Window title or application name")}, ("title",)), "field:title"),
    "open_url": (_object_schema({"url": _string("Absolute URL or hostname")}, ("url",)), "field:url"),
    "set_volume": (_object_schema({
        "level": _integer("Absolute volume level", minimum=0, maximum=100),
        "command": _string("Relative command", enum=("mute", "unmute", "up", "down")),
    }), "volume"),
    "play_pause_media": (_EMPTY_INPUT, "none"),
    "copy_text": (_object_schema({"text": _string("Text to put on the clipboard")}, ("text",)), "field:text"),
    "launch_app": (_object_schema({"command": _string("Configured application name or executable")}, ("command",)), "field:command"),
    "get_weather": (_object_schema({"location": _string("Location to query")}, ("location",)), "field:location"),
    "review_discourse_claim": (_EMPTY_INPUT, "none"),
    "inspect_unresolved_questions": (_EMPTY_INPUT, "none"),
    "inspect_memory_candidates": (_object_schema({"focus": _string("Optional memory focus")}), "field:focus"),
    "compare_recent_turns": (_object_schema({"focus": _string("Optional comparison focus")}), "field:focus"),
    "inspect_social_state": (_EMPTY_INPUT, "none"),
    "detect_internal_contradictions": (_EMPTY_INPUT, "none"),
    "summarize_open_loops": (_EMPTY_INPUT, "none"),
    "inspect_capability_health": (_EMPTY_INPUT, "none"),
    "get_active_application": (_EMPTY_INPUT, "none"),
    "read_visible_ui_text": (_object_schema({"query": _string("Optional text or region focus")}), "field:query"),
    "inspect_runtime_log": (_object_schema({"query": _string("Optional log search focus")}), "field:query"),
    "read_current_error": (_EMPTY_INPUT, "none"),
    "inspect_selected_file": (_object_schema({"include_content": _boolean("Read a safe preview when a real path is available")}), "selected_file"),
    "get_project_status": (_EMPTY_INPUT, "none"),
    "project_list_files": (_object_schema({
        "pattern": _string("Optional filename filter"),
        "limit": _integer("Maximum returned files", minimum=1, maximum=3000),
    }), "json"),
    "project_read_file": (_object_schema({
        "path": _string("Workspace-relative path"),
        "max_chars": _integer("Maximum characters", minimum=200, maximum=120000),
    }, ("path",)), "json"),
    "project_search_text": (_object_schema({
        "query": _string("Text or regex to search"),
        "regex": _boolean("Treat query as regex"),
        "limit": _integer("Maximum matches", minimum=1, maximum=1000),
    }, ("query",)), "json"),
    "project_find_symbol": (_object_schema({
        "symbol": _string("Class, function, or symbol name"),
        "limit": _integer("Maximum matches", minimum=1, maximum=500),
    }, ("symbol",)), "json"),
    "project_semantic_search": (_object_schema({
        "query": _string("Natural-language description of the code or behavior to find"),
        "limit": _integer("Maximum semantic matches", minimum=1, maximum=20),
        "reindex": _boolean("Refresh the incremental semantic index before searching"),
    }, ("query",)), "json"),
    "project_git_log": (_object_schema({
        "limit": _integer("Maximum recent commits", minimum=1, maximum=100),
        "path": _string("Optional workspace-relative path to scope history"),
    }), "json"),
    "project_dependency_summary": (_object_schema({
        "limit": _integer("Maximum dependency names across manifests", minimum=1, maximum=300),
    }), "json"),
    "project_test_inventory": (_object_schema({
        "limit": _integer("Maximum test files", minimum=1, maximum=1200),
    }), "json"),
    "project_git_diff": (_object_schema({"staged": _boolean("Read staged diff instead of working tree")}), "json"),
    "project_apply_patch": (_object_schema({"patch": _string("Unified diff")}, ("patch",)), "json"),
    "project_run_tests": (_object_schema({
        "command": _string("Optional test command"),
        "timeout": _integer("Timeout in seconds", minimum=10, maximum=1800),
    }), "json"),
    "project_run_command": (_object_schema({
        "command": _string("Development command") ,
        "timeout": _integer("Timeout in seconds", minimum=5, maximum=1800),
    }, ("command",)), "json"),
    "project_rollback": (_object_schema({"operation_id": _string("Operation to roll back; empty means latest")}), "json"),
    "project_package_patch": (_object_schema({
        "operation_id": _string("Operation to package; empty means latest"),
        "notes_vi": _string("Optional Vietnamese release notes"),
    }), "json"),
    "project_execute_task": (_object_schema({
        "task": _string("Complete coding task") ,
        "workspace_source": _string("Optional Git URL, ZIP, or local path"),
        "workspace_id": _string("Optional workspace id"),
    }, ("task",)), "json"),
    "workspace_join": (_object_schema({
        "source": _string("Git URL, ZIP, or local path"),
        "workspace_id": _string("Optional workspace id"),
    }, ("source",)), "json"),
    "workspace_switch": (_object_schema({"workspace_id": _string("Workspace id or name")}, ("workspace_id",)), "json"),
    "workspace_list": (_EMPTY_INPUT, "none"),
    "workspace_leave": (_object_schema({
        "workspace_id": _string("Workspace id or name"),
        "delete_managed_copy": _boolean("Delete only the Eris-managed copy"),
    }, ("workspace_id",)), "json"),
    # Context-only observation contracts are explicit even though they are not
    # invokable. Complete coverage prevents an accidental empty-schema fallback
    # from silently turning a sensor feed into a callable tool.
    "observe_active_app": (_EMPTY_INPUT, "context_only"),
    "observe_window_title": (_EMPTY_INPUT, "context_only"),
    "observe_idle_state": (_EMPTY_INPUT, "context_only"),
    "read_uia_text": (_EMPTY_INPUT, "context_only"),
    "semantic_screen_vision": (_EMPTY_INPUT, "context_only"),
    "camera_view": (_EMPTY_INPUT, "context_only"),
    "meet_participants": (_EMPTY_INPUT, "context_only"),
    "telemetry_summary": (_EMPTY_INPUT, "context_only"),
}


def capability_input_schema(capability_id: str) -> dict[str, Any]:
    capability_key = str(capability_id or "").strip()
    contract = _CAPABILITY_INPUT_CONTRACTS.get(capability_key)
    if contract is None:
        # Runtime extensions can expose short-lived read-only capabilities that
        # are not part of the static registry. Give those extensions a closed,
        # argument-free contract rather than silently accepting arbitrary data.
        return json.loads(json.dumps(_EMPTY_INPUT, ensure_ascii=False))
    schema, _ = contract
    return json.loads(json.dumps(schema, ensure_ascii=False))


def capability_argument_mode(capability_id: str) -> str:
    capability_key = str(capability_id or "").strip()
    contract = _CAPABILITY_INPUT_CONTRACTS.get(capability_key)
    if contract is None:
        return "none"
    return str(contract[1])


def validate_registry_contract_coverage() -> tuple[bool, tuple[str, ...]]:
    missing = tuple(sorted(set(_CAPABILITY_DATA) - set(_CAPABILITY_INPUT_CONTRACTS)))
    extra = tuple(sorted(set(_CAPABILITY_INPUT_CONTRACTS) - set(_CAPABILITY_DATA)))
    issues = tuple(f"missing:{item}" for item in missing) + tuple(f"extra:{item}" for item in extra)
    return (not issues), issues


def _validate_value(value: Any, schema: Mapping[str, Any], path: str) -> str:
    expected = str(schema.get("type", "") or "")
    if expected == "string" and not isinstance(value, str):
        return f"{path}:expected_string"
    if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return f"{path}:expected_integer"
    if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return f"{path}:expected_number"
    if expected == "boolean" and not isinstance(value, bool):
        return f"{path}:expected_boolean"
    if expected == "object" and not isinstance(value, Mapping):
        return f"{path}:expected_object"
    if "enum" in schema and value not in schema.get("enum", ()):
        return f"{path}:invalid_enum"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path}:below_minimum"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path}:above_maximum"
    return ""


def validate_arguments_against_schema(
    arguments: Mapping[str, Any] | None,
    schema: Mapping[str, Any],
    *,
    capability_id: str = "",
) -> tuple[bool, str]:
    if not isinstance(arguments, Mapping):
        return False, "arguments_must_be_object"
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    missing = [name for name in required if name not in arguments]
    if missing:
        return False, "missing_required:" + ",".join(sorted(missing))
    if schema.get("additionalProperties") is False:
        extra = [name for name in arguments if name not in properties]
        if extra:
            return False, "unexpected_arguments:" + ",".join(sorted(extra))
    for name, value in arguments.items():
        error = _validate_value(value, properties.get(name, {}), name)
        if error:
            return False, error
    if capability_id == "set_volume" and not ("level" in arguments or "command" in arguments):
        return False, "set_volume_requires_level_or_command"
    return True, "valid"


def validate_capability_arguments(capability_id: str, arguments: Mapping[str, Any] | None) -> tuple[bool, str]:
    return validate_arguments_against_schema(
        arguments,
        capability_input_schema(capability_id),
        capability_id=str(capability_id or "").strip(),
    )


def encode_capability_arguments(capability_id: str, arguments: Mapping[str, Any] | None) -> str:
    valid, reason = validate_capability_arguments(capability_id, arguments)
    if not valid:
        raise ValueError(reason)
    payload = dict(arguments or {})
    mode = capability_argument_mode(capability_id)
    if mode == "none":
        return ""
    if mode.startswith("field:"):
        return str(payload.get(mode.split(":", 1)[1], "") or "")
    if mode == "volume":
        if "level" in payload:
            return str(payload["level"])
        return str(payload.get("command", "") or "")
    if mode == "selected_file":
        return "content" if bool(payload.get("include_content", False)) else ""
    if mode == "json":
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    raise ValueError(f"unknown_argument_mode:{mode}")

_coverage_ok, _coverage_issues = validate_registry_contract_coverage()
if not _coverage_ok:
    raise RuntimeError("capability_contract_coverage:" + ",".join(_coverage_issues))


def singing_capability_enabled() -> bool:
    """Singing/SVS là capability riêng, mặc định tắt; TTS không giả làm máy hát."""
    try:
        from runtime.rollout_control import feature_enabled
        return feature_enabled("singing_capability")
    except Exception:
        return os.environ.get("ERIS_SINGING_CAPABILITY", "0").strip().lower() in {"1", "true", "yes", "on"}


def iter_public_capabilities(
    action_ids: Iterable[str] | None = None,
    *,
    runtime_flags: Mapping[str, bool] | None = None,
    include_unavailable: bool = True,
) -> tuple[CapabilityDefinition, ...]:
    ids = tuple(action_ids or VALID_SYSTEM_ACTION_LIST)
    # No prompt-order priority remains. Every registered observational contract
    # is included, and Gemma performs semantic shortlisting from exact IDs.
    ids = (*ids, *_OBSERVATIONAL_CAPABILITY_IDS)
    ids = tuple(dict.fromkeys(ids))
    out: list[CapabilityDefinition] = []
    for action_id in ids:
        cap = _CAPABILITY_DATA.get(str(action_id))
        if cap is None:
            continue
        resolved = replace(cap, live_available=_resolve_available(cap, runtime_flags=runtime_flags))
        if include_unavailable or resolved.live_available:
            out.append(resolved)
    return tuple(out)



def capability_snapshot_id(
    action_ids: Iterable[str] | None = None,
    *,
    runtime_flags: Mapping[str, bool] | None = None,
) -> str:
    payload = [
        {
            "id": cap.id,
            "available": cap.live_available,
            "effect": cap.effect,
            "risk": cap.risk_level,
            "tool": cap.requires_tool,
            "reason": _runtime_reason(cap.id) if runtime_flags is None else "",
        }
        for cap in iter_public_capabilities(action_ids, runtime_flags=runtime_flags)
    ]
    if runtime_flags is None:
        try:
            from actions.capability_runtime import capability_runtime_revision

            payload.append({"runtime_revision": capability_runtime_revision()})
        except Exception:
            pass
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def live_capability_ids(
    action_ids: Iterable[str] | None = None,
    *,
    runtime_flags: Mapping[str, bool] | None = None,
) -> tuple[str, ...]:
    return tuple(
        cap.id for cap in iter_public_capabilities(
            action_ids, runtime_flags=runtime_flags, include_unavailable=False
        )
    )


def capability_definition(capability_id: str) -> CapabilityDefinition | None:
    return _CAPABILITY_DATA.get(str(capability_id or "").strip())


def _resolved_runtime_flags(runtime_flags: Mapping[str, bool] | None) -> Mapping[str, bool] | None:
    if runtime_flags is not None:
        return runtime_flags
    try:
        from actions.capability_runtime import runtime_capability_flags

        flags = runtime_capability_flags()
        return flags or None
    except Exception:
        return None


def _runtime_reason(capability_id: str) -> str:
    try:
        from actions.capability_runtime import capability_runtime_reason

        return capability_runtime_reason(capability_id)
    except Exception:
        return ""


def _resolve_available(cap: CapabilityDefinition, *, runtime_flags: Mapping[str, bool] | None) -> bool:
    flags = _resolved_runtime_flags(runtime_flags)
    if flags is not None:
        if cap.id in flags:
            return bool(flags[cap.id])
        if cap.runtime_flag and cap.runtime_flag in flags:
            return bool(flags[cap.runtime_flag])
    if not cap.runtime_flag:
        return bool(cap.live_available)
    raw = os.getenv(cap.runtime_flag)
    if raw is None or str(raw).strip() == "":
        return bool(cap.live_available)
    return str(raw).strip().lower() not in {"0", "false", "off", "no"}


__all__ = [
    "CapabilityDefinition",
    "iter_public_capabilities",
    "capability_snapshot_id",
    "live_capability_ids",
    "capability_definition",
    "canonicalize_action_id",
    "singing_capability_enabled",
    "_OBSERVATIONAL_CAPABILITY_IDS",
]
