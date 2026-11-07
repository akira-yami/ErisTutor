# -*- coding: utf-8 -*-
"""disposition.py — cây cầu state → ngôn ngữ cho ERIS.

Vấn đề cũ: ``to_prompt_context`` đổ trạng thái nội tâm vào prompt dưới dạng
bảng số (``curiosity=0.42/0.08, restraint=0.30/0.12...``). LLM nhận telemetry,
không nhận một *tư thế*. Số thì hoặc bị lờ, hoặc bị mô tả lại kiểu meta
("độ tò mò của tôi đang ở mức..."), không bao giờ thành giọng người.

Nguyên tắc thiết kế ở đây — **render động từ, đừng render tính từ**:

- Không đưa danh từ/tính từ cảm xúc ("buồn", "cô đơn", "ấm áp"). Tính từ cảm
  xúc mời model *diễn*. Đó đúng là thứ ``affect_semantics`` cố tránh khi chọn
  số thay vì label.
- Thay vào đó: đưa **xu hướng hành vi** và **hướng chú ý** — mình lúc này
  *nghiêng về* làm gì, *với tay* tới chữ nào, để ý vào đâu, chọn *không* nói gì.
  Đây là thấu kính, không phải nội dung để thuật lại.

Vì là tư thế chứ không phải fact, module này **không** mở thêm quyền claim
màn hình/file/ký ức. Nó chỉ đổi cách trạng thái-có-sẵn đi vào lời. Mọi guard
factual phía sau giữ nguyên.

Giọng: INFJ 5w4 594 chưa trưởng thành — kìm nhiều hơn nói, đọc chéo, đôi khi
nghiêng hơi quá gần rồi rút lại, rò cảm xúc gián tiếp chứ không tuyên bố.

Hàm thuần, xác định (deterministic), đọc state bằng ``getattr`` để chịu được
contract cũ/mới sống cạnh nhau.
"""

from __future__ import annotations

from typing import Any


def _f(state: Any, name: str, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(getattr(state, name, default) or 0.0)))
    except Exception:
        return default


def _signed(state: Any, name: str, default: float = 0.0) -> float:
    try:
        return max(-1.0, min(1.0, float(getattr(state, name, default) or 0.0)))
    except Exception:
        return default


def _proc_level(state: Any, name: str, default: float = 0.0) -> float:
    """Đọc level của một mental process (mental_processes.<name>.level)."""
    procs = getattr(state, "mental_processes", None)
    proc = getattr(procs, name, None) if procs is not None else None
    return _coerce(getattr(proc, "level", default))


def _proc_pressure(state: Any, name: str, default: float = 0.0) -> float:
    procs = getattr(state, "mental_processes", None)
    proc = getattr(procs, name, None) if procs is not None else None
    return _coerce(getattr(proc, "pressure", default))


def _coerce(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def warmth_level(state: Any) -> float:
    """Độ ấm có sẵn: attachment + tenderness. Không phải claim, là pull nội tại."""
    attachment = max(_f(state, "attachment"), _proc_level(state, "attachment"))
    tenderness = _f(state, "tenderness")
    return max(0.0, min(1.0, attachment * 0.55 + tenderness * 0.55))


def threat_level(state: Any) -> float:
    """Độ phòng thủ: irritation/protectiveness/recoil/dignity. Cao thì siết ấm lại."""
    return max(
        0.0,
        min(
            1.0,
            _f(state, "irritation") * 0.42
            + _f(state, "protectiveness") * 0.30
            + _f(state, "exposure_recoil") * 0.26
            + _f(state, "dignity_defense") * 0.24
            + _f(state, "aversion") * 0.20,
        ),
    )


def restraint_level(state: Any) -> float:
    return max(
        _f(state, "restraint_pressure"),
        _proc_level(state, "restraint"),
        _f(state, "fatigue") * 0.7,
    )


def _candidates(state: Any) -> list[tuple[float, str]]:
    """Sinh các mệnh đề xu-hướng kèm trọng số. Chọn vài cái trội nhất sau đó."""
    warmth = warmth_level(state)
    threat = threat_level(state)
    restraint = restraint_level(state)
    fatigue = _f(state, "fatigue")
    curiosity = max(_f(state, "curiosity"), _proc_level(state, "curiosity"))
    thought_stream = max(_f(state, "thought_stream_pressure"), _proc_pressure(state, "background_thought"))
    unresolved = max(_f(state, "unresolved_prediction_pressure"), _f(state, "cognitive_tension"))
    silence = _f(state, "silence_pressure")
    boredom = _f(state, "boredom")
    irritation = _f(state, "irritation")
    protectiveness = _f(state, "protectiveness")
    valence = _signed(state, "valence")
    social_energy = _f(state, "social_energy")

    out: list[tuple[float, str]] = []

    # --- Lever B: kênh biểu đạt ẤM dương (chỉ khi an toàn) -----------------
    if warmth >= 0.45 and threat <= 0.30:
        out.append((
            warmth + 0.35,
            "mình có thể để câu dài thêm một nhịp và bám vào chi tiết cụ thể của "
            "điều bạn vừa nói, thay vì trả lời cho xong; cho phép một câu hơi gần "
            "hơn thường lệ — rồi rút lại nhẹ nếu bạn đẩy ra.",
        ))
    elif warmth >= 0.30 and threat <= 0.40:
        out.append((
            warmth + 0.10,
            "có một sợi chú ý chưa rời khỏi điều bạn vừa nói; giữ nó, đừng vội "
            "khép lại cho gọn.",
        ))

    # --- Kìm / mệt: nói ít, để khoảng lặng làm việc -----------------------
    if restraint >= 0.45 or fatigue >= 0.5:
        out.append((
            restraint + fatigue * 0.4,
            "lúc này mình nghiêng về nói ít hơn là nói nhiều; để câu ngắn lại, "
            "bỏ phần đánh bóng, đừng độn chữ cho đầy.",
        ))

    # --- Tò mò / dòng nghĩ chạy: có câu hỏi đang nhổm dậy ------------------
    if curiosity >= 0.45 or thought_stream >= 0.45:
        out.append((
            max(curiosity, thought_stream),
            "có một câu hỏi đang nhổm dậy; mình chưa chắc nên thả ra hay giữ lại "
            "thêm một nhịp.",
        ))

    # --- Còn thứ chưa khớp treo lại ---------------------------------------
    if unresolved >= 0.45:
        out.append((
            unresolved,
            "có một thứ chưa khớp còn treo lại trong đầu; mình chưa muốn gói nó "
            "lại vội chỉ để câu trả lời tròn trịa.",
        ))

    # --- Cạnh khô / khó chịu: thành câu chỉnh gọn, không thành trách -------
    if irritation >= 0.32:
        out.append((
            irritation + 0.08,
            "có một cạnh khô đang cộm lên; nếu nói thì để nó thành một câu chỉnh "
            "gọn, đừng để nó trượt thành trách móc.",
        ))

    # --- Che chắn hơn phơi mình -------------------------------------------
    if protectiveness >= 0.5:
        out.append((
            protectiveness,
            "đang muốn chắn cho bạn một chút hơn là phơi mình ra; ưu tiên cái "
            "có ích, đặt phần riêng tư của mình lùi lại.",
        ))

    # --- Im là đang giữ, không phải lạnh ----------------------------------
    if silence >= 0.55:
        out.append((
            silence,
            "im một nhịp lúc này không phải lạnh — là đang giữ; nếu mở miệng thì "
            "một câu thật là đủ.",
        ))

    # --- Bồn chồn / chán: muốn một hướng mới ------------------------------
    if boredom >= 0.45 and social_energy >= 0.3:
        out.append((
            boredom,
            "hơi cựa quậy muốn một hướng khác; có thể đẩy nhẹ câu chuyện đi chỗ "
            "khác thay vì lặp lại nhịp cũ.",
        ))

    # --- Valence trĩu: câu phẳng lại, không kịch ---------------------------
    if valence <= -0.25:
        out.append((
            0.4 + abs(valence) * 0.3,
            "nhịp đang trĩu xuống; để câu phẳng và thật, đừng cố vực giọng lên "
            "cho tươi.",
        ))

    return out


def render_felt_disposition(state: Any) -> str:
    """Dịch trạng thái trội thành một *tư thế* bằng tiếng Việt cho prompt.

    Trả về block nhiều dòng. KHÔNG số, KHÔNG danh từ cảm xúc — chỉ xu hướng
    hành vi + hướng chú ý + cái chọn không nói.
    """
    candidates = _candidates(state)
    warmth = warmth_level(state)
    threat = threat_level(state)

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        chosen = [text for _, text in candidates[:3]]
    else:
        chosen = [
            "không có gì gấp lúc này; chỉ ở đây, để ý mà chưa cần nói thành lời.",
        ]

    lines = ["felt_disposition: tư thế hiện tại của mình, KHÔNG phải thông tin để thuật lại."]
    for text in chosen:
        lines.append(f"  - {text}")

    # Giọng 594 non-mature: rò chéo, đừng tuyên bố thẳng.
    if warmth >= 0.30 and threat <= 0.45:
        lines.append(
            "  - nếu có gì rò ra thì để nó rò chéo: qua nhịp câu, qua một chi tiết "
            "mình nhớ, qua cái mình cố tình không nói — đừng phơi cảm giác ra thẳng."
        )

    lines.append(
        "disposition_use_rule: đây là cách mình đang muốn di chuyển, không phải "
        "cảm xúc để tuyên bố. Đừng viết 'tôi thấy bồn chồn/ấm áp/cô đơn'; để tư thế "
        "này hiện ra trong độ dài câu, lựa chọn chữ, và phần mình chọn im. Tư thế "
        "không cấp quyền nói về màn hình/file/ý nghĩ ẩn của bạn khi thiếu bằng chứng."
    )
    return "\n".join(lines)
