import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  ResponsiveContainer, ComposedChart, Line, Bar, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ReferenceArea,
} from 'recharts';
import {
  TrendingUp, Plus, Trash2, Edit3, X, RefreshCw, WifiOff,
  Search, ChevronDown, Copy, BarChart2, Activity, Eye, EyeOff,
  Settings2, LayoutDashboard, ZoomIn, ZoomOut, Move,
} from 'lucide-react';

const API = '/api';

// ── Color palette ──────────────────────────────────────────────
const PALETTE = [
  '#00f0ff', '#ff0055', '#00ff88', '#ffb700', '#8b5cf6',
  '#f97316', '#ec4899', '#06b6d4', '#10b981', '#ef4444',
  '#a78bfa', '#34d399', '#fbbf24', '#60a5fa', '#f472b6',
  '#4ade80', '#fb923c', '#38bdf8',
];

const CHART_TYPES = [
  { v: 'line', l: 'Line', icon: Activity },
  { v: 'area', l: 'Area', icon: TrendingUp },
  { v: 'bar',  l: 'Bar',  icon: BarChart2 },
];

const PRESETS = [
  { label: '15m', minutes: 15,    limit: 200  },
  { label: '1h',  minutes: 60,    limit: 300  },
  { label: '6h',  minutes: 360,   limit: 600  },
  { label: '24h', minutes: 1440,  limit: 1000 },
  { label: '7d',  minutes: 10080, limit: 2000 },
  { label: '30d', minutes: 43200, limit: 5000 },
];

function nowMinus(m) { return new Date(Date.now() - m * 60_000).toISOString(); }
function uid() { return Math.random().toString(36).slice(2, 10); }

function fmtTs(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('vi-VN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function fmtTsShort(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
}

function newChart(name = 'New Chart') {
  return { id: uid(), name, preset: '1h', live: true, points: [] };
}

// ── API helpers ─────────────────────────────────────────────────
async function apiGet(path) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
async function apiPost(path, body) {
  const r = await fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
async function apiPut(path, body) {
  const r = await fetch(`${API}${path}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
async function apiDelete(path) {
  const r = await fetch(`${API}${path}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

// ── Custom tooltip ──────────────────────────────────────────────
function CustomTooltip({ active, payload, label, points }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-card/90 border border-white/10 rounded-xl p-3.5 shadow-[0_10px_40px_rgba(0,0,0,0.6)] text-xs min-w-[200px] backdrop-blur-2xl ring-1 ring-white/5">
      <p className="text-text-muted mb-2.5 font-bold tracking-tight border-b border-white/5 pb-1.5 flex justify-between items-center">
        <span>{fmtTs(label)}</span>
        <Activity size={10} className="text-accent-primary animate-pulse" />
      </p>
      {payload.map((entry) => {
        const pt = points?.find(p => p.mapping_id === entry.dataKey);
        return (
          <div key={entry.dataKey} className="flex items-center justify-between gap-6 mb-2 last:mb-0 group">
            <span className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0 transition-transform group-hover:scale-125" style={{ background: entry.color, boxShadow: `0 0 8px ${entry.color}` }} />
              <span className="text-text-secondary group-hover:text-white transition-colors truncate max-w-[130px] font-medium">{pt?.label || entry.name}</span>
            </span>
            <span className="font-bold text-white tabular-nums text-sm">
              {entry.value != null ? Number(entry.value).toLocaleString('vi-VN', { minimumFractionDigits: 1, maximumFractionDigits: 3 }) : '—'}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Point row in editor ──────────────────────────────────────────
function PointRow({ pt, mappings, onUpdate, onRemove }) {
  const [open, setOpen] = useState(false);
  const m = mappings.find(x => x.id === pt.mapping_id);
  const SEL = 'w-full px-1 py-1 bg-bg-input border border-border rounded text-[10px] text-white focus:outline-none';

  return (
    <div className="glass-card p-2.5 space-y-2 border border-border/40">
      <div className="flex items-center gap-2">
        <span className="w-3 h-3 rounded-full flex-shrink-0 ring-1 ring-white/20" style={{ background: pt.color }} />
        <span className="flex-1 text-xs font-medium text-white truncate">{pt.label || m?.label || '—'}</span>
        <button onClick={() => setOpen(o => !o)} className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white transition-all">
          <Settings2 size={12} />
        </button>
        <button onClick={onRemove} className="p-1 rounded hover:bg-error/20 text-text-muted hover:text-error transition-all">
          <Trash2 size={12} />
        </button>
      </div>
      {open && (
        <div className="space-y-2 pt-1 border-t border-border/30">
          <div>
            <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Label</label>
            <input
              className="w-full px-2 py-1 bg-bg-input border border-border rounded text-xs text-white focus:outline-none focus:border-border-focus"
              value={pt.label} placeholder={m?.label || 'Auto'}
              onChange={e => onUpdate({ label: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Color</label>
              <div className="flex items-center gap-1">
                <input type="color" value={pt.color}
                  onChange={e => onUpdate({ color: e.target.value })}
                  className="w-6 h-6 rounded cursor-pointer bg-transparent border border-border"
                />
                <select className="flex-1 px-1 py-1 bg-bg-input border border-border rounded text-[10px] text-white focus:outline-none"
                  value={pt.color} onChange={e => onUpdate({ color: e.target.value })}>
                  {PALETTE.map(c => <option key={c} value={c} style={{ background: c }}>■</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Y-Axis</label>
              <select className={SEL} value={pt.yAxis || pt.y_axis || 'left'}
                onChange={e => onUpdate({ yAxis: e.target.value, y_axis: e.target.value })}>
                <option value="left">Left</option>
                <option value="right">Right</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Type</label>
              <select className={SEL} value={pt.type}
                onChange={e => onUpdate({ type: e.target.value })}>
                {CHART_TYPES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
              </select>
            </div>
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={pt.visible !== false}
              onChange={e => onUpdate({ visible: e.target.checked })} className="rounded" />
            <span className="text-[10px] text-text-muted">Visible on chart</span>
          </label>
        </div>
      )}
    </div>
  );
}

// ── Chart editor modal ───────────────────────────────────────────
function ChartEditor({ chart, mappings, groups, onSave, onClose }) {
  const [form, setForm] = useState(JSON.parse(JSON.stringify(chart)));
  const [search, setSearch] = useState('');
  const [filterGroup, setFilterGroup] = useState('');
  const [filterDevice, setFilterDevice] = useState('');

  const uniqueDevices = [...new Set(mappings.map(m => m.device_id))].sort();

  const availableMappings = useMemo(() => {
    const usedIds = new Set(form.points.map(p => p.mapping_id));
    return mappings.filter(m => {
      if (!m.enabled) return false;
      if (filterGroup && m.group_id !== filterGroup) return false;
      if (filterDevice && String(m.device_id) !== filterDevice) return false;
      if (search) {
        const q = search.toLowerCase();
        const lbl = (m.label || `${m.object_type}:${m.object_instance}`).toLowerCase();
        if (!lbl.includes(q) && !String(m.device_id).includes(q)) return false;
      }
      return !usedIds.has(m.id);
    });
  }, [mappings, form.points, search, filterGroup, filterDevice]);

  const addPoint = (m) => {
    const colorIdx = form.points.length % PALETTE.length;
    setForm(f => ({
      ...f,
      points: [...f.points, {
        id: uid(),
        mapping_id: m.id,
        label: m.label || `${m.object_type}:${m.object_instance}`,
        color: PALETTE[colorIdx],
        yAxis: 'left', y_axis: 'left',
        type: 'line',
        visible: true,
      }],
    }));
  };

  const updatePoint = (idx, upd) => {
    setForm(f => {
      const pts = [...f.points];
      pts[idx] = { ...pts[idx], ...upd };
      return { ...f, points: pts };
    });
  };

  const removePoint = (idx) => {
    setForm(f => ({ ...f, points: f.points.filter((_, i) => i !== idx) }));
  };

  const SEL_CLS = 'w-full px-2.5 py-1.5 bg-bg-input border border-border rounded-lg text-xs text-white focus:outline-none focus:border-border-focus';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-bg-secondary border border-border rounded-2xl shadow-2xl flex w-full max-w-3xl mx-4 max-h-[90vh] overflow-hidden">

        {/* Left: chart config */}
        <div className="w-72 flex-shrink-0 border-r border-border flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <h3 className="text-sm font-bold text-white">⚙ Chart Settings</h3>
            <button onClick={onClose} className="p-1 rounded hover:bg-bg-input text-text-muted hover:text-white"><X size={16} /></button>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Chart Name</label>
              <input
                className="w-full px-2.5 py-1.5 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="e.g. FCU Temperature"
              />
            </div>
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Default Range</label>
              <select className={SEL_CLS} value={form.preset}
                onChange={e => setForm(f => ({ ...f, preset: e.target.value }))}>
                {PRESETS.map(p => <option key={p.label} value={p.label}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <div className="text-[10px] text-text-muted uppercase tracking-wider mb-2 flex items-center justify-between">
                <span>Points ({form.points.length})</span>
                {form.points.length === 0 && <span className="text-warning text-[10px]">Add from right →</span>}
              </div>
              <div className="space-y-2">
                {form.points.map((pt, idx) => (
                  <PointRow key={pt.id} pt={pt} idx={idx} mappings={mappings}
                    onUpdate={upd => updatePoint(idx, upd)}
                    onRemove={() => removePoint(idx)}
                  />
                ))}
                {form.points.length === 0 && (
                  <div className="text-center py-6 text-text-muted text-xs border border-dashed border-border/40 rounded-lg">
                    No points yet
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="p-4 border-t border-border">
            <button
              onClick={() => { if (form.name.trim() && form.points.length > 0) onSave(form); }}
              disabled={!form.name.trim() || form.points.length === 0}
              className="w-full py-2 bg-gradient-to-r from-accent-primary to-purple-600 text-white text-sm font-medium rounded-lg disabled:opacity-40 hover:opacity-90 transition-all"
            >
              ✓ Save Chart
            </button>
          </div>
        </div>

        {/* Right: point picker */}
        <div className="flex-1 flex flex-col min-h-0">
          <div className="px-4 py-3 border-b border-border">
            <p className="text-sm font-bold text-white">Add Points</p>
            <p className="text-xs text-text-muted mt-0.5">Click a point to add it to the chart</p>
          </div>
          <div className="p-3 border-b border-border space-y-2">
            <div className="relative">
              <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
              <input className="w-full pl-7 pr-2.5 py-1.5 bg-bg-input border border-border rounded-lg text-xs text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                placeholder="Search points…" value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <select className={SEL_CLS} value={filterGroup} onChange={e => setFilterGroup(e.target.value)}>
                <option value="">All Groups</option>
                {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select>
              <select className={SEL_CLS} value={filterDevice} onChange={e => setFilterDevice(e.target.value)}>
                <option value="">All Devices</option>
                {uniqueDevices.map(id => <option key={id} value={String(id)}>Device {id}</option>)}
              </select>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-1">
            {availableMappings.length === 0 && (
              <p className="text-xs text-text-muted text-center py-8">
                {mappings.length === 0 ? 'No points available' : 'All points already added or no match'}
              </p>
            )}
            {availableMappings.map(m => (
              <button key={m.id} onClick={() => addPoint(m)}
                className="w-full text-left px-3 py-2 rounded-lg flex items-center gap-2.5 hover:bg-white/8 text-xs transition-all group border border-transparent hover:border-border/40">
                <Plus size={12} className="text-text-muted group-hover:text-accent-primary flex-shrink-0 transition-colors" />
                <div className="flex-1 min-w-0">
                  <div className="text-text-secondary group-hover:text-white truncate transition-colors">
                    {m.label || `${m.object_type}:${m.object_instance}`}
                  </div>
                  <div className="text-text-muted text-[10px]">Dev {m.device_id} • {m.object_type}</div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Chart viewer with zoom/pan ───────────────────────────────────
function ChartViewer({ chart, mappings, preset, live, customFrom, customTo, loading: globalLoading }) {
  const [chartData, setChartData] = useState([]);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef(null);

  // ── Zoom/pan state ──
  const [zoomDomain, setZoomDomain] = useState(null);  // { x1, x2 } ISO strings or null
  const [isDragging, setIsDragging]   = useState(false);
  const dragRef = useRef({ startX: null, domain: null });

  const visiblePoints = useMemo(
    () => chart.points.filter(p => p.visible !== false),
    [chart.points]
  );
  const hasRight = useMemo(() => visiblePoints.some(p => (p.yAxis || p.y_axis) === 'right'), [visiblePoints]);
  const visibleIdsKey = visiblePoints.map(p => p.mapping_id).join(',');

  const getRange = useCallback(() => {
    if (preset === 'custom') {
      return { start: customFrom ? `${customFrom}:00` : undefined, end: customTo ? `${customTo}:00` : undefined, limit: 5000 };
    }
    const p = PRESETS.find(x => x.label === preset) || PRESETS[1];
    return { start: nowMinus(p.minutes), end: undefined, limit: p.limit };
  }, [preset, customFrom, customTo]);

  const buildData = useCallback((seriesMap) => {
    const tsSet = new Set();
    Object.values(seriesMap).forEach(pts => pts.forEach(p => tsSet.add(p.timestamp)));
    const sorted = [...tsSet].sort();
    const byMid = {};
    Object.entries(seriesMap).forEach(([mid, pts]) => {
      byMid[mid] = [...pts].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    });
    const lastKnown = {};
    return sorted.map(ts => {
      const row = { timestamp: ts };
      Object.keys(byMid).forEach(mid => {
        const exact = byMid[mid].find(p => p.timestamp === ts);
        if (exact !== undefined) {
          const v = Number(exact.value);
          lastKnown[mid] = isNaN(v) ? lastKnown[mid] : v;
        }
        row[mid] = lastKnown[mid] !== undefined ? lastKnown[mid] : null;
      });
      return row;
    });
  }, []);

  const fetchData = useCallback(async () => {
    if (!visibleIdsKey) { setChartData([]); return; }
    setLoading(true);
    try {
      const { start, end, limit } = getRange();
      const params = new URLSearchParams({ ids: visibleIdsKey, limit });
      if (start) params.set('start', start);
      if (end) params.set('end', end);
      const res = await fetch(`${API}/history/multi?${params}`).then(r => r.json());
      setChartData(buildData(res.series || {}));
    } catch (e) { console.error(e); }
    setLoading(false);
  }, [visibleIdsKey, getRange, buildData]);

  useEffect(() => {
    setZoomDomain(null);
    fetchData();
    clearInterval(timerRef.current);
    if (live && preset !== 'custom' && !['30d', '7d'].includes(preset)) {
      timerRef.current = setInterval(fetchData, 120_000);
    }
    return () => clearInterval(timerRef.current);
  }, [fetchData, live, preset]);

  // ── Zoom/Pan logic ──
  const displayData = useMemo(() => {
    if (!zoomDomain) return chartData;
    return chartData.filter(d => d.timestamp >= zoomDomain.x1 && d.timestamp <= zoomDomain.x2);
  }, [chartData, zoomDomain]);

  const zoomIn = () => {
    if (chartData.length < 2) return;
    const all = chartData.map(d => d.timestamp);
    const [x1, x2] = zoomDomain ? [zoomDomain.x1, zoomDomain.x2] : [all[0], all[all.length - 1]];
    const span = new Date(x2) - new Date(x1);
    const midMs = (new Date(x1).getTime() + new Date(x2).getTime()) / 2;
    const newSpan = span * 0.5;
    setZoomDomain({ x1: new Date(midMs - newSpan / 2).toISOString(), x2: new Date(midMs + newSpan / 2).toISOString() });
  };
  const zoomOut = () => {
    if (!zoomDomain) return;
    const span = new Date(zoomDomain.x2) - new Date(zoomDomain.x1);
    const midMs = (new Date(zoomDomain.x1).getTime() + new Date(zoomDomain.x2).getTime()) / 2;
    const newSpan = span * 2;
    const newX1 = new Date(midMs - newSpan / 2).toISOString();
    const newX2 = new Date(midMs + newSpan / 2).toISOString();
    const allTs = chartData.map(d => d.timestamp);
    if (newX1 <= allTs[0] && newX2 >= allTs[allTs.length - 1]) setZoomDomain(null);
    else setZoomDomain({ x1: newX1, x2: newX2 });
  };
  const resetZoom = () => setZoomDomain(null);

  const onWheel = (e) => {
    const data = zoomDomain ? chartData.filter(d => d.timestamp >= zoomDomain.x1 && d.timestamp <= zoomDomain.x2) : chartData;
    if (data.length < 2) return;
    const x1 = new Date(data[0].timestamp).getTime();
    const x2 = new Date(data[data.length - 1].timestamp).getTime();
    const span = x2 - x1;
    const fact = e.deltaY > 0 ? 1.25 : 0.8;
    const mid = (x1 + x2) / 2;
    const nX1 = new Date(mid - (span * fact) / 2).toISOString();
    const nX2 = new Date(mid + (span * fact) / 2).toISOString();
    setZoomDomain({ x1: nX1, x2: nX2 });
  };
  const onMouseDown = (e) => {
    if (e.button !== 0) return;
    setIsDragging(true);
    const dom = zoomDomain ? { x1: new Date(zoomDomain.x1).getTime(), x2: new Date(zoomDomain.x2).getTime() }
      : chartData.length > 1 ? { x1: new Date(chartData[0].timestamp).getTime(), x2: new Date(chartData[chartData.length - 1].timestamp).getTime() } : null;
    dragRef.current = { startX: e.clientX, domain: dom };
    e.preventDefault();
  };
  const onMouseMove = (e) => {
    if (!isDragging || !dragRef.current.domain) return;
    const { startX, domain } = dragRef.current;
    const shift = -((e.clientX - startX) / 800) * (domain.x2 - domain.x1);
    setZoomDomain({ x1: new Date(domain.x1 + shift).toISOString(), x2: new Date(domain.x2 + shift).toISOString() });
  };
  const onMouseUp = () => setIsDragging(false);

  return (
    <div className="flex flex-col h-full bg-bg-card/30 rounded-xl border border-white/5 overflow-hidden">
      {/* Mini header for individual zoom */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border/20">
        <span className="text-[10px] font-bold text-white uppercase truncate flex-1">{chart.name}</span>
        <div className="flex items-center gap-0.5">
          <button onClick={zoomIn} className="p-1 rounded hover:bg-white/5 text-text-muted"><ZoomIn size={10} /></button>
          <button onClick={zoomOut} className="p-1 rounded hover:bg-white/5 text-text-muted"><ZoomOut size={10} /></button>
          <button onClick={resetZoom} className="p-1 rounded hover:bg-white/5 text-[10px] text-text-muted">⟲</button>
          <button onClick={fetchData} className="p-1 rounded hover:bg-white/5 text-text-muted">
            <RefreshCw size={10} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="flex-1 p-2 min-h-0 relative">
        {/* Subtle background glow */}
        <div className="absolute inset-0 bg-radial-[at_50%_40%] from-accent-primary/2 to-transparent opacity-50 pointer-events-none" />
        
        {chart.points.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-text-muted relative z-10">
            <TrendingUp size={48} className="opacity-10 mb-4" />
            <p className="text-sm font-medium">No points added to this chart</p>
            <p className="text-xs mt-1 opacity-60">Click Edit to add points from the registry</p>
          </div>
        ) : chartData.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-full text-text-muted relative z-10">
            <WifiOff size={40} className="opacity-10 mb-4" />
            <p className="text-sm">No sequence data found in range</p>
            <p className="text-xs mt-1">Try expanding the window or enabling Live mode</p>
          </div>
        ) : (
          <div
            className="w-full h-full relative z-10"
            style={{ cursor: isDragging ? 'grabbing' : 'grab', userSelect: 'none' }}
            onWheel={onWheel}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            onMouseLeave={onMouseUp}
            onDoubleClick={resetZoom}
          >
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={displayData} margin={{ top: 15, right: hasRight ? 65 : 15, left: 10, bottom: 5 }}>
                <defs>
                  {visiblePoints.map(pt => (
                    <filter key={`glow-${pt.id}`} id={`glow-${pt.id}`} x="-20%" y="-20%" width="140%" height="140%">
                      <feGaussianBlur stdDeviation="1.5" result="blur" />
                      <feComposite in="SourceGraphic" in2="blur" operator="over" />
                    </filter>
                  ))}
                  <linearGradient id="gridGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="rgba(255,255,255,0.01)" />
                    <stop offset="50%" stopColor="rgba(255,255,255,0.05)" />
                    <stop offset="100%" stopColor="rgba(255,255,255,0.01)" />
                  </linearGradient>
                </defs>

                <CartesianGrid strokeDasharray="4 4" stroke="url(#gridGradient)" vertical={false} />
                
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={fmtTsShort}
                  tick={{ fontSize: 10, fill: 'var(--color-text-muted)', fontWeight: 500 }}
                  tickLine={false}
                  axisLine={{ stroke: 'var(--color-text-muted)', strokeOpacity: 0.15 }}
                  minTickGap={60}
                  interval="preserveStartEnd"
                />
                
                <YAxis yAxisId="left" orientation="left"
                  tick={{ fontSize: 10, fill: 'var(--color-text-muted)', fontWeight: 600 }} 
                  tickLine={false} axisLine={false} width={45} 
                  domain={['auto', 'auto']}
                />
                
                {hasRight && (
                  <YAxis yAxisId="right" orientation="right"
                    tick={{ fontSize: 10, fill: 'var(--color-text-muted)', fontWeight: 600 }} 
                    tickLine={false} axisLine={false} width={45}
                    domain={['auto', 'auto']}
                  />
                )}

                <Tooltip content={<CustomTooltip points={chart.points} />} />
                
                {visiblePoints.map((pt) => {
                  const mapping = mappings.find(m => m.id === pt.mapping_id);
                  const isBinary = mapping && (mapping.object_type || '').toLowerCase().includes('binary');
                  const lineType = isBinary ? 'stepAfter' : 'monotone';
                  const yAxisId = pt.yAxis || pt.y_axis || 'left';
                  
                  const props = {
                    key: pt.id,
                    dataKey: pt.mapping_id,
                    name: pt.mapping_id,
                    yAxisId,
                    stroke: pt.color,
                    strokeWidth: 2.5,
                    connectNulls: true,
                    type: lineType,
                    dot: false,
                    activeDot: { r: 5, fill: pt.color, stroke: 'white', strokeWidth: 2 },
                    filter: `url(#glow-${pt.id})`,
                  };

                  if (pt.type === 'area') return (
                    <Area {...props} fill={`url(#grad-${pt.id})`} fillOpacity={0.08}>
                      <defs>
                        <linearGradient id={`grad-${pt.id}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={pt.color} stopOpacity={0.3} />
                          <stop offset="100%" stopColor={pt.color} stopOpacity={0} />
                        </linearGradient>
                      </defs>
                    </Area>
                  );
                  
                  if (pt.type === 'bar') return (
                    <Bar key={pt.id} dataKey={pt.mapping_id} name={pt.mapping_id}
                      yAxisId={yAxisId} fill={pt.color}
                      opacity={0.85} radius={[3, 3, 0, 0]} 
                      style={{ filter: `blur(0.5px)` }}
                    />
                  );
                  
                  return <Line {...props} />;
                })}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Point legend */}
      {chart.points.length > 0 && (
        <div className="px-4 pb-3 flex flex-wrap gap-2 flex-shrink-0">
          {chart.points.map(pt => (
            <div key={pt.id} className={`flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full border transition-all ${
              pt.visible !== false ? 'border-border/50 text-text-secondary' : 'border-border/20 text-text-muted opacity-50'
            }`}>
              <span className="w-2 h-2 rounded-full" style={{ background: pt.color }} />
              <span className="truncate max-w-[80px]">{pt.label}</span>
              <span className="text-text-muted opacity-60">{(pt.yAxis || pt.y_axis) === 'right' ? '→R' : '→L'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main page — 2-panel layout ───────────────────────────────────
export function ChartsPage() {
  const [charts, setCharts]           = useState([]);
  const [mappings, setMappings]       = useState([]);
  const [groups, setGroups]           = useState([]);
  const [selectedIds, setSelectedIds] = useState([]);
  
  // Shared time controls
  const [preset, setPreset] = useState('1h');
  const [live, setLive] = useState(true);
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');

  const [editingChart, setEditingChart] = useState(null);
  const [saving, setSaving]             = useState(false);
  const [loading, setLoading]           = useState(true);

  // Load charts from API on mount + Migration logic
  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true);
      try {
        const [c, m, g] = await Promise.all([
          apiGet('/charts'),
          fetch(`${API}/mappings`).then(r => r.json()),
          fetch(`${API}/groups`).then(r => r.json()),
        ]);
        if (!active) return;

        let loaded = c.charts || [];
        setMappings(m.mappings || []);
        setGroups(g.groups || []);

        // ── Migration from localStorage ──
        const localRaw = localStorage.getItem('charts');
        if (loaded.length === 0 && localRaw) {
          try {
            const localCharts = JSON.parse(localRaw);
            if (Array.isArray(localCharts) && localCharts.length > 0) {
              console.log('[Migration] Moving local charts to server...');
              const migrated = [];
              for (const lc of localCharts) {
                // Normalize old format to new format
                const payload = {
                  name: lc.name || 'Migrated Chart',
                  preset: lc.preset || '1h',
                  live: lc.live !== false,
                  points: (lc.points || []).map(p => ({
                    mapping_id: typeof p === 'string' ? p : (p.mapping_id || p.id),
                    label: p.label || 'Point',
                    color: p.color || PALETTE[0],
                    y_axis: p.y_axis || p.yAxis || 'left',
                    type: p.type || 'line',
                    visible: p.visible !== false
                  }))
                };
                const res = await apiPost('/charts', payload);
                migrated.push(res.chart);
              }
              loaded = migrated;
              localStorage.removeItem('charts'); // Clean up
              console.log('[Migration] Success');
            }
          } catch (me) { console.error('Migration failed:', me); }
        }

        setCharts(loaded);
        if (loaded.length > 0) setSelectedIds([loaded[0].id]);
      } catch (e) {
        console.error('Initial load failed:', e);
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    return () => { active = false; };
  }, []);



  const saveChart = async (form) => {
    setSaving(true);
    try {
      // Normalize points for backend (map yAxis → y_axis)
      const payload = {
        ...form,
        points: form.points.map(p => ({
          id: p.id || uid(),
          mapping_id: p.mapping_id,
          label: p.label,
          color: p.color,
          y_axis: p.yAxis || p.y_axis || 'left',
          type: p.type || 'line',
          visible: p.visible !== false,
        })),
      };

      const existing = charts.find(c => c.id === form.id);
      if (existing) {
        const res = await apiPut(`/charts/${form.id}`, payload);
        setCharts(prev => prev.map(c => c.id === form.id ? res.chart : c));
      } else {
        const res = await apiPost('/charts', payload);
        const created = res.chart;
        setCharts(prev => [...prev, created]);
        setSelectedIds(prev => [...prev, created.id]);
      }
    } catch (e) { console.error('Save chart failed:', e); }
    setSaving(false);
    setEditingChart(null);
  };

  const deleteChart = async (id) => {
    if (!confirm('Delete this chart?')) return;
    try {
      await apiDelete(`/charts/${id}`);
      setCharts(prev => {
        const next = prev.filter(c => c.id !== id);
        setSelectedIds(curr => curr.filter(x => x !== id));
        return next;
      });
    } catch (e) { console.error(e); }
  };

  const duplicateChart = async (chart) => {
    const copy = JSON.parse(JSON.stringify(chart));
    copy.id = '';
    copy.name = `${chart.name} (copy)`;
    try {
      const res = await apiPost('/charts', copy);
      setCharts(prev => [...prev, res.chart]);
      setSelectedIds(prev => [...prev, res.chart.id]);
    } catch (e) { console.error(e); }
  };

  const startEdit = (chart) => setEditingChart(JSON.parse(JSON.stringify(chart)));
  const startCreate = () => setEditingChart(newChart(`Chart ${charts.length + 1}`));

  return (
    <div className="flex h-full" style={{ minHeight: 0 }}>

      {/* ── LEFT SIDEBAR ── */}
      <div className="w-56 flex-shrink-0 border-r border-border flex flex-col bg-bg-secondary/50">
        {/* Sidebar header */}
        <div className="flex items-center justify-between px-3 py-3 border-b border-border">
          <span className="text-xs font-bold text-white flex items-center gap-1.5">
            <TrendingUp size={13} className="text-accent-primary" /> Charts
          </span>
          <button onClick={startCreate}
            className="flex items-center gap-1 px-2 py-1 bg-accent-primary/20 border border-accent-primary/40 text-accent-primary rounded-lg text-[10px] font-medium hover:bg-accent-primary/30 transition-all">
            <Plus size={10} /> New
          </button>
        </div>

        {/* Chart list */}
        <div className="flex-1 overflow-y-auto py-1">
          {loading && (
            <div className="flex items-center justify-center py-8">
              <RefreshCw size={14} className="animate-spin text-text-muted" />
            </div>
          )}
          {!loading && charts.length === 0 && (
            <div className="px-3 py-6 text-center text-text-muted text-xs">
              <LayoutDashboard size={24} className="opacity-20 mx-auto mb-2" />
              No charts yet
            </div>
          )}
          {charts.map(c => {
            const isSelected = selectedIds.includes(c.id);
            return (
              <button
                key={c.id}
                onClick={() => {
                  setSelectedIds(prev => 
                    prev.includes(c.id) 
                      ? prev.filter(id => id !== c.id) 
                      : [...prev, c.id]
                  );
                }}
                className={`w-full text-left px-3 py-3 flex items-center gap-3 text-xs transition-all relative group overflow-hidden ${
                  isSelected
                    ? 'bg-accent-primary/10 text-white'
                    : 'text-text-secondary hover:bg-white/5 border-l-2 border-transparent hover:text-white'
                }`}
              >
                {isSelected && (
                  <>
                    <div className="absolute left-0 top-0 bottom-0 w-1 bg-accent-primary shadow-[0_0_12px_var(--color-accent-primary)]" />
                    <div className="absolute inset-0 bg-gradient-to-r from-accent-primary/10 to-transparent" />
                  </>
                )}
                <Activity size={12} className={isSelected ? 'text-accent-primary animate-pulse' : 'text-text-muted'} />
                <span className="flex-1 truncate font-semibold tracking-wide uppercase text-[10px]">{c.name}</span>
                <span className={`text-[9px] px-1.5 py-0.5 rounded bg-white/5 border border-white/10 ${isSelected ? 'opacity-100 text-accent-primary border-accent-primary/20' : 'opacity-0 group-hover:opacity-60'} transition-opacity`}>
                  {c.points?.length || 0}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── RIGHT PANEL ── */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {selectedIds.length === 0 ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center h-full text-text-muted">
            <LayoutDashboard size={56} className="opacity-20 mb-4" />
            <p className="text-base font-medium text-text-secondary">
              {charts.length === 0 ? 'No charts yet' : 'Select one or more charts'}
            </p>
            <p className="text-sm mt-1">
              {charts.length === 0
                ? 'Create a chart to start visualizing BACnet points'
                : 'Toggle charts in the sidebar to view them in parallel'}
            </p>
            <button onClick={startCreate}
              className="mt-5 flex items-center gap-2 px-5 py-2.5 bg-accent-primary/20 border border-accent-primary/40 text-accent-primary rounded-xl hover:bg-accent-primary/30 transition-all font-medium text-sm">
              <Plus size={16} /> Create First Chart
            </button>
          </div>
        ) : (
          <div className="flex flex-col h-full overflow-hidden">
            {/* Global controls bar */}
            <div className="flex flex-wrap items-center gap-1.5 px-4 py-2 border-b border-border/40 bg-bg-secondary/40">
              <div className="flex gap-1 mr-2">
                {PRESETS.map(p => (
                  <button key={p.label} onClick={() => setPreset(p.label)}
                    className={`px-2.5 py-1 rounded text-[10px] font-bold transition-all ${
                      preset === p.label ? 'bg-accent-primary text-white shadow' : 'text-text-muted hover:text-white hover:bg-white/5'
                    }`}>
                    {p.label}
                  </button>
                ))}
                <button onClick={() => setPreset('custom')}
                  className={`px-2.5 py-1 rounded text-[10px] font-bold transition-all ${
                    preset === 'custom' ? 'bg-accent-primary text-white shadow' : 'text-text-muted hover:text-white hover:bg-white/5'
                  }`}>
                  Custom
                </button>
              </div>

              {preset === 'custom' && (
                <div className="flex items-center gap-1 mr-4">
                  <input type="datetime-local" className="px-2 py-1 bg-bg-input border border-border rounded text-[10px] text-white focus:outline-none" 
                    value={customFrom} onChange={e => setCustomFrom(e.target.value)} />
                  <span className="text-text-muted text-[10px]">→</span>
                  <input type="datetime-local" className="px-2 py-1 bg-bg-input border border-border rounded text-[10px] text-white focus:outline-none" 
                    value={customTo} onChange={e => setCustomTo(e.target.value)} />
                </div>
              )}

              <button onClick={() => setLive(l => !l)}
                className={`flex items-center gap-2 px-3 py-1 rounded-lg text-[10px] font-bold transition-all border ${
                  live ? 'text-green-400 bg-green-400/10 border-green-400/20' : 'text-text-muted border-white/10 hover:text-white hover:bg-white/5'
                }`}>
                {live ? <><span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" /> Live Mode</> : <><WifiOff size={10} /> Paused</>}
              </button>

              <div className="ml-auto flex items-center gap-1">
                <span className="text-[10px] text-text-muted bg-white/5 px-2 py-1 rounded-md border border-white/5 font-medium">
                  {selectedIds.length} View{selectedIds.length > 1 ? 's' : ''}
                </span>
                <button onClick={() => setSelectedIds([])} className="p-1 px-2 rounded-md hover:bg-error/15 text-text-muted hover:text-error text-[10px] transition-all">Clear All</button>
              </div>
            </div>

            {/* Grid display */}
            <div className="flex-1 overflow-y-auto p-4 content-start">
              <div className={`grid gap-4 ${
                selectedIds.length === 1 ? 'grid-cols-1 h-full' : 
                selectedIds.length <= 2 ? 'grid-cols-1 lg:grid-cols-2' : 
                'grid-cols-1 md:grid-cols-2'
              }`}>
                {charts.filter(c => selectedIds.includes(c.id)).map(chart => (
                  <div key={chart.id} className="h-[400px] flex flex-col relative group">
                    <ChartViewer
                      chart={chart}
                      mappings={mappings}
                      preset={preset}
                      live={live}
                      customFrom={customFrom}
                      customTo={customTo}
                    />
                    {/* Floating chart actions */}
                    <div className="absolute top-1 right-12 flex opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onClick={() => startEdit(chart)} className="p-1.5 rounded hover:bg-white/10 text-white transition-all"><Edit3 size={11} /></button>
                      <button onClick={() => duplicateChart(chart)} className="p-1.5 rounded hover:bg-white/10 text-white transition-all"><Copy size={11} /></button>
                      <button onClick={() => deleteChart(chart.id)} className="p-1.5 rounded hover:bg-error/20 text-error transition-all"><Trash2 size={11} /></button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Editor modal */}
      {editingChart && (
        <ChartEditor
          chart={editingChart}
          mappings={mappings}
          groups={groups}
          onSave={saveChart}
          onClose={() => setEditingChart(null)}
        />
      )}
    </div>
  );
}
