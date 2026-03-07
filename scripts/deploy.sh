#!/bin/bash
# ═══════════════════════════════════════════════
# BACnet-MQTT Gateway — Deploy to Raspberry Pi
# Usage: ./scripts/deploy.sh
# ═══════════════════════════════════════════════
set -e

PI_USER="pi"
PI_HOST="10.25.7.21"
PI_PASS="Raspberry"
PI_DIR="/home/pi/bacnet_mqtt_gateway"
SERVICE="bacnet-gateway"

echo "🚀 Deploying BACnet-MQTT Gateway to Pi ($PI_HOST)..."

# 1. Sync files (exclude venv, __pycache__, data, .git)
echo "[1/3] Syncing files..."
sshpass -p "$PI_PASS" rsync -avz --delete \
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
  ./ "$PI_USER@$PI_HOST:$PI_DIR/"

# 2. Install/update dependencies on Pi
echo "[2/3] Installing dependencies on Pi..."
sshpass -p "$PI_PASS" ssh -o StrictHostKeyChecking=no "$PI_USER@$PI_HOST" \
  "$PI_DIR/venv/bin/pip install -q -r $PI_DIR/requirements.txt 2>/dev/null || true"

# 3. Restart service
echo "[3/3] Restarting gateway service..."
sshpass -p "$PI_PASS" ssh -o StrictHostKeyChecking=no "$PI_USER@$PI_HOST" \
  "sudo systemctl restart $SERVICE && sleep 2 && sudo systemctl status $SERVICE --no-pager"

echo ""
echo "✅ Deploy complete!"
echo "🌐 Web UI: http://$PI_HOST:8080"
echo "📋 Logs:   sshpass -p '$PI_PASS' ssh $PI_USER@$PI_HOST 'journalctl -u $SERVICE -f'"
