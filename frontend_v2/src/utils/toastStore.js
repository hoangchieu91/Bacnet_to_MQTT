// Simple global toast store — no external deps needed
let _listeners = [];
let _toasts = [];
let _nextId = 0;

const MAX_TOASTS = 5;

export function addToast(message, type = 'info', duration = 8000) {
  const id = ++_nextId;
  const toast = { id, message, type, createdAt: Date.now() };
  _toasts = [toast, ..._toasts].slice(0, MAX_TOASTS);
  _notify();
  if (duration > 0) {
    setTimeout(() => removeToast(id), duration);
  }
  return id;
}

export function removeToast(id) {
  _toasts = _toasts.filter(t => t.id !== id);
  _notify();
}

export function clearAllToasts() {
  _toasts = [];
  _notify();
}

export function getToasts() {
  return _toasts;
}

export function subscribe(listener) {
  _listeners.push(listener);
  return () => { _listeners = _listeners.filter(l => l !== listener); };
}

function _notify() {
  _listeners.forEach(l => l(_toasts));
}

// Convenience helpers
export const toast = {
  success: (msg, dur) => addToast(msg, 'success', dur),
  error: (msg, dur) => addToast(msg, 'error', dur || 12000),
  warning: (msg, dur) => addToast(msg, 'warning', dur || 10000),
  info: (msg, dur) => addToast(msg, 'info', dur),
};
