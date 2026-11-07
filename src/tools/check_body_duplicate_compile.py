from __future__ import annotations

"""Kiểm tra nhanh các khai báo type C# trùng giữa Body chuẩn và các bản sao trong project.

Script này không biên dịch C#. Nó chỉ giúp phát hiện đúng loại lỗi dây chuyền kiểu
"already contains a definition" do cùng một class xuất hiện ở nhiều file khác nhau.
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path

TYPE_RE = re.compile(
    r"^\s*(?:public|private|protected|internal)?\s*"
    r"(?:(?:sealed|static|abstract|partial)\s+)*"
    r"(?:class|record|struct|enum|interface)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Tìm type C# bị khai báo ở nhiều file trong project Eris.")
    parser.add_argument("project_root", nargs="?", default="..", help="Thư mục chứa body/ và src/.")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    by_type: dict[str, list[Path]] = defaultdict(list)
    files = [p for p in root.rglob("*.cs") if ".godot" not in p.parts and "obj" not in p.parts and "bin" not in p.parts]

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
        for name in TYPE_RE.findall(text):
            by_type[name].append(path)

    suspicious = []
    for name, paths in sorted(by_type.items()):
        unique = sorted(set(paths))
        # partial UdpListener hợp lệ khi tất cả part cùng nằm trong Body chuẩn.
        roots = {"body" if _under(p, root / "body") else "src" if _under(p, root / "src") else "other" for p in unique}
        if len(unique) > 1 and len(roots) > 1:
            suspicious.append((name, unique))

    if not suspicious:
        print("[OK] Không thấy type C# bị nhân bản giữa body/src/other.")
        return 0

    print(f"[FAIL] Phát hiện {len(suspicious)} type có khả năng bị compile trùng:\n")
    for name, paths in suspicious:
        print(f"- {name}")
        for path in paths:
            print(f"    {path.relative_to(root)}")
    return 1


def _under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
