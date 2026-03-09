import React, { useState, useEffect, useCallback } from 'react';
import { Save, RefreshCw, TestTube, CheckCircle, XCircle, Download, Upload, Plus, Trash2, Webhook, Loader2, Shield, Eye, EyeOff } from 'lucide-react';
import { useAuth } from '../App';

const API = '/api';

function Section({ title, children }) {
  return (
    <div className="glass-card p-5 mb-5">
      <h3 className="text-xs uppercase tracking-widest text-text-muted font-bold mb-4">{title}</h3>
      {children}
    </div>
  );
}

function Field({ label, value, onChange, type = 'text', placeholder = '', disabled = false }) {
  return (
    <div>
      <label className="text-xs text-text-muted block mb-1">{label}</label>
      <input type={type} value={value || ''} onChange={e => onChange(e.target.value)} placeholder={placeholder} disabled={disabled}
        className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus disabled:opacity-50" />
    </div>
  );
}

// ── Webhook Tab Component ───────────────────────────────────────
function WebhooksTab() {
  const [webhooks, setWebhooks] = useState([]);
  const [editingId, setEditingId] = useState(null); // null = none, 'new' = new form
  const [form, setForm] = useState({ name: '', url: '', severity_filter: ['warning', 'critical'], secret_header: '', enabled: true });
  const [testResults, setTestResults] = useState({}); // { [id]: { loading, ok, status, error } }

  const fetchWebhooks = useCallback(async () => {
    try {
      const res = await fetch(`${API}/webhooks`);
      const data = await res.json();
      setWebhooks(data.webhooks || []);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { fetchWebhooks(); }, [fetchWebhooks]);

  const resetForm = () => setForm({ name: '', url: '', severity_filter: ['warning', 'critical'], secret_header: '', enabled: true });

  const openNew = () => { resetForm(); setEditingId('new'); };
  const openEdit = (wh) => { setForm({ ...wh }); setEditingId(wh.id); };
  const cancel = () => { setEditingId(null); resetForm(); };

  const saveWebhook = async () => {
    if (!form.url.trim()) return;
    try {
      if (editingId === 'new') {
        await fetch(`${API}/webhooks`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) });
      } else {
        await fetch(`${API}/webhooks/${editingId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) });
      }
      await fetchWebhooks();
      cancel();
    } catch (e) { console.error(e); }
  };

  const deleteWebhook = async (id) => {
    if (!confirm('Delete this webhook?')) return;
    await fetch(`${API}/webhooks/${id}`, { method: 'DELETE' });
    await fetchWebhooks();
  };

  const toggleEnabled = async (wh) => {
    await fetch(`${API}/webhooks/${wh.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !wh.enabled }) });
    await fetchWebhooks();
  };

  const sendTest = async (id) => {
    setTestResults(p => ({ ...p, [id]: { loading: true } }));
    try {
      const res = await fetch(`${API}/webhooks/${id}/test`, { method: 'POST' });
      const data = await res.json();
      setTestResults(p => ({ ...p, [id]: { loading: false, ...data } }));
      setTimeout(() => setTestResults(p => { const n = { ...p }; delete n[id]; return n; }), 6000);
    } catch (e) {
      setTestResults(p => ({ ...p, [id]: { loading: false, ok: false, error: e.message } }));
    }
  };

  const toggleSeverity = (sev) => {
    setForm(p => ({
      ...p,
      severity_filter: p.severity_filter.includes(sev)
        ? p.severity_filter.filter(s => s !== sev)
        : [...p.severity_filter, sev]
    }));
  };

  const SEV_COLORS = { info: 'text-blue-400 border-blue-500/40', warning: 'text-yellow-400 border-yellow-500/40', critical: 'text-red-400 border-red-500/40' };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <p className="text-xs text-text-secondary">Nhận HTTP POST khi có alarm. Hỗ trợ bất kỳ webhook nào: Slack, Teams, n8n, Make, custom API...</p>
        <button onClick={openNew} className="flex items-center gap-2 px-3 py-1.5 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-xs font-medium">
          <Plus size={13} /> Add Webhook
        </button>
      </div>

      {/* New / Edit form */}
      {editingId && (
        <div className="glass-card p-4 mb-4 border border-accent-primary/30">
          <h4 className="text-xs font-bold text-accent-primary mb-3">{editingId === 'new' ? 'New Webhook' : 'Edit Webhook'}</h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
            <Field label="Name" value={form.name} onChange={v => setForm(p => ({ ...p, name: v }))} placeholder="e.g. Teams Alert" />
            <Field label="URL" value={form.url} onChange={v => setForm(p => ({ ...p, url: v }))} placeholder="https://..." />
            <Field label="Secret Header (X-Webhook-Secret)" value={form.secret_header} onChange={v => setForm(p => ({ ...p, secret_header: v }))} placeholder="Optional" />
            <div>
              <label className="text-xs text-text-muted block mb-2">Severity Filter</label>
              <div className="flex gap-2">
                {['info', 'warning', 'critical'].map(sev => (
                  <button key={sev} onClick={() => toggleSeverity(sev)}
                    className={`px-2.5 py-1 text-xs rounded border font-medium transition-all ${form.severity_filter.includes(sev) ? SEV_COLORS[sev] + ' bg-current/10' : 'text-text-muted border-border/30'}`}>
                    {sev}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={saveWebhook} className="px-3 py-1.5 bg-success/20 text-success border border-success/30 rounded text-xs font-medium hover:bg-success/30">
              <CheckCircle size={12} className="inline mr-1" />Save
            </button>
            <button onClick={cancel} className="px-3 py-1.5 bg-bg-input border border-border rounded text-xs text-text-muted hover:text-white">Cancel</button>
          </div>
        </div>
      )}

      {/* Webhook list */}
      <div className="space-y-2">
        {webhooks.length === 0 && !editingId && (
          <div className="text-center py-8 text-text-muted text-sm">
            <Webhook size={32} className="mx-auto mb-2 opacity-30" />
            <p>Chưa có webhook nào. Nhấn Add Webhook để bắt đầu.</p>
          </div>
        )}
        {webhooks.map(wh => {
          const tr = testResults[wh.id];
          return (
            <div key={wh.id} className={`glass-card p-3 flex items-center gap-3 ${!wh.enabled ? 'opacity-50' : ''}`}>
              {/* Enable toggle */}
              <button onClick={() => toggleEnabled(wh)}
                className={`w-8 h-4 rounded-full transition-all flex-shrink-0 ${wh.enabled ? 'bg-success' : 'bg-bg-input border border-border'}`}>
                <span className={`block w-3 h-3 rounded-full bg-white transition-all mx-0.5 ${wh.enabled ? 'translate-x-4' : 'translate-x-0'}`} />
              </button>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-white truncate">{wh.name || 'Unnamed'}</span>
                  <div className="flex gap-1">
                    {(wh.severity_filter || []).map(s => (
                      <span key={s} className={`text-[9px] px-1.5 py-0 rounded border font-bold ${SEV_COLORS[s]}`}>{s}</span>
                    ))}
                  </div>
                </div>
                <p className="text-[11px] text-text-muted truncate">{wh.url}</p>
              </div>

              {/* Test result */}
              {tr && (
                <span className={`text-xs flex items-center gap-1 flex-shrink-0 ${tr.loading ? 'text-text-muted' : tr.ok ? 'text-success' : 'text-error'}`}>
                  {tr.loading ? <Loader2 size={12} className="animate-spin" /> : tr.ok ? <CheckCircle size={12} /> : <XCircle size={12} />}
                  {tr.loading ? 'Testing...' : tr.ok ? `${tr.status} OK (${tr.elapsed_ms}ms)` : tr.error || `HTTP ${tr.status}`}
                </span>
              )}

              {/* Actions */}
              <div className="flex items-center gap-1 flex-shrink-0">
                <button onClick={() => sendTest(wh.id)} disabled={tr?.loading}
                  className="px-2 py-1 text-[10px] border border-border rounded text-text-muted hover:text-white hover:border-accent-primary transition-all disabled:opacity-40">
                  Test
                </button>
                <button onClick={() => openEdit(wh)} className="p-1.5 text-text-muted hover:text-white rounded hover:bg-bg-card">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                </button>
                <button onClick={() => deleteWebhook(wh.id)} className="p-1.5 text-text-muted hover:text-error rounded hover:bg-error/10">
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Payload format reference */}
      {webhooks.length > 0 && (
        <details className="mt-4">
          <summary className="text-xs text-text-muted cursor-pointer hover:text-white">Xem sample payload JSON</summary>
          <pre className="mt-2 p-3 bg-bg-input rounded-lg text-[10px] text-text-secondary overflow-auto">{JSON.stringify({
            event: "threshold_breach",
            severity: "warning",
            timestamp: "2026-03-07T14:00:00Z",
            point: { label: "HBP456_Temp", device_id: 10121, object_type: "analogInput", object_instance: 1 },
            value: "32.5",
            alarm_state: "high-limit",
            message: "HBP456_Temp: high-limit (val=32.5)"
          }, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

// ── Users Tab Component ────────────────────────────────────────
function UsersTab() {
  const [users, setUsers] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ username: '', password: '', role: 'viewer' });
  const [showPw, setShowPw] = useState(false);
  const [saving, setSaving] = useState(false);

  const fetchUsers = useCallback(async () => {
    try {
      const res = await fetch(`${API}/users`);
      if (res.ok) {
        const data = await res.json();
        setUsers(data.users || []);
      }
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const createUser = async () => {
    if (!form.username || !form.password) return;
    setSaving(true);
    try {
      const res = await fetch(`${API}/users`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      if (res.ok) { setForm({ username: '', password: '', role: 'viewer' }); setShowForm(false); await fetchUsers(); }
    } catch (e) { console.error(e); }
    setSaving(false);
  };

  const deleteUser = async (id) => {
    if (!confirm('Delete this user?')) return;
    await fetch(`${API}/users/${id}`, { method: 'DELETE' });
    await fetchUsers();
  };

  const changeRole = async (id, role) => {
    await fetch(`${API}/users/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role }) });
    await fetchUsers();
  };

  const toggleEnabled = async (u) => {
    await fetch(`${API}/users/${u.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !u.enabled }) });
    await fetchUsers();
  };

  const ROLE_COLORS = { admin: 'text-accent-primary border-accent-primary/40', operator: 'text-yellow-400 border-yellow-400/40', viewer: 'text-text-muted border-border' };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <p className="text-xs text-text-secondary">Quản lý tài khoản đăng nhập. Khi có ít nhất 1 user, gateway yêu cầu đăng nhập.</p>
        <button onClick={() => setShowForm(true)} className="flex items-center gap-2 px-3 py-1.5 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-xs font-medium">
          <Plus size={13} /> Add User
        </button>
      </div>

      {showForm && (
        <div className="glass-card p-4 mb-4 border border-accent-primary/30">
          <h4 className="text-xs font-bold text-accent-primary mb-3">New User</h4>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
            <div>
              <label className="text-xs text-text-muted block mb-1">Username</label>
              <input value={form.username} onChange={e => setForm(p => ({ ...p, username: e.target.value }))}
                className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus" placeholder="username" />
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Password</label>
              <div className="relative">
                <input type={showPw ? 'text' : 'password'} value={form.password} onChange={e => setForm(p => ({ ...p, password: e.target.value }))}
                  className="w-full px-3 py-2 pr-9 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus" placeholder="••••••" />
                <button type="button" onClick={() => setShowPw(p => !p)} className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted">
                  {showPw ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
              </div>
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Role</label>
              <select value={form.role} onChange={e => setForm(p => ({ ...p, role: e.target.value }))}
                className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none">
                <option value="viewer">Viewer (read-only)</option>
                <option value="operator">Operator (read + write BACnet)</option>
                <option value="admin">Admin (full access)</option>
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={createUser} disabled={saving || !form.username || !form.password}
              className="px-3 py-1.5 bg-success/20 text-success border border-success/30 rounded text-xs font-medium hover:bg-success/30 disabled:opacity-50">
              <CheckCircle size={12} className="inline mr-1" />Create
            </button>
            <button onClick={() => setShowForm(false)} className="px-3 py-1.5 bg-bg-input border border-border rounded text-xs text-text-muted hover:text-white">Cancel</button>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {users.length === 0 && !showForm && (
          <div className="text-center py-8 text-text-muted text-sm">
            <Shield size={32} className="mx-auto mb-2 opacity-30" />
            <p>Chưa có user. Khi trống, gateway <span className="text-yellow-400">không yêu cầu đăng nhập</span>.</p>
          </div>
        )}
        {users.map(u => (
          <div key={u.id} className={`glass-card p-3 flex items-center gap-3 ${!u.enabled ? 'opacity-50' : ''}`}>
            <button onClick={() => toggleEnabled(u)}
              className={`w-8 h-4 rounded-full transition-all flex-shrink-0 ${u.enabled ? 'bg-success' : 'bg-bg-input border border-border'}`}>
              <span className={`block w-3 h-3 rounded-full bg-white transition-all mx-0.5 ${u.enabled ? 'translate-x-4' : 'translate-x-0'}`} />
            </button>
            <span className="text-sm font-medium text-white flex-1">{u.username}</span>
            <select value={u.role} onChange={e => changeRole(u.id, e.target.value)}
              className={`text-[11px] font-bold px-2 py-1 rounded border bg-transparent focus:outline-none ${ROLE_COLORS[u.role]}`}>
              <option value="viewer">viewer</option>
              <option value="operator">operator</option>
              <option value="admin">admin</option>
            </select>
            <button onClick={() => deleteUser(u.id)} className="p-1.5 text-text-muted hover:text-error rounded hover:bg-error/10">
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>

      <div className="mt-4 p-3 bg-bg-input rounded-lg border border-border/50">
        <p className="text-[11px] text-text-muted"><span className="text-accent-primary font-bold">Admin</span> — toàn quyền · <span className="text-yellow-400 font-bold">Operator</span> — đọc + ghi BACnet · <span className="text-text-secondary font-bold">Viewer</span> — chỉ đọc</p>
      </div>
    </div>
  );
}

export function SettingsPage() {
  const [bacnet, setBacnet] = useState({});
  const [mqtt, setMqtt] = useState({});
  const [interfaces, setInterfaces] = useState([]);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [tab, setTab] = useState('bacnet');

  const fetchConfig = useCallback(async () => {
    try {
      const [b, m, ifaces] = await Promise.all([
        fetch(`${API}/bacnet/config`).then(r => r.json()),
        fetch(`${API}/mqtt/config`).then(r => r.json()),
        fetch(`${API}/bacnet/interfaces`).then(r => r.json()).catch(() => ({ interfaces: [] })),
      ]);
      setBacnet(b); setMqtt(m);
      setInterfaces(ifaces.interfaces || []);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  const saveBacnet = async () => {
    setSaving(true);
    try {
      await fetch(`${API}/bacnet/config`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(bacnet) });
    } catch (e) { console.error(e); }
    setSaving(false);
  };

  const saveMqtt = async () => {
    setSaving(true);
    try {
      await fetch(`${API}/mqtt/config`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(mqtt) });
    } catch (e) { console.error(e); }
    setSaving(false);
  };

  const testMqtt = async () => {
    setTestResult(null);
    try {
      const res = await fetch(`${API}/mqtt/test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(mqtt) });
      const data = await res.json();
      setTestResult(data.success ? 'ok' : 'fail');
    } catch (e) { setTestResult('fail'); }
    setTimeout(() => setTestResult(null), 5000);
  };

  const exportConfig = async () => {
    try {
      const res = await fetch(`${API}/config/export`);
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url;
      a.download = `gateway_config_${new Date().toISOString().slice(0, 10)}.json`;
      a.click(); URL.revokeObjectURL(url);
    } catch (e) { console.error(e); }
  };

  const importConfig = () => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.json';
    input.onchange = async (e) => {
      const file = e.target.files[0]; if (!file) return;
      if (!confirm('Import will overwrite current configuration. Continue?')) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        await fetch(`${API}/config/import`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        await fetchConfig();
      } catch (err) { console.error(err); }
    };
    input.click();
  };

  const { user: currentUser } = useAuth();
  const isAdmin = !currentUser || currentUser.role === 'admin';

  const tabs = [
    { id: 'bacnet', label: 'BACnet' },
    { id: 'mqtt', label: 'MQTT' },
    { id: 'webhooks', label: '🔔 Webhooks' },
    ...(isAdmin ? [{ id: 'users', label: '👤 Users' }] : []),
    { id: 'system', label: 'System' },
  ];

  return (
    <div className="p-6">
      <h2 className="text-2xl font-bold tracking-tight text-white mb-6">Settings</h2>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-bg-input rounded-xl p-1">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${tab === t.id ? 'bg-gradient-to-r from-accent-primary to-purple-600 text-white shadow-lg' : 'text-text-secondary hover:text-white'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* BACnet Tab */}
      {tab === 'bacnet' && (
        <Section title="BACnet Configuration">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="col-span-2 lg:col-span-1">
              <label className="text-xs text-text-muted block mb-1">Network Interface</label>
              <select value={bacnet.interface || ''} onChange={e => setBacnet(p => ({ ...p, interface: e.target.value }))}
                className="w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none focus:border-border-focus">
                <option value="">Auto-detect</option>
                {interfaces.map(i => <option key={i.name} value={i.name}>{i.name} — {i.ip}</option>)}
              </select>
            </div>
            <Field label="IP Override" value={bacnet.ip} onChange={v => setBacnet(p => ({ ...p, ip: v }))} placeholder="Auto-detect" />
            <Field label="Port" value={bacnet.port} onChange={v => setBacnet(p => ({ ...p, port: Number(v) }))} type="number" placeholder="47808" />
            <Field label="Device ID" value={bacnet.device_id} onChange={v => setBacnet(p => ({ ...p, device_id: Number(v) }))} type="number" placeholder="599" />
            <Field label="BMS Server IP" value={bacnet.bms_server_ip || ''} onChange={v => setBacnet(p => ({ ...p, bms_server_ip: v }))} placeholder="e.g. 192.168.20.10 (passive WHO-IS monitor)" />
          </div>
          <button onClick={saveBacnet} disabled={saving}
            className="flex items-center gap-2 px-4 py-2 mt-4 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-sm font-medium disabled:opacity-50">
            <Save size={14} /> Save BACnet Config
          </button>
        </Section>
      )}

      {/* MQTT Tab */}
      {tab === 'mqtt' && (
        <Section title="MQTT Configuration">
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            <Field label="Broker Host" value={mqtt.host} onChange={v => setMqtt(p => ({ ...p, host: v }))} placeholder="localhost" />
            <Field label="Port" value={mqtt.port} onChange={v => setMqtt(p => ({ ...p, port: Number(v) }))} type="number" placeholder="1883" />
            <Field label="Username" value={mqtt.username} onChange={v => setMqtt(p => ({ ...p, username: v }))} placeholder="Optional" />
            <Field label="Password" value={mqtt.password} onChange={v => setMqtt(p => ({ ...p, password: v }))} type="password" placeholder="Optional" />
            <Field label="Topic Prefix" value={mqtt.topic_prefix} onChange={v => setMqtt(p => ({ ...p, topic_prefix: v }))} placeholder="bacnet/" />
            <Field label="Client ID" value={mqtt.client_id} onChange={v => setMqtt(p => ({ ...p, client_id: v }))} placeholder="bacnet-gateway" />
          </div>
          <div className="flex gap-2 mt-4">
            <button onClick={saveMqtt} disabled={saving}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-sm font-medium disabled:opacity-50">
              <Save size={14} /> Save MQTT Config
            </button>
            <button onClick={testMqtt}
              className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-text-secondary text-sm hover:text-white hover:border-accent-primary transition-all">
              <TestTube size={14} /> Test Connection
            </button>
            {testResult === 'ok' && <span className="flex items-center gap-1 text-success text-sm"><CheckCircle size={14} /> Connected!</span>}
            {testResult === 'fail' && <span className="flex items-center gap-1 text-error text-sm"><XCircle size={14} /> Failed</span>}
          </div>
        </Section>
      )}

      {/* Webhooks Tab */}
      {tab === 'webhooks' && (
        <Section title="Webhook Alerts">
          <WebhooksTab />
        </Section>
      )}

      {/* Users Tab */}
      {tab === 'users' && isAdmin && (
        <Section title="User Management">
          <UsersTab />
        </Section>
      )}

      {/* System Tab */}
      {tab === 'system' && (
        <>
          <Section title="Configuration Backup">
            <p className="text-xs text-text-secondary mb-4">Export or import the entire gateway configuration including all mappings, groups, and schedules.</p>
            <div className="flex gap-2">
              <button onClick={exportConfig} className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-text-secondary text-sm hover:text-white hover:border-accent-primary transition-all">
                <Download size={14} /> Export Config
              </button>
              <button onClick={importConfig} className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-text-secondary text-sm hover:text-white hover:border-accent-primary transition-all">
                <Upload size={14} /> Import Config
              </button>
            </div>
          </Section>
          <Section title="About">
            <div className="text-xs text-text-secondary space-y-1">
              <div><b className="text-white">BACnet-MQTT Gateway</b> v2.0.0</div>
              <div>React Frontend • FastAPI Backend • AG-Grid</div>
              <div>Running on Raspberry Pi / Ubuntu Server</div>
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
