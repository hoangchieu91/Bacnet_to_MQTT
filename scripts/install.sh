#!/bin/bash
# BACnet-MQTT Gateway — Install script for Raspberry Pi
set -e

APP_DIR="/home/pi/bacnet_mqtt_gateway"
SERVICE_FILE="$APP_DIR/scripts/bacnet-gateway.service"

echo "=== BACnet-MQTT Gateway Installer ==="

# 1. Create venv if not exists
if [ ! -d "$APP_DIR/venv" ]; then
    echo "[1/4] Creating Python virtual environment..."
    python3 -m venv "$APP_DIR/venv"
else
    echo "[1/4] venv already exists, skipping."
fi

# 2. Install dependencies
echo "[2/4] Installing Python dependencies..."
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 3. Install systemd service
echo "[3/4] Installing systemd service..."
sudo cp "$SERVICE_FILE" /etc/systemd/system/bacnet-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable bacnet-gateway.service

# 4. Start service
echo "[4/4] Starting gateway service..."
sudo systemctl restart bacnet-gateway.service
sleep 2
sudo systemctl status bacnet-gateway.service --no-pager

echo ""
echo "=== Installation complete ==="
echo "Web UI:  http://$(hostname -I | awk '{print $1}'):8080"
echo "Logs:    journalctl -u bacnet-gateway -f"
