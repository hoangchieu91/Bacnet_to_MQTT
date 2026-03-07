import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Search, WifiOff, Plus, RefreshCw, Loader2, ChevronDown, ChevronRight, Settings2, List, LayoutGrid, Filter, Database, Wifi, CheckSquare, Square } from 'lucide-react';

const API = '/api';

export function DevicesPage() {
  const [devices, setDevices] = useState([]);
  const [configured, setConfigured] = useState([]);
  const [liveCount, setLiveCount] = useState(0);
  const [scanning, setScanning] = useState(false);
  const [expandedDevice, setExpandedDevice] = useState(null);
  const [objects, setObjects] = useState({});
  const [loadingObjs, setLoadingObjs] = useState({});
  const [selectedObjs, setSelectedObjs] = useState({});
  const [adding, setAdding] = useState(null);
  const [addResult, setAddResult] = useState({});
  const [showScanOpts, setShowScanOpts] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState('id');
  const [sortDir, setSortDir] = useState('asc');
  const [viewMode, setViewMode] = useState('table');
  const [networkFilter, setNetworkFilter] = useState('all');
  const [scanMode, setScanMode] = useState('full');
  const [scanTimeout, setScanTimeout] = useState(10);
  const [lowId, setLowId] = useState('');
  const [highId, setHighId] = useState('');
  const [specificId, setSpecificId] = useState('');

  // Load from registry (persistent cache) — shows known devices even when gateway stopped
  const fetchDevices = useCallback(async () => {
    try {
      const [disc, conf] = await Promise.all([
        fetch(`${API}/bacnet/devices`).then(r => r.json()),
        fetch(`${API}/bacnet/configured-devices`).then(r => r.json()),
      ]);
      setDevices(disc.devices || []);
      setLiveCount(disc.live_count || 0);
      setConfigured(conf.devices || []);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => {
    fetchDevices();
    const iv = setInterval(fetchDevices, 10000);
    return () => clearInterval(iv);
  }, [fetchDevices]);

  const handleScan = async () => {
    setScanning(true);
    try {
      const body = { scan_mode: scanMode, timeout: scanTimeout };
      if (scanMode === 'range' && lowId && highId) { body.low_id = Number(lowId); body.high_id = Number(highId); }
      if (scanMode === 'specific' && specificId) body.device_id = Number(specificId);
      await fetch(`${API}/bacnet/discover`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      await fetchDevices();
    } catch (e) { console.error(e); }
    setScanning(false);
  };

  const loadObjects = async (deviceId, forceRefresh = false) => {
    const id = String(deviceId);
    if (expandedDevice === id && !forceRefresh) { setExpandedDevice(null); return; }
    setExpandedDevice(id);
    if (objects[id] && !forceRefresh) return;
    setLoadingObjs(prev => ({ ...prev, [id]: true }));
    try {
      const url = forceRefresh
        ? `${API}/bacnet/devices/${deviceId}/objects?refresh=true`
        : `${API}/bacnet/devices/${deviceId}/objects`;
      const res = await fetch(url);
      const data = await res.json();
      const objs = data.error ? data.error : (data.objects || []);
      setObjects(prev => ({ ...prev, [id]: objs }));
      if (Array.isArray(objs)) {
        setSelectedObjs(prev => ({ ...prev, [id]: new Set(objs.map((_, i) => i)) }));
      }
    } catch (e) {
      setObjects(prev => ({ ...prev, [id]: 'error fetching objects' }));
    }
    setLoadingObjs(prev => ({ ...prev, [id]: false }));
  };

  const toggleObjSelect = (deviceId, idx) => {
    const id = String(deviceId);
    setSelectedObjs(prev => {
      const s = new Set(prev[id] || []);
      if (s.has(idx)) s.delete(idx); else s.add(idx);
      return { ...prev, [id]: s };
    });
  };

  const selectAllObjs = (deviceId) => {
    const id = String(deviceId);
    setSelectedObjs(prev => ({ ...prev, [id]: new Set((objects[id] || []).map((_, i) => i)) }));
  };
  const selectNoneObjs = (deviceId) => {
    const id = String(deviceId);
    setSelectedObjs(prev => ({ ...prev, [id]: new Set() }));
  };

  const addSelectedPoints = async (deviceId) => {
    const id = String(deviceId);
    const objs = objects[id] || [];
    const sel = selectedObjs[id] || new Set();
    const toAdd = objs.filter((_, i) => sel.has(i));
    if (!toAdd.length) return;
    setAdding(id);
    setAddResult(prev => ({ ...prev, [id]: null }));
    try {
      const res = await fetch(`${API}/mappings/bulk`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mappings: toAdd.map(obj => ({
            device_id: Number(deviceId),
            object_type: obj.object_type,
            object_instance: obj.object_instance,
            label: obj.object_name || '',
          }))
        })
      });
      const data = await res.json();
      setAddResult(prev => ({ ...prev, [id]: { created: data.created, error: data.error } }));
    } catch (e) {
      setAddResult(prev => ({ ...prev, [id]: { error: e.message } }));
    }
    setAdding(null);
  };

  const configuredIds = new Set((configured || []).map(d => String(d.device_id)));
  const getNetwork = (addr) => {
    if (!addr) return 'Unknown';
    if (addr.includes('.')) return 'IP';
    const parts = addr.split(':');
    if (parts.length === 2 && /^\d+$/.test(parts[0])) return parts[0];
    return 'Other';
  };

  const networks = useMemo(() => {
    const nets = new Set();
    devices.forEach(d => nets.add(d.network_id || getNetwork(d.address)));
    return [...nets].sort((a, b) => a === 'IP' ? -1 : b === 'IP' ? 1 : Number(a) - Number(b));
  }, [devices]);

  const networkCounts = useMemo(() => {
    const counts = {};
    devices.forEach(d => { const n = d.network_id || getNetwork(d.address); counts[n] = (counts[n] || 0) + 1; });
    return counts;
  }, [devices]);

  const filtered = useMemo(() => {
    let list = devices;
    if (networkFilter !== 'all') list = list.filter(d => (d.network_id || getNetwork(d.address)) === networkFilter);
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      list = list.filter(d => String(d.device_id||d.id).includes(q)
        || (d.device_name||'').toLowerCase().includes(q)
        || (d.address||'').toLowerCase().includes(q)
        || (d.vendor_name||'').toLowerCase().includes(q));
    }
    list = [...list].sort((a, b) => {
      let va, vb;
      if (sortBy === 'id') { va = a.device_id||0; vb = b.device_id||0; }
      else if (sortBy === 'name') { va = (a.device_name||'').toLowerCase(); vb = (b.device_name||'').toLowerCase(); }
      else if (sortBy === 'ip') { va = a.address||''; vb = b.address||''; }
      else { va = a.network_id||getNetwork(a.address); vb = b.network_id||getNetwork(b.address); }
      if (typeof va === 'number') return sortDir === 'asc' ? va - vb : vb - va;
      return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    });
    return list;
  }, [devices, searchQuery, sortBy, sortDir, networkFilter]);

  const toggleSort = (col) => { if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc'); else { setSortBy(col); setSortDir('asc'); } };
  const SortIcon = ({ col }) => <span className={`ml-1 text-[10px] ${sortBy === col ? 'text-accent-primary' : 'text-text-muted'}`}>{sortBy === col ? (sortDir === 'asc' ? '▲' : '▼') : '↕'}</span>;

  const INPUT_CLS = 'w-full px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white focus:outline-none';
  const SEL_CLS = `${INPUT_CLS} appearance-none`;

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">Devices</h2>
          <p className="text-xs text-text-muted mt-1">
            <span className="text-info font-medium">{devices.length} known</span>
            {' · '}
            <span className="text-success">{liveCount} live</span>
            {' · '}
            <span className="text-text-muted">{devices.length - liveCount} from registry</span>
            {searchQuery && ` · ${filtered.length} matched`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowScanOpts(!showScanOpts)}
            className={`p-2 rounded-lg border transition-all ${showScanOpts ? 'border-accent-primary text-accent-primary bg-accent-primary/10' : 'border-border text-text-secondary hover:text-white hover:border-accent-primary'}`}>
            <Settings2 size={16} />
          </button>
          <button onClick={handleScan} disabled={scanning}
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-sm font-medium disabled:opacity-50">
            {scanning ? <Loader2 size={16} className="animate-spin" /> : <Wifi size={16} />}
            {scanning ? 'Scanning...' : 'Scan BACnet'}
          </button>
          <button onClick={fetchDevices} title="Reload registry" className="p-2 rounded-lg border border-border text-text-secondary hover:text-white transition-all">
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {/* Info banner: showing from registry */}
      {devices.length > 0 && liveCount === 0 && (
        <div className="flex items-center gap-2 mb-4 px-3 py-2 bg-info/5 border border-info/20 rounded-lg text-xs text-info">
          <Database size={12} />
          <span>Hiển thị thiết bị từ registry. Gateway chưa chạy — Click <b>Scan BACnet</b> để cập nhật trạng thái live nếu cần, hoặc click device để xem/thêm points từ cache.</span>
        </div>
      )}

      {/* Scan Options Panel */}
      {showScanOpts && (
        <div className="glass-card p-4 mb-4">
          <div className="text-xs font-bold uppercase tracking-widest text-text-muted mb-3">Scan Configuration</div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <label className="text-[10px] text-text-muted block mb-1">Scan Mode</label>
              <select value={scanMode} onChange={e => setScanMode(e.target.value)} className={SEL_CLS}>
                <option value="full">🌐 Full Broadcast</option>
                <option value="range">📏 Range (ID→ID)</option>
                <option value="specific">🎯 Specific Device ID</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-text-muted block mb-1">Timeout (s)</label>
              <input type="number" min={3} max={60} value={scanTimeout} onChange={e => setScanTimeout(Number(e.target.value))} className={INPUT_CLS} />
            </div>
            {scanMode === 'range' && (<>
              <div><label className="text-[10px] text-text-muted block mb-1">Low Device ID</label><input type="number" placeholder="10000" value={lowId} onChange={e => setLowId(e.target.value)} className={INPUT_CLS} /></div>
              <div><label className="text-[10px] text-text-muted block mb-1">High Device ID</label><input type="number" placeholder="11000" value={highId} onChange={e => setHighId(e.target.value)} className={INPUT_CLS} /></div>
            </>)}
            {scanMode === 'specific' && (
              <div><label className="text-[10px] text-text-muted block mb-1">Device ID</label><input type="number" placeholder="10121" value={specificId} onChange={e => setSpecificId(e.target.value)} className={INPUT_CLS} /></div>
            )}
          </div>
        </div>
      )}

      {/* Network Filter + Search */}
      {devices.length > 0 && (
        <div className="space-y-3 mb-4">
          {networks.length > 1 && (
            <div className="flex items-center gap-2 flex-wrap">
              <Filter size={13} className="text-text-muted" />
              <button onClick={() => setNetworkFilter('all')} className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-all ${networkFilter === 'all' ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/40' : 'bg-bg-input text-text-muted border border-border hover:text-white'}`}>All ({devices.length})</button>
              {networks.map(net => (
                <button key={net} onClick={() => setNetworkFilter(net === networkFilter ? 'all' : net)}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-all ${networkFilter === net ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/40' : 'bg-bg-input text-text-muted border border-border hover:text-white'}`}>
                  {net === 'IP' ? '🌐 IP' : `📡 Net ${net}`} ({networkCounts[net] || 0})
                </button>
              ))}
            </div>
          )}
          <div className="flex items-center gap-3">
            <div className="relative flex-1">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
              <input type="text" placeholder="Search by ID, name, address, vendor..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus" />
            </div>
            <div className="flex border border-border rounded-lg overflow-hidden">
              <button onClick={() => setViewMode('table')} className={`p-2 ${viewMode === 'table' ? 'bg-accent-primary/20 text-accent-primary' : 'text-text-muted hover:text-white'}`}><List size={16} /></button>
              <button onClick={() => setViewMode('grid')} className={`p-2 ${viewMode === 'grid' ? 'bg-accent-primary/20 text-accent-primary' : 'text-text-muted hover:text-white'}`}><LayoutGrid size={16} /></button>
            </div>
          </div>
        </div>
      )}

      {/* Table View */}
      {viewMode === 'table' && filtered.length > 0 && (
        <div className="glass-card overflow-hidden">
          <div className="overflow-x-auto max-h-[calc(100vh-320px)] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 z-10 bg-bg-secondary">
                <tr className="border-b border-border">
                  <th className="px-3 py-2.5 text-left font-bold text-text-muted cursor-pointer hover:text-white select-none" onClick={() => toggleSort('id')}>ID <SortIcon col="id" /></th>
                  <th className="px-3 py-2.5 text-left font-bold text-text-muted cursor-pointer hover:text-white select-none" onClick={() => toggleSort('name')}>Name <SortIcon col="name" /></th>
                  <th className="px-3 py-2.5 text-left font-bold text-text-muted cursor-pointer hover:text-white select-none" onClick={() => toggleSort('network')}>Network <SortIcon col="network" /></th>
                  <th className="px-3 py-2.5 text-left font-bold text-text-muted cursor-pointer hover:text-white select-none" onClick={() => toggleSort('ip')}>Address <SortIcon col="ip" /></th>
                  <th className="px-3 py-2.5 text-left font-bold text-text-muted">Status</th>
                  <th className="px-3 py-2.5 text-right font-bold text-text-muted">▸</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(dev => {
                  const id = String(dev.device_id || dev.id);
                  const isConfigured = configuredIds.has(id);
                  const isExpanded = expandedDevice === id;
                  const isLive = dev.live === true;
                  const devObjects = objects[id];
                  const isLoading = loadingObjs[id];
                  const name = dev.device_name || `Device ${id}`;
                  const net = dev.network_id || getNetwork(dev.address);
                  const sel = selectedObjs[id] || new Set();
                  const result = addResult[id];

                  return (
                    <React.Fragment key={id}>
                      <tr className={`border-b border-border/30 hover:bg-white/[0.02] cursor-pointer transition-colors ${isExpanded ? 'bg-accent-primary/5' : ''}`}
                        onClick={() => loadObjects(id)}>
                        <td className="px-3 py-2.5 font-mono font-bold text-white">{id}</td>
                        <td className="px-3 py-2.5 text-text-primary">{name}</td>
                        <td className="px-3 py-2.5">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${net === 'IP' ? 'bg-info/15 text-info' : 'bg-purple-500/15 text-purple-400'} font-bold`}>
                            {net === 'IP' ? '🌐 IP' : `📡 ${net}`}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-text-secondary font-mono text-[11px]">{dev.address || '—'}</td>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {isLive
                              ? <span className="text-[10px] px-2 py-0.5 rounded-full bg-success/15 text-success font-bold flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse inline-block" />Live</span>
                              : <span className="text-[10px] px-2 py-0.5 rounded-full bg-bg-input text-text-muted font-bold flex items-center gap-1"><Database size={9} /> Registry</span>}
                            {isConfigured && <span className="text-[10px] px-2 py-0.5 rounded-full bg-accent-primary/15 text-accent-primary font-bold">Mapped</span>}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 text-right text-text-muted">{isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                      </tr>

                      {/* Expanded: object list with per-point checkboxes */}
                      {isExpanded && (
                        <tr>
                          <td colSpan={6} className="bg-bg-primary/60 px-4 py-3">
                            {isLoading ? (
                              <div className="text-xs text-text-muted py-3 text-center"><Loader2 size={14} className="animate-spin inline mr-2" />Loading objects…</div>
                            ) : typeof devObjects === 'string' ? (
                              <div className="text-xs text-error py-2">{devObjects}</div>
                            ) : !devObjects ? (
                              <div className="text-xs text-text-muted py-2">Loading…</div>
                            ) : devObjects.length === 0 ? (
                              <div className="text-xs text-text-muted py-2 text-center">No objects found</div>
                            ) : (
                              <div>
                                {/* Toolbar */}
                                <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                                  <div className="flex items-center gap-3">
                                    <span className="text-[10px] text-text-muted font-medium">{devObjects.length} objects</span>
                                    <button onClick={e => { e.stopPropagation(); selectAllObjs(id); }} className="text-[10px] text-accent-primary hover:underline">All</button>
                                    <button onClick={e => { e.stopPropagation(); selectNoneObjs(id); }} className="text-[10px] text-text-muted hover:underline">None</button>
                                    {sel.size > 0 && <span className="text-[10px] text-success font-bold">{sel.size} selected</span>}
                                  </div>
                                  <div className="flex gap-2">
                                    <button onClick={(e) => { e.stopPropagation(); loadObjects(id, true); }}
                                      title="Re-read objects from BACnet network (slow)"
                                      className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-border text-[10px] text-text-muted hover:text-white transition-all">
                                      <RefreshCw size={10} /> Refresh from BACnet
                                    </button>
                                    <button onClick={(e) => { e.stopPropagation(); addSelectedPoints(id); }}
                                      disabled={adding === id || sel.size === 0}
                                      className="flex items-center gap-1.5 px-3 py-1.5 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-[10px] font-bold disabled:opacity-50 transition-all hover:-translate-y-0.5">
                                      {adding === id ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                                      Add {sel.size > 0 ? `${sel.size} Point${sel.size > 1 ? 's' : ''}` : 'Selected'}
                                    </button>
                                  </div>
                                </div>

                                {result && (
                                  <div className={`text-[10px] mb-2 px-2.5 py-1.5 rounded-lg ${result.error ? 'bg-error/10 text-error border border-error/20' : 'bg-success/10 text-success border border-success/20'}`}>
                                    {result.error ? `❌ ${result.error}` : `✅ Added ${result.created} point${result.created !== 1 ? 's' : ''}`}
                                  </div>
                                )}

                                {/* Per-object checkboxes */}
                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-1 max-h-52 overflow-y-auto pr-1">
                                  {devObjects.map((obj, i) => {
                                    const isChecked = sel.has(i);
                                    const typeShort = (obj.object_type || '').substring(0, 2).toUpperCase();
                                    return (
                                      <label key={i} onClick={e => e.stopPropagation()}
                                        className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg cursor-pointer transition-all select-none ${isChecked ? 'bg-accent-primary/10 border border-accent-primary/30' : 'bg-bg-input/30 border border-transparent hover:bg-bg-input/60'}`}>
                                        <input type="checkbox" className="sr-only" checked={isChecked} onChange={() => toggleObjSelect(id, i)} />
                                        {isChecked
                                          ? <CheckSquare size={12} className="text-accent-primary shrink-0" />
                                          : <Square size={12} className="text-text-muted shrink-0" />}
                                        <span className="text-[9px] px-1 py-0 rounded bg-info/15 text-info font-bold shrink-0">{typeShort}</span>
                                        <span className="text-[11px] text-text-secondary truncate flex-1">{obj.object_name || `${obj.object_type}:${obj.object_instance}`}</span>
                                        <span className="text-[10px] text-text-muted shrink-0">#{obj.object_instance}</span>
                                      </label>
                                    );
                                  })}
                                </div>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Empty state */}
      {devices.length === 0 && !scanning && (
        <div className="glass-card p-12 text-center">
          <WifiOff size={40} className="mx-auto text-text-muted mb-4 opacity-40" />
          <p className="text-text-secondary text-sm mb-2">No known devices</p>
          <p className="text-text-muted text-xs">Click <b>Scan BACnet</b> to discover devices on the network. Devices are then saved permanently.</p>
        </div>
      )}
      {filtered.length === 0 && devices.length > 0 && searchQuery && (
        <div className="glass-card p-8 text-center">
          <Search size={30} className="mx-auto text-text-muted mb-3 opacity-40" />
          <p className="text-text-secondary text-sm">No devices matching "{searchQuery}"</p>
        </div>
      )}
    </div>
  );
}
