import React, { useState, useEffect, useCallback } from 'react';
import { AlertTriangle, Plus, Trash2, Edit3, X, CheckCircle, Bell, RefreshCw } from 'lucide-react';

const API = '/api';
const INPUT_CLS = 'w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus transition-all';
const SELECT_CLS = 'w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus transition-all';
const LABEL_CLS = 'block text-xs text-text-muted mb-1 font-medium uppercase tracking-wider';

function timeSince(iso) {
  if (!iso) return '—';
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m ago`;
}

function ModalBase({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-bg-secondary border border-border rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h3 className="text-base font-bold text-white">{title}</h3>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-bg-input text-text-muted hover:text-white"><X size={18} /></button>
        </div>
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}

const CONDITION_OPS = [
  { v: 'gt', l: '> Greater than' }, { v: 'gte', l: '>= Greater or equal' },
  { v: 'lt', l: '< Less than' }, { v: 'lte', l: '<= Less or equal' },
  { v: 'eq', l: '= Equals' }, { v: 'ne', l: '≠ Not equal' },
];

function RuleFormModal({ rule, mappings, onSave, onClose }) {
  const [form, setForm] = useState({
    name: rule?.name || '',
    trigger_mapping_id: rule?.trigger_mapping_id || '',
    trigger_op: (rule?.trigger_condition?.split(':')[0]) || 'gt',
    trigger_val: (rule?.trigger_condition?.split(':').slice(1).join(':')) || '',
    expected_mapping_id: rule?.expected_mapping_id || '',
    expected_value: rule?.expected_value ?? '',
    tolerance_seconds: rule?.tolerance_seconds ?? 120,
    severity: rule?.severity || 'warning',
    notify_topic: rule?.notify_topic || '',
    enabled: rule?.enabled !== false,
  });
  const [hasResponse, setHasResponse] = useState(!!(rule?.expected_mapping_id));
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    const payload = {
      ...form,
      trigger_condition: `${form.trigger_op}:${form.trigger_val}`,
      tolerance_seconds: Number(form.tolerance_seconds),
      // Clear response fields if not tracking response
      expected_mapping_id: hasResponse ? form.expected_mapping_id : '',
      expected_value: hasResponse ? form.expected_value : '',
    };
    delete payload.trigger_op; delete payload.trigger_val;
    await onSave(payload);
    setSaving(false);
  };

  const labelOf = (id) => {
    const m = mappings.find(m => m.id === id);
    return m ? (m.label || `${m.object_type}:${m.object_instance}`) : id;
  };

  return (
    <ModalBase title={rule ? '✏️ Edit Rule' : '➕ New Anomaly Rule'} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className={LABEL_CLS}>Rule Name *</label>
          <input className={INPUT_CLS} required placeholder="e.g. FCU must cool when temp > 26°C"
            value={form.name} onChange={e => set('name', e.target.value)} />
        </div>

        <div className="bg-bg-input/30 rounded-xl p-4 border border-border/40 space-y-3">
          <div className="text-[10px] uppercase tracking-widest text-warning/80 font-bold">🔴 Trigger — when this point...</div>
          <div>
            <label className={LABEL_CLS}>Trigger Point *</label>
            <select className={SELECT_CLS} required value={form.trigger_mapping_id} onChange={e => set('trigger_mapping_id', e.target.value)}>
              <option value="">— Select point —</option>
              {mappings.map(m => <option key={m.id} value={m.id}>{m.label || `Dev${m.device_id} ${m.object_type}:${m.object_instance}`}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL_CLS}>Condition</label>
              <select className={SELECT_CLS} value={form.trigger_op} onChange={e => set('trigger_op', e.target.value)}>
                {CONDITION_OPS.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
              </select>
            </div>
            <div>
              <label className={LABEL_CLS}>Value *</label>
              <input className={INPUT_CLS} required placeholder="e.g. 26.0 or active"
                value={form.trigger_val} onChange={e => set('trigger_val', e.target.value)} />
            </div>
          </div>
        </div>

        <div className="bg-bg-input/30 rounded-xl p-4 border border-border/40 space-y-3">
          {/* Header with toggle */}
          <div className="flex items-center justify-between">
            <div className="text-[10px] uppercase tracking-widest text-success/80 font-bold">🟢 Expected — then this point must respond</div>
            <label className="flex items-center gap-2 cursor-pointer">
              <span className="text-xs text-text-muted">{hasResponse ? 'Enabled' : 'Alarm only'}</span>
              <div className="relative inline-block w-9 h-5">
                <input type="checkbox" checked={hasResponse} onChange={e => setHasResponse(e.target.checked)} className="sr-only peer" />
                <div className="w-9 h-5 rounded-full bg-bg-input border border-border peer-checked:bg-success/70 peer-checked:border-success/70 transition-all" />
                <div className="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-text-muted peer-checked:bg-white peer-checked:translate-x-4 transition-all" />
              </div>
            </label>
          </div>

          {hasResponse ? (
            <>
              <div>
                <label className={LABEL_CLS}>Response Point *</label>
                <select className={SELECT_CLS} required value={form.expected_mapping_id} onChange={e => set('expected_mapping_id', e.target.value)}>
                  <option value="">— Select point —</option>
                  {mappings.map(m => <option key={m.id} value={m.id}>{m.label || `Dev${m.device_id} ${m.object_type}:${m.object_instance}`}</option>)}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={LABEL_CLS}>Expected Value *</label>
                  <input className={INPUT_CLS} required placeholder="e.g. cooling or 1 or active"
                    value={form.expected_value} onChange={e => set('expected_value', e.target.value)} />
                </div>
                <div>
                  <label className={LABEL_CLS}>Grace Period (s)</label>
                  <input className={INPUT_CLS} type="number" min="5" max="3600"
                    value={form.tolerance_seconds} onChange={e => set('tolerance_seconds', e.target.value)} />
                </div>
              </div>
            </>
          ) : (
            <div className="text-xs text-text-muted bg-bg-input/40 rounded-lg p-3 border border-border/30">
              <span className="text-warning font-medium">⚡ Alarm mode:</span> Alarm triggers immediately when condition is met. No response tracking.
              <div className="mt-2">
                <label className={LABEL_CLS}>Grace Period (s) — delay before alarm (0 = immediate)</label>
                <input className={INPUT_CLS} type="number" min="0" max="3600"
                  value={form.tolerance_seconds} onChange={e => set('tolerance_seconds', e.target.value)} />
              </div>
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={LABEL_CLS}>Severity</label>
            <select className={SELECT_CLS} value={form.severity} onChange={e => set('severity', e.target.value)}>
              <option value="warning">⚠️ Warning</option>
              <option value="critical">🔴 Critical</option>
            </select>
          </div>
          <div>
            <label className={LABEL_CLS}>MQTT Alert Topic (optional)</label>
            <input className={INPUT_CLS} placeholder="bacnet/alerts/..."
              value={form.notify_topic} onChange={e => set('notify_topic', e.target.value)} />
          </div>
        </div>

        <div className="flex gap-3 pt-2">
          <button type="button" onClick={onClose} className="flex-1 px-4 py-2 border border-border rounded-lg text-sm text-text-secondary hover:text-white transition-all">Cancel</button>
          <button type="submit" disabled={saving} className="flex-1 px-4 py-2 bg-accent-gradient rounded-lg text-white text-sm font-medium disabled:opacity-50">
            {saving ? 'Saving…' : rule ? '✓ Update Rule' : '✓ Create Rule'}
          </button>
        </div>
      </form>
    </ModalBase>
  );
}

export function AnomalyPage() {
  const [tab, setTab] = useState('active'); // 'active' | 'rules'
  const [alarms, setAlarms] = useState([]);
  const [rules, setRules] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editRule, setEditRule] = useState(null);
  const [toast, setToast] = useState('');

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [al, ru, mp] = await Promise.allSettled([
        fetch(`${API}/anomaly/active`).then(r => r.json()),
        fetch(`${API}/anomaly/rules`).then(r => r.json()),
        fetch(`${API}/mappings`).then(r => r.json()),
      ]);
      if (al.status === 'fulfilled') setAlarms(al.value.alarms || []);
      if (ru.status === 'fulfilled') setRules(ru.value.rules || []);
      if (mp.status === 'fulfilled') setMappings(mp.value.mappings || []);
    } catch (e) { console.error(e); }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchAll();
    const t = setInterval(fetchAll, 15000);
    return () => clearInterval(t);
  }, [fetchAll]);

  const createRule = async (payload) => {
    await fetch(`${API}/anomaly/rules`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    await fetchAll();
    setShowForm(false);
    showToast('✅ Rule created');
  };

  const updateRule = async (id, payload) => {
    await fetch(`${API}/anomaly/rules/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    await fetchAll();
    setEditRule(null);
    showToast('✅ Rule updated');
  };

  const deleteRule = async (id) => {
    if (!confirm('Delete this rule?')) return;
    await fetch(`${API}/anomaly/rules/${id}`, { method: 'DELETE' });
    await fetchAll();
    showToast('🗑 Rule deleted');
  };

  const toggleRule = async (rule) => {
    await fetch(`${API}/anomaly/rules/${rule.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !rule.enabled })
    });
    await fetchAll();
  };

  const labelOf = (id) => {
    const m = mappings.find(m => m.id === id);
    return m ? (m.label || `Dev${m.device_id} ${m.object_type}:${m.object_instance}`) : id;
  };

  return (
    <div className="p-6 flex flex-col h-screen">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 z-[100] px-4 py-2 bg-success/10 border border-success/30 text-success rounded-lg text-sm font-medium shadow-lg">{toast}</div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-2xl font-bold text-white flex items-center gap-2">
            <AlertTriangle size={24} className="text-warning" /> Anomaly Monitor
          </h2>
          <p className="text-xs text-text-muted mt-1">
            {alarms.length > 0 ? <span className="text-error font-medium">🔴 {alarms.length} active alarm{alarms.length > 1 ? 's' : ''}</span> : <span className="text-success">🟢 All clear</span>}
            {' • '}{rules.length} rule{rules.length !== 1 ? 's' : ''} configured
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={fetchAll} disabled={loading} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button onClick={() => setShowForm(true)}
            className="flex items-center gap-2 px-4 py-2 bg-accent-gradient rounded-lg text-white text-sm font-medium shadow-[0_2px_12px_var(--color-accent-glow)] hover:-translate-y-0.5 transition-transform">
            <Plus size={16} /> New Rule
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-5 bg-bg-input/30 p-1 rounded-xl w-fit border border-border/40">
        {[['active', `🔴 Active Alarms (${alarms.length})`], ['rules', `📋 Rules (${rules.length})`]].map(([t, l]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${tab === t ? 'bg-bg-secondary text-white shadow-md border border-border/60' : 'text-text-secondary hover:text-white'}`}>
            {l}
          </button>
        ))}
      </div>

      {/* Active Alarms Tab */}
      {tab === 'active' && (
        <div className="flex-1 overflow-y-auto space-y-3">
          {alarms.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-text-muted">
              <CheckCircle size={48} className="mb-3 opacity-30 text-success" />
              <p className="text-sm font-medium">No active anomalies</p>
              <p className="text-xs mt-1">All monitored scenarios are within expected parameters</p>
            </div>
          ) : alarms.map(alarm => (
            <div key={alarm.rule_id} className={`glass-card p-4 border-l-4 ${alarm.severity === 'critical' ? 'border-l-error' : 'border-l-warning'}`}>
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-bold ${alarm.severity === 'critical' ? 'bg-error/20 text-error' : 'bg-warning/20 text-warning'}`}>
                      {alarm.severity === 'critical' ? '🔴 CRITICAL' : '⚠️ WARNING'}
                    </span>
                    <span className="text-xs text-text-muted">{alarm.alarm_count}× alarm</span>
                    <span className="text-xs text-text-muted">• {timeSince(alarm.last_alarm_at)}</span>
                  </div>
                  <h4 className="text-sm font-bold text-white mb-2">{alarm.rule_name}</h4>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="bg-bg-input/40 rounded-lg p-2 border border-border/30">
                      <div className="text-text-muted mb-0.5">Response Point</div>
                      <div className="font-medium truncate">{labelOf(alarm.expected_mapping)}</div>
                    </div>
                    <div className="bg-bg-input/40 rounded-lg p-2 border border-border/30">
                      <div className="text-text-muted mb-0.5">Expected → Actual</div>
                      <div>
                        <span className="text-success font-bold">{String(alarm.expected_value)}</span>
                        <span className="text-text-muted mx-1">→</span>
                        <span className="text-error font-bold">{alarm.actual_value ?? '—'}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Rules Tab */}
      {tab === 'rules' && (
        <div className="flex-1 overflow-y-auto space-y-2">
          {rules.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-text-muted">
              <Bell size={48} className="mb-3 opacity-30" />
              <p className="text-sm font-medium">No rules configured</p>
              <p className="text-xs mt-1">Create a rule to monitor BACnet point scenarios</p>
            </div>
          ) : rules.map(rule => (
            <div key={rule.id} className={`glass-card p-4 transition-all ${!rule.enabled ? 'opacity-50' : ''}`}>
              <div className="flex items-center gap-3">
                {/* Toggle */}
                <label className="relative inline-block w-9 h-5 cursor-pointer shrink-0">
                  <input type="checkbox" checked={rule.enabled !== false} onChange={() => toggleRule(rule)} className="sr-only peer" />
                  <div className="w-9 h-5 rounded-full bg-bg-input border border-border peer-checked:bg-accent-primary peer-checked:border-accent-primary transition-all" />
                  <div className="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-text-muted peer-checked:bg-white peer-checked:translate-x-4 transition-all" />
                </label>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-bold text-white truncate">{rule.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-bold ${rule.severity === 'critical' ? 'bg-error/20 text-error' : 'bg-warning/20 text-warning'}`}>
                      {rule.severity}
                    </span>
                  </div>
                  <div className="text-xs text-text-muted">
                    When <span className="text-white">{labelOf(rule.trigger_mapping_id)}</span>
                    {' '}<span className="text-accent-primary">{rule.trigger_condition}</span>
                    {rule.expected_mapping_id ? (
                      <>
                        {' '}&rarr; expect <span className="text-white">{labelOf(rule.expected_mapping_id)}</span>
                        {' '}= <span className="text-success">{rule.expected_value}</span>
                        {' '}within <span className="text-info">{rule.tolerance_seconds}s</span>
                      </>
                    ) : (
                      <>
                        {' '}→ <span className="text-warning">⚡ alarm</span>
                        {rule.tolerance_seconds > 0 && <> after <span className="text-info">{rule.tolerance_seconds}s</span></>}
                      </>
                    )}
                  </div>
                </div>

                <div className="flex gap-1 shrink-0">
                  <button onClick={() => { setEditRule(rule); }} className="p-1.5 rounded-lg hover:bg-bg-input text-text-muted hover:text-white transition-all">
                    <Edit3 size={14} />
                  </button>
                  <button onClick={() => deleteRule(rule.id)} className="p-1.5 rounded-lg hover:bg-error/10 text-text-muted hover:text-error transition-all">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modals */}
      {showForm && (
        <RuleFormModal mappings={mappings} onSave={createRule} onClose={() => setShowForm(false)} />
      )}
      {editRule && (
        <RuleFormModal rule={editRule} mappings={mappings}
          onSave={(payload) => updateRule(editRule.id, payload)}
          onClose={() => setEditRule(null)} />
      )}
    </div>
  );
}
