#!/bin/bash
# BACnet-MQTT Gateway — Install script
# Usage: sudo ./scripts/install.sh
# Works on: Ubuntu Server, Raspberry Pi OS
set -e

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_FILE="$APP_DIR/scripts/bacnet-gateway.service"
CURRENT_USER="${SUDO_USER:-$(whoami)}"

echo "=== BACnet-MQTT Gateway Installer ==="
echo "App dir: $APP_DIR"
echo "User:    $CURRENT_USER"
echo ""

# 1. Install system dependencies
echo "[1/6] Installing system dependencies..."
apt-get update -q
apt-get install -y nginx python3-venv python3-pip sshpass 2>&1 | tail -3

# 2. Create venv if not exists
echo "[2/6] Setting up Python virtual environment..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 3. Install systemd service for backend (port 8000)
echo "[3/6] Installing bacnet-gateway systemd service..."
cp "$SERVICE_FILE" /etc/systemd/system/bacnet-gateway.service

# 4. Configure NGINX (port 8080 → static + proxy to 8000)
echo "[4/6] Configuring NGINX..."
cat > /etc/nginx/sites-available/bacnet-gateway << 'NGINXEOF'
server {
    listen 8080;
    server_name _;

    gzip on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;

    root /home/user/bacnet_mqtt_gateway/frontend_v2/dist;
    index index.html;

    location /assets/ {
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
NGINXEOF
ln -sf /etc/nginx/sites-available/bacnet-gateway /etc/nginx/sites-enabled/bacnet-gateway
rm -f /etc/nginx/sites-enabled/default

# 5. Fix file permissions for NGINX to read frontend dist
echo "[5/6] Setting file permissions..."
DIST="$APP_DIR/frontend_v2/dist"
if [ -d "$DIST" ]; then
    find "$DIST" -type d -exec chmod o+rx {} \;
    find "$DIST" -type f -exec chmod o+r {} \;
fi
chmod o+x "$(dirname "$APP_DIR")" "$APP_DIR" "$APP_DIR/frontend_v2" 2>/dev/null || true

# 6. Enable and start services
echo "[6/6] Enabling and starting services..."
systemctl daemon-reload
systemctl enable bacnet-gateway nginx
systemctl restart bacnet-gateway
sleep 3
systemctl restart nginx

echo ""
echo "=== Installation complete ==="
systemctl is-active bacnet-gateway && echo "✅ bacnet-gateway: active"
systemctl is-active nginx && echo "✅ nginx: active"
echo ""
echo "Web UI:  http://$(hostname -I | awk '{print $1}'):8080"
echo "API:     http://$(hostname -I | awk '{print $1}'):8000 (internal only)"
echo "Logs:    journalctl -u bacnet-gateway -f"
