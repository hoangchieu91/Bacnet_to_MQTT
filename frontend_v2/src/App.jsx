import React, { useState } from 'react';
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
import { ToastContainer } from './components/ToastContainer';

function App() {
  const [page, setPage] = useState('dashboard');

  return (
    <div className="min-h-screen bg-bg-primary text-text-primary flex">
      <ToastContainer />
      <Sidebar activePage={page} onNavigate={setPage} />
      <main className="ml-[220px] flex-1">
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
      </main>
    </div>
  );
}

export default App;
