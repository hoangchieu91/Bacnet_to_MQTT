#!/bin/bash
# Deploy BACKEND only — restart bacnet-gateway service
# Dùng khi thay đổi Python code (main.py, gateway_engine.py...)
# Lưu ý: sẽ có vài giây disconnected trong khi BACnet reinit
# Usage: ./deploy-backend.sh [ip]

set -e

REMOTE_USER="user"
REMOTE_PASS="Admin@12345"
IP_OPENVPN="10.212.154.2"
IP_TAILSCALE="100.74.25.27"
REMOTE_BACKEND="/home/user/bacnet_mqtt_gateway/backend"

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

# ── Sync & restart ─────────────────────────────────────────────
echo "▶ Syncing backend Python files to ${REMOTE}..."
sshpass -p "${REMOTE_PASS}" rsync -az \
  --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.db' \
  -e "ssh -o StrictHostKeyChecking=no" \
  "$(dirname "$0")/../backend/" \
  "${REMOTE_USER}@${REMOTE}:${REMOTE_BACKEND}/"

echo "▶ Restarting bacnet-gateway service..."
sshpass -p "${REMOTE_PASS}" ssh -o StrictHostKeyChecking=no "${REMOTE_USER}@${REMOTE}" \
  "echo '${REMOTE_PASS}' | sudo -S systemctl restart bacnet-gateway && sleep 5 && systemctl is-active bacnet-gateway"

echo "✅ Backend deployed to ${REMOTE} and restarted"
echo "  ⚠ BACnet sẽ reconnect lại trong vài phút"
