/**
 * Dashboard.js — Real-time chart, Pi health monitoring, and live point viewer.
 */

const Dashboard = (() => {
    let chart = null;
    const MAX_POINTS = 40;
    const datasets = {};
    const livePoints = {};        // { mapping_id: { label, value, mode, updated, ... } }
    let liveFilterText = '';
    let healthInterval = null;

    function initChart() {
        const el = document.getElementById('liveChart');
        if (!el) return;

        const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
        chart = new ApexCharts(el, {
            chart: {
                type: 'area',
                height: 260,
                fontFamily: 'Inter, system-ui, sans-serif',
                background: 'transparent',
                foreColor: isDark ? '#94a3b8' : '#555',
                toolbar: { show: false },
                animations: {
                    enabled: true,
                    easing: 'easeinout',
                    speed: 400,
                    dynamicAnimation: { enabled: true, speed: 250 },
                },
                zoom: { enabled: false },
            },
            series: [],
            stroke: { curve: 'smooth', width: 2 },
            fill: {
                type: 'gradient',
                gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0.05, stops: [0, 90, 100] },
            },
            xaxis: {
                type: 'category',
                labels: { style: { fontSize: '10px' }, rotate: -45, rotateAlways: false },
                tickAmount: 8,
            },
            yaxis: {
                labels: { style: { fontSize: '10px' }, formatter: (v) => v != null ? v.toFixed(1) : '' },
                forceNiceScale: true,
            },
            legend: {
                position: 'top',
                fontSize: '11px',
                markers: { radius: 3, width: 10, height: 10 },
            },
            grid: { borderColor: isDark ? 'rgba(148,163,184,0.06)' : 'rgba(0,0,0,0.08)', strokeDashArray: 4 },
            tooltip: { theme: isDark ? 'dark' : 'light' },
            dataLabels: { enabled: false },
            markers: { size: 0 },
        });
        chart.render();
    }

    const COLORS = [
        '#3b82f6', '#8b5cf6', '#22c55e', '#f59e0b',
        '#ef4444', '#06b6d4', '#ec4899', '#14b8a6',
    ];

    // ── WebSocket point update handler ─────────
    function addUpdate(data) {
        if (chart === null) initChart();
        if (chart === null) return;

        const id = data.mapping_id;
        const label = data.label || `${data.object_type}:${data.object_instance}`;
        const value = parseFloat(data.value);
        const time = new Date(data.timestamp).toLocaleTimeString();

        // Add to chart if numeric
        if (!isNaN(value)) {
            if (!datasets[id]) {
                const colorIdx = Object.keys(datasets).length % COLORS.length;
                datasets[id] = {
                    name: label,
                    data: [],
                    color: COLORS[colorIdx],
                };
            }

            const ds = datasets[id];
            ds.data.push(value);
            if (ds.data.length > MAX_POINTS) ds.data.shift();

            // Rebuild series for ApexCharts
            const seriesArr = Object.values(datasets).map(d => ({
                name: d.name,
                data: d.data,
                color: d.color,
            }));

            // Get labels (use latest dataset length)
            const maxLen = Math.max(...seriesArr.map(s => s.data.length));
            const labels = [];
            for (let i = 0; i < maxLen; i++) labels.push('');
            // Only set last few labels
            if (!chart._labels) chart._labels = [];
            chart._labels.push(time);
            if (chart._labels.length > MAX_POINTS) chart._labels.shift();

            chart.updateOptions({
                xaxis: { categories: chart._labels },
            }, false, false);
            chart.updateSeries(seriesArr, false);
        }

        // Update live points
        livePoints[id] = {
            label: label,
            value: data.value,
            mode: data.read_mode || 'poll',
            updated: time,
            device_id: data.device_id,
            object_type: data.object_type,
            object_instance: data.object_instance,
        };
        renderLivePoints();
        renderGroupSummary();
    }

    // ── Live Points Table ─────────────────────
    function renderLivePoints() {
        const body = document.getElementById('live-points-body');
        if (!body) return;

        const entries = Object.values(livePoints);
        const filtered = liveFilterText
            ? entries.filter(p => p.label.toLowerCase().includes(liveFilterText))
            : entries;

        if (filtered.length === 0) {
            body.innerHTML = `
        <tr>
          <td colspan="4" class="empty-state" style="padding:24px">
            <p>${entries.length === 0 ? 'No data yet. Start the gateway to begin polling.' : 'No points match filter.'}</p>
          </td>
        </tr>`;
            return;
        }

        body.innerHTML = filtered.map(p => {
            const modeIcon = p.mode === 'cov' ? '⚡ COV' : '🔄 Poll';
            const modeBadge = p.mode === 'cov'
                ? '<span class="badge badge-success" style="font-size:0.72rem">⚡ COV</span>'
                : '<span class="badge badge-info" style="font-size:0.72rem">🔄 Poll</span>';
            return `
        <tr>
          <td title="Device ${p.device_id} / ${p.object_type}:${p.object_instance}">${escapeHtml(p.label)}</td>
          <td><span class="value-live" style="font-weight:600">${p.value ?? '—'}</span></td>
          <td>${modeBadge}</td>
          <td style="color:var(--text-muted);font-size:0.82rem">${p.updated}</td>
        </tr>`;
        }).join('');

        const subtitle = document.getElementById('live-points-subtitle');
        if (subtitle) {
            subtitle.textContent = `${entries.length} points — ${filtered.length} shown`;
        }
    }

    function filterLivePoints(text) {
        liveFilterText = (text || '').toLowerCase();
        renderLivePoints();
    }

    // ── Pi Health Polling ─────────────────────
    async function pollHealth() {
        try {
            const h = await App.api('/api/health');

            // CPU
            const cpuBar = document.getElementById('health-cpu-bar');
            const cpuPct = document.getElementById('health-cpu-pct');
            if (cpuBar && cpuPct) {
                cpuBar.style.width = `${h.cpu_percent}%`;
                cpuPct.textContent = `${h.cpu_percent}%`;
                cpuBar.className = `progress-fill ${h.cpu_percent > 90 ? 'danger' : h.cpu_percent > 70 ? 'warn' : ''}`;
            }

            // RAM
            const ramBar = document.getElementById('health-ram-bar');
            const ramPct = document.getElementById('health-ram-pct');
            if (ramBar && ramPct) {
                ramBar.style.width = `${h.ram_percent}%`;
                ramPct.textContent = `${h.ram_percent}% (${h.ram_used_mb}/${h.ram_total_mb} MB)`;
                ramBar.className = `progress-fill ${h.ram_status === 'critical' || h.ram_status === 'throttle' ? 'danger' : h.ram_status === 'warn' ? 'warn' : ''}`;
            }

            // RAM badge
            const badge = document.getElementById('health-ram-badge');
            if (badge) {
                const statusMap = {
                    normal: { text: '✅ Normal', cls: 'badge-success' },
                    warn: { text: '🟡 Warning', cls: 'badge-warning' },
                    throttle: { text: '🟠 Throttled', cls: 'badge-warning' },
                    critical: { text: '🔴 Critical', cls: 'badge-danger' },
                };
                const s = statusMap[h.ram_status] || statusMap.normal;
                badge.textContent = s.text;
                badge.className = `badge ${s.cls}`;
            }

            // Disk
            const diskBar = document.getElementById('health-disk-bar');
            const diskPct = document.getElementById('health-disk-pct');
            if (diskBar && diskPct) {
                diskBar.style.width = `${h.disk_percent}%`;
                diskPct.textContent = `${h.disk_percent}% (${h.disk_used_gb}/${h.disk_total_gb} GB)`;
                diskBar.className = `progress-fill ${h.disk_percent > 90 ? 'danger' : h.disk_percent > 80 ? 'warn' : ''}`;
            }

            // Temperature
            const temp = document.getElementById('health-temp');
            const tempBig = document.getElementById('health-temp-big');
            if (temp && tempBig) {
                const t = h.cpu_temp;
                if (t !== null && t !== undefined) {
                    temp.textContent = '';
                    tempBig.textContent = `${t}°C`;
                    tempBig.style.color = t > 80 ? '#e74c3c' : t > 65 ? '#f0ad4e' : 'var(--accent-primary)';
                } else {
                    temp.textContent = 'N/A';
                    tempBig.textContent = '—';
                }
            }

            // Subtitle
            const subtitle = document.getElementById('health-subtitle');
            if (subtitle) {
                subtitle.textContent = `Load: ${h.load_avg_1m} / ${h.load_avg_5m} / ${h.load_avg_15m} — RAM available: ${h.ram_available_mb} MB`;
            }

        } catch (e) {
            console.error('Health poll error:', e);
        }
    }

    function startHealthPolling() {
        pollHealth();  // immediate
        if (healthInterval) clearInterval(healthInterval);
        healthInterval = setInterval(pollHealth, 5000);
    }

    function stopHealthPolling() {
        if (healthInterval) {
            clearInterval(healthInterval);
            healthInterval = null;
        }
    }

    // ── Group Summary Cards ───────────────────
    function renderGroupSummary() {
        const container = document.getElementById('group-summary-container');
        if (!container) return;

        // Need mappings info for groups
        const entries = Object.values(livePoints);
        if (entries.length === 0) {
            container.innerHTML = '';
            return;
        }

        // Group by group name using data from Mapping module if available
        const groups = {};
        let mappings = [];
        try { mappings = typeof Mapping !== 'undefined' ? (Mapping._mappings || []) : []; } catch (e) { }

        // Fallback: try to fetch from livePoints device grouping
        entries.forEach(pt => {
            // Find mapping for this point to get group
            const m = mappings.find(x => (x.label || `${x.object_type}:${x.object_instance}`) === pt.label);
            const groupName = (m && m.group) || 'Ungrouped';
            if (!groups[groupName]) groups[groupName] = { values: [], count: 0 };
            groups[groupName].count++;
            const numVal = parseFloat(pt.value);
            if (!isNaN(numVal)) groups[groupName].values.push(numVal);
        });

        const groupNames = Object.keys(groups).filter(g => g !== 'Ungrouped');
        if (groupNames.length === 0) {
            container.innerHTML = '';
            return;
        }

        container.innerHTML = groupNames.map(name => {
            const g = groups[name];
            const vals = g.values;
            const avg = vals.length ? (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1) : '—';
            const min = vals.length ? Math.min(...vals).toFixed(1) : '—';
            const max = vals.length ? Math.max(...vals).toFixed(1) : '—';

            return `
                <div class="card" style="flex:1;min-width:180px;max-width:280px;padding:12px">
                    <div style="font-weight:600;font-size:0.9rem;margin-bottom:6px;color:var(--accent-primary)">📁 ${name}</div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:0.78rem">
                        <div><span style="color:var(--text-muted)">Points:</span> <b>${g.count}</b></div>
                        <div><span style="color:var(--text-muted)">Avg:</span> <b>${avg}</b></div>
                        <div><span style="color:var(--text-muted)">Min:</span> <b>${min}</b></div>
                        <div><span style="color:var(--text-muted)">Max:</span> <b>${max}</b></div>
                    </div>
                </div>`;
        }).join('');
    }

    return {
        addUpdate, initChart, filterLivePoints,
        pollHealth, startHealthPolling, stopHealthPolling,
        renderGroupSummary,
    };
})();
