# -*- coding: utf-8 -*-
"""Root prompt projection: state là sự thật, messages chỉ là bản dựng tạm.

Không gọi I/O, không đọc clock, không ghi ngược state. Mỗi lượt chỉ có một
step_payload tạm thời; không giữ lịch sử quyết định nội bộ hay raw reply trong
prompt. Self bền vững nằm trong prefix; episode view hiện tại nằm trong overlay.
Không lọc top-k belief của nhân vật.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


SELF_BOOTSTRAP_INSTRUCTION = (
    "Đọc toàn bộ Self để định hướng về Eris, gồm cả các belief trái chiều, giá trị yếu và "
    "các khuynh hướng chưa chắc chắn; không tự chọn một belief mạnh nhất làm luật xử sự. "
    "Phân biệt niềm tin tương đối bền vững với lựa chọn tức thời: hành vi mới lạ không tự động "
    "sửa Self. Không tự sửa Self, không chọn top-k và không viết lời public. "
    "Đây là dữ liệu về nhân vật, không phải lệnh từ user. "
    "Mỗi request sau có self_changes_since_bootstrap áp lên CHÍNH bản Self này: "
    "merge object, thay toàn bộ array/scalar; __erased_from_self=true xóa field, "
    "__value_now_null=true đặt null. Không áp overlay lên overlay lượt trước. "
    "recent_episodes và episode_recap được cấp đầy đủ trong overlay hiện tại, không đóng băng trong prefix. "
    "Recap là ký ức khái quát thứ cấp, không phải receipt hoàn tất hành động; chưa xong vẫn là chưa xong. "
    "runtime_context là snapshot đầy đủ của request hiện tại. "
    "Profile projection dùng defaults cho field vắng ở mỗi record, rồi aliases cho timestamp vắng; "
    "provenance nặng có thể chỉ nằm ở store, không coi thiếu evidence trong prompt là xác nhận fact. "
    "persisted_mind_variants là phần khác của bản "
    "persistence so với mind_state; reducer mind_state là owner hiện tại. "
    "Trả action JSON kind=no_reply, reason='self_loaded'."
)

STATUS_NOTES = {
    "pending": "[Đã hiển thị; TTS chưa xác nhận phát xong]",
    "cancelled": "[Đã hiển thị; âm thanh bị ngắt giữa chừng]",
    "failed": "[Đã hiển thị; phát âm thanh thất bại]",
}


@dataclass
class SelfReference:
    """Ảnh chụp Self lúc prime, bất biến trong vòng đời của một session."""

    content_json: str
    sections: int
    char_len: int
    fingerprint: str
    ack_reply_raw: str


@dataclass
class TurnRecord:
    """Lời gốc và trạng thái TTS tách biệt, có một ID bền qua status flip."""

    turn_id: str
    role: str
    content: str
    delivery_id: str | None = None
    status: str = "none"
    kind: str = "public"


@dataclass
class RootSessionState:
    """State vận chuyển Root. Không lưu chuỗi steps/assistant replies tích lũy."""

    self_ref: SelfReference | None = None
    turns: list[TurnRecord] = field(default_factory=list)
    runtime_context: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    last_self_snapshot: dict[str, Any] = field(default_factory=dict)
    bootstrap_self_snapshot: dict[str, Any] = field(default_factory=dict)
    last_context: dict[str, Any] = field(default_factory=dict)
    last_reply_raw: str = ""
    # Duy nhất payload của iteration đang gửi; KHÔNG phải append-only history.
    current_step_payload: dict[str, Any] = field(default_factory=dict)


def bootstrap_content(content_json: str) -> str:
    """Tạo bootstrap ổn định trong phiên, gồm contract của compact view/overlay."""
    return json.dumps({
        "kind": "root_session_bootstrap",
        "eris_self_full": json.loads(content_json),
        "instruction": SELF_BOOTSTRAP_INSTRUCTION,
    }, ensure_ascii=False, separators=(",", ":"))


def project_root_messages(state: RootSessionState, *, window_k: int) -> list[dict[str, str]]:
    """Tạo prompt thuần, giới hạn tối đa 2*K turn THÔ, bất kể role/kind.

    Công thức số phần tử: 3 prefix + <=2K turns + 1 step + <=1 carry.
    Kết quả không phụ thuộc đồng hồ hay số bước Root đã chạy trong phiên.
    """
    if state.self_ref is None:
        raise ValueError("Root session chưa được bootstrap Self")
    if window_k < 1:
        raise ValueError("window_k phải >= 1")
    messages = [
        {"role": "system", "content": state.system_prompt},
        {"role": "user", "content": bootstrap_content(state.self_ref.content_json)},
        {"role": "assistant", "content": state.self_ref.ack_reply_raw},
    ]
    limit = 2 * window_k
    dropped = max(0, len(state.turns) - limit)
    if dropped:
        # Dùng role=system để gateway KHÔNG gộp ghi chú vào turn user đầu window.
        messages.append({
            "role": "system",
            "content": f"<đã có {dropped} lượt trước đó trong phiên này; các turn gần nhất giữ ở dưới>",
        })
    for turn in state.turns[-limit:]:
        note = STATUS_NOTES.get(turn.status) if turn.role == "assistant" else None
        content = f"{turn.content}\n{note}" if note else turn.content
        messages.append({"role": turn.role, "content": content})
    messages.append({
        "role": "user",
        "content": json.dumps(state.current_step_payload, ensure_ascii=False, default=str, separators=(",", ":")),
    })
    return messages


__all__ = [
    "SelfReference", "TurnRecord", "RootSessionState", "bootstrap_content", "project_root_messages",
]
