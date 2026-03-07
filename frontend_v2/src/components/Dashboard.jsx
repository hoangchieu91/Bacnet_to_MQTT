import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Activity, Server, Cpu, HardDrive, Thermometer, Play, Square, MemoryStick, Gauge, Wifi, Database, MonitorSmartphone, TrendingUp, Search } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts';

const API = '/api';
const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;

// Palette for chart lines
const COLORS = ['#00f0ff', '#a855f7', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4', '#8b5cf6'];

function formatUptime(seconds) {
  if (!seconds) return '—';
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

const StatCard = ({ label, value, detail, icon: Icon }) => (
  <div className="glass-card p-4 relative overflow-hidden group hover:border-accent-primary/30 transition-all">
    <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-accent-primary to-transparent opacity-60" />
    <div className="flex items-start justify-between">
      <div>
        <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted font-bold mb-1.5">{label}</div>
        <div className="text-2xl font-bold text-white">{value ?? '—'}</div>
        <div className="text-[11px] text-text-secondary mt-1">{detail}</div>
      </div>
      {Icon && <Icon size={20} className="text-text-muted opacity-40 group-hover:opacity-60 transition-opacity" />}
    </div>
  </div>
);

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

// Custom tooltip for Recharts
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-secondary border border-border rounded-lg p-2.5 text-xs shadow-xl">
      <div className="text-text-muted mb-1.5">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-white font-medium">{typeof p.value === 'number' ? p.value.toFixed(2) : p.value}</span>
          <span className="text-text-muted truncate max-w-[120px]">{p.name}</span>
        </div>
      ))}
    </div>
  );
};

// Chart line selector
function ChartSelector({ mappings, selected, onToggle }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {mappings.slice(0, 8).map((m, i) => {
        const label = (m.label || `${m.object_type}:${m.object_instance}`).split(/[.\\/\\\\]/).pop();
        const isOn = selected.has(m.id);
        return (
          <button key={m.id} onClick={() => onToggle(m.id)}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-bold border transition-all ${
              isOn ? 'text-white border-opacity-80' : 'text-text-muted border-border hover:border-accent-primary/40 hover:text-white'
            }`}
            style={isOn ? { borderColor: COLORS[i % COLORS.length], background: `${COLORS[i % COLORS.length]}18` } : {}}>
            <span className="w-2 h-2 rounded-full" style={{ background: isOn ? COLORS[i % COLORS.length] : '#444' }} />
            {label.length > 14 ? label.slice(0, 14) + '…' : label}
          </button>
        );
      })}
    </div>
  );
}

export function Dashboard() {
  const [status, setStatus] = useState(null);
  const [health, setHealth] = useState(null);
  const [events, setEvents] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [liveSearch, setLiveSearch] = useState('');
  const [chartSelected, setChartSelected] = useState(new Set());
  const [chartData, setChartData] = useState([]); // [{time, [pointId]: value}]
  const intervalRef = useRef(null);
  const chartBufferRef = useRef([]); // rolling 60 samples

  const fetchAll = useCallback(async () => {
    try {
      const [st, hl, ev, mp] = await Promise.allSettled([
        fetch(`${API}/status`).then(r => r.json()),
        fetch(`${API}/health`).then(r => r.json()),
        fetch(`${API}/events?limit=8`).then(r => r.json()),
        fetch(`${API}/mappings`).then(r => r.json()),
      ]);
      if (st.status === 'fulfilled') setStatus(st.value);
      if (hl.status === 'fulfilled') setHealth(hl.value);
      if (ev.status === 'fulfilled') setEvents(ev.value.events || []);
      if (mp.status === 'fulfilled') {
        const pts = mp.value.mappings || [];
        setMappings(pts);
        // Auto-select first 4 numeric points for chart
        setChartSelected(prev => {
          if (prev.size > 0) return prev;
          const analogs = pts.filter(p => (p.object_type || '').toLowerCase().includes('analog') && p.last_value != null);
          return new Set(analogs.slice(0, 4).map(p => p.id));
        });
      }
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => {
    fetchAll();
    intervalRef.current = setInterval(() => {
      fetchAll();
    }, 5000);
    return () => clearInterval(intervalRef.current);
  }, [fetchAll]);

  // Update chart buffer when mappings refresh
  useEffect(() => {
    if (mappings.length === 0 || chartSelected.size === 0) return;
    const now = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const point = { time: now };
    for (const id of chartSelected) {
      const m = mappings.find(x => x.id === id);
      if (m?.last_value != null) {
        const v = parseFloat(m.last_value);
        if (!isNaN(v)) point[id] = v;
      }
    }
    chartBufferRef.current = [...chartBufferRef.current.slice(-59), point];
    setChartData([...chartBufferRef.current]);
  }, [mappings, chartSelected]);

  const toggleChartPoint = useCallback((id) => {
    setChartSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else if (next.size < 7) next.add(id);
      return next;
    });
  }, []);

  const gatewayRunning = status?.gateway === 'running';
  const ramTotalMb = health?.ram_total_mb || 0;
  const ramAvailMb = health?.ram_available_mb || 0;

  const handleToggle = async () => {
    try {
      const endpoint = gatewayRunning ? `${API}/gateway/stop` : `${API}/gateway/start`;
      await fetch(endpoint, { method: 'POST' });
      setTimeout(fetchAll, 1000);
    } catch (e) { console.error(e); }
  };

  // Live points filtering
  const filteredMappings = liveSearch
    ? mappings.filter(m => (m.label || '').toLowerCase().includes(liveSearch.toLowerCase()) || String(m.device_id).includes(liveSearch))
    : mappings;

  // Chart series
  const colorMap = {};
  [...chartSelected].forEach((id, i) => { colorMap[id] = COLORS[i % COLORS.length]; });
  const chartMappings = mappings.filter(m => chartSelected.has(m.id));

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold tracking-tight text-white">Dashboard</h2>
        <button onClick={handleToggle}
          className={`flex items-center gap-2 px-5 py-2.5 rounded-lg text-white font-medium transition-all hover:-translate-y-0.5 ${gatewayRunning ? 'bg-error/80 hover:bg-error shadow-[0_4px_20px_rgba(255,60,60,0.3)]' : 'bg-accent-gradient shadow-[0_4px_20px_var(--color-accent-glow)]'}`}>
          {gatewayRunning ? <><Square size={16} fill="white" /> Stop</> : <><Play size={16} fill="white" /> Start Gateway</>}
        </button>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Gateway" icon={Activity}
          value={gatewayRunning ? 'Running' : (status?.gateway?.toUpperCase() || '—')}
          detail={`Uptime: ${formatUptime(status?.uptime_seconds)}`} />
        <StatCard label="Active Mappings" icon={Database}
          value={status?.active_mappings ?? '—'} detail="Points being polled" />
        <StatCard label="BACnet Devices" icon={Wifi}
          value={status?.discovered_devices ?? '—'}
          detail={status?.bacnet_connected ? '🟢 Connected' : '🔴 Disconnected'} />
        <StatCard label="MQTT" icon={MonitorSmartphone}
          value={status?.mqtt_connected ? 'Online' : 'Offline'}
          detail={status?.mqtt_connected ? '🟢 Broker connected' : '🔴 Disconnected'} />
      </div>

      {/* System Health */}
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
                  <span className={`font-bold ${health.cpu_temp > 70 ? 'text-error' : health.cpu_temp > 55 ? 'text-warning' : 'text-success'}`}>{health.cpu_temp}°C</span>
                </div>
                <div className="h-2 rounded-full bg-bg-input overflow-hidden">
                  <div className={`h-full rounded-full ${health.cpu_temp > 70 ? 'bg-error' : health.cpu_temp > 55 ? 'bg-warning' : 'bg-success'}`}
                    style={{ width: `${Math.min(health.cpu_temp, 100)}%` }} />
                </div>
              </div>
            )}
          </div>
          {ramTotalMb < 2048 && (
            <div className="mt-3 flex items-start gap-2 text-[10px] text-warning bg-warning/5 border border-warning/20 rounded-lg p-2.5">
              <span className="text-base leading-none">⚠️</span>
              <span>RAM {(ramTotalMb / 1024).toFixed(1)} GB — Với 500+ devices nên nâng lên 4 GB+</span>
            </div>
          )}
        </div>
      )}

      {/* Live Chart + Live Points Table — two-column */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">

        {/* Live Trend Chart */}
        <div className="glass-card p-5 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <div>
              <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted font-bold">Live Trend</div>
              <div className="text-[10px] text-text-muted mt-0.5">Last 5 minutes · auto-updates every 5s</div>
            </div>
            <TrendingUp size={16} className="text-accent-primary opacity-60" />
          </div>
          <ChartSelector mappings={mappings.filter(m => (m.object_type||'').toLowerCase().includes('analog'))} selected={chartSelected} onToggle={toggleChartPoint} />
          {chartData.length > 1 ? (
            <div className="mt-4 flex-1" style={{ minHeight: 180 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                  <XAxis dataKey="time" tick={{ fill: '#5a5a75', fontSize: 9 }} tickLine={false} interval="preserveStartEnd" />
                  <YAxis tick={{ fill: '#5a5a75', fontSize: 9 }} tickLine={false} axisLine={false} />
                  <Tooltip content={<CustomTooltip />} />
                  {chartMappings.map((m, i) => (
                    <Line key={m.id} type="monotone" dataKey={m.id} name={m.label || `${m.object_type}:${m.object_instance}`}
                      stroke={colorMap[m.id]} strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-xs text-text-muted mt-4" style={{ minHeight: 180 }}>
              <div className="text-center">
                <TrendingUp size={32} className="mx-auto mb-2 opacity-20" />
                <p>Waiting for data…</p>
                <p className="text-[10px] mt-1">Select analog points above to chart them</p>
              </div>
            </div>
          )}
        </div>

        {/* Live Points Table */}
        <div className="glass-card p-5 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <div>
              <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted font-bold">Live Points</div>
              <div className="text-[10px] text-text-muted mt-0.5">{filteredMappings.length} of {mappings.length} points</div>
            </div>
          </div>
          {/* Search */}
          <div className="relative mb-3">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
            <input type="text" placeholder="Filter by label or device…" value={liveSearch}
              onChange={e => setLiveSearch(e.target.value)}
              className="w-full pl-8 pr-3 py-1.5 bg-bg-input border border-border rounded-lg text-xs text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus transition-all" />
          </div>
          {/* Table */}
          <div className="flex-1 overflow-y-auto space-y-1" style={{ maxHeight: 280 }}>
            {filteredMappings.length === 0 ? (
              <div className="text-xs text-text-muted text-center py-8">No points yet. Start the gateway first.</div>
            ) : filteredMappings.map(m => {
              const label = (m.label || `${m.object_type}:${m.object_instance}`).split(/[.\\/\\\\]/).pop();
              const ot = (m.object_type || '').toLowerCase();
              const isBin = ot.includes('binary');
              const val = m.last_value;
              const isActive = isBin && (val === 'active' || val === 1 || String(val).toLowerCase() === 'active');
              return (
                <div key={m.id} className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-bg-input/30 hover:bg-bg-input/60 transition-colors group">
                  <span className="text-[9px] font-bold text-info/70 w-7 shrink-0">
                    {TYPE_SHORT[m.object_type] || m.object_type?.slice(0,2)?.toUpperCase()}
                  </span>
                  <span className="text-xs text-white flex-1 truncate" title={m.label}>{label}</span>
                  <span className="text-[10px] text-text-muted shrink-0">Dev {m.device_id}</span>
                  <span className={`text-sm font-bold shrink-0 w-20 text-right tabular-nums ${
                    val == null ? 'text-text-muted' : isBin ? (isActive ? 'text-success' : 'text-error') : 'text-white'
                  }`}>
                    {val == null ? '—' : isBin ? (isActive ? '● ON' : '○ OFF') : `${String(val).slice(0, 10)}${m.units ? ' ' + m.units : ''}`}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Recent Events */}
      <div className="glass-card p-5">
        <h3 className="text-[10px] font-bold uppercase tracking-[0.15em] text-text-muted mb-3">Recent Events</h3>
        {events.length > 0 ? (
          <div className="space-y-1.5">
            {events.map((ev, i) => (
              <div key={i} className="flex items-start gap-3 px-3 py-2 rounded-lg bg-bg-input/30 border border-border/30 hover:bg-bg-input/50 transition-colors">
                <div className={`mt-1.5 w-2 h-2 rounded-full flex-shrink-0 ${ev.severity === 'critical' ? 'bg-error' : ev.severity === 'warning' ? 'bg-warning' : 'bg-info'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate">{ev.message || ev.mapping_label || 'Event'}</div>
                  <div className="text-[10px] text-text-muted mt-0.5">{ev.timestamp ? new Date(ev.timestamp).toLocaleString('vi-VN') : ''}</div>
                </div>
                {ev.value != null && <span className="text-xs font-bold text-accent-primary">{ev.value}</span>}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-text-muted text-center py-6">No recent events</div>
        )}
      </div>
    </div>
  );
}

const TYPE_SHORT = {
  analogInput:'AI', analogOutput:'AO', analogValue:'AV',
  binaryInput:'BI', binaryOutput:'BO', binaryValue:'BV',
  multiStateInput:'MSI',multiStateOutput:'MSO',multiStateValue:'MSV',
  'analog-input':'AI','analog-output':'AO','analog-value':'AV',
  'binary-input':'BI','binary-output':'BO','binary-value':'BV',
};
