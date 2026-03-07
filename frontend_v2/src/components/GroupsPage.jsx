import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Trash2, Edit3, Check, X, FolderOpen, ChevronDown, ChevronRight } from 'lucide-react';

const API = '/api';

export function GroupsPage() {
  const [groups, setGroups] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState('');
  const [newName, setNewName] = useState('');
  const [showNew, setShowNew] = useState(false);
  const [expandedGroup, setExpandedGroup] = useState(null);
  const [showAssign, setShowAssign] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [g, m] = await Promise.all([
        fetch(`${API}/groups`).then(r => r.json()),
        fetch(`${API}/mappings`).then(r => r.json()),
      ]);
      setGroups(g.groups || []);
      setMappings(m.mappings || []);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const createGroup = async () => {
    if (!newName.trim()) return;
    try {
      await fetch(`${API}/groups`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: newName.trim() }) });
      setNewName(''); setShowNew(false);
      await fetchData();
    } catch (e) { console.error(e); }
  };

  const updateGroup = async (id) => {
    if (!editName.trim()) return;
    try {
      await fetch(`${API}/groups/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: editName.trim() }) });
      setEditingId(null);
      await fetchData();
    } catch (e) { console.error(e); }
  };

  const deleteGroup = async (id) => {
    if (!confirm('Delete this group?')) return;
    try {
      await fetch(`${API}/groups/${id}`, { method: 'DELETE' });
      await fetchData();
    } catch (e) { console.error(e); }
  };

  // Multi-group support: group field stores comma-separated group names
  const getPointGroups = (m) => (m.group || '').split(',').map(s => s.trim()).filter(Boolean);
  const isPointInGroup = (m, groupName) => getPointGroups(m).includes(groupName);
  const getGroupMappings = (groupName) => mappings.filter(m => isPointInGroup(m, groupName));

  const togglePointInGroup = async (mappingId, groupName) => {
    const point = mappings.find(m => m.id === mappingId);
    if (!point) return;
    const currentGroups = getPointGroups(point);
    let newGroups;
    if (currentGroups.includes(groupName)) {
      newGroups = currentGroups.filter(g => g !== groupName);
    } else {
      newGroups = [...currentGroups, groupName];
    }
    try {
      await fetch(`${API}/mappings/${mappingId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group: newGroups.join(', ') })
      });
      await fetchData();
    } catch (e) { console.error(e); }
  };

  const unassignedPoints = mappings.filter(m => !m.group || m.group.trim() === '');

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">Groups</h2>
          <p className="text-xs text-text-muted mt-1">{groups.length} groups • {unassignedPoints.length} unassigned points</p>
        </div>
        <button onClick={() => setShowNew(true)}
          className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent-primary to-purple-600 rounded-lg text-white text-sm font-medium">
          <Plus size={16} /> New Group
        </button>
      </div>

      {showNew && (
        <div className="glass-card p-4 mb-4 flex items-center gap-3">
          <input type="text" placeholder="Group name..." value={newName} onChange={e => setNewName(e.target.value)} autoFocus
            onKeyDown={e => e.key === 'Enter' && createGroup()}
            className="flex-1 px-3 py-2 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus" />
          <button onClick={createGroup} className="p-2 rounded-lg bg-success/20 text-success hover:bg-success/30"><Check size={16} /></button>
          <button onClick={() => setShowNew(false)} className="p-2 rounded-lg bg-error/20 text-error hover:bg-error/30"><X size={16} /></button>
        </div>
      )}

      <div className="space-y-3">
        {groups.map(g => {
          const groupPoints = getGroupMappings(g.name);
          const isExpanded = expandedGroup === g.id;
          const isEditing = editingId === g.id;
          const isAssigning = showAssign === g.id;
          return (
            <div key={g.id} className="glass-card overflow-hidden">
              <div className="p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 cursor-pointer" onClick={() => setExpandedGroup(isExpanded ? null : g.id)}>
                    {isExpanded ? <ChevronDown size={16} className="text-accent-primary" /> : <ChevronRight size={16} className="text-text-muted" />}
                    <FolderOpen size={18} className="text-accent-secondary" />
                    {isEditing ? (
                      <input type="text" value={editName} onChange={e => setEditName(e.target.value)} autoFocus
                        onKeyDown={e => { if (e.key === 'Enter') updateGroup(g.id); if (e.key === 'Escape') setEditingId(null); }}
                        onClick={e => e.stopPropagation()}
                        className="flex-1 mr-2 px-2 py-1 bg-bg-input border border-border-focus rounded text-sm text-white focus:outline-none" />
                    ) : (
                      <h3 className="text-sm font-bold text-white">{g.name}</h3>
                    )}
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-accent-primary/10 text-accent-primary">{groupPoints.length} pts</span>
                  </div>
                  <div className="flex items-center gap-1">
                    {isEditing ? (
                      <>
                        <button onClick={() => updateGroup(g.id)} className="p-1.5 rounded text-success hover:bg-success/10"><Check size={14} /></button>
                        <button onClick={() => setEditingId(null)} className="p-1.5 rounded text-error hover:bg-error/10"><X size={14} /></button>
                      </>
                    ) : (
                      <>
                        <button onClick={() => setShowAssign(isAssigning ? null : g.id)}
                          className={`px-2 py-1 rounded text-[10px] font-bold transition-all ${isAssigning ? 'bg-accent-primary/20 text-accent-primary' : 'text-text-muted hover:text-white hover:bg-bg-card'}`}>
                          {isAssigning ? 'Done' : '+ Assign'}
                        </button>
                        <button onClick={() => { setEditingId(g.id); setEditName(g.name); }} className="p-1.5 rounded text-text-muted hover:text-white hover:bg-bg-card"><Edit3 size={14} /></button>
                        <button onClick={() => deleteGroup(g.id)} className="p-1.5 rounded text-text-muted hover:text-error hover:bg-error/10"><Trash2 size={14} /></button>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {/* Assignment panel */}
              {isAssigning && (
                <div className="border-t border-border p-3 bg-bg-primary/40 max-h-64 overflow-y-auto">
                  <div className="text-[10px] text-text-muted mb-2">Click to toggle point assignment (multi-group supported):</div>
                  {mappings.map(m => {
                    const inGroup = isPointInGroup(m, g.name);
                    const label = m.label || `${m.object_type}:${m.object_instance}`;
                    return (
                      <div key={m.id} onClick={() => togglePointInGroup(m.id, g.name)}
                        className={`flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer text-xs transition-all ${inGroup ? 'bg-success/10 text-white' : 'text-text-secondary hover:bg-bg-card'}`}>
                        <div className={`w-4 h-4 rounded border flex items-center justify-center ${inGroup ? 'bg-success border-success' : 'border-border'}`}>
                          {inGroup && <Check size={10} className="text-white" />}
                        </div>
                        <span className="truncate">{label}</span>
                        <span className="text-[10px] text-text-muted ml-auto">Dev {m.device_id}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {isExpanded && groupPoints.length > 0 && (
                <div className="border-t border-border p-2 bg-bg-primary/40">
                  <div className="grid gap-0.5" style={{ gridTemplateColumns: '40px 1fr 120px 70px 24px' }}>
                    <div className="text-[9px] text-text-muted px-1 py-0.5 font-bold">TYPE</div>
                    <div className="text-[9px] text-text-muted px-1 py-0.5 font-bold">LABEL</div>
                    <div className="text-[9px] text-text-muted px-1 py-0.5 font-bold text-right">VALUE</div>
                    <div className="text-[9px] text-text-muted px-1 py-0.5 font-bold text-right">UNIT</div>
                    <div></div>
                    {groupPoints.map(m => {
                      const label = m.label || `${m.object_type}:${m.object_instance}`;
                      const ot = (m.object_type || '').toLowerCase();
                      const isBinary = ot.includes('binary');
                      return (
                        <React.Fragment key={m.id}>
                          <span className="px-1 py-1"><span className="px-1 py-0.5 rounded bg-info/15 text-info text-[9px] font-bold">{m.object_type?.slice(0, 3)?.toUpperCase()}</span></span>
                          <span className="text-xs text-text-secondary truncate py-1">{label}</span>
                          <span className={`text-xs font-bold text-right py-1 ${isBinary ? (m.last_value === 'active' || m.last_value === 1 ? 'text-success' : 'text-error') : 'text-white'}`}>
                            {m.last_value != null ? String(m.last_value) : '—'}
                          </span>
                          <span className="text-[10px] text-text-muted text-right py-1">{m.units || ''}</span>
                          <button onClick={() => togglePointInGroup(m.id, g.name)} className="p-0.5 rounded text-text-muted hover:text-error hover:bg-error/10 justify-self-center" title="Remove">
                            <X size={11} />
                          </button>
                        </React.Fragment>
                      );
                    })}
                  </div>
                </div>
              )}
              {isExpanded && groupPoints.length === 0 && (
                <div className="border-t border-border p-4 text-xs text-text-muted text-center bg-bg-primary/40">
                  No points assigned. Click <b>+ Assign</b> to add points.
                </div>
              )}
            </div>
          );
        })}

        {groups.length === 0 && (
          <div className="glass-card p-12 text-center">
            <FolderOpen size={40} className="mx-auto text-text-muted mb-4 opacity-40" />
            <p className="text-text-secondary text-sm">No groups created yet</p>
            <p className="text-text-muted text-xs mt-1">Create a group and assign points to organize your BACnet network</p>
          </div>
        )}
      </div>
    </div>
  );
}
