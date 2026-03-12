import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../App';
import {
  Search, ChevronRight, Edit3, RotateCw, Clock, Radio,
  AlertTriangle, Check, X, Loader, FolderTree, Wrench,
} from 'lucide-react';

const API = '/api';

// ── Object type grouping ─────────────────────────────────────────
const TYPE_GROUPS = {
  'Analog': ['analogInput', 'analogOutput', 'analogValue', 'analog-input', 'analog-output', 'analog-value'],
  'Binary': ['binaryInput', 'binaryOutput', 'binaryValue', 'binary-input', 'binary-output', 'binary-value'],
  'MultiState': ['multiStateInput', 'multiStateOutput', 'multiStateValue', 'multi-state-input', 'multi-state-output', 'multi-state-value'],
  'Device': ['device'],
};

function getTypeGroup(type) {
  for (const [group, types] of Object.entries(TYPE_GROUPS)) {
    if (types.includes(type)) return group;
  }
  return 'Other';
}

const TYPE_ICONS = {
  Analog: '📊', Binary: '🔘', MultiState: '🔢', Device: '🖥️', Other: '📦',
};

// ── Card component ───────────────────────────────────────────────
function Card({ title, icon, children, className = '' }) {
  return (
    <div className={`bg-bg-secondary border border-border rounded-lg ${className}`}>
      {title && (
        <div className="px-4 py-3 border-b border-border flex items-center gap-2">
          {icon && <span className="text-sm">{icon}</span>}
          <span className="text-xs font-bold uppercase tracking-wider text-text-muted">{title}</span>
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}

// ── Tab pill ─────────────────────────────────────────────────────
function TabPills({ tabs, active, onChange }) {
  return (
    <div className="flex gap-1 bg-bg-primary/50 rounded-lg p-1 mb-4">
      {tabs.map(t => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
            active === t.id
              ? 'bg-accent-primary/20 text-accent-primary'
              : 'text-text-muted hover:text-white hover:bg-white/5'
          }`}>
          {t.icon && <span className="mr-1.5">{t.icon}</span>}
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
export function BacnetToolsPage() {
  const { apiFetch } = useAuth();
  const f = apiFetch || fetch;

  const [tab, setTab] = useState('browse');
  const [devices, setDevices] = useState([]);
  const [selectedDevice, setSelectedDevice] = useState(null);
  const [objects, setObjects] = useState([]);
  const [loadingObjects, setLoadingObjects] = useState(false);
  const [selectedObject, setSelectedObject] = useState(null);
  const [properties, setProperties] = useState({});
  const [loadingProps, setLoadingProps] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState({});

  // Write state
  const [writeTarget, setWriteTarget] = useState({ property: '', value: '', priority: '' });
  const [writeResult, setWriteResult] = useState(null);
  const [writing, setWriting] = useState(false);

  // Management state
  const [mgmtResult, setMgmtResult] = useState(null);
  const [mgmtLoading, setMgmtLoading] = useState(false);

  // Who-Is state
  const [whoisRange, setWhoisRange] = useState({ low: 0, high: 4194303 });
  const [whoisResults, setWhoisResults] = useState([]);
  const [whoisLoading, setWhoisLoading] = useState(false);

  // Load discovered devices
  useEffect(() => {
    f(`${API}/bacnet/devices`).then(r => r.json()).then(d => {
      setDevices(d.devices || d || []);
    }).catch(() => {});
  }, []);

  // Load objects when device selected
  const loadObjects = useCallback(async (devId) => {
    setSelectedDevice(devId);
    setSelectedObject(null);
    setProperties({});
    setLoadingObjects(true);
    try {
      const r = await f(`${API}/tools/devices/${devId}/objects`);
      const d = await r.json();
      setObjects(d.objects || []);
    } catch { setObjects([]); }
    setLoadingObjects(false);
  }, [f]);

  // Load properties when object selected
  const loadProperties = useCallback(async (obj) => {
    setSelectedObject(obj);
    setLoadingProps(true);
    try {
      const r = await f(`${API}/tools/devices/${selectedDevice}/objects/${obj.type}/${obj.instance}/properties`);
      const d = await r.json();
      setProperties(d.properties || {});
    } catch { setProperties({}); }
    setLoadingProps(false);
  }, [f, selectedDevice]);

  // Group objects by type
  const grouped = {};
  objects.forEach(obj => {
    const g = getTypeGroup(obj.type);
    if (!grouped[g]) grouped[g] = [];
    grouped[g].push(obj);
  });

  // Write property
  const doWrite = async () => {
    setWriting(true);
    setWriteResult(null);
    try {
      const r = await f(`${API}/tools/write-property`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device_id: selectedDevice,
          object_type: selectedObject?.type,
          object_instance: selectedObject?.instance,
          property: writeTarget.property,
          value: writeTarget.value,
          priority: writeTarget.priority ? parseInt(writeTarget.priority) : undefined,
        }),
      });
      const d = await r.json();
      setWriteResult(d);
      if (d.success && selectedObject) loadProperties(selectedObject);
    } catch (e) { setWriteResult({ success: false, message: e.message }); }
    setWriting(false);
  };

  // Management actions
  const doMgmt = async (action, extra = {}) => {
    setMgmtLoading(true);
    setMgmtResult(null);
    try {
      const r = await f(`${API}/tools/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: selectedDevice, ...extra }),
      });
      const d = await r.json();
      setMgmtResult(d);
    } catch (e) { setMgmtResult({ success: false, message: e.message }); }
    setMgmtLoading(false);
  };

  // Who-Is
  const doWhois = async () => {
    setWhoisLoading(true);
    try {
      const r = await f(`${API}/tools/whois`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(whoisRange),
      });
      const d = await r.json();
      setWhoisResults(d.devices || []);
    } catch { setWhoisResults([]); }
    setWhoisLoading(false);
  };

  const TABS = [
    { id: 'browse', label: 'Device Browser', icon: '🔍' },
    { id: 'write',  label: 'Property Editor', icon: '✏️' },
    { id: 'manage', label: 'Device Mgmt', icon: '🔧' },
    { id: 'scan',   label: 'Who-Is Scanner', icon: '📡' },
  ];

  return (
    <div className="p-4 md:p-6 max-w-[1400px] mx-auto space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center">
          <Wrench size={20} className="text-white" />
        </div>
        <div>
          <h1 className="text-lg font-bold text-white">BACnet Tools</h1>
          <p className="text-xs text-text-muted">Device browser, property editor, commissioning tools</p>
        </div>
      </div>

      {/* Device Selector */}
      <Card>
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-xs font-medium text-text-muted">Device:</label>
          <select value={selectedDevice || ''} onChange={e => loadObjects(parseInt(e.target.value))}
            className="bg-bg-primary border border-border rounded-lg px-3 py-1.5 text-sm text-white min-w-[240px]">
            <option value="">— Select device —</option>
            {devices.map(d => (
              <option key={d.device_id} value={d.device_id}>
                [{d.device_id}] {d.name || d.address}
              </option>
            ))}
          </select>
          {selectedDevice && (
            <span className="text-[10px] text-text-muted bg-bg-primary px-2 py-1 rounded font-mono">
              {devices.find(d => d.device_id === selectedDevice)?.address}
            </span>
          )}
          {loadingObjects && <Loader size={14} className="animate-spin text-accent-primary" />}
          <span className="text-[10px] text-text-muted ml-auto">
            {devices.length > 0
              ? <><span className="text-success font-bold">{devices.length}</span> devices discovered</>
              : <span className="text-amber-400">⚠ No devices — check Settings or run Who-Is Scan</span>
            }
          </span>
        </div>
      </Card>

      <TabPills tabs={TABS} active={tab} onChange={setTab} />

      {/* ═══ TAB: BROWSE ═══ */}
      {tab === 'browse' && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          {/* Object tree */}
          <div className="lg:col-span-4">
            <Card title="Object List" icon="📋">
              {objects.length === 0 ? (
                <div className="space-y-3 py-2">
                  {!selectedDevice ? (
                    <div className="text-center">
                      <div className="text-2xl mb-2">🔍</div>
                      <p className="text-xs text-text-muted">Select a device above to browse its objects</p>
                      <div className="mt-3 space-y-1.5">
                        <div className="flex items-center gap-2 text-[11px] text-text-muted">
                          <span className="w-5 h-5 rounded-full bg-accent-primary/20 text-accent-primary flex items-center justify-center text-[10px] font-bold">1</span>
                          Choose a device from the dropdown
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-text-muted">
                          <span className="w-5 h-5 rounded-full bg-accent-primary/20 text-accent-primary flex items-center justify-center text-[10px] font-bold">2</span>
                          Expand object groups (Analog, Binary...)
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-text-muted">
                          <span className="w-5 h-5 rounded-full bg-accent-primary/20 text-accent-primary flex items-center justify-center text-[10px] font-bold">3</span>
                          Click an object to view its properties
                        </div>
                      </div>
                      {devices.length === 0 && (
                        <button onClick={() => setTab('scan')}
                          className="mt-3 px-3 py-1.5 bg-accent-primary/10 text-accent-primary border border-accent-primary/30 rounded-lg text-[11px] font-medium hover:bg-accent-primary/20 transition-all">
                          📡 Run Who-Is Scan to find devices
                        </button>
                      )}
                    </div>
                  ) : (
                    <div className="text-center">
                      <div className="text-2xl mb-2">📭</div>
                      <p className="text-xs text-text-muted">No objects found on device {selectedDevice}</p>
                      <p className="text-[10px] text-text-muted mt-1">The device may not support ReadObjectList or may be unreachable.</p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="space-y-1 max-h-[500px] overflow-y-auto">
                  {Object.entries(grouped).map(([group, objs]) => (
                    <div key={group}>
                      <button
                        onClick={() => setExpandedGroups(prev => ({ ...prev, [group]: !prev[group] }))}
                        className="w-full flex items-center gap-2 px-2 py-1.5 text-xs font-bold text-text-muted hover:text-white rounded transition-all"
                      >
                        <span>{TYPE_ICONS[group]}</span>
                        <span>{group}</span>
                        <span className="ml-auto text-[10px] bg-bg-primary px-1.5 rounded">{objs.length}</span>
                        <ChevronRight size={12} className={`transition-transform ${expandedGroups[group] ? 'rotate-90' : ''}`} />
                      </button>
                      {expandedGroups[group] && objs.map(obj => (
                        <button key={`${obj.type}-${obj.instance}`}
                          onClick={() => loadProperties(obj)}
                          className={`w-full text-left px-6 py-1 text-[11px] rounded transition-all ${
                            selectedObject?.type === obj.type && selectedObject?.instance === obj.instance
                              ? 'bg-accent-primary/15 text-accent-primary'
                              : 'text-text-secondary hover:text-white hover:bg-white/5'
                          }`}>
                          <span className="font-mono">{obj.type}:{obj.instance}</span>
                          {obj.name && <span className="ml-2 text-text-muted">— {obj.name}</span>}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>

          {/* Properties panel */}
          <div className="lg:col-span-8">
            <Card title={selectedObject ? `${selectedObject.type}:${selectedObject.instance}` : 'Properties'} icon="📋">
              {loadingProps ? (
                <div className="flex items-center gap-2 text-xs text-text-muted">
                  <Loader size={14} className="animate-spin" /> Reading properties...
                </div>
              ) : Object.keys(properties).length === 0 ? (
                <div className="text-center py-8">
                  <div className="text-3xl mb-2">📋</div>
                  <p className="text-xs text-text-muted">Click an object in the left panel to view all its properties</p>
                  <p className="text-[10px] text-text-muted mt-1">Properties include: objectName, presentValue, units, statusFlags, description...</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border">
                        <th className="text-left py-2 px-2 font-bold text-text-muted uppercase tracking-wider text-[10px]">Property</th>
                        <th className="text-left py-2 px-2 font-bold text-text-muted uppercase tracking-wider text-[10px]">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(properties).map(([key, val]) => (
                        <tr key={key} className="border-b border-border/50 hover:bg-white/[0.02]">
                          <td className="py-1.5 px-2 font-mono text-accent-primary">{key}</td>
                          <td className="py-1.5 px-2 text-white">
                            {Array.isArray(val)
                              ? <span className="text-text-muted">[{val.join(', ')}]</span>
                              : String(val)
                            }
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </div>
      )}

      {/* ═══ TAB: WRITE ═══ */}
      {tab === 'write' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card title="Write Property" icon="✏️">
            {!selectedObject ? (
              <div className="space-y-3 py-2">
                <div className="text-center">
                  <div className="text-2xl mb-2">✏️</div>
                  <p className="text-xs text-text-muted">No object selected</p>
                  <div className="mt-3 space-y-1.5">
                    <div className="flex items-center gap-2 text-[11px] text-text-muted">
                      <span className="w-5 h-5 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center text-[10px] font-bold">1</span>
                      Go to <button onClick={() => setTab('browse')} className="text-accent-primary hover:underline font-medium">Device Browser</button>
                    </div>
                    <div className="flex items-center gap-2 text-[11px] text-text-muted">
                      <span className="w-5 h-5 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center text-[10px] font-bold">2</span>
                      Select a device and click an object
                    </div>
                    <div className="flex items-center gap-2 text-[11px] text-text-muted">
                      <span className="w-5 h-5 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center text-[10px] font-bold">3</span>
                      Come back here to write properties
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="text-xs text-text-muted">
                  Target: <span className="text-accent-primary font-mono">{selectedObject.type}:{selectedObject.instance}</span>
                  {' '}on device <span className="text-white font-bold">{selectedDevice}</span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[10px] text-text-muted font-bold uppercase">Property</label>
                    <input value={writeTarget.property} onChange={e => setWriteTarget(p => ({ ...p, property: e.target.value }))}
                      placeholder="objectName, presentValue, description..."
                      className="w-full bg-bg-primary border border-border rounded px-3 py-1.5 text-xs text-white mt-1" />
                  </div>
                  <div>
                    <label className="text-[10px] text-text-muted font-bold uppercase">Value</label>
                    <input value={writeTarget.value} onChange={e => setWriteTarget(p => ({ ...p, value: e.target.value }))}
                      placeholder="New value"
                      className="w-full bg-bg-primary border border-border rounded px-3 py-1.5 text-xs text-white mt-1" />
                  </div>
                </div>
                <div className="w-32">
                  <label className="text-[10px] text-text-muted font-bold uppercase">Priority (1-16)</label>
                  <input type="number" min="1" max="16" value={writeTarget.priority}
                    onChange={e => setWriteTarget(p => ({ ...p, priority: e.target.value }))}
                    placeholder="16"
                    className="w-full bg-bg-primary border border-border rounded px-3 py-1.5 text-xs text-white mt-1" />
                </div>
                <button onClick={doWrite} disabled={writing || !writeTarget.property}
                  className="px-4 py-2 bg-accent-primary hover:bg-accent-primary/80 text-white rounded-lg text-xs font-bold disabled:opacity-40 flex items-center gap-2">
                  {writing ? <Loader size={12} className="animate-spin" /> : <Edit3 size={12} />}
                  Write Property
                </button>
                {writeResult && (
                  <div className={`p-3 rounded-lg text-xs flex items-center gap-2 ${
                    writeResult.success ? 'bg-success/10 text-success border border-success/30' : 'bg-error/10 text-error border border-error/30'
                  }`}>
                    {writeResult.success ? <Check size={14} /> : <X size={14} />}
                    {writeResult.success ? 'Write successful' : writeResult.message}
                  </div>
                )}
              </div>
            )}
          </Card>
          <Card title="Quick Actions" icon="⚡">
            {!selectedObject ? (
              <div className="text-center py-4">
                <div className="text-2xl mb-2">⚡</div>
                <p className="text-xs text-text-muted">Quick actions will appear after selecting an object</p>
                <p className="text-[10px] text-text-muted mt-1">Rename, edit description, write value, set COV...</p>
              </div>
            ) : (
              <div className="space-y-2">
                <button onClick={() => { setWriteTarget({ property: 'objectName', value: '', priority: '' }); }}
                  className="w-full text-left px-3 py-2 rounded-lg bg-bg-primary hover:bg-white/5 text-xs transition-all flex items-center gap-2">
                  <Edit3 size={12} className="text-amber-400" /> ✏️ Rename Object (objectName)
                </button>
                <button onClick={() => { setWriteTarget({ property: 'description', value: '', priority: '' }); }}
                  className="w-full text-left px-3 py-2 rounded-lg bg-bg-primary hover:bg-white/5 text-xs transition-all flex items-center gap-2">
                  <Edit3 size={12} className="text-blue-400" /> 📝 Edit Description
                </button>
                <button onClick={() => { setWriteTarget({ property: 'presentValue', value: '', priority: '8' }); }}
                  className="w-full text-left px-3 py-2 rounded-lg bg-bg-primary hover:bg-white/5 text-xs transition-all flex items-center gap-2">
                  <Edit3 size={12} className="text-green-400" /> 📊 Write Present Value
                </button>
                <button onClick={() => { setWriteTarget({ property: 'covIncrement', value: '', priority: '' }); }}
                  className="w-full text-left px-3 py-2 rounded-lg bg-bg-primary hover:bg-white/5 text-xs transition-all flex items-center gap-2">
                  <Edit3 size={12} className="text-purple-400" /> 📈 Set COV Increment
                </button>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* ═══ TAB: MANAGE ═══ */}
      {tab === 'manage' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <Card title="Reinitialize Device" icon="🔄">
            {!selectedDevice ? <div className="text-center py-3"><div className="text-xl mb-1">🔄</div><p className="text-xs text-text-muted">Select a device above to reinitialize it</p><p className="text-[10px] text-text-muted mt-1">Warmstart = soft restart, Coldstart = factory reset</p></div> : (
              <div className="space-y-3">
                <p className="text-[11px] text-text-muted">Send ReinitializeDevice to device {selectedDevice}</p>
                <div className="flex gap-2">
                  <button onClick={() => doMgmt('reinitialize', { state: 'warmstart' })}
                    disabled={mgmtLoading}
                    className="px-3 py-2 bg-amber-600/20 hover:bg-amber-600/40 text-amber-400 border border-amber-600/30 rounded-lg text-xs font-bold flex items-center gap-1.5">
                    <RotateCw size={12} /> Warmstart
                  </button>
                  <button onClick={() => doMgmt('reinitialize', { state: 'coldstart' })}
                    disabled={mgmtLoading}
                    className="px-3 py-2 bg-red-600/20 hover:bg-red-600/40 text-red-400 border border-red-600/30 rounded-lg text-xs font-bold flex items-center gap-1.5">
                    <AlertTriangle size={12} /> Coldstart
                  </button>
                </div>
              </div>
            )}
          </Card>

          <Card title="Communication Control" icon="📡">
            {!selectedDevice ? <div className="text-center py-3"><div className="text-xl mb-1">📡</div><p className="text-xs text-text-muted">Select a device to control communication</p><p className="text-[10px] text-text-muted mt-1">Enable or disable BACnet communication on the device</p></div> : (
              <div className="space-y-3">
                <p className="text-[11px] text-text-muted">Enable/disable device communication</p>
                <div className="flex gap-2">
                  <button onClick={() => doMgmt('comm-control', { state: 'enable' })}
                    disabled={mgmtLoading}
                    className="px-3 py-2 bg-green-600/20 hover:bg-green-600/40 text-green-400 border border-green-600/30 rounded-lg text-xs font-bold flex items-center gap-1.5">
                    <Check size={12} /> Enable
                  </button>
                  <button onClick={() => doMgmt('comm-control', { state: 'disable' })}
                    disabled={mgmtLoading}
                    className="px-3 py-2 bg-red-600/20 hover:bg-red-600/40 text-red-400 border border-red-600/30 rounded-lg text-xs font-bold flex items-center gap-1.5">
                    <X size={12} /> Disable
                  </button>
                </div>
              </div>
            )}
          </Card>

          <Card title="Time Synchronization" icon="🕐">
            <div className="space-y-3">
              <p className="text-[11px] text-text-muted">Sync device clock with Pi's system time</p>
              <div className="flex gap-2">
                <button onClick={() => doMgmt('time-sync', {})}
                  disabled={mgmtLoading}
                  className="px-3 py-2 bg-cyan-600/20 hover:bg-cyan-600/40 text-cyan-400 border border-cyan-600/30 rounded-lg text-xs font-bold flex items-center gap-1.5">
                  <Clock size={12} /> Sync All (Broadcast)
                </button>
                {selectedDevice && (
                  <button onClick={() => doMgmt('time-sync', { device_id: selectedDevice })}
                    disabled={mgmtLoading}
                    className="px-3 py-2 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 border border-blue-600/30 rounded-lg text-xs font-bold flex items-center gap-1.5">
                    <Clock size={12} /> Sync #{selectedDevice}
                  </button>
                )}
              </div>
            </div>
          </Card>

          {mgmtResult && (
            <div className="lg:col-span-3">
              <div className={`p-3 rounded-lg text-xs flex items-center gap-2 ${
                mgmtResult.success ? 'bg-success/10 text-success border border-success/30' : 'bg-error/10 text-error border border-error/30'
              }`}>
                {mgmtResult.success ? <Check size={14} /> : <X size={14} />}
                {mgmtResult.message || (mgmtResult.success ? 'Operation completed' : 'Operation failed')}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══ TAB: SCAN ═══ */}
      {tab === 'scan' && (
        <div className="space-y-4">
          <Card title="Who-Is Scanner" icon="📡">
            <div className="flex items-end gap-3 flex-wrap">
              <div>
                <label className="text-[10px] text-text-muted font-bold uppercase">Low Device ID</label>
                <input type="number" value={whoisRange.low}
                  onChange={e => setWhoisRange(p => ({ ...p, low: parseInt(e.target.value) || 0 }))}
                  className="w-32 bg-bg-primary border border-border rounded px-3 py-1.5 text-xs text-white mt-1 block" />
              </div>
              <div>
                <label className="text-[10px] text-text-muted font-bold uppercase">High Device ID</label>
                <input type="number" value={whoisRange.high}
                  onChange={e => setWhoisRange(p => ({ ...p, high: parseInt(e.target.value) || 4194303 }))}
                  className="w-32 bg-bg-primary border border-border rounded px-3 py-1.5 text-xs text-white mt-1 block" />
              </div>
              <button onClick={doWhois} disabled={whoisLoading}
                className="px-4 py-2 bg-accent-primary hover:bg-accent-primary/80 text-white rounded-lg text-xs font-bold disabled:opacity-40 flex items-center gap-2">
                {whoisLoading ? <Loader size={12} className="animate-spin" /> : <Search size={12} />}
                Scan
              </button>
              <span className="text-[10px] text-text-muted">{whoisResults.length} devices found</span>
            </div>
          </Card>

          {whoisResults.length > 0 && (
            <Card title="Scan Results" icon="📋">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left py-2 px-2 text-[10px] font-bold text-text-muted uppercase">Device ID</th>
                    <th className="text-left py-2 px-2 text-[10px] font-bold text-text-muted uppercase">Address</th>
                    <th className="text-left py-2 px-2 text-[10px] font-bold text-text-muted uppercase">Name</th>
                    <th className="text-left py-2 px-2 text-[10px] font-bold text-text-muted uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {whoisResults.map(d => (
                    <tr key={d.device_id} className="border-b border-border/50 hover:bg-white/[0.02]">
                      <td className="py-1.5 px-2 font-mono text-accent-primary font-bold">{d.device_id}</td>
                      <td className="py-1.5 px-2 font-mono text-text-secondary">{d.address}</td>
                      <td className="py-1.5 px-2 text-white">{d.name}</td>
                      <td className="py-1.5 px-2">
                        <button onClick={() => { loadObjects(d.device_id); setTab('browse'); }}
                          className="text-accent-primary hover:underline text-[11px]">
                          Browse →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
