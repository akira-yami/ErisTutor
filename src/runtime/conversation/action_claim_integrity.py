# -*- coding: utf-8 -*-
"""Action Integrity V2: đối chiếu *hành động đã làm* với receipt của executor.

Không dùng LLM để quyết định PASS/FAIL; không tin ``claim_manifest`` hoặc câu
nói của Root làm chứng cứ. Đây là rào cho các mẫu lời xác nhận hành động đã
khai báo, KHÔNG phải bộ kiểm chứng mọi mệnh đề ngôn ngữ tự nhiên.

Quy tắc an toàn:
- Receipt đúng event/session/run/generation, đúng executor/action và SUCCESS
  *đã quan sát/hoàn tất*, không chỉ 'dispatched'.
- Mỗi loại hành động có ID riêng: gửi message != gửi email != gửi file;
  apply patch != đóng gói != lưu file; run test != chạy command.
- Nhắc đích đến cụ thể thì đích đó phải có trong OUTPUT của executor;
  argument do model đề nghị không tự chứng minh hậu điều kiện.
- Nhắc số lượng N thì cần N invocation riêng biệt cùng loại/cùng đích.
- Kiểm tra toàn bộ surface (hiển thị, memory, TTS), không dùng manifest.

Nếu sau này thêm capability: bổ sung contract + receipt shape + regression
trước khi cấp quyền xác nhận bằng lời. Regex không thể nhận diện hết paraphrase;
không quảng cáo đây là bảo đảm tổng quát chống bịa.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping
import unicodedata


# Contract ID là danh sách tường minh; tuyệt đối không tự ánh xạ mọi action có
# chung từ 'send' hoặc chung side effect 'write' vào một loại chứng cứ.
_ACTION_IDS: dict[str, frozenset[str]] = {
    "send_email": frozenset({"send_email", "gmail_send", "email_send"}),
    "send_message": frozenset({"send_message"}),
    "send_file": frozenset({"send_file", "share_file", "upload_file"}),
    "send_generic": frozenset(
        {"send_email", "gmail_send", "email_send", "send_message", "send_file", "share_file", "upload_file"}
    ),
    "save_file": frozenset({"save_file", "write_file"}),
    "apply_patch": frozenset({"project_apply_patch"}),
    "package_patch": frozenset({"project_package_patch"}),
    "delete_file": frozenset({"delete_file", "remove_file"}),
    "delete_email": frozenset({"delete_email", "gmail_delete"}),
    "book": frozenset({"create_calendar_event", "calendar_create_event", "book_reservation"}),
    "pay": frozenset({"submit_payment", "make_payment", "checkout_pay"}),
    "run_tests": frozenset({"project_run_tests"}),
    "run_command": frozenset({"project_run_command"}),
    "inspect": frozenset(
        {
            "inspect_runtime_log",
            "read_current_error",
            "inspect_selected_file",
            "project_read_file",
            "project_search_text",
            "project_find_symbol",
            "project_git_diff",
            "project_run_tests",
            "project_run_command",
            "get_project_status",
        }
    ),
    "copy": frozenset({"copy_text"}),
    "open": frozenset({"open_url"}),
}
_EFFECT: dict[str, str] = {
    **dict.fromkeys(("send_email", "send_message", "send_file", "send_generic"), "send"),
    **dict.fromkeys(("save_file", "apply_patch", "package_patch"), "save"),
    **dict.fromkeys(("delete_file", "delete_email"), "delete"),
    "book": "book",
    "pay": "pay",
    "run_tests": "run",
    "run_command": "run",
    "inspect": "inspect",
    "copy": "copy",
    "open": "open",
}

_VI_VERBS = {
    "send": r"gửi",
    "save": r"lưu|ghi|áp|đóng\s+gói",
    "delete": r"xóa|xoá|gỡ\s+bỏ",
    "book": r"đặt|đăng\s+ký",
    "pay": r"thanh\s+toán|trả\s+tiền",
    "run": r"chạy",
    "inspect": r"kiểm\s+tra|đọc",
    "copy": r"sao\s+chép|copy",
    "open": r"mở",
}
# Giữ ngữ cảnh một câu thay vì một group 'send' chung cho toàn bài phát biểu.
_VI_VERB_PATTERN = (
    r"gửi|lưu|ghi|áp|đóng\s+gói|xóa|xoá|gỡ\s+bỏ|đặt|đăng\s+ký|"
    r"thanh\s+toán|trả\s+tiền|chạy|kiểm\s+tra|đọc|sao\s+chép|copy|mở"
)
_VI = re.compile(
    rf"(?i)\b(?:tớ|tôi|mình|eris)\s+(?:đã|vừa|mới)\s+(?P<verb>{_VI_VERB_PATTERN})\b"
    rf"|(?:^|[.!?。！？]\s*|\n\s*)(?:đã|vừa)\s+(?P<elliptic>{_VI_VERB_PATTERN})\b"
    rf"|\b(?:tớ|tôi|mình|eris)\s+(?P<bare>{_VI_VERB_PATTERN})\b"
)
_EN = re.compile(
    r"(?i)\bI(?:\s+(?:have\s+)?|['’]ve\s+)(?:(?:already|just)\s+)?"
    r"(?P<verb>sent|emailed|messaged|saved|wrote|patched|deleted|removed|"
    r"booked|registered|scheduled|paid|purchased|ran|executed|checked|"
    r"inspected|read|copied|opened)\b"
)
_JA = re.compile(
    r"送った(?!ら)|送信しました|送りました|送信済み|保存した|保存しました|"
    r"書き込みました|削除した(?!ら)|削除しました|消しました|予約した|予約しました|"
    r"登録しました|支払った|支払いました|決済しました|実行した|実行しました|"
    r"テストを走らせた|確認した|確認しました|調べました|コピーした|コピーしました|"
    r"開きました|開いた"
)
# Trong một câu, ngắt trước câu kế tiếp; không tách dấu chấm ở email/path/url.
_END = re.compile(r"[!?。！？\n]|\.(?=\s|$)")
_DIGITS = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_COUNT = re.compile(
    r"(?i)\b(\d{1,4}|một|hai|ba|bốn|năm|one|two|three|four|five)\s+"
    r"(?:email|e-mails?|mails?|tin\s+nhắn|messages?|files?|tệp|tài\s+liệu|"
    r"đường\s+dẫn|lần|tests?|kiểm\s+thử|người\s+nhận)\b"
)
_EMAIL = re.compile(r"(?i)[\w.+-]+@[\w.-]+\.[a-z]{2,}")
_FILE_OBJECT = re.compile(r"(?i)\b(?:file|tệp|tài\s+liệu)\s+[`\"\']?([\w./\\-]+\.[a-z0-9_-]+)")
_VI_TARGET = re.compile(r"(?i)\b(?:cho|tới|đến)\s+([^,;.!?。！？\n]+)")
_EN_TARGET = re.compile(r"(?i)\bto\s+([^,;!?。！？\n]+)")
# Những cụm tiếp nối/đệm không phải tên đích; không tự suy ra nơi nhận.
_TARGET_STOP = re.compile(
    r"(?i)\s+(?:rồi|xong|thành\s+công|đấy|nha|nhé|nhỉ|already|successfully|just|today|hôm\s+nay|but|nhưng|và)\b.*$"
)
_OK = frozenset({"completed", "success", "succeeded", "ok", "observed"})


@dataclass(frozen=True, slots=True)
class ActionClaim:
    """Một mệnh đề đã-làm; surface là một bản thể hiện của cùng thông điệp."""

    kind: str
    target: str = ""
    quantity: int = 1


def _norm(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().casefold().split()).rstrip(".,!。！")


def _file_target(value: str) -> str:
    # File trên Linux có phân biệt hoa/thường; không casefold đường dẫn.
    return "path:" + unicodedata.normalize("NFKC", str(value)).strip().rstrip(".,!。！")


def _email_target(value: str) -> str:
    # Domain email không phân biệt hoa/thường; local part có thể phân biệt.
    local, domain = value.split("@", 1)
    return unicodedata.normalize("NFKC", local) + "@" + _norm(domain)


def _url_target(value: str) -> str:
    # Hostname không phân biệt hoa/thường, nhưng path/query URL có thể có.
    from urllib.parse import urlsplit, urlunsplit

    raw = unicodedata.normalize("NFKC", value).strip().rstrip(".,!。！")
    parts = urlsplit(raw)
    return "url:" + urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))


def _verb_kind(verb: str, clause: str, *, lang: str) -> str | None:
    v = _norm(verb)
    c = _norm(clause)
    if lang == "ja":
        if any(x in verb for x in ("送", "送り")):
            effect = "send"
        elif any(x in verb for x in ("保存", "書き込み")):
            effect = "save"
        elif any(x in verb for x in ("削除", "消し")):
            effect = "delete"
        elif any(x in verb for x in ("予約", "登録")):
            effect = "book"
        elif any(x in verb for x in ("支払", "決済")):
            effect = "pay"
        elif any(x in verb for x in ("実行", "走らせ")):
            effect = "run"
        elif any(x in verb for x in ("確認", "調べ")):
            effect = "inspect"
        elif "コピー" in verb:
            effect = "copy"
        else:
            effect = "open"
    elif lang == "en":
        effect = next(
            (
                e
                for e, verbs in {
                    "send": ("sent", "emailed", "messaged"),
                    "save": ("saved", "wrote", "patched"),
                    "delete": ("deleted", "removed"),
                    "book": ("booked", "registered", "scheduled"),
                    "pay": ("paid", "purchased"),
                    "run": ("ran", "executed"),
                    "inspect": ("checked", "inspected", "read"),
                    "copy": ("copied",),
                    "open": ("opened",),
                }.items()
                if v in verbs
            ),
            None,
        )
    else:
        effect = next((e for e, pattern in _VI_VERBS.items() if re.fullmatch(pattern, v, re.I)), None)
    if effect == "send":
        if re.search(r"email|e-mail|\bmail\b|メール", c):
            return "send_email"
        if re.search(r"tin\s+nhắn|message|メッセージ", c):
            return "send_message"
        if re.search(r"file|tệp|tài\s+liệu|ファイル", c):
            return "send_file"
        if v == "emailed":
            return "send_email"
        if v == "messaged":
            return "send_message"
        return "send_generic"
    if effect == "save":
        if re.search(r"patch|bản\s+vá|パッチ|\báp\b|\bpatched\b", c):
            return "package_patch" if re.search(r"đóng\s+gói|packag", c) else "apply_patch"
        if re.search(r"đóng\s+gói|package|zip", c):
            return "package_patch"
        return "save_file"
    if effect == "delete":
        return "delete_email" if re.search(r"email|e-mail|\bmail\b|メール", c) else "delete_file"
    if effect == "run":
        return "run_tests" if re.search(r"tests?|kiểm\s+thử|テスト", c) else "run_command"
    return effect


def _quantity(clause: str) -> int:
    m = _COUNT.search(clause)
    if not m:
        return 1
    raw = _norm(m.group(1))
    return int(raw) if raw.isdigit() else _DIGITS[raw]


def _target(clause: str, kind: str, lang: str) -> str:
    # Email đích dạng address nhận diện chính xác ngay cả trong lời Nhật.
    # Phải chứng minh qua output, không so substring tên trong câu.
    if kind == "send_email":
        match = _EMAIL.search(clause)
        if match:
            return _email_target(match.group())
    if kind in ("send_file", "save_file", "delete_file", "apply_patch", "package_patch"):
        file_match = _FILE_OBJECT.search(clause)
        if file_match:
            return _file_target(file_match.group(1))
    match = _VI_TARGET if lang == "vi" else _EN_TARGET if lang == "en" else None
    m = match.search(clause) if match else None
    if m and kind.startswith("send_"):
        candidate = m.group(1).strip(" \t:;,.")
        # Nếu nhiều đích, không thể coi một receipt là đã gửi cho tất cả.
        if re.search(r"\s+(?:và|and|or|hoặc)\s+|\s*[/、]\s*", candidate, re.I):
            return "__multiple_targets_unresolved__"
        candidate = _TARGET_STOP.sub("", candidate).strip(" \t:;,.")
        # Cú pháp 'cho An, Bình' vẫn là nhiều đích (đừng bỏ sót Bình).
        after = clause[m.end() :]
        if re.match(r",\s*(?!và\s+tôi\b|tôi\b|mình\b|I\b|then\b|rồi\b)[\wÀ-ỹ@]", after, re.I):
            return "__multiple_targets_unresolved__"
        return _norm(candidate)
    if lang == "en" and kind in ("send_email", "send_message"):
        # 'I emailed An' không có từ 'to', nhưng An vẫn là người nhận.
        direct = re.search(
            r"(?i)\b(?:emailed|messaged)\s+"
            r"(?!an?\s+(?:email|message)\b)([\wÀ-ỹ@.+-]+)",
            clause,
        )
        if direct:
            return _norm(direct.group(1))
    # Đích URL cụ thể trong lời xác nhận đã mở.
    if kind == "open":
        u = re.search(r"https?://[^\s,;!?。！？]+", clause, re.I)
        if u:
            return _url_target(u.group())
    return ""


def _is_action_object(kind: str, verb: str, clause: str) -> bool:
    """Loại idiom/giao tiếp thuần túy trước khi đụng receipt gate."""
    rest = _norm(clause)
    if kind == "book":
        return bool(
            re.search(
                r"\b(?:lịch|bàn|chỗ|vé|lớp|suất|cuộc hẹn|booking|table|ticket|appointment|reservation)\b|予約|登録",
                rest,
            )
        )
    if kind == "open":
        return bool(
            re.search(r"\b(?:url|link|website|webpage|browser|trang web|đường dẫn)\b|https?://|ブラウザ|リンク", rest)
        )
    if kind == "save_file":
        return bool(
            re.search(
                r"\b(?:file|tệp|tài liệu|thay đổi|dữ liệu|document|changes?|settings?|data)\b|保存|書き込み", rest
            )
        )
    if kind == "inspect":
        return bool(
            re.search(
                r"\b(?:file|tệp|log|lỗi|mã|source|project|repo|kết quả test|logs?|files?|code|repository|error|tests?)\b|確認|調べ",
                rest,
            )
        )
    if kind == "send_generic":
        if "送った" in rest or "送りました" in rest or "送信済み" in rest:
            return True
        return bool(
            re.search(r"\b(?:gửi|sent|送った|送りました|送信済み)\s*(?:rồi|xong|it|that)?\s*[.!?。！？]?$", rest)
        )
    if kind == "delete_file":
        return bool(
            re.search(r"\b(?:file|tệp|thư mục|dữ liệu|bản ghi|tài liệu|folder|directory|files?|data)\b|削除|消し", rest)
        )
    return True


def _claims(surface: str) -> tuple[ActionClaim, ...]:
    text = str(surface or "")
    found: list[ActionClaim] = []
    for regex, lang in ((_VI, "vi"), (_EN, "en"), (_JA, "ja")):
        for m in regex.finditer(text):
            verb = (
                (m.group("verb") or m.group("elliptic") or m.group("bare"))
                if lang == "vi"
                else (m.group("verb") if lang == "en" else m.group())
            )
            # Xem phần còn lại của *câu chứa claim*, không nhìn nội dung câu kế.
            end = _END.search(text, m.end())
            # Phần object tiếng Nhật thường nằm TRƯỚC động từ (メールを送信...).
            before = tuple(_END.finditer(text, 0, m.start()))
            start = before[-1].end() if before else 0
            clause = text[start : end.start() if end else len(text)][:500]
            # 'Tớ gửi ... rồi/xong' là một claim dù không có chữ 'đã'.
            if (
                lang == "vi"
                and m.group("bare")
                and not re.search(
                    r"(?i)\b(?:rồi|xong|thành\s+công)\b", text[m.end() : end.start() if end else len(text)]
                )
            ):
                continue
            # Không biến một lời phủ định thành tuyên bố hoàn thành.
            if re.search(r"(?i)^\s*(?:not\b|không\b|chưa\b)", text[m.end() : end.start() if end else len(text)]):
                continue
            kind = _verb_kind(verb, clause, lang=lang)
            if kind is not None and _is_action_object(kind, verb, clause):
                found.append(ActionClaim(kind, _target(clause, kind, lang), _quantity(clause)))
    return tuple(found)


def detected_completed_actions(surfaces: Iterable[str]) -> frozenset[str]:
    """API V1 compatibility: trả về nhóm semantic, không phải giấy phép phát."""
    return frozenset(_EFFECT[c.kind] for surface in surfaces for c in _claims(surface))


def _confirmed_targets(result: Mapping[str, Any]) -> frozenset[str]:
    """Chỉ xem trường kết quả executor, KHÔNG lấy arguments do Root/model gửi."""
    data = result.get("data")
    if not isinstance(data, Mapping):
        data = {}
    # Schema đầu ra cho các capability tương lai: 'confirmed_targets' hoặc các
    # trường result được executor chứng thực. Không lấy chuỗi message suy đoán.
    fields = ("confirmed_targets", "recipients", "recipient", "to", "files", "file", "path", "target", "url")
    targets: set[str] = set()
    for field in fields:
        raw = data.get(field)
        values = (raw,) if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else ()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            if _EMAIL.fullmatch(value.strip()):
                targets.add(_email_target(value.strip()))
            else:
                targets.add(_norm(value))
            if field in ("files", "file", "path", "target", "confirmed_targets"):
                targets.add(_file_target(value))
            if field in ("url", "target", "confirmed_targets") and re.match(r"https?://", value, re.I):
                targets.add(_url_target(value))
    meta = result.get("public_meta")
    # action_input là output do executor tự trả, chỉ dùng nếu OBSERVED và
    # hành động có một đối tượng chuỗi đơn nghĩa (open_url).
    if (
        isinstance(meta, Mapping)
        and meta.get("observed") is True
        and str(result.get("action_id", "")).lower() == "open_url"
    ):
        action_input = meta.get("action_input")
        if isinstance(action_input, str) and _norm(action_input):
            targets.add(
                _url_target(action_input) if re.match(r"https?://", action_input, re.I) else _norm(action_input)
            )
    return frozenset(targets)


def _proves(receipt: Any, claim: ActionClaim, *, event_id: str, session_id: str, run_id: str, generation: int) -> bool:
    if (
        not event_id
        or not session_id
        or not run_id
        or not getattr(receipt, "invocation_id", "")
        or str(getattr(receipt, "event_id", "")) != event_id
        or str(getattr(receipt, "session_id", "")) != session_id
        or str(getattr(receipt, "run_id", "")) != run_id
        or getattr(receipt, "generation", None) != generation
        or str(getattr(receipt, "name", "")).lower() != "action"
    ):
        return False
    result = getattr(receipt, "result", None)
    if not isinstance(result, Mapping) or result.get("success") is not True:
        return False
    status = str(getattr(receipt, "status", "")).lower()
    if status not in _OK or str(result.get("status", "")).lower() != status:
        return False
    # Nếu result có proof của executor: DISPATCHED != OBSERVED; không nhận
    # câu đã-hoàn-tất dù việc gửi yêu cầu thành công.
    meta = result.get("public_meta")
    if isinstance(meta, Mapping):
        if (
            meta.get("observed") is not True
            or _norm(meta.get("public_mode")) != "observed_success"
            or meta.get("success") is not True
        ):
            return False
    if status == "observed" and (not isinstance(meta, Mapping) or meta.get("observed") is not True):
        return False
    action_id = _norm(result.get("action_id"))
    if action_id not in _ACTION_IDS[claim.kind]:
        return False
    if isinstance(meta, Mapping) and _norm(meta.get("action_name")) != action_id:
        return False
    return not claim.target or claim.target in _confirmed_targets(result)


def _verified_units(receipt: Any, claim: ActionClaim) -> int:
    # Một lệnh pytest có thể chạy N test: N phải nằm trong output có cấu trúc,
    # không lấy từ mô tả tự do hoặc arguments. Các action khác luôn 1 receipt=1.
    if claim.kind == "run_tests":
        data = getattr(receipt, "result", {}).get("data")
        if isinstance(data, Mapping):
            for key in ("tests_run", "tests_total"):
                count = data.get(key)
                if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 1000000:
                    return count
    return 1


def enforce_completed_action_claims(
    *, surfaces: Iterable[str], receipts: Iterable[Any], event_id: str, session_id: str, run_id: str, generation: int
) -> None:
    """Fail-closed với các mẫu đã nhận diện; không tự rewrite lời Eris.

    Mỗi surface là cách thể hiện độc lập. Một receipt có thể chứng minh cùng
    một sự kiện trên display và TTS, nhưng không chứng minh '3 emails'.
    """
    entries = tuple(receipts)
    problems: set[str] = set()
    for surface in surfaces:
        # Cùng câu lặp từ hai pattern không được vô tình đếm gấp đôi.
        claims = set(_claims(str(surface or "")))
        for claim in claims:
            valid = {
                str(getattr(r, "invocation_id", "")): _verified_units(r, claim)
                for r in entries
                if _proves(r, claim, event_id=event_id, session_id=session_id, run_id=run_id, generation=generation)
            }
            if sum(valid.values()) < claim.quantity:
                problems.add(_EFFECT[claim.kind])
    if problems:
        # Không log nội dung câu, target, recipients hoặc dữ liệu cá nhân.
        raise RuntimeError("public_action_receipt_missing:" + ",".join(sorted(problems)))


__all__ = ["ActionClaim", "detected_completed_actions", "enforce_completed_action_claims"]
