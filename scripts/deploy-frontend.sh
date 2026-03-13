#!/bin/bash
# Deploy FRONTEND only — build Vite app, sync dist/ to server
# Không restart backend, không ảnh hưởng BACnet
# Usage: ./deploy-frontend.sh [ip]

set -e

REMOTE_USER="user"
REMOTE_PASS="Admin@12345"
IP_OPENVPN="10.212.154.2"
IP_TAILSCALE="100.74.25.27"
REMOTE_DIST="/home/user/bacnet_mqtt_gateway/frontend_v2/dist"

# ── Resolve target IP ──────────────────────────────────────────
resolve_host() {
  local ip="$1"
  sshpass -p "${REMOTE_PASS}" ssh -o StrictHostKeyChecking=no \
    -o ConnectTimeout=5 \
    "${REMOTE_USER}@${ip}" "echo ok" 2>/dev/null
}

if [ -n "$1" ]; then
  REMOTE="$1"
  echo "▶ Using explicit IP: ${REMOTE}"
elif resolve_host "${IP_OPENVPN}"; then
  REMOTE="${IP_OPENVPN}"
  echo "▶ Reached via OpenVPN: ${REMOTE}"
elif resolve_host "${IP_TAILSCALE}"; then
  REMOTE="${IP_TAILSCALE}"
  echo "▶ Reached via Tailscale: ${REMOTE}"
else
  echo "❌ Cannot reach Ubuntu gateway via OpenVPN (${IP_OPENVPN}) or Tailscale (${IP_TAILSCALE})"
  exit 1
fi

# ── Build ──────────────────────────────────────────────────────
echo "▶ Building frontend..."
cd "$(dirname "$0")/../frontend_v2"
npm run build 2>&1 | tail -3

# ── Sync ──────────────────────────────────────────────────────
echo "▶ Syncing dist/ to ${REMOTE} (no backend restart)..."
sshpass -p "${REMOTE_PASS}" rsync -az --delete dist/ \
  -e "ssh -o StrictHostKeyChecking=no" \
  "${REMOTE_USER}@${REMOTE}:${REMOTE_DIST}/"

echo "▶ Fixing NGINX read permissions..."
sshpass -p "${REMOTE_PASS}" ssh -o StrictHostKeyChecking=no "${REMOTE_USER}@${REMOTE}" \
  "echo '${REMOTE_PASS}' | sudo -S bash -c 'find ${REMOTE_DIST} -type d -exec chmod o+rx {} \; && find ${REMOTE_DIST} -type f -exec chmod o+r {} \;'"

echo "✅ Frontend deployed to ${REMOTE} — NGINX serves new files, BACnet untouched"
