import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
// WriteControl state: { [mapping_id]: { priority: '8', value: '', loading: false, result: null } }
import { RefreshCw, Loader2, Eye, Unlock, TrendingUp, Activity, AlertTriangle, ArrowUpDown, BarChart2, Pencil, Send, Minus } from 'lucide-react';

const API = '/api';

const TYPE_SHORT = {
  analogInput: 'AI', analogOutput: 'AO', analogValue: 'AV',
  binaryInput: 'BI', binaryOutput: 'BO', binaryValue: 'BV',
  multiStateInput: 'MSI', multiStateOutput: 'MSO', multiStateValue: 'MSV',
  'analog-input': 'AI', 'analog-output': 'AO', 'analog-value': 'AV',
  'binary-input': 'BI', 'binary-output': 'BO', 'binary-value': 'BV',
  device: 'DEV',
};

const TYPE_COLOR = {
  AI: 'bg-blue-500/15 text-blue-400', AO: 'bg-orange-500/15 text-orange-400',
  AV: 'bg-cyan-500/15 text-cyan-400', BI: 'bg-green-500/15 text-green-400',
  BO: 'bg-red-500/15 text-red-400', BV: 'bg-purple-500/15 text-purple-400',
  MSI: 'bg-teal-500/15 text-teal-400', MSO: 'bg-pink-500/15 text-pink-400',
  MSV: 'bg-indigo-500/15 text-indigo-400', DEV: 'bg-gray-500/15 text-gray-400',
};

// Priority level descriptions
const PA_LABELS = {
  1: 'Manual Life Safety', 2: 'Automatic Life Safety', 3: 'Avail 3', 4: 'Avail 4',
  5: 'Critical Equip Control', 6: 'Min On/Off', 7: 'Avail 7', 8: 'Manual Operator',
  9: 'Avail 9', 10: 'Avail 10', 11: 'Avail 11', 12: 'Avail 12',
  13: 'Avail 13', 14: 'Avail 14', 15: 'Avail 15', 16: 'Fallback',
};

const PA_COLOR = (p) => {
  if (p <= 2) return 'bg-red-500 text-white';
  if (p <= 4) return 'bg-orange-500 text-white';
  if (p <= 8) return 'bg-yellow-500 text-black';
  if (p <= 12) return 'bg-blue-500 text-white';
  return 'bg-gray-600 text-white';
};

export function MonitorPage() {
  const [mappings, setMappings] = useState([]);
  const [paData, setPaData] = useState({});
  const [loading, setLoading] = useState(false);
  const [loadingPA, setLoadingPA] = useState({});
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('ALL');
  const [groupFilter, setGroupFilter] = useState('ALL');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [expandedPA, setExpandedPA] = useState(null);
  const [writeCtrl, setWriteCtrl] = useState({}); // { [mapping_id]: { priority, value, loading, result } }
  const [tab, setTab] = useState('values'); // 'values' | 'priority'
  const [loadingAll, setLoadingAll] = useState(false);
  const intervalRef = useRef(null);
  const paIntervalRef = useRef(null);

  const [initLoading, setInitLoading] = useState(true);

  const fetchMappings = useCallback(async () => {
    try {
      const res = await fetch(`${API}/mappings`);
      const data = await res.json();
      const list = data.mappings || [];
      // Only update if we got real data (guard against transient empty response)
      setMappings(prev => list.length > 0 ? list : prev);
    } catch (e) { console.error(e); }
    finally { setInitLoading(false); }
  }, []);

  useEffect(() => {
    fetchMappings();
  }, [fetchMappings]);

  useEffect(() => {
    clearInterval(intervalRef.current);
    if (autoRefresh) {
      intervalRef.current = setInterval(fetchMappings, 5000);
    }
    return () => clearInterval(intervalRef.current);
  }, [autoRefresh, fetchMappings]);

  // Auto-load PA when user switches to Priority Array tab & no data loaded yet
  useEffect(() => {
    if (tab === 'priority' && mappings.length > 0 && Object.keys(paData).length === 0) {
      loadAllPA();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, mappings.length]);

  const loadAllPA = useCallback(async (targetMappings) => {
    const targets = targetMappings || mappings;
    const outputs = targets.filter(m => {
      const ot = (m.object_type || '').toLowerCase();
      return ot.includes('output') || ot.includes('value');
    });
    if (!outputs.length) return;
    setLoadingAll(true);
    const results = {};
    await Promise.allSettled(outputs.map(async m => {
      try {
        const res = await fetch(`${API}/bacnet/priority_array/${m.device_id}/${m.object_type}/${m.object_instance}`);
        if (!res.ok) return;
        const data = await res.json();
        results[m.id] = { pa: data.priority_array || {}, pv: data.present_value };
      } catch { /* skip */ }
    }));
    setPaData(prev => ({ ...prev, ...results }));
    setLoadingAll(false);
  }, [mappings]);

  const loadSinglePA = async (m) => {
    setLoadingPA(prev => ({ ...prev, [m.id]: true }));
    try {
      const res = await fetch(`${API}/bacnet/priority_array/${m.device_id}/${m.object_type}/${m.object_instance}`);
      const data = await res.json();
      setPaData(prev => ({ ...prev, [m.id]: { pa: data.priority_array || {}, pv: data.present_value } }));
    } catch { /* skip */ }
    setLoadingPA(prev => ({ ...prev, [m.id]: false }));
  };

  const relinquishAll = async (m) => {
    if (!confirm(`Relinquish ALL priorities on ${m.label || m.object_instance}?`)) return;
    try {
      await fetch(`${API}/bacnet/release`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: m.device_id, object_type: m.object_type, object_instance: m.object_instance, priority: 'all' })
      });
      setTimeout(() => loadSinglePA(m), 600);
    } catch (e) { console.error(e); }
  };

  const writeValue = async (m, priority, value) => {
    const mid = m.id;
    setWriteCtrl(p => ({ ...p, [mid]: { ...p[mid], loading: true, result: null } }));
    try {
      // For binary types keep 'active'/'inactive' as string; for others parse to number
      const ot = (m.object_type || '').toLowerCase();
      const isBin = ot.includes('binary');
      const sendVal = isBin ? value : parseFloat(value);
      const res = await fetch(`${API}/bacnet/write`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: m.device_id, object_type: m.object_type, object_instance: m.object_instance, value: sendVal, priority: parseInt(priority) })
      });
      const d = await res.json();
      const ok = d.success !== false;
      setWriteCtrl(p => ({ ...p, [mid]: { ...p[mid], loading: false, result: ok ? '✅ Wrote' : `❌ ${d.error || 'Failed'}` } }));
      if (ok) setTimeout(() => loadSinglePA(m), 600);
    } catch (e) {
      setWriteCtrl(p => ({ ...p, [mid]: { ...p[mid], loading: false, result: `❌ ${e.message}` } }));
    }
  };

  const relinquishOne = async (m, priority) => {
    const mid = m.id;
    setWriteCtrl(p => ({ ...p, [mid]: { ...p[mid], loading: true, result: null } }));
    try {
      await fetch(`${API}/bacnet/release`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: m.device_id, object_type: m.object_type, object_instance: m.object_instance, priority })
      });
      setWriteCtrl(p => ({ ...p, [mid]: { ...p[mid], loading: false, result: `✅ Released P${priority}` } }));
      setTimeout(() => loadSinglePA(m), 600);
    } catch (e) {
      setWriteCtrl(p => ({ ...p, [mid]: { ...p[mid], loading: false, result: `❌ ${e.message}` } }));
    }
  };

  const groups = useMemo(() => [...new Set(mappings.flatMap(m => (m.group || '').split(',').map(s => s.trim()).filter(Boolean)))], [mappings]);
  const types = useMemo(() => [...new Set(mappings.map(m => TYPE_SHORT[m.object_type] || m.object_type?.slice(0,2)?.toUpperCase()).filter(Boolean))].sort(), [mappings]);

  const filtered = useMemo(() => {
    let list = mappings;
    if (tab === 'priority') list = list.filter(m => {
      const ot = (m.object_type || '').toLowerCase();
      return ot.includes('output') || ot.includes('value');
    });
    if (typeFilter !== 'ALL') list = list.filter(m => (TYPE_SHORT[m.object_type] || '') === typeFilter);
    if (groupFilter !== 'ALL') list = list.filter(m => (m.group || '').includes(groupFilter));
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(m => (m.label || '').toLowerCase().includes(q) || String(m.object_instance).includes(q) || String(m.device_id).includes(q));
    }
    return list;
  }, [mappings, search, typeFilter, groupFilter, tab]);

  const polledCount = mappings.filter(m => m.last_value != null).length;
  const paLoadedCount = Object.keys(paData).length;
  const outputCount = mappings.filter(m => (m.object_type || '').toLowerCase().includes('output')).length;

  const formatValue = (m) => {
    const val = m.last_value;
    if (val == null) return null;
    const ot = (m.object_type || '').toLowerCase();
    if (ot.includes('binary')) {
      return val === 'active' || val === 1 || val === '1' ? { text: 'ACTIVE', cls: 'text-success' } : { text: 'INACTIVE', cls: 'text-text-muted' };
    }
    const num = parseFloat(val);
    if (!isNaN(num)) return { text: num.toFixed(2), unit: m.units || '', cls: 'text-white' };
    return { text: String(val), cls: 'text-white' };
  };

  const getAge = (ts) => {
    if (!ts) return null;
    const sec = Math.round((Date.now() - new Date(ts).getTime()) / 1000);
    if (sec < 60) return `${sec}s`;
    if (sec < 3600) return `${Math.floor(sec/60)}m`;
    return `${Math.floor(sec/3600)}h`;
  };

  return (
    <div className="p-6 flex flex-col" style={{ height: 'calc(100vh - 0px)' }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3 shrink-0">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Eye size={24} className="text-accent-primary" /> Live Monitor
          </h2>
          <p className="text-xs text-text-muted mt-1">
            <span className="text-white font-medium">{mappings.length}</span> points mapped&nbsp;·&nbsp;
            <span className={polledCount > 0 ? 'text-success' : 'text-text-muted'}>{polledCount} polled</span>&nbsp;·&nbsp;
            <span className="text-accent-primary">{paLoadedCount} with PA</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} className="accent-accent-primary" />
            Auto-refresh 5s
          </label>
          <button onClick={fetchMappings} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white transition-all"><RefreshCw size={14} /></button>
          {tab === 'priority' && (
            <button onClick={() => loadAllPA()} disabled={loadingAll}
              className="flex items-center gap-2 px-3 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-xs font-medium disabled:opacity-50">
              {loadingAll ? <Loader2 size={14} className="animate-spin" /> : <BarChart2 size={14} />}
              Load All PA ({outputCount} outputs)
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-3 shrink-0 border-b border-border">
        {[
          { id: 'values', icon: Activity, label: 'Live Values' },
          { id: 'priority', icon: ArrowUpDown, label: `Priority Array (${outputCount} outputs)` },
        ].map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-all -mb-px ${tab === t.id ? 'border-accent-primary text-accent-primary' : 'border-transparent text-text-secondary hover:text-white'}`}>
            <t.icon size={14} />{t.label}
          </button>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 mb-3 flex-wrap shrink-0">
        <input type="text" placeholder="Search label, instance, device..." value={search} onChange={e => setSearch(e.target.value)}
          className="px-3 py-1.5 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted w-56 focus:outline-none focus:border-border-focus" />
        <div className="flex gap-1 flex-wrap">
          <button onClick={() => setTypeFilter('ALL')} className={`px-2 py-1 rounded-full text-[10px] font-bold ${typeFilter === 'ALL' ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/40' : 'bg-bg-input border border-border text-text-secondary hover:text-white'}`}>ALL</button>
          {types.map(t => (
            <button key={t} onClick={() => setTypeFilter(typeFilter === t ? 'ALL' : t)} className={`px-2 py-1 rounded-full text-[10px] font-bold ${typeFilter === t ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/40' : 'bg-bg-input border border-border text-text-secondary hover:text-white'}`}>{t}</button>
          ))}
        </div>
        {groups.length > 0 && (
          <div className="flex gap-1 flex-wrap border-l border-border pl-2">
            {groups.map(g => (
              <button key={g} onClick={() => setGroupFilter(groupFilter === g ? 'ALL' : g)} className={`px-2 py-1 rounded-full text-[10px] ${groupFilter === g ? 'bg-purple-500/20 text-purple-400 border border-purple-500/40' : 'bg-bg-input border border-border text-text-secondary hover:text-white'}`}>{g}</button>
            ))}
          </div>
        )}
        <span className="ml-auto text-[10px] text-text-muted">{filtered.length} showing</span>
      </div>

      {/* Loading / No mappings */}
      {mappings.length === 0 && (
        <div className="glass-card p-10 text-center">
          {initLoading ? (
            <>
              <Loader2 size={36} className="mx-auto text-accent-primary mb-3 animate-spin" />
              <p className="text-text-secondary text-sm">Đang tải danh sách points...</p>
            </>
          ) : (
            <>
              <Activity size={36} className="mx-auto text-text-muted mb-3 opacity-40" />
              <p className="text-text-secondary text-sm mb-1">No points configured</p>
              <p className="text-text-muted text-xs">Go to Devices → select points → Add to mappings first</p>
            </>
          )}
        </div>
      )}

      {/* ── TAB: Live Values ── */}
      {tab === 'values' && mappings.length > 0 && (
        <div className="flex-1 overflow-y-auto">
          {polledCount === 0 && (
            <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-warning/5 border border-warning/20 rounded-lg text-xs text-warning">
              <AlertTriangle size={12} />
              <span>Gateway vừa khởi động. Các giá trị sẽ hiện sau vài giây khi có poll cycle đầu tiên. Tự động refresh mỗi 5s.</span>
            </div>
          )}
          <div className="glass-card overflow-hidden">
            <table className="w-full text-xs">
              <thead className="sticky top-0 z-10 bg-bg-secondary border-b border-border">
                <tr>
                  <th className="px-3 py-2.5 text-left font-bold text-text-muted">Type</th>
                  <th className="px-3 py-2.5 text-left font-bold text-text-muted">Label</th>
                  <th className="px-3 py-2.5 text-left font-bold text-text-muted">Device · Inst</th>
                  <th className="px-3 py-2.5 text-right font-bold text-text-muted">Value / Priority</th>
                  <th className="px-3 py-2.5 text-right font-bold text-text-muted">Updated</th>
                  <th className="px-3 py-2.5 text-center font-bold text-text-muted">Poll</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(m => {
                               const fv = formatValue(m);
                  const age = getAge(m.last_updated);
                  const ot = TYPE_SHORT[m.object_type] || m.object_type?.slice(0,2)?.toUpperCase() || '?';
                  const isStale = m.last_updated && (Date.now() - new Date(m.last_updated).getTime()) > 60000;
                  // Derive active priority from PA data (lowest slot with a non-null value)
                  const mPa = paData[m.id];
                  const activePriority = mPa ? Object.entries(mPa.pa || {}).filter(([, v]) => v != null && v !== 'null' && v !== 'Null').map(([k]) => parseInt(k)).sort((a,b) => a-b)[0] : null;
                  return (
                    <tr key={m.id} className="border-b border-border/30 hover:bg-white/[0.02] transition-colors">
                      <td className="px-3 py-2.5">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${TYPE_COLOR[ot] || 'bg-gray-500/15 text-gray-400'}`}>{ot}</span>
                      </td>
                      <td className="px-3 py-2.5 text-text-primary font-medium max-w-[220px] truncate">{m.label || `${m.object_type}:${m.object_instance}`}</td>
                      <td className="px-3 py-2.5 text-text-muted font-mono">{m.device_id} · {m.object_instance}</td>
                      <td className="px-3 py-2.5 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {fv ? (
                            <span className={`font-bold ${fv.cls}`}>{fv.text}{fv.unit ? <span className="text-text-muted ml-1 font-normal text-[10px]">{fv.unit}</span> : null}</span>
                          ) : (
                            <span className="text-text-muted italic">waiting…</span>
                          )}
                          {activePriority != null && (
                            <span className={`text-[9px] px-1.5 py-0 rounded font-bold border ${PA_COLOR(activePriority)} opacity-90`} title={`Active at Priority ${activePriority}`}>
                              P{activePriority}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {age ? <span className={`text-[10px] ${isStale ? 'text-warning' : 'text-text-muted'}`}>{age} ago</span> : <span className="text-text-muted text-[10px]">—</span>}
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <span className={`inline-block w-2 h-2 rounded-full ${m.enabled ? 'bg-success animate-pulse' : 'bg-bg-input'}`} title={m.enabled ? 'Polling' : 'Disabled'} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── TAB: Priority Array ── */}
      {tab === 'priority' && mappings.length > 0 && (
        <div className="flex-1 overflow-y-auto space-y-1.5">
          {paLoadedCount === 0 && (
            <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-info/5 border border-info/20 rounded-lg text-xs text-info">
              <BarChart2 size={12} />
              <span>Click <b>Load All PA</b> để đọc Priority Array từ BACnet cho tất cả output points. Dữ liệu này cho biết AI/người vận hành nào đang override giá trị ở priority nào (1=cao nhất, 16=thấp nhất).</span>
            </div>
          )}
          {filtered.map(m => {
            const pa = paData[m.id];
            const pvFromPA = pa ? pa.pv : null;
            const pv = pvFromPA ?? m.last_value;
            const paArr = pa ? pa.pa : {};
            const actives = Object.entries(paArr).filter(([, v]) => v != null && v !== 'null' && v !== 'Null');
            const isLoadingPA = loadingPA[m.id];
            const ot = TYPE_SHORT[m.object_type] || m.object_type?.slice(0,2)?.toUpperCase() || '?';
            const isExpanded = expandedPA === m.id;
            const wc = writeCtrl[m.id] || { priority: '8', value: '', loading: false, result: null };

            // PA grid: 2 rows of 8
            const row1 = Array.from({length: 8}, (_, i) => i + 1);
            const row2 = Array.from({length: 8}, (_, i) => i + 9);
            const paGrid = (row) => (
              <div className="flex items-center gap-1">
                {row.map(p => {
                  const val = paArr[String(p)];
                  const active = val != null && val !== 'null' && val !== 'Null';
                  // Format value for display: binary → A/I, number → truncate
                  const displayVal = active
                    ? (String(val) === 'active' ? 'A' : String(val) === 'inactive' ? 'I' : String(val).length > 3 ? String(val).slice(0,3) : String(val))
                    : null;
                  return (
                    <div key={p} title={`P${p} — ${PA_LABELS[p]}: ${active ? val : 'null'}`}
                      className={`w-7 h-7 rounded flex flex-col items-center justify-center text-[8px] font-bold transition-all cursor-default gap-0
                        ${active ? PA_COLOR(p) : 'bg-bg-input/50 text-text-muted/60 border border-border/30'}`}>
                      <span className="text-[9px] leading-none">{p}</span>
                      {active && <span className="leading-none opacity-90">{displayVal}</span>}
                    </div>
                  );
                })}
              </div>
            );

            return (
              <div key={m.id} className="glass-card overflow-hidden">
                {/* Row header */}
                <div className="flex items-start gap-3 px-4 py-3 cursor-pointer hover:bg-white/[0.02]"
                  onClick={() => setExpandedPA(isExpanded ? null : m.id)}>

                  {/* Left: type + label + value */}
                  <div className="flex flex-col gap-0.5 min-w-[180px] shrink-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${TYPE_COLOR[ot] || 'bg-gray-500/15 text-gray-400'}`}>{ot}</span>
                      <span className="text-sm font-medium text-white truncate max-w-[140px]">{m.label || `${m.object_type}:${m.object_instance}`}</span>
                    </div>
                    <div className="flex items-baseline gap-2">
                      <span className="text-[10px] text-text-muted font-mono">{m.device_id}:{m.object_instance}</span>
                      <span className={`text-base font-bold ${pv != null ? 'text-accent-primary' : 'text-text-muted'}`}>
                        {pv != null ? String(pv) : '—'}
                      </span>
                    </div>
                  </div>

                  {/* Center: PA 2-row grid */}
                  <div className="flex flex-col gap-1 flex-1" onClick={e => e.stopPropagation()}>
                    {paGrid(row1)}
                    {paGrid(row2)}
                  </div>

                  {/* Right: status + actions */}
                  <div className="flex flex-col items-end gap-1.5 shrink-0" onClick={e => e.stopPropagation()}>
                    <div className="flex items-center gap-1">
                      {actives.length > 0 && (
                        <span className="px-2 py-0.5 rounded-full bg-warning/20 text-warning text-[10px] font-bold">
                          {actives.length} override{actives.length > 1 ? 's' : ''}
                        </span>
                      )}
                      {pa && actives.length === 0 && <span className="text-[10px] text-success">Clean</span>}
                      <button onClick={() => loadSinglePA(m)} disabled={isLoadingPA}
                        className="p-1 rounded hover:bg-bg-card text-text-muted hover:text-white disabled:opacity-50" title="Read PA">
                        {isLoadingPA ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                      </button>
                      {actives.length > 0 && (
                        <button onClick={() => relinquishAll(m)}
                          className="p-1 rounded hover:bg-error/10 text-error" title="Relinquish All">
                          <Unlock size={12} />
                        </button>
                      )}
                    </div>
                    {/* Inline Write Control */}
                    <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
                      <select
                        value={wc.priority}
                        onChange={e => setWriteCtrl(p => ({ ...p, [m.id]: { ...wc, priority: e.target.value, result: null } }))}
                        className="text-[10px] px-1.5 py-1 bg-bg-input border border-border rounded text-white focus:outline-none focus:border-accent-primary w-[70px]">
                        {Array.from({length:16},(_,i)=>i+1).map(p => (
                          <option key={p} value={String(p)}>P{p} {paArr[String(p)] != null && paArr[String(p)] !== 'null' ? '●' : ''}</option>
                        ))}
                      </select>
                      {(() => {
                        const ot = (m.object_type || '').toLowerCase();
                        const isBin = ot.includes('binary');
                        if (isBin) {
                          return (
                            <select
                              value={wc.value}
                              onChange={e => setWriteCtrl(p => ({ ...p, [m.id]: { ...wc, value: e.target.value, result: null } }))}
                              className="text-[10px] px-1.5 py-1 bg-bg-input border border-border rounded text-white w-[90px] focus:outline-none focus:border-accent-primary"
                            >
                              <option value="">-- chọn --</option>
                              <option value="active">✅ Active</option>
                              <option value="inactive">⬛ Inactive</option>
                            </select>
                          );
                        }
                        return (
                          <input
                            type="number" step="any"
                            placeholder="value"
                            value={wc.value}
                            onChange={e => setWriteCtrl(p => ({ ...p, [m.id]: { ...wc, value: e.target.value, result: null } }))}
                            className="text-[10px] px-2 py-1 bg-bg-input border border-border rounded text-white w-16 focus:outline-none focus:border-accent-primary"
                            onKeyDown={e => e.key === 'Enter' && wc.value && writeValue(m, wc.priority, wc.value)}
                          />
                        );
                      })()}
                      <button
                        disabled={!wc.value || wc.loading}
                        onClick={() => writeValue(m, wc.priority, wc.value)}
                        title="Write value at this priority"
                        className="flex items-center gap-1 px-2 py-1 bg-accent-primary/80 hover:bg-accent-primary rounded text-white text-[10px] font-bold disabled:opacity-40 transition-all">
                        {wc.loading ? <Loader2 size={10} className="animate-spin" /> : <Send size={10} />}
                        Write
                      </button>
                      <button
                        disabled={wc.loading}
                        onClick={() => relinquishOne(m, parseInt(wc.priority))}
                        title={`Release P${wc.priority}`}
                        className="flex items-center gap-1 px-2 py-1 bg-error/20 hover:bg-error/30 border border-error/30 rounded text-error text-[10px] font-bold disabled:opacity-40 transition-all">
                        <Minus size={10} />
                        Rel
                      </button>
                    </div>
                    {wc.result && <span className="text-[10px] text-right">{wc.result}</span>}
                  </div>
                </div>

                {/* Expanded: full 16-level PA detail with color-coded cards */}
                {isExpanded && pa && (
                  <div className="px-4 py-3 border-t border-border bg-bg-primary/40">
                    <div className="text-[10px] text-text-muted mb-2 font-bold uppercase tracking-wider">Priority Detail (1 = highest priority)</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-1.5">
                      {Array.from({length: 16}, (_, i) => i + 1).map(p => {
                        const val = paArr[String(p)];
                        const active = val != null && val !== 'null' && val !== 'Null';
                        return (
                          <div key={p} className={`flex flex-col gap-1 px-2.5 py-2 rounded-lg cursor-pointer hover:brightness-110 transition-all
                            ${active ? 'bg-warning/10 border border-warning/30' : 'bg-bg-input/30 border border-transparent'}`}
                            onClick={e => { e.stopPropagation(); setWriteCtrl(prev => ({ ...prev, [m.id]: { ...wc, priority: String(p), result: null } })); }}>
                            <span className={`text-xs font-bold w-6 h-6 rounded flex items-center justify-center self-start ${active ? PA_COLOR(p) : 'bg-bg-input text-text-muted'}`}>{p}</span>
                            <div className="text-[8px] text-text-muted leading-tight">{PA_LABELS[p]}</div>
                            <div className={`text-xs font-bold ${active ? 'text-warning' : 'text-text-muted/40'}`}>
                              {active ? String(val) : '—'}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <div className="text-[9px] text-text-muted mt-1.5">💡 Click một ô để chọn priority đó trong control trên</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
