# -*- coding: utf-8 -*-
"""Persistent repository context cho coding agent Eris.

Mục tiêu của module này không phải nhét toàn bộ source vào prompt. Thay vào đó,
Eris luôn lập bản đồ toàn workspace (path, symbol, import, config, entrypoint), lưu
cache bền vững và chỉ compile phần ngữ cảnh liên quan tới task hiện tại. Đây là
ranh giới quan trọng giữa "đã index cả repo" và "đốt context window bằng cả repo".
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

_REPO_CONTEXT_VERSION = "repo_context.v1"
_MAX_PARSE_BYTES = 1_500_000
_MAX_PROMPT_CHARS = 16_000
_MAX_SYMBOLS_PER_FILE = 80
_MAX_IMPORTS_PER_FILE = 80

# Vendor/cache/build không đại diện cho code người dùng và làm index phình vô ích.
# Tên file vẫn được hiểu thông qua config/package manifests, không cần parse node_modules.
_IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode-test", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "env", "node_modules", "__pycache__",
    "dist", "build", "out", "coverage", ".next", ".nuxt", ".gradle", "target",
}
_SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".cs", ".gd",
    ".java", ".kt", ".kts", ".go", ".rs", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".vue", ".svelte", ".html", ".css", ".scss", ".sql", ".sh", ".ps1", ".bat",
}
_TEXT_CONFIG_EXTENSIONS = {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".md", ".txt", ".xml"}
_IMPORTANT_NAMES = {
    "readme", "readme.md", "package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt",
    "tsconfig.json", "jsconfig.json", "project.godot", "cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
}
_ENTRY_NAMES = {
    "main.py", "app.py", "server.py", "index.ts", "index.tsx", "index.js", "main.ts", "main.tsx",
    "main.js", "program.cs", "main.cs", "main.gd", "main.rs", "main.go",
}

_TOKEN_RE = re.compile(r"[A-Za-zÀ-ỹ0-9_.\-/]+", re.UNICODE)
_TS_SYMBOL_RE = re.compile(
    r"(?:^|\n)\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:class|interface|type|enum|function|const|let|var)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_GENERIC_SYMBOL_RE = re.compile(
    r"(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+|final\s+|abstract\s+)*"
    r"(?:class|interface|struct|enum|func|function|def)\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)
_IMPORT_RE = re.compile(
    r"(?:from\s+([A-Za-z0-9_./-]+)\s+import|import\s+([A-Za-z0-9_./-]+)|"
    r"from\s+['\"]([^'\"]+)['\"]|require\(\s*['\"]([^'\"]+)['\"]\s*\))"
)


def _safe_rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _path_key(value: str) -> str:
    return str(value or "").replace("\\", "/").strip("/").lower()


def _goal_tokens(goal: str) -> list[str]:
    tokens: list[str] = []
    stop = {"file", "files", "code", "repo", "src", "trong", "các", "cho", "với", "the", "and"}
    for raw in _TOKEN_RE.findall(str(goal or "").lower()):
        # Path user gõ (kể cả typo như src/cotex) phải đóng góp từng component;
        # nếu giữ nguyên "src/cotex" thì fuzzy matcher chẳng bao giờ gặp "cortex".
        pieces = [piece.strip("._-") for piece in re.split(r"[\\/]", raw) if piece.strip("._-")]
        for token in pieces:
            if len(token) >= 3 and token not in stop:
                tokens.append(token)
    return list(dict.fromkeys(tokens))[:64]


def _workspace_cache_id(workspace: Path) -> str:
    normalized = os.path.normcase(str(workspace.resolve()))
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:20]


class RepositoryContextManager:
    """Index toàn repository và compile context pack theo task.

    Cache được lưu ngoài workspace (cạnh AgentTaskStore), vì đây là state của Eris
    chứ không phải artifact mà user phải commit vào Git.
    """

    def __init__(self, cache_root: str | Path, *, logger: Any = None) -> None:
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.logger = logger

    def ensure(self, workspace_path: str | Path, goal: str = "") -> dict[str, Any]:
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            return {
                "version": _REPO_CONTEXT_VERSION,
                "ready": False,
                "workspace": str(workspace),
                "error": "workspace_unavailable",
                "context_pack": "",
                "resolved_targets": [],
                "confidence": 0.0,
            }

        cache_path = self.cache_root / f"{_workspace_cache_id(workspace)}.json"
        previous = self._read_cache(cache_path)
        previous_files = previous.get("files") if isinstance(previous.get("files"), dict) else {}

        entries, directories = self._scan_inventory(workspace)
        current_paths = set(entries)
        previous_paths = set(previous_files)
        removed = sorted(previous_paths - current_paths)
        changed: list[str] = []
        reused = 0
        indexed_files: dict[str, dict[str, Any]] = {}

        for rel, meta in entries.items():
            old = previous_files.get(rel) if isinstance(previous_files.get(rel), dict) else None
            if old and self._fingerprint_equal(old, meta):
                indexed_files[rel] = old
                reused += 1
                continue
            indexed_files[rel] = self._index_file(workspace, rel, meta)
            changed.append(rel)

        index = self._build_index_document(
            workspace,
            indexed_files,
            directories,
            changed=changed,
            removed=removed,
            reused=reused,
        )
        self._write_cache(cache_path, index)

        grounded = self._ground_goal(goal, index)
        context_pack = self._compile_context_pack(goal, index, grounded)
        return {
            "version": _REPO_CONTEXT_VERSION,
            "ready": True,
            "workspace": str(workspace),
            "cache_path": str(cache_path),
            "stats": index.get("stats", {}),
            "architecture": index.get("architecture", {}),
            "resolved_targets": grounded["targets"],
            "target_files": grounded["target_files"],
            "confidence": grounded["confidence"],
            "changed_since_index": changed,
            "removed_since_index": removed,
            "cache_reused_files": reused,
            "context_pack": context_pack,
        }

    def _scan_inventory(self, workspace: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
        entries: dict[str, dict[str, Any]] = {}
        directories: set[str] = set()
        for current, dirnames, filenames in os.walk(workspace):
            dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".eris_tmp"))
            current_path = Path(current)
            if current_path != workspace:
                try:
                    directories.add(_safe_rel(workspace, current_path))
                except ValueError:
                    continue
            for name in sorted(filenames):
                path = current_path / name
                try:
                    stat = path.stat()
                    if path.is_symlink() and workspace not in path.resolve().parents:
                        continue
                    rel = _safe_rel(workspace, path)
                except (OSError, RuntimeError, ValueError):
                    continue
                suffix = path.suffix.lower()
                entries[rel] = {
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                    "suffix": suffix,
                    "source": suffix in _SOURCE_EXTENSIONS,
                    "config": suffix in _TEXT_CONFIG_EXTENSIONS or name.lower() in _IMPORTANT_NAMES,
                }
        return entries, sorted(directories)

    @staticmethod
    def _fingerprint_equal(old: dict[str, Any], current: dict[str, Any]) -> bool:
        return int(old.get("size", -1)) == int(current.get("size", -2)) and int(old.get("mtime_ns", -1)) == int(current.get("mtime_ns", -2))

    def _index_file(self, workspace: Path, rel: str, meta: dict[str, Any]) -> dict[str, Any]:
        out = dict(meta)
        out["symbols"] = []
        out["imports"] = []
        out["parse_error"] = ""
        if not (meta.get("source") or meta.get("config")) or int(meta.get("size", 0) or 0) > _MAX_PARSE_BYTES:
            return out
        path = workspace / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            out["parse_error"] = type(exc).__name__
            return out
        suffix = str(meta.get("suffix", "") or "")
        if suffix in {".py", ".pyi"}:
            symbols, imports = self._parse_python(text)
        else:
            symbols, imports = self._parse_generic(text)
        out["symbols"] = symbols[:_MAX_SYMBOLS_PER_FILE]
        out["imports"] = imports[:_MAX_IMPORTS_PER_FILE]
        if Path(rel).name.lower() in _IMPORTANT_NAMES:
            out["summary_head"] = "\n".join(text.splitlines()[:80])[:7000]
        return out

    @staticmethod
    def _parse_python(text: str) -> tuple[list[str], list[str]]:
        symbols: list[str] = []
        imports: list[str] = []
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, TypeError):
            return RepositoryContextManager._parse_generic(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(node.name)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module:
                    imports.append(module)
        return list(dict.fromkeys(symbols)), list(dict.fromkeys(imports))

    @staticmethod
    def _parse_generic(text: str) -> tuple[list[str], list[str]]:
        symbols = _TS_SYMBOL_RE.findall(text)
        if not symbols:
            symbols = _GENERIC_SYMBOL_RE.findall(text)
        imports: list[str] = []
        for match in _IMPORT_RE.finditer(text):
            imports.append(next((group for group in match.groups() if group), ""))
        return list(dict.fromkeys(s for s in symbols if s)), list(dict.fromkeys(i for i in imports if i))

    def _build_index_document(
        self,
        workspace: Path,
        files: dict[str, dict[str, Any]],
        directories: list[str],
        *,
        changed: list[str],
        removed: list[str],
        reused: int,
    ) -> dict[str, Any]:
        languages = Counter()
        symbol_count = 0
        import_count = 0
        source_roots = Counter()
        important_files: list[str] = []
        entrypoints: list[str] = []
        for rel, meta in files.items():
            suffix = str(meta.get("suffix", "") or "")
            if meta.get("source"):
                languages[suffix or "(none)"] += 1
                first = rel.split("/", 1)[0]
                source_roots[first] += 1
            symbol_count += len(meta.get("symbols") or [])
            import_count += len(meta.get("imports") or [])
            name = Path(rel).name.lower()
            if name in _IMPORTANT_NAMES or name.startswith("readme"):
                important_files.append(rel)
            if name in _ENTRY_NAMES:
                entrypoints.append(rel)

        return {
            "version": _REPO_CONTEXT_VERSION,
            "workspace": str(workspace),
            "updated_at": time.time(),
            "files": files,
            "directories": directories,
            "stats": {
                "files": len(files),
                "directories": len(directories),
                "source_files": sum(1 for m in files.values() if m.get("source")),
                "symbols": symbol_count,
                "imports": import_count,
                "changed": len(changed),
                "removed": len(removed),
                "reused": reused,
            },
            "architecture": {
                "languages": dict(languages.most_common(12)),
                "source_roots": [name for name, _ in source_roots.most_common(12)],
                "important_files": sorted(important_files)[:80],
                "entrypoints": sorted(entrypoints)[:80],
            },
        }

    def _ground_goal(self, goal: str, index: dict[str, Any]) -> dict[str, Any]:
        tokens = _goal_tokens(goal)
        directories = list(index.get("directories") or [])
        files = list((index.get("files") or {}).keys())
        components = sorted({part for p in [*directories, *files] for part in p.split("/") if part})
        token_matches: dict[str, str] = {}
        for token in tokens:
            exact = next((c for c in components if c.lower() == token), "")
            if exact:
                token_matches[token] = exact
                continue
            close = difflib.get_close_matches(token, [c.lower() for c in components], n=1, cutoff=0.74)
            if close:
                lowered = close[0]
                token_matches[token] = next((c for c in components if c.lower() == lowered), lowered)

        scored: list[tuple[float, str, str]] = []
        for path in directories:
            score = self._score_path(path, tokens, token_matches, is_dir=True)
            if score > 0:
                scored.append((score, path, "directory"))
        for path in files:
            score = self._score_path(path, tokens, token_matches, is_dir=False)
            if score > 0:
                scored.append((score, path, "file"))
        scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))

        targets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for score, path, kind in scored:
            key = _path_key(path)
            if key in seen:
                continue
            # Nếu directory đã được chọn thì các file con chưa cần chiếm target slot.
            if kind == "file" and any(t["kind"] == "directory" and key.startswith(_path_key(t["path"]) + "/") for t in targets):
                continue
            seen.add(key)
            targets.append({"path": path, "kind": kind, "score": round(score, 3)})
            if len(targets) >= 8:
                break

        target_files: list[str] = []
        for target in targets:
            path = str(target["path"])
            if target["kind"] == "file":
                target_files.append(path)
            else:
                prefix = _path_key(path) + "/"
                for rel, meta in (index.get("files") or {}).items():
                    if _path_key(rel).startswith(prefix) and (meta.get("source") or meta.get("config")):
                        target_files.append(rel)
        target_files = list(dict.fromkeys(target_files))[:120]

        top_score = float(targets[0]["score"]) if targets else 0.0
        confidence = min(0.99, 0.55 + min(0.44, top_score / 5.0)) if targets else 0.62
        return {"targets": targets, "target_files": target_files, "confidence": round(confidence, 3), "token_matches": token_matches}

    @staticmethod
    def _score_path(path: str, tokens: list[str], token_matches: dict[str, str], *, is_dir: bool) -> float:
        lower = _path_key(path)
        parts = lower.split("/")
        score = 0.0
        for token in tokens:
            if token == lower:
                score += 5.0
            elif token in parts:
                score += 3.5
            elif token in lower:
                score += 1.6
            matched = str(token_matches.get(token, "") or "").lower()
            if matched and matched in parts:
                score += 2.7
        if is_dir and score > 0:
            score += 0.15
        return score

    def _compile_context_pack(self, goal: str, index: dict[str, Any], grounded: dict[str, Any]) -> str:
        stats = index.get("stats") if isinstance(index.get("stats"), dict) else {}
        architecture = index.get("architecture") if isinstance(index.get("architecture"), dict) else {}
        files = index.get("files") if isinstance(index.get("files"), dict) else {}

        top_tree = self._compact_tree(list(files), max_depth=2, limit=220)
        targets = list(grounded.get("targets") or [])
        target_files = list(grounded.get("target_files") or [])
        target_lines: list[str] = []
        for rel in target_files[:80]:
            meta = files.get(rel) if isinstance(files.get(rel), dict) else {}
            symbols = ", ".join((meta.get("symbols") or [])[:12])
            imports = ", ".join((meta.get("imports") or [])[:8])
            extras = []
            if symbols:
                extras.append("symbols=" + symbols)
            if imports:
                extras.append("imports=" + imports)
            target_lines.append(f"- {rel}" + (" | " + " | ".join(extras) if extras else ""))

        important_lines: list[str] = []
        for rel in (architecture.get("important_files") or [])[:14]:
            meta = files.get(rel) if isinstance(files.get(rel), dict) else {}
            head = str(meta.get("summary_head", "") or "").strip()
            if head:
                head = re.sub(r"\s+", " ", head)[:900]
                important_lines.append(f"- {rel}: {head}")
            else:
                important_lines.append(f"- {rel}")

        text = (
            "[PERSISTENT REPOSITORY CONTEXT]\n"
            f"Index version: {_REPO_CONTEXT_VERSION}\n"
            f"Workspace: {index.get('workspace', '')}\n"
            f"Indexed full inventory: {stats.get('files', 0)} files, {stats.get('source_files', 0)} source files, "
            f"{stats.get('symbols', 0)} symbols, {stats.get('imports', 0)} imports.\n"
            f"Languages/extensions: {json.dumps(architecture.get('languages', {}), ensure_ascii=False)}\n"
            f"Source roots: {', '.join(architecture.get('source_roots', []) or []) or '(none)'}\n"
            f"Entrypoints: {', '.join(architecture.get('entrypoints', []) or []) or '(none)'}\n"
            f"Task: {goal}\n"
            f"Resolved targets (confidence={grounded.get('confidence', 0)}): {json.dumps(targets, ensure_ascii=False)}\n"
            "\nRepository tree (depth<=2; the persistent index contains the rest):\n"
            + "\n".join(top_tree)
            + "\n\nTask-near files from the full index:\n"
            + ("\n".join(target_lines) if target_lines else "- No strong target yet; use read/search tools to ground the task.")
            + "\n\nImportant manifests/docs:\n"
            + ("\n".join(important_lines) if important_lines else "- none detected")
            + "\n\nRULE: This index is orientation evidence, not a substitute for reading implementation. "
              "Before modifying an existing file, deep-read that file and relevant callers/dependencies. "
              "Resolve ambiguous/typo paths against this index instead of guessing."
        )
        if len(text) > _MAX_PROMPT_CHARS:
            text = text[: _MAX_PROMPT_CHARS - 1].rstrip() + "…"
        return text

    @staticmethod
    def _compact_tree(paths: Iterable[str], *, max_depth: int, limit: int) -> list[str]:
        seen: set[str] = set()
        rows: list[str] = []
        for rel in sorted(paths):
            parts = rel.split("/")
            if len(parts) > max_depth + 1:
                key = "/".join(parts[:max_depth]) + "/…"
            else:
                key = rel
            if key in seen:
                continue
            seen.add(key)
            rows.append("- " + key)
            if len(rows) >= limit:
                rows.append("- … (full path inventory remains in persistent RepoContext)")
                break
        return rows

    @staticmethod
    def _read_cache(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("version") == _REPO_CONTEXT_VERSION:
                return payload
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return {}

    @staticmethod
    def _write_cache(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


__all__ = ["RepositoryContextManager"]
