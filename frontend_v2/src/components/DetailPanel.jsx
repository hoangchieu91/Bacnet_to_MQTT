import React, { useState, useEffect, useCallback } from 'react';
import { X, Send, Unlock, RefreshCw, Loader2 } from 'lucide-react';
import { toast } from '../utils/toastStore';

const API = '/api';

const PRIORITY_NAMES = {
  1: 'Manual Life Safety', 2: 'Automatic Life Safety',
  3: 'Available (3)', 4: 'Available (4)',
  5: 'Critical Equipment', 6: 'Minimum On/Off',
  7: 'Available (7)', 8: 'Manual Operator',
  9: 'Available (9)', 10: 'Available (10)',
  11: 'Available (11)', 12: 'Available (12)',
  13: 'Available (13)', 14: 'Available (14)',
  15: 'Available (15)', 16: 'Default',
};

function PropRow({ label, value }) {
  if (value == null || value === '') return null;
  return (
    <div className="flex justify-between py-1.5 border-b border-border/50 last:border-0">
      <span className="text-xs text-text-muted font-medium">{label}</span>
      <span className="text-xs text-text-primary font-medium">{String(value)}</span>
    </div>
  );
}

export function DetailPanel({ mapping: m, onClose }) {
  if (!m) return null;

  const [pa, setPa] = useState({});
  const [pv, setPv] = useState(m.last_value);
  const [loadingPa, setLoadingPa] = useState(false);
  const [writeValue, setWriteValue] = useState('');
  const [writePriority, setWritePriority] = useState(8);
  const [writing, setWriting] = useState(false);
  const [releasing, setReleasing] = useState(null); // null | number | 'all'

  const ot = (m.object_type || '').toLowerCase();
  const isBinary = ot.includes('binary');
  const isMultiState = ot.includes('multi') || ot.includes('multistate');
  const isAnalog = ot.includes('analog');
  const isInput = ot.includes('input');
  const isWritable = !isInput;

  const fullLabel = m.label || `${m.object_type}:${m.object_instance}`;
  const shortLabel = fullLabel.split(/[.\/\\]/).pop() || fullLabel;

  // ── Fetch Priority Array ──────────────────
  const fetchPA = useCallback(async () => {
    setLoadingPa(true);
    try {
      const res = await fetch(`${API}/bacnet/priority_array/${m.device_id}/${m.object_type}/${m.object_instance}`);
      const data = await res.json();
      setPa(data.priority_array || {});
      if (data.present_value != null) setPv(data.present_value);
    } catch (e) { console.error('PA fetch error:', e); }
    setLoadingPa(false);
  }, [m.device_id, m.object_type, m.object_instance]);

  useEffect(() => { fetchPA(); }, [fetchPA]);

  // ── Write Value ──────────────────
  const handleWrite = async (val, pri) => {
    const v = val !== undefined ? val : writeValue;
    const p = pri !== undefined ? pri : writePriority;
    if (v === '' || v == null) { toast.warning('Enter a value before writing'); return; }
    setWriting(true);
    try {
      const res = await fetch(`${API}/bacnet/write`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: m.device_id, object_type: m.object_type, object_instance: m.object_instance, value: isBinary ? (v === 'active' ? 'active' : 'inactive') : isMultiState ? Number(v) : Number(v), priority: p })
      });
      const data = await res.json();
      if (data.success) {
        toast.success(`Written ${shortLabel} @ P${p}`);
        setWriteValue('');
        setTimeout(() => fetchPA(), 500);
      } else {
        toast.error(`Write ${shortLabel} failed: ${data.error || 'Device rejected. Check if writable & priority available.'}`);
      }
    } catch (e) { toast.error(`Network error: ${String(e)}`); }
    setWriting(false);
  };

  // ── Relinquish ──────────────────
  const handleRelease = async (priority) => {
    setReleasing(priority);
    try {
      const res = await fetch(`${API}/bacnet/release`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: m.device_id, object_type: m.object_type, object_instance: m.object_instance, priority })
      });
      const data = await res.json();
      if (data.success) {
        toast.success(priority === 'all' ? `Released all priorities — ${shortLabel}` : `Released P${priority} — ${shortLabel}`);
        setTimeout(() => fetchPA(), 500);
      } else {
        toast.error(`Release ${shortLabel} failed: ${data.error || 'Device rejected.'}`);
      }
    } catch (e) { toast.error(`Network error: ${String(e)}`); }
    setReleasing(null);
  };

  // Count active priorities
  const activePriorities = Object.entries(pa).filter(([, v]) => v !== null && v !== undefined && v !== 'null').length;

  return (
    <div className="fixed top-0 right-0 w-[420px] h-full bg-bg-secondary/95 backdrop-blur-2xl border-l border-border z-40 flex flex-col animate-slide-in overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between p-5 border-b border-border">
        <div className="min-w-0 flex-1">
          <h3 className="text-lg font-bold text-white truncate">{shortLabel}</h3>
          <p className="text-[10px] text-text-muted mt-0.5 truncate">{fullLabel}</p>
          <p className="text-[10px] text-text-muted">Device {m.device_id} • {m.object_type}:{m.object_instance}</p>
        </div>
        <button onClick={onClose} className="p-2 rounded-lg hover:bg-bg-card transition-colors text-text-muted hover:text-white">
          <X size={18} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-5 space-y-5">

        {/* Current Value */}
        <div className="glass-card p-4">
          <div className="text-[10px] uppercase tracking-widest text-text-muted font-semibold mb-2">Present Value</div>
          <div className="text-3xl font-bold text-gradient">
            {pv != null ? String(pv) : '—'}
            {isAnalog && m.units && <span className="text-sm font-normal text-text-muted ml-2">{m.units}</span>}
          </div>
          {isBinary && (
            <div className="mt-2 text-xs text-text-secondary">
              Active: <b className="text-success">{m.active_text || 'Active'}</b> • Inactive: <b className="text-error">{m.inactive_text || 'Inactive'}</b>
            </div>
          )}
          {isMultiState && m.state_text?.length > 0 && (
            <div className="mt-2 text-xs text-text-secondary">
              States: {m.state_text.map((s, i) => <span key={i} className="inline-block mr-1 px-1.5 py-0.5 rounded bg-info/10 text-info text-[10px]">{i + 1}={s}</span>)}
            </div>
          )}
        </div>

        {/* Write Control — Smart per type */}
        <div>
          <h4 className="text-xs font-bold uppercase tracking-widest text-text-muted mb-3">Write Value</h4>
          {!isWritable ? (
            <div className="glass-card p-4 flex items-center gap-3 border border-warning/30 bg-warning/5">
              <span className="text-2xl">🔒</span>
              <div>
                <div className="text-sm font-bold text-warning">Read-Only Point</div>
                <div className="text-xs text-text-secondary mt-0.5">
                  <b>{m.object_type}</b> (Input type) — cannot be written to. Only Output and Value types support BACnet write commands.
                </div>
              </div>
            </div>
          ) : (
            <div className="glass-card p-4 space-y-3">
              {isBinary ? (
                /* Binary: ON/OFF buttons */
                <div className="flex gap-2">
                  <button onClick={() => handleWrite('active', writePriority)} disabled={writing}
                    className="flex-1 px-4 py-2.5 rounded-lg font-bold text-sm bg-success/15 text-success border border-success/30 hover:bg-success/25 disabled:opacity-50 transition-all">
                    {m.active_text || 'Active'} (ON)
                  </button>
                  <button onClick={() => handleWrite('inactive', writePriority)} disabled={writing}
                    className="flex-1 px-4 py-2.5 rounded-lg font-bold text-sm bg-error/15 text-error border border-error/30 hover:bg-error/25 disabled:opacity-50 transition-all">
                    {m.inactive_text || 'Inactive'} (OFF)
                  </button>
                </div>
              ) : isMultiState && m.state_text?.length > 0 ? (
                /* MultiState: dropdown of states */
                <div className="flex items-center gap-2">
                  <select value={writeValue} onChange={e => setWriteValue(e.target.value)}
                    className="flex-1 px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus">
                    <option value="">Select state...</option>
                    {m.state_text.map((s, i) => <option key={i} value={i + 1}>{i + 1} — {s}</option>)}
                  </select>
                  <button onClick={() => handleWrite()} disabled={writing || !writeValue}
                    className="p-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white disabled:opacity-50" title="Write">
                    {writing ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                  </button>
                </div>
              ) : (
                /* Analog: number input with units */
                <div className="flex items-center gap-2">
                  <div className="relative flex-1">
                    <input type="number" step="any" placeholder="Value" value={writeValue} onChange={e => setWriteValue(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && handleWrite()}
                      className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus" />
                    {m.units && <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-text-muted">{m.units}</span>}
                  </div>
                  <button onClick={() => handleWrite()} disabled={writing || !writeValue}
                    className="p-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white disabled:opacity-50" title="Write">
                    {writing ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                  </button>
                </div>
              )}

              {/* Priority selector */}
              <div className="flex items-center gap-2 text-xs">
                <span className="text-text-muted">Priority:</span>
                <select value={writePriority} onChange={e => setWritePriority(Number(e.target.value))}
                  className="px-2 py-1 bg-bg-input border border-border rounded text-xs text-white focus:outline-none focus:border-border-focus">
                  {Array.from({ length: 16 }, (_, i) => i + 1).map(p => <option key={p} value={p}>P{p} — {PRIORITY_NAMES[p]}</option>)}
                </select>
              </div>
            </div>
          )}
        </div>

        {/* Priority Array */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-xs font-bold uppercase tracking-widest text-text-muted">
              Priority Array <span className="text-accent-primary ml-1">{activePriorities > 0 ? `(${activePriorities} active)` : ''}</span>
            </h4>
            <div className="flex items-center gap-1">
              <button onClick={fetchPA} disabled={loadingPa} className="p-1 rounded hover:bg-bg-card text-text-muted hover:text-white" title="Refresh">
                <RefreshCw size={12} className={loadingPa ? 'animate-spin' : ''} />
              </button>
              {activePriorities > 0 && (
                <button onClick={() => handleRelease('all')} disabled={releasing === 'all'}
                  className="flex items-center gap-1 px-2 py-1 rounded-md bg-error/10 text-error hover:bg-error/20 text-[10px] font-bold transition-all disabled:opacity-50" title="Release all">
                  {releasing === 'all' ? <Loader2 size={10} className="animate-spin" /> : <Unlock size={10} />}
                  Relinquish All
                </button>
              )}
            </div>
          </div>
          <div className="glass-card p-2">
            {Array.from({ length: 16 }, (_, i) => i + 1).map(i => {
              const val = pa[String(i)];
              const hasValue = val !== null && val !== undefined && val !== 'null';
              const isReleasing = releasing === i;
              return (
                <div key={i} className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs ${hasValue ? 'bg-success/5' : ''}`}>
                  <span className={`inline-flex items-center justify-center w-6 h-5 rounded text-[10px] font-bold ${hasValue ? 'bg-success/20 text-success' : 'bg-bg-input text-text-muted'}`}>
                    {i}
                  </span>
                  <span className="flex-1 text-text-secondary truncate">{PRIORITY_NAMES[i]}</span>
                  <span className={hasValue ? 'font-bold text-accent-primary min-w-[50px] text-right' : 'text-text-muted min-w-[50px] text-right'}>
                    {hasValue ? String(val) : 'null'}
                  </span>
                  {hasValue && (
                    <button onClick={() => handleRelease(i)} disabled={isReleasing}
                      className="p-1 rounded hover:bg-error/10 text-text-muted hover:text-error transition-colors disabled:opacity-50" title={`Release P${i}`}>
                      {isReleasing ? <Loader2 size={12} className="animate-spin" /> : <Unlock size={12} />}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Properties */}
        <div>
          <h4 className="text-xs font-bold uppercase tracking-widest text-text-muted mb-3">Properties</h4>
          <div className="glass-card p-4">
            <PropRow label="Description" value={m.description} />
            <PropRow label="Units" value={m.units} />
            <PropRow label="Active Text" value={m.active_text} />
            <PropRow label="Inactive Text" value={m.inactive_text} />
            <PropRow label="State Text" value={m.state_text?.join(', ')} />
            <PropRow label="Read Mode" value={m.read_mode || 'poll'} />
            <PropRow label="Poll Interval" value={`${m.poll_interval}s`} />
            <PropRow label="MQTT Topic" value={m.mqtt_topic || 'auto'} />
            <PropRow label="Group" value={m.group} />
          </div>
        </div>
      </div>
    </div>
  );
}
