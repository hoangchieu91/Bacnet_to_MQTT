import React, { useState, useEffect, useCallback } from 'react';
import { Save, RefreshCw, TestTube, CheckCircle, XCircle, Download, Upload } from 'lucide-react';

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

  const tabs = [
    { id: 'bacnet', label: 'BACnet' },
    { id: 'mqtt', label: 'MQTT' },
    { id: 'system', label: 'System' },
  ];

  return (
    <div className="p-6 max-w-3xl">
      <h2 className="text-2xl font-bold tracking-tight text-white mb-6">Settings</h2>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-bg-input rounded-xl p-1 w-fit">
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
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
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
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
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
