import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Activity, Server, Wifi, Database, MonitorSmartphone, Play, Square,
  Search, Filter, RefreshCw, ChevronDown, X, AlertTriangle, CheckCircle,
  WifiOff, Info, Clock,
} from 'lucide-react';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';

const API = '/api';

// ── Helpers ──────────────────────────────────────────────────────────────────
function formatUptime(seconds) {
  if (!seconds) return '—';
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function relTime(ts) {
  if (!ts) return '';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return new Date(ts).toLocaleDateString('vi-VN');
}

function fmtTime(ts) {
  if (!ts) return '';
  return new Date(ts).toLocaleString('vi-VN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

// ── Stat Card ─────────────────────────────────────────────────────────────────
const StatCard = ({ label, value, detail, icon: Icon, accent }) => (
  <div className="glass-card p-4 relative overflow-hidden group hover:border-accent-primary/30 transition-all">
    <div className={`absolute top-0 left-0 right-0 h-0.5 ${accent || 'bg-gradient-to-r from-accent-primary to-transparent'} opacity-70`} />
    <div className="flex items-start justify-between">
      <div>
        <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted font-bold mb-1.5">{label}</div>
        <div className="text-2xl font-bold text-white">{value ?? '—'}</div>
        <div className="text-[11px] text-text-secondary mt-1">{detail}</div>
      </div>
      {Icon && <Icon size={20} className="text-text-muted opacity-40 group-hover:opacity-60 transition-opacity mt-0.5" />}
    </div>
  </div>
);

// ── Meter Bar ─────────────────────────────────────────────────────────────────
const MeterBar = ({ label, percent, detail, thresholds = [50, 80] }) => {
  const p = percent || 0;
  const color = p > thresholds[1] ? 'bg-error' : p > thresholds[0] ? 'bg-warning' : 'bg-success';
  return (
    <div className="flex-1 min-w-[180px]">
      <div className="flex justify-between text-xs mb-1">
        <span className="text-text-muted font-bold uppercase text-[10px] tracking-wider">{label}</span>
        <span className="font-semibold text-white">{detail || `${p.toFixed(1)}%`}</span>
      </div>
      <div className="h-2 rounded-full bg-bg-input overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${Math.min(p, 100)}%` }} />
      </div>
    </div>
  );
};

// ── Custom Tooltip for charts ──────────────────────────────────────────────────
const ChartTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  const hour = label ? new Date(label + 'Z').toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }) : '';
  return (
    <div className="bg-bg-secondary border border-border rounded-lg p-3 text-xs shadow-xl">
      <div className="text-text-muted mb-2">{hour}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 mb-1">
          <div className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-white font-semibold">{p.value}</span>
          <span className="text-text-muted">{p.name}</span>
        </div>
      ))}
    </div>
  );
};

// ── Event Row ─────────────────────────────────────────────────────────────────
const EVENT_META = {
  device_offline: { color: 'text-error', bg: 'bg-error/10 border-error/20', dot: 'bg-error', icon: WifiOff,    label: 'Offline' },
  device_online:  { color: 'text-success', bg: 'bg-success/10 border-success/20', dot: 'bg-success', icon: CheckCircle, label: 'Online' },
  cov_fallback:   { color: 'text-warning', bg: 'bg-warning/10 border-warning/20', dot: 'bg-warning', icon: AlertTriangle, label: 'COV→Poll' },
  anomaly:        { color: 'text-error', bg: 'bg-error/10 border-error/20', dot: 'bg-error', icon: AlertTriangle, label: 'Anomaly' },
  system:         { color: 'text-info', bg: 'bg-info/10 border-info/20', dot: 'bg-info', icon: Info, label: 'System' },
};
const SEV_META = {
  critical: { dot: 'bg-error', color: 'text-error' },
  warning:  { dot: 'bg-warning', color: 'text-warning' },
  info:     { dot: 'bg-info', color: 'text-info' },
};

function EventRow({ ev }) {
  const meta = EVENT_META[ev.event_type] || { dot: 'bg-info', bg: 'bg-bg-input/30 border-border/30', label: ev.event_type };
  const sevMeta = SEV_META[ev.severity] || SEV_META.info;
  const DotColor = ev.event_type === 'device_offline' ? 'bg-error'
    : ev.event_type === 'device_online' ? 'bg-success'
    : sevMeta.dot;
  return (
    <div className={`flex items-start gap-3 px-3 py-2.5 rounded-lg border transition-colors hover:brightness-110 ${meta.bg || 'bg-bg-input/30 border-border/30'}`}>
      <div className={`mt-1.5 w-2 h-2 rounded-full flex-shrink-0 ${DotColor}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium text-white truncate">{ev.message || ev.event_type}</span>
          {ev.event_type && (
            <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded ${meta.color || ''} bg-white/5`}>
              {meta.label || ev.event_type}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 mt-0.5 flex-wrap">
          {ev.device_id && <span className="text-[10px] text-text-muted">Dev {ev.device_id}</span>}
          {ev.data?.address && <span className="text-[10px] text-accent-primary font-mono">{ev.data.address}</span>}
          {ev.data?.network && <span className="text-[10px] text-info/70">{ev.data.network}</span>}
          <span className="text-[10px] text-text-muted ml-auto">{fmtTime(ev.timestamp)}</span>
          <span className="text-[9px] text-text-muted opacity-60">{relTime(ev.timestamp)}</span>
        </div>
      </div>
    </div>
  );
}

// ── Events Panel ──────────────────────────────────────────────────────────────
const TIME_PRESETS = [
  { label: '1h', hours: 1 }, { label: '6h', hours: 6 }, { label: '24h', hours: 24 },
  { label: '7d', hours: 168 }, { label: 'All', hours: 0 },
];
const EVENT_TYPES = [
  { value: '', label: 'All Types' },
  { value: 'device_offline', label: '🔴 Offline' },
  { value: 'device_online', label: '🟢 Online' },
  { value: 'cov_fallback', label: '⚡ COV→Poll' },
  { value: 'anomaly', label: '⚠️ Anomaly' },
  { value: 'system', label: 'ℹ️ System' },
];
const SEVERITIES = [
  { value: '', label: 'All Severity' },
  { value: 'critical', label: '🔴 Critical' },
  { value: 'warning', label: '🟡 Warning' },
  { value: 'info', label: '🔵 Info' },
];

function EventsPanel({ devices }) {
  const [events, setEvents] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(0);
  const PAGE = 30;

  // Filters
  const [timePreset, setTimePreset] = useState(24); // hours; 0 = all
  const [eventType, setEventType] = useState('');
  const [severity, setSeverity] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [search, setSearch] = useState('');

  const buildParams = useCallback(() => {
    const p = new URLSearchParams();
    if (eventType) p.set('event_type', eventType);
    if (severity) p.set('severity', severity);
    if (deviceId) p.set('device_id', deviceId);
    if (search) p.set('search', search);
    if (timePreset > 0) {
      const from = new Date(Date.now() - timePreset * 3600 * 1000).toISOString();
      p.set('from_ts', from);
    }
    p.set('limit', PAGE);
    p.set('offset', page * PAGE);
    return p.toString();
  }, [eventType, severity, deviceId, search, timePreset, page]);

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/events?${buildParams()}`);
      const d = await r.json();
      setEvents(d.events || []);
      setTotal(d.total || 0);
    } catch (e) { console.error(e); } finally { setLoading(false); }
  }, [buildParams]);

  useEffect(() => { setPage(0); }, [eventType, severity, deviceId, search, timePreset]);
  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  // Auto-refresh every 10s
  useEffect(() => {
    const t = setInterval(fetchEvents, 10000);
    return () => clearInterval(t);
  }, [fetchEvents]);

  return (
    <div className="glass-card p-5 flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted font-bold">Recent Events</div>
          <div className="text-[10px] text-text-muted mt-0.5">{total} events matched</div>
        </div>
        <button onClick={fetchEvents} className="p-1.5 rounded-lg border border-border text-text-muted hover:text-white hover:border-accent-primary/50 transition-all">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Filters row */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* Time presets */}
        <div className="flex gap-1 bg-bg-input rounded-lg p-1">
          {TIME_PRESETS.map(({ label, hours }) => (
            <button key={hours} onClick={() => setTimePreset(hours)}
              className={`px-2.5 py-1 rounded text-[10px] font-bold transition-all ${timePreset === hours ? 'bg-accent-primary text-white shadow' : 'text-text-muted hover:text-white'}`}>
              {label}
            </button>
          ))}
        </div>

        {/* Event type */}
        <select value={eventType} onChange={e => setEventType(e.target.value)}
          className="px-2 py-1.5 bg-bg-input border border-border rounded-lg text-[11px] text-white focus:outline-none focus:border-border-focus min-w-[120px]">
          {EVENT_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>

        {/* Severity */}
        <select value={severity} onChange={e => setSeverity(e.target.value)}
          className="px-2 py-1.5 bg-bg-input border border-border rounded-lg text-[11px] text-white focus:outline-none focus:border-border-focus min-w-[120px]">
          {SEVERITIES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>

        {/* Device filter */}
        <select value={deviceId} onChange={e => setDeviceId(e.target.value)}
          className="px-2 py-1.5 bg-bg-input border border-border rounded-lg text-[11px] text-white focus:outline-none focus:border-border-focus min-w-[120px]">
          <option value="">All Devices</option>
          {devices.map(d => (
            <option key={d.device_id} value={d.device_id}>
              {d.name || `Dev ${d.device_id}`} ({d.device_id})
            </option>
          ))}
        </select>

        {/* Search message */}
        <div className="relative flex-1 min-w-[140px]">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input type="text" placeholder="Search message, address…" value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-7 pr-3 py-1.5 bg-bg-input border border-border rounded-lg text-[11px] text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus" />
          {search && <button onClick={() => setSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-white"><X size={11} /></button>}
        </div>
      </div>

      {/* Event list */}
      <div className="space-y-1.5 flex-1 overflow-y-auto" style={{ maxHeight: 420 }}>
        {loading && events.length === 0 ? (
          <div className="text-xs text-text-muted text-center py-8">Loading…</div>
        ) : events.length === 0 ? (
          <div className="text-xs text-text-muted text-center py-8">No events found for this filter</div>
        ) : events.map(ev => <EventRow key={ev.id} ev={ev} />)}
      </div>

      {/* Pagination */}
      {total > PAGE && (
        <div className="flex items-center justify-between pt-2 border-t border-border/30">
          <span className="text-[10px] text-text-muted">
            {page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} of {total}
          </span>
          <div className="flex gap-1">
            <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
              className="px-3 py-1 text-[10px] rounded border border-border text-text-muted hover:text-white hover:border-accent-primary/40 disabled:opacity-30 transition-all">
              ← Prev
            </button>
            <button onClick={() => setPage(p => p + 1)} disabled={(page + 1) * PAGE >= total}
              className="px-3 py-1 text-[10px] rounded border border-border text-text-muted hover:text-white hover:border-accent-primary/40 disabled:opacity-30 transition-all">
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Dashboard ─────────────────────────────────────────────────────────────
export function Dashboard() {
  const [status, setStatus]   = useState(null);
  const [health, setHealth]   = useState(null);
  const [devices, setDevices] = useState([]);
  const [chartData, setChartData] = useState([]);
  const [chartHours, setChartHours] = useState(24);
  const intervalRef = useRef(null);

  const fetchAll = useCallback(async () => {
    try {
      const [st, hl, dv, ch] = await Promise.allSettled([
        fetch(`${API}/status`).then(r => r.json()),
        fetch(`${API}/health`).then(r => r.json()),
        fetch(`${API}/bacnet/devices`).then(r => r.json()),

        fetch(`${API}/events/online-chart?hours=${chartHours}`).then(r => r.json()),
      ]);
      if (st.status === 'fulfilled') setStatus(st.value);
      if (hl.status === 'fulfilled') setHealth(hl.value);
      if (dv.status === 'fulfilled') setDevices(dv.value.devices || dv.value || []);
      if (ch.status === 'fulfilled') setChartData(ch.value.series || []);
    } catch (e) { console.error(e); }
  }, [chartHours]);

  useEffect(() => {
    fetchAll();
    intervalRef.current = setInterval(fetchAll, 15000);
    return () => clearInterval(intervalRef.current);
  }, [fetchAll]);

  const gatewayRunning = status?.gateway === 'running';

  const onlineDevices  = devices.filter(d => d.status === 'online').length;
  const offlineDevices = devices.filter(d => d.status !== 'online').length;

  const handleToggle = async () => {
    try {
      await fetch(gatewayRunning ? `${API}/gateway/stop` : `${API}/gateway/start`, { method: 'POST' });
      setTimeout(fetchAll, 800);
    } catch (e) { console.error(e); }
  };

  // Format chart X-axis
  const fmtHour = (str) => {
    if (!str) return '';
    try {
      const d = new Date(str + 'Z');
      return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
    } catch { return str.slice(11, 16); }
  };

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">System Stability</h2>
          <p className="text-xs text-text-muted mt-0.5">Gateway health &amp; device monitoring</p>
        </div>
        <button onClick={handleToggle}
          className={`flex items-center gap-2 px-5 py-2.5 rounded-lg text-white font-medium transition-all hover:-translate-y-0.5 ${
            gatewayRunning
              ? 'bg-error/80 hover:bg-error shadow-[0_4px_20px_rgba(255,60,60,0.3)]'
              : 'bg-accent-gradient shadow-[0_4px_20px_var(--color-accent-glow)]'
          }`}>
          {gatewayRunning ? <><Square size={16} fill="white" /> Stop</> : <><Play size={16} fill="white" /> Start Gateway</>}
        </button>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Gateway" icon={Activity}
          value={gatewayRunning ? 'Running' : (status?.gateway?.toUpperCase() || '—')}
          detail={`Uptime: ${formatUptime(status?.uptime_seconds)}`}
          accent={gatewayRunning ? 'bg-gradient-to-r from-success to-transparent' : 'bg-gradient-to-r from-error to-transparent'} />
        <StatCard label="Online Devices" icon={Wifi}
          value={onlineDevices}
          detail={`${offlineDevices} offline · ${devices.length} total`}
          accent="bg-gradient-to-r from-success to-transparent" />
        <StatCard label="Active Points" icon={Database}
          value={status?.active_mappings ?? '—'}
          detail="Points being polled / COV" />
        <StatCard label="MQTT" icon={MonitorSmartphone}
          value={status?.mqtt_connected ? 'Online' : 'Offline'}
          detail={status?.mqtt_connected ? '🟢 Broker connected' : '🔴 Disconnected'}
          accent={status?.mqtt_connected ? 'bg-gradient-to-r from-success to-transparent' : 'bg-gradient-to-r from-error to-transparent'} />
      </div>


      {/* System Resources */}
      {health && (
        <div className="glass-card p-5">
          <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted font-bold mb-4">System Resources</div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            <MeterBar label="CPU" percent={health.cpu_percent} detail={`${health.cpu_percent?.toFixed(1)}%`} />
            <MeterBar label="RAM" percent={health.ram_percent}
              detail={`${(health.ram_used_mb / 1024).toFixed(1)} / ${(health.ram_total_mb / 1024).toFixed(1)} GB`}
              thresholds={[60, 85]} />
            <MeterBar label="Disk" percent={health.disk_percent}
              detail={`${health.disk_used_gb?.toFixed(1)} / ${health.disk_total_gb?.toFixed(1)} GB`}
              thresholds={[70, 90]} />
            {health.cpu_temp != null && (
              <div className="flex-1 min-w-[180px]">
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-text-muted font-bold uppercase text-[10px] tracking-wider">Temperature</span>
                  <span className={`font-bold ${health.cpu_temp > 70 ? 'text-error' : health.cpu_temp > 55 ? 'text-warning' : 'text-success'}`}>
                    {health.cpu_temp}°C
                  </span>
                </div>
                <div className="h-2 rounded-full bg-bg-input overflow-hidden">
                  <div className={`h-full rounded-full ${health.cpu_temp > 70 ? 'bg-error' : health.cpu_temp > 55 ? 'bg-warning' : 'bg-success'}`}
                    style={{ width: `${Math.min(health.cpu_temp, 100)}%` }} />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Online/Offline Chart */}
      <div className="glass-card p-5">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted font-bold">Device Online ↔ Offline Events</div>
            <div className="text-[10px] text-text-muted mt-0.5">Number of connect/disconnect events per hour</div>
          </div>
          <div className="flex gap-1 bg-bg-input rounded-lg p-1">
            {[6, 24, 72, 168].map(h => (
              <button key={h} onClick={() => setChartHours(h)}
                className={`px-2.5 py-1 rounded text-[10px] font-bold transition-all ${chartHours === h ? 'bg-accent-primary text-white shadow' : 'text-text-muted hover:text-white'}`}>
                {h < 24 ? `${h}h` : h === 24 ? '24h' : h === 72 ? '3d' : '7d'}
              </button>
            ))}
          </div>
        </div>

        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}
              barCategoryGap="30%">
              <XAxis dataKey="time" tickFormatter={fmtHour} tick={{ fill: '#5a5a75', fontSize: 9 }} tickLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fill: '#5a5a75', fontSize: 9 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip content={<ChartTooltip />} />
              <Legend wrapperStyle={{ fontSize: 10, color: '#888' }} />
              <Bar dataKey="online" name="Online Events" fill="#22c55e" radius={[3, 3, 0, 0]} maxBarSize={24} />
              <Bar dataKey="offline" name="Offline Events" fill="#ef4444" radius={[3, 3, 0, 0]} maxBarSize={24} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex items-center justify-center h-[200px] text-xs text-text-muted">
            <div className="text-center">
              <Wifi size={32} className="mx-auto mb-2 opacity-20" />
              <p>No connection events in this time range</p>
              <p className="text-[10px] mt-1 opacity-60">Events accumulate as devices go online/offline</p>
            </div>
          </div>
        )}
      </div>

      {/* Events Panel */}
      <EventsPanel devices={devices} />
    </div>
  );
}
