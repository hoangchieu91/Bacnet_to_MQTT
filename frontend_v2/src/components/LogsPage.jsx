import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Trash2, Download, RefreshCw, AlertTriangle, Info, Bug, CheckCircle } from 'lucide-react';

const API = '/api';

const LEVEL_COLORS = {
  ERROR: 'text-error', WARNING: 'text-warning', INFO: 'text-info', DEBUG: 'text-text-muted',
};
const LEVEL_ICONS = {
  ERROR: AlertTriangle, WARNING: AlertTriangle, INFO: Info, DEBUG: Bug,
};

function parseLevel(line) {
  if (line.includes('[ERROR]')) return 'ERROR';
  if (line.includes('[WARNING]')) return 'WARNING';
  if (line.includes('[DEBUG]')) return 'DEBUG';
  return 'INFO';
}

export function LogsPage() {
  const [logs, setLogs] = useState([]);
  const [filter, setFilter] = useState('');
  const [levelFilter, setLevelFilter] = useState('ALL');
  const [autoScroll, setAutoScroll] = useState(true);
  const containerRef = useRef(null);
  const intervalRef = useRef(null);

  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch(`${API}/logs?lines=200`);
      const data = await res.json();
      setLogs(data.logs || []);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => {
    fetchLogs();
    intervalRef.current = setInterval(fetchLogs, 3000);
    return () => clearInterval(intervalRef.current);
  }, [fetchLogs]);

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const filteredLogs = logs.filter(line => {
    if (filter && !line.toLowerCase().includes(filter.toLowerCase())) return false;
    if (levelFilter !== 'ALL' && !line.includes(`[${levelFilter}]`)) return false;
    return true;
  });

  const handleExport = () => {
    const blob = new Blob([filteredLogs.join('\n')], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `gateway_logs_${new Date().toISOString().slice(0, 16)}.txt`;
    a.click(); URL.revokeObjectURL(url);
  };

  const levelCounts = { ERROR: 0, WARNING: 0, INFO: 0, DEBUG: 0 };
  logs.forEach(l => { const lv = parseLevel(l); levelCounts[lv]++; });

  return (
    <div className="p-6 flex flex-col h-screen">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">Logs</h2>
          <p className="text-xs text-text-muted mt-1">{logs.length} lines • auto-refresh 3s</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleExport} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all" title="Export"><Download size={16} /></button>
          <button onClick={fetchLogs} className="p-2 rounded-lg border border-border text-text-secondary hover:text-white hover:border-accent-primary transition-all" title="Refresh"><RefreshCw size={16} /></button>
        </div>
      </div>

      {/* Level badges */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        {['ALL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'].map(lv => (
          <button key={lv} onClick={() => setLevelFilter(lv)}
            className={`px-3 py-1 rounded-full text-xs font-bold transition-all ${levelFilter === lv ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/40' : 'bg-bg-input border border-border text-text-secondary hover:text-white'}`}>
            {lv} {lv !== 'ALL' && <span className="ml-1 opacity-60">{levelCounts[lv]}</span>}
          </button>
        ))}
        <div className="flex-1" />
        <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
          <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} className="accent-accent-primary" />
          Auto-scroll
        </label>
      </div>

      {/* Search */}
      <input type="text" placeholder="Filter logs..." value={filter} onChange={e => setFilter(e.target.value)}
        className="w-full px-4 py-2 bg-bg-input border border-border rounded-xl text-sm text-white placeholder:text-text-muted mb-3 focus:outline-none focus:border-border-focus" />

      {/* Log output */}
      <div ref={containerRef} className="flex-1 glass-card p-4 overflow-y-auto font-mono text-xs leading-relaxed min-h-[300px]">
        {filteredLogs.map((line, i) => {
          const level = parseLevel(line);
          const color = LEVEL_COLORS[level] || 'text-text-primary';
          return (
            <div key={i} className={`${color} py-0.5 hover:bg-bg-card-hover px-2 rounded whitespace-pre-wrap break-all`}>
              {line}
            </div>
          );
        })}
        {filteredLogs.length === 0 && <div className="text-text-muted text-center py-8">No logs matching filter</div>}
      </div>
    </div>
  );
}
