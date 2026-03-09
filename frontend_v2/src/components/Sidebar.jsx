import React, { useEffect, useState } from 'react';
import { LayoutDashboard, Cable, Activity, Replace, Users, TrendingUp, FileText, Clock, Settings, Eye, AlertTriangle, LogOut, Download, ChevronLeft, ChevronRight } from 'lucide-react';
import { useAuth } from '../App';

const SidebarItem = ({ icon: Icon, label, active = false, onClick, badge, collapsed }) => (
  <div
    onClick={onClick}
    title={collapsed ? label : undefined}
    className={`flex items-center gap-3 px-3 py-2 rounded-md cursor-pointer transition-all relative group ${
      active ? 'bg-blue-500/15 text-accent-primary' : 'text-text-secondary hover:bg-blue-500/10 hover:text-white'
    } ${collapsed ? 'justify-center px-2' : ''}`}
  >
    {active && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-3/5 bg-gradient-to-b from-accent-primary to-purple-500 rounded-r-md" />}
    <Icon size={18} className="flex-shrink-0" />
    {!collapsed && <span className="text-sm font-medium flex-1 truncate">{label}</span>}
    {!collapsed && badge && <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-error/20 text-error">{badge}</span>}
    {collapsed && (
      <div className="absolute left-full ml-2 px-2 py-1 bg-bg-secondary border border-border rounded-md text-xs text-white whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 shadow-lg">
        {label}
      </div>
    )}
  </div>
);

export function Sidebar({ activePage = 'dashboard', onNavigate, collapsed = false, onToggle }) {
  const nav = (page) => () => onNavigate?.(page);
  const [status, setStatus] = useState(null);
  const { user, logout } = useAuth();

  useEffect(() => {
    const load = () => fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const bacnetOk = status?.bacnet_connected;
  const mqttOk = status?.mqtt_connected;

  return (
    // hidden on mobile, flex on md+
    <aside
      className="hidden md:flex bg-bg-secondary border-r border-border flex-col fixed top-0 left-0 bottom-0 z-50 backdrop-blur-xl transition-all duration-300"
      style={{ width: collapsed ? '64px' : '220px' }}
    >
      {/* Header */}
      <div className={`flex items-center p-3 border-b border-border ${collapsed ? 'justify-center' : 'justify-between'}`}>
        {!collapsed && (
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 flex-shrink-0 bg-gradient-to-br from-accent-primary to-purple-600 rounded-md flex items-center justify-center text-white shadow-[0_0_20px_rgba(0,240,255,0.3)]">
              ⚡
            </div>
            <div className="min-w-0">
              <h1 className="text-base font-bold tracking-tight text-gradient truncate">Gateway V2</h1>
              <span className="text-xs text-text-muted">BACnet → MQTT</span>
            </div>
          </div>
        )}
        {collapsed && (
          <div className="w-9 h-9 bg-gradient-to-br from-accent-primary to-purple-600 rounded-md flex items-center justify-center text-white shadow-[0_0_20px_rgba(0,240,255,0.3)]">
            ⚡
          </div>
        )}
        <button
          onClick={onToggle}
          className={`flex-shrink-0 p-1.5 rounded-md text-text-muted hover:text-white hover:bg-white/10 transition-all ${collapsed ? 'absolute right-1 top-3' : ''}`}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 p-2 overflow-y-auto overflow-x-hidden flex flex-col gap-0.5">
        {!collapsed && <div className="text-[10px] uppercase font-bold tracking-widest text-text-muted px-3 mt-2 mb-1">Overview</div>}
        {collapsed && <div className="my-1 border-t border-border/50" />}
        <SidebarItem icon={LayoutDashboard} label="Dashboard" active={activePage === 'dashboard'} onClick={nav('dashboard')} collapsed={collapsed} />
        <SidebarItem icon={Activity} label="Device Health" active={activePage === 'device-health'} onClick={nav('device-health')} collapsed={collapsed} />

        {!collapsed && <div className="text-[10px] uppercase font-bold tracking-widest text-text-muted px-3 mt-3 mb-1">BACnet</div>}
        {collapsed && <div className="my-1 border-t border-border/50" />}
        <SidebarItem icon={Cable} label="Devices" active={activePage === 'devices'} onClick={nav('devices')} collapsed={collapsed} />
        <SidebarItem icon={Replace} label="Mappings" active={activePage === 'mappings'} onClick={nav('mappings')} collapsed={collapsed} />
        <SidebarItem icon={Users} label="Groups" active={activePage === 'groups'} onClick={nav('groups')} collapsed={collapsed} />
        <SidebarItem icon={Eye} label="Monitor" active={activePage === 'monitor'} onClick={nav('monitor')} collapsed={collapsed} />

        {!collapsed && <div className="text-[10px] uppercase font-bold tracking-widest text-text-muted px-3 mt-3 mb-1">Analytics</div>}
        {collapsed && <div className="my-1 border-t border-border/50" />}
        <SidebarItem icon={TrendingUp} label="Trending" active={activePage === 'charts'} onClick={nav('charts')} collapsed={collapsed} />
        <SidebarItem icon={FileText} label="Logs" active={activePage === 'logs'} onClick={nav('logs')} collapsed={collapsed} />
        <SidebarItem icon={Clock} label="Scheduler" active={activePage === 'scheduler'} onClick={nav('scheduler')} collapsed={collapsed} />
        <SidebarItem icon={AlertTriangle} label="Anomaly" active={activePage === 'anomaly'} onClick={nav('anomaly')} collapsed={collapsed} />
        <SidebarItem icon={Download} label="Export" active={activePage === 'export'} onClick={nav('export')} collapsed={collapsed} />

        {!collapsed && <div className="text-[10px] uppercase font-bold tracking-widest text-text-muted px-3 mt-3 mb-1">Config</div>}
        {collapsed && <div className="my-1 border-t border-border/50" />}
        <SidebarItem icon={Settings} label="Settings" active={activePage === 'settings'} onClick={nav('settings')} collapsed={collapsed} />
      </nav>

      {/* User info + logout */}
      {user && user.username !== 'anonymous' && (
        <div className={`px-3 pt-3 pb-2 border-t border-border flex items-center ${collapsed ? 'justify-center' : 'gap-2'}`}>
          {!collapsed && (
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold text-white truncate">{user.username}</div>
              <div className={`text-[10px] font-bold ${
                user.role === 'admin' ? 'text-accent-primary' :
                user.role === 'operator' ? 'text-yellow-400' : 'text-text-muted'
              }`}>{user.role}</div>
            </div>
          )}
          <button onClick={logout} title="Logout"
            className="p-1.5 rounded text-text-muted hover:text-error hover:bg-error/10 transition-all flex-shrink-0">
            <LogOut size={14} />
          </button>
        </div>
      )}

      {/* Status footer */}
      <div className={`p-3 border-t border-border text-xs space-y-1.5 ${collapsed ? 'flex flex-col items-center' : ''}`}>
        <div className="flex items-center gap-2" title={collapsed ? `BACnet: ${bacnetOk ? 'Connected' : 'Disconnected'}` : undefined}>
          <div className={`w-2 h-2 rounded-full flex-shrink-0 transition-colors ${bacnetOk ? 'bg-success shadow-[0_0_8px_rgba(0,255,136,0.5)] animate-pulse' : 'bg-error'}`} />
          {!collapsed && <span>BACnet: <span className={`font-medium ${bacnetOk ? 'text-success' : 'text-error'}`}>{bacnetOk ? 'Connected' : 'Off'}</span></span>}
        </div>
        <div className="flex items-center gap-2" title={collapsed ? `MQTT: ${mqttOk ? 'Connected' : 'Disconnected'}` : undefined}>
          <div className={`w-2 h-2 rounded-full flex-shrink-0 transition-colors ${mqttOk ? 'bg-success shadow-[0_0_8px_rgba(0,255,136,0.5)] animate-pulse' : 'bg-error'}`} />
          {!collapsed && <span>MQTT: <span className={`font-medium ${mqttOk ? 'text-success' : 'text-error'}`}>{mqttOk ? 'Connected' : 'Off'}</span></span>}
        </div>
        {!collapsed && status?.active_mappings != null && (
          <div className="text-text-muted">Polling <span className="text-white font-medium">{status.active_mappings}</span> pts</div>
        )}
      </div>
    </aside>
  );
}
