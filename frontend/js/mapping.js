/**
 * Mapping.js — 2-column master-detail point mapping manager.
 * Left panel: searchable/filterable point list.
 * Right panel: detail view with BACnet properties + priority array.
 */

const Mapping = (() => {
  let mappings = [];
  let selectedId = null; // currently selected mapping ID
  let filterText = '';
  const selectedIds = new Set();

  const PRIORITY_NAMES = {
    1: 'Manual Life Safety', 2: 'Automatic Life Safety',
    3: 'Available (3)', 4: 'Available (4)',
    5: 'Critical Equipment', 6: 'Minimum On/Off',
    7: 'Available (7)', 8: 'Manual Operator',
    9: 'Available (9)', 10: 'Available (10)',
    11: 'Available (11)', 12: 'Available (12)',
    13: 'Available (13)', 14: 'Available (14)',
    15: 'Available (15)', 16: 'Default',
  };

  // ── Load ────────────────────────────────────
  async function load() {
    try {
      const data = await App.api('/api/mappings');
      mappings = data.mappings || [];
      populateFilters();
      renderList();
      if (selectedId) showDetail(selectedId);
    } catch (e) {
      console.error('Mapping load error:', e);
    }
  }

  function populateFilters() {
    const typeF = document.getElementById('mapping-type-filter');
    const groupF = document.getElementById('mapping-group-filter');
    if (typeF) {
      const cur = typeF.value;
      const types = [...new Set(mappings.map(m => m.object_type))].sort();
      typeF.innerHTML = '<option value="">All Types</option>' +
        types.map(t => `<option value="${t}">${shortType(t) || t}</option>`).join('');
      typeF.value = cur;
    }
    if (groupF) {
      const cur = groupF.value;
      const groups = [...new Set(mappings.map(m => m.group).filter(Boolean))].sort();
      groupF.innerHTML = '<option value="">All Groups</option>' +
        groups.map(g => `<option value="${g}">${g}</option>`).join('');
      groupF.value = cur;
    }
  }

  // ── WebSocket update ────────────────────────
  function updateFromWs(data) {
    const m = mappings.find(x => x.id === data.mapping_id);
    if (m) {
      m.last_value = data.value;
      if (data.priority_array) m.priority_array = data.priority_array;
      renderList();
      if (selectedId === m.id) showDetail(m.id);
    }
  }

  // ── Filter / Search ─────────────────────────
  function filter(text) {
    if (text !== undefined) filterText = text;
    renderList();
  }

  // ── Get filtered mappings ───────────────────
  function getFiltered() {
    const search = (filterText || document.getElementById('mapping-search')?.value || '').toLowerCase();
    const typeF = document.getElementById('mapping-type-filter')?.value || '';
    const modeF = document.getElementById('mapping-mode-filter')?.value || '';
    const groupF = document.getElementById('mapping-group-filter')?.value || '';

    return mappings.filter(m => {
      if (search && !(m.label || '').toLowerCase().includes(search) &&
        !m.object_type.toLowerCase().includes(search) &&
        !(m.group || '').toLowerCase().includes(search) &&
        !String(m.device_id).includes(search)) return false;
      if (typeF && m.object_type !== typeF) return false;
      if (modeF && (m.read_mode || 'poll') !== modeF) return false;
      if (groupF && (m.group || '') !== groupF) return false;
      return true;
    });
  }

  // ── Render Left Panel (Point List) ──────────
  function renderList() {
    const container = document.getElementById('mapping-list-container');
    const countEl = document.getElementById('mapping-count-info');
    if (!container) return;

    const filtered = getFiltered();
    if (countEl) countEl.textContent = `${filtered.length} / ${mappings.length} points`;

    if (filtered.length === 0) {
      container.innerHTML = `<div class="empty-state" style="padding:32px"><p>${mappings.length ? 'No points match filter.' : 'No mappings configured.'
        }</p></div>`;
      return;
    }

    container.innerHTML = filtered.map(m => {
      const isActive = m.id === selectedId;
      const mode = (m.read_mode || 'poll') === 'cov' ? '⚡' : '🔄';
      const val = m.last_value != null ? m.last_value : '—';
      const units = m.units || '';
      const ot = (m.object_type || '').toLowerCase();
      const typeBadge = shortType(m.object_type);
      let valHtml;
      if (ot.includes('binary')) {
        const isOn = (m.last_value === 'active' || m.last_value === 1 || String(m.last_value).toLowerCase() === 'active');
        const lbl = isOn ? (m.active_text || 'Active') : (m.inactive_text || 'Inactive');
        const clr = isOn ? '#22c55e' : '#ef4444';
        valHtml = `<span style="color:${clr};font-weight:600;font-size:0.82rem">${escapeHtml(lbl)}</span>`;
      } else if (ot.includes('multistate') || ot.includes('multi-state') || ot.includes('multi_state')) {
        const idx = parseInt(m.last_value);
        if (m.state_text && idx >= 1 && idx <= m.state_text.length) {
          valHtml = `<span style="font-size:0.82rem">${escapeHtml(m.state_text[idx - 1])}</span>`;
        } else {
          valHtml = `${val}`;
        }
      } else {
        valHtml = `${val}${units ? ' <span style="font-size:0.7rem;color:var(--text-muted)">' + units + '</span>' : ''}`;
      }

      return `
      <div class="mapping-item ${isActive ? 'active' : ''}" 
           title="${escapeHtml(m.label || m.object_type + ':' + m.object_instance)}">
        <input type="checkbox" ${selectedIds.has(m.id) ? 'checked' : ''}
          onclick="event.stopPropagation(); Mapping.toggleSelect('${m.id}', this.checked)"
          style="flex-shrink:0;margin-right:4px" />
        <div style="flex:1;min-width:0" onclick="Mapping.select('${m.id}')">
          <div class="mi-label">${escapeHtml(shortLabel(m))}</div>
          <div class="mi-meta">
            <span class="badge badge-info" style="font-size:0.65rem;padding:0px 4px">${typeBadge}</span>
            ${m.group ? `<span class="badge" style="font-size:0.6rem;padding:0px 4px;background:var(--accent-secondary);color:#fff">${escapeHtml(m.group)}</span>` : ''}
            <span>Dev ${m.device_id}</span>
            ${!m.enabled ? '<span style="color:#e74c3c">⏸</span>' : ''}
          </div>
        </div>
        <div class="mi-value">${valHtml}</div>
      </div>`;
    }).join('');
  }

  function shortType(t) {
    const map = {
      analogInput: 'AI', analogOutput: 'AO', analogValue: 'AV',
      binaryInput: 'BI', binaryOutput: 'BO', binaryValue: 'BV',
      multiStateInput: 'MSI', multiStateOutput: 'MSO', multiStateValue: 'MSV',
      'analog-input': 'AI', 'analog-output': 'AO', 'analog-value': 'AV',
      'binary-input': 'BI', 'binary-output': 'BO', 'binary-value': 'BV',
      'multi-state-input': 'MSI', 'multi-state-output': 'MSO', 'multi-state-value': 'MSV',
      device: 'DEV',
    };
    return map[t] || t;
  }

  // Extract short readable name from BACnet objectName path
  // e.g. 'Drivers.BacnetNetwork.demo.BO_01' → 'BO_01'
  function shortLabel(m) {
    const full = m.label || `${m.object_type}:${m.object_instance}`;
    // Take last segment after dots or slashes
    const parts = full.split(/[.\/\\]/);
    const short = parts[parts.length - 1] || full;
    // If short still long, try last 2 segments
    if (short.length > 24 && parts.length >= 2) {
      return parts.slice(-2).join('.');
    }
    return short;
  }

  // ── Select Point → Show Detail ──────────────
  function select(id) {
    selectedId = id;
    renderList(); // update active highlight
    showDetail(id);
  }

  function showDetail(id) {
    const m = mappings.find(x => x.id === id);
    if (!m) return;

    document.getElementById('mapping-detail-empty').classList.add('hidden');
    document.getElementById('mapping-detail-content').classList.remove('hidden');

    // Header — use short label + show full as subtitle
    const fullLabel = m.label || `${m.object_type}:${m.object_instance}`;
    document.getElementById('detail-label').textContent = shortLabel(m);
    document.getElementById('detail-subtitle').textContent =
      `${fullLabel}\nDevice ${m.device_id} • ${shortType(m.object_type)}:${m.object_instance} • MQTT: ${m.mqtt_topic || 'auto'}`;

    // Value & Mode
    const valueDisplay = formatDisplayValue(m);
    document.getElementById('detail-value').innerHTML = valueDisplay;
    document.getElementById('detail-units').textContent = m.units || '—';
    const mode = (m.read_mode || 'poll');
    document.getElementById('detail-mode').textContent = mode === 'cov' ? '⚡ COV' : '🔄 Poll';
    document.getElementById('detail-interval').textContent = `${m.poll_interval}s interval • ${m.enabled ? '✅ Enabled' : '⏸ Disabled'}`;

    // Properties table
    renderPropertiesTable(m);

    // Priority array
    renderDetailPriority(m);

    // Dynamic write controls based on object type
    renderWriteControls(m);

    // Alarm thresholds
    loadAlarmConfig(m);
  }

  function renderPropertiesTable(m) {
    const body = document.getElementById('detail-props-body');
    const props = [
      ['Description', m.description],
      ['Units', m.units],
      ['Active Text', m.active_text],
      ['Inactive Text', m.inactive_text],
      ['State Text', m.state_text ? m.state_text.join(', ') : null],
      ['Read Mode', m.read_mode || 'poll'],
      ['Poll Interval', m.poll_interval + 's'],
      ['MQTT Topic', m.mqtt_topic || 'auto'],
    ];

    const rows = props.filter(([, v]) => v != null).map(([k, v]) =>
      `<tr><td style="font-weight:500;width:120px;font-size:0.82rem;color:var(--text-secondary)">${k}</td>
           <td style="font-size:0.85rem">${escapeHtml(String(v))}</td></tr>`
    ).join('');

    body.innerHTML = rows || '<tr><td colspan="2" style="color:var(--text-muted);font-size:0.85rem">Click 📡 to load from device</td></tr>';
  }

  function renderDetailPriority(m) {
    const body = document.getElementById('detail-priority-body');
    const pa = m.priority_array || {};

    let rows = '';
    for (let i = 1; i <= 16; i++) {
      const val = pa[String(i)];
      const hasValue = val !== null && val !== undefined;
      const valDisplay = hasValue
        ? `<span style="font-weight:600;color:var(--accent-primary)">${val}</span>`
        : '<span style="color:var(--text-muted)">null</span>';
      const badgeClass = hasValue ? 'badge-success' : '';
      const releaseBtn = hasValue
        ? `<button class="btn btn-sm btn-secondary" onclick="Mapping.releasePriority(${i})" style="padding:2px 6px;font-size:0.7rem">🔓</button>`
        : '';

      rows += `<tr>
        <td><span class="badge ${badgeClass}" style="min-width:24px;justify-content:center;font-size:0.75rem">${i}</span></td>
        <td style="font-size:0.78rem">${PRIORITY_NAMES[i]}</td>
        <td>${valDisplay}</td>
        <td>${releaseBtn}</td>
      </tr>`;
    }
    body.innerHTML = rows;
  }

  // ── Load BACnet Properties ──────────────────
  async function loadProperties() {
    if (!selectedId) return;
    const m = mappings.find(x => x.id === selectedId);
    if (!m) return;

    App.toast('Loading BACnet properties…', 'info');
    try {
      const resp = await App.api(`/api/mappings/${m.id}/properties`);
      if (resp.mapping) {
        Object.assign(m, resp.mapping);
        showDetail(m.id);
        App.toast('Properties loaded', 'success');
      } else if (resp.error) {
        App.toast(resp.error, 'error');
      }
    } catch (e) {
      App.toast('Failed to load properties: ' + e.message, 'error');
    }
  }

  // ── Load All Properties (Bulk) ───────────────
  async function loadAllProperties() {
    if (!mappings.length) { App.toast('No mappings to load', 'warning'); return; }
    App.toast(`Loading properties for ${mappings.length} points…`, 'info');

    let ok = 0, fail = 0;
    for (const m of mappings) {
      try {
        const resp = await App.api(`/api/mappings/${m.id}/properties`);
        if (resp.mapping) {
          Object.assign(m, resp.mapping);
          ok++;
        } else {
          fail++;
        }
      } catch {
        fail++;
      }
    }
    renderList();
    if (selectedId) showDetail(selectedId);
    App.toast(`Properties: ${ok} loaded, ${fail} failed`, ok > 0 ? 'success' : 'error');
  }

  // ── Format display value with state text ───
  function formatDisplayValue(m) {
    const val = m.last_value;
    if (val == null) return '—';

    const ot = (m.object_type || '').toLowerCase();

    // Binary: show active/inactive text
    if (ot.includes('binary')) {
      const isActive = (val === 'active' || val === 1 || val === true || String(val).toLowerCase() === 'active');
      const stateLabel = isActive
        ? (m.active_text || 'Active')
        : (m.inactive_text || 'Inactive');
      const color = isActive ? '#22c55e' : '#ef4444';
      return `<span style="color:${color};font-weight:700">${escapeHtml(stateLabel)}</span>`;
    }

    // Multi-state: show state text if available
    if (ot.includes('multistate') || ot.includes('multi-state') || ot.includes('multi_state')) {
      const idx = parseInt(val);
      if (m.state_text && Array.isArray(m.state_text) && idx >= 1 && idx <= m.state_text.length) {
        return `<span style="font-weight:600">${escapeHtml(m.state_text[idx - 1])}</span>
                <span style="font-size:0.7rem;color:var(--text-muted)">(${idx})</span>`;
      }
      return String(val);
    }

    // Analog: show numeric value
    return String(val);
  }

  // ── Dynamic write controls by type ──────────
  function renderWriteControls(m) {
    const container = document.getElementById('detail-write-controls');
    if (!container) return;

    const ot = (m.object_type || '').toLowerCase();
    const isInput = ot.includes('input'); // Inputs are read-only

    // Priority selector (shared)
    const prioritySel = `
      <select class="form-select" id="detail-write-priority" style="width:auto;padding:6px;font-size:0.82rem">
        ${[8, 9, 10, 11, 12, 13, 14, 15, 16].map(p =>
      `<option value="${p}" ${p === 16 ? 'selected' : ''}}>P${p}</option>`
    ).join('')}
      </select>`;

    const writeBtn = `<button class="btn btn-sm btn-primary" onclick="Mapping.writeAtPriority()">✍️ Write</button>`;
    const releaseBtn = `<button class="btn btn-sm btn-danger" onclick="Mapping.releaseAllPriorities()">🔓 Release All</button>`;

    if (isInput) {
      container.innerHTML = `<div style="font-size:0.8rem;color:var(--text-muted);padding:4px">📖 Read-only input object</div>`;
      return;
    }

    // Binary: toggle switch
    if (ot.includes('binary')) {
      const isActive = (m.last_value === 'active' || m.last_value === 1 || m.last_value === true
        || String(m.last_value).toLowerCase() === 'active');
      const activeText = m.active_text || 'Active';
      const inactiveText = m.inactive_text || 'Inactive';

      container.innerHTML = `
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <div style="display:flex;border-radius:6px;overflow:hidden;border:1px solid var(--border)">
            <button class="btn btn-sm ${!isActive ? 'btn-danger' : 'btn-secondary'}" id="btn-inactive"
              onclick="document.getElementById('detail-write-value').value='inactive'" 
              style="border-radius:0;min-width:70px;font-size:0.78rem">
              ${escapeHtml(inactiveText)}
            </button>
            <button class="btn btn-sm ${isActive ? 'btn-success' : 'btn-secondary'}" id="btn-active"
              onclick="document.getElementById('detail-write-value').value='active'"
              style="border-radius:0;min-width:70px;font-size:0.78rem">
              ${escapeHtml(activeText)}
            </button>
          </div>
          <input type="hidden" id="detail-write-value" value="${isActive ? 'active' : 'inactive'}" />
          ${prioritySel} ${writeBtn} ${releaseBtn}
        </div>`;

      // Wire up button toggle
      container.querySelector('#btn-inactive').addEventListener('click', () => {
        container.querySelector('#btn-inactive').className = 'btn btn-sm btn-danger';
        container.querySelector('#btn-active').className = 'btn btn-sm btn-secondary';
      });
      container.querySelector('#btn-active').addEventListener('click', () => {
        container.querySelector('#btn-active').className = 'btn btn-sm btn-success';
        container.querySelector('#btn-inactive').className = 'btn btn-sm btn-secondary';
      });
      return;
    }

    // Multi-state: dropdown of states
    if (ot.includes('multistate') || ot.includes('multi-state') || ot.includes('multi_state')) {
      let stateOptions = '';
      if (m.state_text && Array.isArray(m.state_text) && m.state_text.length > 0) {
        stateOptions = m.state_text.map((s, i) =>
          `<option value="${i + 1}">${i + 1} — ${escapeHtml(s)}</option>`
        ).join('');
      } else {
        // Fallback: numeric 1-10
        for (let i = 1; i <= 10; i++) stateOptions += `<option value="${i}">State ${i}</option>`;
      }

      container.innerHTML = `
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <select class="form-select" id="detail-write-value" style="width:auto;padding:6px;font-size:0.82rem">
            ${stateOptions}
          </select>
          ${prioritySel} ${writeBtn} ${releaseBtn}
        </div>`;
      return;
    }

    // Analog (default): numeric input
    container.innerHTML = `
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <input type="number" class="form-input" id="detail-write-value" placeholder="Value"
          style="width:100px;padding:6px" step="any" />
        ${prioritySel} ${writeBtn} ${releaseBtn}
      </div>`;
  }

  // ── Write at Priority ───────────────────────
  async function writeAtPriority() {
    if (!selectedId) return;
    const m = mappings.find(x => x.id === selectedId);
    if (!m) return;

    const rawValue = document.getElementById('detail-write-value')?.value;
    const priority = parseInt(document.getElementById('detail-write-priority')?.value || '16');
    const ot = (m.object_type || '').toLowerCase();

    if (rawValue === '' || rawValue == null) { App.toast('Select/enter a value', 'warning'); return; }

    // Convert value based on object type
    let parsedValue;
    if (ot.includes('binary')) {
      // BACnet binary: 'active' or 'inactive'
      parsedValue = rawValue;
    } else if (ot.includes('multistate') || ot.includes('multi-state') || ot.includes('multi_state')) {
      // BACnet multi-state: integer state index
      parsedValue = parseInt(rawValue);
    } else {
      // Analog: float/int
      const num = parseFloat(rawValue);
      parsedValue = isNaN(num) ? rawValue : num;
    }

    try {
      const resp = await App.api('/api/bacnet/write', 'POST', {
        device_id: m.device_id,
        object_type: m.object_type,
        object_instance: m.object_instance,
        value: parsedValue,
        priority: priority,
      });

      if (resp.success) {
        App.toast(`Write ${parsedValue} @P${priority} — OK`, 'success');
        setTimeout(() => refreshPriorityArray(), 1000);
      } else {
        const errMsg = resp.error || resp.detail || `Write rejected @P${priority}. Check if this object is commandable and the priority level is not protected.`;
        App.toast(errMsg, 'error');
      }
    } catch (e) {
      const msg = e.message || 'Unknown error';
      App.toast('Write error: ' + msg, 'error');
    }
  }

  // ── Release priority ────────────────────────
  async function releasePriority(priority) {
    if (!selectedId) return;
    const m = mappings.find(x => x.id === selectedId);
    if (!m) return;

    try {
      const resp = await App.api('/api/bacnet/release', 'POST', {
        device_id: m.device_id, object_type: m.object_type,
        object_instance: m.object_instance, priority: priority,
      });
      if (resp.success) {
        App.toast(`Released P${priority}`, 'success');
        setTimeout(() => refreshPriorityArray(), 1000);
      }
    } catch (e) { App.toast('Release error: ' + e.message, 'error'); }
  }

  async function releaseAllPriorities() {
    if (!selectedId) return;
    const m = mappings.find(x => x.id === selectedId);
    if (!m) return;
    if (!confirm('Release ALL priorities?')) return;

    try {
      const resp = await App.api('/api/bacnet/release', 'POST', {
        device_id: m.device_id, object_type: m.object_type,
        object_instance: m.object_instance, priority: 'all',
      });
      if (resp.success) {
        App.toast(`Released ${resp.released}/16`, 'success');
        setTimeout(() => refreshPriorityArray(), 1000);
      }
    } catch (e) { App.toast('Release all error: ' + e.message, 'error'); }
  }

  async function refreshPriorityArray() {
    if (!selectedId) return;
    const m = mappings.find(x => x.id === selectedId);
    if (!m) return;

    try {
      const resp = await App.api(
        `/api/bacnet/priority_array/${m.device_id}/${m.object_type}/${m.object_instance}`
      );
      if (resp.priority_array) {
        m.priority_array = resp.priority_array;
        m.last_value = resp.present_value;
        renderList();
        showDetail(m.id);
      }
    } catch (e) { console.error('PA refresh error:', e); }
  }

  // ── Groups Helper ─────────────────────────────
  function populateGroupSelect(elementId, selectedValue = '', addKeepCurrent = false) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const configGroups = (typeof Groups !== 'undefined' && Groups.getConfigGroups) ? Groups.getConfigGroups() : [];

    let html = '';
    if (addKeepCurrent) {
      html += '<option value="">Keep current</option>';
      html += '<option value="NONE">None (Clear group)</option>';
    } else {
      html += '<option value="">None</option>';
    }

    configGroups.forEach(g => {
      html += `<option value="${escapeHtml(g.name)}">${escapeHtml(g.name)}</option>`;
    });

    el.innerHTML = html;

    // Set value if valid
    if (selectedValue) {
      // Find if value exists in options
      const exists = Array.from(el.options).some(o => o.value === selectedValue);
      if (exists) el.value = selectedValue;
      else if (!addKeepCurrent) {
        // Add custom if it was somehow created outside or before
        el.innerHTML += `<option value="${escapeHtml(selectedValue)}">${escapeHtml(selectedValue)}</option>`;
        el.value = selectedValue;
      }
    } else if (!addKeepCurrent) {
      el.value = '';
    }
  }

  // ── Add/Edit Modal ──────────────────────────
  function openAddModal() {
    document.getElementById('mapping-modal-title').textContent = 'Add Mapping';
    document.getElementById('mapping-form').reset();
    document.getElementById('map-edit-id').value = '';
    document.getElementById('map-poll-interval').value = '10';
    populateGroupSelect('map-group', '', false);
    document.getElementById('mapping-modal').classList.remove('hidden');
  }

  function closeModal() {
    document.getElementById('mapping-modal').classList.add('hidden');
  }

  function edit(id) {
    const mid = id || selectedId;
    const m = mappings.find(x => x.id === mid);
    if (!m) return;

    document.getElementById('mapping-modal-title').textContent = 'Edit Mapping';
    document.getElementById('map-edit-id').value = m.id;
    document.getElementById('map-label').value = m.label || '';
    document.getElementById('map-device-id').value = m.device_id;
    document.getElementById('map-object-type').value = m.object_type;
    document.getElementById('map-object-instance').value = m.object_instance;
    document.getElementById('map-poll-interval').value = m.poll_interval;
    document.getElementById('map-mqtt-topic').value = m.mqtt_topic || '';
    const readModeEl = document.getElementById('map-read-mode');
    if (readModeEl) readModeEl.value = m.read_mode || 'poll';
    const groupEl = document.getElementById('map-group');
    if (groupEl) populateGroupSelect('map-group', m.group, false);
    document.getElementById('mapping-modal').classList.remove('hidden');
  }

  async function save(event) {
    event.preventDefault();

    const editId = document.getElementById('map-edit-id').value;
    const payload = {
      label: document.getElementById('map-label').value,
      device_id: parseInt(document.getElementById('map-device-id').value),
      object_type: document.getElementById('map-object-type').value,
      object_instance: parseInt(document.getElementById('map-object-instance').value),
      poll_interval: parseInt(document.getElementById('map-poll-interval').value) || 10,
      mqtt_topic: document.getElementById('map-mqtt-topic').value,
      read_mode: document.getElementById('map-read-mode')?.value || 'poll',
      group: document.getElementById('map-group')?.value || '',
      enabled: true,
    };

    try {
      if (editId) {
        await App.api(`/api/mappings/${editId}`, 'PUT', payload);
        App.toast('Mapping updated', 'success');
      } else {
        await App.api('/api/mappings', 'POST', payload);
        App.toast('Mapping created', 'success');
      }
      closeModal();
      await load();
    } catch (e) { App.toast('Save failed: ' + e.message, 'error'); }
  }

  async function removeSelected() {
    if (!selectedId) return;
    if (!confirm('Remove this mapping?')) return;
    try {
      await App.api(`/api/mappings/${selectedId}`, 'DELETE');
      App.toast('Mapping deleted', 'success');
      selectedId = null;
      document.getElementById('mapping-detail-empty').classList.remove('hidden');
      document.getElementById('mapping-detail-content').classList.add('hidden');
      await load();
    } catch (e) { App.toast('Delete failed: ' + e.message, 'error'); }
  }

  async function remove(id) {
    if (!confirm('Remove this mapping?')) return;
    try {
      await App.api(`/api/mappings/${id}`, 'DELETE');
      App.toast('Mapping deleted', 'success');
      if (selectedId === id) {
        selectedId = null;
        document.getElementById('mapping-detail-empty').classList.remove('hidden');
        document.getElementById('mapping-detail-content').classList.add('hidden');
      }
      await load();
    } catch (e) { App.toast('Delete failed: ' + e.message, 'error'); }
  }

  async function toggleEnabled(id, enabled) {
    try {
      await App.api(`/api/mappings/${id}`, 'PUT', { enabled });
    } catch (e) { App.toast('Update failed: ' + e.message, 'error'); }
  }

  function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }

  // ── Export Mappings ─────────────────────────
  async function exportMappings() {
    try {
      const resp = await App.api('/api/mappings/export');
      const blob = new Blob([JSON.stringify(resp.mappings, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ts = new Date().toISOString().slice(0, 10);
      a.download = `bacnet_mappings_${ts}.json`;
      a.click();
      URL.revokeObjectURL(url);
      App.toast(`Exported ${resp.mappings.length} mappings`, 'success');
    } catch (e) {
      App.toast('Export failed: ' + e.message, 'error');
    }
  }

  // ── Import Mappings ─────────────────────────
  function importMappings() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        let data = JSON.parse(text);
        // Support both {mappings:[...]} and [...] formats
        if (Array.isArray(data)) data = { mappings: data };
        if (!data.mappings || !Array.isArray(data.mappings)) {
          App.toast('Invalid file: expected "mappings" array', 'error');
          return;
        }
        const resp = await App.api('/api/mappings/import', 'POST', data);
        App.toast(`Import: ${resp.added} added, ${resp.updated} updated, ${resp.errors} errors (total: ${resp.total})`,
          resp.errors > 0 ? 'warning' : 'success');
        await load();
      } catch (err) {
        App.toast('Import failed: ' + err.message, 'error');
      }
    };
    input.click();
  }
  // ── Selection ───────────────────────────────
  function toggleSelect(id, checked) {
    if (checked) selectedIds.add(id);
    else selectedIds.delete(id);
    // Update select-all checkbox state
    const sa = document.getElementById('mapping-select-all');
    if (sa) sa.checked = selectedIds.size === getFiltered().length && selectedIds.size > 0;
  }

  function toggleSelectAll(checked) {
    const filtered = getFiltered();
    selectedIds.clear();
    if (checked) filtered.forEach(m => selectedIds.add(m.id));
    renderList();
  }

  // ── Bulk Edit ──────────────────────────────
  function bulkEditModal() {
    if (selectedIds.size === 0) { App.toast('Select points first (use checkboxes)', 'warning'); return; }
    document.getElementById('bulk-edit-count').textContent = `${selectedIds.size} points selected`;
    document.getElementById('bulk-device-id').value = '';
    document.getElementById('bulk-read-mode').value = '';
    document.getElementById('bulk-poll-interval').value = '';
    populateGroupSelect('bulk-group', '', true);
    document.getElementById('bulk-edit-modal').classList.remove('hidden');
  }

  function closeBulkEdit() { document.getElementById('bulk-edit-modal').classList.add('hidden'); }

  async function applyBulkEdit() {
    const devId = document.getElementById('bulk-device-id').value;
    const readMode = document.getElementById('bulk-read-mode').value;
    const group = document.getElementById('bulk-group').value;
    const pollInt = document.getElementById('bulk-poll-interval').value;

    let updated = 0;
    for (const id of selectedIds) {
      const payload = {};
      if (devId) payload.device_id = parseInt(devId);
      if (readMode) payload.read_mode = readMode;
      if (group) payload.group = (group === 'NONE' ? '' : group);
      if (pollInt) payload.poll_interval = parseInt(pollInt);
      if (Object.keys(payload).length === 0) continue;
      try {
        await App.api(`/api/mappings/${id}`, 'PUT', payload);
        updated++;
      } catch (e) { console.error('Bulk edit error:', e); }
    }
    App.toast(`Updated ${updated}/${selectedIds.size} points`, 'success');
    closeBulkEdit();
    selectedIds.clear();
    await load();
  }

  // ── Clone ──────────────────────────────────
  function cloneModal() {
    if (selectedIds.size === 0) { App.toast('Select points to clone (use checkboxes)', 'warning'); return; }
    document.getElementById('clone-count').textContent = `${selectedIds.size} points selected`;
    document.getElementById('clone-device-id').value = '';
    populateGroupSelect('clone-group', '', false);
    document.getElementById('clone-prefix').value = '';
    document.getElementById('clone-modal').classList.remove('hidden');
  }

  function closeClone() { document.getElementById('clone-modal').classList.add('hidden'); }

  async function applyClone() {
    const targetDev = document.getElementById('clone-device-id').value;
    if (!targetDev) { App.toast('Target Device ID required', 'error'); return; }
    const group = document.getElementById('clone-group').value;
    const prefix = document.getElementById('clone-prefix').value;

    let created = 0;
    for (const id of selectedIds) {
      const src = mappings.find(m => m.id === id);
      if (!src) continue;
      const newLabel = prefix ? prefix + (src.label || src.object_type + '_' + src.object_instance) : src.label;
      const payload = {
        device_id: parseInt(targetDev),
        object_type: src.object_type,
        object_instance: src.object_instance,
        poll_interval: src.poll_interval,
        read_mode: src.read_mode || 'poll',
        label: newLabel || '',
        group: group || src.group || '',
        enabled: true,
      };
      try {
        await App.api('/api/mappings', 'POST', payload);
        created++;
      } catch (e) { console.error('Clone error:', e); }
    }
    App.toast(`Cloned ${created} points to device ${targetDev}`, 'success');
    closeClone();
    selectedIds.clear();
    await load();
  }

  // ── Alarm Config ──────────────────────────
  function loadAlarmConfig(m) {
    const cfg = m.alarm_config || {};
    const en = document.getElementById('alarm-enabled');
    const hi = document.getElementById('alarm-high-limit');
    const lo = document.getElementById('alarm-low-limit');
    const db = document.getElementById('alarm-deadband');
    const sv = document.getElementById('alarm-severity');
    if (en) en.checked = cfg.enabled || false;
    if (hi) hi.value = cfg.high_limit != null ? cfg.high_limit : '';
    if (lo) lo.value = cfg.low_limit != null ? cfg.low_limit : '';
    if (db) db.value = cfg.deadband != null ? cfg.deadband : 0.5;
    if (sv) sv.value = cfg.severity || 'warning';
  }

  async function saveAlarmConfig() {
    if (!selectedId) return;
    const payload = {
      enabled: document.getElementById('alarm-enabled')?.checked || false,
      high_limit: document.getElementById('alarm-high-limit')?.value ? parseFloat(document.getElementById('alarm-high-limit').value) : null,
      low_limit: document.getElementById('alarm-low-limit')?.value ? parseFloat(document.getElementById('alarm-low-limit').value) : null,
      deadband: parseFloat(document.getElementById('alarm-deadband')?.value) || 0.5,
      severity: document.getElementById('alarm-severity')?.value || 'warning',
    };
    try {
      await App.api(`/api/mappings/${selectedId}/alarm`, 'PUT', payload);
      // Update local mapping
      const m = mappings.find(x => x.id === selectedId);
      if (m) m.alarm_config = payload;
      App.toast('Alarm config saved', 'success');
    } catch (e) {
      App.toast('Error saving alarm: ' + e.message, 'error');
    }
  }

  return {
    load, filter, select, openAddModal, closeModal, edit, save,
    remove, removeSelected, toggleEnabled, loadProperties, loadAllProperties,
    writeAtPriority, releasePriority, releaseAllPriorities, updateFromWs,
    exportMappings, importMappings,
    toggleSelect, toggleSelectAll,
    bulkEditModal, applyBulkEdit, closeBulkEdit,
    cloneModal, applyClone, closeClone,
    saveAlarmConfig,
  };
})();
