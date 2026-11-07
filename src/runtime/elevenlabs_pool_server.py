# -*- coding: utf-8 -*-
"""elevenlabs_pool_server.py — REST API Server & HTML Control Panel Tiếng Việt cho ElevenPool.

Cung cấp Web Interface tại http://localhost:18888 hỗ trợ quản lý tài khoản,
theo dõi token/ký tự còn lại, đếm ngược thời gian hồi Quota và tự động đồng bộ.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from runtime.elevenlabs_pool_db import GLOBAL_ELEVEN_POOL, ElevenPoolDB

_LOGGER = logging.getLogger("ElevenPoolServer")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ERIS — Quản Lý Account ElevenLabs (ElevenPool)</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --card-border: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #6366f1;
            --accent-hover: #4f46e5;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --info: #3b82f6;
            --radius: 12px;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            line-height: 1.5;
            padding: 24px;
            max-width: 1280px;
            margin: 0 auto;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--card-border);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .brand-logo {
            width: 40px;
            height: 40px;
            background: linear-gradient(135deg, #818cf8, #c084fc);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 20px;
            color: #fff;
            box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);
        }

        .brand-title h1 {
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.02em;
        }

        .brand-title p {
            font-size: 13px;
            color: var(--text-muted);
        }

        .btn {
            background-color: var(--accent);
            color: #fff;
            border: none;
            padding: 10px 18px;
            font-size: 14px;
            font-weight: 600;
            border-radius: var(--radius);
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }

        .btn:hover {
            background-color: var(--accent-hover);
            transform: translateY(-1px);
        }

        .btn-secondary {
            background-color: #334155;
            color: var(--text-main);
        }

        .btn-secondary:hover {
            background-color: #475569;
        }

        .btn-sm {
            padding: 6px 12px;
            font-size: 12px;
            border-radius: 6px;
        }

        .btn-danger {
            background-color: #dc2626;
        }
        .btn-danger:hover {
            background-color: #b91c1c;
        }

        /* Stats grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .stat-card {
            background-color: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 20px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }

        .stat-label {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-muted);
            margin-bottom: 6px;
        }

        .stat-value {
            font-size: 26px;
            font-weight: 700;
            color: #fff;
        }

        .stat-sub {
            font-size: 12px;
            color: var(--success);
            margin-top: 4px;
        }

        /* Main layout */
        .grid-layout {
            display: grid;
            grid-template-columns: 1fr;
            gap: 24px;
        }

        .card {
            background-color: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 24px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }

        .card-title {
            font-size: 16px;
            font-weight: 600;
        }

        /* Table */
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }

        th {
            text-align: left;
            padding: 12px 14px;
            color: var(--text-muted);
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid var(--card-border);
        }

        td {
            padding: 14px;
            border-bottom: 1px solid #1e293b;
            vertical-align: middle;
        }

        tr:hover td {
            background-color: rgba(255, 255, 255, 0.02);
        }

        .badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 600;
        }

        .badge-active {
            background-color: rgba(16, 185, 129, 0.15);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-exhausted {
            background-color: rgba(239, 68, 68, 0.15);
            color: var(--danger);
            border: 1px solid rgba(239, 68, 68, 0.3);
        }

        .badge-disabled {
            background-color: rgba(148, 163, 184, 0.15);
            color: var(--text-muted);
            border: 1px solid rgba(148, 163, 184, 0.3);
        }

        .progress-bar-bg {
            width: 100%;
            height: 8px;
            background-color: #334155;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 6px;
        }

        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, #10b981, #6366f1);
            border-radius: 4px;
            transition: width 0.3s ease;
        }

        .progress-bar-fill.danger {
            background: linear-gradient(90deg, #f59e0b, #ef4444);
        }

        /* Form Controls */
        .form-group {
            margin-bottom: 16px;
        }

        .form-label {
            display: block;
            font-size: 13px;
            font-weight: 500;
            color: var(--text-muted);
            margin-bottom: 6px;
        }

        .form-input, .form-textarea {
            width: 100%;
            background-color: #0f172a;
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 10px 14px;
            color: var(--text-main);
            font-family: inherit;
            font-size: 14px;
            transition: border-color 0.2s ease;
        }

        .form-input:focus, .form-textarea:focus {
            outline: none;
            border-color: var(--accent);
        }

        .form-textarea {
            resize: vertical;
            min-height: 100px;
        }

        .token-text {
            font-family: monospace;
            font-size: 12px;
            color: var(--text-muted);
            background: #0f172a;
            padding: 4px 8px;
            border-radius: 4px;
        }

        .empty-state {
            text-align: center;
            padding: 48px 24px;
            color: var(--text-muted);
        }

        /* Modal */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(4px);
            align-items: center;
            justify-content: center;
            z-index: 100;
        }

        .modal-overlay.active {
            display: flex;
        }

        .modal-box {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 24px;
            width: 100%;
            max-width: 540px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }
    </style>
</head>
<body>

    <header>
        <div class="brand">
            <div class="brand-logo">E</div>
            <div class="brand-title">
                <h1>ElevenPool Dashboard</h1>
                <p>Hệ Thống Quản Lý & Xoay Token ElevenLabs Cho ERIS</p>
            </div>
        </div>
        <div style="display: flex; gap: 10px;">
            <button class="btn btn-secondary" onclick="loadData()">
                🔄 Làm Mới
            </button>
            <button class="btn" onclick="openAddModal()">
                ➕ Thêm Refresh Token
            </button>
        </div>
    </header>

    <!-- Stat Dashboard Cards -->
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-label">TỔNG KÝ TỰ KHẢ DỤNG</div>
            <div class="stat-value" id="stat-remaining">0</div>
            <div class="stat-sub" id="stat-percent">0% hạn mức khả dụng</div>
        </div>

        <div class="stat-card">
            <div class="stat-label">ACCOUNT ĐANG HOẠT ĐỘNG</div>
            <div class="stat-value" id="stat-active">0</div>
            <div class="stat-sub" style="color: var(--text-muted);" id="stat-total-acc">Tổng số 0 tài khoản</div>
        </div>

        <div class="stat-card">
            <div class="stat-label">ACCOUNT HẾT QUOTA</div>
            <div class="stat-value" style="color: var(--danger);" id="stat-exhausted">0</div>
            <div class="stat-sub" style="color: var(--warning);" id="stat-reset-info">Chờ đếm ngược reset</div>
        </div>
    </div>

    <!-- Table Card -->
    <div class="grid-layout">
        <div class="card">
            <div class="card-header">
                <div class="card-title">Danh Sách Tài Khoản ElevenLabs</div>
            </div>

            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Tên Account</th>
                            <th>Refresh Token</th>
                            <th>Trạng Thái</th>
                            <th>Dung Lượng Ký Tự Còn Lại</th>
                            <th>Thời Gian Đăng Ký</th>
                            <th>Thời Gian Hồi Quota</th>
                            <th style="text-align: right;">Hành Động</th>
                        </tr>
                    </thead>
                    <tbody id="accounts-table-body">
                        <!-- Dynamic content -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Add Account Modal -->
    <div class="modal-overlay" id="add-modal">
        <div class="modal-box">
            <div class="card-header" style="margin-bottom: 16px;">
                <div class="card-title">Thêm Tài Khoản ElevenLabs Mới</div>
                <button class="btn btn-secondary btn-sm" onclick="closeAddModal()">✕</button>
            </div>
            <form id="add-form" onsubmit="handleAddAccount(event)">
                <div class="form-group">
                    <label class="form-label">Tên Gợi Nhớ (Tùy chọn)</label>
                    <input type="text" id="add-label" class="form-input" placeholder="VD: Acc 1 - Gtvkun">
                </div>
                <div class="form-group">
                    <label class="form-label">Refresh Token(s)</label>
                    <textarea id="add-token" class="form-textarea" placeholder="Dán Refresh Token vào đây (Cho phép dán nhiều token, mỗi token 1 dòng)..." required></textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Ghi Chú</label>
                    <input type="text" id="add-notes" class="form-input" placeholder="Ghi chú bổ sung (tùy chọn)">
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px;">
                    <button type="button" class="btn btn-secondary" onclick="closeAddModal()">Hủy</button>
                    <button type="submit" class="btn">Lưu & Đồng Bộ Quota</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        function openAddModal() {
            document.getElementById('add-modal').classList.add('active');
        }

        function closeAddModal() {
            document.getElementById('add-modal').classList.remove('active');
        }

        async function loadData() {
            try {
                const res = await fetch('/api/accounts');
                const data = await res.json();

                // Update stats
                document.getElementById('stat-remaining').innerText = data.summary.total_characters_remaining.toLocaleString();
                document.getElementById('stat-active').innerText = data.summary.active_accounts;
                document.getElementById('stat-exhausted').innerText = data.summary.exhausted_accounts;
                document.getElementById('stat-total-acc').innerText = `Tổng số ${data.summary.total_accounts} tài khoản`;

                const percent = data.summary.total_characters_limit > 0
                    ? Math.round((data.summary.total_characters_remaining / data.summary.total_characters_limit) * 100)
                    : 0;
                document.getElementById('stat-percent').innerText = `${percent}% hạn mức còn lại`;

                // Render table
                const tbody = document.getElementById('accounts-table-body');
                if (!data.accounts || data.accounts.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="7" class="empty-state">
                                Chưa có tài khoản ElevenLabs nào trong danh sách.<br>
                                Vui lòng nhấn nút <strong>"Thêm Refresh Token"</strong> để bổ sung.
                            </td>
                        </tr>
                    `;
                    return;
                }

                tbody.innerHTML = data.accounts.map(acc => {
                    let badgeClass = 'badge-active';
                    let statusText = 'Hoạt động';
                    if (acc.status === 'exhausted') {
                        badgeClass = 'badge-exhausted';
                        statusText = 'Hết Quota';
                    } else if (acc.status === 'disabled') {
                        badgeClass = 'badge-disabled';
                        statusText = 'Đã Tắt';
                    } else if (acc.status === 'cooling') {
                        badgeClass = 'badge-exhausted';
                        statusText = 'Tạm Nghỉ';
                    }

                    const progressClass = acc.usage_percent > 85 ? 'progress-bar-fill danger' : 'progress-bar-fill';

                    let resetDisplay = 'Chưa xác định';
                    if (acc.reset_remaining_s > 0) {
                        const days = Math.floor(acc.reset_remaining_s / 86400);
                        const hours = Math.floor((acc.reset_remaining_s % 86400) / 3600);
                        const mins = Math.floor((acc.reset_remaining_s % 3600) / 60);
                        resetDisplay = `Còn ${days > 0 ? days + ' ngày ' : ''}${hours}h ${mins}m (${acc.reset_at_iso})`;
                    } else if (acc.reset_at_iso) {
                        resetDisplay = acc.reset_at_iso;
                    }

                    return `
                        <tr>
                            <td>
                                <strong>${escapeHtml(acc.label)}</strong>
                                ${acc.notes ? `<div style="font-size:11px; color:var(--text-muted);">${escapeHtml(acc.notes)}</div>` : ''}
                            </td>
                            <td><span class="token-text">${escapeHtml(acc.refresh_token_masked)}</span></td>
                            <td><span class="badge ${badgeClass}">${statusText}</span></td>
                            <td style="min-width: 180px;">
                                <div style="display:flex; justify-content:space-between; font-size:12px; font-weight:600;">
                                    <span>${acc.remaining_characters.toLocaleString()} ký tự còn lại</span>
                                    <span>${acc.usage_percent}%</span>
                                </div>
                                <div class="progress-bar-bg">
                                    <div class="${progressClass}" style="width: ${acc.usage_percent}%;"></div>
                                </div>
                            </td>
                            <td style="font-size:12px; color:var(--text-muted);">${acc.created_at_iso}</td>
                            <td style="font-size:12px; color:${acc.status === 'exhausted' ? 'var(--warning)' : 'var(--text-muted)'};">${resetDisplay}</td>
                            <td style="text-align: right; white-space: nowrap;">
                                <button class="btn btn-secondary btn-sm" onclick="syncAccount(${acc.id})" title="Đồng bộ lại từ ElevenLabs">🔄</button>
                                <button class="btn btn-secondary btn-sm" onclick="toggleAccount(${acc.id})" title="Bật/Tắt">${acc.status === 'disabled' ? '▶️' : '⏸️'}</button>
                                <button class="btn btn-danger btn-sm" onclick="deleteAccount(${acc.id})" title="Xóa">🗑️</button>
                            </td>
                        </tr>
                    `;
                }).join('');

            } catch (err) {
                console.error('Lỗi tải dữ liệu:', err);
            }
        }

        async function handleAddAccount(e) {
            e.preventDefault();
            const label = document.getElementById('add-label').value.trim();
            const rawToken = document.getElementById('add-token').value.trim();
            const notes = document.getElementById('add-notes').value.trim();

            if (!rawToken) return;

            const tokens = rawToken.split(/\r?\n/).map(t => t.trim()).filter(t => t.length > 0);

            try {
                for (let i = 0; i < tokens.length; i++) {
                    const tok = tokens[i];
                    const itemLabel = tokens.length > 1 ? `${label || 'Acc'} #${i + 1}` : label;
                    await fetch('/api/accounts', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ refresh_token: tok, label: itemLabel, notes: notes })
                    });
                }
                closeAddModal();
                document.getElementById('add-form').reset();
                loadData();
            } catch (err) {
                alert('Lỗi thêm tài khoản: ' + err.message);
            }
        }

        async function syncAccount(id) {
            try {
                const res = await fetch(`/api/accounts/${id}/sync`, { method: 'POST' });
                const data = await res.json();
                alert(data.message);
                loadData();
            } catch (err) {
                alert('Lỗi đồng bộ: ' + err.message);
            }
        }

        async function toggleAccount(id) {
            try {
                await fetch(`/api/accounts/${id}/toggle`, { method: 'POST' });
                loadData();
            } catch (err) {
                alert('Lỗi đổi trạng thái: ' + err.message);
            }
        }

        async function deleteAccount(id) {
            if (!confirm('Bạn có chắc chắn muốn xóa tài khoản này khỏi hệ thống xoay?')) return;
            try {
                await fetch(`/api/accounts/${id}`, { method: 'DELETE' });
                loadData();
            } catch (err) {
                alert('Lỗi xóa tài khoản: ' + err.message);
            }
        }

        function escapeHtml(str) {
            if (!str) return '';
            return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
        }

        // Auto load on start & interval
        loadData();
        setInterval(loadData, 15000);
    </script>
</body>
</html>
"""


class ElevenPoolHandler(BaseHTTPRequestHandler):
    pool_db: ElevenPoolDB = GLOBAL_ELEVEN_POOL

    def _send_json(self, data: Any, status_code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status_code: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "" or path == "/":
            self._send_html(HTML_TEMPLATE)
            return

        if path == "/api/accounts":
            accounts = [a.to_dict() for a in self.pool_db.list_accounts()]
            summary = self.pool_db.get_summary_stats()
            self._send_json({"accounts": accounts, "summary": summary})
            return

        if path == "/api/summary":
            self._send_json(self.pool_db.get_summary_stats())
            return

        self._send_json({"error": "Not Found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        payload = {}
        if post_data:
            try:
                payload = json.loads(post_data.decode("utf-8"))
            except Exception:
                pass

        if path == "/api/accounts":
            refresh_token = str(payload.get("refresh_token") or "").strip()
            label = str(payload.get("label") or "").strip()
            notes = str(payload.get("notes") or "").strip()

            ok, msg, acc = self.pool_db.add_account(refresh_token, label, notes, auto_sync=True)
            if ok and acc:
                self._send_json({"success": True, "message": msg, "account": acc.to_dict()}, 201)
            else:
                self._send_json({"success": False, "message": msg}, 400)
            return

        m_sync = re.match(r"^/api/accounts/(\d+)/sync$", path)
        if m_sync:
            acc_id = int(m_sync.group(1))
            ok, msg = self.pool_db.sync_subscription(acc_id)
            self._send_json({"success": ok, "message": msg}, 200 if ok else 400)
            return

        m_toggle = re.match(r"^/api/accounts/(\d+)/toggle$", path)
        if m_toggle:
            acc_id = int(m_toggle.group(1))
            ok, msg = self.pool_db.toggle_account(acc_id)
            self._send_json({"success": ok, "message": msg}, 200 if ok else 400)
            return

        self._send_json({"error": "Not Found"}, 404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        m_del = re.match(r"^/api/accounts/(\d+)$", path)
        if m_del:
            acc_id = int(m_del.group(1))
            ok, msg = self.pool_db.delete_account(acc_id)
            self._send_json({"success": ok, "message": msg}, 200 if ok else 400)
            return

        self._send_json({"error": "Not Found"}, 404)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard HTTP request log spam
        pass


_SERVER_INSTANCE: Optional[ThreadingHTTPServer] = None
_SERVER_THREAD: Optional[threading.Thread] = None


def start_elevenlabs_pool_server(host: str = "127.0.0.1", port: int = 18888) -> ThreadingHTTPServer:
    global _SERVER_INSTANCE, _SERVER_THREAD
    if _SERVER_INSTANCE is not None:
        return _SERVER_INSTANCE

    try:
        p_env = os.environ.get("ERIS_ELEVENLABS_POOL_PORT", "").strip()
        if p_env and p_env.isdigit():
            port = int(p_env)
    except Exception:
        pass

    server = ThreadingHTTPServer((host, port), ElevenPoolHandler)
    _SERVER_INSTANCE = server

    def _run():
        _LOGGER.info("[ElevenPool] Web UI Control Panel started at http://%s:%d", host, port)
        server.serve_forever()

    thread = threading.Thread(target=_run, daemon=True, name="ElevenPool-WebUI")
    thread.start()
    _SERVER_THREAD = thread

    return server


if __name__ == "__main__":
    port_arg = 18888
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port_arg = int(sys.argv[1])

    print("=== ERIS ElevenLabs Pool Web UI Server ===")
    print(f"Khởi chạy giao diện tại: http://127.0.0.1:{port_arg}")
    srv = start_elevenlabs_pool_server(port=port_arg)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nĐã dừng server.")
