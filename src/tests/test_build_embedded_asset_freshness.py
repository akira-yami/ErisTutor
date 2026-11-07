"""An embedded browser asset change must invalidate the reused Godot export."""
import ast
import os
from pathlib import Path
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('filename', ['chat.js', 'index.html', 'chat.css', 'canvas.js', 'canvas.html', 'canvas.css'])
def test_fast_build_rejects_export_older_than_embedded_asset(tmp_path, filename):
    tree = ast.parse((REPO / '_fullbuild.py').read_text(encoding='utf-8-sig'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'newest_mtime', 'assert_godot_pack_fresh'}]
    namespace = {'os': os, 'sys': sys, 'REPO': str(tmp_path)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '_fullbuild.py', 'exec'), namespace)
    asset = tmp_path / 'src/body/richchat' / filename
    asset.parent.mkdir(parents=True)
    asset.write_text('changed browser resource', encoding='utf-8')
    pack = tmp_path / 'Eris.pck'
    pack.write_bytes(b'old exported body')
    os.utime(pack, (100, 100))
    os.utime(asset, (200, 200))
    with pytest.raises(SystemExit) as rejected:
        namespace['assert_godot_pack_fresh'](str(pack))
    assert rejected.value.code == 1
    os.utime(pack, (300, 300))
    namespace['assert_godot_pack_fresh'](str(pack))
