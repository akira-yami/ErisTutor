# -*- coding: utf-8 -*-
"""P2-C: bảng tiến triển HTML tĩnh, offline; không mở server hoặc dùng JS.

Nguồn dữ liệu duy nhất là learning_insights() của LearningService. HTML chỉ
được sinh theo yêu cầu, mọi chuỗi từ hồ sơ/môn học đều được escape.
"""
from __future__ import annotations

from html import escape
from datetime import datetime, timezone
from typing import Mapping, Any


_LABELS = {
    "possible_repeated_gap": ("Lỗi có dấu hiệu lặp", "Xem lại rồi kiểm tra độc lập"),
    "review_due": ("Đến hạn ôn", "Thử nhớ lại không gợi ý"),
    "needs_repair": ("Cần giải thích lại", "Sửa chỗ sai rồi thử bài mới"),
    "needs_independent_probe": ("Cần thử tự làm", "Một bài mới không gợi ý"),
    "unassessed": ("Chưa đánh giá", "Bài khảo sát ngắn"),
    "evidence_available": ("Đã có bằng chứng", "Tiếp tục học"),
}


def render_dashboard(snapshot: Mapping[str, Any]) -> str:
    """Tạo trang HTML độc lập; không dựng điểm năng lực giả từ tỷ lệ thành công."""
    path = snapshot.get("path") or {}
    title = escape(str(path.get("title") or "Chưa chọn môn"))
    skills = list(snapshot.get("knowledge") or [])[:64]
    rows = []
    counts = {"review": 0, "probe": 0}
    for skill in skills:
        label, action = _LABELS.get(str(skill.get("signals", {}).get("status")), ("Cần quan sát", "Kiểm tra thêm"))
        signals = skill.get("signals") or {}
        status = str(signals.get("status") or "")
        if status == "review_due":
            counts["review"] += 1
        if status == "needs_independent_probe":
            counts["probe"] += 1
        evidence = (f"{int(signals.get('independent_correct') or 0)} đúng · "
                    f"{int(signals.get('independent_incorrect') or 0)} sai · "
                    f"{int(signals.get('assisted_families') or 0)} có hỗ trợ")
        patterns = ", ".join(escape(str(e.get("error_code") or ""))
                              for e in (signals.get("repeated_error_hypotheses") or [])[:4])
        rows.append(
            '<article class="skill"><div><h3>' + escape(str(skill.get("title") or "(Chưa đặt tên)")) + '</h3>'
            '<small>' + escape(evidence) + '</small>'
            + ('<p class="notes">Mã lỗi quan sát: ' + patterns + '</p>' if patterns else '')
            + '</div><div class="activity"><strong>' + escape(label) + '</strong><span>' + escape(action) + '</span></div></article>'
        )
    if not rows:
        rows.append('<p class="empty">Chưa có kiến thức được lưu. Hãy bắt đầu một lộ trình học trước.</p>')
    evaluations = snapshot.get("teaching_evaluation") or {}
    methods = []
    for name, stats in list((evaluations.get("policies") or {}).items())[:12]:
        method = escape(str(name))
        count = int(stats.get("independent_families") or 0)
        summary = "Cần thêm bằng chứng" if not stats.get("enough_samples") else "Đủ mẫu mô tả, chưa chứng minh nhân quả"
        methods.append('<article class="skill"><div><h3>' + method + '</h3><small>'
                       + str(count) + ' họ bài làm độc lập</small></div><div class="activity"><span>'
                       + escape(summary) + '</span></div></article>')
    if not methods:
        methods.append('<p class="empty">Chưa có đủ lần thực hành để so sánh phương pháp dạy.</p>')
    mode = escape(str((snapshot.get("retention") or {}).get("active_mode", "shadow")))
    return '''<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">
<title>ERIS · Learning Insights</title><style>
:root{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#372f43;background:#f5effb;line-height:1.55}
*{box-sizing:border-box}body{margin:0;padding:30px 18px;background:radial-gradient(ellipse at 80% 0%,#f9ddeb 0,transparent 42%),#f5effb}
main{width:min(100%,1050px);margin:auto;background:#fffcff;border:1px solid #e8d8ec;border-radius:25px;box-shadow:0 20px 65px #67547a20;overflow:hidden}
header{padding:32px 36px;background:linear-gradient(110deg,#f6e6f4,#f3eefe)}.kicker{font-size:12px;font-weight:750;letter-spacing:.15em;color:#93629b;text-transform:uppercase}
h1{font-size:clamp(25px,4vw,40px);margin:6px 0;color:#493957}header p{max-width:70ch;margin:0;color:#65566e}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;padding:24px 36px}.metric{border:1px solid #ebddf0;background:#fff9ff;border-radius:16px;padding:18px}
.metric b{font-size:27px;display:block;color:#80578b}.metric small{display:block;color:#65566e}
section{padding:4px 36px 32px}h2{font-size:20px;margin:14px 0 18px}.skill{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 2px;border-top:1px solid #ece2f1}
h3{margin:0 0 4px;font-size:16px}.skill small{color:#726679}.notes{color:#86638b;font-size:12px}.activity{text-align:right;min-width:190px}.activity strong{color:#76518a;display:block;font-size:13px}.activity span{color:#6e6173;font-size:12px;display:block}
footer{background:#f9f3fb;padding:20px 36px;color:#716477;font-size:13px}.empty{color:#76667f}
@media(max-width:620px){body{padding:8px}main{border-radius:15px}header,section,footer{padding:24px 18px}.metrics{padding:20px 18px;grid-template-columns:1fr}.skill{align-items:flex-start;flex-direction:column;gap:9px}.activity{text-align:left}}
</style></head><body><main><header><div class="kicker">ERIS / Learning Insights</div>
<h1>''' + title + '''</h1><p>Những tín hiệu Eris quan sát từ bài làm thật. Không phải xếp hạng trí thông minh hay kết luận cố định về năng lực.</p></header>
<div class="metrics"><div class="metric"><b>''' + str(len(skills)) + '''</b><small>Kiến thức trong bản xem</small></div>
<div class="metric"><b>''' + str(counts["review"]) + '''</b><small>Kiến thức đến hạn ôn</small></div>
<div class="metric"><b>''' + str(counts["probe"]) + '''</b><small>Cần bài kiểm tra độc lập</small></div></div>
<section><h2>Bản đồ bằng chứng</h2>''' + ''.join(rows) + '''</section>
<section><h2>Quan sát cách dạy</h2><p>Chỉ là thống kê; không đủ để kết luận phương pháp nào tốt hơn.</p>''' + ''.join(methods) + '''</section>
<footer>Lịch ôn: ''' + mode + ''' · Bản báo cáo tại thời điểm xuất, không tự cập nhật. Không dùng dữ liệu này để chấm điểm con người.</footer>
</main></body></html>'''


__all__ = ["render_dashboard"]
