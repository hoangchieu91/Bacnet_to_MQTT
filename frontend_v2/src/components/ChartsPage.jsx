import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  ResponsiveContainer, ComposedChart, Line, Bar, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, Brush, ReferenceLine,
} from 'recharts';
import {
  TrendingUp, Plus, Trash2, Edit3, X, RefreshCw, WifiOff,
  Search, ChevronDown, Copy, BarChart2, Activity, Eye, EyeOff,
  Settings2, Palette, Ruler, LayoutDashboard,
} from 'lucide-react';

const API = '/api';
const LS_KEY = 'gw_charts_v2';

// ── Color palette ──────────────────────────────────────────────
const PALETTE = [
  '#00f0ff', '#ff0055', '#00ff88', '#ffb700', '#8b5cf6',
  '#f97316', '#ec4899', '#06b6d4', '#10b981', '#ef4444',
  '#a78bfa', '#34d399', '#fbbf24', '#60a5fa', '#f472b6',
  '#4ade80', '#fb923c', '#38bdf8',
];

// ── Chart type options ─────────────────────────────────────────
const CHART_TYPES = [
  { v: 'line', l: 'Line', icon: Activity },
  { v: 'area', l: 'Area', icon: TrendingUp },
  { v: 'bar',  l: 'Bar',  icon: BarChart2 },
];

// ── Time range presets ─────────────────────────────────────────
const PRESETS = [
  { label: '15m', minutes: 15,    limit: 200  },
  { label: '1h',  minutes: 60,    limit: 300  },
  { label: '6h',  minutes: 360,   limit: 600  },
  { label: '24h', minutes: 1440,  limit: 1000 },
  { label: '7d',  minutes: 10080, limit: 2000 },
  { label: '30d', minutes: 43200, limit: 5000 },
];

function nowMinus(m) {
  return new Date(Date.now() - m * 60_000).toISOString();
}

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

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

// ── Load/save charts from localStorage ──────────────────────────
function loadCharts() {
  try { return JSON.parse(localStorage.getItem(LS_KEY)) || []; }
  catch { return []; }
}

function saveCharts(charts) {
  localStorage.setItem(LS_KEY, JSON.stringify(charts));
}

// ── Default new chart ───────────────────────────────────────────
function newChart(name = 'New Chart') {
  return {
    id: uid(),
    name,
    preset: '1h',
    live: true,
    points: [],  // { id, mapping_id, label, color, yAxis: 'left'|'right', type: 'line'|'area'|'bar', visible: true }
  };
}

// ── Custom tooltip ──────────────────────────────────────────────
function CustomTooltip({ active, payload, label, points }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-card/95 border border-border rounded-xl p-3 shadow-2xl text-xs min-w-[180px] backdrop-blur-xl">
      <p className="text-text-muted mb-2 font-medium border-b border-border/40 pb-1">{fmtTs(label)}</p>
      {payload.map((entry) => {
        const pt = points?.find(p => p.mapping_id === entry.dataKey);
        return (
          <div key={entry.dataKey} className="flex items-center justify-between gap-4 mb-1">
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: entry.color }} />
              <span className="text-text-secondary truncate max-w-[120px]">{pt?.label || entry.name}</span>
            </span>
            <span className="font-bold text-white tabular-nums">
              {entry.value != null ? Number(entry.value).toFixed(3) : '—'}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Point row in editor ──────────────────────────────────────────
function PointRow({ pt, mappings, onUpdate, onRemove, idx }) {
  const [open, setOpen] = useState(false);
  const m = mappings.find(x => x.id === pt.mapping_id);

  return (
    <div className="glass-card p-2.5 space-y-2 border border-border/40">
      {/* Row header */}
      <div className="flex items-center gap-2">
        <span className="w-3 h-3 rounded-full flex-shrink-0 ring-1 ring-white/20" style={{ background: pt.color }} />
        <span className="flex-1 text-xs font-medium text-white truncate">
          {pt.label || m?.label || '—'}
        </span>
        <button onClick={() => setOpen(o => !o)} className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white transition-all">
          <Settings2 size={12} />
        </button>
        <button onClick={onRemove} className="p-1 rounded hover:bg-error/20 text-text-muted hover:text-error transition-all">
          <Trash2 size={12} />
        </button>
      </div>

      {open && (
        <div className="space-y-2 pt-1 border-t border-border/30">
          {/* Label */}
          <div>
            <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Label</label>
            <input
              className="w-full px-2 py-1 bg-bg-input border border-border rounded text-xs text-white focus:outline-none focus:border-border-focus"
              value={pt.label}
              placeholder={m?.label || 'Auto'}
              onChange={e => onUpdate({ label: e.target.value })}
            />
          </div>
          {/* Color + Y-axis + Type */}
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Color</label>
              <div className="flex items-center gap-1">
                <input type="color" value={pt.color}
                  onChange={e => onUpdate({ color: e.target.value })}
                  className="w-6 h-6 rounded cursor-pointer bg-transparent border border-border"
                />
                <select
                  className="flex-1 px-1 py-1 bg-bg-input border border-border rounded text-[10px] text-white focus:outline-none"
                  value={pt.color}
                  onChange={e => onUpdate({ color: e.target.value })}
                >
                  {PALETTE.map(c => (
                    <option key={c} value={c} style={{ background: c }}>■</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Y-Axis</label>
              <select
                className="w-full px-1 py-1 bg-bg-input border border-border rounded text-[10px] text-white focus:outline-none"
                value={pt.yAxis}
                onChange={e => onUpdate({ yAxis: e.target.value })}
              >
                <option value="left">Left</option>
                <option value="right">Right</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Type</label>
              <select
                className="w-full px-1 py-1 bg-bg-input border border-border rounded text-[10px] text-white focus:outline-none"
                value={pt.type}
                onChange={e => onUpdate({ type: e.target.value })}
              >
                {CHART_TYPES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
              </select>
            </div>
          </div>
          {/* Visibility */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={pt.visible !== false} onChange={e => onUpdate({ visible: e.target.checked })}
              className="rounded" />
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
        yAxis: 'left',
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
            {/* Name */}
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Chart Name</label>
              <input
                className="w-full px-2.5 py-1.5 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="e.g. FCU Temperature"
              />
            </div>

            {/* Default time range */}
            <div>
              <label className="text-[10px] text-text-muted uppercase tracking-wider block mb-1">Default Range</label>
              <select className={SEL_CLS} value={form.preset}
                onChange={e => setForm(f => ({ ...f, preset: e.target.value }))}>
                {PRESETS.map(p => <option key={p.label} value={p.label}>{p.label}</option>)}
              </select>
            </div>

            {/* Points list */}
            <div>
              <div className="text-[10px] text-text-muted uppercase tracking-wider mb-2 flex items-center justify-between">
                <span>Points ({form.points.length})</span>
                {form.points.length === 0 && <span className="text-warning text-[10px]">Add from right →</span>}
              </div>
              <div className="space-y-2">
                {form.points.map((pt, idx) => (
                  <PointRow
                    key={pt.id}
                    pt={pt}
                    idx={idx}
                    mappings={mappings}
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

          {/* Save */}
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

// ── Single chart viewer ──────────────────────────────────────────
function ChartViewer({ chart, onEdit, onDelete, onDuplicate, mappings }) {
  const [chartData, setChartData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [preset, setPreset] = useState(chart.preset || '1h');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo]   = useState('');
  const [live, setLive] = useState(chart.live !== false);
  const [collapsed, setCollapsed] = useState(false);
  const timerRef = useRef(null);

  // Memoize to avoid recreating on every render
  const visiblePoints = useMemo(
    () => chart.points.filter(p => p.visible !== false),
    [chart.points]
  );
  const hasRight = useMemo(() => visiblePoints.some(p => p.yAxis === 'right'), [visiblePoints]);
  // Stable string key — only changes when point IDs actually change
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
    return sorted.map(ts => {
      const row = { timestamp: ts };
      Object.entries(seriesMap).forEach(([mid, pts]) => {
        const pt = pts.find(p => p.timestamp === ts);
        row[mid] = pt ? Number(pt.value) : null;
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
    fetchData();
    clearInterval(timerRef.current);
    if (live && preset !== 'custom' && !['30d', '7d'].includes(preset)) {
      timerRef.current = setInterval(fetchData, 120_000);
    }
    return () => clearInterval(timerRef.current);
  }, [fetchData, live, preset]);

  const inputCls = 'px-2 py-1 bg-bg-input border border-border rounded text-[10px] text-white focus:outline-none focus:border-border-focus';

  return (
    <div className="glass-card flex flex-col">
      {/* Chart header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border/40">
        <button onClick={() => setCollapsed(c => !c)} className="p-1 rounded hover:bg-white/8 text-text-muted hover:text-white transition-all">
          <ChevronDown size={14} className={`transition-transform ${collapsed ? '-rotate-90' : ''}`} />
        </button>
        <span className="font-semibold text-white text-sm flex-1 truncate">{chart.name}</span>
        <span className="text-[10px] text-text-muted">{chart.points.length} pt{chart.points.length !== 1 && 's'}</span>
        {loading && <RefreshCw size={12} className="animate-spin text-text-muted" />}

        {/* Live */}
        <button onClick={() => setLive(l => !l)}
          className={`flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium transition-all ${live ? 'text-green-400 bg-green-500/10' : 'text-text-muted hover:text-white'}`}>
          {live ? <><span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />Live</> : <><WifiOff size={10} />Paused</>}
        </button>

        {/* Actions */}
        <button onClick={onEdit} className="p-1.5 rounded hover:bg-white/8 text-text-muted hover:text-white transition-all"><Edit3 size={13} /></button>
        <button onClick={onDuplicate} className="p-1.5 rounded hover:bg-white/8 text-text-muted hover:text-white transition-all"><Copy size={13} /></button>
        <button onClick={onDelete} className="p-1.5 rounded hover:bg-error/15 text-text-muted hover:text-error transition-all"><Trash2 size={13} /></button>
      </div>

      {!collapsed && (
        <>
          {/* Time range bar */}
          <div className="flex flex-wrap items-center gap-1.5 px-4 py-2 border-b border-border/30 bg-bg-input/20">
            {PRESETS.map(p => (
              <button key={p.label} onClick={() => setPreset(p.label)}
                className={`px-2.5 py-0.5 rounded text-[10px] font-medium transition-all ${
                  preset === p.label ? 'bg-accent-primary text-white' : 'text-text-muted hover:text-white hover:bg-white/8'
                }`}>
                {p.label}
              </button>
            ))}
            <button onClick={() => setPreset('custom')}
              className={`px-2.5 py-0.5 rounded text-[10px] font-medium transition-all ${
                preset === 'custom' ? 'bg-accent-primary text-white' : 'text-text-muted hover:text-white hover:bg-white/8'
              }`}>
              Custom
            </button>
            {preset === 'custom' && (
              <div className="flex items-center gap-1">
                <input type="datetime-local" className={inputCls} value={customFrom} onChange={e => setCustomFrom(e.target.value)} />
                <span className="text-text-muted text-[10px]">→</span>
                <input type="datetime-local" className={inputCls} value={customTo} onChange={e => setCustomTo(e.target.value)} />
              </div>
            )}
            <button onClick={fetchData} className="ml-auto p-1 rounded hover:bg-white/8 text-text-muted hover:text-white transition-all">
              <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>

          {/* Chart body */}
          <div className="p-4">
            {chart.points.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-text-muted">
                <TrendingUp size={40} className="opacity-20 mb-3" />
                <p className="text-sm">No points added</p>
                <button onClick={onEdit} className="mt-2 text-xs text-accent-primary hover:underline">+ Edit chart to add points</button>
              </div>
            ) : chartData.length === 0 && !loading ? (
              <div className="flex flex-col items-center justify-center py-12 text-text-muted">
                <p className="text-sm">No data in this time range</p>
                <p className="text-xs mt-1">Try expanding the range</p>
              </div>
            ) : (
              <div style={{ height: 300 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={chartData} margin={{ top: 5, right: hasRight ? 60 : 12, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={fmtTsShort}
                      tick={{ fontSize: 9, fill: '#64748b' }}
                      tickLine={false}
                      axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                      minTickGap={50}
                    />
                    {/* Left Y-axis */}
                    <YAxis
                      yAxisId="left"
                      orientation="left"
                      tick={{ fontSize: 9, fill: '#64748b' }}
                      tickLine={false}
                      axisLine={false}
                      width={46}
                    />
                    {/* Right Y-axis (only if needed) */}
                    {hasRight && (
                      <YAxis
                        yAxisId="right"
                        orientation="right"
                        tick={{ fontSize: 9, fill: '#64748b' }}
                        tickLine={false}
                        axisLine={false}
                        width={46}
                      />
                    )}
                    <Tooltip content={<CustomTooltip points={chart.points} />} />
                    <Legend
                      wrapperStyle={{ fontSize: 10, paddingTop: 8 }}
                      formatter={(value) => {
                        const pt = chart.points.find(p => p.mapping_id === value);
                        return <span style={{ color: '#94a3b8' }}>{pt?.label || value}</span>;
                      }}
                    />
                    <Brush
                      dataKey="timestamp"
                      height={20}
                      stroke="rgba(255,255,255,0.1)"
                      fill="rgba(255,255,255,0.02)"
                      tickFormatter={fmtTsShort}
                      travellerWidth={5}
                    />
                    {visiblePoints.map((pt) => {
                      const props = {
                        key: pt.id,
                        dataKey: pt.mapping_id,
                        name: pt.mapping_id,
                        yAxisId: pt.yAxis || 'left',
                        stroke: pt.color,
                        fill: pt.color,
                        strokeWidth: 1.5,
                      };
                      if (pt.type === 'area') return (
                        <Area {...props} type="monotone" fillOpacity={0.12} dot={false} activeDot={{ r: 3 }} connectNulls={false} />
                      );
                      if (pt.type === 'bar') return (
                        <Bar {...props} opacity={0.8} radius={[2, 2, 0, 0]} />
                      );
                      return (
                        <Line {...props} type="monotone" dot={false} activeDot={{ r: 3 }} connectNulls={false} />
                      );
                    })}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* Point legend below chart */}
          {chart.points.length > 0 && (
            <div className="px-4 pb-3 flex flex-wrap gap-2">
              {chart.points.map(pt => (
                <div key={pt.id} className={`flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full border transition-all ${
                  pt.visible !== false ? 'border-border/50 text-text-secondary' : 'border-border/20 text-text-muted opacity-50'
                }`}>
                  <span className="w-2 h-2 rounded-full" style={{ background: pt.color }} />
                  <span className="truncate max-w-[80px]">{pt.label}</span>
                  <span className="text-text-muted opacity-60">{pt.yAxis === 'right' ? '→R' : '→L'}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Main page ───────────────────────────────────────────────────
export function ChartsPage() {
  const [charts, setCharts] = useState(() => loadCharts());
  const [mappings, setMappings] = useState([]);
  const [groups, setGroups] = useState([]);
  const [editingChart, setEditingChart] = useState(null);  // chart obj or null
  const [isCreating, setIsCreating] = useState(false);

  // Persist on change
  useEffect(() => { saveCharts(charts); }, [charts]);

  // Fetch metadata
  useEffect(() => {
    Promise.all([
      fetch(`${API}/mappings`).then(r => r.json()),
      fetch(`${API}/groups`).then(r => r.json()),
    ]).then(([m, g]) => {
      setMappings(m.mappings || []);
      setGroups(g.groups || []);
    }).catch(console.error);
  }, []);

  const saveChart = (chart) => {
    setCharts(prev => {
      const idx = prev.findIndex(c => c.id === chart.id);
      if (idx >= 0) {
        const next = [...prev]; next[idx] = chart; return next;
      }
      return [...prev, chart];
    });
    setEditingChart(null);
    setIsCreating(false);
  };

  const deleteChart = (id) => {
    if (!confirm('Delete this chart?')) return;
    setCharts(prev => prev.filter(c => c.id !== id));
  };

  const duplicateChart = (chart) => {
    const copy = JSON.parse(JSON.stringify(chart));
    copy.id = uid();
    copy.name = `${chart.name} (copy)`;
    setCharts(prev => [...prev, copy]);
  };

  const startEdit = (chart) => {
    setEditingChart(JSON.parse(JSON.stringify(chart)));
    setIsCreating(false);
  };

  const startCreate = () => {
    setEditingChart(newChart(`Chart ${charts.length + 1}`));
    setIsCreating(true);
  };

  return (
    <div className="p-6 flex flex-col gap-4 min-h-screen">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <TrendingUp size={22} className="text-accent-primary" />
            Charts
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            {charts.length} chart{charts.length !== 1 ? 's' : ''} • Multiple points & Y-axes • Persisted locally
          </p>
        </div>
        <button onClick={startCreate}
          className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent-primary to-purple-600 text-white text-sm font-medium rounded-lg shadow-lg hover:-translate-y-0.5 transition-transform">
          <Plus size={16} /> New Chart
        </button>
      </div>

      {/* Charts list */}
      {charts.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-text-muted">
          <LayoutDashboard size={56} className="opacity-20 mb-4" />
          <p className="text-base font-medium text-text-secondary">No charts yet</p>
          <p className="text-sm mt-1">Create a chart to start visualizing BACnet points</p>
          <button onClick={startCreate}
            className="mt-5 flex items-center gap-2 px-5 py-2.5 bg-accent-primary/20 border border-accent-primary/40 text-accent-primary rounded-xl hover:bg-accent-primary/30 transition-all font-medium text-sm">
            <Plus size={16} /> Create First Chart
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {charts.map(chart => (
            <ChartViewer
              key={chart.id}
              chart={chart}
              mappings={mappings}
              onEdit={() => startEdit(chart)}
              onDelete={() => deleteChart(chart.id)}
              onDuplicate={() => duplicateChart(chart)}
            />
          ))}
        </div>
      )}

      {/* Editor modal */}
      {editingChart && (
        <ChartEditor
          chart={editingChart}
          mappings={mappings}
          groups={groups}
          onSave={saveChart}
          onClose={() => { setEditingChart(null); setIsCreating(false); }}
        />
      )}
    </div>
  );
}
