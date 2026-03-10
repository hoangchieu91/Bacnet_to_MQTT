#!/bin/bash
# Deploy BACKEND only — restart bacnet-gateway service
# Dùng khi thay đổi Python code (main.py, gateway_engine.py...)
# Lưu ý: sẽ có vài giây disconnected trong khi BACnet reinit
# Usage: ./deploy-backend.sh

set -e
REMOTE="user@100.116.210.25"
REMOTE_BACKEND="/home/user/bacnet_mqtt_gateway/backend"

echo "▶ Syncing backend Python files..."
sshpass -p "Admin@12345" rsync -az \
  -e "ssh -o StrictHostKeyChecking=no" \
  "$(dirname "$0")/../backend/" \
  "${REMOTE}:${REMOTE_BACKEND}/"

echo "▶ Restarting bacnet-gateway service..."
sshpass -p "Admin@12345" ssh -o StrictHostKeyChecking=no "${REMOTE}" \
  "echo 'Admin@12345' | sudo -S systemctl restart bacnet-gateway && sleep 5 && systemctl is-active bacnet-gateway"

echo "✅ Backend deployed and restarted"
echo "  ⚠ BACnet sẽ reconnect lại trong vài phút"
