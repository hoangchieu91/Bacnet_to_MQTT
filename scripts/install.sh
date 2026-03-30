#!/bin/bash
# BACnet-MQTT Gateway — Install script (Consolidated V2)
# Usage: sudo ./scripts/install.sh
# Works on: Ubuntu Server, Raspberry Pi OS
set -e

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_FILE="$APP_DIR/scripts/bacnet-gateway.service"
CURRENT_USER="${SUDO_USER:-$(whoami)}"

echo "=== BACnet-MQTT Gateway Installer (Consolidated) ==="
echo "App dir: $APP_DIR"
echo "User:    $CURRENT_USER"
echo ""

# 1. Install system dependencies
echo "[1/4] Installing system dependencies..."
apt-get update -q
# Nginx no longer required as FastAPI serves static files directly
apt-get install -y python3-venv python3-pip sshpass 2>&1 | tail -3

# 2. Create venv if not exists
echo "[2/4] Setting up Python virtual environment..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
# Ensure ownership matches current user for pip install
chown -R "$CURRENT_USER:$CURRENT_USER" "$APP_DIR/venv"
sudo -u "$CURRENT_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip -q
sudo -u "$CURRENT_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 3. Install systemd service for unified gateway (port 80)
echo "[3/4] Installing bacnet-gateway systemd service..."
cp "$SERVICE_FILE" /etc/systemd/system/bacnet-gateway.service

# 4. Enable and start services
echo "[4/4] Enabling and starting services..."
systemctl daemon-reload
systemctl enable bacnet-gateway
systemctl restart bacnet-gateway
sleep 3

echo ""
echo "=== Installation complete ==="
systemctl is-active bacnet-gateway && echo "✅ bacnet-gateway: active"
echo ""
echo "Web UI & API:  http://$(hostname -I | awk '{print $1}'):80"
echo "Logs:          journalctl -u bacnet-gateway -f"
echo ""
