import React, { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  LayoutDashboard, Activity, Map, Users, Eye, BarChart2,
  FileText, Clock, Settings, Zap, Wifi, WifiOff,
  ChevronRight, AlertTriangle,
} from 'lucide-react';

const API = '/api';

const NAV = [
  { group: 'OVERVIEW', items: [
    { path: '/', label: 'Dashboard', icon: LayoutDashboard },
    { path: '/device-health', label: 'Device Health', icon: Activity },
  ]},
  { group: 'BACNET', items: [
    { path: '/devices', label: 'Devices', icon: Wifi },
    { path: '/mappings', label: 'Mappings', icon: Map },
    { path: '/groups', label: 'Groups', icon: Users },
    { path: '/monitor', label: 'Monitor', icon: Eye },
  ]},
  { group: 'ANALYTICS', items: [
    { path: '/charts', label: 'Charts', icon: BarChart2 },
    { path: '/logs', label: 'Logs', icon: FileText },
    { path: '/scheduler', label: 'Scheduler', icon: Clock },
    { path: '/anomaly', label: 'Anomaly', icon: AlertTriangle },
  ]},
  { group: 'CONFIG', items: [
    { path: '/settings', label: 'Settings', icon: Settings },
  ]},
];

export function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
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
    <aside className="w-[200px] shrink-0 flex flex-col bg-bg-secondary border-r border-border h-screen">
      {/* Logo */}
      <div className="px-4 pt-5 pb-4 border-b border-border">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent-primary to-purple-600 flex items-center justify-center">
            <Zap size={16} className="text-white" />
          </div>
          <div>
            <div className="text-sm font-bold text-white tracking-tight">Gateway <span className="text-accent-primary">V2</span></div>
            <div className="text-[10px] text-text-muted">BACnet → MQTT</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-4">
        {NAV.map(group => (
          <div key={group.group}>
            <div className="px-2 mb-1 text-[9px] font-bold tracking-widest text-text-muted uppercase">{group.group}</div>
            {group.items.map(item => {
              const active = location.pathname === item.path;
              return (
                <button key={item.path} onClick={() => navigate(item.path)}
                  className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left text-xs font-medium transition-all group ${
                    active
                      ? 'bg-accent-primary/15 text-accent-primary'
                      : 'text-text-secondary hover:text-white hover:bg-white/[0.04]'
                  }`}>
                  <item.icon size={15} className={active ? 'text-accent-primary' : 'text-text-muted group-hover:text-white'} />
                  {item.label}
                  {active && <ChevronRight size={12} className="ml-auto text-accent-primary" />}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Status footer */}
      <div className="px-3 py-3 border-t border-border space-y-1.5">
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${bacnetOk ? 'bg-success' : 'bg-error'}`} />
          <span className="text-[10px] text-text-muted">BACnet: <span className={bacnetOk ? 'text-success' : 'text-error'}>{bacnetOk ? 'Connected' : 'Disconnected'}</span></span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${mqttOk ? 'bg-success' : 'bg-error'}`} />
          <span className="text-[10px] text-text-muted">MQTT: <span className={mqttOk ? 'text-success' : 'text-error'}>{mqttOk ? 'Connected' : 'Disconnected'}</span></span>
        </div>
        {status?.active_mappings > 0 && (
          <div className="text-[10px] text-text-muted">Polling <span className="text-accent-primary font-bold">{status.active_mappings}</span> points</div>
        )}
      </div>
    </aside>
  );
}
