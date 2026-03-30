import React, { useState, useEffect } from 'react';
import {
  LayoutDashboard, Activity, Cable, Replace, Users, Eye, TrendingUp,
  FileText, Clock, Settings, Zap, AlertTriangle, Download,
  ChevronRight, ChevronLeft, Wrench,
} from 'lucide-react';

const API = '/api';

const NAV = [
  { group: 'OVERVIEW', items: [
    { page: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { page: 'device-health', label: 'Device Health', icon: Activity },
  ]},
  { group: 'BACNET', items: [
    { page: 'devices', label: 'Devices', icon: Cable },
    { page: 'mappings', label: 'Mappings', icon: Replace },
    { page: 'groups', label: 'Groups', icon: Users },
    { page: 'monitor', label: 'Monitor', icon: Eye },
  ]},
  { group: 'TOOLS', items: [
    { page: 'bacnet-tools', label: 'BACnet Tools', icon: Wrench },
  ]},
  { group: 'ANALYTICS', items: [
    { page: 'charts', label: 'Charts', icon: TrendingUp },
    { page: 'logs', label: 'Logs', icon: FileText },
    { page: 'scheduler', label: 'Scheduler', icon: Clock },
    { page: 'anomaly', label: 'Anomaly', icon: AlertTriangle },
    { page: 'export', label: 'Export', icon: Download },
  ]},
  { group: 'CONFIG', items: [
    { page: 'settings', label: 'Settings', icon: Settings },
  ]},
];

export function Sidebar({ activePage, onNavigate, collapsed, onToggle }) {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    const fetch_ = async () => {
      try {
        const r = await fetch(`${API}/status`);
        const d = await r.json();
        setStatus(d);
      } catch { /* ignore */ }
    };
    fetch_();
    const iv = setInterval(fetch_, 5000);
    return () => clearInterval(iv);
  }, []);

  const bacnetOk = status?.bacnet_connected;
  const mqttOk = status?.mqtt_connected;

  return (
    <aside
      className={`hidden md:flex flex-col bg-bg-secondary border-r border-border h-screen fixed top-0 left-0 z-30 transition-all duration-300 ${
        collapsed ? 'w-16' : 'w-[220px]'
      }`}
    >
      {/* Logo + collapse toggle */}
      <div className="px-3 pt-4 pb-3 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent-primary to-sky-600 flex items-center justify-center shrink-0">
            <Zap size={16} className="text-white" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="text-sm font-bold text-white tracking-tight truncate">Gateway <span className="text-accent-primary">V2</span></div>
              <div className="text-[10px] text-text-muted">BACnet → MQTT</div>
            </div>
          )}
        </div>
        <button onClick={onToggle} className="p-1 rounded text-text-muted hover:text-white hover:bg-white/10 transition-colors shrink-0">
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-4">
        {NAV.map(group => (
          <div key={group.group}>
            {!collapsed && (
              <div className="px-2 mb-1 text-[9px] font-bold tracking-widest text-text-muted uppercase">{group.group}</div>
            )}
            {group.items.map(item => {
              const active = activePage === item.page;
              return (
                <button key={item.page} onClick={() => onNavigate(item.page)}
                  title={collapsed ? item.label : undefined}
                  className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left text-xs font-medium transition-all group ${
                    active
                      ? 'bg-accent-primary/15 text-accent-primary'
                      : 'text-text-secondary hover:text-white hover:bg-white/[0.04]'
                  } ${collapsed ? 'justify-center' : ''}`}>
                  <item.icon size={15} className={active ? 'text-accent-primary' : 'text-text-muted group-hover:text-white'} />
                  {!collapsed && item.label}
                  {!collapsed && active && <ChevronRight size={12} className="ml-auto text-accent-primary" />}
                </button>
              );
            })}
          </div>
        ))}

        {/* External Diagnostic Tools */}
        <div>
          {!collapsed && (
            <div className="px-2 mb-1 text-[9px] font-bold tracking-widest text-text-muted uppercase">Diagnostics</div>
          )}
          <a href={`${window.location.protocol}//${window.location.hostname}:8765`} target="_blank" rel="noopener noreferrer"
            title={collapsed ? 'MS/TP Tools' : undefined}
            className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium text-text-secondary hover:text-white hover:bg-white/[0.04] transition-all ${collapsed ? 'justify-center' : ''}`}>
            <span className="text-sm shrink-0">🔌</span>
            {!collapsed && <>MS/TP Tools <span className="ml-auto text-[9px] text-text-muted">↗</span></>}
          </a>
          <a href={`${window.location.protocol}//${window.location.hostname}:8766`} target="_blank" rel="noopener noreferrer"
            title={collapsed ? 'Modbus RTU' : undefined}
            className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium text-text-secondary hover:text-white hover:bg-white/[0.04] transition-all ${collapsed ? 'justify-center' : ''}`}>
            <span className="text-sm shrink-0">📟</span>
            {!collapsed && <>Modbus RTU <span className="ml-auto text-[9px] text-text-muted">↗</span></>}
          </a>
        </div>
      </nav>

      {/* Status footer */}
      <div className={`px-3 py-3 border-t border-border space-y-1.5 ${collapsed ? 'flex flex-col items-center' : ''}`}>
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${bacnetOk ? 'bg-success' : 'bg-error'}`} />
          {!collapsed && (
            <span className="text-[10px] text-text-muted">BACnet: <span className={bacnetOk ? 'text-success' : 'text-error'}>{bacnetOk ? 'OK' : 'Off'}</span></span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${mqttOk ? 'bg-success' : 'bg-error'}`} />
          {!collapsed && (
            <span className="text-[10px] text-text-muted">MQTT: <span className={mqttOk ? 'text-success' : 'text-error'}>{mqttOk ? 'OK' : 'Off'}</span></span>
          )}
        </div>
        {!collapsed && status?.active_mappings > 0 && (
          <div className="text-[10px] text-text-muted">Polling <span className="text-accent-primary font-bold">{status.active_mappings}</span> points</div>
        )}
      </div>
    </aside>
  );
}
