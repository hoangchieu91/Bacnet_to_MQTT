import React, { useState } from 'react';
import { Lock, Eye, EyeOff, Loader2 } from 'lucide-react';

const API = '/api';

export function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || 'Invalid credentials');
        return;
      }
      onLogin(data);
    } catch (e) {
      setError('Cannot connect to server');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-bg-primary flex items-center justify-center p-4">
      {/* Background glow */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-accent-primary/10 rounded-full blur-3xl" />
        <div className="absolute top-2/3 left-1/3 w-64 h-64 bg-purple-600/10 rounded-full blur-3xl" />
      </div>

      <div className="relative w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-14 h-14 bg-gradient-to-br from-accent-primary to-purple-600 rounded-xl mx-auto mb-4 flex items-center justify-center shadow-lg shadow-accent-primary/30">
            <Lock size={24} className="text-white" />
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">BACnet Gateway</h1>
          <p className="text-text-muted text-sm mt-1">Sign in to continue</p>
        </div>

        {/* Card */}
        <div className="glass-card p-6">
          <form onSubmit={submit} className="space-y-4">
            {/* Username */}
            <div>
              <label className="text-xs text-text-muted block mb-1.5 font-medium">Username</label>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                placeholder="admin"
                className="w-full px-3 py-2.5 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus transition-colors"
              />
            </div>

            {/* Password */}
            <div>
              <label className="text-xs text-text-muted block mb-1.5 font-medium">Password</label>
              <div className="relative">
                <input
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  autoComplete="current-password"
                  placeholder="••••••••"
                  className="w-full px-3 py-2.5 pr-10 bg-bg-input border border-border rounded-lg text-sm text-white placeholder:text-text-muted focus:outline-none focus:border-border-focus transition-colors"
                />
                <button type="button" onClick={() => setShowPw(p => !p)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-white">
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            {/* Error */}
            {error && (
              <p className="text-error text-xs text-center bg-error/10 border border-error/20 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || !username.trim() || !password}
              className="w-full py-2.5 bg-gradient-to-r from-accent-primary to-purple-600 text-white text-sm font-medium rounded-lg shadow-lg shadow-accent-primary/20 hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading ? <><Loader2 size={15} className="animate-spin" /> Signing in...</> : 'Sign In'}
            </button>
          </form>
        </div>

        <p className="text-center text-text-muted text-xs mt-6">
          BACnet-MQTT Gateway V2.0
        </p>
      </div>
    </div>
  );
}
