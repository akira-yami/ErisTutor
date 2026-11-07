document.addEventListener('DOMContentLoaded', () => {
    // Navigation
    const navItems = document.querySelectorAll('.nav-item');
    const sections = document.querySelectorAll('.view-section');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            
            // Update active nav
            navItems.forEach(nav => nav.classList.remove('active'));
            item.classList.add('active');

            // Update active section
            const targetId = item.getAttribute('data-target');
            sections.forEach(sec => sec.classList.remove('active'));
            document.getElementById(targetId).classList.add('active');
        });
    });

    // Start Dashboard polling
    pollHealth();
    setInterval(pollHealth, 3000);
});

// Toast Notifications
function showToast(message) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerText = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Setup Functions
async function checkRequirements() {
    try {
        const response = await fetch('/api/setup/requirements');
        const data = await response.json();
        
        const resDiv = document.getElementById('req-results');
        resDiv.innerHTML = '';
        
        Object.entries(data).forEach(([key, val]) => {
            const tag = document.createElement('span');
            tag.className = `req-tag ${val.ok ? 'pass' : 'fail'}`;
            tag.innerText = `${key}: ${val.version}`;
            resDiv.appendChild(tag);
        });
        
        showToast("Requirements check complete.");
    } catch (error) {
        showToast("Error checking requirements.");
    }
}

async function installDependencies() {
    try {
        const response = await fetch('/api/setup/dependencies', { method: 'POST' });
        const data = await response.json();
        showToast(data.message || "Installing dependencies...");
    } catch (error) {
        showToast("Error installing dependencies.");
    }
}

async function triggerBuild() {
    try {
        const response = await fetch('/api/system/build', { method: 'POST' });
        const data = await response.json();
        showToast(data.message || "Build started.");
    } catch (error) {
        showToast("Error starting build.");
    }
}

// Dashboard Functions
async function pollHealth() {
    // Only poll if dashboard is visible
    if (!document.getElementById('dashboard').classList.contains('active')) return;

    try {
        const response = await fetch('/api/services/health');
        const data = await response.json();
        
        const grid = document.getElementById('health-grid');
        grid.innerHTML = '';
        
        data.forEach(service => {
            const isOk = service.status === 'OK';
            const card = document.createElement('div');
            card.className = 'health-card';
            
            card.innerHTML = `
                <div class="health-header">
                    <h3>${service.name}</h3>
                    <div class="status-dot ${isOk ? 'ok' : 'down'}" title="${service.status}"></div>
                </div>
                <div class="port-label">Port: ${service.port}</div>
            `;
            grid.appendChild(card);
        });
    } catch (error) {
        console.error("Health polling failed", error);
    }
}
