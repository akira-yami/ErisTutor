// ==========================================================================
// Eris Account & Resource Hub — Frontend Controller
// ==========================================================================

const API_BASE = window.location.origin;

// Tab Management
const navTabs = document.querySelectorAll('.nav-tab');
const tabPanes = document.querySelectorAll('.tab-pane');
const pageTitle = document.getElementById('page-title');
const pageDesc = document.getElementById('page-desc');

const TAB_TITLES = {
    overview: { title: "Tổng quan Hệ thống", desc: "Theo dõi tình trạng tài khoản và tài nguyên AI theo thời gian thực." },
    deepseek: { title: "Tài khoản Não bộ DeepSeek", desc: "Quản lý danh sách email/password xoay vòng trên Whale2API Pool." },
    elevenlabs: { title: "Tài khoản Giọng nói ElevenLabs", desc: "Quản lý Firebase Refresh Tokens & Hạn mức ký tự TTS." },
    gemini: { title: "Gemini Web & Model 3.8", desc: "Quản lý Cookie phiên đăng nhập Google để xử lý Vision & Model 3.8." },
    secrets: { title: "⚡ Gemma & Cloud Keys Pool (.db & JSONL)", desc: "Quản lý xoay vòng API Keys trong SQLite DB, tự động hạ nhiệt khi gặp HTTP 429 và đồng bộ JSONL cho worker." },
    ports: { title: "Cổng Dịch Vụ Cục Bộ", desc: "Kiểm tra tình trạng lắng nghe của các service và gateway nội bộ." },
};

function switchTab(tabId) {
    navTabs.forEach(t => t.classList.remove('active'));
    tabPanes.forEach(p => p.classList.remove('active'));

    const tabBtn = document.getElementById(`nav-${tabId}`);
    const pane = document.getElementById(`pane-${tabId}`);

    if (tabBtn) tabBtn.classList.add('active');
    if (pane) pane.classList.add('active');

    if (TAB_TITLES[tabId]) {
        pageTitle.innerText = TAB_TITLES[tabId].title;
        pageDesc.innerText = TAB_TITLES[tabId].desc;
    }

    // Trigger tab-specific loads
    if (tabId === 'deepseek') loadDeepSeekAccounts();
    if (tabId === 'elevenlabs') loadElevenLabsAccounts();
    if (tabId === 'gemini') loadGeminiStatus();
    if (tabId === 'secrets') {
        loadCloudKeys();
        loadSecrets();
    }
    if (tabId === 'ports') loadPorts();
}

navTabs.forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        switchTab(tab);
    });
});

// Toast Notifications
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${type === 'success' ? '✓' : '⚠'}</span>
        <span>${message}</span>
    `;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// ── Overview & Readiness ───────────────────────────────────────────────────

async function loadOverview() {
    try {
        const res = await fetch(`${API_BASE}/api/overview`);
        const data = await res.json();

        // 1. Radial Progress
        const percent = data.readiness_percent || 0;
        const circle = document.getElementById('readiness-circle');
        const offset = 314 - (314 * percent) / 100;
        circle.style.strokeDashoffset = offset;
        document.getElementById('readiness-percent').innerText = `${percent}%`;

        // Radial stroke color
        if (percent >= 75) {
            circle.style.stroke = '#10b981';
        } else if (percent >= 50) {
            circle.style.stroke = '#f59e0b';
        } else {
            circle.style.stroke = '#f43f5e';
        }

        // Hero badge & title
        const badge = document.getElementById('readiness-badge');
        const title = document.getElementById('readiness-status-text');
        const detail = document.getElementById('readiness-detail-text');

        if (data.is_ready) {
            badge.className = 'badge ready';
            badge.innerText = 'Sẵn sàng';
            title.innerText = 'Eris đã sẵn sàng hoạt động!';
            detail.innerText = 'Não bộ, giọng nói và mắt quan sát đều đang trong trạng thái tối ưu.';
        } else {
            badge.className = 'badge warning';
            badge.innerText = 'Cần kiểm tra';
            title.innerText = 'Một số tài nguyên cần bổ sung';
            detail.innerText = 'Vui lòng kiểm tra các mục chưa đủ tài khoản hoặc cookie để Eris chạy tốt nhất.';
        }

        // Quick metrics
        document.getElementById('qm-deepseek').innerText = `${data.deepseek.active}/${data.deepseek.total} Acc`;
        document.getElementById('qm-eleven').innerText = `${Math.round(data.elevenlabs.quota_remaining / 1000)}k chars`;
        document.getElementById('qm-gemini').innerText = data.gemini.ready ? (data.gemini.age_hours !== null ? `${data.gemini.age_hours}h ago` : 'OK') : 'Chưa sync';
        document.getElementById('qm-groq').innerText = data.cloud_keys.groq_configured ? 'Sẵn sàng' : 'Chưa có';

        // Runtime status indicator
        const pulse = document.getElementById('runtime-pulse');
        const rLabel = document.getElementById('runtime-status-label');
        if (data.runtime_running) {
            pulse.className = 'pulse-indicator online';
            rLabel.innerText = 'Eris đang chạy';
        } else {
            pulse.className = 'pulse-indicator';
            rLabel.innerText = 'Eris đang tắt';
        }

        // DeepSeek Pillar
        document.getElementById('ds-active-count').innerText = data.deepseek.active;
        document.getElementById('ds-total-count').innerText = data.deepseek.total;
        const chipDs = document.getElementById('chip-ds');
        if (data.deepseek.ready) {
            chipDs.className = 'status-chip online';
            chipDs.innerText = 'Sẵn sàng';
            document.getElementById('ds-desc-status').innerText = 'Gateway 6011-6016 và pool accounts hoạt động tốt.';
        } else {
            chipDs.className = 'status-chip warning';
            chipDs.innerText = 'Thiếu acc';
            document.getElementById('ds-desc-status').innerText = 'Chưa có tài khoản hoạt động nào trong pool.';
        }

        // ElevenLabs Pillar
        document.getElementById('el-quota-rem').innerText = data.elevenlabs.quota_remaining.toLocaleString();
        const totalQuota = data.elevenlabs.quota_remaining + data.elevenlabs.quota_used;
        const pctEl = totalQuota > 0 ? (data.elevenlabs.quota_remaining / totalQuota) * 100 : 0;
        document.getElementById('el-quota-bar').style.width = `${pctEl}%`;
        const chipEl = document.getElementById('chip-el');
        if (data.elevenlabs.ready) {
            chipEl.className = 'status-chip online';
            chipEl.innerText = 'Sẵn sàng';
            document.getElementById('el-desc-status').innerText = `${data.elevenlabs.active} tài khoản hoạt động trong pool.`;
        } else {
            chipEl.className = 'status-chip warning';
            chipEl.innerText = 'Hết ký tự';
            document.getElementById('el-desc-status').innerText = 'Hạn mức ký tự còn lại quá thấp, cần nạp thêm token.';
        }

        // Gemini Pillar
        const chipGemini = document.getElementById('chip-gemini');
        if (data.gemini.ready) {
            chipGemini.className = 'status-chip online';
            chipGemini.innerText = 'Tốt';
            document.getElementById('gemini-freshness').innerText = `${data.gemini.age_hours}h`;
            document.getElementById('gemini-desc-status').innerText = `Lần đồng bộ cuối: ${data.gemini.last_synced}`;
        } else {
            chipGemini.className = 'status-chip offline';
            chipGemini.innerText = 'Hết hạn';
            document.getElementById('gemini-freshness').innerText = 'Hết hạn';
            document.getElementById('gemini-desc-status').innerText = 'Cookie đã cũ hoặc chưa được đồng bộ.';
        }

        // Groq Pillar
        const chipGroq = document.getElementById('chip-groq');
        if (data.cloud_keys.groq_configured) {
            chipGroq.className = 'status-chip online';
            chipGroq.innerText = 'Đã cài';
            document.getElementById('groq-key-status').innerText = 'Active';
            document.getElementById('groq-desc-status').innerText = `Key: ${data.cloud_keys.groq_masked}`;
        } else {
            chipGroq.className = 'status-chip warning';
            chipGroq.innerText = 'Trống';
            document.getElementById('groq-key-status').innerText = 'Chưa có';
            document.getElementById('groq-desc-status').innerText = 'Chưa thiết lập key dự phòng.';
        }

    } catch (err) {
        console.error("Failed to load overview:", err);
    }
}

function escapeHtml(s) {
    return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
}

// ── DeepSeek Management ────────────────────────────────────────────────────

let deepseekTestPollTimer = null;

async function loadDeepSeekAccounts() {
    const tbody = document.getElementById('ds-accounts-tbody');
    try {
        const res = await fetch(`${API_BASE}/api/deepseek/accounts`);
        const data = await res.json();
        const accounts = data.accounts || [];
        const expiredCount = data.expired_mute_count || 0;

        const activeCount = accounts.filter(a => !a.discarded).length;
        const mutedCount = accounts.filter(a => a.discarded).length;
        document.getElementById('ds-table-count').innerText = `${accounts.length} tài khoản (${activeCount} hoạt động, ${mutedCount} tạm ngưng)`;

        // Update Unmute Expired button
        const unmuteExpiredBtn = document.getElementById('btn-unmute-expired');
        const expiredBadge = document.getElementById('expired-mute-badge');
        if (unmuteExpiredBtn && expiredBadge) {
            if (expiredCount > 0) {
                unmuteExpiredBtn.style.display = 'inline-flex';
                expiredBadge.innerText = expiredCount;
            } else {
                unmuteExpiredBtn.style.display = 'none';
            }
        }

        if (accounts.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4">Chưa có tài khoản nào. Hãy dán token hoặc tài khoản vào ô bên trên!</td></tr>`;
            return;
        }

        tbody.innerHTML = accounts.map(a => {
            // Status Chip
            let statusBadge = '<span class="status-chip online">Hoạt động</span>';
            if (a.status_code === 'expired_mute') {
                statusBadge = '<span class="status-chip warning" style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4);">Hết hạn cấm</span>';
            } else if (a.status_code === 'muted') {
                statusBadge = '<span class="status-chip offline" style="background: rgba(239, 68, 68, 0.15); color: #f87171;">Tạm ngưng</span>';
            } else if (a.status_code === 'manual') {
                statusBadge = '<span class="status-chip offline">Khóa thủ công</span>';
            } else if (a.discarded) {
                statusBadge = `<span class="status-chip offline">${escapeHtml(a.status_label)}</span>`;
            }

            // Auth Type & Token
            let tokenHtml = '<span class="text-muted text-sm">—</span>';
            if (a.has_token) {
                tokenHtml = `<span class="badge" style="background: rgba(16, 185, 129, 0.15); color: #34d399;" title="User Token trực tiếp">🔑 ${escapeHtml(a.token_masked)}</span>`;
            } else if (a.auth_type === 'Password') {
                tokenHtml = `<span class="badge" style="background: rgba(148, 163, 184, 0.15); color: #94a3b8;">Mật khẩu</span>`;
            }

            // Mute Countdown
            let muteHtml = '<span class="text-muted text-sm">—</span>';
            if (a.mute_until_raw) {
                const datePart = `<div style="font-size: 0.8rem; color: #cbd5e1; font-weight: 500;">📅 ${escapeHtml(a.mute_until_formatted)}</div>`;
                let badgePart = '';
                if (a.is_mute_expired) {
                    badgePart = `<span class="badge" style="background: rgba(245, 158, 11, 0.25); color: #f59e0b; font-weight: 600; margin-top: 3px; display: inline-block;">Đã hết hạn</span>`;
                } else {
                    badgePart = `<span class="badge" style="background: rgba(99, 102, 241, 0.2); color: #a5b4fc; margin-top: 3px; display: inline-block;">⏳ ${escapeHtml(a.mute_remaining_human)}</span>`;
                }
                muteHtml = `${datePart}${badgePart}`;
            }

            // Device ID
            let deviceHtml = '<span class="text-muted text-sm">—</span>';
            if (a.has_device_id) {
                deviceHtml = `
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <code style="font-size: 0.75rem;">${escapeHtml(a.device_id_masked)}</code>
                        <button class="table-action-btn" title="Đặt lại Device ID (lấy mới ở lần ping/gọi sau để tránh fingerprint)" onclick="clearDeepSeekDeviceId(${a.id})">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                        </button>
                    </div>
                `;
            }

            // Row Actions
            const testBtn = `
                <button class="table-action-btn btn-test-single" id="ds-test-btn-${a.id}" onclick="testSingleDeepSeek('${escapeAttr(a.identifier)}', ${a.id})" title="Gửi ping probe test kiểm tra sống/chết/mute">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
                    Ping
                </button>
            `;
            let toggleBtn = '';
            if (a.discarded) {
                toggleBtn = `<button class="table-action-btn" style="color: #10b981;" onclick="unmuteDeepSeek(false, ${a.id})">Mở khóa</button>`;
            } else {
                toggleBtn = `<button class="table-action-btn" style="color: #f59e0b;" onclick="promptMuteDeepSeek(${a.id}, '${escapeAttr(a.identifier)}')">Mute</button>`;
            }
            const deleteBtn = `<button class="table-action-btn text-rose" onclick="deleteDeepSeekAccount(${a.id})">Xóa</button>`;

            return `
                <tr>
                    <td><strong>#${a.id}</strong></td>
                    <td><code>${escapeHtml(a.identifier)}</code></td>
                    <td>${tokenHtml}</td>
                    <td>${statusBadge}</td>
                    <td>${muteHtml}</td>
                    <td>${deviceHtml}</td>
                    <td>
                        <div style="display: flex; align-items: center; gap: 4px;">
                            ${testBtn}
                            ${toggleBtn}
                            ${deleteBtn}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4 text-rose">Lỗi nạp danh sách DeepSeek: ${err}</td></tr>`;
    }
}

async function testSingleDeepSeek(identifier, id) {
    const btn = document.getElementById(`ds-test-btn-${id}`);
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner-sm"></span> Đang ping...`;
    }
    try {
        const res = await fetch(`${API_BASE}/api/deepseek/test/${encodeURIComponent(identifier)}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.ok) {
            showToast(`Tài khoản ${identifier}: Hoạt động tốt (${data.message || '200 OK'})!`);
            if (btn) btn.innerHTML = `<span style="color: #10b981;">✓ Sống</span>`;
        } else if (res.ok && data.status === 'started') {
            showToast(`Đang chạy probe kiểm tra ${identifier}, hệ thống sẽ cập nhật sau giây lát...`);
            if (btn) btn.innerHTML = `<span style="color: #818cf8;">⏳ Chờ...</span>`;
        } else {
            const msg = data.detail || data.message || "Tài khoản bị mute hoặc lỗi";
            showToast(`Tài khoản ${identifier}: ${msg}`, "error");
            if (btn) btn.innerHTML = `<span style="color: #f43f5e;">✗ Lỗi</span>`;
        }
    } catch (err) {
        showToast(`Lỗi ping tài khoản: ${err}`, "error");
        if (btn) btn.innerHTML = `<span style="color: #f43f5e;">✗ Lỗi</span>`;
    }
    setTimeout(() => {
        loadDeepSeekAccounts();
        loadOverview();
    }, 1800);
}

async function testAllDeepSeekAccounts() {
    const testAllBtn = document.getElementById('btn-test-all-ds');
    const progressCard = document.getElementById('ds-test-progress-card');
    const statusLabel = document.getElementById('ds-test-job-status');
    const counterLabel = document.getElementById('ds-test-job-counter');
    const fillBar = document.getElementById('ds-test-job-fill');

    if (testAllBtn) testAllBtn.disabled = true;
    if (progressCard) progressCard.style.display = 'block';
    if (statusLabel) statusLabel.innerText = 'Đang khởi động probe...';
    if (counterLabel) counterLabel.innerText = '0 / --';
    if (fillBar) fillBar.style.width = '5%';

    try {
        const res = await fetch(`${API_BASE}/api/deepseek/test-all`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.detail || "Không thể bắt đầu kiểm tra", "error");
            if (testAllBtn) testAllBtn.disabled = false;
            if (progressCard) progressCard.style.display = 'none';
            return;
        }

        showToast("Đã khởi chạy probe kiểm tra toàn bộ pool!");

        // Poll test status until completed
        if (deepseekTestPollTimer) clearInterval(deepseekTestPollTimer);
        deepseekTestPollTimer = setInterval(async () => {
            try {
                const sRes = await fetch(`${API_BASE}/api/deepseek/test-status`);
                const sData = await sRes.json();
                const total = sData.total || 0;
                const done = sData.done || 0;
                const status = sData.status || '';

                if (total > 0) {
                    const pct = Math.round((done / total) * 100);
                    if (fillBar) fillBar.style.width = `${Math.max(pct, 5)}%`;
                    if (counterLabel) counterLabel.innerText = `${done} / ${total} (${pct}%)`;
                }

                if (status === 'completed' || status === 'offline' || (done >= total && total > 0)) {
                    clearInterval(deepseekTestPollTimer);
                    deepseekTestPollTimer = null;
                    if (statusLabel) statusLabel.innerText = 'Hoàn tất kiểm tra!';
                    if (fillBar) fillBar.style.width = '100%';
                    showToast(`Đã hoàn tất kiểm tra pool: ${sData.ok || 0} sống, ${sData.failed || 0} lỗi/mute.`);

                    setTimeout(() => {
                        if (progressCard) progressCard.style.display = 'none';
                        if (testAllBtn) testAllBtn.disabled = false;
                        loadDeepSeekAccounts();
                        loadOverview();
                    }, 2500);
                } else {
                    if (statusLabel) statusLabel.innerText = `Đang probe (${done}/${total})...`;
                }
            } catch (pErr) {
                console.error("Poll status err:", pErr);
            }
        }, 1500);

    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
        if (testAllBtn) testAllBtn.disabled = false;
        if (progressCard) progressCard.style.display = 'none';
    }
}

async function unmuteExpiredDeepSeek() {
    try {
        const res = await fetch(`${API_BASE}/api/deepseek/unmute-expired`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `Đã mở khóa ${data.unmuted_count || 0} tài khoản hết hạn!`);
            loadDeepSeekAccounts();
            loadOverview();
        } else {
            showToast(data.detail || "Mở khóa thất bại", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

function exportDeepSeekCSV() {
    window.location.href = `${API_BASE}/api/deepseek/export-csv`;
    showToast("Đang tải file CSV danh sách tài khoản DeepSeek...");
}

async function promptMuteDeepSeek(id, identifier) {
    const hoursStr = prompt(
        `Tạm ngưng (Mute) tài khoản ${identifier}:\n` +
        `• Nhập số giờ muốn mute (VD: 1, 6, 24, 72)\n` +
        `• Hoặc nhập 0 để khóa vô thời hạn (manual)\n` +
        `Mặc định: 24`,
        "24"
    );
    if (hoursStr === null) return;
    const hours = parseInt(hoursStr.trim(), 10);
    if (isNaN(hours) || hours < 0) {
        showToast("Số giờ không hợp lệ", "error");
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/deepseek/mute-custom`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: id, duration_hours: hours, reason: hours > 0 ? "muted" : "manual" }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(hours > 0 ? `Đã mute tài khoản ${identifier} trong ${hours} giờ` : `Đã khóa thủ công tài khoản ${identifier}`);
            loadDeepSeekAccounts();
            loadOverview();
        } else {
            showToast(data.detail || "Cập nhật thất bại", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function clearDeepSeekDeviceId(id) {
    try {
        const res = await fetch(`${API_BASE}/api/deepseek/clear-device-id/${id}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast("Đã đặt lại Device Token! Ở lần gọi kế tiếp hệ thống sẽ tự sinh thiết bị mới.");
            loadDeepSeekAccounts();
        } else {
            showToast(data.detail || "Không thể đặt lại Device ID", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function addDeepSeekAccounts() {
    const textarea = document.getElementById('ds-input-accounts');
    const text = textarea.value.trim();
    if (!text) {
        showToast("Vui lòng dán danh sách token hoặc email,pass", "error");
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/deepseek/accounts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accounts_text: text }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(`Đã thêm thành công ${data.added} tài khoản vào pool!`);
            textarea.value = '';
            loadDeepSeekAccounts();
            loadOverview();
        } else {
            showToast(data.detail || "Thêm tài khoản thất bại", "error");
        }
    } catch (err) {
        showToast(`Lỗi kết nối: ${err}`, "error");
    }
}

async function unmuteDeepSeek(all = false, id = null) {
    try {
        const res = await fetch(`${API_BASE}/api/deepseek/unmute`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ unmute_all: all, account_id: id }),
        });
        const data = await res.json();
        showToast(data.message || "Đã mở khóa tài khoản");
        loadDeepSeekAccounts();
        loadOverview();
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function deleteDeepSeekAccount(id) {
    if (!confirm(`Bạn có chắc muốn xóa tài khoản ID ${id}?`)) return;
    try {
        const res = await fetch(`${API_BASE}/api/deepseek/accounts/${id}`, { method: 'DELETE' });
        const data = await res.json();
        showToast(data.message || "Đã xóa");
        loadDeepSeekAccounts();
        loadOverview();
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

// ── ElevenLabs Management ──────────────────────────────────────────────────

async function loadElevenLabsAccounts() {
    const tbody = document.getElementById('el-accounts-tbody');
    try {
        const res = await fetch(`${API_BASE}/api/elevenlabs/accounts`);
        const data = await res.json();
        const accounts = data.accounts || [];

        document.getElementById('el-table-count').innerText = `${accounts.length} giọng / tokens`;

        if (accounts.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4">Chưa có giọng nào trong pool. Hãy dán chuỗi JSON hoặc token vào ô bên trên!</td></tr>`;
            return;
        }

        tbody.innerHTML = accounts.map(a => {
            const limit = a.character_limit || 10000;
            const remaining = a.remaining || 0;
            const pct = Math.round((remaining / limit) * 100);
            const barColor = pct > 40 ? 'var(--emerald)' : (pct > 15 ? 'var(--amber)' : 'var(--rose)');

            return `
                <tr>
                    <td><strong>#${a.id}</strong></td>
                    <td>
                        <div style="font-weight: 600; color: #fff;">${escapeHtml(a.label)}</div>
                        ${a.notes ? `<div class="text-muted text-sm" style="font-size: 0.72rem;">${escapeHtml(a.notes)}</div>` : ''}
                    </td>
                    <td><code>${escapeHtml(a.refresh_token_masked)}</code></td>
                    <td>
                        <div style="display: flex; justify-content: space-between; font-size: 0.8rem; margin-bottom: 3px;">
                            <strong>${remaining.toLocaleString()}</strong>
                            <span class="text-muted">/ ${limit.toLocaleString()}</span>
                        </div>
                        <div class="progress-bar-wrap" style="height: 5px;">
                            <div class="progress-bar-fill" style="width: ${pct}%; background: ${barColor};"></div>
                        </div>
                    </td>
                    <td><span style="font-size: 0.8rem;">📅 ${escapeHtml(a.reset_at_str)}</span></td>
                    <td><span class="status-chip ${a.status === 'active' ? 'online' : 'offline'}">${escapeHtml(a.status)}</span></td>
                    <td>
                        <div style="display: flex; align-items: center; gap: 4px;">
                            <button class="table-action-btn" title="Đồng bộ hạn mức tài khoản này" onclick="syncSingleElevenLabsVoice(${a.id})">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path></svg>
                                Sync
                            </button>
                            <button class="table-action-btn" title="Đổi tên giọng / nhãn tài khoản" onclick="promptRenameElevenLabsVoice(${a.id}, '${escapeAttr(a.label)}')">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                                Đổi tên
                            </button>
                            <button class="table-action-btn text-rose" onclick="deleteElevenLabsAccount(${a.id})">Xóa</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4 text-rose">Lỗi nạp token ElevenLabs: ${err}</td></tr>`;
    }
}

async function addElevenLabsTokens() {
    const textarea = document.getElementById('el-input-tokens');
    const voiceInput = document.getElementById('el-voice-name-input');
    const fbInput = document.getElementById('el-firebase-key-input');

    const text = textarea.value.trim();
    const voiceName = voiceInput ? voiceInput.value.trim() : '';
    const fbKey = fbInput ? fbInput.value.trim() : '';

    if (!text) {
        showToast("Vui lòng dán chuỗi JSON Firebase auth hoặc Refresh Token", "error");
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/elevenlabs/tokens`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tokens_text: text,
                voice_name: voiceName || null,
                firebase_api_key: fbKey || null
            }),
        });
        const data = await res.json();
        if (res.ok) {
            const parts = [];
            if (data.added > 0) parts.push(`Thêm mới ${data.added} giọng`);
            if (data.updated > 0) parts.push(`Cập nhật token cho ${data.updated} giọng`);
            const summary = parts.length > 0 ? parts.join(", ") : "Xử lý thành công";

            showToast(`${summary} & đã đồng bộ hạn mức ký tự!`);
            if (data.api_key_detected) {
                showToast("Đã tự động cập nhật Firebase Web API Key!", "success");
            }

            textarea.value = '';
            if (voiceInput) voiceInput.value = '';
            loadElevenLabsAccounts();
            loadOverview();
        } else {
            showToast(data.detail || "Thêm giọng thất bại", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function promptRenameElevenLabsVoice(id, currentName) {
    const newName = prompt(`Nhập tên giọng / nhãn mới cho tài khoản #${id}:`, currentName);
    if (!newName || !newName.trim() || newName.trim() === currentName) return;

    try {
        const res = await fetch(`${API_BASE}/api/elevenlabs/rename`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: id, new_name: newName.trim() }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `Đã đổi tên giọng thành "${newName.trim()}"`);
            loadElevenLabsAccounts();
        } else {
            showToast(data.detail || "Đổi tên thất bại", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function syncSingleElevenLabsVoice(id) {
    showToast(`Đang đồng bộ hạn mức tài khoản #${id}...`);
    try {
        const res = await fetch(`${API_BASE}/api/elevenlabs/sync/${id}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Đã đồng bộ hạn mức thành công!");
            loadElevenLabsAccounts();
            loadOverview();
        } else {
            showToast(data.detail || "Đồng bộ thất bại", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function syncElevenLabsQuotas() {
    showToast("Đang đồng bộ toàn bộ hạn mức từ ElevenLabs...");
    try {
        const res = await fetch(`${API_BASE}/api/elevenlabs/sync`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Đã đồng bộ thành công!");
            loadElevenLabsAccounts();
            loadOverview();
        } else {
            showToast(data.detail || "Đồng bộ thất bại", "error");
        }
    } catch (err) {
        showToast(`Lỗi đồng bộ: ${err}`, "error");
    }
}


async function deleteElevenLabsAccount(id) {
    if (!confirm(`Bạn có chắc muốn xóa token ID ${id}?`)) return;
    try {
        const res = await fetch(`${API_BASE}/api/elevenlabs/accounts/${id}`, { method: 'DELETE' });
        const data = await res.json();
        showToast(data.message || "Đã xóa");
        loadElevenLabsAccounts();
        loadOverview();
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

// ── Gemini Management ──────────────────────────────────────────────────────

async function loadGeminiStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/gemini/status`);
        const data = await res.json();

        const statusEl = document.getElementById('g-cred-status');
        const iconEl = document.getElementById('g-status-icon');

        if (data.exists && !data.is_stale) {
            statusEl.innerText = `Cookie khả dụng (${data.age_hours} giờ trước)`;
            statusEl.style.color = '#34d399';
            iconEl.style.borderColor = '#10b981';
        } else if (data.exists && data.is_stale) {
            statusEl.innerText = `Cookie đã cũ (${data.age_hours} giờ trước) - Cần đồng bộ lại`;
            statusEl.style.color = '#fbbf24';
            iconEl.style.borderColor = '#f59e0b';
        } else {
            statusEl.innerText = 'Chưa có Cookie đăng nhập';
            statusEl.style.color = '#fb7185';
            iconEl.style.borderColor = '#f43f5e';
        }

        document.getElementById('g-psid-display').innerText = data.psid_masked || 'None';
        document.getElementById('g-psidts-display').innerText = data.psidts_masked || 'None';
        document.getElementById('g-last-synced').innerText = data.last_synced;
        document.getElementById('g-docker-status').innerText = data.docker_running ? '🟢 Online (Port 8000)' : '⚪ Chưa bật';
    } catch (err) {
        console.error("Failed to load Gemini status:", err);
    }
}

async function pasteGeminiCookie() {
    const raw = document.getElementById('gemini-raw-cookie').value.trim();
    if (!raw) {
        showToast("Vui lòng dán chuỗi cookie hợp lệ", "error");
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/cookies`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ raw_cookie: raw }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast("Đã đồng bộ cookie Gemini thành công!");
            document.getElementById('gemini-raw-cookie').value = '';
            loadGeminiStatus();
            loadOverview();
        } else {
            showToast(data.detail || "Lỗi đồng bộ cookie", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function launchTutor() {
    try {
        const res = await fetch(`${API_BASE}/api/gemini/launch-tutor`, { method: 'POST' });
        const data = await res.json();
        showToast(data.message || "Đã khởi động cửa sổ Google Tutor");
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

// ── Gemma & Cloud Keys Pool Management ─────────────────────────────────────

async function loadCloudKeys() {
    const geminiListEl = document.getElementById('gemini-keys-list');
    const groqListEl = document.getElementById('groq-keys-list');
    const geminiCounter = document.getElementById('gemini-pool-counter');
    const groqCounter = document.getElementById('groq-pool-counter');

    try {
        const res = await fetch(`${API_BASE}/api/cloud-keys`);
        const data = await res.json();

        const geminiKeys = data.gemini_keys || [];
        const groqKeys = data.groq_keys || [];

        if (geminiCounter) geminiCounter.innerText = `${geminiKeys.length} keys`;
        if (groqCounter) groqCounter.innerText = `${groqKeys.length} keys`;

        renderCloudKeyList(geminiListEl, geminiKeys, 'gemini');
        renderCloudKeyList(groqListEl, groqKeys, 'groq');
    } catch (err) {
        if (geminiListEl) geminiListEl.innerHTML = `<div class="empty-state text-rose">Lỗi nạp danh sách key: ${err}</div>`;
        if (groqListEl) groqListEl.innerHTML = `<div class="empty-state text-rose">Lỗi nạp danh sách key: ${err}</div>`;
    }
}

function renderCloudKeyList(containerEl, keys, provider) {
    if (!containerEl) return;
    if (keys.length === 0) {
        containerEl.innerHTML = `
            <div class="empty-state">
                Chưa có key nào trong pool ${provider === 'gemini' ? 'Google AI Studio (Gemma)' : 'Groq'}.<br>
                Bấm <strong>"+ Thêm Key / Nạp Hàng Loạt"</strong> ở trên để nạp vào SQLite Database!
            </div>
        `;
        return;
    }

    containerEl.innerHTML = `
        <div class="cloud-keys-grid">
            ${keys.map(k => {
                let statusBadge = '';
                if (k.status === 'cooling') {
                    statusBadge = `<span class="badge badge-cooling">🟡 Hạ nhiệt (${k.cooldown_remaining_s}s)</span>`;
                } else if (k.status === 'active') {
                    statusBadge = `<span class="badge badge-active">🟢 Hoạt động</span>`;
                } else if (k.status === 'disabled') {
                    statusBadge = `<span class="badge badge-disabled">⚪ Tạm dừng</span>`;
                } else {
                    statusBadge = `<span class="badge badge-exhausted">🔴 Hết Quota</span>`;
                }

                const latencyBadge = k.latency_ms > 0
                    ? `<span style="font-size: 0.76rem; color: #34d399; font-weight: 600;">⚡ ${k.latency_ms}ms</span>`
                    : `<span style="font-size: 0.76rem; color: var(--text-muted);">Chưa đo ping</span>`;

                const modelsList = k.models_supported && k.models_supported.length > 0
                    ? `<div class="cloud-key-models">
                        ${k.models_supported.map(m => `<span class="model-tag">${escapeHtml(m)}</span>`).join('')}
                       </div>`
                    : '';

                return `
                    <div class="cloud-key-card" id="cloud-key-${k.id}">
                        <div class="cloud-key-meta">
                            <div class="cloud-key-topline">
                                <span class="cloud-key-label">#${k.id} ${escapeHtml(k.label)}</span>
                                <code class="cloud-key-masked">${escapeHtml(k.api_key_masked)}</code>
                                ${statusBadge}
                                ${latencyBadge}
                            </div>
                            <div class="cloud-key-subline">
                                <span>Lượt dùng: <strong>${k.usage_count}</strong></span>
                                <span>Lỗi: <strong>${k.error_count}</strong></span>
                                <span>Dùng lần cuối: ${escapeHtml(k.last_used_at_iso)}</span>
                            </div>
                            ${modelsList}
                        </div>
                        <div class="cloud-key-actions">
                            <button class="btn btn-sm btn-test-single" onclick="testCloudKey(${k.id})">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                                Test & Quét
                            </button>
                            <button class="table-action-btn" onclick="toggleCloudKeyStatus(${k.id}, '${k.status}')">
                                ${k.status === 'disabled' ? 'Kích hoạt' : 'Tạm dừng'}
                            </button>
                            <button class="table-action-btn text-rose" onclick="deleteCloudKey(${k.id})">
                                Xoá
                            </button>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function openAddCloudKeyModal() {
    const modal = document.getElementById('modal-cloud-key');
    if (modal) modal.classList.add('active');
}

function closeAddCloudKeyModal() {
    const modal = document.getElementById('modal-cloud-key');
    if (modal) modal.classList.remove('active');
    document.getElementById('modal-key-input').value = '';
    document.getElementById('modal-key-label').value = '';
}

async function submitAddCloudKey() {
    const provider = document.getElementById('modal-key-provider').value;
    const label = document.getElementById('modal-key-label').value.trim();
    const rawInput = document.getElementById('modal-key-input').value.trim();

    if (!rawInput) {
        showToast("Vui lòng nhập API Key hoặc dán danh sách hàng loạt", "error");
        return;
    }

    const lines = rawInput.split('\n').map(l => l.trim()).filter(Boolean);

    try {
        let payload = { provider, label };
        if (lines.length > 1) {
            payload.batch_text = rawInput;
        } else {
            payload.api_key = lines[0];
        }

        const res = await fetch(`${API_BASE}/api/cloud-keys`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Đã lưu vào SQLite Database thành công!");
            closeAddCloudKeyModal();
            loadCloudKeys();
            loadSecrets();
            loadOverview();
        } else {
            showToast(data.detail || "Lỗi lưu key", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function testCloudKey(keyId) {
    const card = document.getElementById(`cloud-key-${keyId}`);
    if (card) {
        const btn = card.querySelector('.btn-test-single');
        if (btn) btn.innerHTML = `<span class="spinner-sm"></span> Đang ping...`;
    }

    try {
        const res = await fetch(`${API_BASE}/api/cloud-keys/${keyId}/test`, { method: 'POST' });
        const data = await res.json();
        if (data.ok) {
            let msg = `✓ Ping thành công (${data.latency_ms}ms)!`;
            if (data.gemma_models && data.gemma_models.length) {
                msg += ` Model: ${data.gemma_models.slice(0, 3).join(', ')}`;
            }
            showToast(msg);
        } else {
            showToast(`✕ Lỗi: ${data.error || 'Test thất bại'}`, "error");
        }
        loadCloudKeys();
    } catch (err) {
        showToast(`Lỗi test: ${err}`, "error");
        loadCloudKeys();
    }
}

async function toggleCloudKeyStatus(keyId, currentStatus) {
    const newStatus = currentStatus === 'disabled' ? 'active' : 'disabled';
    try {
        const res = await fetch(`${API_BASE}/api/cloud-keys/${keyId}/status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus }),
        });
        const data = await res.json();
        showToast(data.message || "Đã cập nhật trạng thái");
        loadCloudKeys();
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function deleteCloudKey(keyId) {
    if (!confirm(`Bạn có chắc muốn xoá Key #${keyId} khỏi SQLite Database?`)) return;
    try {
        const res = await fetch(`${API_BASE}/api/cloud-keys/${keyId}`, { method: 'DELETE' });
        const data = await res.json();
        showToast(data.message || "Đã xoá key");
        loadCloudKeys();
        loadSecrets();
        loadOverview();
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function syncCloudKeys() {
    try {
        const res = await fetch(`${API_BASE}/api/cloud-keys/sync`, { method: 'POST' });
        const data = await res.json();
        showToast(data.message || "Đã đồng bộ xoay vòng vào config/secrets.json & config/cloud_keys.jsonl");
    } catch (err) {
        showToast(`Lỗi đồng bộ: ${err}`, "error");
    }
}

function exportAndDownloadJsonl() {
    window.open(`${API_BASE}/api/cloud-keys/export-jsonl`, '_blank');
}

// ── Secondary Secrets Management (Firebase & DeepSeek Official) ───────────

async function loadSecrets() {
    try {
        const res = await fetch(`${API_BASE}/api/secrets`);
        const data = await res.json();
        const fbEl = document.getElementById('sec-firebase-current');
        const dsEl = document.getElementById('sec-deepseek-current');
        if (fbEl) fbEl.innerText = data.firebase_key_masked || 'Chưa cài';
        if (dsEl) dsEl.innerText = data.deepseek_key_masked || 'Chưa cài';
    } catch (err) {
        console.error("Failed to load secondary secrets:", err);
    }
}

async function saveSecondarySecrets() {
    const firebase = document.getElementById('input-firebase-key').value.trim();
    const deepseek = document.getElementById('input-deepseek-key').value.trim();

    try {
        const res = await fetch(`${API_BASE}/api/secrets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                firebase_key: firebase || null,
                deepseek_key: deepseek || null,
            }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast("Đã lưu cấu hình phụ trợ vào config/secrets.json");
            document.getElementById('input-firebase-key').value = '';
            document.getElementById('input-deepseek-key').value = '';
            loadSecrets();
            loadOverview();
        } else {
            showToast(data.detail || "Lưu thất bại", "error");
        }
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

// ── Ports Monitor ──────────────────────────────────────────────────────────

async function loadPorts() {
    const container = document.getElementById('ports-grid-container');
    try {
        const res = await fetch(`${API_BASE}/api/system/ports`);
        const ports = await res.json();

        container.innerHTML = ports.map(p => `
            <div class="port-item-card">
                <div class="port-info">
                    <h4>${p.name}</h4>
                    <span>127.0.0.1:${p.port}</span>
                </div>
                <div class="port-badge ${p.listening ? 'online' : 'offline'}">
                    <span class="port-dot"></span>
                    <span>${p.status}</span>
                </div>
            </div>
        `).join('');
    } catch (err) {
        container.innerHTML = `<div class="text-rose py-4">Lỗi quét ports: ${err}</div>`;
    }
}

// ── Eris Controls ──────────────────────────────────────────────────────────

async function startEris() {
    showToast("Đang gửi lệnh khởi động Eris...");
    try {
        const res = await fetch(`${API_BASE}/api/system/start-eris`, { method: 'POST' });
        const data = await res.json();
        showToast(data.message || "Đã gửi lệnh khởi động Eris");
        setTimeout(loadOverview, 2000);
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

async function stopEris() {
    if (!confirm("Bạn có chắc muốn dừng các tiến trình Eris đang chạy?")) return;
    try {
        const res = await fetch(`${API_BASE}/api/system/stop-eris`, { method: 'POST' });
        const data = await res.json();
        showToast(data.message || "Đã dừng Eris");
        setTimeout(loadOverview, 1500);
    } catch (err) {
        showToast(`Lỗi: ${err}`, "error");
    }
}

// ── Initialization ─────────────────────────────────────────────────────────

function loadAllData() {
    loadOverview();
}

window.addEventListener('DOMContentLoaded', () => {
    loadOverview();
    // Poll every 15 seconds
    setInterval(loadOverview, 15000);
});
