import React, { useEffect, useState } from 'react';
import { LayoutDashboard, Cable, Activity, Replace, Users, LineChart, FileText, Clock, Settings, Eye, AlertTriangle } from 'lucide-react';

const SidebarItem = ({ icon: Icon, label, active = false, onClick, badge }) => (
  <div
    onClick={onClick}
    className={`flex items-center gap-3 px-3 py-2 rounded-md cursor-pointer transition-all relative ${
      active ? 'bg-blue-500/15 text-accent-primary' : 'text-text-secondary hover:bg-blue-500/10 hover:text-white'
    }`}
  >
    {active && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-3/5 bg-gradient-to-b from-accent-primary to-purple-500 rounded-r-md" />}
    <Icon size={18} />
    <span className="text-sm font-medium flex-1">{label}</span>
    {badge && <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-error/20 text-error">{badge}</span>}
  </div>
);

export function Sidebar({ activePage = 'dashboard', onNavigate }) {
  const nav = (page) => () => onNavigate?.(page);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    const load = () => fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const bacnetOk = status?.bacnet_connected;
  const mqttOk = status?.mqtt_connected;

  return (
    <aside className="w-[220px] bg-bg-secondary border-r border-border flex flex-col fixed top-0 left-0 bottom-0 z-50 backdrop-blur-xl">
      <div className="flex items-center p-4 border-b border-border">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-gradient-to-br from-accent-primary to-purple-600 rounded-md flex items-center justify-center text-white shadow-[0_0_20px_rgba(0,240,255,0.3)]">
            ⚡
          </div>
          <div>
            <h1 className="text-base font-bold tracking-tight text-gradient">Gateway V2</h1>
            <span className="text-xs text-text-muted">BACnet → MQTT</span>
          </div>
        </div>
      </div>

      <nav className="flex-1 p-3 overflow-y-auto flex flex-col gap-0.5">
        <div className="text-[10px] uppercase font-bold tracking-widest text-text-muted px-3 mt-2 mb-1">Overview</div>
        <SidebarItem icon={LayoutDashboard} label="Dashboard" active={activePage === 'dashboard'} onClick={nav('dashboard')} />
        <SidebarItem icon={Activity} label="Device Health" active={activePage === 'device-health'} onClick={nav('device-health')} />

        <div className="text-[10px] uppercase font-bold tracking-widest text-text-muted px-3 mt-3 mb-1">BACnet</div>
        <SidebarItem icon={Cable} label="Devices" active={activePage === 'devices'} onClick={nav('devices')} />
        <SidebarItem icon={Replace} label="Mappings" active={activePage === 'mappings'} onClick={nav('mappings')} />
        <SidebarItem icon={Users} label="Groups" active={activePage === 'groups'} onClick={nav('groups')} />
        <SidebarItem icon={Eye} label="Monitor" active={activePage === 'monitor'} onClick={nav('monitor')} />

        <div className="text-[10px] uppercase font-bold tracking-widest text-text-muted px-3 mt-3 mb-1">Analytics</div>
        <SidebarItem icon={LineChart} label="Charts" active={activePage === 'charts'} onClick={nav('charts')} />
        <SidebarItem icon={FileText} label="Logs" active={activePage === 'logs'} onClick={nav('logs')} />
        <SidebarItem icon={Clock} label="Scheduler" active={activePage === 'scheduler'} onClick={nav('scheduler')} />
        <SidebarItem icon={AlertTriangle} label="Anomaly" active={activePage === 'anomaly'} onClick={nav('anomaly')} />

        <div className="text-[10px] uppercase font-bold tracking-widest text-text-muted px-3 mt-3 mb-1">Config</div>
        <SidebarItem icon={Settings} label="Settings" active={activePage === 'settings'} onClick={nav('settings')} />
      </nav>

      {/* Live status footer */}
      <div className="p-4 border-t border-border text-xs space-y-2">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full transition-colors ${bacnetOk ? 'bg-success shadow-[0_0_8px_rgba(0,255,136,0.5)] animate-pulse' : 'bg-error'}`} />
          <span>BACnet: <span className={`font-medium ${bacnetOk ? 'text-success' : 'text-error'}`}>{bacnetOk ? 'Connected' : 'Disconnected'}</span></span>
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full transition-colors ${mqttOk ? 'bg-success shadow-[0_0_8px_rgba(0,255,136,0.5)] animate-pulse' : 'bg-error'}`} />
          <span>MQTT: <span className={`font-medium ${mqttOk ? 'text-success' : 'text-error'}`}>{mqttOk ? 'Connected' : 'Disconnected'}</span></span>
        </div>
        {status?.active_mappings != null && (
          <div className="text-text-muted">Polling <span className="text-white font-medium">{status.active_mappings}</span> points</div>
        )}
      </div>
    </aside>
  );
}
