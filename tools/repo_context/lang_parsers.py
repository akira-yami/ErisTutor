# -*- coding: utf-8 -*-
"""Language Parsers — Plugin parsers cho từng ngôn ngữ lập trình.

Mỗi parser nhận Path → trả về FileEntry chuẩn.
Không cần dependency ngoài (stdlib only + regex).

Parsers hiện có:
    Python   (.py)              → ast.parse() — full signatures
    GDScript (.gd)              → regex — class/func
    C#       (.cs)              → regex — class/interface/method
    TypeScript (.ts, .tsx)      → regex — class/interface/function/arrow
    JavaScript (.js, .jsx)      → regex — function/class/arrow
    Shader   (.shader, .gdshader, .glsl, .hlsl, .wgsl) → regex — functions
    Fallback (any)              → first non-empty lines + grep patterns

Thêm parser mới: implement _parse_<lang>() và đăng ký vào PARSER_REGISTRY.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MethodEntry:
    name: str
    args: str = ""
    returns: str = ""
    is_static: bool = False
    is_async: bool = False
    access: str = "public"  # public / private / protected


@dataclass
class ClassEntry:
    name: str
    bases: list[str] = field(default_factory=list)
    doc: str = ""
    methods: list[MethodEntry] = field(default_factory=list)
    kind: str = "class"  # class / interface / struct / enum


@dataclass
class FunctionEntry:
    name: str
    args: str = ""
    returns: str = ""
    is_async: bool = False
    doc: str = ""


@dataclass
class FileEntry:
    path: Path
    size_bytes: int
    language: str
    module_doc: str = ""
    classes: list[ClassEntry] = field(default_factory=list)
    functions: list[FunctionEntry] = field(default_factory=list)
    parse_error: bool = False
    error_message: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Python parser (AST-based)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_python(path: Path, source: str) -> FileEntry:
    """Parse Python via ast module — full signature accuracy."""
    import ast

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        return FileEntry(
            path=path,
            size_bytes=len(source.encode()),
            language="python",
            parse_error=True,
            error_message=str(e),
        )

    # Module docstring
    module_doc = ""
    body = getattr(tree, "body", [])
    if body and isinstance(body[0], ast.Expr):
        val = body[0].value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            module_doc = val.value.strip().split("\n")[0][:120]

    def _ann(node: ast.expr | None) -> str:
        if node is None:
            return ""
        try:
            return ast.unparse(node)[:80]
        except Exception:
            return "..."

    def _args(args: ast.arguments) -> str:
        parts = []
        for a in args.posonlyargs:
            parts.append(a.arg)
        if args.posonlyargs:
            parts.append("/")
        for a in args.args:
            ann = _ann(a.annotation)
            parts.append(f"{a.arg}: {ann}" if ann else a.arg)
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        elif args.kwonlyargs:
            parts.append("*")
        for a in args.kwonlyargs:
            parts.append(a.arg)
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        result = ", ".join(parts)
        return result[:100] + ("..." if len(result) > 100 else "")

    def _method_doc(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        b = getattr(node, "body", [])
        if b and isinstance(b[0], ast.Expr):
            v = b[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                return v.value.strip().split("\n")[0][:80]
        return ""

    classes: list[ClassEntry] = []
    functions: list[FunctionEntry] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    bases.append("?")

            # Class docstring
            cls_doc = ""
            cbody = getattr(node, "body", [])
            if cbody and isinstance(cbody[0], ast.Expr):
                cv = cbody[0].value
                if isinstance(cv, ast.Constant) and isinstance(cv.value, str):
                    cls_doc = cv.value.strip().split("\n")[0][:80]

            methods = []
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    is_static = any(
                        (ast.unparse(d) if hasattr(ast, "unparse") else "").strip() == "staticmethod"
                        for d in item.decorator_list
                    )
                    access = "private" if item.name.startswith("__") and not item.name.endswith("__") else "public"
                    methods.append(MethodEntry(
                        name=item.name,
                        args=_args(item.args),
                        returns=_ann(item.returns),
                        is_static=is_static,
                        is_async=isinstance(item, ast.AsyncFunctionDef),
                        access=access,
                    ))

            classes.append(ClassEntry(
                name=node.name,
                bases=bases,
                doc=cls_doc,
                methods=methods,
                kind="class",
            ))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(FunctionEntry(
                name=node.name,
                args=_args(node.args),
                returns=_ann(node.returns),
                is_async=isinstance(node, ast.AsyncFunctionDef),
                doc=_method_doc(node),
            ))

    return FileEntry(
        path=path,
        size_bytes=len(source.encode()),
        language="python",
        module_doc=module_doc,
        classes=classes,
        functions=functions,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GDScript parser (regex)
# ─────────────────────────────────────────────────────────────────────────────

_GD_CLASS_RE = re.compile(r"^class\s+(\w+)(?:\s+extends\s+(\w+))?", re.MULTILINE)
_GD_FUNC_RE = re.compile(
    r"^(?:static\s+)?func\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*(\w[\w\.]*))?\s*:",
    re.MULTILINE,
)
_GD_DOC_RE = re.compile(r'^##\s*(.*)', re.MULTILINE)


def _parse_gdscript(path: Path, source: str) -> FileEntry:
    """Parse GDScript via regex."""
    lines = source.split("\n")

    # Module doc: first ## comment block
    module_doc = ""
    for line in lines[:20]:
        stripped = line.strip()
        if stripped.startswith("##"):
            module_doc = stripped[2:].strip()[:120]
            break
        elif stripped and not stripped.startswith("#"):
            break

    # Top-level class (optional — GDScript files are often one implicit class)
    # Find named inner classes
    classes: list[ClassEntry] = []
    functions: list[FunctionEntry] = []

    # Detect extends (file-level base class)
    extends_match = re.search(r"^extends\s+(\S+)", source, re.MULTILINE)
    file_extends = extends_match.group(1) if extends_match else ""

    # All funcs (top-level = file class methods in GDScript)
    for m in _GD_FUNC_RE.finditer(source):
        name = m.group(1)
        args = (m.group(2) or "").strip()
        ret = (m.group(3) or "").strip()
        # Check if inside a named class (rough: indented)
        line_start = source.rfind("\n", 0, m.start()) + 1
        indent = len(source[line_start:m.start()]) - len(source[line_start:m.start()].lstrip())
        is_static = "static func" in source[max(0, m.start()-10):m.start()+1]
        if indent == 0:
            # Top-level func → file class method
            functions.append(FunctionEntry(name=name, args=args, returns=ret))
        # Inner class funcs: skip for now (handled below)

    # Named inner classes
    for m in _GD_CLASS_RE.finditer(source):
        cls_name = m.group(1)
        bases = [m.group(2)] if m.group(2) else (["Object"] if not file_extends else [file_extends])
        classes.append(ClassEntry(name=cls_name, bases=bases, kind="class"))

    # If no explicit top-level class, create implicit file class
    if file_extends or functions:
        file_class = ClassEntry(
            name=path.stem,
            bases=[file_extends] if file_extends else [],
            doc=module_doc,
            methods=[
                MethodEntry(name=fn.name, args=fn.args, returns=fn.returns)
                for fn in functions
            ],
            kind="class",
        )
        classes.insert(0, file_class)
        functions = []  # Move to class methods

    return FileEntry(
        path=path,
        size_bytes=len(source.encode()),
        language="gdscript",
        module_doc=module_doc,
        classes=classes,
        functions=functions,
    )


# ─────────────────────────────────────────────────────────────────────────────
# C# parser (regex)
# ─────────────────────────────────────────────────────────────────────────────

_CS_CLASS_RE = re.compile(
    r"^\s*(?:public|private|internal|protected|sealed|abstract|static|partial)*\s*"
    r"(class|interface|struct|enum|record)\s+(\w+)"
    r"(?:\s*[:<][^{]*)?\s*\{",
    re.MULTILINE,
)
_CS_METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed)*\s+"
    r"(?:async\s+)?"
    r"([\w\[\]<>,\s\.?]+?)\s+"   # return type
    r"(\w+)\s*"                   # method name
    r"\(([^)]*)\)\s*"             # args
    r"(?:where\s+\w+[^{]*)?"
    r"\{",
    re.MULTILINE,
)
_CS_DOC_RE = re.compile(r'///\s*<summary>(.*?)</summary>', re.DOTALL)


def _parse_csharp(path: Path, source: str) -> FileEntry:
    """Parse C# via regex."""
    # Namespace/module doc from summary comment
    module_doc = ""
    doc_match = _CS_DOC_RE.search(source[:2000])
    if doc_match:
        module_doc = doc_match.group(1).strip().replace("\n", " ")[:120]

    # Find namespace
    ns_match = re.search(r"^namespace\s+([\w.]+)", source, re.MULTILINE)
    namespace = ns_match.group(1) if ns_match else ""

    classes: list[ClassEntry] = []

    for m in _CS_CLASS_RE.finditer(source):
        kind = m.group(1)  # class / interface / struct / enum
        name = m.group(2)
        # Find methods inside (rough: scan after class opening)
        class_start = m.end()
        # Find matching closing brace (simplified: look for next top-level {})
        class_body = source[class_start:class_start + 8000]  # rough window

        methods: list[MethodEntry] = []
        for mm in _CS_METHOD_RE.finditer(class_body):
            ret_type = mm.group(1).strip()
            mname = mm.group(2)
            args = mm.group(3).strip()

            # Skip if it looks like a control flow keyword
            if mname in {"if", "while", "for", "foreach", "switch", "using", "catch"}:
                continue
            # Skip constructors that look like class name (handled separately)
            if ret_type in {"public", "private", "protected", "internal", "static"}:
                continue

            access = "public"
            full_match_text = class_body[max(0, mm.start()-60):mm.start()]
            if "private" in full_match_text:
                access = "private"
            elif "protected" in full_match_text:
                access = "protected"
            is_async = "async" in full_match_text

            methods.append(MethodEntry(
                name=mname,
                args=args[:80],
                returns=ret_type[:40],
                is_async=is_async,
                access=access,
            ))

        classes.append(ClassEntry(
            name=name,
            bases=[namespace] if namespace else [],
            methods=methods[:20],  # cap
            kind=kind,
        ))

    return FileEntry(
        path=path,
        size_bytes=len(source.encode()),
        language="csharp",
        module_doc=module_doc,
        classes=classes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# TypeScript / JavaScript parser (regex)
# ─────────────────────────────────────────────────────────────────────────────

_TS_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:abstract\s+)?(class|interface)\s+(\w+)"
    r"(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?\s*\{",
    re.MULTILINE,
)
_TS_METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|static|async|override|readonly|abstract)*\s*"
    r"(?:async\s+)?"
    r"(\w+)\s*\(([^)]*)\)(?:\s*:\s*([\w\[\]<>|&\s\.?]+))?\s*[{;]",
    re.MULTILINE,
)
_TS_FUNC_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)(?:\s*:\s*([\w\[\]<>|&\s\.?]+))?\s*\{",
    re.MULTILINE,
)
_TS_ARROW_RE = re.compile(
    r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(",
    re.MULTILINE,
)


def _parse_typescript(path: Path, source: str) -> FileEntry:
    """Parse TypeScript / JavaScript via regex."""
    # Module doc from JSDoc
    module_doc = ""
    jsdoc_match = re.search(r"/\*\*\s*(.*?)\*/", source[:1000], re.DOTALL)
    if jsdoc_match:
        first_line = jsdoc_match.group(1).strip().split("\n")[0]
        module_doc = re.sub(r"^\*+\s*", "", first_line).strip()[:120]

    lang = "typescript" if path.suffix in {".ts", ".tsx"} else "javascript"
    classes: list[ClassEntry] = []
    functions: list[FunctionEntry] = []

    for m in _TS_CLASS_RE.finditer(source):
        kind = m.group(1)
        name = m.group(2)
        extends = m.group(3) or ""
        bases = [extends] if extends else []

        # Methods (rough scan)
        class_body = source[m.end():m.end() + 6000]
        methods = []
        for mm in _TS_METHOD_RE.finditer(class_body):
            mname = mm.group(1)
            if mname in {"if", "while", "for", "return", "const", "let", "var"}:
                continue
            methods.append(MethodEntry(
                name=mname,
                args=mm.group(2).strip()[:80] if mm.group(2) else "",
                returns=mm.group(3).strip()[:40] if mm.group(3) else "",
            ))

        classes.append(ClassEntry(name=name, bases=bases, methods=methods[:15], kind=kind))

    # Top-level functions
    for m in _TS_FUNC_RE.finditer(source):
        functions.append(FunctionEntry(
            name=m.group(1),
            args=m.group(2).strip()[:80] if m.group(2) else "",
            returns=m.group(3).strip()[:40] if m.group(3) else "",
        ))

    # Arrow functions (export const)
    for m in _TS_ARROW_RE.finditer(source):
        functions.append(FunctionEntry(name=m.group(1), args="..."))

    return FileEntry(
        path=path,
        size_bytes=len(source.encode()),
        language=lang,
        module_doc=module_doc,
        classes=classes,
        functions=functions[:20],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shader parser (regex — very simple)
# ─────────────────────────────────────────────────────────────────────────────

_SHADER_FUNC_RE = re.compile(
    r"^(?:void|float|vec[234]|mat[234]|bool|int|uint|sampler\w*|half\w*|[\w]+)\s+"
    r"(\w+)\s*\(([^)]*)\)\s*\{",
    re.MULTILINE,
)


def _parse_shader(path: Path, source: str) -> FileEntry:
    """Parse GLSL/HLSL/GDShader — extract function signatures."""
    lang_map = {
        ".glsl": "shader", ".hlsl": "shader",
        ".wgsl": "shader", ".shader": "shader",
        ".gdshader": "shader",
    }
    lang = lang_map.get(path.suffix.lower(), "shader")

    functions = []
    for m in _SHADER_FUNC_RE.finditer(source):
        functions.append(FunctionEntry(
            name=m.group(1),
            args=m.group(2).strip()[:80],
        ))

    # Shader type from first non-comment line
    module_doc = ""
    for line in source.split("\n")[:10]:
        stripped = line.strip()
        if stripped and not stripped.startswith("//") and not stripped.startswith("#"):
            module_doc = stripped[:80]
            break

    return FileEntry(
        path=path,
        size_bytes=len(source.encode()),
        language=lang,
        module_doc=module_doc,
        functions=functions[:30],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fallback parser (any text file)
# ─────────────────────────────────────────────────────────────────────────────

_FALLBACK_PATTERN = re.compile(
    r"^\s*(?:def|func|function|fn|sub|procedure|method|fun)\s+(\w+)\s*\(",
    re.MULTILINE | re.IGNORECASE,
)
_FALLBACK_CLASS = re.compile(
    r"^\s*(?:class|struct|interface|trait|object)\s+(\w+)",
    re.MULTILINE | re.IGNORECASE,
)


def _parse_fallback(path: Path, source: str) -> FileEntry:
    """Generic fallback: grep for class/func patterns."""
    ext = path.suffix.lower()

    # Guess language from extension
    from .repo_detector import _EXT_TO_LANG
    lang = _EXT_TO_LANG.get(ext, ext.lstrip(".") or "text")

    # First non-empty line as doc
    module_doc = ""
    for line in source.split("\n")[:5]:
        stripped = line.strip().lstrip("#//;*").strip()
        if stripped:
            module_doc = stripped[:100]
            break

    classes = []
    functions = []

    for m in _FALLBACK_CLASS.finditer(source[:10000]):
        classes.append(ClassEntry(name=m.group(1)))

    for m in _FALLBACK_PATTERN.finditer(source[:10000]):
        functions.append(FunctionEntry(name=m.group(1)))

    return FileEntry(
        path=path,
        size_bytes=len(source.encode()),
        language=lang,
        module_doc=module_doc,
        classes=classes[:20],
        functions=functions[:20],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Parser registry + dispatch
# ─────────────────────────────────────────────────────────────────────────────

# Extension → parser function
PARSER_REGISTRY: dict[str, Callable[[Path, str], FileEntry]] = {
    ".py":       _parse_python,
    ".gd":       _parse_gdscript,
    ".cs":       _parse_csharp,
    ".ts":       _parse_typescript,
    ".tsx":      _parse_typescript,
    ".js":       _parse_typescript,   # JS parser is subset of TS
    ".jsx":      _parse_typescript,
    ".shader":   _parse_shader,
    ".gdshader": _parse_shader,
    ".glsl":     _parse_shader,
    ".hlsl":     _parse_shader,
    ".wgsl":     _parse_shader,
}

# Extensions bỏ qua hoàn toàn (binary / data / không có ý nghĩa với AI)
SKIP_EXTENSIONS: set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".avi", ".webm",
    ".ttf", ".otf", ".woff", ".woff2",
    ".pdf", ".docx", ".xlsx",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".pyd",
    ".pyc", ".pyo",
    ".min.js",
    ".lock",       # package-lock.json etc (too large)
    ".import",     # Godot import files
    ".uid",        # Godot UID files
}

# Tên file bỏ qua
SKIP_FILENAMES: set[str] = {
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
}

# Max file size để parse (tránh file khổng lồ)
MAX_PARSE_SIZE = 500_000  # 500KB


def parse_file(path: Path) -> FileEntry | None:
    """Parse một file bất kỳ → FileEntry.

    Args:
        path: Path tới file.

    Returns:
        FileEntry hoặc None nếu file nên bỏ qua.
    """
    # Skip checks
    if path.name in SKIP_FILENAMES:
        return None

    suffix = path.suffix.lower()
    # Handle compound extension như .min.js
    if path.name.endswith(".min.js") or path.name.endswith(".min.css"):
        return None

    if suffix in SKIP_EXTENSIONS:
        return None

    # Size check
    try:
        size = path.stat().st_size
    except OSError:
        return None

    if size == 0:
        return None

    if size > MAX_PARSE_SIZE:
        # File quá lớn: chỉ trả stub
        return FileEntry(
            path=path,
            size_bytes=size,
            language=suffix.lstrip(".") or "unknown",
            module_doc=f"[file too large: {size//1024}KB]",
        )

    # Read source
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return FileEntry(
            path=path,
            size_bytes=size,
            language=suffix.lstrip(".") or "unknown",
            parse_error=True,
            error_message=str(e),
        )

    # Select parser
    parser = PARSER_REGISTRY.get(suffix)
    if parser is not None:
        try:
            return parser(path, source)
        except Exception as e:
            return FileEntry(
                path=path,
                size_bytes=size,
                language=suffix.lstrip(".") or "unknown",
                parse_error=True,
                error_message=str(e),
            )

    # Fallback: chỉ parse nếu là text file có vẻ là code
    text_like_exts = {
        ".lua", ".rb", ".php", ".swift", ".kt", ".go", ".rs",
        ".java", ".cpp", ".c", ".h", ".hpp", ".cc",
        ".gml", ".nim", ".zig", ".ex", ".exs",
        ".tscn", ".tres",  # Godot scene/resource (text format)
        ".cfg", ".ini", ".toml", ".yaml", ".yml",
        ".json",
        ".md", ".txt",
        ".bat", ".sh", ".bash", ".ps1",
        ".sql",
        ".html", ".xml",
        ".scss", ".css",
    }
    if suffix in text_like_exts:
        try:
            return _parse_fallback(path, source)
        except Exception:
            pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Text rendering
# ─────────────────────────────────────────────────────────────────────────────

def entry_to_text(entry: FileEntry, repo_root: Path | None = None) -> str:
    """Chuyển FileEntry → text skeleton đẹp."""
    lines: list[str] = []

    try:
        rel = entry.path.relative_to(repo_root) if repo_root else entry.path
        rel_str = rel.as_posix()
    except (ValueError, AttributeError):
        rel_str = str(entry.path)

    size_kb = entry.size_bytes / 1024
    lang_badge = f"[{entry.language}]"
    lines.append(f"\n# {rel_str} {lang_badge} [{size_kb:.0f}KB]")

    if entry.module_doc:
        lines.append(f"# {entry.module_doc}")

    if entry.parse_error:
        lines.append(f"# [!] Parse error: {entry.error_message}")
        return "\n".join(lines)

    for cls in entry.classes:
        bases_str = f"({', '.join(cls.bases)})" if cls.bases else ""
        kind_prefix = "" if cls.kind == "class" else f"{cls.kind} "
        lines.append(f"{kind_prefix}class {cls.name}{bases_str}:")
        if cls.doc:
            lines.append(f'  """{cls.doc}"""')
        for m in cls.methods[:15]:
            ret = f" -> {m.returns}" if m.returns else ""
            prefix = "async " if m.is_async else ""
            static = "static " if m.is_static else ""
            lines.append(f"  {static}{prefix}def {m.name}({m.args}){ret}")

    for fn in entry.functions[:20]:
        ret = f" -> {fn.returns}" if fn.returns else ""
        prefix = "async " if fn.is_async else ""
        lines.append(f"{prefix}def {fn.name}({fn.args}){ret}")

    return "\n".join(lines)
