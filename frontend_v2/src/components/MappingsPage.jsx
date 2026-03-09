import React, { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { AgGridReact } from 'ag-grid-react';
import { AllCommunityModule, ModuleRegistry, themeQuartz } from 'ag-grid-community';
import { useMappingStore } from '../stores/mappingStore';
import { Search, Plus, Download, Upload, Copy, Trash2, RefreshCw, Edit3, X } from 'lucide-react';
import { DetailPanel } from './DetailPanel';

ModuleRegistry.registerModules([AllCommunityModule]);

const API = '/api';

const darkGridTheme = themeQuartz.withParams({
  backgroundColor: 'transparent', foregroundColor: '#ffffff',
  headerBackgroundColor: 'rgba(10, 10, 24, 0.8)', headerTextColor: '#a0a0b8',
  headerFontSize: 11, headerFontWeight: 600,
  oddRowBackgroundColor: 'rgba(18, 18, 38, 0.3)',
  fontFamily: 'Outfit, system-ui, sans-serif', fontSize: 13,
  rowHoverColor: 'rgba(0, 240, 255, 0.06)',
  selectedRowBackgroundColor: 'rgba(0, 240, 255, 0.12)',
  borderColor: 'rgba(255,255,255,0.06)', accentColor: '#00f0ff',
  columnBorder: false, wrapperBorder: false, wrapperBorderRadius: 12,
  headerColumnBorder: false, cellHorizontalPadding: 14, spacing: 6,
  rowVerticalPaddingScale: 1.2, headerVerticalPaddingScale: 1.5,
});

const TYPE_MAP = {
  analogInput:'AI', analogOutput:'AO', analogValue:'AV',
  binaryInput:'BI', binaryOutput:'BO', binaryValue:'BV',
  multiStateInput:'MSI', multiStateOutput:'MSO', multiStateValue:'MSV',
  'analog-input':'AI','analog-output':'AO','analog-value':'AV',
  'binary-input':'BI','binary-output':'BO','binary-value':'BV',
  'multi-state-input':'MSI','multi-state-output':'MSO','multi-state-value':'MSV', device:'DEV',
};

const OBJ_TYPES = [
  { v:'analogInput', l:'Analog Input (AI)' }, { v:'analogOutput', l:'Analog Output (AO)' },
  { v:'analogValue', l:'Analog Value (AV)' }, { v:'binaryInput', l:'Binary Input (BI)' },
  { v:'binaryOutput', l:'Binary Output (BO)' }, { v:'binaryValue', l:'Binary Value (BV)' },
  { v:'multiStateInput', l:'Multi-State Input (MSI)' }, { v:'multiStateOutput', l:'Multi-State Output (MSO)' },
  { v:'multiStateValue', l:'Multi-State Value (MSV)' },
];

const INPUT_CLS = 'w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus transition-all';
const SELECT_CLS = 'w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus transition-all';
const LABEL_CLS = 'block text-xs text-text-muted mb-1 font-medium uppercase tracking-wider';

function ModalBase({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-bg-secondary border border-border rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h3 className="text-base font-bold text-white">{title}</h3>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-bg-input text-text-muted hover:text-white transition-all"><X size={18} /></button>
        </div>
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}

// ── Add Point Modal ─────────────────────────────────────────────
function AddPointModal({ onClose, groups }) {
  const { createMapping, fetchMappings } = useMappingStore();
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    device_id: '', object_type: 'analogInput', object_instance: '',
    label: '', poll_interval: 10, read_mode: 'poll',
    mqtt_topic: '', group: '',
  });

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.device_id || form.object_instance === '') return;
    setSaving(true);
    try {
      await createMapping({
        ...form,
        device_id: Number(form.device_id),
        object_instance: Number(form.object_instance),
        poll_interval: Number(form.poll_interval),
      });
      onClose();
    } catch (err) {
      alert('Error: ' + err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <ModalBase title="➕ Add New Point" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={LABEL_CLS}>Device ID *</label>
            <input className={INPUT_CLS} type="number" required placeholder="e.g. 2507"
              value={form.device_id} onChange={e => set('device_id', e.target.value)} />
          </div>
          <div>
            <label className={LABEL_CLS}>Object Instance *</label>
            <input className={INPUT_CLS} type="number" required placeholder="e.g. 1"
              value={form.object_instance} onChange={e => set('object_instance', e.target.value)} />
          </div>
        </div>
        <div>
          <label className={LABEL_CLS}>Object Type *</label>
          <select className={SELECT_CLS} value={form.object_type} onChange={e => set('object_type', e.target.value)}>
            {OBJ_TYPES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
          </select>
        </div>
        <div>
          <label className={LABEL_CLS}>Label / Name</label>
          <input className={INPUT_CLS} placeholder="e.g. Room Temp AHU-01"
            value={form.label} onChange={e => set('label', e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={LABEL_CLS}>Read Mode</label>
            <select className={SELECT_CLS} value={form.read_mode} onChange={e => set('read_mode', e.target.value)}>
              <option value="poll">🔄 Poll</option>
              <option value="cov">⚡ COV</option>
            </select>
          </div>
          <div>
            <label className={LABEL_CLS}>Poll Interval (s)</label>
            <input className={INPUT_CLS} type="number" min="1" max="3600"
              value={form.poll_interval} onChange={e => set('poll_interval', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={LABEL_CLS}>Group</label>
            <select className={SELECT_CLS} value={form.group} onChange={e => set('group', e.target.value)}>
              <option value="">— None —</option>
              {groups.map(g => <option key={g.id} value={g.name}>{g.name}</option>)}
            </select>
          </div>
          <div>
            <label className={LABEL_CLS}>MQTT Topic (auto if blank)</label>
            <input className={INPUT_CLS} placeholder="bacnet/device/.../value"
              value={form.mqtt_topic} onChange={e => set('mqtt_topic', e.target.value)} />
          </div>
        </div>
        <div className="flex gap-3 pt-2">
          <button type="button" onClick={onClose} className="flex-1 px-4 py-2 border border-border rounded-lg text-sm text-text-secondary hover:text-white hover:border-accent-primary transition-all">Cancel</button>
          <button type="submit" disabled={saving} className="flex-1 px-4 py-2 bg-accent-gradient rounded-lg text-white text-sm font-medium disabled:opacity-50 shadow-[0_2px_12px_var(--color-accent-glow)] hover:-translate-y-0.5 transition-transform">
            {saving ? 'Saving…' : '✓ Add Point'}
          </button>
        </div>
      </form>
    </ModalBase>
  );
}

// ── Bulk Edit Modal ─────────────────────────────────────────────
function BulkEditModal({ count, onClose, groups, selectedIds, onDone }) {
  const { bulkUpdate } = useMappingStore();
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ read_mode:'', poll_interval:'', group:'' });
  const set = (k,v) => setForm(f => ({ ...f, [k]: v }));

  const handleApply = async () => {
    if (selectedIds.size === 0) return;
    setSaving(true);
    const payload = {};
    if (form.read_mode) payload.read_mode = form.read_mode;
    if (form.poll_interval) payload.poll_interval = Number(form.poll_interval);
    if (form.group !== '') payload.group = form.group === '__CLEAR__' ? '' : form.group;
    if (Object.keys(payload).length === 0) { setSaving(false); onClose(); return; }
    const updated = await bulkUpdate([...selectedIds], payload);
    setSaving(false);
    onDone?.(updated);
    onClose();
  };

  return (
    <ModalBase title={`✏️ Bulk Edit — ${count} Points Selected`} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-xs text-text-muted">Leave blank to keep each point's current value.</p>
        <div>
          <label className={LABEL_CLS}>Read Mode</label>
          <select className={SELECT_CLS} value={form.read_mode} onChange={e => set('read_mode', e.target.value)}>
            <option value="">— Keep current —</option>
            <option value="poll">🔄 Poll</option>
            <option value="cov">⚡ COV</option>
          </select>
        </div>
        <div>
          <label className={LABEL_CLS}>Poll Interval (s)</label>
          <input className={INPUT_CLS} type="number" min="1" placeholder="Keep current"
            value={form.poll_interval} onChange={e => set('poll_interval', e.target.value)} />
        </div>
        <div>
          <label className={LABEL_CLS}>Group</label>
          <select className={SELECT_CLS} value={form.group} onChange={e => set('group', e.target.value)}>
            <option value="">— Keep current —</option>
            <option value="__CLEAR__">⛔ Clear (none)</option>
            {groups.map(g => <option key={g.id} value={g.name}>{g.name}</option>)}
          </select>
        </div>
        <div className="flex gap-3 pt-2">
          <button onClick={onClose} className="flex-1 px-4 py-2 border border-border rounded-lg text-sm text-text-secondary hover:text-white transition-all">Cancel</button>
          <button onClick={handleApply} disabled={saving} className="flex-1 px-4 py-2 bg-gradient-to-r from-warning to-orange-500 rounded-lg text-white text-sm font-medium disabled:opacity-50 hover:-translate-y-0.5 transition-transform">
            {saving ? 'Applying…' : `✓ Apply to ${count} Points`}
          </button>
        </div>
      </div>
    </ModalBase>
  );
}

// ── Clone Points Modal ──────────────────────────────────────────
function CloneModal({ count, onClose, groups, selectedMappings, onDone }) {
  const { createMapping } = useMappingStore();
  const [saving, setSaving] = useState(false);
  const [targetDevice, setTargetDevice] = useState('');
  const [prefix, setPrefix] = useState('');
  const [targetGroup, setTargetGroup] = useState('');

  const handleClone = async () => {
    if (!targetDevice || selectedMappings.length === 0) return;
    setSaving(true);
    let cloned = 0;
    for (const m of selectedMappings) {
      try {
        await createMapping({
          device_id: Number(targetDevice),
          object_type: m.object_type,
          object_instance: m.object_instance,
          label: prefix ? `${prefix}${m.label || m.object_instance}` : m.label,
          poll_interval: m.poll_interval,
          read_mode: m.read_mode,
          group: targetGroup || m.group || '',
          mqtt_topic: '',
        });
        cloned++;
      } catch (e) { console.warn('Clone skip:', e.message); }
    }
    setSaving(false);
    onDone?.(cloned);
    onClose();
  };

  return (
    <ModalBase title={`📋 Clone ${count} Points to Device`} onClose={onClose}>
      <div className="space-y-4">
        <div>
          <label className={LABEL_CLS}>Target Device ID *</label>
          <input className={INPUT_CLS} type="number" required placeholder="e.g. 3001"
            value={targetDevice} onChange={e => setTargetDevice(e.target.value)} />
        </div>
        <div>
          <label className={LABEL_CLS}>Label Prefix (optional)</label>
          <input className={INPUT_CLS} placeholder="e.g. FCU-02_"
            value={prefix} onChange={e => setPrefix(e.target.value)} />
        </div>
        <div>
          <label className={LABEL_CLS}>Assign to Group</label>
          <select className={SELECT_CLS} value={targetGroup} onChange={e => setTargetGroup(e.target.value)}>
            <option value="">— Keep original group —</option>
            {groups.map(g => <option key={g.id} value={g.name}>{g.name}</option>)}
          </select>
        </div>
        <div className="bg-bg-input/50 rounded-lg p-3 text-xs text-text-muted border border-border/30">
          Will clone: {selectedMappings.map(m => m.label || `${m.object_type}:${m.object_instance}`).join(', ')}
        </div>
        <div className="flex gap-3 pt-2">
          <button onClick={onClose} className="flex-1 px-4 py-2 border border-border rounded-lg text-sm text-text-secondary hover:text-white transition-all">Cancel</button>
          <button onClick={handleClone} disabled={saving || !targetDevice} className="flex-1 px-4 py-2 bg-gradient-to-r from-success to-emerald-600 rounded-lg text-white text-sm font-medium disabled:opacity-50 hover:-translate-y-0.5 transition-transform">
            {saving ? `Cloning…` : `✓ Clone ${count} Points`}
          </button>
        </div>
      </div>
    </ModalBase>
  );
}

// ── Cell renderers ──────────────────────────────────────────────
function TypeBadge({ value }) {
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wide bg-info/15 text-info">{TYPE_MAP[value] || value}</span>;
}
function ModeBadge({ value }) {
  const isCov = value === 'cov';
  return <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold ${isCov ? 'bg-success/15 text-success' : 'bg-accent-primary/15 text-accent-primary'}`}>{isCov ? '⚡ COV' : '🔄 Poll'}</span>;
}
function ValueCell({ data }) {
  const val = data?.last_value;
  if (val == null) return <span className="text-text-muted">—</span>;
  const ot = (data.object_type || '').toLowerCase();
  if (ot.includes('binary')) {
    const isOn = (val === 'active' || val === 1 || String(val).toLowerCase() === 'active');
    return <span className={`font-bold ${isOn ? 'text-success' : 'text-error'}`}>{isOn ? (data.active_text || 'Active') : (data.inactive_text || 'Inactive')}</span>;
  }
  return <span className="font-semibold">{String(val)}{data.units ? <span className="text-text-muted text-[10px] ml-1">{data.units}</span> : ''}</span>;
}
function EnabledCell({ value, data }) {
  const update = useMappingStore(s => s.updateMapping);
  return (
    <label className="relative inline-block w-9 h-5 cursor-pointer">
      <input type="checkbox" checked={value !== false} onChange={e => update(data.id, { enabled: e.target.checked })} className="sr-only peer" />
      <div className="w-9 h-5 rounded-full bg-bg-input border border-border peer-checked:bg-accent-primary peer-checked:border-accent-primary transition-all" />
      <div className="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-text-muted peer-checked:bg-white peer-checked:translate-x-4 transition-all" />
    </label>
  );
}

// ── Main Page ───────────────────────────────────────────────────
export function MappingsPage() {
  const gridRef = useRef();
  const { mappings, loading, fetchMappings, selectedId, setSelectedId,
    selectedIds, selectAll, clearSelection, deleteMapping, exportMappings, importMappings } = useMappingStore();

  const [quickFilter, setQuickFilter] = useState('');
  const [showDetail, setShowDetail] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [showClone, setShowClone] = useState(false);
  const [groups, setGroups] = useState([]);
  const [toast, setToast] = useState('');

  useEffect(() => { fetchMappings(); }, [fetchMappings]);
  useEffect(() => {
    fetch(`${API}/groups`).then(r => r.json()).then(d => setGroups(d.groups || [])).catch(() => {});
  }, []);

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const colDefs = useMemo(() => [
    { headerCheckboxSelection: true, checkboxSelection: true, width: 50, pinned: 'left', headerCheckboxSelectionFilteredOnly: true, suppressHeaderMenuButton: true },
    { field: 'label', headerName: 'Label', flex: 2, minWidth: 180, editable: true,
      valueGetter: p => { const full = p.data?.label || `${p.data?.object_type}:${p.data?.object_instance}`; return full.split(/[.\\/\\\\]/).pop() || full; },
      cellClass: 'font-medium' },
    { field: 'object_type', headerName: 'Type', width: 90, cellRenderer: p => <TypeBadge value={p.value} /> },
    { field: 'device_id', headerName: 'Device', width: 90, editable: true, cellClass: 'text-text-secondary' },
    { field: 'object_instance', headerName: 'Instance', width: 100 },
    { headerName: 'Value', width: 130, cellRenderer: p => <ValueCell data={p.data} />, valueGetter: p => p.data?.last_value },
    { field: 'read_mode', headerName: 'Mode', width: 100, cellRenderer: p => <ModeBadge value={p.value || 'poll'} />, editable: true, cellEditor: 'agSelectCellEditor', cellEditorParams: { values: ['poll', 'cov'] } },
    { field: 'poll_interval', headerName: 'Interval', width: 90, editable: true, valueFormatter: p => `${p.value}s`, cellClass: 'text-text-secondary' },
    { field: 'group', headerName: 'Group', width: 120, editable: true, cellClass: 'text-accent-secondary font-medium' },
    { field: 'mqtt_topic', headerName: 'MQTT Topic', flex: 1, minWidth: 150, editable: true, cellClass: 'text-text-muted text-xs' },
    { field: 'enabled', headerName: 'On', width: 70, cellRenderer: p => <EnabledCell value={p.value} data={p.data} /> },
  ], []);

  const defaultColDef = useMemo(() => ({ sortable: true, filter: true, resizable: true, cellStyle: { display: 'flex', alignItems: 'center' } }), []);

  const onCellValueChanged = useCallback(async (e) => {
    const { data, colDef } = e;
    if (!colDef.field || !data?.id) return;
    try {
      // Optimistic update: patch the row in-place so ag-grid re-renders immediately
      // without re-fetching all 100+ mappings from the server
      await fetch(`${API}/mappings/${data.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [colDef.field]: e.newValue }),
      });
      // Update the store mapping in-place (no full re-fetch)
      useMappingStore.setState(state => ({
        mappings: state.mappings.map(m => m.id === data.id ? { ...m, [colDef.field]: e.newValue } : m)
      }));
    } catch (err) { console.error('Inline edit error:', err); }
  }, []);

  const onRowClicked = useCallback((e) => {
    if (e.data?.id) { setSelectedId(e.data.id); setShowDetail(true); }
  }, [setSelectedId]);

  const onSelectionChanged = useCallback(() => {
    const rows = gridRef.current?.api?.getSelectedRows() || [];
    selectAll(rows.map(r => r.id));
  }, [selectAll]);

  const handleExport = useCallback(async () => {
    try {
      const data = await exportMappings();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `mappings_${new Date().toISOString().slice(0, 10)}.json`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { console.error(e); }
  }, [exportMappings]);

  const handleImport = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.json';
    input.onchange = async (e) => {
      const file = e.target.files[0]; if (!file) return;
      try {
        const text = await file.text();
        let data = JSON.parse(text);
        if (Array.isArray(data)) data = { mappings: data };
        await importMappings(data);
        showToast('✅ Import successful');
      } catch (err) { alert('Import failed: ' + err.message); }
    };
    input.click();
  }, [importMappings]);

  const handleDeleteSelected = useCallback(async () => {
    // Read directly from ag-grid to avoid stale closure on selectedIds
    const selectedRows = gridRef.current?.api?.getSelectedRows() || [];
    if (selectedRows.length === 0) { showToast('⚠️ No rows selected'); return; }
    if (!confirm(`Delete ${selectedRows.length} mapping(s)?`)) return;
    let deleted = 0;
    for (const row of selectedRows) {
      try {
        await fetch(`${API}/mappings/${row.id}`, { method: 'DELETE' });
        deleted++;
      } catch (e) { console.error('Delete failed for', row.id, e); }
    }
    clearSelection();
    await fetchMappings();
    showToast(`🗑 Deleted ${deleted} point${deleted !== 1 ? 's' : ''}`);
  }, [clearSelection, fetchMappings]);

  const selectedMapping = mappings.find(m => m.id === selectedId);
  const selectedMappings = mappings.filter(m => selectedIds.has(m.id));
  const selCount = selectedIds.size;

  return (
    <div className="flex h-screen w-full overflow-hidden">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 z-[100] px-4 py-2 bg-success/10 border border-success/30 text-success rounded-lg text-sm font-medium shadow-lg">
          {toast}
        </div>
      )}

      <div className={`flex-1 flex flex-col p-6 min-w-0 transition-all duration-300 ${showDetail ? 'mr-[420px]' : ''}`}>
        {/* Toolbar */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="text-2xl font-bold tracking-tight text-white">Mappings</h2>
            <p className="text-xs text-text-muted mt-1">
              {mappings.length} points configured
              {selCount > 0 && <span className="ml-2 text-accent-primary font-bold">• {selCount} selected</span>}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={fetchMappings} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all" title="Refresh">
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </button>
            <button onClick={handleExport} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all" title="Export JSON">
              <Download size={16} />
            </button>
            <button onClick={handleImport} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all" title="Import JSON">
              <Upload size={16} />
            </button>

            {/* Bulk actions — visible when rows selected */}
            {selCount > 0 && (<>
              <button onClick={() => setShowBulkEdit(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-warning/40 text-warning hover:bg-warning/10 text-xs font-medium transition-all" title="Bulk edit selected">
                <Edit3 size={14} /> Bulk Edit ({selCount})
              </button>
              <button onClick={() => setShowClone(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-success/40 text-success hover:bg-success/10 text-xs font-medium transition-all" title="Clone to another device">
                <Copy size={14} /> Clone ({selCount})
              </button>
              <button onClick={handleDeleteSelected}
                className="p-2 rounded-lg border border-error/40 text-error hover:bg-error/10 transition-all" title="Delete selected">
                <Trash2 size={16} />
              </button>
            </>)}

            <button onClick={() => setShowAdd(true)}
              className="flex items-center gap-2 px-4 py-2 bg-accent-gradient rounded-lg text-white text-sm font-medium shadow-[0_2px_12px_var(--color-accent-glow)] hover:-translate-y-0.5 transition-transform">
              <Plus size={16} /> Add Point
            </button>
          </div>
        </div>

        {/* Search */}
        <div className="relative mb-4">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text" placeholder="Search by label, type, group, device ID..."
            value={quickFilter} onChange={e => setQuickFilter(e.target.value)}
            className="w-full pl-10 pr-4 py-2.5 bg-bg-input border border-border rounded-xl text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus focus:shadow-[0_0_0_3px_rgba(0,240,255,0.1)] transition-all"
          />
        </div>

        {/* AG-Grid */}
        <div className="flex-1 rounded-2xl overflow-hidden border border-border bg-bg-card/30 backdrop-blur-xl" style={{ minHeight: '400px' }}>
          <AgGridReact
            ref={gridRef}
            rowData={mappings} columnDefs={colDefs} defaultColDef={defaultColDef}
            quickFilterText={quickFilter} rowSelection="multiple"
            suppressRowClickSelection={true}
            onCellValueChanged={onCellValueChanged}
            onRowClicked={onRowClicked}
            onSelectionChanged={onSelectionChanged}
            animateRows={true} getRowId={p => p.data.id}
            theme={darkGridTheme} rowHeight={44} headerHeight={40}
            overlayNoRowsTemplate='<div style="padding:40px;color:#5a5a75;">No mappings yet. Click <b>+ Add Point</b> to start.</div>'
          />
        </div>
      </div>

      {/* Detail Panel */}
      {showDetail && selectedMapping && (
        <DetailPanel mapping={selectedMapping} onClose={() => setShowDetail(false)} />
      )}

      {/* Modals */}
      {showAdd && <AddPointModal onClose={() => setShowAdd(false)} groups={groups} />}
      {showBulkEdit && (
        <BulkEditModal count={selCount} selectedIds={selectedIds} groups={groups}
          onClose={() => setShowBulkEdit(false)}
          onDone={n => { showToast(`✅ Updated ${n} points`); clearSelection(); }} />
      )}
      {showClone && (
        <CloneModal count={selCount} selectedMappings={selectedMappings} groups={groups}
          onClose={() => setShowClone(false)}
          onDone={n => { showToast(`✅ Cloned ${n} points`); clearSelection(); }} />
      )}
    </div>
  );
}
