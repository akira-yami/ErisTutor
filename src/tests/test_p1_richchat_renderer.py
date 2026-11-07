"""P1 JS renderer contract tests; skip if Node is unavailable."""
import shutil
import subprocess
from pathlib import Path
import pytest

JS = Path(__file__).resolve().parents[1] / 'body' / 'richchat' / 'artifacts.js'


def run_js(source):
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    result = subprocess.run(['node', '-e', f"const A = require({str(JS)!r});\n" + source],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_renders_math_and_table_as_native_blocks():
    source = r'''
let art={schema:'eris.artifact.v1',artifact_id:'art_'+'a'.repeat(24),
  revision:1,turn_id:'chat:1',kind:'math',content:{latex:'\\frac{1}{2}'},
  presentation:{target:['rich_chat'],title:'Math'}};
let math=A.render(art);
if(math.error || !math.html.includes('math-block')||!math.hasMath) process.exit(1);
art.kind='table';art.content={columns:['x','y'],rows:[['1','2']]};
let table=A.render(art);
if(table.error||!table.html.includes('<table>'))process.exit(2);
console.log('OK');
'''
    assert 'OK' in run_js(source)


def test_escapes_untrusted_tool_content():
    source = r'''
let art={schema:'eris.artifact.v1',artifact_id:'art_'+'a'.repeat(24),
  revision:1,turn_id:'chat:1',kind:'table',
  content:{columns:['<img src=x onerror=alert(1)>'],rows:[['<script>alert(1)</script>']]},
  presentation:{target:['rich_chat'],title:'<script>x</script>'}};
let result=A.render(art);
if(result.html.includes('<script>')||result.html.includes('<img '))process.exit(1);
if(!result.html.includes('&lt;script&gt;'))process.exit(2);
console.log('SAFE');
'''
    assert 'SAFE' in run_js(source)


def test_requires_exact_turn_and_safe_artifact_ids():
    source = r'''
let art={schema:'eris.artifact.v1',artifact_id:'art_'+'a'.repeat(24),
  revision:1,turn_id:'chat:2',kind:'math',content:{latex:'x'},
  presentation:{target:['rich_chat']}};
if(A.flatten([{turn_id:'chat:1',artifacts:[art]}]).length)process.exit(1);
art.turn_id='chat:1';
if(A.flatten([{turn_id:'chat:1',artifacts:[art]}]).length!==1)process.exit(2);
art.artifact_id='../bad';
if(!A.render(art).error)process.exit(3);
console.log('STRICT');
'''
    assert 'STRICT' in run_js(source)


def test_p2_hides_provisional_gallery_when_tool_result_is_bound():
    source = r'''
const oldId='pi_'+'b'.repeat(24);
const artId='art_'+'a'.repeat(24);
const provisional={intent_id:oldId,turn_id:'job:1',provisional:true,
  artifacts:[{schema:'eris.artifact.v1',artifact_id:artId,revision:1,
    turn_id:'job:1',kind:'math',content:{latex:'x'},presentation:{target:['rich_chat']}}]};
const bound={intent_id:'pi_'+'c'.repeat(24),turn_id:'delivery:actual',provisional:false,
  artifacts:[{schema:'eris.artifact.v1',artifact_id:'art_'+'d'.repeat(24),revision:1,
    turn_id:'delivery:actual',kind:'math',content:{latex:'x'},
    metadata:{source_bundle_id:oldId},presentation:{target:['rich_chat']}}]};
const items=A.flatten([provisional,bound]);
if(items.length!==1 || items[0].turn_id!=='delivery:actual')process.exit(1);
if(A.flatten([provisional]).length!==1)process.exit(2);
console.log('DEDUP_OK');
'''
    assert 'DEDUP_OK' in run_js(source)
