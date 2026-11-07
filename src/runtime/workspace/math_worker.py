# -*- coding: utf-8 -*-
"""Tiến trình tính toán riêng cho Artifact Workspace.

Đây là worker CÓ GIỚI HẠN phép toán, không phải môi trường chạy Python
không tin cậy. Chỉ nhận JSON khai báo tác vụ; không eval/exec chuỗi của LLM.
Nếu cần chạy mã Python tự do, sử dụng backend Docker riêng.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path
import sys


def _expr(source: str, *, x=None):
    import sympy as sp
    if not isinstance(source, str) or len(source) > 250:
        raise ValueError('Biểu thức phải là chuỗi ngắn tối đa 250 ký tự')
    names = {'x': sp.Symbol('x'), 'y': sp.Symbol('y'), 'pi': sp.pi, 'E': sp.E}
    funcs = {'sin': sp.sin, 'cos': sp.cos, 'tan': sp.tan, 'exp': sp.exp,
             'log': sp.log, 'sqrt': sp.sqrt, 'abs': sp.Abs}
    tree = ast.parse(source.replace('^', '**'), mode='eval')
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise ValueError('Biểu thức quá phức tạp')

    def walk(node, depth=0):
        if depth > 16:
            raise ValueError('Biểu thức lồng quá sâu')
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            if abs(float(node.value)) > 1e6 or not math.isfinite(float(node.value)):
                raise ValueError('Hằng số vượt giới hạn')
            return sp.Rational(str(node.value))
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand, depth+1)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            a, b = walk(node.left, depth+1), walk(node.right, depth+1)
            if isinstance(node.op, ast.Add): return a+b
            if isinstance(node.op, ast.Sub): return a-b
            if isinstance(node.op, ast.Mult): return a*b
            if isinstance(node.op, ast.Div): return a/b
            if isinstance(node.op, ast.Pow):
                if not b.is_number or abs(float(b)) > 12:
                    raise ValueError('Lũy thừa quá lớn hoặc không xác định')
                return a**b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = funcs.get(node.func.id)
            if fn and len(node.args) == 1 and not node.keywords:
                return fn(walk(node.args[0], depth+1))
        raise ValueError(f'Phép toán không được hỗ trợ: {type(node).__name__}')

    return walk(tree.body)


def _save_figure(fig, folder: Path, filename: str, artifacts: list[dict]):
    path = folder / filename
    fig.savefig(path, dpi=145, bbox_inches='tight', facecolor='#121922')
    import matplotlib.pyplot as plt
    plt.close(fig)
    data = path.read_bytes()
    if len(data) > 3_000_000:
        path.unlink(missing_ok=True)
        raise ValueError('Ảnh vượt 3 MB')
    artifacts.append({'type': 'image', 'path': filename, 'sha256': hashlib.sha256(data).hexdigest(),
                      'alt': 'Hình ảnh được tính toán và dựng từ Python'})


def _formula(latex: str, folder: Path, artifacts: list[dict], *, filename: str = "formula.png"):
    """Dùng mathtext cho tập LaTeX được hỗ trợ; trả chữ thay thế khi lỗi."""
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    fig, ax = plt.subplots(figsize=(8, 1.5))
    fig.patch.set_facecolor('#121922')
    ax.set_axis_off()
    try:
        ax.text(.03, .55, '$' + latex + '$', fontsize=22, color='white', va='center', transform=ax.transAxes)
        fig.canvas.draw()  # Phát hiện cú pháp LaTeX mà mathtext không hỗ trợ.
        _save_figure(fig, folder, filename, artifacts)
    except Exception:
        plt.close(fig)
        artifacts.append({'type': 'text', 'text': 'Công thức LaTeX (trình dựng ảnh không hỗ trợ cú pháp này): ' + latex[:400]})


def _plot(expr, raw: dict, folder: Path, artifacts: list[dict]):
    import numpy as np
    import sympy as sp
    import matplotlib.pyplot as plt
    x = sp.Symbol('x')
    if expr.free_symbols - {x}:
        raise ValueError('Đồ thị hiện hỗ trợ biểu thức chỉ phụ thuộc x')
    low = float(raw.get('xmin', -5)); high = float(raw.get('xmax', 5))
    if not (math.isfinite(low) and math.isfinite(high) and -1e5 <= low < high <= 1e5):
        raise ValueError('Khoảng x không hợp lệ')
    if high-low > 1e5:
        raise ValueError('Khoảng đồ thị quá rộng')
    xx = np.linspace(low, high, 500)
    fn = sp.lambdify(x, expr, modules=['numpy'])
    with np.errstate(all='ignore'):
        yy = np.asarray(fn(xx), dtype=float)
    if yy.ndim == 0: yy = np.full_like(xx, float(yy))
    if yy.shape != xx.shape:
        raise ValueError('Giá trị đồ thị không hợp lệ')
    yy[~np.isfinite(yy)] = np.nan
    yy[np.abs(yy) > 1e6] = np.nan
    if not np.isfinite(yy).any():
        raise ValueError('Không có điểm hữu hạn để vẽ')
    fig, ax = plt.subplots(figsize=(8, 4.6))
    fig.patch.set_facecolor('#121922'); ax.set_facecolor('#19232e')
    ax.plot(xx, yy, color='#70d2fa', linewidth=2.2)
    ax.axhline(0, color='#b3c0cc', linewidth=.8); ax.axvline(0, color='#b3c0cc', linewidth=.8)
    ax.grid(alpha=.15); ax.tick_params(colors='#e2e8f0')
    for spine in ax.spines.values(): spine.set_color('#596577')
    ax.set_xlabel('x', color='white'); ax.set_ylabel('y', color='white')
    ax.set_title('y = '+str(expr)[:70], color='white')
    _save_figure(fig, folder, 'plot.png', artifacts)


def _matrix(rows, folder: Path, artifacts: list[dict]):
    import sympy as sp
    import matplotlib.pyplot as plt
    if (not isinstance(rows, list) or not rows or len(rows) > 8 or
        any(not isinstance(r, list) or len(r) != len(rows[0]) for r in rows) or
        not (1 <= len(rows[0]) <= 8)):
        raise ValueError('Ma trận phải là mảng 1-8 hàng và 1-8 cột đều nhau')
    safe_rows = []
    for row in rows:
        safe_row = []
        for cell in row:
            if not isinstance(cell, (int, float, str)) or len(str(cell)) > 40:
                raise ValueError('Phần tử ma trận không hợp lệ')
            value = _expr(str(cell))
            if value.free_symbols:
                raise ValueError('Bản ma trận hiện hỗ trợ phần tử số')
            safe_row.append(value)
        safe_rows.append(safe_row)
    m = sp.Matrix(safe_rows)
    artifacts.append({'type':'table', 'headers':[f'C{i+1}' for i in range(m.cols)],
                      'rows':[[str(m[i,j]) for j in range(m.cols)] for i in range(m.rows)]})
    result = 'Ma trận ' + str(m.rows) + '×' + str(m.cols)
    if m.rows == m.cols:
        result += '\nĐịnh thức: ' + str(m.det()) + '\nHạng: ' + str(m.rank())
        if m.det() != 0:
            result += '\nMa trận khả nghịch.'
    else:
        result += '\nHạng: ' + str(m.rank())
    artifacts.append({'type':'text','text':result})
    fig, ax = plt.subplots(figsize=(max(4, m.cols*1.05), max(2.2, m.rows*.65)))
    fig.patch.set_facecolor('#121922'); ax.set_axis_off()
    for i in range(m.rows):
        for j in range(m.cols):
            ax.text((j+.5)/m.cols, 1-(i+.5)/m.rows, str(m[i,j])[:28],
                    color='#f5f7fa', fontsize=18, ha='center', va='center', transform=ax.transAxes)
    ax.plot([.02,.005,.005,.02], [.95,.95,.05,.05], color='#83d4fc', transform=ax.transAxes)
    ax.plot([.98,.995,.995,.98], [.95,.95,.05,.05], color='#83d4fc', transform=ax.transAxes)
    _save_figure(fig, folder, 'matrix.png', artifacts)



def _binary_determinant_max(request: dict, folder: Path, artifacts: list[dict]) -> None:
    """Tìm định thức lớn nhất cho ma trận 0/1 cấp 3, kèm chứng minh tổng quát.

    Kiểm hết 512 ma trận để chọn ví dụ; chứng minh chặn trên dùng bất đẳng
    thức Hadamard trên ma trận dấu cấp 4, không dùng vét cạn làm chứng minh duy nhất.
    """
    import itertools
    import sympy as sp
    n = request.get('size', 3)
    if type(n) is not int or n != 3:
        raise ValueError('Hiện chỉ hỗ trợ ma trận 0/1 cấp 3 cho loại bài toán này')
    maximum = -1000
    minimum = 1000
    witness = None
    count = 0
    for bits in itertools.product((0, 1), repeat=9):
        a,b,c,d,e,f,g,h,i = bits
        determinant = a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)
        count += 1
        if determinant > maximum:
            maximum, witness = determinant, [list(bits[j:j+3]) for j in (0, 3, 6)]
        minimum = min(minimum, determinant)
    if maximum != 2 or minimum != -2 or witness is None or count != 512:
        raise RuntimeError('Kết quả vét cạn không khớp tính chất toán học đã kiểm chứng')
    artifacts.extend([
        {'type':'section', 'text':'Bước 1 · Kết luận'},
        {'type':'text', 'text':'Giá trị lớn nhất của định thức ma trận 0–1 cấp 3 là 2.'},
        {'type':'formula', 'latex':r'\max_{a_{ij}\in\{0,1\}}\det(A)=2'},
        {'type':'section', 'text':'Bước 2 · Một ma trận đạt dấu bằng'},
    ])
    _formula(r'\max\mathrm{det}(A)=2', folder, artifacts, filename='formula_1.png')
    _matrix(witness, folder, artifacts)
    artifacts.append({'type':'text', 'text':'Ma trận ở trên có det(A) = 2 (đã kiểm tra chính xác).'} )
    artifacts.extend([
        {'type':'section', 'text':'Bước 3 · Vì sao không thể lớn hơn 2?'},
        {'type':'text', 'text':(
            'Với ma trận A cấp 3 chỉ gồm 0 và 1, dựng ma trận H cấp 4: '
            'hàng đầu tiên toàn số 1; cột đầu tiên toàn số 1; '
            'khối 3×3 còn lại là 1−2A (chỉ gồm −1 và 1). '
            'Trừ hàng đầu tiên khỏi ba hàng còn lại ta có |det(H)| = 8|det(A)|.' )},
        {'type':'text', 'text':(
            'Mỗi hàng của H gồm bốn số ±1 nên có độ dài Euclid bằng 2. '
            'Bất đẳng thức Hadamard cho |det(H)| ≤ 2⁴ = 16. '
            'Suy ra 8|det(A)| ≤ 16 và |det(A)| ≤ 2. '
            'Vì đã tìm thấy ma trận đạt 2 nên giá trị lớn nhất đúng bằng 2.')},
        {'type':'formula', 'latex':r'8|\det(A)|=|\det(H)|\leq 16\Rightarrow |\det(A)|\leq 2'},
        {'type':'section', 'text':'Bước 4 · Kiểm tra độc lập bằng máy'},
        {'type':'text', 'text':'Đã xét đủ 2⁹ = 512 ma trận 0–1 cấp 3, giá trị nhỏ nhất là −2 và lớn nhất là 2.'},
    ])
    _formula(r'|\mathrm{det}(A)|\leq 2', folder, artifacts, filename='formula_2.png')


def run(request: dict, folder: Path) -> dict:
    import sympy as sp
    import matplotlib
    matplotlib.use('Agg')
    folder.mkdir(parents=True, exist_ok=True)
    kind = str(request.get('kind') or '')
    artifacts = []
    if kind == 'binary_determinant_max':
        _binary_determinant_max(request, folder, artifacts)
    elif kind == 'matrix':
        _matrix(request.get('rows'), folder, artifacts)
    elif kind in {'simplify','differentiate','integrate','solve','plot'}:
        source = str(request.get('expression') or '')
        expr = _expr(source)
        artifacts.append({'type':'code','language':'text', 'text':source})
        if kind == 'plot':
            _plot(expr, request, folder, artifacts)
        else:
            var = sp.Symbol('x')
            if kind == 'simplify': answer = sp.simplify(expr)
            elif kind == 'differentiate': answer = sp.diff(expr, var)
            elif kind == 'integrate': answer = sp.integrate(expr, var)
            else:
                if expr.free_symbols - {var}: raise ValueError('Giải phương trình hiện chỉ theo x')
                solutions = sp.solve(expr, var)
                artifacts.append({'type':'text','text':'Nghiệm: '+', '.join(map(str,solutions))})
                answer = None
            if answer is not None:
                artifacts.append({'type':'text', 'text':str(answer)})
                artifacts.append({'type':'formula','latex':sp.latex(answer)[:1200]})
                _formula(sp.latex(answer)[:500], folder, artifacts)
    else:
        raise ValueError('Tác vụ không được hỗ trợ: '+kind)
    return {'version':1,'title':str(request.get('title') or 'Kết quả toán học')[:140],
            'artifacts': artifacts}


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    input_path = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()
    # Đảm bảo input/output cùng vùng làm việc đã khai báo.
    if input_path.parent != output_dir.parent:
        raise ValueError('Thư mục input/output không khớp')
    request = json.loads(input_path.read_text(encoding='utf-8'))
    if len(input_path.read_bytes()) > 12_000:
        raise ValueError('Yêu cầu vượt giới hạn')
    result = run(request, output_dir)
    (output_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print('ARTIFACT_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
