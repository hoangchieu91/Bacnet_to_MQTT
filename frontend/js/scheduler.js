/**
 * Scheduler.js — Schedule management UI for timed BACnet write operations.
 */

const Scheduler = (() => {
    let schedules = [];

    async function load() {
        try {
            const data = await App.api('/api/schedules');
            schedules = data.schedules || [];
            render();
        } catch (e) {
            console.error('Failed to load schedules:', e);
        }
    }

    function render() {
        const container = document.getElementById('schedule-list');
        if (!container) return;

        if (schedules.length === 0) {
            container.innerHTML = `
                <div class="empty-state" style="padding:32px;text-align:center">
                    <p style="font-size:1.5rem;margin-bottom:8px">⏰</p>
                    <p>No schedules configured.</p>
                    <p style="color:var(--text-muted);font-size:0.85rem">Click "+ New Schedule" to add a timed operation.</p>
                </div>`;
            return;
        }

        const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

        const rows = schedules.map(s => {
            // Parse cron for display
            const parts = (s.cron || '').split('|');
            const time = parts[0] || '—';
            let days = 'Every day';
            if (parts.length > 1) {
                days = parts[1].split(',').map(d => DAY_NAMES[parseInt(d)] || d).join(', ');
            }

            const statusClass = s.enabled ? 'badge-success' : '';
            const statusText = s.enabled ? '✅ Active' : '⏸ Disabled';

            return `
                <tr>
                    <td style="font-weight:600">${escapeHtml(s.name || s.id)}</td>
                    <td>Device ${s.device_id} / ${s.object_type}:${s.object_instance}</td>
                    <td style="font-weight:600;color:var(--accent-primary)">${s.value}</td>
                    <td>P${s.priority}</td>
                    <td><code>${time}</code></td>
                    <td>${days}</td>
                    <td><span class="badge ${statusClass}">${statusText}</span></td>
                    <td>
                        <button class="btn btn-sm btn-secondary" onclick="Scheduler.edit('${s.id}')" title="Edit">✏️</button>
                        <button class="btn btn-sm btn-danger" onclick="Scheduler.remove('${s.id}')" title="Delete">🗑</button>
                    </td>
                </tr>`;
        }).join('');

        container.innerHTML = `
            <table class="data-table" style="width:100%">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Target</th>
                        <th>Value</th>
                        <th>Priority</th>
                        <th>Time</th>
                        <th>Days</th>
                        <th>Status</th>
                        <th style="width:100px"></th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    function openModal(sched = null) {
        const modal = document.getElementById('schedule-modal');
        const title = document.getElementById('schedule-modal-title');
        if (!modal) return;

        if (sched) {
            title.textContent = 'Edit Schedule';
            document.getElementById('sched-id').value = sched.id;
            document.getElementById('sched-name').value = sched.name || '';
            document.getElementById('sched-device').value = sched.device_id || '';
            document.getElementById('sched-obj-type').value = sched.object_type || 'binaryOutput';
            document.getElementById('sched-obj-instance').value = sched.object_instance || '';
            document.getElementById('sched-value').value = sched.value != null ? sched.value : '';
            document.getElementById('sched-priority').value = sched.priority || 8;
            // Parse cron
            const parts = (sched.cron || '').split('|');
            document.getElementById('sched-time').value = parts[0] || '';
            // Set day checkboxes
            const days = parts.length > 1 ? parts[1].split(',') : [];
            for (let i = 0; i < 7; i++) {
                const cb = document.getElementById(`sched-day-${i}`);
                if (cb) cb.checked = days.length === 0 || days.includes(String(i));
            }
            document.getElementById('sched-enabled').checked = sched.enabled !== false;
        } else {
            title.textContent = 'New Schedule';
            document.getElementById('sched-id').value = '';
            document.getElementById('sched-name').value = '';
            document.getElementById('sched-device').value = '';
            document.getElementById('sched-obj-type').value = 'binaryOutput';
            document.getElementById('sched-obj-instance').value = '';
            document.getElementById('sched-value').value = '';
            document.getElementById('sched-priority').value = '8';
            document.getElementById('sched-time').value = '';
            for (let i = 0; i < 7; i++) {
                const cb = document.getElementById(`sched-day-${i}`);
                if (cb) cb.checked = i < 5; // Default Mon-Fri
            }
            document.getElementById('sched-enabled').checked = true;
        }

        modal.classList.remove('hidden');
    }

    function closeModal() {
        document.getElementById('schedule-modal')?.classList.add('hidden');
    }

    function edit(id) {
        const s = schedules.find(x => x.id === id);
        if (s) openModal(s);
    }

    async function save(event) {
        event.preventDefault();

        // Build cron string
        const time = document.getElementById('sched-time').value;
        const selectedDays = [];
        for (let i = 0; i < 7; i++) {
            if (document.getElementById(`sched-day-${i}`)?.checked) {
                selectedDays.push(i);
            }
        }
        // If all days selected, omit day filter
        const cron = selectedDays.length === 7 ? time : `${time}|${selectedDays.join(',')}`;

        const payload = {
            name: document.getElementById('sched-name').value,
            device_id: parseInt(document.getElementById('sched-device').value),
            object_type: document.getElementById('sched-obj-type').value,
            object_instance: parseInt(document.getElementById('sched-obj-instance').value),
            value: parseSmartValue(document.getElementById('sched-value').value),
            priority: parseInt(document.getElementById('sched-priority').value) || 8,
            cron: cron,
            enabled: document.getElementById('sched-enabled').checked,
        };

        const id = document.getElementById('sched-id').value;
        try {
            if (id) {
                await App.api(`/api/schedules/${id}`, 'PUT', payload);
                App.toast('Schedule updated', 'success');
            } else {
                await App.api('/api/schedules', 'POST', payload);
                App.toast('Schedule created', 'success');
            }
            closeModal();
            await load();
        } catch (e) {
            App.toast('Error: ' + e.message, 'error');
        }
    }

    async function remove(id) {
        if (!confirm('Delete this schedule?')) return;
        try {
            await App.api(`/api/schedules/${id}`, 'DELETE');
            App.toast('Schedule deleted', 'success');
            await load();
        } catch (e) {
            App.toast('Error: ' + e.message, 'error');
        }
    }

    // Parse value: number, boolean, or string
    function parseSmartValue(v) {
        if (v === 'true' || v === 'active') return 1;
        if (v === 'false' || v === 'inactive') return 0;
        const n = Number(v);
        return isNaN(n) ? v : n;
    }

    return { load, openModal, closeModal, edit, save, remove };
})();
