import React, { useState, useEffect } from 'react';
import { X, XCircle, CheckCircle, AlertTriangle, Info } from 'lucide-react';
import { subscribe, getToasts, removeToast, clearAllToasts } from '../utils/toastStore';

const ICONS = {
  success: CheckCircle,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
};
const COLORS = {
  success: 'border-green-500/40 bg-green-500/10 text-green-400',
  error: 'border-red-500/40 bg-red-500/10 text-red-400',
  warning: 'border-yellow-500/40 bg-yellow-500/10 text-yellow-400',
  info: 'border-blue-500/40 bg-blue-500/10 text-blue-400',
};

export function ToastContainer() {
  const [toasts, setToasts] = useState(getToasts());

  useEffect(() => {
    return subscribe(setToasts);
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 w-80 pointer-events-none">
      {toasts.length > 1 && (
        <button onClick={clearAllToasts}
          className="pointer-events-auto self-end px-2 py-1 rounded text-[10px] font-bold text-text-muted hover:text-white bg-bg-secondary/90 border border-border backdrop-blur-lg transition-all">
          Clear All ({toasts.length})
        </button>
      )}
      {toasts.map(t => {
        const Icon = ICONS[t.type] || Info;
        return (
          <div key={t.id}
            className={`pointer-events-auto flex items-start gap-2 px-3 py-2.5 rounded-lg border backdrop-blur-xl shadow-lg animate-slide-in ${COLORS[t.type] || COLORS.info}`}>
            <Icon size={16} className="mt-0.5 flex-shrink-0" />
            <span className="flex-1 text-xs leading-relaxed break-words">{t.message}</span>
            <button onClick={() => removeToast(t.id)} className="p-0.5 rounded hover:bg-white/10 flex-shrink-0">
              <X size={12} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
