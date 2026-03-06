/**
 * Groups.js — Group monitoring dashboard.
 * Shows points organized by group with live values and alarm indicators.
 */

const Groups = (() => {
  let mappings = [];
  let groups = {};
  let configGroups = []; // Array of GroupConfig objects from backend

  async function load() {
    try {
      // Load mappings
      const mappingsData = await App.api('/api/mappings');
      mappings = mappingsData.mappings || mappingsData || [];

      // Load group configs
      try {
        const groupsData = await App.api('/api/groups');
        configGroups = groupsData.groups || [];
      } catch (e) { console.warn("Could not load groups", e); }

      groups = {};
      for (const m of mappings) {
        // Determine group name based on configured groups
        let gName = 'Ungrouped';
        if (m.group) {
          const cfg = configGroups.find(g => g.id === m.group || g.name === m.group);
          gName = cfg ? cfg.name : m.group;
        }

        if (!groups[gName]) groups[gName] = [];
        groups[gName].push(m);
      }
      render();
    } catch (e) {
      console.error('Groups load error:', e);
    }
  }

  function render() {
    const container = document.getElementById('groups-container');
    if (!container) return;

    const groupNames = Object.keys(groups).sort((a, b) => {
      if (a === 'Ungrouped') return 1;
      if (b === 'Ungrouped') return -1;
      return a.localeCompare(b);
    });

    if (groupNames.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="icon">📋</div>
          <h3>No groups defined</h3>
          <p>Assign groups to points in the Mappings page to organize them here.</p>
        </div>`;
      return;
    }

    container.innerHTML = groupNames.map(name => {
      const points = groups[name];
      const onlineCount = points.filter(p => p.last_value != null).length;
      return `
      <div class="card group-card" style="padding:0;overflow:hidden;margin-bottom:10px">
        <div class="group-header" style="padding:10px 14px;display:flex;align-items:center;justify-content:space-between;
          background:var(--bg-tertiary);border-bottom:1px solid var(--border);cursor:pointer"
          onclick="Groups.toggleGroup('${name}')">
          <div style="display:flex;align-items:center;gap:8px">
            <span class="group-chevron" id="chev-${css(name)}" style="transition:transform 0.2s">▾</span>
            <span style="font-weight:600;font-size:0.9rem">${esc(name)}</span>
            <span class="badge" style="font-size:0.7rem">${points.length} pts</span>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:0.75rem;color:var(--text-secondary)">${onlineCount}/${points.length} active</span>
            ${getAlarmBadges(points)}
          </div>
        </div>
        <div id="grp-${css(name)}" class="group-body" style="max-height:600px;overflow-y:auto">
          <table style="width:100%;font-size:0.8rem;border-collapse:collapse">
            <thead>
              <tr style="background:var(--bg-secondary)">
                <th style="padding:6px 10px;text-align:left;width:30%">Point</th>
                <th style="padding:6px 10px;text-align:left">Type</th>
                <th style="padding:6px 10px;text-align:left">Device</th>
                <th style="padding:6px 10px;text-align:right">Value</th>
                <th style="padding:6px 10px;text-align:center">Mode</th>
                <th style="padding:6px 10px;text-align:right">Updated</th>
              </tr>
            </thead>
            <tbody>
              ${points.map(p => pointRow(p)).join('')}
            </tbody>
          </table>
        </div>
      </div>`;
    }).join('');
  }

  function pointRow(p) {
    const val = p.last_value != null ? p.last_value : '—';
    const valColor = val === '—' ? 'var(--text-muted)' : (isBinary(val) ? binaryColor(val) : 'var(--accent)');
    const modeIcon = p.read_mode === 'cov' ? '⚡' : '🔄';
    const age = p.last_updated ? timeSince(p.last_updated) : 'Never';
    const alarm = p.alarm_state && p.alarm_state !== 'normal' ?
      `<span style="color:var(--error);font-size:0.7rem;margin-left:4px">🔔 ${p.alarm_state}</span>` : '';
    return `
    <tr style="border-bottom:1px solid var(--border)" id="grp-row-${p.id}">
      <td style="padding:5px 10px;font-weight:500">${esc(p.label || p.object_type + ':' + p.object_instance)}${alarm}</td>
      <td style="padding:5px 10px"><span class="badge badge-type">${p.object_type}</span></td>
      <td style="padding:5px 10px;color:var(--text-secondary)">Dev ${p.device_id}</td>
      <td style="padding:5px 10px;text-align:right;font-weight:600;color:${valColor};font-family:monospace">${fmtVal(val)}</td>
      <td style="padding:5px 10px;text-align:center">${modeIcon}</td>
      <td style="padding:5px 10px;text-align:right;color:var(--text-muted);font-size:0.75rem">${age}</td>
    </tr>`;
  }

  function toggleGroup(name) {
    const body = document.getElementById('grp-' + css(name));
    const chev = document.getElementById('chev-' + css(name));
    if (!body) return;
    if (body.style.display === 'none') {
      body.style.display = '';
      if (chev) chev.style.transform = '';
    } else {
      body.style.display = 'none';
      if (chev) chev.style.transform = 'rotate(-90deg)';
    }
  }

  function updateFromWs(msg) {
    if (msg.type !== 'point_update') return;
    const row = document.getElementById('grp-row-' + msg.mapping_id);
    if (!row) return;
    // Update the value cell
    const cells = row.querySelectorAll('td');
    if (cells.length >= 4) {
      const val = msg.value != null ? msg.value : '—';
      const valColor = val === '—' ? 'var(--text-muted)' : (isBinary(val) ? binaryColor(val) : 'var(--accent)');
      cells[3].innerHTML = `<span style="font-weight:600;color:${valColor};font-family:monospace">${fmtVal(val)}</span>`;
    }
    if (cells.length >= 6) {
      cells[5].textContent = 'Just now';
    }
  }

  // ── Manage Groups Modal ─────────────────────
  function openManageModal() {
    document.getElementById('manage-groups-modal').classList.remove('hidden');
    renderManageModal();
  }

  function closeManageModal() {
    document.getElementById('manage-groups-modal').classList.add('hidden');
    document.getElementById('new-group-name').value = '';
  }

  function renderManageModal() {
    const listContainer = document.getElementById('groups-list');
    if (!listContainer) return;

    if (configGroups.length === 0) {
      listContainer.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted)">No groups created yet.</div>';
      return;
    }

    listContainer.innerHTML = configGroups.map(g => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--border)">
                <span style="font-weight:500">${esc(g.name)}</span>
                <div style="display:flex;gap:4px">
                    <button class="btn btn-sm btn-primary" onclick="Groups.openAssignModal('${g.id}','${esc(g.name)}')">📌 Gán Points</button>
                    <button class="btn btn-sm btn-error" onclick="Groups.deleteGroup('${g.id}')">🗑</button>
                </div>
            </div>
        `).join('');
  }

  async function createGroup() {
    const nameInput = document.getElementById('new-group-name');
    const name = nameInput.value.trim();
    if (!name) return;

    try {
      await App.api('/api/groups', 'POST', { id: '', name: name, description: '' });
      nameInput.value = '';
      App.toast('Group created', 'success');
      await load(); // Reload all data
      renderManageModal(); // Refresh modal
    } catch (e) {
      App.toast('Error creating group: ' + e.message, 'error');
    }
  }

  async function deleteGroup(id) {
    if (!confirm('Are you sure you want to delete this group? Points in this group will become Ungrouped.')) return;

    try {
      await App.api('/api/groups/' + id, 'DELETE');
      App.toast('Group deleted', 'success');

      // Optionally, we could clean up mappings that reference this group, but let's keep it simple: 
      // they will just show up as the raw group string if not found, or we could update mappings.

      await load();
      renderManageModal();
    } catch (e) {
      App.toast('Error deleting group: ' + e.message, 'error');
    }
  }

  function getConfigGroups() {
    return configGroups;
  }

  // Helpers
  function css(name) { return name.replace(/[^a-zA-Z0-9]/g, '_'); }
  function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
  function isBinary(v) { const s = String(v).toLowerCase(); return ['on', 'off', 'active', 'inactive', 'true', 'false', '0', '1'].includes(s); }
  function binaryColor(v) {
    const s = String(v).toLowerCase();
    return ['on', 'active', 'true', '1'].includes(s) ? 'var(--success)' : 'var(--error)';
  }
  function fmtVal(v) {
    if (v === '—') return v;
    if (typeof v === 'number') return Number.isInteger(v) ? v : v.toFixed(2);
    return String(v);
  }
  function timeSince(iso) {
    try {
      const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
      if (s < 10) return 'Now';
      if (s < 60) return s + 's';
      if (s < 3600) return Math.floor(s / 60) + 'm';
      if (s < 86400) return Math.floor(s / 3600) + 'h';
      return Math.floor(s / 86400) + 'd';
    } catch { return '—'; }
  }
  function getAlarmBadges(points) {
    const alarms = points.filter(p => p.alarm_state && p.alarm_state !== 'normal');
    if (alarms.length === 0) return '<span style="font-size:0.7rem;color:var(--success)">🟢 Normal</span>';
    return `<span style="font-size:0.7rem;color:var(--error)">🔴 ${alarms.length} alarm(s)</span>`;
  }

  // ── Assign Points Modal ─────────────────────
  let assignGroupId = null;
  let assignGroupName = '';

  function openAssignModal(groupId, groupName) {
    assignGroupId = groupId;
    assignGroupName = groupName;
    document.getElementById('assign-modal-title').textContent = `📌 Gán Points → ${groupName}`;
    document.getElementById('assign-search').value = '';
    renderAssignList('');
    document.getElementById('assign-points-modal').classList.remove('hidden');
  }

  function closeAssignModal() {
    document.getElementById('assign-points-modal').classList.add('hidden');
    assignGroupId = null;
  }

  function renderAssignList(filter) {
    const container = document.getElementById('assign-points-list');
    if (!container) return;

    const filterLower = (filter || '').toLowerCase();
    const filtered = mappings.filter(m => {
      if (!filterLower) return true;
      const label = (m.label || `${m.object_type}:${m.object_instance}`).toLowerCase();
      return label.includes(filterLower) || String(m.device_id).includes(filterLower);
    });

    if (filtered.length === 0) {
      container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted)">Không tìm thấy points.</div>';
      return;
    }

    container.innerHTML = filtered.map(m => {
      const label = m.label || `${m.object_type}:${m.object_instance}`;
      const isAssigned = m.group === assignGroupId || m.group === assignGroupName;
      return `
        <label style="display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--border);cursor:pointer;font-size:0.85rem"
          onmouseover="this.style.background='var(--bg-tertiary)'" onmouseout="this.style.background=''">
          <input type="checkbox" value="${m.id}" ${isAssigned ? 'checked' : ''}
            class="assign-point-cb">
          <div style="flex:1">
            <div style="font-weight:500">${esc(label)}</div>
            <div style="font-size:0.75rem;color:var(--text-muted)">Dev ${m.device_id} • ${m.object_type}:${m.object_instance}</div>
          </div>
          ${isAssigned ? '<span class="badge badge-success" style="font-size:0.65rem">✓ Assigned</span>' : ''}
        </label>`;
    }).join('');
  }

  function filterAssignList(q) {
    renderAssignList(q);
  }

  async function saveAssignment() {
    const checkboxes = document.querySelectorAll('.assign-point-cb');
    const checkedIds = new Set();
    checkboxes.forEach(cb => {
      if (cb.checked) checkedIds.add(cb.value);
    });

    // Update each mapping: if checked → set group, if unchecked but was this group → clear
    let updated = 0;
    for (const m of mappings) {
      const wasInGroup = m.group === assignGroupId || m.group === assignGroupName;
      const shouldBeInGroup = checkedIds.has(m.id);

      if (shouldBeInGroup && !wasInGroup) {
        try {
          await App.api(`/api/mappings/${m.id}`, 'PUT', { group: assignGroupName });
          m.group = assignGroupName;
          updated++;
        } catch (e) { console.error('Assign error:', e); }
      } else if (!shouldBeInGroup && wasInGroup) {
        try {
          await App.api(`/api/mappings/${m.id}`, 'PUT', { group: '' });
          m.group = '';
          updated++;
        } catch (e) { console.error('Unassign error:', e); }
      }
    }

    App.toast(`Đã cập nhật ${updated} points`, 'success');
    closeAssignModal();
    await load();
  }

  return {
    load, render, toggleGroup, updateFromWs,
    openManageModal, closeManageModal, createGroup, deleteGroup,
    openAssignModal, closeAssignModal, saveAssignment, filterAssignList,
    getConfigGroups
  };
})();
