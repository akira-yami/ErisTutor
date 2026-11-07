import os


ERIS_GUARD_MODE = os.environ.get("ERIS_GUARD_MODE", "off").strip().lower() or "off"

STRICT_GUARD_MODE = "strict"
SELF_CLAMP_GUARD_MODE = "self_clamp"
LOG_ONLY_GUARD_MODE = "log_only"
OFF_GUARD_MODE = "off"

VALID_GUARD_MODES = {
    STRICT_GUARD_MODE,
    SELF_CLAMP_GUARD_MODE,
    LOG_ONLY_GUARD_MODE,
    OFF_GUARD_MODE,
}


def guards_warn_only() -> bool:
    """Compatibility switch for legacy diagnostics.

    Production defaults to ``off``. Enabling this switch records old guard
    findings without granting them permission to rewrite public text.
    """
    raw = str(os.environ.get("ERIS_GUARDS_WARN_ONLY", "0") or "").strip().lower()
    return raw not in {"", "0", "false", "no", "off", "none"}


def get_guard_mode(name: str | None = None) -> str:
    """Trả chế độ guard hiện hành.

    Tham số `name` (tùy chọn) cho phép override theo từng rule trong tương lai;
    hiện dùng mode toàn cục. Nếu `guards_warn_only()` bật, mọi rule về log_only.
    """
    if guards_warn_only():
        return LOG_ONLY_GUARD_MODE
    if name:
        # Không còn tự nâng một rule cũ lên STRICT chỉ vì nó có tên. Chỉ bật lại
        # khi operator đặt env tường minh; mặc định mọi final-text guard đều OFF.
        per_rule = os.environ.get(f"ERIS_GUARD_MODE__{str(name).upper()}", "").strip().lower()
        if per_rule in VALID_GUARD_MODES:
            return per_rule
        return OFF_GUARD_MODE
    mode = os.environ.get("ERIS_GUARD_MODE", ERIS_GUARD_MODE).strip().lower()
    return mode if mode in VALID_GUARD_MODES else OFF_GUARD_MODE
