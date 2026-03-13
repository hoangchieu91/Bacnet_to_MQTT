import React, { useState, useEffect, useCallback, createContext, useContext } from 'react';
import { Sidebar } from './components/Sidebar';
import { Dashboard } from './components/Dashboard';
import { MappingsPage } from './components/MappingsPage';
import { DevicesPage } from './components/DevicesPage';
import { GroupsPage } from './components/GroupsPage';
import { ChartsPage } from './components/ChartsPage';
import { LogsPage } from './components/LogsPage';
import { SchedulerPage } from './components/SchedulerPage';
import { SettingsPage } from './components/SettingsPage';
import { MonitorPage } from './components/MonitorPage';
import { DeviceHealthPage } from './components/DeviceHealthPage';
import { AnomalyPage } from './components/AnomalyPage';
import { LoginPage } from './components/LoginPage';
import { ToastContainer } from './components/ToastContainer';
import { ExportPage } from './components/ExportPage';
import { BacnetToolsPage } from './components/BacnetToolsPage';
import {
  LayoutDashboard, Activity, Replace, Eye, TrendingUp, Settings,
  Cable, Users, FileText, Clock, AlertTriangle, Download, Menu, X,
} from 'lucide-react';

// ── Auth Context ──────────────────────────────────────────────
export const AuthContext = createContext({ user: null, token: null, logout: () => {}, apiFetch: null });
export const useAuth = () => useContext(AuthContext);

const TOKEN_KEY = 'bacnet_gw_token';
const USER_KEY  = 'bacnet_gw_user';

/** Fetch wrapper that automatically adds Authorization header */
export function makeApiFetch(token) {
  return (url, opts = {}) => {
    const headers = { ...(opts.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return fetch(url, { ...opts, headers });
  };
}

// ── Bottom nav items (most-used on mobile) ────────────────────
const BOTTOM_NAV = [
  { page: 'dashboard',     icon: LayoutDashboard, label: 'Home' },
  { page: 'device-health', icon: Activity,        label: 'Health' },
  { page: 'monitor',       icon: Eye,             label: 'Monitor' },
  { page: 'mappings',      icon: Replace,         label: 'Maps' },
  { page: 'more',          icon: Menu,            label: 'More' },
];

// All pages for the "More" drawer
const MORE_ITEMS = [
  { page: 'devices',   icon: Cable,         label: 'Devices' },
  { page: 'groups',    icon: Users,         label: 'Groups' },
  { page: 'charts',    icon: TrendingUp,    label: 'Trending' },
  { page: 'logs',      icon: FileText,      label: 'Logs' },
  { page: 'scheduler', icon: Clock,         label: 'Scheduler' },
  { page: 'anomaly',   icon: AlertTriangle, label: 'Anomaly' },
  { page: 'export',    icon: Download,      label: 'Export' },
  { page: 'settings',  icon: Settings,      label: 'Settings' },
];

function MobileMoreDrawer({ activePage, onNavigate, onClose }) {
  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden" onClick={onClose} />
      {/* Drawer sliding up from bottom */}
      <div className="fixed bottom-16 left-0 right-0 z-50 md:hidden animate-slide-up">
        <div className="mx-3 mb-2 bg-bg-secondary border border-border rounded-2xl shadow-2xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="text-sm font-bold text-white">More</span>
            <button onClick={onClose} className="p-1 rounded text-text-muted hover:text-white">
              <X size={16} />
            </button>
          </div>
          <div className="grid grid-cols-4 gap-0 p-3">
            {MORE_ITEMS.map(({ page, icon: Icon, label }) => (
              <button key={page} onClick={() => { onNavigate(page); onClose(); }}
                className={`flex flex-col items-center gap-1.5 py-3 px-1 rounded-xl transition-all ${
                  activePage === page
                    ? 'bg-accent-primary/15 text-accent-primary'
                    : 'text-text-secondary hover:bg-white/5 hover:text-white'
                }`}>
                <Icon size={22} />
                <span className="text-[10px] font-medium text-center leading-tight">{label}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

function App() {
  const [page, setPage] = useState('dashboard');
  const [authChecked, setAuthChecked] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
  });
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() =>
    localStorage.getItem('sidebar_collapsed') === 'true'
  );
  const [showMore, setShowMore] = useState(false);
  // Reactive mobile detection — updates on window resize
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.innerWidth < 768
  );

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const toggleSidebar = () => setSidebarCollapsed(prev => {
    const next = !prev;
    localStorage.setItem('sidebar_collapsed', String(next));
    return next;
  });

  const navigate = (p) => {
    setPage(p);
    setShowMore(false);
  };

  useEffect(() => {
    fetch('/api/auth/status')
      .then(r => r.json())
      .then(data => { setAuthEnabled(data.auth_enabled); setAuthChecked(true); })
      .catch(() => setAuthChecked(true));
  }, []);

  useEffect(() => {
    if (!authEnabled || !token) return;
    makeApiFetch(token)('/api/auth/me')
      .then(r => { if (!r.ok) handleLogout(); })
      .catch(() => {});
  }, [authEnabled, token]);

  const handleLogin = useCallback((data) => {
    const { token: tok, username, role } = data;
    const userInfo = { username, role };
    if (tok) localStorage.setItem(TOKEN_KEY, tok);
    localStorage.setItem(USER_KEY, JSON.stringify(userInfo));
    setToken(tok);
    setUser(userInfo);
  }, []);

  const handleLogout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }, []);

  const apiFetch = makeApiFetch(token);

  if (!authChecked) return null;
  if (authEnabled && !token) return <LoginPage onLogin={handleLogin} />;

  const authCtx = {
    user: user || { username: 'anonymous', role: 'admin' },
    token,
    logout: handleLogout,
    apiFetch,
  };

  // Determine if active page is in "more" group for highlighting
  const isMoreActive = MORE_ITEMS.some(i => i.page === page);

  return (
    <AuthContext.Provider value={authCtx}>
      <div className="min-h-screen bg-bg-primary text-text-primary">
        <ToastContainer />

        {/* Desktop sidebar (hidden on mobile via Sidebar component) */}
        <Sidebar activePage={page} onNavigate={navigate} collapsed={sidebarCollapsed} onToggle={toggleSidebar} />

        {/* Main content */}
        <main
          className="min-h-screen overflow-x-hidden transition-all duration-300 pb-20 md:pb-0"
          style={isMobile ? {} : {
            marginLeft: `${sidebarCollapsed ? 64 : 220}px`,
            width: `calc(100vw - ${sidebarCollapsed ? 64 : 220}px)`,
          }}
        >

          {/* slideUp animation for More drawer */}
          <style>{`
            @keyframes slideUp {
              from { transform: translateY(20px); opacity: 0; }
              to   { transform: translateY(0);    opacity: 1; }
            }
            .animate-slide-up { animation: slideUp 0.2s ease-out; }
          `}</style>

          {page === 'dashboard'      && <Dashboard />}
          {page === 'devices'        && <DevicesPage />}
          {page === 'device-health'  && <DeviceHealthPage />}
          {page === 'mappings'       && <MappingsPage />}
          {page === 'groups'         && <GroupsPage />}
          {page === 'charts'         && <ChartsPage />}
          {page === 'logs'           && <LogsPage />}
          {page === 'scheduler'      && <SchedulerPage />}
          {page === 'monitor'        && <MonitorPage />}
          {page === 'anomaly'        && <AnomalyPage />}
          {page === 'settings'       && <SettingsPage />}
          {page === 'export'         && <ExportPage />}
          {page === 'bacnet-tools'   && <BacnetToolsPage />}
        </main>

        {/* ── Mobile Bottom Navigation Bar ── */}
        <nav className="fixed bottom-0 left-0 right-0 z-40 md:hidden
                        bg-bg-secondary/95 backdrop-blur-xl border-t border-border
                        safe-area-inset-bottom">
          <div className="flex items-stretch h-16">
            {BOTTOM_NAV.map(({ page: p, icon: Icon, label }) => {
              const isActive = p === 'more' ? isMoreActive || showMore : page === p;
              return (
                <button
                  key={p}
                  onClick={() => {
                    if (p === 'more') setShowMore(s => !s);
                    else navigate(p);
                  }}
                  className={`flex-1 flex flex-col items-center justify-center gap-1 transition-all relative
                    ${isActive ? 'text-accent-primary' : 'text-text-muted hover:text-white'}`}
                >
                  {/* Active dot indicator */}
                  {isActive && (
                    <span className="absolute top-0 left-1/2 -translate-x-1/2 w-6 h-0.5 rounded-b-full bg-accent-primary" />
                  )}
                  <Icon size={22} strokeWidth={isActive ? 2.2 : 1.8} />
                  <span className={`text-[10px] font-medium ${isActive ? 'text-accent-primary' : ''}`}>
                    {label}
                  </span>
                </button>
              );
            })}
          </div>
          {/* Safe area for notch phones */}
          <div className="h-safe-bottom bg-bg-secondary/95" />
        </nav>

        {/* Mobile "More" drawer */}
        {showMore && (
          <MobileMoreDrawer
            activePage={page}
            onNavigate={navigate}
            onClose={() => setShowMore(false)}
          />
        )}
      </div>
    </AuthContext.Provider>
  );
}

export default App;
