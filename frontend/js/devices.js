/**
 * Devices.js — BACnet device discovery (multi-mode), auto-map object browser.
 */

const Devices = (() => {
    let deviceList = [];
    let currentDeviceId = null;
    let allObjects = [];        // full list from device
    let selectedObjects = {};   // { 'type:instance': true }

    // ── BACnet Config ─────────────────────────────

    async function loadBacnetConfig() {
        try {
            const cfg = await App.api('/api/bacnet/config');
            document.getElementById('bacnet-ip').value = cfg.ip || '';
            document.getElementById('bacnet-mask').value = cfg.mask || '24';
            document.getElementById('bacnet-port').value = cfg.port || 47808;
        } catch (e) {
            // Silent
        }
    }

    async function detectInterfaces() {
        try {
            const data = await App.api('/api/bacnet/interfaces');
            const select = document.getElementById('bacnet-interface-select');
            select.innerHTML = '<option value="">— Select Interface —</option>';

            if (!data.interfaces || data.interfaces.length === 0) {
                select.innerHTML = '<option value="">No interfaces detected</option>';
                App.toast('No usable network interfaces found', 'warning');
                return;
            }

            data.interfaces.forEach(iface => {
                const stateIcon = iface.state === 'UP' ? '🟢' : '🔴';
                const opt = document.createElement('option');
                opt.value = JSON.stringify(iface);
                opt.textContent = `${stateIcon} ${iface.interface} — ${iface.ip}/${iface.mask} (${iface.state})`;
                select.appendChild(opt);
            });

            App.toast(`Detected ${data.interfaces.length} interface(s)`, 'success');
        } catch (e) {
            App.toast('Interface detection failed: ' + e.message, 'error');
        }
    }

    function onInterfaceSelect(selectEl) {
        if (!selectEl.value) return;
        try {
            const iface = JSON.parse(selectEl.value);
            document.getElementById('bacnet-ip').value = iface.ip;
            document.getElementById('bacnet-mask').value = iface.mask;
        } catch (e) {
            // Ignore
        }
    }

    async function saveBacnetConfig() {
        const ip = document.getElementById('bacnet-ip').value.trim();
        const mask = document.getElementById('bacnet-mask').value.trim();
        const port = parseInt(document.getElementById('bacnet-port').value) || 47808;

        if (!ip) {
            App.toast('Please select or enter a BACnet IP address', 'warning');
            return;
        }

        try {
            const resp = await App.api('/api/bacnet/config', 'PUT', {
                ip, mask, port,
                device_id: 599,
                default_poll_interval: 10,
            });

            if (resp.status === 'updated') {
                App.toast(`BACnet configured: ${ip}/${mask}:${port}`, 'success');
            } else {
                App.toast('Config update failed', 'error');
            }
        } catch (e) {
            App.toast('Save failed: ' + e.message, 'error');
        }
    }

    // ── Scan Mode UI ─────────────────────────────

    function onScanModeChange(mode) {
        const rangeRow = document.getElementById('scan-range-row');
        const specificRow = document.getElementById('scan-specific-row');

        rangeRow.classList.add('hidden');
        specificRow.classList.add('hidden');

        if (mode === 'range') {
            rangeRow.classList.remove('hidden');
        } else if (mode === 'specific') {
            specificRow.classList.remove('hidden');
        }
    }

    // ── Device Discovery ──────────────────────────

    async function discover() {
        const btn = document.getElementById('btn-discover');
        const statusEl = document.getElementById('scan-status');
        btn.disabled = true;

        const scanMode = document.getElementById('scan-mode').value;
        const timeout = parseInt(document.getElementById('scan-timeout').value) || 10;

        const body = { scan_mode: scanMode, timeout };

        if (scanMode === 'range') {
            body.low_id = parseInt(document.getElementById('scan-low-id').value) || 0;
            body.high_id = parseInt(document.getElementById('scan-high-id').value) || 4194303;
        } else if (scanMode === 'specific') {
            const devId = document.getElementById('scan-device-id').value.trim();
            if (!devId) {
                App.toast('Please enter a Device ID to scan', 'warning');
                btn.disabled = false;
                return;
            }
            body.device_id = parseInt(devId);
        }

        const modeLabels = {
            full: '🌐 Full Network',
            range: `📊 Range ${body.low_id || 0}–${body.high_id || '?'}`,
            specific: `🎯 Device ${body.device_id || '?'}`,
        };
        const modeLabel = modeLabels[scanMode] || 'Scan';

        btn.textContent = '⏳ Scanning…';
        statusEl.textContent = `${modeLabel} — waiting ${timeout}s for responses…`;

        let remaining = timeout;
        const countdownInterval = setInterval(() => {
            remaining--;
            if (remaining > 0) {
                statusEl.textContent = `${modeLabel} — ${remaining}s remaining…`;
            } else {
                statusEl.textContent = `${modeLabel} — processing results…`;
            }
        }, 1000);

        try {
            const fetchTimeout = (timeout + 20) * 1000;
            const data = await App.api('/api/bacnet/discover', 'POST', body, fetchTimeout);

            clearInterval(countdownInterval);

            if (data.error) {
                App.toast(data.error, 'error');
                statusEl.textContent = `❌ Error: ${data.error}`;
                return;
            }

            deviceList = data.devices || [];
            renderDevices();

            if (deviceList.length === 0) {
                statusEl.textContent = `✅ Scan complete — no devices found`;
                App.toast('No devices found on the network.', 'warning');
            } else {
                statusEl.textContent = `✅ Found ${deviceList.length} device(s)`;
                App.toast(`Found ${deviceList.length} device(s)`, 'success');
            }
        } catch (e) {
            clearInterval(countdownInterval);
            statusEl.textContent = `❌ ${e.message}`;
            App.toast('Discovery failed: ' + e.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '🔍 Start Scan';
        }
    }

    function renderDevices() {
        const grid = document.getElementById('devices-grid');

        if (deviceList.length === 0) {
            grid.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1">
          <div class="icon">🔌</div>
          <h3>No devices discovered</h3>
          <p>Configure BACnet interface above, then scan the network.</p>
        </div>`;
            return;
        }

        grid.innerHTML = deviceList.map(dev => `
      <div class="device-card" onclick="Devices.viewObjects(${dev.device_id})">
        <div class="device-name">
          ${escapeHtml(dev.device_name || 'Unknown Device')}
        </div>
        <div class="device-meta">
          <span>ID: ${dev.device_id}</span>
          <span>📍 ${escapeHtml(dev.address || '—')}</span>
        </div>
        ${dev.vendor_name ? `<div class="device-meta" style="margin-top:4px"><span>${escapeHtml(dev.vendor_name)}</span></div>` : ''}
        <div style="margin-top:8px;font-size:0.82rem;color:var(--text-secondary)">Click to browse & auto-map objects</div>
      </div>
    `).join('');
    }

    // ── Object Browser & Auto Map ─────────────────

    async function viewObjects(deviceId) {
        const modal = document.getElementById('device-objects-modal');
        const title = document.getElementById('modal-device-title');
        const subtitle = document.getElementById('modal-device-subtitle');
        const body = document.getElementById('modal-objects-body');

        currentDeviceId = deviceId;
        allObjects = [];
        selectedObjects = {};

        const dev = deviceList.find(d => d.device_id === deviceId);
        title.textContent = dev
            ? `${dev.device_name || 'Device'} (ID: ${deviceId})`
            : `Device ${deviceId}`;
        subtitle.textContent = `📍 ${dev?.address || '—'} — Loading objects…`;

        body.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:24px">⏳ Reading object list from device…</td></tr>';
        modal.classList.remove('hidden');
        updateSelectionCount();

        try {
            const data = await App.api(`/api/bacnet/devices/${deviceId}/objects`, 'GET', null, 60000);

            if (data.error) {
                body.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:24px;color:var(--error)">${data.error}</td></tr>`;
                subtitle.textContent = `Error loading objects`;
                return;
            }

            allObjects = data.objects || [];
            subtitle.textContent = `📍 ${dev?.address || '—'} — ${allObjects.length} objects found`;

            // Populate type filter
            const typeFilter = document.getElementById('obj-type-filter');
            const types = [...new Set(allObjects.map(o => o.object_type))].sort();
            typeFilter.innerHTML = '<option value="">All Types (' + allObjects.length + ')</option>';
            types.forEach(t => {
                const count = allObjects.filter(o => o.object_type === t).length;
                typeFilter.innerHTML += `<option value="${t}">${t} (${count})</option>`;
            });

            renderObjects();
        } catch (e) {
            body.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:24px;color:var(--error)">Error: ${e.message}</td></tr>`;
            subtitle.textContent = `Error loading objects`;
        }
    }

    function renderObjects() {
        const body = document.getElementById('modal-objects-body');
        const filter = document.getElementById('obj-type-filter').value;

        const filtered = filter
            ? allObjects.filter(o => o.object_type === filter)
            : allObjects;

        if (filtered.length === 0) {
            body.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px">No objects match filter</td></tr>';
            document.getElementById('obj-count-info').textContent = '0 objects';
            return;
        }

        body.innerHTML = filtered.map(obj => {
            const key = `${obj.object_type}:${obj.object_instance}`;
            const checked = selectedObjects[key] ? 'checked' : '';
            return `
        <tr>
          <td>
            <input type="checkbox" ${checked} data-key="${key}"
              onchange="Devices.toggleObject('${key}', this.checked)" />
          </td>
          <td><span class="badge badge-info">${escapeHtml(obj.object_type)}</span></td>
          <td>${obj.object_instance}</td>
          <td>${escapeHtml(obj.object_name || '—')}</td>
        </tr>`;
        }).join('');

        document.getElementById('obj-count-info').textContent =
            `Showing ${filtered.length} of ${allObjects.length} objects`;
    }

    function filterObjects() {
        renderObjects();
    }

    function toggleObject(key, checked) {
        if (checked) {
            selectedObjects[key] = true;
        } else {
            delete selectedObjects[key];
        }
        updateSelectionCount();
    }

    function toggleSelectAll(checked) {
        const filter = document.getElementById('obj-type-filter').value;
        const filtered = filter
            ? allObjects.filter(o => o.object_type === filter)
            : allObjects;

        filtered.forEach(obj => {
            const key = `${obj.object_type}:${obj.object_instance}`;
            if (checked) {
                selectedObjects[key] = true;
            } else {
                delete selectedObjects[key];
            }
        });

        renderObjects();
        updateSelectionCount();
    }

    function updateSelectionCount() {
        const count = Object.keys(selectedObjects).length;
        document.getElementById('selected-count').textContent = count;
        document.getElementById('btn-map-selected').disabled = count === 0;
    }

    async function mapSelected() {
        const count = Object.keys(selectedObjects).length;
        if (count === 0) return;

        const pollInterval = parseInt(document.getElementById('automap-poll-interval').value) || 10;
        const readMode = document.getElementById('automap-read-mode')?.value || 'auto';
        const dev = deviceList.find(d => d.device_id === currentDeviceId);
        const deviceName = dev?.device_name || `device_${currentDeviceId}`;

        // Build bulk mappings with de-duplication
        const mappings = [];
        const seen = new Set();
        for (const key of Object.keys(selectedObjects)) {
            const [objType, objInstStr] = key.split(':');
            const objInst = parseInt(objInstStr);

            // Client-side dedup
            const dedupKey = `${currentDeviceId}:${objType}:${objInst}`;
            if (seen.has(dedupKey)) continue;
            seen.add(dedupKey);

            const obj = allObjects.find(o => o.object_type === objType && o.object_instance === objInst);

            // Auto label: extract short name from objectName
            const fullName = obj?.object_name || `${objType}_${objInst}`;
            const parts = fullName.split(/[.\/\\]/);
            const label = parts[parts.length - 1] || fullName;

            // Auto MQTT topic: bacnet/{deviceId}/{type}/{instance}
            const mqttTopic = `bacnet/${currentDeviceId}/${objType}/${objInst}`;

            // For 'auto' mode: try COV for output/value types, poll for input types
            let finalMode = readMode;
            if (readMode === 'auto') {
                const ot = objType.toLowerCase();
                // Input types usually support COV well, Output/Value may or may not
                // Default: use COV for all — gateway engine will fallback to poll if COV fails
                finalMode = 'cov';
            }

            mappings.push({
                label,
                device_id: currentDeviceId,
                object_type: objType,
                object_instance: objInst,
                poll_interval: pollInterval,
                read_mode: finalMode,
                mqtt_topic: mqttTopic,
                enabled: true,
            });
        }

        const btn = document.getElementById('btn-map-selected');
        btn.disabled = true;
        btn.textContent = `⏳ Creating ${mappings.length} mappings…`;

        try {
            const resp = await App.api('/api/mappings/bulk', 'POST', { mappings }, 30000);

            if (resp.error) {
                App.toast(resp.error, 'error');
                return;
            }

            if (resp.created !== undefined) {
                let msg = `✅ Created ${resp.created} mapping(s)`;
                if (resp.skipped) msg += ` (${resp.skipped} duplicates skipped)`;
                App.toast(msg, 'success');

                if (resp.ram_warning) {
                    setTimeout(() => App.toast(resp.ram_warning, 'warning'), 500);
                }

                selectedObjects = {};
                updateSelectionCount();
                renderObjects();
                closeModal();

                App.navigateTo('mappings');
                setTimeout(() => Mapping.load(), 300);
            } else {
                App.toast('Bulk mapping failed', 'error');
            }
        } catch (e) {
            App.toast('Map error: ' + e.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = `📥 Map Selected (${Object.keys(selectedObjects).length})`;
        }
    }

    function closeModal() {
        document.getElementById('device-objects-modal').classList.add('hidden');
    }

    // Load on page visit
    async function loadExisting() {
        await loadBacnetConfig();
        // Auto-detect interfaces so dropdown is pre-populated
        await detectInterfaces();
        // Show configured devices from mappings
        await refreshConfigured();
        try {
            const data = await App.api('/api/bacnet/devices');
            deviceList = data.devices || [];
            renderDevices();
        } catch (e) {
            // Silent
        }
    }

    async function refreshConfigured() {
        const grid = document.getElementById('configured-devices-grid');
        if (!grid) return;
        try {
            const data = await App.api('/api/bacnet/configured-devices');
            const devices = data.devices || [];
            if (devices.length === 0) {
                grid.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;padding:8px">No devices configured in mappings.</div>';
                return;
            }
            grid.innerHTML = devices.map(d => {
                const statusIcon = d.online ? '🟢' : '🔴';
                const statusText = d.online ? 'Online' : 'Offline';
                const statusColor = d.online ? 'var(--success)' : 'var(--error)';
                const age = d.last_updated ? timeSince(d.last_updated) : 'Never';
                return `
                <div class="card" style="padding:10px;cursor:pointer" onclick="Devices.viewObjects(${d.device_id})">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
                        <span style="font-weight:600;font-size:0.9rem">${statusIcon} Device ${d.device_id}</span>
                        <span style="font-size:0.7rem;color:${statusColor};font-weight:500">${statusText}</span>
                    </div>
                    <div style="font-size:0.75rem;color:var(--text-secondary)">
                        <div>📍 ${d.address || 'Unknown address'}</div>
                        <div>📊 ${d.point_count} points mapped</div>
                        <div>🕐 ${age}</div>
                    </div>
                </div>`;
            }).join('');
        } catch (e) {
            grid.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;padding:8px">Error loading configured devices.</div>';
        }
    }

    function timeSince(isoStr) {
        try {
            const d = new Date(isoStr);
            const secs = Math.floor((Date.now() - d.getTime()) / 1000);
            if (secs < 10) return 'Just now';
            if (secs < 60) return secs + 's ago';
            if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
            if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
            return Math.floor(secs / 86400) + 'd ago';
        } catch { return isoStr; }
    }

    return {
        discover, viewObjects, closeModal, loadExisting, refreshConfigured,
        detectInterfaces, onInterfaceSelect, saveBacnetConfig,
        onScanModeChange, toggleSelectAll, toggleObject, filterObjects,
        mapSelected,
    };
})();
