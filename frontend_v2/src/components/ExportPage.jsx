import React, { useState, useEffect, useCallback } from 'react';
import { Download, FileText, Activity, RefreshCw, Calendar, Filter } from 'lucide-react';

const API = '/api';

function StatCard({ icon: Icon, label, value, sub, color = 'text-accent-primary' }) {
  return (
    <div className="glass-card p-4 flex items-center gap-3">
      <div className={`p-2 rounded-lg bg-current/10 ${color}`}>
        <Icon size={18} className={color} />
      </div>
      <div>
        <div className="text-xl font-bold text-white">{value?.toLocaleString() ?? '—'}</div>
        <div className="text-xs text-text-muted">{label}</div>
        {sub && <div className="text-[10px] text-text-muted mt-0.5">{sub}</div>}
      </div>
    </div>
  );
}

function fmt(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' });
}

function toInputDate(iso) {
  if (!iso) return '';
  return iso.slice(0, 10); // YYYY-MM-DD
}

export function ExportPage() {
  const [summary, setSummary] = useState(null);
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(false);

  // History export filters
  const [hFrom, setHFrom] = useState('');
  const [hTo, setHTo] = useState('');
  const [hMappings, setHMappings] = useState([]);  // selected mapping IDs

  // Events export filters
  const [eFrom, setEFrom] = useState('');
  const [eTo, setETo] = useState('');
  const [eType, setEType] = useState('');

  const fetchSummary = useCallback(async () => {
    setLoading(true);
    try {
      const [s, m] = await Promise.all([
        fetch(`${API}/export/summary`).then(r => r.json()),
        fetch(`${API}/mappings`).then(r => r.json()),
      ]);
      setSummary(s);
      setMappings(m.mappings || []);
      // Pre-fill dates from summary
      if (s.history_oldest) setHFrom(toInputDate(s.history_oldest));
      if (s.history_newest) setHTo(toInputDate(s.history_newest));
      if (s.event_oldest) setEFrom(toInputDate(s.event_oldest));
      if (s.event_newest) setETo(toInputDate(s.event_newest));
    } catch (e) { console.error(e); }
    setLoading(false);
  }, []);

  useEffect(() => { fetchSummary(); }, [fetchSummary]);

  const buildParams = (obj) => {
    const p = new URLSearchParams();
    Object.entries(obj).forEach(([k, v]) => { if (v) p.set(k, v); });
    return p.toString();
  };

  const downloadHistory = () => {
    const params = buildParams({
      from_ts: hFrom ? `${hFrom}T00:00:00` : '',
      to_ts: hTo ? `${hTo}T23:59:59` : '',
      mapping_id: hMappings.join(','),
    });
    window.open(`${API}/export/history.csv?${params}`, '_blank');
  };

  const downloadEvents = () => {
    const params = buildParams({
      from_ts: eFrom ? `${eFrom}T00:00:00` : '',
      to_ts: eTo ? `${eTo}T23:59:59` : '',
      event_type: eType,
    });
    window.open(`${API}/export/events.csv?${params}`, '_blank');
  };

  const toggleMapping = (id) => setHMappings(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);
  const selectAll = () => setHMappings(mappings.map(m => m.id));
  const clearAll = () => setHMappings([]);

  const inputCls = "w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus";

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">Data Export</h2>
          <p className="text-xs text-text-muted mt-1">Xuất dữ liệu lịch sử ra file CSV</p>
        </div>
        <button onClick={fetchSummary} disabled={loading} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all">
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <StatCard icon={Activity} label="Point records" value={summary?.history_count}
          sub={`${fmt(summary?.history_oldest)} → ${fmt(summary?.history_newest)}`} color="text-accent-primary" />
        <StatCard icon={FileText} label="Event logs" value={summary?.event_count}
          sub={`${fmt(summary?.event_oldest)} → ${fmt(summary?.event_newest)}`} color="text-purple-400" />
        <StatCard icon={Calendar} label="Points configured" value={mappings.length} color="text-green-400" />
        <StatCard icon={Download} label="Max rows/CSV" value="200,000" sub="history · 50k events" color="text-yellow-400" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">

        {/* Point History Export */}
        <div className="glass-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-lg bg-accent-primary/15 flex items-center justify-center">
              <Activity size={16} className="text-accent-primary" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">Point History</h3>
              <p className="text-[11px] text-text-muted">Giá trị đo theo thời gian</p>
            </div>
          </div>

          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-text-muted block mb-1">Từ ngày</label>
                <input type="date" className={inputCls} value={hFrom} onChange={e => setHFrom(e.target.value)} />
              </div>
              <div>
                <label className="text-xs text-text-muted block mb-1">Đến ngày</label>
                <input type="date" className={inputCls} value={hTo} onChange={e => setHTo(e.target.value)} />
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs text-text-muted">Chọn điểm (bỏ trống = tất cả)</label>
                <div className="flex gap-2">
                  <button onClick={selectAll} className="text-[10px] text-accent-primary hover:underline">All</button>
                  <button onClick={clearAll} className="text-[10px] text-text-muted hover:underline">Clear</button>
                </div>
              </div>
              <div className="max-h-36 overflow-y-auto space-y-1 border border-border/40 rounded-lg p-2 bg-bg-input/40">
                {mappings.length === 0 && <p className="text-[11px] text-text-muted text-center py-2">No points configured</p>}
                {mappings.map(m => (
                  <label key={m.id} className="flex items-center gap-2 cursor-pointer group">
                    <input type="checkbox" checked={hMappings.includes(m.id)} onChange={() => toggleMapping(m.id)}
                      className="w-3 h-3 accent-blue-500" />
                    <span className="text-xs text-text-secondary group-hover:text-white truncate">
                      {m.label || `${m.object_type}:${m.object_instance}`}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <button onClick={downloadHistory}
              className="w-full flex items-center justify-center gap-2 py-2.5 bg-gradient-to-r from-accent-primary to-blue-600 rounded-lg text-white text-sm font-medium hover:opacity-90 transition-opacity">
              <Download size={15} /> Download point_history.csv
            </button>
          </div>
        </div>

        {/* Event Log Export */}
        <div className="glass-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-lg bg-purple-500/15 flex items-center justify-center">
              <FileText size={16} className="text-purple-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">Event Log</h3>
              <p className="text-[11px] text-text-muted">Alarm, schedule, system events</p>
            </div>
          </div>

          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-text-muted block mb-1">Từ ngày</label>
                <input type="date" className={inputCls} value={eFrom} onChange={e => setEFrom(e.target.value)} />
              </div>
              <div>
                <label className="text-xs text-text-muted block mb-1">Đến ngày</label>
                <input type="date" className={inputCls} value={eTo} onChange={e => setETo(e.target.value)} />
              </div>
            </div>

            <div>
              <label className="text-xs text-text-muted block mb-1">Event Type (bỏ trống = tất cả)</label>
              <select className={inputCls} value={eType} onChange={e => setEType(e.target.value)}>
                <option value="">All events</option>
                <option value="alarm">alarm</option>
                <option value="schedule">schedule</option>
                <option value="gateway">gateway</option>
                <option value="device">device</option>
              </select>
            </div>

            {/* Column reference */}
            <div className="p-2.5 bg-bg-input rounded-lg border border-border/40">
              <p className="text-[10px] text-text-muted font-bold mb-1">CSV columns:</p>
              <p className="text-[10px] text-text-secondary font-mono">timestamp, event_type, device_id, mapping_id, severity, message</p>
            </div>

            <button onClick={downloadEvents}
              className="w-full flex items-center justify-center gap-2 py-2.5 bg-gradient-to-r from-purple-600 to-purple-800 rounded-lg text-white text-sm font-medium hover:opacity-90 transition-opacity">
              <Download size={15} /> Download event_log.csv
            </button>
          </div>
        </div>
      </div>

      {/* Quick examples */}
      <div className="mt-4 glass-card p-4">
        <p className="text-xs font-bold text-text-muted mb-2">💡 Mở bằng Excel / Google Sheets / Python:</p>
        <div className="font-mono text-[11px] text-text-secondary space-y-1">
          <p><span className="text-accent-primary">pandas:</span> df = pd.read_csv("point_history.csv", parse_dates=["timestamp"])</p>
          <p><span className="text-green-400">power query:</span> Data → Get Data → From Text/CSV → point_history.csv</p>
        </div>
      </div>
    </div>
  );
}
