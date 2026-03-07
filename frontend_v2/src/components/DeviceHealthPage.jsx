import React, { useState, useEffect, useCallback, useRef } from 'react';
import { RefreshCw, Search, Wifi, WifiOff, Activity, X, Circle } from 'lucide-react';

const API = '/api';

function timeSince(iso) {
  if (!iso) return '—';
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function DeviceTile({ device, onClick }) {
  const online = device.online;
  const failPct = Math.min((device.fail_count || 0) / 5, 1);

  return (
    <div
      onClick={() => onClick(device)}
      className={`relative group cursor-pointer rounded-xl border p-3 flex flex-col gap-1 transition-all hover:-translate-y-0.5 hover:shadow-lg ${
        online
          ? 'bg-success/5 border-success/20 hover:border-success/50'
          : 'bg-error/5 border-error/20 hover:border-error/50'
      }`}
    >
      {/* Status dot */}
      <div className={`absolute top-2 right-2 w-2.5 h-2.5 rounded-full ${online ? 'bg-success shadow-[0_0_6px_rgba(0,255,136,0.6)]' : 'bg-error shadow-[0_0_6px_rgba(255,60,60,0.6)]'} ${online ? 'animate-pulse' : ''}`} />

      {/* Device ID */}
      <div className={`text-xs font-bold tabular-nums ${online ? 'text-success' : 'text-error'}`}>
        #{device.device_id}
      </div>

      {/* Name */}
      <div className="text-[11px] text-white font-medium truncate leading-tight" title={device.name}>
        {device.name || `Device ${device.device_id}`}
      </div>

      {/* Address */}
      <div className="text-[10px] text-text-muted truncate">{device.address || '—'}</div>

      {/* Stats bar */}
      <div className="flex items-center gap-2 mt-1">
        <span className="text-[10px] text-text-muted">{device.point_count || 0} pts</span>
        {!online && device.fail_count > 0 && (
          <span className="text-[10px] text-error">✕{device.fail_count}</span>
        )}
        {device.last_seen && (
          <span className="text-[10px] text-text-muted ml-auto">{timeSince(device.last_seen)}</span>
        )}
      </div>

      {/* Fail indicator bar */}
      {!online && failPct > 0 && (
        <div className="h-0.5 w-full bg-bg-input rounded-full overflow-hidden mt-1">
          <div className="h-full bg-error rounded-full" style={{ width: `${failPct * 100}%` }} />
        </div>
      )}
    </div>
  );
}

function DeviceDetailModal({ device, onClose }) {
  if (!device) return null;
  const online = device.online;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-bg-secondary border border-border rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <div className="flex items-center gap-3">
            <div className={`w-3 h-3 rounded-full ${online ? 'bg-success' : 'bg-error'}`} />
            <h3 className="text-base font-bold text-white">Device #{device.device_id}</h3>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-bg-input text-text-muted hover:text-white"><X size={18} /></button>
        </div>
        <div className="p-6 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            {[
              ['Status', online ? '🟢 Online' : '🔴 Offline'],
              ['Name', device.name || '—'],
              ['Address', device.address || '—'],
              ['Points', `${device.point_count || 0} mapped`],
              ['Fail Count', device.fail_count || 0],
              ['Last Seen', timeSince(device.last_seen)],
            ].map(([k, v]) => (
              <div key={k} className="bg-bg-input/40 rounded-lg p-3 border border-border/30">
                <div className="text-[10px] text-text-muted uppercase tracking-wider mb-1">{k}</div>
                <div className="text-sm font-medium text-white">{v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function DeviceHealthPage() {
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('ALL'); // ALL | ONLINE | OFFLINE
  const [selected, setSelected] = useState(null);
  const intervalRef = useRef(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/devices/health`);
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      setDevices(data.devices || []);
    } catch (e) {
      console.error('Health fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    intervalRef.current = setInterval(fetchHealth, 30000); // Auto-refresh every 30s
    return () => clearInterval(intervalRef.current);
  }, [fetchHealth]);

  const onlineCount = devices.filter(d => d.online).length;
  const offlineCount = devices.length - onlineCount;

  const filtered = devices.filter(d => {
    if (filter === 'ONLINE' && !d.online) return false;
    if (filter === 'OFFLINE' && d.online) return false;
    if (search) {
      const q = search.toLowerCase();
      if (!String(d.device_id).includes(q) && !(d.name || '').toLowerCase().includes(q) && !(d.address || '').includes(q)) return false;
    }
    return true;
  });

  return (
    <div className="p-6 flex flex-col h-screen">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Activity size={24} className="text-accent-primary" /> Device Health
          </h2>
          <p className="text-xs text-text-muted mt-1">
            {devices.length} devices total •
            <span className="text-success ml-1">{onlineCount} online</span> •
            <span className="text-error ml-1">{offlineCount} offline</span>
          </p>
        </div>
        <button onClick={fetchHealth} disabled={loading}
          className="flex items-center gap-2 px-3 py-2 bg-bg-input border border-border rounded-lg text-text-secondary hover:text-white hover:border-accent-primary text-sm transition-all disabled:opacity-50">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-3 gap-3 mb-5">
        <div className="glass-card p-4">
          <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Total Devices</div>
          <div className="text-2xl font-bold text-white">{devices.length}</div>
        </div>
        <div className="glass-card p-4 border-success/20">
          <div className="text-[10px] uppercase tracking-widest text-success/70 mb-1">Online</div>
          <div className="text-2xl font-bold text-success">{onlineCount}</div>
          <div className="text-xs text-text-muted mt-1">{devices.length > 0 ? ((onlineCount / devices.length) * 100).toFixed(1) : 0}% availability</div>
        </div>
        <div className="glass-card p-4 border-error/20">
          <div className="text-[10px] uppercase tracking-widest text-error/70 mb-1">Offline</div>
          <div className="text-2xl font-bold text-error">{offlineCount}</div>
          {offlineCount > 0 && (
            <div className="text-xs text-error/60 mt-1 flex items-center gap-1"><WifiOff size={10} /> Needs attention</div>
          )}
        </div>
      </div>

      {/* Filter + Search */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <div className="flex gap-1">
          {['ALL', 'ONLINE', 'OFFLINE'].map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                filter === f
                  ? f === 'ONLINE' ? 'bg-success/20 text-success border border-success/40'
                    : f === 'OFFLINE' ? 'bg-error/20 text-error border border-error/40'
                    : 'bg-accent-primary/20 text-accent-primary border border-accent-primary/40'
                  : 'bg-bg-input border border-border text-text-secondary hover:text-white'
              }`}>
              {f === 'ALL' ? `All (${devices.length})` : f === 'ONLINE' ? `🟢 Online (${onlineCount})` : `🔴 Offline (${offlineCount})`}
            </button>
          ))}
        </div>
        <div className="relative flex-1 max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
          <input type="text" placeholder="Search by ID, name, or address…"
            value={search} onChange={e => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2 bg-bg-input border border-border rounded-lg text-xs text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus transition-all" />
        </div>
        {search && <span className="text-xs text-text-muted">{filtered.length} results</span>}
      </div>

      {/* Device Grid */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-text-muted">
            <Wifi size={40} className="mb-3 opacity-30" />
            <p className="text-sm">No devices found</p>
            <p className="text-xs mt-1">Start the gateway and run discovery first</p>
          </div>
        ) : (
          <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
            {filtered.map(d => (
              <DeviceTile key={d.device_id} device={d} onClick={setSelected} />
            ))}
          </div>
        )}
      </div>

      {/* Detail Modal */}
      {selected && <DeviceDetailModal device={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
