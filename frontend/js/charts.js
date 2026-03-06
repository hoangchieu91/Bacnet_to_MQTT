/**
 * Charts.js — Chart configuration and history visualization.
 * Uses ApexCharts for modern, interactive charts with real-time COV updates.
 */

const Charts = (() => {
    let configs = [];
    let activeConfigId = null;
    let chartInstance = null;
    let refreshTimer = null;
    let currentRange = '1h';
    let mappings = [];

    // Color palette
    const COLORS = [
        '#8a5cf6', '#06b6d4', '#f59e0b', '#ef4444', '#22c55e',
        '#ec4899', '#3b82f6', '#14b8a6', '#f97316', '#6366f1',
    ];

    // ── Load ────────────────────────────────────
    async function load() {
        try {
            const [cfgData, mapData] = await Promise.all([
                App.api('/api/charts'),
                App.api('/api/mappings'),
            ]);
            configs = cfgData.charts || [];
            mappings = mapData.mappings || [];
            renderConfigList();
            loadHistoryStats();
            if (activeConfigId) renderChart(activeConfigId);
        } catch (e) {
            console.error('Charts load error:', e);
        }
    }

    // ── History Stats ───────────────────────────
    async function loadHistoryStats() {
        try {
            const stats = await App.api('/api/history/stats/overview');
            const el = (id) => document.getElementById(id);
            el('hist-db-size').textContent = stats.db_size_mb + ' MB';
            el('hist-db-limit').textContent = `Max: ${stats.max_db_size_mb} MB`;
            el('hist-total-records').textContent = formatNumber(stats.total_records);
            el('hist-retention').textContent = `Retention: ${stats.retention_days} days`;
            el('hist-max-per-point').textContent = formatNumber(stats.max_records_per_point);
            el('hist-oldest').textContent = stats.oldest_record
                ? new Date(stats.oldest_record).toLocaleDateString()
                : '—';
        } catch (e) {
            console.error('History stats error:', e);
        }
    }

    function formatNumber(n) {
        if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
        return String(n);
    }

    // ── Render Config List ──────────────────────
    function renderConfigList() {
        const container = document.getElementById('chart-config-list');
        if (!container) return;

        if (configs.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding:24px"><p>No charts configured.</p></div>';
            return;
        }

        container.innerHTML = configs.map(c => {
            const isActive = c.id === activeConfigId;
            const pointCount = (c.point_ids || []).length;
            const typeIcon = c.chart_type === 'bar' ? '📊' : '📈';
            return `
      <div class="mapping-item ${isActive ? 'active' : ''}" onclick="Charts.activate('${c.id}')"
           style="padding:10px 14px">
        <div style="flex:1;min-width:0">
          <div class="mi-label">${typeIcon} ${escapeHtml(c.name)}</div>
          <div class="mi-meta">
            <span>${pointCount} point${pointCount !== 1 ? 's' : ''}</span>
            <span>⏱ ${c.refresh_seconds || 10}s</span>
          </div>
        </div>
        <div style="display:flex;gap:4px">
          <button class="btn btn-sm btn-secondary" onclick="event.stopPropagation();Charts.editConfig('${c.id}')" title="Edit">✏️</button>
          <button class="btn btn-sm btn-danger" onclick="event.stopPropagation();Charts.deleteConfig('${c.id}')" title="Delete">🗑</button>
        </div>
      </div>`;
        }).join('');
    }

    // ── Activate Chart ──────────────────────────
    function activate(configId) {
        activeConfigId = configId;
        renderConfigList();
        renderChart(configId);
    }

    // ── Render Chart with ApexCharts ────────────
    async function renderChart(configId) {
        const config = configs.find(c => c.id === configId);
        if (!config) return;

        document.getElementById('active-chart-title').textContent = config.name;

        // Calculate time range
        const now = new Date();
        const start = getStartTime(now, currentRange);

        // Fetch history for each point
        const series = [];
        const pointIds = config.point_ids || [];

        for (let i = 0; i < pointIds.length; i++) {
            const pid = pointIds[i];
            try {
                const resp = await App.api(
                    `/api/history/${pid}?start=${start.toISOString()}&limit=2000`
                );
                const mapping = mappings.find(m => m.id === pid);
                const label = mapping
                    ? (mapping.label || `${mapping.object_type}:${mapping.object_instance}`)
                    : pid;

                const data = (resp.records || []).map(r => {
                    let y = r.value;
                    if (typeof y === 'string') {
                        const lv = y.toLowerCase();
                        if (lv === 'active' || lv === 'on' || lv === 'true') y = 1;
                        else if (lv === 'inactive' || lv === 'off' || lv === 'false') y = 0;
                        else y = parseFloat(y);
                    }
                    if (isNaN(y)) y = null;
                    return y !== null ? [new Date(r.timestamp).getTime(), y] : null;
                }).filter(Boolean);

                series.push({ name: label, data, _pid: pid });
            } catch (e) {
                console.error(`History fetch error for ${pid}:`, e);
            }
        }

        // Destroy existing chart
        const chartEl = document.getElementById('history-chart');
        if (chartInstance) {
            chartInstance.destroy();
            chartInstance = null;
        }

        // Determine if binary-only
        const allValues = series.flatMap(s => s.data.map(d => d[1]));
        const isBinary = allValues.length > 0 && allValues.every(v => v === 0 || v === 1);

        // ApexCharts options
        const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
        const options = {
            chart: {
                type: config.chart_type === 'bar' ? 'bar' : 'area',
                height: 350,
                fontFamily: 'Inter, system-ui, sans-serif',
                background: 'transparent',
                foreColor: isDark ? '#a0a0b8' : '#555',
                toolbar: {
                    show: true,
                    tools: { download: true, selection: true, zoom: true, zoomin: true, zoomout: true, pan: true, reset: true },
                },
                zoom: { enabled: true, type: 'x' },
                animations: {
                    enabled: true,
                    easing: 'easeinout',
                    speed: 600,
                    dynamicAnimation: { enabled: true, speed: 300 },
                },
                dropShadow: { enabled: false },
            },
            series: series.map((s, i) => ({
                name: s.name,
                data: s.data,
                color: config.color_map?.[s._pid] || COLORS[i % COLORS.length],
            })),
            stroke: {
                curve: isBinary ? 'stepline' : 'smooth',
                width: 2.5,
            },
            fill: {
                type: 'gradient',
                gradient: {
                    shadeIntensity: 1,
                    opacityFrom: 0.4,
                    opacityTo: 0.05,
                    stops: [0, 90, 100],
                },
            },
            xaxis: {
                type: 'datetime',
                labels: {
                    datetimeUTC: false,
                    style: { fontSize: '11px' },
                },
                tooltip: { enabled: false },
            },
            yaxis: {
                labels: { style: { fontSize: '11px' }, formatter: (val) => val != null ? val.toFixed(1) : '' },
                min: isBinary ? -0.1 : undefined,
                max: isBinary ? 1.5 : undefined,
                forceNiceScale: !isBinary,
            },
            tooltip: {
                theme: isDark ? 'dark' : 'light',
                x: { format: 'HH:mm:ss dd/MM' },
                y: { formatter: (val) => val != null ? val.toFixed(2) : '—' },
            },
            legend: {
                position: 'top',
                horizontalAlign: 'left',
                fontSize: '12px',
                markers: { radius: 3 },
            },
            grid: {
                borderColor: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.08)',
                strokeDashArray: 4,
            },
            dataLabels: { enabled: false },
            markers: {
                size: currentRange === '30d' ? 0 : 3,
                strokeWidth: 0,
                hover: { sizeOffset: 3 },
            },
        };

        chartInstance = new ApexCharts(chartEl, options);
        chartInstance.render();

        // Store point IDs for COV updates
        chartInstance._pointIds = pointIds;

        // Setup auto-refresh (fallback for non-COV updates)
        clearInterval(refreshTimer);
        const refreshSec = config.refresh_seconds || 30;
        refreshTimer = setInterval(() => renderChart(configId), refreshSec * 1000);
    }

    // ── Real-time COV Update ────────────────────
    function updateFromWs(msg) {
        if (msg.type !== 'point_update' || !chartInstance || !chartInstance._pointIds) return;
        const pid = msg.mapping_id;
        if (!chartInstance._pointIds.includes(pid)) return;

        // Find the series index
        const idx = chartInstance._pointIds.indexOf(pid);
        if (idx < 0) return;

        let val = msg.value;
        if (typeof val === 'string') {
            const lv = val.toLowerCase();
            if (lv === 'active' || lv === 'on' || lv === 'true') val = 1;
            else if (lv === 'inactive' || lv === 'off' || lv === 'false') val = 0;
            else val = parseFloat(val);
        }
        if (isNaN(val) || val === null) return;

        // Append new data point to the correct series
        chartInstance.appendData([{
            data: [[Date.now(), val]]
        }]);
    }

    function getStartTime(now, range) {
        const d = new Date(now);
        switch (range) {
            case '1h': d.setHours(d.getHours() - 1); break;
            case '6h': d.setHours(d.getHours() - 6); break;
            case '24h': d.setDate(d.getDate() - 1); break;
            case '7d': d.setDate(d.getDate() - 7); break;
            case '30d': d.setDate(d.getDate() - 30); break;
        }
        return d;
    }

    function setRange(range) {
        currentRange = range;
        document.querySelectorAll('.chart-range-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.range === range);
        });
        if (activeConfigId) renderChart(activeConfigId);
    }

    // ── Config CRUD ─────────────────────────────
    function openConfigModal() {
        document.getElementById('chart-modal-title').textContent = 'New Chart';
        document.getElementById('chart-name').value = '';
        document.getElementById('chart-type').value = 'line';
        document.getElementById('chart-refresh').value = '30';
        document.getElementById('chart-edit-id').value = '';
        populatePointSelector([]);
        document.getElementById('chart-config-modal').classList.remove('hidden');
    }

    function editConfig(id) {
        const c = configs.find(x => x.id === id);
        if (!c) return;
        document.getElementById('chart-modal-title').textContent = 'Edit Chart';
        document.getElementById('chart-name').value = c.name;
        document.getElementById('chart-type').value = c.chart_type || 'line';
        document.getElementById('chart-refresh').value = c.refresh_seconds || 30;
        document.getElementById('chart-edit-id').value = c.id;
        populatePointSelector(c.point_ids || []);
        document.getElementById('chart-config-modal').classList.remove('hidden');
    }

    function closeConfigModal() {
        document.getElementById('chart-config-modal').classList.add('hidden');
    }

    function populatePointSelector(selectedIds) {
        const sel = document.getElementById('chart-points');
        sel.innerHTML = mappings.map(m => {
            const label = m.label || `${m.object_type}:${m.object_instance}`;
            const selected = selectedIds.includes(m.id) ? 'selected' : '';
            return `<option value="${m.id}" ${selected}>Dev${m.device_id} — ${escapeHtml(label)}</option>`;
        }).join('');
    }

    async function saveConfig() {
        const editId = document.getElementById('chart-edit-id').value;
        const selEl = document.getElementById('chart-points');
        const pointIds = Array.from(selEl.selectedOptions).map(o => o.value);

        if (!document.getElementById('chart-name').value) {
            App.toast('Chart name required', 'warning');
            return;
        }
        if (pointIds.length === 0) {
            App.toast('Select at least 1 point', 'warning');
            return;
        }

        const payload = {
            name: document.getElementById('chart-name').value,
            point_ids: pointIds,
            chart_type: document.getElementById('chart-type').value,
            refresh_seconds: parseInt(document.getElementById('chart-refresh').value) || 30,
        };

        try {
            if (editId) {
                await App.api(`/api/charts/${editId}`, 'PUT', payload);
                App.toast('Chart updated', 'success');
            } else {
                await App.api('/api/charts', 'POST', payload);
                App.toast('Chart created', 'success');
            }
            closeConfigModal();
            await load();
        } catch (e) {
            App.toast('Save failed: ' + e.message, 'error');
        }
    }

    async function deleteConfig(id) {
        if (!confirm('Delete this chart configuration?')) return;
        try {
            await App.api(`/api/charts/${id}`, 'DELETE');
            App.toast('Chart deleted', 'success');
            if (activeConfigId === id) {
                activeConfigId = null;
                if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
                document.getElementById('active-chart-title').textContent = 'Select a chart to display';
                clearInterval(refreshTimer);
            }
            await load();
        } catch (e) {
            App.toast('Delete failed: ' + e.message, 'error');
        }
    }

    function escapeHtml(text) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }

    return {
        load, activate, setRange, updateFromWs,
        openConfigModal, editConfig, closeConfigModal, saveConfig, deleteConfig,
    };
})();
