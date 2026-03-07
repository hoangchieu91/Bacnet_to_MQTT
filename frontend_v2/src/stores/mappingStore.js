import { create } from 'zustand';

const API_BASE = '/api';

async function apiFetch(url, method = 'GET', body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${API_BASE}${url}`, opts);
  if (!res.ok) throw new Error(`API ${method} ${url} failed: ${res.status}`);
  return res.json();
}

export const useMappingStore = create((set, get) => ({
  mappings: [],
  loading: false,
  selectedId: null,
  selectedIds: new Set(),
  filterText: '',

  // ── Load all mappings ──────────────────────
  fetchMappings: async () => {
    set({ loading: true });
    try {
      const data = await apiFetch('/mappings');
      set({ mappings: data.mappings || [], loading: false });
    } catch (e) {
      console.error('fetchMappings error:', e);
      set({ loading: false });
    }
  },

  // ── Create mapping ─────────────────────────
  createMapping: async (payload) => {
    try {
      await apiFetch('/mappings', 'POST', payload);
      await get().fetchMappings();
    } catch (e) { console.error('createMapping error:', e); throw e; }
  },

  // ── Update mapping ─────────────────────────
  updateMapping: async (id, payload) => {
    try {
      await apiFetch(`/mappings/${id}`, 'PUT', payload);
      await get().fetchMappings();
    } catch (e) { console.error('updateMapping error:', e); throw e; }
  },

  // ── Delete mapping ─────────────────────────
  deleteMapping: async (id) => {
    try {
      await apiFetch(`/mappings/${id}`, 'DELETE');
      set(state => ({
        mappings: state.mappings.filter(m => m.id !== id),
        selectedId: state.selectedId === id ? null : state.selectedId
      }));
    } catch (e) { console.error('deleteMapping error:', e); throw e; }
  },

  // ── Bulk update ────────────────────────────
  bulkUpdate: async (ids, payload) => {
    let updated = 0;
    for (const id of ids) {
      try {
        await apiFetch(`/mappings/${id}`, 'PUT', payload);
        updated++;
      } catch (e) { console.error('bulkUpdate error for', id, e); }
    }
    await get().fetchMappings();
    return updated;
  },

  // ── Export/Import ──────────────────────────
  exportMappings: async () => {
    const resp = await apiFetch('/mappings/export');
    return resp.mappings;
  },

  importMappings: async (data) => {
    const resp = await apiFetch('/mappings/import', 'POST', data);
    await get().fetchMappings();
    return resp;
  },

  // ── Selection ──────────────────────────────
  setSelectedId: (id) => set({ selectedId: id }),
  toggleSelectId: (id) => set(state => {
    const next = new Set(state.selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    return { selectedIds: next };
  }),
  selectAll: (ids) => set({ selectedIds: new Set(ids) }),
  clearSelection: () => set({ selectedIds: new Set() }),
  setFilterText: (text) => set({ filterText: text }),
}));
