import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  RefreshCw, Search, Wifi, WifiOff, Activity, X,
  LayoutGrid, Network, ChevronDown, ChevronRight
} from 'lucide-react';

const API = '/api';

function timeSince(iso) {
  if (!iso) return '—';
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function parseAddress(addr) {
  if (!addr) return { network: 'Unknown', node: '', type: 'unknown', netKey: 'zzz_unknown' };
  if (addr.includes('.')) return { network: 'IP/Ethernet', node: addr, type: 'ip', netKey: '000_ip' };
  if (addr.includes(':')) {
    const [net, node] = addr.split(':');
    return { network: `Net ${net}`, node, type: 'mstp', netKey: `${net.padStart(8,'0')}_mstp`, netId: net };
  }
  return { network: 'Unknown', node: addr, type: 'unknown', netKey: 'zzz_unknown' };
}

const NET_PALETTE = [
  '#6366f1','#8b5cf6','#0ea5e9','#14b8a6','#f59e0b',
  '#ef4444','#ec4899','#22c55e','#f97316','#06b6d4',
  '#a78bfa','#fb923c','#34d399','#60a5fa','#f472b6',
];
const _colorMap = {};
let _colorIdx = 0;
function netColor(key) {
  if (!_colorMap[key]) _colorMap[key] = NET_PALETTE[_colorIdx++ % NET_PALETTE.length];
  return _colorMap[key];
}

function StatusDot({ online, size = 'sm' }) {
  const px = size === 'sm' ? 'w-2.5 h-2.5' : 'w-3.5 h-3.5';
  if (online === true)  return <div className={`${px} rounded-full bg-success shadow-[0_0_5px_rgba(0,255,136,0.5)] animate-pulse`} />;
  if (online === false) return <div className={`${px} rounded-full bg-error   shadow-[0_0_5px_rgba(255,60,60,0.5)]`} />;
  return <div className={`${px} rounded-full bg-warning/60`} />;
}

function DeviceTile({ device, onClick }) {
  const border = device.online === true
    ? 'border-success/25 bg-success/5 hover:border-success/60'
    : device.online === false
    ? 'border-error/25 bg-error/5 hover:border-error/60'
    : 'border-border/30 bg-bg-input/30 hover:border-border-focus';
  return (
    <div onClick={() => onClick(device)}
      className={`relative cursor-pointer rounded-xl border p-3 flex flex-col gap-1 transition-all hover:-translate-y-0.5 hover:shadow-md ${border}`}>
      <div className="absolute top-2 right-2"><StatusDot online={device.online} /></div>
      <div className={`text-xs font-bold tabular-nums ${device.online === true ? 'text-success' : device.online === false ? 'text-error' : 'text-warning'}`}>
        #{device.device_id}
      </div>
      <div className="text-[11px] text-white font-medium truncate pr-3" title={device.name}>{device.name || `Device ${device.device_id}`}</div>
      <div className="text-[10px] text-text-muted">{device.address || '—'}</div>
      <div className="flex items-center gap-2 mt-0.5">
        <span className="text-[10px] text-text-muted">{device.point_count || 0} pts</span>
        {device.bms_queried && (
          <span className="text-[9px] font-bold px-1 rounded bg-violet-500/20 text-violet-400 border border-violet-500/30">BMS</span>
        )}
        {device.online === false && device.fail_count > 0 && <span className="text-[10px] text-error">✕{device.fail_count}</span>}
        {device.last_seen && <span className="text-[10px] text-text-muted ml-auto">{timeSince(device.last_seen)}</span>}
      </div>
    </div>
  );
}

const OBJ_TYPES = ['analogInput','analogOutput','analogValue','binaryInput','binaryOutput','binaryValue','multiStateInput','multiStateOutput','multiStateValue'];

function DeviceModal({ device, onClose }) {
  if (!device) return null;
  const p = parseAddress(device.address);
  const [tab, setTab] = useState('info');
  const [form, setForm] = useState({
    object_type: 'analogInput', object_instance: '', label: '',
    mqtt_topic: '', poll_interval: 30, group: '', read_mode: 'poll',
  });
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState(null); // null=not loaded
  const [histLoading, setHistLoading] = useState(false);

  const loadHistory = async (force = false) => {
    if (history !== null && !force) return; // already loaded
    setHistLoading(true);
    try {
      const res = await fetch(`/api/devices/${device.device_id}/offline-history?limit=100`);
      const data = await res.json();
      setHistory(data);
    } catch { setHistory({ incidents: [], offline_count: 0, current_online: null }); }
    setHistLoading(false);
  };

  const handleTabChange = (k) => {
    setTab(k); setResult(null);
    if (k === 'history') loadHistory(true); // always reload for fresh status
  };

  const handleAdd = async () => {
    if (!form.object_instance) { setResult({ ok: false, msg: 'Object Instance is required' }); return; }
    setSaving(true); setResult(null);
    try {
      const payload = {
        device_id: device.device_id,
        object_type: form.object_type,
        object_instance: parseInt(form.object_instance, 10),
        label: form.label || `${form.object_type}:${form.object_instance}`,
        mqtt_topic: form.mqtt_topic || null,
        poll_interval: Number(form.poll_interval) || 30,
        group: form.group || null,
        read_mode: form.read_mode,
        enabled: true,
      };
      const res = await fetch('/api/mappings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok && data.mapping) {
        setResult({ ok: true, msg: `✓ Added: ${data.mapping.label || data.mapping.id}` });
        setForm(f => ({ ...f, object_instance: '', label: '', mqtt_topic: '' }));
      } else {
        setResult({ ok: false, msg: data.detail || data.error || 'Failed to add' });
      }
    } catch (e) { setResult({ ok: false, msg: String(e) }); }
    setSaving(false);
  };

  const fmtTs = (iso) => {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toLocaleString('vi-VN', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' });
    } catch { return iso; }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-bg-secondary border border-border rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <StatusDot online={device.online} size="lg" />
            <div>
              <h3 className="font-bold text-white text-sm">Device #{device.device_id}</h3>
              <p className="text-[10px] text-text-muted truncate max-w-[260px]">{device.name || '—'}</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-bg-input text-text-muted hover:text-white"><X size={16}/></button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-border shrink-0">
          {[
            ['info', '📊 Info'],
            ['add', '➕ Add Point'],
            ['history', '📋 Offline History'],
          ].map(([k, lbl]) => (
            <button key={k} onClick={() => handleTabChange(k)}
              className={`relative px-4 py-2.5 text-xs font-bold transition-colors whitespace-nowrap ${tab===k ? 'text-accent-primary border-b-2 border-accent-primary' : 'text-text-muted hover:text-white'}`}>
              {lbl}
              {/* Badge: show only unresolved offline count on History tab */}
              {k === 'history' && history && (() => {
                const unresolved = (history.incidents || []).filter(i => !i.online_at).length;
                return unresolved > 0 ? (
                  <span className="absolute -top-1 -right-1 bg-error text-white text-[8px] font-bold px-1 rounded-full min-w-[14px] text-center">
                    {unresolved}
                  </span>
                ) : null;
              })()}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto">
          {tab === 'info' && (
            <div className="p-5 grid grid-cols-2 gap-3">
              {[['Name',device.name||'—'],['Network',p.network],['Address',device.address||'—'],['Node',p.node||'—'],
                ['Status',device.online===true?'🟢 Online':device.online===false?'🔴 Offline':'⏳ Pending'],
                ['Points',`${device.point_count||0} mapped`],['Fail Count',device.fail_count||0],['Last Seen',timeSince(device.last_seen)]]
                .map(([k,v])=>(
                  <div key={k} className="bg-bg-input/40 rounded-lg p-3 border border-border/30">
                    <div className="text-[10px] text-text-muted uppercase tracking-wider mb-1">{k}</div>
                    <div className="text-sm font-medium text-white truncate">{String(v)}</div>
                  </div>
              ))}
            </div>
          )}

          {tab === 'add' && (
            <div className="p-5 space-y-3">
              <p className="text-[11px] text-text-muted">Add a BACnet point mapping for <b className="text-white">Device #{device.device_id}</b></p>
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Object Type</label>
                  <select value={form.object_type} onChange={e => setForm(f=>({...f,object_type:e.target.value}))}
                    className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus">
                    {OBJ_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Object Instance *</label>
                  <input type="number" min="0" placeholder="e.g. 1" value={form.object_instance}
                    onChange={e => setForm(f=>({...f,object_instance:e.target.value}))}
                    className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus" />
                </div>
                <div>
                  <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Poll Interval (s)</label>
                  <input type="number" min="5" value={form.poll_interval}
                    onChange={e => setForm(f=>({...f,poll_interval:e.target.value}))}
                    className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus" />
                </div>
                <div>
                  <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Label</label>
                  <input type="text" placeholder="e.g. Room Temp" value={form.label}
                    onChange={e => setForm(f=>({...f,label:e.target.value}))}
                    className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus" />
                </div>
                <div>
                  <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Group</label>
                  <input type="text" placeholder="e.g. HVAC" value={form.group}
                    onChange={e => setForm(f=>({...f,group:e.target.value}))}
                    className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus" />
                </div>
                <div className="col-span-2">
                  <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">MQTT Topic <span className="normal-case text-text-muted">(optional — auto if blank)</span></label>
                  <input type="text" placeholder="e.g. building/floor3/temp" value={form.mqtt_topic}
                    onChange={e => setForm(f=>({...f,mqtt_topic:e.target.value}))}
                    className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus" />
                </div>
              </div>
              {result && (
                <div className={`text-xs px-3 py-2 rounded-lg ${result.ok ? 'bg-success/10 text-success border border-success/30' : 'bg-error/10 text-error border border-error/30'}`}>
                  {result.msg}
                </div>
              )}
              <button onClick={handleAdd} disabled={saving || !form.object_instance}
                className="w-full py-2.5 rounded-xl font-bold text-sm bg-gradient-to-r from-accent-primary to-purple-600 text-white disabled:opacity-50 hover:opacity-90 transition-all flex items-center justify-center gap-2">
                {saving ? '⏳ Adding...' : '➕ Add Point Mapping'}
              </button>
            </div>
          )}

          {tab === 'history' && (
            <div className="p-4">
              {histLoading ? (
                <div className="flex items-center justify-center py-10 text-text-muted text-sm">
                  ⏳ Loading offline history...
                </div>
              ) : !history ? null : (
                <>
                  {/* Summary bar */}
                  <div className="flex items-center gap-3 mb-4 px-4 py-3 rounded-xl bg-error/8 border border-error/20">
                    <div className="text-3xl font-black text-error">{history.offline_count}</div>
                    <div>
                      <div className="text-sm font-bold text-white">Offline incident{history.offline_count !== 1 ? 's' : ''}</div>
                      <div className="text-[10px] text-text-muted">recorded for Device #{device.device_id}</div>
                    </div>
                  </div>

                  {/* Current status banner — shows when device is now online but has unresolved incidents */}
                  {history.current_online === true && history.incidents.some(i => !i.online_at) && (
                    <div className="mb-3 flex items-center gap-2 px-3 py-2 rounded-lg bg-success/10 border border-success/30 text-xs text-success">
                      <div className="w-2 h-2 rounded-full bg-success animate-pulse shrink-0" />
                      Device hiện đang <b>Online</b>
                      {history.current_last_seen && (
                        <span className="text-text-muted ml-1">— Last seen {fmtTs(history.current_last_seen)}</span>
                      )}
                    </div>
                  )}

                  {history.incidents.length === 0 ? (
                    <div className="text-center py-8 text-text-muted text-sm">
                      🟢 No offline incidents recorded — device has been stable.
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {/* Header row */}
                      <div className="grid grid-cols-[1fr_1fr_80px] gap-2 px-2 pb-1 border-b border-border/30">
                        <div className="text-[9px] text-text-muted uppercase tracking-wider">Offline At</div>
                        <div className="text-[9px] text-text-muted uppercase tracking-wider">Back Online At</div>
                        <div className="text-[9px] text-text-muted uppercase tracking-wider text-right">Duration</div>
                      </div>
                      {history.incidents.map((inc, idx) => (
                        <div key={idx}
                          className={`grid grid-cols-[1fr_1fr_80px] gap-2 px-3 py-2.5 rounded-lg border text-xs
                            ${!inc.online_at ? 'bg-error/10 border-error/30' : 'bg-bg-input/30 border-border/20 hover:bg-bg-input/50'}`}>
                          {/* Offline time */}
                          <div>
                            <div className="text-error font-medium leading-tight">{fmtTs(inc.offline_at)}</div>
                          </div>
                          {/* Online time */}
                          <div>
                            {inc.online_at
                              ? <div className="text-success font-medium leading-tight">{fmtTs(inc.online_at)}</div>
                              : history.current_online === true
                                ? <div className="text-success/70 italic text-[10px]">Now online ✓</div>
                                : <div className="text-error/70 italic">Still offline</div>
                            }
                          </div>
                          {/* Duration */}
                          <div className="text-right">
                            <span className={`font-bold ${!inc.online_at ? 'text-error' : 'text-text-secondary'}`}>
                              {inc.duration_text}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <p className="text-[10px] text-text-muted text-center mt-3">
                    Showing {history.incidents.length} most recent incident{history.incidents.length !== 1 ? 's' : ''}
                  </p>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


/* ══════════════════════════════════════════════════════
   Network Accordion Card
══════════════════════════════════════════════════════ */
function NetworkCard({ networkName, devices, color, defaultOpen, onTileClick }) {
  const [open, setOpen] = useState(defaultOpen);
  const online  = devices.filter(d => d.online === true).length;
  const offline = devices.filter(d => d.online === false).length;
  const pending = devices.filter(d => d.online === null || d.online === undefined).length;
  const bmsDevs = devices.filter(d => d.bms_queried).length;
  const pct = devices.length > 0 ? (online / devices.length) * 100 : 0;

  return (
    <div className="rounded-2xl border border-border/50 overflow-hidden bg-bg-secondary/40 backdrop-blur-sm"
      style={{ borderLeftColor: color, borderLeftWidth: 3 }}>
      {/* ── Header / toggler ── */}
      <button
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-bg-input/30 transition-colors text-left"
        onClick={() => setOpen(o => !o)}>
        {/* expand icon */}
        <div style={{ color }} className="flex-shrink-0">
          {open ? <ChevronDown size={16}/> : <ChevronRight size={16}/>}
        </div>

        {/* Network name */}
        <span className="font-bold text-sm text-white min-w-[80px]">{networkName}</span>

        {/* Progress bar */}
        <div className="flex-1 h-1.5 bg-bg-input rounded-full overflow-hidden mx-2 hidden sm:block">
          <div className="h-full rounded-full transition-all duration-500"
            style={{ width: `${pct}%`, backgroundColor: pct > 80 ? '#22c55e' : pct > 40 ? '#f59e0b' : '#ef4444' }} />
        </div>

        {/* Stats */}
        <div className="flex items-center gap-3 text-xs flex-shrink-0">
          <span className="font-bold text-white tabular-nums">{devices.length}</span>
          <span className="text-text-muted">devices</span>
          {online  > 0 && <span className="text-success font-semibold">● {online} online</span>}
          {offline > 0 && <span className="text-error   font-semibold">● {offline} off</span>}
          {pending > 0 && <span className="text-warning/70 text-[11px]">⏳ {pending}</span>}
          {bmsDevs > 0 && <span className="text-violet-400 text-[11px] font-medium">👁 {bmsDevs} BMS</span>}
          {/* % badge */}
          <span className="hidden sm:block px-1.5 py-0.5 rounded-md text-[10px] font-mono"
            style={{ background: `${color}22`, color }}>
            {pct.toFixed(0)}%
          </span>
        </div>
      </button>

      {/* ── Expanded device tiles ── */}
      {open && (
        <div className="px-4 pb-4 pt-2 border-t border-border/30">
          <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(140px,1fr))' }}>
            {devices.map(d => <DeviceTile key={d.device_id} device={d} onClick={onTileClick}/>)}
          </div>
        </div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════
   Main Page
══════════════════════════════════════════════════════════ */
export function DeviceHealthPage() {
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search,  setSearch]  = useState('');
  const [filter,  setFilter]  = useState('ALL');
  const [view,    setView]    = useState('network'); // 'grid' | 'network'
  const [selected, setSelected] = useState(null);
  const timerRef = useRef(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/devices/health`);
      if (!r.ok) throw new Error(r.statusText);
      const d = await r.json();
      setDevices(d.devices || []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchHealth();
    timerRef.current = setInterval(fetchHealth, 30000);
    return () => clearInterval(timerRef.current);
  }, [fetchHealth]);

  const onlineCount  = devices.filter(d => d.online === true).length;
  const offlineCount = devices.filter(d => d.online === false).length;
  const pendingCount = devices.filter(d => d.online !== true && d.online !== false).length;

  const filtered = useMemo(() => devices.filter(d => {
    if (filter === 'ONLINE'  && d.online !== true)  return false;
    if (filter === 'OFFLINE' && d.online !== false) return false;
    if (search) {
      const q = search.toLowerCase();
      if (!String(d.device_id).includes(q) &&
          !(d.name||'').toLowerCase().includes(q) &&
          !(d.address||'').includes(q)) return false;
    }
    return true;
  }), [devices, filter, search]);

  const byNetwork = useMemo(() => {
    const g = {};
    for (const d of filtered) {
      const { network, netKey } = parseAddress(d.address);
      if (!g[netKey]) g[netKey] = { networkName: network, devices: [] };
      g[netKey].devices.push(d);
    }
    return Object.entries(g)
      .sort(([a],[b]) => a.localeCompare(b))
      .map(([key, val]) => ({ key, color: netColor(key), ...val }));
  }, [filtered]);

  // Auto-expand top 3 networks by default
  const topNets = useMemo(() => new Set(byNetwork.slice(0,3).map(n=>n.key)), [byNetwork]);

  return (
    <div className="flex flex-col h-screen p-6">
      {/* Title */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold text-white flex items-center gap-2">
            <Activity size={22} className="text-accent-primary"/> Device Health
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            {devices.length} total •
            <span className="text-success ml-1">{onlineCount} online</span> •
            <span className="text-error ml-1">{offlineCount} offline</span>
            {pendingCount > 0 && <span className="text-warning ml-1">• {pendingCount} pending</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* View toggle */}
          <div className="flex bg-bg-input border border-border rounded-lg overflow-hidden text-xs">
            {[['grid','Grid',<LayoutGrid size={12}/>],['network','By Network',<Network size={12}/>]].map(([v,l,ic])=>(
              <button key={v} onClick={()=>setView(v)}
                className={`px-3 py-2 flex items-center gap-1.5 border-r last:border-r-0 border-border transition-all ${view===v?'bg-accent-primary/20 text-accent-primary':'text-text-secondary hover:text-white'}`}>
                {ic}{l}
              </button>
            ))}
          </div>
          <button onClick={fetchHealth} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-2 bg-bg-input border border-border rounded-lg text-text-secondary hover:text-white text-xs transition-all disabled:opacity-50">
            <RefreshCw size={13} className={loading?'animate-spin':''}/> Refresh
          </button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        {[
          { label:'Total',    val: devices.length, cls:'text-white' },
          { label:'Online',   val: onlineCount,    cls:'text-success', sub:`${devices.length>0?((onlineCount/devices.length)*100).toFixed(1):0}%` },
          { label:'Offline',  val: offlineCount,   cls:'text-error',   sub: offlineCount>0?'Needs attention':undefined },
          { label:'Networks', val: byNetwork.length,cls:'text-warning', sub:'MSTP / IP lines' },
        ].map(({label,val,cls,sub})=>(
          <div key={label} className="glass-card p-4">
            <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">{label}</div>
            <div className={`text-2xl font-bold ${cls}`}>{val}</div>
            {sub && <div className={`text-xs mt-1 ${cls} opacity-60`}>{sub}</div>}
          </div>
        ))}
      </div>

      {/* Filter + Search */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex gap-1">
          {[['ALL',`All (${devices.length})`],['ONLINE',`🟢 (${onlineCount})`],['OFFLINE',`🔴 (${offlineCount})`]].map(([f,l])=>(
            <button key={f} onClick={()=>setFilter(f)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all border ${
                filter===f
                  ? f==='ONLINE'  ? 'bg-success/20 text-success border-success/40'
                  : f==='OFFLINE' ? 'bg-error/20 text-error border-error/40'
                  : 'bg-accent-primary/20 text-accent-primary border-accent-primary/40'
                  : 'bg-bg-input border-border text-text-secondary hover:text-white'
              }`}>{l}</button>
          ))}
        </div>
        <div className="relative flex-1 max-w-xs">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"/>
          <input value={search} onChange={e=>setSearch(e.target.value)}
            placeholder="Search ID, name, address…"
            className="w-full pl-8 pr-3 py-2 bg-bg-input border border-border rounded-lg text-xs text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus"/>
        </div>
        {search && <span className="text-xs text-text-muted">{filtered.length} results</span>}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-text-muted">
            <Wifi size={36} className="mb-3 opacity-20"/>
            <p className="text-sm">No devices found</p>
          </div>
        ) : view === 'grid' ? (
          /* ── Flat grid ── */
          <div className="grid gap-2" style={{gridTemplateColumns:'repeat(auto-fill,minmax(140px,1fr))'}}>
            {filtered.map(d=><DeviceTile key={d.device_id} device={d} onClick={setSelected}/>)}
          </div>
        ) : (
          /* ── Network accordion cards ── */
          <div className="space-y-2">
            {byNetwork.map(({ key, networkName, devices: devs, color }) => (
              <NetworkCard
                key={key}
                networkName={networkName}
                devices={devs}
                color={color}
                defaultOpen={topNets.has(key)}
                onTileClick={setSelected}
              />
            ))}
          </div>
        )}
      </div>

      {selected && <DeviceModal device={selected} onClose={()=>setSelected(null)}/>}
    </div>
  );
}
