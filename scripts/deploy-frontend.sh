#!/bin/bash
# Deploy FRONTEND only — không restart backend, không ảnh hưởng BACnet
# Usage: ./deploy-frontend.sh

set -e
REMOTE="user@10.212.154.2"
REMOTE_DIST="/home/user/bacnet_mqtt_gateway/frontend_v2/dist"

echo "▶ Building frontend..."
cd "$(dirname "$0")/../frontend_v2"
npm run build 2>&1 | tail -3

echo "▶ Syncing dist/ to server (no backend restart)..."
sshpass -p "Admin@12345" rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=no -o KexAlgorithms=diffie-hellman-group14-sha256 -o Ciphers=aes128-ctr -o MACs=hmac-sha2-256" \
  "$(dirname "$0")/../frontend_v2/dist/" \
  "${REMOTE}:${REMOTE_DIST}/"

echo "▶ Fixing permissions for NGINX..."
sshpass -p "Admin@12345" ssh -o "StrictHostKeyChecking=no" -o "KexAlgorithms=diffie-hellman-group14-sha256" -o "Ciphers=aes128-ctr" -o "MACs=hmac-sha2-256" "${REMOTE}" \
  "echo 'Admin@12345' | sudo -S bash -c 'find ${REMOTE_DIST} -type d -exec chmod o+rx {} \\; && find ${REMOTE_DIST} -type f -exec chmod o+r {} \\;'"

echo "✅ Frontend deployed — NGINX serves new files immediately, BACnet untouched"
