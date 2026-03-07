import React, { useState, useEffect, useCallback } from 'react';
import { LineChart, Plus, Trash2, RefreshCw, TrendingUp } from 'lucide-react';

const API = '/api';

export function ChartsPage() {
  const [charts, setCharts] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [historyData, setHistoryData] = useState({});
  const [stats, setStats] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [c, m, s] = await Promise.all([
        fetch(`${API}/charts`).then(r => r.json()),
        fetch(`${API}/mappings`).then(r => r.json()),
        fetch(`${API}/history/stats/overview`).then(r => r.json()).catch(() => null),
      ]);
      setCharts(c.charts || []);
      setMappings(m.mappings || []);
      setStats(s);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const loadHistory = useCallback(async (mappingId) => {
    if (historyData[mappingId]) return;
    try {
      const res = await fetch(`${API}/history/${mappingId}?limit=100`);
      const data = await res.json();
      setHistoryData(prev => ({ ...prev, [mappingId]: data.records || [] }));
    } catch (e) { console.error(e); }
  }, [historyData]);

  const deleteChart = async (id) => {
    if (!confirm('Delete this chart?')) return;
    try {
      await fetch(`${API}/charts/${id}`, { method: 'DELETE' });
      await fetchData();
    } catch (e) { console.error(e); }
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">Charts</h2>
          <p className="text-xs text-text-muted mt-1">{charts.length} charts configured</p>
        </div>
        <button onClick={fetchData} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all">
          <RefreshCw size={16} />
        </button>
      </div>

      {/* History Stats */}
      {stats && (
        <div className="glass-card p-5 mb-6">
          <div className="text-xs uppercase tracking-widest text-text-muted font-semibold mb-3">History Database</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div><div className="text-2xl font-bold text-gradient">{stats.total_records?.toLocaleString() || 0}</div><div className="text-xs text-text-muted">Records</div></div>
            <div><div className="text-2xl font-bold text-white">{stats.total_events?.toLocaleString() || 0}</div><div className="text-xs text-text-muted">Events</div></div>
            <div><div className="text-2xl font-bold text-white">{stats.db_size_mb ? `${stats.db_size_mb.toFixed(1)} MB` : '—'}</div><div className="text-xs text-text-muted">DB Size</div></div>
            <div><div className="text-2xl font-bold text-white">{stats.unique_mappings || 0}</div><div className="text-xs text-text-muted">Tracked Points</div></div>
          </div>
        </div>
      )}

      {/* Quick History View per Mapping */}
      <div className="text-xs uppercase tracking-widest text-text-muted font-semibold mb-3">Point History</div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {mappings.filter(m => m.enabled).slice(0, 12).map(m => {
          const records = historyData[m.id] || [];
          const hasData = records.length > 0;
          const lastVal = hasData ? records[records.length - 1] : null;
          return (
            <div key={m.id} className="glass-card p-4 cursor-pointer hover:border-accent-primary/50" onClick={() => loadHistory(m.id)}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <TrendingUp size={14} className="text-accent-primary" />
                  <span className="text-sm font-medium text-white truncate">{m.label || `${m.object_type}:${m.object_instance}`}</span>
                </div>
                <span className="text-xs text-text-muted">Dev {m.device_id}</span>
              </div>
              {hasData ? (
                <div className="flex items-end gap-0.5 h-12 mt-2">
                  {records.slice(-40).map((r, i) => {
                    const val = parseFloat(r.value);
                    const min = Math.min(...records.slice(-40).map(x => parseFloat(x.value) || 0));
                    const max = Math.max(...records.slice(-40).map(x => parseFloat(x.value) || 0));
                    const range = max - min || 1;
                    const h = Math.max(4, ((val - min) / range) * 100);
                    return <div key={i} className="flex-1 bg-accent-primary/40 rounded-t" style={{ height: `${h}%` }} title={`${r.value} @ ${r.timestamp}`} />;
                  })}
                </div>
              ) : (
                <div className="text-xs text-text-muted mt-2">Click to load history…</div>
              )}
              {lastVal && <div className="text-xs text-text-secondary mt-1">Last: <span className="font-semibold text-white">{lastVal.value}</span> {m.units || ''}</div>}
            </div>
          );
        })}
      </div>

      {mappings.length === 0 && (
        <div className="glass-card p-12 text-center mt-4">
          <LineChart size={40} className="mx-auto text-text-muted mb-4 opacity-40" />
          <p className="text-text-secondary text-sm">No mappings to chart. Add points first.</p>
        </div>
      )}
    </div>
  );
}
