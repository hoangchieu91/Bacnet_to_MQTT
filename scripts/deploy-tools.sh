#!/bin/bash
# Deploy MODBUS + MSTP tools to Pi
# Preserves config.yaml (symlinked to /home/pi/bacnet_data/)
# Usage: ./deploy-tools.sh [pi-ip]

set -e
PI_IP="${1:-10.25.7.21}"
PI_USER="pi"
PI_PASS="Raspberry"
REMOTE_BASE="/home/pi/bacnet_mqtt_gateway/tools"

echo "▶ Deploying Modbus RTU Tools..."
sshpass -p "${PI_PASS}" rsync -az \
  --exclude='config.yaml' \
  --exclude='*.db' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  -e "ssh -o StrictHostKeyChecking=no" \
  "$(dirname "$0")/../tools/modbus/" \
  "${PI_USER}@${PI_IP}:${REMOTE_BASE}/modbus/"

echo "▶ Deploying MS/TP Tools..."
sshpass -p "${PI_PASS}" rsync -az \
  --exclude='config.yaml' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  -e "ssh -o StrictHostKeyChecking=no" \
  "$(dirname "$0")/../tools/mstp/" \
  "${PI_USER}@${PI_IP}:${REMOTE_BASE}/mstp/"

echo "▶ Restarting services..."
sshpass -p "${PI_PASS}" ssh -o StrictHostKeyChecking=no "${PI_USER}@${PI_IP}" \
  "sudo systemctl restart modbus-rtu-tools mstp-tools && sleep 3 && \
   systemctl is-active modbus-rtu-tools && echo '  ✅ Modbus RTU OK' && \
   systemctl is-active mstp-tools && echo '  ✅ MS/TP OK'"

echo "✅ Tools deployed — configs preserved in /home/pi/bacnet_data/"
