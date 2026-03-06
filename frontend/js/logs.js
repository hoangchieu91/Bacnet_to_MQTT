/**
 * Logs.js — Dedicated Event Logs page management.
 */

const Logs = (() => {
  let eventsList = [];

  async function loadEvents() {
    const container = document.getElementById('logs-container');
    if (!container) return;

    const type = document.getElementById('log-type-filter')?.value || '';
    const severity = document.getElementById('log-severity-filter')?.value || '';

    try {
      let url = '/api/events?limit=200';
      if (type) url += `&event_type=${type}`;
      if (severity) url += `&severity=${severity}`;

      const data = await App.api(url);
      eventsList = data.events || [];
      render();
    } catch (e) {
      container.innerHTML = '<div style="color:var(--error);padding:16px;text-align:center">Error loading events.</div>';
    }
  }

  function render() {
    const container = document.getElementById('logs-container');
    if (!container) return;

    updateCounts(eventsList);

    if (eventsList.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted);padding:16px;text-align:center">No events found matching criteria.</div>';
      return;
    }

    container.innerHTML = `<table style="width:100%;border-collapse:collapse">
      <thead>
        <tr style="background:var(--bg-tertiary);position:sticky;top:0;z-index:1;box-shadow:0 1px 2px rgba(0,0,0,0.1)">
          <th style="padding:8px 12px;text-align:left;width:160px">Timestamp</th>
          <th style="padding:8px 12px;text-align:left;width:180px">Event Type</th>
          <th style="padding:8px 12px;text-align:left;width:120px">Severity</th>
          <th style="padding:8px 12px;text-align:left">Message / Details</th>
        </tr>
      </thead>
      <tbody>
        ${eventsList.map(e => {
      const typeIcons = {
        device_online: '🟢', device_offline: '🔴',
        alarm: '🔔', poll_error: '⚠️', write_error: '❌'
      };
      const sevStyle = {
        info: 'color:var(--text-secondary)',
        warning: 'color:#f59e0b;font-weight:600',
        critical: 'color:var(--error);font-weight:600'
      };
      const ts = new Date(e.timestamp).toLocaleString('vi-VN');

      let details = e.message || '';
      if (e.data_json) {
        try {
          const d = JSON.parse(e.data_json);
          const badge = d.alarm_state ? `<span class="badge" style="background:var(--error);color:#fff">${d.alarm_state}</span>` : '';
          details += ' ' + badge;
        } catch (_) { }
      }

      return `<tr style="border-bottom:1px solid var(--border)">
            <td style="padding:8px 12px;color:var(--text-muted);white-space:nowrap">${ts}</td>
            <td style="padding:8px 12px;font-weight:500;">
              <span style="display:inline-block;width:20px">${typeIcons[e.event_type] || '📌'}</span> 
              ${e.event_type}
            </td>
            <td style="padding:8px 12px;${sevStyle[e.severity] || ''}">${e.severity.toUpperCase()}</td>
            <td style="padding:8px 12px">${details}</td>
          </tr>`;
    }).join('')}
      </tbody>
    </table>`;
  }

  function updateCounts(list) {
    const total = list.length;
    const warning = list.filter(e => e.severity === 'warning').length;
    const critical = list.filter(e => e.severity === 'critical').length;
    const info = list.filter(e => e.severity === 'info').length;
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('log-count-total', total);
    set('log-count-warning', warning);
    set('log-count-critical', critical);
    set('log-count-info', info);
  }

  function setQuickFilter(mode) {
    const typeSelect = document.getElementById('log-type-filter');
    const sevSelect = document.getElementById('log-severity-filter');
    if (!typeSelect || !sevSelect) return;

    if (mode === 'alarm') {
      typeSelect.value = 'alarm';
      sevSelect.value = '';
      loadEvents();
    } else if (mode === 'connection') {
      typeSelect.value = '';
      sevSelect.value = '';
      // Filter locally from cached list
      const filtered = eventsList.filter(e => e.event_type === 'device_online' || e.event_type === 'device_offline');
      const container = document.getElementById('logs-container');
      updateCounts(filtered);
      if (filtered.length === 0) {
        container.innerHTML = '<div style="color:var(--text-muted);padding:16px;text-align:center">No connection events found.</div>';
      } else {
        const prevList = eventsList;
        eventsList = filtered;
        render();
        eventsList = prevList;
      }
    }
  }

  return { loadEvents, setQuickFilter };
})();
