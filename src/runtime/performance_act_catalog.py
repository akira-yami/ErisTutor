# -*- coding: utf-8 -*-
"""performance_act_catalog.py — catalog các màn biểu diễn nhỏ của ERIS.

Trước đây ``stage_performance_planner`` chứa nhiều if/else hard-code.  Khi muốn
thêm "dỗi giả", "thở dài", "cà khịa", ta phải sửa logic nhận diện lẫn dữ liệu
biểu diễn trong cùng một chỗ.  Catalog này tách dữ liệu act ra riêng:

    trigger -> PerformanceActSpec -> stage render

Nhờ vậy mở rộng hành vi chỉ là thêm spec mới, ít đụng hot path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PerformanceActSpec:
    kind: str
    triggers: tuple[str, ...]
    audible_prefix: str
    fallback_line: str
    delivery_hint: str
    nonverbal_cue: str
    stage_motion: str
    prosody_note: str
    # Nếu true, planner cần thấy marker kiểu "đi/coi/nghe/thử" để tránh kích hoạt
    # nhầm khi user chỉ kể chuyện có chữ "cười" hoặc "giận".
    requires_request_marker: bool = True


PERFORMANCE_ACT_CATALOG: tuple[PerformanceActSpec, ...] = (
    PerformanceActSpec(
        kind="playful_laugh",
        triggers=("cuoi", "laugh"),
        audible_prefix="Hah... haha.",
        fallback_line="bạn định cho tôi làm trò hề gì vậy?",
        delivery_hint="dry_tease",
        nonverbal_cue="audible_laugh_soft",
        stage_motion="look_away",
        prosody_note="Start with a small real laugh, then turn it into dry teasing.",
    ),
    PerformanceActSpec(
        kind="fake_annoyed_deadpan",
        triggers=("noi gian", "gia vo noi gian", "angry", "mad"),
        audible_prefix="...",
        fallback_line="Ồ, ra lệnh cho tôi nổi giận theo lịch à. bạn đang huấn luyện rạp xiếc hay gì vậy?",
        delivery_hint="deadpan",
        nonverbal_cue="scoff_soft",
        stage_motion="blink_slow",
        prosody_note="Fake annoyance. Deadpan, clipped ending, more unimpressed than angry.",
    ),
    PerformanceActSpec(
        kind="fake_sulk",
        triggers=("doi", "hon doi", "sulk"),
        audible_prefix="Hmph.",
        fallback_line="Dỗi theo yêu cầu thì còn gì là dỗi nữa, bạn.",
        delivery_hint="dry_tease",
        nonverbal_cue="scoff_soft",
        stage_motion="look_away",
        prosody_note="A tiny pout in the voice, but keep it controlled.",
    ),
    PerformanceActSpec(
        kind="soft_sigh",
        triggers=("tho dai", "sigh"),
        audible_prefix="Haa...",
        fallback_line="Rồi. Một tiếng thở dài có trách nhiệm.",
        delivery_hint="low_quiet",
        nonverbal_cue="sigh_soft",
        stage_motion="look_down",
        prosody_note="Let the sigh be audible, then speak quietly.",
    ),
    PerformanceActSpec(
        kind="fake_cry_refusal",
        triggers=("khoc", "cry"),
        audible_prefix="...",
        fallback_line="Không. Khóc giả nghe ngốc lắm. Tôi giữ chút phẩm giá cuối cùng đã.",
        delivery_hint="deadpan",
        nonverbal_cue="look_down",
        stage_motion="look_down",
        prosody_note="Refuse gently; dry, quiet, not cruel.",
    ),
    PerformanceActSpec(
        kind="requested_tease",
        triggers=("cham choc", "ca khia", "mia mai", "tease", "roast"),
        audible_prefix="Hah.",
        fallback_line="bạn tự nguyện bước lên thớt đấy nhé.",
        delivery_hint="dry_tease",
        nonverbal_cue="audible_laugh_soft",
        stage_motion="small_smile",
        prosody_note="Short amused breath, then dry teasing.",
    ),
    PerformanceActSpec(
        kind="curious_hm",
        triggers=("hm", "hmm", "nghi ngo", "to mo"),
        audible_prefix="Mm?",
        fallback_line="Rồi, tôi nghe đây. bạn đang định thử tôi tiếp đúng không?",
        delivery_hint="curious_soft",
        nonverbal_cue="tilt_head",
        stage_motion="tilt_head",
        prosody_note="A small curious hum before speaking.",
    ),
)


def act_spec_by_kind(kind: str) -> PerformanceActSpec | None:
    wanted = str(kind or "").strip()
    for spec in PERFORMANCE_ACT_CATALOG:
        if spec.kind == wanted:
            return spec
    return None


__all__ = [
    "PERFORMANCE_ACT_CATALOG",
    "PerformanceActSpec",
    "act_spec_by_kind",
]
