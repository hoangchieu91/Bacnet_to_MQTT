import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Trash2, Clock, Play, RefreshCw, CheckCircle, XCircle, Loader2, Save, X } from 'lucide-react';

const API = '/api';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function parseCron(cron) {
  // "HH:MM" or "HH:MM|0,1,2,3,4"
  const parts = cron.split('|');
  const time = parts[0]?.trim() || '';
  const days = parts[1] ? parts[1].split(',').map(d => parseInt(d.trim())).filter(d => !isNaN(d)) : [];
  return { time, days };
}

function buildCron(time, days) {
  if (!days || days.length === 0) return time;
  return `${time}|${days.sort().join(',')}`;
}

function RelativeTime({ iso }) {
  if (!iso) return null;
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  const label = diff < 60 ? `${diff}s ago` : diff < 3600 ? `${Math.floor(diff / 60)}m ago` : `${Math.floor(diff / 3600)}h ago`;
  return <span className="text-[10px] text-text-muted">{label}</span>;
}

const emptyForm = { name: '', mapping_id: '', value: '', priority: 8, cron: '', enabled: true, time: '08:00', days: [] };

export function SchedulerPage() {
  const [schedules, setSchedules] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [runStatus, setRunStatus] = useState({});   // { id: {time, success, message} }
  const [showNew, setShowNew] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [running, setRunning] = useState({});       // { id: true } while running

  const fetchData = useCallback(async () => {
    try {
      const [s, m, rs] = await Promise.all([
        fetch(`${API}/schedules`).then(r => r.json()),
        fetch(`${API}/mappings`).then(r => r.json()),
        fetch(`${API}/schedules/status`).then(r => r.json()).catch(() => ({ status: {} })),
      ]);
      setSchedules(s.schedules || []);
      setMappings(m.mappings || []);
      setRunStatus(rs.status || {});
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Poll run status every 5s
  useEffect(() => {
    const t = setInterval(() => {
      fetch(`${API}/schedules/status`).then(r => r.json()).then(d => setRunStatus(d.status || {})).catch(() => {});
    }, 5000);
    return () => clearInterval(t);
  }, []);

  const getMappingLabel = (id) => {
    const m = mappings.find(x => x.id === id);
    return m ? (m.label || `${m.object_type}:${m.object_instance}`) : id;
  };

  const buildFormPayload = (f) => {
    const { time, days, name, mapping_id, value, priority, enabled } = f;
    // Resolve mapping_id → device_id, object_type, object_instance
    const mapping = mappings.find(m => m.id === mapping_id);
    return {
      id: editingId || undefined,
      name: name || getMappingLabel(mapping_id),
      mapping_id,
      device_id: mapping?.device_id,
      object_type: mapping?.object_type,
      object_instance: mapping?.object_instance,
      value,
      priority,
      cron: buildCron(time, days),
      enabled,
    };
  };

  const saveSchedule = async () => {
    const payload = buildFormPayload(form);
    if (!payload.mapping_id || !payload.value || !payload.cron) return;
    try {
      if (editingId) {
        await fetch(`${API}/schedules/${editingId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      } else {
        await fetch(`${API}/schedules`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      }
      setShowNew(false); setEditingId(null); setForm(emptyForm);
      await fetchData();
    } catch (e) { console.error(e); }
  };

  const openEdit = (sched) => {
    const { time, days } = parseCron(sched.cron || '');
    setForm({ name: sched.name || '', mapping_id: sched.mapping_id || '', value: sched.value || '', priority: sched.priority || 8, cron: sched.cron || '', time, days, enabled: sched.enabled });
    setEditingId(sched.id);
    setShowNew(true);
  };

  const cancelForm = () => { setShowNew(false); setEditingId(null); setForm(emptyForm); };

  const toggleEnabled = async (sched) => {
    await fetch(`${API}/schedules/${sched.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !sched.enabled }) });
    await fetchData();
  };

  const deleteSchedule = async (id) => {
    if (!confirm('Delete this schedule?')) return;
    await fetch(`${API}/schedules/${id}`, { method: 'DELETE' });
    await fetchData();
  };

  const runNow = async (id) => {
    setRunning(p => ({ ...p, [id]: true }));
    try {
      await fetch(`${API}/schedules/${id}/run`, { method: 'POST' });
      await fetchData();
    } catch (e) { console.error(e); }
    setRunning(p => { const n = { ...p }; delete n[id]; return n; });
  };

  const toggleDay = (d) => setForm(p => ({
    ...p,
    days: p.days.includes(d) ? p.days.filter(x => x !== d) : [...p.days, d]
  }));

  const inputCls = "w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus";

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">Scheduler</h2>
          <p className="text-xs text-text-muted mt-1">{schedules.length} scheduled commands</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchData} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all"><RefreshCw size={16} /></button>
          <button onClick={() => { cancelForm(); setShowNew(true); }}
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-sm font-medium">
            <Plus size={16} /> New Schedule
          </button>
        </div>
      </div>

      {/* Create / Edit Form */}
      {showNew && (
        <div className="glass-card p-5 mb-5 border border-accent-primary/30">
          <h3 className="text-sm font-bold mb-4 text-accent-primary">{editingId ? 'Edit Schedule' : 'New Schedule'}</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="text-xs text-text-muted block mb-1">Name (optional)</label>
              <input className={inputCls} placeholder="e.g. Morning Setpoint" value={form.name}
                onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">BACnet Point</label>
              <select className={inputCls} value={form.mapping_id} onChange={e => setForm(p => ({ ...p, mapping_id: e.target.value }))}>
                <option value="">Select point...</option>
                {mappings.map(m => <option key={m.id} value={m.id}>{m.label || `${m.object_type}:${m.object_instance}`}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Time (HH:MM)</label>
              <input type="time" className={inputCls} value={form.time}
                onChange={e => setForm(p => ({ ...p, time: e.target.value }))} />
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Value to Write</label>
              <input className={inputCls} placeholder="e.g. 21.5 or True" value={form.value}
                onChange={e => setForm(p => ({ ...p, value: e.target.value }))} />
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Priority</label>
              <select className={inputCls} value={form.priority} onChange={e => setForm(p => ({ ...p, priority: Number(e.target.value) }))}>
                {[8, 9, 10, 11, 12, 13, 14, 15, 16].map(p => <option key={p} value={p}>Priority {p}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-2">Days (empty = every day)</label>
              <div className="flex gap-1.5 flex-wrap">
                {DAYS.map((d, i) => (
                  <button key={i} onClick={() => toggleDay(i)}
                    className={`px-2.5 py-1 text-xs rounded border font-medium transition-all ${form.days.includes(i)
                      ? 'bg-accent-primary/20 text-accent-primary border-accent-primary/50'
                      : 'text-text-muted border-border/40 hover:border-accent-primary/30'}`}>
                    {d}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="flex gap-2 mt-2">
            <button onClick={saveSchedule}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-sm font-medium">
              <Save size={14} /> {editingId ? 'Save Changes' : 'Create'}
            </button>
            <button onClick={cancelForm} className="px-4 py-2 border border-border rounded-lg text-text-secondary text-sm hover:text-white">
              <X size={14} className="inline mr-1" />Cancel
            </button>
          </div>
        </div>
      )}

      {/* Schedule List */}
      <div className="space-y-3">
        {schedules.map(s => {
          const { time, days } = parseCron(s.cron || '');
          const rs = runStatus[s.id];
          const isRunning = running[s.id];
          return (
            <div key={s.id} className={`glass-card p-4 ${!s.enabled ? 'opacity-60' : ''}`}>
              <div className="flex items-start gap-4">
                {/* Enable toggle */}
                <button onClick={() => toggleEnabled(s)}
                  className={`mt-0.5 w-8 h-4 rounded-full transition-all flex-shrink-0 ${s.enabled ? 'bg-success' : 'bg-bg-input border border-border'}`}>
                  <span className={`block w-3 h-3 rounded-full bg-white transition-all mx-0.5 ${s.enabled ? 'translate-x-4' : 'translate-x-0'}`} />
                </button>

                <div className="flex-1 min-w-0">
                  {/* Name + point */}
                  <div className="text-sm font-semibold text-white">
                    {s.name || getMappingLabel(s.mapping_id)}
                  </div>
                  {s.name && <div className="text-xs text-text-muted truncate">{getMappingLabel(s.mapping_id)}</div>}

                  {/* Schedule info row */}
                  <div className="flex flex-wrap items-center gap-3 mt-1.5">
                    <span className="flex items-center gap-1 text-xs text-text-secondary">
                      <Clock size={11} /> {time || s.cron}
                    </span>
                    {days.length > 0 && (
                      <div className="flex gap-1">
                        {DAYS.map((d, i) => (
                          <span key={i} className={`text-[9px] px-1 rounded font-bold ${days.includes(i) ? 'bg-accent-primary/20 text-accent-primary' : 'text-border'}`}>{d}</span>
                        ))}
                      </div>
                    )}
                    {days.length === 0 && <span className="text-[10px] text-text-muted">Every day</span>}
                    <span className="text-xs">→ <b className="text-accent-primary">{s.value}</b> @ P{s.priority}</span>
                  </div>

                  {/* Last run status */}
                  {rs && (
                    <div className={`flex items-center gap-1.5 mt-1.5 text-[11px] ${rs.success ? 'text-success' : 'text-error'}`}>
                      {rs.success ? <CheckCircle size={11} /> : <XCircle size={11} />}
                      <span className="truncate max-w-xs">{rs.message}</span>
                      <RelativeTime iso={rs.time} />
                    </div>
                  )}
                </div>

                {/* Action buttons */}
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <button onClick={() => runNow(s.id)} disabled={isRunning} title="Run Now"
                    className="flex items-center gap-1 px-2.5 py-1.5 border border-border rounded-lg text-text-secondary text-xs hover:text-success hover:border-success transition-all disabled:opacity-40">
                    {isRunning ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                    {isRunning ? 'Running...' : 'Run Now'}
                  </button>
                  <button onClick={() => openEdit(s)} title="Edit"
                    className="p-1.5 rounded-lg text-text-muted hover:text-white hover:bg-bg-card">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                  </button>
                  <button onClick={() => deleteSchedule(s.id)} title="Delete"
                    className="p-1.5 rounded-lg text-text-muted hover:text-error hover:bg-error/10">
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            </div>
          );
        })}

        {schedules.length === 0 && !showNew && (
          <div className="glass-card p-12 text-center">
            <Clock size={40} className="mx-auto text-text-muted mb-4 opacity-40" />
            <p className="text-text-secondary text-sm">No schedules configured</p>
            <p className="text-text-muted text-xs mt-1">Create a schedule to automatically write values at specific times</p>
          </div>
        )}
      </div>
    </div>
  );
}
