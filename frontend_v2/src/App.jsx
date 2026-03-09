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

function App() {
  const [page, setPage] = useState('dashboard');
  const [authChecked, setAuthChecked] = useState(false);   // waiting for /api/auth/status
  const [authEnabled, setAuthEnabled] = useState(false);    // server requires login?
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
  });
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() =>
    localStorage.getItem('sidebar_collapsed') === 'true'
  );

  const toggleSidebar = () => setSidebarCollapsed(prev => {
    const next = !prev;
    localStorage.setItem('sidebar_collapsed', String(next));
    return next;
  });

  // Check if auth is enabled on server
  useEffect(() => {
    fetch('/api/auth/status')
      .then(r => r.json())
      .then(data => {
        setAuthEnabled(data.auth_enabled);
        setAuthChecked(true);
      })
      .catch(() => setAuthChecked(true));
  }, []);

  // Validate existing token on mount (if auth enabled)
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

  // Show nothing while checking auth
  if (!authChecked) return null;

  // Show LoginPage if auth enabled and not logged in
  if (authEnabled && !token) {
    return <LoginPage onLogin={handleLogin} />;
  }

  const authCtx = {
    user: user || { username: 'anonymous', role: 'admin' },
    token,
    logout: handleLogout,
    apiFetch,
  };

  return (
    <AuthContext.Provider value={authCtx}>
      <div className="min-h-screen bg-bg-primary text-text-primary">
        <ToastContainer />
        <Sidebar activePage={page} onNavigate={setPage} collapsed={sidebarCollapsed} onToggle={toggleSidebar} />
        <main
          className="min-h-screen overflow-x-hidden transition-all duration-300"
          style={{
            marginLeft: sidebarCollapsed ? '64px' : '220px',
            width: sidebarCollapsed ? 'calc(100vw - 64px)' : 'calc(100vw - 220px)',
          }}
        >
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
        </main>
      </div>
    </AuthContext.Provider>
  );
}

export default App;
