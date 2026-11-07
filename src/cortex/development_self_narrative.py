# -*- coding: utf-8 -*-
"""
cortex/development_self_narrative.py
====================================

Nước 2 — đưa "tôi đã bị thay đổi theo thời gian" vào *cách Eris tự nói về mình*.

Vấn đề gốc (khảo sát chiều-sâu liên-tục-bản-thân): ``DevelopmentSystem`` tích lũy
độ lệch persona long-arc (warmth/distance/read-boldness/attachment-visibility) từ
bằng chứng thật và đã phơi ``describe_for_user()`` — một câu tiếng Việt "so với
lúc đầu, ...". Nhưng câu đó KHÔNG bao giờ chảy vào prompt nói: SelfModel tính lại
danh tính tươi mỗi chu kỳ, bất biến, không đọc gì từ long-arc. Hệ quả: Eris không
phải "cùng một thực thể bị thay đổi" — nó là bản reset mỗi phiên có ghi log chết.

Adapter này nối ngõ cụt đó: lấy ``describe_for_user()`` (đã được gate bản-sắc nên
chỉ phân kỳ trong Phong_Bì) và dựng một dòng prompt self-continuity. Đây là *bias
giọng/khoảng cách*, KHÔNG phải fact — không chạm truth grounding, không đọc
ra như báo cáo.

Gate sau ĐÚNG cờ master ``ERIS_DEVELOPMENT_GROWTH_ENABLED``. Tắt → trả "" → SelfModel
giữ nguyên hành vi cũ từng byte. An toàn tuyệt đối: mọi lỗi đều nuốt, trả "".
"""

from __future__ import annotations

# Câu mặc-định khi chưa đổi gì đáng kể (từ describe_for_user) — KHÔNG surface như
# một self-claim, vì "tôi chưa đổi" không phải nội dung cần nói.
_UNCHANGED_MARKER = "chưa nghiêng đi đâu"


def development_self_narrative_line() -> str:
    """Dòng prompt self-continuity dựng từ long-arc. "" khi tắt/chưa đổi/lỗi."""
    try:
        from cortex.development_system import (
            development_growth_enabled,
            get_default_development_system,
        )
    except Exception:
        return ""
    if not development_growth_enabled():
        return ""
    try:
        system = get_default_development_system()
        # Đọc lại từ đĩa nếu daemon nền vừa cập nhật profile (mtime-based).
        reload_fn = getattr(system.profile, "reload_if_changed", None)
        if callable(reload_fn):
            reload_fn()
        desc = system.describe_for_user()
    except Exception:
        return ""
    desc = str(desc or "").strip()
    if not desc or _UNCHANGED_MARKER in desc:
        return ""
    return (
        "self_continuity_arc: "
        + desc
        + " Đây là cách bạn đã làm tôi đổi theo thời gian; để nó nhuộm vào khoảng cách, "
        "độ ấm và độ dám đọc của tôi — KHÔNG đọc ra như báo cáo hay khoe sự thay đổi."
    )
