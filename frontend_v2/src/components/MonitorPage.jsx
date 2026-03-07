import React, { useState, useEffect, useCallback, useRef } from 'react';
import { RefreshCw, Loader2, Eye, Filter, Unlock } from 'lucide-react';

const API = '/api';

const TYPE_SHORT = {
  analogInput: 'AI', analogOutput: 'AO', analogValue: 'AV',
  binaryInput: 'BI', binaryOutput: 'BO', binaryValue: 'BV',
  multiStateInput: 'MSI', multiStateOutput: 'MSO', multiStateValue: 'MSV',
  'analog-input': 'AI', 'analog-output': 'AO', 'analog-value': 'AV',
  'binary-input': 'BI', 'binary-output': 'BO', 'binary-value': 'BV',
};

export function MonitorPage() {
  const [mappings, setMappings] = useState([]);
  const [paData, setPaData] = useState({});
  const [loading, setLoading] = useState(false);
  const [loadingPA, setLoadingPA] = useState({});
  const [filter, setFilter] = useState('');
  const [groupFilter, setGroupFilter] = useState('ALL');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const intervalRef = useRef(null);

  const fetchMappings = useCallback(async () => {
    try {
      const res = await fetch(`${API}/mappings`);
      const data = await res.json();
      setMappings(data.mappings || []);
    } catch (e) { console.error(e); }
  }, []);

  const fetchAllPA = useCallback(async (points) => {
    const targets = points || mappings;
    if (targets.length === 0) return;
    setLoading(true);
    const results = {};
    await Promise.allSettled(targets.map(async m => {
      try {
        const res = await fetch(`${API}/bacnet/priority_array/${m.device_id}/${m.object_type}/${m.object_instance}`);
        const data = await res.json();
        results[m.id] = { pa: data.priority_array || {}, pv: data.present_value };
      } catch (e) { /* skip */ }
    }));
    setPaData(prev => ({ ...prev, ...results }));
    setLoading(false);
  }, [mappings]);

  useEffect(() => {
    fetchMappings();
  }, [fetchMappings]);

  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(() => fetchMappings(), 5000);
    }
    return () => clearInterval(intervalRef.current);
  }, [autoRefresh, fetchMappings]);

  const handleRefreshAllPA = () => fetchAllPA();
  const handleRefreshSinglePA = async (m) => {
    setLoadingPA(prev => ({ ...prev, [m.id]: true }));
    try {
      const res = await fetch(`${API}/bacnet/priority_array/${m.device_id}/${m.object_type}/${m.object_instance}`);
      const data = await res.json();
      setPaData(prev => ({ ...prev, [m.id]: { pa: data.priority_array || {}, pv: data.present_value } }));
    } catch (e) { /* skip */ }
    setLoadingPA(prev => ({ ...prev, [m.id]: false }));
  };

  const handleRelinquishAll = async (m) => {
    try {
      await fetch(`${API}/bacnet/release`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: m.device_id, object_type: m.object_type, object_instance: m.object_instance, priority: 'all' })
      });
      setTimeout(() => handleRefreshSinglePA(m), 500);
    } catch (e) { console.error(e); }
  };

  const groups = [...new Set(mappings.flatMap(m => (m.group || '').split(',').map(s => s.trim()).filter(Boolean)))];
  const filtered = mappings.filter(m => {
    if (filter && !(m.label || '').toLowerCase().includes(filter.toLowerCase()) && !String(m.object_instance).includes(filter)) return false;
    if (groupFilter !== 'ALL' && !(m.group || '').includes(groupFilter)) return false;
    return true;
  });

  return (
    <div className="p-6 flex flex-col h-screen">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Eye size={24} className="text-accent-primary" /> Live Monitor
          </h2>
          <p className="text-xs text-text-muted mt-1">{filtered.length} points • {Object.keys(paData).length} with PA data</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} className="accent-accent-primary" />
            Auto-refresh
          </label>
          <button onClick={handleRefreshAllPA} disabled={loading}
            className="flex items-center gap-2 px-3 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-xs font-medium disabled:opacity-50">
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Load All PA
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <input type="text" placeholder="Search point..." value={filter} onChange={e => setFilter(e.target.value)}
          className="px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted w-48 focus:outline-none focus:border-border-focus" />
        <div className="flex gap-1 flex-wrap">
          <button onClick={() => setGroupFilter('ALL')}
            className={`px-2 py-1 rounded-full text-[10px] font-bold ${groupFilter === 'ALL' ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/40' : 'bg-bg-input border border-border text-text-secondary'}`}>
            ALL
          </button>
          {groups.map(g => (
            <button key={g} onClick={() => setGroupFilter(g)}
              className={`px-2 py-1 rounded-full text-[10px] font-bold ${groupFilter === g ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/40' : 'bg-bg-input border border-border text-text-secondary'}`}>
              {g}
            </button>
          ))}
        </div>
      </div>

      {/* Monitor Grid */}
      <div className="flex-1 overflow-y-auto space-y-2 min-h-[300px]">
        {filtered.map(m => {
          const pa = paData[m.id];
          const pv = pa ? pa.pv : m.last_value;
          const paArr = pa ? pa.pa : {};
          const actives = Object.entries(paArr).filter(([, v]) => v != null && v !== 'null');
          const ot = (m.object_type || '').toLowerCase();
          const isBinary = ot.includes('binary');
          const label = m.label || `${m.object_type}:${m.object_instance}`;
          const short = label.split(/[.\/\\]/).pop() || label;
          const isLoadingPA = loadingPA[m.id];

          return (
            <div key={m.id} className="glass-card px-4 py-3 flex items-center gap-3">
              {/* Type badge */}
              <span className="px-1.5 py-0.5 rounded bg-info/15 text-info text-[10px] font-bold min-w-[28px] text-center">
                {TYPE_SHORT[m.object_type] || m.object_type?.slice(0, 3)?.toUpperCase()}
              </span>

              {/* Label */}
              <div className="min-w-[160px]">
                <div className="text-sm font-medium text-white truncate">{short}</div>
                <div className="text-[10px] text-text-muted">Dev {m.device_id} • Inst {m.object_instance}</div>
              </div>

              {/* Present Value */}
              <div className="min-w-[100px] text-right">
                <span className={`text-sm font-bold ${isBinary ? (pv === 'active' || pv === 1 ? 'text-success' : 'text-error') : 'text-white'}`}>
                  {pv != null ? String(pv) : '—'}
                </span>
                {m.units && <span className="text-[10px] text-text-muted ml-1">{m.units}</span>}
              </div>

              {/* Mini PA indicator */}
              <div className="flex-1 flex items-center gap-0.5 min-w-[200px]">
                {Array.from({ length: 16 }, (_, i) => i + 1).map(p => {
                  const val = paArr[String(p)];
                  const active = val != null && val !== 'null';
                  return (
                    <div key={p} title={`P${p}: ${active ? val : 'null'}`}
                      className={`w-3 h-3 rounded-sm text-[6px] flex items-center justify-center font-bold ${active ? 'bg-success/60 text-white' : 'bg-bg-input/60 text-transparent'}`}>
                      {p}
                    </div>
                  );
                })}
              </div>

              {/* Active priorities text */}
              <div className="min-w-[80px] text-right">
                {actives.length > 0 ? (
                  <span className="text-[10px] text-accent-primary font-bold">
                    {actives.map(([p, v]) => `P${p}=${v}`).join(', ')}
                  </span>
                ) : pa ? (
                  <span className="text-[10px] text-text-muted">No overrides</span>
                ) : (
                  <span className="text-[10px] text-text-muted italic">PA not loaded</span>
                )}
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1">
                <button onClick={() => handleRefreshSinglePA(m)} disabled={isLoadingPA}
                  className="p-1 rounded hover:bg-bg-card text-text-muted hover:text-white disabled:opacity-50" title="Load PA">
                  {isLoadingPA ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                </button>
                {actives.length > 0 && (
                  <button onClick={() => handleRelinquishAll(m)}
                    className="p-1 rounded hover:bg-error/10 text-text-muted hover:text-error" title="Relinquish All">
                    <Unlock size={12} />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
