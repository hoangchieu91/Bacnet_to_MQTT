/**
 * App.js — Main application logic, navigation, WebSocket, toasts.
 */

const App = (() => {
    let ws = null;
    let gatewayRunning = false;
    let mqttMessageCount = 0;
    let statusPollTimer = null;

    // ── Initialise ──────────────────────────────
    function init() {
        setupNavigation();
        connectWebSocket();
        pollStatus();
        statusPollTimer = setInterval(pollStatus, 3000);
        Dashboard.startHealthPolling();
        loadLivePointsFromMappings();
    }

    // ── Navigation ──────────────────────────────
    function setupNavigation() {
        document.querySelectorAll('.nav-item[data-page]').forEach(item => {
            item.addEventListener('click', () => {
                const page = item.dataset.page;
                navigateTo(page);
            });
        });
    }

    function navigateTo(page) {
        // Active nav
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        const navEl = document.querySelector(`[data-page="${page}"]`);
        if (navEl) navEl.classList.add('active');

        // Active page
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        const pageEl = document.getElementById(`page-${page}`);
        if (pageEl) pageEl.classList.add('active');

        // Trigger page-specific load
        if (page === 'mqtt') MqttConfig.load();
        if (page === 'system') System.refreshLogs();
        if (page === 'mappings') Mapping.load();
        if (page === 'groups' && typeof Groups !== 'undefined') Groups.load();
        if (page === 'charts' && typeof Charts !== 'undefined') Charts.load();
        if (page === 'logs' && typeof Logs !== 'undefined') Logs.loadEvents();
        if (page === 'scheduler' && typeof Scheduler !== 'undefined') Scheduler.load();
    }

    // ── WebSocket ───────────────────────────────
    function connectWebSocket() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        const url = `${proto}://${location.host}/ws`;

        ws = new WebSocket(url);

        ws.onopen = () => {
            console.log('WebSocket connected');
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleWsMessage(data);
            } catch (e) {
                console.error('WS parse error:', e);
            }
        };

        ws.onclose = () => {
            console.log('WebSocket disconnected. Reconnecting in 3s…');
            setTimeout(connectWebSocket, 3000);
        };

        ws.onerror = () => {
            ws.close();
        };
    }

    function handleWsMessage(data) {
        if (data.type === 'point_update') {
            mqttMessageCount++;
            document.getElementById('stat-mqtt-messages').textContent = mqttMessageCount;
            Dashboard.addUpdate(data);
            Mapping.updateFromWs(data);
            if (typeof Groups !== 'undefined') Groups.updateFromWs(data);
            if (typeof Charts !== 'undefined') Charts.updateFromWs(data);
        } else if (data.type === 'device_status') {
            // Device online/offline change
            const icon = data.online ? '🟢' : '🔴';
            const status = data.online ? 'online' : 'offline';
            toast(`${icon} Device ${data.device_id} is ${status}`, data.online ? 'success' : 'warning');
        } else if (data.type === 'alarm') {
            toast(`🔔 Alarm: ${data.label || data.mapping_id} — ${data.alarm_state}`, 'error');
        }
    }

    // ── Status Polling ──────────────────────────
    async function pollStatus() {
        try {
            const resp = await fetch('/api/status');
            const status = await resp.json();

            gatewayRunning = status.gateway === 'running';

            // Stats
            document.getElementById('stat-gateway-status').textContent = status.gateway.toUpperCase();
            document.getElementById('stat-active-mappings').textContent = status.active_mappings;

            // Device count from API
            document.getElementById('stat-bacnet-devices').textContent = status.discovered_devices || 0;

            // Uptime
            const uptime = Math.floor(status.uptime_seconds || 0);
            const hrs = Math.floor(uptime / 3600);
            const mins = Math.floor((uptime % 3600) / 60);
            const secs = uptime % 60;
            document.getElementById('stat-uptime').textContent =
                uptime > 0 ? `Uptime: ${hrs}h ${mins}m ${secs}s` : 'Uptime: —';

            // BACnet
            const bnDot = document.getElementById('status-dot-bacnet');
            const bnText = document.getElementById('status-text-bacnet');
            if (status.bacnet_connected) {
                bnDot.classList.add('connected');
                bnText.textContent = 'Connected';
                document.getElementById('stat-bacnet-detail').textContent = 'Connected';
            } else {
                bnDot.classList.remove('connected');
                bnText.textContent = 'Disconnected';
                document.getElementById('stat-bacnet-detail').textContent = 'Disconnected';
            }

            // MQTT
            const mqDot = document.getElementById('status-dot-mqtt');
            const mqText = document.getElementById('status-text-mqtt');
            if (status.mqtt_connected) {
                mqDot.classList.add('connected');
                mqText.textContent = 'Connected';
                document.getElementById('stat-mqtt-detail').textContent = 'Connected';
            } else {
                mqDot.classList.remove('connected');
                mqText.textContent = 'Disconnected';
                document.getElementById('stat-mqtt-detail').textContent = 'Disconnected';
            }

            // Button text
            const btn = document.getElementById('btn-start-gateway');
            if (gatewayRunning) {
                btn.textContent = '⏹ Stop Gateway';
                btn.classList.remove('btn-primary');
                btn.classList.add('btn-danger');
            } else {
                btn.textContent = '▶ Start Gateway';
                btn.classList.remove('btn-danger');
                btn.classList.add('btn-primary');
            }
        } catch (e) {
            console.error('Status poll error:', e);
        }
    }

    // ── Pre-load existing mappings into Live Points ──
    async function loadLivePointsFromMappings() {
        try {
            const resp = await fetch('/api/mappings');
            const data = await resp.json();
            const mappings = data.mappings || data || [];
            mappings.forEach(m => {
                if (!m.enabled) return;
                Dashboard.addUpdate({
                    mapping_id: m.id,
                    label: m.label || `${m.object_type}:${m.object_instance}`,
                    value: m.last_value ?? '—',
                    device_id: m.device_id,
                    object_type: m.object_type,
                    object_instance: m.object_instance,
                    read_mode: m.read_mode || 'poll',
                    timestamp: m.last_updated || new Date().toISOString(),
                });
            });
        } catch (e) {
            console.error('Load live points error:', e);
        }
    }

    // ── Gateway Toggle ──────────────────────────
    async function toggleGateway() {
        const endpoint = gatewayRunning ? '/api/gateway/stop' : '/api/gateway/start';
        try {
            const resp = await fetch(endpoint, { method: 'POST' });
            const data = await resp.json();

            if (data.error) {
                toast(data.error, 'error');
            } else {
                toast(`Gateway ${data.status}`, 'success');
            }

            await pollStatus();
        } catch (e) {
            toast('Failed to toggle gateway: ' + e.message, 'error');
        }
    }

    // ── Toast Notifications ─────────────────────
    function toast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        const el = document.createElement('div');
        el.className = `toast ${type}`;
        el.innerHTML = `<span>${message}</span>`;
        container.appendChild(el);
        setTimeout(() => {
            el.style.opacity = '0';
            el.style.transform = 'translateX(40px)';
            el.style.transition = '0.3s ease';
            setTimeout(() => el.remove(), 300);
        }, 4000);
    }

    // ── API helper ──────────────────────────────
    async function api(url, method = 'GET', body = null, timeoutMs = 30000) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
        };
        if (body) opts.body = JSON.stringify(body);
        try {
            const resp = await fetch(url, opts);
            clearTimeout(timer);
            return resp.json();
        } catch (e) {
            clearTimeout(timer);
            if (e.name === 'AbortError') throw new Error('Request timed out');
            throw e;
        }
    }

    // ── Sidebar Collapse ────────────────────────
    function toggleSidebar() {
        const sb = document.getElementById('sidebar');
        sb.classList.toggle('collapsed');
        localStorage.setItem('sidebarCollapsed', sb.classList.contains('collapsed') ? '1' : '0');
    }

    // ── Theme Toggle ────────────────────────────
    function toggleTheme() {
        const html = document.documentElement;
        const current = html.getAttribute('data-theme') || 'dark';
        const next = current === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-theme', next);
        localStorage.setItem('theme', next);
        const btn = document.getElementById('theme-toggle');
        if (btn) btn.textContent = next === 'dark' ? '🌙 Dark Mode' : '☀️ Light Mode';
    }

    // Restore sidebar/theme from localStorage
    function restorePrefs() {
        const theme = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', theme);
        const btn = document.getElementById('theme-toggle');
        if (btn) btn.textContent = theme === 'dark' ? '🌙 Dark Mode' : '☀️ Light Mode';
        if (localStorage.getItem('sidebarCollapsed') === '1') {
            document.getElementById('sidebar')?.classList.add('collapsed');
        }
    }
    // Call on load
    document.addEventListener('DOMContentLoaded', () => setTimeout(restorePrefs, 50));

    // ── Expose ──────────────────────────────────
    return { init, navigateTo, toggleGateway, toast, api, pollStatus, toggleSidebar, toggleTheme };
})();

// System namespace (logs, config export/import)
const System = (() => {
    async function refreshLogs() {
        try {
            const data = await App.api('/api/logs');
            const viewer = document.getElementById('log-viewer');
            if (data.logs && data.logs.length > 0) {
                viewer.innerHTML = data.logs.map(line => {
                    let cls = '';
                    if (line.includes('[INFO]')) cls = 'level-info';
                    else if (line.includes('[WARNING]')) cls = 'level-warning';
                    else if (line.includes('[ERROR]')) cls = 'level-error';
                    return `<div class="log-line"><span class="${cls}">${escapeHtml(line)}</span></div>`;
                }).join('');
                viewer.scrollTop = viewer.scrollHeight;
            } else {
                viewer.innerHTML = '<div class="log-line" style="color:var(--text-muted)">No logs yet.</div>';
            }
        } catch (e) {
            console.error('Log refresh error:', e);
        }
    }

    async function exportConfig() {
        try {
            const data = await App.api('/api/config/export');
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'bacnet_mqtt_config.json';
            a.click();
            URL.revokeObjectURL(url);
            App.toast('Config exported', 'success');
        } catch (e) {
            App.toast('Export failed: ' + e.message, 'error');
        }
    }

    async function importConfig(event) {
        const file = event.target.files[0];
        if (!file) return;
        try {
            const text = await file.text();
            const data = JSON.parse(text);
            await App.api('/api/config/import', 'POST', data);
            App.toast('Config imported successfully!', 'success');
            App.pollStatus();
        } catch (e) {
            App.toast('Import failed: ' + e.message, 'error');
        }
        event.target.value = '';
    }

    return { refreshLogs, exportConfig, importConfig };
})();

// ── Helper ──────────────────────────────────
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ── Boot ────────────────────────────────────
document.addEventListener('DOMContentLoaded', App.init);
