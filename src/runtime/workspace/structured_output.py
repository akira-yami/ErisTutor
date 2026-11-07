# -*- coding: utf-8 -*-
"""V5: Biểu diễn tài liệu công khai độc lập với thoại của Eris.

Không thay nội dung Root, không sửa stage, không tự quyết có nên nói.
Chỉ biến transcript ĐÃ COMMIT thành các khối hiển thị trong panel.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import threading
import uuid
from typing import Any, Mapping

_MAX_TEXT = 24_000
_MAX_BLOCKS = 80
_MAX_UDP = 46_000
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ERIS-StructuredOutput")


def text_digest(text: str) -> str:
    """Hash UTF-8 của transcript gốc, không chuẩn hóa/đổi dấu."""
    return hashlib.sha256(str(text).replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def _paragraphs(lines: list[str]) -> list[dict]:
    """Phân tích Markdown ở mức block, bảo toàn nguyên văn văn bản.

    LaTeX không bị diễn giải thành tag sân khấu hoặc công cụ.
    """
    result: list[dict[str, Any]] = []
    buffer: list[str] = []
    n = len(lines)

    def flush() -> None:
        if buffer:
            content = "\n".join(buffer)
            # Không để một câu Root dài thành bức tường chữ. Phân đoạn ở
            # ranh giới câu, không thêm/bớt ý, transcript gốc vẫn được lưu nguyên.
            if len(content) <= 390:
                result.append({"type": "paragraph", "text": content})
            else:
                sentences = re.split(r'(?<=[.!?;])\s+', content)
                group = ""
                for sentence in sentences:
                    if not sentence: continue
                    if group and len(group) + len(sentence) + 1 > 310:
                        result.append({"type": "paragraph", "text": group})
                        group = ""
                    group += (" " if group else "") + sentence
                if group: result.append({"type": "paragraph", "text": group})
            buffer.clear()

    i = 0
    while i < n and len(result) < _MAX_BLOCKS:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue
        fence = re.match(r'^\s*(`{3,}|~{3,})([\w.+-]*)\s*$', line)
        if fence:
            flush()
            marker = fence.group(1)
            code: list[str] = []
            i += 1
            while i < n and not re.match(r'^\s*' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}\s*$', lines[i]):
                code.append(lines[i]); i += 1
            result.append({"type": "code", "language": fence.group(2)[:30], "text": "\n".join(code)})
            i += 1
            continue
        if stripped in ('$$', r'\['):
            flush()
            end = '$$' if stripped == '$$' else r'\]'
            latex: list[str] = []
            i += 1
            while i < n and lines[i].strip() != end:
                latex.append(lines[i]); i += 1
            result.append({"type": "formula", "latex": "\n".join(latex).strip()})
            i += 1
            continue
        blockmath = re.fullmatch(r'\s*\$\$(.+?)\$\$\s*', line) or re.fullmatch(r'\s*\\\[(.+?)\\\]\s*', line)
        if blockmath:
            flush(); result.append({"type": "formula", "latex": blockmath.group(1).strip()}); i += 1; continue
        heading = re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if heading:
            flush(); result.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2)}); i += 1; continue
        if re.match(r'^\s*>\s?', line):
            flush()
            quote = []
            while i < n and re.match(r'^\s*>\s?', lines[i]):
                quote.append(re.sub(r'^\s*>\s?', '', lines[i])); i += 1
            result.append({"type": "quote", "text": "\n".join(quote)}); continue
        if re.match(r'^\s*(?:[-*+] |\d+[.)] )', line):
            flush(); entries=[]
            while i < n and re.match(r'^\s*(?:[-*+] |\d+[.)] )', lines[i]):
                entries.append(re.sub(r'^\s*(?:[-*+] |\d+[.)] )', '', lines[i])); i += 1
            result.append({"type": "list", "items": entries[:40]}); continue
        if '|' in line and i+1 < n and re.match(r'^\s*\|?\s*:?-{2,}:?\s*\|', lines[i+1]):
            flush()
            headers = [s.strip() for s in line.strip().strip('|').split('|')]
            rows=[]; i += 2
            while i < n and '|' in lines[i] and len(rows)<30:
                rows.append([s.strip() for s in lines[i].strip().strip('|').split('|')]);i += 1
            result.append({"type": "table", "headers": headers[:12], "rows": rows});continue
        buffer.append(line); i += 1
    flush()
    return result[:_MAX_BLOCKS]


# Một số phiên TTS/Root xuất toán bằng ký tự Unicode (∫₀¹, x²), thay vì
# LaTeX có dấu $. Đổi CHỈ biểu thức tích phân có cận và vi phân đủ rõ thành
# formula để panel dựng toán; phần chữ xung quanh vẫn giữ nguyên.
_SUB = str.maketrans('₀₁₂₃₄₅₆₇₈₉₋', '0123456789-')
_SUP = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁻', '0123456789-')
_UNICODE_INTEGRAL = (
    r'∫\s*[₀₁₂₃₄₅₆₇₈₉₋]+\s*[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+\s*'
    r'[a-zA-Z0-9²³⁰¹^*/+().\- ]{1,70}?\s*d[a-zA-Z]\b'
    r'(?:\s*=\s*[0-9a-zA-Z²³⁰¹₀₁₂₃₄₅₆₇₈₉ |^*/+().\-]+){0,3}'
)
_INLINE_MATH = re.compile(
    r'(?<!\\)\$(?!\$)(.+?)(?<!\\)\$|\\\((.+?)\\\)|(?P<unicode>' + _UNICODE_INTEGRAL + r')'
)


def _unicode_integral_latex(expression: str) -> str:
    match = re.match(
        r'∫\s*([₀₁₂₃₄₅₆₇₈₉₋]+)\s*([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)\s*(.*)',
        expression, re.S,
    )
    if not match:
        return expression
    lower, upper, rest = match.groups()
    rest = re.sub(
        r'\|([₀₁₂₃₄₅₆₇₈₉₋]+)([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)',
        lambda m: r'\vert_{' + m.group(1).translate(_SUB) + '}^{' + m.group(2).translate(_SUP) + '}',
        rest,
    )
    rest = re.sub(
        r'([a-zA-Z0-9])([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+|[²³]+)',
        lambda m: m.group(1) + '^{' + m.group(2).translate(_SUP).replace('²','2').replace('³','3') + '}',
        rest,
    )
    rest = re.sub(
        r'(?<![\w{}])([0-9]+|[a-zA-Z](?:\^\{[0-9]+\})?)/([0-9]+)(?!\w)',
        lambda m: r'\frac{' + m.group(1) + '}{' + m.group(2) + '}',
        rest,
    )
    rest = re.sub(r'\bd([a-zA-Z])\b', r'\\,d\1', rest, count=1)
    return r'\int_{' + lower.translate(_SUB) + '}^{' + upper.translate(_SUP) + '} ' + rest.strip()


def parse_markdown(text: str) -> list[dict]:
    """Chuyển transcript sang khối, không bắt Root phải gọi tool."""
    if not isinstance(text, str) or len(text) > _MAX_TEXT:
        raise ValueError("Transcript quá dài hoặc không hợp lệ")
    initial = _paragraphs(text.splitlines())
    output: list[dict] = []
    pattern = _INLINE_MATH
    for block in initial:
        if block.get('type') != 'paragraph':
            output.append(block)
            continue
        content = block['text']
        end = 0
        matches = list(pattern.finditer(content))
        for found in matches[:15]:
            if content[end:found.start()].strip():
                output.append({'type':'paragraph', 'text':content[end:found.start()].strip()})
            latex = (found.group(1) or found.group(2) or '').strip()
            if found.group('unicode'):
                latex = _unicode_integral_latex(found.group('unicode'))
            output.append({'type':'formula', 'latex':latex})
            end = found.end()
        if matches:
            if content[end:].strip(): output.append({'type':'paragraph', 'text':content[end:].strip()})
        else:
            output.append(block)
    return output[:_MAX_BLOCKS]


def _render_math(blocks: list[dict], workspace: Path, delivery_id: str) -> None:
    """Dựng PNG cho LaTeX phổ biến, fallback chữ nếu MathText chưa hỗ trợ.

    Chỉ đọc biểu thức toán; không gọi shell/LaTeX executable từ output model.
    """
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt

    folder = workspace / 'documents' / delivery_id
    folder.mkdir(parents=True, exist_ok=True)
    count=0
    for block in blocks:
        if block.get('type') != 'formula' or count >= 12:
            continue
        tex = str(block.get('latex','')).strip()
        if not tex or len(tex) > 700:
            continue
        try:
            matrix = re.search(r'\\begin\{([pbvBV]?matrix)\}(.+?)\\end\{\1\}', tex, re.S)
            if matrix:
                # Hình ma trận được dựng từ từng ô; không đòi cài TeX/Chromium.
                rows = [[cell.strip() for cell in row.split('&')]
                        for row in re.split(r'\\\\', matrix.group(2))]
                rows = [row for row in rows if any(row)]
                if not rows or len(rows) > 12 or max(map(len, rows)) > 12:
                    continue
                fig = plt.figure(figsize=(min(10., max(3.6, max(map(len,rows))*1.4)),
                                          min(9., max(1.8, len(rows)*.72))), dpi=125)
                fig.patch.set_alpha(0)
                ax=fig.add_axes([.1,.08,.8,.84]); ax.axis('off')
                for ri,row in enumerate(rows):
                    for ci,cell in enumerate(row):
                        ax.text((ci+.5)/max(map(len,rows)), 1-(ri+.5)/len(rows),
                                '$'+cell+'$', ha='center',va='center',fontsize=18)
                ax.text(.02,.5,'(', fontsize=65,va='center',ha='right')
                ax.text(.98,.5,')', fontsize=65,va='center',ha='left')
            else:
                fig = plt.figure(figsize=(min(12.,max(4.,len(tex)*0.095)), 1.1), dpi=125)
                fig.patch.set_alpha(0)
                fig.text(.03, .5, '$'+tex.strip('$')+'$', fontsize=17, va='center')
            name = f'math_{count}.png'
            dest = folder / name
            fig.savefig(dest, dpi=125, transparent=True, bbox_inches='tight', pad_inches=.2)
            plt.close(fig)
            data=dest.read_bytes()
            if len(data)>2_500_000: dest.unlink(missing_ok=True); continue
            block['path']=f'documents/{delivery_id}/{name}'
            block['sha256']=hashlib.sha256(data).hexdigest()
            count+=1
        except Exception:
            plt.close('all')
            # Công thức không được Matplotlib hỗ trợ sẽ hiển thị dạng có thể copy.
            continue


def prepare_document(*, text: str, session_id: str, generation: int,
                     run_id: str, delivery_id: str, turn_id: str = "", workspace: str | Path | None = None,
                     render_math: bool = True) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', delivery_id):
        # Identity của lượt có thể chứa ':' hoặc UUID. Không dùng trực tiếp làm path.
        disk_id = hashlib.sha256(delivery_id.encode('utf-8')).hexdigest()[:28]
    else:
        disk_id = delivery_id
    blocks = parse_markdown(text)
    if workspace is not None and render_math:
        try: _render_math(blocks, Path(workspace), disk_id)
        except (ImportError, OSError): pass
    return {
        'parse_mode':'workspace_document','workspace_version':1,
        'document_id': text_digest(f'{session_id}:{generation}:{run_id}:{delivery_id}')[:32],
        'delivery_id':str(delivery_id)[:150], 'turn_id':str(turn_id)[:150],
        'session_id':str(session_id)[:150],
        'run_id':str(run_id)[:150], 'generation':int(generation),
        'text_hash': text_digest(text), 'blocks':blocks,
    }


def send_document_acknowledged(doc: Mapping[str, Any], *, host: str='127.0.0.1',
                               port: int=4242, retries: int=2, timeout: float=.35) -> bool:
    """Gửi datagram tài liệu riêng, đợi ACK; gửi lặp an toàn nhờ document_id."""
    raw=json.dumps(dict(doc),ensure_ascii=False,separators=(',', ':')).encode('utf-8')
    if len(raw)>_MAX_UDP:
        raise ValueError('Document vượt giới hạn UDP')
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        for _ in range(max(1,min(retries,4))):
            sock.sendto(raw,(host,port))
            try:
                reply,_=sock.recvfrom(2048)
                ack=json.loads(reply)
                if ack.get('parse_mode')=='workspace_document_ack' and ack.get('document_id')==doc.get('document_id'):
                    return ack.get('status')=='rendered'
            except (socket.timeout,ValueError,OSError):
                continue
    return False


def dispatch_committed_document(*, text: str, session_id: str, generation: int,
                                run_id: str, delivery_id: str, turn_id: str = "", transport: Any = None,
                                logger: Any=None) -> None:
    """Không chặn TTS: đưa render/gửi tài liệu ra worker riêng, sau public commit."""
    if not text.strip() or len(text)>_MAX_TEXT:
        return
    host, port = getattr(transport,'_send_target', ('127.0.0.1',4242))
    workspace = os.environ.get('ERIS_ARTIFACT_WORKSPACE','logs/eris_artifacts')

    def work():
        try:
            doc=prepare_document(text=text,session_id=session_id,generation=generation,
                                 run_id=run_id,delivery_id=delivery_id,turn_id=turn_id,workspace=workspace)
            # P0: publish a local contract fixture for the future presentation
            # runtime, without changing/depending on the existing UDP ACK.
            from .artifact_protocol import (
                protocol_enabled, adapter_document_source, write_bundle_sidecar,
            )
            if protocol_enabled():
                try:
                    bundle = adapter_document_source(doc, source_text=text)
                    write_bundle_sidecar(bundle, workspace)
                except (ValueError, OSError, TypeError) as exc:
                    if logger is not None:
                        logger.warning('[presentation-p0] document sidecar failed: %s', exc)
            ok=send_document_acknowledged(doc,host=host,port=port)
            if logger is not None and not ok:
                logger.debug('[workspace-v5] chưa có xác nhận hiển thị document=%s',doc['document_id'])
        except Exception as exc:
            if logger is not None:
                logger.debug('[workspace-v5] không gửi được tài liệu: %s',exc)
    _POOL.submit(work)
