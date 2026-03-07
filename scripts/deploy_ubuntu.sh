#!/bin/bash
# ═══════════════════════════════════════════════
# BACnet-MQTT Gateway — Deploy to Ubuntu Server
# Usage: ./scripts/deploy_ubuntu.sh
# ═══════════════════════════════════════════════
set -e

SRV_USER="user"
SRV_HOST="100.116.210.25"
SRV_PASS="Admin@12345"
SRV_DIR="/home/user/bacnet_mqtt_gateway"
SERVICE="bacnet-gateway"

echo "🚀 Deploying BACnet-MQTT Gateway to Ubuntu ($SRV_HOST)..."

# 1. Sync files
echo "[1/3] Syncing files..."
sshpass -p "$SRV_PASS" rsync -avz --delete \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='.git' \
  --exclude='data' \
  --exclude='config/runtime_config.json' \
  --exclude='*.pyc' \
  --exclude='.agents' \
  --exclude='docs/pi_access.md' \
  --exclude='node_modules' \
  --exclude='frontend_v2/src' \
  --exclude='frontend_v2/.git' \
  -e "ssh -o StrictHostKeyChecking=no" \
  ./ "$SRV_USER@$SRV_HOST:$SRV_DIR/"

# 2. Install/update dependencies
echo "[2/3] Installing dependencies..."
sshpass -p "$SRV_PASS" ssh -o StrictHostKeyChecking=no "$SRV_USER@$SRV_HOST" \
  "$SRV_DIR/venv/bin/pip install -q -r $SRV_DIR/requirements.txt 2>/dev/null || true"

# 3. Restart service
echo "[3/3] Restarting gateway service..."
sshpass -p "$SRV_PASS" ssh -o StrictHostKeyChecking=no "$SRV_USER@$SRV_HOST" \
  "echo '$SRV_PASS' | sudo -S systemctl restart $SERVICE && sleep 2 && echo '$SRV_PASS' | sudo -S systemctl status $SERVICE --no-pager"

echo ""
echo "✅ Deploy complete!"
echo "🌐 Web UI: http://$SRV_HOST:8080"
echo "📋 Logs:   sshpass -p '$SRV_PASS' ssh $SRV_USER@$SRV_HOST 'journalctl -u $SERVICE -f'"
