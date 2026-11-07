# -*- coding: utf-8 -*-
"""
Eris Semantic Indexer (Codebase Indexing)
Sử dụng LanceDB, Tree-sitter và SentenceTransformers để lập chỉ mục mã nguồn.
Tham khảo kiến trúc từ Continue.dev.
"""

import os
import uuid
import sqlite3
import hashlib
import threading
from pathlib import Path
from typing import List, Dict, Any, Generator

_SEMANTIC_IMPORT_ERROR: Exception | None = None
try:
    import lancedb
    import pyarrow as pa
    from sentence_transformers import SentenceTransformer
    import tree_sitter
    import tree_sitter_python
    import tree_sitter_javascript
    import tree_sitter_typescript
except ImportError as exc:
    lancedb = None
    _SEMANTIC_IMPORT_ERROR = exc

class Chunk:
    def __init__(self, path: str, content: str, start_line: int, end_line: int):
        self.path = path
        self.content = content
        self.start_line = start_line
        self.end_line = end_line

class SemanticIndexer:
    def __init__(self, workspace_path: str):
        if lancedb is None:
            detail = f": {_SEMANTIC_IMPORT_ERROR}" if _SEMANTIC_IMPORT_ERROR else ""
            raise ImportError(
                "Semantic coding dependencies are missing; install Eris with the 'semantic' extra" + detail
            )
            
        self.workspace_path = Path(workspace_path)
        self.eris_dir = self.workspace_path / ".eris"
        self.eris_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_path = self.eris_dir / "lancedb"
        self.sqlite_path = self.eris_dir / "index_cache.sqlite"
        
        self.db = lancedb.connect(str(self.db_path))
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self.table_name = "codebase_index"
        self._lock = threading.RLock()

        self._init_sqlite()
        self._init_parsers()

    def _init_sqlite(self):
        self.conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS index_cache (
                uuid TEXT PRIMARY KEY,
                cacheKey TEXT,
                path TEXT,
                startLine INTEGER,
                endLine INTEGER,
                contents TEXT
            )
        ''')
        self.conn.commit()

    def _init_parsers(self):
        self.parsers = {
            ".py": tree_sitter.Language(tree_sitter_python.language()),
            ".js": tree_sitter.Language(tree_sitter_javascript.language()),
            ".ts": tree_sitter.Language(tree_sitter_typescript.language_typescript()),
            ".tsx": tree_sitter.Language(tree_sitter_typescript.language_tsx()),
        }

    def _get_cache_key(self, content: str) -> str:
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _chunk_file(self, file_path: Path, content: str) -> List[Chunk]:
        ext = file_path.suffix.lower()
        if ext not in self.parsers:
            # Fallback naive chunking by lines for unknown languages
            lines = content.split('\n')
            chunks = []
            chunk_size = 50
            for i in range(0, len(lines), chunk_size):
                chunk_lines = lines[i:i+chunk_size]
                chunks.append(Chunk(str(file_path), '\n'.join(chunk_lines), i, i+len(chunk_lines)))
            return chunks

        # Tree-sitter AST chunking
        parser = tree_sitter.Parser()
        parser.set_language(self.parsers[ext])
        tree = parser.parse(content.encode('utf-8'))
        
        chunks = []
        
        # Hàm đệ quy để trích xuất hàm và class
        def traverse(node):
            if node.type in ['function_definition', 'class_definition', 'method_definition', 'arrow_function', 'function_declaration']:
                start_line = node.start_point[0]
                end_line = node.end_point[0]
                chunk_content = content.split('\n')[start_line:end_line+1]
                chunks.append(Chunk(str(file_path), '\n'.join(chunk_content), start_line, end_line))
            
            for child in node.children:
                traverse(child)
                
        traverse(tree.root_node)
        
        if not chunks:
            # Nếu không tìm thấy hàm/class, chia theo dòng
            chunks = [Chunk(str(file_path), content[:2000], 0, len(content.split('\n')))]
            
        return chunks

    def is_ready(self) -> bool:
        """Whether this workspace already has a searchable vector table."""
        try:
            return self.table_name in self.db.table_names()
        except Exception:
            return False

    def _remove_indexed_path(self, relative_path: str) -> None:
        """Delete stale chunks for one file before inserting its new version."""
        cursor = self.conn.execute("SELECT uuid FROM index_cache WHERE path = ?", (relative_path,))
        uuids = [str(row[0]) for row in cursor.fetchall() if row and row[0]]
        if uuids and self.table_name in self.db.table_names():
            tbl = self.db.open_table(self.table_name)
            escaped = [value.replace("'", "''") for value in uuids]
            uuid_str = ", ".join(f"'{value}'" for value in escaped)
            tbl.delete(f"uuid IN ({uuid_str})")
        self.conn.execute("DELETE FROM index_cache WHERE path = ?", (relative_path,))

    def index_workspace(self, force: bool = False):
        """Incrementally index the workspace without retaining stale file chunks."""
        data = []
        cache_inserts = []
        
        for root, dirs, files in os.walk(self.workspace_path):
            if ".git" in root or "node_modules" in root or ".eris" in root or "__pycache__" in root:
                continue
                
            for file in files:
                if not file.endswith(('.py', '.js', '.ts', '.tsx', '.md', '.json')):
                    continue
                
                path = Path(root) / file
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                except Exception:
                    continue
                    
                cache_key = self._get_cache_key(content)
                rel_path = str(path.relative_to(self.workspace_path)).replace("\\", "/")
                
                # Check cache
                cursor = self.conn.execute("SELECT uuid FROM index_cache WHERE cacheKey = ? AND path = ?", (cache_key, rel_path))
                if cursor.fetchone() and not force:
                    continue
                    
                # The file changed (or force=True): remove its previous chunks
                # before inserting replacements, otherwise semantic search keeps
                # returning obsolete implementations forever.
                with self._lock:
                    self._remove_indexed_path(rel_path)

                # Chunk and Embed
                chunks = self._chunk_file(path, content)
                for chunk in chunks:
                    chunk_uuid = str(uuid.uuid4())
                    vector = self.model.encode(chunk.content).tolist()
                    data.append({
                        "vector": vector,
                        "uuid": chunk_uuid,
                        "path": rel_path
                    })
                    cache_inserts.append((chunk_uuid, cache_key, rel_path, chunk.start_line, chunk.end_line, chunk.content))
                    
        if data:
            with self._lock:
                if self.table_name in self.db.table_names():
                    tbl = self.db.open_table(self.table_name)
                    tbl.add(data)
                else:
                    self.db.create_table(self.table_name, data=data)

                self.conn.executemany(
                    "INSERT OR REPLACE INTO index_cache (uuid, cacheKey, path, startLine, endLine, contents) VALUES (?, ?, ?, ?, ?, ?)",
                    cache_inserts
                )
                self.conn.commit()
            
    def update_file(self, relative_path: str, content: str):
        """Cập nhật một file vào index (dùng khi user sửa file trên VS Code)."""
        cache_key = self._get_cache_key(content)
        path = self.workspace_path / relative_path
        
        # Xóa chunk cũ atomically trước khi thêm bản mới.
        with self._lock:
            self._remove_indexed_path(relative_path)
        
        # Thêm chunk mới
        data = []
        cache_inserts = []
        chunks = self._chunk_file(path, content)
        for chunk in chunks:
            chunk_uuid = str(uuid.uuid4())
            vector = self.model.encode(chunk.content).tolist()
            data.append({
                "vector": vector,
                "uuid": chunk_uuid,
                "path": relative_path
            })
            cache_inserts.append((chunk_uuid, cache_key, relative_path, chunk.start_line, chunk.end_line, chunk.content))
            
        if data:
            with self._lock:
                if self.table_name in self.db.table_names():
                    tbl = self.db.open_table(self.table_name)
                    tbl.add(data)
                else:
                    self.db.create_table(self.table_name, data=data)

                self.conn.executemany(
                    "INSERT OR REPLACE INTO index_cache (uuid, cacheKey, path, startLine, endLine, contents) VALUES (?, ?, ?, ?, ?, ?)",
                    cache_inserts
                )
                self.conn.commit()

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Tìm kiếm ngữ nghĩa (Semantic Search)."""
        if self.table_name not in self.db.table_names():
            return []
            
        vector = self.model.encode(query).tolist()
        with self._lock:
            tbl = self.db.open_table(self.table_name)
            results = tbl.search(vector).limit(limit).to_list()

            final_results = []
            for res in results:
                uuid_val = res['uuid']
                cursor = self.conn.execute("SELECT path, startLine, endLine, contents FROM index_cache WHERE uuid = ?", (uuid_val,))
                row = cursor.fetchone()
                if row:
                    final_results.append({
                        "path": row[0],
                        "startLine": row[1],
                        "endLine": row[2],
                        "content": row[3],
                        "distance": res['_distance']
                    })

        return final_results
