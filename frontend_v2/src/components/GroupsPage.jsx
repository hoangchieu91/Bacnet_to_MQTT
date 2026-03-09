import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Plus, Trash2, Edit3, Check, X, FolderOpen, ChevronDown, ChevronRight, Search } from 'lucide-react';

const API = '/api';

export function GroupsPage() {
  const [groups, setGroups] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [initLoading, setInitLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState('');
  const [newName, setNewName] = useState('');
  const [showNew, setShowNew] = useState(false);
  const [expandedGroup, setExpandedGroup] = useState(null);
  const [assignState, setAssignState] = useState(null); // { groupId, search, pendingIds: Set }

  const fetchData = useCallback(async () => {
    try {
      const [g, m] = await Promise.all([
        fetch(`${API}/groups`).then(r => r.json()),
        fetch(`${API}/mappings`).then(r => r.json()),
      ]);
      setGroups(g.groups || []);
      setMappings(m.mappings || []);
    } catch (e) { console.error(e); }
    finally { setInitLoading(false); }
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

  const getPointGroups = (m) => (m.group || '').split(',').map(s => s.trim()).filter(Boolean);
  const getGroupMappings = (groupName) => mappings.filter(m => getPointGroups(m).includes(groupName));

  // Open assign panel: pre-populate pendingIds with points already in this group
  const openAssign = (g) => {
    const alreadyIn = new Set(mappings.filter(m => getPointGroups(m).includes(g.name)).map(m => m.id));
    setAssignState({ groupId: g.id, groupName: g.name, search: '', pendingIds: alreadyIn });
  };

  // Apply: compute added/removed and batch PUT
  const applyAssign = async () => {
    if (!assignState) return;
    const { groupName, pendingIds } = assignState;
    const changed = mappings.filter(m => {
      const inBefore = getPointGroups(m).includes(groupName);
      const inAfter = pendingIds.has(m.id);
      return inBefore !== inAfter;
    });
    await Promise.all(changed.map(m => {
      const current = getPointGroups(m);
      const inAfter = pendingIds.has(m.id);
      const newGroups = inAfter ? [...current, groupName] : current.filter(g => g !== groupName);
      return fetch(`${API}/mappings/${m.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group: newGroups.join(', ') })
      });
    }));
    setAssignState(null);
    await fetchData();
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

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {groups.map(g => {
          const groupPoints = getGroupMappings(g.name);
          const isExpanded = expandedGroup === g.id;
          const isEditing = editingId === g.id;
          const isAssigning = assignState?.groupId === g.id;

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
                        <button onClick={() => isAssigning ? applyAssign() : openAssign(g)}
                          className={`px-2.5 py-1 rounded text-[10px] font-bold transition-all ${isAssigning ? 'bg-success/20 text-success border border-success/30' : 'text-text-muted hover:text-white hover:bg-bg-card border border-transparent'}`}>
                          {isAssigning ? '✓ Apply' : '+ Assign'}
                        </button>
                        {isAssigning && (
                          <button onClick={() => setAssignState(null)} className="p-1.5 rounded text-text-muted hover:text-error hover:bg-error/10"><X size={13} /></button>
                        )}
                        <button onClick={() => { setEditingId(g.id); setEditName(g.name); }} className="p-1.5 rounded text-text-muted hover:text-white hover:bg-bg-card"><Edit3 size={14} /></button>
                        <button onClick={() => deleteGroup(g.id)} className="p-1.5 rounded text-text-muted hover:text-error hover:bg-error/10"><Trash2 size={14} /></button>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {/* ── Assignment panel with Search + Select All ── */}
              {isAssigning && assignState && (
                <AssignPanel
                  mappings={mappings}
                  assignState={assignState}
                  setAssignState={setAssignState}
                />
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
                          <button onClick={() => {
                            // Quick remove: open assign, uncheck this point, apply
                            const alreadyIn = new Set(groupPoints.map(p => p.id));
                            alreadyIn.delete(m.id);
                            setAssignState({ groupId: g.id, groupName: g.name, search: '', pendingIds: alreadyIn });
                          }} className="p-0.5 rounded text-text-muted hover:text-error hover:bg-error/10 justify-self-center" title="Remove">
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
            {initLoading ? (
              <>
                <div className="w-8 h-8 border-2 border-accent-primary/30 border-t-accent-primary rounded-full animate-spin mx-auto mb-3" />
                <p className="text-text-secondary text-sm">Đang tải groups...</p>
              </>
            ) : (
              <>
                <FolderOpen size={40} className="mx-auto text-text-muted mb-4 opacity-40" />
                <p className="text-text-secondary text-sm">No groups created yet</p>
                <p className="text-text-muted text-xs mt-1">Create a group and assign points to organize your BACnet network</p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Assign Panel component ──────────────────────────────────────
function AssignPanel({ mappings, assignState, setAssignState }) {
  const { search, pendingIds } = assignState;

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return mappings;
    return mappings.filter(m =>
      (m.label || '').toLowerCase().includes(q) ||
      String(m.device_id).includes(q) ||
      (m.object_type || '').toLowerCase().includes(q) ||
      String(m.object_instance).includes(q)
    );
  }, [mappings, search]);

  const filteredIds = useMemo(() => new Set(filtered.map(m => m.id)), [filtered]);
  const checkedInFiltered = filtered.filter(m => pendingIds.has(m.id)).length;

  const toggle = (id) => {
    setAssignState(s => {
      const next = new Set(s.pendingIds);
      if (next.has(id)) next.delete(id); else next.add(id);
      return { ...s, pendingIds: next };
    });
  };

  const selectAllFiltered = () => setAssignState(s => ({ ...s, pendingIds: new Set([...s.pendingIds, ...filteredIds]) }));
  const deselectAllFiltered = () => setAssignState(s => {
    const next = new Set(s.pendingIds);
    filteredIds.forEach(id => next.delete(id));
    return { ...s, pendingIds: next };
  });

  return (
    <div className="border-t border-border bg-bg-primary/40">
      {/* Search + bulk actions */}
      <div className="flex items-center gap-2 px-3 pt-3 pb-2">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text" placeholder="Search label, device, type..."
            value={search} autoFocus
            onChange={e => setAssignState(s => ({ ...s, search: e.target.value }))}
            className="w-full pl-8 pr-3 py-1.5 bg-bg-input border border-border rounded-lg text-xs text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus"
          />
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={selectAllFiltered}
            className="px-2 py-1 rounded text-[10px] font-bold bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25 transition-all">
            All ({filtered.length})
          </button>
          <button onClick={deselectAllFiltered}
            className="px-2 py-1 rounded text-[10px] font-bold bg-bg-input text-text-muted hover:text-white transition-all">
            None
          </button>
          <span className="text-[10px] text-text-muted">
            {checkedInFiltered}/{filtered.length} ✓
          </span>
        </div>
      </div>

      {/* Points list */}
      <div className="max-h-56 overflow-y-auto px-2 pb-2 space-y-0.5">
        {filtered.map(m => {
          const inGroup = pendingIds.has(m.id);
          const label = m.label || `${m.object_type}:${m.object_instance}`;
          return (
            <div key={m.id} onClick={() => toggle(m.id)}
              className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg cursor-pointer text-xs select-none transition-all ${inGroup ? 'bg-success/10 text-white border border-success/20' : 'text-text-secondary hover:bg-bg-card border border-transparent'}`}>
              <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-all ${inGroup ? 'bg-success border-success' : 'border-border'}`}>
                {inGroup && <Check size={10} className="text-white" />}
              </div>
              <span className="truncate flex-1">{label}</span>
              <span className="text-[9px] px-1 py-0.5 rounded bg-info/10 text-info font-bold shrink-0">{(m.object_type||'').slice(0,2).toUpperCase()}</span>
              <span className="text-[10px] text-text-muted shrink-0">Dev {m.device_id}</span>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="text-xs text-text-muted text-center py-4">No points match "{search}"</div>
        )}
      </div>
    </div>
  );
}

