import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Trash2, Edit3, Check, X, Clock, Play, Pause, RefreshCw } from 'lucide-react';

const API = '/api';

export function SchedulerPage() {
  const [schedules, setSchedules] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ mapping_id: '', value: '', priority: 8, cron: '', enabled: true });

  const fetchData = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([
        fetch(`${API}/schedules`).then(r => r.json()),
        fetch(`${API}/mappings`).then(r => r.json()),
      ]);
      setSchedules(s.schedules || []);
      setMappings(m.mappings || []);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const createSchedule = async () => {
    try {
      await fetch(`${API}/schedules`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) });
      setShowNew(false); setForm({ mapping_id: '', value: '', priority: 8, cron: '', enabled: true });
      await fetchData();
    } catch (e) { console.error(e); }
  };

  const toggleEnabled = async (sched) => {
    try {
      await fetch(`${API}/schedules/${sched.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !sched.enabled }) });
      await fetchData();
    } catch (e) { console.error(e); }
  };

  const deleteSchedule = async (id) => {
    if (!confirm('Delete this schedule?')) return;
    try {
      await fetch(`${API}/schedules/${id}`, { method: 'DELETE' });
      await fetchData();
    } catch (e) { console.error(e); }
  };

  const getMappingLabel = (id) => {
    const m = mappings.find(x => x.id === id);
    return m ? (m.label || `${m.object_type}:${m.object_instance}`) : id;
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">Scheduler</h2>
          <p className="text-xs text-text-muted mt-1">{schedules.length} scheduled commands</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchData} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all"><RefreshCw size={16} /></button>
          <button onClick={() => setShowNew(true)}
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-sm font-medium">
            <Plus size={16} /> New Schedule
          </button>
        </div>
      </div>

      {showNew && (
        <div className="glass-card p-5 mb-5">
          <h3 className="text-sm font-bold mb-3">Create Schedule</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-text-muted block mb-1">Point</label>
              <select value={form.mapping_id} onChange={e => setForm(p => ({ ...p, mapping_id: e.target.value }))}
                className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus">
                <option value="">Select point...</option>
                {mappings.map(m => <option key={m.id} value={m.id}>{m.label || `${m.object_type}:${m.object_instance}`}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Cron Expression</label>
              <input type="text" placeholder="*/5 * * * *" value={form.cron} onChange={e => setForm(p => ({ ...p, cron: e.target.value }))}
                className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus" />
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Value</label>
              <input type="text" placeholder="Value to write" value={form.value} onChange={e => setForm(p => ({ ...p, value: e.target.value }))}
                className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus" />
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Priority</label>
              <select value={form.priority} onChange={e => setForm(p => ({ ...p, priority: Number(e.target.value) }))}
                className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus">
                {[8, 9, 10, 11, 12, 13, 14, 15, 16].map(p => <option key={p} value={p}>Priority {p}</option>)}
              </select>
            </div>
          </div>
          <div className="flex gap-2 mt-4">
            <button onClick={createSchedule} className="px-4 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-sm font-medium">Create</button>
            <button onClick={() => setShowNew(false)} className="px-4 py-2 border border-border rounded-lg text-text-secondary text-sm">Cancel</button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        {schedules.map(s => (
          <div key={s.id} className="glass-card p-4 flex items-center gap-4">
            <button onClick={() => toggleEnabled(s)} className={`p-2 rounded-lg ${s.enabled ? 'bg-success/15 text-success' : 'bg-bg-input text-text-muted'}`}>
              {s.enabled ? <Play size={16} /> : <Pause size={16} />}
            </button>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-white truncate">{getMappingLabel(s.mapping_id)}</div>
              <div className="flex items-center gap-3 text-xs text-text-secondary mt-0.5">
                <span className="flex items-center gap-1"><Clock size={12} /> {s.cron}</span>
                <span>→ <b className="text-accent-primary">{s.value}</b> @ P{s.priority}</span>
              </div>
            </div>
            <button onClick={() => deleteSchedule(s.id)} className="p-2 rounded-lg text-text-muted hover:text-error hover:bg-error/10"><Trash2 size={16} /></button>
          </div>
        ))}
        {schedules.length === 0 && (
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
